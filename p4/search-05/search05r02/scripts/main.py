#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多库联合检索流水线入口（仅标准库，确定性，无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json \
      --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --resume --input /input --output /app/output --work /app/work

流程：
  1. 读 /input/case.json 与 /input/task.md，立即写 result.json 骨架（status=partial）
  2. 阶段 P1：发现材料 -> 分类响应 -> 字段归一 -> 去重 -> 合并 -> 覆盖 -> 证据
  3. 阶段 P2：确定性相关性提示与排序；若给 --decisions 则合并人工/模型仲裁
  4. 阶段 P3：写最终 result.json 及 merged_records.json / evidence_map.json /
     coverage_report.md / pipeline_state.json，记录阶段预算与耗时
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as fs  # noqa: E402


def iso_now() -> str:
    return fs.iso_now()


def _phase_entry(pid: str, started_at: str, t0: float, status: str) -> Dict:
    meta = next((p for p in fs.PHASES if p["id"] == pid), {})
    return {
        "id": pid,
        "name": meta.get("name", pid),
        "budget_seconds": meta.get("budget_seconds"),
        "max_model_calls": meta.get("max_model_calls"),
        "status": status,
        "elapsed_seconds": round(time.time() - t0, 2),
        "started_at": started_at,
    }


def write_result(output_dir: Path, res: Dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "result.json"
    tmp = output_dir / "result.json.tmp"
    tmp.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(target)


def extract_request(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    for k in ("query", "query_terms", "term", "q", "search_terms",
              "queryString"):
        if obj.get(k) not in (None, "", []):
            return str(obj[k])[:500]
    msg = obj.get("message")
    if isinstance(msg, dict):
        for k in ("query", "query_terms", "term", "q"):
            if msg.get(k) not in (None, "", []):
                return str(msg[k])[:500]
    return None


def load_decisions(path: Optional[str]) -> Dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    obj, _ = fs.load_json(p)
    return obj if isinstance(obj, dict) else {}


def parse_materials(input_dir: Path):
    """返回 (sources, records, degradations)。"""
    sources: List[Dict] = []
    records: List[Dict] = []
    degradations: List[str] = []
    for p in fs.discover_input_files(input_dir):
        kind = fs.classify_file(p)
        rel = p.relative_to(input_dir).as_posix()
        if kind == "json":
            obj, errs = fs.load_json(p)
            degradations.extend(errs)
            if obj is None:
                degradations.append("json_unparsed:%s" % rel)
                continue
            db = fs.classify_response(obj)
            req = extract_request(obj)
            recs = fs.extract_records(obj)
            if recs:
                norm_recs = []
                for raw, loc in recs:
                    rec = fs.normalize_record(raw, db)
                    rec["_source_file"] = rel
                    rec["_locator"] = loc
                    rec["_request"] = req
                    rec["_raw"] = raw
                    norm_recs.append(rec)
                    records.append(rec)
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": len(norm_recs),
                    "record_locators": [r["_locator"] for r in norm_recs],
                    "request": req,
                })
            else:
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": 0,
                    "record_locators": [], "request": req,
                    "note": "no bibliographic records detected",
                })
        elif kind == "pdf":
            text, deg = fs.parse_pdf_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
            })
        else:
            text, deg = fs.read_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })
    return sources, records, degradations


def default_ranking(candidates: List[Dict]) -> List[Dict]:
    order = {"high": 0, "medium": 1, "low": 2}
    ranked = sorted(candidates, key=lambda c: (
        order.get(c.get("relevance_hint", "low"), 2), c["record_id"]))
    return [
        {
            "record_id": c["record_id"],
            "rank": i + 1,
            "relevance": c.get("relevance_hint", "low"),
            "rationale": "deterministic relevance_hint ranking (词项命中)",
        }
        for i, c in enumerate(ranked)
    ]


def build_full_result(skeleton: Dict, sources: List[Dict], records: List[Dict],
                      degradations: List[str], at: str) -> Dict:
    res = dict(skeleton)
    res["sources"] = sources
    res["degradations"] = list(res.get("degradations", [])) + degradations
    res["change_log"] = list(res.get("change_log", []))
    res["change_log"].append({
        "step": "parse_fuse", "status": "done", "at": at,
        "detail": "parsed %d input record(s) across %d source(s)"
                  % (len(records), len([s for s in sources
                                        if s["type"] == "database_response"])),
    })

    if not records:
        has_missing_input = any(
            isinstance(e, str) and e.startswith("missing:")
            for e in res.get("errors", []))
        res["status"] = "input_error" if has_missing_input \
            else "insufficient_evidence"
        res["coverage"] = fs.build_coverage([], [])
        res["uncertainty"] = {
            "items": [{"field": "candidates",
                       "reason": "no bibliographic records detected"}],
            "note": "未检出任何文献记录，不补造",
        }
        res["change_log"].append({"step": "empty", "status": "done", "at": at,
                                  "detail": "no candidates"})
        return res

    clusters = fs.dedup_records(records)
    merged = [fs.merge_cluster(c, records) for c in clusters]
    terms = (skeleton.get("search_scope") or {}).get("derived_query_terms", [])
    for ci, m in enumerate(merged):
        first_idx = clusters[ci]["indices"][0]
        m["relevance_hint"] = fs.relevance_hint(m, terms)
        m["provenance"] = {
            "databases": m["databases"],
            "dedup_reasons": m["dedup_reason"],
            "chain": ["discover", "classify", "normalize", "dedup", "merge"],
            "request": records[first_idx].get("_request"),
        }

    res["candidates"] = merged
    res["ranking"] = default_ranking(merged)
    res["evidence_records"] = fs.build_evidence(records, clusters)
    res["coverage"] = fs.build_coverage(records, clusters)
    res["conflicts"] = _collect_conflicts(merged)
    res["missing_metadata"] = _collect_missing(merged, records)
    res["change_log"].append({
        "step": "dedup_merge", "status": "done", "at": at,
        "detail": "merged %d records into %d candidates"
                  % (len(records), len(merged)),
    })

    has_gap = (res["conflicts"] or res["missing_metadata"] or
               res["degradations"] or res["uncertainty"]["items"])
    res["status"] = "partial" if has_gap else "ok"
    return res


def _collect_conflicts(merged: List[Dict]) -> List[Dict]:
    out: List[Dict] = []
    for m in merged:
        for c in m.get("conflicts", []):
            out.append({"record_id": m["record_id"], **c})
    return out


def _collect_missing(merged: List[Dict], records: List[Dict]) -> List[Dict]:
    out: List[Dict] = []
    for m in merged:
        missing = [f for f in fs.CANONICAL_FIELDS
                   if m.get(f) in (None, "", [])]
        if missing:
            out.append({"record_id": m["record_id"],
                        "missing_fields": missing,
                        "filled_by": m.get("field_sources", {})})
    return out


def write_aux(output_dir: Path, work_dir: Path, res: Dict,
              merged: List[Dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "merged_records.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    evidence_map = {
        "evidence_records": res["evidence_records"],
        "conflicts": res["conflicts"],
        "missing_metadata": res["missing_metadata"],
        "permission_issues": res["permission_issues"],
        "empty_results": res["empty_results"],
    }
    (output_dir / "evidence_map.json").write_text(
        json.dumps(evidence_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (output_dir / "coverage_report.md").write_text(
        _coverage_md(res), encoding="utf-8")
    # 中间态，供 --resume 增量复用
    state = {
        "candidates": res["candidates"],
        "coverage": res["coverage"],
        "evidence_records": res["evidence_records"],
        "sources": res["sources"],
        "conflicts": res["conflicts"],
        "missing_metadata": res["missing_metadata"],
    }
    (work_dir / "pipeline_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def _coverage_md(res: Dict) -> str:
    cov = res.get("coverage") or {}
    lines = ["# 覆盖报告", "",
             "- case_id: %s" % res.get("case_id"),
             "- status: %s" % res.get("status"),
             "- total_input_records: %s" % cov.get("total_input_records", 0),
             "- merged_record_count: %s" % cov.get("merged_record_count", 0),
             "- duplicates_merged: %s" % cov.get("duplicates_merged", 0),
             "", "## 每库覆盖", ""]
    per = cov.get("per_database", {})
    for db in cov.get("databases", []):
        d = per.get(db, {})
        lines.append("- **%s**: record_count=%s, single_db_only=%s"
                     % (db, d.get("record_count", 0),
                        d.get("single_db_only", 0)))
        lines.append("  - fields_missing: %s"
                     % ", ".join(d.get("fields_missing", [])) or "（无）")
    lines.append("")
    lines.append("## 仅单库收录")
    single = cov.get("records_in_single_db_only", [])
    lines.append("- %s" % (", ".join(single) if single else "（无）"))
    lines.append("")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    fs.reconfigure_stdio()
    started = iso_now()
    t0 = time.time()
    phases: List[Dict] = []

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case_path = input_dir / "case.json"
    task_path = input_dir / "task.md"

    case: Optional[Dict] = None
    errors: List[str] = []
    if case_path.exists():
        case, jerrs = fs.load_json(case_path)
        errors.extend(jerrs)
    else:
        errors.append("missing:%s" % case_path.as_posix())
    task_text = ""
    if task_path.exists():
        task_text, _ = fs.read_text(task_path)

    meta = fs._meta_values(case)
    contract = case.get("output_contract") if isinstance(case, dict) else None
    if contract is not None and not isinstance(contract, list):
        contract = None
    case_id = "unknown"
    if isinstance(case, dict):
        case_id = str(_first_str(case, ("case_id", "id", "caseId")) or "unknown")

    skeleton = fs.build_skeleton(case_id, started)
    fs.ensure_contract_keys(skeleton, contract)
    skeleton["research_question"] = meta["question"]
    skeleton["search_scope"] = {
        "databases": [str(x) for x in meta["databases"]],
        "queries": [str(x) for x in meta["queries"]],
        "derived_query_terms": fs.derive_query_terms(
            meta["question"], task_text, *meta["queries"]),
    }
    skeleton["errors"] = errors
    if not case_path.exists():
        skeleton["status"] = "input_error"
    skeleton["change_log"] = [{
        "step": "skeleton", "status": "written", "at": started,
        "detail": "先落盘合法 partial 骨架，后续只做增量改进",
    }]
    write_result(output_dir, skeleton)
    phases.append(_phase_entry("P0_skeleton", started, t0, "done"))

    if args.skeleton_only:
        return _finish(output_dir, skeleton, phases, t0, started)

    # --resume：复用上次 pipeline_state.json，跳过解析与融合
    state_file = work_dir / "pipeline_state.json"
    if args.resume and state_file.exists():
        state, _ = fs.load_json(state_file)
        if isinstance(state, dict) and state.get("candidates"):
            res = dict(skeleton)
            for k in ("candidates", "coverage", "evidence_records", "sources",
                      "conflicts", "missing_metadata"):
                res[k] = state.get(k, skeleton.get(k))
            res["ranking"] = default_ranking(res["candidates"])
            res["status"] = "partial"
            res["change_log"] = list(res.get("change_log", [])) + [{
                "step": "resume", "status": "done", "at": started,
                "detail": "reused pipeline_state.json, skipped P1 parse_fuse"}]
            phases.append(_phase_entry("P1_parse_fuse", started, t0, "skipped"))
            res = _apply_and_finalize(res, args, started)
            phases.append(_phase_entry("P2_rank", started, t0, "done"))
            res["runtime"]["phases"] = phases
            res["runtime"]["finished_at"] = iso_now()
            res["runtime"]["seconds"] = round(time.time() - t0, 2)
            write_result(output_dir, res)
            write_aux(output_dir, work_dir, res, res["candidates"])
            return 0

    # 阶段 P1：解析与融合
    sources, records, degradations = parse_materials(input_dir)
    phases.append(_phase_entry("P1_parse_fuse", started, t0, "done"))

    res = build_full_result(skeleton, sources, records, degradations, started)
    res = _apply_and_finalize(res, args, started)
    phases.append(_phase_entry("P2_rank", started, t0, "done"))

    res["runtime"]["phases"] = phases
    res["runtime"]["finished_at"] = iso_now()
    res["runtime"]["seconds"] = round(time.time() - t0, 2)

    write_result(output_dir, res)
    write_aux(output_dir, work_dir, res, res["candidates"])
    return 0


def _first_str(case: Dict, keys: tuple) -> Optional[str]:
    for k in keys:
        v = case.get(k)
        if v not in (None, "", [], {}):
            return str(v)
    return None


def _apply_and_finalize(res: Dict, args: argparse.Namespace,
                        at: str) -> Dict:
    decisions = load_decisions(getattr(args, "decisions", None))
    if decisions:
        fs.apply_decisions(res, decisions, at)
    return res


def _finish(output_dir: Path, res: Dict, phases: List[Dict], t0: float,
            started: str) -> int:
    res["runtime"]["phases"] = phases
    res["runtime"]["finished_at"] = iso_now()
    res["runtime"]["seconds"] = round(time.time() - t0, 2)
    write_result(output_dir, res)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="多数据库联合检索流水线（时间预算·早落盘）")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--skeleton-only", action="store_true",
                   help="只写 partial 骨架后退出")
    p.add_argument("--decisions", default=None,
                   help="合并人工/模型仲裁 decisions.json")
    p.add_argument("--resume", action="store_true",
                   help="复用 work/pipeline_state.json，跳过解析融合")
    return p


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
