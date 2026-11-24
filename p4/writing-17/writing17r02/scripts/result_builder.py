#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and validate result.json for the mind-map skill.

Standard library only.
Modes:
  --init      Write an early partial skeleton (must run within first 3 minutes).
  --finalize  Merge deliverables and check report, set honest status, validate.
  --validate  Validate an existing result.json (exit 0 ok / 1 invalid).

Usage (init):
  python3 -B scripts/result_builder.py --init --case /input/case.json \
      --task /input/task.md --output-dir /app/output

Usage (finalize):
  python3 -B scripts/result_builder.py --finalize --case /input/case.json \
      --mindmap /app/work/mindmap.json --report /app/output/evidence_report.json \
      --output-dir /app/output --fill "mindmap=mindmap.svg"
"""
import argparse
import json
import sys
import time
from pathlib import Path

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
CONTRACT_KEY_CANDIDATES = (
    "output_contract", "outputContract", "outputs", "expected_outputs",
    "output_spec", "outputSpec", "deliverables",
)


def read_json(path):
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, f"cannot read {p}: {exc}"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, f"bad JSON in {p}: {exc}"


def _default_for(value):
    """Return null for scalars, [] for lists, {} for dicts (recursively)."""
    if isinstance(value, dict):
        return {k: _default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return None
    return None


def discover_contract(case):
    if not isinstance(case, dict):
        return {}
    for key in CONTRACT_KEY_CANDIDATES:
        if key in case:
            return case[key] if isinstance(case[key], dict) else {"value": case[key]}
    # search one level of nesting (task/spec/requirements)
    for key in ("task", "spec", "requirements", "interface"):
        inner = case.get(key)
        if isinstance(inner, dict):
            for ck in CONTRACT_KEY_CANDIDATES:
                if ck in inner:
                    return inner[ck] if isinstance(inner[ck], dict) else {"value": inner[ck]}
    return {}


def echo_contract(contract):
    return _default_for(contract) if isinstance(contract, dict) else {"value": None}


def case_id_of(case, case_path):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if key in case and case[key] not in (None, ""):
                return str(case[key])
    return Path(case_path).stem or "unknown"


def build_skeleton(case, case_path):
    contract = discover_contract(case)
    return {
        "case_id": case_id_of(case, case_path),
        "status": "partial",
        **echo_contract(contract),
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "action": "init_skeleton",
                "detail": "early partial result written before analysis; "
                          "fields echoed from output_contract with null/[] defaults",
            }
        ],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": time.time(), "finished_at": None, "elapsed_s": None},
    }


def set_in(d, keys, value):
    """Set d[k1][k2]... value, creating intermediate dicts. Keys may be dotted."""
    cur = d
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value


def parse_fill(spec):
    if "=" not in spec:
        raise ValueError(f"fill must be key=value, got: {spec!r}")
    key, value = spec.split("=", 1)
    # allow JSON values such as {"a":1} or [1,2]
    try:
        value = json.loads(value)
    except json.JSONDecodeError:
        pass
    return key, value


def derive_sources_and_evidence_map(mm):
    """Return (sources, evidence_map) from mindmap evidence entries."""
    by_loc = {}
    for n in mm.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        for e in n.get("evidence") or []:
            if not isinstance(e, dict) or not e.get("locator"):
                continue
            loc = str(e["locator"])
            by_loc.setdefault(loc, []).append({
                "node_id": str(n.get("id", "")),
                "kind": n.get("kind"),
                "quote": e.get("quote", ""),
                "type": e.get("type"),
            })
    for edge in mm.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        for e in edge.get("evidence") or []:
            if not isinstance(e, dict) or not e.get("locator"):
                continue
            loc = str(e["locator"])
            by_loc.setdefault(loc, []).append({
                "node_id": str(edge.get("from", "")),
                "edge_id": str(edge.get("id", "")),
                "quote": e.get("quote", ""),
                "type": e.get("type"),
            })
    evidence_map = {loc: refs for loc, refs in sorted(by_loc.items())}
    sources = [
        {
            "locator": loc,
            "count": len(refs),
            "quotes": [r.get("quote", "") for r in refs[:5]],
        }
        for loc, refs in sorted(by_loc.items())
    ]
    return sources, evidence_map


def validate(result):
    errors = []
    warnings = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"], []
    if not result.get("case_id"):
        errors.append("missing case_id")
    if result.get("status") not in STATUSES:
        errors.append(f"status must be one of {STATUSES}")
    for field in ("sources", "uncertainty", "change_log", "errors", "degradations", "pending_human"):
        if field not in result or not isinstance(result[field], list):
            errors.append(f"{field} must be a list")
    for item in result.get("sources", []):
        if not isinstance(item, dict) or not item.get("locator"):
            warnings.append("a source entry lacks locator")
    for item in result.get("uncertainty", []):
        if not isinstance(item, dict) or not item.get("item"):
            warnings.append("an uncertainty entry lacks item")
    if result.get("runtime") is None:
        result["runtime"] = {}
    return errors, warnings


def init_mode(args):
    case, err = read_json(args.case)
    if err:
        case = None
    skeleton = build_skeleton(case, args.case)
    if err:
        skeleton["status"] = "input_error"
        skeleton["errors"].append(err)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    outpath = out / "result.json"
    outpath.write_text(
        json.dumps(skeleton, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": skeleton["status"], "written": str(outpath)}, ensure_ascii=False))


def finalize_mode(args):
    result_path = Path(args.output_dir) / "result.json"
    result, err = read_json(result_path)
    if err or not isinstance(result, dict):
        result = build_skeleton(None, args.case)
        result["errors"].append(err or "no prior result.json; rebuilt skeleton")
    started = result.get("runtime", {}).get("started_at")
    result.setdefault("runtime", {})["finished_at"] = time.time()
    if started:
        result["runtime"]["elapsed_s"] = round(time.time() - started, 2)

    if args.mindmap:
        mm, mm_err = read_json(args.mindmap)
        if mm_err:
            result["errors"].append(mm_err)
        else:
            nodes = mm.get("nodes") or []
            edges = mm.get("edges") or []
            result.setdefault("mindmap_summary", {})
            result["mindmap_summary"] = {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "title": mm.get("title"),
            }
            if not nodes:
                result["status"] = "insufficient_evidence"
            sources, evidence_map = derive_sources_and_evidence_map(mm)
            if not result.get("sources"):
                result["sources"] = sources
            if evidence_map:
                (Path(args.output_dir) / "evidence_map.json").write_text(
                    json.dumps(evidence_map, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

    if args.report:
        rep, rep_err = read_json(args.report)
        # Preserve non-report string errors (read failures); replace report-derived lists
        prior_str_errors = [e for e in result.get("errors", []) if isinstance(e, str)]
        prior_pending = [p for p in result.get("pending_human", [])
                         if not (isinstance(p, dict) and p.get("kind") == "unverified_nodes")]
        prior_uncertainty = [u for u in result.get("uncertainty", [])
                             if not (isinstance(u, dict) and u.get("type") == "source_conflict")]
        if rep_err:
            result["errors"] = prior_str_errors + [rep_err]
            result["degradations"] = result.get("degradations", [])
            result["uncertainty"] = prior_uncertainty
            result["pending_human"] = prior_pending
        elif isinstance(rep, dict):
            report_errors = rep.get("errors", [])
            report_warnings = rep.get("warnings", [])
            report_conflicts = rep.get("conflicts", [])
            unverified = rep.get("unverified", [])
            result["errors"] = prior_str_errors + report_errors
            result["degradations"] = report_warnings
            result["uncertainty"] = prior_uncertainty + report_conflicts
            if report_conflicts:
                result["source_conflicts"] = report_conflicts
            result["pending_human"] = prior_pending + (
                [{"kind": "unverified_nodes", "items": unverified}] if unverified else []
            )
            if rep.get("node_count", 0) > 0 and not report_errors:
                result["status"] = "ok" if result.get("status") != "insufficient_evidence" else "partial"
            elif rep.get("node_count", 0) == 0:
                result["status"] = "insufficient_evidence"

    for spec in args.fill or []:
        key, value = parse_fill(spec)
        keys = key.split(".")
        set_in(result, keys, value)
        # honest check: a fill value that names a file must exist under output dir
        if isinstance(value, str) and not value.startswith(("/", "http")):
            candidate = Path(args.output_dir) / value
            if value.endswith((".json", ".svg", ".mm", ".md", ".pdf", ".png", ".xlsx", ".docx", ".pptx")):
                if not candidate.exists():
                    result.setdefault("degradations", []).append(
                        {"item": key, "problem": f"contract field points to missing file: {value}"}
                    )

    result["change_log"].append({
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": "finalize",
        "detail": "merged deliverables, evidence report and contract fields",
    })

    errors, warnings = validate(result)
    result["errors"].extend(errors)
    if warnings:
        result["degradations"].extend(warnings)
    # never overwrite an honest failure status just because validation passed
    if errors and result.get("status") == "ok":
        result["status"] = "partial"

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    (Path(args.output_dir) / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result.get("status"),
                      "errors": len(errors),
                      "warnings": len(warnings)}, ensure_ascii=False))
    sys.exit(1 if errors else 0)


def validate_mode(args):
    result, err = read_json(args.result)
    if err:
        print(f"INVALID: {err}", file=sys.stderr)
        sys.exit(1)
    errors, warnings = validate(result)
    for e in errors:
        print("ERROR:", e, file=sys.stderr)
    for w in warnings:
        print("WARN:", w, file=sys.stderr)
    if errors:
        print("INVALID", file=sys.stderr)
        sys.exit(1)
    print("VALID")
    sys.exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--mindmap", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--output-dir", default="/app/output")
    ap.add_argument("--fill", action="append", default=None)
    args = ap.parse_args()

    modes = sum(1 for m in (args.init, args.finalize, args.validate) if m)
    if modes != 1:
        ap.error("choose exactly one of --init, --finalize, --validate")
    if args.init:
        init_mode(args)
    elif args.finalize:
        finalize_mode(args)
    else:
        validate_mode(args)


if __name__ == "__main__":
    main()
