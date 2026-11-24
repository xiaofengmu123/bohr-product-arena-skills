"""In-memory self-test for the citation-management skill.

Run directly (`python3 -B scripts/selftest.py`) or via
`python3 -B scripts/main.py --self-test`.  Exit code 0 means every check
passed; any failure prints a traceback-style message and exits 1.
Only deterministic behavior is tested here — never fabricated outputs.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ci  # noqa: E402


def _eq(name: str, got, expected) -> bool:
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} expected={expected!r}")
    return ok


def test_bibtex() -> bool:
    bib = (
        "@article{smith2020,\n"
        "  author = {Smith, John and Zhang, San},\n"
        "  title = {Deep {Learning} for {Science}},\n"
        "  journal = {Nature},\n"
        "  year = {2020},\n"
        "  volume = {580},\n"
        "  number = {1},\n"
        "  pages = {1--10},\n"
        "  doi = {10.1000/xyz}\n"
        "}\n"
        "@book{lee2019,\n"
        "  author = {Lee, A.},\n"
        "  title = {A {Book}},\n"
        "  publisher = {Springer},\n"
        "  year = {2019}\n"
        "}\n"
    )
    entries = ci.parse_bibtex(bib)
    if len(entries) != 2:
        return _eq("bibtex entry count", len(entries), 2)
    norm = ci.normalize_entry(entries[0], 1, "x.bib")
    return (
        _eq("bibtex normalized title", norm["title"],
            "Deep Learning for Science")
        and _eq("bibtex journal", entries[0].get("journal"), "Nature")
        and _eq("bibtex doi", entries[0].get("doi"), "10.1000/xyz")
    )


def test_ris() -> bool:
    ris = (
        "TY  - JOUR\n"
        "AU  - Smith, John\n"
        "AU  - Zhang, San\n"
        "TI  - Test paper\n"
        "JO  - Nature\n"
        "PY  - 2021//\n"
        "VL  - 12\n"
        "SP  - 1\n"
        "EP  - 9\n"
        "DO  - 10.1000/abc\n"
        "ER  - \n"
    )
    entries = ci.parse_ris(ris)
    if len(entries) != 1:
        return _eq("ris entry count", len(entries), 1)
    e = entries[0]
    return (
        _eq("ris title", e.get("title"), "Test paper")
        and _eq("ris authors", e.get("author"), ["Smith, John", "Zhang, San"])
        and _eq("ris pages", e.get("pages"), "1-9")
    )


def test_normalize_doi() -> bool:
    return (
        _eq("doi bare", ci.normalize_doi("10.1000/X"), "10.1000/x")
        and _eq("doi url", ci.normalize_doi("https://doi.org/10.1000/X"),
               "10.1000/x")
        and _eq("doi prefix", ci.normalize_doi("doi: 10.1000/x"), "10.1000/x")
    )


def test_deduplicate() -> bool:
    a = ci.normalize_entry({"title": "Same paper", "author": "Smith, J",
                            "year": "2020", "doi": "10.1/a"}, 1, "x.bib")
    b = ci.normalize_entry({"title": "Same paper", "author": "Smith, J",
                            "year": "2020", "doi": "10.1/a",
                            "journal": "Nature"}, 2, "y.json")
    c = ci.normalize_entry({"title": "Different", "author": "Smith, J",
                            "year": "2020"}, 3, "x.bib")
    canonical, merge_map = ci.deduplicate([a, b, c])
    if len(canonical) != 2:
        return _eq("dedup canonical count", len(canonical), 2)
    return (
        _eq("dedup merged_from", canonical[0]["merged_from"], [1, 2])
        and _eq("dedup reason", merge_map[0]["reasons"], ["doi_equal"])
        and _eq("dedup fill journal", canonical[0]["journal"], "Nature")
    )


def test_renumber() -> bool:
    text = "See [3] and [1, 3-4] for details."
    citations = ci.detect_citations(text)
    mapping = ci.build_renumber_plan(citations)
    # first appearance: 3 -> 1, then 1 -> 2, 4 -> 3
    ok = mapping == {3: 1, 1: 2, 4: 3}
    if not ok:
        return _eq("renumber mapping", mapping, {3: 1, 1: 2, 4: 3})
    out = ci.apply_renumber(text, citations, mapping)
    return _eq("renumber text", out, "See [1] and [1-3] for details.")


def test_format() -> bool:
    e = ci.normalize_entry(
        {"type": "article", "title": "A test", "author": "Smith, J",
         "journal": "Nature", "year": "2020", "volume": "1",
         "issue": "2", "pages": "3-4", "doi": "10.1/x"}, 1, "x.bib")
    g = ci.format_entry(e, "gbt7714")
    return (
        _eq("gbt prefix", g.startswith("Smith, J. A test[J]. Nature, 2020"),
            True)
        and _eq("gbt has doi", "DOI: 10.1/x" in g, True)
    )


def test_consistency() -> bool:
    canonical = [ci.normalize_entry({"title": f"T{i}", "author": "A, B",
                                     "year": "2020"}, i, "x.bib")
                 for i in range(1, 4)]
    citations = ci.detect_citations("See [1] and [5].")
    mapping = ci.build_renumber_plan(citations)
    report = ci.consistency_check(canonical, citations, mapping)
    return (
        _eq("unresolved", report["unresolved_citations"],
            [{"marker": "[5]", "line": 1, "number": 5}])
        and _eq("uncited", report["uncited_entries"], [2, 3])
    )


def run() -> int:
    tests = [
        ("bibtex", test_bibtex),
        ("ris", test_ris),
        ("doi normalize", test_normalize_doi),
        ("deduplicate", test_deduplicate),
        ("renumber", test_renumber),
        ("format", test_format),
        ("consistency", test_consistency),
    ]
    failures = 0
    print(f"selftest: {len(tests)} checks")
    for name, fn in tests:
        try:
            if not fn():
                failures += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}: raised {exc!r}")
    print(f"selftest: {'OK' if failures == 0 else f'{failures} FAILED'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
