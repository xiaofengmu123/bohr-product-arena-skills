# -*- coding: utf-8 -*-
"""Deterministic conservation and contract checks (standard library only).

Every function here is pure: no random numbers, no wall-clock reads, no
filesystem access. They are consumed by ``main.py verify`` and exercised by
``selftest.py``. A check is a *heuristic tripwire*, not a substitute for the
model's own judgement: it flags edits that need a human/agent decision, and
the agent must either revert the edit or record an explicit reason.

Two families of checks are provided:

1. Conservation checks -- compare an ``original`` string with a ``revised``
   string and report numbers, units-adjacent tokens, bracketed citations,
   figure/table/equation references and hedge markers that were lost or added.
2. Contract checks -- verify that a ``result`` object mirrors every key of the
   supplied ``output_contract`` (recursively), that the change log is a valid
   append-only list, and that the required envelope fields are present.
"""

import json
import re
from collections import Counter

# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

# Numbers: integers (with optional thousands separators), decimals, scientific
# notation. Lookarounds keep years like 2020 and keep "1.5" intact while not
# splitting "1.2.3" style version strings mid-token.
NUMBER_RE = re.compile(
    r"(?<![\w.])"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?"
    r"(?:[eE][+-]?\d+)?"
    r"(?![\w.])"
)

# Bracketed citation groups: [1], [1,2], [3-5], [12, 13].
BRACKET_CITE_RE = re.compile(
    r"\[\s*\d+(?:\s*[,\-\u2013]\s*\d+)*\s*\]"
)

# Figure / table / equation references in English and Chinese.
FIGREF_RE = re.compile(
    r"\b(?:Fig(?:ure)?s?\.?|Table|Eq(?:uation)?s?\.?|Eq\.)\s*\(?\s*[0-9]+[A-Za-z]?\s*\)?"
    r"|(?<![\w])(?:图|表|公式|式)\s*\(?\s*[0-9]+[A-Za-z]?\)?"
)

# Hedges whose *removal* could silently strengthen a claim.
HEDGE_WEAK = [
    "may", "might", "could", "possibly", "potentially", "likely", "probably",
    "apparently", "seem", "seems", "seemed", "appear", "appears", "appeared",
    "suggest", "suggests", "suggested", "indicating", "indicate", "indicates",
    "preliminary", "tentative", "proposed", "hypothesis", "tends", "tend",
    "perhaps", "to our knowledge", "in part", "partly", "consistent with",
    "\u53ef\u80fd", "\u4e5f\u8bb8", "\u6216\u8bb8", "\u4f3c\u4e4e", "\u5927\u6982",
    "\u521d\u6b65", "\u6709\u5f85", "\u503e\u5411\u4e8e", "\u63d0\u793a",
    "\u8868\u660e", "\u6697\u793a", "\u63a8\u6d4b", "\u636e\u6211\u4eec\u6240\u77e5",
    "\u90e8\u5206\u5730", "\u4e0e...\u4e00\u81f4",
]

# Strong / absolute markers that should not be silently introduced or amplified.
HEDGE_STRONG = [
    "definitively", "conclusively", "undoubtedly", "absolutely", "certainly",
    "always", "never", "must", "prove", "proved", "proves", "proven",
    "confirm", "confirms", "confirmed", "establish", "establishes",
    "demonstrate", "demonstrates", "demonstrated", "guarantee", "ensure",
    "unequivocally", "invariably",
    "\u5f7b\u5e95", "\u6beb\u65e0\u7591\u95ee", "\u7edd\u5bf9", "\u5fc5\u7136",
    "\u603b\u662f", "\u4ece\u672a", "\u5fc5\u5b9a", "\u8bc1\u660e", "\u8bc1\u5b9e",
    "\u786e\u5b9a", "\u786e\u4fdd", "\u4e00\u5b9a", "\u5b8c\u5168",
]


def extract_numbers(text):
    """Sorted list of numeric tokens with thousands separators removed."""
    if not text:
        return []
    return sorted(m.group(0).replace(",", "") for m in NUMBER_RE.finditer(text))


def extract_citations(text):
    """Sorted list of normalized bracketed citation tokens."""
    if not text:
        return []
    return sorted(
        re.sub(r"\s+", "", m.group(0)) for m in BRACKET_CITE_RE.finditer(text)
    )


def extract_figrefs(text):
    """Sorted list of normalized figure/table/equation reference tokens."""
    if not text:
        return []
    return sorted(
        re.sub(r"\s+", "", m.group(0)) for m in FIGREF_RE.finditer(text)
    )


def extract_hedges(text):
    """Count occurrences of weak and strong hedge markers (case-folded)."""
    if not text:
        return {"weak": Counter(), "strong": Counter()}
    lowered = text.lower()
    weak = Counter()
    strong = Counter()
    for t in HEDGE_WEAK:
        c = _word_count(lowered, t.lower())
        if c:
            weak[t.lower()] = c
    for t in HEDGE_STRONG:
        c = _word_count(lowered, t.lower())
        if c:
            strong[t.lower()] = c
    return {"weak": weak, "strong": strong}


def _word_count(haystack, token):
    """Count non-overlapping occurrences of token as a word/phrase boundary."""
    n = 0
    start = 0
    while True:
        i = haystack.find(token, start)
        if i < 0:
            return n
        # avoid matching inside a longer word
        before_ok = i == 0 or not haystack[i - 1].isalnum()
        after_ok = (i + len(token) >= len(haystack)
                    or not haystack[i + len(token)].isalnum())
        if before_ok and after_ok:
            n += 1
        start = i + len(token)


# ---------------------------------------------------------------------------
# Conservation checks
# ---------------------------------------------------------------------------

def _diff(orig_counter, rev_counter):
    lost = orig_counter - rev_counter
    added = rev_counter - orig_counter
    lost_list = sorted(lost.elements())
    added_list = sorted(added.elements())
    return lost_list, added_list


def check_conservation(original, revised):
    """Compare numeric/citation/figure tokens between original and revised.

    Returns ``{"errors": [...], "warnings": [...]}``. A lost token is an
    error (content destroyed); an added token is a warning (possible new
    material that must be justified or removed).
    """
    errors, warnings = [], []
    for name, fn in (
        ("number", extract_numbers),
        ("citation", extract_citations),
        ("figref", extract_figrefs),
    ):
        lost, added = _diff(Counter(fn(original)), Counter(fn(revised)))
        for t in lost:
            errors.append(
                f"[{name}] lost token '{t}': original has it, revised does not"
            )
        for t in added:
            warnings.append(
                f"[{name}] added token '{t}': not in original; justify or remove"
            )
    return {"errors": errors, "warnings": warnings}


def check_hedge(original, revised):
    """Flag hedge removal (weakening reversal) or strength injection.

    Removed weak hedges and added strong markers are warnings because both
    can shift the strength of a claim; the agent must revert or write an
    explicit reason. This is a lexical heuristic, so it only ever prompts a
    decision -- it does not silently block an edit.
    """
    warnings = []
    o = extract_hedges(original)
    r = extract_hedges(revised)
    weak_lost, _ = _diff(o["weak"], r["weak"])
    _, strong_added = _diff(o["strong"], r["strong"])
    for t in weak_lost:
        warnings.append(f"[hedge] weak marker '{t}' removed; strength may increase")
    for t in strong_added:
        warnings.append(f"[hedge] strong marker '{t}' added; strength may increase")
    return {"errors": [], "warnings": warnings}


# ---------------------------------------------------------------------------
# Contract checks
# ---------------------------------------------------------------------------

REQUIRED_ENVELOPE = [
    "case_id", "status", "sources", "uncertainty", "change_log",
    "pending_human", "errors", "degradations", "runtime",
]

VALID_STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

CHANGE_LOG_REQUIRED_KEYS = {"stage", "field", "reason"}


def _collect_contract_keys(contract, prefix=""):
    """Recursively list every leaf-key path in a contract dict."""
    keys = []
    if not isinstance(contract, dict):
        return keys
    for k, v in contract.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_collect_contract_keys(v, path))
        else:
            keys.append(path)
    return keys


_MISSING = object()


def _get_path(obj, path):
    """Return the value at a dotted path, or _MISSING if any key is absent.

    A present key whose value is None is *not* missing: the contract mirror
    intentionally fills unfilled fields with null, which is a valid state.
    """
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def check_contract(result, contract):
    """Verify result mirrors every contract key and carries the envelope."""
    errors, warnings = [], []
    if not isinstance(contract, dict):
        warnings.append("[contract] output_contract is not a dict; envelope only")
        return {"errors": errors, "warnings": warnings}

    for path in _collect_contract_keys(contract):
        if _get_path(result, path) is _MISSING:
            errors.append(f"[contract] missing field '{path}'")

    if isinstance(result, dict):
        for k in REQUIRED_ENVELOPE:
            if k not in result:
                errors.append(f"[contract] missing envelope field '{k}'")
        status = result.get("status")
        if status not in VALID_STATUSES:
            errors.append(f"[contract] invalid status {status!r}")
        if not result.get("case_id"):
            errors.append("[contract] case_id is empty")
    else:
        errors.append("[contract] result is not a JSON object")
    return {"errors": errors, "warnings": warnings}


def check_change_log(result):
    """Validate the append-only change log structure."""
    errors, warnings = [], []
    log = result.get("change_log") if isinstance(result, dict) else None
    if not isinstance(log, list):
        errors.append("[change_log] missing or not a list")
        return {"errors": errors, "warnings": warnings}
    for i, entry in enumerate(log):
        if not isinstance(entry, dict):
            errors.append(f"[change_log] entry {i} is not an object")
            continue
        for k in CHANGE_LOG_REQUIRED_KEYS:
            if k not in entry:
                errors.append(f"[change_log] entry {i} missing '{k}'")
    if len(log) < 2:
        warnings.append(
            "[change_log] fewer than 2 entries; scaffold+finalize should both log"
        )
    return {"errors": errors, "warnings": warnings}


def check_envelope(result):
    """Confirm all required envelope fields exist with the right types."""
    errors = []
    if not isinstance(result, dict):
        return {"errors": ["[envelope] result is not a JSON object"], "warnings": []}
    for k in REQUIRED_ENVELOPE:
        if k not in result:
            errors.append(f"[envelope] missing '{k}'")
    for k in ("sources", "uncertainty", "change_log", "pending_human",
              "errors", "degradations"):
        if k in result and not isinstance(result[k], list):
            errors.append(f"[envelope] '{k}' must be a list")
    if "runtime" in result and not isinstance(result["runtime"], dict):
        errors.append("[envelope] 'runtime' must be an object")
    return {"errors": errors, "warnings": []}


def run_checks(edits, glossary, contract, result=None):
    """Aggregate conservation + structural checks over an edits payload.

    ``edits``: dict with ``units`` (list of {id, original, revised, ...}).
    ``glossary``: dict, ignored by checks but accepted for a uniform API.
    ``contract``: the output_contract dict (may be None).
    ``result``: optional final result object to contract-check.
    """
    report = {
        "errors": [],
        "warnings": [],
        "stats": {
            "units": 0,
            "units_missing_revised": 0,
            "units_missing_reason": 0,
            "units_with_number": 0,
        },
    }
    units = []
    if isinstance(edits, dict):
        units = edits.get("units") or []
    report["stats"]["units"] = len(units)

    for u in units:
        if not isinstance(u, dict):
            report["errors"].append("[unit] non-object unit entry")
            continue
        uid = u.get("id", "?")
        original = u.get("original") or ""
        revised = u.get("revised") or ""
        if not revised.strip():
            report["stats"]["units_missing_revised"] += 1
            report["errors"].append(f"[unit {uid}] revised text is empty")
        if not (u.get("reason") or "").strip():
            report["stats"]["units_missing_reason"] += 1
            report["errors"].append(f"[unit {uid}] missing reason")
        if extract_numbers(original):
            report["stats"]["units_with_number"] += 1
        cons = check_conservation(original, revised)
        hed = check_hedge(original, revised)
        for e in cons["errors"]:
            report["errors"].append(f"[unit {uid}] {e}")
        for w in cons["warnings"] + hed["warnings"]:
            report["warnings"].append(f"[unit {uid}] {w}")

    if result is not None:
        report["errors"].extend(check_contract(result, contract)["errors"])
        report["warnings"].extend(check_contract(result, contract)["warnings"])
        for fn in (check_change_log, check_envelope):
            out = fn(result)
            report["errors"].extend(out["errors"])
            report["warnings"].extend(out["warnings"])
    return report
