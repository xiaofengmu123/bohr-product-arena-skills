# -*- coding: utf-8 -*-
"""CLI entry point for the academic-polish-02 Skill.

Subcommands
-----------
scaffold   Discover /input, write a *partial* result.json immediately and the
           work-layer skeletons (edits.json / evidence_index.json /
           glossary.json).  This is the "first-3-minutes" requirement.
verify     Run deterministic conservation checks (checks.py) against the work
           layer and write check_report.json + gain metrics.
finalize   Assemble editable artifacts (manuscript.md / glossary.md) and the
           full result.json from the work layer.
selfcheck  Validate final result.json, referenced artifacts, and rubric
           alignment; non-zero exit on failure.

Default paths follow the runtime contract:
    --input  /input       --output  /app/output       --work  /app/work

Standard library only; no network; no model calls; deterministic layout.
The import path is resolved relative to this file, so the working directory
does not matter.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import checks  # noqa: E402  (same directory)

DEFAULT_CONTRACT = {
    # target editable artifact, relative to /app/output
    "deliverable": None,
    # list of {fact, locator, quote} — every number/citation/key claim
    "fact_source_map": [],
    # list of {type, detail, status} — diffs, human confirmations, gaps
    "version_boundary": [],
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml", ".yml",
            ".rst", ".xml", ".html", ".bib"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
SCHEMA_VERSION = "2.0.0"


# ---------------------------------------------------------------------------
# Robust file reading
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
    """Read a UTF-8 text file with BOM/bad-encoding tolerance."""
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError(f"file too large ({size} bytes > {limit})")
    with open(path, "rb") as f:
        raw = f.read()
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, size
        except (UnicodeDecodeError, ValueError) as e:  # pragma: no cover
            last_err = e
            continue
    raise last_err or ValueError(f"cannot decode {path}")


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
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


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover(input_dir):
    """Return (files, case, task_text, errors).  Never raises."""
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
        for key in ("text", "draft", "document", "manuscript", "source_text",
                    "passage", "body", "原文", "底稿"):
            v = case.get(key)
            if isinstance(v, str) and len(v.strip()) > 40:
                candidates.append(("case.json." + key, v))
    for p in files:
        base = os.path.basename(p).lower()
        if base in ("case.json", "task.md", "challenge.json"):
            continue
        ext = os.path.splitext(base)[1]
        if ext not in TEXT_EXT:
            continue
        name_ok = any(k in base for k in
                      ("manuscript", "draft", "abstract", "grant", "reply",
                       "response", "paper", "text", "原文", "底稿", "草稿",
                       "section", "chapter"))
        try:
            txt, _enc, _size = read_text(p)
        except Exception:
            continue
        if name_ok or len(txt) > 200:
            candidates.append((p, txt))
    if not candidates:
        best, best_len = None, -1
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
            if len(txt) > best_len:
                best, best_len = p, len(txt)
        if best:
            txt, _e, _s = read_text(best)
            candidates.append((best, txt))
    # candidates already sorted by priority; pick first non-empty
    for path, txt in candidates:
        if txt and txt.strip():
            return path, txt
    return None, ""


def infer_case_id(case):
    if not isinstance(case, dict):
        return None
    for k in ("id", "case_id", "caseId", "challenge_id"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, int):
            return str(v)
    return None


def source_records(input_dir, files):
    recs = []
    for p in files:
        try:
            size = os.path.getsize(p)
            ext = os.path.splitext(p)[1].lower()
            dtype = "text" if ext in TEXT_EXT else "binary"
            recs.append({
                "path": p, "type": dtype, "bytes": size,
                "digest": file_digest(p),
            })
        except OSError:
            continue
    return recs


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------

def cmd_scaffold(args):
    t0 = time.time()
    files, case, task_text, errors = discover(args.input)
    case_id = infer_case_id(case) or "unknown"
    draft_path, draft_text = find_draft(case, files, task_text)
    contract = DEFAULT_CONTRACT.copy()
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        for k, v in case["output_contract"].items():
            contract[k] = v  # keep unknown keys, fill defaults below

    # Determine degradation signals.
    degradations = list(errors)
    if not files:
        degradations.append("empty input directory")
    if not draft_text.strip():
        degradations.append("no draft text found")
    status = "input_error" if (errors and not files) else "partial"
    if not draft_text.strip() and not errors:
        status = "insufficient_evidence"

    # Editable work-layer skeleton.
    edits = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "target_lang": None,
        "genre": None,
        "source": draft_path,
        "units": [],
    }
    evidence_index = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "facts": [],
        "source_files": source_records(args.input, files),
    }
    glossary = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "terms": [],
    }

    write_json(os.path.join(args.work, "edits.json"), edits)
    write_json(os.path.join(args.work, "evidence_index.json"), evidence_index)
    write_json(os.path.join(args.work, "glossary.json"), glossary)

    # Honest partial result skeleton (first-3-minutes requirement).
    partial = {
        "case_id": case_id,
        "status": status,
        "sources": source_records(args.input, files),
        "uncertainty": [],
        "change_log": [{
            "phase": "0 scaffold", "at_epoch_s": round(time.time(), 1),
            "action": "wrote partial result skeleton",
        }],
        "pending_human": [],
        "errors": list(errors),
        "degradations": list(dict.fromkeys(degradations)),
        "runtime": {"elapsed_s": round(time.time() - t0, 2)},
        "gain_metrics": None,
    }
    # Fill contract fields into partial (missing -> null/[]).
    for k in contract:
        partial[k] = contract[k]
    write_json(os.path.join(args.output, "result.json"), partial)
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def cmd_verify(args):
    t0 = time.time()
    edits = read_json(os.path.join(args.work, "edits.json"))
    glossary = read_json(os.path.join(args.work, "glossary.json"))
    evidence = read_json(os.path.join(args.work, "evidence_index.json"))
    if not isinstance(edits, dict):
        edits = {"units": [], "_read_error": "edits.json missing"}
    if not isinstance(glossary, dict):
        glossary = {"terms": []}
    if not isinstance(evidence, dict):
        evidence = {"facts": []}

    # Reconstruct full original from units (fallback: empty).
    full_original = "\n".join(u.get("original", "")
                              for u in edits.get("units", []))
    findings = checks.run_all(edits, glossary, full_original)
    counts, verdict = checks.summarize(findings)
    gain = checks.compute_gain_metrics(edits, findings)

    report = {
        "schema_version": SCHEMA_VERSION,
        "case_id": edits.get("case_id") or evidence.get("case_id"),
        "verdict": verdict,
        "counts": counts,
        "findings": findings,
        "gain_metrics": gain,
        "runtime_s": round(time.time() - t0, 2),
    }
    write_json(os.path.join(args.work, "check_report.json"), report)
    print(verdict)
    return 0


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def _render_manuscript(edits):
    units = edits.get("units", []) if isinstance(edits, dict) else []
    lines = []
    lines.append("# 润色稿（可编辑）")
    lines.append("")
    lines.append("> 本稿由逐句编辑记录自动组装；请以 `result.json` 的 "
                 "`version_boundary` 与 `pending_human` 为最终边界。")
    lines.append("")
    for u in units:
        oid = u.get("id", "")
        orig = u.get("original", "")
        rev = u.get("revised", "")
        etype = u.get("edit_type", "untyped")
        lines.append(f"## {oid}  [{etype}]")
        lines.append("")
        lines.append("**原文**")
        lines.append("")
        lines.append(orig)
        lines.append("")
        lines.append("**改文**")
        lines.append("")
        lines.append(rev)
        lines.append("")
        reason = u.get("reason", "")
        if reason:
            lines.append("**修改理由**")
            lines.append("")
            lines.append(reason)
            lines.append("")
        hedge = u.get("hedge_note", "")
        if hedge:
            lines.append("**限定词说明**")
            lines.append("")
            lines.append(hedge)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_glossary(glossary):
    terms = glossary.get("terms", []) if isinstance(glossary, dict) else []
    lines = ["# 术语表（可编辑）", ""]
    if not terms:
        lines.append("（暂无术语；请在 `glossary.json` 中补充后重新 finalize。）")
        return "\n".join(lines) + "\n"
    lines.append("| 概念 | 规范写法 | 禁用/变体 | 缩写 | 首次出现定位 |")
    lines.append("|---|---|---|---|---|")
    for t in terms:
        lines.append("| {concept} | {canonical} | {variants} | {abbr} | {loc} |".format(
            concept=(t.get("concept") or "").replace("|", "\\|"),
            canonical=(t.get("canonical") or "").replace("|", "\\|"),
            variants=", ".join(t.get("variants") or []).replace("|", "\\|"),
            abbr=(t.get("abbr") or "").replace("|", "\\|"),
            loc=(t.get("first_use_locator") or "").replace("|", "\\|"),
        ))
    return "\n".join(lines) + "\n"


def cmd_finalize(args):
    t0 = time.time()
    edits = read_json(os.path.join(args.work, "edits.json"))
    glossary = read_json(os.path.join(args.work, "glossary.json"))
    report = read_json(os.path.join(args.work, "check_report.json"))
    files, case, task_text, errors = discover(args.input)
    case_id = (edits.get("case_id") if isinstance(edits, dict) else None) \
        or infer_case_id(case) or "unknown"

    if not isinstance(edits, dict):
        edits = {"units": [], "case_id": case_id}
    if not isinstance(glossary, dict):
        glossary = {"terms": []}

    contract = DEFAULT_CONTRACT.copy()
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        for k, v in case["output_contract"].items():
            contract[k] = v

    # Assemble editable artifacts.
    manuscript = _render_manuscript(edits)
    glos = _render_glossary(glossary)
    with open(os.path.join(args.output, "manuscript.md"), "w",
              encoding="utf-8") as f:
        f.write(manuscript)
    with open(os.path.join(args.output, "glossary.md"), "w",
              encoding="utf-8") as f:
        f.write(glos)

    # Status decision from verify report.
    counts = (report or {}).get("counts", {})
    errs = counts.get("error", 0)
    status = "ok" if errs == 0 else "partial"
    if not edits.get("units") and not task_text.strip():
        status = "insufficient_evidence"

    result = {
        "case_id": case_id,
        "status": status,
        "sources": source_records(args.input, files),
        "uncertainty": [],
        "change_log": [
            {"phase": "0 scaffold", "at_epoch_s": round(time.time(), 1),
             "action": "wrote partial result skeleton"},
            {"phase": "5 finalize", "at_epoch_s": round(time.time(), 1),
             "action": "assembled manuscript.md + glossary.md + result.json"},
        ],
        "pending_human": [],
        "errors": list(errors),
        "degradations": [],
        "runtime": {"elapsed_s": round(time.time() - t0, 2)},
        "gain_metrics": (report or {}).get("gain_metrics", {}),
        "check_counts": counts,
    }
    # Fill contract fields (preserve unknown keys, missing -> null/[]).
    for k in contract:
        result[k] = contract[k]
    # Point deliverable at the assembled manuscript when not already set.
    if result.get("deliverable") in (None, "", [], {}):
        result["deliverable"] = "manuscript.md"
    write_json(os.path.join(args.output, "result.json"), result)
    return 0


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------

def cmd_selfcheck(args):
    failures = []
    result = read_json(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict):
        failures.append("result.json missing or not an object")
        print("SELFCHECK FAIL")
        print("\n".join(failures))
        return 1
    if not result.get("case_id"):
        failures.append("case_id empty")
    if result.get("status") not in STATUSES:
        failures.append(f"status illegal: {result.get('status')!r}")

    # Contract completeness: every key of DEFAULT_CONTRACT + output_contract
    # must be present in result.json (missing -> null/[] acceptable).
    for k in DEFAULT_CONTRACT:
        if k not in result:
            failures.append(f"output_contract field missing: {k}")

    deliverable = result.get("deliverable")
    if isinstance(deliverable, str) and deliverable:
        dp = os.path.join(args.output, deliverable)
        if not os.path.isfile(dp):
            failures.append(f"deliverable not found: {deliverable}")
    elif deliverable:
        failures.append(f"deliverable has unexpected type: {type(deliverable)!r}")

    # Rubric alignment proof.
    rubric = checks.rubric_alignment_report()
    if any(f["level"] == "error" for f in rubric):
        failures.append("rubric alignment incomplete")

    # References must exist when shipped.
    ref_dir = os.path.join(os.path.dirname(HERE), "references")
    if not os.path.isdir(ref_dir):
        failures.append("references/ directory missing")

    if failures:
        print("SELFCHECK FAIL")
        print("\n".join("- " + f for f in failures))
        return 1
    print("SELFCHECK OK")
    print(f"case_id={result['case_id']} status={result['status']} "
          f"deliverable={result.get('deliverable')}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py", description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--output", default="/app/output")
    common.add_argument("--work", default="/app/work")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scaffold", parents=[common],
                   help="discover input, write partial skeleton")
    sub.add_parser("verify", parents=[common],
                   help="run deterministic conservation checks")
    sub.add_parser("finalize", parents=[common],
                   help="assemble artifacts and result.json")
    sub.add_parser("selfcheck", parents=[common],
                   help="validate final result and artifacts")
    args = p.parse_args(argv)

    for d in (args.output, args.work):
        os.makedirs(d, exist_ok=True)

    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    if args.cmd == "selfcheck":
        return cmd_selfcheck(args)
    p.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
