# -*- coding: utf-8 -*-
"""Self-test for the literature-delta Skill.

Runs the CLI against synthesized inputs (empty dir, bad JSON, demo case with
duplicate records) and verifies the result contract, idempotency and
degradation handling. Uses only the standard library; no hidden test answers
are shipped, and the demo records below are explicitly synthetic.

Run from the skill root:

    python3 scripts/selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "scripts" / "main.py"


def run(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", str(MAIN), *args],
        capture_output=True, text=True, cwd=str(cwd or ROOT),
    )


def _synthetic_records():
    return [
        {"title": "A New Therapy for X", "doi": "10.1000/XYZ",
         "authors": ["A. One"], "journal": "J. Med.", "published_date": "2026-01-15",
         "version": "journal", "source": {"file": "materials/a.json", "locator": "p1",
                                           "quote": "first report"}},
        {"title": "A New Therapy for X", "doi": "10.1000/xyz",
         "authors": ["A. One"], "version": "preprint",
         "source": {"file": "materials/b.json", "locator": "p2",
                    "quote": "preprint version"}},
        {"title": "Unrelated Study on Y", "arxiv": "2501.00001",
         "version": "preprint",
         "source": {"file": "materials/c.json", "locator": "p3", "quote": "y study"}},
    ]


def make_demo_input(d):
    (d / "case.json").write_text(json.dumps({
        "case_id": "demo-case-001",
        "output_contract": {"search_results": "list", "evidence": "list",
                            "status_notes": "object"},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "task.md").write_text(
        "# demo\nchallenge_id: `challenge-demo`\n", encoding="utf-8")
    mats = d / "materials"
    mats.mkdir(exist_ok=True)
    (mats / "records.json").write_text(
        json.dumps(_synthetic_records(), ensure_ascii=False), encoding="utf-8")


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  -> " + str(detail)) if detail else ""))
    return ok


def main():
    results = []
    tmp = Path(tempfile.mkdtemp(prefix="litdelta_selftest_"))

    # 1. --help exits 0
    r = run("--help")
    results.append(check("help exit 0", r.returncode == 0, r.returncode))

    # 2. empty input dir -> insufficient_evidence, valid result.json
    empty_in = tmp / "empty_in"
    empty_in.mkdir()
    empty_out = tmp / "empty_out"
    empty_work = tmp / "empty_work"
    r = run("run", "--input", str(empty_in), "--output", str(empty_out),
            "--work", str(empty_work))
    rj = json.loads((empty_out / "result.json").read_text(encoding="utf-8"))
    results.append(check("empty input status", rj.get("status") == "insufficient_evidence",
                         rj.get("status")))
    results.append(check("empty input exit 0", r.returncode == 0, r.returncode))

    # 3. bad JSON -> input_error, does not crash
    bad_in = tmp / "bad_in"
    bad_in.mkdir()
    (bad_in / "case.json").write_text("{ not json ", encoding="utf-8")
    (bad_in / "task.md").write_text("x", encoding="utf-8")
    bad_out = tmp / "bad_out"
    bad_work = tmp / "bad_work"
    r = run("run", "--input", str(bad_in), "--output", str(bad_out), "--work", str(bad_work))
    rj = json.loads((bad_out / "result.json").read_text(encoding="utf-8"))
    results.append(check("bad JSON status", rj.get("status") == "input_error", rj.get("status")))
    results.append(check("bad JSON exit 0", r.returncode == 0, r.returncode))

    # 4. normalize + dedup on synthetic records
    r = run("normalize", "--record", json.dumps({"doi": "10.1000/XYZ", "title": "A Paper"}))
    norm = json.loads(r.stdout)
    results.append(check("normalize doi", norm["canonical_id"]["type"] == "doi"
                         and norm["canonical_id"]["value"] == "10.1000/xyz",
                         norm["canonical_id"]))

    recs = tmp / "records.json"
    recs.write_text(json.dumps(_synthetic_records(), ensure_ascii=False), encoding="utf-8")
    dd = tmp / "dedup.json"
    r = run("dedup", "--records", str(recs), "--out", str(dd))
    dj = json.loads(dd.read_text(encoding="utf-8"))
    results.append(check("dedup suppresses duplicate DOI",
                         dj["input_count"] == 3 and dj["merged_count"] == 2,
                         "%d -> %d" % (dj["input_count"], dj["merged_count"])))

    # 5. finalize with findings (records + snapshot) -> new/updated detected
    demo_in = tmp / "demo_in"
    demo_in.mkdir()
    make_demo_input(demo_in)
    demo_out = tmp / "demo_out"
    demo_work = tmp / "demo_work"
    demo_work.mkdir()
    findings = {
        "status": "ok",
        "subscription": {"query": "synthetic demo", "sources": ["local"]},
        "window": {"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T00:00:00Z", "tz": "UTC"},
        "snapshot": {"previous_ids": [], "captured_at": "2026-01-01T00:00:00Z"},
        "records": _synthetic_records(),
        "search_results": _synthetic_records(),
        "ranking": [],
        "evidence": [{"locator": "materials/a.json#p1", "quote": "first report"}],
        "uncertainty": [],
        "pending_human": [],
    }
    (demo_work / "findings.json").write_text(
        json.dumps(findings, ensure_ascii=False), encoding="utf-8")
    r = run("finalize", "--input", str(demo_in), "--output", str(demo_out),
            "--work", str(demo_work))
    rj = json.loads((demo_out / "result.json").read_text(encoding="utf-8"))
    results.append(check("finalize exit 0", r.returncode == 0, r.returncode))
    results.append(check("finalize status ok", rj.get("status") == "ok", rj.get("status")))
    results.append(check("contract fields present",
                         all(k in rj for k in ("search_results", "evidence", "status_notes")),
                         list(rj.keys())))
    results.append(check("tracking new_items",
                         len(rj["tracking"]["new_items"]) == 2,
                         str(rj["tracking"]["new_items"])))
    results.append(check("dedup merged_count",
                         rj["dedup"]["merged_count"] == 2,
                         rj["dedup"]["merged_count"]))
    results.append(check("report.md exists", (demo_out / "report.md").exists()))

    # 6. idempotency: run finalize twice, domain content identical
    def canonical(obj):
        """Strip honest-but-time-varying metadata for a domain-level compare."""
        o = json.loads(json.dumps(obj))
        for k in ("runtime", "change_log"):
            o.pop(k, None)
        return json.dumps(o, sort_keys=True, ensure_ascii=False)

    first = canonical(json.loads((demo_out / "result.json").read_text(encoding="utf-8")))
    run("finalize", "--input", str(demo_in), "--output", str(demo_out),
        "--work", str(demo_work))
    second = canonical(json.loads((demo_out / "result.json").read_text(encoding="utf-8")))
    results.append(check("idempotent domain content", first == second))

    passed = sum(1 for x in results if x)
    print("\n%d/%d checks passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
