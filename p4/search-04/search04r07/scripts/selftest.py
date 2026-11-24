#!/usr/bin/env python3
"""selftest.py — offline self-check for the citation-tracing skill.

Runs the robustness matrix required by the scoring rubric without needing the
official hidden cases:
  * empty input directory          -> graceful skeleton, exit code 0
  * malformed case.json            -> degraded, valid result.json
  * valid case.json + output_contract -> every contract field is present
  * oversized file                 -> skipped with a degradation record
  * missing parameters             -> defaults to /input /app/output /app/work
  * determinism                    -> repeated bootstrap yields identical payload
                                     (excluding timestamps/change_log)
  * finalize                       -> report/csv/svg artifacts + valid SVG

Usage:  python3 scripts/selftest.py [--verbose]
Exit code 0 means all checks passed; 1 means at least one check failed.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import citation_lib as lib  # noqa: E402
import main as cli  # noqa: E402


VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    if VERBOSE or not ok:
        print(("PASS" if ok else "FAIL") + "  " + name + (("  " + detail) if detail else ""))
    return bool(ok)


def _write(path, content, mode="w", encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding=encoding) as fh:
        fh.write(content)


def _strip_volatile(result):
    """Remove timestamp-dependent fields for determinism comparison."""
    if isinstance(result, dict):
        return {k: _strip_volatile(v) for k, v in result.items()
                if k not in ("change_log", "runtime_seconds", "_started_at")}
    if isinstance(result, list):
        return [_strip_volatile(v) for v in result]
    return result


def test_empty_input(tmp):
    case_dir = os.path.join(tmp, "empty")
    out = os.path.join(tmp, "out_empty")
    work = os.path.join(tmp, "work_empty")
    code = cli.bootstrap(case_dir, out, work)
    r, err = lib.read_json(os.path.join(out, "result.json"))
    return check("empty-input exits 0 and writes valid result",
                 code == 0 and err is None and r is not None
                 and r.get("status") in ("input_error", "insufficient_evidence"),
                 f"status={r.get('status') if r else None}")


def test_bad_json(tmp):
    case_dir = os.path.join(tmp, "badjson")
    os.makedirs(case_dir, exist_ok=True)
    _write(os.path.join(case_dir, "case.json"), "{ not valid json !!!")
    out = os.path.join(tmp, "out_bad")
    work = os.path.join(tmp, "work_bad")
    code = cli.bootstrap(case_dir, out, work)
    r, err = lib.read_json(os.path.join(out, "result.json"))
    degraded = any(e.get("item") == "case.json" for e in (r or {}).get("errors", []))
    return check("bad-JSON degrades without crash",
                 code == 0 and err is None and r is not None and degraded,
                 f"errors={len((r or {}).get('errors', []))}")


def test_contract(tmp):
    case_dir = os.path.join(tmp, "contract")
    os.makedirs(case_dir, exist_ok=True)
    contract = {
        "results": {"type": "object"},
        "seed": {"type": "string"},
        "seed_key": {"type": "string"},
        "research_question": {"type": "string"},
        "limitations": {"type": "object"},
        "pending_human": {"type": "array"},
        "sources": {"type": "array"},
    }
    _write(os.path.join(case_dir, "case.json"), json.dumps({
        "case_id": "case-test-001",
        "output_contract": contract,
    }, ensure_ascii=False))
    out = os.path.join(tmp, "out_contract")
    work = os.path.join(tmp, "work_contract")
    cli.bootstrap(case_dir, out, work)
    r, err = lib.read_json(os.path.join(out, "result.json"))
    ok = err is None and r.get("case_id") == "case-test-001"
    missing = [k for k in contract if k not in r]
    return check("output_contract fields fully backfilled", ok and not missing,
                 f"missing={missing}")


def test_oversized(tmp):
    big = os.path.join(tmp, "big.txt")
    _write(big, "x" * (lib.MAX_TEXT_BYTES + 10))
    text, err = lib.read_text_robust(big)
    return check("oversized file is skipped", text is None and err and "oversized" in err,
                 f"err={err}")


def test_defaults():
    return check("missing params fall back to defaults",
                 cli.DEFAULT_INPUT == "/input" and cli.DEFAULT_OUTPUT == "/app/output"
                 and cli.DEFAULT_WORK == "/app/work")


def test_determinism(tmp):
    case_dir = os.path.join(tmp, "det")
    os.makedirs(case_dir, exist_ok=True)
    _write(os.path.join(case_dir, "case.json"), json.dumps({
        "case_id": "det-1",
        "output_contract": {"papers": {"type": "array"}, "seed": {"type": "string"}},
    }))
    a_dir, b_dir = os.path.join(tmp, "det_a"), os.path.join(tmp, "det_b")
    cli.bootstrap(case_dir, a_dir, os.path.join(tmp, "det_wa"))
    cli.bootstrap(case_dir, b_dir, os.path.join(tmp, "det_wb"))
    ra, _ = lib.read_json(os.path.join(a_dir, "result.json"))
    rb, _ = lib.read_json(os.path.join(b_dir, "result.json"))
    same = _strip_volatile(ra) == _strip_volatile(rb)
    return check("repeated bootstrap is deterministic", same)


def test_finalize(tmp):
    case_dir = os.path.join(tmp, "fin")
    os.makedirs(case_dir, exist_ok=True)
    _write(os.path.join(case_dir, "case.json"), json.dumps({
        "case_id": "fin-1",
        "output_contract": {
            "results": {"type": "object"},
            "seed": {"type": "string"},
            "seed_key": {"type": "string"},
        },
    }))
    out = os.path.join(tmp, "out_fin")
    work = os.path.join(tmp, "work_fin")
    cli.bootstrap(case_dir, out, work)
    # inject a small traced graph before finalize
    r, _ = lib.read_json(os.path.join(out, "result.json"))
    r["seed"] = {"id": "P1", "title": "Seed paper", "year": 2020}
    r["seed_key"] = "doi:10.1234/seed"
    r["results"] = {
        "papers": [
            {"id": "P1", "title": "Seed paper", "year": 2020, "doi": "10.1234/seed", "source_confidence": 1.0},
            {"id": "P2", "title": "Cited work", "year": 2018, "doi": "10.1234/work", "source_confidence": 0.9},
        ],
        "edges": [
            {"source": "P1", "target": "P2", "relation": "cites",
             "evidence": {"locator": "materials/x.pdf#p2", "quote": "we build on [P2]"}},
        ],
    }
    lib.atomic_write_text(os.path.join(out, "result.json"), lib.json_dumps(r))
    code = cli.finalize(out, work, case_dir)
    svg_path = os.path.join(out, "citation_graph.svg")
    svg_ok = os.path.exists(svg_path)
    if svg_ok:
        try:
            ET.parse(svg_path)
        except Exception:
            svg_ok = False
    return check("finalize writes report/csv/svg and valid SVG",
                 code == 0 and svg_ok
                 and os.path.exists(os.path.join(out, "report.md"))
                 and os.path.exists(os.path.join(out, "papers.csv"))
                 and os.path.exists(os.path.join(out, "edges.csv")))


def main():
    tmp = tempfile.mkdtemp(prefix="citation_selftest_")
    try:
        test_empty_input(tmp)
        test_bad_json(tmp)
        test_contract(tmp)
        test_oversized(tmp)
        test_defaults()
        test_determinism(tmp)
        test_finalize(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\nselftest: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:  # a broken selftest must still report, not hang
        print(f"selftest crashed: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)