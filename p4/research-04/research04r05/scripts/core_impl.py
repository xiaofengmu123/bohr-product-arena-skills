# -*- coding: utf-8 -*-
"""Shared implementation for the research-frontier-contract skill.

Responsibilities (all stdlib-only, deterministic):
  * fault-tolerant text/JSON reading with encoding fallback;
  * input discovery (control files vs. literature materials);
  * output_contract type inference and field-by-field alignment;
  * deterministic mechanical extraction (years/titles/terms/candidates);
  * atomic writes and change_log bookkeeping;
  * report/table rendering.
"""

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

VERSION = "2.0.0"

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
CONTROL_NAMES = {"case.json", "task.md", "input.json", "manifest.json"}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 200

YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
GAP_PAT = re.compile(
    r"future work|limitation|open question|remains? (?:unclear|unknown|open)|"
    r"still (?:unknown|unclear)|有待|尚不|尚待|未解决|进一步研究|需要进一步|remains to be",
    re.IGNORECASE,
)
CONFLICT_PAT = re.compile(
    r"however|in contrast|versus|vs\.|conflict(?:ing)?|discrepan|inconsisten|"
    r"不一致|矛盾|冲突|争议",
    re.IGNORECASE,
)

STOPWORDS = set(
    "the a an of to in for on with and or by is are was were be been being as at "
    "from this that these those it its we they he she which who whom whose not "
    "no but if then than so such into over under between after before during "
    "among through against also can may will would should could have has had do "
    "does did more most other some any each all both about into using based used "
    "use new one two three first study results show shown".split()
)

ALIASES = {
    "case_id": "case_id", "id": "case_id", "challenge_id": "case_id",
    "status": "status",
    "research_question": "research_question", "question": "research_question",
    "questions": "research_question",
    "protocol": "protocol", "screening_protocol": "protocol",
    "time_trend": "time_trend", "trend": "time_trend", "trends": "time_trend",
    "time_trends": "time_trend",
    "topic_evolution": "topic_evolution", "topic": "topic_evolution",
    "evolution": "topic_evolution", "topics": "topic_evolution",
    "representative_works": "representative_works", "works": "representative_works",
    "representative_work": "representative_works",
    "research_gaps": "research_gaps", "gaps": "research_gaps", "gap": "research_gaps",
    "evidence_map": "evidence_map", "evidence": "evidence_map",
    "conflicts": "conflicts", "conflict": "conflicts",
    "sources": "sources", "source": "sources",
    "uncertainty": "uncertainty", "uncertainties": "uncertainty",
    "pending_human": "pending_human", "pending": "pending_human",
    "change_log": "change_log", "changelog": "change_log", "change_logs": "change_log",
    "limitations": "limitations", "limitation": "limitations",
    "errors": "errors", "error": "errors",
    "degradations": "degradations", "degradation": "degradations",
    "runtime": "runtime",
    "deliverable": "deliverable", "deliverables": "deliverables",
    "report": "deliverable",
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def decode_text(data):
    """Return (text, encoding_or_error)."""
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace"), "latin-1(replace)"


def read_text_safe(path):
    """Read a text file with encoding fallback; never raises."""
    try:
        data = read_bytes(path)
        return decode_text(data)[0]
    except Exception as exc:  # pragma: no cover - defensive
        return ""


def load_json_safe(path):
    """Return (obj_or_None, error_or_None)."""
    try:
        text = read_text_safe(path)
        if text.lstrip().startswith("\ufeff"):
            text = text.lstrip()[1:]
        return json.loads(text), None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def atomic_write(path, obj, ensure_ascii=False):
    """Write JSON atomically (temp file + os.replace)."""
    ensure_dir(os.path.dirname(path) or ".")
    text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=2) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

def is_control_name(name):
    base = os.path.basename(name).lower()
    return base in CONTROL_NAMES or base.endswith(".case.json")


def discover_input(root):
    """Walk root deterministically; return (files, control_paths)."""
    files = []
    controls = []
    if not os.path.isdir(root):
        return files, controls
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            abs_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_path, root).replace("\\", "/")
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                size = -1
            entry = {"rel": rel, "abs": abs_path, "size": size}
            if is_control_name(fn):
                controls.append(entry)
            else:
                files.append(entry)
    return files, controls


def classify_kind(rel):
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".md", ".txt", ".tex", ".bib"):
        return "text"
    if ext == ".json":
        return "json"
    if ext == ".csv":
        return "csv"
    if ext in (".xml", ".html", ".htm"):
        return "text"
    return "other"


def pdf_to_text(pdf_path, work_materials_dir):
    """Extract text from a PDF; returns (text, method, ok)."""
    candidates = []
    for tool in ("pdftotext",):
        try:
            out = os.path.join(work_materials_dir, "pdf_extract.txt")
            cp = subprocess.run(
                [tool, "-layout", pdf_path, out],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            )
            if cp.returncode == 0 and os.path.exists(out):
                candidates.append((read_text_safe(out), "pdftotext"))
        except Exception:
            pass
    # platform tool via env hook
    tool_env = os.environ.get("PDF_TEXT_TOOL")
    if not candidates and tool_env:
        try:
            cp = subprocess.run(
                [tool_env, pdf_path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
            )
            if cp.returncode == 0:
                candidates.append((cp.stdout.decode("utf-8", "replace"), "pdf-text-tool"))
        except Exception:
            pass
    if candidates:
        text, method = candidates[0]
        if text.strip():
            return text, method, True
    return "", "unparsed", False


def extract_materials(files, work_dir):
    """Extract text for each material file; returns list of doc dicts."""
    ensure_dir(work_dir)
    materials_dir = os.path.join(work_dir, "materials")
    ensure_dir(materials_dir)
    docs = []
    for i, f in enumerate(files):
        doc = {
            "id": "mat_%03d" % (i + 1),
            "rel": f["rel"],
            "abs": f["abs"],
            "size": f["size"],
            "kind": classify_kind(f["rel"]),
            "text": "",
            "text_path": None,
            "parse_ok": True,
            "parse_method": None,
        }
        if f["size"] > MAX_FILE_BYTES:
            doc["parse_ok"] = False
            doc["oversize"] = True
            docs.append(doc)
            continue
        if doc["kind"] == "pdf":
            text, method, ok = pdf_to_text(f["abs"], materials_dir)
            doc["text"] = text
            doc["parse_method"] = method
            doc["parse_ok"] = ok
        else:
            text = read_text_safe(f["abs"])
            doc["text"] = text
            doc["parse_method"] = "text-decode"
            doc["parse_ok"] = bool(text) or f["size"] == 0
        # persist a normalized copy for quote locating
        if doc["text"]:
            out_rel = "materials/%s.txt" % doc["id"]
            out_abs = os.path.join(work_dir, out_rel)
            write_text(out_abs, doc["text"])
            doc["text_path"] = out_rel
        docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Contract alignment (core differentiator)
# ---------------------------------------------------------------------------

def infer_type(desc):
    s = (desc or "").strip().lower()
    # pipe-separated token list -> enum/string (e.g. "ok | partial | input_error")
    if "|" in s and re.fullmatch(r"[a-z_0-9| ]+", s):
        return "string"
    if any(k in s for k in ("数组", "array", "list", "[]", "清单", "列表")):
        return "array"
    if any(k in s for k in ("对象", "object", "dict", "{}", "块", "结构")):
        return "object"
    if any(k in s for k in ("数值", "数字", "number", "int", "float", "整数", "计数")):
        return "number"
    if any(k in s for k in ("布尔", "boolean", "bool")):
        return "boolean"
    if any(k in s for k in ("string", "字符串", "文本", "路径", "枚举", "回填", "相对路径")):
        return "string"
    return "object"


def type_default(t):
    return {"string": "", "object": {}, "array": [], "number": 0, "boolean": False}.get(t, {})


def actual_type(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def type_matches(value, declared):
    return actual_type(value) == declared


def is_empty(value):
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


def coerce_type(value, t):
    """Only used to produce a default value of the declared type."""
    if t == "string":
        return "" if value is None else str(value)
    if t == "array":
        return value if isinstance(value, list) else []
    if t == "object":
        return value if isinstance(value, dict) else {}
    if t == "number":
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    if t == "boolean":
        return value if isinstance(value, bool) else False
    return value


def find_source(key, analysis):
    k = (key or "").strip().lower()
    if k in ALIASES and ALIASES[k] in analysis:
        return ALIASES[k]
    for src in (
        "time_trend", "topic_evolution", "representative_works", "research_gaps",
        "evidence_map", "change_log", "research_question", "protocol", "conflicts",
    ):
        if src in k or k in src:
            return src
    return None


def align_contract(contract, analysis):
    """Return (aligned_values_dict, alignment_records_list).

    Honest-value rule: a non-empty analysis value is preserved as-is even if its
    actual type differs from the declared type; the discrepancy is recorded in
    the alignment note instead of being silently coerced. Only empty or missing
    values fall back to the declared-type default.
    """
    values = {}
    records = []
    if not isinstance(contract, dict):
        return values, records
    for key in sorted(contract.keys()):
        desc = contract.get(key)
        t = infer_type(desc)
        source = find_source(key, analysis)
        value = analysis.get(source) if source else None
        if is_empty(value):
            value = type_default(t)
            value_status = "empty" if source else "not_reported"
            note = "" if source else "契约字段无对应分析源，置类型缺省值，待深度分析或人工补全"
        else:
            value_status = "filled"
            if type_matches(value, t):
                note = ""
            else:
                note = "声明类型 %s 与分析值实际类型 %s 不一致，保留分析值原结构，请人工复核口径" % (
                    t, actual_type(value))
        values[key] = value
        records.append({
            "contract_key": key,
            "declared": (desc or "").strip(),
            "inferred_type": t,
            "actual_type": actual_type(value),
            "source": source or "n/a",
            "value_status": value_status,
            "note": note,
        })
    return values, records


def resolve_case_id(case, task_text):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        m = re.search(r"challenge[-_ ]?id[:：\s]+([A-Za-z0-9_-]+)", task_text, re.I)
        if m:
            return m.group(1)
    return "unknown"


def resolve_status_enum(contract):
    """Best-effort: extract a declared status enum from the contract description."""
    if isinstance(contract, dict):
        desc = contract.get("status") or ""
        found = re.findall(r"[a-z_]{2,}", (desc or "").lower())
        allowed = [w for w in found if w in
                   ("ok", "partial", "insufficient_evidence", "input_error",
                    "answered", "not_reported", "unknown")]
        if allowed:
            return allowed
    return None


# ---------------------------------------------------------------------------
# Skeleton + mechanical extraction
# ---------------------------------------------------------------------------

def summarize(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return "object{%s}" % ",".join(sorted(value.keys())[:6])
    if isinstance(value, list):
        return "list[%d]" % len(value)
    if isinstance(value, str):
        return "str[%d]" % len(value)
    return str(value)


def build_skeleton(case, task_text, contract, started_at=None):
    case_id = resolve_case_id(case, task_text)
    analysis = {
        "case_id": case_id,
        "status": "partial",
        "research_question": {"question": None, "scope": None,
                              "sub_questions": [], "domain_note": None},
        "protocol": {"inclusion_criteria": [], "exclusion_criteria": [],
                     "search_scope": None, "date_definition_note": None},
        "time_trend": {"unit_note": None, "distribution": [], "windows": [], "claims": []},
        "topic_evolution": {"stage_divisions": [], "term_mapping": [],
                            "events": [], "claims": []},
        "representative_works": {"criteria": [], "stages": []},
        "research_gaps": {"gaps": [], "excluded_not_gaps": []},
        "evidence_map": [],
        "conflicts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": started_at or now_iso(),
                    "phase": "skeleton", "script_version": VERSION},
        "deliverable": "report.md",
        "deliverables": ["report.md", "evidence_map.json",
                         "tables/documents.csv", "tables/year_distribution.csv",
                         "tables/term_frequency.csv"],
        "coverage": {"evidence_excluded": [], "search_omissions": [],
                     "sample_boundary": None},
    }
    aligned, records = align_contract(contract, analysis)
    result = dict(analysis)
    result.update(aligned)
    result["contract_alignment"] = records
    result["revision"] = 0
    result["change_log"] = [{
        "time": now_iso(), "action": "skeleton",
        "reason": "前3分钟落盘骨架：固定字段 + output_contract 逐字段对齐",
        "before": None, "after": summarize(result),
    }]
    status_enum = resolve_status_enum(contract)
    if status_enum:
        result["_declared_status_enum"] = status_enum
    return result


def mechanical_extract(docs, work_dir):
    """Deterministic baseline extraction; returns dict."""
    out = {
        "documents": [], "year_distribution": [], "terms": [],
        "gap_candidates": [], "conflict_candidates": [],
        "representative_candidates": [],
    }
    year_counts = {}
    term_freq = {}
    for d in docs:
        text = d.get("text") or ""
        lines = [ln for ln in text.splitlines() if ln.strip()]
        doc = {
            "id": d["id"], "title": None, "year": None, "year_source": None,
            "path": d["rel"], "kind": d["kind"], "parse_ok": d.get("parse_ok", True),
            "parse_method": d.get("parse_method"),
            "text_path": d.get("text_path"),
        }
        if lines:
            doc["title"] = lines[0].strip()[:200]
        head = "\n".join(lines[:40])
        m = YEAR_RE.search(head)
        if m:
            doc["year"] = int(m.group(1))
            doc["year_source"] = "text_header_heuristic"
        out["documents"].append(doc)
        if doc["year"] is not None:
            year_counts[doc["year"]] = year_counts.get(doc["year"], 0) + 1
        for tok in re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", text.lower()):
            if tok not in STOPWORDS and len(tok) < 40:
                term_freq[tok] = term_freq.get(tok, 0) + 1
        for i, ln in enumerate(lines):
            if GAP_PAT.search(ln):
                out["gap_candidates"].append(
                    {"doc_id": d["id"], "line": i + 1, "snippet": ln.strip()[:300]})
            if CONFLICT_PAT.search(ln):
                out["conflict_candidates"].append(
                    {"doc_id": d["id"], "line": i + 1, "snippet": ln.strip()[:300]})
        if doc["title"]:
            out["representative_candidates"].append({
                "doc_id": d["id"], "title": doc["title"], "year": doc["year"],
                "kind": "candidate", "rationale": "机械候选，待 Agent 按阶段/首创/里程碑核验",
            })
    out["year_distribution"] = [
        {"year": y, "count": c,
         "doc_ids": [x["id"] for x in out["documents"] if x["year"] == y]}
        for y, c in sorted(year_counts.items())
    ]
    out["terms"] = [
        {"term": t, "count": c}
        for t, c in sorted(term_freq.items(), key=lambda kv: (-kv[1], kv[0]))[:100]
    ]
    return out


def append_change_log(result, action, reason, before, after):
    result.setdefault("change_log", [])
    result["change_log"].append({
        "time": now_iso(), "action": action, "reason": reason,
        "before": summarize(before), "after": summarize(after),
    })
    result["revision"] = int(result.get("revision", 0)) + 1
    return result


def commit(result, patch, action, reason):
    """Apply a top-level patch and bookkeep change_log."""
    before = {k: result.get(k) for k in patch}
    for k, v in patch.items():
        result[k] = v
    append_change_log(result, action, reason, before, patch)
    return result


def re_align(result, contract):
    """Recompute aligned contract fields and the alignment table in place."""
    if not isinstance(contract, dict):
        result["contract_alignment"] = []
        return result
    aligned, records = align_contract(contract, result)
    for k, v in aligned.items():
        result[k] = v
    result["contract_alignment"] = records
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_report(result, out_path):
    lines = ["# 研究前沿与趋势分析报告", ""]
    lines.append("- case_id: %s" % result.get("case_id", "unknown"))
    lines.append("- status: %s" % result.get("status", "unknown"))
    lines.append("- revision: %s" % result.get("revision", 0))
    lines.append("")
    lines.append("## 研究问题")
    rq = result.get("research_question") or {}
    lines.append("- question: %s" % (rq.get("question") or "未确定"))
    lines.append("- scope: %s" % (rq.get("scope") or "未确定"))
    lines.append("")
    lines.append("## 时间趋势")
    tt = result.get("time_trend") or {}
    lines.append("- unit_note: %s" % (tt.get("unit_note") or "未确定"))
    for row in (tt.get("distribution") or []):
        lines.append("- %s: %d 篇 (%s)" % (row.get("year"), row.get("count"),
                                            ", ".join(row.get("doc_ids", []))))
    lines.append("")
    lines.append("## 主题演化")
    te = result.get("topic_evolution") or {}
    for ev in (te.get("events") or []):
        lines.append("- [%s] %s (%s) — %s" % (
            ev.get("type"), ev.get("term"), ev.get("period"), ev.get("cause")))
    lines.append("")
    lines.append("## 代表性工作")
    rw = result.get("representative_works") or {}
    for st in (rw.get("stages") or []):
        lines.append("- %s (%s)" % (st.get("stage"), st.get("period")))
        for w in st.get("works", []):
            lines.append("  - %s (%s) [%s]" % (w.get("title"), w.get("year"), w.get("kind")))
    lines.append("")
    lines.append("## 研究空白")
    rg = result.get("research_gaps") or {}
    for g in (rg.get("gaps") or []):
        lines.append("- [%s] %s" % (g.get("type"), g.get("description")))
    lines.append("")
    lines.append("## 契约对齐")
    for rec in (result.get("contract_alignment") or []):
        lines.append("- %s (%s) ← %s [%s]" % (
            rec.get("contract_key"), rec.get("inferred_type"),
            rec.get("source"), rec.get("value_status")))
    lines.append("")
    lines.append("## 不确定性 / 待人工")
    for u in (result.get("uncertainty") or []):
        if isinstance(u, dict):
            lines.append("- %s" % (u.get("description") or u.get("kind") or str(u)))
        else:
            lines.append("- %s" % str(u))
    for p in (result.get("pending_human") or []):
        if isinstance(p, dict):
            lines.append("- [待人工] %s" % (p.get("item") or str(p)))
        else:
            lines.append("- [待人工] %s" % str(p))
    lines.append("")
    write_text(out_path, "\n".join(lines) + "\n")


def render_tables(result, tables_dir):
    ensure_dir(tables_dir)

    docs_path = os.path.join(tables_dir, "documents.csv")
    with open(docs_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "title", "year", "year_source", "path", "kind", "parse_ok"])
        for d in (result.get("_baseline", {}).get("documents") or []):
            w.writerow([d.get("id"), d.get("title"), d.get("year"),
                        d.get("year_source"), d.get("path"), d.get("kind"),
                        d.get("parse_ok")])

    year_path = os.path.join(tables_dir, "year_distribution.csv")
    with open(year_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "count", "doc_ids"])
        for row in (result.get("time_trend", {}).get("distribution") or []):
            w.writerow([row.get("year"), row.get("count"),
                        ";".join(row.get("doc_ids", []))])

    term_path = os.path.join(tables_dir, "term_frequency.csv")
    with open(term_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["term", "count"])
        for row in (result.get("_baseline", {}).get("terms") or []):
            w.writerow([row.get("term"), row.get("count")])


def write_evidence_map(result, out_path):
    atomic_write(out_path, {
        "case_id": result.get("case_id"),
        "items": result.get("evidence_map", []),
        "count": len(result.get("evidence_map", [])),
    })
