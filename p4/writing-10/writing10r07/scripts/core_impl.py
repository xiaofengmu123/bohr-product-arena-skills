#!/usr/bin/env python3
"""Bootstrap a partial, valid /app/output/result.json plus a revision-ledger
template from the /input case materials.

Robustness variant goals (all fail-soft, never crash):
  * empty input dir      -> status=insufficient_evidence, exit 0
  * bad JSON / encoding  -> recorded in errors/degradations, continue
  * missing params       -> defaults to /input, /app/output, /app/work
  * oversized files      -> skipped (>2MB) and recorded in degradations
  * write failure        -> retried once, then a minimal valid artifact
Only the Python standard library is used; output is deterministic.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB per-file cap
ENCODINGS = ("utf-8-sig", "utf-8", "latin-1")
ALLOWED_STATUS = {
    "in_progress", "complete", "partial",
    "insufficient_evidence", "input_error", "failed",
}
COMMENT_TYPES = ("method", "data", "writing", "format", "ethics")
TONE_VALUES = ("accept", "clarify", "polite_refute", "appeal")


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """Return (text, encoding, error). Never raises on missing/bad files."""
    if not os.path.exists(path):
        return None, None, "missing"
    if os.path.isdir(path):
        return None, None, "is_directory"
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, None, "stat_error:%s" % e
    if size > MAX_FILE_BYTES:
        return None, None, "oversized:%d" % size
    last_err = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except (UnicodeDecodeError, OSError) as e:
            last_err = "%s:%s" % (type(e).__name__, e)
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace"), "utf-8-replace", last_err
    except OSError as e:
        return None, None, "%s:%s" % (type(e).__name__, e)


def load_json(path):
    """Return (obj, error). Never raises."""
    text, enc, err = read_text(path)
    if err:
        return None, "read:%s" % err
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return None, "json:%s" % e


def find_case_id(obj):
    """Try common case-id keys without assuming a fixed schema."""
    if not isinstance(obj, dict):
        return None
    for key in ("case_id", "caseId", "challenge_id", "challengeId", "id"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def discover_materials(input_dir):
    """Walk input_dir; return list of {path, rel, size, read_error, encoding}."""
    found = []
    if not os.path.isdir(input_dir):
        return found
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            text, enc, err = read_text(full)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            found.append({
                "path": full,
                "rel": rel,
                "size": size,
                "encoding": enc,
                "read_error": err,
            })
    return found


def extract_comments(text):
    """Heuristically split reviewer comments into {id, quote, anchor}.

    Supports structured IDs (R1, Comment 1, 意见1, 评论 1, 1.) and falls back
    to assigning C<n> ids to non-empty paragraphs. Returns an ordered list.
    """
    if not text:
        return []
    lines = text.splitlines()
    header_re = re.compile(
        r'^\s*(?:#+\s*)?'
        r'(?:(?:R\s*(\d+))|(?:[Cc]omment\s*(\d+))|(?:意见\s*(\d+))|(?:评论\s*(\d+))|(?:(\d+)\s*[.、]))'
        r'\s*[:：\-]?\s*(.*)$'
    )
    comments = []
    current = None
    for idx, raw in enumerate(lines, 1):
        m = header_re.match(raw)
        if m:
            gid = next((g for g in m.groups()[:5] if g is not None), None)
            rest = (m.group(6) or "").strip()
            cid = "R%s" % gid if gid else None
            if current is not None and (current["quote"] or current.get("extra")):
                comments.append(current)
            current = {"id": cid, "quote": rest, "anchor": idx, "extra": []}
            continue
        if current is not None:
            s = raw.strip()
            if s:
                current["extra"].append(s)
            continue
        # text before any recognized header is kept as a preamble (ignored here)
    if current is not None:
        comments.append(current)

    out = []
    for c in comments:
        body = " ".join([c["quote"]] + c["extra"]).strip()
        if not body:
            continue
        cid = c["id"] or ("C%d" % (len(out) + 1))
        out.append({"id": cid, "quote": body[:400], "anchor": c["anchor"]})

    # Fallback: no structured header found -> one comment per non-empty line
    if not out:
        for idx, raw in enumerate(lines, 1):
            s = raw.strip()
            if s and not s.startswith("#") and len(s) > 1:
                out.append({"id": "C%d" % (len(out) + 1), "quote": s[:400], "anchor": idx})
    return out


def write_json(path, obj):
    """Write JSON atomically-ish; return error string or None."""
    for attempt in (1, 2):
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return None
        except OSError as e:
            if attempt == 2:
                return "write_error:%s" % e
    return "write_error:unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--out", default="/app/output/result.json")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args(argv)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    errors, degradations, gaps = [], [], []
    sources = []
    comment_index = []

    case_obj, case_err = load_json(a.case)
    if case_err:
        errors.append("case:%s" % case_err)
    case_id = find_case_id(case_obj) if case_obj else None
    if not case_id:
        case_id = "unknown"
        degradations.append("case_id not found in case.json; using 'unknown'")

    task_text, _, task_err = read_text(a.task)
    if task_err:
        degradations.append("task:%s" % task_err)

    materials = discover_materials(a.input)
    for m in materials:
        rel = m["rel"]
        if rel in ("case.json", "task.md"):
            continue
        if m["read_error"]:
            if m["read_error"].startswith("oversized"):
                degradations.append("skipped oversized file: %s (%s)" % (rel, m["read_error"]))
            else:
                degradations.append("unreadable file: %s (%s)" % (rel, m["read_error"]))
        sources.append({
            "id": "src-%d" % (len(sources) + 1),
            "type": "material",
            "ref": rel,
            "description": "input material (%s bytes)" % m["size"],
        })

    # Prefer the two named materials defined by this fixed case.
    baseline_rel = next((m for m in materials if m["rel"] == "materials/manuscript-baseline.md"), None)
    comments_rel = next((m for m in materials if m["rel"] == "materials/reviewer-comments.md"), None)
    if not baseline_rel:
        gaps.append("materials/manuscript-baseline.md not found; cannot apply revisions")
    if not comments_rel:
        gaps.append("materials/reviewer-comments.md not found; no comments to respond to")

    comments_text = None
    if comments_rel and not comments_rel["read_error"]:
        comments_text, _, _ = read_text(comments_rel["path"])
    comment_index = extract_comments(comments_text) if comments_text else []
    if comment_index:
        sources.append({
            "id": "src-comments",
            "type": "material",
            "ref": "materials/reviewer-comments.md",
            "description": "reviewer comments; %d comment blocks extracted" % len(comment_index),
        })

    if not materials:
        gaps.append("no input materials found under /input")

    if case_err:
        status = "input_error"
    elif not materials or not baseline_rel or not comments_rel:
        status = "insufficient_evidence"
    else:
        status = "in_progress"

    result = {
        "case_id": case_id,
        "status": status,
        "deliverable": {
            "path": "/app/output/revision_manuscript.md",
            "format": "markdown",
            "title": None,
            "components": {
                "revision_manuscript": {"path": "/app/output/revision_manuscript.md", "status": "not_started"},
                "response_letter": {"path": "/app/output/response_letter.md", "status": "not_started"},
                "revision_ledger": {"path": "/app/output/revision_ledger.json", "status": "not_started"},
                "evidence_list": {"path": "/app/output/evidence_list.md", "status": "not_started"},
            },
            "previews": [],
            "status": "not_started",
        },
        "fact_source_mapping": [],
        "version_boundary": {
            "changes": [],
            "pending_human": [],
            "material_gaps": gaps,
            "anomalies": [],
        },
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {"ts": now_ts(), "action": "scaffold", "detail": "partial skeleton written; status=%s" % status}
        ],
        "errors": errors,
        "degradations": degradations,
        "runtime": {
            "started_at": now_ts(),
            "comment_index": comment_index,
        },
    }

    err = write_json(a.out, result)
    if err:
        errors.append(err)
        # Final fallback: minimal but valid JSON in the same place.
        minimal = {
            "case_id": case_id,
            "status": "failed",
            "deliverable": {"path": None, "format": None, "title": None, "components": {}, "previews": [], "status": "not_started"},
            "fact_source_mapping": [],
            "version_boundary": {"changes": [], "pending_human": [], "material_gaps": ["result write failed"], "anomalies": []},
            "sources": sources,
            "uncertainty": [],
            "pending_human": [],
            "change_log": [{"ts": now_ts(), "action": "scaffold_fallback", "detail": err}],
            "errors": errors,
            "degradations": degradations,
        }
        write_json(a.out, minimal)

    # Also write a ledger template with one row per detected comment so the
    # agent has a checklist of every item to answer (the "答得全" guarantee).
    ledger_path = os.path.join(os.path.dirname(a.out), "revision_ledger.json")
    ledger = [
        {
            "comment_id": c["id"],
            "comment_quote": c["quote"],
            "comment_anchor": c["anchor"],
            "sub_requirements": [],
            "type": None,
            "severity": None,
            "decision": None,
            "rationale": "",
            "action": "",
            "manuscript_location": None,
            "evidence": {"status": "not_started", "source": None, "fallback": None},
            "tone": None,
            "verification": "unchecked",
            "cross_references": [],
        }
        for c in comment_index
    ]
    write_json(ledger_path, ledger)

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "status": status,
        "materials": len(materials),
        "comments_detected": len(comment_index),
        "errors": errors,
        "degradations": degradations,
        "ledger": ledger_path,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
