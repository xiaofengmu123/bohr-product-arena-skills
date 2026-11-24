#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-plan 自检脚本（scripts/selftest.py）。

覆盖四类自检，全部通过退出码 0，任一失败退出码 1：
  1) 空输入目录：写合法结果，status=insufficient_evidence，不抛异常；
  2) 坏 JSON：捕获并记录到 errors，继续产出合法结果；
  3) 缺输入目录：写合法结果，status=input_error；
  4) 中间表示全链路 round-trip：合成材料 → index → verify → grade → assemble
     → validate，验证"中间表示与可复核"的确定性装配（含故意写错的 quote 被检出）。

用法（在 SKILL.md 所在目录下）：
  python3 -B scripts/selftest.py
"""

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import main as main_mod  # noqa: E402
import litqa as litqa_mod  # noqa: E402

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s%s" % (label, (" — " + detail) if detail else ""))
    else:
        FAIL += 1
        print("  [FAIL] %s%s" % (label, (" — " + detail) if detail else ""))


def scenario_empty():
    print("[scenario] empty input directory")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, out, work = root / "in", root / "out", root / "work"
        inp.mkdir()
        result, issues = main_mod.write_skeleton(str(inp), str(out), str(work))
        check("result.json written", (out / "result.json").exists())
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        check("status insufficient_evidence", r.get("status") == "insufficient_evidence",
              r.get("status"))
        check("case_id present", bool(r.get("case_id")))
        check("plan.json written", (work / "plan.json").exists())
        check("matrix.json written", (work / "assessment_matrix.json").exists())


def scenario_bad_json():
    print("[scenario] malformed case.json")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, out, work = root / "in", root / "out", root / "work"
        inp.mkdir()
        (inp / "case.json").write_text("{ this is not json", encoding="utf-8")
        result, issues = main_mod.write_skeleton(str(inp), str(out), str(work))
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        check("valid result.json despite bad input", r.get("status") == "partial")
        check("error recorded", len(r.get("errors", [])) > 0, str(r.get("errors", [])[:1]))


def scenario_missing_input():
    print("[scenario] missing input directory")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, out, work = root / "no-such-input", root / "out", root / "work"
        result, issues = main_mod.write_skeleton(str(inp), str(out), str(work))
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        check("status input_error", r.get("status") == "input_error", r.get("status"))
        check("result.json exists", (out / "result.json").exists())


def scenario_roundtrip():
    print("[scenario] intermediate-representation round-trip")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, out, work = root / "in", root / "out", root / "work"
        inp.mkdir()
        (work / "extracted").mkdir(parents=True)

        # 合成材料（明确标注：仅用于自检）
        paper = (
            "Title: Synthetic placebo-controlled trial for self-test.\n\n"
            "Methods. This was a double-blind randomized controlled trial. "
            "A total of 240 participants were randomly assigned in a 1:1 ratio "
            "to intervention or placebo. The primary outcome was mortality at "
            "30 days.\n\n"
            "Results. All-cause mortality occurred in 12 of 120 participants in "
            "the intervention group and 20 of 120 in the placebo group "
            "(relative risk 0.60).\n\n"
            "Discussion. Limitations include short follow-up and no blinding "
            "assessment reported."
        )
        (work / "extracted" / "paper.txt").write_text(paper, encoding="utf-8")

        # 1) index
        index, i_issues = litqa_mod.run_index(str(work), str(work / "extracted"))
        check("index produced 1 document", len(index.get("documents", [])) == 1,
              "docs=%d" % len(index.get("documents", [])))
        check("sentence anchors exist",
              any(a["kind"] == "sentence"
                  for d in index["documents"] for p in d["pages"] for a in p["anchors"]))

        # 2) 写 assessment_matrix.json（引用真实句子 + 一条故意写错的 quote）
        anchors = [a for d in index["documents"] for p in d["pages"] for a in p["anchors"]]
        true_sent = next(a for a in anchors if a["kind"] == "sentence"
                         and "240 participants" in a["norm"])
        matrix = {
            "schema": main_mod.SCHEMA_MATRIX,
            "case_id": "selftest",
            "status": "ok",
            "document_ref": "d0",
            "rubric_version": "references/rubric.md",
            "rows": [
                {"id": "r1", "dimension": "research_design", "checkpoint": "randomization",
                 "critical": True, "verdict": "adequate",
                 "judgment": "明确描述随机分配与双盲设计。",
                 "evidence_id": true_sent["locator"], "locator": true_sent["locator"],
                 "quote": true_sent["text"][:60], "basis": "direct_quote",
                 "confidence": "high", "abstained": False, "abstain_reason": None},
                {"id": "r2", "dimension": "evidence_sufficiency", "checkpoint": "sample_size",
                 "critical": True, "verdict": "high_risk",
                 "judgment": "样本量较小，未报告把握度计算。",
                 "evidence_id": true_sent["locator"], "locator": true_sent["locator"],
                 "quote": "this phrase does not exist anywhere in the text",
                 "basis": "inference", "confidence": "low", "abstained": False,
                 "abstain_reason": None},
            ],
        }
        main_mod.atomic_write_json(work / "assessment_matrix.json", matrix)

        # 3) verify：应检出 r2 的 quote 不存在
        report, v_issues = litqa_mod.run_verify(str(work))
        check("verify flags bad quote", report.get("unverified") == 1,
              "unverified=%d" % report.get("unverified"))
        check("verify passes good quote",
              any(it["row_id"] == "r1" and it["verified"] for it in report["items"]))

        # 4) grade
        grade, g_issues = litqa_mod.run_grade(str(work))
        check("grade overall high", grade.get("overall") == "high", grade.get("overall"))
        check("grade has derivation", bool(grade.get("derivation")))

        # 5) 写 plan.json
        plan = main_mod.plan_skeleton("selftest", "ok")
        plan["research_question"]["text"] = "Synthetic self-test question."
        plan["retrieval_plan"]["query"] = "synthetic"
        plan["sources"] = [{"id": "d0", "type": "text", "path_or_uri": "extracted/paper.txt"}]
        plan["change_log"] = [{"time_utc": "2026-01-01T00:00:00Z", "event": "selftest",
                               "detail": "synthetic"}]
        main_mod.atomic_write_json(work / "plan.json", plan)

        # 6) assemble
        result, a_issues = litqa_mod.run_assemble(str(inp), str(work), str(out))
        check("assemble wrote result.json", (out / "result.json").exists())
        check("assemble wrote assessment.md", (out / "assessment.md").exists())
        check("assessment rows carried over", len(result.get("assessment", [])) == 2)

        # 7) validate：无 error 级问题
        errors, warnings = main_mod.validate_dir(str(inp), str(out), str(work))
        check("validate has no errors", len(errors) == 0, "; ".join(errors))
        check("validate flags quote mismatch as warning", any("r2" in w for w in warnings))


def main():
    print("litqa-plan selftest")
    scenario_empty()
    scenario_bad_json()
    scenario_missing_input()
    scenario_roundtrip()
    print("")
    print("RESULT: %d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

