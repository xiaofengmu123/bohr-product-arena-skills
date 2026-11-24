#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the evidence-driven writing Skill (stdlib only).

Runs the real main.py pipeline against isolated temp directories covering the
robustness contract: empty input, bad JSON, missing parameters, oversized
files and a minimal normal case.  Exits 0 when all checks pass.

This is a *robustness* self-test only; it uses synthetic minimal inputs and
never pretends to be one of the three official fixed cases.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"

SCENARIOS = []


def scenario(name):
    def deco(fn):
        SCENARIOS.append((name, fn))
        return fn
    return deco


def _run(input_dir, extra=None):
    with tempfile.TemporaryDirectory(prefix="skill-selftest-") as tmp:
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "out")
        cmd = [sys.executable, "-B", str(MAIN), "run",
               "--input", input_dir, "--work", work, "--out", out,
               "--case", os.path.join(input_dir, "case.json"),
               "--task", os.path.join(input_dir, "task.md")]
        if extra:
            cmd.extend(extra)
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        result_path = os.path.join(out, "result.json")
        result = None
        if os.path.exists(result_path):
            try:
                with open(result_path, encoding="utf-8") as fh:
                    result = json.load(fh)
            except Exception as exc:
                result = {"_parse_error": str(exc)}
        return {"rc": run.returncode, "result": result, "stderr": run.stderr}


def _check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("| " + detail if detail else ""))
    return bool(cond)


@scenario("empty_input")
def empty_input():
    with tempfile.TemporaryDirectory(prefix="sk-e-") as d:
        r = _run(d)
    ok = r["rc"] == 0 and isinstance(r["result"], dict)
    status = (r["result"] or {}).get("status") if isinstance(r["result"], dict) else None
    ok = ok and status in ("insufficient_evidence", "input_error", "partial", "failed", "in_progress")
    return _check("empty input exits 0 with honest status", ok, f"status={status}")


@scenario("bad_json")
def bad_json():
    with tempfile.TemporaryDirectory(prefix="sk-b-") as d:
        Path(d, "case.json").write_text("{ this is not valid json", encoding="utf-8")
        Path(d, "task.md").write_text("# task\n", encoding="utf-8")
        r = _run(d)
    res = r["result"]
    ok = r["rc"] == 0 and isinstance(res, dict) and (res.get("errors") or res.get("degradations"))
    return _check("bad JSON degrades without crash", ok,
                  f"rc={r['rc']} errors={len((res or {}).get('errors') or [])}")


@scenario("missing_params")
def missing_params():
    # Point --input/--case at paths that do not exist.
    with tempfile.TemporaryDirectory(prefix="sk-m-") as d:
        r = _run(os.path.join(d, "does-not-exist"))
    res = r["result"]
    ok = r["rc"] == 0 and isinstance(res, dict) and "status" in (res or {})
    return _check("missing input paths still write valid result.json", ok,
                  f"status={(res or {}).get('status')}")


@scenario("oversized_file")
def oversized_file():
    with tempfile.TemporaryDirectory(prefix="sk-o-") as d:
        Path(d, "case.json").write_text(json.dumps({"id": "case-oversized"}), encoding="utf-8")
        Path(d, "task.md").write_text("# task\n", encoding="utf-8")
        # 3MB material exceeds the 2MB cap and must be skipped, not parsed.
        with open(os.path.join(d, "big.txt"), "wb") as fh:
            fh.write(b"x" * (3 * 1024 * 1024))
        Path(d, "small.md").write_text("claim: smoking correlates with cancer\n", encoding="utf-8")
        r = _run(d)
    res = r["result"]
    oversized_noted = any("big.txt" in str(x) for x in (res or {}).get("degradations") or [])
    ok = r["rc"] == 0 and isinstance(res, dict) and oversized_noted
    return _check("oversized file skipped and recorded", ok,
                  f"degradations={len((res or {}).get('degradations') or [])}")


@scenario("minimal_normal")
def minimal_normal():
    with tempfile.TemporaryDirectory(prefix="sk-n-") as d:
        Path(d, "case.json").write_text(json.dumps({
            "case_id": "case-min",
            "output_contract": {"deliverable": "manuscript", "sources": "evidence map", "change_log": "versions"},
        }), encoding="utf-8")
        Path(d, "task.md").write_text("# 写作任务\n请产出论断清单、证据表与成稿。\n", encoding="utf-8")
        Path(d, "material.md").write_text(
            "Section 1. A randomized trial of 200 patients showed treatment reduced mortality.\n"
            "Section 2. The cohort study found an association between diet and outcome.\n",
            encoding="utf-8",
        )
        r = _run(d)
    res = r["result"]
    contract_keys_ok = all(k in res for k in ("deliverable", "sources", "change_log")) if isinstance(res, dict) else False
    case_id_ok = (res or {}).get("case_id") == "case-min"
    ok = r["rc"] == 0 and isinstance(res, dict) and contract_keys_ok and case_id_ok
    return _check("normal case: contract keys + case_id present", ok,
                  f"status={(res or {}).get('status')} case_id={(res or {}).get('case_id')}")


def main():
    print(f"running {len(SCENARIOS)} robustness scenarios against {MAIN}\n")
    results = [fn() for _, fn in SCENARIOS]
    passed = sum(results)
    print(f"\n{passed}/{len(SCENARIOS)} passed")
    return 0 if passed == len(SCENARIOS) else 1


if __name__ == "__main__":
    sys.exit(main())
