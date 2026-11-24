"""core.py -- deterministic state, input discovery and assembly helpers.

This module implements the *mechanics* of the research-question-driven search
skill. It deliberately does NOT perform the intellectual work (decomposition,
relevance judgement, query planning, stop reasoning): those are the agent's
model-request tasks. It persists them deterministically and turns them into
reviewable artifacts.

Design invariants (robustness variant)
--------------------------------------
- Pure standard library, Python >= 3.8.
- No function raises on malformed input; problems are recorded in ``errors``
  or ``degradations`` and the run continues.
- State lives in ``<work>/state.json``; every update is atomic (temp file +
  ``os.replace``), so a killed process never leaves a corrupt state file.
- ``result.json`` is always valid JSON and always present after ``run``.
- Same input -> same output (deterministic ordering, fixed score formula);
  only timestamps and computed runtime differ between identical runs.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import tempfile

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_FILE = 2 * 1024 * 1024   # skip larger text/json files, record degradation
MAX_FILES = 100
MAX_EXCERPT = 6000                # chars of decoded text kept in the manifest
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
VALID_SCREENING = ("included", "excluded", "uncertain", "merged_duplicate")

CONTRACT_KEYS = ("output_contract", "output", "schema", "required_fields", "contract")
CASE_ID_KEYS = ("case_id", "id", "caseId", "challenge_id")
QUESTION_KEYS = ("research_question", "question", "researchQuestion", "query", "task")

# Transparent score formula: 0.6*relevance + 0.2*metadata_completeness
# + 0.2*source_reliability. Source reliability is a fixed mapping below.
SOURCE_RELIABILITY = {
    "paper_search": 0.9, "crossref": 0.9, "pubmed": 0.9, "openalex": 0.9,
    "doi": 0.9, "arxiv": 0.85, "scholar": 0.8, "scopus": 0.8, "wos": 0.8,
    "web_search": 0.7, "web": 0.7, "website": 0.6,
    "materials": 0.8, "pdf": 0.8, "local": 0.8,
}
DEFAULT_SOURCE_RELIABILITY = 0.5

METADATA_FIELDS = ("authors", "year", "journal", "doi")


def iso_now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# robust I/O helpers
# --------------------------------------------------------------------------- #

def read_text(path: str, issues: list) -> str:
    """Decode a text file defensively. Returns None on any failure."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        issues.append("DEG: size(%s): %s" % (path, e))
        return None
    if size > MAX_TEXT_FILE:
        issues.append("DEG: skip oversized text %s (%d bytes > %d)" % (path, size, MAX_TEXT_FILE))
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        issues.append("ERR: read(%s): %s" % (path, e))
        return None
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception as e:  # pragma: no cover - defensive
        issues.append("ERR: decode(%s): %s" % (path, e))
        return None


def read_json_file(path: str, issues: list):
    """Parse a JSON file defensively. Returns None on failure."""
    text = read_text(path, issues)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        issues.append("ERR: bad json %s: %s" % (path, e))
        return None


def atomic_write_text(path: str, text: str, issues: list) -> None:
    try:
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError as e:
        issues.append("ERR: write(%s): %s" % (path, e))


def atomic_write_json(path: str, obj, issues: list) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", issues)


def writable_dir(output: str, work: str, issues: list) -> str:
    """Return an output dir, falling back to work/ if output is not writable."""
    try:
        os.makedirs(output, exist_ok=True)
        probe = os.path.join(output, ".probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("")
        os.remove(probe)
        return output
    except OSError as e:
        issues.append("DEG: output dir %s not writable (%s); fallback to %s" % (output, e, work))
        os.makedirs(work, exist_ok=True)
        return work


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #

def default_state() -> dict:
    return {
        "_version": 1,
        "status": "partial",
        "case_id": None,
        "research_question": None,
        "sub_questions": [],
        "query_plan": [],
        "term_expansion": [],
        "stop_decision": None,
        "results": [],
        "records": [],
        "evidence": [],
        "sources": [],
        "uncertainty": {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []},
        "change_log": [],
        "status_notes": "",
        "limitations": [],
        "errors": [],
        "degradations": [],
        "pending_human": [],
        "output_contract": {},
        "input_manifest": [],
        "runtime": {"started_at": None, "finished_at": None, "seconds": None, "requests": None},
    }


def load_state(work: str, issues: list) -> dict:
    path = os.path.join(work, "state.json")
    state = default_state()
    if not os.path.isfile(path):
        return state
    obj = read_json_file(path, issues)
    if not isinstance(obj, dict):
        issues.append("ERR: state.json is not a dict; using fresh default state")
        return state
    for k, v in state.items():
        if k in obj:
            state[k] = obj[k]
    if not isinstance(state.get("uncertainty"), dict):
        state["uncertainty"] = {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []}
    if not isinstance(state.get("runtime"), dict):
        state["runtime"] = {"started_at": None, "finished_at": None, "seconds": None, "requests": None}
    return state


def save_state(work: str, state: dict, issues: list) -> None:
    os.makedirs(work, exist_ok=True)
    state["_saved_at"] = iso_now()
    atomic_write_json(os.path.join(work, "state.json"), state, issues)


def add_change(state: dict, action: str, reason: str, status=None) -> None:
    state.setdefault("change_log", []).append({
        "time": iso_now(), "action": action, "reason": reason, "status": status,
    })
    if len(state["change_log"]) > 500:
        state["change_log"] = state["change_log"][-500:]


# --------------------------------------------------------------------------- #
# input discovery and case parsing
# --------------------------------------------------------------------------- #

def kind_of(name: str) -> str:
    low = name.lower()
    if low.endswith(".json"):
        return "json"
    if low.endswith((".md", ".markdown", ".txt", ".yaml", ".yml")):
        return "text"
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith((".csv", ".tsv")):
        return "csv"
    return "other"


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _file_info(path: str, rel: str, issues: list) -> dict:
    info = {"name": rel, "path": path, "kind": kind_of(rel)}
    try:
        info["size"] = os.path.getsize(path)
    except OSError as e:
        issues.append("DEG: size(%s): %s" % (rel, e))
        info["size"] = None
    info["sha1"] = _sha1(path)
    if info["kind"] in ("text", "json", "csv"):
        text = read_text(path, issues)
        if text is None:
            info["skipped_oversize"] = True
            return info
        info["text"] = text[:MAX_EXCERPT]
        if info["kind"] == "json":
            try:
                info["parsed"] = json.loads(text)
            except (json.JSONDecodeError, ValueError) as e:
                issues.append("ERR: bad json %s: %s" % (rel, e))
                info["parsed"] = None
    return info


def discover_input(input_dir: str, issues: list) -> list:
    manifest = []
    if not os.path.isdir(input_dir):
        issues.append("ERR: input dir missing: %s" % input_dir)
        return manifest
    try:
        entries = sorted(os.listdir(input_dir))
    except OSError as e:
        issues.append("ERR: listdir(%s): %s" % (input_dir, e))
        return manifest
    for name in entries:
        p = os.path.join(input_dir, name)
        if os.path.isdir(p):
            try:
                subs = sorted(os.listdir(p))
            except OSError:
                subs = []
            for sub in subs:
                sp = os.path.join(p, sub)
                if os.path.isfile(sp):
                    manifest.append(_file_info(sp, name + "/" + sub, issues))
        elif os.path.isfile(p):
            manifest.append(_file_info(p, name, issues))
    return manifest[:MAX_FILES]


def _first_value(obj: dict, keys) -> object:
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    return None


def _default_for_spec(spec) -> object:
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        return None
    return None


def extract_contract(obj: dict, issues: list) -> dict:
    """Echo the output_contract: every field filled with null/[]/{} defaults."""
    contract = {}
    found = False
    for key in CONTRACT_KEYS:
        if key in obj and isinstance(obj[key], dict):
            for field, spec in obj[key].items():
                contract[field] = _default_for_spec(spec)
            found = True
            break
        if key in obj and isinstance(obj[key], list):
            for field in obj[key]:
                if isinstance(field, str) and field not in contract:
                    contract[field] = None
            found = True
            break
    if not found:
        # No explicit contract: record nothing (the case's own keys are echoed
        # only when a contract is present; otherwise standard fields apply).
        pass
    return contract


def build_case_summary(manifest: list, issues: list) -> dict:
    summary = {
        "case_found": False,
        "case_parsed": None,
        "case_parse_error": False,
        "case_id": None,
        "research_question": None,
        "output_contract": {},
        "task_text": None,
        "materials": [],
    }
    for f in manifest:
        base = os.path.basename(f["name"]).lower()
        if base == "case.json" or (base.endswith(".json") and "case" in base):
            summary["case_found"] = True
            if "parsed" in f and f["parsed"] is None:
                summary["case_parse_error"] = True
            obj = f.get("parsed")
            if isinstance(obj, dict):
                summary["case_parsed"] = obj
                summary["case_id"] = _first_value(obj, CASE_ID_KEYS)
                summary["research_question"] = _first_value(obj, QUESTION_KEYS)
                summary["output_contract"] = extract_contract(obj, issues)
        elif base in ("task.md", "task.txt", "task.markdown"):
            summary["task_text"] = f.get("text")
        if f["kind"] == "pdf" or f["kind"] == "csv":
            summary["materials"].append(f)
    return summary


# --------------------------------------------------------------------------- #
# records, scoring, screening
# --------------------------------------------------------------------------- #

def to_float(v, default=0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, x))


def source_reliability(database) -> float:
    if not database:
        return DEFAULT_SOURCE_RELIABILITY
    key = str(database).strip().lower()
    for k, v in SOURCE_RELIABILITY.items():
        if k in key:
            return v
    return DEFAULT_SOURCE_RELIABILITY


def compute_score(record: dict):
    rel = to_float(record.get("relevance"), 0.0)
    present = sum(1 for f in METADATA_FIELDS if record.get(f) not in (None, "", [], {}))
    meta = present / len(METADATA_FIELDS)
    src = source_reliability(record.get("database"))
    score = round(0.6 * rel + 0.2 * meta + 0.2 * src, 4)
    breakdown = {
        "relevance": round(rel, 4),
        "metadata_completeness": round(meta, 4),
        "source_reliability": round(src, 4),
        "formula": "0.6*relevance + 0.2*metadata_completeness + 0.2*source_reliability",
    }
    return score, breakdown


def normalize_record(rec: dict) -> dict:
    r = dict(rec)
    r.setdefault("id", None)
    r.setdefault("title", None)
    r.setdefault("authors", None)
    r.setdefault("year", None)
    r.setdefault("journal", None)
    r.setdefault("doi", None)
    r.setdefault("url", None)
    r.setdefault("database", None)
    r.setdefault("sub_question_ids", [])
    r.setdefault("relevance", None)
    r.setdefault("relevance_reason", None)
    r.setdefault("screening", "uncertain")
    r.setdefault("screening_reason", None)
    r.setdefault("evidence", None)
    if r.get("screening") not in VALID_SCREENING:
        r["screening"] = "uncertain"
    if not isinstance(r.get("sub_question_ids"), list):
        r["sub_question_ids"] = []
    if not isinstance(r.get("evidence"), dict):
        r["evidence"] = None
    return r


def _record_key(rec: dict) -> str:
    payload = json.dumps(
        {"title": rec.get("title"), "doi": rec.get("doi"), "year": rec.get("year")},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def build_results(records: list) -> list:
    out = []
    for rec in records:
        if rec.get("screening") == "included":
            r = dict(rec)
            score, breakdown = compute_score(r)
            r["score"] = score
            r["score_breakdown"] = breakdown
            out.append(r)
    out.sort(key=lambda x: (x.get("score") or 0, x.get("year") or 0), reverse=True)
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def import_records(state: dict, records: list, issues: list) -> None:
    if not isinstance(records, list):
        issues.append("ERR: import 需要记录数组或 {records:[...]}")
        return
    by_id = {}
    for i, rec in enumerate(state.get("records") or []):
        if isinstance(rec, dict) and rec.get("id"):
            by_id[rec["id"]] = i
    for rec in records:
        if not isinstance(rec, dict):
            issues.append("DEG: 跳过非对象记录")
            continue
        r = normalize_record(rec)
        key = r.get("id") or _record_key(r)
        if r.get("id") and key in by_id:
            state["records"][by_id[key]].update(r)
        else:
            state["records"].append(r)
    state["results"] = build_results(state["records"])


def build_screening(records: list, results: list) -> dict:
    included, excluded, uncertain, merged = [], [], [], []
    for rec in records or []:
        entry = {
            "record_id": rec.get("id"),
            "title": rec.get("title"),
            "year": rec.get("year"),
            "database": rec.get("database"),
            "screening_reason": rec.get("screening_reason"),
            "sub_question_ids": rec.get("sub_question_ids"),
        }
        bucket = rec.get("screening")
        if bucket == "included":
            included.append(entry)
        elif bucket == "excluded":
            excluded.append(entry)
        elif bucket == "merged_duplicate":
            merged.append(entry)
        else:
            uncertain.append(entry)
    return {
        "total_records": len(records or []),
        "included": included,
        "excluded": excluded,
        "uncertain": uncertain,
        "merged_duplicates": merged,
        "note": "全量筛选账本；排除项不静默丢弃，逐条保留 screening_reason。",
    }


def build_result(state: dict) -> dict:
    # 1) echo the case's output_contract fields first (filled defaults)
    result = {}
    for k, v in (state.get("output_contract") or {}).items():
        result[k] = v
    # 2) standard fields (these win on name collisions)
    result["case_id"] = state.get("case_id")
    result["status"] = state.get("status") if state.get("status") in VALID_STATUS else "partial"
    result["research_question"] = state.get("research_question")
    result["sub_questions"] = state.get("sub_questions") or []
    result["query_plan"] = state.get("query_plan") or []
    result["term_expansion"] = state.get("term_expansion") or []
    result["stop_decision"] = state.get("stop_decision")
    result["results"] = state.get("results") or []
    result["screening"] = build_screening(state.get("records") or [], state.get("results") or [])
    result["evidence"] = state.get("evidence") or []
    result["sources"] = state.get("sources") or []
    result["uncertainty"] = state.get("uncertainty") or {
        "missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": [],
    }
    result["change_log"] = state.get("change_log") or []
    result["status_notes"] = state.get("status_notes") or ""
    result["limitations"] = state.get("limitations") or []
    result["errors"] = state.get("errors") or []
    result["degradations"] = state.get("degradations") or []
    result["pending_human"] = state.get("pending_human") or []
    result["runtime"] = state.get("runtime") or {}
    return result


# --------------------------------------------------------------------------- #
# markdown rendering
# --------------------------------------------------------------------------- #

def render_screening(state: dict) -> str:
    s = build_screening(state.get("records") or [], state.get("results") or [])
    lines = ["# 筛选账本（全量）", ""]
    lines.append("共 %d 条记录；included=%d excluded=%d uncertain=%d merged_duplicate=%d"
                 % (s["total_records"], len(s["included"]), len(s["excluded"]),
                    len(s["uncertain"]), len(s["merged_duplicates"])))
    lines.append("")

    def table(title, rows):
        out = ["## %s (%d)" % (title, len(rows)), ""]
        out.append("| record_id | title | year | database | sub_question_ids | reason |")
        out.append("|-|-|-|-|-|-|")
        for r in rows:
            out.append("| %s | %s | %s | %s | %s | %s |" % (
                str(r.get("record_id") or ""),
                str(r.get("title") or "").replace("|", "\\|"),
                str(r.get("year") or ""),
                str(r.get("database") or ""),
                ",".join(map(str, r.get("sub_question_ids") or [])),
                str(r.get("screening_reason") or "").replace("|", "\\|"),
            ))
        out.append("")
        return out

    lines += table("included", s["included"])
    lines += table("excluded", s["excluded"])
    lines += table("uncertain", s["uncertain"])
    lines += table("merged_duplicates", s["merged_duplicates"])
    lines.append(s["note"])
    return "\n".join(lines) + "\n"


def render_plan(state: dict) -> str:
    lines = ["# 检索计划与拆解", ""]
    lines.append("## 研究问题")
    lines.append("")
    lines.append("- " + (state.get("research_question") or "（未填写）"))
    lines.append("")
    for section, key in (
        ("子问题拆解", "sub_questions"),
        ("查询计划", "query_plan"),
        ("术语扩展", "term_expansion"),
        ("停止判断", "stop_decision"),
    ):
        lines.append("## %s" % section)
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(state.get(key), ensure_ascii=False, indent=2, default=str))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def load_input_json(args, issues: list):
    if getattr(args, "inline", None):
        try:
            return json.loads(args.inline)
        except (json.JSONDecodeError, ValueError) as e:
            issues.append("ERR: inline json 无法解析: %s" % e)
            return None
    if getattr(args, "file", None):
        return read_json_file(args.file, issues)
    issues.append("ERR: 本命令需要 --file 或 --inline 提供输入")
    return None


def assemble_state(state: dict, output: str, work: str, args, issues: list) -> str:
    outdir = writable_dir(output, work, issues)
    result = build_result(state)
    atomic_write_json(os.path.join(outdir, "result.json"), result, issues)
    atomic_write_text(os.path.join(outdir, "screening.md"), render_screening(state), issues)
    atomic_write_text(os.path.join(outdir, "plan.md"), render_plan(state), issues)
    return outdir


def cmd_run(args, issues: list) -> int:
    os.makedirs(args.work, exist_ok=True)
    state = default_state()
    state["runtime"]["started_at"] = iso_now()

    manifest = discover_input(args.input, issues)
    state["input_manifest"] = [
        {"name": f["name"], "size": f.get("size"), "sha1": f.get("sha1"), "kind": f["kind"]}
        for f in manifest
    ]
    summary = build_case_summary(manifest, issues)
    state["case_id"] = summary["case_id"]
    state["research_question"] = summary["research_question"]
    state["output_contract"] = summary["output_contract"]

    # classify issues collected so far
    for msg in issues:
        if msg.startswith("ERR:"):
            state["errors"].append(msg[4:].strip())
        else:
            state["degradations"].append(msg[4:].strip() if msg.startswith("DEG:") else msg)
    issues[:] = []

    if not manifest:
        state["status"] = "insufficient_evidence"
        state["status_notes"] = "输入目录为空或不可读，无任何科研材料；无法检索，未补造文献。"
        state["pending_human"].append("提供案例材料（case.json / task.md / materials/）后重跑 run。")
    elif not summary["case_found"]:
        state["status"] = "partial"
        state["status_notes"] = "未找到 case.json，依据 task.md 与其他材料继续；case_id 未知。"
        state["pending_human"].append("确认 case_id 与 output_contract。")
    elif summary["case_parse_error"] or summary["case_parsed"] is None:
        state["status"] = "input_error"
        state["status_notes"] = "case.json 无法解析为 JSON 对象；记录错误并落盘骨架，等待修复后增量更新。"
    elif not isinstance(summary["case_parsed"], dict):
        state["status"] = "input_error"
        state["status_notes"] = "case.json 顶层不是对象。"
    else:
        state["status"] = "partial"
        state["status_notes"] = "骨架已落盘；拆解、检索、筛选与停止判断需增量更新。"
    state["runtime"]["requests"] = getattr(args, "runtime_requests", None) if hasattr(args, "runtime_requests") else None

    add_change(state, "run", "创建 partial 骨架并落盘 result.json", state["status"])
    save_state(args.work, state, issues)
    # echo a case summary for the agent
    atomic_write_json(os.path.join(args.work, "case_summary.json"), summary, issues)
    assemble_state(state, args.output, args.work, args, issues)
    return 0


def cmd_set_plan(args, issues: list) -> int:
    state = load_state(args.work, issues)
    data = load_input_json(args, issues)
    if isinstance(data, dict):
        if "research_question" in data and data["research_question"] not in (None, ""):
            state["research_question"] = data["research_question"]
        for key in ("sub_questions", "query_plan", "term_expansion", "stop_decision"):
            if key in data:
                state[key] = data[key]
        add_change(state, "set-plan", "更新拆解/查询计划/术语扩展/停止判断", state.get("status"))
    save_state(args.work, state, issues)
    return 0


def cmd_import(args, issues: list) -> int:
    state = load_state(args.work, issues)
    data = load_input_json(args, issues)
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and "records" in data:
        records = data["records"]
        if "sources" in data and isinstance(data["sources"], list):
            state["sources"] = data["sources"]
    else:
        records = None
    import_records(state, records if records is not None else [], issues)
    if records is not None:
        add_change(state, "import", "导入并全量筛选 %d 条候选记录" % len(records), state.get("status"))
    save_state(args.work, state, issues)
    return 0


def cmd_evidence(args, issues: list) -> int:
    state = load_state(args.work, issues)
    data = load_input_json(args, issues)
    entries = data if isinstance(data, list) else (data.get("evidence") if isinstance(data, dict) else None)
    if entries is None:
        issues.append("ERR: evidence 需要数组或 {evidence:[...]}")
    elif isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                e2 = dict(e)
                e2.setdefault("timestamp", iso_now())
                e2.setdefault("kind", "query")
                state["evidence"].append(e2)
        add_change(state, "evidence", "追加 %d 条证据日志" % len(entries), state.get("status"))
    save_state(args.work, state, issues)
    return 0


def cmd_assemble(args, issues: list) -> int:
    state = load_state(args.work, issues)
    rt = state.setdefault("runtime", {})
    started = rt.get("started_at")
    if started:
        try:
            t0 = _dt.datetime.fromisoformat(str(started))
            rt["seconds"] = round((_dt.datetime.now().astimezone() - t0).total_seconds(), 1)
        except (ValueError, TypeError):
            rt["seconds"] = None
    rt["finished_at"] = iso_now()
    if getattr(args, "runtime_seconds", None) is not None:
        rt["seconds"] = args.runtime_seconds
    if getattr(args, "runtime_requests", None) is not None:
        rt["requests"] = args.runtime_requests
    add_change(state, "assemble", "组装 result.json / screening.md / plan.md", state.get("status"))
    save_state(args.work, state, issues)
    assemble_state(state, args.output, args.work, args, issues)
    return 0


# --------------------------------------------------------------------------- #
# selfcheck
# --------------------------------------------------------------------------- #

def run_checks(state: dict, result: dict) -> list:
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("case_id", bool(result.get("case_id")), "case_id=%r" % result.get("case_id"))
    add("status_valid", result.get("status") in VALID_STATUS, "status=%r" % result.get("status"))

    sqs = state.get("sub_questions") or []
    add("sub_questions_present", len(sqs) >= 1, "%d 条子问题" % len(sqs))
    sq_ok = True
    for i, sq in enumerate(sqs):
        if not isinstance(sq, dict) or not (sq.get("id") or sq.get("text") or sq.get("question")):
            sq_ok = False
            break
    add("sub_questions_typed", sq_ok, "每条子问题含 id 与 text/question")

    coverage_ok = True
    for sq in sqs:
        sq_id = (sq.get("id") if isinstance(sq, dict) else None)
        has = any(sq_id in (r.get("sub_question_ids") or []) for r in (state.get("results") or []))
        if not has and sq_id:
            gaps = state.get("uncertainty", {}).get("coverage_gaps") or []
            if not any(isinstance(g, dict) and g.get("sub_question_id") == sq_id for g in gaps) \
               and sq_id not in [str(x) for x in gaps]:
                coverage_ok = False
                break
    add("sub_question_coverage", coverage_ok, "每个子问题有 included 证据或已列入 coverage_gaps")

    results = state.get("results") or []
    if result.get("status") == "insufficient_evidence":
        add("results_or_insufficient", True, "材料不足，已如实标注，无结果属预期")
    else:
        add("results_or_insufficient", len(results) >= 1, "%d 条 included 结果" % len(results))

    screening = result.get("screening") or {}
    total = screening.get("total_records", 0)
    ledgered = (len(screening.get("included", [])) + len(screening.get("excluded", []))
                + len(screening.get("uncertain", [])) + len(screening.get("merged_duplicates", [])))
    add("screening_complete", ledgered == total, "账本覆盖 %d/%d 条记录" % (ledgered, total))
    add("evidence_present", len(state.get("evidence") or []) >= 1, "%d 条证据日志" % len(state.get("evidence") or []))
    add("change_log_present", len(state.get("change_log") or []) >= 1, "%d 条变更" % len(state.get("change_log") or []))

    for r in results:
        ev = r.get("evidence")
        if not (isinstance(ev, dict) and ev.get("locator") and ev.get("quote")):
            add("result_evidence_quote", False, "结果 %r 缺少 locator+quote" % r.get("id"))
            break
    else:
        add("result_evidence_quote", True, "所有 included 结果带 locator+quote" if results else "无结果，跳过证据溯源检查")

    return checks


def cmd_selfcheck(args, issues: list) -> int:
    state = load_state(args.work, issues)
    result = build_result(state)
    checks = run_checks(state, result)
    lines = []
    all_pass = True
    for c in checks:
        if not c["ok"]:
            all_pass = False
        lines.append("[%s] %s — %s" % ("PASS" if c["ok"] else "FAIL", c["name"], c["detail"]))
    report = "\n".join(lines) + "\n"
    print(report)
    outdir = writable_dir(args.output, args.work, issues)
    atomic_write_text(os.path.join(outdir, "selfcheck.md"), report, issues)
    return 0 if all_pass else 1
