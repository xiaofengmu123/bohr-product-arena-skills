"""main.py -- thin CLI entry for the research-question-driven search skill.

Delegates all logic to core.py. Kept small so the bulk of the package can be
reviewed separately; implementation and field contracts live in core.py and
references/*.md.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研问题驱动检索 Skill 的确定性辅助脚本（鲁棒性变体）。",
    )
    sub = p.add_subparsers(dest="command")

    def add_io(sp):
        sp.add_argument("--input", default=core.DEFAULT_INPUT)
        sp.add_argument("--output", default=core.DEFAULT_OUTPUT)
        sp.add_argument("--work", default=core.DEFAULT_WORK)

    sp = sub.add_parser("run", help="落盘 partial 骨架（每个案例最先执行）")
    add_io(sp)
    sp.add_argument("--runtime-requests", type=int, default=None)

    sp = sub.add_parser("set-plan", help="更新拆解/查询计划/术语扩展/停止判断")
    sp.add_argument("--file")
    sp.add_argument("--inline")
    sp.add_argument("--work", default=core.DEFAULT_WORK)

    sp = sub.add_parser("import", help="导入候选记录并全量筛选")
    sp.add_argument("--file")
    sp.add_argument("--inline")
    sp.add_argument("--work", default=core.DEFAULT_WORK)

    sp = sub.add_parser("evidence", help="追加证据/查询日志")
    sp.add_argument("--file")
    sp.add_argument("--inline")
    sp.add_argument("--work", default=core.DEFAULT_WORK)

    sp = sub.add_parser("assemble", help="组装 result.json / screening.md / plan.md")
    add_io(sp)
    sp.add_argument("--runtime-seconds", type=float, default=None)
    sp.add_argument("--runtime-requests", type=int, default=None)

    sp = sub.add_parser("selfcheck", help="逐条自检并写 selfcheck.md")
    sp.add_argument("--output", default=core.DEFAULT_OUTPUT)
    sp.add_argument("--work", default=core.DEFAULT_WORK)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    issues = []
    dispatch = {
        "run": core.cmd_run,
        "set-plan": core.cmd_set_plan,
        "import": core.cmd_import,
        "evidence": core.cmd_evidence,
        "assemble": core.cmd_assemble,
        "selfcheck": core.cmd_selfcheck,
    }
    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        return 0
    try:
        return fn(args, issues)
    except SystemExit as e:
        return int(e.code or 0)
    except Exception as e:  # never crash the harness
        sys.stderr.write("ERR: unexpected exception in %s: %r\n" % (args.command, e))
        return 0


if __name__ == "__main__":
    sys.exit(main())
