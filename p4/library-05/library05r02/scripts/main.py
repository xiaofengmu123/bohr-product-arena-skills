#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 执行入口（六命令）。

    python scripts/main.py init      # 扫描盘点 + 立即落盘 partial result.json 骨架
    python scripts/main.py show      # 打印带 locator 的文件/分组/问题，供模型阅读
    # 模型阅读 show 输出后，把判断写进 /app/work/draft.json
    python scripts/main.py check     # 机械校验 draft.json（结构 + source_id + quote 逐字命中）
    python scripts/main.py build     # 合并确定性结果与 draft，无条件收口交付
    python scripts/main.py selfcheck # 只复检 /app/output 下产物

均支持 --input/--work/--output 覆盖默认目录；默认路径也必须能直接跑。
退出码恒为 0：真实问题写进 result.json 的 errors/degradations，不靠退出码表达。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import libcore  # noqa: E402
import rules  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

SKILL_VERSION = "2.0.0"
DELIVERABLE = "library_index.json"

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")


# ---------------------------------------------------------------- 目录与输入
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


def ensure_output(output_dir: Path, work_dir: Path) -> tuple:
    """输出目录不可写时依次降级。返回 (实际目录, 降级说明)。"""
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


def load_case(input_dir: Path):
    """读 /input/case.json（utf-8-sig 容错）。坏 JSON 返回 {'_bad': reason}。"""
    p = input_dir / "case.json"
    if not p.exists():
        return {"_bad": "missing_case_json"}
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        return json.loads(text)
    except Exception as e:  # noqa: BLE001
        return {"_bad": f"bad_json:{type(e).__name__}:{e}"}


def load_draft(work_dir: Path):
    p = work_dir / "draft.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return {"_bad": "bad_draft_json"}


# ---------------------------------------------------------------- 相位时间
def phase_log_path(work_dir: Path) -> Path:
    return work_dir / "phase_log.json"


def phase_begin(work_dir: Path, phase: str) -> dict:
    """记录阶段开始并返回阶段日志。首次调用写入 started_at 与预算。"""
    p = phase_log_path(work_dir)
    log = {}
    if p.exists():
        try:
            log = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            log = {}
    log.setdefault("started_at", time.time())
    log.setdefault("budget_seconds", rules.BUDGET_SECONDS)
    log.setdefault("model_requests_budget", rules.MODEL_REQUESTS)
    log.setdefault("phases", {})
    log["phases"][phase] = {"start": time.time(), "end": None}
    _write_json(p, log)
    return log


def phase_end(work_dir: Path, phase: str):
    p = phase_log_path(work_dir)
    try:
        log = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    log.setdefault("phases", {}).setdefault(phase, {"start": time.time(), "end": None})
    log["phases"][phase]["end"] = time.time()
    _write_json(p, log)


def runtime_summary(work_dir: Path) -> dict:
    log = {}
    p = phase_log_path(work_dir)
    if p.exists():
        try:
            log = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            log = {}
    started = log.get("started_at", time.time())
    phases = []
    for name, info in sorted((log.get("phases") or {}).items()):
        dur = None
        if info.get("start") is not None and info.get("end") is not None:
            dur = round(info["end"] - info["start"], 2)
        phases.append({"phase": name, "seconds": dur})
    return {
        "budget_seconds": log.get("budget_seconds", rules.BUDGET_SECONDS),
        "model_requests_budget": log.get("model_requests_budget", rules.MODEL_REQUESTS),
        "early_skeleton_seconds": rules.EARLY_SKELETON_SECONDS,
        "elapsed_seconds": round(time.time() - started, 2),
        "phases": phases,
    }


def _write_json(p: Path, data):
    try:
        p.write_text(rules.stable_dump(data), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- 契约/组装
def build_sources(lib: dict) -> list:
    return [{"id": f["id"], "rel_path": f["rel_path"], "name": f["name"],
             "sha256": f["sha256"], "size": f["size"], "type": f["type"]}
            for f in lib["files"]]


def _ev(files_by_id, f_id, quote):
    f = files_by_id.get(f_id)
    loc = f["rel_path"] if f else f_id
    return {"source_id": f_id, "locator": loc, "quote": quote}


def build_engine_claims(lib: dict) -> list:
    claims = []
    analysis = lib["analysis"]
    files_by_id = {f["id"]: f for f in lib["files"]}
    n = 0
    for g in analysis.get("alias_groups", []):
        n += 1
        q = "|".join(files_by_id[i]["name"] for i in g if i in files_by_id)
        claims.append({"claim_id": f"C{n:03d}", "kind": "directly_read",
                       "confidence": "high",
                       "text": "文件 {} 的 sha256 相同，为同一内容的副本/别名".format(
                           ", ".join(g)),
                       "evidence": [_ev(files_by_id, i, files_by_id[i]["sha256"] or "")
                                    for i in g if i in files_by_id]})
    for g in analysis.get("version_groups", []):
        n += 1
        claims.append({"claim_id": f"C{n:03d}", "kind": "inferred",
                       "confidence": "heuristic",
                       "text": "文件 {} 归一化 stem 相同且带版本标记，为候选版本".format(
                           ", ".join(g["member_ids"])),
                       "evidence": [_ev(files_by_id, i, files_by_id[i]["name"])
                                    for i in g["member_ids"] if i in files_by_id]})
    for g in analysis.get("conflicts", []):
        n += 1
        claims.append({"claim_id": f"C{n:03d}", "kind": "conflict",
                       "confidence": "heuristic",
                       "text": "文件 {} stem 相同、哈希不同且无版本标记，存在来源冲突".format(
                           ", ".join(g["member_ids"])),
                       "evidence": [_ev(files_by_id, i, files_by_id[i]["name"])
                                    for i in g["member_ids"] if i in files_by_id]})
    for rel in analysis.get("explicit_relations", []):
        n += 1
        members = rel.get("member_ids", [])
        text = "显式关系: " + rel["raw"]
        if members:
            text += " (涉及 {})".format(", ".join(members))
        claims.append({"claim_id": f"C{n:03d}", "kind": rel["kind"],
                       "confidence": "explicit",
                       "text": text,
                       "evidence": [{"source_id": rel.get("source", "case.json"),
                                     "locator": rel.get("locator", rel.get("source", "case.json")),
                                     "quote": rel["raw"]}]})
    return claims


def build_draft_claims(draft: dict, base: int) -> list:
    claims = []
    if not isinstance(draft, dict):
        return claims
    for i, note in enumerate(draft.get("notes") or []):
        claims.append({"claim_id": f"N{i + 1:03d}", "kind": note.get("kind"),
                       "confidence": note.get("confidence", "inferred"),
                       "text": note.get("text", ""),
                       "basis": note.get("basis", ""),
                       "evidence": note.get("evidence") or []})
    for i, q in enumerate(draft.get("queries") or []):
        claims.append({"claim_id": f"Q{i + 1:03d}", "kind": "query_answer",
                       "confidence": "explicit" if q.get("status") == "answered" else "inferred",
                       "text": "检索 {}: {}".format(q.get("query_id"),
                                                    q.get("answer", q.get("status", ""))),
                       "evidence": q.get("evidence") or []})
    return claims


def build_query_results(case, lib, draft) -> list:
    queries = []
    if isinstance(case, dict):
        for k in ("queries", "questions", "searches", "search_queries"):
            if isinstance(case.get(k), list):
                queries = case[k]
                break
    index = libcore.build_index(lib)
    det = libcore.run_queries(queries, index, lib["corpus"])
    # draft 覆盖：模型给出的最终答案优先，但保留命中证据
    draft_map = {}
    if isinstance(draft, dict):
        for q in draft.get("queries") or []:
            draft_map[q.get("query_id")] = q
    out = []
    for d in det:
        qid = d["query_id"]
        item = {"query_id": qid, "query": d["query"], "status": d["status"],
                "hits": d["hits"]}
        if qid in draft_map:
            item["answer"] = draft_map[qid].get("answer")
            item["status"] = draft_map[qid].get("status", d["status"])
            item["evidence"] = draft_map[qid].get("evidence") or []
        out.append(item)
    return out


def build_change_log(case, lib, output_dir) -> list:
    """把 case 的 operations 记录为 change_log；recover 操作确定性执行。"""
    log = []
    if not isinstance(case, dict):
        return log
    ops = case.get("operations") or case.get("ops") or case.get("tasks")
    if not isinstance(ops, list):
        return log
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            log.append({"op": str(op), "status": "recorded",
                        "locator": f"case.json#operations[{i}]", "before": None,
                        "after": None, "reason": "non-dict op"})
            continue
        opname = str(op.get("op") or op.get("operation") or "record")
        locator = op.get("locator") or f"case.json#operations[{i}]"
        reason = op.get("reason") or op.get("note") or ""
        entry = {"op": opname, "status": "recorded", "locator": locator,
                 "before": op.get("before"), "after": op.get("after"), "reason": reason}
        if opname in ("recover", "restore", "copy"):
            target = op.get("target") or op.get("name") or op.get("dest")
            src = op.get("source") or op.get("src")
            resolved = _resolve_file(lib, src or target)
            if resolved:
                dest_dir = output_dir / "recovered"
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / (target or resolved["name"])
                    shutil.copyfile(Path(resolved["abs"]), dest)
                    entry["status"] = "executed"
                    entry["after"] = str(dest.relative_to(output_dir.parent)) if output_dir.parent else str(dest)
                    entry["source_file"] = resolved["rel_path"]
                except OSError as e:
                    entry["status"] = "failed"
                    entry["error"] = str(e)
            else:
                entry["status"] = "pending"
                entry["error"] = "target_not_found"
        elif opname in ("index", "index_by"):
            entry["status"] = "executed"
            entry["after"] = DELIVERABLE
        log.append(entry)
    return log


def _resolve_file(lib, name):
    if not name:
        return None
    for f in lib["files"]:
        if f["name"] == name or f["rel_path"] == name or name in f["rel_path"]:
            return {"rel_path": f["rel_path"], "name": f["name"],
                    "abs": str(Path(lib["input_dir"]) / f["rel_path"])}
    return None


def build_pending(lib, draft, case) -> list:
    out = []
    for g in lib["analysis"].get("conflicts", []):
        out.append({"type": "source_conflict", "member_ids": g["member_ids"],
                    "reason": "stem 相同、哈希不同且无版本标记，需人工裁定"})
    for f in lib["files"]:
        if f["type"] == "unreadable" or f["size"] < 0:
            out.append({"type": "unreadable", "file": f["id"],
                        "rel_path": f["rel_path"], "reason": "不可读或 stat 失败"})
    if isinstance(draft, dict) and draft.get("pending_human"):
        for item in draft["pending_human"]:
            out.append(item)
    return out


def build_errors_degradations(case, lib, draft, output_fallback) -> tuple:
    errors, deg = [], []
    if isinstance(case, dict) and case.get("_bad"):
        errors.append({"scope": "case.json", "reason": case["_bad"]})
    if isinstance(draft, dict) and draft.get("_bad"):
        errors.append({"scope": "draft.json", "reason": draft["_bad"]})
    if not isinstance(draft, dict) or not (draft.get("notes") or draft.get("queries")):
        deg.append({"scope": "draft", "reason": "no_draft",
                    "note": "仅确定性盘点结果，无模型语义解释"})
    for f in lib["files"]:
        if f.get("pdf_text_state") and f["pdf_text_state"] != "ok":
            deg.append({"scope": "file", "file": f["id"], "rel_path": f["rel_path"],
                        "reason": f"pdf_text_{f['pdf_text_state']}"})
    if output_fallback:
        deg.append({"scope": "output", "reason": output_fallback})
    return errors, deg


def build_result(lib, case, draft, output_dir, work_dir, output_fallback) -> dict:
    case_id = rules.derive_case_id(case)
    contract_keys = rules.extract_contract(case)
    status = "partial"
    result = rules.build_skeleton(case_id, contract_keys, status)

    engine_claims = build_engine_claims(lib)
    draft_claims = build_draft_claims(draft if isinstance(draft, dict) else {}, len(engine_claims))
    claims = engine_claims + draft_claims
    query_results = build_query_results(case, lib, draft)
    change_log = build_change_log(case, lib, output_dir)
    sources = build_sources(lib)
    pending = build_pending(lib, draft, case)
    errors, degradations = build_errors_degradations(case, lib, draft, output_fallback)

    # 状态判定
    if errors and not lib["files"]:
        status = "input_error"
    elif not lib["files"] and not sources:
        status = "insufficient_evidence"
    elif not degradations and not pending and draft_claims:
        status = "ok"
    elif draft_claims or engine_claims:
        status = "partial" if (degradations or pending) else "ok"
    else:
        status = "partial"

    # 契约字段回填
    for key in contract_keys:
        result[key] = rules.resolve_contract_value(key, lib, draft, {
            "change_log": change_log, "sources": sources,
            "result": {"query_results": query_results},
            "evidence_map": _evidence_map(claims)})

    result["case_id"] = case_id
    result["status"] = status
    result["result"] = {
        "summary": {
            "files_total": lib["scan_meta"].get("files_total", 0),
            "total_bytes": lib["scan_meta"].get("total_bytes", 0),
            "alias_groups": len(lib["analysis"].get("alias_groups", [])),
            "version_groups": len(lib["analysis"].get("version_groups", [])),
            "conflicts": len(lib["analysis"].get("conflicts", [])),
            "queries_answered": sum(1 for q in query_results if q.get("status") == "answered"),
        },
        "groups": {
            "alias_groups": lib["analysis"].get("alias_groups", []),
            "version_groups": lib["analysis"].get("version_groups", []),
            "conflicts": lib["analysis"].get("conflicts", []),
            "explicit_relations": lib["analysis"].get("explicit_relations", []),
        },
        "query_results": query_results,
    }
    result["claims"] = claims
    result["sources"] = sources
    result["evidence_map"] = _evidence_map(claims)
    result["change_log"] = change_log
    result["pending_human"] = pending
    result["errors"] = errors
    result["degradations"] = degradations
    heur = sum(1 for c in claims if c.get("confidence") in ("heuristic", "inferred"))
    result["uncertainty"] = {"summary": "启发式/推断结论 {} 条，需证据复核".format(heur),
                             "count": heur,
                             "items": [{"claim_id": c["claim_id"], "confidence": c["confidence"],
                                        "text": c["text"]} for c in claims
                                       if c.get("confidence") in ("heuristic", "inferred")]}
    result["uncertainties"] = result["uncertainty"]["items"]
    result["runtime"] = runtime_summary(work_dir)
    return result


def _evidence_map(claims) -> list:
    out = []
    for c in claims:
        out.append({"claim_id": c.get("claim_id"),
                    "kind": c.get("kind"),
                    "text": c.get("text"),
                    "evidence": c.get("evidence") or []})
    return out


# ---------------------------------------------------------------- 命令实现
def cmd_init(input_dir, work_dir, output_dir):
    phase_begin(work_dir, "init")
    case = load_case(input_dir)
    lib = libcore.scan(input_dir)
    output_dir, fallback = ensure_output(output_dir, work_dir)
    _write_json(work_dir / "library.json", lib)
    _write_json(work_dir / "case.json", case)
    # 立即落盘 partial 骨架（前 3 分钟硬要求）
    skeleton = rules.build_skeleton(rules.derive_case_id(case),
                                    rules.extract_contract(case), "partial")
    _write_json(output_dir / "result.json", skeleton)
    draft_path = work_dir / "draft.json"
    if not draft_path.exists():
        _write_json(draft_path, {"case_id": rules.derive_case_id(case),
                                 "notes": [], "queries": []})
    phase_end(work_dir, "init")
    print("init done: files={} output={}".format(len(lib["files"]), output_dir))


def cmd_show(work_dir):
    phase_begin(work_dir, "show")
    lib_path = work_dir / "library.json"
    if not lib_path.exists():
        print("no library.json; run init first")
        phase_end(work_dir, "show")
        return
    lib = json.loads(lib_path.read_text(encoding="utf-8"))
    print("== files ==")
    for f in lib["files"]:
        print(f"{f['id']}  {f['rel_path']}  type={f['type']} size={f['size']} "
              f"sha={str(f['sha256'])[:12]} perm={f['permissions']}")
    print("== alias_groups ==")
    for g in lib["analysis"].get("alias_groups", []):
        print("  " + ", ".join(g))
    print("== version_groups ==")
    for g in lib["analysis"].get("version_groups", []):
        print("  stem={} members={} basis={}".format(g["stem"], ",".join(g["member_ids"]), g["basis"]))
    print("== conflicts ==")
    for g in lib["analysis"].get("conflicts", []):
        print("  stem={} members={}".format(g["stem"], ",".join(g["member_ids"])))
    print("== explicit_relations ==")
    for r in lib["analysis"].get("explicit_relations", []):
        print("  [{}] {} <- {}".format(r["kind"], r["raw"], r["source"]))
    print("== scan_meta ==")
    print(json.dumps(lib.get("scan_meta", {}), ensure_ascii=False))
    phase_end(work_dir, "show")


def cmd_check(work_dir):
    phase_begin(work_dir, "check")
    lib_path = work_dir / "library.json"
    draft = load_draft(work_dir)
    report = dict(rules.CHECK_REPORT_TEMPLATE)
    lib = {}
    if lib_path.exists():
        lib = json.loads(lib_path.read_text(encoding="utf-8"))
    if isinstance(draft, dict) and draft.get("_bad"):
        report["errors"].append(draft["_bad"])
    if isinstance(draft, dict):
        known = set(f["id"] for f in lib.get("files", [])) | {"case.json", "task.md", "case", "task"}
        corpus = {f["id"]: lib.get("corpus", {}).get(f["id"], "") for f in lib.get("files", [])}
        for src, text in lib.get("config_files", {}).items():
            corpus[src] = text
        for i, note in enumerate(draft.get("notes") or []):
            rules._validate_note(note, i, known, corpus, report)
        report["note_count"] = len(draft.get("notes") or [])
        report["query_count"] = len(draft.get("queries") or [])
    report["ok"] = (not report["errors"])
    _write_json(work_dir / "check_report.json", report)
    phase_end(work_dir, "check")
    print("check ok={} errors={} warnings={}".format(report["ok"],
                                                     len(report["errors"]),
                                                     len(report["warnings"])))


def cmd_build(input_dir, work_dir, output_dir):
    phase_begin(work_dir, "build")
    case = load_case(input_dir)
    lib_path = work_dir / "library.json"
    if lib_path.exists():
        lib = json.loads(lib_path.read_text(encoding="utf-8"))
    else:
        lib = libcore.scan(input_dir)
    draft = load_draft(work_dir)
    output_dir, fallback = ensure_output(output_dir, work_dir)
    result = build_result(lib, case, draft, output_dir, work_dir, fallback)

    _write_json(output_dir / "result.json", result)
    # 确定性产物（不含 runtime/mtime）
    _write_json(output_dir / DELIVERABLE, json.loads(libcore.dump_library(lib)))
    _write_json(output_dir / "evidence_map.json", _evidence_map(result["claims"]))
    (output_dir / "inventory.md").write_text(render_inventory(lib, result), encoding="utf-8")
    phase_end(work_dir, "build")
    print("build done status={} claims={} queries={}".format(
        result["status"], len(result["claims"]),
        len(result["result"].get("query_results", []))))


def render_inventory(lib, result) -> str:
    lines = ["# 科研文档库盘点交接清单", ""]
    lines.append("- case_id: `{}`".format(result.get("case_id", "")))
    lines.append("- status: `{}`".format(result.get("status", "")))
    lines.append("- 文件数: {}".format(lib["scan_meta"].get("files_total", 0)))
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| id | rel_path | type | size | sha256(前12) | perm |")
    lines.append("|---|---|---|---|---|---|")
    for f in lib["files"]:
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            f["id"], f["rel_path"], f["type"], f["size"],
            (f["sha256"] or "")[:12], f.get("permissions")))
    lines.append("")
    lines.append("## 分组与冲突")
    for kind, title in (("alias_groups", "副本/别名"), ("version_groups", "候选版本"),
                        ("conflicts", "来源冲突")):
        lines.append(f"### {title}")
        for g in lib["analysis"].get(kind, []):
            if isinstance(g, dict):
                members = g.get("member_ids", [])
                stem = g.get("stem", "")
            else:
                members = g
                stem = ""
            lines.append("- {}（stem={}）".format(", ".join(members), stem))
    lines.append("")
    lines.append("## 降级与待办")
    for d in result.get("degradations", []):
        lines.append("- 降级: {}".format(d.get("reason", d)))
    for p in result.get("pending_human", []):
        lines.append("- 待办: {}".format(p.get("reason", p)))
    return "\n".join(lines) + "\n"


def cmd_selfcheck(work_dir, output_dir):
    phase_begin(work_dir, "selfcheck")
    ok = True
    msgs = []
    for name in ("result.json", DELIVERABLE, "evidence_map.json", "inventory.md"):
        p = output_dir / name
        if not p.exists() or p.stat().st_size == 0:
            ok = False
            msgs.append("missing/empty " + name)
    try:
        r = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        if not r.get("case_id") or not r.get("status"):
            ok = False
            msgs.append("result.json 缺 case_id/status")
    except Exception as e:  # noqa: BLE001
        ok = False
        msgs.append("result.json 不可解析: " + str(e))
    print("selfcheck {} {}".format("PASS" if ok else "FAIL", "; ".join(msgs)))
    phase_end(work_dir, "selfcheck")
    return 0


# ---------------------------------------------------------------- 入口
def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description="科研文档库修复 Skill")
    ap.add_argument("command", choices=["init", "show", "check", "build", "selfcheck"])
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args(argv)
    input_dir, work_dir, output_dir = resolve_dirs(args)

    if args.command == "init":
        cmd_init(input_dir, work_dir, output_dir)
    elif args.command == "show":
        cmd_show(work_dir)
    elif args.command == "check":
        cmd_check(work_dir)
    elif args.command == "build":
        cmd_build(input_dir, work_dir, output_dir)
    elif args.command == "selfcheck":
        cmd_selfcheck(work_dir, output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())