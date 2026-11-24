#!/usr/bin/env python3
"""Self-test for the academic-writing skill (stdlib only).

Runs robustness scenarios against scripts/main.py and scripts/consistency.py in
isolated temp dirs and verifies that no scenario crashes and that a valid
result.json is produced.  Exits 0 when every scenario passes, 1 otherwise.

Scenarios: empty input, bad JSON, bad encoding, oversized file, missing case
file, --help, unknown flag, idempotent re-run, and consistency on a minimal draft.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
CONSISTENCY = os.path.join(HERE, "consistency.py")


def run(pyfile, args):
    return subprocess.run(
        [sys.executable, "-B", pyfile] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def read_result(out_dir):
    p = os.path.join(out_dir, "result.json")
    if not os.path.exists(p):
        return None, "missing result.json"
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "bad result.json: %s" % exc
    if not isinstance(data, dict):
        return None, "result.json not an object"
    return data, None


def scenario(name, fn):
    try:
        fn()
        print("PASS  %s" % name)
        return True
    except AssertionError as exc:
        print("FAIL  %s: %s" % (name, exc))
        return False
    except Exception as exc:  # noqa: BLE001 - self-test must report, not crash
        print("FAIL  %s: unexpected %r" % (name, exc))
        return False


def make_dirs():
    root = tempfile.mkdtemp(prefix="skill_selftest_")
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    for d in (inp, out, work):
        os.makedirs(d, exist_ok=True)
    return root, inp, out, work


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def check_base(root, inp, out, work):
    rc = run(MAIN, ["--case", os.path.join(inp, "case.json"),
                    "--task", os.path.join(inp, "task.md"),
                    "--output", out, "--work", work]).returncode
    assert rc == 0, "main.py exited %d" % rc
    result, err = read_result(out)
    assert result is not None, err
    for key in ("case_id", "status", "sources", "uncertainty",
                "pending_human", "change_log", "errors", "degradations"):
        assert key in result, "missing key %s" % key
    assert isinstance(result["change_log"], list) and result["change_log"], "empty change_log"
    return result


def s_empty():
    root, inp, out, work = make_dirs()
    try:
        # no case.json at all -> default path used
        rc = run(MAIN, ["--case", os.path.join(inp, "case.json"),
                        "--task", os.path.join(inp, "task.md"),
                        "--output", out, "--work", work]).returncode
        assert rc == 0, "main.py exited %d" % rc
        result, err = read_result(out)
        assert result is not None, err
        assert result["status"] in ("partial", "insufficient_evidence", "input_error"), result["status"]
        assert result["errors"], "expected recorded error for missing case.json"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def s_bad_json():
    root, inp, out, work = make_dirs()
    try:
        write(os.path.join(inp, "case.json"), "{ this is not json")
        write(os.path.join(inp, "task.md"), "# task\n")
        result = check_base(root, inp, out, work)
        assert result["errors"], "expected error for bad JSON"
        assert result["case_id"] == "case.json", result["case_id"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def s_bad_encoding():
    root, inp, out, work = make_dirs()
    try:
        write_bytes(os.path.join(inp, "case.json"), b"\xff\xfe\x00\x01 not utf8 \xff")
        write(os.path.join(inp, "task.md"), "# task\n")
        rc = run(MAIN, ["--case", os.path.join(inp, "case.json"),
                        "--task", os.path.join(inp, "task.md"),
                        "--output", out, "--work", work]).returncode
        assert rc == 0, "main.py exited %d" % rc
        result, err = read_result(out)
        assert result is not None, err
    finally:
        shutil.rmtree(root, ignore_errors=True)


def s_oversized():
    root, inp, out, work = make_dirs()
    try:
        write(os.path.join(inp, "case.json"), json.dumps({"id": "C1"}))
        write(os.path.join(inp, "task.md"), "# task\n")
        big = os.path.join(inp, "big.txt")
        write(big, "x" * 60000)
        result = check_base(root, inp, out, work)
        # default max is 2MB, so 60KB is not oversized; verify small cap path instead
        rc = run(MAIN, ["--case", os.path.join(inp, "case.json"),
                        "--task", os.path.join(inp, "task.md"),
                        "--output", out, "--work", work,
                        "--max-bytes", "100"]).returncode
        assert rc == 0, "main.py exited %d" % rc
        result2, err = read_result(out)
        assert result2 is not None, err
        assert any("oversize" in d for d in result2["degradations"]), result2["degradations"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def s_missing_case():
    root, inp, out, work = make_dirs()
    try:
        # input dir exists but case file missing
        rc = run(MAIN, ["--case", os.path.join(inp, "nope.json"),
                        "--task", os.path.join(inp, "task.md"),
                        "--output", out, "--work", work]).returncode
        assert rc == 0, "main.py exited %d" % rc
        result, err = read_result(out)
        assert result is not None, err
        assert result["errors"], "expected error for missing case file"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def s_help_and_unknown_flag():
    assert run(MAIN, ["--help"]).returncode == 0, "--help should exit 0"
    root, inp, out, work = make_dirs()
    try:
        write(os.path.join(inp, "case.json"), json.dumps({"id": "C1"}))
        write(os.path.join(inp, "task.md"), "# task\n")
        rc = run(MAIN, ["--case", os.path.join(inp, "case.json"),
                        "--task", os.path.join(inp, "task.md"),
                        "--output", out, "--work", work,
                        "--bogus-flag", "1"]).returncode
        # unknown flag is tolerated only if it was an allowed kwarg; argparse
        # would reject it.  We test that rejection is a clean exit, not a crash.
        _ = rc
        # Standard argparse rejects unknown args with rc=2, which is acceptable
        # ("缺参数/未知参数" must not corrupt state); ensure no crash traceback.
        assert rc in (0, 2), "unexpected rc %d" % rc
    finally:
        shutil.rmtree(root, ignore_errors=True)


def s_idempotent():
    root, inp, out, work = make_dirs()
    try:
        write(os.path.join(inp, "case.json"), json.dumps({"id": "C1", "output_contract": {
            "deliverable": "manuscript.md", "fact_source_map": "evidence_map.json",
            "version_limits": "change_log"}}))
        write(os.path.join(inp, "task.md"), "# task\n")
        write(os.path.join(inp, "paper.txt"), "n=30  p=0.04  10.0  doi: 10.1000/xyz123\n")
        check_base(root, inp, out, work)
        first, _ = read_result(out)
        # simulate agent refinement before re-run
        first["sources"].append({"ref_id": "mat-paper-txt", "locator": "mat-paper-txt@L1", "quote": "n=30"})
        first["uncertainty"].append("人工待确认项示例")
        first["status"] = "partial"
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(first, fh, ensure_ascii=False)
        rc = run(MAIN, ["--case", os.path.join(inp, "case.json"),
                        "--task", os.path.join(inp, "task.md"),
                        "--output", out, "--work", work]).returncode
        assert rc == 0, "main.py exited %d" % rc
        second, err = read_result(out)
        assert second is not None, err
        assert len(second["sources"]) == 1, "re-run must preserve sources"
        assert len(second["uncertainty"]) == 1, "re-run must preserve uncertainty"
        assert len(second["change_log"]) > len(first["change_log"]), "change_log must grow"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def s_consistency():
    root, inp, out, work = make_dirs()
    try:
        draft = os.path.join(out, "manuscript.md")
        write(draft, "# 摘要\n\n结论见 [S:mat-x]。参考文献见 [C:missing-ref]。\n\n"
                     "# 方法\n\n# 结果\n\n# 讨论\n\n# 结论\n\n"
                     "# 参考文献\n\n[C:real]: Doe J. Title. J 2020;1(1):1. DOI unknown\n")
        rc = run(CONSISTENCY, ["--draft", draft, "--index",
                               os.path.join(work, "material_index.json"),
                               "--work", work]).returncode
        assert rc == 0, "consistency.py exited %d" % rc
        rep = os.path.join(work, "consistency_report.json")
        with open(rep, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert "dangling_sources" in data["checks"], data["checks"]
        assert "mat-x" in data["checks"]["dangling_sources"]
        assert "missing-ref" in data["checks"]["dangling_citations"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    scenarios = [
        ("empty_input", s_empty),
        ("bad_json", s_bad_json),
        ("bad_encoding", s_bad_encoding),
        ("oversized_file", s_oversized),
        ("missing_case_file", s_missing_case),
        ("help_and_unknown_flag", s_help_and_unknown_flag),
        ("idempotent_rerun", s_idempotent),
        ("consistency_check", s_consistency),
    ]
    results = [(name, scenario(name, fn)) for name, fn in scenarios]
    passed = sum(1 for _, ok in results if ok)
    print("\nselftest: %d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
