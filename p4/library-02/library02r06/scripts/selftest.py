"""selftest.py - synthetic self-test for the library-retrieval Skill.

Creates a throwaway synthetic library under a temp directory (clearly NOT a
formal case), runs the full pipeline + --check, and asserts deterministic,
verifiable behavior. Stdlib only. Exit 0 = pass, 1 = fail.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(*args):
    return subprocess.run([sys.executable, "-B", MAIN] + list(args),
                          capture_output=True, text=True)


def make_library(root):
    os.makedirs(os.path.join(root, "materials", "sub"), exist_ok=True)
    write = lambda p, s: open(os.path.join(root, p), "w", encoding="utf-8").write(s)
    write("case.json", json.dumps({
        "case_id": "selftest-001",
        "output_contract": {"summary": {}, "files": [], "answers": []},
        "questions": ["Which file contains the buffer formula?"],
    }, ensure_ascii=False))
    write("task.md", "# Synthetic selftest task\n\n1. 盘点文件与权限\n2. 检索: buffer formula 写在哪份文件\n")
    write("materials/a.txt", "alpha buffer formula: 50 mM Tris, 150 mM NaCl\n")
    write("materials/a_copy.txt", "alpha buffer formula: 50 mM Tris, 150 mM NaCl\n")
    write("materials/b.txt", "beta protocol, different content entirely\n")
    write("materials/broken.json", "{ this is not valid json")
    write("materials/catalog.csv", "path,version\nmaterials/a.txt,v1\nmaterials/gone.txt,v2\n")
    # attempt a symlink; skip gracefully if the platform forbids it
    try:
        os.symlink("a.txt", os.path.join(root, "materials", "alias_link.txt"))
    except (OSError, NotImplementedError):
        pass


def main():
    tmp = tempfile.mkdtemp(prefix="lib-selftest-")
    root = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    make_library(root)

    r1 = run("--case-json", os.path.join(root, "case.json"),
             "--task-path", os.path.join(root, "task.md"),
             "--input-root", root, "--output-dir", out, "--work-dir", work,
             "--skeleton-only")
    assert r1.returncode == 0, "skeleton-only failed:\n" + r1.stderr
    skel = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    assert skel["case_id"] == "selftest-001", skel.get("case_id")
    assert skel["status"] == "partial", skel.get("status")

    r2 = run("--case-json", os.path.join(root, "case.json"),
             "--task-path", os.path.join(root, "task.md"),
             "--input-root", root, "--output-dir", out, "--work-dir", work)
    assert r2.returncode == 0, "pipeline failed:\n" + r2.stderr
    result = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    model = json.load(open(os.path.join(out, "library_model.json"), encoding="utf-8"))

    # contract fields back-filled
    for k in ("summary", "files", "answers"):
        assert k in result, "missing contract field %s" % k
    # duplicate group detected by content hash (a.txt + a_copy.txt)
    assert len(model["duplicate_groups"]) >= 1, "duplicate group not detected"
    # broken catalog record -> missing
    recon = model["reconciliation"]
    assert any("gone.txt" in m["catalog_ref"] for m in recon["missing"]), \
        "missing catalog ref not detected"
    # invalid JSON recorded as degradation, not a crash
    assert any(d.get("kind") in ("bad_encoding", "unreadable") or
               "broken.json" in d.get("path", "") for d in result["degradations"]), \
        "broken json not recorded"

    # --check passes and re-verifies hashes
    r3 = run("--check", os.path.join(out, "result.json"),
             "--case-json", os.path.join(root, "case.json"), "--input-root", root)
    assert r3.returncode == 0, "--check failed:\n" + r3.stdout + r3.stderr

    # determinism: run again, identical result except timestamps
    out2 = os.path.join(tmp, "output2")
    run("--case-json", os.path.join(root, "case.json"),
        "--task-path", os.path.join(root, "task.md"),
        "--input-root", root, "--output-dir", out2, "--work-dir", os.path.join(tmp, "work2"))
    r2b = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
    def strip_time(obj):
        if isinstance(obj, dict):
            return {k: strip_time(v) for k, v in obj.items()
                    if k not in ("time", "generated_at_utc")}
        if isinstance(obj, list):
            return [strip_time(v) for v in obj]
        return obj
    assert strip_time(result) == strip_time(r2b), "non-deterministic output"

    print("SELFTEST PASS (synthetic): files=%d dup_groups=%d missing=%d" % (
        result["computed"]["summary"]["files"],
        len(model["duplicate_groups"]),
        len(recon["missing"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
