#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check: verify result.json contract and trace every finding quote to source.

Independent checker: it rebuilds the evidence index directly from /input and
/app/work/parsed (not from the possibly-edited evidence.json), so a quote only
passes when it is actually present in the material. Prints [OK]/[WARN]/[FAIL]
lines; exits non-zero if any FAIL remains.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import extract
from main import (
    ALLOWED_STATUS,
    BASIS_KINDS,
    CORE_KEYS,
    DEFAULT_CATEGORIES,
    DEFAULT_SEVERITIES,
    DEFAULT_TYPES,
    FINDING_REQUIRED,
    flatten_leaves,
)

DEFAULT_INPUT = Path("/input")
DEFAULT_OUTPUT = Path("/app/output")
DEFAULT_WORK = Path("/app/work")


class Report:
    def __init__(self):
        self.ok = 0
        self.warn = 0
        self.fail = 0
        self.lines = []

    def add(self, level, key, msg):
        self.lines.append((level, key, msg))
        if level == "OK":
            self.ok += 1
        elif level == "WARN":
            self.warn += 1
        else:
            self.fail += 1

    def dump(self):
        for level, key, msg in self.lines:
            print(f"[{level}] {key}: {msg}")
        print(f"summary: OK={self.ok} WARN={self.warn} FAIL={self.fail}")


def _norm(s: str) -> str:
    return extract.norm(s)


def build_quote_lookup(inp: Path, work: Path):
    """Return (lookup_by_basename, errors). lookup maps basename -> (text, lines)."""
    lookup = {}
    errors = []
    for it in extract.discover(inp):
        if it["role"] in {"material", "task"}:
            text, err = extract.read_text_robust(Path(it["path"]))
            if err:
                errors.append(f"{it['rel']}: {err}")
                continue
            lines = extract.split_lines(text)
            lookup[it["name"]] = (text, lines)
            lookup[it["rel"]] = (text, lines)
    parsed_dir = Path(work) / "parsed"
    if parsed_dir.exists():
        for p in sorted(parsed_dir.rglob("*.txt")):
            text, err = extract.read_text_robust(p)
            if err:
                errors.append(f"{p.name}: {err}")
                continue
            lines = extract.split_lines(text)
            lookup[p.name] = (text, lines)
            # "paper.txt" also serves as proxy for finding.file == "paper.pdf".
            stem = p.stem
            if stem.endswith(".pdf"):
                stem = stem[:-4]
            lookup[stem] = (text, lines)
    return lookup, errors


def find_quote(quote: str, text: str, lines):
    q = _norm(quote)
    if not q:
        return False, "empty quote", None
    for ln in lines:
        if q == ln["t"]:
            return True, f"L{ln['n']}", ln["n"]
    for ln in lines:
        if q in ln["t"]:
            return True, f"L{ln['n']} (substring)", ln["n"]
    joined = _norm(text)
    if q in joined:
        return True, "joined text", None
    return False, None, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--work", default=str(DEFAULT_WORK))
    args = ap.parse_args(argv)

    inp, out, work = Path(args.input), Path(args.output), Path(args.work)
    rep = Report()

    result_path = out / "result.json"
    if not result_path.exists():
        rep.add("FAIL", "result.json", f"missing: {result_path}")
        rep.dump()
        return 1
    result, err = extract.read_json_robust(result_path)
    if err or not isinstance(result, dict):
        rep.add("FAIL", "result.json", f"unparseable: {err}")
        rep.dump()
        return 1

    # 1. Identity and status.
    case_id = result.get("case_id")
    rep.add("OK" if case_id else "FAIL", "case_id", str(case_id or "EMPTY"))
    status = result.get("status")
    rep.add("OK" if status in ALLOWED_STATUS else "FAIL", "status",
            f"{status!r} allowed={sorted(ALLOWED_STATUS)}")

    # 2. Output contract: every original key must survive, flattened leaves present.
    case_obj, _, contract, _, case_errors = _load_case_for_contract(inp)
    oc = result.get("output_contract")
    if contract:
        if not isinstance(oc, dict):
            rep.add("FAIL", "output_contract", "missing or not an object")
        else:
            missing = [k for k in contract if k not in oc]
            rep.add("OK" if not missing else "FAIL", "output_contract.keys",
                    f"missing={missing or 'none'}")
            flat = flatten_leaves(oc)
            missing_top = [k for k in flat if k not in result]
            rep.add("WARN" if missing_top else "OK", "output_contract.flatten",
                    f"top-level missing={missing_top or 'none'}")
    else:
        rep.add("WARN", "output_contract", "not present in case.json (contract_missing should be true)")

    # 3. Ledger arrays.
    for key in ("sources", "uncertainty", "change_log"):
        val = result.get(key)
        ok = isinstance(val, list) and len(val) > 0
        rep.add("OK" if ok else "FAIL", key, f"len={len(val) if isinstance(val, list) else 'n/a'}")

    # 4. report.md present and readable.
    report_path = out / "report.md"
    if not report_path.exists():
        rep.add("FAIL", "report.md", "missing")
    else:
        try:
            report_text = report_path.read_text(encoding="utf-8")
            rep.add("OK" if len(report_text.strip()) > 20 else "FAIL", "report.md",
                    f"{len(report_text)} bytes")
        except OSError as exc:
            rep.add("FAIL", "report.md", str(exc))

    # 5. Finding schema and type/severity/category validity.
    findings = result.get("report", {}).get("findings", [])
    if not isinstance(findings, list):
        rep.add("FAIL", "report.findings", "not a list")
        findings = []
    allowed_types = set(result.get("allowed_types", DEFAULT_TYPES))
    allowed_severities = set(result.get("allowed_severities", DEFAULT_SEVERITIES))
    allowed_categories = set(result.get("allowed_categories", DEFAULT_CATEGORIES))

    # 6. Rebuild source lookup for quote verification.
    lookup, lookup_errors = build_quote_lookup(inp, work)
    for e in lookup_errors:
        rep.add("WARN", "source_index", e)

    for f in findings:
        fid = f.get("id", "?")
        missing_fields = [k for k in FINDING_REQUIRED if k not in f or f.get(k) in (None, "")]
        rep.add("OK" if not missing_fields else "FAIL", f"finding.{fid}.fields",
                f"missing={missing_fields or 'none'}")
        if f.get("type") not in allowed_types:
            rep.add("FAIL", f"finding.{fid}.type", f"unknown type {f.get('type')!r}")
        if f.get("severity") not in allowed_severities:
            rep.add("FAIL", f"finding.{fid}.severity", f"unknown severity {f.get('severity')!r}")
        if f.get("category") not in allowed_categories:
            rep.add("WARN", f"finding.{fid}.category", f"unlisted category {f.get('category')!r}")
        bk = f.get("basis_kind")
        if bk not in BASIS_KINDS:
            rep.add("WARN", f"finding.{fid}.basis_kind", f"unknown basis_kind {bk!r}")
        if bk == "normative_clause" and not (f.get("basis") and f.get("basis_source")):
            rep.add("FAIL", f"finding.{fid}.basis",
                    "normative_clause requires basis text AND basis_source")

        # Quote verification.
        quote = f.get("quote", "")
        ffile = str(f.get("file", ""))
        candidates = [ffile, Path(ffile).name]
        text_lines = None
        for cand in candidates:
            if cand in lookup:
                text_lines = lookup[cand]
                break
        if text_lines is None:
            rep.add("WARN", f"finding.{fid}.quote", f"source not found for file={ffile!r}")
        else:
            found, loc, line_no = find_quote(quote, text_lines[0], text_lines[1])
            rep.add("OK" if found else "FAIL", f"finding.{fid}.quote",
                    f"verbatim {'found @ ' + loc if found else 'NOT FOUND'}")
            if found and f.get("locator"):
                m = re.search(r"#L(\d+)", str(f.get("locator")))
                if m and line_no is not None and int(m.group(1)) != line_no:
                    rep.add("WARN", f"finding.{fid}.locator",
                            f"locator says L{m.group(1)} but quote found at L{line_no}")

    # 7. Honest status vs. evidence.
    if not findings and status == "ok":
        rep.add("FAIL", "status.honesty", "status=ok but no findings were recorded")
    if not lookup and status not in {"insufficient_evidence", "input_error"}:
        rep.add("WARN", "status.honesty", "no readable source text yet status is not insufficient_evidence")

    rep.dump()
    return 1 if rep.fail else 0


def _load_case_for_contract(inp: Path):
    """Lightweight contract loader for validation (no dependency on main.load_case)."""
    files = extract.discover(inp)
    case_item = next((it for it in files if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, {}, ["/input/case.json not found"]
    obj, err = extract.read_json_robust(Path(case_item["path"]))
    if err or not isinstance(obj, dict):
        return None, "unknown", {}, {}, [err or "not an object"]
    contract = obj.get("output_contract")
    contract = contract if isinstance(contract, dict) else {}
    return obj, obj.get("case_id") or obj.get("id") or "unknown", contract, {}, []


if __name__ == "__main__":
    raise SystemExit(main())
