#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py — 落盘并增量维护 /app/output/result.json 骨架。

用途:
  1. 初始化：从 /input/case.json 与 /input/task.md 读取案例标识与 output_contract，
     立即写出一版合法 partial 结果（总预算内前 3 分钟必须完成）。
  2. 增量更新：把后续各步骤产出的字段合并进 result.json，保持骨架始终最新。

仅依赖 Python 标准库。用法:
  python3 scripts/scaffold.py --input /input/case.json --task /input/task.md --output /app/output
  python3 scripts/scaffold.py --update --result /app/output/result.json --merge /app/work/step3.json
  python3 scripts/scaffold.py --update --result /app/output/result.json --set 'report_goal={"goal":"x"}'
"""

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_CONTRACT = {
    "report_goal": None,
    "audience": None,
    "narrative": None,
    "pages": [],
    "claims": [],
    "editable_deck": None,
    "evidence_map": None,
    "qa_prep": None,
    "rendering": None,
    "limitations": [],
}

STATUS_ENUM = {"complete", "partial", "insufficient_evidence", "input_error", "failed"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path):
    """按 utf-8-sig 容错读取；坏编码不崩溃，返回 (text, error)。"""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read(), None
    except OSError as e:
        return None, str(e)


def load_json(path):
    """容错加载 JSON；返回 (obj, error)。"""
    text, err = read_text(path)
    if err is not None:
        return None, err
    if text is None:
        return None, "missing"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "json_error: %s" % e


def dump_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def build_skeleton(case, input_path, task_path):
    case = case if isinstance(case, dict) else {}
    case_id = case.get("id") or case.get("armiId") or "unknown"
    contract = case.get("output_contract")
    if not isinstance(contract, dict) or not contract:
        contract = copy.deepcopy(DEFAULT_CONTRACT)
    else:
        contract = copy.deepcopy(contract)

    result = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [
            {
                "ts": now_iso(),
                "action": "scaffold",
                "scope": "result.json",
                "detail": "落盘 partial 骨架",
            }
        ],
        "runtime": {
            "generated_by": "scaffold.py",
            "at": now_iso(),
            "input": input_path,
            "task": task_path,
        },
    }
    # 把 output_contract 的字段平铺到顶层，保证"含全部字段"；不覆盖 case_id/status
    for k, v in contract.items():
        result.setdefault(k, copy.deepcopy(v))
    return result


def deep_merge(base, patch):
    """字典递归合并；列表追加去重；其余覆盖。返回 base。"""
    if not isinstance(patch, dict):
        return base
    for k, v in patch.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        elif k in base and isinstance(base[k], list) and isinstance(v, list):
            for item in v:
                if item not in base[k]:
                    base[k].append(item)
        else:
            base[k] = copy.deepcopy(v)
    return base


def log_change(result, action, scope, detail=""):
    result.setdefault("change_log", [])
    entry = {"ts": now_iso(), "action": action, "scope": scope}
    if detail:
        entry["detail"] = detail
    result["change_log"].append(entry)


def parse_set(pair):
    if "=" not in pair:
        raise ValueError("--set 需为 key=json 形式: %r" % pair)
    k, v = pair.split("=", 1)
    try:
        val = json.loads(v)
    except ValueError:
        val = v
    return k, val


def init_main(args):
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    case, err = load_json(args.input)
    if err is not None:
        case = {}
        if err != "missing":
            print("warn: case.json 读取失败(%s)，落盘最小骨架" % err, file=sys.stderr)
    result = build_skeleton(case, args.input, args.task)
    if err is not None and err != "missing":
        result["errors"].append("case.json: %s" % err)
    dump_json(args.output, result)
    print(json.dumps({"ok": True, "case_id": result["case_id"], "status": result["status"]},
                     ensure_ascii=False))
    return 0


def update_main(args):
    result, err = load_json(args.result)
    if err is not None or not isinstance(result, dict):
        print("error: result.json 不可读: %s" % err, file=sys.stderr)
        return 2
    if args.merge:
        patch, perr = load_json(args.merge)
        if perr is not None or not isinstance(patch, dict):
            print("error: --merge 文件不可读: %s" % perr, file=sys.stderr)
            return 2
        deep_merge(result, patch)
    for pair in (args.set or []):
        k, v = parse_set(pair)
        deep_merge(result, {k: v})
    parts = []
    if args.merge:
        parts.append("merge=%s" % args.merge)
    for pair in (args.set or []):
        parts.append("set=%s" % pair.split("=", 1)[0])
    log_change(result, "update", "result.json", "; ".join(parts))
    dump_json(args.result, result)
    print(json.dumps({"ok": True, "case_id": result.get("case_id"), "status": result.get("status")},
                     ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--output", default="/app/output/result.json")
    ap.add_argument("--update", action="store_true", help="增量合并而非初始化")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--merge", help="待合并的 JSON 文件")
    ap.add_argument("--set", action="append", help="key=json 形式的单字段覆盖，可多次")
    args = ap.parse_args()
    if args.update:
        return update_main(args)
    return init_main(args)


if __name__ == "__main__":
    sys.exit(main())
