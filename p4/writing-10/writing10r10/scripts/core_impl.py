#!/usr/bin/env python3
"""Pure, side-effect-light logic for the review-response revision workflow.

This module deliberately keeps the "revision ledger" (revision_ledger.json) as
the single source of truth.  Every derived artifact (response letter, location
map, evidence ledger, auto-edited manuscript draft) is rendered from the
ledger, and every check runs against the ledger plus the actual manuscript
text.  Nothing here performs network access or invents facts.
"""

import datetime
import json
import os
import re

VALID_DECISIONS = {"accept", "partial_accept", "reject", "clarify", "appeal"}
VALID_TONES = {"accepting", "clarifying", "polite_rebuttal", "appeal"}
VALID_EVIDENCE_STATUS = {"provided", "not_provided", "waived", "pending"}
MAX_FILE_BYTES = 2 * 1024 * 1024


# --------------------------------------------------------------------------
# Robust file / text helpers
# --------------------------------------------------------------------------

def now_ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Return (text, warnings). Never raises on missing/bad files."""
    warnings = []
    if not path or not os.path.exists(path):
        return None, ["missing: %s" % (path or "<empty path>")]
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, ["stat failed %s: %s" % (path, exc)]
    if size > max_bytes:
        return None, ["skipped oversized file %s (%d > %d)" % (path, size, max_bytes)]
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, ["read failed %s: %s" % (path, exc)]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), warnings
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), warnings + ["decoded with replacement: %s" % path]


def load_json_robust(path):
    """Return a parsed object or None. Tolerates BOM and bad JSON."""
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def _norm(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


# --------------------------------------------------------------------------
# Input discovery
# --------------------------------------------------------------------------

def discover(input_dir, case_path=None, task_path=None):
    """Scan the input directory without assuming fixed names or keys."""
    d = {
        "input_dir": input_dir,
        "case_path": None,
        "task_path": None,
        "manuscript_path": None,
        "comments_path": None,
        "manuscript_text": "",
        "comments_text": "",
        "task_text": "",
        "case_id": None,
        "case_json": None,
        "others": [],
        "warnings": [],
    }
    if not os.path.isdir(input_dir):
        d["warnings"].append("input directory missing: %s" % input_dir)
        return d

    # Locate the case definition.
    if case_path and os.path.exists(case_path):
        d["case_path"] = case_path
    else:
        for name in ("case.json", "case.yaml", "input.json"):
            p = os.path.join(input_dir, name)
            if os.path.isfile(p):
                d["case_path"] = p
                break
    if d["case_path"]:
        data = load_json_robust(d["case_path"])
        if isinstance(data, dict):
            d["case_json"] = data
            d["case_id"] = data.get("case_id") or data.get("id") or data.get("challenge_id")
        else:
            d["warnings"].append("case file is not a JSON object: %s" % d["case_path"])

    # Locate task.md.
    if task_path and os.path.exists(task_path):
        d["task_path"] = task_path
    else:
        p = os.path.join(input_dir, "task.md")
        if os.path.isfile(p):
            d["task_path"] = p
    if d["task_path"]:
        d["task_text"], w = read_text(d["task_path"])
        d["warnings"] += w

    # Walk the input tree for materials, including a materials/ subdirectory.
    candidates = []
    for root, _dirs, files in os.walk(input_dir):
        for name in files:
            candidates.append(os.path.join(root, name))

    for full in candidates:
        low = os.path.basename(full).lower()
        if d["manuscript_path"] is None and re.search(r"manuscript|baseline|draft|paper", low):
            d["manuscript_path"] = full
        elif d["comments_path"] is None and re.search(r"comment|review|referee", low):
            d["comments_path"] = full
        else:
            d["others"].append(full)

    if d["manuscript_path"]:
        d["manuscript_text"], w = read_text(d["manuscript_path"])
        d["warnings"] += w
    if d["comments_path"]:
        d["comments_text"], w = read_text(d["comments_path"])
        d["warnings"] += w
    return d


# --------------------------------------------------------------------------
# Skeletons
# --------------------------------------------------------------------------

def result_skeleton(case_id):
    return {
        "case_id": case_id or "unknown",
        "status": "in_progress",
        "deliverable": {
            "primary": "revised_manuscript",
            "path": None,
            "format": "markdown",
            "title": None,
            "status": "pending",
            "artifacts": {
                "revised_manuscript": {"path": None, "format": "markdown", "title": None, "status": "pending"},
                "response_letter": {"path": None, "format": "markdown", "title": None, "status": "pending"},
                "revision_locations": {"path": None, "format": "markdown", "status": "pending"},
                "evidence_ledger": {"path": None, "format": "markdown", "status": "pending"},
            },
        },
        "fact_source_mapping": [],
        "version_boundary": {
            "changes": [],
            "pending_confirmation": [],
            "material_gaps": [],
            "anomalies": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": now_ts(), "finished_at": None, "requests_used": None, "seconds_used": None},
        "change_log": [],
    }


def ledger_skeleton(case_id):
    return {
        "case_id": case_id or "unknown",
        "manuscript_source": None,
        "comments_source": None,
        "comments": [],
    }


def minimal_result(case_id, error):
    r = result_skeleton(case_id)
    r["status"] = "input_error"
    r["errors"] = [str(error)]
    return r


def append_change(result, action, detail):
    result.setdefault("change_log", [])
    result["change_log"].append({"ts": now_ts(), "action": action, "detail": detail})


# --------------------------------------------------------------------------
# Comment splitting (best-effort seed; the agent refines the ledger)
# --------------------------------------------------------------------------

_COMMENT_HEADER = re.compile(
    r"^\s*(?:#+\s*)?(?:(?:Reviewer|Referee|Comment|Review|意见|问题)\s*[#:：]?\s*\d+\s*(?:[-:：.]\s*|$)|"
    r"\d+\s*[.、:：]\s+)",
    re.IGNORECASE,
)


def split_comments(text):
    """Seed the ledger with one entry per recognisable comment block."""
    if not text or not text.strip():
        return []
    blocks = []
    current = None
    for line in text.splitlines():
        if _COMMENT_HEADER.match(line):
            if current is not None:
                blocks.append(current)
            current = {"raw_text": line.strip()}
        elif current is None:
            if line.strip():
                current = {"raw_text": line.strip()}
        else:
            current["raw_text"] += "\n" + line
    if current is not None:
        blocks.append(current)

    out = []
    for idx, block in enumerate(blocks, 1):
        raw = block["raw_text"].strip()
        out.append({
            "id": "C%d" % idx,
            "ref": first_line(raw) or ("Comment %d" % idx),
            "raw_text": raw,
            "sub_requirements": [],
            "decision": None,
            "tone": None,
            "response": "",
            "rationale": "",
            "actions": [],
            "locations": [],
            "evidence": [],
        })
    return out


# --------------------------------------------------------------------------
# Checks (coverage -> alignment -> evidence -> tone)
# --------------------------------------------------------------------------

def check_coverage(comments):
    out = {"addressed": 0, "open": [], "uncovered_subrequirements": 0, "errors": [], "anomalies": []}
    if not comments:
        out["errors"].append("ledger contains no comments")
        return out
    for c in comments:
        if c.get("decision") in VALID_DECISIONS:
            out["addressed"] += 1
        else:
            out["open"].append(c.get("id"))
        for s in (c.get("sub_requirements") or []):
            if not s.get("disposition"):
                out["uncovered_subrequirements"] += 1
                out["anomalies"].append("%s.%s has no disposition" % (c.get("id"), s.get("id", "?")))
    return out


def check_alignment(comments, baseline_text, revised_text):
    base = _norm(baseline_text)
    rev = _norm(revised_text)
    out = {"verified": 0, "unverified": 0, "errors": [], "anomalies": []}
    for c in comments:
        for loc in c.get("locations", []):
            before = _norm(loc.get("before"))
            after = _norm(loc.get("after"))
            if not before and not after:
                continue
            in_base = before in base if before else True
            in_rev = after in rev if after else True
            if in_base and in_rev:
                loc["anchor_status"] = "verified"
                out["verified"] += 1
            else:
                loc["anchor_status"] = "unverified"
                out["unverified"] += 1
                msg = "%s location '%s...' -> " % (c.get("id"), before[:40])
                if not in_base:
                    msg += "before not found in baseline; "
                if not in_rev:
                    msg += "after not found in revised; "
                out["anomalies"].append(msg.rstrip("; "))
    return out


def check_evidence(comments):
    out = {"provided": 0, "pending": 0, "errors": [], "anomalies": []}
    for c in comments:
        for e in c.get("evidence", []):
            st = e.get("status")
            if st == "provided":
                out["provided"] += 1
            elif st in VALID_EVIDENCE_STATUS:
                out["pending"] += 1
            else:
                e["status"] = "pending"
                out["pending"] += 1
                out["anomalies"].append("%s evidence '%s' has no status" % (c.get("id"), (e.get("item") or "")[:40]))
    return out


def check_tone(comments):
    out = {"violations": 0, "errors": [], "anomalies": []}
    for c in comments:
        d = c.get("decision")
        t = c.get("tone")
        if d == "appeal" and t != "appeal":
            out["violations"] += 1
            out["anomalies"].append("%s decision=appeal requires tone=appeal, got %s" % (c.get("id"), t))
        if d is not None and t not in VALID_TONES:
            out["violations"] += 1
            out["anomalies"].append("%s has decision %s but no valid tone" % (c.get("id"), d))
    return out


def run_checks(ledger, baseline_text, revised_text):
    comments = ledger.get("comments", [])
    cov = check_coverage(comments)
    align = check_alignment(comments, baseline_text, revised_text)
    evi = check_evidence(comments)
    tone = check_tone(comments)
    summary = {
        "total_comments": len(comments),
        "addressed": cov["addressed"],
        "open_comments": cov["open"],
        "uncovered_subrequirements": cov["uncovered_subrequirements"],
        "locations_verified": align["verified"],
        "locations_unverified": align["unverified"],
        "evidence_provided": evi["provided"],
        "evidence_pending": evi["pending"],
        "tone_violations": tone["violations"],
    }
    return {
        "summary": summary,
        "errors": cov["errors"] + align["errors"] + evi["errors"] + tone["errors"],
        "anomalies": cov["anomalies"] + align["anomalies"] + evi["anomalies"] + tone["anomalies"],
    }


# --------------------------------------------------------------------------
# Deterministic edit application (first-cut revised manuscript)
# --------------------------------------------------------------------------

def apply_edits(baseline_text, ledger):
    text = baseline_text or ""
    applied = 0
    skipped = []
    for c in ledger.get("comments", []):
        for loc in c.get("locations", []):
            before = loc.get("before") or ""
            after = loc.get("after") or ""
            if not before:
                continue
            norm_before = _norm(before)
            haystack = _norm(text)
            if norm_before in haystack and text.find(before) != -1:
                text = text.replace(before, after, 1)
                applied += 1
            else:
                skipped.append("%s '%s'" % (c.get("id"), before[:40]))
    banner = [
        "# Revised manuscript (auto draft)",
        "",
        "Auto-generated by deterministically applying recorded before/after edits "
        "from revision_ledger.json. Review and finalise this file; do not submit "
        "unreviewed.",
        "",
        "Applied edits: %d | Skipped (anchor not found): %d" % (applied, len(skipped)),
        "",
    ]
    if skipped:
        banner += ["Skipped anchors:", ""]
        banner += ["- " + s for s in skipped]
        banner += [""]
    return "\n".join(banner) + "\n" + text


# --------------------------------------------------------------------------
# Rendering derived artifacts from the ledger
# --------------------------------------------------------------------------

def render_response_letter(ledger, case_id):
    lines = ["# Response letter (draft)", "", "Case: %s" % case_id, ""]
    for c in ledger.get("comments", []):
        cid = c.get("id")
        ref = c.get("ref") or cid
        lines += ["## %s — %s" % (cid, ref), ""]
        lines += ["- Decision: %s | Tone: %s" % (c.get("decision") or "pending", c.get("tone") or "pending"), ""]
        subs = c.get("sub_requirements") or []
        if subs:
            lines += ["Sub-requirements:"]
            for s in subs:
                lines.append("- %s [%s] %s — disposition: %s"
                             % (s.get("id", "?"), s.get("type", ""), s.get("text", ""), s.get("disposition") or "pending"))
            lines.append("")
        lines += ["Response:", c.get("response") or "(pending — to be written)", ""]
        for a in c.get("actions") or []:
            lines.append("- %s: %s" % (a.get("verb", ""), a.get("detail", "")))
        if c.get("actions"):
            lines.append("")
        for loc in c.get("locations") or []:
            lines.append("- Location %s: \"%s\" → \"%s\" [%s]"
                         % (loc.get("section", ""), loc.get("before", ""), loc.get("after", ""), loc.get("anchor_status", "")))
        if c.get("locations"):
            lines.append("")
        for e in c.get("evidence") or []:
            lines.append("- Evidence: %s — %s — %s (%s)"
                         % (e.get("item", ""), e.get("status", ""), e.get("source") or "", e.get("note") or ""))
        if c.get("evidence"):
            lines.append("")
    return "\n".join(lines)


def render_locations(ledger):
    lines = ["# Revision location map", "", "| Comment | Section | Before | After | Anchor |", "|-|-|-|-|-|"]
    for c in ledger.get("comments", []):
        for loc in c.get("locations") or []:
            before = (loc.get("before") or "").replace("|", "\\|").replace("\n", " ")
            after = (loc.get("after") or "").replace("|", "\\|").replace("\n", " ")
            lines.append("| %s | %s | %s | %s | %s |"
                         % (c.get("id"), (loc.get("section") or "").replace("|", "\\|"),
                            before, after, loc.get("anchor_status", "")))
    return "\n".join(lines)


def render_evidence(ledger):
    lines = ["# Evidence ledger", "", "| Comment | Item | Status | Source | Note |", "|-|-|-|-|-|"]
    for c in ledger.get("comments", []):
        for e in c.get("evidence") or []:
            lines.append("| %s | %s | %s | %s | %s |"
                         % (c.get("id"),
                            (e.get("item") or "").replace("|", "\\|"),
                            e.get("status", ""),
                            (e.get("source") or "").replace("|", "\\|"),
                            (e.get("note") or "").replace("|", "\\|")))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Fact-source mapping / sources / merge
# --------------------------------------------------------------------------

def build_sources(inputs):
    out = []
    for key, label, typ in (
        ("case_path", "case definition and constraints", "case_json"),
        ("task_path", "task description", "task_md"),
        ("manuscript_path", "manuscript baseline", "manuscript"),
        ("comments_path", "reviewer comments summary", "reviewer_comments"),
    ):
        p = inputs.get(key)
        if p:
            out.append({"id": key.split("_")[0], "type": typ, "ref": p, "description": label})
    return out


def build_fact_source_mapping(ledger):
    out = []
    for c in ledger.get("comments", []):
        cid = c.get("id")
        src = c.get("ref") or cid
        for loc in c.get("locations") or []:
            if loc.get("before") or loc.get("after"):
                out.append({
                    "claim": "Change in %s: '%s' -> '%s'" % (loc.get("section") or "unspecified",
                                                             loc.get("before", ""), loc.get("after", "")),
                    "source_ref": src,
                    "location": loc.get("section") or cid,
                    "status": "traced" if loc.get("anchor_status") == "verified" else "unverified",
                })
        for e in c.get("evidence") or []:
            out.append({
                "claim": e.get("item", ""),
                "source_ref": e.get("source") or src,
                "location": e.get("source") or cid,
                "status": "traced" if e.get("status") == "provided"
                          else ("unverified" if e.get("status") in ("not_provided", "pending") else e.get("status")),
            })
        if c.get("decision"):
            out.append({
                "claim": "Decision %s for %s: %s" % (c.get("decision"), cid, c.get("rationale", "")),
                "source_ref": src,
                "location": cid,
                "status": "traced",
            })
    return out


def compute_status(result, ledger, out_dir):
    comments = ledger.get("comments", [])
    has_manuscript = bool(ledger.get("manuscript_source"))
    fatal = any("no comments" not in e and "contains no comments" not in e for e in (result.get("errors") or []))
    if not comments and not has_manuscript:
        return "insufficient_evidence"
    if fatal:
        return "input_error"
    revised_path = os.path.join(out_dir, "revised_manuscript.md")
    response_path = os.path.join(out_dir, "response_letter.md")
    all_decided = bool(comments) and all(c.get("decision") in VALID_DECISIONS for c in comments)
    subs_done = all(
        s.get("disposition") for c in comments for s in (c.get("sub_requirements") or [])
    )
    if all_decided and subs_done and os.path.exists(revised_path) and os.path.exists(response_path):
        return "complete"
    return "partial"


def merge_result(result, ledger, out_dir, inputs):
    art = result.setdefault("deliverable", {}).setdefault("artifacts", {})
    files = {
        "revised_manuscript": ("revised_manuscript.md", "markdown"),
        "response_letter": ("response_letter.md", "markdown"),
        "revision_locations": ("revision_locations.md", "markdown"),
        "evidence_ledger": ("evidence_ledger.md", "markdown"),
    }
    primary_path = None
    for key, (fname, fmt) in files.items():
        p = os.path.join(out_dir, fname)
        status = "complete" if os.path.exists(p) else "pending"
        art[key] = {"path": p if os.path.exists(p) else None, "format": fmt, "status": status}
        if key == "revised_manuscript" and os.path.exists(p):
            primary_path = p
    result["deliverable"]["primary"] = "revised_manuscript"
    result["deliverable"]["path"] = primary_path
    result["deliverable"]["format"] = "markdown"
    result["deliverable"]["status"] = "complete" if primary_path else "pending"
    result["deliverable"]["title"] = inputs.get("case_id") or "Revised manuscript"

    # Preserve agent-added mappings, then append ledger-derived ones (deduped).
    existing = result.get("fact_source_mapping") or []
    seen = {e.get("claim") for e in existing if isinstance(e, dict)}
    for e in build_fact_source_mapping(ledger):
        if e.get("claim") not in seen:
            existing.append(e)
            seen.add(e.get("claim"))
    result["fact_source_mapping"] = existing

    result["sources"] = build_sources(inputs)
    result["version_boundary"].setdefault("changes", [])
    result["version_boundary"].setdefault("pending_confirmation", [])
    result["version_boundary"].setdefault("material_gaps", [])
    result["version_boundary"].setdefault("anomalies", [])
    if inputs.get("warnings"):
        result["degradations"] = inputs["warnings"]
    if inputs.get("material_missing"):
        result["version_boundary"]["material_gaps"] = inputs["material_missing"]

    result["status"] = compute_status(result, ledger, out_dir)
    append_change(result, "merge", "Merged ledger and inputs into result.json; status=%s" % result["status"])
    result["runtime"]["finished_at"] = now_ts()
    return result
