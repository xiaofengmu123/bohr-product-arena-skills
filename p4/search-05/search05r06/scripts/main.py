#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多数据库联合检索流水线入口（仅标准库，确定性，无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json \
      --input /input --output /app/output --work /app/work

流程：
  1. 读 /input/case.json 与 /input/task.md，立即写 result.json 骨架（status=partial）
     ——满足“前 3 分钟先落盘可复核骨架”的硬约束。
  2. 发现并解析 /input 下所有材料（JSON 响应 / PDF / 文本）。
  3. 字段归一 -> 去重 -> 合并 -> 覆盖 -> 证据 -> 相关性提示 -> 基线排序。
  4. 落盘中间表示 plan.json / record_model.json，再生成 result.json 与
     coverage_report.md。
  5. 若提供 --decisions，则用模型/人工仲裁覆盖排序、状态、局限等，再落盘。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import core_impl as fs


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 契约 / 骨架
# --------------------------------------------------------------------------- #
def resolve_contract(case: Dict[str, Any]) -> Optional[List[str]]:
    """从 case.json 解析 output_contract；支持 list（键名）与 dict（键 -> 默认值）。"""
    oc = case.get("output_contract")
    if isinstance(oc, list):
        return [str(k) for k in oc]
    if isinstance(oc, dict):
        return [str(k) for k in oc.keys()]
    return None


def contract_defaults(case: Dict[str, Any]) -> Dict[str, Any]:
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        out = {}
        for k, spec in oc.items():
            if isinstance(spec, dict) and "default" in spec:
                out[str(k)] = spec["default"]
            elif isinstance(spec, str):
                out[str(k)] = {"string": "", "list": [], "dict": {}, "null": None}.get(spec.lower(), None)
            else:
                out[str(k)] = None
        return out
    return {}


def build_skeleton(case_id: str, started: str, contract_keys: List[str],
                   defaults: Dict[str, Any]) -> Dict[str, Any]:
    skel: Dict[str, Any] = {
        "case_id": case_id,
        "status": "partial",
        "research_question": None,
        "search_scope": None,
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
        "uncertainty": {"items": [], "note": "initial skeleton written before analysis"},
        "change_log": [
            {"step": "skeleton", "status": "written", "at": started,
             "detail": "先落盘合法 partial 骨架，后续只做增量改进"},
        ],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": started, "finished_at": None, "seconds": 0,
                    "pipeline_version": fs.VERSION, "python": sys.version.split()[0]},
    }
    for k in contract_keys:
        skel.setdefault(k, defaults.get(k))
    return skel


def ensure_contract_keys(res: Dict[str, Any], contract_keys: Optional[List[str]],
                         defaults: Dict[str, Any]) -> None:
    keys = contract_keys if contract_keys is not None else fs.DEFAULT_CONTRACT
    for k in keys:
        if k not in res:
            res[k] = defaults.get(k)


# --------------------------------------------------------------------------- #
# 决策合并
# --------------------------------------------------------------------------- #
def apply_decisions(res: Dict[str, Any], decisions: Dict[str, Any], at: str) -> List[str]:
    applied: List[str] = []
    if not isinstance(decisions, dict):
        return applied
    for key in ("status", "ranking", "screening", "uncertainty", "limitations",
                "pending_human", "empty_results", "conflicts", "permission_issues"):
        if key in decisions and decisions[key] is not None:
            if key == "status":
                if decisions[key] in fs.STATUSES:
                    res["status"] = decisions[key]
                    applied.append("status")
            elif key == "ranking":
                res["ranking"] = decisions[key]
                applied.append("ranking")
            elif key == "screening":
                res["screening"] = decisions[key]
                applied.append("screening")
            elif key == "uncertainty":
                res["uncertainty"] = decisions[key]
                applied.append("uncertainty")
            else:
                res[key] = decisions[key]
                applied.append(key)
    if applied:
        res.setdefault("change_log", []).append(
            {"step": "model_refine", "status": "applied", "at": at,
             "detail": "applied decisions: " + ", ".join(applied)}
        )
    return applied


# --------------------------------------------------------------------------- #
# 材料解析
# --------------------------------------------------------------------------- #
def read_case(input_dir: Path) -> Dict[str, Any]:
    """读取 case.json / task.md；缺 case.json 时返回 input_error 上下文。"""
    case: Dict[str, Any] = {}
    errors: List[str] = []
    question: Optional[str] = None
    queries: List[str] = []
    search_scope: Optional[Dict[str, Any]] = None
    case_path = input_dir / "case.json"
    if not case_path.is_file():
        errors.append("missing: %s" % case_path)
        return {"case": case, "errors": errors, "question": question,
                "queries": queries, "search_scope": search_scope}
    obj, errs = fs.load_json(case_path)
    errors.extend(errs)
    if isinstance(obj, dict):
        case = obj
        question = fs._first_str(obj.get("research_question") or obj.get("question") or obj.get("task"))
        q = obj.get("queries") or obj.get("keywords") or obj.get("search_terms")
        if isinstance(q, str):
            queries = [q]
        elif isinstance(q, list):
            queries = [str(x) for x in q if x]
        scope = obj.get("search_scope")
        if isinstance(scope, dict):
            search_scope = scope
    else:
        errors.append("case.json 顶层不是对象")
    task_md = input_dir / "task.md"
    if task_md.is_file():
        t, _ = fs.read_text(task_md)
        # task.md 只作事实依据记录，不当作答案来源
    return {"case": case, "errors": errors, "question": question,
            "queries": queries, "search_scope": search_scope}


def parse_materials(input_dir: Path) -> Dict[str, Any]:
    """解析输入目录里的数据库响应与论文；返回 sources / records / degradations / queries_by_file。"""
    sources: List[Dict[str, Any]] = []
    all_records: List[Dict[str, Any]] = []
    degradations: List[str] = []
    queries_by_file: Dict[str, Optional[str]] = {}
    pdf_texts: List[Tuple[Path, str]] = []

    for p in fs.discover_input_files(input_dir):
        if p.name in {"case.json", "task.md"}:
            continue
        rel = p.relative_to(input_dir).as_posix()
        kind = fs.classify_file(p)
        if kind == "json":
            obj, errs = fs.load_json(p)
            degradations.extend(errs)
            if obj is None:
                degradations.append("json_unparsed: %s" % rel)
                continue
            db = fs.classify_response(obj)
            recs = fs.extract_records(obj)
            queries_by_file[rel] = fs.extract_request(obj)
            if recs:
                norm_recs = []
                for idx, (raw, loc) in enumerate(recs):
                    rec = fs.normalize_record(raw, db)
                    rec["_db"] = db
                    rec["_source_file"] = rel
                    rec["_locator"] = loc
                    rec["_request"] = queries_by_file[rel]
                    rec["_raw"] = raw
                    rec["_idx"] = idx
                    rec["_raw_snapshot"] = _snapshot(raw, db)
                    norm_recs.append(rec)
                    all_records.append(rec)
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": len(norm_recs),
                    "record_locators": [r["_locator"] for r in norm_recs],
                    "fields_missing": _fields_missing(norm_recs),
                })
            else:
                sources.append({
                    "source_id": "file:" + rel, "type": "database_response",
                    "db": db, "path": rel, "record_count": 0, "record_locators": [],
                    "note": "no bibliographic records detected (config or empty response)",
                })
        elif kind == "pdf":
            text, deg = fs.parse_pdf_text(p)
            degradations.extend(deg)
            pdf_texts.append((p, text))
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
            })
        elif kind == "text":
            text, deg = fs.read_text(p)
            degradations.extend(deg)
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })
        else:
            sources.append({
                "source_id": "file:" + rel, "type": "other", "path": rel,
                "note": "unsupported file type, skipped",
            })

    return {"sources": sources, "records": all_records, "degradations": degradations,
            "queries_by_file": queries_by_file, "pdf_texts": pdf_texts}


def _snapshot(raw: Any, db: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    keys = ["title", "DOI", "doi", "pmid", "pmcid", "pubYear", "year",
            "container-title", "journalTitle", "fulljournalname", "uid", "pageInfo", "pubdate"]
    out: Dict[str, Any] = {}
    for k in keys:
        if k in raw and raw[k] not in (None, "", []):
            out[k] = raw[k]
    if not out.get("title"):
        for k in ("title", "Title"):
            if raw.get(k):
                out["title"] = raw[k]
    # 作者逐字片段
    for k in ("authorString", "author"):
        if raw.get(k):
            out.setdefault(k, raw[k])
    return out


def _fields_missing(norm_recs: List[Dict[str, Any]]) -> List[str]:
    if not norm_recs:
        return []
    missing = set(fs.CANONICAL_SCALAR_FIELDS) | {"authors", "first_author", "issn"}
    for r in norm_recs:
        for f in list(missing):
            v = r.get(f)
            has = v not in (None, "", []) and (not isinstance(v, list) or len(v) > 0)
            if has:
                missing.discard(f)
        if not missing:
            break
    return sorted(missing)


# --------------------------------------------------------------------------- #
# 派生状态
# --------------------------------------------------------------------------- #
def derive_status(candidates: List[Any], degradations: List[str], conflicts: List[Any],
                  missing: List[str], permissions: List[str], had_case_error: bool,
                  decisions_status: Optional[str]) -> str:
    if decisions_status in fs.STATUSES:
        return decisions_status
    if had_case_error:
        return "input_error"
    if not candidates:
        return "insufficient_evidence"
    if degradations or conflicts or missing or permissions:
        return "partial"
    return "ok"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(input_dir: Path, output_dir: Path, work_dir: Path,
        decisions_path: Optional[Path], skeleton_only: bool) -> Dict[str, Any]:
    started = iso_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case_info = read_case(input_dir)
    case = case_info["case"]
    case_id = str(case.get("case_id") or case.get("id") or (input_dir / "case.json").stem)
    contract_keys = resolve_contract(case)
    defaults = contract_defaults(case)
    question = case_info["question"]
    queries = case_info["queries"]
    search_scope = case_info["search_scope"]

    result = build_skeleton(case_id, started, contract_keys or fs.DEFAULT_CONTRACT, defaults)
    result["research_question"] = question
    result["search_scope"] = search_scope or {
        "databases": [], "queries": queries, "derived_query_terms": [],
    }

    # 第 0 步：立即落盘骨架（先落盘，再分析）
    _write_json(output_dir / "result.json", result)
    if skeleton_only:
        return {"status": result["status"], "result": str(output_dir / "result.json"),
                "skeleton_only": True}

    # 解析材料
    materials = parse_materials(input_dir)
    sources = materials["sources"]
    records = materials["records"]
    degradations = materials["degradations"]
    queries_by_file = materials["queries_by_file"]

    result["sources"] = [
        {k: v for k, v in s.items() if k != "fields_missing"} for s in sources
    ]
    result["degradations"] = degradations
    result["errors"] = [e for e in case_info["errors"] if e]

    # 去重
    clusters, dedup_notes = fs.dedup_records(records)
    merged: List[Dict[str, Any]] = []
    all_conflicts: List[Dict[str, Any]] = []
    for cid, members in enumerate(clusters):
        m, conflicts = fs.merge_cluster([records[i] for i in members], cid)
        merged.append(m)
        all_conflicts.extend(conflicts)

    # 相关性提示 + 基线排序
    for m in merged:
        m["relevance_hint"] = fs.relevance_hint(m, question, queries)
    result["candidates"] = merged
    result["ranking"] = fs.baseline_ranking(merged)
    result["conflicts"] = all_conflicts
    result["evidence_records"] = fs.build_evidence(merged, all_conflicts, queries_by_file)

    # 覆盖（把 per-source 的 fields_missing 合入 coverage）
    coverage = fs.build_coverage(sources, merged, len(records), len(merged))
    for s in sources:
        if s.get("db") and s["db"] in coverage["systematically_missing_fields"]:
            coverage["systematically_missing_fields"][s["db"]]["fields_missing"] = s.get("fields_missing", [])
    result["coverage"] = coverage

    # 空结果 / 缺失元数据
    for s in sources:
        if s.get("type") == "database_response" and s.get("record_count") == 0:
            result["empty_results"].append({
                "source_file": s.get("path"), "db": s.get("db"),
                "note": s.get("note") or "no records",
            })
    missing_metadata = []
    for m in merged:
        for field in fs.CANONICAL_SCALAR_FIELDS + ["authors", "issn"]:
            v = m.get(field)
            has = v not in (None, "", []) and (not isinstance(v, list) or len(v) > 0)
            if not has:
                missing_metadata.append({"record_id": m["record_id"], "field": field})
    result["missing_metadata"] = missing_metadata

    # 中间表示
    plan = fs.build_plan(input_dir, sources, case_id, question, queries,
                         degradations, dedup_notes)
    record_model = fs.build_record_model(merged, clusters, records, all_conflicts)

    # 模型/人工仲裁
    decisions: Dict[str, Any] = {}
    if decisions_path and decisions_path.is_file():
        obj, errs = fs.load_json(decisions_path)
        degradations.extend(errs)
        if isinstance(obj, dict):
            decisions = obj
    applied = apply_decisions(result, decisions, iso_now())

    # 诚实状态
    result["status"] = derive_status(
        merged, degradations, all_conflicts, missing_metadata, result.get("permission_issues") or [],
        bool(case_info["errors"]), decisions.get("status"),
    )
    if applied:
        result["status"] = decisions.get("status", result["status"]) if "status" in applied else result["status"]

    # 完成时间
    result["runtime"]["finished_at"] = iso_now()
    result["change_log"].append({
        "step": "pipeline", "status": "done", "at": iso_now(),
        "detail": "parsed %d files, %d raw records, %d merged, %d conflicts" % (
            len(sources), len(records), len(merged), len(all_conflicts)),
    })

    ensure_contract_keys(result, contract_keys, defaults)

    # 落盘：先写中间表示，再从中派生最终 result.json
    _write_json(output_dir / "plan.json", plan)
    _write_json(output_dir / "record_model.json", record_model)
    _write_json(output_dir / "result.json", result)
    _write_json(work_dir / "decisions.example.json",
                {"status": result["status"], "ranking": result["ranking"],
                 "uncertainty": result["uncertainty"],
                 "limitations": [], "pending_human": [],
                 "_note": "example decisions template; copy to decisions.json and edit"})
    _write_md(output_dir / "coverage_report.md", _coverage_report(result))

    return {"status": result["status"], "result": str(output_dir / "result.json"),
            "records": len(records), "merged": len(merged),
            "conflicts": len(all_conflicts), "degradations": len(degradations)}


def _coverage_report(result: Dict[str, Any]) -> str:
    lines = [
        "# 覆盖报告",
        "",
        "## 状态",
        "- status: %s" % result.get("status"),
        "- case_id: %s" % result.get("case_id"),
        "- 候选记录数: %d" % len(result.get("candidates", [])),
        "- 证据条数: %d" % len(result.get("evidence_records", [])),
        "",
        "## 覆盖",
    ]
    cov = result.get("coverage") or {}
    lines.append("- 数据库: %s" % ", ".join(cov.get("databases", []) or ["<none>"]))
    lines.append("- 输入记录总数: %s" % cov.get("total_input_records"))
    lines.append("- 合并后记录数: %s" % cov.get("merged_record_count"))
    lines.append("- 去重合并掉: %s" % cov.get("duplicates_merged"))
    lines.append("- 仅单库记录: %s" % cov.get("records_in_single_db_only"))
    per_db = cov.get("systematically_missing_fields") or {}
    for db, info in per_db.items():
        lines.append("- %s: 输入 %s 条, 系统性缺字段 %s" % (
            db, info.get("input_records"), ", ".join(info.get("fields_missing", []) or ["<none>"])))
    lines += ["", "## 降级", "- " + ("; ".join(result.get("degradations", [])) or "<none>"),
              "", "## 局限", "- " + ("; ".join(result.get("limitations", [])) or "<none>"),
              "", "## 待人工", "- " + ("; ".join(result.get("pending_human", [])) or "<none>"),
              "", "## 变更记录"]
    for c in result.get("change_log", []):
        lines.append("- [%s] %s: %s" % (c.get("at"), c.get("step"), c.get("detail")))
    return "\n".join(lines) + "\n"


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_md(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(
        description="多数据库联合检索流水线（先落盘骨架，再做增量改进）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--input", default="/input", help="输入目录")
    ap.add_argument("--output", default="/app/output", help="输出目录")
    ap.add_argument("--work", default="/app/work", help="工作目录")
    ap.add_argument("--decisions", default=None, help="模型/人工仲裁 decisions.json")
    ap.add_argument("--skeleton-only", action="store_true", help="只写骨架，不解析材料")
    args = ap.parse_args(argv)

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    decisions = Path(args.decisions) if args.decisions else None

    summary = run(input_dir, output_dir, work_dir, decisions, args.skeleton_only)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
