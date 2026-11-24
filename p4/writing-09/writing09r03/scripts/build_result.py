#!/usr/bin/env python3
"""Deterministically build and incrementally merge /app/output/result.json.

Two modes:

  --init   create the honest partial skeleton: case_id + every output_contract
           field (null/empty placeholder, original names preserved) + the
           standard trace fields status/sources/uncertainty/pending_human/
           change_log.
  --patch  deep-merge a patch JSON into the existing result.json.

Determinism contract: output is serialized with sorted keys, ensure_ascii=False,
indent=2, and a trailing newline. The skeleton is a pure function of the input
summary, so running --init twice on the same summary yields byte-identical
files. change_log entries carry a monotonic `step` and the input `fingerprint`,
never wall-clock time, so repeat runs compare equal.

Standard library only.
"""
import argparse
import json
import os
import sys

VALID_STATUS = {
    "ok", "partial", "complete", "failed",
    "insufficient_evidence", "input_error", "in_progress",
}

DEFAULT_CONTRACT = {
    "deliverable": None,
    "facts_sources": [],
    "version_boundaries": {},
}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except OSError as exc:
        raise SystemExit("cannot read %s: %s" % (path, exc))
    except ValueError as exc:
        raise SystemExit("bad JSON in %s: %s" % (path, exc))


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def placeholder_for(value):
    """Keep structural shape, erase content, so contract field names/types
    are preserved without inventing any data."""
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    return None


def contract_fields(contract):
    if isinstance(contract, dict) and contract:
        return {k: placeholder_for(v) for k, v in contract.items()}
    if isinstance(contract, list):
        return {name: None for name in contract}
    return dict(DEFAULT_CONTRACT)


def build_skeleton(summary_path, result_path):
    summary = load_json(summary_path)
    case_id = summary.get("case_id")
    contract = summary.get("output_contract") or {}
    fingerprint = summary.get("run_fingerprint")

    result = {"case_id": case_id}
    result.update(contract_fields(contract))
    result["status"] = "partial"
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{
        "step": 0,
        "action": "skeleton_created",
        "fingerprint": fingerprint,
    }]

    os.makedirs(os.path.dirname(os.path.abspath(result_path)), exist_ok=True)
    dump_json(result, result_path)
    return result


def deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            out[k] = deep_merge(base.get(k), v) if k in base else v
        return out
    return patch


def apply_patch(result_path, patch_path):
    result = load_json(result_path)
    patch = load_json(patch_path)
    merged = deep_merge(result, patch)
    dump_json(merged, result_path)
    return merged


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", default="/app/work/input_summary.json")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--patch", default=None,
                    help="path to a patch JSON to deep-merge (uses --result)")
    ap.add_argument("--init", action="store_true",
                    help="create the partial skeleton (default if no --patch)")
    a = ap.parse_args(argv)

    if a.patch:
        merged = apply_patch(a.result, a.patch)
        print(json.dumps({"ok": True, "action": "patch", "status": merged.get("status")},
                         ensure_ascii=False, sort_keys=True))
    else:
        result = build_skeleton(a.summary, a.result)
        print(json.dumps({"ok": True, "action": "init", "status": result.get("status")},
                         ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
