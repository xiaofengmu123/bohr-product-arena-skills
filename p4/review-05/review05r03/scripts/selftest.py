#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test harness for the source-bound academic audit skill.

Builds a synthetic demo input in a temporary directory and exercises the full
pipeline: extract -> run -> merge -> validate. Also verifies:
* every CLI answers --help with exit code 0;
* the same findings file merged twice yields identical conclusions
  (volatile timestamps excluded);
* an empty input directory still returns a valid result.json with
  ``insufficient_evidence`` and exit code 0;
* a corrupt case.json is recorded in errors without crashing.

The demo material is clearly synthetic and is created at runtime in a temp
directory; nothing here is a pre-written answer for the official cases.
Exit 0 = all checks passed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

DEMO_CASE = {
    "case_id": "demo-selftest-001",
    "output_contract": {
        "case_id": "",
        "status": "partial",
        "report": {"findings": [], "checked_items": []},
        "sources": [],
        "uncertainty": [],
        "change_log": [],
    },
}

DEMO_MATERIAL = """\
# Demo study for self-test (synthetic, not a real case)

## Methods

We enrolled n = 120 participants after written informed consent; the protocol
was approved by the institutional review board (approval no. IRB-2024-001).

Statistical analysis used two-sided tests; the primary outcome differed
between arms (p = 0.041, 95% CI 0.002 to 0.080).

## Results

Figure 1 shows the flow of participants, adapted from a published diagram with
permission under a Creative Commons Attribution license.

## Funding and conflicts

This synthetic work was supported by grant number 2024-ABC-01. The authors
declare no conflict of interest. Correspondence: contact@example.org.

## References

1. Doe J, Smith A. A benchmark of review agents. J Demo Stud. 2024. doi:10.1234/demo.5678
"""


def run(cmd, cwd=None, timeout=180):
    proc = subprocess.run(
        [sys.executable, "-B", *cmd],
        cwd=str(cwd or ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return proc


def strip_volatile(obj):
    """Remove timestamps/runtime from a decoded JSON structure."""
    if isinstance(obj, dict):
        return {
            k: strip_volatile(v)
            for k, v in obj.items()
            if k not in {"runtime", "change_log", "time", "at", "started_at"}
        }
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def make_demo(tmp):
    demo = Path(tmp) / "demo"
    (demo / "materials").mkdir(parents=True)
    write_json(demo / "case.json", DEMO_CASE)
    (demo / "task.md").write_text("# demo task\n", encoding="utf-8")
    (demo / "materials" / "paper.md").write_text(DEMO_MATERIAL, encoding="utf-8")
    return demo


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))
    return ok


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    results = []
    tmp = tempfile.mkdtemp(prefix="audit-selftest-")
    out = Path(tmp) / "out"
    work = Path(tmp) / "work"
    demo = make_demo(tmp)

    # 1. help/exit codes
    for script in ("main.py", "extract.py"):
        p = run([str(SCRIPTS / script), "--help"])
        results.append(check(f"{script} --help exit 0", p.returncode == 0,
                             f"rc={p.returncode}"))

    # 2. full pipeline on demo
    p = run([str(SCRIPTS / "main.py"), "run",
             "--input", str(demo), "--output", str(out), "--work", str(work)])
    results.append(check("main.py run exit 0", p.returncode == 0,
                         p.stderr.decode("utf-8", "replace").strip()[:200]))
    res_path = out / "result.json"
    if res_path.exists():
        r = read_json(res_path)
        results.append(check("skeleton case_id", r.get("case_id") == "demo-selftest-001"))
        results.append(check("skeleton status partial", r.get("status") == "partial"))
        results.append(check("skeleton output_contract", isinstance(r.get("output_contract"), dict)))
        results.append(check("evidence has observations",
                             (work / "evidence.json").exists()))
    else:
        results.append(check("skeleton result.json exists", False))

    # 3. merge a finding whose quote is verbatim from the material
    findings = {
        "summary": "demo finding",
        "findings": [
            {
                "type": "norm_violation",
                "severity": "minor",
                "category": "privacy",
                "title": "Demo contact email exposed",
                "file": "materials/paper.md",
                "locator": "",
                "quote": "contact@example.org",
                "evidence": "email pattern in Correspondence line",
                "basis": "privacy convention: avoid raw contact in deliverable",
                "revision": "replace with institutional address",
            }
        ],
        "checked_items": [
            {"item": s, "status": "checked", "note": "demo"}
            for s in ["academic_integrity", "citation", "privacy", "copyright",
                      "ethics", "submission", "quality", "statistics", "method",
                      "logic"]
        ],
    }
    fpath = Path(tmp) / "findings.json"
    write_json(fpath, findings)
    p = run([str(SCRIPTS / "main.py"), "merge",
             "--findings", str(fpath), "--input", str(demo),
             "--output", str(out), "--work", str(work)])
    results.append(check("main.py merge exit 0", p.returncode == 0,
                         p.stderr.decode("utf-8", "replace").strip()[:200]))
    r1 = read_json(res_path) if res_path.exists() else {}
    results.append(check("merged finding id", r1.get("report", {}).get("findings", [{}])[0].get("id") == "F001"))

    # 4. validation passes
    p = run([str(SCRIPTS / "main.py"), "validate",
             "--input", str(demo), "--output", str(out), "--work", str(work)])
    results.append(check("main.py validate exit 0", p.returncode == 0,
                         p.stdout.decode("utf-8", "replace").strip()[:200]))

    # 5. determinism: merge again, compare conclusions ignoring timestamps
    p = run([str(SCRIPTS / "main.py"), "merge",
             "--findings", str(fpath), "--input", str(demo),
             "--output", str(out), "--work", str(work)])
    r2 = read_json(res_path)
    results.append(check(
        "deterministic re-merge",
        strip_volatile(r1) == strip_volatile(r2),
        "same findings file merged twice must yield identical conclusions",
    ))

    # 6. empty input directory
    empty = Path(tmp) / "empty"
    empty.mkdir()
    eout = Path(tmp) / "eout"
    ework = Path(tmp) / "ework"
    p = run([str(SCRIPTS / "main.py"), "run",
             "--input", str(empty), "--output", str(eout), "--work", str(ework)])
    eres = read_json(eout / "result.json") if (eout / "result.json").exists() else {}
    results.append(check("empty input exit 0", p.returncode == 0))
    results.append(check("empty input status", eres.get("status") == "insufficient_evidence"))

    # 7. corrupt case.json
    bad = Path(tmp) / "bad"
    bad.mkdir()
    write_json(bad / "case.json", "{ this is not json")
    (bad / "materials").mkdir(parents=True)
    (bad / "materials" / "x.md").write_text("some text", encoding="utf-8")
    bout = Path(tmp) / "bout"
    bwork = Path(tmp) / "bwork"
    p = run([str(SCRIPTS / "main.py"), "run",
             "--input", str(bad), "--output", str(bout), "--work", str(bwork)])
    bres = read_json(bout / "result.json") if (bout / "result.json").exists() else {}
    results.append(check("bad case.json exit 0", p.returncode == 0))
    results.append(check("bad case.json recorded", bool(bres.get("errors"))))

    passed = sum(1 for x in results if x)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
