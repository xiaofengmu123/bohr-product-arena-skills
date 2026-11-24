"""main.py - 科研知识库问答 Skill 的薄入口。

用法（相对路径，从 SKILL 包根目录调用）：
  python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 scripts/main.py inventory --input /input --work /app/work
  # Agent 在 inventory 之后写入 /app/work/answers.json（模型阶段）
  python3 scripts/main.py finalize --input /input --output /app/output --work /app/work \
      --answers /app/work/answers.json

所有逻辑在 core.py；本文件只负责参数解析、未知参数容错与默认目录回退。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研知识库盘点、证据索引与结果汇总（确定性、标准库）。",
    )
    p.add_argument("phase", choices=["scaffold", "inventory", "finalize"])
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--answers", default="/app/work/answers.json")
    return p


def main(argv=None) -> int:
    parser = _build_parser()
    args, unknown = parser.parse_known_args(argv)
    try:
        if args.phase == "scaffold":
            core.scaffold(args.input, args.output, args.work)
        elif args.phase == "inventory":
            core.inventory(args.input, args.work)
        elif args.phase == "finalize":
            core.finalize(args.input, args.output, args.work, args.answers)
        return 0
    except Exception as exc:  # noqa: BLE001
        # 任何异常都不让进程崩溃；写最小合法产物后退出 0
        _emergency_result(args.output, str(exc))
        return 0


def _emergency_result(output_dir: str, reason: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "result.json")
    if os.path.isfile(path):
        return  # 已有骨架/部分结果，避免覆盖
    minimal = {
        "case_id": "unknown",
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {
                "ts": core._utcnow(),
                "step": "emergency",
                "status": "error",
                "reason": reason,
            }
        ],
    }
    core.write_json(path, minimal)


if __name__ == "__main__":
    raise SystemExit(main())