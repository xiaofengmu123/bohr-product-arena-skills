#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 文献筛选与智能排序 Skill 的确定性实现。

仅依赖 Python 3 标准库。职责：

- discover：发现并解析 /input 下的 case.json / task.md / materials，写出合法的
  result.json 骨架（partial）与 case_normalized.json；
- screen：  确定性词面信号、去重聚类、基线排序，写 scaffold.json（词面先验）；
- finalize：result.json 契约校验与自检，写 selfcheck.json（errors=0 方可交付）；
- selftest：用合成夹具跑通 discover→screen→finalize，并验证空输入/坏 JSON 降级。

设计约束：所有脚本均不猜测 relevance/tier/priority_score；这些语义字段只能由
Agent 依据材料填写，脚本只提供确定性的发现、脚手架与契约校验。
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import traceback
import unicodedata

# ---------------------------------------------------------------------------
# 常量与别名
# ---------------------------------------------------------------------------
STATUS_VALUES = {"ok", "partial", "insufficient_evidence", "input_error"}
RELEVANCE_VALUES = {"direct", "indirect", "irrelevant", "unknown"}
TIER_VALUES = {"retain", "pending", "exclude"}
CONFIDENCE_VALUES = {"high", "medium", "low"}
TIER_ORDER = {"retain": 0, "pending": 1, "exclude": 2}
MAX_MATERIAL_BYTES = 2 * 1024 * 1024

CANDIDATE_KEYS = [
    "candidates", "papers", "records", "documents", "results", "items",
    "entries", "literature", "references", "publications", "candidate_papers",
]
QUESTION_KEYS = [
    "research_question", "question", "query", "task", "research_problem",
    "topic", "objective", "goal", "research_goal", "problem_statement",
]
INCLUSION_KEYS = [
    "inclusion_criteria", "include_criteria", "inclusion", "criteria_in",
    "include", "inclusion_rules",
]
EXCLUSION_KEYS = [
    "exclusion_criteria", "exclude_criteria", "exclusion", "criteria_ex",
    "exclude", "exclusion_rules",
]
CONTRACT_KEYS = [
    "output_contract", "output_schema", "expected_fields", "result_schema",
    "output_fields", "schema",
]
CASE_ID_KEYS = ["case_id", "id", "challenge_id", "case", "task_id"]

FIELD_ALIASES = {
    "record_id": ["record_id", "paper_id", "uid", "id", "index", "key", "pid"],
    "title": ["title", "name", "paper_title", "article_title", "headline"],
    "authors": ["authors", "author", "author_list", "creators", "by"],
    "year": ["year", "date", "year_published", "publication_year", "pub_year"],
    "venue": ["venue", "journal", "conference", "source", "publication",
              "container", "publisher", "booktitle"],
    "doi": ["doi", "DOI"],
    "url": ["url", "link", "pdf_url", "href", "eprint_url"],
    "abstract": ["abstract", "summary", "description", "content", "text",
                 "abstract_text", "snippet"],
    "source_type": ["source_type", "type", "kind", "publication_type",
                    "document_type", "entry_type"],
}

# 评分维度 → 产生证据的流程步骤 / 承载证据的字段 / 自检项 ID（见 references/checklist.md）
DIMENSION_AUDIT = {
    "actual_effect": {
        "cn": "实际效果", "score": 40,
        "workflow_steps": ["discover", "screen", "judge", "dedupe", "finalize"],
        "result_fields": ["results", "relevance", "tier", "evidence", "dedupe_groups"],
        "checklist_ids": ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
    },
    "use_gain": {
        "cn": "使用增益", "score": 25,
        "workflow_steps": ["judge", "finalize"],
        "result_fields": ["results", "change_log", "uncertainty", "sort_key"],
        "checklist_ids": ["B1", "B2", "B3"],
    },
    "innovation_gain": {
        "cn": "创新增益", "score": 15,
        "workflow_steps": ["screen", "judge", "dedupe"],
        "result_fields": ["tier", "conflict", "dedupe", "sort_key", "criteria_matched"],
        "checklist_ids": ["C1", "C2", "C3"],
    },
    "reliability": {
        "cn": "可靠体验", "score": 10,
        "workflow_steps": ["discover", "finalize", "selftest"],
        "result_fields": ["status", "errors", "degradations", "runtime"],
        "checklist_ids": ["D1", "D2", "D3", "D4"],
    },
    "ecosystem": {
        "cn": "生态贡献", "score": 10,
        "workflow_steps": ["selftest"],
        "result_fields": ["sources", "runtime", "change_log", "dimension_audit"],
        "checklist_ids": ["E1", "E2", "E3"],
    },
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:  # 写盘失败：记录但不崩溃（调用方决定降级）
        return False, str(exc)
    return True, None


def read_text(path, max_bytes=MAX_MATERIAL_BYTES):
    """按 utf-8-sig 优先读取，逐级容错；超限返回 (None, 'too_large')。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_error: %s" % exc
    if size > max_bytes:
        return None, "too_large"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except (UnicodeDecodeError, OSError):
            continue
    return None, "undecodable"


def load_json_loose(path):
    """读取 JSON；坏 JSON 返回 (None, 错误消息) 不抛异常。"""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "json_error: %s" % exc


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def norm_title(s):
    """规范化标题用于去重/平局键：NFKC、小写、去标点、去版本后缀。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s)
    s = re.sub(
        r"\b(v\d+[a-z]?|version\s*\d+|preprint|published|final version|"
        r"arxiv|biorxiv|medrxiv|ssrn|chemrxiv)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _first_nonempty(mapping, aliases):
    for key in aliases:
        val = mapping.get(key)
        if val not in (None, "", [], {}):
            return val
    return None


def _as_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, (dict, str)):
        return [val]
    try:
        return list(val)
    except TypeError:
        return [val]


def _coerce_year(val):
    """从 '2020'、2020、'2020-03-01' 提取四位年份；失败 None。"""
    if val is None:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return int(val) if 0 < int(val) < 10000 else None
    m = re.search(r"(1[89]\d{2}|20\d{2})", str(val))
    return int(m.group(1)) if m else None


def _coerce_score(val):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return None
    if 0.0 <= v <= 1.0:
        return round(v, 4)
    return None


# ---------------------------------------------------------------------------
# 输入发现与解析
# ---------------------------------------------------------------------------
def _scan_input(input_dir):
    """发现 case.json / task.md 与材料文件，返回结构化来源清单。"""
    sources = []
    case_path = None
    task_path = None
    materials = []
    if not os.path.isdir(input_dir):
        return sources, case_path, task_path, materials
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            low = fn.lower()
            if low == "case.json" and case_path is None:
                case_path = full
                sources.append({"path": rel, "kind": "case", "role": "primary"})
            elif low == "task.md" and task_path is None:
                task_path = full
                sources.append({"path": rel, "kind": "task", "role": "primary"})
            elif low.endswith((".pdf", ".md", ".jsonl", ".txt")):
                materials.append(full)
                sources.append({"path": rel, "kind": "material",
                                "role": "support"})
    return sources, case_path, task_path, materials


def _extract_candidates(obj):
    """在 case.json 顶层与常见容器键里找候选文献列表。"""
    for key in CANDIDATE_KEYS:
        val = obj.get(key)
        if isinstance(val, list):
            return key, val
    for key, val in obj.items():
        if isinstance(val, dict) and "papers" in val and isinstance(
                val["papers"], list):
            return key + ".papers", val["papers"]
    return None, []


def _extract_contract(obj):
    """发现并回填 output_contract 相关键；返回原始对象（缺省 {}）。"""
    for key in CONTRACT_KEYS:
        if key in obj and obj[key] not in (None, "", []):
            return key, obj[key]
    return None, {}


def _normalize_record(raw, index):
    """把单条候选规整为统一结构；元数据缺失填 null，不猜测。"""
    rec = {}
    if isinstance(raw, dict):
        mapping = raw
        rec["_raw"] = raw
    elif isinstance(raw, str):
        mapping = {"title": raw}
        rec["_raw"] = {"title": raw}
    else:
        mapping = {}
        rec["_raw"] = raw

    rid = _first_nonempty(mapping, FIELD_ALIASES["record_id"])
    rec["record_id"] = str(rid) if rid is not None else "rec_%04d" % index
    rec["title"] = _first_nonempty(mapping, FIELD_ALIASES["title"])
    authors = _first_nonempty(mapping, FIELD_ALIASES["authors"])
    if isinstance(authors, list):
        rec["authors"] = [str(a) for a in authors]
    elif authors is not None:
        rec["authors"] = [str(authors)]
    else:
        rec["authors"] = []
    rec["year"] = _coerce_year(_first_nonempty(mapping, FIELD_ALIASES["year"]))
    rec["venue"] = _first_nonempty(mapping, FIELD_ALIASES["venue"])
    rec["doi"] = _first_nonempty(mapping, FIELD_ALIASES["doi"])
    rec["url"] = _first_nonempty(mapping, FIELD_ALIASES["url"])
    rec["abstract"] = _first_nonempty(mapping, FIELD_ALIASES["abstract"])
    st = _first_nonempty(mapping, FIELD_ALIASES["source_type"])
    rec["source_type"] = str(st).lower() if st is not None else None
    rec["_source_index"] = index
    return rec


def _build_skeleton(case_id, question, inclusion, exclusion, contract,
                    normalized_candidates, sources, task_text):
    """生成合法的 partial 骨架（此时语义字段为占位，等待 Agent 填写）。"""
    results = []
    for rec in normalized_candidates:
        results.append({
            "record_id": rec["record_id"],
            "title": rec["title"],
            "authors": rec["authors"],
            "year": rec["year"],
            "venue": rec["venue"],
            "doi": rec["doi"],
            "url": rec["url"],
            "source_type": rec["source_type"],
            "relevance": "unknown",
            "tier": "pending",
            "priority_score": None,
            "rank": None,
            "reasons": [],
            "evidence": [],
            "confidence": "low",
            "criteria_matched": [],
            "conflict": None,
            "dedupe": None,
            "metadata_complete": bool(rec["title"] and rec["abstract"]),
            "sort_key": None,
        })
    ts = now_iso()
    skeleton = {
        "case_id": case_id,
        "status": "partial" if results else "insufficient_evidence",
        "research_question": question,
        "inclusion_criteria": _as_list(inclusion),
        "exclusion_criteria": _as_list(exclusion),
        "output_contract": contract,
        "results": results,
        "evidence": [],
        "status_notes": [],
        "omissions": [],
        "dedupe_groups": [],
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [
            {"ts": ts, "step": "discover", "action": "skeleton written"}
        ],
        "runtime": {"started_at": ts, "ended_at": None, "request_budget": 64,
                    "time_budget_s": 900},
        "dimension_audit": {
            "mapping": DIMENSION_AUDIT,
            "note": "五个评分维度到流程步骤/结果字段/自检项 ID 的对齐",
        },
    }
    if not results:
        skeleton["omissions"].append({
            "kind": "empty_candidates",
            "detail": "未发现候选文献；不生成示例文献，等待人工补全输入。",
        })
    if not sources:
        skeleton["degradations"].append({
            "kind": "no_input_files",
            "detail": "输入目录无 case.json / task.md / 材料文件。",
        })
    return skeleton


def run_discover(input_dir, work_dir, output_dir):
    report = {"errors": [], "degradations": [], "written": []}
    _ensure_dir(work_dir)
    _ensure_dir(output_dir)

    sources, case_path, task_path, _materials = _scan_input(input_dir)
    case_obj = None
    case_err = None
    task_text = None
    if case_path:
        case_obj, case_err = load_json_loose(case_path)
    if task_path:
        task_text, _ = read_text(task_path)

    question = None
    inclusion, exclusion = [], []
    contract = {}
    contract_key = None
    candidates = []
    case_id = None

    if case_obj is not None and isinstance(case_obj, dict):
        case_id = _first_nonempty(case_obj, CASE_ID_KEYS)
        question = _first_nonempty(case_obj, QUESTION_KEYS)
        inclusion = _as_list(_first_nonempty(case_obj, INCLUSION_KEYS))
        exclusion = _as_list(_first_nonempty(case_obj, EXCLUSION_KEYS))
        contract_key, contract = _extract_contract(case_obj)
        if contract_key is None:
            contract = {}
        _ckey, candidates = _extract_candidates(case_obj)
    else:
        if case_path:
            report["errors"].append({
                "kind": "bad_case_json",
                "detail": case_err or "case.json 无法解析或非对象",
                "fallback": "使用 task.md 文本继续",
            })
        if task_text:
            m = re.search(r"challenge[-_]id[:：]\s*`?([0-9a-zA-Z-]+)", task_text)
            if m:
                case_id = m.group(1)
            question = task_text[:2000]
        else:
            report["degradations"].append({
                "kind": "missing_input",
                "detail": "既无 case.json 也无 task.md，返回 insufficient_evidence",
            })

    normalized = [_normalize_record(c, i) for i, c in enumerate(candidates)]
    if case_id is None:
        case_id = "unknown"

    # 材料读取失败/超限记录：文本材料在此预读；PDF 留到步骤 4 由 pdftotext 解析
    for src in sources:
        if src["kind"] != "material":
            continue
        low = src["path"].lower()
        if low.endswith(".pdf"):
            # PDF 是合法材料，不在此标记为失败；步骤 4 负责 pdftotext 提取
            continue
        if low.endswith((".md", ".jsonl", ".txt")):
            full = os.path.join(input_dir, src["path"].replace("/", os.sep))
            _txt, err = read_text(full)
            if err:
                report["degradations"].append({
                    "kind": "material_skipped",
                    "path": src["path"],
                    "detail": err,
                })

    skeleton = _build_skeleton(case_id, question, inclusion, exclusion,
                                contract, normalized, sources, task_text)
    if case_path and case_obj is None:
        skeleton["status"] = "input_error" if not task_text else "partial"
    if report["errors"]:
        skeleton["errors"].extend(report["errors"])
    if report["degradations"]:
        skeleton["degradations"].extend(report["degradations"])

    # 写出骨架（先落盘，任何异常都不能留下空产物）
    write_json(os.path.join(output_dir, "result.json"), skeleton)
    report["written"].append(os.path.join(output_dir, "result.json"))

    norm = {
        "case_id": case_id,
        "question": question,
        "inclusion": inclusion,
        "exclusion": exclusion,
        "contract": contract,
        "candidates": normalized,
        "task_text": (task_text or "")[:20000],
    }
    write_json(os.path.join(work_dir, "case_normalized.json"), norm)
    report["written"].append(os.path.join(work_dir, "case_normalized.json"))
    return skeleton, report


# ---------------------------------------------------------------------------
# 词面信号、去重聚类、基线排序（screen）
# ---------------------------------------------------------------------------
def _tokenize(text):
    if not text:
        return set()
    s = unicodedata.normalize("NFKC", str(text)).lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s)
    tokens = [t for t in s.split() if len(t) > 1]
    return set(tokens)


def _keyword_terms(question, inclusion, exclusion):
    """从科研问题与纳排标准抽取确定性词面信号（仅作先验）。"""
    terms = set()
    for text in [question] + list(inclusion) + list(exclusion):
        if not text:
            continue
        for tok in _tokenize(text):
            if len(tok) > 2:
                terms.add(tok)
    return terms


def _lexical_score(rec, terms):
    """确定性词面先验：标题命中 ×2 + 摘要命中 ×1，归一化到 [0,1]。"""
    if not terms:
        return 0.0
    t = _tokenize(rec.get("title"))
    a = _tokenize(rec.get("abstract"))
    hits_t = len(t & terms)
    hits_a = len(a & terms)
    denom = max(1, 2 * len(terms))
    return round(min(1.0, (2 * hits_t + hits_a) / denom), 4)


def _evidence_completeness(rec):
    reasons = rec.get("reasons") or []
    evidence = rec.get("evidence") or []
    if reasons and evidence:
        return 1.0
    if reasons:
        return 0.5
    return 0.0


def _sort_key_for(rec):
    """确定性平局键；语义字段缺省时用最保守值。"""
    tier = rec.get("tier") if rec.get("tier") in TIER_VALUES else "pending"
    score = rec.get("priority_score")
    if score is None:
        score = -1.0
    year = rec.get("year")
    year = year if isinstance(year, int) else -1
    nt = norm_title(rec.get("title") or "")
    rid = str(rec.get("record_id") or "")
    return (TIER_ORDER[tier], -float(score), -_evidence_completeness(rec),
            -int(year), nt, rid)


def _dedup_cluster(records):
    """按规范化标题聚类；组内选代表，其余标 duplicate。"""
    groups = {}
    for rec in records:
        key = norm_title(rec.get("title") or "")
        if not key:
            key = "doi:" + str(rec.get("doi") or rec.get("record_id"))
        groups.setdefault(key, []).append(rec)

    dedupe_groups = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # 代表选择：published 优先于 preprint，年份新者优先，否则输入序靠前
        def _rank(m):
            st = (m.get("source_type") or "").lower()
            published = 0 if ("preprint" not in st and "arxiv" not in st
                              and "biorxiv" not in st) else 1
            return (published, -int(m.get("year") or 0),
                    m.get("_source_index", 0))
        ordered = sorted(members, key=_rank)
        rep = ordered[0]
        dedupe_groups.append({
            "key": key,
            "representative_record_id": rep["record_id"],
            "decision": "keep_representative",
            "members": [
                {"record_id": m["record_id"], "title": m.get("title"),
                 "role": "representative" if m is rep else "duplicate",
                 "note": ("published preferred" if m is rep and len(ordered) > 1
                          else "version/duplicate merged")}
                for m in ordered
            ],
        })
    return dedupe_groups


def run_screen(work_dir, output_dir):
    norm_path = os.path.join(work_dir, "case_normalized.json")
    norm, err = load_json_loose(norm_path)
    if err or not isinstance(norm, dict):
        scaffold = {"error": "case_normalized.json missing or invalid: %s" % err,
                    "base_score_note": "no candidates"}
        write_json(os.path.join(work_dir, "scaffold.json"), scaffold)
        return scaffold, {"errors": [{"kind": "normalized_missing",
                                      "detail": err}]}

    records = norm.get("candidates", [])
    terms = _keyword_terms(norm.get("question"), norm.get("inclusion"),
                           norm.get("exclusion"))
    enriched = []
    for rec in records:
        base = _lexical_score(rec, terms)
        r = dict(rec)
        r["base_score"] = base
        r["_terms_count"] = len(terms)
        enriched.append(r)
    enriched.sort(key=lambda r: (-r["base_score"], -int(r.get("year") or 0),
                                 norm_title(r.get("title") or ""),
                                 str(r.get("record_id") or "")))
    scaffold = {
        "base_score_note": "词面先验，仅供 Agent 参考；严禁直接作为最终排序分数",
        "terms_count": len(terms),
        "terms": sorted(terms)[:200],
        "baseline": [
            {"record_id": r.get("record_id"), "title": r.get("title"),
             "base_score": r["base_score"], "year": r.get("year")}
            for r in enriched
        ],
        "dedupe_groups": _dedup_cluster(enriched),
    }
    write_json(os.path.join(work_dir, "scaffold.json"), scaffold)
    return scaffold, {"errors": [], "degradations": []}


# ---------------------------------------------------------------------------
# 契约校验与自检（finalize）
# ---------------------------------------------------------------------------
_REQUIRED_TOP = [
    "case_id", "status", "results", "sources", "uncertainty", "change_log",
]
_REQUIRED_REC = [
    "record_id", "title", "authors", "year", "venue", "doi", "url",
    "source_type", "relevance", "tier", "priority_score", "rank", "reasons",
    "evidence", "confidence", "criteria_matched", "conflict", "dedupe",
]


def _validate_result(result, candidate_count):
    errors, warnings = [], []
    for f in _REQUIRED_TOP:
        if f not in result:
            errors.append("top_level_missing: %s" % f)
    if result.get("status") not in STATUS_VALUES:
        errors.append("bad_status: %r" % result.get("status"))
    results = result.get("results")
    if not isinstance(results, list):
        errors.append("results_not_list")
        results = []
    if candidate_count is not None and len(results) != candidate_count:
        errors.append("coverage_mismatch: results=%d candidates=%d"
                      % (len(results), candidate_count))
    seen = set()
    for i, rec in enumerate(results):
        tag = rec.get("record_id", "results[%d]" % i)
        for f in _REQUIRED_REC:
            if f not in rec:
                errors.append("record_missing: %s.%s" % (tag, f))
        if rec.get("relevance") not in RELEVANCE_VALUES:
            errors.append("bad_relevance: %s=%r" % (tag, rec.get("relevance")))
        if rec.get("tier") not in TIER_VALUES:
            errors.append("bad_tier: %s=%r" % (tag, rec.get("tier")))
        if rec.get("confidence") not in CONFIDENCE_VALUES:
            errors.append("bad_confidence: %s=%r" % (tag,
                                                     rec.get("confidence")))
        if rec.get("priority_score") is None:
            warnings.append("unscored: %s" % tag)
        else:
            s = _coerce_score(rec.get("priority_score"))
            if s is None:
                errors.append("bad_score: %s=%r" % (tag,
                                                    rec.get("priority_score")))
        ev = rec.get("evidence") or []
        if not isinstance(ev, list):
            errors.append("bad_evidence: %s" % tag)
        elif any(not (isinstance(e, dict) and e.get("locator")
                      and e.get("quote")) for e in ev):
            warnings.append("incomplete_evidence: %s" % tag)
        if rec.get("relevance") in ("direct", "indirect") and not ev:
            warnings.append("no_evidence_for_positive: %s" % tag)
        rid = rec.get("record_id")
        if rid in seen:
            errors.append("duplicate_record_id: %s" % rid)
        seen.add(rid)
    return errors, warnings


def _assign_ranks(results):
    """按确定性平局键重排并写 rank=1..N 与 sort_key；不改动语义判断。"""
    ordered = sorted(results, key=_sort_key_for)
    for i, rec in enumerate(ordered):
        rec["rank"] = i + 1
        rec["sort_key"] = list(_sort_key_for(rec))
    return ordered


def run_finalize(output_dir, work_dir):
    out_path = os.path.join(output_dir, "result.json")
    result, err = load_json_loose(out_path)
    if err or not isinstance(result, dict):
        selfcheck = {"errors": [{"kind": "result_missing",
                                 "detail": err or "result.json 缺失"}],
                     "errors_count": 1, "warnings": [], "warnings_count": 0}
        write_json(os.path.join(output_dir, "selfcheck.json"), selfcheck)
        return selfcheck

    norm_path = os.path.join(work_dir, "case_normalized.json")
    norm, _ = load_json_loose(norm_path)
    candidate_count = None
    if isinstance(norm, dict):
        candidate_count = len(norm.get("candidates", []))

    errors, warnings = _validate_result(result, candidate_count)

    if isinstance(result.get("results"), list):
        result["results"] = _assign_ranks(result["results"])

    # dimension_audit 完整性校验（评分维度对齐的硬性要求）
    da = result.get("dimension_audit", {}).get("mapping", {})
    for dim in DIMENSION_AUDIT:
        if dim not in da:
            errors.append("dimension_audit_missing: %s" % dim)
        else:
            for k in ("workflow_steps", "result_fields", "checklist_ids"):
                if not da[dim].get(k):
                    errors.append("dimension_audit_empty: %s.%s" % (dim, k))

    result["runtime"]["ended_at"] = now_iso()
    result["change_log"].append({
        "ts": now_iso(), "step": "finalize",
        "action": "contract validation; errors=%d warnings=%d"
                  % (len(errors), len(warnings)),
    })
    # 只对确定性字段做修复；语义字段缺失一律警告，不改写
    if "dimension_audit" not in result:
        result["dimension_audit"] = {"mapping": DIMENSION_AUDIT}
    write_json(out_path, result)

    selfcheck = {
        "errors": errors, "errors_count": len(errors),
        "warnings": warnings, "warnings_count": len(warnings),
        "candidate_count": candidate_count,
        "checked_at": now_iso(),
    }
    write_json(os.path.join(output_dir, "selfcheck.json"), selfcheck)
    return selfcheck


# ---------------------------------------------------------------------------
# 合成自检（不接触正式案例）
# ---------------------------------------------------------------------------
_SAMPLE_CASE = {
    "case_id": "selftest_case",
    "research_question": "小细胞肺癌脑转移的放疗联合免疫治疗疗效",
    "inclusion_criteria": ["研究对象为小细胞肺癌脑转移患者",
                           "报告放疗联合免疫的疗效结局"],
    "exclusion_criteria": ["非小细胞肺癌", "仅摘要无法判断"],
    "output_contract": {"results": "screened & ranked list",
                        "evidence": "traceability map"},
    "papers": [
        {"id": "p1", "title": "Radiotherapy plus immunotherapy for brain "
         "metastases in small cell lung cancer", "authors": ["A. Test"],
         "year": 2022, "venue": "J Clin Oncol", "doi": "10.0/selftest1",
         "abstract": "Patients with small cell lung cancer brain metastases "
         "received radiotherapy plus immunotherapy and outcomes were "
         "reported.", "source_type": "article"},
        {"id": "p2", "title": "A review of immunotherapy in non-small cell "
         "lung cancer", "authors": ["B. Test"], "year": 2021,
         "venue": "Lung Cancer", "doi": "10.0/selftest2",
         "abstract": "Non-small cell lung cancer treatments are summarized.",
         "source_type": "review"},
        {"id": "p3", "title": "Radiotherapy plus immunotherapy for brain "
         "metastases in small cell lung cancer", "authors": ["A. Test"],
         "year": 2023, "venue": "J Clin Oncol", "doi": "10.0/selftest1",
         "abstract": "Updated analysis of the same cohort.",
         "source_type": "article"},
    ],
}


def run_selftest():
    tmp = tempfile.mkdtemp(prefix="lsr_selftest_")
    inp = os.path.join(tmp, "input")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "output")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(_SAMPLE_CASE, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# selftest fixture（合成夹具，非正式案例）\n")

    fails = []
    skeleton, rep = run_discover(inp, work, out)
    if skeleton["status"] != "partial":
        fails.append("skeleton status=%s" % skeleton["status"])
    if len(skeleton["results"]) != 3:
        fails.append("skeleton results=%d" % len(skeleton["results"]))
    if skeleton["output_contract"] != _SAMPLE_CASE["output_contract"]:
        fails.append("output_contract not echoed")
    if not os.path.exists(os.path.join(out, "result.json")):
        fails.append("result.json missing after discover")

    scaffold, rep2 = run_screen(work, out)
    if len(scaffold.get("dedupe_groups", [])) != 1:
        fails.append("dedupe_groups=%d" % len(scaffold.get("dedupe_groups", [])))

    # 模拟 Agent 填写语义字段（直接改骨架），再跑 finalize
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    for rec in res["results"]:
        if rec["record_id"] == "p1":
            rec.update(relevance="direct", tier="retain", priority_score=0.9,
                       confidence="high", reasons=["匹配对象与方法"],
                       evidence=[{"locator": "case.json#papers[0].abstract",
                                  "quote": "brain metastases ... outcomes"}])
        elif rec["record_id"] == "p2":
            rec.update(relevance="irrelevant", tier="exclude",
                       priority_score=0.1, confidence="high",
                       reasons=["研究对象不匹配"],
                       evidence=[{"locator": "case.json#papers[1].abstract",
                                  "quote": "Non-small cell lung cancer"}])
        else:
            rec.update(relevance="direct", tier="retain", priority_score=0.85,
                       confidence="high", reasons=["同队列更新"],
                       evidence=[{"locator": "case.json#papers[2].abstract",
                                  "quote": "Updated analysis"}])
    write_json(os.path.join(out, "result.json"), res)
    check = run_finalize(out, work)
    if check["errors_count"] != 0:
        fails.append("finalize errors=%s" % check["errors"])

    # 空输入降级
    empty_in = os.path.join(tmp, "empty_input")
    os.makedirs(empty_in, exist_ok=True)
    sk_empty, _ = run_discover(empty_in, os.path.join(tmp, "w2"),
                               os.path.join(tmp, "o2"))
    if sk_empty["status"] != "insufficient_evidence":
        fails.append("empty status=%s" % sk_empty["status"])

    # 坏 JSON 降级
    bad_in = os.path.join(tmp, "bad_input")
    os.makedirs(bad_in, exist_ok=True)
    with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    sk_bad, _ = run_discover(bad_in, os.path.join(tmp, "w3"),
                             os.path.join(tmp, "o3"))
    if "bad_case_json" not in str(sk_bad.get("errors")):
        fails.append("bad json not recorded")

    if fails:
        print("selftest FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    print("selftest ok (fixture only, not a real case)")
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="文献筛选与智能排序 Skill：discover/screen/finalize/selftest")
    parser.add_argument("--cmd", choices=["discover", "screen", "finalize",
                                          "selftest", "all"], default="discover")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "discover":
            run_discover(args.input, args.work, args.output)
        elif args.cmd == "screen":
            run_screen(args.work, args.output)
        elif args.cmd == "finalize":
            run_finalize(args.output, args.work)
        elif args.cmd == "selftest":
            return run_selftest()
        elif args.cmd == "all":
            run_discover(args.input, args.work, args.output)
            run_screen(args.work, args.output)
            run_finalize(args.output, args.work)
        return 0
    except Exception as exc:  # noqa: BLE001
        # 任何未捕获异常都要落一条可读错误，不让进程留下空产物
        msg = "cmd=%s failed: %s\n%s" % (args.cmd, exc,
                                         traceback.format_exc())
        print(msg, file=sys.stderr)
        try:
            err_path = os.path.join(args.output, "skill_error.txt")
            _ensure_dir(args.output)
            with open(err_path, "w", encoding="utf-8") as fh:
                fh.write(msg)
        except OSError:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
