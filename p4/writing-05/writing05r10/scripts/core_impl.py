#!/usr/bin/env python3
"""Deterministic core helpers for evidence-driven writing (standard library only).

This module holds the heavy, pure logic so that main.py stays small. The LLM
performs the actual reading/analysis; these functions do the verification and
bookkeeping that make the result independently reproducible (复得现).
"""
import json
import os
import re
import unicodedata
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0.0"

# Evidence strength. Higher rank = stronger. Unverified/preprint rank below
# even expert opinion because their final peer-review status is unresolved.
STRENGTH_RANKS = {
    "meta_analysis": 8,
    "systematic_review": 7,
    "rct": 6,
    "cohort": 5,
    "case_control": 4,
    "cross_sectional": 3,
    "case_series": 2,
    "case_report": 1,
    "expert_opinion": 0,
    "preprint": -1,
    "unverified": -2,
    "unknown": -3,
}

SUPPORT_KINDS = {"direct", "indirect", "none", "conflict", "unknown"}
SOURCE_TYPES = {"original", "review", "guideline", "preprint", "secondary", "unknown"}
CLAIM_STATUSES = {"ok", "needs_edit", "missing_source", "overclaim", "conflict", "unknown"}

DEFAULT_CONTRACT = {
    "deliverable": None,
    "sources": [],
    "change_log": [],
}

TEXT_EXTENSIONS = {".txt", ".md", ".tex", ".rst", ".csv", ".json", ".html"}

# Verbs whose plain use asserts causation. They over-claim when the underlying
# design is correlational or the support is indirect/absent.
CAUSAL_HINTS = [
    "导致", "引起", "降低", "提高", "增加", "减少", "使得", "驱动", "促进", "抑制",
    "causes", "caused", "cause", "leads to", "led to", "reduces", "reduced",
    "increases", "increased", "drives", "driven", "promotes", "promoted",
]

# Uncertainty lexicon: phrase -> (class, meaning). Used to check that the
# wording of a sentence matches the strength of its evidence.
UNCERTAINTY_PHRASES = {
    "证实": ("strong", "established, direct evidence"),
    "表明": ("strong", "established, direct evidence"),
    "demonstrates": ("strong", "established"),
    "demonstrate": ("strong", "established"),
    "demonstrated": ("strong", "established"),
    "支持": ("moderate", "supporting, not definitive"),
    "提示": ("moderate", "suggestive"),
    "初步表明": ("moderate", "preliminary"),
    "suggests": ("moderate", "suggestive"),
    "suggest": ("moderate", "suggestive"),
    "indicates": ("moderate", "indicative"),
    "indicate": ("moderate", "indicative"),
    "shows": ("moderate", "indicative"),
    "可能": ("weak", "possibility"),
    "或许": ("weak", "possibility"),
    "may": ("weak", "possibility"),
    "might": ("weak", "possibility"),
    "有望": ("prospective", "expectation, not established"),
}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    return s.replace("\r\n", "\n").replace("\r", "\n")


def collapse_ws(s):
    """Collapse all whitespace for tolerant substring matching."""
    return re.sub(r"\s+", "", normalize(s))


def split_sentences(text):
    """Deterministic sentence split used to normalize locators.

    Splits after full-width enders (。！？；) and ASCII !?; unconditionally,
    and after ASCII '.' only when followed by whitespace or end-of-string so
    that decimals (3.14) are not broken. The LLM still performs the actual
    claim segmentation; this is only for locator normalization.
    """
    text = normalize(text)
    if not text:
        return []
    text = re.sub(r"([。！？；;!?])", r"\1\n", text)
    text = re.sub(r"\.(?=\s|$)", ".\n", text)
    return [p.strip() for p in re.split(r"\n+", text) if p.strip()]


def verify_quote(source_text, quote):
    """Return (ok, reason) after a tolerant verbatim check.

    The quote must appear as a contiguous character run in the source after
    whitespace normalization. This is the hard gate for 复得现: a claim's quote
    is either verbatim in the material or it is rejected as non-verbatim.
    """
    if not quote or not quote.strip():
        return False, "empty quote"
    hay = collapse_ws(source_text)
    if not hay:
        return False, "empty source"
    needle = collapse_ws(quote)
    if needle in hay:
        return True, "verbatim"
    needle2 = collapse_ws(quote.strip("\"'“”‘’…[]()"))
    if needle2 and needle2 in hay:
        return True, "verbatim (normalized)"
    return False, "not found verbatim"


def find_quote_span(source_text, quote):
    """Return (start, end) character offsets of quote in normalized source, or None."""
    hay = normalize(source_text)
    needle = normalize(quote).strip()
    if not needle:
        return None
    i = hay.find(needle)
    if i < 0:
        needle2 = normalize(quote.strip("\"'“”‘’…[]()"))
        i = hay.find(needle2)
        needle = needle2
    if i < 0:
        return None
    return (i, i + len(needle))


def next_id(prefix, existing):
    """Next deterministic id (C001, S001, ...) above the highest used number."""
    n = 0
    for e in existing or []:
        eid = e.get("id", "") if isinstance(e, dict) else ""
        if isinstance(eid, str) and eid.startswith(prefix) and eid[len(prefix):].isdigit():
            n = max(n, int(eid[len(prefix):]))
    return f"{prefix}{n + 1:03d}"


def backfill_contract(contract):
    """Merge the case's output_contract keys with defaults.

    Never drops or renames unknown keys; missing defaults are supplied.
    """
    merged = dict(DEFAULT_CONTRACT)
    if isinstance(contract, dict):
        merged.update(contract)
    return merged


def result_skeleton(case_id, contract):
    """Build the initial legal result.json skeleton (status=in_progress)."""
    c = backfill_contract(contract)
    sk = {
        "case_id": case_id,
        "status": "in_progress",
        "deliverable": c.get("deliverable"),
        "sources": c.get("sources") if isinstance(c.get("sources"), list) else [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": (c.get("change_log") if isinstance(c.get("change_log"), list) else []) + [
            {"time": utc_now(), "action": "scaffold", "detail": "result skeleton written"}
        ],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"schema_version": SCHEMA_VERSION, "started_at": utc_now()},
    }
    for k, v in c.items():
        if k not in sk:
            sk[k] = v
    return sk


def init_claim_graph(case_id, materials=None):
    """Create an empty claim-evidence intermediate representation (IR)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "materials": materials or [],
        "claims": [],
        "sources": [],
        "draft": "",
        "summary": "",
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
    }


def check_overclaim(statement):
    """Heuristic: causal verbs in the statement may over-claim the design."""
    s = normalize(statement).lower()
    return any(h.lower() in s for h in CAUSAL_HINTS)


def validate_claim_graph(graph):
    """Validate the IR before rendering. Returns (issues, warnings).

    issues  = hard errors that must be fixed or force status=partial.
    warnings = soft gaps recorded in limitations.
    """
    issues = []
    warnings = []
    if not isinstance(graph, dict):
        return [{"level": "error", "msg": "claim graph is not a dict"}], []
    claims = graph.get("claims") or []
    sources = graph.get("sources") or []
    source_ids = {s.get("id") for s in sources if isinstance(s, dict) and s.get("id")}
    claim_ids = set()
    for c in claims:
        if not isinstance(c, dict):
            issues.append({"level": "error", "msg": "claim entry is not a dict"})
            continue
        cid = c.get("id")
        if not cid:
            issues.append({"level": "error", "msg": "claim missing id"})
            continue
        if cid in claim_ids:
            issues.append({"level": "error", "msg": f"duplicate claim id {cid}"})
        claim_ids.add(cid)
        if not (c.get("statement") or "").strip():
            issues.append({"level": "error", "msg": f"{cid} missing statement"})
        if not (c.get("quote") or "").strip():
            warnings.append({"level": "warn", "msg": f"{cid} missing verbatim quote"})
        if not (c.get("locator") or "").strip():
            warnings.append({"level": "warn", "msg": f"{cid} missing locator"})
        if c.get("support") not in SUPPORT_KINDS:
            issues.append({"level": "error", "msg": f"{cid} invalid support '{c.get('support')}'"})
        if c.get("strength") not in STRENGTH_RANKS:
            issues.append({"level": "error", "msg": f"{cid} invalid strength '{c.get('strength')}'"})
        if c.get("status") not in CLAIM_STATUSES:
            warnings.append({"level": "warn", "msg": f"{cid} invalid status '{c.get('status')}'"})
        for sid in (c.get("source_ids") or []):
            if sid not in source_ids:
                issues.append({"level": "error", "msg": f"{cid} references missing source '{sid}'"})
        if c.get("support") in ("indirect", "none", "conflict") and check_overclaim(c.get("statement", "")):
            warnings.append({
                "level": "warn",
                "msg": f"{cid} possible overclaim: causal wording with {c.get('support')} support",
            })
    return issues, warnings


def build_sources(graph):
    """Derive the result.json `sources` evidence map from the IR."""
    out = []
    for s in (graph.get("sources") or []):
        out.append({
            "key": s.get("id"),
            "value": s.get("citation") or "",
            "kind": s.get("type") or "unknown",
            "location": s.get("locator") or "",
            "source_doc": s.get("source_doc") or s.get("path") or "",
            "quote": s.get("quote") or "",
            "metadata_status": s.get("metadata_status") or "unverified",
            "change": s.get("note") or "",
        })
    for c in (graph.get("claims") or []):
        for sid in (c.get("source_ids") or []):
            out.append({
                "key": c.get("id"),
                "value": c.get("statement") or "",
                "kind": "claim_evidence",
                "location": c.get("locator") or "",
                "source_doc": sid,
                "quote": c.get("quote") or "",
                "change": f"support={c.get('support')}; strength={c.get('strength')}",
            })
    return out


def decide_status(graph, issues):
    if not isinstance(graph, dict) or not (graph.get("claims") or graph.get("sources")):
        return "insufficient_evidence"
    if any(i.get("level") == "error" for i in issues):
        return "partial"
    if not (graph.get("draft") or "").strip():
        return "partial"
    return "complete"


def render_markdown(graph):
    """Render the human-readable three-stage deliverable from the IR."""
    if not isinstance(graph, dict):
        return "# 文献证据驱动写作交付\n\n材料不足，无法生成成稿。\n"
    lines = ["# 文献证据驱动写作交付", ""]
    claims = graph.get("claims") or []
    sources = graph.get("sources") or []
    lines.append("## 1. 论断清单")
    lines.append("")
    for c in claims:
        lines.append(
            f"- **{c.get('id')}** [{c.get('strength', '?')} | {c.get('support', '?')}] "
            f"{c.get('statement', '')}"
        )
        lines.append(f"  - locator: `{c.get('locator', '')}`")
        if c.get("quote"):
            lines.append(f"  - quote: “{c.get('quote')}”")
        if c.get("source_ids"):
            lines.append(f"  - sources: {', '.join(c.get('source_ids') or [])}")
        if c.get("uncertainty"):
            lines.append(f"  - uncertainty: {c.get('uncertainty')}")
        if c.get("action"):
            lines.append(f"  - action: {c.get('action')}")
    lines.append("")
    lines.append("## 2. 证据表")
    lines.append("")
    lines.append("| 来源 | 类型 | 引用 | 定位 | 原文摘录 | 核验状态 |")
    lines.append("|---|---|---|---|---|---|")
    for s in sources:
        quote = (s.get("quote") or "")[:80]
        lines.append(
            f"| {s.get('id', '')} | {s.get('type', '')} | {s.get('citation', '')} "
            f"| `{s.get('locator', '')}` | “{quote}” | {s.get('metadata_status', '')} |"
        )
    lines.append("")
    lines.append("## 3. 成稿")
    lines.append("")
    draft = (graph.get("draft") or "").strip()
    if draft:
        lines.append(draft)
    else:
        lines.append("（成稿见 result.json 的 deliverable.draft；此处未附加草稿正文。）")
    lines.append("")
    return "\n".join(lines)


def list_materials(root, max_files=50, max_bytes=2 * 1024 * 1024):
    """Deterministic listing of readable text material files under root."""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext not in TEXT_EXTENSIONS and fn not in ("case.json", "task.md"):
                continue
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            item = {"path": p, "size": sz}
            if sz > max_bytes:
                item["skipped"] = "too large"
            out.append(item)
            if len(out) >= max_files:
                return out
    return out
