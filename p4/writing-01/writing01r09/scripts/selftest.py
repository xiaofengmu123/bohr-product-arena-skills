#!/usr/bin/env python3
"""自检入口：python3 scripts/selftest.py

用临时目录跑「空输入 / 坏 JSON / 正常输入」三种降级场景，验证脚本本身可运行；
不产出任何正式结果。退出码 0=自检通过，1=存在失败项。等价于 `main.py selftest`。

只依赖 Python 标准库；相对路径运行于 Skill 根目录。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402  同目录导入，无第三方依赖

if __name__ == "__main__":
    sys.exit(main.main(["selftest"]))
