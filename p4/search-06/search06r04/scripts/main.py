# -*- coding: utf-8 -*-
"""Entry point for the literature-tracking Skill (explicit degradation variant).

Usage (relative to the skill root, e.g. /skills/weekly-skill):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work \\
                                      --findings /app/work/findings.json
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
    python3 scripts/main.py selftest  --work /tmp/skill-selftest

`scaffold` must run in the first minutes: it writes a valid partial result.json
(case_id + every output_contract field + honest status). `run` is scaffold +
deterministic material inventory + finalize with empty findings. `finalize`
merges the agent's findings, enriches them with dedup / version classification
and validation, then writes result.json and report.md.

The scripts never fabricate literature content: missing inputs yield
`insufficient_evidence` or `input_error`, and every gap becomes a typed
`limitation` or `uncertainty` entry, not an invented record.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8-sig") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _report_errors(errors):
    return [{"kind": "error", "message": e} for e in errors]


def _material_inventory(materials, max_bytes=core.MAX_FILE_BYTES):
    """Deterministic inventory: paths + sizes + sha256, skipping oversized files."""
    inventory = []
    degradations = []
    for path in materials:
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            degradations.append({"reason": "read failed", "path": path, "detail": str(exc)})
            continue
        if size > max_bytes:
            degradations.append({"reason": "size_cap", "path": path,
                                 "detail": "%d > %d bytes, skipped" % (size, max_bytes)})
            continue
        try:
            digest = _sha256_file(path)
        except OSError as exc:
            degradations.append({"reason": "read failed", "path": path, "detail": str(exc)})
            continue
        inventory.append({"path": path, "size": size, "sha256": digest})
    return inventory, degradations


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_scaffold(args):
    _ensure_dir(args.output)
    _ensure_dir(args.work)
    case_id, contract, data, errors = core.extract_case(args.input)
    status = core.initial_status(data, errors)
    skeleton = core.make_skeleton(case_id, contract, status)
    skeleton["change_log"] = core.append_change(
        [], "skeleton_written", {"status": status, "contract_keys": contract}
    )
    skeleton["errors"] = _report_errors(errors)
    skeleton["limitations"] = _gap_limitations(data, errors)
    _write_json(os.path.join(args.output, "result.json"), skeleton)
    meta = {"case_id": case_id, "contract_keys": contract,
            "materials": [os.path.basename(m) for m in data["materials"]]}
    _write_json(os.path.join(args.work, "meta.json"), meta)
    print(json.dumps({"case_id": case_id, "status": status,
                      "contract_keys": contract}, ensure_ascii=False))
    return 0


def _gap_limitations(data, errors):
    """Turn discovery gaps into typed limitations, not silent omissions."""
    out = []
    if not data.get("case_obj") and not data.get("task_text"):
        out.append(core.make_limitation_item(
            "lim-input", "case.json / task.md", "no_material",
            "input directory contains neither case.json nor task.md"))
    for e in errors:
        if "json parse failed" in e:
            out.append(core.make_limitation_item(
                "lim-json", "case.json", "parse_failed", e))
        elif "size_cap" in e:
            out.append(core.make_limitation_item(
                "lim-size", "material", "size_cap", e))
        elif "encoding" in e:
            out.append(core.make_limitation_item(
                "lim-enc", "material", "encoding", e))
    return out


def cmd_run(args):
    """scaffold + deterministic inventory + finalize with empty findings."""
    rc = cmd_scaffold(args)
    if rc != 0:
        return rc
    case_id, contract, data, errors = core.extract_case(args.input)
    inventory, degradations = _material_inventory(data["materials"])
    findings = {
        "status": "partial",
        "sources": [{"path": p, "sha256": _sha256_file(p),
                     "size": os.path.getsize(p)} for p in data["materials"]
                    if os.path.isfile(p)],
        "limitations": _gap_limitations(data, errors),
        "degradations": degradations,
        "uncertainty": [core.make_uncertainty_item(
            "unc-analysis", "substantive analysis not yet performed",
            "material", "pending_human",
            affects=["search_results", "ranking", "tracking"],
            basis="run mode only writes a partial skeleton; the agent must fill findings.json")],
        "change_log": core.append_change(
            [], "inventory_written",
            {"material_count": len(data["materials"])}),
    }
    findings_path = os.path.join(args.work, "findings.json")
    _write_json(findings_path, findings)
    return cmd_finalize(args)


def cmd_finalize(args):
    _ensure_dir(args.output)
    case_id, contract, data, errors = core.extract_case(args.input)
    findings = {}
    findings_path = args.findings or os.path.join(args.work, "findings.json")
    if os.path.isfile(findings_path):
        findings, ferrs = core.load_json(findings_path)
        errors.extend(ferrs)
    else:
        findings = {"status": "partial"}
        errors.append("findings file missing: %s" % findings_path)

    # Deterministic enrichment: dedup + version chains + incremental split when
    # the agent provided raw records and a snapshot.
    if isinstance(findings, dict) and isinstance(findings.get("records"), list):
        dedup = core.dedup_records(findings["records"])
        findings.setdefault("dedup", dedup)
        chains = core.build_version_chain(findings["records"])
        findings.setdefault("version_chains", chains)
        snapshot_keys = []
        snap = findings.get("snapshot") or {}
        if isinstance(snap, dict):
            snapshot_keys = snap.get("canonical_ids") or []
        split = core.classify_incremental(findings["records"], snapshot_keys)
        findings.setdefault("incremental_split", {
            "new_count": len(split["new"]),
            "updated_count": len(split["updated"]),
            "removed_count": len(split["removed"]),
            "retraction_count": len(split["retractions"]),
            "known_count": len(split["known"]),
        })

    status = findings.get("status") if isinstance(findings, dict) else "partial"
    if status not in core.STATUS_VALUES:
        status = core.initial_status(data, errors)
    change_log = (findings.get("change_log") if isinstance(findings, dict) else None) or []
    change_log = core.append_change(change_log, "finalize", {"status": status})
    findings["change_log"] = change_log

    result, degradations = core.assemble_result(
        case_id, contract, findings, status=status,
        change_log=change_log,
        runtime={"fingerprint": core.fingerprint(findings)},
    )
    result["errors"] = list(result.get("errors") or []) + _report_errors(errors)
    result["degradations"] = list(result.get("degradations") or []) + [
        {"reason": d, "detail": d} if isinstance(d, str) else d for d in degradations
    ]
    if not result["limitations"]:
        result["limitations"] = _gap_limitations(data, errors)

    _write_json(os.path.join(args.output, "result.json"), result)
    _write_json(os.path.join(args.output, "report.md"),
                render_report(result))
    print(json.dumps({"case_id": case_id, "status": result["status"],
                      "items": _count_items(result)}, ensure_ascii=False))
    return 0


def _count_items(result):
    counts = {}
    for key in ("search_results", "ranking", "evidence", "uncertainty",
                "limitations", "sources", "pending_human"):
        val = result.get(key)
        counts[key] = len(val) if isinstance(val, list) else 0
    return counts


def cmd_dedup(args):
    _ensure_dir(os.path.dirname(args.out) or ".")
    records, errs = core.load_json(args.records)
    if records is None:
        _write_json(args.out, {"items": [], "clusters": {}, "dropped": [],
                               "errors": _report_errors(errs)})
        return 1
    if not isinstance(records, list):
        records = records.get("records") if isinstance(records, dict) else []
    result = core.dedup_records(records)
    result["errors"] = _report_errors(errs)
    _write_json(args.out, result)
    print(json.dumps({"kept": len(result["items"]),
                      "dropped": len(result["dropped"])}, ensure_ascii=False))
    return 0


def cmd_normalize(args):
    try:
        record = json.loads(args.record)
    except ValueError:
        record = {"title": args.record}
    norm = core.normalize_record(record)
    print(json.dumps(norm, ensure_ascii=False, indent=2))
    return 0


def render_report(result):
    """Human-readable report mirroring result.json (no invented content)."""
    lines = ["# 文献发现与追踪报告", ""]
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- 生成依据: 仅材料原文；缺失项显式标注 limitations/uncertainty。")
    lines.append("")
    for section in ("search_results", "ranking", "evidence",
                    "uncertainty", "limitations", "sources", "pending_human"):
        val = result.get(section)
        count = len(val) if isinstance(val, list) else (0 if val is None else 1)
        lines.append("## %s (%d)" % (section, count))
        if isinstance(val, list):
            for item in val:
                lines.append("- " + _one_line(item))
        elif val is not None:
            lines.append("- " + _one_line(val))
        lines.append("")
    st = result.get("status_notes")
    if isinstance(st, dict):
        lines.append("## status_notes")
        lines.append("- empty: %s" % st.get("empty"))
        lines.append("- conflicts: %s" % len(st.get("conflicts") or []))
        lines.append("- permission_errors: %s" % len(st.get("permission_errors") or []))
        lines.append("")
    cl = result.get("change_log")
    if isinstance(cl, list) and cl:
        lines.append("## change_log")
        for e in cl:
            lines.append("- %s %s" % (e.get("at", ""), e.get("event", "")))
        lines.append("")
    return "\n".join(lines)


def _one_line(item):
    if not isinstance(item, dict):
        return str(item)[:160]
    bits = []
    for k in ("id", "title", "claim", "topic", "scope", "reason", "path"):
        if item.get(k) not in (None, ""):
            bits.append("%s=%s" % (k, str(item.get(k))[:80]))
    conf = item.get("confidence")
    if conf:
        bits.append("confidence=%s" % conf)
    return "; ".join(bits) or json.dumps(item, ensure_ascii=False)[:160]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Literature-tracking Skill (explicit degradation & uncertainty variant)",
    )
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scaffold", help="write a valid partial result.json skeleton")
    sub.add_parser("run", help="scaffold + inventory + finalize with empty findings")
    fp = sub.add_parser("finalize", help="merge findings and write result.json + report.md")
    fp.add_argument("--findings", default=None)
    dp = sub.add_parser("dedup", help="deduplicate a records file")
    dp.add_argument("--records", required=True)
    dp.add_argument("--out", required=True)
    np = sub.add_parser("normalize", help="normalize one record and print its key")
    np.add_argument("--record", required=True)
    sp = sub.add_parser("selftest", help="run deterministic robustness self-tests")
    sp.add_argument("--work", default=None)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "scaffold":
        return cmd_scaffold(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "finalize":
        return cmd_finalize(args)
    if args.command == "dedup":
        return cmd_dedup(args)
    if args.command == "normalize":
        return cmd_normalize(args)
    if args.command == "selftest":
        from selftest import run
        return run(args.work)
    return 2


if __name__ == "__main__":
    sys.exit(main())
