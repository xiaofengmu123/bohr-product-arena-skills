#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 实验方案可行性审查入口（薄编排器）。

职责：在审查最初时刻扫描 /input，构建中间表示 case_record，并立即写出一份
"结构合法、内容诚实"的 partial result.json 到 /app/output/result.json，
保证后续任何中途异常都不会留下不完整产物。真正的逻辑都在 core_impl.py。

调用：
  python3 scripts/main.py --input /input --output /app/output --work /app/work
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 允许相对路径导入（在 Skill 根目录或 scripts/ 下执行均可）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as engine  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError as exc:
        print("warn: mkdir %s failed: %s" % (path, exc), file=sys.stderr)
        return False


def write_json(path, payload):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return True
    except OSError as exc:
        print("warn: write %s failed: %s" % (path, exc), file=sys.stderr)
        return False


def run(input_dir, output_dir, work_dir):
    result = None
    files, notes = engine.discover_inputs(input_dir)
    if not files:
        # 空输入目录：仍要写合法结果，状态 insufficient_evidence。
        record = engine.build_case_record(input_dir, files, {}, notes)
        result = engine.make_skeleton(record)
        result["status"] = "insufficient_evidence"
        result["uncertainty"] = ["输入目录无任何可读文件"]
        result["pending_human"] = ["请提供 case.json、task.md 及科研材料"]
        result["change_log"].append({
            "step": "skeleton", "action": "输入目录为空",
            "reason": "无任何可读输入 → insufficient_evidence"})
    else:
        data, err = engine.load_case(input_dir, files)
        if data is None:
            record = engine.build_case_record(input_dir, files, {}, notes)
            result = engine.make_skeleton(record)
            result["status"] = "input_error"
            result["errors"].append({"kind": "case_load", "message": err})
            result["change_log"].append({
                "step": "skeleton", "action": "case.json 无法解析",
                "reason": "case.json 无法解析 → input_error"})
        else:
            record = engine.build_case_record(input_dir, files, data, notes)
            result = engine.make_skeleton(record)

    # 输入清单（供 Agent 阅读）。
    inventory = {
        "input_dir": input_dir,
        "files": [{"relpath": f["relpath"], "size": f["size"],
                   "ext": f["ext"], "status": f["status"]} for f in files],
        "notes": notes,
    }

    ensure_dir(work_dir)
    ensure_dir(output_dir)
    write_json(os.path.join(work_dir, "case_record.json"), record)
    write_json(os.path.join(work_dir, "input_inventory.json"), inventory)
    write_json(os.path.join(output_dir, "result.json"), result)

    print(json.dumps({
        "status": result["status"],
        "case_id": result["case_id"],
        "input_files": len(files),
        "contract_keys": len(record.get("output_contract") or {}),
        "notes": notes[:8],
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="实验方案可行性审查入口")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)
    return run(args.input, args.output, args.work)


if __name__ == "__main__":
    raise SystemExit(main())