#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献检索 Skill 的纯函数内核。

本模块只做确定性的数据变换：规范化、去重、打分、排序、契约回填与
确定性指纹。不读文件、不写文件、不打印、不取系统时间，因此同一份输入
（含工作区 records.json / evidence.json 的状态）在任何机器、任何时区、
任何 Python 3.8+ 上都会得到相同的 assemble 产物。

设计取舍（相对常见实现）：
- 时间戳一律用单调序列 seq 表达，不用墙钟时间，从源头保证可复现；
- 排序的并列次序由“规范化标题 + 原始导入顺序”二次排序，杜绝 set/dict
  遍历顺序导致的抖动；
- 打分权重、来源权重表为模块级常量，评审可直接对照复核。
"""

import hashlib
import json
import re
import unicodedata

# ---------------------------------------------------------------------------
# 常量表（确定性，不随运行变化）
# ---------------------------------------------------------------------------

# 来源可靠性权重：同一指标多库不一致时记录 source_conflict，不擅自取舍；
# 这里只用于评分，不是对数据库的事实断言。
SOURCE_RELIABILITY = {
    "crossref": 1.00,
    "doi": 1.00,
    "europepmc": 0.95,
    "pubmed": 0.95,
    "semantic_scholar": 0.90,
    "publisher": 0.90,
    "openalex": 0.88,
    "arxiv": 0.85,
    "paper_search": 0.85,
    "materials": 0.80,
    "manual": 0.70,
    "web_search": 0.60,
    "unknown": 0.50,
}

# 打分权重：0.6 相关性 + 0.2 元数据完整度 + 0.2 来源可靠性。
SCORE_WEIGHTS = {"relevance": 0.6, "metadata": 0.2, "source": 0.2}

# 题目 output_contract 常见键名（先发现，再假设；未知键不丢弃）。
CONTRACT_KEY_NAMES = (
    "output_contract", "outputContract", "output", "result_schema", "schema",
    "required_output", "required_fields", "expected_fields", "contract",
)

# 概念拆分的停用词（仅用于生成查询计划的种子，Agent 在运行期可再扩展）。
_TOPIC_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "with", "to",
    "by", "from", "using", "based", "study", "studies", "research", "method",
    "methods", "approach", "analysis", "review", "reviews", "literature",
    "关于", "研究", "方法", "及其", "在", "的", "和", "与", "及", "对",
}

_CONCEPT_SPLIT_RE = re.compile(
    r"[\s,，、;；:：/|+&|\(\)（）\[\]【】「」《》\"'‘’“”?.！!。]+"
)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def clamp01(x):
    """把数值夹到 [0, 1]。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.5
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def stable_json(obj):
    """确定性 JSON 序列化：键排序、紧凑分隔、禁用非 ASCII 转义。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def content_hash(obj):
    """对任意对象做内容指纹（sha256），只依赖内容、不依赖环境。"""
    return hashlib.sha256(stable_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 规范化与去重
# ---------------------------------------------------------------------------

def _fold_unicode(s):
    """NFKD 折叠，去掉组合字符，用于标题归一化（不影响中文）。"""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def normalize_title(title):
    """标题归一化：小写、去变音符、仅保留字母数字与 CJK，作为去重键。"""
    if not isinstance(title, str):
        return ""
    s = _fold_unicode(title).lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s


def normalize_doi(doi):
    """DOI 归一化：去前缀、去空白、小写。"""
    if not isinstance(doi, str):
        return ""
    s = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s.strip()


def canonical_authors(authors):
    """作者归一化：列表取非空字符串；字符串按逗号/分号拆开。"""
    if isinstance(authors, (list, tuple)):
        return [str(a).strip() for a in authors if str(a).strip()]
    if isinstance(authors, str) and authors.strip():
        return [a.strip() for a in re.split(r"[,;；，]", authors) if a.strip()]
    return []


def canonical_year(year):
    """年份归一化：int/str 均可，解析出四位数年份，失败返回 None。"""
    if isinstance(year, bool):
        return None
    if isinstance(year, int):
        return year if 1000 <= year <= 9999 else None
    if isinstance(year, str):
        m = re.search(r"(1[89]\d{2}|20\d{2})", year)
        return int(m.group(1)) if m else None
    return None


def dedup_key(record):
    """去重主键：有 DOI 用 DOI，否则用规范化标题，都没有用内容指纹。"""
    doi = normalize_doi(record.get("doi"))
    if doi:
        return ("doi", doi)
    title = normalize_title(record.get("title"))
    if title:
        return ("title", title)
    return ("content", content_hash(record))


def merge_record(old, new):
    """合并两条同键记录：空字段不回填，作者/证据做确定性合并。"""
    merged = dict(old) if isinstance(old, dict) else {}

    def present(v):
        return v not in (None, "", [], {})

    for key in ("title", "year", "journal", "doi", "url", "database",
                "relevance", "relevance_reason", "evidence", "locator",
                "quote"):
        if present(new.get(key)) and not present(merged.get(key)):
            merged[key] = new[key]

    # 作者：取更完整的一方（条数多者优先，确定性）
    old_a = canonical_authors(old.get("authors")) if isinstance(old, dict) else []
    new_a = canonical_authors(new.get("authors"))
    if len(new_a) > len(old_a):
        merged["authors"] = new_a
    elif not old_a and new_a:
        merged["authors"] = new_a
    elif old_a:
        merged["authors"] = old_a

    # evidence：合并 locator/quote 的碎片
    ev = dict(merged.get("evidence") or {})
    for piece in (new.get("evidence"), new):
        if isinstance(piece, dict):
            for k in ("locator", "quote"):
                if present(piece.get(k)) and not present(ev.get(k)):
                    ev[k] = piece[k]
    if ev:
        merged["evidence"] = ev
    return merged


def dedup_records(records):
    """按 dedup_key 去重，保留首次出现顺序；返回去重后的记录列表。"""
    merged = {}
    order = []
    for rec in (records or []):
        if not isinstance(rec, dict):
            continue
        key = dedup_key(rec)
        if key not in merged:
            merged[key] = {}
            order.append(key)
        merged[key] = merge_record(merged[key], rec)
    return [merged[k] for k in order]


# ---------------------------------------------------------------------------
# 打分与排序（确定性）
# ---------------------------------------------------------------------------

def relevance_value(record):
    """相关性：Agent 在 import 时给出 0..1；缺失按 0.5 中性处理。"""
    v = record.get("relevance")
    if v in (None, ""):
        return 0.5
    if isinstance(v, bool):
        return 0.5
    return clamp01(v)


def metadata_completeness(record):
    """元数据完整度 = 标题/作者/年份/期刊/DOI 五字段的命中比例。"""
    hit = 0
    for key in ("title", "authors", "year", "journal", "doi"):
        v = record.get(key)
        if v not in (None, "", [], {}):
            hit += 1
    return hit / 5.0


def source_reliability(database):
    """来源可靠性权重（查常量表，未知回退 unknown）。"""
    key = (database or "unknown").lower().strip().replace("-", "_")
    return SOURCE_RELIABILITY.get(key, SOURCE_RELIABILITY["unknown"])


def score_record(record):
    """单条打分：0.6·relevance + 0.2·metadata + 0.2·source，保留 4 位。"""
    rel = relevance_value(record)
    meta = metadata_completeness(record)
    src = source_reliability(record.get("database"))
    score = (SCORE_WEIGHTS["relevance"] * rel
             + SCORE_WEIGHTS["metadata"] * meta
             + SCORE_WEIGHTS["source"] * src)
    return round(score, 4)


def rank_records(records):
    """排序：score 降序；并列按规范化标题升序，再按原始顺序，保证稳定。"""
    scored = []
    for i, rec in enumerate(records or []):
        item = dict(rec)
        item["_order"] = i
        scored.append(item)
    scored.sort(key=lambda r: (
        -score_record(r),
        normalize_title(r.get("title") or ""),
        r["_order"],
    ))
    out = []
    for rank, r in enumerate(scored, 1):
        r = {k: v for k, v in r.items() if k != "_order"}
        r["score"] = score_record(r)
        r["rank"] = rank
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 输入解析（先发现，再假设）
# ---------------------------------------------------------------------------

def extract_case_id(case_data, task_text="", case_path=None):
    """从 case.json / task.md 提取 case_id，多重回退，不抛异常。"""
    if isinstance(case_data, dict):
        for key in ("case_id", "caseId", "challenge_id", "challengeId",
                    "id", "armiId", "challengeId", "armId"):
            v = case_data.get(key)
            if v not in (None, ""):
                return str(v)
    m = re.search(r"(?:case_id|challenge_id|challengeId)\s*[:：]\s*`?([A-Za-z0-9_\-]+)",
                  task_text or "")
    if m:
        return m.group(1)
    if case_path:
        import os
        return "case:" + os.path.splitext(os.path.basename(case_path))[0]
    return "unknown"


def extract_research_question(case_data, task_text=""):
    """提取研究问题，优先结构化字段，其次题面正则，最后空字符串。"""
    if isinstance(case_data, dict):
        for key in ("research_question", "researchQuestion", "question",
                    "query", "topic", "task", "problem", "research_topic",
                    "research_problem", "title"):
            v = case_data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    m = re.search(
        r"(?:研究问题|科研问题|研究主题|Research question|Research topic|Research Question)\s*[:：]\s*(.+)",
        task_text or "")
    if m:
        return m.group(1).strip()[:2000]
    return ""


def extract_contract(case_data):
    """抽取题目 output_contract 的全部字段（原样回填），返回 dict。"""
    out = {}
    if not isinstance(case_data, dict):
        return out
    for name in CONTRACT_KEY_NAMES:
        v = case_data.get(name)
        if v is None:
            continue
        if isinstance(v, dict):
            for k, val in v.items():
                out.setdefault(k, val)
        elif isinstance(v, list):
            if name in ("required_fields", "expected_fields"):
                for k in v:
                    out.setdefault(str(k), None)
            else:
                out.setdefault(name, list(v))
        else:
            out.setdefault(name, v)
    return out


# ---------------------------------------------------------------------------
# 查询计划（确定性种子，Agent 在运行期可扩展）
# ---------------------------------------------------------------------------

def split_concepts(text, max_concepts=5):
    """把研究问题拆成核心概念片段，作为查询计划种子。"""
    if not isinstance(text, str) or not text.strip():
        return []
    parts = _CONCEPT_SPLIT_RE.split(text)
    out = []
    seen = set()
    for p in parts:
        p = p.strip()
        if len(p) < 2:
            continue
        key = p.casefold()
        if key in _TOPIC_STOPWORDS or key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= max_concepts:
            break
    return out


_EXPANSION_CHECKLIST = [
    "同义词（synonym）",
    "缩写与全称（abbreviation / full form）",
    "跨语言表达（中英文术语对译）",
    "上位/下位概念（hypernym / hyponym）",
    "近义方法名与研究对象别名",
]


def build_plan(research_question):
    """生成确定性查询计划：单概念 → AND 组合 → 引文/回溯。"""
    concepts = split_concepts(research_question)
    queries = []
    for c in concepts:
        queries.append({
            "id": "q-single-%d" % (len(queries) + 1),
            "terms": [c],
            "op": "single",
            "purpose": "召回核心概念“%s”的相关文献" % c,
            "expansion_checklist": list(_EXPANSION_CHECKLIST),
        })
    # 两两 AND（上限 6 对，控制请求量）
    pair_count = 0
    for i in range(len(concepts)):
        for j in range(i + 1, len(concepts)):
            if pair_count >= 6:
                break
            queries.append({
                "id": "q-and-%d" % (len(queries) + 1),
                "terms": [concepts[i], concepts[j]],
                "op": "AND",
                "purpose": "用 AND 组合“%s”与“%s”提高相关性" % (concepts[i], concepts[j]),
                "expansion_checklist": list(_EXPANSION_CHECKLIST),
            })
            pair_count += 1
    if len(concepts) >= 2:
        queries.append({
            "id": "q-full-%d" % (len(queries) + 1),
            "terms": list(concepts),
            "op": "AND",
            "purpose": "全概念 AND，控制无关结果、锁定主题边界",
            "expansion_checklist": list(_EXPANSION_CHECKLIST),
        })
    queries.append({
        "id": "q-snowball-%d" % (len(queries) + 1),
        "terms": [],
        "op": "citation",
        "purpose": "由已召回高相关文献的参考文献与引证关系补充关键文献",
        "expansion_checklist": [],
    })
    return {
        "concepts": concepts,
        "queries": queries,
        "keyword_expansion_checklist": list(_EXPANSION_CHECKLIST),
        "dedup_rule": "DOI 优先，缺失时按规范化标题去重；同键合并保留非空字段",
        "scoring_rule": "0.6·relevance + 0.2·metadata_completeness + 0.2·source_reliability",
        "note": "本计划为确定性种子；运行期 Agent 可在同一 evidence 记录中追加实际执行过的扩展查询。",
    }


# ---------------------------------------------------------------------------
# 状态判断与指纹
# ---------------------------------------------------------------------------

def decide_status(records):
    """按证据完整度诚实判定 status。"""
    if not records:
        return "insufficient_evidence"
    ok_all = True
    for r in records:
        ev = r.get("evidence") if isinstance(r.get("evidence"), dict) else {}
        has_source = bool(r.get("title")) and bool(r.get("doi") or r.get("url"))
        has_evidence = bool(ev.get("locator")) and bool(ev.get("quote"))
        has_reason = bool(r.get("relevance_reason"))
        if not (has_source and has_evidence and has_reason):
            ok_all = False
            break
    return "ok" if ok_all else "partial"


def build_fingerprint(result_obj, excluded_fields=()):
    """对确定性字段做内容指纹；excluded_fields 可显式剔除墙钟类字段。"""
    clone = {k: v for k, v in result_obj.items() if k not in excluded_fields}
    return content_hash(clone)


def merge_sources(records, evidence):
    """由候选记录与证据记录汇总唯一来源（确定性排序）。"""
    sources = []
    seen = set()
    for r in (records or []):
        db = (r.get("database") or "unknown").strip()
        key = db.lower()
        if key not in seen:
            seen.add(key)
            sources.append({"database": db,
                            "type": "candidate_record",
                            "reliability": source_reliability(db)})
    for e in (evidence or []):
        if not isinstance(e, dict):
            continue
        src = (e.get("source") or "").strip()
        if not src:
            continue
        key = ("tool", src.lower())
        if key not in seen:
            seen.add(key)
            sources.append({"database": e.get("database") or src,
                            "source": src,
                            "type": "query_tool",
                            "reliability": source_reliability(e.get("database"))})
    sources.sort(key=lambda s: stable_json(s))
    return sources
