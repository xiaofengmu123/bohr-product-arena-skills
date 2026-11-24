#!/usr/bin/env python3
"""Shared implementation for the budgeted systematic-review Skill.

Pure Python standard library. Deterministic: the same input directory and the
same work files produce the same result fields in the same order, so a second
run is reproducible and comparable.

This module owns the single source of truth for:
  * status vocabulary and the result.json skeleton
  * the 900-second / 64-request phase plan (time budget guardrail)
  * input discovery, tolerant decoding and atomic writes
  * PRISMA recomputation and the self-check rule set
"""

import copy
import json
import os
import time

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

# Domain fields for a systematic review. These are filled into result.json when
# the case's output_contract does not already declare them; declared contract
# keys always win and are preserved verbatim.
DEFAULT_CONTRACT = {
    "review_question": None,
    "subquestions": [],
    "search_strategy": {"query_blocks": []},
    "inclusion_criteria": [],
    "exclusion_criteria": [],
    "screening_records": [],
    "prisma_flow": {},
    "quality_appraisal": [],
    "synthesis": {"conclusions": [], "conflicts": [], "gaps": []},
    "evidence_map": [],
    "uncertainty": [],
}

# Fields always appended to result.json regardless of the contract. These carry
# the honesty layer the platform contract requires.
HONEST_FIELDS = [
    "case_id", "status", "sources", "uncertainty", "change_log",
    "limitations", "errors", "degradations", "pending_human",
    "runtime_seconds", "artifacts",
]

# Recognised material extensions during input discovery.
MATERIAL_EXTS = {
    ".pdf", ".md", ".txt", ".csv", ".json", ".jsonl", ".xml", ".html",
    ".htm", ".tex", ".bib", ".ris", ".enw", ".png", ".jpg", ".jpeg",
    ".tif", ".tiff", ".docx", ".pptx", ".xlsx",
}

# Text files larger than this are truncated; the truncation is recorded as a
# degradation so the reader never silently trusts a partial parse.
MAX_TEXT_BYTES = 2 * 1024 * 1024

# Phase plan. ``end`` is seconds since run start; ``requests`` is the model
# request allowance for that phase. This is the one place the budget lives so
# SKILL.md prose and the runtime helper cannot drift apart.
PHASES = [
    {"id": "P0", "name": "skeleton",  "end": 180,  "requests": 3},
    {"id": "P1", "name": "protocol",  "end": 720,  "requests": 7},
    {"id": "P2", "name": "screening", "end": 1800, "requests": 20},
    {"id": "P3", "name": "quality",   "end": 2520, "requests": 12},
    {"id": "P4", "name": "synthesis", "end": 3360, "requests": 12},
    {"id": "P5", "name": "buffer",    "end": 3540, "requests": 6},
]
TOTAL_SECONDS = 900
TOTAL_REQUESTS = 64
RESERVE_REQUESTS = 4          # keep 4 requests as emergency reserve
HARD_STOP_SECONDS = 840       # stop new model requests, keep 60 s for writes
CRITICAL_SECONDS = 120        # enter "critical" when <=120 s remain

DECODE_ORDER = ("utf-8-sig", "gb18030", "latin-1")


# ----------------------------------------------------------------------------
# Small utilities
# ----------------------------------------------------------------------------

def epoch_now():
    return time.time()


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def _decode(raw, path):
    errors, degradations = [], []
    if len(raw) > MAX_TEXT_BYTES:
        raw = raw[:MAX_TEXT_BYTES]
        degradations.append("truncated %s at %d bytes" % (path, MAX_TEXT_BYTES))
    for enc in DECODE_ORDER:
        try:
            return raw.decode(enc), errors, degradations
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("latin-1", errors="replace"), errors, degradations


def read_text(path):
    """Read a UTF-8 text file with encoding fallback. Returns (text, errors, degradations)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, ["cannot read %s: %s" % (path, exc)], []
    return _decode(raw, path)


def write_text(path, text):
    try:
        ensure_dir(os.path.dirname(path) or ".")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def load_json(path):
    """Load a JSON file. Returns (obj, errors, degradations); obj is None on failure."""
    text, errors, degradations = read_text(path)
    if text is None:
        return None, errors, degradations
    try:
        return json.loads(text), errors, degradations
    except ValueError as exc:
        errors.append("bad JSON in %s: %s" % (path, exc))
        return None, errors, degradations


def read_jsonl(path):
    """Read a JSONL file line by line. Malformed lines are reported, not fatal."""
    text, errors, _ = read_text(path)
    records = []
    if text is None:
        return records, errors
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            errors.append("bad JSONL line %d in %s: %s" % (lineno, path, exc))
            continue
        if isinstance(obj, dict):
            records.append(obj)
        else:
            errors.append("non-object JSONL line %d in %s" % (lineno, path))
    return records, errors


def write_json(path, obj):
    """Atomically write JSON (temp file + rename). Returns list of error strings."""
    try:
        ensure_dir(os.path.dirname(path) or ".")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return []
    except OSError as exc:
        return ["cannot write %s: %s" % (path, exc)]


def load_json_optional(path):
    """Like load_json, but a missing file is not an error (returns None, [], [])."""
    if not os.path.isfile(path):
        return None, [], []
    return load_json(path)


def read_jsonl_optional(path):
    """Like read_jsonl, but a missing file is not an error (returns [], [])."""
    if not os.path.isfile(path):
        return [], []
    return read_jsonl(path)


def load_value(path):
    """Load a work-file value as (value, errors, degradations): JSON object,
    JSONL list, or plain text. All three return a 3-tuple."""
    lower = path.lower()
    if lower.endswith(".jsonl"):
        records, errs = read_jsonl(path)
        return records, errs, []
    if lower.endswith(".json"):
        return load_json(path)
    return read_text(path)


def deep_merge(base, patch):
    """Recursively merge ``patch`` into a copy of ``base`` (dicts merge, others replace)."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def fill_defaults(value):
    """Keep a contract's key structure but empty its leaves: list->[], dict->{}, scalar->None."""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {k: fill_defaults(v) for k, v in value.items()}
    if isinstance(value, bool):
        return False
    return None


# ----------------------------------------------------------------------------
# Input discovery and contract handling
# ----------------------------------------------------------------------------

def discover_input(input_dir):
    inv = {
        "input_dir": input_dir, "exists": False, "total_files": 0,
        "case_json": None, "task_md": None, "materials": [], "other": [],
    }
    if not os.path.isdir(input_dir):
        return inv
    inv["exists"] = True
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir)
            inv["total_files"] += 1
            base = name.lower()
            ext = os.path.splitext(base)[1].lower()
            if base == "case.json":
                inv["case_json"] = full
            elif base in ("task.md", "task.txt", "task.json"):
                inv["task_md"] = full
            elif ext in MATERIAL_EXTS:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = -1
                inv["materials"].append({"path": full, "rel": rel, "size": size})
            else:
                inv["other"].append(rel)
    return inv


def extract_case_id(case_obj):
    if not isinstance(case_obj, dict):
        return ""
    for key in ("case_id", "caseId", "id", "armiId", "challenge_id", "challengeId"):
        value = case_obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None:
            return str(value)
    return ""


def build_contract(case_obj):
    """Return the case's output_contract, or DEFAULT_CONTRACT when absent."""
    if isinstance(case_obj, dict):
        for key in ("output_contract", "outputContract", "contract"):
            value = case_obj.get(key)
            if isinstance(value, dict):
                return copy.deepcopy(value)
    return copy.deepcopy(DEFAULT_CONTRACT)


def new_skeleton(case_id, contract, note=""):
    """Build the earliest valid result.json: contract keys emptied + honest fields."""
    contract = contract or DEFAULT_CONTRACT
    result = {k: fill_defaults(v) for k, v in contract.items()}
    for key, default in DEFAULT_CONTRACT.items():
        if key not in result:
            result[key] = copy.deepcopy(default)
    result["case_id"] = case_id or "unknown"
    result["status"] = STATUS_PARTIAL
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["limitations"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime_seconds"] = 0
    result["artifacts"] = []
    if note:
        result["change_log"].append({"ts": iso_now(), "action": "skeleton", "detail": note})
    return result


# ----------------------------------------------------------------------------
# PRISMA recomputation
# ----------------------------------------------------------------------------

_EXCLUDE = {"exclude", "excluded", "no", "reject", "rejected"}
_INCLUDE = {"include", "included", "yes", "accept", "accepted"}
_TA_STAGES = {"title_abstract", "title_abstract_screen", "titles_abstracts",
              "titles", "abstracts", "stage1", "s1", "title"}
_FT_STAGES = {"fulltext", "full_text", "full_text_screen", "fulltext_screen",
              "stage2", "s2", "ft"}


def _count_stage(records, stages, key):
    screened = 0
    included = 0
    excluded = 0
    for rec in records:
        stage = (rec.get("stage") or rec.get("round") or "").strip().lower()
        if stage not in stages:
            continue
        screened += 1
        decision = (rec.get("decision") or "").strip().lower()
        if decision in _INCLUDE:
            included += 1
        elif decision in _EXCLUDE:
            excluded += 1
    return {"screened": screened, "included": included, "excluded": excluded}


def compute_prisma(records):
    records = [r for r in records if isinstance(r, dict)]
    stages = {
        "title_abstract": _count_stage(records, _TA_STAGES, "title_abstract"),
        "fulltext": _count_stage(records, _FT_STAGES, "fulltext"),
    }
    flow = {"stages": stages, "included_total": stages["fulltext"]["included"]}
    # Keep the total meaningful even if a stage label is unusual.
    if not stages["title_abstract"]["screened"] and not stages["fulltext"]["screened"]:
        flow["note"] = "no recognised screening stages found"
    return flow


# ----------------------------------------------------------------------------
# Status derivation and sources
# ----------------------------------------------------------------------------

def derive_status(result, case_id):
    has_question = bool((result.get("review_question") or "").strip())
    n_screening = len(result.get("screening_records") or [])
    n_conclusions = len((result.get("synthesis") or {}).get("conclusions") or [])
    n_evidence = len(result.get("evidence_map") or [])
    if has_question and n_screening and n_conclusions and n_evidence:
        return STATUS_OK
    if has_question or n_screening or n_conclusions or n_evidence:
        return STATUS_PARTIAL
    if result.get("errors"):
        return STATUS_INPUT_ERROR
    return STATUS_INSUFFICIENT


def collect_sources(result):
    seen = set()
    out = []

    def add(source):
        if not source:
            return
        key = json.dumps(source, sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            out.append(source)

    for ev in result.get("evidence_map") or []:
        if isinstance(ev, dict) and ev.get("source"):
            add(ev["source"])
    for rec in result.get("screening_records") or []:
        if isinstance(rec, dict):
            source = {}
            for k in ("record_id", "id", "title", "source"):
                if rec.get(k):
                    source[k] = rec[k]
            if source:
                add(source)
    return out


# ----------------------------------------------------------------------------
# Budget guardrail
# ----------------------------------------------------------------------------

def budget_report(state, now=None):
    now = now if now is not None else epoch_now()
    started = state.get("started_epoch")
    started = started if isinstance(started, (int, float)) else now
    elapsed = max(0.0, now - started)
    remaining = max(0.0, TOTAL_SECONDS - elapsed)
    requests_used = int(state.get("requests_used", 0) or 0)
    requests_remaining = max(0, TOTAL_REQUESTS - requests_used)

    current = PHASES[-1]
    for phase in PHASES:
        if elapsed < phase["end"]:
            current = phase
            break

    checkpoints = state.get("checkpoints")
    done = set(checkpoints) if isinstance(checkpoints, dict) else set()

    if elapsed >= HARD_STOP_SECONDS or requests_used >= TOTAL_REQUESTS:
        verdict = "stop"
        action = "预算已用尽：停止新的模型请求，立即执行 finalize + selfcheck，然后停止。"
    elif remaining <= CRITICAL_SECONDS or requests_remaining <= RESERVE_REQUESTS:
        verdict = "critical"
        action = "接近预算下限：不再开启新的内容工作，立即 finalize 落盘现有结果。"
    elif elapsed >= current["end"] and current["id"] not in done:
        verdict = "behind"
        action = ("阶段 %s 的软截止已过但未写检查点：本阶段做最小收口后马上 finalize，"
                  "不要继续扩展范围。" % current["id"])
    else:
        verdict = "on_track"
        action = ("继续阶段 %s，软截止还有 %.0f 秒；剩余请求 %d 次。" %
                  (current["id"], current["end"] - elapsed, requests_remaining))

    return {
        "verdict": verdict,
        "action": action,
        "now_epoch": now,
        "started_epoch": started,
        "elapsed_s": int(elapsed),
        "remaining_s": int(remaining),
        "phase": current["id"],
        "phase_deadline_epoch": started + current["end"],
        "phase_remaining_s": int(max(0, current["end"] - elapsed)),
        "requests_used": requests_used,
        "requests_remaining": requests_remaining,
        "hard_stop_epoch": started + HARD_STOP_SECONDS,
        "checkpoints": sorted(done),
        "phase_plan": PHASES,
    }


# ----------------------------------------------------------------------------
# Self-check
# ----------------------------------------------------------------------------

def validate_result(result, contract):
    errors, warnings = [], []

    def warn(msg):
        warnings.append(msg)

    if not isinstance(result, dict):
        return {"ok": False, "errors": ["result is not a JSON object"], "warnings": [],
                "error_count": 1, "warning_count": 0}

    for key in (contract or {}).keys():
        if key not in result:
            errors.append("missing contract field: %s" % key)
    for key in HONEST_FIELDS:
        if key not in result:
            errors.append("missing honest field: %s" % key)

    status = result.get("status")
    if status not in (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR):
        errors.append("invalid status: %r" % (status,))

    case_id = result.get("case_id")
    if not str(case_id or "").strip():
        warn("case_id is empty")

    question = (result.get("review_question") or "").strip()
    subquestions = result.get("subquestions") or []
    blocks = (result.get("search_strategy") or {}).get("query_blocks") or []
    inclusion = result.get("inclusion_criteria") or []
    exclusion = result.get("exclusion_criteria") or []
    screening = result.get("screening_records") or []
    appraisal = result.get("quality_appraisal") or []
    evidence = result.get("evidence_map") or []
    synthesis = result.get("synthesis") or {}
    conclusions = synthesis.get("conclusions") or []
    conflicts = synthesis.get("conflicts") or []
    gaps = synthesis.get("gaps") or []

    content_complete = all([question, subquestions, blocks, screening,
                            conclusions, evidence])
    if status == STATUS_OK and not content_complete:
        errors.append("status=ok but core content incomplete (question/subquestions/"
                      "query_blocks/screening/conclusions/evidence_map)")

    if not question:
        warn("review_question empty")
    if len(subquestions) < 1:
        warn("subquestions empty (recommend >=2 with dependencies)")
    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            warn("query_blocks[%d] not an object" % i)
            continue
        for field in ("database", "query", "intent"):
            if not (block.get(field) or "").strip():
                warn("query_blocks[%d] missing %s" % (i, field))
        if not block.get("time_limit") and not block.get("language"):
            warn("query_blocks[%d] missing time/language limits" % i)
    if not inclusion:
        warn("inclusion_criteria empty")
    if not exclusion:
        warn("exclusion_criteria empty")

    stages = set()
    for i, rec in enumerate(screening):
        if not isinstance(rec, dict):
            warn("screening_records[%d] not an object" % i)
            continue
        stages.add((rec.get("stage") or rec.get("round") or "").strip().lower())
        decision = (rec.get("decision") or "").strip().lower()
        if decision in _EXCLUDE:
            if not rec.get("reason"):
                warn("screening[%d] exclude decision missing reason" % i)
            if not rec.get("criterion_id"):
                warn("screening[%d] exclude decision missing criterion_id" % i)
    if len(stages) < 2:
        warn("screening_records have fewer than 2 distinct stages "
             "(expect title/abstract + fulltext)")

    computed = compute_prisma(screening)
    recorded = result.get("prisma_flow") or {}
    if computed.get("stages", {}).get("title_abstract", {}).get("screened") and recorded:
        if json.dumps(computed, sort_keys=True) != json.dumps(recorded, sort_keys=True):
            warn("prisma_flow does not match counts recomputed from screening_records")

    evidence_ids = set()
    for i, ev in enumerate(evidence):
        if not isinstance(ev, dict):
            warn("evidence_map[%d] not an object" % i)
            continue
        eid = ev.get("id") or ev.get("evidence_id")
        if eid:
            evidence_ids.add(eid)
        if not (ev.get("locator") or "").strip():
            warn("evidence[%s] missing locator" % (eid or i))
        if not (ev.get("quote") or "").strip():
            warn("evidence[%s] missing verbatim quote" % (eid or i))

    for i, con in enumerate(conclusions):
        if not isinstance(con, dict):
            warn("conclusions[%d] not an object" % i)
            continue
        refs = con.get("evidence_ids") or []
        unresolved = [r for r in refs if r not in evidence_ids]
        if unresolved:
            errors.append("conclusion[%d] evidence_ids unresolved: %s"
                          % (i, unresolved))
        if not refs:
            warn("conclusion[%d] has no evidence_ids" % i)

    for i, con in enumerate(conflicts):
        if not isinstance(con, dict):
            continue
        sources = con.get("sources") or con.get("evidence_ids") or []
        if len(sources) < 2:
            warn("conflict[%d] lists fewer than 2 sources" % i)

    for i, gap in enumerate(gaps):
        if isinstance(gap, dict) and not (gap.get("gap") or gap.get("detail")
                                          or gap.get("locator")):
            warn("gap[%d] is not located to a concrete missing piece" % i)

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


# ----------------------------------------------------------------------------
# Human-readable review.md renderer (deterministic)
# ----------------------------------------------------------------------------

def _block(value):
    if value is None:
        return "(未填)"
    if isinstance(value, str):
        return value.strip() or "(未填)"
    return json.dumps(value, ensure_ascii=False)


def render_review_md(result):
    lines = []
    lines.append("# 系统性文献综述：可复核记录")
    lines.append("")
    lines.append("> 由 `scripts/main.py finalize` 确定性生成，与 `result.json` 同源；"
                 "人工复核以 `result.json` 为准。")
    lines.append("")

    lines.append("## 1. 研究问题")
    lines.append("")
    lines.append(_block(result.get("review_question")))
    subquestions = result.get("subquestions") or []
    if subquestions:
        lines.append("")
        lines.append("**子问题**")
        for i, item in enumerate(subquestions, 1):
            if isinstance(item, dict):
                text = item.get("text") or item.get("question") or _block(item)
                deps = item.get("depends_on")
                suffix = ("（依赖 %s）" % ", ".join(map(str, deps))) if deps else ""
            else:
                text, suffix = str(item), ""
            lines.append("%d. %s%s" % (i, text, suffix))
    lines.append("")

    blocks = (result.get("search_strategy") or {}).get("query_blocks") or []
    lines.append("## 2. 检索式")
    lines.append("")
    if not blocks:
        lines.append("(未填)")
    for i, block in enumerate(blocks, 1):
        if isinstance(block, dict):
            lines.append("**块 %d**：数据库 %s｜意图 %s" %
                         (i, _block(block.get("database")), _block(block.get("intent"))))
            lines.append("")
            lines.append("```")
            lines.append(_block(block.get("query")))
            lines.append("```")
            limits = []
            if block.get("time_limit"):
                limits.append("时间 %s" % block["time_limit"])
            if block.get("language"):
                limits.append("语言 %s" % block["language"])
            if block.get("fields"):
                limits.append("字段 %s" % block["fields"])
            if limits:
                lines.append("限制：" + "；".join(limits))
        else:
            lines.append("%d. %s" % (i, str(block)))
    lines.append("")

    inclusion = result.get("inclusion_criteria") or []
    exclusion = result.get("exclusion_criteria") or []
    lines.append("## 3. 纳排标准")
    lines.append("")
    lines.append("**纳入**")
    for item in inclusion:
        lines.append("- " + _block(item.get("text") if isinstance(item, dict) else item))
    lines.append("")
    lines.append("**排除**")
    for item in exclusion:
        lines.append("- " + _block(item.get("text") if isinstance(item, dict) else item))
    lines.append("")

    lines.append("## 4. 筛选流程（PRISMA）")
    lines.append("")
    flow = result.get("prisma_flow") or {}
    stages = flow.get("stages") or {}
    lines.append("| 阶段 | 筛出 | 纳入 | 排除 |")
    lines.append("|-|-|-|-|")
    for name in ("title_abstract", "fulltext"):
        stage = stages.get(name) or {}
        lines.append("| %s | %s | %s | %s |" %
                     (name, stage.get("screened", 0), stage.get("included", 0),
                      stage.get("excluded", 0)))
    lines.append("")
    lines.append("纳入合计：%s" % flow.get("included_total", 0))
    lines.append("")

    lines.append("## 5. 质量评价")
    lines.append("")
    appraisal = result.get("quality_appraisal") or []
    if not appraisal:
        lines.append("(未填)")
    for item in appraisal:
        if isinstance(item, dict):
            lines.append("- %s：总体 %s（影响：%s）" %
                         (_block(item.get("record_id") or item.get("source")),
                          _block(item.get("overall")), _block(item.get("impact"))))
    lines.append("")

    lines.append("## 6. 综合结论 / 冲突 / 空白")
    lines.append("")
    synthesis = result.get("synthesis") or {}
    lines.append("**结论**")
    for con in synthesis.get("conclusions") or []:
        if isinstance(con, dict):
            lines.append("- " + _block(con.get("text")))
            lines.append("  证据：%s" % ", ".join(map(str, con.get("evidence_ids") or [])))
        else:
            lines.append("- " + str(con))
    lines.append("")
    lines.append("**冲突**")
    for con in synthesis.get("conflicts") or []:
        lines.append("- " + _block(con.get("text") if isinstance(con, dict) else con))
    lines.append("")
    lines.append("**空白**")
    for gap in synthesis.get("gaps") or []:
        lines.append("- " + _block(gap.get("gap") if isinstance(gap, dict) else gap))
    lines.append("")

    lines.append("## 7. 证据映射")
    lines.append("")
    evidence = result.get("evidence_map") or []
    if not evidence:
        lines.append("(未填)")
    for ev in evidence:
        if isinstance(ev, dict):
            lines.append("- %s｜来源 %s｜位置 %s" %
                         (_block(ev.get("id")), _block(ev.get("source")),
                          _block(ev.get("locator"))))
            lines.append("  > %s" % _block(ev.get("quote")))
    lines.append("")

    lines.append("## 8. 不确定性与限制")
    lines.append("")
    for label, key in (("不确定性", "uncertainty"), ("限制", "limitations"),
                       ("待人工", "pending_human"), ("错误", "errors"),
                       ("降级", "degradations")):
        value = result.get(key) or []
        if value:
            lines.append("**%s**" % label)
            for item in value:
                lines.append("- " + (str(item) if not isinstance(item, dict)
                                     else _block(item.get("detail") or item)))
            lines.append("")
    lines.append("状态：`%s`｜case_id：`%s`" %
                 (_block(result.get("status")), _block(result.get("case_id"))))
    lines.append("")
    return "\n".join(lines)
