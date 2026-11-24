#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- 多数据库联合检索流水线入口（仅标准库，确定性，无网络）。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --skeleton-only --input /input --output /app/output
  python3 -B scripts/main.py --decisions /app/work/decisions.json \
      --input /input --output /app/output --work /app/work

流程（总预算 900 秒 / 64 次模型请求）：
  1. 读 /input/case.json 与 /input/task.md，立即写 result.json 骨架（status=partial）
  2. 发现 /input 下全部材料，按结构识别并解析数据库响应与论文
  3. 字段归一 -> 身份去重 -> 合并 -> 覆盖比较 -> 证据记录 -> 相关性提示
  4. 写 result.json 及 merged_records.json / evidence_map.json / coverage_report.md
  5. 若提供 --decisions，合并模型/人工仲裁，再落盘
  6. 依据实际缺口给出诚实 status，绝不编造。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fedsearch as fs


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _empty_for(kind: str) -> Any:
    """骨架里按语义给缺省值：dict 类给 {}，列表类给 []，其余 null。"""
    if kind in ("list", "array"):
        return []
    if kind in ("object", "map", "dict"):
        return {}
    return None


def resolve_contract(case: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """从 case.json 解析 output_contract 字段名。

    支持三种写法：
      - output_contract = ["a","b",...]
      - output_contract = {"fields": ["a","b"], ...}
      - output_contract = {"a": "type", "b": "type", ...}
    返回 (字段名列表, 类型映射)。
    """
    oc = case.get("output_contract")
    types: Dict[str, str] = {}
    names: List[str] = []
    if isinstance(oc, list):
        names = [str(x) for x in oc if str(x)]
    elif isinstance(oc, dict):
        if isinstance(oc.get("fields"), list):
            names = [str(x) for x in oc["fields"] if str(x)]
        elif isinstance(oc.get("fields"), dict):
            fields = oc["fields"]
            names = [str(k) for k in fields]
            for k, v in fields.items():
                if isinstance(v, str):
                    types[str(k)] = v.lower()
        else:
            names = [str(k) for k in oc]
            for k, v in oc.items():
                if isinstance(v, str):
                    types[str(k)] = v.lower()
    return names, types


def read_case(input_dir: Path) -> Tuple[Dict[str, Any], List[str]]:
    """读 /input/case.json；缺失/损坏时返回 最小字典 + errors。"""
    errors: List[str] = []
    case_path = input_dir / "case.json"
    if not case_path.exists():
        return {}, ["missing: %s" % case_path.as_posix()]
    obj, errs = fs.load_json(case_path)
    errors.extend(errs)
    if obj is None:
        return {}, errors
    if not isinstance(obj, dict):
        return {"_raw": obj}, ["case_json_not_object"]
    return obj, errors


def read_task(input_dir: Path) -> Tuple[str, List[str]]:
    task_path = input_dir / "task.md"
    if not task_path.exists():
        return "", []
    text, degs = fs.read_text(task_path)
    return text, degs


def case_identity(case: Dict[str, Any]) -> str:
    for k in ("case_id", "caseId", "id", "challenge_id"):
        v = case.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v).strip()
    return "unknown_case"


def case_question(case: Dict[str, Any]) -> Optional[str]:
    for k in ("research_question", "question", "task", "title", "prompt"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def case_queries(case: Dict[str, Any]) -> List[str]:
    for k in ("queries", "keywords", "search_terms", "query", "terms"):
        v = case.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            return [fs._text_of(x) for x in v if fs._text_of(x)]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
    return []


def case_databases(case: Dict[str, Any]) -> List[str]:
    for k in ("databases", "sources", "dbs"):
        v = case.get(k)
        if isinstance(v, list):
            return [fs._text_of(x) for x in v if fs._text_of(x)]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
    return []


def build_skeleton(case_id: str, contract_names: List[str],
                   contract_types: Dict[str, str], started: str) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    res["case_id"] = case_id
    res["status"] = "partial"
    # 1) output_contract 全部字段原样回填
    for k in contract_names:
        res[k] = _empty_for(contract_types.get(k, "list"))
    # 2) 固定字段
    res.setdefault("research_question", None)
    res.setdefault("search_scope", None)
    res.setdefault("candidates", [])
    res.setdefault("ranking", [])
    res.setdefault("evidence_records", [])
    res.setdefault("coverage", None)
    res.setdefault("empty_results", [])
    res.setdefault("conflicts", [])
    res.setdefault("missing_metadata", [])
    res.setdefault("permission_issues", [])
    res.setdefault("limitations", [])
    res["sources"] = []
    res["uncertainty"] = {"items": [], "note": "initial skeleton"}
    res["change_log"] = [
        {"step": "skeleton", "status": "written", "at": started,
         "detail": "先落盘合法 partial 骨架，后续只做增量改进"}
    ]
    res["errors"] = []
    res["degradations"] = []
    res["pending_human"] = []
    res["runtime"] = {"started_at": started, "finished_at": None, "seconds": 0,
                      "pipeline_version": fs.VERSION, "python": sys.version.split()[0]}
    return res


def ensure_key(res: Dict[str, Any], key: str, kind: str = "list") -> None:
    if key not in res:
        res[key] = _empty_for(kind)


def apply_decisions(res: Dict[str, Any], decisions: Dict[str, Any],
                    at: str) -> List[str]:
    applied: List[str] = []
    if not isinstance(decisions, dict):
        return applied
    for key in ("status", "ranking", "screening", "uncertainty", "limitations",
                "pending_human", "empty_results", "conflicts",
                "permission_issues", "missing_metadata"):
        if key in decisions and decisions[key] is not None:
            if key == "status":
                if decisions[key] in fs.STATUSES:
                    res["status"] = decisions[key]
                    applied.append("status")
            elif key in ("ranking", "screening"):
                res[key] = decisions[key]
                applied.append(key)
            else:
                res[key] = decisions[key]
                applied.append(key)
    if applied:
        res.setdefault("change_log", []).append(
            {"step": "model_refine", "status": "applied", "at": at,
             "detail": "applied decisions: " + ", ".join(applied)})
    return applied


def parse_materials(input_dir: Path) -> Dict[str, Any]:
    """解析输入目录全部材料。"""
    sources: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    degradations: List[str] = []
    pdf_notes: List[Dict[str, Any]] = []
    other_texts: List[Dict[str, Any]] = []

    for p in fs.discover_input_files(input_dir):
        rel = p.relative_to(input_dir).as_posix()
        if p.stat().st_size > fs.MAX_FILE_BYTES:
            degradations.append("too_large:%s:%dB" % (rel, p.stat().st_size))
            continue
        kind = fs.classify_file(p)
        if kind == "json":
            obj, errs = fs.load_json(p)
            degradations.extend((rel + ": " + e) for e in errs)
            if obj is None:
                degradations.append("json_unparsed:" + rel)
                continue
            db = fs.classify_response(obj)
            recs = fs.extract_records(obj)
            norm_recs: List[Dict[str, Any]] = []
            for raw, loc in recs:
                rec = fs.normalize_record(raw, db)
                rec["_db"] = db
                rec["_source_file"] = rel
                rec["_locator"] = loc
                rec["_request"] = fs.extract_request(obj)
                rec["_raw"] = raw
                norm_recs.append(rec)
                records.append(rec)
            sources.append({
                "source_id": "file:" + rel,
                "type": "database_response",
                "db": db,
                "path": rel,
                "record_count": len(norm_recs),
                "record_locators": [r["_locator"] for r in norm_recs],
                "note": "" if norm_recs else "no bibliographic records detected",
            })
        elif kind == "pdf":
            text, degs = fs.parse_pdf_text(p)
            degradations.extend((rel + ": " + d) for d in degs)
            pdf_notes.append({"path": rel, "text_chars": len(text)})
            sources.append({
                "source_id": "file:" + rel, "type": "document", "path": rel,
                "text_chars": len(text),
                "note": "text extracted" if text else "no text (see degradations)",
            })
        else:
            text, degs = fs.read_text(p)
            degradations.extend((rel + ": " + d) for d in degs)
            other_texts.append({"path": rel, "text_chars": len(text)})
            sources.append({
                "source_id": "file:" + rel, "type": "text", "path": rel,
                "text_chars": len(text),
            })
    return {
        "sources": sources, "records": records, "degradations": degradations,
        "pdf_notes": pdf_notes, "other_texts": other_texts,
    }


def attach_field_sources(records: List[Dict[str, Any]]) -> None:
    for r in records:
        r["_field_sources"] = fs.field_sources_of(r, r.get("_db", "?"),
                                                  r.get("_locator", "?"))


def build_ranking(merged: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """确定性自动排序：相关性命中降序 -> 年份降序 -> 标题。"""
    decorated = []
    for i, rec in enumerate(merged):
        hint = rec.get("_relevance_hint") or {"score": 0}
        score = int(hint.get("score", 0))
        year = rec.get("year") or 0
        title = fs._text_of(rec.get("title")) or ""
        decorated.append((score, year, title, rec))
    decorated.sort(key=lambda x: (-x[0], -(x[1] or 0), x[2]))
    out = []
    for idx, (score, year, title, rec) in enumerate(decorated, start=1):
        out.append({
            "record_id": rec.get("record_id"),
            "rank": idx,
            "relevance": ("high" if score >= 3 else
                          "medium" if score >= 1 else "low"),
            "auto_score": score,
            "rationale": "auto: term_overlap score=%d matched=%s" % (
                score, ",".join((rec.get("_relevance_hint") or {}).get("matched_terms", []))),
        })
    return out


def final_status(res: Dict[str, Any], n_candidates: int,
                 input_errors: List[str], degradations: List[str]) -> str:
    if input_errors:
        return "input_error" if res.get("case_id") == "unknown_case" else "partial"
    if n_candidates == 0:
        return "insufficient_evidence" if not degradations else "partial"
    if degradations or res.get("limitations") or res.get("conflicts") or \
            res.get("missing_metadata") or res.get("permission_issues"):
        return "partial"
    return "ok"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


def build_coverage(records: List[Dict[str, Any]], merged: List[Dict[str, Any]],
                   cluster_map: Dict[int, int], dbs_present: List[str]) -> Dict[str, Any]:
    """逐库统计 fields_missing 与单库记录。"""
    db_records: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        db_records.setdefault(r.get("_db", "generic"), []).append(r)

    per_db: Dict[str, Any] = {}
    fields = ("title", "authors", "year", "journal", "volume", "issue", "pages",
              "doi", "pmid", "pmcid", "issn", "doc_type", "abstract", "publisher")
    single_db_ids: Dict[str, int] = {}
    for db, recs in sorted(db_records.items()):
        missing = []
        for f in fields:
            filled = [r for r in recs if r.get(f) not in (None, "", [])]
            if len(filled) < len(recs):
                missing.append(f)
        per_db[db] = {"record_count": len(recs), "fields_missing": missing}

    # 判断每个簇是否只在单库收录
    cluster_dbs: Dict[int, set] = {}
    for i, r in enumerate(records):
        cluster_dbs.setdefault(cluster_map.get(i, i), set()).add(r.get("_db", "generic"))
    for cid, dbs in cluster_dbs.items():
        if len(dbs) == 1:
            db = next(iter(dbs))
            single_db_ids[db] = single_db_ids.get(db, 0) + 1

    # 系统性缺失字段：跨库普遍为空
    sys_missing = []
    for f in fields:
        filled = [r for r in records if r.get(f) not in (None, "", [])]
        if len(filled) == 0:
            sys_missing.append(f)

    # 原始记录数 - 簇数 = 合并掉的重复记录数
    cluster_count = len(set(cluster_map.values())) if cluster_map else len(merged)
    dup_count = max(0, len(records) - cluster_count)

    return {
        "databases": dbs_present,
        "per_database": per_db,
        "total_input_records": len(records),
        "merged_record_count": len(merged),
        "duplicates_merged": dup_count,
        "records_in_single_db_only": single_db_ids,
        "systematically_missing_fields": sys_missing,
    }


def build_evidence(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ev = []
    seen = set()
    for r in records:
        loc = r.get("_locator", "?")
        key = (r.get("_source_file"), loc)
        if key in seen:
            continue
        seen.add(key)
        raw = r.get("_raw") or {}
        quote = json.dumps(raw, ensure_ascii=False)
        if len(quote) > 800:
            # 取逐字片段：优先标题 + 一个标识符
            title = fs._text_of(r.get("title")) or ""
            doi = r.get("doi") or ""
            quote = ("title=%r; doi=%r" % (title[:200], doi)) if title or doi \
                else quote[:800]
        ev.append({
            "evidence_id": "ev:file:%s:%s" % (r.get("_source_file", "?"), loc),
            "type": "record",
            "db": r.get("_db"),
            "source_file": r.get("_source_file"),
            "locator": loc,
            "quote": quote,
        })
    return ev


def finalize(res: Dict[str, Any], started: str, t0: float) -> None:
    finish = fs.iso_now()
    res["runtime"]["finished_at"] = finish
    res["runtime"]["seconds"] = int(round(time.time() - t0))
    res["change_log"].append({
        "step": "finalize", "status": res["status"], "at": finish,
        "detail": "pipeline finished; candidates=%d" % len(res.get("candidates", [])),
    })


def run(input_dir: Path, output_dir: Path, work_dir: Path,
        decisions: Optional[Dict[str, Any]] = None,
        skeleton_only: bool = False) -> Dict[str, Any]:
    t0 = time.time()
    started = fs.iso_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case, input_errors = read_case(input_dir)
    task_text, task_degs = read_task(input_dir)
    input_errors.extend(task_degs)

    case_id = case_identity(case)
    contract_names, contract_types = resolve_contract(case)
    if not contract_names:
        contract_names = list(fs.DEFAULT_CONTRACT)

    res = build_skeleton(case_id, contract_names, contract_types, started)
    res["errors"] = input_errors
    res["research_question"] = case_question(case)
    queries = case_queries(case)
    dbs_expected = case_databases(case)
    res["search_scope"] = {
        "databases": dbs_expected,
        "queries": queries,
        "derived_query_terms": fs.tokenize(case_question(case)),
    }

    # 先落盘骨架（含缺失 case.json 的降级）
    write_json(output_dir / "result.json", res)

    if skeleton_only:
        res["change_log"].append({"step": "skeleton_only", "status": "partial",
                                   "at": fs.iso_now(),
                                   "detail": "仅写骨架，按请求退出"})
        finalize(res, started, t0)
        write_json(output_dir / "result.json", res)
        return res

    mat = parse_materials(input_dir)
    records: List[Dict[str, Any]] = mat["records"]
    sources: List[Dict[str, Any]] = mat["sources"]
    degradations: List[str] = mat["degradations"]

    # 术语：研究问题 + 显式查询 + task 文本里的题面词
    terms = list(dict.fromkeys(
        fs.tokenize(case_question(case)) + [t for q in queries for t in fs.tokenize(q)]
    ))

    attach_field_sources(records)
    for r in records:
        r["_relevance_hint"] = fs.relevance_hint(r, terms)

    # 稳定的记录内排序（去重对输入顺序敏感前先按稳定键排序）
    records.sort(key=lambda r: (fs.record_key(r), r.get("_source_file", ""),
                                 r.get("_locator", "")))

    clusters, reasons = fs.dedup_records(records)

    merged: List[Dict[str, Any]] = []
    cluster_map: Dict[int, int] = {}
    all_conflicts: List[Dict[str, Any]] = []
    for cid, idxs in enumerate(clusters):
        recs = [records[i] for i in idxs]
        m, cfs = fs.merge_cluster(recs)
        m["record_id"] = "rec:%06d" % (cid + 1)
        # 命中取簇内最大值
        best_hint = max(recs, key=lambda r: (r.get("_relevance_hint") or {}).get("score", 0))
        m["_relevance_hint"] = best_hint.get("_relevance_hint")
        m["relevance_hint"] = m["_relevance_hint"]
        m["field_sources"] = m.get("_merged_field_sources", {})
        m["provenance"] = m.get("_provenance_members", [])
        m["_dedup_reason"] = reasons[cid] if cid < len(reasons) else "new_record"
        m["raw_snapshot"] = json.dumps(
            [r.get("_raw") for r in recs], ensure_ascii=False)[:4000]
        merged.append(m)
        for i in idxs:
            cluster_map[i] = cid
        all_conflicts.extend(cfs)

    # 清理内部键
    public_fields = ("record_id", "title", "authors", "year", "journal", "volume",
                     "issue", "pages", "doi", "pmid", "pmcid", "issn", "doc_type",
                     "abstract", "publisher", "identifiers", "field_sources",
                     "provenance", "relevance_hint", "raw_snapshot")
    candidates: List[Dict[str, Any]] = []
    for m in merged:
        c = {}
        for k in public_fields:
            v = m.get(k)
            if k == "authors":
                c[k] = v if isinstance(v, list) else []
            elif k == "provenance":
                c[k] = v if isinstance(v, list) else []
            elif k in ("identifiers", "field_sources"):
                c[k] = v if isinstance(v, dict) else {}
            else:
                c[k] = v
        candidates.append(c)

    dbs_present = sorted({r.get("_db", "generic") for r in records})
    coverage = build_coverage(records, merged, cluster_map, dbs_present)
    evidence_records = build_evidence(records)
    ranking = build_ranking(merged)

    res["sources"] = sources
    res["candidates"] = candidates
    res["ranking"] = ranking
    res["evidence_records"] = evidence_records
    res["coverage"] = coverage
    res["conflicts"] = all_conflicts
    res["degradations"] = degradations
    res["search_scope"] = {
        "databases": dbs_expected or dbs_present,
        "queries": queries,
        "derived_query_terms": terms,
    }

    # 缺失元数据：编目后字段缺失记录
    missing = []
    for c in candidates:
        absent = [f for f in ("doi", "pmcid", "issn", "pages", "abstract")
                  if c.get(f) in (None, "", [])]
        if absent:
            missing.append({"record_id": c.get("record_id"),
                            "title": c.get("title"), "missing_fields": absent})
    res["missing_metadata"] = missing

    # 空结果说明
    if not records:
        res["empty_results"] = [{
            "scope": "all_inputs", "reason": "no bibliographic records parsed",
            "note": "检查 /input 下是否存在数据库响应（Crossref/EuropePMC/PubMed/出版社 JSON）"}]

    # 未覆盖数据库说明
    got = set(dbs_present)
    want = set(dbs_expected) if dbs_expected else set()
    for db in sorted(want - got):
        res["empty_results"].append({"scope": "database", "db": db,
                                     "reason": "no response parsed for expected database"})
        res["limitations"].append("expected database not covered in inputs: %s" % db)

    if res["conflicts"] or res["missing_metadata"] or degradations or \
            (want - got):
        res["status"] = "partial"
    elif input_errors:
        res["status"] = "input_error" if not records and not case else "partial"
    elif not candidates:
        res["status"] = "insufficient_evidence"
    else:
        res["status"] = "ok"

    res["uncertainty"] = {
        "items": [
            {"item": "auto relevance is term-overlap only; human/model ranking"
                     " may override via decisions.json",
             "level": "medium"}],
        "note": "字段取值与来源逐条可回溯；未提供的元数据不臆造",
    }

    write_json(output_dir / "result.json", res)
    write_json(output_dir / "merged_records.json", {"case_id": case_id,
                                                    "records": candidates,
                                                    "clusters": clusters})
    write_json(output_dir / "evidence_map.json",
               {"case_id": case_id, "evidence_records": evidence_records,
                "conflicts": all_conflicts, "missing_metadata": missing})
    cov_md = render_coverage_report(case_id, coverage, sources, degradations,
                                    res["limitations"], candidates, ranking)
    (output_dir / "coverage_report.md").write_text(cov_md, encoding="utf-8")

    if decisions is not None:
        apply_decisions(res, decisions, fs.iso_now())
        write_json(output_dir / "result.json", res)

    finalize(res, started, t0)
    write_json(output_dir / "result.json", res)

    # 把 decisions 的骨架请求写入 work，供模型下次读
    if decisions is None:
        write_json(work_dir / "state.json",
                   {"case_id": case_id, "candidate_count": len(candidates),
                    "merged_count": len(merged), "status": res["status"],
                    "next": "可选：先读 coverage_report.md 复核，再写 decisions.json"})
    return res


def render_coverage_report(case_id: str, coverage: Dict[str, Any],
                           sources: List[Dict[str, Any]],
                           degradations: List[str],
                           limitations: List[str],
                           candidates: List[Dict[str, Any]],
                           ranking: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# 覆盖报告（coverage report）")
    lines.append("")
    lines.append("- case_id: `%s`" % case_id)
    lines.append("- 输入记录总数: %s" % coverage.get("total_input_records", 0))
    lines.append("- 合并后记录数: %s" % coverage.get("merged_record_count", 0))
    lines.append("- 合并掉的重复数: %s" % coverage.get("duplicates_merged", 0))
    lines.append("")
    lines.append("## 逐库覆盖")
    lines.append("")
    per_db = coverage.get("per_database", {})
    if per_db:
        for db, info in per_db.items():
            lines.append("- **%s**: %d 条记录；缺字段: %s" % (
                db, info.get("record_count", 0),
                ", ".join(info.get("fields_missing", [])) or "无"))
    else:
        lines.append("（无数据库响应）")
    lines.append("")
    lines.append("## 仅单库收录")
    single = coverage.get("records_in_single_db_only", {})
    lines.append("")
    if single:
        for db, n in sorted(single.items()):
            lines.append("- %s: %d 簇仅此库收录" % (db, n))
    lines.append("")
    lines.append("## 系统性缺失字段")
    sysmiss = coverage.get("systematically_missing_fields", [])
    lines.append("")
    lines.append(", ".join(sysmiss) if sysmiss else "（无）")
    lines.append("")
    lines.append("## 降级与局限")
    lines.append("")
    if degradations:
        for d in degradations:
            lines.append("- %s" % d)
    else:
        lines.append("（无降级）")
    for l in limitations:
        lines.append("- %s" % l)
    lines.append("")
    lines.append("## 候选与排序摘要")
    lines.append("")
    for r in ranking:
        lines.append("- [rank %s] %s (relevance=%s)" % (
            r.get("rank"), r.get("record_id"), r.get("relevance")))
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="多数据库联合检索流水线（仅标准库，确定性，无网络）")
    ap.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    ap.add_argument("--output", default="/app/output", help="输出目录（默认 /app/output）")
    ap.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    ap.add_argument("--decisions", default=None, help="decisions.json 路径（可选）")
    ap.add_argument("--skeleton-only", action="store_true",
                    help="只写骨架后退出")
    args = ap.parse_args(argv)

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)

    decisions = None
    if args.decisions:
        dp = Path(args.decisions)
        if dp.exists():
            obj, errs = fs.load_json(dp)
            if obj is not None and isinstance(obj, dict):
                decisions = obj
            else:
                decisions = {}
        else:
            decisions = {}

    run(input_dir, output_dir, work_dir, decisions=decisions,
        skeleton_only=args.skeleton_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())