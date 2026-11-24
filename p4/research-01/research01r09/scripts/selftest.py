#!/usr/bin/env python3
"""Offline self-test for the deep-research skill (standard library only).

Builds a tiny demo input in a temp directory, runs the full pipeline through
main.py exactly as the agent would call it, then runs selfcheck and prints a
PASS/FAIL summary. Always exits 0 so a harness is never crashed by a check.

Run from anywhere:
    python3 -B scripts/selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args):
    proc = subprocess.run([sys.executable, "-B", MAIN] + args,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def main():
    tmp = tempfile.mkdtemp(prefix="deepresearch_selftest_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    mat = os.path.join(inp, "materials")
    os.makedirs(mat)
    os.makedirs(work)

    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "demo-1", "output_contract": {
            "research_product": None, "evidence_matrix": None, "uncertainty": None}},
            fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo task\n\nExplain the material and note its limits.\n")
    with open(os.path.join(mat, "paper.md"), "w", encoding="utf-8") as fh:
        fh.write("p3 §2.2: the sample reports an effect size of 0.42.\n")

    steps = []
    for name, argv in (
        ("skeleton", ["skeleton", "--input", inp, "--output", out, "--work", work]),
        ("merge", ["merge", "--input", inp, "--output", out, "--work", work,
                   "--payload", os.path.join(work, "payload.json")]),
        ("finalize", ["finalize", "--input", inp, "--output", out, "--work", work]),
        ("selfcheck", ["selfcheck", "--input", inp, "--output", out, "--work", work]),
    ):
        if name == "merge":
            payload = {
                "research_product": {
                    "type": "synthesis", "question": "demo question",
                    "summary": "demo conclusion", "conclusions": [
                        {"id": "C1", "text": "effect size 0.42", "evidence_ids": ["E1"],
                         "strength": "established"}]},
                "evidence_matrix": [
                    {"id": "E1", "subquestion": "SQ1", "source": "materials/paper.md",
                     "locator": "p3 §2.2", "quote": "effect size of 0.42",
                     "claim": "effect size 0.42", "judgment": "supports",
                     "reason": "direct quote", "conclusion_ids": ["C1"]}],
                "uncertainty": {"conflicts": [], "gaps": ["no control group reported"],
                                "undetermined": [], "biases": []},
                "sources": [{"id": "materials/paper.md", "type": "markdown"}],
                "status": "partial",
            }
            with open(os.path.join(work, "payload.json"), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
        with open(os.path.join(work, "decomposition.md"), "w", encoding="utf-8") as fh:
            fh.write("## 子问题\n- SQ1 效应量 -> SQ2 适用范围（依赖）\n- SQ2 适用范围\n")
        with open(os.path.join(work, "search_plan.md"), "w", encoding="utf-8") as fh:
            fh.write("## 检索计划\n顺序: SQ1 -> SQ2；来源: materials；停止: 材料穷尽\n")
        code, out_txt, err_txt = run(argv)
        steps.append((name, code, out_txt, err_txt))

    all_ok = all(code == 0 for _, code, _, _ in steps)
    report = []
    for name, code, out_txt, err_txt in steps:
        report.append("[%s] exit=%d" % (name, code))
        if err_txt.strip():
            report.append("  stderr: %s" % err_txt.strip()[:300])
    # Parse selfcheck summary.
    check_ok = False
    for name, _, out_txt, _ in steps:
        if name == "selfcheck":
            try:
                check_ok = json.loads(out_txt).get("ok", False)
            except ValueError:
                check_ok = False
    all_ok = all_ok and check_ok
    print("\n".join(report))
    print("SELFTEST %s (temp dir: %s)" % ("PASS" if all_ok else "FAIL", tmp))
    # Keep the temp dir on failure for inspection; remove on success.
    if all_ok:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
