#!/usr/bin/env python3
"""End-to-end self-test for the deep-research evidence-ledger skill.

Runs the real CLI (main.py) against synthetic, clearly-labelled example inputs
in a temporary directory and asserts the robustness contract:

  1. empty input  -> exit 0, status=insufficient_evidence
  2. bad JSON     -> exit 0, status=input_error, errors recorded
  3. normal case  -> skeleton -> card -> finalize -> selfcheck, valid JSON,
                     locator+quote present, evidence back-links resolve
  4. determinism  -> two finalize runs produce the same structural output

No prepared answers or benchmark scores are produced; every assertion uses
synthetic demo data and is labelled as such.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable

CASES = []


def run(args, cwd=None):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([PY, "-B", MAIN] + args, cwd=cwd or HERE,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)
    return proc


def check(name, condition, detail=""):
    CASES.append((name, bool(condition), detail))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                           (" -- " + detail) if detail else ""))
    return condition


def make_demo(base):
    """Create a labelled synthetic demo input tree. Returns its input dir."""
    inp = os.path.join(base, "input")
    work = os.path.join(base, "work")
    out = os.path.join(base, "output")
    for d in (inp, work, out, os.path.join(inp, "materials")):
        os.makedirs(d, exist_ok=True)
    case = {
        "case_id": "demo-0001",
        "task": "示例：评估某干预对某结局的影响（合成演示，非正式案例）",
        "output_contract": {
            "research_product": {"type": "synthesis", "question": "", "summary": "",
                                 "conclusions": []},
            "evidence_matrix": [],
            "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
            "custom_metric": "number",
        },
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 示例任务（合成演示）\n\n围绕研究问题组织证据并说明边界。\n")
    with open(os.path.join(inp, "materials", "paper_a.txt"), "w", encoding="utf-8") as fh:
        fh.write("p1 示例段落：干预组结局优于对照组（合成数值，仅演示）。\n")
    with open(os.path.join(inp, "materials", "paper_b.txt"), "w", encoding="utf-8") as fh:
        fh.write("p3 冲突来源：另一项研究未观察到显著差异（合成数值，仅演示）。\n")
    return inp, work, out


def seed_ledger(inp, work, out):
    cards = [
        {"id": "E1", "subquestion": "SQ1", "source": "materials/paper_a.txt",
         "locator": "p1", "quote": "干预组结局优于对照组",
         "claim": "示例材料声称干预有效", "judgment": "supports",
         "reason": "逐字引用自 paper_a.txt", "conclusion_ids": ["C1"]},
        {"id": "E2", "subquestion": "SQ2", "source": "materials/paper_b.txt",
         "locator": "p3", "quote": "未观察到显著差异",
         "claim": "示例材料报告无效", "judgment": "conflicts",
         "reason": "与 E1 方向相反，如实并列", "conclusion_ids": ["C1"]},
    ]
    with open(os.path.join(work, "evidence_cards.jsonl"), "w", encoding="utf-8") as fh:
        for card in cards:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")
    with open(os.path.join(work, "product.json"), "w", encoding="utf-8") as fh:
        json.dump({"type": "synthesis", "question": "示例研究问题（合成演示）",
                   "summary": "示例综合结论：证据方向不一致，需并列呈现。"},
                  fh, ensure_ascii=False, indent=2)
    with open(os.path.join(work, "conclusions.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "C1", "text": "示例结论：现有证据方向不一致",
                             "evidence_ids": ["E1", "E2"],
                             "strength": "inferred"}, ensure_ascii=False) + "\n")
    with open(os.path.join(work, "uncertainty.json"), "w", encoding="utf-8") as fh:
        json.dump({"conflicts": [{"sources": ["paper_a.txt", "paper_b.txt"],
                                  "resolution": "如实并列，材料不足无法裁定"}],
                   "biases": ["仅两条合成证据，存在选择性偏倚"],
                   "gaps": ["缺少长期随访结局与样本量信息，需补充原始数据"],
                   "undetermined": ["总体效应方向无法由现有两条证据确定"]},
                  fh, ensure_ascii=False, indent=2)
    with open(os.path.join(work, "decomposition.md"), "w", encoding="utf-8") as fh:
        fh.write("## SQ1 干预是否影响结局\n## SQ2 各来源结论是否一致\n")
    with open(os.path.join(work, "search_plan.md"), "w", encoding="utf-8") as fh:
        fh.write("顺序：先 SQ1 后 SQ2；来源范围：材料内全部文本；停止条件：证据覆盖两子问题。\n")


def main():
    base = tempfile.mkdtemp(prefix="deep-research-selftest-")
    passed = True
    print("== 1) empty input ==")
    empty = os.path.join(base, "empty")
    os.makedirs(os.path.join(empty, "input"))
    os.makedirs(os.path.join(empty, "output"))
    os.makedirs(os.path.join(empty, "work"))
    proc = run(["skeleton", "--input", os.path.join(empty, "input"),
                "--output", os.path.join(empty, "output"),
                "--work", os.path.join(empty, "work")])
    r = json.load(open(os.path.join(empty, "output", "result.json"), encoding="utf-8"))
    passed &= check("empty exit 0", proc.returncode == 0, "rc=%d" % proc.returncode)
    passed &= check("empty status", r.get("status") == "insufficient_evidence", r.get("status"))

    print("== 2) bad JSON ==")
    bad = os.path.join(base, "bad")
    for d in ("input", "output", "work"):
        os.makedirs(os.path.join(bad, d))
    with open(os.path.join(bad, "input", "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    proc = run(["skeleton", "--input", os.path.join(bad, "input"),
                "--output", os.path.join(bad, "output"),
                "--work", os.path.join(bad, "work")])
    r = json.load(open(os.path.join(bad, "output", "result.json"), encoding="utf-8"))
    passed &= check("bad-json exit 0", proc.returncode == 0, "rc=%d" % proc.returncode)
    passed &= check("bad-json status", r.get("status") == "input_error", r.get("status"))
    passed &= check("bad-json errors recorded", bool(r.get("errors")))

    print("== 3) normal case ==")
    inp, work, out = make_demo(os.path.join(base, "normal"))
    seed_ledger(inp, work, out)
    proc = run(["skeleton", "--input", inp, "--output", out, "--work", work])
    passed &= check("skeleton exit 0", proc.returncode == 0)
    proc = run(["finalize", "--input", inp, "--output", out, "--work", work])
    passed &= check("finalize exit 0", proc.returncode == 0)
    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    passed &= check("case_id echoed", r.get("case_id") == "demo-0001", r.get("case_id"))
    passed &= check("contract echoed", "custom_metric" in r, "custom_metric present")
    passed &= check("status ok", r.get("status") == "ok", r.get("status"))
    cards = r.get("evidence_matrix") or []
    passed &= check("evidence locator+quote", all(c.get("locator") and c.get("quote") for c in cards))
    concl = (r.get("research_product") or {}).get("conclusions") or []
    ids = {c.get("id") for c in cards}
    passed &= check("back-links resolve", all(
        e in ids for c in concl for e in (c.get("evidence_ids") or [])))
    proc = run(["selfcheck", "--input", inp, "--output", out, "--work", work])
    passed &= check("selfcheck exit 0", proc.returncode == 0)

    print("== 4) determinism ==")
    run(["finalize", "--input", inp, "--output", out, "--work", work])
    r2 = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    strip = lambda x: {k: v for k, v in x.items()
                       if k not in ("runtime_seconds", "change_log")}  # noqa: E731
    passed &= check("deterministic layout", strip(r) == strip(r2))

    shutil.rmtree(base, ignore_errors=True)
    print("\nSUMMARY: %d/%d passed" % (sum(1 for _, ok, _ in CASES if ok), len(CASES)))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
