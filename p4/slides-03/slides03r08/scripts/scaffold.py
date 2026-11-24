#!/usr/bin/env python3
"""scaffold.py — 读取案例输入，尽早落盘 result.json 骨架；支持 --update 增量合并。

职责：
  1) 容错读取 /input/case.json 与 /input/task.md（BOM / 坏编码 / 坏 JSON 不崩溃）；
  2) 确定 case_id 与输出契约（case.json 含 output_contract 时原样保留全部键）；
  3) 发现材料文件（单文件超 20MB 跳过并记录到 degradations）；
  4) 写出 status=partial 的合法 result.json 骨架。

仅依赖 Python 标准库。用法：
  python3 scaffold.py --input /input/case.json --task /input/task.md --work /app/work --output /app/output
  python3 scaffold.py --update --result /app/output/result.json --patch patch.json --note "填入 pages"
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
import sys

DEFAULT_CONTRACT = {
    "case_id": None,
    "report_goal": None,
    "audience": None,
    "narrative": None,
    "pages": [],
    "editable_deck": None,
    "evidence_map": [],
    "qa_prep": [],
    "rendering": None,
}

META_FIELDS = [
    "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime",
]

MATERIAL_EXTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".pptx", ".ppt", ".odp", ".docx", ".doc", ".md", ".markdown",
    ".txt", ".tex", ".csv", ".tsv", ".xlsx", ".xls", ".html",
}

MAX_MATERIAL_BYTES = 20 * 1024 * 1024


def read_text(path: str) -> str:
    """按 utf-8-sig 读取，失败依次退回 gb18030 / latin-1，绝不抛异常。"""
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def load_json(path: str):
    """容错读取 JSON；坏 JSON / 缺失返回 None。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def discover_materials(roots):
    """扫描目录，返回 (材料清单, 跳过清单)；跳过超大文件。"""
    found = []
    skipped = []
    seen = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn in ("result.json", "selftest_report.json"):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in MATERIAL_EXTS:
                    continue
                full = os.path.abspath(os.path.join(dirpath, fn))
                if full in seen:
                    continue
                seen.add(full)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if size > MAX_MATERIAL_BYTES:
                    skipped.append({"path": full, "reason": "oversize", "bytes": size})
                    continue
                found.append({
                    "path": full,
                    "name": fn,
                    "type": ext.lstrip("."),
                    "bytes": size,
                    "read": False,
                })
    found.sort(key=lambda x: x["path"])
    skipped.sort(key=lambda x: x["path"])
    return found, skipped


def determine_case_id(case, contract):
    if isinstance(contract, dict):
        for key in ("case_id", "id", "armiId"):
            if contract.get(key):
                return contract[key]
    if isinstance(case, dict):
        for key in ("id", "armiId", "case_id"):
            if case.get(key):
                return case[key]
    return None


def merge_contract(contract):
    """保留给定契约全部键；缺失的默认契约键补 null/[]。"""
    if not isinstance(contract, dict):
        contract = copy.deepcopy(DEFAULT_CONTRACT)
    out = copy.deepcopy(contract)
    for key, default in DEFAULT_CONTRACT.items():
        if key not in out:
            out[key] = copy.deepcopy(default)
    return out


def build_skeleton(case, contract, materials, skipped):
    contract = merge_contract(contract)
    case_id = determine_case_id(case, contract) or contract.get("case_id")
    contract["case_id"] = case_id
    skeleton = copy.deepcopy(contract)
    skeleton["status"] = "partial"
    skeleton["sources"] = materials
    skeleton["uncertainty"] = []
    skeleton["pending_human"] = []
    skeleton["errors"] = []
    skeleton["degradations"] = []
    if skipped:
        skeleton["degradations"].append({
            "kind": "oversize_materials_skipped",
            "detail": skipped,
        })
    skeleton["runtime"] = {
        "started_at": now_iso(),
        "budget_seconds": 900,
        "budget_requests": 64,
        "scaffold_generated_at": now_iso(),
    }
    skeleton["change_log"] = [{
        "time": now_iso(),
        "action": "scaffold",
        "scope": "result.json 骨架落盘",
        "detail": "status=partial；output_contract 字段已保留；sources 已列出",
    }]
    return skeleton


def deep_merge(base, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--result", default=None,
                    help="直接指定 result.json 路径（默认 --output/result.json）")
    ap.add_argument("--update", action="store_true", help="增量合并模式")
    ap.add_argument("--patch", default=None, help="增量合并的 JSON patch 文件")
    ap.add_argument("--note", default="incremental update", help="change_log 动作说明")
    args = ap.parse_args(argv)

    result_path = args.result or os.path.join(args.output, "result.json")

    if args.update:
        base = load_json(result_path)
        if base is None:
            print(json.dumps({"ok": False, "error": "cannot read result for update"},
                             ensure_ascii=False))
            return 2
        patch = {}
        if args.patch:
            patch = load_json(args.patch) or {}
        else:
            raw = sys.stdin.read()
            try:
                patch = json.loads(raw) if raw.strip() else {}
            except ValueError:
                patch = {}
        deep_merge(base, patch)
        base.setdefault("change_log", []).append({
            "time": now_iso(),
            "action": "update",
            "scope": args.note,
            "detail": ("合并键：" + ", ".join(sorted(patch.keys()))) if patch else "（无新键）",
        })
        write_json(result_path, base)
        print(json.dumps({"ok": True, "mode": "update", "result": result_path,
                          "merged_keys": sorted(patch.keys())}, ensure_ascii=False))
        return 0

    case = load_json(args.input)
    if not isinstance(case, dict):
        case = {}
    task_text = read_text(args.task)
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else None
    input_dir = os.path.dirname(os.path.abspath(args.input))
    materials, skipped = discover_materials([
        input_dir,
        args.work,
        os.path.join(input_dir, "materials"),
    ])
    skeleton = build_skeleton(case, contract, materials, skipped)
    write_json(result_path, skeleton)
    print(json.dumps({
        "ok": True,
        "mode": "scaffold",
        "result": result_path,
        "case_id": skeleton.get("case_id"),
        "has_output_contract": isinstance(case.get("output_contract"), dict),
        "materials_found": len(materials),
        "materials_skipped": len(skipped),
        "status": skeleton["status"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
