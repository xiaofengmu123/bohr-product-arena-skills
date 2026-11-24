#!/usr/bin/env python3
"""Core library for the fund-proposal writing Skill.

Differentiated design: a *three-layer intermediate representation (IR)*
rather than an ad-hoc ledger.

    L1 source_manifest  -- inventory of every input file (sha256, size, encoding)
    L2 evidence_bank    -- evidence entries, each with locator + verbatim quote
    L3 element_map      -- seven-element coverage + cross-element consistency matrix

Validation order is *validate-before-render*: evidence closure (查得到)
-> element coverage (七要素齐全) -> cross-element consistency (对得上)
-> draft -> self-check.

Standard library only. Deterministic output (no random, fixed key order).
Never fabricates scores, citations, or runtime results.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SEVEN_ELEMENTS = [
    "立项依据",
    "科学问题",
    "研究目标",
    "研究内容",
    "创新点",
    "技术路线",
    "研究基础与可行性",
]

# The four review gates stated in the task brief.
FOUR_GATES = [
    {
        "id": "立得住",
        "question": "立项依据是否说明该问题为何值得做、为何现在必须做，而非政策口号堆叠",
        "check": "element_map['立项依据'] 是否有 necessity 论证并引用可追溯依据",
    },
    {
        "id": "说得准",
        "question": "创新点是否写清与具体已有工作的差别，而非“首次/国际领先/填补空白”等不可核验修饰",
        "check": "每条创新点是否有 representative_work 对比与 evidence 引用",
    },
    {
        "id": "对得上",
        "question": "研究目标、研究内容、技术路线、考核指标是否逐条对应",
        "check": "consistency_matrix 是否把 goal/content/route/indicator 四列逐行对齐",
    },
    {
        "id": "查得到",
        "question": "数字、术语、引用、图表、关键论断是否可追溯到具体来源并区分已完成/拟开展",
        "check": "evidence_bank 每条是否有 locator + quote，且 status 非 unmapped",
    },
]

EVIDENCE_KINDS = (
    "number",
    "term",
    "citation",
    "figure",
    "claim",
    "policy",
    "prior_result",
    "method",
    "indicator",
)

EVIDENCE_STATUSES = ("mapped", "unmapped", "conflict", "pending_human")


# ---------------------------------------------------------------------------
# Filesystem / encoding helpers (tolerant, never raise on bad input)
# ---------------------------------------------------------------------------

MAX_READ_BYTES = 2 * 1024 * 1024  # 2 MB per file


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: str, max_bytes: int = MAX_READ_BYTES):
    """Read a text file tolerantly. Returns (text, encoding, truncated) or (None, reason, False)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        return None, f"unreadable: {exc}", False
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc, truncated
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(replace)", truncated


def load_json(path: str):
    """Load JSON tolerantly. Returns (obj, [errors])."""
    text, enc, truncated = read_text(path)
    if text is None:
        return None, [f"unreadable: {path}"]
    problems = []
    if truncated:
        problems.append(f"truncated at {MAX_READ_BYTES} bytes: {path}")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"bad json {path}: {exc}"]
    return obj, problems


def write_json(path: str, obj) -> None:
    """Write JSON deterministically (utf-8, indent=2, no ascii escaping)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def sha256_file(path: str):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Intermediate representation (workbench)
# ---------------------------------------------------------------------------

def new_workbench(case_id: str, started_at: str) -> dict:
    return {
        "schema_version": "1.0.0",
        "case_id": case_id,
        "started_at": started_at,
        "updated_at": started_at,
        "stage": "bootstrap",
        "source_manifest": [],   # L1
        "evidence_bank": [],     # L2
        "element_map": {},       # L3
        "consistency_matrix": [],  # 对得上: goal/content/route/indicator rows
        "gates": {g["id"]: "pending" for g in FOUR_GATES},
        "problems": [],
    }


def new_manifest_entry(path: str, rel: str) -> dict:
    text, enc, truncated = read_text(path)
    size = None
    try:
        size = os.path.getsize(path)
    except OSError:
        pass
    return {
        "path": path,
        "rel": rel,
        "size_bytes": size,
        "sha256": sha256_file(path),
        "encoding": enc,
        "truncated": truncated,
        "ext": os.path.splitext(path)[1].lower(),
        "parsed": text is not None,
    }


def new_evidence_entry(claim: str, kind: str, source_file: str, locator: str,
                       quote: str, status: str = "mapped") -> dict:
    if kind not in EVIDENCE_KINDS:
        kind = "claim"
    if status not in EVIDENCE_STATUSES:
        status = "mapped"
    return {
        "claim": claim,
        "kind": kind,
        "source_file": source_file,
        "locator": locator,      # e.g. "paper.md#p3" / "fig2" / "table1"
        "quote": quote,          # verbatim fragment from the material
        "status": status,
        "added_at": utcnow(),
    }


# ---------------------------------------------------------------------------
# Result.json contract
# ---------------------------------------------------------------------------

def new_result(case_id: str, contract_hint=None, out_dir=None) -> dict:
    now = utcnow()
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "artifact": {
            "path": None,
            "kind": None,
            "genre": None,
            "audience": None,
            "format": None,
            "elements_covered": [],
            "preview_path": None,
        },
        "fact_source_map": [],
        "version_boundary": {
            "modification_diff": [],
            "manual_confirm": [],
            "material_gaps": [],
            "anomalies": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": now,
            "updated_at": now,
            "elapsed_s": 0,
            "stage": "bootstrap",
            "requests_used": 0,
        },
    }
    if contract_hint is not None:
        # Preserve the case's own output_contract verbatim for the platform.
        result["contract_hint"] = contract_hint
    return result


def log_change(result: dict, action: str, reason: str, impact: str = "") -> None:
    entry = {"at": utcnow(), "action": action, "reason": reason, "impact": impact}
    result.setdefault("change_log", []).append(entry)
    result["runtime"]["updated_at"] = utcnow()


# ---------------------------------------------------------------------------
# Consistency / gate checks (validate-before-render)
# ---------------------------------------------------------------------------

def check_workbench(wb: dict) -> list:
    """Return a list of (code, detail) problems found in the workbench."""
    problems = []

    # L3: seven-element coverage
    missing = [e for e in SEVEN_ELEMENTS if e not in wb.get("element_map", {})]
    if missing:
        problems.append(("element_missing", missing))

    # L2: evidence closure
    bank = wb.get("evidence_bank", [])
    unmapped = [x.get("claim", "?") for x in bank if x.get("status") == "unmapped"]
    conflicts = [x.get("claim", "?") for x in bank if x.get("status") == "conflict"]
    no_quote = [x.get("claim", "?") for x in bank if not x.get("quote")]
    no_locator = [x.get("claim", "?") for x in bank if not x.get("locator")]
    if unmapped:
        problems.append(("evidence_unmapped", unmapped))
    if conflicts:
        problems.append(("evidence_conflict", conflicts))
    if no_quote:
        problems.append(("evidence_no_quote", no_quote))
    if no_locator:
        problems.append(("evidence_no_locator", no_locator))

    # 对得上: consistency matrix rows must align four columns
    for row in wb.get("consistency_matrix", []):
        for col in ("goal", "content", "route", "indicator"):
            if not row.get(col):
                problems.append(("consistency_missing_column", (row.get("goal"), col)))
    return problems


def evaluate_gates(wb: dict) -> dict:
    """Set gate states from workbench content; returns the updated gates dict."""
    gates = {g["id"]: "pending" for g in FOUR_GATES}
    bank = wb.get("evidence_bank", [])
    elements = wb.get("element_map", {})

    # 查得到
    if bank and all(x.get("status") == "mapped" and x.get("quote") and x.get("locator")
                    for x in bank):
        gates["查得到"] = "ok"
    elif bank:
        gates["查得到"] = "partial"

    # 立得住
    base = elements.get("立项依据", {})
    if base.get("necessity") and base.get("evidence_refs"):
        gates["立得住"] = "ok"
    elif base:
        gates["立得住"] = "partial"

    # 说得准
    innovation = elements.get("创新点", {})
    if innovation.get("points") and innovation.get("representative_work"):
        gates["说得准"] = "ok"
    elif innovation:
        gates["说得准"] = "partial"

    # 对得上
    matrix = wb.get("consistency_matrix", [])
    if matrix and all(all(row.get(c) for c in ("goal", "content", "route", "indicator"))
                      for row in matrix):
        gates["对得上"] = "ok"
    elif matrix:
        gates["对得上"] = "partial"

    wb["gates"] = gates
    return gates


def derive_status(wb: dict, requested: str = "done") -> str:
    """Honest status: never upgrade a partial workbench to done."""
    if requested == "failed":
        return "failed"
    problems = check_workbench(wb)
    if problems:
        return "partial"
    return requested if requested in ("done", "partial") else "partial"
