# -*- coding: utf-8 -*-
"""selftest.py - deterministic self-test for the document library skill.

Creates a throwaway input tree under the system temp dir, runs the CLI in
skeleton / full / check modes, and asserts the reliability contract. Standard
library only; no network, no third-party deps, no prepared answers.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def write(path, text=None, raw=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if raw is not None:
        with open(path, "wb") as fh:
            fh.write(raw)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text or "")


def make_case(tmp):
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    os.makedirs(os.path.join(inp, "empty_dir"), exist_ok=True)

    write(os.path.join(inp, "case.json"), json.dumps({
        "case_id": "selftest-001",
        "output_contract": {
            "file_manifest": "library_manifest.csv",
            "dedup": "dedup_version.json",
            "tree": "directory_tree.txt",
        },
    }))
    write(os.path.join(inp, "task.md"),
          "# 测试任务\nchallenge_id: `selftest-001`\n")
    write(os.path.join(inp, "materials", "analysis_v1.txt"), "alpha\n")
    write(os.path.join(inp, "materials", "analysis_v2.txt"), "alpha beta\n")
    write(os.path.join(inp, "materials", "data.csv"), "a,b\n1,2\n")
    write(os.path.join(inp, "materials", "copy_of_data.csv"), "a,b\n1,2\n")
    write(os.path.join(inp, "materials", "catalog.json"), json.dumps([
        {"path": "materials/data.csv", "sha256":
            "5f5d1e0c1b2a3f6d7e8f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2"},
        {"path": "materials/missing.csv", "size": 10},
    ]))
    write(os.path.join(inp, "materials", "bad.json"), "{not valid json")
    write(os.path.join(inp, "materials", "notes_VERSION.txt"),
          "v1 -> v2\nv2 -> v3 修复\n")
    # large sparse file (exceeds 2MB hash cap) without real disk allocation
    large = os.path.join(inp, "materials", "large.bin")
    with open(large, "wb") as fh:
        fh.seek(2 * 1024 * 1024 + 16)
        fh.write(b"x")
    # broken symlink (best effort; may need privileges on Windows)
    try:
        os.symlink("no_such_target.txt",
                   os.path.join(inp, "materials", "broken_link.txt"))
    except (OSError, NotImplementedError):
        pass
    return inp, out, work


def assert_ok(rc, name):
    if rc != 0:
        raise SystemExit("selftest FAILED at %s (rc=%d)" % (name, rc))


def check_result(out):
    path = os.path.join(out, "result.json")
    if not os.path.exists(path):
        raise SystemExit("selftest FAILED: result.json missing")
    with open(path, "r", encoding="utf-8") as fh:
        result = json.load(fh)
    for req in ("case_id", "status", "sources", "uncertainty", "change_log",
                "computed"):
        assert req in result, "missing %s" % req
    assert result["case_id"], "case_id empty"
    assert result["status"] in ("ok", "partial", "insufficient_evidence",
                                "input_error"), "bad status"
    for key in ("file_manifest", "dedup", "tree"):
        assert key in result, "contract field missing %s" % key
    for artifact in ("library_manifest.csv", "directory_tree.txt",
                     "dedup_version.json", "recovery_report.json",
                     "library_index.json", "library_manifest.md",
                     "operation_log.jsonl"):
        assert os.path.exists(os.path.join(out, artifact)), \
            "artifact missing %s" % artifact
    return result


def main():
    tmp = tempfile.mkdtemp(prefix="library_skill_selftest_")
    try:
        inp, out, work = make_case(tmp)
        base = ["--input", inp, "--output", out, "--work", work,
                "--case-json", os.path.join(inp, "case.json"),
                "--task-path", os.path.join(inp, "task.md")]

        rc = run(["--skeleton-only"] + base).returncode
        assert_ok(rc, "skeleton")
        rc = run(base).returncode
        assert_ok(rc, "full")
        result = check_result(out)
        rc = run(["--check", os.path.join(out, "result.json"),
                  "--case-json", os.path.join(inp, "case.json")]).returncode
        assert_ok(rc, "check")

        # empty input -> insufficient_evidence, exit 0
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        rc = run(["--skeleton-only", "--input", empty,
                  "--output", os.path.join(tmp, "out_empty"),
                  "--work", os.path.join(tmp, "work_empty"),
                  "--case-json", os.path.join(empty, "case.json"),
                  "--task-path", os.path.join(empty, "task.md")]).returncode
        assert_ok(rc, "empty-skeleton")

        # missing case.json -> input_error
        rc = run(["--skeleton-only", "--input", empty,
                  "--output", os.path.join(tmp, "out_nocase"),
                  "--work", os.path.join(tmp, "work_nocase")]).returncode
        assert_ok(rc, "no-case-skeleton")

        print("selftest OK: status=%s files=%d dup=%d version=%d"
              % (result["status"],
                 result["computed"]["summary"]["counts"]["files"],
                 len(result["computed"]["duplicate_groups"]),
                 len(result["computed"]["version_candidates"])))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
