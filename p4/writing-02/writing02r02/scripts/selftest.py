# -*- coding: utf-8 -*-
"""Built-in self-test for the academic-polish Skill package.

Exercises the real CLI commands against throwaway fixtures in a temp
directory: normal scaffold -> clean verify -> finalize -> selfcheck, a
deliberately broken edits.json to prove verify catches drifts, and an
empty-input degradation case. Pure standard library; deterministic fixtures;
no network and no model calls.

Run directly:  python3 -B scripts/selftest.py
or via CLI:     python3 -B scripts/main.py selftest
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402

SAMPLE = (
    "We enrolled 120 patients with severe sepsis (Figure 1).\n"
    "Mortality at 28 days was 22.5% in the intervention group and 31.2% in "
    "the control group [1].\n"
    "The intervention significantly reduced mortality (P = 0.03).\n"
    "These results suggest that X may improve survival [2,3].\n"
    "However, the small sample size (n = 120) limits generalizability.\n"
)

CASE = {
    "id": "fixture-case-01",
    "output_contract": {
        "deliverable": None,
        "fact_source_map": [],
        "version_boundary": [],
        "custom_extra": None,
    },
    "text": SAMPLE,
}

TASK = (
    "# 学术语言润色\n\n"
    "润色上述摘要，保持数字、单位、引用与图表指向不变，"
    "给出逐句修改说明。\n"
)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _clean_units():
    sentences = [
        "We enrolled 120 patients with severe sepsis (Figure 1).",
        "Mortality at 28 days was 22.5% in the intervention group and 31.2% "
        "in the control group [1].",
        "The intervention significantly reduced mortality (P = 0.03).",
        "These results suggest that X may improve survival [2,3].",
        "However, the small sample size (n = 120) limits generalizability.",
    ]
    units = []
    for i, s in enumerate(sentences, 1):
        units.append({
            "original": s,
            "revised": s,
            "edit_type": "retain",
            "reason": "已合规，仅作覆盖记录",
            "evidence": {
                "locator": "case.json.text#s%d" % i,
                "quote": s,
            },
        })
    return units


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def run():
    tmp = tempfile.mkdtemp(prefix="polish-selftest-")
    passed = []
    try:
        # --- normal case ---
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        _write(os.path.join(inp, "case.json"), json.dumps(CASE, ensure_ascii=False))
        _write(os.path.join(inp, "task.md"), TASK)

        s = main.cmd_scaffold(inp, out, work)
        _check(s["case_id"] == "fixture-case-01", "case_id mismatch")
        _check(s["status"] in ("partial", "ok"), "scaffold status invalid")
        _check(s["facts"] >= 4, "fact pool under-extracted: %d" % s["facts"])
        _check(os.path.isfile(os.path.join(out, "result.json")),
               "partial result.json missing")
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        _check(r.get("custom_extra") is None, "unknown contract key dropped")
        _check(r.get("deliverable") == "manuscript.md", "deliverable not set")
        ledger = json.load(open(os.path.join(work, "phase_ledger.json"),
                                encoding="utf-8"))
        _check(sum(p["budget_s"] for p in ledger["phases"]) == 900,
               "phase budget != 900s")
        _check(sum(p["requests"] for p in ledger["phases"]) == 64,
               "phase requests != 64")
        passed.append("scaffold")

        # --- clean edits -> verify clean ---
        edits = {"meta": {"case_id": "fixture-case-01",
                          "source_file": "case.json.text",
                          "target_lang": "en", "genre": "abstract",
                          "pending_human": [], "uncertainty": []},
                 "source_text": SAMPLE,
                 "units": _clean_units()}
        main.write_json(os.path.join(work, "edits.json"), edits)
        main.write_json(os.path.join(work, "glossary.json"), {"terms": []})
        v = main.cmd_verify(inp, out, work)
        _check(len(v["report"]["errors"]) == 0, "clean edits reported errors: %r"
               % v["report"]["errors"])
        passed.append("verify-clean")

        # --- finalize + selfcheck ---
        f = main.cmd_finalize(inp, out, work)
        _check(f["status"] == "ok", "finalize status not ok: %r" % f["status"])
        _check(os.path.isfile(os.path.join(out, "manuscript.md")),
               "manuscript.md missing")
        ck = main.cmd_selfcheck(inp, out, work)
        _check(ck["ok"], "selfcheck failed: %r" % ck["problems"])
        passed.append("finalize+selfcheck")

        # --- broken edits -> verify must flag drifts ---
        broken = _clean_units()
        broken[1]["revised"] = (
            "Mortality at 28 days was 25.5% in the intervention group and "
            "31.2% in the control group.")  # number changed + citation dropped
        broken[2]["evidence"] = {}  # factual unit without evidence
        main.write_json(os.path.join(work, "edits.json"),
                        {"meta": {}, "source_text": SAMPLE, "units": broken})
        vb = main.cmd_verify(inp, out, work)
        codes = {e["code"] for e in vb["report"]["errors"]}
        _check("number_missing" in codes, "number drift not caught")
        _check("citation_missing" in codes, "citation drop not caught")
        _check("missing_evidence" in codes, "missing evidence not caught")
        passed.append("verify-broken")

        # --- empty input degradation ---
        empty_in = os.path.join(tmp, "empty")
        empty_out = os.path.join(tmp, "empty_out")
        empty_work = os.path.join(tmp, "empty_work")
        os.makedirs(empty_in, exist_ok=True)
        es = main.cmd_scaffold(empty_in, empty_out, empty_work)
        _check(es["status"] == "insufficient_evidence",
               "empty input status: %r" % es["status"])
        er = json.load(open(os.path.join(empty_out, "result.json"),
                            encoding="utf-8"))
        _check(er.get("case_id"), "empty input case_id missing")
        passed.append("empty-input")

        print("selftest PASS: " + ", ".join(passed))
        return 0
    except Exception as e:  # noqa: BLE001
        print("selftest FAIL: %s" % e)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run())
