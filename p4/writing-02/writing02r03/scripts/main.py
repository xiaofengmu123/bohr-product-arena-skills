# -*- coding: utf-8 -*-
"""CLI entry point for the academic-polish-03 Skill.

Subcommands
-----------
scaffold   Discover /input, build evidence index and write a *partial*
           result.json skeleton immediately (first-3-minutes requirement).
verify     Run the deterministic conservation checks in checks.py against
           /app/work/edits.json and /app/work/glossary.json.
finalize   Assemble editable Markdown artifacts and the full result.json.
selfcheck  Validate result.json, artifacts, and re-run finalize to prove the
           result is deterministic (same work files -> identical result).

Default paths follow the runtime contract:
    --input  /input       --output  /app/output       --work  /app/work

Standard library only; no network; no model calls. All file writes are
deterministic (sorted keys, stable ordering, no timestamps inside result.json);
the runtime clock is recorded by the platform, not invented here.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import checks  # noqa: E402  (same directory)

DEFAULT_CONTRACT = {
    "deliverable": "manuscript.md",
    "fact_source_map": [],
    "version_boundary": [],
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml", ".yml",
            ".rst", ".xml", ".html"}
BINARY_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
              ".png", ".jpg", ".jpeg", ".gif", ".svg", ".tif", ".tiff"}
MAX_TEXT_BYTES = 2 * 1024 * 1024

# Abbreviations whose trailing period must not split a sentence.
ABBREVS = ["e.g.", "i.e.", "et al.", "cf.", "vs.", "Fig.", "Figs.", "Eq.",
           "Eqs.", "Ref.", "Refs.", "approx.", "Sect.", "Sec.", "no.", "nos.",
           "vol.", "vols.", "Dr.", "Mr.", "Ms.", "Prof.", "Inc.", "Ltd.",
           "ca.", "al."]

VOLATILE_KEYS = {"runtime", "elapsed_s", "start_iso", "end_iso", "timestamp"}


# ---------------------------------------------------------------------------
# Robust file I/O
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
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
    raise last_err or ValueError("cannot decode %s" % path)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {"_read_error": str(e)}


def read_list(path):
    obj = read_json(path)
    return obj if isinstance(obj, list) else []


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Determinism helpers
# ---------------------------------------------------------------------------

def canonical(obj):
    """Order-independent canonical serialization (stable across runs)."""
    if isinstance(obj, dict):
        parts = []
        for k in sorted(obj.keys(), key=lambda x: str(x)):
            parts.append(json.dumps(k, ensure_ascii=False) + ":" + canonical(obj[k]))
        return "{" + ",".join(parts) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(canonical(v) for v in obj) + "]"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if obj is None:
        return "null"
    if isinstance(obj, (int, float)):
        return repr(obj)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def strip_volatile(obj):
    """Remove timing fields so determinism comparisons ignore wall-clock noise."""
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items()
                if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Input discovery
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
    case, task_text = None, ""
    for p in files:
        base = os.path.basename(p).lower()
        if base == "case.json":
            parsed = read_json(p)
            if isinstance(parsed, dict) and "_read_error" in parsed:
                errors.append("case.json unreadable: %s" % parsed["_read_error"])
                case = None
            else:
                case = parsed
        elif base == "task.md":
            try:
                task_text, _enc, _size = read_text(p)
            except Exception as e:
                errors.append("task.md unreadable: %s" % e)
    return files, case, task_text, errors


def find_draft(case, files):
    """Heuristically locate the manuscript to polish. Returns (path, text)."""
    candidates = []
    if isinstance(case, dict):
        for key in ("text", "draft", "document", "manuscript", "source_text",
                    "passage", "body", "原文", "底稿", "草稿"):
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
                       "response", "paper", "text", "原文", "底稿", "草稿"))
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
            if best is None or len(txt) > best[1]:
                best = (p, len(txt), txt)
        if best:
            candidates.append((best[0], best[2]))
    if not candidates:
        return None, ""
    candidates.sort(key=lambda c: -len(c[1]))
    return candidates[0]


def split_sentences(text):
    """Best-effort editing-unit splitter; abbreviation-aware and deterministic."""
    if not text:
        return []
    tmp = text
    for ab in ABBREVS:
        tmp = tmp.replace(ab, ab[:-1] + "\uE002")
    tmp = re.sub(r"(?<=[。！？!?])", "\uE003", tmp)
    tmp = re.sub(r"(?<=\.)\s+(?=[A-Z0-9\u4e00-\u9fff\"“'(\[])",
                 "\uE003", tmp)
    out = []
    for p in tmp.split("\uE003"):
        p = p.replace("\uE002", ".").strip()
        if p:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Heuristic target detection (deterministic keyword rules)
# ---------------------------------------------------------------------------

def detect_lang(task_text):
    tl = (task_text or "").lower()
    if any(k in tl for k in ("中译英", "英译中", "中英", "bilingual", "双语",
                             "译成英文", "translated into english", "中英文")):
        return "bilingual"
    if any(k in tl for k in ("英文摘要", "english abstract", "英文", "english")):
        return "en"
    return "conservative"


def detect_genre(task_text):
    tl = (task_text or "").lower()
    for key, g in (("基金", "grant"), ("grant", "grant"), ("学位", "thesis"),
                   ("thesis", "thesis"), ("dissertation", "thesis"),
                   ("审稿", "reply"), ("回复", "reply"), ("rebuttal", "reply"),
                   ("review response", "reply"), ("摘要", "abstract"),
                   ("abstract", "abstract"), ("期刊", "journal"),
                   ("journal", "journal"), ("论文", "journal")):
        if key in tl:
            return g
    return "journal"


# ---------------------------------------------------------------------------
# Result assembly (shared by scaffold and finalize; deterministic)
# ---------------------------------------------------------------------------

def assemble_result(work_dir, status, fact_map, vb, check_errors, extra_log=None):
    evidx = read_json(os.path.join(work_dir, "evidence_index.json")) or {}
    contract = read_json(os.path.join(work_dir, "contract.json"))
    if not isinstance(contract, dict):
        contract = dict(DEFAULT_CONTRACT)
    result = {}
    for k, v in contract.items():
        result[k] = copy.deepcopy(v) if isinstance(v, (list, dict)) else v
    clog = read_list(os.path.join(work_dir, "change_log_extra.json"))
    if extra_log:
        clog = clog + extra_log
    input_errors = evidx.get("errors") or []
    result["deliverable"] = "manuscript.md"
    result["fact_source_map"] = fact_map
    result["version_boundary"] = vb
    result["case_id"] = evidx.get("case_id") or "unknown"
    result["status"] = status
    result["sources"] = evidx.get("sources") or []
    result["uncertainty"] = read_list(os.path.join(work_dir, "uncertainty.json"))
    result["pending_human"] = read_list(os.path.join(work_dir, "pending_human.json"))
    result["change_log"] = clog
    result["errors"] = input_errors + (check_errors or [])
    result["degradations"] = evidx.get("degradations") or []
    result["runtime"] = {"deterministic": True}
    return result


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------

def scaffold(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    files, case, task_text, errors = discover(input_dir)

    case_id = "unknown"
    contract = None
    if isinstance(case, dict):
        case_id = (case.get("id") or case.get("case_id")
                   or case.get("challenge_id") or "unknown")
        if isinstance(case.get("output_contract"), dict):
            contract = dict(case["output_contract"])
    if contract is None:
        contract = dict(DEFAULT_CONTRACT)

    draft_path, draft_text = find_draft(case, files)

    sources, degradations = [], []
    for p in files:
        try:
            size = os.path.getsize(p)
            ext = os.path.splitext(p)[1].lower()
            src = {"path": p, "type": ext.lstrip(".") or "unknown",
                   "bytes": size, "digest": file_digest(p)}
            if ext in BINARY_EXT:
                src["opaque"] = True
            sources.append(src)
        except OSError as e:
            errors.append("cannot stat %s: %s" % (p, e))
    for p in files:
        ext = os.path.splitext(p)[1].lower()
        if ext in BINARY_EXT:
            degradations.append(
                {"file": p, "kind": "binary",
                 "note": "二进制材料未自动解析；运行时按需用 pdftotext/OCR/渲染器读取真实内容"})

    status = "input_error" if not os.path.isdir(input_dir) else (
        "insufficient_evidence" if not draft_text.strip() else "partial")

    # Work state (edits/glossary/evidence/contract and agent-editable channels).
    units = [{"index": i, "original": s, "revised": "", "edit_type": "",
              "reason": "", "evidence": {"locator": "", "quote": ""},
              "hedge_note": ""}
             for i, s in enumerate(split_sentences(draft_text))]
    edits = {
        "case_id": case_id,
        "target_lang": detect_lang(task_text),
        "genre": detect_genre(task_text),
        "source_path": draft_path or "case.json.text",
        "full_revised": "",
        "fact_source_map": [],
        "units": units,
    }
    write_json(os.path.join(work_dir, "edits.json"), edits)
    write_json(os.path.join(work_dir, "glossary.json"), {"terms": []})
    write_json(os.path.join(work_dir, "evidence_index.json"), {
        "case_id": case_id, "sources": sources, "errors": errors,
        "degradations": degradations,
        "draft": {"path": draft_path,
                  "bytes": len(draft_text.encode("utf-8", "replace"))},
    })
    write_json(os.path.join(work_dir, "contract.json"), contract)
    write_json(os.path.join(work_dir, "uncertainty.json"),
               [] if draft_text.strip() else ["未发现可润色的底稿文本"])
    write_json(os.path.join(work_dir, "pending_human.json"), [])
    write_json(os.path.join(work_dir, "change_log_extra.json"),
               [{"event": "scaffold", "status": status,
                 "detail": "partial skeleton written (first-3-minutes requirement)"}])

    result = assemble_result(work_dir, status, [], [], [])
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify(work_dir):
    edits = read_json(os.path.join(work_dir, "edits.json")) or {}
    glossary = read_json(os.path.join(work_dir, "glossary.json")) or {}
    fact_map = edits.get("fact_source_map") or []
    report = checks.run_checks(edits, glossary, fact_map)
    write_json(os.path.join(work_dir, "check_report.json"), report)
    s = report["stats"]
    print("verify: errors=%d warnings=%d resolved=%d units=%d"
          % (s["errors"], s["warnings"], s["resolved"], s["units"]))
    return report


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

def escape_pipe(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def render_manuscript(full, units, fact_map):
    lines = ["# 润色后文本（可编辑正稿）\n", full + "\n", "---\n",
             "## 逐句修改说明\n"]
    for i, u in enumerate(units, 1):
        ev = u.get("evidence") or {}
        lines.append("### 第 %d 句（index=%s）" % (i, u.get("index", i - 1)))
        lines.append("- 原文：%s" % (u.get("original") or ""))
        lines.append("- 改文：%s" % (u.get("revised") or ""))
        lines.append("- 修改类型：%s" % (u.get("edit_type") or "no_change"))
        lines.append("- 理由：%s" % (u.get("reason") or ""))
        if (ev.get("locator") or "").strip() or (ev.get("quote") or "").strip():
            lines.append("- 证据：`%s` → 「%s」" % (ev.get("locator") or "",
                                                    ev.get("quote") or ""))
        if (u.get("hedge_note") or "").strip():
            lines.append("- 限定词说明：%s" % u["hedge_note"])
        lines.append("")
    if fact_map:
        lines.append("## 事实与来源映射\n")
        lines.append("| 事实 | 定位 | 逐字引用 |")
        lines.append("|---|---|---|")
        for f in fact_map:
            lines.append("| %s | %s | %s |"
                         % (escape_pipe(f.get("fact")),
                            escape_pipe(f.get("locator")),
                            escape_pipe(f.get("quote"))))
        lines.append("")
    return "\n".join(lines)


def render_glossary(glossary):
    terms = (glossary or {}).get("terms") or []
    lines = ["# 术语表\n", "| 概念 | 规范写法 | 缩写 | 同义变体 |", "|---|---|---|---|"]
    for t in terms:
        variants = "、".join(t.get("variants") or [])
        lines.append("| %s | %s | %s | %s |"
                     % (escape_pipe(t.get("concept")),
                        escape_pipe(t.get("canonical")),
                        escape_pipe(t.get("abbrev") or t.get("abbreviation") or ""),
                        escape_pipe(variants)))
    lines.append("")
    return "\n".join(lines)


def build_version_boundary(units, report):
    changed = [u for u in units
               if (u.get("revised") or "").strip()
               and (u.get("revised") or "").strip() != (u.get("original") or "").strip()]
    vb = [{"type": "diff",
           "detail": {"changed_units": len(changed),
                      "unchanged_units": len(units) - len(changed)},
           "status": "complete"}]
    for w in (report.get("warnings") or []):
        vb.append({"type": "pending_human", "detail": w, "status": "pending"})
    for r in (report.get("resolved") or []):
        vb.append({"type": "human_confirmed", "detail": r, "status": "confirmed"})
    return vb


def finalize(work_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    edits = read_json(os.path.join(work_dir, "edits.json")) or {}
    glossary = read_json(os.path.join(work_dir, "glossary.json")) or {}
    report = read_json(os.path.join(work_dir, "check_report.json")) or {}

    units = (edits.get("units") or [])
    full = (edits.get("full_revised") or "").strip()
    if not full:
        ordered = sorted(units, key=lambda u: (u.get("index", 0),
                                               u.get("original", "")))
        full = "\n\n".join((u.get("revised") or u.get("original") or "").strip()
                           for u in ordered).strip()

    errs = report.get("errors") or []
    warns = report.get("warnings") or []
    has_draft = bool(units) or bool(full)
    if not has_draft or not full:
        status = "insufficient_evidence"
    elif errs:
        status = "partial"
    elif warns:
        status = "partial"
    else:
        status = "ok"

    units_sorted = sorted(units, key=lambda u: (u.get("index", 10 ** 9),
                                                u.get("original", "")))
    fact_map = sorted(edits.get("fact_source_map") or [],
                      key=lambda f: ((f.get("locator") or ""),
                                     (f.get("quote") or ""),
                                     (f.get("fact") or "")))

    write_text(os.path.join(output_dir, "manuscript.md"),
               render_manuscript(full, units_sorted, fact_map))
    write_text(os.path.join(output_dir, "glossary.md"),
               render_glossary(glossary))

    result = assemble_result(
        work_dir, status, fact_map,
        build_version_boundary(units_sorted, report), errs,
        extra_log=[{"event": "finalize", "status": status,
                    "detail": "manuscript.md + glossary.md assembled"}])
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

def selfcheck(input_dir, output_dir, work_dir):
    problems = []
    res = read_json(os.path.join(output_dir, "result.json"))
    if res is None:
        return {"ok": False, "problems": ["result.json missing"]}
    if isinstance(res, dict) and "_read_error" in res:
        problems.append("result.json unreadable: %s" % res["_read_error"])
    else:
        if not (res.get("case_id") or "").strip():
            problems.append("case_id empty")
        if res.get("status") not in STATUSES:
            problems.append("status invalid: %r" % res.get("status"))
        for k in ("sources", "uncertainty", "pending_human", "change_log",
                  "errors", "degradations"):
            if not isinstance(res.get(k), list):
                problems.append("%s must be a list" % k)
        fm = res.get("fact_source_map")
        if not isinstance(fm, list):
            problems.append("fact_source_map must be a list")
        else:
            for i, f in enumerate(fm):
                if not isinstance(f, dict) or not (f.get("fact") and
                                                   f.get("locator") and
                                                   f.get("quote")):
                    problems.append("fact_source_map[%d] missing fact/locator/quote" % i)
        if res.get("status") not in ("insufficient_evidence", "input_error"):
            for name in ("manuscript.md", "glossary.md"):
                if not os.path.isfile(os.path.join(output_dir, name)):
                    problems.append("missing artifact %s" % name)

    # Determinism proof: re-run finalize from the same work state, compare.
    redo = os.path.join(work_dir, "_selfcheck_redo")
    try:
        if os.path.isdir(redo):
            shutil.rmtree(redo)
        finalize(work_dir, redo)
        r2 = read_json(os.path.join(redo, "result.json")) or {}
        if canonical(strip_volatile(res)) != canonical(strip_volatile(r2)):
            problems.append("non-deterministic finalize: canonical result differs on re-run")
    except Exception as e:
        problems.append("determinism check failed: %r" % e)

    ok = not problems
    print("selfcheck: %s" % ("OK" if ok else "FAIL"))
    for p in problems:
        print("  - %s" % p)
    return {"ok": ok, "problems": problems}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="academic-polish-03 deterministic polishing pipeline")
    p.add_argument("command", choices=["scaffold", "verify", "finalize",
                                       "selfcheck"])
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "scaffold":
        scaffold(args.input, args.output, args.work)
    elif args.command == "verify":
        verify(args.work)
    elif args.command == "finalize":
        finalize(args.work, args.output)
    elif args.command == "selfcheck":
        r = selfcheck(args.input, args.output, args.work)
        sys.exit(0 if r["ok"] else 1)
    return 0


if __name__ == "__main__":
    main()
