#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Results & Discussion chapter writing — robust, standard-library pipeline.

Subcommands
-----------
read    Normalize /input (case.json, task.md, materials) into input_summary.json.
build   Emit or merge a legally-shaped result.json skeleton: every declared
        output_contract field plus status / sources / uncertainty /
        pending_human / change_log / degradations / errors / runtime.
check   Validate result.json: structure, contract fields, evidence locators,
        overclaim phrases, change_log shape.
quote   Extract a verbatim quote from a material file by locator (L12, L12-15,
        P3, "Fig 2", "表 3").
run     read -> build -> check in one pass.

Robustness contract (this variant's focus)
------------------------------------------
* Every command prints a JSON envelope to stdout and exits 0. Degradation is
  reported inside the envelope, never as an unhandled traceback.
* Empty input, bad JSON, bad encoding (BOM / gb18030 / latin-1 fallback),
  missing arguments and oversized files all degrade to a legal, honest result.
* Deterministic: sorted file discovery, stable key order, no randomness.
  Only change_log timestamps vary between runs.
* Standard library only; no third-party imports.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone

VERSION = "1.0.0"
MAX_FILE_BYTES = 2_000_000          # per-file text read cap; oversized -> skip/truncate
QUOTE_CAP_CHARS = 4000              # cap for a single verbatim quote

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"

STATUS_VALUES = {
    "in_progress", "partial", "complete", "failed",
    "insufficient_evidence", "input_error",
}

# Canonical minimum contract fields even when case.json declares nothing.
DEFAULT_CONTRACT_FIELDS = ["deliverable", "facts_sources", "version_boundaries"]

# Overclaim phrases that should not appear without explicit, sourced evidence.
FORBIDDEN_PHRASES = [
    "首次", "国际领先", "世界领先", "全球领先", "填补空白", "颠覆性",
    "革命性", "首创", "世界第一", "全球第一", "无与伦比", "史无前例",
    "前所未有", "彻底解决", "完全解决",
]

USAGE = """usage: python scripts/main.py <subcommand> [options]

subcommands:
  read   read -> input_summary.json
  build  input_summary.json -> result.json skeleton (supports --patch)
  check  validate result.json
  quote  extract a verbatim quote by locator
  run    read -> build -> check (one pass)

Run 'python scripts/main.py <subcommand> --help' for options.
"""


# --------------------------------------------------------------------------- #
# utilities
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text_file(path: str, max_bytes: int = MAX_FILE_BYTES):
    """Read a file as text with encoding fallback and size guard.

    Returns (ok: bool, text: str, error: str, truncated: bool, encoding: str|None).
    Never raises.
    """
    if not path or not os.path.exists(path):
        return (False, "", "file not found", False, None)
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return (False, "", str(exc), False, None)
    if os.path.isdir(path):
        return (False, "", "path is a directory", False, None)
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes) if size > max_bytes else fh.read()
    except OSError as exc:
        return (False, "", str(exc), size > max_bytes, None)
    truncated = size > max_bytes
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return (True, data.decode(enc), "", truncated, enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return (False, "", "undecodable bytes", truncated, None)


def looks_binary(data: bytes) -> bool:
    """Heuristic: NUL byte or a high ratio of control bytes => binary."""
    sample = data[:8192]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    nontext = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return (nontext / len(sample)) > 0.30


def load_json(path: str):
    """Parse a JSON file; return (ok: bool, value, error: str). Never raises."""
    ok, text, err, _trunc, _enc = read_text_file(path, MAX_FILE_BYTES)
    if not ok:
        return (False, None, f"cannot read {path}: {err}")
    try:
        return (True, json.loads(text), "")
    except (json.JSONDecodeError, ValueError) as exc:
        return (False, None, f"bad JSON in {path}: {exc}")


def write_json(obj, path: str) -> str:
    """Write JSON deterministically; return the path or raise OSError."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def contract_keys(contract) -> list:
    """Return field names declared by an output_contract in any common shape.

    Accepts a dict (field -> type), a list of strings, or a list of dicts with
    'name'/'field' keys. Always appends the canonical minimum fields.
    """
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.append(item.get("name") or item.get("field") or item.get("key"))
    for f in DEFAULT_CONTRACT_FIELDS:
        if f not in keys:
            keys.append(f)
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def field_template(field: str):
    templates = {
        "deliverable": {"file": None, "files": [], "format": None, "note": None},
        "facts_sources": [],
        "version_boundaries": {
            "changes": [], "human_confirm": [], "gaps": [], "anomalies": [],
        },
    }
    return templates.get(field, None)


def initial_status(summary: dict) -> str:
    """Honest starting status given what the input scan found."""
    if summary.get("case_id") is None:
        errors = summary.get("errors", [])
        if any("bad JSON" in e or "unusable" in e for e in errors):
            return "input_error"
        return "insufficient_evidence"
    return "in_progress"


def parse_locator(locator: str):
    """Parse a locator string into (kind, payload).

    Kinds: 'line' (single), 'range' (line range), 'para' (paragraph),
    'caption' (search token), 'whole' (full file). Returns (None, None) if
    unparseable (callers treat that as whole-file).
    """
    if not locator:
        return ("whole", None)
    s = locator.strip()
    m = re.match(r"^(?:#)?L(\d+)\s*-\s*L?(\d+)$", s, re.I)
    if m:
        return ("range", (int(m.group(1)), int(m.group(2))))
    m = re.match(r"^(?:#)?L(\d+)$", s, re.I)
    if m:
        return ("line", int(m.group(1)))
    m = re.match(r"^(?:#)?P(\d+)$", s, re.I)
    if m:
        return ("para", int(m.group(1)))
    # caption search: "Fig 2", "Table 3", "Figure 2a", "图 2", "表 3"
    m = re.match(r"^(?:#)?(fig(?:ure)?|table|图|表)\s*([0-9a-zA-Z]+)$", s, re.I)
    if m:
        return ("caption", (m.group(1).lower(), m.group(2)))
    return ("whole", None)


def split_paragraphs(text: str) -> list:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_read(argv):
    p = argparse.ArgumentParser(prog="main.py read")
    p.add_argument("--case", default=None, help="default: <input-dir>/case.json")
    p.add_argument("--task", default=None, help="default: <input-dir>/task.md")
    p.add_argument("--input-dir", default=DEFAULT_INPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    args = p.parse_args(argv)
    case_path = args.case or os.path.join(args.input_dir, "case.json")
    task_path = args.task or os.path.join(args.input_dir, "task.md")

    summary = {
        "case_id": None,
        "output_contract": {},
        "contract_fields": list(DEFAULT_CONTRACT_FIELDS),
        "case_file": case_path,
        "task_file": task_path,
        "case_meta": {},
        "task_excerpt": "",
        "materials": [],
        "sources": [],
        "skipped": [],
        "errors": [],
        "degradations": [],
    }

    # 1. case.json
    ok, case_obj, err = load_json(case_path)
    if ok and isinstance(case_obj, dict):
        summary["case_id"] = case_obj.get("case_id") or case_obj.get("id")
        contract = case_obj.get("output_contract") or case_obj.get("contract") or {}
        summary["output_contract"] = contract
        summary["contract_fields"] = contract_keys(contract)
        summary["case_meta"] = {
            k: case_obj.get(k) for k in ("title", "task", "audience", "genre",
                                         "format", "language", "instructions")
            if case_obj.get(k) is not None
        }
    else:
        summary["errors"].append(f"case.json unusable: {err}")
        summary["degradations"].append("case.json missing or unparseable; "
                                       "contract fell back to canonical minimum.")

    # 2. task.md
    ok, task_text, err, trunc, _enc = read_text_file(task_path, MAX_FILE_BYTES)
    if ok:
        summary["task_excerpt"] = task_text[:4000]
        if trunc:
            summary["degradations"].append("task.md truncated at 2 MB.")
    else:
        summary["errors"].append(f"task.md unusable: {err}")

    # 3. materials: everything else under the input dir.
    exclude = {os.path.basename(case_path), os.path.basename(task_path)}
    mat_paths = []
    if os.path.isdir(args.input_dir):
        try:
            names = sorted(os.listdir(args.input_dir))
        except OSError as exc:
            summary["errors"].append(f"cannot list {args.input_dir}: {exc}")
            names = []
        for name in names:
            if name in exclude:
                continue
            mat_paths.append(os.path.join(args.input_dir, name))

    for path in mat_paths:
        if os.path.isdir(path):
            summary["skipped"].append({"file": path, "reason": "directory"})
            continue
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            summary["skipped"].append({"file": path, "reason": str(exc)})
            continue
        if size > MAX_FILE_BYTES:
            summary["skipped"].append({
                "file": path, "reason": "oversized",
                "size": size, "limit": MAX_FILE_BYTES,
            })
            summary["degradations"].append(
                f"oversized material skipped: {os.path.basename(path)}")
            continue
        try:
            with open(path, "rb") as fh:
                head = fh.read(8192)
        except OSError as exc:
            summary["skipped"].append({"file": path, "reason": str(exc)})
            continue
        if looks_binary(head):
            summary["materials"].append({
                "file": path, "kind": "binary",
                "hint": "PDF/image; parse with platform pdf-text-tool/ocr-tool",
                "size": size,
            })
            summary["sources"].append({"file": path, "kind": "binary"})
            continue
        ok, text, err, _trunc, enc = read_text_file(path, MAX_FILE_BYTES)
        if not ok:
            summary["skipped"].append({"file": path, "reason": err})
            summary["errors"].append(f"material unreadable {path}: {err}")
            continue
        line_count = text.count("\n") + 1
        para_count = len(split_paragraphs(text))
        summary["materials"].append({
            "file": path, "kind": "text", "encoding": enc, "size": size,
            "lines": line_count, "paragraphs": para_count,
            "preview": text[:600],
        })
        summary["sources"].append({
            "file": path, "kind": "text", "lines": line_count,
        })

    out_path = os.path.join(args.work, "input_summary.json")
    os.makedirs(args.work, exist_ok=True)
    write_json(summary, out_path)
    print(json.dumps({
        "ok": True, "summary_path": out_path,
        "case_id": summary["case_id"],
        "contract_fields": summary["contract_fields"],
        "materials": len(summary["materials"]),
        "skipped": len(summary["skipped"]),
        "errors": summary["errors"],
        "degradations": summary["degradations"],
    }, ensure_ascii=False, indent=2))


def _load_summary(path):
    ok, value, err = load_json(path)
    if not ok or not isinstance(value, dict):
        return {"case_id": None, "output_contract": {},
                "contract_fields": list(DEFAULT_CONTRACT_FIELDS),
                "sources": [], "errors": [f"summary unusable: {err}"]}
    return value


def cmd_build(argv):
    p = argparse.ArgumentParser(prog="main.py build")
    p.add_argument("--summary", default="/app/work/input_summary.json")
    p.add_argument("--out", default=DEFAULT_OUTPUT)
    p.add_argument("--patch", action="store_true",
                   help="merge into an existing result.json instead of overwriting")
    args = p.parse_args(argv)

    summary = _load_summary(args.summary)
    fields = summary.get("contract_fields") or contract_keys(summary.get("output_contract"))
    fields = list(dict.fromkeys(fields + DEFAULT_CONTRACT_FIELDS))

    skeleton = {"case_id": summary.get("case_id")}
    for f in fields:
        skeleton[f] = field_template(f)
    skeleton.update({
        "status": initial_status(summary),
        "sources": summary.get("sources", []),
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": now_iso(), "action": "skeleton_created",
            "detail": "result.json skeleton written; fill contract fields incrementally.",
        }],
        "degradations": summary.get("degradations", []),
        "errors": summary.get("errors", []),
        "runtime": {
            "started_at": now_iso(),
            "budget_s": 900,
            "budget_requests": 64,
            "tokens_per_response": 16384,
        },
    })

    if args.patch and os.path.exists(args.out):
        ok, existing, err = load_json(args.out)
        if ok and isinstance(existing, dict):
            merged = dict(existing)
            for f in fields:
                merged.setdefault(f, field_template(f))
            for key, val in skeleton.items():
                if key not in merged or merged[key] in (None, [], {}):
                    if key in ("sources", "errors", "degradations"):
                        merged[key] = val
                    elif key == "change_log":
                        merged.setdefault(key, [])
                    else:
                        merged.setdefault(key, val)
            merged["status"] = merged.get("status") or "in_progress"
            merged.setdefault("change_log", []).append({
                "time": now_iso(), "action": "patched",
                "detail": "merged skeleton fields into existing result.json.",
            })
            skeleton = merged
        else:
            skeleton.setdefault("change_log", []).append({
                "time": now_iso(), "action": "patch_failed",
                "detail": f"existing result.json unreadable, overwrote: {err}",
            })

    write_json(skeleton, args.out)
    print(json.dumps({
        "ok": True, "result_path": args.out,
        "case_id": skeleton.get("case_id"),
        "status": skeleton.get("status"),
        "fields": fields,
        "note": "skeleton ready; fill deliverable / facts_sources / "
                "version_boundaries, then re-run build --patch or write directly.",
    }, ensure_ascii=False, indent=2))


def _iter_deliverable_files(result):
    """Yield paths listed by a deliverable field in several common shapes."""
    d = result.get("deliverable")
    if not d:
        return
    if isinstance(d, dict):
        for key in ("files", "file"):
            v = d.get(key)
            if isinstance(v, str):
                yield v
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        yield item
                    elif isinstance(item, dict) and item.get("file"):
                        yield item["file"]
    elif isinstance(d, str):
        yield d


def cmd_check(argv):
    p = argparse.ArgumentParser(prog="main.py check")
    p.add_argument("--result", default=DEFAULT_OUTPUT)
    p.add_argument("--summary", default="/app/work/input_summary.json")
    args = p.parse_args(argv)

    issues, warnings = [], []
    ok, result, err = load_json(args.result)
    if not ok or not isinstance(result, dict):
        issues.append(f"result.json unusable: {err}")
        print(json.dumps({"passed": False, "issues": issues,
                          "warnings": warnings, "status": None},
                         ensure_ascii=False, indent=2))
        return

    summary = _load_summary(args.summary)
    fields = summary.get("contract_fields") or contract_keys(summary.get("output_contract"))
    fields = list(dict.fromkeys(fields + DEFAULT_CONTRACT_FIELDS))

    if not result.get("case_id"):
        issues.append("missing case_id")
    if result.get("status") not in STATUS_VALUES:
        issues.append(f"status not in {sorted(STATUS_VALUES)}: {result.get('status')!r}")
    for f in fields:
        if f not in result:
            issues.append(f"missing declared contract field: {f}")

    # Evidence records: every key claim needs locator + verbatim quote.
    fs = result.get("facts_sources", [])
    if isinstance(fs, list):
        for i, item in enumerate(fs):
            if not isinstance(item, dict):
                warnings.append(f"facts_sources[{i}] is not an object")
                continue
            if not item.get("locator"):
                warnings.append(f"facts_sources[{i}] missing locator")
            if not item.get("quote"):
                warnings.append(f"facts_sources[{i}] missing verbatim quote")
            st = item.get("status")
            if st not in (None, "verified", "unverified", "pending_human", "not_reported"):
                warnings.append(f"facts_sources[{i}] unusual status {st!r}")
    elif fs:
        warnings.append("facts_sources is not a list")

    # Overclaim scan across deliverable text files.
    scanned = 0
    for fpath in _iter_deliverable_files(result):
        fok, text, ferr, _trunc, _enc = read_text_file(fpath, MAX_FILE_BYTES)
        if not fok:
            warnings.append(f"deliverable unreadable {fpath}: {ferr}")
            continue
        scanned += 1
        for phrase in FORBIDDEN_PHRASES:
            if phrase in text:
                warnings.append(f"overclaim phrase in {fpath}: {phrase!r}")

    # change_log shape.
    cl = result.get("change_log", [])
    if isinstance(cl, list):
        for i, entry in enumerate(cl):
            if not isinstance(entry, dict) or "time" not in entry or "action" not in entry:
                warnings.append(f"change_log[{i}] missing time/action")
    else:
        issues.append("change_log is not a list")

    for key in ("sources", "uncertainty", "pending_human"):
        if key in result and not isinstance(result[key], list):
            issues.append(f"{key} is not a list")

    # Completeness sanity: complete/partial should carry a deliverable file.
    if result.get("status") in ("complete", "partial"):
        has_file = any(_iter_deliverable_files(result))
        if not has_file:
            warnings.append("status complete/partial but no deliverable file listed")

    report = {
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "status": result.get("status"),
        "contract_fields_checked": fields,
        "deliverable_files_scanned": scanned,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_quote(argv):
    p = argparse.ArgumentParser(prog="main.py quote")
    p.add_argument("--file", required=True)
    p.add_argument("--locator", default="")
    args = p.parse_args(argv)

    ok, text, err, _trunc, _enc = read_text_file(args.file, MAX_FILE_BYTES)
    if not ok:
        print(json.dumps({"ok": False, "file": args.file, "error": err},
                         ensure_ascii=False, indent=2))
        return
    lines = text.splitlines()
    kind, payload = parse_locator(args.locator)

    if kind == "line":
        idx = payload - 1
        quote = lines[idx] if 0 <= idx < len(lines) else ""
        if not quote:
            print(json.dumps({"ok": False, "file": args.file,
                              "error": "line out of range"}, ensure_ascii=False))
            return
    elif kind == "range":
        a, b = payload
        if a > b:
            a, b = b, a
        quote = "\n".join(lines[a - 1:b])
    elif kind == "para":
        paras = split_paragraphs(text)
        idx = payload - 1
        quote = paras[idx] if 0 <= idx < len(paras) else ""
    elif kind == "caption":
        token, num = payload
        if token in ("fig", "figure"):
            word = r"fig(?:ure)?"
        elif token == "table":
            word = r"table"
        else:
            word = re.escape(token)  # 图 / 表
        pat = re.compile(r"^\s*" + word + r"\s*" + re.escape(num) +
                         r"(?![0-9A-Za-z])", re.I)
        quote = ""
        for i, ln in enumerate(lines):
            if pat.search(ln):
                block = [ln]
                j = i + 1
                while j < len(lines) and lines[j].strip():
                    block.append(lines[j])
                    j += 1
                quote = "\n".join(block)
                break
        if not quote:
            print(json.dumps({"ok": False, "file": args.file,
                              "error": f"caption {args.locator!r} not found"},
                             ensure_ascii=False))
            return
    else:  # whole
        quote = text

    truncated = len(quote) > QUOTE_CAP_CHARS
    if truncated:
        quote = quote[:QUOTE_CAP_CHARS]
    print(json.dumps({"ok": True, "file": args.file, "locator": args.locator,
                      "quote": quote, "truncated": truncated},
                     ensure_ascii=False, indent=2))


def cmd_run(argv):
    import io
    from contextlib import redirect_stdout

    p = argparse.ArgumentParser(prog="main.py run")
    p.add_argument("--case", default=None, help="default: <input-dir>/case.json")
    p.add_argument("--task", default=None, help="default: <input-dir>/task.md")
    p.add_argument("--input-dir", default=DEFAULT_INPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    p.add_argument("--out", default=DEFAULT_OUTPUT)
    args = p.parse_args(argv)

    steps = {}
    for name, fn, fnargv in [
        ("read", cmd_read,
         ["--case", args.case, "--task", args.task,
          "--input-dir", args.input_dir, "--work", args.work]),
        ("build", cmd_build,
         ["--summary", os.path.join(args.work, "input_summary.json"),
          "--out", args.out]),
        ("check", cmd_check,
         ["--result", args.out,
          "--summary", os.path.join(args.work, "input_summary.json")]),
    ]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(fnargv)
        txt = buf.getvalue().strip()
        try:
            steps[name] = json.loads(txt) if txt else None
        except (json.JSONDecodeError, ValueError):
            steps[name] = {"_raw": txt[:2000]}
    print(json.dumps({"ok": True, "steps": steps}, ensure_ascii=False, indent=2))


HANDLERS = {
    "read": cmd_read, "build": cmd_build, "check": cmd_check,
    "quote": cmd_quote, "run": cmd_run,
}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        print(VERSION)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    handler = HANDLERS.get(cmd)
    if handler is None:
        print(json.dumps({"ok": False, "error": f"unknown subcommand {cmd!r}"},
                         ensure_ascii=False))
        print(USAGE)
        return 0
    try:
        handler(rest)
    except SystemExit:
        # argparse printed its own usage; keep the process graceful.
        return 0
    except Exception as exc:  # last-resort grace; never crash the caller
        print(json.dumps({
            "ok": False, "error": str(exc),
            "trace": traceback.format_exc(limit=8)[:2000],
        }, ensure_ascii=False))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
