#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scaffold.py —— 输入发现 + result.json 骨架生成（纯标准库）。

在正式分析前先落盘一版合法 partial 结果：任何中途异常都不会留下空产物。
用法：
    python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import sys
import time
import hashlib

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE = 2 * 1024 * 1024  # 2MB：超限跳过并记录

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

# 无 output_contract 时的兜底契约（思维导图任务最小字段）。
DEFAULT_CONTRACT = {
    "mindmap": None,
    "evidence_map": None,
    "artifacts": None,
    "summary": None,
}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    """utf-8-sig 读取并容错：坏编码降级为 errors='replace'，不崩溃。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, "unreadable: %s" % e, 0
    if len(raw) > MAX_FILE:
        return None, "skipped: >%d bytes" % MAX_FILE, len(raw)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None, len(raw)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded with errors=replace", len(raw)


def iter_files(base):
    """扫描目录，返回 [relpath, abspath]；目录不存在返回空列表。"""
    out = []
    if not os.path.isdir(base):
        return out
    for dirpath, _dirs, files in os.walk(base):
        for fn in sorted(files):
            abs_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_path, base)
            out.append((rel, abs_path))
    return out


def discover_case_id(case_data, task_text):
    if isinstance(case_data, dict):
        for k in ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId"):
            v = case_data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    if task_text:
        import re
        m = re.search(r"challenge[-_](?!id\b)[A-Za-z0-9]+", task_text)
        if m:
            return m.group(0)
        m = re.search(r"case[-_ ]?id['\"]?\s*[:：]\s*['\"]?([A-Za-z0-9._-]+)", task_text, re.I)
        if m:
            return m.group(1)
    return None


def discover_contract(case_data):
    """从 case.json 中找 output_contract（对象或列表），返回 (contract_dict_or_None, path)。"""
    if not isinstance(case_data, dict):
        return None, None
    for key in ("output_contract", "outputContract", "output_schema", "result_schema", "contract"):
        if key in case_data:
            val = case_data[key]
            if isinstance(val, dict):
                return val, key
            if isinstance(val, list):
                # 列表形式：每个字段名回填 null
                d = {}
                for item in val:
                    if isinstance(item, str):
                        d[item] = None
                    elif isinstance(item, dict) and "name" in item:
                        d[item["name"]] = None
                return d, key
    # 也可能嵌在 content/运行时契约文本里，但这里只做结构发现；找不到交给 Agent。
    return None, None


def build_skeleton(case_id, contract, sources, notes):
    skeleton = {
        "case_id": case_id or "unknown",
        "status": "partial",
        "sources": sources,
        "uncertainty": [],
        "change_log": [
            {
                "ts": now_iso(),
                "step": "scaffold",
                "summary": "initialized honest partial skeleton before analysis",
            }
        ],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": now_iso(), "seconds": 0},
        "artifacts": {
            "mindmap_svg": None,
            "mindmap_pdf": None,
            "mindmap_mm": None,
            "mindmap_md": None,
            "mindmap_json": None,
            "preview_png": None,
        },
        "input_notes": notes,
    }
    # 回填 output_contract 全部键：不改名、不删未知键；与标准字段冲突时保留标准字段的值。
    if contract:
        for k in contract:
            skeleton.setdefault(k, None)
    return skeleton


def main():
    ap = argparse.ArgumentParser(description="scaffold result.json skeleton")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    # 1. 扫描输入目录
    files = iter_files(args.input)
    case_data = None
    task_text = None
    sources = []
    notes = []
    case_path = None

    for rel, abs_path in files:
        text, err, size = read_text(abs_path)
        role = "material"
        if rel.endswith("case.json") or rel == "case.json":
            role = "case"
        elif rel.endswith("task.md") or rel == "task.md":
            role = "task"
        elif "materials" in rel.replace("\\", "/").split("/") or rel.startswith("materials"):
            role = "material"
        sources.append({"path": os.path.join(args.input, rel), "role": role, "bytes": size})

        if err:
            notes.append({"path": rel, "issue": err})
        if text is None:
            continue

        if role == "case":
            try:
                case_data = json.loads(text)
            except json.JSONDecodeError as e:
                notes.append({"path": rel, "issue": "bad json: %s" % e})
                case_data = None
            case_path = rel
        elif role == "task":
            task_text = text

    # 2. 解析 case_id 与 output_contract
    case_id = discover_case_id(case_data, task_text)
    contract, contract_path = discover_contract(case_data)
    if contract is None:
        contract = dict(DEFAULT_CONTRACT)
        notes.append({"path": "output_contract", "issue": "not found in case.json; using DEFAULT_CONTRACT"})

    # 3. 写 result.json 骨架（先写骨架，保证落盘）
    skeleton = build_skeleton(case_id, contract, sources, notes)
    result_path = os.path.join(args.output, "result.json")
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(skeleton, f, ensure_ascii=False, indent=2)
    except OSError as e:
        # 写盘失败：尽力写最小合法产物到 /tmp 不崩溃
        fallback = os.path.join(args.work, "result.json")
        with open(fallback, "w", encoding="utf-8") as f:
            json.dump({"case_id": case_id or "unknown", "status": "partial",
                       "errors": ["write output failed: %s" % e],
                       "change_log": [], "sources": [], "uncertainty": [],
                       "pending_human": [], "runtime": {"started_at": now_iso(), "seconds": 0},
                       "artifacts": {}}, f, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": False, "result_path": fallback, "case_id": case_id,
                          "contract_keys": sorted(contract) if contract else [],
                          "warning": "output write failed: %s" % e}, ensure_ascii=False))
        return 0

    summary = {
        "ok": True,
        "result_path": result_path,
        "case_id": case_id or "unknown",
        "contract_path": contract_path,
        "contract_keys": sorted(contract) if contract else [],
        "input_file_count": len(sources),
        "notes": notes,
        "next": "write /app/work/mindmap.json then run render_mindmap.py",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
