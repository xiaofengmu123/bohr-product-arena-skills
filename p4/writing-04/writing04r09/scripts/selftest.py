"""In-memory self test for the citation management engine.

Run standalone (`python3 -B scripts/selftest.py`) or via
`python3 -B scripts/main.py --self-test`. Exits 0 when all checks pass.
Uses synthetic fixtures only — never pretends to be a real case.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce

BIB = """
@article{smith2020,
  title = {Deep learning for climate prediction},
  author = {Smith, John and Doe, Jane},
  journal = {Nature Climate},
  year = {2020},
  volume = {10},
  pages = {100-110},
  doi = {10.1000/xyz}
}
@article{smith2020dup,
  title = {Deep Learning for Climate Prediction},
  author = {Smith, John and Doe, Jane},
  journal = {Nat. Climate},
  year = {2020},
  doi = {10.1000/xyz}
}
"""

RIS = """
TY  - JOUR
TI  - A second paper
AU  - Li, Ming
PY  - 2019
JF  - Journal of Tests
VL  - 5
IS  - 2
SP  - 30
DO  - 10.2000/abc
ER  -
"""

MANUSCRIPT = "研究发现 A[1]，另一研究[2,3] 支持此结论，后续证据[1] 再次确认。"


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def run() -> bool:
    ok = True
    try:
        # 1. BibTeX + RIS parsing.
        bib = ce.parse_bibtex(BIB, "test.bib")
        ris = ce.parse_ris(RIS, "test.ris")
        _assert(len(bib) == 2, "bib parse should yield 2 entries")
        _assert(len(ris) == 1, "ris parse should yield 1 entry")
        _assert(bib[0]["doi"] == "10.1000/xyz", "doi normalization failed")

        # 2. Deduplication: two DOI-identical records merge into one.
        entries = bib + ris
        unique, evidence = ce.deduplicate(entries)
        _assert(len(unique) == 2, f"dedup should yield 2 unique, got {len(unique)}")
        _assert(any(e["criterion"] == "doi" for e in evidence), "doi merge evidence missing")

        # 3. Marker detection and renumbering.
        markers = ce.detect_citations(MANUSCRIPT)
        _assert(len(markers) == 3, f"should detect 3 markers, got {len(markers)}")
        _assert(ce.render_numbers([1, 2, 3, 5]) == "[1-3,5]", "range rendering failed")
        entry_map = {e["key"]: e for e in unique}
        first = unique[0]
        second = unique[1]
        assignment = {1: first["key"], 2: second["key"], 3: second["key"]}
        new_text, order, unresolved = ce.renumber_numeric(MANUSCRIPT, assignment, entry_map)
        _assert("[1]" in new_text and "[2]" in new_text, "renumber output missing markers")
        _assert(order and len(order) == 2, "renumber order should have 2 entries")
        _assert(not unresolved, "renumber should have no unresolved with full assignment")

        # 4. Formatting smoke test.
        gbt = ce.format_entry(first, "gbt7714")
        apa = ce.format_entry(first, "apa")
        _assert("SMITH" in gbt, "gbt author formatting failed")
        _assert("doi.org/10.1000/xyz" in apa, "apa doi tail missing")

        # 5. Consistency check.
        rep = ce.consistency_check(new_text, order, entry_map)
        _assert(rep["numbering_consistent"], "numbering should be consistent")

        print("PASS: parsing, dedup, renumber, format, consistency all OK")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"FAIL: {exc}")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
