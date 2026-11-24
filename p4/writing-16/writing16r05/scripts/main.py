#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研成果转化写作 Skill 主入口（仅标准库）。

职责（确定性、幂等）：
  1. 发现 /input 下的 case.json、task.md 与科研材料，不假设固定文件名；
  2. 解析 case.json，提取 case_id、任务约束与 output_contract；
  3. **立即**在 /app/output 写入一份合法的 result.json 骨架（status=partial），
     保证后续任何分析或生成失败时仍有可复核的落盘产物；
  4. 生成 /app/work/manifest.json，供 evidence.py / verify.py 复用。

退出码：0=成功（含 insufficient_evidence / input_error 的诚实状态）；2=参数错误。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_BYTES = 2 * 1024 * 1024          # 单文件文本读取上限 2MB
MAX_SCAN_FILES = 200                       # 扫描文件数上限
TEXT_EXTS = {".md", ".markdown", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".bib", ".tex"}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

# result.json 的必备公共字段；output_contract 字段在此基础上原样合并。
CORE_RESULT = {
    "case_id": None,
    "status": "partial",
    "sources": [],
    "uncertainty": [],
    "change_log": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "runtime": {
        "start": None,
        "end": None,
        "seconds": None,
        "notes": [],
    },
}

# 当 case.json 未显式给出 output_contract 时使用的保守默认契约。
DEFAULT_CONTRACT = {
    "deliverable": {"path": None, "format": "markdown", "description": None},
    "evidence_map": [],
    "change_log": [],
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg, stream=None):
    (stream or sys.stderr).write("[main] %s\n" % msg)


def read_text_file(path):
    """按 utf-8-sig -> utf-8 -> gbk -> latin-1 顺序降级读取，返回 (text, encoding, errors)。"""
    data = None
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_TEXT_BYTES + 1)
    except OSError as exc:
        return None, None, [str(exc)]
    truncated = len(data) > MAX_TEXT_BYTES
    if truncated:
        data = data[:MAX_TEXT_BYTES]
    errors = []
    if truncated:
        errors.append("truncated_to_%d_bytes" % MAX_TEXT_BYTES)
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            text = data.decode(enc)
            return text, enc, errors
        except UnicodeDecodeError as exc:
            errors.append("%s:%s" % (enc, str(exc)))
    # 兜底：强制替换解码，确保不崩溃
    return data.decode("latin-1", errors="replace"), "latin-1(replace)", errors


def parse_json(text, path):
    """容错解析 JSON；失败返回 None 并记录错误。"""
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "json_parse_error@%s: %s" % (path, str(exc))


def extract_case_id(obj):
    """从常见键名中提取 case_id，不假设固定键。"""
    for key in ("case_id", "caseId", "id", "case", "challenge_id", "challengeId"):
        val = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip()
        if val is not None:
            return val
    return None


def extract_contract(obj):
    """从常见键名中提取 output_contract；找不到返回 None。"""
    for key in ("output_contract", "outputContract", "output", "contract", "result_schema"):
        val = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(val, dict):
            return val
    return None


def null_fill(value):
    """把任意契约结构原样回填为合法空值：dict 递归、list->[]、其余->None。"""
    if isinstance(value, dict):
        return {k: null_fill(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def classify(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


def extract_pdf_text(pdf_path, work_dir):
    """若系统有 pdftotext，则把 PDF 正文抽到 work_dir 下的同名 .txt；否则返回 (None, degradation)。"""
    out_path = os.path.join(
        work_dir,
        "extracted",
        os.path.splitext(os.path.basename(pdf_path))[0] + ".txt",
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", pdf_path, out_path],
            capture_output=True,
            timeout=120,
        )
        if proc.returncode == 0 and os.path.exists(out_path):
            return out_path, None
        return None, "pdftotext_failed(%d)" % proc.returncode
    except FileNotFoundError:
        return None, "pdftotext_not_available"
    except subprocess.TimeoutExpired:
        return None, "pdftotext_timeout"


def scan_input(input_dir, work_dir):
    """扫描输入目录，返回 (files, degradations)。"""
    files = []
    degradations = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing:%s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        if len(files) >= MAX_SCAN_FILES:
            degradations.append("scan_truncated_at_%d_files" % MAX_SCAN_FILES)
            break
        for name in names:
            path = os.path.join(root, name)
            if os.path.islink(path):
                degradations.append("symlink_skipped:%s" % path)
                continue
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                degradations.append("stat_failed:%s:%s" % (path, str(exc)))
                continue
            kind = classify(path)
            rec = {
                "path": path,
                "rel": os.path.relpath(path, input_dir),
                "kind": kind,
                "bytes": size,
                "encoding": None,
                "errors": [],
                "extracted_text_path": None,
            }
            if kind == "text" and size <= MAX_TEXT_BYTES:
                text, enc, errs = read_text_file(path)
                rec["encoding"] = enc
                rec["errors"] = errs
                rec["preview_chars"] = len(text or "")
            elif kind == "pdf":
                out_path, deg = extract_pdf_text(path, work_dir)
                rec["extracted_text_path"] = out_path
                if deg:
                    rec["errors"].append(deg)
                    degradations.append(deg)
            elif kind in ("office", "other"):
                rec["errors"].append("binary_not_read_here")
            files.append(rec)
    return files, degradations


def find_case_and_task(files):
    """定位 case.json 与 task.md（按 basename 优先，再按扩展名兜底）。"""
    case_path = None
    task_path = None
    for rec in files:
        base = os.path.basename(rec["path"]).lower()
        if base == "case.json" and case_path is None:
            case_path = rec["path"]
        if base == "task.md" and task_path is None:
            task_path = rec["path"]
    if case_path is None:
        for rec in files:
            if rec["kind"] == "text" and rec["rel"].lower().endswith(".json"):
                case_path = rec["path"]
                break
    if task_path is None:
        for rec in files:
            if rec["kind"] == "text" and rec["rel"].lower().endswith(".md"):
                task_path = rec["path"]
                break
    return case_path, task_path


def build_skeleton(case_id, contract):
    """生成 result.json 骨架：公共字段 + output_contract 全部键原样空值回填。"""
    result = dict(CORE_RESULT)
    result["case_id"] = case_id
    result["runtime"]["start"] = utcnow()
    if isinstance(contract, dict):
        filled = null_fill(contract)
        for k, v in filled.items():
            # 公共字段以 CORE 为准，避免契约覆盖 status 等控制字段
            if k not in CORE_RESULT:
                result[k] = v
        # 契约里明确给出的"主产物路径/格式"是提示而非数据，骨架期原样保留，
        # 便于 Agent 知道该写到哪、verify 能据此定位产物。
        for k in ("deliverable", "output"):
            if isinstance(contract.get(k), dict) and isinstance(result.get(k), dict):
                for hint in ("path", "format", "type", "description"):
                    val = contract[k].get(hint)
                    if isinstance(val, str):
                        result[k][hint] = val
        result.setdefault("_contract_keys", sorted(contract.keys()))
    return result


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="科研成果转化写作 Skill：输入发现 + 结果骨架落盘")
    ap.add_argument("--input", default=DEFAULT_INPUT, help="输入目录（默认 /input）")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    ap.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    started = time.time()
    files, degradations = scan_input(args.input, args.work)
    case_path, task_path = find_case_and_task(files)

    case_obj = None
    task_text = None
    status = "partial"
    errors = []
    uncertainties = []

    if case_path is None:
        status = "input_error"
        errors.append("case.json_not_found")
    else:
        text, _enc, errs = read_text_file(case_path)
        case_obj, parse_err = parse_json(text, case_path)
        errors.extend(errs or [])
        if parse_err:
            errors.append(parse_err)
            status = "input_error"
        elif not isinstance(case_obj, dict):
            errors.append("case.json_not_object")
            status = "input_error"

    if task_path is not None:
        text, _enc, errs = read_text_file(task_path)
        task_text = text
        errors.extend(errs or [])

    case_id = extract_case_id(case_obj) if isinstance(case_obj, dict) else None
    contract = extract_contract(case_obj) if isinstance(case_obj, dict) else None
    if contract is None:
        contract = dict(DEFAULT_CONTRACT)

    if status == "partial":
        material_files = [r for r in files if os.path.basename(r["path"]).lower()
                          not in ("case.json", "task.md")]
        has_text = any(r["kind"] == "text" for r in material_files) or \
            any(r["extracted_text_path"] for r in material_files)
        if not has_text:
            status = "insufficient_evidence"
            uncertainties.append("no_readable_source_material_found")

    skeleton = build_skeleton(case_id, contract)
    skeleton["status"] = status
    skeleton["runtime"]["end"] = utcnow()
    skeleton["runtime"]["seconds"] = round(time.time() - started, 3)
    skeleton["runtime"]["notes"] = ["skeleton_written_by_main_py"]
    skeleton["degradations"] = degradations
    skeleton["errors"] = errors
    if status in ("insufficient_evidence", "input_error"):
        skeleton["uncertainty"] = uncertainties

    result_path = os.path.join(args.output, "result.json")
    write_json(result_path, skeleton)

    manifest = {
        "case_id": case_id,
        "status_at_skeleton": status,
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
        "case_json": case_path,
        "task_md": task_path,
        "contract": contract,
        "contract_keys": sorted(contract.keys()) if isinstance(contract, dict) else None,
        "files": files,
        "degradations": degradations,
        "errors": errors,
        "generated_at": utcnow(),
        "task_preview_chars": len(task_text or ""),
    }
    manifest_path = write_json(os.path.join(args.work, "manifest.json"), manifest)

    log("case_id=%s status=%s files=%d" % (case_id, status, len(files)))
    log("result skeleton -> %s" % result_path)
    log("manifest -> %s" % manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
