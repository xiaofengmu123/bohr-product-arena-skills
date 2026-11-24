#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 0 — 先落盘一版合法 partial 结果骨架。

目的：在 900s/64 请求预算耗尽或中途异常时，/app/output/result.json 始终存在且可解析。
之后所有阶段只通过更新该文件做增量改进，不推倒重写。

用法：
  python scripts/skeleton.py [--input /input] [--out /app/output] [--work /app/work]
仅依赖 Python 标准库；平台路径缺失时自动回退到 ./input ./output ./work。
"""
import argparse
import json
import os
import sys
import time


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def resolve_dir(kind):
    """input/output/work 目录：优先平台路径，缺失回退本地相对路径。"""
    plat = {"input": "/input", "output": "/app/output", "work": "/app/work"}[kind]
    local = {"input": "input", "output": "output", "work": "work"}[kind]
    if os.path.isdir(plat):
        return plat
    return local


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        return {"__error__": str(exc)}


def first_text(path, max_chars=3000):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read(max_chars)
    except OSError:
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=None, help="输入目录（默认 /input 或 ./input）")
    ap.add_argument("--out", default=None, help="输出目录（默认 /app/output 或 ./output）")
    ap.add_argument("--work", default=None, help="工作目录（默认 /app/work 或 ./work）")
    a = ap.parse_args(argv)

    indir = a.input or resolve_dir("input")
    outdir = a.out or resolve_dir("output")
    workdir = a.work or resolve_dir("work")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(workdir, exist_ok=True)

    case = read_json(os.path.join(indir, "case.json"))
    if not isinstance(case, dict) or "__error__" in case:
        case = {}

    case_id = None
    for key in ("case_id", "caseId", "id"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            case_id = val.strip()
            break
    if not case_id:
        case_id = "unknown-case"

    # 题目 output_contract / output_schema / required_fields 原样保留为最高优先级提示
    contract_hint = {}
    for key in ("output_contract", "output_schema", "required_fields"):
        if key in case:
            contract_hint[key] = case[key]

    task_head = first_text(os.path.join(indir, "task.md"))
    started = utcnow()

    result = {
        "case_id": case_id,
        "status": "partial",
        "artifact": {
            "path": None,
            "kind": None,
            "description": "待补：正式七要素章节稿尚未生成"
        },
        "fact_source_map": [],
        "element_map": {},
        "consistency_matrix": {"mapping": [], "issues": []},
        "sources": [],
        "uncertainty": [
            {"item": "初始骨架", "reason": "Step 0 先落盘，尚未完整读取材料"}
        ],
        "pending_human": [],
        "change_log": [
            {
                "ts": started,
                "action": "skeleton",
                "reason": "预算前 3 分钟内先落盘合法 partial 结果",
                "impact": "保证中途异常时产物仍存在且可解析"
            }
        ],
        "version_boundary": {
            "modification_diff": [],
            "manual_confirm": [],
            "material_gaps": [],
            "anomalies": []
        },
        "contract_hint": contract_hint,
        "runtime": {
            "started_at": started,
            "finished_at": None,
            "elapsed_s": None,
            "requests_used": None,
            "budget_requests": 64,
            "budget_seconds": 900
        }
    }

    out_path = os.path.join(outdir, "result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    meta = {
        "case_id": case_id,
        "started_at": started,
        "input_dir": indir,
        "output_dir": outdir,
        "work_dir": workdir,
        "contract_hint_keys": list(contract_hint.keys()),
        "task_head_chars": len(task_head)
    }
    with open(os.path.join(workdir, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "out": out_path,
        "contract_keys": list(contract_hint.keys())
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
