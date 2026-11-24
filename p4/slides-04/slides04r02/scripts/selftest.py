#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- verify the Skill package mechanics without the real harness.

Exercises init_result.py and phase_gate.py in temp dirs and asserts:
  * empty input -> legal result.json, status insufficient_evidence, exit 2
  * minimal case -> exit 0, case_id echoed, skeleton fields all present
  * idempotency -> re-run preserves already-filled fields
  * bad JSON -> exit 2, valid result.json, errors recorded
  * phase_gate -> begin/end/requests/status/check behave, gate passes
  * stdlib-only -> every top-level import is a standard-library module

Exit 0 iff all checks pass; exit 1 otherwise.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
INIT = SCRIPT_DIR / "init_result.py"
GATE = SCRIPT_DIR / "phase_gate.py"
REQUIRED_KEYS = ["case_id", "status", "presentation", "evidence_map", "render_record",
                 "sources", "uncertainty", "pending_human", "change_log", "errors",
                 "degradations", "runtime"]


def run(args):
    return subprocess.run([sys.executable, "-B", *[str(a) for a in args]],
                          capture_output=True, text=True)


def load_result(out):
    with open(Path(out) / "result.json", "r", encoding="utf-8") as f:
        return json.load(f)


results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    suffix = "  [%s]" % detail if (detail and not ok) else ""
    print("%s: %s%s" % (mark, name, suffix))


def stdlib_only(path, extra_allowed=()):
    if hasattr(sys, "stdlib_module_names"):
        allowed = set(sys.stdlib_module_names)
    else:
        allowed = set("argparse hashlib json os sys time pathlib subprocess tempfile shutil re".split())
    allowed |= set(extra_allowed)
    src = path.read_text(encoding="utf-8")
    names = set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z0-9_]+)", src, flags=re.M))
    names -= {"__future__"}
    return sorted(n for n in names if n not in allowed)


def main():
    root = Path(tempfile.mkdtemp(prefix="skill-selftest-"))
    try:
        bad = []
        bad += [(INIT.name, m) for m in stdlib_only(INIT)]
        bad += [(GATE.name, m) for m in stdlib_only(GATE, extra_allowed={"init_result"})]
        bad += [(Path(__file__).name, m) for m in stdlib_only(Path(__file__))]
        check("stdlib_only_imports", not bad, str(bad) if bad else "")

        # T1 empty input
        t1 = root / "t1"
        (t1 / "input").mkdir(parents=True)
        r = run([INIT, "--input", t1 / "input", "--output", t1 / "output", "--work", t1 / "work"])
        res = load_result(t1 / "output")
        check("empty_input_exit2", r.returncode == 2, "exit=%s" % r.returncode)
        check("empty_input_status", res["status"] == "insufficient_evidence", res["status"])
        check("empty_input_keys", all(k in res for k in REQUIRED_KEYS), "")

        # T2 minimal case
        t2 = root / "t2"
        (t2 / "input").mkdir(parents=True)
        (t2 / "input" / "case.json").write_text('{"case_id": "demo-1"}', encoding="utf-8")
        (t2 / "input" / "task.md").write_text("# 任务说明\n做 PPT。", encoding="utf-8")
        r = run([INIT, "--input", t2 / "input", "--output", t2 / "output", "--work", t2 / "work"])
        res = load_result(t2 / "output")
        check("minimal_exit0", r.returncode == 0, "exit=%s" % r.returncode)
        check("minimal_case_id", res["case_id"] == "demo-1", str(res["case_id"]))
        check("minimal_status_inprog", res["status"] == "in_progress", res["status"])
        check("minimal_skeleton_timestamped", bool(res["runtime"].get("skeleton_written_at")), "")

        # T3 idempotency
        res["presentation"]["pptx_path"] = "x.pptx"
        res["evidence_map"].append({"page": 1, "claim": "test", "evidence": []})
        (t2 / "output" / "result.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        r = run([INIT, "--input", t2 / "input", "--output", t2 / "output", "--work", t2 / "work"])
        res2 = load_result(t2 / "output")
        check("idempotent_exit0", r.returncode == 0, "exit=%s" % r.returncode)
        check("idempotent_preserves_pptx", res2["presentation"]["pptx_path"] == "x.pptx", "")
        check("idempotent_preserves_evidence", len(res2["evidence_map"]) == 1, "")

        # T4 bad JSON
        t4 = root / "t4"
        (t4 / "input").mkdir(parents=True)
        (t4 / "input" / "case.json").write_text("{not valid json", encoding="utf-8")
        r = run([INIT, "--input", t4 / "input", "--output", t4 / "output", "--work", t4 / "work"])
        res = load_result(t4 / "output")
        check("badjson_exit2", r.returncode == 2, "exit=%s" % r.returncode)
        check("badjson_status_input_error", res["status"] == "input_error", res["status"])
        check("badjson_errors_recorded", len(res["errors"]) > 0, "")

        # T5 phase gate
        work = t2 / "work"
        r1 = run([GATE, "--work", work, "--begin", "p0_bootstrap"])
        r2 = run([GATE, "--work", work, "--end", "p0_bootstrap"])
        r3 = run([GATE, "--work", work, "--requests", "3", "--phase", "p1_extract"])
        r4 = run([GATE, "--work", work, "--status"])
        check("gate_begin_end", r1.returncode == 0 and r2.returncode == 0,
              "%s,%s" % (r1.returncode, r2.returncode))
        check("gate_requests", r3.returncode == 0 and "3" in r3.stdout, r3.stdout.strip())
        check("gate_status_has_p0", "p0_bootstrap" in r4.stdout, "")
        r5 = run([GATE, "--work", work, "--check"])
        check("gate_check_pass", r5.returncode == 0,
              "exit=%s\n%s\n%s" % (r5.returncode, r5.stdout.strip(), r5.stderr.strip()))
        ledger = json.loads((work / "phase_ledger.json").read_text(encoding="utf-8"))
        check("gate_ledger_req_total", ledger["requests_used_total"] == 3,
              str(ledger["requests_used_total"]))

        fails = [n for n, ok, _ in results if not ok]
        print("\n%d/%d checks passed" % (len(results) - len(fails), len(results)))
        return 1 if fails else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
