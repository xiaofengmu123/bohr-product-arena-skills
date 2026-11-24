#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据驱动写作 Skill 入口：scaffold → (Agent 推理) → finalize。

用法（在 Skill 根目录下执行）：
  python scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python scripts/main.py finalize --input /input --output /app/output --work /app/work [--merge /app/work/result_patch.json]

默认（不带子命令）：先 scaffold（不覆盖非 in_progress 结果），再 finalize。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as ci

# 让 stdout 稳定输出 UTF-8（Windows 控制台编码差异下也不乱码/不抛异常）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def load_case_and_contract(input_dir):
    """发现并读取 case.json，解析契约；返回 (case, contract, cnote, errors, disc)。"""
    disc = ci.discover_inputs(input_dir)
    errors = list(disc["notes"])
    case = None
    if disc["case_json"]:
        case, err = ci.load_json_robust(disc["case_json"])
        if err:
            errors.append(err)
    else:
        errors.append("no case.json found under %s" % input_dir)
    contract, cnote = ci.extract_contract(case)
    return case, contract, cnote, errors, disc


def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case, contract, cnote, errors, disc = load_case_and_contract(args.input)
    out_path = os.path.join(args.output, "result.json")
    # 不覆盖已 finalize 的结果：重复运行安全。
    if os.path.exists(out_path):
        existing, err = ci.load_json_robust(out_path)
        if err == "" and isinstance(existing, dict) and existing.get("status") not in ("in_progress", None):
            print(json.dumps({
                "ok": True, "skipped": True,
                "reason": "result.json already finalized; not overwritten",
                "status": existing.get("status"),
            }, ensure_ascii=False))
            return 0
    result = ci.build_skeleton(case, contract, args.input, errors)
    result["change_log"] = [{
        "time": ci.now_iso(),
        "action": "scaffold",
        "detail": "落盘结果骨架（status=in_progress），回显契约键：" + ", ".join(sorted(contract.keys())),
    }]
    ci.atomic_write_json(out_path, result)
    meta = {
        "case_id": result.get("case_id"),
        "contract": contract,
        "contract_note": cnote,
        "inputs": disc,
        "status": "in_progress",
    }
    ci.atomic_write_json(os.path.join(args.work, "contract_meta.json"), meta)
    print(json.dumps({
        "ok": True,
        "case_id": result.get("case_id"),
        "status": "in_progress",
        "contract_keys": sorted(contract.keys()),
        "contract_note": cnote,
        "result": out_path,
        "contract_meta": os.path.join(args.work, "contract_meta.json"),
    }, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case, contract, cnote, errors, disc = load_case_and_contract(args.input)
    out_path = os.path.join(args.output, "result.json")
    if not os.path.exists(out_path):
        result = ci.build_skeleton(case, contract, args.input, errors)
    else:
        result, err = ci.load_json_robust(out_path)
        if err or not isinstance(result, dict):
            result = ci.build_skeleton(case, contract, args.input, errors)
            errors.append("existing result.json unreadable; rebuilt skeleton: %s" % err)

    # 合并 Agent 推理补丁（若提供）。
    pre_change = result.get("change_log") or []
    if args.merge:
        patch, err = ci.load_json_robust(args.merge)
        if err == "" and isinstance(patch, dict):
            result = ci.deep_merge(result, patch)
        else:
            errors.append("merge patch unreadable: %s" % err)

    # 保证契约键齐全（Agent 补丁不得删除契约键）。
    for key in contract:
        if key not in result:
            result[key] = ci._init_value(contract[key])
    for key, default in (("case_id", None), ("status", None), ("sources", []),
                         ("uncertainty", []), ("pending_human", []),
                         ("change_log", []), ("limitations", []), ("claims", [])):
        result.setdefault(key, default)

    # status 仍为 in_progress 时诚实降级。
    if result.get("status") == "in_progress":
        dv = result.get("deliverable")
        result["status"] = "partial" if (dv and dv not in ({}, "", [])) else "insufficient_evidence"

    result["errors"] = (result.get("errors") or []) + errors
    issues = ci.validate_result(result, contract)

    # 保证 scaffold 记录不丢（Agent 补丁可能整体替换 change_log）。
    cl = result.get("change_log")
    if not isinstance(cl, list):
        cl = []
        result["change_log"] = cl
    if not any(isinstance(e, dict) and e.get("action") == "scaffold" for e in cl):
        for e in reversed(pre_change):
            cl.insert(0, e)

    cl.append({
        "time": ci.now_iso(),
        "action": "finalize",
        "detail": "校验 %d 项契约键，%d 个结构问题；合并来源=%s" % (
            len(contract), len(issues), os.path.basename(args.merge) if args.merge else "none"),
    })
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["finalized_at"] = ci.now_iso()
    result["runtime"]["status_history"] = (result["runtime"].get("status_history") or []) + [result["status"]]

    ci.atomic_write_json(out_path, result)
    report = ci.render_markdown(result, args.output)
    print(json.dumps({
        "ok": True,
        "status": result["status"],
        "issues": issues,
        "result": out_path,
        "report": report,
    }, ensure_ascii=False))
    return 0


def add_common(p, suppress=False):
    d = argparse.SUPPRESS if suppress else "/input"
    p.add_argument("--input", default=d)
    d = argparse.SUPPRESS if suppress else "/app/output"
    p.add_argument("--output", default=d)
    d = argparse.SUPPRESS if suppress else "/app/work"
    p.add_argument("--work", default=d)
    return p


def main():
    ap = argparse.ArgumentParser(description="证据驱动写作 Skill：scaffold/finalize")
    add_common(ap)
    sub = ap.add_subparsers(dest="cmd")
    add_common(sub.add_parser("scaffold", help="落盘 status=in_progress 的结果骨架"), suppress=True)
    fp = add_common(sub.add_parser("finalize", help="合并补丁、校验契约、渲染报告"), suppress=True)
    fp.add_argument("--merge", default=None, help="Agent 推理补丁 JSON 路径")
    args = ap.parse_args()

    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    # 默认：先 scaffold 再 finalize（finalize 自身幂等且不覆盖已改进结果）。
    rc = cmd_scaffold(args)
    if rc != 0:
        return rc
    return cmd_finalize(args)


if __name__ == "__main__":
    sys.exit(main())
