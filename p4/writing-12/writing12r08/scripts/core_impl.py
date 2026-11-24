#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core_impl.py — LaTeX 写作 Skill 的共享实现：发现、骨架、回填、自检。

仅依赖 Python 标准库。函数式、无全局状态，供 main.py 薄壳调用，
便于 selftest.py 与调用方在无 CLI 依赖下复用同一逻辑。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst", ".ltx",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")
CONTRACT_KEY_ALIASES = ("output_contract", "outputContract", "output_fields", "outputs")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio():
    """确保 stdout/stderr 为 UTF-8，避免非 UTF-8 终端下 json 输出崩溃。"""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path):
    """Read UTF-8 text tolerating BOM/encoding issues. Returns (text, error)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds 2MiB"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def read_json(path):
    """Tolerant JSON read. Returns (obj, errors_list)."""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[:exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def discover_input(root):
    """递归发现 /input 下文件，返回相对路径、大小、扩展名、是否文本。"""
    files = []
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "path": full,
                "rel": os.path.relpath(full, root).replace("\\", "/"),
                "size": size,
                "ext": ext,
                "is_text": ext in TEXT_EXTS,
            })
    return files


def find_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    return None


def _extract_keys_from_contract(contract):
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(list(item.keys()))
    return keys


def contract_keys(obj):
    """Return output_contract keys to echo back verbatim, preserving order."""
    if not isinstance(obj, dict):
        return []
    for alias in CONTRACT_KEY_ALIASES:
        keys = _extract_keys_from_contract(obj.get(alias))
        if keys:
            return keys
    return []


def _first_nonempty(obj, keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] not in (None, "", [], {}):
            return obj[k]
    return None


def build_skeleton(root):
    """Build result.json skeleton from /input/case.json. Returns (skeleton, keys)."""
    case_path = os.path.join(root, "case.json")
    notes = []
    case_id = None
    keys = []
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(obj)
        keys = contract_keys(obj)
        if not keys:
            notes.append("output_contract_not_found: metadata-only result")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    skeleton = {"case_id": case_id}
    for key in keys:
        skeleton[key] = None
    skeleton.update({
        "status": "partial" if keys else "input_error",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utcnow(),
            "step": "bootstrap",
            "note": "result skeleton written before analysis; contract fields default to null",
        }],
        "errors": notes,
        "degradations": [],
        "limitations": [],
        "runtime": {"started_at": utcnow(), "producer": "core_impl.py"},
    })
    return skeleton, keys


def _load_contract_keys(work_dir, fallback_input=None):
    inv_path = os.path.join(work_dir, "inventory.json")
    obj, _ = read_json(inv_path)
    if isinstance(obj, dict) and "contract_keys" in obj:
        return list(obj.get("contract_keys") or [])
    if fallback_input:
        case_obj, _ = read_json(os.path.join(fallback_input, "case.json"))
        if case_obj is not None:
            return contract_keys(case_obj)
    return []


def validate_result(result_path, work_dir, input_dir=None):
    """校验 result.json：JSON 合法性 + 必填键 + contract 键齐全。返回问题列表。"""
    problems = []
    obj, errs = read_json(result_path)
    if obj is None:
        return ["unreadable_result: " + "; ".join(errs)]
    problems.extend(errs)
    if not isinstance(obj, dict):
        return ["result_not_object"]
    for required in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if required not in obj:
            problems.append("missing_required_key:" + required)
    if obj.get("status") not in ALLOWED_STATUS:
        problems.append("invalid_status:" + str(obj.get("status")))
    keys = _load_contract_keys(work_dir, input_dir)
    for key in keys:
        if key not in obj:
            problems.append("missing_contract_key:" + key)
    for i, src in enumerate(obj.get("sources") or []):
        if not isinstance(src, dict) or "locator" not in src or "quote" not in src:
            problems.append("bad_source_entry:%d" % i)
    return problems


def finalize_result(result_path, work_dir, input_dir=None):
    """finalize：读入现有 result.json，补齐元数据并写回。返回最终对象。"""
    obj, _ = read_json(result_path)
    if not isinstance(obj, dict):
        obj = {}
    if "case_id" not in obj or obj["case_id"] in (None, "", []):
        obj["case_id"] = "unknown"
    if "status" not in obj or obj["status"] not in ALLOWED_STATUS:
        obj["status"] = "input_error"
    for key in ("sources", "uncertainty", "pending_human", "change_log",
                "errors", "degradations", "limitations"):
        if key not in obj or obj[key] is None:
            obj[key] = []
    obj["runtime"] = obj.get("runtime") or {}
    obj["runtime"]["finalized_at"] = utcnow()
    if "checks" not in obj:
        obj["checks"] = []
    problems = validate_result(result_path, work_dir, input_dir)
    obj["checks"] = list(dict.fromkeys((obj["checks"] or []) + problems))
    write_json(result_path, obj)
    return obj, problems


def index_evidence(root, out_path):
    """扫描 /input，为每个文本文件建立可定位的逐行索引（供 quote 使用）。"""
    index = {"root": root, "files": {}, "generated_at": utcnow()}
    for f in discover_input(root):
        if not f["is_text"] or f["size"] <= 0:
            continue
        text, err = read_text(f["path"])
        if text is None:
            continue
        lines = text.splitlines()
        index["files"][f["rel"]] = {
            "line_count": len(lines),
            "lines": lines,
            "error": err,
        }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_json(out_path, index)
    return index


def quote_evidence(file_path, start, end):
    """从材料中取 [start, end] 行（含边界，1-based）的逐字片段。返回 (text, meta)。"""
    text, err = read_text(file_path)
    if text is None:
        return None, {"error": err or "unreadable"}
    lines = text.splitlines()
    start = max(1, int(start))
    end = min(len(lines), int(end))
    if start > len(lines):
        return "", {"error": "start_beyond_eof", "line_count": len(lines)}
    selected = lines[start - 1:end]
    return "\n".join(selected), {
        "file": file_path,
        "start": start,
        "end": end,
        "line_count": len(lines),
        "decoded_with_replacement": bool(err),
    }


def normalize_status(obj):
    """根据已回填 contract 字段的完整度，推断状态（保守：未完成即 partial）。"""
    if not isinstance(obj, dict):
        return "input_error"
    if obj.get("errors"):
        return "input_error"
    if any(v is None for v in obj.values() if not isinstance(v, (list, dict))):
        return "partial"
    return "ok"


def discover_tex_files(root):
    return [f for f in discover_input(root) if f["ext"] in (".tex", ".ltx", ".bib", ".cls", ".sty")]
