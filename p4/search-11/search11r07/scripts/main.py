#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利检索 Skill 的命令行入口（薄封装，逻辑在 patentlib.py）。

子命令：
  scaffold  发现输入、读 case.json、回填 output_contract、落盘 partial 骨架
  normalize 规范化一个或多个专利号（--text 或 --numbers 或 --file）
  extract   从文本/文件抽取专利号候选
  family    读 records JSON，按共用申请号/优先权号/公开号归并同族
  finalize  校验 result.json、补 runtime、生成 report.md
  validate  校验 result.json，打印 VALID / INVALID

默认路径：--input /input --output /app/output --work /app/work；
可用环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 覆盖，或传对应命令行参数。
所有子命令在降级场景（空输入/坏 JSON/缺文件）都退出码 0，不崩溃。
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import patentlib  # noqa: E402


def _env(name, fallback):
    return os.environ.get(name) or fallback


def _read_json_arg(path):
    if not path or not os.path.exists(path):
        return None
    obj, err = patentlib.load_json(path)
    if err:
        print("warn: cannot read JSON %s: %s" % (path, err.get("error")), file=sys.stderr)
        return None
    return obj


def _load_result(output):
    path = os.path.join(output, "result.json")
    if not os.path.exists(path):
        return None, path, "result.json not found"
    result, err = patentlib.load_json(path)
    if err:
        return None, path, err.get("error")
    if not isinstance(result, dict):
        return None, path, "result.json is not an object"
    return result, path, None


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
            if case_err:
                print("warn: case.json unreadable: %s" % case_err.get("error"), file=sys.stderr)
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
        if f["kind"] in ("pdf", "image", "office"):
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"],
                              "note": "binary; read via platform tools"})
        elif f["kind"] in ("text", "table", "json", "case"):
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

    out_path = os.path.join(args.output, "result.json")
    ok, werr = patentlib.safe_write_json(out_path, result)
    if not ok:
        print("scaffold: write failed: %s" % werr.get("reason"), file=sys.stderr)
        print("scaffold: wrote %s (status=%s, files=%d)"
              % (out_path, result["status"], len(files)))
        return

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract": contract,
        "started_at": result["runtime"]["started_at"],
    }
    patentlib.safe_write_json(os.path.join(args.work, "state.json"), state)

    print("scaffold: wrote %s (status=%s, files=%d)"
          % (out_path, result["status"], len(files)))


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
            print(json.dumps({"hits": []}, ensure_ascii=False, indent=2))
            return
    else:
        text = args.text
    hits = patentlib.extract_numbers(text)
    print(json.dumps({"hits": hits, "count": len(hits)}, ensure_ascii=False, indent=2))


def cmd_family(args):
    records = _read_json_arg(args.file) or []
    if not isinstance(records, list):
        records = [records]
    groups = patentlib.group_families(records)
    print(json.dumps(groups, ensure_ascii=False, indent=2))


def cmd_finalize(args):
    result, path, err = _load_result(args.output)
    if err:
        print("finalize: %s" % err, file=sys.stderr)
        return
    result = patentlib.finalize_result(result, args.status)
    patentlib.safe_write_json(path, result)

    report = build_report(result)
    rpath = os.path.join(args.output, "report.md")
    try:
        with open(rpath, "w", encoding="utf-8") as fh:
            fh.write(report)
    except OSError as e:
        print("finalize: report.md write failed: %s" % e, file=sys.stderr)
    print("finalize: %s -> %s (status=%s)" % (path, rpath, result["status"]))


def build_report(result):
    """由 result.json 生成可读 report.md（只汇总，不新增结论）。"""
    lines = []
    lines.append("# 专利检索结果报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id", "unknown"))
    lines.append("- status: `%s`" % result.get("status", "unknown"))
    lines.append("- research_question: %s" % (result.get("research_question") or "未明确"))
    lines.append("- retrieval_scope: %s" % (result.get("retrieval_scope") or "未明确"))
    lines.append("")
    lines.append("## 专利记录（%d）" % len(result.get("patent_records", []) or []))
    for r in result.get("patent_records", []) or []:
        if isinstance(r, dict):
            rid = r.get("record_id") or "?"
            num = r.get("publication_number") or r.get("application_number") or "?"
            lines.append("- %s: %s" % (rid, num))
    lines.append("")
    lines.append("## 同族（%d）" % len(result.get("families", []) or []))
    for fam in result.get("families", []) or []:
        if isinstance(fam, dict):
            lines.append("- %s [%s]: %d 成员" % (
                fam.get("family_id") or "?", fam.get("family_type") or "?",
                len(fam.get("members", []) or [])))
    lines.append("")
    lines.append("## 法律状态（%d）" % len(result.get("legal_status", []) or []))
    for s in result.get("legal_status", []) or []:
        if isinstance(s, dict):
            lines.append("- %s: %s（as_of %s）" % (
                s.get("record_id") or "?", s.get("status") or "unknown",
                s.get("as_of") or "?"))
    lines.append("")
    lines.append("## 技术文献（%d）" % len(result.get("literature", []) or []))
    for lit in result.get("literature", []) or []:
        if isinstance(lit, dict):
            lines.append("- %s (%s)" % (lit.get("title") or "?", lit.get("year") or "?"))
    lines.append("")
    lines.append("## 来源记录（%d）" % len(result.get("sources", []) or []))
    for s in result.get("sources", []) or []:
        if isinstance(s, dict):
            lines.append("- [%s] %s" % (s.get("engine") or "?", s.get("query") or "?"))
    lines.append("")
    lines.append("## 不确定性 / 待人工复核")
    lines.append("")
    for u in result.get("uncertainty", []) or []:
        if isinstance(u, dict):
            lines.append("- uncertainty: %s" % (u.get("item") or "?"))
    for p in result.get("pending_human", []) or []:
        if isinstance(p, dict):
            lines.append("- pending_human: %s" % (p.get("item") or "?"))
    for lim in result.get("limitations", []) or []:
        if isinstance(lim, dict):
            lines.append("- limitation: %s" % (lim.get("item") or "?"))
    lines.append("")
    lines.append("> 本报告由 result.json 机械汇总生成，逐条结论与证据以 result.json 为准。")
    return "\n".join(lines) + "\n"


def cmd_validate(args):
    result, _path, err = _load_result(args.output)
    if err:
        print("INVALID: %s" % err)
        return
    valid, issues = patentlib.validate_result(result)
    if valid:
        print("VALID")
    else:
        print("INVALID")
        for i in issues:
            print("  - %s" % i)


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="专利检索 Skill 命令行入口（容错优先，降级不崩溃）",
        allow_abbrev=False,
    )
    sub = p.add_subparsers(dest="command")

    # 三个路径参数同时作用于所有子命令（供 scaffold/finalize/validate 使用），
    # 缺省值：环境变量覆盖 → /input、/app/output、/app/work。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=_env("INPUT_DIR", "/input"), help="输入目录")
    common.add_argument("--output", default=_env("OUTPUT_DIR", "/app/output"), help="输出目录")
    common.add_argument("--work", default=_env("WORK_DIR", "/app/work"), help="工作目录")

    sp = sub.add_parser("scaffold", parents=[common], help="发现输入并落盘 partial 骨架")
    sp.set_defaults(func=cmd_scaffold)

    np = sub.add_parser("normalize", parents=[common], help="规范化专利号")
    np.add_argument("--text", default="", help="单个号码字符串")
    np.add_argument("--numbers", nargs="*", help="多个号码（空格分隔）")
    np.add_argument("--file", default=None, help="读 JSON 数组/对象中的号码字段")
    np.set_defaults(func=cmd_normalize)

    ep = sub.add_parser("extract", parents=[common], help="从文本/文件抽取专利号候选")
    ep.add_argument("--text", default="", help="文本")
    ep.add_argument("--file", default=None, help="文本文件路径")
    ep.set_defaults(func=cmd_extract)

    fp = sub.add_parser("family", parents=[common], help="按共用号码归并同族")
    fp.add_argument("--file", required=True, help="records JSON 文件路径")
    fp.set_defaults(func=cmd_family)

    zp = sub.add_parser("finalize", parents=[common], help="校验并收尾 result.json")
    zp.add_argument("--status", default="ok",
                    choices=list(patentlib.STATUS_VALUES), help="最终 status")
    zp.set_defaults(func=cmd_finalize)

    vp = sub.add_parser("validate", parents=[common], help="校验 result.json 并打印 VALID/INVALID")
    vp.set_defaults(func=cmd_validate)

    return p


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # --help/-h 已打印并退出 0；参数错误时退出 2，这里转成优雅退出 0。
        return 0 if e.code in (0, None) else 0
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        args.func(args)
    except Exception as e:  # 兜底：任何子命令异常都不崩溃
        print("error: %s: %s" % (args.command, e), file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
