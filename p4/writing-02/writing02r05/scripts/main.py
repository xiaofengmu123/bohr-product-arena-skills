# -*- coding: utf-8 -*-
"""CLI entry point for the academic-language polishing Skill.

Subcommands
-----------
scaffold   Discover /input, extract the output_contract, and immediately write
           a *partial* result.json skeleton (first-3-minutes requirement) plus
           the editable work files the model fills next.
verify     Run the deterministic conservation/contract checks in checks.py
           against /app/work/edits.json (+ optional result.json) and write
           /app/work/check_report.json.
finalize   Assemble the editable Markdown artifacts (manuscript.md, glossary.md)
           and the full result.json, aligning every output_contract field and
           appending to the change log.
selfcheck  Validate the final result.json (parseable, case_id, status, contract
           mirror, envelope) and that referenced artifacts exist on disk.

Default paths follow the runtime contract:
    --input /input       --output /app/output       --work /app/work
Run from the Skill root so ``scripts/checks.py`` is importable; the import path
is resolved relative to this file, so the working directory does not matter.

Standard library only; no network; no model calls; deterministic layout.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import checks  # noqa: E402  (same directory)

DEFAULT_CONTRACT = {
    "deliverable": None,          # editable main artifact, relative to /app/output
    "fact_source_map": [],        # list of {fact, locator, quote}
    "version_boundary": [],       # list of {type, detail, status}
}

# Envelope keys merged on top of the contract mirror in every result.json.
REQUIRED_ENVELOPE = [
    "case_id", "status", "sources", "uncertainty", "change_log",
    "pending_human", "errors", "degradations", "runtime",
]

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml", ".yml",
            ".rst", ".xml", ".html"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
CONTRACT_KEYS = ("output_contract", "contract", "result_schema",
                 "expected_output", "output_schema")
DRAFT_KEYS = ("text", "draft", "document", "manuscript", "source_text",
              "passage", "body")


# ---------------------------------------------------------------------------
# Robust file I/O
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
    """Read a UTF-8 text file with BOM / bad-encoding tolerance."""
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError(f"file too large ({size} bytes > {limit})")
    raw = open(path, "rb").read()
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, size
        except (UnicodeDecodeError, ValueError) as e:  # pragma: no cover
            last_err = e
            continue
    raise last_err or ValueError(f"cannot decode {path}")


def write_json(path, obj):
    """Write a JSON object with deterministic, diff-friendly layout."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {"_read_error": str(e)}


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def now_iso():
    """Wall-clock timestamp; recorded only as measurement metadata."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarize(value, limit=120):
    """Compact, deterministic description of a value for the change log."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    if isinstance(value, (list, tuple)):
        return f"{type(value).__name__}[{len(value)}]"
    if isinstance(value, dict):
        return f"dict[{len(value)} keys]"
    return value


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover(input_dir):
    """Return (files, case, task_text, errors). Never raises."""
    files, errors = [], []
    if not os.path.isdir(input_dir):
        return files, None, "", [f"input directory missing: {input_dir}"]
    for root, _dirs, names in os.walk(input_dir):
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.isfile(p) and not n.startswith("."):
                files.append(p)
    case = None
    task_text = ""
    for p in files:
        base = os.path.basename(p).lower()
        if base == "case.json":
            parsed = read_json(p)
            if isinstance(parsed, dict) and "_read_error" in parsed:
                errors.append(f"case.json unreadable: {parsed['_read_error']}")
            elif isinstance(parsed, dict):
                case = parsed
            else:
                errors.append("case.json is not a JSON object")
        elif base == "task.md":
            try:
                task_text, _, _ = read_text(p)
            except Exception as e:  # pragma: no cover
                errors.append(f"task.md unreadable: {e}")
    return files, case, task_text, errors


def find_draft(case, files, task_text):
    """Heuristically locate the manuscript to polish. Returns (path, text)."""
    candidates = []
    if isinstance(case, dict):
        for key in DRAFT_KEYS:
            v = case.get(key)
            if isinstance(v, str) and len(v.strip()) > 40:
                candidates.append((f"case.json.{key}", v))
    for p in files:
        base = os.path.basename(p).lower()
        if base in ("case.json", "task.md", "challenge.json"):
            continue
        ext = os.path.splitext(base)[1]
        if ext not in TEXT_EXT:
            continue
        name_ok = any(k in base for k in
                      ("manuscript", "draft", "abstract", "grant", "reply",
                       "response", "paper", "text", "\u539f\u6587",
                       "\u5e95\u7a3f", "\u8349\u7a3f"))
        try:
            txt, _enc, _size = read_text(p)
        except Exception:
            continue
        if name_ok or len(txt) > 200:
            candidates.append((p, txt))
    if not candidates:
        best = None
        for p in files:
            base = os.path.basename(p).lower()
            if base in ("case.json", "task.md", "challenge.json"):
                continue
            if os.path.splitext(p)[1] not in TEXT_EXT:
                continue
            try:
                txt, _e, _s = read_text(p)
            except Exception:
                continue
            if best is None or len(txt) > len(best[1]):
                best = (p, txt)
        if best:
            candidates.append(best)
    return candidates[0] if candidates else (None, "")


# ---------------------------------------------------------------------------
# Contract extraction and alignment
# ---------------------------------------------------------------------------

def _try_json(s):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_json_object(text):
    """Extract the first balanced JSON object found in free text."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return _try_json(text[start:i + 1])
    return None


def extract_contract(case, task_text):
    """Find the output_contract from case.json or task.md; fall back to default.

    Returns ``(contract, source)`` where ``source`` records where it came from.
    """
    if isinstance(case, dict):
        for key in CONTRACT_KEYS:
            v = case.get(key)
            if isinstance(v, dict):
                return v, f"case.json.{key}"
            if isinstance(v, str):
                parsed = _try_json(v)
                if isinstance(parsed, dict):
                    return parsed, f"case.json.{key}"
    if task_text:
        for key in ("output_contract", "\u8f93\u51fa\u5951\u7ea6",
                    "Output contract", "output contract"):
            idx = task_text.find(key)
            if idx < 0:
                continue
            parsed = _extract_json_object(task_text[idx:idx + 8000])
            if isinstance(parsed, dict):
                return parsed, f"task.md@{key}"
    return dict(DEFAULT_CONTRACT), "default"


def neutralize(value):
    """Turn a contract example into a null/[]-typed skeleton.

    Lists become empty lists; dicts keep their keys with neutralized values;
    scalars become None (serialized as null). The raw contract is retained in
    /app/work/contract.json so the model can see the item schema.
    """
    if isinstance(value, dict):
        return {k: neutralize(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def build_result(contract, case_id, status="partial"):
    """Build the result envelope over a neutralized contract mirror."""
    result = neutralize(contract)
    result["case_id"] = case_id
    result["status"] = status
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["pending_human"] = []
    result["errors"] = []
    result["degradations"] = []
    result["runtime"] = {}
    return result


def append_change_log(result, stage, field, before, after, reason):
    """Append one entry to the append-only change log (never mutates others)."""
    entry = {
        "time": now_iso(),
        "stage": stage,
        "field": field,
        "before": summarize(before),
        "after": summarize(after),
        "reason": reason,
    }
    log = result.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    log.append(entry)
    return result


def deep_update(base, override):
    """Recursively overlay override onto base, returning base (mutated)."""
    for k, v in override.items():
        if (k in base and isinstance(base[k], dict) and isinstance(v, dict)):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_scaffold(args):
    files, case, task_text, errors = discover(args.input)
    contract, contract_source = extract_contract(case, task_text)
    case_id = ""
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or case.get("caseId") or ""

    draft_path, draft_text = find_draft(case, files, task_text)
    status = "partial"
    if not draft_text.strip():
        status = "insufficient_evidence"

    result = build_result(contract, case_id or None, status)
    result["runtime"] = {
        "started_at": now_iso(),
        "stage": "scaffold",
        "budget_seconds": args.budget_seconds,
        "budget_requests": args.budget_requests,
    }
    for e in errors:
        result["degradations"].append(e)
    append_change_log(
        result, "scaffold", "result", None, status,
        f"contract source: {contract_source}; envelope + contract skeleton written",
    )

    # Persist the raw contract (schema reference) and editable work files.
    write_json(os.path.join(args.work, "contract.json"),
               {"source": contract_source, "contract": contract})
    edits = {
        "target_lang": None,
        "genre": None,
        "draft_path": draft_path,
        "units": [],
    }
    write_json(os.path.join(args.work, "edits.json"), edits)
    write_json(os.path.join(args.work, "glossary.json"), {"terms": []})
    write_json(os.path.join(args.work, "evidence_index.json"), {"items": []})
    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.work, "contract_values.json"),
               {"_note": "model fills contract field values here; see contract.json"})

    print(f"[scaffold] status={status} case_id={case_id or '<none>'}")
    print(f"[scaffold] contract source: {contract_source}")
    print(f"[scaffold] draft: {draft_path or '<none>'}")
    print(f"[scaffold] wrote {os.path.join(args.output, 'result.json')}")
    return 0


def cmd_verify(args):
    edits = read_json(os.path.join(args.work, "edits.json")) or {}
    glossary = read_json(os.path.join(args.work, "glossary.json")) or {}
    contract_meta = read_json(os.path.join(args.work, "contract.json")) or {}
    contract = contract_meta.get("contract") if isinstance(contract_meta, dict) else None

    result = read_json(os.path.join(args.output, "result.json"))
    report = checks.run_checks(edits, glossary, contract, result)
    write_json(os.path.join(args.work, "check_report.json"), report)

    n_err = len(report["errors"])
    n_warn = len(report["warnings"])
    print(f"[verify] errors={n_err} warnings={n_warn} units={report['stats']['units']}")
    for e in report["errors"][:10]:
        print(f"  E: {e}")
    for w in report["warnings"][:10]:
        print(f"  W: {w}")
    return 0


def cmd_finalize(args):
    edits = read_json(os.path.join(args.work, "edits.json")) or {}
    glossary = read_json(os.path.join(args.work, "glossary.json")) or {}
    evidence = read_json(os.path.join(args.work, "evidence_index.json")) or {}
    contract_meta = read_json(os.path.join(args.work, "contract.json")) or {}
    contract = contract_meta.get("contract") if isinstance(contract_meta, dict) else None
    result = read_json(os.path.join(args.output, "result.json")) or {}
    if not isinstance(result, dict) or "change_log" not in result:
        files, case, task_text, _ = discover(args.input)
        case_id = case.get("case_id") or case.get("id") or case.get("caseId") if isinstance(case, dict) else ""
        result = build_result(contract, case_id, "partial")

    # Auto-populate the three default contract fields when they exist.
    values = read_json(os.path.join(args.work, "contract_values.json")) or {}
    if not isinstance(values, dict):
        values = {}
    # Drop internal keys (e.g. "_note") so they never leak into result.json.
    values = {k: v for k, v in values.items() if not k.startswith("_")}
    auto = {}
    if isinstance(contract, dict):
        if "deliverable" in contract and "deliverable" not in values:
            auto["deliverable"] = "manuscript.md"
        if "fact_source_map" in contract and "fact_source_map" not in values:
            items = evidence.get("items") if isinstance(evidence, dict) else []
            auto["fact_source_map"] = items if isinstance(items, list) else []
        if "version_boundary" in contract and "version_boundary" not in values:
            auto["version_boundary"] = _derive_version_boundary(edits, result)
    deep_update(result, auto)
    deep_update(result, values)
    write_json(os.path.join(args.work, "contract_values.json"), values)

    # Assemble artifacts.
    manuscript = _build_manuscript(edits)
    write_text(os.path.join(args.output, "manuscript.md"), manuscript)
    terms = glossary.get("terms") if isinstance(glossary, dict) else []
    if terms:
        write_text(os.path.join(args.output, "glossary.md"), _build_glossary(glossary))
    append_change_log(
        result, "finalize", "result", "partial", _final_status(result),
        "artifacts assembled; contract fields aligned from edits/evidence/values",
    )
    result["runtime"]["stage"] = "finalize"
    result["runtime"]["finished_at"] = now_iso()
    write_json(os.path.join(args.output, "result.json"), result)
    print("[finalize] wrote manuscript.md + result.json"
          + (" + glossary.md" if terms else ""))
    return 0


def _final_status(result):
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return "partial"
    return result.get("status", "partial")


def _derive_version_boundary(edits, result):
    boundary = []
    units = edits.get("units") if isinstance(edits, dict) else []
    for u in units or []:
        if not isinstance(u, dict):
            continue
        boundary.append({
            "type": u.get("edit_type") or "edit",
            "detail": {
                "id": u.get("id"),
                "original": u.get("original"),
                "revised": u.get("revised"),
                "reason": u.get("reason"),
            },
            "status": "auto",
        })
    for item in (result.get("pending_human") or []):
        boundary.append({"type": "pending_human", "detail": item, "status": "open"})
    return boundary


def _build_manuscript(edits):
    """Render polished text plus a per-sentence edit table."""
    units = edits.get("units") if isinstance(edits, dict) else []
    lines = ["# 润色稿", ""]
    lang = edits.get("target_lang") or "unknown"
    genre = edits.get("genre") or "unknown"
    lines.append(f"- target_lang: {lang}")
    lines.append(f"- genre: {genre}")
    lines.append("")
    lines.append("## 润色后的完整文本")
    lines.append("")
    for u in units or []:
        if isinstance(u, dict) and (u.get("revised") or "").strip():
            lines.append((u.get("revised") or "").strip())
            lines.append("")
    lines.append("## 逐句修改说明")
    lines.append("")
    lines.append("| # | 原文 | 改文 | 修改类型 | 理由 |")
    lines.append("|---|---|---|---|---|")
    for u in units or []:
        if not isinstance(u, dict):
            continue
        cells = [
            str(u.get("id", "")),
            _cell(u.get("original")),
            _cell(u.get("revised")),
            _cell(u.get("edit_type")),
            _cell(u.get("reason")),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _cell(v):
    if v is None:
        return ""
    return str(v).replace("|", "\\|").replace("\n", " ")


def _build_glossary(glossary):
    terms = glossary.get("terms") if isinstance(glossary, dict) else []
    lines = ["# 术语表", ""]
    for t in terms or []:
        if not isinstance(t, dict):
            continue
        lines.append(f"- **{t.get('term', '')}** — {t.get('canonical', '')}")
        if t.get("first_use"):
            lines.append(f"  - 首次出现: {t['first_use']}")
        if t.get("variants"):
            lines.append(f"  - 异体写法: {', '.join(t['variants'])}")
    return "\n".join(lines) + "\n"


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def cmd_selfcheck(args):
    result = read_json(os.path.join(args.output, "result.json"))
    problems = []
    if not isinstance(result, dict) or "_read_error" in result:
        problems.append("result.json unreadable or not an object")
        print("SELFCHECK FAIL")
        for p in problems:
            print(f"  - {p}")
        return 0
    contract_meta = read_json(os.path.join(args.work, "contract.json")) or {}
    contract = contract_meta.get("contract") if isinstance(contract_meta, dict) else None
    for fn, needs_contract in ((checks.check_contract, True),
                               (checks.check_change_log, False),
                               (checks.check_envelope, False)):
        out = fn(result, contract) if needs_contract else fn(result)
        problems.extend(out["errors"])
    # referenced artifacts exist
    for field in ("deliverable",):
        if field in result and isinstance(result[field], str) and result[field]:
            p = os.path.join(args.output, result[field])
            if not os.path.isfile(p):
                problems.append(f"deliverable missing on disk: {p}")
    if problems:
        print("SELFCHECK FAIL")
        for p in problems[:20]:
            print(f"  - {p}")
    else:
        print("SELFCHECK OK")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Academic-language polishing Skill: scaffold/verify/finalize/selfcheck.")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--budget-seconds", type=int, default=900)
    p.add_argument("--budget-requests", type=int, default=64)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("scaffold")
    sub.add_parser("verify")
    sub.add_parser("finalize")
    sub.add_parser("selfcheck")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "scaffold": cmd_scaffold,
        "verify": cmd_verify,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
