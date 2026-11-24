# -*- coding: utf-8 -*-
"""core_impl.py — 纯标准库：输入发现、文本解析、确定性抽取与契约回填。

职责边界：
- 本模块只做“可复算的机械工作”，产出 machine_candidate 与统计表，不做语义判断。
- 语义判断（趋势、主题演化、代表作、研究空白）由 Agent 依据 SKILL.md 在机械基线上核验并升级。
- 所有函数确定性：固定排序、固定正则、无随机数，重复运行结果一致。

被 main.py 与 selftest.py 共用。
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ------------------------------------------------------------------ 枚举常量
STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}
KIND_ENUM = {"origin", "method", "milestone", "benchmark"}
EVENT_ENUM = {"emergence", "merge", "split", "decline", "terminology_drift"}
GAP_TYPE_ENUM = {"open_question", "method_failure", "unresolved_conflict", "coverage_absence"}
SUPPORT_ENUM = {"reported", "not_reported", "inferred", "source_conflict"}
EVIDENCE_STATUS_ENUM = {"reported", "not_reported", "source_conflict", "inferred", "machine_candidate"}

# ------------------------------------------------------------------ 资源上限
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 200
ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "latin-1")

TEXT_EXTS = {".txt", ".md", ".markdown", ".text", ".jsonl", ".bib", ".ris"}
JSON_EXTS = {".json"}
PDF_EXTS = {".pdf"}

# ------------------------------------------------------------------ 正则
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")
GAP_RE = re.compile(
    r"\b(future work|remains?\s+(open|unclear|unknown|unresolved)|not yet|unknown|"
    r"open question|unresolved|limitation|needs?\s+(further|more)|warrants?\s+(further|more)|"
    r"poorly understood|understudied|little is known|to be (determined|elucidated|explored))\b",
    re.IGNORECASE,
)
CONFLICT_RE = re.compile(
    r"\b(however|nevertheless|in contrast|by contrast|versus|on the other hand|"
    r"disagreement|conflicts?|conflicting|discrepan\w+|whereas|contradict\w*|controversial)\b",
    re.IGNORECASE,
)
ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")

TITLE_KEYS = ("title", "name", "headline")
YEAR_KEYS = ("year", "publication_year", "pub_year", "published_year", "date", "published",
             "issued", "created", "publication_date", "pubdate")
CITED_KEYS = ("cited_by", "citation_count", "citations", "times_cited", "is_referenced_by_count")
TEXT_VALUE_KEYS = ("abstract", "description", "summary", "full_text", "fulltext", "body",
                   "content", "text", "claims", "notes")

STOPWORDS = frozenset(
    """the a an and or but if then else of to in on for with by at from as is are was were
    be been being this that these those it its it's i we you he she they them their our
    your his her my me us not no nor so such than too very can will just don't do does did
    have has had having about into over under again further once here there when where why
    how all any both each few more most other some only own same s t u v w x y z would
    should could may might must shall need new using use used based between during within
    among via per et al fig figure table etc e.g i.e""".split()
)


# ------------------------------------------------------------------ 时间
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ 输入发现
def discover_inputs(input_dir: str) -> dict:
    root = Path(input_dir)
    if not root.exists():
        return {"root": str(root), "exists": False, "files": [], "case_json": None, "task_md": None}
    files = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        files.append({
            "path": str(p),
            "rel": str(p.relative_to(root)).replace(os.sep, "/"),
            "size": st.st_size,
            "ext": p.suffix.lower(),
            "name": p.name,
        })
    case_json = _find_keyfile(files, "case.json")
    task_md = _find_keyfile(files, "task.md")
    return {"root": str(root), "exists": True, "files": files,
            "case_json": case_json, "task_md": task_md}


def _find_keyfile(files: list, name: str):
    exact = [f for f in files if f["name"].lower() == name]
    if exact:
        return exact[0]
    return next((f for f in files if f["name"].lower().startswith(name.split(".")[0])), None)


# ------------------------------------------------------------------ 文本读取
def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> dict:
    p = Path(path)
    try:
        size = p.stat().st_size if p.exists() else 0
    except OSError:
        size = 0
    if size > max_bytes:
        return {"path": str(p), "text": "", "encoding": None, "truncated": True, "skipped": True,
                "size": size, "error": "size %d > %d bytes" % (size, max_bytes)}
    try:
        raw = p.read_bytes()
    except OSError as e:
        return {"path": str(p), "text": "", "encoding": None, "truncated": False, "skipped": True,
                "size": size, "error": str(e)}
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            return {"path": str(p), "text": text, "encoding": enc, "truncated": False,
                    "skipped": False, "size": size, "error": None}
        except (UnicodeDecodeError, LookupError):
            continue
    text = raw.decode("utf-8", errors="replace")
    return {"path": str(p), "text": text, "encoding": "utf-8-replace", "truncated": False,
            "skipped": False, "size": size, "error": "decode fallback (errors=replace)"}


def load_json_safe(path: str) -> dict:
    rd = read_text_safe(path)
    if rd["skipped"] or rd["text"] == "":
        return {"obj": None, "error": rd["error"] or "empty", "encoding": rd["encoding"]}
    try:
        return {"obj": json.loads(rd["text"]), "error": None, "encoding": rd["encoding"]}
    except (ValueError, TypeError) as e:
        return {"obj": None, "error": str(e), "encoding": rd["encoding"]}


def write_json(path: str, obj) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ------------------------------------------------------------------ PDF 文本
def extract_pdf_text(path: str, work_dir: str) -> dict:
    exe = shutil.which("pdftotext")
    if not exe:
        return {"text": "", "ok": False, "error": "pdftotext not found"}
    out = Path(work_dir) / "_pdftotext_tmp.txt"
    try:
        cp = subprocess.run([exe, "-layout", str(path), str(out)],
                            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"text": "", "ok": False, "error": str(e)}
    if cp.returncode != 0:
        err = (cp.stderr or b"").decode("utf-8", "replace")[:300]
        return {"text": "", "ok": False, "error": err or "returncode %d" % cp.returncode}
    text = ""
    if out.exists():
        try:
            text = out.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return {"text": text, "ok": True, "error": None}


# ------------------------------------------------------------------ 记录拆分
def json_records(obj):
    if isinstance(obj, dict):
        return [obj]
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []


def record_text(record) -> str:
    if not isinstance(record, dict):
        return str(record)
    parts = []
    for k in TEXT_VALUE_KEYS:
        v = record.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, (list, tuple)):
            parts.extend(str(x) for x in v if isinstance(x, (str, int, float)))
    if not parts:
        parts.append(json.dumps(record, ensure_ascii=False, default=str))
    return "\n".join(parts)


# ------------------------------------------------------------------ 元数据抽取
def _year_from_value(v):
    m = YEAR_RE.search(str(v))
    return int(m.group(1)) if m else None


def record_year(text: str, record) -> dict:
    if isinstance(record, dict):
        for k in YEAR_KEYS:
            v = record.get(k)
            if v not in (None, "", [], {}):
                y = _year_from_value(v)
                if y:
                    return {"year": y, "source": "metadata", "confidence": "high"}
    if text:
        head = text[:4000]
        years = [int(y) for y in YEAR_RE.findall(head)]
        if years:
            y, _ = Counter(years).most_common(1)[0]
            return {"year": y, "source": "citation", "confidence": "medium"}
    return {"year": None, "source": None, "confidence": "low"}


def record_title(text: str, record) -> str | None:
    if isinstance(record, dict):
        for k in TITLE_KEYS:
            v = record.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:400]
    if text.strip():
        first = text.strip().splitlines()[0] if text.strip().splitlines() else ""
        return first[:400] if first else None
    return None


def record_cited(record):
    if not isinstance(record, dict):
        return None
    for k in CITED_KEYS:
        v = record.get(k)
        if v not in (None, "", [], {}):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    return None


# ------------------------------------------------------------------ 术语与句子
def tokenize_terms(text: str) -> list:
    terms = []
    ascii_tokens = [t.lower() for t in ASCII_TOKEN_RE.findall(text)]
    terms.extend(t for t in ascii_tokens if t not in STOPWORDS)
    for a, b in zip(ascii_tokens, ascii_tokens[1:]):
        if a not in STOPWORDS and b not in STOPWORDS:
            terms.append("%s %s" % (a, b))
    for run in CJK_RE.findall(text):
        for i in range(len(run) - 1):
            terms.append(run[i:i + 2])
        if len(run) >= 4:
            for i in range(len(run) - 3):
                terms.append(run[i:i + 4])
    return terms


def split_sentences(text: str) -> list:
    lines = text.splitlines()
    out = []
    cur = []
    start = 1
    for i, line in enumerate(lines, 1):
        parts = SENT_SPLIT_RE.split(line)
        if len(parts) == 1:
            cur.append(parts[0])
        else:
            for j, part in enumerate(parts):
                if j == 0:
                    cur.append(part)
                else:
                    out.append((" ".join(cur).strip(), start, i))
                    cur = [part]
                    start = i
    if cur:
        out.append((" ".join(cur).strip(), start, len(lines)))
    return [(s, a, b) for (s, a, b) in out if s.strip()]


def extract_candidates(sentences: list) -> tuple:
    gaps, conflicts = [], []
    for sent, ls, le in sentences:
        if GAP_RE.search(sent):
            gaps.append({"text": sent, "line_start": ls, "line_end": le})
        if CONFLICT_RE.search(sent):
            conflicts.append({"text": sent, "line_start": ls, "line_end": le})
    return gaps, conflicts


# ------------------------------------------------------------------ 定位与归一
def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def locate_quote(text: str, quote: str) -> tuple:
    """在原文中定位逐字 quote，返回 (line_start, line_end)；找不到返回 (None, None)。"""
    norm_quote = normalize_text(quote)
    if not norm_quote:
        return None, None
    lines = text.splitlines()
    norm_lines = [normalize_text(l) for l in lines]
    # 先整行匹配，再跨行滑动窗口
    for i, nl in enumerate(norm_lines, 1):
        if norm_quote in nl:
            return i, i
    joined = normalize_text(text)
    idx = joined.find(norm_quote)
    if idx < 0:
        return None, None
    # 把字符索引映回行号（近似：按累计行长）
    acc = 0
    start = end = None
    for i, l in enumerate(lines, 1):
        nl = normalize_text(l)
        span = len(nl) + (1 if nl else 0)
        if start is None and acc + span > idx:
            start = i
        if start is not None and acc + span >= idx + len(norm_quote):
            end = i
            break
        acc += span
    return start, end


# ------------------------------------------------------------------ 契约回填
def merge_contract(case_obj) -> tuple:
    """返回 (contract_dict, contract_keys)。case.json 无 output_contract 时返回空。"""
    if not isinstance(case_obj, dict):
        return {}, []
    oc = case_obj.get("output_contract")
    if isinstance(oc, dict):
        return dict(oc), sorted(oc.keys())
    return {}, []


def case_id_from(case_obj, task_text: str) -> str:
    if isinstance(case_obj, dict):
        for k in ("case_id", "id", "challenge_id"):
            v = case_obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        m = re.search(r"challenge[-_]id[:：]\s*[`\"']?([A-Za-z0-9\-_]+)", task_text)
        if m:
            return m.group(1)
    return "unknown"


# ------------------------------------------------------------------ CSV
def write_csv(path: str, fieldnames: list, rows: list) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
