#!/usr/bin/env python3
"""resultlib: deterministic, standard-library-only helpers for the deep-research skill.

This module owns the boring, error-prone parts so the agent-written payloads stay
small and JSON assembly stays deterministic:

  * input discovery (never assume fixed file names)
  * tolerant text/JSON reading with encoding fallbacks and a 2 MB size cap
  * contract extraction + result skeleton construction (backfill every contract field)
  * deterministic deep-merge and final assembly of report/evidence artifacts
  * six-point self-check shared by main.py and selftest.py
"""

import copy
import json
import os
import time

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

MAX_TEXT_BYTES = 2 * 1024 * 1024

MATERIAL_EXTS = {
    ".pdf", ".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html",
    ".htm", ".tex", ".bib", ".png", ".jpg", ".jpeg", ".svg", ".gif",
    ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
}

# Default result contract. Matches the task's required outputs: research product,
# evidence matrix and uncertainty. The meta keys (status/sources/change_log/...) are
# added on top by new_skeleton() and are never dropped.
DEFAULT_CONTRACT = {
    "research_product": {
        "type": None,
        "question": None,
        "summary": None,
        "conclusions": [],
    },
    "evidence_matrix": [],
    "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
}

# Keys that the skill always adds, regardless of the case-provided contract.
META_KEYS = ("sources", "change_log", "limitations", "errors",
             "degradations", "pending_human")

CONTRACT_KEYS = ("output_contract", "output_schema", "result_schema", "required_fields")

WORK_TEXT_NAMES = ("decomposition.md", "search_plan.md", "synthesis.md")


def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def read_text(path):
    """Tolerant text read. Returns (text, errors, degradations).

    Encoding order: utf-8-sig -> utf-8 -> gb18030 -> latin-1. Files larger than
    2 MB are truncated and the truncation is recorded, not hidden.
    """
    errs, degs = [], []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", ["stat failed %s: %s" % (path, exc)], []
    if size > MAX_TEXT_BYTES:
        degs.append("truncated to 2MB: %s (%d bytes)" % (path, size))
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_TEXT_BYTES)
    except OSError as exc:
        return "", ["read failed %s: %s" % (path, exc)], degs
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), errs, degs
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), errs, degs


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return []
    except OSError as exc:
        return ["write failed %s: %s" % (path, exc)]


def load_json(path):
    text, errs, degs = read_text(path)
    obj = None
    try:
        obj = json.loads(text)
    except (ValueError, TypeError) as exc:
        errs.append("bad JSON %s: %s" % (path, exc))
    return obj, errs, degs


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        return []
    except OSError as exc:
        return ["write failed %s: %s" % (path, exc)]


def _is_material(name):
    return os.path.splitext(name)[1].lower() in MATERIAL_EXTS


def discover_input(input_dir):
    """Scan the input tree and classify files. Never assumes a fixed layout."""
    inv = {
        "input_dir": input_dir,
        "exists": False,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "other": [],
        "total_files": 0,
    }
    if not input_dir or not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    for root, _dirs, files in os.walk(input_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            low = name.lower()
            inv["total_files"] += 1
            if low == "case.json" and inv["case_json"] is None:
                inv["case_json"] = full
            elif low in ("task.md", "task.txt") and inv["task_md"] is None:
                inv["task_md"] = full
            elif _is_material(name):
                inv["materials"].append(rel)
            else:
                inv["other"].append(rel)
    return inv


def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "case"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return ""


def build_contract(case_obj):
    """Return the case-provided output contract verbatim when present, else the default."""
    if isinstance(case_obj, dict):
        for key in CONTRACT_KEYS:
            val = case_obj.get(key)
            if isinstance(val, dict) and val:
                return copy.deepcopy(val)
            if isinstance(val, list) and val:
                return {str(k): None for k in val}
    return copy.deepcopy(DEFAULT_CONTRACT)


def _contract_default(value):
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    return None


def new_skeleton(case_id, contract, note=""):
    """Build a legal partial result with every contract field backfilled."""
    result = {"case_id": case_id or "", "status": STATUS_PARTIAL}
    for key in META_KEYS:
        result[key] = []
    result["runtime_seconds"] = 0
    for key, value in (contract or {}).items():
        if key not in result:
            result[key] = _contract_default(value)
    result.setdefault("change_log", []).append({
        "ts": iso_now(), "action": "skeleton", "detail": note or "skeleton created",
    })
    return result


def deep_merge(dst, src):
    """Recursive merge; dicts merge, lists/scalars replace deterministically."""
    for key, value in src.items():
        if key in dst and isinstance(dst[key], dict) and isinstance(value, dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)
    return dst


def read_work_notes(work_dir):
    texts = {}
    for name in WORK_TEXT_NAMES:
        text, _, _ = read_text(os.path.join(work_dir, name))
        texts[name] = text or ""
    return texts


def load_evidence_cards(work_dir):
    path = os.path.join(work_dir, "evidence_cards.jsonl")
    cards, errs = [], []
    if not os.path.exists(path):
        return cards, errs
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError as exc:
                    errs.append("evidence_cards.jsonl line %d: %s" % (lineno, exc))
                    continue
                if isinstance(obj, dict):
                    cards.append(obj)
    except OSError as exc:
        errs.append("read failed %s: %s" % (path, exc))
    return cards, errs


def _evidence_row(card):
    return {
        "id": card.get("id"),
        "subquestion": card.get("subquestion"),
        "source": card.get("source"),
        "locator": card.get("locator"),
        "quote": card.get("quote"),
        "claim": card.get("claim"),
        "judgment": card.get("judgment"),
        "reason": card.get("reason"),
        "conclusion_ids": card.get("conclusion_ids", []),
    }


def finalize_artifacts(args, result):
    """Assemble report.md / evidence_map.json and copy work notes. Returns errors."""
    errs = []
    texts = read_work_notes(args.work)
    cards, card_errs = load_evidence_cards(args.work)
    errs.extend(card_errs)

    matrix = result.get("evidence_matrix") or []
    if cards and not matrix:
        matrix = [_evidence_row(c) for c in cards]
        result["evidence_matrix"] = matrix

    evidence_map = {
        "evidence": matrix,
        "cards": cards,
        "decomposition": texts.get("decomposition.md", ""),
        "search_plan": texts.get("search_plan.md", ""),
    }
    errs.extend(write_json(os.path.join(args.output, "evidence_map.json"), evidence_map))

    report = _render_report(result)
    errs.extend(write_text(os.path.join(args.output, "report.md"), report))

    for name in ("decomposition.md", "search_plan.md"):
        text = texts.get(name, "")
        if text:
            errs.extend(write_text(os.path.join(args.output, name), text))
    return errs


def _render_report(result):
    rp = result.get("research_product") or {}
    lines = ["# 深度研究报告", "", "- case_id: `%s`" % result.get("case_id", ""),
             "- status: `%s`" % result.get("status", ""), ""]
    if rp.get("question"):
        lines += ["## 研究问题", "", str(rp.get("question")), ""]
    if rp.get("summary"):
        lines += ["## 综合结论", "", str(rp.get("summary")), ""]
    cons = rp.get("conclusions") or []
    if cons:
        lines += ["## 结论清单", ""]
        for c in cons:
            if not isinstance(c, dict):
                continue
            ev = ",".join(str(e) for e in (c.get("evidence_ids") or []))
            lines.append("- **%s** %s `[%s]` `%s`" % (
                c.get("id", "?"), c.get("text", ""), c.get("strength", ""), ev))
        lines.append("")
    unc = result.get("uncertainty") or {}
    for key, label in (("conflicts", "冲突"), ("gaps", "空白"),
                       ("undetermined", "无法确定"), ("biases", "偏倚")):
        items = unc.get(key) or []
        if items:
            lines += ["### %s" % label, ""]
            for it in items:
                lines.append("- %s" % (it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)))
            lines.append("")
    pend = result.get("pending_human") or []
    if pend:
        lines += ["## 待人工确认", ""]
        for it in pend:
            lines.append("- %s" % (it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def selfcheck(result, args):
    """Six-point coverage check + contract legality. Returns a report dict."""
    texts = read_work_notes(args.work)
    cards, _ = load_evidence_cards(args.work)
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("json_parsable", isinstance(result, dict), "result.json parses to an object")

    deco = texts.get("decomposition.md", "")
    sq_count = deco.count("SQ")
    has_dep = ("依赖" in deco) or ("->" in deco) or ("depends" in deco.lower())
    add("decomposition", bool(deco) and sq_count >= 2 and has_dep,
        "decomposition.md with >=2 subquestions and dependencies (SQ=%d)" % sq_count)

    plan = texts.get("search_plan.md", "")
    add("search_plan", bool(plan) and any(k in plan for k in ("顺序", "停止", "来源")),
        "search_plan.md states order, source scope and stop condition")

    matrix = result.get("evidence_matrix") or []
    rp = result.get("research_product") or {}
    cons = rp.get("conclusions") or []
    ev_ids = {row.get("id") for row in matrix if isinstance(row, dict) and row.get("id")}
    all_loc_quote = all(isinstance(r, dict) and r.get("locator") and r.get("quote") for r in matrix)
    dangling = sorted({eid for c in cons if isinstance(c, dict)
                       for eid in (c.get("evidence_ids") or []) if eid not in ev_ids})
    add("evidence_linkage", bool(matrix) and all_loc_quote and not dangling,
        "matrix=%d rows with locator+quote; dangling conclusion evidence=%s" % (len(matrix), dangling or "none"))

    conflicts = (result.get("uncertainty") or {}).get("conflicts") or []
    conflict_ok = (not conflicts) or all(
        isinstance(c, dict) and c.get("sources") and c.get("resolution") for c in conflicts)
    add("conflicts", conflict_ok,
        "conflicts side-by-side with sources+resolution (n=%d)" % len(conflicts))

    add("conclusions", bool(cons) and bool(rp.get("question")) and bool(rp.get("summary")),
        "conclusions=%d with question+summary present" % len(cons))

    gaps = (result.get("uncertainty") or {}).get("gaps") or []
    pend = result.get("pending_human") or []
    add("gaps", bool(gaps or pend), "explicit gaps/pending_human (gaps=%d, pending=%d)" % (len(gaps), len(pend)))

    required = ("case_id", "status", "research_product", "evidence_matrix",
                "uncertainty", "sources", "change_log")
    missing = [k for k in required if k not in result]
    add("contract_complete", not missing, "missing required keys: %s" % (missing or "none"))

    passed = sum(1 for c in checks if c["ok"])
    return {"ok": passed == len(checks), "passed": passed, "total": len(checks),
            "summary": "%d/%d checks passed" % (passed, len(checks)), "checks": checks}
