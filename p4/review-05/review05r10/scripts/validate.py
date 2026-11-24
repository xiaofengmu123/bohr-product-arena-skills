#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent verifier for the source-bound academic compliance audit.

Checks the result.json contract against the input and the machine evidence,
then verifies every finding's verbatim quote can be re-located in a source
file. Emits FAIL (hard contract violation) and WARN (soft issue) lines; exits
non-zero iff any FAIL is present.

Subcommands
-----------
validate   (default) validate result.json + report.md.
selftest   run robustness checks (empty / bad JSON / normal / merge idempotency
           / quote-traceability negative) in a temp dir.

Standard library only. Imports extract.py from the same directory.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import extract

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
FINDING_REQUIRED = {
    "id", "type", "severity", "category", "title", "file", "locator",
    "quote", "evidence", "basis", "revision",
}
LIST_FIELDS = ("sources", "uncertainty", "change_log", "errors", "degradations", "pending_human")


def _load_json(path):
    if not Path(path).exists():
        return None, f"missing: {path}"
    return extract.read_json_robust(path)


def _text_path_for(file, text_index):
    """Resolve a finding's `file` reference to a text path for quote checks."""
    if not file:
        return None
    want = Path(str(file))
    candidates = []
    for entry in text_index:
        if not entry.get("has_text") or not entry.get("text_path"):
            continue
        candidates.append((entry.get("file", ""), entry["text_path"]))
    for ref, tp in candidates:
        if ref == str(file):
            return tp
    for ref, tp in candidates:
        if Path(ref).name == want.name:
            return tp
    for ref, tp in candidates:
        if Path(ref).stem == want.stem:
            return tp
    return None


def _has_text(file, text_index):
    for entry in text_index:
        if entry.get("file") == str(file) or Path(entry.get("file", "")).name == Path(str(file)).name:
            return entry.get("has_text", False), entry.get("text_path")
    return None, None


def validate_result(result, taxonomy, text_index):
    """Return (fails, warns)."""
    fails, warns = [], []
    if not isinstance(result, dict):
        return ["result.json is not a JSON object"], []
    if not result.get("case_id"):
        fails.append("case_id empty")
    if result.get("status") not in ALLOWED_STATUS:
        fails.append(f"status not in {sorted(ALLOWED_STATUS)}: {result.get('status')!r}")
    for key in LIST_FIELDS:
        if not isinstance(result.get(key), list):
            fails.append(f"{key} missing or not a list")
    if "output_contract" not in result:
        fails.append("output_contract missing")
    report = result.get("report", {})
    if not isinstance(report, dict):
        fails.append("report missing or not an object")
        return fails, warns
    if not isinstance(report.get("findings"), list):
        fails.append("report.findings missing or not a list")
    if not isinstance(report.get("coverage"), dict):
        fails.append("report.coverage missing or not an object")

    facet_names = taxonomy.get("facet_names", [])
    facets_schema = taxonomy.get("facets_schema", {})
    finding_types = taxonomy.get("finding_types", [])
    categories = taxonomy.get("categories", [])
    severities = taxonomy.get("severities", [])

    coverage = report.get("coverage", {})
    for facet in facet_names:
        if facet not in coverage:
            fails.append(f"coverage missing facet: {facet}")

    findings = report.get("findings", [])
    for i, f in enumerate(findings, start=1):
        fid = f.get("id", f"#unknown-{i}")
        for key in FINDING_REQUIRED:
            if key not in f or f.get(key) in (None, ""):
                fails.append(f"finding {fid} missing/empty field: {key}")
        if f.get("type") not in finding_types:
            fails.append(f"finding {fid} bad type: {f.get('type')!r}")
        if f.get("severity") not in severities:
            fails.append(f"finding {fid} bad severity: {f.get('severity')!r}")
        if f.get("category") not in categories:
            fails.append(f"finding {fid} bad category: {f.get('category')!r}")
        # Per-facet typed-finding enforcement.
        schema = facets_schema.get(f.get("category"))
        if isinstance(schema, dict):
            if schema.get("finding_types") and f.get("type") not in schema["finding_types"]:
                fails.append(f"finding {fid} type {f.get('type')!r} not allowed by facet {f.get('category')}")
            for req in (schema.get("evidence_required") or []):
                if not f.get(req):
                    fails.append(f"finding {fid} missing evidence_required field for facet: {req}")
        if "basis_kind" not in f or not f.get("basis_kind"):
            warns.append(f"finding {fid} missing basis_kind (recommended)")

        # Quote traceability.
        quote = f.get("quote")
        if not quote:
            fails.append(f"finding {fid} empty quote")
            continue
        tp = _text_path_for(f.get("file"), text_index)
        if tp is None:
            has_text, _ = _has_text(f.get("file", ""), text_index)
            if has_text is False:
                warns.append(f"finding {fid} quote not verifiable: source has no text layer")
            else:
                fails.append(f"finding {fid} quote not traceable: no text file for {f.get('file')!r}")
            continue
        text, err = extract.read_text_robust(tp)
        if err:
            fails.append(f"finding {fid} text unreadable: {err}")
            continue
        locator, mode, ratio = extract.locate_quote(text, quote)
        if locator is None:
            fails.append(f"finding {fid} quote not traceable in {f.get('file')!r}")
        else:
            f.setdefault("quote_match_mode", mode)
            f.setdefault("quote_match_ratio", ratio)

    # Coverage / status honesty.
    not_checked = [c for c, e in coverage.items() if e.get("status") == "not_checked"]
    pending = [c for c, e in coverage.items() if e.get("status") == "pending_review"]
    if not_checked or pending:
        warns.append(f"facets not fully checked (not_checked={len(not_checked)}, pending={len(pending)})")
    if result.get("pending_human"):
        warns.append(f"{len(result['pending_human'])} pending_human item(s)")
    if result.get("status") == "ok":
        if not_checked or pending or result.get("pending_human"):
            fails.append("status=ok but review is incomplete (unchecked facets or pending_human)")
    if result.get("status") == "input_error" and not result.get("errors"):
        warns.append("status=input_error but errors list is empty")
    return fails, warns


def cmd_validate(inp, out, work):
    inp, out, work = Path(inp), Path(out), Path(work)
    result, err = _load_json(out / "result.json")
    if err:
        print(f"FAIL result.json unreadable: {err}")
        return 1
    evidence, _ = _load_json(work / "evidence.json")
    taxonomy = (evidence or {}).get("taxonomy") or extract.resolve_taxonomy({}, {})
    text_index = (evidence or {}).get("text_index", [])
    fails, warns = validate_result(result, taxonomy, text_index)
    for w in warns:
        print(f"WARN - {w}")
    if not (out / "report.md").exists():
        fails.append("report.md missing")
    if fails:
        print("FAIL")
        for f in fails:
            print(f" - {f}")
        return 1
    print(f"OK status={result.get('status')} "
          f"findings={len(result.get('report', {}).get('findings', []))} "
          f"warns={len(warns)}")
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def cmd_selftest():
    import main
    from shutil import rmtree
    base = Path(tempfile.mkdtemp(prefix="audit-selftest-"))
    try:
        # 1) empty input dir -> insufficient_evidence, exit 0
        empty = base / "empty_in"; empty.mkdir()
        eo, ew = base / "empty_out", base / "empty_work"
        main.cmd_run(empty, eo, ew)
        r1, _ = extract.read_json_robust(eo / "result.json")
        assert r1 and r1.get("status") == "insufficient_evidence", "empty input status"

        # 2) bad JSON case -> input_error, errors recorded
        bad = base / "bad_in"; bad.mkdir()
        (bad / "case.json").write_text("{not json", encoding="utf-8")
        (bad / "mat.txt").write_text("sample text with [1] citation", encoding="utf-8")
        bo, bw = base / "bad_out", base / "bad_work"
        main.cmd_run(bad, bo, bw)
        r2, _ = extract.read_json_robust(bo / "result.json")
        assert r2 and r2.get("status") == "input_error", "bad json status"
        assert r2.get("errors"), "bad json should record an error"

        # 3) normal input -> partial skeleton + observations + contract mirror
        ok = base / "ok_in"; ok.mkdir()
        (ok / "case.json").write_text(json.dumps({
            "case_id": "demo-1",
            "output_contract": {"summary": "s", "items": []},
        }), encoding="utf-8")
        (ok / "mat.txt").write_text("The trial used n = 40 and p < 0.05 [12].", encoding="utf-8")
        oo, ow = base / "ok_out", base / "ok_work"
        main.cmd_run(ok, oo, ow)
        r3, _ = extract.read_json_robust(oo / "result.json")
        assert r3.get("case_id") == "demo-1", "case_id preserved"
        assert r3.get("output_contract", {}).get("summary") is None, "contract defaulted"
        assert "summary" in r3, "contract flattened to top level"
        ev, _ = extract.read_json_robust(ow / "evidence.json")
        assert len(ev.get("observations", [])) >= 1, "observations extracted"

        # 4) merge a valid finding -> status update + traceable quote
        fnd = [{
            "id": "f-1", "type": "fact_error", "severity": "major",
            "category": "statistics", "title": "sample size mismatch",
            "file": "mat.txt", "locator": "mat.txt#L1",
            "quote": "The trial used n = 40 and p < 0.05 [12].",
            "evidence": "text states n=40", "basis": "verbatim quote",
            "revision": "confirm n", "basis_kind": "material_quote",
        }]
        fp = base / "findings.json"
        fp.write_text(json.dumps({"findings": fnd, "status": "partial"}), encoding="utf-8")
        main.cmd_merge(ok, oo, ow, fp)
        r4, _ = extract.read_json_robust(oo / "result.json")
        assert len(r4["report"]["findings"]) == 1, "finding merged"
        assert r4["status"] == "partial", "status partial after merge"

        # 5) idempotent merge -> changed=False, no duplicate change_log
        main.cmd_merge(ok, oo, ow, fp)
        r5, _ = extract.read_json_robust(oo / "result.json")
        assert len(r5["change_log"]) == len(r4["change_log"]), "idempotent merge"

        # 6) validate passes on the merged result
        rc = cmd_validate(ok, oo, ow)
        assert rc == 0, "merged result validates"

        # 7) negative: fake quote must FAIL validation
        fnd2 = [{
            "id": "f-2", "type": "norm_violation", "severity": "minor",
            "category": "citation", "title": "fake", "file": "mat.txt",
            "locator": "mat.txt#L1", "quote": "this text does not exist anywhere",
            "evidence": "none", "basis": "none", "revision": "none",
        }]
        fp.write_text(json.dumps({"findings": fnd2}), encoding="utf-8")
        main.cmd_merge(ok, oo, ow, fp)
        assert cmd_validate(ok, oo, ow) == 1, "fake quote must fail validation"

        print("selftest: OK (empty / bad-json / normal / merge-idempotency / quote-fail)")
        return 0
    except AssertionError as exc:
        print(f"selftest: FAIL — {exc}")
        return 1
    finally:
        rmtree(base, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description="Validate the compliance audit result.json contract.")
    sub = parser.add_subparsers(dest="command")
    p_val = sub.add_parser("validate", help="validate result.json")
    p_val.add_argument("--input", default="/input")
    p_val.add_argument("--output", default="/app/output")
    p_val.add_argument("--work", default="/app/work")
    sub.add_parser("selftest", help="run robustness self-tests")

    args = parser.parse_args(argv)
    if args.command == "selftest":
        return cmd_selftest()
    return cmd_validate(Path(args.input), Path(args.output), Path(args.work))


if __name__ == "__main__":
    sys.exit(main())
