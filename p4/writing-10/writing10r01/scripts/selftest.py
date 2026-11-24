#!/usr/bin/env python3
"""selftest.py — deterministic self-test for a revision package.

Checks the four scored questions mechanically where possible:
  (A) 答得全 — every reviewer comment and its sub-requirements are registered;
  (B) 改得准 — accepted/partial-accept decisions have a landed change with
      locator + verbatim after-quote that resolves inside the manuscript;
  (C) 证得住 — fact_source_mapping entries carry a verbatim quote;
  (D) 对得上 — deliverable files referenced actually exist on disk.

Prints a JSON report; always exits 0 so a degraded report is still readable.
The hard pass/fail schema gate is validate_output.py.
"""
import argparse
import json
import os
import re
import sys

MAX_FILE = 2_000_000


def read_text(path):
    if not os.path.exists(path):
        return None
    try:
        if os.path.getsize(path) > MAX_FILE:
            return None
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeError, OSError):
            continue
    return None


def load_json(path):
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def find_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            out.append(os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/"))
    return sorted(out)


def resolve(ref, roots):
    """Resolve a source_ref/locator to an existing file, stripping '#...' anchors."""
    path = ref.split("#", 1)[0] if ref else ""
    if not path:
        return None
    for root in roots:
        cand = os.path.join(root, path)
        if os.path.isfile(cand):
            return cand
    if os.path.isfile(path):
        return path
    return None


def comment_markers(text):
    """Heuristic reviewer-comment IDs: R1/Q1/#1/Comment 1/意见1/1. /1)."""
    if not text:
        return []
    pats = [
        re.compile(r"\b[Rr]\s*(\d{1,2})\b"),
        re.compile(r"\b[Qq]\s*(\d{1,2})\b"),
        re.compile(r"(?:^|\n)\s*#\s*(\d{1,2})\b", re.M),
        re.compile(r"(?:^|\n)\s*Comment\s*(\d{1,2})\b", re.I | re.M),
        re.compile(r"(?:^|\n)\s*(?:意见|审稿意见|评审意见|评论)\s*(\d{1,2})\b", re.M),
        re.compile(r"(?:^|\n)\s*(\d{1,2})\s*[.)、:：]\s+\S", re.M),
    ]
    ids = []
    for pat in pats:
        for match in pat.finditer(text):
            ids.append(match.group(1))
    return sorted(set(ids))


def norm_cid(cid):
    return re.sub(r"\D", "", str(cid)) if cid is not None else ""


def main():
    ap = argparse.ArgumentParser(description="Self-test a revision package.")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    args = ap.parse_args()

    result = load_json(args.result)
    report = {"ok": False, "checks": [], "summary": {}}
    if not isinstance(result, dict):
        report["error"] = "result.json missing or invalid"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    roots = [
        args.input,
        os.path.dirname(os.path.abspath(args.result)),
        os.path.dirname(os.path.abspath(args.case)),
    ]
    roots = [r for r in roots if r]

    def add(name, passed, detail):
        report["checks"].append({"check": name, "passed": bool(passed), "detail": detail})

    # (A) completeness: comment_registry covers reviewer comments
    registry = result.get("comment_registry") or []
    comments_file = None
    for filename in find_files(args.input):
        lower = filename.lower()
        if "reviewer-comment" in lower or "reviewer_comment" in lower or "comments" in lower:
            comments_file = filename
            break
    comments_text = read_text(os.path.join(args.input, comments_file)) if comments_file else None
    markers = comment_markers(comments_text)
    reg_ids = {norm_cid(c.get("comment_id")) for c in registry if isinstance(c, dict)}
    missing = [m for m in markers if norm_cid(m) and norm_cid(m) not in reg_ids]
    add("A_completeness_comment_coverage", not missing,
        "markers=%s registry=%s missing=%s" % (markers, sorted(reg_ids), missing))

    bad_entries = []
    for c in registry:
        if not isinstance(c, dict):
            bad_entries.append("non_dict")
            continue
        if not c.get("comment_id") or not c.get("decision"):
            bad_entries.append(str(c.get("comment_id")))
        subs = c.get("sub_requirements")
        if not isinstance(subs, list) or not subs:
            bad_entries.append(str(c.get("comment_id")) + ":no_sub_requirements")
    add("A_completeness_sub_requirements", not bad_entries, "bad=%s" % bad_entries[:10])

    # (B) landability: accepted/partial decisions have evidence with locator + quote
    evidence = result.get("evidence_map") or []
    accepted = [c for c in registry
                if isinstance(c, dict) and c.get("decision") in ("accept", "partial-accept")]
    ev_ids = {e.get("related_comment_id") for e in evidence if isinstance(e, dict)}
    unlanded = [c.get("comment_id") for c in accepted if c.get("comment_id") not in ev_ids]
    add("B_landed_changes_have_evidence", not unlanded, "unlanded=%s" % unlanded)

    unresolved_quotes = []
    for e in evidence:
        if not isinstance(e, dict):
            continue
        loc = e.get("locator") or ""
        quote = e.get("quote_after") or e.get("quote") or ""
        if not loc or not quote:
            unresolved_quotes.append(e.get("change_id") or e.get("claim_id") or "?")
            continue
        target = resolve(loc, roots)
        if not target:
            unresolved_quotes.append(str(e.get("change_id") or "?") + ":locator_missing")
            continue
        text = read_text(target) or ""
        if quote and quote not in text:
            unresolved_quotes.append(str(e.get("change_id") or "?") + ":quote_not_found")
    add("B_locator_and_quote_resolve", not unresolved_quotes,
        "bad=%s" % unresolved_quotes[:10])

    # (C) fact_source_mapping entries carry a verbatim quote
    fsm = result.get("fact_source_mapping") or []
    no_quote = []
    for item in fsm:
        if not isinstance(item, dict):
            no_quote.append("non_dict")
        elif not item.get("quote"):
            no_quote.append(item.get("claim_id") or item.get("claim") or "?")
    add("C_fact_quotes_present", not no_quote, "no_quote=%s" % no_quote[:10])

    # (D) deliverable files exist
    deliverable = result.get("deliverable") or {}
    missing_files = []
    for key in ("path", "manuscript_path"):
        p = deliverable.get(key)
        if p and not os.path.isfile(p):
            missing_files.append(str(p))
    add("D_deliverable_files_exist", not missing_files, "missing=%s" % missing_files)

    passed = sum(1 for c in report["checks"] if c["passed"])
    report["ok"] = passed == len(report["checks"])
    report["summary"] = {
        "passed": passed,
        "total": len(report["checks"]),
        "comments_found": len(markers),
        "registry": len(registry),
        "evidence_entries": len(evidence),
        "fsm_entries": len(fsm),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
