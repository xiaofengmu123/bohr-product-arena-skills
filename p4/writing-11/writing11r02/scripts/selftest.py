#!/usr/bin/env python3
"""Self-test for the academic-translation-bilingual Skill package.

Runs offline in a throwaway temp directory: verifies SKILL.md frontmatter,
compiles every script, then exercises main.py (brief / init / add / finalize)
and check.py on empty and malformed inputs. Exits non-zero on any failure.

Pure standard library. Usage:
  python3 scripts/selftest.py          # auto-locates the Skill root
  python3 scripts/selftest.py --root /path/to/skill
"""

import argparse
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DEFAULT = os.path.dirname(HERE)

REQUIRED_FILES = ["SKILL.md", "scripts/main.py", "scripts/check.py"]
FRONTMATTER_KEYS = ["name", "description", "version"]


def log(ok, msg):
    print(("[PASS] " if ok else "[FAIL] ") + msg)
    return ok


def check_frontmatter(root):
    sk = os.path.join(root, "SKILL.md")
    if not os.path.exists(sk):
        return log(False, "SKILL.md missing")
    text = open(sk, "r", encoding="utf-8-sig").read()
    if not text.startswith("---"):
        return log(False, "SKILL.md has no YAML frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        return log(False, "SKILL.md frontmatter not closed")
    fm = text[3:end].strip()
    ok = True
    for key in FRONTMATTER_KEYS:
        # accept 'key:' or 'key :' followed by any value
        if not any(line.strip().startswith(key + ":") or
                   line.strip().startswith(key + " :") for line in fm.splitlines()):
            log(False, "frontmatter missing key: %s" % key)
            ok = False
    log(ok, "SKILL.md frontmatter keys present")
    # verify referenced scripts exist
    for rel in ("scripts/main.py", "scripts/check.py", "scripts/selftest.py"):
        if not os.path.exists(os.path.join(root, rel)):
            log(False, "referenced file missing: %s" % rel)
            ok = False
    return ok


def compile_scripts(root):
    ok = True
    for name in ("main.py", "check.py", "selftest.py"):
        p = os.path.join(root, "scripts", name)
        try:
            py_compile.compile(p, doraise=True)
            ok = log(True, "compiles: scripts/%s" % name) and ok
        except py_compile.PyCompileError as e:
            ok = log(False, "compile failed: scripts/%s: %s" % (name, e)) and ok
    return ok


def run_py(root, script, args, timeout=60):
    p = os.path.join(root, "scripts", script)
    return subprocess.run([sys.executable, p] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def test_main_flow(root):
    ok = True
    d = tempfile.mkdtemp(prefix="skill-selftest-")
    inp = os.path.join(d, "input")
    out = os.path.join(d, "output")
    work = os.path.join(d, "work")
    os.makedirs(inp)
    try:
        # 1. brief on empty input -> valid JSON, no crash
        r = run_py(root, "main.py",
                   ["--input", inp, "--output", out, "--work", work, "brief"])
        ok = log(r.returncode == 0, "main.py brief exits 0 on empty input") and ok
        brief = json.loads(r.stdout) if r.returncode == 0 else None
        if brief is not None and "degradations" in brief and brief["degradations"]:
            log(True, "brief records missing-case degradation")

        # 2. init -> valid skeleton
        r = run_py(root, "main.py",
                   ["--input", inp, "--output", out, "--work", work, "init"])
        ok = log(r.returncode == 0, "main.py init exits 0") and ok
        rp = os.path.join(out, "result.json")
        res = json.load(open(rp, "r", encoding="utf-8")) if os.path.exists(rp) else None
        ok = log(isinstance(res, dict) and res.get("status") == "in_progress",
                 "init writes status=in_progress skeleton") and ok

        # 3. add-list / add-note / add-change / finalize
        run_py(root, "main.py", ["--input", inp, "--output", out, "--work", work,
                                 "add-list", "deliverables",
                                 '{"path":"out.md","format":"markdown","title":"t","description":"d"}'])
        run_py(root, "main.py", ["--input", inp, "--output", out, "--work", work,
                                 "add-note", "uncertainty",
                                 '{"item":"x","reason":"test","resolution":"n/a"}'])
        run_py(root, "main.py", ["--input", inp, "--output", out, "--work", work,
                                 "add-note", "version_boundary.material_gaps",
                                 '{"item":"gap","reason":"missing"}'])
        run_py(root, "main.py", ["--input", inp, "--output", out, "--work", work,
                                 "add-change", "--action", "edit", "k", "detail"])
        r = run_py(root, "main.py", ["--input", inp, "--output", out, "--work", work,
                                     "finalize", "--status", "partial"])
        ok = log(r.returncode == 0, "main.py finalize exits 0") and ok
        res = json.load(open(rp, "r", encoding="utf-8"))
        ok = log(res.get("status") == "partial", "finalize sets status=partial") and ok
        ok = log(len(res.get("deliverables", [])) == 1, "deliverables list updated") and ok
        ok = log(len(res.get("uncertainty", [])) == 1, "uncertainty list updated") and ok
        ok = log(len(res.get("version_boundary", {}).get("material_gaps", [])) == 1,
                 "version_boundary.material_gaps updated") and ok
        ok = log(len(res.get("change_log", [])) >= 2, "change_log records init + finalize") and ok
        ok = log(isinstance(res.get("runtime", {}).get("seconds"), (int, float)),
                 "runtime.seconds computed") and ok

        # 4. bad JSON input must not crash brief
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        r = run_py(root, "main.py",
                   ["--input", inp, "--output", out, "--work", work, "brief"])
        ok = log(r.returncode == 0, "main.py brief survives malformed case.json") and ok
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return ok


def test_check_flow(root):
    ok = True
    d = tempfile.mkdtemp(prefix="skill-selftest-check-")
    out = os.path.join(d, "output")
    os.makedirs(out)
    try:
        # a broken bilingual sample: SRC[1] has no TRG, number dropped
        sample = os.path.join(out, "draft.md")
        with open(sample, "w", encoding="utf-8") as fh:
            fh.write("## §1 结果 (Results)\n"
                     "> SRC[1]|loc:paper.pdf#p3|The recovery was 12.5% (n=200).\n"
                     "> TRG[2]|loc:paper.pdf#p3|回收率为 12.4%。\n"
                     "> SRC[2]|loc:paper.pdf#p3|See [1,2] and (Smith, 2020).\n"
                     "> TRG[2]|loc:paper.pdf#p3|见文献。\n")
        glossary = os.path.join(out, "glossary.json")
        with open(glossary, "w", encoding="utf-8") as fh:
            json.dump({"terms": [{"canonical": "回收率",
                                  "variants": ["recovery", "回收率"]}]}, fh)
        report = os.path.join(d, "report.json")
        r = run_py(root, "check.py",
                   ["--dir", out, "--glossary", glossary, "--out", report])
        ok = log(r.returncode == 0, "check.py exits 0") and ok
        rep = json.load(open(report, "r", encoding="utf-8"))
        classes = {e["class"] for e in rep["errors"]} | {w["class"] for w in rep["warnings"]}
        ok = log("alignment" in classes, "check.py detects broken alignment") and ok
        ok = log("numbers" in classes, "check.py detects dropped number") and ok
        ok = log("citations" in classes, "check.py detects dropped citations") and ok

        # empty dir -> valid report, zero errors
        empty = os.path.join(d, "empty")
        os.makedirs(empty)
        report2 = os.path.join(d, "report2.json")
        r = run_py(root, "check.py", ["--dir", empty, "--out", report2])
        ok = log(r.returncode == 0, "check.py exits 0 on empty dir") and ok
        rep2 = json.load(open(report2, "r", encoding="utf-8"))
        ok = log(rep2["error_count"] == 0, "empty dir yields zero errors") and ok
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="Skill self-test")
    ap.add_argument("--root", default=ROOT_DEFAULT)
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    print("Skill root: %s" % root)

    results = []
    results.append(("frontmatter", check_frontmatter(root)))
    results.append(("compile", compile_scripts(root)))
    results.append(("main flow", test_main_flow(root)))
    results.append(("check flow", test_check_flow(root)))

    print("\n--- summary ---")
    failed = 0
    for name, ok in results:
        print("%s: %s" % (name, "PASS" if ok else "FAIL"))
        if not ok:
            failed += 1
    print("\n%d/%d sections passed" % (len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
