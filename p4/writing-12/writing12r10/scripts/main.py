#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — 事实优先写作 Skill 的编排入口（薄壳，逻辑在 core_impl / latex_tools）。

子命令：
  bootstrap  扫描 /input，立即落盘 result.json 骨架 + inventory.json（先落盘再分析）
  discover   列出 /input 下所有文件
  extract    构建事实库 factbase.json + source_index.json（中间表示）
  patch      对 result.json 做增量字段/来源/说明更新（保持合法 JSON）
  finalize   补齐契约字段与元数据，诚实判定 status
  selfcheck  校验 result.json 是否满足输出契约（有问题退出码 1）
  selftest   对标准库组件跑确定性自测（空输入/坏 JSON/坏编码）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as ci  # noqa: E402

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}


def _paths(args):
    return (getattr(args, "input", None) or DEFAULTS["input"],
            getattr(args, "output", None) or DEFAULTS["output"],
            getattr(args, "work", None) or DEFAULTS["work"])


def cmd_bootstrap(args):
    inp, out, work = _paths(args)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    skeleton, keys = ci.build_skeleton(inp)
    ci.write_json(os.path.join(out, "result.json"), skeleton)
    inventory = {
        "input_root": inp,
        "contract_keys": keys,
        "files": ci.discover_input(inp),
        "generated_at": ci.utcnow(),
    }
    ci.write_json(os.path.join(work, "inventory.json"), inventory)
    print(json.dumps({
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_keys": keys,
        "file_count": len(inventory["files"]),
        "result_path": os.path.join(out, "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args):
    inp, _, _ = _paths(args)
    print(json.dumps({"input_root": inp, "files": ci.discover_input(inp)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_extract(args):
    inp, out, work = _paths(args)
    os.makedirs(work, exist_ok=True)
    factbase, notes = ci.extract_facts(inp)
    ci.write_json(os.path.join(work, "factbase.json"), factbase)
    ci.write_json(os.path.join(work, "source_index.json"), ci.index_sources(inp))
    print(json.dumps({
        "fact_count": factbase["fact_count"],
        "conflicts": factbase["conflicts"],
        "notes": notes,
        "factbase": os.path.join(work, "factbase.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_patch(args):
    _, out, work = _paths(args)
    rpath = os.path.join(out, "result.json")
    obj, errs = ci.load_result(rpath)
    if obj is None:
        # 从 inventory 重建骨架，再应用补丁，保证"先有骨架再改"
        inv_path = os.path.join(work, "inventory.json")
        keys = []
        if os.path.isfile(inv_path):
            inv, _ = ci.read_json(inv_path)
            keys = inv.get("contract_keys", []) if isinstance(inv, dict) else []
        obj = {"case_id": "unknown", "status": "partial"}
        for k in keys:
            obj[k] = None
    for pair in args.set or []:
        key, value = pair.split("=", 1)
        try:
            value = json.loads(value)
        except Exception:
            pass
        obj[key] = value
    for note in args.note or []:
        obj.setdefault("change_log", []).append(
            {"time": ci.utcnow(), "step": "patch", "note": note})
    if args.source:
        obj.setdefault("sources", []).append({"locator": args.source[0],
                                               "quote": args.source[1]})
    if args.uncertainty:
        obj.setdefault("uncertainty", []).append(args.uncertainty)
    if args.pending:
        obj.setdefault("pending_human", []).append(args.pending)
    if args.status:
        if args.status not in ci.ALLOWED_STATUS:
            print(json.dumps({"error": "bad_status", "allowed": sorted(ci.ALLOWED_STATUS)}))
            return 1
        obj["status"] = args.status
    ci.ensure_meta(obj)
    ci.write_json(rpath, obj)
    print(json.dumps({"ok": True, "result_path": rpath}, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    inp, out, work = _paths(args)
    os.makedirs(out, exist_ok=True)
    rpath = os.path.join(out, "result.json")
    obj, errs = ci.load_result(rpath)
    if obj is None:
        obj, keys = ci.build_skeleton(inp)
    else:
        _, keys = ci.build_skeleton(inp)
        # 契约键若在骨架中存在但 result 缺失，补 null
        for k in keys:
            obj.setdefault(k, None)
    ci.ensure_meta(obj)

    # 判定主产物与材料存在性
    has_primary = any(
        f for f in ci.discover_input(out) if f["ext"] in (".tex", ".md", ".docx", ".svg", ".csv")
    )
    has_material = any(f["is_text"] for f in ci.discover_input(inp))
    obj["status"] = ci.resolve_status(obj, keys, has_primary, has_material)
    obj["runtime"]["finished_at"] = ci.utcnow()
    obj["runtime"]["producer"] = "main.py finalize"
    if not has_primary and obj["status"] != "insufficient_evidence":
        obj["degradations"].append("no_primary_artifact_in_output")
    ci.write_json(rpath, obj)
    print(json.dumps({"status": obj["status"], "result_path": rpath}, ensure_ascii=False))
    return 0


def cmd_quote(args):
    inp, _, _ = _paths(args)
    text, err = ci.quote_file(inp, args.file, args.start, args.end)
    if text is None:
        print(json.dumps({"error": err}, ensure_ascii=False))
        return 1
    print(text)
    return 0


def cmd_selfcheck(args):
    inp, out, work = _paths(args)
    report, code = ci.selfcheck(out, work, inp)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


def cmd_selftest(args):
    """确定性自测：空目录 / 坏 JSON / 坏编码 / 契约回填。"""
    import tempfile
    results = []
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    inp = os.path.join(tmp, "input"); out = os.path.join(tmp, "output"); work = os.path.join(tmp, "work")
    os.makedirs(inp); os.makedirs(out); os.makedirs(work)

    # 1) 空输入
    sk, keys = ci.build_skeleton(inp)
    assert sk["case_id"] == "unknown" and sk["status"] == "input_error", "empty-input skeleton"
    results.append("empty_input: ok")

    # 2) 截断但可恢复的 JSON（合法前缀 + 尾随垃圾）
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write('{"case_id": "c1", "output_contract": {"a": 1, "b": 2}} TRAILING')
    obj, errs = ci.read_json(os.path.join(inp, "case.json"))
    assert obj is not None and obj.get("case_id") == "c1" and errs, "truncated-json tolerance"
    results.append("bad_json: ok")

    # 3) 坏编码（latin-1 字节）
    with open(os.path.join(inp, "materials.txt"), "wb") as fh:
        fh.write("caf\xe9 naive".encode("latin-1"))
    text, err = ci.read_text(os.path.join(inp, "materials.txt"))
    assert text is not None and err == "decoded_with_replacement", "bad-encoding tolerance"
    results.append("bad_encoding: ok")

    # 4) 契约回填原样
    good = os.path.join(tmp, "good", "input"); os.makedirs(good)
    with open(os.path.join(good, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": "X1", "output_contract": {"title": None, "abstract": None}}, fh)
    sk2, keys2 = ci.build_skeleton(good)
    assert keys2 == ["title", "abstract"] and "title" in sk2 and "abstract" in sk2, "contract echo"
    results.append("contract_echo: ok")

    # 5) 事实抽取与冲突
    fbs, _ = ci.extract_facts(good)
    assert isinstance(fbs["fact_count"], int) and "facts" in fbs, "fact extraction"
    results.append("fact_extract: ok")

    print(json.dumps({"selftest": "ok", "checks": results}, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    # 三个通用选项同时挂到顶层与每个子命令，且用 SUPPRESS 避免子命令覆盖顶层值。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=argparse.SUPPRESS)
    common.add_argument("--output", default=argparse.SUPPRESS)
    common.add_argument("--work", default=argparse.SUPPRESS)

    ap = argparse.ArgumentParser(
        prog="main.py", parents=[common],
        description="事实优先 LaTeX 写作 Skill 编排入口（仅标准库）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("bootstrap", parents=[common], help="落盘 result.json 骨架 + inventory.json")
    sub.add_parser("discover", parents=[common], help="列出 /input 文件")
    sub.add_parser("extract", parents=[common], help="构建事实库 factbase.json + source_index.json")

    p_patch = sub.add_parser("patch", parents=[common], help="增量更新 result.json（保持合法 JSON）")
    p_patch.add_argument("--set", action="append", metavar="KEY=VALUE")
    p_patch.add_argument("--note", action="append")
    p_patch.add_argument("--source", nargs=2, metavar=("LOCATOR", "QUOTE"))
    p_patch.add_argument("--uncertainty")
    p_patch.add_argument("--pending")
    p_patch.add_argument("--status", choices=sorted(ci.ALLOWED_STATUS))

    sub.add_parser("finalize", parents=[common], help="补齐契约字段并诚实判定 status")
    p_quote = sub.add_parser("quote", parents=[common], help="按 locator 取逐字片段（L 行号 1-based）")
    p_quote.add_argument("--file", required=True, metavar="REL_PATH")
    p_quote.add_argument("--start", type=int, required=True)
    p_quote.add_argument("--end", type=int, default=None)

    sub.add_parser("selfcheck", parents=[common], help="校验 result.json 契约，有问题退出码 1")
    sub.add_parser("selftest", parents=[common], help="标准库组件确定性自测")
    return ap


def main(argv=None):
    ci.reconfigure_stdio()
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 0
    handlers = {
        "bootstrap": cmd_bootstrap, "discover": cmd_discover,
        "extract": cmd_extract, "patch": cmd_patch,
        "finalize": cmd_finalize, "quote": cmd_quote,
        "selfcheck": cmd_selfcheck, "selftest": cmd_selftest,
    }
    try:
        return handlers[args.cmd](args)
    except Exception as exc:  # 兜底：任何异常退出码 0，绝不崩溃
        print(json.dumps({"fatal": str(exc), "hint": "fallback handled, no crash"},
                         ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
