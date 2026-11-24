#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py —— 学术文献检索 Skill 的确定性核心（纯标准库）。

职责：稳健 IO、输入发现、契约回填、查询规划、候选人导入/去重、
透明打分、证据记录、组装 result.json / candidates.md、自检与复现指纹。

设计目标（本版变体 #鲁棒性）：
  1. 空输入 / 坏 JSON / 坏编码 / 缺参数 / 超大文件 / 二进制污染 均不崩溃；
  2. 题目 output_contract 字段原样回填：缺值给 null 或 []，不改名、不删未知键；
  3. 同一输入重复运行，除时间戳外的主内容确定性一致（提供 fingerprint 佐证）；
  4. 结果可复核：每条候选须带 locator + 逐字 quote，缺失者标记 unverified。
"""
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 单文件读取上限：超过即跳过并记录 degradation，保证超大文件不拖垮进程。
MAX_FILE_BYTES = 2 * 1024 * 1024
# 整棵树扫描上限，防止超大输入目录导致不可控耗时（超出后不再深入）。
MAX_DISCOVER_ENQUEUE = 2000

# 可能承载输出契约的键名；同名不同写法都要识别。
CONTRACT_KEYS = (
    "output_contract", "outputContract", "output", "result_schema", "schema",
    "required_output", "required_fields", "expected_fields", "contract",
    "output_schema", "response_schema", "submission_schema", "result_shape",
)

# 候选记录允许的字段宽容集：导入时归一化，未知字段保留但挪到 meta 下。
CANDIDATE_FIELD_MAP = {
    "title": ("title",),
    "authors": ("authors", "author", "creators"),
    "year": ("year", "pub_year", "publication_year", "issued"),
    "journal": ("journal", "venue", "journal_title", "container_title"),
    "doi": ("doi", "DOI"),
    "url": ("url", "link", "pdf_url", "fulltext_url"),
    "database": ("database", "source", "source_db", "origin"),
    "relevance": ("relevance", "relevance_score", "score"),
    "relevance_reason": ("relevance_reason", "reason", "rationale", "match_rationale"),
    "abstract": ("abstract", "summary", "synopsis"),
    "evidence": ("evidence", "evidences", "source_locators"),
}


def now_iso():
    """本地时区 ISO 时间戳；仅用于 trace（不参与 fingerprint 计算）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_dir(path):
    if path:
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except OSError:
            return False
    return True


# --------------------------------------------------------------------------- #
# 稳健 IO
# --------------------------------------------------------------------------- #
def sniff_binary(data: bytes) -> bool:
    """粗判二进制：含 NUL 或不可打印字节比例过高。"""
    if not data:
        return False
    if b"\x00" in data[:4096]:
        return True
    sample = data[:8192]
    if not sample:
        return False
    bad = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return bad / len(sample) > 0.05


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """读文本文件。返回 (text, degradations)。任何失败都降级为 ''，绝不抛异常。"""
    if not path or not os.path.isfile(path):
        return "", ["missing:%s" % str(path)]
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", ["stat_failed:%s" % str(path)]
    if size > max_bytes:
        return "", ["size_exceeded:%s:%d" % (str(path), size)]
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return "", ["read_failed:%s:%s" % (str(path), str(e)[:80])]
    if sniff_binary(data):
        return "", ["binary_or_polluted:%s" % str(path)]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1"):
        try:
            return data.decode(enc), []
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), ["fallback_decode:%s" % str(path)]


def read_json(path, default=None, max_bytes=MAX_FILE_BYTES):
    """读 JSON。坏 JSON / 坏编码 / 空文件均降级返回 default，不抛异常。"""
    if default is None:
        default = {}
    text, deg = read_text(path, max_bytes)
    if deg:
        return default, deg
    if not text.strip():
        return default, ["empty:%s" % str(path)]
    try:
        obj = json.loads(text)
        return obj, []
    except Exception as e:
        return default, ["bad_json:%s:%s" % (str(path), str(e)[:120])]


def write_text(path, text):
    """原子写文本：先写临时文件再 os.replace，写失败返回 False 不崩溃。"""
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if not ensure_dir(parent):
        return False
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, target)
        return True
    except OSError as e:
        print("write_text failed:", path, str(e)[:200], file=sys.stderr)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def write_json(path, obj, sort_keys=True):
    """原子写 JSON：ensure_ascii=False、sort_keys 稳定，保证重跑字节一致。"""
    def _default(o):
        if isinstance(o, (datetime,)):
            return o.isoformat()
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    target = os.path.abspath(path)
    if not ensure_dir(os.path.dirname(target)):
        return False
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2,
                      sort_keys=sort_keys, default=_default)
        os.replace(tmp, target)
        return True
    except (OSError, TypeError, ValueError) as e:
        print("write_json failed:", path, str(e)[:200], file=sys.stderr)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# --------------------------------------------------------------------------- #
# 输入发现与解析
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    """扫描输入目录，文件名}列表。超限时截断并记录 degradation。"""
    files = []
    degs = []
    if not input_dir or not os.path.isdir(input_dir):
        return files, ["no_input_dir:%s" % str(input_dir)]
    queued = 0
    try:
        for root, _dirs, names in os.walk(input_dir):
            for n in sorted(names):
                if queued >= MAX_DISCOVER_ENQUEUE:
                    degs.append("discover_truncated:%s" % str(input_dir))
                    return files, degs
                p = os.path.join(root, n)
                queued += 1
                # 超大文件：跳过并记录 degradation，防止拖垮进程。
                try:
                    if os.path.getsize(p) > MAX_FILE_BYTES:
                        degs.append("size_exceeded:%s:%d" % (p, os.path.getsize(p)))
                        continue
                except OSError:
                    pass
                files.append(p)
    except OSError as e:
        degs.append("walk_failed:%s:%s" % (str(input_dir), str(e)[:80]))
    return files, degs


def find_case_file(files):
    for f in files:
        if os.path.basename(f).lower() == "case.json":
            return f
    for f in files:
        if os.path.basename(f).lower().startswith("case") and f.lower().endswith(".json"):
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
    # 兜底：任意 markdown 均可作为任务说明（不止 task.md，增强鲁棒性）。
    for f in files:
        if f.lower().endswith(".md"):
            return f
    return None


def extract_case_id(case_data, task_text, files):
    for k in ("case_id", "caseId", "id", "challenge_id", "challengeId", "armiId", "armi_id"):
        v = case_data.get(k) if isinstance(case_data, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    for pat in (r"(?:case_id|challenge_id)\s*[:：]\s*`?([A-Za-z0-9_\-]+)",
                r"\bchallenge-([A-Za-z0-9]+)\b"):
        m = re.search(pat, task_text or "")
        if m:
            return m.group(1)
    # 兜底：以 case.json 文件名派生，保证 case_id 恒非空。
    for f in files:
        if os.path.basename(f).lower().endswith(".json"):
            return "case:" + os.path.splitext(os.path.basename(f))[0]
    return "unknown"


def extract_research_question(case_data, task_text):
    if isinstance(case_data, dict):
        for k in ("research_question", "researchQuestion", "question", "query",
                  "topic", "task", "problem", "research_topic", "research_problem",
                  "title", "goal", "objective"):
            v = case_data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        m = re.search(r"(?:研究问题|科研问题|研究主题|Research question|Research topic|Task[:：]?)\s*[:：]?\s*(.+)",
                      task_text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()[:2000]
        for line in task_text.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "-", "*", "`", "|", ">")):
                return s[:2000]
    return None


def extract_contract(case_data):
    """从 case.json 提取输出契约：返回 (contract_dict, found_key)。"""
    if not isinstance(case_data, dict):
        return {}, None
    for k in CONTRACT_KEYS:
        v = case_data.get(k)
        if isinstance(v, dict):
            return v, k
        if isinstance(v, list) and v:
            return {"fields": v}, k
        if isinstance(v, str) and v.strip():
            return {"spec": v}, k
    # 兜底：一些平台把契约写进 scoring / schema 下。
    for group in ("scoring", "result", "output_spec"):
        g = case_data.get(group)
        if isinstance(g, dict):
            for k in CONTRACT_KEYS:
                if isinstance(g.get(k), dict):
                    return g[k], group + "." + k
    return {}, None


def parse_case(input_dir, work_dir):
    """解析 /input。返回 dict：case_id/research_question/files/contract/...，永不崩溃。"""
    files, discover_deg = discover_input(input_dir)
    case_path = find_case_file(files)
    task_path = find_task_file(files)
    degradations = list(discover_deg)

    case_data, d = read_json(case_path, {}) if case_path else ({}, ["no_case_json"])
    degradations += d
    task_text, d = read_text(task_path) if task_path else ("", ["no_task_md"])
    degradations += d

    contract, contract_key = extract_contract(case_data)
    # 同时从 task 文本中探测契约片段（若题面内嵌 output_contract）。
    if not contract and task_text:
        for key in CONTRACT_KEYS:
            m = re.search(r"['\"]?%s['\"]?\s*[:：]\s*(\{.*?\})" % re.escape(key),
                          task_text, flags=re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(1))
                    if isinstance(parsed, dict):
                        contract, contract_key = parsed, key
                        break
                except Exception:
                    continue

    summary = {
        "input_dir": input_dir,
        "work_dir": work_dir,
        "files": files,
        "case_path": case_path,
        "task_path": task_path,
        "case_id": extract_case_id(case_data, task_text, files),
        "research_question": extract_research_question(case_data, task_text),
        "contract": contract,
        "contract_key": contract_key,
        "case_data": case_data,
        "task_text": task_text,
        "degradations": degradations,
        "has_case_json": bool(case_path),
        "has_task_md": bool(task_path),
    }
    return summary


# --------------------------------------------------------------------------- #
# 查询规划（轻量、不消耗模型请求，模型后续可改写 plan.json）
# --------------------------------------------------------------------------- #
def build_plan(case_summary):
    question = (case_summary.get("research_question") or "").strip()
    terms = []
    if question:
        for chunk in re.split(r"[\s,:：;；、，]+", question):
            c = chunk.strip("()（）[]【】\"'“”‘’《》<>「」")
            if len(c) >= 2 and not re.fullmatch(r"(the|a|an|of|to|in|on|for|and|or|with|by|at|研究|的|与|和|在|对|及|或|其|之)", c, flags=re.IGNORECASE):
                terms.append(c)
    # 去重保序，最多取 8 个核心词。
    seen, core = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            core.append(t)
        if len(core) >= 8:
            break
    plan = {
        "research_question": question,
        "core_terms": core,
        "stages": [
            {"round": 1, "kind": "single_concept", "desc": "单核心概念召回，建立主干候选", "queries": core[:6]},
            {"round": 2, "kind": "and_combination", "desc": "核心概念 AND 组合，提高相关性", "queries": []},
            {"round": 3, "kind": "synonym_expansion", "desc": "同义词/缩写/跨语言扩展，补召回", "queries": []},
            {"round": 4, "kind": "citation_and_local", "desc": "引文追踪与本地材料的增量导入", "queries": []},
        ],
        "rules": {
            "expand_synonyms": True,
            "dedup_by": ["doi", "title+year"],
            "require_evidence": True,
            "score_weights": {"relevance": 0.6, "metadata": 0.2, "source": 0.2},
        },
    }
    return plan


# --------------------------------------------------------------------------- #
# 候选记录：归一化、去重 fingerprint、upsert
# --------------------------------------------------------------------------- #
def norm_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def title_fingerprint(title, year=None):
    t = norm_text(title)
    words = [w for w in t.split() if len(w) > 1]
    key = " ".join(words)
    if not key:
        return None
    y = ""
    if year:
        yv = extract_year(year)
        if yv:
            y = "@" + str(yv)
    return "t:" + key + y


def doi_fingerprint(doi):
    d = norm_text(doi)
    if not d:
        return None
    d = re.sub(r"^(https?://(dx\.)?doi\.org/)?", "", d)
    d = re.sub(r"^doi[:/]", "", d)
    d = d.strip()
    return ("d:" + d) if d else None


def extract_year(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        y = int(value)
        return y if 1400 <= y <= 2200 else None
    m = re.search(r"(1[4-9]\d\d|20\d\d)", str(value))
    return int(m.group(1)) if m else None


def normalize_authors(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for a in value:
            if isinstance(a, dict):
                nm = a.get("name") or a.get("given", "") + " " + a.get("family", "")
                nm = nm.strip()
            else:
                nm = str(a).strip()
            if nm:
                out.append(nm)
        return out
    s = str(value).strip()
    if not s:
        return []
    s = re.sub(r"^(和|,|\s*et al\.?)\s*", "", s.lower()) if False else s
    parts = re.split(r"\s*[;,|/]\s*|\s+and\s+|\s*、\s*", s)
    return [p.strip() for p in parts if p.strip()]


def normalize_record(item):
    """任意导入项 -> 规范候选记录。未知字段携带到 meta，未知字段不丢弃。"""
    if not isinstance(item, dict):
        return None
    rec = {
        "title": None, "authors": [], "year": None, "journal": None,
        "doi": None, "url": None, "database": None, "relevance": None,
        "relevance_reason": None, "abstract": None, "evidence": [],
    }
    for canon, aliases in CANDIDATE_FIELD_MAP.items():
        for a in aliases:
            if a in item and item.get(a) not in (None, ""):
                rec[canon] = item[a]
                break
    rec["authors"] = normalize_authors(item.get("authors", item.get("author") or item.get("creators")))
    rec["year"] = extract_year(item.get("year", item.get("pub_year") or item.get("publication_year") or item.get("issued")))
    rec["title"] = (rec["title"] or "").strip() or None
    if rec["title"] is None:
        # 没有标题的候选不可核验，导入但标记非可用。
        rec["title"] = None
    ev = item.get("evidence", item.get("source_locators"))
    rec["evidence"] = normalize_evidence(ev)
    # 保存未识别的其余字段，用于可复核追踪。
    known = set()
    for canon, aliases in CANDIDATE_FIELD_MAP.items():
        known.update(aliases)
    meta = {k: v for k, v in item.items() if k not in known and k not in ("evidence", "evidences", "source_locators")}
    if meta:
        rec["meta"] = meta
    rec["fingerprint"] = first_fingerprint(rec)
    return rec


def normalize_evidence(evidence):
    """证据归一化为 [{'locator':str,'quote':str,'kind':str}]，逐字 quote 不得为空。"""
    out = []
    if not evidence:
        return out
    if isinstance(evidence, dict):
        evidence = [evidence]
    elif isinstance(evidence, str):
        evidence = [{"quote": evidence, "locator": "", "kind": "text"}]
    if not isinstance(evidence, (list, tuple)):
        return out
    for e in evidence:
        if isinstance(e, str):
            e = {"quote": e, "locator": "", "kind": "text"}
        if not isinstance(e, dict):
            continue
        loc = (e.get("locator") or e.get("where") or e.get("source") or "").strip()
        quote = (e.get("quote") or e.get("text") or e.get("excerpt") or "").strip()
        kind = (e.get("kind") or e.get("type") or "evidence").strip()
        if quote:
            out.append({"locator": loc, "quote": quote, "kind": kind})
    return out


def first_fingerprint(rec):
    fp = doi_fingerprint(rec.get("doi"))
    if fp:
        return fp
    return title_fingerprint(rec.get("title"), rec.get("year"))


def records_equal(a, b):
    """判两名记录是否同一文献：优先 DOI，其次规范标题。"""
    da = doi_fingerprint(a.get("doi"))
    db = doi_fingerprint(b.get("doi"))
    if da and db:
        return da == db
    if da or db:
        return False
    ta = title_fingerprint(a.get("title"), a.get("year"))
    tb = title_fingerprint(b.get("title"), b.get("year"))
    return ta is not None and ta == tb


def load_records(work_dir):
    path = os.path.join(work_dir, "records.json")
    obj, _ = read_json(path, [])
    return obj if isinstance(obj, list) else []


def save_records(work_dir, records):
    return write_json(os.path.join(work_dir, "records.json"), records)


def import_records(work_dir, incoming, source_label=""):
    """upsert 一批候选。返回 (records, added, merged, skipped_reason)。"""
    records = load_records(work_dir)
    added, merged = 0, 0
    reasons = []
    for item in incoming:
        rec = normalize_record(item)
        if rec is None:
            reasons.append("skipped_non_dict:" + source_label)
            continue
        if rec.get("title") is None and not rec.get("doi"):
            reasons.append("skipped_no_identifiers:" + source_label)
            continue
        fp = rec.get("fingerprint")
        idx = None
        if fp:
            for i, r in enumerate(records):
                if r.get("fingerprint") == fp:
                    idx = i
                    break
        if idx is None:
            rec["_imported_from"] = source_label
            rec["_imported_at"] = now_iso()
            records.append(rec)
            added += 1
        else:
            # 合并：优先回填缺失字段；作者取并集；evidence 取并集。
            old = records[idx]
            for k in ("title", "journal", "doi", "url", "database", "abstract",
                      "relevance_reason"):
                if not old.get(k) and rec.get(k):
                    old[k] = rec[k]
            if not old.get("year") and rec.get("year"):
                old["year"] = rec["year"]
            old["authors"] = merge_authors(old.get("authors"), rec.get("authors"))
            old["evidence"] = merge_evidence(old.get("evidence"), rec.get("evidence"))
            if rec.get("relevance") is not None and old.get("relevance") is None:
                old["relevance"] = rec["relevance"]
            old["_merged_from"] = (old.get("_merged_from") or []) + [source_label]
            records[idx] = old
            merged += 1
    save_records(work_dir, records)
    return records, added, merged, reasons


def merge_authors(a, b):
    a = a if isinstance(a, list) else []
    b = b if isinstance(b, list) else []
    seen, out = set(), []
    for x in a + b:
        k = norm_text(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def merge_evidence(a, b):
    a = a if isinstance(a, list) else []
    b = b if isinstance(b, list) else []
    seen, out = set(), []
    for e in a + b:
        if not isinstance(e, dict):
            continue
        k = (e.get("locator") or "") + "\x00" + (e.get("quote") or "")
        if k.strip("\x00") and k not in seen:
            seen.add(k)
            out.append(e)
    return out


# --------------------------------------------------------------------------- #
# 打分与排序（透明、确定性）
# --------------------------------------------------------------------------- #
def metadata_completeness(rec):
    checks = ["title", "authors", "year", "journal", "doi", "url"]
    present = 0
    for k in checks:
        v = rec.get(k)
        if k == "authors":
            if isinstance(v, list) and v:
                present += 1
        elif v not in (None, "", []):
            present += 1
    return present / len(checks)


def source_reliability(rec):
    doi = rec.get("doi")
    url = rec.get("url") or ""
    db = (rec.get("database") or "").lower()
    trusted_dbs = ("crossref", "europe pmc", "europepmc", "pubmed", "publisher",
                   "doi", "publisher_response", "springer", "elsevier", "nature",
                   "ieee", "wiley", "acs")
    if doi:
        if any(t in db for t in trusted_dbs):
            return 1.0
        return 0.85
    if url:
        host = (re.sub(r"https?://", "", url.lower()).split("/")[0] if url else "")
        if any(h in host for h in ("doi.org", "pubmed", "arxiv", "ncbi", "springer",
                                   "sciencedirect", "nature", "acs", "ieee", "wiley")):
            return 0.75
        return 0.55
    return 0.15


def clamp(v, lo=0.0, hi=1.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, v))


def score_record(rec, weights=None):
    weights = weights or {"relevance": 0.6, "metadata": 0.2, "source": 0.2}
    rel = clamp(rec.get("relevance"), 0.0, 1.0)
    if rec.get("relevance") is None:
        rel = 0.0
    meta = metadata_completeness(rec)
    src = source_reliability(rec)
    raw = weights["relevance"] * rel + weights["metadata"] * meta + weights["source"] * src
    return round(raw, 4), {"relevance": round(rel, 4), "metadata": round(meta, 4),
                           "source": round(src, 4)}


def sort_records(records, weights=None):
    """确定性排序：score desc -> year desc -> title asc。"""
    scored = []
    for r in records:
        s, parts = score_record(r, weights)
        copy = dict(r)
        copy["score"] = s
        copy["score_components"] = parts
        copy["verified"] = r.get("verified", True)
        if not (r.get("evidence") or []):
            copy["verified"] = False
        scored.append(copy)
    scored.sort(key=lambda x: (
        -x["score"],
        -(x.get("year") or 0),
        norm_text(x.get("title") or ""),
    ))
    for i, r in enumerate(scored, 1):
        # 排序前冻结 rank，与后续展示一致。
        r["rank_pre"] = i
    return scored


# --------------------------------------------------------------------------- #
# 证据记录（查询/筛选/排序/追踪）
# --------------------------------------------------------------------------- #
def load_evidence(work_dir):
    path = os.path.join(work_dir, "evidence.json")
    obj, _ = read_json(path, [])
    return obj if isinstance(obj, list) else []


def append_evidence(work_dir, entry):
    entries = load_evidence(work_dir)
    if not isinstance(entry, dict):
        return entries, ["evidence_not_dict"]
    entry = dict(entry)
    entry.setdefault("timestamp", now_iso())
    entry.setdefault("kind", "query")
    entry.setdefault("source", None)
    entry.setdefault("database", None)
    entry.setdefault("rationale", None)
    entry.setdefault("results_count", None)
    entries.append(entry)
    write_json(os.path.join(work_dir, "evidence.json"), entries)
    return entries, []


# --------------------------------------------------------------------------- #
# change_log（时间有序 trace；时间戳必然不同，不参与 fingerprint）
# --------------------------------------------------------------------------- #
def load_change_log(work_dir):
    path = os.path.join(work_dir, "change_log.json")
    obj, _ = read_json(path, [])
    return obj if isinstance(obj, list) else []


def append_change(work_dir, action, reason, status="partial"):
    entries = load_change_log(work_dir)
    entries.append({
        "time": now_iso(),
        "action": action,
        "reason": reason,
        "status": status,
    })
    write_json(os.path.join(work_dir, "change_log.json"), entries)
    return entries


# --------------------------------------------------------------------------- #
# 契约回填与组装
# --------------------------------------------------------------------------- #
def backfill_contract(contract):
    """题目 output_contract 全部字段原样回填：缺值 null/[]，不删未知键。"""
    if not isinstance(contract, dict):
        return {}
    out = {}
    for k, v in contract.items():
        if v is None:
            out[k] = None
        elif isinstance(v, list):
            out[k] = []
        elif isinstance(v, dict):
            out[k] = backfill_contract(v)
        elif isinstance(v, str):
            out[k] = ""
        elif isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = None
        else:
            out[k] = None
    return out


def contract_missing_keys(contract):
    """对含 required 语义的契约，抽取需回填的键名集合（尽力而为）。"""
    keys = []
    if not isinstance(contract, dict):
        return keys
    if isinstance(contract.get("fields"), list):
        keys += [f for f in contract["fields"] if isinstance(f, str)]
    if isinstance(contract.get("required_fields"), list):
        keys += [f for f in contract["required_fields"] if isinstance(f, str)]
    if isinstance(contract.get("required"), list):
        keys += [f for f in contract["required"] if isinstance(f, str)]
    if isinstance(contract.get("properties"), dict):
        keys += list(contract["properties"].keys())
    return keys


def canonical_payload(result):
    """供 reproducibility fingerprint 使用：剔除时间戳类字段。"""
    skip = {"runtime", "change_log", "fingerprint", "reproducibility"}
    keys = [k for k in result if k not in skip]
    payload = {k: result[k] for k in sorted(keys)}
    txt = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(txt.encode("utf-8")).hexdigest()


def build_result(case_summary, work_dir, status="partial"):
    records = load_records(work_dir)
    plan_path = os.path.join(work_dir, "plan.json")
    plan, _ = read_json(plan_path, build_plan(case_summary))
    evidence = load_evidence(work_dir)
    change_log = load_change_log(work_dir)

    contracts = dict(case_summary.get("contract") or {})
    backfilled = backfill_contract(contracts)

    results = sort_records(records, (plan.get("rules") or {}).get("score_weights"))

    unverified = [r for r in results if not r.get("verified")]
    # 状态推断：有结果 -> ok（有降级则 partial）；无结果 -> insufficient_evidence。
    status = "ok" if results else "insufficient_evidence"
    if results and case_summary.get("degradations"):
        status = "partial"

    result = {
        "case_id": case_summary.get("case_id") or "unknown",
        "status": status,
        "research_question": case_summary.get("research_question"),
        "search_scope": {
            "input_files": case_summary.get("files", []),
            "contract_key": case_summary.get("contract_key"),
            "query_plan_applied": plan.get("research_question") is not None,
            "criteria": plan.get("rules"),
        },
        "results": results,
        "evidence": evidence,
        "sources": build_sources(results, evidence),
        "uncertainty": {
            "missing_metadata": missing_metadata(results),
            "conflicts": find_conflicts(results),
            "coverage_gaps": coverage_gaps(plan, results),
            "notes": list(case_summary.get("degradations") or []),
        },
        "status_notes": build_status_notes(results, case_summary, unverified),
        "limitations": build_limitations(case_summary, results, unverified),
        "pending_human": build_pending(results, unverified),
        "errors": build_errors(case_summary),
        "degradations": list(case_summary.get("degradations") or []),
        "change_log": change_log,
    }
    # 输出契约字段原样回填到 result 顶层（值 null/[]）。未知键名兜底也补齐。
    result.update(backfilled)
    missing = [k for k in contract_missing_keys(contracts) if k not in result]
    for k in missing:
        result[k] = None

    payload_hash = canonical_payload(result)
    result["reproducibility"] = {
        "fingerprint": payload_hash,
        "note": "主内容确定性指纹（剔除 runtime/change_log 时间戳）。",
    }
    result["runtime"] = {
        "work_dir": work_dir,
        "output_note": "时间戳为本次运行记录，不影响主内容复现。",
    }
    return result


def build_sources(results, evidence):
    out = []
    for r in results:
        db = r.get("database") or "unknown"
        url = r.get("url")
        doi = r.get("doi")
        if doi or url:
            out.append({"database": db, "doi": doi, "url": url})
    seen = set()
    unique = []
    for s in out:
        k = json.dumps(s, sort_keys=True, ensure_ascii=False)
        if k not in seen:
            seen.add(k)
            unique.append(s)
    return unique


def missing_metadata(results):
    out = []
    for r in results:
        miss = [k for k, v in (("title", r.get("title")), ("authors", r.get("authors")),
                               ("year", r.get("year")), ("journal", r.get("journal")),
                               ("doi", r.get("doi"))) if v in (None, "", [])]
        if miss:
            out.append({"title": r.get("title"), "missing": miss, "fingerprint": r.get("fingerprint")})
    return out


def find_conflicts(results):
    """简单置信冲突：同 title 不同 DOI 或不同 title 同 DOI。"""
    by_title, by_doi = {}, {}
    for r in results:
        t = title_fingerprint(r.get("title"))
        if t:
            by_title.setdefault(t, []).append(r)
        d = doi_fingerprint(r.get("doi"))
        if d:
            by_doi.setdefault(d, []).append(r)
    conflicts = []
    for t, grp in by_title.items():
        dois = {g.get("doi") for g in grp if g.get("doi")}
        if len(dois) > 1:
            conflicts.append({"kind": "title_with_multiple_dois", "values": sorted(str(x) for x in dois)})
    return conflicts[:50]


def coverage_gaps(plan, results):
    """低相关性占比、缺少证据的记录数等覆盖缺口说明。"""
    gaps = []
    if results:
        low = [r for r in results if r.get("score", 0) < 0.5]
        unver = [r for r in results if not r.get("verified")]
        if len(unver) == len(results) and results:
            gaps.append("全部结果缺少逐字证据（locator+quote），未标 verified")
        elif unver:
            gaps.append("%d/%d 条结果缺少逐字证据，已计入 uncertainty.missing_metadata" % (len(unver), len(results)))
    else:
        gaps.append("未召回任何候选，检查查询词与数据库覆盖")
    return gaps


def build_status_notes(results, case_summary, unverified):
    notes = []
    if not results:
        notes.append("候选为空：未召回任何可核验文献。")
    if unverified:
        notes.append("%d 条候选缺少逐字证据，已从 verified 降为未核验。" % len(unverified))
    if case_summary.get("degradations"):
        notes.append("输入解析存在降级，详见 uncertainties.notes 与 degradations。")
    if not notes:
        notes.append("结果已按规则排序并通过证据核验。")
    return notes


def build_limitations(case_summary, results, unverified):
    lim = []
    if not results:
        lim.append("未获得候选文献；可能因关键词覆盖不足、数据库不可达或输入不含可导入材料。")
    for d in case_summary.get("degradations") or []:
        lim.append("输入降级：" + d)
    if unverified:
        lim.append("%d 条候选证据不充分，未将其视为已核验结论。" % len(unverified))
    if not lim:
        lim.append("无阻断性局限；结果以当前输入与检索范围为准。")
    return lim


def build_pending(results, unverified):
    items = []
    for r in unverified:
        items.append({"title": r.get("title"), "why": "缺少逐字证据或元数据，需人工复核",
                      "fingerprint": r.get("fingerprint")})
    return items


def build_errors(case_summary):
    errs = []
    for d in case_summary.get("degradations") or []:
        if d.startswith(("bad_json", "read_failed", "no_input_dir", "walk_failed")):
            errs.append(d)
    if not case_summary.get("files"):
        errs.append("输入目录为空或不可读。")
    return errs


# --------------------------------------------------------------------------- #
# candidates.md 渲染
# --------------------------------------------------------------------------- #
def render_candidates_md(result):
    lines = []
    lines.append("# 候选文献清单（可编辑，供人工复核）")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- 生成说明：本文件由 result.json 渲染；表格中标 `[需复核]` 的条目缺少逐字证据或关键元数据。")
    lines.append("")
    results = result.get("results") or []
    if not results:
        lines.append("（无候选记录）")
    else:
        lines.append("| rank | score | title | year | journal | doi | 需复核 |")
        lines.append("|-|-|-|-|-|-|-|")
        for r in results:
            t = (r.get("title") or "(无标题)").replace("|", "\\|")
            j = (r.get("journal") or "-").replace("|", "\\|")
            d = r.get("doi") or ""
            if d:
                d = "`%s`" % d
            flag = "`[需复核]`" if not r.get("verified") else ""
            lines.append("| %d | %s | %s | %s | %s | %s | %s |" % (
                r.get("rank", 0), r.get("score", 0), t, r.get("year") or "-", j, d, flag))
    lines.append("")
    lines.append("## 证据摘录")
    for r in results:
        ev = r.get("evidence") or []
        if ev:
            lines.append("")
            lines.append("### %s" % (r.get("title") or "(无标题)"))
            for e in ev[:3]:
                lines.append("- locator: `%s`" % (e.get("locator") or ""))
                lines.append("  quote: %s" % (e.get("quote") or ""))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 骨架（先落盘合法 partial，再增量改进）
# --------------------------------------------------------------------------- #
def write_skeleton(output_dir, work_dir, case_summary):
    result = build_result(case_summary, work_dir, status="partial")
    # 有研究问题但尚未召回候选 -> 工作进行中，标记 partial；
    # 无研究问题（空输入/无材料）-> 保持 insufficient_evidence。
    if result.get("status") == "insufficient_evidence" and result.get("research_question"):
        result["status"] = "partial"
    ok = write_json(os.path.join(output_dir, "result.json"), result)
    w2 = write_text(os.path.join(output_dir, "candidates.md"), render_candidates_md(result))
    return ok and w2


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #
def selfcheck(output_dir, work_dir):
    checks = []
    result_path = os.path.join(output_dir, "result.json")
    obj, deg = read_json(result_path, None)
    checks.append(("result.json 存在且可解析", obj is not None and not deg))
    contracts = obj or {}
    for required in ("case_id", "status", "results", "evidence", "sources",
                     "uncertainty", "change_log", "reproducibility"):
        checks.append(("result.json 含字段 %s" % required, required in contracts))
    st = contracts.get("status")
    checks.append(("status 合法枚举", st in ("ok", "partial", "insufficient_evidence", "input_error")))
    reps = contracts.get("reproducibility") or {}
    checks.append(("含 reproducibility.fingerprint", bool(reps.get("fingerprint"))))
    for r in contracts.get("results", []):
        if not (r.get("evidence") or []):
            checks.append(("结果待核验（无证据）", False))
    candidates_path = os.path.join(output_dir, "candidates.md")
    cbytes = 0
    if os.path.isfile(candidates_path):
        cbytes = os.path.getsize(candidates_path)
    checks.append(("candidates.md 已生成", cbytes > 0))
    return checks