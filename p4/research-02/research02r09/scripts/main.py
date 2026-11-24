#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lit-review-synth 确定性脚手架（仅 Python 标准库）。

职责：
1) 发现 /input 下的 case.json、task.md 与材料文件；
2) 解析 case.json 中的 output_contract，回填其全部字段（缺值给 null/[]，不改名、不删键）；
3) 立刻写出一版合法 result.json（status=partial）作为安全骨架；
4) 扫描材料、计算 SHA-256 去重、对 PDF 调用 pdftotext 抽取文本；
5) 生成 work/inventory.json、screening_log.json 模板、空 evidence_table.jsonl 及产物占位文件。

设计目标：空输入、坏 JSON、坏编码、缺软件都不崩溃；重复运行结构一致且不覆盖已填充的产物。
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
MAX_FILE_BYTES = 2 * 1024 * 1024
PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
RECORD_EXTS = {".json", ".jsonl", ".csv", ".bib", ".ris", ".xml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | RECORD_EXTS
META_NAMES = {"case.json", "task.md", "result.json"}

DEFAULT_CONTRACT = {
    "research_question": None,
    "screening_criteria": None,
    "screening_log": [],
    "evidence_table": [],
    "themes": [],
    "review_draft": None,
    "conflicts_and_gaps": [],
    "references": [],
}

ARTIFACT_FILES = {
    "review": "review.md",
    "screening_log": "screening_log.json",
    "evidence_table": "evidence_table.jsonl",
    "themes": "themes.md",
    "conflicts_and_gaps": "conflicts_and_gaps.md",
}

PLACEHOLDER = {
    "review.md": "# 综述草稿\n\n（骨架占位：Phase 3 由 Agent 填充；每句实质论断须带 [S#] 标记）\n",
    "themes.md": "# 主题归纳\n\n（骨架占位：Phase 2 由 Agent 从证据聚类生成，列出每个主题的支撑文献与边界）\n",
    "conflicts_and_gaps.md": "# 冲突与研究空白\n\n（骨架占位：Phase 3 由 Agent 并列呈现冲突结论并落到具体证据缺口）\n",
}


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print("[lit-review-synth] " + msg, flush=True)


def read_text(path):
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
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None


def default_for_spec(spec):
    """依据 schema/type 提示：array→[]，object→{}，可空数组→null，其余→null。"""
    if isinstance(spec, dict):
        t = spec.get("type")
        if isinstance(t, str):
            tl = t.lower()
            if tl == "array":
                return []
            if tl == "object":
                return {}
        elif isinstance(t, list):
            ts = [str(x).lower() for x in t]
            if "array" in ts and "null" in ts:
                return None
            if "array" in ts:
                return []
            if "object" in ts and "null" in ts:
                return None
            if "object" in ts:
                return {}
        if "properties" in spec:
            return {}
        if "items" in spec:
            return []
        if "default" in spec:
            return spec["default"]
    return None


def fields_from(v):
    """把多种形态的 output_contract 归一化为 [(字段名, 默认值), ...]。"""
    if isinstance(v, dict):
        props = v.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        fl = v.get("fields")
        if isinstance(fl, (dict, list)):
            return fields_from(fl)
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
        for k, v in node.items():
            if k.lower() in ("output_contract", "outputcontract", "output_schema", "outputschema"):
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
        for k in ("case_id", "caseId", "id", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    if isinstance(case, dict):
        for k in ("research_question", "question", "query", "prompt", "title", "objective"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        t = case.get("task")
        if isinstance(t, dict):
            for k in ("research_question", "question", "prompt", "description", "title", "objective"):
                v = t.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return None


def find_case_file(input_dir):
    direct = os.path.join(input_dir, "case.json")
    if os.path.isfile(direct):
        return direct
    if os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            for f in sorted(files):
                if f.lower() == "case.json":
                    return os.path.join(root, f)
        for root, _dirs, files in os.walk(input_dir):
            for f in sorted(files):
                if f.lower().endswith(".json"):
                    p = os.path.join(root, f)
                    data, _errs = read_json(p)
                    if isinstance(data, dict) and find_output_contract(data) is not None:
                        return p
    return None


def classify(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext in RECORD_EXTS:
        return "record"
    return None


def scan_materials(input_dir):
    materials = []
    skipped = []
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            if os.path.basename(f).lower() in META_NAMES:
                continue
            kind = classify(p)
            if kind is None:
                continue
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = 0
            if sz > MAX_FILE_BYTES:
                skipped.append({"path": p, "reason": "size_exceeds_limit"})
                continue
            materials.append({
                "path": p,
                "kind": kind,
                "size": sz,
                "sha256": sha256_file(p),
            })
    return materials, skipped


def dedup(materials):
    """按 SHA-256 去重；同哈希只保留排序路径最前的一条，其余记 duplicate。"""
    by_hash = {}
    unique = []
    dups = []
    for m in sorted(materials, key=lambda x: x["path"]):
        h = m.get("sha256")
        if h is None:
            unique.append(m)
            continue
        if h in by_hash:
            dups.append(m)
            by_hash[h]["paths"].append(m["path"])
        else:
            by_hash[h] = {"hash": h, "paths": [m["path"]], "kept": m["path"]}
            unique.append(m)
    dup_list = [v for v in by_hash.values() if len(v["paths"]) > 1]
    return unique, dups, dup_list


def assign_source_ids(unique):
    out = []
    for i, m in enumerate(sorted(unique, key=lambda x: x["path"])):
        m2 = dict(m)
        m2["source_id"] = "S%02d" % (i + 1)
        out.append(m2)
    return out


def extract_pdf(pdf_path, out_path):
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext 不可用"
    try:
        proc = subprocess.run(
            [exe, "-enc", "UTF-8", pdf_path, out_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if proc.returncode != 0:
            msg = proc.stderr.decode("utf-8", "replace")[:200] if proc.stderr else ""
            return None, "pdftotext 退出码 %d: %s" % (proc.returncode, msg)
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            return out_path, None
        return None, "抽取结果为空"
    except subprocess.TimeoutExpired:
        return None, "pdftotext 超时"
    except Exception as e:  # noqa: BLE001
        return None, "pdftotext 异常: " + str(e)


def build_skeleton(args, case_id, contract_fields, started, errors, degradations):
    result = {
        "case_id": case_id,
        "status": "input_error" if errors else "partial",
        "artifacts": {label: os.path.join(args.output, fname)
                      for label, fname in ARTIFACT_FILES.items()},
    }
    for name, default in contract_fields:
        result[name] = default
    result.update({
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": errors or [],
        "degradations": degradations or [],
        "change_log": [{"time": started, "event": "骨架落盘（status=partial）"}],
        "runtime": {"started_at": started, "finished_at": None},
    })
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="文献综述合成脚手架：发现材料、回填契约、落盘骨架并抽取 PDF 文本。")
    ap.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    ap.add_argument("--output", default="/app/output", help="输出目录（默认 /app/output）")
    ap.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    ap.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    ap.add_argument("--force-extract", action="store_true", help="重新抽取已存在的 PDF 文本")
    ap.add_argument("--reset", action="store_true", help="覆盖已存在的 result.json 与占位产物")
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    started = now_ts()
    errors = []
    degradations = []

    # ---- case.json ----
    case_path = find_case_file(args.input)
    case = None
    if case_path is None:
        errors.append("未找到 case.json（已扫描 %s）" % args.input)
    else:
        case, case_errors = read_json(case_path)
        if case_errors:
            errors.extend(case_errors)

    case_id = extract_case_id(case, case_path)
    question = extract_question(case)

    # ---- 契约字段 ----
    spec = find_output_contract(case) if isinstance(case, (dict, list)) else None
    contract_fields = fields_from(spec) if spec is not None else None
    if not contract_fields:
        contract_fields = [(k, v) for k, v in DEFAULT_CONTRACT.items()]
    if question:
        contract_fields = [
            (name, question if name.lower() in ("research_question", "question", "query") else default)
            for name, default in contract_fields
        ]

    # ---- 骨架（先落盘，任何后续异常都不留空产物）----
    result_path = os.path.join(args.output, "result.json")
    if args.reset or not os.path.isfile(result_path):
        result = build_skeleton(args, case_id, contract_fields, started, errors, degradations)
        write_json(result_path, result)
        log("已写骨架 result.json（status=%s）" % result["status"])
    else:
        log("result.json 已存在，跳过骨架写入（如需重置加 --reset）")
        result, _errs = read_json(result_path) or ({}, ["无法读取已有 result.json"])
        result.setdefault("sources", [])
        result.setdefault("uncertainty", [])
        result.setdefault("pending_human", [])
        result.setdefault("errors", [])
        result.setdefault("degradations", [])
        result.setdefault("change_log", [])
        result.setdefault("runtime", {"started_at": started, "finished_at": None})

    # ---- 占位产物（不覆盖已填充文件）----
    for fname, text in PLACEHOLDER.items():
        p = os.path.join(args.output, fname)
        if args.reset or not os.path.isfile(p):
            write_text(p, text)
    ev_path = os.path.join(args.output, "evidence_table.jsonl")
    if args.reset or not os.path.isfile(ev_path):
        write_text(ev_path, "")

    # ---- 材料盘点 ----
    materials, skipped = scan_materials(args.input)
    unique, dups, dup_list = dedup(materials)
    sources = assign_source_ids(unique)

    extracted_dir = os.path.join(args.work, "extracted")
    os.makedirs(extracted_dir, exist_ok=True)
    for s in sources:
        if s["kind"] != "pdf":
            continue
        out_path = os.path.join(extracted_dir, s["source_id"] + ".txt")
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0 and not args.force_extract:
            s["extracted"] = out_path
            continue
        ep, err = extract_pdf(s["path"], out_path)
        if ep:
            s["extracted"] = ep
        else:
            s["extracted"] = None
            s["extraction_error"] = err
            degradations.append({"source_id": s["source_id"], "path": s["path"], "detail": err})

    # ---- inventory ----
    inventory = {
        "case_id": case_id,
        "research_question": question,
        "scanned_at": started,
        "materials": sources,
        "duplicates": dup_list,
        "excluded_duplicates": [d["path"] for d in dups],
        "skipped": skipped,
        "degradations": degradations,
    }
    write_json(os.path.join(args.work, "inventory.json"), inventory)

    # ---- screening_log 模板（仅缺失或 --reset 时写，避免覆盖 Agent 填充）----
    scr_path = os.path.join(args.output, "screening_log.json")
    if args.reset or not os.path.isfile(scr_path):
        screening = {
            "criteria": {
                "research_question": question,
                "inclusion": [],
                "exclusion": [],
                "dedup_rule": "SHA-256 内容哈希；相同哈希只保留排序路径最前的一条",
            },
            "decisions": [
                {"source_id": s["source_id"], "path": s["path"],
                 "decision": "pending", "reason": None}
                for s in sources
            ],
            "duplicates": dup_list,
        }
        write_json(scr_path, screening)

    # ---- 更新 result.json：sources / degradations / change_log ----
    result["sources"] = [
        {
            "source_id": s["source_id"],
            "path": s["path"],
            "kind": s["kind"],
            "size_bytes": s["size"],
            "sha256": s["sha256"],
            "extracted": s.get("extracted"),
            "note": s.get("extraction_error"),
        }
        for s in sources
    ]
    result["degradations"] = list(degradations)
    if "runtime" not in result or not isinstance(result.get("runtime"), dict):
        result["runtime"] = {"started_at": started, "finished_at": None}
    result["runtime"]["started_at"] = started
    inventory_event = "材料盘点完成：%d 条唯一来源、%d 条去重、%d 条抽取失败" % (
        len(sources), len(dups), len(degradations))
    change_log = list(result.get("change_log") or [])
    if not change_log or change_log[-1].get("event") != inventory_event:
        change_log.append({"time": started, "event": inventory_event})
    result["change_log"] = change_log
    write_json(result_path, result)

    log("唯一来源 %d、去重 %d、PDF 抽取失败 %d" % (len(sources), len(dups), len(degradations)))
    if degradations:
        for d in degradations:
            log("抽取降级: %s -> %s" % (d.get("source_id"), d.get("detail")))
    log("完成。result.json -> %s" % result_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
