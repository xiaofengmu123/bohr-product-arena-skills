#!/usr/bin/env python3
"""core_impl.py — deterministic helpers for the deep-research skill.

Standard library only. Imported by scripts/main.py and scripts/selftest.py.
The module is import-safe with `python3 -B scripts/core_impl.py` and does not
depend on the current working directory.
"""

import hashlib
import json
import os
import re
import subprocess
import time

# --- status vocabulary -------------------------------------------------------
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

# --- input discovery ---------------------------------------------------------
TEXT_EXTS = {
    ".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".html",
    ".htm", ".tex", ".bib", ".rst", ".yaml", ".yml", ".log", ".nfo",
}
BINARY_EXTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif",
    ".tiff", ".svg", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
}
MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB per text file before truncation

DEFAULT_CONTRACT = {
    "case_id": "",
    "status": STATUS_PARTIAL,
    "research_product": {
        "type": "synthesis",
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


# --- time --------------------------------------------------------------------
def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- filesystem --------------------------------------------------------------
def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def _iter_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            abs_path = os.path.join(dirpath, fn)
            out.append(abs_path)
    out.sort()
    return out


def _rel(root, abs_path):
    try:
        return os.path.relpath(abs_path, root).replace(os.sep, "/")
    except ValueError:
        return abs_path.replace(os.sep, "/")


def discover_input(input_dir):
    """Return an inventory dict; never raises on missing/empty input."""
    inv = {
        "exists": bool(input_dir) and os.path.isdir(input_dir),
        "input_dir": input_dir,
        "total_files": 0,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "other": [],
        "by_ext": {},
        "errors": [],
    }
    if not inv["exists"]:
        inv["errors"].append("input directory does not exist: %s" % input_dir)
        return inv

    for abs_path in _iter_files(input_dir):
        name = os.path.basename(abs_path).lower()
        rel = _rel(input_dir, abs_path)
        ext = os.path.splitext(name)[1].lower()
        size = 0
        try:
            size = os.path.getsize(abs_path)
        except OSError as exc:  # pragma: no cover
            inv["errors"].append("stat failed %s: %s" % (rel, exc))
            continue
        inv["total_files"] += 1
        inv["by_ext"].setdefault(ext, []).append(rel)
        if name == "case.json" and inv["case_json"] is None:
            inv["case_json"] = abs_path
        elif name == "task.md" and inv["task_md"] is None:
            inv["task_md"] = abs_path
        elif ext in TEXT_EXTS or ext in BINARY_EXTS:
            inv["materials"].append({
                "path": rel, "abs": abs_path, "ext": ext, "size": size,
            })
        else:
            inv["other"].append({"path": rel, "abs": abs_path, "ext": ext, "size": size})
    return inv


# --- text & JSON loading (encoding-tolerant) ---------------------------------
def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Return (text, errors, degradations). Falls back through encodings."""
    errs, degs = [], []
    if not path or not os.path.isfile(path):
        return "", ["missing file: %s" % path], degs
    raw = b""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
            degs.append("truncated to %d bytes: %s" % (max_bytes, path))
    except OSError as exc:
        return "", ["read failed %s: %s" % (path, exc)], degs

    text = None
    last_err = None
    for enc in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError) as exc:
            last_err = exc
    if text is None:
        text = raw.decode("latin-1", errors="replace")
        errs.append("decode failed %s: %s (used latin-1 replace)" % (path, last_err))
    return text, errs, degs


def load_json(path, max_bytes=MAX_TEXT_BYTES):
    """Return (obj, errors, degradations). Non-JSON/absent yields (None, ...)."""
    text, errs, degs = read_text(path, max_bytes)
    if errs:
        return None, errs, degs
    if not text.strip():
        return None, ["empty file: %s" % path], degs
    try:
        return json.loads(text), errs, degs
    except ValueError as exc:
        return None, ["invalid JSON %s: %s" % (path, exc)], degs


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return []
    except OSError as exc:
        return ["write failed %s: %s" % (path, exc)]


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return []
    except OSError as exc:
        return ["write failed %s: %s" % (path, exc)]


# --- contract handling -------------------------------------------------------
def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId"):
        val = case_obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(val)
    return ""


def _template_from_schema(schema, depth=0):
    """Turn an output_contract schema into a null/[] placeholder template."""
    if depth > 6:
        return None
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            out[k] = _template_from_schema(v, depth + 1)
        return out
    if isinstance(schema, list):
        return []
    return None


def build_contract(case_obj):
    """Return the required result field template (contract fields + core fields)."""
    contract = {}
    if isinstance(case_obj, dict):
        for key in ("output_contract", "output_schema", "result_schema", "outputContract"):
            if isinstance(case_obj.get(key), dict):
                contract = case_obj[key]
                break
    template = _template_from_schema(contract) or {}
    return deep_merge(DEFAULT_CONTRACT, template)


def new_skeleton(case_id, contract, note=""):
    result = deep_merge(DEFAULT_CONTRACT, contract or {})
    result["case_id"] = case_id or ""
    result["status"] = STATUS_PARTIAL
    result["change_log"] = [{
        "ts": iso_now(), "action": "skeleton",
        "detail": note or "skeleton before research begins",
    }]
    return result


# --- merging -----------------------------------------------------------------
def deep_merge(base, extra):
    """Recursive dict merge; lists and scalars in extra replace base values."""
    if not isinstance(base, dict) or not isinstance(extra, dict):
        return extra
    out = dict(base)
    for key, val in extra.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


# --- quote / locator verification -------------------------------------------
def normalize(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def sha256(text):
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:16]


def pdftotext_extract(path):
    """Best-effort PDF text extraction via pdftotext; returns (text, error)."""
    for binary in ("pdftotext",):
        try:
            proc = subprocess.run(
                [binary, path, "-"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=60,
            )
            if proc.returncode == 0:
                try:
                    return proc.stdout.decode("utf-8", errors="replace"), None
                except Exception as exc:  # pragma: no cover
                    return "", str(exc)
            return "", (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        except FileNotFoundError:
            return "", "pdftotext not available"
        except Exception as exc:  # pragma: no cover
            return "", str(exc)
    return "", "no extractor"


def load_source_text(input_dir, source):
    """Return (text, kind, error) for a source path in the evidence matrix."""
    if not source:
        return "", "missing", "empty source"
    path = source if os.path.isabs(source) else os.path.join(input_dir, source)
    if not os.path.isfile(path):
        return "", "missing", "source file not found: %s" % source
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        text, err = pdftotext_extract(path)
        return text, "pdf", err
    if ext in TEXT_EXTS:
        text, errs, _degs = read_text(path)
        return text, "text", (errs[0] if errs else None)
    return "", "binary", "binary source not auto-extracted: %s" % source


def verify_quote(input_dir, evidence):
    """Verify one evidence row's quote appears verbatim in its source.

    Returns {"status": "ok"|"not_found"|"unverifiable", "detail": str}.
    """
    quote = evidence.get("quote", "")
    source = evidence.get("source", "")
    if not quote or not source:
        return {"status": "unverifiable", "detail": "missing quote or source"}
    text, _kind, err = load_source_text(input_dir, source)
    if err:
        return {"status": "unverifiable", "detail": err}
    if normalize(quote) in normalize(text):
        return {"status": "ok", "detail": "verbatim match"}
    return {"status": "not_found", "detail": "quote not found in source text"}


# --- validation --------------------------------------------------------------
def validate_result(result, input_dir, work_dir=None):
    """Return a list of check records: {id, label, pass, detail}."""
    checks = []
    result = result if isinstance(result, dict) else {}

    def add(cid, label, ok, detail):
        checks.append({"id": cid, "label": label, "pass": bool(ok), "detail": detail})

    add("V1", "result.json 可解析为对象", isinstance(result, dict), "")
    add("V2", "case_id 非空", bool(result.get("case_id")), str(result.get("case_id")))
    add("V3", "status 在合法枚举内", result.get("status") in STATUSES,
        str(result.get("status")))

    rp = result.get("research_product") if isinstance(result.get("research_product"), dict) else {}
    concl = rp.get("conclusions") if isinstance(rp.get("conclusions"), list) else []
    ev = result.get("evidence_matrix") if isinstance(result.get("evidence_matrix"), list) else []
    ev_ids = {e.get("id") for e in ev if isinstance(e, dict) and e.get("id")}

    add("V4", "conclusions 非空", bool(concl), "%d conclusions" % len(concl))

    unlinked = []
    for c in concl:
        cid = c.get("id")
        ids = c.get("evidence_ids") or []
        if not ids or not any(i in ev_ids for i in ids):
            unlinked.append(cid)
    add("V5", "每条结论 evidence_ids 均命中证据矩阵", not unlinked,
        "unlinked: %s" % (", ".join(map(str, unlinked)) if unlinked else "none"))

    missing_loc = []
    for e in ev:
        loc = e.get("locator")
        if not e.get("quote") or not isinstance(loc, dict) or not (
                loc.get("unit_id") or loc.get("unit_type") or loc.get("source")):
            missing_loc.append(e.get("id"))
    add("V6", "每条证据含 locator + 逐字 quote", not missing_loc,
        "missing: %s" % (", ".join(map(str, missing_loc)) if missing_loc else "none"))

    uc = result.get("uncertainty") if isinstance(result.get("uncertainty"), dict) else {}
    conflicts = uc.get("conflicts") if isinstance(uc.get("conflicts"), list) else []
    bad_conf = []
    for cf in conflicts:
        if not (cf.get("source_a") and cf.get("source_b")):
            bad_conf.append(cf)
    add("V7", "冲突证据逐条含两个来源", not bad_conf,
        "%d conflicts" % len(conflicts))

    gaps = uc.get("gaps") or []
    ph = result.get("pending_human") or []
    add("V8", "研究空白已显式写出", bool(gaps or ph),
        "%d gaps, %d pending_human" % (len(gaps), len(ph)))

    add("V9", "sources 非空", bool(result.get("sources")), "%d sources" %
        len(result.get("sources") or []))
    add("V10", "change_log 非空", bool(result.get("change_log")),
        "%d entries" % len(result.get("change_log") or []))

    verified = ok = 0
    not_found = []
    for e in ev:
        if not e.get("quote"):
            continue
        res = verify_quote(input_dir, e)
        if res["status"] == "ok":
            ok += 1
        elif res["status"] == "not_found":
            not_found.append(e.get("id"))
        verified += 1
    add("V11", "逐字 quote 可回源", not not_found,
        "%d/%d verified%s" % (ok, verified,
                              (", not_found: %s" % ", ".join(map(str, not_found))) if not_found else ""))

    # decomposition / search plan presence (if work dir given)
    if work_dir:
        decomp = os.path.join(work_dir, "decomposition.md")
        sq_lines = 0
        if os.path.isfile(decomp):
            try:
                with open(decomp, "r", encoding="utf-8") as fh:
                    sq_lines = sum(1 for line in fh if re.match(r"\s*[-*]?\s*SQ\d+", line))
            except OSError:
                sq_lines = 0
        add("V12", "decomposition.md 含 >=2 个可检索子问题", sq_lines >= 2,
            "%d SQ lines" % sq_lines)
    return checks


def build_evidence_map(result, input_dir):
    """Build the fine-grained provenance map (variant focus)."""
    ev = result.get("evidence_matrix") if isinstance(result.get("evidence_matrix"), list) else []
    rp = result.get("research_product") if isinstance(result.get("research_product"), dict) else {}
    concl = rp.get("conclusions") if isinstance(rp.get("conclusions"), list) else []

    rows = []
    by_sq = {}
    by_source = {}
    total = verified_ok = 0
    failed = []

    for e in ev:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        quote = e.get("quote") or ""
        source = e.get("source") or ""
        total += 1
        check = verify_quote(input_dir, e)
        is_ok = check["status"] == "ok"
        if is_ok:
            verified_ok += 1
        else:
            failed.append({"evidence_id": eid, "status": check["status"],
                           "detail": check["detail"]})

        row = {
            "evidence_id": eid,
            "subquestion": e.get("subquestion"),
            "source": source,
            "locator": e.get("locator") or {},
            "quote": quote,
            "quote_hash": e.get("quote_hash") or sha256(quote),
            "claim": e.get("claim"),
            "judgment": e.get("judgment"),
            "reason": e.get("reason"),
            "conclusion_ids": e.get("conclusion_ids") or [],
            "verified": is_ok,
        }
        rows.append(row)
        by_sq.setdefault(e.get("subquestion") or "unassigned", []).append(eid)
        by_source.setdefault(source or "unassigned", []).append(eid)

    conclusion_index = []
    for c in concl:
        cid = c.get("id")
        linked = [r for r in rows if cid in r["conclusion_ids"]]
        conclusion_index.append({
            "conclusion_id": cid,
            "text": c.get("text"),
            "strength": c.get("strength"),
            "evidence_ids": c.get("evidence_ids") or [],
            "evidence": [{
                "evidence_id": r["evidence_id"],
                "locator": r["locator"],
                "quote": r["quote"],
                "quote_hash": r["quote_hash"],
                "verified": r["verified"],
            } for r in linked],
        })

    return {
        "case_id": result.get("case_id"),
        "generated_at": iso_now(),
        "schema_version": "2.0-evidence",
        "quote_integrity": {
            "total": total,
            "verified": verified_ok,
            "failed": failed,
        },
        "conclusion_index": conclusion_index,
        "evidence_by_subquestion": by_sq,
        "sources_index": [{
            "source": src, "evidence_ids": ids,
        } for src, ids in sorted(by_source.items())],
    }
