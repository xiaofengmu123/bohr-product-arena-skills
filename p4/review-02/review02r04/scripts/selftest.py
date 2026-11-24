"""selftest.py - self-test for the citation-fact-audit Skill.

Standard library only; deterministic; exit code 0 means every check passed.

Modes:
  --result PATH         validate an existing result.json against the contract
  --scenario NAME       run one degradation scenario (empty|badjson|missing|sample)
  --scenario all        run every scenario in isolated temp directories

Each scenario executes scripts/main.py through the current interpreter with
explicit --input/--output/--work paths, then validates the produced
result.json. This mirrors the runtime contract: the Skill must leave a legal,
honest result file even when the input is broken or absent.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(HERE, "main.py")

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
SEVERITIES = {"critical", "major", "minor", "suggestion"}
CATEGORIES = {
    "citation_support", "fact_source", "evidence_location", "numerical_statistical",
    "argumentation", "academic_norm", "method_design",
}
REQUIRED_FINDING_KEYS = ("id", "category", "finding_type", "severity", "verdict", "locator", "quote", "suggestion")
TOP_KEYS = ("case_id", "status", "summary", "review_report", "evidence", "sources",
            "uncertainty", "pending_human", "limitations", "errors", "degradations", "change_log", "runtime")


def _problems(result):
    problems = []
    for key in TOP_KEYS:
        if key not in result:
            problems.append("missing top-level key: %s" % key)
    if result.get("status") not in STATUSES:
        problems.append("invalid status: %r" % (result.get("status"),))
    findings = result.get("review_report", {}).get("findings")
    if not isinstance(findings, list):
        problems.append("review_report.findings is not a list")
    else:
        for f in findings:
            for key in REQUIRED_FINDING_KEYS:
                if key not in f or f.get(key) in (None, ""):
                    problems.append("%s missing required key %s" % (f.get("id", "?"), key))
            if f.get("category") not in CATEGORIES:
                problems.append("%s invalid category %r" % (f.get("id"), f.get("category")))
            if f.get("severity") not in SEVERITIES:
                problems.append("%s invalid severity %r" % (f.get("id"), f.get("severity")))
            if "quote_verified" not in f:
                problems.append("%s missing quote_verified" % (f.get("id"),))
    if "coverage_matrix" not in result.get("review_report", {}):
        problems.append("missing coverage_matrix")
    if not isinstance(result.get("sources"), list):
        problems.append("sources is not a list")
    if not isinstance(result.get("uncertainty"), list):
        problems.append("uncertainty is not a list")
    if not isinstance(result.get("change_log"), list):
        problems.append("change_log is not a list")
    return problems


def validate_result(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            result = json.load(fh)
    except (OSError, ValueError) as exc:
        print("FAIL %s: unreadable/invalid JSON (%s)" % (path, exc))
        return 1
    problems = _problems(result)
    if problems:
        print("FAIL %s (%d problems):" % (path, len(problems)))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("OK %s: case_id=%s status=%s findings=%d" % (
        path, result.get("case_id"), result.get("status"),
        len(result.get("review_report", {}).get("findings", []))))
    return 0


def _write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _run_main(input_dir, output_dir, work_dir):
    return subprocess.run(
        [sys.executable, "-B", MAIN_PY, "--input", input_dir, "--output", output_dir, "--work", work_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )


def _scenario(name):
    base = tempfile.mkdtemp(prefix="cfa-selftest-%s-" % name)
    input_dir = os.path.join(base, "input")
    output_dir = os.path.join(base, "output")
    work_dir = os.path.join(base, "work")
    os.makedirs(input_dir)
    if name == "empty":
        pass  # literally nothing
    elif name == "badjson":
        _write(os.path.join(input_dir, "case.json"), "{not valid json")
        _write(os.path.join(input_dir, "task.md"), "# task\n")
        _write(os.path.join(input_dir, "paper.txt"), "Some text to review.")
    elif name == "missing":
        _write(os.path.join(input_dir, "case.json"), json.dumps({"case_id": "case-missing-material"}))
        _write(os.path.join(input_dir, "task.md"), "# task\n")
    elif name == "sample":
        _write(os.path.join(input_dir, "case.json"), json.dumps({"case_id": "case-sample", "output_contract": {"grading_rubric": "six categories"}}))
        _write(os.path.join(input_dir, "task.md"), "# task\n")
        _write(os.path.join(input_dir, "paper.txt"), "The method yields a significant improvement. The result is reported in Table 1.")
    else:
        raise ValueError("unknown scenario: %s" % name)
    proc = _run_main(input_dir, output_dir, work_dir)
    if proc.returncode != 0:
        print("FAIL scenario=%s: main.py exited %d\n%s" % (name, proc.returncode, proc.stderr.decode("utf-8", "replace")))
        return 1
    code = validate_result(os.path.join(output_dir, "result.json"))
    if code == 0:
        with open(os.path.join(output_dir, "result.json"), "r", encoding="utf-8") as fh:
            r = json.load(fh)
        if name in ("empty", "missing") and r.get("status") not in ("insufficient_evidence", "input_error"):
            print("FAIL scenario=%s: expected honest degraded status, got %r" % (name, r.get("status")))
            return 1
        if name == "badjson" and not r.get("errors"):
            print("FAIL scenario=%s: expected recorded error for bad JSON" % name)
            return 1
        print("OK scenario=%s -> status=%s verdict=%s" % (name, r.get("status"), r.get("summary", {}).get("verdict")))
    return code


def main(argv):
    parser = argparse.ArgumentParser(prog="selftest.py", description="self-test for citation-fact-audit")
    parser.add_argument("--result", help="validate an existing result.json")
    parser.add_argument("--scenario", choices=["empty", "badjson", "missing", "sample", "all"], help="run degradation scenario(s)")
    args = parser.parse_args(argv)
    if args.result:
        return validate_result(args.result)
    if args.scenario:
        names = ["empty", "badjson", "missing", "sample"] if args.scenario == "all" else [args.scenario]
        code = 0
        for name in names:
            code = max(code, _scenario(name))
        return code
    # default: run every scenario, the strongest single self-check
    return main(["--scenario", "all"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
