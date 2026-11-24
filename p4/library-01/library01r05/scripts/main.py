"""main.py - 契约驱动科研资料库修复：薄入口，实现在 core_impl.py。

用法：
  python3 -B scripts/main.py                        # 全量运行
  python3 -B scripts/main.py --skeleton-only        # 只落盘 partial 骨架
  python3 -B scripts/main.py --update patch.json --result /app/output/result.json
  python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json
  python3 -B scripts/main.py --selftest             # 运行自检

退出码：0 成功；1 未预期错误（已尽力落盘）；2 写盘失败（骨架仍在）。
仅依赖 Python 标准库。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 默认运行时路径（Linux 容器）；在 Windows 上被显式参数覆盖。
DEFAULTS = {
    "case_json": "/input/case.json",
    "task_path": "/input/task.md",
    "input_root": "/input",
    "output_dir": "/app/output",
    "work_dir": "/app/work",
}


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="契约驱动科研资料库修复（document library repair）",
        add_help=True,
    )
    p.add_argument("--case-json", default=DEFAULTS["case_json"], help="案例 JSON 路径")
    p.add_argument("--task-path", default=DEFAULTS["task_path"], help="task.md 路径")
    p.add_argument("--input-root", default=DEFAULTS["input_root"], help="只读输入目录")
    p.add_argument("--output-dir", default=DEFAULTS["output_dir"], help="最终产物目录")
    p.add_argument("--work-dir", default=DEFAULTS["work_dir"], help="中间产物目录")
    p.add_argument("--skeleton-only", action="store_true", help="只写 partial 骨架")
    p.add_argument("--update", metavar="PATCH", help="应用增量 patch（set/merge/append）")
    p.add_argument("--result", metavar="PATH", help="--update/--check 的目标 result.json")
    p.add_argument("--check", metavar="PATH", help="校验 result.json（契约覆盖率）")
    p.add_argument("--selftest", action="store_true", help="运行自检脚本")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    sys.path.insert(0, HERE)

    if args.selftest:
        import selftest

        return selftest.run()

    import core_impl

    if args.check:
        code = core_impl.run_check(args.check, args.case_json)
        raise SystemExit(code)
    if args.update:
        if not args.result:
            print("error: --update requires --result PATH", file=sys.stderr)
            raise SystemExit(2)
        code = core_impl.apply_update(args.update, args.result, args.case_json)
        raise SystemExit(code)
    if args.skeleton_only:
        code = core_impl.run_skeleton(
            args.case_json, args.task_path, args.input_root,
            args.output_dir, args.work_dir,
        )
        raise SystemExit(code)

    code = core_impl.run(
        args.case_json, args.task_path, args.input_root,
        args.output_dir, args.work_dir,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
