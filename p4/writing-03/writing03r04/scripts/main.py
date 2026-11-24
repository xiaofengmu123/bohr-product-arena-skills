#!/usr/bin/env python3
"""盘点输入并落盘/增量合并 result.json 骨架。

仅依赖 Python 标准库。作为 /skills/weekly-skill 的入口脚本之一，
负责在任务早期（前 3 分钟）产出一版合法 partial 结果，保证中途异常
也不会留下不完整产物。幂等：若 result.json 已存在则增量合并，不整体重建。

用法:
  python scripts/main.py --case /input/case.json --task /input/task.md \
      --out /app/output --work /app/work --material-root /input --material-root /app/work
"""
import argparse
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_MATERIAL_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限，超限跳过并记录
EXCLUDE_SOURCE_NAMES = {"case.json", "task.md", "result.json", "verification_report.json", "change_log.json"}


def _now_utc():
    # fromtimestamp(..., tz=utc) 在 Python 3.x 全版本可用，避免 timezone.utc 的版本差异
    return datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat(timespec="seconds")


def read_text_robust(path):
    """以 BOM/坏编码容错方式读文本。返回 (text, errors)；绝不抛异常。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, ["read-failed: %s" % exc]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError as exc:
            continue
    return raw.decode("utf-8", errors="replace"), ["decode-fallback"]


def read_json_robust(path):
    """返回 (data, errors)；绝不抛异常。"""
    text, errors = read_text_robust(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, errors + ["json-invalid: %s" % exc]


def discover_dir(root):
    """列出 root 下普通文件，附带大小与超限标记。"""
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            entry = {
                "path": path,
                "name": fn,
                "rel": os.path.relpath(path, root),
                "bytes": size,
            }
            if size is not None and size > MAX_MATERIAL_BYTES:
                entry["skipped"] = "over_size_limit"
            out.append(entry)
    return out


def build_material_inventory(roots):
    inventory = []
    seen = set()
    for root in roots:
        for entry in discover_dir(root):
            key = os.path.abspath(entry["path"])
            if key in seen:
                continue
            seen.add(key)
            inventory.append(entry)
    return inventory


def build_sources(inventory):
    sources = []
    for entry in inventory:
        if entry.get("skipped"):
            continue
        if entry["name"].lower() in EXCLUDE_SOURCE_NAMES:
            continue
        sources.append({
            "id": "src-%03d" % (len(sources) + 1),
            "type": "material",
            "title": entry["name"],
            "path": entry["path"],
            "doi": None,
            "accessed": _now_utc(),
            "locator": None,
            "verification": "unverified",
        })
    return sources


def fallback_contract():
    return {
        "deliverable": {"content": "", "artifact_path": [], "description": ""},
        "fact_source_map": [],
        "version_boundary": {
            "changes": [],
            "pending_human": [],
            "material_gaps": [],
            "exceptions": [],
        },
    }


def build_skeleton(case_data, case_errors, inventory, sources):
    now = _now_utc()
    case_id = None
    output_contract = None
    if isinstance(case_data, dict):
        case_id = case_data.get("case_id") or case_data.get("id") or case_data.get("caseId")
        output_contract = case_data.get("output_contract")

    if case_data is None or case_id is None:
        status = "input_error"
    else:
        status = "partial"

    contract_fields = output_contract if isinstance(output_contract, dict) else fallback_contract()

    skeleton = {
        "case_id": case_id,
        "status": status,
        "output_contract": contract_fields,
    }
    # 把 output_contract 全部字段提升到顶层，满足“含 output_contract 全部字段”的契约要求
    for key, value in contract_fields.items():
        skeleton.setdefault(key, value)

    skeleton.setdefault("sources", sources)
    skeleton.setdefault("uncertainty", {
        "claims_requiring_confirmation": [],
        "conflicting_specs": [],
        "missing_assets": [],
        "material_gaps": [],
        "not_verifiable": [],
        "degradations": [],
        "limitations": [],
    })
    skeleton.setdefault("pending_human", [])
    skeleton.setdefault("errors", list(case_errors or []))
    skeleton.setdefault("degradations", [])
    skeleton.setdefault("material_inventory", inventory)
    skeleton.setdefault("runtime", {
        "started_at": now,
        "budget": {"seconds": 900, "model_requests": 64, "max_tokens_per_response": 16384},
        "steps": [],
    })
    skeleton.setdefault("change_log", [{
        "ts": now,
        "action": "scaffold",
        "detail": "初始骨架落盘；status=%s" % status,
    }])
    return skeleton


def merge_result(existing, skeleton):
    """增量合并：保留已有字段，仅填充缺失字段；change_log 只追加一次 scaffold。"""
    if not isinstance(existing, dict):
        return skeleton
    out = dict(existing)
    for key, value in skeleton.items():
        if key not in out or out[key] in (None, "", [], {}):
            out[key] = value
    if not isinstance(out.get("runtime"), dict) or not out["runtime"].get("started_at"):
        out["runtime"] = skeleton.get("runtime", {})
    change_log = out.get("change_log")
    if not isinstance(change_log, list):
        change_log = list(skeleton.get("change_log", []))
    elif not any(isinstance(e, dict) and e.get("action") == "scaffold" for e in change_log):
        change_log = change_log + list(skeleton.get("change_log", []))
    out["change_log"] = change_log
    return out


def atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="盘点输入并落盘/增量合并 result.json 骨架。")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--material-root", action="append", default=[])
    args = ap.parse_args(argv)

    result_path = os.path.join(args.out, "result.json")

    try:
        os.makedirs(args.out, exist_ok=True)
        os.makedirs(args.work, exist_ok=True)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": "makedirs-failed: %s" % exc, "result_path": result_path},
                         ensure_ascii=False))
        return 1

    roots = list(args.material_root) or ["/input", args.work]
    inventory = build_material_inventory(roots)
    sources = build_sources(inventory)

    case_data, case_errors = read_json_robust(args.case)
    read_text_robust(args.task)  # 仅用于盘点，不阻塞

    skeleton = build_skeleton(case_data, case_errors, inventory, sources)

    existing = None
    if os.path.exists(result_path):
        existing, _ = read_json_robust(result_path)
    merged = merge_result(existing, skeleton)

    try:
        atomic_write(result_path, merged)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": "write-failed: %s" % exc, "result_path": result_path},
                         ensure_ascii=False))
        return 1

    print(json.dumps({
        "ok": True,
        "result_path": result_path,
        "status": merged.get("status"),
        "case_id": merged.get("case_id"),
        "material_files": len(inventory),
        "sources": len(sources),
        "errors": case_errors or [],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
