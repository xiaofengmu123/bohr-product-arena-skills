#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py -- engine for the "latex-writing-compile-budgeted" Skill.

Design focus (variant #时间预算与早落盘):
  * A persistent phase ledger (/app/work/state.json) with per-phase time/request
    allowances derived from the global 900 s / 64 request budget.
  * Hard guardrails: when remaining time < 120 s or remaining requests < 10, the
    run is forced into "finalize" and the honest partial result is kept.
  * The first valid result.json skeleton is flushed during bootstrap (t ~ 0),
    i.e. inside the mandatory 3-minute early window; later phases only improve it
    monotonically. Any crash therefore leaves a loadable, honest result.

Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst",
    ".toc", ".lof", ".lot",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")
RESERVED_KEYS = {
    "case_id", "status", "artifacts", "sources", "uncertainty",
    "pending_human", "errors", "degradations", "change_log", "runtime",
}

# ---- time-budget variant constants ----
TOTAL_SECONDS = 900
TOTAL_REQUESTS = 64
EARLY_FLUSH_SECONDS = 180  # mandatory first partial result within 3 minutes
HARD_MIN_TIME = 120         # below this, stop improving and finalize
HARD_MIN_REQUESTS = 10      # below this, stop improving and finalize
COMPILE_TIMEOUT_SECONDS = 120
DEFAULT_PASSES = 3

PHASE_PLAN = [
    {"phase": "bootstrap",   "seconds": 60,  "requests": 3,  "goal": "flush result.json skeleton + inventory + state (early flush)"},
    {"phase": "discover",    "seconds": 60,  "requests": 4,  "goal": "scan materials, classify genre/template/contract"},
    {"phase": "draft",       "seconds": 120, "requests": 16, "goal": "fact pool -> editable main.tex + .bib, fill contract fields"},
    {"phase": "compile_fix", "seconds": 420, "requests": 30, "goal": "compile -> classify log -> root-cause fix -> recompile loop"},
    {"phase": "verify",      "seconds": 180, "requests": 8,  "goal": "evidence map, selfcheck, fill sources/uncertainty/pending_human"},
    {"phase": "finalize",    "seconds": 60,  "requests": 3,  "goal": "validate contract keys, attach PDF preview if compiled, final status"},
]

ERROR_CATEGORIES = {
    "latex_error", "missing_class", "missing_package", "undefined_control_sequence",
    "math_error", "runaway_argument", "font_missing", "capacity_exceeded",
    "too_many_errors",
}
WARNING_CATEGORIES = {
    "undefined_reference", "undefined_citation", "multiply_defined_label",
    "overfull", "underfull",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio() -> None:
    """Ensure UTF-8 stdio so JSON output never crashes on a non-UTF-8 terminal."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------- IO helpers

def read_text(path):
    """Read UTF-8 text tolerating BOM/encoding issues. Returns (text, error)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds %d bytes" % MAX_FILE_BYTES
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def read_json(path):
    """Tolerant JSON read. Returns (obj, errors_list)."""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[:exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json_atomic(path, obj):
    """Write JSON via temp file + rename so a crash never leaves a partial file."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = __import__("tempfile").mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ------------------------------------------------------------- input discovery

def discover_input(root):
    files = []
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "path": full,
                "rel": os.path.relpath(full, root).replace("\\", "/"),
                "size": size,
                "ext": ext,
                "is_text": ext in TEXT_EXTS,
            })
    return files


def find_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    return None


def contract_keys(obj):
    """Return output_contract keys to echo back verbatim, preserving order."""
    if not isinstance(obj, dict):
        return []
    contract = obj.get("output_contract")
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(k for k in item.keys() if k not in keys)
    return keys


def material_files(files):
    """Files other than case.json / task.md that plausibly carry the science material."""
    return [f for f in files if f["rel"] not in ("case.json", "task.md")]


def build_skeleton(input_root):
    """Build result.json skeleton. Returns (skeleton, keys, case_id, notes)."""
    case_path = os.path.join(input_root, "case.json")
    notes = []
    case_id = None
    keys = []
    files = discover_input(input_root)
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(obj)
        keys = contract_keys(obj)
        if not keys:
            notes.append("output_contract_not_found: metadata-only result")
    else:
        notes.append("case.json_not_found_in:" + input_root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    skeleton = {"case_id": case_id}
    for key in keys:
        if key not in RESERVED_KEYS:
            skeleton[key] = None
    skeleton.update({
        "status": "partial" if keys else "input_error",
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": notes,
        "degradations": [],
        "change_log": [{
            "time": utcnow(),
            "step": "bootstrap",
            "note": "partial skeleton flushed within the first 3 minutes; contract fields default to null until evidence is written",
        }],
        "runtime": {
            "started_at": utcnow(),
            "finished_at": None,
            "producer": "main.py",
            "engine": None,
            "compile_passes": None,
        },
    })
    return skeleton, keys, case_id, notes


def result_paths_present(files):
    return bool(material_files(files))


# ------------------------------------------------------------- state ledger

def reset_state(work, input_root):
    state = {
        "started_at": utcnow(),
        "phase": "bootstrap",
        "budget": {
            "total_seconds": TOTAL_SECONDS,
            "total_requests": TOTAL_REQUESTS,
            "early_flush_seconds": EARLY_FLUSH_SECONDS,
            "hard_min_time": HARD_MIN_TIME,
            "hard_min_requests": HARD_MIN_REQUESTS,
            "phase_plan": PHASE_PLAN,
        },
        "phases": [],
        "degradations": [],
        "input_root": input_root,
        "last_flushed_at": None,
    }
    write_json_atomic(os.path.join(work, "state.json"), state)
    return state


def load_state(work):
    path = os.path.join(work, "state.json")
    if not os.path.isfile(path):
        return None
    obj, _errs = read_json(path)
    return obj if isinstance(obj, dict) else None


def save_state(work, state):
    state["last_flushed_at"] = utcnow()
    write_json_atomic(os.path.join(work, "state.json"), state)
    return state


def phase_begin(work, phase, requests=0):
    state = load_state(work)
    if state is None:
        state = reset_state(work, "/input")
    state["phase"] = phase
    state.setdefault("phases", []).append({
        "phase": phase, "began_at": utcnow(), "ended_at": None,
        "requests": int(requests or 0),
    })
    save_state(work, state)
    return state, BudgetGovernor(state).report()


def phase_commit(work, phase, requests=0, note=""):
    state = load_state(work)
    if state is None:
        return None, None
    for p in reversed(state.setdefault("phases", [])):
        if p["phase"] == phase and p.get("ended_at") is None:
            p["ended_at"] = utcnow()
            p["requests"] = int(requests or p.get("requests", 0))
            if note:
                p["note"] = note
            break
    state["phase"] = phase + "_done"
    save_state(work, state)
    return state, BudgetGovernor(state).report()


# ------------------------------------------------------------- budget governor

class BudgetGovernor:
    """Tracks elapsed wall-clock time and declared request usage against the plan."""

    def __init__(self, state):
        self.state = state or {}
        budget = self.state.get("budget") or {}
        self.total_seconds = int(budget.get("total_seconds", TOTAL_SECONDS))
        self.total_requests = int(budget.get("total_requests", TOTAL_REQUESTS))
        self.early_flush = int(budget.get("early_flush_seconds", EARLY_FLUSH_SECONDS))
        self.hard_min_time = int(budget.get("hard_min_time", HARD_MIN_TIME))
        self.hard_min_requests = int(budget.get("hard_min_requests", HARD_MIN_REQUESTS))
        self.started_at = self.state.get("started_at")

    def _epoch(self, ts):
        if not ts:
            return time.time()
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except Exception:
            return time.time()

    def elapsed(self):
        return max(0.0, time.time() - self._epoch(self.started_at))

    def remaining_time(self):
        return max(0.0, self.total_seconds - self.elapsed())

    def used_requests(self):
        return sum(int(p.get("requests", 0)) for p in (self.state.get("phases") or []))

    def remaining_requests(self):
        return max(0, self.total_requests - self.used_requests())

    def phase_allowance(self, phase):
        plan = (self.state.get("budget") or {}).get("phase_plan", PHASE_PLAN)
        for p in plan:
            if p.get("phase") == phase:
                return int(p.get("seconds", 60)), int(p.get("requests", 2))
        return 60, 2

    def must_downgrade(self):
        reasons = []
        if self.remaining_time() < self.hard_min_time:
            reasons.append("low_time")
        if self.remaining_requests() < self.hard_min_requests:
            reasons.append("low_requests")
        return (bool(reasons), reasons)

    def early_flush_due(self):
        return self.elapsed() >= self.early_flush

    def report(self):
        downgrade, reasons = self.must_downgrade()
        return {
            "started_at": self.started_at,
            "elapsed_s": round(self.elapsed(), 1),
            "remaining_time_s": round(self.remaining_time(), 1),
            "used_requests": self.used_requests(),
            "remaining_requests": self.remaining_requests(),
            "must_downgrade": downgrade,
            "downgrade_reasons": reasons,
            "early_flush_due": self.early_flush_due(),
            "phase_plan": (self.state.get("budget") or {}).get("phase_plan", PHASE_PLAN),
        }


# ------------------------------------------------------------- LaTeX engine

def find_engine(prefer=None):
    candidates = ([prefer] if prefer else [])
    candidates += ["latexmk", "tectonic", "pdflatex", "xelatex", "lualatex"]
    for name in candidates:
        if name:
            path = shutil.which(name)
            if path:
                return name, path
    return None, None


def _build_command(engine, main, outdir, pass_idx):
    if engine == "latexmk":
        return ["latexmk", "-pdf", "-interaction=nonstopmode", "-file-line-error",
                "-halt-on-error", "-outdir", outdir, main]
    if engine == "tectonic":
        return ["tectonic", "-Z", "shell-escape", main]
    return [engine, "-interaction=nonstopmode", "-halt-on-error", "-file-line-error",
            "-synctex=1", "-output-directory", outdir, main]


def _find_pdf(main, outdir):
    base = os.path.splitext(os.path.basename(main))[0]
    for d in (outdir, os.path.dirname(os.path.abspath(main))):
        p = os.path.join(d, base + ".pdf")
        if os.path.isfile(p):
            return p
    return None


def _log_for(main, outdir):
    base = os.path.splitext(os.path.basename(main))[0]
    p = os.path.join(outdir, base + ".log")
    return p if os.path.isfile(p) else None


def compile_latex(main, outdir=None, engine=None, passes=DEFAULT_PASSES,
                  timeout=COMPILE_TIMEOUT_SECONDS):
    """Compile a .tex up to `passes` times, classifying every pass's log."""
    main = os.path.abspath(main)
    base = os.path.dirname(main)
    outdir = os.path.abspath(outdir) if outdir else base
    os.makedirs(outdir, exist_ok=True)
    name, path = find_engine(engine)
    if name is None:
        return {
            "engine": None, "engine_path": None, "success": False, "engine_missing": True,
            "degradations": ["engine_missing: no latexmk/tectonic/pdflatex/xelatex/lualatex on PATH"],
            "passes": [], "root_cause": None, "pdf": None, "log": None, "command": None,
        }
    pass_reports = []
    success = False
    for i in range(int(passes)):
        cmd = _build_command(name, main, outdir, i)
        entry = {"pass": i + 1, "command": cmd}
        try:
            proc = subprocess.run(cmd, cwd=base, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            findings = classify_log(combined, main)
            pdf = _find_pdf(main, outdir)
            ok = proc.returncode == 0 and bool(pdf)
            entry.update({"returncode": proc.returncode, "ok": ok, "pdf": pdf,
                          "log": _log_for(main, outdir), "findings": findings,
                          "rerun_needed": findings.get("rerun_needed", False)})
            pass_reports.append(entry)
            if ok and not findings.get("rerun_needed"):
                success = True
                break
            if proc.returncode != 0 and findings.get("has_error"):
                break  # -halt-on-error already stopped; do not blindly loop
        except subprocess.TimeoutExpired as exc:
            entry.update({"timeout": True, "returncode": None,
                          "stderr": str(exc), "findings": {}})
            pass_reports.append(entry)
            break
        except OSError as exc:
            entry.update({"oserror": str(exc), "returncode": None, "findings": {}})
            pass_reports.append(entry)
            break

    if not success:
        success = any(p.get("ok") for p in pass_reports)
    pdf = _find_pdf(main, outdir)
    root_cause = None
    for p in reversed(pass_reports):
        rc = (p.get("findings") or {}).get("root_cause")
        if rc:
            root_cause = rc
            break
    return {
        "engine": name, "engine_path": path, "success": success, "engine_missing": False,
        "passes": pass_reports, "root_cause": root_cause, "pdf": pdf,
        "log": _log_for(main, outdir),
        "command": pass_reports[-1]["command"] if pass_reports else None,
    }


# ------------------------------------------------------------ log classifier

FILE_LINE = re.compile(r"^(?:\./|/)?(?P<file>[^\s:()]+):(?P<line>\d+):\s*(?P<msg>.*)$")
LINE_HINT = re.compile(r"^l\.(?P<line>\d+)\s+(?P<ctx>.*)$")
RERUN = re.compile(r"Rerun to get cross-references right", re.I)

CATEGORY_RULES = [
    ("missing_class", re.compile(r"LaTeX Error: File `[^']+\.cls' not found", re.I)),
    ("missing_package", re.compile(r"LaTeX Error: File `[^']+' not found", re.I)),
    ("undefined_control_sequence", re.compile(r"Undefined control sequence\.", re.I)),
    ("math_error", re.compile(r"(?:Double subscript|Double superscript|Missing [{$] inserted|Extra [}]|Misplaced alignment tab character|Display math should end with)", re.I)),
    ("runaway_argument", re.compile(r"Runaway argument\?", re.I)),
    ("font_missing", re.compile(r"(?:fontspec error|The font |LaTeX Error: Font .*not found)", re.I)),
    ("capacity_exceeded", re.compile(r"TeX capacity exceeded", re.I)),
    ("too_many_errors", re.compile(r"Fatal error occurred", re.I)),
]
WARNING_RULES = [
    ("undefined_reference", re.compile(r"LaTeX Warning: Reference `([^']+)' on page \d+ undefined", re.I)),
    ("undefined_citation", re.compile(r"LaTeX Warning: Citation `([^']+)' on page \d+ undefined", re.I)),
    ("multiply_defined_label", re.compile(r"LaTeX Warning: Label `([^']+)' multiply defined", re.I)),
    ("overfull", re.compile(r"Overfull \\hbox", re.I)),
    ("underfull", re.compile(r"Underfull \\hbox", re.I)),
]


def _locate(lines, i, main):
    """Recover file/line for a TeX error block, preferring file:line then l.N."""
    file_ = os.path.basename(main) if main else None
    line = None
    for j in range(i, min(i + 6, len(lines))):
        m = LINE_HINT.match(lines[j].strip())
        if m:
            line = m.group("line")
            break
    for j in range(i, max(-1, i - 8), -1):
        m = FILE_LINE.match(lines[j].strip())
        if m:
            file_ = m.group("file")
            line = line or m.group("line")
            break
        m2 = re.match(r"^\((.+)$", lines[j].strip())
        if m2:
            file_ = m2.group(1).strip()
    return file_, line


def _categorize(message):
    for cat, rx in CATEGORY_RULES:
        if rx.search(message or ""):
            return cat
    return "latex_error"


def _context(lines, i):
    for j in range(i + 1, min(i + 4, len(lines))):
        m = LINE_HINT.match(lines[j].strip())
        if m:
            return m.group("ctx").strip()
    return ""


def classify_log(text, main=None):
    text = text or ""
    lines = text.splitlines()
    findings = []

    # Classic "! " blocks (nonstopmode without -file-line-error).
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("!"):
            continue
        message = stripped.lstrip("! ").strip()
        category = _categorize(message)
        file_, lineno = _locate(lines, i, main)
        findings.append({"category": category, "message": message,
                         "file": file_, "line": lineno,
                         "context": _context(lines, i)})

    # -file-line-error style "file:line: message" lines.
    for i, line in enumerate(lines):
        m = FILE_LINE.match(line.strip())
        if not m:
            continue
        msg = m.group("msg").strip()
        category = _categorize(msg)
        if category == "latex_error" and not ("error" in msg.lower() or "undefined" in msg.lower()):
            continue
        findings.append({"category": category, "message": msg,
                         "file": m.group("file"), "line": m.group("line"),
                         "context": _context(lines, i)})

    for cat, rx in WARNING_RULES:
        for m in rx.finditer(text):
            findings.append({"category": cat, "message": m.group(0),
                             "file": None, "line": None, "context": None})

    root_cause = next((f for f in findings if f["category"] in ERROR_CATEGORIES), None)
    has_error = root_cause is not None
    return {
        "findings": findings,
        "root_cause": root_cause,
        "has_error": has_error,
        "rerun_needed": bool(RERUN.search(text)),
        "refcheck": refcheck(text),
    }


def refcheck(text):
    refs = re.findall(r"Reference `([^']+)' on page \d+ undefined", text or "")
    cites = re.findall(r"Citation `([^']+)' on page \d+ undefined", text or "")
    dup = re.findall(r"Label `([^']+)' multiply defined", text or "")
    return {
        "undefined_refs": sorted(set(refs)),
        "undefined_cites": sorted(set(cites)),
        "multiply_defined": sorted(set(dup)),
        "rerun_needed": bool(RERUN.search(text or "")),
    }


# ------------------------------------------------------------- evidence tools

NUMBER_UNIT = re.compile(
    r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:%|°[CFK]|µm|nm|mm|cm|m|kg|g|s|min|h|"
    r"eV|keV|MeV|GeV|V|A|W|Hz|kHz|MHz|GHz|mol|M|Pa|bar)?"
)
DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
CITE = re.compile(r"\\cite\{([^}]+)\}")


def index_sources(root, out_path):
    files = discover_input(root)
    index = {"root": root, "generated_at": utcnow(), "files": []}
    for f in files:
        entry = dict(f)
        if f["is_text"]:
            text, err = read_text(f["path"])
            if text is not None:
                entry["lines"] = text.count("\n") + 1
            if err:
                entry["read_error"] = err
        index["files"].append(entry)
    write_json_atomic(out_path, index)
    return index


def quote(file_path, start=None, end=None):
    text, err = read_text(file_path)
    if text is None:
        return {"file": file_path, "error": err, "text": None}
    lines = text.splitlines()
    if start is None:
        start = 1
    if end is None:
        end = len(lines)
    start = max(1, min(int(start), max(len(lines), 1)))
    end = max(start, min(int(end), len(lines)))
    return {"file": file_path, "start": start, "end": end,
            "text": "\n".join(lines[start - 1:end]), "error": err}


def extract_facts(text, max_facts=200):
    facts = []
    for m in NUMBER_UNIT.finditer(text or ""):
        s = m.group(0).strip()
        if any(ch.isdigit() for ch in s):
            ctx = text[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
            facts.append({"value": s, "context": ctx})
            if len(facts) >= max_facts:
                break
    return facts
