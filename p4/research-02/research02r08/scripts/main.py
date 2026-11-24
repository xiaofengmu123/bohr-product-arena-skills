#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的确定性脚手架（仅 Python 标准库）。

Phase 0 职责：
1) 发现 /input 下的 case.json 与 task.md，以及材料文件（PDF/文本/题录）；
2) 解析 case.json 中的 output_contract，原样回填全部字段（缺值给 null/[]，不改名、不删未知键）；
3) 立即写出一版合法 result.json（status=partial）作为安全骨架；
4) 扫描材料、计算 SHA-256、按 DOI/题名/内容哈希去重，对 PDF 调用 pdftotext 抽取文本；
5) 在 /app/work 生成 inventory.json、dedup.json、contract.json，并在 /app/output 生成各主产物的空骨架。

设计目标：空输入、坏 JSON、坏编码、缺 pdftotext 均不崩溃；同一输入重复运行产物一致
（时间戳由环境变量 SKILL_NOW 控制，便于可复现验证）。
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILE_BYTES = 2 * 1024 * 1024
PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
RECORD_EXTS = {".json", ".jsonl", ".csv", ".bib", ".ris", ".xml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | RECORD_EXTS
META_NAMES = {"case.json", "task.md", "result.json"}
SKIP_DIRS = {".git", "__pycache__"}

# 未在 case.json 中找到 output_contract 时的回退字段（类型是回填依据）。
DEFAULT_CONTRACT_FIELDS = {
    "research_question": None,
    "screening_criteria": None,
    "screening_log": [],
    "evidence_table": [],
    "themes": [],
    "review_draft": None,
    "conflicts_and_gaps": [],
    "references": [],
}

ARTIFACT_NAMES = {
    "review_md": "review.md",
    "screening_log_json": "screening_log.json",
    "evidence_table_jsonl": "evidence_table.jsonl",
    "themes_md": "themes.md",
    "conflicts_and_gaps_md": "conflicts_and_gaps.md",
}


def now_ts():
    """返回 UTC 时间戳；SKILL_NOW 固定时可用于确定性复现。"""
    fixed = os.environ.get("SKILL_NOW")
    if fixed:
        return fixed
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("[lit-review] " + msg, flush=True)


def read_text(path):
    """按 utf-8-sig / utf-8 / latin-1 顺序容错读取；失败返回 (None, None, err)。"""
    last = "未知错误"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc, None
        except UnicodeDecodeError as e:
            last = str(e)
        except Exception as e:  # noqa: BLE001
            return None, None, str(e)
    return None, None, last


def read_json(path):
    text, _enc, err = read_text(path)
    if text is None:
        return None, ["无法读取: " + (err or "未知错误")]
    try:
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001
        return None, ["JSON 解析失败: " + str(e)]


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# output_contract 归一化
# --------------------------------------------------------------------------- #

def default_for_spec(spec):
    """依据 schema/type 提示给默认值；数组 []，对象 {}，其余 null。"""
    if isinstance(spec, dict):
        if "default" in spec:
            return spec["default"]
        t = spec.get("type")
        types = t if isinstance(t, list) else [t] if t is not None else []
        lower = [str(x).lower() for x in types]
        if "null" in lower:
            return None
        if "array" in lower:
            return []
        if "object" in lower:
            return {}
        if "boolean" in lower:
            return False
        if "integer" in lower or "number" in lower:
            return None
        if "items" in spec:
            return []
        if "properties" in spec:
            return {}
    return None


def fields_from(v):
    """把多种形态的 output_contract 归一化为 [(字段名, 默认值), ...]，保持顺序。"""
    if isinstance(v, dict):
        props = v.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        fl = v.get("fields")
        if isinstance(fl, (dict, list)):
            return fields_from(fl)
        # 视作 name -> spec/默认值 的映射
        return [(name, default_for_spec(v[name])) for name in v]
    if isinstance(v, list):
        out = []
        for item in v:
            if isinstance(item, str):
                out.append((item, None))
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id")
                      or item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    out.append((nm, default_for_spec(item)))
        return out
    if isinstance(v, str) and v.strip():
        return [(v, None)]
    return None


def find_output_contract(node):
    """递归查找 output_contract / output_schema；找不到返回 None。"""
    if isinstance(node, dict):
        for k, val in node.items():
            if k.lower() in ("output_contract", "outputcontract",
                             "output_schema", "outputschema"):
                return val
        for val in node.values():
            r = find_output_contract(val)
            if r is not None:
                return r
    elif isinstance(node, list):
        for item in node:
            r = find_output_contract(item)
            if r is not None:
                return r
    return None


def extract_case_id(case, case_path):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "id", "challenge_id", "caseID"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    if isinstance(case, dict):
        for k in ("research_question", "question", "query", "prompt",
                  "objective", "title"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        t = case.get("task")
        if isinstance(t, dict):
            for k in ("research_question", "question", "prompt", "description",
                      "objective", "title"):
                v = t.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def find_case_file(input_dir):
    """先找 case.json；找不到再找任一含 output_contract 的 .json。"""
    p = os.path.join(input_dir, "case.json")
    if os.path.isfile(p):
        return p
    for root, _dirs, files in os.walk(input_dir):
        for f in sorted(files):
            if f.lower() == "case.json":
                return os.path.join(root, f)
    # 兜底：任一 json 含 output_contract
    for root, _dirs, files in os.walk(input_dir):
        for f in sorted(files):
            if f.lower().endswith(".json"):
                fp = os.path.join(root, f)
                data, err = read_json(fp)
                if err is None and find_output_contract(data) is not None:
                    return fp
    return None


# --------------------------------------------------------------------------- #
# 材料发现与去重
# --------------------------------------------------------------------------- #

def norm_key(s):
    """小写、去空白、去常见标点，用于题名/DOI 归一化。"""
    s = re.sub(r"\s+", " ", str(s or "")).strip().lower()
    s = re.sub(r"[\u2018\u2019\u201c\u201d\"']", "", s)
    return s


def record_meta_keys(path):
    """从题录文件提取候选去重键（doi/题名）。解析失败返回空列表。"""
    keys = []
    data, err = read_json(path)
    if err is not None:
        return keys
    records = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for k in ("records", "items", "papers", "references", "documents",
                  "entries"):
            v = data.get(k)
            if isinstance(v, list):
                records = v
                break
        if not records and any(k in data for k in ("doi", "title", "name")):
            records = [data]
    for rec in records:
        if not isinstance(rec, dict):
            continue
        doi = rec.get("doi") or rec.get("DOI") or rec.get("id")
        if isinstance(doi, str) and doi.strip():
            keys.append(("doi", norm_key(doi)))
        title = (rec.get("title") or rec.get("name")
                 or rec.get("TI") or rec.get("article_title"))
        if isinstance(title, str) and title.strip():
            keys.append(("title", norm_key(title)))
    return keys


def discover_materials(input_dir):
    """扫描材料文件；返回 (materials, scan_errors)。材料按路径排序保证确定性。"""
    materials = []
    scan_errors = []
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            fp = os.path.join(root, f)
            name = f.lower()
            if name in META_NAMES:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in MATERIAL_EXTS:
                continue
            try:
                size = os.path.getsize(fp)
            except OSError as e:
                scan_errors.append({"path": fp, "error": str(e)})
                continue
            if size > MAX_FILE_BYTES:
                scan_errors.append({"path": fp, "error":
                                    "超出单文件上限 %d 字节，已跳过" % MAX_FILE_BYTES})
                continue
            materials.append({"path": fp, "ext": ext, "bytes": size})
    materials.sort(key=lambda m: m["path"])
    return materials, scan_errors


def dedup_materials(materials):
    """按 DOI/题名/内容哈希去重；保留排序靠前者。返回 (kept, dedup_records)。"""
    owner = {}   # key -> 保留材料路径
    kept = []
    dedup_records = []
    for m in materials:
        digest = sha256_file(m["path"])
        keys = []
        if digest:
            keys.append(("sha256", digest))
        if m["ext"] in RECORD_EXTS:
            keys.extend(record_meta_keys(m["path"]))
        hit = next(((k, v) for k, v in keys
                    if v and (k + ":" + v) in owner), None)
        if hit is not None:
            dedup_records.append({
                "removed_path": m["path"],
                "kept_path": owner[hit[0] + ":" + hit[1]],
                "reason": hit[0],
                "key": hit[1]})
        else:
            for k, v in keys:
                if v:
                    owner[k + ":" + v] = m["path"]
            kept.append(m)
    return kept, dedup_records


# --------------------------------------------------------------------------- #
# PDF 抽取
# --------------------------------------------------------------------------- #

def extract_pdf(src, out_path, degradations):
    try:
        cmd = ["pdftotext", "-enc", "UTF-8", src, out_path]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=90)
        if proc.returncode != 0:
            degradations.append({"path": src,
                                 "error": "pdftotext 退出码 %d: %s" % (
                                     proc.returncode,
                                     proc.stderr.decode("utf-8", "replace")[:200])})
            return "extraction_failed"
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            degradations.append({"path": src, "error": "pdftotext 产出为空"})
            return "extraction_empty"
        return "extracted"
    except FileNotFoundError:
        degradations.append({"path": src, "error": "缺少 pdftotext，未能抽取 PDF"})
        return "extraction_failed"
    except Exception as e:  # noqa: BLE001
        degradations.append({"path": src, "error": str(e)})
        return "extraction_failed"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def build_result_skeleton(case_id, contract_fields, question, artifacts):
    result = {"case_id": case_id}
    for name, default in contract_fields:
        if name == "research_question" and question:
            result[name] = question
        elif name == "case_id" or name == "status":
            continue
        else:
            result[name] = default
    result["status"] = "partial"
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [
        {"ts": now_ts(), "phase": "phase0", "action": "scaffold_written",
         "detail": "确定性脚手架落盘，status=partial；后续 Phase 仅增量改进"}
    ]
    result["errors"] = []
    result["degradations"] = []
    result["limitations"] = []
    result["runtime"] = {"started_at": now_ts(), "elapsed_seconds": 0,
                         "note": "由 Phase 4 校验后更新"}
    result["artifacts"] = artifacts
    return result


def write_templates(output_dir, question):
    q = question or "（来自 case.json；未给出时为 null）"
    write_text(os.path.join(output_dir, "review.md"),
               "# 综述草稿（骨架）\n\n"
               "> 状态：PARTIAL_SCAFFOLD —— 由 Phase 3 覆盖为正式草稿；本文件不含正式结论。\n\n"
               "研究问题：" + q + "\n\n"
               "<!-- 正文每句实质论断必须带 [S#] 标记，指向 evidence_table.jsonl 的 evidence_id。 -->\n"
               "<!-- 冲突结论并列并标 disputed；研究空白落到具体证据缺口，未报告不等于不存在。 -->\n")
    write_text(os.path.join(output_dir, "themes.md"),
               "# 主题归纳（骨架）\n\n"
               "> 状态：PARTIAL_SCAFFOLD —— 由 Phase 2 覆盖；本文件不含正式结论。\n\n"
               "<!-- 主题/子主题从证据聚类（方法、对象、结论分歧）生成，不套背景/方法/挑战/展望通用框架。 -->\n"
               "<!-- 每个主题列出支撑 source_id 与边界，避免主题重叠或遗漏。 -->\n")
    write_text(os.path.join(output_dir, "conflicts_and_gaps.md"),
               "# 冲突与研究空白（骨架）\n\n"
               "> 状态：PARTIAL_SCAFFOLD —— 由 Phase 3 覆盖；本文件不含正式结论。\n\n"
               "<!-- 冲突：并列 claim_a / claim_b + 各自 evidence_id + type(result_conflict|method_mutual_exclusion)。 -->\n"
               "<!-- 空白：gap_id + 具体证据缺口 + 检索范围说明；区分 not_reported 与不存在。 -->\n")
    write_json(os.path.join(output_dir, "screening_log.json"), {
        "criteria_written_before_screening": True,
        "screening_criteria": {
            "population": None,
            "include": [],
            "exclude": [],
            "dedup_rule": "doi_normalized|title_normalized|content_sha256"
        },
        "dedup_records": [],
        "decisions": []
    })
    write_text(os.path.join(output_dir, "evidence_table.jsonl"), "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="文献综述合成 Skill 脚手架")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    extracted_dir = os.path.join(args.work, "extracted")
    os.makedirs(extracted_dir, exist_ok=True)

    errors = []
    degradations = []

    # 1) 发现并解析 case.json / task.md
    case_path = find_case_file(args.input)
    case = None
    if case_path is None:
        errors.append("未找到 case.json，也没有含 output_contract 的 JSON 输入")
    else:
        case, perr = read_json(case_path)
        if perr:
            errors.extend(perr)

    case_id = extract_case_id(case, case_path)
    question = extract_question(case)
    contract = find_output_contract(case)

    # 2) 归一化 contract 字段；坏 JSON 或缺失时用回退字段。
    contract_fields = fields_from(contract)
    if contract_fields is None:
        contract_fields = list(DEFAULT_CONTRACT_FIELDS.items())
        if contract is not None:
            degradations.append("output_contract 结构无法归一化，采用回退字段集")

    # 3) 扫描与去重材料
    materials, scan_errors = discover_materials(args.input)
    errors.extend(scan_errors)
    kept, dedup_records = dedup_materials(materials)

    # 4) 分配 source_id 并抽取 PDF
    inventory_materials = []
    for i, m in enumerate(kept):
        source_id = "src_%02d" % (i + 1)
        item = {
            "source_id": source_id,
            "path": m["path"],
            "ext": m["ext"],
            "bytes": m["bytes"],
            "sha256": sha256_file(m["path"]),
            "extracted_path": None,
            "extraction_status": "not_needed",
        }
        if m["ext"] in PDF_EXTS:
            out_path = os.path.join(extracted_dir, source_id + ".txt")
            st = extract_pdf(m["path"], out_path, degradations)
            item["extraction_status"] = st
            if st == "extracted":
                item["extracted_path"] = out_path
        elif m["ext"] in TEXT_EXTS:
            item["extraction_status"] = "text"
            item["extracted_path"] = m["path"]
        else:
            item["extraction_status"] = "record"
        inventory_materials.append(item)

    inventory = {
        "input_dir": os.path.abspath(args.input),
        "scanned_at": now_ts(),
        "case_file": case_path,
        "task_file": (os.path.join(args.input, "task.md")
                      if os.path.isfile(os.path.join(args.input, "task.md"))
                      else None),
        "materials": inventory_materials,
        "dedup": dedup_records,
    }
    write_json(os.path.join(args.work, "inventory.json"), inventory)
    write_json(os.path.join(args.work, "dedup.json"),
               {"records": dedup_records, "kept": [m["source_id"]
                                                   for m in inventory_materials]})
    write_json(os.path.join(args.work, "contract.json"),
               {"fields": [n for n, _ in contract_fields],
                "raw_contract": contract})

    # 5) 落盘主产物骨架 + 合法 result.json
    write_templates(args.output, question)
    artifacts = dict(ARTIFACT_NAMES)
    result = build_result_skeleton(case_id, contract_fields, question, artifacts)
    if errors:
        result["errors"] = errors
    if degradations:
        result["degradations"] = degradations
    if case_path is None or case is None:
        result["status"] = "input_error"
        result["limitations"].append(
            "输入不可解析（缺少 case.json 或 JSON 非法）；采用回退字段集，不生成示例内容")
    elif not kept:
        result["status"] = "insufficient_evidence"
        result["limitations"].append(
            "未发现可读材料文件；sources/uncertainty 保持为空，不生成示例内容")
    write_json(os.path.join(args.output, "result.json"), result)

    log("case_id=%s materials=%d kept=%d dedup=%d status=%s" % (
        case_id, len(materials), len(kept), len(dedup_records), result["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
