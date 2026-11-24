#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the results-discussion-writing skill package.

Runs with only the standard library. Creates its own temporary demo inputs,
exercises the build pipeline, and asserts the contract invariants:

  * empty input dir  -> status in_progress, exit 0, no crash
  * missing contract -> default contract fields present
  * bad JSON         -> recorded as an error, still writes a legal skeleton
  * valid demo       -> result.json has case_id + every output_contract field
                        + the guaranteed extras + an append-only change_log
  * over-claim scan  -> catches 首次/颠覆性 etc.
  * validate         -> rejects a result with a missing contract field

Exit code 0 means all assertions passed. This is an honest engineering check,
not a benchmark score.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s%s" % (status, name, (" — " + detail) if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def run_build(case_obj, input_dir, work, output):
    """Drive cmd_build through imported core (no subprocess path issues)."""
    case_path = os.path.join(input_dir, "case.json")
    core.write_json(case_path, case_obj)
    started = core.now_iso()
    contract_keys, contract_def, notes = core.extract_contract(case_obj)
    inputs = core.scan_inputs(input_dir)
    skeleton = core.build_skeleton(case_obj, contract_keys, started_at=started)
    summary = core.summarize_case(case_obj, None, inputs, contract_keys,
                                  contract_def, notes)
    core.write_json(os.path.join(work, "input_summary.json"), summary)
    core.write_json(os.path.join(work, "contract.json"),
                    {"fields": contract_keys, "definition": contract_def,
                     "notes": notes, "guaranteed_extras": core.EXTRAS})
    out_result = os.path.join(output, "result.json")
    core.write_json(out_result, skeleton)
    errors, warnings = core.validate_result(skeleton, contract_keys)
    return skeleton, contract_keys, errors, warnings


def main():
    root = tempfile.mkdtemp(prefix="rdw-selftest-")
    try:
        # --- empty input directory -------------------------------------
        empty_in = os.path.join(root, "empty")
        os.makedirs(empty_in)
        empty_work = os.path.join(root, "empty-work")
        empty_out = os.path.join(root, "empty-out")
        sk, keys, errs, warns = run_build(
            {"case_id": "empty-case"}, empty_in, empty_work, empty_out)
        check("empty-input no crash", True)
        check("empty-input status", sk.get("status") == "in_progress",
              "status=%r" % sk.get("status"))
        check("empty-input default contract", "deliverable" in sk and "facts_sources" in sk,
              "keys=%r" % keys)

        # --- bad JSON still yields a legal skeleton --------------------
        bad_in = os.path.join(root, "bad")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "broken.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        bad_work = os.path.join(root, "bad-work")
        bad_out = os.path.join(root, "bad-out")
        sk2, keys2, errs2, warns2 = run_build(
            {"case_id": "bad-case", "output_contract": ["deliverable", "facts_sources"]},
            bad_in, bad_work, bad_out)
        bad_case_path = os.path.join(root, "bad-case.json")
        with open(bad_case_path, "w", encoding="utf-8") as fh:
            fh.write("{broken json")
        _bad_obj, bad_case_errs = core.load_json_robust(bad_case_path)
        check("bad case.json recorded", any("json" in e for e in bad_case_errs),
              "errors=%r" % bad_case_errs)
        check("bad-input legal skeleton", "case_id" in sk2 and "deliverable" in sk2, "")

        # --- valid demo with an explicit contract ----------------------
        demo_in = os.path.join(root, "demo-in")
        os.makedirs(demo_in)
        with open(os.path.join(demo_in, "fig2_table.txt"), "w", encoding="utf-8") as fh:
            fh.write("Figure 2B: viability 62 ± 5% (n=3), p=0.031 vs control\n")
        demo_work = os.path.join(root, "demo-work")
        demo_out = os.path.join(root, "demo-out")
        case = {
            "case_id": "demo-rdw-1",
            "output_contract": {
                "deliverable": {"file": "manuscript.md"},
                "facts_sources": "list",
                "version_boundaries": "object",
            },
        }
        sk3, keys3, errs3, warns3 = run_build(case, demo_in, demo_work, demo_out)
        check("contract fields verbatim", all(k in sk3 for k in
              ["deliverable", "facts_sources", "version_boundaries"]),
              "keys=%r" % keys3)
        check("guaranteed extras present", all(k in sk3 for k in core.EXTRAS),
              "extras=%r" % [k for k in core.EXTRAS if k not in sk3])
        check("case_id copied", sk3["case_id"] == "demo-rdw-1", sk3["case_id"])
        check("change_log append-only list", isinstance(sk3["change_log"], list)
              and len(sk3["change_log"]) >= 1 and sk3["change_log"][0]["seq"] == 0, "")
        check("skeleton validates", len(errs3) == 0, "errors=%r" % errs3)

        # --- change_log appends and keeps numbering --------------------
        core.append_change(sk3, "fill", "deliverable", "draft results section",
                           before=None, after="draft v1")
        core.append_change(sk3, "verify", "facts_sources", "attach locator+quote")
        core.write_json(os.path.join(demo_out, "result.json"), sk3)
        check("change_log grew", len(sk3["change_log"]) == 3,
              "len=%d" % len(sk3["change_log"]))
        check("change_log numbered", [e["seq"] for e in sk3["change_log"]] == [0, 1, 2], "")

        # --- over-claim scan -------------------------------------------
        hits = core.forbid_scan("本结果为国际首次、填补空白、颠覆性发现")
        check("forbid-scan catches zh", any(h["token"] == "国际首次" for h in hits)
              and any(h["token"] == "填补空白" for h in hits), "hits=%r" % hits)
        hits_en = core.forbid_scan("an unprecedented result")
        check("forbid-scan catches en", any(h["token"] == "unprecedented" for h in hits_en), "")

        # --- validate rejects a broken result --------------------------
        broken = dict(sk3)
        broken.pop("deliverable", None)
        e4, w4 = core.validate_result(broken, keys3)
        check("validate catches missing field", any("deliverable" in x for x in e4),
              "errors=%r" % e4)

        # --- sigfig helper ---------------------------------------------
        check("sigfig 62.0 -> 3", core.sigfig_digits("62.0") == 3,
              str(core.sigfig_digits("62.0")))
        check("sigfig 0.050 -> 2", core.sigfig_digits("0.050") == 2,
              str(core.sigfig_digits("0.050")))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("----")
    if FAILURES:
        print("SELFTEST FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("SELFTEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
