#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 核心实现（纯标准库），由 scripts/main.py 导入调用。

确定性、不编造、增量改进三原则的实现都集中在此：
  * result.json 只输出带证据（locator+quote）的候选，缺证据进入 pending_human；
  * 缺材料写 insufficient_evidence，从不示例填充；
  * skeleton 先落盘合法 partial 骨架，之后仅增量更新。
"""
import datetime
import hashlib
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024

CONTRACT_KEYS = (
    "output_contract", "outputContract", "output", "result_schema", "schema",
    "required_output", "required_fields", "expected_fields", "contract",
)

DATABASE_TRUST = {
    "crossref": 0.90, "pubmed": 0.90, "europepmc": 0.88, "arxiv": 0.85,
    "semantic scholar": 0.85, "semanticscholar": 0.85, "openalex": 0.88,
    "doi": 0.92, "publisher": 0.92, "dblp": 0.85, "scholar": 0.80,
    "web": 0.60, "unknown": 0.50, "local": 0.70,
}

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")


# --------------------------------------------------------------------------
# 稳健 IO
# --------------------------------------------------------------------------
def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


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
        data = open(path, "rb").read()
    except OSError as e:
        return "", ["read_failed:%s:%s" % (path, str(e)[:80])]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), []
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), ["fallback_decode:" + path]


def read_json(path, default=None, max_bytes=MAX_FILE_BYTES):
    if default is None:
        default = {}
    text, deg = read_text(path, max_bytes)
    if deg:
        return default, deg
    if not text.strip():
        return default, ["empty:" + path]
    try:
        return json.loads(text), []
    except Exception as e:
        return default, ["bad_json:%s:%s" % (path, str(e)[:120])]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("write_text failed: %s %s" % (path, str(e)[:200]), file=sys.stderr)
        return False


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("write_json failed: %s %s" % (path, str(e)[:200]), file=sys.stderr)
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


def parse_case(input_dir, work_dir):
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    degradations = []

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    degradations += d
    task_text, d = read_text(task_path) if task_path else ("", ["no_task_md"])
    degradations += d

    case_id = None
    for k in ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId"):
        v = case_data.get(k)
        if v not in (None, ""):
            case_id = str(v)
            break
    if not case_id:
        m = re.search(r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        case_id = ("case:" + os.path.splitext(os.path.basename(case_path))[0]) if case_path else "unknown"

    research_question = None
    for k in ("research_question", "researchQuestion", "question", "query", "topic",
              "task", "problem", "research_topic", "research_problem", "title"):
        v = case_data.get(k)
        if isinstance(v, str) and v.strip():
            research_question = v.strip()
            break
    if not research_question:
        m = re.search(r"(?:研究问题|科研问题|研究主题|Research question|Research topic)\s*[:：]\s*(.+)", task_text)
        if m:
            research_question = m.group(1).strip()[:2000]
    if not research_question:
        for line in task_text.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "-", ">", "|", "*", "`")):
                research_question = s[:2000]
                break

    contract = {}
    for k in CONTRACT_KEYS:
        v = case_data.get(k)
        if v is not None:
            contract[k] = v
    if isinstance(case_data.get("output_contract"), dict):
        contract = dict(case_data.get("output_contract"))
        contract["_source_key"] = "output_contract"

    materials = [f for f in files if "/materials/" in f.replace("\\", "/") or f.replace("\\", "/").startswith("materials/")]

    summary = {
        "case_id": case_id,
        "research_question": research_question,
        "case_path": case_path,
        "task_path": task_path,
        "input_files": files,
        "materials": materials,
        "output_contract": contract,
        "task_excerpt": task_text[:3000],
        "case_keys": sorted(case_data.keys()),
        "parsed_at": now_iso(),
        "degradations": degradations,
    }
    ensure_dir(work_dir)
    write_json(os.path.join(work_dir, "case_summary.json"), summary)
    write_json(os.path.join(work_dir, "input_degradations.json"), degradations)
    return summary


# --------------------------------------------------------------------------
# 查询规划
# --------------------------------------------------------------------------
def make_plan(summary):
    rq = summary.get("research_question") or ""
    contract = summary.get("output_contract") or {}
    return {
        "research_question": rq,
        "generated_at": now_iso(),
        "query_ladder": [
            {"step": 1, "type": "concept_seed", "desc": "从研究问题抽取核心概念词（对象/方法/指标），单概念召回"},
            {"step": 2, "type": "keyword_expansion", "desc": "对每个概念做同义词/缩写/跨语言/上位词扩展，逐项核验并记录扩展依据"},
            {"step": 3, "type": "and_combination", "desc": "核心概念 AND 组合，控制召回精度；记录每次查询命中数与相关性"},
            {"step": 4, "type": "citation_chain", "desc": "对高相关种子文献做引文/被引追踪，补充关键遗漏"},
        ],
        "databases": ["crossref", "europepmc", "arxiv", "openalex"],
        "budget": {"total_seconds": 900, "model_requests": 64},
        "milestone": {"first_3_minutes": "落盘 status=partial 的 result.json 骨架，之后仅增量改进"},
        "output_contract_keys": sorted(contract.keys()) if isinstance(contract, dict) else [],
    }


# --------------------------------------------------------------------------
# 记录规范化与去重
# --------------------------------------------------------------------------
def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def normalize_title(t):
    s = _clean(t).lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def normalize_doi(d):
    if not d:
        return ""
    s = _clean(d).lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    return s.strip().strip(".,;")


def record_key(rec):
    doi = normalize_doi(rec.get("doi"))
    if doi:
        return ("doi:" + doi)
    t = normalize_title(rec.get("title"))
    if t:
        return ("title:" + t)
    return None


def short_id(rec):
    key = record_key(rec) or json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        return [x.strip() for x in re.split(r"[,;、]", s) if x.strip()]
    return [v]


def _to_float(v, default=0.0):
    try:
        f = float(v)
        if f != f:
            return default
        return max(0.0, min(1.0, f)) if default is not None and 0.0 <= default <= 1.0 else f
    except (TypeError, ValueError):
        return default


def _norm_evidence(ev):
    if not isinstance(ev, dict):
        ev = {}
    return {
        "locator": _clean(ev.get("locator") or ev.get("location")),
        "quote": _clean(ev.get("quote") or ev.get("text")),
        "source": _clean(ev.get("source")),
    }


def merge_record(old, new):
    def quality(r):
        q = 0
        if _clean(r.get("title")):
            q += 2
        if _as_list(r.get("authors")):
            q += 1
        if r.get("year"):
            q += 1
        if _clean(r.get("journal")):
            q += 1
        if normalize_doi(r.get("doi")):
            q += 2
        if _clean(r.get("url")):
            q += 1
        ev = r.get("evidence") or {}
        if (ev.get("locator") or "").strip() and (ev.get("quote") or "").strip():
            q += 2
        if r.get("meta_verified"):
            q += 1
        return q
    base = dict(old)
    if quality(new) > quality(old):
        base = dict(new)
    for f in ("title", "authors", "year", "journal", "doi", "url", "database",
              "abstract", "relevance_reason", "evidence", "meta_verified", "meta_confidence"):
        if not base.get(f) and new.get(f):
            base[f] = new[f]
    if not base.get("database"):
        base["database"] = "unknown"
    return base


def _validate_record(norm):
    problems = []
    if not norm.get("title"):
        problems.append("missing_title")
    if not norm.get("doi") and not norm.get("url"):
        problems.append("missing_source")
    ev = norm.get("evidence") or {}
    if not (ev.get("locator") or "").strip():
        problems.append("missing_evidence_locator")
    if not (ev.get("quote") or "").strip():
        problems.append("missing_evidence_quote")
    if norm.get("meta_verified") is False:
        problems.append("unverified_metadata")
    if norm.get("year") in (None, "", 0):
        problems.append("missing_year")
    if not norm.get("authors"):
        problems.append("missing_authors")
    if not norm.get("journal"):
        problems.append("missing_journal")
    return problems


# --------------------------------------------------------------------------
# import / evidence / uncertainty
# --------------------------------------------------------------------------
def load_work_json(work_dir, name, default):
    path = os.path.join(work_dir, name)
    data, _deg = read_json(path, default)
    return data, path


def cmd_import(args):
    work_dir = args.work
    ensure_dir(work_dir)
    records, path = load_work_json(work_dir, "records.json", {"records": []})
    if not isinstance(records.get("records"), list):
        records = {"records": []}

    if getattr(args, "inline", None):
        incoming = json.loads(args.inline)
    else:
        incoming, _deg = read_json(args.file, [])
    if isinstance(incoming, dict):
        incoming = incoming.get("records", incoming.get("results", [incoming]))
    if not isinstance(incoming, list):
        incoming = []

    imported = 0
    for rec in incoming:
        if not isinstance(rec, dict):
            continue
        title = _clean(rec.get("title"))
        if not title and not normalize_doi(rec.get("doi")):
            continue
        key = record_key(rec)
        idx = None
        for i, old in enumerate(records["records"]):
            if record_key(old) == key and key is not None:
                idx = i
                break
        norm = {
            "title": title,
            "authors": _as_list(rec.get("authors")),
            "year": rec.get("year"),
            "journal": _clean(rec.get("journal")),
            "doi": normalize_doi(rec.get("doi")) or None,
            "url": _clean(rec.get("url")),
            "database": _clean(rec.get("database")) or "unknown",
            "abstract": _clean(rec.get("abstract")),
            "relevance": _to_float(rec.get("relevance"), 0.5),
            "relevance_reason": _clean(rec.get("relevance_reason")),
            "score": _to_float(rec.get("score"), 0.0),
            "evidence": _norm_evidence(rec.get("evidence")),
            "meta_verified": bool(rec.get("meta_verified", False)),
            "meta_confidence": _to_float(rec.get("meta_confidence"), 0.5),
            "source_trust": _to_float(rec.get("source_trust"), None) if rec.get("source_trust") is not None else None,
            "record_id": short_id(rec),
        }
        norm["_validation"] = _validate_record(norm)
        if idx is not None:
            records["records"][idx] = merge_record(records["records"][idx], norm)
        else:
            records["records"].append(norm)
        imported += 1

    write_json(path, records)
    log_change(work_dir, "import", "导入候选 %d 条" % imported)
    print("imported=%d total=%d" % (imported, len(records["records"])))
    return records


def cmd_evidence(args):
    work_dir = args.work
    ensure_dir(work_dir)
    entries, path = load_work_json(work_dir, "evidence.json", [])
    if not isinstance(entries, list):
        entries = []
    obj = json.loads(args.inline)
    if not isinstance(obj, dict):
        print("evidence 需要 JSON object", file=sys.stderr)
        return entries
    obj.setdefault("timestamp", now_iso())
    obj.setdefault("kind", "trace")
    entries.append(obj)
    write_json(path, entries)
    log_change(work_dir, "evidence", "追加证据 %s" % (obj.get("kind") or "trace"))
    print("evidence_total=%d" % len(entries))
    return entries


def cmd_uncertainty(args):
    work_dir = args.work
    ensure_dir(work_dir)
    u, path = load_work_json(work_dir, "uncertainty.json",
                             {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []})
    obj = json.loads(args.inline)
    if not isinstance(obj, dict):
        print("uncertainty 需要 JSON object", file=sys.stderr)
        return u
    for field in ("missing_metadata", "conflicts", "coverage_gaps", "notes"):
        if field in obj:
            val = obj[field]
            if isinstance(val, list):
                u.setdefault(field, []).extend(val)
            else:
                u.setdefault(field, []).append(val)
    write_json(path, u)
    log_change(work_dir, "uncertainty", "追加不确定性条目")
    print("uncertainty_updated fields=%s" % ",".join(sorted(obj.keys())))
    return u


# --------------------------------------------------------------------------
# assemble：去重 + 打分 + 证据闸门 + 排序 + 写结果
# --------------------------------------------------------------------------
def meta_completeness(rec):
    fields = 0.0
    if rec.get("authors"):
        fields += 1.0
    if rec.get("year"):
        fields += 1.0
    if rec.get("journal"):
        fields += 1.0
    if rec.get("doi"):
        fields += 1.0
    if rec.get("url"):
        fields += 1.0
    weight = 0.5 if rec.get("meta_verified") is False else 1.0
    return (fields / 5.0) * weight


def source_trust(rec):
    st = rec.get("source_trust")
    if st is not None:
        return float(st)
    db = (rec.get("database") or "unknown").lower()
    return DATABASE_TRUST.get(db, 0.50)


def final_score(rec):
    rel = _to_float(rec.get("relevance"), 0.5)
    s = 0.6 * rel + 0.2 * meta_completeness(rec) + 0.2 * source_trust(rec)
    return round(min(1.0, max(0.0, s)), 4)


def _has_evidence(rec):
    ev = rec.get("evidence") or {}
    return bool((ev.get("locator") or "").strip() and (ev.get("quote") or "").strip())


def build_result(input_dir, output_dir, work_dir):
    ensure_dir(work_dir)
    ensure_dir(output_dir)

    summary_path = os.path.join(work_dir, "case_summary.json")
    if not os.path.isfile(summary_path):
        summary = parse_case(input_dir, work_dir)
    else:
        summary, _ = read_json(summary_path, {})

    plan_path = os.path.join(work_dir, "plan.json")
    if not os.path.isfile(plan_path):
        plan = make_plan(summary)
        write_json(plan_path, plan)
    else:
        plan, _ = read_json(plan_path, {})

    records, _ = load_work_json(work_dir, "records.json", {"records": []})
    evidence, _ = load_work_json(work_dir, "evidence.json", [])
    uncertainty, _ = load_work_json(work_dir, "uncertainty.json",
                                    {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []})
    change_log, _ = load_work_json(work_dir, "change_log.json", [])
    input_deg, _ = load_work_json(work_dir, "input_degradations.json", [])
    errors = list(input_deg) if isinstance(input_deg, list) else []

    start_marker = os.path.join(work_dir, "start_time.txt")
    start_ts = read_text(start_marker)[0].strip() or None
    runtime = {
        "started_at": start_ts or summary.get("parsed_at"),
        "finished_at": now_iso(),
        "seconds_elapsed": None,
    }
    if start_ts:
        try:
            dt0 = datetime.datetime.strptime(start_ts, "%Y-%m-%dT%H:%M:%S%z")
            dt1 = datetime.datetime.now(datetime.timezone.utc)
            runtime["seconds_elapsed"] = round((dt1 - dt0).total_seconds(), 1)
        except Exception:
            pass

    rec_list = records.get("records", []) if isinstance(records, dict) else []

    dedup = {}
    order = []
    for rec in rec_list:
        key = record_key(rec)
        if key is None:
            continue
        if key not in dedup:
            dedup[key] = rec
            order.append(key)
        else:
            dedup[key] = merge_record(dedup[key], rec)

    results = []
    pending_human = []
    uncertainty_missing = list(uncertainty.get("missing_metadata", []))
    seen_missing = set()

    for key in order:
        rec = dedup[key]
        problems = rec.get("_validation", _validate_record(rec))
        rankable = _has_evidence(rec) and rec.get("title") and (rec.get("doi") or rec.get("url"))
        rec = {k: v for k, v in rec.items() if not k.startswith("_")}
        if rankable:
            rec["source_trust"] = source_trust(rec)
            rec["score"] = final_score(rec)
            rec["verification_status"] = "verified" if (
                rec.get("meta_verified") and not any(p in problems for p in
                ("missing_evidence_locator", "missing_evidence_quote"))) else "partial"
            results.append(rec)
        else:
            reason = []
            if not rec.get("title"):
                reason.append("missing_title")
            if not (rec.get("doi") or rec.get("url")):
                reason.append("missing_source")
            if not _has_evidence(rec):
                reason.append("missing_evidence")
            rec["pending_reason"] = reason
            rec["verification_status"] = "unverified"
            pending_human.append(rec)
        for p in problems:
            if p.startswith("missing_") and p not in seen_missing:
                seen_missing.add(p)
                uncertainty_missing.append({
                    "field": p.replace("missing_", ""),
                    "record_id": rec.get("record_id"),
                    "title": rec.get("title"),
                    "note": "元数据或证据缺失，未擅自补全",
                })

    results.sort(key=lambda r: (-(r.get("score") or 0), -(r.get("year") or 0), r.get("title") or ""))
    for i, r in enumerate(results, 1):
        r["rank"] = i

    evidence_out = [e for e in evidence if isinstance(e, dict)]
    for r in results:
        ev = r.get("evidence") or {}
        if ev.get("locator") and ev.get("quote"):
            evidence_out.append({
                "kind": "relevance",
                "record_id": r.get("record_id"),
                "title": r.get("title"),
                "source": ev.get("source") or r.get("database"),
                "database": r.get("database"),
                "locator": ev.get("locator"),
                "quote": ev.get("quote"),
                "timestamp": now_iso(),
            })

    sources = _build_sources(evidence_out, results)

    uncertainty_out = {
        "missing_metadata": _dedup_uncertainty(uncertainty_missing),
        "conflicts": uncertainty.get("conflicts", []),
        "coverage_gaps": uncertainty.get("coverage_gaps", []),
        "verification": _build_verification(results, pending_human),
        "notes": uncertainty.get("notes", []),
    }

    limitations = []
    if pending_human:
        limitations.append("有 %d 条候选因缺标题/来源/证据被移入 pending_human，未作为正式结果输出。" % len(pending_human))
    if uncertainty_out["missing_metadata"]:
        limitations.append("部分结果元数据不完整，已逐字段列入 uncertainty.missing_metadata。")
    if uncertainty_out["coverage_gaps"]:
        limitations.append("检索覆盖存在缺口，详见 uncertainty.coverage_gaps。")
    if not rec_list:
        limitations.append("未导入任何候选文献。")

    status = _decide_status(results, pending_human, rec_list, errors)

    status_notes = []
    if status == "insufficient_evidence":
        status_notes.append("未获得可用证据，未编造任何文献。")
    if status == "partial":
        status_notes.append("已产出可用结果，但仍有未完成或未核验部分，详见 limitations/uncertainty。")
    if errors:
        status_notes.append("存在 %d 条输入/IO 降级，详见 errors/degradations。" % len(errors))

    contract = summary.get("output_contract") or {}
    contract_out = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            if k.startswith("_"):
                continue
            contract_out[k] = v
    for k in CONTRACT_KEYS:
        contract_out.setdefault(k, None)

    result = {
        "case_id": summary.get("case_id"),
        "status": status,
        "research_question": summary.get("research_question"),
        "search_scope": plan.get("query_ladder") if isinstance(plan, dict) else [],
        "results": results,
        "evidence": evidence_out,
        "sources": sources,
        "uncertainty": uncertainty_out,
        "limitations": limitations,
        "pending_human": pending_human,
        "errors": errors,
        "degradations": errors,
        "status_notes": status_notes,
        "change_log": change_log if isinstance(change_log, list) else [],
        "runtime": runtime,
    }
    result.update(contract_out)

    write_json(os.path.join(output_dir, "result.json"), result)
    write_text(os.path.join(output_dir, "candidates.md"), render_candidates_md(result))
    log_change(work_dir, "assemble",
               "组装 result.json（results=%d, pending=%d）" % (len(results), len(pending_human)),
               status=status)
    return result


def _dedup_uncertainty(items):
    seen = set()
    out = []
    for it in items:
        k = json.dumps(it, ensure_ascii=False, sort_keys=True, default=str)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def _build_verification(results, pending):
    dbs = {}
    for r in results + pending:
        db = (r.get("database") or "unknown").lower()
        dbs.setdefault(db, {"database": db, "trust": DATABASE_TRUST.get(db, 0.50),
                            "records": 0, "verified": 0})
        dbs[db]["records"] += 1
        if r.get("verification_status") == "verified":
            dbs[db]["verified"] += 1
    return {"per_database": sorted(dbs.values(), key=lambda x: -x["records"])}


def _build_sources(evidence, results):
    seen = set()
    out = []
    for e in evidence:
        if not isinstance(e, dict):
            continue
        name = e.get("source") or e.get("database")
        if not name:
            continue
        key = str(name).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "source": name,
            "database": e.get("database"),
            "kind": e.get("kind"),
            "url": e.get("url") or e.get("locator"),
        })
    for r in results:
        db = (r.get("database") or "").lower()
        if db and db not in seen:
            seen.add(db)
            out.append({"source": r.get("database"), "database": db, "kind": "record", "url": r.get("url")})
    return out


def _decide_status(results, pending, rec_list, errors):
    if errors and not rec_list:
        return "input_error"
    if not results:
        return "partial" if pending else "insufficient_evidence"
    return "partial" if pending else "ok"


# --------------------------------------------------------------------------
# candidates.md 渲染
# --------------------------------------------------------------------------
def render_candidates_md(result):
    lines = [
        "# 候选文献清单（可编辑复核）",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- 生成时间: %s" % (result.get("runtime", {}).get("finished_at") or ""),
        "",
        "## 排序规则",
        "",
        "score = 0.6·relevance + 0.2·元数据完整度 + 0.2·来源可靠度；同分按年份、标题排序。",
        "",
        "## 结果",
        "",
    ]
    for r in result.get("results", []):
        authors = ", ".join(r.get("authors") or []) or "作者不详"
        lines.append("### %d. %s" % (r.get("rank"), r.get("title") or "无标题"))
        lines.append("")
        lines.append("- 作者: %s" % authors)
        lines.append("- 年份: %s　期刊: %s" % (r.get("year") or "不详", r.get("journal") or "不详"))
        lines.append("- DOI: `%s`　URL: %s" % (r.get("doi") or "—", r.get("url") or "—"))
        lines.append("- 数据库: %s　相关性: %s　score: %s　核验: %s" % (
            r.get("database"), r.get("relevance"), r.get("score"), r.get("verification_status")))
        lines.append("- 相关性依据: %s" % (r.get("relevance_reason") or "—"))
        ev = r.get("evidence") or {}
        lines.append("- 证据定位: %s" % (ev.get("locator") or "—"))
        lines.append("- 逐字引用: `%s`" % (ev.get("quote") or "—"))
        lines.append("")
    if result.get("pending_human"):
        lines.append("## 待人工复核（未达证据闸门，未作为正式结果）")
        lines.append("")
        for r in result["pending_human"]:
            lines.append("- %s（原因: %s）" % (r.get("title") or "无标题", "、".join(r.get("pending_reason") or [])))
        lines.append("")
    lines.append("## 不确定性与遗漏")
    lines.append("")
    lines.append("- coverage_gaps: %s" % json.dumps(result.get("uncertainty", {}).get("coverage_gaps", []), ensure_ascii=False))
    lines.append("- limitations: %s" % json.dumps(result.get("limitations", []), ensure_ascii=False))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# skeleton / run / selfcheck
# --------------------------------------------------------------------------
def cmd_skeleton(args):
    ensure_dir(args.work)
    ensure_dir(args.output)
    write_text(os.path.join(args.work, "start_time.txt"), now_iso() + "\n")
    summary = parse_case(args.input, args.work)
    write_json(os.path.join(args.work, "plan.json"), make_plan(summary))
    skeleton = {
        "case_id": summary.get("case_id"),
        "status": "partial",
        "research_question": summary.get("research_question"),
        "search_scope": [],
        "results": [],
        "evidence": [],
        "sources": [],
        "uncertainty": {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "verification": {}, "notes": []},
        "limitations": ["骨架阶段：尚未检索，仅完成输入解析与查询规划。"],
        "pending_human": [],
        "errors": summary.get("degradations", []),
        "degradations": summary.get("degradations", []),
        "status_notes": ["早期骨架，后续步骤将增量更新。"],
        "change_log": log_change(args.work, "skeleton", "落盘 partial 骨架", "partial"),
        "runtime": {"started_at": now_iso(), "finished_at": None, "seconds_elapsed": None},
    }
    write_json(os.path.join(args.output, "result.json"), skeleton)
    write_text(os.path.join(args.output, "candidates.md"), render_candidates_md(skeleton))
    print("skeleton_written case_id=%s" % summary.get("case_id"))
    return skeleton


def cmd_run(args):
    result = cmd_skeleton(args)
    print("run_done status=%s" % result.get("status"))
    return result


def cmd_selfcheck(args):
    result, _ = read_json(os.path.join(args.output, "result.json"), {})
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    chk("result.json 存在", os.path.isfile(os.path.join(args.output, "result.json")))
    chk("case_id 非空", bool((result.get("case_id") or "").strip()))
    chk("status 合法", result.get("status") in VALID_STATUS, str(result.get("status")))
    chk("results 为列表", isinstance(result.get("results"), list))
    chk("sources 为列表", isinstance(result.get("sources"), list))
    chk("uncertainty 存在", isinstance(result.get("uncertainty"), dict))
    chk("change_log 为列表", isinstance(result.get("change_log"), list))
    chk("pending_human 存在", isinstance(result.get("pending_human"), list))
    chk("limitations 存在", isinstance(result.get("limitations"), list))

    ev_ok = True
    for r in result.get("results", []):
        ev = r.get("evidence") or {}
        if not ((ev.get("locator") or "").strip() and (ev.get("quote") or "").strip()):
            ev_ok = False
        if not r.get("title"):
            ev_ok = False
        if not (r.get("doi") or r.get("url")):
            ev_ok = False
    chk("证据闸门：results 每条均有 locator+quote+来源", ev_ok)

    print("selfcheck results=%d pending=%d" % (len(result.get("results", [])), len(result.get("pending_human", []))))
    ok_all = all(c["ok"] for c in checks)
    for c in checks:
        print("[%s] %s %s" % ("PASS" if c["ok"] else "FAIL", c["name"], c["detail"]))
    print("SELFCHECK %s" % ("PASS" if ok_all else "FAIL"))
    return 0


# --------------------------------------------------------------------------
# 顶层 dispatch（由 scripts/main.py 调用）
# --------------------------------------------------------------------------
def dispatch(args):
    cmd = args.cmd
    if cmd in ("run", "skeleton"):
        cmd_run(args) if cmd == "run" else cmd_skeleton(args)
    elif cmd == "parse":
        summary = parse_case(args.input, args.work)
        print("case_id=%s question=%s" % (summary.get("case_id"), (summary.get("research_question") or "")[:60]))
    elif cmd == "plan":
        summary, _ = read_json(os.path.join(args.work, "case_summary.json"), {})
        if not summary:
            summary = parse_case(args.input, args.work)
        write_json(os.path.join(args.work, "plan.json"), make_plan(summary))
        print("plan_written")
    elif cmd == "import":
        cmd_import(args)
    elif cmd == "evidence":
        cmd_evidence(args)
    elif cmd == "uncertainty":
        cmd_uncertainty(args)
    elif cmd == "assemble":
        result = build_result(args.input, args.output, args.work)
        print("assemble_done status=%s results=%d pending=%d" % (
            result["status"], len(result["results"]), len(result["pending_human"])))
    elif cmd == "selfcheck":
        return cmd_selfcheck(args)
    return 0