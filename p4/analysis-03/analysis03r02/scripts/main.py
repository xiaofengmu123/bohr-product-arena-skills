#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-viz-checkpoint 主控 CLI（仅标准库）。

子命令：
  init     发现输入、提取契约、写 result.json 骨架 + work/plan.md + work/ledger.json
  commit   增量更新 result.json：--set / --set-key+--set-value / --status / --log / --phase / --files
  ledger   打印预算台账（阶段计划、已消耗、剩余请求估算）
  check    校验 result.json 结构与预算记录；--fail 时遇 FAIL 退出 1，--require-contract
           时契约字段缺失计为 FAIL

默认参数：--input /input --output /app/output --work /app/work。
设计约束：除 `check --fail` 命中 FAIL 外，任何命令退出码恒为 0，保证 Harness 不被中断。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C


def ensure_dirs(output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)


def make_plan(ctx):
    lines = [
        "# 执行计划（sci-viz-checkpoint）",
        "",
        "- case_id: `%s`" % ctx["case_id"],
        "- 契约来源: `%s`" % (ctx["contract_path"] or "未找到"),
        "- 材料: %d 个数据文件 / %d 个文本材料" % (
            len(ctx["found"]["data"]), len(ctx["found"]["materials"])),
        "",
        "## 阶段预算",
        "",
        "| 阶段 | 时间窗 | 请求预算 | 门槛 |",
        "|-|-|-|-|",
    ]
    for p in C.PHASE_PLAN:
        lines.append("| %s %s | %s | %d | %s |" % (
            p["id"], p["name"], p["window"], p["requests"], p["gate"]))
    lines.append("| 保留 | 失败恢复 | %d | 仅在中断/失败时使用 |" % C.RESERVE_REQUESTS)
    lines += [
        "",
        "## 步骤（阶段 B）",
        "",
        "1. 读数据字典与字段语义，记录清洗口径",
        "2. 缺失/异常/重复先记录再处理",
        "3. 统计（描述统计、分组、相关/趋势）",
        "4. 按数据类型与分析目的选图（见 references/figure-spec.md）",
        "5. 编码/分面/配色，标注与图注",
        "6. 导出 SVG + PDF + >=300dpi PNG",
        "",
        "每步用 `python3 scripts/main.py commit --phase B --log '...'` 增量落盘。",
    ]
    return "\n".join(lines) + "\n"


def cmd_init(args):
    ensure_dirs(args.output, args.work)
    ctx = C.case_context(args.input)
    result_path = os.path.join(args.output, "result.json")
    if os.path.exists(result_path):
        existing, _err = C.load_json_safe(result_path)
        if isinstance(existing, dict) and existing:
            obj = existing
            obj.setdefault("case_id", ctx["case_id"])
            obj.setdefault("budget", C.new_budget())
        else:
            obj = C.build_skeleton(ctx["case_id"], ctx["contract"])
    else:
        obj = C.build_skeleton(ctx["case_id"], ctx["contract"])

    has_any = bool(ctx["found"]["case_json"] or ctx["found"]["task_md"]
                   or ctx["found"]["data"] or ctx["found"]["materials"])
    if not has_any:
        obj["status"] = "insufficient_evidence"
        obj.setdefault("uncertainty", []).append(
            {"item": "empty_input", "reason": "输入目录无 case.json/task.md/材料",
             "pending_human": True})

    obj.setdefault("errors", [])
    obj.setdefault("degradations", [])
    for deg in ctx["degradations"]:
        if deg not in obj["degradations"]:
            obj["degradations"].append(deg)

    if ctx["contract"] is None:
        note = "未发现 output_contract，使用最小字段集兜底，字段语义见 SKILL.md 第 3 节"
        obj.setdefault("uncertainty", [])
        if not any(isinstance(u, dict) and u.get("item") == "output_contract_missing"
                   for u in obj["uncertainty"]):
            obj["uncertainty"].append(
                {"item": "output_contract_missing", "reason": note, "pending_human": True})

    C.write_json_atomic(result_path, obj)
    C.write_text_atomic(os.path.join(args.work, "plan.md"), make_plan(ctx))
    C.write_json_atomic(os.path.join(args.work, "ledger.json"), obj.get("budget"))
    print(json.dumps({"ok": True, "result": result_path, "case_id": ctx["case_id"],
                      "contract_path": ctx["contract_path"], "contract": bool(ctx["contract"]),
                      "degradations": obj.get("degradations", [])}, ensure_ascii=False))
    return 0


def cmd_commit(args):
    ensure_dirs(args.output, args.work)
    result_path = os.path.join(args.output, "result.json")
    obj, _err = C.load_json_safe(result_path)
    if not isinstance(obj, dict) or not obj:
        obj = C.build_skeleton("unknown", None)
        obj["runtime"]["started_at"] = C.now_iso()
        obj["budget"]["started_at"] = obj["runtime"]["started_at"]

    if args.set:
        parsed = C.parse_assign(args.set)
        if parsed.get("__raw__") is not None and list(parsed) == ["__raw__"]:
            obj.setdefault("errors", []).append(
                {"step": "commit", "message": "无法解析 --set: %s" % args.set})
        else:
            C.merge_deep(obj, parsed)
    if args.set_key:
        C.set_dotted(obj, args.set_key, C.parse_scalar(args.set_value))
    if args.status:
        obj["status"] = args.status
    if args.phase:
        obj.setdefault("budget", C.new_budget())
        started = (obj.get("runtime") or {}).get("started_at")
        C.mark_phase_done(obj["budget"], args.phase, started)

    obj.setdefault("change_log", [])
    seq = max([e.get("seq", 0) for e in obj["change_log"]] + [0]) + 1
    entry = {
        "ts": C.now_iso(),
        "seq": seq,
        "step": args.step or "commit",
        "phase": args.phase,
        "action": args.log or args.set or "",
        "files": [f for f in (args.files or "").split(",") if f],
    }
    obj["change_log"].append(entry)
    obj.setdefault("runtime", {})["updated_at"] = C.now_iso()
    C.write_json_atomic(result_path, obj)
    print(json.dumps({"ok": True, "seq": seq, "status": obj.get("status"),
                      "change_log_len": len(obj["change_log"])}, ensure_ascii=False))
    return 0


def cmd_ledger(args):
    result_path = os.path.join(args.output, "result.json")
    obj, _err = C.load_json_safe(result_path)
    budget = obj.get("budget", C.new_budget()) if isinstance(obj, dict) else C.new_budget()
    started = budget.get("started_at") or (obj.get("runtime") or {}).get("started_at")
    elapsed = C.elapsed_since(started) if started else None
    print("budget ledger")
    print("  total: %ds / %d requests" % (
        budget.get("total_seconds", C.TOTAL_SECONDS),
        budget.get("total_requests", C.TOTAL_REQUESTS)))
    print("  elapsed: %s" % ("unknown" if elapsed is None else "%ds" % elapsed))
    for p in budget.get("phases", []):
        mark = "DONE" if p.get("done") else "todo"
        print("  [%s] %s %s  %s  (%ds / %d req)  %s" % (
            mark, p.get("id"), p.get("name"), p.get("window"),
            p.get("time_sec", 0), p.get("requests", 0), p.get("gate")))
    print("  reserve: %d requests" % budget.get("reserve_requests", C.RESERVE_REQUESTS))
    return 0


def cmd_check(args):
    result_path = os.path.join(args.output, "result.json")
    obj, _err = C.load_json_safe(result_path)
    contract = None
    if args.require_contract:
        ctx = C.case_context(args.input)
        contract = ctx["contract"]
    checks, failed = C.validate(obj, require_contract=args.require_contract, contract=contract)
    for c in checks:
        print("[%s] %s: %s" % (c["level"], c["code"], c["message"]))
    print("summary: %d check(s), %d FAIL" % (len(checks), failed))
    if args.fail and failed:
        return 1
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--output", default="/app/output")
    common.add_argument("--work", default="/app/work")

    p = argparse.ArgumentParser(description="sci-viz-checkpoint 主控脚本")
    sub = p.add_subparsers(dest="command")

    s_init = sub.add_parser("init", help="发现输入、写 result.json 骨架与 work/plan.md",
                            parents=[common])
    s_init.set_defaults(func=cmd_init)

    s_commit = sub.add_parser("commit", help="增量更新 result.json", parents=[common])
    s_commit.add_argument("--set", help="JSON 对象或 key=value，如 'status=\"ok\"' 或 '{\"a\":1}'")
    s_commit.add_argument("--set-key", help="按点号路径设置单个字段")
    s_commit.add_argument("--set-value", help="--set-key 对应的值")
    s_commit.add_argument("--status", choices=C.STATUS_VALUES)
    s_commit.add_argument("--log", default="", help="本步动作说明")
    s_commit.add_argument("--step", default="commit", help="步骤名")
    s_commit.add_argument("--phase", choices=("A", "B", "C"), help="所属阶段")
    s_commit.add_argument("--files", default="", help="逗号分隔的本步产物路径")
    s_commit.set_defaults(func=cmd_commit)

    s_ledger = sub.add_parser("ledger", help="打印预算台账", parents=[common])
    s_ledger.set_defaults(func=cmd_ledger)

    s_check = sub.add_parser("check", help="校验 result.json", parents=[common])
    s_check.add_argument("--fail", action="store_true", help="命中 FAIL 时退出码 1")
    s_check.add_argument("--require-contract", action="store_true", help="契约字段缺失计为 FAIL")
    s_check.set_defaults(func=cmd_check)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except Exception as exc:  # 任何异常也不中断 Harness：写最小合法结果并退出 0
        try:
            ensure_dirs(args.output, args.work)
            minimal = {
                "case_id": "unknown", "status": "input_error",
                "sources": [], "uncertainty": [], "change_log": [],
                "errors": [{"step": getattr(args, "command", None), "message": str(exc)}],
                "degradations": [], "runtime": {"started_at": C.now_iso()},
                "artifacts": [], "budget": C.new_budget(),
            }
            C.write_json_atomic(os.path.join(args.output, "result.json"), minimal)
        except Exception:
            pass
        print("error: %s" % exc, file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
