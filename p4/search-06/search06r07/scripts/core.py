# -*- coding: utf-8 -*-
"""Core implementation for the literature-tracking Skill.

Deterministic, standard-library-only helpers for:
  * input discovery (recursive scan, no fixed filenames assumed),
  * tolerant text / JSON loading (encoding fallback, JSON salvage, size guard),
  * canonical dedup keys (DOI / arXiv / normalized title) and version chains,
  * partial-result skeleton assembly and finalize/merge logic.

Nothing in this module fabricates literature content.  When inputs are
missing or unreadable the result is an honest ``insufficient_evidence`` /
``input_error`` status plus recorded errors, never invented records.

Determinism: every path is sorted, no randomness is used, and the wall clock
can be fixed via the ``SKILL_NOW`` environment variable so two runs over the
same inputs produce byte-identical outputs (see ``utcnow``).
"""

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024      # 2 MB per input file
MAX_TOTAL_BYTES = 32 * 1024 * 1024    # 32 MB aggregate input budget

# Meta fields always present in result.json beyond case_id + output contract.
META_FIELDS = [
    "status",
    "sources",
    "uncertainty",
    "change_log",
    "errors",
    "degradations",
    "pending_human",
    "runtime",
]

# Domain-level contract keys used when no output_contract is found in the
# case.  They cover the three required output rows of the task (candidate
# list, evidence, status notes) plus the tracking/dedup state.
DEFAULT_CONTRACT_KEYS = [
    "search_results",
    "ranking",
    "evidence",
    "status_notes",
    "tracking",
    "dedup",
]

DEFAULT_CONTRACT_DEFAULTS = {
    "search_results": [],
    "ranking": [],
    "evidence": [],
    "status_notes": {"empty": None, "conflicts": [], "permission_errors": []},
    "tracking": {
        "subscription": None,
        "window": None,
        "snapshot": None,
        "new_items": [],
        "updates": [],
        "retractions": [],
        "reminders": [],
    },
    "dedup": {},
}

_CASE_ID_HINTS = ("case_id", "id", "challenge_id")
_CASE_ID_RE = re.compile(
    r"challenge_id[:\s]*`([^`]+)`|challenge_id[:\s]*([\w.-]+)"
)
_CONTRACT_KEYS = ("output_contract", "contract", "output_schema", "output")

_RETRACT_RE = re.compile(r"\bretract(?:ed|ion)?\b", re.I)
_CORRECT_RE = re.compile(
    r"\b(erratum|corrigendum|correction|addendum)\b", re.I
)
_PREPRINT_RE = re.compile(
    r"\b(preprint|pre-print)\b|arxiv|biorxiv|medrxiv|chemrxiv|ssrn", re.I
)


# ---------------------------------------------------------------------------
# Clock (fixable for deterministic replay)
# ---------------------------------------------------------------------------

def utcnow():
    """Return an ISO-8601 UTC timestamp.

    ``SKILL_NOW`` overrides the wall clock so replays are byte-identical.
    """
    env = os.environ.get("SKILL_NOW")
    if env:
        return env
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tolerant file reading
# ---------------------------------------------------------------------------

def read_text_safe(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file with BOM/encoding fallback and a size guard.

    Returns ``(text_or_none, encoding_or_none, error_or_none)``.  The last
    resort is latin-1 with replacement, which never raises on bytes.
    """
    p = Path(path)
    try:
        if not p.exists():
            return None, None, "missing file: %s" % p
        size = p.stat().st_size
    except OSError as exc:
        return None, None, "stat failed for %s: %s" % (p, exc)

    if size > max_bytes:
        return None, None, "size limit exceeded (%d bytes > %d): %s" % (
            size, max_bytes, p
        )

    try:
        raw = p.read_bytes()
    except OSError as exc:
        return None, None, "read failed for %s: %s" % (p, exc)

    # BOM-guided decodes first.
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig", None
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace"), "utf-16-le", None
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace"), "utf-16-be", None

    for encoding in ("utf-8", "utf-16"):
        try:
            return raw.decode(encoding), encoding, None
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)", None


def load_json(path, max_bytes=MAX_FILE_BYTES):
    """Tolerant JSON load.  Returns ``(obj_or_none, errors)``.

    A parse failure is reported and a balanced outermost fragment is
    salvaged when possible; otherwise the caller receives ``None``.
    """
    text, _enc, err = read_text_safe(path, max_bytes)
    if err:
        return None, [err]
    if text is None:
        return None, ["no text read from %s" % path]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        salvaged = _salvage_json(text)
        if salvaged is not None:
            return salvaged, ["json parse failed (%s); salvaged fragment" % exc]
        return None, ["json parse failed: %s" % exc]


def _salvage_json(text):
    """Try to recover the outermost balanced ``{...}`` or ``[...]`` span."""
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        end = -1
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
        if end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """Scan an input tree; return ``(case_path, task_path, materials,
    errors, degradations)``.

    No fixed filenames are assumed beyond the documented ``case.json`` and
    ``task.md``; every other file is treated as material.  Oversized files
    are skipped and reported in ``degradations``.
    """
    errors, degradations = [], []
    root = Path(input_dir)
    if not root.exists():
        return None, None, [], ["input dir missing: %s" % root], []

    case, task = None, None
    materials = []
    total_bytes = 0

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError as exc:
            errors.append("stat failed for %s: %s" % (p, exc))
            continue
        total_bytes += size
        if size > MAX_FILE_BYTES:
            degradations.append(
                "oversized file skipped (%d bytes > %d): %s"
                % (size, MAX_FILE_BYTES, p)
            )
            continue
        name = p.name.lower()
        if name == "case.json":
            if case is None:
                case = p
            else:
                degradations.append("multiple case.json; keeping first: %s" % p)
        elif name == "task.md":
            if task is None:
                task = p
            else:
                degradations.append("multiple task.md; keeping first: %s" % p)
        else:
            materials.append(p)

    if total_bytes > MAX_TOTAL_BYTES:
        degradations.append(
            "aggregate input size %d exceeds %d bytes; consider trimming"
            % (total_bytes, MAX_TOTAL_BYTES)
        )
    if case is None and task is None:
        errors.append("no case.json or task.md found under %s" % root)
    return case, task, materials, errors, degradations


# ---------------------------------------------------------------------------
# Output contract / case id extraction
# ---------------------------------------------------------------------------

def _contract_from_value(value):
    if isinstance(value, dict):
        keys = list(value.keys())
        return keys if keys else None
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.extend(list(item.keys()))
        return out if out else None
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return None


def find_output_contract(obj):
    """Return a list of required field names, or None when absent."""
    if isinstance(obj, dict):
        for key in _CONTRACT_KEYS:
            found = _contract_from_value(obj.get(key))
            if found:
                return found
        for key in ("task", "spec", "input", "case"):
            found = find_output_contract(obj.get(key))
            if found:
                return found
    return None


def extract_case_id(obj, task_text=None):
    """Best-effort case id from the case object or task text."""
    def rec(node):
        if isinstance(node, dict):
            for key in _CASE_ID_HINTS:
                if key in node and node[key] not in (None, ""):
                    return str(node[key])
            for key in ("task", "spec", "input", "case"):
                found = rec(node.get(key))
                if found:
                    return found
        return None

    cid = rec(obj)
    if not cid and task_text:
        match = _CASE_ID_RE.search(task_text)
        if match:
            cid = match.group(1) or match.group(2)
    return cid or "unknown-case"


# ---------------------------------------------------------------------------
# Normalization / canonical keys
# ---------------------------------------------------------------------------

def normalize_doi(value):
    """Normalize a DOI: lowercase, strip URL/prefix, strip trailing punct."""
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    s = s.strip().rstrip(".,;:")
    s = s.lower()
    if not s or "/" not in s:
        return None
    return s


_ARXIV_RE = re.compile(
    r"(?:arxiv\s*:\s*)?"
    r"(?:https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)?"
    r"((?:[a-z-]+(?:\.[A-Z]{2})?/)?\d{4}\.\d{4,5}|\d{4}\.\d{4,5})"
    r"(?:v\d+)?",
    re.I,
)


def normalize_arxiv(value):
    """Normalize an arXiv id, dropping the version suffix for identity."""
    if value is None:
        return None
    match = _ARXIV_RE.search(str(value).strip())
    if not match:
        return None
    return re.sub(r"v\d+$", "", match.group(1)).lower().strip()


def normalize_title(value):
    """Normalize a title: strip tags, fold accents, lowercase, collapse."""
    if value is None:
        return None
    s = str(value)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^0-9a-z\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def canonical_keys(record):
    """Return ``[(kind, key), ...]`` for a record, DOI first."""
    keys = []
    doi = normalize_doi(record.get("doi") or record.get("DOI"))
    if doi:
        keys.append(("doi", doi))
    arxiv = normalize_arxiv(
        record.get("arxiv")
        or record.get("arxiv_id")
        or record.get("eprint")
        or record.get("url", "")
    )
    if arxiv:
        keys.append(("arxiv", arxiv))
    title = normalize_title(record.get("title"))
    if title:
        keys.append(("title", title))
    return keys


def canonical_id(record):
    """Return a stable readable id: DOI > arXiv > title-hash."""
    keys = canonical_keys(record)
    if not keys:
        return None
    for kind, key in keys:
        if kind == "doi":
            return "doi:" + key
    for kind, key in keys:
        if kind == "arxiv":
            return "arxiv:" + key
    for kind, key in keys:
        if kind == "title":
            return "title:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return None


# ---------------------------------------------------------------------------
# Dedup / version chains
# ---------------------------------------------------------------------------

def dedup_records(records):
    """Cluster records by any shared canonical key (union-find) and classify
    version chains.  Returns a deterministic dedup object.
    """
    records = list(records or [])
    n = len(records)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    key_index = {}
    for i, rec in enumerate(records):
        rec = rec if isinstance(rec, dict) else {}
        for kind, key in canonical_keys(rec):
            key_index.setdefault((kind, key), []).append(i)

    for indices in key_index.values():
        first = indices[0]
        for j in indices[1:]:
            union(first, j)

    groups = {}
    for i, rec in enumerate(records):
        groups.setdefault(find(i), []).append(rec)

    clusters = []
    orphans = []
    for i in sorted(groups):
        members = groups[i]
        cid = None
        for member in members:
            cid = canonical_id(member) or cid
        if cid is None or cid.startswith("title:"):
            # still group by title-hash; only truly keyless records orphan
            pass
        summary = _classify_group(members)
        cluster = {
            "canonical_id": cid or ("cluster:%d" % i),
            "member_count": len(members),
            "flags": summary["flags"],
            "representative": summary["representative"],
            "members": summary["members"],
        }
        if cid is None:
            orphans.append(cluster)
        else:
            clusters.append(cluster)

    key_types = {}
    for kind, _key in key_index:
        key_types[kind] = key_types.get(kind, 0) + 1

    return {
        "records_in": n,
        "clusters": sorted(clusters, key=lambda c: c["canonical_id"]),
        "orphans": sorted(orphans, key=lambda c: c["canonical_id"]),
        "duplicates_removed": max(0, n - len(groups)),
        "key_types": key_types,
    }


def _classify_group(members):
    flags = {
        "retraction": False,
        "correction": False,
        "preprint": False,
        "formal": False,
    }
    summaries = []
    best = None
    best_rank = -1

    for rec in members:
        rec = rec if isinstance(rec, dict) else {}
        title = str(rec.get("title") or "")
        typ = str(rec.get("type") or rec.get("publication_type") or "")
        journal = rec.get("journal") or rec.get("container_title")
        text = " ".join([title, typ, str(journal or "")])
        if _RETRACT_RE.search(text):
            flags["retraction"] = True
        if _CORRECT_RE.search(text):
            flags["correction"] = True
        is_pre = bool(_PREPRINT_RE.search(text)) and not journal
        if is_pre:
            flags["preprint"] = True
        elif rec.get("doi") or journal:
            flags["formal"] = True

        rank = 0
        if not flags["retraction"] and not flags["correction"]:
            rank += 1
        if rec.get("doi"):
            rank += 2
        if journal:
            rank += 2
        if is_pre:
            rank -= 1
        if rank > best_rank:
            best_rank = rank
            best = rec

        summaries.append(
            {
                "title": rec.get("title"),
                "doi": normalize_doi(rec.get("doi")),
                "arxiv": normalize_arxiv(rec.get("arxiv") or rec.get("eprint")),
                "journal": journal,
                "year": rec.get("year") or rec.get("published_year"),
                "type": typ or None,
                "url": rec.get("url") or rec.get("link"),
                "source": rec.get("source") or rec.get("provider"),
            }
        )

    if best is None:
        best = members[0] if members else {}

    return {
        "flags": flags,
        "representative": _summarize(best),
        "members": summaries,
    }


def _summarize(rec):
    rec = rec if isinstance(rec, dict) else {}
    doi = normalize_doi(rec.get("doi"))
    arxiv = normalize_arxiv(rec.get("arxiv") or rec.get("eprint"))
    title = rec.get("title")
    return {
        "title": title,
        "doi": doi,
        "arxiv": arxiv,
        "journal": rec.get("journal") or rec.get("container_title"),
        "authors": rec.get("authors") or rec.get("author"),
        "year": rec.get("year") or rec.get("published_year"),
        "date": rec.get("date") or rec.get("published_date"),
        "type": rec.get("type") or rec.get("publication_type"),
        "url": rec.get("url") or rec.get("link"),
        "source": rec.get("source") or rec.get("provider"),
    }


# ---------------------------------------------------------------------------
# Skeleton / inventory / finalize
# ---------------------------------------------------------------------------

def _status_from_errors(errors, degradations):
    malformed = any(
        "json parse" in e or "read failed" in e or "salvaged" in e
        for e in (errors or [])
    )
    if malformed:
        return "input_error"
    if errors:
        return "insufficient_evidence"
    if degradations:
        return "partial"
    return "partial"


def make_skeleton(case_id, contract_keys, status="partial"):
    result = {"case_id": case_id}
    for key in contract_keys:
        if key in DEFAULT_CONTRACT_DEFAULTS:
            result[key] = json.loads(json.dumps(DEFAULT_CONTRACT_DEFAULTS[key]))
        else:
            result[key] = None
    for key in META_FIELDS:
        if key == "status":
            result[key] = status
        elif key == "runtime":
            result[key] = None
        else:
            result[key] = []
    return result


def write_json(path, obj):
    """Deterministic JSON write (sorted keys, compact separators, UTF-8)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        obj, ensure_ascii=False, indent=2, sort_keys=True
    )
    p.write_text(text + "\n", encoding="utf-8")
    return str(p)


def extract_case(input_dir):
    """Return ``(case_id, contract_keys, data, errors, degradations)``."""
    case_path, task_path, materials, errors, degradations = discover_input(
        input_dir
    )
    case_obj, task_text = None, None
    if case_path is not None:
        case_obj, errs = load_json(str(case_path))
        errors.extend(errs)
    if task_path is not None:
        task_text, _enc, err = read_text_safe(str(task_path))
        if err:
            errors.append("task.md read failed: %s" % err)
        elif task_text is None:
            errors.append("task.md empty: %s" % task_path)

    case_id = extract_case_id(case_obj, task_text)
    contract = find_output_contract(case_obj)
    if contract is None:
        contract = list(DEFAULT_CONTRACT_KEYS)

    data = {
        "case_obj": case_obj,
        "task_text": task_text,
        "case_path": str(case_path) if case_path else None,
        "task_path": str(task_path) if task_path else None,
        "materials": [str(m) for m in materials],
    }
    return case_id, contract, data, errors, degradations


def scaffold(input_dir, output_dir, work_dir):
    """Write an honest partial result skeleton early.  Returns (code, result)."""
    errors, degradations = [], []
    try:
        case_id, contract, data, errors, degradations = extract_case(input_dir)
    except Exception as exc:  # never let scaffold crash
        case_id, contract, data = "unknown-case", list(DEFAULT_CONTRACT_KEYS), {}
        errors.append("extract_case crashed: %s" % exc)

    status = _status_from_errors(errors, degradations)
    result = make_skeleton(case_id, contract, status=status)
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = {
        "started_at": utcnow(),
        "elapsed_seconds": None,
    }
    result["change_log"] = [
        {
            "event": "skeleton_written",
            "status": status,
            "time": utcnow(),
            "contract_keys": contract,
        }
    ]

    try:
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        write_json(os.path.join(output_dir, "result.json"), result)
        inv = {
            "case_id": case_id,
            "contract_keys": contract,
            "case_path": data.get("case_path"),
            "task_path": data.get("task_path"),
            "materials": data.get("materials", []),
            "errors": errors,
            "degradations": degradations,
            "inventoried_at": utcnow(),
        }
        write_json(os.path.join(work_dir, "inventory.json"), inv)
    except OSError as exc:
        # Last-resort minimal valid product.
        minimal = {
            "case_id": case_id,
            "status": "input_error",
            "errors": ["write failed: %s" % exc],
        }
        try:
            write_json(os.path.join(output_dir, "result.json"), minimal)
        except OSError:
            pass
        return 2, minimal

    return 0, result


def build_inventory(input_dir, work_dir):
    """Deterministic material inventory for the Agent.  Returns (inv, e, d)."""
    case_id, contract, data, errors, degradations = extract_case(input_dir)
    inv = {
        "case_id": case_id,
        "contract_keys": contract,
        "case_path": data.get("case_path"),
        "task_path": data.get("task_path"),
        "materials": data.get("materials", []),
        "errors": errors,
        "degradations": degradations,
        "inventoried_at": utcnow(),
    }
    try:
        write_json(os.path.join(work_dir, "inventory.json"), inv)
    except OSError as exc:
        degradations.append("inventory write failed: %s" % exc)
    return inv, errors, degradations


def load_records(path):
    """Load a records JSON (list of dicts).  Returns (records, errors)."""
    obj, errors = load_json(path)
    if obj is None:
        return [], errors
    if isinstance(obj, dict):
        for key in ("records", "search_results", "items"):
            if isinstance(obj.get(key), list):
                return obj[key], errors
        return [], errors + ["records file is not a list: %s" % path]
    if isinstance(obj, list):
        return obj, errors
    return [], errors + ["records file is not a list: %s" % path]


def _merge_list(base, extra):
    if isinstance(extra, list):
        seen = set()
        out = []
        for item in list(base) + list(extra):
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out
    return base


def finalize(input_dir, output_dir, work_dir, findings_path):
    """Merge agent findings over the skeleton and write result.json + report.md.

    Returns ``(code, result)``.
    """
    errors, degradations = [], []

    # Re-discover input (deterministic; matches scaffold).
    try:
        case_id, contract, data, errors, degradations = extract_case(input_dir)
    except Exception as exc:
        case_id, contract, data = "unknown-case", list(DEFAULT_CONTRACT_KEYS), {}
        errors.append("extract_case crashed: %s" % exc)

    # Preserve the early skeleton's change_log when present.
    prior_log = []
    prior_path = os.path.join(output_dir, "result.json")
    if os.path.exists(prior_path):
        try:
            prior, _errs = load_json(prior_path)
            if isinstance(prior, dict) and isinstance(prior.get("change_log"), list):
                prior_log = prior["change_log"]
        except Exception:
            prior_log = []

    # Load findings (optional).
    findings = {}
    findings_errors = []
    if findings_path and os.path.exists(findings_path):
        findings, findings_errors = load_json(findings_path)
        if findings is None:
            findings = {}
        errors.extend(findings_errors)

    result = make_skeleton(case_id, contract, status="partial")

    # Fill contract keys from findings (or leave skeleton defaults).
    for key in contract:
        if key in findings and findings[key] is not None:
            result[key] = findings[key]

    # Meta fields.
    declared_status = findings.get("status")
    if declared_status in ("ok", "partial", "insufficient_evidence", "input_error"):
        status = declared_status
    elif errors or findings_errors:
        status = _status_from_errors(errors, degradations)
    else:
        status = "ok" if (findings.get("search_results") or findings.get("new_items")) else "partial"

    result["status"] = status
    result["sources"] = _merge_list(findings.get("sources") or [], [])
    result["uncertainty"] = _merge_list(findings.get("uncertainty") or [], [])
    result["pending_human"] = _merge_list(findings.get("pending_human") or [], [])
    result["errors"] = _merge_list(errors, findings.get("errors") or [])
    result["degradations"] = _merge_list(degradations, findings.get("degradations") or [])
    result["runtime"] = findings.get("runtime") or {
        "started_at": prior_log[0]["time"] if prior_log else utcnow(),
        "elapsed_seconds": None,
        "finished_at": utcnow(),
    }

    change_log = list(prior_log)
    change_log.append(
        {
            "event": "finalized",
            "status": status,
            "time": utcnow(),
            "findings_path": findings_path,
            "contract_keys": contract,
        }
    )
    result["change_log"] = change_log

    try:
        write_json(os.path.join(output_dir, "result.json"), result)
        report = build_report(result)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(os.path.join(output_dir, "report.md")).write_text(
            report, encoding="utf-8"
        )
    except OSError as exc:
        minimal = {
            "case_id": case_id,
            "status": "input_error",
            "errors": ["write failed: %s" % exc],
        }
        try:
            write_json(os.path.join(output_dir, "result.json"), minimal)
        except OSError:
            pass
        return 2, minimal

    return 0, result


def build_report(result):
    """Render a human-readable report.md consistent with result.json."""
    lines = [
        "# 文献发现与追踪结果报告",
        "",
        "本报告由 `scripts/main.py finalize` 确定性生成，与 `result.json` 一致。",
        "",
        "## 1. 运行状态",
        "",
        "- case_id：`%s`" % (result.get("case_id") or "unknown-case"),
        "- status：`%s`" % (result.get("status") or "partial"),
    ]
    runtime = result.get("runtime")
    if isinstance(runtime, dict):
        lines.append(
            "- 开始/结束：`%s` / `%s`"
            % (runtime.get("started_at"), runtime.get("finished_at"))
        )
    lines += [
        "",
        "## 2. 主结果字段",
        "",
    ]
    for key in DEFAULT_CONTRACT_KEYS:
        value = result.get(key)
        rendered = _render_value(value)
        lines.append("### %s" % key)
        lines.append("")
        lines.append(rendered)
        lines.append("")

    lines += [
        "## 3. 证据与来源",
        "",
    ]
    for item in result.get("evidence") or []:
        if isinstance(item, dict):
            lines.append(
                "- locator：`%s`　quote：`%s`"
                % (item.get("locator") or item.get("source") or "?", item.get("quote") or "")
            )
        else:
            lines.append("- %s" % item)
    lines += [
        "",
        "## 4. 不确定性 / 冲突",
        "",
    ]
    for item in result.get("uncertainty") or []:
        lines.append("- %s" % (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
    lines += [
        "",
        "## 5. 变更记录",
        "",
    ]
    for item in result.get("change_log") or []:
        if isinstance(item, dict):
            lines.append("- %s：%s（%s）" % (item.get("time"), item.get("event"), item.get("status")))
        else:
            lines.append("- %s" % item)
    lines += [
        "",
        "## 6. 错误 / 降级 / 待人工",
        "",
        "- errors：%s" % (len(result.get("errors") or [])),
        "- degradations：%s" % (len(result.get("degradations") or [])),
        "- pending_human：%s" % (len(result.get("pending_human") or [])),
    ]
    for item in (result.get("errors") or [])[:20]:
        lines.append("  - [error] %s" % item)
    for item in (result.get("degradations") or [])[:20]:
        lines.append("  - [degradation] %s" % item)
    for item in (result.get("pending_human") or [])[:20]:
        lines.append("  - [pending] %s" % (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
    return "\n".join(lines) + "\n"


def _render_value(value):
    if value is None:
        return "_（无 / null）_"
    if isinstance(value, (list, dict)):
        return "```json\n%s\n```" % json.dumps(value, ensure_ascii=False, indent=2)
    return "`%s`" % value
