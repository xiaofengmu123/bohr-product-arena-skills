# -*- coding: utf-8 -*-
"""CLI entry point for the academic-polish conservation Skill.

Subcommands
-----------
scaffold   Discover /input, freeze the baseline register and write a
           *partial* result.json immediately (first-3-minutes rule).
verify     Run the deterministic conservation checks (checks.py) against
           /app/work/register.json and /app/work/edits.json.
finalize   Assemble the editable manuscript.md, glossary.md and the full
           result.json.
selfcheck  Validate result.json schema, referenced artifacts, and cross-check
           every fact_source_map quote against the source sentences.

Default paths follow the runtime contract:
    --input  /input       --output  /app/output       --work  /app/work
Run from the Skill root so ``scripts/checks.py`` is importable; the import
path is resolved relative to this file, so the working directory does not
matter. Standard library only; deterministic; no model or network calls.
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

import checks  # noqa: E402

DEFAULT_CONTRACT = {
    "deliverable": "manuscript.md",
    "fact_source_map": [],
    "version_boundary": [],
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml", ".yml",
            ".rst", ".xml", ".html"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_FACTS = 200


# ---------------------------------------------------------------------------
# Robust file I/O
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
    """Read a UTF-8 text file with BOM / bad-encoding tolerance."""
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError("file too large ({} bytes > {})".format(size, limit))
    raw = open(path, "rb").read()
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, size
        except (UnicodeDecodeError, ValueError) as e:
            last_err = e
            continue
    raise last_err or ValueError("cannot decode {}".format(path))


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


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


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_entry(stage, note):
    return {"stage": stage, "time": now_iso(), "note": note}


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover(input_dir):
    """Return (files, case, task_text, errors). Never raises."""
    files, errors = [], []
    if not os.path.isdir(input_dir):
        return files, None, "", ["input directory missing: {}".format(input_dir)]
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
                errors.append("case.json unreadable: {}".format(parsed["_read_error"]))
                case = None
            else:
                case = parsed
        elif base == "task.md":
            try:
                task_text, _, _ = read_text(p)
            except Exception as e:
                errors.append("task.md unreadable: {}".format(e))
    return files, case, task_text, errors


def find_draft(case, files, task_text):
    """Heuristically locate the manuscript to polish. Returns (path, text)."""
    candidates = []
    if isinstance(case, dict):
        for key in ("text", "draft", "document", "manuscript", "source_text",
                    "passage", "body", "abstract"):
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
                       "response", "paper", "text", "source", "material",
                       "材料", "原文", "底稿", "草稿", "稿"))
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
        if best is not None:
            candidates.append(best)
    if not candidates:
        return None, ""
    candidates.sort(key=lambda c: -len(c[1]))
    return candidates[0]


def get_case_id(case, task_text):
    if isinstance(case, dict):
        for k in ("id", "case_id", "challenge_id", "challengeId"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    m = re.search(r"challenge_id:\s*`?([A-Za-z0-9\-_.]+)`?", task_text or "")
    if m:
        return m.group(1)
    return "unknown"


def get_contract(case):
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict):
            return dict(oc)
    return dict(DEFAULT_CONTRACT)


def build_sources(files):
    srcs = []
    for p in files:
        ext = os.path.splitext(p)[1].lower()
        item = {"path": p, "type": "text" if ext in TEXT_EXT else "opaque",
                "bytes": None, "digest": None}
        try:
            st = os.stat(p)
            item["bytes"] = st.st_size
            item["digest"] = file_digest(p)
        except OSError as e:
            item["error"] = str(e)
        srcs.append(item)
    return srcs


def build_result(case_id, contract, sources):
    result = {"case_id": case_id}
    merged = dict(DEFAULT_CONTRACT)
    merged.update(contract)  # contract wins; unknown keys preserved verbatim
    for k in ("fact_source_map", "version_boundary"):
        if not isinstance(merged.get(k), list):
            merged[k] = []
    if not isinstance(merged.get("deliverable"), str) or not merged.get("deliverable"):
        merged["deliverable"] = "manuscript.md"
    result.update(merged)
    result.update({
        "status": "partial",
        "sources": sources,
        "uncertainty": "",
        "change_log": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime": {"started_at": now_iso()},
    })
    return result


def build_evidence(register, draft_path):
    facts, seen = [], set()
    for s in register.get("sentences", []):
        for cat in ("numbers", "units", "citations", "figures"):
            for tok in s.get("features", {}).get(cat, []):
                key = (cat, tok)
                if key in seen:
                    continue
                seen.add(key)
                facts.append({"fact": "{}:{}".format(cat, tok),
                              "locator": s.get("locator", ""),
                              "quote": s.get("original", "")})
                if len(facts) >= MAX_FACTS:
                    return facts
    return facts


def locator_for(draft_path):
    if draft_path.startswith("case.json."):
        return draft_path
    return os.path.basename(draft_path)


def _join(sents):
    out = ""
    for s in sents:
        if not out:
            out = s
            continue
        sep = "" if (out[-1:] in "。！？!?，,；;：:" or (s and "\u4e00" <= s[0] <= "\u9fff")) else " "
        out += sep + s
    return out


# ---------------------------------------------------------------------------
# Subcommand: scaffold
# ---------------------------------------------------------------------------

def scaffold(args):
    t0 = time.time()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    files, case, task_text, dis_errors = discover(args.input)
    degradations = list(dis_errors)
    case_id = get_case_id(case, task_text)
    contract = get_contract(case)
    sources = build_sources(files)
    draft_path, draft_text = find_draft(case, files, task_text)

    if not draft_text:
        result = build_result(case_id, contract, sources)
        result["status"] = "insufficient_evidence"
        result["uncertainty"] = "no draft text located under /input"
        result["degradations"] = degradations
        result["pending_human"] = [{"item": "draft", "reason": "no source text located"}]
        result["change_log"].append(log_entry("scaffold", "no draft; skeleton written"))
        result["runtime"]["scaffold_s"] = round(time.time() - t0, 3)
        write_json(os.path.join(args.output, "result.json"), result)
        write_json(os.path.join(args.work, "register.json"),
                   {"schema": "conservation-register/1", "paragraphs": 0, "sentences": []})
        write_json(os.path.join(args.work, "edits.json"),
                   {"schema": "sentence-edit-ledger/1", "units": []})
        write_json(os.path.join(args.work, "evidence_index.json"), [])
        write_json(os.path.join(args.work, "glossary.json"),
                   {"schema": "glossary/1", "terms": [], "candidates": [], "rules": []})
        return 0

    register = checks.compute_register(draft_text, locator_prefix=locator_for(draft_path))
    evidence = build_evidence(register, draft_path)

    units = []
    for s in register["sentences"]:
        units.append({
            "sentence_id": s["id"],
            "original": s["original"],
            "revised": s["original"],  # placeholder = no change yet
            "edit_type": "no_change",
            "reason": "",
            "evidence": s["locator"],
            "hedge_justified": False,
        })
    edits = {"schema": "sentence-edit-ledger/1", "units": units}
    glossary = {"schema": "glossary/1",
                "terms": [],
                "candidates": checks.extract_terms(draft_text),
                "rules": []}

    result = build_result(case_id, contract, sources)
    result["status"] = "partial"
    result["deliverable"] = "manuscript.md"
    result["fact_source_map"] = evidence
    result["version_boundary"] = [
        {"type": "baseline", "detail": "register frozen at scaffold; no edits applied yet",
         "status": "pending"},
    ]
    result["uncertainty"] = "genre/audience not yet inferred; refine in stage 1"
    result["degradations"] = degradations
    result["change_log"].append(log_entry(
        "scaffold",
        "register {} sentences from {}; partial result written".format(
            len(register["sentences"]), draft_path)))
    result["runtime"]["scaffold_s"] = round(time.time() - t0, 3)
    result["runtime"]["draft_bytes"] = len(draft_text.encode("utf-8"))

    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.work, "register.json"), register)
    write_json(os.path.join(args.work, "edits.json"), edits)
    write_json(os.path.join(args.work, "evidence_index.json"), evidence)
    write_json(os.path.join(args.work, "glossary.json"), glossary)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: verify
# ---------------------------------------------------------------------------

def verify(args):
    register = read_json(os.path.join(args.work, "register.json")) or {"sentences": []}
    edits = read_json(os.path.join(args.work, "edits.json")) or {"units": []}
    report = checks.run_checks(register, edits)
    write_json(os.path.join(args.work, "check_report.json"), report)

    result = read_json(os.path.join(args.output, "result.json"))
    if isinstance(result, dict) and "_read_error" not in result:
        result["errors"] = report["errors"]
        result.setdefault("change_log", [])
        result["change_log"].append(log_entry(
            "verify",
            "errors={} warnings={}".format(report["summary"]["errors"],
                                           report["summary"]["warnings"])))
        result.setdefault("runtime", {})["verify_ok"] = report["ok"]
        write_json(os.path.join(args.output, "result.json"), result)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: finalize
# ---------------------------------------------------------------------------

def finalize(args):
    t0 = time.time()
    register = read_json(os.path.join(args.work, "register.json")) or {"sentences": []}
    edits = read_json(os.path.join(args.work, "edits.json")) or {"units": []}
    glossary = read_json(os.path.join(args.work, "glossary.json")) or {}
    evidence = read_json(os.path.join(args.work, "evidence_index.json")) or []
    report = checks.run_checks(register, edits)

    sent_map = {s["id"]: s for s in register.get("sentences", [])}
    units = sorted(edits.get("units", []), key=lambda u: u.get("sentence_id", 10 ** 9))
    unit_map = {u.get("sentence_id"): u for u in units if u.get("sentence_id") is not None}

    paras = {}
    for sid in sent_map:
        para = sent_map[sid].get("paragraph", 0)
        paras.setdefault(para, []).append(sid)
    polished_paras = []
    for p in sorted(paras):
        sents = []
        for sid in sorted(paras[p]):
            u = unit_map.get(sid)
            sents.append(u["revised"] if u else sent_map[sid]["original"])
        polished_paras.append(_join(sents))
    polished = "\n\n".join(polished_paras)

    md = ["# 润色后全文", "", polished, "",
          "## 逐句修改表", "",
          "| 句序 | 原文 | 改文 | 修改类型 | 理由 | 证据 |",
          "|---|---|---|---|---|---|"]
    for u in units:
        sid = u.get("sentence_id", "?")
        esc = lambda x: (str(x) if x is not None else "").replace("|", "\\|").replace("\n", " ")
        md.append("| {} | {} | {} | {} | {} | {} |".format(
            sid, esc(u.get("original")), esc(u.get("revised")),
            esc(u.get("edit_type")), esc(u.get("reason")), esc(u.get("evidence"))))
    write_text(os.path.join(args.output, "manuscript.md"), "\n".join(md))

    gl = ["# 术语表", "",
          "| 术语 | 首选写法 | 首次出现 | 说明 |",
          "|---|---|---|---|"]
    for t in glossary.get("terms", []):
        esc = lambda x: (str(x) if x is not None else "").replace("|", "\\|").replace("\n", " ")
        gl.append("| {} | {} | {} | {} |".format(
            esc(t.get("term")), esc(t.get("preferred")),
            esc(t.get("first_use")), esc(t.get("note"))))
    if glossary.get("candidates"):
        gl.append("")
        gl.append("## 待确认缩写（脚本自动检出）")
        gl.append("")
        gl.append("；".join(str(c) for c in glossary.get("candidates", [])))
    write_text(os.path.join(args.output, "glossary.md"), "\n".join(gl))

    vb = [{"type": "diff", "detail": "{} sentences edited; {} errors; {} warnings".format(
        len(units), report["summary"]["errors"], report["summary"]["warnings"]),
        "status": "resolved" if report["ok"] else "unresolved"}]
    if report["warnings"]:
        vb.append({"type": "human_confirm", "detail": "{} warnings require revert or written reason".format(
            len(report["warnings"])), "status": "pending"})

    result = read_json(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict) or "_read_error" in result:
        case_id = get_case_id(None, "")
        contract = get_contract(None)
        result = build_result(case_id, contract, [])
    result["deliverable"] = "manuscript.md"
    result["fact_source_map"] = evidence
    result["version_boundary"] = vb
    result["status"] = "ok" if report["ok"] else "partial"
    result["errors"] = report["errors"]
    result["pending_human"] = [{"item": "warnings", "count": report["summary"]["warnings"],
                                "reason": "unresolved conservation warnings"}] if report["warnings"] else []
    result.setdefault("change_log", [])
    result["change_log"].append(log_entry(
        "finalize",
        "manuscript.md + glossary.md written; status={}".format(result["status"])))
    result.setdefault("runtime", {})["finalize_s"] = round(time.time() - t0, 3)
    write_json(os.path.join(args.output, "result.json"), result)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: selfcheck
# ---------------------------------------------------------------------------

def selfcheck(args):
    problems = []
    result = read_json(os.path.join(args.output, "result.json"))
    register = read_json(os.path.join(args.work, "register.json")) or {"sentences": []}

    if not isinstance(result, dict) or "_read_error" in result:
        problems.append("result.json missing or unparseable")
        report = {"ok": False, "problems": problems, "summary": {"problems": len(problems)}}
        write_json(os.path.join(args.output, "selfcheck_report.json"), report)
        print("SELFCHECK FAIL")
        return 1

    if not result.get("case_id"):
        problems.append("case_id empty")
    if result.get("status") not in STATUSES:
        problems.append("invalid status: {}".format(result.get("status")))
    for k in DEFAULT_CONTRACT:
        if k not in result:
            problems.append("missing contract field: {}".format(k))
    if result.get("errors"):
        problems.append("verify errors remain: {}".format(len(result["errors"])))

    d = result.get("deliverable")
    if d:
        dp = d if os.path.isabs(d) else os.path.join(args.output, d)
        if not os.path.isfile(dp):
            problems.append("deliverable missing: {}".format(d))
    if not os.path.isfile(os.path.join(args.output, "manuscript.md")):
        problems.append("manuscript.md missing")

    originals = [checks._ws_norm(s.get("original", "")) for s in register.get("sentences", [])]
    for f in result.get("fact_source_map", []):
        quote = checks._ws_norm(f.get("quote", ""))
        if quote and quote not in originals:
            problems.append("unverifiable fact quote: {}".format(str(f.get("fact", ""))))

    report = {"ok": len(problems) == 0, "problems": problems,
              "summary": {"problems": len(problems)}}
    write_json(os.path.join(args.output, "selfcheck_report.json"), report)
    if isinstance(result, dict) and "_read_error" not in result:
        result.setdefault("runtime", {})["selfcheck_ok"] = report["ok"]
        result.setdefault("change_log", [])
        result["change_log"].append(log_entry("selfcheck", "problems={}".format(len(problems))))
        write_json(os.path.join(args.output, "result.json"), result)
    print("SELFCHECK OK" if report["ok"] else "SELFCHECK FAIL")
    for p in problems:
        print("  - " + p)
    return 0 if report["ok"] else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="academic-polish conservation Skill")
    p.add_argument("command", choices=["scaffold", "verify", "finalize", "selfcheck"])
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    fn = {"scaffold": scaffold, "verify": verify,
          "finalize": finalize, "selfcheck": selfcheck}[args.command]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
