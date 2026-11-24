#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — cross-lingual known-item literature retrieval orchestrator.

Subcommands
  init      discover /input, extract case_id + output_contract, compute the
            phase plan, and atomically write a legal partial result skeleton
            to /app/output/result.json. Run this FIRST so a valid partial
            deliverable is on disk within the 180s hard deadline.
  phase     report the current phase, elapsed time and remaining budgets
            (900s total / 64 model requests) from work/state.json.
  patch     deep-merge a JSON patch file into result.json (incremental update).
  validate  verify result.json is legal JSON and contains case_id, all
            output_contract fields and the standard fields.
  status    print a short progress summary from result.json.

Standard library only. Default paths follow the runtime contract:
  --input  /input
  --output /app/output
  --work   /app/work
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

TOTAL_BUDGET_SECONDS = 900
TOTAL_BUDGET_REQUESTS = 64
SKELETON_DEADLINE_SECONDS = 180
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB per material file

# Standard semantic result fields the agent fills in via incremental patches,
# in addition to any output_contract fields carried over from the case.
SEMANTIC_FIELDS = [
    "research_question", "terminology_table", "queries", "results",
    "evidence_map", "dedup", "missing", "conflicts",
]

# Phase table: (phase_id, name, start_sec, end_sec, output_patch)
# Windows are advisory; phase 0 must complete before SKELETON_DEADLINE_SECONDS.
PHASES = [
    (0, "init", 0, 60, "result.json skeleton + work/state.json"),
    (1, "read_scope", 0, 180, "research_question + language pair + seeds"),
    (2, "terminology", 60, 300, "terminology_table"),
    (3, "retrieve", 120, 600, "queries + results"),
    (4, "identify", 300, 720, "language/version + dedup"),
    (5, "review", 480, 840, "evidence_map + conflicts + missing"),
    (6, "finalize", 720, 900, "validate + change_log + final status"),
]

LANG_ALIASES = {
    "pt": "pt", "por": "pt", "portuguese": "pt", "português": "pt", "pt-br": "pt",
    "en": "en", "eng": "en", "english": "en", "en-us": "en", "en-gb": "en",
    "es": "es", "spa": "es", "spanish": "es", "español": "es",
    "fr": "fr", "fra": "fr", "french": "fr", "français": "fr",
    "de": "de", "ger": "de", "deu": "de", "german": "de", "deutsch": "de",
    "zh": "zh", "chi": "zh", "zho": "zh", "chinese": "zh", "中文": "zh",
    "ja": "ja", "jpn": "ja", "japanese": "ja", "日语": "ja",
    "ru": "ru", "rus": "ru", "russian": "ru",
    "it": "it", "ita": "it", "italian": "it",
}

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.I)
ARXIV_RE = re.compile(r"arxiv[:\s/]*([a-z\-]+\.[A-Za-z]{2}/\d{7}|\d{4}\.\d{4,5})", re.I)
PMID_RE = re.compile(r"pmid[:\s]*(\d{5,9})", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")


# --------------------------------------------------------------------------
# Robust text / JSON IO
# --------------------------------------------------------------------------


def read_text(path):
    """Read a file as text, tolerating BOM and common bad encodings."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def safe_json_load(path):
    """Return (obj, error) where obj is None on failure."""
    try:
        text, _ = read_text(path)
    except OSError as exc:
        return None, "read_error:%s" % exc
    try:
        return json.loads(text), None
    except ValueError as exc:
        # Attempt to salvage the first balanced JSON object in the text.
        m = re.search(r"\{", text)
        if m:
            frag = _extract_balanced(text, m.start())
            if frag:
                try:
                    return json.loads(frag), "salvaged"
                except ValueError:
                    pass
        return None, "bad_json:%s" % exc


def _extract_balanced(text, start):
    depth = 0
    i = start
    in_string = False
    escaped = False
    while i < len(text):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def atomic_write_json(path, obj):
    """Atomically write JSON (UTF-8, 2-space indent) to path."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def deep_merge(base, patch):
    """Recursively merge dicts; lists and scalars are replaced wholesale."""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for key, value in patch.items():
            if key in out:
                out[key] = deep_merge(out[key], value)
            else:
                out[key] = value
        return out
    return patch


# --------------------------------------------------------------------------
# Input discovery
# --------------------------------------------------------------------------


def discover_inputs(input_dir):
    found = {"case": None, "task": None, "materials": [], "degradations": []}
    if not os.path.isdir(input_dir):
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
        for name in sorted(os.listdir(materials)):
            full = os.path.join(materials, name)
            if os.path.isfile(full):
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        found["degradations"].append(
                            "skipped_large_file:%s" % name)
                        continue
                except OSError:
                    pass
                found["materials"].append(full)
    return found


# --------------------------------------------------------------------------
# Contract extraction
# --------------------------------------------------------------------------


def find_contract(obj, depth=0):
    if isinstance(obj, dict):
        for key in ("output_contract", "outputContract", "output_contract_fields"):
            if key in obj and isinstance(obj[key], (dict, list)):
                return obj[key]
        for value in obj.values():
            result = find_contract(value, depth + 1)
            if result is not None:
                return result
    elif isinstance(obj, list) and depth < 4:
        for value in obj:
            result = find_contract(value, depth + 1)
            if result is not None:
                return result
    return None


def find_contract_from_text(text):
    if not text:
        return None
    for match in re.finditer(r"output_contract", text, re.I):
        idx = match.end()
        while idx < len(text) and text[idx] in " \t\r\n:=":
            idx += 1
        if idx < len(text) and text[idx] == "{":
            frag = _extract_balanced(text, idx)
            if frag:
                try:
                    return json.loads(frag)
                except ValueError:
                    continue
    return None


def find_case_id(case, task_text=""):
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


def contract_fields(contract):
    """Return (dict_fields, list_fields) top-level names for a contract."""
    if isinstance(contract, dict):
        return list(contract.keys()), []
    if isinstance(contract, list):
        return [], contract
    return [], []


def blank_for_contract(contract):
    """Produce a blank placeholder that mirrors the contract shape."""
    if isinstance(contract, dict):
        return {k: None for k in contract}
    if isinstance(contract, list):
        return []
    return None


# --------------------------------------------------------------------------
# Language & known-item seed helpers
# --------------------------------------------------------------------------


def normalize_lang(token):
    if token is None:
        return None
    key = str(token).strip().lower().replace("_", "-")
    if key in LANG_ALIASES:
        return LANG_ALIASES[key]
    if "-" in key:
        base = key.split("-")[0]
        if base in LANG_ALIASES:
            return LANG_ALIASES[base]
    return None


def language_pair(text):
    """Return {'source': .., 'target': .., 'direction': ..} guessed from text."""
    langs = []
    seen = set()
    for word in re.findall(r"[A-Za-zÀ-ÿ\u4e00-\u9fff]{2,}", text):
        norm = normalize_lang(word)
        if norm and norm not in seen:
            seen.add(norm)
            langs.append(norm)
    pair = {"source": None, "target": None, "direction": "undetermined"}
    if len(langs) >= 2:
        pair["source"] = langs[0]
        pair["target"] = langs[1]
        pair["direction"] = "bidirectional" if langs[0] != langs[1] else "monolingual"
    elif len(langs) == 1:
        pair["source"] = langs[0]
        pair["target"] = langs[0]
        pair["direction"] = "undetermined"
    return pair


def extract_seeds(text, case):
    """Extract known-item seeds: DOI, arXiv, PMID, year, quoted title fragments."""
    hay = text or ""
    if isinstance(case, dict):
        hay += " " + json.dumps(case, ensure_ascii=False)
    seeds = {"doi": [], "arxiv": [], "pmid": [], "year": [], "title_fragments": []}
    seeds["doi"] = [m.group(0).rstrip(".,;") for m in DOI_RE.finditer(hay)]
    seeds["arxiv"] = list(dict.fromkeys(ARXIV_RE.findall(hay)))
    seeds["pmid"] = list(dict.fromkeys(PMID_RE.findall(hay)))
    seeds["year"] = list(dict.fromkeys(YEAR_RE.findall(hay)))
    for m in re.finditer(r"[\"“]([^\"”]{8,200})[\"”]", hay):
        seeds["title_fragments"].append(m.group(1).strip())
    return seeds


# --------------------------------------------------------------------------
# Phase plan
# --------------------------------------------------------------------------


def build_state(start_ts, case_id, contract):
    now = time.time()
    elapsed = max(0, int(now - start_ts))
    phases = []
    for pid, name, s, e, out in PHASES:
        phases.append({
            "phase_id": pid,
            "name": name,
            "window": [s, e],
            "deadline_ts": start_ts + e,
            "output": out,
            "done": False,
        })
    return {
        "case_id": case_id,
        "start_ts": start_ts,
        "total_budget_seconds": TOTAL_BUDGET_SECONDS,
        "total_budget_requests": TOTAL_BUDGET_REQUESTS,
        "skeleton_deadline_seconds": SKELETON_DEADLINE_SECONDS,
        "elapsed_seconds": elapsed,
        "phases": phases,
        "request_usage": {"planned": 64, "used": 0, "reserved_each_phase": None},
        "contract_shape": "dict" if isinstance(contract, dict) else "list",
    }


def current_phase(state):
    now = time.time()
    elapsed = int(now - state["start_ts"])
    state["elapsed_seconds"] = max(0, elapsed)
    remaining = max(0, TOTAL_BUDGET_SECONDS - elapsed)
    active = None
    for ph in state["phases"]:
        if elapsed <= ph["window"][1]:
            active = ph
            break
    if active is None:
        active = state["phases"][-1]
    return active, remaining, elapsed


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_init(args):
    found = discover_inputs(args.input)
    case = {}
    case_err = None
    if found["case"]:
        case, err = safe_json_load(found["case"])
        if case is None:
            case, case_err = {}, "bad_json:%s" % err
        elif not isinstance(case, dict):
            case, case_err = {}, "case_not_object"
    else:
        case_err = "case.json_not_found"

    task_text = ""
    if found["task"]:
        try:
            task_text, _ = read_text(found["task"])
        except OSError as exc:
            case_err = (case_err or "") + " task.md_read_error:%s" % exc

    contract = find_contract(case) or find_contract_from_text(task_text)
    case_id = find_case_id(case, task_text)
    blank = blank_for_contract(contract) if contract is not None else {}

    start_ts = time.time()
    state = build_state(start_ts, case_id, contract)
    errors = [e for e in [case_err] if e]
    if errors:
        state["input_errors"] = errors

    skeleton = {
        "case_id": case_id,
        "status": "input_error" if case_err else "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [{
            "at": int(start_ts),
            "phase": 0,
            "action": "init",
            "summary": "wrote partial result skeleton",
        }],
        "errors": errors,
        "degradations": list(found["degradations"]),
        "pending_human": [],
        "runtime": {"started_at": start_ts, "elapsed_seconds": 0},
    }
    if isinstance(blank, dict):
        for key, value in blank.items():
            skeleton.setdefault(key, value)
    # Seed semantic fields with honest empty placeholders; do not override any
    # contract field that already uses the same name.
    for field in SEMANTIC_FIELDS:
        skeleton.setdefault(field, [] if field in
                            ("terminology_table", "queries", "results",
                             "evidence_map", "dedup", "missing", "conflicts")
                            else None)

    contract_field_names = []
    if isinstance(contract, dict):
        contract_field_names = list(contract.keys())
    elif isinstance(contract, list):
        contract_field_names = ["<list_contract>"]
    state["contract_field_names"] = contract_field_names

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(os.path.join(args.output, "result.json"), skeleton)
    atomic_write_json(os.path.join(args.work, "state.json"), state)
    # Also drop a human-readable plan so the agent can read it without JSON tooling.
    plan_lines = ["# phase plan", "case_id=%s" % case_id,
                  "budget=900s/64requests skeleton_deadline=180s"]
    for ph in state["phases"]:
        plan_lines.append("phase %d %s window=%d-%ds output=%s" %
                          (ph["phase_id"], ph["name"], ph["window"][0],
                           ph["window"][1], ph["output"]))
    with open(os.path.join(args.work, "plan.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(plan_lines) + "\n")
    print("init ok: case_id=%s status=%s contract=%s" %
          (case_id, skeleton["status"],
           "dict" if isinstance(contract, dict) else "list"))
    return 0


def cmd_phase(args):
    state_path = os.path.join(args.work, "state.json")
    if not os.path.exists(state_path):
        print("no state.json; run init first")
        return 1
    state, err = safe_json_load(state_path)
    if state is None:
        print("state.json unreadable:", err)
        return 1
    active, remaining, elapsed = current_phase(state)
    print("elapsed=%ds remaining=%ds" % (elapsed, remaining))
    print("active_phase=%d (%s) window=%d-%ds" %
          (active["phase_id"], active["name"], active["window"][0],
           active["window"][1]))
    print("next_output=%s" % active["output"])
    if elapsed > SKELETON_DEADLINE_SECONDS:
        print("skeleton_deadline=PASSED (should already be on disk)")
    else:
        print("skeleton_deadline=%ds away" %
              (SKELETON_DEADLINE_SECONDS - elapsed))
    return 0


def cmd_patch(args):
    result_path = os.path.join(args.output, "result.json")
    if not os.path.exists(result_path):
        print("result.json not found; run init first")
        return 1
    patch, err = safe_json_load(args.patch)
    if patch is None:
        print("patch unreadable:", err)
        return 1
    result, err = safe_json_load(result_path)
    if result is None:
        print("result.json unreadable:", err)
        return 1
    # `_change_log` is a control field, not result content: pop before merging.
    entry = patch.pop("_change_log", None) if isinstance(patch, dict) else None
    merged = deep_merge(result, patch)
    atomic_write_json(result_path, merged)
    # Append a change_log entry if the patch declared one.
    if entry:
        log = merged.setdefault("change_log", [])
        log.append(entry)
        atomic_write_json(result_path, merged)
    print("patch ok")
    return 0


def cmd_validate(args):
    result_path = os.path.join(args.output, "result.json")
    if not os.path.exists(result_path):
        print("MISSING result.json")
        return 1
    result, err = safe_json_load(result_path)
    if result is None:
        print("INVALID_JSON:", err)
        return 1
    issues = []
    if not isinstance(result, dict):
        issues.append("result_not_object")
    if not result.get("case_id"):
        issues.append("missing_case_id")
    if result.get("status") not in ("ok", "partial", "insufficient_evidence", "input_error"):
        issues.append("invalid_status:%s" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing_standard_field:%s" % key)
    # Check output_contract fields saved at init time (state.json).
    state_path = os.path.join(args.work, "state.json")
    if os.path.exists(state_path):
        state, _ = safe_json_load(state_path)
        if isinstance(state, dict):
            for name in state.get("contract_field_names", []):
                if name != "<list_contract>" and name not in result:
                    issues.append("missing_contract_field:%s" % name)
    if issues:
        print("ISSUES:", "; ".join(issues))
        return 1
    print("ok: case_id=%s status=%s" % (result.get("case_id"), result.get("status")))
    return 0


def cmd_status(args):
    result_path = os.path.join(args.output, "result.json")
    if not os.path.exists(result_path):
        print("no result.json")
        return 1
    result, err = safe_json_load(result_path)
    if result is None:
        print("unreadable:", err)
        return 1
    print("case_id=%s status=%s" % (result.get("case_id"), result.get("status")))
    print("sources=%d results=%d change_log=%d" %
          (len(result.get("sources", [])),
           len(result.get("results", []) if isinstance(result.get("results"), list) else []),
           len(result.get("change_log", []))))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="main.py",
                                     description="cross-lingual known-item retrieval orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_paths(p):
        p.add_argument("--input", default=DEFAULT_INPUT)
        p.add_argument("--output", default=DEFAULT_OUTPUT)
        p.add_argument("--work", default=DEFAULT_WORK)
        return p

    add_paths(sub.add_parser("init", help="discover input, write skeleton + phase plan"))
    p_phase = sub.add_parser("phase", help="report current phase and budgets")
    p_phase.add_argument("--work", default=DEFAULT_WORK)
    p_patch = sub.add_parser("patch", help="deep-merge a JSON patch into result.json")
    add_paths(p_patch)
    p_patch.add_argument("--patch", required=True)
    p_val = sub.add_parser("validate", help="validate result.json")
    p_val.add_argument("--output", default=DEFAULT_OUTPUT)
    p_val.add_argument("--work", default=DEFAULT_WORK)
    p_st = sub.add_parser("status", help="print progress summary")
    p_st.add_argument("--output", default=DEFAULT_OUTPUT)

    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "phase": cmd_phase,
        "patch": cmd_patch,
        "validate": cmd_validate,
        "status": cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
