"""Shared implementation for the research selection & novelty review skill.

All mechanics live here; `main.py` is a thin CLI.  Only the Python standard
library is used.  The runtime is expected to provide `pdftotext`; when it is
missing the pipeline degrades gracefully instead of failing.

Intermediate representations (the verifiability layer):
  /app/work/plan.json           research question + search protocol
  /app/work/evidence_index.json material inventory produced by discovery
  /app/work/evidence_model.json evidence records with locator + verbatim quote
  /app/work/figure_model.json   comparison matrix, novelty claims, feasibility
  /app/work/synthesis.json      conclusions, gaps, boundary, conflicts, uncertainty
  /app/work/change_log.json     append-only change record
  /app/work/state.json          run state (started_at, phase)

`finalize` deterministically merges these artifacts into the output contract.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024
SUPPORTED_TEXT_EXT = {".md", ".txt", ".json", ".csv", ".xml", ".html", ".htm",
                      ".yaml", ".yml", ".bib", ".tsv", ".rst"}
PDF_EXT = ".pdf"

# Top-level contract keys that must always be present in result.json.
STANDARD_CONTRACT = [
    "case_id", "status", "research_question", "search_protocol",
    "prior_work", "novelty", "feasibility", "evidence_matrix", "conflicts",
    "synthesis", "sources", "uncertainty", "pending_human", "errors",
    "degradations", "limitations", "change_log", "runtime",
]

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

NOVELTY_TYPES = ("method", "data", "scenario_transfer", "conclusion_revision")
NOVELTY_VERDICTS = ("novel", "covered", "partially_covered", "unverifiable")
PREMISE_STATUSES = ("reported", "assumed", "missing", "conflicting")
FEASIBILITY_ASPECTS = ("data", "compute", "experiment_condition",
                       "time_window", "personnel")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_hash(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def resolve_dir(value, default):
    return value if value else default


def ensure_dirs(output, work):
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.join(work, "extracted"), exist_ok=True)
    os.makedirs(os.path.join(work, "fragments"), exist_ok=True)
    os.makedirs(output, exist_ok=True)


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def write_text_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def read_json_tolerant(path):
    """Return (value, error_message).  Never raises on unreadable/bad JSON."""
    if not os.path.exists(path):
        return None, "missing file: %s" % path
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, "cannot read %s: %s" % (path, exc)
    text = decode_bytes(raw, path)
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001 - deliberate tolerant parse
        return None, "bad JSON in %s: %s" % (path, exc)


def decode_bytes(raw, path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def read_text_tolerant(path, max_bytes=MAX_FILE_BYTES):
    """Return (text, encoding, error)."""
    if not os.path.exists(path):
        return None, None, "missing file: %s" % path
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return None, None, "file exceeds %d bytes" % max_bytes
        raw = open(path, "rb").read()
    except OSError as exc:
        return None, None, "cannot read %s: %s" % (path, exc)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8-replace", None


def load_change_log(work):
    path = os.path.join(work, "change_log.json")
    value, err = read_json_tolerant(path)
    if err or not isinstance(value, dict):
        return []
    return value.get("entries", []) if isinstance(value.get("entries"), list) else []


def append_change(work, action, detail):
    path = os.path.join(work, "change_log.json")
    entries = load_change_log(work)
    entries.append({"time": now_iso(), "action": action, "detail": detail})
    write_json_atomic(path, {"entries": entries})
    return entries


def load_state(work):
    path = os.path.join(work, "state.json")
    value, err = read_json_tolerant(path)
    if err or not isinstance(value, dict):
        return {}
    return value


def save_state(work, state):
    write_json_atomic(os.path.join(work, "state.json"), state)


def case_identity(input_root, case_data):
    """Stable case_id from input; falls back to a hash of the input root."""
    if isinstance(case_data, dict):
        for key in ("case_id", "id", "challenge_id", "caseId"):
            val = case_data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return "unresolved-" + short_hash(os.path.abspath(input_root))


def load_contract(case_data):
    """Echo the input output_contract verbatim, else fall back to standard."""
    if isinstance(case_data, dict) and isinstance(case_data.get("output_contract"), dict):
        return dict(case_data["output_contract"])
    return {k: None for k in STANDARD_CONTRACT if k not in ("case_id", "status")}


def iter_input_files(input_root):
    """Yield (relative_path, absolute_path) for material files only."""
    if not os.path.isdir(input_root):
        return
    for dirpath, dirnames, filenames in os.walk(input_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            abs_path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(abs_path, input_root).replace(os.sep, "/")
            if rel_path in ("case.json", "task.md"):
                continue
            yield rel_path, abs_path


def pdf_to_text(pdf_path, out_txt):
    """Extract text with pdftotext; return (ok, note)."""
    cmd = ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, out_txt]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=120)
    except FileNotFoundError:
        return False, "pdftotext not found"
    except OSError as exc:
        return False, "pdftotext failed: %s" % exc
    except subprocess.TimeoutExpired:
        return False, "pdftotext timeout"
    if proc.returncode != 0:
        return False, "pdftotext exit %d" % proc.returncode
    if os.path.exists(out_txt) and os.path.getsize(out_txt) > 0:
        return True, ""
    return False, "empty extraction"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(input_root, output, work, argv_extra=None):
    ensure_dirs(output, work)
    input_root = resolve_dir(input_root, DEFAULT_INPUT)
    output = resolve_dir(output, DEFAULT_OUTPUT)
    work = resolve_dir(work, DEFAULT_WORK)

    case_data = None
    case_err = None
    fatal_input = False
    case_path = os.path.join(input_root, "case.json")
    if not os.path.isdir(input_root):
        fatal_input = True
        case_err = "input directory missing: %s" % input_root
    elif not os.path.exists(case_path):
        fatal_input = True
        case_err = "case.json missing"
    else:
        case_data, case_err = read_json_tolerant(case_path)
        # A bad-JSON case.json is a degradation, not a fatal input error.

    case_id = case_identity(input_root, case_data)
    contract = load_contract(case_data)

    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract,
    }
    # Every output_contract key also materialises at top level as null/[].
    for key, val in contract.items():
        skeleton[key] = val if val is not None else ([] if _list_key(key) else None)
    for key in STANDARD_CONTRACT:
        skeleton.setdefault(key, [] if _list_key(key) else None)
    skeleton["status"] = "partial"
    skeleton["runtime"] = {"started_at": now_iso(), "finished_at": None,
                           "seconds": None, "phase": "init"}
    skeleton["change_log"] = []

    errors = []
    if case_err:
        errors.append(case_err)
    if fatal_input:
        skeleton["status"] = "input_error"
    skeleton["errors"] = errors
    skeleton["degradations"] = []
    skeleton["limitations"] = []

    write_json_atomic(os.path.join(output, "result.json"), skeleton)

    # Intermediate representation skeletons, written before any analysis.
    plan = {
        "case_id": case_id,
        "status": "planned",
        "research_question": {
            "original": None, "object": None, "variables": [],
            "baseline": None, "expected_outcome": None,
            "falsification_condition": None, "testable_statement": None,
            "status": "missing", "gaps": [],
        },
        "search_protocol": {
            "queries": [], "inclusion_criteria": [], "exclusion_criteria": [],
            "scope": None, "screening_steps": [], "limitations": [],
        },
        "comparison_dimensions": ["task", "method", "data", "evaluation", "conclusion"],
        "novelty_taxonomy": list(NOVELTY_TYPES),
        "feasibility_aspects": list(FEASIBILITY_ASPECTS),
        "budget": {"seconds": 900, "model_requests": 64,
                   "max_tokens_per_response": 16384},
    }
    write_json_atomic(os.path.join(work, "plan.json"), plan)
    write_json_atomic(os.path.join(work, "evidence_index.json"),
                      {"case_id": case_id, "materials": [], "locator_scheme":
                       "relative_path then #L<line> in cited text file"})
    write_json_atomic(os.path.join(work, "evidence_model.json"), {"records": []})
    write_json_atomic(os.path.join(work, "figure_model.json"),
                      {"comparison_matrix": [], "novelty_claims": [],
                       "feasibility_premises": []})
    write_json_atomic(os.path.join(work, "synthesis.json"),
                      {"conclusions": [], "research_gaps": [],
                       "boundary_statement": None, "conflicts": [],
                       "uncertainty": {"biases": [], "gaps": [],
                                       "undetermined_items": []},
                       "pending_human": [], "limitations": []})
    save_state(work, {"started_at": now_iso(), "phase": "init",
                      "case_id": case_id})
    append_change(work, "init", "skeleton + intermediate representations written")

    # Missing input is already an honest terminal state, but we still exit 0.
    return 0


def _list_key(key):
    return key in ("evidence_matrix", "conflicts", "sources", "uncertainty",
                   "pending_human", "errors", "degradations", "limitations",
                   "change_log", "prior_work", "novelty", "feasibility",
                   "search_protocol", "research_question", "synthesis")


def cmd_discover(input_root, output, work, argv_extra=None):
    input_root = resolve_dir(input_root, DEFAULT_INPUT)
    output = resolve_dir(output, DEFAULT_OUTPUT)
    work = resolve_dir(work, DEFAULT_WORK)
    ensure_dirs(output, work)

    if not os.path.isdir(input_root):
        _set_status(output, work, "input_error")
        append_change(work, "discover", "input directory missing")
        return 0

    case_path = os.path.join(input_root, "case.json")
    if not os.path.exists(case_path):
        _set_status(output, work, "input_error")
        append_change(work, "discover", "case.json missing")
        return 0
    case_data, case_err = read_json_tolerant(case_path)
    case_id = case_identity(input_root, case_data)
    contract = load_contract(case_data)

    degradations = []
    errors = []
    if case_err:
        # Bad JSON in case.json: degrade, do not treat as a fatal input error.
        errors.append(case_err)

    materials = []
    n = 0
    for rel_path, abs_path in iter_input_files(input_root):
        n += 1
        sid = "src%02d" % n
        ext = os.path.splitext(abs_path)[1].lower()
        try:
            size = os.path.getsize(abs_path)
        except OSError as exc:
            errors.append("stat %s: %s" % (rel_path, exc))
            continue
        if size > MAX_FILE_BYTES:
            degradations.append("skipped >2MB: %s" % rel_path)
            materials.append({"source_id": sid, "path": rel_path,
                              "type": ext, "bytes": size, "cite_path": None,
                              "text_available": False, "char_count": 0,
                              "note": "skipped: size limit"})
            continue

        record = {"source_id": sid, "path": rel_path, "type": ext,
                  "bytes": size, "cite_path": None, "text_available": False,
                  "char_count": 0, "note": ""}
        if ext == PDF_EXT:
            out_txt = os.path.join(work, "extracted",
                                   os.path.basename(rel_path) + ".txt")
            ok, note = pdf_to_text(abs_path, out_txt)
            if ok:
                text, enc, _ = read_text_tolerant(out_txt, max_bytes=MAX_FILE_BYTES)
                record.update({"cite_path": os.path.join("extracted",
                                os.path.basename(rel_path) + ".txt"),
                               "text_available": True,
                               "char_count": len(text) if text else 0})
            else:
                degradations.append("PDF text unavailable: %s (%s)"
                                    % (rel_path, note))
                record["note"] = "text_unavailable: " + note
        elif ext in SUPPORTED_TEXT_EXT:
            text, enc, err = read_text_tolerant(abs_path, max_bytes=MAX_FILE_BYTES)
            if err:
                degradations.append("read failed: %s (%s)" % (rel_path, err))
                record["note"] = err
            else:
                record.update({"cite_path": rel_path, "text_available": True,
                               "char_count": len(text) if text else 0})
                if ext == ".json" and text:
                    try:
                        json.loads(text)
                    except Exception as exc:  # noqa: BLE001
                        degradations.append("malformed JSON: %s" % rel_path)
                        record["note"] = "malformed JSON: %s" % exc
        else:
            record["note"] = "unsupported extension"
            degradations.append("unsupported extension: %s" % rel_path)
        materials.append(record)

    has_readable = any(m.get("text_available") for m in materials)
    if not materials:
        status = "insufficient_evidence"
    elif not has_readable:
        status = "insufficient_evidence"
    else:
        status = "partial"

    index = {"case_id": case_id, "materials": materials,
             "locator_scheme": "relative_path then #L<line> in cited text file"}
    write_json_atomic(os.path.join(work, "evidence_index.json"), index)
    append_change(work, "discover", "inventoried %d material(s), readable=%s"
                  % (len(materials), has_readable))

    _update_result_fields(output, work, {
        "case_id": case_id,
        "status": status,
        "output_contract": contract,
        "sources": _sources_from(materials),
        "errors": errors,
        "degradations": degradations,
    })
    return 0


def _sources_from(materials):
    out = []
    for m in materials:
        out.append({"source_id": m["source_id"], "path": m["path"],
                    "type": m["type"], "bytes": m["bytes"],
                    "cite_path": m["cite_path"],
                    "text_available": m["text_available"],
                    "char_count": m["char_count"], "note": m["note"]})
    return out


def _set_status(output, work, status):
    _update_result_fields(output, work, {"status": status})


def _update_result_fields(output, work, fields):
    result_path = os.path.join(output, "result.json")
    result, err = read_json_tolerant(result_path)
    if err or not isinstance(result, dict):
        result = {"case_id": None, "status": "partial"}
    result.update(fields)
    if result.get("change_log") is None:
        result["change_log"] = []
    write_json_atomic(result_path, result)


def cmd_record(input_root, output, work, kind, fragment_path):
    work = resolve_dir(work, DEFAULT_WORK)
    output = resolve_dir(output, DEFAULT_OUTPUT)
    ensure_dirs(output, work)

    if kind not in ("plan", "evidence", "figure", "synthesis"):
        sys.stderr.write("unknown kind: %s\n" % kind)
        return 2
    frag, err = read_json_tolerant(fragment_path)
    if err:
        sys.stderr.write(err + "\n")
        return 2

    target = {"plan": "plan.json", "evidence": "evidence_model.json",
              "figure": "figure_model.json", "synthesis": "synthesis.json"}[kind]
    target_path = os.path.join(work, target)
    current, _ = read_json_tolerant(target_path)
    if not isinstance(current, dict):
        current = {}
    if kind == "evidence":
        records = frag.get("records", []) if isinstance(frag, dict) else []
        merged = _dedupe_records(current.get("records", []), records)
        current["records"] = merged
    else:
        current = _deep_merge(current, frag if isinstance(frag, dict) else {})
    write_json_atomic(target_path, current)
    append_change(work, "record:" + kind,
                  "merged %d top-level keys from %s"
                  % (len(frag) if isinstance(frag, dict) else 0, fragment_path))
    return 0


def _dedupe_records(existing, incoming):
    out = list(existing)
    seen = set(r.get("evidence_id") for r in out if isinstance(r, dict)
               and r.get("evidence_id"))
    for rec in incoming:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("evidence_id")
        if rid and rid in seen:
            out = [r for r in out if not (isinstance(r, dict)
                                          and r.get("evidence_id") == rid)]
        if rid:
            seen.add(rid)
        out.append(rec)
    return out


def _deep_merge(base, override):
    out = dict(base)
    for key, val in override.items():
        if (isinstance(val, dict) and isinstance(out.get(key), dict)):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def cmd_finalize(input_root, output, work, argv_extra=None):
    input_root = resolve_dir(input_root, DEFAULT_INPUT)
    output = resolve_dir(output, DEFAULT_OUTPUT)
    work = resolve_dir(work, DEFAULT_WORK)
    ensure_dirs(output, work)

    result, err = read_json_tolerant(os.path.join(output, "result.json"))
    if err or not isinstance(result, dict):
        result = {"case_id": None, "status": "partial"}

    case_data = None
    if os.path.exists(os.path.join(input_root, "case.json")):
        case_data, _ = read_json_tolerant(os.path.join(input_root, "case.json"))
    case_id = result.get("case_id") or case_identity(input_root, case_data)
    contract = result.get("output_contract") or load_contract(case_data)

    plan, _ = read_json_tolerant(os.path.join(work, "plan.json"))
    evidence, _ = read_json_tolerant(os.path.join(work, "evidence_model.json"))
    figure, _ = read_json_tolerant(os.path.join(work, "figure_model.json"))
    synthesis, _ = read_json_tolerant(os.path.join(work, "synthesis.json"))
    index, _ = read_json_tolerant(os.path.join(work, "evidence_index.json"))

    result["case_id"] = case_id
    result["output_contract"] = contract
    for key, val in contract.items():
        if val is not None:
            result.setdefault(key, val)
    for key in STANDARD_CONTRACT:
        result.setdefault(key, [] if _list_key(key) else None)

    if isinstance(plan, dict):
        result["research_question"] = plan.get("research_question")
        result["search_protocol"] = plan.get("search_protocol")

    if isinstance(figure, dict):
        result["prior_work"] = {
            "comparison_matrix": figure.get("comparison_matrix", []),
            "comparable_works": figure.get("comparable_works", []),
            "non_comparable_reasons": figure.get("non_comparable_reasons", []),
            "coverage_notes": figure.get("coverage_notes", []),
        }
        result["novelty"] = {"claims": figure.get("novelty_claims", [])}
        result["feasibility"] = {
            "premises": figure.get("feasibility_premises", []),
            "conditional_conclusion": figure.get("conditional_conclusion"),
        }

    if isinstance(evidence, dict):
        result["evidence_matrix"] = _normalise_evidence(evidence.get("records", []))

    if isinstance(synthesis, dict):
        result["conflicts"] = synthesis.get("conflicts", [])
        result["synthesis"] = {
            "conclusions": synthesis.get("conclusions", []),
            "research_gaps": synthesis.get("research_gaps", []),
            "boundary_statement": synthesis.get("boundary_statement"),
        }
        result["uncertainty"] = synthesis.get("uncertainty",
                                              {"biases": [], "gaps": [],
                                               "undetermined_items": []})
        result["pending_human"] = synthesis.get("pending_human", [])
        result["limitations"] = synthesis.get("limitations", [])

    if isinstance(index, dict) and isinstance(index.get("materials"), list):
        result["sources"] = _sources_from(index["materials"])

    state = load_state(work)
    started = state.get("started_at") or result.get("runtime", {}).get("started_at")
    result["runtime"] = {
        "started_at": started,
        "finished_at": now_iso(),
        "seconds": None,
        "phase": "finalize",
    }
    result["change_log"] = load_change_log(work)

    # Honest status resolution: never upgrade a skeleton past what evidence
    # supports; explicit input errors win, else keep the recorded status.
    if not os.path.isdir(input_root):
        result["status"] = "input_error"
    elif result.get("status") in (None, ""):
        result["status"] = "partial"

    issues = validate_result(result)
    write_json_atomic(os.path.join(output, "result.json"), result)
    report = build_report(result, issues)
    write_text_atomic(os.path.join(output, "report.md"), report)
    append_change(work, "finalize",
                  "merged artifacts; validation issues=%d" % len(issues))

    if issues and result.get("status") == "ok":
        # A contract key is missing: report it but still leave a valid file.
        sys.stderr.write("validation issues:\n")
        for issue in issues:
            sys.stderr.write("  - %s\n" % issue)
        return 2
    return 0


def _normalise_evidence(records):
    out = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        out.append({
            "evidence_id": rec.get("evidence_id"),
            "source_id": rec.get("source_id"),
            "locator": rec.get("locator"),
            "quote": rec.get("quote"),
            "claim": rec.get("claim"),
            "field": rec.get("field"),
            "status": rec.get("status", "reported"),
        })
    return out


def validate_result(result):
    issues = []
    if not result.get("case_id"):
        issues.append("case_id missing")
    if result.get("status") not in STATUSES:
        issues.append("invalid status: %r" % result.get("status"))
    for key in ("research_question", "search_protocol", "prior_work",
                "novelty", "feasibility", "evidence_matrix", "sources",
                "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing contract key: %s" % key)
    for key in ("novelty", "feasibility"):
        val = result.get(key)
        if isinstance(val, dict) and isinstance(val.get("claims"), list):
            for claim in val["claims"]:
                if not claim.get("verdict") or not claim.get("covered_by"):
                    issues.append("novelty claim missing verdict/covered_by: %s"
                                  % claim.get("claim_id"))
        if isinstance(val, dict) and isinstance(val.get("premises"), list):
            for prem in val["premises"]:
                if prem.get("status") not in PREMISE_STATUSES:
                    issues.append("feasibility premise missing status: %s"
                                  % prem.get("premise_id"))
    return issues


def build_report(result, issues=None):
    issues = issues or []
    lines = [
        "# 科研选题与创新性评估报告",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- finished_at: %s" % result.get("runtime", {}).get("finished_at"),
        "",
    ]
    rq = result.get("research_question") or {}
    lines += ["## 1. 研究问题",
              "",
              "- 可检验陈述：%s" % (rq.get("testable_statement") or "未给出"),
              "- 研究对象：%s" % (rq.get("object") or "未给出"),
              "- 比较基线：%s" % (rq.get("baseline") or "未给出"),
              "- 可证伪条件：%s" % (rq.get("falsification_condition") or "未给出"),
              ""]
    prior = result.get("prior_work") or {}
    lines += ["## 2. 已有工作比较",
              "",
              "- 比较矩阵行数：%d" % len(prior.get("comparison_matrix", [])),
              "- 不可直接比较原因：%d 条" % len(prior.get("non_comparable_reasons", [])),
              ""]
    novelty = result.get("novelty") or {}
    lines += ["## 3. 创新点判定", ""]
    for c in novelty.get("claims", []):
        lines.append("- %s：`%s`（%s）" % (c.get("claim_id"),
                     c.get("statement"), c.get("verdict")))
    lines.append("")
    feas = result.get("feasibility") or {}
    lines += ["## 4. 可行性前提", ""]
    for p in feas.get("premises", []):
        lines.append("- %s：`%s`（%s）" % (p.get("premise_id"),
                     p.get("statement"), p.get("status")))
    lines.append("")
    lines += ["## 5. 证据与不确定性",
              "",
              "- 证据记录：%d 条" % len(result.get("evidence_matrix", [])),
              "- 冲突记录：%d 条" % len(result.get("conflicts", [])),
              "- 研究空白：%d 条" % len((result.get("synthesis") or {})
                                        .get("research_gaps", [])),
              "- 无法确定项：%d 条" % len((result.get("uncertainty") or {})
                                          .get("undetermined_items", [])),
              ""]
    if issues:
        lines += ["## 校验问题", ""]
        for issue in issues:
            lines.append("- %s" % issue)
        lines.append("")
    lines.append("> 本报告由 finalize 依据中间产物确定性合并生成；每条结论应能通过 "
                 "evidence_matrix 的 locator 与逐字 quote 回到材料。")
    return "\n".join(lines)
