"""Standalone self-test for the citation-management skill.

Runs the in-memory engine tests from scripts/main.py plus a few integration
checks (help flag, robust handling of an empty input directory and a bad JSON
file).  Exit code 0 = pass, non-zero = fail.  Pure standard library.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce  # noqa: E402
import main as main_mod  # noqa: E402


def _test_engine():
    bib = """@article{smith2020,
  author = {Smith, John and Doe, Jane},
  title = {A Test of Citation Management},
  journal = {J. Test. Res.},
  year = {2020},
  volume = {12},
  number = {3},
  pages = {100-110},
  doi = {10.1000/xyz123}
}

@article{smith2020dup,
  author = {Smith, John and Doe, Jane},
  title = {A test of citation management},
  journal = {J. Test. Res.},
  year = {2020},
  doi = {10.1000/xyz123}
}"""
    entries = ce.parse_bibtex(bib)
    assert len(entries) == 2, f"expected 2 entries, got {len(entries)}"
    norms = [ce.normalize_entry(e) for e in entries]
    assert norms[0]["fields"]["doi"] == "10.1000/xyz123"
    assert norms[0]["fields"]["_entry_type"] == "article"
    props = ce.deduplicate(norms)[1]
    assert len(props) == 1 and props[0]["criterion"] == "doi", f"dedup proposal: {props}"

    text = "A first result [1]. A second result [2, 3]. A third result."
    claims = ce.extract_claims(text)
    assert len(claims) >= 3, f"claims: {len(claims)}"
    assert claims[0]["markers"] and claims[1]["markers"]
    assert claims[2]["needs_citation"] is False or claims[2]["needs_citation"] is True

    refs = ce.assign_ref_ids(norms)
    ce.apply_merge_proposals({"references": refs, "merge_proposals": props})
    dup = [r for r in refs if r["status"] == "duplicate"]
    assert len(dup) == 1, "one entry should be marked duplicate"

    # Candidate merge: same title/author/year without DOI must be proposed but
    # not auto-applied (same-name/different-work guard).
    bib2 = """@article{lee2019,
  author = {Lee, A.},
  title = {Same Title Here},
  year = {2019}
}
@article{lee2019b,
  author = {Lee, A.},
  title = {Same Title Here},
  year = {2019}
}"""
    e2 = ce.parse_bibtex(bib2)
    n2 = [ce.normalize_entry(e) for e in e2]
    props2 = ce.deduplicate(n2)[1]
    assert len(props2) == 1 and props2[0]["confidence"] == "candidate", f"candidate: {props2}"
    refs2 = ce.assign_ref_ids(n2)
    ce.apply_merge_proposals({"references": refs2, "merge_proposals": props2})
    assert all(r["status"] == "canonical" for r in refs2), "candidate merge must not auto-apply"

    num = ce.compute_numbering([{"claim_id": "c001", "ref_ids": ["r001"]}],
                               ["c001", "c002", "c003"])
    assert num["id_to_number"]["r001"] == 1
    assert ce.compress_numbers([1, 2, 3, 5]) == "1-3,5"
    assert ce.compress_numbers([1, 2]) == "1,2"

    gbt = ce.render_entry_gbt(refs[0])
    assert "[J]" in gbt and "10.1000/xyz123" in gbt, gbt
    apa = ce.render_entry_apa(refs[0])
    assert "(2020)" in apa, apa
    return True


def _run_cli(args, cwd):
    py = sys.executable
    return subprocess.run([py, "-B", str(_SCRIPT_DIR / "main.py"), *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=120)


def _test_cli_robustness():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        out = root / "output"
        work = root / "work"
        inp.mkdir()
        # 1. empty input -> insufficient_evidence, exit 0
        r = _run_cli(["--input", str(inp), "--output", str(out), "--work", str(work)], root)
        assert r.returncode == 0, f"empty input rc={r.returncode} stderr={r.stderr}"
        res = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert res["status"] == "insufficient_evidence", res["status"]
        # 2. bad JSON case.json -> input_error captured, still exit 0
        (inp / "case.json").write_text("{not json", encoding="utf-8")
        (inp / "task.md").write_text("# task", encoding="utf-8")
        r2 = _run_cli(["--input", str(inp), "--output", str(out), "--work", str(work)], root)
        assert r2.returncode == 0
        res2 = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert res2["status"] in ("partial", "insufficient_evidence", "input_error"), res2["status"]
        assert res2["errors"], "bad JSON should be recorded"
    return True


def main():
    try:
        _test_engine()
        print("engine self-test PASSED")
    except Exception as exc:  # noqa: BLE001
        print(f"engine self-test FAILED: {exc}")
        return 1
    try:
        _test_cli_robustness()
        print("cli robustness self-test PASSED")
    except Exception as exc:  # noqa: BLE001
        print(f"cli robustness self-test FAILED: {exc}")
        return 1
    # --help must exit 0
    r = subprocess.run([sys.executable, "-B", str(_SCRIPT_DIR / "main.py"), "--help"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print("--help self-test FAILED")
        return 1
    print("--help self-test PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
