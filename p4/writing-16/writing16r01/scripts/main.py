#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S0 脚手架：读取输入 → 尽早落盘 result.json 骨架 → 生成事实清单。

用法：
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --help

退出码恒为 0（空输入、坏 JSON、写盘失败均写合法 result.json 并如实标注）。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence_lib as ev

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def load_json(path):
    """容错读 JSON，返回 (obj_or_None, degradation_or_None)。"""
    p = Path(path)
    if not p.exists():
        return None, "missing"
    text, deg = ev.read_text(p)
    if text is None:
        return None, deg or "unreadable"
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, f"bad_json:{type(e).__name__}"


def default_for_spec(spec):
    """按 output_contract 的字段规格给缺省值；缺值一律 null/[]，不编造。"""
    if isinstance(spec, dict):
        if spec.get("type") == "array" or "items" in spec:
            return []
        if spec.get("type") == "object" and isinstance(spec.get("properties"), dict):
            return {k: default_for_spec(v) for k, v in spec["properties"].items()}
        if "enum" in spec and isinstance(spec.get("enum"), list):
            return spec["enum"][0] if spec["enum"] else None
        return None
    if isinstance(spec, list):
        return []
    return None


def build_skeleton(case_obj, contract):
    """构造 result.json 骨架：case_id + 全部 output_contract 键 + 附加键。"""
    case_id = None
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("id") or case_obj.get("task_id")
    skel = {"case_id": case_id}
    if isinstance(contract, dict):
        for k in contract:
            skel[k] = default_for_spec(contract[k])
    skel.update({
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "artifacts": {},
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": ev.now_iso(), "seconds": 0, "notes": "skeleton; fill incrementally"},
    })
    return skel


def main(argv=None):
    ap = argparse.ArgumentParser(description="科研成果转化写作 Skill 脚手架")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--case", default=None, help="可选：直接指定 case.json 路径")
    args = ap.parse_args(argv)

    t0 = time.time()
    outdir = Path(args.output)
    workdir = Path(args.work)
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    errors, degradations = [], []

    case_path = args.case or (Path(args.input) / "case.json")
    case_obj, cdeg = load_json(case_path)
    if cdeg:
        degradations.append(f"case.json: {cdeg}")

    contract = case_obj.get("output_contract") if isinstance(case_obj, dict) else None
    case_id = None
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("id") or case_obj.get("task_id")

    task_text, _ = ev.read_text(Path(args.input) / "task.md")
    if task_text is None:
        degradations.append("task.md: missing")

    files = ev.discover_files(args.input)
    CONTROL_NAMES = {"case.json", "task.md"}
    material_files = [f for f in files if f.name.lower() not in CONTROL_NAMES]
    facts, fdeg = ev.build_fact_inventory(files, args.input)
    degradations.extend(fdeg)

    # 事实清单写入 work（确定性，供 Agent 溯源）
    try:
        (workdir / "fact_inventory.json").write_text(
            json.dumps({"case_id": case_id, "generated_at": ev.now_iso(),
                        "file_count": len(files), "facts": facts, "degradations": degradations},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError as e:
        errors.append(f"work write failed: {e}")

    if not isinstance(case_obj, dict):
        skel = {
            "case_id": case_id,
            "status": "input_error",
            "sources": [], "uncertainty": [], "change_log": [], "artifacts": {},
            "errors": ["case.json missing or unreadable"],
            "degradations": degradations, "pending_human": [],
            "runtime": {"started_at": ev.now_iso(), "seconds": 0},
        }
    else:
        skel = build_skeleton(case_obj, contract)
        skel["degradations"] = degradations
        skel["errors"] = errors
        if not material_files:
            skel["status"] = "insufficient_evidence"
            skel["pending_human"].append("未发现材料文件（case.json/task.md 之外）；无法完成改写，请确认材料路径")

    skel["runtime"]["seconds"] = round(time.time() - t0, 2)

    # 尽早落盘 result.json 骨架
    result_path = outdir / "result.json"
    try:
        result_path.write_text(json.dumps(skel, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        minimal = {"case_id": case_id, "status": "input_error",
                   "sources": [], "uncertainty": [], "change_log": [],
                   "errors": [f"cannot write result.json: {e}"], "degradations": [],
                   "pending_human": [], "runtime": {"seconds": round(time.time() - t0, 2)}}
        print(json.dumps(minimal, ensure_ascii=False), file=sys.stderr)
        return 0

    summary = {
        "case_id": case_id,
        "status": skel["status"],
        "output_contract_keys": list(contract.keys()) if isinstance(contract, dict) else [],
        "material_files": [ev.relpath(f, args.input) for f in material_files],
        "fact_count": len(facts),
        "result": str(result_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
