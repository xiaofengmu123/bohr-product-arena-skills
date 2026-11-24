#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — LaTeX 写作与编译 Skill 的命令行入口。

子命令（退出码语义：非致命失败=0，仅 selfcheck 发现问题时=1）：
  bootstrap   扫描 /input，立即落盘 result.json 骨架 + work/inventory.json
  discover    打印 /input 文件清单（JSON）
  finalize    规范化并重写 /app/output/result.json（确定性、补齐全字段）
  selfcheck   校验 result.json 契约，逐条输出自检清单
  manifest    生成 /app/output/build_manifest.json（产物 sha256 清单，供复核）
  compile     本地 TeX 引擎兜底编译（首选平台 latex-compiler 工具）
  classify-log 解析 .log，报错按 文件:行号+分类+根因 输出
  refcheck    检查 \\ref / \\cite 未解析项
  evidence index/quote  材料证据索引与逐字片段抽取

仅依赖 Python 标准库；--input/--output/--work 缺省指向 /input、/app/output、
/app/work。SKILL.md 中全部以相对路径 scripts/main.py 调用。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402


def _common_parser():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--input", default=C.DEFAULT_INPUT)
    p.add_argument("--output", default=C.DEFAULT_OUTPUT)
    p.add_argument("--work", default=C.DEFAULT_WORK)
    return p


def _arg_parser():
    common = _common_parser()
    p = argparse.ArgumentParser(prog="main.py",
                                description="LaTeX 写作与编译 Skill 入口",
                                parents=[common])
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("bootstrap", parents=[common])
    sub.add_parser("discover", parents=[common])
    sub.add_parser("finalize", parents=[common])
    sub.add_parser("selfcheck", parents=[common])
    sub.add_parser("manifest", parents=[common])

    cp = sub.add_parser("compile")
    cp.add_argument("--main", required=True)
    cp.add_argument("--outdir", default=C.DEFAULT_OUTPUT)
    cp.add_argument("--passes", type=int, default=3)
    cp.add_argument("--engine", default="auto")

    cl = sub.add_parser("classify-log")
    cl.add_argument("--log", required=True)
    cl.add_argument("--main")

    rc = sub.add_parser("refcheck")
    rc.add_argument("--dir", required=True)
    rc.add_argument("--bib")

    ct = sub.add_parser("check-tex")
    ct.add_argument("--main", required=True)
    ct.add_argument("--bib")

    ev = sub.add_parser("evidence")
    evsub = ev.add_subparsers(dest="evcmd")
    ei = evsub.add_parser("index")
    ei.add_argument("--root", default=C.DEFAULT_INPUT)
    ei.add_argument("--out", default=os.path.join(C.DEFAULT_WORK, "source_index.json"))
    eq = evsub.add_parser("quote")
    eq.add_argument("--file", required=True)
    eq.add_argument("--start", type=int, default=1)
    eq.add_argument("--end", type=int, default=None)
    return p


def cmd_bootstrap(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    skeleton, keys = C.build_skeleton(args.input)
    C.write_json(os.path.join(args.output, "result.json"), skeleton)
    inventory = {
        "input_root": args.input,
        "contract_keys": keys,
        "files": C.discover_input(args.input),
        "run_id": skeleton["runtime"]["run_id"],
    }
    C.write_json(os.path.join(args.work, "inventory.json"), inventory)
    print(json.dumps({
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_keys": keys,
        "file_count": len(inventory["files"]),
        "run_id": skeleton["runtime"]["run_id"],
        "result_path": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args):
    print(json.dumps({"input_root": args.input,
                      "files": C.discover_input(args.input)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    inv_path = os.path.join(args.work, "inventory.json")
    keys = []
    if os.path.isfile(inv_path):
        inv, _ = C.read_json(inv_path)
        if isinstance(inv, dict):
            keys = inv.get("contract_keys", []) or []
    if not keys:
        _, case_obj = C.read_json(os.path.join(args.input, "case.json"))
        keys = C.contract_keys(case_obj) if case_obj else []
    result_path = os.path.join(args.output, "result.json")
    result, _ = C.read_json(result_path)
    if not isinstance(result, dict):
        result = {}
    result = C.normalize_result(result, keys)
    run_id = C.run_id_for(args.input)
    result["runtime"]["run_id"] = run_id
    for entry in result.get("change_log", []):
        if isinstance(entry, dict) and not entry.get("at"):
            entry["at"] = run_id
    result["change_log"].append({
        "at": run_id, "step": "finalize",
        "note": "normalized result.json; contract keys echoed verbatim",
    })
    C.write_json(result_path, result)
    checks, hard_ok = C.selfcheck(result, keys, args.output)
    print(C.render_checklist(checks))
    print(json.dumps({"hard_ok": hard_ok, "run_id": run_id}, ensure_ascii=False))
    return 0  # finalize 本身不因自检失败退出，交由 selfcheck 判断


def cmd_selfcheck(args):
    result_path = os.path.join(args.output, "result.json")
    result, errs = C.read_json(result_path)
    if not isinstance(result, dict):
        print("selfcheck: result.json missing or unparseable; " + "; ".join(errs))
        return 1
    inv_path = os.path.join(args.work, "inventory.json")
    keys = []
    if os.path.isfile(inv_path):
        inv, _ = C.read_json(inv_path)
        if isinstance(inv, dict):
            keys = inv.get("contract_keys", []) or []
    checks, hard_ok = C.selfcheck(result, keys, args.output)
    print(C.render_checklist(checks))
    return 0 if hard_ok else 1


def cmd_manifest(args):
    manifest = C.build_manifest(args.output, args.input)
    C.write_json(os.path.join(args.output, "build_manifest.json"), manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def cmd_compile(args):
    report = C.compile_tex(args.main, args.outdir, passes=args.passes,
                           engine_pref=args.engine)
    C.write_json(os.path.join(args.outdir, "compile_report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_classify(args):
    report = {"log": args.log, "errors": C.classify_log(args.log, args.main)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_refcheck(args):
    report = C.refcheck(args.dir, args.bib)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_check_tex(args):
    report = C.check_tex_requirements(args.main, args.bib)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_evidence(args):
    if args.evcmd == "index":
        C.index_evidence(args.root, args.out)
        print(json.dumps({"indexed": args.out}, ensure_ascii=False))
        return 0
    if args.evcmd == "quote":
        text, err = C.quote_file(args.file, args.start, args.end)
        if err:
            print(json.dumps({"error": err}, ensure_ascii=False))
        else:
            print(text)
        return 0
    print("evidence: choose 'index' or 'quote'")
    return 0


def main(argv=None):
    C.reconfigure_stdio()
    args = _arg_parser().parse_args(argv)
    handlers = {
        "bootstrap": cmd_bootstrap,
        "discover": cmd_discover,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
        "manifest": cmd_manifest,
        "compile": cmd_compile,
        "classify-log": cmd_classify,
        "refcheck": cmd_refcheck,
        "check-tex": cmd_check_tex,
        "evidence": cmd_evidence,
    }
    if args.cmd not in handlers:
        _arg_parser().print_help()
        return 0
    try:
        return handlers[args.cmd](args)
    except Exception as exc:  # 非致命异常统一降级，不崩溃
        print(json.dumps({"error": "unhandled_%s" % exc.__class__.__name__,
                          "detail": str(exc)}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
