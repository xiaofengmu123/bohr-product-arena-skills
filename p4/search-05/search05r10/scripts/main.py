#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多数据库联合检索流水线入口（仅标准库，确定性，无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json \
      --input /input --output /app/output --work /app/work

流程（首步即落盘骨架，随后只做增量改进）：
  阶段 0  发现 /input，立即写 result.json 骨架（status=partial）
  阶段 1  解析数据库响应 + 论文/文本，字段归一、去重、合并、覆盖、证据
  阶段 2  确定性相关性提示 + 排序；可叠加 decisions.json 的人工/模型仲裁
  阶段 3  落盘 result.json 与 merged_records.json / evidence_map.json / coverage_report.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import core_impl as ci


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def build_skeleton(case_id: str, started: str) -> Dict[str, Any]:
    """先落盘一版合法 partial 结果；任何异常都不留下空产物。"""
    res: Dict[str, Any] = {
        "case_id": case_id,
        "status": "partial",
        "research_question": None,
        "search_scope": {"databases": [], "queries": [], "derived_query_terms": []},
        "candidates": [],
        "ranking": [],
        "evidence_records": [],
        "coverage": None,
        "empty_results": [],
        "conflicts": [],
        "missing_metadata": [],
        "permission_issues": [],
        "limitations": [],
        "sources": [],
        "uncertainty": {"items": [], "note": "initial skeleton before material parsing"},
        "change_log": [
            {"step": "skeleton", "status": "written", "at": started,
             "detail": "先落盘合法 partial 骨架，后续只做增量改进"},
        ],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {
            "started_at": started, "finished_at": None, "seconds": 0,
            "pipeline_version": ci.VERSION, "python": sys.version.split()[0],
        },
    }
    return res


def ensure_contract_keys(res: Dict[str, Any], contract: Optional[List[str]]) -> None:
    """保证题目 output_contract 的每个键都存在；缺值填 null，不改名不删键。"""
    keys = contract if isinstance(contract, list) else ci.DEFAULT_CONTRACT
    for k in keys:
        if k not in res:
            res[k] = None
    # 固定字段兜底
    for k in ci.FIXED_FIELDS:
        if k not in res:
            res[k] = None


def apply_decisions(res: Dict[str, Any], decisions: Dict[str, Any], at: str) -> List[str]:
    """把 decisions.json 的人工/模型仲裁合并进结果；返回已应用项。"""
    applied: List[str] = []
    if not isinstance(decisions, dict):
        return applied
    for key in ("status", "ranking", "screening", "uncertainty", "limitations",
                "pending_human", "empty_results", "conflicts", "permission_issues",
                "missing_metadata"):
        if key not in decisions or decisions[key] is None:
            continue
        if key == "status":
            if decisions[key] in ci.STATUSES:
                res["status"] = decisions[key]
                applied.append("status")
        elif key == "ranking":
            res["ranking"] = decisions[key]
            applied.append("ranking")
        elif key == "screening":
            res.setdefault("screening", decisions[key])
            res["screening"] = decisions[key]
            applied.append("screening")
        else:
            res[key] = decisions[key]
            applied.append(key)
    if applied:
        res.setdefault("change_log", []).append({
            "step": "model_refine", "status": "applied", "at": at,
            "detail": "applied decisions: " + ", ".join(applied),
        })
    return applied


def extract_output_contract(case: Dict[str, Any]) -> Optional[List[str]]:
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        keys = oc.get("fields") or oc.get("keys")
        if isinstance(keys, list):
            return [str(k) for k in keys]
    if isinstance(oc, list):
        return [str(k) for k in oc]
    return None


def parse_materials(input_dir: Path) -> Dict[str, Any]:
    """扫描 /input 全部文件，解析数据库响应与文档；返回 sources / records / degradations。"""
    sources: List[Dict[str, Any]] = []
    all_records: List[Dict[str, Any]] = []
    degradations: List[str] = []
    pdf_texts: List[Tuple[Path, str]] = []

    for p in ci.discover_input_files(input_dir):
        rel = p.relative_to(input_dir).as_posix()
        kind = ci.classify_file(p)
        if kind == "json":
            obj, errs = ci.load_json(p)
            degradations.extend(errs)
            if obj is None:
                degradations.append("json_unparsed: %s" % rel)
                continue
            db = ci.fingerprint(obj)
            recs = ci.extract_records(obj)
            if recs:
                norm_recs: List[Dict[str, Any]] = []
                for raw, loc in recs:
                    rec = ci.normalize_record(raw, db)
                    rec["_db"] = db
                    rec["_source_file"] = rel
                    rec["_locator"] = loc
                    rec["_request"] = ci.extract_request(obj)
                    rec["_raw"] = raw
                    norm_recs.append(rec)
                    all_records.append(rec)
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": len(norm_recs),
                    "record_locators": [r["_locator"] for r in norm_recs],
                })
            else:
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": 0,
                    "record_locators": [], "note": "no bibliographic records detected",
                })
        elif kind == "pdf":
            text, deg = ci.parse_pdf_text(p)
            degradations.extend(deg)
            pdf_texts.append((p, text))
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
            })
        else:
            text, deg = ci.read_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })
    return {"sources": sources, "records": all_records, "degradations": degradations,
            "pdf_texts": pdf_texts}


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ci.safe_json_dumps(obj), encoding="utf-8")


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path,
                 skeleton_only: bool = False, decisions: Optional[Dict[str, Any]] = None,
                 started: Optional[str] = None) -> Dict[str, Any]:
    started = started or iso_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case, errors = ci.load_case_meta(input_dir)
    case_id = ci.case_id_from(case, input_dir)
    contract = extract_output_contract(case)

    # 第一步：立即落盘骨架。
    res = build_skeleton(case_id, started)
    res["errors"] = errors
    res["runtime"]["started_at"] = started
    ensure_contract_keys(res, contract)
    result_path = output_dir / "result.json"
    write_json(result_path, res)

    if skeleton_only:
        res["runtime"]["finished_at"] = iso_now()
        res["runtime"]["seconds"] = round(max(0.0, time.time() - _parse_started(started)), 1)
        write_json(result_path, res)
        return res

    # 阶段 1：解析材料。
    materials = parse_materials(input_dir)
    res["sources"] = materials["sources"]
    res["degradations"].extend(materials["degradations"])
    res["change_log"].append({
        "step": "parse_materials", "status": "done", "at": iso_now(),
        "detail": f"parsed {len(materials['sources'])} source files, {len(materials['records'])} raw records",
    })

    rq = ci.research_question_from(case, "")
    # task.md 已在 load_case_meta 读取，此处仅用其文本辅助提取研究问题。
    task_text = ""
    tp = input_dir / "task.md"
    if tp.exists():
        task_text, _ = ci.read_text(tp)
    rq = ci.research_question_from(case, task_text)
    res["research_question"] = rq
    res["search_scope"] = ci.search_scope_from(case, rq)

    # 阶段 1 继续：归一 -> 去重 -> 合并。
    clusters = ci.cluster_records(materials["records"])
    merged = [ci.merge_cluster(cid, members, reason) for cid, members, reason in clusters]
    res["candidates"] = merged
    res["coverage"] = ci.coverage_metrics(merged, materials["records"])
    res["evidence_records"] = ci.evidence_records_for(merged, materials["records"])

    # 阶段 2：相关性排序（模型可在后续 decisions.json 覆盖）。
    terms = ci.query_terms_from(case, rq)
    res["ranking"] = ci.rank_candidates(merged, terms)

    # 冲突 / 缺失元数据汇总。
    for m in merged:
        res["conflicts"].extend(m.get("conflicts", []))
        for field in ci.FIELD_LABELS:
            if field in ("identifiers",):
                continue
            if m.get(field) in (None, "", [], {}):
                res["missing_metadata"].append({
                    "record_id": m["record_id"], "field": field,
                    "note": "not reported in any source",
                })

    # 权限/空结果说明。
    empty_files = [s for s in materials["sources"] if s.get("record_count", 0) == 0]
    for s in empty_files:
        res["empty_results"].append({
            "source_file": s.get("path"),
            "db": s.get("db"),
            "note": s.get("note", "no bibliographic records detected"),
        })
    res["permission_issues"] = []

    # 阶段 3：状态判定（诚实取值；缺失元数据单独报告，不必然降级）。
    missing_case = any(e.startswith("missing: ") and e.endswith("case.json") for e in errors)
    if missing_case and not (input_dir / "case.json").exists():
        res["status"] = "input_error"
    elif not res["candidates"]:
        res["status"] = "insufficient_evidence"
        res["limitations"].append("no bibliographic candidates extracted from /input")
    elif res["degradations"] or res["conflicts"]:
        res["status"] = "partial"
    else:
        res["status"] = "ok"

    # 应用 decisions（人工/模型仲裁）。
    if decisions is not None:
        applied = apply_decisions(res, decisions, iso_now())
        if applied:
            res["change_log"].append({
                "step": "decisions", "status": "merged", "at": iso_now(),
                "detail": "decisions merged: " + ", ".join(applied),
            })

    # 阶段 3：落盘辅助产物（可打开、可编辑、可复现）。
    write_json(work_dir / "merged_records.json", merged)
    evidence_map = {
        "records": res["evidence_records"],
        "conflicts": res["conflicts"],
        "missing_metadata": res["missing_metadata"],
    }
    write_json(work_dir / "evidence_map.json", evidence_map)
    (work_dir / "coverage_report.md").write_text(
        ci.safe_json_dumps(res["coverage"]) if res["coverage"] else "# coverage\n\n(null)\n",
        encoding="utf-8")

    res["runtime"]["finished_at"] = iso_now()
    res["runtime"]["seconds"] = round(time.time() - _parse_started(started), 1)
    res["change_log"].append({
        "step": "finalize", "status": "written", "at": iso_now(),
        "detail": "result.json and auxiliary artifacts written",
    })
    write_json(result_path, res)
    return res


def _parse_started(started: str) -> float:
    try:
        s = started.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return time.time()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="多数据库联合检索流水线（确定性、无网络、仅标准库）")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--skeleton-only", action="store_true",
                   help="只写一版 partial 骨架后退出")
    p.add_argument("--decisions", default=None,
                   help="人工/模型仲裁 decisions.json 路径")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_stdio()
    args = build_parser().parse_args(argv)
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)

    decisions: Optional[Dict[str, Any]] = None
    if args.decisions:
        dp = Path(args.decisions)
        if dp.exists():
            try:
                decisions = json.loads(dp.read_text(encoding="utf-8-sig", errors="replace"))
            except Exception as e:  # noqa: BLE001
                print(f"warning: cannot parse decisions {dp}: {e}", file=sys.stderr)

    try:
        res = run_pipeline(input_dir, output_dir, work_dir,
                           skeleton_only=args.skeleton_only, decisions=decisions)
        print(json.dumps({"case_id": res.get("case_id"), "status": res.get("status"),
                          "result": (output_dir / "result.json").as_posix()},
                         ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001
        # 最终兜底：哪怕流水线异常也保证存在合法 result.json。
        try:
            res = build_skeleton("unknown", iso_now())
            res["status"] = "input_error"
            res["errors"].append(f"pipeline_error: {type(e).__name__}: {e}")
            write_json(output_dir / "result.json", res)
        except Exception:
            pass
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
