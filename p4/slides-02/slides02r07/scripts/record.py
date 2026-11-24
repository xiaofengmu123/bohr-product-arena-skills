#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""record.py：对 /app/output/result.json 做增量、可回溯的修改。

只依赖 Python 标准库。原子写盘；result.json 缺失/损坏时先重建最小骨架再记录，
不中断后续增量。每次调用都会自动追加 change_log。

子命令：
    change      --step --action [--reason] [--status]
    source      --ref --loc [--kind] [--used-in]
    evidence    --page --role --claim [--refs] [--loc]
    version     --name [--note] [--files]
    set-field   --field --value [--type str|json]
    set-status  --status
    note        --message [--bucket unresolved|missing|notes]
"""

import argparse
import datetime
import json
import os
import sys

DEFAULT_OUTPUT = "/app/output"
RESERVED = ("case_id", "status", "sources", "uncertainty", "pending_human",
            "change_log", "errors", "degradations", "runtime")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:
        return None, str(e)


def write_json(path, obj):
    try:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
        return None
    except Exception as e:
        return "write_error:%s" % e


def new_skeleton():
    return {
        "case_id": "", "status": "partial", "sources": [],
        "uncertainty": {"unresolved": [], "missing_artifacts": [], "notes": ""},
        "pending_human": [], "change_log": [], "errors": [],
        "degradations": [], "runtime": {},
    }


def load_or_init(path):
    obj, err = read_json(path)
    if not isinstance(obj, dict):
        obj = new_skeleton()
        if err:
            obj["errors"].append("record_init:" + err)
            obj["change_log"].append({
                "t": now_iso(), "step": "record", "action": "recover_skeleton",
                "status": "partial", "reason": "result.json 缺失/损坏，重建最小骨架后继续",
            })
    return obj


def append_change(obj, step, action, reason, status):
    obj.setdefault("change_log", [])
    obj["change_log"].append({
        "t": now_iso(), "step": step, "action": action,
        "status": status or obj.get("status", "partial"), "reason": reason or "",
    })
    obj.setdefault("runtime", {})["updated_at"] = now_iso()


def split_list(text):
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def cmd_change(obj, a):
    append_change(obj, a.step or "update", a.action or "", a.reason, a.status)


def cmd_source(obj, a):
    obj.setdefault("sources", [])
    obj["sources"].append({
        "ref": a.ref or "", "loc": a.loc or "", "kind": a.kind or "unknown",
        "used_in": split_list(a.used_in),
    })
    append_change(obj, "source", "add_source", "source %s @ %s" % (a.ref, a.loc), None)


def cmd_evidence(obj, a):
    em = obj.get("evidence_map")
    if not isinstance(em, list):
        em = []
    em.append({
        "page": a.page, "role": a.role or "", "claim": a.claim or "",
        "refs": split_list(a.refs), "loc": a.loc or "",
    })
    obj["evidence_map"] = em
    append_change(obj, "evidence", "add_evidence", "page %s role %s" % (a.page, a.role), None)


def cmd_version(obj, a):
    vr = obj.get("version_records")
    if not isinstance(vr, list):
        vr = []
    vr.append({
        "name": a.name or ("v%d" % (len(vr) + 1)), "note": a.note or "",
        "files": split_list(a.files), "t": now_iso(),
    })
    obj["version_records"] = vr
    append_change(obj, "version", "save_version", "version %s" % (a.name or ""), None)


def cmd_set_field(obj, a):
    field = a.field
    if field in RESERVED:
        append_change(obj, "set-field", "refuse_reserved", "保留字段 %s 不可直接覆盖" % field, None)
        obj.setdefault("errors", []).append("set_field_refused_reserved:%s" % field)
        return
    val = a.value
    if a.type == "json":
        try:
            val = json.loads(val)
        except ValueError as e:
            obj.setdefault("errors", []).append("set_field_bad_json:%s" % e)
            append_change(obj, "set-field", "bad_json_value", "field %s 值非 JSON" % field, None)
            return
    obj[field] = val
    append_change(obj, "set-field", "set_field", "field %s -> %r" % (field, val), None)


def cmd_set_status(obj, a):
    obj["status"] = a.status
    append_change(obj, "set-status", "set_status", "status -> %s" % a.status, a.status)


def cmd_note(obj, a):
    u = obj.setdefault("uncertainty", {})
    bucket = a.bucket or "notes"
    if bucket == "unresolved":
        u.setdefault("unresolved", []).append(a.message)
    elif bucket == "missing":
        u.setdefault("missing_artifacts", []).append(a.message)
    else:
        u["notes"] = (u.get("notes") or "") + ((a.message + "\n") if a.message else "")
    append_change(obj, "note", "add_uncertainty_note", a.message, None)


def build_parser():
    p = argparse.ArgumentParser(description="增量记录 result.json（stdlib only）")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="result.json 所在目录或完整路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("change", help="追加 change_log")
    c.add_argument("--step", default="update")
    c.add_argument("--action", required=True)
    c.add_argument("--reason", default="")
    c.add_argument("--status", default="")
    c.set_defaults(func=cmd_change)

    s = sub.add_parser("source", help="追加 sources")
    s.add_argument("--ref", required=True)
    s.add_argument("--loc", default="")
    s.add_argument("--kind", default="unknown")
    s.add_argument("--used-in", default="")
    s.set_defaults(func=cmd_source)

    e = sub.add_parser("evidence", help="追加 evidence_map 页级证据")
    e.add_argument("--page", required=True)
    e.add_argument("--role", default="")
    e.add_argument("--claim", default="")
    e.add_argument("--refs", default="")
    e.add_argument("--loc", default="")
    e.set_defaults(func=cmd_evidence)

    v = sub.add_parser("version", help="追加 version_records 快照")
    v.add_argument("--name", default="")
    v.add_argument("--note", default="")
    v.add_argument("--files", default="")
    v.set_defaults(func=cmd_version)

    f = sub.add_parser("set-field", help="写契约字段（保留字段拒绝）")
    f.add_argument("--field", required=True)
    f.add_argument("--value", default="")
    f.add_argument("--type", choices=["str", "json"], default="str")
    f.set_defaults(func=cmd_set_field)

    st = sub.add_parser("set-status", help="设置 status")
    st.add_argument("--status", required=True)
    st.set_defaults(func=cmd_set_status)

    n = sub.add_parser("note", help="追加 uncertainty 说明")
    n.add_argument("--message", required=True)
    n.add_argument("--bucket", choices=["unresolved", "missing", "notes"], default="notes")
    n.set_defaults(func=cmd_note)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    out_dir = args.output
    path = out_dir if out_dir.endswith(".json") else os.path.join(out_dir, "result.json")
    obj = load_or_init(path)
    args.func(obj, args)
    err = write_json(path, obj)
    if err:
        print("record degraded: %s" % err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
