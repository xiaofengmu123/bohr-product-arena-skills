# -*- coding: utf-8 -*-
"""Self-test for the literature-tracking Skill (stdlib only, no network).

Runs deterministic checks in a temporary directory:
  1. empty input dir        -> insufficient_evidence, exit 0, valid JSON
  2. bad JSON case.json     -> input_error recorded, no crash
  3. missing parameters     -> /input /app/output /app/work defaults accepted
  4. dedup determinism      -> same records twice -> identical output
  5. evidence_map validation-> missing locator/quote flagged
  6. canonical-id precedence-> DOI > arXiv > title

Usage: python3 scripts/selftest.py
Exit code 0 when all checks pass.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402


def check(name, condition):
    print("%s  %s" % ("PASS" if condition else "FAIL", name))
    return bool(condition)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # 1. empty input
        empty_in = base / "empty_in"
        empty_in.mkdir()
        empty_out = base / "empty_out"
        core.ensure_dir(empty_out)
        cid, contract, data, errors = core.extract_case(str(empty_in))
        status = core.initial_status(data, errors)
        result = core.make_skeleton(cid, contract, status)
        core.write_result(str(empty_out), result)
        ok &= check("empty input -> insufficient_evidence",
                    status == "insufficient_evidence" and result["case_id"] == "unknown-case")

        # 2. bad JSON
        bad_in = base / "bad_in"
        bad_in.mkdir()
        (bad_in / "case.json").write_text("{ not json", encoding="utf-8")
        (bad_in / "task.md").write_text("# task", encoding="utf-8")
        cid2, contract2, data2, errors2 = core.extract_case(str(bad_in))
        status2 = core.initial_status(data2, errors2)
        ok &= check("bad JSON -> input_error",
                    status2 == "input_error" and any("parse failed" in e for e in errors2))

        # 3. defaults
        ok &= check("default paths present",
                    core.DEFAULT_INPUT == "/input" and core.DEFAULT_OUTPUT == "/app/output"
                    and core.DEFAULT_WORK == "/app/work")

        # 4. dedup determinism
        recs = [
            {"record_id": "r1", "doi": "10.1000/XYZ", "title": "A Paper",
             "published_date": "2024-01-01", "type": "published", "journal": "J"},
            {"record_id": "r2", "doi": "https://doi.org/10.1000/xyz", "title": "a paper",
             "published_date": "2024-01-01", "type": "published", "journal": "J"},
            {"record_id": "r3", "arxiv": "arXiv:2101.00001v2", "title": "B Paper",
             "type": "preprint"},
            {"record_id": "r4", "arxiv": "2101.00001", "title": "B Paper v2",
             "type": "preprint"},
        ]
        d1 = core.dedup_records(recs)
        d2 = core.dedup_records(recs)
        s1 = core.dump_json(None, d1)
        s2 = core.dump_json(None, d2)
        ok &= check("dedup determinism (same input twice)", s1 == s2)
        ok &= check("dedup groups=2 duplicates=2",
                    d1["canonical_id_count"] == 2 and d1["duplicate_count"] == 2)

        # 5. evidence_map validation
        em = {
            "c1": {"claim": "X", "locator": "m/x.json#/0", "quote": "verbatim"},
            "c2": {"claim": "Y", "locator": "", "quote": "text"},
            "c3": {"claim": "Z", "locator": "m/z.json", "quote": None},
        }
        issues = core.validate_evidence_map(em)
        ok &= check("evidence_map flags missing locator/quote", len(issues) == 2)

        # 6. canonical precedence
        r = {"doi": "10.1/ABC", "arxiv": "1234.5678", "title": "T"}
        ok &= check("canonical DOI precedence", core.canonical_id(r) == "doi:10.1/abc")

        # 7. quote_hash check
        q = "verbatim quote"
        em2 = {"h": {"claim": "H", "locator": "m", "quote": q,
                     "quote_hash": "deadbeef"}}
        ok &= check("quote_hash mismatch flagged", len(core.validate_evidence_map(em2)) == 1)

    print("selftest:", "ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
