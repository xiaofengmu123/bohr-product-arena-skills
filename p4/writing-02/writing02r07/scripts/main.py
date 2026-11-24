# -*- coding: utf-8 -*-
"""CLI entry point for the academic-language polishing Skill (robustness variant).

Subcommands
-----------
scaffold   Discover /input, build the evidence index and write a *partial*
           result.json skeleton immediately (first-3-minutes requirement).
verify     Run the deterministic conservation checks in checks.py against
           /app/work/edits.json and /app/work/evidence_index.json.
finalize   Assemble the editable Markdown artifacts and the full result.json.
selfcheck  Validate the final result.json and every referenced artifact.

Default paths follow the runtime contract:
    --input  /input       --output  /app/output       --work  /app/work

Run from the Skill root so ``scripts/checks.py`` is importable; the import
path is resolved relative to this file, so the working directory does not
matter. Standard library only; no network; no model calls; no randomness.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import checks  # noqa: E402  (same directory)

DEFAULT_CONTRACT = {
    "deliverable": None,
    "fact_source_map": [],
    "version_boundary": [],
}

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
TEXT_EXT = {".md", ".txt", ".tex", ".json", ".csv", ".tsv", ".yaml", ".yml",
            ".rst", ".xml", ".html", ".bib"}
MAX_TEXT_BYTES = 2 * 1024 * 1024
DRAFT_KEYS = ("text", "draft", "document", "manuscript", "source_text",
              "passage", "body", "abstract", "原文", "底稿", "草稿")
DRAFT_NAME_HINTS = ("manuscript", "draft", "abstract", "grant", "reply",
                    "response", "paper", "text", "原文", "底稿", "草稿")
SENT_END_RE = re.compile(r"(?<=[。！？!?])\s*")


# ---------------------------------------------------------------------------
# Robust file reading / writing
# ---------------------------------------------------------------------------

def read_text(path, limit=MAX_TEXT_BYTES):
    """Read a text file with BOM/bad-encoding tolerance.

    Returns (text, encoding, byte_size). Raises ValueError on oversize or
    undecodable content; callers decide how to degrade.
    """
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError(f"file too large ({size} bytes > {limit})")
    raw = open(path, "rb").read()
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


def read_json(path):
    """Read a JSON file, never raising on malformed content.

    Returns the parsed object, None on missing file, or
    {"_read_error": "<message>"} on malformed/undecodable JSON.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        return {"_read_error": f"{type(e).__name__}: {e}"}


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def now_meta():
    """Return a monotonic-free wall-clock marker for runtime metadata only.

    The marker is excluded from any determinism-sensitive comparison.
    """
    import time
    return int(time.time())


# ---------------------------------------------------------------------------
# Input discovery (never assumes a fixed layout)
# ---------------------------------------------------------------------------

def discover(input_dir):
    """Return (files, case, task_text, materials, errors, degradations).

    Never raises. ``case`` is None or a dict; malformed case.json becomes
    {"_read_error": ...} while the discovery continues.
    """
    files, errors, degradations = [], [], []
    case, task_text = None, ""
    materials = []
    if not input_dir or not os.path.isdir(input_dir):
        return files, case, "", materials, [
            f"input directory missing or not a directory: {input_dir!r}"], []

    try:
        walk = list(os.walk(input_dir))
    except OSError as e:
        return files, case, "", materials, [f"cannot walk input dir: {e}"], []

    for root, _dirs, names in walk:
        for n in sorted(names):
            if n.startswith("."):
                continue
            p = os.path.join(root, n)
            if os.path.isfile(p):
                files.append(p)
                rel = os.path.relpath(p, input_dir)
                if "materials" in rel.split(os.sep):
                    materials.append(p)

    for p in files:
        base = os.path.basename(p).lower()
        if base == "case.json":
            parsed = read_json(p)
            if isinstance(parsed, dict) and "_read_error" in parsed:
                errors.append(f"case.json unreadable: {parsed['_read_error']}")
                case = {"_read_error": parsed["_read_error"]}
            else:
                case = parsed
        elif base == "task.md":
            try:
                task_text, _enc, _size = read_text(p)
            except Exception as e:
                errors.append(f"task.md unreadable: {e}")
    return files, case, task_text, materials, errors, degradations


def find_draft(case, files, task_text):
    """Locate the manuscript to polish. Returns (source, text, errors)."""
    candidates, errors = [], []

    def _push(label, text):
        if isinstance(text, str) and len(text.strip()) > 20:
            candidates.append((label, text))

    if isinstance(case, dict):
        for key in DRAFT_KEYS:
            v = case.get(key)
            if isinstance(v, str):
                _push("case.json." + key, v)

    for p in files:
        base = os.path.basename(p).lower()
        if base in ("case.json", "task.md", "challenge.json"):
            continue
        ext = os.path.splitext(base)[1]
        if ext not in TEXT_EXT:
            continue
        name_ok = any(k in base for k in DRAFT_NAME_HINTS)
        try:
            txt, _enc, _size = read_text(p)
        except ValueError as e:
            errors.append(f"skipped {p}: {e}")
            continue
        except Exception as e:
            errors.append(f"skipped {p}: {e}")
            continue
        if name_ok or len(txt) > 200:
            _push(p, txt)

    if not candidates:
        best, best_len = None, 0
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
            try:
                candidates.append((best, read_text(best)[0]))
            except Exception:
                pass

    if not candidates:
        return None, "", errors
    # Prefer the longest real-looking draft; keep the source label.
    source, text = max(candidates, key=lambda c: len(c[1].strip()))
    return source, text, errors


# ---------------------------------------------------------------------------
# Sentence splitting (deterministic)
# ---------------------------------------------------------------------------

def split_sentences(text):
    """Split text into (paragraph_index, sentence_index, sentence)."""
    units = []
    for pi, para in enumerate(text.split("\n")):
        stripped = para.strip()
        if not stripped:
            continue
        parts = [s for s in SENT_END_RE.split(stripped) if s.strip()]
        if not parts:
            parts = [stripped]
        for si, s in enumerate(parts):
            units.append({"para": pi, "sent": si, "text": s.strip()})
    return units


# ---------------------------------------------------------------------------
# Evidence index
# ---------------------------------------------------------------------------

def build_evidence_index(source, text, units):
    """Return (evidence_index, fact_source_map) with locator + quote."""
    evidence_index = []
    fact_source_map = []
    for u in units:
        loc = f"{source}#p{u['para']}.s{u['sent']}"
        t = u["text"]
        has_fact = bool(checks.extract_measurements(t) or
                        checks.extract_plain_numbers(t) or
                        checks.extract_citations(t) or
                        checks.extract_figrefs(t))
        entry = {"locator": loc, "quote": t, "has_numeric_or_ref": has_fact}
        evidence_index.append(entry)
        if has_fact:
            fact_source_map.append({"fact": t, "locator": loc, "quote": t})
    return evidence_index, fact_source_map


# ---------------------------------------------------------------------------
# Edits template
# ---------------------------------------------------------------------------

def build_edits(case, source, units):
    case_id = ""
    if isinstance(case, dict):
        case_id = str(case.get("id") or case.get("case_id") or "").strip()
    edits = {
        "case_id": case_id,
        "draft_source": source,
        "target_lang": "auto",
        "genre": "auto",
        "units": [],
    }
    for i, u in enumerate(units):
        edits["units"].append({
            "id": i + 1,
            "locator": f"{source}#p{u['para']}.s{u['sent']}",
            "original": u["text"],
            "revised": u["text"],  # no-op default; model edits in place
            "edit_type": "no_change",
            "reason": "",
            "evidence": {"locator": f"{source}#p{u['para']}.s{u['sent']}",
                         "quote": u["text"]},
        })
    return edits


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    files, case, task_text, materials, errors, degradations = discover(args.input)

    result = {
        "case_id": "",
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "change_log": [],
        "runtime": {"seconds_estimate": 0},
    }
    # honour the case's own output_contract if present, else default
    contract = DEFAULT_CONTRACT
    if isinstance(case, dict):
        result["case_id"] = str(case.get("id") or case.get("case_id") or "").strip()
        if isinstance(case.get("output_contract"), dict):
            contract = case["output_contract"]
    for k, v in contract.items():
        result[k] = copy_deep(v)

    def log(stage, action, status):
        result["change_log"].append({"stage": stage, "action": action,
                                     "status": status})

    if not files and not case and not task_text:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append(
            "输入目录为空或不可访问，无法发现任何事实底稿")
        log("scaffold", "empty input", "insufficient_evidence")
        write_json(os.path.join(args.output, "result.json"), result)
        write_json(os.path.join(args.work, "edits.json"),
                   {"case_id": "", "draft_source": None, "units": []})
        write_json(os.path.join(args.work, "evidence_index.json"), [])
        write_json(os.path.join(args.work, "glossary.json"), {})
        print("scaffold: insufficient_evidence (empty input)")
        return 0

    source, draft, derrors = find_draft(case, files, task_text)
    result["errors"].extend(derrors)

    # source records with size + digest fingerprint — always recorded, even
    # when no draft is found, so oversized/opaque files are never silent.
    for p in files:
        try:
            sz = os.path.getsize(p)
            dig = file_digest(p) if sz <= MAX_TEXT_BYTES else None
            kind = "opaque" if os.path.splitext(p)[1] not in TEXT_EXT else "text"
            if sz > MAX_TEXT_BYTES:
                kind = "skipped_oversized"
                result["degradations"].append(
                    f"文件超限跳过（>2MB）: {p} ({sz} bytes)")
            result["sources"].append({
                "path": p,
                "type": kind,
                "bytes": sz,
                "digest": dig,
            })
        except OSError as e:
            result["degradations"].append(f"无法统计文件 {p}: {e}")

    if not source or not draft.strip():
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append("未发现可解析的事实底稿（inline text 或材料文件）")
        log("scaffold", "no draft found", "insufficient_evidence")
        write_json(os.path.join(args.output, "result.json"), result)
        write_json(os.path.join(args.work, "edits.json"),
                   {"case_id": result["case_id"], "draft_source": None, "units": []})
        write_json(os.path.join(args.work, "evidence_index.json"), [])
        write_json(os.path.join(args.work, "glossary.json"), {})
        print("scaffold: insufficient_evidence (no draft)")
        return 0

    units = split_sentences(draft)
    evidence_index, fact_source_map = build_evidence_index(source, draft, units)
    edits = build_edits(case, source, units)

    # result skeleton is a *valid partial deliverable* right now
    result["deliverable"] = "manuscript.md"
    result["fact_source_map"] = fact_source_map
    result["version_boundary"] = [
        {"type": "diff", "detail": "逐句 diff 见 edits.json", "status": "pending"},
        {"type": "human_confirmation",
         "detail": "限定强度/数值/单位/引用/图表指向的所有警告须人工确认",
         "status": "pending"},
        {"type": "gap", "detail": "尚未执行 verify/finalize", "status": "pending"},
    ]
    result["change_log"] = [{"stage": "scaffold", "action": "skeleton written",
                             "status": "partial"}]

    glossary = {
        "terms": {},
        "abbreviations": {},
        "conflicts": [],
        "notes": "模型在阶段1填写术语与缩写首次出现形式",
    }

    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.work, "edits.json"), edits)
    write_json(os.path.join(args.work, "evidence_index.json"), evidence_index)
    write_json(os.path.join(args.work, "glossary.json"), glossary)
    print(f"scaffold: partial skeleton written; {len(units)} sentences, "
          f"{len(fact_source_map)} fact entries, source={source}")
    return 0


def cmd_verify(args):
    edits = read_json(os.path.join(args.work, "edits.json"))
    evidence = read_json(os.path.join(args.work, "evidence_index.json"))
    if isinstance(edits, dict) and "_read_error" in edits:
        edits = {"units": []}
    if isinstance(evidence, dict) and "_read_error" in evidence:
        evidence = []
    if not isinstance(edits, dict) or not isinstance(edits.get("units"), list):
        report = {"errors": 1, "warnings": 0, "units": [],
                  "coverage": [], "provenance": [],
                  "summary": "edits.json 缺失或格式错误，无法校验"}
        write_json(os.path.join(args.work, "check_report.json"), report)
        print("verify: edits.json missing or malformed")
        return 0

    units = edits.get("units", [])
    unit_reports = []
    all_errors, all_warnings = [], []
    for u in units:
        issues = checks.compare_unit(u.get("original", ""), u.get("revised", ""))
        errs, warns = checks.classify_issues(issues)
        all_errors.extend(errs)
        all_warnings.extend(warns)
        unit_reports.append({"id": u.get("id"), "locator": u.get("locator"),
                             "errors": errs, "warnings": warns})

    coverage = checks.coverage_check(units)
    all_warnings.extend(coverage)

    fact_map = []
    result = read_json(os.path.join(args.output, "result.json"))
    if isinstance(result, dict) and isinstance(result.get("fact_source_map"), list):
        fact_map = result["fact_source_map"]
    provenance = checks.provenance_check(fact_map)
    all_errors.extend(provenance)

    n_err = len([i for i in all_errors if i["severity"] == "error"])
    n_warn = len([i for i in all_warnings if i["severity"] == "warning"])
    report = {
        "errors": n_err,
        "warnings": n_warn,
        "units": unit_reports,
        "coverage": coverage,
        "provenance": provenance,
        "summary": f"{n_err} error(s), {n_warn} warning(s)",
    }
    write_json(os.path.join(args.work, "check_report.json"), report)
    print(f"verify: {report['summary']}")
    return 0


def cmd_finalize(args):
    edits = read_json(os.path.join(args.work, "edits.json"))
    glossary = read_json(os.path.join(args.work, "glossary.json"))
    evidence = read_json(os.path.join(args.work, "evidence_index.json"))
    result = read_json(os.path.join(args.output, "result.json"))
    if isinstance(result, dict) and "_read_error" in result:
        result = {"status": "partial", "case_id": "", "errors": [],
                  "degradations": [], "uncertainty": [], "pending_human": [],
                  "sources": [], "change_log": []}
    if not isinstance(result, dict):
        result = {"status": "partial", "case_id": "", "errors": [],
                  "degradations": [], "uncertainty": [], "pending_human": [],
                  "sources": [], "change_log": []}
    if not isinstance(edits, dict):
        edits = {"case_id": result.get("case_id", ""), "draft_source": None,
                 "units": []}
    units = edits.get("units", []) if isinstance(edits.get("units"), list) else []

    # polished full text: revised where edited, original otherwise
    body_parts = []
    for u in units:
        rev = (u.get("revised") or "").strip()
        body_parts.append(rev if rev else (u.get("original") or "").strip())

    manuscript_lines = []
    manuscript_lines.append("# 润色后文本（可编辑正稿）")
    manuscript_lines.append("")
    manuscript_lines.append("> 本文件由 finalize 组装；`revised` 为空处回退为原文。")
    manuscript_lines.append("")
    manuscript_lines.append("## 正文")
    manuscript_lines.append("")
    for para in body_parts:
        manuscript_lines.append(para)
        manuscript_lines.append("")
    manuscript_lines.append("## 逐句修改说明")
    manuscript_lines.append("")
    manuscript_lines.append("| # | 原文 | 改文 | 类型 | 理由 |")
    manuscript_lines.append("|---|---|---|---|---|")
    for u in units:
        o = (u.get("original") or "").replace("|", "\\|")
        r = (u.get("revised") or "").replace("|", "\\|")
        t = (u.get("edit_type") or "").replace("|", "\\|")
        reason = (u.get("reason") or "").replace("|", "\\|")
        manuscript_lines.append(f"| {u.get('id', '')} | {o} | {r} | {t} | {reason} |")
    manuscript_lines.append("")
    manuscript_path = os.path.join(args.output, "manuscript.md")
    with open(manuscript_path, "w", encoding="utf-8") as f:
        f.write("\n".join(manuscript_lines))

    glossary_lines = ["# 术语表（可编辑）", ""]
    if isinstance(glossary, dict):
        terms = glossary.get("terms") or {}
        abbrs = glossary.get("abbreviations") or {}
        conflicts = glossary.get("conflicts") or []
        glossary_lines.append("## 术语统一")
        glossary_lines.append("")
        if terms:
            for k, v in terms.items():
                glossary_lines.append(f"- {k}: {v}")
        else:
            glossary_lines.append("（尚未填写；模型在阶段1建立）")
        glossary_lines.append("")
        glossary_lines.append("## 缩写与首次出现")
        glossary_lines.append("")
        if abbrs:
            for k, v in abbrs.items():
                glossary_lines.append(f"- {k}: {v}")
        else:
            glossary_lines.append("（尚未填写）")
        if conflicts:
            glossary_lines.append("")
            glossary_lines.append("## 术语冲突")
            for c in conflicts:
                glossary_lines.append(f"- {c}")
    glossary_path = os.path.join(args.output, "glossary.md")
    with open(glossary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(glossary_lines))

    # merge evidence-derived fact_source_map back into result if not already set
    if not result.get("fact_source_map") and isinstance(evidence, list):
        result["fact_source_map"] = [
            {"fact": e.get("quote", ""), "locator": e.get("locator", ""),
             "quote": e.get("quote", "")}
            for e in evidence if e.get("has_numeric_or_ref")
        ]

    result["deliverable"] = "manuscript.md"
    result["version_boundary"] = result.get("version_boundary") or [
        {"type": "diff", "detail": "逐句 diff 见 manuscript.md", "status": "done"},
    ]
    result["status"] = "ok" if units else result.get("status", "partial")
    result["change_log"] = result.get("change_log") or []
    result["change_log"].append({"stage": "finalize", "action": "artifacts assembled",
                                 "status": "done"})
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["finalized_at_epoch"] = now_meta()

    write_json(os.path.join(args.output, "result.json"), result)
    print(f"finalize: wrote manuscript.md + glossary.md + result.json "
          f"({len(units)} units)")
    return 0


def cmd_selfcheck(args):
    result = read_json(os.path.join(args.output, "result.json"))
    problems = []
    if not isinstance(result, dict):
        problems.append("result.json missing or not an object")
        print("selfcheck: FAIL")
        for p in problems:
            print(" -", p)
        return 1
    if isinstance(result, dict) and "_read_error" in result:
        problems.append("result.json unreadable: " + result["_read_error"])

    if not str(result.get("case_id") or "").strip() and \
            result.get("status") != "insufficient_evidence":
        problems.append("case_id empty while status != insufficient_evidence")
    if result.get("status") not in STATUSES:
        problems.append(f"status {result.get('status')!r} illegal")
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            problems.append(f"missing field {key}")
    deliverable = result.get("deliverable")
    if deliverable:
        dp = os.path.join(args.output, deliverable)
        if not os.path.exists(dp):
            problems.append(f"deliverable missing: {dp}")
    for p in problems:
        print(" -", p)
    if problems:
        print("selfcheck: FAIL")
        return 1
    print("selfcheck: OK")
    return 0


def copy_deep(obj):
    import copy
    return copy.deepcopy(obj)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description="Academic-language polishing Skill (robustness variant)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    for name in ("scaffold", "verify", "finalize", "selfcheck"):
        sp = sub.add_parser(name)
        add_common(sp)
        sp.set_defaults(func=globals()["cmd_" + name])
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # noqa: BLE001 - degrade, never crash
        print(f"fatal: {type(e).__name__}: {e}", file=sys.stderr)
        # best-effort minimal artifact so a crash never leaves nothing
        if args.cmd == "scaffold":
            try:
                os.makedirs(args.output, exist_ok=True)
                write_json(os.path.join(args.output, "result.json"), {
                    "case_id": "", "status": "input_error",
                    "sources": [], "uncertainty": ["内部异常，见 errors"],
                    "pending_human": [], "change_log": [],
                    "errors": [f"{type(e).__name__}: {e}"],
                    "degradations": ["scaffold crashed, minimal skeleton written"],
                })
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
