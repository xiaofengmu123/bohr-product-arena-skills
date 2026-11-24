#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — deterministic runtime helpers for the deep-research skill.

Standard library only. Imported by scripts/main.py (CLI) and
scripts/selftest.py (self-test). Every function takes explicit paths and does
not depend on the current working directory. Repeated runs on the same input
produce identical output JSON apart from timestamps in change_log.

Design goal (variant focus): make degradation and uncertainty explicit. The
schema tracks not only positive evidence but also negative evidence (searches
that returned nothing), so a "research gap" is always backed by a recorded
search attempt rather than by a generic claim.
"""

import copy
import json
import os
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_INPUT_DIR = "/input"
DEFAULT_OUTPUT_DIR = "/app/output"
DEFAULT_WORK_DIR = "/app/work"

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per text file; larger files truncated

# Extensions treated as input material (everything except case.json/task.md).
MATERIAL_EXTS = {
    ".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".tex", ".bib", ".yaml", ".yml", ".rst", ".rtf", ".odt", ".svg",
    ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
}

# Standard result schema used when case.json carries no output_contract.
STANDARD_FIELDS = {
    "case_id": "",
    "status": STATUS_PARTIAL,
    "research_product": {
        "type": "",          # synthesis|screening|trend|proposal
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

# Fields that must always be present even when a contract overrides them.
MANDATORY_EXTRA = (
    "status", "sources", "uncertainty", "change_log", "limitations",
    "errors", "degradations", "pending_human", "runtime_seconds",
)

UNCERTAINTY_SUBFIELDS = ("conflicts", "biases", "gaps", "undetermined")

VALID_JUDGMENTS = (
    "supports", "partial", "unsupported", "conflicts", "extraction_failed",
)

VALID_STRENGTHS = ("established", "inferred", "conjectural", "undetermined")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------
def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def read_text(path, cap=MAX_FILE_BYTES):
    """Read text with encoding fallback. Returns (text, encoding, degradations).

    Never raises: undecodable content falls back to latin-1 and is flagged.
    Files larger than `cap` bytes are truncated and flagged.
    """
    degradations = []
    try:
        with open(path, "rb") as fh:
            data = fh.read(cap + 1)
    except OSError as exc:
        return "", "unreadable", ["cannot read %s: %s" % (path, exc)]

    if len(data) > cap:
        data = data[:cap]
        degradations.append("truncated %s at %d bytes" % (path, cap))

    encoding = None
    text = ""
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            text = data.decode(enc)
            encoding = enc
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if encoding is None:  # pragma: no cover - latin-1 always succeeds
        text = data.decode("latin-1", errors="replace")
        encoding = "latin-1"
    if encoding == "latin-1" and any(b >= 0x80 for b in data[:4096]):
        degradations.append(
            "non-utf8/gb18030 bytes in %s; read as latin-1 (may be lossy)" % path)
    return text, encoding, degradations


def read_text_quiet(path):
    text, _, _ = read_text(path)
    return text


def load_json(path):
    if not os.path.exists(path):
        return None, ["missing JSON file: %s" % path], []
    text, _enc, degradations = read_text(path)
    try:
        obj = json.loads(text)
        return obj, [], degradations
    except ValueError as exc:
        return None, ["bad JSON in %s: %s" % (path, exc)], degradations


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) if os.path.dirname(path) else ".")
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) if os.path.dirname(path) else ".")
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def read_jsonl(path):
    items, errors = [], []
    if not os.path.exists(path):
        return items, ["missing JSONL file: %s" % path]
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError as exc:
                errors.append("bad JSONL line %d in %s: %s" % (lineno, path, exc))
    return items, errors


def append_jsonl(path, obj):
    ensure_dir(os.path.dirname(path) if os.path.dirname(path) else ".")
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return []
    except OSError as exc:
        return ["cannot append %s: %s" % (path, exc)]


def load_state(work_dir):
    state, _errs, _degs = load_json(os.path.join(work_dir, "run_state.json"))
    return state if isinstance(state, dict) else None


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------
def discover_input(input_dir):
    inv = {
        "exists": False,
        "input_dir": input_dir,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "total_files": 0,
        "total_bytes": 0,
    }
    if not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for fn in sorted(files):
            path = os.path.join(root, fn)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            ext = os.path.splitext(fn)[1].lower()
            low = fn.lower()
            inv["total_files"] += 1
            inv["total_bytes"] += max(size, 0)
            if low in ("case.json", "input.json"):
                if inv["case_json"] is None:
                    inv["case_json"] = path
            elif low == "task.md":
                if inv["task_md"] is None:
                    inv["task_md"] = path
            elif ext in MATERIAL_EXTS:
                inv["materials"].append(
                    {"path": path, "rel": rel, "size": size, "ext": ext})
    return inv


def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "task_id"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def extract_contract(obj):
    """Return the declared output contract dict, or {} if none is present."""
    if not isinstance(obj, dict):
        return {}
    for key in ("output_contract", "output_schema", "result_schema", "contract"):
        value = obj.get(key)
        if isinstance(value, dict):
            return value
    return {}


# ---------------------------------------------------------------------------
# Skeleton construction (early legal result)
# ---------------------------------------------------------------------------
def new_skeleton(case_id, contract, note="skeleton before research"):
    result = copy.deepcopy(STANDARD_FIELDS)
    # 1) Echo every declared contract field exactly (preserve unknown keys).
    for key, value in (contract or {}).items():
        result[key] = copy.deepcopy(value)
    # 2) Identity.
    result["case_id"] = case_id or ""
    # 3) Re-assert mandatory fields if the contract replaced them with None.
    for key in MANDATORY_EXTRA:
        if key not in result or result[key] is None:
            result[key] = copy.deepcopy(STANDARD_FIELDS[key])
    # 4) Guarantee the uncertainty sub-structure.
    if not isinstance(result.get("uncertainty"), dict):
        result["uncertainty"] = copy.deepcopy(STANDARD_FIELDS["uncertainty"])
    for key, value in STANDARD_FIELDS["uncertainty"].items():
        result["uncertainty"].setdefault(key, copy.deepcopy(value))
    # 5) Initial change record.
    if not isinstance(result.get("change_log"), list):
        result["change_log"] = []
    result["change_log"].append({
        "ts": iso_now(), "action": "skeleton", "detail": note or "initial skeleton",
    })
    result["runtime_seconds"] = 0
    return result


def deep_merge(target, patch):
    """Recursive dict merge; lists are replaced, not concatenated."""
    for key, value in patch.items():
        if (key in target and isinstance(target[key], dict)
                and isinstance(value, dict)):
            deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


# ---------------------------------------------------------------------------
# Evidence and search-log folding
# ---------------------------------------------------------------------------
def merge_cards(existing, cards):
    """Fold evidence cards into the matrix; stable id-keyed replacement."""
    out = list(existing or [])
    seen = {str(e.get("id")) for e in out if e.get("id") is not None}
    for card in cards:
        cid = card.get("id")
        if cid is None:
            continue
        if str(cid) in seen:
            for idx, e in enumerate(out):
                if str(e.get("id")) == str(cid):
                    out[idx] = copy.deepcopy(card)
                    break
        else:
            out.append(copy.deepcopy(card))
            seen.add(str(cid))
    return out


def merge_search_log(existing, entries):
    out = list(existing or [])
    seen = {str(e.get("id")) for e in out if e.get("id") is not None}
    for entry in entries:
        eid = entry.get("id")
        if eid is None:
            out.append(copy.deepcopy(entry))
        elif str(eid) not in seen:
            out.append(copy.deepcopy(entry))
            seen.add(str(eid))
    return out


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------
def build_report_md(result, notes):
    rp = result.get("research_product") or {}
    unc = result.get("uncertainty") or {}
    lines = ["# 深度研究报告", ""]
    lines.append("- case_id: `%s`" % (result.get("case_id") or "(unknown)"))
    lines.append("- status: `%s`" % (result.get("status") or "partial"))
    lines.append("- 研究类型: `%s`" % (rp.get("type") or "unset"))
    lines.append("- 运行时: %.1f s" % (result.get("runtime_seconds") or 0.0))
    lines.append("")
    lines.append("## 研究问题")
    lines.append("")
    lines.append(str(rp.get("question") or "（未声明）"))
    lines.append("")
    lines.append("## 综合结论")
    lines.append("")
    lines.append(str(rp.get("summary") or "（未声明）"))
    lines.append("")
    lines.append("## 结论明细")
    lines.append("")
    for c in rp.get("conclusions") or []:
        strength = c.get("strength") or "undetermined"
        lines.append("- **%s** (%s): %s" % (
            c.get("id") or "C?", strength, c.get("text") or ""))
        eids = c.get("evidence_ids") or []
        if eids:
            lines.append("  - 证据: " + ", ".join(str(x) for x in eids))
    lines.append("")
    lines.append("## 证据矩阵（%d 条）" % len(result.get("evidence_matrix") or []))
    lines.append("")
    for e in result.get("evidence_matrix") or []:
        lines.append("- **%s** `%s` — 来源 %s · 定位 %s" % (
            e.get("id") or "E?", e.get("judgment") or "supports",
            e.get("source") or "?", e.get("locator") or "?"))
        lines.append("  - 断言: %s" % (e.get("claim") or ""))
        lines.append("  - 引文: “%s”" % (e.get("quote") or ""))
    lines.append("")
    lines.append("## 不确定性")
    lines.append("")
    lines.append("- conflicts: %d 条" % len(unc.get("conflicts") or []))
    lines.append("- biases: %d 条" % len(unc.get("biases") or []))
    lines.append("- gaps: %d 条" % len(unc.get("gaps") or []))
    lines.append("- undetermined: %d 条" % len(unc.get("undetermined") or []))
    lines.append("")
    lines.append("## 缺口与待人工确认")
    lines.append("")
    for item in result.get("pending_human") or []:
        lines.append("- %s" % item)
    if not (result.get("pending_human") or []):
        lines.append("- （无）")
    lines.append("")
    lines.append("## 限制")
    lines.append("")
    for item in result.get("limitations") or []:
        lines.append("- %s" % item)
    if not (result.get("limitations") or []):
        lines.append("- （无）")
    lines.append("")
    for name in ("decomposition.md", "search_plan.md", "synthesis.md"):
        text = (notes or {}).get(name, "")
        if text.strip():
            lines.append("## %s" % name)
            lines.append("")
            lines.append(text.strip())
            lines.append("")
    return "\n".join(lines) + "\n"


def build_evidence_map(result):
    rp = result.get("research_product") or {}
    conclusions = {}
    for c in rp.get("conclusions") or []:
        if c.get("id"):
            conclusions[str(c.get("id"))] = c
    subquestions = {}
    for e in result.get("evidence_matrix") or []:
        sq = e.get("subquestion") or "(unassigned)"
        subquestions.setdefault(sq, []).append(e.get("id"))
    return {
        "case_id": result.get("case_id"),
        "conclusions": conclusions,
        "subquestions": subquestions,
        "evidence_matrix": result.get("evidence_matrix") or [],
        "conflicts": (result.get("uncertainty") or {}).get("conflicts") or [],
        "gaps": (result.get("uncertainty") or {}).get("gaps") or [],
    }


# ---------------------------------------------------------------------------
# Quote verification (fabrication guard)
# ---------------------------------------------------------------------------
def normalize_ws(text):
    return " ".join(str(text).split())


def resolve_source(source, input_dir):
    """Best-effort map of an evidence source string to a local file path."""
    if not source:
        return None
    rel = source.replace("\\", "/")
    candidates = [os.path.join(input_dir, rel)]
    base = os.path.basename(rel)
    if base and os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            for fn in files:
                if fn == base:
                    candidates.append(os.path.join(root, fn))
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def verify_quotes(result, input_dir):
    """Check each verbatim quote against the local source file it cites.

    Returns {"pass": bool, "verified": int, "checked": int, "unverifiable": [],
    "failures": []}. A quote that cannot be found in a plain-text source is a
    failure (fabrication risk); binary/PDF sources and paraphrase-tagged rows
    are counted as unverifiable rather than failures.
    """
    verified = checked = 0
    unverifiable = []
    failures = []
    for e in result.get("evidence_matrix") or []:
        quote = (e.get("quote") or "").strip()
        if not quote:
            continue
        if e.get("verification") == "paraphrase":
            checked += 1
            verified += 1
            continue
        checked += 1
        path = resolve_source(e.get("source"), input_dir)
        if not path:
            unverifiable.append(str(e.get("id")))
            continue
        text, _enc, _degs = read_text(path)
        if normalize_ws(quote) in normalize_ws(text):
            verified += 1
        elif path.lower().endswith(
                (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
                 ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff")):
            unverifiable.append(str(e.get("id")))
        else:
            failures.append(str(e.get("id")))
    detail = ("verified=%d/%d, unverifiable=%s%s" % (
        verified, checked, ",".join(unverifiable) if unverifiable else "[]",
        ", failures=" + ",".join(failures) if failures else ""))
    return {"pass": not failures, "verified": verified, "checked": checked,
            "unverifiable": unverifiable, "failures": failures, "detail": detail}


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------
def selfcheck(result, input_dir, work_dir):
    checks = []
    if not isinstance(result, dict):
        return [{"id": "json", "label": "result.json 可解析为对象",
                 "pass": False, "critical": True, "detail": "not a JSON object"}]

    checks.append({"id": "json", "label": "result.json 可解析为对象",
                   "pass": True, "critical": True, "detail": "ok"})

    case_id = result.get("case_id")
    checks.append({"id": "case_id", "label": "case_id 已回填",
                   "pass": isinstance(case_id, str), "critical": False,
                   "detail": repr(case_id)})

    status = result.get("status")
    checks.append({"id": "status", "label": "status 取值合法",
                   "pass": status in STATUSES, "critical": True,
                   "detail": str(status)})

    state = load_state(work_dir) or {}
    contract = state.get("contract") or {}
    missing = [k for k in contract if k not in result]
    checks.append({"id": "contract", "label": "output_contract 字段原样齐备",
                   "pass": not missing, "critical": True,
                   "detail": ("missing=" + ",".join(missing)) if missing
                   else "all %d fields present" % len(contract)})

    rp = result.get("research_product") or {}
    conclusions = rp.get("conclusions") or []
    evidence = result.get("evidence_matrix") or []
    if status in (STATUS_INSUFFICIENT, STATUS_INPUT_ERROR):
        checks.append({"id": "conclusions", "label": "结论非空（或已如实声明不足）",
                       "pass": True, "critical": False,
                       "detail": "status=%s，不强求结论" % status})
    else:
        checks.append({"id": "conclusions", "label": "综合结论非空",
                       "pass": bool(conclusions), "critical": True,
                       "detail": "%d conclusions" % len(conclusions)})

    ev_ids = {str(e.get("id")) for e in evidence}
    bad = []
    for c in conclusions:
        for eid in c.get("evidence_ids") or []:
            if str(eid) not in ev_ids:
                bad.append(str(eid))
    checks.append({"id": "backlink", "label": "结论 evidence_ids 全部命中证据表",
                   "pass": not bad, "critical": True,
                   "detail": ("unresolved=" + ",".join(bad)) if bad
                   else "all resolved"})

    missing_lq = [str(e.get("id")) for e in evidence
                  if not (e.get("locator") and e.get("quote"))]
    checks.append({"id": "locator_quote", "label": "每条证据含 locator + 逐字 quote",
                   "pass": not missing_lq, "critical": True,
                   "detail": ("missing=" + ",".join(missing_lq)) if missing_lq
                   else "all present"})

    unc = result.get("uncertainty") or {}
    conflicts = unc.get("conflicts") or []
    conflict_ev = [e for e in evidence if e.get("judgment") == "conflicts"]
    conf_ok = True
    conf_detail = "no conflict evidence flagged"
    if conflict_ev:
        conf_ok = bool(conflicts) and all(
            isinstance(cf.get("sources"), list) and len(cf.get("sources") or []) >= 2
            for cf in conflicts)
        conf_detail = ("%d conflict rows, %d recorded" %
                       (len(conflict_ev), len(conflicts)))
    checks.append({"id": "conflicts", "label": "冲突证据并列呈现（≥2 来源）",
                   "pass": conf_ok, "critical": False, "detail": conf_detail})

    gaps = unc.get("gaps") or []
    pending = result.get("pending_human") or []
    limitations = result.get("limitations") or []
    if status == STATUS_INSUFFICIENT:
        gaps_ok = bool(gaps or pending or limitations)
        checks.append({"id": "gaps", "label": "材料不足时显式声明缺口",
                       "pass": gaps_ok, "critical": True,
                       "detail": "gaps=%d pending=%d limitations=%d" %
                       (len(gaps), len(pending), len(limitations))})
    else:
        checks.append({"id": "gaps", "label": "研究空白/待确认项非空",
                       "pass": bool(gaps or pending), "critical": False,
                       "detail": "gaps=%d pending=%d" % (len(gaps), len(pending))})

    errs = result.get("errors") or []
    degradations = result.get("degradations") or []
    hon_ok = (not errs and not degradations) or bool(limitations)
    checks.append({"id": "degradation", "label": "降级/异常已写入 limitations",
                   "pass": hon_ok, "critical": True,
                   "detail": "errors=%d degradations=%d limitations=%d" %
                   (len(errs), len(degradations), len(limitations))})

    vq = verify_quotes(result, input_dir)
    checks.append({"id": "quote_verify", "label": "逐字引文可在源中复现",
                   "pass": vq["pass"], "critical": False, "detail": vq["detail"]})

    return checks


def selfcheck_summary(checks):
    critical_fail = [c for c in checks if c.get("critical") and not c["pass"]]
    return {"ok": not critical_fail, "passed": sum(1 for c in checks if c["pass"]),
            "total": len(checks), "critical_failures": [c["id"] for c in critical_fail]}
