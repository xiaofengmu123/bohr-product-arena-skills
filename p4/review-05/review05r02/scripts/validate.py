#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the evidence-anchored academic audit skill.

Checks the contract and verifiability properties that the scoring rubric
rewards, then writes ``<work>/validate_report.json``:

- result.json parses and carries a non-empty case_id
- status is honest and inside the allowed set
- output_contract echoes every original key of /input/case.json
- sources / uncertainty / change_log are arrays
- every finding has the required fields, legal enums, and a quote that can be
  re-located verbatim inside the extracted corpus (normalized containment)
- coverage covers all ten facets with legal statuses
- basis never fabricates an authority: unavailable bases must be capped at
  minor/info and recorded for human review
- report.md exists and points back to result.json

Exit code equals the number of hard FAILs (capped at 255), so the agent can
detect and repair failures without parsing the JSON report by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import extract

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
FINDING_TYPES = {"fact_error", "insufficient_evidence", "norm_violation", "suggestion"}
SEVERITIES = {"critical", "major", "minor", "info"}
CATEGORIES = {
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "statistics", "method", "logic",
}
BASIS_KINDS = {
    "material_quote", "general_principle", "target_journal_guide",
    "dataset_license", "ethics_policy", "unavailable",
}
FACETS = [
    "academic_integrity", "citation", "privacy", "copyright", "ethics",
    "submission", "quality", "statistics", "method", "logic",
]
COVERAGE_STATUSES = {"checked_clean", "finding", "insufficient_evidence", "not_applicable", "not_checked"}


def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, path)


class Checker:
    def __init__(self):
        self.checks = []  # (level, name, ok, detail)

    def add(self, level, name, ok, detail=""):
        self.checks.append((level, name, bool(ok), detail))

    def hard_fails(self):
        return sum(1 for level, _n, ok, _d in self.checks if level == "FAIL" and not ok)

    def print_report(self):
        for level, name, ok, detail in self.checks:
            if ok:
                print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
            else:
                print(f"[{level}] {name}" + (f" — {detail}" if detail else ""))


def load_corpus(work: Path):
    path = work / "corpus.json"
    if not path.exists():
        return None
    obj, err = extract.read_json_robust(path)
    return obj


def _normpath(s):
    return str(s or "").replace("\\", "/")


def segment_index(corpus):
    """Map file -> list of (locator, normalized_text)."""
    index = {}
    for seg in corpus.get("segments", []):
        f = _normpath(seg.get("file", ""))
        index.setdefault(f, []).append((seg.get("locator", ""), seg.get("text", "")))
    return index


def quote_matches(qtext, segments, file_norm_cache=None):
    """Return (found, locators) for a quote inside one file's segments."""
    qn = extract.normalize(qtext)
    ql = extract.normalize_loose(qtext)
    if not qn:
        return False, []
    locs = []
    for locator, text in segments:
        tn = extract.normalize(text)
        if qn in tn:
            locs.append(locator)
            continue
        if ql and extract.normalize_loose(text) and ql in extract.normalize_loose(text):
            locs.append(locator)
    return bool(locs), locs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Self-check the audit deliverable")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--work", default=str(DEFAULT_WORK))
    args = parser.parse_args(argv)

    inp = Path(args.input)
    out = Path(args.output)
    work = Path(args.work)

    c = Checker()
    report = {"checks": []}

    result_path = out / "result.json"
    if not result_path.exists():
        c.add("FAIL", "result.json exists", False, str(result_path))
        c.print_report()
        atomic_write_json(work / "validate_report.json", {"hard_fails": c.hard_fails(), "checks": c.checks})
        sys.exit(min(255, c.hard_fails()))

    result, err = extract.read_json_robust(result_path)
    if result is None:
        c.add("FAIL", "result.json parses", False, err)
        c.print_report()
        atomic_write_json(work / "validate_report.json", {"hard_fails": c.hard_fails(), "checks": c.checks})
        sys.exit(min(255, c.hard_fails()))
    c.add("FAIL", "result.json parses", True)

    # case_id
    case_id = result.get("case_id")
    c.add("FAIL", "case_id non-empty", bool(isinstance(case_id, str) and case_id.strip()), repr(case_id))

    # status
    status = result.get("status")
    c.add("FAIL", "status in allowed set", status in ALLOWED_STATUS, repr(status))

    # output_contract echo
    case_obj = None
    case_item = next((it for it in extract.discover(inp) if it["role"] == "case"), None)
    if case_item:
        case_obj, _ = extract.read_json_robust(Path(case_item["path"]))
    if isinstance(case_obj, dict) and isinstance(case_obj.get("output_contract"), dict):
        contract = case_obj["output_contract"]
        echoed = result.get("output_contract")
        if isinstance(echoed, dict):
            missing = [k for k in contract.keys() if k not in echoed]
            c.add("FAIL", "output_contract echoes all top-level keys", not missing, ",".join(missing) if missing else "all present")
        else:
            c.add("FAIL", "output_contract echoes all top-level keys", False, "output_contract not a dict")
    else:
        c.add("PASS", "output_contract echo", True, "no output_contract in case.json")

    # arrays
    for key in ("sources", "uncertainty", "change_log"):
        val = result.get(key)
        c.add("FAIL", f"{key} is an array", isinstance(val, list), type(val).__name__ if not isinstance(val, list) else f"{len(val)} items")

    # findings schema + quote re-verification
    corpus = load_corpus(work)
    index = segment_index(corpus) if corpus else {}
    report_body = result.get("report", {})
    findings = report_body.get("findings", [])
    c.add("FAIL", "report.findings is a list", isinstance(findings, list), type(findings).__name__ if not isinstance(findings, list) else f"{len(findings)} findings")

    quote_pass = quote_fail = locator_pass = locator_fail = 0
    required = {"id", "type", "severity", "category", "title", "file", "locator", "quote", "evidence", "basis", "revision"}
    for i, f in enumerate(findings, 1):
        if not isinstance(f, dict):
            c.add("FAIL", f"finding #{i} is object", False)
            continue
        missing = sorted(k for k in required if k not in f)
        if missing:
            c.add("FAIL", f"finding #{i} has required fields", False, "missing " + ",".join(missing))
        else:
            c.add("PASS", f"finding #{i} ({f.get('id','?')}) has required fields", True)
        c.add("FAIL", f"finding #{i} type legal", f.get("type") in FINDING_TYPES, repr(f.get("type")))
        c.add("FAIL", f"finding #{i} severity legal", f.get("severity") in SEVERITIES, repr(f.get("severity")))
        c.add("FAIL", f"finding #{i} category legal", f.get("category") in CATEGORIES, repr(f.get("category")))

        qtext = f.get("quote", "")
        ffile = _normpath(f.get("file", ""))
        if not qtext:
            c.add("FAIL", f"finding #{i} quote non-empty", False)
            continue
        if ffile not in index:
            quote_fail += 1
            c.add("FAIL", f"finding #{i} quote relocates to source", False, f"file not in corpus: {ffile}")
            continue
        found, locs = quote_matches(qtext, index[ffile])
        if not found:
            quote_fail += 1
            c.add("FAIL", f"finding #{i} quote relocates to source", False, f"quote not found in {ffile}")
        else:
            quote_pass += 1
            c.add("PASS", f"finding #{i} quote relocates to source", True, "found in " + ",".join(locs[:3]))
            if f.get("locator") in locs:
                locator_pass += 1
                c.add("PASS", f"finding #{i} locator resolves", True, f["locator"])
            else:
                locator_fail += 1
                c.add("FAIL", f"finding #{i} locator resolves", False, f"expected one of {locs[:5]}, got {f.get('locator')!r}")

        # basis anti-fabrication
        basis = f.get("basis")
        if not isinstance(basis, dict):
            c.add("FAIL", f"finding #{i} basis is object", False)
        else:
            kind = basis.get("kind")
            c.add("FAIL", f"finding #{i} basis.kind legal", kind in BASIS_KINDS, repr(kind))
            if kind == "unavailable" and f.get("severity") in {"critical", "major"}:
                c.add("FAIL", f"finding #{i} unavailable basis capped", False, "unavailable basis must be minor/info")

    # coverage
    coverage = report_body.get("coverage", [])
    if isinstance(coverage, list):
        facets = [x.get("facet") for x in coverage if isinstance(x, dict)]
        missing_facets = [f for f in FACETS if f not in facets]
        c.add("FAIL", "coverage covers all ten facets", not missing_facets, ",".join(missing_facets) if missing_facets else "complete")
        bad = [x.get("facet") for x in coverage if isinstance(x, dict) and x.get("status") not in COVERAGE_STATUSES]
        c.add("FAIL", "coverage statuses legal", not bad, ",".join(bad) if bad else "all legal")
    else:
        c.add("FAIL", "coverage is a list", False)

    # status honesty: status=ok requires full coverage and no errors
    if status == "ok":
        if result.get("errors"):
            c.add("FAIL", "status ok requires no errors", False, "; ".join(result["errors"][:3]))
        if isinstance(coverage, list):
            incomplete = [x.get("facet") for x in coverage if isinstance(x, dict)
                          and x.get("status") not in {"checked_clean", "finding", "not_applicable"}]
            c.add("FAIL", "status ok requires full coverage", not incomplete,
                  ",".join(incomplete) if incomplete else "full")

    # report.md
    report_md = out / "report.md"
    if not report_md.exists():
        c.add("FAIL", "report.md exists", False, str(report_md))
    else:
        text = report_md.read_text(encoding="utf-8", errors="replace")
        c.add("FAIL", "report.md references result.json", "result.json" in text)

    c.print_report()
    n_fail = c.hard_fails()
    summary = {
        "hard_fails": n_fail,
        "quote_pass": quote_pass,
        "quote_fail": quote_fail,
        "locator_pass": locator_pass,
        "locator_fail": locator_fail,
        "checks": [{"level": l, "name": n, "ok": o, "detail": d} for l, n, o, d in c.checks],
    }
    atomic_write_json(work / "validate_report.json", summary)
    print(f"\nvalidate: {n_fail} hard FAIL(s), quote {quote_pass}/{quote_pass + quote_fail} verified")
    sys.exit(min(255, n_fail))


if __name__ == "__main__":
    main()
