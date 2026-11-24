"""main.py - 薄入口封装，实现位于 core_impl.py（纯标准库）。"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _help():
    print(
        "usage: main.py [skeleton|run|update|check|selftest] [options]\n"
        "\n"
        "  skeleton  --case-json /input/case.json --task-path /input/task.md\n"
        "            --output-dir /app/output --work-dir /app/work\n"
        "  run       [同上]，执行全量盘点/去重/版本/别名/对账/索引/问答\n"
        "  update    --patch patch.json --result /app/output/result.json\n"
        "  check     --result /app/output/result.json --case-json /input/case.json\n"
        "  selftest  运行自检（生成临时合成输入并清理）\n"
    )


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        _help()
        raise SystemExit(0)
    target = os.path.join(HERE, "core_impl.py")
    sys.argv[0] = target
    runpy.run_path(target, run_name="__main__")
