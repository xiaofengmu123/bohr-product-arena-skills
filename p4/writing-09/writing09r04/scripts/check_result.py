#!/usr/bin/env python3
"""Validate result.json and run a built-in self-test.

Usage:
  python scripts/check_result.py --result /app/output/result.json \
      --summary /app/work/input_summary.json [--selftest]

Exit code:
  0 = no hard errors (warnings may remain; they are printed and actionable)
  1 = hard errors found (structure/contract/consistency violations)

Standard library only. The self-test is executed in-process (no subprocess
pipes), so it also runs under confined runtimes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import read_input  # noqa: E402
import build_result  # noqa: E402

ALLOWED_STATUS = build_result.ALLOWED_STATUS

# Words that require explicit evidence or removal. Occurrences are warnings,
# because the checker cannot read the evidence itself; the agent must either
# justify the word with a locator or delete it.
FORBIDDEN_WORDS = [
    "首次", "国际领先", "填补空白", "颠覆性", "重大突破", "世界首次",
    "全球首创", "国内外首创", "前所未有的", "里程碑式", "革命性",
    "first-ever", "world-leading", "breakthrough", "unprecedented",
    "revolutionary", "paradigm-shifting",
]


def read_json(path):
    """Return (obj, error); never raises."""
    if not os.path.isfile(path):
        return None, "file missing: %s" % path
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, "json parse failed: %s" % exc


def load_deliverable_text(result, output_dir):
    """Collect text from deliverable files for content checks. Never raises."""
    chunks = []
    deliverable = result.get("deliverable")
    if isinstance(deliverable, dict):
        files = deliverable.get("files") or []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = item.get("path") or ""
            if not path:
                continue
            full = path if os.path.isabs(path) else os.path.join(output_dir, path)
            if os.path.isfile(full):
                text, _enc, _err = read_input.read_text(full)
                chunks.append((path, text))
    return chunks


def check_forbidden_words(chunks):
    warnings = []
    for path, text in chunks:
        for word in FORBIDDEN_WORDS:
            count = text.count(word)
            if count:
                warnings.append({
                    "kind": "forbidden_word",
                    "file": path,
                    "word": word,
                    "count": count,
                    "note": "remove or attach an evidence locator+quote",
                })
    return warnings


def check_sources(result):
    """Every source/fact record should carry a locator; unverified claims must be flagged."""
    errors, warnings = [], []
    seen_verified_missing_quote = False
    for key in ("facts_sources", "sources"):
        for i, item in enumerate(result.get(key) or []):
            if not isinstance(item, dict):
                errors.append({"kind": "source_not_object", "field": key, "index": i})
                continue
            locator = item.get("locator") or item.get("source") or ""
            quote = item.get("quote") or ""
            status = item.get("status") or item.get("evidence_status") or "unverified"
            if not locator:
                errors.append({
                    "kind": "source_missing_locator", "field": key, "index": i,
                    "note": "each record needs locator (file:page/para) or explicit not_reported",
                })
                continue
            if status == "verified" and not quote:
                seen_verified_missing_quote = True
                errors.append({
                    "kind": "verified_missing_quote", "field": key, "index": i,
                    "note": "verified claims need a verbatim quote",
                })
    return errors, warnings


def run_checks(result, summary, output_dir):
    """Return a report dict; hard errors vs warnings are separated."""
    errors, warnings = [], []

    if not isinstance(result, dict):
        return {"errors": [{"kind": "result_not_object"}], "warnings": []}

    if not result.get("case_id"):
        errors.append({"kind": "missing_case_id"})
    if result.get("status") not in ALLOWED_STATUS:
        errors.append({"kind": "invalid_status", "value": result.get("status")})

    contract = summary.get("output_contract") or build_result.DEFAULT_CONTRACT
    if isinstance(contract, dict):
        for key in contract:
            if key not in result:
                errors.append({"kind": "missing_contract_field", "field": key})

    summary_case = summary.get("case_id") or ""
    if summary_case and result.get("case_id") and summary_case != result.get("case_id"):
        errors.append({
            "kind": "case_id_mismatch",
            "result": result.get("case_id"),
            "summary": summary_case,
        })

    for key in ("sources", "uncertainty", "pending_human", "limitations", "change_log"):
        if key not in result:
            errors.append({"kind": "missing_envelope_field", "field": key})
        elif not isinstance(result[key], list):
            errors.append({"kind": "envelope_field_not_list", "field": key})

    for entry in result.get("change_log") or []:
        if not isinstance(entry, dict) or not entry.get("ts"):
            warnings.append({"kind": "change_log_entry_missing_ts"})
            break

    src_err, src_warn = check_sources(result)
    errors.extend(src_err)
    warnings.extend(src_warn)

    chunks = load_deliverable_text(result, output_dir)
    warnings.extend(check_forbidden_words(chunks))

    # Explicit degradation/uncertainty presence: the variant's core requirement.
    limitations = result.get("limitations") or []
    uncertainty = result.get("uncertainty") or []
    if not isinstance(limitations, list) or not isinstance(uncertainty, list):
        errors.append({"kind": "limitations_or_uncertainty_not_list"})
    if result.get("status") == "complete" and (not limitations or not uncertainty):
        warnings.append({
            "kind": "complete_without_explicit_boundaries",
            "note": "complete results should still list limitations/uncertainty (can be empty only when genuinely none)",
        })

    return {"errors": errors, "warnings": warnings}


def selftest():
    """In-process self-test covering the documented failure modes."""
    tmp = tempfile.mkdtemp(prefix="rdw-selftest-")
    input_dir = os.path.join(tmp, "input")
    work_dir = os.path.join(tmp, "work")
    out_dir = os.path.join(tmp, "output")
    os.makedirs(input_dir)

    case = {"case_id": "selftest-case", "output_contract": build_result.DEFAULT_CONTRACT}
    case_path = os.path.join(input_dir, "case.json")
    with open(case_path, "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    task_path = os.path.join(input_dir, "task.md")
    with open(task_path, "w", encoding="utf-8") as fh:
        fh.write("# selftest task\n")

    # 1) read_input -> summary
    rc = read_input.main(["--case", case_path, "--task", task_path,
                          "--input-dir", input_dir, "--work", work_dir])
    assert rc == 0, "read_input selftest failed"
    summary_path = os.path.join(work_dir, "input_summary.json")
    summary_obj, err = read_json(summary_path)
    assert err is None and summary_obj.get("case_id") == "selftest-case"

    # 2) build skeleton (first honest partial)
    rc = build_result.main(["--summary", summary_path, "--output-dir", out_dir])
    assert rc == 0, "build_result selftest failed"
    result_path = os.path.join(out_dir, "result.json")
    result_obj, err = read_json(result_path)
    assert err is None
    report = run_checks(result_obj, summary_obj, out_dir)
    assert report["errors"] == [], report["errors"]

    # 3) empty input -> insufficient_evidence, still valid JSON
    empty_dir = os.path.join(tmp, "empty-input")
    os.makedirs(empty_dir)
    rc = read_input.main(["--case", os.path.join(empty_dir, "case.json"),
                          "--task", os.path.join(empty_dir, "task.md"),
                          "--input-dir", empty_dir, "--work", work_dir])
    assert rc == 0
    rc = build_result.main(["--summary", summary_path, "--output-dir", out_dir])
    assert rc == 0

    # 4) forbidden word triggers a warning, not a hard error
    manuscript = os.path.join(out_dir, "manuscript.md")
    with open(manuscript, "w", encoding="utf-8") as fh:
        fh.write("This is a 颠覆性 breakthrough claim.\n")
    build_result.main(["--summary", summary_path, "--output-dir", out_dir,
                       "--set", "deliverable.title=selftest",
                       "--set", 'deliverable.files=[{"path":"manuscript.md","kind":"markdown"}]'])
    result_obj, _err = read_json(result_path)
    report = run_checks(result_obj, summary_obj, out_dir)
    words = [w for w in report["warnings"] if w.get("kind") == "forbidden_word"]
    assert words, "forbidden word not detected"

    # 5) bad JSON case file degrades instead of crashing
    bad_case = os.path.join(tmp, "bad-case.json")
    with open(bad_case, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    rc = read_input.main(["--case", bad_case, "--task", task_path,
                          "--input-dir", input_dir, "--work", work_dir])
    assert rc == 0, "bad JSON did not degrade gracefully"

    return {"ok": True, "checked": ["read", "skeleton", "empty-input", "forbidden-word", "bad-json"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--summary", default="/app/work/input_summary.json")
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        report = selftest()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    result, rerr = read_json(a.result)
    if rerr:
        print(json.dumps({"ok": False, "errors": [{"kind": "result_read_failed", "note": rerr}]},
                         ensure_ascii=False, indent=2))
        return 1
    summary, _serr = read_json(a.summary)
    if not isinstance(summary, dict):
        summary = {}

    report = run_checks(result, summary, a.output_dir)
    report["ok"] = not report["errors"]
    report["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
