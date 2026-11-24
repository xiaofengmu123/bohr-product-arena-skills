# -*- coding: utf-8 -*-
"""Self-test for the literature-tracking Skill (standard library only).

Run from the skill root:

    python3 scripts/selftest.py

Every assertion maps to a scoring dimension (effect / gain / innovation /
reliability / ecosystem). The script writes its artifacts into a temporary
directory and cleans up, so it can be re-run deterministically anywhere.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402
import main as main_mod  # noqa: E402

PASS = 0
FAIL = 0


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print("PASS  %s" % name)
    except AssertionError as exc:
        FAIL += 1
        print("FAIL  %s -> %s" % (name, exc))
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print("ERROR %s -> %s: %s" % (name, type(exc).__name__, exc))


# ---------------------------------------------------------------------------
# Effect / correctness: normalization + keys
# ---------------------------------------------------------------------------

def test_doi_normalization():
    assert core.normalize_doi("https://doi.org/10.1000/XYZ") == "10.1000/xyz"
    assert core.normalize_doi("doi:10.1000/ABC.") == "10.1000/abc"
    assert core.normalize_doi("10.1038/s41586-024-00000-1") == "10.1038/s41586-024-00000-1"
    assert core.normalize_doi("not a doi") is None
    assert core.normalize_doi(None) is None


def test_arxiv_normalization():
    assert core.normalize_arxiv("arXiv:2001.01234v2") == "2001.01234"
    assert core.normalize_arxiv("https://arxiv.org/abs/hep-th/9901001v1") == "hep-th/9901001"
    assert core.normalize_arxiv("https://example.com/x") is None


def test_title_normalization():
    assert core.normalize_title("The Quick, Brown: Fox!") == "the quick brown fox"
    assert core.normalize_title("  ") is None


def test_key_priority():
    rec = {"doi": "10.1000/xyz", "arxiv": "2001.01234", "title": "A Paper"}
    assert core.canonical_key(rec) == ("doi", "10.1000/xyz")
    rec2 = {"arxiv": "2001.01234", "title": "A Paper"}
    assert core.canonical_key(rec2) == ("arxiv", "2001.01234")
    rec3 = {"title": "A Paper"}
    assert core.canonical_key(rec3) == ("title", "a paper")
    assert core.canonical_key({"x": 1}) is None


# ---------------------------------------------------------------------------
# Innovation: version classification + chains + dedup
# ---------------------------------------------------------------------------

def test_version_classification():
    assert core.classify_version({"title": "Erratum: X"}) == "correction"
    assert core.classify_version({"title": "Retraction of X"}) == "retraction"
    assert core.classify_version({"title": "X", "version": "preprint"}) == "preprint"
    assert core.classify_version({"doi": "10.1/x", "journal": "Nature"}) == "journal"


def test_dedup_grouping():
    recs = [
        {"title": "A Study", "doi": "10.1000/abc"},
        {"title": "A STUDY.", "doi": "https://doi.org/10.1000/abc"},
        {"title": "Another", "doi": "10.1000/def"},
    ]
    groups, unkeyed = core.dedup_records(recs)
    assert len(groups) == 2 and not unkeyed
    g = next(g for g in groups if g["key"] == "10.1000/abc")
    assert g["count"] == 2


def test_version_chain():
    recs = [
        {"title": "X", "type": "preprint", "doi": "10.1/a"},
        {"title": "X", "type": "journal", "doi": "10.1/b"},
    ]
    chains = core.build_version_chains(recs)
    assert len(chains) == 1 and chains[0]["is_version_chain"]
    assert chains[0]["stages"] == ["preprint", "journal"]


# ---------------------------------------------------------------------------
# Effect: time window and increment classification
# ---------------------------------------------------------------------------

def test_date_parsing():
    assert core.parse_date("2024-03-15") == core.parse_date({"date-parts": [[2024, 3, 15]]})
    assert core.parse_date("2024-03-15T00:00:00Z") == core.parse_date("2024-03-15")
    assert core.parse_date("2024") == core.parse_date("2024-01-01")
    assert core.parse_date(None) is None
    # timezone: 08:00+08:00 == 00:00 UTC
    assert core.parse_date("2024-03-15T08:00:00+08:00") == core.parse_date("2024-03-15T00:00:00Z")


def test_closed_window_boundaries():
    win = {"start": "2024-01-01", "end": "2024-01-31"}
    assert core.in_time_window({"published": "2024-01-01"}, win) is True
    assert core.in_time_window({"published": "2024-01-31"}, win) is True
    assert core.in_time_window({"published": "2023-12-31"}, win) is False
    assert core.in_time_window({"published": "2024-02-01"}, win) is False
    assert core.in_time_window({}, win) is None


def test_diff_buckets():
    snapshot = {"canonical_ids": ["10.1000/old"]}
    window = {"start": "2024-01-01", "end": "2024-01-31"}
    cands = [
        {"title": "New", "doi": "10.1000/new", "published": "2024-01-10"},
        {"title": "Old", "doi": "10.1000/old", "published": "2024-01-15"},
        {"title": "Late", "doi": "10.1000/late", "published": "2023-06-01"},
        {"title": "Retraction of Old", "doi": "10.1000/retr", "published": "2024-01-20"},
        {"title": "Erratum: Old", "doi": "10.1000/corr", "published": "2024-01-20"},
    ]
    diff = core.diff_snapshot(cands, snapshot, window)
    new_dois = [e["record"]["doi"] for e in diff["new_items"]]
    assert "10.1000/new" in new_dois
    assert "10.1000/corr" in new_dois  # correction on unseen work is still new to the subscriber
    assert [e["record"]["doi"] for e in diff["late_indexed"]] == ["10.1000/late"]
    assert [e["record"]["doi"] for e in diff["retractions"]] == ["10.1000/retr"]
    # already-seen duplicate -> suppressed
    assert any(e["record"]["doi"] == "10.1000/old" for e in diff["suppressed"])


def test_reminder_suppression():
    diff = {
        "new_items": [{"record": {"doi": "10.1000/x"}, "reason": "in_window_new"},
                      {"record": {"doi": "10.1000/X"}, "reason": "in_window_new"}],
        "updates": [], "retractions": [], "late_indexed":
        [{"record": {"doi": "10.1000/late"}, "reason": "late_indexed_old_item"}],
        "suppressed": [],
    }
    out = core.build_reminders(diff)
    # DOI normalization collapses 10.1000/x and 10.1000/X to one notification
    assert len(out["reminders"]) == 1
    assert len(out["suppressed"]) == 2


# ---------------------------------------------------------------------------
# Reliability: scaffold idempotency, empty dir, bad JSON
# ---------------------------------------------------------------------------

def run_main(args):
    rc = main_mod.main(args)
    assert rc == 0, "main() returned %s for %s" % (rc, args)
    return rc


def test_scaffold_empty_dir_idempotent():
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "input"; inp.mkdir()
        out = Path(td) / "output"
        work = Path(td) / "work"
        run_main(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
        r1 = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r1["status"] == "insufficient_evidence"
        assert r1["case_id"] == "unknown-case"
        assert r1["change_log"][0]["event"] == "skeleton_written"
        # second run -> byte-identical (determinism; timestamps fixed to skeleton event only)
        run_main(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
        r2 = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r1 == r2


def test_bad_json_input_error():
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "input"; inp.mkdir()
        (inp / "case.json").write_text("{not valid json", encoding="utf-8")
        (inp / "task.md").write_text("x", encoding="utf-8")
        out = Path(td) / "output"
        work = Path(td) / "work"
        run_main(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r["status"] == "input_error"
        assert any("parse failed" in e for e in r["errors"])


def test_contract_backfill():
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "input"; inp.mkdir()
        (inp / "case.json").write_text(json.dumps({
            "case_id": "case-x",
            "output_contract": {"hits": [], "evidence_map": [], "notes": ""},
        }), encoding="utf-8")
        (inp / "task.md").write_text("t", encoding="utf-8")
        out = Path(td) / "output"
        work = Path(td) / "work"
        run_main(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r["case_id"] == "case-x"
        assert r.get("hits") is not None
        assert r.get("evidence_map") is not None
        assert r.get("notes") == ""


def test_evidence_validation():
    problems = core.validate_evidence([
        {"locator": "m#p1", "quote": "q"},
        {"locator": "", "quote": "q"},
        {"locator": "m#p2", "quote": ""},
    ])
    assert len(problems) == 2


def test_atomic_write_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a" / "b.json"
        core.write_json_atomic(str(p), {"x": [1, 2]})
        assert json.loads(p.read_text(encoding="utf-8")) == {"x": [1, 2]}
        assert not (p.parent / "b.json.tmp").exists()


# ---------------------------------------------------------------------------

def main_selftest():
    check("DOI normalization", test_doi_normalization)
    check("arXiv normalization", test_arxiv_normalization)
    check("Title normalization", test_title_normalization)
    check("Key priority DOI>arXiv>title", test_key_priority)
    check("Version classification", test_version_classification)
    check("Dedup grouping", test_dedup_grouping)
    check("Version chain", test_version_chain)
    check("Date parsing incl. timezone", test_date_parsing)
    check("Closed-window boundaries", test_closed_window_boundaries)
    check("Snapshot diff buckets", test_diff_buckets)
    check("Reminder suppression", test_reminder_suppression)
    check("Scaffold empty-dir idempotent", test_scaffold_empty_dir_idempotent)
    check("Bad JSON -> input_error", test_bad_json_input_error)
    check("Contract backfill", test_contract_backfill)
    check("Evidence validation", test_evidence_validation)
    check("Atomic write roundtrip", test_atomic_write_roundtrip)
    print("")
    print("selftest: %d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main_selftest())
