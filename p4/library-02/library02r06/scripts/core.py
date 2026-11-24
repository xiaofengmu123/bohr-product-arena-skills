"""core.py - deterministic document-library retrieval & repair pipeline.

Standard library only. Deterministic (sorted walks, stable ordering, no RNG).
Layered intermediate representation (IR):

    plan.json            task IR  : goals / rules / filters / questions / contract
    inventory.json       data IR  : files, dirs, symlinks, hardlinks, hashes, perms
    library_model.json   domain IR: duplicates, version candidates, aliases,
                                   catalog parse + reconciliation, search index
    evidence_model.json  evidence IR: claim -> locator + verbatim quote
    result.json          final    : contract fields + status + sources +
                                   uncertainty + change_log + computed summary

Every later layer is mechanically cross-checkable against earlier layers with
`--check` (hash re-computation, verbatim-quote substring check, referential
integrity, contract completeness).
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat as statmod
import subprocess
import sys
import time
import unicodedata
import zipfile

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
CAP_DEFAULT = 2 * 1024 * 1024  # 2 MiB per-file content cap
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
    ".bib", ".ris", ".xml", ".html", ".htm", ".yaml", ".yml", ".log",
    ".py", ".tex", ".ini", ".cfg", ".rst", ".org", ".tex", ".text",
}
CATALOG_EXTS = {".json", ".csv", ".md", ".bib", ".ris", ".sqlite", ".db", ".sqlite3"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".svg"}
OFFICE_EXTS = {
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods", ".odp",
}

CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract",
    "expected_output", "outputSchema", "output_schema", "result_spec",
)
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "change_log", "errors",
    "degradations", "runtime", "limitations", "pending_human", "computed",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "this", "that", "these",
    "those", "it", "as", "at", "by", "from", "into", "has", "have", "had",
    "not", "no", "we", "you", "they", "he", "she", "his", "her", "their",
    "i", "me", "my", "but", "if", "then", "than", "so", "such", "also",
    "can", "may", "will", "would", "should", "could", "which", "who",
    "what", "when", "where", "how", "why", "do", "does", "did", "our",
}

STEM_STOP_WORDS = {
    "final", "draft", "rev", "clean", "cleaned", "edit", "edited", "copy",
    "old", "new", "backup", "bak", "latest", "version", "ver", "v", "r",
    "dup", "duplicate", "renamed", "scan", "scanned", "text", "ocr",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
VERSION_TOKEN_RE = re.compile(
    r"\b(?:v|ver|version|rev|r)\s*\.?\s*\d+(?:\.\d+)*[a-z]?\b", re.I)
DATE_TOKEN_RE = re.compile(r"\b\d{4}[-_/]\d{1,2}[-_/]\d{1,2}\b|\b\d{8}\b")
BRACKET_RE = re.compile(r"[\[\]()（）()]+")
SPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def norm_slash(path):
    return path.replace("\\", "/")


def relpath(root, full):
    rel = os.path.relpath(full, root)
    return "" if rel == "." else norm_slash(rel)


def utcnow_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, cap=CAP_DEFAULT):
    """Return (text, status, detail). status in ok|degraded|too_large|unreadable."""
    try:
        with open(path, "rb") as f:
            raw = f.read(cap + 1)
    except OSError as e:
        return None, "unreadable", str(e)
    if len(raw) > cap:
        return None, "too_large", "exceeds %d bytes" % cap
    try:
        return raw.decode("utf-8-sig"), "ok", "utf-8-sig"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("utf-8"), "ok", "utf-8"
    except UnicodeDecodeError:
        pass
    return raw.decode("latin-1"), "degraded", "latin-1 (not utf-8)"


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest(), "ok", None
    except OSError as e:
        return None, "unreadable", str(e)


def normalize_text(text):
    return SPACE_RE.sub(" ", text).strip().lower()


def norm_sha256(text):
    return hashlib.sha256(normalize_text(text).encode("utf-8", "replace")).hexdigest()


def tokenize(text, max_tokens=20000):
    latin = TOKEN_RE.findall(text.lower())[:max_tokens]
    toks = [t for t in latin if t not in STOPWORDS and len(t) > 1]
    cjk = set()
    for run in CJK_RE.findall(text):
        if len(run) == 1:
            cjk.add(run)
        else:
            for i in range(len(run) - 1):
                cjk.add(run[i:i + 2])
    return toks + sorted(cjk)


def detect_file_type(path, ext):
    ext = (ext or "").lower()
    if ext == ".pdf":
        return "pdf"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in TEXT_EXTS:
        return "text"
    # magic sniff for extensionless / odd files
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return "unknown"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "office"
    if head.startswith(b"\x89PNG") or head.startswith(b"GIF8") or head.startswith(b"\xff\xd8"):
        return "image"
    if head.startswith(b"SQLite format 3"):
        return "sqlite"
    return "binary"


def normalize_stem(name):
    base = os.path.splitext(name)[0]
    s = base.lower()
    s = s.replace("_", " ").replace("-", " ")
    s = VERSION_TOKEN_RE.sub(" ", s)
    s = DATE_TOKEN_RE.sub(" ", s)
    s = BRACKET_RE.sub(" ", s)
    s = re.sub(r"\bcopy\s+(?:of\s+)?\d*\b|\b副本\b|\b拷贝\b|\bdup\b", " ", s)
    words = [w for w in s.split() if w not in STEM_STOP_WORDS and len(w) > 1]
    return " ".join(words).strip()


def token_jaccard(tokens_a, tokens_b):
    if not tokens_a or not tokens_b:
        return 0.0
    sa, sb = set(tokens_a[:2000]), set(tokens_b[:2000])
    inter = len(sa & sb)
    union = len(sa | sb)
    return round(inter / union, 4) if union else 0.0


def deep_merge(dst, src):
    if isinstance(dst, dict) and isinstance(src, dict):
        for k, v in src.items():
            if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
                deep_merge(dst[k], v)
            else:
                dst[k] = v
    else:
        return src
    return dst


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# plan (task IR)
# ---------------------------------------------------------------------------
def find_contract(obj):
    if not isinstance(obj, dict):
        return None, None
    for k in CONTRACT_KEYS:
        if k in obj and isinstance(obj[k], (dict, list)):
            return obj[k], k
    # one-level fallback under a "task"/"spec"/"schema" wrapper
    for wrap in ("task", "spec", "schema", "config"):
        w = obj.get(wrap)
        if isinstance(w, dict):
            for k in CONTRACT_KEYS:
                if k in w and isinstance(w[k], (dict, list)):
                    return w[k], "%s.%s" % (wrap, k)
    return None, None


def infer_case_id(case, case_path, task_text):
    for k in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        v = case.get(k) if isinstance(case, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    m = re.search(r"challenge_id[:\s`'\"]+([A-Za-z0-9._-]+)", task_text)
    if m:
        return m.group(1)
    if case_path:
        base = os.path.basename(case_path)
        return os.path.splitext(base)[0]
    return "unknown"


def extract_questions(case, task_text):
    qs = []
    if isinstance(case, dict):
        for k in ("questions", "queries", "tasks", "searches"):
            v = case.get(k)
            if isinstance(v, list):
                for x in v:
                    qs.append({"id": "q%d" % (len(qs) + 1), "text": str(x), "source": "case.json"})
            elif isinstance(v, str) and v.strip():
                qs.append({"id": "q%d" % (len(qs) + 1), "text": v.strip(), "source": "case.json"})
    pattern = re.compile(
        r"(?im)^\s*(?:Q\d*|问题\s*\d*|检索\s*\d*|查询\s*\d*|question\s*\d*|query\s*\d*)\s*[:：]\s*(.+)$")
    for m in pattern.finditer(task_text):
        qs.append({"id": "q%d" % (len(qs) + 1), "text": m.group(1).strip(), "source": "task.md"})
    # de-dup by text
    seen, out = set(), []
    for q in qs:
        if q["text"] not in seen:
            seen.add(q["text"])
            out.append(q)
    return out


def extract_goals(task_text):
    goals = []
    for m in re.finditer(r"(?im)^\s*(?:[-*]|\d+[.)])\s*(.+)$", task_text):
        line = m.group(1).strip()
        if 4 <= len(line) <= 160 and any(k in line for k in
                ("盘点", "组织", "元数据", "版本", "检索", "定位", "过滤", "回链",
                 "清单", "记录", "恢复", "去重", "对账", "索引", "organize",
                 "retriev", "index", "version", "metadata")):
            goals.append(line)
    return goals[:40]


def detect_filter_dimensions(text):
    dims = []
    for kw in ("年份", "year", "项目", "project", "文件类型", "file type", "类型",
               "批次", "batch", "版本", "version", "权限", "permission", "来源",
               "source", "作者", "author", "日期", "date"):
        if kw.lower() in text.lower():
            dims.append(kw)
    return sorted(set(dims))


def build_plan(case_json_path, task_path, input_root):
    case_json_missing = not (case_json_path and os.path.exists(case_json_path))
    case = load_json(case_json_path, {}) if not case_json_missing else {}
    task_text = ""
    if task_path and os.path.exists(task_path):
        task_text, _, _ = read_text(task_path)
        task_text = task_text or ""
    contract_raw, contract_key = find_contract(case)
    case_id = infer_case_id(case, case_json_path if not case_json_missing else None, task_text)
    if case_json_missing and case_id == "case":
        case_id = "unknown"
    return {
        "schema": "library-retrieval-plan/1",
        "case_id": case_id,
        "case_json_path": case_json_path,
        "task_path": task_path,
        "input_root": input_root,
        "case_json_missing": case_json_missing,
        "contract_key": contract_key,
        "contract": contract_raw,
        "goals": extract_goals(task_text),
        "filter_dimensions": detect_filter_dimensions(task_text + " " + json.dumps(case, ensure_ascii=False)),
        "questions": extract_questions(case, task_text),
        "heuristic": True,
        "generated_at_utc": utcnow_iso(),
    }


# ---------------------------------------------------------------------------
# inventory (data IR)
# ---------------------------------------------------------------------------
def try_pdf_extract(full_path, out_dir, file_id):
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "no_pdftotext"
    out = os.path.join(out_dir, file_id + ".txt")
    try:
        r = subprocess.run([exe, "-enc", "UTF-8", "-layout", full_path, out],
                           capture_output=True, timeout=90)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            return norm_slash(out), "ok"
        return None, "pdftotext_failed_rc=%d" % r.returncode
    except Exception as e:  # noqa: BLE001
        return None, "pdftotext_error:%s" % type(e).__name__


def try_docx_extract(full_path, out_dir, file_id):
    try:
        with zipfile.ZipFile(full_path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return None, "docx_error:%s" % type(e).__name__
    body = re.sub(r"<w:p[ >]", "\n", xml)
    body = re.sub(r"<[^>]+>", "", body)
    body = re.sub(r"\n{2,}", "\n", body)
    out = os.path.join(out_dir, file_id + ".txt")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
        return norm_slash(out), "ok"
    except OSError as e:
        return None, "write_error:%s" % str(e)


def walk_inventory(input_root, work_dir):
    files, dirs, errors, degradations = [], [], [], []
    extracted_dir = os.path.join(work_dir, "extracted")
    os.makedirs(extracted_dir, exist_ok=True)

    def onerror(err):
        errors.append({"kind": "walk_error", "path": norm_slash(getattr(err, "filename", "?")),
                       "detail": str(err)})

    for root, dnames, fnames in os.walk(input_root, onerror=onerror):
        dnames.sort()
        fnames.sort()
        rel_root = relpath(input_root, root)
        for d in dnames:
            full = os.path.join(root, d)
            try:
                empty = len(os.listdir(full)) == 0
            except OSError:
                empty = None
            dirs.append({"path": norm_slash(os.path.join(rel_root, d)) if rel_root else d,
                         "empty": empty})
        for f in fnames:
            full = os.path.join(root, f)
            rel = norm_slash(os.path.join(rel_root, f)) if rel_root else f
            rec = {
                "id": "f%d" % (len(files) + 1),
                "path": rel,
                "name": f,
                "ext": os.path.splitext(f)[1].lower(),
                "role": "control" if rel in ("case.json", "task.md") else "material",
                "is_symlink": False, "link_target": None, "is_broken_symlink": False,
                "hardlink_group": None, "size": None, "mtime": None, "mode": None,
                "file_type": None, "sha256": None, "sha256_norm": None,
                "read_status": None, "encoding": None, "text_locator": None,
                "line_count": None, "error": None,
            }
            try:
                lst = os.lstat(full)
                rec["mode"] = oct(statmod.S_IMODE(lst.st_mode))
                rec["size"] = lst.st_size
                rec["mtime"] = int(lst.st_mtime)
                if os.path.islink(full):
                    rec["is_symlink"] = True
                    try:
                        rec["link_target"] = norm_slash(os.readlink(full))
                    except OSError as e:
                        rec["link_target"] = str(e)
                    rec["is_broken_symlink"] = not os.path.exists(full)
                    rec["read_status"] = "symlink"
                    rec["file_type"] = "symlink"
                    files.append(rec)
                    continue
                if not os.path.isfile(full):
                    rec["read_status"] = "non_regular"
                    rec["file_type"] = "special"
                    files.append(rec)
                    continue
            except OSError as e:
                rec["read_status"] = "unreadable"
                rec["error"] = "lstat:%s" % str(e)
                rec["file_type"] = "unknown"
                files.append(rec)
                degradations.append({"kind": "unreadable", "path": rel, "detail": str(e)})
                continue

            rec["file_type"] = detect_file_type(full, rec["ext"])

            if rec["size"] is not None and rec["size"] > CAP_DEFAULT:
                rec["read_status"] = "too_large"
                rec["error"] = "exceeds %d bytes" % CAP_DEFAULT
                degradations.append({"kind": "too_large", "path": rel, "detail": rec["error"]})
                files.append(rec)
                continue

            text, tstatus, tdetail = read_text(full)
            if tstatus == "ok":
                rec["sha256"] = _sha256_path(full)
                rec["sha256_norm"] = norm_sha256(text)
                rec["encoding"] = tdetail
                rec["text_locator"] = rel
                rec["line_count"] = text.count("\n") + 1
                rec["read_status"] = "ok"
            elif tstatus == "degraded":
                rec["sha256"] = _sha256_path(full)
                rec["encoding"] = tdetail
                rec["text_locator"] = rel
                rec["line_count"] = text.count("\n") + 1
                rec["read_status"] = "degraded"
                degradations.append({"kind": "bad_encoding", "path": rel, "detail": tdetail})
            else:
                # binary (pdf/office/image/sqlite/...) -> content hash only
                rec["sha256"] = _sha256_path(full)
                rec["read_status"] = tstatus
                rec["error"] = tdetail
                if rec["file_type"] == "pdf":
                    out, pstatus = try_pdf_extract(full, extracted_dir, rec["id"])
                    if pstatus == "ok":
                        rec["text_locator"] = out
                        rec["read_status"] = "ok(pdf)"
                    else:
                        degradations.append({"kind": "pdf_extract", "path": rel, "detail": pstatus})
                elif rec["file_type"] == "office" and rec["ext"] == ".docx":
                    out, dstatus = try_docx_extract(full, extracted_dir, rec["id"])
                    if dstatus == "ok":
                        rec["text_locator"] = out
                        rec["read_status"] = "ok(docx)"
                    else:
                        degradations.append({"kind": "office_extract", "path": rel, "detail": dstatus})
                else:
                    degradations.append({"kind": "no_text_layer", "path": rel,
                                         "detail": "binary without text layer; needs OCR/manual"})
            files.append(rec)

    # hardlink detection among files sharing content hash (os.path.samefile)
    by_hash = {}
    for rec in files:
        if rec.get("sha256") and rec.get("read_status") == "ok" and not rec.get("is_symlink"):
            by_hash.setdefault(rec["sha256"], []).append(rec)
    hard_group = 0
    for group in by_hash.values():
        if len(group) < 2:
            continue
        n = len(group)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = group[i], group[j]
                try:
                    if os.path.samefile(os.path.join(input_root, a["path"]),
                                        os.path.join(input_root, b["path"])):
                        hard_group += 1
                        a["hardlink_group"] = a["hardlink_group"] or ("h%d" % hard_group)
                        b["hardlink_group"] = b["hardlink_group"] or a["hardlink_group"]
                except OSError:
                    continue

    stats = {
        "files": len(files), "dirs": len(dirs),
        "total_bytes": sum((f.get("size") or 0) for f in files),
        "symlinks": sum(1 for f in files if f.get("is_symlink")),
        "broken_symlinks": sum(1 for f in files if f.get("is_broken_symlink")),
        "hardlink_groups": hard_group,
    }
    return {
        "schema": "library-inventory/1",
        "files": files, "directories": dirs,
        "errors": errors, "degradations": degradations, "stats": stats,
    }


def _sha256_path(path):
    h, _, _ = sha256_file(path)
    return h


# ---------------------------------------------------------------------------
# catalog parsing + reconciliation
# ---------------------------------------------------------------------------
def parse_catalog(path, text):
    """Return (kind, records). records is a list of dicts. None if not a catalog."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                raw = fh.read()
            data = json.loads(raw) if raw.strip() else None
        except Exception as e:  # noqa: BLE001
            return "json_error", [{"_kind": "json_error", "error": str(e)}]
        if data is None:
            return None
        if isinstance(data, list):
            return "json", [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            for k in ("files", "records", "items", "entries", "documents"):
                v = data.get(k)
                if isinstance(v, list):
                    return "json", [r for r in v if isinstance(r, dict)]
            return "json", [data]
        return None
    if ext == ".csv" or ext == ".tsv":
        try:
            delim = "," if ext == ".csv" else "\t"
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f, delimiter=delim)
                rows = [dict(r) for r in reader]
            return "csv", rows
        except Exception:
            return None
    if ext == ".bib":
        return "bibtex", parse_bibtex(text)
    if ext == ".ris":
        return "ris", parse_ris(text)
    if ext in (".sqlite", ".db", ".sqlite3"):
        return "sqlite", parse_sqlite(path)
    if ext == ".md":
        return "markdown", parse_markdown_tables(text)
    return None


def parse_bibtex(text):
    entries = []
    # simple @type{key, field={value}, ...}
    pat = re.compile(r"@\w+\s*\{\s*([^,]+),\s*(.*?)\}\s*[,;]?\s*(?=@|\Z)", re.S)
    for key, body in pat.findall(text):
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[{\"]([^}\"]*)[}\"]", body):
            fields[fm.group(1).lower()] = fm.group(2).strip()
        entries.append({"key": key.strip(), "_kind": "bibtex", **fields})
    return entries


def parse_ris(text):
    entries, cur = [], None
    for line in text.splitlines():
        if len(line) >= 6 and line[2:4] == "  " and line[4] == "-":
            tag, val = line[:2].strip().upper(), line[6:].strip()
            if tag == "TY":
                if cur:
                    entries.append(cur)
                cur = {"_kind": "ris"}
            if cur is not None:
                cur[tag] = val
        elif line.strip() == "ER" and cur is not None:
            entries.append(cur)
            cur = None
    if cur:
        entries.append(cur)
    return entries


def parse_sqlite(path):
    try:
        con = sqlite3.connect(path)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        records = [{"_kind": "sqlite_table", "table": t} for t in tables]
        # try to read a files-ish table
        for t in tables:
            try:
                cur.execute('SELECT * FROM "%s" LIMIT 200' % t)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                records.extend([{"_kind": "sqlite_row", "table": t, **r} for r in rows[:50]])
            except Exception:
                continue
        con.close()
        return records
    except Exception as e:  # noqa: BLE001
        return [{"_kind": "sqlite_open_error", "error": str(e)}]


def parse_markdown_tables(text):
    records = []
    # GFM tables: lines starting with | and a separator line of ---
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|"):
            header = lines[i].strip().strip("|").split("|")
            header = [h.strip() for h in header]
            if i + 1 < len(lines) and re.match(r"^[\s|:\-]+$", lines[i + 1]):
                j = i + 2
                while j < len(lines) and lines[j].strip().startswith("|"):
                    cells = lines[j].strip().strip("|").split("|")
                    cells = [c.strip() for c in cells]
                    records.append(dict(zip(header, cells)))
                    j += 1
                i = j
                continue
        i += 1
    # YAML-like "- key: value" lists
    for m in re.finditer(r"(?m)^\s*[-*]\s+(?:path|file|filename|name|version|hash)\s*:\s*(.+)$", text):
        k, v = m.group(0).strip().lstrip("-*").strip().split(":", 1)
        records.append({k.strip().lower(): v.strip()})
    return records


def reconcile(catalog_records, inventory_files):
    path_keys = ("path", "file", "filename", "file_path", "location", "relative_path", "name")
    refs = []
    for rec in catalog_records:
        for k in path_keys:
            if k in rec and isinstance(rec[k], str) and rec[k].strip():
                refs.append(norm_slash(rec[k].strip()))
                break
    inv_paths = {f["path"] for f in inventory_files}
    inv_basenames = {}
    for f in inventory_files:
        inv_basenames.setdefault(os.path.basename(f["path"]), []).append(f["path"])

    matches, missing = [], []
    for ref in sorted(set(refs)):
        hit = None
        if ref in inv_paths:
            hit = ref
        else:
            cand = inv_basenames.get(os.path.basename(ref), [])
            if len(cand) == 1:
                hit = cand[0]
            elif len(cand) > 1:
                hit = "ambiguous:" + ",".join(cand)
        (matches if hit and not hit.startswith("ambiguous:") else missing).append(
            {"catalog_ref": ref, "resolved": hit})
    orphaned = sorted(inv_paths - {m["resolved"] for m in matches if m["resolved"]})
    return {
        "matches": matches, "mismatches": [], "missing": missing, "orphaned": orphaned,
        "note": "catalog refs matched by relative path or unique basename; heuristic",
    }


# ---------------------------------------------------------------------------
# library model (domain IR)
# ---------------------------------------------------------------------------
def build_library_model(inventory, input_root):
    files = inventory["files"]
    materials = [f for f in files if f.get("role") != "control"]

    def resolve_loc(loc):
        if not loc:
            return None
        if os.path.isabs(loc):
            return loc
        return os.path.join(input_root, loc)


    # duplicates by content sha256
    by_hash = {}
    for f in materials:
        if f.get("sha256"):
            by_hash.setdefault(f["sha256"], []).append(f["id"])
    dup_groups = []
    for g, (h, ids) in enumerate(sorted(by_hash.items()), 1):
        if len(ids) > 1:
            canonical = sorted(ids, key=lambda i: (len(_path_of(inventory, i)), i))[0]
            dup_groups.append({"group_id": "dup%d" % g, "sha256": h,
                               "files": sorted(ids), "canonical": canonical})

    # version candidates by normalized stem
    by_stem = {}
    for f in materials:
        stem = normalize_stem(f["name"])
        if stem:
            by_stem.setdefault(stem, []).append(f)
    version_candidates = []
    for g, (stem, group) in enumerate(sorted(by_stem.items()), 1):
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (r.get("mtime") or 0, r.get("size") or 0, r["path"]))
        ids = [r["id"] for r in group]
        sims = []
        texts = {}
        for r in group:
            loc = resolve_loc(r.get("text_locator"))
            if loc and os.path.exists(loc):
                t, st, _ = read_text(loc)
                if st in ("ok", "degraded"):
                    texts[r["id"]] = tokenize(t)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a["id"] in texts and b["id"] in texts:
                    sims.append({"a": a["id"], "b": b["id"],
                                 "jaccard": token_jaccard(texts[a["id"]], texts[b["id"]])})
        version_candidates.append({
            "group_id": "ver%d" % g, "stem": stem, "files": ids,
            "order_oldest_to_newest": ids, "method": "heuristic",
            "similarities": sims,
        })

    # aliases
    aliases = []
    for f in files:
        if f.get("is_symlink"):
            aliases.append({"kind": "broken_symlink" if f.get("is_broken_symlink") else "symlink",
                            "path": f["path"], "target": f.get("link_target")})
        if f.get("hardlink_group"):
            aliases.append({"kind": "hardlink", "path": f["path"],
                            "group": f["hardlink_group"]})

    # catalogs + reconciliation
    catalogs, all_records = [], []
    model_degradations = []
    for f in materials:
        if f["ext"] not in CATALOG_EXTS or f.get("read_status") not in ("ok", "degraded"):
            continue
        cat_path = resolve_loc(f["path"])
        text, st, _ = read_text(cat_path)
        parsed = parse_catalog(cat_path, text or "")
        if parsed:
            kind, records = parsed
            if kind == "json_error":
                model_degradations.append({
                    "kind": "bad_json", "path": f["path"],
                    "detail": str(records[0].get("error")),
                })
                continue
            catalogs.append({"path": f["path"], "kind": kind, "records": len(records)})
            all_records.extend([{"_catalog": f["path"], **r} for r in records])
    reconciliation = reconcile(all_records, materials)

    # index (term -> postings)
    index = {}
    file_name_index = {}
    for f in materials:
        loc = resolve_loc(f.get("text_locator"))
        if not loc:
            continue
        text, st, _ = read_text(loc)
        if st not in ("ok", "degraded") or not text:
            continue
        line_tokens = {}
        for ln, line in enumerate(text.splitlines(), 1):
            for tok in tokenize(line, 500):
                line_tokens.setdefault(tok, []).append(ln)
        for tok, lines in line_tokens.items():
            if len(index.get(tok, {})) < 200:
                index.setdefault(tok, {})[f["id"]] = lines[:20]
        name_tokens = tokenize(f["name"], 100) + [f["name"].lower()]
        for tok in name_tokens:
            file_name_index.setdefault(tok, set()).add(f["id"])

    def postings_to_list(d):
        return {t: [{"file": fid, "lines": lines} for fid, lines in sorted(p.items())]
                for t, p in sorted(d.items()) if len(p) <= 50}

    return {
        "schema": "library-model/1",
        "duplicate_groups": dup_groups,
        "version_candidates": version_candidates,
        "aliases": aliases,
        "catalogs": catalogs,
        "reconciliation": reconciliation,
        "index": postings_to_list(index),
        "file_name_index": {t: sorted(ids) for t, ids in sorted(file_name_index.items())},
        "degradations": model_degradations,
        "heuristics_applied": [
            "content-sha256-dedup", "normalized-stem-version",
            "token-jaccard-similarity", "basename-catalog-reconcile",
            "token-plus-cjk-bigram-index",
        ],
    }


def _path_of(inventory, file_id):
    for f in inventory["files"]:
        if f["id"] == file_id:
            return f["path"]
    return ""


# ---------------------------------------------------------------------------
# evidence model (evidence IR)
# ---------------------------------------------------------------------------
def build_evidence_model(plan, inventory, model):
    claims = []
    # inventory counts
    stats = inventory["stats"]
    claims.append({
        "claim_id": "c_stat_files", "kind": "count",
        "claim": "inventory contains %d files and %d directories" % (stats["files"], stats["dirs"]),
        "sources": [{"locator": "inventory.json", "quote": "", "verified": True}],
        "confidence": "high", "status": "verified",
    })
    for dg in model["duplicate_groups"]:
        paths = [_path_of(inventory, i) for i in dg["files"]]
        claims.append({
            "claim_id": "c_" + dg["group_id"], "kind": "duplicate",
            "claim": "content-identical files: %s" % ", ".join(paths),
            "sources": [{"locator": ",".join(paths), "quote": "sha256=%s" % dg["sha256"], "verified": True}],
            "confidence": "high", "status": "verified",
        })
    for vc in model["version_candidates"]:
        paths = [_path_of(inventory, i) for i in vc["files"]]
        claims.append({
            "claim_id": "c_" + vc["group_id"], "kind": "version",
            "claim": "heuristic version chain (stem '%s'): %s" % (vc["stem"], ", ".join(paths)),
            "sources": [{"locator": ",".join(paths), "quote": "stem=%s" % vc["stem"], "verified": True}],
            "confidence": "medium", "status": "pending_human",
        })
    recon = model["reconciliation"]
    claims.append({
        "claim_id": "c_reconciliation", "kind": "reconciliation",
        "claim": "reconciliation: %d matches, %d missing, %d orphaned" % (
            len(recon["matches"]), len(recon["missing"]), len(recon["orphaned"])),
        "sources": [{"locator": "library_model.json#reconciliation", "quote": "", "verified": True}],
        "confidence": "high", "status": "verified",
    })
    return {
        "schema": "evidence-model/1",
        "case_id": plan["case_id"],
        "claims": claims,
    }


# ---------------------------------------------------------------------------
# result assembly
# ---------------------------------------------------------------------------
def fill_contract_placeholder(contract):
    if isinstance(contract, dict):
        return {k: ([] if isinstance(v, list) else None) for k, v in contract.items()}
    if isinstance(contract, list):
        return []
    return None


def build_result(plan, inventory, model, evidence, status, case_json_path, output_dir):
    contract = plan.get("contract")
    result = {}
    contract_filled = fill_contract_placeholder(contract)
    if isinstance(contract_filled, dict):
        result.update(contract_filled)
    # move reserved-key collisions to computed._contract_*
    moved = {}
    for k in list(result.keys()):
        if k in RESERVED_KEYS:
            moved["_contract_" + k] = result.pop(k)
    result["case_id"] = plan["case_id"]
    result["status"] = status
    result["sources"] = []
    result["uncertainty"] = [{
        "item": "intermediate-representation heuristic layers",
        "reason": "version chains, catalog reconciliation and index are deterministic heuristics; human/agent must confirm semantic correctness",
        "severity": "medium",
    }]
    if contract is None:
        result["uncertainty"].append({
            "item": "output_contract",
            "reason": "no output_contract/output/result_schema/contract key found in case.json",
            "severity": "high",
        })
    result["change_log"] = [{
        "time": utcnow_iso(), "actor": "skill-script",
        "step": "build", "detail": "generated layered IR + result skeleton",
    }]
    result["errors"] = inventory.get("errors", [])
    result["degradations"] = inventory.get("degradations", []) + model.get("degradations", [])
    if plan.get("case_json_missing"):
        result["errors"] = result["errors"] + [{
            "kind": "missing_case_json", "path": plan.get("case_json_path"),
            "detail": "case.json not found",
        }]
    result["limitations"] = []
    result["pending_human"] = [c["claim"] for c in evidence["claims"] if c["status"] == "pending_human"]
    result["runtime"] = {
        "skill_version": VERSION, "python": sys.version.split()[0],
        "input_root": plan["input_root"],
    }
    compact_files = [{
        "path": f["path"], "sha256": f["sha256"], "size": f["size"],
        "status": f["read_status"], "type": f["file_type"], "role": f["role"],
    } for f in inventory["files"]]
    result["computed"] = {
        "plan": {k: plan[k] for k in ("case_id", "goals", "filter_dimensions", "questions") if k in plan},
        "files": compact_files,
        "summary": inventory["stats"],
        "duplicate_groups": model["duplicate_groups"],
        "version_candidates": model["version_candidates"],
        "aliases": model["aliases"],
        "reconciliation": model["reconciliation"],
        "index_terms": len(model["index"]),
        "answers": [],
        "artifacts": {},
        "evidence": evidence,
    }
    if moved:
        result["computed"].update(moved)
    return result


# ---------------------------------------------------------------------------
# output artifacts
# ---------------------------------------------------------------------------
def write_artifacts(output_dir, plan, inventory, model, evidence, result):
    os.makedirs(output_dir, exist_ok=True)
    artifacts = {}
    artifacts["plan.json"] = write_json(os.path.join(output_dir, "plan.json"), plan)
    artifacts["inventory.json"] = write_json(os.path.join(output_dir, "inventory.json"), inventory)
    artifacts["library_model.json"] = write_json(os.path.join(output_dir, "library_model.json"), model)
    artifacts["evidence_model.json"] = write_json(os.path.join(output_dir, "evidence_model.json"), evidence)

    # library_manifest.csv
    csv_path = os.path.join(output_dir, "library_manifest.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="\n") as f:
        w = csv.writer(f)
        w.writerow(["path", "sha256", "size", "mtime", "mode", "file_type", "read_status",
                    "is_symlink", "link_target", "hardlink_group"])
        for r in inventory["files"]:
            w.writerow([r["path"], r["sha256"], r["size"], r["mtime"], r["mode"],
                        r["file_type"], r["read_status"], r["is_symlink"],
                        r["link_target"], r["hardlink_group"]])
    artifacts["library_manifest.csv"] = csv_path

    # directory_tree.txt
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    lines = []
    for d in sorted(inventory["directories"], key=lambda x: x["path"]):
        lines.append(d["path"] + ("  [空目录]" if d["empty"] else ""))
    for f in sorted(inventory["files"], key=lambda x: x["path"]):
        suffix = "  [symlink]" if f.get("is_symlink") else ("  [hardlink:%s]" % f["hardlink_group"] if f.get("hardlink_group") else "")
        lines.append(f["path"] + suffix)
    with open(tree_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    artifacts["directory_tree.txt"] = tree_path

    # operation_log.jsonl (deterministic events; timestamps are the only runtime variance)
    log_path = os.path.join(output_dir, "operation_log.jsonl")
    events = [
        {"time": utcnow_iso(), "actor": "skill-script", "step": "plan", "detail": "built plan.json"},
        {"time": utcnow_iso(), "actor": "skill-script", "step": "inventory", "detail": "walked input tree"},
        {"time": utcnow_iso(), "actor": "skill-script", "step": "model", "detail": "duplicates/versions/aliases/reconciliation/index"},
        {"time": utcnow_iso(), "actor": "skill-script", "step": "evidence", "detail": "built evidence_model.json"},
        {"time": utcnow_iso(), "actor": "skill-script", "step": "finalize", "detail": "wrote result.json"},
    ]
    with open(log_path, "w", encoding="utf-8", newline="\n") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    artifacts["operation_log.jsonl"] = log_path

    result_path = os.path.join(output_dir, "result.json")
    artifacts["result.json"] = result_path
    result["computed"]["artifacts"] = {k: os.path.basename(v) for k, v in artifacts.items()}
    write_json(result_path, result)
    return artifacts, result


# ---------------------------------------------------------------------------
# verification (--check)
# ---------------------------------------------------------------------------
def parse_locator_path(locator):
    """Return the file path part of a locator like 'materials/x.txt#L12' or 'x.pdf#p3'."""
    if not locator:
        return None
    return locator.split("#")[0].strip()


def collect_sources(obj):
    """Recursively gather all dicts stored under any 'sources' key."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "sources" and isinstance(v, list):
                out.extend(s for s in v if isinstance(s, dict))
            else:
                out.extend(collect_sources(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(collect_sources(v))
    return out


def check_result(result_path, case_json_path, input_root):
    result = load_json(result_path)
    issues, warnings = [], []
    if not isinstance(result, dict):
        print("CHECK FAIL: result.json is not a JSON object")
        return 1
    if not (result.get("case_id") and str(result["case_id"]).strip()):
        issues.append("missing/empty case_id")
    if result.get("status") not in ALLOWED_STATUS:
        issues.append("invalid status %r" % result.get("status"))

    # contract completeness
    case = load_json(case_json_path, {}) if case_json_path and os.path.exists(case_json_path) else {}
    contract, ckey = find_contract(case)
    if isinstance(contract, dict):
        for k in contract:
            present = k in result or ("computed" in result and isinstance(result["computed"], dict)
                                      and ("_contract_" + k) in result["computed"])
            if not present:
                issues.append("contract field missing: %s" % k)

    # evidence quote verification (best effort)
    verified = unverifiable = mismatch = 0
    sources = collect_sources(result)
    for src in sources:
        loc = src.get("locator", "") if isinstance(src, dict) else ""
        quote = src.get("quote", "") if isinstance(src, dict) else ""
        if not quote:
            continue
        path = parse_locator_path(loc)
        if not path or path in ("inventory.json", "library_model.json", "plan.json"):
            continue
        full = os.path.join(input_root, path) if not os.path.isabs(path) else path
        if not os.path.isfile(full):
            unverifiable += 1
            continue
        text, st, _ = read_text(full)
        if st not in ("ok", "degraded") or not text:
            # try pdftotext for pdfs
            if full.lower().endswith(".pdf"):
                tmp = try_pdf_extract(full, os.path.dirname(result_path), "checkpdf")
                if tmp and os.path.exists(tmp):
                    text, st, _ = read_text(tmp)
            if not text:
                unverifiable += 1
                continue
        if quote in text:
            verified += 1
        else:
            mismatch += 1
            warnings.append("quote mismatch in %s: %r" % (loc, quote[:80]))

    # hash re-computation against computed.files
    hash_ok = hash_checked = 0
    files = result.get("computed", {}).get("files", [])
    for fr in files:
        p = fr.get("path")
        if not p or fr.get("role") == "control":
            continue
        full = os.path.join(input_root, p) if not os.path.isabs(p) else p
        if not os.path.isfile(full):
            continue
        h, _, _ = sha256_file(full)
        hash_checked += 1
        if h == fr.get("sha256"):
            hash_ok += 1
        else:
            warnings.append("hash mismatch for %s" % p)

    if issues:
        print("CHECK FAIL:")
        for i in issues:
            print("  -", i)
        return 1
    print("CHECK OK: case_id=%s status=%s" % (result.get("case_id"), result.get("status")))
    print("  contract fields: %s" % (ckey or "n/a"))
    print("  quotes: verified=%d unverifiable=%d mismatch=%d" % (verified, unverifiable, mismatch))
    print("  hashes: rechecked=%d ok=%d" % (hash_checked, hash_ok))
    for w in warnings:
        print("  WARN:", w)
    return 0


# ---------------------------------------------------------------------------
# query (--query)
# ---------------------------------------------------------------------------
def query(term, output_dir):
    model = load_json(os.path.join(output_dir, "library_model.json"), {})
    if not model:
        print("no library_model.json found under %s" % output_dir)
        return 1
    idx = model.get("index", {})
    fn = model.get("file_name_index", {})
    term_l = term.strip().lower()
    hits = idx.get(term_l, [])
    name_hits = fn.get(term_l, [])
    inv = load_json(os.path.join(output_dir, "inventory.json"), {})
    id2path = {f["id"]: f["path"] for f in inv.get("files", [])}
    if not hits and not name_hits:
        print("no exact index hit for %r (try another term)" % term)
        return 0
    print("content hits:")
    for h in hits:
        print("  %s lines=%s" % (id2path.get(h.get("file"), h.get("file")), h.get("lines")))
    print("name hits:")
    for fid in name_hits:
        print("  %s" % id2path.get(fid, fid))
    return 0


# ---------------------------------------------------------------------------
# main pipeline
# ---------------------------------------------------------------------------
def build_pipeline(args):
    plan = build_plan(args.case_json, args.task_path, args.input_root)
    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(args.work_dir, exist_ok=True)
    base_status = "input_error" if plan.get("case_json_missing") else "partial"
    if args.skeleton_only:
        result = build_result(plan, {"files": [], "directories": [], "errors": [],
                                     "degradations": [], "stats": {}},
                              {"schema": "library-model/1", "duplicate_groups": [],
                               "version_candidates": [], "aliases": [], "catalogs": [],
                               "reconciliation": {}, "index": {}, "file_name_index": {},
                               "heuristics_applied": [], "degradations": []},
                              {"schema": "evidence-model/1", "case_id": plan["case_id"], "claims": []},
                              base_status, args.case_json, args.output_dir)
        if plan.get("case_json_missing"):
            result["errors"] = result.get("errors", []) + [
                {"kind": "missing_case_json", "path": args.case_json, "detail": "case.json not found"}]
        write_json(os.path.join(args.output_dir, "result.json"), result)
        write_json(os.path.join(args.work_dir, "plan.json"), plan)
        print("skeleton: case_id=%s status=%s -> %s/result.json" % (
            plan["case_id"], base_status, args.output_dir))
        return 0
    inventory = walk_inventory(args.input_root, args.work_dir)
    model = build_library_model(inventory, args.input_root)
    evidence = build_evidence_model(plan, inventory, model)
    result = build_result(plan, inventory, model, evidence, base_status,
                          args.case_json, args.output_dir)
    artifacts, result = write_artifacts(args.output_dir, plan, inventory, model, evidence, result)
    n_files = len(inventory["files"])
    print("finalize: %d files, %d dup groups, %d version candidates, %d aliases, "
          "%d orphaned, %d index terms -> %s" % (
              n_files, len(model["duplicate_groups"]), len(model["version_candidates"]),
              len(model["aliases"]), len(model["reconciliation"].get("orphaned", [])),
              len(model["index"]), result["computed"]["artifacts"].get("result.json")))
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="main.py", description="library-retrieval Skill")
    p.add_argument("--case-json", default="/input/case.json")
    p.add_argument("--task-path", default="/input/task.md")
    p.add_argument("--input-root", default="/input")
    p.add_argument("--output-dir", default="/app/output")
    p.add_argument("--work-dir", default="/app/work")
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", metavar="PATCH.json", help="deep-merge a patch into result.json")
    p.add_argument("--result", metavar="RESULT.json", default="/app/output/result.json")
    p.add_argument("--check", metavar="RESULT.json", help="verify an existing result.json")
    p.add_argument("--query", metavar="TERM", help="search the built index")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.check:
        sys.exit(check_result(args.check, args.case_json, args.input_root))
    if args.query:
        sys.exit(query(args.query, args.output_dir))
    if args.update:
        patch = load_json(args.update, {})
        result = load_json(args.result, {})
        if not isinstance(result, dict) or not isinstance(patch, dict):
            print("update failed: result or patch is not a dict")
            sys.exit(1)
        deep_merge(result, patch)
        result["change_log"] = result.get("change_log", []) + [{
            "time": utcnow_iso(), "actor": "skill-script", "step": "update",
            "detail": "merged patch from %s" % os.path.basename(args.update),
        }]
        write_json(args.result, result)
        print("updated %s" % args.result)
        sys.exit(0)
    sys.exit(build_pipeline(args))


if __name__ == "__main__":
    main()
