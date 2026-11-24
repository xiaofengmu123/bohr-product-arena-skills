# -*- coding: utf-8 -*-
"""selftest.py — 独立自检脚本。

在临时目录构造一个微型科研知识库案例，端到端运行
scaffold → inventory → finalize，并验证：
  1. result.json 合法且含 case_id / status / output_contract 回填
  2. evidence_map 的逐字引用回核通过
  3. 重复文件（SHA256）与版本家族（v1/v2）被正确检出
  4. 空输入目录的降级产物合法且不崩溃

运行：python3 scripts/selftest.py
退出码 0 表示全部通过，1 表示存在失败项。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(core_impl.run_selftest())