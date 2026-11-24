#!/usr/bin/env python3
"""Shared helpers for the reviewer-revision skill.

Standard library only; no network, no third-party deps, no fabrication.
All functions degrade gracefully: on bad input they return (None, error) and the
caller writes an honest partial result instead of crashing.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024

# The three output-contract fields fixed by the topic (used when case.json does
# not carry its own output_contract).
DEFAULT_CONTRACT_FIELDS = ["deliverable", "fact_source_mapping", "version_boundary"]

DECISION_OK = ("accept", "partial", "reject", "clarify")
CHANGE_OK = ("done", "partial", "not_done", "blocked")
EVIDENCE_OK = ("in_place", "planned", "not_possible", "partial")


def utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path) -> tuple:
    """Return (text, None) or (None, error). Tolerates BOM and latin-1 fallback."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return None, "cannot read: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "file too large (%d bytes > %d)" % (len(raw), MAX_FILE_BYTES)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return None, "undecodable bytes"


def load_json(path) -> tuple:
    """Return (obj, None) or (None, error)."""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, TypeError) as exc:
        return None, "bad json: %s" % exc


def write_json(path, obj) -> str | None:
    """Atomically write obj as UTF-8 JSON; return None on success or an error string."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return "mkdir failed: %s" % exc
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        return None
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return "write failed: %s" % exc


def discover_files(root) -> list:
    """Recursively list regular files under root as relative posix paths."""
    root = Path(root)
    if not root.exists():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            try:
                out.append(p.relative_to(root).as_posix())
            except ValueError:
                out.append(str(p))
    return out


def extract_case_id(case_obj) -> str | None:
    if not isinstance(case_obj, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        val = case_obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def contract_fields(case_obj) -> list:
    """Return output-contract field names from case.json, else the fixed three."""
    if isinstance(case_obj, dict):
        oc = case_obj.get("output_contract") or case_obj.get("outputContract")
        if isinstance(oc, dict) and oc:
            keys = [k for k in oc.keys() if isinstance(k, str) and k.strip()]
            if keys:
                return keys
        if isinstance(oc, list) and oc:
            keys = [str(k) for k in oc if isinstance(k, str) and k.strip()]
            if keys:
                return keys
    return list(DEFAULT_CONTRACT_FIELDS)


def material_paths(case_obj, task_text, input_dir) -> list:
    """Best-effort discovery of material file paths named in case/task text."""
    input_dir = Path(input_dir)
    refs = set()
    for source in (task_text or "", json.dumps(case_obj, ensure_ascii=False) if case_obj else ""):
        if not source:
            continue
        for m in re.finditer(r"materials/[^\s\"')\]]+", source):
            refs.add(m.group(0))
    paths = []
    for ref in sorted(refs):
        candidate = input_dir / ref
        if candidate.exists() and candidate.is_file():
            paths.append(str(candidate))
    return paths


def build_skeleton(case_path, task_path, out_dir, work_dir) -> tuple:
    """Write result.json skeleton with status=in_progress. Always returns (result, error)."""
    out_dir = Path(out_dir)
    work_dir = Path(work_dir)
    for d in (out_dir, work_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {}, "mkdir failed: %s" % exc

    errors, degradations = [], []
    case_obj, case_err = (None, "missing %s" % case_path)
    if os.path.exists(case_path):
        case_obj, case_err = load_json(case_path)
    if case_err:
        errors.append("case.json: %s" % case_err)
        if "missing" not in case_err:
            case_obj = None

    task_text, task_err = (None, "missing %s" % task_path)
    if os.path.exists(task_path):
        task_text, task_err = read_text(task_path)
    if task_err:
        degradations.append("task.md: %s" % task_err)

    case_id = extract_case_id(case_obj) if case_obj else None
    fields = contract_fields(case_obj)

    if case_id is None and not errors:
        errors.append("case_id not found in case.json")

    result = {
        "case_id": case_id or "unknown",
        "status": "in_progress",
        "deliverable": None,
        "fact_source_mapping": [],
        "version_boundary": {
            "changes": [],
            "pending_confirmation": [],
            "material_gaps": [],
            "anomalies": [],
        },
        "pending_human": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [{"ts": utcnow_iso(), "action": "scaffold", "detail": "skeleton written"}],
        "errors": errors,
        "degradations": degradations,
        "runtime": {"started_at": utcnow_iso(), "finished_at": None, "seconds": None},
    }
    for f in fields:
        result.setdefault(f, None)

    if case_obj is not None:
        result["sources"].append(
            {"id": "case", "type": "json", "ref": str(case_path),
             "description": "case metadata, constraints and material paths"}
        )
    if task_text is not None:
        result["sources"].append(
            {"id": "task", "type": "markdown", "ref": str(task_path),
             "description": "task instructions and format constraints"}
        )

    err = write_json(out_dir / "result.json", result)
    return result, err


# ---- best-effort comment extraction -------------------------------------
# Never fabricates IDs: if nothing matches, returns an empty list and the caller
# records a material gap; the agent fills the ledger by reading the material.

_PAT_COMMENT = [
    re.compile(r"^\s*[Rr]eviewer\s*[#]?\s*(\d+)\s*[:：.、\-—]\s*(.*)$"),
    re.compile(r"^\s*[Rr](\d+)\s*[:：.、\-—]\s*(.*)$"),
    re.compile(r"^\s*(?:Comment|Q)\s*(\d+)\s*[:：.、\-—]\s*(.*)$", re.IGNORECASE),
    re.compile(r"^\s*[\[【]?\s*(\d+)\s*[\]】]?\s*[:：.、\-—]\s*(.*)$"),
]


def parse_comment_blocks(text: str) -> list:
    """Return [{comment_id, raw_quote, source_line}] best-effort."""
    if not text:
        return []
    blocks = []
    current = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        matched = None
        for idx, pat in enumerate(_PAT_COMMENT):
            m = pat.match(line)
            if m:
                if idx in (0, 1):
                    num = m.group(1)
                    tag = "R" + num
                elif idx == 2:
                    num, tag = m.group(1), "Q" + m.group(1)
                else:
                    num, tag = m.group(1), "C" + m.group(1)
                matched = (tag, m.group(2))
                break
        if matched:
            if current is not None:
                blocks.append(current)
            current = {"comment_id": matched[0], "raw_quote": matched[1], "source_line": i + 1}
        elif current is not None and line.strip():
            current["raw_quote"] += "\n" + line
    if current is not None:
        blocks.append(current)
    for b in blocks:
        b["raw_quote"] = b["raw_quote"].strip()
    return blocks


def build_ledgers(comments_text, baseline_text, out_dir, work_dir) -> tuple:
    """Write the three ledger skeletons. Returns (ledgers_dict, errors_list)."""
    out_dir = Path(out_dir)
    work_dir = Path(work_dir)
    for d in (out_dir, work_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    blocks = parse_comment_blocks(comments_text or "")
    review_ledger = {
        "schema_version": "1.0",
        "generated_at": utcnow_iso(),
        "comments_ref": "materials/reviewer-comments.md",
        "baseline_ref": "materials/manuscript-baseline.md",
        "parse_note": (
            "best-effort structural extraction; agent must verify every raw_quote and fill "
            "type/severity/sub_requirements/decision/tone/refs/status"
        ),
        "comments": [
            {
                "comment_id": b["comment_id"],
                "raw_quote": b["raw_quote"],
                "source_line": b["source_line"],
                "type": "unknown",
                "severity": "unknown",
                "sub_requirements": [],
                "decision": "open",
                "tone": "clarify",
                "response_summary": "",
                "revision_refs": [],
                "evidence_refs": [],
                "status": "open",
            }
            for b in blocks
        ],
    }
    revision_map = {
        "schema_version": "1.0",
        "generated_at": utcnow_iso(),
        "baseline_version": "baseline-1",
        "revision_version": "revision-1",
        "changes": [],
    }
    evidence_ledger = {
        "schema_version": "1.0",
        "generated_at": utcnow_iso(),
        "evidence": [],
    }
    errs = []
    for name, obj in (
        ("review_ledger.json", review_ledger),
        ("revision_map.json", revision_map),
        ("evidence_ledger.json", evidence_ledger),
    ):
        e = write_json(out_dir / name, obj)
        if e:
            errs.append(e)
    return {"review_ledger": review_ledger, "revision_map": revision_map,
            "evidence_ledger": evidence_ledger}, errs


# ---- validation -----------------------------------------------------------

def validate_result(result_path, out_dir) -> list:
    """Return a list of problem strings (empty means the result is self-consistent)."""
    result, err = load_json(result_path)
    if err:
        return ["cannot read result.json: %s" % err]
    if not isinstance(result, dict):
        return ["result.json is not a JSON object"]
    problems = []
    if not result.get("case_id"):
        problems.append("missing case_id")
    for f in DEFAULT_CONTRACT_FIELDS:
        if f not in result:
            problems.append("missing output-contract field: %s" % f)
    for f in ("status", "sources", "uncertainty", "change_log"):
        if f not in result:
            problems.append("missing field: %s" % f)
    if result.get("status") not in (
        "in_progress", "complete", "partial", "failed", "insufficient_evidence", "input_error"
    ):
        problems.append("invalid status: %r" % result.get("status"))
    problems.extend(validate_cross_refs(out_dir))
    problems.extend(validate_response_letter(out_dir))
    return problems


def _load_ledger(out_dir, name):
    obj, err = load_json(Path(out_dir) / name)
    return obj, err


def validate_cross_refs(out_dir) -> list:
    problems = []
    rl, e1 = _load_ledger(out_dir, "review_ledger.json")
    rm, e2 = _load_ledger(out_dir, "revision_map.json")
    el, e3 = _load_ledger(out_dir, "evidence_ledger.json")
    for name, err in (("review_ledger.json", e1), ("revision_map.json", e2),
                      ("evidence_ledger.json", e3)):
        if err:
            problems.append("%s: %s" % (name, err))
    if e1 or e2 or e3:
        return problems

    comments = (rl or {}).get("comments", [])
    comment_ids = {c.get("comment_id") for c in comments if isinstance(c, dict)}
    for c in comments:
        cid = c.get("comment_id")
        if c.get("decision") in ("accept", "partial") and not (
            c.get("revision_refs") or c.get("evidence_refs")
        ):
            problems.append("%s: accepted/partial but no revision_refs or evidence_refs" % cid)
        if c.get("decision") in ("accept", "partial") and c.get("status") == "open":
            problems.append("%s: decision set but status still open" % cid)
        if c.get("decision") == "open" and (c.get("revision_refs") or c.get("evidence_refs")):
            problems.append("%s: decision open but refs already set" % cid)
        if c.get("decision") not in DECISION_OK + ("open",):
            problems.append("%s: invalid decision %r" % (cid, c.get("decision")))

    changes = (rm or {}).get("changes", [])
    change_ids = {ch.get("change_id") for ch in changes if isinstance(ch, dict)}
    for ch in changes:
        chid = ch.get("change_id")
        for cid in (ch.get("comment_ids") or []):
            if cid not in comment_ids:
                problems.append("change %s references unknown comment %s" % (chid, cid))
        if ch.get("status") == "done":
            if not ch.get("locator"):
                problems.append("change %s: done but missing locator" % chid)
            if not (ch.get("before_quote") or ch.get("after_quote")):
                problems.append("change %s: done but missing before/after quote" % chid)
        if ch.get("status") not in CHANGE_OK:
            problems.append("change %s: invalid status %r" % (chid, ch.get("status")))

    evidence = (el or {}).get("evidence", [])
    for ev in evidence:
        eid = ev.get("evidence_id")
        for cid in (ev.get("comment_ids") or []):
            if cid not in comment_ids:
                problems.append("evidence %s references unknown comment %s" % (eid, cid))
        if ev.get("status") == "not_possible" and not ev.get("fallback"):
            problems.append("evidence %s: not_possible but missing fallback" % eid)
        if ev.get("status") not in EVIDENCE_OK:
            problems.append("evidence %s: invalid status %r" % (eid, ev.get("status")))
    return problems


def validate_response_letter(out_dir) -> list:
    """Check every CHG-x / E-x referenced in the response letter is real and done/in_place."""
    problems = []
    resp = None
    for p in sorted(Path(out_dir).glob("*.md")):
        if ("response" in p.name.lower()) or ("reply" in p.name.lower()) or ("rebuttal" in p.name.lower()):
            resp = p
            break
    if resp is None:
        return problems
    text, err = read_text(resp)
    if err:
        return ["response letter unreadable: %s" % err]

    rm, e2 = _load_ledger(out_dir, "revision_map.json")
    el, e3 = _load_ledger(out_dir, "evidence_ledger.json")
    if e2 or e3:
        return problems
    changes = {(ch or {}).get("change_id"): ch for ch in (rm or {}).get("changes", [])}
    evidence = {(ev or {}).get("evidence_id"): ev for ev in (el or {}).get("evidence", [])}

    for ref in sorted(set(re.findall(r"CHG-\d+", text))):
        ch = changes.get(ref)
        if ch is None:
            problems.append("response letter references unknown change %s" % ref)
        elif ch.get("status") != "done":
            problems.append("response letter references %s but status=%s" % (ref, ch.get("status")))
    for ref in sorted(set(re.findall(r"\bE-\d+\b", text))):
        ev = evidence.get(ref)
        if ev is None:
            problems.append("response letter references unknown evidence %s" % ref)
        elif ev.get("status") not in ("in_place", "planned"):
            problems.append("response letter references %s but status=%s" % (ref, ev.get("status")))
    return problems
