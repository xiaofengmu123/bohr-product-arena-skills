#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 标准库纯实现的共享工具层。

职责：输入发现、编码容错、原子写盘、契约骨架、确定性机械抽取
（年份 / 标题 / 被引 / 术语种子 / 空白候选 / 冲突候选 / 代表作候选）。
无任何第三方依赖；路径全部经 pathlib 归一，同一代码可在 Linux
Harness（/input、/app/output、/app/work）与 Windows 开发机运行。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SKILL_NAME = "research-frontier-trend-checkpoint"
SKILL_VERSION = "1.1.0"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件 2MB 上限
MAX_FILES = 200                    # 输入文件数上限（超出采样并记录降级）

# 枚举值（selftest 校验时使用，与 references/contract.md 保持一致）
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
SUPPORT_VALUES = ("reported", "not_reported", "inferred")
EVIDENCE_KIND_VALUES = ("reported", "not_reported", "source_conflict", "inferred")
CONFIDENCE_VALUES = ("high", "medium", "low")
GAP_TYPE_VALUES = ("open_question", "method_failure", "unresolved_conflict", "coverage_absence")
EVENT_TYPE_VALUES = ("emergence", "merge", "split", "decline", "terminology_drift")
WORK_KIND_VALUES = ("origin", "method", "milestone", "benchmark")

FIXED_KEYS = [
    "case_id", "status", "research_question", "protocol", "time_trend",
    "topic_evolution", "representative_works", "research_gaps", "evidence_map",
    "conflicts", "sources", "uncertainty", "pending_human", "change_log",
    "limitations", "errors", "degradations", "runtime",
]

TEXT_EXTS = {".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".xml",
             ".tex", ".bib", ".yaml", ".yml", ".rst", ".html", ".htm"}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


# ---------------------------------------------------------------------------
# 时间 / 文件 I/O
# ---------------------------------------------------------------------------
def now_iso() -> str:
    """UTC ISO 时间戳（仅用于 change_log / runtime，不参与分析内容）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def read_text_smart(path, max_bytes: int = MAX_FILE_BYTES):
    """按 utf-8-sig → utf-8 → gb18030 → latin-1 顺序容错读取。

    返回 (text, encoding, truncated)。单文件超限时截断读取并标 truncated。
    """
    p = Path(path)
    size = p.stat().st_size if p.exists() else 0
    truncated = size > max_bytes
    raw = p.read_bytes() if not truncated else p.read_bytes()[:max_bytes]
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), enc, truncated
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace", truncated


def load_json(path, default=None):
    """读取 JSON；坏 JSON / 缺失返回 default，不抛异常。"""
    p = Path(path)
    if not p.exists():
        return default
    text, _enc, _tr = read_text_smart(p)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return default


def write_json_atomic(path, obj, indent: int = 2):
    """先写临时文件再 os.replace，保证中断时旧版本仍完整可读。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, p)
    return str(p)


def deep_merge(base, patch):
    """字典递归合并；列表 / 标量整体替换（列表不做增量拼接，避免残留机器候选）。"""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    for key, val in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            base[key] = deep_merge(base[key], val)
        else:
            base[key] = val
    return base


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------
def scan_input(input_dir, max_files: int = MAX_FILES):
    """扫描输入目录，返回 (entries, skipped, degradations)。

    不假设固定文件名；把文件分类为 text / pdf / office / other。
    超文件数上限按排序采样并记录降级，超 2MB 的单个文件跳过并记录。
    """
    root = Path(input_dir)
    files = [p for p in root.rglob("*")
             if p.is_file() and not p.name.startswith(".")]
    files.sort(key=lambda p: str(p).lower())
    degradations = []
    if len(files) > max_files:
        degradations.append(
            "input has %d files > cap %d; sampled first %d" % (len(files), max_files, max_files))
        files = files[:max_files]
    entries, skipped = [], []
    for p in files:
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            skipped.append({"path": str(p), "reason": "exceeds_2MB_cap", "size": size})
            continue
        ext = p.suffix.lower()
        if ext in PDF_EXTS:
            kind = "pdf"
        elif ext in OFFICE_EXTS:
            kind = "office"
        elif ext in TEXT_EXTS:
            kind = "text"
        else:
            kind = "other"
        entries.append({
            "path": str(p),
            "rel": str(p.relative_to(root)).replace("\\", "/"),
            "size": size,
            "ext": ext or "(none)",
            "kind": kind,
        })
    return entries, skipped, degradations


def extract_case_meta(case_obj, task_text):
    """从 case.json 与 task.md 抽取 case_id / output_contract / 问题提示。"""
    case_id = None
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id", "challengeId", "caseId"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                case_id = val.strip()
                break
    if not case_id and task_text:
        m = re.search(r"challenge[_\-]?id[:：]\s*[`\"']?([A-Za-z0-9\-_]+)", task_text, re.I)
        if m:
            case_id = m.group(1)
    contract = None
    if isinstance(case_obj, dict):
        for key in ("output_contract", "output_schema", "output"):
            val = case_obj.get(key)
            if isinstance(val, dict):
                contract = val
                break
    question = None
    if isinstance(case_obj, dict):
        for key in ("research_question", "question", "prompt", "task"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                question = val.strip()
                break
    return case_id or "unknown", contract, question


# ---------------------------------------------------------------------------
# 契约骨架
# ---------------------------------------------------------------------------
def build_skeleton(case_id, contract=None, question=None):
    """构建可交付的 partial 骨架：先原样回填 output_contract 全部字段，
    再补齐固定字段；缺值给 null/[]，不改名、不删未知键。"""
    result = {}
    if isinstance(contract, dict):
        for key, val in contract.items():
            result[key] = val
    fixed = {
        "case_id": case_id,
        "status": "partial",
        "research_question": {
            "question": question or "",
            "scope": "",
            "sub_questions": [],
            "domain_note": "",
        },
        "protocol": {
            "search_scope": "",
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "date_definition_note": "",
            "dedup_note": "",
        },
        "time_trend": {"unit_note": "", "distribution": [], "windows": [], "claims": []},
        "topic_evolution": {"stage_divisions": [], "term_mapping": [], "events": [], "claims": []},
        "representative_works": {"criteria": [], "stages": []},
        "research_gaps": {"gaps": [], "excluded_not_gaps": []},
        "evidence_map": [],
        "conflicts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": now_iso(), "finished_at": None,
            "elapsed_s": None, "requests_used": None, "phases": [],
        },
    }
    for key, val in fixed.items():
        result.setdefault(key, val)
    result.setdefault("_meta", {
        "skill": SKILL_NAME, "version": SKILL_VERSION,
        "revision": 0, "machine": True,
    })
    return result


def append_change(result, action, reason, before="", after=""):
    entry = {"time": now_iso(), "action": action, "reason": reason,
             "before": before, "after": after}
    result.setdefault("change_log", []).append(entry)
    return entry


def bump_revision(result, action, reason, before="", after=""):
    meta = result.setdefault("_meta", {})
    meta["revision"] = int(meta.get("revision", 0)) + 1
    append_change(result, action, reason, before, after)
    return meta["revision"]


def derive_status(result):
    """仅用于 finalize 兜底：四类产物齐 + evidence_map 非空 → ok，
    任一类有内容 → partial，否则 insufficient_evidence。"""
    tt = result.get("time_trend") or {}
    te = result.get("topic_evolution") or {}
    rw = result.get("representative_works") or {}
    rg = result.get("research_gaps") or {}
    em = result.get("evidence_map") or []
    has_trend = bool(tt.get("distribution") or tt.get("claims"))
    has_evo = bool(te.get("stage_divisions") or te.get("events"))
    has_repr = bool(rw.get("stages") or rw.get("criteria"))
    has_gap = bool(rg.get("gaps"))
    products = [has_trend, has_evo, has_repr, has_gap]
    if all(products) and em:
        return "ok"
    if any(products):
        return "partial"
    return "insufficient_evidence"


# ---------------------------------------------------------------------------
# 确定性机械抽取（只产“候选/原始统计”，不产结论；结论由 Agent 核验后写入）
# ---------------------------------------------------------------------------
YEAR_RE = re.compile(r"\b(19[0-9]{2}|20[0-9]{2})\b")


def _coerce_year(value):
    if isinstance(value, int):
        return value if 1900 <= value <= 2100 else None
    if isinstance(value, str):
        m = re.search(r"(19|20)\d{2}", value)
        if m:
            y = int(m.group(0))
            return y if 1900 <= y <= 2100 else None
    return None


def extract_year(path, text, metadata=None):
    """年份来源优先级：metadata > 文件名 > 文首 4000 字符。

    返回 (year, source)；找不到返回 (None, 'missing')。文本命中仅作低置信候选。
    """
    if isinstance(metadata, dict):
        for key in ("year", "pub_year", "publication_year", "date"):
            y = _coerce_year(metadata.get(key))
            if y:
                return y, "metadata.%s" % key
    p = Path(path)
    y = _coerce_year(p.name)
    if y:
        return y, "filename"
    head = (text or "")[:4000]
    m = re.search(r"(19|20)\d{2}", head)
    if m:
        y = int(m.group(0))
        if 1900 <= y <= 2100:
            return y, "text_head"
    return None, "missing"


def extract_title(path, text, metadata=None):
    if isinstance(metadata, dict):
        for key in ("title", "name"):
            val = metadata.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if text:
        for line in text.splitlines():
            line = re.sub(r"^\s*(#{1,6}|>\s*|\*\s*|-\s*)", "", line).strip()
            if 3 <= len(line) <= 300:
                return line
    p = Path(path)
    stem = p.stem.replace("_", " ").replace("-", " ").strip()
    return stem if stem else p.name


CITED_RE = re.compile(r"(?:cited by|citations?|被引|引用)\s*[:：]?\s*(\d+)", re.I)


def extract_citations(text, metadata=None):
    if isinstance(metadata, dict):
        for key in ("citations", "cited_by", "citation_count", "times_cited"):
            val = metadata.get(key)
            if isinstance(val, int) and val >= 0:
                return val
    m = CITED_RE.search(text or "")
    if m:
        return int(m.group(1))
    return None


EN_STOP = set((
    "the and of to in for on with by at from as is are was were been being be it its "
    "this that these those not no but if then than so such can may will would should "
    "could about into over after before between out up down under again further during "
    "because through while both each few more most other some only own same too very "
    "also however therefore thus which who whom whose they we you he she"
).split())

WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{3,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,6}")


def tokenize_terms(text, top_n=40):
    """抽取英文词与中文 2–6 字串的频率种子（原始候选，非主题结论）。"""
    if not text:
        return []
    counter = Counter()
    for w in WORD_RE.findall(text):
        wl = w.lower()
        if wl in EN_STOP:
            continue
        counter[wl] += 1
    for s in CJK_RE.findall(text):
        counter[s] += 1
    ranked = counter.most_common(top_n)
    return [{"term": t, "count": c} for t, c in ranked]


GAP_PATTERNS = re.compile(
    r"future work|remains? (unclear|unknown|open)|open question|not yet (been|fully|addressed)"
    r"|little is known|needs? (further|more)|warrants? (further|more)"
    r"|under[- ]?(explored|studied|investigated)|unresolved|remain[s]? to be"
    r"|limitation[s]? of|further (research|study|investigation|work)"
    r"|more (research|studies) (is|are) needed|still (lack|unclear|unknown)|not been addressed"
    r"|未来工作|有待|尚不明确|仍不清楚|仍然未知|缺乏|空白|需要进一步|亟待|未解决|尚无|尚需|仍需",
    re.I,
)

CONFLICT_PATTERNS = re.compile(
    r"however|in contrast|on the contrary|versus|discrepan|conflict|inconsisten|disagree|contradict"
    r"|challenge[sd]? (the|this)|debate[sd]?|mixed (results|evidence)"
    r"|然而|但是|相反|矛盾|不一致|争议|分歧|相比之下|与此相反",
    re.I,
)


def find_marked_lines(text, pattern, max_lines=40):
    """按行扫描命中句（保留行号便于 locator 回链）。"""
    out = []
    for i, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if pattern.search(line):
            out.append({"line": i, "text": line[:500]})
            if len(out) >= max_lines:
                break
    return out


def extract_document(doc, text, metadata=None):
    path = doc.get("original") or doc.get("path") or ""
    year, year_source = extract_year(path, text, metadata)
    return {
        "doc_id": doc.get("doc_id", ""),
        "original": path,
        "title": extract_title(path, text, metadata),
        "year": year,
        "year_source": year_source,
        "citations": extract_citations(text, metadata),
        "chars": len(text) if text else 0,
        "terms": tokenize_terms(text),
        "gap_lines": find_marked_lines(text, GAP_PATTERNS),
        "conflict_lines": find_marked_lines(text, CONFLICT_PATTERNS),
    }


def year_distribution(docs):
    known = [d for d in docs if isinstance(d.get("year"), int)]
    buckets = {}
    for d in known:
        y = d["year"]
        b = buckets.setdefault(y, {"year": y, "count": 0, "doc_ids": []})
        b["count"] += 1
        b["doc_ids"].append(d["doc_id"])
    return [buckets[y] for y in sorted(buckets)]


def representative_candidates(docs):
    """按年份分桶，桶内按被引降序（未知被引排后），标 rank 供 Agent 核验。"""
    by_year = {}
    for d in docs:
        y = d.get("year") if isinstance(d.get("year"), int) else "unknown"
        by_year.setdefault(y, []).append(d)
    order = sorted(by_year.keys(), key=lambda y: (y == "unknown", -1 if y == "unknown" else y))
    stages = []
    for y in order:
        group = sorted(by_year[y], key=lambda d: (
            -(d["citations"] if isinstance(d.get("citations"), int) else -1), d["doc_id"]))
        stages.append({
            "year": y,
            "candidates": [
                {"doc_id": d["doc_id"], "title": d["title"], "year": d["year"],
                 "citations": d["citations"], "rank": i + 1, "kind": None}
                for i, d in enumerate(group[:5])
            ],
        })
    return stages


def content_hash(obj):
    """对分析内容（剔除 change_log / runtime 时间戳）做稳定哈希，供重复运行一致性核对。"""
    def strip_times(o):
        if isinstance(o, dict):
            return {k: strip_times(v) for k, v in o.items() if k not in ("time", "started_at", "finished_at")}
        if isinstance(o, list):
            return [strip_times(v) for v in o]
        return o
    payload = json.dumps(strip_times(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
