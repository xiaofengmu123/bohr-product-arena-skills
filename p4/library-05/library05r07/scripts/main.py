#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 执行入口。

五命令主路径（评测 Agent 应照 SKILL.md 顺序执行）：
    python scripts/main.py init       # 扫描盘点 + partial result.json + 工作稿模板
    python scripts/main.py show       # 打印带 locator 的盘点（供模型阅读）
    # 模型阅读 show 输出后，把语义判断写进 /app/work/draft.json
    python scripts/main.py check      # 机械校验 draft（结构 + 溯源命中）
    python scripts/main.py build      # 合并确定性结果与 draft，无条件收口交付
    python scripts/main.py selfcheck  # 复检 /app/output 产物
所有子命令支持 --input/--work/--output 覆盖默认目录；默认路径也能直接跑。
退出码恒为 0：真实问题写进 errors/degradations，不靠退出码表达。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import libcore  # noqa: E402

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

DRAFT_TEMPLATE = {
    "case_id": "",
    "notes": [
        {
            "target": "F001",
            "kind": "alias",  # alias|version|metadata|correction|query_answer
            "text": "在此写语义判断（如行政版本备注、别名裁定、检索答案）。",
            "basis": "依据材料中哪句话",
            "confidence": "explicit",  # explicit|inferred
            "evidence": [
                {"source_id": "F001", "locator": "materials/a.txt", "quote": "逐字片段"}
            ],
        }
    ],
    "queries": [
        {
            "query_id": "Q1",
            "answer": "在此写检索答案",
            "status": "answered",  # answered|no_direct_match|insufficient_evidence
            "evidence": [
                {"source_id": "F001", "locator": "materials/a.txt", "quote": "逐字片段"}
            ],
        }
    ],
    "contract": {},
    "recoveries": [],
}


# ---------------------------------------------------------------- 目录解析


def resolve_dirs(args) -> tuple:
    input_dir = Path(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    work_dir = Path(args.work) if getattr(args, "work", None) else DEFAULT_WORK
    output_dir = Path(args.output) if getattr(args, "output", None) else DEFAULT_OUTPUT
    for d in (work_dir, output_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return input_dir, work_dir, output_dir


def ensure_output(output_dir: Path, work_dir: Path):
    for cand in (output_dir, work_dir / "output_fallback", SKILL_ROOT / "skill_output"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            if cand != output_dir:
                return cand, f"output_fallback:{cand.name}"
            return cand, None
        except OSError:
            continue
    return output_dir, "output_not_writable"


# ---------------------------------------------------------------- case 解析


def load_case(input_dir: Path):
    case_path = input_dir / "case.json"
    if not case_path.exists():
        return None, "", "case.json missing"
    try:
        raw = case_path.read_bytes()
    except OSError as e:
        return None, "", f"case.json read failed: {e}"
    text, _enc, err = libcore.decode_bytes(raw)
    if err:
        return None, "", f"case.json decode failed: {err}"
    try:
        case = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return None, text, f"case.json invalid JSON: {e}"
    return case, text, None


def load_task(input_dir: Path):
    for name in ("task.md", "task.txt", "task.json"):
        p = input_dir / name
        if p.exists():
            try:
                raw = p.read_bytes()
                text, _enc, err = libcore.decode_bytes(raw)
                return text, err
            except OSError as e:
                return "", f"task read failed: {e}"
    return "", "task.md missing"


def derive_case_id(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id", "case"):
            v = case.get(key)
            if v not in (None, ""):
                return str(v)
    return "unknown"


def extract_contract(case):
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str):
                        keys.append(item[cand])
                        break
    return keys


def _as_query_list(raw):
    out = []
    if isinstance(raw, str):
        out = [raw]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(item.get("text") or item.get("question") or item.get("query") or "")
    elif isinstance(raw, dict):
        for item in raw.values():
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(item.get("text") or item.get("question") or item.get("query") or "")
    return out


def extract_queries(case):
    queries = []
    if not isinstance(case, dict):
        return queries
    for key in ("queries", "questions", "searches", "retrieval_questions", "检索", "检索问题"):
        for i, t in enumerate(_as_query_list(case.get(key))):
            if t and t.strip() and t.strip() not in (q[1] for q in queries):
                queries.append((f"Q{len(queries) + 1}", t.strip()))
    return [{"query_id": qid, "text": text} for qid, text in queries]


def _normalize_relations(raw):
    out = []
    if not raw:
        return out
    items = raw if isinstance(raw, list) else [raw]
    for item in items:
        if isinstance(item, str):
            out.append({"kind": "unknown", "text": item, "member_ids": _find_ids(item)})
        elif isinstance(item, dict):
            rec = dict(item)
            rec.setdefault("kind", item.get("type") or item.get("relation") or "unknown")
            rec.setdefault("member_ids", (item.get("member_ids") or item.get("files")
                                          or _find_ids(item.get("text") or "")))
            out.append(rec)
    return out


def extract_relations(case):
    rels = []
    if not isinstance(case, dict):
        return rels
    for key in ("relations", "aliases", "versions", "version_notes",
                "admin_notes", "administrative_notes", "别名", "版本备注"):
        rels.extend(_normalize_relations(case.get(key)))
    return rels


def extract_manifest(case):
    if not isinstance(case, dict):
        return []
    for key in ("manifest", "expected_files", "required_files", "file_list", "inventory"):
        raw = case.get(key)
        if isinstance(raw, list):
            return [str(x) for x in raw if isinstance(x, str)]
        if isinstance(raw, dict):
            return [str(x) for x in raw.values() if isinstance(x, str)]
        if isinstance(raw, str):
            return [raw]
    return []


def extract_operations(case):
    if not isinstance(case, dict):
        return []
    for key in ("operations", "recovery", "recovery_ops", "index_operations", "tasks"):
        raw = case.get(key)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            return list(raw.values())
        if isinstance(raw, str):
            return [raw]
    return []


def _find_ids(text):
    if not text:
        return []
    return sorted(set(re.findall(r"F\d{3}", text)))


# ---------------------------------------------------------------- init


def cmd_init(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    t0 = time.time()
    errors = []
    degradations = []

    case, case_text, case_err = load_case(input_dir)
    task_text, task_err = load_task(input_dir)
    if task_err:
        degradations.append({"issue": task_err})

    case_id = derive_case_id(case) if case else "unknown"
    contract_keys = extract_contract(case) if case else []
    queries = extract_queries(case) if case else []
    relations = extract_relations(case) if case else []
    manifest = extract_manifest(case) if case else []
    operations = extract_operations(case) if case else []
    if case_err:
        errors.append(case_err)

    scan = libcore.scan_directory(input_dir, errors, degradations)
    files = scan["files"]
    directories = scan["directories"]

    analysis = libcore.analyze_relations(files, relations)

    # manifest 交叉核对：缺失/被引文件
    discovered = {f["rel_path"] for f in files}
    manifest_missing = [m for m in manifest if m not in discovered]

    # 初始（确定性）change_log + pending_human
    change_log = [{
        "step": 1, "action": "scan",
        "before": None,
        "after": {"files": len(files), "directories": len(directories)},
        "reason": "init 盘点",
        "locator": str(input_dir),
    }]
    pending_human = []
    for m in manifest_missing:
        pending_human.append({"item": m, "reason": "manifest 引用但未在输入中发现"})
        degradations.append({"issue": f"missing_referenced_file:{m}"})

    # 建立检索索引（用文件 + 关系/metadata 文本）
    metadata_texts = [json.dumps(r, ensure_ascii=False) for r in relations]
    metadata_texts += [json.dumps(op) if not isinstance(op, str) else op for op in operations]
    index = libcore.build_index(files, metadata_texts)

    actual_out, fallback_note = ensure_output(output_dir, work_dir)
    if fallback_note:
        degradations.append({"issue": fallback_note})

    library = {
        "case_id": case_id,
        "input_dir": str(input_dir),
        "files": files,
        "directories": directories,
        "analysis": analysis,
        "explicit_relations": relations,
        "query_defs": queries,
        "query_answers": libcore.answer_queries(queries, files, index),
        "index": {"term_count": len(index.get("terms", {})),
                  "sample_terms": list(index.get("terms", {}))[:100]},
        "manifest": manifest,
        "manifest_missing": manifest_missing,
        "operations": operations,
        "change_log": change_log,
        "pending_human": pending_human,
        "errors": errors,
        "degradations": degradations,
    }

    summary = _summary(files, directories)
    # 先落盘 partial 结果骨架（满足前 3 分钟要求）
    skeleton = libcore.build_skeleton(case_id, contract_keys, "partial")
    skeleton["result"] = {"inventory_summary": summary}
    skeleton["errors"] = errors
    skeleton["degradations"] = degradations
    skeleton["runtime"] = {"seconds": round(time.time() - t0, 3),
                           "command": "init", "skill_version": libcore.SKILL_VERSION}

    _atomic_json_dump(library, work_dir / "library.json")
    _atomic_json_dump(skeleton, actual_out / "result.json")

    # 工作稿模板
    template = dict(DRAFT_TEMPLATE)
    template["case_id"] = case_id
    template["notes"] = []
    template["queries"] = [
        {"query_id": q["query_id"], "answer": "", "status": "no_direct_match", "evidence": []}
        for q in queries
    ]
    _atomic_json_dump(template, work_dir / "draft.json")

    print(json.dumps({
        "command": "init",
        "case_id": case_id,
        "case_error": case_err,
        "contract_keys": contract_keys,
        "files": len(files),
        "directories": len(directories),
        "duplicates": len(analysis["duplicate_groups"]),
        "versions": len(analysis["version_groups"]),
        "conflicts": len(analysis["conflict_groups"]),
        "aliases": len(analysis["alias_groups"]),
        "queries": len(queries),
        "manifest_missing": manifest_missing,
        "output": str(actual_out),
        "degradations": degradations,
    }, ensure_ascii=False, indent=2))
    return 0


def _summary(files, directories):
    total = sum(f.get("size") or 0 for f in files)
    unreadable = sum(1 for f in files if f.get("status") == "unreadable")
    empty = sum(1 for f in files if f.get("status") == "empty")
    return {
        "files": len(files),
        "directories": len(directories),
        "total_bytes": total,
        "readable": len(files) - unreadable,
        "unreadable": unreadable,
        "empty": empty,
    }


def _atomic_json_dump(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _atomic_text_dump(text: str, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- show


def cmd_show(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    lib_path = work_dir / "library.json"
    if not lib_path.exists():
        print("library.json 不存在，请先运行 init。")
        return 0
    library = _load_json(lib_path)
    if library is None:
        print("library.json 无法解析。")
        return 0

    files = library.get("files", [])
    groups = library.get("analysis", {})
    print(f"[case_id] {library.get('case_id')}")
    print(f"[input] {library.get('input_dir')}")
    print(f"[summary] files={len(files)} dirs={len(library.get('directories', []))} "
          f"dup={len(groups.get('duplicate_groups', []))} ver={len(groups.get('version_groups', []))} "
          f"conflict={len(groups.get('conflict_groups', []))} alias={len(groups.get('alias_groups', []))}\n")

    print("### 文件（逐条带 locator）")
    for f in files:
        preview = (f.get("text_preview") or "").replace("\n", "⏎")[:400]
        print(f"{f['id']}\t{f['rel_path']}\tsize={f.get('size')}\t{f.get('file_type')}"
              f"\tsha={ (f.get('sha256') or '')[:12] }\t{f.get('status')}")
        if preview:
            print(f"    preview: {preview}")

    print("\n### 关系分组")
    for g in groups.get("duplicate_groups", []):
        print(f"dup\t{g['member_ids']}\tbasis=identical_bytes")
    for g in groups.get("version_groups", []):
        print(f"version\t{g['member_ids']}\tbasis={g.get('basis')}")
    for g in groups.get("conflict_groups", []):
        print(f"conflict\t{g['member_ids']}\tbasis={g.get('basis')}  ← 需人工裁定")
    for g in groups.get("alias_groups", []):
        print(f"alias\t{g['member_ids']}\tbasis=explicit")

    print("\n### 显式关系（行政版本备注）")
    for r in library.get("explicit_relations", []):
        print(json.dumps(r, ensure_ascii=False)[:300])

    print("\n### 检索问题（需在 draft.json 补充答案）")
    for q in library.get("query_defs", []):
        print(f"{q['query_id']}\t{q['text']}")

    print("\n### 待人工 / 缺失")
    for p in library.get("pending_human", []):
        print(f"pending_human\t{p.get('item')}\t{p.get('reason')}")
    return 0


def _load_json(path: Path):
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    text, _enc, err = libcore.decode_bytes(raw)
    if err:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- check


def cmd_check(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    lib_path = work_dir / "library.json"
    draft_path = work_dir / "draft.json"
    report = {"present": False, "draft": None, "issues": [], "summary": ""}

    library = _load_json(lib_path)
    if library is None:
        report["issues"].append({"issue": "library.json missing or unparsable"})
        _atomic_json_dump(report, work_dir / "check_report.json")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    draft = _load_json(draft_path)
    if draft is None:
        report["issues"].append({"issue": "draft.json missing or unparsable"})
        _atomic_json_dump(report, work_dir / "check_report.json")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    case, case_text, _case_err = load_case(input_dir)
    task_text, _task_err = load_task(input_dir)

    verified, issues = libcore.verify_draft(
        library, draft, case_text or "", task_text or "")
    _atomic_json_dump(verified, work_dir / "draft.verified.json")

    structure_issues = _check_structure(draft)
    issues = structure_issues + issues
    report["present"] = True
    report["issues"] = issues
    report["summary"] = (f"{len(draft.get('notes', []))} notes, "
                         f"{len(draft.get('queries', []))} queries, "
                         f"{len(issues)} issues")
    _atomic_json_dump(report, work_dir / "check_report.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _check_structure(draft):
    issues = []
    if not isinstance(draft, dict):
        return [{"issue": "draft 不是对象"}]
    for n in draft.get("notes", []):
        if not isinstance(n.get("target"), str):
            issues.append({"context": "note", "issue": "缺少 target"})
        if not isinstance(n.get("evidence"), list):
            issues.append({"context": f'note:{n.get("target")}', "issue": "缺少 evidence 列表"})
    for q in draft.get("queries", []):
        if not isinstance(q.get("query_id"), str):
            issues.append({"context": "query", "issue": "缺少 query_id"})
        if isinstance(q.get("evidence"), list) and not all(
                isinstance(e, dict) for e in q["evidence"]):
            issues.append({"context": f'query:{q.get("query_id")}', "issue": "evidence 元素不是对象"})
    return issues


# ---------------------------------------------------------------- build


def cmd_build(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    t0 = time.time()
    degradations = []

    library = _load_json(work_dir / "library.json")
    draft = _load_json(work_dir / "draft.json")
    if library is None:
        actual_out, note = ensure_output(output_dir, work_dir)
        errors = ["library.json missing — build 无确定性盘点可合；请先运行 init"]
        result = libcore.build_skeleton("unknown", [], "input_error")
        result["errors"] = errors
        result["degradations"] = [{"issue": "library.json missing"}]
        result["runtime"] = {"seconds": round(time.time() - t0, 3), "command": "build"}
        _atomic_json_dump(result, actual_out / "result.json")
        print(json.dumps({"command": "build", "status": "input_error", "note": "library.json missing"},
                         ensure_ascii=False))
        return 0

    if draft is None or not _has_semantic_draft(draft):
        degradations.append({"issue": "no_draft",
                             "detail": "未读材料/未写 draft，仅交付确定性盘点骨架"})

    case, case_text, case_err = load_case(input_dir)
    if case_err:
        degradations.append({"issue": f"case_reload_failed:{case_err}"})
    task_text, _task_err = load_task(input_dir)

    contract_keys = extract_contract(case) if isinstance(case, dict) else []
    # 若 draft 缺失 contract 键，补齐
    if draft is not None and "contract" not in draft:
        draft["contract"] = {}

    # 合并前机械验证 draft 证据：quote 逐字命中 + source_id 存在；未命中降级为 partial。
    draft_effective = draft
    if draft is not None:
        verified, verify_issues = libcore.verify_draft(
            library, draft, case_text or "", task_text or "")
        draft_effective = verified
        n_bad = sum(1 for i in verify_issues
                    if i.get("issue") in ("quote_miss", "source_id_not_found"))
        if n_bad:
            degradations.append({"issue": "unverified_evidence",
                                 "detail": f"{n_bad} 条证据未命中"})

    # 把 draft.queries 的答案并入机械检索结果，回写 library 使契约 queries 键一致。
    merged_answers = _merge_query_answers(library.get("query_answers", []), draft_effective)
    library["query_answers"] = merged_answers

    summary = _summary(library.get("files", []), library.get("directories", []))
    index = libcore.build_index(library.get("files", []), [])

    runtime = {"seconds": round(time.time() - t0, 3), "command": "build",
               "skill_version": libcore.SKILL_VERSION}
    all_errors = list(library.get("errors", [])) + ([] if not case_err else [case_err])
    all_degradations = list(library.get("degradations", [])) + degradations
    result = libcore.finalize_result(
        library.get("case_id", "unknown"), contract_keys, library, draft_effective,
        summary, index, runtime, all_errors, all_degradations, case_err)

    actual_out, fallback_note = ensure_output(output_dir, work_dir)
    if fallback_note:
        result["degradations"].append({"issue": fallback_note})

    _atomic_json_dump(result, actual_out / "result.json")
    _atomic_json_dump(library, actual_out / "library_index.json")
    _atomic_json_dump(_build_evidence_map(result), actual_out / "evidence_map.json")
    _atomic_text_dump(_build_inventory_md(result, library), actual_out / "inventory.md")

    print(json.dumps({"command": "build", "case_id": result["case_id"], "status": result["status"],
                      "claims": len(result["claims"]), "sources": len(result["sources"]),
                      "pending_human": len(result["pending_human"]),
                      "errors": len(result["errors"]), "degradations": len(result["degradations"]),
                      "output": str(actual_out), }, ensure_ascii=False, indent=2))
    return 0


def _has_semantic_draft(draft):
    if not isinstance(draft, dict):
        return False
    if draft.get("notes"):
        return True
    if any(q.get("answer") for q in draft.get("queries", [])):
        return True
    if draft.get("contract"):
        return True
    return False


def _merge_query_answers(mechanical, draft):
    by_id = {q.get("query_id"): q for q in mechanical}
    for q in (draft or {}).get("queries", []):
        qid = q.get("query_id")
        if not qid:
            continue
        if qid in by_id:
            by_id[qid]["answer"] = q.get("answer", "")
            by_id[qid]["status"] = q.get("status", by_id[qid]["status"])
            by_id[qid]["evidence"] = q.get("evidence", [])
        else:
            by_id[qid] = {"query_id": qid, "query": "", "answer": q.get("answer", ""),
                          "status": q.get("status", "no_direct_match"), "hits": [],
                          "evidence": q.get("evidence", [])}
    return [by_id[k] for k in sorted(by_id, key=libcore.natural_key)]


def _build_evidence_map(result):
    maps = []
    for c in result.get("claims", []):
        maps.append({"claim_id": c.get("claim_id"), "evidence": c.get("evidence", [])})
    return {"case_id": result.get("case_id"), "maps": maps,
            "sources_index": result.get("sources", [])}


def _build_inventory_md(result, library):
    lines = []
    lines.append(f"# 文档库交接清单 — {result.get('case_id')}")
    lines.append("")
    lines.append(f"- 状态：`{result.get('status')}`")
    lines.append("- 本清单由确定性引擎生成，除 `runtime` 外不含时间戳，同一输入可复跑核对。")
    lines.append("")
    s = result.get("result", {}).get("inventory_summary", {})
    lines.append("## 盘点摘要")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for k in ("files", "directories", "total_bytes", "readable", "unreadable", "empty"):
        lines.append(f"| {k} | {s.get(k, 0)} |")
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| ID | 路径 | 类型 | 大小 | sha256(前12) | 状态 |")
    lines.append("|---|---|---|---|---|---|")
    for f in library.get("files", []):
        lines.append(f"| {f['id']} | {f['rel_path']} | {f.get('file_type')} | "
                     f"{f.get('size')} | {(f.get('sha256') or '')[:12]} | {f.get('status')} |")
    lines.append("")
    lines.append("## 重复 / 版本 / 别名 / 冲突")
    lines.append("")
    for label, key in (("重复（字节级）", "duplicate_groups"),
                       ("版本/改版", "version_groups"),
                       ("别名", "alias_groups"),
                       ("冲突（待人工）", "conflict_groups")):
        lines.append(f"### {label}")
        for g in result.get("result", {}).get(key, []):
            lines.append(f"- `{', '.join(g.get('member_ids', []))}`（{g.get('basis', '')}）")
        lines.append("")
    lines.append("## 检索问答")
    lines.append("")
    for q in result.get("result", {}).get("query_answers", []):
        lines.append(f"- **{q.get('query_id')}**（{q.get('status')}）: "
                     f"{q.get('answer') or '（未补充答案）'}")
    lines.append("")
    lines.append("## 失败与恢复")
    lines.append("")
    for e in result.get("errors", []):
        lines.append(f"- 错误：{e}")
    for d in result.get("degradations", []):
        if isinstance(d, dict):
            lines.append(f"- 降级：{d.get('issue')} {d.get('detail', '')}")
        else:
            lines.append(f"- 降级：{d}")
    for p in result.get("pending_human", []):
        lines.append(f"- 待人工：{p.get('item')}（{p.get('reason')}）")
    lines.append("")
    lines.append("## 复跑命令")
    lines.append("")
    lines.append("```bash")
    lines.append("python -B scripts/main.py init")
    lines.append("python -B scripts/main.py show")
    lines.append("python -B scripts/main.py check")
    lines.append("python -B scripts/main.py build")
    lines.append("python -B scripts/main.py selfcheck")
    lines.append("```")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- selfcheck


def cmd_selfcheck(args):
    input_dir, work_dir, output_dir = resolve_dirs(args)
    actual_out, _note = ensure_output(output_dir, work_dir)
    checks = []

    res = _load_json(actual_out / "result.json")
    checks.append({"check": "result.json 存在且可解析", "ok": res is not None})
    if res is not None:
        ok_envelope = all(k in res for k in ("case_id", "status", "sources",
                                             "uncertainty", "change_log"))
        checks.append({"check": "result.json 含 case_id/status/sources/uncertainty/change_log",
                       "ok": ok_envelope})
        checks.append({"check": "case_id 非空", "ok": bool(res.get("case_id"))})
        checks.append({"check": "status 合法",
                       "ok": res.get("status") in libcore.ALLOWED_STATUS})

    for name in ("library_index.json", "evidence_map.json", "inventory.md"):
        p = actual_out / name
        ok = p.exists() and p.stat().st_size > 0
        checks.append({"check": f"{name} 非空", "ok": ok})

    all_ok = all(c["ok"] for c in checks)
    report = {"ok": all_ok, "checks": checks, "output": str(actual_out)}
    _atomic_json_dump(report, work_dir / "selfcheck_report.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- 入口


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="科研文档库修复 Skill（鲁棒优先版）：init/show/check/build/selfcheck。")
    parser.add_argument("command", nargs="?",
                        help="子命令（init/show/check/build/selfcheck；缺省打印帮助）")
    parser.add_argument("--input", help="输入目录（默认 /input）")
    parser.add_argument("--work", help="工作目录（默认 /app/work）")
    parser.add_argument("--output", help="输出目录（默认 /app/output）")
    return parser


def _reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv=None):
    _reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cmd = args.command
        if cmd is None or cmd == "help":
            parser.print_help()
            return 0
        if cmd == "init":
            return cmd_init(args)
        if cmd == "show":
            return cmd_show(args)
        if cmd == "check":
            return cmd_check(args)
        if cmd == "build":
            return cmd_build(args)
        if cmd == "selfcheck":
            return cmd_selfcheck(args)
        parser.print_help()
        return 0
    except SystemExit:
        raise
    except Exception as e:  # 兜底：绝不因异常留下空产物
        try:
            _, work_dir, output_dir = resolve_dirs(args)
            actual_out, _n = ensure_output(output_dir, work_dir)
            err_res = {"status": "input_error",
                       "errors": [f"unexpected exception: {e}",
                                  traceback.format_exc(limit=3)],
                       "degradations": [], "case_id": "unknown", "result": {},
                       "sources": [], "uncertainty": {"summary": "crash", "count": 0, "items": []},
                       "change_log": [], "pending_human": [],
                       "runtime": {"skill_version": libcore.SKILL_VERSION}}
            _atomic_json_dump(err_res, actual_out / "result.json")
        except Exception:  # noqa: BLE001
            pass
        print(f"error: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())