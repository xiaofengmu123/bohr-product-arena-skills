#!/usr/bin/env python3
"""Self-test for the revision-response Skill package.

Builds *synthetic, clearly-labelled* fixtures in a temporary directory (never a
formal case or a prepared answer), then runs scaffold.py and check_revision.py
against them to prove the scripts actually work and actually detect the
inconsistencies the SKILL.md promises to catch. Exits 0 when every assertion
passes, 1 otherwise. Standard library only.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCAFFOLD = os.path.join(HERE, "scaffold.py")
CHECK = os.path.join(HERE, "check_revision.py")

# Every fixture below is synthetic (SYNTHETIC-SELFTEST), not a real paper.
CASE = {
    "case_id": "synthetic-selftest-case",
    "content": "SYNTHETIC-SELFTEST: minimal revision fixture for self-test only.",
    "materials": ["materials/manuscript-baseline.md", "materials/reviewer-comments.md"],
}
TASK = "# SYNTHETIC-SELFTEST task\nRevise the baseline and reply point by point.\n"
BASELINE = (
    "# Synthetic Baseline v1\n\n## Results\n"
    "The baseline cohort had 120 participants and a mean age of 41.5 years.\n"
    "Anchor fragment: we observed a 12% increase in recovery.\n"
)
COMMENTS = (
    "# Synthetic reviewer comments\n\n"
    "R1 (method): clarify the inclusion criteria and report the dropout rate.\n"
    "R2 (data): provide the per-group sample sizes.\n"
)


def run(script, args):
    proc = subprocess.run(
        [sys.executable, script] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def main(argv=None):
    work = None
    failures = []
    passed = []
    if argv:
        # minimal argv parse: --work <dir>
        for i, tok in enumerate(argv):
            if tok == "--work" and i + 1 < len(argv):
                work = argv[i + 1]
    if not work:
        work = tempfile.mkdtemp(prefix="revision-skill-selftest-")

    case_dir = os.path.join(work, "input")
    mat_dir = os.path.join(case_dir, "materials")
    out_dir = os.path.join(work, "output")
    for d in (case_dir, mat_dir, out_dir):
        os.makedirs(d, exist_ok=True)

    def w(name, content):
        path = os.path.join(case_dir, name) if "/" not in name else os.path.join(work, "input", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    case_path = w("case.json", json.dumps(CASE, ensure_ascii=False))
    w("task.md", TASK)
    w("materials/manuscript-baseline.md", BASELINE)
    w("materials/reviewer-comments.md", COMMENTS)

    def expect(name, condition, detail=""):
        if condition:
            passed.append(name)
        else:
            failures.append(name + (": " + detail if detail else ""))

    # --- Test 1: scaffold produces a valid in_progress skeleton ---
    rc, out, err = run(SCAFFOLD, [
        "--case", case_path, "--task", os.path.join(case_dir, "task.md"),
        "--materials", mat_dir, "--out", os.path.join(out_dir, "result.json"),
        "--work", os.path.join(work, "work"),
    ])
    expect("scaffold-exit-0", rc == 0, "rc=%d stderr=%s" % (rc, err.strip()))
    result_path = os.path.join(out_dir, "result.json")
    expect("scaffold-wrote-result", os.path.exists(result_path))
    with open(result_path, "r", encoding="utf-8") as f:
        skeleton = json.load(f)
    expect("scaffold-case-id", skeleton.get("case_id") == "synthetic-selftest-case")
    expect("scaffold-status", skeleton.get("status") == "in_progress")
    for field in ("deliverable", "fact_source_mapping", "version_boundary"):
        expect("scaffold-contract-" + field, field in skeleton)

    # --- Test 2: a good result passes check_revision ---
    revised = "# Synthetic Baseline v2\n\n## Results\n" \
        "The baseline cohort had 120 participants and a mean age of 41.5 years.\n" \
        "Anchor fragment: we observed a 12% increase in recovery.\n" \
        "Per-group sample sizes are 60 and 60.\n"
    revised_path = os.path.join(out_dir, "revised-manuscript.md")
    with open(revised_path, "w", encoding="utf-8") as f:
        f.write(revised)

    good = {
        "case_id": "synthetic-selftest-case",
        "status": "partial",
        "deliverable": {"path": revised_path, "format": "markdown",
                        "title": "Synthetic revision", "version": "v2",
                        "sections": ["Results"], "status": "ready"},
        "fact_source_mapping": [
            {"claim_id": "R1-A1", "claim": "cohort had 120 participants",
             "kind": "number", "source_ref": "materials/manuscript-baseline.md",
             "location": "Results, para 1", "quote": "120 participants",
             "status": "traced"},
            {"claim_id": "R2-C1", "claim": "per-group sample sizes added",
             "kind": "revision", "source_ref": "materials/manuscript-baseline.md",
             "location": "Results, para 1", "quote": "60 and 60",
             "status": "traced"},
        ],
        "version_boundary": {
            "changes": [
                {"id": "C01", "comment_id": "R2", "before": "no per-group sizes",
                 "after": "Per-group sample sizes are 60 and 60.",
                 "location": "Results, para 1", "anchor": "Per-group sample sizes are 60 and 60",
                 "manuscript_version": "v2", "status": "applied"},
            ],
            "pending_confirmation": [], "material_gaps": [], "anomalies": [],
        },
        "response_letter": {"path": os.path.join(out_dir, "response-letter.md"), "entries": [
            {"comment_id": "R1", "decision": "accept", "tone": "accept",
             "summary": "We clarified the inclusion criteria in the revised text.",
             "subrequirements": ["R1a"], "change_ids": [], "evidence": [
                 {"item": "inclusion criteria", "status": "provided",
                  "source_ref": "materials/manuscript-baseline.md", "quote": "120 participants"}],
             "unresolved": []},
            {"comment_id": "R2", "decision": "accept", "tone": "accept",
             "summary": "We now report per-group sample sizes.",
             "subrequirements": ["R2a"], "change_ids": ["C01"], "evidence": [],
             "unresolved": []},
        ]},
        "sources": [], "uncertainty": [], "pending_human": [],
        "limitations": [{"item": "synthetic fixture", "scope": "self-test", "impact": "none"}],
        "degradations": [], "change_log": [],
    }
    good_path = os.path.join(out_dir, "good-result.json")
    with open(good_path, "w", encoding="utf-8") as f:
        json.dump(good, f, ensure_ascii=False, indent=2)
    rc, out, err = run(CHECK, [
        "--result", good_path, "--manuscript", revised_path,
        "--work", os.path.join(work, "work"),
    ])
    report = json.loads(out)
    expect("check-good-exit-0", rc == 0, "rc=%d" % rc)
    expect("check-good-no-fail", report.get("fail", 0) == 0, json.dumps(report.get("items", []), ensure_ascii=False))

    # --- Test 3: a broken result is actually flagged ---
    broken = json.loads(json.dumps(good))
    broken["fact_source_mapping"][0]["quote"] = ""  # traced without quote
    broken["version_boundary"]["changes"][0]["anchor"] = "this anchor is not in the manuscript"
    broken_path = os.path.join(out_dir, "broken-result.json")
    with open(broken_path, "w", encoding="utf-8") as f:
        json.dump(broken, f, ensure_ascii=False, indent=2)
    rc, out, err = run(CHECK, [
        "--result", broken_path, "--manuscript", revised_path,
        "--work", os.path.join(work, "work"),
    ])
    report = json.loads(out)
    expect("check-broken-flags-fail", report.get("fail", 0) >= 2,
           "fail=%d" % report.get("fail", 0))

    print(json.dumps({
        "synthetic": True,
        "note": "all fixtures are synthetic self-test data, not a formal case or answer",
        "work": work,
        "passed": len(passed),
        "failed": len(failures),
        "passed_items": passed,
        "failed_items": failures,
    }, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
