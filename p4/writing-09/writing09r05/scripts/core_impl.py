#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core implementation for the results-discussion-writing skill.

Pure Python standard library only. Deterministic. Never fabricates data or
benchmark scores. All paths are arguments or defaults; nothing is hard-coded
to a specific machine except the documented runtime defaults.

The module concentrates the *contract* machinery: input normalization, output
contract extraction, result.json skeleton construction, append-only change
log, evidence-map validation, and claim-strength over-claim scanning. Keeping
this in one file lets main.py stay small and the self-test reuse it directly.
"""

import datetime
import json
import os
import re
import sys

UTF8_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
MAX_TEXT_BYTES = 2 * 1000 * 1000  # 2 MB cap per text file before truncation

# Fields the skill guarantees in every result.json on top of the case's own
# output_contract keys. `case_id` is handled separately and always present.
EXTRAS = [
    "status",
    "sources",
    "uncertainty",
    "pending_human",
    "change_log",
    "limitations",
    "runtime",
]

# Fallback contract when /input/case.json does not declare output_contract.
DEFAULT_CONTRACT = ["deliverable", "facts_sources", "version_boundaries"]

# Over-claim wording that must be removed or justified by a locator + verbatim
# quote before it may stay in the manuscript. Flagging is a warning, not a
# hard failure, because some of these words are legitimate with evidence.
FORBIDDEN_ZH = (
    "首次", "国际首次", "国内首次", "国际领先", "国内领先", "世界领先",
    "填补空白", "颠覆性", "革命性", "世界第一", "全球首创", "重大突破",
    "唯一", "首次发现",
)
FORBIDDEN_EN = (
    "first-in-class", "first-in-human", "unprecedented", "revolutionary",
    "paradigm-shifting",
)


def now_iso():
    """UTC ISO-8601 timestamp with second precision."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def read_text_robust(path):
    """Read a text file tolerating BOM and common encodings.

    Returns (text, encoding, error). Oversized files are truncated with an
    explicit marker rather than crashing; binary PDFs are reported as
    non-text and left to the platform pdf-text-tool.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, None, "unreadable: %s" % exc

    if len(raw) > MAX_TEXT_BYTES:
        text = raw[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
        return text + "\n[TRUNCATED]", "utf-8", "oversize"

    for enc in UTF8_ENCODINGS:
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8", "bad_encoding"


def load_json_robust(path):
    """Load JSON with BOM/bad-encoding tolerance.

    Returns (obj, errors). Never raises: a broken file yields ({}, [error]).
    """
    errors = []
    text, enc, err = read_text_robust(path)
    if text is None:
        return {}, [err]
    if err:
        errors.append("encoding: %s" % err)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append("json: %s (line %s col %s)" % (exc.msg, exc.lineno, exc.colno))
        return {}, errors
    if not isinstance(obj, dict):
        return {}, ["json root is %s, expected object" % type(obj).__name__]
    return obj, errors


def scan_inputs(input_dir):
    """Discover text material files; never assume fixed names.

    PDFs are listed but not read as binary text; the skill instructs the
    agent to use the platform pdf-text-tool for those. Returns a dict keyed
    by file name with path/size/kind/text/error.
    """
    found = {}
    if not os.path.isdir(input_dir):
        return found
    for name in sorted(os.listdir(input_dir)):
        full = os.path.join(input_dir, name)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = -1
        lower = name.lower()
        if lower.endswith(".pdf"):
            found[name] = {"path": full, "size": size, "kind": "pdf",
                           "text": None, "error": "binary; use pdf-text-tool"}
            continue
        if lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".svg")):
            found[name] = {"path": full, "size": size, "kind": "image",
                           "text": None, "error": "image; use pdf-render-tool/ocr-tool"}
            continue
        text, enc, err = read_text_robust(full)
        found[name] = {"path": full, "size": size, "kind": "text",
                       "text": text, "error": err}
    return found


def extract_contract(case):
    """Return (contract_keys, contract_def, notes).

    contract_keys are the top-level result.json field names the case declares
    and that must be preserved verbatim. contract_def carries the raw contract
    description when the case provides one. Missing contract falls back to the
    documented default.
    """
    notes = []
    raw = case.get("output_contract")
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
        contract_def = raw
    elif isinstance(raw, list):
        keys = []
        contract_def = raw
        for item in raw:
            if isinstance(item, dict) and "field" in item:
                keys.append(str(item["field"]))
            elif isinstance(item, str):
                keys.append(item)
    elif isinstance(raw, str):
        keys = [raw]
        contract_def = raw
    else:
        keys = list(DEFAULT_CONTRACT)
        contract_def = None
        notes.append("output_contract missing; using default contract")
    # dedupe while preserving order
    seen, ordered = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered, contract_def, notes


def _empty_for(kind):
    """A neutral placeholder by common contract type, guessed from the name."""
    lk = kind.lower()
    if any(w in lk for w in ("deliver", "manuscript", "file", "output")):
        return {"file": None, "content": None}
    if any(w in lk for w in ("source", "fact", "evidence", "map", "log", "list",
                             "item", "uncertain", "pending", "limit", "human")):
        return []
    if any(w in lk for w in ("boundary", "version", "meta", "info", "runtime",
                             "status", "diff", "record")):
        return {}
    return None


def build_skeleton(case, contract_keys, started_at=None):
    """Create an honest in_progress result.json with every contract key present.

    All contract fields start as explicit neutral placeholders (never omitted),
    plus the guaranteed extras and an initial change_log entry recording the
    skeleton write itself.
    """
    case_id = case.get("case_id") or case.get("id") or "unknown"
    started = started_at or now_iso()
    result = {"case_id": case_id}
    for k in contract_keys:
        result[k] = _empty_for(k)
    result["status"] = "in_progress"
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{
        "seq": 0,
        "ts": started,
        "actor": "skill",
        "action": "skeleton",
        "target": "result.json",
        "before": None,
        "after": "in_progress",
        "reason": "initial honest partial result, all contract fields present",
    }]
    result["limitations"] = []
    result["runtime"] = {
        "started_at": started,
        "ended_at": None,
        "seconds_elapsed": None,
        "model_requests_estimate": 0,
    }
    return result


def append_change(result, action, target, reason, before=None, after=None,
                  actor="agent"):
    """Append one entry to the change_log, keeping it append-only and numbered."""
    log = result.setdefault("change_log", [])
    seq = len(log)
    log.append({
        "seq": seq,
        "ts": now_iso(),
        "actor": actor,
        "action": action,
        "target": target,
        "before": before,
        "after": after,
        "reason": reason,
    })
    return seq


def forbid_scan(text):
    """Find over-claim wording in a manuscript text. Returns hit list.

    Each hit carries the offending token and its zero-based offset so a third
    party can open the text and check the exact sentence. The skill treats a
    hit as a *warning to justify or remove*, not an automatic rejection.
    """
    if not text:
        return []
    hits = []
    for token in FORBIDDEN_ZH:
        start = 0
        while True:
            idx = text.find(token, start)
            if idx < 0:
                break
            hits.append({"token": token, "offset": idx, "lang": "zh"})
            start = idx + 1
    for token in FORBIDDEN_EN:
        for m in re.finditer(r"\b" + re.escape(token) + r"\b", text, re.IGNORECASE):
            hits.append({"token": token, "offset": m.start(), "lang": "en"})
    hits.sort(key=lambda h: h["offset"])
    return hits


def validate_result(result, contract_keys):
    """Validate result.json structure. Returns (errors, warnings).

    errors = contract/structural violations that must be fixed before
    status=complete. warnings = quality signals (over-claim words, unverified
    evidence) that require a documented decision.
    """
    errors, warnings = [], []
    if not isinstance(result, dict):
        return ["result root is not an object"], []
    if not result.get("case_id"):
        errors.append("case_id missing or empty")
    for k in contract_keys:
        if k not in result:
            errors.append("missing output_contract field: %s" % k)
    for k in EXTRAS:
        if k not in result:
            errors.append("missing guaranteed field: %s" % k)
    if "change_log" in result:
        if not isinstance(result["change_log"], list):
            errors.append("change_log must be a list")
        else:
            for i, e in enumerate(result["change_log"]):
                if not isinstance(e, dict):
                    errors.append("change_log[%d] not an object" % i)
                elif "ts" not in e or "action" not in e or "target" not in e:
                    errors.append("change_log[%d] missing ts/action/target" % i)
    if "sources" in result:
        if not isinstance(result["sources"], list):
            errors.append("sources must be a list")
        else:
            for i, e in enumerate(result["sources"]):
                if isinstance(e, dict):
                    if not e.get("locator") or not e.get("quote"):
                        warnings.append("sources[%d] lacks locator or verbatim quote" % i)
    # over-claim scan over deliverable content, if present
    content_text = _collect_deliverable_text(result)
    hits = forbid_scan(content_text)
    if hits:
        warnings.append("over-claim wording found (%d hits): %s"
                        % (len(hits), ", ".join(h["token"] for h in hits[:6])))
    return errors, warnings


def _collect_deliverable_text(result):
    """Best-effort extraction of the deliverable text for the over-claim scan."""
    parts = []
    dv = result.get("deliverable")
    if isinstance(dv, dict):
        if isinstance(dv.get("content"), str):
            parts.append(dv["content"])
        if isinstance(dv.get("file"), str):
            parts.append(dv["file"])
    for key in ("facts_sources", "version_boundaries"):
        if isinstance(result.get(key), str):
            parts.append(result[key])
    return "\n".join(p for p in parts if p)


def write_json(path, obj):
    """Write pretty JSON with UTF-8 (no BOM) and trailing newline."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def summarize_case(case, task_text, inputs, contract_keys, contract_def, notes):
    """Build the normalized input summary the agent works from."""
    return {
        "case_id": case.get("case_id") or case.get("id") or "unknown",
        "case_keys": sorted(case.keys()),
        "output_contract_fields": contract_keys,
        "output_contract_definition": contract_def,
        "contract_notes": notes,
        "task_bytes": len(task_text or ""),
        "task_head": (task_text or "")[:400],
        "materials": {
            name: {"kind": m["kind"], "size": m["size"], "error": m["error"]}
            for name, m in inputs.items()
        },
        "material_names": sorted(inputs.keys()),
    }


def sigfig_digits(num_str):
    """Count significant figures of a normalized decimal string.

    Returns None when the string is not a plausible number. Used by the agent
    to keep manuscript values consistent with source precision.
    """
    s = str(num_str).strip()
    m = re.match(r"^([+-]?)(\d+)(?:\.(\d*))?$", s)
    if not m:
        m = re.match(r"^([+-]?)\.(\d+)$", s)
        if not m:
            return None
        digits = m.group(2)
        return len(digits.lstrip("0"))
    sign, int_part, frac_part = m.group(1), m.group(2), m.group(3)
    digits = int_part + frac_part
    digits = digits.lstrip("0")
    if digits == "":
        return 0
    if frac_part == "" and int_part != "0":
        # trailing zeros ambiguous: 1200 could be 2 or 4 sig figs
        return len(digits.rstrip("0")) or 1
    return len(digits)


if __name__ == "__main__":
    # Minimal direct-use guard: import does nothing; running prints a hint.
    print("core_impl is a library; run scripts/main.py or scripts/selftest.py")
    sys.exit(0)
