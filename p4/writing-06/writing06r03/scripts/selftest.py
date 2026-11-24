#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package-level self-test for paper-structure-logic-optimization.

Checks:
  1. SKILL.md exists and has name/description/version YAML frontmatter.
  2. scripts run (--help exit code 0).
  3. determinism: build a fixed demo input, run `evidence` twice into two work
     dirs, and assert evidence.json is byte-identical (SHA-256).
  4. evidence content is non-empty (candidate claims/values/boundary produced).
  5. result contract: if a result.json is provided/found, validate it.

Exit code 0 means pass; 1 means fail. The report is printed as JSON.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Emit UTF-8 on stdout/stderr regardless of the platform locale.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEMO_MATERIAL = """\
# Title

## Abstract

We show that treatment X improves outcome Y (p < 0.05, 12.4% vs 8.1%).

## Introduction

The research question asks whether X causes Y. Prior work suggests a correlation.

## Methods

We recruited 120 participants.

## Results

Treatment X significantly improved outcome Y (12.4% vs 8.1%, p = 0.031).

## Discussion

Our results indicate that X leads to Y, therefore X causes Y.

## Conclusion

X causes Y and should be adopted.
"""

DEMO_CASE = {
    "case_id": "selftest-case-1",
    "id": "selftest-case-1",
    "output_contract": {
        "diagnosis": None,
        "edit_plan": None,
        "source_map": None,
        "version_boundary": None,
        "artifacts": None,
    },
}


def _check(name, ok, detail=""):
    return {"name": name, "result": "PASS" if ok else "FAIL", "detail": detail}


def _read_skill_md():
    skill_md = os.path.join(ROOT, "SKILL.md")
    if not os.path.exists(skill_md):
        return None, "SKILL.md missing"
    try:
        with open(skill_md, "r", encoding="utf-8-sig") as f:
            return f.read(), None
    except OSError as e:
        return None, str(e)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="selftest.py",
                                 description="package self-test for the paper-structure Skill")
    ap.add_argument("--result", default=None, help="result.json to validate (optional)")
    args = ap.parse_args(argv)

    checks = []

    # 1. frontmatter
    content, err = _read_skill_md()
    if content is None:
        checks.append(_check("skill_md_exists", False, err))
    else:
        front = content.lstrip().startswith("---")
        has = all(k in content for k in ("name:", "description:", "version:"))
        checks.append(_check("skill_frontmatter", front and has,
                             f"SKILL.md {len(content)} chars"))

    # 2. scripts run
    for script in ("main.py", "selftest.py"):
        p = os.path.join(HERE, script)
        if not os.path.exists(p):
            checks.append(_check(f"{script}_exists", False, "missing"))
            continue
        r = subprocess.run([sys.executable, "-B", p, "--help"],
                           capture_output=True, text=True, timeout=60)
        checks.append(_check(f"{script}_help", r.returncode == 0,
                             f"exit {r.returncode}: {(r.stderr or '').strip()[:120]}"))

    # 3. determinism
    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        indir = os.path.join(tmp, "input")
        os.makedirs(indir)
        with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as f:
            json.dump(DEMO_CASE, f, ensure_ascii=False)
        with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as f:
            f.write("# selftest task\n请完成论文结构诊断。\n")
        with open(os.path.join(indir, "paper.md"), "w", encoding="utf-8") as f:
            f.write(DEMO_MATERIAL)

        w1 = os.path.join(tmp, "work1")
        w2 = os.path.join(tmp, "work2")
        o1 = os.path.join(tmp, "out1")
        o2 = os.path.join(tmp, "out2")
        main_py = os.path.join(HERE, "main.py")
        r1 = subprocess.run([sys.executable, "-B", main_py, "evidence",
                             "--input", indir, "--work", w1, "--output", o1,
                             "--case", os.path.join(indir, "case.json"),
                             "--task", os.path.join(indir, "task.md")],
                            capture_output=True, text=True, timeout=120)
        r2 = subprocess.run([sys.executable, "-B", main_py, "evidence",
                             "--input", indir, "--work", w2, "--output", o2,
                             "--case", os.path.join(indir, "case.json"),
                             "--task", os.path.join(indir, "task.md")],
                            capture_output=True, text=True, timeout=120)

        e1 = os.path.join(w1, "evidence.json")
        e2 = os.path.join(w2, "evidence.json")
        if not (r1.returncode == 0 and r2.returncode == 0
                and os.path.exists(e1) and os.path.exists(e2)):
            checks.append(_check("determinism", False,
                                 f"evidence rc {r1.returncode}/{r2.returncode}"))
        else:
            with open(e1, "rb") as f:
                h1 = hashlib.sha256(f.read()).hexdigest()
            with open(e2, "rb") as f:
                h2 = hashlib.sha256(f.read()).hexdigest()
            checks.append(_check("determinism", h1 == h2, f"{h1[:12]} vs {h2[:12]}"))

            with open(e1, encoding="utf-8") as f:
                ev = json.load(f)
            claims = len(ev.get("candidate_claims", []))
            values = len(ev.get("candidate_values", []))
            boundary = len(ev.get("candidate_boundary", []))
            checks.append(_check("evidence_content",
                                 claims > 0 and values > 0 and boundary > 0,
                                 f"claims={claims} values={values} boundary={boundary}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 4. result contract
    result_path = args.result or os.environ.get("RESULT_JSON")
    if result_path and os.path.exists(result_path):
        try:
            with open(result_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            required = ["case_id", "status", "sources", "uncertainty",
                        "pending_human", "change_log"]
            missing = [k for k in required if k not in data]
            checks.append(_check("result_contract", not missing, f"missing={missing}"))
        except Exception as e:  # noqa: BLE001
            checks.append(_check("result_contract", False, str(e)))
    else:
        checks.append(_check("result_contract", True,
                             "no result.json to check (runtime uses main.py verify)"))

    report = {"pass": all(c["result"] == "PASS" for c in checks), "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
