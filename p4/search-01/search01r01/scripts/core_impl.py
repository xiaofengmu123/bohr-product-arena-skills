#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl —— 学术文献检索 Skill 的确定性状态机与组装逻辑（纯标准库）。

本模块不联网检索；检索由 Agent 通过 paper_search / web_search 完成，
脚本只做确定性的状态管理与组装，保证任何中途失败都能留下合法 result.json。

职责：
  稳健 IO 与输入发现
  parse    : 解析 /input 生成 case_summary.json（含 output_contract 字段名）
  plan     : 生成查询计划脚手架 plan.json
  import   : 候选文献 upsert 到 records.json（按 DOI / 规范化标题去重）
  evidence : 追加查询/来源证据到 evidence.json
  assemble : 去重 + 打分 + 排序 + 组装 result.json / candidates.md / evidence_map.json
  selfcheck: 校验 result.json 是否满足输出契约
"""
import json
import math
import os
import re
import sys
import time

VERSION = "1.1.0"
MAX_FILE_BYTES = 2 * 1024 * 1024

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 题目输出契约的可能键名（不假设固定键名，先发现再假设）
CONTRACT_KEY_NAMES = (
    "output_contract", "outputContract", "output_schema", "result_schema",
    "schema", "contract", "required_output", "required_fields",
    "expected_fields", "output",
)

# result.json 内置强制字段（共同满足题目“检索结果/证据记录/状态说明”三要求）
REQUIRED_FIELDS = (
    "case_id", "status", "research_question", "search_scope",
    "results", "evidence", "evidence_map", "sources", "uncertainty",
    "status_notes", "change_log", "limitations", "errors",
    "degradations", "pending_human", "runtime",
)

META_FIELDS = ("title", "authors", "year", "journal", "doi")

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")


# ---------------- 通用工具 ----------------
def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def now_iso():
    lt = time.localtime()
    off = time.strftime("%z", lt) or "+0000"
    return time.strftime("%Y-%m-%dT%H:%M:%S", lt) + off


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
        return default, ["bad_json:" + str(path) + ":" + str(e)[:120]]


def _sanitize(obj):
    """把 NaN/Inf 等非法 JSON 值替换为 None，保证可序列化。"""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 6)
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
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
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_sanitize(obj), f, ensure_ascii=False, indent=2,
                      allow_nan=False, default=str)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError) as e:
        print("write_json 失败:", path, str(e)[:200], file=sys.stderr)
        return False


def read_work_json(work_dir, name, default):
    if isinstance(default, list):
        return read_json(os.path.join(work_dir, name), [])
    if isinstance(default, dict):
        return read_json(os.path.join(work_dir, name), {})
    val, _ = read_json(os.path.join(work_dir, name), default)
    return val


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


# ---------------- 输入发现 ----------------
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


def find_materials(files, case_path=None, task_path=None):
    skip = {case_path, task_path}
    out = []
    for f in files:
        if f in skip:
            continue
        b = os.path.basename(f).lower()
        if b.endswith((".pdf", ".txt", ".md", ".json", ".xml", ".csv")):
            out.append(f)
    return out


# ---------------- output_contract 字段发现 ----------------
def _fields_from(value):
    out = []
    if isinstance(value, dict):
        if isinstance(value.get("properties"), dict):
            out += [str(x) for x in value["properties"].keys()]
        if isinstance(value.get("required"), list):
            out += [str(x) for x in value["required"]]
        for k in ("fields", "keys"):
            if isinstance(value.get(k), dict):
                out += [str(x) for x in value[k].keys()]
            elif isinstance(value.get(k), list):
                out += [str(x) for x in value[k]]
    elif isinstance(value, list):
        out += [str(x) for x in value]
    return out


def extract_contract_fields(case_data):
    names = []
    if isinstance(case_data, dict):
        for key, val in case_data.items():
            kl = str(key).lower().replace("-", "_").replace(" ", "_")
            if kl in CONTRACT_KEY_NAMES:
                names += _fields_from(val)
    # 若只给了一个 {字段: 说明} 的映射，键本身就是字段名
    if isinstance(case_data, dict):
        for key in CONTRACT_KEY_NAMES:
            if key in case_data and isinstance(case_data[key], dict):
                sub = case_data[key]
                if not _fields_from(sub) and "properties" not in sub and "required" not in sub:
                    names += [str(x) for x in sub.keys()]
    return sorted(set(n for n in names if n))


# ---------------- parse ----------------
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
        m = re.search(r"(?:case_id|challenge_id|armiId)\s*[:：]\s*`?([A-Za-z0-9_\-.:]+)", task_text)
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
        m = re.search(
            r"(?:研究问题|科研问题|研究主题|Research question|Research topic)\s*[:：]\s*(.+)",
            task_text)
        if m:
            research_question = m.group(1).strip()[:2000]
    if not research_question:
        for line in task_text.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "-", "*", "`", "|")):
                research_question = s[:2000]
                break

    constraints = {}
    for k in ("constraints", "budget", "requirements", "scope"):
        v = case_data.get(k)
        if isinstance(v, (dict, list, str)):
            constraints[k] = v

    contract_fields = extract_contract_fields(case_data)
    summary = {
        "case_id": case_id,
        "research_question": research_question,
        "input_dir": input_dir,
        "case_path": case_path,
        "task_path": task_path,
        "materials": find_materials(files, case_path, task_path),
        "contract_fields": contract_fields,
        "constraints": constraints,
        "started_at": now_iso(),
        "version": VERSION,
    }
    ensure_dir(work_dir)
    write_json(os.path.join(work_dir, "case_summary.json"), summary)
    write_json(os.path.join(work_dir, "degradations.json"), degradations)
    log_change(work_dir, "parse", "解析输入并提取研究问题与 output_contract 字段", "partial")
    return summary


# ---------------- plan ----------------
def build_plan(work_dir):
    summary, _ = read_json(os.path.join(work_dir, "case_summary.json"), {})
    rq = summary.get("research_question") or ""
    # 粗糙分词仅为脚手架提示；真实同义词/缩写由 Agent 在检索中补充并记录。
    tokens = [t for t in re.split(r"[\s,，;；:：()（）\[\]【】]+", rq) if len(t) > 1]
    plan = {
        "research_question": rq,
        "auto_hint_tokens": tokens[:20],
        "rounds": [
            {"round": 1, "type": "single_concept", "purpose": "单概念召回，锁定核心术语",
             "keywords": [], "expansions": [], "status": "pending"},
            {"round": 2, "type": "concept_combination", "purpose": "多概念 AND 组合，提高精度",
             "keywords": [], "expansions": [], "status": "pending"},
            {"round": 3, "type": "citation_backtrack", "purpose": "从关键综述/种子论文回溯引用",
             "keywords": [], "expansions": [], "status": "pending"},
        ],
        "keyword_expansion_checklist": [
            "专业术语 / 同义词", "缩写与全称", "跨语言表达（中英）", "近义/上位/下位概念",
            "方法名异写", "研究对象别名", "排除词（控制无关结果）"
        ],
        "coverage_checklist": [
            "是否覆盖主要研究方向", "是否命中关键综述", "是否覆盖种子论文的引用与被引",
            "是否存在明显漏检（coverage_gaps）"
        ],
        "note": "本文件为脚手架；Agent 须在检索后把 keywords/expansions/status 落盘，作为证据。",
    }
    write_json(os.path.join(work_dir, "plan.json"), plan)
    log_change(work_dir, "plan", "生成多轮查询计划脚手架与关键词扩展清单", "partial")
    return plan


# ---------------- 规范化 / 去重 / 打分 ----------------
def normalize_title(s):
    if not s:
        return ""
    s = str(s).lower()
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_doi(s):
    if not s:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def record_key(r):
    doi = normalize_doi(r.get("doi") or r.get("DOI") or "")
    if doi:
        return "doi:" + doi
    title = normalize_title(r.get("title"))
    if title:
        return "title:" + title
    return None


def _norm_evidence(e):
    if not isinstance(e, dict):
        return json.dumps({"raw": str(e)}, ensure_ascii=False, sort_keys=True)
    return json.dumps(e, ensure_ascii=False, sort_keys=True, default=str)


def _normalize_record(raw):
    if not isinstance(raw, dict):
        return None
    r = dict(raw)
    r.pop("_id", None)
    # authors: string -> list
    if isinstance(r.get("authors"), str):
        txt = r["authors"].strip()
        if not txt:
            r["authors"] = []
        elif ";" in txt:
            r["authors"] = [a.strip() for a in txt.split(";") if a.strip()]
        else:
            r["authors"] = [txt]
    if "authors" in r and not isinstance(r["authors"], list):
        r["authors"] = [str(r["authors"])]
    # evidence list
    ev = r.get("evidence")
    if ev is None:
        ev = []
    if isinstance(ev, dict):
        ev = [ev]
    if not isinstance(ev, list):
        ev = []
    out_ev = []
    for e in ev:
        if isinstance(e, dict):
            e.setdefault("kind", "evidence")
            e.setdefault("locator", None)
            e.setdefault("quote", None)
            out_ev.append(e)
    # 便捷字段：relevant_locator / relevant_quote -> 一条 relevance 证据
    if r.get("relevant_locator") or r.get("relevant_quote"):
        out_ev.insert(0, {
            "kind": "relevance",
            "locator": r.get("relevant_locator"),
            "quote": r.get("relevant_quote"),
        })
    r["evidence"] = out_ev
    return r


def merge_record(existing, new):
    for k, v in new.items():
        if k == "evidence":
            continue
        if v not in (None, "", []):
            existing[k] = v
    cur = list(existing.get("evidence") or [])
    have = {_norm_evidence(x) for x in cur}
    for e in (new.get("evidence") or []):
        if _norm_evidence(e) not in have:
            cur.append(e)
            have.add(_norm_evidence(e))
    existing["evidence"] = cur
    return existing


def import_records(work_dir, payload):
    """payload 为单个记录 dict 或记录 list。按 DOI/标题 upsert，返回记录总数。"""
    path = os.path.join(work_dir, "records.json")
    records, _ = read_json(path, [])
    if not isinstance(records, list):
        records = []
    items = payload if isinstance(payload, list) else [payload]
    idx = {}
    for rec in records:
        k = record_key(rec)
        if k:
            idx[k] = rec
    appended = 0
    for raw in items:
        rec = _normalize_record(raw)
        if not rec:
            continue
        k = record_key(rec)
        if k and k in idx:
            idx[k] = merge_record(idx[k], rec)
        else:
            if k:
                idx[k] = rec
            records.append(rec)
            idx[k] = rec
            appended += 1
    write_json(path, records)
    log_change(work_dir, "import", "导入 %d 条候选（新增 %d 条，按 DOI/标题去重合并）"
               % (len(items), appended), "partial")
    return len(records)


def metadata_completeness(r):
    n = 0
    for f in META_FIELDS:
        v = r.get(f)
        if isinstance(v, list):
            ok = len(v) > 0
        else:
            ok = v not in (None, "")
        if ok:
            n += 1
    return round(n / len(META_FIELDS), 4)


def source_reliability(db):
    d = (db or "").lower()
    if "crossref" in d:
        return 1.0
    if "europe pmc" in d or "europepmc" in d:
        return 0.95
    if "pubmed" in d:
        return 0.95
    if "openalex" in d:
        return 0.9
    if "publisher" in d or "doi.org" in d:
        return 0.9
    if "semantic" in d or "arxiv" in d:
        return 0.85
    return 0.7


def relevance_of(r):
    v = r.get("relevance")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        x = float(v)
        if 0 <= x <= 1:
            return round(x, 4)
    return None


def score_record(r):
    rel = relevance_of(r)
    if rel is None:
        rel = 0.0
    meta = metadata_completeness(r)
    src = source_reliability(r.get("database") or r.get("source") or "")
    return round(0.6 * rel + 0.2 * meta + 0.2 * src, 4), rel


# ---------------- evidence ----------------
def add_evidence(work_dir, payload):
    if not isinstance(payload, dict):
        payload = {}
    path = os.path.join(work_dir, "evidence.json")
    entries, _ = read_json(path, [])
    if not isinstance(entries, list):
        entries = []
    e = {
        "kind": payload.get("kind") or "query",
        "query": payload.get("query"),
        "source": payload.get("source"),
        "database": payload.get("database"),
        "locator": payload.get("locator"),
        "quote": payload.get("quote"),
        "rationale": payload.get("rationale"),
        "timestamp": now_iso(),
    }
    entries.append(e)
    write_json(path, entries)
    return entries


# ---------------- assemble ----------------
def _load_work(work_dir):
    summary, sdeg = read_json(os.path.join(work_dir, "case_summary.json"), {})
    records, rdeg = read_json(os.path.join(work_dir, "records.json"), [])
    evid, edeg = read_json(os.path.join(work_dir, "evidence.json"), [])
    deg, ddeg = read_json(os.path.join(work_dir, "degradations.json"), [])
    changes, _ = read_json(os.path.join(work_dir, "change_log.json"), [])
    plan, _ = read_json(os.path.join(work_dir, "plan.json"), {})
    if not isinstance(records, list):
        records = []
    if not isinstance(evid, list):
        evid = []
    if not isinstance(deg, list):
        deg = []
    if not isinstance(changes, list):
        changes = []
    return summary, records, evid, deg + sdeg + rdeg + edeg + ddeg, changes, plan


def _build_evidence_map(records, evid, uncertainty, result_links):
    """把查询证据、逐条记录证据、去重/冲突/遗漏证据收敛为 evidence_map。

    每条证据都含 locator + 逐字 quote（缺失则标 None 并进入 missing_metadata），
    并通过 supports 反向链接到它支撑的结论（result#k / research_question 等）。
    """
    emap = {}
    counter = 1

    def add(kind, claim, locator, quote, source=None, database=None,
            rationale=None, supports=None, timestamp=None):
        nonlocal counter
        eid = "EV%04d" % counter
        counter += 1
        emap[eid] = {
            "kind": kind,
            "claim": claim,
            "locator": locator,
            "quote": quote,
            "source": source,
            "database": database,
            "rationale": rationale,
            "supports": supports or [],
            "timestamp": timestamp or now_iso(),
        }
        return eid

    # 1) 查询证据
    for e in evid:
        add("query", "检索查询与来源记录", e.get("locator"), e.get("quote"),
            e.get("source"), e.get("database"), e.get("rationale"),
            ["research_question", "search_scope"], e.get("timestamp"))

    # 2) 每条记录的证据 + 去重标记
    for i, r in enumerate(records, 1):
        tag = "result#%d" % i if i in result_links else ("record:%s" %
               (normalize_doi(r.get("doi")) or normalize_title(r.get("title")) or str(i)))
        for e in (r.get("evidence") or []):
            if not isinstance(e, dict):
                continue
            add(e.get("kind") or "evidence",
                "支撑候选文献“%s”" % ((r.get("title") or "?")[:60]),
                e.get("locator"), e.get("quote"),
                r.get("database"), r.get("database"), r.get("relevance_reason"),
                [tag], None)

    # 3) 冲突证据
    for c in (uncertainty.get("conflicts") or []):
        add("conflict", "来源冲突记录", c.get("locator"), c.get("quote"),
            c.get("source"), c.get("database"), c.get("note"), ["uncertainty"])

    # 4) 覆盖缺口证据
    for g in (uncertainty.get("coverage_gaps") or []):
        add("gap", "覆盖缺口说明", g.get("locator"), g.get("quote"),
            None, None, g.get("note"), ["uncertainty"])

    return emap


def _runtime(summary, work_dir):
    started = None
    for f in ("case_summary.json", "change_log.json"):
        p = os.path.join(work_dir, f)
        if os.path.isfile(p):
            try:
                obj = json.load(open(p, encoding="utf-8-sig"))
            except Exception:
                continue
            if f == "case_summary.json" and obj.get("started_at"):
                started = obj["started_at"]
                break
            if f == "change_log.json" and isinstance(obj, list) and obj:
                started = obj[0].get("time")
                break
    return {
        "started_at": started or (summary.get("started_at") or now_iso()),
        "updated_at": now_iso(),
        "elapsed_seconds": None,
        "script_version": VERSION,
    }


def _collect_sources(records, evid):
    seen = {}
    for e in evid:
        key = (e.get("database") or e.get("source") or "unknown")
        if key not in seen:
            seen[key] = {"name": key, "type": "search", "database": e.get("database"),
                         "note": e.get("rationale"), "access_count": 0}
        seen[key]["access_count"] += 1
    for r in records:
        db = r.get("database") or "unknown"
        if db not in seen:
            seen[db] = {"name": db, "type": "record", "database": db,
                        "note": "候选记录来源", "access_count": 0}
        seen[db]["access_count"] += 1
    return list(seen.values())


def assemble(input_dir, output_dir, work_dir):
    ensure_dir(output_dir)
    summary, records, evid, degradations, changes, plan = _load_work(work_dir)

    results = []
    missing_meta = []
    missing_evidence = []

    # 去重（防御式再查一遍）
    dedup = {}
    order = []
    for r in records:
        k = record_key(r)
        if not k:
            order.append((len(order), r))
            continue
        if k not in dedup:
            dedup[k] = len(order)
            order.append((len(order), r))
        else:
            dedup[k] = dedup[k]
    final_records = [r for _, r in order]

    scored = []
    for r in final_records:
        if not isinstance(r, dict):
            continue
        s, rel = score_record(r)
        if rel is None:
            missing_meta.append((r.get("title"), "relevance"))
        if not (r.get("evidence")):
            missing_evidence.append(r.get("title"))
        for f in META_FIELDS:
            v = r.get(f)
            empty = (v in (None, "")) or (isinstance(v, list) and not v)
            if empty:
                missing_meta.append((r.get("title") or "?title", f))
        scored.append((r, s))
    scored.sort(key=lambda t: (-t[1], normalize_title(t[0].get("title"))))

    result_links = {}
    uncertainty = {
        "missing_metadata": [{"record": t, "field": f} for t, f in missing_meta],
        "conflicts": [],
        "coverage_gaps": [],
        "notes": [],
    }

    for i, (r, s) in enumerate(scored, 1):
        result_links[i] = True
        results.append({
            "rank": i,
            "title": r.get("title"),
            "authors": r.get("authors") or [],
            "year": r.get("year"),
            "journal": r.get("journal"),
            "doi": r.get("doi"),
            "url": r.get("url"),
            "database": r.get("database"),
            "relevance": r.get("relevance"),
            "relevance_reason": r.get("relevance_reason"),
            "score": s,
            "evidence": r.get("evidence") or [],
        })

    status = "ok"
    status_notes = []
    if not results:
        # 空输入/无材料 -> insufficient_evidence；输入存在但坏 JSON/超大 -> input_error
        bad_input = any(("bad_json" in d) or ("size_exceeded" in d) or
                        ("fallback_decode" in d) for d in degradations)
        has_input = bool(summary.get("case_path") or summary.get("task_path")
                         or summary.get("materials"))
        status = "input_error" if (bad_input and has_input) else "insufficient_evidence"
        status_notes.append("候选文献为空，未检索或材料不足；不补造文献。")
    elif missing_evidence or missing_meta:
        status = "partial"
        if missing_evidence:
            status_notes.append("%d 条候选缺少 locator+quote 证据" % len(missing_evidence))
        if missing_meta:
            status_notes.append("%d 处元数据缺失，已列入 uncertainty.missing_metadata"
                                % len(missing_meta))

    evidence_map = _build_evidence_map(final_records, evid, uncertainty, result_links)
    # 回链 evidence_map 的 id 到每条 result
    eid_by_tag = {}
    for eid, e in evidence_map.items():
        for sup in e["supports"]:
            eid_by_tag.setdefault(sup, []).append(eid)
    for res in results:
        res["evidence_ids"] = eid_by_tag.get("result#%d" % res["rank"], [])

    runtime = _runtime(summary, work_dir)
    result = {
        "case_id": summary.get("case_id"),
        "status": status,
        "research_question": summary.get("research_question"),
        "search_scope": {"input_dir": input_dir,
                         "databases": sorted({(r.get("database") or "") for r in final_records})},
        "results": results,
        "evidence": evid,
        "evidence_map": evidence_map,
        "sources": _collect_sources(final_records, evid),
        "uncertainty": uncertainty,
        "status_notes": status_notes,
        "change_log": changes,
        "limitations": list(dict.fromkeys(degradations)),
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": runtime,
    }

    # 题目 output_contract 字段原样回填（缺值 null，不改名不删键）
    for name in (summary.get("contract_fields") or []):
        if name not in result:
            result[name] = None

    # 检索结果列表可能被指定为 contract 字段之一；确保指向主产物
    for alias in ("candidates", "papers", "articles", "retrieved"):
        if alias in result and result[alias] is None:
            result[alias] = results

    result_path = os.path.join(output_dir, "result.json")
    ok = write_json(result_path, result)
    if not ok:
        # 写盘失败：仍尽力写最小合法 JSON
        write_text(result_path, json.dumps({"case_id": summary.get("case_id"),
                                            "status": "input_error",
                                            "results": [], "errors": ["result write failed"]},
                                           ensure_ascii=False, indent=2))

    write_json(os.path.join(output_dir, "evidence_map.json"), evidence_map)
    write_text(os.path.join(output_dir, "candidates.md"), render_candidates(result))
    write_json(os.path.join(work_dir, "last_result.json"), result)
    log_change(work_dir, "assemble", "组装 result.json / candidates.md / evidence_map.json", status)
    return result


def render_candidates(result):
    lines = ["# 候选文献清单（可编辑，供人工复核）", "",
             "- case_id: `%s`" % (result.get("case_id")),
             "- status: `%s`" % (result.get("status")),
             "- 研究问题: %s" % (result.get("research_question") or "（未提供）"), ""]
    for r in result.get("results") or []:
        lines.append("## %d. %s" % (r.get("rank"), r.get("title") or "（标题缺失）"))
        lines.append("- 作者: %s" % (", ".join(r.get("authors") or []) or "未报告"))
        lines.append("- 年份/期刊: %s / %s" % (r.get("year") or "?", r.get("journal") or "?"))
        lines.append("- DOI: %s" % (r.get("doi") or "未报告"))
        lines.append("- 来源: %s | 得分: %s | 相关性: %s"
                     % (r.get("database") or "?", r.get("score"), r.get("relevance")))
        if r.get("relevance_reason"):
            lines.append("- 相关性依据: %s" % r["relevance_reason"])
        for e in (r.get("evidence") or []):
            lines.append("  - 证据 [%s] locator=%s quote=%r"
                         % (e.get("kind"), e.get("locator"), e.get("quote")))
        lines.append("")
    if not (result.get("results")):
        lines.append("（空：无候选文献 — 诚实留空，不补造。）")
    return "\n".join(lines) + "\n"


# ---------------- selfcheck ----------------
def selfcheck(output_dir, work_dir, strict=False):
    checks = []
    issues = []

    def check(cond, name, detail):
        checks.append({"check": name, "detail": detail, "pass": bool(cond)})
        if not cond:
            issues.append(name)

    result_path = os.path.join(output_dir, "result.json")
    result, deg = read_json(result_path, None)
    check(result is not None and isinstance(result, dict), "result_json_exists",
          "result.json 存在且为合法 JSON；%s" % (deg or "ok"))
    if not isinstance(result, dict):
        result = {}

    check(bool(result.get("case_id")), "case_id", "case_id 非空")
    check(result.get("status") in ALLOWED_STATUS, "status_valid",
          "status ∈ %s" % (ALLOWED_STATUS,))
    check(isinstance(result.get("results"), list), "results_list", "results 为列表")
    check(isinstance(result.get("evidence"), list), "evidence_list", "evidence 为列表")
    check(isinstance(result.get("evidence_map"), dict), "evidence_map",
          "evidence_map 存在且为 dict（结构更细）")
    check(isinstance(result.get("sources"), list), "sources", "sources 为列表")
    check(isinstance(result.get("uncertainty"), dict), "uncertainty", "uncertainty 为 dict")
    check(isinstance(result.get("change_log"), list), "change_log", "change_log 为列表")
    check("pending_human" in result, "pending_human", "pending_human 字段存在")

    # 每条结果证据溯源：locator + quote
    ev_ok = all(
        isinstance(e, dict) and (e.get("locator") or e.get("quote") is not None)
        for r in (result.get("results") or []) for e in (r.get("evidence") or []))
    check(ev_ok, "evidence_locator_quote", "每条结果证据均带 locator 或逐字 quote")

    # 每份证据可回指
    em = result.get("evidence_map") or {}
    em_ok = all(
        isinstance(v, dict) and "kind" in v and "locator" in v
        for v in em.values())
    check(em_ok, "evidence_map_shape", "evidence_map 每条含 kind+locator+quote")

    # 合同字段回填
    summary, _ = read_json(os.path.join(work_dir, "case_summary.json"), {})
    contract_fields = summary.get("contract_fields") or []
    missing = [f for f in contract_fields if f not in result]
    check(not missing, "contract_backfill",
          "output_contract 字段全部回填；缺 %s" % (missing or "无"))

    # 空结果时状态诚实
    if not (result.get("results")):
        check(result.get("status") in ("insufficient_evidence", "input_error"),
              "empty_honest", "空结果必须如实标注 insufficient_evidence/input_error")

    report = {
        "time": now_iso(),
        "passed": len([c for c in checks if c["pass"]]),
        "failed": len(issues),
        "results": checks,
        "summary": "PASS" if not issues else ("FAIL (%d issue(s))" % len(issues)),
    }
    write_json(os.path.join(work_dir, "selfcheck.json"), report)
    for c in checks:
        print(("[PASS] " if c["pass"] else "[FAIL] ") + c["check"] + " — " + c["detail"])
    print("SELFCHECK " + report["summary"])
    if strict and issues:
        return False
    return True


# ---------------- run / skeleton ----------------
def write_skeleton(input_dir, output_dir, work_dir, status="partial"):
    ensure_dir(output_dir)
    case_id = "unknown"
    summary, _ = read_json(os.path.join(work_dir, "case_summary.json"), {})
    case_id = summary.get("case_id") or "unknown"
    skel = {
        "case_id": case_id,
        "status": status,
        "research_question": summary.get("research_question"),
        "search_scope": {},
        "results": [],
        "evidence": [],
        "evidence_map": {},
        "sources": [],
        "uncertainty": {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []},
        "status_notes": ["骨架结果：检索尚未完成，仅供中途容错。"],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": now_iso(), "updated_at": now_iso(),
                    "elapsed_seconds": None, "script_version": VERSION},
    }
    for name in (summary.get("contract_fields") or []):
        if name not in skel:
            skel[name] = None
    write_json(os.path.join(output_dir, "result.json"), skel)
    ensure_dir(work_dir)
    log_change(work_dir, "skeleton", "落盘 partial 结果骨架（容错）", "partial")
    return skel


def run_pipeline(input_dir, output_dir, work_dir):
    ensure_dir(work_dir)
    ensure_dir(output_dir)
    write_skeleton(input_dir, output_dir, work_dir, "partial")
    summary = parse_case(input_dir, work_dir)
    build_plan(work_dir)
    result = assemble(input_dir, output_dir, work_dir)
    return result