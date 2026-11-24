#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py - local self-check for the library-repair skill.

Creates a small synthetic fixture under a temp directory (NOT a formal case,
and never shipped as an answer), runs the pipeline, asserts the contract and
artifacts, exercises the documented degradation paths, and cleans up.

Usage: python -B scripts/selftest.py
Exit code 0 = all checks passed; 1 = one or more checks failed.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(data)


def _run(argv):
    return core_impl.main(argv)


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _strip_time(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "runtime" or k == "time":
                continue
            out[k] = _strip_time(v)
        return out
    if isinstance(obj, list):
        return [_strip_time(x) for x in obj]
    return obj


def build_fixture(base: str):
    root = os.path.join(base, "input")
    os.makedirs(os.path.join(root, "materials", "data"), exist_ok=True)
    os.makedirs(os.path.join(root, "materials", "empty_dir"), exist_ok=True)

    a = b"hello world\n"
    b = b"hello world\n"          # duplicate of a.txt
    v1 = b"report v1 content\n"
    v2 = b"report v2 content - changed\n"

    _write(os.path.join(root, "materials", "a.txt"), a)
    _write(os.path.join(root, "materials", "b.txt"), b)
    _write(os.path.join(root, "materials", "report_v1.pdf"), v1)
    _write(os.path.join(root, "materials", "report_v2.pdf"), v2)
    _write(os.path.join(root, "materials", "data", "notes.md"),
           "# notes\nold.txt -> new.txt (replaced for review)\n")
    _write(os.path.join(root, "materials", "bad.json"), "{this is not valid json")
    _write(os.path.join(root, "materials", "bad_enc.bin"), b"\xff\xfe\x00not text\x80\x81")

    # Symbolic link (best-effort; Windows may disallow without privileges).
    link_created = False
    try:
        os.symlink(os.path.join(root, "materials", "a.txt"),
                   os.path.join(root, "materials", "link_a.txt"))
        link_created = True
    except (OSError, NotImplementedError):
        pass

    manifest = {
        "files": [
            {"path": "materials/a.txt", "sha256": _sha(a)},
            {"path": "materials/missing.txt", "sha256": "deadbeef"},
        ]
    }
    _write(os.path.join(root, "materials", "manifest.json"),
           json.dumps(manifest, ensure_ascii=False))

    case = {
        "case_id": "case-selftest-001",
        "output_contract": {
            "files": [],
            "summary": {},
            "duplicate_groups": [],
            "version_candidates": [],
            "reconciliation": {},
            "answers": [],
        },
    }
    _write(os.path.join(root, "case.json"), json.dumps(case, ensure_ascii=False))

    task = (
        "# 科研资料库与文件管理\n"
        "- challenge_id: `challenge-2bfd9194`\n"
        "## 行政版本说明\n"
        "old.txt -> new.txt (replaces old for review)\n"
        "report_v1.pdf -> report_v2.pdf\n"
    )
    _write(os.path.join(root, "task.md"), task)
    return root, link_created


def test_full(base, root, link_created):
    out = os.path.join(base, "out_full")
    work = os.path.join(base, "work_full")
    rc = _run(["--case-json", os.path.join(root, "case.json"),
               "--task-path", os.path.join(root, "task.md"),
               "--input-root", root, "--output-dir", out, "--work-dir", work])
    assert rc == 0, f"full pipeline rc={rc}"
    result = _load(os.path.join(out, "result.json"))
    assert result["case_id"] == "case-selftest-001", "case_id mismatch"
    assert result["status"] == "ok", f"unexpected status {result['status']}"
    # Contract fields present at top level.
    for f in ("files", "summary", "duplicate_groups", "version_candidates",
              "reconciliation", "answers"):
        assert f in result, f"contract field {f} missing from result"
    assert result["summary"]["file_count"] >= 5, "file_count wrong"
    assert result["summary"]["duplicate_group_count"] >= 1, "duplicate group not detected"
    assert result["summary"]["version_candidate_count"] >= 1, "version candidate not detected"
    assert len(result["change_log"]) >= 2, "change_log missing entries"
    assert result.get("_contract_meta", {}).get("total_fields") == 6, "contract meta wrong"
    if link_created:
        assert result["summary"]["broken_symlink_count"] >= 0, "broken symlink count key missing"
    for artifact in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                     "recovery_report.json", "library_index.json", "library_manifest.md",
                     "operation_log.jsonl", "contract_alignment.json"):
        assert os.path.exists(os.path.join(out, artifact)), f"artifact {artifact} missing"
    # Reconciliation: a.txt match + missing.txt missing.
    assert result["reconciliation"]["counts"]["matches"] >= 1, "reconcile match missing"
    assert result["reconciliation"]["counts"]["missing"] >= 1, "reconcile missing missing"
    # --check passes.
    rc2 = _run(["--check", os.path.join(out, "result.json"),
                "--case-json", os.path.join(root, "case.json")])
    assert rc2 == 0, f"--check rc={rc2}"
    print("  [PASS] full pipeline + contract alignment + artifacts + --check")
    return result


def test_determinism(base, root):
    out1 = os.path.join(base, "out_det1")
    out2 = os.path.join(base, "out_det2")
    _run(["--input-root", root, "--output-dir", out1, "--work-dir", os.path.join(base, "w1")])
    _run(["--input-root", root, "--output-dir", out2, "--work-dir", os.path.join(base, "w2")])
    r1 = _strip_time(_load(os.path.join(out1, "result.json")))
    r2 = _strip_time(_load(os.path.join(out2, "result.json")))
    assert r1 == r2, "non-deterministic result.json"
    print("  [PASS] deterministic re-run (timestamps stripped)")


def test_skeleton(base, root):
    out = os.path.join(base, "out_skel")
    rc = _run(["--case-json", os.path.join(root, "case.json"),
               "--task-path", os.path.join(root, "task.md"),
               "--input-root", root, "--output-dir", out,
               "--work-dir", os.path.join(base, "w_skel"), "--skeleton-only"])
    assert rc == 0, f"skeleton rc={rc}"
    result = _load(os.path.join(out, "result.json"))
    assert result["case_id"] == "case-selftest-001", "skeleton case_id missing"
    assert result["status"] in ("partial", "ok"), "skeleton status wrong"
    assert "change_log" in result, "skeleton change_log missing"
    print("  [PASS] --skeleton-only writes honest partial result first")


def test_empty_input(base):
    empty = os.path.join(base, "empty_input")
    os.makedirs(empty, exist_ok=True)
    _write(os.path.join(empty, "case.json"),
           json.dumps({"case_id": "case-empty-001",
                       "output_contract": {"files": []}}, ensure_ascii=False))
    _write(os.path.join(empty, "task.md"), "# empty case\n")
    out = os.path.join(base, "out_empty")
    rc = _run(["--case-json", os.path.join(empty, "case.json"),
               "--task-path", os.path.join(empty, "task.md"),
               "--input-root", empty, "--output-dir", out,
               "--work-dir", os.path.join(base, "w_empty")])
    assert rc == 0, f"empty input rc={rc}"
    result = _load(os.path.join(out, "result.json"))
    assert result["status"] == "insufficient_evidence", f"empty status {result['status']}"
    print("  [PASS] empty input -> insufficient_evidence, exit 0")


def test_missing_case(base):
    root = os.path.join(base, "missing_case")
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "task.md"), "# no case\n")
    out = os.path.join(base, "out_mc")
    rc = _run(["--input-root", root, "--output-dir", out, "--work-dir", os.path.join(base, "w_mc")])
    assert rc == 0, f"missing case rc={rc}"
    result = _load(os.path.join(out, "result.json"))
    assert result["status"] == "input_error", f"missing case status {result['status']}"
    print("  [PASS] missing case.json -> input_error")


def test_bad_json(base):
    root = os.path.join(base, "bad_case")
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "case.json"), "{bad json")
    _write(os.path.join(root, "task.md"), "# task\n- challenge_id: `challenge-x`\n")
    out = os.path.join(base, "out_bad")
    rc = _run(["--case-json", os.path.join(root, "case.json"),
               "--task-path", os.path.join(root, "task.md"),
               "--input-root", root, "--output-dir", out,
               "--work-dir", os.path.join(base, "w_bad")])
    assert rc == 0, f"bad json rc={rc}"
    result = _load(os.path.join(out, "result.json"))
    assert result["case_id"] == "challenge-x", "case_id not recovered from task.md"
    assert any("case.json" in str(e) for e in result.get("errors", [])), "bad json not recorded"
    print("  [PASS] bad case.json recorded in errors, case_id recovered from task.md")


def main():
    base = tempfile.mkdtemp(prefix="library-skill-selftest-")
    try:
        root, link_created = build_fixture(base)
        test_full(base, root, link_created)
        test_determinism(base, root)
        test_skeleton(base, root)
        test_empty_input(base)
        test_missing_case(base)
        test_bad_json(base)
        print("\nALL SELFTESTS PASSED")
        return 0
    except AssertionError as e:
        print(f"\nSELFTEST FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"\nSELFTEST ERROR: {e!r}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
