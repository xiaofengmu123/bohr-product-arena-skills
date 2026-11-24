#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package self-test for the slide skill.

Runs the contract engine and CLI end-to-end on a temporary fixture and checks:

  S1  materialize/build_skeleton mirror every contract leaf
  S2  `init` writes a complete result.json + inventory
  S3  `validate` reports missing required fields (exit 2) before filling
  S4  a filled result validates clean (exit 0)
  S5  `init` is idempotent: rerunning preserves work, one bootstrap entry
  S6  missing input degrades to input_error instead of crashing

Exit code 0 means all checks passed; 1 means a check failed.  No benchmark
scores or scientific data are produced or claimed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import contract  # noqa: E402


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    failures = []

    def check(name, cond, detail=""):
        status = "PASS" if cond else "FAIL"
        print("[{}] {} {}".format(status, name, detail))
        if not cond:
            failures.append(name)

    # S1: skeleton mirrors every contract leaf.
    schema = contract.DEFAULT_CONTRACT
    skeleton = contract.build_skeleton("selftest", schema)
    leaves = list(contract.walk_leaves(schema))
    mirrored = all(contract._path_key_exists(skeleton, p) for p, _r, _t in leaves)
    check("S1-skeleton-mirrors-contract", mirrored,
          "{} leaves".format(len(leaves)))

    # S2-S6: end-to-end CLI on a temporary fixture.
    tmp = tempfile.mkdtemp(prefix="slide-selftest-")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        case = {
            "case_id": "fixture-001",
            "output_contract": {
                "presentation": {"pptx_path": "!string", "pdf_path": "string"},
                "evidence_map": "!list",
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh)

        base = [sys.executable, "-B", os.path.join(SCRIPT_DIR, "main.py"),
                "--input", inp, "--output", out, "--work", work]

        r1 = run(base + ["init"])
        check("S2-init-exit0", r1.returncode == 0, r1.stdout.strip())
        res1 = contract.load_json(os.path.join(out, "result.json"))
        check("S2-result-created", res1 is not None)
        check("S2-contract-fields-present",
              res1 is not None and "presentation" in res1 and "evidence_map" in res1)

        r2 = run(base + ["validate"])
        check("S3-validate-flags-missing", r2.returncode == 2,
              "exit={}".format(r2.returncode))

        # Fill required fields, then validate again.
        res = contract.load_json(os.path.join(out, "result.json"))
        res["presentation"]["pptx_path"] = os.path.join(out, "deck.pptx")
        res["evidence_map"] = [{"page": 1, "claim": "fixture claim", "refs": [],
                                "evidence_status": "ok"}]
        with open(os.path.join(out, "deck.pptx"), "wb") as fh:
            fh.write(b"PK\x03\x04fixture")
        contract.write_json(os.path.join(out, "result.json"), res)

        r3 = run(base + ["validate"])
        check("S4-filled-validates", r3.returncode == 0,
              "exit={}".format(r3.returncode))

        r4 = run(base + ["init"])  # idempotent merge
        res4 = contract.load_json(os.path.join(out, "result.json"))
        bootstraps = [e for e in res4.get("change_log", [])
                      if isinstance(e, dict) and e.get("code") == "bootstrap"]
        kept = res4["presentation"]["pptx_path"] == os.path.join(out, "deck.pptx")
        check("S5-init-idempotent", r4.returncode == 0 and kept and len(bootstraps) == 1,
              "bootstraps={} kept={}".format(len(bootstraps), kept))

        # S6: empty input degrades gracefully.
        empty_in = os.path.join(tmp, "empty-input")
        empty_out = os.path.join(tmp, "empty-output")
        os.makedirs(empty_in)
        r5 = run([sys.executable, "-B", os.path.join(SCRIPT_DIR, "main.py"),
                  "--input", empty_in, "--output", empty_out,
                  "--work", os.path.join(tmp, "empty-work"), "init"])
        res5 = contract.load_json(os.path.join(empty_out, "result.json"))
        check("S6-empty-input-degrades",
              r5.returncode == 0 and res5 is not None and res5["status"] == "input_error")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 40)
    if failures:
        print("FAILED: {}".format(", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
