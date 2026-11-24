#!/usr/bin/env python3
"""Early-save bootstrap for reviewer-response cases. Standard library only.

Reads /input/case.json and task.md, discovers the baseline manuscript and
reviewer comments, decomposes comments into a structured ledger, and writes a
legal partial /app/output/result.json immediately. The ledger plus skeleton
guarantee that a later crash or budget exhaustion still leaves a valid,
inspectable output rather than an empty directory.

Usage:
  python scripts/scaffold.py --case /input/case.json --task /input/task.md \
      --work /app/work --out /app/output/result.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

DEFAULTS = {
    "case": "/input/case.json",
    "task": "/input/task.md",
    "work": "/app/work",
    "out": "/app/output/result.json",
}

TEXT_EXT = (".md", ".txt", ".json", ".csv", ".tex", ".markdown")
MAX_FILE = 2_000_000

REQUIRED_FIELDS = [
    "case_id", "status", "deliverable", "fact_source_mapping",
    "version_boundary", "sources", "uncertainty", "change_log",
]
ALLOWED_STATUS = {
    "in_progress", "complete", "partial", "failed", "ok",
    "insufficient_evidence", "input_error",
}


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, limit=MAX_FILE):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return None


def read_json(path):
    txt = read_text(path)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        return None


def list_materials(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for fname in sorted(files):
            if fname.lower().endswith(TEXT_EXT):
                out.append(os.path.join(dirpath, fname))
    return out


def discover_materials(input_dir):
    """Find the baseline manuscript and reviewer-comments files by basename."""
    manuscript = None
    comments = None
    others = []
    for p in list_materials(input_dir):
        base = os.path.basename(p).lower()
        if any(k in base for k in ("manuscript", "baseline", "draft", "paper")):
            if manuscript is None:
                manuscript = p
        elif any(k in base for k in ("comment", "review", "reviewer", "意见")):
            if comments is None:
                comments = p
        else:
            others.append(p)
    return manuscript, comments, others


_COMMENT_START = re.compile(
    r"^\s*(?:"
    r"(?:[Rr]eviewer\s*[#]?\s*\d+|R\d+)\s*[-–—:：]?\s*"
    r"|(?:[Cc]omment\s*[#]?\s*\d+)\s*[-–—:：]?\s*"
    r"|(?:意见|审稿意见)\s*\d+\s*[、.：:)）\-]?\s*"
    r"|Q\d+\s*[-–—:：]?\s*"
    r"|\d+[\.\)、:：]\s+"
    r")"
)


def decompose_comments(text):
    """Heuristic split of a reviewer-comments file into structured items.

    This is a deterministic starting ledger, not the final decomposition: the
    Agent must review, merge and enrich each item (type/severity/decision).
    """
    if not text:
        return []
    items, cur = [], None
    for ln in text.splitlines():
        stripped = ln.strip()
        if (_COMMENT_START.match(ln) and stripped
                and not stripped.startswith(("#", "```"))):
            if cur is not None and cur["raw"].strip():
                items.append(cur)
            cur = {"raw": ln, "body": [ln]}
        elif cur is not None:
            cur["body"].append(ln)
    if cur is not None and cur["raw"].strip():
        items.append(cur)

    ledger = []
    for i, it in enumerate(items, 1):
        ledger.append({
            "id": "C%02d" % i,
            "reviewer": None,
            "raw_text": "\n".join(it["body"]).strip(),
            "type": None,            # method|data|writing|format|ethics|other
            "severity": None,        # major|minor|cosmetic
            "decision": None,        # accept|partial|reject|clarify|n/a
            "sub_requirements": [],
            "actions": [],
            "evidence_status": None,  # provided|not_needed|cannot_provide|pending|alternative
            "locator": None,          # section > paragraph > "anchor quote"
            "anchor_quote": None,
            "status": "to_process",   # to_process|done|blocked|skipped
        })
    return ledger


def validate_schema(obj):
    errs = []
    if not isinstance(obj, dict):
        return False, ["result is not a JSON object"]
    for k in REQUIRED_FIELDS:
        if k not in obj:
            errs.append("missing required field: %s" % k)
    status = obj.get("status")
    if status is not None and status not in ALLOWED_STATUS:
        errs.append("invalid status: %r" % status)
    for k in ("fact_source_mapping", "sources", "uncertainty", "change_log"):
        if k in obj and not isinstance(obj.get(k), list):
            errs.append("%s must be an array" % k)
    if "deliverable" in obj and not isinstance(obj.get("deliverable"), dict):
        errs.append("deliverable must be an object")
    if "version_boundary" in obj and not isinstance(obj.get("version_boundary"), dict):
        errs.append("version_boundary must be an object")
    return len(errs) == 0, errs


def build_result(case_id, manuscript, comments, others, ledger,
                 material_gaps, degradations=None):
    sources = []
    for role, path in (("manuscript", manuscript), ("reviewer_comments", comments)):
        if path:
            sources.append({"id": role, "type": "material", "ref": path,
                            "description": os.path.basename(path)})
    for p in others:
        sources.append({"id": "material_%d" % len(sources), "type": "material",
                        "ref": p, "description": os.path.basename(p)})
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "deliverable": {
            "path": "/app/output/response_letter.md",
            "format": "markdown",
            "title": None,
            "sections": [],
            "status": "in_progress",
            "revised_manuscript": "/app/output/revised_manuscript.md",
        },
        "fact_source_mapping": [],
        "version_boundary": {
            "changes": [],
            "pending_confirmation": [],
            "material_gaps": material_gaps,
            "anomalies": [],
        },
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": degradations or [],
        "change_log": [],
        "runtime": {
            "started_at": ts(),
            "budget_seconds": 900,
            "budget_requests": 64,
            "comment_count": len(ledger),
        },
    }
    result["change_log"].append({
        "ts": ts(), "action": "scaffold",
        "detail": "partial skeleton written early (status=in_progress); "
                  "%d comments decomposed" % len(ledger),
    })
    if material_gaps:
        result["change_log"].append({
            "ts": ts(), "action": "material_gap",
            "detail": "; ".join(material_gaps),
        })
    return result


def run(case_path, task_path, work_dir, out_path):
    os.makedirs(work_dir, exist_ok=True)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    input_dir = os.path.dirname(os.path.abspath(case_path))
    degradations = []
    case, case_id = None, None
    case_raw = read_text(case_path)
    if case_raw is not None:
        try:
            case = json.loads(case_raw)
        except ValueError:
            degradations.append("case.json exists but is not valid JSON; treated as absent")
    else:
        degradations.append("case.json missing or unreadable")
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id")

    manuscript, comments, others = discover_materials(input_dir)
    gaps = []
    if manuscript is None:
        gaps.append("baseline manuscript not found under %s" % input_dir)
    if comments is None:
        gaps.append("reviewer comments not found under %s" % input_dir)

    comments_text = read_text(comments) if comments else None
    ledger = decompose_comments(comments_text)
    if not ledger and comments_text:
        ledger = [{
            "id": "C01", "reviewer": None, "raw_text": comments_text.strip(),
            "type": None, "severity": None, "decision": None,
            "sub_requirements": [], "actions": [], "evidence_status": None,
            "locator": None, "anchor_quote": None, "status": "to_process",
        }]

    result = build_result(case_id, manuscript, comments, others, ledger,
                          gaps, degradations)
    ok, errs = validate_schema(result)
    if not ok:
        result["errors"].extend(errs)

    ledger_path = os.path.join(work_dir, "comment_ledger.json")
    with open(ledger_path, "w", encoding="utf-8") as fh:
        json.dump({"case_id": case_id, "comments": ledger,
                   "manuscript_path": manuscript, "comments_path": comments,
                   "generated_at": ts()}, fh, ensure_ascii=False, indent=2)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default=DEFAULTS["case"])
    ap.add_argument("--task", default=DEFAULTS["task"])
    ap.add_argument("--work", default=DEFAULTS["work"])
    ap.add_argument("--out", default=DEFAULTS["out"])
    a = ap.parse_args()
    result = run(a.case, a.task, a.work, a.out)
    print(json.dumps({
        "ok": True, "status": result["status"], "case_id": result["case_id"],
        "comments": result["runtime"]["comment_count"],
        "material_gaps": result["version_boundary"]["material_gaps"],
        "degradations": result["degradations"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
