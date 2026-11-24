"""core implementation for the research-selection-audit skill.

Standard-library only.  All paths are passed in explicitly; the CLI in main.py
supplies the Linux runtime defaults (/input, /app/output, /app/work) while the
self-test supplies temporary paths, so the same code runs on both platforms.

Architecture
------------
The pipeline separates *facts* from *claims*:

1. Evidence ledger  -- /app/work/ledger.jsonl (append-only JSON Lines).
   Each line is one verbatim evidence record:
   {id, source_id, locator, quote, facet, status, note}
2. Declarative case spec -- /app/work/spec/*.json.  Each fragment's file name
   maps to a top-level key of result.json (question -> research_question,
   prior -> prior_work, novelty -> novelty, feasibility -> feasibility,
   synthesis -> synthesis, uncertainty -> uncertainty, conflicts -> conflicts,
   meta -> metadata such as status/limitations/pending_human).
3. Single compiler -- assemble().  It merges spec + ledger, then runs a fixed
   verification order: structure -> inventory -> referential integrity ->
   verbatim quote check -> status consistency -> emit.

This makes "可核验性" a mechanical property: a quote that does not appear in
the extracted material is flagged in `validation` and `pending_human` instead
of being silently accepted.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

MAX_FILE_BYTES = 2 * 1024 * 1024

TEXT_EXTS = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".rst", ".tex", ".bib", ".ini", ".cfg", ".log",
}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {
    ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls", ".odt", ".ods", ".odp",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif", ".svg"}

VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CORE_DELIVERABLES = ("research_question", "search_protocol", "prior_work", "novelty", "feasibility")
REF_KEYS = ("evidence", "evidence_ids", "evidence_refs", "evidence_id", "supported_by")

SPEC_KEYS = {
    "question": "research_question",
    "protocol": "search_protocol",
    "prior": "prior_work",
    "novelty": "novelty",
    "feasibility": "feasibility",
    "synthesis": "synthesis",
    "uncertainty": "uncertainty",
    "conflicts": "conflicts",
    "meta": "_meta",
}


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

def utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def norm_ws(s) -> str:
    """Collapse all whitespace so verbatim quotes can be matched robustly."""
    return re.sub(r"\s+", " ", (s or "")).strip()


def read_text(path):
    """Tolerant text read: utf-8-sig -> utf-8 -> latin-1 (never raises on decode)."""
    data = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1"), "latin-1"


def read_json(path):
    """Return (data, error).  Never raises on bad JSON/encoding."""
    try:
        txt, _enc = read_text(path)
    except OSError as e:
        return None, "unreadable: %s" % e
    try:
        return json.loads(txt), None
    except Exception as e:  # json.JSONDecodeError and friends
        return None, "bad json: %s" % e


def write_json(path, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def dedupe(items):
    seen = set()
    out = []
    for it in items:
        key = json.dumps(it, sort_keys=True, ensure_ascii=False, default=str) if isinstance(it, dict) else str(it)
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def ensure_uncertainty(u):
    if not isinstance(u, dict):
        u = {}
    for k in ("bias", "gaps", "unresolved", "limitations"):
        u.setdefault(k, [])
    return u


def norm_rid(s):
    """Normalize evidence ids so E1 / E01 / E001 compare equal."""
    m = re.match(r"^[Ee](\d+)$", (s or "").strip())
    if m:
        return "E" + str(int(m.group(1)))
    return (s or "").strip()


def dir_hash(input_dir) -> str:
    h = hashlib.sha1()
    h.update(os.path.abspath(input_dir).encode("utf-8", "replace"))
    if os.path.isdir(input_dir):
        for root, dirs, files in os.walk(input_dir):
            dirs.sort()
            for f in sorted(files):
                h.update(os.path.join(root, f).encode("utf-8", "replace"))
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------
# material discovery and text extraction
# --------------------------------------------------------------------------

def discover_materials(input_dir):
    items, skipped = [], []
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            try:
                size = os.path.getsize(p)
            except OSError as e:
                skipped.append({"path": p, "reason": "stat error: %s" % e})
                continue
            if size > MAX_FILE_BYTES:
                skipped.append({"path": p, "reason": "size %d > %d" % (size, MAX_FILE_BYTES)})
                continue
            rel = os.path.relpath(p, input_dir).replace(os.sep, "/")
            ext = os.path.splitext(f)[1].lower()
            if ext in PDF_EXTS:
                kind = "pdf"
            elif ext in TEXT_EXTS:
                kind = "text"
            elif ext in OFFICE_EXTS:
                kind = "office"
            elif ext in IMAGE_EXTS:
                kind = "image"
            else:
                kind = "other"
            items.append({"path": p, "rel": rel, "name": f, "ext": ext, "size": size, "kind": kind})
    return items, skipped


def which_pdftotext():
    return shutil.which("pdftotext")


def extract_text(m, text_dir, degradations) -> None:
    """Fill m['extracted'] and m['text_file']; never raises."""
    sid = m["source_id"]
    dst = os.path.join(text_dir, sid + ".txt")
    kind = m["kind"]
    if kind in ("text", "other"):
        try:
            txt, enc = read_text(m["path"])
            if kind == "other" and enc == "latin-1":
                degradations.append({"what": "non_utf8_text", "detail": m["rel"] + " decoded with latin-1 fallback"})
            with open(dst, "w", encoding="utf-8") as f:
                f.write(txt)
            m["extracted"] = True
            m["encoding"] = enc
            m["text_file"] = dst
        except OSError as e:
            degradations.append({"what": "read_failed", "detail": m["rel"] + ": " + str(e)})
            m["extracted"] = False
        return
    if kind == "pdf":
        tool = which_pdftotext()
        if not tool:
            degradations.append({"what": "pdftotext_unavailable", "detail": "cannot extract " + m["rel"]})
            m["extracted"] = False
            return
        try:
            r = subprocess.run([tool, "-layout", m["path"], dst], capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
                m["extracted"] = True
                m["encoding"] = "utf-8"
                m["text_file"] = dst
            else:
                degradations.append({"what": "pdf_extract_failed", "detail": (r.stderr or "").strip()[:200]})
                m["extracted"] = False
        except Exception as e:  # pragma: no cover - defensive
            degradations.append({"what": "pdf_extract_failed", "detail": str(e)})
            m["extracted"] = False
        return
    # office / image: needs a converter or OCR; honest degradation instead of guessing
    m["extracted"] = False
    degradations.append({"what": "text_unavailable", "detail": m["rel"] + " (" + kind + ")"})


def make_source(m, work_dir):
    tf = m.get("text_file")
    rel_tf = os.path.relpath(tf, work_dir).replace(os.sep, "/") if tf else None
    return {
        "source_id": m.get("source_id"),
        "path": m.get("rel"),
        "kind": m.get("kind"),
        "size_bytes": m.get("size"),
        "encoding": m.get("encoding"),
        "extracted": bool(m.get("extracted", False)),
        "text_file": rel_tf,
    }


# --------------------------------------------------------------------------
# ledger, spec, state
# --------------------------------------------------------------------------

def load_ledger(work_dir):
    path = os.path.join(work_dir, "ledger.jsonl")
    records, errors = [], []
    if not os.path.exists(path):
        return records, errors
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    errors.append("ledger line %d not an object" % i)
                    continue
                rec.setdefault("id", "E%03d" % i)
                records.append(rec)
            except Exception as e:
                errors.append("ledger line %d bad json: %s" % (i, e))
    return records, errors


def load_spec(work_dir):
    spec_dir = os.path.join(work_dir, "spec")
    out, errors = {}, []
    if not os.path.isdir(spec_dir):
        return out, errors
    for fname in sorted(os.listdir(spec_dir)):
        if not fname.endswith(".json"):
            continue
        key = SPEC_KEYS.get(fname[:-5], fname[:-5])
        data, err = read_json(os.path.join(spec_dir, fname))
        if err:
            errors.append("spec fragment %s: %s" % (fname, err))
            continue
        out[key] = data
    return out, errors


def update_state(work_dir, phase) -> None:
    sp = os.path.join(work_dir, "state.json")
    d = {}
    if os.path.exists(sp):
        d, _ = read_json(sp)
        if not isinstance(d, dict):
            d = {}
    d["phase"] = phase
    write_json(sp, d)


def _started_at(work_dir):
    sp = os.path.join(work_dir, "state.json")
    if os.path.exists(sp):
        d, _ = read_json(sp)
        if isinstance(d, dict) and isinstance(d.get("started_at"), str):
            return d["started_at"]
    return utcnow_iso()


# --------------------------------------------------------------------------
# skeleton and contract backfill
# --------------------------------------------------------------------------

def build_skeleton(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case, case_err = read_json(os.path.join(input_dir, "case.json"))
    errors = []
    if case_err:
        errors.append("case.json: " + case_err)
        case = None
    if not isinstance(case, dict):
        case = {}
    oc = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    case_id = None
    for k in ("case_id", "id"):
        v = case.get(k)
        if isinstance(v, str) and v:
            case_id = v
            break
    if not case_id:
        case_id = "unresolved-" + dir_hash(input_dir)
    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": oc,
        "research_question": None,
        "search_protocol": None,
        "prior_work": None,
        "novelty": None,
        "feasibility": None,
        "evidence_matrix": [],
        "conflicts": [],
        "synthesis": {"conclusions": [], "research_gaps": [], "boundary_statement": None},
        "sources": [],
        "uncertainty": {"bias": [], "gaps": [], "unresolved": [], "limitations": []},
        "pending_human": [],
        "errors": errors,
        "degradations": [],
        "change_log": [],
        "runtime": {
            "started_at": _started_at(work_dir),
            "finished_at": None,
            "budget_seconds": 900,
            "budget_requests": 64,
            "max_tokens_per_response": 16384,
        },
        "validation": {},
    }
    # Echo every output_contract key to the top level without clobbering
    # richer defaults for keys this package already defines.
    for k in oc:
        skeleton.setdefault(k, None)
    return skeleton, case


def load_or_skeleton(input_dir, output_dir, work_dir):
    p = os.path.join(output_dir, "result.json")
    if os.path.exists(p):
        data, err = read_json(p)
        if err is None and isinstance(data, dict):
            return data, None
    return build_skeleton(input_dir, output_dir, work_dir)


# --------------------------------------------------------------------------
# verification primitives
# --------------------------------------------------------------------------

def quote_check(entry, text_dir) -> str:
    sid = entry.get("source_id")
    quote = entry.get("quote") or ""
    if not sid:
        return "source_missing"
    if not quote:
        return "empty_quote"
    if len(norm_ws(quote)) < 8:
        return "too_short"
    tpath = os.path.join(text_dir, (sid or "") + ".txt")
    if not os.path.exists(tpath):
        return "text_unavailable"
    try:
        txt, _ = read_text(tpath)
    except OSError:
        return "text_unavailable"
    if norm_ws(quote) in norm_ws(txt):
        return "verified"
    return "not_found"


def find_evidence_refs(obj):
    """Collect every (path, ref) where a claim points at an evidence id."""
    refs = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                p = path + "/" + k
                if k in REF_KEYS:
                    if isinstance(v, str) and v:
                        refs.append((p, v))
                    elif isinstance(v, list):
                        for x in v:
                            if isinstance(x, str) and x:
                                refs.append((p, x))
                else:
                    walk(v, p)
        elif isinstance(o, list):
            for i, x in enumerate(o):
                walk(x, path + "/%d" % i)

    walk(obj, "")
    return refs


# --------------------------------------------------------------------------
# compilation
# --------------------------------------------------------------------------

def assemble(input_dir, output_dir, work_dir):
    result, _case = build_skeleton(input_dir, output_dir, work_dir)
    errors = list(result.get("errors") or [])
    degradations = []

    # --- inventory ---
    inventory = []
    inv_missing = False
    inv_path = os.path.join(work_dir, "material_inventory.json")
    if os.path.exists(inv_path):
        d, err = read_json(inv_path)
        if err:
            errors.append("material_inventory.json: " + err)
        elif isinstance(d, dict):
            inventory = d.get("materials") or []
            if not isinstance(inventory, list):
                inventory = []
            degradations.extend(d.get("degradations") or [])
    else:
        inv_missing = True

    # --- ledger ---
    ledger, ledger_errors = load_ledger(work_dir)
    errors.extend(ledger_errors)
    ledger_ids = {norm_rid(r.get("id")) for r in ledger if r.get("id")}

    # --- spec ---
    spec, spec_errors = load_spec(work_dir)
    errors.extend(spec_errors)
    meta = None
    if isinstance(spec.get("_meta"), dict):
        meta = spec.pop("_meta")
    else:
        spec.pop("_meta", None)
    for key, val in spec.items():
        result[key] = val

    # --- evidence matrix + verbatim quote check ---
    text_dir = os.path.join(work_dir, "text")
    evidence_matrix = []
    unverified = []
    for rec in ledger:
        entry = {
            "id": rec.get("id"),
            "source_id": rec.get("source_id"),
            "locator": rec.get("locator") or "",
            "quote": rec.get("quote") or "",
            "field": rec.get("facet"),
            "status": rec.get("status"),
            "note": rec.get("note"),
        }
        check = quote_check(entry, text_dir)
        entry["quote_check"] = check
        entry["quote_verified"] = (check == "verified")
        if not entry["quote_verified"]:
            unverified.append(entry["id"])
        evidence_matrix.append(entry)
    result["evidence_matrix"] = evidence_matrix

    # --- referential integrity ---
    missing_refs = []
    for key in ("research_question", "prior_work", "novelty", "feasibility", "synthesis"):
        for path, ref in find_evidence_refs(result.get(key)):
            if norm_rid(ref) not in ledger_ids:
                missing_refs.append("%s@%s -> %s" % (key, path, ref))

    # --- sources ---
    sources = [make_source(m, work_dir) for m in inventory]
    if meta is not None:
        for x in (meta.get("extra_sources") or []):
            if isinstance(x, dict):
                sources.append(x)
    result["sources"] = sources

    # --- change log ---
    change_log = []
    cl_path = os.path.join(work_dir, "change_log.jsonl")
    if os.path.exists(cl_path):
        with open(cl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        change_log.append(json.loads(line))
                    except Exception:
                        pass
    if meta is not None and isinstance(meta.get("change_log"), list):
        change_log.extend(meta["change_log"])
    result["change_log"] = change_log

    # --- uncertainty / synthesis defaults ---
    result["uncertainty"] = ensure_uncertainty(result.get("uncertainty"))
    if meta is not None and isinstance(meta.get("limitations"), list):
        result["uncertainty"]["limitations"] = list(result["uncertainty"]["limitations"]) + meta["limitations"]
    syn = result.get("synthesis")
    if not isinstance(syn, dict):
        syn = {}
    for k, d in (("conclusions", []), ("research_gaps", []), ("boundary_statement", None)):
        syn.setdefault(k, d)
    result["synthesis"] = syn

    # --- pending_human ---
    if meta is not None and isinstance(meta.get("pending_human"), list):
        result["pending_human"] = list(meta["pending_human"])
    for eid in unverified:
        if not any((isinstance(x, dict) and x.get("evidence_id") == eid) for x in result["pending_human"]):
            result["pending_human"].append(
                {"what": "verify_evidence", "evidence_id": eid, "reason": "quote_check failed"}
            )

    # --- floor status (structural facts cannot be upgraded away) ---
    if not os.path.isdir(input_dir):
        floor = "input_error"
    elif not inventory:
        floor = "insufficient_evidence"
    else:
        floor = "partial"
    rank = {"ok": 0, "partial": 1, "insufficient_evidence": 2, "input_error": 3}
    status = (meta or {}).get("status") if meta is not None else None
    if not (isinstance(status, str) and status in VALID_STATUS):
        status = floor
    if rank.get(status, 1) < rank.get(floor, 1):
        status = floor

    # --- "ok" requires completion evidence, not just a claim ---
    if status == "ok":
        missing_core = [k for k in CORE_DELIVERABLES if result.get(k) is None]
        conclusions = syn.get("conclusions") or []
        if missing_core or not conclusions or unverified or missing_refs or errors:
            status = "partial"
    result["status"] = status

    # --- errors / degradations ---
    if inv_missing:
        degradations.append({"what": "material_inventory_missing", "detail": "run scan before build for full quote verification"})
    result["errors"] = dedupe(errors)
    result["degradations"] = dedupe(degradations)

    # --- status consistency notes ---
    status_notes = []
    if result["status"] == "ok":
        if result["errors"]:
            status_notes.append("status=ok but errors non-empty")
        if result["pending_human"]:
            status_notes.append("status=ok but pending_human non-empty")
        if result["uncertainty"].get("limitations"):
            status_notes.append("status=ok but limitations non-empty")
    contract_missing = [k for k in result.get("output_contract", {}) if result.get(k) is None]
    result["validation"] = {
        "quote_unverified": unverified,
        "missing_evidence_refs": missing_refs,
        "status_notes": status_notes,
        "contract_missing": contract_missing,
    }
    result["runtime"]["finished_at"] = utcnow_iso()
    return result


# --------------------------------------------------------------------------
# report.md
# --------------------------------------------------------------------------

def render_value(v, indent=0):
    pad = "  " * indent
    if isinstance(v, dict):
        out = []
        for k, val in list(v.items())[:20]:
            if isinstance(val, (dict, list)):
                out.append("%s- **%s**:" % (pad, k))
                out.append(render_value(val, indent + 1))
            else:
                s = str(val)
                if len(s) > 300:
                    s = s[:300] + "…"
                out.append("%s- **%s**: %s" % (pad, k, s))
        if len(v) > 20:
            out.append("%s- …（另有 %d 项）" % (pad, len(v) - 20))
        return "\n".join(out)
    if isinstance(v, list):
        out = []
        for item in v[:20]:
            if isinstance(item, (dict, list)):
                out.append(render_value(item, indent + 1))
            else:
                s = str(item)
                if len(s) > 300:
                    s = s[:300] + "…"
                out.append("%s- %s" % (pad, s))
        if len(v) > 20:
            out.append("%s- …（另有 %d 项）" % (pad, len(v) - 20))
        return "\n".join(out)
    s = str(v)
    if len(s) > 500:
        s = s[:500] + "…"
    return pad + s


def write_report(result, path) -> None:
    L = []
    L.append("# 科研选题与创新性评估报告")
    L.append("")
    L.append("- case_id: `%s`" % result.get("case_id"))
    L.append("- status: `%s`" % result.get("status"))
    L.append("- 证据条数: %d" % len(result.get("evidence_matrix") or []))
    L.append("- 冲突条数: %d" % len(result.get("conflicts") or []))
    L.append("- 待人工确认: %d" % len(result.get("pending_human") or []))
    L.append("- 错误: %d / 降级: %d" % (len(result.get("errors") or []), len(result.get("degradations") or [])))
    L.append("")
    for title, key in (
        ("研究问题", "research_question"),
        ("检索方案", "search_protocol"),
        ("已有工作比较", "prior_work"),
        ("创新点识别", "novelty"),
        ("可行性判断", "feasibility"),
        ("综合结论", "synthesis"),
        ("冲突证据", "conflicts"),
        ("不确定性", "uncertainty"),
    ):
        L.append("## %s" % title)
        L.append("")
        val = result.get(key)
        if val in (None, [], {}):
            L.append("_未填写_")
        else:
            L.append(render_value(val))
        L.append("")
    L.append("## 证据矩阵")
    L.append("")
    em = result.get("evidence_matrix") or []
    if not em:
        L.append("_无证据记录_")
    else:
        for e in em:
            q = e.get("quote") or ""
            if len(q) > 200:
                q = q[:200] + "…"
            L.append("- **%s** `%s` `%s` [%s] — %s" % (e.get("id"), e.get("source_id"), e.get("locator"), e.get("quote_check"), q))
    L.append("")
    L.append("## 校验与状态")
    L.append("")
    v = result.get("validation") or {}
    L.append("- 引文未核验: %s" % (", ".join(str(x) for x in (v.get("quote_unverified") or [])) or "无"))
    L.append("- 缺失证据引用: %s" % ("；".join(str(x) for x in (v.get("missing_evidence_refs") or [])) or "无"))
    L.append("- 状态提示: %s" % ("；".join(str(x) for x in (v.get("status_notes") or [])) or "无"))
    L.append("- 待人工确认: %d 项" % len(result.get("pending_human") or []))
    L.append("- 限制: %d 项" % len((result.get("uncertainty") or {}).get("limitations") or []))
    L.append("")
    L.append("## 变更记录")
    L.append("")
    cl = result.get("change_log") or []
    if not cl:
        L.append("_无_")
    else:
        for c in cl:
            L.append("- %s — %s" % (c.get("ts", ""), c.get("msg", "")))
    L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# --------------------------------------------------------------------------
# CLI commands
# --------------------------------------------------------------------------

def cmd_init(input, output, work):
    skeleton, _case = build_skeleton(input, output, work)
    write_json(os.path.join(output, "result.json"), skeleton)
    os.makedirs(os.path.join(work, "spec"), exist_ok=True)
    ledger_path = os.path.join(work, "ledger.jsonl")
    if not os.path.exists(ledger_path):
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write("")
    write_json(os.path.join(work, "state.json"), {
        "started_at": skeleton["runtime"]["started_at"],
        "phase": "init",
        "case_id": skeleton["case_id"],
    })
    return 0


def cmd_scan(input, output, work):
    result, _case = load_or_skeleton(input, output, work)
    errors = list(result.get("errors") or [])
    degradations = []
    if not os.path.isdir(input):
        result["status"] = "input_error"
        errors.append("input directory missing: %s" % input)
        result["errors"] = errors
        result["degradations"] = list(result.get("degradations") or [])
        write_json(os.path.join(output, "result.json"), result)
        write_json(os.path.join(work, "material_inventory.json"), {
            "materials": [], "skipped": [], "degradations": [], "input_dir_missing": True,
        })
        update_state(work, "scan")
        return 0
    materials, skipped = discover_materials(input)
    materials.sort(key=lambda m: m["rel"].lower())
    for i, m in enumerate(materials, 1):
        m["source_id"] = "m%02d" % i
    text_dir = os.path.join(work, "text")
    os.makedirs(text_dir, exist_ok=True)
    for m in materials:
        extract_text(m, text_dir, degradations)
    inventory = {"materials": materials, "skipped": skipped, "degradations": degradations, "input_dir_missing": False}
    write_json(os.path.join(work, "material_inventory.json"), inventory)
    result["sources"] = [make_source(m, work) for m in materials]
    result["degradations"] = dedupe(list(result.get("degradations") or []) + degradations)
    if not materials:
        result["status"] = "insufficient_evidence"
        result["uncertainty"] = ensure_uncertainty(result.get("uncertainty"))
        result["uncertainty"]["gaps"].append("输入目录内未发现材料文件")
    result["errors"] = errors
    write_json(os.path.join(output, "result.json"), result)
    update_state(work, "scan")
    return 0


def cmd_evidence(work, id, source, locator, quote, facet, status, note):
    rec = {
        "id": id or None,
        "source_id": source,
        "locator": locator or "",
        "quote": quote,
        "facet": facet or "general",
        "status": status or "fact",
        "note": note or "",
    }
    if not rec["id"]:
        records, _ = load_ledger(work)
        rec["id"] = "E%03d" % (len(records) + 1)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "ledger.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return 0


def cmd_log(work, msg):
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "change_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": utcnow_iso(), "msg": msg}, ensure_ascii=False) + "\n")
    return 0


def cmd_build(input, output, work):
    try:
        result = assemble(input, output, work)
        write_json(os.path.join(output, "result.json"), result)
        write_report(result, os.path.join(output, "report.md"))
        return 0
    except Exception as e:  # last-resort minimal valid result
        try:
            os.makedirs(output, exist_ok=True)
            result, _case = build_skeleton(input, output, work)
            result["errors"] = list(result.get("errors") or []) + ["build failed: %s" % e]
            result["status"] = "partial"
            write_json(os.path.join(output, "result.json"), result)
            write_report(result, os.path.join(output, "report.md"))
            return 0
        except Exception:
            return 2


def cmd_selftest():
    return run_selftest()


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="research-selection-audit CLI")

    def add_io(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    sp = p.add_subparsers(dest="command", required=True)
    sp_init = sp.add_parser("init", help="write partial result skeleton within the first 3 minutes")
    add_io(sp_init)
    sp_init.set_defaults(func=cmd_init)

    sp_scan = sp.add_parser("scan", help="discover materials and extract text/PDF")
    add_io(sp_scan)
    sp_scan.set_defaults(func=cmd_scan)

    sp_build = sp.add_parser("build", help="compile spec + ledger into result.json and report.md")
    add_io(sp_build)
    sp_build.set_defaults(func=cmd_build)

    sp_ev = sp.add_parser("evidence", help="append one evidence record to the ledger")
    sp_ev.add_argument("--work", default="/app/work")
    sp_ev.add_argument("--id", default=None)
    sp_ev.add_argument("--source", required=True)
    sp_ev.add_argument("--locator", default="")
    sp_ev.add_argument("--quote", required=True)
    sp_ev.add_argument("--facet", default="general")
    sp_ev.add_argument("--status", default="fact")
    sp_ev.add_argument("--note", default="")
    sp_ev.set_defaults(func=cmd_evidence)

    sp_log = sp.add_parser("log", help="append a change-log entry")
    sp_log.add_argument("--work", default="/app/work")
    sp_log.add_argument("--msg", required=True)
    sp_log.set_defaults(func=cmd_log)

    sp_st = sp.add_parser("selftest", help="run synthetic self-tests")
    sp_st.set_defaults(func=cmd_selftest)

    return p


# --------------------------------------------------------------------------
# self-test (synthetic inputs only; never touches real cases)
# --------------------------------------------------------------------------

def run_selftest():
    base = tempfile.mkdtemp(prefix="rsa_selftest_")
    results = []

    def check(name, fn):
        try:
            fn()
            results.append((name, True, ""))
        except Exception as e:
            results.append((name, False, "%s: %s" % (type(e).__name__, e)))

    def sid_of(work, rel):
        inv, _ = read_json(os.path.join(work, "material_inventory.json"))
        for m in inv.get("materials", []):
            if m.get("rel") == rel:
                return m.get("source_id")
        raise AssertionError("material %r not found" % rel)

    def _help():
        p = build_parser()
        for c in ("init", "scan", "evidence", "log", "build", "selftest"):
            try:
                p.parse_args([c, "--help"])
            except SystemExit as e:
                assert e.code == 0, "%s --help exit=%r" % (c, e.code)
            else:
                raise AssertionError("%s --help did not exit" % c)
    check("help_exit0", _help)

    def _missing_input():
        d = os.path.join(base, "does_not_exist")
        out = os.path.join(base, "out_missing")
        work = os.path.join(base, "work_missing")
        assert cmd_init(d, out, work) == 0
        assert cmd_scan(d, out, work) == 0
        r, _ = read_json(os.path.join(out, "result.json"))
        assert r["status"] == "input_error", r["status"]
    check("missing_input", _missing_input)

    def _empty_input():
        d = os.path.join(base, "empty")
        os.makedirs(d)
        out = os.path.join(base, "out_empty")
        work = os.path.join(base, "work_empty")
        assert cmd_init(d, out, work) == 0
        assert cmd_scan(d, out, work) == 0
        r, _ = read_json(os.path.join(out, "result.json"))
        assert r["status"] == "insufficient_evidence", r["status"]
    check("empty_input", _empty_input)

    def _bad_json():
        d = os.path.join(base, "badjson")
        os.makedirs(os.path.join(d, "materials"))
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        with open(os.path.join(d, "materials", "paper.txt"), "w", encoding="utf-8") as f:
            f.write("This is a test paper. It proposes method X for task Y.\n")
        out = os.path.join(base, "out_bad")
        work = os.path.join(base, "work_bad")
        assert cmd_init(d, out, work) == 0
        assert cmd_scan(d, out, work) == 0
        assert cmd_build(d, out, work) == 0
        r, _ = read_json(os.path.join(out, "result.json"))
        assert r["errors"], "expected errors for bad case.json"
        assert r["status"] in ("partial", "insufficient_evidence"), r["status"]
    check("bad_case_json", _bad_json)

    def _full():
        d = os.path.join(base, "full")
        os.makedirs(os.path.join(d, "materials"))
        case = {"case_id": "CASE-1", "output_contract": {"research_question": "rq", "prior_work": "pw"}}
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f)
        paper = "The proposed method uses a transformer to predict binding affinity. Baseline is a random forest."
        with open(os.path.join(d, "materials", "paper.txt"), "w", encoding="utf-8") as f:
            f.write(paper + "\n")
        out = os.path.join(base, "out_full")
        work = os.path.join(base, "work_full")
        assert cmd_init(d, out, work) == 0
        assert cmd_scan(d, out, work) == 0
        sid = sid_of(work, "materials/paper.txt")
        assert cmd_evidence(work, None, sid, "materials/paper.txt:1",
                            "uses a transformer to predict binding affinity",
                            "prior_work", "fact", "") == 0
        os.makedirs(os.path.join(work, "spec"), exist_ok=True)
        write_json(os.path.join(work, "spec", "question.json"), {
            "testable_statement": "Replacing RF with a transformer changes binding-affinity MAE on the fixed benchmark.",
            "object": "binding-affinity model", "variables": ["model type"], "baseline": "random forest",
            "expected": "lower MAE", "falsifiable_condition": "MAE not lower vs baseline", "status": "complete",
        })
        write_json(os.path.join(work, "spec", "prior.json"), [{
            "work": "paper.txt", "task": "binding affinity", "method": "transformer", "data": "unknown",
            "metric": "MAE", "conclusion": "transformer proposed", "evidence_ids": ["E001"], "comparable": "partial",
        }])
        write_json(os.path.join(work, "spec", "novelty.json"), [{
            "claim": "transformer for binding affinity", "type": "method",
            "verdict": "covered", "covered_by": "paper.txt", "evidence_ids": ["E001"],
            "reason": "same method already in material",
        }])
        write_json(os.path.join(work, "spec", "feasibility.json"), [{
            "premise": "data availability", "status": "missing",
            "note": "material does not state data source",
        }])
        write_json(os.path.join(work, "spec", "synthesis.json"), {
            "conclusions": ["The material already proposes a transformer, so the claimed method is not novel within this evidence set."],
            "research_gaps": ["no dataset reported"],
            "boundary_statement": "Limited to the single material file.",
        })
        write_json(os.path.join(work, "spec", "meta.json"), {"status": "partial", "limitations": ["single source"]})
        assert cmd_build(d, out, work) == 0
        r, _ = read_json(os.path.join(out, "result.json"))
        assert r["case_id"] == "CASE-1", r["case_id"]
        assert r["output_contract"] == case["output_contract"]
        assert r["research_question"]["testable_statement"]
        assert r["evidence_matrix"], "evidence matrix empty"
        e0 = r["evidence_matrix"][0]
        assert e0["quote_check"] == "verified", e0["quote_check"]
        assert r["validation"]["missing_evidence_refs"] == [], r["validation"]["missing_evidence_refs"]
        assert os.path.exists(os.path.join(out, "report.md"))
    check("full_pipeline_verified", _full)

    def _mismatch():
        d = os.path.join(base, "mismatch")
        os.makedirs(os.path.join(d, "materials"))
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "CASE-2"}, f)
        with open(os.path.join(d, "materials", "paper.txt"), "w", encoding="utf-8") as f:
            f.write("Actual text that does not contain the quoted sentence.\n")
        out = os.path.join(base, "out_mismatch")
        work = os.path.join(base, "work_mismatch")
        cmd_init(d, out, work)
        cmd_scan(d, out, work)
        sid = sid_of(work, "materials/paper.txt")
        cmd_evidence(work, None, sid, "materials/paper.txt:1",
                     "a sentence that is not present in the source at all",
                     "prior_work", "fact", "")
        cmd_build(d, out, work)
        r, _ = read_json(os.path.join(out, "result.json"))
        e0 = r["evidence_matrix"][0]
        assert e0["quote_check"] in ("not_found", "too_short"), e0["quote_check"]
        assert any((x.get("evidence_id") == e0["id"]) for x in r["pending_human"])
    check("quote_mismatch_flagged", _mismatch)

    def _determinism():
        d = os.path.join(base, "det")
        os.makedirs(os.path.join(d, "materials"))
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "CASE-3"}, f)
        with open(os.path.join(d, "materials", "paper.txt"), "w", encoding="utf-8") as f:
            f.write("Determinism check text with enough length for a quote.\n")
        out = os.path.join(base, "out_det")
        work = os.path.join(base, "work_det")
        cmd_init(d, out, work)
        cmd_scan(d, out, work)
        sid = sid_of(work, "materials/paper.txt")
        cmd_evidence(work, None, sid, "materials/paper.txt:1", "Determinism check text", "prior_work", "fact", "")
        os.makedirs(os.path.join(work, "spec"), exist_ok=True)
        write_json(os.path.join(work, "spec", "question.json"), {
            "testable_statement": "A minimal deterministic statement.", "object": "x", "variables": [],
            "baseline": "none", "expected": "none", "falsifiable_condition": "none", "status": "partial",
        })
        write_json(os.path.join(work, "spec", "synthesis.json"), {
            "conclusions": ["determinism"], "research_gaps": [], "boundary_statement": "synthetic",
        })
        out_a = os.path.join(base, "out_det_a")
        out_b = os.path.join(base, "out_det_b")
        cmd_build(d, out_a, work)
        cmd_build(d, out_b, work)
        ra, _ = read_json(os.path.join(out_a, "result.json"))
        rb, _ = read_json(os.path.join(out_b, "result.json"))
        ra["runtime"]["finished_at"] = None
        rb["runtime"]["finished_at"] = None
        assert ra == rb, "deterministic build differs"
    check("determinism", _determinism)

    n_pass = sum(1 for _, ok, _ in results if ok)
    for name, ok, err in results:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" — " + err) if err else ""))
    print("selftest: %d/%d passed" % (n_pass, len(results)))
    return 0 if n_pass == len(results) else 1
