#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — 长文档写作 Skill 的确定性脚手架、契约抽取与证据管理。

仅依赖 Python 标准库。职责：
  scan          发现 /input 下的材料，写 discovery.json
  scaffold      抽取输出契约并立即落盘 result.json 骨架（status=partial）
  add-evidence  向 result.json 追加一条证据映射（claim+locator+quote）
  add-change    向 result.json 追加一条修改记录（before/after/reason）
  set-status    更新 result.json 的 status
  finalize      校验 result.json 契约完整性并写 selfcheck.json

用法（相对 Skill 根目录）：
  python3 -B scripts/main.py scan      --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py scaffold  --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py add-evidence --claim "..." --locator "materials/x.md#p3" --quote "..."
  python3 -B scripts/main.py add-change   --action edit --target "1.2 方法" --before "..." --after "..." --reason "..."
  python3 -B scripts/main.py set-status   --status ok
  python3 -B scripts/main.py finalize  --input /input --output /app/output --work /app/work
"""

import argparse
import datetime
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

MAX_FILE_BYTES = 2_000_000
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

CASE_KEYS = ("case.json", "case_json", "input.json")
CASE_ID_KEYS = ("case_id", "caseId", "caseid", "task_id", "taskId", "id")
CONTRACT_KEYS = (
    "output_contract", "outputContract", "output_schema", "outputSchema",
    "result_contract", "result_schema", "resultSchema", "expected_output",
    "expectedOutput", "contract",
)

# 集合类键名提示，用于给契约键补默认值
LIST_HINTS = (
    "list", "items", "sources", "references", "evidence", "errors", "warnings",
    "changes", "uncertainty", "pending", "degradations", "limitations", "notes",
    "tags", "labels", "entries", "records", "mapping", "mappings", "figures",
    "tables", "equations", "citations", "chapters", "sections", "artifacts",
    "outputs", "requirements", "fields", "checks",
)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """多编码容错读取，返回 (text, truncated)。"""
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1)
        truncated = len(data) > max_bytes
        data = data[:max_bytes]
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return data.decode(enc), truncated
            except (UnicodeDecodeError, ValueError):
                continue
        return data.decode("utf-8", errors="replace"), truncated
    except OSError as e:
        return None, False


def safe_json_load(path):
    """容错读 JSON，返回 (obj, error_str_or_None)。"""
    text, truncated = read_text(path)
    if text is None:
        return None, "unreadable: %s" % path
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as e:
        return None, "bad_json: %s (%s)" % (path, e)


def atomic_write_text(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def discover(input_dir):
    """扫描输入目录，返回记录列表。"""
    items = []
    if not os.path.isdir(input_dir):
        return items
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            size = -1
            try:
                size = os.path.getsize(full)
            except OSError:
                pass
            if size > MAX_FILE_BYTES:
                items.append({"path": rel, "abs": full, "kind": "skipped_too_large", "size": size})
                continue
            low = name.lower()
            if low == "case.json":
                kind = "case"
            elif low in ("task.md", "task.txt"):
                kind = "task"
            elif low.endswith(".pdf"):
                kind = "material_pdf"
            elif low.endswith((".md", ".markdown")):
                kind = "material_md"
            elif low.endswith((".txt", ".json", ".jsonl", ".csv")):
                kind = "material_text"
            elif low.endswith((".docx", ".doc", ".pptx", ".xlsx", ".odt")):
                kind = "material_office"
            else:
                kind = "other"
            items.append({"path": rel, "abs": full, "kind": kind, "size": size})
    return items


def find_case_file(input_dir):
    items = discover(input_dir)
    for it in items:
        if it["kind"] == "case":
            return it["abs"]
    # 退回：任何名为 case.json 或含 output_contract 的 json
    for it in items:
        if it["kind"] in ("material_text", "other") and it["path"].lower().endswith(".json"):
            obj, err = safe_json_load(it["abs"])
            if obj is not None and (isinstance(obj, dict) and any(k in obj for k in CONTRACT_KEYS)):
                return it["abs"]
    return None


def extract_case_id(case_obj):
    if isinstance(case_obj, dict):
        for k in CASE_ID_KEYS:
            if k in case_obj and case_obj[k] not in (None, "", []):
                v = case_obj[k]
                return str(v)
        # 嵌套 task/meta
        for sub in ("task", "meta", "metadata"):
            if isinstance(case_obj.get(sub), dict):
                for k in CASE_ID_KEYS:
                    if k in case_obj[sub] and case_obj[sub][k] not in (None, "", []):
                        return str(case_obj[sub][k])
    return "unknown"


def extract_contract(case_obj):
    """从 case.json 提取输出契约。返回 dict（键→默认值）。"""
    contract = {}
    if not isinstance(case_obj, dict):
        return contract
    for k in CONTRACT_KEYS:
        if k not in case_obj:
            continue
        v = case_obj[k]
        if isinstance(v, dict):
            # JSON Schema 风格：properties
            if "properties" in v and isinstance(v["properties"], dict):
                for pk in v["properties"]:
                    contract[pk] = None
            else:
                for ck, cv in v.items():
                    contract[ck] = cv if cv is not None else None
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, str):
                    contract[item] = None
                elif isinstance(item, dict) and "name" in item:
                    contract[item["name"]] = None
        elif isinstance(v, str):
            contract[v] = None
        return contract  # 找到第一个可用契约即返回
    return contract


def default_for_key(key):
    low = key.lower()
    if any(h in low for h in LIST_HINTS):
        return []
    return None


def load_result(output_dir):
    p = os.path.join(output_dir, "result.json")
    obj, err = safe_json_load(p)
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    return {}


def save_result(output_dir, result):
    os.makedirs(output_dir, exist_ok=True)
    atomic_write_json(os.path.join(output_dir, "result.json"), result)


STANDARD_DEFAULTS = {
    "status": "partial",
    "artifact": None,
    "artifacts": [],
    "evidence": [],
    "sources": [],
    "uncertainty": [],
    "change_log": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "runtime": {},
}


def cmd_scan(args):
    items = discover(args.input)
    case_file = find_case_file(args.input)
    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(os.path.join(args.work, "discovery.json"), {
        "input_dir": args.input,
        "scanned_at": now_iso(),
        "case_file": case_file,
        "files": items,
    })
    print("discovered %d files; case_file=%s" % (len(items), case_file))
    for it in items:
        print("  [%s] %s (%s bytes)" % (it["kind"], it["path"], it["size"]))
    return 0


def cmd_scaffold(args):
    case_file = find_case_file(args.input)
    case_obj = {}
    degradations = []
    if case_file is None:
        degradations.append("no case.json found under %s" % args.input)
    else:
        case_obj, err = safe_json_load(case_file)
        if case_obj is None:
            degradations.append(err)
            case_obj = {}
        elif not isinstance(case_obj, dict):
            degradations.append("case.json is not a dict")
            case_obj = {}

    case_id = extract_case_id(case_obj)
    contract = extract_contract(case_obj)

    result = load_result(args.output)
    # case_id：保留已存在的非 unknown 值，否则用本次检测值
    existing_cid = result.get("case_id")
    if existing_cid in (None, "", "unknown") or not existing_cid:
        result["case_id"] = case_id
    elif case_id not in ("unknown",) and existing_cid != case_id:
        result.setdefault("errors", []).append(
            "case_id mismatch: existing=%s detected=%s" % (existing_cid, case_id)
        )

    # 回填契约键（缺值补 null/[]，不改名、不删未知键）
    for k in contract:
        result.setdefault(k, default_for_key(k))
    # 标准字段
    for k, d in STANDARD_DEFAULTS.items():
        if k not in result:
            result[k] = [] if isinstance(d, list) else d
        elif result[k] is None and k == "status":
            result[k] = "partial"

    rt = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    rt.setdefault("started_at", now_iso())
    rt["scaffolded_at"] = now_iso()
    result["runtime"] = rt
    result.setdefault("degradations", [])
    for d in degradations:
        if d not in result["degradations"]:
            result["degradations"].append(d)

    # 空输入诚实降级：无 case.json 且无任何材料 → insufficient_evidence
    items = discover(args.input)
    has_materials = any(it["kind"].startswith("material") for it in items)
    if case_file is None and not has_materials:
        if result.get("status") in (None, "", "partial"):
            result["status"] = "insufficient_evidence"

    save_result(args.output, result)
    print("scaffolded result.json case_id=%s contract_keys=%d status=%s"
          % (result["case_id"], len(contract), result["status"]))
    return 0


def _load_and_save(args):
    result = load_result(args.output)
    if not result:
        result = {"case_id": "unknown"}
        for k, d in STANDARD_DEFAULTS.items():
            result[k] = [] if isinstance(d, list) else d
    return result


def cmd_add_evidence(args):
    result = _load_and_save(args)
    entry = {
        "claim": args.claim or "",
        "locator": args.locator or "",
        "quote": args.quote or "",
        "source": args.source or "",
        "kind": args.kind or "fact",
        "confidence": args.confidence or "high",
    }
    if not args.claim and not args.quote:
        result.setdefault("errors", []).append("add-evidence: claim and quote both empty")
    result.setdefault("evidence", []).append(entry)
    save_result(args.output, result)
    print("evidence entries=%d" % len(result["evidence"]))
    return 0


def cmd_add_change(args):
    result = _load_and_save(args)
    entry = {
        "action": args.action or "edit",
        "target": args.target or "",
        "before": args.before or "",
        "after": args.after or "",
        "reason": args.reason or "",
        "status": args.status or "done",
    }
    result.setdefault("change_log", []).append(entry)
    save_result(args.output, result)
    print("change_log entries=%d" % len(result["change_log"]))
    return 0


def cmd_set_status(args):
    result = _load_and_save(args)
    if args.status not in ALLOWED_STATUS:
        print("invalid status: %s (allowed: %s)" % (args.status, ", ".join(sorted(ALLOWED_STATUS))))
        return 2
    result["status"] = args.status
    save_result(args.output, result)
    print("status=%s" % args.status)
    return 0


def cmd_finalize(args):
    result = _load_and_save(args)
    case_file = find_case_file(args.input)
    contract = {}
    if case_file:
        case_obj, _err = safe_json_load(case_file)
        if isinstance(case_obj, dict):
            contract = extract_contract(case_obj)

    checks = []
    def check(cid, passed, detail):
        checks.append({"check": cid, "pass": bool(passed), "detail": detail})

    check("case_id_present", bool(result.get("case_id")), str(result.get("case_id")))
    check("status_valid", result.get("status") in ALLOWED_STATUS, str(result.get("status")))

    missing = [k for k in contract if k not in result]
    check("contract_keys_complete", not missing, "missing=%s" % missing if missing else "ok")

    evidence = result.get("evidence") or []
    ev_ok = all(isinstance(e, dict) and e.get("locator") and e.get("quote") for e in evidence) \
        if evidence else True
    check("evidence_has_locator_and_quote", ev_ok, "entries=%d" % len(evidence))

    artifact = result.get("artifact")
    if artifact:
        p = artifact if os.path.isabs(artifact) else os.path.join(args.output, artifact)
        check("artifact_exists", os.path.exists(p), p)
    else:
        check("artifact_exists", False, "artifact not set")

    change_log = result.get("change_log") or []
    cl_ok = all(isinstance(e, dict) and (e.get("after") or e.get("before")) for e in change_log) \
        if change_log else True
    check("change_log_has_before_after", cl_ok, "entries=%d" % len(change_log))

    rt = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
    rt["finished_at"] = now_iso()
    result["runtime"] = rt
    result["self_check"] = checks
    save_result(args.output, result)

    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(os.path.join(args.work, "selfcheck.json"), {
        "finalized_at": now_iso(),
        "checks": checks,
    })

    n_pass = sum(1 for c in checks if c["pass"])
    print("finalize: %d/%d checks passed" % (n_pass, len(checks)))
    for c in checks:
        print("  [%s] %s :: %s" % ("PASS" if c["pass"] else "FAIL", c["check"], c["detail"]))
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)

    p = argparse.ArgumentParser(description="长文档写作 Skill 脚手架与证据管理")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", parents=[common], help="扫描输入材料")
    sub.add_parser("scaffold", parents=[common], help="落盘 result.json 骨架")
    sub.add_parser("finalize", parents=[common], help="校验并完成 result.json")

    sp = sub.add_parser("add-evidence", parents=[common], help="追加一条证据映射")
    sp.add_argument("--claim")
    sp.add_argument("--locator")
    sp.add_argument("--quote")
    sp.add_argument("--source", default="")
    sp.add_argument("--kind", default="fact")
    sp.add_argument("--confidence", default="high")

    sp = sub.add_parser("add-change", parents=[common], help="追加一条修改记录")
    sp.add_argument("--action", default="edit")
    sp.add_argument("--target", default="")
    sp.add_argument("--before", default="")
    sp.add_argument("--after", default="")
    sp.add_argument("--reason", default="")
    sp.add_argument("--status", default="done")

    sp = sub.add_parser("set-status", parents=[common], help="更新 status")
    sp.add_argument("--status", required=True)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "add-evidence":
        return cmd_add_evidence(args)
    if args.cmd == "add-change":
        return cmd_add_change(args)
    if args.cmd == "set-status":
        return cmd_set_status(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
