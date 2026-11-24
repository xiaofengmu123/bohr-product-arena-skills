#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grant-proposal-writer helper: skeleton + manifest + verify (Python stdlib only).

Subcommands:
  init      write a minimal valid /app/output/result.json (first-3-minute partial)
  manifest  scan /input and dump a source manifest (relative path, size, sha256)
  verify    validate result.json structure; exit 0 = ok, 1 = broken

Paths default to /input, /app/output, /app/work on the pinned runtime image;
on a machine without those directories, fall back to <skill>/input|output|work.
All file I/O is UTF-8(-sig)-tolerant; bad JSON is recorded, never fatal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

# Force UTF-8 stdout/stderr so Chinese paths never crash a GBK console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SEVEN_ELEMENTS = (
    "立项依据", "科学问题", "研究目标", "研究内容",
    "创新点", "技术路线", "研究基础与可行性",
)

REQUIRED_KEYS = (
    "case_id", "status", "artifact", "fact_source_map",
    "version_boundary", "sources", "uncertainty", "change_log",
)

VALID_STATUS = ("done", "partial", "failed", "in_progress")
MAX_FILE = 2_000_000  # 2MB per-file cap; larger files are skipped and recorded


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def input_dir() -> str:
    return "/input" if os.path.isdir("/input") else os.path.join(ROOT, "input")


def output_dir() -> str:
    return "/app/output" if os.path.isdir("/app") else os.path.join(ROOT, "output")


def work_dir() -> str:
    return "/app/work" if os.path.isdir("/app") else os.path.join(ROOT, "work")


def read_json(path: str):
    if not os.path.exists(path):
        return None, "missing: %s" % path
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, "bad json (%s): %s" % (path, exc)


def extract_contract(case) -> dict:
    """Flatten output_contract/output_schema/required_fields into top-level hints."""
    if not isinstance(case, dict):
        return {}
    hint: dict = {}
    for key in ("output_contract", "output_schema", "required_fields"):
        val = case.get(key)
        if isinstance(val, dict):
            for k, v in val.items():
                hint.setdefault(k, v)
        elif isinstance(val, (list, tuple)):
            for item in val:
                if isinstance(item, str):
                    hint.setdefault(item, None)
                elif isinstance(item, dict):
                    for k, v in item.items():
                        hint.setdefault(k, v)
    return hint


def scan_manifest(inp: str) -> list:
    items = []
    if not os.path.isdir(inp):
        return items
    for root, _dirs, files in os.walk(inp):
        for fn in sorted(files):
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, inp).replace("\\", "/")
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                items.append({"path": rel, "size": None, "sha256": None, "error": str(exc)})
                continue
            if size > MAX_FILE:
                items.append({"path": rel, "size": size, "sha256": None,
                              "skipped": "size>%d" % MAX_FILE})
                continue
            digest = hashlib.sha256()
            try:
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        digest.update(chunk)
                items.append({"path": rel, "size": size, "sha256": digest.hexdigest()})
            except OSError as exc:
                items.append({"path": rel, "size": size, "sha256": None, "error": str(exc)})
    return items


def build_skeleton(case_id: str, hint=None) -> dict:
    sk = {
        "case_id": case_id,
        "status": "partial",
        "artifact": {"path": "", "format": "md", "description": ""},
        "element_map": {
            e: {"section_path": "", "claims": [], "temporal": ""} for e in SEVEN_ELEMENTS
        },
        "consistency_matrix": {
            "goals": [], "content": [], "route": [], "indicators": [], "mismatches": [],
        },
        "fact_source_map": [],
        "version_boundary": {
            "modification_diff": "", "manual_confirm": [], "material_gaps": [], "anomalies": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {"time": now_iso(), "action": "init_skeleton",
             "reason": "first-3-minute partial delivery; incremental updates follow",
             "impact": "minimal valid result.json written"},
        ],
    }
    if hint:
        sk["contract_hint"] = hint
        for k, v in hint.items():
            if k not in sk:
                sk[k] = v if v is not None else None
    return sk


def cmd_init(args) -> int:
    out = args.out or output_dir()
    work = args.work or work_dir()
    inp = args.input or input_dir()
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    case, err = read_json(os.path.join(inp, "case.json"))
    case_id = "unknown"
    if isinstance(case, dict):
        case_id = str(case.get("case_id") or case.get("id")
                      or case.get("challenge_id") or "unknown")
    hint = extract_contract(case) if isinstance(case, dict) else {}
    sk = build_skeleton(case_id, hint)
    if err and not isinstance(case, dict):
        sk["errors"] = [err]
    sk["sources"] = scan_manifest(inp)

    result_path = os.path.join(out, "result.json")
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(sk, fh, ensure_ascii=False, indent=2)

    meta = {"case_id": sk["case_id"], "input_dir": inp, "output_dir": out,
            "work_dir": work, "init_at": now_iso(), "result": result_path}
    with open(os.path.join(work, "run_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    print(json.dumps({"ok": True, "result": result_path,
                      "case_id": sk["case_id"], "status": sk["status"],
                      "sources": len(sk["sources"])}, ensure_ascii=False))
    return 0


def cmd_manifest(args) -> int:
    inp = args.input or input_dir()
    work = args.work or work_dir()
    os.makedirs(work, exist_ok=True)
    items = scan_manifest(inp)
    with open(os.path.join(work, "source_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"generated_at": now_iso(), "input_dir": inp, "files": items},
                  fh, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "files": len(items)}, ensure_ascii=False))
    return 0


def cmd_verify(args) -> int:
    result = args.result or os.path.join(output_dir(), "result.json")
    data, err = read_json(result)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    if not isinstance(data, dict):
        print(json.dumps({"ok": False, "error": "result.json is not an object"},
                         ensure_ascii=False))
        return 1
    missing = [k for k in REQUIRED_KEYS if k not in data]
    status = data.get("status")
    problems = list(missing)
    if status not in VALID_STATUS:
        problems.append("invalid status: %r" % status)
    if status == "done":
        ap = (data.get("artifact") or {}).get("path") or ""
        if not ap:
            problems.append("status=done but artifact.path is empty")
    res = {"ok": not problems, "problems": problems, "status": status,
           "fact_map_count": len(data.get("fact_source_map") or []),
           "sources_count": len(data.get("sources") or [])}
    print(json.dumps(res, ensure_ascii=False))
    return 0 if not problems else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="grant-proposal-writer helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="write partial result.json skeleton")
    p.add_argument("--input", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--work", default=None)
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("manifest", help="scan /input and dump source manifest")
    p.add_argument("--input", default=None)
    p.add_argument("--work", default=None)
    p.set_defaults(func=cmd_manifest)
    p = sub.add_parser("verify", help="validate result.json structure")
    p.add_argument("--result", default=None)
    p.set_defaults(func=cmd_verify)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
