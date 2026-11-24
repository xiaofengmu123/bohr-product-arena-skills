# -*- coding: utf-8 -*-
"""selftest.py — package self-test for the document-library repair skill.

Runs the deterministic pipeline against a synthetic in-memory input tree and
checks the output contract, statuses, artifacts and degradation handling.
No formal case data is embedded; everything here is explicitly synthetic.
The CLI is exercised through the real `scripts/main.py` entry point.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_PY = os.path.join(HERE, "main.py")

PASS = []
FAIL = []
SKIP = []


def record(name, ok, detail=""):
    if ok:
        PASS.append(name)
    else:
        FAIL.append((name, detail))


def run_cli(argv):
    cmd = [sys.executable, "-B", MAIN_PY] + argv
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:  # noqa: BLE001
        return "raised", "", str(exc)


def build_demo(root):
    os.makedirs(os.path.join(root, "proj", "raw"), exist_ok=True)
    os.makedirs(os.path.join(root, "empty_dir"), exist_ok=True)
    contract = {
        "title": "string",
        "files": "array",
        "answers": "array",
    }
    case = {
        "case_id": "selftest-case-0001",
        "output_contract": contract,
        "questions": ["where is the buffer recipe"],
    }
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\nchallenge_id: `selftest-0001`\n")

    with open(os.path.join(root, "proj", "protocol_v1.txt"), "w", encoding="utf-8") as fh:
        fh.write("Buffer recipe: 10 mM Tris, pH 7.4.\nSecond line of evidence.\n")
    with open(os.path.join(root, "proj", "raw", "protocol_copy.txt"), "w", encoding="utf-8") as fh:
        fh.write("Buffer recipe: 10 mM Tris, pH 7.4.\nSecond line of evidence.\n")
    with open(os.path.join(root, "proj", "protocol_v2.txt"), "w", encoding="utf-8") as fh:
        fh.write("Buffer recipe: 10 mM Tris, pH 8.0, revised for 2024.\n")
    with open(os.path.join(root, "bad.json"), "w", encoding="utf-8") as fh:
        fh.write('{"files": [')
    with open(os.path.join(root, "badenc.txt"), "wb") as fh:
        fh.write(b"\xff\xfe\x00not-utf8\xff\xff")

    flags = {"hardlink_ok": False, "symlink_ok": False}
    try:
        os.link(
            os.path.join(root, "proj", "protocol_v1.txt"),
            os.path.join(root, "proj", "protocol_hardlink.txt"),
        )
        flags["hardlink_ok"] = True
    except OSError:
        pass
    try:
        os.symlink("protocol_v1.txt", os.path.join(root, "proj", "protocol_link.txt"))
        flags["symlink_ok"] = True
    except OSError:
        pass
    return flags


def check_demo_output(output_dir, flags):
    rp = os.path.join(output_dir, "result.json")
    if not os.path.exists(rp):
        record("demo:result_exists", False, "missing result.json")
        return
    result = json.load(open(rp, encoding="utf-8"))
    record("demo:result_json_valid", isinstance(result, dict))
    record("demo:case_id", result.get("case_id") == "selftest-case-0001", result.get("case_id"))
    record("demo:status_valid", result.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"),
           result.get("status"))
    record("demo:contract_fields", all(k in result for k in ("title", "files", "answers")),
           "missing contract fields")
    record("demo:sources_key", "sources" in result)
    record("demo:uncertainty_key", "uncertainty" in result)
    record("demo:change_log", isinstance(result.get("change_log"), list) and bool(result["change_log"]))
    record("demo:computed", "computed" in result and "files" in result.get("computed", {}))
    files = result.get("computed", {}).get("files", [])
    record("demo:file_count", len(files) >= 6, "files=%d" % len(files))
    dup = result.get("computed", {}).get("duplicate_groups", [])
    record("demo:duplicate_detected", any(len(g.get("paths", [])) >= 2 for g in dup),
           "dup_groups=%d" % len(dup))
    ver = result.get("computed", {}).get("version_candidates", [])
    record("demo:version_candidates", len(ver) >= 1, "ver=%d" % len(ver))
    for artifact in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                     "recovery_report.json", "library_index.json", "library_manifest.md",
                     "operation_log.jsonl"):
        record("demo:artifact_%s" % artifact, os.path.exists(os.path.join(output_dir, artifact)))
    tree_path = os.path.join(output_dir, "directory_tree.txt")
    if os.path.exists(tree_path):
        tree = open(tree_path, encoding="utf-8").read()
        record("demo:empty_dir_marked", "[空目录]" in tree)
    else:
        record("demo:empty_dir_marked", False, "tree missing")
    if flags.get("hardlink_ok"):
        aliases = result.get("computed", {}).get("aliases", {}).get("hardlinks", [])
        record("demo:hardlink_group", any(len(a.get("paths", [])) >= 2 for a in aliases),
               "hardlinks=%d" % len(aliases))
    else:
        SKIP.append("demo:hardlink_group (os.link unavailable)")


def check_skeleton(output_dir):
    rp = os.path.join(output_dir, "result.json")
    result = json.load(open(rp, encoding="utf-8"))
    record("skeleton:exists", os.path.exists(rp))
    record("skeleton:status_partial", result.get("status") == "partial", result.get("status"))
    record("skeleton:contract_fields", all(k in result for k in ("title", "files", "answers")))


def check_empty_input(output_dir):
    rp = os.path.join(output_dir, "result.json")
    result = json.load(open(rp, encoding="utf-8"))
    record("empty:status", result.get("status") == "insufficient_evidence", result.get("status"))


def check_missing_case(output_dir):
    rp = os.path.join(output_dir, "result.json")
    result = json.load(open(rp, encoding="utf-8"))
    record("missing_case:status", result.get("status") == "input_error", result.get("status"))


def main():
    base = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        demo_in = os.path.join(base, "demo_in")
        demo_out = os.path.join(base, "demo_out")
        demo_work = os.path.join(base, "demo_work")
        flags = build_demo(demo_in)

        rc, out, err = run_cli([
            "--input", demo_in, "--output", demo_out, "--work", demo_work,
            "--case-json", os.path.join(demo_in, "case.json"),
            "--task-path", os.path.join(demo_in, "task.md"),
        ])
        record("demo:exit_zero", rc == 0, "rc=%s out=%s err=%s" % (rc, out[-200:], err[-200:]))
        check_demo_output(demo_out, flags)

        rc, out, err = run_cli([
            "--check", os.path.join(demo_out, "result.json"),
            "--case-json", os.path.join(demo_in, "case.json"),
        ])
        record("check:no_issues", rc == 0, "rc=%s out=%s" % (rc, out))

        skel_out = os.path.join(base, "skel_out")
        rc, out, err = run_cli([
            "--input", demo_in, "--output", skel_out, "--work", os.path.join(base, "skel_work"),
            "--skeleton-only", "--case-json", os.path.join(demo_in, "case.json"),
        ])
        record("skeleton:exit_zero", rc == 0, "rc=%s" % rc)
        check_skeleton(skel_out)

        empty_in = os.path.join(base, "empty_in")
        os.makedirs(empty_in)
        with open(os.path.join(empty_in, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "empty-case", "output_contract": {"title": "string"}}, fh)
        with open(os.path.join(empty_in, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# empty task\n")
        empty_out = os.path.join(base, "empty_out")
        rc, out, err = run_cli(["--input", empty_in, "--output", empty_out, "--work", os.path.join(base, "empty_work")])
        record("empty:exit_zero", rc == 0, "rc=%s" % rc)
        check_empty_input(empty_out)

        missing_in = os.path.join(base, "missing_in")
        os.makedirs(missing_in)
        missing_out = os.path.join(base, "missing_out")
        rc, out, err = run_cli(["--input", missing_in, "--output", missing_out, "--work", os.path.join(base, "missing_work")])
        record("missing_case:exit_zero", rc == 0, "rc=%s" % rc)
        check_missing_case(missing_out)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("SELFTEST PASS: %d" % len(PASS))
    print("SELFTEST FAIL: %d" % len(FAIL))
    print("SELFTEST SKIP: %d" % len(SKIP))
    for name in PASS:
        print("  PASS", name)
    for name, detail in FAIL:
        print("  FAIL", name, "->", detail)
    for name in SKIP:
        print("  SKIP", name)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
