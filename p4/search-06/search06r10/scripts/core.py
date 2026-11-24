# -*- coding: utf-8 -*-
"""Domain logic for the literature-delta Skill.

Design: every module-level function is deterministic and side-effect free
(no network, no wall-clock branching that changes results). I/O helpers use
utf-8-sig tolerant reads, per-file size caps, and atomic writes so that the
same inputs always produce the same outputs (idempotency). The ordered
"pipeline" below is an explicit stage registry: each stage has a `run`
function and a `validate` function, executed in a fixed order with a
change_log entry after every stage. This keeps validation order observable,
unlike a single monolithic main() that mixes parsing, judging and writing.
"""

import json
import os
import re
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 2_000_000

DEFAULT_CONTRACT_KEYS = ["search_results", "evidence", "status_notes"]

# Domain fields always present in result.json beyond case_id + contract.
DOMAIN_FIELDS = [
    "search_results",
    "ranking",
    "evidence",
    "status_notes",
    "tracking",
    "dedup",
]

# Meta fields always present in result.json.
META_DEFAULTS = {
    "status": None,
    "sources": [],
    "uncertainty": [],
    "change_log": [],
    "errors": [],
    "degradations": [],
    "pending_human": [],
    "runtime": {},
}

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

_TYPE_PRIORITY = {"doi": 0, "arxiv": 1, "title": 2, "none": 3}

VERSION_ORDER = {
    "preprint": 0,
    "unknown": 0,
    "journal": 1,
    "published": 1,
    "correction": 2,
    "erratum": 2,
    "corrigendum": 2,
    "retraction": 3,
}

_CASE_ID_HINTS = ("case_id", "id", "challenge_id")
_CASE_ID_RE = re.compile(r"challenge_id[:\s]*`?([A-Za-z0-9_.\-]+)`?")


# ---------------------------------------------------------------------------
# Deterministic text / identifier normalization
# ---------------------------------------------------------------------------

def _strip_accents(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


def normalize_title(title):
    """Stable case/punct/whitespace-insensitive title key (keeps CJK)."""
    if title is None:
        return ""
    s = unicodedata.normalize("NFKD", str(title))
    s = _strip_accents(s)
    s = s.lower()
    return "".join(ch for ch in s if ch.isalnum())


def normalize_doi(doi):
    """Return a canonical DOI string, or "" when absent/unparseable."""
    if doi is None:
        return ""
    s = str(doi).strip()
    low = s.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if low.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.strip().lower().rstrip(".,;")
    m = re.match(r"10\.\d{4,9}/\S+", s)
    return m.group(0).rstrip(".,;") if m else s


def normalize_arxiv(arxiv):
    """Return a canonical arXiv id (version suffix removed)."""
    if arxiv is None:
        return ""
    s = str(arxiv).strip()
    low = s.lower()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:"):
        if low.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.strip()
    m = re.match(r"^(\d{4})\.(\d{4,5})(?:v\d+)?$", s)
    if m:
        return "%s.%s" % (m.group(1), m.group(2))
    m = re.match(r"^[a-z\-]+(?:\.[a-z\-]+)?/\d{7}(?:v\d+)?$", s)
    if m:
        return re.sub(r"v\d+$", "", s)
    return s


def canonical_id(record):
    """Return (key_type, key) with priority DOI > arXiv > normalized title."""
    doi = normalize_doi(record.get("doi"))
    if doi:
        return ("doi", doi)
    arx = normalize_arxiv(record.get("arxiv"))
    if arx:
        return ("arxiv", arx)
    title = normalize_title(record.get("title"))
    if title:
        return ("title", title)
    return ("none", "")


def key_str(kind_key):
    """Serialize a canonical_id tuple to a stable string key."""
    kind, key = kind_key
    return "%s:%s" % (kind, key)


def classify_version(record):
    """Map record version/kind hints to a canonical version token."""
    raw = str(record.get("version") or record.get("kind") or "").lower()
    if raw in ("preprint", "pre-print", "arxiv", "working paper", "workingpaper"):
        return "preprint"
    if raw in ("journal", "published", "article", "final", "version of record"):
        return "journal"
    if raw in ("correction", "erratum", "corrigendum", "update", "revised"):
        return "correction"
    if raw in ("retraction", "retracted", "withdrawn"):
        return "retraction"
    return "unknown"


def is_retraction(record):
    """True when the record marks a retraction/withdrawal."""
    return classify_version(record) == "retraction" or bool(record.get("retracted"))


def _max_version(versions):
    best = -1
    for v in versions:
        best = max(best, VERSION_ORDER.get(v, 0))
    return best


# ---------------------------------------------------------------------------
# Dedup / version chain / incremental diff
# ---------------------------------------------------------------------------

def _pick_representative(members):
    """Pick the metadata source with the richest stable identifiers."""
    def score(m):
        s = 0
        if normalize_doi(m.get("doi")):
            s += 4
        if normalize_arxiv(m.get("arxiv")):
            s += 2
        if m.get("title"):
            s += 1
        if m.get("published_date") or m.get("date"):
            s += 1
        return s

    return max(members, key=lambda m: (score(m), str(m.get("published_date") or "")))


def dedup(records):
    """Group records by canonical_id and merge same-document versions.

    Returns a list of merged groups, sorted deterministically by key type
    priority then key value. Duplicates (reposts, DOI+arXiv coexistence,
    multi-source entries) collapse into one group.
    """
    if not isinstance(records, list):
        return []
    groups = OrderedDict()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        kid = canonical_id(rec)
        g = groups.setdefault(key_str(kid), {
            "canonical_id": {"type": kid[0], "value": kid[1]},
            "members": [],
            "retracted": False,
        })
        g["members"].append(rec)
        if is_retraction(rec):
            g["retracted"] = True

    merged = []
    for gkey, g in groups.items():
        rep = _pick_representative(g["members"])
        versions = sorted({classify_version(m) for m in g["members"]},
                          key=lambda v: VERSION_ORDER.get(v, 0))
        merged.append({
            "canonical_id": g["canonical_id"],
            "member_count": len(g["members"]),
            "representative": rep,
            "version_chain": versions,
            "retracted": g["retracted"],
            "members": g["members"],
        })
    merged.sort(key=lambda m: (
        _TYPE_PRIORITY.get(m["canonical_id"]["type"], 9),
        m["canonical_id"]["value"],
    ))
    return merged


def snapshot_of(records):
    """Build a {canonical_key: {versions, retracted}} snapshot from records."""
    snap = {}
    for rec in (records if isinstance(records, list) else []):
        if not isinstance(rec, dict):
            continue
        kid = canonical_id(rec)
        k = key_str(kid)
        if kid[0] == "none":
            continue
        entry = snap.setdefault(k, {"versions": set(), "retracted": False})
        entry["versions"].add(classify_version(rec))
        if is_retraction(rec):
            entry["retracted"] = True
    return {
        k: {"versions": sorted(v["versions"], key=lambda x: VERSION_ORDER.get(x, 0)),
            "retracted": v["retracted"]}
        for k, v in snap.items()
    }


def diff_snapshot(current_records, snapshot):
    """Diff current records against a previous snapshot.

    `snapshot` may be a dict (from snapshot_of) or a list of canonical keys.
    Returns {'new': [...], 'updated': [...], 'removed': [...]}.
    """
    if isinstance(snapshot, list):
        prev = {str(k): {"versions": [], "retracted": False} for k in snapshot}
    elif isinstance(snapshot, dict):
        prev = {str(k): (v if isinstance(v, dict) else {"versions": [], "retracted": False})
                for k, v in snapshot.items()}
    else:
        prev = {}

    cur = snapshot_of(current_records)
    new = sorted(k for k in cur if k not in prev)
    removed = sorted(k for k in prev if k not in cur)
    updated = []
    for k in cur:
        if k not in prev:
            continue
        old = prev[k]
        if cur[k]["retracted"] and not old.get("retracted"):
            updated.append(k)
        elif _max_version(cur[k]["versions"]) > _max_version(old.get("versions", [])):
            updated.append(k)
    return {"new": new, "updated": sorted(updated), "removed": removed}


def build_reminders(diff, records):
    """Build reminder list; dedup + snapshot diff already suppress reposts."""
    reminders = []
    cur = snapshot_of(records)
    for k in diff["new"]:
        reminders.append({"key": k, "kind": "new", "reason": "not in previous snapshot"})
    for k in diff["updated"]:
        kind = "retraction" if cur.get(k, {}).get("retracted") else "update"
        reminders.append({"key": k, "kind": kind,
                          "reason": "version advanced or newly retracted"})
    return reminders


# ---------------------------------------------------------------------------
# Tolerant I/O (utf-8-sig, size cap, atomic write)
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    p = Path(path)
    if not p.exists():
        return None, ["missing file: %s" % p]
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, ["stat failed %s: %s" % (p, exc)]
    if size > max_bytes:
        return None, ["file too large (%d > %d): %s" % (size, max_bytes, p)]
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return None, ["read failed %s: %s" % (p, exc)]
    for enc in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc), []
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), ["decoded with replacement: %s" % p]


def load_json(path):
    text, errs = read_text(path)
    if text is None:
        return None, errs
    try:
        return json.loads(text), errs
    except json.JSONDecodeError as exc:
        return None, errs + ["json parse failed %s: %s" % (path, exc)]


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return str(p)


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, p)
    return str(p)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Input discovery & contract extraction
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    root = Path(input_dir)
    errors = []
    case, task = None, None
    materials = []
    if not root.exists():
        return None, None, [], ["input dir missing: %s" % root]
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.name.lower() == "case.json":
            case = p
        elif p.name.lower() == "task.md":
            task = p
        else:
            materials.append(p)
    if case is None and task is None:
        errors.append("no case.json or task.md found under %s" % root)
    return case, task, materials, errors


def _find_case_id(obj):
    if isinstance(obj, dict):
        for k in _CASE_ID_HINTS:
            if obj.get(k) not in (None, ""):
                return str(obj[k])
        for k in ("task", "spec", "input", "metadata"):
            v = _find_case_id(obj.get(k))
            if v:
                return v
    return None


def extract_case_id(case_obj, task_text):
    cid = _find_case_id(case_obj)
    if cid:
        return cid
    if task_text:
        m = _CASE_ID_RE.search(task_text)
        if m:
            return m.group(1)
    return "unknown-case"


def find_output_contract(obj):
    if isinstance(obj, dict):
        for k in ("output_contract", "contract", "output_schema", "output"):
            v = obj.get(k)
            if isinstance(v, dict):
                return list(v.keys())
            if isinstance(v, list):
                return [str(x) for x in v if isinstance(x, str)]
            if isinstance(v, str):
                return [v]
        for k in ("task", "spec", "input"):
            found = find_output_contract(obj.get(k))
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Context (immutable per run)
# ---------------------------------------------------------------------------

def build_context(input_dir, output_dir, work_dir):
    case_path, task_path, materials, errors = discover_input(input_dir)
    case_obj, task_text = None, None
    if case_path is not None:
        case_obj, errs = load_json(str(case_path))
        errors.extend(errs)
    if task_path is not None:
        task_text, errs = read_text(str(task_path))
        errors.extend(errs)

    case_id = extract_case_id(case_obj, task_text)
    contract = find_output_contract(case_obj)
    if not contract:
        contract = list(DEFAULT_CONTRACT_KEYS)

    return {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "work_dir": str(work_dir),
        "case_path": str(case_path) if case_path else None,
        "task_path": str(task_path) if task_path else None,
        "materials": [str(m) for m in materials],
        "case_obj": case_obj,
        "task_text": task_text,
        "case_id": case_id,
        "contract_keys": contract,
        "errors": errors,
        "degradations": [],
        "change_log": [],
        "findings": {},
    }


def initial_status(ctx):
    errors = ctx["errors"]
    has_case = ctx["case_obj"] is not None
    has_task = ctx["task_text"] is not None
    malformed = any("json parse failed" in e or "read failed" in e for e in errors)
    if not has_case and not has_task:
        return "input_error" if malformed else "insufficient_evidence"
    return "input_error" if malformed else "partial"


def make_skeleton(ctx, status=None):
    if status is None:
        status = initial_status(ctx)
    result = {"case_id": ctx["case_id"]}
    for k in ctx["contract_keys"]:
        result.setdefault(k, None)
    for k in DOMAIN_FIELDS:
        result.setdefault(k, None if k == "status_notes" else [] if k in (
            "search_results", "ranking", "evidence") else {})
    result["status_notes"] = {"empty": None, "conflicts": [], "permission_errors": []}
    result["tracking"] = {
        "subscription": None, "window": None, "snapshot": None,
        "new_items": [], "updates": [], "retractions": [], "reminders": [],
    }
    result["dedup"] = {}
    for k, default in META_DEFAULTS.items():
        result[k] = default
    result["status"] = status
    result["runtime"] = {"seconds": 0.0, "utc_started": utc_now(), "requests_used": 0}
    result["change_log"] = [{
        "event": "skeleton_written", "at": utc_now(), "status": status,
    }]
    return result


# ---------------------------------------------------------------------------
# Pipeline stage registry (ordered; run then validate)
# ---------------------------------------------------------------------------

def stage_scaffold(ctx):
    result = make_skeleton(ctx)
    write_json(str(Path(ctx["output_dir"]) / "result.json"), result)
    return {"result": result}


def validate_scaffold(ctx, out):
    errs = []
    r = out["result"]
    if r.get("case_id") in (None, ""):
        errs.append("skeleton missing case_id")
    if "status" not in r:
        errs.append("skeleton missing status")
    for k in ctx["contract_keys"]:
        if k not in r:
            errs.append("skeleton missing contract field: %s" % k)
    return errs


def stage_inventory(ctx):
    inv = {
        "case_path": ctx["case_path"],
        "task_path": ctx["task_path"],
        "materials": ctx["materials"],
        "material_count": len(ctx["materials"]),
        "errors": ctx["errors"],
        "at": utc_now(),
    }
    write_json(str(Path(ctx["work_dir"]) / "inventory.json"), inv)
    return {"inventory": inv}


def validate_inventory(ctx, out):
    errs = []
    if not out["inventory"]["materials"] and not out["inventory"]["case_path"] \
            and not out["inventory"]["task_path"]:
        errs.append("no input material discovered")
    return errs


def load_findings(work_dir, explicit_path=None):
    path = Path(explicit_path) if explicit_path else Path(work_dir) / "findings.json"
    if not path.exists():
        return {}, []
    obj, errs = load_json(str(path))
    if not isinstance(obj, dict):
        return {}, errs + ["findings.json is not a JSON object: %s" % path]
    return obj, errs


def stage_finalize(ctx):
    findings = ctx.get("findings") or {}
    records = findings.get("records") or []
    merged = dedup(records)
    snap = snapshot_of(records)
    prev_snap = findings.get("snapshot")
    prev_snap = prev_snap.get("previous_ids") if isinstance(prev_snap, dict) else prev_snap
    diff = diff_snapshot(records, prev_snap)
    reminders = build_reminders(diff, records)

    result = make_skeleton(ctx)
    for k, v in findings.items():
        if k in DOMAIN_FIELDS or k in META_DEFAULTS or k == "status":
            result[k] = v
    result["search_results"] = findings.get("search_results", records)
    result["ranking"] = findings.get("ranking", [])
    result["evidence"] = findings.get("evidence", [])
    result["sources"] = findings.get("sources", ctx["materials"])
    result["uncertainty"] = findings.get("uncertainty", [])
    result["pending_human"] = findings.get("pending_human", [])
    result["errors"] = ctx["errors"] + findings.get("errors", [])
    result["degradations"] = ctx["degradations"] + findings.get("degradations", [])

    tracking = dict(result["tracking"])
    tracking["subscription"] = findings.get("subscription", tracking.get("subscription"))
    tracking["window"] = findings.get("window", tracking.get("window"))
    tracking["snapshot"] = findings.get("snapshot", {"previous_ids": prev_snap or [],
                                                     "current": snap})
    tracking["new_items"] = [k for k in diff["new"]]
    tracking["updates"] = [k for k in diff["updated"]]
    tracking["retractions"] = [k for k in diff["updated"] if snap.get(k, {}).get("retracted")]
    tracking["reminders"] = reminders
    result["tracking"] = tracking

    dedup_info = findings.get("dedup", {})
    dedup_info["input_count"] = len(records)
    dedup_info["merged_count"] = len(merged)
    dedup_info["duplicates_suppressed"] = max(0, len(records) - len(merged))
    dedup_info["canonical_key_priority"] = ["doi", "arxiv", "title"]
    result["dedup"] = dedup_info

    result["status"] = resolve_status(ctx, findings)
    result["runtime"]["seconds"] = round(time.time() - ctx.get("started_at", time.time()), 3)
    result["change_log"] = ctx["change_log"] + [
        {"event": "finalize", "at": utc_now(),
         "status": result["status"],
         "new": len(diff["new"]), "updated": len(diff["updated"]),
         "removed": len(diff["removed"]), "merged": len(merged)},
    ]

    write_json(str(Path(ctx["output_dir"]) / "result.json"), result)
    write_text(str(Path(ctx["output_dir"]) / "report.md"), render_report(result, ctx))
    return {"result": result, "diff": diff, "merged": merged}


def resolve_status(ctx, findings):
    base = initial_status(ctx)
    if base in ("input_error", "insufficient_evidence"):
        return base
    status = findings.get("status")
    if status in VALID_STATUS:
        return status
    if findings.get("records"):
        return "ok"
    return "partial"


def validate_finalize(ctx, out):
    errs = []
    r = out["result"]
    if r.get("status") not in VALID_STATUS:
        errs.append("invalid status: %r" % r.get("status"))
    for k in ctx["contract_keys"]:
        if k not in r:
            errs.append("result missing contract field: %s" % k)
    return errs


PIPELINE = [
    {"name": "scaffold", "run": stage_scaffold, "validate": validate_scaffold},
    {"name": "inventory", "run": stage_inventory, "validate": validate_inventory},
    {"name": "finalize", "run": stage_finalize, "validate": validate_finalize},
]


def run_pipeline(ctx, stage_names=None):
    wanted = [s["name"] for s in PIPELINE] if stage_names is None else list(stage_names)
    ctx["started_at"] = time.time()
    for stage in PIPELINE:
        if stage["name"] not in wanted:
            continue
        t0 = time.time()
        out = stage["run"](ctx)
        errs = stage["validate"](ctx, out)
        ctx["change_log"].append({
            "event": "stage_%s" % stage["name"],
            "at": utc_now(),
            "ok": not errs,
            "errors": errs,
            "seconds": round(time.time() - t0, 3),
        })
    return ctx


# ---------------------------------------------------------------------------
# Command implementations (called by main.py)
# ---------------------------------------------------------------------------

def cmd_scaffold(input_dir, output_dir, work_dir):
    ctx = build_context(input_dir, output_dir, work_dir)
    run_pipeline(ctx, ["scaffold"])
    return 0


def cmd_inventory(input_dir, output_dir, work_dir):
    ctx = build_context(input_dir, output_dir, work_dir)
    run_pipeline(ctx, ["scaffold", "inventory"])
    return 0


def cmd_normalize(record_arg):
    if record_arg.startswith("@"):
        text, errs = read_text(record_arg[1:])
        if text is None:
            print(json.dumps({"error": errs}, ensure_ascii=False))
            return 1
        obj = json.loads(text)
    else:
        obj = json.loads(record_arg)
    kid = canonical_id(obj)
    out = {
        "input": obj,
        "canonical_id": {"type": kid[0], "value": kid[1]},
        "title_norm": normalize_title(obj.get("title")),
        "doi_norm": normalize_doi(obj.get("doi")),
        "arxiv_norm": normalize_arxiv(obj.get("arxiv")),
        "version": classify_version(obj),
        "retracted": is_retraction(obj),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_dedup(records_path, out_path):
    records, errs = load_json(records_path)
    if records is None or not isinstance(records, list):
        records = []
    merged = dedup(records)
    result = {
        "input_count": len(records),
        "merged_count": len(merged),
        "duplicates_suppressed": max(0, len(records) - len(merged)),
        "canonical_key_priority": ["doi", "arxiv", "title"],
        "groups": merged,
    }
    write_json(out_path, result)
    print(json.dumps({"input": len(records), "merged": len(merged)}, ensure_ascii=False))
    return 0


def cmd_finalize(input_dir, output_dir, work_dir, findings_path):
    ctx = build_context(input_dir, output_dir, work_dir)
    findings, errs = load_findings(work_dir, findings_path)
    ctx["findings"] = findings
    ctx["errors"].extend(errs)
    run_pipeline(ctx, ["scaffold", "inventory", "finalize"])
    return 0


def cmd_run(input_dir, output_dir, work_dir):
    """Deterministic fallback: scaffold + inventory + finalize with empty findings.

    Produces a valid, honest result even when the agent performed no research.
    """
    ctx = build_context(input_dir, output_dir, work_dir)
    ctx["findings"] = {}
    run_pipeline(ctx, ["scaffold", "inventory", "finalize"])
    return 0


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(result, ctx):
    tr = result.get("tracking", {})
    lines = [
        "# 文献增量追踪报告",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- 候选条数: %d" % len(result.get("search_results", []) or []),
        "- 证据条数: %d" % len(result.get("evidence", []) or []),
        "- 去重前/后: %d / %d" % (result.get("dedup", {}).get("input_count", 0),
                                  result.get("dedup", {}).get("merged_count", 0)),
        "",
        "## 增量判定",
        "",
        "- 新增: %s" % (", ".join(tr.get("new_items", [])) or "无"),
        "- 更新: %s" % (", ".join(tr.get("updates", [])) or "无"),
        "- 撤稿/更正: %s" % (", ".join(tr.get("retractions", [])) or "无"),
        "- 提醒数: %d" % len(tr.get("reminders", []) or []),
        "",
        "## 不确定性",
        "",
    ]
    unc = result.get("uncertainty", []) or []
    if unc:
        for u in unc:
            if isinstance(u, dict):
                lines.append("- %s" % (u.get("note") or json.dumps(u, ensure_ascii=False)))
            else:
                lines.append("- %s" % u)
    else:
        lines.append("- 无（或未标注）")
    lines += ["", "## 错误与降级", ""]
    errs = result.get("errors", []) or []
    degs = result.get("degradations", []) or []
    if errs or degs:
        for e in errs:
            lines.append("- [error] %s" % e)
        for d in degs:
            lines.append("- [degradation] %s" % d)
    else:
        lines.append("- 无")
    lines += ["", "## 待人工确认", ""]
    pend = result.get("pending_human", []) or []
    if pend:
        for p in pend:
            lines.append("- %s" % p)
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)
