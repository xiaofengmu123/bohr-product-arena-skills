"""Deterministic core library for the verifiable systematic-review skill.

Standard library only. Design invariant: every deliverable under /app/output is
a pure function of (input files + work artifacts). All JSON is serialized with
sorted keys and fixed separators, evidence IDs are content-addressed, and no
wall-clock time is written into result.json, so two runs over the same input
produce byte-identical output. Measured telemetry (runtime, request counts) is
kept separately in /app/work/run_state.json.
"""

import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

CASE_ID_KEYS = ("case_id", "caseId", "id", "armiId", "challenge_id")

MATERIAL_EXTS = {
    ".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html",
    ".htm", ".tex", ".bib", ".ris", ".docx", ".xlsx", ".pptx",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff",
}

MAX_BYTES = 2 * 1024 * 1024

# Domain fields of the output contract. Unknown contract keys from case.json's
# output_contract are added on top of this and preserved verbatim.
DEFAULT_CONTRACT = {
    "review_question": "",
    "subquestions": [],
    "search_strategy": {"query_blocks": []},
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": {},
    "quality_appraisal": [],
    "synthesis": {"conclusions": [], "conflicts": [], "gaps": []},
    "evidence_map": [],
    "uncertainty": {},
}

RECORD_SEPARATOR = "\x1f"


def epoch_now():
    """Wall-clock seconds; used only in run_state.json telemetry, never in result.json."""
    return time.time()


def iso_now():
    """ISO timestamp for run_state.json telemetry."""
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def canonical_dump(obj):
    """Deterministic JSON text: sorted keys, stable separators, UTF-8."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def write_json(path, obj):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(canonical_dump(obj))
            fh.write("\n")
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def write_text(path, text):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def decode_bytes(raw):
    """Decode with utf-8-sig -> gb18030 -> latin-1 fallback. Returns (text, encoding, degraded)."""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc), enc, False
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace"), "latin-1", True


def load_text(path, max_bytes=MAX_BYTES):
    if not os.path.exists(path):
        return "", ["missing file %s" % path], []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return "", ["cannot stat %s: %s" % (path, exc)], []
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes)
    except OSError as exc:
        return "", ["cannot read %s: %s" % (path, exc)], []
    text, enc, bad = decode_bytes(raw)
    degradations = []
    if bad:
        degradations.append("lossy decode %s (%s)" % (path, enc))
    if size > max_bytes:
        degradations.append("truncated to %d of %d bytes" % (max_bytes, size))
    return text, [], degradations


def load_json(path, max_bytes=MAX_BYTES):
    text, errs, degs = load_text(path, max_bytes)
    if errs:
        return None, errs, degs
    if not text.strip():
        return None, ["empty JSON file %s" % path], degs
    try:
        return json.loads(text), [], degs
    except Exception as exc:
        return None, ["bad JSON %s: %s" % (path, exc)], degs


def read_jsonl(path):
    if not os.path.exists(path):
        return [], [], []
    text, errs, degs = load_text(path)
    rows = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception as exc:
            errs.append("bad JSONL %s:%d %s" % (path, lineno, exc))
    return rows, errs, degs


def deep_merge(base, patch):
    out = copy.deepcopy(base) if isinstance(base, (dict, list)) else base
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    for key, value in patch.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def stable_id(scope, *parts):
    h = hashlib.sha256()
    h.update(scope.encode("utf-8"))
    for part in parts:
        h.update(RECORD_SEPARATOR.encode("utf-8"))
        h.update((part if isinstance(part, str) else str(part)).encode("utf-8"))
    return h.hexdigest()[:12]


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return ""
    for key in CASE_ID_KEYS:
        value = case_obj.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def build_contract(case_obj):
    """Start from the domain default, then add every unknown output_contract key with
    a type-appropriate empty value so no key is renamed or dropped."""
    contract = copy.deepcopy(DEFAULT_CONTRACT)
    if not isinstance(case_obj, dict):
        return contract
    oc = case_obj.get("output_contract")
    if not isinstance(oc, dict):
        return contract
    for key, value in oc.items():
        if key in contract:
            continue
        if isinstance(value, dict):
            contract[key] = {}
        elif isinstance(value, list):
            contract[key] = []
        else:
            contract[key] = None
    return contract


def new_skeleton(case_id, contract, status=STATUS_PARTIAL, note=""):
    result = copy.deepcopy(contract)
    result["case_id"] = case_id or "unknown"
    result["status"] = status
    result["sources"] = []
    result["change_log"] = []
    result["pending_human"] = []
    result["limitations"] = []
    result["errors"] = []
    result["degradations"] = []
    result["runtime_seconds"] = None
    result["outputs"] = {}
    if note:
        result["change_log"] = [{"seq": 0, "action": "skeleton", "note": note}]
    return result


def discover_input(input_dir):
    inv = {
        "input_dir": input_dir,
        "exists": os.path.isdir(input_dir),
        "total_files": 0,
        "case_json": None,
        "task_md": None,
        "materials": [],
        "other": [],
    }
    if not inv["exists"]:
        return inv
    files = []
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            files.append({"path": path, "rel": rel, "size": size})
    files.sort(key=lambda f: f["rel"])
    inv["total_files"] = len(files)
    for f in files:
        base = os.path.basename(f["rel"]).lower()
        ext = os.path.splitext(base)[1].lower()
        if base == "case.json":
            inv["case_json"] = f["path"]
        elif base == "task.md":
            inv["task_md"] = f["path"]
        elif ext in MATERIAL_EXTS:
            inv["materials"].append(f)
        else:
            inv["other"].append(f)
    return inv


def read_status_override(work_dir):
    path = os.path.join(work_dir, "status_override.txt")
    if not os.path.exists(path):
        return None
    text, errs, _ = load_text(path)
    if errs:
        return None
    for line in text.splitlines():
        status = line.strip()
        if status in STATUSES:
            return status
    return None


def read_work_data(work_dir):
    data = {"protocol": {}, "screening": [], "quality": [], "evidence": [], "synthesis": {}}
    errors, degradations = [], []
    proto, e1, d1 = load_json(os.path.join(work_dir, "protocol.json"))
    if isinstance(proto, dict):
        data["protocol"] = proto
    errors.extend(e1)
    degradations.extend(d1)
    for name, key in (("screening.jsonl", "screening"),
                      ("quality.jsonl", "quality"),
                      ("evidence.jsonl", "evidence")):
        rows, e2, d2 = read_jsonl(os.path.join(work_dir, name))
        data[key] = rows
        errors.extend(e2)
        degradations.extend(d2)
    synth, e3, d3 = load_json(os.path.join(work_dir, "synthesis.json"))
    if isinstance(synth, dict):
        data["synthesis"] = synth
    errors.extend(e3)
    degradations.extend(d3)
    return data, errors, degradations


def compute_prisma(screening):
    """Recount the PRISMA flow from raw screening records so counts are always re-derivable."""
    seen = set()
    identified, duplicates = 0, 0
    for rec in screening:
        sid = rec.get("source_id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        identified += 1
        if rec.get("decision") == "duplicate" or rec.get("duplicate_of"):
            duplicates += 1
    title_rows = [r for r in screening
                  if r.get("stage") == "title"
                  and not (r.get("decision") == "duplicate" or r.get("duplicate_of"))]
    ft_rows = [r for r in screening if r.get("stage") == "fulltext"]
    return {
        "identified": identified,
        "duplicates_removed": duplicates,
        "screened_title_abstract": len({r.get("source_id") for r in title_rows}),
        "excluded_title_abstract": len({r.get("source_id") for r in title_rows if r.get("decision") == "exclude"}),
        "assessed_fulltext": len({r.get("source_id") for r in ft_rows}),
        "excluded_fulltext": len({r.get("source_id") for r in ft_rows if r.get("decision") == "exclude"}),
        "included": len({r.get("source_id") for r in ft_rows if r.get("decision") == "include"}),
    }


def derive_status(data, material_count, errors):
    proto = data.get("protocol") or {}
    screening = data.get("screening") or []
    quality = data.get("quality") or []
    evidence = data.get("evidence") or []
    synth = data.get("synthesis") or {}
    includes = {r.get("source_id") for r in screening
                if r.get("stage") == "fulltext" and r.get("decision") == "include"}
    qa_ids = {q.get("source_id") for q in quality}
    has_protocol = bool(proto.get("review_question")) and bool(proto.get("subquestions"))
    has_search = bool((proto.get("search_strategy") or {}).get("query_blocks"))
    has_criteria = bool(proto.get("inclusion_criteria")) and bool(proto.get("exclusion_criteria"))
    has_screening = bool(screening)
    has_qa = bool(includes) and includes.issubset(qa_ids)
    has_synthesis = bool((synth.get("conclusions") or []))
    has_evidence = bool(evidence)
    if material_count == 0 and not has_protocol and not has_screening:
        return STATUS_INSUFFICIENT
    if has_protocol and has_search and has_criteria and has_screening and has_qa and has_synthesis and has_evidence:
        return STATUS_OK
    return STATUS_PARTIAL


def build_evidence_map(evidence):
    out = []
    for item in evidence:
        entry = copy.deepcopy(item)
        entry.setdefault(
            "evidence_id",
            "ev-" + stable_id("ev", entry.get("source_id", ""), entry.get("locator", ""), entry.get("quote", "")),
        )
        out.append(entry)
    out.sort(key=lambda e: e["evidence_id"])
    return out


def build_synthesis(synthesis, evidence_map):
    ev_ids = {e["evidence_id"] for e in evidence_map}
    conclusions = []
    for item in (synthesis.get("conclusions") or []):
        concl = copy.deepcopy(item)
        concl.setdefault("id", "cl-" + stable_id("cl", concl.get("claim", "")))
        concl.setdefault("evidence_ids", [])
        concl["evidence_ids"] = [x for x in concl["evidence_ids"] if x in ev_ids]
        conclusions.append(concl)
    return {
        "conclusions": conclusions,
        "conflicts": list(synthesis.get("conflicts") or []),
        "gaps": list(synthesis.get("gaps") or []),
    }


def build_uncertainty(synthesis, synth):
    uncertainty = copy.deepcopy(synthesis.get("uncertainty") or {})
    if not isinstance(uncertainty, dict):
        uncertainty = {}
    uncertainty.setdefault("conflict_count", len(synth.get("conflicts") or []))
    uncertainty.setdefault("gap_count", len(synth.get("gaps") or []))
    uncertainty.setdefault("bias", [])
    uncertainty.setdefault("not_reported", [])
    return uncertainty


def build_sources(protocol, screening, quality, evidence):
    sources = protocol.get("sources")
    if sources:
        return sorted(sources, key=lambda s: str(s.get("source_id", "")))
    by_id = {}
    for rec in list(screening) + list(quality) + list(evidence):
        sid = rec.get("source_id")
        if sid and sid not in by_id:
            by_id[sid] = {"source_id": sid, "file": rec.get("file", ""), "title": rec.get("title", "")}
    return sorted(by_id.values(), key=lambda s: str(s["source_id"]))


def build_change_log():
    """Logical, order-only change log (no wall clock) so output stays deterministic."""
    return [
        {"seq": 0, "action": "skeleton", "note": "partial skeleton written before analysis"},
        {"seq": 1, "action": "protocol", "note": "question, subquestions, search strategy, criteria"},
        {"seq": 2, "action": "screening", "note": "title and fulltext screening records with reasons"},
        {"seq": 3, "action": "quality_appraisal", "note": "per-dimension appraisal with locators and quotes"},
        {"seq": 4, "action": "synthesis", "note": "conclusions, conflicts and gaps with evidence backlinks"},
        {"seq": 5, "action": "finalize", "note": "deterministic assembly and selfcheck"},
    ]


def build_result(case_id, contract, data, status):
    proto = data.get("protocol") or {}
    screening = data.get("screening") or []
    quality = data.get("quality") or []
    evidence = data.get("evidence") or []
    synthesis = data.get("synthesis") or {}
    result = copy.deepcopy(contract)
    result["case_id"] = case_id or "unknown"
    result["status"] = status
    result["review_question"] = proto.get("review_question", "")
    result["subquestions"] = list(proto.get("subquestions") or [])
    result["search_strategy"] = proto.get("search_strategy") or {"query_blocks": []}
    result["inclusion_criteria"] = list(proto.get("inclusion_criteria") or [])
    result["exclusion_criteria"] = list(proto.get("exclusion_criteria") or [])
    result["screening_records"] = list(screening)
    result["prisma_flow"] = compute_prisma(screening)
    result["quality_appraisal"] = list(quality)
    evidence_map = build_evidence_map(evidence)
    result["evidence_map"] = evidence_map
    result["synthesis"] = build_synthesis(synthesis, evidence_map)
    result["uncertainty"] = build_uncertainty(synthesis, result["synthesis"])
    result["sources"] = build_sources(proto, screening, quality, evidence)
    result["change_log"] = build_change_log()
    result["pending_human"] = list(proto.get("pending_human") or [])
    result["limitations"] = list(proto.get("limitations") or [])
    result["errors"] = list(data.get("errors") or [])
    result["degradations"] = list(data.get("degradations") or [])
    result["runtime_seconds"] = None
    result["outputs"] = {
        "review_md": "review.md",
        "screening_log_json": "screening_log.json",
        "evidence_map_json": "evidence_map.json",
    }
    return result


def build_review_md(result):
    prisma = result.get("prisma_flow") or {}
    lines = [
        "# 系统性文献综述：%s" % (result.get("review_question") or "(未提供研究问题)"),
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- 纳入研究数：%s（检索命中 %s，标题/摘要筛选 %s，全文评估 %s）"
        % (prisma.get("included", 0), prisma.get("identified", 0),
           prisma.get("screened_title_abstract", 0), prisma.get("assessed_fulltext", 0)),
        "",
        "## 纳排标准",
        "",
    ]
    for item in (result.get("inclusion_criteria") or []):
        lines.append("- [纳入 %s] %s" % (item.get("id", "?"), item.get("criterion", "")))
    for item in (result.get("exclusion_criteria") or []):
        lines.append("- [排除 %s] %s" % (item.get("id", "?"), item.get("criterion", "")))
    lines += ["", "## 综合结论", ""]
    for i, concl in enumerate((result.get("synthesis") or {}).get("conclusions", []), 1):
        lines.append("%d. %s（证据：%s，置信：%s）"
                     % (i, concl.get("claim", ""), ", ".join(concl.get("evidence_ids", [])),
                        concl.get("confidence", "not_reported")))
    lines += ["", "## 冲突证据", ""]
    conflicts = (result.get("synthesis") or {}).get("conflicts", [])
    if conflicts:
        for item in conflicts:
            lines.append("- %s：`%s` 与 `%s` 分歧（处理：%s）"
                         % (item.get("claim", ""), item.get("source_a", "?"),
                            item.get("source_b", "?"), item.get("resolution", "undetermined")))
    else:
        lines.append("- 未记录到冲突证据。")
    lines += ["", "## 研究空白", ""]
    gaps = (result.get("synthesis") or {}).get("gaps", [])
    if gaps:
        for item in gaps:
            lines.append("- %s（范围：%s，状态：%s）"
                         % (item.get("gap", ""), item.get("scope", ""), item.get("evidence_status", "not_reported")))
    else:
        lines.append("- 未记录到研究空白。")
    lines += ["", "## 限制与待人工确认", ""]
    for item in (result.get("limitations") or []):
        lines.append("- %s" % item)
    for item in (result.get("pending_human") or []):
        lines.append("- [待确认] %s" % item)
    return "\n".join(lines) + "\n"


def normalize_ws(text):
    return "".join((text or "").split())


def verify_quote(input_dir, rec):
    file_rel = rec.get("file") or ""
    quote = rec.get("quote") or ""
    if not file_rel or not quote:
        return None, "missing file/quote"
    path = os.path.join(input_dir, file_rel.replace("/", os.sep))
    if not os.path.exists(path):
        return None, "file missing: %s" % file_rel
    text, errs, _ = load_text(path)
    if errs:
        return None, "unreadable: %s" % file_rel
    if normalize_ws(quote) in normalize_ws(text):
        return True, "verbatim"
    return False, "not verbatim in %s" % file_rel


def run_selfcheck(result_path, input_dir, work_dir, contract=None):
    rep = {"ok": True, "fatal": [], "warnings": [], "checks": []}

    def add(name, ok, detail=""):
        rep["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            rep["warnings"].append("%s: %s" % (name, detail))

    def fatal(name, detail):
        rep["fatal"].append("%s: %s" % (name, detail))
        rep["ok"] = False

    result, errs, degs = load_json(result_path)
    if errs or not isinstance(result, dict):
        fatal("result", "missing or unparseable result.json")
        return rep
    if contract is None:
        state, _, _ = load_json(os.path.join(work_dir, "run_state.json"))
        contract = (state or {}).get("contract") or DEFAULT_CONTRACT
    for key in contract:
        if key not in result:
            fatal("contract", "missing contract field %s" % key)
    if not result.get("case_id"):
        fatal("case_id", "case_id missing or empty")
    if result.get("status") not in STATUSES:
        fatal("status", "invalid status %r" % result.get("status"))

    add("review_question", bool(result.get("review_question")))
    add("subquestions", len(result.get("subquestions") or []) >= 1)
    qb = (result.get("search_strategy") or {}).get("query_blocks") or []
    add("query_blocks", bool(qb))
    for block in qb:
        missing = [k for k in ("database", "query", "intent") if not block.get(k)]
        if missing:
            add("query_block_fields", False, "block %s missing %s" % (block.get("id", "?"), ",".join(missing)))
        else:
            add("query_block_fields", True, "block %s" % block.get("id", "?"))
    add("inclusion_criteria", bool(result.get("inclusion_criteria")))
    add("exclusion_criteria", bool(result.get("exclusion_criteria")))

    criteria_ids = {}
    for item in (result.get("inclusion_criteria") or []) + (result.get("exclusion_criteria") or []):
        cid = item.get("id")
        if cid:
            criteria_ids[cid] = item.get("kind", "")

    screening = result.get("screening_records") or []
    add("screening_records", bool(screening))
    seen_records = set()
    for rec in screening:
        rid = rec.get("record_id")
        if rid in seen_records:
            add("screening_unique_ids", False, "duplicate record_id %s" % rid)
        seen_records.add(rid)
        if rec.get("decision") == "exclude":
            ok = bool(rec.get("reason")) and bool(rec.get("criterion_id"))
            add("exclude_reason", ok, "record %s" % rid)
            if rec.get("criterion_id") and rec["criterion_id"] not in criteria_ids:
                add("criterion_exists", False, "record %s cites unknown criterion %s" % (rid, rec["criterion_id"]))
            if rec.get("criterion_id") in criteria_ids:
                kind = criteria_ids[rec["criterion_id"]]
                consistent = (rec["decision"] == "exclude" and kind == "exclusion") or \
                             (rec["decision"] == "include" and kind == "inclusion")
                if not consistent:
                    add("criteria_consistency", False,
                        "record %s decision=%s but criterion %s is %s" % (rid, rec["decision"], rec["criterion_id"], kind))
        if rec.get("duplicate_of"):
            dup_ok = rec["duplicate_of"] in seen_records
            add("duplicate_of_valid", dup_ok, "record %s" % rid)

    recomputed = compute_prisma(screening)
    stored = result.get("prisma_flow") or {}
    prisma_ok = all(recomputed.get(k) == stored.get(k) for k in recomputed)
    add("prisma_recount", prisma_ok, "recomputed=%s stored=%s" % (recomputed, stored))

    evidence_map = result.get("evidence_map") or []
    ev_ids = {e.get("evidence_id") for e in evidence_map}
    add("evidence_map", bool(evidence_map))
    quote_targets = []
    for entry in evidence_map:
        if not entry.get("evidence_id") or not entry.get("locator") or not entry.get("quote"):
            add("evidence_fields", False, "entry %s incomplete" % entry.get("evidence_id", "?"))
        else:
            add("evidence_fields", True, "entry %s" % entry["evidence_id"])
        quote_targets.append(entry)
    for item in (result.get("quality_appraisal") or []):
        for dim in (item.get("dimensions") or []):
            quote_targets.append(dim)
    conflicts = (result.get("synthesis") or {}).get("conflicts") or []
    add("conflicts_structured", all(c.get("source_a") and c.get("source_b") for c in conflicts) if conflicts else True)
    for conf in conflicts:
        quote_targets.append({"file": conf.get("file_a") or conf.get("source_a"), "quote": conf.get("quote_a", "")})
        quote_targets.append({"file": conf.get("file_b") or conf.get("source_b"), "quote": conf.get("quote_b", "")})

    conclusions = (result.get("synthesis") or {}).get("conclusions") or []
    add("conclusions", bool(conclusions))
    for concl in conclusions:
        for eid in concl.get("evidence_ids", []):
            if eid not in ev_ids:
                add("conclusion_backlink", False, "conclusion %s cites missing evidence %s" % (concl.get("id", "?"), eid))
            else:
                add("conclusion_backlink", True, "conclusion %s -> %s" % (concl.get("id", "?"), eid))

    for target in quote_targets:
        if target.get("quote"):
            found, detail = verify_quote(input_dir, target)
            add("quote_verbatim", found is not False, "%s (%s)" % (detail, target.get("file", "?")))

    add("change_log", bool(result.get("change_log")))
    add("sources", bool(result.get("sources")))
    add("uncertainty", isinstance(result.get("uncertainty"), dict))
    add("pending_human_field", isinstance(result.get("pending_human"), list))

    # Determinism round-trip: canonical dump must re-parse to an equal object.
    try:
        again = json.loads(canonical_dump(result))
        add("canonical_roundtrip", again == result)
    except Exception as exc:
        add("canonical_roundtrip", False, str(exc))

    if rep["fatal"]:
        rep["ok"] = False
    return rep
