# -*- coding: utf-8 -*-
"""core.py — evidence-ledger pipeline for document-library retrieval / repair.

Differential architecture (vs. a single "compute then patch" script):

  1. A typed, append-only *evidence ledger* is the single source of truth.
     Every stage appends typed events (kind / item / detail / severity);
     all editable artifacts and ``result.json`` are *derived* from the ledger,
     never written ad hoc. A reviewer can replay the ledger to rebuild every
     downstream product.
  2. Schema validation runs at *every stage boundary* (not only at the end),
     so any partially built state is internally consistent.
  3. Record IDs are deterministic (path-based). Directory traversal is sorted,
     JSON is emitted with ``sort_keys=True`` and no randomness is used, so a
     second run reproduces the same JSON up to wall-clock timestamps.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import sqlite3
import stat as stat_mod
import sys
import time
import unicodedata
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_HASH_BYTES = 2 * 1024 * 1024          # skip content hashing above this size
MAX_TEXT_BYTES = 8 * 1024 * 1024          # skip full-text extraction above this
MAX_INDEX_TERMS = 20000                   # cap the term -> files index
MAX_SIMILARITY_PAIRS = 20000              # cap pairwise content comparisons

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

RESERVED = {
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime", "limitations",
    "computed",
}

CONTRACT_KEYS = ("output_contract", "output", "result_schema", "contract")

CASE_ID_KEYS = ("case_id", "caseId", "id", "case", "challenge_id")

# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def path_id(relpath: str) -> str:
    """Deterministic short id for a record, derived from its relative path."""
    return hashlib.sha1(relpath.encode("utf-8", "replace")).hexdigest()[:12]


def to_rel(path: str, root: str) -> str:
    try:
        r = os.path.relpath(path, root)
    except ValueError:  # different drives on Windows
        r = os.path.abspath(path)
    return r.replace(os.sep, "/")


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def safe_hash(path: str, limit: int) -> dict:
    """Return {sha256, content_status, note} for a regular file, never raising."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return {"sha256": None, "content_status": "unreadable", "note": str(exc)}
    if size > limit:
        return {"sha256": None, "content_status": "too_large",
                "note": "size %d > %d" % (size, limit)}
    try:
        data = read_bytes(path)
    except OSError as exc:
        return {"sha256": None, "content_status": "unreadable", "note": str(exc)}
    digest = hashlib.sha256(data).hexdigest()
    binary = b"\x00" in data[:8192]
    return {"sha256": digest, "content_status": "hashed",
            "binary": binary, "note": ""}


def read_text(path: str) -> tuple:
    """Decode a text file with an encoding fallback chain.

    Returns ``(text, encoding, degraded_flag, error)``; never raises.
    """
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", None, True, str(exc)
    if size > MAX_TEXT_BYTES:
        return "", None, True, "too_large_for_text"
    try:
        raw = read_bytes(path)
    except OSError as exc:
        return "", None, True, str(exc)
    last_err = None
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc, False, None
        except (UnicodeDecodeError, LookupError) as exc:
            last_err = str(exc)
    return raw.decode("utf-8", "replace"), "utf-8", True, last_err


def normalize_stem(name: str) -> str:
    """Canonical stem for version-chain grouping.

    Heuristic: NFKC normalisation, lower-case, strip a version/copy token set,
    collapse non-alphanumeric runs to '_'. Clearly labelled as heuristic in the
    output; the Agent reviews the candidates, it does not decide alone.
    """
    base, _ = os.path.splitext(name)
    base = unicodedata.normalize("NFKC", base).lower()
    base = re.sub(
        r"(copy\s*\(?\d*\)?|copie|duplicate|副本|拷贝|扫描件|扫描版|scan(?:ned)?|"
        r"final|draft|rev(?:ision)?\.?\s*\d+|v\.?\d+(?:\.\d+)*|v\d+|"
        r"version\s*\d+(?:\.\d+)*|修订版?\d*|终稿|草稿|定稿|old|new)",
        " ", base, flags=re.IGNORECASE,
    )
    base = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", base)
    return base.strip("_")


HEADING_RE = re.compile(
    r"^(?:"
    r"#{1,6}\s+.+"
    r"|\d+(?:\.\d+)*[\.、）)]?\s*.+"
    r"|第[一二三四五六七八九十百千万零〇\d]+[章节篇部分]\s*.*"
    r"|(?:abstract|introduction|methods?|materials?\s*(?:and\s*methods)?"
    r"|results?|discussion|conclusion|conclusions|references?|acknowledg)"
    r"\b.*"
    r")",
    re.IGNORECASE,
)


def split_terms(text: str):
    """Lightweight term tokenizer: keep latin words and CJK runs >= 2 chars."""
    toks = re.findall(r"[a-z0-9][a-z0-9._-]*|[\u4e00-\u9fff]{2,}", text.lower())
    seen = set()
    out = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_sections(text: str, max_sections: int = 400):
    """Coarse section splitting: headings detected by a heading regex."""
    lines = text.splitlines()
    sections = []
    cur_title = "(top)"
    cur_lines = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped and HEADING_RE.match(stripped) and len(sections) < max_sections:
            if cur_lines or cur_title != "(top)":
                sections.append({
                    "title": cur_title,
                    "start_line": idx - len(cur_lines),
                    "para_count": max(1, len(cur_lines)),
                    "preview": stripped[:240],
                })
            cur_title = stripped[:160]
            cur_lines = []
        else:
            if stripped:
                cur_lines.append(stripped)
    if cur_title != "(top)" or cur_lines:
        sections.append({
            "title": cur_title,
            "start_line": max(0, len(lines) - len(cur_lines)),
            "para_count": max(1, len(cur_lines)),
            "preview": (cur_lines[0][:240] if cur_lines else ""),
        })
    return sections


# --------------------------------------------------------------------------- #
# evidence ledger
# --------------------------------------------------------------------------- #


class Ledger:
    """Append-only, typed event log; the single source of truth."""

    def __init__(self):
        self.events = []
        self.errors = 0
        self.degradations = 0

    def add(self, stage, kind, item, detail=None, severity="info"):
        entry = {
            "seq": len(self.events) + 1,
            "stage": stage,
            "kind": kind,
            "item": item,
            "detail": detail or {},
            "severity": severity,
        }
        if severity == "error":
            self.errors += 1
        elif severity == "degradation":
            self.degradations += 1
        self.events.append(entry)
        return entry

    def validate(self):
        """Stage-boundary schema check; returns list of violations."""
        problems = []
        seen_seq = set()
        for ev in self.events:
            if not isinstance(ev.get("seq"), int):
                problems.append("event missing int seq")
            if ev["seq"] in seen_seq:
                problems.append("duplicate seq %s" % ev["seq"])
            seen_seq.add(ev["seq"])
            if ev.get("severity") not in ("info", "warning", "error", "degradation"):
                problems.append("bad severity at seq %s" % ev["seq"])
        return problems


# --------------------------------------------------------------------------- #
# pipeline stages
# --------------------------------------------------------------------------- #


class Pipeline:
    def __init__(self, input_root, output_dir, work_dir, case_json=None,
                 task_path=None, max_hash_bytes=MAX_HASH_BYTES,
                 max_text_bytes=MAX_TEXT_BYTES, skeleton_only=False):
        self.input_root = os.path.abspath(input_root)
        self.output_dir = os.path.abspath(output_dir)
        self.work_dir = os.path.abspath(work_dir)
        self.case_json_path = case_json
        self.task_path = task_path
        self.max_hash_bytes = max_hash_bytes
        self.max_text_bytes = max_text_bytes
        self.skeleton_only = skeleton_only
        self.ledger = Ledger()
        self.started = time.time()

        self.case_data = {}
        self.contract = None
        self.contract_keys = []
        self.case_id = ""
        self.files = []          # file records (dirs separate)
        self.dirs = []
        self.errors = []
        self.degradations = []
        # derived products are initialised empty so every code path
        # (including error/skeleton paths) has well-defined attributes
        self.dup_groups = []
        self.aliases = []
        self.version_candidates = []
        self.catalogs = []
        self.reconciliation = {}
        self.index = {}
        self.answers = []

    # -- helpers ---------------------------------------------------------- #

    def _ensure_dirs(self):
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)

    def _load_case(self):
        path = self.case_json_path or os.path.join(self.input_root, "case.json")
        if not os.path.isfile(path):
            # try alternates
            for cand in ("case.json", "input.json", "task_input.json"):
                p = os.path.join(self.input_root, cand)
                if os.path.isfile(p):
                    path = p
                    break
            else:
                self.ledger.add("meta", "case_missing", path,
                                {"note": "no case.json found"}, "error")
                self.errors.append("case.json missing -> status=input_error")
                return
        self.case_json_path = path
        try:
            raw = read_bytes(path)
            text = raw.decode("utf-8-sig")
            self.case_data = json.loads(text)
            self.ledger.add("meta", "case_loaded", to_rel(path, self.input_root))
        except Exception as exc:  # noqa: BLE001
            self.ledger.add("meta", "case_bad_json", to_rel(path, self.input_root),
                            {"error": str(exc)}, "error")
            self.errors.append("case.json unparseable: %s" % exc)
            self.case_data = {}

        self._discover_contract()
        self._discover_case_id()

    def _discover_contract(self):
        cd = self.case_data if isinstance(self.case_data, dict) else {}
        for key in CONTRACT_KEYS:
            if key in cd and cd[key] is not None:
                self.contract = cd[key]
                break
        if self.contract is None and isinstance(self.case_data, dict):
            # some cases put the schema directly under "schema" or "expected"
            for key in ("schema", "expected", "fields"):
                if key in cd and isinstance(cd[key], (dict, list)):
                    self.contract = cd[key]
                    break
        self.contract_keys = self._contract_field_names(self.contract)

    @staticmethod
    def _contract_field_names(contract):
        if isinstance(contract, dict):
            return list(contract.keys())
        if isinstance(contract, list):
            names = []
            for item in contract:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict):
                    names.append(item.get("name") or item.get("key")
                                  or item.get("field") or item.get("path")
                                  or ("field_%d" % len(names)))
            return names
        return []

    def _discover_case_id(self):
        cd = self.case_data if isinstance(self.case_data, dict) else {}
        for key in CASE_ID_KEYS:
            if key in cd and cd[key] not in (None, ""):
                self.case_id = str(cd[key])
                return
        # fallback: task.md challenge_id
        if self.task_path and os.path.isfile(self.task_path):
            try:
                text = read_text(self.task_path)[0]
                m = re.search(r"challenge_id[:\s`]+([A-Za-z0-9._-]+)", text)
                if m:
                    self.case_id = m.group(1)
                    return
            except Exception:  # noqa: BLE001
                pass
        # fallback: input dir basename
        self.case_id = os.path.basename(self.input_root.rstrip("/\\")) or "unknown"

    # -- stage 0: discover (fast; used by --skeleton-only) ----------------- #

    def stage_discover(self):
        root = self.input_root
        if not os.path.isdir(root):
            self.ledger.add("inventory", "input_missing", root, {}, "error")
            self.errors.append("input root missing")
            return
        for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                    followlinks=False):
            dirnames.sort()
            filenames.sort()
            for d in dirnames:
                full = os.path.join(dirpath, d)
                rel = to_rel(full, root)
                try:
                    st = os.lstat(full)
                except OSError as exc:
                    self.ledger.add("inventory", "dir_unreadable", rel,
                                    {"error": str(exc)}, "degradation")
                    continue
                self.dirs.append({
                    "relpath": rel, "kind": "dir",
                    "mode": stat_mod.filemode(st.st_mode),
                    "empty": True,  # filled later
                })
            for f in filenames:
                full = os.path.join(dirpath, f)
                rel = to_rel(full, root)
                rec = self._stat_file(full, rel)
                if rec:
                    self.files.append(rec)
                    self.ledger.add("inventory", rec["kind"], rel,
                                    {"size": rec.get("size")}, "info")
        # mark empty dirs
        non_empty = {os.path.dirname(f["relpath"]) for f in self.files}
        non_empty |= {d for d in non_empty}
        for d in self.dirs:
            d["empty"] = d["relpath"] not in non_empty and \
                d["relpath"] != "."
        # dirs directly contained files/dirs may not be in non_empty set;
        # a dir is non-empty if it appears as a parent of any file or subdir
        parents = set()
        for f in self.files:
            p = f["relpath"]
            while "/" in p:
                p = p.rsplit("/", 1)[0]
                parents.add(p)
        for d in self.dirs:
            d["empty"] = d["relpath"] not in parents
        self._mark_roles()
        self.ledger.add("inventory", "summary", ".",
                        {"files": len(self.files), "dirs": len(self.dirs)})

    def _mark_roles(self):
        """Mark control (task input) files vs. research material files."""
        control = {"case.json", "task.md", "input.json", "task_input.json",
                   "questions.json", "queries.json", "questions.md",
                   "queries.md", "questions.txt"}
        for f in self.files:
            base = os.path.basename(f["relpath"]).lower()
            f["role"] = "control" if base in control else "material"

    @property
    def material_files(self):
        return [f for f in self.files if f.get("role") != "control"]

    def _stat_file(self, full, rel):
        try:
            st = os.lstat(full)
        except OSError as exc:
            self.ledger.add("inventory", "stat_error", rel,
                            {"error": str(exc)}, "degradation")
            self.degradations.append("stat failed: %s" % rel)
            return None
        mode = st.st_mode
        rec = {
            "id": path_id(rel),
            "relpath": rel,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "mode": stat_mod.filemode(mode),
            "perm_octal": oct(stat_mod.S_IMODE(mode)),
            "ext": os.path.splitext(rel)[1].lower(),
            "kind": "file",
            "link_target": None,
            "inode": None,
            "nlink": None,
        }
        if stat_mod.S_ISLNK(mode):
            rec["kind"] = "symlink"
            try:
                rec["link_target"] = os.readlink(full)
                if not os.path.exists(full):
                    rec["kind"] = "broken_symlink"
            except OSError as exc:
                rec["kind"] = "broken_symlink"
                rec["link_target"] = None
                rec["note"] = "readlink failed: %s" % exc
            self.ledger.add("alias", "symlink", rel,
                            {"target": rec["link_target"],
                             "broken": rec["kind"] == "broken_symlink"})
            return rec
        if stat_mod.S_ISDIR(mode):
            return None  # handled separately
        if not stat_mod.S_ISREG(mode):
            rec["kind"] = "non_regular"
            self.ledger.add("inventory", "non_regular", rel)
            return rec
        try:
            rec["inode"] = st.st_ino
            rec["nlink"] = st.st_nlink
        except (AttributeError, OSError):
            pass
        return rec

    # -- stage 1: hash + dedup ------------------------------------------- #

    def stage_hash_dedup(self):
        by_hash = defaultdict(list)
        for rec in self.files:
            if rec["kind"] != "file":
                continue
            info = safe_hash(os.path.join(self.input_root, rec["relpath"]),
                             self.max_hash_bytes)
            rec["sha256"] = info["sha256"]
            rec["content_status"] = info["content_status"]
            rec["binary"] = info.get("binary")
            if info.get("note"):
                rec["hash_note"] = info["note"]
            if info["sha256"]:
                by_hash[info["sha256"]].append(rec["relpath"])
                self.ledger.add("content", "hashed", rec["relpath"],
                                {"sha256": info["sha256"]})
            else:
                self.ledger.add("content", info["content_status"],
                                rec["relpath"], {"note": info.get("note", "")},
                                "degradation")
        dup_groups = []
        for digest, members in by_hash.items():
            if len(members) >= 2:
                members = sorted(members)
                dup_groups.append({
                    "hash": digest, "count": len(members),
                    "members": members, "canonical": members[0],
                })
                self.ledger.add("dedup", "duplicate_group", members[0],
                                {"hash": digest, "members": members})
        dup_groups.sort(key=lambda g: (-g["count"], g["hash"]))
        self.dup_groups = dup_groups

    # -- stage 2: aliases ------------------------------------------------ #

    def stage_aliases(self):
        sym_links = defaultdict(list)
        hard_links = defaultdict(list)
        for rec in self.files:
            if rec["kind"] in ("symlink", "broken_symlink"):
                key = rec.get("link_target") or rec["relpath"]
                sym_links[key].append(rec["relpath"])
            if rec["kind"] == "file" and rec.get("nlink", 1) > 1 and rec.get("inode"):
                hard_links[(rec["inode"],)].append(rec["relpath"])
        aliases = []
        for target, members in sorted(sym_links.items()):
            aliases.append({"type": "symlink", "target": target,
                            "members": sorted(members)})
        for inode, members in sorted(hard_links.items()):
            if len(members) >= 2:
                aliases.append({"type": "hardlink", "inode": inode[0],
                                "members": sorted(members)})
        self.aliases = aliases
        self.ledger.add("alias", "alias_summary", ".",
                        {"symlink_groups": sum(1 for a in aliases
                                              if a["type"] == "symlink"),
                         "hardlink_groups": sum(1 for a in aliases
                                                if a["type"] == "hardlink")})

    # -- stage 3: version candidates ------------------------------------- #

    def stage_versions(self):
        by_stem = defaultdict(list)
        for rec in self.files:
            if rec["kind"] != "file":
                continue
            stem = normalize_stem(rec["relpath"])
            by_stem[stem].append(rec)
        candidates = []
        for stem, recs in sorted(by_stem.items()):
            if len(recs) < 2:
                continue
            recs = sorted(recs, key=lambda r: r["relpath"])
            members = [r["relpath"] for r in recs]
            sims = self._content_similarities(recs)
            candidates.append({
                "stem": stem,
                "members": members,
                "similarity": sims,
                "heuristic": True,
                "note": "normalized stem + content similarity; Agent reviews",
            })
            self.ledger.add("version", "candidate", stem,
                            {"members": members, "similarity": sims})
        self.version_candidates = candidates

    def _content_similarities(self, recs):
        """Pairwise difflib ratio on small text files; size fallback."""
        texts = {}
        for r in recs:
            path = os.path.join(self.input_root, r["relpath"])
            txt = ""
            if r.get("content_status") == "hashed" and not r.get("binary"):
                try:
                    if os.path.getsize(path) <= self.max_text_bytes:
                        txt = read_text(path)[0]
                except OSError:
                    txt = ""
            texts[r["relpath"]] = txt
        out = []
        n = len(recs)
        if n * (n - 1) // 2 > MAX_SIMILARITY_PAIRS:
            return out
        for i in range(n):
            for j in range(i + 1, n):
                a, b = recs[i]["relpath"], recs[j]["relpath"]
                if texts[a] and texts[b]:
                    ratio = round(difflib.SequenceMatcher(None, texts[a],
                                                          texts[b]).ratio(), 4)
                    out.append({"a": a, "b": b, "ratio": ratio})
                else:
                    sa, sb = recs[i]["size"], recs[j]["size"]
                    ratio = None
                    if sa and sb:
                        ratio = round(min(sa, sb) / max(sa, sb), 4)
                    out.append({"a": a, "b": b, "ratio": ratio,
                                "basis": "size"})
        return out

    # -- stage 4: catalog parsing ---------------------------------------- #

    def stage_catalog(self):
        catalogs = []
        for rec in self.files:
            ext = rec["ext"]
            name = os.path.basename(rec["relpath"]).lower()
            parser = None
            if ext == ".json":
                parser = self._parse_json_catalog
            elif ext == ".csv":
                parser = self._parse_csv_catalog
            elif ext == ".md":
                parser = self._parse_md_catalog
            elif ext == ".bib":
                parser = self._parse_bib_catalog
            elif ext in (".ris", ".enw"):
                parser = self._parse_ris_catalog
            elif ext in (".db", ".sqlite", ".sqlite3"):
                parser = self._parse_sqlite_catalog
            if parser is None:
                continue
            if any(k in name for k in ("catalog", "library", "manifest",
                                       "index", "inventory", "bibliography",
                                       "references", "目录", "清单", "库")):
                try:
                    result = parser(rec)
                    if result:
                        catalogs.append(result)
                        self.ledger.add("catalog", "parsed", rec["relpath"],
                                        {"entries": result["entry_count"],
                                         "format": result["format"]})
                except Exception as exc:  # noqa: BLE001
                    self.ledger.add("catalog", "parse_error", rec["relpath"],
                                    {"error": str(exc)}, "degradation")
                    self.degradations.append("catalog parse failed: %s" % rec["relpath"])
        self.catalogs = catalogs

    def _entry(self, source, row, fields):
        return {"source": source, "row": row, "fields": fields}

    def _parse_json_catalog(self, rec):
        path = os.path.join(self.input_root, rec["relpath"])
        text, _, bad, err = read_text(path)
        if bad and err:
            raise ValueError("encoding: %s" % err)
        data = json.loads(text or "null")
        entries = []
        if isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    entries.append(self._entry(rec["relpath"], i, item))
        elif isinstance(data, dict):
            # a dict of records, or a single record
            for i, (k, v) in enumerate(data.items()):
                if isinstance(v, dict):
                    e = dict(v)
                    e.setdefault("id", k)
                    entries.append(self._entry(rec["relpath"], i, e))
            if not entries:
                entries.append(self._entry(rec["relpath"], 0, data))
        return {"source": rec["relpath"], "format": "json",
                "entry_count": len(entries), "entries": entries}

    def _parse_csv_catalog(self, rec):
        path = os.path.join(self.input_root, rec["relpath"])
        text, _, bad, err = read_text(path)
        if bad and err:
            raise ValueError("encoding: %s" % err)
        reader = csv.DictReader(io.StringIO(text or ""))
        entries = []
        for i, row in enumerate(reader):
            entries.append(self._entry(rec["relpath"], i, dict(row)))
        return {"source": rec["relpath"], "format": "csv",
                "entry_count": len(entries), "entries": entries}

    def _parse_md_catalog(self, rec):
        path = os.path.join(self.input_root, rec["relpath"])
        text, _, bad, err = read_text(path)
        if bad and err:
            raise ValueError("encoding: %s" % err)
        lines = (text or "").splitlines()
        rows = []
        header = None
        for line in lines:
            if "|" in line:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if header is None and cells and all(c for c in cells):
                    header = cells
                elif header and len(cells) == len(header):
                    rows.append(cells)
        entries = []
        for i, row in enumerate(rows):
            if all(not c or c in ("---", "---:") for c in row):
                continue
            entries.append(self._entry(rec["relpath"], i,
                                       dict(zip(header, row))))
        return {"source": rec["relpath"], "format": "markdown",
                "entry_count": len(entries), "entries": entries}

    def _parse_bib_catalog(self, rec):
        path = os.path.join(self.input_root, rec["relpath"])
        text, _, bad, err = read_text(path)
        if bad and err:
            raise ValueError("encoding: %s" % err)
        entries = []
        for m in re.finditer(r"@(\w+)\s*\{\s*([^,]+),\s*(.*?)\n\}",
                             text or "", re.S):
            etype, key, body = m.group(1), m.group(2).strip(), m.group(3)
            fields = {"_type": etype, "_key": key}
            for fm in re.finditer(r"(\w+)\s*=\s*[{\"](.*?)[}\"]", body, re.S):
                fields[fm.group(1)] = fm.group(2).strip()
            entries.append(self._entry(rec["relpath"], len(entries), fields))
        return {"source": rec["relpath"], "format": "bibtex",
                "entry_count": len(entries), "entries": entries}

    def _parse_ris_catalog(self, rec):
        path = os.path.join(self.input_root, rec["relpath"])
        text, _, bad, err = read_text(path)
        if bad and err:
            raise ValueError("encoding: %s" % err)
        entries = []
        cur = {}
        for line in (text or "").splitlines():
            if line.startswith("ER  -"):
                if cur:
                    entries.append(self._entry(rec["relpath"],
                                               len(entries), dict(cur)))
                cur = {}
                continue
            m = re.match(r"^([A-Z0-9]{2})\s+-\s?(.*)$", line)
            if m:
                cur[m.group(1)] = m.group(2).strip()
        if cur:
            entries.append(self._entry(rec["relpath"], len(entries), dict(cur)))
        return {"source": rec["relpath"], "format": "ris",
                "entry_count": len(entries), "entries": entries}

    def _parse_sqlite_catalog(self, rec):
        path = os.path.join(self.input_root, rec["relpath"])
        try:
            con = sqlite3.connect(path)
        except sqlite3.Error as exc:
            raise ValueError("sqlite_open_error: %s" % exc)
        entries = []
        try:
            cur = con.cursor()
            tables = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for table in tables:
                try:
                    rows = cur.execute("SELECT * FROM %s" % table).fetchall()
                    cols = [d[0] for d in cur.description]
                except sqlite3.Error as exc:
                    self.ledger.add("catalog", "sqlite_query_error", rec["relpath"],
                                    {"table": table, "error": str(exc)},
                                    "degradation")
                    continue
                for i, row in enumerate(rows):
                    fields = dict(zip(cols, row))
                    fields["_table"] = table
                    entries.append(self._entry(rec["relpath"],
                                               len(entries), fields))
        finally:
            con.close()
        return {"source": rec["relpath"], "format": "sqlite",
                "entry_count": len(entries), "entries": entries}

    # -- stage 5: reconciliation ----------------------------------------- #

    def stage_reconcile(self):
        disk_basenames = {os.path.basename(f["relpath"]).lower(): f["relpath"]
                          for f in self.files if f["kind"] == "file"}
        disk_hashes = {f["sha256"]: f["relpath"] for f in self.files
                       if f.get("sha256")}
        matches, mismatches, missing, orphaned = [], [], [], []
        seen_disk = set()
        for cat in self.catalogs:
            for e in cat["entries"]:
                fields = e["fields"]
                cand = None
                for key in ("path", "file", "filename", "file_path", "relpath"):
                    v = fields.get(key)
                    if not v:
                        continue
                    v = str(v).replace(os.sep, "/")
                    v = os.path.basename(v).lower()
                    if v in disk_basenames:
                        cand = disk_basenames[v]
                        break
                if cand is None:
                    for key in ("sha256", "hash", "md5", "checksum"):
                        v = fields.get(key)
                        if v and str(v) in disk_hashes:
                            cand = disk_hashes[str(v)]
                            break
                if cand is None:
                    # try loose basename on any field value
                    for key, val in fields.items():
                        vs = str(val).replace(os.sep, "/")
                        base = os.path.basename(vs).lower()
                        if base in disk_basenames:
                            cand = disk_basenames[base]
                            break
                if cand:
                    matches.append({"entry": e, "file": cand})
                    seen_disk.add(cand)
                else:
                    missing.append({"entry": e})
        for f in self.files:
            if f["kind"] == "file" and f["relpath"] not in seen_disk:
                orphaned.append({"file": f["relpath"]})
        # mismatches: entries whose referenced path exists but points to a
        # different file than the hash implies — conservatively, entries with a
        # "path" field that does not exist on disk.
        for cat in self.catalogs:
            for e in cat["entries"]:
                for key in ("path", "file_path", "relpath", "file"):
                    v = e["fields"].get(key)
                    if v and isinstance(v, str):
                        rel = v.replace(os.sep, "/")
                        if not any(f["relpath"] == rel for f in self.files):
                            mismatches.append({"entry": e, "claimed_path": rel,
                                               "note": "referenced path absent"})
        self.reconciliation = {
            "matches": matches, "mismatches": mismatches,
            "missing": missing, "orphaned": orphaned,
        }
        self.ledger.add("reconcile", "summary", ".",
                        {"matches": len(matches), "mismatches": len(mismatches),
                         "missing": len(missing), "orphaned": len(orphaned)})

    # -- stage 6: index --------------------------------------------------- #

    def stage_index(self):
        term_map = defaultdict(list)
        file_index = []
        section_index = []
        total_terms = 0
        for rec in self.files:
            if rec["kind"] != "file":
                continue
            path = os.path.join(self.input_root, rec["relpath"])
            text, enc, bad, err = read_text(path)
            entry = {
                "relpath": rec["relpath"], "sha256": rec.get("sha256"),
                "ext": rec["ext"], "kind": rec["kind"],
                "text_status": "ok" if not bad else "degraded",
                "encoding": enc, "note": err,
                "sections": [], "term_count": 0,
            }
            if text:
                terms = split_terms(text)
                entry["term_count"] = len(terms)
                for t in terms:
                    if total_terms < MAX_INDEX_TERMS:
                        term_map[t].append(rec["relpath"])
                        total_terms += 1
                entry["sections"] = extract_sections(text)
                for sec in entry["sections"]:
                    sec["file"] = rec["relpath"]
                    section_index.append(sec)
            file_index.append(entry)
            if not text and rec.get("content_status") == "hashed" and \
                    rec.get("binary"):
                entry["text_status"] = "binary_no_text_layer"
                self.ledger.add("index", "binary", rec["relpath"],
                                {"note": "no text layer; use pdf/ocr tool"},
                                "degradation")
        # dedupe term map lists and cap
        for t in list(term_map):
            term_map[t] = sorted(set(term_map[t]))
        self.index = {"term_map": dict(term_map), "files": file_index,
                      "sections": section_index}

    # -- stage 7: mechanical Q&A ----------------------------------------- #

    def stage_qa(self):
        questions = self._discover_questions()
        answers = []
        for q in questions:
            answers.append(self._answer(q))
        self.answers = answers
        self.ledger.add("qa", "summary", ".", {"questions": len(questions)})

    def _discover_questions(self):
        qs = []
        cd = self.case_data if isinstance(self.case_data, dict) else {}
        for key in ("questions", "queries", "searches", "qa", "task_questions"):
            val = cd.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        qs.append(item)
                    elif isinstance(item, dict):
                        qs.append(item.get("question") or item.get("query")
                                  or item.get("text") or json.dumps(item,
                                                                   ensure_ascii=False))
            elif isinstance(val, str):
                qs.append(val)
        # questions files in input
        for name in ("questions.json", "queries.json", "questions.md",
                     "queries.md", "questions.txt"):
            p = os.path.join(self.input_root, name)
            if os.path.isfile(p):
                text, _, _, _ = read_text(p)
                if text:
                    if name.endswith(".json"):
                        try:
                            data = json.loads(text)
                            if isinstance(data, list):
                                for item in data:
                                    qs.append(item if isinstance(item, str)
                                              else json.dumps(item,
                                                             ensure_ascii=False))
                        except json.JSONDecodeError:
                            qs.append(text)
                    else:
                        for line in text.splitlines():
                            line = line.strip()
                            if line and not line.startswith("#"):
                                qs.append(line)
        # dedupe preserving order
        seen = set()
        out = []
        for q in qs:
            if q and q not in seen:
                seen.add(q)
                out.append(q)
        return out

    def _answer(self, q):
        ql = q.lower()
        base = {"question": q, "answer_type": "mechanical", "answer": None,
                "evidence": []}
        # counts
        m = re.search(r"(多少|how many|count|number of)\s*(files?|文件)", ql)
        if m or ql.strip() in ("文件数", "file count", "how many files"):
            base["answer_type"] = "count"
            base["answer"] = {"files": len(self.files),
                              "dirs": len(self.dirs),
                              "duplicate_groups": len(getattr(self, "dup_groups", [])),
                              "broken_symlinks": sum(1 for f in self.files
                                                     if f["kind"] == "broken_symlink")}
            return base
        if any(k in ql for k in ("重复", "duplicate", "dedup")):
            base["answer_type"] = "duplicates"
            base["answer"] = getattr(self, "dup_groups", [])
            return base
        if any(k in ql for k in ("断链", "broken", "symlink", "符号链接",
                                 "硬链接", "hardlink", "alias", "别名")):
            base["answer_type"] = "aliases"
            base["answer"] = getattr(self, "aliases", [])
            return base
        if any(k in ql for k in ("缺失", "missing", "orphan", "未登记",
                                 "对账", "reconcil")):
            base["answer_type"] = "reconciliation"
            base["answer"] = getattr(self, "reconciliation", {})
            return base
        if any(k in ql for k in ("版本", "version", "修订")):
            base["answer_type"] = "versions"
            base["answer"] = getattr(self, "version_candidates", [])
            return base
        # hash lookup
        m = re.search(r"hash\s+(?:of\s+)?[\"']?([^\"'?\s]+)", ql)
        if m:
            target = m.group(1)
            for f in self.files:
                if f["relpath"].endswith(target) or target in f["relpath"]:
                    base["answer_type"] = "hash"
                    base["answer"] = {"relpath": f["relpath"],
                                      "sha256": f.get("sha256"),
                                      "content_status": f.get("content_status")}
                    base["evidence"].append({"locator": f["relpath"],
                                             "quote": f.get("sha256") or ""})
                    return base
        # term search
        terms = split_terms(q)
        if terms:
            hits = self._search(terms)
            base["answer_type"] = "search"
            base["answer"] = hits[:20]
            base["evidence"] = [{"locator": h["file"],
                                 "quote": h["quote"]} for h in hits[:10]]
            if not hits:
                base["answer_type"] = "empty_result"
                base["answer"] = {"note": "no match; conditions kept as hard "
                                   "constraint, not relaxed"}
            return base
        base["answer_type"] = "unresolved"
        base["answer"] = {"note": "mechanical stage could not classify; "
                                  "Agent reviews with ledger evidence"}
        return base

    def _search(self, terms):
        term_map = getattr(self, "index", {}).get("term_map", {})
        scores = defaultdict(int)
        for t in terms:
            for rel in term_map.get(t, []):
                scores[rel] += 1
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        hits = []
        for rel, score in ranked:
            # find a paragraph containing the longest term
            path = os.path.join(self.input_root, rel)
            text, _, _, _ = read_text(path)
            quote = ""
            if text:
                for para in text.split("\n\n"):
                    if any(t in para.lower() for t in terms):
                        quote = " ".join(para.split())[:300]
                        break
                if not quote:
                    quote = " ".join(text.split())[:300]
            hits.append({"file": rel, "score": score, "quote": quote})
        return hits

    # ------------------------------------------------------------------- #

    def run(self):
        self._ensure_dirs()
        self._load_case()
        self.stage_discover()
        if self.skeleton_only:
            self.write_result(status="partial")
            return
        errs = " ".join(self.errors)
        if "input root missing" in errs or "case.json missing" in errs:
            self.write_result(status="input_error")
            return
        if not self.files:
            self.write_result(status="insufficient_evidence")
            return
        if not self.material_files:
            self.write_result(status="insufficient_evidence")
            return
        self.stage_hash_dedup()
        self._checkpoint()
        self.stage_aliases()
        self._checkpoint()
        self.stage_versions()
        self._checkpoint()
        self.stage_catalog()
        self._checkpoint()
        self.stage_reconcile()
        self._checkpoint()
        self.stage_index()
        self._checkpoint()
        self.stage_qa()
        self._checkpoint()
        self.write_artifacts()
        status = "ok" if not self.errors else "partial"
        self.write_result(status=status)

    def _checkpoint(self):
        problems = self.ledger.validate()
        if problems:
            for p in problems:
                self.ledger.add("meta", "validation", ".", {"problem": p},
                                "error")
            self.errors.extend(problems)

    # -- result assembly -------------------------------------------------- #

    def write_result(self, status="partial"):
        self._ensure_dirs()
        payload = self._base_payload(status)
        path = os.path.join(self.output_dir, "result.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        # also persist the ledger as an auditable artifact in work dir
        lpath = os.path.join(self.work_dir, "ledger.json")
        with open(lpath, "w", encoding="utf-8") as fh:
            json.dump({"events": self.ledger.events}, fh, ensure_ascii=False,
                      indent=2, sort_keys=True)
        return path

    def _base_payload(self, status):
        runtime = {
            "started_at": now_iso(),
            "duration_seconds": round(time.time() - self.started, 3),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "skeleton_only": bool(self.skeleton_only),
        }
        payload = OrderedDict()
        payload["case_id"] = self.case_id
        payload["status"] = status
        # echo contract fields verbatim (placeholder null / [] by declared type)
        for key in self.contract_keys:
            dest = key if key not in RESERVED else "_contract_" + key
            placeholder = None
            if isinstance(self.contract, dict):
                declared = self.contract.get(key)
                if isinstance(declared, list):
                    placeholder = []
                elif isinstance(declared, dict):
                    placeholder = None
            payload[dest] = placeholder
        payload["sources"] = self._auto_sources()
        payload["uncertainty"] = []
        if not self.contract and self.contract_keys == []:
            payload["uncertainty"].append({
                "item": "output_contract", "reason": "no output_contract found "
                "in case.json/task.md", "severity": "medium",
            })
        payload["pending_human"] = []
        payload["change_log"] = [{
            "seq": 1, "time": now_iso(), "actor": "scripts/core.py",
            "step": "pipeline", "detail": "status=%s" % status,
        }]
        payload["errors"] = self.errors
        payload["degradations"] = self.degradations
        payload["runtime"] = runtime
        payload["limitations"] = []
        payload["computed"] = self._computed_payload()
        return payload

    def _auto_sources(self):
        src = []
        if self.files:
            src.append({
                "item": "file_inventory",
                "locator": "library_manifest.csv",
                "quote": "%d files inventoried (SHA-256 content hashing; "
                         "too_large/unreadable/binary recorded)" % len(self.files),
            })
        if self.dup_groups:
            src.append({
                "item": "duplicate_groups",
                "locator": "dedup_version.json",
                "quote": "%d content-hash duplicate groups" % len(self.dup_groups),
            })
        return src

    def _computed_payload(self):
        comp = OrderedDict()
        comp["files"] = [self._file_public(f) for f in self.files]
        comp["directories"] = [d for d in self.dirs]
        comp["summary"] = {
            "files": len(self.files),
            "material_files": len(self.material_files),
            "dirs": len(self.dirs),
            "duplicate_groups": len(getattr(self, "dup_groups", [])),
            "version_candidates": len(getattr(self, "version_candidates", [])),
            "alias_groups": len(getattr(self, "aliases", [])),
            "catalog_entries": sum(c["entry_count"]
                                   for c in getattr(self, "catalogs", [])),
            "broken_symlinks": sum(1 for f in self.files
                                   if f["kind"] == "broken_symlink"),
            "too_large": sum(1 for f in self.files
                             if f.get("content_status") == "too_large"),
            "unreadable": sum(1 for f in self.files
                              if f.get("content_status") == "unreadable"),
        }
        comp["duplicate_groups"] = getattr(self, "dup_groups", [])
        comp["version_candidates"] = getattr(self, "version_candidates", [])
        comp["aliases"] = getattr(self, "aliases", [])
        comp["catalogs"] = [{"source": c["source"], "format": c["format"],
                             "entry_count": c["entry_count"],
                             "entries": c["entries"]}
                            for c in getattr(self, "catalogs", [])]
        comp["reconciliation"] = getattr(self, "reconciliation", {})
        comp["answers"] = getattr(self, "answers", [])
        comp["index"] = {"term_count": len(getattr(self, "index", {})
                                             .get("term_map", {})),
                         "file_count": len(getattr(self, "index", {})
                                            .get("files", [])),
                         "sections": getattr(self, "index", {})
                         .get("sections", [])[:1000]}
        comp["artifacts"] = self._artifact_list()
        return comp

    @staticmethod
    def _file_public(f):
        out = {k: f.get(k) for k in
               ("id", "relpath", "size", "mtime", "mode", "perm_octal", "ext",
                "kind", "link_target", "inode", "nlink", "sha256",
                "content_status", "binary", "hash_note", "note", "role")}
        return {k: v for k, v in out.items() if v is not None}

    def _artifact_list(self):
        names = ["result.json", "library_manifest.csv", "directory_tree.txt",
                 "dedup_version.json", "recovery_report.json",
                 "library_index.json", "library_manifest.md",
                 "operation_log.jsonl"]
        return [{"name": n, "path": os.path.join(self.output_dir, n)}
                for n in names]

    # -- artifacts -------------------------------------------------------- #

    def write_artifacts(self):
        self._write_manifest_csv()
        self._write_directory_tree()
        self._write_dedup_version()
        self._write_recovery_report()
        self._write_index_json()
        self._write_manifest_md()
        self._write_operation_log()

    def _write_manifest_csv(self):
        path = os.path.join(self.output_dir, "library_manifest.csv")
        cols = ["relpath", "kind", "role", "size", "mtime", "perm_octal",
                "ext", "sha256", "content_status", "link_target", "inode",
                "nlink"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for f in sorted(self.files, key=lambda r: r["relpath"]):
                w.writerow({k: f.get(k) for k in cols})
            for d in sorted(self.dirs, key=lambda r: r["relpath"]):
                w.writerow({"relpath": d["relpath"] + ("/" if d["relpath"] != "." else ""),
                            "kind": "dir", "size": "",
                            "mtime": "", "perm_octal": d["mode"], "ext": "",
                            "sha256": "", "content_status": "",
                            "link_target": "", "inode": "", "nlink": ""})

    def _write_directory_tree(self):
        path = os.path.join(self.output_dir, "directory_tree.txt")
        lines = []
        for d in sorted(self.dirs, key=lambda r: r["relpath"]):
            marker = " [空目录]" if d["empty"] else ""
            lines.append(d["relpath"] + "/" + marker)
        for f in sorted(self.files, key=lambda r: r["relpath"]):
            suffix = ""
            if f["kind"] == "symlink":
                suffix = " -> %s" % f.get("link_target")
            elif f["kind"] == "broken_symlink":
                suffix = " -> %s [断链]" % f.get("link_target")
            lines.append(f["relpath"] + suffix)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))

    def _write_dedup_version(self):
        path = os.path.join(self.output_dir, "dedup_version.json")
        data = {
            "duplicate_groups": getattr(self, "dup_groups", []),
            "version_candidates": getattr(self, "version_candidates", []),
            "aliases": getattr(self, "aliases", []),
            "version_notes": [],  # filled by Agent via --update
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)

    def _write_recovery_report(self):
        path = os.path.join(self.output_dir, "recovery_report.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(getattr(self, "reconciliation", {}), fh,
                      ensure_ascii=False, indent=2, sort_keys=True)

    def _write_index_json(self):
        path = os.path.join(self.output_dir, "library_index.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(getattr(self, "index", {}), fh, ensure_ascii=False,
                      indent=2, sort_keys=True)

    def _write_manifest_md(self):
        path = os.path.join(self.output_dir, "library_manifest.md")
        lines = ["# 科研资料库交接报告", "",
                 "- case_id: `%s`" % self.case_id,
                 "- 状态: `%s`" % "ok",
                 "- 文件数: %d，目录数: %d" % (len(self.files), len(self.dirs)),
                 "- 重复组: %d，版本候选: %d，别名组: %d" % (
                     len(getattr(self, "dup_groups", [])),
                     len(getattr(self, "version_candidates", [])),
                     len(getattr(self, "aliases", []))),
                 "",
                 "## 主要产物", ""]
        for name in ("library_manifest.csv", "directory_tree.txt",
                     "dedup_version.json", "recovery_report.json",
                     "library_index.json", "operation_log.jsonl"):
            lines.append("- `%s`" % name)
        lines += ["", "## 说明",
                  "本报告由脚本的确定性阶段生成；语义结论（版本链对齐、问题回答）"
                  "由 Agent 在候选上补充，并通过 `--update` 回写。"]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def _write_operation_log(self):
        path = os.path.join(self.output_dir, "operation_log.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for ev in self.ledger.events:
                fh.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# update / check subcommands
# --------------------------------------------------------------------------- #


def deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return patch


def cmd_update(args):
    result_path = args.result
    patch_path = args.update
    with open(result_path, "r", encoding="utf-8-sig") as fh:
        result = json.load(fh)
    with open(patch_path, "r", encoding="utf-8-sig") as fh:
        patch = json.load(fh)
    merged = deep_merge(result, patch)
    cl = merged.setdefault("change_log", [])
    cl.append({"seq": len(cl) + 1, "time": now_iso(),
               "actor": "scripts/main.py --update",
               "step": "patch", "detail": os.path.basename(patch_path)})
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print("update: merged %s into %s" % (patch_path, result_path))


def cmd_check(args):
    result_path = args.result
    problems = []
    try:
        with open(result_path, "r", encoding="utf-8-sig") as fh:
            result = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print("CHECK FAIL: result.json not parseable: %s" % exc)
        return 1
    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in ALLOWED_STATUS:
        problems.append("invalid status %r" % result.get("status"))
    for s in result.get("sources", []):
        if not s.get("locator") or not s.get("quote"):
            problems.append("source missing locator/quote: %r" % s.get("item"))
    # contract fields check
    contract_keys = []
    if args.case_json and os.path.isfile(args.case_json):
        try:
            with open(args.case_json, "r", encoding="utf-8-sig") as fh:
                case = json.load(fh)
            for key in CONTRACT_KEYS:
                if key in case:
                    c = case[key]
                    contract_keys = (list(c.keys()) if isinstance(c, dict)
                                     else [str(x) for x in c])
                    break
        except Exception:  # noqa: BLE001
            pass
    for key in contract_keys:
        if key not in result and "_contract_" + key not in result:
            problems.append("contract field missing: %s" % key)
    if problems:
        for p in problems:
            print("CHECK FAIL: %s" % p)
        return 1
    print("CHECK OK: %s" % result_path)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="document-library retrieval/repair pipeline")
    p.add_argument("--input", default=DEFAULT_INPUT, help="input root")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="output dir")
    p.add_argument("--work", default=DEFAULT_WORK, help="work dir")
    p.add_argument("--case-json", default=None, help="case.json path")
    p.add_argument("--task-path", default=None, help="task.md path")
    p.add_argument("--skeleton-only", action="store_true",
                   help="discover + write partial result skeleton only")
    p.add_argument("--max-hash-bytes", type=int, default=MAX_HASH_BYTES)
    p.add_argument("--max-text-bytes", type=int, default=MAX_TEXT_BYTES)
    p.add_argument("--update", metavar="PATCH.json",
                   help="merge a JSON patch into result.json")
    p.add_argument("--result", metavar="RESULT.json",
                   default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p.add_argument("--check", action="store_true", help="validate result.json")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if args.check:
        return cmd_check(args)
    if args.update:
        return cmd_update(args)
    if args.case_json is None:
        args.case_json = os.path.join(args.input, "case.json")
    if args.task_path is None:
        t = os.path.join(args.input, "task.md")
        if os.path.isfile(t):
            args.task_path = t
    pipe = Pipeline(args.input, args.output, args.work,
                    case_json=args.case_json, task_path=args.task_path,
                    max_hash_bytes=args.max_hash_bytes,
                    max_text_bytes=args.max_text_bytes,
                    skeleton_only=args.skeleton_only)
    try:
        pipe.run()
    except Exception as exc:  # noqa: BLE001
        # last-resort: never leave an empty output dir; write minimal payload
        pipe.errors.append("unhandled exception: %s" % exc)
        try:
            pipe.write_result(status="partial")
        except Exception:  # noqa: BLE001
            pass
        print("pipeline error: %s" % exc, file=sys.stderr)
        return 2
    nf = len(pipe.files)
    print("finalize: %d files, %d dup groups, %d version candidates, "
          "%d alias groups, %d reconcile missing -> %s"
          % (nf, len(getattr(pipe, "dup_groups", [])),
             len(getattr(pipe, "version_candidates", [])),
             len(getattr(pipe, "aliases", [])),
             len(getattr(pipe, "reconciliation", {}).get("missing", [])),
             os.path.join(args.output, "result.json")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
