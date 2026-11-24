#!/usr/bin/env python3
"""读取并归一化运行时输入，产出 input_summary.json。

只登记与读取文本；PDF 等二进制只登记不解析（由平台 pdf-text-tool 负责）。
产物含：case_id、原样回显的 output_contract、材料清单、检测到的约束与告警。
坏 JSON / 坏编码 / 超长文件都降级记录，不崩溃。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".xml", ".html"}
PDF_EXTS = {".pdf"}
MAX_TEXT_BYTES = 2 * 1024 * 1024      # 文本单文件读取上限，超限截断
MAX_PREVIEW_CHARS = 6000              # 摘要中每份材料的预览上限
MAX_FILE_BYTES = 20 * 1024 * 1024     # 任意文件登记上限，超限跳过并记录

DEFAULT_CONTRACT = ["deliverable", "facts_sources", "version_boundaries"]


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: str):
    """按 utf-8-sig → utf-8 → gb18030 → latin-1 容错读取，返回 (text, encoding)。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError:
            raise
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace"), "utf-8(replace)"


def load_json(path: str):
    """解析 JSON；坏 JSON 返回 (None, error_dict)。"""
    try:
        raw, _enc = read_text(path)
    except OSError as e:
        return None, {"file": os.path.basename(path), "error": "unreadable", "detail": str(e)}
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, {"file": os.path.basename(path), "error": "bad JSON", "detail": str(e)}


def inventory(input_dir: str):
    """扫描输入目录，返回 (items, warnings)。"""
    items, warnings = [], []
    if not os.path.isdir(input_dir):
        return items, [{"error": "input dir not found", "path": input_dir}]
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError as e:
                warnings.append({"file": rel, "error": "stat failed", "detail": str(e)})
                continue
            ext = os.path.splitext(fn)[1].lower()
            kind = "pdf" if ext in PDF_EXTS else ("text" if ext in TEXT_EXTS else "other")
            if size > MAX_FILE_BYTES:
                warnings.append({"file": rel, "issue": "oversize-skipped", "size": size})
                kind += ":skipped"
            items.append({"file": rel, "ext": ext, "kind": kind, "size": size})
    return items, warnings


def detect_constraints(text: str):
    """轻量检测受众/文体/格式约束关键词（只为提醒 Agent，不据此补造）。"""
    found = []
    for label, keys in {
        "audience": ["受众", "读者", "期刊", "audience", "journal"],
        "style": ["文体", "风格", "style", "tone"],
        "format": ["格式", "format", "word", "latex", "字数", "word count"],
        "language": ["中文", "英文", "双语", "bilingual", "language"],
    }.items():
        if any(k in text for k in keys):
            found.append(label)
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Normalize runtime input into input_summary.json")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input-dir", default="/input")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args(argv)

    os.makedirs(a.work, exist_ok=True)
    warnings = []

    case_obj, case_err = load_json(a.case)
    if case_err:
        case_obj = {}
        warnings.append(case_err)

    task_text = ""
    if os.path.exists(a.task):
        try:
            task_text, _ = read_text(a.task)
        except OSError as e:
            warnings.append({"file": os.path.basename(a.task), "error": "unreadable", "detail": str(e)})

    case_id = ""
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("caseId") or case_obj.get("id") or ""
    if not case_id:
        base = os.path.basename(a.case)
        case_id = base.rsplit(".", 1)[0] if base else "unknown"
        warnings.append({"issue": "case_id missing", "fallback": case_id})

    contract = None
    default_used = True
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract") or case_obj.get("outputContract")
    if isinstance(contract, dict) and contract:
        default_used = False
    else:
        contract = {k: None for k in DEFAULT_CONTRACT}
        warnings.append({"issue": "output_contract missing", "fallback": DEFAULT_CONTRACT})

    items, inv_warnings = inventory(a.input_dir)
    warnings.extend(inv_warnings)

    material_previews = {}
    for it in items:
        if it["kind"] != "text" or it["size"] > MAX_TEXT_BYTES:
            continue
        p = os.path.join(a.input_dir, it["file"].replace("/", os.sep))
        try:
            txt, _enc = read_text(p)
            if len(txt) > MAX_TEXT_BYTES:
                txt = txt[:MAX_TEXT_BYTES]
                warnings.append({"file": it["file"], "issue": "text-truncated", "limit": MAX_TEXT_BYTES})
            material_previews[it["file"]] = txt[:MAX_PREVIEW_CHARS]
        except OSError as e:
            warnings.append({"file": it["file"], "error": "read failed", "detail": str(e)})

    probe_text = task_text
    if isinstance(case_obj, dict):
        probe_text += "\n" + json.dumps(case_obj, ensure_ascii=False)

    summary = {
        "case_id": case_id,
        "output_contract": contract,
        "output_contract_keys": sorted(contract.keys()),
        "materials": items,
        "material_previews": material_previews,
        "constraints_detected": detect_constraints(probe_text),
        "task_path": a.task,
        "warnings": warnings,
        "generated_at": now_utc(),
        "default_contract_used": default_used,
    }

    out = os.path.join(a.work, "input_summary.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps({
        "ok": True, "summary": out, "case_id": case_id,
        "contract_keys": summary["output_contract_keys"],
        "materials": len(items), "warnings": len(warnings),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
