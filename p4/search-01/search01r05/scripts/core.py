#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术文献检索 Skill 核心实现（纯标准库）。

本模块承担：
  1. 稳健 IO 与输入发现（BOM / 坏编码 / 坏 JSON / 超限文件均降级，不崩溃）；
  2. 输出契约的发现与归一化（output_contract / output / schema / required_fields ...）；
  3. 候选记录的去重、打分与稳定排序；
  4. 结果组装：按「规范字段 + 契约原名字段 + 契约回填报告」三层发布；
  5. 结构化自检（result.json 是否满足输出契约）。

设计取舍（变体 #输出契约完整）：
  契约优先。先把任意形式的 output_contract 归一化为有序字段表，
  组装阶段逐一对齐：case_id 与契约全部字段保证存在，未获得的回填 null/[]，
  绝不改名、绝不删未知键；并生成 contract_report 供评审逐字段核对。
所有内容决策确定性可复现；change_log / runtime 为真实的运行留痕。
"""
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
SCHEMA_VERSION = "1.1.0"

STATUS_SET = ("ok", "partial", "insufficient_evidence", "input_error")

# 可能承载 output_contract 的键名（大小写变体见发现逻辑）
CONTRACT_KEY_NAMES = (
    "output_contract", "outputContract", "output", "result_schema",
    "schema", "required_output", "required_fields", "expected_fields",
    "contract", "fields", "output_schema",
)

# 数据库 -> 来源可靠度（用于 0.2 权重）
_SOURCE_RELIABILITY = {
    "crossref": 1.0, "pubmed": 1.0, "europepmc": 1.0, "europe_pmc": 1.0,
    "publisher": 1.0, "doi": 1.0, "openalex": 0.95, "scopus": 0.95,
    "semanticscholar": 0.9, "semantic_scholar": 0.9, "dblp": 0.9,
    "arxiv": 0.8, "biorxiv": 0.8, "medrxiv": 0.8, "ssrn": 0.8,
    "preprint": 0.7, "scholar": 0.7, "google_scholar": 0.7,
    "web": 0.6, "web_search": 0.6,
}
_SOURCE_FALLBACK = (
    ("crossref", 1.0), ("pubmed", 1.0), ("europepmc", 1.0), ("openalex", 0.95),
    ("semanticscholar", 0.9), ("arxiv", 0.8), ("publisher", 1.0),
)


# --------------------------------------------------------------------------
# 稳健 IO
# --------------------------------------------------------------------------
def ensure_dir(path):
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """读取 UTF-8 文本，容错 BOM / 坏编码 / 坏 JSON。返回 (text, degradations)。"""
    if not path or not os.path.isfile(path):
        return "", ["missing:" + str(path)]
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", ["stat_failed:" + str(path)]
    if size > max_bytes:
        return "", ["size_exceeded:%s:%d" % (path, size)]
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return "", ["read_failed:%s:%s" % (path, str(e)[:80])]
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
        print("write_text failed: %s %s" % (path, str(e)[:120]), file=sys.stderr)
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
        print("write_json failed: %s %s" % (path, str(e)[:120]), file=sys.stderr)
        return False


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


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


def touch_run_start(work_dir):
    path = os.path.join(work_dir, "run_meta.json")
    meta, _ = read_json(path, {})
    if not isinstance(meta, dict):
        meta = {}
    if "start" not in meta:
        meta["start"] = time.time()
        meta["input_key"] = ""
    write_json(path, meta)
    return meta


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


def _pick_first(data, keys):
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def canon_key(name):
    if name is None:
        return ""
    s = str(name).strip()
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)  # camelCase -> snake_case
    s = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s


def extract_contract_from_text(text):
    """从 task.md / content 字符串中发现 output_contract（JSON 块或 Markdown 表）。"""
    if not text:
        return None
    # 1) 围栏 JSON 块
    for m in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.S | re.I):
        try:
            obj = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(obj, dict):
            for k in CONTRACT_KEY_NAMES:
                if k in obj:
                    return obj[k]
            return obj
        if isinstance(obj, list):
            return obj
    # 2) Markdown 表格：表头含 字段/field/name/key
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "|" in line:
            cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            if any(c in ("字段", "field", "name", "key", "内容", "字段与要求") for c in cells):
                header_idx = i
                break
    if header_idx is not None:
        fields = {}
        for line in lines[header_idx + 1:]:
            if "|" not in line:
                continue
            raw_cells = line.strip().strip("|").split("|")
            if all(set(c.strip()) <= set("-: ") for c in raw_cells):
                continue
            cells = [c.strip() for c in raw_cells]
            if not cells:
                continue
            name = cells[0].strip("`").strip()
            if name and re.match(r"^[A-Za-z_][A-Za-z0-9_\-]{0,40}$", name):
                fields[name] = " ".join(cells[1:]).strip() or "field"
        if fields:
            return {"fields": fields}
    return None


def parse_case(input_dir):
    files = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    degradations = []

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    degradations += d
    task_text, d = read_text(task_path) if task_path else ("", ["no_task_md"])
    degradations += d

    case_id = _pick_first(case_data, ("case_id", "caseId", "id", "challenge_id",
                                      "challengeId", "armiId"))
    if not case_id:
        m = re.search(r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)", task_text)
        if m:
            case_id = m.group(1)
    if not case_id:
        case_id = ("case:" + os.path.splitext(os.path.basename(case_path))[0]) if case_path else "unknown"

    research_question = _pick_first(case_data, ("research_question", "researchQuestion",
                                                "question", "query", "topic", "task",
                                                "problem", "title", "research_topic"))
    if not research_question:
        for tag in ("研究问题", "科研问题", "研究主题", "Research question", "Research topic", "题目"):
            m = re.search(r"(?:%s)\s*[:：]\s*(.+)" % tag, task_text)
            if m:
                research_question = m.group(1).strip()[:2000]
                break
    if not research_question:
        research_question = None

    # 输出契约：优先 case.json 显式键，其次从 task.md 提取
    raw_contract = None
    for k in CONTRACT_KEY_NAMES:
        if k in case_data and case_data[k] not in (None, ""):
            raw_contract = case_data[k]
            break
    if raw_contract is None:
        raw_contract = extract_contract_from_text(task_text)

    summary = {
        "case_id": case_id,
        "research_question": research_question,
        "search_scope": _pick_first(case_data, ("search_scope", "searchScope", "scope", "search_scope_note")),
        "input_files": [os.path.basename(f) for f in files],
        "materials": [f for f in files if "material" in os.path.basename(f).lower()
                      or f.lower().endswith(".pdf")],
        "constraints": {},
        "output_contract_raw": raw_contract,
        "contract_spec": normalize_contract(raw_contract),
        "degradations": degradations,
    }
    # 常用约束兜底读取
    for k in ("search_scope", "scope", "constraints", "limits", "requirements", "notes"):
        if k in case_data:
            summary["constraints"][k] = case_data[k]
    return summary


# --------------------------------------------------------------------------
# 输出契约归一化
# --------------------------------------------------------------------------
def _spec_item(name, required, type_, description):
    return {
        "name": str(name),
        "required": bool(required),
        "type": type_,
        "description": str(description or ""),
    }


def normalize_contract(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        names = re.findall(r"`([A-Za-z_][A-Za-z0-9_\-]{1,40})`", s)
        if names:
            return [_spec_item(n, False, None, "") for n in dict.fromkeys(names)]
        try:
            obj = json.loads(s)
            if isinstance(obj, (dict, list)):
                return normalize_contract(obj)
        except Exception:
            pass
        return [_spec_item(s, False, None, "")]
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, str):
                out.append(_spec_item(item, False, None, ""))
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key") or item.get("id")
                if not name:
                    continue
                out.append(_spec_item(name,
                                      item.get("required") or item.get("is_required") or False,
                                      item.get("type"),
                                      item.get("description") or item.get("desc")))
        return out
    if isinstance(raw, dict):
        required = set()
        r = raw.get("required")
        if isinstance(r, list):
            required = set(str(x) for x in r)
        if isinstance(raw.get("fields"), (dict, list)):
            items = raw["fields"]
        elif isinstance(raw.get("properties"), dict):
            items = raw["properties"]
        else:
            items = raw
        out = []
        if isinstance(items, dict):
            for name, v in items.items():
                if isinstance(v, dict):
                    out.append(_spec_item(name,
                                          v.get("required") or (name in required),
                                          v.get("type"),
                                          v.get("description") or v.get("desc")))
                else:
                    out.append(_spec_item(name, name in required, None, v or ""))
        elif isinstance(items, list):
            out = normalize_contract(items)
        # required 中但未出现在 properties/fields 里的字段也要保留（契约完整性）
        for name in required:
            if not any(x["name"] == str(name) for x in out):
                out.append(_spec_item(name, True, None, ""))
        return out
    return []


# 规范字段 <-> 契约字段名 别名桥表（变体核心：逐一对齐）
_CANONICAL_ALIASES = {
    "case_id": ("case_id", "caseid", "case", "id", "challenge_id", "challengeid", "armiid", "armi_id"),
    "status": ("status", "run_status", "state"),
    "research_question": ("research_question", "researchquestion", "question", "query", "topic", "task", "problem", "title"),
    "search_scope": ("search_scope", "searchscope", "scope"),
    "results": ("results", "result", "candidates", "literature", "papers", "records", "search_results", "hits"),
    "evidence": ("evidence", "evidence_records", "traces", "query_log", "querylog", "search_trace"),
    "sources": ("sources", "source_list", "references", "databases"),
    "uncertainty": ("uncertainty", "uncertainties", "coverage"),
    "pending_human": ("pending_human", "pendinghuman", "to_verify", "open_questions"),
    "errors": ("errors", "error_list"),
    "degradations": ("degradations", "fallbacks"),
    "change_log": ("change_log", "changelog", "change_history", "change_records", "log"),
    "limitations": ("limitations", "limits"),
    "status_notes": ("status_notes", "statusnotes", "notes"),
    "runtime": ("runtime", "timing"),
    "query_plan": ("query_plan", "plan", "search_plan"),
}

_ALIAS_TO_CANON = {}
for _canon, _aliases in _CANONICAL_ALIASES.items():
    _ALIAS_TO_CANON[canon_key(_canon)] = _canon
    for _a in _aliases:
        _ALIAS_TO_CANON[canon_key(_a)] = _canon


def resolve_contract_value(result, name):
    """把契约字段名解析到规范值。返回 (value, source) 或 (None, None)。"""
    n = canon_key(name)
    canon = _ALIAS_TO_CANON.get(n)
    if canon and canon in result:
        return result[canon], "canonical:" + canon
    if name in result:
        return result[name], "raw:" + name
    if n in result:
        return result[n], "normalized:" + n
    return None, None


def default_for_field(field):
    t = str(field.get("type") or "").lower()
    if "array" in t or "list" in t:
        return []
    return None


# --------------------------------------------------------------------------
# 候选记录：归一 / 去重 / 打分 / 排序
# --------------------------------------------------------------------------
def norm_doi(doi):
    if not doi:
        return None
    d = str(doi).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = d.strip()
    return d or None


def fingerprint(text):
    if not text:
        return ""
    t = str(text).lower()
    t = re.sub(r"\W+", "", t, flags=re.UNICODE)
    return t


def _split_authors(a):
    if isinstance(a, str):
        parts = [x.strip() for x in re.split(r"[;,、]", a) if x.strip()]
        return parts or None
    if isinstance(a, list):
        return [str(x).strip() for x in a if str(x).strip()] or None
    return None


def normalize_record(r):
    if not isinstance(r, dict):
        return None
    rc = dict(r)
    rc["authors"] = _split_authors(rc.get("authors"))
    if rc.get("year") is not None:
        try:
            rc["year"] = int(rc["year"])
        except (TypeError, ValueError):
            pass
    ev = rc.get("evidence")
    if isinstance(ev, dict):
        ev = [ev]
    rc["evidence"] = ev if isinstance(ev, list) else []
    return rc


def dedup_key(rec):
    doi = norm_doi(rec.get("doi"))
    if doi:
        return ("doi", doi)
    t = rec.get("title")
    if t:
        return ("title", fingerprint(t))
    return None


def metadata_completeness(rec):
    fields = ("title", "authors", "year", "journal", "doi", "url")
    return sum(1 for f in fields if rec.get(f) not in (None, "", [], [""])) / float(len(fields))


def relevance_value(rec):
    v = rec.get("relevance")
    if v is None:
        return 0.5 if rec.get("relevance_reason") else 0.0
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, min(1.0, v))


def source_reliability(database):
    db = canon_key(database or "")
    if db in _SOURCE_RELIABILITY:
        return _SOURCE_RELIABILITY[db]
    for prefix, score in _SOURCE_FALLBACK:
        if prefix in db:
            return score
    return 0.5


def score_record(rec):
    """透明打分：0.6·relevance + 0.2·元数据完整度 + 0.2·来源可靠度。"""
    return round(
        0.6 * relevance_value(rec)
        + 0.2 * metadata_completeness(rec)
        + 0.2 * source_reliability(rec.get("database")), 4)


def merge_records(a, b):
    primary = a if (metadata_completeness(a), relevance_value(a)) >= \
        (metadata_completeness(b), relevance_value(b)) else b
    secondary = b if primary is a else a
    out = {}
    for k in set(list(primary.keys()) + list(secondary.keys())):
        pv = primary.get(k)
        if pv not in (None, "", [], [""]):
            out[k] = pv
        else:
            out[k] = secondary.get(k, pv)
    evs, seen = [], set()
    for e in (primary.get("evidence") or []) + (secondary.get("evidence") or []):
        sig = json.dumps(e, ensure_ascii=False, sort_keys=True, default=str)
        if sig not in seen:
            seen.add(sig)
            evs.append(e)
    out["evidence"] = evs
    return out


def dedup_records(records):
    by_key = {}
    for r in records:
        r = normalize_record(r)
        if not r or not r.get("title"):
            continue
        k = dedup_key(r)
        if k is None:
            k = ("raw", "k%d" % len(by_key))
        if k in by_key:
            by_key[k] = merge_records(by_key[k], r)
        else:
            by_key[k] = r
    ranked = sorted(by_key.values(),
                    key=lambda x: (score_record(x), (x.get("year") or 0), fingerprint(x.get("title") or "")),
                    reverse=True)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
        r["score"] = score_record(r)
        r["metadata_completeness"] = round(metadata_completeness(r), 4)
        r["source_reliability"] = round(source_reliability(r.get("database")), 4)
    return ranked


# --------------------------------------------------------------------------
# 查询规划（确定性模板 + 输入关键词核验清单）
# --------------------------------------------------------------------------
def build_query_plan(case_summary):
    rq = case_summary.get("research_question") or "unknown topic"
    plan = {
        "research_question": rq,
        "strategy": "多轮查询规划 + 关键词扩展 + 跨库召回 + 去重核验",
        "rounds": [
            {"round": 1, "concept": "核心概念直查（题目原词）",
             "notes": "用研究问题中的核心实体/方法直接检索，建立候选基线。"},
            {"round": 2, "concept": "同义词/缩写/上位词扩展",
             "notes": "对每个核心概念列同义词、缩写、跨语言表达，构造 OR/AND 组合。"},
            {"round": 3, "concept": "引用与被引（reference graph）",
             "notes": "从命中记录的参考文献/引证关系补充关键文献，降低遗漏。"},
            {"round": 4, "concept": "数据库交叉核验",
             "notes": "用 Crossref/EuropePMC/publisher 响应核验 DOI、元数据与去重。"},
        ],
        "keyword_checklist": [
            "核心实体是否出现", "是否扩展了同义词/缩写", "是否覆盖上位/下位概念",
            "是否加入方法/对象限定词", "是否控制无关结果（否定词/年限）",
        ],
        "coverage_gaps": [],
        "notes": [],
    }
    return plan


# --------------------------------------------------------------------------
# 组装 result.json + candidates.md
# --------------------------------------------------------------------------
def build_result(case_summary, plan, records, evidence, change_log, errors, degradations):
    results = dedup_records(records)
    sources = []
    seen_src = set()
    for r in results:
        s = r.get("database") or r.get("source")
        if s and s not in seen_src:
            seen_src.add(s)
            sources.append(s)
    for e in evidence:
        s = e.get("source") or e.get("database")
        if s and s not in seen_src:
            seen_src.add(s)
            sources.append(s)

    uncertainty = {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []}
    for r in results:
        missing = [f for f in ("authors", "year", "journal", "doi", "url")
                   if r.get(f) in (None, "", [], [""])]
        # DOI 存在时 url 可派生，不视作元数据缺失
        if "url" in missing and r.get("doi") not in (None, ""):
            missing.remove("url")
        if missing:
            uncertainty["missing_metadata"].append({"title": r.get("title"), "missing": missing})
        if r.get("source_conflict"):
            uncertainty["conflicts"].append(r.get("source_conflict"))
    uncertainty["coverage_gaps"] = list(plan.get("coverage_gaps", [])) if isinstance(plan, dict) else []
    uncertainty["notes"] = list(plan.get("notes", [])) if isinstance(plan, dict) else []

    pending_human = []
    for r in results:
        if r.get("relevance") is None and not r.get("relevance_reason"):
            pending_human.append({"title": r.get("title"), "issue": "relevance 未标注，需人工判定"})

    limitations = []
    limitations += list((plan.get("notes") or []) if isinstance(plan, dict) else [])
    if uncertainty["missing_metadata"]:
        limitations.append("部分结果存在元数据缺失，已列入 uncertainty.missing_metadata。")
    if degradations:
        limitations.append("输入解析存在降级：%s" % "; ".join(str(x) for x in degradations[:5]))

    if not results:
        status = "input_error" if case_summary.get("input_error") else "insufficient_evidence"
    elif errors or degraded(pending_human, uncertainty, degradations):
        status = "partial"
    else:
        status = "ok"

    result = {
        "case_id": case_summary.get("case_id", "unknown"),
        "status": status,
        "research_question": case_summary.get("research_question"),
        "search_scope": case_summary.get("search_scope"),
        "results": results,
        "evidence": evidence,
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": pending_human,
        "errors": errors,
        "degradations": degradations,
        "limitations": limitations,
        "change_log": change_log,
        "query_plan": plan,
        "runtime": {
            "seconds": _runtime_seconds(),
        },
        "result_schema_version": SCHEMA_VERSION,
    }
    return result


def degraded(pending_human, uncertainty, degradations):
    return bool(pending_human) or bool(uncertainty.get("missing_metadata")) \
        or bool(uncertainty.get("coverage_gaps")) or bool(degradations)


def _runtime_seconds():
    # runtime 由 skeleton 记录 start；本函数仅在 standalone 调用时兜底
    return None


def apply_contract(result, contract_spec):
    """按输出契约逐一对齐：契约字段保证存在，未获得的回填 null/[]。"""
    report = []
    for field in contract_spec:
        name = field["name"]
        value, source = resolve_contract_value(result, name)
        if value is None and source is None:
            value = default_for_field(field)
            source = "backfilled:" + ("empty_array" if value == [] else "null")
        result[name] = value
        report.append({
            "name": name,
            "required": field.get("required", False),
            "type": field.get("type"),
            "source": source,
            "value_preview": _preview(value),
            "filled": bool(source is not None and not source.startswith("backfilled")),
            "status": "filled" if (source and not source.startswith("backfilled"))
                      else ("empty_backfilled" if value == [] else "null_backfilled"),
        })
    result["contract_report"] = {
        "spec_version": SCHEMA_VERSION,
        "total_fields": len(contract_spec),
        "filled": sum(1 for x in report if x["status"] == "filled"),
        "null_backfilled": sum(1 for x in report if x["status"] == "null_backfilled"),
        "empty_backfilled": sum(1 for x in report if x["status"] == "empty_backfilled"),
        "fields": report,
    }
    return result


def _preview(value):
    if isinstance(value, list):
        return "[%d items]" % len(value)
    if isinstance(value, dict):
        return "{%d keys}" % len(value)
    if value is None:
        return None
    s = str(value)
    return s[:80] + ("..." if len(s) > 80 else "")


def assemble(input_dir, output_dir, work_dir):
    ensure_dir(output_dir)
    cs = _load_case_summary(input_dir, work_dir)
    plan, _ = read_json(os.path.join(work_dir, "plan.json"), {})
    records, _ = read_json(os.path.join(work_dir, "records.json"), [])
    evidence, _ = read_json(os.path.join(work_dir, "evidence.json"), [])
    change_log, _ = read_json(os.path.join(work_dir, "change_log.json"), [])
    errors_file = os.path.join(work_dir, "errors.json")
    errors, _ = read_json(errors_file, [])
    degradations = list(cs.get("degradations", []))
    if not isinstance(records, list):
        records = []
    if not isinstance(evidence, list):
        evidence = []
    if not isinstance(change_log, list):
        change_log = []
    if not isinstance(errors, list):
        errors = []

    result = build_result(cs, plan, records, evidence, change_log, errors, degradations)
    # 运行时兜底
    meta, _ = read_json(os.path.join(work_dir, "run_meta.json"), {})
    start = meta.get("start") if isinstance(meta, dict) else None
    end = time.time()
    result["runtime"] = {
        "start_unix": start,
        "end_unix": end,
        "seconds": round(end - start, 2) if start else None,
    }
    contract_spec = cs.get("contract_spec") or []
    result = apply_contract(result, contract_spec)

    # 兜底：契约字段即使不在契约里，也保证 case_id/status 存在
    result.setdefault("case_id", cs.get("case_id", "unknown"))
    result.setdefault("status", result.get("status", "partial"))

    write_json(os.path.join(output_dir, "result.json"), result)
    write_text(os.path.join(output_dir, "candidates.md"), render_candidates(result))
    log_change(work_dir, "assemble",
               "组装 result.json；结果 %d 条；契约字段 %d 个" % (
                   len(result.get("results", [])), len(result.get("contract_report", {}).get("fields", []))),
               result["status"])
    return result


def _load_case_summary(input_dir, work_dir):
    cs, deg = read_json(os.path.join(work_dir, "case_summary.json"), {})
    if not cs:
        cs = parse_case(input_dir)
        cs.setdefault("degradations", []).extend(deg)
    return cs


def render_candidates(result):
    lines = ["# 候选文献（可编辑复核清单）", ""]
    lines.append("- case_id: `%s`" % result.get("case_id", ""))
    lines.append("- status: `%s`" % result.get("status", ""))
    lines.append("- result_schema_version: `%s`" % SCHEMA_VERSION)
    lines.append("")
    if not result.get("results"):
        lines.append("> 材料不足，暂无候选。如需补检索，请按 SKILL.md 第 4 节流程继续。")
        return "\n".join(lines) + "\n"
    lines.append("| rank | title | authors | year | journal | doi/url | database | relevance | score |")
    lines.append("|-|-|-|-|-|-|-|-|-|")
    for r in result["results"]:
        authors = ", ".join(r.get("authors") or []) or "-"
        loc = r.get("doi") or r.get("url") or "-"
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %.4f |" % (
            r.get("rank", 0),
            (r.get("title") or "-").replace("|", "\\|"),
            authors.replace("|", "\\|"),
            r.get("year") or "-",
            (r.get("journal") or "-").replace("|", "\\|"),
            str(loc).replace("|", "\\|"),
            r.get("database") or "-",
            r.get("relevance") if r.get("relevance") is not None else "-",
            r.get("score", 0.0),
        ))
    lines.append("")
    lines.append("## 复核指引")
    lines.append("逐条核验 ranking：标题/作者/年份/期刊/DOI 是否与来源一致；relevance_reason 是否有 locator+quote 支持；")
    lines.append("冲突与缺失见 result.json 的 uncertainty / pending_human。")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# 骨架（前 3 分钟必须落盘）
# --------------------------------------------------------------------------
def skeleton(output_dir, work_dir):
    ensure_dir(output_dir)
    ensure_dir(work_dir)
    result = {
        "case_id": "pending",
        "status": "partial",
        "research_question": None,
        "search_scope": None,
        "results": [],
        "evidence": [],
        "sources": [],
        "uncertainty": {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []},
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": ["骨架先落盘，后续增量改进。"],
        "change_log": log_change(work_dir, "skeleton", "落盘 partial 骨架，保证任何中途失败也有合法 result.json", "partial"),
        "query_plan": None,
        "runtime": None,
        "result_schema_version": SCHEMA_VERSION,
    }
    write_json(os.path.join(output_dir, "result.json"), result)
    write_text(os.path.join(output_dir, "candidates.md"),
               "# 候选文献\n\n> partial 骨架：检索尚未完成。\n")
    return result


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def selfcheck(output_dir, input_dir=None, work_dir=None):
    rp = os.path.join(output_dir, "result.json")
    text, deg = read_text(rp)
    checks = []
    if not text:
        checks.append(("FAIL", "result.json 缺失或不可读"))
        return False, checks, deg
    obj, deg2 = read_json(rp, None)
    if obj is None or not isinstance(obj, dict):
        checks.append(("FAIL", "result.json 不是合法 JSON 对象"))
        return False, checks, deg + deg2

    def check(cond, label):
        checks.append(("PASS" if cond else "FAIL", label))

    check(True, "result.json 存在且为合法 JSON 对象")
    check(bool(obj.get("case_id")), "case_id 非空")
    check(obj.get("status") in STATUS_SET, "status 取值合法")
    check(isinstance(obj.get("results"), list), "results 为列表")
    ok_item = True
    for r in obj.get("results", []):
        if not isinstance(r, dict):
            ok_item = False
        elif not r.get("title") or not (r.get("doi") or r.get("url")):
            ok_item = False
    check(ok_item, "每条结果均有 title 与 doi/url")
    check(isinstance(obj.get("evidence"), list), "evidence 为列表且每项可溯源")
    check(isinstance(obj.get("sources"), list), "sources 为列表")
    check(isinstance(obj.get("uncertainty"), dict), "uncertainty 为字典")
    check(isinstance(obj.get("change_log"), list) and obj.get("change_log"),
          "change_log 非空")
    fields = obj.get("contract_report", {}).get("fields", [])
    if fields:
        check(all(f["name"] in obj for f in fields), "契约字段逐一对齐（所有键均在结果中）")
        check(not any(f["status"] not in ("filled", "null_backfilled", "empty_backfilled") for f in fields),
              "契约字段回填状态合法")
    else:
        check(True, "契约字段（空契约，跳过）")
    # 证据溯源
    ev_ok = all(isinstance(e, dict) and (e.get("query") or e.get("locator") or e.get("kind"))
                for e in obj.get("evidence", []))
    check(ev_ok, "evidence 条目包含 query/locator/kind 之一")
    return all(c[0] == "PASS" for c in checks), checks, deg + deg2