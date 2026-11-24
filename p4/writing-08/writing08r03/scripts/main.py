# -*- coding: utf-8 -*-
"""Entry point for the methods-experiments-writing skill.

Subcommands:
  skeleton  落盘合法 result.json 骨架 + state.json（前 3 分钟内先做）
  inventory 扫描 /input，分类材料，抽取带 locator+quote 的候选事实
  validate  校验 result.json 契约字段、证据与产物路径
  finalize  设置最终 status，追加 change_log，重算 content_sha256
  run       顺序执行 skeleton -> inventory -> validate
  selftest  确定性自检（合成输入跑两遍比对哈希；空目录/坏 JSON 降级）

Standard library only. Each subcommand accepts --input/--output/--work,
defaulting to /input, /app/output, /app/work.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C  # noqa: E402


def _timer(start):
    return int(time.monotonic() - start)


def _add_io(subparser):
    subparser.add_argument("--input", default=C.DEFAULT_INPUT,
                           help="输入目录（默认 /input）")
    subparser.add_argument("--output", default=C.DEFAULT_OUTPUT,
                           help="产物目录（默认 /app/output）")
    subparser.add_argument("--work", default=C.DEFAULT_WORK,
                           help="工作目录（默认 /app/work）")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="方法与实验章节写作 Skill 的确定性辅助脚本")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("skeleton", help="落盘 result.json 骨架")
    _add_io(sp)

    sp = sub.add_parser("inventory", help="扫描材料并抽取候选事实")
    _add_io(sp)

    sp = sub.add_parser("validate", help="校验 result.json")
    _add_io(sp)

    fp = sub.add_parser("finalize", help="收口：设 status 并重算哈希")
    _add_io(fp)
    fp.add_argument("--status", default="ok",
                    help="ok | partial | insufficient_evidence | input_error")

    sp = sub.add_parser("run", help="顺序 skeleton -> inventory -> validate")
    _add_io(sp)

    sp = sub.add_parser("selftest", help="确定性自检")
    sp.add_argument("--work-root", default="", help="自检临时根目录（默认系统临时目录）")

    args = parser.parse_args(argv)

    if args.cmd == "selftest":
        start = time.monotonic()
        passed, lines = C.run_selftest(args.work_root or None)
        for line in lines:
            print(line)
        print("elapsed_seconds=%d" % _timer(start))
        print("RESULT:", "PASS" if passed else "FAIL")
        return 0 if passed else 1

    start = time.monotonic()
    if args.cmd == "skeleton":
        result, _state, _rep = C.run_skeleton(args.input, args.output, args.work)
        print("skeleton status=%s case_id=%s" % (result["status"], result["case_id"]))
    elif args.cmd == "inventory":
        inv, facts, deg = C.run_inventory(args.input, args.output, args.work)
        print("inventory materials=%d facts=%d degradations=%d"
              % (len(inv["materials"]), facts["count"], len(deg)))
    elif args.cmd == "validate":
        report = C.run_validate(args.input, args.output, args.work)
        print("validate ok=%s status=%s issues=%d"
              % (report["ok"], report.get("status"), len(report["issues"])))
        for issue in report["issues"]:
            print("  -", issue)
        return 0 if report["ok"] else 1
    elif args.cmd == "finalize":
        rep = C.run_finalize(args.input, args.output, args.work, status=args.status)
        print("finalize ok=%s status=%s" % (rep.get("ok"), rep.get("status")))
        return 0 if rep.get("ok") else 1
    elif args.cmd == "run":
        report = C.run_pipeline(args.input, args.output, args.work)
        print("run validate ok=%s issues=%d" % (report["ok"], len(report["issues"])))
        return 0 if report["ok"] else 1
    print("elapsed_seconds=%d" % _timer(start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
