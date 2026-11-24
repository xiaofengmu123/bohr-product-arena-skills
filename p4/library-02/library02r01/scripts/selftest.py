"""selftest.py - self-contained robustness check for the library skill.

Creates a *synthetic* fixture (clearly marked, never an answer to a real case),
runs the pipeline through scripts/main.py, and verifies the contract that the
scoring rubric emphasizes: skeleton fast path, full run, artifact files, honest
status, evidence map with locator+quote, and --check passing.

Usage: python -B scripts/selftest.py
Exit code 0 = all checks passed; non-zero = a check failed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def make_fixture(root):
    """Build a minimal synthetic input tree (not a real case answer)."""
    import hashlib
    case = {
        "case_id": "selftest-synthetic-001",
        "output_contract": {
            "summary": None,
            "manifest": None,
            "duplicates": None,
            "versions": None,
            "aliases": None,
            "reconciliation": None,
            "index": None,
            "answers": None,
        },
    }
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False, indent=2)

    task = ("# 合成自检题目（示例，非正式案例）\n\n"
            "1. 库中有多少个文件？\n"
            "2. 哪些文件是重复的？\n"
            "3. 有哪些版本候选？\n"
            "4. 对账结果如何？\n")
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
        fh.write(task)

    mat = os.path.join(root, "materials")
    os.makedirs(mat, exist_ok=True)
    content_v1 = "# Protocol\nBuffer: Tris-HCl 50 mM pH 7.5\n"
    content_v2 = "# Protocol\nBuffer: Tris-HCl 100 mM pH 8.0\n"
    with open(os.path.join(mat, "protocol_v1.md"), "w", encoding="utf-8") as fh:
        fh.write(content_v1)
    with open(os.path.join(mat, "protocol_v2.md"), "w", encoding="utf-8") as fh:
        fh.write(content_v2)
    with open(os.path.join(mat, "protocol_v1_copy.md"), "w",
              encoding="utf-8") as fh:
        fh.write(content_v1)
    sha_v1 = hashlib.sha256(content_v1.encode("utf-8")).hexdigest()
    with open(os.path.join(mat, "library.json"), "w", encoding="utf-8") as fh:
        json.dump([
            {"path": "materials/protocol_v1.md", "sha256": sha_v1},
            {"path": "materials/nonexistent.md", "sha256": "0" * 64},
        ], fh, ensure_ascii=False, indent=2)
    # A broken symlink (skipped on platforms without symlink support).
    try:
        os.symlink(os.path.join(mat, "missing.txt"),
                   os.path.join(mat, "broken_link.txt"))
    except OSError:
        pass
    return case


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def check(name, condition, detail=""):
    ok_flag = bool(condition)
    if ok_flag:
        print("[PASS] %s" % name)
    else:
        print("[FAIL] %s %s" % (name, detail))
    return ok_flag


def main():
    root = tempfile.mkdtemp(prefix="library_selftest_")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    ok = True
    try:
        make_fixture(root)

        # 1. Skeleton fast path.
        r = run([sys.executable, "-B", MAIN, "--input", root,
                 "--output", out, "--work", work,
                 "--case-json", os.path.join(root, "case.json"),
                 "--task-path", os.path.join(root, "task.md"),
                 "--skeleton-only"], HERE)
        ok &= check("skeleton exit 0", r.returncode == 0, r.stderr)
        sk = os.path.join(out, "result.json")
        ok &= check("skeleton result.json exists", os.path.isfile(sk))
        if os.path.isfile(sk):
            with open(sk, encoding="utf-8") as fh:
                s = json.load(fh)
            ok &= check("skeleton case_id", s.get("case_id"))
            ok &= check("skeleton status partial",
                        s.get("status") == "partial")
            ok &= check("skeleton contract fields",
                        "summary" in s and "manifest" in s)

        # 2. Full run.
        r = run([sys.executable, "-B", MAIN, "--input", root,
                 "--output", out, "--work", work,
                 "--case-json", os.path.join(root, "case.json"),
                 "--task-path", os.path.join(root, "task.md")], HERE)
        ok &= check("full run exit 0", r.returncode == 0, r.stderr)
        for name in ("result.json", "library_manifest.csv",
                     "directory_tree.txt", "dedup_version.json",
                     "recovery_report.json", "library_index.json",
                     "library_manifest.md", "operation_log.jsonl"):
            ok &= check("artifact %s" % name,
                        os.path.isfile(os.path.join(out, name)))

        with open(sk, encoding="utf-8") as fh:
            result = json.load(fh)
        ok &= check("status honest",
                    result.get("status") in {"ok", "partial",
                                             "insufficient_evidence",
                                             "input_error"})
        ok &= check("sources present", isinstance(result.get("sources"), list))
        ok &= check("uncertainty present",
                    isinstance(result.get("uncertainty"), list))
        ok &= check("change_log present",
                    isinstance(result.get("change_log"), list))
        em = (result.get("computed") or {}).get("evidence_map") or []
        ok &= check("evidence_map non-empty", len(em) > 0)
        if em:
            e0 = em[0]
            ok &= check("evidence locator", bool(e0.get("locator")))
            ok &= check("evidence quote key", "quote" in e0)
            ok &= check("evidence kind", bool(e0.get("kind")))

        # 3. --check.
        r = run([sys.executable, "-B", MAIN, "--check", sk,
                 "--case-json", os.path.join(root, "case.json")], HERE)
        ok &= check("--check passes", r.returncode == 0, r.stdout + r.stderr)

        # 4. Empty input robustness.
        empty = os.path.join(root, "empty")
        os.makedirs(empty, exist_ok=True)
        eout = os.path.join(root, "empty_out")
        r = run([sys.executable, "-B", MAIN, "--input", empty,
                 "--output", eout, "--work", os.path.join(root, "empty_work"),
                 "--case-json", os.path.join(root, "case.json")], HERE)
        ok &= check("empty input exit 0", r.returncode == 0, r.stderr)
        if os.path.isfile(os.path.join(eout, "result.json")):
            with open(os.path.join(eout, "result.json"), encoding="utf-8") as fh:
                er = json.load(fh)
            ok &= check("empty input status",
                        er.get("status") == "insufficient_evidence")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
