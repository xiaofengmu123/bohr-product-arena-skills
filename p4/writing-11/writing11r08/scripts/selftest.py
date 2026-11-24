#!/usr/bin/env python3
"""Self-test for the translation Skill package.

Verifies, in a clean temporary directory (no formal case outputs are written):
  1. SKILL.md has valid YAML frontmatter (name / description / version);
  2. all scripts run `--help` with exit code 0 (stdlib only);
  3. references/*.md exist and are non-empty;
  4. main.py init / add-list / finalize / validate behave on a synthetic case
     (default contract, then a declared output_contract);
  5. check_consistency.py detects a planted number/unit omission, a term-variant
     collision, and a fabricated fact quote.

Exit code 0 means all checks passed; 1 means at least one failure. Prints a
human-readable report. Stdlib only; deterministic.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL = os.path.join(ROOT, "SKILL.md")
MAIN = os.path.join(HERE, "main.py")
CHECK = os.path.join(HERE, "check_consistency.py")
REFS = os.path.join(ROOT, "references")


def run(args):
    return subprocess.run([sys.executable, "-B"] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def check_frontmatter():
    issues = []
    if not os.path.exists(SKILL):
        return ["SKILL.md missing"]
    with open(SKILL, "r", encoding="utf-8-sig") as f:
        text = f.read()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        issues.append("SKILL.md missing leading '---'")
        return issues
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        issues.append("SKILL.md frontmatter not closed with '---'")
        return issues
    fm = "\n".join(lines[1:end])
    for key in ("name", "description", "version"):
        if not re.search(r'^%s\s*:' % key, fm, re.MULTILINE):
            issues.append("frontmatter missing %s" % key)
    return issues


def check_help():
    issues = []
    for script, label in ((MAIN, "main.py"), (CHECK, "check_consistency.py"),
                          (os.path.join(HERE, "selftest.py"), "selftest.py")):
        r = run([script, "--help"])
        if r.returncode != 0:
            issues.append("%s --help failed: %s" % (label, r.stderr.strip()[:200]))
    return issues


def check_references():
    issues = []
    if not os.path.isdir(REFS):
        return ["references/ missing"]
    found = False
    for fn in sorted(os.listdir(REFS)):
        if fn.endswith(".md"):
            found = True
            p = os.path.join(REFS, fn)
            if os.path.getsize(p) == 0:
                issues.append("%s is empty" % fn)
    if not found:
        issues.append("no references/*.md files")
    return issues


def check_workflow(tmp):
    issues = []
    case = {"case_id": "selftest-case-1"}
    case_path = os.path.join(tmp, "case.json")
    with open(case_path, "w", encoding="utf-8") as f:
        json.dump(case, f)
    out = os.path.join(tmp, "out")
    work = os.path.join(tmp, "work")

    r = run([MAIN, "--case", case_path, "--out", out, "--work", work, "init"])
    if r.returncode != 0:
        issues.append("main.py init failed: %s" % r.stderr.strip()[:200])
        return issues
    result_path = os.path.join(out, "result.json")
    result = json.load(open(result_path, encoding="utf-8"))
    if result.get("case_id") != "selftest-case-1":
        issues.append("init case_id mismatch")
    for k in ("deliverables", "fact_source_map", "version_boundary", "status",
              "sources", "uncertainty", "pending_human", "change_log"):
        if k not in result:
            issues.append("init missing field %s" % k)
    if result.get("status") != "in_progress":
        issues.append("init status != in_progress")

    r = run([MAIN, "--out", out, "add-list", "--field", "deliverables",
             "--item", json.dumps({"path": os.path.join(out, "translation.md"),
                                   "format": "markdown", "title": "t",
                                   "description": "d"})])
    if r.returncode != 0:
        issues.append("add-list failed")

    # create the deliverable file so validate passes its existence check
    with open(os.path.join(out, "translation.md"), "w", encoding="utf-8") as f:
        f.write("# demo\n")

    r = run([MAIN, "--out", out, "finalize", "--status", "success"])
    if r.returncode != 0:
        issues.append("finalize failed")

    r = run([MAIN, "--out", out, "validate"])
    if r.returncode != 0:
        issues.append("validate failed after finalize: %s" % r.stdout.strip()[:300])

    # second case with a declared output_contract
    case2 = {"case_id": "selftest-case-2",
             "output_contract": {"translation": "text", "terms": "list"}}
    case2_path = os.path.join(tmp, "case2.json")
    with open(case2_path, "w", encoding="utf-8") as f:
        json.dump(case2, f)
    out2 = os.path.join(tmp, "out2")
    r = run([MAIN, "--case", case2_path, "--out", out2, "init"])
    if r.returncode != 0:
        issues.append("init (declared contract) failed")
    else:
        r2 = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
        for k in ("translation", "terms"):
            if k not in r2:
                issues.append("declared output_contract key not preserved: %s" % k)
    return issues


def check_consistency_demo(tmp):
    issues = []
    src = os.path.join(tmp, "src")
    tgt = os.path.join(tmp, "tgt")
    os.makedirs(src, exist_ok=True)
    os.makedirs(tgt, exist_ok=True)

    source_text = (
        "The film thickness was 42.7 \u03bcm. The model has 1.5 million "
        "parameters and 3.2% error (2019). See Figure 1 and Table 2. [12]\n"
    )
    target_text = (
        "\u8be5\u6a21\u578b\u8fbe\u5230150\u4e07\u53c2\u6570\uff0c"
        "\u8bef\u5dee3.2%\uff082019\uff09\u3002\u89c1\u56fe1\u548c\u88682\u3002"
        "[12] RMSE \u4e0e root mean square error \u5747\u53ef\u7528\u3002\n"
    )
    # target deliberately omits "42.7 um" and uses two variants of one term.
    with open(os.path.join(src, "source.txt"), "w", encoding="utf-8") as f:
        f.write(source_text)
    with open(os.path.join(tgt, "translation.md"), "w", encoding="utf-8") as f:
        f.write(target_text)

    glossary = {"terms": [
        {"canonical": "\u5747\u65b9\u6839\u8bef\u5dee",
         "variants": ["root mean square error", "RMSE"]},
    ]}
    glossary_path = os.path.join(tmp, "glossary.json")
    with open(glossary_path, "w", encoding="utf-8") as f:
        json.dump(glossary, f, ensure_ascii=False)

    # a result.json with one valid quote and one fabricated quote
    result_path = os.path.join(tmp, "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "case_id": "selftest",
            "fact_source_map": [
                {"item": "num", "kind": "number", "value": "42.7",
                 "quote": "The film thickness was 42.7 \u03bcm"},
                {"item": "fake", "kind": "claim", "value": "x",
                 "quote": "A sentence that never appeared in the source"},
            ],
        }, f, ensure_ascii=False)

    rep = os.path.join(tmp, "report.json")
    r = run([CHECK, "--source", src, "--target", tgt,
             "--glossary", glossary_path, "--result", result_path,
             "--out", rep])
    if r.returncode != 0:
        issues.append("check_consistency.py crashed: %s" % r.stderr.strip()[:300])
        return issues
    report = json.load(open(rep, encoding="utf-8"))
    types = {e["type"] for e in report.get("errors", [])}
    wtypes = {w["type"] for w in report.get("warnings", [])}
    if "number_missing" not in types:
        issues.append("number omission not detected")
    if "unit_missing" not in wtypes:
        issues.append("unit omission not detected")
    if "term_variant_collision" not in wtypes:
        issues.append("term variant collision not detected")
    if "fact_quote_not_found" not in wtypes:
        issues.append("fabricated fact quote not detected")
    if "citation_missing" in wtypes:
        issues.append("false-positive citation_missing (12 should be preserved)")
    return issues


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true",
                    help="skip the planted-bug demo (fast smoke check)")
    args = ap.parse_args(argv)
    all_issues = []
    for label, fn in (
        ("frontmatter", check_frontmatter),
        ("script --help", check_help),
        ("references", check_references),
    ):
        issues = fn()
        all_issues.extend(issues)
        print("[%s] %s" % ("PASS" if not issues else "FAIL", label))
        for i in issues:
            print("   - " + i)

    with tempfile.TemporaryDirectory(prefix="skill-selftest-") as tmp:
        issues = check_workflow(tmp)
        all_issues.extend(issues)
        print("[%s] main.py workflow" % ("PASS" if not issues else "FAIL"))
        for i in issues:
            print("   - " + i)

        if not args.quick:
            issues = check_consistency_demo(tmp)
            all_issues.extend(issues)
            print("[%s] check_consistency planted-bug demo"
                  % ("PASS" if not issues else "FAIL"))
            for i in issues:
                print("   - " + i)

    if all_issues:
        print("\nSELFTEST FAILED (%d issue(s))" % len(all_issues))
        return 1
    print("\nSELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
