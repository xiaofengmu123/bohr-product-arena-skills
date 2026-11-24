#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — sci-library-organizer 编排入口（仅标准库）。

承诺：启动即写 result.json 骨架；绝不改 /input；无证据字段 not_reported。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

__version__ = "1.0.0"

DEFAULT_CONTRACT = {"library": {}, "evidence": {}, "operations": [], "failures": {}}
EXTRAS = ("status", "sources", "uncertainty", "limitations", "change_log",
          "errors", "degradations", "pending_human", "runtime")


def deep_placeholder(v):
    if isinstance(v, dict):
        return {k: deep_placeholder(x) for k, x in v.items()}
    if isinstance(v, list):
        return []
    return None


def build_result(case_id, contract, data):
    result = {"case_id": case_id}
    for key in contract:
        result[key] = data.get(key) if key in data else deep_placeholder(contract[key])
    for extra in EXTRAS:
        result.setdefault(extra, data.get(extra))
    return result


def read_case_context(input_dir, task_arg):
    case = core.read_json(os.path.join(input_dir, "case.json"))
    case_id = "unknown"
    contract = None
    if isinstance(case, dict):
        case_id = (case.get("case_id") or case.get("id")
                   or case.get("challenge_id") or "unknown")
        contract = (case.get("output_contract") or case.get("outputContract")
                    or case.get("output_schema") or case.get("result_contract")
                    or case.get("contract"))
    if not isinstance(contract, dict) or not contract:
        contract = DEFAULT_CONTRACT
    return case_id, contract, case


def empty_data():
    return {
        "library": {"catalog": [], "hierarchy": {}, "metadata": {}, "versions": []},
        "evidence": {"hashes": {}, "permissions": {}, "locators": []},
        "operations": [],
        "failures": {"skipped": [], "conflicts": [], "permission_errors": [],
                     "duplicates": [], "recoveries": []},
        "status": "partial", "sources": [], "uncertainty": {}, "limitations": [],
        "change_log": [], "errors": [], "degradations": [], "pending_human": [],
        "runtime": {},
    }


def log_event(change_log, msg):
    change_log.append({"ts": core.utc_now_iso(), "event": msg})


def compute_uncertainty(catalog):
    unclassified = [s for s, e in catalog.items() if e["category"] in (None, "unknown")]
    no_text = [s for s, e in catalog.items() if e.get("has_text_layer") is False]
    missing = {}
    for s, e in catalog.items():
        for f, c in e.get("metadata", {}).items():
            if isinstance(c, dict) and c.get("confidence") == "not_reported":
                missing.setdefault(f, []).append(s)
    return {
        "unclassified_count": len(unclassified), "unclassified": sorted(unclassified),
        "no_text_layer_count": len(no_text), "no_text_layer": sorted(no_text),
        "missing_field_counts": {k: len(v) for k, v in sorted(missing.items())},
        "missing_field_detail": {k: sorted(v) for k, v in sorted(missing.items())},
        "source_conflicts": [],
        "note": "缺失字段如实标注 not_reported，未虚构作者/年份/DOI。",
    }


def compute_limitations(inv, catalog, degradations):
    lim = []
    if inv.get("error"):
        lim.append({"scope": "input", "detail": inv["error"]})
    for s, e in catalog.items():
        if e.get("encrypted"):
            lim.append({"source": s, "detail": "encrypted PDF", "impact": "metadata not_reported"})
        elif e.get("has_text_layer") is False:
            lim.append({"source": s, "detail": "no text layer", "impact": "metadata from filename only"})
    for d in degradations:
        lim.append(d)
    return lim


def compute_failures(inv, catalog, rollback):
    skipped, conflicts, perm_errors, duplicates, recoveries = [], [], [], [], []
    for f in inv.get("files", []):
        perm = f.get("permissions") or {}
        if not perm.get("stat_ok", True) or perm.get("readable") is False:
            perm_errors.append({"source": f["source"], "error": "unreadable", "permissions": perm})
        for msg in f.get("errors", []):
            skipped.append({"source": f["source"], "errors": msg})
    for src, e in sorted(catalog.items()):
        if e.get("duplicate_of"):
            duplicates.append({"source": src, "duplicate_of": e["duplicate_of"], "sha256": e["sha256"]})
    name_map = {}
    for src, e in sorted(catalog.items()):
        if e.get("keep"):
            key = (e.get("category_path"), e.get("suggested_name"))
            name_map.setdefault(key, []).append(src)
    for key, members in name_map.items():
        distinct = {catalog[m]["sha256"] for m in members}
        if len(members) > 1 and len(distinct) > 1:
            conflicts.append({"target": "/".join(str(x) for x in key if x), "sources": members,
                              "note": "same target name, different content; -N suffix"})
    recoveries = list(rollback) if rollback else [{"note": "dry-run: nothing to roll back"}]
    return skipped, conflicts, perm_errors, duplicates, recoveries


def build_versions(catalog):
    groups = {}
    for src, e in sorted(catalog.items()):
        gid = e.get("version_group")
        if gid:
            groups.setdefault(gid, {"group_id": gid, "members": [], "basis": []})
            groups[gid]["members"].append(src)
            groups[gid]["basis"] = e.get("version_group_basis", [])
    out = []
    for gid in sorted(groups):
        g = groups[gid]
        g["members"] = sorted(g["members"])
        g["basis"] = sorted(set(g["basis"]))
        g["distinct_roles"] = sorted({catalog[m].get("version_role", "unknown") for m in g["members"]})
        out.append(g)
    return out


def build_library(catalog, dims):
    return {
        "catalog": [catalog[s] for s in sorted(catalog)],
        "hierarchy": {"dimensions": dims,
                      "paths": sorted({e["category_path"] for e in catalog.values() if e.get("category_path")})},
        "metadata": {"files": len(catalog),
                     "fields_available": sorted({f for e in catalog.values()
                                                 for f, c in e.get("metadata", {}).items()
                                                 if isinstance(c, dict) and c.get("confidence") != "not_reported"})},
        "versions": build_versions(catalog),
    }


def build_evidence(inv, catalog):
    return {
        "hashes": {e["source"]: e["sha256"] for e in catalog.values()},
        "permissions": {f["source"]: f.get("permissions", {}) for f in inv.get("files", [])},
        "locators": sorted(
            [{"source": e["source"], "field": f, "locator": ev["locator"], "quote": ev["quote"]}
             for e in catalog.values() for f, c in e.get("metadata", {}).items()
             if isinstance(c, dict) for ev in c.get("evidence", [])],
            key=lambda x: (x["source"], x["field"])),
    }


def apply_decisions(catalog, decisions):
    pending = []
    if not isinstance(decisions, dict):
        return pending
    items = decisions.get("files") or decisions.get("items") or []
    if isinstance(items, dict):
        items = list(items.values())
    for it in items:
        if not isinstance(it, dict):
            continue
        src = it.get("source") or it.get("path") or it.get("file")
        if not src or src not in catalog:
            continue
        fields = it.get("fields") or it.get("metadata") or it
        for fname, fobj in fields.items():
            if fname in ("source", "path", "file") or not isinstance(fobj, dict):
                continue
            ev = fobj.get("evidence") or []
            if not ev:
                pending.append({"source": src, "field": fname, "reason": "decision without evidence"})
                continue
            catalog[src].setdefault("metadata", {})[fname] = {
                "value": fobj.get("value"), "confidence": fobj.get("confidence") or "certain",
                "method": "decisions", "evidence": ev}
    return pending


def determine_status(input_missing, file_count, errors, skipped, perm_errors, conflicts):
    if input_missing:
        return "input_error"
    if file_count == 0:
        return "insufficient_evidence"
    if errors or skipped or perm_errors or conflicts:
        return "partial"
    return "ok"


def main(argv=None):
    p = argparse.ArgumentParser(description="科研资料整理（可复核可回滚，仅标准库）")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--task", default=None)
    p.add_argument("--decisions", default=None)
    p.add_argument("--hierarchy", default="document_type,topic")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case_id, contract, raw_case = read_case_context(args.input, args.task)
    dims = [d for d in args.hierarchy.split(",") if d]

    data = empty_data()
    data["runtime"] = {"started": core.utc_now_iso(), "version": __version__}
    data["change_log"] = [{"ts": core.utc_now_iso(), "event": "skeleton written before inventory"}]
    core.write_json(os.path.join(args.output, "result.json"), build_result(case_id, contract, data))

    degradations, errors = [], []
    input_missing = not os.path.isdir(args.input)
    if input_missing:
        errors.append("input directory missing: %s" % args.input)

    inv = core.inventory(args.input)
    if inv.get("error"):
        degradations.append({"scope": "input", "detail": inv["error"]})
    core.write_json(os.path.join(args.work, "inventory.json"), inv)

    catalog = core.build_catalog(inv.get("files", []))
    core.assign_categories(catalog, dims)
    core.resolve_duplicates(catalog)
    core.group_versions(catalog)
    core.build_naming(catalog)
    plan = core.build_plan(catalog, dims)

    decisions = core.read_json(args.decisions) if args.decisions else None
    for pd in apply_decisions(catalog, decisions):
        data["pending_human"].append(pd)

    rollback = []
    if args.apply:
        rollback = core.apply_plan(args.input, os.path.join(args.output, "library"), catalog, plan)
        core.write_json(os.path.join(args.output, "rollback.json"), {"created": rollback})
    core.write_json(os.path.join(args.work, "organization.json"), {"plan": plan, "rollback": rollback})

    skipped, conflicts, perm_errors, duplicates, recoveries = compute_failures(inv, catalog, rollback)
    data["library"] = build_library(catalog, dims)
    data["evidence"] = build_evidence(inv, catalog)
    data["operations"] = plan
    data["failures"] = {"skipped": skipped, "conflicts": conflicts,
                        "permission_errors": perm_errors, "duplicates": duplicates,
                        "recoveries": recoveries}
    data["uncertainty"] = compute_uncertainty(catalog)
    data["limitations"] = compute_limitations(inv, catalog, degradations)
    data["sources"] = [{"source": e["source"], "sha256": e["sha256"],
                        "locator": e["source"], "quotes": []} for e in catalog.values()]
    data["errors"] = list(errors)
    data["degradations"] = list(degradations)
    data["runtime"]["finished"] = core.utc_now_iso()
    data["runtime"]["file_count"] = len(inv.get("files", []))
    data["runtime"]["apply"] = bool(args.apply)

    inv_sources = {f["source"] for f in inv.get("files", [])}
    missing_from_catalog = sorted(inv_sources - set(catalog))
    if missing_from_catalog:
        data["degradations"].append({"scope": "catalog", "detail": "files missing from catalog",
                                     "files": missing_from_catalog})
        data["status"] = "partial"

    data["status"] = determine_status(input_missing, len(inv.get("files", [])),
                                      errors, skipped, perm_errors, conflicts)
    log_event(data["change_log"], "finalized status=%s" % data["status"])
    core.write_json(os.path.join(args.output, "result.json"), build_result(case_id, contract, data))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        out = "/app/output"
        try:
            os.makedirs(out, exist_ok=True)
            core.write_json(os.path.join(out, "result.json"), {
                "case_id": "unknown", "status": "partial",
                "errors": ["unhandled exception: %r" % exc],
                "degradations": ["top-level exception caught"],
                "sources": [], "uncertainty": {}, "limitations": [],
                "change_log": [], "pending_human": [], "runtime": {}})
        except OSError:
            pass
        sys.exit(0)
