# -*- coding: utf-8 -*-
"""CLI entry point for the academic-polish (lean) Skill.

Subcommands
-----------
scaffold   Discover /input, split the draft into edit units, and write a
           *partial* result.json immediately (first-3-minutes guarantee).
verify     Run deterministic conservation checks against work/edits.json and
           work/glossary.json; write work/check_report.json.
finalize   Assemble editable manuscript.md + glossary.md and the full
           result.json (contract fields, sources, uncertainty, change_log).
selfcheck  Validate the final result.json and referenced artifacts.

Defaults follow the runtime contract:
    --input /input   --output /app/output   --work /app/work
Pass explicit paths for local self-testing. Standard library only; no network;
no model calls; deterministic layout.
"""

import argparse
import copy
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
    "deliverable": None,
    "fact_source_map": [],
    "version_boundary": [],
}
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml",
            ".yml", ".rst", ".xml", ".html", ".jsonl"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
SENT_SPLIT = re.compile(
    r"(?<=[。！？!?])|(?<=[.!?])\s+(?=[A-Z\u4e00-\u9fff\"'“‘（([])")
NUM_CITE_HEDGE = re.compile(r"[0-9]|\[\d|Fig|Table|图|表|式|Eq")


# ---------------------------------------------------------------------------
# Robust IO
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError(f"file too large ({size} bytes > {limit})")
    raw = open(path, "rb").read()
    last = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, size
        except (UnicodeDecodeError, ValueError) as e:
            last = e
    raise last or ValueError(f"cannot decode {path}")


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
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
    files, errors = [], []
    if not os.path.isdir(input_dir):
        return files, None, "", errors + [
            f"input directory missing: {input_dir}"]
    for root, _d, names in os.walk(input_dir):
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
                errors.append(f"case.json unreadable: {parsed['_read_error']}")
            else:
                case = parsed
        elif base == "task.md":
            try:
                task_text, _, _ = read_text(p)
            except Exception as e:  # noqa: BLE001
                errors.append(f"task.md unreadable: {e}")
    return files, case, task_text, errors


def find_draft(case, files):
    """Locate the manuscript to polish. Returns (path, text) or (None, None)."""
    cands = []
    if isinstance(case, dict):
        for key in ("text", "draft", "document", "manuscript", "source_text",
                    "passage", "body", "abstract"):
            v = case.get(key)
            if isinstance(v, str) and len(v.strip()) > 20:
                cands.append(("case.json." + key, v))
    for p in files:
        base = os.path.basename(p).lower()
        if base in ("case.json", "task.md", "challenge.json"):
            continue
        ext = os.path.splitext(base)[1]
        if ext not in TEXT_EXT:
            continue
        name_ok = any(k in base for k in
                      ("manuscript", "draft", "abstract", "grant", "reply",
                       "response", "paper", "text", "原文", "底稿", "草稿"))
        try:
            txt, _, _ = read_text(p)
        except Exception:  # noqa: BLE001
            continue
        if name_ok or len(txt) > 200:
            cands.append((p, txt))
    if not cands:
        return None, None
    # longest candidate is the best guess for the full draft
    path, text = max(cands, key=lambda c: len(c[1]))
    return path, text


def split_sentences(text):
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text)
             if p.strip()]
    sents = []
    for para in paras:
        for c in re.split(r"(?<=[。！？!?])\s*|(?<=[.!?])\s+(?=[A-Z\u4e00-\u9fff\"'“‘（([])",
                          para):
            c = c.strip()
            if c:
                sents.append(c)
    return sents


def extract_facts(sentence, locator):
    nums = checks.extract_numbers(sentence)
    cites = checks.extract_citations(sentence)
    hedges = checks.extract_tokens(sentence, checks.HEDGES)
    tokens = {"numbers": nums, "citations": cites, "hedges": hedges}
    if any(tokens.values()):
        return [{"kind": "fact-bearing", "locator": locator, "quote": sentence,
                 "tokens": tokens}]
    return []


def build_sources(files, case):
    sources = []
    for p in files:
        size = os.path.getsize(p)
        ext = os.path.splitext(p)[1].lower()
        typ = "text" if ext in TEXT_EXT else ("pdf" if ext == ".pdf" else "opaque")
        rec = {"path": p, "type": typ, "bytes": size,
               "digest": file_digest(p) if size <= MAX_TEXT_BYTES else None}
        if typ == "text" and size <= MAX_TEXT_BYTES:
            try:
                txt, _, _ = read_text(p)
                rec["chars"] = len(txt)
            except Exception:  # noqa: BLE001
                rec["read_error"] = True
        sources.append(rec)
    if isinstance(case, dict) and "text" in case:
        sources.append({"path": "case.json.text", "type": "inline",
                        "bytes": len(case["text"].encode("utf-8", "replace")),
                        "digest": hashlib.sha256(
                            case["text"].encode("utf-8", "replace")).hexdigest()[:16]})
    return sources


def case_id_of(case, fallback="unknown"):
    if isinstance(case, dict):
        return case.get("id") or case.get("case_id") or fallback
    return fallback


def contract_of(case):
    c = (case or {}).get("output_contract")
    return c if isinstance(c, dict) and c else copy.deepcopy(DEFAULT_CONTRACT)


def apply_contract(contract, values):
    """Echo every contract key; fill known keys, null/[] for the rest."""
    out = {}
    for k in contract:
        out[k] = values.get(k, [] if k in ("fact_source_map",
                                           "version_boundary") else None)
    return out


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------

def scaffold(args):
    t0 = time.time()
    files, case, task_text, errors = discover(args.input)
    cid = case_id_of(case)
    contract = contract_of(case)
    draft_path, draft_text = find_draft(case, files)
    runtime = lambda: {"seconds": round(time.time() - t0, 3)}  # noqa: E731

    if not draft_text or not draft_text.strip():
        result = {"case_id": cid, "status": "insufficient_evidence",
                  "sources": build_sources(files, case),
                  "uncertainty": ["no draft located in /input"],
                  "pending_human": ["provide the manuscript to polish"],
                  "change_log": [{"t": time.time(), "msg": "scaffold: no draft"}],
                  "errors": errors, "degradations": [], "runtime": runtime()}
        result.update(apply_contract(contract, {}))
        write_json(os.path.join(args.output, "result.json"), result)
        write_json(os.path.join(args.work, "edits.json"),
                   {"meta": {"case_id": cid, "draft_path": None,
                             "unit_count": 0}, "units": []})
        write_json(os.path.join(args.work, "evidence_index.json"),
                   build_sources(files, case))
        write_json(os.path.join(args.work, "glossary.json"),
                   {"terms": [], "notes": []})
        print("scaffold: insufficient_evidence (no draft)")
        return 0

    sents = split_sentences(draft_text)
    units = []
    for i, s in enumerate(sents, 1):
        loc = f"{draft_path}#L{i}"
        units.append({
            "id": i, "locator": loc, "original": s, "revised": "",
            "edit_type": "", "reason": "",
            "facts": extract_facts(s, loc), "evidence": [],
            "status": "pending",
        })

    edits = {"meta": {"case_id": cid, "draft_path": draft_path,
                      "unit_count": len(units)}, "units": units}
    glossary = {"terms": [], "notes": []}
    sources = build_sources(files, case)

    result = {"case_id": cid, "status": "partial",
              "sources": sources,
              "uncertainty": ["partial skeleton; units pending model edit"],
              "pending_human": [],
              "change_log": [{"t": time.time(),
                              "msg": f"scaffold: {len(units)} units split"}],
              "errors": errors, "degradations": [], "runtime": runtime()}
    result.update(apply_contract(contract, {"deliverable": "manuscript.md"}))
    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.work, "edits.json"), edits)
    write_json(os.path.join(args.work, "evidence_index.json"), sources)
    write_json(os.path.join(args.work, "glossary.json"), glossary)
    print(f"scaffold: ok, {len(units)} units, draft={draft_path}")
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def verify(args):
    edits = read_json(os.path.join(args.work, "edits.json"))
    glossary = read_json(os.path.join(args.work, "glossary.json"))
    if not isinstance(edits, dict):
        print("verify: edits.json missing; run scaffold first")
        return 1
    report = checks.check_all(edits, glossary)
    checks.write_report(report, os.path.join(args.work, "check_report.json"))
    s = report["stats"]
    print(f"verify: {s['units']} units, {s['errors']} errors, "
          f"{s['warnings']} warnings")
    return 0


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def _clean_cell(text):
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def build_manuscript(edits):
    parts = []
    for u in edits.get("units") or []:
        rev = u.get("revised") or u.get("original") or ""
        parts.append(rev)
    body = "\n\n".join(p for p in parts if p.strip())
    rows = ["| # | 原文 | 改文 | 修改类型 | 理由 |",
            "|---|------|------|----------|------|"]
    for u in edits.get("units") or []:
        rows.append(
            f"| {u.get('id')} | {_clean_cell(u.get('original'))} | "
            f"{_clean_cell(u.get('revised') or '（未改）')} | "
            f"{_clean_cell(u.get('edit_type') or '—')} | "
            f"{_clean_cell(u.get('reason') or '—')} |")
    return ("# 润色正稿（可编辑）\n\n## 润色全文\n\n" + body +
            "\n\n## 逐句修改说明\n\n" + "\n".join(rows) + "\n")


def build_glossary_md(glossary):
    terms = (glossary or {}).get("terms") or []
    if not terms:
        return "# 术语表\n\n（本案例未提供术语表）\n"
    rows = ["| 概念 | 规范写法 | 别名/变体 | 首次出现 |",
            "|---|---|---|---|"]
    for t in terms:
        rows.append(
            f"| {_clean_cell(t.get('concept'))} | "
            f"{_clean_cell(t.get('canonical'))} | "
            f"{_clean_cell('、'.join(t.get('variants') or []))} | "
            f"{_clean_cell(t.get('first_seen'))} |")
    return "# 术语表\n\n" + "\n".join(rows) + "\n"


def build_fact_map(edits):
    out = []
    for u in edits.get("units") or []:
        for f in u.get("facts") or []:
            out.append({"fact": f.get("quote"),
                        "locator": f.get("locator"),
                        "quote": f.get("quote")})
    return out


def build_boundary(edits):
    out = []
    for u in edits.get("units") or []:
        if not (u.get("revised") or "").strip():
            out.append({"type": "pending_human",
                        "detail": f"unit {u.get('id')} not revised",
                        "status": "pending"})
        elif u.get("revised") != u.get("original") and u.get("edit_type"):
            out.append({"type": "diff",
                        "detail": f"unit {u.get('id')}: {u.get('edit_type')}",
                        "status": "done"})
    return out


def finalize(args):
    t0 = time.time()
    edits = read_json(os.path.join(args.work, "edits.json"))
    glossary = read_json(os.path.join(args.work, "glossary.json"))
    if not isinstance(edits, dict):
        print("finalize: edits.json missing; run scaffold first")
        return 1
    case = read_json(os.path.join(args.input, "case.json"))
    if isinstance(case, dict) and "_read_error" in case:
        case = None
    cid = case_id_of(case, (edits.get("meta") or {}).get("case_id"))
    contract = contract_of(case)

    report = checks.check_all(edits, glossary)
    units = edits.get("units") or []
    has_pending = any(not (u.get("revised") or "").strip() for u in units)
    status = "ok" if (not report["errors"] and not has_pending and units) \
        else "partial"
    if not units:
        status = "insufficient_evidence"

    manuscript = build_manuscript(edits)
    glossary_md = build_glossary_md(glossary)
    fact_map = build_fact_map(edits)
    boundary = build_boundary(edits)
    boundary += [{"type": "check", "detail":
                  f"{len(report['errors'])} errors / "
                  f"{len(report['warnings'])} warnings", "status": "done"}]

    with open(os.path.join(args.output, "manuscript.md"), "w",
              encoding="utf-8") as f:
        f.write(manuscript)
    with open(os.path.join(args.output, "glossary.md"), "w",
              encoding="utf-8") as f:
        f.write(glossary_md)

    values = {"deliverable": "manuscript.md",
              "fact_source_map": fact_map,
              "version_boundary": boundary}
    result = {"case_id": cid, "status": status,
              "sources": read_json(os.path.join(args.work,
                                                "evidence_index.json")) or [],
              "uncertainty": [] if status == "ok" else
              ["units pending model edit or check errors remain"],
              "pending_human": [b["detail"] for b in boundary
                                if b["type"] == "pending_human"],
              "change_log": [{"t": time.time(),
                              "msg": f"finalize: status={status}"}],
              "errors": report["errors"], "degradations": [],
              "runtime": {"seconds": round(time.time() - t0, 3)}}
    result.update(apply_contract(contract, values))
    write_json(os.path.join(args.output, "result.json"), result)
    print(f"finalize: status={status}, errors={len(report['errors'])}, "
          f"warnings={len(report['warnings'])}")
    return 0


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------

def selfcheck(args):
    issues = []
    result = read_json(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict):
        print("selfcheck: FAIL result.json unreadable")
        return 1
    if not result.get("case_id"):
        issues.append("case_id empty")
    if result.get("status") not in STATUSES:
        issues.append(f"bad status {result.get('status')!r}")
    for field in ("sources", "uncertainty", "change_log"):
        if field not in result:
            issues.append(f"missing {field}")
    if not os.path.isfile(os.path.join(args.output, "manuscript.md")):
        issues.append("manuscript.md missing")
    for f in result.get("fact_source_map") or []:
        if not (f.get("fact") and f.get("locator") and f.get("quote")):
            issues.append("fact_source_map entry missing fact/locator/quote")
            break
    if issues:
        print("selfcheck: FAIL " + "; ".join(issues))
        return 1
    print(f"selfcheck: OK status={result.get('status')}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cmd", choices=["scaffold", "verify", "finalize",
                                   "selfcheck"])
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    for d in (args.output, args.work):
        os.makedirs(d, exist_ok=True)
    fn = {"scaffold": scaffold, "verify": verify,
          "finalize": finalize, "selfcheck": selfcheck}[args.cmd]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
