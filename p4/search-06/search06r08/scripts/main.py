# -*- coding: utf-8 -*-
"""Entry point for the literature-tracking Skill.

Usage (paths are relative to the skill root, e.g. /skills/weekly-skill):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py chains    --records /app/work/records.json --out /app/work/chains.json
    python3 scripts/main.py diff      --records /app/work/records.json --snapshot /app/work/snapshot.json \
                                      --window '{"start":"2024-01-01","end":"2024-01-31"}' --out /app/work/diff.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work \
                                      --findings /app/work/findings.json

`scaffold` must run in the first minutes: it writes a valid partial result.json
(case_id + every output_contract field + honest status). `run` = scaffold +
deterministic material inventory + finalize with empty findings. `finalize`
merges the Agent's findings, enriches them with dedup / version chains /
snapshot diff, then writes result.json and report.md.

The scripts never fabricate literature: missing inputs yield
`insufficient_evidence` or `input_error`, never invented records.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

# Default contract mirrors the task's three required output rows.
DEFAULT_CONTRACT = {
    "search_results": [],
    "ranking": [],
    "evidence": [],
    "status_notes": None,
    "tracking": None,
    "dedup": {},
}

# Always-present result fields beyond case_id and the output contract.
META_FIELDS = [
    "status",
    "sources",
    "uncertainty",
    "change_log",
    "errors",
    "degradations",
    "pending_human",
    "runtime",
    "validation",
]

_CASE_ID_HINTS = ("case_id", "id", "challenge_id")
_CASE_ID_RE = re.compile(r"challenge_id[:\s]*`([^`]+)`|challenge_id[:\s]*([\w.-]+)")


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """Return (case_path, task_path, material_paths, errors)."""
    errors = []
    root = Path(input_dir)
    if not root.exists():
        return None, None, [], ["input dir missing: %s" % root]
    case = None
    task = None
    materials = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name == "case.json":
            case = p
        elif name == "task.md":
            task = p
        else:
            materials.append(p)
    if case is None and task is None:
        errors.append("no case.json or task.md found under %s" % root)
    return case, task, materials, errors


def _case_id_from_obj(obj):
    if isinstance(obj, dict):
        for k in _CASE_ID_HINTS:
            v = obj.get(k)
            if v not in (None, ""):
                return str(v)
        for k in ("task", "spec", "input", "metadata"):
            v = _case_id_from_obj(obj.get(k))
            if v:
                return v
    return None


def find_output_contract(obj):
    """Locate the output_contract; return a dict {field: default} or None.

    Dict form preserves the contract's own default values; list/string forms
    map every name to None.
    """
    if isinstance(obj, dict):
        for k in ("output_contract", "contract", "output_schema", "output"):
            v = obj.get(k)
            if isinstance(v, dict):
                return dict(v)
            if isinstance(v, list):
                return {str(x): None for x in v if isinstance(x, str)}
            if isinstance(v, str):
                return {v: None}
        for k in ("task", "spec", "input"):
            found = find_output_contract(obj.get(k))
            if found:
                return found
    return None


def extract_case(input_dir):
    """Read case.json + task.md; return (case_id, contract_keys, data, errors)."""
    case_path, task_path, materials, errors = discover_input(input_dir)
    case_obj = None
    task_text = None
    if case_path is not None:
        try:
            case_obj = core.load_json(str(case_path))
        except core.InputFormatError as exc:
            errors.append(str(exc))
        except core.OversizeFileError as exc:
            errors.append(str(exc))
    if task_path is not None:
        try:
            task_text = core.read_text(str(task_path))
        except (core.InputFormatError, core.OversizeFileError) as exc:
            errors.append("task.md read failed: %s" % exc)

    case_id = _case_id_from_obj(case_obj)
    if not case_id and task_text:
        m = _CASE_ID_RE.search(task_text)
        if m:
            case_id = m.group(1) or m.group(2)
    if not case_id:
        case_id = "unknown-case"

    contract = find_output_contract(case_obj)
    if contract is None:
        contract = dict(DEFAULT_CONTRACT)

    data = {
        "case_obj": case_obj,
        "task_text": task_text,
        "case_path": str(case_path) if case_path else None,
        "task_path": str(task_path) if task_path else None,
        "materials": [str(m) for m in materials],
    }
    return case_id, contract, data, errors


def _initial_status(data, errors):
    """Honest initial status from discovered inputs."""
    has_case = data.get("case_obj") is not None
    has_task = data.get("task_text") is not None
    malformed = any("parse failed" in e or "read failed" in e for e in errors)
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


# ---------------------------------------------------------------------------
# Skeleton / result assembly
# ---------------------------------------------------------------------------

def make_skeleton(case_id, contract, status="partial"):
    result = {"case_id": case_id}
    for k, default in (contract or {}).items():
        result.setdefault(k, default)
    for k in META_FIELDS:
        if k == "status":
            result[k] = status
        elif k == "runtime":
            result[k] = {"started_at": core.utc_now_iso()}
        elif k == "change_log":
            result[k] = [{"event": "skeleton_written", "at": core.utc_now_iso(), "status": status}]
        elif k in ("validation",):
            result[k] = {}
        else:
            result[k] = []
    return result


def inventory_materials(input_dir, work_dir):
    """Deterministic material inventory: names, sizes, readable prefix. No content assumption."""
    _, _, materials, errors = discover_input(input_dir)
    inv = {"materials": [], "errors": errors}
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    for mp in materials:
        p = Path(mp)
        entry = {"path": str(p), "name": p.name, "size": p.stat().st_size if p.exists() else None}
        try:
            text = core.read_text(str(p))
            entry["readable"] = True
            entry["preview"] = text[:200]
        except core.OversizeFileError:
            entry["readable"] = False
            entry["note"] = "oversize"
        except core.InputFormatError as exc:
            entry["readable"] = False
            entry["note"] = str(exc)
        inv["materials"].append(entry)
    core.write_json_atomic(str(work / "inventory.json"), inv)
    return inv


# ---------------------------------------------------------------------------
# finalize: merge findings + enrich + write result.json / report.md
# ---------------------------------------------------------------------------

def load_findings(findings_path):
    if not findings_path or not Path(findings_path).exists():
        return {}, ["findings file missing: %s" % findings_path]
    try:
        obj = core.load_json(str(findings_path))
    except (core.InputFormatError, core.OversizeFileError) as exc:
        return {}, [str(exc)]
    if isinstance(obj, dict):
        return obj, []
    return {}, ["findings file is not a JSON object"]


def enrich_tracking(findings):
    records = findings.get("search_results") or []
    if not isinstance(records, list):
        records = []
    snapshot = findings.get("snapshot") or findings.get("tracking", {}).get("snapshot") or {}
    window = findings.get("window") or findings.get("tracking", {}).get("window") or {}
    groups, unkeyed = core.dedup_records(records)
    diff = core.diff_snapshot(records, snapshot, window)
    reminders = core.build_reminders(diff)
    chains = core.build_version_chains(records)
    return {
        "subscription": findings.get("subscription"),
        "window": window,
        "snapshot": snapshot,
        "new_items": [e["record"] for e in diff["new_items"]],
        "updates": [e["record"] for e in diff["updates"]],
        "retractions": [e["record"] for e in diff["retractions"]],
        "late_indexed": [e["record"] for e in diff["late_indexed"]],
        "reminders": reminders["reminders"],
        "suppressed": reminders["suppressed"],
        "dedup_groups": groups,
        "unkeyed_records": unkeyed,
        "version_chains": chains,
    }


def merge_result(skeleton, findings, tracking):
    result = dict(skeleton)
    for k in ("search_results", "ranking", "evidence", "status_notes", "dedup"):
        if k in findings:
            result[k] = findings[k]
    result["tracking"] = tracking
    result["status"] = findings.get("status") or result.get("status") or "partial"
    if "sources" in findings:
        result["sources"] = findings["sources"]
    if "uncertainty" in findings:
        result["uncertainty"] = findings["uncertainty"]
    if "pending_human" in findings:
        result["pending_human"] = findings["pending_human"]
    if "errors" in findings:
        result["errors"] = result.get("errors", []) + findings["errors"]
    if "degradations" in findings:
        result["degradations"] = result.get("degradations", []) + findings["degradations"]
    ev_problems = core.validate_evidence(result.get("evidence"))
    result["validation"] = {
        "evidence_problems": ev_problems,
        "evidence_ok": len(ev_problems) == 0,
        "self_check": findings.get("self_check"),
    }
    result["runtime"]["finished_at"] = core.utc_now_iso()
    result["change_log"].append({"event": "finalized", "at": core.utc_now_iso(), "status": result["status"]})
    return result


def render_report(result):
    lines = []
    lines.append("# 文献追踪结果报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("")
    tracking = result.get("tracking") or {}
    window = tracking.get("window") or {}
    lines.append("## 订阅与窗口")
    lines.append("")
    lines.append("- subscription: `%s`" % json.dumps(tracking.get("subscription"), ensure_ascii=False))
    lines.append("- window: `%s`" % json.dumps(window, ensure_ascii=False))
    lines.append("")
    lines.append("## 增量统计")
    lines.append("")
    lines.append("- new_items: %d" % len(tracking.get("new_items") or []))
    lines.append("- updates: %d" % len(tracking.get("updates") or []))
    lines.append("- retractions: %d" % len(tracking.get("retractions") or []))
    lines.append("- late_indexed (suppressed): %d" % len(tracking.get("late_indexed") or []))
    lines.append("- reminders: %d" % len(tracking.get("reminders") or []))
    lines.append("- suppressed: %d" % len(tracking.get("suppressed") or []))
    lines.append("")
    lines.append("## 状态与缺口")
    lines.append("")
    lines.append("- errors: %d" % len(result.get("errors") or []))
    lines.append("- degradations: %d" % len(result.get("degradations") or []))
    lines.append("- pending_human: %d" % len(result.get("pending_human") or []))
    lines.append("- uncertainty: %d" % len(result.get("uncertainty") or []))
    lines.append("")
    lines.append("> 本报告由 `scripts/main.py finalize` 依据 findings.json 确定性生成，")
    lines.append("> 与 result.json 字段一致；具体证据见 result.json 的 `evidence`。")
    return "\n".join(lines) + "\n"


def do_finalize(args, findings):
    case_id, contract, data, errors = extract_case(args.input)
    skeleton = make_skeleton(case_id, contract, "partial")
    tracking = enrich_tracking(findings)
    result = merge_result(skeleton, findings, tracking)
    if errors and not result.get("errors"):
        result["errors"] = errors
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    core.write_json_atomic(str(out_dir / "result.json"), result)
    core.write_text_atomic(str(out_dir / "report.md"), render_report(result))
    return result


def run_scaffold(args):
    case_id, contract, data, errors = extract_case(args.input)
    status = _initial_status(data, errors)
    skeleton = make_skeleton(case_id, contract, status)
    if errors:
        skeleton["errors"] = errors
    if not data.get("materials"):
        skeleton["pending_human"] = [{"item": "materials", "reason": "no material files discovered"}]
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    core.write_json_atomic(str(out_dir / "result.json"), skeleton)
    core.write_text_atomic(str(out_dir / "report.md"), render_report(skeleton))
    Path(args.work).mkdir(parents=True, exist_ok=True)
    print(json.dumps({"case_id": case_id, "status": status,
                      "contract_fields": sorted(contract.keys()),
                      "errors": errors}, ensure_ascii=False))
    return 0


def run_full(args):
    case_id, contract, data, errors = extract_case(args.input)
    status = _initial_status(data, errors)
    inv = inventory_materials(args.input, args.work)
    findings = {"status": status, "errors": errors,
                "degradations": [], "sources": [], "uncertainty": [], "pending_human": [],
                "search_results": [], "ranking": [], "evidence": [], "status_notes":
                {"empty": None, "conflicts": [], "permission_errors": []}}
    result = do_finalize(args, findings)
    print(json.dumps({"case_id": case_id, "status": result["status"],
                      "materials": len(inv.get("materials") or [])}, ensure_ascii=False))
    return 0


def run_dedup(args):
    records, errs = load_records(args.records)
    groups, unkeyed = core.dedup_records(records)
    core.write_json_atomic(args.out, {"groups": groups, "unkeyed": unkeyed, "errors": errs})
    print(json.dumps({"groups": len(groups), "unkeyed": len(unkeyed)}, ensure_ascii=False))
    return 0


def run_chains(args):
    records, errs = load_records(args.records)
    chains = core.build_version_chains(records)
    core.write_json_atomic(args.out, {"version_chains": chains, "errors": errs})
    print(json.dumps({"chains": len(chains),
                      "multi_stage": sum(1 for c in chains if c["is_version_chain"])}, ensure_ascii=False))
    return 0


def run_diff(args):
    records, errs = load_records(args.records)
    snapshot = {}
    if args.snapshot and Path(args.snapshot).exists():
        try:
            snapshot = core.load_json(args.snapshot)
        except (core.InputFormatError, core.OversizeFileError) as exc:
            errs.append(str(exc))
    try:
        window = json.loads(args.window) if args.window else {}
    except json.JSONDecodeError as exc:
        errs.append("window JSON parse failed: %s" % exc)
        window = {}
    diff = core.diff_snapshot(records, snapshot, window)
    core.write_json_atomic(args.out, {"diff": diff, "snapshot": snapshot, "window": window, "errors": errs})
    print(json.dumps({k: len(v) for k, v in diff.items()}, ensure_ascii=False))
    return 0


def load_records(path):
    if not path or not Path(path).exists():
        return [], ["records file missing: %s" % path]
    try:
        obj = core.load_json(path)
    except (core.InputFormatError, core.OversizeFileError) as exc:
        return [], [str(exc)]
    if isinstance(obj, list):
        return obj, []
    if isinstance(obj, dict) and isinstance(obj.get("search_results"), list):
        return obj["search_results"], []
    return [], ["records file is neither a list nor {search_results: [...]}"]


def run_normalize(args):
    try:
        rec = json.loads(args.record)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": "record JSON parse failed: %s" % exc}, ensure_ascii=False))
        return 1
    key = core.canonical_key(rec)
    out = {
        "doi": core.normalize_doi(rec.get("doi")),
        "arxiv": core.normalize_arxiv(rec.get("arxiv") or rec.get("arxiv_id") or rec.get("eprint")),
        "title": core.normalize_title(rec.get("title")),
        "canonical_key": key,
        "version": core.classify_version(rec),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scripts/main.py",
                                     description="Literature-tracking Skill entry point.")
    sub = parser.add_subparsers(dest="command")

    for name in ("scaffold", "run", "finalize"):
        p = sub.add_parser(name)
        p.add_argument("--input", default="/input")
        p.add_argument("--output", default="/app/output")
        p.add_argument("--work", default="/app/work")
        if name == "finalize":
            p.add_argument("--findings", default="/app/work/findings.json")

    p = sub.add_parser("dedup")
    p.add_argument("--records", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("chains")
    p.add_argument("--records", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("diff")
    p.add_argument("--records", required=True)
    p.add_argument("--snapshot", default=None)
    p.add_argument("--window", default=None)
    p.add_argument("--out", required=True)

    p = sub.add_parser("normalize")
    p.add_argument("--record", required=True)

    args = parser.parse_args(argv)
    if args.command == "scaffold":
        return run_scaffold(args)
    if args.command == "run":
        return run_full(args)
    if args.command == "finalize":
        findings, ferrs = load_findings(args.findings)
        do_finalize(args, findings)
        print(json.dumps({"status": "finalized", "findings_errors": ferrs}, ensure_ascii=False))
        return 0 if not ferrs else 2
    if args.command == "dedup":
        return run_dedup(args)
    if args.command == "chains":
        return run_chains(args)
    if args.command == "diff":
        return run_diff(args)
    if args.command == "normalize":
        return run_normalize(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
