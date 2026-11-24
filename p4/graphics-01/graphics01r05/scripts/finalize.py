#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finalize.py — merge incremental work artifacts into result.json and validate the contract.

Standard library only. Run from the Skill root:

    python scripts/finalize.py --result /app/output/result.json --work-dir /app/work --output-dir /app/output

What it does:
  * loads the existing result.json (run scaffold.py first);
  * merges optional work/*.json lists (provenance, sources, uncertainty, pending_human,
    degradations, errors, editable_source_files, export_files) with de-duplication;
  * validates top-level contract keys, types and the status enum;
  * appends an idempotent ``finalized`` change_log entry;
  * always writes the result back (even if validation fails) so the artifact is preserved,
    and exits 0 only when the contract is satisfied.
"""
import argparse
import json
import os
import sys

from scaffold import RESULT_CONTRACT, ITEM_HINTS, STATUS_ENUM, iso_now, read_text, write_json

MERGEABLE = {
    "editable_source_files": "editable_source_files.json",
    "export_files": "export_files.json",
    "provenance": "provenance.json",
    "sources": "sources.json",
    "uncertainty": "uncertainty.json",
    "pending_human": "pending_human.json",
    "degradations": "degradations.json",
    "errors": "errors.json",
}


def _key(item):
    return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)


def merge_lists(base, extra):
    if not isinstance(base, list):
        base = []
    if not isinstance(extra, list):
        return base
    out = list(base)
    seen = {_key(x) for x in out}
    for item in extra:
        k = _key(item)
        if k not in seen:
            out.append(item)
            seen.add(k)
    return out


def validate(result, errors, warnings):
    for k, spec in RESULT_CONTRACT.items():
        if spec.get("required") and k not in result:
            errors.append("missing required field: %s" % k)
            continue
        if k not in result:
            warnings.append("field absent (should be defaulted): %s" % k)
            continue
        v = result[k]
        t = spec["type"]
        if t == "str" and not isinstance(v, str):
            errors.append("field %r: expected str, got %s" % (k, type(v).__name__))
        elif t == "enum" and (not isinstance(v, str) or v not in spec.get("enum", [])):
            errors.append("field %r: invalid status %r" % (k, v))
        elif t == "list" and not isinstance(v, list):
            errors.append("field %r: expected list" % k)
        elif t == "object" and not isinstance(v, dict):
            errors.append("field %r: expected object" % k)
    if result.get("status") not in STATUS_ENUM:
        errors.append("status %r not in %s" % (result.get("status"), STATUS_ENUM))
    for field, hints in ITEM_HINTS.items():
        items = result.get(field)
        if not isinstance(items, list):
            continue
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                warnings.append("%s[%d] is not an object" % (field, i))
                continue
            for h in hints:
                if h not in it:
                    warnings.append("%s[%d] missing hint key %r" % (field, i, h))


def maybe_advise_status(result, warnings):
    """Never auto-promote; only warn when a partial result looks complete."""
    if result.get("status") != "partial":
        return
    has_source = bool(result.get("editable_source_files"))
    has_export = bool(result.get("export_files"))
    has_prov = bool(result.get("provenance"))
    clean = not result.get("errors") and not result.get("uncertainty")
    if has_source and has_export and has_prov and clean:
        warnings.append("status is still 'partial' but deliverables look complete; "
                        "confirm and set 'complete' explicitly")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--work-dir", default="/app/work")
    a = ap.parse_args(argv)

    if not os.path.exists(a.result):
        print(json.dumps({"ok": False, "error": "result.json missing; run scaffold.py first"}))
        return 2

    text, _enc, note = read_text(a.result)
    if text is None:
        print(json.dumps({"ok": False, "error": "result.json unreadable: %s" % note}))
        return 2
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        print(json.dumps({"ok": False, "error": "result.json is bad JSON: %s" % e}))
        return 2
    if not isinstance(result, dict):
        print(json.dumps({"ok": False, "error": "result.json root is not an object"}))
        return 2

    merged = 0
    for field, fname in MERGEABLE.items():
        p = os.path.join(a.work_dir, fname)
        if not os.path.exists(p):
            continue
        text, _enc, note = read_text(p)
        obj, err = None, "unreadable"
        if text is not None:
            try:
                obj, err = json.loads(text), ""
            except (json.JSONDecodeError, ValueError) as e:
                err = "bad JSON: %s" % e
        if obj is None:
            result.setdefault("errors", []).append(
                {"item": fname, "reason": "work artifact skipped: %s" % err,
                 "handling": "continue with remaining artifacts; check /app/work"})
            continue
        before = len(result.get(field) or []) if isinstance(result.get(field), list) else 0
        result[field] = merge_lists(result.get(field), obj)
        merged += len(result[field]) - before

    # Fill any missing required/default fields (defensive; scaffold already does this).
    from scaffold import default_for
    for k, spec in RESULT_CONTRACT.items():
        result.setdefault(k, default_for(spec))
    result.setdefault("case_id", "unknown")
    result.setdefault("status", "partial")

    errors, warnings = [], []
    validate(result, errors, warnings)
    maybe_advise_status(result, warnings)

    # Idempotent finalize marker.
    final_mark = {"time": iso_now(), "action": "finalized",
                  "detail": "contract validated; work artifacts merged (merged=%d)" % merged}
    if not any(isinstance(x, dict) and x.get("action") == "finalized" and
               x.get("detail") == final_mark["detail"] for x in result.get("change_log", [])):
        result.setdefault("change_log", []).append(final_mark)

    result["runtime"] = dict(result.get("runtime") or {})
    result["runtime"].update({"validated_at": iso_now(), "merge_count": merged,
                              "validation_errors": len(errors),
                              "validation_warnings": len(warnings)})

    # Record validation problems without failing to write the artifact.
    if errors:
        for e in errors:
            entry = {"item": e, "reason": "contract validation failed", "handling": "fix and re-run finalize"}
            if entry not in result.setdefault("errors", []):
                result["errors"].append(entry)

    os.makedirs(a.output_dir, exist_ok=True)
    write_json(a.result, result)
    print(json.dumps({"ok": len(errors) == 0, "case_id": result.get("case_id"),
                      "status": result.get("status"), "merged": merged,
                      "validation_errors": len(errors), "validation_warnings": len(warnings)},
                     ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
