#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口（纯标准库）。

子命令：
  scaffold   发现输入、读 case.json、回填 output_contract、落盘 partial 骨架
  normalize  规范化一个或多个专利号（参数或 JSON 文件）
  extract    从文本/文件抽取专利号候选
  family     读 records JSON，按共用申请号/优先权号归并同族（观测族口径）
  checkpoint 增量落盘：盖时间戳 + 写 change_log + 校验合法性
  budget     打印剩余时间 / 请求预算与阶段护栏（配合 3 分钟早落盘）
  finalize   校验 result.json、补 runtime、生成 report.md
  validate   校验 result.json，打印 VALID / INVALID

默认路径：--input /input --output /app/output --work /app/work
（可用环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 覆盖，亦可传命令行参数覆盖；
  公共参数既可放在子命令前，也可放在子命令后。）

纯标准库实现；同一输入重复运行，分析字段结果一致。
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import patentlib  # noqa: E402


def _env_default(name, fallback):
    return os.environ.get(name) or fallback


def _read_json_arg(path):
    if not path or not os.path.exists(path):
        return None
    obj, err = patentlib.load_json(path)
    if err:
        print("warn: cannot read JSON %s: %s" % (path, err["error"]), file=sys.stderr)
        return None
    return obj


def _load_result(args):
    path = os.path.join(args.output, "result.json")
    if not os.path.exists(path):
        return None, path, "result.json not found"
    result, err = patentlib.load_json(path)
    if err:
        return None, path, err["error"]
    if not isinstance(result, dict):
        return None, path, "result.json is not an object"
    return result, path, None


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------
def cmd_scaffold(args):
    patentlib.os.makedirs(args.output, exist_ok=True)
    patentlib.os.makedirs(args.work, exist_ok=True)

    files = patentlib.discover_input(args.input)
    case_obj = None
    case_path = None
    case_err = None
    for f in files:
        if f["kind"] == "case":
            case_path = f["path"]
            case_obj, case_err = patentlib.load_json(f["path"])
            break

    contract = None
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract") or case_obj.get("outputContract")

    result = patentlib.build_skeleton(case_obj, contract)
    if case_path is not None and not isinstance(case_obj, dict):
        result["errors"].append({
            "item": "case_json_unreadable",
            "reason": (case_err or {}).get("error", "case.json is not a JSON object"),
        })
        result["degradations"].append({
            "item": "case_json_fallback",
            "reason": "proceeded with case_id=unknown and default schema",
        })
        result["uncertainty"].append({"item": "case_id_unknown",
                                      "reason": "case.json unreadable"})
    elif isinstance(case_obj, dict) and result["case_id"] == "unknown":
        result["uncertainty"].append({"item": "case_id_unknown",
                                      "reason": "case.json has no case_id"})

    inventory = []
    for f in files:
        if f["kind"] in ("pdf", "image"):
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"],
                              "note": "binary; read via platform PDF/OCR tools"})
        elif f["kind"] in ("text", "table", "task", "note", "case"):
            text, enc, err = patentlib.read_text(f["path"])
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"],
                              "encoding": enc, "chars": len(text), "read_error": err})
        else:
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"]})

    result.setdefault("input_inventory", inventory)
    if not files:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({"item": "no_input_files",
                                      "reason": "input directory has no discoverable files"})
        result["pending_human"].append({"item": "input_missing",
                                        "why": "provide case.json / task.md / materials"})

    out_path = os.path.join(args.output, "result.json")
    patentlib.write_json(out_path, result)

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract_fields": result.get("contract_fields", []),
        "started_at": result["runtime"]["started_at"],
        "early_save_deadline_seconds": 180,
    }
    patentlib.write_json(os.path.join(args.work, "state.json"), state)

    print("scaffold: wrote %s (status=%s, files=%d, contract_fields=%d)"
          % (out_path, result["status"], len(files), len(result.get("contract_fields", []))))


# ---------------------------------------------------------------------------
# normalize / extract / family
# ---------------------------------------------------------------------------
def cmd_normalize(args):
    if args.file:
        data = _read_json_arg(args.file) or []
        items = data if isinstance(data, list) else [data]
        outputs = [patentlib.normalize_patent_number(x) for x in items]
    elif args.numbers:
        outputs = [patentlib.normalize_patent_number(x) for x in args.numbers]
    else:
        outputs = [patentlib.normalize_patent_number(args.text)]
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


def cmd_extract(args):
    if args.file:
        text, _enc, err = patentlib.read_text(args.file)
        if err and not text:
            print(json.dumps({"error": err}, ensure_ascii=False), file=sys.stderr)
            sys.exit(0)
    else:
        text = args.text
    hits = patentlib.extract_numbers(text)
    print(json.dumps(hits, ensure_ascii=False, indent=2))


def cmd_family(args):
    records = _read_json_arg(args.file) or []
    if not isinstance(records, list):
        records = [records]
    groups = patentlib.group_families(records)
    print(json.dumps({"families": groups, "observed": True, "exhaustive": False},
                     ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# checkpoint / budget
# ---------------------------------------------------------------------------
def cmd_checkpoint(args):
    result, path, err = _load_result(args)
    if err:
        print("checkpoint: %s" % err, file=sys.stderr)
        sys.exit(0)
    at = patentlib.now_iso()
    note = args.note or "incremental save"
    result.setdefault("change_log", []).append({"at": at, "action": "checkpoint", "note": note})
    rt = result.setdefault("runtime", {})
    rt["last_saved_at"] = at
    rt.setdefault("checkpoints", []).append({"at": at, "status": result.get("status"), "note": note})
    if args.requests is not None:
        try:
            result.setdefault("budget", {}).setdefault("used_model_requests", int(args.requests))
        except ValueError:
            print("warn: --requests must be an integer, ignored", file=sys.stderr)
    if args.status:
        result["status"] = args.status
    patentlib.write_json(path, result)
    ok, errors, warnings = patentlib.validate_result(result, result.get("contract_fields"))
    print("checkpoint: wrote %s (valid=%s, status=%s)" % (path, ok, result.get("status")))
    for e in errors:
        print("  error: %s" % e)
    for w in warnings:
        print("  warn: %s" % w)


def cmd_budget(args):
    result, _path, _err = _load_result(args)
    started = None
    budget = {}
    if isinstance(result, dict):
        started = result.get("runtime", {}).get("started_at")
        budget = result.get("budget", {})
    if started is None:
        state, _e = patentlib.load_json(os.path.join(args.work, "state.json"))
        if isinstance(state, dict):
            started = state.get("started_at")
    elapsed = patentlib.elapsed_since(started)
    total = budget.get("total_seconds", 900)
    limit = budget.get("model_requests_limit", 64)
    used = budget.get("used_model_requests", 0)
    early = budget.get("early_save_seconds", 180)

    print("elapsed=%.1fs / %ds total (%.0f%%); model_requests=%s / %s"
          % (elapsed, total, 100.0 * elapsed / max(1, total), used, limit))
    if elapsed < early:
        print("[guard] within first %ds: partial result.json MUST already exist" % early)
    else:
        print("[guard] early-save window passed; verify result.json is valid before continuing")
    print("phase | window(s) | <=req | must_save | deliverable")
    for p in budget.get("phases", patentlib.PHASE_BUDGET):
        mark = " *" if (p.get("start_s") or 0) <= elapsed < (p.get("end_s") or 0) else ""
        print("  %-12s %4d-%4d  <=%-3d  %-5s  %s%s"
              % (p.get("name", ""), p.get("start_s", 0), p.get("end_s", 0),
                 p.get("max_requests", 0), "yes" if p.get("must_save") else "no",
                 p.get("deliverable", ""), mark))
    if elapsed > total:
        print("[guard] OVER BUDGET: finalize immediately, keep status honest (partial)")


# ---------------------------------------------------------------------------
# finalize / validate
# ---------------------------------------------------------------------------
def cmd_finalize(args):
    result, path, err = _load_result(args)
    if err:
        print("finalize: %s" % err, file=sys.stderr)
        sys.exit(0)
    if args.status:
        result["status"] = args.status
    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or patentlib.now_iso()
    finished = patentlib.now_iso()
    rt["started_at"] = started
    rt["finished_at"] = finished
    rt["elapsed_seconds"] = round(patentlib.elapsed_since(started), 1)
    result.setdefault("change_log", []).append({
        "at": finished, "action": "finalize", "note": "finalization; status=%s" % result.get("status"),
    })
    patentlib.write_json(path, result)

    ok, errors, warnings = patentlib.validate_result(result, result.get("contract_fields"))
    report_path = os.path.join(args.output, "report.md")
    patentlib.write_report(report_path, result)

    print("finalize: status=%s valid=%s report=%s" % (result.get("status"), ok, report_path))
    for e in errors:
        print("  error: %s" % e)
    for w in warnings:
        print("  warn: %s" % w)


def cmd_validate(args):
    result, _path, err = _load_result(args)
    if err:
        print("INVALID: %s" % err)
        sys.exit(1)
    contract_fields = result.get("contract_fields")
    ok, errors, warnings = patentlib.validate_result(result, contract_fields)
    if ok:
        print("VALID (case_id=%s, status=%s)" % (result.get("case_id"), result.get("status")))
        for w in warnings:
            print("  warn: %s" % w)
        sys.exit(0)
    else:
        print("INVALID")
        for e in errors:
            print("  error: %s" % e)
        for w in warnings:
            print("  warn: %s" % w)
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="专利与技术文献检索 Skill 的命令行入口（纯标准库，确定性）")

    # 公共参数：既可在子命令前，也可在子命令后出现。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=_env_default("INPUT_DIR", "/input"),
                        help="输入目录（默认 /input，可用 INPUT_DIR 覆盖）")
    common.add_argument("--output", default=_env_default("OUTPUT_DIR", "/app/output"),
                        help="输出目录（默认 /app/output，可用 OUTPUT_DIR 覆盖）")
    common.add_argument("--work", default=_env_default("WORK_DIR", "/app/work"),
                        help="工作目录（默认 /app/work，可用 WORK_DIR 覆盖）")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scaffold", parents=[common], help="发现输入并落盘 partial 骨架")
    sp.set_defaults(func=cmd_scaffold)

    sp = sub.add_parser("normalize", parents=[common], help="规范化一个或多个专利号")
    sp.add_argument("--text", default="", help="单个专利号字符串")
    sp.add_argument("--numbers", nargs="*", default=None, help="多个专利号（空格分隔）")
    sp.add_argument("--file", default=None, help="读取 JSON 文件中的号码列表")
    sp.set_defaults(func=cmd_normalize)

    sp = sub.add_parser("extract", parents=[common], help="从文本/文件抽取专利号候选")
    sp.add_argument("--text", default="", help="待抽取文本")
    sp.add_argument("--file", default=None, help="待抽取文本文件")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("family", parents=[common], help="按共用申请号/优先权号归并同族")
    sp.add_argument("--file", required=True, help="records JSON 文件路径")
    sp.set_defaults(func=cmd_family)

    sp = sub.add_parser("checkpoint", parents=[common], help="增量落盘 + 记录 check 点 + 校验")
    sp.add_argument("--note", default=None, help="checkpoint 说明")
    sp.add_argument("--status", default=None, help="覆盖 status")
    sp.add_argument("--requests", default=None, help="更新 used_model_requests")
    sp.set_defaults(func=cmd_checkpoint)

    sp = sub.add_parser("budget", parents=[common], help="打印剩余时间/请求预算与阶段护栏")
    sp.set_defaults(func=cmd_budget)

    sp = sub.add_parser("finalize", parents=[common], help="校验 + 补 runtime + 生成 report.md")
    sp.add_argument("--status", default=None, help="覆盖 status")
    sp.set_defaults(func=cmd_finalize)

    sp = sub.add_parser("validate", parents=[common], help="校验 result.json，打印 VALID/INVALID")
    sp.set_defaults(func=cmd_validate)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
