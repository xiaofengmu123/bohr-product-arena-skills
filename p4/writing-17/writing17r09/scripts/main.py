#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mind-map writing skill entry point.

Standard library only. Two phases:

  1. scaffold (default, no --spec): discover input, read case.json/task.md,
     and write a legal partial result.json to /app/output immediately. This is
     the "first-3-minutes skeleton" — it must land before any analysis, so a
     crash still leaves a valid, honest partial deliverable.

  2. finalize (with --spec <spec.json>): load the agent-written mind-map spec,
     validate it, render OPML/Markdown/SVG, verify evidence quotes against
     material text, and upgrade result.json to ok / partial.

Also supports --check to validate an existing output directory.

Usage:
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py --input /input --output /app/output --work /app/work --spec /app/work/spec.json
  python3 -B scripts/main.py --input /input --output /app/output --check
"""

import argparse
import datetime
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import evidence
import mindmap

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# The task's "Skill 运行后的输出要求" three content fields (used when
# case.json carries no explicit output_contract object).
DEFAULT_CONTRACT = {
    "deliverable": None,          # 目标产物
    "evidence_map": [],           # 事实与来源映射
    "version_boundaries": None,   # 版本与边界
}

DELIVERABLE_KEYS = ("mindmap_opml", "mindmap_markdown", "preview_svg",
                    "preview_png", "preview_pdf")


def _iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _find_file(input_dir, name):
    for root, _dirs, files in os.walk(input_dir):
        if name in files:
            return os.path.join(root, name)
    return None


def _extract_case_id(case_obj, case_path):
    """case_id from common keys; fall back to the file stem."""
    if isinstance(case_obj, dict):
        for k in ("case_id", "id", "challenge_id", "challengeId", "armiId"):
            v = case_obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if case_path:
        return os.path.splitext(os.path.basename(case_path))[0]
    return "unknown_case"


def _extract_output_contract(case_obj):
    """Echo the task's output_contract keys, if present, else defaults."""
    if isinstance(case_obj, dict):
        for k, v in case_obj.items():
            if k.lower() in ("output_contract", "outputcontract") and isinstance(v, dict):
                return v
    return None


def _material_text_index(input_dir, work_dir):
    """Build {rel: text} for text-extractable files, skipping oversized/binary."""
    index = {}
    dirs = [d for d in (input_dir, work_dir) if d and os.path.isdir(d)]
    seen = set()
    for d in dirs:
        for rec in evidence.discover_inputs(d):
            if rec["kind"] not in ("text", "material"):
                continue
            rel = rec["rel"]
            key = rel
            if key in seen:
                key = os.path.basename(d) + "/" + rel
            seen.add(key)
            if rec["size"] < 0 or rec["size"] > evidence.MAX_FILE_BYTES:
                continue
            text, meta = evidence.read_text(rec["path"])
            if text is not None:
                index[key] = text
            # also index by bare filename for lenient matching
            base = os.path.basename(rec["rel"])
            if base not in index:
                index[base] = text
    return index


def _scaffold(input_dir, output_dir, work_dir):
    """Discover inputs and return a minimal honest result dict."""
    started = _iso_now()
    t0 = time.time()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    result = {
        "case_id": None,
        "status": "partial",
        "deliverable": None,
        "evidence_map": [],
        "version_boundaries": None,
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {
            "started_at": started,
            "seconds_elapsed": 0.0,
            "phase": "scaffold",
            "python": sys.version.split()[0],
            "entrypoint": "scripts/main.py",
        },
    }

    case_path = _find_file(input_dir, "case.json")
    case_obj = None
    if case_path:
        case_obj, case_err = evidence.load_json_safe(case_path)
        if case_err:
            result["degradations"].append({"where": "case.json", "detail": case_err})
        result["case_id"] = _extract_case_id(case_obj, case_path)
        oc = _extract_output_contract(case_obj)
        if oc:
            # Echo every key of the declared contract verbatim, empty defaults.
            echoed = {}
            for k in oc:
                echoed[k] = [] if isinstance(oc[k], list) else None
            result["deliverable"] = echoed.get("deliverable", None)
            result["evidence_map"] = echoed.get("evidence_map", [])
            result["version_boundaries"] = echoed.get("version_boundaries", None)
            for k in ("deliverable", "evidence_map", "version_boundaries"):
                if k not in echoed:
                    result[k] = DEFAULT_CONTRACT[k]
            result["_output_contract"] = echoed
    else:
        result["case_id"] = "unknown_case"
        result["degradations"].append({"where": "input", "detail": "case.json not found"})

    records = evidence.discover_inputs(input_dir)
    if not records:
        result["degradations"].append({"where": "input", "detail": "empty input directory"})
        result["status"] = "insufficient_evidence"
    result["sources"] = [
        {"path": r["rel"], "size": r["size"], "kind": r["kind"]} for r in records
    ]
    result["runtime"]["seconds_elapsed"] = round(time.time() - t0, 2)
    result["change_log"].append({
        "at": started, "action": "scaffold",
        "detail": "written partial result skeleton before analysis",
    })
    return result


def _write_result(output_dir, result):
    path = os.path.join(output_dir, "result.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def _finalize(spec_path, result, input_dir, work_dir, output_dir, status_override):
    """Render and upgrade the scaffold into a final result."""
    t0 = time.time()
    try:
        spec = mindmap.load_spec(spec_path)
    except Exception as e:
        result["errors"].append({"where": "spec", "detail": str(e)})
        result["status"] = "input_error"
        return result

    problems = mindmap.validate_spec(spec)
    for p in problems:
        if p["severity"] == "error":
            result["errors"].append(p)
        else:
            result["degradations"].append(p)

    if any(p["severity"] == "error" for p in problems):
        result["status"] = "input_error"
        result["version_boundaries"] = result["version_boundaries"] or {}
        result["version_boundaries"]["spec_valid"] = False
        result["version_boundaries"]["problems"] = problems
        return result

    try:
        rendered = mindmap.render_all(spec)
    except Exception as e:
        result["errors"].append({"where": "render", "detail": str(e)})
        result["status"] = "input_error"
        return result

    meta = spec.get("meta") or {}
    stem = "mindmap"
    try:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (meta.get("title") or "mindmap"))[:60] or "mindmap"
        stem = safe
    except Exception:
        stem = "mindmap"

    files = {}
    paths = {
        "opml": os.path.join(output_dir, stem + ".opml"),
        "markdown": os.path.join(output_dir, stem + ".md"),
        "svg": os.path.join(output_dir, stem + ".svg"),
    }
    with open(paths["opml"], "w", encoding="utf-8") as fh:
        fh.write(rendered["opml"])
    with open(paths["markdown"], "w", encoding="utf-8") as fh:
        fh.write(rendered["markdown"])
    with open(paths["svg"], "w", encoding="utf-8") as fh:
        fh.write(rendered["svg"])
    files["mindmap_opml"] = os.path.basename(paths["opml"])
    files["mindmap_markdown"] = os.path.basename(paths["markdown"])
    files["preview_svg"] = os.path.basename(paths["svg"])

    # Extract evidence entries from nodes and relations.
    entries = []
    for n in spec.get("nodes", []):
        ev = n.get("evidence") or {}
        if ev.get("locator") or ev.get("quote"):
            entries.append({
                "node_id": n.get("id"),
                "label": n.get("label"),
                "claim": n.get("label"),
                "locator": ev.get("locator"),
                "quote": ev.get("quote"),
                "source": ev.get("source") or ev.get("locator"),
                "kind": ev.get("kind") or "fact",
                "verified": None,
            })
    for r in spec.get("relations", []):
        ev = r.get("evidence") or {}
        if ev.get("locator") or ev.get("quote"):
            entries.append({
                "relation": "%s->%s" % (r.get("from"), r.get("to")),
                "claim": "%s %s %s" % (r.get("from"), r.get("type") or "related", r.get("to")),
                "locator": ev.get("locator"),
                "quote": ev.get("quote"),
                "source": ev.get("source") or ev.get("locator"),
                "kind": ev.get("kind") or "fact",
                "verified": None,
            })

    text_index = _material_text_index(input_dir, work_dir)
    evidence.verify_evidence(entries, text_index)
    conflicts = evidence.detect_conflicts(entries)
    for c in conflicts:
        result["uncertainty"].append(c)

    result["evidence_map"] = entries
    result["deliverable"] = {
        "format": "OPML (editable) + Markdown (readable) + SVG (preview)",
        "purpose": meta.get("purpose"),
        "audience": meta.get("audience"),
    }
    result["deliverable"].update(files)
    result["version_boundaries"] = {
        "spec_valid": True,
        "material_gaps": [],
        "human_confirm": [],
        "anomalies": [],
        "node_count": len(spec.get("nodes", [])),
        "relation_count": len(spec.get("relations", [])),
    }

    if not entries:
        result["version_boundaries"]["material_gaps"].append("no evidence attached to any node/relation")

    missing_quote = [e for e in entries if e.get("verified") == "not_found"]
    for e in missing_quote[:20]:
        result["pending_human"].append({
            "where": e.get("node_id") or e.get("relation"),
            "detail": "quote not found verbatim in material",
            "locator": e.get("locator"),
            "quote": (e.get("quote") or "")[:120],
        })

    # Honest status: ok only if spec is valid and no evidence verification hard failures.
    if missing_quote:
        result["status"] = "partial"
    else:
        result["status"] = "ok"

    if status_override in STATUS_VALUES:
        result["status"] = status_override

    result["runtime"]["seconds_elapsed"] = round(time.time() - t0, 2)
    result["runtime"]["phase"] = "finalize"
    result["change_log"].append({
        "at": _iso_now(), "action": "finalize",
        "detail": "rendered opml/md/svg, verified %d evidence entries" % len(entries),
    })
    return result


def _check(output_dir):
    """Validate an existing output directory. Returns (problems, result)."""
    problems = []
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.exists(result_path):
        return [{"severity": "error", "check": "result.json exists", "detail": "missing"}], None
    try:
        with open(result_path, "rb") as fh:
            result = json.loads(fh.read().decode("utf-8-sig"))
    except Exception as e:
        return [{"severity": "error", "check": "result.json parses", "detail": str(e)}], None

    for field in ("case_id", "status"):
        if not result.get(field):
            problems.append({"severity": "error", "check": field, "detail": "missing/empty"})
    if result.get("status") not in STATUS_VALUES:
        problems.append({"severity": "error", "check": "status", "detail": "invalid %r" % result.get("status")})

    d = result.get("deliverable") or {}
    for key in DELIVERABLE_KEYS:
        p = d.get(key)
        if not p:
            continue
        fp = p if os.path.isabs(p) else os.path.join(output_dir, os.path.basename(p))
        if not os.path.exists(fp):
            problems.append({"severity": "warn", "check": "deliverable %s exists" % key, "detail": p})
            continue
        if key in ("mindmap_opml", "preview_svg"):
            try:
                import xml.etree.ElementTree as ET
                ET.parse(fp)
            except Exception as e:
                problems.append({"severity": "error", "check": "xml parses %s" % key, "detail": str(e)})

    for i, e in enumerate(result.get("evidence_map") or []):
        if not (e.get("locator") or e.get("quote")):
            problems.append({"severity": "warn", "check": "evidence[%d] locator/quote" % i, "detail": "empty"})
    return problems, result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mind-map writing skill: scaffold, finalize, check.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--spec", default=None, help="path to agent-written spec.json")
    ap.add_argument("--status", default=None, choices=STATUS_VALUES)
    ap.add_argument("--check", action="store_true", help="validate existing output only")
    args = ap.parse_args(argv)

    if args.check:
        problems, result = _check(args.output)
        if result is None:
            print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False))
            return 1
        ok = not any(p["severity"] == "error" for p in problems)
        print(json.dumps({"ok": ok, "case_id": result.get("case_id"),
                          "status": result.get("status"), "problems": problems},
                         ensure_ascii=False))
        return 0 if ok else 1

    output_dir = args.output
    work_dir = args.work
    input_dir = args.input
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    # Phase 1: always write the partial skeleton first.
    result = _scaffold(input_dir, output_dir, work_dir)
    result_path = _write_result(output_dir, result)

    # Phase 2: optional finalize from a spec.
    if args.spec:
        result = _finalize(args.spec, result, input_dir, work_dir, output_dir, args.status)
        result_path = _write_result(output_dir, result)

    print(json.dumps({
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "result": result_path,
        "sources": len(result.get("sources") or []),
        "evidence": len(result.get("evidence_map") or []),
        "errors": result.get("errors"),
        "degradations": result.get("degradations"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
