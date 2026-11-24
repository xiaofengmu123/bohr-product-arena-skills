#!/usr/bin/env python3
"""Self-check for the grant-writing Skill output contract.

Checks:
  - result.json exists and is valid JSON;
  - case_id, status present and status in allowed set;
  - every declared output_contract key is present (default: artifact,
    fact_source_map, version_boundary) plus appended fields;
  - artifact.path points to an existing editable file;
  - every verified/paraphrased fact has locator+quote, not_reported has no
    quote, conflict has a note;
  - seven elements coverage and objective/content/route/indicator alignment;
  - source manifest includes SHA256 where present.

Usage:
  python3 scripts/selftest.py [--case /input/case.json] [--out /app/output]
                              [--work /app/work]

Exit code 0 = passed, 1 = failed (prints JSON report). Pure standard library.
"""
import argparse
import json
import os
import sys

ALLOWED_STATUS = {"in_progress", "done", "partial", "failed",
                  "insufficient_evidence", "input_error"}
DEFAULT_CONTRACT = ["artifact", "fact_source_map", "version_boundary"]
SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容", "技术路线",
    "创新点", "研究基础与可行性",
]
APPENDED = ["status", "sources", "uncertainty", "limitations",
            "pending_human", "change_log"]


def load_json(path):
    if not os.path.isfile(path):
        return None, "missing"
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return None, str(e)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--work", default=None)
    a = ap.parse_args(argv)

    out_dir = a.out or ("/app/output" if os.path.isdir("/app") else "output")
    work_dir = a.work or ("/app/work" if os.path.isdir("/app") else "work")

    issues = []
    result_path = os.path.join(out_dir, "result.json")
    result, err = load_json(result_path)
    if result is None:
        print(json.dumps({"ok": False, "issues": ["result.json: %s" % err]},
                         ensure_ascii=False, indent=2))
        return 1

    if not result.get("case_id"):
        issues.append("missing case_id")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        issues.append("invalid or missing status: %r" % status)

    # Contract fields (echo defaults + any declared contract).
    case, _ = load_json(a.case or os.path.join(
        "/input" if os.path.isdir("/input") else "input", "case.json"))
    contract_keys = list(DEFAULT_CONTRACT)
    if case:
        for key in ("output_contract", "output_schema", "required_fields"):
            val = case.get(key)
            if isinstance(val, dict):
                contract_keys = list(val.keys()) or contract_keys
                break
            if isinstance(val, list):
                contract_keys = [str(k) for k in val if str(k)] or contract_keys
                break
    for k in contract_keys:
        if k not in result:
            issues.append("missing contract field: %s" % k)
    for k in APPENDED:
        if k not in result:
            issues.append("missing appended field: %s" % k)

    # Artifact existence.
    art = result.get("artifact") or {}
    if art.get("path"):
        if not os.path.isfile(art["path"]):
            issues.append("artifact.path not a file: %s" % art["path"])
    else:
        issues.append("artifact.path empty")

    # Fact traceability.
    facts = result.get("fact_source_map") or []
    for i, c in enumerate(facts):
        st = c.get("ev_status", "")
        if st in ("verified", "paraphrased"):
            if not (c.get("locator") and c.get("quote")):
                issues.append("fact[%d] %s missing locator/quote" % (i, st))
        if st == "not_reported" and c.get("quote"):
            issues.append("fact[%d] not_reported has quote" % i)
        if st == "conflict" and not c.get("note"):
            issues.append("fact[%d] conflict without note" % i)
        if st not in {"verified", "paraphrased", "inferred",
                      "not_reported", "conflict"}:
            issues.append("fact[%d] bad ev_status %r" % (i, st))

    # Seven elements.
    seven = result.get("seven_elements") or {}
    covered = seven.get("covered") or {}
    missing_elems = [e for e in SEVEN_ELEMENTS if not covered.get(e)]
    if missing_elems:
        issues.append("uncovered elements: %s" % ", ".join(missing_elems))
    cons = seven.get("consistency") or {}
    counts = [cons.get("objective_count"), cons.get("content_count"),
              cons.get("route_count"), cons.get("indicator_count")]
    if any(isinstance(x, int) for x in counts) and len(set(x for x in counts if x is not None)) > 1:
        issues.append("目标/内容/路线/指标数量不一致: %s" % counts)

    # Sources SHA256.
    sources = result.get("sources") or []
    for s in sources:
        if s.get("path") and not s.get("sha256"):
            issues.append("source missing sha256: %s" % s.get("path"))

    report = {"ok": not issues, "issues": issues,
              "status": status, "case_id": result.get("case_id"),
              "artifact": art.get("path"),
              "fact_count": len(facts),
              "uncovered_elements": missing_elems}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
