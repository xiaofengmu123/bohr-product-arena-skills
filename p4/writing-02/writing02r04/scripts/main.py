# -*- coding: utf-8 -*-
"""CLI entry point for the uncertainty-aware academic-polishing Skill.

Subcommands
-----------
scaffold   Discover /input, write a *partial* result.json immediately (the
           first-three-minutes requirement) plus an edits.json work skeleton.
verify     Run deterministic conservation checks in checks.py against
           /app/work/edits.json and write /app/work/check_report.json.
finalize   Assemble editable Markdown artifacts (manuscript.md, glossary.md)
           and the full result.json, deriving status/uncertainty/limitations
           from the check report. Never fabricates data.
selfcheck  Validate result.json and referenced artifacts; exit 0 only if OK.

Defaults follow the runtime contract:
    --input /input   --output /app/output   --work /app/work
Run from the Skill root (``python3 -B scripts/main.py ...``); the import path
is resolved relative to this file, so the working directory does not matter.

Standard library only; deterministic; no model calls.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import checks  # noqa: E402

DEFAULT_CONTRACT = {
    "deliverable": None,       # editable main artifact, relative to output
    "fact_source_map": [],     # [{fact, locator, quote}]
    "version_boundary": [],    # [{type, detail, status}]
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml", ".yml",
            ".rst", ".xml", ".html", ".bib", ".nfo"}
BINARY_EXT = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".png", ".jpg",
              ".jpeg", ".svg", ".tiff", ".gif"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
DRAFT_NAME_HINTS = ("manuscript", "draft", "abstract", "grant", "reply",
                    "response", "paper", "text", "原文", "底稿", "草稿", "摘要",
                    "摘要稿")


# ---------------------------------------------------------------------------
# Robust file IO
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError("file too large (%d bytes > %d)" % (size, limit))
    raw = open(path, "rb").read()
    last = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, size
        except (UnicodeDecodeError, ValueError) as e:
            last = e
    raise last or ValueError("cannot decode " + path)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_json(path):
    return checks.safe_json_load(path)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------------------
# Input discovery (never raises)
# ---------------------------------------------------------------------------

def discover(input_dir):
    files, errors = [], []
    if not os.path.isdir(input_dir):
        return files, None, "", ["input directory missing: " + input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.isfile(p) and not n.startswith("."):
                files.append(p)
    case, task_text = None, ""
    for p in files:
        base = os.path.basename(p).lower()
        if base == "case.json":
            parsed = read_json(p)
            if isinstance(parsed, dict) and "_read_error" in parsed:
                errors.append("case.json unreadable: " + parsed["_read_error"])
            else:
                case = parsed
        elif base == "task.md":
            try:
                task_text, _, _ = read_text(p)
            except Exception as e:
                errors.append("task.md unreadable: " + str(e))
    return files, case, task_text, errors


def source_entry(p):
    ext = os.path.splitext(p)[1].lower()
    if ext in TEXT_EXT:
        typ = "text"
    elif ext in BINARY_EXT:
        typ = "opaque"
    else:
        typ = "unknown"
    return {"path": p, "type": typ, "bytes": os.path.getsize(p),
            "sha": digest(p)}


def find_draft(case, files):
    """Return (path, text) for the manuscript to polish, or (None, None)."""
    if isinstance(case, dict):
        for key in ("text", "draft", "document", "manuscript", "source_text",
                    "passage", "body", "content"):
            v = case.get(key)
            if isinstance(v, str) and len(v.strip()) > 40:
                return ("case.json." + key, v)
    candidates = []
    for p in files:
        base = os.path.basename(p).lower()
        if base in ("case.json", "task.md", "challenge.json"):
            continue
        ext = os.path.splitext(base)[1]
        if ext not in TEXT_EXT:
            continue
        try:
            txt, _, _ = read_text(p)
        except Exception:
            continue
        name_ok = any(k in base for k in DRAFT_NAME_HINTS)
        if name_ok or len(txt) > 200:
            candidates.append((len(txt), p, txt))
    if not candidates:
        return (None, None)
    candidates.sort(reverse=True)
    _, p, txt = candidates[0]
    return (p, txt)


def split_sentences(text):
    """Naive, deterministic sentence splitter for building edit skeletons."""
    parts = re.split(r"(?<=[。！？!?])\s*|\n{2,}", text or "")
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out or ([text.strip()] if text and text.strip() else [])


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _case_id(case):
    if isinstance(case, dict):
        return case.get("case_id") or case.get("id") or case.get("armiId")
    return None


def _contract(case):
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        return dict(case["output_contract"])
    return dict(DEFAULT_CONTRACT)


def _edits_path(work):
    return os.path.join(work, "edits.json")


def cmd_scaffold(args):
    t0 = time.time()
    files, case, task_text, errs = discover(args.input)
    draft_path, draft_text = find_draft(case, files)
    cid = _case_id(case)
    contract = _contract(case)
    status = "partial" if (files and draft_text) else "input_error" if \
        not files else "insufficient_evidence"

    edits = {
        "case_id": cid,
        "draft": {"path": draft_path, "bytes": len(draft_text or ""),
                  "sha": digest(draft_path) if draft_path and
                  os.path.isfile(draft_path) else None},
        "task_digest": digest(os.path.join(args.input, "task.md")) if
        os.path.isfile(os.path.join(args.input, "task.md")) else None,
        "target_lang": None, "genre": None, "audience": None,
        "units": [{"id": "u%04d" % (i + 1), "locator": None,
                   "original": s, "revised": None, "edit_type": None,
                   "reason": None, "evidence": None, "confidence": None,
                   "uncertainty_reason": None, "hedge_justified": False}
                  for i, s in enumerate(split_sentences(draft_text))],
        "facts": [], "glossary": [], "gaps": [],
    }
    write_json(_edits_path(args.work), edits)

    # contract fields backfilled with empty/placeholder values (never dropped)
    contract_vals = {}
    for k in contract:
        if k == "deliverable":
            contract_vals[k] = "manuscript.md"
        elif k in ("fact_source_map", "version_boundary"):
            contract_vals[k] = []
        else:
            contract_vals[k] = None
    result = dict(contract_vals)
    result.update({
        "case_id": cid,
        "status": status,
        "sources": [source_entry(p) for p in files],
        "uncertainty": {"summary": "scaffold partial; edits pending", "items":
                        []},
        "limitations": [],
        "pending_human": [],
        "change_log": [{"step": "scaffold", "at": now_iso(),
                        "detail": "partial skeleton written before analysis"}],
        "errors": list(errs),
        "degradations": list(errs),
        "runtime": {"seconds": round(time.time() - t0, 2),
                    "started_at": now_iso()},
    })
    write_json(os.path.join(args.output, "result.json"), result)
    print("scaffold ok status=%s units=%d" % (status, len(edits["units"])))
    return 0


def cmd_verify(args):
    edits = read_json(_edits_path(args.work))
    if not isinstance(edits, dict):
        edits = {"units": [], "facts": [], "glossary": [], "gaps": []}
    report = checks.build_report(edits)
    write_json(os.path.join(args.work, "check_report.json"), report)
    print("verify errors=%d warnings=%d" % (len(report["errors"]),
                                            len(report["warnings"])))
    return 0


def _build_uncertainty(edits, report):
    items = []
    for u in edits.get("units", []):
        conf = u.get("confidence")
        if conf in ("low", "medium") or u.get("uncertainty_reason"):
            items.append({"locator": u.get("locator") or u.get("id"),
                          "level": conf or "unset",
                          "reason": u.get("uncertainty_reason") or
                          "edit confidence not high"})
    for f in edits.get("facts", []):
        if f.get("certainty") in ("inferred", "unverified"):
            items.append({"locator": f.get("locator"), "level":
                          f.get("certainty"), "reason":
                          "fact not verbatim-verified"})
    for w in report.get("warnings", []):
        items.append({"locator": w.get("locator"), "level": "warning",
                      "reason": "%s: %s" % (w.get("type"), w.get("detail"))})
    summary = report.get("summary", {})
    return {"summary": "%d units, %d errors, %d unjustified warnings, %d gaps"
            % (summary.get("units", 0), summary.get("errors", 0),
               summary.get("unjustified_warnings", 0),
               summary.get("gaps", 0)), "items": items}


def _build_limitations(edits, report, sources):
    lim = []
    for s in sources:
        if s.get("type") == "opaque":
            lim.append("binary source not text-parsed: %s" % s.get("path"))
    for g in edits.get("gaps", []):
        lim.append("%s: %s" % (g.get("type"), g.get("detail") or g.get(
            "locator")))
    if report.get("summary", {}).get("unjustified_warnings", 0):
        lim.append("unresolved conservation warnings remain")
    if not edits.get("target_lang"):
        lim.append("target_lang not set; minimal-edit mode assumed")
    return lim


def cmd_finalize(args):
    t0 = time.time()
    files, case, _task, _errs = discover(args.input)
    edits = read_json(_edits_path(args.work))
    report = read_json(os.path.join(args.work, "check_report.json"))
    if not isinstance(edits, dict):
        edits = {"case_id": _case_id(case), "draft": {}, "units": [],
                 "facts": [], "glossary": [], "gaps": []}
    if not isinstance(report, dict):
        report = checks.build_report(edits)

    units = edits.get("units", [])
    revised_text = "\n\n".join(u.get("revised") or u.get("original", "") for
                               u in units)

    # manuscript.md: revised full text + sentence-level edit table
    lines = ["# 润色后正稿（可编辑）", "",
             "> 本文件由 finalize 汇总生成；逐句修改依据见下表。", "",
             "## 一、润色全文", "", revised_text, "", "## 二、逐句修改表", "",
             "| # | 原文 | 改文 | 修改类型 | 理由 |",
             "|---|------|------|----------|------|"]
    for u in units:
        o = (u.get("original") or "").replace("|", "\\|").replace("\n", " ")
        r = (u.get("revised") or "").replace("|", "\\|").replace("\n", " ")
        lines.append("| %s | %s | %s | %s | %s |" % (
            u.get("id", "?"), o, r, u.get("edit_type") or "—",
            (u.get("reason") or "—").replace("|", "\\|")))
    os.makedirs(args.output, exist_ok=True)
    with open(os.path.join(args.output, "manuscript.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # glossary.md
    glines = ["# 术语表（可编辑）", "",
              "| 术语 | 规范写法 | 异形/同义 | 首次出现定位 |",
              "|------|----------|-----------|--------------|"]
    for g in edits.get("glossary", []):
        glines.append("| %s | %s | %s | %s |" % (
            g.get("term", "?").replace("|", "\\|"),
            g.get("canonical", g.get("term", "?")).replace("|", "\\|"),
            "、".join(g.get("variants", [])),
            g.get("first_use_locator") or "—"))
    with open(os.path.join(args.output, "glossary.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(glines) + "\n")

    # status from report
    if not units and not edits.get("draft", {}).get("path"):
        status = "insufficient_evidence"
    elif report.get("summary", {}).get("errors", 0) > 0:
        status = "partial"
    elif report.get("summary", {}).get("unjustified_warnings", 0) > 0:
        status = "partial"
    else:
        status = "ok"

    cid = edits.get("case_id") or _case_id(case)
    contract = _contract(case)

    contract_vals = {}
    for k in contract:
        if k == "deliverable":
            contract_vals[k] = "manuscript.md"
        elif k == "fact_source_map":
            contract_vals[k] = [{"fact": f.get("fact"), "locator":
                                 f.get("locator"), "quote": f.get("quote")}
                                for f in edits.get("facts", [])]
        elif k == "version_boundary":
            vb = [{"type": "diff", "detail": "sentence-level edits in "
                   "manuscript.md", "status": "done"}]
            vb += [{"type": g.get("type", "gap"), "detail": g.get("detail",
                    ""), "status": "pending_human"} for g in
                   edits.get("gaps", [])]
            contract_vals[k] = vb
        else:
            contract_vals[k] = None

    result = dict(contract_vals)
    prev = read_json(os.path.join(args.output, "result.json"))
    prev_log = (prev.get("change_log") or []) if isinstance(prev, dict) else []
    result.update({
        "case_id": cid,
        "status": status,
        "deliverable": "manuscript.md",
        "sources": [source_entry(p) for p in files],
        "uncertainty": _build_uncertainty(edits, report),
        "limitations": [],
        "pending_human": [{"locator": g.get("locator"), "reason":
                           g.get("detail") or g.get("type")} for g in
                          edits.get("gaps", [])],
        "change_log": prev_log + [{"step": "finalize", "at": now_iso(),
                                   "detail": "artifacts assembled; status="
                                   + status}],
        "errors": report.get("errors", []),
        "degradations": list(_errs),
        "runtime": {"seconds": round(time.time() - t0, 2),
                    "started_at": now_iso()},
    })
    result["limitations"] = _build_limitations(edits, report,
                                               result["sources"])
    write_json(os.path.join(args.output, "result.json"), result)
    print("finalize ok status=%s" % status)
    return 0


def cmd_selfcheck(args):
    errors = []
    result = read_json(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict) or "_read_error" in result:
        print("selfcheck FAIL: result.json missing or unreadable")
        return 1
    if not result.get("case_id") and result.get("status") != "input_error":
        errors.append("case_id empty")
    if result.get("status") not in STATUSES:
        errors.append("invalid status: %r" % result.get("status"))
    for field in ("sources", "uncertainty", "change_log"):
        if field not in result:
            errors.append("missing field: " + field)
    deliverable = result.get("deliverable")
    if deliverable:
        p = os.path.join(args.output, deliverable)
        if not os.path.isfile(p):
            errors.append("deliverable missing: " + p)
    for artifact in ("manuscript.md",):
        if not os.path.isfile(os.path.join(args.output, artifact)):
            errors.append("artifact missing: " + artifact)
    if errors:
        print("selfcheck FAIL: " + "; ".join(errors))
        return 1
    print("selfcheck OK status=%s" % result.get("status"))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(prog="main.py",
                                description="Uncertainty-aware academic polish"
                                " Skill CLI")
    p.add_argument("command", choices=["scaffold", "verify", "finalize",
                                       "selfcheck"])
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    args = p.parse_args(argv)
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    return {"scaffold": cmd_scaffold, "verify": cmd_verify,
            "finalize": cmd_finalize, "selfcheck": cmd_selfcheck}[
                args.command](args)


if __name__ == "__main__":
    sys.exit(main())
