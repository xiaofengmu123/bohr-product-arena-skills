"""selftest.py — 独立自检脚本（与 main.py selftest 子命令等价）。

用法：
    python3 -B scripts/selftest.py

覆盖降级场景：输入目录缺失、空输入、坏 JSON case.json、超大文件、
坏编码文件、全流程契约回填、重跑幂等。全部用例通过时退出码 0。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_impl import run_selftest  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_selftest())
