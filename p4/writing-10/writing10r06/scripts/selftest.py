#!/usr/bin/env python3
"""Self-test for the reviewer-revision skill.

Creates throwaway directories under the system temp dir and exercises four
scenarios. All synthetic materials are generated at runtime, clearly labelled
"SYNTHETIC SELFTEST", and are never written into the shipped package.

Run:  python scripts/selftest.py
Exit code 0 means every scenario behaved as documented.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core  # noqa: E402


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def scenario_empty():
    with tempfile.TemporaryDirectory(prefix="rrs_empty_") as tmp:
        base = Path(tmp)
        out, work, inp = base / "output", base / "work", base / "input"
        inp.mkdir()
        result, err = core.build_skeleton(str(inp / "case.json"), str(inp / "task.md"), str(out), str(work))
        assert err is None, err
        assert result["case_id"] == "unknown"
        assert result["errors"]
        return "empty input dir -> valid skeleton, errors recorded"


def scenario_bad_json():
    with tempfile.TemporaryDirectory(prefix="rrs_bad_") as tmp:
        base = Path(tmp)
        out, work, inp = base / "output", base / "work", base / "input"
        _write(inp / "case.json", "{ not valid json ")
        _write(inp / "task.md", "# task\n")
        result, err = core.build_skeleton(str(inp / "case.json"), str(inp / "task.md"), str(out), str(work))
        assert err is None, err
        assert result["case_id"] == "unknown"
        assert any("bad json" in e for e in result["errors"])
        return "bad case.json -> skeleton written, error recorded, no crash"


def scenario_missing_input():
    with tempfile.TemporaryDirectory(prefix="rrs_missing_") as tmp:
        base = Path(tmp)
        out, work, inp = base / "output", base / "work", base / "input"
        result, err = core.build_skeleton(str(inp / "case.json"), str(inp / "task.md"), str(out), str(work))
        assert err is None, err
        assert result["case_id"] == "unknown"
        assert result["errors"]
        return "missing input dir -> skeleton written, errors recorded"


def scenario_happy():
    with tempfile.TemporaryDirectory(prefix="rrs_happy_") as tmp:
        base = Path(tmp)
        out, work, inp = base / "output", base / "work", base / "input"
        # ---- synthetic materials, clearly labelled, runtime-only -------------
        _write(inp / "case.json", json.dumps({
            "case_id": "SYNTHETIC-SELFTEST-001",
            "task": "Reply to reviewers and revise the baseline manuscript.",
        }, ensure_ascii=False))
        _write(inp / "task.md", "# 审稿回复与论文修改\n\n材料见 materials/。")
        _write(inp / "materials" / "reviewer-comments.md",
               "R1: Please justify the sample size.\nR2: The figure 3 legend is missing units.\n")
        _write(inp / "materials" / "manuscript-baseline.md",
               "# Baseline\n\n## Methods\n\nThe sample size was chosen pragmatically.\n\n## Figure 3\n\nLegend.\n")

        result, err = core.build_skeleton(str(inp / "case.json"), str(inp / "task.md"), str(out), str(work))
        assert err is None, err
        assert result["case_id"] == "SYNTHETIC-SELFTEST-001"

        comments, _ = core.read_text(str(inp / "materials" / "reviewer-comments.md"))
        baseline, _ = core.read_text(str(inp / "materials" / "manuscript-baseline.md"))
        ledgers, led_errs = core.build_ledgers(comments, baseline, str(out), str(work))
        assert not led_errs, led_errs
        assert len(ledgers["review_ledger"]["comments"]) == 2

        # Fill a consistent, minimal ledger (agent's job, exercised here end-to-end).
        rl = ledgers["review_ledger"]
        rl["comments"][0]["decision"] = "accept"
        rl["comments"][0]["type"] = "method"
        rl["comments"][0]["severity"] = "major"
        rl["comments"][0]["revision_refs"] = ["CHG-1"]
        rl["comments"][0]["status"] = "addressed"
        rl["comments"][1]["decision"] = "accept"
        rl["comments"][1]["revision_refs"] = ["CHG-2"]
        rl["comments"][1]["status"] = "addressed"
        ledgers["revision_map"]["changes"] = [
            {"change_id": "CHG-1", "comment_ids": ["R1"], "target": "materials/manuscript-baseline.md",
             "section": "Methods", "anchor": "sample size was chosen pragmatically",
             "before_quote": "chosen pragmatically.", "after_quote": "chosen as n=120 per power analysis.",
             "locator": "revision-1:Methods:para1", "status": "done", "note": ""},
            {"change_id": "CHG-2", "comment_ids": ["R2"], "target": "materials/manuscript-baseline.md",
             "section": "Figure 3", "anchor": "Legend.",
             "before_quote": "Legend.", "after_quote": "Legend. Units: ms.",
             "locator": "revision-1:Figure 3:legend", "status": "done", "note": ""},
        ]
        ledgers["evidence_ledger"]["evidence"] = []
        for name, obj in (("review_ledger.json", rl), ("revision_map.json", ledgers["revision_map"]),
                          ("evidence_ledger.json", ledgers["evidence_ledger"])):
            e = core.write_json(out / name, obj)
            assert e is None, e

        _write(out / "response-letter.md",
               "# Response letter (SYNTHETIC SELFTEST)\n\nR1: revised; see CHG-1.\nR2: revised; see CHG-2.\n")
        result["status"] = "complete"
        result["deliverable"] = {"path": str(out / "response-letter.md"), "format": "markdown",
                                 "title": "synthetic", "sections": [], "status": "done"}
        result["fact_source_mapping"] = [
            {"claim": "sample size n=120", "source_ref": "materials/manuscript-baseline.md",
             "locator": "revision-1:Methods:para1", "quote": "chosen as n=120 per power analysis.", "status": "traced"},
        ]
        e = core.write_json(out / "result.json", result)
        assert e is None, e

        problems = core.validate_result(str(out / "result.json"), str(out))
        assert problems == [], problems
        return "happy path -> ledgers + response letter + result.json validate cleanly"


def scenario_inconsistent():
    """Verify validate actually catches a fabricated 'revised' claim."""
    with tempfile.TemporaryDirectory(prefix="rrs_badref_") as tmp:
        base = Path(tmp)
        out, work, inp = base / "output", base / "work", base / "input"
        _write(inp / "case.json", json.dumps({"case_id": "SYNTHETIC-SELFTEST-002"}, ensure_ascii=False))
        _write(inp / "task.md", "# task\n")
        _write(inp / "materials" / "reviewer-comments.md", "R1: add a citation.\n")
        _write(inp / "materials" / "manuscript-baseline.md", "# Baseline\n\nMethods.\n")

        result, err = core.build_skeleton(str(inp / "case.json"), str(inp / "task.md"), str(out), str(work))
        assert err is None, err
        comments, _ = core.read_text(str(inp / "materials" / "reviewer-comments.md"))
        baseline, _ = core.read_text(str(inp / "materials" / "manuscript-baseline.md"))
        ledgers, led_errs = core.build_ledgers(comments, baseline, str(out), str(work))
        assert not led_errs, led_errs

        # Accept a comment but do NOT create the claimed change -> must be flagged.
        rl = ledgers["review_ledger"]
        rl["comments"][0]["decision"] = "accept"
        rl["comments"][0]["revision_refs"] = ["CHG-99"]
        rl["comments"][0]["status"] = "addressed"
        core.write_json(out / "review_ledger.json", rl)
        # revision_map has no CHG-99 -> cross-ref broken.
        core.write_json(out / "revision_map.json", ledgers["revision_map"])
        core.write_json(out / "evidence_ledger.json", ledgers["evidence_ledger"])
        _write(out / "response-letter.md", "# Response (SYNTHETIC SELFTEST)\n\nR1 revised; see CHG-99.\n")

        result["status"] = "complete"
        result["deliverable"] = {"path": str(out / "response-letter.md"), "format": "markdown",
                                 "title": "synthetic", "sections": [], "status": "done"}
        core.write_json(out / "result.json", result)

        problems = core.validate_result(str(out / "result.json"), str(out))
        joined = "\n".join(problems)
        assert "references unknown change CHG-99" in joined, problems
        return "validate catches response referencing a non-existent change"


def main():
    checks = [
        scenario_empty,
        scenario_bad_json,
        scenario_missing_input,
        scenario_happy,
        scenario_inconsistent,
    ]
    failed = 0
    for fn in checks:
        try:
            msg = fn()
            print("PASS  %-14s %s" % (fn.__name__, msg))
        except AssertionError as exc:
            failed += 1
            print("FAIL  %-14s %s" % (fn.__name__, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("ERROR %-14s %s" % (fn.__name__, exc))
    print("selftest: %d/%d passed" % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
