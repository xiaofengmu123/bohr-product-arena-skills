#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic core for the evidence-driven-writing Skill.

Only the Python standard library is used. All state changes flow through this
module so that, given the same ``analysis.json`` and the same ``--fixed-time``,
``finalize`` produces byte-identical ``result.json``, ``evidence_table.md`` and
``manuscript.md``. Content extraction, claim splitting and evidence matching
are the Agent's intellectual work; this module only normalises, validates,
assembles and renders that work in a reproducible order.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MAX_FILE_BYTES = 2 * 1024 * 1024

STATUS_VALUES = ("in_progress", "complete", "partial", "failed")

# Evidence-strength rubric. Levels are ordered from strongest to weakest so
# that a single numeric rank can be derived for deterministic ordering.
STRENGTH_LEVELS = {
    "L1": "共识/多中心RCT/系统综述或Meta分析/权威指南",
    "L2": "单篇RCT/大样本前瞻队列/高质量注册研究",
    "L3": "观察性/病例对照/小样本/单中心/回顾性研究",
    "L4": "预印本/会议摘要/专家意见/综述二手转述",
    "U": "证据强度无法判定或材料不足",
}
STRENGTH_RANK = {"L1": 4, "L2": 3, "L3": 2, "L4": 1, "U": 0}

SUPPORT_LEVELS = ("fully", "partially", "weakly", "conflict", "none")
CLAIM_TYPES = (
    "factual",
    "inference",
    "comparison",
    "causal",
    "citation",
    "recommendation",
)
EVIDENCE_TYPES = (
    "primary",
    "review",
    "guideline",
    "preprint",
    "expert_opinion",
    "unknown",
)

VERDICT_MAP = {
    "fully": "supported",
    "partially": "partial",
    "weakly": "weak",
    "conflict": "conflict",
    "none": "unsupported",
}

STANDARD_KEYS = (
    "case_id",
    "status",
    "deliverable",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "limitations",
    "runtime",
)


def now_iso(fixed_time=None):
    """Deterministic-or-real UTC timestamp (ISO 8601, second precision)."""
    if fixed_time:
        return fixed_time
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_safe(path, max_bytes=MAX_FILE_BYTES):
    """Read text with BOM/encoding fallback. Returns (text|None, errors)."""
    errors = []
    if not os.path.exists(path):
        return None, [f"missing: {path}"]
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, [f"stat failed: {path}: {exc}"]
    if size > max_bytes:
        return None, [f"skipped (>{max_bytes} bytes): {path}"]
    raw = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                raw = fh.read()
            break
        except (UnicodeDecodeError, ValueError) as exc:
            errors.append(f"decode {enc} failed: {path}: {exc}")
    return raw, errors


def load_json_safe(path):
    """Parse JSON with fallback. Returns (obj|None, errors)."""
    raw, errors = read_text_safe(path)
    if raw is None:
        return None, errors
    try:
        return json.loads(raw), errors
    except json.JSONDecodeError as exc:
        return None, errors + [f"bad json: {path}: {exc}"]


def stable_dumps(obj):
    """Deterministic JSON serialisation (sorted keys, no ASCII escapes)."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_text(path, text):
    """Atomic-ish UTF-8 write via a same-directory temp file."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_json_stable(path, obj):
    write_text(path, stable_dumps(obj))


def get_case_id(data):
    """case_id > caseId > id, matching the platform convention."""
    if not isinstance(data, dict):
        return None
    for key in ("case_id", "caseId", "id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def extract_contract(data):
    """Return (contract_keys, raw_contract) from any of the known field names."""
    if not isinstance(data, dict):
        return list(STANDARD_KEYS[3:6]), None
    for key in ("output_contract", "outputContract", "contract", "result_schema"):
        if key in data and data[key] is not None:
            raw = data[key]
            if isinstance(raw, dict):
                return list(raw.keys()), raw
            if isinstance(raw, list):
                keys = [k for k in raw if isinstance(k, str) and k.strip()]
                return keys or list(STANDARD_KEYS[3:6]), raw
            if isinstance(raw, str) and raw.strip():
                return [raw.strip()], raw
    return ["deliverable", "sources", "change_log"], None


def extract_pdf_text(pdf_path, work_dir):
    """Best-effort pdftotext extraction. Returns (text|None, errors)."""
    errors = []
    exe = shutil.which("pdftotext")
    if exe is None:
        return None, [f"pdftotext not found; cannot parse {pdf_path}"]
    out_txt = os.path.join(
        work_dir,
        os.path.splitext(os.path.basename(pdf_path))[0] + ".txt",
    )
    try:
        proc = subprocess.run(
            [exe, "-layout", pdf_path, out_txt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return None, [f"pdftotext failed for {pdf_path}: {exc}"]
    if proc.returncode != 0:
        return None, [f"pdftotext rc={proc.returncode}: {pdf_path}"]
    text, read_errors = read_text_safe(out_txt)
    return text, errors + read_errors


def normalize_claim_id(idx, explicit=None):
    """Force claim ids to C001..Cnnn; duplicates are disambiguated later."""
    if isinstance(explicit, str) and re.fullmatch(r"C\d{3,}", explicit.strip()):
        return explicit.strip()
    return "C%03d" % (idx + 1)


def normalize_locator(locator):
    """Return a non-empty locator or '' (caller decides how to degrade)."""
    if isinstance(locator, str):
        return locator.strip()
    return ""


def _coerce_claim(claim, idx):
    """Normalise a single claim record to a stable dict."""
    if not isinstance(claim, dict):
        claim = {"claim_text": str(claim)}
    claim_type = claim.get("claim_type")
    if claim_type not in CLAIM_TYPES:
        claim_type = "factual"
    out = {
        "claim_id": normalize_claim_id(idx, claim.get("claim_id")),
        "claim_text": str(claim.get("claim_text", "")).strip(),
        "claim_type": claim_type,
        "hedge_required": bool(claim.get("hedge_required", False)),
    }
    if claim.get("uncertainty"):
        out["uncertainty"] = str(claim["uncertainty"]).strip()
    if claim.get("verdict"):
        out["verdict"] = claim["verdict"]
    return out


def _coerce_evidence(evidence, idx):
    """Normalise one evidence record; strength/support are clamped."""
    if not isinstance(evidence, dict):
        evidence = {}
    strength = evidence.get("strength", "U")
    if strength not in STRENGTH_LEVELS:
        strength = "U"
    support = evidence.get("support_level", "none")
    if support not in SUPPORT_LEVELS:
        support = "none"
    evidence_type = evidence.get("evidence_type", "unknown")
    if evidence_type not in EVIDENCE_TYPES:
        evidence_type = "unknown"
    return {
        "evidence_id": "E%03d" % (idx + 1),
        "claim_id": str(evidence.get("claim_id", "")).strip(),
        "source_doc": str(evidence.get("source_doc", "")).strip(),
        "locator": normalize_locator(evidence.get("locator")),
        "quote": str(evidence.get("quote", "")).strip(),
        "support_level": support,
        "strength": strength,
        "evidence_type": evidence_type,
        "strength_reason": str(evidence.get("strength_reason", "")).strip(),
    }


def validate_analysis(analysis):
    """Structural checks on the raw intermediate output. Returns problems.

    Claim/evidence cross-reference validation runs in ``finalize`` AFTER claim
    ids are normalised (claims may omit ``claim_id`` and be auto-numbered), so
    it is intentionally not performed here.
    """
    problems = []
    if not isinstance(analysis, dict):
        return ["analysis is not a JSON object"]
    claims = analysis.get("claims")
    evidence = analysis.get("evidence")
    if not isinstance(claims, list) or not claims:
        problems.append("no claims list (claims must be a non-empty array)")
    if not isinstance(evidence, list):
        problems.append("no evidence list (evidence must be an array)")
    manuscript = analysis.get("manuscript")
    if not isinstance(manuscript, str) or not manuscript.strip():
        problems.append("manuscript is empty")
    return problems


def scaffold(case_path, task_path, out_dir, work_dir, fixed_time=None):
    """Write an honest partial skeleton immediately; never clobber a finished
    result. Returns (result, errors)."""
    errors = []
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    result_path = os.path.join(out_dir, "result.json")
    existing = None
    if os.path.exists(result_path):
        existing, read_errors = load_json_safe(result_path)
        errors += read_errors
        if isinstance(existing, dict) and existing.get("status") in ("complete", "failed"):
            return existing, errors + [
                "result.json already finalised; preserved, not overwritten"
            ]

    case, case_errors = load_json_safe(case_path)
    errors += case_errors
    case_id = get_case_id(case) if isinstance(case, dict) else None
    contract_keys, raw_contract = extract_contract(case if isinstance(case, dict) else {})

    task_text, task_errors = read_text_safe(task_path)
    errors += task_errors

    ts = now_iso(fixed_time)
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "deliverable": {
            "manuscript_path": os.path.join(out_dir, "manuscript.md"),
            "evidence_table_path": os.path.join(out_dir, "evidence_table.md"),
            "claim_list": [],
            "evidence_table": [],
            "summary": "",
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {
                "time": ts,
                "action": "scaffold",
                "detail": "写入早期 partial 结果骨架；case_id 与 output_contract 键已回显",
            }
        ],
        "limitations": [
            "早期骨架：尚未完成材料通读、论断切分与论断—证据匹配",
            "task.md 存在" if task_text is not None else "task.md 缺失或不可读",
        ],
        "runtime": {"started_at": ts, "finished_at": None, "fixed_time": bool(fixed_time)},
    }
    for key in contract_keys:
        if key in skeleton:
            continue
        if raw_contract is not None and isinstance(raw_contract, dict):
            skeleton[key] = raw_contract.get(key)
        else:
            skeleton[key] = None
    write_json_stable(result_path, skeleton)
    return skeleton, errors


def _status_from_analysis(analysis, problems):
    claims = analysis.get("claims") if isinstance(analysis, dict) else None
    evidence = analysis.get("evidence") if isinstance(analysis, dict) else None
    manuscript = analysis.get("manuscript") if isinstance(analysis, dict) else None
    if not isinstance(claims, list) or not claims:
        return "failed"
    if isinstance(manuscript, str) and manuscript.strip() and not problems:
        return "complete"
    return "partial"


def finalize(analysis_path, case_path, out_dir, work_dir, fixed_time=None):
    """Assemble the final result.json + evidence_table.md + manuscript.md in a
    deterministic order. Returns (result, errors)."""
    errors = []
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    analysis, analysis_errors = load_json_safe(analysis_path)
    errors += analysis_errors
    if not isinstance(analysis, dict):
        failed = {
            "case_id": None,
            "status": "failed",
            "deliverable": {},
            "sources": [],
            "uncertainty": [],
            "pending_human": [],
            "change_log": [
                {
                    "time": now_iso(fixed_time),
                    "action": "finalize",
                    "detail": "analysis.json 不可读或为空",
                }
            ],
            "limitations": analysis_errors or ["analysis.json 缺失或不可读"],
            "runtime": {"started_at": now_iso(fixed_time), "finished_at": None},
        }
        write_json_stable(os.path.join(out_dir, "result.json"), failed)
        return failed, errors

    case, case_errors = load_json_safe(case_path)
    errors += case_errors
    case_id = get_case_id(case) if isinstance(case, dict) else None
    if case_id is None:
        case_id = analysis.get("case_id")
    contract_keys, raw_contract = extract_contract(case if isinstance(case, dict) else {})

    problems = validate_analysis(analysis)

    # ---- claims: stable order, dedupe by id ------------------------------
    claims_raw = analysis.get("claims") or []
    seen = set()
    claims = []
    for idx, item in enumerate(claims_raw):
        claim = _coerce_claim(item, idx)
        cid = claim["claim_id"]
        if cid in seen:
            cid = "C%03d" % (idx + 1)
            claim["claim_id"] = cid
        seen.add(cid)
        claims.append(claim)
    claims.sort(key=lambda c: c["claim_id"])

    # ---- evidence: stable order ------------------------------------------
    evidence_raw = analysis.get("evidence") or []
    evidence = [_coerce_evidence(ev, idx) for idx, ev in enumerate(evidence_raw)]
    evidence.sort(key=lambda e: (e["claim_id"], e["source_doc"], e["locator"], e["evidence_id"]))

    # ---- cross-reference validation on normalised data -------------------
    claim_ids = {c["claim_id"] for c in claims}
    for ev in evidence:
        if ev["claim_id"] not in claim_ids:
            problems.append(f"evidence {ev['claim_id']!r} has no matching claim_id")
        if not ev["quote"]:
            problems.append(f"evidence {ev['claim_id']!r} lacks quote")
        if not ev["locator"]:
            problems.append(f"evidence {ev['claim_id']!r} lacks locator")
    errors += ["validation: " + p for p in problems]

    evidence_by_claim = {}
    for ev in evidence:
        evidence_by_claim.setdefault(ev["claim_id"], []).append(ev)

    claim_list = []
    for claim in claims:
        cid = claim["claim_id"]
        supports = evidence_by_claim.get(cid, [])
        if not supports:
            claim["verdict"] = "unsupported"
            claim["support"] = []
        else:
            supports.sort(key=lambda e: (-STRENGTH_RANK[e["strength"]], e["source_doc"]))
            best = supports[0]
            claim["verdict"] = VERDICT_MAP.get(best["support_level"], "unsupported")
            claim["support"] = supports
        claim_list.append(claim)

    # ---- sources: stable order -------------------------------------------
    sources_raw = analysis.get("sources") or []
    sources = []
    for src in sources_raw:
        if not isinstance(src, dict):
            continue
        sources.append(
            {
                "key": str(src.get("key", "")).strip(),
                "value": str(src.get("value", "")).strip(),
                "kind": str(src.get("kind", "")).strip(),
                "location": str(src.get("location", "")).strip(),
                "source_doc": str(src.get("source_doc", "")).strip(),
                "change": str(src.get("change", "")).strip(),
            }
        )
    sources.sort(key=lambda s: (s["key"], s["source_doc"]))

    uncertainty = [str(u) for u in (analysis.get("uncertainty") or []) if str(u).strip()]
    pending_human = [str(p) for p in (analysis.get("pending_human") or []) if str(p).strip()]
    limitations = [str(l) for l in (analysis.get("limitations") or []) if str(l).strip()]
    for p in problems:
        limitations.append("结构缺口: " + p)

    status = _status_from_analysis(analysis, problems)
    ts = now_iso(fixed_time)

    manuscript_text = analysis.get("manuscript") or ""
    summary = analysis.get("summary") or ""

    deliverable = {
        "manuscript_path": os.path.join(out_dir, "manuscript.md"),
        "evidence_table_path": os.path.join(out_dir, "evidence_table.md"),
        "claim_count": len(claim_list),
        "evidence_count": len(evidence),
        "claim_list": claim_list,
        "evidence_table": evidence,
        "summary": str(summary).strip(),
    }

    result = {
        "case_id": case_id,
        "status": status,
        "deliverable": deliverable,
        "sources": sources,
        "uncertainty": uncertainty,
        "pending_human": pending_human,
        "change_log": [
            {
                "time": ts,
                "action": "analyze",
                "detail": f"论断 {len(claim_list)} 条 / 证据 {len(evidence)} 条已归一化",
            },
            {
                "time": ts,
                "action": "finalize",
                "detail": "组装 result.json 并渲染 evidence_table.md / manuscript.md",
            },
        ],
        "limitations": limitations,
        "runtime": {"started_at": ts, "finished_at": ts, "fixed_time": bool(fixed_time)},
    }
    for key in contract_keys:
        if key in result:
            continue
        if raw_contract is not None and isinstance(raw_contract, dict):
            result[key] = raw_contract.get(key)
        else:
            result[key] = None

    write_json_stable(os.path.join(out_dir, "result.json"), result)
    write_text(os.path.join(out_dir, "evidence_table.md"), render_evidence_table(result))
    write_text(
        os.path.join(out_dir, "manuscript.md"),
        render_manuscript(result, manuscript_text),
    )
    return result, errors


def render_evidence_table(result):
    """Human-readable, per-claim evidence blocks (mirrors result.json)."""
    lines = [
        "# 证据表（论断—证据映射）",
        "",
        "> 本文件由 result.json 同源生成，不新增事实。字段定义见 references/01_evidence_method.md。",
        "",
    ]
    claims = (result.get("deliverable") or {}).get("claim_list") or []
    if not claims:
        lines.append("_无论断记录。_")
        return "\n".join(lines) + "\n"
    for claim in claims:
        lines.append("## %s  [%s]" % (claim.get("claim_id", "?"), claim.get("verdict", "?")))
        lines.append("")
        lines.append("- **论断**: %s" % (claim.get("claim_text") or "（空）"))
        lines.append("- **类型**: %s" % (claim.get("claim_type") or "?"))
        if claim.get("uncertainty"):
            lines.append("- **不确定性**: %s" % claim["uncertainty"])
        if claim.get("hedge_required"):
            lines.append("- **需限定语**: 是")
        lines.append("")
        supports = claim.get("support") or []
        if not supports:
            lines.append("- **证据**: 无（verdict=unsupported）")
            lines.append("")
            continue
        for ev in supports:
            lines.append("### %s — %s" % (ev.get("evidence_id", "?"), ev.get("support_level", "?")))
            lines.append("")
            lines.append("- **来源文档**: %s" % (ev.get("source_doc") or "?"))
            lines.append("- **定位**: %s" % (ev.get("locator") or "?"))
            lines.append("- **证据强度**: %s — %s" % (ev.get("strength") or "U", STRENGTH_LEVELS.get(ev.get("strength"), "")))
            lines.append("- **证据类型**: %s" % (ev.get("evidence_type") or "unknown"))
            lines.append("- **强度理由**: %s" % (ev.get("strength_reason") or "（未给）"))
            lines.append("")
            quote = ev.get("quote") or ""
            lines.append("> 原文摘录: %s" % quote)
            lines.append("")
    return "\n".join(lines)


def render_manuscript(result, manuscript_text):
    """Editable final manuscript with a traceability header."""
    header = [
        "# 成稿（可编辑）",
        "",
        "> 依据与逐条证据见 evidence_table.md；本稿不新增材料外事实。",
        "> case_id: %s；status: %s" % (result.get("case_id") or "?", result.get("status") or "?"),
        "",
    ]
    return "\n".join(header) + (manuscript_text.rstrip() + "\n" if manuscript_text else "_（空）_\n")


def validate_result(result):
    """Check a finished result against the output contract. Returns problems."""
    problems = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    if not result.get("case_id"):
        problems.append("case_id missing")
    if result.get("status") not in STATUS_VALUES:
        problems.append("status missing/invalid")
    if not isinstance(result.get("deliverable"), dict):
        problems.append("deliverable missing/invalid")
    else:
        claims = result["deliverable"].get("claim_list")
        if not isinstance(claims, list):
            problems.append("deliverable.claim_list missing/invalid")
        for c in (claims or []):
            if not c.get("claim_id") or not c.get("claim_text"):
                problems.append("claim lacks claim_id/claim_text")
            for ev in (c.get("support") or []):
                if not ev.get("quote") or not ev.get("locator"):
                    problems.append(
                        f"claim {c.get('claim_id')} evidence lacks quote/locator"
                    )
    if not isinstance(result.get("sources"), list):
        problems.append("sources missing/invalid")
    if not isinstance(result.get("uncertainty"), list):
        problems.append("uncertainty missing/invalid")
    if not isinstance(result.get("change_log"), list):
        problems.append("change_log missing/invalid")
    return problems
