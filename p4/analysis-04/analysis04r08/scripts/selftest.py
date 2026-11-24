"""selftest — 运行科研数据分析 Skill 的四种失败场景自检。

用法：python3 -B scripts/selftest.py
场景：空输入 / 坏 JSON / 坏编码 / 缺参数。全部通过退出 0，任一失败退出 1。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as C  # noqa: E402

if __name__ == "__main__":
    sys.exit(0 if C.run_selftest() else 1)
