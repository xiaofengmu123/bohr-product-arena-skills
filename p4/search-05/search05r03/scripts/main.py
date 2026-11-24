#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多数据库联合检索流水线入口（仅标准库、确定性、无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json --input /input --output /app/output

流程：
  1. 读取 /input/case.json 与 /input/task.md，立即写入 result.json 合法 partial 骨架
  2. 发现 /input 下全部材料，解析数据库响应与论文 PDF
  3. 字段归一 -> 可解释去重 -> 稳定合并 -> 覆盖比较 -> 证据记录 -> 词项相关性排序
  4. 落盘 result.json + merged_records.json / evidence_map.json / coverage_report.md
  5. 若提供 --decisions，则用仲裁结果覆盖排序/状态/局限后再次落盘
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import fedcore as fs


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _extract_response_query(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    for key in ("query", "q", "queryString", "query_string", "searchTerm", "term"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    req = obj.get("request")
    if isinstance(req, dict):
        for key in ("queryString", "query", "q", "search", "query_term"):
            v = req.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _case_queries(case: Dict) -> List[str]:
    out: List[str] = []
    for k in ("queries", "keywords", "search_terms", "terms"):
        v = case.get(k)
        if isinstance(v, list):
            out.extend(str(x) for x in v if x is not None)
        elif isinstance(v, str) and v.strip():
            out.append(v.strip())
    return _dedupe_preserve_order(out)


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _load_case(input_dir: Path) -> Dict:
    """读取 case.json；缺失/坏 JSON 返回占位 dict，错误交给调用方记录。"""
    case_path = input_dir / "case.json"
    if not case_path.exists():
        return {"__missing_case": True}
    obj, errs = fs.load_json(case_path)
    if obj is None:
        return {"__bad_case": True, "__errors": errs}
    return obj if isinstance(obj, dict) else {"__bad_case": True, "__errors": ["case.json not a dict"]}


def _case_id(case: Dict) -> str:
    for k in ("case_id", "caseId", "id"):
        v = case.get(k)
        if v:
            return str(v)
    return "unknown"


def parse_input_materials(input_dir: Path) -> Dict[str, Any]:
    """解析输入目录全部材料，返回 sources / records / degradations / pdf_texts。"""
    records: List[Dict] = []
    sources: List[Dict] = []
    degradations: List[str] = []
    pdf_texts: List[Dict] = []
    case_queries: List[str] = []

    files, deg = fs.discover_input_files(input_dir)
    degradations.extend(deg)
    for p in files:
        rel = p.relative_to(input_dir).as_posix()
        kind = fs.classify_file(p)
        if kind == "json":
            obj, errs = fs.load_json(p)
            degradations.extend(("json:%s: %s" % (rel, e)) for e in errs)
            if obj is None:
                degradations.append("json_unparsed: %s" % rel)
                continue
            db = fs.classify_response(obj)
            resp_query = _extract_response_query(obj)
            recs = fs.extract_records(obj, db)
            norm_records: List[Dict] = []
            for raw, loc in recs:
                rec = fs.normalize_record(raw, db)
                rec["_db"] = db
                rec["_source_file"] = rel
                rec["_locator"] = loc
                rec["_request"] = {"query": resp_query, "case_queries": case_queries}
                rec["record_id"] = "src:%s:%s" % (db, fs.safe_name(loc))
                rec["_snapshot"] = _verbatim_snapshot(raw)
                rec["_raw"] = raw
                norm_records.append(rec)
                records.append(rec)
            sources.append({
                "source_id": "file:" + rel, "type": "database_response", "db": db,
                "path": rel, "record_count": len(norm_records),
                "record_locators": [r["_locator"] for r in norm_records],
                "query": resp_query,
            } if norm_records else {
                "source_id": "file:" + rel, "type": "database_response", "db": db,
                "path": rel, "record_count": 0, "record_locators": [],
                "query": resp_query,
                "note": "no bibliographic records detected (config or empty response)",
            })
        elif kind == "pdf":
            text, deg = fs.parse_pdf_text(p)
            degradations.extend(deg)
            pdf_texts.append({"path": rel, "text_chars": len(text), "text": text})
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
            })
        else:
            text, deg = fs.read_text_robust(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })
    return {"records": records, "sources": sources, "degradations": degradations,
            "pdf_texts": pdf_texts, "case_queries": case_queries}


def _verbatim_snapshot(raw: Dict) -> str:
    try:
        s = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:  # noqa: BLE001
        s = str(raw)
    return s[: fs.MAX_SNAPSHOT_CHARS]


def _append_to_record_requests(records: List[Dict], case_queries: List[str]) -> None:
    for r in records:
        if isinstance(r.get("_request"), dict):
            r["_request"]["case_queries"] = case_queries


def _merge_candidates(records: List[Dict], case: Dict) -> Dict[str, Any]:
    """去重、合并、相关性、覆盖、证据、排序。"""
    clusters = fs.cluster_records(records)
    conflicts: List[Dict] = []
    merged_list: List[Dict] = []
    for i, clu in enumerate(clusters):
        merged, conf = fs.merge_cluster(clu, i, len(clusters))
        merged_list.append(merged)
        conflicts.extend(conf)
    terms = fs.build_terms(case)
    for m in merged_list:
        m["relevance_hint"] = fs.relevance_score(m, terms)
    # 默认确定性排序：相关性分 -> 年份降 -> 标题 -> record_id
    merged_list.sort(key=lambda m: (
        -m["relevance_hint"]["score"],
        -(m.get("year") or 0),
        fs.title_key(m.get("title")),
        m["record_id"],
    ))
    ranking: List[Dict] = []
    for rank, m in enumerate(merged_list, 1):
        ranking.append({
            "record_id": m["record_id"], "rank": rank,
            "relevance": m["relevance_hint"]["label"],
            "score": m["relevance_hint"]["score"],
            "rationale": "lexical_overlap score=%d matched=%s" % (
                m["relevance_hint"]["score"],
                ",".join(m["relevance_hint"]["matched_terms"]) or "-",
            ),
        })
    coverage = fs.build_coverage(records, clusters, merged_list)
    evidence = fs.build_evidence(merged_list)
    # 缺失元数据（确定性列举）
    missing_metadata: List[Dict] = []
    for m in merged_list:
        missing = [f for f in ("title", "authors", "year", "journal", "doi")
                   if m.get(f) in (None, "", [])]
        if missing:
            missing_metadata.append({"record_id": m["record_id"], "missing_fields": missing})
    db_set = sorted({r["_db"] for r in records})
    search_scope = {
        "databases": case.get("databases") or db_set,
        "queries": (case.get("queries") or case.get("keywords") or case.get("search_terms")) if isinstance(
            case, dict) else [],
        "derived_query_terms": terms,
    }
    return {
        "candidates": merged_list, "ranking": ranking, "evidence_records": evidence,
        "coverage": coverage, "conflicts": conflicts, "missing_metadata": missing_metadata,
        "search_scope": search_scope,
    }


def _status_for(res: Dict, records: List[Dict], degradations: List[str],
                errors: List[str], missing_case: bool, bad_case: bool) -> str:
    if missing_case or bad_case:
        return "input_error"
    if not records and not res.get("candidates"):
        return "insufficient_evidence"
    cov = res.get("coverage") or {}
    systematic_gap = bool(cov.get("systematically_missing_fields"))
    if (degradations or errors or res.get("conflicts") or res.get("missing_metadata")
            or res.get("limitations") or res.get("empty_results") or systematic_gap):
        return "partial"
    return "ok"


def run_pipeline(input_dir: Path, output_dir: Path, work_dir: Path,
                 decisions_path: Optional[Path] = None, skeleton_only: bool = False) -> Dict:
    """主流水线。可被 selftest 复用；返回 result dict。"""
    started = fs.now_iso()
    t0 = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case = _load_case(input_dir)
    missing_case = bool(case.get("__missing_case"))
    bad_case = bool(case.get("__bad_case"))
    case_errors = case.get("__errors", []) if bad_case else []
    case_id = _case_id(case)
    case_queries = _case_queries(_strip_private(case))

    res = fs.build_skeleton(case_id, started, work_dir)
    res["case_id"] = case_id
    res["research_question"] = _first_str(case, "research_question", "question", "task")
    res["sources"] = []
    res["coverage"] = {"databases": [], "total_input_records": 0, "merged_record_count": 0,
                        "duplicates_merged": 0, "records_in_single_db_only": [],
                        "systematically_missing_fields": []}
    if missing_case:
        res["errors"].append("missing: %s" % (input_dir / "case.json").as_posix())
    if bad_case:
        res["errors"].extend(case_errors)

    # 立即嵌回 output_contract 键（若 case.json 声明了），再落盘骨架
    contract = None
    if isinstance(case, dict):
        contract = case.get("output_contract")
    fs.ensure_contract(res, contract)

    # 首次落盘：无论后续成败，result.json 已合法存在且状态为 partial
    _write_result(output_dir, res, work_dir)

    if skeleton_only:
        res["runtime"]["finished_at"] = fs.now_iso()
        res["runtime"]["seconds"] = round(time.time() - t0, 2)
        res["runtime"]["content_fingerprint"] = fs.compute_fingerprint(res)
        _write_result(output_dir, res, work_dir)
        return res

    materials = parse_input_materials(input_dir)
    records: List[Dict] = materials["records"]
    sources: List[Dict] = materials["sources"]
    degradations: List[str] = materials["degradations"]
    pdf_texts: List[Dict] = materials["pdf_texts"]

    _append_to_record_requests(records, _case_queries(_strip_private(case)))
    res["sources"] = sources
    res["degradations"].extend(degradations)

    merged = _merge_candidates(records, _strip_private(case))
    res["candidates"] = merged["candidates"]
    res["ranking"] = merged["ranking"]
    res["evidence_records"] = merged["evidence_records"]
    res["coverage"] = merged["coverage"]
    res["conflicts"] = merged["conflicts"]
    res["missing_metadata"] = merged["missing_metadata"]
    res["search_scope"] = merged["search_scope"]

    # 空结果与缺口说明
    empty_sources = [s for s in sources if not s.get("record_count")]
    for s in empty_sources:
        res["empty_results"].append({"source": s["source_id"], "note": s.get("note", "no records")})
    if records and not merged["candidates"]:
        res["empty_results"].append({"note": "input records present but no candidates merged"})

    if pdf_texts and not any(t["text_chars"] for t in pdf_texts):
        res["limitations"].append("PDF text not extracted (pdftotext unavailable); PDFs listed by path only")

    # 状态诚实取值
    res["status"] = _status_for(res, records, degradations, res["errors"], missing_case, bad_case)

    res.setdefault("change_log", []).append({
        "step": "pipeline", "status": "done", "at": fs.now_iso(),
        "detail": "parsed %d sources, %d input records, %d merged candidates" % (
            len(sources), len(records), len(res["candidates"])),
    })

    # 决策仲裁（若提供）
    if decisions_path is not None and decisions_path.exists():
        decisions, _ = fs.load_json(decisions_path)
        if isinstance(decisions, dict):
            fs.apply_decisions(res, decisions, fs.now_iso())

    res["runtime"]["finished_at"] = fs.now_iso()
    res["runtime"]["seconds"] = round(time.time() - t0, 2)
    res["runtime"]["python"] = sys.version.split()[0]
    res["runtime"]["content_fingerprint"] = fs.compute_fingerprint(res)

    _write_result(output_dir, res, work_dir)
    return res


def _strip_private(case: Dict) -> Dict:
    return {k: v for k, v in case.items() if not k.startswith("__")}


def _first_str(case: Dict, *keys: Any) -> Optional[str]:
    for k in keys:
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _write_result(output_dir: Path, res: Dict, work_dir: Path) -> None:
    """确定性落盘主产物与辅助产物。"""
    path = output_dir / "result.json"
    path.write_text(fs._stable_json(res), encoding="utf-8")

    try:
        merged_payload = {
            "case_id": res.get("case_id"),
            "generated_meta": {"pipeline_version": fs.VERSION, "note": "editable, reproducible"},
            "candidates": res.get("candidates"),
        }
        (work_dir / "merged_records.json").write_text(fs._stable_json(merged_payload), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        evidence_payload = {
            "case_id": res.get("case_id"),
            "evidence_records": res.get("evidence_records"),
            "conflicts": res.get("conflicts"),
            "missing_metadata": res.get("missing_metadata"),
        }
        (work_dir / "evidence_map.json").write_text(fs._stable_json(evidence_payload), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        (work_dir / "coverage_report.md").write_text(_coverage_md(res), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _coverage_md(res: Dict) -> str:
    lines = ["# Coverage report", "", "case_id: %s" % res.get("case_id"),
             "status: %s" % res.get("status"), ""]
    cov = res.get("coverage") or {}
    lines.append("- databases: %s" % ", ".join(cov.get("databases", [])))
    lines.append("- total_input_records: %s" % cov.get("total_input_records"))
    lines.append("- merged_record_count: %s" % cov.get("merged_record_count"))
    lines.append("- duplicates_merged: %s" % cov.get("duplicates_merged"))
    lines.append("- records_in_single_db_only: %s" % ", ".join(cov.get("records_in_single_db_only", [])))
    lines.append("- systematically_missing_fields: %s" % ", ".join(cov.get("systematically_missing_fields", [])))
    lines.append("")
    for d in cov.get("per_database", []):
        lines.append("## %s" % d.get("db"))
        lines.append("- records: %s" % d.get("record_count"))
        lines.append("- fields_missing: %s" % ", ".join(d.get("fields_missing", [])))
        lines.append("")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py", description="多数据库联合检索流水线（确定性、标准库、无网络）")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--decisions", default=None, help="可选 decisions.json 仲裁文件路径")
    p.add_argument("--skeleton-only", action="store_true", help="仅落盘 partial 骨架后退出")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_stdio()
    args = _build_parser().parse_args(argv)
    res = run_pipeline(
        Path(args.input), Path(args.output), Path(args.work),
        decisions_path=Path(args.decisions) if args.decisions else None,
        skeleton_only=args.skeleton_only,
    )
    print("status=%s candidates=%d fingerprint=%s" % (
        res.get("status"), len(res.get("candidates", [])),
        (res.get("runtime") or {}).get("content_fingerprint", "?")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())