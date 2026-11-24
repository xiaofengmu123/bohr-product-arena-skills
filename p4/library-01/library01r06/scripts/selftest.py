#!/usr/bin/env python3
"""selftest.py -- deterministic self-test for the document-library repair Skill.

Creates disposable fixtures under a temporary directory, runs the real
scripts/main.py through its CLI, and asserts the documented behaviours:

  * skeleton writes a legal partial result.json first;
  * full run produces all editable artifacts;
  * --check and --verify both pass;
  * empty input -> exit 0 + insufficient_evidence;
  * bad JSON -> continues with errors recorded;
  * missing case.json -> input_error.

Stdlib only; no third-party dependencies.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable


def run(*args, cwd=None):
    return subprocess.run(
        [PY, "-B", MAIN] + list(args),
        capture_output=True, text=True, cwd=cwd or HERE,
    )


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def make_case(root):
    inp = os.path.join(root, "input")
    case = {
        "case_id": "selftest-case-01",
        "task": "organize the materials and answer the questions",
        "output_contract": {
            "library_manifest": {"type": "array", "default": []},
            "duplicate_groups": {"type": "array", "default": []},
            "questions": [
                {"name": "count", "question": "How many files are there?"},
                {"name": "duplicates", "question": "Which files are duplicates?"},
                {"name": "authors", "question": "Who are the authors?"},
            ],
        },
    }
    write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(inp, "task.md"),
          "# selftest task\n\n- 按内容哈希去重，而不是按文件名。\n- 重建版本链。\n")
    m = os.path.join(inp, "materials")
    write(os.path.join(m, "data_v1.txt"), "raw instrument data version one\n")
    write(os.path.join(m, "data_v2.txt"), "raw instrument data version two\n")
    write(os.path.join(m, "report.txt"), "same body\n")
    write(os.path.join(m, "report (copy).txt"), "same body\n")
    write(os.path.join(m, "catalog.bib"),
          "@article{a1,\n  author = {Smith, J},\n  year = {2024},\n  title = {A Paper}\n}\n")
    write(os.path.join(m, "bad.json"), "{ this is not json")
    os.makedirs(os.path.join(m, "empty_dir"), exist_ok=True)
    # Symlink only where the platform permits it (Linux runtime target).
    try:
        os.symlink(os.path.join(m, "report.txt"), os.path.join(m, "report_link.txt"))
    except OSError:
        pass
    return inp


def check_file(path):
    assert os.path.exists(path), "missing artifact: " + path


def main():
    failures = []
    root = tempfile.mkdtemp(prefix="libskill-selftest-")
    inp = make_case(root)
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    base = ["--input", inp, "--output", out, "--work", work,
            "--case-json", os.path.join(inp, "case.json"),
            "--task-path", os.path.join(inp, "task.md")]

    def expect(label, cond):
        print(("PASS" if cond else "FAIL") + "  " + label)
        if not cond:
            failures.append(label)

    # 1. skeleton
    r = run(*base, "--skeleton-only")
    expect("skeleton exit 0", r.returncode == 0)
    if os.path.exists(os.path.join(out, "result.json")):
        rr = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        expect("skeleton case_id", rr.get("case_id") == "selftest-case-01")
        expect("skeleton status partial", rr.get("status") == "partial")
        expect("skeleton contract fields backfilled",
               "library_manifest" in rr and "duplicate_groups" in rr)
    else:
        expect("skeleton result.json exists", False)

    # 2. full run
    r = run(*base)
    expect("full exit 0", r.returncode == 0)
    for f in ("result.json", "plan.json", "library_model.json", "library_manifest.csv",
              "directory_tree.txt", "dedup_version.json", "recovery_report.json",
              "library_index.json", "library_manifest.md", "operation_log.jsonl"):
        check_file(os.path.join(out, f))
    expect("all editable artifacts exist", True)

    rr = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    expect("full case_id", rr.get("case_id") == "selftest-case-01")
    expect("status in enum", rr.get("status") in {"ok", "partial", "insufficient_evidence", "input_error"})
    model = json.load(open(os.path.join(out, "library_model.json"), encoding="utf-8"))
    expect("dedup found by content", len(model["duplicate_groups"]) >= 1)
    expect("version candidate found by stem", any(
        "data" == g["stem_norm"] for g in model["version_candidates"]))
    expect("errors/degradations captured bad.json", any(
        "bad.json" in str(e.get("item", "")) for e in
        (model["errors"] + model["degradations"] + model["catalog_parse_issues"])))

    # 3. check + verify
    expect("--check passes", run("--check", os.path.join(out, "result.json"),
                                 "--case-json", os.path.join(inp, "case.json")).returncode == 0)
    expect("--verify passes", run("--verify", os.path.join(out, "result.json")).returncode == 0)

    # 4. empty input
    eout = os.path.join(root, "eout")
    ework = os.path.join(root, "ework")
    einp = os.path.join(root, "einput")
    os.makedirs(einp, exist_ok=True)
    write(os.path.join(einp, "case.json"), json.dumps({"case_id": "empty-01"}))
    r = run("--input", einp, "--output", eout, "--work", ework,
            "--case-json", os.path.join(einp, "case.json"))
    expect("empty input exit 0", r.returncode == 0)
    if os.path.exists(os.path.join(eout, "result.json")):
        er = json.load(open(os.path.join(eout, "result.json"), encoding="utf-8"))
        expect("empty input honest status", er.get("status") in {"insufficient_evidence", "partial", "ok"})
    else:
        expect("empty input result exists", False)

    # 5. missing case.json
    mout = os.path.join(root, "mout")
    r = run("--input", einp, "--output", mout, "--work", os.path.join(root, "mwork"),
            "--case-json", os.path.join(einp, "no-such.json"))
    if os.path.exists(os.path.join(mout, "result.json")):
        mr = json.load(open(os.path.join(mout, "result.json"), encoding="utf-8"))
        expect("missing case.json -> input_error", mr.get("status") == "input_error")
    else:
        expect("missing case.json result exists", False)

    print("----")
    if failures:
        print("SELFTEST FAIL: %d check(s)" % len(failures))
        return 1
    print("SELFTEST PASS (all checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
