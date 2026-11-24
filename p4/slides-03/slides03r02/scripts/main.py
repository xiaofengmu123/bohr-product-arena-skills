#!/usr/bin/env python3
"""main.py — 编排入口：发现材料 → 落盘骨架 → 契约自检（仅标准库）。

内容组织（主线/逐页/演示件/渲染）由加载本 Skill 的 Agent 按 SKILL.md 执行；
本脚本提供确定性的脚手架与自检层，保证任何时刻 result.json 合法、产物可核。

用法示例：
  python3 scripts/main.py --input /input --task /input/task.md --work /app/work --output /app/output
  python3 scripts/main.py --input /input --work /app/work --output /app/output --strict
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scaffold  # noqa: E402
import selftest  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="发现材料 → 落盘骨架 → 自检（仅标准库）")
    ap.add_argument("--input", default="/input", help="输入目录，默认 /input")
    ap.add_argument("--task", default="/input/task.md", help="task.md 路径")
    ap.add_argument("--work", default="/app/work", help="工作目录，默认 /app/work")
    ap.add_argument("--output", default="/app/output", help="输出目录，默认 /app/output")
    ap.add_argument("--case", default=None, help="case.json 路径覆盖")
    ap.add_argument("--strict", action="store_true", help="任一机器 fail 项返回非零")
    a = ap.parse_args(argv)

    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.output, exist_ok=True)

    case_path = a.case or os.path.join(a.input, "case.json")
    case, err = scaffold.load_json(case_path)
    if case is None:
        case = {}

    # 发现材料并写入工作目录清单
    materials = scaffold.discover_materials(a.input)
    with open(os.path.join(a.work, "materials.json"), "w", encoding="utf-8") as f:
        json.dump({"input_dir": a.input, "materials": materials}, f, ensure_ascii=False, indent=2)

    # 早落盘：写合法 partial 骨架（前三分钟铁律）
    result = scaffold.build_skeleton(case, a.input, a.task)
    if err:
        result.setdefault("uncertainty", []).append("case.json: {0}".format(err))
    scaffold.write_result(result, a.output)

    # 自检当前骨架
    checks = selftest.check(result, a.output, a.work)
    with open(os.path.join(a.work, "selftest_report.json"), "w", encoding="utf-8") as f:
        json.dump({"ok": True, "checks": checks, "summary": selftest.summarize(checks)},
                  f, ensure_ascii=False, indent=2)

    summary = {
        "ok": True,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "materials_found": len(materials),
        "skeleton": os.path.join(a.output, "result.json"),
        "phase": result.get("runtime", {}).get("phase"),
    }
    print(json.dumps(summary, ensure_ascii=False))

    hard_fail = any(c["status"] == "fail" for c in checks)
    return 1 if (a.strict and hard_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
