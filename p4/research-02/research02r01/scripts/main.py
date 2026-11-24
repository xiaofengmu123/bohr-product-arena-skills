#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-traced-review 确定性脚手架（仅 Python 标准库）。

本脚本只做"发现 + 归一化 + 落盘骨架"，不做任何综述分析：
1) 发现 /input 下的 case.json、task.md 与材料文件；
2) 归一化 output_contract 的多种形态，原样回填全部字段（缺值 null/[]，不改名、不删键）；
3) 立即写出一版合法 result.json（status=partial）作为安全骨架；
4) 扫描材料、计算 SHA-256、对 PDF 调用 pdftotext 抽取文本；
5) 写 work/inventory.json 与筛选/证据空骨架。

设计目标：空输入、坏 JSON、坏编码、缺 pdftotext 都不崩溃；同一输入重复运行结果一致。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".tex"}
REC_EXTS = {".json", ".jsonl", ".csv", ".tsv", ".bib", ".ris", ".xml", ".yaml", ".yml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | REC_EXTS
META_NAMES = {"case.json", "task.md", "result.json"}
MAX_FILE_BYTES = 2 * 1024 * 1024

# 无 output_contract 时的回退字段集（仅作为可用的默认结构，不改写 case.json）
DEFAULT_FIELDS = [
    "research_question",
    "screening_criteria",
    "screening_log",
    "evidence_table",
    "evidence_map",
    "themes",
    "review_draft",
    "conflicts_and_gaps",
    "references",
]


def ts_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("[evidence-traced-review] " + msg, flush=True)


def read_text(path):
    """按 utf-8-sig / utf-8 / latin-1 顺序容错读取；返回 (text, encoding, error)。"""
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as exc:
            last_err = str(exc)
        except Exception as exc:  # noqa: BLE001
            return None, None, str(exc)
    return None, None, last_err


def read_json(path):
    """读 JSON 并解析；失败返回 (None, [错误信息])。"""
    text, _enc, err = read_text(path)
    if text is None:
        return None, ["无法读取 " + path + ": " + (err or "未知错误")]
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, ["JSON 解析失败 " + path + ": " + str(exc)]


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None


def default_for_spec(spec):
    """依据 schema/type 提示给出缺省值：数组 []、对象 {}、其余 None。"""
    if isinstance(spec, dict):
        spec_type = spec.get("type")
        if isinstance(spec_type, str):
            t = spec_type.lower()
            if t == "array":
                return []
            if t == "object":
                return {}
        if isinstance(spec_type, list):
            lowered = [str(x).lower() for x in spec_type]
            if "null" in lowered:
                return None
            if "array" in lowered:
                return []
            if "object" in lowered:
                return {}
        if "items" in spec or "properties" in spec:
            return [] if "items" in spec else {}
    return None


def normalize_fields(contract):
    """把多种形态的 output_contract 归一化为 [(字段名, 缺省值), ...]。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        fields = contract.get("fields")
        if isinstance(fields, (dict, list)):
            return normalize_fields(fields)
        # 视作 name -> spec 的映射
        return [(name, default_for_spec(contract[name])) for name in contract]
    if isinstance(contract, list):
        out = []
        for item in contract:
            if isinstance(item, str):
                out.append((item, None))
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id")
                      or item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    out.append((nm, default_for_spec(item)))
        return out
    if isinstance(contract, str) and contract.strip():
        return [(contract.strip(), None)]
    return None


def find_contract(node):
    """递归查找 output_contract / output_schema / outputcontract / outputschema。"""
    if isinstance(node, dict):
        for key, val in node.items():
            if key.lower() in ("output_contract", "outputcontract",
                               "output_schema", "outputschema"):
                return val
        for val in node.values():
            found = find_contract(val)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_contract(item)
            if found is not None:
                return found
    return None


def first_str(case, keys):
    if isinstance(case, dict):
        for key in keys:
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def extract_case_id(case, case_path):
    val = first_str(case, ("case_id", "caseId", "id", "challenge_id", "challengeId"))
    if val:
        return val
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    val = first_str(case, ("research_question", "question", "query", "prompt",
                           "title", "objective", "topic"))
    if val:
        return val
    if isinstance(case, dict):
        task = case.get("task")
        if isinstance(task, dict):
            return first_str(task, ("research_question", "question", "prompt",
                                    "description", "title", "objective"))
    return None


def find_case_file(input_dir):
    """先找 case.json；找不到再找任一含 output_contract 的 .json。"""
    direct = os.path.join(input_dir, "case.json")
    if os.path.isfile(direct):
        return direct
    if os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            for name in sorted(files):
                if name.lower() == "case.json":
                    return os.path.join(root, name)
        for root, _dirs, files in os.walk(input_dir):
            for name in sorted(files):
                if not name.lower().endswith(".json"):
                    continue
                path = os.path.join(root, name)
                obj, err = read_json(path)
                if obj is not None and find_contract(obj) is not None:
                    return path
    return None


def discover_materials(input_dir):
    """扫描材料文件，跳过元文件名与隐藏文件；返回按相对路径排序的列表。"""
    found = []
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(files):
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in MATERIAL_EXTS:
                continue
            if name.lower() in META_NAMES:
                continue
            found.append(os.path.join(root, name))
    found.sort()
    return found


def extract_pdf(pdf_path, out_txt):
    """调用 pdftotext 抽取；返回 (ok, message)。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return False, "pdftotext 不可用"
    try:
        proc = subprocess.run(
            [exe, "-layout", pdf_path, out_txt],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
        if proc.returncode == 0 and os.path.isfile(out_txt):
            return True, None
        msg = proc.stderr.decode("utf-8", "replace").strip()
        return False, "pdftotext 退出码 %d: %s" % (proc.returncode, msg or "无输出")
    except Exception as exc:  # noqa: BLE001
        return False, "pdftotext 异常: " + str(exc)


def build_inventory(materials, input_dir, work_dir, errors, degradations):
    """扫描材料、计算哈希、抽取 PDF；返回 (inventory, extracted_dir)。"""
    inventory = []
    extracted_dir = os.path.join(work_dir, "extracted")
    os.makedirs(extracted_dir, exist_ok=True)

    for idx, path in enumerate(materials):
        rel = os.path.relpath(path, input_dir).replace("\\", "/")
        size = 0
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            errors.append("无法读取大小 " + rel + ": " + str(exc))
        ext = os.path.splitext(path)[1].lower()
        kind = ("pdf" if ext in PDF_EXTS
                else "record" if ext in REC_EXTS else "text")
        entry = {
            "source_id": "src%02d" % (idx + 1),
            "path": path,
            "rel": rel,
            "kind": kind,
            "bytes": size,
            "sha256": sha256_file(path),
            "extracted": None,
            "skipped": None,
        }
        if size > MAX_FILE_BYTES:
            entry["skipped"] = "超过 %d 字节上限" % MAX_FILE_BYTES
            errors.append("跳过超大文件 " + rel)
        elif kind == "pdf":
            stem = "src%02d" % (idx + 1)
            out_txt = os.path.join(extracted_dir, stem + ".txt")
            ok, msg = extract_pdf(path, out_txt)
            if ok:
                entry["extracted"] = out_txt
            else:
                entry["extracted"] = None
                degradations.append("PDF 抽取失败 " + rel + ": " + (msg or ""))
        inventory.append(entry)
    return inventory, extracted_dir


def scan_records(inventory, errors):
    """对记录类文件做轻量元数据扫描（标题/DOI 候选），用于去重提示；非致命。"""
    hints = {}
    doi_re = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.I)
    for entry in inventory:
        if entry["kind"] != "record" or entry.get("skipped"):
            continue
        text, _enc, err = read_text(entry["path"])
        if text is None:
            errors.append("记录读取失败 " + entry["rel"] + ": " + (err or ""))
            continue
        dois = [d.rstrip(".,;)]}") for d in doi_re.findall(text)][:8]
        if dois:
            hints[entry["source_id"]] = dois
    return hints


def make_skeleton(case, case_path, contract_fields, started):
    """构造 result.json 骨架：契约字段优先，诚实必填字段其次，可选辅助字段最后。"""
    data = {}
    for name, default in contract_fields:
        data[name] = default
    # 规格要求的诚实必填字段：始终写入真实运行值（语义与契约一致）
    required = {
        "case_id": extract_case_id(case, case_path),
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "ts": started,
            "phase": "phase0",
            "action": "main.py 落盘 partial 骨架",
            "status": "partial",
        }],
    }
    for key, val in required.items():
        data[key] = val
    # 可选辅助字段：仅在与契约字段不冲突时写入
    optional = {
        "artifacts": {
            "review": "/app/output/review.md",
            "screening_log": "/app/output/screening_log.json",
            "evidence_table": "/app/output/evidence_table.jsonl",
            "evidence_map": "/app/output/evidence_map.json",
            "themes": "/app/output/themes.md",
            "conflicts_and_gaps": "/app/output/conflicts_and_gaps.md",
        },
        "errors": [],
        "degradations": [],
        "runtime": {
            "started": started,
            "input": "/input",
            "output": "/app/output",
            "work": "/app/work",
        },
    }
    for key, val in optional.items():
        if key not in data:
            data[key] = val
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="evidence-traced-review 脚手架")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)

    started = ts_now()
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(os.path.join(args.work, "extracted"), exist_ok=True)

    errors, degradations = [], []

    case_path = find_case_file(args.input)
    case = None
    contract = None
    contract_fields = None
    if case_path is None:
        errors.append("未找到 case.json")
    else:
        case, case_errors = read_json(case_path)
        if case is None:
            errors.extend(case_errors or [])
            errors.append("case.json 不可解析")
        else:
            contract = find_contract(case)
            if contract is None:
                degradations.append("未发现 output_contract，使用默认字段集")
    if contract_fields is None:
        if contract is not None:
            contract_fields = normalize_fields(contract)
        if not contract_fields:
            contract_fields = [(name, None) for name in DEFAULT_FIELDS]

    result = make_skeleton(case, case_path, contract_fields, started)
    result["errors"] = errors[:]
    result["degradations"] = degradations[:]

    if case is None:
        if case_path is None:
            result["status"] = "insufficient_evidence"
            result["pending_human"] = ["未找到 case.json，且无可用材料可处理"]
        else:
            result["status"] = "input_error"
            result["pending_human"] = ["case.json 不可解析，无法获得 case_id 与 output_contract"]
    elif contract is None:
        result["status"] = "partial"
        result["uncertainty"].append(
            "case.json 无 output_contract，已按默认字段集回填；请以 case.json 实际契约为准")

    # 立即落盘 result.json（前 3 分钟安全骨架）
    write_json(os.path.join(args.output, "result.json"), result)
    log("result.json 骨架已落盘 (status=%s)" % result["status"])

    # 材料盘点
    materials = discover_materials(args.input)
    inventory, extracted_dir = build_inventory(
        materials, args.input, args.work, errors, degradations)
    record_hints = scan_records(inventory, errors)

    manifest = {
        "case_path": case_path,
        "case_id": result["case_id"],
        "question": extract_question(case) if case else None,
        "contract_fields": [name for name, _ in contract_fields],
        "materials": inventory,
        "record_hints": record_hints,
    }
    write_json(os.path.join(args.work, "manifest.json"), manifest)
    write_json(os.path.join(args.work, "inventory.json"), inventory)

    # 空骨架：筛选日志、证据表、证据映射
    write_json(os.path.join(args.work, "screening_log.json"), {
        "criteria": {"population": None, "include": None,
                     "exclude": None, "dedup": None},
        "decisions": [],
        "duplicates": [],
    })
    write_text(os.path.join(args.work, "evidence_table.jsonl"), "")
    write_json(os.path.join(args.work, "evidence_map.json"), {
        "version": 1, "case_id": result["case_id"], "claims": []})

    # 更新 result.json：回填错误/降级/清单
    result["errors"] = errors
    result["degradations"] = degradations
    result["sources"] = [
        {"source_id": e["source_id"], "rel": e["rel"], "kind": e["kind"],
         "sha256": e["sha256"], "extracted": bool(e.get("extracted")),
         "skipped": e.get("skipped")}
        for e in inventory
    ]
    result["runtime"]["updated"] = ts_now()
    result["change_log"].append({
        "ts": ts_now(), "phase": "phase0",
        "action": "完成材料盘点与抽取", "status": result["status"]})
    write_json(os.path.join(args.output, "result.json"), result)

    log("材料 %d 个，抽取完成；work/manifest.json 与 work/inventory.json 已写"
        % len(inventory))
    if errors:
        log("errors: %s" % "; ".join(errors[:5]))
    if degradations:
        log("degradations: %s" % "; ".join(degradations[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
