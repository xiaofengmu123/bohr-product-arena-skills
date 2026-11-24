#!/usr/bin/env python3
"""analyze.py -- search coverage analysis CLI dispatcher.

Thin entry wrapper; all logic lives in core_impl.py. Standard library only.

Subcommands:
  scaffold  Write a minimal but valid result.json skeleton (status=partial).
  inspect   Inventory /input, classify files, write work/inspect.json.
  run       Build frozen reference set, match search results, compute coverage.
  merge     Apply adjudication/attribution/supplementary-search updates.

Defaults: --input /input --output /app/output --work /app/work
All commands exit 0 on known degraded states; they write a valid result.json
instead of crashing.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=argparse.SUPPRESS,
                        help="输入目录（默认 /input）")
    common.add_argument("--output", default=argparse.SUPPRESS,
                        help="输出目录（默认 /app/output）")
    common.add_argument("--work", default=argparse.SUPPRESS,
                        help="工作目录（默认 /app/work）")

    p = argparse.ArgumentParser(
        prog="analyze.py", parents=[common],
        description="科研文献检索覆盖率与遗漏分析流水线（scaffold/inspect/run/merge）。")
    p.set_defaults(input="/input", output="/app/output", work="/app/work")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scaffold", parents=[common],
                   help="落盘最小合法 result.json 骨架")
    sub.add_parser("inspect", parents=[common],
                   help="盘点输入文件并写 work/inspect.json")

    rp = sub.add_parser("run", parents=[common],
                        help="构建参考集并完成命中判定与覆盖率计算")
    rp.add_argument("--refs", default=None,
                    help="逗号分隔的参考集文件名/glob（覆盖文件名启发式）")
    rp.add_argument("--results", default=None,
                    help="逗号分隔的检索结果文件名/glob（覆盖文件名启发式）")

    mp = sub.add_parser("merge", parents=[common],
                        help="应用裁决/归因/补检更新")
    mp.add_argument("--updates", required=True,
                    help="更新文件 JSON 路径（adjudications/attributions/"
                         "supplementary_searches/residual_omissions）")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    out_dir = args.output
    work_dir = args.work
    core_impl.ensure_dir(out_dir)
    core_impl.ensure_dir(work_dir)

    if args.command == "scaffold":
        core_impl.cmd_scaffold(args.input, out_dir, work_dir)
    elif args.command == "inspect":
        core_impl.cmd_inspect(args.input, out_dir, work_dir)
    elif args.command == "run":
        core_impl.cmd_run(args.input, out_dir, work_dir,
                          refs_glob=args.refs, results_glob=args.results)
    elif args.command == "merge":
        core_impl.cmd_merge(out_dir, work_dir, args.updates)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- last-resort guard, never crash
        # Attempt a minimal valid artifact before exiting non-zero.
        try:
            path = os.path.join("/app/output", "result.json")
            core_impl.write_json_atomic(path, {
                "case_id": "unknown",
                "status": "input_error",
                "errors": ["uncaught exception: %r" % exc],
                "sources": [], "uncertainty": [], "change_log": [
                    {"at": core_impl.now_iso(), "action": "crash-guard",
                     "detail": "未捕获异常被捕获并落盘最小错误结果"}],
            })
        except Exception:
            pass
        print("analyze.py crashed: %r" % exc, file=sys.stderr)
        sys.exit(1)
