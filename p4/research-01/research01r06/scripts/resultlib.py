#!/usr/bin/env python3
"""Shared helpers for the deep-research skill runtime.

Standard library only. All functions are deterministic: the same input files
produce the same JSON/markdown layout, so a recorded run can be re-run and
diffed by a reviewer.
"""

import copy
import datetime as _dt
import json
import os
import time as _time

# ---------------------------------------------------------------------------
# Status vocabulary (kept as literal strings so result.json is self-describing)
# ---------------------------------------------------------------------------
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUS = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

MAX_TEXT_BYTES = 2 * 1024 * 1024
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

MATERIAL_EXTS = {
    ".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".tex", ".bib", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
    ".svg", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
}

CONTRACT_ALIASES = ("output_contract", "output_schema", "result_schema",
                    "outputContract", "resultSchema")
CASE_ID_KEYS = ("case_id", "caseId", "id", "armiId")

# Default output contract. If case.json supplies output_contract /
# output_schema / result_schema, its fields are added on top and must be
# back-filled verbatim; the fields below are always present so the harness and
# reviewers can rely on a stable envelope.
DEFAULT_CONTRACT = {
    "case_id": "",
    "status": STATUS_PARTIAL,
    "research_product": {
        "type": "",
        "question": "",
        "summary": "",
        "conclusions": [],
    },
    "evidence_matrix": [],
    "uncertainty": {
        "conflicts": [],
        "biases": [],
        "gaps": [],
        "undetermined": [],
    },
    "sources": [],
    "change_log": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "pending_human": [],
    "runtime_seconds": 0,
}


# ---------------------------------------------------------------------------
# Time / paths / low-level I/O
# ---------------------------------------------------------------------------
def epoch_now():
    return int(_time.time())


def iso_now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def read_text(path):
    """Read text with encoding fallback. Returns (text, errors, degradations)."""
    errors, degradations = [], []
    if not path or not os.path.exists(path):
        return "", ["missing file: %s" % (path,)], degradations
    size = -1
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        errors.append("stat failed %s: %s" % (path, exc))
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_TEXT_BYTES + 1)
    except OSError as exc:
        return "", errors + ["read failed %s: %s" % (path, exc)], degradations
    if size > MAX_TEXT_BYTES or len(raw) > MAX_TEXT_BYTES:
        raw = raw[:MAX_TEXT_BYTES]
        degradations.append("truncated (>2MB): %s" % path)
    for enc in TEXT_ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if enc not in ("utf-8-sig", "utf-8"):
            degradations.append("decoded as %s: %s" % (enc, path))
        return text, errors, degradations
    return raw.decode("latin-1", errors="replace"), errors, degradations


def load_json(path):
    """Load a JSON file. Returns (obj, errors, degradations); obj is None on failure."""
    if not path:
        return None, ["load_json called with empty path"], []
    text, errs, degs = read_text(path)
    if errs:
        return None, errs, degs
    try:
        obj = json.loads(text)
    except ValueError as exc:
        return None, ["bad JSON %s: %s" % (path, exc)], degs
    return obj, [], degs


def write_json(path, obj):
    """Write JSON deterministically. Returns a list of error strings (empty on success)."""
    try:
        ensure_dir(os.path.dirname(path) or ".")
    except OSError as exc:
        return ["mkdir failed for %s: %s" % (path, exc)]
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except (OSError, TypeError, ValueError) as exc:
        return ["write failed %s: %s" % (path, exc)]
    return []


def write_text(path, text):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        return ["write failed %s: %s" % (path, exc)]
    return []


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------
def _safe_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def discover_input(input_dir):
    inv = {
        "exists": False,
        "input_dir": input_dir,
        "total_files": 0,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "other": [],
        "errors": [],
        "degradations": [],
    }
    if not input_dir or not os.path.isdir(input_dir):
        inv["errors"].append("input directory not found: %s" % (input_dir,))
        return inv
    inv["exists"] = True
    for root, _dirs, files in os.walk(input_dir):
        for name in files:
            full = os.path.join(root, name)
            low = name.lower()
            inv["total_files"] += 1
            if low == "case.json" and not inv["case_json"]:
                inv["case_json"] = full
            elif low == "task.md" and not inv["task_md"]:
                inv["task_md"] = full
            ext = os.path.splitext(name)[1].lower()
            rec = {"path": full, "name": name, "ext": ext, "size": _safe_size(full)}
            if ext in MATERIAL_EXTS or low in ("case.json", "task.md"):
                inv["materials"].append(rec)
            else:
                inv["other"].append(rec)
    inv["materials"].sort(key=lambda r: r["path"])
    inv["other"].sort(key=lambda r: r["path"])
    return inv


# ---------------------------------------------------------------------------
# Contract handling
# ---------------------------------------------------------------------------
def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if val is not None and not isinstance(val, (dict, list)):
            return str(val)
    return ""


def value_default(name, template):
    """Default value for an output_contract field we do not otherwise fill."""
    if isinstance(template, dict):
        return copy.deepcopy(template) if template else {}
    if isinstance(template, list):
        return copy.deepcopy(template) if template else []
    if isinstance(template, bool):
        return False
    if isinstance(template, (int, float)):
        return None
    if isinstance(template, str):
        return "" if name != "status" else STATUS_PARTIAL
    return None


def build_contract(case_obj):
    """Union of DEFAULT_CONTRACT and any output_contract declared in case.json."""
    contract = copy.deepcopy(DEFAULT_CONTRACT)
    if not isinstance(case_obj, dict):
        return contract
    oc = None
    for key in CONTRACT_ALIASES:
        val = case_obj.get(key)
        if isinstance(val, dict) and val:
            oc = val
            break
    if oc is None:
        return contract
    for key, val in oc.items():
        if key not in contract:
            contract[key] = value_default(key, val)
    return contract


def normalize_status(status):
    if status in VALID_STATUS:
        return status
    if status in ("success", "done", "complete"):
        return STATUS_OK
    if status in ("fail", "failed", "error"):
        return STATUS_INPUT_ERROR
    return STATUS_PARTIAL


def new_skeleton(case_id, contract, note=""):
    result = copy.deepcopy(contract) if isinstance(contract, dict) else copy.deepcopy(DEFAULT_CONTRACT)
    result["case_id"] = case_id or ""
    result["status"] = normalize_status(result.get("status", STATUS_PARTIAL))
    result.setdefault("change_log", [])
    if note:
        result["change_log"].append({"ts": iso_now(), "action": "skeleton", "detail": note})
    return result


def deep_merge(base, patch):
    """Recursive merge; patch values (including lists) replace base values."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(base)
    for key, val in patch.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


# ---------------------------------------------------------------------------
# Validation and six-point coverage
# ---------------------------------------------------------------------------
def validate_result(result):
    errors = []
    if not isinstance(result, dict):
        return False, ["result.json is not a JSON object"]
    if not result.get("case_id"):
        errors.append("missing case_id")
    if result.get("status") not in VALID_STATUS:
        errors.append("invalid status %r" % (result.get("status"),))
    rp = result.get("research_product")
    if not isinstance(rp, dict):
        errors.append("research_product missing or not an object")
    else:
        concls = rp.get("conclusions")
        if not isinstance(concls, list) or not concls:
            errors.append("research_product.conclusions empty")
    matrix = result.get("evidence_matrix")
    ids = set()
    if not isinstance(matrix, list):
        errors.append("evidence_matrix missing or not a list")
    else:
        for i, row in enumerate(matrix):
            rid = row.get("id") if isinstance(row, dict) else None
            if not rid:
                errors.append("evidence_matrix[%d] missing id" % i)
            elif rid in ids:
                errors.append("duplicate evidence id %r" % (rid,))
            if rid:
                ids.add(rid)
            if isinstance(row, dict):
                if not row.get("locator"):
                    errors.append("evidence %s missing locator" % (rid,))
                if not row.get("quote"):
                    errors.append("evidence %s missing quote" % (rid,))
    if isinstance(rp, dict):
        for concl in (rp.get("conclusions") or []):
            if not isinstance(concl, dict):
                continue
            for eid in (concl.get("evidence_ids") or []):
                if eid not in ids:
                    errors.append(
                        "conclusion %s references missing evidence %r" % (concl.get("id"), eid))
    for field in ("sources", "change_log", "limitations", "errors",
                  "degradations", "pending_human"):
        if not isinstance(result.get(field), list):
            errors.append("field %r must be a list" % (field,))
    if not isinstance(result.get("uncertainty"), dict):
        errors.append("uncertainty missing or not an object")
    return (not errors), errors


def six_point_coverage(result, work_dir):
    """Map the six rubric items onto machine-checkable artifacts."""
    checks = []
    plan = None
    if work_dir:
        plan_obj, _e, _d = load_json(os.path.join(work_dir, "plan.json"))
        if isinstance(plan_obj, dict):
            plan = plan_obj

    subqs = (plan or {}).get("subquestions")
    ok1 = isinstance(subqs, list) and len(subqs) >= 2 and all(
        isinstance(s, dict) and s.get("id") and s.get("text") for s in subqs)
    checks.append({"id": "decomposition", "ok": ok1,
                   "detail": "subquestions=%d (need >=2 with id+text)"
                             % (len(subqs) if isinstance(subqs, list) else 0)})

    order = (plan or {}).get("search_order")
    stop = (plan or {}).get("stop_conditions")
    ok2 = isinstance(order, list) and bool(order) and isinstance(stop, dict) and bool(stop)
    checks.append({"id": "search_plan", "ok": ok2,
                   "detail": "search_order=%s stop_conditions=%s" % (bool(order), bool(stop))})

    matrix = result.get("evidence_matrix") or []
    ok3 = bool(matrix) and all(
        isinstance(r, dict) and r.get("locator") and r.get("quote") for r in matrix)
    concls = ((result.get("research_product") or {}).get("conclusions") or [])
    ids = {r.get("id") for r in matrix if isinstance(r, dict)}
    ok3 = ok3 and bool(concls) and all(
        isinstance(c, dict) and c.get("evidence_ids") and
        all(e in ids for e in c["evidence_ids"]) for c in concls)
    checks.append({"id": "evidence_linkage", "ok": ok3,
                   "detail": "evidence_rows=%d conclusions=%d"
                             % (len(matrix), len(concls))})

    conflicts = ((result.get("uncertainty") or {}).get("conflicts") or [])
    ok4 = all(
        isinstance(c, dict) and
        (len([s for s in (c.get("sources") or []) if s]) >= 2 or
         (c.get("source_a") and c.get("source_b"))) and
        c.get("resolution") for c in conflicts)
    checks.append({"id": "conflicts", "ok": ok4 if conflicts else None,
                   "detail": "conflicts=%d" % len(conflicts)})

    ok5 = bool(concls) and ((result.get("research_product") or {}).get("summary")
                            or "").strip() != ""
    checks.append({"id": "conclusions", "ok": ok5,
                   "detail": "conclusions=%d summary=%s" % (len(concls), ok5)})

    gaps = ((result.get("uncertainty") or {}).get("gaps") or [])
    pending = result.get("pending_human") or []
    ok6 = bool(gaps) or bool(pending)
    checks.append({"id": "gaps", "ok": ok6,
                   "detail": "gaps=%d pending_human=%d" % (len(gaps), len(pending))})

    passed = sum(1 for c in checks if c["ok"] is True)
    total = sum(1 for c in checks if c["ok"] is not None)
    return {"checks": checks, "passed": passed, "total": total,
            "all_ok": passed == total and total > 0}


# ---------------------------------------------------------------------------
# Markdown generation from structured intermediates
# ---------------------------------------------------------------------------
def md_table(headers, rows):
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def plan_to_markdown(plan):
    """Derive decomposition.md + search_plan.md text from plan.json (single source of truth)."""
    if not isinstance(plan, dict):
        return "", ""
    subqs = plan.get("subquestions") or []
    decom = ["# 问题拆解", "",
             "研究问题：%s" % (plan.get("research_question") or "（未填）"), "",
             "任务类型：%s" % (plan.get("task_type") or "（未填）"), ""]
    for s in subqs:
        if not isinstance(s, dict):
            continue
        deps = ", ".join(s.get("depends_on") or []) or "无"
        facets = s.get("facets") or {}
        fstr = "；".join("%s=%s" % (k, v if isinstance(v, str) else ",".join(v or []))
                         for k, v in facets.items() if v) or "未标注"
        decom.append("## %s" % s.get("id", "?"))
        decom.append("- 问题：%s" % s.get("text", ""))
        decom.append("- 要素：%s" % fstr)
        decom.append("- 依赖：%s" % deps)
        decom.append("")
    planmd = ["# 检索计划", "",
              "检索顺序：%s" % " → ".join(plan.get("search_order") or []), ""]
    for group in (plan.get("parallel_groups") or []):
        planmd.append("- 可并行：%s" % "、".join(group))
    stop = plan.get("stop_conditions") or {}
    for key, val in stop.items():
        planmd.append("- 停止条件（%s）：%s" % (key, val))
    planmd.append("")
    for s in subqs:
        if not isinstance(s, dict):
            continue
        q = (s.get("search") or {})
        planmd.append("## %s" % s.get("id", "?"))
        planmd.append("- 检索式：`%s`" % q.get("query", ""))
        planmd.append("- 来源范围：%s" % "、".join(q.get("sources") or []))
        planmd.append("- 停止条件：%s" % q.get("stop_condition", ""))
        planmd.append("")
    return "\n".join(decom), "\n".join(planmd)
