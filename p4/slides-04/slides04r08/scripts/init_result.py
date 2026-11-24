#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init_result.py — 落盘 partial 结果骨架（幂等合并），仅依赖标准库。

在限时任务最开始（前 3 分钟）运行一次：
  1. 扫描 /input，发现 case.json / task.md 与材料目录，不假设固定文件名；
  2. 提取 case_id，并按 case.json 里 output_contract 原样回填全部键；
  3. 在 /app/output/result.json 写 status=in_progress 的完整骨架，
     所有契约字段以 null/[] 占位，后续步骤只做增量填充；
  4. 幂等：已存在且非空的字段不被覆盖，只补齐缺失键。

退出码：
  0 成功（输入为空/坏 JSON 也写出合法骨架，不崩溃）
  2 参数错误
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SIZE_CAP = 2 * 1024 * 1024  # 单文件 2MB，超限跳过并记录


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, cap=SIZE_CAP):
    """utf-8-sig 读取并容错；不存在/超限返回 None。"""
    p = Path(path)
    if not p.is_file():
        return None
    if p.stat().st_size > cap:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeError:
            continue
    return None


def read_json(path):
    txt = read_text(path)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except (json.JSONDecodeError, ValueError):
        return None


def discover(input_dir):
    """扫描输入目录，返回 {case_json, task_md, materials}。"""
    root = Path(input_dir)
    found = {"case_json": None, "task_md": None, "materials": []}
    if not root.is_dir():
        return found
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        name = entry.name.lower()
        if name == "case.json" and found["case_json"] is None:
            found["case_json"] = str(entry)
        elif name == "task.md" and found["task_md"] is None:
            found["task_md"] = str(entry)
        elif name not in ("case.json", "task.md") and name.endswith(
            (".pdf", ".pptx", ".docx", ".png", ".jpg", ".jpeg", ".svg", ".csv",
             ".json", ".md", ".txt")
        ):
            found["materials"].append(str(entry))
    return found


def extract_case_id(data):
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


def fill_contract(node):
    """把 output_contract 结构原样回填：dict 递归、list→[]、其余→null。"""
    if isinstance(node, dict):
        return {k: fill_contract(v) for k, v in node.items()}
    if isinstance(node, list):
        return []
    return None


def base_skeleton(case_id, started_at):
    return {
        "case_id": case_id,
        "status": "in_progress",
        "presentation": {
            "pptx_path": None,
            "pdf_path": None,
            "png_paths": [],
            "slide_count": None,
            "page_plan": [],
        },
        "evidence_map": [],
        "render_record": {
            "source_files": [],
            "template_file": None,
            "layouts_used": [],
            "fonts": [],
            "exports": [],
            "integrity": {},
            "media": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "runtime": {
            "started_at": started_at,
            "finished_at": None,
            "elapsed_seconds": None,
            "model_requests": None,
        },
    }


def _empty(v):
    return v is None or v == "" or v == [] or v == {}


def merge(existing, skeleton):
    """增量合并：只补 existing 缺失/为空的值，已填值保留。"""
    if not isinstance(existing, dict):
        existing = {}
    for key, val in skeleton.items():
        if key not in existing:
            existing[key] = val
        elif isinstance(val, dict) and isinstance(existing[key], dict):
            existing[key] = merge(existing[key], val)
        elif _empty(existing[key]) and not _empty(val):
            existing[key] = val
    return existing


def main(argv=None):
    ap = argparse.ArgumentParser(description="落盘 partial 结果骨架（幂等合并）")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(args.work)
    work_dir.mkdir(parents=True, exist_ok=True)
    started_at = now_iso()

    found = discover(args.input)
    case_data = read_json(found["case_json"]) if found["case_json"] else None
    task_text = read_text(found["task_md"]) if found["task_md"] else None

    case_id = extract_case_id(case_data)
    if case_id is None and found["case_json"]:
        case_id = Path(found["case_json"]).parent.name or None

    skeleton = base_skeleton(case_id, started_at)

    # 回填 output_contract 全部键（不改名、不删键）
    if isinstance(case_data, dict) and isinstance(case_data.get("output_contract"), dict):
        skeleton = merge(skeleton, fill_contract(case_data["output_contract"]))

    # 状态诚实化：无 case_id 且无材料 → insufficient_evidence
    if case_id is None and not found["materials"]:
        skeleton["status"] = "insufficient_evidence"
        skeleton["uncertainty"] = [{
            "code": "missing_input",
            "detail": "no case_id and no material files found under input dir",
        }]

    sources = []
    if found["case_json"]:
        sources.append({"path": found["case_json"], "role": "case_input"})
    if found["task_md"]:
        sources.append({"path": found["task_md"], "role": "task_spec"})
    for m in found["materials"]:
        sources.append({"path": m, "role": "material"})
    skeleton["sources"] = sources
    skeleton["render_record"]["source_files"] = list(sources)

    result_path = out_dir / "result.json"
    existing = read_json(str(result_path)) or {}
    merged = merge(existing, skeleton)

    if not isinstance(merged.get("change_log"), list):
        merged["change_log"] = []
    if not any(isinstance(e, dict) and e.get("event") == "skeleton_init"
               for e in merged["change_log"]):
        merged["change_log"].append({
            "ts": started_at,
            "event": "skeleton_init",
            "detail": "partial skeleton written; incremental fills follow",
        })

    inv = {
        "ts": started_at,
        "input_dir": str(Path(args.input)),
        "case_json": found["case_json"],
        "task_md": found["task_md"],
        "materials": found["materials"],
        "case_id": case_id,
        "case_json_valid": (case_data is not None) if found["case_json"] else None,
        "task_md_readable": (task_text is not None) if found["task_md"] else None,
        "degradations": [],
    }
    if found["case_json"] and case_data is None:
        inv["degradations"].append("case.json unreadable or invalid JSON")
    if found["task_md"] and task_text is None:
        inv["degradations"].append("task.md unreadable or over size cap")

    try:
        inv_path = work_dir / "input_inventory.json"
        inv_path.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        inv_path = None

    try:
        result_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print("FATAL: cannot write result.json:", e, file=sys.stderr)
        return 1

    print(json.dumps({
        "result": str(result_path),
        "inventory": str(inv_path) if inv_path else None,
        "status": merged.get("status"),
        "case_id": merged.get("case_id"),
        "sources": len(sources),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
