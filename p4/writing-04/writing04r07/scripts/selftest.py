# -*- coding: utf-8 -*-
"""Standalone self-test for the citation-integrity skill.

Runs with only the standard library.  Creates temporary fixtures on disk,
exercises the pipeline end-to-end (empty input, bad JSON, bad encoding,
oversized file, missing parameters), and verifies that a legal result.json
is always produced.  Exits 0 on success, 1 on any failure.

Usage:
    python3 -B scripts/selftest.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import main as skill_main  # noqa: E402
import core_impl as ce  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [ok] {name}")
    else:
        FAILURES.append(name)
        print(f"  [FAIL] {name} {detail}")


def _write(tmp: Path, name: str, data: bytes) -> Path:
    p = tmp / name
    p.write_bytes(data)
    return p


def _result(out: Path) -> dict:
    return json.loads((out / "result.json").read_text(encoding="utf-8"))


def _run_case(name: str, build) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        out = root / "output"
        work = root / "work"
        inp.mkdir()
        build(inp)
        rc = skill_main.main(["--input", str(inp), "--output", str(out),
                              "--work", str(work)])
        check(name + " exit 0", rc == 0)
        check(name + " result.json exists", (out / "result.json").exists())
        r = _result(out)
        check(name + " status legal",
              r.get("status") in {"ok", "partial", "insufficient_evidence",
                                  "input_error"},
              f"got {r.get('status')}")


def main() -> int:
    print("== core engine self tests ==")
    rc = skill_main.run_self_test()
    check("in-memory engine self-test", rc == 0)

    print("== case: empty input directory ==")
    def empty(inp: Path) -> None:
        pass
    _run_case("empty", empty)

    print("== case: bad case.json + oversized file ==")
    def bad(inp: Path) -> None:
        _write(inp, "case.json", b"{bad json")
        _write(inp, "task.md", b"# task\n")
        big = inp / "big.bib"
        big.write_bytes(b"@" * (ce.DEFAULT_MAX_BYTES + 10))
    _run_case("bad-json-oversized", bad)

    print("== case: bad encoding library + valid manuscript ==")
    def badenc(inp: Path) -> None:
        _write(inp, "case.json",
               b'{"case_id":"enc-test","output_contract":{"manuscript":null}}')
        _write(inp, "task.md", b"# task\n")
        # GB18030-encoded Chinese title, no BOM.
        _write(inp, "lib.bib", "@article{k, title={\u5f15\u7528}, year={2020}}".encode("gb18030"))
        _write(inp, "paper.md", "Claim [1].\n".encode("utf-8"))
    _run_case("bad-encoding", badenc)

    print("== case: full valid pipeline ==")
    def valid(inp: Path) -> None:
        _write(inp, "case.json",
               json.dumps({
                   "case_id": "case-1",
                   "output_contract": {
                       "manuscript": None, "references": None,
                       "evidence_map": None, "unknown_key": None,
                   },
               }).encode("utf-8"))
        _write(inp, "task.md", "# task\n".encode("utf-8"))
        _write(inp, "lib.bib",
               ("@article{a, title={First}, author={Alice}, year={2020}, doi={10.1/a}}\n"
                "@article{b, title={Second}, author={Bob}, year={2021}, doi={10.1/b}}\n"
                ).encode("utf-8"))
        _write(inp, "paper.md", "See [2] and [1] and [2].\n".encode("utf-8"))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        out = root / "output"
        work = root / "work"
        inp.mkdir()
        valid(inp)
        rc = skill_main.main(["--input", str(inp), "--output", str(out),
                              "--work", str(work), "--style", "apa"])
        check("valid exit 0", rc == 0)
        r = _result(out)
        check("valid status partial", r.get("status") == "partial")
        check("valid case_id", r.get("case_id") == "case-1")
        check("valid contract echoes", r.get("unknown_key") is None)
        check("valid manuscript path echoed",
              isinstance(r.get("manuscript"), str) and "manuscript.md" in r.get("manuscript"))
        check("valid references path echoed",
              isinstance(r.get("references"), str) and "references.md" in r.get("references"))
        check("artifacts populated", bool(r.get("artifacts")))
        mtext = (out / "manuscript.md").read_text(encoding="utf-8")
        check("renumbered output", "[1]" in mtext and "[2]" in mtext, mtext.strip())
        rep = json.loads((out / "consistency_report.json").read_text(encoding="utf-8"))
        check("consistency total refs 2", rep.get("total_references") == 2)
        check("consistency no unresolved", rep.get("unresolved_citations") == [])

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} checks -> {FAILURES}")
        return 1
    print("all self tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
