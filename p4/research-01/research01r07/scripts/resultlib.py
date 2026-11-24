#!/usr/bin/env python3
"""resultlib: robustness-first core for the deep-research skill (stdlib only).

Design invariants (robustness variant):
  1. Never raise for malformed input, missing files, bad encoding, or write
     failure. Public read/write functions return ``(value, errors, degradations)``.
  2. Atomic writes via temp file + os.replace: an interrupted run can never leave
     a corrupt ``result.json``.
  3. Deterministic layout: sorted keys, sorted file lists, stable IDs. Repeated
     runs give the same structure (timestamps are real run records).
  4. Size-capped reads: oversized text files are truncated and recorded in
     ``degradations`` instead of being skipped silently or crashing.
  5. Idempotent merge: re-applying the same payload does not duplicate entries.
"""

import copy
import json
import os
import stat
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

VALID_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

DEFAULT_TEXT_MAX = 2 * 1024 * 1024          # 2 MB per text file
MAX_RESULT_BYTES = 16 * 1024 * 1024         # refuse to write a gigantic result blob

TEXT_EXTS = {".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html", ".htm",
             ".tex", ".bib", ".rst", ".yaml", ".yml", ".log", ".tsv"}
BINARY_EXTS = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg",
               ".gif", ".webp", ".svg", ".zip", ".gz", ".tar", ".tif", ".tiff",
               ".doc", ".xls", ".ppt"}

CONTRACT_KEYS = ("output_contract", "output_schema", "result_schema",
                 "response_schema", "contract", "result_contract")

# Fallback contract: the fields the platform requires even when case.json does
# not carry an explicit output_contract. "Missing value -> null / []" rule.
DEFAULT_CONTRACT = {
    "research_product": None,
    "evidence_matrix": [],
    "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
}

EVIDENCE_JUDGMENTS = ("supports", "partial", "unsupported", "conflicts",
                      "extraction_failed")
CONCLUSION_STRENGTHS = ("established", "inferred", "conjectural")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Filesystem primitives (never raise)
# ---------------------------------------------------------------------------

def ensure_dir(path):
    if not path:
        return []
    try:
        os.makedirs(path, exist_ok=True)
        return []
    except OSError as exc:
        return [{"op": "mkdir", "path": path, "error": str(exc)}]


def read_text(path, max_bytes=None):
    """Read a text file with encoding fallback. Returns (text, errors, degradations)."""
    errors, degradations = [], []
    if max_bytes is None:
        max_bytes = DEFAULT_TEXT_MAX
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", [{"op": "stat", "path": os.path.basename(path), "error": str(exc)}], []
    if size == 0:
        return "", [], []
    truncated = size > max_bytes
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        return "", [{"op": "read", "path": os.path.basename(path), "error": str(exc)}], []
    if len(data) > max_bytes:
        data = data[:max_bytes]
        truncated = True
    if truncated:
        degradations.append({
            "type": "truncated",
            "path": os.path.basename(path),
            "limit_bytes": max_bytes,
            "detail": "text file exceeded size limit; content truncated",
        })
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
        degradations.append({
            "type": "bom",
            "path": os.path.basename(path),
            "detail": "utf-8 BOM stripped",
        })
    # Remove NUL bytes that commonly corrupt otherwise-decodable text.
    data = data.replace(b"\x00", b"")

    text = None
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            text = data.decode(encoding)
            if encoding != "utf-8":
                degradations.append({
                    "type": "encoding",
                    "path": os.path.basename(path),
                    "encoding": encoding,
                    "detail": "decoded with fallback encoding (not valid utf-8)",
                })
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:  # defensive; latin-1 should always succeed
        text = data.decode("latin-1", errors="replace")
        degradations.append({
            "type": "encoding",
            "path": os.path.basename(path),
            "encoding": "latin-1(replace)",
            "detail": "lossy decode applied",
        })
    return text, errors, degradations


def load_json(path, max_bytes=None):
    """Load JSON tolerantly. Returns (obj, errors, degradations). Never raises."""
    text, errors, degradations = read_text(path, max_bytes)
    if errors:
        return None, errors, degradations
    stripped = text.strip()
    if not stripped:
        return None, [{"op": "json", "path": os.path.basename(path),
                       "error": "empty file"}], degradations
    try:
        obj = json.loads(stripped)
        return obj, [], degradations
    except (ValueError, TypeError) as exc:
        return None, [{"op": "json", "path": os.path.basename(path),
                       "error": "unparseable JSON: %s" % exc}], degradations


def write_text(path, text):
    """Atomic text write. Returns list of error records (empty on success)."""
    errors = []
    tmp = None
    try:
        directory = os.path.dirname(path)
        if directory:
            ensure_dir(directory)
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        errors.append({"op": "write", "path": os.path.basename(path), "error": str(exc)})
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return errors


def write_json(path, obj):
    """Serialize and write JSON atomically. Returns error records."""
    try:
        text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError) as exc:
        return [{"op": "json", "path": os.path.basename(path),
                 "error": "serialization failed: %s" % exc}]
    return write_text(path, text + "\n")


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def classify_kind(ext):
    if ext in TEXT_EXTS:
        return "text"
    if ext == ".pdf":
        return "pdf"
    if ext in BINARY_EXTS:
        return "binary"
    return "other"


def discover_input(input_dir):
    """Inventory the input tree. Never raises; embeds errors/degradations."""
    inv = {
        "exists": False,
        "input_dir": os.path.abspath(input_dir),
        "total_files": 0,
        "total_bytes": 0,
        "case_json": None,
        "task_md": None,
        "files": [],
        "errors": [],
        "degradations": [],
    }
    if not os.path.isdir(input_dir):
        inv["errors"].append({"op": "scan", "path": input_dir,
                              "error": "input directory does not exist"})
        return inv
    inv["exists"] = True

    def on_walk_error(exc):
        inv["degradations"].append({"type": "walk_error", "error": str(exc)})

    for root, _dirs, names in os.walk(input_dir, onerror=on_walk_error):
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                mode = os.lstat(path).st_mode
                if not stat.S_ISREG(mode):
                    inv["degradations"].append({
                        "type": "skipped_non_regular", "path": name,
                        "detail": "symlink or special file skipped"})
                    continue
                size = os.path.getsize(path)
            except OSError as exc:
                inv["degradations"].append({
                    "type": "stat_failed", "path": name, "error": str(exc)})
                continue
            rel = os.path.relpath(path, input_dir).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            entry = {"path": rel, "name": name, "ext": ext,
                     "kind": classify_kind(ext), "bytes": size}
            inv["files"].append(entry)
            inv["total_files"] += 1
            inv["total_bytes"] += size
            lowered = name.lower()
            if inv["case_json"] is None and (
                    lowered == "case.json" or
                    (lowered.startswith("case") and lowered.endswith(".json"))):
                inv["case_json"] = path
            if inv["task_md"] is None and (
                    lowered == "task.md" or
                    (lowered.startswith("task") and lowered.endswith(".md"))):
                inv["task_md"] = path
    inv["files"].sort(key=lambda item: item["path"])
    return inv


# ---------------------------------------------------------------------------
# Contract & skeleton
# ---------------------------------------------------------------------------

def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "armi_id", "task_id", "taskId"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def build_contract(case_obj):
    """Extract output_contract from case.json, else fall back to defaults."""
    contract = copy.deepcopy(DEFAULT_CONTRACT)
    if isinstance(case_obj, dict):
        for key in CONTRACT_KEYS:
            value = case_obj.get(key)
            if isinstance(value, dict) and value:
                merged = copy.deepcopy(value)
                for known, default in DEFAULT_CONTRACT.items():
                    merged.setdefault(known, copy.deepcopy(default))
                contract = merged
                break
    return contract


def new_skeleton(case_id, contract, note=""):
    """Produce an early, valid partial result object."""
    result = {
        "case_id": case_id or "",
        "status": STATUS_PARTIAL,
        "research_product": {
            "type": "synthesis",
            "question": "",
            "summary": "",
            "conclusions": [],
        },
        "evidence_matrix": [],
        "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
        "sources": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "runtime_seconds": 0,
    }
    for key, value in (contract or {}).items():
        if key not in result:
            result[key] = copy.deepcopy(value)
    result["change_log"].append({
        "ts": iso_now(), "action": "skeleton", "detail": note or "early skeleton",
    })
    return result


def ensure_contract_fields(result, contract):
    """Restore every contract key missing from the result (null/[] verbatim)."""
    for key, value in (contract or {}).items():
        if key not in result:
            result[key] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
# Merge (idempotent)
# ---------------------------------------------------------------------------

def deep_merge(base, payload):
    """Merge payload into base recursively; change_log lists are appended."""
    if not isinstance(payload, dict) or not isinstance(base, dict):
        return payload
    for key, value in payload.items():
        if key == "change_log" and isinstance(value, list) \
                and isinstance(base.get(key), list):
            base.setdefault(key, []).extend(value)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def append_change_log(result, action, detail):
    """Append a change-log entry unless an identical (action, detail) exists.

    Dedupe is global, not tail-only, so re-running a merge/finalize step with
    the same payload does not grow the log (idempotent replay).
    """
    logs = result.setdefault("change_log", [])
    if any(isinstance(item, dict)
           and item.get("action") == action
           and item.get("detail") == detail for item in logs):
        return result
    logs.append({"ts": iso_now(), "action": action, "detail": detail})
    return result


# ---------------------------------------------------------------------------
# Validation & six-point coverage
# ---------------------------------------------------------------------------

def validate(result):
    """Return a list of problem strings. An empty list means structurally valid."""
    problems = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]
    if not result.get("case_id"):
        problems.append("case_id missing or empty")
    if result.get("status") not in VALID_STATUSES:
        problems.append("invalid status: %r" % result.get("status"))
    for required in ("research_product", "evidence_matrix", "uncertainty",
                     "sources", "change_log"):
        if required not in result:
            problems.append("missing top-level key: %s" % required)
    for row in result.get("evidence_matrix") or []:
        if not isinstance(row, dict):
            problems.append("evidence row is not an object")
            continue
        row_id = row.get("id", "?")
        if not row.get("id"):
            problems.append("evidence row missing id")
        if not row.get("locator"):
            problems.append("evidence %s missing locator" % row_id)
        if not row.get("quote") and row.get("judgment") != "extraction_failed":
            problems.append("evidence %s missing verbatim quote" % row_id)
    conclusions = (result.get("research_product") or {}).get("conclusions") or []
    for item in conclusions:
        if not isinstance(item, dict):
            problems.append("conclusion is not an object")
            continue
        if not item.get("id"):
            problems.append("conclusion missing id")
    return problems


def six_point_check(result, work_dir):
    """Assess the six rubric points. Returns (check_dict, detail_list)."""
    check = {
        "decomposition": False,
        "search_plan": False,
        "evidence_chain": False,
        "conflicts": False,
        "synthesis": False,
        "gaps": False,
    }
    details = []

    dec_text, _, _ = read_text(os.path.join(work_dir, "decomposition.md"))
    if dec_text and dec_text.count("SQ") >= 2:
        check["decomposition"] = True
    else:
        details.append("decomposition.md missing or fewer than 2 subquestions")

    sp_text, _, _ = read_text(os.path.join(work_dir, "search_plan.md"))
    if sp_text and len(sp_text.strip()) >= 40:
        check["search_plan"] = True
    else:
        details.append("search_plan.md missing or too short")

    evidence = result.get("evidence_matrix") or []
    conclusions = (result.get("research_product") or {}).get("conclusions") or []
    evidence_ids = {row.get("id") for row in evidence if isinstance(row, dict)}
    all_quoted = all(isinstance(row, dict) and row.get("quote") and row.get("locator")
                     for row in evidence)
    linked = sum(1 for item in conclusions if isinstance(item, dict)
                 for eid in item.get("evidence_ids") or [] if eid in evidence_ids)
    check["evidence_chain"] = bool(evidence) and all_quoted and linked > 0
    if not check["evidence_chain"]:
        details.append("evidence chain incomplete (locator+quote or conclusion link)")

    conflicts = (result.get("uncertainty") or {}).get("conflicts") or []
    check["conflicts"] = bool(conflicts)
    if not check["conflicts"]:
        details.append("uncertainty.conflicts empty")

    check["synthesis"] = bool(conclusions)
    if not check["synthesis"]:
        details.append("research_product.conclusions empty")

    gaps = (result.get("uncertainty") or {}).get("gaps") or []
    pending = result.get("pending_human") or []
    check["gaps"] = bool(gaps) or bool(pending)
    if not check["gaps"]:
        details.append("uncertainty.gaps / pending_human empty")

    return check, details


# ---------------------------------------------------------------------------
# Renderers for companion artifacts
# ---------------------------------------------------------------------------

def render_report(result):
    """Render a human-readable report.md from the result object."""
    product = result.get("research_product") or {}
    lines = [
        "# 深度研究报告",
        "",
        "- case_id: %s" % result.get("case_id", ""),
        "- status: %s" % result.get("status", ""),
        "- type: %s" % product.get("type", ""),
        "",
        "## 研究问题",
        "",
        product.get("question") or "（未填写）",
        "",
        "## 综合结论",
        "",
        product.get("summary") or "（未填写）",
        "",
        "## 结论明细",
        "",
    ]
    for item in product.get("conclusions") or []:
        lines.append("- [%s] %s（强度：%s；证据：%s）" % (
            item.get("id", ""), item.get("text", ""), item.get("strength", ""),
            ", ".join(item.get("evidence_ids") or [])))
    lines += ["", "## 证据矩阵", "", "| ID | 子问题 | 来源 | 定位 | 判断 | 结论 |",
              "|---|---|---|---|---|---|"]
    for row in result.get("evidence_matrix") or []:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            row.get("id", ""), row.get("subquestion", ""), row.get("source", ""),
            row.get("locator", ""), row.get("judgment", ""),
            ", ".join(row.get("conclusion_ids") or [])))
    lines += ["", "## 不确定性", ""]
    uncertainty = result.get("uncertainty") or {}
    lines.append("- 冲突：%d 条" % len(uncertainty.get("conflicts") or []))
    lines.append("- 偏倚：%d 条" % len(uncertainty.get("biases") or []))
    lines.append("- 空白：%d 条" % len(uncertainty.get("gaps") or []))
    lines.append("- 无法确定：%d 条" % len(uncertainty.get("undetermined") or []))
    lines += ["", "## 缺口与待人工确认", ""]
    for item in result.get("pending_human") or []:
        lines.append("- %s" % item)
    if not result.get("pending_human"):
        lines.append("- （无）")
    lines += ["", "## 降级与错误", ""]
    for item in result.get("degradations") or []:
        lines.append("- 降级：%s" % item)
    for item in result.get("errors") or []:
        lines.append("- 错误：%s" % item)
    if not result.get("degradations") and not result.get("errors"):
        lines.append("- （无）")
    return "\n".join(lines) + "\n"


def render_evidence_map(result):
    """Render the standalone evidence map artifact."""
    return {
        "case_id": result.get("case_id"),
        "evidence": result.get("evidence_matrix") or [],
        "conclusions": (result.get("research_product") or {}).get("conclusions") or [],
        "uncertainty": result.get("uncertainty") or {},
        "pending_human": result.get("pending_human") or [],
        "limitations": result.get("limitations") or [],
    }
