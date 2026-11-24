#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— 一键编排入口（纯标准库）。

依次执行 scaffold → render（若 mindmap.json 存在）→ selfcheck。
可直接运行：python3 -B scripts/main.py --input /input --output /app/output --work /app/work
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _parse_dirs(argv):
    input_dir, output_dir, work_dir = "/input", "/app/output", "/app/work"
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--input", "-i") and i + 1 < len(argv):
            input_dir = argv[i + 1]; i += 2
        elif a in ("--output", "-o") and i + 1 < len(argv):
            output_dir = argv[i + 1]; i += 2
        elif a in ("--work", "-w") and i + 1 < len(argv):
            work_dir = argv[i + 1]; i += 2
        else:
            i += 1
    return input_dir, output_dir, work_dir


def _run(script, argv):
    sys.argv = [os.path.join(HERE, script)] + argv
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    except SystemExit:
        pass  # 各脚本自行报告结果，SystemExit 不中断编排


def main():
    input_dir, output_dir, work_dir = _parse_dirs(sys.argv[1:])
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    _run("scaffold.py", ["--input", input_dir, "--output", output_dir, "--work", work_dir])
    mindmap = os.path.join(work_dir, "mindmap.json")
    if os.path.isfile(mindmap):
        _run("render_mindmap.py", ["--mindmap", mindmap, "--output", output_dir])
    _run("selfcheck.py", ["--input", input_dir, "--output", output_dir, "--mindmap", mindmap])
    return 0


if __name__ == "__main__":
    sys.exit(main())
