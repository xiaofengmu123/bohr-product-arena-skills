#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check result.json and optionally run an end-to-end plumbing self test.

Check mode (runtime self-check):
  python scripts/selftest.py --result /app/output/result.json --summary /app/work/input_summary.json

Self-test mode (builds a throwaway fixture in a temp dir at runtime; it is a
plumbing test of the three scripts, not a prepared answer and not a formal
case result):
  python scripts/selftest.py --self-test
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

FORBIDDEN = re.compile(
    r"国际领先|国际先进|填补空白|颠覆性|世界首|全球首|首创|重大突破|里程碑|首次",
    re.I,
)
ALLOWED_STATUS = {
    "in_progress", "partial", "complete", "failed", "ok",
    "insufficient_evidence", "input_error",
}
HARD_CHECKS = {
    "result_json_parseable",
    "case_id_present",
    "all_output_contract_fields_present",
    "sources_present_and_array",
    "uncertainty_present_and_array",
    "change_log_present_and_array",
    "status_valid",
}


def load(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def check(result_path, summary_path):
    result = load(result_path)
    summary = load(summary_path) if summary_path and os.path.isfile(summary_path) else {}

    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("result_json_parseable", True)
    add("case_id_present", bool(result.get("case_id")))
    if summary.get("case_id") and result.get("case_id"):
        add("case_id_matches", result["case_id"] == summary["case_id"],
            "result=%s summary=%s" % (result["case_id"], summary["case_id"]))

    fields = summary.get("output_contract_fields") or list(summary.get("output_contract", {}))
    missing = [f for f in fields if f not in result]
    add("all_output_contract_fields_present", not missing, "missing=%s" % missing)

    for req in ("sources", "uncertainty", "change_log"):
        add("%s_present_and_array" % req, isinstance(result.get(req), list),
            "type=%s" % type(result.get(req)).__name__)

    st = result.get("status")
    add("status_valid", st in ALLOWED_STATUS, "status=%s" % st)
    if st == "failed":
        add("status_failed_has_log", any(
            "fail" in str(c).lower() or "error" in str(c).lower()
            for c in result.get("change_log", [])),
            "failed status should be explained in change_log")

    # Referenced deliverable files should exist (soft: agent reviews).
    files = referenced_files(result)
    for fp in files:
        add("deliverable_exists", os.path.isfile(fp), "path=%s" % fp)

    # facts_sources entries should carry a verbatim quote (soft).
    fs = result.get("facts_sources")
    if isinstance(fs, list) and fs:
        bad = []
        for i, item in enumerate(fs):
            if not isinstance(item, dict):
                bad.append("%d:not-a-dict" % i)
                continue
            if not (item.get("quote") or item.get("text") or item.get("evidence")):
                bad.append("%d:missing-quote" % i)
        add("facts_sources_have_quotes", not bad, "issues=%s" % bad[:8])

    # Forbidden inflated wording (soft: review, justify, or remove).
    blob = json.dumps(result, ensure_ascii=False)
    hits = sorted(set(FORBIDDEN.findall(blob)))
    add("no_inflated_wording", not hits, "hits=%s" % hits)

    hard_fail = any(not c["ok"] for c in checks if c["check"] in HARD_CHECKS)
    print(json.dumps({"ok": not hard_fail, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if not hard_fail else 1


def referenced_files(result):
    """Collect absolute/relative file paths referenced by deliverable."""
    out = []
    d = result.get("deliverable")
    if isinstance(d, dict):
        for k in ("file", "path", "files", "manuscript", "preview"):
            v = d.get(k)
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, list):
                out.extend(x for x in v if isinstance(x, str))
    elif isinstance(d, str):
        out.append(d)
    return out


def self_test():
    """Run read_input -> build_result -> selftest on a throwaway fixture."""
    with tempfile.TemporaryDirectory() as d:
        indir = os.path.join(d, "input")
        work = os.path.join(d, "work")
        outdir = os.path.join(d, "output")
        os.makedirs(indir)
        case = {
            "case_id": "selftest-plumbing",
            "output_contract": {
                "deliverable": {"type": "object"},
                "facts_sources": {"type": "list"},
                "version_boundaries": {"type": "object"},
            },
        }
        with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)
        with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as f:
            f.write("# self-test task\nFigure 1 shows n = 12.\n")
        with open(os.path.join(indir, "material.txt"), "w", encoding="utf-8") as f:
            f.write("Figure 1. p = 0.03, mean ± SE.\n")

        here = os.path.dirname(os.path.abspath(__file__))

        def run(script, args):
            p = os.path.join(here, script)
            return subprocess.run([sys.executable, p] + args, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")

        r1 = run("read_input.py", ["--case", os.path.join(indir, "case.json"),
                                   "--task", os.path.join(indir, "task.md"),
                                   "--input-dir", indir, "--work", work])
        if r1.returncode != 0:
            print(json.dumps({"ok": False, "step": "read_input", "stderr": r1.stderr}, ensure_ascii=False))
            return 1
        r2 = run("build_result.py", ["--summary", os.path.join(work, "input_summary.json"),
                                     "--output-dir", outdir])
        if r2.returncode != 0:
            print(json.dumps({"ok": False, "step": "build_result", "stderr": r2.stderr}, ensure_ascii=False))
            return 1
        result_path = os.path.join(outdir, "result.json")
        result = load(result_path)
        assert result["case_id"] == "selftest-plumbing"
        assert result["status"] == "partial"
        assert "deliverable" in result and "facts_sources" in result and "version_boundaries" in result
        r3 = run("selftest.py", ["--result", result_path,
                                 "--summary", os.path.join(work, "input_summary.json")])
        if r3.returncode != 0:
            print(json.dumps({"ok": False, "step": "selftest", "stdout": r3.stdout}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "pipeline": ["read_input", "build_result", "selftest"],
                          "result_status": result["status"]}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Self-check result.json or run self-test")
    ap.add_argument("--result", help="path to result.json")
    ap.add_argument("--summary", help="path to input_summary.json")
    ap.add_argument("--self-test", action="store_true", help="run throwaway plumbing self-test")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.result:
        return check(a.result, a.summary)
    ap.error("provide --result or --self-test")
    return 2


if __name__ == "__main__":
    sys.exit(main())
