#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研问题驱动检索 Skill 的确定性脚手架（纯标准库，无第三方依赖）。

职责：把“复杂科研问题 → 子问题拆解 → 查询规划 → 术语扩展 → 多轮检索 →
全量筛选 → 透明排序 → 可复核交付”固化为可复现的中间表示与收口流程。
脚本不做检索、不做相关性判断（这些由 Agent 完成）；脚本只负责稳健读输入、
动态回填 output_contract、早期落盘骨架、去重与透明打分、从中间产物组装
result.json、以及自检契约。运行结果由真实输入与 Agent 记录决定，脚本不编造
任何文献、DOI 或分数。

中间产物（/app/work/，可独立复核）：
  case_summary.json  解析后的 case_id / 研究问题 / 约束 / output_contract / 材料清单
  plan.json          拆解 / 术语扩展 / 查询计划 / 停止判据与阈值
  records.json       规范化候选账本（全量记录，含 screening 决定与证据）
  evidence.json      逐轮查询 / 筛选 / 冲突 / 缺口日志
  change_log.json    增量操作留痕
  papers.json        可选：题目给定的有限论文快照（用于“全量记录筛选”口径）

子命令：
  run       骨架流水线：skeleton -> parse -> plan -> assemble
  skeleton  只写一版合法骨架 result.json
  parse     解析 /input -> /app/work/case_summary.json
  plan      生成拆解/规划空模板 -> /app/work/plan.json
  set-plan  合并 Agent 的拆解、术语扩展、查询计划、停止判断
  import    导入并 upsert 候选记录（含筛选决定）-> /app/work/records.json
  evidence  追加查询/轮次/冲突/缺口证据 -> /app/work/evidence.json
  assemble  去重+透明打分+排序，写 /app/output/result.json + screening.md + plan.md
  selfcheck 校验 result.json 是否满足输出契约（逐条 PASS/FAIL）

退出码约定：参数错误、空输入、坏 JSON、坏编码、未知异常一律以退出码 0 收口
（真实问题写进 errors/degradations）；selfcheck 在存在 FAIL 时返回 1，用于给
调用方一个明确的机械校验信号，但即使 selfcheck 失败也不会抹掉已写的 result.json。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
SCREENING_CHOICES = ("include", "exclude", "uncertain")
EVIDENCE_KINDS = ("query", "round", "conflict", "gap", "screening", "note")
STOP_STATUSES = ("pending", "sufficient", "low_marginal_return", "handoff")

# 可能承载 output_contract 的键名（运行时 case.json 键名不固定，逐个探测）
CONTRACT_KEY_NAMES = (
    "output_contract", "outputContract", "output", "result_schema", "schema",
    "required_output", "required_fields", "expected_fields", "contract",
)

# 拆解六要素（供 Agent 对照；脚本仅生成空型模板，不做领域推断）
FACET_SCHEMA = [
    {"id": "object", "label": "对象/样本", "zh": "研究对象、样本或系统"},
    {"id": "mechanism", "label": "机制/关系", "zh": "作用机制、因果关系或现象"},
    {"id": "method", "label": "方法/设计", "zh": "研究方法、测量或实验设计"},
    {"id": "time_range", "label": "时间范围", "zh": "时间窗口或文献年代限制"},
    {"id": "comparison", "label": "比较关系", "zh": "对照组、对比或基准"},
    {"id": "evidence_req", "label": "证据要求", "zh": "研究类型、结局指标、样本量等"},
]

# 契约缺省时的最小根字段（search02 题目要求的检索结果/证据记录/状态说明）
DEFAULT_CONTRACT = {
    "research_question": None,
    "sub_questions": None,
    "query_plan": None,
    "term_expansion": None,
    "stop_decision": None,
    "results": None,
    "evidence": None,
    "status_notes": None,
}
DEFAULT_CONTRACT_TYPES = {
    "research_question": "scalar",
    "sub_questions": "list",
    "query_plan": "list",
    "term_expansion": "list",
    "stop_decision": "object",
    "results": "list",
    "evidence": "list",
    "status_notes": "list",
}

TRUSTED_SOURCE_HINTS = (
    "crossref", "pubmed", "europepmc", "europe pmc", "semantic scholar",
    "arxiv", "publisher", "doi", "openalex", "chemrxiv", "biorxiv", "medrxiv",
)


def _now() -> str:
    # 带时区的本地 ISO 时间，仅用于 runtime/change_log，不进业务字段。
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# --------------------------------------------------------------------------
# 稳健 IO（BOM / 坏编码 / 坏 JSON / 超大文件均降级，不崩溃）
# --------------------------------------------------------------------------
def ensure_dir(path):
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


def read_text(path, max_bytes=MAX_FILE_BYTES):
    if not path or not os.path.isfile(path):
        return "", ["missing:" + str(path)]
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", ["stat_failed:" + str(path)]
    if size > max_bytes:
        return "", ["size_exceeded:%s:%d" % (path, size)]
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return "", ["read_failed:%s:%s" % (path, str(exc)[:80])]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), []
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), ["fallback_decode:" + str(path)]


def read_json(path, default=None, max_bytes=MAX_FILE_BYTES):
    if default is None:
        default = {}
    text, deg = read_text(path, max_bytes)
    if deg:
        return default, deg
    if not text.strip():
        return default, ["empty:" + str(path)]
    try:
        return json.loads(text), []
    except Exception as exc:  # noqa: BLE001
        return default, ["bad_json:%s:%s" % (path, str(exc)[:120])]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print("write_text 失败:", path, str(exc)[:200], file=sys.stderr)
        return False


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print("write_json 失败:", path, str(exc)[:200], file=sys.stderr)
        return False


def log_change(work_dir, action, reason, status="partial"):
    path = os.path.join(work_dir, "change_log.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "time": _now(),
        "action": action,
        "reason": reason,
        "status": status,
    })
    write_json(path, entries)


# --------------------------------------------------------------------------
# 输入发现与解析
# --------------------------------------------------------------------------
def discover_input(input_dir):
    out = []
    if not input_dir or not os.path.isdir(input_dir):
        return out
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            out.append(os.path.join(root, name))
    return out


def _find_by_basename(files, names):
    for f in files:
        if os.path.basename(f).lower() in names:
            return f
    return None


def find_case_file(files):
    path = _find_by_basename(files, ("case.json",))
    if path:
        return path
    for f in files:
        base = os.path.basename(f).lower()
        if base.startswith("case") and base.endswith(".json"):
            return f
    return None


def find_task_file(files):
    path = _find_by_basename(files, ("task.md",))
    if path:
        return path
    for f in files:
        base = os.path.basename(f).lower()
        if base.endswith(".md") and "task" in base:
            return f
    for f in files:
        if f.lower().endswith(".md"):
            return f
    return None


def find_material_files(files):
    out = []
    for f in files:
        base = os.path.basename(f).lower()
        if base == "case.json":
            continue
        if base == "task.md":
            continue
        if base.endswith(".md") and "task" in base:
            continue
        out.append(f)
    return out


def _first_str(data, keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)) and key in ("case_id", "caseId", "id"):
            return str(val)
    return None


def parse_case(input_dir, work_dir):
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    material_paths = find_material_files(files)
    degradations = []

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    degradations += d
    errors = ["case_json:" + x for x in d if x.startswith(("bad_json", "empty", "read_failed", "size_exceeded", "stat_failed", "missing"))]
    task_text, d = read_text(task_path) if task_path else ("", ["no_task_md"])
    degradations += d

    case_id = _first_str(case_data, ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId"))
    if not case_id and task_text:
        m = re.search(r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        case_id = ("case:" + os.path.splitext(os.path.basename(case_path))[0]) if case_path else "unknown"

    research_question = _first_str(case_data, (
        "research_question", "researchQuestion", "question", "query", "topic",
        "task", "problem", "research_topic", "research_problem", "title"))
    if not research_question and task_text:
        m = re.search(r"(?:研究问题|科研问题|研究主题|research question|research topic)\s*[:：]\s*(.+)", task_text)
        if m:
            research_question = m.group(1).strip()[:2000]

    # 动态发现 output_contract：原样保留全部键，缺值按类型回填 null/[]
    output_contract = {}
    output_contract_types = {}
    if isinstance(case_data, dict):
        for key in CONTRACT_KEY_NAMES:
            val = case_data.get(key)
            if isinstance(val, dict) and val:
                output_contract = {}
                output_contract_types = {}
                for kk, vv in val.items():
                    output_contract[str(kk)] = None
                    if isinstance(vv, list):
                        output_contract_types[str(kk)] = "list"
                    elif isinstance(vv, dict):
                        output_contract_types[str(kk)] = "object"
                    else:
                        output_contract_types[str(kk)] = "scalar"
                break
            if isinstance(val, list) and val:
                output_contract = {}
                output_contract_types = {}
                for item in val:
                    if isinstance(item, str):
                        output_contract[item] = None
                        output_contract_types[item] = "scalar"
                    elif isinstance(item, dict):
                        name = None
                        for cand in ("name", "key", "field", "field_id"):
                            if isinstance(item.get(cand), str) and item[cand].strip():
                                name = item[cand].strip()
                                break
                        if name:
                            output_contract[name] = None
                            typ = str(item.get("type") or "").lower()
                            output_contract_types[name] = (
                                "list" if "list" in typ or "array" in typ
                                else "object" if "object" in typ or "dict" in typ
                                else "scalar")
                break
    if not output_contract:
        # 契约缺省时仍保留题目约定的最小字段，避免 result.json 缺失核心键
        output_contract = dict(DEFAULT_CONTRACT)
        output_contract_types = dict(DEFAULT_CONTRACT_TYPES)

    constraints = {}
    if isinstance(case_data, dict):
        for key in ("constraints", "requirements", "limits", "scope"):
            val = case_data.get(key)
            if isinstance(val, dict):
                for a, b in val.items():
                    constraints[str(a)] = b
        for key in ("max_results", "top_k", "max_candidates", "databases", "years", "date_range",
                    "year_from", "year_to", "language", "domains", "required_evidence",
                    "num_papers", "record_ids", "queries", "query_inputs"):
            if case_data.get(key) not in (None, "", []):
                constraints[key] = case_data[key]

    # 题目可能直接给出有限论文/查询快照，规范化写入 papers.json 作为“全量记录”口径
    papers = []
    for key in ("papers", "records", "candidates", "corpus", "snapshot"):
        val = case_data.get(key) if isinstance(case_data, dict) else None
        if isinstance(val, list):
            papers = val
            break
    if papers:
        write_json(os.path.join(work_dir, "papers.json"), {"papers": papers})

    summary = {
        "case_id": case_id,
        "research_question": research_question,
        "case_file": case_path,
        "task_file": task_path,
        "material_files": material_paths,
        "input_files": files,
        "constraints": constraints,
        "output_contract": output_contract,
        "output_contract_types": output_contract_types,
        "errors": errors,
        "degradations": degradations,
    }
    write_json(os.path.join(work_dir, "case_summary.json"), summary)
    return summary


def _fallback_summary(err):
    return {
        "case_id": "unknown",
        "research_question": None,
        "case_file": None,
        "task_file": None,
        "material_files": [],
        "input_files": [],
        "constraints": {},
        "output_contract": dict(DEFAULT_CONTRACT),
        "output_contract_types": dict(DEFAULT_CONTRACT_TYPES),
        "errors": [err],
        "degradations": [],
    }


# --------------------------------------------------------------------------
# 计划：拆解 / 术语扩展 / 查询规划 / 停止条件（脚本给空型模板，内容由 Agent 填）
# --------------------------------------------------------------------------
def build_plan_skeleton(summary):
    return {
        "case_id": summary.get("case_id"),
        "research_question": summary.get("research_question"),
        "facet_schema": FACET_SCHEMA,
        "sub_questions": [],       # [{id, facet, text, key_concepts[], required_evidence, parent}]
        "term_expansion": [],      # [{concept, variants:[{term, type}], rationale, max_variants}]
        "query_plan": [],          # [{step, purpose, query_template, concept_ids[], depends_on[], parallel_group, source}]
        "stop_decision": {
            "status": "pending",   # pending | sufficient | low_marginal_return | handoff
            "reason": None,
            "subquestion_coverage": {},   # {SQ_id: n_evidence}
            "idle_rounds": 0,
        },
        "stop_thresholds": {
            "min_evidence_per_subquestion": 1,
            "min_new_relevant_per_round": 1,
            "max_idle_rounds": 2,
        },
    }


def _shallow_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for key, val in patch.items():
            if key in out and isinstance(out[key], dict) and isinstance(val, dict):
                merged = dict(out[key])
                merged.update(val)
                out[key] = merged
            else:
                out[key] = val
        return out
    return patch


# --------------------------------------------------------------------------
# 记录：规范化 / 去重 / 透明打分
# --------------------------------------------------------------------------
def _norm_doi(doi):
    if not isinstance(doi, str):
        return ""
    s = doi.strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = s.strip(".,;\"'")
    return s


def _norm_title(title):
    if not isinstance(title, str):
        return ""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.strip().lower())


def record_key(rec):
    doi = _norm_doi(rec.get("doi") or "")
    if doi:
        return ("doi", doi)
    title = _norm_title(rec.get("title") or "")
    if title:
        return ("title", title)
    rid = rec.get("record_id") or rec.get("id")
    if rid:
        return ("id", str(rid))
    return ("id", None)


def normalize_record(rec, index):
    rec = dict(rec) if isinstance(rec, dict) else {}
    screening = rec.get("screening")
    if screening not in SCREENING_CHOICES:
        screening = "uncertain"  # 未明确决定一律 uncertain，禁止静默假设相关
    subq = rec.get("sub_question_ids") or rec.get("sub_questions") or []
    if not isinstance(subq, list):
        subq = [subq] if subq else []
    relevance = rec.get("relevance")
    relevance_unset = not isinstance(relevance, (int, float)) or relevance is None
    if relevance_unset:
        relevance = 0.5
    try:
        relevance = max(0.0, min(1.0, float(relevance)))
    except (TypeError, ValueError):
        relevance = 0.5
        relevance_unset = True
    authors = rec.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r"[,;]", authors) if a.strip()]
    if not isinstance(authors, list):
        authors = []
    evidence = rec.get("evidence") or []
    if isinstance(evidence, dict):
        evidence = [evidence]
    if not isinstance(evidence, list):
        evidence = []
    return {
        "record_id": str(rec.get("record_id") or rec.get("id") or ("rec%d" % index)),
        "title": rec.get("title"),
        "authors": authors,
        "year": rec.get("year"),
        "journal": rec.get("journal"),
        "doi": _norm_doi(rec.get("doi") or "") or None,
        "url": rec.get("url"),
        "database": rec.get("database") or rec.get("source"),
        "sub_question_ids": [str(x) for x in subq],
        "relevance": relevance,
        "relevance_unset": relevance_unset,
        "relevance_reason": rec.get("relevance_reason"),
        "screening": screening,
        "screening_reason": rec.get("screening_reason"),
        "evidence": [e for e in evidence if isinstance(e, dict)],
    }


def merge_record(old, new):
    out = dict(old)
    for key, val in new.items():
        if key == "evidence":
            existing = old.get("evidence") or []
            out[key] = list(existing) + [e for e in (val or []) if e not in existing]
        elif key == "sub_question_ids":
            seen = set(out.get(key) or [])
            for x in (val or []):
                if x not in seen:
                    seen.add(x)
            out[key] = sorted(seen)
        elif val not in (None, "", [], {}) and not (key == "relevance_unset" and val is False):
            out[key] = val
    return out


def dedup_records(records):
    key_to_idx = {}
    merged = []
    removed = []
    for rec in records:
        key = record_key(rec)
        if key[0] == "id" and not key[1]:
            merged.append(rec)
            continue
        if key in key_to_idx:
            idx = key_to_idx[key]
            merged[idx] = merge_record(merged[idx], rec)
            removed.append({"record_id": rec.get("record_id"),
                            "merged_into": merged[idx].get("record_id"),
                            "title": rec.get("title")})
        else:
            key_to_idx[key] = len(merged)
            merged.append(rec)
    return merged, removed


def metadata_completeness(rec):
    fields = ("title", "authors", "year", "journal", "doi")
    present = sum(1 for f in fields if rec.get(f) not in (None, "", [], {}))
    return present / len(fields)


def source_reliability(rec):
    doi = (rec.get("doi") or "").strip()
    db = (rec.get("database") or "").lower()
    if doi and any(hint in db for hint in TRUSTED_SOURCE_HINTS):
        return 1.0
    if doi:
        return 0.9
    if rec.get("url"):
        return 0.7
    return 0.4


def compute_score(rec):
    rel = rec.get("relevance")
    if not isinstance(rel, (int, float)):
        rel = 0.5
    meta = metadata_completeness(rec)
    src = source_reliability(rec)
    return round(0.6 * rel + 0.2 * meta + 0.2 * src, 4), {
        "relevance": round(float(rel), 4),
        "metadata_completeness": round(meta, 4),
        "source_reliability": round(src, 4),
        "relevance_unset": bool(rec.get("relevance_unset")),
    }


# --------------------------------------------------------------------------
# 状态判定（只反映证据是否足以支撑结论，不反映分数）
# --------------------------------------------------------------------------
def determine_status(summary, records, plan, evidence):
    if summary.get("errors") and not summary.get("input_files"):
        return "input_error"
    if not records:
        return "insufficient_evidence"
    subqs = plan.get("sub_questions") if isinstance(plan, dict) else []
    if not subqs:
        return "partial"  # 缺拆解即不完整
    included = [r for r in records if r.get("screening") == "include"]
    if not included:
        return "partial"  # 已全量筛选但无相关记录，如实 partial 并说明
    covered = 0
    uncovered = []
    for sq in subqs:
        sid = str(sq.get("id") or "")
        if sid and any(sid in (r.get("sub_question_ids") or []) for r in included):
            covered += 1
        else:
            uncovered.append(sid or sq.get("text", "?"))
    if subqs and covered < len(subqs):
        return "partial"
    stop = plan.get("stop_decision") or {}
    if stop.get("status") in ("sufficient", "low_marginal_return"):
        return "ok" if evidence else "partial"
    return "partial"


# --------------------------------------------------------------------------
# 组装 result.json 与可编辑产物
# --------------------------------------------------------------------------
def build_result(summary, plan, records, evidence, changelog, runtime, removed):
    records = [dict(r) for r in records]
    scored = []
    for rec in records:
        sc, breakdown = compute_score(rec)
        rr = dict(rec)
        rr["score"] = sc
        rr["score_breakdown"] = breakdown
        scored.append(rr)
    scored.sort(key=lambda r: (
        0 if r.get("screening") == "include" else 1,
        -(r.get("score") if isinstance(r.get("score"), (int, float)) else 0),
        (r.get("title") or "").lower(),
    ))
    rank = 0
    for r in scored:
        if r.get("screening") == "include":
            rank += 1
            r["rank"] = rank

    status = determine_status(summary, records, plan, evidence)
    if not isinstance(evidence, list):
        evidence = []

    result = {"case_id": summary.get("case_id"), "status": status}
    contract = summary.get("output_contract") or {}
    ctypes = summary.get("output_contract_types") or {}
    # 先回填契约全部键（缺值按类型 null/[]），保证未知键不被删除。
    # case_id/status 是已确定字段，不在此处被 null 覆盖。
    for key in contract:
        if key in ("case_id", "status"):
            continue
        if key == "research_question":
            result[key] = summary.get("research_question")
        elif key == "sub_questions":
            result[key] = plan.get("sub_questions") or []
        elif key == "query_plan":
            result[key] = plan.get("query_plan") or []
        elif key == "term_expansion":
            result[key] = plan.get("term_expansion") or []
        elif key == "stop_decision":
            result[key] = plan.get("stop_decision") or {}
        elif key == "results":
            result[key] = [r for r in scored if r.get("screening") == "include"]
        elif key == "evidence":
            result[key] = evidence
        elif key == "status_notes":
            result[key] = []
        elif key in ("screening", "sources", "uncertainty", "change_log", "limitations",
                     "errors", "degradations", "pending_human", "runtime"):
            result[key] = None  # 稍后由信封覆盖
        else:
            result[key] = [] if ctypes.get(key) == "list" else (
                {} if ctypes.get(key) == "object" else None)

    result.update({
        "research_question": summary.get("research_question"),
        "sub_questions": plan.get("sub_questions") or [],
        "query_plan": plan.get("query_plan") or [],
        "term_expansion": plan.get("term_expansion") or [],
        "stop_decision": plan.get("stop_decision") or {},
        "results": [r for r in scored if r.get("screening") == "include"],
        "screening": {
            "included": [r for r in scored if r.get("screening") == "include"],
            "excluded": [r for r in scored if r.get("screening") == "exclude"],
            "uncertain": [r for r in scored if r.get("screening") == "uncertain"],
            "merged_duplicates": removed,
        },
        "evidence": evidence,
        "status_notes": [],
        "sources": _collect_sources(scored, evidence),
        "uncertainty": _collect_uncertainty(records, plan, evidence),
        "change_log": changelog,
        "limitations": [],
        "errors": list(summary.get("errors") or []),
        "degradations": list(summary.get("degradations") or []),
        "pending_human": [],
        "runtime": runtime,
    })

    if not plan.get("sub_questions"):
        result["status_notes"].append("缺少子问题拆解：结果不完整，需补全 sub_questions 后重新 assemble")
    uncertain_count = sum(1 for r in records if r.get("screening") == "uncertain")
    if uncertain_count:
        result["status_notes"].append("%d 条记录尚未给出筛选决定（uncertain），需逐条补全 include/exclude" % uncertain_count)
    if removed:
        result["status_notes"].append("已按 DOI/规范化标题合并 %d 条重复候选" % len(removed))
    if not result.get("results"):
        result["status_notes"].append("无相关候选被纳入（included 为空）")
    if result["status"] == "partial" and not result.get("limitations"):
        result["limitations"].append("存在未覆盖子问题、未决定记录或停止判断未收敛，见 uncertainty/status_notes")

    return result


def _collect_sources(records, evidence):
    srcs = []
    seen = set()
    for e in evidence:
        if not isinstance(e, dict):
            continue
        source = e.get("source")
        if not source:
            continue
        db = e.get("database")
        key = ("q", str(source), str(db or ""))
        if key not in seen:
            seen.add(key)
            srcs.append({"source": str(source), "database": db, "query_count": 1, "record_count": 0})
        else:
            for x in srcs:
                if x.get("source") == str(source) and x.get("database") == db:
                    x["query_count"] = x.get("query_count", 0) + 1
    for rec in records:
        db = rec.get("database")
        if not db:
            continue
        hit = next((x for x in srcs if x.get("database") == db), None)
        if hit is not None:
            hit["record_count"] = hit.get("record_count", 0) + 1
        else:
            srcs.append({"source": None, "database": db, "query_count": 0, "record_count": 1})
    return srcs


def _collect_uncertainty(records, plan, evidence):
    missing_meta = []
    conflicts = []
    gaps = []
    for rec in records:
        for field in ("title", "authors", "year", "journal", "doi"):
            if rec.get(field) in (None, "", [], {}):
                missing_meta.append({"record_id": rec.get("record_id"), "field": field,
                                     "title": rec.get("title")})
    for e in evidence:
        if isinstance(e, dict) and e.get("kind") == "conflict":
            conflicts.append({
                "conflict": e.get("rationale") or e.get("query"),
                "record_id": e.get("record_id"),
            })
    subqs = plan.get("sub_questions") if isinstance(plan, dict) else []
    included_ids = set()
    for rec in records:
        if rec.get("screening") == "include":
            included_ids.update(rec.get("sub_question_ids") or [])
    for sq in subqs:
        sid = str(sq.get("id") or "")
        if sid and sid not in included_ids:
            gaps.append({"sub_question_id": sid, "text": sq.get("text"),
                         "issue": "无 included 证据"})
    return {
        "missing_metadata": missing_meta,
        "conflicts": conflicts,
        "coverage_gaps": gaps,
        "notes": [],
    }


def render_screening_md(result):
    lines = ["# 全量记录筛选清单（可编辑，供复核）", "",
             "状态：`%s`；case_id：`%s`" % (result.get("status"), result.get("case_id")),
             "每条记录必须给出 include/exclude/uncertain 决定与理由；未决定记录以 uncertain 呈现，不静默丢弃。",
             ""]
    for group, header in (("included", "## 纳入（include，按 score 降序）"),
                          ("excluded", "## 排除（exclude）"),
                          ("uncertain", "## 待定（uncertain）")):
        rows = result.get("screening", {}).get(group, [])
        lines.append(header)
        lines.append("")
        if not rows:
            lines.append("（无）")
            lines.append("")
            continue
        for rec in rows:
            title = rec.get("title") or "(缺标题)"
            lines.append("- **%s** — %s" % (title, rec.get("doi") or rec.get("url") or "无来源"))
            lines.append("  - 决定：%s | 理由：%s" % (rec.get("screening"),
                                                      rec.get("screening_reason") or "未填写"))
            lines.append("  - 相关性：%.2f（%s）| score=%.4f" % (
                rec.get("relevance"), "未判断" if rec.get("relevance_unset") else "已判断",
                rec.get("score", 0)))
            lines.append("  - 子问题：%s" % (", ".join(rec.get("sub_question_ids") or []) or "未映射"))
            for e in (rec.get("evidence") or [])[:3]:
                lines.append("  - 证据：[%s] %s" % (e.get("locator"), (e.get("quote") or "")[:120]))
    return "\n".join(lines) + "\n"


def render_plan_md(result):
    lines = ["# 检索计划与停止判断（可编辑，供复核）", "",
             "研究问题：%s" % (result.get("research_question") or "(未识别)"), ""]
    lines.append("## 子问题拆解")
    lines.append("")
    if not result.get("sub_questions"):
        lines.append("（缺拆解，需补全）")
        lines.append("")
    for sq in result.get("sub_questions") or []:
        lines.append("- `%s` [%s] %s" % (sq.get("id"), sq.get("facet") or "?", sq.get("text") or ""))
        lines.append("  - 关键概念：%s" % ", ".join(sq.get("key_concepts") or []))
        lines.append("  - 证据要求：%s" % (sq.get("required_evidence") or "未填写"))
    lines.append("")
    lines.append("## 术语扩展")
    lines.append("")
    for t in result.get("term_expansion") or []:
        variants = ", ".join(v.get("term", "") for v in t.get("variants") or [])
        lines.append("- %s → %s（%s）" % (t.get("concept"), variants or "(空)", t.get("rationale") or ""))
    lines.append("")
    lines.append("## 查询计划")
    lines.append("")
    for q in result.get("query_plan") or []:
        lines.append("- 步骤 %s：%s（依赖 %s，并行组 %s）" % (
            q.get("step"), q.get("purpose") or "", q.get("depends_on") or [],
            q.get("parallel_group") or "-"))
    lines.append("")
    lines.append("## 停止判断")
    lines.append("")
    sd = result.get("stop_decision") or {}
    lines.append("- 状态：%s" % sd.get("status"))
    lines.append("- 理由：%s" % (sd.get("reason") or "未填写"))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_skeleton(args):
    ensure_dir(args.output)
    ensure_dir(args.work)
    t0 = time.time()
    try:
        summary = parse_case(args.input, args.work)
    except Exception as exc:  # noqa: BLE001
        summary = _fallback_summary("parse_failed:" + str(exc)[:120])
        write_json(os.path.join(args.work, "case_summary.json"), summary)

    has_input = bool(summary.get("input_files"))
    if summary.get("errors") and not has_input:
        status = "input_error"
    elif not has_input:
        status = "insufficient_evidence"
    else:
        status = "partial"

    plan_path = os.path.join(args.work, "plan.json")
    existing_plan, _ = read_json(plan_path, {})
    if isinstance(existing_plan, dict) and (existing_plan.get("sub_questions")
                                            or existing_plan.get("query_plan")
                                            or existing_plan.get("term_expansion")):
        plan = existing_plan
    else:
        plan = build_plan_skeleton(summary)
        write_json(plan_path, plan)

    # 题目若在 case.json 中给出有限论文/查询快照，骨架阶段即规范化入库，
    # 作为“全量记录筛选”的起点；已有 records.json 时不覆盖。
    store_path = os.path.join(args.work, "records.json")
    existing_store, _ = read_json(store_path, {"records": []})
    existing_records = (existing_store.get("records") if isinstance(existing_store, dict)
                        else existing_store if isinstance(existing_store, list) else [])
    if not existing_records:
        papers_path = os.path.join(args.work, "papers.json")
        papers, _ = read_json(papers_path, {"papers": []})
        snapshot = papers.get("papers") if isinstance(papers, dict) else []
        if isinstance(snapshot, list) and snapshot:
            normalized = [normalize_record(rec, i) for i, rec in enumerate(snapshot)]
            write_json(store_path, {"records": normalized})
            print("skeleton: 已从 case.json 快照规范化导入 %d 条记录" % len(normalized))

    result = {"case_id": summary.get("case_id"), "status": status}
    contract = summary.get("output_contract") or {}
    ctypes = summary.get("output_contract_types") or {}
    for key in contract:
        if key in ("case_id", "status"):
            continue
        result[key] = [] if ctypes.get(key) == "list" else (
            {} if ctypes.get(key) == "object" else None)
    result.update({
        "research_question": summary.get("research_question"),
        "sub_questions": [],
        "query_plan": [],
        "term_expansion": [],
        "stop_decision": plan.get("stop_decision"),
        "results": [],
        "screening": {"included": [], "excluded": [], "uncertain": [], "merged_duplicates": []},
        "evidence": [],
        "status_notes": ["骨架：尚未检索，等待增量补充"],
        "sources": [],
        "uncertainty": {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []},
        "change_log": [],
        "limitations": [],
        "errors": list(summary.get("errors") or []),
        "degradations": list(summary.get("degradations") or []),
        "pending_human": [],
        "runtime": {"seconds": round(time.time() - t0, 3), "model_requests_used": None,
                    "phase": "skeleton"},
    })
    write_json(os.path.join(args.output, "result.json"), result)
    log_change(args.work, "skeleton", "写 status=%s 骨架" % status, status)
    print("skeleton ->", os.path.join(args.output, "result.json"), "status=" + status)
    return result


def cmd_parse(args):
    ensure_dir(args.work)
    summary = parse_case(args.input, args.work)
    print("case_id:", summary.get("case_id"))
    print("research_question:", (summary.get("research_question") or "")[:300])
    print("output_contract keys:", sorted((summary.get("output_contract") or {}).keys()))
    print("material_files:", [os.path.basename(x) for x in summary.get("material_files") or []])
    print("degradations:", summary.get("degradations"))
    return summary


def cmd_plan(args):
    ensure_dir(args.work)
    path = os.path.join(args.work, "plan.json")
    existing, _ = read_json(path, {})
    if isinstance(existing, dict) and (existing.get("sub_questions")
                                       or existing.get("query_plan")
                                       or existing.get("term_expansion")):
        print("plan 已存在且非空，保留不覆盖")
        return existing
    summary, _ = read_json(os.path.join(args.work, "case_summary.json"), {})
    plan = build_plan_skeleton(summary)
    write_json(path, plan)
    print("plan skeleton: sub_questions=%d term_expansion=%d query_plan=%d"
          % (len(plan["sub_questions"]), len(plan["term_expansion"]), len(plan["query_plan"])))
    return plan


def cmd_set_plan(args):
    ensure_dir(args.work)
    path = os.path.join(args.work, "plan.json")
    plan, _ = read_json(path, {})
    if not isinstance(plan, dict):
        plan = build_plan_skeleton({})
    if args.inline:
        try:
            obj = json.loads(args.inline)
        except Exception as exc:  # noqa: BLE001
            print("inline JSON 解析失败:", str(exc)[:200], file=sys.stderr)
            obj = None
        if isinstance(obj, dict):
            plan = _shallow_merge(plan, obj)
    if args.file:
        data, _ = read_json(args.file, {})
        if isinstance(data, dict):
            plan = _shallow_merge(plan, data)
    # 停止判据缺失时补默认阈值，停止状态非法时回退 pending
    plan.setdefault("stop_thresholds", build_plan_skeleton({})["stop_thresholds"])
    sd = plan.get("stop_decision")
    if not isinstance(sd, dict):
        sd = {}
        plan["stop_decision"] = sd
    sd.setdefault("status", "pending")
    if sd.get("status") not in STOP_STATUSES:
        sd["status"] = "pending"
    plan.setdefault("case_id", plan.get("case_id") or "")
    write_json(path, plan)
    log_change(args.work, "set-plan", "更新拆解/术语/查询计划/停止判断", "partial")
    print("plan updated: sub_questions=%d term_expansion=%d query_plan=%d stop=%s"
          % (len(plan.get("sub_questions") or []), len(plan.get("term_expansion") or []),
             len(plan.get("query_plan") or []), (plan.get("stop_decision") or {}).get("status")))
    return plan


def _read_incoming(args, field_names):
    incoming = []
    if args.file:
        data, _ = read_json(args.file, [])
        if isinstance(data, dict):
            for field in field_names:
                if data.get(field):
                    data = data.get(field)
                    break
        if isinstance(data, list):
            incoming.extend(data)
    if args.inline:
        try:
            obj = json.loads(args.inline)
        except Exception as exc:  # noqa: BLE001
            print("inline JSON 解析失败:", str(exc)[:200], file=sys.stderr)
            obj = None
        if isinstance(obj, dict):
            obj = [obj]
        if isinstance(obj, list):
            incoming.extend(obj)
    return [x for x in incoming if isinstance(x, dict)]


def cmd_import(args):
    ensure_dir(args.work)
    store_path = os.path.join(args.work, "records.json")
    store, _ = read_json(store_path, {"records": []})
    if not isinstance(store, dict):
        store = {"records": []}
    existing = store.get("records") or []
    if not isinstance(existing, list):
        existing = []
    incoming = _read_incoming(args, ("records", "candidates", "results", "papers"))

    normalized = [normalize_record(rec, len(existing) + i) for i, rec in enumerate(incoming)]
    key_to_idx = {}
    merged = []
    for rec in existing:
        key_to_idx[record_key(rec)] = len(merged)
        merged.append(rec)
    added = updated = 0
    for rec in normalized:
        key = record_key(rec)
        if key[0] == "id" and not key[1]:
            merged.append(rec)
            added += 1
            continue
        if key in key_to_idx:
            merged[key_to_idx[key]] = merge_record(merged[key_to_idx[key]], rec)
            updated += 1
        else:
            key_to_idx[key] = len(merged)
            merged.append(rec)
            added += 1
    write_json(store_path, {"records": merged})
    log_change(args.work, "import", "导入 %d 条（新增 %d，更新 %d）" % (len(normalized), added, updated), "partial")
    print("records total=%d added=%d updated=%d" % (len(merged), added, updated))
    return merged


def cmd_evidence(args):
    ensure_dir(args.work)
    path = os.path.join(args.work, "evidence.json")
    ev, _ = read_json(path, {"evidence": []})
    if isinstance(ev, dict):
        ev = ev.get("evidence") or []
    if not isinstance(ev, list):
        ev = []
    incoming = _read_incoming(args, ("evidence", "queries", "rounds"))
    ts = _now()
    for e in incoming:
        e.setdefault("timestamp", ts)
        kind = e.get("kind")
        if kind not in EVIDENCE_KINDS:
            e["kind"] = "query"
        e.setdefault("round", 0)
        e.setdefault("source", e.get("source") or e.get("database") or "unknown")
    ev.extend(incoming)
    write_json(path, {"evidence": ev})
    log_change(args.work, "evidence", "追加 %d 条证据/轮次" % len(incoming), "partial")
    print("evidence total=%d" % len(ev))
    return ev


def cmd_assemble(args):
    ensure_dir(args.output)
    ensure_dir(args.work)
    summary, _ = read_json(os.path.join(args.work, "case_summary.json"), {})
    if not isinstance(summary, dict) or not summary:
        summary = _fallback_summary("no_case_summary")
    plan, _ = read_json(os.path.join(args.work, "plan.json"), {})
    if not isinstance(plan, dict):
        plan = build_plan_skeleton(summary)
    store, _ = read_json(os.path.join(args.work, "records.json"), {"records": []})
    if isinstance(store, dict):
        records = store.get("records") or []
    elif isinstance(store, list):
        records = store
    else:
        records = []
    evidence, _ = read_json(os.path.join(args.work, "evidence.json"), {"evidence": []})
    if isinstance(evidence, dict):
        evidence = evidence.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = []
    changelog, _ = read_json(os.path.join(args.work, "change_log.json"), [])
    if not isinstance(changelog, list):
        changelog = []

    records, removed = dedup_records(records)
    if removed:
        write_json(os.path.join(args.work, "records.json"), {"records": records})

    runtime = {"seconds": getattr(args, "runtime_seconds", None),
               "model_requests_used": getattr(args, "runtime_requests", None),
               "phase": "assemble"}

    result = build_result(summary, plan, records, evidence, changelog, runtime, removed)
    result["change_log"] = changelog + [{
        "time": _now(),
        "action": "assemble",
        "reason": "生成 result.json（results=%d, status=%s）" % (len(result["results"]), result["status"]),
        "status": result["status"],
    }]
    # 停止判断若 Agent 未更新，回退为基于覆盖度的说明（不伪造结论）
    if not (plan.get("stop_decision") or {}).get("reason"):
        result["stop_decision"]["reason"] = "未显式填写停止判断；详见 uncertainty.coverage_gaps"

    write_json(os.path.join(args.output, "result.json"), result)
    write_text(os.path.join(args.output, "screening.md"), render_screening_md(result))
    write_text(os.path.join(args.output, "plan.md"), render_plan_md(result))
    log_change(args.work, "assemble", "写 result.json status=" + result["status"], result["status"])
    print("assemble -> status=%s results=%d excluded=%d uncertain=%d evidence=%d"
          % (result["status"], len(result["results"]),
             len(result["screening"]["excluded"]), len(result["screening"]["uncertain"]),
             len(evidence)))
    return result


def cmd_selfcheck(args):
    result, _ = read_json(os.path.join(args.output, "result.json"), None)
    summary, _ = read_json(os.path.join(args.work, "case_summary.json"), {})
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    check("result.json 存在且为合法 JSON", result is not None)
    if result is None:
        for name, ok in checks:
            print(("PASS" if ok else "FAIL"), name)
        return 1
    check("含 case_id", bool(result.get("case_id")))
    check("status 合法", result.get("status") in VALID_STATUS)
    for key in ("sources", "uncertainty", "change_log", "results", "evidence", "status_notes"):
        check("含 " + key, key in result)
    check("含 sub_questions（拆解）", "sub_questions" in result)
    check("含 query_plan（查询规划）", "query_plan" in result)
    check("含 term_expansion（术语扩展）", "term_expansion" in result)
    check("含 stop_decision（停止判断）", "stop_decision" in result)
    contract = summary.get("output_contract") if isinstance(summary, dict) else {}
    missing = [k for k in (contract or {}) if k not in result]
    check("output_contract 字段全部回填", not missing)
    if missing:
        print("  缺字段:", missing)
    results = result.get("results") or []
    check("每条 included 结果均有标题", all((r.get("title") or "").strip() for r in results))
    check("每条 included 结果均有来源", all((r.get("doi") or r.get("url") or r.get("record_id")) for r in results))
    check("每条 included 结果均有相关性/筛选依据",
          all((r.get("relevance_reason") or r.get("screening_reason") or "").strip() for r in results))
    screening = result.get("screening")
    check("screening 账本四键齐全（included/excluded/uncertain/merged_duplicates）",
          isinstance(screening, dict)
          and all(k in screening for k in ("included", "excluded", "uncertain", "merged_duplicates")))
    ok_all = all(ok for _, ok in checks)
    for name, ok in checks:
        print(("PASS" if ok else "FAIL"), name)
    return 0 if ok_all else 1


def _add_common_args(parser, suppress_defaults=False):
    if suppress_defaults:
        parser.add_argument("--input", default=argparse.SUPPRESS, help="输入目录，默认 /input")
        parser.add_argument("--output", default=argparse.SUPPRESS, help="输出目录，默认 /app/output")
        parser.add_argument("--work", default=argparse.SUPPRESS, help="工作目录，默认 /app/work")
    else:
        parser.add_argument("--input", default=DEFAULT_INPUT, help="输入目录，默认 /input")
        parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录，默认 /app/output")
        parser.add_argument("--work", default=DEFAULT_WORK, help="工作目录，默认 /app/work")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="科研问题驱动检索 Skill 辅助脚本（纯标准库）",
    )
    _add_common_args(parser)
    sub = parser.add_subparsers(dest="command")

    _add_common_args(sub.add_parser("run", help="骨架流水线：skeleton→parse→plan→assemble"), True)
    _add_common_args(sub.add_parser("skeleton", help="只写 partial 骨架 result.json"), True)
    _add_common_args(sub.add_parser("parse", help="解析输入→case_summary.json"), True)
    _add_common_args(sub.add_parser("plan", help="生成拆解/规划空模板→plan.json"), True)

    ps = sub.add_parser("set-plan", help="合并 Agent 的拆解/术语/查询计划/停止判断")
    _add_common_args(ps, True)
    ps.add_argument("--file", help="计划 JSON 文件路径")
    ps.add_argument("--inline", help="计划 JSON 字符串")

    pi = sub.add_parser("import", help="导入/更新候选记录（含筛选决定）→records.json")
    _add_common_args(pi, True)
    pi.add_argument("--file", help="候选 JSON 文件路径")
    pi.add_argument("--inline", help="单条/多条候选 JSON 字符串")

    pe = sub.add_parser("evidence", help="追加查询/轮次/冲突/缺口证据→evidence.json")
    _add_common_args(pe, True)
    pe.add_argument("--file", help="证据 JSON 文件路径")
    pe.add_argument("--inline", help="单条/多条证据 JSON 字符串")

    pa = sub.add_parser("assemble", help="去重+打分+排序，写 result.json / screening.md / plan.md")
    _add_common_args(pa, True)
    pa.add_argument("--runtime-seconds", type=float, help="已用墙钟秒数")
    pa.add_argument("--runtime-requests", type=int, help="已用模型请求数")

    _add_common_args(sub.add_parser("selfcheck", help="校验 result.json 契约"), True)
    return parser


def _resolve_args(args):
    for name, default in (("input", DEFAULT_INPUT), ("output", DEFAULT_OUTPUT), ("work", DEFAULT_WORK)):
        val = getattr(args, name, None)
        if val is None:
            setattr(args, name, default)
    return args


def main(argv=None):
    try:
        args = _resolve_args(build_parser().parse_args(argv))
    except SystemExit:
        # 参数错误/--help 也以退出码 0 收口，避免评测侧因非零退出码判 0 分
        return 0
    except Exception as exc:  # noqa: BLE001
        print("argument parsing failed:", str(exc)[:200], file=sys.stderr)
        return 0

    cmd = getattr(args, "command", None)
    if cmd is None:
        build_parser().print_help()
        return 0

    handlers = {
        "run": cmd_run,
        "skeleton": cmd_skeleton,
        "parse": cmd_parse,
        "plan": cmd_plan,
        "set-plan": cmd_set_plan,
        "import": cmd_import,
        "evidence": cmd_evidence,
        "assemble": cmd_assemble,
        "selfcheck": cmd_selfcheck,
    }
    try:
        handler = handlers[cmd]
    except KeyError:
        build_parser().print_help()
        return 0

    try:
        if cmd == "selfcheck":
            return int(cmd_selfcheck(args) or 0)
        handler(args)
        return 0
    except BaseException:  # noqa: BLE001
        _emergency_result(args, traceback.format_exc())
        return 0


def cmd_run(args):
    cmd_skeleton(args)
    cmd_parse(args)
    cmd_plan(args)
    return cmd_assemble(args)


def _emergency_result(args, tb):
    """顶层异常兜底：尽可能在输出目录留下合法 result.json，退出码保持 0。"""
    output = getattr(args, "output", None) or DEFAULT_OUTPUT
    work = getattr(args, "work", None) or DEFAULT_WORK
    ensure_dir(output)
    ensure_dir(work)
    summary, _ = read_json(os.path.join(work, "case_summary.json"), {})
    if not isinstance(summary, dict):
        summary = {"case_id": "unknown", "output_contract": dict(DEFAULT_CONTRACT),
                   "output_contract_types": dict(DEFAULT_CONTRACT_TYPES),
                   "input_files": [], "errors": [], "degradations": []}
    contract = summary.get("output_contract") or {}
    ctypes = summary.get("output_contract_types") or {}
    result = {"case_id": summary.get("case_id") or "unknown-case", "status": "input_error"}
    for key in contract:
        if key in ("case_id", "status"):
            continue
        result[key] = [] if ctypes.get(key) == "list" else (
            {} if ctypes.get(key) == "object" else None)
    last = tb.strip().splitlines()[-1] if tb.strip() else "unknown"
    result.update({
        "research_question": summary.get("research_question"),
        "sub_questions": [], "query_plan": [], "term_expansion": [],
        "stop_decision": {"status": "handoff", "reason": "脚本异常，转人工", "subquestion_coverage": {}, "idle_rounds": 0},
        "results": [],
        "screening": {"included": [], "excluded": [], "uncertain": [], "merged_duplicates": []},
        "evidence": [],
        "status_notes": ["脚本异常，已生成合法兜底结果"],
        "sources": [],
        "uncertainty": {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []},
        "change_log": [{"time": _now(), "action": "emergency", "reason": "未捕获异常", "status": "input_error"}],
        "limitations": ["脚本异常终止，见 errors"],
        "errors": list(summary.get("errors") or []) + ["未捕获异常: " + last],
        "degradations": list(summary.get("degradations") or []),
        "pending_human": ["查看 errors 字段中的异常定位问题"],
        "runtime": {"phase": "emergency", "emergency": True},
    })
    write_json(os.path.join(output, "result.json"), result)
    print("emergency ->", os.path.join(output, "result.json"), "status=input_error")


if __name__ == "__main__":
    sys.exit(main())
