#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口（纯标准库）。

子命令：
  scaffold  发现输入、读 case.json、回填 output_contract、落盘 partial 骨架
  normalize 规范化一个或多个专利号（参数或 JSON 文件）
  extract   从文本/文件抽取专利号候选
  family    读 records JSON，按共用申请号/优先权号归并同族
  finalize  校验 result.json、补 runtime、生成 report.md
  validate  校验 result.json，打印 VALID / INVALID

默认路径：--input /input --output /app/output --work /app/work
（环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 可覆盖默认值。）
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import patentlib  # noqa: E402


def _default(name, fallback):
    return os.environ.get(name) or fallback


def _read_json(path):
    if not path or not os.path.exists(path):
        return None
    obj, err = patentlib.load_json(path)
    if err:
        print("warn: cannot read JSON %s: %s" % (path, err["error"]), file=sys.stderr)
        return None
    return obj


def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    files = patentlib.discover_input(args.input)
    case_obj, case_path, case_err = None, None, None
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

    inventory = []
    for f in files:
        item = {"rel": f["rel"], "kind": f["kind"], "size": f["size"]}
        if f["kind"] in ("text", "table"):
            text, enc, rerr = patentlib.read_text(f["path"])
            item.update({"encoding": enc, "chars": len(text), "read_error": rerr})
        elif f["kind"] in ("pdf", "image"):
            item["note"] = "binary; read via platform tools"
        inventory.append(item)

    result["input_inventory"] = inventory
    if not files:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "no_input_files",
            "reason": "input directory has no discoverable files",
        })
    result.setdefault("change_log", []).append({
        "step": "scaffold", "status": result["status"], "at": patentlib.now_iso(),
    })

    out_path = os.path.join(args.output, "result.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract": contract,
        "started_at": result["runtime"]["started_at"],
    }
    with open(os.path.join(args.work, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

    print("scaffold: wrote %s (status=%s, files=%d)" % (out_path, result["status"], len(files)))


def cmd_normalize(args):
    if args.file:
        data = _read_json(args.file) or []
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
            return
    else:
        text = args.text or ""
    print(json.dumps(patentlib.extract_numbers(text), ensure_ascii=False, indent=2))


def cmd_family(args):
    records = _read_json(args.file) or []
    if not isinstance(records, list):
        records = [records]
    print(json.dumps({"families": patentlib.group_families(records),
                      "observed": True, "exhaustive": False}, ensure_ascii=False, indent=2))


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


def _write_result(path, result):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)


def cmd_finalize(args):
    result, path, err = _load_result(args)
    if err:
        print("finalize: %s" % err, file=sys.stderr)
        return

    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or patentlib.now_iso()
    rt["started_at"] = started
    rt["finished_at"] = patentlib.now_iso()
    try:
        delta = patentlib._parse_iso(rt["finished_at"]) - patentlib._parse_iso(started)
        rt["elapsed_seconds"] = round(max(0.0, delta.total_seconds()), 1)
    except (TypeError, ValueError):
        pass
    if args.status:
        result["status"] = args.status
    result.setdefault("change_log", []).append({
        "step": "finalize", "status": result.get("status"), "at": patentlib.now_iso(),
    })
    _write_result(path, result)

    with open(os.path.join(args.output, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(patentlib.render_report(result))

    ok, issues = patentlib.validate_result(result)
    print("finalize: wrote %s, status=%s, valid=%s (%d issues)"
          % (path, result.get("status"), ok, len(issues)))


def cmd_validate(args):
    result, path, err = _load_result(args)
    if err:
        print("INVALID: %s" % err)
        sys.exit(1)
    ok, issues = patentlib.validate_result(result)
    if ok:
        print("VALID")
    else:
        print("INVALID")
        for issue in issues:
            print("  - %s" % issue)
        sys.exit(1)


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=_default("INPUT_DIR", "/input"))
    common.add_argument("--output", default=_default("OUTPUT_DIR", "/app/output"))
    common.add_argument("--work", default=_default("WORK_DIR", "/app/work"))

    parser = argparse.ArgumentParser(prog="main.py", parents=[common],
                                     description="专利与技术文献检索 Skill CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scaffold", parents=[common], help="发现输入、写 partial 骨架")

    n = sub.add_parser("normalize", parents=[common], help="规范化专利号")
    n.add_argument("--text", default="")
    n.add_argument("--file")
    n.add_argument("--numbers", nargs="+")

    e = sub.add_parser("extract", parents=[common], help="抽取专利号候选")
    e.add_argument("--text", default="")
    e.add_argument("--file")

    f = sub.add_parser("family", parents=[common], help="同族归并")
    f.add_argument("--file", required=True)

    fin = sub.add_parser("finalize", parents=[common], help="校验并生成 report.md")
    fin.add_argument("--status")

    sub.add_parser("validate", parents=[common], help="校验 result.json")

    args = parser.parse_args(argv)
    handlers = {
        "scaffold": cmd_scaffold, "normalize": cmd_normalize, "extract": cmd_extract,
        "family": cmd_family, "finalize": cmd_finalize, "validate": cmd_validate,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
