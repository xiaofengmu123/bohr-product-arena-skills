#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献筛选与智能排序 —— 确定性流水线脚手架（仅 Python 标准库）。

子命令：discover（发现/容错解析/写骨架）、screen（词面信号/去重聚类/基线排序）、
finalize（契约校验/确定性排序/决策指纹）。默认路径 /input、/app/work、/app/output，
可用 --input/--work/--output 覆盖；同输入两次运行除时间戳外决策内容一致。

设计原则：脚本只做"确定性先验"与"契约校验"，相关性/分层/打分由 Agent 依据材料填写；
base_score 仅是词面先验，绝不直接当作最终 priority_score；JSON 以 sort_keys 输出、循环
按排序遍历，不依赖随机数与墙钟顺序。
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"
MAX_FILE_BYTES = 2 * 1024 * 1024

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
VALID_RELEVANCE = {"direct", "indirect", "irrelevant", "unknown"}
VALID_TIER = {"retain", "pending", "exclude"}
VALID_CONFIDENCE = {"high", "medium", "low"}
TIER_ORDER = {"retain": 0, "pending": 1, "exclude": 2}

MATERIAL_EXTS = {".pdf", ".md", ".txt", ".jsonl", ".json", ".csv", ".xml"}

CASE_ID_KEYS = ["case_id", "caseId", "id", "challenge_id", "challengeId"]
QUESTION_KEYS = ["research_question", "researchQuestion", "question", "query",
                 "task", "objective", "research_goal", "研究问题", "问题"]
CRITERIA_KEYS = ["inclusion_criteria", "exclusion_criteria", "include_criteria",
                 "exclude_criteria", "inclusion", "exclusion", "criteria",
                 "纳入标准", "排除标准", "纳排标准", "include", "exclude"]
OUTPUT_CONTRACT_KEYS = ["output_contract", "output_schema", "expected_fields",
                        "output_fields", "output_format"]
CANDIDATE_KEYS = ["papers", "candidates", "records", "results", "items",
                  "documents", "literature", "entries", "候选文献", "文献", "候选集"]

TITLE_KEYS = ["title", "Title", "name", "论文标题", "篇名", "题目"]
AUTHOR_KEYS = ["authors", "author", "Authors", "作者"]
YEAR_KEYS = ["year", "Year", "pub_year", "publication_year", "date", "年份", "发表年份"]
VENUE_KEYS = ["venue", "journal", "journal_name", "publication", "source", "期刊", "会议"]
DOI_KEYS = ["doi", "DOI", "Doi"]
URL_KEYS = ["url", "URL", "link", "pdf_url", "pdfUrl"]
ABSTRACT_KEYS = ["abstract", "Abstract", "summary", "摘要"]
SOURCE_TYPE_KEYS = ["source_type", "sourceType", "type", "doc_type", "文献类型", "类型"]

DECISION_FIELDS = ["relevance", "tier", "priority_score", "rank", "reasons",
                   "evidence", "confidence", "criteria_matched", "conflict", "dedupe"]


# ---------------------------------------------------------------- utilities

def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """utf-8-sig -> utf-8 -> latin-1 逐级降级，绝不抛异常。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_json_file(path):
    raw = read_text(path)
    if not raw:
        return None, "empty file", raw
    try:
        return json.loads(raw), None, raw
    except json.JSONDecodeError as e:
        cleaned = raw.lstrip("\ufeff \t\r\n")
        try:
            return json.loads(cleaned), None, raw
        except json.JSONDecodeError:
            return None, "json_decode_error: %s" % e, raw


def canonical(obj):
    """确定性规范化：字典按 key 排序、列表按序、字符串用 json.dumps。"""
    if isinstance(obj, dict):
        return "{" + ",".join(
            canonical(k) + ":" + canonical(v) for k, v in sorted(obj.items())) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(canonical(x) for x in obj) + "]"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if obj is None:
        return "null"
    if isinstance(obj, (int, float)):
        return repr(obj)
    return json.dumps(str(obj), ensure_ascii=False, sort_keys=True)


def _str(v):
    if v is None:
        return None
    s = v if isinstance(v, str) else str(v)
    s = s.strip()
    return s or None


def _first(d, keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    return None


def _authors(val):
    if val is None:
        return []
    if isinstance(val, str):
        return [a.strip() for a in re.split(r"[,;，；、]|\band\b|&", val) if a.strip()]
    if isinstance(val, list):
        out = []
        for a in val:
            if isinstance(a, str):
                out.append(a.strip())
            elif isinstance(a, dict):
                nm = a.get("name") or a.get("full_name") or a.get("author")
                if nm:
                    out.append(str(nm).strip())
        return out
    return []


def _year(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        y = int(val)
        return str(y) if 1000 <= y <= 3000 else None
    m = re.search(r"(1[89]\d{2}|20\d{2})", str(val))
    return m.group(1) if m else None


def norm_title(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.casefold()
    s = re.sub(r"[\s\-_.,:;'\"()\[\]{}]+", " ", s)
    s = re.sub(r"\b(preprint|published version|accepted manuscript|"
               r"author manuscript|version of record|vol\.?\s*\d+|v\d+)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_doi(s):
    if not s:
        return None
    s = s.strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    s = s.strip()
    return s or None


def title_sim(a, b):
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


EN_STOP = {"the", "a", "an", "of", "for", "in", "on", "and", "or", "with",
           "using", "use", "used", "via", "to", "by", "from", "as", "at",
           "is", "are", "was", "were", "be", "been", "its", "their"}


def title_token_set(s):
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return {w for w in s.split() if w and w not in EN_STOP and len(w) >= 2}


def title_likely_same(a, b):
    """同 DOI / 同规范化标题 / 词集合包含或高重叠 / SequenceMatcher 高相似。"""
    if a == b and bool(a):
        return True
    if not a or not b:
        return False
    ta, tb = title_token_set(a), title_token_set(b)
    if ta and tb:
        overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
        if overlap >= 0.85:
            return True
    return title_sim(a, b) >= 0.85


def evidence_completeness(r):
    ev = r.get("evidence") or []
    if not isinstance(ev, list):
        return 0
    return sum(1 for e in ev if isinstance(e, dict) and (e.get("quote") or "").strip())


def decision_sort_key(r):
    """确定性排序键：tier → priority_score desc → 证据完整度 desc →
    year desc → 规范化标题 asc → record_id asc。"""
    tier = TIER_ORDER.get(r.get("tier"), 9)
    ps = r.get("priority_score")
    ps = float(ps) if isinstance(ps, (int, float)) else 0.0
    try:
        year_i = int(r.get("year") or 0)
    except (TypeError, ValueError):
        year_i = 0
    return (tier, -ps, -evidence_completeness(r), -year_i,
            norm_title(r.get("title") or ""), r.get("record_id") or "")


def tokenize(text):
    """英文单词（长度>=2）+ 中文相邻二字，用于词面先验。"""
    if not text:
        return set()
    t = text.casefold()
    words = set(re.findall(r"[a-z0-9]{2,}", t))
    cjk_segs = re.findall(r"[\u4e00-\u9fff]+", t)
    bigrams = set()
    for seg in cjk_segs:
        for i in range(len(seg) - 1):
            bigrams.add(seg[i:i + 2])
    return words | bigrams


def list_input_files(input_dir):
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            rel = os.path.relpath(p, input_dir).replace(os.sep, "/")
            out.append({"path": p, "rel": rel, "size": size,
                        "ext": os.path.splitext(name)[1].lower()})
    return out


# ---------------------------------------------------------------- discover

def find_case_file(files):
    direct = [f for f in files if "/" not in f["rel"]]
    preferred = {"case.json", "case_input.json", "input.json", "input_case.json", "data.json"}
    for f in direct:
        if f["rel"].split("/")[-1].lower() in preferred:
            return f
    for f in direct:
        if f["ext"] == ".json":
            return f
    return None


def find_task_file(files):
    for f in files:
        if "/" not in f["rel"] and f["rel"].lower() in ("task.md", "task.txt", "readme.md", "problem.md"):
            return f
    return None


def norm_candidate(locator, idx, item):
    d = item if isinstance(item, dict) else {"title": item}
    title = _first(d, TITLE_KEYS)
    if title is None and not isinstance(item, dict):
        title = item
    return {
        "record_id": "rec_%04d" % idx,
        "source_locator": locator,
        "title": _str(title),
        "authors": _authors(_first(d, AUTHOR_KEYS)),
        "year": _year(_first(d, YEAR_KEYS)),
        "venue": _str(_first(d, VENUE_KEYS)),
        "doi": _str(_first(d, DOI_KEYS)),
        "url": _str(_first(d, URL_KEYS)),
        "abstract": _str(_first(d, ABSTRACT_KEYS)),
        "source_type": _str(_first(d, SOURCE_TYPE_KEYS)) or "unknown",
        "raw_keys": sorted(d.keys()) if isinstance(d, dict) else [],
    }


def extract_candidates(obj):
    for key in CANDIDATE_KEYS:
        if not isinstance(obj, dict) or key not in obj or obj[key] is None:
            continue
        val = obj[key]
        items = []
        if isinstance(val, list):
            for i, it in enumerate(val):
                items.append(("case.json#%s[%d]" % (key, i), it))
        elif isinstance(val, dict):
            for k in sorted(val.keys()):
                items.append(("case.json#%s.%s" % (key, k), val[k]))
        else:
            items.append(("case.json#%s" % key, val))
        if items:
            return items, key
    return [], None


def extract_output_contract(obj):
    raw = None
    for k in OUTPUT_CONTRACT_KEYS:
        if isinstance(obj, dict) and k in obj:
            raw = obj[k]
            break
    fields = []
    if isinstance(raw, dict):
        fields = list(raw.keys())
    elif isinstance(raw, list):
        for it in raw:
            if isinstance(it, str):
                fields.append(it)
            elif isinstance(it, dict) and "name" in it:
                fields.append(str(it["name"]))
            elif isinstance(it, dict):
                fields.extend(str(k) for k in it.keys())
    elif isinstance(raw, str) and raw:
        fields = [raw]
    return raw, sorted(set(fields))


def _as_text(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _criteria_bundle(obj):
    bundle = {}
    for k in CRITERIA_KEYS:
        if isinstance(obj, dict) and k in obj:
            bundle[k] = obj[k]
    return bundle


def build_skeleton(case_id, status, candidates, contract_fields, sources,
                   errors, degradations, pending, question, criteria_raw,
                   task_headline):
    results = []
    for c in candidates:
        results.append({
            "record_id": c["record_id"],
            "title": c["title"],
            "authors": c["authors"],
            "year": c["year"],
            "venue": c["venue"],
            "doi": c["doi"],
            "url": c["url"],
            "source_type": c["source_type"],
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
        })
    result = {
        "case_id": case_id,
        "status": status,
        "results": results,
        "dedupe_groups": [],
        "sources": sources,
        "uncertainty": [],
        "change_log": [{"ts": utcnow_iso(), "step": "discover", "action": "skeleton"}],
        "runtime": {"budget_seconds": 900, "model_request_budget": 64,
                    "started_at": utcnow_iso(), "finished_at": None},
        "errors": errors,
        "degradations": degradations,
        "pending_human": pending,
    }
    for f in contract_fields:
        if f not in result:
            result[f] = None
    return result


def run_discover(input_dir, work_dir, output_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    errors, degradations, pending = [], [], []
    files = list_input_files(input_dir)

    if not files:
        result = build_skeleton(None, "input_error", [], [], [], errors, degradations,
                                ["输入目录为空或不可读：/input 未发现任何文件"], None, None, None)
        write_json(os.path.join(output_dir, "result.json"), result)
        return result

    case_file = find_case_file(files)
    task_file = find_task_file(files)
    sources = []
    if case_file:
        sources.append({"path": case_file["rel"], "kind": "json", "role": "case_input"})
    if task_file:
        sources.append({"path": task_file["rel"], "kind": "markdown", "role": "task"})

    materials = []
    skip = [f["rel"] for f in (case_file, task_file) if f]
    for f in files:
        if f["rel"] in skip:
            continue
        if f["size"] > MAX_FILE_BYTES:
            degradations.append("skipped_oversize: %s (%d bytes > %d)"
                                % (f["rel"], f["size"], MAX_FILE_BYTES))
            continue
        if f["ext"] in MATERIAL_EXTS:
            materials.append(f)
            sources.append({"path": f["rel"], "kind": f["ext"].lstrip("."), "role": "material"})

    case_id = None
    question = None
    criteria_raw = None
    contract_fields = []
    candidates = []
    task_headline = None

    if task_file:
        txt = read_text(task_file["path"])
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        task_headline = lines[0] if lines else None

    if case_file:
        obj, err, raw = load_json_file(case_file["path"])
        if obj is None:
            errors.append("case_json_unreadable: %s (%s)" % (case_file["rel"], err))
            obj = {}
        if isinstance(obj, dict):
            case_id = _str(_first(obj, CASE_ID_KEYS))
            question = _str(_first(obj, QUESTION_KEYS))
            criteria_raw = _criteria_bundle(obj)
            _, contract_fields = extract_output_contract(obj)
            items, _src = extract_candidates(obj)
            candidates = [norm_candidate(loc, i, it) for i, (loc, it) in enumerate(items)]
        else:
            errors.append("case_json_root_not_object: %s" % case_file["rel"])

    if not candidates:
        degradations.append("no_candidates_found_in_case_json")
        if question is None and task_headline:
            question = task_headline
    else:
        for c in candidates:
            if not c["abstract"]:
                pending.append("missing_abstract: %s" % c["record_id"])
            if not c["year"]:
                pending.append("missing_year: %s" % c["record_id"])
            if not c["doi"]:
                pending.append("missing_doi: %s" % c["record_id"])

    status = "partial"
    if not case_file and not task_file:
        status = "input_error"
        pending.append("未发现 case.json 或 task.md")
    elif not candidates:
        status = "insufficient_evidence"
        pending.append("未发现候选文献；请提供 papers/candidates/records 等字段")

    norm = {
        "case_id": case_id,
        "research_question": question,
        "task_headline": task_headline,
        "criteria": criteria_raw if criteria_raw is not None else {},
        "output_contract_fields": contract_fields,
        "candidates": candidates,
        "materials": [{"path": m["rel"], "abs": m["path"], "kind": m["ext"].lstrip("."),
                       "size": m["size"]} for m in materials],
        "input_files": [{"path": f["rel"], "size": f["size"], "ext": f["ext"]} for f in files],
    }
    write_json(os.path.join(work_dir, "case_normalized.json"), norm)

    result = build_skeleton(case_id, status, candidates, contract_fields, sources,
                            errors, degradations, pending, question, criteria_raw,
                            task_headline)
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


# ---------------------------------------------------------------- screen

def _is_preprint(rec):
    s = " ".join(str(x or "").casefold() for x in
                 [rec.get("source_type"), rec.get("venue"), rec.get("title")])
    return ("preprint" in s or "arxiv" in s or "biorxiv" in s or "medrxiv" in s)


def _meta_completeness(rec):
    return sum(1 for k in ("doi", "venue", "abstract", "year", "url") if rec.get(k))


def run_screen(work_dir, output_dir):
    norm_path = os.path.join(work_dir, "case_normalized.json")
    obj, err, _ = load_json_file(norm_path)
    if obj is None:
        raise SystemExit("screen: cannot read %s (%s)" % (norm_path, err))
    candidates = obj.get("candidates") or []
    question = obj.get("research_question") or ""
    criteria = obj.get("criteria") or {}
    crit_text = " ".join(_as_text(v) for v in (criteria.values() if isinstance(criteria, dict) else [criteria]))
    q_tokens = tokenize(question + " " + crit_text)

    records = []
    for c in candidates:
        title_tokens = tokenize(c.get("title") or "")
        abs_tokens = tokenize(c.get("abstract") or "")
        title_hits = sorted(q_tokens & title_tokens)
        abs_hits = sorted(q_tokens & abs_tokens)
        title_ratio = len(title_hits) / max(1, len(q_tokens))
        abs_ratio = len(abs_hits) / max(1, len(q_tokens))
        base_score = round(min(1.0, 0.65 * title_ratio + 0.35 * abs_ratio), 4)
        records.append({
            "record_id": c["record_id"],
            "norm_title": norm_title(c.get("title") or ""),
            "norm_doi": norm_doi(c.get("doi") or ""),
            "base_score": base_score,
            "lexical_hits": title_hits + [h for h in abs_hits if h not in title_hits],
            "is_preprint": _is_preprint(c),
            "meta_completeness": _meta_completeness(c),
        })

    # 去重候选聚类（确定性）：同 DOI / 同规范化标题 / 标题相似度 > 0.92
    groups = []
    for i in range(len(records)):
        placed = False
        for g in groups:
            for j in g:
                a, b = records[i], records[j]
                same_doi = a["norm_doi"] and b["norm_doi"] and a["norm_doi"] == b["norm_doi"]
                if same_doi or title_likely_same(a["norm_title"], b["norm_title"]):
                    g.add(i)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            groups.append({i})

    dedupe_candidates = []
    for g in groups:
        members = sorted(g)
        # 组内裁定优先级：正式发表 > 预印本；年份新 > 旧；元数据完整 > 缺；record_id 兜底
        def grp_key(i):
            r = records[i]
            return (1 if r["is_preprint"] else 0,
                    -(int(candidates[i].get("year") or 0)),
                    -r["meta_completeness"],
                    r["record_id"])
        ordered = sorted(members, key=grp_key)
        group_title = records[ordered[0]]["norm_title"] or "group"
        group_id = "grp_" + hashlib.sha256(group_title.encode("utf-8")).hexdigest()[:8]
        roles = {}
        for pos, mi in enumerate(ordered):
            roles[records[mi]["record_id"]] = "primary" if pos == 0 else ("duplicate" if pos == 1 else "version")
        basis = "exact" if all(records[m]["norm_title"] == records[ordered[0]]["norm_title"] for m in members) else "similar"
        dedupe_candidates.append({
            "group_id": group_id,
            "members": [records[m]["record_id"] for m in ordered],
            "roles": roles,
            "basis": basis,
        })

    # 基线排序：base_score desc → year desc → norm_title asc → record_id asc
    def base_sort_key(i):
        r = records[i]
        return (-r["base_score"], -(int(candidates[i].get("year") or 0)),
                r["norm_title"], r["record_id"])
    scaffold_order = [records[i]["record_id"] for i in sorted(range(len(records)), key=base_sort_key)]
    for pos, i in enumerate(sorted(range(len(records)), key=base_sort_key)):
        records[i]["base_rank"] = pos + 1

    scaffold = {
        "case_id": obj.get("case_id"),
        "q_tokens_count": len(q_tokens),
        "records": records,
        "scaffold_order": scaffold_order,
        "dedupe_candidates": dedupe_candidates,
        "note": "base_score 仅为词面先验，不得直接当作最终 priority_score",
    }
    write_json(os.path.join(work_dir, "scaffold.json"), scaffold)
    return scaffold


# ---------------------------------------------------------------- finalize

def decision_content(result):
    out = {"case_id": result.get("case_id"), "results": [], "dedupe_groups": result.get("dedupe_groups") or []}
    for r in result.get("results") or []:
        d = {}
        for k in ["record_id", "title", "authors", "year", "venue", "doi", "url", "source_type"]:
            d[k] = r.get(k)
        for k in DECISION_FIELDS:
            d[k] = r.get(k)
        out["results"].append(d)
    return out


def fingerprint(result):
    return hashlib.sha256(canonical(decision_content(result)).encode("utf-8")).hexdigest()


def run_finalize(work_dir, output_dir):
    result_path = os.path.join(output_dir, "result.json")
    norm_path = os.path.join(work_dir, "case_normalized.json")
    result, err, _ = load_json_file(result_path)
    norm, nerr, _ = load_json_file(norm_path)
    errors, warnings = [], []

    if result is None:
        return {"errors": ["result.json missing or unreadable: %s" % err],
                "warnings": [], "fingerprint": None, "result": None}
    if norm is None:
        warnings.append("case_normalized.json 不可读，覆盖性校验降级为仅检查 result.json 本身")

    if not result.get("case_id"):
        errors.append("case_id 缺失")
    if result.get("status") not in VALID_STATUS:
        errors.append("status 非法：%r" % result.get("status"))

    contract_fields = (norm or {}).get("output_contract_fields") or []
    for f in contract_fields:
        if f not in result:
            errors.append("缺少 output_contract 字段：%s" % f)

    expected_ids = [c["record_id"] for c in (norm or {}).get("candidates", [])]
    results = result.get("results")
    if not isinstance(results, list) or not results:
        errors.append("results 为空或非列表")
        results = results if isinstance(results, list) else []

    result_ids = [r.get("record_id") for r in results]
    if len(result_ids) != len(set(result_ids)):
        errors.append("results 存在重复 record_id")
    if expected_ids:
        missing = [x for x in expected_ids if x not in result_ids]
        extra = [x for x in result_ids if x not in expected_ids]
        if missing:
            errors.append("候选覆盖缺失：%s" % ", ".join(missing))
        if extra:
            errors.append("results 出现未知 record_id：%s" % ", ".join(extra))

    for r in results:
        rid = r.get("record_id")
        if r.get("relevance") not in VALID_RELEVANCE:
            errors.append("%s: relevance 非法 %r" % (rid, r.get("relevance")))
        if r.get("tier") not in VALID_TIER:
            errors.append("%s: tier 非法 %r" % (rid, r.get("tier")))
        ps = r.get("priority_score")
        if ps is None:
            errors.append("%s: priority_score 缺失" % rid)
        elif not (isinstance(ps, (int, float)) and 0.0 <= ps <= 1.0):
            errors.append("%s: priority_score 越界 %r" % (rid, ps))
        for ev in (r.get("evidence") or []):
            if not isinstance(ev, dict) or not (ev.get("locator") or "").strip() or not (ev.get("quote") or "").strip():
                errors.append("%s: 证据项须含 locator 与逐字 quote" % rid)
        if r.get("tier") == "retain" and not r.get("reasons"):
            warnings.append("%s: retain 但无 reasons" % rid)
        if r.get("relevance") == "unknown" and r.get("tier") != "pending":
            warnings.append("%s: relevance=unknown 应归入 pending" % rid)
        if r.get("tier") in ("retain", "exclude") and evidence_completeness(r) == 0:
            warnings.append("%s: tier=%s 但无证据引用" % (rid, r.get("tier")))

    if not errors and results:
        # 强制确定性排序，并重写 rank=1..N（保证同输入同顺序）
        try:
            results.sort(key=decision_sort_key)
        except Exception as e:  # noqa: BLE001
            errors.append("确定性排序失败：%s" % e)
        for i, r in enumerate(results):
            r["rank"] = i + 1
        result["results"] = results

    fp = fingerprint(result)
    result["decision_fingerprint"] = fp
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["finished_at"] = utcnow_iso()

    # 回写校验错误到 result.json（幂等：先移除上一轮 finalize 项再重算）
    base_errors = [x for x in (result.get("errors") or [])
                   if not str(x).startswith("[finalize] ")]
    result["errors"] = base_errors + ["[finalize] " + str(e) for e in errors]

    # 诚实 status 修正
    status = result.get("status")
    if status in ("ok", "partial"):
        if errors:
            result["status"] = "partial"
            result["change_log"] = (result.get("change_log") or []) + [
                {"ts": utcnow_iso(), "step": "finalize", "action": "status->partial (存在 errors)"}]
        elif status == "partial" and not (result.get("pending_human") or []):
            result["status"] = "ok"
            result["change_log"] = (result.get("change_log") or []) + [
                {"ts": utcnow_iso(), "step": "finalize", "action": "status->ok (errors=0)"}]

    write_json(result_path, result)
    selfcheck = {
        "case_id": result.get("case_id"),
        "errors": errors,
        "warnings": warnings,
        "decision_fingerprint": fp,
        "result_count": len(results),
        "checked_at": utcnow_iso(),
    }
    write_json(os.path.join(output_dir, "selfcheck.json"), selfcheck)
    return {"errors": errors, "warnings": warnings, "fingerprint": fp, "result": result}


# ---------------------------------------------------------------- CLI

def _add_io_args(sp):
    sp.add_argument("--input", default=os.environ.get("INPUT_DIR", DEFAULT_INPUT))
    sp.add_argument("--work", default=os.environ.get("WORK_DIR", DEFAULT_WORK))
    sp.add_argument("--output", default=os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT))


def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py", description="文献筛选与智能排序：确定性流水线脚手架")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("discover", help="发现输入、写骨架与归一化文件")
    _add_io_args(sp)
    sp = sub.add_parser("screen", help="确定性词面信号、去重聚类、基线排序")
    _add_io_args(sp)
    sp = sub.add_parser("finalize", help="契约校验、确定性排序、决策指纹")
    _add_io_args(sp)
    args = p.parse_args(argv)

    if args.cmd == "discover":
        r = run_discover(args.input, args.work, args.output)
        print("discover: case_id=%s status=%s results=%d sources=%d"
              % (r.get("case_id"), r.get("status"), len(r.get("results") or []),
                 len(r.get("sources") or [])))
        return 0
    if args.cmd == "screen":
        s = run_screen(args.work, args.output)
        print("screen: records=%d dedupe_groups=%d scaffold_order=%d"
              % (len(s.get("records") or []), len(s.get("dedupe_candidates") or []),
                 len(s.get("scaffold_order") or [])))
        return 0
    if args.cmd == "finalize":
        rep = run_finalize(args.work, args.output)
        print("finalize: errors=%d warnings=%d fingerprint=%s"
              % (len(rep.get("errors") or []), len(rep.get("warnings") or []),
                 (rep.get("fingerprint") or "")[:12]))
        if rep.get("errors"):
            for e in rep["errors"]:
                print("  [error] %s" % e)
            return 1
        for w in rep.get("warnings") or []:
            print("  [warn] %s" % w)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
