#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的确定性脚手架（仅 Python 标准库）。

本脚本只做"确定可自动化"的部分，绝不生成任何实质结论或数值：
  1) 发现 /input 下的 case.json、task.md 与材料文件；
  2) 解析 case.json 中的 output_contract，把其全部字段归一化并回填
     （缺值给 null/[]，不改名、不删未知键）；
  3) 立即写出一版合法 result.json（status=partial）作为安全骨架；
  4) 写 work/plan.json，把 900 秒 / 64 次请求拆成阶段预算；
  5) 扫描材料、计算 SHA-256 去重、对 PDF 调用 pdftotext 抽取文本；
  6) 生成 work/inventory.json、work/contract.json 与空 evidence_table.jsonl。

设计目标：空输入、坏 JSON、坏编码、缺软件都不崩溃；重复运行结果一致。
所有实质内容（筛选、证据、主题、草稿）由 Agent 在骨架之上增量填充。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
MAX_FILE_BYTES = 2 * 1024 * 1024
PDF_EXTS = {".pdf"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
RECORD_EXTS = {".json", ".jsonl", ".csv", ".bib", ".ris", ".xml"}
MATERIAL_EXTS = PDF_EXTS | TEXT_EXTS | RECORD_EXTS
META_NAMES = {"case.json", "task.md", "result.json"}

# 当 case.json 未提供 output_contract 时的兜底字段（文献综述通用契约）。
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

# 阶段预算：名称、截止秒、请求上限、说明。
PHASE_PLAN = [
    ("phase0", "skeleton", 180, 6, "读取输入、回填契约、落盘 partial 骨架与 plan.json"),
    ("phase1", "inventory_screen", 360, 12, "扫描去重、PDF 抽取、screening_log.json"),
    ("phase2", "evidence_themes", 600, 24, "evidence_table.jsonl、themes.md"),
    ("phase3", "draft_conflicts", 840, 20, "review.md、conflicts_and_gaps.md"),
    ("phase4", "validate_finalize", 900, 2, "validate_result.py、status 定稿、change_log 收尾"),
]


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg):
    print("[lit-review] " + msg, flush=True)


def read_text(path):
    """按 utf-8-sig / utf-8 / latin-1 顺序容错读取；不抛异常。"""
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
    text, _enc, err = read_text(path)
    if text is None:
        return None, ["无法读取: " + (err or "未知错误")]
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, ["JSON 解析失败: " + str(exc)]


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def write_json(path, data):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def write_text(path, text):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def append_jsonl(path, obj):
    ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def sha256_file(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:  # noqa: BLE001
        return None


def default_for_spec(spec):
    """按 schema/type 提示给数组 []、对象 {}，其余 None。"""
    if isinstance(spec, dict):
        stype = spec.get("type")
        if isinstance(stype, str):
            if stype == "array":
                return []
            if stype == "object":
                return {}
        if isinstance(stype, list):
            lowered = [str(x).lower() for x in stype]
            if "array" in lowered and "null" in lowered:
                return None
        if "items" in spec:
            return []
        if "properties" in spec:
            return {}
    return None


def fields_from(value):
    """把多种形态的 output_contract 归一化为 [(字段名, 默认值), ...]。"""
    if isinstance(value, dict):
        props = value.get("properties")
        if isinstance(props, dict):
            return [(name, default_for_spec(props[name])) for name in props]
        if "fields" in value:
            return fields_from(value["fields"])
        # 视作 name -> spec 的映射
        return [(name, default_for_spec(value[name])) for name in value]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                result.append((item, None))
            elif isinstance(item, dict):
                name = (item.get("name") or item.get("field_id")
                        or item.get("id") or item.get("key") or item.get("field"))
                if name:
                    result.append((name, default_for_spec(item)))
        return result
    if isinstance(value, str) and value.strip():
        return [(value, None)]
    return None


def find_output_contract(node):
    """递归查找 output_contract / output_schema；找不到返回 None。"""
    if isinstance(node, dict):
        for key, val in node.items():
            if key.lower() in ("output_contract", "outputcontract",
                               "output_schema", "outputschema"):
                return val
        for val in node.values():
            found = find_output_contract(val)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_output_contract(item)
            if found is not None:
                return found
    return None


def extract_case_id(case, case_path):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id", "challenge_id"):
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    base = os.path.basename(case_path or "")
    if base.lower().endswith(".json"):
        base = base[:-5]
    return base or "unknown"


def extract_question(case):
    if isinstance(case, dict):
        for key in ("research_question", "question", "query", "prompt",
                    "title", "objective"):
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        task = case.get("task")
        if isinstance(task, dict):
            for key in ("research_question", "question", "prompt",
                        "description", "title", "objective"):
                val = task.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
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
                obj, errs = read_json(path)
                if obj is not None and not errs and find_output_contract(obj) is not None:
                    return path
    return None


def discover_materials(input_dir):
    """扫描输入目录，跳过元文件，返回按路径排序的材料清单。"""
    found = []
    if not os.path.isdir(input_dir):
        return found
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        for name in sorted(files):
            if name.lower() in META_NAMES:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in MATERIAL_EXTS:
                continue
            path = os.path.join(root, name)
            found.append(path)
    return found


def extract_pdf(pdf_path, out_txt):
    """调用 pdftotext；失败返回 (False, 原因)，不伪造解析结果。"""
    for cmd in ("pdftotext", "pdftotext.exe"):
        try:
            proc = subprocess.run(
                [cmd, "-layout", pdf_path, out_txt],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            if proc.returncode == 0 and os.path.isfile(out_txt):
                return True, None
            return False, "returncode=%s" % proc.returncode
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
    return False, "pdftotext 未安装"


def write_plan(work_dir, case_id, budget_seconds, request_budget,
               checkpoint_seconds):
    plan = {
        "case_id": case_id,
        "budget": {
            "wall_seconds": budget_seconds,
            "model_requests": request_budget,
            "tokens_per_response": 16384,
            "checkpoint_seconds": checkpoint_seconds,
        },
        "phases": [],
        "checkpoints": [],
        "last_updated": now_ts(),
    }
    for pid, label, deadline, req, note in PHASE_PLAN:
        plan["phases"].append({
            "id": pid,
            "label": label,
            "deadline_seconds": deadline,
            "request_budget": req,
            "note": note,
            "status": "pending",
        })
    plan["checkpoints"].append({
        "ts": now_ts(),
        "phase": "phase0",
        "action": "plan_written",
        "detail": "时间预算计划落盘，先于任何实质分析",
    })
    write_json(os.path.join(work_dir, "plan.json"), plan)
    return plan


def touch_phase(work_dir, plan, phase_id, status, note, files_written=None):
    """记录阶段检查点：更新 plan.json 并追加 checkpoints。"""
    entry = {
        "ts": now_ts(),
        "phase": phase_id,
        "action": status,
        "detail": note,
    }
    if files_written:
        entry["files"] = list(files_written)
    plan.setdefault("checkpoints", []).append(entry)
    for ph in plan.get("phases", []):
        if ph["id"] == phase_id:
            ph["status"] = status
            ph["finished_at"] = now_ts()
    plan["last_updated"] = now_ts()
    write_json(os.path.join(work_dir, "plan.json"), plan)


def build_result(case, case_path, input_dir, work_dir, output_dir,
                 budget_seconds, request_budget, checkpoint_seconds):
    """返回 result.json 字典（骨架，status=partial）。"""
    case_id = extract_case_id(case, case_path)
    contract = find_output_contract(case)
    fields = fields_from(contract)
    if not fields:
        fields = list(DEFAULT_CONTRACT_FIELDS.items())
        contract_source = "fallback_default"
    else:
        contract_source = "output_contract"

    result = {"case_id": case_id}
    for name, default in fields:
        # 已有同名键时保留原值（不覆盖输入），否则填默认值。
        if name not in result:
            result[name] = default

    result.update({
        "status": "partial",
        "status_reason": "Phase 0 骨架已落盘，实质分析待完成",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "ts": now_ts(),
            "phase": "phase0",
            "action": "skeleton_written",
            "detail": "合法 partial 骨架落盘，早于 3 分钟检查点",
        }],
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": now_ts(),
            "budget_seconds": budget_seconds,
            "model_request_budget": request_budget,
            "checkpoint_seconds": checkpoint_seconds,
            "contract_source": contract_source,
            "input_dir": input_dir,
            "work_dir": work_dir,
            "output_dir": output_dir,
            "plan_path": os.path.join(work_dir, "plan.json"),
        },
    })
    return result, fields, contract_source


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="文献综述合成 Skill 确定性脚手架（早落盘 + 时间预算）")
    parser.add_argument("--input", default="/input", help="输入目录")
    parser.add_argument("--output", default="/app/output", help="最终产物目录")
    parser.add_argument("--work", default="/app/work", help="中间产物目录")
    parser.add_argument("--budget", type=int, default=900, help="总时间预算（秒）")
    parser.add_argument("--requests", type=int, default=64, help="模型请求预算")
    parser.add_argument("--checkpoint", type=int, default=180,
                        help="骨架落盘检查点（秒）")
    args = parser.parse_args(argv)

    ensure_dir(args.output)
    ensure_dir(args.work)
    errors, degradations = [], []

    case_path = find_case_file(args.input)
    case = None
    if case_path is None:
        errors.append("未找到 case.json 或含 output_contract 的 JSON")
    else:
        case, errs = read_json(case_path)
        if errs:
            errors.extend(errs)
            case = None

    result, fields, contract_source = build_result(
        case, case_path, args.input, args.work, args.output,
        args.budget, args.requests, args.checkpoint)

    # 若输入不可解析，如实标 input_error；否则保持 partial。
    if case_path is None or case is None:
        result["status"] = "input_error"
        result["status_reason"] = "输入不可解析或缺少 case.json"
    result["errors"] = errors
    result["degradations"] = degradations

    # 1) 立刻落盘骨架 result.json —— 任何后续异常都不留空产物。
    write_json(os.path.join(args.output, "result.json"), result)

    # 2) 写时间预算计划。
    plan = write_plan(args.work, result["case_id"], args.budget,
                      args.requests, args.checkpoint)

    # 3) 写契约字段清单，供 validate_result.py 校验。
    write_json(os.path.join(args.work, "contract.json"), {
        "case_id": result["case_id"],
        "contract_source": contract_source,
        "fields": [{"name": n, "default": d} for n, d in fields],
    })

    if case_path is None or case is None:
        log("输入不可解析，已落盘 input_error 骨架")
        return 0

    # 4) 盘点材料、去重、抽取文本。
    materials = discover_materials(args.input)
    inventory = {"case_id": result["case_id"], "materials": [], "dedup": []}
    seen_hashes = {}

    for path in materials:
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        ext = os.path.splitext(path)[1].lower()
        sha = sha256_file(path)
        kind = ("pdf" if ext in PDF_EXTS
                else "text" if ext in TEXT_EXTS else "record")
        rec = {
            "path": path,
            "rel_path": os.path.relpath(path, args.input),
            "kind": kind,
            "sha256": sha,
            "size_bytes": size,
            "over_limit": size > MAX_FILE_BYTES,
        }
        if size > MAX_FILE_BYTES:
            rec["note"] = "超过 2MB 上限，跳过自动抽取"
            degradations.append("超大文件跳过: " + rec["rel_path"])
            inventory["materials"].append(rec)
            continue
        if sha and sha in seen_hashes:
            inventory["dedup"].append({
                "rel_path": rec["rel_path"],
                "duplicate_of": seen_hashes[sha],
                "sha256": sha,
            })
            continue
        if sha:
            seen_hashes[sha] = rec["rel_path"]

        if kind == "pdf":
            out_txt = os.path.join(args.work, "extracted",
                                   os.path.basename(path) + ".txt")
            ok, reason = extract_pdf(path, out_txt)
            rec["extracted"] = out_txt if ok else None
            rec["extraction_error"] = reason if not ok else None
            if not ok:
                degradations.append("PDF 抽取失败 %s: %s" % (rec["rel_path"], reason))
        elif kind == "text":
            out_txt = os.path.join(args.work, "extracted",
                                   os.path.basename(path))
            text, _enc, err = read_text(path)
            if text is not None:
                write_text(out_txt, text)
                rec["extracted"] = out_txt
            else:
                rec["extraction_error"] = err
                degradations.append("文本读取失败 %s: %s" % (rec["rel_path"], err))
        else:
            rec["extracted"] = None  # 题录由 Agent 按需读取
        inventory["materials"].append(rec)

    write_json(os.path.join(args.work, "inventory.json"), inventory)

    # 5) 空证据表骨架（字段见 SKILL.md / references/evidence_spec.md）。
    if not os.path.exists(os.path.join(args.output, "evidence_table.jsonl")):
        write_text(os.path.join(args.output, "evidence_table.jsonl"), "")

    result["degradations"] = degradations
    result["runtime"]["material_count"] = len(inventory["materials"])
    result["runtime"]["dedup_count"] = len(inventory["dedup"])
    write_json(os.path.join(args.output, "result.json"), result)

    touch_phase(args.work, plan, "phase0", "completed",
                "骨架与盘点完成，等待 Agent 填充实质内容",
                files_written=["result.json", "inventory.json", "plan.json",
                               "contract.json"])

    log("骨架已落盘：case_id=%s status=%s materials=%d"
        % (result["case_id"], result["status"], len(inventory["materials"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
