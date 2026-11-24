#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a result.json against the skill's output contract.

Standard library only. Exit code 0 = contract satisfied (or only advisory
warnings); exit code 1 = hard failure (missing/invalid case_id or status).
This script never modifies the result file; it only reports.

Usage:
    python3 scripts/validate.py --output /app/output
"""

import argparse
import json
import sys
from pathlib import Path

# Canonical result contract. Keep in sync with SKILL.md "输出契约" section.
RESULT_CONTRACT = {
    "case_id": str,            # required non-empty identifier
    "status": str,             # ok | partial | insufficient_evidence | input_error
    "deliverables": list,      # [{path, type, description}]
    "evidence_map": list,      # [{locator, quote, claim, type, status}]
    "sources": list,           # [{path, size_bytes, role}]
    "uncertainty": dict,       # {claim_strength_note, unresolved[]}
    "change_log": list,        # [{id, paragraph, action, before, after, reason}]
    "pending_human": list,     # items needing human confirmation
    "material_gaps": list,     # missing materials / unsupported claims
    "errors": list,
    "degradations": list,
    "runtime": dict,           # {start, end, seconds, stage}
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}


def load_result(path):
    p = Path(path)
    if not p.exists():
        return None, "missing_file: {}".format(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return None, "unparseable_json: {}".format(exc)
    if not isinstance(data, dict):
        return None, "result_root_not_object"
    return data, None


def validate(output_dir):
    result_path = Path(output_dir) / "result.json"
    data, err = load_result(result_path)
    if err:
        return 1, [err], []

    errors, warnings = [], []

    # Hard requirements
    case_id = data.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        errors.append("case_id_missing_or_empty")
    status = data.get("status")
    if status not in STATUSES:
        errors.append("invalid_status: {}".format(repr(status)))

    # Contract keys
    for key, typ in RESULT_CONTRACT.items():
        if key not in data:
            warnings.append("missing_key: {} (will be null/[] per contract)".format(key))
        elif not isinstance(data[key], typ):
            warnings.append("wrong_type: {} expected {}".format(key, typ.__name__))

    # Evidence entries should carry a locator and a verbatim quote.
    emap = data.get("evidence_map", [])
    if isinstance(emap, list):
        for i, e in enumerate(emap):
            if not isinstance(e, dict):
                warnings.append("evidence_map[{}]: not an object".format(i))
            elif not e.get("quote"):
                warnings.append("evidence_map[{}]: missing quote".format(i))
            elif not e.get("locator"):
                warnings.append("evidence_map[{}]: missing locator".format(i))

    # Deliverable paths should exist on disk (editable artifacts).
    for i, d in enumerate(data.get("deliverables", []) or []):
        if isinstance(d, dict) and d.get("path"):
            ap = (Path(output_dir) / d["path"])
            if not ap.exists():
                warnings.append("deliverable_missing_on_disk: {}".format(d["path"]))

    if errors:
        return 1, errors, warnings
    return 0, errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate result.json contract.")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args(argv)

    code, errors, warnings = validate(args.output)
    print("validate result.json in:", args.output)
    print("hard_errors:", json.dumps(errors, ensure_ascii=False))
    print("warnings:", json.dumps(warnings, ensure_ascii=False))
    print("exit_code:", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
