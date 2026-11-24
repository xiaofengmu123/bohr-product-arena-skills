#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init_result.py — 输入发现 + 结果骨架（幂等、确定性）。

只依赖 Python 标准库。职责：
  1. 发现输入目录下的 case.json、task.md 与材料文件（不假设固定文件名/键名）；
  2. 提取 case_id；
  3. 按题目 output_contract（或内置三字段契约）先落一版合法 result.json 骨架，
     所有字段先行存在（空值/null/[]），后续只做增量填充；
  4. 幂等合并：已存在的非空值不被覆盖，只记录真实变更；
  5. 确定性：时间戳通过 --now 注入，序列化使用 canonical JSON（sort_keys=True）。

用法：
  python scripts/init_result.py --input /input --output /app/output --work /app/work
退出码：0=成功（输入目录缺失时仍写最小合法骨架，缺口记入 uncertainty）；
2=参数错误（argparse 默认）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

# 内置契约：题目没有显式 output_contract 时使用；与题目“三个契约字段”对齐。
DEFAULT_CONTRACT = ["presentation", "evidence_map", "render_record"]

PRESENTATION_SKELETON = {
    "pptx_path": None,
    "pdf_path": None,
    "png_paths": [],
    "slide_count": 0,
    "page_plan": [],
}

RENDER_RECORD_SKELETON = {
    "source_files": [],
    "template_file": None,
    "layouts_used": [],
    "fonts": [],
    "exports": [],
    "integrity": None,
    "media": [],
}

# evidence_map 是页级列表，骨架为空数组。
CONTRACT_SKELETONS = {
    "presentation": PRESENTATION_SKELETON,
    "render_record": RENDER_RECORD_SKELETON,
    "evidence_map": [],
}

EXT_TYPE = {
    ".pdf": "pdf", ".pptx": "pptx", ".potx": "pptx", ".docx": "docx",
    ".xlsx": "xlsx", ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".svg": "image", ".txt": "text", ".md": "text",
    ".csv": "data", ".json": "json",
}

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件读取上限，超限跳过并记录


def resolve_now(now_arg):
    """把 --now 解析为 UTC ISO 时间戳；'fixed' 是自检用的确定性哨兵。"""
    if now_arg in (None, "", "now"):
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if now_arg == "fixed":
        return "1970-01-01T00:00:00Z"
    return now_arg


def canonical_dumps(obj):
    """确定性 JSON 序列化：排序键、不转义非 ASCII、缩进 2、末尾换行。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def read_text(path, limit=MAX_FILE_BYTES):
    """utf-8-sig 容错读取；超限返回 None 并由调用方记录。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "unreadable"
    if size > limit:
        return None, "oversize"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, "unreadable:%s" % exc
    return None, "bad_encoding"


def read_json(path):
    """容错读 JSON：坏 JSON 返回 None，不抛异常。"""
    text, err = read_text(path)
    if err is not None:
        return None, err
    try:
        return json.loads(text), None
    except (ValueError, TypeError):
        return None, "bad_json"


def file_type(name):
    ext = os.path.splitext(name)[1].lower()
    return EXT_TYPE.get(ext, "other")


def sha8(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:8]
    except OSError:
        return None


def discover(input_dir):
    """递归清点输入目录；返回 (case_json, task_md, materials, degradations)。"""
    case_json = None
    task_md = None
    materials = []
    degradations = []
    if not os.path.isdir(input_dir):
        degradations.append({"code": "input_dir_missing", "detail": input_dir})
        return case_json, task_md, materials, degradations

    names = os.listdir(input_dir)
    if "case.json" in names:
        case_json = os.path.join(input_dir, "case.json")
    if "task.md" in names:
        task_md = os.path.join(input_dir, "task.md")

    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            path = os.path.join(root, fn)
            if path in (case_json, task_md):
                continue
            size = os.path.getsize(path) if os.path.isfile(path) else 0
            if size > MAX_FILE_BYTES:
                degradations.append({
                    "code": "file_oversize_skipped",
                    "path": path,
                    "size": size,
                })
                continue
            materials.append({
                "path": path,
                "type": file_type(fn),
                "size": size,
                "sha256_8": sha8(path),
            })

    materials.sort(key=lambda m: m["path"])
    return case_json, task_md, materials, degradations


def extract_case_id(case_obj):
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "armiId", "challenge_id"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return "unknown-case"


def extract_contract(case_obj):
    """题目显式 output_contract 优先；键名原样回填，未知键给 null。"""
    if isinstance(case_obj, dict):
        oc = case_obj.get("output_contract")
        if isinstance(oc, dict):
            keys = [k for k in oc.keys() if isinstance(k, str)]
            if keys:
                return keys
    return list(DEFAULT_CONTRACT)


def build_skeleton(case_id, contract_keys, sources, uncertainty, now):
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "sources": sources,
        "uncertainty": list(uncertainty),
        "change_log": [
            {
                "at": now,
                "event": "skeleton_created",
                "detail": "partial 骨架已落盘，后续只做增量填充",
            }
        ],
        # 兼容性字段，便于降级时标注
        "errors": [],
        "degradations": [],
        "pending_human": [],
    }
    for key in contract_keys:
        if key in CONTRACT_SKELETONS:
            import copy
            result[key] = copy.deepcopy(CONTRACT_SKELETONS[key])
        else:
            result[key] = None  # 未知契约键：原样保留键名，值给 null
    return result


def _is_empty(value):
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


def merge(existing, incoming):
    """只把 incoming 的非空值填进 existing 的空位；返回 (merged, changed_keys)。"""
    changed = []
    for key, value in incoming.items():
        if key not in existing:
            existing[key] = value
            changed.append(key)
        elif _is_empty(existing[key]) and not _is_empty(value):
            existing[key] = value
            changed.append(key)
    return existing, changed


def main(argv=None):
    parser = argparse.ArgumentParser(description="输入发现 + 结果骨架（幂等、确定性）")
    parser.add_argument("--input", default="/input", help="输入目录，默认 /input")
    parser.add_argument("--output", default="/app/output", help="产物目录，默认 /app/output")
    parser.add_argument("--work", default="/app/work", help="工作目录，默认 /app/work")
    parser.add_argument("--now", default="now",
                        help="时间戳注入；默认 now；'fixed' 为确定性自检哨兵")
    args = parser.parse_args(argv)

    now = resolve_now(args.now)
    case_json, task_md, materials, degradations = discover(args.input)

    case_obj = None
    case_err = None
    if case_json is not None:
        case_obj, case_err = read_json(case_json)
        if case_err is not None:
            degradations.append({"code": "case_json_%s" % case_err, "path": case_json})

    case_id = extract_case_id(case_obj)
    contract_keys = extract_contract(case_obj)

    sources = {
        "input": args.input,
        "case_json": case_json,
        "task_md": task_md,
        "materials": materials,
    }

    uncertainty = []
    for d in degradations:
        uncertainty.append({"code": d.get("code", "degradation"), "detail": d.get("detail") or d.get("path")})

    result = build_skeleton(case_id, contract_keys, sources, uncertainty, now)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    out_path = os.path.join(args.output, "result.json")
    existing = None
    if os.path.isfile(out_path):
        existing, exist_err = read_json(out_path)
        if exist_err is not None:
            uncertainty.append({"code": "prev_result_%s" % exist_err,
                                "detail": "重建骨架"})
            result["uncertainty"] = uncertainty
            existing = None

    if existing is not None and isinstance(existing, dict):
        merged, changed = merge(existing, result)
        if changed:
            merged["change_log"] = list(existing.get("change_log") or [])
            merged["change_log"].append({
                "at": now,
                "event": "merged",
                "detail": sorted(changed),
            })
        result = merged

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(canonical_dumps(result))

    inventory = {
        "case_id": case_id,
        "generated_at": now,
        "contract_keys": contract_keys,
        "case_json": case_json,
        "task_md": task_md,
        "materials": materials,
        "degradations": degradations,
    }
    inv_path = os.path.join(args.work, "input_inventory.json")
    with open(inv_path, "w", encoding="utf-8") as fh:
        fh.write(canonical_dumps(inventory))

    print("init_result: case_id=%s contract=%s materials=%d -> %s" % (
        case_id, ",".join(contract_keys), len(materials), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
