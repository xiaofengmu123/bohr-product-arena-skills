#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — robustness and correctness self-test for the library-repair skill.

Runs the skill against synthetic temporary cases (never against real case data)
and verifies: empty input, bad JSON, bad encoding, missing case.json, duplicate
detection, version candidates, catalog reconciliation, and run-to-run
determinism.  Exit code 0 means all checks passed.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name, detail))


def write(path, content, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def run_case(root, name, case_json=None, task_md=None):
    work = os.path.join(root, "work")
    out = os.path.join(root, "out")
    case_path = os.path.join(root, "case.json")
    task_path = os.path.join(root, "task.md")
    if case_json is not None:
        write(case_path, case_json)
    if task_md is not None:
        write(task_path, task_md)
    core.run_pipeline(case_path, task_path, os.path.join(root, "input"), out, work)
    return out


def scenario_empty_input():
    root = tempfile.mkdtemp(prefix="selftest-empty-")
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    write(os.path.join(root, "case.json"), json.dumps({"case_id": "t-empty", "output_contract": {"manifest": {}, "summary": {}}}))
    write(os.path.join(root, "task.md"), "# t\n整理资料库\n")
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    core.run_pipeline(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                      os.path.join(root, "input"), out, work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check("empty_input.status", res["status"] == "insufficient_evidence", res["status"])
    check("empty_input.contract_fields", "manifest" in res and "summary" in res)
    check("empty_input.case_id", res["case_id"] == "t-empty", str(res.get("case_id")))
    shutil.rmtree(root, ignore_errors=True)


def scenario_bad_json():
    root = tempfile.mkdtemp(prefix="selftest-badjson-")
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    write(os.path.join(root, "input", "a.txt"), "hello\n")
    write(os.path.join(root, "case.json"), "{ this is not json")
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    core.run_pipeline(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                      os.path.join(root, "input"), out, work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check("bad_json.status", res["status"] == "input_error", res["status"])
    check("bad_json.errors_recorded", any(e["error"].startswith("bad_json") for e in res.get("errors", [])))
    shutil.rmtree(root, ignore_errors=True)


def scenario_bad_encoding():
    root = tempfile.mkdtemp(prefix="selftest-badenc-")
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    write_bytes(os.path.join(root, "input", "broken.csv"), b"\xff\xfe\x00\x9cbroken,csv\n")
    write(os.path.join(root, "case.json"), json.dumps({"case_id": "t-enc", "output_contract": {"files": []}}))
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    core.run_pipeline(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                      os.path.join(root, "input"), out, work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check("bad_encoding.no_crash", "status" in res, str(res.get("status")))
    check("bad_encoding.file_recorded", any(f["name"] == "broken.csv" for f in res["computed"]["files"]))
    shutil.rmtree(root, ignore_errors=True)


def scenario_missing_case():
    root = tempfile.mkdtemp(prefix="selftest-nocase-")
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    write(os.path.join(root, "input", "x.txt"), "x")
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    core.run_pipeline(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                      os.path.join(root, "input"), out, work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check("missing_case.status", res["status"] == "input_error", res["status"])
    shutil.rmtree(root, ignore_errors=True)


def scenario_full():
    root = tempfile.mkdtemp(prefix="selftest-full-")
    inp = os.path.join(root, "input")
    os.makedirs(os.path.join(inp, "empty_dir"), exist_ok=True)
    os.makedirs(os.path.join(inp, "data"), exist_ok=True)
    content_a = "version one\nline2\n"
    content_b = "version two\nline2\n"
    write(os.path.join(inp, "paper.txt"), content_a)
    write(os.path.join(inp, "paper_v2.txt"), content_b)
    write(os.path.join(inp, "paper_v2_copy.txt"), content_b)
    write(os.path.join(inp, "data", "raw.csv"), "path,sha256\npaper.txt,\n")
    catalog = [
        {"path": "paper.txt", "sha256": hashlib.sha256(content_a.encode()).hexdigest()},
        {"path": "ghost.txt", "sha256": "0" * 64},
    ]
    write(os.path.join(inp, "catalog.json"), json.dumps(catalog))
    write(os.path.join(inp, "notes.txt"), "paper 版本 v2 替代 v1（草稿）")
    if hasattr(os, "symlink"):
        try:
            os.symlink("paper.txt", os.path.join(inp, "link_to_paper.txt"))
        except (OSError, NotImplementedError):
            pass
        try:
            os.symlink("missing_target.txt", os.path.join(inp, "broken_link.txt"))
        except (OSError, NotImplementedError):
            pass

    case = {"case_id": "t-full", "output_contract": {"files": [], "summary": {}, "duplicates": [], "recovery": {}}}
    write(os.path.join(root, "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(root, "task.md"), "# t\n整理资料库\n版本说明：paper v2 替代 v1\n")

    out1 = run_case(root, "full")
    out2_dir = os.path.join(root, "out2")
    work2 = os.path.join(root, "work2")
    core.run_pipeline(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                      inp, out2_dir, work2)

    r1 = json.load(open(os.path.join(out1, "result.json"), encoding="utf-8"))
    r2 = json.load(open(os.path.join(out2_dir, "result.json"), encoding="utf-8"))

    check("full.artifacts_exist", all(os.path.exists(os.path.join(out1, f)) for f in core.ARTIFACTS.values()))
    check("full.duplicate_detected", len(r1["computed"]["duplicate_groups"]) == 1,
          "groups=%d" % len(r1["computed"]["duplicate_groups"]))
    check("full.version_candidates", len(r1["computed"]["version_candidates"]) >= 1,
          "cands=%d" % len(r1["computed"]["version_candidates"]))
    check("full.reconciliation_missing", r1["computed"]["reconciliation"]["counts"]["missing"] >= 1,
          str(r1["computed"]["reconciliation"]["counts"]))
    check("full.contract_autofill", r1.get("duplicates") and len(r1.get("duplicates", [])) >= 1)
    check("full.case_id", r1["case_id"] == "t-full", str(r1.get("case_id")))
    check("full.check_passes", core.check_result(os.path.join(out1, "result.json"),
                                                 os.path.join(root, "case.json"))[0])

    # Determinism: strip runtime/change_log timestamps then compare.
    def strip(obj):
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items() if k not in ("runtime", "change_log")}
        if isinstance(obj, list):
            return [strip(x) for x in obj]
        return obj

    check("full.deterministic", strip(r1) == strip(r2))
    shutil.rmtree(root, ignore_errors=True)


def scenario_skeleton():
    root = tempfile.mkdtemp(prefix="selftest-skeleton-")
    os.makedirs(os.path.join(root, "input"), exist_ok=True)
    write(os.path.join(root, "case.json"), json.dumps({"case_id": "t-sk", "output_contract": {"x": None, "y": []}}))
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    core.run_skeleton(os.path.join(root, "case.json"), os.path.join(root, "task.md"), out, work)
    res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check("skeleton.status", res["status"] == "partial", res["status"])
    check("skeleton.contract_placeholders", res.get("x") is None and res.get("y") == [])
    check("skeleton.case_id", res["case_id"] == "t-sk")
    shutil.rmtree(root, ignore_errors=True)


def main():
    scenarios = [
        ("empty_input", scenario_empty_input),
        ("bad_json", scenario_bad_json),
        ("bad_encoding", scenario_bad_encoding),
        ("missing_case", scenario_missing_case),
        ("full", scenario_full),
        ("skeleton", scenario_skeleton),
    ]
    for name, fn in scenarios:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - self-test must not crash silently
            check(name + ".raised", False, repr(e))
    failed = [r for r in RESULTS if not r[1]]
    print("")
    print("selftest: %d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
