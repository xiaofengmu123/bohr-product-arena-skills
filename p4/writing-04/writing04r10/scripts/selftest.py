"""In-memory self-tests for the citation-management Skill (pure stdlib).

Run:
    python3 -B scripts/selftest.py
Exit code 0 when every assertion passes; 1 otherwise. No files are written.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import core_impl as ce
import main as entry


_PASS = 0
_FAIL = 0


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"FAIL  {name}")


def _bib() -> str:
    return """@article{smith2020,
  author = {Smith, John and Wang, Lei},
  title = {A Study of Citation Quality},
  journal = {Journal of Testing},
  year = {2020},
  volume = {10},
  number = {2},
  pages = {100--110},
  doi = {10.1000/xyz.123}
}
@article{smith2020dup,
  author = {Smith, John and Wang, Lei},
  title = {A study of citation quality},
  journal = {Journal of Testing},
  year = {2020},
  volume = {10},
  pages = {100--110}
}
@article{jones2019,
  author = {Jones, A.},
  title = {Different Result},
  journal = {Other Journal},
  year = {2019},
  doi = {10.9999/other}
}"""


def test_bibtex() -> None:
    entries = ce.parse_bibtex(_bib())
    check("bibtex parses 3 entries", len(entries) == 3)
    check("bibtex field lowercased", "title" in entries[0])
    check("bibtex key captured", entries[0].get("_key") == "smith2020")
    check("bibtex nested title kept", "Citation Quality" in entries[0].get("title", ""))


def test_ris() -> None:
    text = (
        "TY  - JOUR\nTI  - Test Title\nAU  - Smith, John\nAU  - Wang, Lei\n"
        "PY  - 2021\nDO  - 10.1000/abc\nJO  - Journal of RIS\nER  - \n"
    )
    entries = ce.parse_ris(text)
    check("ris parses 1 entry", len(entries) == 1)
    check("ris authors joined", "Smith" in entries[0].get("author", ""))
    check("ris doi captured", entries[0].get("doi") == "10.1000/abc")


def test_normalize_and_dedup() -> None:
    raw = ce.parse_bibtex(_bib())
    norm = [ce.normalize_entry(r, "test.bib", i) for i, r in enumerate(raw)]
    check("doi normalized lowercase", norm[0]["_norm_doi"] == "10.1000/xyz.123")
    deduped, merge, orig_to_dedup = ce.deduplicate(norm)
    check("doi+title dup merged to 2", len(deduped) == 2)
    check("merge evidence recorded", len(merge) == 1)
    check("merge criterion explained", merge[0]["criterion"] in ("doi", "title_author_year"))
    check("orig-to-dedup maps dup to survivor", orig_to_dedup[0] == orig_to_dedup[1])


def test_renumber() -> None:
    ms = ce.parse_manuscript_text(
        "Alpha [2] and beta [1,3]. Gamma [2-3]. Delta [4].",
        "t.md",
    )
    _, mapping = ce.renumber_markers(ms, [0, 1, 2, 3])
    check("renumber old->new populated", len(mapping["old_to_new"]) == 4)
    check("renumber first-appearance order", mapping["old_to_new"][2] == 1)


def test_renumber_after_dedup() -> None:
    # 3 original entries where #2 is a duplicate of #1 -> survivors 0,1
    ms = ce.parse_manuscript_text("One [1]. Two [2]. Three [3].", "t.md")
    _, mapping = ce.renumber_markers(ms, [0, 0, 1])
    check(
        "merged duplicate maps to same new number",
        mapping["old_to_new"][1] == mapping["old_to_new"][2],
    )
    check("survivor set contiguous", set(mapping["old_to_new"].values()) == {1, 2})


def test_format() -> None:
    raw = ce.parse_bibtex(_bib())[0]
    e = ce.normalize_entry(raw, "t.bib", 0)
    g = ce.format_gbt7714(e)
    check("gbt7714 contains DOI", "DOI:" in g)
    check("gbt7714 has type tag", "[J]" in g)
    a = ce.format_apa(e)
    check("apa contains doi url", "https://doi.org/" in a)


def test_consistency() -> None:
    ms = ce.parse_manuscript_text("X [1] and Y [5].", "t.md")
    entries = [{"title": "A"}, {"title": "B"}]
    rep = ce.consistency_check(ms, entries, [0, 1], {"old_to_new": {}, "new_order": []})
    check("unresolved citation detected", 5 in rep["unresolved_citations"])
    check("unreferenced entry detected", len(rep["unreferenced_entries"]) >= 1)


def test_skeleton_contract() -> None:
    ledger = ce.Ledger()
    case = {
        "case_id": "c1",
        "output_contract": {"manuscript_path": "str", "references_path": "str"},
    }
    sk = entry.build_skeleton(case, ledger)
    check("skeleton echoes contract keys", "manuscript_path" in sk)
    check("skeleton has status", sk["status"] == "partial")
    check("skeleton has case_id", sk["case_id"] == "c1")
    check("skeleton sources is list", isinstance(sk["sources"], list))


def test_contract_backfill() -> None:
    result = {"case_id": "c1", "manuscript_path": None, "notes_path": None}
    artifacts = {
        "manuscript": "/app/output/manuscript.md",
        "references": "/app/output/references.md",
        "evidence_map": "/app/output/evidence_map.json",
        "consistency_report": "/app/output/consistency_report.json",
        "ir": "/app/output/citation_graph.json",
    }
    entry._backfill_contract(result, artifacts)
    check("contract manuscript key filled", result["manuscript_path"] == artifacts["manuscript"])
    check("unknown contract key stays null", result["notes_path"] is None)


def test_bad_json() -> None:
    ledger = ce.Ledger()
    out = ce.load_json_text("{not json", "bad.json", ledger)
    check("bad json returns None", out is None)
    check("bad json recorded", any("bad JSON" in e for e in ledger.errors))


def test_compact() -> None:
    check("compact ranges", ce._compact_numbers([1, 2, 3, 5]) == ["1-3", "5"])
    check("compact single", ce._compact_numbers([7]) == ["7"])


def run_all() -> bool:
    global _PASS, _FAIL
    _PASS = _FAIL = 0
    tests = [
        test_bibtex,
        test_ris,
        test_normalize_and_dedup,
        test_renumber,
        test_renumber_after_dedup,
        test_format,
        test_consistency,
        test_skeleton_contract,
        test_contract_backfill,
        test_bad_json,
        test_compact,
    ]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            _FAIL += 1
            print(f"ERROR {t.__name__}: {exc}")
    print(f"selftest: {_PASS} passed, {_FAIL} failed")
    return _FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
