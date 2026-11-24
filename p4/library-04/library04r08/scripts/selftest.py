"""selftest.py - package self-check for the research-kb-qa skill.

Run from the skill root:

    python3 scripts/selftest.py

The script validates, in order:

1. SKILL.md frontmatter (name / description / version) and body length;
2. that the CLI subcommands exist and ``--help`` exits 0;
3. end-to-end degradation behaviour on synthetic inputs (empty directory,
   bad JSON, bad encoding, duplicates and versions, output_contract echo);
4. that every produced result.json is legal JSON with case_id + status.

The tests use only the Python standard library and temporary directories, so
they run anywhere and never touch /input or /app/output.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
SKILL_MD = os.path.join(SKILL_ROOT, "SKILL.md")

if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402
import pipeline  # noqa: E402

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print("PASS  %s" % name)
    else:
        FAIL.append(name)
        print("FAIL  %s%s" % (name, (" - " + detail) if detail else ""))


def read_frontmatter():
    if not os.path.isfile(SKILL_MD):
        return None, []
    with open(SKILL_MD, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if not lines or lines[0].strip() != "---":
        return None, lines
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, lines
    meta = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, lines


def test_frontmatter():
    meta, lines = read_frontmatter()
    check("frontmatter block present", meta is not None)
    if meta is None:
        return
    check("frontmatter name", bool(meta.get("name")))
    check("frontmatter description", bool(meta.get("description")))
    check("frontmatter version", bool(meta.get("version")))
    body = lines[lines.index("---", 1) + 1:] if meta else lines
    check("SKILL.md body <= 180 lines", len(body) <= 180,
          "got %d body lines" % len(body))


def run_main(argv):
    """Run main.main() and return (exit_code, captured stdout is not needed)."""
    try:
        code = main.main(argv)
        return code, None
    except SystemExit as exc:
        return (exc.code if isinstance(exc.code, int) else 1), None
    except Exception as exc:  # noqa: BLE001
        return 1, "%s: %s" % (type(exc).__name__, exc)


def test_cli_help():
    code, err = run_main(["--help"])
    check("main.py --help exits 0", code == 0, err or "exit %d" % code)
    for cmd in ("scaffold", "inventory", "finalize"):
        code, err = run_main([cmd, "--help"])
        check("main.py %s --help exits 0" % cmd, code == 0,
              err or "exit %d" % code)


def make_base(prefix):
    """Create a base temp dir with an ``input`` subdir (mirrors the runtime
    contract where /input, /app/output, /app/work are separate locations)."""
    base = tempfile.mkdtemp(prefix=prefix)
    os.makedirs(os.path.join(base, "input"), exist_ok=True)
    return base


def write_case_json(base, text):
    with open(os.path.join(base, "input", "case.json"), "w",
              encoding="utf-8") as fh:
        fh.write(text)


def run_phases(base):
    """Run scaffold → inventory → finalize; output/work sit beside input."""
    input_dir = os.path.join(base, "input")
    out = os.path.join(base, "out")
    work = os.path.join(base, "work")
    for argv in (
        ["scaffold", "--input", input_dir, "--output", out, "--work", work],
        ["inventory", "--input", input_dir, "--output", out, "--work", work],
    ):
        code, err = run_main(argv)
        if code != 0:
            return None, "phase %s failed: %s" % (argv[0], err)
    # Write an empty answers file so finalize has something to merge.
    answers_path = os.path.join(work, "answers.json")
    with open(answers_path, "w", encoding="utf-8") as fh:
        json.dump({"output": {}, "answers": [], "uncertainty": [],
                   "degradations": [], "limitations": [], "pending_human": []},
                  fh)
    code, err = run_main(["finalize", "--input", input_dir,
                          "--output", out, "--work", work,
                          "--answers", answers_path])
    if code != 0:
        return None, "finalize failed: %s" % err
    result_path = os.path.join(out, "result.json")
    if not os.path.isfile(result_path):
        return None, "result.json missing"
    result, err = pipeline.load_json(result_path)
    if err is not None:
        return None, "result.json unreadable: %s" % err
    return result, None


def test_empty_input():
    base = make_base("kbqa_empty_")
    try:
        result, err = run_phases(base)
        check("empty input does not crash", err is None, err)
        if err is None:
            check("empty input status=insufficient_evidence",
                  result.get("status") == pipeline.STATUS_INSUFFICIENT,
                  "got %s" % result.get("status"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_bad_json():
    base = make_base("kbqa_badjson_")
    try:
        write_case_json(base, "{not valid json!!!")
        with open(os.path.join(base, "input", "task.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# task\n")
        with open(os.path.join(base, "input", "note.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("hello\n")
        result, err = run_phases(base)
        check("bad case.json does not crash", err is None, err)
        if err is None:
            check("bad JSON recorded in errors",
                  any("case.json" in str(e) for e in result.get("errors", [])),
                  "errors=%s" % json.dumps(result.get("errors"),
                                           ensure_ascii=False))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_bad_encoding():
    base = make_base("kbqa_badenc_")
    try:
        write_case_json(base, '{"case_id": "c1", "output_contract": {}}')
        with open(os.path.join(base, "input", "task.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# task\n")
        with open(os.path.join(base, "input", "legacy.txt"), "wb") as fh:
            fh.write(b"caf\xe9 latin-1 content\n")
        result, err = run_phases(base)
        check("bad encoding does not crash", err is None, err)
        if err is None:
            sources = {s["relative_path"]: s for s in result.get("sources", [])}
            check("legacy.txt inventoried as text",
                  sources.get("legacy.txt", {}).get("type") == "text")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_duplicates_versions_and_contract():
    base = make_base("kbqa_dup_")
    input_dir = os.path.join(base, "input")
    try:
        write_case_json(base, json.dumps({
            "case_id": "case-123",
            "output_contract": {
                "answers": [],
                "inventory": {},
                "summary": "expected",
            },
            "questions": ["What does the library contain?"],
        }))
        with open(os.path.join(input_dir, "task.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("# task\n")
        os.makedirs(os.path.join(input_dir, "materials"))
        same = "identical body\n"
        for name in ("paper_v1.md", "paper_v2.md"):
            with open(os.path.join(input_dir, "materials", name), "w",
                      encoding="utf-8") as fh:
                fh.write(same)
        with open(os.path.join(input_dir, "materials", "report_final.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("final version body\n")
        with open(os.path.join(input_dir, "materials", "report_draft.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("draft version body\n")

        out = os.path.join(base, "out")
        work = os.path.join(base, "work")
        run_main(["scaffold", "--input", input_dir, "--output", out,
                  "--work", work])
        run_main(["inventory", "--input", input_dir, "--output", out,
                  "--work", work])

        inv, err = pipeline.load_json(os.path.join(work, "inventory.json"))
        check("inventory.json readable", err is None, err)
        if err is None:
            dup_groups = inv.get("duplicate_groups", [])
            check("duplicate copies grouped by hash",
                  any(len(g["paths"]) >= 2 for g in dup_groups),
                  "groups=%s" % json.dumps(dup_groups, ensure_ascii=False))
            fams = inv.get("version_families", [])
            bases = {f["base_name"] for f in fams}
            check("version family for report draft/final",
                  "report.md" in bases, "bases=%s" % sorted(bases))

        result, err = run_phases(base)
        check("full pipeline succeeds", err is None, err)
        if err is None:
            check("case_id echoed", result.get("case_id") == "case-123")
            for key in ("answers", "inventory", "summary"):
                check("output_contract key echoed: %s" % key,
                      key in result, "keys=%s" % sorted(result.keys()))
            for key in ("status", "sources", "uncertainty", "change_log",
                        "pending_human", "limitations"):
                check("standard field present: %s" % key, key in result)
            check("evidence_map.json written",
                  os.path.isfile(os.path.join(out, "evidence_map.json")))
            check("inventory.md written",
                  os.path.isfile(os.path.join(out, "inventory.md")))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main_selftest():
    print("== research-kb-qa selftest ==")
    test_frontmatter()
    test_cli_help()
    test_empty_input()
    test_bad_json()
    test_bad_encoding()
    test_duplicates_versions_and_contract()

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main_selftest())
