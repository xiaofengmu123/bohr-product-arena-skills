"""In-memory self-tests for the citation management skill.

Run with:
    python3 -B scripts/selftest.py
or via the main entry point:
    python3 -B scripts/main.py --self-test

Exit code 0 means all checks passed. The tests cover the deterministic
backbone only (parsing, dedup, renumbering, claim extraction, formatting,
skeleton echo). They never assert on fabricated or semantic judgments.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import citation_engine as ce  # noqa: E402


def _check(name: str, cond: bool, detail: str = "") -> bool:
    if not cond:
        print("FAIL %s: %s" % (name, detail))
        return False
    print("ok   %s" % name)
    return True


def test_bibtex_parse() -> bool:
    bib = """
@article{smith2023,
  author = {Smith, John and Doe, Jane},
  title = {A Study of Citation Provenance},
  journal = {Journal of Testing},
  year = {2023},
  volume = {12},
  number = {3},
  pages = {45--67},
  doi = {10.1000/abc.123}
}
@book{lee2022,
  title = {Reference Management},
  author = {Lee, A.},
  year = {2022},
  publisher = {Academic Press}
}
"""
    entries = ce.parse_bibtex(bib)
    ok = _check("bibtex parse count", len(entries) == 2, str(len(entries)))
    if entries:
        ok &= _check("bibtex author kept", entries[0].get("authors") == "Smith, John and Doe, Jane",
                     str(entries[0].get("authors")))
        ok &= _check("bibtex year", entries[0].get("year") == "2023")
    return ok


def test_ris_parse() -> bool:
    ris = """TY  - JOUR
TI  - Another Paper
AU  - Zhang, Wei
PY  - 2021
JO  - Science Advances
VL  - 7
DO  - 10.1000/xyz
ER  -"""
    entries = ce.parse_ris(ris)
    ok = _check("ris parse count", len(entries) == 1, str(len(entries)))
    if entries:
        ok &= _check("ris title", entries[0].get("title") == "Another Paper")
        ok &= _check("ris authors list", isinstance(entries[0].get("authors"), list))
    return ok


def test_dedup() -> bool:
    entries = [
        {"title": "Same Paper", "authors": "Smith, John", "year": "2020", "doi": "10.1000/a"},
        {"title": "Same Paper", "authors": "Smith, J.", "year": "2020", "doi": "10.1000/A"},
        {"title": "Different Work", "authors": "Smith, John", "year": "2020", "doi": ""},
    ]
    result = ce.deduplicate(entries)
    ok = _check("dedup merges doi", len(result["entries"]) == 2, str(len(result["entries"])))
    ok &= _check("merge evidence present", len(result["merge_evidence"]) == 1)
    if result["merge_evidence"]:
        ok &= _check("merge basis explainable", "basis" in result["merge_evidence"][0])
    return ok


def test_renumber() -> bool:
    text = "First claim [3] and another [1,2] here. More [3-5] text."
    mapping = ce.build_renumber_map(text)
    ok = _check("renumber map first-appearance", mapping.get(3) == 1 and mapping.get(1) == 2, str(mapping))
    out = ce.renumber_text(text, mapping)
    ok &= _check("renumber single", "[1]" in out, out)
    ok &= _check("renumber list", "[2,3]" in out, out)
    ok &= _check("renumber range expanded", "[1,4-5]" in out, out)
    return ok


def test_range_expansion() -> bool:
    nums = ce._expand_marker_numbers("[3-5]")
    ok = _check("range expands", nums == [3, 4, 5], str(nums))
    ok &= _check("collapse ranges", ce._collapse_ranges([1, 4, 5]) == "1,4-5", ce._collapse_ranges([1, 4, 5]))
    return ok


def test_claims() -> bool:
    text = "This method achieves 92% accuracy [1]. It was proposed by Smith. A second finding of 45 cases follows."
    claims = ce.extract_claims(text, "manuscript.md")
    ok = _check("cited claim found", len(claims["cited_claims"]) == 1)
    ok &= _check("candidate claims found", len(claims["candidate_claims"]) >= 1)
    if claims["cited_claims"]:
        c = claims["cited_claims"][0]
        ok &= _check("claim locator line", c["locator"]["line"] == 1)
        ok &= _check("claim verbatim quote", c["quote"] == c["text"])
    return ok


def test_formatting() -> bool:
    entry = {
        "type": "article", "title": "A Study", "authors": "Smith, John",
        "year": "2023", "journal": "J Test", "volume": "12", "issue": "3",
        "pages": "45-67", "doi": "10.1000/abc",
    }
    g = ce.format_reference(entry, "gbt7714")
    a = ce.format_reference(entry, "apa")
    ok = _check("gbt7714 has [J]", "[J]" in g, g)
    ok &= _check("apa has doi url", "https://doi.org/" in a, a)
    return ok


def test_skeleton_echo() -> bool:
    import main as m
    case = {
        "case_id": "c1",
        "output_contract": {"manuscript": "x", "custom_key": "y", "nested": {"a": 1}},
    }
    s = m.build_skeleton(case, {"errors": [], "degradations": []})
    ok = _check("skeleton case_id", s["case_id"] == "c1")
    ok &= _check("skeleton echoes keys", s.get("manuscript") is None and s.get("custom_key") is None and "nested" in s)
    ok &= _check("skeleton status partial", s["status"] == "partial")
    return ok


def test_json_roundtrip() -> bool:
    text = '{"references": [{"title": "T", "authors": "A", "year": "2020"}]}'
    data = ce.load_json_text(text, "test.json", {"errors": [], "degradations": []})
    entries = ce.parse_reference_json(data)
    ok = _check("json library parse", len(entries) == 1 and entries[0]["title"] == "T", str(entries))
    return ok


def run_all() -> int:
    tests = [
        test_bibtex_parse,
        test_ris_parse,
        test_dedup,
        test_renumber,
        test_range_expansion,
        test_claims,
        test_formatting,
        test_skeleton_echo,
        test_json_roundtrip,
    ]
    failures = 0
    for t in tests:
        try:
            if not t():
                failures += 1
        except Exception as exc:  # noqa: BLE001
            print("ERROR %s: %s" % (t.__name__, exc))
            failures += 1
    print("\n%d/%d tests passed" % (len(tests) - failures, len(tests)))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
