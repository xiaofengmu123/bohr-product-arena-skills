#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — cross-lingual literature search: contract + evidence IR + renderer.

Architecture (deliberately different from a patch-merge orchestrator):

  * The source of truth is an append-only evidence log
        <work>/evidence.ndjson
    Each line is one self-describing evidence record (kind + body + locator +
    quote). Records are upserted by id, so re-running the same step is idempotent.
  * result.json is DERIVED deterministically from (state.json, evidence.ndjson)
    by render(). It is never mutated in place, so there are no orphaned or
    half-merged fields and re-rendering is a pure function of its inputs.
  * output_contract fields are backfilled at render time: known semantic field
    names are matched case-insensitively (including CJK aliases); unknown
    contract keys are preserved and filled with null / [] rather than dropped.

Standard library only.
"""

import hashlib
import json
import os
import re
import time

SKILL_VERSION = "2.0.0"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_BYTES = 2 * 1024 * 1024  # skip larger single text files; record degradation

# self-describing evidence record kinds
ALLOWED_KINDS = {
    "research_question", "term", "query", "result", "dedup",
    "conflict", "missing", "degradation", "error", "uncertainty",
}

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")


# --------------------------------------------------------------------------
# text / json IO with graceful fallback
# --------------------------------------------------------------------------


def _norm(name):
    """Normalize a field name: lowercase, drop whitespace/punctuation, keep CJK."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


# normalized name -> semantic section produced by build_sections()
ALIASES = {
    "caseid": "case_id", "caseidentifier": "case_id", "案例id": "case_id",
    "status": "status", "状态": "status",
    "sources": "sources", "source": "sources", "来源": "sources",
    "uncertainty": "uncertainty", "uncertainties": "uncertainty",
    "不确定性": "uncertainty", "不确定": "uncertainty",
    "changelog": "change_log", "changehistory": "change_log",
    "变更记录": "change_log", "变更": "change_log",
    "errors": "errors", "error": "errors", "错误": "errors",
    "degradations": "degradations", "degradation": "degradations",
    "降级": "degradations",
    "pendinghuman": "pending_human", "pending": "pending_human",
    "待人工": "pending_human", "待人工复核": "pending_human",
    "runtime": "runtime", "运行时": "runtime",
    "researchquestion": "research_question", "question": "research_question",
    "rq": "research_question", "scope": "research_question",
    "研究问题": "research_question", "问题": "research_question",
    "terminology": "terminology_table", "terminologytable": "terminology_table",
    "terms": "terminology_table", "termlist": "terminology_table",
    "glossary": "terminology_table", "术语表": "terminology_table",
    "术语": "terminology_table", "词表": "terminology_table",
    "queries": "queries", "query": "queries", "querylist": "queries",
    "查询": "queries", "查询式": "queries", "检索式": "queries",
    "results": "results", "result": "results", "candidates": "results",
    "hits": "results", "literature": "results", "papers": "results",
    "结果": "results", "文献": "results", "候选": "results", "候选集": "results",
    "evidence": "evidence_map", "evidencemap": "evidence_map",
    "证据": "evidence_map", "证据表": "evidence_map",
    "dedup": "dedup", "deduplication": "dedup", "duplicates": "dedup",
    "去重": "dedup", "查重": "dedup",
    "missing": "missing", "omissions": "missing", "gaps": "missing",
    "缺失": "missing", "遗漏": "missing",
    "conflicts": "conflicts", "conflict": "conflicts", "冲突": "conflicts",
}

COLLECTION_KEYS = {
    "terminology_table", "queries", "results", "evidence_map", "dedup",
    "missing", "conflicts", "sources", "uncertainty", "change_log",
    "errors", "degradations", "pending_human",
}


def semantic_key_for(name):
    return ALIASES.get(_norm(name))


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Read UTF-8 text with BOM / bad-encoding fallback. Returns (text, truncated)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", False
    if size > max_bytes:
        return "", True
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return "", False
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), False
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), False


def safe_json_load(path):
    text, _trunc = read_text(path)
    if not text:
        return None, "empty_or_unreadable"
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# input discovery (do not assume fixed file names)
# --------------------------------------------------------------------------


def discover_inputs(input_dir):
    found = {"case": None, "task": None, "materials": []}
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
    mat = os.path.join(input_dir, "materials")
    if os.path.isdir(mat):
        try:
            found["materials"] = [
                os.path.join(mat, n) for n in sorted(os.listdir(mat))
                if os.path.isfile(os.path.join(mat, n))
            ]
        except OSError:
            pass
    return found


def load_inputs(input_dir):
    found = discover_inputs(input_dir)
    case, case_err = {}, None
    if found["case"]:
        case, err = safe_json_load(found["case"])
        if case is None:
            case_err = "bad_json:" + err
    task_text = ""
    if found["task"]:
        task_text, _ = read_text(found["task"])
    if case_err is None and not found["case"] and not task_text:
        case_err = "input_missing:no_usable_case_or_task"
    return found, case, task_text, case_err


# --------------------------------------------------------------------------
# contract extraction (output_contract may live in case.json or task.md)
# --------------------------------------------------------------------------


def find_contract(obj, _depth=0):
    if isinstance(obj, dict):
        for key in ("output_contract", "outputContract", "output_contract_fields"):
            if key in obj and isinstance(obj[key], (dict, list)):
                return obj[key]
        if _depth < 6:
            for value in obj.values():
                result = find_contract(value, _depth + 1)
                if result is not None:
                    return result
    elif isinstance(obj, list) and _depth < 6:
        for value in obj:
            result = find_contract(value, _depth + 1)
            if result is not None:
                return result
    return None


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


def find_contract_from_text(text):
    if not text:
        return None
    for m in re.finditer(r"output_contract", text, re.I):
        idx = m.end()
        while idx < len(text) and text[idx] in " \t\r\n:=":
            idx += 1
        if idx < len(text) and text[idx] == "{":
            frag = _extract_balanced(text, idx)
            if frag:
                try:
                    return json.loads(frag)
                except Exception:  # noqa: BLE001
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


def _field_spec(name, spec):
    name = str(name)
    is_list = False
    if isinstance(spec, str):
        is_list = any(t in spec.lower() for t in ("list", "array", "[]", "列表", "多个"))
    elif isinstance(spec, dict):
        is_list = any(t in str(spec.get("type", "")).lower()
                      for t in ("list", "array", "[]"))
    elif isinstance(spec, list):
        is_list = True
    if semantic_key_for(name) in COLLECTION_KEYS:
        is_list = True
    return {"name": name, "is_list": is_list}


def normalize_contract(contract):
    fields = []
    if isinstance(contract, dict):
        for key, value in contract.items():
            fields.append(_field_spec(key, value))
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields.append(_field_spec(item, None))
            elif isinstance(item, dict) and item.get("name"):
                fields.append(_field_spec(item["name"], item.get("type")))
    return fields


# --------------------------------------------------------------------------
# evidence store (append-only JSONL, upsert by id)
# --------------------------------------------------------------------------


def ensure_id(rec):
    if not isinstance(rec, dict):
        return rec
    if rec.get("id"):
        return rec
    h = hashlib.sha1(
        json.dumps(rec, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    rec["id"] = "rec_" + h
    return rec


class EvidenceStore:
    def __init__(self, work_dir):
        self.path = os.path.join(work_dir, "evidence.ndjson")

    def load(self):
        records = []
        if not os.path.exists(self.path):
            return records
        text, _trunc = read_text(self.path)
        if not text:
            return records
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:  # noqa: BLE001
                records.append({
                    "id": "bad_line_%d" % i, "kind": "error",
                    "body": {"message": "evidence line could not be parsed"},
                    "locator": self.path, "quote": line[:200],
                })
        return records

    def append(self, records):
        existing = self.load()
        index = {}
        for i, rec in enumerate(existing):
            if isinstance(rec, dict) and rec.get("id"):
                index[rec["id"]] = i
        for rec in records:
            ensure_id(rec)
            rid = rec.get("id")
            if rid and rid in index:
                existing[index[rid]] = rec
            else:
                existing.append(rec)
                if rid:
                    index[rid] = len(existing) - 1
        self._write(existing)

    def _write(self, records):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


def state_path(work_dir):
    return os.path.join(work_dir, "state.json")


def save_state(work_dir, state):
    atomic_write_json(state_path(work_dir), state)


def load_state(work_dir):
    state, _err = safe_json_load(state_path(work_dir))
    return state if isinstance(state, dict) else None


# --------------------------------------------------------------------------
# deterministic render
# --------------------------------------------------------------------------


def build_change_log(records):
    counts = {}
    for rec in records:
        kind = rec.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    log = [{"step": "init", "note": "evidence store initialized; partial skeleton written"}]
    for kind in sorted(counts):
        log.append({"step": kind, "records": counts[kind]})
    return log


def build_sections(records):
    sections = {
        "research_question": None,
        "terminology_table": [], "queries": [], "results": [],
        "dedup": [], "missing": [], "conflicts": [],
        "sources": [], "uncertainty": [], "errors": [],
        "degradations": [], "pending_human": [],
        "evidence_map": {}, "change_log": build_change_log(records),
    }
    sources = []
    for rec in records:
        kind = rec.get("kind")
        body = rec.get("body") if isinstance(rec.get("body"), dict) else rec
        if kind == "research_question" and sections["research_question"] is None:
            sections["research_question"] = body
        elif kind == "term":
            sections["terminology_table"].append(body)
        elif kind == "query":
            sections["queries"].append(body)
        elif kind == "result":
            sections["results"].append(body)
        elif kind == "dedup":
            sections["dedup"].append(body)
        elif kind == "conflict":
            sections["conflicts"].append(body)
        elif kind == "missing":
            sections["missing"].append(body)
        elif kind == "error":
            sections["errors"].append(body)
        elif kind == "degradation":
            sections["degradations"].append(body)
        elif kind == "uncertainty":
            sections["uncertainty"].append(body)

        src = rec.get("source")
        if isinstance(body, dict) and not src:
            src = body.get("source")
        if src and src not in sources:
            sources.append(src)

        loc = rec.get("locator")
        quote = rec.get("quote")
        if loc or quote:
            sections["evidence_map"][rec.get("id")] = {
                "kind": kind, "locator": loc, "quote": quote,
            }

        if rec.get("pending_human"):
            sections["pending_human"].append({
                "id": rec.get("id"), "kind": kind,
                "note": rec.get("pending_note") or "",
            })

        conf = rec.get("confidence")
        if conf in ("low", "undetermined") and kind in ("result", "term", "query"):
            note = {"id": rec.get("id"), "kind": kind, "confidence": conf,
                    "note": rec.get("uncertainty_note") or ""}
            if note not in sections["uncertainty"]:
                sections["uncertainty"].append(note)

    sections["sources"] = sources
    return sections


def compute_status(state, records):
    case_err = state.get("case_error") if isinstance(state, dict) else None
    if case_err and not records:
        return "input_error"
    if not records:
        return "partial"
    has_result = any(r.get("kind") == "result" for r in records)
    has_rq = any(r.get("kind") == "research_question" for r in records)
    blocking = any(
        r.get("severity") == "blocking"
        and r.get("kind") in ("missing", "conflict", "degradation", "error")
        for r in records
    )
    if not has_result and not has_rq:
        return "insufficient_evidence"
    if blocking:
        return "partial"
    if has_result:
        return "ok"
    return "partial"


def elapsed(state):
    started = state.get("started_at") if isinstance(state, dict) else None
    if started:
        return round(time.time() - started, 3)
    return None


def render(state, records):
    sec = build_sections(records)
    full = {
        "case_id": state.get("case_id", "unknown_case"),
        "status": state.get("status_override") or compute_status(state, records),
        "sources": sec["sources"],
        "uncertainty": sec["uncertainty"],
        "change_log": sec["change_log"],
        "errors": sec["errors"],
        "degradations": sec["degradations"],
        "pending_human": sec["pending_human"],
        "runtime": elapsed(state),
        "research_question": sec["research_question"],
        "terminology_table": sec["terminology_table"],
        "queries": sec["queries"],
        "results": sec["results"],
        "evidence_map": sec["evidence_map"],
        "dedup": sec["dedup"],
        "missing": sec["missing"],
        "conflicts": sec["conflicts"],
    }
    contract_fields = state.get("contract_fields") or []
    for f in contract_fields:
        key = f["name"]
        current = full.get(key)
        if current not in (None, "", [], {}):
            continue
        sk = semantic_key_for(key)
        if sk:
            full[key] = full.get(sk, [] if f.get("is_list") else None)
        else:
            full[key] = [] if f.get("is_list") else None

    ordered = {}
    ordered["case_id"] = full["case_id"]
    ordered["status"] = full["status"]
    for f in contract_fields:
        key = f["name"]
        if key not in ordered:
            ordered[key] = full.get(key)
    for key, value in full.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate(result):
    issues = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    if not result.get("case_id"):
        issues.append("missing case_id")
    if result.get("status") not in STATUSES:
        issues.append("invalid status: %r" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing standard field: %s" % key)
    em = result.get("evidence_map")
    if isinstance(em, dict):
        for rid, entry in em.items():
            if not isinstance(entry, dict) or not (entry.get("locator") or entry.get("quote")):
                issues.append("evidence_map entry %s lacks locator/quote" % rid)
    return issues
