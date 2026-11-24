#!/usr/bin/env python3
"""Early partial-result scaffold.

Creates /app/output/result.json with case_id, all output_contract fields from
case.json, and reserved fields (status/sources/uncertainty/pending_human/
change_log/intermediates). Idempotent: if the file exists, only missing keys are
added and the change_log is appended; populated values are never overwritten.

Standard library only. Exit codes: 0 = ok, 2 = hard failure (unparseable case).
"""
import argparse
import datetime as _dt
import json
import os
import sys

FALLBACK_CONTRACT = {
    "deliverable": None,
    "fact_source_map": [],
    "version_boundary": {
        "diff": [],
        "human_review_required": [],
        "material_gaps": [],
        "exceptions": [],
    },
}

RESERVED = {
    "status": "partial",
    "sources": [],
    "uncertainty": {
        "claims_requiring_confirmation": [],
        "conflicting_evidence": [],
        "material_gaps": [],
        "limitations": [],
    },
    "pending_human": [],
    "change_log": [],
    "intermediates": {
        "format_spec": None,
        "content_units": None,
        "plan": None,
        "fidelity_report": None,
    },
}


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def load_json(path):
    """Read JSON tolerantly (utf-8-sig, BOM, fallback encodings)."""
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                raw = f.read()
            break
        except (UnicodeDecodeError, OSError):
            continue
    if raw is None:
        raise OSError("cannot read %s" % path)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try stripping a leading BOM byte manually.
        if raw.startswith("\ufeff"):
            return json.loads(raw[1:])
        raise


def case_id_from(data, case_path):
    for key in ("case_id", "caseId", "caseid"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # Some cases reuse the top-level "id" as the case identifier.
    v = data.get("id")
    if isinstance(v, str) and v.strip():
        return v.strip()
    base = os.path.basename(case_path)
    if base.lower().endswith(".json"):
        base = base[:-5]
    return "unknown:" + base


def contract_fields(data):
    oc = data.get("output_contract")
    fields = {}
    if isinstance(oc, dict) and oc:
        for key in oc.keys():
            if isinstance(key, str) and key.strip():
                fields[key.strip()] = None
    else:
        fields = dict(FALLBACK_CONTRACT)
    return fields


def merge(existing, incoming):
    """Deep-merge incoming into existing; existing non-null values win."""
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return incoming
    for k, v in incoming.items():
        if k not in existing or existing[k] is None:
            existing[k] = v
        elif isinstance(existing[k], dict) and isinstance(v, dict):
            existing[k] = merge(existing[k], v)
        # lists/strings/numbers already present are kept as-is
    return existing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    result_path = os.path.join(a.out, "result.json")

    try:
        data = load_json(a.case)
    except (OSError, json.JSONDecodeError) as e:
        # Still emit a minimal valid result so no later step sees an empty dir.
        data = {}
        skeleton = {
            "case_id": "unknown",
            "status": "failed",
            "sources": [],
            "uncertainty": RESERVED["uncertainty"],
            "pending_human": [{"item": "case.json 不可解析", "reason": str(e),
                               "suggested_action": "检查 /input/case.json 是否合法 JSON"}],
            "change_log": [{"ts": _now(), "action": "scaffold_failure", "detail": str(e)}],
            "intermediates": dict(RESERVED["intermediates"]),
        }
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(skeleton, f, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": False, "case_id": "unknown", "status": "failed"},
                         ensure_ascii=False))
        return 2

    case_id = case_id_from(data, a.case)
    contract = contract_fields(data)

    skeleton = {"case_id": case_id}
    for k, v in contract.items():
        skeleton[k] = v
    skeleton.update(RESERVED)
    skeleton["change_log"] = [{"ts": _now(), "action": "scaffold",
                               "detail": "骨架落盘 status=partial，契约字段已铺平"}]

    if os.path.exists(result_path):
        try:
            prev = load_json(result_path)
        except (OSError, json.JSONDecodeError):
            prev = {}
        # Reconcile case_id only if previously unknown; never silently rename.
        if isinstance(prev, dict) and (not prev.get("case_id") or
                                       str(prev.get("case_id")).startswith("unknown:")):
            prev["case_id"] = case_id
        merged = merge(prev, skeleton)
        log = merged.setdefault("change_log", [])
        if isinstance(log, list):
            log.append({"ts": _now(), "action": "scaffold_merge",
                        "detail": "增量合并，保留已填内容"})
        merged["status"] = merged.get("status") or "partial"
        final = merged
    else:
        final = skeleton

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "case_id": case_id, "status": final.get("status")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
