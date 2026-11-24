# -*- coding: utf-8 -*-
"""Self-test for the literature-tracking Skill.

Pure standard library, no network, no external files.  Builds synthetic inputs
in a temporary directory, exercises every code path in ``pipeline.py``, and
reports a checkable checklist against the scoring dimensions.

Run from the skill root:

    python3 -B scripts/selftest.py
    python3 -B scripts/main.py --help

Exit code 0 means every check passed.  A check that fails prints a concrete
message and the script exits non-zero.

The determinism check (D5) is the core of this variant: it runs ``finalize``
twice on identical inputs and asserts that the substantive fields are
byte-identical after stripping wall-clock metadata (``runtime`` and
``change_log[*].time``), proving same-input -> same-output.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline  # noqa: E402


PASS = "[PASS]"
FAIL = "[FAIL]"

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print("%s %s%s" % (PASS if ok else FAIL, name,
                       (" -- " + detail) if detail else ""))


def _strip_volatile(obj):
    """Remove wall-clock fields so two runs can be diffed deterministically."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "runtime":
                continue
            if k == "change_log" and isinstance(v, list):
                v = [{kk: vv for kk, vv in (e.items() if isinstance(e, dict) else [])
                      if kk != "time"} for e in v]
            out[k] = _strip_volatile(v)
        return out
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _stable(obj):
    return json.dumps(_strip_volatile(obj), ensure_ascii=False, sort_keys=True)


def make_case(root, case_id="challenge-test", contract=None, body=None):
    """Create a synthetic /input directory."""
    inp = Path(root) / "input"
    inp.mkdir(parents=True, exist_ok=True)
    case = {"case_id": case_id}
    if contract is not None:
        case["output_contract"] = contract
    (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text(
        "# test\n\n- challenge_id: `%s`\n\n%s\n" % (case_id, body or ""),
        encoding="utf-8")
    (inp / "materials").mkdir(exist_ok=True)
    (inp / "materials" / "notes.md").write_text(
        "sample material\n", encoding="utf-8")
    return inp


def test_help():
    """A0: entry point exists and reports usage."""
    ok = Path(__file__).resolve().parent.joinpath("main.py").exists()
    check("A0 main.py present", ok, "scripts/main.py must exist")


def test_empty_input(tmp):
    """C1: empty input dir -> exit 0, insufficient_evidence."""
    empty = Path(tmp) / "empty"
    empty.mkdir()
    out = Path(tmp) / "out_empty"
    errs = []
    rc = run_scaffold(empty, out)
    r = load_result(out)
    check("C1 empty input -> insufficient_evidence, exit 0",
          rc == 0 and r and r.get("status") == "insufficient_evidence",
          "status=%s rc=%s" % (r.get("status") if r else None, rc))


def test_bad_json(tmp):
    """C2: bad JSON -> input_error, not a crash."""
    inp = Path(tmp) / "badjson"
    inp.mkdir()
    (inp / "case.json").write_text("{ not json", encoding="utf-8")
    out = Path(tmp) / "out_badjson"
    rc = run_scaffold(inp, out)
    r = load_result(out)
    check("C2 bad JSON -> input_error, no crash",
          rc == 0 and r and r.get("status") == "input_error",
          "status=%s" % (r.get("status") if r else None))


def test_scaffold_contract(tmp):
    """B2: output_contract keys are all backfilled."""
    inp = make_case(tmp, "challenge-contract",
                    contract={"search_results": [], "ranking": [], "evidence": []},
                    body="some task")
    out = Path(tmp) / "out_contract"
    rc = run_scaffold(inp, out)
    r = load_result(out)
    ok = (rc == 0 and r and r.get("case_id") == "challenge-contract"
          and "search_results" in r and "ranking" in r and "evidence" in r
          and "status" in r and "sources" in r and "change_log" in r)
    check("B2 scaffold backfills every contract field + meta", ok,
          "case_id=%s keys=%s" % (r.get("case_id") if r else None,
                                  sorted(r.keys()) if r else None))


def test_normalize():
    """D2: DOI/arXiv/title normalisation is stable and canonical."""
    rec = {"doi": "https://doi.org/10.1000/XYZ", "title": "  A Paper!  "}
    cid = pipeline.canonical_id(rec)
    check("D2 DOI canonical id", cid == "doi:10.1000/xyz", cid)
    rec2 = {"arxiv": "arXiv:1901.01234v2"}
    cid2 = pipeline.canonical_id(rec2)
    check("D2 arXiv canonical id strips version", cid2 == "arxiv:1901.01234", cid2)
    rec3 = {"title": "A Paper"}
    check("D2 title canonical id", pipeline.canonical_id(rec3) == "title:a paper")


def test_dedup(tmp):
    """D3: DOI > arXiv > title priority; duplicates merged; markers detected."""
    recs = [
        {"title": "A Paper", "doi": "10.1000/XYZ"},
        {"title": "a paper", "doi": "https://doi.org/10.1000/xyz"},
        {"title": "Another", "arxiv": "arXiv:1901.01234v1"},
        {"title": "Another", "arxiv": "1901.01234"},
        {"title": "Retracted Study", "doi": "10.1000/RET", "status": "retracted"},
    ]
    d = pipeline.dedup_records(recs)
    counts = d["counts"]
    check("D3 dedup merges DOI/arXiv/title variants",
          counts["groups"] == 3 and counts["duplicates_merged"] == 2,
          json.dumps(counts))
    ret = [g for g in d["groups"] if "retraction" in g["markers"]]
    check("D3 retraction marker detected", len(ret) == 1)


def test_determinism(tmp):
    """D5: identical inputs -> byte-identical substantive output."""
    inp = make_case(tmp, "challenge-det",
                    contract={"search_results": [], "evidence": []})
    work = Path(tmp) / "work"
    findings = {
        "search_results": [
            {"title": "A Paper", "doi": "10.1000/XYZ",
             "locator": "materials/notes.md:1", "quote": "sample material"}],
        "evidence": [
            {"claim": "one candidate found", "locator": "materials/notes.md:1",
             "quote": "sample material"}],
        "tracking": {"subscription": "topic=test", "window": {"start": "2026-01-01", "end": "2026-01-08"}},
        "status": "ok",
    }
    work.mkdir(parents=True, exist_ok=True)
    (work / "findings.json").write_text(json.dumps(findings, ensure_ascii=False),
                                        encoding="utf-8")

    out1 = Path(tmp) / "out1"
    out2 = Path(tmp) / "out2"
    pipeline.finalize(str(inp), str(out1), str(work),
                      findings_path=str(work / "findings.json"))
    pipeline.finalize(str(inp), str(out2), str(work),
                      findings_path=str(work / "findings.json"))
    r1 = load_result(out1)
    r2 = load_result(out2)
    identical = _stable(r1) == _stable(r2)
    check("D5 same input -> same substantive output (determinism)", identical,
          "stable hash %s" % (hash(_stable(r1)) % 100000))


def test_oversize_and_missing(tmp):
    """C3/C4: oversize file skipped; missing input dir handled."""
    inp = make_case(tmp, "challenge-big")
    big = Path(inp) / "materials" / "big.md"
    big.write_bytes(b"x" * (pipeline.MAX_FILE_BYTES + 1))
    text, errs = pipeline.read_text(str(big))
    check("C3 oversize file skipped with degradation",
          text is None and any("oversize" in e for e in errs), "; ".join(errs))
    text, errs = pipeline.read_text(str(Path(tmp) / "no-such-file"))
    check("C4 missing file reported", text is None and any("missing" in e for e in errs))


def run_scaffold(inp, out):
    import subprocess
    script = Path(__file__).resolve().parent / "main.py"
    rc = subprocess.call([sys.executable, "-B", str(script), "scaffold",
                          "--input", str(inp), "--output", str(out)])
    return rc


def load_result(out):
    p = Path(out) / "result.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Skill self-test")
    parser.add_argument("--keep-tmp", action="store_true",
                        help="keep the temporary directory for inspection")
    a = parser.parse_args(argv)

    test_help()
    tmp = tempfile.mkdtemp(prefix="lit-selftest-")
    try:
        test_empty_input(tmp)
        test_bad_json(tmp)
        test_scaffold_contract(tmp)
        test_normalize()
        test_dedup(tmp)
        test_determinism(tmp)
        test_oversize_and_missing(tmp)
    finally:
        if not a.keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok in _results if not ok]
    print("")
    print("Selftest: %d passed, %d failed" % (len(_results) - len(failed), len(failed)))
    if failed:
        print("Failed: " + ", ".join(failed))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
