# -*- coding: utf-8 -*-
"""Built-in self-test for the literature-tracking Skill.

Exercises the failure scenarios listed in references/checklist.md and asserts
that every run exits 0 while producing a legal result.json. Uses tempdirs, so
it never touches the user's /input or /app/output. Standard library only.

Run via:  python3 scripts/main.py selftest
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

_MAIN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def _run(cmd, cwd):
    from subprocess import run, PIPE
    return run([sys.executable, "-B", _MAIN_PY] + cmd,
               cwd=cwd, stdout=PIPE, stderr=PIPE, text=True)


def _write(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _check_result(out_dir, label, failures):
    rp = Path(out_dir) / "result.json"
    if not rp.exists():
        failures.append("%s: result.json missing" % label)
        return None
    try:
        r = json.loads(rp.read_text(encoding="utf-8"))
    except Exception as exc:
        failures.append("%s: result.json not valid JSON: %s" % (label, exc))
        return None
    if not r.get("case_id"):
        failures.append("%s: missing case_id" % label)
    if not r.get("status"):
        failures.append("%s: missing status" % label)
    if "change_log" not in r:
        failures.append("%s: missing change_log" % label)
    if "uncertainty" not in r or "sources" not in r:
        failures.append("%s: missing uncertainty/sources" % label)
    return r


def _scenario_empty(tmp):
    d = tmp / "empty"
    inp = d / "input"
    inp.mkdir(parents=True)
    out = d / "out"
    work = d / "work"
    p = _run(["run", "--input", str(inp), "--output", str(out),
              "--work", str(work)], tmp)
    r = _check_result(out, "empty-input", [])
    fails = []
    if p.returncode != 0:
        fails.append("empty-input: non-zero exit %d" % p.returncode)
    if r and r["status"] not in ("insufficient_evidence", "input_error"):
        fails.append("empty-input: status=%s" % r["status"])
    return fails


def _scenario_bad_json(tmp):
    d = tmp / "badjson"
    inp = d / "input"
    inp.mkdir(parents=True)
    _write(inp / "case.json", "{ this is not json")
    _write(inp / "task.md", "# task\nchallenge_id: `challenge-bad`\n")
    out = d / "out"
    work = d / "work"
    p = _run(["run", "--input", str(inp), "--output", str(out),
              "--work", str(work)], tmp)
    r = _check_result(out, "bad-json", [])
    fails = []
    if p.returncode != 0:
        fails.append("bad-json: non-zero exit %d" % p.returncode)
    if r and not any("json parse failed" in e for e in r.get("errors", [])):
        fails.append("bad-json: expected parse error recorded")
    return fails


def _scenario_normal(tmp):
    d = tmp / "normal"
    inp = d / "input"
    inp.mkdir(parents=True)
    _write(inp / "case.json", json.dumps({
        "case_id": "case-selftest",
        "output_contract": {"search_results": [], "evidence": [],
                            "status_notes": {}}}))
    _write(inp / "task.md", "# test\nchallenge_id: `challenge-selftest`\n")
    _write(inp / "materials" / "notes.md", "A sample note for discovery.")
    out = d / "out"
    work = d / "work"
    p = _run(["plan", "--input", str(inp), "--output", str(out),
              "--work", str(work)], tmp)
    r = _check_result(out, "normal", [])
    fails = []
    if p.returncode != 0:
        fails.append("normal: non-zero exit %d" % p.returncode)
    plan_path = work / "plan.json"
    if not plan_path.exists():
        fails.append("normal: plan.json missing")
    else:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan.get("case_id") != "case-selftest":
            fails.append("normal: plan case_id mismatch")
        if not plan.get("materials"):
            fails.append("normal: materials not discovered")
    if r:
        for k in ("search_results", "evidence", "status_notes"):
            if k not in r:
                fails.append("normal: contract field %s missing" % k)
    return fails


def _scenario_dedup(tmp):
    d = tmp / "dedup"
    d.mkdir()
    recs = [
        {"doi": "10.1000/ABC", "title": "A Study of Things",
         "published_date": "2026-01-10"},
        {"doi": "https://doi.org/10.1000/abc", "title": "A study of things",
         "published_date": "2026-01-12"},
        {"arxiv": "2401.01234v1", "title": "A Study of Things"},
        {"title": "Unrelated Paper", "published_date": "2025-06-01"},
    ]
    rp = d / "records.json"
    _write(rp, json.dumps(recs))
    out = d / "dedup.json"
    p = _run(["dedup", "--records", str(rp), "--out", str(out)], tmp)
    fails = []
    if p.returncode != 0:
        fails.append("dedup: non-zero exit %d" % p.returncode)
        return fails
    if not out.exists():
        fails.append("dedup: dedup.json missing")
        return fails
    dd = json.loads(out.read_text(encoding="utf-8"))
    if dd["summary"]["input_count"] != 4:
        fails.append("dedup: input_count wrong")
    if dd["summary"]["group_count"] != 2:
        fails.append("dedup: expected 2 canonical groups, got %d"
                     % dd["summary"]["group_count"])
    return fails


def run(input_dir, output_dir, work_dir):
    del input_dir, output_dir, work_dir  # selftest uses its own tempdirs
    failures = []
    with tempfile.TemporaryDirectory(prefix="lit-track-selftest-") as tdir:
        tmp = Path(tdir)
        try:
            failures += _scenario_empty(tmp)
            failures += _scenario_bad_json(tmp)
            failures += _scenario_normal(tmp)
            failures += _scenario_dedup(tmp)
        except Exception:
            failures.append("uncaught: %s" % traceback.format_exc())

    if failures:
        print("selftest FAILED (%d):" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("selftest ok: empty / bad-json / normal / dedup scenarios passed")
    return 0


if __name__ == "__main__":
    sys.exit(run("/input", "/app/output", "/app/work"))
