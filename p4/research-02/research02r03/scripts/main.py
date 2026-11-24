#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0 确定性骨架入口（薄封装，核心逻辑在 core_impl.py）。

用法：
  python -B scripts/main.py --input /input --output /app/output --work /app/work

行为：
  1. 发现 case.json / task.md / 材料文件；
  2. 回填 output_contract 全部字段并立即落盘一版合法 result.json（status=partial 等）；
  3. 扫描材料、按内容哈希去重、对 PDF 调用 pdftotext 抽取文本；
  4. 写 inventory.json、manifest.json 与各主产物的骨架文件。

退出码：0 表示成功（即使输入为空也返回 0，并写 insufficient_evidence 骨架）。
"""

import argparse

import core_impl as core


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="生成文献综述 Skill 的 Phase 0 确定性骨架"
    )
    parser.add_argument("--input", default="/input", help="原始输入目录（默认 /input）")
    parser.add_argument("--output", default="/app/output", help="最终产物目录（默认 /app/output）")
    parser.add_argument("--work", default="/app/work", help="中间产物目录（默认 /app/work）")
    parser.add_argument("--max-bytes", type=int, default=core.MAX_FILE_BYTES,
                        help="单文件大小上限（默认 2MB）")
    args = parser.parse_args(argv)

    result, _ok = core.build_skeleton(args.input, args.output, args.work, args.max_bytes)
    print("[lit-review] Phase 0 骨架已落盘：case_id=%s status=%s"
          % (result.get("case_id"), result.get("status")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
