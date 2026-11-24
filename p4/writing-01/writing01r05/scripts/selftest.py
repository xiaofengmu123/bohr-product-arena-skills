#!/usr/bin/env python3
"""Self-check for the fact-traceable academic writing skill.

Validates the final deliverable against the scoring dimensions:
  A  output contract completeness (result.json)
  B  factual traceability (evidence map, no dangling markers)
  C  citation correspondence (every [C:key] defined, no decoration)
  D  section completeness (manuscript has real body, not an outline)
  E  editability (Markdown preserved; markers intact)
  F  degradation honesty (errors/degradations/uncertainty present)

Exit code: 0 = usable deliverable, 2 = result.json missing/invalid.
Warnings do not abort; they are the Agent's action list before finalizing.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from main import (  # noqa: E402
    read_json_safe, read_text, extract_contract, contract_defaults,
    find_case_file, case_id_from, now_iso, flatten_paths,
)
from evidence import analyze, _mat_keys  # noqa: E402

HEADINGS = re.compile(r"^#{1,6}\s+.+", re.MULTILINE)


def _all_contract_paths(spec):
    """Return the set of dotted paths the contract requires."""
    defaults = contract_defaults(spec) or {}
    if isinstance(defaults, dict):
        return set(p for p, _ in flatten_paths(defaults))
    return set()


def _path_exists(obj, path):
    """True if the nested dotted path exists, regardless of its value."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return True


def _has_value_at(obj, path):
    """True if the nested path exists with a non-null value."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return cur is not None


def main(argv=None):
    ap = argparse.ArgumentParser(description="self-check skill deliverable")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    checks = []   # (dimension, item, ok, detail)
    def add(dim, item, ok, detail=""):
        checks.append({"dimension": dim, "item": item, "ok": bool(ok), "detail": detail})

    rp = os.path.join(args.output, "result.json")
    mp = os.path.join(args.output, "manuscript.md")
    ip = os.path.join(args.work, "material_index.json")

    result, rerr = read_json_safe(rp)
    if result is None or not isinstance(result, dict):
        print(json.dumps({"ok": False, "fatal": "result.json missing or invalid: %s" % rerr,
                          "checks": checks}, ensure_ascii=False, indent=2))
        return 2

    case_path = find_case_file(args.input)
    case, _ = (read_json_safe(case_path) if case_path else (None, None))
    contract = extract_contract(case)
    expected_case = case_id_from(case, case_path or args.input)

    # A. contract completeness
    add("A", "result.json valid JSON", True)
    add("A", "case_id present", "case_id" in result and bool(result.get("case_id")),
        "case_id=%s" % result.get("case_id"))
    add("A", "case_id matches input", result.get("case_id") == expected_case,
        "expected=%s" % expected_case)
    contract_paths = _all_contract_paths(contract)
    absent = [p for p in contract_paths if not _path_exists(result, p)]
    add("A", "output_contract keys present (verbatim)", not absent,
        "absent=%s" % (absent or "none"))
    null_paths = [p for p in contract_paths
                  if _path_exists(result, p) and not _has_value_at(result, p)]
    if result.get("status") == "complete":
        add("A", "output_contract keys filled (complete requires non-null)",
            not null_paths, "null=%s" % (null_paths or "none"))
    else:
        add("A", "null contract fields allowed (honest partial)",
            True, "null=%s" % (null_paths or "none"))
    for std in ("status", "sources", "uncertainty", "pending_human",
                "change_log", "errors", "degradations"):
        add("A", "standard field %s" % std, std in result, "")
    add("A", "status honest (not complete w/o deliverable)",
        result.get("status") in ("in_progress", "partial", "complete", "failed"),
        "status=%s" % result.get("status"))
    add("A", "change_log has entries",
        isinstance(result.get("change_log"), list) and len(result["change_log"]) > 0,
        "entries=%d" % (len(result.get("change_log", []))))

    # B / C. evidence & citations
    text, terr = read_text(mp)
    if text is None:
        add("B", "manuscript.md readable", False, "err=%s" % terr)
        a = {"dangling_sources": [], "dangling_citations": [],
             "unused_citations": [], "bare_numeric_claims": []}
    else:
        add("B", "manuscript.md readable", True, "chars=%d" % len(text))
        a = analyze(text, result, _mat_keys(ip))
        add("B", "no dangling [S:key]", not a["dangling_sources"],
            "dangling=%s" % a["dangling_sources"])
        add("B", "no unannotated numeric claims", not a["bare_numeric_claims"],
            "count=%d" % len(a["bare_numeric_claims"]))
        add("C", "no dangling [C:key]", not a["dangling_citations"],
            "dangling=%s" % a["dangling_citations"])
        add("C", "no unused [C:key] definitions", not a["unused_citations"],
            "unused=%s" % a["unused_citations"])

        # D / E. real manuscript body, not an outline
        headings = HEADINGS.findall(text)
        add("D", "has section headings", len(headings) >= 2, "headings=%d" % len(headings))
        body_words = len(re.sub(r"[#>*`|\[\]]+", " ", text).split())
        add("D", "has substantive body (>=400 words)", body_words >= 400,
            "words=%d" % body_words)
        has_markers = ("[S:" in text) or ("[C:" in text)
        add("E", "Markdown preserved + evidence markers", has_markers,
            "has_markers=%s" % has_markers)

    # F. degradation honesty
    flagged = (len(result.get("uncertainty", [])) or len(result.get("pending_human", []))
               or len(result.get("errors", [])) or len(result.get("degradations", [])))
    status = result.get("status")
    add("F", "limits recorded when status is partial",
        (status != "partial") or bool(flagged),
        "flagged=%s status=%s" % (flagged, status))

    ok_all = all(c["ok"] for c in checks)
    summary = {"ok": ok_all, "ts": now_iso(), "result": rp,
               "status": result.get("status"), "checks": checks,
               "warnings": [c for c in checks if not c["ok"]]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
