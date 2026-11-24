#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 学位论文与长文档写作 Skill 的编排器（仅标准库）。

职责：
  - 发现并容错读取 /input 下的材料（case.json / task.md / materials / PDF 等）。
  - 提取 case_id 与 output_contract（如有），按“原样回填、缺值补 null/[]”原则组织结果。
  - --phase init：立即写一份合法的 partial 结果骨架到 /app/output/result.json。
  - --phase finalize：合并 Agent 写好的 result_patch.json 后收尾。
  - --phase check：校验 result.json 契约是否完整。

用法：
  python3 scripts/main.py --phase init [--input /input --output /app/output --work /app/work]
  python3 scripts/main.py --phase finalize [同上]
  python3 scripts/main.py --phase check [--output /app/output]
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB
CONTRACT_KEYS = ("deliverable", "fact_source_map", "version_boundary")
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_text(path):
    """按 utf-8-sig 读取，失败降级 latin-1，再失败返回 None。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def read_json_tolerant(path):
    txt = read_text(path)
    if txt is None:
        return None, "unreadable file: %s" % path
    try:
        return json.loads(txt), None
    except (ValueError, TypeError) as exc:
        # 尝试提取首个合法 JSON 片段
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            try:
                return json.loads(m.group(0)), None
            except (ValueError, TypeError):
                pass
        return None, "bad JSON in %s: %s" % (path, exc)


def discover_inputs(input_dir):
    """扫描输入目录，返回 {rel_path: {abs, size, kind}} 与异常列表。"""
    files = {}
    issues = []
    if not os.path.isdir(input_dir):
        return files, ["input dir missing: %s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in names:
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, input_dir)
            try:
                size = os.path.getsize(abs_path)
            except OSError as exc:
                issues.append("stat failed: %s (%s)" % (rel, exc))
                continue
            if size > MAX_FILE_BYTES:
                issues.append("skipped oversize file: %s (%d bytes)" % (rel, size))
                continue
            files[rel] = {"abs": abs_path, "size": size, "kind": classify(name)}
    return files, issues


def classify(name):
    low = name.lower()
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith((".md", ".markdown", ".txt")):
        return "text"
    if low.endswith(".json"):
        return "json"
    if low.endswith((".docx",)):
        return "docx"
    if low.endswith((".xlsx", ".csv")):
        return "table"
    if low.endswith((".pptx",)):
        return "slides"
    return "other"


def extract_case_id(case, input_dir):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, int):
                return str(val)
    return os.path.basename(os.path.abspath(input_dir)) or "unknown"


def extract_output_contract(case):
    """从 case.json 提取 output_contract（兼容常见键名），失败返回空 dict。"""
    if isinstance(case, dict):
        for key in ("output_contract", "outputContract", "output_schema", "result_schema", "contract"):
            val = case.get(key)
            if isinstance(val, dict) and val:
                return val
            if isinstance(val, list) and val:
                return {"fields": val}
    return {}


def default_skeleton(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "deliverable": {
            "path": "deliverables/draft.md",
            "format": "markdown",
            "title": None,
            "description": None,
            "completion": "skeleton",
            "preview_path": None,
        },
        "fact_source_map": [],
        "version_boundary": {
            "diff_summary": None,
            "pending_human": [],
            "material_gaps": [],
            "exceptions": [],
        },
        "sources": [],
        "uncertainty": {
            "summary": None,
            "unverified_claims": [],
            "conflicts": [],
        },
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started": now_iso(), "finished": None, "elapsed_s": None},
        "output_contract": {},
    }


def merge_contract(result, contract):
    """把 output_contract 的全部键原样回填到 result 顶层；未知键补 null。"""
    result["output_contract"] = contract
    if isinstance(contract, dict):
        for key, val in contract.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                # 已存在的规范结构只补缺，不覆盖已写好的内容
                for sub in val:
                    result[key].setdefault(sub, None)
            else:
                result.setdefault(key, val if val is not None else None)
    return result


def fill_contract_defaults(result):
    """确保三个规范契约字段存在，缺省为合法空结构。"""
    for key in CONTRACT_KEYS:
        if key not in result or result[key] is None:
            if key == "deliverable":
                result[key] = default_skeleton(result.get("case_id", "unknown"))["deliverable"]
            elif key == "fact_source_map":
                result[key] = []
            else:
                result[key] = {"diff_summary": None, "pending_human": [], "material_gaps": [], "exceptions": []}
    return result


def deep_merge(base, patch):
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def phase_init(args):
    files, issues = discover_inputs(args.input)
    case_rel = next((r for r in files if os.path.basename(r).lower() == "case.json"), None)
    task_rel = next((r for r in files if os.path.basename(r).lower() == "task.md"), None)

    case, case_err = (None, None)
    if case_rel:
        case, case_err = read_json_tolerant(files[case_rel]["abs"])

    case_id = extract_case_id(case, args.input)
    contract = extract_output_contract(case) if case is not None else {}

    result = default_skeleton(case_id)
    merge_contract(result, contract)

    errors = []
    degradations = list(issues)
    if case_err:
        errors.append(case_err)
        degradations.append("case.json 不可解析，使用目录名兜底 case_id 与空契约")
    if case_rel is None:
        degradations.append("未发现 case.json")
    if task_rel is None:
        degradations.append("未发现 task.md")

    # 材料清单写入 sources
    for rel, meta in sorted(files.items()):
        if rel in (case_rel, task_rel):
            continue
        result["sources"].append(
            {"id": "src-%03d" % (len(result["sources"]) + 1), "path": rel,
             "type": meta["kind"], "size_bytes": meta["size"], "note": "discovered in /input"}
        )

    result["errors"] = errors
    result["degradations"] = degradations
    result["version_boundary"]["material_gaps"].append(
        "骨架阶段：主产物尚未依据材料撰写，deliverable 为占位结构。"
    )

    ensure_dir(args.output)
    write_json_atomic(os.path.join(args.output, "result.json"), result)

    # 占位可编辑产物（明确标注，不冒充正式内容）
    ensure_dir(os.path.join(args.output, "deliverables"))
    draft_path = os.path.join(args.output, "deliverables", "draft.md")
    if not os.path.exists(draft_path):
        with open(draft_path, "w", encoding="utf-8") as fh:
            fh.write("# 草稿（骨架，待 Agent 依据材料填写）\n\n> 状态：partial。"
                     "本文件为 Phase 0 骨架占位，正式章节/改稿内容在 P3 阶段写入并替换。\n")
    print(json.dumps({"case_id": case_id, "status": result["status"],
                      "sources": len(result["sources"]), "degradations": len(degradations)},
                     ensure_ascii=False))
    return 0


def phase_finalize(args):
    result_path = os.path.join(args.output, "result.json")
    result, err = read_json_tolerant(result_path)
    if result is None:
        result = default_skeleton(extract_case_id(None, args.input))
        result["errors"].append("finalize 前 result.json 缺失或不可解析：" + str(err))
        result["degradations"].append("以最小骨架重建")

    patch_path = os.path.join(args.work, "result_patch.json")
    if os.path.exists(patch_path):
        patch, perr = read_json_tolerant(patch_path)
        if isinstance(patch, dict):
            deep_merge(result, patch)
        else:
            result["degradations"].append("result_patch.json 不可用：" + str(perr))

    fill_contract_defaults(result)
    result.setdefault("case_id", extract_case_id(None, args.input))
    if result.get("status") not in STATUSES:
        result["status"] = "partial"

    # 依据产物与证据粗判状态（仅当 Agent 未显式设定更严格的 status 时）
    draft = os.path.join(args.output, result.get("deliverable", {}).get("path", "deliverables/draft.md"))
    if result.get("status") == "partial":
        if os.path.exists(draft) and os.path.getsize(draft) > 0 and result.get("fact_source_map"):
            result["status"] = "ok" if not result["version_boundary"]["pending_human"] else "partial"

    start = result["runtime"].get("started")
    if start:
        try:
            st = datetime.fromisoformat(start)
            result["runtime"]["elapsed_s"] = round((datetime.now(timezone.utc) - st).total_seconds(), 1)
        except (ValueError, TypeError):
            result["runtime"]["elapsed_s"] = None
    result["runtime"]["finished"] = now_iso()

    ensure_dir(args.output)
    write_json_atomic(result_path, result)
    print(json.dumps({"case_id": result["case_id"], "status": result["status"],
                      "facts": len(result.get("fact_source_map", [])),
                      "changes": len(result.get("change_log", []))}, ensure_ascii=False))
    return 0


def phase_check(args):
    result_path = os.path.join(args.output, "result.json")
    result, err = read_json_tolerant(result_path)
    if result is None:
        print("FAIL: result.json 缺失或不可解析: %s" % err)
        return 1
    problems = []
    for key in ["case_id", "status", "deliverable", "fact_source_map", "version_boundary",
                "sources", "uncertainty", "change_log"]:
        if key not in result:
            problems.append("缺少键 %s" % key)
    if result.get("status") not in STATUSES:
        problems.append("status 非法: %r" % result.get("status"))
    if not isinstance(result.get("fact_source_map"), list):
        problems.append("fact_source_map 应为 list")
    if not isinstance(result.get("change_log"), list):
        problems.append("change_log 应为 list")
    if problems:
        print("FAIL:\n  " + "\n  ".join(problems))
        return 1
    print("OK status=%s case_id=%s facts=%d changes=%d" % (
        result["status"], result["case_id"], len(result["fact_source_map"]), len(result["change_log"])))
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="长文档写作 Skill 编排器")
    p.add_argument("--phase", choices=["init", "finalize", "check"], default="init")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    ensure_dir(args.work)
    if args.phase == "init":
        return phase_init(args)
    if args.phase == "finalize":
        return phase_finalize(args)
    return phase_check(args)


if __name__ == "__main__":
    sys.exit(main())
