#!/usr/bin/env python3
"""Self-check for the academic-format-adaptation skill.

Verifies the ``/app/output/result.json`` contract and the traceability of
evidence records (locator + verbatim quote). Produces
``/app/output/verification_report.json`` and exits:

- 0  : no failures (warnings allowed)
- 1  : warnings only (advisory issues worth fixing)
- 2  : hard failure (missing case_id, invalid JSON, contract fields absent,
       reserved fields absent, or an illegal status)

Only the Python standard library is used.
"""
import argparse
import json
import os
import re
import sys

DEFAULT_RESULT = "/app/output/result.json"
DEFAULT_CASE = "/input/case.json"
DEFAULT_REPORT = "/app/output/verification_report.json"

RESERVED = ("status", "sources", "uncertainty", "change_log")
ALLOWED_STATUS = {"partial", "complete", "failed", "insufficient_evidence", "input_error"}
FALLBACK_CONTRACT_KEYS = ("deliverable", "fact_source_map", "version_boundary")
TEXT_EXTENSIONS = {".md", ".txt", ".json", ".jsonl", ".tex", ".csv", ".html", ".xml", ".yaml", ".yml"}
PLACEHOLDER_PATTERN = re.compile(r"\b(TODO|FIXME|XXX|TBD|PLACEHOLDER|占位|待补|待填)\b", re.IGNORECASE)
QUOTE_MIN_LEN = 4  # short quotes (e.g. "是") are treated as anchors, not failures


def read_json(path):
    if not path or not os.path.exists(path):
        return None, "missing_file"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error:%s" % exc
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return None, "undecodable"
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "bad_json:%s" % exc


def discover_case_id(data):
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def normalize(text):
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def collect_evidence_records(node, out, path="$"):
    """Recursively collect dict entries that look like evidence records.

    A record qualifies when it carries a ``quote`` or both ``locator`` and a
    claim text (``claim_text``/``claim_id``/``spec_text``).
    """
    if isinstance(node, dict):
        has_quote = isinstance(node.get("quote"), str)
        has_locator = isinstance(node.get("locator"), str)
        has_claim = any(isinstance(node.get(k), str) and node.get(k).strip()
                        for k in ("claim_text", "claim_id", "spec_text", "statement", "text"))
        if has_quote or (has_locator and has_claim):
            out.append((path, node))
        for key, value in node.items():
            collect_evidence_records(value, out, "%s.%s" % (path, key))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            collect_evidence_records(item, out, "%s[%d]" % (path, idx))


def iter_material_text_files(roots):
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext in TEXT_EXTENSIONS:
                    yield os.path.join(dirpath, name)


def locate_quote(quote, roots):
    """Return the first material file that contains the normalized quote."""
    needle = normalize(quote)
    if not needle:
        return None
    for path in iter_material_text_files(roots):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            continue
        if needle in normalize(text):
            return path
    return None


def scan_placeholders(result):
    text = json.dumps(result, ensure_ascii=False)
    hits = []
    for match in PLACEHOLDER_PATTERN.finditer(text):
        hits.append(match.group(0))
    return hits


def check_artifact_paths(result):
    missing = []
    for key in ("deliverable", "artifact_path", "artifact_paths", "preview_path", "output_path"):
        value = result.get(key)
        if isinstance(value, str) and value and not os.path.exists(value):
            missing.append((key, value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item and not os.path.exists(item):
                    missing.append((key, item))
        elif isinstance(value, dict):
            for sub in value.values():
                if isinstance(sub, str) and sub and not os.path.exists(sub):
                    missing.append((key, sub))
    return missing


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate result.json contract and evidence traceability")
    ap.add_argument("--result", default=os.environ.get("SKILL_RESULT", DEFAULT_RESULT))
    ap.add_argument("--case", default=os.environ.get("SKILL_CASE", DEFAULT_CASE))
    ap.add_argument("--report", default=os.environ.get("SKILL_REPORT", DEFAULT_REPORT))
    ap.add_argument("--material-root", action="append", default=None,
                    help="root to search for quote locatability; repeatable")
    a = ap.parse_args(argv)

    roots = a.material_root or [DEFAULT_CASE.rsplit("/", 1)[0] if "/" in DEFAULT_CASE else "/input",
                                "/app/work"]

    failures = []
    warnings = []
    checks = []

    result, result_err = read_json(a.result)
    if result_err:
        failures.append("result_json:%s" % result_err)
    else:
        checks.append("result.json parsed")

    case_data, case_err = read_json(a.case)

    if result_err is None:
        # 1. case_id
        case_id = result.get("case_id")
        if not case_id:
            failures.append("missing case_id in result.json")
        else:
            checks.append("case_id present: %s" % case_id)
            if case_data is not None:
                expected_id = discover_case_id(case_data)
                if expected_id and expected_id != case_id:
                    failures.append("case_id mismatch: result=%s case=%s" % (case_id, expected_id))
                else:
                    checks.append("case_id matches case.json")

        # 2. contract fields
        contract_fields = None
        if isinstance(case_data, dict):
            contract = case_data.get("output_contract")
            if isinstance(contract, dict):
                contract_fields = list(contract.keys())
            elif isinstance(contract, list):
                contract_fields = [x if isinstance(x, str) else x.get("key")
                                   for x in contract if isinstance(x, (str, dict))]
            else:
                contract_fields = [k for k in FALLBACK_CONTRACT_KEYS if case_data.get(k) is not None]
        if contract_fields is None:
            contract_fields = FALLBACK_CONTRACT_KEYS
        missing_contract = [k for k in contract_fields if k not in result]
        if missing_contract:
            failures.append("missing output_contract fields: %s" % ", ".join(missing_contract))
        else:
            checks.append("output_contract fields present: %s" % ", ".join(contract_fields))

        # 3. reserved fields
        missing_reserved = [k for k in RESERVED if k not in result]
        if missing_reserved:
            failures.append("missing reserved fields: %s" % ", ".join(missing_reserved))
        else:
            checks.append("reserved fields present: %s" % ", ".join(RESERVED))

        # 4. status
        status = result.get("status")
        if status not in ALLOWED_STATUS:
            failures.append("illegal status: %r" % status)
        else:
            checks.append("status=%s" % status)

        # 5. evidence records
        records = []
        collect_evidence_records(result, records)
        if records:
            incomplete = []
            for path, rec in records:
                for key in ("quote", "locator"):
                    if not isinstance(rec.get(key), str) or not rec[key].strip():
                        incomplete.append("%s.%s" % (path, key))
            if incomplete:
                warnings.append("evidence records missing quote/locator: %d/%d (e.g. %s)" %
                                (len(incomplete), len(records), incomplete[0]))
            else:
                checks.append("evidence records all carry quote+locator: %d records" % len(records))

            # 6. quote locatability
            unlocated = []
            for path, rec in records:
                quote = rec.get("quote")
                locator = rec.get("locator")
                if isinstance(quote, str) and len(normalize(quote)) >= QUOTE_MIN_LEN:
                    found = locate_quote(quote, roots)
                    if not found:
                        unlocated.append((path, locator or rec.get("source_ref") or "?"))
            if unlocated:
                warnings.append("quotes not found in material roots: %d (e.g. %s)" %
                                (len(unlocated), unlocated[0][0]))
            else:
                checks.append("all %d quotes located in material roots" % len(records))
        else:
            warnings.append("no evidence records found (evidence_map/fact_source_map empty)")

        # 7. placeholders
        ph = scan_placeholders(result)
        if ph:
            warnings.append("placeholder markers found: %s" % ", ".join(sorted(set(ph))))
        else:
            checks.append("no placeholder markers")

        # 8. artifacts
        missing_artifacts = check_artifact_paths(result)
        if missing_artifacts:
            warnings.append("artifact paths missing on disk: %d (e.g. %s)" %
                            (len(missing_artifacts), missing_artifacts[0][0]))
        else:
            checks.append("no dangling artifact paths (or no paths declared)")

        # 9. change_log shape
        cl = result.get("change_log")
        if not isinstance(cl, list) or not cl:
            warnings.append("change_log empty or not a list")
        else:
            bad = [i for i, e in enumerate(cl)
                   if not isinstance(e, dict) or not e.get("ts") or not e.get("action")]
            if bad:
                warnings.append("change_log entries missing ts/action at indices %s" % bad[:3])
            else:
                checks.append("change_log well-formed (%d entries)" % len(cl))

    report = {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "checks": checks,
        "evidence_records": len(records) if result_err is None else 0,
        "status": "pass" if not failures else ("warn" if not failures else "fail"),
        "result_path": a.result,
        "material_roots": roots,
    }
    report["status"] = "fail" if failures else ("warn" if warnings else "pass")

    try:
        out_dir = os.path.dirname(a.report) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        return 2
    if warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
