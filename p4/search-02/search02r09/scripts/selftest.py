#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Selftest for research-question-retrieval: runs main.py in-process against
temporary inputs (empty dir, bad JSON, bad encoding, idempotency) and asserts
it never crashes and always writes a valid result.json. Pure standard library.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import main  # noqa: E402


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def mkroot():
    root = tempfile.mkdtemp(prefix="rqr_selftest_")
    for sub in ("input", "output", "work"):
        os.makedirs(os.path.join(root, sub))
    return root


results = []


def scenario_empty():
    root = mkroot()
    rc = main.main(["run", "--input", root + "/input", "--output",
                    root + "/output", "--work", root + "/work"])
    check("empty-input exit 0", rc == 0)
    r = _load(os.path.join(root, "output", "result.json"))
    check("empty-input valid status",
          r.get("status") in ("insufficient_evidence", "partial", "input_error"))
    shutil.rmtree(root, ignore_errors=True)


def scenario_bad_json():
    root = mkroot()
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as f:
        f.write('{"case_id": "c1", "broken": ')
    rc = main.main(["run", "--input", root + "/input", "--output",
                    root + "/output", "--work", root + "/work"])
    check("bad-json exit 0", rc == 0)
    r = _load(os.path.join(root, "output", "result.json"))
    check("bad-json recorded in errors", len(r.get("errors", [])) >= 1)
    shutil.rmtree(root, ignore_errors=True)


def scenario_bad_encoding():
    root = mkroot()
    # latin-1 bytes that are invalid UTF-8 (é = 0xE9)
    with open(os.path.join(root, "input", "case.json"), "wb") as f:
        f.write(b'{"case_id": "c-\xe9", "output_contract": {"answer": null}}')
    rc = main.main(["run", "--input", root + "/input", "--output",
                    root + "/output", "--work", root + "/work"])
    check("bad-encoding exit 0", rc == 0)
    r = _load(os.path.join(root, "output", "result.json"))
    check("bad-encoding contract backfilled", "answer" in r and r.get("case_id") is not None)
    shutil.rmtree(root, ignore_errors=True)


def scenario_contract():
    root = mkroot()
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "c-x", "output_contract": {
            "answer": None, "evidence_span": [], "confidence": None}}, f)
    main.main(["run", "--input", root + "/input", "--output", root + "/output",
               "--work", root + "/work"])
    with open(os.path.join(root, "work", "cv.json"), "w", encoding="utf-8") as f:
        json.dump({"answer": "demo", "confidence": 0.5}, f)
    main.main(["set-contract", "--file", root + "/work/cv.json",
               "--work", root + "/work"])
    main.main(["assemble", "--input", root + "/input", "--output",
               root + "/output", "--work", root + "/work"])
    r = _load(os.path.join(root, "output", "result.json"))
    check("contract fields present", all(k in r for k in
          ("answer", "evidence_span", "confidence")))
    check("contract values backfilled", r.get("answer") == "demo" and
          "evidence_span" in r and r.get("evidence_span") is None)
    shutil.rmtree(root, ignore_errors=True)


def scenario_idempotent():
    root = mkroot()
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "c-idem", "task": "demo"}, f)
    with open(os.path.join(root, "input", "task.md"), "w", encoding="utf-8") as f:
        f.write("# demo task\n")
    args = ["run", "--input", root + "/input", "--output", root + "/output",
            "--work", root + "/work"]
    main.main(args)
    with open(os.path.join(root, "output", "result.json"), "rb") as f:
        a = f.read()
    main.main(["assemble", "--input", root + "/input", "--output",
               root + "/output", "--work", root + "/work"])
    with open(os.path.join(root, "output", "result.json"), "rb") as f:
        b = f.read()
    check("idempotent re-assemble", a == b)
    shutil.rmtree(root, ignore_errors=True)


def scenario_help():
    try:
        main.main(["--help"])
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    check("--help exit 0", code == 0)


def scenario_selfcheck():
    root = mkroot()
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "c-sc"}, f)
    main.main(["run", "--input", root + "/input", "--output", root + "/output",
               "--work", root + "/work"])
    rc = main.main(["selfcheck", "--output", root + "/output", "--work",
                    root + "/work"])
    check("selfcheck exit 0", rc == 0)
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    scenario_help()
    scenario_empty()
    scenario_bad_json()
    scenario_bad_encoding()
    scenario_contract()
    scenario_idempotent()
    scenario_selfcheck()
    n_pass = sum(1 for _, ok, _ in results if ok)
    print("\nselftest: %d/%d PASS" % (n_pass, len(results)))
    sys.exit(0 if n_pass == len(results) else 1)