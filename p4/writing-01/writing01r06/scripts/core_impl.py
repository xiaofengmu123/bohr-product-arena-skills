#!/usr/bin/env python3
"""Shared, stdlib-only helpers for the fact-traceable academic-writing skill.

Design rules
------------
* No side effects on import. Every function returns data or an error string;
  it never raises for expected input problems (missing files, bad JSON,
  bad encoding, oversized files).
* Deterministic: same input directory -> same JSON output. All timestamps use
  UTC ISO-8601; ordering is sorted where it matters.
* The intermediate representations (contract_meta / plan / material_index /
  figure_model / evidence_map / consistency_report) are the audit trail: every
  fact in the manuscript can be traced manuscript -> [S:locator] -> claim ->
  locator + verbatim quote -> source file.
"""

import datetime
import json
import os
import re
import time

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB per source file

TEXT_EXTS = {".md", ".txt", ".csv", ".tsv", ".json", ".tex", ".bib", ".ris",
             ".enl", ".xml", ".html", ".yaml", ".yml"}
MATERIAL_EXTS = TEXT_EXTS | {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
                             ".docx", ".xlsx", ".pptx", ".svg", ".doc",
                             ".xls", ".ppt", ".webp"}

STATUS_ENUM = {"in_progress", "partial", "complete", "failed",
               "insufficient_evidence", "input_error"}

DEFAULT_CONTRACT = {
    "deliverable": "manuscript.md",
    "fact_source_map": "evidence_map.json",
    "version_limits": {
        "editable": True,
        "changes_recorded": True,
        "unconfirmed_marked": True,
    },
}

SOURCE_RE = re.compile(r"\[S:([^\]\s]+)\]")
CITE_RE = re.compile(r"\[C:([^\]\s]+)\]")
CITE_DEF_RE = re.compile(r"^\s*\[C:([^\]\s]+)\]\s*", re.MULTILINE)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
FIG_RE = re.compile(r"\b(?:Figure|Fig\.?|图)\s*(\d+)", re.IGNORECASE)
TABLE_RE = re.compile(r"\b(?:Table|表)\s*(\d+)", re.IGNORECASE)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_timestamp():
    return time.time()


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file robustly. Returns (text, error_string_or_None)."""
    if not os.path.isfile(path):
        return None, "file not found: %s" % path
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, "stat failed: %s" % e
    if size > max_bytes:
        return None, "file too large (%d bytes > %d): %s" % (size, max_bytes, path)
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gbk", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), None
        except UnicodeError as e:
            last_err = "%s: %s" % (enc, e)
            continue
        except OSError as e:
            return None, "open failed: %s" % e
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        return text, "decode fallback (errors=replace); last error: %s" % (last_err or "none")
    except OSError as e:
        return None, "read failed: %s" % e


def read_json(path, max_bytes=MAX_FILE_BYTES):
    """Read and parse JSON. Returns (obj_or_None, error_string_or_None)."""
    text, err = read_text(path, max_bytes)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "JSON decode error in %s: %s" % (path, e)


def write_json(path, obj):
    """Atomically write JSON (utf-8, no BOM). Returns error string or None."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return None
    except OSError as e:
        return "write failed for %s: %s" % (path, e)


def classify_material(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".svg"):
        return "image"
    if ext in (".docx", ".doc"):
        return "docx"
    if ext in (".xlsx", ".xls"):
        return "xlsx"
    if ext in (".pptx", ".ppt"):
        return "pptx"
    if ext == ".bib":
        return "bib"
    if ext in TEXT_EXTS:
        return "text"
    return "unknown"


def discover_input(input_dir):
    """Scan input dir (recursively) for case.json / task.md / materials.

    Never assumes fixed filenames. Returns a dict with keys
    case_file, task_file, materials, warnings.
    """
    out = {"case_file": None, "task_file": None, "materials": [], "warnings": []}
    if not os.path.isdir(input_dir):
        out["warnings"].append("input dir not found: %s" % input_dir)
        return out
    files = []
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and os.path.basename(d) not in ("work", "output", "result")]
        for n in names:
            if n.startswith("."):
                continue
            p = os.path.join(root, n)
            if os.path.isfile(p):
                files.append(p)
    files.sort()
    for p in files:
        base = os.path.basename(p).lower()
        if out["case_file"] is None and base == "case.json":
            out["case_file"] = p
            continue
        if out["task_file"] is None and base in ("task.md", "tasks.md", "task.txt"):
            out["task_file"] = p
            continue
        ext = os.path.splitext(base)[1].lower()
        if ext in MATERIAL_EXTS and base != "result.json":
            out["materials"].append(p)
    if out["case_file"] is None:
        # fallback: any JSON whose basename suggests a case/input spec
        for p in files:
            base = os.path.basename(p).lower()
            if base.endswith(".json") and "result" not in base and "output" not in base:
                out["case_file"] = p
                break
    return out


def extract_case_id(case, case_file):
    if isinstance(case, dict):
        for key in ("id", "case_id", "caseId", "challenge_id", "challengeId",
                    "task_id", "taskId"):
            v = case.get(key)
            if isinstance(v, str) and v:
                return v
    if case_file:
        return os.path.splitext(os.path.basename(case_file))[0]
    return "unknown"


def extract_output_contract(case):
    if isinstance(case, dict):
        for key in ("output_contract", "outputContract", "output", "contract"):
            v = case.get(key)
            if isinstance(v, dict) and v:
                return dict(v)
    return dict(DEFAULT_CONTRACT)


def extract_task_hints(case):
    """Pull lightweight task metadata when present; never fabricate."""
    hints = {"writing_task": None, "audience": None, "format": None,
             "language": None}
    if not isinstance(case, dict):
        return hints
    for key in ("task", "instruction", "prompt", "objective", "description"):
        v = case.get(key)
        if isinstance(v, str) and v:
            hints["writing_task"] = v[:4000]
            break
    for key in ("audience", "target_audience"):
        v = case.get(key)
        if isinstance(v, str) and v:
            hints["audience"] = v
            break
    for key in ("format", "output_format", "genre"):
        v = case.get(key)
        if isinstance(v, str) and v:
            hints["format"] = v
            break
    for key in ("language", "lang"):
        v = case.get(key)
        if isinstance(v, str) and v:
            hints["language"] = v
            break
    return hints


def scan_markers(text):
    """Return (list_of_source_markers, list_of_citation_keys)."""
    if not text:
        return [], []
    return SOURCE_RE.findall(text), CITE_RE.findall(text)


def find_citation_definitions(text):
    """Find [C:key] definitions at the start of reference-list lines."""
    if not text:
        return []
    return [m.group(1) for m in CITE_DEF_RE.finditer(text)]


def extract_headings(text):
    return [m.group(1).strip() for m in HEADING_RE.finditer(text or "")]


def build_skeleton(input_dir, output_dir, work_dir):
    """Discover input, write result.json skeleton + intermediate JSON.

    Returns (summary_dict, error_string_or_None). Never raises.
    """
    started = now_iso()
    started_epoch = utc_timestamp()
    warnings = []
    disc = discover_input(input_dir)
    warnings.extend(disc["warnings"])

    case = None
    case_err = None
    if disc["case_file"]:
        case, case_err = read_json(disc["case_file"])
        if case_err:
            warnings.append("case.json unreadable: %s" % case_err)

    case_id = extract_case_id(case, disc["case_file"])
    contract = extract_output_contract(case)
    hints = extract_task_hints(case)

    if not disc["case_file"] and not disc["materials"]:
        status = "insufficient_evidence"
    elif case_err and not disc["materials"]:
        status = "input_error"
    else:
        status = "in_progress"

    result = dict(contract)
    result.update({
        "case_id": case_id,
        "status": status,
        "artifacts": {
            "manuscript": os.path.join(output_dir, "manuscript.md"),
            "evidence_map": os.path.join(work_dir, "evidence_map.json"),
            "material_index": os.path.join(work_dir, "material_index.json"),
            "figure_model": os.path.join(work_dir, "figure_model.json"),
            "plan": os.path.join(work_dir, "plan.json"),
            "consistency_report": os.path.join(work_dir, "consistency_report.json"),
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [
            {"ts": started, "step": "init",
             "note": "skeleton written; status=%s" % status}
        ],
        "runtime": {"start_ts": started, "start_epoch": started_epoch,
                    "end_ts": None, "elapsed_s": None},
    })
    if case_err:
        result["errors"].append({"kind": "case_parse", "message": case_err})
        result["degradations"].append("case.json unreadable; output_contract defaults applied")
    if not disc["materials"]:
        result["degradations"].append("no material files discovered under %s" % input_dir)

    contract_meta = {
        "case_id": case_id,
        "input_dir": input_dir,
        "case_file": disc["case_file"],
        "task_file": disc["task_file"],
        "output_contract": contract,
        "task_hints": hints,
        "detected_materials": [
            {"path": m, "kind": classify_material(m)} for m in disc["materials"]
        ],
        "warnings": warnings,
        "created_ts": started,
    }

    plan = {
        "case_id": case_id,
        "created_ts": started,
        "writing_task": hints["writing_task"],
        "audience": hints["audience"],
        "format": hints["format"],
        "language": hints["language"],
        "sections": [],        # agent fills: [{"title": ..., "required": bool}]
        "figure_plan": [],     # agent fills
        "citation_plan": [],   # agent fills
        "open_questions": [],  # agent fills
    }

    material_index = {
        "case_id": case_id,
        "created_ts": started,
        "materials": [
            {
                "id": "M%d" % (i + 1),
                "path": m,
                "kind": classify_material(m),
                "label": os.path.basename(m),
                "parse_status": "todo",     # todo | ok | failed | partial
                "key_claims": [],           # {"id","text","locator","quote","numbers","units","sample_size","flags"}
                "numbers": [],
                "citations": [],            # {"key","authors","year","journal","volume","issue","pages","doi","quote"}
                "flags": [],
            }
            for i, m in enumerate(disc["materials"])
        ],
        "conflicts": [],
    }

    figure_model = {"case_id": case_id, "created_ts": started,
                    "figures": [], "tables": []}

    evidence_map = {
        "case_id": case_id,
        "facts": [],
        "citations_used": [],
        "citations_defined": [],
        "dangling_sources": [],
        "dangling_citations": [],
        "unused_citations": [],
        "report": {},
    }

    consistency_report = {
        "case_id": case_id,
        "checks": [],
        "missing_sections": [],
        "figure_conflicts": [],
        "status_honest": None,
        "summary": {"passed": 0, "failed": 0, "warnings": 0},
    }

    errs = []
    for path, obj in [
        (os.path.join(output_dir, "result.json"), result),
        (os.path.join(work_dir, "contract_meta.json"), contract_meta),
        (os.path.join(work_dir, "plan.json"), plan),
        (os.path.join(work_dir, "material_index.json"), material_index),
        (os.path.join(work_dir, "figure_model.json"), figure_model),
        (os.path.join(work_dir, "evidence_map.json"), evidence_map),
        (os.path.join(work_dir, "consistency_report.json"), consistency_report),
    ]:
        e = write_json(path, obj)
        if e:
            errs.append(e)

    summary = {
        "case_id": case_id,
        "status": status,
        "case_file": disc["case_file"],
        "task_file": disc["task_file"],
        "material_count": len(disc["materials"]),
        "warnings": warnings,
        "wrote": [os.path.join(output_dir, "result.json")] +
                 [os.path.join(work_dir, n) for n in
                  ("contract_meta.json", "plan.json", "material_index.json",
                   "figure_model.json", "evidence_map.json",
                   "consistency_report.json")],
    }
    return summary, ("; ".join(errs) if errs else None)


def build_evidence_map(manuscript_path, material_index_path, work_dir):
    """Cross-check [S:] / [C:] markers against the material index and draft.

    Writes evidence_map.json and returns (report_dict, error_string_or_None).
    """
    text, err = read_text(manuscript_path)
    if err:
        return None, err
    index, err2 = read_json(material_index_path)
    if err2:
        index = {"materials": []}

    sources, cites = scan_markers(text)
    cite_defs = find_citation_definitions(text)

    material_ids = set()
    claim_locators = set()
    material_citations = set()
    for m in index.get("materials", []):
        mid = m.get("id")
        if mid:
            material_ids.add(mid)
        for c in m.get("key_claims", []):
            cid = c.get("id")
            loc = c.get("locator")
            if mid and cid:
                claim_locators.add("%s.%s" % (mid, cid))
            if mid and loc:
                claim_locators.add("%s.%s" % (mid, loc))
        for c in m.get("citations", []):
            k = c.get("key")
            if k:
                material_citations.add(k)

    facts = []
    dangling_sources = []
    for s in sorted(set(sources)):
        prefix = s.split(".", 1)[0]
        mapped = prefix in material_ids or s in claim_locators
        facts.append({"marker": s, "mapped": mapped})
        if not mapped:
            dangling_sources.append(s)

    used = sorted(set(cites))
    dangling_citations = [c for c in used if c not in cite_defs]
    unused_citations = [c for c in sorted(set(cite_defs)) if c not in used]
    # citations defined in material index but never reproduced in manuscript refs
    missing_from_manuscript = sorted(material_citations - set(cite_defs))

    report = {
        "case_id": index.get("case_id"),
        "counts": {
            "source_markers": len(set(sources)),
            "citation_used": len(used),
            "citation_defined_in_manuscript": len(set(cite_defs)),
        },
        "facts": facts,
        "citations_used": used,
        "citations_defined": sorted(set(cite_defs)),
        "dangling_sources": dangling_sources,
        "dangling_citations": dangling_citations,
        "unused_citations": unused_citations,
        "material_citations_missing_from_manuscript": missing_from_manuscript,
        "ok": not dangling_sources and not dangling_citations,
    }
    e = write_json(os.path.join(work_dir, "evidence_map.json"), report)
    return report, e


def run_consistency_check(result_path, manuscript_path, plan_path, work_dir):
    """Run structural and honesty checks against the current draft.

    Writes consistency_report.json; returns (report_dict, error_string_or_None).
    """
    result, err = read_json(result_path)
    if err:
        result = {}
    text, err2 = read_text(manuscript_path)
    if err2:
        text = ""
    plan, err3 = read_json(plan_path)
    if err3:
        plan = {}

    checks = []
    headings = extract_headings(text)
    heading_norm = [re.sub(r"\s+", "", h).lower() for h in headings]

    required_sections = []
    for s in plan.get("sections", []):
        title = s.get("title") if isinstance(s, dict) else s
        if title:
            required_sections.append(title)
    missing_sections = []
    for title in required_sections:
        norm = re.sub(r"\s+", "", title).lower()
        if not any(norm in h for h in heading_norm):
            missing_sections.append(title)
    checks.append({
        "id": "sections_complete",
        "name": "计划章节全部有对应标题",
        "pass": not missing_sections,
        "detail": "missing=%s" % (missing_sections or "none"),
    })

    fig_nums = sorted({int(x) for x in FIG_RE.findall(text)})
    table_nums = sorted({int(x) for x in TABLE_RE.findall(text)})
    fig_ok = fig_nums == list(range(1, len(fig_nums) + 1)) if fig_nums else True
    table_ok = table_nums == list(range(1, len(table_nums) + 1)) if table_nums else True
    checks.append({
        "id": "figure_numbering",
        "name": "Figure 编号连续且从 1 开始",
        "pass": fig_ok,
        "detail": "found=%s" % (fig_nums or "none"),
    })
    checks.append({
        "id": "table_numbering",
        "name": "Table 编号连续且从 1 开始",
        "pass": table_ok,
        "detail": "found=%s" % (table_nums or "none"),
    })

    status = result.get("status")
    checks.append({
        "id": "status_valid",
        "name": "status 在允许枚举内",
        "pass": status in STATUS_ENUM,
        "detail": "status=%s" % status,
    })

    # Honesty: a complete result must not carry unresolved human/uncertainty items.
    unresolved = []
    for item in result.get("uncertainty", []) + result.get("pending_human", []):
        if not (isinstance(item, dict) and item.get("resolved") is True):
            unresolved.append(item)
    status_honest = True
    if status == "complete" and unresolved:
        status_honest = False
    checks.append({
        "id": "status_honest",
        "name": "status 与未决项一致（complete 不得遗留未决项）",
        "pass": status_honest,
        "detail": "unresolved_count=%d" % len(unresolved),
    })

    summary = {
        "passed": sum(1 for c in checks if c["pass"]),
        "failed": sum(1 for c in checks if not c["pass"]),
        "warnings": 0,
    }
    report = {
        "case_id": result.get("case_id"),
        "checks": checks,
        "missing_sections": missing_sections,
        "figure_conflicts": [],
        "status_honest": status_honest,
        "summary": summary,
        "headings": headings,
    }
    e = write_json(os.path.join(work_dir, "consistency_report.json"), report)
    return report, e


def has_unresolved(items):
    for item in items or []:
        if not (isinstance(item, dict) and item.get("resolved") is True):
            return True
    return False


def finalize_result(output_dir, work_dir, status=None):
    """Finalize result.json: timestamp, honest status, validate schema.

    Returns (summary_dict, error_string_or_None).
    """
    rp = os.path.join(output_dir, "result.json")
    result, err = read_json(rp)
    if err:
        return None, err
    if not isinstance(result, dict):
        return None, "result.json is not a JSON object"

    finished = now_iso()
    start_epoch = result.get("runtime", {}).get("start_epoch")
    elapsed = round(utc_timestamp() - start_epoch, 1) if start_epoch else None

    unresolved = has_unresolved(result.get("uncertainty") or []) or \
        has_unresolved(result.get("pending_human") or [])
    new_status = status or result.get("status") or "partial"
    if new_status == "complete" and unresolved:
        new_status = "partial"
        result["degradations"] = list(result.get("degradations") or [])
        result["degradations"].append(
            "status requested as complete but unresolved uncertainty/pending_human "
            "items remain; downgraded to partial"
        )
    if new_status not in STATUS_ENUM:
        new_status = "partial"
    result["status"] = new_status

    runtime = dict(result.get("runtime") or {})
    runtime["end_ts"] = finished
    runtime["elapsed_s"] = elapsed
    result["runtime"] = runtime

    change_log = list(result.get("change_log") or [])
    change_log.append({"ts": finished, "step": "finalize",
                       "note": "finalized with status=%s" % new_status})
    result["change_log"] = change_log

    # Ensure required top-level keys exist even if the case contract lacked them.
    for key in ("case_id", "status", "sources", "uncertainty",
                "pending_human", "change_log", "errors", "degradations"):
        result.setdefault(key, [] if key not in ("case_id", "status") else None)

    e = write_json(rp, result)
    if e:
        return None, e
    return {
        "case_id": result.get("case_id"),
        "status": new_status,
        "elapsed_s": elapsed,
        "has_unresolved": unresolved,
    }, None
