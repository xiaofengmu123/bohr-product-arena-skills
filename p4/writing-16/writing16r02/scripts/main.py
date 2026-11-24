#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bootstrap entry point for the research-writing transfer skill.

Deterministic, standard-library only. This script does NOT write the paper;
it prepares a grounded workspace and writes an honest, complete result
skeleton as early as possible, so any later interruption still leaves a
valid ``/app/output/result.json``.

Usage:
    python3 scripts/main.py --input /input --work /app/work --output /app/output
    python3 scripts/main.py --help

Exit code is 0 on every handled condition (including an empty input
directory, which yields ``status=insufficient_evidence``). A non-zero exit
means the script itself could not even write the skeleton.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:  # sibling module; works when run as "python3 scripts/main.py"
    from validate import RESULT_CONTRACT, STATUSES
except Exception:  # pragma: no cover - fallback keeps the script runnable
    RESULT_CONTRACT = {
        "case_id": str, "status": str, "deliverables": list,
        "evidence_map": list, "sources": list, "uncertainty": dict,
        "change_log": list, "pending_human": list, "material_gaps": list,
        "errors": list, "degradations": list, "runtime": dict,
    }
    STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB per source file; larger files are capped

TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".tex", ".xml",
             ".yaml", ".yml", ".rst", ".bib"}
MATERIAL_EXTS = TEXT_EXTS | {".pdf", ".docx", ".pptx", ".xlsx", ".html"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_text(path, cap=MAX_TEXT_BYTES):
    """Read a text file with BOM/encoding tolerance. Returns (text, degraded)."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return "", f"read_failed: {exc}"
    if len(raw) > cap:
        raw = raw[:cap]
        degraded = "truncated_at_{}_bytes".format(cap)
    else:
        degraded = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), degraded
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), (degraded or "lossy_decode")


def safe_json(path):
    """Load JSON tolerantly; returns dict/list or None on failure."""
    text, _ = read_text(path)
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except Exception:
        # Try to salvage a JSON object by dropping trailing junk.
        for end in range(len(text), 0, -1):
            for closer in ("}", "]"):
                if text[end - 1] == closer:
                    try:
                        return json.loads(text[:end])
                    except Exception:
                        break
            break
        return None


def scan_input(input_dir):
    """Enumerate files, tagging case.json / task.md vs. materials."""
    root = Path(input_dir)
    if not root.exists():
        return None, [], [], []
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            entries.append((p, size))
    case_files, task_files, materials = [], [], []
    for p, size in entries:
        name = p.name.lower()
        rel = str(p.relative_to(root))
        if name == "case.json":
            case_files.append((p, rel, size))
        elif name in ("task.md", "task.txt") or rel.endswith("task.md"):
            task_files.append((p, rel, size))
        elif p.suffix.lower() in MATERIAL_EXTS:
            materials.append((p, rel, size))
    return root, case_files, task_files, materials


def get_case_id(case):
    """Resolve the case identifier without assuming a single key name."""
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return None


def extract_requirements(case, task_texts):
    """Heuristic, non-authoritative extraction of task constraints.

    Returns a dict of candidate strings plus a confidence flag. The agent must
    confirm every value against the original files before writing.
    """
    req = {
        "audience": None, "genre": None, "format": None,
        "length_limit": None, "language": None, "source_files": [],
        "explicit_keys": [],
    }
    pool = []
    if isinstance(case, dict):
        for key in ("task", "instruction", "requirement", "brief", "output_contract"):
            v = case.get(key)
            if isinstance(v, str):
                pool.append(v)
            elif isinstance(v, (dict, list)):
                pool.append(json.dumps(v, ensure_ascii=False))
    pool.extend(task_texts)

    def pick(needles, text):
        low = text.lower()
        for n in needles:
            i = low.find(n.lower())
            if i >= 0:
                seg = text[i:i + 400]
                return seg.strip()
        return None

    all_text = "\n".join(pool)
    req["audience"] = pick(("受众", "读者", "audience", "target reader", "面向"), all_text)
    req["genre"] = pick(("体裁", "文体", "genre", "格式", "format"), all_text)
    req["format"] = pick(("输出格式", "deliverable", "产物", "output format"), all_text)
    req["length_limit"] = pick(("字数", "篇幅", "length", "word limit", "不超过"), all_text)
    req["language"] = pick(("语言", "language", "英文", "中文", "双语"), all_text)

    if isinstance(case, dict):
        for key in case.keys():
            if isinstance(key, str) and key.lower() not in {
                "id", "case_id", "challenge_id", "content", "metadata"}:
                req["explicit_keys"].append(key)
    req["_confidence"] = "heuristic; confirm against original files before writing"
    return req


def build_skeleton(case_id, req, materials, start, errors, degradations):
    """Assemble a valid result.json with every contract key populated."""
    skel = {}
    for key, typ in RESULT_CONTRACT.items():
        if typ is dict:
            skel[key] = {}
        elif typ is list:
            skel[key] = []
        else:
            skel[key] = None
    skel["case_id"] = case_id
    skel["status"] = "partial"
    skel["sources"] = [{"path": m[1], "size_bytes": m[2]} for m in materials]
    skel["errors"] = errors
    skel["degradations"] = degradations
    skel["runtime"] = {
        "start": start, "end": None, "seconds": None,
        "stage": "bootstrap_skeleton",
    }
    return skel


def write_json(path, obj):
    """Write JSON atomically-ish: write to a temp file then replace."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bootstrap a grounded result skeleton.")
    ap.add_argument("--input", default="/input", help="runtime input dir")
    ap.add_argument("--work", default="/app/work", help="scratch/work dir")
    ap.add_argument("--output", default="/app/output", help="final output dir")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing result.json skeleton")
    args = ap.parse_args(argv)

    start = now_iso()
    t0 = time.time()
    errors, degradations = [], []

    Path(args.work).mkdir(parents=True, exist_ok=True)
    Path(args.output).mkdir(parents=True, exist_ok=True)

    out_result = Path(args.output) / "result.json"
    if out_result.exists() and not args.force:
        try:
            existing = json.loads(out_result.read_text(encoding="utf-8"))
            print("result.json already exists; leaving it untouched (use --force to rebuild).")
            print("status:", existing.get("status"), "case_id:", existing.get("case_id"))
            return 0
        except Exception:
            pass  # unreadable skeleton -> rebuild it below

    root, case_files, task_files, materials = scan_input(args.input)
    if root is None:
        # Input directory missing entirely.
        case_id = None
        req = {"audience": None, "genre": None, "format": None,
               "length_limit": None, "language": None, "source_files": [],
               "explicit_keys": [], "_confidence": "input directory missing"}
        status = "insufficient_evidence"
        errors.append("input_dir_missing: {}".format(args.input))
    else:
        case_id = None
        case = None
        if case_files:
            case = safe_json(case_files[0][0])
            case_id = get_case_id(case)
            if case is None:
                errors.append("case.json_unparseable: {}".format(case_files[0][1]))
        elif not task_files and not materials:
            errors.append("empty_input_dir: no case.json/task.md/materials found")
        task_texts, tdegs = [], []
        for p, rel, _ in task_files:
            text, deg = read_text(p)
            task_texts.append(text)
            if deg:
                tdegs.append("{}: {}".format(rel, deg))
        req = extract_requirements(case, task_texts)
        degradations.extend(tdegs)
        status = "partial" if (case_files or task_files or materials) else "insufficient_evidence"

    skeleton = build_skeleton(case_id, req, materials, start, errors, degradations)
    skeleton["status"] = status
    skeleton["uncertainty"] = {
        "claim_strength_note": "populated by the writing agent; see SKILL.md",
        "unresolved": [],
    }
    skeleton["pending_human"] = list(errors)

    # Requirements snapshot for the agent to read incrementally.
    req_path = Path(args.work) / "requirements.json"
    write_json(req_path, {"case_id": case_id, "requirements": req,
                          "materials": [m[1] for m in materials]})

    write_json(out_result, skeleton)
    secs = round(time.time() - t0, 3)
    print("wrote skeleton:", out_result)
    print("case_id:", case_id, "| status:", status, "| materials:", len(materials),
          "| elapsed_s:", secs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
