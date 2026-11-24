#!/usr/bin/env python3
"""Self-test for the revision skill: robustness + contract compliance.

Scenarios (all standard library, temporary directories only):
  1. empty input dir   -> scaffold still writes a valid partial result.json
  2. bad JSON case     -> degrades to case_id=unknown + anomaly, exit 0
  3. minimal valid run -> scaffold + check_revision, one anchored claim found
  4. broken contract   -> validator flags missing required fields

Exit 0 = all scenarios pass; exit 1 = at least one failed. Prints PASS/FAIL lines.
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import scaffold  # noqa: E402
import check_revision  # noqa: E402


def scenario_empty(root):
    inp = os.path.join(root, "empty", "input")
    out = os.path.join(root, "empty", "output")
    work = os.path.join(root, "empty", "work")
    os.makedirs(inp, exist_ok=True)
    scaffold.main(["--case", os.path.join(inp, "case.json"),
                   "--task", os.path.join(inp, "task.md"),
                   "--out", out, "--work", work])
    result, _ = check_revision.read_json(os.path.join(out, "result.json"))
    ok = result is not None and result.get("status") == "partial"
    gaps = (result or {}).get("version_boundary", {}).get("material_gaps", [])
    return ok and bool(gaps), "status=partial with material_gaps=%d" % len(gaps)


def scenario_bad_json(root):
    inp = os.path.join(root, "bad", "input")
    out = os.path.join(root, "bad", "output")
    work = os.path.join(root, "bad", "work")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ")
    rc = scaffold.main(["--case", os.path.join(inp, "case.json"),
                        "--task", os.path.join(inp, "task.md"),
                        "--out", out, "--work", work])
    result, _ = check_revision.read_json(os.path.join(out, "result.json"))
    anomalies = (result or {}).get("version_boundary", {}).get("anomalies", [])
    return rc == 0 and result is not None and any("bad JSON" in str(a) for a in anomalies), \
        "rc=0, case_id=%s, anomaly recorded" % (result or {}).get("case_id")


def scenario_minimal(root):
    inp = os.path.join(root, "mini", "input")
    out = os.path.join(root, "mini", "output")
    work = os.path.join(root, "mini", "work")
    mat = os.path.join(inp, "materials")
    os.makedirs(mat, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "CASE-001", "task": "审稿回复与论文修改"}, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 审稿回复与论文修改\n要求逐条回复并保留来源。\n")
    with open(os.path.join(mat, "manuscript-baseline.md"), "w", encoding="utf-8") as f:
        f.write("# Baseline\n\n结果 old。\n")
    with open(os.path.join(mat, "reviewer-comments.md"), "w", encoding="utf-8") as f:
        f.write("意见 1（数据）：请替换表述“old”。\n")

    scaffold.main(["--case", os.path.join(inp, "case.json"),
                   "--task", os.path.join(inp, "task.md"),
                   "--out", out, "--work", work])

    # fill the manuscript and ledger to simulate a completed revision
    ms_path = os.path.join(out, "revised_manuscript.md")
    with open(ms_path, "w", encoding="utf-8") as f:
        f.write("# Revised\n\n结果 new。\n")
    with open(os.path.join(out, "response_letter.md"), "w", encoding="utf-8") as f:
        f.write("# 逐条回复信\n\n意见 1：已采纳，见修改定位表。\n")
    ledger_path = os.path.join(out, "revision_ledger.json")
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump({"case_id": "CASE-001", "comments": [{
            "comment_id": "1",
            "comment_type": "data",
            "severity": "minor",
            "sub_requirements": ["替换表述"],
            "decision": "accept",
            "rationale": "按意见修正表述",
            "tone": "accept",
            "modifications": [{"action": "替换表述", "file": "revised_manuscript.md",
                               "location": "§Results", "anchor": "old→new",
                               "status": "done"}],
            "evidence": [{"need": "无", "status": "in_place", "source": "manuscript-baseline.md"}],
        }]}, f, ensure_ascii=False)

    # bring result.json to a complete, contract-compliant state
    result, _ = check_revision.read_json(os.path.join(out, "result.json"))
    result["status"] = "complete"
    result["fact_source_mapping"] = [{
        "claim": "表述 old 已改为 new", "source_ref": "materials/manuscript-baseline.md",
        "location": "§Results", "quote": "结果 old。", "status": "traced"}]
    result["version_boundary"]["changes"] = [{
        "comment_id": "1", "action": "替换表述", "location": "§Results", "status": "done"}]
    result["change_log"].append({"ts": "t", "action": "revise", "detail": "替换 old→new"})
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    issues, _warn = check_revision.validate_contract(result)
    report = check_revision.verify_ledger(
        check_revision.read_json(ledger_path)[0],
        check_revision.read_text(ms_path),
        check_revision.read_text(os.path.join(out, "response_letter.md")),
        check_revision.read_text(os.path.join(mat, "reviewer-comments.md")))
    found = report["coverage"]["claims_found"]
    return (not issues) and found == 1 and not report["gaps"], \
        "contract_ok=%s claims_found=%d gaps=%d" % (not issues, found, len(report["gaps"]))


def scenario_broken_contract(root):
    bad = {"status": "complete"}  # missing case_id, deliverable, sources, ...
    issues, _warn = check_revision.validate_contract(bad)
    return len(issues) >= 3, "flags %d contract issues" % len(issues)


def main():
    root = tempfile.mkdtemp(prefix="revskill_selftest_")
    scenarios = [
        ("empty input", scenario_empty),
        ("bad JSON", scenario_bad_json),
        ("minimal valid run", scenario_minimal),
        ("broken contract", scenario_broken_contract),
    ]
    all_ok = True
    for name, fn in scenarios:
        try:
            ok, detail = fn(root)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, "exception: %s" % exc
        print("PASS" if ok else "FAIL", "-", name, "(" + detail + ")")
        all_ok = all_ok and ok
    shutil.rmtree(root, ignore_errors=True)
    print("SELFTEST", "OK" if all_ok else "FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
