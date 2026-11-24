#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py -- research-kb-qa Skill 的薄入口，实现在 core_impl.py。

用法：
  python3 scripts/main.py full       # 依次执行 scaffold → inventory → finalize
  python3 scripts/main.py scaffold   # 只落盘 partial 结果骨架（3 分钟内先跑）
  python3 scripts/main.py inventory  # 只做目录/元数据/版本/重复/权限盘点
  python3 scripts/main.py finalize   # 只融合并校验 answers.json
  python3 scripts/main.py --help     # 查看参数
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    # 确保可 import 同目录 core_impl，且相对路径调用不依赖 cwd。
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import core_impl
    core_impl.run()
