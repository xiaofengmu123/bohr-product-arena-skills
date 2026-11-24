#!/usr/bin/env python3
"""Package self-test for academic-translation-bilingual.

Verifies: SKILL.md frontmatter, script runnability, and an end-to-end fixture
(init -> consistency check -> report). Standard library only. Exit 0 = pass.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run(args):
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("%s %s%s" % (status, name, (" - " + detail if detail else "")))
    return bool(cond)


def main():
    ok = True
    skill = os.path.join(ROOT, "SKILL.md")
    ok &= check("SKILL.md exists", os.path.exists(skill), skill)
    txt = ""
    if os.path.exists(skill):
        with open(skill, "r", encoding="utf-8") as f:
            txt = f.read()
    m = re.match(r"^---\s*\n(.*?)\n---", txt, re.DOTALL)
    ok &= check("SKILL.md has frontmatter", bool(m))
    if m:
        fm = m.group(1)
        for key in ("name:", "description:", "version:"):
            ok &= check("frontmatter contains " + key.strip(":"), key in fm)
    for s in ("main.py", "check_consistency.py"):
        p = os.path.join(HERE, s)
        ok &= check("%s exists" % s, os.path.exists(p), p)
        if os.path.exists(p):
            r = run([p, "--help"])
            ok &= check("%s --help rc=0" % s, r.returncode == 0, "rc=%d" % r.returncode)

    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input")
        out = os.path.join(td, "output")
        work = os.path.join(td, "work")
        for d in (inp, out, work):
            os.makedirs(d)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "demo-1",
                       "output_contract": {"deliverables": [], "fact_source_map": [],
                                           "version_boundary": {}}}, f, ensure_ascii=False)
        with open(os.path.join(inp, "paper.txt"), "w", encoding="utf-8") as f:
            f.write("The model achieved 95.3% accuracy (n=1024) [1].\n")
        with open(os.path.join(out, "glossary.json"), "w", encoding="utf-8") as f:
            json.dump({"terms": [{"canonical": "准确率", "src": "accuracy",
                                  "variants": ["ACC"], "note": "全文统一"}]}, f,
                      ensure_ascii=False)
        with open(os.path.join(out, "bilingual.md"), "w", encoding="utf-8") as f:
            f.write("### [SEG-01]\n- src: The model achieved 95.3% accuracy (n=1024) [1].\n"
                    "- tgt: 该模型达到 95.3% 的准确率（n=1024）[1]。\n- src_loc: paper.txt:1\n\n"
                    "## 参考文献\n\n[1] Author, A. A benchmark of translation fidelity. 2020.\n")

        r = run([os.path.join(HERE, "main.py"), "--init",
                 "--case", os.path.join(inp, "case.json"),
                 "--out", os.path.join(out, "result.json"), "--work", work])
        ok &= check("main.py --init rc=0", r.returncode == 0,
                    r.stdout.strip().replace("\n", " ")[:100])
        res_path = os.path.join(out, "result.json")
        ok &= check("result.json created", os.path.exists(res_path))
        if os.path.exists(res_path):
            with open(res_path, "r", encoding="utf-8") as f:
                rj = json.load(f)
            ok &= check("result.json case_id", rj.get("case_id") == "demo-1")
            ok &= check("result.json status", rj.get("status") == "in_progress")
            ok &= check("result.json output_contract keys",
                        all(k in rj for k in ("deliverables", "fact_source_map",
                                              "version_boundary")))
            ok &= check("result.json extra keys",
                        all(k in rj for k in ("sources", "uncertainty", "change_log")))

        r = run([os.path.join(HERE, "main.py"), "--finalize", "--status", "success",
                 "--case", os.path.join(inp, "case.json"),
                 "--out", os.path.join(out, "result.json"), "--work", work])
        ok &= check("main.py --finalize rc=0", r.returncode == 0)

        r = run([os.path.join(HERE, "check_consistency.py"), "--dir", out,
                 "--glossary", os.path.join(out, "glossary.json"),
                 "--out", os.path.join(work, "report.json")])
        ok &= check("check_consistency.py rc=0", r.returncode == 0,
                    r.stdout.strip().replace("\n", " "))
        rep_path = os.path.join(work, "report.json")
        ok &= check("report.json created", os.path.exists(rep_path))
        if os.path.exists(rep_path):
            with open(rep_path, "r", encoding="utf-8") as f:
                rep = json.load(f)
            ok &= check("report has summary", "summary" in rep)
            ok &= check("report has segments=1", rep.get("segments") == 1)
            sev = {i["severity"] for i in rep.get("issues", [])}
            ok &= check("demo fixture clean of errors", "error" not in sev,
                        "severities=%s" % sorted(sev))

    print("\nSELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
