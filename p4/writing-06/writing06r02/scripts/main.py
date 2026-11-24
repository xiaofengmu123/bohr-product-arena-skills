#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-structure-review Skill 入口。

流程：skeleton（立即落盘 partial 骨架）→ evidence（抽取候选锚点）→ verify（契约校验）。
三步互相独立，任何一步失败都保留已落盘的合法 partial result.json，不崩溃、不伪造。

调用（相对路径，以 Skill 根目录为工作目录）：
    python scripts/main.py --case /input/case.json --task /input/task.md \
        --input /input --work /app/work --output /app/output

环境变量可覆盖默认路径：CASE_JSON、TASK_MD、INPUT_DIR、WORK_DIR、OUTPUT_DIR、RESULT_JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def _default(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="paper-structure-review: 落盘骨架、抽取证据、校验契约")
    ap.add_argument("--case", default=_default("CASE_JSON", "/input/case.json"))
    ap.add_argument("--task", default=_default("TASK_MD", "/input/task.md"))
    ap.add_argument("--input", default=_default("INPUT_DIR", "/input"))
    ap.add_argument("--work", default=_default("WORK_DIR", "/app/work"))
    ap.add_argument("--output", default=_default("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--result", default=None,
                    help="默认取 --output/result.json；可用环境变量 RESULT_JSON 覆盖")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    result_path = a.result or os.environ.get("RESULT_JSON") or os.path.join(a.output, "result.json")
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.output, exist_ok=True)
    report = {"ok": True, "steps": [], "errors": []}
    try:
        sk = core.ensure_skeleton(a.case, a.task, a.output, result_path)
        report["steps"].append({"step": "skeleton", **sk})
    except Exception as exc:  # noqa: BLE001 —— 写盘失败也不能崩
        report["ok"] = False
        report["errors"].append("skeleton failed: %r" % exc)
    try:
        ev = core.extract_evidence(a.input, a.work)
        report["steps"].append({"step": "evidence", **ev})
        report["errors"].extend(ev.get("errors") or [])
    except Exception as exc:  # noqa: BLE001
        report["errors"].append("evidence failed: %r" % exc)
        report["steps"].append({"step": "evidence", "ok": False})
    try:
        vf = core.verify_contract(result_path, a.input)
        report["steps"].append({"step": "verify", **vf})
        report["errors"].extend(vf.get("errors") or [])
        if vf.get("warnings"):
            report.setdefault("warnings", []).extend(vf["warnings"])
    except Exception as exc:  # noqa: BLE001
        report["errors"].append("verify failed: %r" % exc)
        report["steps"].append({"step": "verify", "ok": False})

    report["ok"] = report["ok"] and not report["errors"]
    # 始终以退出码 0 结束：partial 骨架本身就是合法交付，Harness 读取文件判定结果。
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
