# -*- coding: utf-8 -*-
"""Self-test for the literature-tracking Skill (standard library only).

Verifies the deterministic contract without any fabricated literature:

    python3 scripts/selftest.py [--input /input --output /app/output --work /app/work]

Exit code 0 when every check passes; nonzero otherwise. The check cases are
synthetic micro-fixtures (empty dir, bad JSON, tiny records), not formal answers.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as pl
import main as app

CHECKS = []


def check(name, fn):
    def run():
        fn()
        return "ok"
    CHECKS.append((name, run))


def _tmpdir():
    return Path(tempfile.mkdtemp(prefix="lt-selftest-"))


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ns(**kw):
    kw.setdefault("input", DEFAULT_INPUT)
    kw.setdefault("output", DEFAULT_OUTPUT)
    kw.setdefault("work", DEFAULT_WORK)
    return argparse.Namespace(**kw)


DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def test_scaffold_empty():
    base = _tmpdir()
    app.cmd_scaffold(_ns(input=str(base / "in"), output=str(base / "out"),
                         work=str(base / "work")))
    result = json.loads((base / "out" / "result.json").read_text(encoding="utf-8"))
    assert result.get("case_id") == "unknown-case"
    assert result.get("status") in ("insufficient_evidence", "input_error")
    assert isinstance(result.get("change_log"), list) and result["change_log"][0]["event"] == "skeleton_written"


def test_scaffold_bad_json():
    base = _tmpdir()
    _write(base / "in" / "case.json", "{not valid json")
    app.cmd_scaffold(_ns(input=str(base / "in"), output=str(base / "out"),
                         work=str(base / "work")))
    result = json.loads((base / "out" / "result.json").read_text(encoding="utf-8"))
    assert result.get("status") == "input_error"
    assert any("json parse failed" in e for e in result.get("errors", []))


def test_contract_backfill():
    base = _tmpdir()
    _write(base / "in" / "case.json", json.dumps({
        "case_id": "case-1", "output_contract": {"answer": [], "notes": ""}}))
    app.cmd_scaffold(_ns(input=str(base / "in"), output=str(base / "out"),
                         work=str(base / "work")))
    result = json.loads((base / "out" / "result.json").read_text(encoding="utf-8"))
    assert result["case_id"] == "case-1"
    assert "answer" in result and "notes" in result
    assert all(k in result for k in
               ("status", "sources", "uncertainty", "change_log", "pending_human"))


def test_normalize():
    assert pl.normalize_doi("https://doi.org/10.1000/XYZ") == "10.1000/xyz"
    assert pl.normalize_arxiv("arXiv:2103.12345v2") == "2103.12345"
    assert pl.normalize_title("A Paper: Sub!") == "a paper sub"
    assert pl.canonical_id({"doi": "10.1/X"}) == "doi:10.1/x"


def test_dedup_merge():
    recs = [
        {"title": "A Study", "doi": "10.1000/XYZ"},
        {"title": "A Study", "arxiv": "2103.12345"},
        {"title": "A STUDY!", "doi": "10.1000/xyz"},
    ]
    merged = pl.dedup(recs)
    # first two share title -> one group; third shares DOI -> same group via title of first
    assert len(merged) == 1
    assert merged[0]["version_marks"]


def test_dedup_deterministic():
    recs = [{"title": "B", "doi": "10.2/x"}, {"title": "B", "doi": "10.2/X"}]
    assert json.dumps(pl.dedup(recs), sort_keys=True) == \
        json.dumps(pl.dedup(recs), sort_keys=True)


def test_finalize_roundtrip():
    base = _tmpdir()
    _write(base / "in" / "case.json", json.dumps({"case_id": "case-1"}))
    _write(base / "in" / "task.md", "# task\n")
    findings = {
        "status": "ok",
        "subscription": {"query": "test", "sources": ["local"], "frequency": "daily"},
        "window": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z"},
        "snapshot": {"seen": {}},
        "records": [{"title": "New Paper", "doi": "10.3/new", "date": "2024-01-01"}],
        "evidence": [{"locator": "materials/x.md#L2", "quote": "New Paper"}],
    }
    _write(base / "work" / "findings.json", json.dumps(findings))
    app.cmd_finalize(_ns(input=str(base / "in"), output=str(base / "out"),
                         work=str(base / "work"), findings=str(base / "work" / "findings.json")))
    result = json.loads((base / "out" / "result.json").read_text(encoding="utf-8"))
    assert result["case_id"] == "case-1"
    assert result["status"] == "ok"
    assert len(result["tracking"]["new_items"]) == 1
    assert (base / "out" / "report.md").exists()
    assert result["tracking"]["new_items"][0]["canonical_id"] == "doi:10.3/new"


check("scaffold on empty dir", test_scaffold_empty)
check("scaffold on bad JSON", test_scaffold_bad_json)
check("output_contract backfill", test_contract_backfill)
check("normalize keys", test_normalize)
check("dedup merges variants", test_dedup_merge)
check("dedup deterministic", test_dedup_deterministic)
check("finalize roundtrip", test_finalize_roundtrip)


def main(argv=None):
    failed = 0
    for name, fn in CHECKS:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
    print("selftest %d/%d passed" % (len(CHECKS) - failed, len(CHECKS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
