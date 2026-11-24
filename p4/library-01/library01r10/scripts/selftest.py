#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - run the pipeline against synthetic fixtures and verify the
invariants the rubric cares about. Only the standard library is used.

The fixtures are generated inside a temporary directory and never touch
/outputs or the real /input; the printed results are test results, not
official case answers.

Usage:
    python3 -B scripts/selftest.py [--quick]
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

import core_impl  # noqa: E402


def _mk(path: str, content: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def _case(case_id: str, contract: dict) -> dict:
    return {"case_id": case_id, "output_contract": contract}


def _build_fixture(root: str) -> dict:
    case = _case("challenge-self-test", {
        "summary": "text", "file_count": "integer", "duplicate_count":
        "integer", "broken_link_count": "integer", "notes": "list"})
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    task = ("# Self-test task\n\n## 版本说明\n"
            "draft-v1 被 draft-v2 取代；扫描件 scan.pdf 为终版。\n\n"
            "- challenge_id: `challenge-self-test`\n")
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
        fh.write(task)
    mat = os.path.join(root, "input", "materials")
    a = _mk(os.path.join(mat, "report-draft-v1.pdf"), b"%PDF-1.4 test one\n")
    _mk(os.path.join(mat, "report-draft-v2.pdf"), b"%PDF-1.4 test one\n")
    _mk(os.path.join(mat, "scan.pdf"), b"scan final unique content\n")
    # Duplicate content, different name.
    _mk(os.path.join(mat, "data-2024-01-01.csv"), "col1,col2\n1,2\n".encode())
    _mk(os.path.join(mat, "data-backup.csv"), "col1,col2\n1,2\n".encode())
    # Catalog manifest (JSON) that references two of them.
    manifest = os.path.join(root, "input", "manifest.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump([{"path": "materials/scan.pdf",
                    "sha256": hashlib.sha256(b"scan final unique content\n")
                    .hexdigest()},
                   {"path": "materials/missing.txt"}], fh, ensure_ascii=False)
    # Empty directory must survive the tree.
    os.makedirs(os.path.join(root, "input", "empty"), exist_ok=True)
    # Broken symlink (best effort; skip if the platform refuses).
    link = os.path.join(root, "input", "materials", "dangling-link.pdf")
    try:
        os.symlink(os.path.join(root, "input", "no-such.pdf"), link)
    except (OSError, NotImplementedError):
        pass
    return {"case": case, "root": root, "task": task, "a": a}


def _run_all() -> int:
    failures = 0
    tmp = tempfile.mkdtemp(prefix="selftest-lib-")
    try:
        fix = _build_fixture(tmp)
        root = fix["root"]
        case_path = os.path.join(root, "case.json")
        task_path = os.path.join(root, "task.md")
        input_root = os.path.join(root, "input")
        out = os.path.join(root, "out")
        work = os.path.join(root, "work")

        # 1) skeleton-first: partial result must exist before any scan.
        rc = core_impl.run_pipeline(case_path, task_path, input_root, out, work,
                                    "", skeleton_only=True)
        assert rc == 0, "skeleton exit code"
        skel = json.load(open(os.path.join(out, "result.json"),
                              encoding="utf-8"))
        assert skel["case_id"] == "challenge-self-test"
        assert skel["status"] == "partial"
        for key in ("summary", "file_count", "duplicate_count",
                    "broken_link_count", "notes"):
            assert key in skel, "skeleton missing contract key %s" % key
        print("ok  skeleton: partial result with all contract keys first")

        # 2) full run.
        rc = core_impl.run_pipeline(case_path, task_path, input_root, out, work,
                                    "", skeleton_only=False)
        assert rc == 0, "run exit code"
        result = json.load(open(os.path.join(out, "result.json"),
                                 encoding="utf-8"))
        assert result["case_id"] == "challenge-self-test"
        assert result["status"] in ("ok", "partial")
        assert isinstance(result["sources"], list)
        assert isinstance(result["change_log"], list)
        assert isinstance(result["uncertainty"], list)
        ir = json.load(open(os.path.join(work, "inventory.ir.json"),
                            encoding="utf-8"))
        # Duplicate group: data-2024-01-01.csv == data-backup.csv.
        assert ir["duplicates"]["total_groups"] >= 1
        # Reconciliation: scan.pdf matched, missing.txt missing.
        assert ir["reconciliation"]["applicable"] is True
        assert ir["reconciliation"]["counts"]["missing"] >= 1
        assert ir["reconciliation"]["counts"]["matches"] >= 1
        # Version notes parsed from task.md.
        assert ir["version_notes"]["count"] >= 1
        print("ok  run: analysis stages produced evidence")

        # 3) determinism: re-run produces the same canonical IR content except
        #    timestamps/change_log timestamps.
        out2 = os.path.join(root, "out2")
        work2 = os.path.join(root, "work2")
        core_impl.run_pipeline(case_path, task_path, input_root, out2, work2,
                               "", skeleton_only=False)
        ir2 = json.load(open(os.path.join(work2, "inventory.ir.json"),
                             encoding="utf-8"))
        canon = json.dumps(ir["entries"], sort_keys=True)
        canon2 = json.dumps(ir2["entries"], sort_keys=True)
        assert canon == canon2, "entries not deterministic"
        assert ir["duplicates"] == ir2["duplicates"], "duplicates not deterministic"
        assert ir["versions"] == ir2["versions"], "versions not deterministic"
        print("ok  determinism: two runs yield identical canonical content")

        # 4) check passes on the produced result.
        rc = core_impl._check(os.path.join(out, "result.json"), case_path,
                              task_path)
        assert rc == 0, "check should pass"
        print("ok  check: result.json passes validation")

        # 5) empty input -> insufficient_evidence, exit 0.
        empty_in = os.path.join(root, "empty-input")
        os.makedirs(empty_in, exist_ok=True)
        out3 = os.path.join(root, "out3")
        rc = core_impl.run_pipeline(case_path, task_path, empty_in, out3, work,
                                    "", skeleton_only=False)
        assert rc == 0
        r3 = json.load(open(os.path.join(out3, "result.json"),
                            encoding="utf-8"))
        assert r3["status"] == "insufficient_evidence", r3["status"]
        print("ok  empty input: status=insufficient_evidence")

        # 6) missing case.json -> input_error.
        out4 = os.path.join(root, "out4")
        rc = core_impl.run_pipeline(os.path.join(root, "nope.json"), task_path,
                                    input_root, out4, work, "",
                                    skeleton_only=False)
        assert rc == 0
        r4 = json.load(open(os.path.join(out4, "result.json"),
                            encoding="utf-8"))
        assert r4["status"] == "input_error", r4["status"]
        print("ok  missing case.json: status=input_error")

        # 7) bad JSON case.json degrades without crashing.
        bad_case = os.path.join(root, "bad.json")
        with open(bad_case, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        out5 = os.path.join(root, "out5")
        rc = core_impl.run_pipeline(bad_case, task_path, input_root, out5, work,
                                    "", skeleton_only=False)
        assert rc == 0
        r5 = json.load(open(os.path.join(out5, "result.json"),
                            encoding="utf-8"))
        assert r5["status"] in ("partial", "input_error")
        print("ok  bad case.json: degraded, no crash")

        # 8) CLI wiring through main.py.
        if os.path.exists(os.path.join(HERE, "main.py")):
            import runpy
            rc = core_impl.cli_main("check",
                                    ["--result", os.path.join(out, "result.json"),
                                     "--case-json", case_path,
                                     "--task-path", task_path])
            assert rc == 0
            print("ok  cli: cli_main('check') wiring works")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("selftest: %s" % ("ALL PASS" if failures == 0 else "FAILURES"))
    return failures


if __name__ == "__main__":
    raise SystemExit(_run_all())
