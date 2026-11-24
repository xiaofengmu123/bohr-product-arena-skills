#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口。

子命令：
  scaffold   发现输入、读 case.json、回填 output_contract、落盘 partial 骨架
  normalize  规范化一个或多个专利号（参数或 JSON 文件）
  extract    从文本/文件抽取专利号候选（含 OCR 粘连边界合并）
  family     读 records JSON，按共用申请号/优先权号归并同族
  finalize   校验 result.json、补 runtime、生成 report.md
  validate   校验 result.json，打印 VALID / INVALID

默认路径：--input /input --output /app/output --work /app/work
（可用环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 覆盖默认值。）

纯标准库实现；同一输入重复运行，分析字段结果一致。
"""

import argparse
import json
import os
import sys
from datetime import datetime

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
        print("warn: cannot read JSON %s: %s" % (path, err["error"]),
              file=sys.stderr)
        return None
    return obj


def _make_parser():
    # 公共路径参数作为父解析器，供各子命令继承，允许 `main.py scaffold --input ...`
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=_env_default("INPUT_DIR", "/input"))
    common.add_argument("--output", default=_env_default("OUTPUT_DIR", "/app/output"))
    common.add_argument("--work", default=_env_default("WORK_DIR", "/app/work"))

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="专利与技术文献检索 Skill 工具（纯标准库）",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scaffold", help="发现输入并落盘 partial 骨架",
                   parents=[common])

    p_norm = sub.add_parser("normalize", help="规范化专利号", parents=[common])
    p_norm.add_argument("--text", default=None, help="单个号码文本")
    p_norm.add_argument("--numbers", nargs="*", default=None,
                        help="多个号码文本")
    p_norm.add_argument("--file", default=None, help="号码列表 JSON 文件")

    p_ext = sub.add_parser("extract", help="从文本抽取专利号候选",
                           parents=[common])
    p_ext.add_argument("--text", default=None, help="直接给定文本")
    p_ext.add_argument("--file", default=None, help="从文件读取文本")

    p_fam = sub.add_parser("family", help="按共用申请/优先权号归并同族",
                           parents=[common])
    p_fam.add_argument("--file", default=None, help="records JSON 文件")

    p_fin = sub.add_parser("finalize", help="校验、补 runtime、生成 report.md",
                           parents=[common])
    p_fin.add_argument("--status", default=None,
                       help="可选：覆盖 status（不推荐，应诚实）")

    sub.add_parser("validate", help="校验 result.json 并打印 VALID/INVALID",
                   parents=[common])
    return parser


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

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
        result["status"] = "input_error"

    # 输入清单与读取降级
    inventory = []
    for f in files:
        entry = {"rel": f["rel"], "kind": f["kind"], "size": f["size"]}
        if f["kind"] in ("pdf", "image"):
            entry["note"] = "binary; read via platform PDF/OCR tools"
        elif f["kind"] in ("text", "table"):
            text, enc, err = patentlib.read_text(f["path"])
            entry["encoding"] = enc
            entry["chars"] = len(text) if text else 0
            entry["read_error"] = err
        inventory.append(entry)

    result.setdefault("input_inventory", inventory)
    if not files:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "no_input_files",
            "reason": "input directory has no discoverable files",
        })

    out_path = os.path.join(args.output, "result.json")
    patentlib.write_result(out_path, result)

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract": contract,
        "started_at": result["runtime"]["started_at"],
    }
    with open(os.path.join(args.work, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

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
        outputs = [patentlib.normalize_patent_number(args.text or "")]
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


def cmd_extract(args):
    if args.file:
        text, _enc, err = patentlib.read_text(args.file)
        if err and not text:
            print(json.dumps({"error": err}, ensure_ascii=False),
                  file=sys.stderr)
            sys.exit(0)
    else:
        text = args.text or ""
    hits = patentlib.extract_numbers(text)
    print(json.dumps(hits, ensure_ascii=False, indent=2))


def cmd_family(args):
    records = _read_json_arg(args.file) or []
    if not isinstance(records, list):
        records = [records]
    groups = patentlib.group_families(records)
    print(json.dumps({"families": groups, "observed": True,
                      "exhaustive": False}, ensure_ascii=False, indent=2))


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


def _write_report(path, result):
    """生成可读的 report.md（简洁、诚实，与 result.json 一致）。"""
    lines = [
        "# 检索报告",
        "",
        "- case_id: `%s`" % result.get("case_id", "unknown"),
        "- status: `%s`" % result.get("status", "unknown"),
        "- research_question: %s" % (result.get("research_question") or "未填写"),
        "- retrieval_scope: %s" % (result.get("retrieval_scope") or "未填写"),
        "",
        "## 产物统计",
        "",
        "| 字段 | 数量 |",
        "|---|---|",
        "| patent_records | %d |" % len(result.get("patent_records", [])),
        "| families | %d |" % len(result.get("families", [])),
        "| legal_status | %d |" % len(result.get("legal_status", [])),
        "| literature | %d |" % len(result.get("literature", [])),
        "| evidence | %d |" % len(result.get("evidence", [])),
        "| sources | %d |" % len(result.get("sources", [])),
        "| uncertainty | %d |" % len(result.get("uncertainty", [])),
        "| pending_human | %d |" % len(result.get("pending_human", [])),
        "",
        "## 证据与来源",
        "",
    ]
    for i, src in enumerate(result.get("sources", []), 1):
        lines.append("%d. `%s` %s" % (i, src.get("engine", "?"),
                                      src.get("url") or src.get("query") or ""))
    if not result.get("sources"):
        lines.append("（无来源记录）")
    lines += [
        "",
        "## 不确定性",
        "",
    ]
    for u in result.get("uncertainty", []):
        lines.append("- `%s`: %s" % (u.get("item", "?"), u.get("reason", "")))
    if not result.get("uncertainty"):
        lines.append("（无）")
    lines += [
        "",
        "## 待人工复核",
        "",
    ]
    for p in result.get("pending_human", []):
        lines.append("- %s" % (p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)))
    if not result.get("pending_human"):
        lines.append("（无）")
    lines += [
        "",
        "> 本报告由 finalize 生成，与 result.json 保持一致；result.json 为唯一机器可读产物。",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def cmd_finalize(args):
    result, path, err = _load_result(args)
    if err:
        print("finalize: %s" % err, file=sys.stderr)
        sys.exit(0)

    rt = result.setdefault("runtime", {})
    started = rt.get("started_at") or patentlib.now_iso()
    rt["started_at"] = started
    rt["finished_at"] = patentlib.now_iso()
    try:
        s0 = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        s1 = datetime.fromisoformat(str(rt["finished_at"]).replace("Z", "+00:00"))
        rt["elapsed_seconds"] = round(max(0.0, (s1 - s0).total_seconds()), 1)
    except (ValueError, TypeError):
        rt["elapsed_seconds"] = None

    if args.status and args.status in ("ok", "partial", "insufficient_evidence",
                                       "input_error"):
        result["status"] = args.status

    patentlib.write_result(path, result)

    # 生成 report.md（非关键，失败不影响 result.json）
    try:
        _write_report(os.path.join(args.output, "report.md"), result)
    except OSError as exc:
        print("finalize: report.md write failed: %s" % exc, file=sys.stderr)

    ok, errors = patentlib.validate_result(result)
    print("finalize: result.json written (status=%s, valid=%s)"
          % (result.get("status"), ok))
    for e in errors:
        print("  - %s" % e)


def cmd_validate(args):
    result, path, err = _load_result(args)
    if err:
        print("INVALID: %s" % err)
        sys.exit(1)
    contract = None
    if isinstance(result, dict):
        contract = result.get("_contract")
    ok, errors = patentlib.validate_result(result, contract)
    if ok:
        print("VALID")
    else:
        print("INVALID")
        for e in errors:
            print("  - %s" % e)
        sys.exit(1)


def main(argv=None):
    parser = _make_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(0)
    handler = {
        "scaffold": cmd_scaffold,
        "normalize": cmd_normalize,
        "extract": cmd_extract,
        "family": cmd_family,
        "finalize": cmd_finalize,
        "validate": cmd_validate,
    }.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    handler(args)
    sys.exit(0)


if __name__ == "__main__":
    main()
