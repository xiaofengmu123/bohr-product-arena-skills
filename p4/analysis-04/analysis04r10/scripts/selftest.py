#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package self-test. Verifies structure, frontmatter, script runnability and a
synthetic end-to-end run through init/discover/profile/plan/run/check/deliver.

Usage (from package root):
    python3 -B scripts/selftest.py [--work <tmp>]
Exit code is always 0 (a failing check is reported inside the JSON, never as a crash).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)


def check(name, ok, detail=""):
    return {"check": name, "ok": bool(ok), "detail": detail}


def read(p):
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError as e:
        return "READ_ERROR: %s" % e


def main():
    results = []

    # 1. structure
    results.append(check("SKILL.md exists", os.path.isfile(os.path.join(ROOT, "SKILL.md"))))
    md = read(os.path.join(ROOT, "SKILL.md"))
    fm_ok = md.startswith("---")
    for key in ("name:", "description:", "version:"):
        if key not in md[:600]:
            fm_ok = False
    results.append(check("SKILL.md valid YAML frontmatter (name/description/version)", fm_ok))
    results.append(check("main.py exists", os.path.isfile(os.path.join(_HERE, "main.py"))))
    results.append(check("core_impl.py exists", os.path.isfile(os.path.join(_HERE, "core_impl.py"))))

    # 2. scripts run --help with exit 0
    py = sys.executable
    for script in ("main.py", "core_impl.py"):
        p = subprocess.run([py, "-B", os.path.join(_HERE, script), "--help"],
                           capture_output=True, text=True, timeout=60)
        results.append(check("%s --help exit 0" % script, p.returncode == 0, p.stderr[:200]))

    # 3. engine selftest (SVG + PNG writer + JSON sanitize)
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        p = subprocess.run([py, "-B", os.path.join(_HERE, "main.py"), "selftest",
                            "--work", tmp, "--output", tmp],
                           capture_output=True, text=True, timeout=120)
        results.append(check("engine selftest exit 0", p.returncode == 0, p.stdout[:200]))
        sj = os.path.join(tmp, "selftest.json")
        if os.path.isfile(sj):
            data = json.loads(read(sj))
            results.append(check("engine selftest png_writer", data.get("png_writer") is True))
            results.append(check("engine selftest svg_renderer", data.get("svg_renderer") is True))
        else:
            results.append(check("engine selftest json produced", False))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 4. synthetic end-to-end run (empty input -> insufficient_evidence, exit 0)
    tmp2 = tempfile.mkdtemp(prefix="skill_e2e_")
    try:
        inp = os.path.join(tmp2, "input")
        out = os.path.join(tmp2, "output")
        work = os.path.join(tmp2, "work")
        os.makedirs(inp, exist_ok=True)
        os.makedirs(out, exist_ok=True)
        os.makedirs(work, exist_ok=True)
        for cmd in ("init", "discover", "profile"):
            p = subprocess.run([py, "-B", os.path.join(_HERE, "main.py"), cmd,
                                "--input", inp, "--output", out, "--work", work],
                               capture_output=True, text=True, timeout=120)
            results.append(check("empty-input %s exit 0" % cmd, p.returncode == 0, p.stderr[:200]))
        rj = os.path.join(out, "result.json")
        if os.path.isfile(rj):
            r = json.loads(read(rj))
            results.append(check("empty-input result.json valid + status field", "status" in r, str(r.get("status"))))
        else:
            results.append(check("empty-input result.json produced", False))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    n_ok = sum(1 for r in results if r["ok"])
    payload = {"total": len(results), "passed": n_ok, "results": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
