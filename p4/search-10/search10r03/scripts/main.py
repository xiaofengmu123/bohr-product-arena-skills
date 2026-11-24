#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 跨语言文献检索 Skill 编排器 CLI。

子命令：
  init      发现 /input、解析 case_id 与 output_contract，原子写入 partial 结果骨架。
            必须最先执行，保证任何后续异常都不留下不完整产物。
  patch     深合并 JSON 补丁到 result.json（字典递归合并、列表整体替换），重算指纹。
  finalize  重算指纹、记录 runtime、追加 change_log 收尾条目。
  validate  校验 result.json 合法性与契约字段，输出缺失清单。
  status    摘要当前进度与 change_log。
  selftest  在临时目录构建多种输入（正常/空目录/坏 JSON/坏编码），自检确定性。

每个子命令均接受 --input/--output/--work（缺省 /input、/app/output、/app/work），
路径选项可置于子命令之后，例如：
  python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
仅依赖 Python 标准库。
"""

import argparse
import json
import os
import sys

try:
    import core
except ImportError:  # 从任意 cwd 调用时的安全兜底
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import core


def _read_patch(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-8-sig")
    except OSError as exc:
        print("patch_read_error:%s" % exc)
        sys.exit(0)
    try:
        return json.loads(text)
    except ValueError as exc:
        print("patch_bad_json:%s" % exc)
        sys.exit(0)


def cmd_init(args):
    result, errs = core.do_init(args.input, args.output, args.work)
    print("case_id=%s status=%s errors=%d" % (result["case_id"], result["status"], len(errs)))
    if errs:
        print("errors=%s" % "; ".join(errs))
    return 0


def cmd_patch(args):
    patch_obj = _read_patch(args.patch)
    result, err = core.do_patch(args.output, args.work, patch_obj)
    if err:
        print("patch_failed:%s" % err)
    else:
        print("patched status=%s results=%d" % (result.get("status"), len(result.get("results") or [])))
    return 0


def cmd_finalize(args):
    result, err = core.do_finalize(args.input, args.output, args.work)
    if err:
        print("finalize_failed:%s" % err)
    else:
        print("finalized status=%s runtime=%s fingerprint=%s" % (
            result.get("status"), result.get("runtime"), result.get("determinism_fingerprint")))
    return 0


def cmd_validate(args):
    issues = core.do_validate(args.input, args.output)
    if issues:
        print("ISSUES (%d):" % len(issues))
        for issue in issues:
            print("  - %s" % issue)
    else:
        print("OK: result.json 合法，契约字段齐全，指纹一致")
    return 0


def cmd_status(args):
    print(core.do_status(args.output))
    return 0


def cmd_selftest(args):
    try:
        import selftest
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import selftest
    ok = selftest.run_selftest(quiet=args.quiet)
    return 0 if ok else 1


def _add_path_args(parser):
    parser.add_argument("--input", default=core.DEFAULT_INPUT, help="输入目录，缺省 /input")
    parser.add_argument("--output", default=core.DEFAULT_OUTPUT, help="产物目录，缺省 /app/output")
    parser.add_argument("--work", default=core.DEFAULT_WORK, help="工作目录，缺省 /app/work")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="cross-lingual literature search skill orchestrator",
        add_help=True,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="写 partial 结果骨架（最先执行）")
    _add_path_args(p_init)

    p_patch = sub.add_parser("patch", help="深合并 JSON 补丁")
    p_patch.add_argument("--patch", required=True, help="补丁 JSON 文件路径")
    _add_path_args(p_patch)

    p_fin = sub.add_parser("finalize", help="收尾：重算指纹并记录 runtime")
    _add_path_args(p_fin)

    p_val = sub.add_parser("validate", help="校验结果契约与合法性")
    _add_path_args(p_val)

    p_st = sub.add_parser("status", help="进度摘要")
    _add_path_args(p_st)

    p_self = sub.add_parser("selftest", help="临时目录自检（含确定性）")
    p_self.add_argument("--quiet", action="store_true", help="只输出摘要，不逐条打印")
    _add_path_args(p_self)

    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "patch": cmd_patch,
        "finalize": cmd_finalize,
        "validate": cmd_validate,
        "status": cmd_status,
        "selftest": cmd_selftest,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
