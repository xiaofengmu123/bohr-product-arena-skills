#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型优先文献综述 Skill 的确定性脚手架（仅 Python 标准库）。

与"直接写综述"不同，本脚本只做可复现的机械工作，把思考留给 Agent：
  1) 发现 /input 下的 case.json、task.md 与材料文件；
  2) 解析 case.json 中的 output_contract，按原键名回填全部字段（缺值 null/[]，不改名、不删键）；
  3) 立即写出一版合法 result.json（status=partial）作为安全骨架；
  4) 扫描材料、计算 SHA-256 用于去重、对 PDF 调用 pdftotext 抽取文本；
  5) 生成 plan.json / claims.json / screening_log.json / evidence_table.jsonl 的空模型骨架。

设计目标：空输入、坏 JSON、坏编码、缺软件都不崩溃；同一输入重复运行字节一致。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
RECORD_EXTS = {".json", ".jsonl", ".csv", ".bib", ".ris", ".xml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | RECORD_EXTS
SKIP_NAMES = {"case.json", "task.md", "result.json", "resources.yaml"}
MAX_FILE_BYTES = 2 * 1024 * 1024
CONTRACT_KEYS = {
    "output_contract", "outputcontract", "output_schema", "outputschema",
    "output_format", "outputformat",
}
CASE_ID_KEYS = ("case_id", "caseId", "id", "challenge_id")
QUESTION_KEYS = ("research_question", "question", "query", "prompt", "title", "objective")


def log(msg):
    print("[lit-review] " + msg, flush=True)


def read_text(path):
    """按 utf-8-sig / utf-8 / latin-1 顺序容错读取；绝不抛异常。"""
    last = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc, None
        except UnicodeDecodeError as e:
            last = str(e)
        except Exception as e:  # 权限/不存在等
            return None, None, str(e)
    return None, None, last


def read_json(path):
    text, _enc, err = read_text(path)
    if text is None:
        return None, ["无法读取: " + (err or "未知错误")]
    try:
        return json.loads(text), None
    except Exception as e:
        return None, ["JSON 解析失败: " + str(e)]


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def find_case_file(input_dir):
    """先找 case.json；找不到再找任一含 output_contract 的 .json。"""
    if not os.path.isdir(input_dir):
        return None
    p = os.path.join(input_dir, "case.json")
    if os.path.isfile(p):
        return p
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for f in sorted(files):
            if f.lower() == "case.json":
                return os.path.join(root, f)
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for f in sorted(files):
            if f.lower().endswith(".json"):
                fp = os.path.join(root, f)
                data, errs = read_json(fp)
                if data is not None and find_contract(data) is not None:
                    return fp
    return None


def find_contract(node, _depth=0):
    """递归查找 output_contract / output_schema 等键；找不到返回 None。"""
    if _depth > 8:
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.lower() in CONTRACT_KEYS:
                return v
        for v in node.values():
            r = find_contract(v, _depth + 1)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = find_contract(item, _depth + 1)
            if r is not None:
                return r
    return None


def default_for_spec(spec):
    """依据 schema/type 提示：数组给 []，对象给 {}，其余 null。"""
    if isinstance(spec, dict):
        t = spec.get("type")
        types = []
        if isinstance(t, str):
            types.append(t.lower())
        elif isinstance(t, list):
            types = [str(x).lower() for x in t]
        if "array" in types:
            return []
        if "object" in types:
            return {}
        if "items" in spec and "array" in types:
            return []
        if "properties" in spec and "object" in types:
            return {}
        if "properties" in spec:
            return {}
        if "items" in spec:
            return []
    return None


def contract_fields(contract):
    """把多种形态的 output_contract 归一化为 [(字段名, 默认值), ...]。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        fl = contract.get("fields")
        if isinstance(fl, (dict, list)):
            return contract_fields(fl)
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
        return [(contract, None)]
    return None


def get_case_id(case, case_path):
    if isinstance(case, dict):
        for k in CASE_ID_KEYS:
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def get_question(case):
    if isinstance(case, dict):
        for k in QUESTION_KEYS:
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        t = case.get("task")
        if isinstance(t, dict):
            for k in QUESTION_KEYS:
                v = t.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def find_task_file(input_dir):
    if not os.path.isdir(input_dir):
        return None
    p = os.path.join(input_dir, "task.md")
    if os.path.isfile(p):
        return p
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for f in sorted(files):
            if f.lower() == "task.md":
                return os.path.join(root, f)
    return None


def scan_materials(input_dir):
    """返回 (items, duplicate_groups)。items 按路径排序；去重组按哈希排序。"""
    items = []
    if os.path.isdir(input_dir):
        for root, dirs, files in os.walk(input_dir):
            dirs.sort()
            for f in sorted(files):
                fp = os.path.join(root, f)
                base = f.lower()
                if base in SKIP_NAMES:
                    continue
                ext = os.path.splitext(base)[1]
                if ext not in MATERIAL_EXTS:
                    continue
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    size = -1
                if size > MAX_FILE_BYTES:
                    items.append({
                        "path": fp, "name": f, "ext": ext,
                        "sha256": None, "size": size,
                        "skipped": True, "reason": "size_exceeds_2MB",
                    })
                    continue
                items.append({
                    "path": fp, "name": f, "ext": ext,
                    "sha256": sha256_file(fp), "size": size,
                    "skipped": False, "reason": None,
                })
    items.sort(key=lambda x: x["path"])
    by_hash = {}
    for it in items:
        h = it.get("sha256")
        if h:
            by_hash.setdefault(h, []).append(it["path"])
    duplicate_groups = [
        {"sha256": h, "paths": sorted(ps)}
        for h, ps in sorted(by_hash.items()) if len(ps) > 1
    ]
    return items, duplicate_groups


def extract_pdfs(items, work_dir):
    """对 PDF 调用 pdftotext 抽取文本；返回 (ok_count, failures)。"""
    pdftotext = shutil.which("pdftotext")
    ok_count = 0
    failures = []
    out_dir = os.path.join(work_dir, "extracted")
    os.makedirs(out_dir, exist_ok=True)
    if not pdftotext:
        return 0, [{"kind": "pdftotext_missing",
                    "detail": "环境未提供 pdftotext，PDF 文本未抽取；请用已给文本材料继续"}]
    for it in items:
        if it.get("skipped") or it.get("ext") not in PDF_EXTS:
            continue
        src = it["path"]
        dst = os.path.join(out_dir, os.path.splitext(it["name"])[0] + ".txt")
        if os.path.exists(dst):
            ok_count += 1
            continue
        for cmd in ([pdftotext, "-enc", "UTF-8", src, dst],
                    [pdftotext, src, dst]):
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=120)
                if os.path.exists(dst) and os.path.getsize(dst) > 0:
                    ok_count += 1
                    break
            except Exception as e:
                failures.append({"path": src, "detail": str(e)})
                break
        else:
            failures.append({"path": src, "detail": "pdftotext 未产出文本"})
    return ok_count, failures


def now_iso():
    # 骨架阶段不依赖墙钟，保证重复运行字节一致；Agent 增量写盘时自行带真实时间。
    return "1970-01-01T00:00:00Z"


def build_result_skeleton(case_id, question, contract, case_path):
    result = {"case_id": case_id, "status": "partial"}
    fields = contract_fields(contract) or []
    for name, default in fields:
        result[name] = default
    if question and "research_question" in result and result.get("research_question") is None:
        result["research_question"] = question
    result["artifacts"] = []
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{
        "seq": 0, "phase": "scaffold", "action": "写 result.json 骨架（partial）",
        "file": "result.json", "ts": now_iso(),
    }]
    result["errors"] = []
    result["degradations"] = []
    return result, fields


def main(argv):
    ap = argparse.ArgumentParser(description="文献综述 Skill 确定性脚手架")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    errors = []
    degradations = []

    case_path = find_case_file(args.input)
    task_path = find_task_file(args.input)

    case = None
    if case_path:
        case, errs = read_json(case_path)
        if case is None:
            errors.extend(errs or [])
    else:
        errors.append("未找到 case.json 或含 output_contract 的 .json")

    case_id = get_case_id(case, case_path)
    question = get_question(case)
    contract = find_contract(case) if case is not None else None

    result, fields = build_result_skeleton(case_id, question, contract, case_path)

    if not case_path:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append(
            {"kind": "no_case_input", "detail": "未找到 case.json；无法确定研究问题与材料"})
    elif case is None:
        result["status"] = "input_error"
    else:
        result["status"] = "partial"

    result["errors"] = errors
    result["degradations"] = list(degradations)

    # 扫描材料与去重
    items, duplicate_groups = scan_materials(args.input)
    ok_extract, failures = extract_pdfs(items, args.work)
    if failures:
        result["degradations"].extend(failures)

    inventory = {
        "input_dir": args.input,
        "case_file": case_path,
        "task_file": task_path,
        "materials": items,
        "duplicate_groups": duplicate_groups,
        "extraction": {"pdftotext_available": shutil.which("pdftotext") is not None,
                       "pdfs_extracted": ok_extract},
    }
    write_json(os.path.join(args.work, "inventory.json"), inventory)

    # 空中间模型骨架
    plan = {
        "research_question": question,
        "screening_protocol": {
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "dedup_rules": [
                "同一 SHA-256 视为重复，保留元数据最全的一份",
                "同一 DOI 或规范化题名视为重复，保留全文版本",
            ],
            "frozen": False,
        },
        "theme_model": {
            "themes": [],
            "method": "bottom-up clustering from evidence fields (object/method/result/dispute)",
        },
        "claim_map_note": "claim 图由 claims.json 权威维护；本字段仅为覆盖索引",
        "coverage": {"covered_sources": [], "gaps": []},
    }
    write_json(os.path.join(args.output, "plan.json"), plan)
    write_json(os.path.join(args.output, "claims.json"), {"claims": []})

    screening_log = {
        "research_question": question,
        "criteria": {
            "inclusion": [],
            "exclusion": [],
            "dedup_rules": [
                "同一 SHA-256 视为重复，保留元数据最全的一份",
                "同一 DOI 或规范化题名视为重复，保留全文版本",
            ],
            "frozen_at": None,
        },
        "decisions": [],
        "dedup": duplicate_groups,
    }
    write_json(os.path.join(args.output, "screening_log.json"), screening_log)

    evidence_path = os.path.join(args.output, "evidence_table.jsonl")
    if not os.path.exists(evidence_path):
        write_text(evidence_path, "")

    write_json(os.path.join(args.output, "result.json"), result)

    log("case_id=%s status=%s contract_fields=%d materials=%d pdfs_extracted=%d"
        % (case_id, result["status"], len(fields), len(items), ok_extract))
    log("wrote result.json + plan.json + claims.json + screening_log.json + evidence_table.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
