#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Systematic review core implementation (standard library only).

Design (deliberately different from a "mutate result.json in place" approach):

* The canonical *work files* under /app/work are the single source of truth:
    protocol.json   -- review question, subquestions, search blocks, criteria
    sources.json    -- bibliographic records (identified set)
    screening.jsonl -- two-round decisions (title_abstract, full_text)
    quality.jsonl   -- per-study quality appraisal
    evidence.jsonl  -- evidence cards (locator + verbatim quote)
    synthesis.json  -- conclusions / conflicts / gaps
* ``result.json`` is a **derived artifact**: ``finalize`` deterministically
  replays the work files into result.json + review.md + screening_log.json +
  evidence_map.json, so human and machine outputs cannot drift.
* Validation is **write-time** (``record``) and **replay-time**
  (``finalize`` runs every gate *before* assigning status; ``selfcheck``
  re-runs the same gates on the emitted result).

Only the standard library is imported.
"""

import copy
import hashlib
import json
import os
import re
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
VALID_STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

ENCODINGS = ("utf-8-sig", "gb18030", "latin-1")
MAX_FILE_BYTES = 2 * 1024 * 1024

MATERIAL_EXT = {
    ".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".tex", ".bib", ".ris", ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls",
}
SKIP_NAMES = {"case.json", "task.md", "task.txt", "run_state.json"}

DEFAULT_CONTRACT = {
    "review_question": None,
    "subquestions": [],
    "search_strategy": {},
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": {},
    "quality_appraisal": [],
    "synthesis": {},
    "evidence_map": {},
    "uncertainty": {},
}

HONEST_FIELDS = ("status", "sources", "uncertainty", "change_log", "limitations",
                 "errors", "degradations", "pending_human", "runtime_seconds")


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if path and not os.path.isdir(path):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def read_text(path):
    """Read a text file with encoding fallback and a 2MB size cap."""
    errors, degs = [], []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", ["read failed %s: %s" % (path, exc)], []
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1) if size > MAX_FILE_BYTES else fh.read()
    except OSError as exc:
        return "", ["read failed %s: %s" % (path, exc)], []
    if size > MAX_FILE_BYTES:
        degs.append("truncated oversized file %s (%d bytes)" % (os.path.basename(path), size))
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), errors, degs
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return raw.decode("utf-8", errors="replace"), errors, degs
    except Exception as exc:  # pragma: no cover
        errors.append("decode failed %s: %s" % (path, exc))
        return "", errors, degs


def load_json(path):
    """Return (obj, errors, degradations). A *missing* file is not an error."""
    if not os.path.exists(path):
        return None, [], []
    text, errors, degs = read_text(path)
    if not text.strip():
        return None, errors + ["empty json %s" % os.path.basename(path)], degs
    try:
        return json.loads(text), errors, degs
    except Exception as exc:
        return None, errors + ["bad json %s: %s" % (os.path.basename(path), exc)], degs


def read_jsonl(path):
    """Return (list_of_dicts, errors). A missing file yields ([], [])."""
    if not os.path.exists(path):
        return [], []
    items, errors = [], []
    text, terr, _ = read_text(path)
    errors.extend(terr)
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception as exc:
            errors.append("%s:%d bad json line: %s" % (os.path.basename(path), i, exc))
            continue
        if isinstance(obj, dict):
            items.append(obj)
        else:
            errors.append("%s:%d not a json object" % (os.path.basename(path), i))
    return items, errors


def write_json(path, obj):
    try:
        ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return []
    except Exception as exc:
        return ["write failed %s: %s" % (path, exc)]


def write_text(path, text):
    try:
        ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return []
    except Exception as exc:
        return ["write failed %s: %s" % (path, exc)]


def append_jsonl(path, obj):
    try:
        ensure_dir(os.path.dirname(path))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return []
    except Exception as exc:
        return ["append failed %s: %s" % (path, exc)]


def deep_merge(base, payload):
    if not isinstance(base, dict) or not isinstance(payload, dict):
        return copy.deepcopy(payload)
    out = copy.deepcopy(base)
    for key, val in payload.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def slug(text):
    """Deterministic short id derived from a title/text (stable across reruns)."""
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", (text or "").lower()).strip("-")
    s = (s or "id")[:24]
    digest = hashlib.sha1((text or "").encode("utf-8", "ignore")).hexdigest()[:6]
    return "%s-%s" % (s, digest)


# ---------------------------------------------------------------------------
# Input discovery and contract
# ---------------------------------------------------------------------------

def _is_material(name):
    low = name.lower()
    if low in SKIP_NAMES:
        return False
    return os.path.splitext(low)[1] in MATERIAL_EXT or low.endswith(
        (".jsonl", ".csv", ".bib", ".ris"))


def discover_input(input_dir):
    inv = {"input_dir": input_dir, "exists": False, "case_json": None, "task_md": None,
           "materials": [], "other": [], "total_files": 0}
    if not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            low = name.lower()
            entry = {"path": path, "rel": rel, "size": _size(path)}
            if low == "case.json":
                inv["case_json"] = path
            elif low in ("task.md", "task.txt"):
                if not inv["task_md"]:
                    inv["task_md"] = path
            elif _is_material(name):
                inv["materials"].append(entry)
            else:
                inv["other"].append(entry)
    inv["total_files"] = (len(inv["materials"]) + len(inv["other"])
                          + (1 if inv["case_json"] else 0) + (1 if inv["task_md"] else 0))
    return inv


def extract_case_id(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "challenge_id", "challengeId"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _default_for_type(typeinfo):
    t = typeinfo
    if isinstance(typeinfo, dict):
        t = typeinfo.get("type") or typeinfo.get("format") or ""
    t = str(t).lower()
    if "array" in t or t in ("list",):
        return []
    if "object" in t or t in ("dict", "map"):
        return {}
    return None  # scalar (string/number/boolean) -> null means "not yet reported"


def _contract_from_spec(spec):
    props = spec.get("properties") if isinstance(spec, dict) else None
    if isinstance(props, dict) and props:
        return {k: _default_for_type(v) for k, v in props.items()}
    if isinstance(spec, dict):
        return {k: _default_for_type(v) for k, v in spec.items()}
    return copy.deepcopy(DEFAULT_CONTRACT)


def build_contract(case_obj):
    """Discover the task's output_contract keys; fall back to domain defaults."""
    if not isinstance(case_obj, dict):
        return copy.deepcopy(DEFAULT_CONTRACT)
    for key in ("output_contract", "outputContract", "result_schema", "contract"):
        spec = case_obj.get(key)
        if isinstance(spec, dict):
            return _contract_from_spec(spec)
    return copy.deepcopy(DEFAULT_CONTRACT)


def new_skeleton(case_id, contract, note=None):
    """A valid partial result with every contract key filled and honest fields set."""
    result = {"case_id": case_id or "unknown"}
    collisions = [k for k in contract if k in HONEST_FIELDS]
    for field, default in contract.items():
        result[field] = copy.deepcopy(default)
    result["status"] = STATUS_PARTIAL
    result["sources"] = []
    result["uncertainty"] = {}
    result["change_log"] = []
    result["limitations"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime_seconds"] = 0
    if note:
        result["change_log"].append({"ts": iso_now(), "action": "skeleton", "detail": note})
    if collisions:
        result.setdefault("errors", []).append(
            "output_contract keys collide with honest fields: %s (honest values win)" % ", ".join(collisions))
    return result


def input_state(inv, case_obj):
    """Classify input usability: ok | insufficient | input_error."""
    has_material = bool(inv.get("materials"))
    if inv.get("case_json") and not isinstance(case_obj, dict):
        return "input_error"
    if not inv.get("case_json") and not inv.get("task_md") and not has_material:
        return "insufficient"
    return "ok"


# ---------------------------------------------------------------------------
# Canonical work files (intermediate representation) loading
# ---------------------------------------------------------------------------

def load_work(work_dir):
    work, errors = {}, []

    def load(name):
        obj, errs, _ = load_json(os.path.join(work_dir, name))
        errors.extend(errs)
        return obj

    def loadl(name):
        items, errs = read_jsonl(os.path.join(work_dir, name))
        errors.extend(errs)
        return items

    work["protocol"] = load("protocol.json") or {}
    work["sources"] = load("sources.json") or []
    work["synthesis"] = load("synthesis.json") or {}
    work["screening"] = loadl("screening.jsonl")
    work["quality"] = loadl("quality.jsonl")
    work["evidence"] = loadl("evidence.jsonl")
    return work, errors


# ---------------------------------------------------------------------------
# Write-time record validation
# ---------------------------------------------------------------------------

def validate_record(kind, obj):
    errors = []
    if kind == "protocol":
        if not isinstance(obj, dict):
            errors.append("protocol must be a JSON object")
        elif not (obj.get("review_question") or "").strip():
            errors.append("protocol.review_question is required")
    elif kind == "sources":
        if not isinstance(obj, list):
            errors.append("sources must be a JSON array")
        else:
            for i, s in enumerate(obj):
                if not isinstance(s, dict) or not s.get("source_id"):
                    errors.append("sources[%d] needs source_id" % i)
    elif kind == "synthesis":
        if not isinstance(obj, dict):
            errors.append("synthesis must be a JSON object")
    elif kind == "screening":
        if not isinstance(obj, dict):
            errors.append("screening event must be an object")
        else:
            if obj.get("round") not in ("title_abstract", "full_text"):
                errors.append("round must be title_abstract|full_text")
            if not obj.get("source_id"):
                errors.append("source_id is required")
            if obj.get("decision") not in ("include", "exclude", "unclear"):
                errors.append("decision must be include|exclude|unclear")
            if obj.get("decision") == "exclude" and not (obj.get("reason") and obj.get("criterion_id")):
                errors.append("exclude requires reason + criterion_id")
    elif kind == "quality":
        if not isinstance(obj, dict):
            errors.append("quality event must be an object")
        else:
            if not obj.get("source_id"):
                errors.append("source_id is required")
            if not isinstance(obj.get("dimensions"), list) or not obj.get("dimensions"):
                errors.append("dimensions must be a non-empty array")
            if not obj.get("overall"):
                errors.append("overall is required")
            for d in (obj.get("dimensions") or []):
                if not isinstance(d, dict) or not d.get("dim") or not d.get("score"):
                    errors.append("each dimension needs dim+score")
                if not d.get("basis_locator") or not d.get("quote"):
                    errors.append("each dimension needs basis_locator+quote")
    elif kind == "evidence":
        if not isinstance(obj, dict):
            errors.append("evidence event must be an object")
        else:
            for f in ("evidence_id", "source_id", "locator", "quote"):
                if not obj.get(f):
                    errors.append("evidence.%s is required" % f)
    else:
        errors.append("unknown kind: %s" % kind)
    return errors


# ---------------------------------------------------------------------------
# Validation (replay-time gates; order mirrors the review pipeline)
# ---------------------------------------------------------------------------

def compute_prisma(sources, screening, quality):
    dup_ids = set()
    for r in screening:
        if r.get("duplicate_of"):
            dup_ids.add(r.get("source_id"))
    ta = [r for r in screening if r.get("round") == "title_abstract"]
    ft = [r for r in screening if r.get("round") == "full_text"]
    ta_excluded = [r for r in ta if r.get("decision") == "exclude"]
    ft_excluded = [r for r in ft if r.get("decision") == "exclude"]
    included = [r for r in ft if r.get("decision") == "include"]

    def count_reasons(records):
        counts = {}
        for r in records:
            cid = r.get("criterion_id") or "unspecified"
            counts[cid] = counts.get(cid, 0) + 1
        return counts

    return {
        "identified": len(sources),
        "duplicates_removed": len(dup_ids),
        "title_abstract_screened": len(ta),
        "title_abstract_excluded": len(ta_excluded),
        "full_text_assessed": len(ft),
        "full_text_excluded": len(ft_excluded),
        "included": len(included),
        "quality_appraised": len(quality),
        "excluded_reasons": count_reasons(ta_excluded + ft_excluded),
    }


def validate_work(work, contract):
    report = {"ok": True, "checks": [], "errors": [], "warnings": []}
    proto = work.get("protocol") or {}
    sources = work.get("sources") or []
    screening = work.get("screening") or []
    quality = work.get("quality") or []
    evidence = work.get("evidence") or []
    synthesis = work.get("synthesis") or {}

    def check(cid, label, ok, detail=""):
        report["checks"].append({"id": cid, "label": label, "pass": bool(ok), "detail": detail})
        if not ok:
            report["ok"] = False

    # 1. protocol
    check("protocol", "review_question 非空", bool((proto or {}).get("review_question")))
    subs = (proto or {}).get("subquestions") or []
    check("subquestions", "subquestions >= 1", len(subs) >= 1, "count=%d" % len(subs))
    qbs = ((proto or {}).get("search_strategy") or {}).get("query_blocks") or []
    check("query_blocks", "query_blocks 非空", len(qbs) >= 1, "count=%d" % len(qbs))
    qb_ok = all(isinstance(q, dict) and q.get("database") and q.get("query") and q.get("intent")
                for q in qbs)
    check("query_blocks_detail", "每个检索块含 database+query+intent", qb_ok)
    inc = (proto or {}).get("inclusion_criteria") or []
    exc = (proto or {}).get("exclusion_criteria") or []
    check("criteria", "纳/排条款均 >=1", len(inc) >= 1 and len(exc) >= 1,
          "in=%d ex=%d" % (len(inc), len(exc)))
    clause_ok = all(isinstance(c, dict) and c.get("id") and c.get("clause") for c in inc + exc)
    check("criteria_clauses", "每条条款含 id+clause", clause_ok)

    # 2. screening (two rounds)
    ta = [r for r in screening if r.get("round") == "title_abstract"]
    ft = [r for r in screening if r.get("round") == "full_text"]
    check("screening_ta", "题录级筛选记录存在", len(ta) >= 1, "count=%d" % len(ta))
    ta_exc = [r for r in ta if r.get("decision") == "exclude"]
    check("screening_ta_reasons", "题录级排除含 reason+criterion_id",
          all(r.get("reason") and r.get("criterion_id") for r in ta_exc))
    check("screening_ft", "全文级筛选记录存在", len(ft) >= 1, "count=%d" % len(ft))
    ft_exc = [r for r in ft if r.get("decision") == "exclude"]
    check("screening_ft_reasons", "全文级排除含 reason+criterion_id",
          all(r.get("reason") and r.get("criterion_id") for r in ft_exc))
    seen = {}
    for r in screening:
        key = (r.get("source_id"), r.get("round"))
        if key in seen and r.get("source_id"):
            report["warnings"].append("duplicate screening record for %s" % (key,))
        seen[key] = True
    included_ids = [r.get("source_id") for r in ft if r.get("decision") == "include"]
    check("screening_included", "至少一篇纳入", len(included_ids) >= 1, "count=%d" % len(included_ids))

    # 3. quality appraisal
    q_src = {q.get("source_id") for q in quality}
    missing_q = [s for s in included_ids if s not in q_src]
    check("quality_coverage", "每个纳入研究都有质量评价", not missing_q, "missing=%s" % missing_q)
    q_dim_ok = all(isinstance(q, dict) and isinstance(q.get("dimensions"), list)
                   and q.get("dimensions") for q in quality)
    check("quality_dims", "质量评价含维度列表", q_dim_ok)
    check("quality_overall", "质量评价含总体等级", all(q.get("overall") for q in quality))
    dim_ok = all(
        all(d.get("dim") and d.get("score") and d.get("basis_locator") and d.get("quote")
            for d in q.get("dimensions", []))
        for q in quality)
    check("quality_basis", "每维度含 basis_locator+quote", dim_ok)

    # 4. evidence cards
    check("evidence_present", "证据卡非空", len(evidence) >= 1, "count=%d" % len(evidence))
    check("evidence_fields", "每证据卡含 locator+quote",
          all(e.get("evidence_id") and e.get("source_id") and e.get("locator") and e.get("quote")
              for e in evidence))
    ev_ids = {e.get("evidence_id") for e in evidence}
    check("evidence_unique", "证据 id 唯一", len(ev_ids) == len(evidence))

    # 5. synthesis
    concl = synthesis.get("conclusions") or []
    check("synthesis_conclusions", "综合结论非空", len(concl) >= 1, "count=%d" % len(concl))
    check("synthesis_stmt", "每条结论含 statement", all(c.get("statement") for c in concl))
    dangling = []
    for c in concl:
        for eid in (c.get("evidence_ids") or []):
            if eid not in ev_ids:
                dangling.append(eid)
    check("synthesis_backlink", "结论 evidence_ids 命中证据表", not dangling, "dangling=%s" % dangling)
    conflicts = synthesis.get("conflicts") or []
    check("conflicts", "冲突逐条并列 >=2 来源",
          all(isinstance(f, dict) and len(f.get("sources") or []) >= 2 for f in conflicts),
          "count=%d" % len(conflicts))
    gaps = synthesis.get("gaps") or []
    check("gaps", "研究空白落位到具体缺口",
          all(isinstance(g, dict) and g.get("description") and g.get("kind") for g in gaps),
          "count=%d" % len(gaps))

    # 6. PRISMA recompute
    prisma = compute_prisma(sources, screening, quality)
    report["prisma"] = prisma
    check("prisma", "PRISMA 计数与筛选记录一致",
          prisma["included"] == len(included_ids) and prisma["full_text_assessed"] == len(ft))

    report["stage_status"] = {
        "protocol": bool((proto or {}).get("review_question")),
        "screening": len(screening) > 0,
        "quality": len(quality) > 0,
        "evidence": len(evidence) > 0,
        "synthesis": len(concl) > 0,
    }
    return report


def assign_status(report, state):
    if state == "input_error":
        return STATUS_INPUT_ERROR
    if state == "insufficient":
        return STATUS_INSUFFICIENT
    if report.get("ok"):
        return STATUS_OK
    return STATUS_PARTIAL


# ---------------------------------------------------------------------------
# Deterministic replay into final artifacts
# ---------------------------------------------------------------------------

def build_evidence_map(evidence):
    m = {}
    for e in evidence:
        if isinstance(e, dict) and e.get("evidence_id"):
            m[e["evidence_id"]] = e
    return {k: m[k] for k in sorted(m)}


def _build_uncertainty(work):
    synthesis = work.get("synthesis") or {}
    unc = {
        "conflicts": synthesis.get("conflicts") or [],
        "gaps": synthesis.get("gaps") or [],
        "biases": synthesis.get("biases") or [],
        "unresolved": [c.get("id") for c in (synthesis.get("conflicts") or [])
                       if c.get("resolution") == "undetermined"],
        "abstract_only_sources": [],
        "extraction_failed_sources": [],
    }
    for s in work.get("sources") or []:
        flags = s.get("flags") or []
        if "abstract_only" in flags:
            unc["abstract_only_sources"].append(s.get("source_id"))
        if "extraction_failed" in flags:
            unc["extraction_failed_sources"].append(s.get("source_id"))
    return unc


def _build_pending(work, report, state):
    pending = []
    if state == "insufficient":
        pending.append("缺少可用输入材料，无法进行系统性综述")
    elif state == "input_error":
        pending.append("case.json 无法解析，需人工修复输入")
    stage = report.get("stage_status") or {}
    if not stage.get("protocol"):
        pending.append("尚未完成问题拆解与检索协议（protocol.json）")
    if not stage.get("screening"):
        pending.append("尚未完成题录/全文两轮筛选（screening.jsonl）")
    if not stage.get("quality"):
        pending.append("尚未完成纳入研究质量评价（quality.jsonl）")
    if not stage.get("evidence"):
        pending.append("尚未抽取证据卡（evidence.jsonl）")
    if not stage.get("synthesis"):
        pending.append("尚未完成综合结论/冲突/空白（synthesis.json）")
    for f in (work.get("synthesis") or {}).get("conflicts") or []:
        if f.get("resolution") == "undetermined":
            pending.append("冲突 %s 无法裁定，保留并列证据" % (f.get("id") or "?"))
    return pending


def _fill_artifact_hints(result, synthesis):
    """Populate common 'main artifact' contract keys from the synthesis, when present.

    Only fills keys that the discovered contract already declared (their defaults
    are [] / {} / null), so unknown contract keys are never invented.
    """
    concl = (synthesis or {}).get("conclusions") or []
    summary_text = "；".join(c.get("statement") or "" for c in concl)
    if "conclusions" in result and isinstance(result.get("conclusions"), list) and not result["conclusions"]:
        result["conclusions"] = list(concl)
    for key in ("summary", "answer"):
        if key in result and result.get(key) is None:
            result[key] = summary_text or None
    if "result" in result and isinstance(result.get("result"), dict) and not result["result"]:
        result["result"] = dict(synthesis or {})


def assemble_result(work, case_id, contract, status, report, runtime,
                    errors, degradations, limitations, pending):
    result = new_skeleton(case_id, contract, note=None)
    proto = work.get("protocol") or {}
    sources = work.get("sources") or []
    screening = work.get("screening") or []
    quality = work.get("quality") or []
    evidence = work.get("evidence") or []
    synthesis = work.get("synthesis") or {}

    result["status"] = status
    result["review_question"] = proto.get("review_question")
    result["subquestions"] = proto.get("subquestions") or []
    result["search_strategy"] = proto.get("search_strategy") or {}
    result["inclusion_criteria"] = proto.get("inclusion_criteria") or []
    result["exclusion_criteria"] = proto.get("exclusion_criteria") or []
    result["screening_records"] = screening
    result["prisma_flow"] = report.get("prisma") or compute_prisma(sources, screening, quality)
    result["quality_appraisal"] = quality
    result["synthesis"] = synthesis
    result["evidence_map"] = build_evidence_map(evidence)
    result["sources"] = sources
    result["uncertainty"] = _build_uncertainty(work)
    result["pending_human"] = pending
    result["limitations"] = list(limitations)
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    result["runtime_seconds"] = int(runtime)
    result["deliverables"] = {
        "review_md": "review.md",
        "screening_log": "screening_log.json",
        "evidence_map": "evidence_map.json",
    }
    _fill_artifact_hints(result, synthesis)
    result["change_log"].append({
        "ts": iso_now(), "action": "finalize",
        "detail": "deterministic replay: work files -> result.json/ review.md / logs",
    })
    return result


def build_screening_log(sources, screening):
    by_id = {s.get("source_id"): s for s in sources if isinstance(s, dict)}
    rows = []
    for r in sorted(screening, key=lambda x: (str(x.get("source_id")), str(x.get("round")))):
        src = by_id.get(r.get("source_id"), {})
        rows.append({
            "source_id": r.get("source_id"),
            "title": src.get("title"),
            "round": r.get("round"),
            "decision": r.get("decision"),
            "reason": r.get("reason"),
            "criterion_id": r.get("criterion_id"),
            "judge": r.get("judge", "agent"),
            "duplicate_of": r.get("duplicate_of"),
        })
    return rows


def render_review_md(result):
    L = ["# 系统性文献综述", "",
         "> 本文件由 `scripts/main.py finalize` 从规范工作文件确定性重放生成，与 `result.json` 同源。", "",
         "## 概览", "",
         "- 案例：`%s`" % (result.get("case_id") or "unknown"),
         "- 状态：`%s`" % (result.get("status") or STATUS_PARTIAL),
         "- 研究问题：%s" % (result.get("review_question") or "（未填写）"), ""]
    subs = result.get("subquestions") or []
    if subs:
        L.append("### 子问题")
        L.append("")
        for s in subs:
            L.append("- %s" % (s.get("question") or s.get("id") or str(s)))
        L.append("")
    L += ["## 检索策略", ""]
    qbs = (result.get("search_strategy") or {}).get("query_blocks") or []
    if qbs:
        for q in qbs:
            L.append("- **%s**（%s；字段 %s）：`%s`" % (
                q.get("id") or "?", q.get("database") or "?",
                ", ".join(q.get("fields") or []) or "默认", q.get("query") or ""))
            L.append("  - 意图：%s" % (q.get("intent") or "（未说明）"))
        L.append("")
    else:
        L += ["（未提供检索式）", ""]
    L += ["## 纳入/排除标准", ""]
    for c in (result.get("inclusion_criteria") or []):
        L.append("- 纳入 %s：%s" % (c.get("id") or "?", c.get("clause") or ""))
    for c in (result.get("exclusion_criteria") or []):
        L.append("- 排除 %s：%s" % (c.get("id") or "?", c.get("clause") or ""))
    L.append("")
    L += ["## PRISMA 流程", "", "```text"]
    prisma = result.get("prisma_flow") or {}
    L.append("识别 %(identified)s → 去重 %(duplicates_removed)s → 题录筛选 %(title_abstract_screened)s"
             "（排除 %(title_abstract_excluded)s）→ 全文评估 %(full_text_assessed)s"
             "（排除 %(full_text_excluded)s）→ 纳入 %(included)s" % prisma)
    L += ["```", "", "## 综合结论", ""]
    concl = (result.get("synthesis") or {}).get("conclusions") or []
    for i, c in enumerate(concl, 1):
        L.append("%d. %s（置信度：%s；证据：%s）" % (
            i, c.get("statement") or "", c.get("confidence") or "未标注",
            ", ".join(c.get("evidence_ids") or []) or "无"))
    if not concl:
        L.append("（未形成结论）")
    L.append("")
    L += ["## 冲突证据与研究空白", ""]
    for f in (result.get("synthesis") or {}).get("conflicts") or []:
        srcs = " vs ".join(str(s.get("source_id") or "?") for s in (f.get("sources") or []))
        L.append("- 冲突 %s：%s（%s；裁定：%s）" % (
            f.get("id") or "?", f.get("claim") or "", srcs, f.get("resolution") or "未裁定"))
    for g in (result.get("synthesis") or {}).get("gaps") or []:
        L.append("- 空白 %s（%s）：%s" % (
            g.get("id") or "?", g.get("kind") or "?", g.get("description") or ""))
    L.append("")
    L += ["## 证据回链", ""]
    evmap = result.get("evidence_map") or {}
    for eid in sorted(evmap):
        e = evmap[eid]
        L.append("- `%s`（%s；%s；%s）：%s → “%s”" % (
            eid, e.get("source_id") or "?", e.get("kind") or "?",
            e.get("strength") or "?", e.get("claim") or "", (e.get("quote") or "")[:80]))
    L.append("")
    L += ["## 不确定性与待人工确认", ""]
    unc = result.get("uncertainty") or {}
    L.append("- 冲突 %d 条；空白 %d 条；无法裁定 %d 条" % (
        len(unc.get("conflicts") or []), len(unc.get("gaps") or []), len(unc.get("unresolved") or [])))
    for p in (result.get("pending_human") or []):
        L.append("- 待确认：%s" % p)
    for lim in (result.get("limitations") or []):
        L.append("- 限制：%s" % lim)
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# selfcheck on the emitted result (defense in depth)
# ---------------------------------------------------------------------------

def selfcheck_result(result, contract):
    checks = []

    def check(cid, label, ok, detail=""):
        checks.append({"id": cid, "label": label, "pass": bool(ok), "detail": detail})

    check("case_id", "case_id 非空", bool(result.get("case_id")))
    check("status", "status 合法", result.get("status") in VALID_STATUSES)
    check("status_honest", "status != ok 时列缺口",
          result.get("status") == STATUS_OK or bool(result.get("limitations")
          or result.get("pending_human") or result.get("errors")))
    check("review_question", "review_question 非空", bool(result.get("review_question")))
    check("search", "search_strategy 非空", bool(result.get("search_strategy")))
    check("criteria", "纳排标准非空",
          bool(result.get("inclusion_criteria")) and bool(result.get("exclusion_criteria")))
    check("screening", "筛选记录存在", bool(result.get("screening_records")))
    exc_ok = all((r.get("reason") and r.get("criterion_id"))
                 for r in (result.get("screening_records") or [])
                 if r.get("decision") == "exclude")
    check("exclude_reasons", "排除均含 reason+criterion_id", exc_ok)
    evmap = result.get("evidence_map") or {}
    ev_ok = bool(evmap) and all(e.get("locator") and e.get("quote") for e in evmap.values())
    check("evidence_map", "证据表逐条 locator+quote", ev_ok)
    concl = (result.get("synthesis") or {}).get("conclusions") or []
    ev_ids = set(evmap.keys())
    backlink = all(all(eid in ev_ids for eid in (c.get("evidence_ids") or [])) for c in concl)
    check("backlink", "结论 evidence_ids 命中证据表", backlink)
    check("conflicts", "冲突逐条并列 >=2 来源",
          all(len(f.get("sources") or []) >= 2
              for f in ((result.get("synthesis") or {}).get("conflicts") or [])))
    check("gaps", "研究空白已记录", bool((result.get("synthesis") or {}).get("gaps")))
    check("change_log", "change_log 非空", bool(result.get("change_log")))
    check("sources", "sources 非空", bool(result.get("sources")))
    missing = [k for k in contract if k not in result]
    check("contract_complete", "output_contract 字段齐全", not missing, "missing=%s" % missing)
    return checks
