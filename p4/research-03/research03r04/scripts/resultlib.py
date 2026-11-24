#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic helpers for the systematic-review Skill (standard library only).

Design commitments
------------------
* "Discover before assuming": scan /input without hard-coding file names or keys.
* "Save early, improve incrementally": ``skeleton`` writes a valid partial result
  before any analysis; every later command only deep-merges into it.
* "Every claim traces to a verbatim quote": evidence cards carry locator + quote
  + content hash; ``selfcheck`` re-reads source text and verifies the quote is
  verbatim. Unverifiable quotes are reported, never silently trusted.
* "Honest degradation": missing, truncated, badly encoded or unparseable input is
  recorded in ``errors``/``degradations``/``limitations``/``pending_human`` and
  never replaced with invented data.
* "Deterministic re-run": sorted keys, stable evidence ids, PRISMA recomputed from
  screening records, no wall-clock-dependence in the content fields.
"""

import copy
import hashlib
import json
import os
import re
import time

# --------------------------------------------------------------------- status
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

CONFIDENCE_LEVELS = ("high", "moderate", "low", "insufficient")
STRENGTH_LEVELS = ("established", "suggestive", "preliminary", "not_reported", "conflicting")
DECISIONS = ("include", "exclude", "pending", "duplicate")

# Fallback domain contract used when case.json carries no output_contract. These
# are the fields the review protocol itself must always produce; unknown keys from
# a real output_contract are preserved alongside them.
DEFAULT_CONTRACT = {
    "review_question": "",
    "subquestions": [],
    "search_strategy": {},
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": {},
    "quality_appraisal": [],
    "synthesis": {"conclusions": [], "conflicts": [], "gaps": []},
    "evidence_map": {},
    "uncertainty": [],
}

MATERIAL_EXTS = (".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html",
                 ".htm", ".tex", ".bib", ".png", ".jpg", ".jpeg", ".webp", ".gif",
                 ".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp")
TEXT_EXTS = (".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html", ".htm",
             ".tex", ".bib")


# ----------------------------------------------------------------------- time
def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------ utilities
def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def norm_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def quote_hash(quote):
    return hashlib.sha1(norm_ws(quote).encode("utf-8", "replace")).hexdigest()[:12]


def dedupe(items):
    out = []
    for it in items or []:
        if it not in out:
            out.append(it)
    return out


def deep_merge(base, patch):
    for k, v in (patch or {}).items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


# ------------------------------------------------------------------ io layer
def read_text(path, cap=2 * 1024 * 1024):
    """Read text with encoding fallback; truncate above ``cap`` and note it."""
    errors, degs = [], []
    if not os.path.isfile(path):
        return None, ["not found: %s" % path], degs
    try:
        with open(path, "rb") as fh:
            raw = fh.read(cap + 1)
    except OSError as e:
        return None, ["cannot read %s: %s" % (path, e)], degs
    truncated = len(raw) > cap
    if truncated:
        raw = raw[:cap]
        degs.append("truncated %s at %d bytes" % (path, cap))
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        if enc in ("gb18030", "latin-1"):
            degs.append("decoded %s with fallback encoding %s" % (path, enc))
        return text, errors, degs
    return None, ["undecodable: %s" % path], degs


def load_json(path):
    errors, degs = [], []
    if not os.path.isfile(path):
        return None, ["not found: %s" % path], degs
    text, errs, d2 = read_text(path)
    errors.extend(errs)
    degs.extend(d2)
    if text is None:
        return None, errors, degs
    if not text.strip():
        return None, ["empty file: %s" % path], degs
    try:
        return json.loads(text), errors, degs
    except Exception as e:  # noqa: BLE001 - keep helper crash-free
        return None, ["invalid JSON %s: %s" % (path, e)], degs


def read_jsonl(path):
    items, errors = [], []
    if not os.path.isfile(path):
        return items, ["not found: %s" % path]
    text, errs, _ = read_text(path, cap=64 * 1024 * 1024)
    errors.extend(errs)
    if text is None:
        return items, errors
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception as e:  # noqa: BLE001
            errors.append("bad JSONL line %d in %s: %s" % (i, path, e))
    return items, errors


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        return []
    except OSError as e:
        return ["cannot write %s: %s" % (path, e)]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return []
    except OSError as e:
        return ["cannot write %s: %s" % (path, e)]


# ------------------------------------------------------------- input discovery
def discover_input(input_dir):
    inv = {"exists": False, "total_files": 0, "case_json": None, "task_md": None,
           "materials": [], "others": [], "errors": [], "degradations": []}
    if not os.path.isdir(input_dir):
        inv["errors"].append("input directory not found: %s" % input_dir)
        return inv
    inv["exists"] = True
    try:
        walk = list(os.walk(input_dir))
    except OSError as e:
        inv["errors"].append("cannot walk %s: %s" % (input_dir, e))
        return inv
    for root, dirs, files in walk:
        dirs.sort()
        files.sort()
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError as e:
                inv["errors"].append("cannot stat %s: %s" % (rel, e))
                continue
            inv["total_files"] += 1
            low = fn.lower()
            entry = {"path": p, "rel": rel, "size": size}
            if low == "case.json":
                inv["case_json"] = p
            elif low in ("task.md", "task.txt", "task"):
                if inv["task_md"] is None:
                    inv["task_md"] = p
            elif low.endswith(MATERIAL_EXTS):
                inv["materials"].append(entry)
            else:
                inv["others"].append(entry)
    inv["materials"].sort(key=lambda m: m["rel"])
    inv["others"].sort(key=lambda m: m["rel"])
    return inv


CASE_ID_KEYS = ("case_id", "caseId", "id", "armiId", "challenge_id", "challengeId")


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return ""
    for k in CASE_ID_KEYS:
        v = case_obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return ""


def extract_output_contract(case_obj):
    if not isinstance(case_obj, dict):
        return None
    for k in ("output_contract", "outputContract", "output", "result_schema", "resultSchema"):
        v = case_obj.get(k)
        if isinstance(v, dict):
            return v
    return None


def build_contract(case_obj):
    """Return the full key set that result.json must carry, with typed defaults."""
    oc = extract_output_contract(case_obj)
    contract = {}
    if isinstance(oc, dict):
        for k, v in oc.items():
            contract[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else None
    for k, v in DEFAULT_CONTRACT.items():
        cur = contract.get(k)
        if cur is None or cur == "" or cur == [] or cur == {}:
            contract[k] = copy.deepcopy(v)
    return contract


# ------------------------------------------------------------------- skeleton
def new_skeleton(case_id, contract, note=""):
    result = {"case_id": case_id or "unknown"}
    result.update(copy.deepcopy(contract or {}))
    result.update({
        "status": STATUS_PARTIAL,
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime_seconds": 0.0,
    })
    if note:
        result["change_log"].append({"ts": iso_now(), "action": "skeleton", "detail": note})
    return result


# ---------------------------------------------------------------- PRISMA flow
def norm_round(rd):
    rd = (rd or "").strip().lower()
    if rd in ("title", "title_abstract", "title-abstract", "tiab", "abstract", "screen1"):
        return "title_abstract"
    if rd in ("fulltext", "full", "full_text", "full-text", "pdf", "screen2"):
        return "fulltext"
    return rd or "title_abstract"


def norm_decision(d):
    d = (d or "").strip().lower()
    if d in ("include", "included"):
        return "include"
    if d in ("exclude", "excluded"):
        return "exclude"
    if d in ("pending", "unclear", "maybe"):
        return "pending"
    if d in ("duplicate", "dup"):
        return "duplicate"
    return "pending"


def compute_prisma(records):
    stages = {
        "title_abstract": {"screened": 0, "include": 0, "exclude": 0, "pending": 0,
                           "duplicate": 0, "reasons": {}},
        "fulltext": {"screened": 0, "include": 0, "exclude": 0, "pending": 0,
                     "duplicate": 0, "reasons": {}},
    }
    for r in records or []:
        if not isinstance(r, dict):
            continue
        rd = norm_round(r.get("round"))
        if rd not in stages:
            rd = "title_abstract"
        st = stages[rd]
        st["screened"] += 1
        dec = norm_decision(r.get("decision"))
        st[dec] += 1
        if dec == "exclude":
            crit = (r.get("criterion_id") or "unspecified")
            st["reasons"][crit] = st["reasons"].get(crit, 0) + 1
    ta, ft = stages["title_abstract"], stages["fulltext"]
    included = ft["include"] if ft["screened"] > 0 else ta["include"]
    return {
        "identified": ta["screened"],
        "duplicates_removed": ta["duplicate"] + ft["duplicate"],
        "title_abstract": {"screened": ta["screened"], "included": ta["include"],
                           "excluded": ta["exclude"], "excluded_reasons": ta["reasons"]},
        "fulltext": {"screened": ft["screened"], "included": ft["include"],
                     "excluded": ft["exclude"], "excluded_reasons": ft["reasons"]},
        "included": included,
    }


# ------------------------------------------------------------------ assembling
def build_sources(inv):
    sources = []
    if inv.get("case_json"):
        sources.append({"role": "case", "path": inv["case_json"], "status": "read"})
    if inv.get("task_md"):
        sources.append({"role": "task", "path": inv["task_md"], "status": "read"})
    for m in (inv.get("materials") or []):
        sources.append({"role": "material", "path": m["path"], "rel": m["rel"],
                        "size": m["size"], "status": "read"})
    return sources


def grade_status(result, inv):
    if result.get("status") == STATUS_INPUT_ERROR:
        return STATUS_INPUT_ERROR
    question = (result.get("review_question") or "").strip()
    conclusions = (result.get("synthesis") or {}).get("conclusions") or []
    evidence_map = result.get("evidence_map") or {}
    has_materials = bool((inv or {}).get("materials"))
    if not question and not has_materials and not conclusions:
        return STATUS_INSUFFICIENT
    if not question:
        return STATUS_PARTIAL
    if not ((result.get("search_strategy") or {}).get("query_blocks")):
        return STATUS_PARTIAL
    if not (result.get("screening_records")):
        return STATUS_PARTIAL
    if not conclusions or not evidence_map:
        return STATUS_PARTIAL
    for c in conclusions:
        for eid in (c.get("evidence_ids") or []):
            if eid not in evidence_map:
                return STATUS_PARTIAL
    return STATUS_OK


def write_md_review(args, result, inv):
    lines = [
        "# Systematic review",
        "",
        "- question: %s" % (result.get("review_question") or "(not set)"),
        "- status: %s" % result.get("status"),
        "- case_id: %s" % result.get("case_id"),
        "",
        "## PRISMA flow",
        "```json",
        json.dumps(result.get("prisma_flow") or {}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Conclusions",
    ]
    for c in (result.get("synthesis") or {}).get("conclusions") or []:
        lines.append("- [%s] %s  (evidence: %s)" % (
            c.get("strength", "not_reported"),
            c.get("text", ""),
            ", ".join(c.get("evidence_ids") or [])))
    lines += ["", "## Conflicts"]
    for c in (result.get("synthesis") or {}).get("conflicts") or []:
        lines.append("- %s (resolution: %s)" % (c.get("description", ""), c.get("resolution", "undetermined")))
    lines += ["", "## Gaps"]
    for g in (result.get("synthesis") or {}).get("gaps") or []:
        lines.append("- [%s] %s" % (g.get("kind", "?"), g.get("description", "")))
    lines += ["", "## Uncertainty"]
    for u in (result.get("uncertainty") or []):
        lines.append("- [%s] %s -> %s" % (u.get("type", "?"), u.get("item", ""), u.get("handling", "")))
    lines += ["", "## Limitations"]
    for lim in (result.get("limitations") or []):
        lines.append("- %s" % lim)
    lines += ["", "## Pending human review"]
    for ph in (result.get("pending_human") or []):
        lines.append("- %s" % ph)
    lines.append("")
    return write_text(os.path.join(args.output, "review.md"), "\n".join(lines))


# ---------------------------------------------------------------- run commands
def run_discover(args):
    return discover_input(args.input)


def run_skeleton(args):
    ensure_dir(args.output)
    ensure_dir(args.work)
    inv = discover_input(args.input)
    case_obj, errs, degs = None, [], []
    if inv.get("case_json"):
        case_obj, errs, degs = load_json(inv["case_json"])
    case_id = extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = build_contract(case_obj if isinstance(case_obj, dict) else None)
    result_path = os.path.join(args.output, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": list(errs), "degradations": list(degs)}
    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = load_json(result_path)
        if not isinstance(existing, dict):
            fresh = new_skeleton(case_id, contract, note="repaired unreadable result.json")
            summary["errors"].extend(write_json(result_path, fresh))
            summary["wrote"] = True
    else:
        result = new_skeleton(case_id, contract, note="skeleton before systematic review begins")
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        has_usable = bool(inv.get("case_json")) or bool(inv.get("task_md")) or bool(inv.get("materials"))
        if not inv["exists"] or not has_usable:
            result["status"] = STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no usable case.json/task.md/materials found under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json not found; review_question taken from task.md if readable")
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append("case.json present but unparseable")
        werr = write_json(result_path, result)
        summary["wrote"] = not bool(werr)
        summary["errors"].extend(werr)
    state = {"case_id": case_id, "contract": contract, "inventory": inv,
             "started_at": epoch_now(), "phase": "skeleton",
             "input_dir": args.input, "output_dir": args.output, "work_dir": args.work}
    summary["errors"].extend(write_json(os.path.join(args.work, "run_state.json"), state))
    return summary


def load_result(args):
    result, errs, degs = load_json(os.path.join(args.output, "result.json"))
    state = {}
    state_obj, e2, d2 = load_json(os.path.join(args.work, "run_state.json"))
    if isinstance(state_obj, dict):
        state = state_obj
    errs.extend(e2)
    degs.extend(d2)
    contract = state.get("contract") or copy.deepcopy(DEFAULT_CONTRACT)
    if not isinstance(result, dict):
        result = new_skeleton(state.get("case_id", ""), contract, note="created during command")
        result["status"] = STATUS_INSUFFICIENT
    return result, state, contract, errs, degs


def run_merge(args):
    payload, errs, degs = load_json(args.payload)
    if not isinstance(payload, dict):
        return {"ok": False, "errors": errs or ["payload is not a JSON object"],
                "degradations": degs}
    result, _, _, e2, d2 = load_result(args)
    result = deep_merge(result, payload)
    result.setdefault("change_log", []).append(
        {"ts": iso_now(), "action": "merge",
         "detail": "merged %s" % os.path.basename(args.payload)})
    errs.extend(write_json(os.path.join(args.output, "result.json"), result))
    return {"ok": not errs, "errors": errs, "degradations": degs + d2}


def run_finalize(args):
    result, state, contract, errs, degs = load_result(args)
    inv = state.get("inventory") or discover_input(args.input)

    protocol, e, d = load_json(os.path.join(args.work, "protocol.json"))
    errs += e
    degs += d
    protocol = protocol if isinstance(protocol, dict) else {}

    screening, e = read_jsonl(os.path.join(args.work, "screening.jsonl"))
    errs += e
    qa, e = read_jsonl(os.path.join(args.work, "quality_appraisal.jsonl"))
    errs += e
    evidence, e = read_jsonl(os.path.join(args.work, "evidence_cards.jsonl"))
    errs += e
    synth, e, d = load_json(os.path.join(args.work, "synthesis.json"))
    errs += e
    degs += d
    synth = synth if isinstance(synth, dict) else {}

    evidence_map = {}
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        evid = ev.get("evidence_id")
        if not evid:
            errs.append("evidence card without evidence_id skipped")
            continue
        ev.setdefault("quote_hash", quote_hash(ev.get("quote", "")))
        evidence_map[evid] = ev

    domain = {
        "review_question": protocol.get("review_question", ""),
        "subquestions": protocol.get("subquestions", []),
        "search_strategy": protocol.get("search_strategy", {}),
        "inclusion_criteria": protocol.get("inclusion_criteria", []),
        "exclusion_criteria": protocol.get("exclusion_criteria", []),
        "screening_records": screening,
        "prisma_flow": compute_prisma(screening),
        "quality_appraisal": qa,
        "synthesis": {
            "conclusions": synth.get("conclusions") or [],
            "conflicts": synth.get("conflicts") or [],
            "gaps": synth.get("gaps") or [],
        },
        "evidence_map": evidence_map,
        "uncertainty": synth.get("uncertainty") or [],
    }
    result = deep_merge(result, domain)
    result["sources"] = build_sources(inv)
    result["limitations"] = dedupe((result.get("limitations") or []) + (synth.get("limitations") or []))
    result["pending_human"] = dedupe((result.get("pending_human") or []) + (synth.get("pending_human") or []))
    result["errors"] = dedupe((result.get("errors") or []) + errs)
    result["degradations"] = dedupe((result.get("degradations") or []) + degs)
    result["runtime_seconds"] = round(epoch_now() - float(state.get("started_at") or epoch_now()), 3)
    result["status"] = grade_status(result, inv)
    result.setdefault("change_log", []).append(
        {"ts": iso_now(), "action": "finalize", "detail": "assembled result from work files"})

    errs += write_json(os.path.join(args.output, "result.json"), result)
    errs += write_json(os.path.join(args.output, "evidence_map.json"), evidence_map)
    errs += write_json(os.path.join(args.output, "screening_log.json"), screening)
    errs += write_md_review(args, result, inv)
    return {"ok": not errs, "errors": errs, "degradations": degs,
            "status": result["status"],
            "conclusions": len((result.get("synthesis") or {}).get("conclusions") or [])}


# ------------------------------------------------------------------ selfcheck
def collect_text_sources(args, inv):
    srcs = []
    exdir = os.path.join(args.work, "extracted")
    if os.path.isdir(exdir):
        for root, dirs, files in os.walk(exdir):
            dirs.sort()
            files.sort()
            for f in files:
                p = os.path.join(root, f)
                rel = os.path.relpath(p, exdir).replace("\\", "/")
                srcs.append(("extracted:" + rel, p))
    for m in (inv.get("materials") or []):
        rel = (m.get("rel") or "").lower()
        if rel.endswith(TEXT_EXTS):
            srcs.append(("material:" + m.get("rel"), m.get("path")))
    return srcs


def verify_evidence_quotes(evidence_map, srcs, cap=16 * 1024 * 1024):
    cache = {}
    for label, path in srcs:
        text, _, _ = read_text(path, cap=cap)
        cache[label] = norm_ws(text) if text is not None else ""
    reports = []
    for evid, ev in (evidence_map or {}).items():
        nq = norm_ws(ev.get("quote", ""))
        if not nq:
            reports.append({"evidence_id": evid, "ok": False, "reason": "empty_quote"})
            continue
        found = None
        for label, ntext in cache.items():
            if nq and nq in ntext:
                found = label
                break
        if found:
            reports.append({"evidence_id": evid, "ok": True, "source": found})
        else:
            reports.append({"evidence_id": evid, "ok": False,
                            "reason": "no_verbatim_match",
                            "sources_checked": len(cache)})
    return reports, cache


def run_selfcheck(args, strict=False):
    result, state, contract, load_errs, load_degs = load_result(args)
    inv = discover_input(args.input)
    checks = []
    nerr = nwarn = 0

    def add(cid, level, ok, detail=""):
        nonlocal nerr, nwarn
        if not ok:
            if level == "error":
                nerr += 1
            elif level == "warn":
                nwarn += 1
        checks.append({"id": cid, "level": level, "pass": bool(ok), "detail": detail})

    # 1-4. structural validity
    add("json_parses", "error", not load_errs, "; ".join(load_errs) or "result.json parsed")
    add("case_id", "error", bool(result.get("case_id")), "case_id=%r" % result.get("case_id"))
    add("status_valid", "error", result.get("status") in STATUSES,
        "status=%r" % result.get("status"))
    missing = [k for k in contract if k not in result]
    add("contract_fields", "error", not missing,
        "missing_contract_keys=%s" % (missing or "none"))

    # 5-6. question decomposition
    question = (result.get("review_question") or "").strip()
    add("review_question", "error", bool(question), "question=%r" % question[:80])
    subq = result.get("subquestions") or []
    add("subquestions", "error", len(subq) >= 1, "count=%d" % len(subq))
    add("subquestions_depth", "warn", len(subq) >= 2,
        "recommend >=2 subquestions; got %d" % len(subq))

    # 7-8. search strategy
    qblocks = (result.get("search_strategy") or {}).get("query_blocks") or []
    ok_qb = bool(qblocks) and all(
        b.get("database") and b.get("query") and b.get("intent") for b in qblocks)
    add("query_blocks", "error", ok_qb, "count=%d" % len(qblocks))
    bare = []
    for b in qblocks:
        q = (b.get("query") or "").strip()
        if q and " " in q and len(q.split()) <= 3 and not re.search(r"\b(AND|OR|NOT)\b", q, re.I):
            bare.append(b.get("id"))
    add("query_keyword_only", "warn", not bare,
        "blocks that look like bare keyword strings: %s" % (bare or "none"))

    # 9-10. inclusion/exclusion clauses
    inc = result.get("inclusion_criteria") or []
    exc = result.get("exclusion_criteria") or []
    add("criteria_present", "error", bool(inc) and bool(exc),
        "inclusion=%d exclusion=%d" % (len(inc), len(exc)))
    clause_ok = all(isinstance(c, dict) and c.get("id") and c.get("text") for c in inc + exc)
    add("criteria_clauses", "warn", clause_ok,
        "each criterion should carry id+text (judgeable clause)")

    # 11-12. screening records
    records = result.get("screening_records") or []
    add("screening_present", "error", bool(records) or result.get("status") == STATUS_INSUFFICIENT,
        "records=%d" % len(records))
    bad_excl = []
    for r in records:
        if norm_decision(r.get("decision")) == "exclude":
            if not (r.get("reason") and r.get("criterion_id")):
                bad_excl.append(r.get("record_id") or r.get("id") or "(unnamed)")
    add("screening_exclusions", "error", not bad_excl,
        "excludes missing reason/criterion_id: %s" % (bad_excl or "none"))

    # 13. PRISMA recount
    pf = result.get("prisma_flow") or {}
    recount = compute_prisma(records)
    same = json.dumps(pf, sort_keys=True, ensure_ascii=False) == json.dumps(recount, sort_keys=True, ensure_ascii=False)
    add("screening_recount", "error", same,
        "prisma_flow matches screening_records recount")

    # 14. quality appraisal
    qa = result.get("quality_appraisal") or []
    add("quality_present", "warn", bool(qa), "quality_appraisal entries=%d" % len(qa))
    qa_ok = True
    for item in qa:
        dims = item.get("dimensions") or []
        if not item.get("overall_grade") or not dims:
            qa_ok = False
        for dm in dims:
            if not (dm.get("basis_locator") and (dm.get("quote") or "").strip()):
                qa_ok = False
    add("quality_dimensions", "error", qa_ok,
        "each entry needs overall_grade + dimensions with basis_locator+quote")

    # 15-16. conclusions & evidence backlinks
    synth = result.get("synthesis") or {}
    conclusions = synth.get("conclusions") or []
    add("conclusions_present", "error", bool(conclusions), "count=%d" % len(conclusions))
    evmap = result.get("evidence_map") or {}
    unresolved = []
    for c in conclusions:
        for eid in (c.get("evidence_ids") or []):
            if eid not in evmap:
                unresolved.append(eid)
    add("conclusions_evidence", "error", not unresolved,
        "evidence_ids not in evidence_map: %s" % (unresolved or "none"))

    # 17-18. evidence card fields + verbatim verification
    miss_fields = [eid for eid, ev in evmap.items()
                   if not ev.get("locator") or not (ev.get("quote") or "").strip()]
    add("evidence_quote_fields", "error", not miss_fields,
        "cards missing locator/quote: %s" % (miss_fields or "none"))
    srcs = collect_text_sources(args, inv)
    reports, _cache = verify_evidence_quotes(evmap, srcs)
    if not srcs:
        add("evidence_verbatim", "warn", False,
            "no text sources to verify against (extract PDFs to work/extracted/*.txt)")
    else:
        bad = [r for r in reports if not r["ok"]]
        add("evidence_verbatim", "error", not bad,
            "verified=%d/%d unverified=%s" % (
                len(reports) - len(bad), len(reports),
                [b["evidence_id"] for b in bad][:5]))

    # 19-20. conflicts & gaps
    conflicts = synth.get("conflicts") or []
    add("conflicts_present", "warn", bool(conflicts), "count=%d" % len(conflicts))
    conf_bad = [c.get("id") for c in conflicts if not (c.get("source_a") and c.get("source_b"))]
    add("conflicts_paired", "error", not conf_bad,
        "conflict entries lacking two sources: %s" % (conf_bad or "none"))
    gaps = synth.get("gaps") or []
    add("gaps_present", "warn", bool(gaps), "count=%d" % len(gaps))
    gap_bad = [g.get("id") for g in gaps if not (g.get("kind") and g.get("description"))]
    add("gaps_located", "error", not gap_bad,
        "gap entries lacking kind/description: %s" % (gap_bad or "none"))

    # 21. uncertainty model
    uncertainty = result.get("uncertainty") or []
    add("uncertainty_present", "warn", bool(uncertainty), "count=%d" % len(uncertainty))
    un_bad = [u.get("type") or u for u in uncertainty
              if not (isinstance(u, dict) and u.get("type") and u.get("item"))]
    add("uncertainty_typed", "error", not un_bad,
        "uncertainty entries missing type/item: %s" % (un_bad or "none"))

    # 22. honesty fields
    need = ("sources", "uncertainty", "change_log", "limitations", "pending_human")
    missing_h = [k for k in need if k not in result]
    add("honesty_fields", "error", not missing_h,
        "missing=%s" % (missing_h or "none"))

    report = {
        "status": result.get("status"),
        "case_id": result.get("case_id"),
        "error_count": nerr,
        "warn_count": nwarn,
        "pass": nerr == 0,
        "checks": checks,
        "load_degradations": load_degs,
    }
    return report, (1 if (strict and nerr) else 0)
