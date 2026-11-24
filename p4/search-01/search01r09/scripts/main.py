#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可追溯学术文献检索 Skill 的确定性辅助脚本（纯标准库）。

子命令：
  run         骨架流水线：parse -> plan ->（空态）-> assemble(partial)
  skeleton    仅写一版合法 partial 骨架（不解析/不规划）
  parse       解析 /input -> work/case_summary.json
  plan        由 case_summary 生成查询计划 -> work/plan.json
  import      导入候选文献 -> work/records.json（按 DOI/规范化标题 upsert）
  evidence    追加查询/来源证据 -> work/evidence.json
  assemble    去重+核验+打分+排序，写 output/result.json 与 candidates.md
  selfcheck   校验 result.json（默认退出 0；--strict 时 FAIL 退出 1）
"""

import argparse
import datetime
import json
import os
import re
import sys
import unicodedata

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024

STOPWORDS = set(
    """a an the and or of in on for to with without by from at as is are was were be
    been has have had had this that these those it its their using via among between
    within upon through new based study studies research method methods approach
    approaches analysis review systematic meta""".split()
)

CONTRACT_KEY_NAMES = (
    "output_contract", "outputContract", "output", "result_schema", "schema",
    "required_output", "required_fields", "expected_fields", "contract",
)

METADATA_FIELDS = ("title", "authors", "year", "journal", "doi")

SOURCE_TIER = {
    "crossref": 1.0, "europepmc": 1.0, "europe_pmc": 1.0, "pubmed": 1.0,
    "publisher": 0.95, "doi": 0.95, "doi.org": 0.95,
    "arxiv": 0.9, "arxiv_abs": 0.9, "biorxiv": 0.9, "medrxiv": 0.9,
    "openalex": 0.85, "semantic_scholar": 0.85, "semanticscholar": 0.85,
    "paper_search": 0.8,
    "web_search": 0.7, "web": 0.6,
    "materials": 0.7, "pdf": 0.7, "notes": 0.6,
}
DEFAULT_RELIABILITY = 0.6

# ---------------------------------------------------------------- helpers


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(
        timespec="seconds")


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
        return "", ["read_failed:" + str(path) + ":" + str(e)[:80]]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), []
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), [
        "fallback_decode:" + str(path)]


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
        return default, ["bad_json:" + str(path) + ":" + str(e)[:120]]


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


def norm_title(title):
    s = str(title or "")
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[\W_]+", " ", s, flags=re.UNICODE)
    return " ".join(s.split())


def norm_doi(doi):
    if not doi:
        return None
    s = str(doi).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = s.strip("/").strip(".")
    s = re.sub(r"[;,.]+$", "", s)
    return s or None


def norm_authors(authors):
    if authors is None:
        return []
    if isinstance(authors, str):
        parts = [p.strip() for p in re.split(r"[;,]", authors) if p.strip()]
        return parts
    if isinstance(authors, list):
        return [str(x).strip() for x in authors if str(x).strip()]
    return []


# ---------------------------------------------------------------- discovery


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


# ---------------------------------------------------------------- contract


def extract_contract(case_data):
    """返回 (keys, spec)。支持 dict/list/str 三种 contract 形态。"""
    spec = None
    for k in CONTRACT_KEY_NAMES:
        v = case_data.get(k)
        if v not in (None, "", [], {}):
            spec = v
            break
    keys = []
    if isinstance(spec, dict):
        keys = list(spec.keys())
    elif isinstance(spec, list):
        for it in spec:
            if isinstance(it, str):
                keys.append(it)
            elif isinstance(it, dict):
                keys.append(it.get("name") or it.get("field") or it.get("key"))
    elif isinstance(spec, str) and spec.strip():
        keys = [spec.strip()]
    keys = [str(k) for k in keys if k]
    return keys, spec


def ensure_contract_fields(result, keys, spec):
    for k in keys:
        default = None
        if isinstance(spec, dict):
            v = spec.get(k)
            if isinstance(v, dict):
                if str(v.get("type", "")).lower() in ("list", "array") or \
                        isinstance(v.get("items"), dict):
                    default = []
                elif str(v.get("type", "")).lower() in ("object", "dict"):
                    default = {}
            elif isinstance(v, list):
                default = v if v else []
        if k not in result:
            result[k] = default


# ---------------------------------------------------------------- parse


def parse_case(input_dir, work_dir):
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    degradations = []
    errors = []

    if case_path:
        case_data, d = read_json(case_path, {})
    else:
        case_data, d = {}, ["no_case_json"]
    degradations += d
    if not isinstance(case_data, dict):
        case_data = {}
        errors.append("case_json_not_object")

    task_text = ""
    if task_path:
        task_text, d = read_text(task_path)
        degradations += d
    else:
        degradations.append("no_task_md")

    if case_path is None and task_path is None:
        errors.append("no_input_files")

    case_id = None
    for k in ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId"):
        v = case_data.get(k)
        if v not in (None, ""):
            case_id = str(v)
            break
    if not case_id:
        m = re.search(
            r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        case_id = ("case:" + os.path.splitext(os.path.basename(case_path))[0]) \
            if case_path else "unknown"

    research_question = None
    for k in ("research_question", "researchQuestion", "question", "query",
              "topic", "task", "problem", "research_topic", "title"):
        v = case_data.get(k)
        if isinstance(v, str) and v.strip():
            research_question = v.strip()
            break
    if not research_question:
        m = re.search(
            r"(?:研究问题|科研问题|研究主题|Research question|Research topic)\s*[:：]\s*(.+)",
            task_text)
        if m:
            research_question = m.group(1).strip()[:2000]
    if not research_question:
        for line in task_text.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "-", "|", "`", ">", "*")):
                research_question = s[:2000]
                break

    contract_keys, contract_spec = extract_contract(case_data)

    materials = [f for f in files if f not in (case_path, task_path)]

    summary = {
        "case_id": case_id,
        "research_question": research_question,
        "contract_keys": contract_keys,
        "contract_spec": contract_spec,
        "case_path": case_path,
        "task_path": task_path,
        "input_files": files,
        "materials": materials,
        "degradations": degradations,
        "errors": errors,
        "parsed_at": now_iso(),
    }
    write_json(os.path.join(work_dir, "case_summary.json"), summary)
    return summary


# ---------------------------------------------------------------- plan


def plan_queries(research_question):
    rq = str(research_question or "")
    tokens = [
        w for w in re.findall(r"[A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff\-\.]*", rq)
        if len(w) >= 3 and w.lower() not in STOPWORDS
    ]
    seeds = [t for t in tokens if not any(c.isdigit() for c in t)][:6]
    anchor = ""
    if seeds:
        anchor = " AND ".join(seeds[:3])
    return [
        {
            "round": 1,
            "kind": "concept_seed",
            "queries": seeds,
            "purpose": "用核心概念词逐个召回，先建立候选池",
        },
        {
            "round": 2,
            "kind": "synonym_abbrev",
            "queries": [t + " OR " + t.upper() for t in seeds[:3]],
            "purpose": "补充同义词、缩写、跨语言/上位下位表达，提升 Recall",
        },
        {
            "round": 3,
            "kind": "and_combination",
            "queries": [anchor] if anchor else [],
            "purpose": "AND 组合收紧主题，控制无关结果",
        },
        {
            "round": 4,
            "kind": "citation_chain",
            "queries": [],
            "purpose": "从高相关记录的被引/引文追踪关键文献",
        },
    ]


def do_plan(work_dir, summary):
    rq = summary.get("research_question") or ""
    plan = {
        "research_question": rq,
        "rounds": plan_queries(rq),
        "keyword_expansion_checklist": [
            "专业术语 / 缩写 / 全称", "同义词与近义表达", "上下位概念与宽窄检索",
            "跨语种表达（中/英）", "方法名 / 模型名 / 数据集名",
        ],
        "note": "本清单为确定性规划提示；实际关键词扩展与查询由 Agent 在检索中执行并记录到 evidence。",
        "created_at": now_iso(),
    }
    write_json(os.path.join(work_dir, "plan.json"), plan)
    return plan


# ---------------------------------------------------------------- state


def ensure_state(work_dir):
    for fn, default in (("records.json", []), ("evidence.json", [])):
        p = os.path.join(work_dir, fn)
        if not os.path.isfile(p):
            write_json(p, default)


def load_state(work_dir):
    records, _ = read_json(os.path.join(work_dir, "records.json"), [])
    evidence, _ = read_json(os.path.join(work_dir, "evidence.json"), [])
    change_log, _ = read_json(os.path.join(work_dir, "change_log.json"), [])
    if not isinstance(records, list):
        records = []
    if not isinstance(evidence, list):
        evidence = []
    if not isinstance(change_log, list):
        change_log = []
    return records, evidence, change_log


# ---------------------------------------------------------------- import


def load_candidates_from_text(text):
    try:
        obj = json.loads(text)
    except Exception:
        return [], ["bad_candidates_json"]
    if isinstance(obj, list):
        return obj, []
    if isinstance(obj, dict):
        for k in ("results", "candidates", "records", "items", "data"):
            if isinstance(obj.get(k), list):
                return obj[k], []
        return [], ["no_candidate_list"]
    return [], ["unexpected_candidates_root"]


def same_evidence(a, b):
    return (str(a.get("locator")) == str(b.get("locator")) and
            str(a.get("quote")) == str(b.get("quote")))


def merge_record(old, new):
    m = dict(old)
    for f in list(METADATA_FIELDS) + ["url", "database", "relevance",
                                      "relevance_reason", "source"]:
        ov, nv = m.get(f), new.get(f)
        if ov in (None, "", []) and nv not in (None, "", []):
            m[f] = nv
    ev = list(m.get("evidence") or [])
    for e in (new.get("evidence") or []):
        if isinstance(e, dict) and not any(same_evidence(x, e) for x in ev):
            ev.append(e)
    m["evidence"] = ev
    merged = list(m.get("merged_from") or []) + [new.get("title")]
    if merged:
        m["merged_from"] = merged
    return m


def import_candidates(work_dir, file_path):
    if not file_path or not os.path.isfile(file_path):
        return 0, ["candidates_file_missing:" + str(file_path)]
    text, deg = read_text(file_path)
    candidates, err = load_candidates_from_text(text)
    records, _, _ = load_state(work_dir)
    skipped = []
    for c in candidates:
        if not isinstance(c, dict) or not str(c.get("title") or "").strip():
            skipped.append("no_title")
            continue
        rec = dict(c)
        rec["authors"] = norm_authors(rec.get("authors"))
        rec["doi"] = norm_doi(rec.get("doi"))
        key_doi = rec.get("doi")
        key_title = norm_title(rec.get("title"))
        matched = False
        for i, old in enumerate(records):
            old_doi = norm_doi(old.get("doi"))
            if key_doi and old_doi and key_doi == old_doi:
                records[i] = merge_record(old, rec)
                matched = True
                break
            if key_title and norm_title(old.get("title")) == key_title:
                records[i] = merge_record(old, rec)
                matched = True
                break
        if not matched:
            rec["score"] = None
            records.append(rec)
    write_json(os.path.join(work_dir, "records.json"), records)
    return len(candidates) - len(skipped), skipped + deg + err


# ---------------------------------------------------------------- evidence


def add_evidence(work_dir, entry):
    if not isinstance(entry, dict):
        return 0, ["bad_evidence_entry"]
    required = ("query", "source")
    if not all(entry.get(k) for k in required):
        return 0, ["evidence_missing_query_or_source"]
    entry.setdefault("timestamp", now_iso())
    entry.setdefault("database", entry.get("source"))
    entry.setdefault("kind", "query")
    entry.setdefault("rationale", "")
    entry.setdefault("scope", "")
    evidence, _ = read_json(os.path.join(work_dir, "evidence.json"), [])
    if not isinstance(evidence, list):
        evidence = []
    evidence.append(entry)
    write_json(os.path.join(work_dir, "evidence.json"), evidence)
    return len(evidence), []


# ---------------------------------------------------------------- scoring


def meta_completeness(rec):
    present = 0
    for f in METADATA_FIELDS:
        v = rec.get(f)
        if f == "authors":
            if v and isinstance(v, list) and any(str(x).strip() for x in v):
                present += 1
        elif v not in (None, "", [], {}):
            present += 1
    return present / float(len(METADATA_FIELDS))


def source_reliability(rec):
    db = str(rec.get("database") or "").lower().strip()
    src = str(rec.get("source") or "").lower().strip()
    for k in (db, src):
        if k in SOURCE_TIER:
            return SOURCE_TIER[k]
    for k, v in SOURCE_TIER.items():
        if k and (k in db or k in src):
            return v
    return DEFAULT_RELIABILITY


def score_record(rec):
    rel = rec.get("relevance")
    if not isinstance(rel, (int, float)):
        rel = 0.5
    rel = max(0.0, min(1.0, float(rel)))
    meta = meta_completeness(rec)
    sr = source_reliability(rec)
    score = round(0.6 * rel + 0.2 * meta + 0.2 * sr, 4)
    return {"score": score, "metadata_completeness": round(meta, 4),
            "source_reliability": sr, "relevance": rel}


# ---------------------------------------------------------------- assemble


def determine_status(summary, records, errors):
    if "no_input_files" in errors:
        return "input_error"
    if not records:
        return "insufficient_evidence"
    if errors:
        return "partial"
    return "ok"


def render_candidates(records):
    lines = ["# 候选文献清单", "",
             "> 由 result.json 渲染，供人工复核与编辑。",
             "",
             "| # | score | title | authors | year | journal | doi | 相关依据 |",
             "|-|-|-|-|-|-|-|-|"]
    for r in records:
        authors = ", ".join(str(a) for a in (r.get("authors") or [])[:3])
        if len(r.get("authors") or []) > 3:
            authors += " et al."
        reason = str(r.get("relevance_reason") or "").replace("|", "\\|")
        reason = (reason[:120] + "…") if len(reason) > 120 else reason
        line = "| {rank} | {score} | {title} | {au} | {y} | {j} | {d} | {r} |".format(
            rank=r.get("rank"), score=r.get("score"), title=str(r.get("title") or "").replace("|", "\\|"),
            au=authors, y=r.get("year") or "", j=str(r.get("journal") or "").replace("|", "\\|"),
            d=r.get("doi") or "", r=reason)
        lines.append(line)
    return "\n".join(lines) + "\n"


def assemble(input_dir, output_dir, work_dir, status_override=None):
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    summary, _ = read_json(os.path.join(work_dir, "case_summary.json"), {})
    if not isinstance(summary, dict):
        summary = {}
    records, evidence, change_log = load_state(work_dir)

    # 去重已由 import 完成；此处对 records 打分排序
    scored = []
    missing = []
    conflicts = []
    for rec in records:
        if not isinstance(rec, dict) or not str(rec.get("title") or "").strip():
            continue
        s = score_record(rec)
        rec["relevance"] = s["relevance"]
        rec["score"] = s["score"]
        rec["metadata_completeness"] = s["metadata_completeness"]
        rec["source_reliability"] = s["source_reliability"]
        for f in METADATA_FIELDS:
            v = rec.get(f)
            empty = v in (None, "", [])
            if f in ("title", "authors") and isinstance(v, list) and v:
                empty = False
            if empty:
                missing.append({"field": f, "title": rec.get("title")})
        scored.append(rec)

    def sort_key(rec):
        return (rec.get("score") or 0, rec.get("year") or 0,
                str(rec.get("title") or ""))

    scored.sort(key=sort_key, reverse=True)
    for i, rec in enumerate(scored, 1):
        rec["rank"] = i

    errors = list((summary.get("errors") or []))
    degradations = list((summary.get("degradations") or []))
    status = status_override or determine_status(summary, scored, errors)

    sources = []
    seen_src = set()
    for e in evidence:
        if not isinstance(e, dict):
            continue
        name = str(e.get("source") or "")
        if name and name not in seen_src:
            seen_src.add(name)
            sources.append({
                "name": name,
                "database": e.get("database"),
                "kind": e.get("kind"),
                "first_seen": e.get("timestamp"),
            })

    notes = []
    if status == "partial" and not scored and not evidence:
        notes.append("骨架已落盘，检索尚未开始。")
    if status == "insufficient_evidence":
        notes.append("未获得可核验候选文献；详见 limitations 与 coverage_gaps。")
    if status_override and status != determine_status(summary, scored, errors):
        notes.append("status 由 --status 显式指定为 " + status + "。")

    uncertainty = {
        "missing_metadata": missing[:50],
        "conflicts": conflicts,
        "coverage_gaps": [],
        "notes": [],
    }

    result = {
        "case_id": summary.get("case_id") or "unknown",
        "status": status,
        "research_question": summary.get("research_question"),
        "search_scope": {
            "task": "围绕研究问题检索开放学术文献，保留筛选与排序依据",
            "materials_used": (summary.get("materials") or [])[:50],
        },
        "results": scored,
        "evidence": evidence,
        "sources": sources,
        "status_notes": notes,
        "uncertainty": uncertainty,
        "pending_human": [],
        "limitations": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": change_log,
        "runtime": {
            "assembled_at": now_iso(),
            "record_count": len(scored),
            "evidence_count": len(evidence),
        },
    }

    ensure_contract_fields(result, summary.get("contract_keys") or [],
                           summary.get("contract_spec"))

    write_json(os.path.join(output_dir, "result.json"), result)
    write_text(os.path.join(output_dir, "candidates.md"),
               render_candidates(scored))
    return result


# ---------------------------------------------------------------- skeleton


def skeleton(work_dir, output_dir, status="partial"):
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    ensure_state(work_dir)
    result = {
        "case_id": None,
        "status": status,
        "research_question": None,
        "search_scope": {},
        "results": [],
        "evidence": [],
        "sources": [],
        "status_notes": ["骨架已落盘，检索尚未开始。"],
        "uncertainty": {"missing_metadata": [], "conflicts": [],
                        "coverage_gaps": [], "notes": []},
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": load_state(work_dir)[2],
        "runtime": {"assembled_at": now_iso(), "record_count": 0,
                    "evidence_count": 0},
    }
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


def run_pipeline(input_dir, output_dir, work_dir):
    ensure_dir(work_dir)
    log_change(work_dir, "run.start", "骨架流水线启动", "partial")
    skeleton(work_dir, output_dir)  # 先行落盘，保证中途失败仍有合法产物
    summary = parse_case(input_dir, work_dir)
    log_change(work_dir, "parse.done",
               "case_id=%s, contract_keys=%d" % (
                   summary.get("case_id"), len(summary.get("contract_keys") or [])),
               "partial")
    do_plan(work_dir, summary)
    ensure_state(work_dir)
    result = assemble(input_dir, output_dir, work_dir,
                      status_override="partial")
    return result


# ---------------------------------------------------------------- selfcheck


def selfcheck(output_dir, input_dir=None, work_dir=None):
    failures = []
    ok = []
    res_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(res_path):
        return ["result_json_missing"], ok

    result, deg = read_json(res_path, None)
    if deg:
        failures.append("result_json_unreadable:" + ";".join(deg))
        return failures, ok
    if not isinstance(result, dict):
        return ["result_not_object"], ok

    def chk(name, cond, detail=""):
        if cond:
            ok.append(name)
        else:
            failures.append(name + ((": " + detail) if detail else ""))

    chk("case_id", bool(result.get("case_id")))
    chk("status_valid", result.get("status") in
        ("ok", "partial", "insufficient_evidence", "input_error"),
        str(result.get("status")))
    chk("results_list", isinstance(result.get("results"), list))
    chk("evidence_list", isinstance(result.get("evidence"), list))
    chk("sources_list", isinstance(result.get("sources"), list))
    chk("uncertainty_dict", isinstance(result.get("uncertainty"), dict))
    chk("change_log_list", isinstance(result.get("change_log"), list))
    chk("pending_human_present", "pending_human" in result)
    chk("runtime_present", "runtime" in result)

    for i, r in enumerate(result.get("results") or []):
        ev = r.get("evidence") or []
        has_loc_quote = any(isinstance(e, dict) and e.get("locator") and
                            e.get("quote") for e in ev)
        chk("result_%d_evidence" % i, bool(has_loc_quote),
            "每条结果需含 locator+quote")
        chk("result_%d_source" % i,
            bool((r.get("doi") or r.get("url") or r.get("database"))),
            "每条结果需含 DOI/URL/来源")

    # 契约字段回填校验（需要重新解析输入）
    if input_dir and work_dir:
        summary2, _ = read_json(os.path.join(work_dir, "case_summary.json"), {})
        if isinstance(summary2, dict) and summary2.get("contract_keys"):
            missing = [k for k in summary2["contract_keys"] if k not in result]
            chk("contract_fields_backfilled", not missing,
                "缺 " + ",".join(missing))
    return failures, ok


# ---------------------------------------------------------------- argparse


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="可追溯学术文献检索 Skill 确定性辅助脚本")
    sub = p.add_subparsers(dest="cmd")

    for name in ("run", "skeleton", "assemble", "selfcheck"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default=DEFAULT_INPUT)
        sp.add_argument("--output", default=DEFAULT_OUTPUT)
        sp.add_argument("--work", default=DEFAULT_WORK)
        if name == "assemble":
            sp.add_argument("--status", choices=(
                "ok", "partial", "insufficient_evidence", "input_error"))
        if name == "selfcheck":
            sp.add_argument("--strict", action="store_true")

    sp = sub.add_parser("parse")
    sp.add_argument("--input", default=DEFAULT_INPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)

    sp = sub.add_parser("plan")
    sp.add_argument("--input", default=DEFAULT_INPUT)
    sp.add_argument("--work", default=DEFAULT_WORK)

    sp = sub.add_parser("import")
    sp.add_argument("--file", required=True)
    sp.add_argument("--work", default=DEFAULT_WORK)

    sp = sub.add_parser("evidence")
    sp.add_argument("--inline", required=True)
    sp.add_argument("--work", default=DEFAULT_WORK)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 0

    if args.cmd == "run":
        run_pipeline(args.input, args.output, args.work)
        print("run: skeleton+parse+plan+assemble 完成 ->",
              os.path.join(args.output, "result.json"))
    elif args.cmd == "skeleton":
        skeleton(args.work, args.output)
        print("skeleton 完成 ->", os.path.join(args.output, "result.json"))
    elif args.cmd == "parse":
        s = parse_case(args.input, args.work)
        print("parse 完成: case_id=%s, files=%d" % (
            s.get("case_id"), len(s.get("input_files") or [])))
    elif args.cmd == "plan":
        s, _ = read_json(os.path.join(args.work, "case_summary.json"), {})
        if not isinstance(s, dict):
            s = parse_case(args.input, args.work)
        do_plan(args.work, s)
        print("plan 完成 ->", os.path.join(args.work, "plan.json"))
    elif args.cmd == "import":
        n, info = import_candidates(args.work, args.file)
        print("import 完成: 导入 %d 条, 备注 %s" % (n, json.dumps(info)[:200]))
    elif args.cmd == "evidence":
        try:
            entry = json.loads(args.inline)
        except Exception as e:
            print("evidence --inline 不是合法 JSON:", e, file=sys.stderr)
            return 2
        n, info = add_evidence(args.work, entry)
        print("evidence 完成: 共 %d 条, 备注 %s" % (n, json.dumps(info)[:200]))
    elif args.cmd == "assemble":
        assemble(args.input, args.output, args.work,
                 status_override=getattr(args, "status", None))
        print("assemble 完成 ->", os.path.join(args.output, "result.json"))
    elif args.cmd == "selfcheck":
        failures, ok = selfcheck(args.output, args.input, args.work)
        for line in ok:
            print("PASS:", line)
        for line in failures:
            print("FAIL:", line)
        if getattr(args, "strict", False) and failures:
            return 1
        print("selfcheck: %d pass, %d fail" % (len(ok), len(failures)))
    return 0


if __name__ == "__main__":
    sys.exit(main())