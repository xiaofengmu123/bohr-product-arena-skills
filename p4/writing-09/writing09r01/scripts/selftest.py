#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the results-and-discussion writing skill.

Runs the robustness battery required by SKILL.md on throwaway temp dirs:
  * empty input dir
  * bad JSON case.json
  * bad-encoding material file
  * happy path (normalize -> skeleton -> patch -> check)
  * evidence-map hard checks (missing locator/quote)
  * overclaim wording detection

No third-party dependencies; exit code 0 only if every assertion passes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as ci  # noqa: E402

PASS = 0
FAIL = 0


def ok(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s" % label)


def write(path, content, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as fh:
        fh.write(content)


def make_case(tmp, case_obj, task_text, extra=None):
    write(os.path.join(tmp, "case.json"), json.dumps(case_obj, ensure_ascii=False))
    write(os.path.join(tmp, "task.md"), task_text)
    if extra:
        for rel, content, enc in extra:
            write(os.path.join(tmp, rel), content, enc)


def test_empty_input():
    print("[1] empty input dir")
    tmp = tempfile.mkdtemp(prefix="rdw_empty_")
    try:
        summary, _ = ci.normalize_input(tmp, os.path.join(tmp, "work"))
        ok(summary.get("case_id") in (None, ""), "empty dir -> case_id None")
        ok(summary.get("output_contract") == ci.DEFAULT_CONTRACT, "default contract applied")
        result, _ = ci.build_skeleton(summary, os.path.join(tmp, "out"))
        ok(os.path.exists(os.path.join(tmp, "out", "result.json")), "result.json still written")
        ok(result.get("status") == "partial", "status=partial on empty input")
        rep = ci.check_result(os.path.join(tmp, "out", "result.json"),
                              os.path.join(tmp, "work", "input_summary.json"))
        ok(not rep.get("ok"), "check flags missing case_id (honest)")
    except Exception:
        traceback.print_exc()
        ok(False, "empty input raised unexpectedly")
    finally:
        pass


def test_bad_json():
    print("[2] bad JSON case.json")
    tmp = tempfile.mkdtemp(prefix="rdw_badjson_")
    try:
        write(os.path.join(tmp, "case.json"), "{not valid json")
        write(os.path.join(tmp, "task.md"), "# 题目\n结果与讨论")
        summary, _ = ci.normalize_input(tmp, os.path.join(tmp, "work"))
        ok(any(e.get("error") == "bad_json" for e in summary["errors"]), "bad JSON recorded")
        ok(summary.get("output_contract") == ci.DEFAULT_CONTRACT, "default contract fallback")
        result, _ = ci.build_skeleton(summary, os.path.join(tmp, "out"))
        ok(result.get("status") == "partial", "skeleton still produced")
    except Exception:
        traceback.print_exc()
        ok(False, "bad JSON raised unexpectedly")
    finally:
        pass


def test_bad_encoding():
    print("[3] bad-encoding material")
    tmp = tempfile.mkdtemp(prefix="rdw_badenc_")
    try:
        write(os.path.join(tmp, "case.json"),
              json.dumps({"case_id": "c-enc", "output_contract": {"deliverable": None}}))
        write(os.path.join(tmp, "task.md"), "# task")
        raw = b"\xff\xfe\x00garbage\x9cbytes"
        with open(os.path.join(tmp, "fig1.csv"), "wb") as fh:
            fh.write(raw)
        summary, _ = ci.normalize_input(tmp, os.path.join(tmp, "work"))
        mat = [m for m in summary["materials"] if os.path.basename(m["path"]) == "fig1.csv"]
        ok(bool(mat), "material discovered")
        ok(mat[0].get("kind") == "text" and mat[0].get("preview") != "", "bad bytes read with replace")
    except Exception:
        traceback.print_exc()
        ok(False, "bad encoding raised unexpectedly")
    finally:
        pass


def _evidence_entry(**kw):
    base = {"claim_id": "R1", "claim": "treated 78% vs control 52%",
            "claim_type": "result_comparison", "locator": "materials/fig2_stat.md#L3",
            "quote": "treated 78 +/- 5 %, control 52 +/- 6 %, p=0.012",
            "confidence": "direct", "status": "verified",
            "value": {"number": "78", "unit": "%", "stat": "mean±SD", "p": "0.012"}}
    base.update(kw)
    return base


def test_happy_path():
    print("[4] happy path: normalize -> skeleton -> patch -> check")
    tmp = tempfile.mkdtemp(prefix="rdw_happy_")
    try:
        make_case(tmp,
                  {"case_id": "c-happy",
                   "output_contract": {"deliverable": None, "facts_sources": [], "version_boundaries": {}}},
                  "# 题目\n结果与讨论章节写作",
                  [("materials/fig2_stat.md", "treated 78 +/- 5 %, control 52 +/- 6 %, p=0.012", "utf-8")])
        summary, spath = ci.normalize_input(tmp, os.path.join(tmp, "work"))
        ok(summary["case_id"] == "c-happy", "case_id parsed")
        result, rpath = ci.build_skeleton(summary, os.path.join(tmp, "out"))
        ok("deliverable" in result and "facts_sources" in result and "version_boundaries" in result,
           "contract fields present in skeleton")

        patch = {
            "status": "partial",
            "deliverable": {"file": "results_discussion.md", "section_results": "78% vs 52% (p=0.012)."},
            "facts_sources": [{"claim": "78 vs 52", "locator": "materials/fig2_stat.md#L3",
                               "quote": "treated 78 +/- 5 %, control 52 +/- 6 %"}],
            "evidence_map": [_evidence_entry()],
            "change_log": [{"time": ci.now_iso(), "action": "filled_draft", "detail": "draft"}],
        }
        write(os.path.join(tmp, "patch.json"), json.dumps(patch, ensure_ascii=False))
        result, err = ci.apply_patch(rpath, os.path.join(tmp, "patch.json"))
        ok(err is None, "patch applied")
        ok(result["deliverable"]["file"] == "results_discussion.md", "patch merged into deliverable")
        rep = ci.check_result(rpath, spath)
        ok(rep.get("ok"), "happy path passes check (no hard errors)")
        ok(rep.get("warnings", 0) == 0, "no warnings on happy path")
    except Exception:
        traceback.print_exc()
        ok(False, "happy path raised unexpectedly")
    finally:
        pass


def test_evidence_checks():
    print("[5] evidence-map hard checks")
    tmp = tempfile.mkdtemp(prefix="rdw_ev_")
    try:
        make_case(tmp, {"case_id": "c-ev",
                        "output_contract": {"deliverable": None, "facts_sources": [], "version_boundaries": {}}},
                  "# task", [("materials/t1.md", "some result text", "utf-8")])
        summary, spath = ci.normalize_input(tmp, os.path.join(tmp, "work"))
        result, rpath = ci.build_skeleton(summary, os.path.join(tmp, "out"))

        bad_ev = _evidence_entry(quote="")  # missing verbatim quote
        patch = {"evidence_map": [bad_ev], "status": "partial"}
        write(os.path.join(tmp, "patch.json"), json.dumps(patch, ensure_ascii=False))
        ci.apply_patch(rpath, os.path.join(tmp, "patch.json"))
        rep = ci.check_result(rpath, spath)
        ok(not rep.get("ok"), "missing quote -> hard error")
        ok(any(c["code"] == "evidence_invalid" for c in rep["checks"]), "evidence_invalid reported")

        causal = _evidence_entry(claim_id="D1", claim_type="causal_claim",
                                 locator="materials/ref1.md#L2", quote="mechanism X",
                                 strength="speculative")  # missing causal_boundary + basis
        patch = {"evidence_map": [causal]}
        write(os.path.join(tmp, "patch.json"), json.dumps(patch, ensure_ascii=False))
        ci.apply_patch(rpath, os.path.join(tmp, "patch.json"))
        rep = ci.check_result(rpath, spath)
        ok(not rep.get("ok"), "causal claim without boundary/basis -> hard error")
    except Exception:
        traceback.print_exc()
        ok(False, "evidence checks raised unexpectedly")
    finally:
        pass


def test_overclaim_scan():
    print("[6] overclaim wording scan")
    hits = ci.scan_forbidden("该方案首次填补空白，具有革命性意义，unprecedented breakthrough.")
    ok("首次" in hits and "填补空白" in hits and "革命性" in hits, "Chinese overclaims detected")
    ok("unprecedented" in hits and "breakthrough" in hits, "English overclaims detected")
    ok(ci.scan_forbidden("p=0.012, 78% vs 52%, CI 95%") == [], "neutral text has no overclaims")


def main():
    print("results-and-discussion skill self-test")
    test_empty_input()
    test_bad_json()
    test_bad_encoding()
    test_happy_path()
    test_evidence_checks()
    test_overclaim_scan()
    print("----\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
