#!/usr/bin/env python3
"""write_result.py -- 原子管理 /app/output/result.json。

子命令：init / set / add-list / log / finalize / get
确定性保证：
- JSON 一律 sort_keys=True 序列化，同内容必同字节（时间字段除外）；
- 追加/更新只改目标键，不重排其余内容；
- 先写临时文件再 os.replace，避免半成品。
"""
import argparse
import datetime
import json
import os
import sys

DEFAULT_OUT = "/app/output/result.json"
DEFAULT_BRIEF = "/app/work/case_brief.json"
DEFAULT_CONTRACT = {
    "deliverables": [],
    "fact_source_map": [],
    "version_boundary": {
        "modifications": [],
        "human_confirm": [],
        "material_gaps": [],
        "anomalies": [],
        "editable": True,
    },
}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def load_case_id(brief=None):
    paths = []
    if brief:
        paths.append(brief)
    paths.append(DEFAULT_BRIEF)
    paths.append("/input/case.json")
    for p in paths:
        d = load_json(p)
        if isinstance(d, dict):
            for k in ("case_id", "id", "caseId"):
                if d.get(k) is not None:
                    return str(d[k])
    return None


def ensure_skeleton(out, contract, brief=None):
    d = load_json(out)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("case_id", load_case_id(brief))
    d.setdefault("status", "in_progress")
    for k, v in (contract or DEFAULT_CONTRACT).items():
        d.setdefault(k, v)
    for k in ("sources", "uncertainty", "pending_human", "limitations", "change_log"):
        d.setdefault(k, [])
    d.setdefault("runtime", {})
    return d


def atomic_write(path, obj):
    out_dir = os.path.dirname(path) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def parse_value(s):
    s = (s or "").strip()
    if s == "":
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError, TypeError):
        return s


def cmd_init(out, contract, brief=None):
    d = ensure_skeleton(out, contract, brief)
    d["status"] = "in_progress"
    d.setdefault("runtime", {}).setdefault("created_at", now_iso())
    entry = {"time": now_iso(), "action": "init", "key": "result.json",
             "detail": "skeleton written (in_progress)"}
    logs = d.setdefault("change_log", [])
    if not isinstance(logs, list):
        logs = d["change_log"] = []
    if not any(isinstance(e, dict) and e.get("action") == "init" for e in logs):
        logs.append(entry)
    atomic_write(out, d)
    return d


def main():
    ap = argparse.ArgumentParser(description="管理 result.json")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--brief", default=DEFAULT_BRIEF)
    ap.add_argument("--contract", default=None, help="声明 output_contract 的 JSON 文件")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    p = sub.add_parser("set")
    p.add_argument("key")
    p.add_argument("value")

    p = sub.add_parser("add-list")
    p.add_argument("key")
    p.add_argument("item")

    p = sub.add_parser("log")
    p.add_argument("action")
    p.add_argument("key")
    p.add_argument("detail")

    p = sub.add_parser("finalize")
    p.add_argument("--status", required=True)

    sub.add_parser("get")

    a = ap.parse_args()

    contract = DEFAULT_CONTRACT
    if a.contract and os.path.isfile(a.contract):
        c = load_json(a.contract)
        if isinstance(c, dict) and c:
            contract = c

    if a.cmd == "init":
        d = cmd_init(a.out, contract, a.brief)
    else:
        d = load_json(a.out)
        if not isinstance(d, dict):
            d = ensure_skeleton(a.out, contract, a.brief)
        if a.cmd == "set":
            d[a.key] = parse_value(a.value)
        elif a.cmd == "add-list":
            lst = d.setdefault(a.key, [])
            if not isinstance(lst, list):
                lst = d[a.key] = []
            lst.append(parse_value(a.item))
        elif a.cmd == "log":
            lst = d.setdefault("change_log", [])
            if not isinstance(lst, list):
                lst = d["change_log"] = []
            lst.append({"time": now_iso(), "action": a.action,
                        "key": a.key, "detail": a.detail})
        elif a.cmd == "finalize":
            d["status"] = a.status
            lst = d.setdefault("change_log", [])
            if not isinstance(lst, list):
                lst = d["change_log"] = []
            lst.append({"time": now_iso(), "action": "finalize",
                        "key": "status", "detail": "finalized as " + a.status})
        atomic_write(a.out, d)

    if a.cmd == "get":
        print(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps({"ok": True, "status": d.get("status"),
                          "change_log_len": len(d.get("change_log", []))},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
