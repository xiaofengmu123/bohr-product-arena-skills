"""core_impl.py — deterministic scaffolding for research-question-driven search.

Commands (invoked through scripts/main.py, thin wrapper):

  run        scan /input, write skeleton state, assemble a legal partial result.json
  set-plan   merge sub_questions / query_plan / term_expansion / stop_decision
  import     append/merge candidate records (all-record screening ledger)
  evidence   append evidence entries (query / round / conflict / gap)
  assemble   merge state into result.json + screening.md + plan.md
  selfcheck  validate result.json against the checklist; prints PASS/FAIL

Design goals (upgrade over a naive single-pass keyword search):
  * every returned record is screened (included / excluded / uncertain /
    merged_duplicate); exclusions are never silently dropped.
  * each sub-question is annotated with six retrieval elements (object /
    mechanism / method / time_range / comparison / evidence_requirement).
  * coverage is traced per sub-question and gaps are surfaced automatically.
  * duplicates are merged with provenance; conflicting records are both kept
    and flagged, never silently reconciled.
  * scoring is transparent: 0.6*relevance + 0.2*metadata + 0.2*source_reliability.

Pure standard library. Deterministic given identical state files; runtime
counters and timestamps are the only intentionally non-deterministic fields.
"""

import argparse
import json
import os
import re
import sys
import time

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_FILE_BYTES = 2 * 1024 * 1024
CONTRACT_KEYS = ("output_contract", "output", "schema", "required_fields", "contract", "result_schema")

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# Text / JSON IO with graceful degradation (BOM, bad encoding, bad JSON, size)
# --------------------------------------------------------------------------- #

def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Return (text_or_None, error_or_None). Never raises on IO/encoding."""
    if not os.path.exists(path):
        return None, "missing file: %s" % path
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat error: %s" % exc
    if size > max_bytes:
        return None, "skipped: size %d > %d bytes" % (size, max_bytes)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read error: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", "replace"), "decoded with replacement"


def load_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - any JSON failure degrades, not crashes
        return None, "bad json: %s" % exc


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def read_json(path):
    if not os.path.exists(path):
        return None
    data, _ = load_json(path)
    return data


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# State files under /app/work
# --------------------------------------------------------------------------- #

def state_path(work, name):
    return os.path.join(work, name)


def load_state(work, name, default):
    data = read_json(state_path(work, name))
    if data is None:
        return default
    return data


def save_state(work, name, data):
    ensure_dir(work)
    write_json(state_path(work, name), data)


def append_change(meta, action, reason, status):
    meta.setdefault("change_log", [])
    meta["change_log"].append({
        "time": now_iso(),
        "action": action,
        "reason": reason,
        "status": status,
    })
    return meta


# --------------------------------------------------------------------------- #
# Input discovery
# --------------------------------------------------------------------------- #

def scan_input(input_dir):
    """Inventory /input without assuming fixed names. Returns a dict."""
    inventory = {"files": [], "materials": [], "degradations": []}
    if not os.path.isdir(input_dir):
        return inventory, "input dir missing: %s" % input_dir
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            size = os.path.getsize(full) if os.path.exists(full) else 0
            text, err = read_text(full)
            entry = {"path": rel, "size": size}
            if err and err.startswith("skipped"):
                entry["status"] = "skipped"
                inventory["degradations"].append("%s: %s" % (rel, err))
            elif name.lower() in ("case.json", "task.md", "task.json", "notes.md"):
                entry["status"] = "text"
                inventory["files"].append(entry)
            elif name.lower().endswith((".pdf", ".md", ".txt", ".json", ".csv")):
                entry["status"] = "material"
                inventory["materials"].append(entry)
            else:
                entry["status"] = "other"
                inventory["materials"].append(entry)
    return inventory, None


def find_case_payload(input_dir, inventory):
    """Read case.json (or equivalent) and task.md; return (case_data, task_text, errors)."""
    case_data, task_text, errors = None, "", []
    for entry in inventory.get("files", []):
        path = os.path.join(input_dir, entry["path"])
        data, err = load_json(path) if entry["path"].lower().endswith(".json") else (None, None)
        if entry["path"].lower().endswith(".json"):
            if err:
                errors.append(err)
            elif isinstance(data, dict) and case_data is None:
                case_data = data
        if entry["path"].lower() in ("task.md", "task.txt") or entry["path"].lower().endswith(".md"):
            text, _ = read_text(path)
            if text:
                task_text = text
    return case_data, task_text, errors


def extract_case_id(case_data, input_dir):
    if isinstance(case_data, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "task_id"):
            val = case_data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, (int, float)):
                return str(val)
    base = os.path.basename(os.path.abspath(input_dir)) or "unknown"
    return base if base not in ("", "/") else "unknown"


def extract_research_question(case_data, task_text):
    if isinstance(case_data, dict):
        for key in ("research_question", "question", "task", "query", "prompt"):
            val = case_data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        content = case_data.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:4000]
    if task_text:
        # First non-empty, non-heading line is a reasonable default when absent.
        for line in task_text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and len(s) > 6:
                return s[:4000]
    return ""


def extract_contract(case_data):
    """Echo the output contract fields, defaulting missing values to null/[]."""
    if not isinstance(case_data, dict):
        return None, None
    src = None
    for key in CONTRACT_KEYS:
        if key in case_data:
            src = key
            raw = case_data[key]
            break
    if src is None:
        return None, None
    if isinstance(raw, dict):
        contract = {}
        for k, v in raw.items():
            if isinstance(v, (str, int, float, bool, type(None), list, dict)):
                contract[k] = v
            else:
                contract[k] = None
        return contract, src
    if isinstance(raw, list):
        return {str(k): None for k in raw}, src
    return None, src


def material_inventory(inventory):
    return [{"path": m["path"], "size": m["size"]} for m in inventory.get("materials", [])]


# --------------------------------------------------------------------------- #
# run: skeleton -> parse -> assemble
# --------------------------------------------------------------------------- #

def cmd_run(params):
    input_dir = params.get("input") or "/input"
    output_dir = params.get("output") or "/app/output"
    work_dir = params.get("work") or "/app/work"
    ensure_dir(work_dir)
    ensure_dir(output_dir)

    inventory, inv_err = scan_input(input_dir)
    case_data, task_text, parse_errors = find_case_payload(input_dir, inventory)

    case_id = extract_case_id(case_data, input_dir)
    research_question = extract_research_question(case_data, task_text)
    contract, contract_src = extract_contract(case_data)

    case_summary = {
        "case_id": case_id,
        "research_question": research_question,
        "task_text": (task_text or "")[:6000],
        "constraints": {},
        "output_contract": contract if contract is not None else {},
        "output_contract_source": contract_src,
        "materials": material_inventory(inventory),
        "input_errors": list(parse_errors) + ([inv_err] if inv_err else []),
        "degradations": list(inventory.get("degradations", [])),
    }

    # Honest initial status: empty/absent input cannot even start decomposition,
    # while any real task text or case payload starts as a partial skeleton.
    has_input = bool(case_data) or bool(task_text.strip()) or bool(inventory.get("materials"))
    if parse_errors and not has_input:
        initial_status = "input_error"
    elif has_input:
        initial_status = "partial"
    else:
        initial_status = "insufficient_evidence"

    meta = load_state(work_dir, "meta.json", {})
    plan = load_state(work_dir, "plan.json", {})
    records = load_state(work_dir, "records.json", [])
    evidence = load_state(work_dir, "evidence.json", [])

    # First run: initialize plan/records/evidence/meta without clobbering later
    # incremental work on re-run. case_summary is always re-derived from /input.
    if not plan:
        plan = {
            "research_question": research_question,
            "key_concepts": [],
            "sub_questions": [],
            "query_plan": [],
            "term_expansion": [],
            "stop_decision": {"status": "continue", "reason": "not yet assessed"},
        }
        save_state(work_dir, "plan.json", plan)
    if not meta.get("change_log"):
        meta.setdefault("status", initial_status)
        if initial_status == "insufficient_evidence":
            meta.setdefault("status_notes", "输入目录无可解析的任务说明或科研材料，无法拆解子问题；不编造文献。")
        elif initial_status == "input_error":
            meta.setdefault("status_notes", "输入存在不可解析内容（坏 JSON/坏编码），已记录并降级。")
        else:
            meta.setdefault("status_notes", "骨架已落盘，等待拆解/检索/筛选增量写入。")
        meta.setdefault("limitations", [])
        meta.setdefault("errors", list(parse_errors))
        meta.setdefault("degradations", [])
        meta.setdefault("pending_human", [])
        meta.setdefault("runtime", {"seconds": 0, "requests": 0, "started_at": now_iso()})
        append_change(meta, "run", "skeleton created from /input", initial_status)

    if not isinstance(records, list):
        records = []
    if not isinstance(evidence, list):
        evidence = []

    save_state(work_dir, "case_summary.json", case_summary)
    save_state(work_dir, "meta.json", meta)
    save_state(work_dir, "records.json", records)
    save_state(work_dir, "evidence.json", evidence)

    if contract is not None:
        pass  # contract is stored in case_summary; echoed during assemble

    result = assemble_result(input_dir, output_dir, work_dir, meta)
    print("run: case_id=%s status=%s files=%d materials=%d"
          % (case_id, result["status"], len(inventory["files"]), len(inventory["materials"])))
    return 0


# --------------------------------------------------------------------------- #
# set-plan / import / evidence: incremental updates
# --------------------------------------------------------------------------- #

def _merge_dict(dst, src):
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return src
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_dict(dst[k], v)
        else:
            dst[k] = v
    return dst


def cmd_set_plan(params):
    work_dir = params.get("work") or "/app/work"
    file_path = params.get("file")
    if not file_path:
        print("set-plan: missing --file", file=sys.stderr)
        return 2
    data, err = load_json(file_path)
    if err:
        meta = load_state(work_dir, "meta.json", {})
        append_change(meta, "set-plan", "failed: %s" % err, "error")
        save_state(work_dir, "meta.json", meta)
        print("set-plan: %s" % err, file=sys.stderr)
        return 1
    if isinstance(data, dict) and "plan" in data:
        data = data["plan"]
    if not isinstance(data, dict):
        print("set-plan: file must be a JSON object", file=sys.stderr)
        return 1
    plan = load_state(work_dir, "plan.json", {})
    _merge_dict(plan, data)
    # Ensure required list shapes.
    for key in ("sub_questions", "query_plan", "term_expansion", "key_concepts"):
        if key in plan and not isinstance(plan[key], list):
            plan[key] = []
    save_state(work_dir, "plan.json", plan)
    meta = load_state(work_dir, "meta.json", {})
    append_change(meta, "set-plan", "plan merged (%d sub_questions)" % len(plan.get("sub_questions", [])), "partial")
    save_state(work_dir, "meta.json", meta)
    print("set-plan: sub_questions=%d query_plan=%d term_expansion=%d"
          % (len(plan.get("sub_questions", [])), len(plan.get("query_plan", [])), len(plan.get("term_expansion", []))))
    return 0


def _normalize_records(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "records" in data and isinstance(data["records"], list):
            return data["records"]
        if "results" in data and isinstance(data["results"], list):
            return data["results"]
    return []


def cmd_import(params):
    work_dir = params.get("work") or "/app/work"
    file_path = params.get("file")
    if not file_path:
        print("import: missing --file", file=sys.stderr)
        return 2
    data, err = load_json(file_path)
    if err:
        meta = load_state(work_dir, "meta.json", {})
        append_change(meta, "import", "failed: %s" % err, "error")
        save_state(work_dir, "meta.json", meta)
        print("import: %s" % err, file=sys.stderr)
        return 1
    incoming = _normalize_records(data)
    records = load_state(work_dir, "records.json", [])
    if not isinstance(records, list):
        records = []
    by_id = {}
    for i, rec in enumerate(records):
        rid = rec.get("record_id") or ("R%d" % (i + 1))
        by_id[rid] = rec
    added, updated = 0, 0
    for rec in incoming:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("record_id") or rec.get("title")
        if not rid:
            continue
        if rid in by_id:
            _merge_dict(by_id[rid], rec)
            updated += 1
        else:
            rec.setdefault("record_id", rid)
            by_id[rid] = rec
            added += 1
    merged = list(by_id.values())
    save_state(work_dir, "records.json", merged)
    meta = load_state(work_dir, "meta.json", {})
    append_change(meta, "import", "records merged (added=%d updated=%d)" % (added, updated), "partial")
    save_state(work_dir, "meta.json", meta)
    print("import: total=%d added=%d updated=%d" % (len(merged), added, updated))
    return 0


def cmd_evidence(params):
    work_dir = params.get("work") or "/app/work"
    file_path = params.get("file")
    if not file_path:
        print("evidence: missing --file", file=sys.stderr)
        return 2
    data, err = load_json(file_path)
    if err:
        meta = load_state(work_dir, "meta.json", {})
        append_change(meta, "evidence", "failed: %s" % err, "error")
        save_state(work_dir, "meta.json", meta)
        print("evidence: %s" % err, file=sys.stderr)
        return 1
    entries = data if isinstance(data, list) else data.get("entries", []) if isinstance(data, dict) else []
    if not isinstance(entries, list):
        entries = []
    evidence = load_state(work_dir, "evidence.json", [])
    if not isinstance(evidence, list):
        evidence = []
    for entry in entries:
        if isinstance(entry, dict):
            entry.setdefault("timestamp", now_iso())
            evidence.append(entry)
    save_state(work_dir, "evidence.json", evidence)
    meta = load_state(work_dir, "meta.json", {})
    append_change(meta, "evidence", "%d entries appended" % len(entries), "partial")
    save_state(work_dir, "meta.json", meta)
    print("evidence: total=%d" % len(evidence))
    return 0


def cmd_set_meta(params):
    """Finalize honest status/notes/limitations/pending_human into meta.json."""
    work_dir = params.get("work") or "/app/work"
    file_path = params.get("file")
    data = {}
    if file_path:
        loaded, err = load_json(file_path)
        if err:
            print("set-meta: %s" % err, file=sys.stderr)
            return 1
        if isinstance(loaded, dict):
            data = loaded
    for key in ("status", "status_notes"):
        if params.get(key) is not None:
            data[key] = params[key]
    if not data:
        print("set-meta: nothing to set (use --file or --status)", file=sys.stderr)
        return 2
    meta = load_state(work_dir, "meta.json", {})
    for key in ("status", "status_notes", "limitations", "errors", "degradations", "pending_human"):
        if key in data:
            meta[key] = data[key]
    if meta.get("status") not in ALLOWED_STATUS:
        meta["status"] = "partial"
    append_change(meta, "set-meta", "final status=%s" % meta.get("status"), meta.get("status"))
    save_state(work_dir, "meta.json", meta)
    print("set-meta: status=%s" % meta.get("status"))
    return 0


# --------------------------------------------------------------------------- #
# assemble: merge state -> result.json + screening.md + plan.md
# --------------------------------------------------------------------------- #

def _natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def score_record(rec):
    rel = rec.get("relevance")
    rel = float(rel) if isinstance(rel, (int, float)) else 0.0
    meta_fields = ("title", "authors", "year", "journal", "doi")
    present = sum(1 for f in meta_fields if rec.get(f) not in (None, "", [], {}))
    meta = present / float(len(meta_fields))
    db = (rec.get("database") or "").lower()
    reliable_hints = ("paper", "pubmed", "doi", "crossref", "openalex", "scopus",
                      "wos", "web of science", "semantic scholar", "arxiv", "publisher", "journal")
    if any(k in db for k in reliable_hints):
        rel_db = 1.0
    elif db:
        rel_db = 0.7
    else:
        rel_db = 0.5
    total = round(0.6 * rel + 0.2 * meta + 0.2 * rel_db, 4)
    breakdown = {
        "relevance": round(rel, 4),
        "metadata": round(meta, 4),
        "source_reliability": round(rel_db, 4),
        "total": total,
    }
    return total, breakdown


def build_results(records):
    included = [r for r in records if r.get("screening") == "included"]
    scored = []
    for rec in included:
        total, breakdown = score_record(rec)
        scored.append((total, rec, breakdown))
    scored.sort(key=lambda t: (-t[0], _natural_key(t[1].get("record_id") or t[1].get("title") or "")))
    results = []
    for rank, (total, rec, breakdown) in enumerate(scored, 1):
        results.append({
            "rank": rank,
            "record_id": rec.get("record_id"),
            "title": rec.get("title"),
            "authors": rec.get("authors"),
            "year": rec.get("year"),
            "journal": rec.get("journal"),
            "doi": rec.get("doi"),
            "url": rec.get("url"),
            "database": rec.get("database"),
            "sub_question_ids": rec.get("sub_question_ids") or [],
            "relevance": rec.get("relevance"),
            "relevance_reason": rec.get("relevance_reason"),
            "screening": rec.get("screening"),
            "screening_reason": rec.get("screening_reason"),
            "evidence": rec.get("evidence") or [],
            "score": total,
            "score_breakdown": breakdown,
        })
    return results


def build_screening_ledger(records):
    ledger = {"included": [], "excluded": [], "uncertain": [], "merged_duplicates": [], "unscreened": [], "total": len(records)}
    for rec in records:
        rid = rec.get("record_id") or rec.get("title")
        decision = rec.get("screening")
        entry = {"record_id": rid, "reason": rec.get("screening_reason")}
        if decision == "included":
            ledger["included"].append(entry)
        elif decision == "excluded":
            ledger["excluded"].append(entry)
        elif decision == "uncertain":
            ledger["uncertain"].append(entry)
        elif decision == "merged_duplicate":
            entry["merged_into"] = rec.get("merged_into")
            ledger["merged_duplicates"].append(entry)
        else:
            ledger["unscreened"].append(entry)
    for key in ("included", "excluded", "uncertain", "merged_duplicates", "unscreened"):
        ledger[key].sort(key=lambda e: _natural_key(e.get("record_id") or ""))
    return ledger


def build_sources(records, case_summary):
    dbs = []
    for rec in records:
        db = rec.get("database")
        if db and db not in dbs:
            dbs.append(db)
    return {
        "databases": sorted(dbs, key=_natural_key),
        "materials": [m.get("path") for m in case_summary.get("materials", [])],
        "searches": len(dbs),
    }


def build_uncertainty(records, evidence, plan, ledger):
    missing_metadata = []
    for rec in records:
        rid = rec.get("record_id") or rec.get("title")
        missing = [f for f in ("title", "authors", "year", "journal", "doi")
                   if rec.get(f) in (None, "", [], {})]
        if missing:
            missing_metadata.append({"record_id": rid, "missing_fields": missing})
    conflicts = [e for e in evidence if e.get("kind") == "conflict"]
    conflicts += [{"record_id": r.get("record_id"), "detail": "conflicting record kept and flagged"}
                  for r in records if r.get("conflict")]
    coverage_gaps = []
    for sq in plan.get("sub_questions", []):
        sqid = sq.get("id")
        required = sq.get("required_evidence") or []
        found = [r for r in records if r.get("screening") == "included" and sqid in (r.get("sub_question_ids") or [])]
        status = sq.get("status")
        if status != "covered" and (required or status == "open" or status == "blocked"):
            coverage_gaps.append({
                "sub_question_id": sqid,
                "question": sq.get("question"),
                "required_evidence": required,
                "found_records": [r.get("record_id") for r in found],
                "note": "uncovered or partially covered sub-question",
            })
    notes = []
    if ledger["unscreened"]:
        notes.append("仍有 %d 条记录未完成筛选（screening 字段缺失）" % len(ledger["unscreened"]))
    return {"missing_metadata": missing_metadata, "conflicts": conflicts, "coverage_gaps": coverage_gaps, "notes": notes}


def assemble_result(input_dir, output_dir, work_dir, meta):
    case_summary = load_state(work_dir, "case_summary.json", {})
    plan = load_state(work_dir, "plan.json", {})
    records = load_state(work_dir, "records.json", [])
    evidence = load_state(work_dir, "evidence.json", [])

    if not isinstance(records, list):
        records = []
    if not isinstance(evidence, list):
        evidence = []

    results = build_results(records)
    ledger = build_screening_ledger(records)

    result = {
        "case_id": case_summary.get("case_id") or extract_case_id(None, input_dir),
        "status": meta.get("status") or "partial",
        "research_question": plan.get("research_question") or case_summary.get("research_question") or "",
        "sub_questions": plan.get("sub_questions", []),
        "query_plan": plan.get("query_plan", []),
        "term_expansion": plan.get("term_expansion", []),
        "stop_decision": plan.get("stop_decision", {"status": "continue", "reason": "not yet assessed"}),
        "results": results,
        "screening": ledger,
        "evidence": evidence,
        "sources": build_sources(records, case_summary),
        "uncertainty": build_uncertainty(records, evidence, plan, ledger),
        "change_log": meta.get("change_log", []),
        "status_notes": meta.get("status_notes", ""),
        "limitations": meta.get("limitations", []),
        "errors": meta.get("errors", []),
        "degradations": list(case_summary.get("degradations", [])) + list(meta.get("degradations", [])),
        "pending_human": meta.get("pending_human", []),
        "runtime": meta.get("runtime", {"seconds": 0, "requests": 0}),
    }

    # Echo every output_contract field; missing values stay null/[].
    contract = case_summary.get("output_contract") or {}
    if isinstance(contract, dict):
        for key, default in contract.items():
            if key not in result or result[key] in (None, [], {}):
                result[key] = default

    write_json(os.path.join(output_dir, "result.json"), result)
    write_screening_md(os.path.join(output_dir, "screening.md"), ledger, results, records)
    write_plan_md(os.path.join(output_dir, "plan.md"), plan, evidence)
    return result


def write_screening_md(path, ledger, results, records):
    lines = ["# 全量筛选清单（人工复核）", ""]
    lines.append("- 总记录数：%d" % ledger["total"])
    lines.append("- 纳入：%d / 排除：%d / 待定：%d / 合并重复：%d / 未筛选：%d" % (
        len(ledger["included"]), len(ledger["excluded"]), len(ledger["uncertain"]),
        len(ledger["merged_duplicates"]), len(ledger["unscreened"])))
    lines.append("")
    lines.append("## 纳入候选（按得分排序）")
    for r in results:
        lines.append("- rank %d [%s] %s (score=%.4f) — %s" % (
            r["rank"], r.get("record_id"), r.get("title"), r.get("score"), r.get("screening_reason") or ""))
        for ev in r.get("evidence") or []:
            lines.append("    - %s: %s" % (ev.get("locator"), ev.get("quote")))
    lines.append("")
    lines.append("## 排除 / 待定 / 合并")
    for group in ("excluded", "uncertain", "merged_duplicates"):
        lines.append("### %s" % group)
        for e in ledger[group]:
            extra = " -> %s" % e.get("merged_into") if e.get("merged_into") else ""
            lines.append("- [%s]%s: %s" % (e.get("record_id"), extra, e.get("reason") or ""))
    lines.append("")
    if ledger["unscreened"]:
        lines.append("## 未完成筛选（必须补齐 screening 决策）")
        for e in ledger["unscreened"]:
            lines.append("- [%s]" % e.get("record_id"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_plan_md(path, plan, evidence):
    lines = ["# 检索计划（人工复核）", ""]
    lines.append("## 研究问题")
    lines.append(str(plan.get("research_question") or "") or "(未提供)")
    lines.append("")
    lines.append("## 子问题拆解")
    for sq in plan.get("sub_questions", []):
        lines.append("- %s: %s" % (sq.get("id"), sq.get("question")))
        for k in ("object", "mechanism", "method", "time_range", "comparison", "evidence_requirement"):
            if sq.get(k):
                lines.append("    - %s: %s" % (k, sq.get(k)))
    lines.append("")
    lines.append("## 查询计划")
    for q in plan.get("query_plan", []):
        lines.append("- %s (order %s, deps %s): %s" % (q.get("id"), q.get("order"), q.get("dependencies"), q.get("query")))
    lines.append("")
    lines.append("## 术语扩展")
    for te in plan.get("term_expansion", []):
        lines.append("- %s: %s" % (te.get("concept"), te.get("variants")))
    lines.append("")
    lines.append("## 停止判断")
    sd = plan.get("stop_decision", {})
    lines.append("- status=%s: %s" % (sd.get("status"), sd.get("reason")))
    lines.append("")
    lines.append("## 证据日志（%d 条）" % len(evidence))
    for e in evidence:
        lines.append("- [%s] %s: %s" % (e.get("kind"), e.get("query") or e.get("source"), e.get("rationale")))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def cmd_assemble(params):
    input_dir = params.get("input") or "/input"
    output_dir = params.get("output") or "/app/output"
    work_dir = params.get("work") or "/app/work"
    ensure_dir(output_dir)
    meta = load_state(work_dir, "meta.json", {})
    runtime = meta.setdefault("runtime", {})
    if params.get("runtime-seconds") is not None:
        try:
            runtime["seconds"] = int(params["runtime-seconds"])
        except (TypeError, ValueError):
            pass
    if params.get("runtime-requests") is not None:
        try:
            runtime["requests"] = int(params["runtime-requests"])
        except (TypeError, ValueError):
            pass
    save_state(work_dir, "meta.json", meta)
    result = assemble_result(input_dir, output_dir, work_dir, meta)
    print("assemble: status=%s results=%d screening_total=%d" % (
        result["status"], len(result["results"]), result["screening"]["total"]))
    return 0


# --------------------------------------------------------------------------- #
# selfcheck
# --------------------------------------------------------------------------- #

def _check(name, ok, detail=""):
    print("%-6s %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    return bool(ok)


def cmd_selfcheck(params):
    output_dir = params.get("output") or "/app/output"
    work_dir = params.get("work") or "/app/work"
    result_path = os.path.join(output_dir, "result.json")
    result, err = load_json(result_path)
    all_ok = True
    all_ok &= _check("result.json 存在且可解析", result is not None, err or "")
    if result is None:
        print("SELFCHECK: FAIL (no parseable result.json)")
        return 1

    all_ok &= _check("case_id 非空", bool(result.get("case_id")))
    all_ok &= _check("status 合法", result.get("status") in ALLOWED_STATUS, str(result.get("status")))
    all_ok &= _check("change_log 非空", bool(result.get("change_log")))
    all_ok &= _check("sources 存在", isinstance(result.get("sources"), dict))
    all_ok &= _check("uncertainty 存在", isinstance(result.get("uncertainty"), dict))

    subqs = result.get("sub_questions", [])
    all_ok &= _check("sub_questions 为列表", isinstance(subqs, list))
    six = ("object", "mechanism", "method", "time_range", "comparison", "evidence_requirement")
    if subqs:
        missing_ann = [sq.get("id") for sq in subqs if not all(sq.get(k) for k in six)]
        all_ok &= _check("子问题含六要素标注", not missing_ann, "缺失: %s" % missing_ann if missing_ann else "")
    else:
        all_ok &= _check("无子问题时已降级 insufficient_evidence",
                         result.get("status") == "insufficient_evidence", str(result.get("status")))

    # All-record screening: every record must carry a decision.
    records = read_json(state_path(work_dir, "records.json")) or []
    unscreened = [r.get("record_id") for r in records if r.get("screening") not in
                  ("included", "excluded", "uncertain", "merged_duplicate")]
    all_ok &= _check("全量筛选：每条记录都有决策", not unscreened, "未筛选: %s" % unscreened if unscreened else "")

    # Excluded / uncertain must carry a reason.
    no_reason = [r.get("record_id") for r in records
                 if r.get("screening") in ("excluded", "uncertain") and not r.get("screening_reason")]
    all_ok &= _check("排除/待定均有理由", not no_reason, "缺理由: %s" % no_reason if no_reason else "")

    # Included results must carry title + evidence locator + quote.
    results = result.get("results", [])
    bad_evidence = []
    for r in results:
        evs = r.get("evidence") or []
        if not r.get("title") or not evs or not all(e.get("locator") and e.get("quote") for e in evs):
            bad_evidence.append(r.get("record_id") or r.get("title"))
    if results:
        all_ok &= _check("纳入结果均有 locator+quote 证据", not bad_evidence, "缺证据: %s" % bad_evidence if bad_evidence else "")
    else:
        all_ok &= _check("无纳入结果已声明缺口", bool(result.get("uncertainty", {}).get("coverage_gaps")) or result.get("status") in ("insufficient_evidence", "partial"))

    # Coverage: uncovered sub-questions must be surfaced.
    uncovered = [sq.get("id") for sq in subqs if sq.get("status") not in ("covered", "sufficient")]
    gaps = result.get("uncertainty", {}).get("coverage_gaps", [])
    all_ok &= _check("覆盖缺口已写入 uncertainty", (not uncovered) or bool(gaps), "未覆盖: %s" % uncovered if uncovered else "")

    # Evidence log: each entry needs source + rationale.
    bad_ev = [e.get("id") or e.get("kind") for e in result.get("evidence", []) if not (e.get("source") and e.get("rationale"))]
    all_ok &= _check("证据日志均有 source+rationale", not bad_ev, "缺字段: %s" % bad_ev if bad_ev else "")

    # Contract echo.
    case_summary = load_state(work_dir, "case_summary.json", {})
    contract = case_summary.get("output_contract") or {}
    missing_contract = [k for k in contract if k not in result]
    all_ok &= _check("output_contract 字段已回填", not missing_contract, "缺失: %s" % missing_contract if missing_contract else "")

    print("SELFCHECK: %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

COMMANDS = {
    "run": cmd_run,
    "set-plan": cmd_set_plan,
    "import": cmd_import,
    "evidence": cmd_evidence,
    "set-meta": cmd_set_meta,
    "assemble": cmd_assemble,
    "selfcheck": cmd_selfcheck,
}


def parse_argv(argv):
    cmd = None
    params = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            key = arg[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                params[k] = v
            else:
                if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                    params[key] = argv[i + 1]
                    i += 1
                else:
                    params[key] = True
        elif cmd is None:
            cmd = arg
        else:
            params.setdefault("_extra", []).append(arg)
        i += 1
    return cmd, params


def print_help():
    print("usage: main.py <command> [options]")
    print("commands:")
    print("  run       --input DIR --output DIR --work DIR    scan input, write skeleton")
    print("  set-plan  --file plan_in.json --work DIR         merge plan (sub/query/terms/stop)")
    print("  import    --file records_in.json --work DIR      merge candidate records")
    print("  evidence  --file evidence_in.json --work DIR     append evidence entries")
    print("  set-meta  --file meta_in.json --work DIR         finalize status/notes/limitations")
    print("  assemble  --input DIR --output DIR --work DIR    write result.json/md")
    print("  selfcheck --output DIR --work DIR                validate result.json")
    print("defaults: --input /input --output /app/output --work /app/work")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("--help", "-h", "help"):
        print_help()
        return 0
    cmd, params = parse_argv(argv)
    if cmd not in COMMANDS:
        print("unknown command: %s" % cmd, file=sys.stderr)
        print_help()
        return 2
    try:
        return COMMANDS[cmd](params)
    except Exception as exc:  # noqa: BLE001 - never crash the harness
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
