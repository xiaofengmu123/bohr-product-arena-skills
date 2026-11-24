"""selftest.py - runnable self-test for the skill package (standard library only).

Builds a synthetic input library in a temp directory, runs the pipeline exactly
as the harness would, and asserts that every deliverable and degradation path
behaves.  It never touches /input or /app/output and never fabricates results.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args, cwd=HERE):
    return subprocess.run([sys.executable, "-B", MAIN] + args, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok: %s" % msg)


def main():
    print("selftest: document library repair")
    tmp = tempfile.mkdtemp(prefix="librepair_selftest_")
    try:
        _build_input(tmp)
        _run_full(tmp)
        _run_skeleton(tmp)
        _run_empty(tmp)
        _run_missing_case(tmp)
        _run_bad_json(tmp)
        print("selftest: ALL PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _build_input(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(os.path.join(inp, "materials", "empty_dir"))
    os.makedirs(out)
    os.makedirs(work)

    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "case_id": "selftest-case",
            "title": "selftest library",
            "output_contract": {
                "library_summary": None,
                "file_manifest": [],
                "directory_tree": None,
                "version_relations": [],
                "reconciliation": None,
                "answers": [],
            },
        }, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# selftest task\n\nchallenge_id: `selftest-case`\n\n组织规则：按内容去重、按主干识别版本。\n")

    proto_v1 = "# protocol v1\nbuffer: Tris-HCl\n"
    proto_v2 = "# protocol v2\nbuffer: Tris-HCl, pH 8.0\n"
    with open(os.path.join(inp, "materials", "protocol_v1.md"), "w", encoding="utf-8") as fh:
        fh.write(proto_v1)
    with open(os.path.join(inp, "materials", "protocol_v1_copy.md"), "w", encoding="utf-8") as fh:
        fh.write(proto_v1)  # duplicate content, different name
    with open(os.path.join(inp, "materials", "protocol_v2.md"), "w", encoding="utf-8") as fh:
        fh.write(proto_v2)
    with open(os.path.join(inp, "materials", "notes.md"), "w", encoding="utf-8") as fh:
        fh.write("# notes\n\n| path | sha256 |\n| --- | --- |\n| materials/protocol_v1.md | x |\n")
    with open(os.path.join(inp, "materials", "data.json"), "w", encoding="utf-8") as fh:
        json.dump([{"path": "materials/protocol_v1.md", "role": "protocol"},
                   {"path": "materials/missing.pdf", "role": "protocol"}], fh, ensure_ascii=False)
    with open(os.path.join(inp, "materials", "refs.bib"), "w", encoding="utf-8") as fh:
        fh.write("@article{smith2026,\n  author = {Smith, A.},\n  title = {Buffer study},\n  year = {2026}\n}\n")
    with open(os.path.join(inp, "materials", "records.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "size"])
        w.writerow(["materials/notes.md", 60])

    # hardlink alias (works without elevated privileges on NTFS)
    try:
        os.link(os.path.join(inp, "materials", "protocol_v1.md"),
                os.path.join(inp, "materials", "hardlink_alias.md"))
    except OSError:
        pass
    # symlink alias (may require privileges on Windows; guarded)
    try:
        os.symlink("protocol_v1.md", os.path.join(inp, "materials", "link_protocol.md"))
    except (OSError, NotImplementedError):
        pass

    return {"input": inp, "output": out, "work": work}


def _run_full(tmp):
    print("[full pipeline]")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    r = run(["--input-root", inp, "--output-dir", out, "--work-dir", work,
             "--case-json", os.path.join(inp, "case.json"),
             "--task-path", os.path.join(inp, "task.md")])
    check(r.returncode == 0, "full pipeline exit 0 (got %d): %s" % (r.returncode, r.stderr.strip()[:200]))

    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(result["case_id"] == "selftest-case", "case_id echoed")
    check(result["status"] in ("partial", "ok", "insufficient_evidence", "input_error"), "valid status")
    check(result["output_contract"] is not None, "output_contract present")
    check(result["file_manifest"], "file_manifest non-empty")
    check(result["sources"] == [], "sources list present")
    check(result["change_log"], "change_log present")

    rows = list(csv.reader(open(os.path.join(out, "library_manifest.csv"), encoding="utf-8")))
    check(len(rows) > 1, "manifest csv has data rows")

    dv = json.load(open(os.path.join(out, "dedup_version.json"), encoding="utf-8"))
    check(any(g["count"] >= 2 for g in dv["duplicate_groups"]), "duplicate group found")
    check(any(v["normalized_stem"] == "protocol" for v in dv["version_candidates"]),
          "version candidate for protocol stem")

    tree = open(os.path.join(out, "directory_tree.txt"), encoding="utf-8").read()
    check("[空目录]" in tree, "empty directory marked in tree")

    check(os.path.exists(os.path.join(out, "operation_log.jsonl")), "operation log exists")
    check(os.path.exists(os.path.join(out, "library_index.json")), "index exists")
    check(os.path.exists(os.path.join(out, "recovery_report.json")), "recovery report exists")
    check(os.path.exists(os.path.join(out, "library_manifest.md")), "handoff markdown exists")

    c = run(["--check", os.path.join(out, "result.json"),
             "--case-json", os.path.join(inp, "case.json")])
    check(c.returncode == 0, "--check passes")


def _run_skeleton(tmp):
    print("[skeleton]")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    r = run(["--skeleton-only", "--input-root", inp, "--output-dir", out, "--work-dir", work,
             "--case-json", os.path.join(inp, "case.json")])
    check(r.returncode == 0, "skeleton exit 0")
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(result["status"] in ("partial", "ok", "insufficient_evidence", "input_error"), "skeleton status valid")
    check(result["case_id"] == "selftest-case", "skeleton case_id")
    check(os.path.exists(os.path.join(work, "budget.json")), "budget ledger written")


def _run_empty(tmp):
    print("[empty input]")
    empty = os.path.join(tmp, "empty_in")
    os.makedirs(empty)
    with open(os.path.join(empty, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "selftest-empty", "output_contract": {}}, fh, ensure_ascii=False)
    out = os.path.join(tmp, "empty_out")
    work = os.path.join(tmp, "empty_work")
    r = run(["--input-root", empty, "--output-dir", out, "--work-dir", work,
             "--case-json", os.path.join(empty, "case.json"),
             "--task-path", os.path.join(empty, "task.md")])
    check(r.returncode == 0, "empty input exit 0")
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(result["status"] == "insufficient_evidence", "empty input -> insufficient_evidence")


def _run_missing_case(tmp):
    print("[missing case.json]")
    inp = os.path.join(tmp, "no_case")
    os.makedirs(inp)
    out = os.path.join(tmp, "no_case_out")
    work = os.path.join(tmp, "no_case_work")
    r = run(["--input-root", inp, "--output-dir", out, "--work-dir", work,
             "--case-json", os.path.join(inp, "case.json"),
             "--task-path", os.path.join(inp, "task.md")])
    check(r.returncode == 0, "missing case exit 0")
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(result["status"] == "input_error", "missing case -> input_error")


def _run_bad_json(tmp):
    print("[bad case.json]")
    inp = os.path.join(tmp, "bad_case")
    os.makedirs(inp)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    out = os.path.join(tmp, "bad_case_out")
    work = os.path.join(tmp, "bad_case_work")
    r = run(["--input-root", inp, "--output-dir", out, "--work-dir", work,
             "--case-json", os.path.join(inp, "case.json"),
             "--task-path", os.path.join(inp, "task.md")])
    check(r.returncode == 0, "bad case exit 0")
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    check(result["status"] == "input_error", "bad json -> input_error")


if __name__ == "__main__":
    sys.exit(main())
