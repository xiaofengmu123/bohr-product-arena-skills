#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prepare.py —— 阶段 P0：发现输入、解析契约、抽取材料、落盘 result.json 骨架。

确定性保证（本 Skill 的核心设计）：
- 目录遍历按相对路径排序；所有 dict 序列化 sort_keys=True + ensure_ascii=False。
- 不读取墙钟时间；change_log.time / runtime 使用固定逻辑时钟 "T+NNNs"。
- 同一 /input 目录 + 同一环境，重复运行 /app/output/result.json 与
  /app/work/manifest.json 字节一致；输入指纹写入 result.json.input_fingerprint。

只用标准库；退出码 0 表示“已落盘合法骨架”，即使输入为空或坏 JSON 也不崩溃。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

PIPELINE_VERSION = "1.0.0"
LOGICAL_EPOCH = "2026-01-01T00:00:00+00:00"  # 逻辑时钟基线，非真实墙钟

TEXT_EXT = {".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".xml",
            ".html", ".htm", ".tex", ".bib", ".ris", ".yaml", ".yml", ".rst"}
PDF_EXT = {".pdf"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".zip", ".gz",
              ".tar", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
              ".epub", ".ipynb", ".7z", ".bz2"}

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_MATERIAL_FILES = 200
CONTROL_NAMES = {"case.json", "task.md", "input.json", "task.json"}

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")


def canonical_json(obj) -> str:
    """确定性序列化：同一 dict 两次调用产出完全相同的字节。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2,
                      separators=(",", ": "))


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_text(text: str) -> str:
    return sha256_of_bytes(text.encode("utf-8"))


def read_text(path: str):
    """按 utf-8-sig → utf-8 → gbk → latin-1 降级读取，返回 (text, encoding)。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)"


def load_json(path: str):
    """返回 (obj_or_None, error_or_None)。坏 JSON 不抛异常。"""
    try:
        text, _enc = read_text(path)
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 —— 契约要求“坏 JSON 可降级”
        return None, "json parse error: %s" % exc


def scan_input(root: str):
    """确定性遍历：目录名与文件名均排序。返回 [(rel, full), ...]。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.isfile(full):
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                out.append((rel, full))
    return out


def resolve_case_id(case, task_text: str) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id", "challenge_id", "challengeId"):
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, (int, float)):
                return str(val)
    m = re.search(r"challenge[-_]?id[:：\s`]+([A-Za-z0-9_\-]+)", task_text or "")
    if m:
        return m.group(1)
    return "unknown"


def extract_pdf_text(pdf_path: str, out_path: str) -> str:
    """尝试用可用的 PDF 文本工具抽取；返回 ""（成功）、工具名或错误信息（失败）。"""
    for tool in ("pdftotext", "pdf-text-tool"):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            subprocess.run([exe, "-enc", "UTF-8", pdf_path, out_path],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
            return ""
        except Exception as exc:  # noqa: BLE001
            return "%s failed: %s" % (tool, exc)
    return "no pdf text tool available"


def build_skeleton(case, task_text: str, contract, n_materials: int,
                   case_found: bool, case_ok: bool, task_found: bool, task_ok: bool) -> dict:
    case_id = resolve_case_id(case, task_text)
    if (case_found and not case_ok) or (task_found and not task_ok):
        status = "input_error"          # 输入契约存在但不可解析
    elif n_materials == 0:
        status = "insufficient_evidence"  # 空输入或无材料
    else:
        status = "partial"

    fixed = {
        "case_id": case_id,
        "status": status,
        "research_question": {
            "question": None,
            "scope": None,
            "sub_questions": [],
            "domain_note": "由后续深度分析填写；本字段由骨架先行占位以保证 partial 结果合法。",
        },
        "protocol": {
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "search_scope": None,
            "date_definition_note": None,
        },
        "time_trend": {
            "unit_note": None,
            "distribution": [],
            "windows": [],
            "claims": [],
        },
        "topic_evolution": {
            "stage_divisions": [],
            "term_mapping": [],
            "events": [],
            "claims": [],
        },
        "representative_works": {
            "criteria": [],
            "stages": [],
        },
        "research_gaps": {
            "gaps": [],
            "excluded_not_gaps": [],
        },
        "evidence_map": [],
        "conflicts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {
                "time": "T+000s",
                "action": "prepare.skeleton",
                "reason": "运行契约：前 3 分钟先落盘 partial 结果骨架，之后只做增量改进。",
                "before": None,
                "after": "result.json 骨架已写 /app/output/result.json",
            },
        ],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {
            "budget_seconds": 900,
            "budget_model_requests": 64,
            "max_tokens_per_response": 16384,
            "clock": "deterministic-logical-clock",
            "clock_note": "time 字段为逻辑时钟 T+NNNs，非真实墙钟；真实运行时长由平台记录。",
            "pipeline_version": PIPELINE_VERSION,
        },
        "input_fingerprint": None,
        "determinism": {
            "canonical_sha256": "",
            "note": "canonical_sha256 = 对 result.json 做确定性序列化、并把本字段置空后的 SHA-256；"
                    "scripts/selftest.py --determinism 复跑机械流水线核验。",
        },
    }

    # 输出契约：原样回填全部字段（缺值保持 null/[]，不改名、不删未知键），
    # 固定字段仅填充契约未提供的键；case_id/status 强制由本包给出。
    result = {}
    if isinstance(contract, dict):
        result.update(contract)
    for key, val in fixed.items():
        if key not in result or key in ("case_id", "status"):
            result[key] = val
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P0: discover input, echo contract, write skeleton")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的骨架（仍为确定性重写）")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(os.path.join(args.work, "materials"), exist_ok=True)
    os.makedirs(os.path.join(args.work, "determinism"), exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(os.path.join(args.output, "tables"), exist_ok=True)

    out_json = os.path.join(args.output, "result.json")
    if os.path.exists(out_json) and not args.force:
        print("[prepare] result.json 已存在；使用 --force 覆盖（确定性重写）", file=sys.stderr)
        return 0

    files = scan_input(args.input)
    case, task_text, contract = None, None, None
    case_path, task_path = None, None
    case_path_rel, task_path_rel = None, None
    case_err, task_err = None, None
    case_found = case_ok = task_found = task_ok = False

    # 1) 定位控制文件（case.json / task.md）
    for rel, full in files:
        name = rel.split("/")[-1].lower()
        if name in ("case.json", "input.json", "task.json") and case is None:
            case_path, case_path_rel = full, rel
            case_found = True
            case, case_err = load_json(full)
            case_ok = case is not None
            if isinstance(case, dict):
                contract = case.get("output_contract")
        elif name == "task.md" and task_text is None:
            task_path, task_path_rel = full, rel
            task_found = True
            task_text, _enc = read_text(full)
            task_ok = bool(task_text and task_text.strip())

    # 2) 发现材料：文本文件按排序取前 MAX_MATERIAL_FILES，PDF 尝试抽取
    materials = []
    sources = []
    errors, degradations = [], []
    pdf_entries = []
    for rel, full in files:
        name = rel.split("/")[-1].lower()
        if name in CONTROL_NAMES:
            continue
        ext = os.path.splitext(rel)[1].lower()
        size = os.path.getsize(full)
        if ext in BINARY_EXT:
            continue
        if size > MAX_FILE_BYTES:
            degradations.append("skip %s: %d bytes > %d" % (rel, size, MAX_FILE_BYTES))
            continue
        sha = sha256_of_bytes(open(full, "rb").read())
        if ext in PDF_EXT:
            pdf_entries.append((rel, full, size, sha))
            continue
        if ext in TEXT_EXT or name.endswith(".jsonl"):
            try:
                text, enc = read_text(full)
            except Exception as exc:  # noqa: BLE001
                errors.append("read %s: %s" % (rel, exc))
                continue
            materials.append({"rel": rel, "full": full, "text": text,
                              "encoding": enc, "size": size, "sha256": sha})
        else:
            degradations.append("skip %s: 未识别的扩展名" % rel)

    n_text_before_cap = len(materials)
    materials = materials[:MAX_MATERIAL_FILES]
    if n_text_before_cap > MAX_MATERIAL_FILES:
        degradations.append("材料数量超过上限 %d，按路径排序确定性采样前 %d 篇（样本边界已记录，"
                            "不宣称全领域覆盖）" % (MAX_MATERIAL_FILES, MAX_MATERIAL_FILES))

    # 3) PDF 文本抽取（确定性：有工具则抽，无工具则记录降级）
    for rel, full, size, sha in pdf_entries:
        if size > MAX_FILE_BYTES:
            degradations.append("skip pdf %s: %d bytes > %d" % (rel, size, MAX_FILE_BYTES))
            continue
        out_txt = os.path.join(args.work, "materials", "pdf_%s.txt" % sha[:12])
        err = extract_pdf_text(full, out_txt)
        if not err and os.path.exists(out_txt) and os.path.getsize(out_txt) > 0:
            text, enc = read_text(out_txt)
            materials.append({"rel": rel, "full": full, "text": text,
                              "encoding": "utf-8(extracted)", "size": size,
                              "sha256": sha, "extracted_path": out_txt})
        else:
            degradations.append("pdf %s 文本不可得：%s" % (rel, err))

    # 4) 写入逐字副本到 /app/work/materials/mat_NNN.txt（无表头，行号与原文一致）
    mat_map = {}
    for idx, m in enumerate(sorted(materials, key=lambda x: x["rel"]), 1):
        mat_name = "mat_%03d.txt" % idx
        mat_path = os.path.join(args.work, "materials", mat_name)
        with open(mat_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(m["text"])
        mat_map[m["rel"]] = mat_name
        sources.append({
            "id": "S%03d" % idx,
            "path": m["rel"],
            "work_path": "work/materials/%s" % mat_name,
            "encoding": m["encoding"],
            "size_bytes": m["size"],
            "sha256": m["sha256"],
            "kind": "material",
            "year": None,
            "title": None,
            "citations": None,
            "text_status": "extracted",
            "machine": True,
        })

    # 5) 控制文件也列入 sources（便于证据溯源）
    for rel in sorted([case_path_rel if case_path else "", task_path_rel if task_path else ""]):
        if not rel:
            continue
        full = os.path.join(args.input, rel.replace("/", os.sep))
        if os.path.isfile(full):
            sources.append({
                "id": "S%03d" % (len(sources) + 1),
                "path": rel,
                "work_path": None,
                "encoding": "utf-8-sig|utf-8",
                "size_bytes": os.path.getsize(full),
                "sha256": sha256_of_bytes(open(full, "rb").read()),
                "kind": "control",
                "year": None,
                "title": None,
                "citations": None,
                "text_status": "read",
                "machine": True,
            })

    # 6) 输入指纹：所有输入文件（按路径排序）的相对路径 + SHA-256
    fp_parts = []
    for rel, full in sorted(files, key=lambda x: x[0]):
        try:
            fp_parts.append("%s\0%s" % (rel, sha256_of_bytes(open(full, "rb").read())))
        except Exception:  # noqa: BLE001
            fp_parts.append("%s\0<unreadable>" % rel)
    input_fp = sha256_of_bytes("\n".join(fp_parts).encode("utf-8"))

    # 7) 组装 result.json 骨架
    skeleton = build_skeleton(case, task_text, contract, len(materials),
                              case_found, case_ok, task_found, task_ok)
    skeleton["sources"] = sources
    skeleton["input_fingerprint"] = input_fp
    skeleton["errors"] = skeleton.get("errors", []) + errors
    skeleton["degradations"] = skeleton.get("degradations", []) + degradations
    if case_err:
        skeleton["errors"].append("case.json: %s" % case_err)
    if not task_found:
        skeleton["limitations"].append("未发现 task.md，研究问题说明缺失")
    elif not task_ok:
        skeleton["limitations"].append("task.md 不可读或为空")
    if not case_found:
        skeleton["limitations"].append("未发现可解析的 case.json，case_id 可能回退为 unknown")
    elif not case_ok:
        skeleton["limitations"].append("case.json 存在但不可解析，output_contract 无法回填")

    # 8) 计算 canonical_sha256（先置空再序列化再回填）
    skeleton["determinism"]["canonical_sha256"] = ""
    body = canonical_json(skeleton)
    skeleton["determinism"]["canonical_sha256"] = sha256_of_text(body)
    with open(out_json, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(skeleton))
        fh.write("\n")

    # 9) manifest + 输入指纹侧车文件
    manifest = {
        "pipeline_version": PIPELINE_VERSION,
        "input_root": args.input,
        "n_input_files": len(files),
        "n_materials": len(materials),
        "mat_map": mat_map,
        "control": {
            "case": case_path_rel if case_path else None,
            "task": task_path_rel if task_path else None,
            "case_parse_error": case_err,
            "task_present": task_ok,
        },
        "output_contract_present": isinstance(contract, dict),
        "input_fingerprint": input_fp,
        "degradations": degradations,
        "errors": errors,
    }
    with open(os.path.join(args.work, "manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(manifest))
        fh.write("\n")
    with open(os.path.join(args.work, "determinism", "input_fingerprint.json"),
              "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json({"input_fingerprint": input_fp}))
        fh.write("\n")

    print("[prepare] status=%s case_id=%s materials=%d files=%d" % (
        skeleton["status"], skeleton["case_id"], len(materials), len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
