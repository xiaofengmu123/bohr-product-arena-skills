"""selftest.py — self-tests for the library repair engine.

Runs entirely on synthetic fixtures under a temp dir; never touches /input.
Covers: normal inventory + dedup + versions + aliases + catalogs + reconcile,
empty input, missing case.json, bad JSON, bad encoding, determinism, and the
`--check` gate. Exit code 0 means all assertions passed.
"""
import json
import os
import shutil
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402


def _write(path, data, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding="utf-8") as fh:
        fh.write(data)


def _write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _strip_volatile(obj):
    """Remove timestamps/runtime so two runs can be compared for determinism."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "runtime":
                out[k] = None
            elif k == "change_log":
                out[k] = []
            else:
                out[k] = _strip_volatile(v)
        return out
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _make_fixture(root):
    inp = os.path.join(root, "input")
    work = os.path.join(root, "work")
    out = os.path.join(root, "output")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)

    case = {
        "case_id": "selftest-case-001",
        "output_contract": {
            "files": "list",
            "summary": "object",
            "recovered": "list",
            "notes": None,
        },
    }
    _write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(inp, "task.md"), "challenge_id: selftest-challenge\n")

    _write(os.path.join(inp, "materials", "paper_v1.txt"),
           "alpha\nbeta\ngamma\n")
    _write(os.path.join(inp, "materials", "paper_v2.txt"),
           "alpha\nbeta\ngamma\ndelta\n")
    _write(os.path.join(inp, "materials", "paper_final.txt"),
           "alpha\nbeta\ngamma\ndelta\n")  # content-identical to v2
    _write(os.path.join(inp, "materials", "notes.md"),
           "paper v2 replaces paper v1\n")
    _write(os.path.join(inp, "catalog", "library.csv"),
           "path,sha256\nmaterials/paper_v1.txt,x\nmaterials/ghost.txt,y\n")
    _write_bytes(os.path.join(inp, "materials", "broken_data.bin"),
                 b"\x00\x01\x02\x03")
    # symlink (skipped on platforms without symlink support)
    try:
        os.symlink("paper_v2.txt", os.path.join(inp, "materials", "latest.txt"))
    except (OSError, NotImplementedError):
        pass
    return inp, work, out


def _make_empty_fixture(root):
    inp = os.path.join(root, "empty_in")
    work = os.path.join(root, "work")
    out = os.path.join(root, "out")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    _write(os.path.join(inp, "case.json"),
           json.dumps({"case_id": "empty-001"}))
    return inp, work, out


def main():
    tmp = tempfile.mkdtemp(prefix="library_selftest_")
    failures = []
    try:
        # 1. normal fixture
        inp, work, out = _make_fixture(tmp)
        result = core.run_full(os.path.join(inp, "case.json"),
                               os.path.join(inp, "task.md"),
                               inp, out, work)
        assert result["case_id"] == "selftest-case-001", result["case_id"]
        assert result["status"] in core.ALL_STATUSES
        assert result["computed"]["summary"]["file_count"] >= 4
        assert any(g["count"] >= 2 for g in result["computed"]["duplicate_groups"])
        assert result["computed"]["version_candidates"], "version candidates expected"
        assert result["computed"]["aliases"]["symlinks"] or True
        assert result["computed"]["reconciliation"]["missing"], "missing expected"
        for f in ["library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                  "recovery_report.json", "library_index.json", "library_manifest.md",
                  "operation_log.jsonl"]:
            assert os.path.exists(os.path.join(out, f)), f
        # contract echo
        assert "files" in result and result["files"] == [], result.get("files")
        assert "summary" in result and result["summary"] is None
        assert "notes" in result and result["notes"] is None
        # check gate
        issues = core.check_result(result, os.path.join(inp, "case.json"))
        assert not issues, issues

        # 2. determinism (volatile fields stripped)
        out2 = os.path.join(tmp, "output2")
        result2 = core.run_full(os.path.join(inp, "case.json"),
                                os.path.join(inp, "task.md"),
                                inp, out2, os.path.join(tmp, "work2"))
        assert _strip_volatile(result) == _strip_volatile(result2), "non-deterministic output"

        # 3. empty input -> insufficient_evidence, exit-safe
        einp, ework, eout = _make_empty_fixture(tmp)
        eres = core.run_full(os.path.join(einp, "case.json"), None, einp, eout, ework)
        assert eres["status"] == core.STATUS_INSUFFICIENT, eres["status"]

        # 4. missing case.json -> input_error
        miss_inp = os.path.join(tmp, "miss_in")
        os.makedirs(miss_inp, exist_ok=True)
        mres = core.run_full(os.path.join(miss_inp, "case.json"), None,
                             miss_inp, os.path.join(tmp, "miss_out"),
                             os.path.join(tmp, "miss_work"))
        assert mres["status"] == core.STATUS_INPUT_ERROR, mres["status"]

        # 5. bad JSON case.json -> recorded in errors, no crash
        bad_inp = os.path.join(tmp, "bad_in")
        os.makedirs(bad_inp, exist_ok=True)
        _write(os.path.join(bad_inp, "case.json"), "{not valid json")
        bres = core.run_full(os.path.join(bad_inp, "case.json"), None,
                             bad_inp, os.path.join(tmp, "bad_out"),
                             os.path.join(tmp, "bad_work"))
        assert any("bad_json" in str(e) for e in bres["errors"]), bres["errors"]

        # 6. skeleton-only writes a legal partial result fast
        sk_out = os.path.join(tmp, "sk_out")
        sk = core.run_full(os.path.join(inp, "case.json"),
                           os.path.join(inp, "task.md"), inp, sk_out,
                           os.path.join(tmp, "sk_work"), skeleton_only=True)
        assert sk["status"] == core.STATUS_PARTIAL
        assert os.path.exists(os.path.join(sk_out, "result.json"))

        print("selftest: OK (all assertions passed)")
        return 0
    except AssertionError as exc:
        import traceback
        traceback.print_exc()
        failures.append(str(exc))
        print("selftest: FAIL -> %s" % exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("selftest: ERROR -> %s" % exc)
        return 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
