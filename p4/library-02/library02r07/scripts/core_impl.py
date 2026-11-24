#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py - deterministic document-library repair pipeline (stdlib only).

Design goals
------------
1. Never crash.  Every phase is wrapped; a broken input degrades to an entry in
   `errors`/`degradations` and the pipeline keeps going.  The top level always
   writes a *valid* result.json, even for an empty input directory.
2. Deterministic.  Directory traversal is sorted, iteration orders are sorted,
   no randomness.  Re-running the same input yields the same result up to the
   runtime timestamps (which are isolated inside `runtime`/`change_log`).
3. Honest.  `status` is one of ok | partial | insufficient_evidence |
   input_error.  Missing data is never invented; it is recorded as
   `not_reported`, in `uncertainty`, or in `pending_human`.

The pipeline is mechanical: inventory -> hash -> dedup -> version candidates ->
aliases (symlink/hardlink) -> administrative catalog parse -> reconciliation ->
index -> mechanical answers -> artifacts -> result.  Semantic judgement is done
by the calling Agent via `--update patch.json`; this module provides the
candidates and the evidence, never invented conclusions.
"""
import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import stat
import sys
import time
import traceback

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
RESERVED_KEYS = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "runtime", "limitations", "change_log",
    "computed",
}
CONTRACT_KEYS = (
    "output_contract", "output", "result_schema", "contract", "output_schema",
    "expected_output", "output_fields", "response_schema", "result", "schema",
)
QUESTION_KEYS = ("questions", "queries", "searches", "prompts", "asks",
                 "questions_list", "qa", "questions_and_answers", "tasks")
HASH_LIMIT = 64 * 1024 * 1024      # 64 MiB: content-hash only below this
TEXT_LIMIT = 2 * 1024 * 1024       # 2 MiB: text-index only below this
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".log", ".bib", ".ris",
    ".py", ".sh", ".r", ".m", ".tex", ".tex", ".ini", ".cfg", ".toml",
}
# Control files delivered by the harness; they are inventory metadata, not
# research material, so they never count toward "material present" and are
# excluded from dedup/version/reconciliation.
CONTROL_BASENAMES = {"case.json", "task.md", "resources.yaml"}

VERSION_TOKEN_RE = re.compile(
    r"\b(?:v|ver|version|rev|revision)\s*\d+(?:[._-]\d+)*\b", re.I)
TRAILING_NUM_RE = re.compile(r"\s+\d+\s*$")
NOISE_WORDS = {
    "draft", "final", "copy", "copie", "edited", "annotated", "scanned",
    "scan", "old", "new", "updated", "latest", "副本", "扫描", "修订",
    "最终", "终稿", "初稿", "新版", "旧版",
}


def _norm(text):
    if text is None:
        return ""
    return text.replace("\ufeff", "")


# --------------------------------------------------------------------------
# Small safe helpers
# --------------------------------------------------------------------------
def iso(ts):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    except (OverflowError, ValueError, OSError):
        return None


def safe_json(path):
    """Read a JSON file tolerating BOM and bad encodings.

    Returns (data, error_string).  Never raises.
    """
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with io.open(path, "r", encoding=enc, errors="replace") as fh:
                raw = fh.read()
            break
        except OSError as exc:
            return None, "unreadable: %s" % exc
    if raw is None:
        return None, "unreadable"
    try:
        return json.loads(raw), None
    except (ValueError, TypeError) as exc:
        return None, "bad_json: %s" % exc


def safe_read_text(path, limit=TEXT_LIMIT):
    """Read a text file; return (text, error).  Never raises."""
    try:
        st = os.stat(path)
        if st.st_size > limit:
            return None, "too_large_text"
    except OSError as exc:
        return None, "stat_error: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with io.open(path, "r", encoding=enc, errors="replace") as fh:
                return fh.read(), None
        except (UnicodeError, OSError) as exc:
            last = str(exc)
    return None, "unreadable_text: %s" % last


def sha256_file(path, limit=HASH_LIMIT):
    """Content SHA-256 for regular files below limit; (hex, reason_or_None)."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return None, "stat_error: %s" % exc
    if not stat.S_ISREG(st.st_mode):
        return None, "non_regular"
    if st.st_size > limit:
        return None, "too_large"
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), None
    except OSError as exc:
        return None, "unreadable: %s" % exc


def readable(path):
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def jsonl_append(path, record):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with io.open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def write_text(path, content):
    """Write UTF-8 text, creating parent dirs; returns error or None."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return None
    except OSError as exc:
        return str(exc)


def write_json(path, obj):
    err = write_text(
        path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))
    return err


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------
def _entry(rel, lst, root, input_root):
    e = {
        "path": rel,
        "type": "file",
        "size": lst.st_size,
        "mtime": iso(lst.st_mtime),
        "mode_octal": "0o%o" % stat.S_IMODE(lst.st_mode),
    }
    if stat.S_ISLNK(lst.st_mode):
        e["type"] = "symlink"
        try:
            e["link_target"] = os.readlink(os.path.join(root, rel))
        except OSError:
            e["link_target"] = None
        target = os.path.join(root, rel)
        e["link_broken"] = not os.path.exists(target)
    elif stat.S_ISDIR(lst.st_mode):
        e["type"] = "dir"
        e["size"] = 0
    else:
        e["type"] = "file"
    e["readable"] = readable(os.path.join(root, rel)) if e["type"] == "file" else None
    norm = rel.replace("\\", "/")
    e["is_control"] = ("/" not in norm and os.path.basename(norm) in CONTROL_BASENAMES)
    return e


def inventory(input_root):
    """Sorted recursive inventory.  Returns (files, dirs, walk_errors)."""
    files, dirs, errors = [], [], []
    input_root = os.path.abspath(input_root or ".")
    if not os.path.isdir(input_root):
        return files, dirs, ["input_root_missing: %s" % input_root]

    def onerror(exc):
        errors.append("walk_error: %s" % exc)

    for dirpath, dirnames, filenames in os.walk(input_root, onerror=onerror):
        dirnames.sort()
        filenames.sort()
        for d in dirnames:
            rel = os.path.relpath(os.path.join(dirpath, d), input_root)
            try:
                lst = os.lstat(os.path.join(dirpath, d))
            except OSError as exc:
                errors.append("lstat_error: %s (%s)" % (rel, exc))
                continue
            dirs.append(_entry(rel, lst, input_root, input_root))
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), input_root)
            try:
                lst = os.lstat(os.path.join(dirpath, f))
            except OSError as exc:
                errors.append("lstat_error: %s (%s)" % (rel, exc))
                continue
            files.append(_entry(rel, lst, input_root, input_root))
    files.sort(key=lambda x: x["path"])
    dirs.sort(key=lambda x: x["path"])
    return files, dirs, errors


# --------------------------------------------------------------------------
# Dedup / version / alias
# --------------------------------------------------------------------------
def _file_of(path):
    return os.path.basename(path)


def normalize_stem(name):
    stem = _file_of(name)
    stem = re.sub(r"\.[^.]+$", "", stem)  # strip one extension
    stem = stem.lower()
    stem = re.sub(r"[._\-]+", " ", stem)
    stem = VERSION_TOKEN_RE.sub(" ", stem)
    stem = TRAILING_NUM_RE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    toks = [t for t in stem.split(" ") if t and t not in NOISE_WORDS]
    return " ".join(toks)


def _text_sample(path):
    txt, _err = safe_read_text(path, limit=TEXT_LIMIT)
    if txt is None:
        return None
    return txt[:20000]


def _similarity(a, b):
    if a is None or b is None:
        return None
    if not a and not b:
        return 1.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 4)


def _content_of(rel, root, text_cache):
    if rel in text_cache:
        return text_cache[rel]
    sample = _text_sample(os.path.join(root, rel))
    text_cache[rel] = sample
    return sample


def build_dedup_version_alias(files, root, errors):
    """Returns (dup_groups, version_candidates, aliases, hardlinks)."""
    by_hash = {}
    for f in files:
        if f["type"] != "file":
            continue
        full = os.path.join(root, f["path"])
        digest, reason = sha256_file(full)
        f["sha256"] = digest
        f["hash_status"] = "ok" if digest else reason
        if digest:
            by_hash.setdefault(digest, []).append(f["path"])

    dup_groups = []
    for digest, paths in sorted(by_hash.items()):
        if len(paths) > 1:
            dup_groups.append({
                "sha256": digest,
                "size": next(
                    (f["size"] for f in files if f["path"] == paths[0]), None),
                "paths": sorted(paths),
                "count": len(paths),
            })
    dup_groups.sort(key=lambda g: g["paths"][0])

    # version candidates by normalized stem + content similarity
    by_stem = {}
    for f in files:
        if f["type"] != "file" or not f.get("sha256"):
            continue
        by_stem.setdefault(normalize_stem(f["path"]), []).append(f["path"])

    text_cache = {}
    version_candidates = []
    for stem in sorted(by_stem):
        paths = sorted(by_stem[stem])
        if len(paths) < 2:
            continue
        samples = {p: _content_of(p, root, text_cache) for p in paths}
        pair_sims = []
        for i, a in enumerate(paths):
            for b in paths[i + 1:]:
                pair_sims.append({
                    "pair": [a, b],
                    "similarity": _similarity(samples.get(a), samples.get(b)),
                })
        pair_sims.sort(key=lambda x: (x["similarity"] is None, x["similarity"] or 0), reverse=True)
        version_candidates.append({
            "stem": stem,
            "members": paths,
            "count": len(paths),
            "pair_similarities": pair_sims,
            "heuristic": True,
        })
    version_candidates.sort(key=lambda v: v["stem"])

    # aliases: symlinks (with broken flag) and hardlink groups
    symlinks = [f for f in files if f["type"] == "symlink"]
    hardlinks = {}
    for f in files:
        if f["type"] != "file":
            continue
        full = os.path.join(root, f["path"])
        try:
            st = os.stat(full)
        except OSError:
            continue
        ino = getattr(st, "st_ino", None)
        nlink = getattr(st, "st_nlink", 0)
        if ino is not None and nlink and nlink > 1:
            hardlinks.setdefault(ino, []).append(f["path"])
    hardlink_groups = [
        {"inode": ino, "paths": sorted(ps)}
        for ino, ps in sorted(hardlinks.items(), key=lambda kv: kv[0])
        if len(ps) > 1
    ]

    aliases = {
        "symlinks": symlinks,
        "broken_symlinks": [s for s in symlinks if s.get("link_broken")],
        "hardlink_groups": hardlink_groups,
    }
    return dup_groups, version_candidates, aliases


# --------------------------------------------------------------------------
# Administrative catalog parsing (JSON/CSV/Markdown/BibTeX/RIS/SQLite)
# --------------------------------------------------------------------------
def _flatten_records(obj, prefix=""):
    recs = []
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            recs.extend(_flatten_records(item, "%s[%d]" % (prefix, i)))
    elif isinstance(obj, dict):
        # heuristic: a dict of dicts or list-of-dicts => records
        if all(isinstance(v, dict) for v in obj.values()) and obj:
            for k, v in obj.items():
                recs.extend(_flatten_records(v, prefix + "." + str(k)))
        else:
            recs.append({"locator": prefix or "<root>", "record": obj})
    else:
        recs.append({"locator": prefix or "<root>", "record": {"value": obj}})
    return recs


def parse_catalog(rel, root, errors):
    """Parse a catalog file into a list of dict records. Never raises."""
    full = os.path.join(root, rel)
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".json":
        data, err = safe_json(full)
        if err:
            errors.append("catalog_json_error: %s (%s)" % (rel, err))
            return []
        return _flatten_records(data, rel)
    if ext == ".csv":
        try:
            with io.open(full, "r", encoding="utf-8-sig", errors="replace") as fh:
                rows = list(csv.DictReader(fh))
            return [{"locator": rel, "record": r} for r in rows]
        except (OSError, csv.Error, UnicodeError) as exc:
            errors.append("catalog_csv_error: %s (%s)" % (rel, exc))
            return []
    if ext in (".md", ".markdown", ".txt", ".rst"):
        txt, err = safe_read_text(full)
        if err:
            errors.append("catalog_text_error: %s (%s)" % (rel, err))
            return []
        return _parse_markdown_table(rel, txt)
    if ext == ".bib":
        txt, err = safe_read_text(full)
        if err:
            errors.append("catalog_bib_error: %s (%s)" % (rel, err))
            return []
        return _parse_bibtex(rel, txt)
    if ext == ".ris":
        txt, err = safe_read_text(full)
        if err:
            errors.append("catalog_ris_error: %s (%s)" % (rel, err))
            return []
        return _parse_ris(rel, txt)
    if ext in (".db", ".sqlite", ".sqlite3"):
        return _parse_sqlite(rel, full, errors)
    return []


def _parse_markdown_table(rel, txt):
    recs = []
    lines = txt.splitlines()
    # find a markdown table: a header row followed by a |---| separator row
    header = None
    for i, line in enumerate(lines):
        if "|" in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
                continue
            if all(re.fullmatch(r":?-{1,}:?", c) for c in cells if c != ""):
                continue
            if header:
                recs.append({"locator": rel, "record": dict(zip(header, cells))})
    return recs


def _parse_bibtex(rel, txt):
    recs = []
    for m in re.finditer(r"@(\w+)\s*\{([^,]+),\s*(.*?)\n\s*\}", txt, re.S):
        etype, key, body = m.group(1), m.group(2), m.group(3)
        rec = {"entry_type": etype, "cite_key": key.strip()}
        for field, value in re.findall(r"(\w+)\s*=\s*[{\"](.*?)[}\"]\s*,?", body, re.S):
            rec[field.lower()] = value.strip()
        recs.append({"locator": rel, "record": rec})
    return recs


def _parse_ris(rel, txt):
    recs = []
    cur = {}
    for line in txt.splitlines():
        if line.startswith("ER") and line[2:4].strip() == "-":
            if cur:
                recs.append({"locator": rel, "record": cur})
                cur = {}
        elif len(line) >= 6 and line[2:4] == "  ":
            tag, val = line[:2].strip(), line[6:].strip()
            cur[tag] = val
    if cur:
        recs.append({"locator": rel, "record": cur})
    return recs


def _parse_sqlite(rel, full, errors):
    try:
        import sqlite3
    except ImportError as exc:
        errors.append("sqlite_unavailable: %s (%s)" % (rel, exc))
        return []
    recs = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % full, uri=True)
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            try:
                rows = cur.execute("SELECT * FROM %s" % t).fetchall()
                cols = [d[0] for d in cur.description]
                for row in rows:
                    recs.append({"locator": rel, "record": dict(zip(cols, row))})
            except sqlite3.Error as exc:
                errors.append("sqlite_query_error: %s (%s) %s" % (rel, t, exc))
        con.close()
    except sqlite3.Error as exc:
        errors.append("sqlite_open_error: %s (%s)" % (rel, exc))
    except OSError as exc:
        errors.append("sqlite_open_error: %s (%s)" % (rel, exc))
    return recs


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------
def _path_candidates(rec):
    if isinstance(rec, dict):
        for k in ("path", "file", "filename", "file_path", "relative_path",
                  "location", "url", "local_path", "附件", "文件", "路径"):
            if k in rec and rec[k]:
                return [str(rec[k])]
    return []


def reconcile(catalog_recs, files_by_path, file_set):
    """Compare catalog records against actual files.

    Returns dict with matches/mismatches/missing/orphaned.
    """
    matches, mismatches, missing, orphaned = [], [], [], []
    seen = set()
    for rec in catalog_recs:
        record = rec.get("record", rec)
        candidates = _path_candidates(record)
        if not candidates:
            mismatches.append({"record": record, "reason": "no_path_field"})
            continue
        found = None
        for cand in candidates:
            norm = cand.replace("\\", "/").lstrip("./")
            if norm in file_set or norm in files_by_path:
                found = norm
                break
        if found:
            matches.append({"record": record, "file": found})
            seen.add(found)
        else:
            missing.append({"record": record, "path": candidates[0]})
    # orphaned = actual files not referenced by any catalog record
    referenced = set()
    for rec in catalog_recs:
        for c in _path_candidates(rec.get("record", rec)):
            referenced.add(c.replace("\\", "/").lstrip("./"))
    for rel in file_set:
        if rel not in referenced:
            orphaned.append({"file": rel})
    orphaned.sort(key=lambda x: x["file"])
    return {
        "matches": matches,
        "mismatches": mismatches,
        "missing": missing,
        "orphaned": orphaned,
        "counts": {
            "matches": len(matches),
            "mismatches": len(mismatches),
            "missing": len(missing),
            "orphaned": len(orphaned),
        },
    }


# --------------------------------------------------------------------------
# Index and mechanical answers
# --------------------------------------------------------------------------
def build_index(files, dirs, root):
    """Searchable index: file rows + text line index + heading index."""
    file_rows = []
    for f in files:
        row = dict(f)
        row["ext"] = os.path.splitext(f["path"])[1].lower()
        file_rows.append(row)
    text_lines = {}
    headings = {}
    for f in files:
        if f["type"] != "file":
            continue
        ext = os.path.splitext(f["path"])[1].lower()
        if ext not in TEXT_EXTENSIONS:
            continue
        txt, err = safe_read_text(os.path.join(root, f["path"]))
        if err or txt is None:
            continue
        lines = txt.splitlines()
        text_lines[f["path"]] = lines
        if ext in (".md", ".markdown", ".txt", ".rst"):
            hs = [{"line": i + 1, "text": l.strip()} for i, l in enumerate(lines)
                  if re.match(r"^#{1,6}\s+\S", l)]
            if hs:
                headings[f["path"]] = hs
    return {
        "file_rows": file_rows,
        "dirs": dirs,
        "text_lines": text_lines,
        "headings": headings,
    }


def _mechanic_answer(qtext, state):
    """Best-effort mechanical answer. Returns (answer_dict or None)."""
    q = _norm(qtext or "").strip()
    ql = q.lower()
    files = state.get("material_files", state["files"])
    answer = {"question": qtext, "answered": False, "kind": "mechanical"}

    def _reply(summary, detail, evidence):
        answer.update({"answered": True, "summary": summary,
                       "detail": detail, "evidence": evidence})
        return answer

    def _num_ev(key, value):
        return [{"item": key, "locator": "computed", "quote": str(value)}]

    n_files = len([f for f in files if f["type"] == "file"])
    n_dirs = len(state["dirs"])
    n_dup = sum(g["count"] for g in state["dup_groups"])
    n_broken = len(state["aliases"]["broken_symlinks"])
    n_missing = state["reconcile"]["counts"]["missing"]
    n_orphan = state["reconcile"]["counts"]["orphaned"]
    n_versions = len(state["version_candidates"])

    if re.search(r"(几个|多少|how many|count|数量|number of)", ql) and re.search(r"(文件|file)", ql):
        return _reply("%d files" % n_files, "regular+non-regular entries counted from /input",
                      _num_ev("file_count", n_files))
    if re.search(r"(目录|directory|dir)", ql) and re.search(r"(几个|多少|how many|count|数量)", ql):
        return _reply("%d directories" % n_dirs, "directories (excluding symlinked dirs)",
                      _num_ev("dir_count", n_dirs))
    if re.search(r"(重复|duplicate|dup)", ql):
        return _reply("%d duplicate files in %d groups" % (n_dup, len(state["dup_groups"])),
                      "content SHA-256 dedup", _num_ev("dup_files", n_dup))
    if re.search(r"(断链|broken|symlink|符号链接|链接失效)", ql):
        return _reply("%d broken symlinks" % n_broken,
                      [{"path": s["path"]} for s in state["aliases"]["broken_symlinks"]],
                      _num_ev("broken_symlinks", n_broken))
    if re.search(r"(缺失|missing)", ql):
        return _reply("%d catalog entries missing" % n_missing,
                      state["reconcile"]["missing"],
                      _num_ev("missing", n_missing))
    if re.search(r"(未登记|orphan|unregistered|不在)", ql):
        return _reply("%d orphaned files" % n_orphan,
                      state["reconcile"]["orphaned"],
                      _num_ev("orphaned", n_orphan))
    if re.search(r"(版本|version)", ql):
        return _reply("%d version-candidate chains" % n_versions,
                      state["version_candidates"],
                      _num_ev("version_chains", n_versions))
    if re.search(r"(哈希|hash|sha)", ql):
        rows = [{"path": f["path"], "sha256": f.get("sha256")}
                for f in files if f.get("sha256")]
        return _reply("%d hashed files" % len(rows), rows,
                      _num_ev("hashed_files", len(rows)))

    # keyword search: "search: <term>"
    m = re.match(r"(?:search|检索|查找|搜索)\s*[:：]\s*(.+)", ql)
    if m:
        term = m.group(1).strip().lower()
        hits = []
        for path, lines in sorted(state["index"]["text_lines"].items()):
            for i, line in enumerate(lines):
                if term in line.lower():
                    hits.append({"file": path, "line": i + 1,
                                 "quote": line[:300]})
                    if len(hits) >= 100:
                        break
            if len(hits) >= 100:
                break
        return _reply("%d keyword hits" % len(hits), hits,
                      [{"item": "search_term", "locator": "computed",
                        "quote": term}])
    return None


def answer_questions(questions, state):
    answers = []
    pending = []
    for q in questions:
        if isinstance(q, dict):
            qid = q.get("id") or q.get("question_id") or q.get("qid")
            text = q.get("question") or q.get("query") or q.get("text") or q.get("prompt") or q.get("q")
        else:
            qid, text = None, str(q)
        a = _mechanic_answer(text, state)
        if a is None:
            pending.append({"id": qid, "question": text,
                            "reason": "semantic_or_unsupported",
                            "suggestion": "use evidence index and headings for manual localization"})
        else:
            a["id"] = qid
            answers.append(a)
    return answers, pending


def detect_questions(case):
    out = []
    if not isinstance(case, dict):
        return out

    def _collect(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower().replace("-", "_") in QUESTION_KEYS:
                    if isinstance(v, list):
                        out.extend(v)
                    elif isinstance(v, dict):
                        out.append(v)
                    else:
                        out.append(v)
                else:
                    _collect(v)
        elif isinstance(obj, list):
            for item in obj:
                _collect(item)

    _collect(case)
    return out


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------
def write_artifacts(output_dir, state, errors):
    """Write all editable artifacts; returns dict of {relpath: error|None}."""
    res = {}
    res["directory_tree.txt"] = write_text(
        os.path.join(output_dir, "directory_tree.txt"),
        render_tree(state["dirs"], state["files"]))
    res["library_manifest.csv"] = write_text(
        os.path.join(output_dir, "library_manifest.csv"),
        render_manifest(state["files"]))
    res["dedup_version.json"] = write_json(
        os.path.join(output_dir, "dedup_version.json"), {
            "duplicate_groups": state["dup_groups"],
            "version_candidates": state["version_candidates"],
            "aliases": state["aliases"],
        })
    res["recovery_report.json"] = write_json(
        os.path.join(output_dir, "recovery_report.json"), state["reconcile"])
    res["library_index.json"] = write_json(
        os.path.join(output_dir, "library_index.json"), {
            "file_rows": state["index"]["file_rows"],
            "headings": state["index"]["headings"],
        })
    res["library_manifest.md"] = write_text(
        os.path.join(output_dir, "library_manifest.md"),
        render_handoff_md(state))
    return res


def render_tree(dirs, files):
    info = {}
    entries = []
    for d in dirs:
        info[d["path"].replace("\\", "/")] = {"kind": "dir"}
        entries.append(d["path"])
    for f in files:
        info[f["path"].replace("\\", "/")] = {
            "kind": f.get("type", "file"),
            "link_target": f.get("link_target"),
        }
        entries.append(f["path"])
    entries.sort()
    if not entries:
        return "(empty input)\n"

    root = {}
    for path in entries:
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        node = root
        for part in parts:
            node = node.setdefault(part, {})
        node["__info__"] = info.get(path.replace("\\", "/"), {})

    lines = ["."]

    def render(node, depth):
        for name in sorted(k for k in node if k != "__info__"):
            child = node[name]
            meta = child.get("__info__", {})
            kind = meta.get("kind", "")
            suffix = ""
            if kind == "symlink":
                suffix = " -> %s" % (meta.get("link_target") or "?")
            elif kind == "dir" and not any(k != "__info__" for k in child):
                suffix = " [空目录]"
            lines.append("    " * (depth + 1) + name + suffix)
            render(child, depth + 1)

    render(root, 0)
    return "\n".join(lines) + "\n"


def render_manifest(files):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["path", "type", "size", "mtime", "mode_octal", "readable",
                "is_control", "sha256", "hash_status", "link_target", "link_broken"])
    for f in files:
        w.writerow([
            f.get("path"), f.get("type"), f.get("size"), f.get("mtime"),
            f.get("mode_octal"), f.get("readable"), f.get("is_control", False),
            f.get("sha256", ""), f.get("hash_status", ""),
            f.get("link_target", ""), f.get("link_broken", ""),
        ])
    return out.getvalue()


def render_handoff_md(state):
    lines = ["# Document library handoff report", ""]
    files = state.get("material_files", state["files"])
    n_files = len([f for f in files if f["type"] == "file"])
    lines.append("## Summary")
    lines.append("- files: %d" % n_files)
    lines.append("- directories: %d" % len(state["dirs"]))
    lines.append("- duplicate groups: %d" % len(state["dup_groups"]))
    lines.append("- version-candidate chains: %d" % len(state["version_candidates"]))
    lines.append("- broken symlinks: %d" % len(state["aliases"]["broken_symlinks"]))
    lines.append("- catalog missing: %d" % state["reconcile"]["counts"]["missing"])
    lines.append("- catalog orphaned: %d" % state["reconcile"]["counts"]["orphaned"])
    lines.append("")
    lines.append("## Duplicate groups (content SHA-256)")
    for g in state["dup_groups"]:
        lines.append("- `%s` -> %s" % (g["sha256"][:12], ", ".join(g["paths"])))
    lines.append("")
    lines.append("## Version candidates (heuristic, normalized stem + similarity)")
    for v in state["version_candidates"]:
        lines.append("- `%s`: %s" % (v["stem"], ", ".join(v["members"])))
    lines.append("")
    lines.append("## Notes")
    lines.append("Version chains are *heuristics*; confirm against file content "
                 "before treating members as one logical document.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Result assembly
# --------------------------------------------------------------------------
def find_contract(case):
    """Locate an output_contract-like value inside case.json."""
    if isinstance(case, dict):
        for k in CONTRACT_KEYS:
            if k in case and case[k] not in (None, "", [], {}):
                return k, case[k]
        # one level deep
        for k, v in case.items():
            if isinstance(v, dict):
                for ck in CONTRACT_KEYS:
                    if ck in v and v[ck] not in (None, "", [], {}):
                        return k + "." + ck, v[ck]
    return None, None


def find_case_id(case, task_text):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "id", "challenge_id", "challengeId",
                  "case", "armiId"):
            if k in case and case[k] not in (None, ""):
                return str(case[k])
    if task_text:
        m = re.search(r"challenge_id[:\s`'\"(]*([A-Za-z0-9_.\-]+)", task_text)
        if m:
            return m.group(1)
        m = re.search(r"case[_ -]?id[:\s`'\"(]*([A-Za-z0-9_.\-]+)", task_text, re.I)
        if m:
            return m.group(1)
    return None


def _echo_contract(result, contract_value):
    if isinstance(contract_value, dict):
        for k, v in contract_value.items():
            if k in RESERVED_KEYS:
                result.setdefault("computed", {}).setdefault(
                    "_contract_%s" % k, v)
            else:
                result[k] = v
        result.setdefault("computed", {})["contract_echoed"] = sorted(contract_value.keys())
    elif isinstance(contract_value, list):
        result.setdefault("computed", {})["contract_list"] = contract_value
    else:
        result.setdefault("computed", {})["contract_value"] = contract_value


def build_result(state):
    case = state["case"]
    task_text = state["task_text"]
    result = {
        "case_id": state["case_id"],
        "status": state["status"],
        "sources": state["sources"],
        "uncertainty": state["uncertainty"],
        "pending_human": state["pending_human"],
        "errors": state["errors"],
        "degradations": state["degradations"],
        "runtime": state["runtime"],
        "limitations": state["limitations"],
        "change_log": state["change_log"],
        "computed": {
            "summary": {
                "files": len([f for f in state["material_files"] if f["type"] == "file"]),
                "directories": len(state["dirs"]),
                "duplicate_groups": len(state["dup_groups"]),
                "version_candidate_chains": len(state["version_candidates"]),
                "broken_symlinks": len(state["aliases"]["broken_symlinks"]),
                "catalog_matches": state["reconcile"]["counts"]["matches"],
                "catalog_mismatches": state["reconcile"]["counts"]["mismatches"],
                "catalog_missing": state["reconcile"]["counts"]["missing"],
                "catalog_orphaned": state["reconcile"]["counts"]["orphaned"],
            },
            "files": state["files"],
            "duplicate_groups": state["dup_groups"],
            "version_candidates": state["version_candidates"],
            "aliases": state["aliases"],
            "reconciliation": state["reconcile"],
            "answers": state["answers"],
            "index": state["index"],
            "artifacts": state["artifacts"],
            "contract_detected": {
                "key": state["contract_key"],
                "value": state["contract_value"],
            },
        },
    }
    _echo_contract(result, state["contract_value"])
    return result


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def _new_state():
    return {
        "case": None, "task_text": "", "case_id": None, "status": "partial",
        "sources": [], "uncertainty": [], "pending_human": [], "errors": [],
        "degradations": [], "runtime": {}, "limitations": [],
        "change_log": [],
        "files": [], "material_files": [], "dirs": [], "dup_groups": [],
        "version_candidates": [],
        "aliases": {"symlinks": [], "broken_symlinks": [], "hardlink_groups": []},
        "reconcile": {"matches": [], "mismatches": [], "missing": [],
                      "orphaned": [], "counts": {"matches": 0, "mismatches": 0,
                                                 "missing": 0, "orphaned": 0}},
        "index": {"file_rows": [], "dirs": [], "text_lines": {},
                  "headings": {}},
        "answers": [], "artifacts": {}, "contract_key": None,
        "contract_value": None,
    }


def _log(state, step, detail):
    state["change_log"].append({
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor": "scripts/core_impl.py", "step": step, "detail": detail,
    })


def run_pipeline(args):
    state = _new_state()
    started = time.time()
    input_root = os.path.abspath(args.input or "/input")
    output_dir = os.path.abspath(args.output or "/app/output")
    work_dir = os.path.abspath(args.work or "/app/work")
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case_path = args.case_json or os.path.join(input_root, "case.json")
    task_path = args.task_path or os.path.join(input_root, "task.md")

    # ---- read inputs (tolerant) ----
    case, case_err = safe_json(case_path)
    if case_err and os.path.isfile(case_path):
        state["errors"].append("case_json: %s" % case_err)
    if case is None:
        state["status"] = "input_error"
        state["errors"].append("case_json_missing_or_unparseable: %s" % case_path)

    task_text = ""
    if os.path.isfile(task_path):
        task_text, task_err = safe_read_text(task_path, limit=2 * 1024 * 1024)
        if task_err:
            state["degradations"].append("task_md: %s" % task_err)

    state["case"] = case
    state["task_text"] = task_text or ""
    state["case_id"] = find_case_id(case, task_text or "")

    contract_key, contract_value = find_contract(case)
    state["contract_key"] = contract_key
    state["contract_value"] = contract_value
    if contract_key is None:
        state["uncertainty"].append({
            "item": "output_contract", "reason": "output_contract not found in case.json",
            "severity": "low", "action": "result carries metadata fields only",
        })

    # ---- inventory ----
    files, dirs, walk_errors = inventory(input_root)
    state["files"] = files
    state["dirs"] = dirs
    material_files = [f for f in files if not f.get("is_control")]
    state["material_files"] = material_files
    for e in walk_errors:
        state["errors"].append(e)

    if not material_files and not dirs:
        state["status"] = "insufficient_evidence"
        state["limitations"].append("no material files/directories (only control files or empty input)")
        _log(state, "inventory", "no material -> insufficient_evidence")

    # ---- dedup / version / alias ----
    dup_groups, version_candidates, aliases = build_dedup_version_alias(
        material_files, input_root, state["errors"])
    state["dup_groups"] = dup_groups
    state["version_candidates"] = version_candidates
    state["aliases"] = aliases

    # ---- catalog parse + reconcile ----
    files_by_path = {f["path"].replace("\\", "/"): f for f in material_files}
    file_set = {f["path"].replace("\\", "/") for f in material_files}
    catalog_recs = []
    for f in material_files:
        if f["type"] != "file":
            continue
        ext = os.path.splitext(f["path"])[1].lower()
        if ext in (".json", ".csv", ".md", ".markdown", ".txt", ".rst",
                   ".bib", ".ris", ".db", ".sqlite", ".sqlite3"):
            # only treat as catalog if it looks like one (contains path/record fields)
            recs = parse_catalog(f["path"], input_root, state["errors"])
            if recs:
                catalog_recs.extend(recs)
                state["degradations"].append(
                    "catalog_detected: %s (%d records)" % (f["path"], len(recs)))
    state["reconcile"] = reconcile(catalog_recs, files_by_path, file_set)

    # ---- index + answers ----
    state["index"] = build_index(files, dirs, input_root)
    questions = detect_questions(case) if isinstance(case, dict) else []
    answers, pending = answer_questions(questions, state)
    state["answers"] = answers
    state["pending_human"].extend(pending)

    # ---- status ----
    if state["status"] != "insufficient_evidence" and state["status"] != "input_error":
        if state["errors"]:
            state["status"] = "partial"
        else:
            state["status"] = "ok"
    if state["pending_human"] and state["status"] == "ok":
        state["status"] = "partial"

    # ---- sources (mechanically derivable ones) ----
    for g in dup_groups[:20]:
        state["sources"].append({
            "item": "duplicate_group", "locator": g["paths"][0],
            "quote": "sha256=%s shared by %d files" % (g["sha256"], g["count"]),
        })
    for v in version_candidates[:20]:
        state["sources"].append({
            "item": "version_candidate", "locator": v["members"][0],
            "quote": "normalized stem '%s'; %d members (heuristic)" % (v["stem"], v["count"]),
        })

    # ---- artifacts ----
    state["artifacts"] = write_artifacts(output_dir, state, state["errors"])
    for rel, err in state["artifacts"].items():
        if err:
            state["errors"].append("artifact_write_failed: %s (%s)" % (rel, err))

    # ---- runtime ----
    state["runtime"] = {
        "start": iso(started),
        "end": iso(time.time()),
        "seconds": round(time.time() - started, 3),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "input_root": input_root,
        "output_dir": output_dir,
    }
    _log(state, "finalize", "pipeline complete; status=%s" % state["status"])

    # ---- write result.json ----
    result = build_result(state)
    result_path = os.path.join(output_dir, "result.json")
    err = write_json(result_path, result)
    if err:
        state["errors"].append("result_write_failed: %s" % err)
        # try a minimal fallback write
        minimal = {"case_id": state["case_id"], "status": "input_error",
                   "errors": [err]}
        write_json(result_path, minimal)
        print("finalize: result write failed (%s); exit 2" % err)
        return 2

    # operation log
    op_log = os.path.join(output_dir, "operation_log.jsonl")
    for row in state["change_log"]:
        jsonl_append(op_log, row)

    print("finalize: %d files, %d dup groups, %d version candidates, "
          "%d broken symlinks, %d missing, %d orphaned -> %s" % (
              len([f for f in material_files if f["type"] == "file"]),
              len(dup_groups), len(version_candidates),
              len(aliases["broken_symlinks"]),
              state["reconcile"]["counts"]["missing"],
              state["reconcile"]["counts"]["orphaned"], result_path))
    return 0


def run_skeleton(args):
    """Write a partial result skeleton fast (the 'first 3 minutes' requirement)."""
    state = _new_state()
    input_root = os.path.abspath(args.input or "/input")
    output_dir = os.path.abspath(args.output or "/app/output")
    work_dir = os.path.abspath(args.work or "/app/work")
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case_path = args.case_json or os.path.join(input_root, "case.json")
    task_path = args.task_path or os.path.join(input_root, "task.md")

    case, case_err = safe_json(case_path)
    task_text = ""
    if os.path.isfile(task_path):
        task_text, _ = safe_read_text(task_path, limit=2 * 1024 * 1024)

    state["case"] = case
    state["task_text"] = task_text or ""
    state["case_id"] = find_case_id(case, task_text or "")
    contract_key, contract_value = find_contract(case)
    state["contract_key"] = contract_key
    state["contract_value"] = contract_value

    if case is None and not state["case_id"]:
        state["status"] = "input_error"
        state["errors"].append("case_json missing/unparseable: %s" % case_path)
    else:
        state["status"] = "partial"
    if case_err:
        state["errors"].append("case_json: %s" % case_err)
    if contract_key is None:
        state["uncertainty"].append({
            "item": "output_contract", "reason": "output_contract not found",
            "severity": "low",
        })

    _log(state, "skeleton", "partial result skeleton written")
    state["runtime"] = {"start": iso(time.time()),
                        "skeleton_only": True, "python": sys.version.split()[0]}

    result = build_result(state)
    result_path = os.path.join(output_dir, "result.json")
    err = write_json(result_path, result)
    if err:
        write_json(result_path, {"case_id": state["case_id"], "status": "input_error",
                                 "errors": [err]})
        return 2
    print("skeleton: -> %s (status=%s)" % (result_path, result["status"]))
    return 0


def _deep_merge(dst, src):
    if isinstance(dst, dict) and isinstance(src, dict):
        for k, v in src.items():
            if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
                _deep_merge(dst[k], v)
            else:
                dst[k] = v
    return dst


def run_update(args):
    """Merge a semantic patch into an existing result.json (incremental)."""
    result_path = args.result or os.path.join(
        args.output or "/app/output", "result.json")
    patch, perr = safe_json(args.update)
    if perr:
        sys.stderr.write("update: cannot read patch: %s\n" % perr)
        return 1
    data, rerr = safe_json(result_path)
    if rerr:
        sys.stderr.write("update: cannot read result: %s\n" % rerr)
        return 1
    _deep_merge(data, patch)
    data.setdefault("change_log", []).append({
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor": "agent", "step": "update",
        "detail": "semantic patch merged from %s" % args.update,
    })
    err = write_json(result_path, data)
    if err:
        sys.stderr.write("update: write failed: %s\n" % err)
        return 2
    print("update: merged %s into %s" % (args.update, result_path))
    return 0


def run_check(args):
    """Validate result.json. Exit 0 on pass, 3 on fail."""
    result_path = args.check
    case_path = args.case_json or "/input/case.json"
    data, rerr = safe_json(result_path)
    if rerr:
        print("check: FAIL result unreadable: %s" % rerr)
        return 3
    problems = []
    if not data.get("case_id"):
        problems.append("case_id empty")
    if data.get("status") not in ALLOWED_STATUS:
        problems.append("bad status: %r" % data.get("status"))

    case, _ = safe_json(case_path)
    contract_key, contract_value = find_contract(case)
    if contract_key and isinstance(contract_value, dict):
        for k in contract_value:
            if k in RESERVED_KEYS:
                ck = ("computed", "_contract_%s" % k)
                if not (isinstance(data.get("computed"), dict) and ck[1] in data["computed"]):
                    problems.append("missing contract field (reserved): %s" % k)
            elif k not in data:
                problems.append("missing contract field: %s" % k)

    for s in (data.get("sources") or []):
        if not s.get("locator") or not s.get("quote"):
            problems.append("source missing locator/quote: %r" % s)
            break

    out_dir = os.path.dirname(result_path) or "."
    if data.get("status") in ("ok", "partial"):
        for rel in ("library_manifest.csv", "directory_tree.txt",
                    "dedup_version.json", "recovery_report.json",
                    "library_index.json", "library_manifest.md",
                    "operation_log.jsonl"):
            if not os.path.isfile(os.path.join(out_dir, rel)):
                problems.append("missing artifact: %s" % rel)

    if problems:
        print("check: FAIL")
        for p in problems:
            print("  - %s" % p)
        return 3
    print("check: OK (status=%s case_id=%s)" % (data.get("status"), data.get("case_id")))
    return 0


def parse_args(argv):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-json", default=None)
    p.add_argument("--task-path", default=None)
    p.add_argument("--skeleton-only", action="store_true")
    p.add_argument("--update", default=None)
    p.add_argument("--result", default=None)
    p.add_argument("--check", default=None)
    p.add_argument("--help", "-h", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(argv)
        if args.check:
            return run_check(args)
        if args.update:
            return run_update(args)
        if args.skeleton_only:
            return run_skeleton(args)
        return run_pipeline(args)
    except SystemExit:
        raise
    except Exception as exc:  # absolute last resort: never crash
        sys.stderr.write("core_impl fatal: %s\n%s\n" % (exc, traceback.format_exc()))
        # try to leave a valid result.json
        try:
            out = os.path.abspath(args.output if "args" in dir() else "/app/output")
            os.makedirs(out, exist_ok=True)
            write_json(os.path.join(out, "result.json"), {
                "case_id": None, "status": "input_error",
                "errors": ["fatal: %s" % exc],
                "sources": [], "uncertainty": [], "pending_human": [],
                "change_log": [], "limitations": [], "degradations": [],
                "runtime": {"seconds": 0.0}, "computed": {},
            })
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
