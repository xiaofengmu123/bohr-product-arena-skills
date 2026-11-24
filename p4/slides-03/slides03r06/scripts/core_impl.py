#!/usr/bin/env python3
"""Core implementation for the academic-talk-organizer Skill.

This module is the single source of truth for the deterministic, reviewable
parts of the pipeline. It only uses the Python standard library and treats all
paths as ordinary arguments so it runs unchanged on the evaluator (Linux,
``/input`` + ``/app``) and locally (Windows, demo directories).

Responsibilities
-----------------
* tolerant JSON / text reading (BOM, bad encoding, bad JSON, size cap)
* material discovery in the input directory (PDF / image / office / text)
* the default output contract (fields preserved from ``case.json`` when given)
* scaffold generation: an early, valid ``result.json`` partial skeleton plus the
  two structured intermediate representations ``plan.json`` and ``figure_model.json``
* merge of the intermediate representations back into ``result.json``
* validation of the contract, evidence provenance (locator + verbatim quote),
  and artifact existence, with a non-strict default and a ``strict`` final gate
* change-log and uncertainty helpers

Intermediate-representation design
----------------------------------
The final deck must be re-derivable from two small, human-readable JSON files:

* ``plan.json``   - narrative plan: report goal, audience, page sequence, one
  ``single_claim`` per page, evidence strength, figure bindings, speaker notes,
  source references. This is the *organisation* layer.
* ``figure_model.json`` - fact model of every figure / table / equation actually
  used: kind, locator, verbatim quote, extracted numbers, availability status,
  and the pages that use it. This is the *provenance* layer.

``result.json`` then becomes a merge of these layers plus the runtime fields
(``status`` / ``sources`` / ``uncertainty`` / ``pending_human`` / ``change_log``).
Every claim can be traced: page -> plan entry -> figure/evidence locator + quote.
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_STATUS = {
    "complete",
    "partial",
    "failed",
    "insufficient_evidence",
    "input_error",
}

EVIDENCE_STRENGTHS = {"high", "medium", "low"}

# Deliverable fields required by the topic (可编辑演示文件 / 页级证据映射 /
# 渲染与版本记录). These are used only when case.json does not provide its own
# output_contract; any contract found in the input is preserved verbatim.
DEFAULT_CONTRACT_KEYS = [
    "report_goal",
    "audience",
    "narrative",
    "pages",
    "editable_deck",
    "evidence_map",
    "qa_prep",
    "rendering",
]

META_KEYS = [
    "case_id",
    "status",
    "sources",
    "uncertainty",
    "pending_human",
    "errors",
    "degradations",
    "change_log",
    "runtime",
]

MAX_SOURCE_BYTES = 2 * 1024 * 1024

_EXT_KIND = {
    ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".svg": "image",
    ".gif": "image", ".webp": "image", ".tif": "image", ".tiff": "image",
    ".pptx": "deck", ".odp": "deck", ".key": "deck", ".potx": "template",
    ".docx": "document", ".doc": "document",
    ".md": "text", ".txt": "text", ".tex": "text", ".rst": "text",
    ".json": "json", ".yaml": "text", ".yml": "text", ".csv": "table",
    ".xlsx": "table", ".tsv": "table",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso():
    """ISO-8601 UTC timestamp for change-log entries."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    """Return ``(value, error)``; ``error`` is ``None`` on success.

    Tolerates BOM, UTF-8/GBK decoding, and returns a descriptive error string
    instead of raising, so a bad file never crashes the pipeline.
    """
    if not path or not os.path.exists(path):
        return None, "missing file: %s" % (path,)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return None, "read error: %s" % (exc,)
    if len(raw) > MAX_SOURCE_BYTES:
        return None, "oversize (%d bytes)" % len(raw)
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            return json.loads(text), None
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError as exc:
            last_err = "bad json (%s)" % (exc,)
    return None, last_err or "undecodable text"


def read_text(path):
    """Return ``(text, error)`` for a plain-text file."""
    if not path or not os.path.exists(path):
        return "", "missing file: %s" % (path,)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return "", "read error: %s" % (exc,)
    if len(raw) > MAX_SOURCE_BYTES:
        return raw.decode("utf-8-sig", errors="replace"), "oversize (%d bytes)" % len(raw)
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "undecodable text (lossy fallback)"


def write_json(path, value):
    """Write JSON with UTF-8 (no BOM), trailing newline, deterministic keys."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def classify_ext(name):
    ext = os.path.splitext(name)[1].lower()
    return _EXT_KIND.get(ext)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_materials(input_dir):
    """Return a list of material records under ``input_dir``.

    A record is ``{"path", "kind", "size"}``. Discovery is informational; it
    never opens or mutates the files.
    """
    items = []
    if not input_dir or not os.path.isdir(input_dir):
        return items
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "__MACOSX")]
        for name in files:
            if name.startswith("."):
                continue
            full = os.path.join(root, name)
            kind = classify_ext(name)
            if kind is None:
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            items.append({"path": full, "kind": kind, "size": size})
    items.sort(key=lambda r: (r["kind"], r["path"]))
    return items


# ---------------------------------------------------------------------------
# Contract and identifiers
# ---------------------------------------------------------------------------

def resolve_case_id(case):
    if not isinstance(case, dict):
        return None
    for key in ("id", "case_id", "challenge_id", "armiId"):
        value = case.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def build_contract(case):
    """Return a dict of output-contract keys (values preset to ``None``).

    If ``case.json`` provides ``output_contract`` as a dict or a list of field
    names, every provided key is preserved; unknown keys are never dropped or
    renamed. Otherwise the topic's default deliverable fields are used.
    """
    oc = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(oc, dict) and oc:
        return {str(k): None for k in oc.keys()}
    if isinstance(oc, list) and oc:
        return {str(k): None for k in oc}
    return {k: None for k in DEFAULT_CONTRACT_KEYS}


def default_plan(case_id):
    return {
        "case_id": case_id,
        "report_goal": None,
        "audience": None,
        "narrative": {"arc": [], "tradeoffs": []},
        "pages": [],
        "qa_prep": [],
    }


def default_figure_model(case_id):
    return {
        "case_id": case_id,
        "figures": [],
        "unresolved": [],
    }


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------

def append_change_log(obj, action, scope):
    log = obj.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        obj["change_log"] = log
    log.append({"ts": now_iso(), "action": action, "scope": scope})
    return log


def build_scaffold(case_path, task_path, work_dir, output_dir):
    """Write the early partial ``result.json`` plus the two intermediate files.

    Always returns ``(result_dict, ok=True)``; any input problem is recorded
    inside the result instead of raising, so the first three minutes never
    leave the output directory empty.

    ``status`` is set honestly:
      * ``partial``              - case.json read, contract scaffolded
      * ``insufficient_evidence``- case.json missing (nothing to organise)
      * ``input_error``          - case.json present but unreadable
    """
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case, case_err = read_json(case_path)
    task_text, task_err = read_text(task_path)

    case_id = resolve_case_id(case)
    if case_id is None:
        case_id = "unknown"
    contract = build_contract(case if isinstance(case, dict) else {})

    if case is None and case_err and "missing" in case_err:
        status = "insufficient_evidence"
    elif case is None:
        status = "input_error"
    else:
        status = "partial"

    materials = discover_materials(os.path.dirname(os.path.abspath(case_path)) if case_path else None)
    sources = [
        {"path": m["path"], "kind": m["kind"], "read": False, "used": False}
        for m in materials
    ]

    errors = []
    if case_err:
        errors.append("case.json: %s" % case_err)
    if task_err:
        errors.append("task.md: %s" % task_err)

    result = {"case_id": case_id, "status": status}
    for key in contract:
        result[key] = None
    result["contract_fields"] = sorted(contract.keys())
    result["pages"] = []
    result["evidence_map"] = []
    result["sources"] = sources
    result["uncertainty"] = []
    result["pending_human"] = []
    result["errors"] = errors
    result["degradations"] = []
    result["change_log"] = []
    result["runtime"] = {
        "started_at": now_iso(),
        "budget_seconds": 900,
        "budget_requests": 64,
        "tokens_per_response": 16384,
    }

    uncertainty = result["uncertainty"]
    if status == "insufficient_evidence":
        uncertainty.append("输入目录缺少 case.json，无法确定案例与契约，未生成页面内容。")
    if status == "input_error":
        uncertainty.append("case.json 不可解析，仅保留骨架与错误记录，未推断任何字段值。")
    if not materials:
        uncertainty.append("未发现可识别的科研材料文件（PDF/图片/Office/文本）。")

    append_change_log(result, "scaffold", "result.json / plan.json / figure_model.json")

    result_path = os.path.join(output_dir, "result.json")
    plan_path = os.path.join(work_dir, "plan.json")
    figure_path = os.path.join(work_dir, "figure_model.json")

    write_json(result_path, result)
    write_json(plan_path, default_plan(case_id))
    write_json(figure_path, default_figure_model(case_id))

    return result, True


# ---------------------------------------------------------------------------
# Merge of intermediate representations
# ---------------------------------------------------------------------------

def _norm_refs(refs):
    out = []
    if not isinstance(refs, list):
        return out
    for r in refs:
        if isinstance(r, dict):
            out.append(r)
    return out


def build_evidence_map(plan, figure_model):
    """Derive a verifiable evidence map from plan pages and the figure model.

    An entry is emitted only when a locator and a non-empty verbatim quote are
    both present, so the map never contains invented provenance.
    """
    seen = set()
    entries = []
    pages = plan.get("pages") if isinstance(plan, dict) else []
    for page in pages if isinstance(pages, list) else []:
        if not isinstance(page, dict):
            continue
        pidx = page.get("page_index")
        for ref in _norm_refs(page.get("source_refs")):
            loc = (ref.get("locator") or "").strip()
            quote = (ref.get("quote") or "").strip()
            if not loc or not quote:
                continue
            key = (pidx, loc)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "page_index": pidx,
                "claim": page.get("single_claim"),
                "strength": page.get("evidence_strength"),
                "kind": ref.get("kind", "text"),
                "locator": loc,
                "quote": quote,
            })
    figures = figure_model.get("figures") if isinstance(figure_model, dict) else []
    for fig in figures if isinstance(figures, list) else []:
        if not isinstance(fig, dict):
            continue
        loc = (fig.get("locator") or "").strip()
        quote = (fig.get("quote") or "").strip()
        if not loc or not quote:
            continue
        used = fig.get("pages_used") or []
        if not isinstance(used, list):
            used = []
        for pidx in used:
            key = (pidx, loc)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "page_index": pidx,
                "claim": None,
                "strength": None,
                "kind": fig.get("kind", "figure"),
                "locator": loc,
                "quote": quote,
            })
    entries.sort(key=lambda e: (e["page_index"] if e["page_index"] is not None else -1,
                                e["locator"]))
    return entries


def merge(result_path, plan_path, figure_path):
    """Merge ``plan.json`` + ``figure_model.json`` into ``result.json``.

    Returns ``(errors, warnings, result)``; writes the merged result.
    """
    errors, warnings = [], []
    result, rerr = read_json(result_path)
    plan, perr = read_json(plan_path)
    figures, ferr = read_json(figure_path)
    for msg in (rerr, perr, ferr):
        if msg:
            errors.append(msg)
    if result is None:
        result = {"case_id": "unknown", "status": "input_error"}
    if not isinstance(result, dict):
        errors.append("result.json root is not an object")
        return errors, warnings, result
    plan = plan if isinstance(plan, dict) else {}
    figures = figures if isinstance(figures, dict) else {}

    for key in ("report_goal", "audience", "narrative", "pages", "qa_prep"):
        if key in plan and plan.get(key) is not None:
            result[key] = plan[key]

    result["evidence_map"] = build_evidence_map(plan, figures)

    rendering = result.get("rendering")
    if not isinstance(rendering, dict):
        rendering = {}
        result["rendering"] = rendering
    rendering["figure_model_summary"] = {
        "figure_count": len(figures.get("figures") or []),
        "unresolved_count": len(figures.get("unresolved") or []),
    }

    if result.get("status") in (None, "partial", "insufficient_evidence", "input_error"):
        pages = result.get("pages")
        if isinstance(pages, list) and pages and not errors:
            result["status"] = "complete"

    append_change_log(result, "merge", "plan.json + figure_model.json -> result.json")
    write_json(result_path, result)
    return errors, warnings, result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(result_path, work_dir, output_dir, strict=False):
    """Validate ``result.json``; returns ``(errors, warnings, ok)``.

    Blocking errors (unreadable file, missing case_id, missing contract key)
    always fail. Evidence and artifact problems are warnings by default and
    errors under ``strict``, which is the final gate before delivery.
    """
    errors, warnings = [], []
    result, rerr = read_json(result_path)
    if rerr:
        errors.append("result.json: %s" % rerr)
        return errors, warnings, False
    if not isinstance(result, dict):
        errors.append("result.json root is not an object")
        return errors, warnings, False

    case_id = result.get("case_id")
    if not case_id:
        errors.append("missing case_id")

    status = result.get("status")
    if status not in ALLOWED_STATUS:
        errors.append("status %r not in %s" % (status, sorted(ALLOWED_STATUS)))

    # Preserve every declared contract key; a key may legitimately be null while
    # a partial result is being built, but it must exist in the schema. The set
    # of declared keys is recorded at scaffold time in ``contract_fields``.
    expected_contract = result.get("contract_fields")
    if not isinstance(expected_contract, list) or not expected_contract:
        expected_contract = DEFAULT_CONTRACT_KEYS
    for key in expected_contract:
        if key not in result:
            errors.append("missing contract field: %s" % key)

    pages = result.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                errors.append("page entry is not an object")
                continue
            for field in ("page_index", "title", "single_claim"):
                if not page.get(field):
                    warnings.append("page %s missing %s" % (page.get("page_index", "?"), field))
            strength = page.get("evidence_strength")
            if strength is not None and strength not in EVIDENCE_STRENGTHS:
                warnings.append("page %s evidence_strength %r invalid" %
                                (page.get("page_index"), strength))
            if page.get("single_claim") and not _norm_refs(page.get("source_refs")):
                msg = "page %s has a claim but no source_refs" % page.get("page_index")
                (errors if strict else warnings).append(msg)
    elif pages is not None:
        errors.append("pages is not a list")

    evidence = result.get("evidence_map")
    if isinstance(evidence, list):
        for entry in evidence:
            if not isinstance(entry, dict):
                warnings.append("evidence entry is not an object")
                continue
            if not (entry.get("locator") or "").strip() or not (entry.get("quote") or "").strip():
                msg = "evidence entry missing locator or verbatim quote"
                (errors if strict else warnings).append(msg)
    elif evidence is not None:
        errors.append("evidence_map is not a list")

    _check_artifacts(result, output_dir, errors if strict else warnings)

    if status in ("complete",):
        if not (isinstance(pages, list) and pages):
            (errors if strict else warnings).append("status=complete but no pages")
        if not (isinstance(evidence, list) and evidence):
            (errors if strict else warnings).append("status=complete but evidence_map empty")

    ok = len(errors) == 0
    return errors, warnings, ok


def _check_artifacts(result, output_dir, sink):
    """Warn/error when referenced editable/render files do not exist."""
    deck = result.get("editable_deck")
    if isinstance(deck, dict):
        p = deck.get("path")
        if p and not os.path.exists(p):
            sink.append("editable_deck file missing: %s" % p)
    rendering = result.get("rendering")
    if isinstance(rendering, dict):
        for key in ("source", "pdf", "preview"):
            v = rendering.get(key)
            if isinstance(v, str) and v and not os.path.exists(v):
                sink.append("rendering.%s missing: %s" % (key, v))
        exports = rendering.get("exports")
        if isinstance(exports, list):
            for item in exports:
                p = item.get("path") if isinstance(item, dict) else item
                if isinstance(p, str) and p and not os.path.exists(p):
                    sink.append("rendering export missing: %s" % p)


def summarize(result_path, plan_path, figure_path):
    """Return a compact human-readable summary of pipeline state."""
    result, rerr = read_json(result_path)
    plan, _ = read_json(plan_path)
    figures, _ = read_json(figure_path)
    if rerr or not isinstance(result, dict):
        return {"result_ok": False, "error": rerr}
    pages = result.get("pages")
    evidence = result.get("evidence_map")
    return {
        "result_ok": True,
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "pages": len(pages) if isinstance(pages, list) else 0,
        "evidence_entries": len(evidence) if isinstance(evidence, list) else 0,
        "sources": len(result.get("sources") or []),
        "uncertainty": len(result.get("uncertainty") or []),
        "change_log": len(result.get("change_log") or []),
        "plan_pages": len(plan.get("pages") or []) if isinstance(plan, dict) else 0,
        "figures": len(figures.get("figures") or []) if isinstance(figures, dict) else 0,
        "figure_unresolved": len(figures.get("unresolved") or []) if isinstance(figures, dict) else 0,
    }
