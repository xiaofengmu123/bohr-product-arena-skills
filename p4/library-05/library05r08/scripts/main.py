#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 的命令行入口。

五命令主路径：init（盘点+partial 骨架）→ show（打印 locator 清单）→
Agent 写 draft.json → check（机械校验）→ build（收口）→ selfcheck（复检）。
均支持 --input/--work/--output；默认 /input、/app/work、/app/output。
退出码恒 0：真实问题写进 errors/degradations。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import core_impl as core  # noqa: E402

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


# --- 目录/输入

def resolve_dirs(args):
    input_dir = Path(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    work_dir = Path(args.work) if getattr(args, "work", None) else DEFAULT_WORK
    output_dir = Path(args.output) if getattr(args, "output", None) else DEFAULT_OUTPUT
    return input_dir, work_dir, output_dir


def ensure_dirs(work_dir, output_dir):
    degradations = []
    for d in (work_dir, output_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    # 输出目录可写探测
    actual = output_dir
    for cand in (output_dir, work_dir / "output_fallback",
                 SKILL_ROOT / "skill_output"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            actual = cand
            break
        except OSError:
            continue
    if actual != output_dir:
        degradations.append({"kind": "output_fallback",
                             "detail": f"输出目录不可写，降级到 {actual.name}"})
    return work_dir, actual, degradations


def load_case(input_dir):
    """读取 case.json（容错）。返回 (case, case_text, error)。"""
    cands = ["case.json", "case.JSON", "case.jsn", "input.json"]
    path = None
    for c in cands:
        p = input_dir / c
        if p.exists():
            path = p
            break
    if path is None:
        return None, "", {"kind": "missing_case_json",
                          "detail": "未找到 case.json；仍盘点 /input 下材料"}
    text = ""
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        case = json.loads(text)
        return case, text, None
    except Exception as exc:
        return None, text, {"kind": "bad_case_json",
                            "detail": f"{exc.__class__.__name__}: {exc}"}


def extract_contract(case):
    """抽取/归一化 output_contract，返回 [(key, default)]。dict/list 形态都支持。"""
    if not isinstance(case, dict):
        return []
    raw = case.get("output_contract")
    if raw is None:
        for alt in ("contract", "output_schema", "output_fields", "result_schema"):
            if alt in case:
                raw = case[alt]
                break
    out = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            default = None
            if isinstance(v, list) or v in ("list", "array"):
                default = []
            elif isinstance(v, dict) and v.get("type") in ("list", "array"):
                default = []
            out.append((str(k), default))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out.append((item, None))
            elif isinstance(item, dict):
                key = None
                for cand in ("name", "key", "field", "field_id", "id"):
                    if isinstance(item.get(cand), str):
                        key = item[cand]
                        break
                if key is None:
                    continue
                default = []
                if item.get("type") in ("list", "array") or item.get("multiple"):
                    default = []
                out.append((str(key), default))
    seen, uniq = set(), []
    for k, d in out:
        if k not in seen:
            seen.add(k)
            uniq.append((k, d))
    return uniq


def derive_case_id(case, input_dir):
    if isinstance(case, dict):
        for key in ("case_id", "id", "case", "challenge_id"):
            if case.get(key) not in (None, ""):
                return str(case[key])
    # task.md 兜底
    task = input_dir / "task.md"
    if task.exists():
        try:
            m = core.re.search(r"challenge_id:\s*`?([A-Za-z0-9\-_]+)", task.read_text(encoding="utf-8-sig", errors="ignore"))
            if m:
                return m.group(1)
        except OSError:
            pass
    return "unknown"


# --- 结果组装

def build_skeleton(case_id, contract, status):
    if not isinstance(contract, list):
        contract = list(contract)
    result = {}
    for key, default in contract:
        result[key] = default
    result["case_id"] = case_id
    result["status"] = status
    result["result"] = {}
    result["sources"] = []
    result["uncertainty"] = {"summary": "", "count": 0, "items": []}
    result["change_log"] = []
    result["pending_human"] = []
    result["errors"] = []
    result["degradations"] = []
    result["uncertainties"] = []
    result["claims"] = []
    result["runtime"] = {}
    return result


def _ev(files_by_id, *ids):
    out = []
    for i in ids:
        f = files_by_id.get(i)
        if f:
            out.append({"source_id": i, "locator": f["rel_path"],
                        "quote": f["rel_path"]})
    return out


def build_claims_base(library):
    """从确定性分析产生 claims（不含 draft 语义层）。"""
    claims, sources = [], []
    files = library.get("files", [])
    files_by_id = {f["id"]: f for f in files}
    analysis = library.get("analysis", {})
    n = 0
    for g in analysis.get("duplicate_groups", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "directly_read", "confidence": "high",
            "text": f"文件 {', '.join(g['member_ids'])} 的 sha256 相同（{g['sha256'][:12]}…），为同一文件的副本/别名",
            "evidence": _ev(files_by_id, *g["member_ids"]),
        })
    for g in analysis.get("version_groups", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "inferred", "confidence": "heuristic",
            "text": f"归一化 stem「{g['stem']}」下 {', '.join(g['member_ids'])} 哈希不同且含版本标记，为候选版本",
            "evidence": _ev(files_by_id, *g["member_ids"]),
        })
    for g in analysis.get("conflict_groups", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "inferred", "confidence": "heuristic",
            "text": f"文件 {', '.join(g['member_ids'])} stem 相同、哈希不同且无版本标记，存在来源冲突（{g.get('note','')}）",
            "evidence": _ev(files_by_id, *g["member_ids"]),
        })
    for rel in analysis.get("explicit_relations", []):
        n += 1
        claims.append({
            "claim_id": f"C{n:03d}", "kind": "directly_read", "confidence": "explicit",
            "text": "显式关系：" + core.stable_json(rel, indent=None),
            "evidence": [{"source_id": "case", "locator": "case.json",
                          "quote": core.stable_json(rel, indent=None)}],
        })
    for f in files:
        sources.append({
            "source_id": f["id"], "rel_path": f["rel_path"],
            "sha256": f["sha256"], "type": f["type"],
        })
    return claims, sources


def merge_draft_claims(claims, draft, files, text_cache):
    """把 draft.json 的语义 notes/queries 追加为带证据的 claims；返回新 claims。"""
    out = list(claims)
    n = len(out)
    for note in draft.get("notes", []) or []:
        n += 1
        out.append({
            "claim_id": f"C{n:03d}",
            "kind": note.get("kind", "correction"),
            "confidence": note.get("confidence", "inferred"),
            "text": note.get("text", ""),
            "basis": note.get("basis", ""),
            "evidence": note.get("evidence", []),
        })
    for q in draft.get("queries", []) or []:
        n += 1
        out.append({
            "claim_id": f"C{n:03d}",
            "kind": "query_answer",
            "confidence": "inferred",
            "text": f"[{q.get('query_id')}] {q.get('text','')} → {q.get('answer','')}",
            "status": q.get("status", "insufficient_evidence"),
            "evidence": q.get("evidence", []),
        })
    return out


# --- 主数据构造

def build_library(input_dir, case, contract, status, runtime):
    files, dirs, errors, degradations = core.scan_inventory(input_dir, case)
    aliases, vnotes, metadata, queries, ops = core.discover_case_features(case)
    analysis = core.analyze(files, aliases, vnotes, metadata)

    # 文本缓存：提取一次，写 work/texts/<id>.txt
    text_cache = {}
    for f in files:
        p = input_dir / f["rel_path"]
        text, _m = core.extract_file_text(p, f["type"])
        if text:
            text_cache[f["id"]] = text

    index = core.build_index(files, text_cache)
    query_results = core.run_queries(queries, files, text_cache)

    library = {
        "case_id": derive_case_id(case, input_dir),
        "contract_keys": [k for k, _d in contract],
        "files": files,
        "dirs": dirs,
        "analysis": analysis,
        "queries": {"raw": queries, "results": query_results},
        "operations": ops,
        "index": index,
        "errors": errors,
        "degradations": degradations,
    }
    # 文本统计（index 条目数 + 有文本层文件数）
    library["index_stats"] = {
        "terms": len(index),
        "files_with_text": len(text_cache),
        "total_files": len(files),
    }
    return library, text_cache


# --- 子命令

def cmd_init(input_dir, work_dir, output_dir):
    start = time.time()
    contract = []
    case, case_text, case_err = load_case(input_dir)
    case_id = derive_case_id(case, input_dir)
    if case_err:
        status = "input_error"
        errors = [case_err]
    else:
        contract = extract_contract(case)
        status = "partial"
        errors = []

    # 先落盘 partial 骨架（防 0 分，前 3 分钟内必有产物）
    skeleton = build_skeleton(case_id, contract, status)
    skeleton["errors"] = errors
    skeleton["runtime"] = {"cmd": "init"}
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text(
            json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

    library, text_cache = build_library(input_dir, case, contract, status, start)
    # 写文本缓存与工作稿模板
    texts_dir = work_dir / "texts"
    try:
        texts_dir.mkdir(parents=True, exist_ok=True)
        for fid, text in text_cache.items():
            (texts_dir / f"{fid}.txt").write_text(text, encoding="utf-8")
    except OSError:
        pass

    draft_template = {
        "case_id": case_id,
        "notes": [],
        "queries": [],
    }
    if library["queries"]["raw"]:
        draft_template["queries"] = [
            {"query_id": q["query_id"], "text": q["text"],
             "answer": "", "status": "insufficient_evidence", "evidence": []}
            for q in library["queries"]["raw"]
        ]

    try:
        (work_dir / "library.json").write_text(
            json.dumps(_strip_text_cache(library), ensure_ascii=False, indent=2),
            encoding="utf-8")
        (work_dir / "draft.json").write_text(
            json.dumps(draft_template, ensure_ascii=False, indent=2), encoding="utf-8")
        (work_dir / "case_text.txt").write_text(case_text, encoding="utf-8")
    except OSError as exc:
        skeleton["degradations"].append({"kind": "work_write_error",
                                          "detail": str(exc)})

    # init 阶段的 result.json（partial）
    skeleton["result"] = core.summary_of(library)
    skeleton["degradations"] = library["degradations"]
    all_errors = list(library["errors"])
    if case_err and case_err not in all_errors:
        all_errors.insert(0, case_err)
    skeleton["errors"] = all_errors
    skeleton["status"] = status
    skeleton["runtime"] = {"cmd": "init", "duration_ms": int((time.time() - start) * 1000)}
    try:
        (output_dir / "result.json").write_text(
            json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return 0


def _strip_text_cache(library):
    """library.json 不直接内嵌全文，仅保留统计，避免体积过大。"""
    lib = dict(library)
    lib.pop("index", None)
    lib["index"] = {"_terms_count": library["index_stats"]["terms"]}
    return lib


def cmd_show(work_dir):
    lib_path = work_dir / "library.json"
    if not lib_path.exists():
        print("无可显示：请先运行 init。")
        return 0
    lib = json.loads(lib_path.read_text(encoding="utf-8"))
    print(f"case_id      : {lib.get('case_id')}")
    print(f"contract_keys: {lib.get('contract_keys')}")
    print(f"files        : {len(lib.get('files', []))}   dirs: {len(lib.get('dirs', []))}")
    print(f"errors       : {len(lib.get('errors', []))}   degradations: {len(lib.get('degradations', []))}")
    print("\n== 文件清单 ==")
    for f in lib.get("files", []):
        marker = "TXT" if f.get("text_layer") else "----"
        print(f"  {f['id']}  [{marker}]  {f['rel_path']}  sha256={f.get('sha256')}  size={f.get('size')}  "
              f"type={f['type']}  ({f.get('text_method') or f.get('status')})")
    a = lib.get("analysis", {})
    for label, key in (("重复组", "duplicate_groups"), ("版本组", "version_groups"),
                       ("冲突组", "conflict_groups"),
                       ("显式关系", "explicit_relations")):
        groups = a.get(key, [])
        print(f"\n== {label} ({len(groups)}) ==")
        for g in groups:
            print("  ", core.stable_json(g, indent=None))
    q = lib.get("queries", {})
    if q.get("raw"):
        print("\n== 检索问题 ==")
        for r in q.get("results", []):
            print(f"  {r['query_id']}  [{r['status']}]  {r['text']}")
            for h in r.get("hits", []):
                print(f"      hit {h['file_id']} score={h['score']}  {h['rel_path']}")
    if lib.get("operations"):
        print("\n== 恢复/索引操作 ==")
        for op in lib["operations"]:
            print("  ", core.stable_json(op, indent=None))
    if lib.get("errors"):
        print("\n== errors ==")
        for e in lib["errors"]:
            print("  ", core.stable_json(e, indent=None))
    if lib.get("degradations"):
        print("\n== degradations ==")
        for d in lib["degradations"]:
            print("  ", core.stable_json(d, indent=None))
    return 0


def _load_text_cache(work_dir, library):
    cache = {}
    texts_dir = work_dir / "texts"
    for f in library.get("files", []):
        p = texts_dir / f"{f['id']}.txt"
        if p.exists():
            try:
                cache[f["id"]] = p.read_text(encoding="utf-8")
            except OSError:
                cache[f["id"]] = ""
        else:
            cache[f["id"]] = ""
    return cache


def cmd_check(work_dir, input_dir):
    lib_path = work_dir / "library.json"
    draft_path = work_dir / "draft.json"
    case_text = ""
    for p in (work_dir / "case_text.txt",):
        if p.exists():
            case_text = p.read_text(encoding="utf-8", errors="replace")
    out = {"ok": False, "problems": [], "warnings": [], "note_count": 0,
           "query_count": 0}
    if not lib_path.exists():
        out["problems"].append("work/library.json 不存在，先运行 init")
        _write_check(out, work_dir)
        return 0
    library = json.loads(lib_path.read_text(encoding="utf-8"))
    text_cache = _load_text_cache(work_dir, library)
    if not draft_path.exists():
        out["problems"].append("work/draft.json 不存在；build 将退回 no_draft")
        _write_check(out, work_dir)
        return 0
    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except Exception as exc:
        out["problems"].append(f"draft.json 解析失败: {exc}")
        _write_check(out, work_dir)
        return 0
    ok, report = core.validate_draft(draft, library["files"], case_text, text_cache)
    out.update(report)
    out["ok"] = ok
    _write_check(out, work_dir)
    print("ok" if ok else "FAILED", core.stable_json(out, indent=None))
    return 0


def _write_check(report, work_dir):
    (work_dir / "check_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")


def cmd_build(input_dir, work_dir, output_dir):
    start = time.time()
    lib_path = work_dir / "library.json"
    if not lib_path.exists():
        # 直接 build 兜底：现场重建盘点
        case, _case_text, _err = load_case(input_dir)
        contract = extract_contract(case)
        library, _tc = build_library(input_dir, case, contract, "partial", start)
        draft = {"case_id": derive_case_id(case, input_dir), "notes": [], "queries": []}
    else:
        library = json.loads(lib_path.read_text(encoding="utf-8"))
        draft_path = work_dir / "draft.json"
        draft = {"case_id": library.get("case_id"), "notes": [], "queries": []}
        if draft_path.exists():
            try:
                draft = json.loads(draft_path.read_text(encoding="utf-8"))
            except Exception:
                draft = {"case_id": library.get("case_id"), "notes": [], "queries": []}

    case, case_text, case_err = load_case(input_dir)
    contract = extract_contract(case)
    case_id = library.get("case_id") or derive_case_id(case, input_dir)
    library["case_id"] = library.get("case_id") or case_id

    # 复盘索引：library.json 里 index 被压缩，需重建
    files = library["files"]
    text_cache = _load_text_cache(work_dir, library)
    index = core.build_index(files, text_cache)
    library["index"] = index
    qraw = library["queries"]["raw"]
    library["queries"]["results"] = core.run_queries(qraw, files, text_cache)

    # 状态判定
    status = "partial"
    degradations = list(library.get("degradations", []))
    errors = list(library.get("errors", []))
    control = {"case.json", "case.JSON", "case.jsn", "input.json", "task.md"}
    material_files = [f for f in files if Path(f["rel_path"]).name not in control]
    if case_err:
        if not material_files:
            status = "insufficient_evidence"
        else:
            status = "input_error"
            if case_err not in errors:
                errors.append(case_err)
    elif not material_files:
        status = "insufficient_evidence"
    else:
        has_draft = bool(draft.get("notes") or draft.get("queries"))
        if has_draft and not errors and not degradations and not library["analysis"].get("conflict_groups"):
            status = "ok"
        elif has_draft:
            status = "partial"
        else:
            degradations.append({"kind": "no_draft",
                                 "detail": "未写 work/draft.json，交付物仅为确定性盘点，语义解释未补齐"})

    # 组装 claims / sources / uncertainty / change_log
    base_claims, sources = build_claims_base(library)
    claims = merge_draft_claims(base_claims, draft, files, text_cache)
    uncertainty_items = []
    for c in claims:
        if c.get("kind") == "inferred" and c.get("confidence") == "heuristic":
            uncertainty_items.append({"claim_id": c["claim_id"], "text": c["text"],
                                       "reason": "启发式归一化 stem / 哈希差异，无显式行政版本备注"})
    for g in library["analysis"].get("conflict_groups", []):
        uncertainty_items.append({"group": g["stem"], "member_ids": g["member_ids"],
                                   "reason": "来源冲突，等待人工裁定"})

    change_log = []
    for op in library.get("operations", []):
        change_log.append({"action": "recorded_operation", "locator": "case.json",
                           "before": None, "after": core.stable_json(op, indent=None),
                           "reason": "case.json 声明的恢复/索引操作原样记录",
                           "note": "语义需人工确认，不擅自执行破坏性操作"})

    result = {
        "case_id": case_id,
        "status": status,
        "result": core.summary_of(library),
        "claims": claims,
        "uncertainties": uncertainty_items,
        "sources": sources,
        "uncertainty": {"summary": f"{len(uncertainty_items)} 项启发式/冲突判断待人工确认",
                         "count": len(uncertainty_items), "items": uncertainty_items},
        "change_log": change_log,
        "pending_human": [u for u in uncertainty_items],
        "errors": errors,
        "degradations": degradations,
        "runtime": {"cmd": "build", "duration_ms": int((time.time() - start) * 1000)},
    }
    # output_contract 字段回填（真实计算值优先，缺料回退 default；契约字段排在最前）
    contract_result = {}
    for key, default in contract:
        contract_result[key] = core.contract_value(key, default, library, claims, sources,
                                                    change_log, uncertainty_items,
                                                    errors, degradations)
    final = dict(contract_result)
    for key in ("case_id", "status", "result", "claims", "uncertainties", "sources",
                "uncertainty", "change_log", "pending_human", "errors",
                "degradations", "runtime"):
        final[key] = result.get(key)

    # 写终产物
    write_outputs(output_dir, final, library, claims, sources, case_id, work_dir)
    return 0


def write_outputs(output_dir, final, library, claims, sources, case_id, work_dir):
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        # library_index.json：内容级事实，确定性（不含 runtime）
        lib_out = _content_library(library)
        (output_dir / "library_index.json").write_text(
            json.dumps(lib_out, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        # evidence_map.json
        ev_map = {"case_id": case_id, "claims": {}, "sources": sources}
        for c in claims:
            ev_map["claims"][c["claim_id"]] = {
                "kind": c.get("kind"),
                "confidence": c.get("confidence"),
                "text": c.get("text"),
                "evidence": c.get("evidence", []),
            }
        (output_dir / "evidence_map.json").write_text(
            json.dumps(ev_map, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        # inventory.md
        md = _render_inventory(final, lib_out)
        (output_dir / "inventory.md").write_text(md, encoding="utf-8")
    except OSError as exc:
        fallback = work_dir / "output_fallback"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            (fallback / "result.json").write_text(
                json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def _content_library(library):
    """library_index.json 只含内容级事实，保证跨环境字节一致。"""
    files = []
    for f in library.get("files", []):
        ff = {k: f[k] for k in ("id", "rel_path", "size", "sha256", "type",
                                 "text_layer", "text_method", "mode") if k in f}
        if "text_len" in f:
            ff["text_len"] = f["text_len"]
        files.append(ff)
    return {
        "case_id": library.get("case_id"),
        "contract_keys": library.get("contract_keys"),
        "files": files,
        "dirs": library.get("dirs", []),
        "analysis": library.get("analysis", {}),
        "queries": library.get("queries", {}),
        "operations": library.get("operations", []),
        "errors": library.get("errors", []),
        "degradations": library.get("degradations", []),
        "index": library.get("index", {}),
        "index_stats": library.get("index_stats", {}),
    }


def _render_inventory(final, lib_out):
    lines = ["# 科研文档库修复 — 交接清单", ""]
    lines.append(f"- case_id：`{final.get('case_id')}`")
    lines.append(f"- status：`{final.get('status')}`")
    lines.append(f"- 文件数：{len(lib_out.get('files', []))}；目录数：{len(lib_out.get('dirs', []))}")
    lines.append("")
    a = lib_out.get("analysis", {})
    lines.append(f"- 重复组：{len(a.get('duplicate_groups', []))}")
    lines.append(f"- 版本组：{len(a.get('version_groups', []))}")
    lines.append(f"- 冲突组：{len(a.get('conflict_groups', []))}")
    lines.append(f"- 显式关系：{len(a.get('explicit_relations', []))}")
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| ID | 相对路径 | 类型 | 大小 | sha256 | 文本层 |")
    lines.append("|---|---|---|---|---|---|")
    for f in lib_out.get("files", []):
        sha = (f.get("sha256") or "")[:12] + "…" if f.get("sha256") else "unreadable"
        lines.append(f"| {f['id']} | {f['rel_path']} | {f['type']} | {f.get('size')} "
                     f"| {sha} | {'是' if f.get('text_layer') else '否'} |")
    lines.append("")
    if lib_out.get("queries", {}).get("raw"):
        lines.append("## 检索问题与命中")
        lines.append("")
        for r in lib_out["queries"].get("results", []):
            lines.append(f"- {r['query_id']} [{r['status']}]：{r['text']}")
            for h in r.get("hits", []):
                lines.append(f"  - {h['file_id']} (score={h['score']})：{h['rel_path']}")
        lines.append("")
    if final.get("errors"):
        lines.append("## 错误与失败")
        lines.append("")
        for e in final["errors"]:
            lines.append(f"- {core.stable_json(e, indent=None)}")
        lines.append("")
    if final.get("degradations"):
        lines.append("## 降级说明")
        lines.append("")
        for d in final["degradations"]:
            lines.append(f"- {core.stable_json(d, indent=None)}")
        lines.append("")
    lines.append("> 本清单由确定性引擎生成；语义判断见 result.json 的 claims 与 evidence_map.json。")
    lines.append("")
    return "\n".join(lines)


def cmd_selfcheck(output_dir):
    problems = []
    required = ("result.json", "evidence_map.json", "inventory.md")
    for name in required:
        p = output_dir / name
        if not p.exists() or p.stat().st_size == 0:
            problems.append(f"缺少产物或为空：{name}")
    # library_index.json 可选但推荐
    if not (output_dir / "library_index.json").exists():
        problems.append("缺少 library_index.json（推荐产出）")
    result = None
    try:
        result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
            if key not in result:
                problems.append(f"result.json 缺少信封字段 {key}")
        if result.get("status") not in ALLOWED_STATUS:
            problems.append(f"status 非法：{result.get('status')}")
    except Exception as exc:
        problems.append(f"result.json 不可解析：{exc}")
    report = {"ok": not problems, "problems": problems}
    (output_dir / "selfcheck_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8")
    print("SELFCHECK", "ok" if report["ok"] else "FAILED")
    if problems:
        for p in problems:
            print(" -", p)
    return 0


# --- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description="科研文档库修复 Skill")
    parser.add_argument("cmd", choices=["init", "show", "check", "build", "selfcheck"])
    parser.add_argument("--input", default=None)
    parser.add_argument("--work", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    input_dir, work_dir, output_dir = resolve_dirs(args)
    work_dir, output_dir, _deg = ensure_dirs(work_dir, output_dir)

    try:
        if args.cmd == "init":
            return cmd_init(input_dir, work_dir, output_dir)
        if args.cmd == "show":
            return cmd_show(work_dir)
        if args.cmd == "check":
            return cmd_check(work_dir, input_dir)
        if args.cmd == "build":
            return cmd_build(input_dir, work_dir, output_dir)
        if args.cmd == "selfcheck":
            return cmd_selfcheck(output_dir)
    except Exception as exc:  # 兜底：永不崩溃，退出码恒 0
        try:
            (output_dir / "result.json").write_text(
                json.dumps({"case_id": "unknown", "status": "input_error",
                            "errors": [{"kind": "fatal", "detail": f"{exc.__class__.__name__}: {exc}"}],
                            "sources": [], "uncertainty": {}, "change_log": []},
                           ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())