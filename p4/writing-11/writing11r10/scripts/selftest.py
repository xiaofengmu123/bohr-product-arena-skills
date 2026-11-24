#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the bilingual scholarly translator Skill.

Runs the core pipeline on synthetic inputs and asserts invariants:
skeleton validity, segmentation/extraction, number/term/citation fidelity,
segment-alignment anchors, empty-input and bad-JSON degradation. Deterministic;
exits 0 only when all checks pass. Run from anywhere:

    python3 -B scripts/selftest.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402

SAMPLE_SRC = """Machine Learning (ML) improves accuracy from 87.3% to 92.1% \
(p = 0.031), but it does not significantly outperform the baseline in 12 of 20 \
trials (Smith et al., 2020). These results may indicate overfitting, because \
only the training set was used [1,2].

Figure 1. Accuracy comparison across conditions.
"""

SAMPLE_TGT = """<!-- seg:s1-1 -->机器学习（ML）将准确率从 87.3% 提高到 92.1% \
（p = 0.031），但在 20 次试验中的 12 次并未显著优于基线（Smith et al., 2020）。\
<!-- seg:s1-2 -->这些结果可能表明过拟合，因为仅使用了训练集 [1,2]。\
<!-- seg:s1-3 -->图 1.\
<!-- seg:s1-4 -->各条件下的准确率比较。"""


def _t(name, cond, note=""):
    return (name, bool(cond), note)


def run_selftest():
    results = []

    # 1. skeleton validity
    sk = core.make_skeleton("case-x", None)
    for k in ("case_id", "status", "deliverables", "fact_source_map",
              "version_boundary", "sources", "uncertainty", "pending_human",
              "change_log"):
        if k not in sk:
            results.append(_t("skeleton:field:%s" % k, False, "missing key"))
            break
    else:
        results.append(_t("skeleton:fields", sk["status"] == "in_progress"))
    results.append(_t("skeleton:version_boundary_editable",
                      sk["version_boundary"].get("editable") is True))

    # 2. atomic write + reload
    d = tempfile.mkdtemp(prefix="bilang-selftest-")
    p = os.path.join(d, "result.json")
    core.atomic_write_json(p, sk)
    reloaded = core.load_json_graceful(p)
    results.append(_t("io:atomic_reload",
                      reloaded == sk))

    # 3. segmentation + extraction on synthetic source
    segs = core.segment_text(SAMPLE_SRC, "materials/sample.txt", "s1")
    results.append(_t("segment:nonempty", len(segs) >= 3, "n=%d" % len(segs)))
    ledger = core.build_ledger(segs, [{"path": "materials/sample.txt",
                                       "type": ".txt", "role": "material"}])
    results.append(_t("ledger:numbers", len(ledger["numbers"]) >= 3,
                      "n=%d" % len(ledger["numbers"])))
    results.append(_t("ledger:terms", len(ledger["terms"]) >= 1,
                      "n=%d" % len(ledger["terms"])))
    results.append(_t("ledger:citations", len(ledger["citations"]) >= 2,
                      "n=%d" % len(ledger["citations"])))
    results.append(_t("ledger:semantic", len(ledger["semantic_markers"]) >= 1))

    # 4. number fidelity: altered digit must be flagged
    core_digits = {n["core"] for n in ledger["numbers"]}
    results.append(_t("verify:number_present", any(
        cd in SAMPLE_TGT for cd in core_digits if cd),
        "target contains source digits"))
    altered = SAMPLE_TGT.replace("87.3", "87.5")
    report = core.verify(ledger, {"out.md": altered})
    results.append(_t("verify:altered_number_flagged",
                      report["status"] == "fail",
                      "status=%s errors=%d" % (report["status"],
                                               len(report["errors"]))))

    # 5. alignment: anchors present -> pass; missing -> error
    good = core.verify(ledger, {"out.md": SAMPLE_TGT})
    results.append(_t("verify:alignment_pass", good["status"] in ("pass", "review"),
                      "status=%s" % good["status"]))
    no_anchor = SAMPLE_TGT.replace("<!-- seg:s1-1 -->", "")
    report2 = core.verify(ledger, {"out.md": no_anchor})
    results.append(_t("verify:missing_anchor_flagged",
                      any(e["check"] == "alignment" for e in report2["errors"])))

    # 6. term consistency: canonical target present/absent
    for t in ledger["terms"]:
        t["canonical_target"] = "机器学习"
    report3 = core.verify(ledger, {"out.md": SAMPLE_TGT})
    results.append(_t("verify:canonical_term_ok",
                      not any(e["check"] == "terms" for e in report3["errors"])))

    # 7. degradation: empty input dir
    empty = os.path.join(d, "empty")
    os.makedirs(empty, exist_ok=True)
    inv = core.discover_input(empty)
    sk2 = core.make_skeleton(None, None)
    results.append(_t("degrade:empty_input_skeleton",
                      sk2["status"] == "in_progress" and inv["missing"]))
    results.append(_t("degrade:bad_json_no_crash",
                      core.load_case(os.path.join(d, "nope.json")) is not None
                      or core.load_case(os.path.join(d, "nope.json"))
                      is not None))

    # 8. bad JSON case.json
    bad = os.path.join(d, "case.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not json")
    case = core.load_case(bad)
    results.append(_t("degrade:bad_case_json", case.get("case_id") is None
                      and case.get("error")))

    return all(r[1] for r in results), results


if __name__ == "__main__":
    ok, results = run_selftest()
    failed = 0
    for name, passed, note in results:
        print("%-32s %s  %s" % (name, "PASS" if passed else "FAIL", note))
        if not passed:
            failed += 1
    print("\n%d/%d passed" % (len(results) - failed, len(results)))
    sys.exit(0 if ok else 1)
