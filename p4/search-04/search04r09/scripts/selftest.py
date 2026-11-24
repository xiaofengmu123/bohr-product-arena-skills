"""selftest.py -- 交付前自检：可直接运行，也可由 core_impl 的 selftest 子命令触发。

覆盖：空输入、坏 JSON、output_contract 回填、身份去重、最短路、
产物生成、SVG 可解析、重复运行产物一致性。全部通过返回 0，否则返回 1。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core_impl import run_selftest  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_selftest())