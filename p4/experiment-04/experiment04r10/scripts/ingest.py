"""ingest.py — input discovery, contract extraction, evidence indexing, skeleton.

Standard library only. Deterministic: walks directories in sorted order and
builds locators from stable relative paths and 1-based line numbers.
"""

import datetime
import json
import os
import re

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TEXT_SPANS = 800

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
ALLOWED_STATUS = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}

SEVEN_FIELDS = (
    "assumptions", "variables", "controls", "steps",
    "parameters", "resources", "interpretation_criteria",
)
FIVE_DIMS = ("goal_fit", "risk", "cost", "feasibility", "evidence")
DEFAULT_WEIGHTS = {
    "goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20,
    "cost": 0.15, "risk": 0.15,
}
DEFAULT_CONTRACT_FIELDS = ("experimental_plan", "evidence_and_risks", "confirmation_items")

CANDIDATE_KEYS = ("candidates", "plans", "candidate_plans", "候选方案", "方案列表", "plans_candidates")
CONSTRAINT_KEYS = ("constraints", "hard_constraints", "budget", "time_window", "deadline",
                   "objective", "goal", "研究目标", "约束")
CASE_ID_KEYS = ("case_id", "challenge_id", "caseId", "id")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file tolerantly. Returns (text_or_None, note)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_error:%s" % exc
    if size > max_bytes:
        return None, "oversize:%d>%d" % (size, max_bytes)
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), enc
        except (UnicodeDecodeError, LookupError) as exc:
            last_err = "%s:%s" % (enc, exc)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(), "utf-8-replace"
    except OSError as exc:
        return None, "read_error:%s" % (exc if last_err is None else "%s;%s" % (last_err, exc))


def safe_json(text):
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - must never raise
        return None, str(exc)


def classify(rel):
    low = rel.lower()
    if low.endswith(".json"):
        return "json"
    if low.endswith((".md", ".txt", ".markdown")):
        return "text"
    if low.endswith(".csv"):
        return "csv"
    if low.endswith((".yaml", ".yml")):
        return "yaml"
    if low.endswith(".pdf"):
        return "pdf"
    return "other"


def walk_files(root):
    files = []
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            files.append({"path": p, "rel": rel, "size": size, "kind": classify(rel)})
    files.sort(key=lambda f: (f["rel"].count("/"), f["rel"]))
    return files


def find_key(obj, key, path="$", hits=None):
    """Recursively find all dict entries whose key equals `key` (case-insensitive)."""
    if hits is None:
        hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (path, k)
            if isinstance(k, str) and k.lower() == key.lower():
                hits.append({"path": p, "value": v})
            find_key(v, key, p, hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_key(v, key, "%s[%d]" % (path, i), hits)
    return hits


def find_any_keys(obj, keys, path="$", hits=None):
    if hits is None:
        hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (path, k)
            if isinstance(k, str) and k.lower() in keys:
                hits.append({"path": p, "key": k, "value": v})
            find_any_keys(v, keys, p, hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_any_keys(v, keys, "%s[%d]" % (path, i), hits)
    return hits


def _extract_case_id(parsed, task_text):
    if isinstance(parsed, dict):
        for key in CASE_ID_KEYS:
            if key in parsed and isinstance(parsed[key], (str, int)):
                return str(parsed[key])
        for hit in find_any_keys(parsed, tuple(CASE_ID_KEYS)):
            if isinstance(hit["value"], (str, int)):
                return str(hit["value"])
    if task_text:
        m = re.search(r"challenge[-_ ]id[:：]?\s*`?([A-Za-z0-9._-]+)`?", task_text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"challenge-([A-Za-z0-9._-]+)", task_text)
        if m:
            return "challenge-" + m.group(1)
    return "unknown"


def _extract_contract(parsed):
    hits = find_key(parsed, "output_contract")
    if not hits:
        return {"found": False, "fields": list(DEFAULT_CONTRACT_FIELDS), "raw": None, "path": None}
    raw = hits[0]["value"]
    fields = []
    if isinstance(raw, dict):
        fields = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                fields.append(item)
            elif isinstance(item, dict):
                fields.extend(str(k) for k in item.keys())
    if not fields:
        fields = list(DEFAULT_CONTRACT_FIELDS)
    return {"found": True, "fields": fields, "raw": raw, "path": hits[0]["path"]}


def _looks_like_candidate(obj):
    if not isinstance(obj, dict):
        return False
    keys = {str(k).lower() for k in obj.keys()}
    markers = {"plan_id", "planid", "name", "title", "方案名", "方案编号", "candidate", "方案"}
    if keys & markers:
        return True
    if len(keys) >= 2 and ("seven_fields" in keys or "assumptions" in keys or "steps" in keys):
        return True
    return False


def _discover_candidates(parsed, root):
    """Return list of candidate objects with stable locators."""
    out = []
    for hit in find_any_keys(parsed, tuple(CANDIDATE_KEYS)):
        value = hit["value"]
        items = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
        for idx, item in enumerate(items):
            if _looks_like_candidate(item):
                out.append({"path": "%s[%d]" % (hit["path"], idx), "raw": item})
    if not out:
        for hit in find_any_keys(parsed, tuple(CANDIDATE_KEYS)):
            value = hit["value"]
            items = value if isinstance(value, list) else []
            for idx, item in enumerate(items):
                if isinstance(item, dict):
                    out.append({"path": "%s[%d]" % (hit["path"], idx), "raw": item})
    # de-duplicate by (path, repr-ish) while keeping order
    seen = set()
    unique = []
    for cand in out:
        key = cand["path"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(cand)
    return unique


def _constraint_hints(parsed):
    hints = []
    for hit in find_any_keys(parsed, tuple(CONSTRAINT_KEYS)):
        val = hit["value"]
        preview = _preview(val)
        hints.append({"path": hit["path"], "key": hit["key"], "value": preview})
    return hints[:40]


def _preview(obj, limit=400):
    if isinstance(obj, str):
        return obj[:limit]
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
        return s[:limit]
    except Exception:  # noqa: BLE001
        return repr(obj)[:limit]


def _text_spans(file_records, input_root):
    spans = []
    for rec in file_records:
        if rec["kind"] not in ("text", "csv", "yaml"):
            continue
        text, note = read_text(rec["path"])
        if text is None:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            line = line.rstrip()
            if not line.strip():
                continue
            if len(spans) >= MAX_TEXT_SPANS:
                break
            spans.append({"locator": "%s#L%d" % (rec["rel"], i), "text": line[:200]})
        if len(spans) >= MAX_TEXT_SPANS:
            break
    return spans


def build_case_pack(input_root):
    errors, degradations = [], []
    files = walk_files(input_root)
    case_json_path = None
    case_json_parsed = None
    case_json_error = None
    task_text = None
    material_manifest = []
    file_records = []

    for rec in files:
        file_records.append(rec)
        if rec["kind"] == "json":
            if rec["rel"] in ("case.json", "input.json") or case_json_path is None:
                text, note = read_text(rec["path"])
                if text is not None:
                    parsed, jerr = safe_json(text)
                    if parsed is not None:
                        case_json_path, case_json_parsed, case_json_error = rec["path"], parsed, None
                    else:
                        errors.append("bad_json:%s:%s" % (rec["rel"], jerr))
                        case_json_error = jerr
                        case_json_parsed = None
                else:
                    degradations.append("unreadable:%s:%s" % (rec["rel"], note))
        elif rec["rel"] in ("task.md", "task.txt") or (rec["kind"] == "text" and task_text is None):
            text, note = read_text(rec["path"])
            if text is not None:
                task_text = text

    parsed = case_json_parsed
    if parsed is None and task_text is not None:
        # task.md is markdown; only treat as JSON if it parses cleanly.
        p, err = safe_json(task_text)
        if p is not None:
            parsed = p

    case_id = _extract_case_id(parsed, task_text)
    contract = _extract_contract(parsed) if parsed is not None else {
        "found": False, "fields": list(DEFAULT_CONTRACT_FIELDS), "raw": None, "path": None}
    candidates = _discover_candidates(parsed, input_root) if parsed is not None else []
    hints = _constraint_hints(parsed) if parsed is not None else []

    for rec in file_records:
        material_manifest.append({
            "rel": rec["rel"], "kind": rec["kind"], "size": rec["size"],
            "locator_base": rec["rel"],
        })

    if case_json_path is None and parsed is None and not file_records:
        input_status = "empty"
    elif case_json_error:
        input_status = "error"
    else:
        input_status = "ok"

    spans = _text_spans(file_records, input_root)

    return {
        "case_id": case_id,
        "input_root": input_root,
        "generated_at": utc_now(),
        "input_status": input_status,
        "files": [{"rel": r["rel"], "size": r["size"], "kind": r["kind"]} for r in file_records],
        "material_manifest": material_manifest,
        "case_json": {"path": case_json_path, "error": case_json_error},
        "task_md_found": task_text is not None,
        "output_contract": contract,
        "candidate_count": len(candidates),
        "candidate_sources": candidates,
        "constraint_hints": hints,
        "text_spans": spans,
        "errors": errors,
        "degradations": degradations,
    }


def _pick_status(pack):
    if pack["input_status"] == "empty":
        return STATUS_INPUT_ERROR
    if pack["case_json"]["error"]:
        return STATUS_INPUT_ERROR
    if pack["candidate_count"] == 0:
        return STATUS_INSUFFICIENT
    return STATUS_PARTIAL


def _default_contract_value(field):
    low = field.lower()
    if low == "experimental_plan":
        return {"testable_question": None, "variables": None, "candidates": []}
    if low == "evidence_and_risks":
        return {"evidence": [], "risks": [], "exceptions": []}
    if low == "confirmation_items":
        return []
    return None


def build_skeleton(pack):
    fields = pack["output_contract"]["fields"]
    result = {
        "case_id": pack["case_id"],
        "status": _pick_status(pack),
        "research_question": None,
        "comparison_frame": None,
        "candidates": [],
        "comparison_matrix": None,
        "selection": None,
        "confirmations": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {"time": utc_now(), "step": "inspect", "note": "skeleton written by inspect"}
        ],
        "pending_human": [],
        "errors": list(pack["errors"]),
        "degradations": list(pack["degradations"]),
        "runtime": {"started_at": utc_now(), "mode": "skeleton"},
        "artifacts": [],
        "limitations": [],
    }
    for f in fields:
        result.setdefault(f, _default_contract_value(f))
    if pack["candidate_count"] == 0:
        result["limitations"].append("no candidate plans discovered in case.json")
    return result


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def read_json(path):
    text, note = read_text(path)
    if text is None:
        return None, "unreadable:%s" % note
    return safe_json(text)
