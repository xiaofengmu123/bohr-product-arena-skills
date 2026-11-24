#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 核心实现（纯标准库，确定性、可复核）。

本模块承载全部 IO 与流水线逻辑；main.py 仅做命令行分发。
设计目标：
  * 任何时刻都能落盘一版合法 result.json（先骨架、后增量）；
  * 以结构化中间产物（case_summary / plan / records / evidence / rubric /
    manifest）作为唯一事实源，便于复核与复现；
  * 所有 IO 容错：空输入、坏 JSON、坏编码、缺文件均不崩溃。
"""
import hashlib
import json
import os
import re
import sys
import time

MAX_FILE_BYTES = 2 * 1024 * 1024

CONTRACT_KEY_NAMES = (
    "output_contract", "outputContract", "output", "result_schema", "schema",
    "required_output", "required_fields", "expected_fields", "contract",
)

CASE_ID_KEYS = (
    "case_id", "caseId", "id", "challenge_id", "challengeId", "armiId",
)

RESEARCH_QUESTION_KEYS = (
    "research_question", "researchQuestion", "question", "query", "topic",
    "task", "problem", "research_topic", "research_problem", "title",
)

DEFAULT_RUBRIC = {
    "rubric_id": "academic-literature-search/v1",
    "weights": {"relevance": 0.6, "metadata": 0.2, "source": 0.2},
    "metadata_fields": ["title", "authors", "year", "journal", "doi"],
    "source_reliability": {
        "crossref": 1.0,
        "europepmc": 0.95,
        "europe_pmc": 0.95,
        "pubmed": 0.95,
        "publisher": 0.95,
        "openalex": 0.9,
        "semantic_scholar": 0.9,
        "arxiv": 0.9,
        "web_search": 0.6,
        "paper_search": 0.8,
        "materials": 0.7,
        "unknown": 0.5,
    },
}


# --------------------------------------------------------------------------
# 稳健 IO
# --------------------------------------------------------------------------
def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def read_text(path, max_bytes=MAX_FILE_BYTES):
    if not path or not os.path.isfile(path):
        return "", ["missing:" + str(path)]
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", ["stat_failed:" + str(path)]
    if size > max_bytes:
        return "", ["size_exceeded:" + str(path) + ":" + str(size)]
    try:
        data = open(path, "rb").read()
    except OSError as e:
        return "", ["read_failed:" + str(path) + ":" + str(e)[:120]]
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
    except Exception as e:
        return default, ["bad_json:" + str(path) + ":" + str(e)[:160]]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("write_text 失败:", path, str(e)[:200], file=sys.stderr)
        return False


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("write_json 失败:", path, str(e)[:200], file=sys.stderr)
        return False


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def file_sha256(path):
    try:
        data = open(path, "rb").read()
        return hashlib.sha256(data).hexdigest(), len(data)
    except OSError:
        return None, 0


def log_change(work_dir, action, reason, status="partial"):
    path = os.path.join(work_dir, "change_log.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "time": now_iso(),
        "action": action,
        "reason": reason,
        "status": status,
    })
    write_json(path, entries)
    return entries


# --------------------------------------------------------------------------
# 输入发现与解析
# --------------------------------------------------------------------------
def discover_input(input_dir):
    out = []
    if not input_dir or not os.path.isdir(input_dir):
        return out
    for root, _dirs, names in os.walk(input_dir):
        for n in sorted(names):
            out.append(os.path.join(root, n))
    return out


def find_case_file(files):
    for f in files:
        if os.path.basename(f).lower() == "case.json":
            return f
    for f in files:
        b = os.path.basename(f).lower()
        if b.startswith("case") and b.endswith(".json"):
            return f
    return None


def find_task_file(files):
    for f in files:
        if os.path.basename(f).lower() == "task.md":
            return f
    for f in files:
        b = os.path.basename(f).lower()
        if b.endswith(".md") and "task" in b:
            return f
    for f in files:
        if f.lower().endswith(".md"):
            return f
    return None


def find_material_files(files):
    """材料：PDF 与可解析的检索响应 JSON（Crossref/EuropePMC 等）。"""
    mats = []
    for f in files:
        b = os.path.basename(f).lower()
        if b in ("case.json", "task.md"):
            continue
        if "task" in b and b.endswith(".md"):
            continue
        if b.endswith((".pdf", ".json", ".md", ".txt")):
            mats.append(f)
    return mats


def collect_contract_template(case_data):
    """递归收集 output_contract 结构，返回回填模板（dict->dict/list->[]/标量->None）。"""
    if not isinstance(case_data, dict):
        return {}
    for k in CONTRACT_KEY_NAMES:
        v = case_data.get(k)
        if isinstance(v, dict):
            return {kk: _contract_tpl(vv) for kk, vv in v.items()}
        if isinstance(v, list):
            return {str(x): None for x in v if isinstance(x, str)}
        if isinstance(v, str):
            return {v.strip(): None} if v.strip() else {}
        if v is not None:
            return {}
    return {}


def _contract_tpl(value):
    if isinstance(value, dict):
        return {k: _contract_tpl(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def extract_case_id(case_data, task_text, case_path):
    v = None
    for k in CASE_ID_KEYS:
        tmp = case_data.get(k)
        if tmp not in (None, ""):
            v = str(tmp)
            break
    if not v:
        m = re.search(r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)", task_text)
        if m:
            v = m.group(1)
    if not v:
        v = ("case:" + os.path.splitext(os.path.basename(case_path))[0]) if case_path else "unknown"
    return v


def extract_research_question(case_data, task_text):
    for k in RESEARCH_QUESTION_KEYS:
        v = case_data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    m = re.search(
        r"(?:研究问题|科研问题|研究主题|Research question|Research topic)\s*[:：]\s*(.+)",
        task_text,
    )
    if m:
        return m.group(1).strip()[:2000]
    for line in task_text.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", "-", "|", "`")) and len(s) < 500:
            if re.search(r"(检索|find|search|文献|literature)", s, re.I):
                return s[:2000]
    return None


def extract_scope(case_data):
    scope = {}
    for k in ("search_scope", "scope"):
        if k in case_data and isinstance(case_data[k], dict):
            scope = case_data[k]
            break
    out = {
        "databases": scope.get("databases", []) or [],
        "time_range": scope.get("time_range") or scope.get("years") or None,
        "languages": scope.get("languages", []) or [],
        "document_types": scope.get("document_types", scope.get("types", [])) or [],
        "constraints": scope.get("constraints", []) or [],
    }
    return out


def parse_case(input_dir, work_dir):
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    mat_paths = find_material_files(files)
    degradations = []

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    degradations += d
    task_text, d = read_text(task_path) if task_path else ("", ["no_task_md"])
    degradations += d

    case_id = extract_case_id(case_data, task_text, case_path)
    research_question = extract_research_question(case_data, task_text)
    scope = extract_scope(case_data)
    contract_tpl = collect_contract_template(case_data)

    summary = {
        "case_path": case_path,
        "task_path": task_path,
        "material_files": mat_paths,
        "case_id": case_id,
        "research_question": research_question,
        "search_scope": scope,
        "output_contract_template": contract_tpl,
        "contract_keys": sorted(contract_tpl.keys()),
        "case_keys": sorted(case_data.keys()) if isinstance(case_data, dict) else [],
        "degradations": degradations,
    }
    write_json(os.path.join(work_dir, "case_summary.json"), summary)
    return summary


# --------------------------------------------------------------------------
# 查询规划（结构化中间产物：查询步骤 + 术语扩展核验清单）
# --------------------------------------------------------------------------
_TERM_RE = re.compile(r"[\w\u4e00-\u9fff\-\+]{2,}", re.UNICODE)


def _dedup_preserve(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def build_plan(summary):
    rq = summary.get("research_question") or ""
    scope = summary.get("search_scope") or {}
    tokens = _dedup_preserve(_TERM_RE.findall(rq or ""))[:12]
    databases = scope.get("databases") or ["crossref", "europepmc", "publisher_materials"]
    steps = []
    tomato = "计划由 case_summary 自动生成，术语扩展与同义词需在执行阶段按材料逐条核验"
    steps.append({
        "step": 1,
        "query_id": "Q1",
        "concept": "核心主题（单概念召回）",
        "terms": tokens or ["(待执行时回填)"],
        "synonyms": [],
        "abbreviations": [],
        "cross_language": [],
        "operator": "OR",
        "targets": databases,
        "purpose": "先以核心术语宽召回，保证 Recall 起点",
        "success_criteria": "返回 5+ 条非空候选并记录数据库/时间戳",
        "status": "planned",
        "note": tomato,
    })
    steps.append({
        "step": 2,
        "query_id": "Q2",
        "concept": "概念组合（交叉约束）",
        "terms": tokens or ["(待执行时回填)"],
        "synonyms": [],
        "abbreviations": [],
        "cross_language": [],
        "operator": "AND",
        "targets": databases,
        "purpose": "用 AND 组合收窄，提升 Precision",
        "success_criteria": "在 Q1 基础上过滤明显无关、保留主题相关",
        "status": "planned",
        "note": tomato,
    })
    steps.append({
        "step": 3,
        "query_id": "Q3",
        "concept": "引文/参考文献追踪",
        "terms": [],
        "synonyms": [],
        "abbreviations": [],
        "cross_language": [],
        "operator": "citation",
        "targets": ["materials_reference_graph"],
        "purpose": "从候选文献的参考文献图补充关键文献，防遗漏",
        "success_criteria": "为每条高相关候选记录其参考文献并去重",
        "status": "planned",
        "note": tomato,
    })
    plan = {
        "plan_id": "plan-" + sha256_hex(rq or "empty")[:12],
        "research_question": rq or None,
        "search_scope": scope,
        "query_steps": steps,
        "term_expansion_checklist": {
            "synonyms": "待核验",
            "abbreviations": "待核验",
            "cross_language": "待核验",
            "related_concepts": "待核验",
        },
        "created_at": now_iso(),
    }
    return plan


# --------------------------------------------------------------------------
# 结果记录：归一化 / 去重 / 打分 / 排序
# --------------------------------------------------------------------------
def normalize_text(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def normalize_doi(doi):
    s = str(doi or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = s.rstrip(".,;")
    return s.lower()


def stable_record_id(rec):
    doi = normalize_doi(rec.get("doi"))
    if doi:
        return "doi:" + doi
    title = normalize_text(rec.get("title") or "")
    slug = re.sub(r"\s+", "-", title)[:80] or "no-title"
    return "title:" + slug + ":" + sha256_hex(title)[:8]


def _first_nonnull(rec, keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def coerce_record(raw, database=None, tool=None, query_id=None, note=None):
    """把 Crossref / EuropePMC / 通用记录归一化为统一 schema。"""
    if not isinstance(raw, dict):
        return None
    # EuropePMC: 单条 result
    if isinstance(raw.get("result"), dict):
        raw = raw["result"]

    title = _first_nonnull(raw, ("title", "articleTitle", "bookTitle"))
    if isinstance(title, list):
        title = title[0] if title else ""
    authors = []
    if isinstance(raw.get("authors"), list):
        authors = raw["authors"]
    elif isinstance(raw.get("author"), list):
        for a in raw["author"]:
            if isinstance(a, dict):
                g = a.get("given", "")
                f = a.get("family", "")
                name = " ".join(x for x in (g, f) if x).strip()
                if not name and a.get("name"):
                    name = a["name"]
                if name and name not in authors:
                    authors.append(name)
    elif isinstance(raw.get("authorString"), str) and raw["authorString"].strip():
        authors = [x.strip() for x in raw["authorString"].split(",") if x.strip()]

    year = None
    for k in ("year", "pubYear", "publicationYear"):
        v = raw.get(k)
        if isinstance(v, int):
            year = v
            break
        if isinstance(v, str) and re.fullmatch(r"\d{4}", v):
            year = int(v)
            break
    if year is None:
        for k in ("published-print", "published", "issued", "created"):
            dp = raw.get(k)
            if isinstance(dp, dict):
                parts = dp.get("date-parts")
                if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                    try:
                        year = int(parts[0][0])
                    except (TypeError, ValueError):
                        pass
                    if year:
                        break

    journal = _first_nonnull(raw, ("journal", "journalInfo", "container-title",
                                   "publicationTitle", "source"))
    if isinstance(journal, list):
        journal = journal[0] if journal else ""
    if isinstance(journal, dict):
        inner = journal.get("journal")
        if isinstance(inner, dict):
            journal = inner.get("title") or inner.get("medlineAbbreviation") or \
                inner.get("name") or ""
        else:
            journal = inner or journal.get("title") or \
                journal.get("medlineAbbreviation") or ""

    doi = raw.get("doi") or raw.get("DOI")
    url = _first_nonnull(raw, ("url", "URL", "link", "fullTextUrl"))

    rec = {
        "record_id": stable_record_id({"title": title, "doi": doi}),
        "title": title or None,
        "authors": authors or [],
        "year": year,
        "journal": journal or None,
        "doi": doi or None,
        "url": url or None,
        "database": database or raw.get("database") or raw.get("source") or "unknown",
        "relevance": None,
        "relevance_reason": None,
        "score": None,
        "score_breakdown": None,
        "evidence": [],
        "dedup": None,
        "provenance": {
            "tool": tool or raw.get("tool") or "import",
            "retrieved_at": now_iso(),
            "query_id": [query_id] if query_id else [],
            "note": note,
        },
        "verified_metadata": False,
        "conflicts": [],
    }
    # 拓展：Crossref 常见字段
    if not rec["url"] and rec["doi"]:
        rec["url"] = "https://doi.org/" + rec["doi"]
    return rec


def unwrap_batch(payload):
    """从多种批处理结构中抽取记录列表。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # EuropePMC：resultList 是 {"result": [...]}
        rl = payload.get("resultList")
        if isinstance(rl, dict) and isinstance(rl.get("result"), list):
            return rl["result"]
        for k in ("results", "items", "resultList", "records", "candidates"):
            if k in payload and isinstance(payload[k], list):
                inner = payload[k]
                if k == "resultList" and inner and isinstance(inner[0], dict):
                    return inner
                return inner
        if isinstance(payload.get("message"), dict) and isinstance(payload["message"].get("items"), list):
            return payload["message"]["items"]
    return []


def import_records(work_dir, records, source_label, database=None, tool=None, query_id=None):
    path = os.path.join(work_dir, "records.json")
    existing, _ = read_json(path, [])
    if not isinstance(existing, list):
        existing = []
    by_id = {}
    for r in existing:
        if isinstance(r, dict) and r.get("record_id"):
            by_id[r["record_id"]] = r
    imported, updated, skipped = 0, 0, 0
    for raw in records:
        rec = coerce_record(raw, database=database, tool=tool, query_id=query_id)
        if rec is None:
            skipped += 1
            continue
        if rec["record_id"] in by_id:
            # upsert：合并 evidence / provenance，按新来源补充缺失元数据
            old = by_id[rec["record_id"]]
            for k in ("title", "authors", "year", "journal", "doi", "url"):
                if old.get(k) in (None, "", []):
                    old[k] = rec.get(k)
            old["database"] = rec.get("database") or old.get("database")
            qids = list(old.setdefault("provenance", {}).get("query_id") or [])
            for q in (rec.get("provenance", {}).get("query_id") or []):
                if q not in qids:
                    qids.append(q)
            old["provenance"]["query_id"] = qids
            updated += 1
        else:
            by_id[rec["record_id"]] = rec
            imported += 1
    out = list(by_id.values())
    st = {"imported": imported, "updated": updated, "skipped": skipped,
          "total": len(out), "source_label": source_label, "at": now_iso()}
    write_json(path, out)
    # 记录导入状态（增量状态）
    imports_path = os.path.join(work_dir, "import_state.json")
    states, _ = read_json(imports_path, [])
    if not isinstance(states, list):
        states = []
    states.append(st)
    write_json(imports_path, states)
    return st


# --------------------------------------------------------------------------
# 证据记录
# --------------------------------------------------------------------------
def append_evidence(work_dir, ev):
    if not isinstance(ev, dict):
        ev = {}
    path = os.path.join(work_dir, "evidence.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "evidence_id": "ev-" + sha256_hex(json.dumps(ev, sort_keys=True, default=str))[:12],
        "kind": ev.get("kind", "source"),
        "query": ev.get("query"),
        "source": ev.get("source") or ev.get("database") or "unknown",
        "database": ev.get("database"),
        "timestamp": ev.get("timestamp") or now_iso(),
        "rationale": ev.get("rationale"),
        "locator": ev.get("locator"),
        "quote": ev.get("quote"),
        "result_count": ev.get("result_count"),
        "query_id": ev.get("query_id"),
    })
    write_json(path, entries)
    return entries[-1]


# --------------------------------------------------------------------------
# 打分与排序（透明加权，rubric 可替换为结构化中间产物）
# --------------------------------------------------------------------------
def load_rubric(work_dir):
    path = os.path.join(work_dir, "rubric.json")
    rubric, deg = read_json(path, {})
    if not isinstance(rubric, dict) or not rubric.get("weights") or \
            not isinstance(rubric.get("source_reliability"), dict):
        rubric = DEFAULT_RUBRIC
        write_json(path, rubric)
    return rubric


def _meta_complete(rec, fields):
    total = len(fields)
    got = 0
    for f in fields:
        v = rec.get(f)
        if f == "authors":
            if isinstance(v, list) and v:
                got += 1
            continue
        if v not in (None, "", [], {}):
            got += 1
    return got / max(total, 1)


def _source_reliability(rec, rubric):
    db = normalize_text(rec.get("database") or "")
    m = rubric.get("source_reliability", {})
    for key in ("crossref", "europepmc", "europe_pmc", "pubmed", "publisher",
                "openalex", "semantic_scholar", "arxiv"):
        if key in db:
            return m.get(key, 0.8)
    if "material" in db or "pdf" in db:
        return m.get("materials", 0.7)
    return m.get("unknown", 0.5)


def attach_evidence_to_record(evidences, rec):
    """把以 query_id 关联的证据（locator+quote）挂到记录上，供可复核。"""
    qids = set(rec.get("provenance", {}).get("query_id") or [])
    got = []
    for ev in evidences:
        eid = ev.get("query_id")
        loc = ev.get("locator")
        quote = ev.get("quote")
        if (eid and eid in qids) or loc or quote:
            if eid and eid in qids:
                got.append(ev)
    return got


def score_and_rank(records, rubric, evidences):
    weights = rubric.get("weights", {"relevance": 0.6, "metadata": 0.2, "source": 0.2})
    fields = rubric.get("metadata_fields", ["title", "authors", "year", "journal", "doi"])
    scored = []
    for r in records:
        if not isinstance(r, dict):
            continue
        rel = r.get("relevance")
        rel_norm = (rel / 100.0) if isinstance(rel, (int, float)) and 0 <= rel <= 100 else 0.0
        mc = _meta_complete(r, fields)
        sr = _source_reliability(r, rubric)
        score = 100.0 * (weights.get("relevance", 0.6) * rel_norm +
                         weights.get("metadata", 0.2) * mc +
                         weights.get("source", 0.2) * sr)
        r["score"] = round(score, 3)
        r["score_breakdown"] = {
            "relevance": round(rel_norm, 4),
            "metadata_completeness": round(mc, 4),
            "source_reliability": sr,
        }
        linked = attach_evidence_to_record(evidences, r)
        r["evidence"] = [{"locator": e.get("locator"), "quote": e.get("quote"),
                          "kind": e.get("kind"), "source": e.get("source")}
                         for e in linked if e.get("locator") or e.get("quote")]
        scored.append(r)

    def sort_key(r):
        return (r.get("score") or 0.0, r.get("year") or 0, normalize_text(r.get("title") or ""))
    scored.sort(key=sort_key, reverse=True)
    for i, r in enumerate(scored, 1):
        r["rank"] = i
    return scored


def dedup_records(scored):
    """按 DOI > 规范化标题 去重，保留代表记录并记录 duplicates。"""
    groups = {}
    order = []
    for r in scored:
        doi = normalize_doi(r.get("doi") or "")
        key = ("doi:" + doi) if doi else ("title:" + normalize_text(r.get("title") or ""))
        if key not in groups:
            groups[key] = r
            order.append(key)
        else:
            rep = groups[key]
            dupes = rep.setdefault("dedup", {"canonical": key, "duplicates": [], "method": None})
            dupes.setdefault("duplicates", []).append(r.get("record_id"))
            dupes["method"] = "doi" if doi else "title_ngram"
            # 用更完整元数据 / 更可靠来源的代表记录
            if _meta_complete(r, ["title", "authors", "year", "journal", "doi"]) > \
               _meta_complete(rep, ["title", "authors", "year", "journal", "doi"]) and \
               _source_reliability(r, {"source_reliability": DEFAULT_RUBRIC["source_reliability"]}) >= \
               _source_reliability(rep, {"source_reliability": DEFAULT_RUBRIC["source_reliability"]}):
                rep_dupes = rep.get("dedup", {}).get("duplicates", [])
                groups[key] = r
                r.setdefault("dedup", {"canonical": key, "duplicates": rep_dupes, "method": None})
    out = []
    for key in order:
        g = groups[key]
        d = g.get("dedup") or {}
        d["canonical"] = key
        if not d.get("method"):
            d["method"] = "none"
        g["dedup"] = d
        out.append(g)
    return out


# --------------------------------------------------------------------------
# 组装 result.json 与 candidates.md
# --------------------------------------------------------------------------
def derive_sources(records, evidences):
    srcs = {}
    for r in records:
        db = r.get("database")
        if db:
            srcs[normalize_text(str(db))] = {"name": str(db), "type": "database"}
    for e in evidences:
        s = e.get("source")
        if s:
            srcs[normalize_text(str(s))] = {"name": str(s),
                                            "type": e.get("kind", "source")}
    out = []
    for k, v in sorted(srcs.items()):
        if k and v["name"]:
            out.append({"name": v["name"], "type": v["type"], "accessed_at": now_iso()})
    return out


def assemble(input_dir, output_dir, work_dir):
    summary, deg1 = read_json(os.path.join(work_dir, "case_summary.json"), {})
    if not isinstance(summary, dict):
        summary = {}
    records, deg2 = read_json(os.path.join(work_dir, "records.json"), [])
    if not isinstance(records, list):
        records = []
    evidences, deg3 = read_json(os.path.join(work_dir, "evidence.json"), [])
    if not isinstance(evidences, list):
        evidences = []
    plan, deg4 = read_json(os.path.join(work_dir, "plan.json"), {})
    changes, deg5 = read_json(os.path.join(work_dir, "change_log.json"), [])
    state, deg6 = read_json(os.path.join(work_dir, "state.json"), {})

    case_id = summary.get("case_id") or "unknown"
    has_input = bool(summary.get("case_path") or summary.get("task_path"))

    rubric = load_rubric(work_dir)
    scored = score_and_rank(records, rubric, evidences)
    deduped = dedup_records(scored)

    missing_meta = []
    conflicts = []
    for r in deduped:
        for f in ("title", "authors", "year", "journal", "doi"):
            v = r.get(f)
            if v in (None, "", [], {}):
                missing_meta.append({"record_id": r.get("record_id"), "field": f})
        if r.get("conflicts"):
            conflicts.append({"record_id": r.get("record_id"), "conflicts": r["conflicts"]})

    degs = sorted(set(deg1 + deg2 + deg3 + deg4 + deg5 + deg6 +
                      summary.get("degradations", [])))

    # 状态判定
    if not has_input:
        status = "input_error"
    elif not deduped:
        status = "insufficient_evidence"
    elif missing_meta or degs:
        status = "partial"
    else:
        status = "ok"

    # 运行时
    started = state.get("started_at")
    finished = now_iso()
    elapsed = None
    if isinstance(state.get("started_ts"), (int, float)):
        elapsed = round(time.time() - state["started_ts"], 1)

    result = {
        "case_id": case_id,
        "status": status,
        "research_question": summary.get("research_question"),
        "search_scope": summary.get("search_scope") or {},
        "results": deduped,
        "evidence": evidences,
        "sources": derive_sources(deduped, evidences),
        "uncertainty": {
            "missing_metadata": missing_meta,
            "conflicts": conflicts,
            "coverage_gaps": [],
            "notes": [],
        },
        "status_notes": [],
        "pending_human": [],
        "change_log": changes if isinstance(changes, list) else [],
        "errors": [],
        "degradations": degs,
        "limitations": [],
        "runtime": {"started_at": started, "finished_at": finished, "elapsed_s": elapsed},
    }

    # 回填题目 output_contract（缺值给 null/[]，不改名、不删键）
    tpl = summary.get("output_contract_template")
    if isinstance(tpl, dict):
        for k, v in tpl.items():
            if k not in result:
                result[k] = v

    # 状态说明
    notes = result["status_notes"]
    if status == "insufficient_evidence":
        notes.append("未获得候选文献或材料不足；已保留空结果，不补造文献。")
    if status == "partial":
        notes.append("已获得部分候选，但仍存在元数据缺失或读取降级，详见 uncertainty/degradations。")
    if status == "input_error":
        notes.append("输入目录无有效 case.json/task.md，返回最小合法产物。")
    if degs:
        result["limitations"].append("存在输入读取降级，详见 degradations。")

    ensure_dir(output_dir)
    write_json(os.path.join(output_dir, "result.json"), result)
    write_text(os.path.join(output_dir, "candidates.md"), render_candidates_md(result))
    return result


def render_candidates_md(result):
    lines = [
        "# 候选文献（可编辑复核清单）",
        "",
        "> 本文件由 result.json 确定性渲染；作为示意输出，不替代正式结果。",
        "",
        f"- case_id: `{result.get('case_id')}`",
        f"- status: `{result.get('status')}`",
        f"- research_question: {result.get('research_question') or '（未识别）'}",
        "",
        "| rank | 标题 | 第一作者 | 年份 | 期刊/来源 | DOI | score | 相关性依据(evidence) |",
        "|-|-|-|-|-|-|-|-|",
    ]
    for r in result.get("results") or []:
        title = str(r.get("title") or "（无标题）").replace("|", "\\|")[:90]
        authors = r.get("authors") or []
        first = (authors[0] if authors else r.get("journal") or "-")
        first = str(first).replace("|", "\\|")[:24]
        year = r.get("year") or "-"
        jour = str(r.get("journal") or r.get("database") or "-").replace("|", "\\|")[:28]
        doi = r.get("doi") or ""
        doi_txt = f"`{doi}`" if doi else "-"
        score = r.get("score")
        score_txt = f"{score}" if score is not None else "-"
        ev = r.get("evidence") or []
        ev_txt = f"{len(ev)} 条" if ev else "-"
        lines.append(f"| {r.get('rank')} | {title} | {first} | {year} | {jour} | {doi_txt} | {score_txt} | {ev_txt} |")
    lines += [
        "",
        f"共 `{len(result.get('results') or [])}` 条去重后候选。",
        "",
        "## 状态说明",
    ]
    for n in result.get("status_notes") or []:
        lines.append(f"- {n}")
    lines += [
        "",
        "## 证据记录",
    ]
    for e in result.get("evidence") or []:
        q = str(e.get("query") or "").replace("\n", " ")[:120]
        lines.append(f"- [{e.get('kind')}] `{q}` — source={e.get('source')}, db={e.get('database')}")
    if not (result.get("evidence") or []):
        lines.append("- （无）")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# manifest：中间产物内容指纹（可复核/可复现）
# --------------------------------------------------------------------------
def write_manifest(work_dir, output_dir):
    artifacts = ["case_summary.json", "plan.json", "records.json", "evidence.json",
                 "rubric.json", "import_state.json"]
    entries = []
    for name in artifacts:
        p = os.path.join(work_dir, name)
        if os.path.isfile(p):
            h, n = file_sha256(p)
            entries.append({"file": os.path.join("work", name), "sha256": h,
                            "bytes": n, "at": now_iso()})
    out_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(out_path):
        h, n = file_sha256(out_path)
        entries.append({"file": "output/result.json", "sha256": h, "bytes": n,
                        "at": now_iso()})
    manifest = {
        "manifest_id": "manifest-" + sha256_hex(json.dumps(entries, sort_keys=True))[:12],
        "generated_at": now_iso(),
        "entries": entries,
    }
    ensure_dir(output_dir)
    write_json(os.path.join(output_dir, "manifest.json"), manifest)
    return manifest


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def selfcheck(output_dir, work_dir):
    result, deg = read_json(os.path.join(output_dir, "result.json"), {})
    checks = []
    def add(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    add("result.json 存在且为合法 JSON", not deg, "; ".join(deg) if deg else "ok")
    add("case_id 非空", bool(result.get("case_id")))
    add("status 为合法枚举",
        result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"))
    add("results 为列表", isinstance(result.get("results"), list))
    add("evidence 为列表", isinstance(result.get("evidence"), list))
    add("sources 为列表", isinstance(result.get("sources"), list))
    add("uncertainty 为对象", isinstance(result.get("uncertainty"), dict))
    add("pending_human 为列表", isinstance(result.get("pending_human"), list))
    add("change_log 为列表", isinstance(result.get("change_log"), list))
    add("每条结果有 record_id",
        all(isinstance(r, dict) and r.get("record_id") for r in result.get("results", [])))
    add("每条结果有 title 或来源",
        all(isinstance(r, dict) and (r.get("title") or r.get("database"))
            for r in result.get("results", [])))

    summary = {"total": len(checks), "passed": sum(1 for c in checks if c["pass"]),
               "checks": checks, "conclusion": "PASS" if all(c["pass"] for c in checks) else "FAIL"}
    write_json(os.path.join(work_dir, "selfcheck.json"), summary)
    return summary


def run_skeleton(input_dir, output_dir, work_dir):
    """骨架流水线：解析 -> 规划 -> 组装，立即落盘 partial 结果（不检索）。"""
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    # 初始化全部中间产物，避免骨架阶段出现 missing 降级，并保证重复运行状态一致
    for name, default in (("records.json", []), ("evidence.json", []),
                          ("import_state.json", []), ("change_log.json", [])):
        p = os.path.join(work_dir, name)
        if not os.path.isfile(p):
            write_json(p, default)
    load_rubric(work_dir)

    state_path = os.path.join(work_dir, "state.json")
    state, _ = read_json(state_path, {})
    if not state.get("started_ts"):
        state["started_ts"] = time.time()
        state["started_at"] = now_iso()
    write_json(state_path, state)

    summary = parse_case(input_dir, work_dir)
    plan = build_plan(summary)
    write_json(os.path.join(work_dir, "plan.json"), plan)

    has_input = bool(summary.get("case_path") or summary.get("task_path"))
    log_change(work_dir, "run", "骨架流水线（parse->plan->assemble，未检索）",
               "partial" if has_input else "input_error")

    result = assemble(input_dir, output_dir, work_dir)
    write_manifest(work_dir, output_dir)
    return result