# -*- coding: utf-8 -*-
"""Self-test for the literature-tracking skill.

Standard library only. Exercises the failure and idempotency scenarios the
scoring rubric rewards: empty input, malformed JSON, normal scaffold/finalize,
dedup determinism, and byte-identical re-runs.

Usage::

    python3 -B scripts/selftest.py

Exit code 0 when every scenario passes; 1 otherwise.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402
import main as main_mod  # noqa: E402


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _run(scenarios):
    passed = 0
    for name, fn in scenarios:
        try:
            fn()
            print("PASS  %s" % name)
            passed += 1
        except Exception as exc:
            print("FAIL  %s\n      %s" % (name, exc))
            traceback.print_exc()
    print("\n%d/%d scenarios passed" % (passed, len(scenarios)))
    return 0 if passed == len(scenarios) else 1


def main():
    scenarios = []

    # 1. Empty input dir -> insufficient_evidence, no crash.
    def t_empty():
        with tempfile.TemporaryDirectory() as d:
            inp = os.path.join(d, "input")
            out = os.path.join(d, "output")
            work = os.path.join(d, "work")
            os.makedirs(inp)
            rc = main_mod.main(["scaffold", "--input", inp,
                                "--output", out, "--work", work])
            assert rc == 0, "scaffold exit %s" % rc
            res = json.load(open(os.path.join(out, "result.json"),
                                 encoding="utf-8"))
            assert res["status"] == "insufficient_evidence", res["status"]
            assert res["case_id"] == "unknown-case"
    scenarios.append(("empty_input", t_empty))

    # 2. Malformed JSON -> input_error recorded, no crash.
    def t_bad_json():
        with tempfile.TemporaryDirectory() as d:
            inp = os.path.join(d, "input")
            out = os.path.join(d, "output")
            work = os.path.join(d, "work")
            os.makedirs(inp)
            with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
                fh.write("{not valid json")
            rc = main_mod.main(["scaffold", "--input", inp,
                                "--output", out, "--work", work])
            assert rc == 0
            res = json.load(open(os.path.join(out, "result.json"),
                                 encoding="utf-8"))
            assert res["status"] == "input_error", res["status"]
            assert any("parse failed" in e for e in res["errors"])
    scenarios.append(("bad_json", t_bad_json))

    # 3. Normal scaffold + finalize with findings: contract backfill complete.
    def t_normal():
        with tempfile.TemporaryDirectory() as d:
            inp = os.path.join(d, "input")
            out = os.path.join(d, "output")
            work = os.path.join(d, "work")
            os.makedirs(inp)
            os.makedirs(work)
            case = {
                "case_id": "case-test-1",
                "output_contract": {
                    "search_results": [],
                    "evidence": [],
                    "status_notes": {},
                },
            }
            with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
                json.dump(case, fh)
            with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
                fh.write("# test\n")
            rc = main_mod.main(["scaffold", "--input", inp,
                                "--output", out, "--work", work])
            assert rc == 0
            res = json.load(open(os.path.join(out, "result.json"),
                                 encoding="utf-8"))
            for k in ("search_results", "evidence", "status_notes"):
                assert k in res, "missing contract field %s" % k
            assert res["case_id"] == "case-test-1"
            assert res["status"] == "partial"
            # findings + finalize
            findings = {
                "status": "ok",
                "search_results": [
                    {"doi": "10.1000/XYZ", "title": "A Paper"},
                    {"doi": "10.1000/XYZ", "title": "a-paper", "type": "published"},
                ],
                "evidence": [{"claim": "x", "locator": "m.md#1", "quote": "x"}],
                "status_notes": {"empty": None, "conflicts": [], "permission_errors": []},
                "tracking": {"subscription": "q", "window": "w", "snapshot": {}},
            }
            fp = os.path.join(work, "findings.json")
            with open(fp, "w", encoding="utf-8") as fh:
                json.dump(findings, fh)
            rc = main_mod.main(["finalize", "--input", inp, "--output", out,
                                "--work", work, "--findings", fp])
            assert rc == 0
            res2 = json.load(open(os.path.join(out, "result.json"),
                                  encoding="utf-8"))
            assert res2["status"] == "ok"
            assert "dedup" in res2 and "tracking" in res2
            # Two records sharing a DOI must collapse into one group.
            assert res2["dedup"]["group_count"] == 1, res2["dedup"]["group_count"]
            assert os.path.isfile(os.path.join(out, "report.md"))
    scenarios.append(("normal_finalize", t_normal))

    # 4. Dedup determinism + DOI/arXiv/title normalization.
    def t_dedup_norm():
        recs = [
            {"doi": "https://doi.org/10.1000/ABC", "title": "One"},
            {"doi": "10.1000/abc", "title": "One again"},
            {"arxiv": "arXiv:2301.12345v2", "title": "Two"},
            {"arxiv": "2301.12345", "title": "Two b"},
            {"title": "A Paper: With, Punctuation!", "source": "x"},
            {"title": "a paper with punctuation", "source": "y"},
        ]
        groups = core.dedup_records(recs)
        assert len(groups) == 3, "expected 3 groups, got %d" % len(groups)
        ids = sorted(g["canonical_id"] for g in groups)
        assert any(i.startswith("doi:10.1000/abc") for i in ids)
        assert any(i.startswith("arxiv:2301.12345") for i in ids)
        assert any(i.startswith("title:sha1:") for i in ids)
        # Determinism: identical input -> identical JSON.
        g1 = json.dumps(core.dedup_records(recs), sort_keys=True)
        g2 = json.dumps(core.dedup_records(recs), sort_keys=True)
        assert g1 == g2
    scenarios.append(("dedup_normalization", t_dedup_norm))

    # 5. Idempotency: two finalize runs produce byte-identical result.json.
    def t_idempotent():
        with tempfile.TemporaryDirectory() as d:
            inp = os.path.join(d, "input")
            out = os.path.join(d, "output")
            work = os.path.join(d, "work")
            os.makedirs(inp)
            os.makedirs(work)
            case = {"case_id": "idem-1",
                    "output_contract": {"search_results": [], "evidence": []}}
            with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
                json.dump(case, fh)
            findings = {"status": "ok",
                        "search_results": [{"doi": "10.1/2", "title": "T"}]}
            fp = os.path.join(work, "findings.json")
            with open(fp, "w", encoding="utf-8") as fh:
                json.dump(findings, fh)
            main_mod.main(["finalize", "--input", inp, "--output", out,
                           "--work", work, "--findings", fp])
            h1 = _sha(os.path.join(out, "result.json"))
            main_mod.main(["finalize", "--input", inp, "--output", out,
                           "--work", work, "--findings", fp])
            h2 = _sha(os.path.join(out, "result.json"))
            assert h1 == h2, "re-run changed result.json"
    scenarios.append(("idempotent_rerun", t_idempotent))

    # 6. Version-chain classification + snapshot diff.
    def t_version_snapshot():
        recs = [
            {"arxiv": "2301.1", "type": "preprint", "title": "V", "date": "2023-01-01"},
            {"arxiv": "2301.1", "type": "published", "title": "V", "date": "2023-06-01"},
            {"doi": "10.1000/9", "type": "retraction", "title": "R"},
        ]
        groups = core.dedup_records(recs)
        snap = {"arxiv:2301.1": core.version_marker(recs[0])}
        new_items, updates, _unch, missing = core.snapshot_diff(groups, snap)
        assert len(new_items) == 1 and new_items[0]["key_kind"] == "doi"
        assert len(updates) == 1 and updates[0]["canonical_id"] == "arxiv:2301.1"
        rem, sup = core.build_reminders(new_items, updates, groups)
        kinds = {r["kind"] for r in rem}
        assert "retraction" in kinds, kinds
        assert "version_update" in kinds, kinds
    scenarios.append(("version_snapshot_diff", t_version_snapshot))

    return _run(scenarios)


if __name__ == "__main__":
    sys.exit(main())
