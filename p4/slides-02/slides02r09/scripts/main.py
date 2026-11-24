#!/usr/bin/env python3
"""学术演示稿 Skill 的单文件编排器（仅 Python 标准库）。

子命令：
  scaffold  读取 case.json，先落盘一版 partial 结果骨架与工作区种子文件（幂等）。
  record    增量追加一条 change/source/fact/evidence/version 记录。
  check     校验 result.json 契约完整性与证据溯源，写 self_check.json（退出码 1=有缺口）。
  finalize  定稿：合并 sources/change_log/uncertainty/pending_human，写终版 result.json。

路径规则：--input/--work/--output 覆盖，否则读环境变量 INPUT_DIR/WORK_DIR/OUTPUT_DIR，
缺省 /input、/app/work、/app/output。脚本不依赖当前工作目录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULTS = {"input": "/input", "work": "/app/work", "output": "/app/output"}
RESERVED = ["case_id", "status", "sources", "uncertainty", "pending_human", "change_log"]
STORES = {
    "change": "change_log.json",
    "source": "sources.json",
    "fact": "facts.json",
    "evidence": "evidence_map.json",
    "version": "versions.json",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return Path(path).read_text(encoding=enc), None
        except UnicodeDecodeError:
            continue
    return None, "unreadable encoding"


def read_json(path):
    if not os.path.exists(path):
        return None, "missing"
    txt, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(txt), None
    except json.JSONDecodeError as exc:
        return None, "bad JSON: %s" % exc


def write_json(path, obj) -> Path:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return Path(path)


def read_list(path):
    obj, err = read_json(path)
    return obj if (err is None and isinstance(obj, list)) else []


def parse_list(s):
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def parse_ints(s):
    out = []
    for x in parse_list(s):
        try:
            out.append(int(x))
        except ValueError:
            pass
    return out


def find_case_json(input_dir):
    p = Path(input_dir)
    for cand in ("case.json", "CASE.json", "Case.json"):
        if (p / cand).exists():
            return str(p / cand)
    jsons = sorted(p.glob("*.json")) if p.exists() else []
    for j in jsons:
        obj, err = read_json(j)
        if err is None and isinstance(obj, dict) and ("case_id" in obj or "output_contract" in obj):
            return str(j)
    return None


def resolve_dirs(args):
    inp = args.input or os.environ.get("INPUT_DIR", DEFAULTS["input"])
    work = args.work or os.environ.get("WORK_DIR", DEFAULTS["work"])
    out = args.output or os.environ.get("OUTPUT_DIR", DEFAULTS["output"])
    return Path(inp), Path(work), Path(out)


def cmd_scaffold(args):
    inp, work, out = resolve_dirs(args)
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    result_path = out / "result.json"
    if result_path.exists() and not args.force:
        obj, err = read_json(result_path)
        if err is None and isinstance(obj, dict):
            print(json.dumps({"skipped": True,
                              "reason": "result.json exists; incremental mode (use --force to reset)"},
                             ensure_ascii=False))
            return 0
    cj = find_case_json(inp)
    case_id, contract, errors = "unknown", {}, []
    if cj is None:
        errors.append("case.json not found under input dir")
    else:
        obj, err = read_json(cj)
        if err:
            errors.append("case.json unreadable: %s" % err)
        elif not isinstance(obj, dict):
            errors.append("case.json is not a JSON object")
        else:
            case_id = str(obj.get("case_id") or obj.get("id") or "unknown")
            contract = obj.get("output_contract")
            if contract is None:
                contract = {}
            if not isinstance(contract, dict):
                errors.append("output_contract is not an object; treating as empty")
                contract = {}
    skeleton = {"case_id": case_id, "status": "partial"}
    for k in sorted(contract):
        skeleton[k] = None
    skeleton["sources"] = []
    skeleton["uncertainty"] = {"unresolved": list(errors), "missing_artifacts": [], "notes": ""}
    skeleton["pending_human"] = []
    skeleton["change_log"] = [{
        "t": now_iso(), "step": "scaffold", "action": "write partial result skeleton",
        "status": "done", "reason": "persist honest partial result within first 3 minutes",
    }]
    write_json(result_path, skeleton)
    write_json(work / "case_summary.json", {
        "case_id": case_id, "input_dir": str(inp), "output_dir": str(out),
        "case_json": cj, "output_contract": contract, "errors": errors, "created_at": now_iso(),
    })
    seeds = {"facts.json": [], "evidence_map.json": [], "sources.json": [], "versions.json": []}
    for name, val in seeds.items():
        if not (work / name).exists() or args.force:
            write_json(work / name, val)
    if not (work / "change_log.json").exists() or args.force:
        write_json(work / "change_log.json", skeleton["change_log"])
    print(json.dumps({"case_id": case_id, "contract_fields": len(contract), "warnings": errors},
                     ensure_ascii=False))
    return 0


def cmd_record(args):
    _, work, _ = resolve_dirs(args)
    os.makedirs(work, exist_ok=True)
    kind = args.kind
    entry = {"t": now_iso()}
    if kind == "change":
        entry.update({"step": args.step or "", "action": args.action or "",
                      "status": args.status or "done", "reason": args.reason or ""})
    elif kind == "source":
        entry.update({"ref": args.ref or "", "loc": args.loc or "",
                      "type": args.type or "other", "used_in": parse_ints(args.used_in)})
    elif kind == "fact":
        entry.update({"id": args.id or "", "fact_kind": args.fact_kind or "quote",
                      "quote": args.quote or "", "loc": args.loc or "",
                      "used_in": parse_ints(args.used_in)})
    elif kind == "evidence":
        entry.update({"page": args.page, "role": args.role or "", "claim": args.claim or "",
                      "fact_ids": parse_list(args.fact_ids), "refs": parse_list(args.refs),
                      "loc": args.loc or ""})
    elif kind == "version":
        files = []
        for f in parse_list(args.files):
            digest = None
            if f and os.path.exists(f):
                digest = hashlib.sha256(Path(f).read_bytes()).hexdigest()[:16]
            files.append({"path": f, "digest": digest})
        entry.update({"name": args.name or "", "note": args.note or "", "files": files})
    items = read_list(work / STORES[kind])
    items.append(entry)
    write_json(work / STORES[kind], items)
    if kind != "change":
        cl = read_list(work / "change_log.json")
        cl.append({"t": now_iso(), "step": args.step or "record",
                   "action": "%s recorded" % kind, "status": "done", "reason": args.reason or ""})
        write_json(work / "change_log.json", cl)
    print(json.dumps({"recorded": kind, "total": len(items)}, ensure_ascii=False))
    return 0


def check_artifact(value, out: Path):
    """校验一个产物路径存在且魔数正确（PDF/PNG/PPTX）。返回 (ok, detail)。"""
    p = Path(value)
    if not p.is_absolute():
        p = out / value
    if not p.exists():
        return False, "missing file: %s" % value
    head = p.read_bytes()[:8]
    if value.lower().endswith(".pdf") and not head.startswith(b"%PDF"):
        return False, "bad PDF magic: %s" % value
    if value.lower().endswith(".png") and head[:8] != b"\x89PNG\r\n\x1a\n":
        return False, "bad PNG magic: %s" % value
    if value.lower().endswith(".pptx") and head[:2] != b"PK":
        return False, "bad pptx magic: %s" % value
    return True, "exists"


def run_check(work: Path, out: Path):
    result, err = read_json(out / "result.json")
    if err or not isinstance(result, dict):
        checks = [{"id": "result_exists", "label": "result.json 可读",
                   "status": "fail", "detail": err or "not object"}]
        self_check = {"t": now_iso(), "checks": checks,
                      "summary": {"pass": 0, "fail": 1, "warn": 0}, "overall": "gaps"}
        write_json(out / "self_check.json", self_check)
        print(json.dumps(self_check["summary"], ensure_ascii=False))
        return 1
    summary, _ = read_json(work / "case_summary.json")
    contract = (summary or {}).get("output_contract") or {}
    facts = read_list(work / "facts.json")
    ev = read_list(work / "evidence_map.json")
    srcs = read_list(work / "sources.json")
    cl = read_list(work / "change_log.json")
    checks = []

    def add(cid, label, ok, detail=""):
        checks.append({"id": cid, "label": label,
                       "status": "pass" if ok else "fail", "detail": detail})

    add("case_id", "case_id 非空", bool(result.get("case_id")))
    add("status", "status 合法", result.get("status") in ("partial", "complete", "failed"))
    missing = [k for k in contract if k not in result]
    add("contract_keys", "output_contract 字段全部存在", not missing, "missing=%s" % missing)
    nulls = [k for k in contract if result.get(k) is None]
    if result.get("status") == "complete":
        add("contract_filled", "complete 状态无 null 契约字段", not nulls, "null=%s" % nulls)
    else:
        checks.append({"id": "contract_filled", "label": "null 契约字段已如实标注",
                       "status": "warn", "detail": "null=%s" % nulls})
    add("reserved", "保留字段齐全",
        all(k in result for k in ("sources", "uncertainty", "pending_human", "change_log")))
    add("change_log", "change_log 非空", "change_log" in result and len(result.get("change_log") or []) > 0)
    if result.get("status") == "complete":
        add("sources_nonempty", "complete 需有 sources", len(srcs) > 0)
        add("evidence_nonempty", "complete 需有 evidence_map", len(ev) > 0)
    bad_fact = [f for f in facts if not (f.get("quote", "").strip() and f.get("loc", "").strip())]
    add("fact_trace", "每个事实都有逐字 quote 与 locator", not bad_fact, "bad=%d" % len(bad_fact))
    fact_ids = {f.get("id") for f in facts if f.get("id")}
    dangling = []
    for e in ev:
        for fid in (e.get("fact_ids") or []):
            if fid not in fact_ids:
                dangling.append((e.get("page"), fid))
    add("fact_binding", "evidence 引用的事实都存在", not dangling, "dangling=%s" % dangling)
    bad_ev = [e for e in ev if not (e.get("page") is not None
                                    and e.get("role", "").strip() and e.get("claim", "").strip())]
    add("evidence_shape", "evidence 有 page/role/claim", not bad_ev, "bad=%d" % len(bad_ev))
    file_keys = [k for k in contract
                 if any(t in k.lower() for t in ("file", "preview", "present"))]
    for k in file_keys:
        val = result.get(k)
        vals = val if isinstance(val, list) else ([val] if isinstance(val, str) and val else [])
        bad = []
        for v in vals:
            ok, detail = check_artifact(v, out)
            if not ok:
                bad.append(detail)
        label = "产物 %s 存在且可解析" % k
        if not bad:
            add("artifact_%s" % k, label, True, "")
        elif result.get("status") == "complete":
            add("artifact_%s" % k, label, False, "bad=%s" % bad)
        else:
            checks.append({"id": "artifact_%s" % k, "label": label,
                           "status": "warn", "detail": "bad=%s" % bad})
    fails = sum(1 for c in checks if c["status"] == "fail")
    warns = sum(1 for c in checks if c["status"] == "warn")
    self_check = {"t": now_iso(), "checks": checks,
                  "summary": {"pass": len(checks) - fails - warns, "fail": fails, "warn": warns},
                  "overall": "gaps" if fails else "ok"}
    write_json(out / "self_check.json", self_check)
    print(json.dumps(self_check["summary"], ensure_ascii=False))
    return 1 if fails else 0


def cmd_check(args):
    _, work, out = resolve_dirs(args)
    return run_check(work, out)


def cmd_finalize(args):
    _, work, out = resolve_dirs(args)
    result, err = read_json(out / "result.json")
    if err or not isinstance(result, dict):
        print(json.dumps({"error": "result.json missing; run scaffold first"}, ensure_ascii=False))
        return 1
    status = args.status or result.get("status") or "partial"
    result["status"] = status
    result["sources"] = read_list(work / "sources.json")
    result["change_log"] = read_list(work / "change_log.json")
    result["change_log"].append({"t": now_iso(), "step": "finalize",
                                 "action": "set status=%s" % status, "status": "done",
                                 "reason": args.note or ""})
    result.setdefault("uncertainty", {"unresolved": [], "missing_artifacts": [], "notes": ""})
    result.setdefault("pending_human", [])
    if args.note:
        notes = result["uncertainty"].get("notes", "")
        result["uncertainty"]["notes"] = (notes + " | " + args.note) if notes else args.note
    write_json(out / "result.json", result)
    return run_check(work, out)


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", help="input dir (default /input or $INPUT_DIR)")
    common.add_argument("--work", help="work dir (default /app/work or $WORK_DIR)")
    common.add_argument("--output", help="output dir (default /app/output or $OUTPUT_DIR)")
    s = sub.add_parser("scaffold", parents=[common], help="write partial result skeleton")
    s.add_argument("--force", action="store_true", help="reset existing skeleton")
    r = sub.add_parser("record", parents=[common], help="append one record")
    r.add_argument("--kind", required=True, choices=list(STORES))
    r.add_argument("--step"); r.add_argument("--action"); r.add_argument("--status"); r.add_argument("--reason")
    r.add_argument("--ref"); r.add_argument("--loc"); r.add_argument("--type")
    r.add_argument("--id"); r.add_argument("--fact-kind"); r.add_argument("--quote")
    r.add_argument("--page", type=int); r.add_argument("--role"); r.add_argument("--claim")
    r.add_argument("--fact-ids"); r.add_argument("--refs"); r.add_argument("--used-in")
    r.add_argument("--name"); r.add_argument("--note"); r.add_argument("--files")
    c = sub.add_parser("check", parents=[common], help="validate result.json; exit 1 on gaps")
    f = sub.add_parser("finalize", parents=[common], help="merge records and set status")
    f.add_argument("--status", choices=["complete", "partial", "failed"])
    f.add_argument("--note")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return {"scaffold": cmd_scaffold, "record": cmd_record,
            "check": cmd_check, "finalize": cmd_finalize}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
