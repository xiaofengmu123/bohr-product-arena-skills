#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口。

子命令：
  scaffold   发现输入、回填 output_contract、落盘 partial 骨架 + work/state.json
  normalize  规范化一个或多个专利号（--text / --numbers / --file）
  extract    从文本/文件抽取专利号候选
  family     读 records JSON，按共用申请号/优先权号归并同族（observed，非穷尽）
  mirror     只读展示 output_contract 的字段镜像与对齐清单
  finalize   校验契约、补 runtime、刷新 contract_coverage、生成 report.md
  validate   校验 result.json（契约对齐 + 字段合法性 + change_log），打印 VALID/INVALID

默认路径：--input /input --output /app/output --work /app/work
（可用环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 覆盖默认值，亦可传参覆盖。）

纯标准库实现；同一输入重复运行，分析字段结果一致（确定性排序）。
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import core_impl as ci  # noqa: E402


def _env(name, fallback):
    return os.environ.get(name) or fallback


def _read_json(path):
    if not path or not os.path.exists(path):
        return None
    obj, err = ci.load_json(path)
    if err:
        print("warn: cannot read JSON %s: %s" % (path, err.get("error")), file=sys.stderr)
        return None
    return obj


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------
def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    files = ci.discover_input(args.input)
    case_obj = None
    case_path = None
    case_err = None
    for f in files:
        if f["kind"] == "case":
            case_path = f["path"]
            case_obj, case_err = ci.load_json(f["path"])
            break

    contract = None
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract") or case_obj.get("outputContract")

    result = ci.build_skeleton(case_obj, contract)
    if case_path is not None and not isinstance(case_obj, dict):
        result["errors"].append({
            "item": "case_json_unreadable",
            "reason": (case_err or {}).get("error") if case_err else "case.json 不是对象",
        })
        result["degradations"].append({
            "item": "case_json_fallback",
            "reason": "proceeded with case_id=unknown and default schema",
        })

    inventory = []
    for f in files:
        if f["kind"] in ("pdf", "image"):
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"],
                              "note": "binary; read via platform tools"})
        elif f["kind"] in ("text", "table"):
            text, enc, err = ci.read_text(f["path"])
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"],
                              "encoding": enc, "chars": len(text),
                              "read_error": err})
        else:
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"]})

    result["input_inventory"] = inventory
    if not files:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "no_input_files",
            "reason": "input directory has no discoverable files",
        })

    out_path = os.path.join(args.output, "result.json")
    _write_json(out_path, result)

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract": contract,
        "started_at": result["runtime"]["started_at"],
    }
    _write_json(os.path.join(args.work, "state.json"), state)

    print("scaffold: wrote %s (status=%s, files=%d, contract_fields=%d)"
          % (out_path, result["status"], len(files),
             result["contract_coverage"].get("total_fields", 0)))


# ---------------------------------------------------------------------------
# normalize / extract / family / mirror
# ---------------------------------------------------------------------------
def cmd_normalize(args):
    if args.file:
        data = _read_json(args.file) or []
        items = data if isinstance(data, list) else [data]
        outputs = [ci.normalize_patent_number(x) for x in items]
    elif args.numbers:
        outputs = [ci.normalize_patent_number(x) for x in args.numbers]
    else:
        outputs = [ci.normalize_patent_number(args.text)]
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


def cmd_extract(args):
    if args.file:
        text, _enc, err = ci.read_text(args.file)
        if err and not text:
            print(json.dumps({"error": err}, ensure_ascii=False), file=sys.stderr)
            return
    else:
        text = args.text or ""
    hits = ci.extract_numbers(text)
    print(json.dumps(hits, ensure_ascii=False, indent=2))


def cmd_family(args):
    records = _read_json(args.file) or []
    if not isinstance(records, list):
        records = [records]
    groups = ci.group_families(records)
    print(json.dumps({"families": groups, "observed": True, "exhaustive": False},
                     ensure_ascii=False, indent=2))


def cmd_mirror(args):
    data = _read_json(args.file)
    if not isinstance(data, dict):
        print(json.dumps({"error": "file must be a JSON object"}, ensure_ascii=False))
        return
    contract = data.get("output_contract") or data.get("outputContract")
    if not isinstance(contract, dict):
        print(json.dumps({"contract": None, "note": "no output_contract in file"},
                         ensure_ascii=False))
        return
    print(json.dumps({
        "mirror": ci.resolve_default(contract),
        "fields": ci.flatten_contract(contract),
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# finalize / validate
# ---------------------------------------------------------------------------
def _load_result(args):
    path = os.path.join(args.output, "result.json")
    if not os.path.exists(path):
        return None, path, "result.json not found"
    result, err = ci.load_json(path)
    if err:
        return None, path, err.get("error")
    if not isinstance(result, dict):
        return None, path, "result.json is not an object"
    return result, path, None


def cmd_finalize(args):
    result, path, err = _load_result(args)
    if err:
        print("finalize: %s" % err, file=sys.stderr)
        return
    if args.status:
        result["status"] = args.status
    # 恢复契约用于对齐（scaffold 时存入 work/state.json）
    contract = None
    state_path = os.path.join(args.work, "state.json")
    state = _read_json(state_path)
    if isinstance(state, dict):
        contract = state.get("contract")
    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or ci.now_iso()
    rt["started_at"] = started
    rt["finished_at"] = ci.now_iso()
    rt["elapsed_seconds"] = ci.elapsed_seconds(started, rt["finished_at"])
    if isinstance(contract, dict):
        ci.refresh_contract_coverage(result, contract)
    if not result.get("change_log") or not any(
            e.get("action") == "finalize" for e in result.get("change_log")):
        ci.record_change(result, "finalize", "finalized runtime and coverage",
                         status_after=result.get("status"))
    _write_json(path, result)

    report = ci.render_report(result)
    with open(os.path.join(args.output, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    print("finalize: updated %s (status=%s, elapsed=%ss), wrote report.md"
          % (path, result["status"], rt["elapsed_seconds"]))


def cmd_validate(args):
    result, path, err = _load_result(args)
    if err:
        print("INVALID: %s" % err)
        sys.exit(1)
    contract = None
    if args.contract:
        data = _read_json(args.contract)
        if isinstance(data, dict):
            contract = data.get("output_contract") or data.get("outputContract")
    else:
        state = _read_json(os.path.join(args.work, "state.json"))
        if isinstance(state, dict):
            contract = state.get("contract")
    ok, issues = ci.validate_result(result, contract)
    for i in issues:
        print("[%s] %s: %s" % (i["level"], i["item"], i["detail"]))
    if ok:
        print("VALID")
        sys.exit(0)
    print("INVALID")
    sys.exit(1)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=_env("INPUT_DIR", "/input"))
    common.add_argument("--output", default=_env("OUTPUT_DIR", "/app/output"))
    common.add_argument("--work", default=_env("WORK_DIR", "/app/work"))

    p = argparse.ArgumentParser(
        prog="main.py", parents=[common],
        description="专利与技术文献检索 Skill 命令行工具（纯标准库）")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("scaffold", parents=[common],
                        help="发现输入并落盘 partial 骨架")
    sp.set_defaults(func=cmd_scaffold)

    sp = sub.add_parser("normalize", parents=[common], help="规范化专利号")
    sp.add_argument("--text", default=None)
    sp.add_argument("--numbers", nargs="*", default=None)
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=cmd_normalize)

    sp = sub.add_parser("extract", parents=[common], help="从文本抽取专利号候选")
    sp.add_argument("--text", default=None)
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("family", parents=[common],
                        help="按共用申请号/优先权号归并同族")
    sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_family)

    sp = sub.add_parser("mirror", parents=[common],
                        help="展示 output_contract 镜像与对齐清单")
    sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_mirror)

    sp = sub.add_parser("finalize", parents=[common],
                        help="校验并补 runtime / coverage / report.md")
    sp.add_argument("--status", default=None,
                    choices=["ok", "partial", "insufficient_evidence", "input_error"])
    sp.set_defaults(func=cmd_finalize)

    sp = sub.add_parser("validate", parents=[common],
                        help="校验 result.json，打印 VALID/INVALID")
    sp.add_argument("--contract", default=None, help="含 output_contract 的 JSON 文件")
    sp.set_defaults(func=cmd_validate)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
