# -*- coding: utf-8 -*-
"""main.py - 引文追踪 Skill 的薄入口（实现见 core_impl.py）。

用法：
  python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
  python3 scripts/main.py finalize  --output /app/output --work /app/work
  python3 scripts/main.py selftest  [--quiet]
  python3 scripts/main.py selfcheck            # selftest 别名
  python3 scripts/main.py --help
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="引文追踪与关联文献发现 Skill 的确定性入口",
    )
    sub = p.add_subparsers(dest="command")

    b = sub.add_parser("bootstrap", help="扫描输入并落盘 result.json 骨架")
    b.add_argument("--input", default="/input")
    b.add_argument("--output", default="/app/output")
    b.add_argument("--work", default="/app/work")
    b.set_defaults(func=core_impl.cmd_bootstrap)

    f = sub.add_parser("finalize", help="校验 result.json 并生成可复核产物")
    f.add_argument("--output", default="/app/output")
    f.add_argument("--work", default="/app/work")
    f.set_defaults(func=core_impl.cmd_finalize)

    s = sub.add_parser("selftest", help="运行确定性自检（不访问网络）")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=_cmd_selftest)

    return p


def _cmd_selftest(args):
    try:
        import selftest
    except ImportError:
        print("selftest.py 缺失", file=sys.stderr)
        return 1
    return selftest.run(quiet=bool(args.quiet))


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
