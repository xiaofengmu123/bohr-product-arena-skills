#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — package-level structural self-check; runs without /input or /app/output.

Standard library only. Run from the Skill root:

    python scripts/selftest.py

Checks (structural only — it never fabricates benchmark scores or scientific data):
  1. SKILL.md exists with name/description/version YAML frontmatter;
  2. RESULT_CONTRACT is internally consistent (required fields have defaults; enums valid);
  3. scaffold.py and finalize.py run with --help (exit 0);
  4. a synthetic roundtrip in a temp dir: scaffold -> finalize -> result.json parses,
     contains every contract key plus an injected unknown output_contract key, correct
     case_id, valid status, non-empty change_log.

Exit code 0 means all checks passed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run(cmd, **kw):
    return subprocess.run([sys.executable] + cmd, capture_output=True, text=True, **kw)


def check_frontmatter(results):
    p = os.path.join(ROOT, "SKILL.md")
    if not os.path.exists(p):
        results.append(("SKILL.md exists", False))
        return
    results.append(("SKILL.md exists", True))
    text = open(p, encoding="utf-8-sig").read()
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        results.append(("SKILL.md YAML frontmatter", False))
        return
    results.append(("SKILL.md YAML frontmatter", True))
    fm = m.group(1)
    for key in ("name", "description", "version"):
        ok = bool(re.search(r"^%s:\s*\S" % key, fm, re.M))
        results.append(("SKILL.md frontmatter key %r" % key, ok))


def check_contract(results):
    from scaffold import RESULT_CONTRACT, STATUS_ENUM
    for k, spec in RESULT_CONTRACT.items():
        if spec.get("required"):
            results.append(("contract field %r required+has default" % k, "default" in spec))
        if spec.get("type") == "enum":
            results.append(("contract field %r enum non-empty" % k, bool(spec.get("enum"))))
    need = ("case_id", "status", "sources", "uncertainty", "change_log")
    results.append(("contract has case_id/status/sources/uncertainty/change_log",
                    all(k in RESULT_CONTRACT for k in need)))
    results.append(("status enum sane", STATUS_ENUM and "partial" in STATUS_ENUM))


def check_help(results):
    for script in ("scaffold.py", "finalize.py"):
        p = os.path.join(HERE, script)
        r = run([p, "--help"])
        results.append(("%s --help exit 0" % script, r.returncode == 0))


def check_roundtrip(results):
    tmp = tempfile.mkdtemp(prefix="sci-figure-contract-selftest-")
    try:
        indir = os.path.join(tmp, "input")
        outdir = os.path.join(tmp, "output")
        workdir = os.path.join(tmp, "work")
        for d in (indir, outdir, workdir):
            os.makedirs(d)
        case = {
            "case_id": "selftest-case-001",
            "task": "synthetic self-check only; not a formal case answer",
            "output_contract": {
                "case_id": "string",
                "status": "string",
                "custom_metric": "object",
                "editable_source_files": "list",
                "export_files": "list",
            },
        }
        with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# synthetic task\nchallenge_id: `selftest-xyz`\n")

        scaffold_py = os.path.join(HERE, "scaffold.py")
        finalize_py = os.path.join(HERE, "finalize.py")
        r1 = run([scaffold_py, "--input-dir", indir, "--output-dir", outdir, "--work-dir", workdir])
        r2 = run([finalize_py, "--result", os.path.join(outdir, "result.json"),
                  "--output-dir", outdir, "--work-dir", workdir])
        results.append(("scaffold roundtrip exit 0", r1.returncode == 0))
        results.append(("finalize roundtrip exit 0", r2.returncode == 0))

        from scaffold import RESULT_CONTRACT, STATUS_ENUM
        with open(os.path.join(outdir, "result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        results.append(("result.json has all bundled contract keys",
                        all(k in result for k in RESULT_CONTRACT)))
        results.append(("unknown contract key echoed", "custom_metric" in result))
        results.append(("case_id correct", result.get("case_id") == "selftest-case-001"))
        results.append(("status valid", result.get("status") in STATUS_ENUM))
        results.append(("change_log non-empty", bool(result.get("change_log"))))
    except Exception as e:  # noqa: BLE001
        results.append(("roundtrip raised exception", False))
        print("roundtrip error: %r" % e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    results = []
    check_frontmatter(results)
    check_contract(results)
    check_help(results)
    check_roundtrip(results)
    failures = [label for label, ok in results if not ok]
    for label, ok in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", label))
    print("---")
    print("total=%d passed=%d failed=%d" % (len(results), len(results) - len(failures), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
