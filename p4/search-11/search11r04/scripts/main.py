#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口。

子命令：
  scaffold  发现输入、读 case.json、回填 output_contract、落盘 partial 骨架
  normalize 规范化一个或多个专利号（--text / --numbers / --file）
  extract   从文本或文件抽取专利号候选（--text / --file）
  family    读 records JSON，按共用申请号/优先权号归并同族（--file）
  finalize  校验 result.json、补 runtime、生成 report.md（--status 可选覆盖）
  validate  校验 result.json，打印 VALID / INVALID

默认路径：--input /input --output /app/output --work /app/work
（环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 可覆盖默认值。）
纯标准库实现；同一输入重复运行，规范化与归并结果一致。
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
        print("warn: 无法读取 JSON %s: %s" % (path, err.get("error")),
              file=sys.stderr)
        return None
    return obj


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------

def cmd_scaffold(args):
    patentlib.os.makedirs(args.output, exist_ok=True)
    patentlib.os.makedirs(args.work, exist_ok=True)

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

    if case_path is not None and (case_err or not isinstance(case_obj, dict)):
        reason = case_err.get("error") if case_err else "case.json 不是 JSON 对象"
        result["errors"].append({"item": "case_json_unreadable", "reason": reason})
        result["degradations"].append({
            "item": "case_json_fallback",
            "reason": "以 case_id=unknown 与默认 schema 继续",
        })
        if result.get("status") not in ("insufficient_evidence", "input_error"):
            result["status"] = "partial"

    inventory = []
    for f in files:
        if f["kind"] in ("pdf", "image"):
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"],
                              "note": "二进制；经平台解析/OCR 工具读取文本层"})
        elif f["kind"] in ("text", "table", "json"):
            text, enc, err = patentlib.read_text(f["path"])
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"],
                              "encoding": enc, "chars": len(text),
                              "read_error": err})
        else:
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"]})

    result.setdefault("input_inventory", inventory)
    if not files:
        result["status"] = "insufficient_evidence"
        patentlib.add_uncertainty(
            result, "no_input_files", "field_missing",
            "输入目录无可发现文件", impact="无法执行检索", mitigation="等待平台提供材料")
        result["change_log"].append({
            "at": patentlib.now_iso(), "event": "no_input",
            "detail": "空输入目录", "status": result["status"]})

    out_path = os.path.join(args.output, "result.json")
    patentlib.write_json(out_path, result)

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract": contract,
        "started_at": result["runtime"]["started_at"],
    }
    patentlib.write_json(os.path.join(args.work, "state.json"), state)

    print("scaffold: wrote %s (status=%s, files=%d)"
          % (out_path, result["status"], len(files)))


# ---------------------------------------------------------------------------
# normalize / extract / family
# ---------------------------------------------------------------------------

def cmd_normalize(args):
    if args.file:
        data = _read_json_arg(args.file)
        if data is None:
            print("normalize: 无法读取文件，退出（不编造结果）", file=sys.stderr)
            sys.exit(0)
        items = data if isinstance(data, list) else [data]
        outputs = [patentlib.normalize_patent_number(x) for x in items]
    elif args.numbers:
        outputs = [patentlib.normalize_patent_number(x) for x in args.numbers]
    elif args.text:
        outputs = [patentlib.normalize_patent_number(args.text)]
    else:
        print("normalize: 需要 --text / --numbers / --file 之一", file=sys.stderr)
        sys.exit(0)
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


def cmd_extract(args):
    if args.file:
        text, _enc, err = patentlib.read_text(args.file)
        if err and not text:
            print(json.dumps({"error": err}, ensure_ascii=False), file=sys.stderr)
            sys.exit(0)
    else:
        text = args.text or ""
    hits = patentlib.extract_numbers(text)
    print(json.dumps({"count": len(hits), "hits": hits},
                     ensure_ascii=False, indent=2))


def cmd_family(args):
    records = _read_json_arg(args.file)
    if records is None:
        print("family: 无法读取记录文件，退出（不编造同族）", file=sys.stderr)
        sys.exit(0)
    if not isinstance(records, list):
        records = [records]
    result = patentlib.group_families(records)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# finalize / validate
# ---------------------------------------------------------------------------

def _load_result(args):
    path = os.path.join(args.output, "result.json")
    if not os.path.exists(path):
        return None, path, "result.json 不存在（先运行 scaffold）"
    result, err = patentlib.load_json(path)
    if err:
        return None, path, err.get("error")
    if not isinstance(result, dict):
        return None, path, "result.json 不是 JSON 对象"
    return result, path, None


def cmd_finalize(args):
    result, path, err = _load_result(args)
    if err:
        print("finalize: %s" % err, file=sys.stderr)
        sys.exit(0)

    if args.status:
        result["status"] = args.status

    for key in ("sources", "uncertainty", "pending_human", "errors",
                "degradations", "limitations", "change_log", "evidence"):
        result.setdefault(key, [])
    result.setdefault("degradation_tier", "green")

    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or patentlib.now_iso()
    rt["started_at"] = started
    rt["finished_at"] = patentlib.now_iso()
    try:
        from datetime import datetime
        s0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        s1 = datetime.fromisoformat(str(rt["finished_at"]).replace("Z", "+00:00"))
        rt["elapsed_seconds"] = round(max(0.0, (s1 - s0).total_seconds()), 1)
    except ValueError:
        rt["elapsed_seconds"] = None

    result["change_log"].append({
        "at": patentlib.now_iso(), "event": "finalize",
        "detail": "校验并生成 report.md", "status": result["status"]})

    errs = patentlib.validate_result(result)
    if errs:
        result["errors"].extend(
            {"item": "validate:" + e, "reason": "finalize 校验失败"} for e in errs)
        if result["status"] == "ok":
            result["status"] = "partial"

    patentlib.write_json(path, result)
    report_path = os.path.join(args.output, "report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(patentlib.build_report(result))

    print("finalize: status=%s errors=%d; wrote %s"
          % (result["status"], len(errs), report_path))


def cmd_validate(args):
    result, path, err = _load_result(args)
    if err:
        print("INVALID: %s" % err)
        sys.exit(0)
    errs = patentlib.validate_result(result)
    if errs:
        print("INVALID:")
        for e in errs:
            print("  - %s" % e)
    else:
        print("VALID")
    sys.exit(0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="专利与技术文献检索 Skill 工具库",
    )
    sub = p.add_subparsers(dest="cmd")

    def add_common(sp):
        sp.add_argument("--input", default=_env_default("INPUT_DIR", "/input"))
        sp.add_argument("--output", default=_env_default("OUTPUT_DIR", "/app/output"))
        sp.add_argument("--work", default=_env_default("WORK_DIR", "/app/work"))
        return sp

    add_common(sub.add_parser("scaffold", help="发现输入并落盘 partial 骨架"))

    n = add_common(sub.add_parser("normalize", help="规范化专利号"))
    n.add_argument("--text")
    n.add_argument("--numbers", nargs="*")
    n.add_argument("--file")

    e = add_common(sub.add_parser("extract", help="抽取专利号候选"))
    e.add_argument("--text")
    e.add_argument("--file")

    f = add_common(sub.add_parser("family", help="按共用号码归并同族"))
    f.add_argument("--file", required=True)

    fin = add_common(sub.add_parser("finalize",
                                    help="校验 + 补 runtime + 生成 report.md"))
    fin.add_argument("--status", choices=sorted(patentlib.STATUSES))

    add_common(sub.add_parser("validate", help="校验 result.json"))
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "scaffold": cmd_scaffold,
        "normalize": cmd_normalize,
        "extract": cmd_extract,
        "family": cmd_family,
        "finalize": cmd_finalize,
        "validate": cmd_validate,
    }
    fn = handlers.get(args.cmd)
    if fn is None:
        parser.print_help()
        sys.exit(0)
    fn(args)


if __name__ == "__main__":
    main()
