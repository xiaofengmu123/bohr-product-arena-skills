#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py — deterministic, standard-library-only helpers and pipeline for the
cross-lingual literature search skill.

Design goals (robustness variant):
  * every entry point degrades instead of raising on malformed input;
  * atomic writes never leave a half-written result.json;
  * list ordering is deterministic (input order / sorted keys, no randomness);
  * single files above the size guard are skipped and recorded, not parsed;
  * bad JSON / bad encoding are captured in errors/degradations and execution
    continues with the remaining material.

This module performs NO network access and makes NO literature claims; the
intellectual retrieval work is done by the calling agent, which patches
evidence back into result.json via the "patch" command.
"""

import json
import os
import re
import tempfile

MAX_TEXT_BYTES = 2 * 1024 * 1024          # skip any single file larger than 2 MB
READ_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

# Fields always present in result.json, independent of any case contract.
STANDARD_FIELDS = [
    "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime",
]

# Default semantic result fields used when a case carries no explicit contract.
DEFAULT_RESULT_FIELDS = [
    "research_question", "terminology_table", "queries", "results",
    "evidence_map", "dedup", "missing", "conflicts",
]

# Field names whose natural empty value is a list, not null.
LIST_LIKE_FIELDS = {
    "sources", "uncertainty", "pending_human", "change_log", "errors",
    "degradations", "terminology_table", "queries", "results", "evidence_map",
    "dedup", "missing", "conflicts", "candidates", "references", "documents",
    "artifacts", "highlights", "records", "hits", "notes",
    "target_languages", "source_languages", "languages", "keywords", "terms",
    "authors", "tags", "categories", "steps", "phases", "criteria",
}


# --------------------------------------------------------------------------
# I/O helpers that never raise on malformed content
# --------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Return (text, encoding_used, error). Never raises on decode errors."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", None, "stat_error:%s" % exc
    if size > max_bytes:
        return "", None, "too_large:%d_bytes" % size
    raw = None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return "", None, "read_error:%s" % exc
    if raw is None:
        return "", None, "read_error"
    last = None
    for enc in READ_ENCODINGS:
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError) as exc:
            last = "%s:%s" % (enc, exc.__class__.__name__)
    return "", None, "undecodable(%s)" % (last or "unknown")


def safe_json_load(path):
    """Return (obj, error). obj is None when the file is not a usable object."""
    text, _, read_err = read_text(path)
    if read_err and not text:
        return None, "read_error:%s" % read_err
    try:
        obj = json.loads(text)
    except ValueError as exc:
        return None, "json_parse:%s" % exc
    return obj, None


def atomic_write_text(path, text):
    """Write text via a temp file + os.replace. Returns True on success."""
    directory = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return False
    suffix = os.path.splitext(path)[1] or ".tmp"
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def atomic_write_json(path, obj):
    """Serialize obj deterministically and atomically. Returns True on success."""
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)
    text += "\n"
    return atomic_write_text(path, text)


# --------------------------------------------------------------------------
# Merge semantics (patch command)
# --------------------------------------------------------------------------

def deep_merge(base, patch):
    """Merge patch into base in place: dicts recurse, everything else replaces.

    Lists are replaced wholesale (per the contract in SKILL.md), scalars and
    nulls replace, and nested dicts merge key-by-key.
    """
    if isinstance(base, dict) and isinstance(patch, dict):
        for key, value in patch.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                deep_merge(base[key], value)
            else:
                base[key] = value
    return base


# --------------------------------------------------------------------------
# Input discovery (do not assume fixed names)
# --------------------------------------------------------------------------

def discover_inputs(input_dir):
    """Return {'case': path|None, 'task': path|None, 'materials': [paths]}."""
    found = {"case": None, "task": None, "materials": []}
    if not input_dir or not os.path.isdir(input_dir):
        return found
    try:
        names = sorted(os.listdir(input_dir))
    except OSError:
        return found
    for name in names:
        full = os.path.join(input_dir, name)
        if os.path.isdir(full):
            continue
        low = name.lower()
        if low == "case.json" or low.endswith(".case.json"):
            found["case"] = found["case"] or full
        elif low == "task.md" or (low.endswith(".md") and "task" in low):
            found["task"] = found["task"] or full
    materials = os.path.join(input_dir, "materials")
    if os.path.isdir(materials):
        found["materials"] = sorted(
            os.path.join(materials, n) for n in os.listdir(materials)
            if os.path.isfile(os.path.join(materials, n))
        )
    return found


# --------------------------------------------------------------------------
# Contract extraction (output_contract may sit in case.json or task.md)
# --------------------------------------------------------------------------

def find_contract(obj, _depth=0):
    """Recursively locate output_contract / outputContract in a JSON object."""
    if isinstance(obj, dict):
        for key in ("output_contract", "outputContract", "output_contract_fields"):
            if key in obj and isinstance(obj[key], (dict, list)):
                return obj[key]
        for value in obj.values():
            result = find_contract(value, _depth + 1)
            if result is not None:
                return result
    elif isinstance(obj, list) and _depth < 4:
        for value in obj:
            result = find_contract(value, _depth + 1)
            if result is not None:
                return result
    return None


def _extract_balanced(text, start):
    """Extract the balanced JSON object whose '{' begins at index `start`."""
    depth = 0
    i = start
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def find_contract_from_text(text):
    """Locate an inline output_contract JSON object in free text (task.md)."""
    if not text:
        return None
    for match in re.finditer(r"output_contract", text, re.I):
        idx = match.end()
        while idx < len(text) and text[idx] in " \t\r\n:=":
            idx += 1
        if idx < len(text) and text[idx] == "{":
            fragment = _extract_balanced(text, idx)
            if fragment:
                try:
                    return json.loads(fragment)
                except ValueError:
                    continue
    return None


def find_case_id(case, task_text=""):
    """Extract case_id, preferring explicit case fields over challenge id."""
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "case_identifier"):
            if case.get(key):
                return str(case[key])
    if task_text:
        m = re.search(r"case[_ -]?id[\"'':=\s]+([A-Za-z0-9_\-\.]+)", task_text, re.I)
        if m:
            return m.group(1)
    if isinstance(case, dict):
        for key in ("challenge_id", "challengeId", "id"):
            if case.get(key):
                return str(case[key])
    return "unknown_case"


def contract_top_fields(contract):
    """Return the ordered top-level field names for a contract (dict only)."""
    if isinstance(contract, dict):
        return list(contract.keys())
    return []


# --------------------------------------------------------------------------
# Skeleton construction
# --------------------------------------------------------------------------

def _empty_value(key):
    if key == "runtime":
        return {"seconds_elapsed": 0}
    if key.lower() in LIST_LIKE_FIELDS:
        return []
    return None


def build_skeleton(contract, case_id, status="partial"):
    """Build the minimum legal result.json object.

    All contract fields are echoed (empty value), then standard fields are
    guaranteed. This is written to disk before any intellectual work so a
    partial deliverable always exists.
    """
    out = {"case_id": case_id, "status": status}
    fields = contract_top_fields(contract)
    if not fields:
        fields = list(DEFAULT_RESULT_FIELDS)
    for key in fields:
        out[key] = _empty_value(key)
    for key in STANDARD_FIELDS:
        if key not in out:
            out[key] = _empty_value(key)
    return out


# --------------------------------------------------------------------------
# Script / language helpers (honest, script-only classification)
# --------------------------------------------------------------------------

_SCRIPT_PATTERNS = {
    "han": r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]",
    "kana": r"[\u3040-\u30ff]",
    "hangul": r"[\uac00-\ud7af\u1100-\u11ff]",
    "cyrillic": r"[\u0400-\u04ff]",
    "arabic": r"[\u0600-\u06ff]",
    "latin": r"[A-Za-z]",
}

_SCRIPT_LANGUAGE_HINT = {
    "han": {"hint": "zh (possibly ja/ko historical use)", "confidence": "script_only"},
    "kana": {"hint": "ja", "confidence": "script_only"},
    "hangul": {"hint": "ko", "confidence": "script_only"},
    "cyrillic": {"hint": "ru/uk/bg/other Cyrillic", "confidence": "script_only"},
    "arabic": {"hint": "ar/fa/ur/other Arabic script", "confidence": "script_only"},
    "latin": {"hint": "en/pt/de/fr/es/other Latin script", "confidence": "script_only"},
    "han+latin_mixed": {"hint": "zh/en mixed title", "confidence": "script_only"},
    "mixed": {"hint": "multilingual", "confidence": "script_only"},
    "undetermined": {"hint": "no readable script", "confidence": "none"},
}


def detect_script(text):
    """Return the dominant writing system of a text, not a language ID.

    This is deliberately conservative: script is a hint, language must be
    confirmed from publisher / abstract / body evidence before use.
    """
    if not text or not text.strip():
        return "undetermined"
    counts = {name: len(re.findall(pattern, text))
              for name, pattern in _SCRIPT_PATTERNS.items()}
    total = sum(counts.values())
    if total == 0:
        return "undetermined"
    best = max(counts, key=counts.get)
    if counts[best] / total >= 0.5:
        return best
    if counts["han"] and counts["latin"]:
        return "han+latin_mixed"
    return "mixed"


def script_language_hint(script):
    """Return a human-readable language hint for a script name."""
    return _SCRIPT_LANGUAGE_HINT.get(script, {"hint": "unknown", "confidence": "none"})


# --------------------------------------------------------------------------
# Pipeline entry points (shared by main.py CLI and selftest.py)
# --------------------------------------------------------------------------

def _record(elapsed):
    return {"seconds_elapsed": round(elapsed, 3)}


def run_init(input_dir, output_dir, work_dir):
    """Discover input, extract case_id/contract, atomically write a skeleton.

    Returns (exit_code, summary_dict). exit_code is always 0 unless the
    result.json itself cannot be written (catastrophic, still reported).
    """
    import time
    started = time.time()

    found = discover_inputs(input_dir)
    case = {}
    errors = []
    degradations = []

    if found["case"]:
        case, err = safe_json_load(found["case"])
        if case is None:
            case = {}
            errors.append("case_json:%s" % err)
        elif not isinstance(case, dict):
            case = {}
            errors.append("case_json:not_an_object")
    else:
        errors.append("case.json_not_found")

    task_text = ""
    if found["task"]:
        task_text, _, read_err = read_text(found["task"])
        if read_err:
            errors.append("task.md:%s" % read_err)

    contract = find_contract(case)
    if contract is None:
        contract = find_contract_from_text(task_text)

    case_id = find_case_id(case, task_text)

    status = "partial"
    if found["case"] is None and found["task"] is None:
        status = "input_error"

    # Material scan: record but do not parse in init; keep the skeleton cheap.
    for path in found["materials"]:
        name = os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            degradations.append({"file": name, "issue": "stat_error"})
            continue
        if size > MAX_TEXT_BYTES:
            degradations.append({"file": name, "bytes": size,
                                 "issue": "too_large_skipped"})

    skeleton = build_skeleton(contract, case_id, status)
    skeleton["errors"] = errors
    skeleton["degradations"] = degradations
    skeleton["runtime"] = _record(time.time() - started)

    ok = atomic_write_json(os.path.join(output_dir, "result.json"), skeleton)
    state = {
        "case_id": case_id,
        "has_contract": contract is not None,
        "contract_fields": contract_top_fields(contract),
        "materials": [os.path.basename(p) for p in found["materials"]],
    }
    atomic_write_json(os.path.join(work_dir, "state.json"), state)

    summary = {
        "status": skeleton["status"],
        "case_id": case_id,
        "contract_fields": contract_top_fields(contract),
        "errors": errors,
        "degradations": degradations,
        "result_written": ok,
    }
    if not ok:
        return 1, summary
    return 0, summary


def run_patch(output_dir, patch_path):
    """Deep-merge a JSON patch into result.json. Returns (exit_code, message)."""
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(result_path):
        return 2, "result.json not found; run init first"
    result, err = safe_json_load(result_path)
    if result is None:
        return 2, "result.json unreadable: %s" % err
    if not isinstance(result, dict):
        return 2, "result.json is not an object"
    patch, perr = safe_json_load(patch_path)
    if patch is None:
        return 2, "patch unreadable: %s" % perr
    if not isinstance(patch, dict):
        return 2, "patch must be a JSON object"

    # Convenience: a top-level "_note" becomes a change_log entry.
    note = patch.pop("_note", None)
    deep_merge(result, patch)
    if note is not None:
        entry = {"event": note} if not isinstance(note, dict) else note
        log = result.setdefault("change_log", [])
        if isinstance(log, list):
            log.append(entry)
    if not atomic_write_json(result_path, result):
        return 1, "failed to write result.json"
    return 0, "patched"


def run_validate(output_dir, work_dir):
    """Check result.json legality and contract completeness. Exit 0 = clean."""
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(result_path):
        return 1, "FAIL result.json missing"
    result, err = safe_json_load(result_path)
    if result is None:
        return 1, "FAIL bad json: %s" % err
    if not isinstance(result, dict):
        return 1, "FAIL result is not an object"

    issues = []
    if not result.get("case_id"):
        issues.append("case_id missing/empty")
    if result.get("status") not in ALLOWED_STATUS:
        issues.append("invalid status: %r" % result.get("status"))
    for field in STANDARD_FIELDS:
        if field not in result:
            issues.append("missing standard field: %s" % field)

    state, _ = safe_json_load(os.path.join(work_dir, "state.json"))
    contract_fields = (state or {}).get("contract_fields") or []
    for field in contract_fields:
        if field not in result:
            issues.append("missing contract field: %s" % field)

    if issues:
        return 1, "FAIL " + "; ".join(issues)
    return 0, "PASS valid (status=%s)" % result.get("status")


def run_status(output_dir, work_dir):
    """Print a short progress summary. Always exit code 0."""
    result_path = os.path.join(output_dir, "result.json")
    result, err = safe_json_load(result_path)
    if result is None:
        return 0, "no result.json yet (%s)" % err
    lines = [
        "case_id=%s" % result.get("case_id"),
        "status=%s" % result.get("status"),
    ]
    for field in ("terminology_table", "queries", "results", "evidence_map",
                  "dedup", "missing", "conflicts"):
        if field in result:
            value = result[field]
            count = len(value) if isinstance(value, list) else (0 if value is None else 1)
            lines.append("%s=%d" % (field, count))
    lines.append("change_log=%d" % len(result.get("change_log") or []))
    lines.append("errors=%d degradations=%d" % (
        len(result.get("errors") or []), len(result.get("degradations") or [])))
    return 0, "\n".join(lines)


def self_test():
    """Run robustness scenarios in a throwaway directory. Returns (ok, report)."""
    import shutil
    import time

    tmp = tempfile.mkdtemp(prefix="clsearch-selftest-")
    lines = []
    failures = []

    def check(name, condition, detail=""):
        lines.append(("%s PASS" if condition else "%s FAIL") % name
                     + ((" :: " + detail) if detail else ""))
        if not condition:
            failures.append(name)

    def make_dir(*parts):
        path = os.path.join(tmp, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    try:
        # 1) empty input directory
        empty_in = make_dir("empty_in")
        out1 = make_dir("out1"); work1 = make_dir("work1")
        code, summary = run_init(empty_in, out1, work1)
        check("empty_input_exit0", code == 0, "code=%s" % code)
        check("empty_input_status", summary["status"] == "input_error",
              "status=%s" % summary["status"])
        res1, e1 = safe_json_load(os.path.join(out1, "result.json"))
        check("empty_input_valid_json", res1 is not None and e1 is None)
        check("empty_input_has_case_id", isinstance(res1, dict) and bool(res1.get("case_id")))

        # 2) case with explicit output_contract
        case_in = make_dir("case_in")
        with open(os.path.join(case_in, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "case-synth-1",
                       "output_contract": {"research_question": "", "results": [], "score": 0}},
                      fh, ensure_ascii=False)
        out2 = make_dir("out2"); work2 = make_dir("work2")
        run_init(case_in, out2, work2)
        res2, _ = safe_json_load(os.path.join(out2, "result.json"))
        check("contract_fields_echoed",
              all(k in res2 for k in ("research_question", "results", "score")))
        check("contract_list_default", isinstance(res2.get("results"), list))

        # 3) bad JSON case.json
        bad_in = make_dir("bad_in")
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": ')
        out3 = make_dir("out3"); work3 = make_dir("work3")
        code3, summary3 = run_init(bad_in, out3, work3)
        check("bad_json_exit0", code3 == 0, "code=%s" % code3)
        check("bad_json_recorded", any("case_json" in e for e in summary3["errors"]),
              "errors=%s" % summary3["errors"])

        # 4) oversized material file is skipped
        big_in = make_dir("big_in")
        with open(os.path.join(big_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id":"case-synth-big"}')
        mat_dir = make_dir("big_in", "materials")
        big_path = os.path.join(mat_dir, "big.pdf")
        with open(big_path, "wb") as fh:
            fh.write(b"\x00" * (MAX_TEXT_BYTES + 1))
        out4 = make_dir("out4"); work4 = make_dir("work4")
        _, summary4 = run_init(big_in, out4, work4)
        check("oversized_skipped",
              any(d.get("issue") == "too_large_skipped" for d in summary4["degradations"]),
              "degradations=%s" % summary4["degradations"])

        # 5) bad encoding tolerated
        enc_in = make_dir("enc_in")
        with open(os.path.join(enc_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id":"case-synth-enc"}')
        with open(os.path.join(enc_in, "task.md"), "wb") as fh:
            fh.write(b"\xff\xfe\x00\x00 broken")
        out5 = make_dir("out5"); work5 = make_dir("work5")
        run_init(enc_in, out5, work5)
        res5, _ = safe_json_load(os.path.join(out5, "result.json"))
        check("bad_encoding_valid_output", isinstance(res5, dict))

        # 6) patch deep-merge and _note convenience
        run_init(case_in, out2, work2)
        patch_path = os.path.join(tmp, "patch.json")
        with open(patch_path, "w", encoding="utf-8") as fh:
            json.dump({"_note": "t0 skeleton", "status": "ok",
                       "results": [{"title": "synthetic", "language": "pt",
                                    "version": "original", "locator": "tmp#1",
                                    "quote": "n/a"}], "score": 7}, fh,
                      ensure_ascii=False)
        code6, _ = run_patch(out2, patch_path)
        res6, _ = safe_json_load(os.path.join(out2, "result.json"))
        check("patch_exit0", code6 == 0)
        check("patch_status_ok", res6.get("status") == "ok")
        check("patch_note_appended", any(e.get("event") == "t0 skeleton"
                                         for e in res6.get("change_log", [])))

        # 7) determinism: two runs over the same input give the same skeleton
        out7a = make_dir("out7a"); work7a = make_dir("work7a")
        out7b = make_dir("out7b"); work7b = make_dir("work7b")
        run_init(case_in, out7a, work7a)
        run_init(case_in, out7b, work7b)
        ra, _ = safe_json_load(os.path.join(out7a, "result.json"))
        rb, _ = safe_json_load(os.path.join(out7b, "result.json"))
        # runtime timing is allowed to differ; everything else must match.
        ra2 = {k: v for k, v in ra.items() if k != "runtime"}
        rb2 = {k: v for k, v in rb.items() if k != "runtime"}
        check("deterministic_except_runtime", ra2 == rb2)

        # 8) validate gate: the contract-complete output passes
        code8, msg8 = run_validate(out2, work2)
        check("validate_pass", code8 == 0, msg8)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = not failures
    report = "\n".join(lines) + "\nRESULT: %s (%d failures)" % (
        "ALL PASS" if ok else "FAILURES", len(failures))
    return ok, report
