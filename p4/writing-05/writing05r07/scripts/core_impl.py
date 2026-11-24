#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robust I/O and deterministic evidence helpers (Python standard library only).

Design contract: every public function returns ``(value, error)`` instead of
raising on malformed input.  This keeps the Skill alive across empty inputs,
bad JSON, bad encodings, oversized files and missing parameters.

Nothing in this module fabricates scientific data; it only normalises and
scaffolds what the caller actually read from disk.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MAX_BYTES = 2_000_000  # per text file; larger files are skipped
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
PDF_TIMEOUT = 180  # seconds

# Evidence-strength ladder, weakest -> strongest.  The LLM grades claims with
# these enums; the script only validates that a reported level is known.
STRENGTH_LEVELS = {
    "unknown":         {"rank": 0, "label": "无法判定",     "wording": "证据不足，不作结论"},
    "opinion":         {"rank": 1, "label": "专家意见/社论", "wording": "仅个别观点认为"},
    "preprint":        {"rank": 1, "label": "预印本",       "wording": "未经同行评议的预印本提示"},
    "case_report":     {"rank": 2, "label": "个案报告",     "wording": "个案报告显示"},
    "preclinical":     {"rank": 2, "label": "临床前研究",   "wording": "体外/动物实验提示"},
    "case_series":     {"rank": 3, "label": "病例系列",     "wording": "小样本病例系列提示"},
    "cross_sectional": {"rank": 3, "label": "横断面研究",   "wording": "横断面研究显示（相关，非因果）"},
    "cohort":          {"rank": 4, "label": "队列研究",     "wording": "队列研究显示（相关，非因果）"},
    "case_control":    {"rank": 4, "label": "病例对照研究", "wording": "病例对照研究显示（相关，非因果）"},
    "rct":             {"rank": 5, "label": "随机对照试验", "wording": "随机对照试验表明"},
    "guideline":       {"rank": 5, "label": "临床指南",     "wording": "指南推荐"},
    "meta":            {"rank": 6, "label": "系统综述/Meta分析", "wording": "多项研究一致表明（领域共识）"},
}


def now_iso() -> str:
    """ISO-8601 UTC timestamp with timezone, used for change_log/runtime."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path, max_bytes=DEFAULT_MAX_BYTES):
    """Read a text file with encoding fallback.  Returns ``(text, error)``."""
    path = Path(path)
    if not path.exists():
        return None, f"missing file: {path}"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"stat failed: {exc}"
    if size > max_bytes:
        return None, f"oversized file ({size} bytes > {max_bytes}): {path}"
    raw = None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"read failed: {exc}"
    last_err = None
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError) as exc:
            last_err = f"{enc}: {exc}"
    # latin-1 is the final fallback and always decodes.
    try:
        return raw.decode("latin-1"), None
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"decode failed: {last_err}; {exc}"


def is_probably_binary(path, peek=8192):
    """Heuristic: NUL bytes in the head usually mean a binary file."""
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(peek)
    except OSError:
        return True


def read_json(path):
    """Parse JSON with encoding fallback.  Returns ``(obj_or_None, error)``."""
    path = Path(path)
    if not path.exists():
        return None, f"missing file: {path}"
    raw = None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"read failed: {exc}"
    last_err = None
    for enc in TEXT_ENCODINGS:
        try:
            text = raw.decode(enc)
            return json.loads(text), None
        except (UnicodeDecodeError, LookupError) as enc_err:
            last_err = f"{enc}: {enc_err}"
            continue
        except json.JSONDecodeError as j_err:
            last_err = f"{enc} json: {j_err}"
            continue
    return None, f"bad JSON ({last_err})"


def atomic_write_json(path, data):
    """Write JSON atomically (temp file + os.replace).  Returns error or None."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return None
    except OSError as exc:
        return f"write failed: {exc}"


def discover_inputs(input_dir, max_bytes=DEFAULT_MAX_BYTES):
    """Walk an input directory.  Returns ``(files, oversized, errors)``."""
    root = Path(input_dir)
    if not root.exists():
        return [], [], [f"input directory missing: {root}"]
    files, oversized, errors = [], [], []
    try:
        entries = sorted(root.rglob("*"))
    except OSError as exc:
        return [], [], [f"walk failed: {exc}"]
    for p in entries:
        try:
            if not p.is_file():
                continue
            size = p.stat().st_size
        except OSError as exc:
            errors.append(f"stat failed {p}: {exc}")
            continue
        rel = str(p.relative_to(root))
        if size > max_bytes:
            oversized.append({"path": rel, "bytes": size})
            continue
        files.append({
            "path": rel,
            "bytes": size,
            "abs": str(p),
            "suffix": p.suffix.lower(),
            "binary": is_probably_binary(p),
        })
    return files, oversized, errors


def try_pdftotext(pdf_path, out_txt):
    """Extract PDF text via the pinned pdftotext tool.  Returns ``(text, error)``.

    Falls back gracefully: missing binary, timeout or non-zero exit all return
    an error string instead of raising.
    """
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext not available on PATH"
    out_txt = Path(out_txt)
    try:
        out_txt.parent.mkdir(parents=True, exist_ok=True)
        run = subprocess.run(
            [exe, "-layout", str(pdf_path), str(out_txt)],
            capture_output=True, text=True, timeout=PDF_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        if run.returncode != 0:
            return None, f"pdftotext rc={run.returncode}: {run.stderr[:400]}"
        if not out_txt.exists() or out_txt.stat().st_size == 0:
            return None, f"pdftotext produced no text for {pdf_path}"
        text, err = read_text(out_txt)
        return text, err
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        return None, f"pdftotext error: {type(exc).__name__}: {exc}"


_SENT_SPLIT = re.compile(r"(?<=[。．.!?！？；;])\s*|\n+")


def segment_sentences(text, max_claims=800):
    """Deterministic sentence splitter producing numbered claim units.

    This is a scaffolding aid for the LLM; the LLM is expected to refine the
    segmentation into *judgeable claims* (one testable assertion each) and to
    drop non-assertion fragments.  Positions make every unit locatable.
    """
    if not text:
        return []
    units, pos = [], 0
    for m in _SENT_SPLIT.finditer(text):
        seg = text[pos:m.start()].strip()
        if seg:
            units.append({"n": len(units) + 1, "start": pos, "end": m.start(), "text": seg})
        pos = m.end()
        if len(units) >= max_claims:
            break
    tail = text[pos:].strip()
    if tail and len(units) < max_claims:
        units.append({"n": len(units) + 1, "start": pos, "end": len(text), "text": tail})
    return units


_LOCATOR_RE = re.compile(
    r"^S(?P<section>\d+)\.P(?P<para>\d+)\.s(?P<sentence>\d+)$"
)


def normalize_locator(value, doc=""):
    """Normalise a locator to ``S{sec}.P{para}.s{sent}`` when possible.

    Accepts ``P3``, ``P3.s2``, ``S2.P1.s4``, or a bare character range
    ``@120-180``.  Unrecognised values are kept verbatim (never discarded).
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = _LOCATOR_RE.match(s)
    if m:
        return s
    # P3.s2 -> S?.P3.s2, keep section unknown as 0
    m2 = re.match(r"^P(?P<para>\d+)\.s(?P<sentence>\d+)$", s)
    if m2:
        return f"S0.P{m2.group('para')}.s{m2.group('sentence')}"
    m3 = re.match(r"^P(?P<para>\d+)$", s)
    if m3:
        return f"S0.P{m3.group('para')}.s0"
    m4 = re.match(r"^@(?P<a>\d+)-(?P<b>\d+)$", s)
    if m4:
        return s  # char range locator kept as-is
    if doc:
        return f"{doc}:{s}"
    return s


def validate_strength(level):
    """Return the level if it is a known enum, else ``unknown``."""
    if isinstance(level, str) and level in STRENGTH_LEVELS:
        return level
    return "unknown"


def init_result(case_id=None):
    """A valid, honest partial result skeleton."""
    ts = now_iso()
    return {
        "case_id": case_id,
        "status": "in_progress",
        "deliverable": {"claims": [], "evidence_table": [], "manuscript": None, "summary": ""},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"time": ts, "action": "scaffold", "detail": "initial partial skeleton"}],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": ts, "finished_at": None, "seconds": None},
    }


def merge_log(change_log, action, detail):
    """Append a change_log entry without mutating shared structures."""
    entry = {"time": now_iso(), "action": action, "detail": detail}
    if isinstance(change_log, list):
        return change_log + [entry]
    return [entry]


def compute_status(result):
    """Conservative status: never downgrade a finished state to in_progress."""
    cur = result.get("status")
    if cur in ("complete", "partial"):
        return cur
    claims = _len(result.get("deliverable", {}).get("claims"))
    evid = _len(result.get("deliverable", {}).get("evidence_table"))
    manuscript = result.get("deliverable", {}).get("manuscript")
    if manuscript and claims and evid:
        return "complete"
    if claims or evid or manuscript:
        return "partial"
    if _len(result.get("errors")):
        return "input_error"
    return "insufficient_evidence"


def _len(obj):
    if isinstance(obj, (list, tuple, str, dict)):
        return len(obj)
    return 0


if __name__ == "__main__":  # lightweight smoke check
    print(json.dumps({
        "levels": sorted(STRENGTH_LEVELS),
        "normalize_examples": [
            normalize_locator("S2.P1.s4"),
            normalize_locator("P3"),
            normalize_locator("@120-180"),
        ],
    }, ensure_ascii=False, indent=2))
