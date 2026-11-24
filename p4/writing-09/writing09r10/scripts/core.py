#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core: deterministic, standard-library helpers for the Results & Discussion
writing skill.

Architecture note (differentiation): the package does not hard-code a fixed set
of chapters. Its central intermediate representation is a *claim ledger*
(``claims.json``), one record per body-text assertion:

    {id, kind, strength, section, locator, quote, unit, value, status}

Writing, drafting, merging and checking all pivot on this ledger, which makes
the four target properties mechanical:
  * 读得准 -> number extraction + value/unit reconciliation
  * 分得清 -> results vs discussion section tag + boundary check
  * 说得住 -> strength tier -> wording calibration
  * 核得了 -> locator + verbatim quote required for every key claim

Everything here is pure Python standard library and deterministic.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

DEFAULT_INPUT_DIR = "/input"
DEFAULT_OUTPUT_DIR = "/app/output"
DEFAULT_WORK_DIR = "/app/work"

MAX_FILE_BYTES = 2_000_000
MAX_TEXT_CHARS = 200_000

STATUS_VALUES = ("partial", "complete", "failed", "insufficient_evidence")
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml",
             ".xml", ".html", ".rtf", ".tex"}
PDF_EXTS = {".pdf"}

DEFAULT_CONTRACT_FIELDS = ("deliverable", "facts_sources", "version_boundaries")

# Overclaim / unsupported-hedge markers. The checker reports these as review
# items; the agent decides whether the phrase is warranted by the evidence.
FORBIDDEN_PATTERNS = [
    r"国际领先", r"填补空白", r"颠覆性", r"革命性", r"世界第一",
    r"世界领先", r"首创", r"史无前例", r"重大突破", r"里程碑",
    r"全球首次", r"国内首次", r"首次发现", r"首次证实",
    r"\bgroundbreaking\b", r"\bunprecedented\b", r"\bworld-?leading\b",
    r"\bparadigm-?shift\b", r"\brevolutionary\b", r"\bfirst-ever\b",
]

STRENGTH_TIERS = (
    "observed",     # 直接观察/测量到的现象或数值
    "statistical",  # 统计检验结果（P 值、CI、效应量）
    "inferred",     # 由数据推断的趋势/关联（较弱）
    "compared",     # 组间比较（方向与幅度）
    "interpreted",  # 解释性论断
    "speculated",   # 机制推测（明确为推测）
    "causal",       # 因果声称（需强证据或标注为推测）
)

# strength tier -> recommended Chinese academic wording (calibrated verbs)
STRENGTH_WORDING = {
    "observed":    "表明 / 显示 / 测得 / 观察发现（确立的观察）",
    "statistical": "差异有统计学意义（注明检验方法与 P 值）",
    "inferred":    "提示 / 初步表明（弱推断）",
    "compared":    "高于 / 低于 / 与…相当 / 变化幅度为…",
    "interpreted": "提示 / 可能反映 / 这一结果提示",
    "speculated":  "推测 / 可能 / 假设（明确标记为推测）",
    "causal":      "仅在可确立因果时用因果动词；否则改用“与…相关，尚不能证明因果”",
}

# number + unit extraction; kept deliberately simple so that the agent can
# reconcile body-text numbers against this mechanically.
NUMBER_UNIT_RE = re.compile(
    r"(?<![\w.,])"
    r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"\s*([%‰]|[µμ]?[A-Za-z°][A-Za-z0-9/%·^\-]*)"
)

NUMBER_ONLY_RE = re.compile(r"(?<![\w.])([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![\w.])")

# Non-unit tokens that occasionally abut a number in prose; excluded so they
# are not mislabelled as units. Units containing /, %, digits or a dot are
# never filtered.
NON_UNIT_WORDS = {
    "vs", "versus", "and", "to", "the", "of", "in", "for", "at", "by",
    "with", "were", "was", "not", "no", "or", "an", "as", "on", "per",
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _log_change(entry: dict, action: str, detail: str) -> None:
    entry.setdefault("change_log", []).append(
        {"ts": now_iso(), "action": action, "detail": detail}
    )


def load_json(path: str, default=None):
    """Robust JSON load: utf-8-sig, BOM, bad JSON all degrade to ``default``."""
    if not path or not os.path.exists(path):
        return default
    try:
        raw = open(path, "rb").read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return default
        text = raw.decode("utf-8-sig")
        return json.loads(text)
    except (OSError, ValueError, UnicodeDecodeError):
        return default


def save_json(path: str, obj) -> str:
    """Write JSON deterministically (sorted keys, ensure_ascii=False)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_text(path: str):
    """Return (text, warning). Tries utf-8-sig, utf-8, gb18030; falls back to
    replace-decoding. Never raises on encoding problems."""
    try:
        raw = open(path, "rb").read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        return "", "unreadable: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return "", "exceeds_size_limit"
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "fallback_replace"


def scan_input(input_dir: str):
    """List input files; skip oversize binaries; classify text vs pdf."""
    entries = []
    if not os.path.isdir(input_dir):
        return entries, ["input_dir_missing: %s" % input_dir]
    for name in sorted(os.listdir(input_dir)):
        full = os.path.join(input_dir, name)
        if not os.path.isfile(full):
            continue
        size = os.path.getsize(full)
        ext = os.path.splitext(name)[1].lower()
        entries.append({"name": name, "path": full, "size": size, "ext": ext})
    return entries, []


def get_output_contract(case: dict):
    """Return the contract fields from case.json; fall back to the three
    standard writing fields when undeclared."""
    oc = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(oc, dict):
        return list(oc.keys())
    if isinstance(oc, list) and all(isinstance(x, str) for x in oc):
        return list(oc)
    return list(DEFAULT_CONTRACT_FIELDS)


def normalize_input(case_path, task_path, input_dir, work_dir):
    """Produce /app/work/input_summary.json."""
    os.makedirs(work_dir, exist_ok=True)
    case = load_json(case_path, {})
    case = case if isinstance(case, dict) else {}
    task_text, task_warn = read_text(task_path) if task_path else ("", "task_missing")
    entries, scan_warn = scan_input(input_dir)

    materials = []
    pdfs = []
    warnings = list(scan_warn)
    if task_warn:
        warnings.append("task.md: %s" % task_warn)
    if case_path and os.path.exists(case_path) and not case:
        warnings.append("case.json empty or unparseable (bad JSON/encoding)")

    for e in entries:
        if e["size"] > MAX_FILE_BYTES:
            warnings.append("skipped oversize: %s (%d bytes)" % (e["name"], e["size"]))
            continue
        if e["ext"] in PDF_EXTS:
            pdfs.append(e)
            materials.append({**e, "kind": "pdf", "note": "use pdf-text-tool"})
        elif e["ext"] in TEXT_EXTS:
            text, w = read_text(e["path"])
            if w:
                warnings.append("%s: %s" % (e["name"], w))
            materials.append({**e, "kind": "text",
                              "chars": len(text),
                              "preview": text[:500]})
        else:
            materials.append({**e, "kind": "binary/unknown", "note": "not read"})

    case_id = case.get("case_id") or case.get("id") or "unknown-case"
    summary = {
        "case_id": case_id,
        "case_path": case_path,
        "task_path": task_path,
        "input_dir": input_dir,
        "work_dir": work_dir,
        "output_contract": get_output_contract(case),
        "task_text": task_text[:MAX_TEXT_CHARS],
        "materials": materials,
        "pdfs_to_parse": pdfs,
        "audience_style": case.get("audience") or case.get("style") or case.get("format"),
        "warnings": warnings,
        "generated_at": now_iso(),
    }
    save_json(os.path.join(work_dir, "input_summary.json"), summary)
    return summary


def build_skeleton(summary, output_dir, status="partial"):
    """Write the minimal honest result.json skeleton."""
    os.makedirs(output_dir, exist_ok=True)
    case_id = summary.get("case_id", "unknown-case")
    result = {
        "case_id": case_id,
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "runtime": {"started_at": now_iso(), "steps": ["skeleton"]},
    }
    for field in summary.get("output_contract", DEFAULT_CONTRACT_FIELDS):
        if field in ("facts_sources", "sources"):
            result[field] = []
        elif field in ("deliverable", "version_boundaries"):
            result[field] = {} if field == "version_boundaries" else {
                "file": "manuscript.md", "content": None}
        else:
            result[field] = None
    _log_change(result, "skeleton", "落盘 partial 骨架：case_id=%s" % case_id)
    save_json(os.path.join(output_dir, "result.json"), result)
    return result


def extract_number_candidates(material_text, file_name, base_locator):
    """Pull numeric candidates (number + trailing unit) with a small context
    window. These are *candidates*, not facts: the agent must confirm each
    against the figure/table and assign strength + section."""
    out = []
    for m in NUMBER_UNIT_RE.finditer(material_text):
        value, unit = m.group(1), (m.group(2) or "")
        if unit:
            gap = m.start(2) - m.end(1)
            # "Fig 2a" style panel labels: single lowercase letter glued to a
            # bare integer is not a measurement (value + unit).
            if gap == 0 and len(unit) == 1 and unit.islower():
                continue
            # prose function words are not units; keep the value, drop the word
            if unit.lower() in NON_UNIT_WORDS and "/" not in unit and "%" not in unit:
                unit = ""
        start = max(0, m.start() - 60)
        end = min(len(material_text), m.end() + 60)
        context = material_text[start:end].replace("\n", " ")
        out.append({
            "value": value,
            "unit": unit,
            "quote": context.strip(),
            "locator": "%s" % base_locator,
            "file": file_name,
        })
    return out


def build_claims(summary, work_dir):
    """Auto-extract numeric candidates into work/claims.json as *draft*
    claims; the model later curates strength/section/status."""
    os.makedirs(work_dir, exist_ok=True)
    claims = []
    for mat in summary.get("materials", []):
        if mat.get("kind") != "text":
            continue
        text, _ = read_text(mat["path"])
        if not text:
            continue
        loc = mat.get("name", "material")
        cands = extract_number_candidates(text[:MAX_TEXT_CHARS], mat["name"], loc)
        for i, c in enumerate(cands):
            claims.append({
                "id": "auto-%s-%03d" % (re.sub(r"\W+", "", mat["name"])[:20] or "mat", i),
                "kind": "number",
                "strength": "observed",
                "section": "results",
                "locator": c["locator"],
                "quote": c["quote"],
                "unit": c["unit"],
                "value": c["value"],
                "status": "candidate",  # agent must confirm -> verified/unverified/pending
                "note": "",
            })
    ledger = {"claims": claims, "manual": [], "generated_at": now_iso()}
    save_json(os.path.join(work_dir, "claims.json"), ledger)
    return ledger


def draft_from_claims(ledger, output_dir):
    """Assemble a manuscript.md skeleton grouped by section. The skeleton lists
    the facts to write (value + unit + locator), not raw source quotes; verbatim
    quotes live in claims.json / result.json facts_sources so they never pollute
    the body-number reconciliation."""
    claims = ledger.get("claims", []) + ledger.get("manual", [])
    results = [c for c in claims if c.get("section") == "results"]
    discussion = [c for c in claims if c.get("section") == "discussion"]
    lines = [
        "# Results & Discussion（草稿骨架，需由 Agent 补全正文）",
        "",
        "> 本文件由 claim 台账生成。每个 `[证据]` 为可回溯定位；写作时保留证据强度，",
        "> 观察写进 Results，解释/文献对照/局限写进 Discussion。逐字原文见 claims.json 的 quote 字段。",
        "",
        "## Results",
        "",
    ]
    if results:
        by_loc = {}
        for c in results:
            by_loc.setdefault(c.get("locator", ""), []).append(c)
        for loc in sorted(by_loc):
            items = by_loc[loc]
            vals = "; ".join(
                " ".join(x for x in (c.get("value", ""), c.get("unit", "")) if x)
                or c.get("note", "")
                for c in items)
            strengths = sorted({c.get("strength", "") for c in items if c.get("strength")})
            lines.append("- %s：%s  {%s}" % (loc, vals, ",".join(strengths)))
    else:
        lines.append("- （暂无已确认的数值 claim，需从图/表/统计输出读取后补入）")
    lines += ["", "## Discussion", ""]
    if discussion:
        for c in discussion:
            text = c.get("note") or c.get("value") or ""
            lines.append("- %s [%s] {%s}" % (text[:120], c.get("locator", ""),
                                            c.get("strength", "")))
    else:
        lines.append("- （暂无讨论 claim，需将解释/文献对照/机制推测/局限写到此节）")
    lines += ["", "## Limitations（暂定）", ""]
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "manuscript.md")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def merge_ledger_into_result(result, ledger, output_dir):
    """Write claim ledger into result['facts_sources'] (if the field exists) and
    refresh deliverable.file."""
    claims = ledger.get("claims", []) + ledger.get("manual", [])
    facts = []
    for c in claims:
        facts.append({
            "id": c.get("id"),
            "kind": c.get("kind"),
            "strength": c.get("strength"),
            "section": c.get("section"),
            "locator": c.get("locator"),
            "quote": c.get("quote"),
            "value": c.get("value"),
            "unit": c.get("unit"),
            "status": c.get("status"),
        })
    if "facts_sources" in result:
        result["facts_sources"] = facts
    if "deliverable" in result and isinstance(result["deliverable"], dict):
        result["deliverable"].setdefault("file", "manuscript.md")
    result.setdefault("sources", [])
    seen = set()
    for c in claims:
        loc = c.get("locator") or ""
        if loc and loc not in seen:
            seen.add(loc)
            result["sources"].append({"locator": loc, "file": c.get("file", ""),
                                      "quote": (c.get("quote") or "")[:200]})
    _log_change(result, "merge", "台账并入 result.json：%d 条 claim" % len(facts))
    save_json(os.path.join(output_dir, "result.json"), result)
    return result


def find_forbidden(text):
    hits = []
    for pat in FORBIDDEN_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            s = max(0, m.start() - 30)
            e = min(len(text), m.end() + 30)
            hits.append({"pattern": pat, "context": text[s:e].replace("\n", " ")})
    return hits


def check_result(result_path, summary, work_dir, output_dir):
    """Validate structure + consistency + forbidden words + source coverage.
    Returns (report, hard_error_count)."""
    result = load_json(result_path, {})
    if not isinstance(result, dict):
        result = {}
    report = {"checks": [], "errors": [], "warnings": [],
              "forbidden": [], "checked_at": now_iso()}
    errors = report["errors"]
    warnings = report["warnings"]

    # 1) structural
    if not result.get("case_id"):
        errors.append("missing case_id")
    if result.get("status") not in STATUS_VALUES:
        warnings.append("status=%r not in %s" % (result.get("status"), STATUS_VALUES))
    contract_fields = summary.get("output_contract", DEFAULT_CONTRACT_FIELDS)
    for field in contract_fields:
        if field not in result:
            errors.append("missing contract field: %s" % field)
        elif result[field] in (None, "", [], {}):
            warnings.append("contract field empty: %s" % field)
    for field in ("status", "sources", "uncertainty", "change_log"):
        if field not in result:
            errors.append("missing required field: %s" % field)
    if not result.get("change_log"):
        warnings.append("change_log empty")

    # 2) deliverable file presence
    ms_path = None
    deliv = result.get("deliverable")
    if isinstance(deliv, dict) and deliv.get("file"):
        ms_path = os.path.join(output_dir, deliv["file"])
        if not os.path.exists(ms_path):
            ms_path = os.path.join(output_dir, os.path.basename(deliv["file"]))
        if os.path.exists(ms_path):
            report["checks"].append("manuscript exists: %s" % ms_path)
        else:
            warnings.append("deliverable file not found: %s" % deliv.get("file"))
    else:
        warnings.append("deliverable.file missing")

    # 3) forbidden / overclaim wording: scan the agent's own prose only
    # (manuscript.md + agent-authored result fields), not verbatim source quotes
    texts = []
    if ms_path and os.path.exists(ms_path):
        mt, _ = read_text(ms_path)
        texts.append(("manuscript.md", mt))
    authored = []
    for key in ("version_boundaries", "uncertainty", "pending_human",
                "change_log", "limitations"):
        authored.append(json.dumps(result.get(key, {}), ensure_ascii=False))
    texts.append(("result.json (authored fields)", " ".join(authored)))
    for label, text in texts:
        for hit in find_forbidden(text):
            report["forbidden"].append({"where": label, **hit})
    if report["forbidden"]:
        warnings.append("found %d overclaim markers (review each)" % len(report["forbidden"]))

    # 4) number reconciliation: body numbers should appear in facts_sources
    fs = result.get("facts_sources") or []
    known_values = {str(x.get("value")) for x in fs if x.get("value")}
    if ms_path and os.path.exists(ms_path):
        mt, _ = read_text(ms_path)
        body_nums = set(NUMBER_ONLY_RE.findall(mt))
        missing = sorted(n for n in body_nums
                         if n not in known_values and abs(_to_f(n)) > 0.0)
        if missing:
            warnings.append("%d body numbers lack a facts_sources record (first: %s)"
                            % (len(missing), ", ".join(missing[:8])))
    report["checks"].append("structure and consistency checked")
    save_json(os.path.join(work_dir, "check_report.json"), report)
    return report, len(errors)


def _to_f(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def run_pipeline(case_path, task_path, input_dir, output_dir, work_dir):
    """read -> skeleton -> claim -> draft -> merge -> check (safe default)."""
    steps = []
    summary = normalize_input(case_path, task_path, input_dir, work_dir)
    steps.append("read")
    evidence = [m for m in summary.get("materials", []) if m.get("kind") in ("text", "pdf")]
    status = "insufficient_evidence" if not evidence else "partial"
    result = build_skeleton(summary, output_dir, status)
    steps.append("skeleton")
    ledger = build_claims(summary, work_dir)
    steps.append("claim")
    draft_from_claims(ledger, output_dir)
    steps.append("draft")
    result = merge_ledger_into_result(result, ledger, output_dir)
    steps.append("merge")
    report, _ = check_result(os.path.join(output_dir, "result.json"),
                             summary, work_dir, output_dir)
    steps.append("check")
    result = load_json(os.path.join(output_dir, "result.json"), {})
    result.setdefault("runtime", {})["steps"] = steps
    result["runtime"]["finished_at"] = now_iso()
    save_json(os.path.join(output_dir, "result.json"), result)
    return result
