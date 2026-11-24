# -*- coding: utf-8 -*-
"""CLI entry point for the academic-language polishing Skill.

Subcommands
-----------
scaffold   Discover /input, build the evidence index and write a *partial*
           result.json skeleton immediately (first-3-minutes requirement).
verify     Run the deterministic conservation checks in evidence.py against
           /app/work/edits.json and /app/work/glossary.json.
finalize   Assemble editable Markdown artifacts and the full result.json.
selfcheck  Validate the final result.json and referenced artifacts.

Default paths follow the runtime contract:
    --input  /input       --output  /app/output       --work  /app/work
Run from the Skill root so that ``scripts/evidence.py`` is importable, or run
anywhere and pass ``--work`` explicitly (the import path is resolved relative
to this file, so the working directory does not matter).

Standard library only; no network; no model calls; deterministic layout.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import evidence  # noqa: E402  (same directory)

DEFAULT_CONTRACT = {
    "deliverable": None,
    "fact_source_map": [],
    "version_boundary": [],
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml", ".yml",
            ".rst", ".xml", ".html", ".latex"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
_ABBREV = {"fig", "figs", "eq", "eqs", "et al", "e.g", "i.e", "cf", "vs",
           "approx", "ref", "refs", "no", "nos", "al", "resp", "etc"}


# ---------------------------------------------------------------------------
# Robust file reading.
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
    """Read a UTF-8 text file with BOM/bad-encoding tolerance."""
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError("file too large (%d bytes > %d)" % (size, limit))
    raw = open(path, "rb").read()
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, size
        except (UnicodeDecodeError, ValueError) as e:
            last_err = e
            continue
    raise last_err or ValueError("cannot decode %s" % path)


def write_json(path, obj):
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
# Input discovery.
# ---------------------------------------------------------------------------

def discover(input_dir):
    """Return (files, case, task_text, errors). Never raises."""
    files, errors = [], []
    if not os.path.isdir(input_dir):
        return files, None, "", ["input directory missing: %s" % input_dir]
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
                errors.append("case.json unreadable: %s" % parsed["_read_error"])
            else:
                case = parsed
        elif base == "task.md":
            try:
                task_text, _e, _s = read_text(p)
            except Exception as exc:  # noqa: BLE001
                errors.append("task.md unreadable: %s" % exc)
    return files, case, task_text, errors


def find_draft(case, files):
    """Heuristically locate the manuscript to polish. Returns list of
    (path, text) candidates in priority order."""
    candidates = []
    if isinstance(case, dict):
        for key in ("text", "draft", "document", "manuscript", "source_text",
                    "passage", "body", "content"):
            v = case.get(key)
            if isinstance(v, str) and len(v.strip()) > 40:
                candidates.append(("case.json.%s" % key, v))
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
                       "材料", "正文"))
        try:
            txt, _e, _s = read_text(p)
        except Exception:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001
                continue
            if best is None or len(txt) > len(best[1]):
                best = (p, txt)
        if best:
            candidates.append(best)
    return candidates


# ---------------------------------------------------------------------------
# Sentence splitting (span-aware, avoids "Fig. 1" / decimals / abbreviations).
# ---------------------------------------------------------------------------

def split_sentences(text):
    spans = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ".!?。！？":
            # abbreviation check: token before the period
            j = i
            while j > start and not text[j - 1].isspace() and \
                    text[j - 1] not in "()[]{}\"'":
                j -= 1
            token = text[j:i].lower().rstrip(".")
            if token in _ABBREV:
                i += 1
                continue
            # decimal check
            if ch == "." and i > 0 and i + 1 < n and \
                    text[i - 1].isdigit() and text[i + 1].isdigit():
                i += 1
                continue
            end = i + 1
            while end < n and text[end] in "\"')]》”』":
                end += 1
            span_text = text[start:end].strip()
            if span_text:
                spans.append((start, end, span_text))
            i = end
            while i < n and text[i].isspace():
                i += 1
            start = i
            continue
        i += 1
    if start < n:
        tail = text[start:].strip()
        if tail:
            spans.append((start, n, tail))
    return spans


def _rel_path(path):
    return path.replace("\\", "/")


# ---------------------------------------------------------------------------
# Subcommand implementations.
# ---------------------------------------------------------------------------

def cmd_scaffold(args):
    t0 = time.time()
    out_dir, work_dir = args.output, args.work
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    files, case, task_text, errs = discover(args.input)

    # Determine output_contract early (preserve unknown keys verbatim).
    contract = dict(DEFAULT_CONTRACT)
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        oc = case["output_contract"]
        contract = dict(oc)
        for k in DEFAULT_CONTRACT:
            contract.setdefault(k, DEFAULT_CONTRACT[k])

    case_id = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or case.get("caseId")

    sources = []
    for p in files:
        try:
            size = os.path.getsize(p)
            sources.append({
                "path": _rel_path(p), "type": "text"
                if os.path.splitext(p)[1] in TEXT_EXT else "binary",
                "bytes": size, "digest": file_digest(p),
            })
        except OSError as exc:  # noqa: BLE001
            errs.append("unreadable source %s: %s" % (p, exc))

    drafts = find_draft(case, files)
    degradations = list(errs)

    result = {
        "case_id": case_id,
        "status": "partial",
        "deliverable": "manuscript.md",
        "fact_source_map": [],
        "version_boundary": [],
        "evidence_map": [],
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": list(errs),
        "degradations": degradations,
        "runtime": {"scaffold_seconds": round(time.time() - t0, 2)},
    }
    # Preserve every unknown output_contract key verbatim (no rename/deletion).
    for k, v in contract.items():
        if k not in result:
            result[k] = v

    edits = {"units": [], "meta": {"input": args.input, "generator": "scaffold"}}
    glossary = []
    evidence_index = {"records": [], "by_kind": {}}

    if not drafts:
        result["status"] = "insufficient_evidence"
        result["pending_human"].append(
            "no draft text found in /input (inline or material files)")
        result["deliverable"] = None
    else:
        draft_path, draft_text = drafts[0]
        result["sources"].append({
            "path": _rel_path(draft_path), "type": "draft",
            "bytes": len(draft_text.encode("utf-8")),
            "digest": hashlib.sha256(
                draft_text.encode("utf-8")).hexdigest()[:16],
        })
        records = evidence.extract_evidence(draft_text, _rel_path(draft_path))
        evidence_index["records"] = records
        by_kind = {}
        for r in records:
            by_kind.setdefault(r["kind"], []).append(r["id"])
        evidence_index["by_kind"] = {k: sorted(v) for k, v in by_kind.items()}
        result["evidence_map"] = records

        glossary = evidence.extract_glossary(draft_text, _rel_path(draft_path))
        result["change_log"].append({
            "step": "scaffold",
            "detail": "evidence index built: %d records across %d kinds"
                      % (len(records), len(by_kind)),
        })

        spans = split_sentences(draft_text)
        units = []
        for idx, (s, e, txt) in enumerate(spans, 1):
            ev_ids = [r["id"] for r in records
                      if r["offset"][0] >= s and r["offset"][1] <= e]
            units.append({
                "id": "u-%03d" % idx,
                "original": txt,
                "revised": "",
                "edit_type": None,
                "reason": "",
                "evidence_ids": ev_ids,
                "note": "",
            })
        edits["units"] = units
        result["change_log"].append({
            "step": "scaffold",
            "detail": "%d sentence units created as edit template" % len(units),
        })

    write_json(os.path.join(work_dir, "edits.json"), edits)
    write_json(os.path.join(work_dir, "glossary.json"), glossary)
    write_json(os.path.join(work_dir, "evidence_index.json"), evidence_index)
    result["runtime"]["scaffold_seconds"] = round(time.time() - t0, 2)
    write_json(os.path.join(out_dir, "result.json"), result)
    print(json.dumps({
        "ok": True, "status": result["status"],
        "units": len(edits["units"]),
        "evidence": len(evidence_index["records"]),
        "glossary": len(glossary),
        "errors": errs,
    }, ensure_ascii=False))
    return 0


def cmd_verify(args):
    work_dir, out_dir = args.work, args.output
    edits = read_json(os.path.join(work_dir, "edits.json"))
    glossary = read_json(os.path.join(work_dir, "glossary.json"))
    report = {"errors": [], "warnings": [], "stats": {}, "glossary": {}}
    if edits is None or not isinstance(edits, dict):
        report["errors"].append({"type": "edits_missing",
                                 "detail": "edits.json absent or invalid"})
    else:
        units = edits.get("units", [])
        filled = [u for u in units if (u.get("revised") or "").strip()]
        report["stats"]["units"] = len(units)
        report["stats"]["filled"] = len(filled)
        report["stats"]["unfilled"] = len(units) - len(filled)
        # every unit must have a revision, an edit_type and evidence linkage
        for u in units:
            uid = u.get("id") or "unit?"
            if not (u.get("revised") or "").strip():
                report["errors"].append({
                    "unit": uid, "type": "unit_unfilled",
                    "detail": "revised text is empty"})
            if not u.get("edit_type"):
                report["errors"].append({
                    "unit": uid, "type": "edit_type_missing",
                    "detail": "edit_type is empty"})
            if not u.get("evidence_ids"):
                report["warnings"].append({
                    "unit": uid, "type": "no_evidence_link",
                    "detail": "no evidence record linked to this unit"})
            elif not u.get("reason"):
                report["warnings"].append({
                    "unit": uid, "type": "reason_missing",
                    "detail": "no reason recorded for edit"})
        cons = evidence.verify_conservation(units)
        report["errors"].extend(cons["errors"])
        report["warnings"].extend(cons["warnings"])
        report["stats"].update(cons["stats"])
    if isinstance(glossary, list):
        dups = [k for k, c in Counter(
            (g.get("abbr", "").lower() for g in glossary if g.get("abbr"))).items()
                if c > 1]
        report["glossary"]["entries"] = len(glossary)
        report["glossary"]["duplicate_abbr"] = dups
    write_json(os.path.join(work_dir, "check_report.json"), report)
    print(json.dumps({
        "ok": len(report["errors"]) == 0,
        "errors": len(report["errors"]),
        "warnings": len(report["warnings"]),
        "stats": report["stats"],
    }, ensure_ascii=False))
    return 0


def cmd_finalize(args):
    work_dir, out_dir = args.work, args.output
    os.makedirs(out_dir, exist_ok=True)
    edits = read_json(os.path.join(work_dir, "edits.json")) or {"units": []}
    glossary = read_json(os.path.join(work_dir, "glossary.json")) or []
    result = read_json(os.path.join(out_dir, "result.json")) or {}
    check = read_json(os.path.join(work_dir, "check_report.json")) or {}

    units = edits.get("units", [])
    polished = "\n\n".join((u.get("revised") or u.get("original") or "").strip()
                           for u in units)
    manuscript = [
        "# Polished manuscript",
        "",
        "> Generated by the academic-polish Skill. Editable Markdown.",
        "",
        "## Polished full text",
        "",
        polished,
        "",
        "## Sentence-by-sentence edit log",
        "",
        "| # | Original | Revised | Edit type | Reason | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    for u in units:
        o = (u.get("original") or "").replace("|", "\\|").replace("\n", " ")
        r = (u.get("revised") or "").replace("|", "\\|").replace("\n", " ")
        t = (u.get("edit_type") or "").replace("|", "\\|")
        rs = (u.get("reason") or "").replace("|", "\\|")
        ev = ",".join(u.get("evidence_ids") or [])
        manuscript.append("| %s | %s | %s | %s | %s | %s |"
                          % (u.get("id"), o, r, t, rs, ev))
    with open(os.path.join(out_dir, "manuscript.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(manuscript) + "\n")

    if glossary:
        gmd = ["# Glossary", "",
               "| Abbreviation | Full form | Locator | Quote | Status |",
               "|---|---|---|---|---|"]
        for g in glossary:
            gmd.append("| %s | %s | %s | %s | %s |" % (
                (g.get("abbr") or "").replace("|", "\\|"),
                (g.get("full") or "").replace("|", "\\|"),
                (g.get("locator") or "").replace("|", "\\|"),
                (g.get("quote") or "").replace("|", "\\|"),
                (g.get("status") or "auto").replace("|", "\\|")))
        with open(os.path.join(out_dir, "glossary.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(gmd) + "\n")

    # Rebuild fact_source_map (contract-compliant) from the evidence map.
    fact_map = []
    for r in result.get("evidence_map", []):
        fact_map.append({
            "fact": "%s: %s" % (r.get("kind"), r.get("quote")),
            "locator": r.get("locator"),
            "quote": r.get("quote"),
        })
    result["fact_source_map"] = fact_map

    version_boundary = []
    for u in units:
        if (u.get("revised") or "").strip() and \
                (u.get("revised") or "") != (u.get("original") or ""):
            version_boundary.append({
                "type": "diff",
                "id": u.get("id"),
                "detail": "edited: %s" % (u.get("edit_type") or "unspecified"),
            })
    for w in check.get("warnings", []):
        version_boundary.append({
            "type": "human_confirm", "unit": w.get("unit"),
            "detail": "%s: %s" % (w.get("type"), w.get("detail")),
        })
    for g in result.get("pending_human", []):
        version_boundary.append({"type": "gap", "detail": g})
    result["version_boundary"] = version_boundary

    n_errs = len(check.get("errors", []))
    n_warn = len(check.get("warnings", []))
    if result.get("status") not in ("insufficient_evidence", "input_error"):
        if n_errs == 0 and all((u.get("revised") or "").strip() for u in units):
            result["status"] = "ok"
        else:
            result["status"] = "partial"
    result["errors"] = check.get("errors", [])
    result["uncertainty"] = [
        {"type": w.get("type"), "unit": w.get("unit"), "detail": w.get("detail")}
        for w in check.get("warnings", [])
    ]
    result["change_log"] = (result.get("change_log") or [])
    result["change_log"].append({
        "step": "finalize",
        "detail": "errors=%d warnings=%d; manuscript.md assembled" % (n_errs, n_warn),
    })
    result["runtime"] = result.get("runtime") or {}
    write_json(os.path.join(out_dir, "result.json"), result)
    print(json.dumps({"ok": True, "status": result["status"],
                      "errors": n_errs, "warnings": n_warn}, ensure_ascii=False))
    return 0


def cmd_selfcheck(args):
    out_dir, work_dir = args.output, args.work
    problems = []
    result = read_json(os.path.join(out_dir, "result.json"))
    if result is None:
        problems.append("result.json missing")
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False))
        return 1
    if not isinstance(result, dict):
        problems.append("result.json is not an object")
    else:
        status = result.get("status")
        if status not in STATUSES:
            problems.append("invalid status: %r" % status)
        if status not in ("insufficient_evidence", "input_error"):
            if not result.get("case_id"):
                problems.append("case_id empty")
            if not result.get("deliverable"):
                problems.append("deliverable empty")
            else:
                fp = os.path.join(out_dir, result["deliverable"])
                if not os.path.isfile(fp):
                    problems.append("deliverable missing: %s" % result["deliverable"])
        if "fact_source_map" not in result:
            problems.append("fact_source_map missing (output_contract)")
        if "version_boundary" not in result:
            problems.append("version_boundary missing (output_contract)")
        if "sources" not in result:
            problems.append("sources missing")
        if "change_log" not in result:
            problems.append("change_log missing")
        if not isinstance(result.get("evidence_map"), list):
            problems.append("evidence_map missing or not a list")
    for fname in ("edits.json", "glossary.json", "evidence_index.json"):
        if not os.path.isfile(os.path.join(work_dir, fname)):
            problems.append("work artifact missing: %s" % fname)
    ok = len(problems) == 0
    print(json.dumps({"ok": ok, "status": (result or {}).get("status"),
                      "problems": problems}, ensure_ascii=False))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Academic-language polishing Skill (evidence-tracing).")
    p.add_argument("--input", default="/input", help="input directory")
    p.add_argument("--output", default="/app/output", help="output directory")
    p.add_argument("--work", default="/app/work", help="work directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scaffold")
    sub.add_parser("verify")
    sub.add_parser("finalize")
    sub.add_parser("selfcheck")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return {"scaffold": cmd_scaffold, "verify": cmd_verify,
                "finalize": cmd_finalize, "selfcheck": cmd_selfcheck}[args.cmd](args)
    except Exception as exc:  # noqa: BLE001
        # Never leave a crash without a minimal result.
        try:
            os.makedirs(args.output, exist_ok=True)
            p = os.path.join(args.output, "result.json")
            if not os.path.isfile(p):
                write_json(p, {
                    "case_id": None, "status": "input_error",
                    "deliverable": None, "fact_source_map": [],
                    "version_boundary": [], "evidence_map": [],
                    "sources": [], "uncertainty": [],
                    "pending_human": [], "change_log": [],
                    "errors": [str(exc)], "degradations": [],
                    "runtime": {},
                })
        except Exception:  # noqa: BLE001
            pass
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
