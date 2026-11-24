"""Deterministic extraction helpers for frontier-trend-review Skill.

Standard library only. Every function is pure/deterministic: same input
produces the same output (no randomness, sorted iteration, fixed seeds).
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

TEXT_ENCODINGS = ("utf-8-sig", "gbk", "latin-1")
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 200
TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".json", ".jsonl", ".yaml", ".yml", ".tex", ".bib", ".tsv")
PDF_SUFFIXES = (".pdf",)

YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)")
YEAR_LABEL_RE = re.compile(
    r"(?:year|published|pub(?:lication)?\s+year|date|见刊|发表|出版|年份)\s*[:：]?\s*(19[5-9]\d|20[0-3]\d)",
    re.IGNORECASE,
)
CITE_RE = re.compile(r"(?:cited\s+by|citations?|被引|引用)\s*[:：]?\s*(\d[\d,]*)", re.IGNORECASE)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")

STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "with", "that", "this", "from",
    "have", "has", "had", "not", "but", "can", "all", "their", "which", "will",
    "than", "been", "into", "more", "such", "they", "these", "those", "also",
    "using", "used", "based", "between", "however", "through", "under", "over",
    "about", "above", "below", "where", "when", "while", "each", "other", "our",
    "its", "his", "her", "him", "she", "them", "you", "your", "upon", "within",
}

GAP_PATTERNS = [
    (r"(?i)\b(future\s+work|further\s+(?:research|study|work|investigation)|remains?\s+(?:unclear|unknown|open|unresolved)|open\s+(?:question|problem)|limitations?|poorly\s+understood|little\s+is\s+known|not\s+yet|has\s+not\s+been)", "future_work"),
    (r"尚(?:不|未|需)|有待|仍(?:然|需|待)|缺乏|不足|未知|局限|空白|未解决|尚未(?:解决|明确)|需进一步", "future_work_zh"),
]
CONFLICT_PATTERNS = [
    (r"(?i)\b(in\s+contrast|on\s+the\s+other\s+hand|versus|conflict(?:ing)?|discrepan\w+|inconsistent|contradict\w+)", "conflict"),
    (r"然而|相反|与之相反|矛盾|不一致|冲突|争议|尚存争议|有争议", "conflict_zh"),
]
KIND_PATTERNS = {
    "origin": (r"(?i)\b(first|initial|original|最早|首次|首创|原创|开创)\b",),
    "method": (r"(?i)\b(method|approach|framework|model|algorithm|技术路线|方法|框架|模型|算法)\b",),
    "milestone": (r"(?i)\b(state[- ]of[- ]the[- ]art|breakthrough|里程碑|突破|显著提升|大幅提升)\b",),
    "benchmark": (r"(?i)\b(benchmark|dataset|evaluation|数据集|基准|评测)\b",),
}

SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?.;；])\s*|\n+")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a file as text with encoding fallback. Returns (text_or_None, err)."""
    if not os.path.isfile(path):
        return None, "missing"
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat_failed: %s" % e
    if size == 0:
        return "", "empty"
    if size > max_bytes:
        return None, "oversize"
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, "unreadable: %s" % e
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc), "ok"
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace"), "degraded"


def load_json(path):
    text, err = read_text(path)
    if err not in ("ok", "degraded"):
        return None, err
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError as e:
        return None, "bad_json: %s" % e


def pdftotext(pdf_path):
    """Try to extract PDF text with pdftotext if available. Returns (text, err)."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "no_pdftotext"
    try:
        proc = subprocess.run(
            [exe, "-layout", pdf_path, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "pdftotext_failed: %s" % e
    if proc.returncode != 0:
        return None, "pdftotext_rc=%d" % proc.returncode
    try:
        return proc.stdout.decode("utf-8", errors="replace"), "ok"
    except Exception as e:  # pragma: no cover
        return None, "decode_failed: %s" % e


def scan_files(input_dir):
    """Deterministically list files under input_dir, sorted."""
    if not os.path.isdir(input_dir):
        return []
    out = []
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            if name.startswith("."):
                continue
            out.append(os.path.join(root, name))
    return out


def relpath(path, base):
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


def is_text_like(name):
    return name.lower().endswith(TEXT_SUFFIXES)


def is_pdf(name):
    return name.lower().endswith(PDF_SUFFIXES)


def extract_year(text, filename):
    head = text[:2000] if text else ""
    for m in YEAR_LABEL_RE.finditer(head):
        return int(m.group(1))
    m = re.search(r"\((19[5-9]\d|20[0-3]\d)\)", head)
    if m:
        return int(m.group(1))
    m = re.search(r"\[(19[5-9]\d|20[0-3]\d)\]", head)
    if m:
        return int(m.group(1))
    m = YEAR_RE.search(head)
    if m:
        return int(m.group(1))
    m = YEAR_RE.search(text or "")
    if m:
        return int(m.group(1))
    return None


def extract_title(text, filename):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    for l in lines[:40]:
        l2 = l.lstrip("#").strip()
        m = re.match(r"^(?:title|题目|标题)\s*[:：]\s*(.{6,300})$", l2, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    for l in lines[:20]:
        if 8 <= len(l) <= 300 and not re.match(r"^(#+|\d+[\.\)]\s|\[|\*\*)", l):
            if not l.lower().startswith(("http", "doi", "arxiv", "figure", "table")):
                return l
    return os.path.basename(filename)


def extract_citations(text):
    m = CITE_RE.search(text or "")
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    return int(digits) if digits else None


def split_sentences_with_lines(text):
    """Return [(sentence, line_start, line_end)] using line numbers."""
    lines = text.splitlines()
    out = []
    buf = []
    start = 0
    for i, line in enumerate(lines, 1):
        buf.append(line)
        if re.search(r"[。！？!?.;；]\s*$", line) or not line.strip():
            sent = " ".join(buf).strip()
            if sent:
                out.append((sent, start or i, i))
            buf = []
            start = 0
        elif start == 0:
            start = i
    if buf:
        sent = " ".join(buf).strip()
        if sent:
            out.append((sent, start or 1, len(lines)))
    return out


def find_matches(text, patterns):
    """Find candidate (sentence, kind, line_start, line_end) tuples."""
    found = []
    for sent, ls, le in split_sentences_with_lines(text):
        for regex, kind in patterns:
            if re.search(regex, sent):
                found.append({"sentence": sent[:500], "kind": kind, "line_start": ls, "line_end": le})
                break
    return found


def term_frequency(docs):
    counts = {}
    doc_years = {}
    for d in docs:
        text = (d.get("text") or "").lower()
        words = set(WORD_RE.findall(text))
        for w in words:
            if w in STOPWORDS or len(w) < 4:
                continue
            counts[w] = counts.get(w, 0) + 1
        for w in words:
            if w not in counts:
                continue
            doc_years.setdefault(w, []).append(d.get("year"))
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:50]
    out = []
    for term, cnt in top:
        years = [y for y in doc_years.get(term, []) if y is not None]
        out.append({
            "term": term,
            "count": cnt,
            "docs": len(years),
            "first_year": min(years) if years else None,
            "last_year": max(years) if years else None,
        })
    return out


def classify_kind(text):
    kinds = []
    for kind, regexes in KIND_PATTERNS.items():
        if any(re.search(r, text or "", re.IGNORECASE) for r in regexes):
            kinds.append(kind)
    return kinds


def make_material_id(index):
    return "mat_%03d" % (index + 1)


def build_doc_record(mid, source, path_rel, text, err, year, title, citations):
    return {
        "id": mid,
        "source_file": source,
        "path": path_rel,
        "error": err,
        "char_count": len(text or ""),
        "year": year,
        "title": title,
        "citations": citations,
    }
