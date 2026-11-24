#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the grant-proposal-writer skill (Python stdlib only).

Scenarios:
  1. main.py --help exits 0 (interface usable).
  2. init on an EMPTY input dir -> valid partial result.json, exit 0.
  3. verify on that result -> ok, all required keys present.
  4. init on a BAD-JSON case.json -> no crash, error recorded.
  5. SKILL.md and references/ present.
Exit 0 on success, 1 on failure.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

# Force UTF-8 so Chinese paths never crash a GBK console during local testing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")

REQUIRED = ("case_id", "status", "artifact", "fact_source_map",
            "version_boundary", "sources", "uncertainty", "change_log")


def run(args, cwd=None):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-B"] + args, cwd=cwd,
                          capture_output=True, text=True, env=env,
                          encoding="utf-8", errors="replace")


def check(name, cond, detail=""):
    print("%s %s %s" % ("[ok]" if cond else "[FAIL]", name, detail))
    return bool(cond)


def main() -> int:
    ok = True

    ok &= check("help", run([MAIN, "--help"]).returncode == 0)

    # empty-input scenario
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        outp = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        r = run([MAIN, "init", "--input", inp, "--out", outp, "--work", work])
        ok &= check("init-empty", r.returncode == 0, r.stdout.strip()[:120])
        result = os.path.join(outp, "result.json")
        ok &= check("result-exists", os.path.exists(result))
        if os.path.exists(result):
            with open(result, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            missing = [k for k in REQUIRED if k not in data]
            ok &= check("required-keys", not missing, ",".join(missing) or "all present")
            ok &= check("partial-status", data.get("status") == "partial",
                        str(data.get("status")))
        r = run([MAIN, "verify", "--result", result])
        ok &= check("verify-ok", r.returncode == 0, r.stdout.strip()[:120])

    # bad-JSON scenario
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        outp = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json ")
        r = run([MAIN, "init", "--input", inp, "--out", outp, "--work", work])
        ok &= check("init-bad-json-no-crash", r.returncode == 0, r.stdout.strip()[:120])
        result = os.path.join(outp, "result.json")
        if os.path.exists(result):
            with open(result, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            ok &= check("bad-json-recorded", "errors" in data, str(data.get("errors")))

    # documentation presence
    ok &= check("skill-md", os.path.exists(os.path.join(ROOT, "SKILL.md")))
    ok &= check("rules-md", os.path.exists(os.path.join(ROOT, "references", "rules.md")))
    ok &= check("checklist-md", os.path.exists(os.path.join(ROOT, "references", "checklist.md")))

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
