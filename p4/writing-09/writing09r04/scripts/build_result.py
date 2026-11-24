#!/usr/bin/env python3
"""Create and incrementally update /app/output/result.json.

Usage:
  # 1) idempotent skeleton (the first honest partial result)
  python scripts/build_result.py --summary /app/work/input_summary.json --output-dir /app/output

  # 2) merge a patch object (deep merge for dicts, replace for scalars/lists)
  python scripts/build_result.py --summary ... --output-dir ... --patch /app/work/patch.json

  # 3) set scalar fields and append change_log entries
  python scripts/build_result.py --summary ... --output-dir ... \
      --set status=partial --set 'deliverable.title=Results and Discussion' \
      --log "filled deliverable outline"

Guarantees:
  - result.json always contains case_id + every output_contract field, plus the
    envelope keys status/sources/uncertainty/pending_human/limitations/change_log.
  - a missing or empty summary still yields a valid skeleton (status defaults to
    insufficient_evidence when no case_id is available).
  - every run appends an honest change_log entry, so provenance is auditable.

Standard library only.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time

ALLOWED_STATUS = {"in_progress", "partial", "complete", "insufficient_evidence", "failed"}

DEFAULT_CONTRACT = {
    "deliverable": {"title": "", "files": [], "summary": ""},
    "facts_sources": [],
    "version_boundaries": {
        "diff_summary": "",
        "pending_human": [],
        "material_gaps": [],
        "anomalies": [],
        "limitations": [],
    },
}

ENVELOPE_DEFAULTS = {
    "status": "in_progress",
    "sources": [],
    "uncertainty": [],
    "pending_human": [],
    "limitations": [],
    "change_log": [],
    "runtime": {
        "started_at": None,
        "finished_at": None,
        "duration_s": None,
        "model_requests": None,
    },
}


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_summary(path):
    """Load input_summary.json tolerantly; empty dict on failure."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def read_result(path):
    """Load existing result.json tolerantly; None when absent or corrupt."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        return None


def make_skeleton(case_id, contract):
    """Fresh skeleton honoring the contract exactly, then the envelope."""
    result = {"case_id": case_id}
    for key, val in contract.items():
        result[key] = copy.deepcopy(val)
    for key, val in ENVELOPE_DEFAULTS.items():
        result.setdefault(key, copy.deepcopy(val))
    if result.get("runtime", {}).get("started_at") is None:
        result.setdefault("runtime", {})["started_at"] = now_utc()
    if not case_id:
        result["status"] = "insufficient_evidence"
    return result


def deep_merge(base, patch):
    """Merge patch into base in place: dicts recurse, everything else replaces."""
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], val)
        else:
            base[key] = copy.deepcopy(val)
    return base


def set_dotted(obj, dotted, value):
    """Set obj['a']['b'] from 'a.b'; create intermediate dicts as needed."""
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        return
    cur = obj
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default="/app/work/input_summary.json")
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--patch")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--log", action="append", default=[])
    a = ap.parse_args(argv)

    os.makedirs(a.output_dir, exist_ok=True)
    result_path = os.path.join(a.output_dir, "result.json")

    summary = read_summary(a.summary)
    case_id = summary.get("case_id") or ""
    contract = summary.get("output_contract") or DEFAULT_CONTRACT
    if not isinstance(contract, dict):
        contract = DEFAULT_CONTRACT

    result = read_result(result_path)
    if result is None:
        result = make_skeleton(case_id, contract)

    # Re-assert every contract field so a late contract change cannot drop keys.
    for key, val in contract.items():
        result.setdefault(key, copy.deepcopy(val))

    actions = []

    if a.patch:
        if os.path.isfile(a.patch):
            try:
                with open(a.patch, "r", encoding="utf-8-sig", errors="replace") as fh:
                    patch = json.load(fh)
                if isinstance(patch, dict):
                    deep_merge(result, patch)
                    actions.append("merged patch %s" % a.patch)
                else:
                    actions.append("patch ignored (not an object): %s" % a.patch)
            except Exception as exc:  # noqa: BLE001
                actions.append("patch failed: %s" % exc)
        else:
            actions.append("patch missing: %s" % a.patch)

    for item in a.set:
        if "=" not in item:
            actions.append("ignored malformed --set: %s" % item)
            continue
        key, _, raw = item.partition("=")
        try:
            value = json.loads(raw)
        except Exception:  # noqa: BLE001 - keep raw string for non-JSON values
            value = raw
        set_dotted(result, key.strip(), value)
        actions.append("set %s=%s" % (key.strip(), raw))

    for msg in a.log:
        result.setdefault("change_log", []).append({
            "ts": now_utc(),
            "action": "manual",
            "detail": msg,
        })
        actions.append("log: %s" % msg)

    # Re-assert the envelope keys and an honest status.
    for key, val in ENVELOPE_DEFAULTS.items():
        result.setdefault(key, copy.deepcopy(val))
    if not result.get("case_id"):
        result["status"] = "insufficient_evidence"
    if result.get("status") not in ALLOWED_STATUS:
        result["status"] = "partial"

    result.setdefault("change_log", []).append({
        "ts": now_utc(),
        "action": "build_result",
        "detail": "; ".join(actions) if actions else "skeleton ensured",
    })

    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "result": result_path,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "actions": actions,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
