# -*- coding: utf-8 -*-
"""CLI entry point for the academic-language polishing Skill.

This edition's design focus is *time budgeting and the early checkpoint*: the
900 s / 64-request budget is split into fixed phases, and ``scaffold`` writes a
valid partial ``result.json`` skeleton immediately (deterministic, zero model
calls) so a crash at any later moment never leaves an empty deliverable.

Subcommands
-----------
scaffold   Discover /input and write the partial result skeleton plus the work
           artifacts (edits.json template, glossary.json, evidence_index.json,
           phase_ledger.json). This is the first-3-minutes early checkpoint.
verify     Run the deterministic conservation checks in checks.py against
           /app/work/edits.json and /app/work/glossary.json.
finalize   Assemble manuscript.md, glossary.md and the full result.json.
selfcheck  Validate the final result.json and referenced artifacts.
selftest   Run the built-in self-test (see selftest.py).
ledger     Print, or with --mark <phase> update, the phase ledger.

Default paths follow the runtime contract:
    --input /input   --output /app/output   --work /app/work
The module imports checks.py relative to this file, so the working directory
does not matter. Standard library only; no network; no model calls.
"""

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import checks  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
TEXT_EXT = {
    ".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml", ".yml",
    ".rst", ".xml", ".html",
}
MAX_TEXT_BYTES = 2 * 1024 * 1024

DEFAULT_CONTRACT = {
    "deliverable": None,
    "fact_source_map": [],
    "version_boundary": [],
}

# Phase ledger: budgets sum to 900 s and 64 model requests.
PHASES = [
    {"id": 0, "name": "scaffold", "budget_s": 40, "requests": 0,
     "description": "发现输入并早落盘 partial 骨架（前 3 分钟硬门槛）"},
    {"id": 1, "name": "read_and_index", "budget_s": 140, "requests": 8,
     "description": "读材料、建术语表与事实池、定 target_lang/genre"},
    {"id": 2, "name": "sentence_edit", "budget_s": 240, "requests": 28,
     "description": "逐句填 edits.json units[]，只改表达不改事实"},
    {"id": 3, "name": "dual_review", "budget_s": 180, "requests": 14,
     "description": "先编辑后独立复核；回读/回译；跑 verify"},
    {"id": 4, "name": "fix_loop", "budget_s": 160, "requests": 12,
     "description": "清零 error；hedge 警告逐一回退或写理由"},
    {"id": 5, "name": "finalize", "budget_s": 60, "requests": 0,
     "description": "组装 manuscript.md / glossary.md / full result.json"},
    {"id": 6, "name": "selfcheck", "budget_s": 80, "requests": 2,
     "description": "跑 selfcheck，复核产物与 result.json"},
]


# ---------------------------------------------------------------------------
# Robust file reading / writing
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
        except (UnicodeDecodeError, ValueError) as e:
            last_err = e
    raise last_err or ValueError(f"cannot decode {path}")


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
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
    """Return (files, case, task_text, errors). Never raises."""
    files, errors = [], []
    if not os.path.isdir(input_dir):
        return files, None, "", [f"input directory missing: {input_dir}"]
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
    """Heuristically locate the manuscript to polish. Returns (path, text)."""
    candidates = []
    if isinstance(case, dict):
        for key in ("text", "draft", "document", "manuscript", "source_text",
                    "passage", "body", "content"):
            v = case.get(key)
            if isinstance(v, str) and len(v.strip()) > 40:
                candidates.append(("case.json." + key, v))
    for p in files:
        base = os.path.basename(p).lower()
        if base in ("case.json", "task.md", "challenge.json"):
            continue
        if os.path.splitext(base)[1] not in TEXT_EXT:
            continue
        name_ok = any(k in base for k in (
            "manuscript", "draft", "abstract", "grant", "reply", "response",
            "paper", "text", "原文", "底稿", "草稿"))
        try:
            txt, _, _ = read_text(p)
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
                txt, _, size = read_text(p)
            except Exception:  # noqa: BLE001
                continue
            if best is None or size > best[0]:
                best = (size, p, txt)
        if best:
            candidates.append((best[1], best[2]))
    if candidates:
        path, text = candidates[0]
        return path, text
    return None, ""


def derive_case_id(case, input_dir):
    if isinstance(case, dict):
        for k in ("id", "case_id", "caseId", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)):
                return str(v)
    base = os.path.basename(os.path.abspath(input_dir).rstrip(os.sep))
    return "case-" + (base or "unknown")


def build_contract(case):
    """Return the output_contract schema, verbatim keys preserved, defaults filled."""
    contract = {}
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        contract = copy.deepcopy(case["output_contract"])
    for k, v in DEFAULT_CONTRACT.items():
        contract.setdefault(k, copy.deepcopy(v))
    return contract


def build_sources(files):
    sources = []
    for p in files:
        try:
            st = os.stat(p)
            sources.append({
                "path": p,
                "type": "text" if os.path.splitext(p)[1] in TEXT_EXT else "opaque",
                "bytes": st.st_size,
                "digest": file_digest(p),
            })
        except OSError as e:  # noqa: BLE001
            sources.append({"path": p, "type": "unreadable", "bytes": None,
                            "digest": None, "note": str(e)})
    return sources


# ---------------------------------------------------------------------------
# Command implementations (importable by selftest.py)
# ---------------------------------------------------------------------------

def cmd_scaffold(input_dir, output_dir, work_dir):
    files, case, task_text, errors = discover(input_dir)
    draft_path, draft_text = find_draft(case, files)
    case_id = derive_case_id(case, input_dir)
    contract = build_contract(case)
    if "deliverable" in contract and not contract.get("deliverable"):
        contract["deliverable"] = "manuscript.md"

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    locator_prefix = draft_path or "draft"
    try:
        rel = os.path.relpath(locator_prefix, input_dir)
        if not rel.startswith(".."):
            locator_prefix = rel
    except ValueError:  # different drive letters on Windows
        pass
    facts = checks.extract_facts(draft_text, locator_prefix=locator_prefix)
    evidence_index = {"facts": facts}
    edits = {
        "meta": {
            "case_id": case_id,
            "source_file": draft_path,
            "target_lang": None,
            "genre": None,
            "pending_human": [],
            "uncertainty": [],
        },
        "source_text": draft_text,
        "units": [],
    }
    glossary = {"terms": []}
    now = time.time()
    ledger = {
        "budget_s": 900,
        "budget_requests": 64,
        "early_skeleton_at_epoch": now,
        "phases": [dict(p, status="pending", done_at=None) for p in PHASES],
    }
    ledger["phases"][0]["status"] = "done"
    ledger["phases"][0]["done_at"] = now

    write_json(os.path.join(work_dir, "edits.json"), edits)
    write_json(os.path.join(work_dir, "glossary.json"), glossary)
    write_json(os.path.join(work_dir, "evidence_index.json"), evidence_index)
    write_json(os.path.join(work_dir, "phase_ledger.json"), ledger)
    write_json(os.path.join(work_dir, "check_report.json"),
               {"errors": [], "warnings": [], "stats": {},
                "note": "verify not yet run"})

    if not draft_text and not files:
        status = "insufficient_evidence"
    elif not draft_text:
        status = "insufficient_evidence"
        errors.append("no editable manuscript discovered in /input")
    else:
        status = "partial"

    result = dict(contract)
    result.update({
        "case_id": case_id,
        "status": status,
        "sources": build_sources(files),
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "phase": 0, "event": "scaffold_partial_skeleton",
            "at_epoch": now,
            "note": "早落盘 partial 结果骨架，后续仅增量改进",
        }],
        "errors": errors,
        "degradations": [],
        "runtime": {"started_at_epoch": now, "duration_s": None,
                    "model_requests_used": 0},
    })
    write_json(os.path.join(output_dir, "result.json"), result)
    return {
        "case_id": case_id, "status": status, "draft": bool(draft_text),
        "facts": len(facts), "files": len(files), "errors": errors,
    }


def cmd_verify(input_dir, output_dir, work_dir):
    report = checks.run_checks(work_dir)
    write_json(os.path.join(work_dir, "check_report.json"), report)
    s = report["stats"]
    print("verify: errors=%d warnings=%d units=%d sentences=%d fact_units=%d"
          % (s["errors"], s["warnings"], s["units"], s["sentences"],
             s["fact_units"]))
    for e in report["errors"]:
        print("  [error] unit %s: %s" % (e.get("unit"), e.get("detail")))
    for w in report["warnings"]:
        print("  [warn ] unit %s: %s" % (w.get("unit"), w.get("detail")))
    return {"report": report,
            "exit_code": 1 if report["errors"] else 0}


def _assemble_manuscript(edits, report, case_id, status):
    units = edits.get("units", []) or []
    revised_text = "\n\n".join(u.get("revised", "") for u in units)
    rows = []
    for i, u in enumerate(units, 1):
        ev = u.get("evidence") or {}
        src = ev.get("locator") or (ev.get("note") or "-")
        rows.append(
            "| %d | %s | %s | %s | %s | %s |" % (
                i,
                _cell(u.get("original", "")),
                _cell(u.get("revised", "")),
                _cell(u.get("edit_type", "")),
                _cell(u.get("reason", "")),
                _cell(src),
            )
        )
    table = "\n".join(rows)
    body = []
    body.append("# 学术语言润色产物\n")
    body.append("- case_id: %s" % case_id)
    body.append("- status: %s" % status)
    body.append("- 校验: errors=%d warnings=%d"
                % (len(report.get("errors", [])),
                   len(report.get("warnings", []))))
    body.append("\n## 一、润色后全文\n")
    body.append(revised_text.strip() or "_（无改文）_")
    body.append("\n\n## 二、逐句修改表\n")
    body.append("| # | 原文 | 改文 | 修改类型 | 理由 | 证据/来源 |")
    body.append("|---|---|---|---|---|---|")
    body.append(table)
    return "\n".join(body) + "\n"


def _cell(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def _assemble_glossary(glossary):
    terms = (glossary or {}).get("terms", []) or []
    if not terms:
        return None
    rows = []
    for i, t in enumerate(terms, 1):
        rows.append("| %d | %s | %s | %s | %s |" % (
            i,
            _cell(t.get("concept", "")),
            _cell(t.get("canonical", "")),
            _cell("、".join(t.get("variants", []) or [])),
            _cell(t.get("first_occurrence", "")),
        ))
    return (
        "# 术语表\n\n"
        "| # | 概念 | 规范写法 | 别名/变体 | 首次出现 |\n"
        "|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )


def _build_fact_map(units):
    fsm = []
    for i, u in enumerate(units, 1):
        orig = u.get("original", "")
        if not checks.has_fact(orig):
            continue
        ev = u.get("evidence") or {}
        fsm.append({
            "fact": orig[:240],
            "locator": (ev.get("locator") or f"edits#u{i}"),
            "quote": (ev.get("quote") or orig),
        })
    return fsm


def _build_boundary(units, report, meta):
    vb = []
    dist = Counter(u.get("edit_type", "unlabeled") for u in units)
    vb.append({"type": "edit_type_distribution", "detail": dict(dist),
               "status": "ok"})
    unresolved = [w for w in report.get("warnings", [])
                  if w.get("code", "").startswith(("hedge", "negation"))]
    if unresolved:
        vb.append({"type": "unresolved_hedge_warnings", "detail": unresolved,
                   "status": "pending_human"})
    for g in (meta.get("pending_human") or []):
        vb.append({"type": "material_gap", "detail": g,
                   "status": "pending_human"})
    for g in (meta.get("uncertainty") or []):
        vb.append({"type": "uncertainty", "detail": g, "status": "pending_human"})
    return vb


def cmd_finalize(input_dir, output_dir, work_dir):
    edits = read_json(os.path.join(work_dir, "edits.json")) or {}
    glossary = read_json(os.path.join(work_dir, "glossary.json")) or {}
    report = read_json(os.path.join(work_dir, "check_report.json")) or {}
    base = read_json(os.path.join(output_dir, "result.json")) or {}

    units = edits.get("units", []) or []
    meta = edits.get("meta", {}) or {}
    case_id = base.get("case_id") or meta.get("case_id") or "unknown"

    errors = report.get("errors", []) or []
    status = "partial" if errors else ("ok" if units else "insufficient_evidence")

    manuscript = _assemble_manuscript(edits, report, case_id, status)
    write_text(os.path.join(output_dir, "manuscript.md"), manuscript)
    gls = _assemble_glossary(glossary)
    if gls is not None:
        write_text(os.path.join(output_dir, "glossary.md"), gls)

    base.update({
        "case_id": case_id,
        "status": status,
        "deliverable": "manuscript.md",
        "fact_source_map": _build_fact_map(units),
        "version_boundary": _build_boundary(units, report, meta),
        "uncertainty": list(meta.get("uncertainty") or []),
        "pending_human": list(meta.get("pending_human") or []),
        "change_log": list(base.get("change_log") or []) + [{
            "phase": 5, "event": "finalize",
            "at_epoch": time.time(),
            "note": "组装 manuscript.md / glossary.md / 完整 result.json",
        }],
        "errors": [e for e in errors],
        "runtime": dict(base.get("runtime") or {},
                        duration_s=round(time.time() - (
                            (base.get("runtime") or {}).get("started_at_epoch")
                            or time.time()), 1)),
    })
    write_json(os.path.join(output_dir, "result.json"), base)
    return {"case_id": case_id, "status": status, "units": len(units),
            "errors": len(errors), "glossary": bool(gls)}


def cmd_selfcheck(input_dir, output_dir, work_dir):
    result = read_json(os.path.join(output_dir, "result.json"))
    problems = []
    if not isinstance(result, dict):
        problems.append("result.json 不可解析或非对象")
    else:
        if not result.get("case_id"):
            problems.append("case_id 为空")
        if result.get("status") not in STATUSES:
            problems.append("status 非法: %r" % result.get("status"))
        deliverable = result.get("deliverable")
        if deliverable and not os.path.isfile(
                os.path.join(output_dir, deliverable)):
            problems.append("deliverable 缺失: %s" % deliverable)
        if not os.path.isfile(os.path.join(output_dir, "manuscript.md")):
            problems.append("manuscript.md 缺失")
        for item in (result.get("fact_source_map") or []):
            if not (item.get("locator") and item.get("quote")):
                problems.append("fact_source_map 项缺少 locator/quote: %r" % item)
                break
        report = read_json(os.path.join(work_dir, "check_report.json")) or {}
        if report.get("errors"):
            problems.append("check_report 仍有 %d 条 error"
                            % len(report["errors"]))
    ok = not problems
    print("selfcheck:", "OK" if ok else "FAIL")
    for p in problems:
        print("  -", p)
    return {"ok": ok, "problems": problems,
            "exit_code": 0 if ok else 1}


def cmd_ledger(input_dir, output_dir, work_dir, mark=None, phase=None):
    ledger = read_json(os.path.join(work_dir, "phase_ledger.json"))
    if mark is not None:
        if ledger is None:
            return {"exit_code": 1, "error": "phase_ledger.json 不存在"}
        idx = phase if phase is not None else mark
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return {"exit_code": 1, "error": "phase 必须是整数"}
        for p in ledger.get("phases", []):
            if p.get("id") == idx:
                p["status"] = "done"
                p["done_at"] = time.time()
        write_json(os.path.join(work_dir, "phase_ledger.json"), ledger)
    if ledger:
        print("phase ledger: budget %ss / %s requests"
              % (ledger.get("budget_s"), ledger.get("budget_requests")))
        for p in ledger.get("phases", []):
            print("  [%s] %-15s %3ss/%2d req" % (
                p.get("status", "pending")[:1].upper(), p.get("name"),
                p.get("budget_s"), p.get("requests")))
    else:
        print("phase_ledger.json 不存在（先运行 scaffold）")
    return {"exit_code": 0}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_path_args(parser, suppress=False):
    """Register --input/--output/--work on a (sub)parser.

    The main parser carries the runtime defaults; subparsers use
    default=argparse.SUPPRESS so ``scaffold --input X`` works without
    clobbering the main-parser defaults when the flag is omitted.
    """
    for flag, default in (
        ("--input", DEFAULT_INPUT),
        ("--output", DEFAULT_OUTPUT),
        ("--work", DEFAULT_WORK),
    ):
        parser.add_argument(flag, default=(argparse.SUPPRESS if suppress else default))


def build_parser():
    p = argparse.ArgumentParser(
        description="学术语言润色 Skill CLI（时间预算与早落盘版）")
    _add_path_args(p)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("scaffold", "verify", "finalize", "selfcheck"):
        _add_path_args(sub.add_parser(name), suppress=True)
    sub.add_parser("selftest")
    lp = sub.add_parser("ledger")
    _add_path_args(lp, suppress=True)
    lp.add_argument("--mark", type=int, default=None)
    lp.add_argument("--phase", type=int, default=None)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "selftest":
        sys.path.insert(0, HERE)
        import selftest  # noqa: E402
        return selftest.run()
    handler = {
        "scaffold": cmd_scaffold,
        "verify": cmd_verify,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
    }.get(args.cmd)
    if args.cmd == "ledger":
        out = cmd_ledger(args.input, args.output, args.work,
                         mark=args.mark, phase=args.phase)
        return out.get("exit_code", 0)
    out = handler(args.input, args.output, args.work)
    return out.get("exit_code", 0)


if __name__ == "__main__":
    sys.exit(main())
