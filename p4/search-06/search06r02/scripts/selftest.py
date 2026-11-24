# -*- coding: utf-8 -*-
"""Self-test for the literature-tracking Skill package.

Run from the skill root (or anywhere):  python3 scripts/selftest.py

It exercises the stdlib-only CLI end to end in a temporary directory:
empty input, malformed JSON, and a minimal case with an output_contract.
Exit code 0 means every check passed; any failure prints a diff and exits 1.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline  # noqa: E402
import main as cli  # noqa: E402


def run_ok(args_list):
    code = cli.main(args_list)
    assert code in (0, None), "unexpected exit code %r for %r" % (code, args_list)
    return code


def check_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp = root / "in"; out = root / "out"; work = root / "work"
        inp.mkdir()
        run_ok(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r.get("case_id") == "unknown-case", r
        assert r.get("status") == "insufficient_evidence", r
        assert isinstance(r.get("change_log"), list) and r["change_log"][0]["event"] == "skeleton_written"
        assert "errors" in r and "degradations" in r and "pending_human" in r
    print("ok: empty input -> insufficient_evidence skeleton")


def check_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp = root / "in"; out = root / "out"; work = root / "work"
        inp.mkdir()
        (inp / "case.json").write_text("{not valid json", encoding="utf-8")
        run_ok(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r.get("status") == "input_error", r
        assert any("json parse failed" in e for e in r.get("errors", [])), r
    print("ok: malformed JSON -> input_error with recorded error")


def check_contract():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp = root / "in"; out = root / "out"; work = root / "work"
        inp.mkdir()
        case = {
            "case_id": "selftest-case",
            "output_contract": {"candidates": [], "evidence": [], "status_notes": {}},
        }
        (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        (inp / "task.md").write_text("# t\nchallenge_id: `selftest-case`\n", encoding="utf-8")
        run_ok(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r.get("case_id") == "selftest-case", r
        for k in ("candidates", "evidence", "status_notes"):
            assert k in r, "missing contract field %s" % k

        # phase budget + finalize with findings
        cli.main(["phase", "--work", str(work), "--phase", "1", "--event", "start"])
        cli.main(["phase", "--work", str(work), "--phase", "1", "--event", "end", "--requests", "3"])
        findings = {
            "status": "ok",
            "search_results": [
                {"doi": "10.1000/XYZ", "title": "A Paper", "type": "published", "date": "2026-01-01"},
                {"doi": "10.1000/xyz", "title": "A Paper", "type": "preprint", "date": "2025-11-01"},
            ],
            "tracking": {"snapshot": []},
        }
        fp = work / "findings.json"
        fp.write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
        run_ok(["finalize", "--input", str(inp), "--output", str(out), "--work", str(work),
                "--findings", str(fp)])
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r.get("status") == "ok", r
        dd = r.get("dedup") or {}
        assert dd.get("groups") == 1, dd
        assert dd["items"][0]["merged"] is True, dd
        assert dd["items"][0]["preprint_count"] == 1 and dd["items"][0]["published_count"] == 1, dd
        tr = r.get("tracking") or {}
        assert len(tr.get("new_items")) == 1, tr
        report = (out / "report.md").read_text(encoding="utf-8")
        assert "selftest-case" in report
        # phase budget was recorded and summarized
        assert r["budget"]["summary"]["request_used"].get("1") == 3, r["budget"]
    print("ok: output_contract backfill, dedup merge, increment, budget summary")


def check_normalize_and_dedup_cli():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rec = json.dumps({"DOI": " 10.1000/XYZ. ", "title": "A  Paper!!"})
        norm = json.loads(_capture(["normalize", "--record", rec]))
        assert norm["_canonical_kind"] == "doi", norm
        assert norm["_canonical_key"] == "doi:10.1000/xyz", norm
        records = [
            {"arxiv": "https://arxiv.org/abs/2101.00001v2", "title": "X"},
            {"arxiv": "2101.00001v1", "title": "X"},
            {"title": "No-ID Paper"},
        ]
        rp = root / "records.json"
        rp.write_text(json.dumps(records), encoding="utf-8")
        out = root / "dedup.json"
        run_ok(["dedup", "--records", str(rp), "--out", str(out)])
        dd = json.loads(out.read_text(encoding="utf-8"))
        assert dd["groups"] == 2, dd
        assert dd["items"][0]["merged"] is True, dd
    print("ok: normalize + dedup CLI, DOI/arXiv/title key priority")


def _capture(args_list):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(args_list)
    return buf.getvalue()


def main():
    check_empty_input()
    check_bad_json()
    check_contract()
    check_normalize_and_dedup_cli()
    print("all selftests passed")


if __name__ == "__main__":
    main()
