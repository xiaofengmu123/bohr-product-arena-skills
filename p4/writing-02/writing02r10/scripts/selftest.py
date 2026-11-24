# -*- coding: utf-8 -*-
"""Deterministic self-test for the academic-polish conservation Skill.

Runs the four subcommands (scaffold / verify / finalize / selfcheck) on
synthetic inputs that exercise:
  * a normal draft with numbers / units / citations / figures / hedges
  * a "good" edit that preserves every feature
  * a "bad" edit that drops a number and strengthens a hedge (must fail)
  * empty input directory            -> insufficient_evidence, exit 0
  * malformed JSON in case.json      -> recorded degradation, no crash
  * bad-encoding draft file          -> decoded with fallback, no crash

Uses only the standard library. Exits 0 if every expectation holds, 1 otherwise.
Run from anywhere:  python scripts/selftest.py
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402
import checks  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name + (("  -- " + detail) if detail else ""))


def ns(**kw):
    return argparse.Namespace(**kw)


def run(cmd, input_dir, output_dir, work_dir):
    args = ns(command=cmd, input=input_dir, output=output_dir, work=work_dir)
    return {"scaffold": main.scaffold, "verify": main.verify,
            "finalize": main.finalize, "selfcheck": main.selfcheck}[cmd](args)


def make_case(tmp, case_body, task_text, draft_text=None, draft_name="materials/draft.md",
              draft_bytes=None, draft_encoding="utf-8"):
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write(case_body)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write(task_text)
    if draft_text is not None:
        md = os.path.join(inp, draft_name)
        os.makedirs(os.path.dirname(md), exist_ok=True)
        if draft_bytes is None:
            draft_bytes = draft_text.encode(draft_encoding)
        with open(md, "wb") as f:
            f.write(draft_bytes)
    return inp


def main_test():
    tmp = tempfile.mkdtemp(prefix="polish_selftest_")
    try:
        draft = ("Tumor growth was reduced by 45.2% after treatment (p = 0.01), "
                 "suggesting efficacy (Fig. 3). "
                 "These data confirm that the drug is effective in 8 of 10 mice.")
        case = json.dumps({
            "id": "case-selftest-1",
            "output_contract": {
                "deliverable": "manuscript.md",
                "fact_source_map": [],
                "version_boundary": [],
                "extra_unknown_key": None,
            },
        }, ensure_ascii=False)
        task = "# 学术语言润色\n\n润色下面的摘要，保留事实与来源。"
        inp = make_case(tmp, case, task, draft)

        out = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")
        run("scaffold", inp, out, work)
        res = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("scaffold writes partial result.json", os.path.isfile(os.path.join(out, "result.json")))
        check("scaffold status is partial", res.get("status") == "partial")
        check("scaffold preserves unknown contract key",
              "extra_unknown_key" in res, res.get("status"))
        reg = json.load(open(os.path.join(work, "register.json"), encoding="utf-8"))
        check("register frozen with sentences", len(reg.get("sentences", [])) >= 2,
              "n={}".format(len(reg.get("sentences", []))))

        # --- good edit: preserve features, change wording ---
        edits = json.load(open(os.path.join(work, "edits.json"), encoding="utf-8"))
        good_map = {
            0: "Tumour growth decreased by 45.2% after treatment (p = 0.01), suggesting efficacy (Fig. 3).",
            1: "These data confirm that the drug is effective in 8 of 10 mice.",
        }
        for u in edits["units"]:
            u["revised"] = good_map.get(u["sentence_id"], u["original"])
            u["edit_type"] = "grammar"
            u["reason"] = "wording change only; facts conserved"
        json.dump(edits, open(os.path.join(work, "edits.json"), "w", encoding="utf-8"), ensure_ascii=False)
        run("verify", inp, out, work)
        rep = json.load(open(os.path.join(work, "check_report.json"), encoding="utf-8"))
        check("good edit passes verify", rep["ok"], "errors={} warnings={}".format(
            rep["summary"]["errors"], rep["summary"]["warnings"]))
        run("finalize", inp, out, work)
        run("selfcheck", inp, out, work)
        res2 = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("good finalize status ok", res2.get("status") == "ok", res2.get("status"))
        check("manuscript.md exists", os.path.isfile(os.path.join(out, "manuscript.md")))
        sc = json.load(open(os.path.join(out, "selfcheck_report.json"), encoding="utf-8"))
        check("selfcheck ok on good output", sc.get("ok"), str(sc.get("summary")))

        # --- bad edit: drop number + strengthen hedge ---
        bad_map = {
            0: "Tumor growth was reduced by 50.0% after treatment (p = 0.01), demonstrating efficacy (Fig. 3).",
            1: "These data confirm that the drug is effective in 8 of 10 mice.",
        }
        for u in edits["units"]:
            u["revised"] = bad_map.get(u["sentence_id"], u["original"])
            u["hedge_justified"] = False
            u["justification"] = ""
        json.dump(edits, open(os.path.join(work, "edits.json"), "w", encoding="utf-8"), ensure_ascii=False)
        run("verify", inp, out, work)
        repb = json.load(open(os.path.join(work, "check_report.json"), encoding="utf-8"))
        cats = {e.get("category") for e in repb["errors"]}
        check("bad edit is rejected (errors>0)", repb["summary"]["errors"] > 0,
              "errors={}".format(repb["summary"]["errors"]))
        check("bad edit flags dropped number", "numbers" in cats, str(cats))
        check("bad edit flags hedge strengthening", "hedges" in cats, str(cats))

        # --- empty input directory ---
        empty = os.path.join(tmp, "empty_in")
        os.makedirs(empty, exist_ok=True)
        eout = os.path.join(tmp, "empty_out")
        ework = os.path.join(tmp, "empty_work")
        rc = run("scaffold", empty, eout, ework)
        eres = json.load(open(os.path.join(eout, "result.json"), encoding="utf-8"))
        check("empty input -> insufficient_evidence (exit 0)",
              rc == 0 and eres.get("status") == "insufficient_evidence", eres.get("status"))

        # --- malformed JSON in case.json ---
        bad_json = make_case(os.path.join(tmp, "badjson"), "{not valid json", task, draft)
        bout = os.path.join(tmp, "badjson_out")
        bwork = os.path.join(tmp, "badjson_work")
        run("scaffold", bad_json, bout, bwork)
        bres = json.load(open(os.path.join(bout, "result.json"), encoding="utf-8"))
        check("malformed case.json -> degradation recorded, no crash",
              "case.json unreadable" in " ".join(bres.get("degradations", [])) or
              bres.get("status") in ("partial", "input_error"), bres.get("status"))

        # --- bad-encoding draft file (GBK bytes, not valid UTF-8) ---
        zh_draft = "肿瘤生长经治疗后下降 45.2%（p = 0.01），提示有效（图 3）。这些数据证实该药有效。"
        enc_draft = zh_draft.encode("gbk")
        enc_in = make_case(os.path.join(tmp, "badenc"), case, task,
                           draft_text=zh_draft, draft_name="materials/bad.md",
                           draft_bytes=enc_draft, draft_encoding="gbk")
        cout = os.path.join(tmp, "badenc_out")
        cwork = os.path.join(tmp, "badenc_work")
        run("scaffold", enc_in, cout, cwork)
        cres = json.load(open(os.path.join(cout, "result.json"), encoding="utf-8"))
        check("bad encoding -> readable without crash",
              cres.get("status") in ("partial", "ok"), cres.get("status"))

        # --- engine unit checks ---
        f = checks.extract_features("We observed a 3.2-fold increase (p < 0.001) in CD8+ T cells (Fig. 2).")
        check("feature extraction finds number", "3.2" in f["numbers"], str(f["numbers"]))
        check("feature extraction finds citation-ish figure", any("fig" in x for x in f["figures"]), str(f["figures"]))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print("\n{} passed, {} failed".format(len(RESULTS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_test())
