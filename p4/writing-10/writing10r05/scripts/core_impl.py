#!/usr/bin/env python3
"""Verify the revision deliverables against the output contract and each other.

Checks (a) result.json has every output-contract field with the right shape,
(b) the revision ledger is internally consistent, (c) every claimed modification
is actually present in the revised manuscript ("改得准/对得上"), and (d) the
response letter and ledger share the same comment ids ("答得全"). Never fabricates
content; missing artifacts are reported as gaps, not errors that crash the run.

Exit code 0 means the result is structurally valid (even if partial); exit code 1
means the result.json itself is missing or unparseable.
"""
import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Output contract: tiers reflect the grading contract.
#   required   = 题目 output_contract + harness 必需字段，缺一即 issue
#   recommended= 提升可复核性的字段，缺了给 warning
# ---------------------------------------------------------------------------
OUTPUT_CONTRACT = {
    "required": {
        "case_id": {"type": "str"},
        "deliverable": {
            "type": "dict",
            "required_keys": ["revised_manuscript", "response_letter",
                              "modification_map", "evidence_ledger", "format", "status"],
        },
        "fact_source_mapping": {"type": "list", "item_keys": ["claim", "source_ref", "location", "status"]},
        "version_boundary": {
            "type": "dict",
            "required_keys": ["changes", "pending_confirmation", "material_gaps", "anomalies"],
        },
        "status": {"type": "str", "enum": ["in_progress", "complete", "partial", "failed"]},
        "sources": {"type": "list", "item_keys": ["id", "type", "ref", "description"]},
        "uncertainty": {"type": "list", "item_keys": ["item", "reason", "disposition"]},
        "change_log": {"type": "list", "item_keys": ["ts", "action", "detail"]},
    },
    "recommended": {
        "pending_human": {"type": "list", "item_keys": ["item", "reason"]},
        "runtime": {"type": "dict"},
    },
}

DECISIONS = {"accept", "partial", "decline", "clarify", "rebut"}
TONES = {"accept", "clarify", "polite_rebuttal", "appeal"}
EVIDENCE_STATUS = {"in_place", "to_add", "not_feasible", "pending"}
MOD_STATUS = {"done", "pending"}
FACT_STATUS = {"traced", "assumption", "unverified"}


def type_of(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, str):
        return "str"
    if isinstance(v, (int, float)):
        return "num"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    if v is None:
        return "null"
    return type(v).__name__


def check_scalar_type(key, expect, value):
    mapping = {"str": "str", "list": "list", "dict": "dict"}
    if expect in mapping and type_of(value) == mapping[expect]:
        return None
    if expect == "str" and type_of(value) == "str":
        return None
    return "field %s: expected %s, got %s" % (key, expect, type_of(value))


def validate_contract(result):
    """Return (issues, warnings). Each issue is a human-readable string."""
    issues, warnings = [], []
    if not isinstance(result, dict):
        return ["result.json root is not an object"], []
    for key, spec in OUTPUT_CONTRACT["required"].items():
        if key not in result:
            issues.append("missing required field: %s" % key)
            continue
        val = result[key]
        if "enum" in spec and val not in spec["enum"]:
            issues.append("field %s: %r not in %s" % (key, val, sorted(spec["enum"])))
        bad = check_scalar_type(key, spec.get("type"), val)
        if bad:
            issues.append(bad)
            continue
        if spec.get("type") == "dict" and "required_keys" in spec:
            for k in spec["required_keys"]:
                if k not in val:
                    issues.append("field %s missing sub-key: %s" % (key, k))
        if spec.get("type") == "list":
            for i, item in enumerate(val):
                if not isinstance(item, dict):
                    issues.append("field %s[%d] is not an object" % (key, i))
                    continue
                for k in spec.get("item_keys", []):
                    if k not in item:
                        issues.append("field %s[%d] missing key: %s" % (key, i, k))
    for key, spec in OUTPUT_CONTRACT["recommended"].items():
        if key not in result:
            warnings.append("recommended field absent: %s" % key)
    return issues, warnings


def read_text(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def read_json(path):
    if not os.path.exists(path):
        return None, "missing"
    text = read_text(path)
    if text is None:
        return None, "unreadable"
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError as exc:
        return None, "bad JSON: %s" % exc


def check_anchor_in_manuscript(anchor, manuscript_text):
    """Verify a modification anchor against the manuscript body.

    anchor forms:
      "old→new" (or "old => new")   -> new present, old absent
      "literal text"                -> present
    Returns (bool, reason). Never treats a missing anchor as a content error.
    """
    if not manuscript_text:
        return False, "manuscript not readable"
    anchor = (anchor or "").strip()
    if not anchor:
        return False, "empty anchor"
    m = re.split(r"\s*(?:→|=>|->)\s*", anchor, maxsplit=1)
    if len(m) == 2 and m[0].strip() and m[1].strip():
        old, new = m[0].strip(), m[1].strip()
        has_new = new in manuscript_text
        has_old = old in manuscript_text
        if has_new and not has_old:
            return True, "new present, old absent"
        if has_new and has_old:
            return False, "old text still present"
        return False, "new text not found"
    if anchor in manuscript_text:
        return True, "literal anchor present"
    return False, "anchor text not found"


def extract_comment_ids(text):
    """Best-effort comment-id discovery; heuristic, never treated as ground truth."""
    if not text:
        return set()
    ids = set(re.findall(r"(?:意见|评论|Comment|Reviewer|R|#)\s*[#:]?\s*([0-9]+)", text, re.I))
    ids |= set(re.findall(r"^\s*([0-9]+)\s*[\.、\)]\s", text, re.M))
    return {i for i in ids if i}


def verify_ledger(ledger, manuscript_text, response_text, comments_text):
    """Return (report dict) with coverage, claims, gaps."""
    report = {"entries": 0, "covered_comment_ids": [], "claim_checks": [], "gaps": []}
    comments = ledger.get("comments", []) if isinstance(ledger, dict) else []
    report["entries"] = len(comments)
    for c in comments:
        if not isinstance(c, dict):
            report["gaps"].append("ledger entry not an object")
            continue
        cid = c.get("comment_id", "<no-id>")
        if not cid or cid == "<no-id>":
            report["gaps"].append("entry missing comment_id")
            continue
        report["covered_comment_ids"].append(str(cid))
        if c.get("decision") not in DECISIONS:
            report["gaps"].append("comment %s: decision %r not in %s"
                                  % (cid, c.get("decision"), sorted(DECISIONS)))
        if c.get("tone") not in TONES:
            report["gaps"].append("comment %s: tone %r not in %s"
                                  % (cid, c.get("tone"), sorted(TONES)))
        mods = c.get("modifications", []) or []
        evids = c.get("evidence", []) or []
        if not mods and c.get("decision") in ("accept", "partial"):
            report["gaps"].append("comment %s: accepted/partial but no modifications listed" % cid)
        for m in mods:
            if not isinstance(m, dict):
                report["gaps"].append("comment %s: modification not an object" % cid)
                continue
            if m.get("status") not in MOD_STATUS:
                report["gaps"].append("comment %s: mod status %r invalid" % (cid, m.get("status")))
            if m.get("status") == "done":
                ok, reason = check_anchor_in_manuscript(m.get("anchor"), manuscript_text)
                report["claim_checks"].append({
                    "comment_id": cid, "anchor": m.get("anchor"),
                    "location": m.get("location"), "found": ok, "reason": reason})
                if not ok:
                    report["gaps"].append(
                        "comment %s: claimed modification not found in manuscript (%s)" % (cid, reason))
        for e in evids:
            if isinstance(e, dict) and e.get("status") not in EVIDENCE_STATUS:
                report["gaps"].append("comment %s: evidence status %r invalid"
                                      % (cid, e.get("status")))
    # response letter must mention every covered comment id (best-effort, substring match
    # to tolerate "R1"/"意见 1"/"Comment 1" reference styles)
    if response_text:
        for cid in report["covered_comment_ids"]:
            if str(cid) not in response_text:
                report["gaps"].append("comment %s not referenced in response letter" % cid)
    # coverage heuristic vs source comments file
    if comments_text:
        src_ids = extract_comment_ids(comments_text)
        missing = sorted(src_ids - set(report["covered_comment_ids"]), key=int) if src_ids else []
        if missing:
            report["gaps"].append("source comments possibly unmatched (heuristic): %s"
                                  % ", ".join(missing))
    report["coverage"] = {
        "ledger_entries": report["entries"],
        "claims_checked": len(report["claim_checks"]),
        "claims_found": sum(1 for c in report["claim_checks"] if c["found"]),
        "gaps": len(report["gaps"]),
    }
    return report


def find_file_by_name(root, name):
    """Return the first file named `name` under root, or None. Never assumes paths."""
    if not root or not os.path.isdir(root):
        return None
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f == name:
                return os.path.join(dirpath, f)
    return None


def deliverable_path(result, key, default):
    try:
        p = result.get("deliverable", {}).get(key, {}).get("path")
        return p if p else default
    except AttributeError:
        return default


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify revision output contract + cross-consistency")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--case", default="/input/case.json")
    a = ap.parse_args(argv)

    result, state = read_json(a.result)
    if result is None:
        print(json.dumps({"ok": False, "fatal": "result.json %s" % state,
                          "issues": ["result.json unreadable or bad JSON"]},
                         ensure_ascii=False, indent=2))
        return 1

    issues, warnings = validate_contract(result)

    manuscript_text = read_text(deliverable_path(
        result, "revised_manuscript", os.path.join(a.out, "revised_manuscript.md")))
    response_text = read_text(deliverable_path(
        result, "response_letter", os.path.join(a.out, "response_letter.md")))
    input_dir = os.path.dirname(os.path.abspath(a.case))
    comments_path = find_file_by_name(input_dir, "reviewer-comments.md") or \
        os.path.join(a.out, "reviewer-comments.md")
    comments_text = read_text(comments_path)
    ledger, _ = read_json(os.path.join(a.out, "revision_ledger.json"))

    report = verify_ledger(ledger or {"comments": []}, manuscript_text, response_text, comments_text)

    ok = len(issues) == 0
    print(json.dumps({
        "ok": ok,
        "contract_issues": issues,
        "contract_warnings": warnings,
        "ledger_report": report,
        "status": result.get("status"),
        "limits": result.get("runtime", {}).get("limits", ""),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
