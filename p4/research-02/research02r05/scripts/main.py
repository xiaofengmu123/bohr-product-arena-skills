#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的确定性脚手架（仅 Python 标准库）。

职责（Phase 0 安全骨架）：
1) 发现 /input 下的 case.json、task.md 与材料文件；
2) 解析 case.json 中的 output_contract，构造与其逐字段对齐的契约负载
   （键名、嵌套结构、类型缺省值均保持，未知键不删、不改名）；
3) 立刻写出一版合法 result.json（含 case_id、status 与全部契约字段，
   以及 sources / uncertainty / pending_human / change_log / errors /
   degradations / runtime / artifacts 诚实字段）；
4) 扫描材料、计算 SHA-256 供去重、对 PDF 调用 pdftotext 抽取文本；
5) 生成 work/inventory.json、screening_log.json 模板与空 evidence_table.jsonl。

设计目标：空输入、坏 JSON、坏编码、缺软件都不崩溃；重复运行结果一致；
本脚本不写入任何"预抽取答案层"，不编造元数据、分数或运行结果。
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

ARTIFACT_NAMES = [
    "review.md",
    "screening_log.json",
    "evidence_table.jsonl",
    "themes.md",
    "conflicts_and_gaps.md",
]

SCHEMA_META_KEYS = {
    "type", "properties", "required", "additionalProperties", "title",
    "description", "$schema", "definitions", "items", "enum", "examples",
    "default", "format", "const",
}


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("[lit-review] " + msg, flush=True)


def read_text(path):
    """按 utf-8-sig / utf-8 / latin-1 顺序容错读取；绝不抛异常。"""
    last = "unknown error"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc, None
        except UnicodeDecodeError as e:
            last = str(e)
        except Exception as e:
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


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    except Exception:
        return None


# ---------------------------------------------------------------------------
# output_contract 解析：目标是"逐字段对齐"，保留键名与嵌套结构。
# ---------------------------------------------------------------------------

def default_for_spec(spec):
    """依据 schema 提示给 array -> []，object -> {}(含嵌套 properties)，其余 -> null。"""
    if isinstance(spec, dict):
        tset = set()
        t = spec.get("type")
        if isinstance(t, str):
            tset.add(t.lower())
        elif isinstance(t, list):
            tset.update(str(x).lower() for x in t)
        if "object" in tset or "properties" in spec:
            props = spec.get("properties")
            if isinstance(props, dict):
                return {k: default_for_spec(v) for k, v in props.items()}
            return {}
        if "array" in tset or "items" in spec:
            return []
        return None
    if isinstance(spec, str):
        s = spec.lower()
        if s in ("array", "list"):
            return []
        if s in ("object", "dict"):
            return {}
        return None
    return None


def build_contract_payload(contract):
    """把多种形态的 output_contract 归一化为一个逐字段对齐的字典。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            payload = {name: default_for_spec(spec) for name, spec in props.items()}
            # 必填但未在 properties 中出现的字段也要占位
            req = contract.get("required")
            if isinstance(req, list):
                for name in req:
                    payload.setdefault(name, None)
            return payload
        fl = contract.get("fields")
        if fl is not None:
            return build_contract_payload(fl)
        # name -> spec 的映射形态（跳过 schema 元键）
        payload = {}
        for name, spec in contract.items():
            if name in SCHEMA_META_KEYS:
                continue
            payload[name] = default_for_spec(spec)
        return payload
    if isinstance(contract, list):
        payload = {}
        for item in contract:
            if isinstance(item, str):
                payload[item] = None
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id")
                      or item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    payload[nm] = default_for_spec(item)
        return payload
    if isinstance(contract, str) and contract.strip():
        return {contract.strip(): None}
    return dict(DEFAULT_CONTRACT_FIELDS)


def contract_shape(contract):
    """返回 (来源形态, 顶层字段名列表)，供审计与自检使用。"""
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            return "schema.properties", sorted(contract["properties"].keys())
        if "fields" in contract:
            _, names = contract_shape(contract["fields"])
            return "schema.fields", names
        names = sorted(k for k in contract if k not in SCHEMA_META_KEYS)
        return "map", names
    if isinstance(contract, list):
        names = []
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                nm = (item.get("name") or item.get("field_id")
                      or item.get("id") or item.get("key") or item.get("field"))
                if nm:
                    names.append(nm)
        return "list", sorted(names)
    if isinstance(contract, str) and contract.strip():
        return "string", [contract.strip()]
    return "default", sorted(DEFAULT_CONTRACT_FIELDS.keys())


def find_output_contract(node):
    """递归查找 output_contract / output_schema；找不到返回 None。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in ("output_contract", "outputcontract",
                             "output_schema", "outputschema"):
                return v
        for v in node.values():
            r = find_output_contract(v)
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
        for k in ("case_id", "caseId", "id", "challenge_id", "armiId"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    base = os.path.basename(case_path or "") or "unknown"
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    if isinstance(case, dict):
        for k in ("research_question", "question", "query", "prompt",
                  "title", "objective", "task_description"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        t = case.get("task")
        if isinstance(t, dict):
            for k in ("research_question", "question", "prompt",
                      "description", "title", "objective"):
                v = t.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def find_case_file(input_dir):
    p = os.path.join(input_dir, "case.json")
    if os.path.isfile(p):
        return p
    if os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            for f in sorted(files):
                if f.lower() == "case.json":
                    return os.path.join(root, f)
    return None


# ---------------------------------------------------------------------------
# 材料发现与抽取
# ---------------------------------------------------------------------------

def _looks_like_path(s):
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s or len(s) > 1024:
        return False
    if any(ch in s for ch in ("\n", "\t")):
        return False
    return ("/" in s or "\\" in s or s.lower().endswith(
        tuple(sorted(MATERIAL_EXTS))) or "materials" in s.lower())


def _collect_path_strings(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower() in ("path", "file", "files", "pdf", "url") and _looks_like_path(v):
                out.append(v)
            else:
                _collect_path_strings(v, out)
    elif isinstance(node, list):
        for item in node:
            _collect_path_strings(item, out)
    elif _looks_like_path(node):
        out.append(node)


def resolve_material_path(ref, input_dir):
    """把 case.json 里出现的路径字符串解析为真实存在的文件。"""
    candidates = []
    s = ref.strip()
    if s.startswith(("http://", "https://")):
        return None
    if os.path.isabs(s):
        candidates.append(s)
    candidates.append(os.path.join(input_dir, s))
    candidates.append(os.path.join(input_dir, "materials", os.path.basename(s)))
    for c in candidates:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return None


def discover_materials(input_dir, case):
    """返回去重后的材料清单；对内容相同的文件按 SHA-256 去重。"""
    seen_paths = set()
    seen_hashes = {}
    items = []

    def add(path):
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            return
        ap = os.path.abspath(path)
        if ap in seen_paths:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in MATERIAL_EXTS:
            return
        if os.path.basename(path).lower() in META_NAMES:
            return
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            items.append({
                "path": path, "rel": os.path.relpath(path, input_dir),
                "ext": ext, "kind": _kind(ext), "size": size,
                "sha256": None, "skipped": "size_exceeds_2MB",
            })
            return
        h = sha256_file(path)
        if h and h in seen_hashes:
            items.append({
                "path": path, "rel": os.path.relpath(path, input_dir),
                "ext": ext, "kind": _kind(ext), "size": size,
                "sha256": h, "duplicate_of": seen_hashes[h],
            })
            return
        seen_paths.add(ap)
        if h:
            seen_hashes[h] = os.path.relpath(path, input_dir)
        items.append({
            "path": path, "rel": os.path.relpath(path, input_dir),
            "ext": ext, "kind": _kind(ext), "size": size, "sha256": h,
        })

    if os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            for f in sorted(files):
                if f.lower() in META_NAMES:
                    continue
                add(os.path.join(root, f))
    refs = []
    if isinstance(case, dict):
        _collect_path_strings(case, refs)
    for ref in refs:
        resolved = resolve_material_path(ref, input_dir)
        if resolved:
            add(resolved)
    return items


def _kind(ext):
    if ext in PDF_EXTS:
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext in RECORD_EXTS:
        return "record"
    return "other"


def extract_pdf_text(pdf_path, out_path):
    """调用 pdftotext 抽取文本；失败返回错误字符串，不伪造结果。"""
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", pdf_path, out_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        if r.returncode != 0 or not os.path.isfile(out_path):
            return (r.stderr.decode("utf-8", "replace") or "pdftotext 无输出").strip()
        return None
    except FileNotFoundError:
        return "pdftotext 不可用（未安装）"
    except Exception as e:
        return "pdftotext 执行异常: " + str(e)


# ---------------------------------------------------------------------------
# Phase 0 主流程
# ---------------------------------------------------------------------------

def build_result(case_id, status, payload, errors, degradations, question,
                 started, phase0_done, artifacts_status, contract_meta):
    result = {
        "case_id": case_id,
        "status": status,
    }
    # 契约字段：原样展开（键名/嵌套结构不变）
    for k, v in payload.items():
        result.setdefault(k, v)
    # 诚实字段（如与契约键重名，诚实字段优先，并记录冲突）
    collisions = [k for k in payload if k in (
        "status", "sources", "uncertainty", "pending_human", "change_log",
        "errors", "degradations", "runtime", "artifacts")]
    result["sources"] = []
    result["uncertainty"] = {
        "conflicts": [],
        "biases": [],
        "gaps": [],
        "undetermined": [],
    }
    result["pending_human"] = []
    result["change_log"] = [{
        "ts": now_ts(),
        "step": "phase0",
        "action": "skeleton_written",
        "detail": "落盘合法骨架：全部契约字段占位，status=%s" % status,
    }]
    result["errors"] = errors
    result["degradations"] = degradations
    elapsed = None
    try:
        t0 = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
        elapsed = round(time.time() - t0, 3)
    except Exception:
        pass
    result["runtime"] = {
        "started_at": started,
        "phase0_done_at": phase0_done,
        "elapsed_s": elapsed,
    }
    result["artifacts"] = artifacts_status
    result["output_contract_fields"] = contract_meta["fields"]
    result["output_contract_source"] = contract_meta["source"]
    if collisions:
        result["change_log"].append({
            "ts": now_ts(), "step": "phase0",
            "action": "key_collision",
            "detail": "契约字段与诚实字段重名，诚实字段优先: " + ",".join(collisions),
        })
    if question and not result.get("research_question"):
        result["research_question"] = question
    return result


def run_phase0(args):
    started = now_ts()
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    errors, degradations = [], []

    case_path = find_case_file(args.input)
    case, case_err = (None, []) if case_path is None else read_json(case_path)
    if case_err:
        errors.extend(case_err)

    materials = discover_materials(args.input, case)
    non_dup = [m for m in materials if "duplicate_of" not in m and "skipped" not in m]

    # 状态判定（诚实，不编造）
    if case_path is None and not non_dup:
        status = "insufficient_evidence"
    elif case is None and case_path is not None:
        status = "input_error"
    else:
        status = "partial"

    if case is None and case_path is None:
        errors.append("未找到 case.json")
    if case_path is not None and case is None:
        errors.append("case.json 不可解析，已按默认契约输出骨架")

    contract = find_output_contract(case) if case is not None else None
    if contract is None:
        contract = dict(DEFAULT_CONTRACT_FIELDS)
        payload = dict(DEFAULT_CONTRACT_FIELDS)
        degradations.append("output_contract 缺失，使用默认契约字段")
        src, names = "default", sorted(DEFAULT_CONTRACT_FIELDS.keys())
    else:
        payload = build_contract_payload(contract)
        src, names = contract_shape(contract)
    contract_meta = {"source": src, "fields": names}
    case_id = extract_case_id(case, case_path) if case is not None else \
        (os.path.splitext(os.path.basename(case_path or ""))[0] or "unknown")
    question = extract_question(case)

    artifacts_status = {}
    for name in ARTIFACT_NAMES:
        artifacts_status[name] = {
            "status": "pending",
            "path": os.path.join(args.output, name),
        }

    # 生成 inventory.json 与抽取文本
    extracted_dir = os.path.join(args.work, "extracted")
    os.makedirs(extracted_dir, exist_ok=True)
    inventory = {
        "generated_at": now_ts(),
        "input_dir": args.input,
        "case_file": case_path,
        "materials": materials,
    }
    for i, m in enumerate(materials):
        if m.get("skipped") or m.get("duplicate_of"):
            continue
        if m["kind"] == "pdf":
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", m["rel"]) or ("doc_%d" % i)
            out_path = os.path.join(extracted_dir, safe + ".txt")
            err = extract_pdf_text(m["path"], out_path)
            if err:
                degradations.append("PDF 抽取失败 %s: %s" % (m["rel"], err))
                m["extracted"] = None
            else:
                m["extracted"] = out_path
        elif m["kind"] == "text":
            txt, _enc, err = read_text(m["path"])
            if txt is None:
                degradations.append("文本读取失败 %s: %s" % (m["rel"], err or "未知"))
            else:
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", m["rel"]) or ("txt_%d" % i)
                out_path = os.path.join(extracted_dir, safe)
                write_text(out_path, txt)
                m["extracted"] = out_path

    write_json(os.path.join(args.work, "inventory.json"), inventory)

    # 模板：筛选标准必须写在逐篇决定之前（先写标准，留空决定区）
    screening_template = {
        "generated_at": now_ts(),
        "note": "纳入/排除标准必须写在逐篇决定之前；Agent 在 Phase 1 填写 criteria 后再填 decisions。",
        "criteria": {
            "research_object": None,
            "include": [],
            "exclude": [],
            "dedup_rule": "按 SHA-256 内容哈希去重；题名/DOI 近似匹配由 Agent 复核。",
        },
        "decisions": [],
    }
    write_json(os.path.join(args.work, "screening_log.json"), screening_template)
    write_text(os.path.join(args.work, "evidence_table.jsonl"), "")

    result = build_result(case_id, status, payload, errors, degradations,
                          question, started, now_ts(), artifacts_status,
                          contract_meta)
    write_json(os.path.join(args.output, "result.json"), result)

    log("case_id=%s status=%s contract_source=%s" % (case_id, status, src))
    log("materials=%d (deduped=%d) degradations=%d" % (
        len(materials), len([m for m in materials if "duplicate_of" in m]), len(degradations)))
    log("skeleton written: %s" % os.path.join(args.output, "result.json"))
    log("next: 读 inventory.json，按 SKILL.md Phase 1-4 增量推进。")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="文献综述合成 Skill Phase 0 骨架生成器（仅标准库）")
    ap.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    ap.add_argument("--output", default="/app/output", help="产物目录（默认 /app/output）")
    ap.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    args = ap.parse_args(argv)
    try:
        return run_phase0(args)
    except Exception as e:
        # 兜底：即使意外异常也尽力留下最小合法产物
        log("fatal: " + str(e))
        try:
            os.makedirs(args.output, exist_ok=True)
            minimal = {
                "case_id": "unknown",
                "status": "input_error",
                "sources": [],
                "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
                "pending_human": ["main.py 异常: " + str(e)],
                "change_log": [{"ts": now_ts(), "step": "phase0",
                                "action": "fatal_fallback", "detail": str(e)}],
                "errors": [str(e)],
                "degradations": [],
            }
            write_json(os.path.join(args.output, "result.json"), minimal)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
