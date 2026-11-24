#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 专利与技术文献检索 Skill 的命令行入口（纯标准库）。

子命令：
  scaffold  发现输入、读 case.json、回填 output_contract、落盘 partial 骨架
  normalize 规范化一个或多个专利号（参数或 JSON 文件）
  extract   从文本/文件抽取专利号候选
  family    读 records JSON，按共用优先权/申请号归并同族
  finalize  校验 result.json、补 runtime、生成 report.md
  validate  校验 result.json，打印 VALID / INVALID
  canonical 输出 result.json 的确定性序列化（供自检比较）

默认路径：--input /input --output /app/output --work /app/work；
可用环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 覆盖，亦可传命令行参数。
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


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# scaffold
# --------------------------------------------------------------------------
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
                print("warn: case.json unreadable: %s" % case_err["error"], file=sys.stderr)
            break

    contract = None
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract") or case_obj.get("outputContract")

    result = patentlib.build_skeleton(case_obj, contract)
    if case_path is not None and not isinstance(case_obj, dict):
        result["errors"].append({
            "item": "case_json_unreadable",
            "reason": case_err["error"] if case_err else "case.json is not a JSON object",
        })
        result["degradations"].append({
            "item": "case_json_fallback",
            "reason": "proceeded with case_id=unknown and default schema",
        })

    # 输入清单：二进制交给平台工具，文本记录编码/字符数
    inventory = []
    for f in files:
        if f["kind"] in ("pdf", "image"):
            inventory.append({"rel": f["rel"], "kind": f["kind"],
                              "size": f["size"], "note": "binary; read via platform tools"})
        elif f["kind"] in ("text", "table", "json"):
            text, enc, err = patentlib.read_text(f["path"])
            inventory.append({"rel": f["rel"], "kind": f["kind"],
                              "size": f["size"], "encoding": enc,
                              "chars": len(text), "read_error": err})
        else:
            inventory.append({"rel": f["rel"], "kind": f["kind"], "size": f["size"]})

    result.setdefault("input_inventory", inventory)
    if not files:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "no_input_files",
            "reason": "input directory has no discoverable files",
        })

    out_path = os.path.join(args.output, "result.json")
    _write(out_path, result)

    state = {
        "case_path": case_path,
        "input_files": [f["rel"] for f in files],
        "contract": contract,
        "started_at": result["runtime"]["started_at"],
        "deterministic_sha": None,
    }
    state["deterministic_sha"] = _sha_of(result)
    with open(os.path.join(args.work, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

    print("scaffold: wrote %s (status=%s, files=%d)"
          % (out_path, result["status"], len(files)))


def _sha_of(result):
    import hashlib
    return hashlib.sha256(patentlib.canonicalize(result).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# normalize / extract / family
# --------------------------------------------------------------------------
def _norm_inputs(args):
    if args.file:
        data = _read_json_arg(args.file)
        if data is None:
            return []
        items = data if isinstance(data, list) else [data]
        return [x if isinstance(x, str) else (x.get("raw") or x.get("text") or "")
                for x in items]
    if args.numbers:
        return list(args.numbers)
    return [args.text]


def cmd_normalize(args):
    outputs = [patentlib.normalize_patent_number(x) for x in _norm_inputs(args)]
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
    print(json.dumps({"families": groups, "observed": True,
                      "exhaustive": False}, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------
# finalize / validate / canonical
# --------------------------------------------------------------------------
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
        s0 = _parse_ts(started)
        s1 = _parse_ts(rt["finished_at"])
        rt["elapsed_seconds"] = round(max(0.0, (s1 - s0).total_seconds()), 1)
    except (ValueError, TypeError):
        rt["elapsed_seconds"] = None

    # 契约键缺失则补 null/[]（finalize 兜底）
    contract_keys = result.get("_contract_keys") or []
    for k in contract_keys:
        if k not in result:
            result[k] = None

    result.setdefault("change_log", []).append({
        "at": patentlib.now_iso(),
        "step": "finalize",
        "action": "validated contract, updated runtime",
    })
    if args.status:
        result["status"] = args.status
    _write(path, result)

    report_path = os.path.join(args.output, "report.md")
    _write_report(report_path, result)
    print("finalize: wrote %s and %s" % (path, report_path))


def _parse_ts(s):
    from datetime import datetime
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _write_report(path, result):
    lines = [
        "# 专利与技术文献检索报告",
        "",
        "- case_id: %s" % result.get("case_id"),
        "- status: %s" % result.get("status"),
        "- research_question: %s" % (result.get("research_question") or "not_reported"),
        "",
        "## 检索范围",
        "",
        str(result.get("retrieval_scope") or "not_reported"),
        "",
        "## 专利记录 / 同族 / 法律状态",
        "",
        "见 result.json 中的 patent_records / families / legal_status。",
        "同族归并仅基于观测到的优先权/申请号链接，exhaustive=false。",
        "",
        "## 证据与来源",
        "",
        "每条结论均带 locator + 逐字 quote（见 evidence），实际查询见 sources。",
        "",
        "## 缺口与不确定性",
        "",
    ]
    for section, key in (("uncertainty", "uncertainty"),
                         ("pending_human", "pending_human"),
                         ("limitations", "limitations"),
                         ("degradations", "degradations")):
        items = result.get(key) or []
        if items:
            lines.append("### %s" % section)
            lines.append("")
            for it in items:
                lines.append("- %s" % (it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)))
            lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def cmd_validate(args):
    result, _path, err = _load_result(args)
    if err:
        print("INVALID: %s" % err)
        sys.exit(0)
    issues, ok = patentlib.validate(result)
    for x in issues:
        print("%s: [%s] %s" % (x["level"].upper(), x["item"], x["message"]))
    if ok:
        print("VALID")
    else:
        print("INVALID")
    sys.exit(0)


def cmd_canonical(args):
    result, _path, err = _load_result(args)
    if err:
        print(json.dumps({"error": err}, ensure_ascii=False), file=sys.stderr)
        sys.exit(0)
    print(patentlib.canonicalize(result))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _add_input_output_work(sp):
    sp.add_argument("--input", default=_env_default("INPUT_DIR", "/input"))
    sp.add_argument("--output", default=_env_default("OUTPUT_DIR", "/app/output"))
    sp.add_argument("--work", default=_env_default("WORK_DIR", "/app/work"))


def _add_output(sp):
    sp.add_argument("--output", default=_env_default("OUTPUT_DIR", "/app/output"))


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="专利与技术文献检索 Skill 命令行入口")

    sub = p.add_subparsers(dest="command", required=True)

    sc = sub.add_parser("scaffold", help="发现输入并落盘 partial 骨架")
    _add_input_output_work(sc)

    n = sub.add_parser("normalize", help="规范化专利号")
    n.add_argument("--text", default="")
    n.add_argument("--numbers", nargs="*")
    n.add_argument("--file")

    e = sub.add_parser("extract", help="从文本抽取专利号候选")
    e.add_argument("--text", default="")
    e.add_argument("--file")

    f = sub.add_parser("family", help="按优先权/申请号归并同族")
    f.add_argument("--file")

    fin = sub.add_parser("finalize", help="校验契约、补 runtime、生成 report.md")
    fin.add_argument("--status", default="")
    _add_output(fin)

    va = sub.add_parser("validate", help="校验 result.json 并打印 VALID/INVALID")
    _add_output(va)

    ca = sub.add_parser("canonical", help="输出确定性序列化")
    _add_output(ca)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    {
        "scaffold": cmd_scaffold,
        "normalize": cmd_normalize,
        "extract": cmd_extract,
        "family": cmd_family,
        "finalize": cmd_finalize,
        "validate": cmd_validate,
        "canonical": cmd_canonical,
    }[args.command](args)


if __name__ == "__main__":
    main()
