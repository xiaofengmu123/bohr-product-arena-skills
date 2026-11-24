#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Long-document academic writing skill helpers.

Subcommands (standard library only):
  scaffold     discover /input, write work/context.json and a valid result.json skeleton
  consistency  run deterministic checks on drafts, write work/consistency_report.json
  finalize     merge Agent updates into the skeleton, validate, write result.json
  selfcheck    run built-in sanity checks (exit 0 on pass)

Runtime defaults: --input /input --output /app/output --work /app/work.
Paths in SKILL.md are relative to the Skill root; the harness installs the
Skill at /skills/weekly-skill.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import consistency  # same directory as this module

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_FILE_BYTES = 2 * 1024 * 1024
PREVIEW_CHARS = 2000
TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".json", ".csv", ".tsv", ".tex", ".bib",
    ".yaml", ".yml", ".rst", ".text",
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    """Return (text, error) with encoding fallbacks. Error None on success."""
    p = Path(path)
    if not p.exists():
        return None, "missing"
    try:
        data = p.read_bytes()
    except OSError as exc:
        return None, f"read_failed:{type(exc).__name__}"
    if len(data) > MAX_FILE_BYTES:
        return None, "oversize"
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace"), "fallback_replace"


def read_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"bad_json:{exc.lineno}:{exc.msg[:80]}"
    except (TypeError, ValueError) as exc:
        return None, f"json_error:{type(exc).__name__}"


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def discover_input(input_dir):
    """Return (case_json, task_md, materials, errors, degradations)."""
    input_dir = Path(input_dir)
    errors, degradations = [], []
    case_json = input_dir / "case.json"
    task_md = input_dir / "task.md"
    materials = []
    if not input_dir.exists():
        return case_json, task_md, [], [f"input_dir_missing:{input_dir}"], []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(input_dir).as_posix()
        name = p.name.lower()
        if p.parent == input_dir and name in ("case.json", "task.md"):
            continue
        suffix = p.suffix.lower()
        if suffix not in TEXT_EXTS and suffix != ".pdf":
            continue
        if suffix == ".pdf":
            materials.append({"path": rel, "type": "pdf", "bytes": p.stat().st_size,
                              "text": None, "preview": None, "note": "parse with platform PDF tool"})
            continue
        text, err = read_text(p)
        if err == "oversize":
            degradations.append(f"oversize_skipped:{rel}")
            materials.append({"path": rel, "type": "oversize", "bytes": p.stat().st_size,
                              "text": None, "preview": None, "error": err})
            continue
        if err:
            errors.append(f"{rel}:{err}")
            materials.append({"path": rel, "type": "unreadable", "bytes": p.stat().st_size,
                              "text": None, "preview": None, "error": err})
            continue
        materials.append({
            "path": rel,
            "type": suffix.lstrip("."),
            "bytes": p.stat().st_size,
            "text": text,
            "preview": text[:PREVIEW_CHARS],
            "error": None,
        })
    return case_json, task_md, materials, errors, degradations


def _default_for(val):
    if isinstance(val, list):
        return []
    if isinstance(val, dict):
        return {}
    return None


def build_skeleton(case_id, output_contract, status):
    if isinstance(output_contract, dict):
        contract = {str(k): _default_for(v) for k, v in output_contract.items()}
    elif isinstance(output_contract, list):
        contract = {str(k): None for k in output_contract}
    else:
        contract = {
            "target_deliverable": None,
            "evidence_map": [],
            "version_and_limits": None,
        }
    return {
        "case_id": case_id,
        "status": status,
        "output_contract": contract,
        "artifacts": {},
        "target_deliverable": None,
        "evidence_map": [],
        "version_and_limits": {},
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {
            "scaffold_started_at": now_iso(),
            "finalized_at": None,
            "elapsed_seconds": None,
            "note": "model request count recorded by platform",
        },
    }


def _preview_only(m):
    return {k: m[k] for k in ("path", "type", "bytes", "preview", "error", "note") if k in m}


def cmd_scaffold(args):
    t0 = time.time()
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case_json, task_md, materials, errors, degradations = discover_input(args.input)

    case_data, case_id, output_contract = None, None, None
    cerr = "case.json:missing"
    if case_json.exists():
        case_data, cerr = read_json(case_json)
        if cerr is None and isinstance(case_data, dict):
            case_id = case_data.get("case_id") or case_data.get("id") or case_data.get("challenge_id") or "unknown"
            for key in ("output_contract", "outputContract", "output_contract_fields"):
                if key in case_data and case_data[key] is not None:
                    output_contract = case_data[key]
                    break
        else:
            errors.append(f"case.json:{cerr}")
    else:
        errors.append(cerr)

    task_text, terr = read_text(task_md) if task_md else (None, "missing")
    if terr:
        errors.append(f"task.md:{terr}")

    if case_data is None and not materials:
        status = "insufficient_evidence"
    else:
        status = "partial"

    result = build_skeleton(case_id or "unknown", output_contract, status)
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"]["scaffold_elapsed_ms"] = int((time.time() - t0) * 1000)

    context = {
        "case_id": case_id,
        "output_contract": output_contract,
        "input_dir": str(Path(args.input)),
        "task_md_path": str(task_md) if task_md else None,
        "task_md_preview": (task_text[:PREVIEW_CHARS] if task_text else None),
        "materials": [_preview_only(m) for m in materials],
        "errors": errors,
        "degradations": degradations,
    }
    write_json(work_dir / "context.json", context)
    write_json(output_dir / "result.json", result)

    print(json.dumps({
        "case_id": result["case_id"],
        "status": result["status"],
        "material_count": len(materials),
        "materials": [_preview_only(m) for m in materials],
        "errors": errors,
        "degradations": degradations,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_consistency(args):
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)

    termlist, terr = read_json(work_dir / "termlist.json")
    bibliography, berr = read_json(work_dir / "bibliography.json")

    drafts = sorted(output_dir.glob("*.md")) + sorted(output_dir.glob("*.tex"))
    if not drafts:
        drafts = sorted(work_dir.glob("*.md")) + sorted(work_dir.glob("*.tex"))

    per_file = {}
    errors = []
    if terr:
        errors.append(f"termlist:{terr}")
    if berr:
        errors.append(f"bibliography:{berr}")
    for d in drafts:
        text, err = read_text(d)
        if err:
            errors.append(f"{d.name}:{err}")
            continue
        per_file[d.name] = consistency.run_checks(text, termlist=termlist, bibliography=bibliography)

    total = sum(r["total_count"] for r in per_file.values())
    critical = sum(r["critical_count"] for r in per_file.values())
    report = {
        "checked_at": now_iso(),
        "files": list(per_file.keys()),
        "per_file": per_file,
        "total_count": total,
        "critical_count": critical,
        "errors": errors,
        "note": "heuristic checks; resolve by hand where noted",
    }
    write_json(work_dir / "consistency_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def deep_merge(base, patch):
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def validate_result(result, output_dir):
    problems = []
    if not result.get("case_id"):
        problems.append("missing:case_id")
    if result.get("status") not in STATUSES:
        problems.append(f"invalid_status:{result.get('status')}")
    if not isinstance(result.get("output_contract"), dict):
        problems.append("missing:output_contract")
    for key in ("sources", "uncertainty", "change_log", "errors", "degradations", "pending_human"):
        if not isinstance(result.get(key), list):
            problems.append(f"missing:{key}")
    artifacts = result.get("artifacts") or {}
    for name, rel in artifacts.items():
        if isinstance(rel, str) and rel and not (output_dir / rel).exists():
            problems.append(f"artifact_missing:{name}:{rel}")
    return problems


def cmd_finalize(args):
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    result_path = output_dir / "result.json"

    result, err = read_json(result_path)
    if err or not isinstance(result, dict):
        case_json, task_md, materials, errors, degradations = discover_input(args.input)
        case_data = read_json(case_json)[0] if case_json.exists() else None
        case_id = None
        output_contract = None
        if isinstance(case_data, dict):
            case_id = case_data.get("case_id") or case_data.get("id") or case_data.get("challenge_id") or "unknown"
            for key in ("output_contract", "outputContract", "output_contract_fields"):
                if key in case_data and case_data[key] is not None:
                    output_contract = case_data[key]
                    break
        result = build_skeleton(case_id or "unknown", output_contract, "partial")
        result["errors"] = errors
        result["degradations"] = degradations

    upd, uerr = read_json(work_dir / "updates" / "result_update.json")
    if uerr is None and isinstance(upd, dict):
        deep_merge(result, upd)
    elif uerr:
        result.setdefault("errors", []).append(f"result_update:{uerr}")

    cr, cerr = read_json(work_dir / "consistency_report.json")
    if cerr is None and isinstance(cr, dict):
        result["consistency_report"] = cr
        if result.get("status") == "ok" and cr.get("critical_count", 0) > 0:
            result["status"] = "partial"

    problems = validate_result(result, output_dir)
    if problems:
        for p in problems:
            if p not in result.get("errors", []):
                result.setdefault("errors", []).append(p)
        if result.get("status") == "ok":
            result["status"] = "partial"

    result["runtime"]["finalized_at"] = now_iso()
    write_json(result_path, result)
    print(json.dumps({"status": result.get("status"), "problems": problems,
                      "artifact_count": len(result.get("artifacts", {}))}, ensure_ascii=False))
    return 0


def cmd_selfcheck(_args=None):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp, out, wrk = root / "input", root / "output", root / "work"
        inp.mkdir(); out.mkdir(); wrk.mkdir()
        ns = argparse.Namespace(input=str(inp), output=str(out), work=str(wrk))

        assert cmd_scaffold(ns) == 0
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r["status"] == "insufficient_evidence", r["status"]
        assert r["case_id"] == "unknown"

        (inp / "case.json").write_text(
            '{"case_id":"c1","output_contract":{"target_deliverable":null,"evidence_map":[]}}',
            encoding="utf-8")
        (inp / "m1.md").write_text(
            "# 材料\n图 1 数据显示 42。见表 2。参考文献 [1]。\n[1] A. Author, 2020.\n",
            encoding="utf-8")
        assert cmd_scaffold(ns) == 0
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r["status"] == "partial"
        assert r["output_contract"] == {"target_deliverable": None, "evidence_map": []}

        (out / "draft.md").write_text(
            "# 草稿\n图 1 结果。见图 2。引用 [1] 与 [5]。\n[1] A. Author, 2020.\n",
            encoding="utf-8")
        assert cmd_consistency(ns) == 0
        cr = json.loads((wrk / "consistency_report.json").read_text(encoding="utf-8"))
        assert cr["critical_count"] >= 1

        (wrk / "updates").mkdir(exist_ok=True)
        (wrk / "updates" / "result_update.json").write_text(
            json.dumps({"status": "ok", "target_deliverable": "draft.md",
                        "artifacts": {"draft": "draft.md"}}, ensure_ascii=False),
            encoding="utf-8")
        assert cmd_finalize(ns) == 0
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert r["case_id"] == "c1"
        assert r["status"] == "partial"  # consistency critical -> downgraded
        assert r["artifacts"]["draft"] == "draft.md"
        assert "artifact_missing" not in r["errors"]
    print("selfcheck ok")
    return 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=os.environ.get("SKILL_INPUT", "/input"))
    common.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", "/app/output"))
    common.add_argument("--work", default=os.environ.get("SKILL_WORK", "/app/work"))

    ap = argparse.ArgumentParser(description="Long-document academic writing skill helpers.",
                                 parents=[common])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("scaffold", help="discover input and write result skeleton", parents=[common])
    sub.add_parser("consistency", help="run consistency checks on drafts", parents=[common])
    sub.add_parser("finalize", help="merge updates and finalize result.json", parents=[common])
    sub.add_parser("selfcheck", help="run built-in sanity checks", parents=[common])
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "scaffold": cmd_scaffold,
        "consistency": cmd_consistency,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
