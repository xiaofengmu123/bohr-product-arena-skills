#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口（纯标准库）。

子命令：
  scaffold  发现输入、读 case.json、回填 output_contract、落盘 partial 骨架
  normalize 规范化一个或多个专利号（--text / --numbers / --file）
  extract   从文本/文件抽取专利号候选（--text / --file）
  family    读 records JSON，按共用申请号/优先权号归并同族（observed）
  evidence  对 result.json 做证据溯源 reconcile（互建 evidence_map/evidence）
  finalize  校验契约、补 runtime、生成 report.md、打印 VALID/INVALID
  validate  校验 result.json，打印 VALID / INVALID

默认路径：--input /input --output /app/output --work /app/work
（环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 可覆盖默认值，亦可传参覆盖。）

纯标准库实现；分析字段在同一输入下重复运行结果一致（runtime 时间戳除外）。
"""

import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import patentlib  # noqa: E402


def _env_default(name, fallback):
    return os.environ.get(name) or fallback


def _print_json(obj):
    try:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(obj, ensure_ascii=True, indent=2))


def _read_json_arg(path):
    if not path or not os.path.exists(path):
        return None, "file_not_found"
    obj, err = patentlib.load_json(path)
    if err:
        print("warn: cannot read JSON %s: %s" % (path, err["reason"]),
              file=sys.stderr)
        return None, err["error"]
    return obj, None


def _load_result(output):
    path = os.path.join(output, "result.json")
    if not os.path.exists(path):
        return None, path, "result.json not found"
    result, err = patentlib.load_json(path)
    if err:
        return None, path, err["reason"]
    if not isinstance(result, dict):
        return None, path, "result.json is not an object"
    return result, path, None


def _load_contract(work):
    state_path = os.path.join(work, "state.json")
    state, err = patentlib.load_json(state_path)
    if err or not isinstance(state, dict):
        return None
    return state.get("contract")


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def cmd_scaffold(args):
    patentlib.os.makedirs(args.output, exist_ok=True)
    patentlib.os.makedirs(args.work, exist_ok=True)

    files = patentlib.discover_input(args.input)
    case_obj, case_path = None, None
    for f in files:
        if f["kind"] == "case":
            case_path = f["path"]
            case_obj, err = patentlib.load_json(f["path"])
            if err:
                print("warn: case.json unreadable: %s" % err["reason"],
                      file=sys.stderr)
            break

    contract = None
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract") or case_obj.get("outputContract")

    result = patentlib.build_skeleton(case_obj, contract)
    if case_path is not None and not isinstance(case_obj, dict):
        result["errors"].append({
            "item": "case_json_unreadable",
            "reason": "case.json is not a JSON object; proceeded with default schema",
        })
        result["degradations"].append({
            "item": "case_json_fallback",
            "reason": "used case_id=unknown and default schema",
        })

    inventory = []
    for f in files:
        if f["kind"] in ("pdf", "image"):
            inventory.append({"rel": f["rel"], "kind": f["kind"],
                              "size": f["size"],
                              "note": "binary; read via platform tools"})
        elif f["kind"] in ("text", "table"):
            text, enc, rerr = patentlib.read_text(f["path"])
            inventory.append({"rel": f["rel"], "kind": f["kind"],
                              "size": f["size"], "encoding": enc,
                              "chars": len(text), "read_error": rerr})
        else:
            inventory.append({"rel": f["rel"], "kind": f["kind"],
                              "size": f["size"]})
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

    print("scaffold: wrote %s (status=%s, files=%d)"
          % (out_path, result["status"], len(files)))


def cmd_normalize(args):
    if args.file:
        data, err = _read_json_arg(args.file)
        if data is None:
            print("warn: normalize file unreadable (%s)" % err, file=sys.stderr)
            data = []
        items = data if isinstance(data, list) else [data]
        outputs = []
        for x in items:
            if isinstance(x, dict):
                raw = x.get("raw") or x.get("number") or x.get("text")
                rec = dict(x)
                rec["normalized"] = patentlib.normalize_patent_number(raw)
                outputs.append(rec)
            else:
                outputs.append(patentlib.normalize_patent_number(x))
    elif args.numbers:
        outputs = [patentlib.normalize_patent_number(x) for x in args.numbers]
    else:
        outputs = [patentlib.normalize_patent_number(args.text)]
    _print_json(outputs)


def cmd_extract(args):
    if args.file:
        text, _enc, err = patentlib.read_text(args.file)
        if err and not text:
            print(json.dumps({"error": err}, ensure_ascii=True), file=sys.stderr)
            sys.exit(0)
    else:
        text = args.text
    _print_json(patentlib.extract_numbers(text))


def cmd_family(args):
    records, err = _read_json_arg(args.file)
    if records is None:
        print("warn: family file unreadable (%s); no records" % err, file=sys.stderr)
        records = []
    if not isinstance(records, list):
        records = [records]
    _print_json(patentlib.group_families(records))


def cmd_evidence(args):
    result, path, err = _load_result(args.output)
    if err:
        print("evidence: %s" % err, file=sys.stderr)
        sys.exit(0)
    result = patentlib.reconcile_evidence(result)
    _write_json(path, result)
    print("evidence: reconciled %d evidence entries into %s"
          % (len(result.get("evidence") or []), path))


def cmd_finalize(args):
    result, path, err = _load_result(args.output)
    if err:
        print("finalize: %s" % err, file=sys.stderr)
        sys.exit(0)

    result = patentlib.reconcile_evidence(result)

    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or patentlib.now_iso()
    rt["started_at"] = started
    rt["finished_at"] = patentlib.now_iso()
    try:
        s0 = patentlib.datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        s1 = patentlib.datetime.fromisoformat(str(rt["finished_at"]).replace("Z", "+00:00"))
        rt["elapsed_seconds"] = round(max(0.0, (s1 - s0).total_seconds()), 1)
    except Exception:
        rt["elapsed_seconds"] = None

    if args.status:
        if args.status in patentlib.DEFAULT_STATUS:
            result["status"] = args.status
        else:
            print("warn: invalid --status %r; keeping %s"
                  % (args.status, result.get("status")), file=sys.stderr)

    result["change_log"].append({
        "ts": patentlib.now_iso(),
        "action": "finalize",
        "detail": "evidence reconciled, runtime filled, report generated",
    })

    contract = _load_contract(args.work)
    problems = patentlib.validate_result(result, contract)
    _write_json(path, result)

    report = patentlib.render_report(result)
    report_path = os.path.join(args.output, "report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    result["artifacts"] = [a for a in result.get("artifacts", [])
                           if not (isinstance(a, dict) and a.get("path") == "report.md")]
    result["artifacts"].append({"path": "report.md", "note": "human-readable summary"})
    _write_json(path, result)

    if problems:
        print("finalize: INVALID (%d problem(s))" % len(problems))
        for p in problems:
            print("  - %s" % p)
    else:
        print("finalize: VALID")
        print("finalize: wrote %s and %s" % (path, report_path))


def cmd_validate(args):
    result, path, err = _load_result(args.output)
    if err:
        print("validate: %s" % err, file=sys.stderr)
        print("INVALID")
        sys.exit(0)
    contract = _load_contract(args.work)
    problems = patentlib.validate_result(result, contract)
    if problems:
        print("INVALID")
        for p in problems:
            print("  - %s" % p)
    else:
        print("VALID")
    print("validated: %s" % path)


def _resolve_paths(args):
    args.input = args.input or _env_default("INPUT_DIR", "/input")
    args.output = args.output or _env_default("OUTPUT_DIR", "/app/output")
    args.work = args.work or _env_default("WORK_DIR", "/app/work")
    return args


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=None)
    common.add_argument("--output", default=None)
    common.add_argument("--work", default=None)

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="专利与技术文献检索 Skill 命令行工具",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("scaffold", parents=[common],
                   help="discover input, write partial skeleton")
    sub.add_parser("validate", parents=[common],
                   help="validate result.json, print VALID/INVALID")

    p_norm = sub.add_parser("normalize", parents=[common],
                            help="normalize patent numbers")
    p_norm.add_argument("--text", default="")
    p_norm.add_argument("--numbers", nargs="*", default=None)
    p_norm.add_argument("--file", default=None)

    p_ext = sub.add_parser("extract", parents=[common],
                           help="extract patent-number candidates")
    p_ext.add_argument("--text", default="")
    p_ext.add_argument("--file", default=None)

    p_fam = sub.add_parser("family", parents=[common],
                           help="group families from records JSON")
    p_fam.add_argument("--file", required=True)

    sub.add_parser("evidence", parents=[common],
                   help="reconcile evidence_map/evidence in result.json")

    p_fin = sub.add_parser("finalize", parents=[common],
                           help="validate, fill runtime, write report.md")
    p_fin.add_argument("--status", default=None,
                       choices=patentlib.DEFAULT_STATUS)

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    args = _resolve_paths(args)

    {
        "scaffold": cmd_scaffold,
        "normalize": cmd_normalize,
        "extract": cmd_extract,
        "family": cmd_family,
        "evidence": cmd_evidence,
        "finalize": cmd_finalize,
        "validate": cmd_validate,
    }[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
