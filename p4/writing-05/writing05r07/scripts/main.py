#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-driven writing Skill entry point (stdlib only).

Subcommands (run from the Skill root; relative paths also work when cwd is
not the Skill root because this file locates its sibling core_impl.py):

    scaffold   write an honest partial result.json immediately
    discover   walk /input and dump a material manifest to /app/work
    extract    pull readable text out of PDF/txt/md materials into /app/work
    finalize   merge claim/evidence/source work files into result.json,
               render /app/output/evidence_report.md, and re-validate
    run        scaffold -> discover -> extract -> finalize in one pass

Every stage exits 0 even when inputs are empty, malformed or oversized; the
status field and errors/degradations tell the truth instead of crashing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core_impl as core  # noqa: E402


def _args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", nargs="?", default="run",
                    choices=["scaffold", "discover", "extract", "finalize", "run"])
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--out", default="/app/output")
    return ap.parse_args(argv)


def _resolve_case_id(case_obj):
    if isinstance(case_obj, dict):
        for key in ("case_id", "caseId", "id"):
            value = case_obj.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _contract_keys(case_obj):
    """Return the output_contract key names declared by the case, if any."""
    if not isinstance(case_obj, dict):
        return []
    for key in ("output_contract", "outputContract", "contract", "result_schema"):
        val = case_obj.get(key)
        if isinstance(val, dict):
            return list(val.keys())
    return []


def _load_existing_result(out):
    data, err = core.read_json(out)
    return (data if isinstance(data, dict) else None), err


def _scaffold(a):
    case_obj, case_err = core.read_json(a.case)
    case_id = _resolve_case_id(case_obj)
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    out_result = os.path.join(a.out, "result.json")
    existing, _ = core.read_json(out_result)
    if isinstance(existing, dict) and existing.get("status") in ("complete", "partial"):
        # Never overwrite an already-improved result; only echo case_id.
        result = existing
        result.setdefault("case_id", case_id)
    else:
        result = core.init_result(case_id)
        if case_err:
            result["errors"].append(case_err)
            result["degradations"].append(f"case.json unreadable: {case_err}")
    err = core.atomic_write_json(out_result, result)
    if err:
        result["errors"].append(err)
    return result, err


def _discover(a):
    files, oversized, errors = core.discover_inputs(a.input)
    manifest = {"files": files, "oversized": oversized, "errors": errors}
    os.makedirs(a.work, exist_ok=True)
    werr = core.atomic_write_json(os.path.join(a.work, "manifest.json"), manifest)
    return manifest, werr


def _extract(a):
    manifest_path = os.path.join(a.work, "manifest.json")
    manifest, _ = core.read_json(manifest_path)
    files = (manifest or {}).get("files", [])
    if not files:  # fall back to a live scan if manifest missing
        files, _, _ = core.discover_inputs(a.input)
        files = [f for f in files]
    os.makedirs(a.work, exist_ok=True)
    extracted = []
    degradations = []
    for f in files:
        suffix = f.get("suffix", "")
        if f.get("binary"):
            degradations.append(f"skipped binary file: {f.get('path')}")
            continue
        src = f.get("abs")
        if suffix == ".pdf":
            out_txt = os.path.join(a.work, "manuscript.txt")
            text, err = core.try_pdftotext(src, out_txt)
            if err:
                degradations.append(f"PDF extraction failed for {f.get('path')}: {err}")
            else:
                extracted.append({"source": f.get("path"), "out": "manuscript.txt", "kind": "pdf"})
        elif suffix in (".txt", ".md", ".json", ".csv", ".yaml", ".yml", ".tsv"):
            text, err = core.read_text(src)
            if err:
                degradations.append(f"text read failed for {f.get('path')}: {err}")
            else:
                out_txt = os.path.join(a.work, f.get("path").replace("/", "_").replace("\\", "_"))
                Path(out_txt).parent.mkdir(parents=True, exist_ok=True)
                try:
                    Path(out_txt).write_text(text, encoding="utf-8")
                    extracted.append({"source": f.get("path"), "out": os.path.basename(out_txt), "kind": suffix.lstrip(".")})
                except OSError as exc:
                    degradations.append(f"write failed for {f.get('path')}: {exc}")
        else:
            degradations.append(f"unsupported extension skipped: {f.get('path')} ({suffix or 'none'})")
    core.atomic_write_json(os.path.join(a.work, "extracted.json"),
                           {"extracted": extracted, "degradations": degradations})
    return extracted, degradations


def _finalize(a):
    out_result = os.path.join(a.out, "result.json")
    result, _ = _load_existing_result(out_result)
    if not isinstance(result, dict):
        result = core.init_result(None)

    # Echo original output_contract keys: default contract is
    # deliverable / sources / change_log; extra declared keys are kept intact.
    case_obj, _ = core.read_json(a.case)
    for key in _contract_keys(case_obj):
        result.setdefault(key, None)
    result.setdefault("case_id", _resolve_case_id(case_obj))
    result.setdefault("status", "in_progress")
    result.setdefault("deliverable", {"claims": [], "evidence_table": [], "manuscript": None, "summary": ""})
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("change_log", [])
    result.setdefault("limitations", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])

    # Surface oversized-file skips recorded during discover.
    manifest, _ = core.read_json(os.path.join(a.work, "manifest.json"))
    if isinstance(manifest, dict):
        for item in manifest.get("oversized", []):
            if isinstance(item, dict):
                result["degradations"].append(
                    f"oversized skipped: {item.get('path')} ({item.get('bytes')} bytes)")

    # Merge work-file outputs without clobbering richer LLM-written content.
    for wf, target_key in (("claims.json", "claims"), ("evidence_table.json", "evidence_table"), ("sources.json", "sources")):
        wpath = os.path.join(a.work, wf)
        if not os.path.exists(wpath):
            continue  # expected before P2 analysis; not a degradation
        wobj, werr = core.read_json(wpath)
        if werr:
            result["degradations"].append(werr)
            continue
        if wobj is None:
            continue
        if target_key == "sources":
            if not result.get("sources") and isinstance(wobj, list):
                result["sources"] = wobj
        else:
            if not result.get("deliverable", {}).get(target_key):
                result.setdefault("deliverable", {})[target_key] = wobj if isinstance(wobj, list) else [wobj]

    # Manuscript path: prefer one already declared; else detect common outputs.
    if not result.get("deliverable", {}).get("manuscript"):
        for name in ("manuscript.md", "manuscript.docx", "manuscript.txt", "manuscript.pdf"):
            if os.path.exists(os.path.join(a.out, name)):
                result["deliverable"]["manuscript"] = name
                break

    # Honest status; append change_log; stamp runtime.
    status = core.compute_status(result)
    result["status"] = status
    result["change_log"] = core.merge_log(result.get("change_log"), "finalize",
                                          f"merged work files; status={status}")
    finished = core.now_iso()
    result["runtime"]["finished_at"] = finished
    try:
        started = result["runtime"].get("started_at")
        from datetime import datetime
        s = datetime.fromisoformat(started.replace("Z", "+00:00"))
        e = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        result["runtime"]["seconds"] = round((e - s).total_seconds(), 1)
    except (ValueError, AttributeError, TypeError):
        result["runtime"]["seconds"] = None

    # Validate strength enums present in evidence_table so downstream is clean.
    for row in (result.get("deliverable", {}).get("evidence_table") or []):
        if isinstance(row, dict) and "strength" in row:
            row["strength"] = core.validate_strength(row.get("strength"))

    werr = core.atomic_write_json(out_result, result)
    if werr:
        result["errors"].append(werr)

    _render_report(a.out, result)
    return result, werr


def _render_report(out_dir, result):
    """Same-source readable report; adds no facts beyond result.json."""
    lines = [
        "# 证据驱动写作复核报告",
        "",
        f"- case_id: {result.get('case_id')}",
        f"- status: {result.get('status')}",
        f"- 论断数: {len(result.get('deliverable', {}).get('claims') or [])}",
        f"- 证据表行数: {len(result.get('deliverable', {}).get('evidence_table') or [])}",
        f"- 来源条数: {len(result.get('sources') or [])}",
        "",
        "## 未确认 / 需人工核对",
        "",
    ]
    pend = result.get("pending_human") or result.get("uncertainty") or []
    if pend:
        for item in pend:
            lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
    else:
        lines.append("- （无）")
    lines += ["", "## 材料缺口与降级", ""]
    lim = result.get("limitations") or result.get("degradations") or []
    if lim:
        for item in lim:
            lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
    else:
        lines.append("- （无）")
    try:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        Path(os.path.join(out_dir, "evidence_report.md")).write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def main(argv=None):
    a = _args(argv)
    try:
        if a.command in ("scaffold", "run"):
            _, serr = _scaffold(a)
        if a.command in ("discover", "run"):
            _discover(a)
        if a.command in ("extract", "run"):
            _extract(a)
        if a.command in ("finalize", "run"):
            _finalize(a)
    except Exception as exc:  # last-resort guard: never crash the harness
        os.makedirs(a.out, exist_ok=True)
        result = core.init_result(None)
        result["status"] = "failed"
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        result["change_log"] = core.merge_log(result.get("change_log"), "guard", str(exc))
        core.atomic_write_json(os.path.join(a.out, "result.json"), result)
    return 0  # always exit 0; the JSON carries the honest state


if __name__ == "__main__":
    sys.exit(main())
