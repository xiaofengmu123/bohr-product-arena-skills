#!/usr/bin/env python3
"""Shared helpers for the research-writing transform skill.

Standard library only. Every function here degrades gracefully: it returns a
value plus an error string (or None) instead of raising, so callers can always
produce a valid partial result. Paths are never assumed; callers discover them.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

MAX_FILE_BYTES = 2_000_000
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# Keys that look like collections get [] instead of None when mirroring an
# output_contract whose concrete value is unknown.
_LIST_HINTS = (
    "artifact", "evidence", "change", "source", "pending", "error",
    "degrad", "limit", "entry", "item", "list", "figure", "reference",
    "term", "glossar", "file", "output", "human", "todo", "map",
)

CLAIM_STRENGTH = {
    "fact": "源文为已确立或直接陈述的事实/数值，保留原强度，逐字核对数字与单位。",
    "causal": "源文明确为因果结论，保留因果表述并保留其限定条件与依据。",
    "correlational": "源文为相关性/关联结论，不得改写为因果。",
    "preliminary": "源文为初步/探索性结果，保留“初步表明”等限定语，不升格为定论。",
    "not_reported": "源文未报告，改写稿不得补造；写入 pending_human 或标注未报告。",
}

REQUIRED_RESULT_FIELDS = ("case_id", "status", "sources", "uncertainty", "change_log")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: str, max_bytes: int = MAX_FILE_BYTES) -> Tuple[Optional[str], Optional[str]]:
    """Read a UTF-8 text file tolerantly (BOM / bad encoding / oversize).

    Returns (text, error). error is None on success. Never raises.
    """
    if not isinstance(path, str) or not os.path.isfile(path):
        return None, "not_a_file:{}".format(path)
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_error:{}".format(exc)
    if size > max_bytes:
        return None, "too_large:{}bytes>{}".format(size, max_bytes)
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, "read_error:{}".format(exc)
    # Last resort: replace undecodable bytes so downstream processing continues.
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return raw.decode("utf-8", errors="replace"), "decoded_with_replace"
    except OSError as exc:
        return None, "read_error:{}".format(exc)


def load_json(path: str, max_bytes: int = MAX_FILE_BYTES) -> Tuple[Optional[Any], Optional[str]]:
    """Load JSON tolerantly; returns (data, error), never raises."""
    text, err = read_text(path, max_bytes)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, "json_error:{}".format(exc)


def write_json(data: Any, path: str) -> Optional[str]:
    """Write JSON with UTF-8; returns an error string or None."""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        return None
    except OSError as exc:
        return "write_error:{}".format(exc)


MATERIAL_EXTS = (
    ".pdf", ".txt", ".md", ".markdown", ".json", ".csv", ".tsv",
    ".docx", ".pptx", ".xlsx", ".tex", ".png", ".jpg", ".jpeg", ".html",
)


def discover_inputs(input_dir: str) -> Dict[str, Any]:
    """Scan an input directory; never assume fixed filenames or keys.

    Returns dict with keys: case (path or None), task (path or None),
    materials (list of paths), others (list), errors (list).
    """
    out: Dict[str, Any] = {
        "case": None, "task": None, "materials": [], "others": [], "errors": [],
    }
    if not os.path.isdir(input_dir):
        out["errors"].append("input_dir_missing:{}".format(input_dir))
        return out
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        out["errors"].append("listdir_error:{}".format(exc))
        return out
    for name in names:
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        low = name.lower()
        if low == "case.json":
            out["case"] = p
        elif low in ("task.md", "task.txt", "task.json", "instructions.md"):
            out["task"] = p
        elif low.endswith(MATERIAL_EXTS):
            out["materials"].append(p)
        else:
            out["others"].append(p)
    return out


def extract_case_id(case: Any) -> Optional[str]:
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId", "task_id"):
        if key in case and case[key] not in (None, ""):
            return str(case[key])
    for nest_key in ("task", "meta", "metadata"):
        nested = case.get(nest_key)
        if isinstance(nested, dict):
            for key in ("case_id", "caseId", "id"):
                if nested.get(key) not in (None, ""):
                    return str(nested[key])
    return None


def extract_output_contract(case: Any) -> Optional[Dict[str, Any]]:
    """Find an output_contract / schema if the runtime case declares one."""
    if not isinstance(case, dict):
        return None
    for key in (
        "output_contract", "outputContract", "output_schema", "outputSchema",
        "result_contract", "resultContract", "result_schema", "resultSchema",
    ):
        value = case.get(key)
        if isinstance(value, dict) and value:
            return value
        if isinstance(value, list) and value:
            return {"fields": value}
    return None


def mirror_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    """Mirror every contract key verbatim; unknown keys kept, values defaulted.

    Scalars become None, collections become []/{} so the skeleton is a valid,
    complete frame the agent fills in without renaming or dropping keys.
    """
    out: Dict[str, Any] = {}
    if not isinstance(contract, dict):
        return out
    for key, value in contract.items():
        low = str(key).lower()
        if isinstance(value, dict):
            out[key] = mirror_contract(value)
        elif isinstance(value, (list, tuple)):
            out[key] = []
        elif any(h in low for h in _LIST_HINTS):
            out[key] = []
        else:
            out[key] = None
    return out


def build_skeleton(case_id: Optional[str], contract: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build an early valid result.json frame.

    Contains case_id, all output_contract keys (mirrored), and the standard
    domain + honesty fields. status starts as 'partial' so a mid-run crash still
    leaves a complete, inspectable result.
    """
    skeleton: Dict[str, Any] = {
        "case_id": case_id,
        "status": "partial",
        "artifacts": [],
        "evidence_map": [],
        "change_log": [],
        "pending_human": [],
        "sources": [],
        "uncertainty": "",
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": now_iso(), "finished_at": None},
    }
    if contract:
        mirrored = mirror_contract(contract)
        for key, value in mirrored.items():
            if key not in skeleton:
                skeleton[key] = value
    return skeleton


def validate_result(data: Any) -> List[str]:
    """Check the result.json schema; returns a list of problem strings."""
    problems: List[str] = []
    if not isinstance(data, dict):
        return ["result_not_object"]
    for field in REQUIRED_RESULT_FIELDS:
        if field not in data:
            problems.append("missing:{}".format(field))
    if data.get("case_id") in (None, ""):
        problems.append("empty:case_id")
    if data.get("status") not in STATUSES:
        problems.append("bad_status:{}".format(data.get("status")))
    if "evidence_map" not in data and "artifacts" not in data:
        problems.append("missing_domain:artifacts/evidence_map")
    return problems
