"""core_impl.py -- implementation of the research-methods & statistics audit helper.

Standard library only. Deterministic: no randomness, no wall-clock-dependent
content except the explicitly recorded timestamps. Subcommands:

  discover   scan /input, extract readable text (PDF page-level / txt / md /
             csv / json / docx), build the locator index and manifest.
  scaffold   write an honest partial result.json skeleton + report.md early.
  finalize   merge findings, run the deterministic numeric pre-check, validate
             the schema, re-render result.json + report.md.
  run        discover -> scaffold -> finalize in one pass.
"""

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

FACETS = [
    "research_design",
    "variable_definition",
    "statistical_methods",
    "sample_size_power",
    "results_reporting",
]

FINDING_TYPES = [
    "factual_error",
    "insufficient_evidence",
    "normative_issue",
    "advisory",
    "pending_review",
]

SEVERITIES = ["critical", "major", "minor", "advisory", "pending"]

CHECK_CLASSES = [
    "fact_check",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argumentation_chain",
    "academic_norms",
]

MANDATORY_FINDING_FIELDS = ["id", "facet", "locator", "quote", "severity", "suggestion"]

CANONICAL_TOP_LEVEL = {
    "case_id", "status", "result_status", "summary", "review_report",
    "evidence", "contract_alignment", "output_contract", "sources",
    "uncertainty", "pending_human", "limitations", "errors", "degradations",
    "numeric_checks", "change_log", "runtime",
}

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

STAT_TERMS = {
    "research_design": ["study design", "randomized", "randomised", "cohort",
                        "case-control", "cross-sectional", "blinding", "blind",
                        "randomization", "研究设计", "随机", "队列", "病例对照",
                        "盲法", "主要结局", "primary outcome", "primary endpoint"],
    "variable_definition": ["exposure", "outcome", "covariate", "baseline",
                            "categorical", "continuous", "missing", "缺失",
                            "暴露", "结局", "协变量", "变量", "定义", "切点"],
    "statistical_methods": ["t-test", "t test", "anova", "chi-square", "chi square",
                            "logistic", "regression", "cox", "kaplan", "mann-whitney",
                            "wilcoxon", "kruskal", "fisher", "mixed model", "gee",
                            "bonferroni", "多重比较", "回归", "检验", "校正"],
    "sample_size_power": ["sample size", "power", "effect size", "alpha",
                          "significance level", "样本量", "功效", "把握度",
                          "效应量", "检验水准"],
    "results_reporting": ["p =", "p=", "p <", "p>", "confidence interval",
                          "95%", "odds ratio", "hazard ratio", "risk ratio",
                          "standard deviation", "置信区间", "p 值", "显著",
                          "表", "图", "Table", "Fig"],
}

P_RE = re.compile(r"(?i)\bp\s*(?:=|<=|>=|<|>|\u2264|\u2265|\u2260)?\s*(0?\.\d+(?:[eE][+-]?\d+)?)")
SIG_WORDS_POS = re.compile(r"显著|统计学意义|统计学差异|差异有统计学|有统计学意义|significant")
SIG_WORDS_NEG = re.compile(r"(?:不|未|无|没有|未见)[\u4e00-\u9fff]{0,8}(?:显著|统计学)|"
                           r"non[\s-]?significan|not\s+significant|no\s+significant")
CI_RE = re.compile(r"(?i)95\s*%\s*(?:置信区间|CI)\s*[:：]?\s*[\[(]?\s*(-?\d+(?:\.\d+)?)\s*"
                   r"[,，;；\u2013\u2014\-~至到]\s*(-?\d+(?:\.\d+)?)\s*[\])]?")
N_RE = re.compile(r"(?i)\b[Nn]\s*=\s*(\d+)")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_robust(path):
    data = Path(path).read_bytes()
    last = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            last = exc
            continue
    raise ValueError("cannot decode JSON: %s (%s)" % (path, last))


def load_json_or_none(path):
    try:
        return load_json_robust(path)
    except Exception:
        return None


def read_text_robust(path):
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def write_json(path, obj):
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# discover
# --------------------------------------------------------------------------- #

def _extract_pdf(pdf_path, stem, texts_dir):
    if shutil.which("pdftotext") is None:
        return {"status": "needs_ocr", "pages": 0, "text_files": [],
                "note": "pdftotext not available; mark for OCR"}
    full_path = texts_dir / (stem + ".full.txt")
    try:
        subprocess.run(["pdftotext", "-enc", "UTF-8", str(pdf_path), str(full_path)],
                       check=True, capture_output=True, timeout=180)
    except Exception as exc:
        return {"status": "error", "pages": 0, "text_files": [],
                "note": "pdftotext failed: %s" % exc}
    if not full_path.exists():
        return {"status": "empty", "pages": 0, "text_files": [],
                "note": "no text layer produced"}
    full = full_path.read_text(encoding="utf-8", errors="replace")
    pages = full.split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    text_files = []
    for i, page_text in enumerate(pages, 1):
        fname = "%s.p%03d.txt" % (stem, i)
        (texts_dir / fname).write_text(page_text, encoding="utf-8")
        text_files.append(fname)
    total = sum(len(t.strip()) for t in pages)
    if total < 20:
        return {"status": "needs_ocr", "pages": len(pages), "text_files": text_files,
                "note": "no extractable text (scanned image?)"}
    return {"status": "ok", "pages": len(pages), "text_files": text_files, "note": ""}


def _docx_to_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs = []
    for p in root.iter("{%s}p" % W_NS):
        paragraphs.append("".join(t.text or "" for t in p.iter("{%s}t" % W_NS)))
    return "\n\n".join(paragraphs)


def _extract_docx(path, stem, texts_dir):
    try:
        text = _docx_to_text(path)
    except Exception as exc:
        return {"status": "error", "text_files": [], "note": "docx parse failed: %s" % exc}
    fname = stem + ".txt"
    (texts_dir / fname).write_text(text, encoding="utf-8")
    return {"status": "ok", "text_files": [fname], "note": ""}


def _extract_text(path, stem, texts_dir):
    if path.stat().st_size > MAX_FILE_SIZE:
        return {"status": "skipped_too_large", "text_files": [],
                "note": "file exceeds %d bytes" % MAX_FILE_SIZE}
    text = read_text_robust(path)
    fname = stem + ".txt"
    (texts_dir / fname).write_text(text, encoding="utf-8")
    return {"status": "ok", "text_files": [fname], "note": ""}


def discover(args):
    inp = Path(args.input)
    work = Path(args.work)
    texts_dir = work / "texts"
    work.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "generated_at": now_iso(),
        "input_dir": str(inp),
        "case": {"case_id": None, "has_case_json": False, "has_task_md": False,
                 "output_contract": None},
        "materials": [],
        "errors": [],
        "degradations": [],
    }

    case_path = inp / "case.json"
    if case_path.exists():
        try:
            case_data = load_json_robust(case_path)
            manifest["case"].update({
                "case_id": case_data.get("case_id") if isinstance(case_data, dict) else None,
                "has_case_json": True,
                "output_contract": (case_data.get("output_contract")
                                    if isinstance(case_data, dict) else None),
            })
            (work / "case.json").write_text(
                json.dumps(case_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            manifest["errors"].append({"kind": "case_json_unreadable", "detail": str(exc)})
            manifest["case"]["has_case_json"] = True
    else:
        manifest["case"]["has_case_json"] = False

    task_path = inp / "task.md"
    manifest["case"]["has_task_md"] = task_path.exists()

    material_paths = sorted(
        p for p in inp.rglob("*")
        if p.is_file() and p.name not in ("case.json", "task.md"))

    for p in material_paths:
        rel = str(p.relative_to(inp)).replace("\\", "/")
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", rel)
        suffix = p.suffix.lower()
        entry = {"relpath": rel, "kind": "unknown", "size": p.stat().st_size,
                 "status": "unread", "text_files": [], "note": ""}
        try:
            if suffix == ".pdf":
                entry["kind"] = "pdf"
                entry.update(_extract_pdf(p, stem, texts_dir))
            elif suffix == ".docx":
                entry["kind"] = "docx"
                entry.update(_extract_docx(p, stem, texts_dir))
            elif suffix in (".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm"):
                entry["kind"] = "text"
                entry.update(_extract_text(p, stem, texts_dir))
            elif suffix in (".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp"):
                entry["kind"] = "image"
                entry["status"] = "not_parsed"
                entry["note"] = "image not parsed; review as figure if needed"
            elif suffix in (".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods"):
                entry["kind"] = "office_binary"
                entry["status"] = "not_parsed"
                entry["note"] = "binary office format not parsed by this helper"
            else:
                entry["kind"] = "other"
                entry.update(_extract_text(p, stem, texts_dir))
        except Exception as exc:
            entry["status"] = "error"
            entry["note"] = str(exc)
        if entry["status"] in ("error", "not_parsed", "skipped_too_large"):
            manifest["degradations"].append({"relpath": rel, "status": entry["status"],
                                             "note": entry["note"]})
        manifest["materials"].append(entry)

    segments = _build_segments(manifest["materials"], texts_dir, inp)
    write_json(work / "segments.json", {"segments": segments})
    write_json(work / "terms.json", _build_terms(segments))
    write_json(work / "manifest.json", manifest)

    print("discover: %d materials, %d segments" % (len(manifest["materials"]), len(segments)))
    for e in manifest["errors"]:
        print("  error: %s" % e.get("detail"))
    for d in manifest["degradations"]:
        print("  degradation: %s (%s)" % (d.get("relpath"), d.get("status")))
    return 0


def _split_paragraphs(text):
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _build_segments(materials, texts_dir, inp):
    segments = []
    for m in materials:
        source = m.get("relpath", "")
        kind = m.get("kind", "unknown")
        for tf in m.get("text_files", []):
            tpath = texts_dir / tf
            if not tpath.exists():
                continue
            text = tpath.read_text(encoding="utf-8", errors="replace")
            if kind == "pdf":
                mpage = re.search(r"\.p(\d{3})\.txt$", tf)
                page = int(mpage.group(1)) if mpage else None
                for k, para in enumerate(_split_paragraphs(text), 1):
                    loc = "p.%s \u00b6%d" % (page, k) if page else "%s \u00b6%d" % (source, k)
                    segments.append({"locator": loc, "source": source, "page": page,
                                     "para": k, "text": para})
            else:
                for k, para in enumerate(_split_paragraphs(text), 1):
                    segments.append({"locator": "%s \u00b6%d" % (source, k), "source": source,
                                     "page": None, "para": k, "text": para})
    return segments


def _build_terms(segments):
    hits = {k: [] for k in STAT_TERMS}
    for seg in segments:
        low = seg["text"].lower()
        for key, terms in STAT_TERMS.items():
            if any(t.lower() in low for t in terms):
                hits[key].append(seg["locator"])
    return hits


# --------------------------------------------------------------------------- #
# output_contract alignment
# --------------------------------------------------------------------------- #

TYPE_DEFAULTS = {"string": "", "number": None, "integer": None, "boolean": False,
                 "array": [], "object": {}, "null": None}


def _contract_to_dict(contract):
    if contract is None:
        return {}
    if isinstance(contract, dict):
        return {str(k): v for k, v in contract.items()}
    if isinstance(contract, list):
        d = {}
        for item in contract:
            if isinstance(item, str):
                d[item] = None
            elif isinstance(item, dict):
                key = item.get("name") or item.get("field") or item.get("key") or item.get("id")
                if key is not None:
                    d[str(key)] = item
        return d
    return {}


def _default_for_contract_value(spec):
    if isinstance(spec, dict):
        if "default" in spec:
            return copy.deepcopy(spec["default"])
        t = str(spec.get("type", "")).strip().lower()
        if t in TYPE_DEFAULTS:
            return TYPE_DEFAULTS[t]
        if "enum" in spec and isinstance(spec["enum"], list) and spec["enum"]:
            return spec["enum"][0]
        if "items" in spec:
            return []
        return {}
    if isinstance(spec, str):
        t = spec.strip().lower()
        return TYPE_DEFAULTS.get(t, None)
    if isinstance(spec, (list, tuple)):
        return []
    return None


def apply_output_contract(result, contract):
    """Copy every output_contract field into result.json verbatim, at top level,
    and keep the raw contract under ``output_contract``. Missing values become
    null/[]/"" -- never fabricated. Returns (result, alignment)."""
    raw = copy.deepcopy(contract) if contract is not None else None
    result["output_contract"] = raw
    alignment = {"received": copy.deepcopy(raw), "fields": [],
                 "unknown_top_level_preserved": []}
    for key, spec in _contract_to_dict(contract).items():
        if not key:
            continue
        if key in result:
            alignment["fields"].append({"field": key, "action": "canonical_present",
                                        "default_ignored": True})
        else:
            default = _default_for_contract_value(spec)
            result[key] = default
            alignment["fields"].append({"field": key, "action": "added_with_default",
                                        "default": default})
    alignment["unknown_top_level_preserved"] = sorted(
        k for k in result.keys() if k not in CANONICAL_TOP_LEVEL)
    result["contract_alignment"] = alignment
    return result, alignment


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #

def canonical_skeleton(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "result_status": "\u672a\u68c0\u67e5",
        "summary": {
            "verdict": "unchecked",
            "overall_assessment": "",
            "counts": {"total": 0, **{s: 0 for s in SEVERITIES}},
            "by_facet": {f: 0 for f in FACETS},
            "by_finding_type": {t: 0 for t in FINDING_TYPES},
            "by_check_class": {c: 0 for c in CHECK_CLASSES},
        },
        "review_report": {"findings": [], "checked_facets": [],
                          "unchecked_facets": list(FACETS)},
        "evidence": {"evidence_map": {}},
        "contract_alignment": {},
        "output_contract": None,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "numeric_checks": [],
        "change_log": [],
        "runtime": {"started_at": now_iso(), "finished_at": None,
                    "elapsed_seconds": None, "model_requests_used": 0},
    }


def _initial_status(manifest, case_data):
    has_materials = bool(manifest and manifest.get("materials"))
    if not case_data and not has_materials:
        return "input_error"
    if not has_materials:
        return "insufficient_evidence"
    return "partial"


def scaffold(args):
    inp = Path(args.input)
    work = Path(args.work)
    out = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    (work / "findings").mkdir(parents=True, exist_ok=True)

    case_data = load_json_or_none(inp / "case.json")
    manifest = load_json_or_none(work / "manifest.json")
    case_id = None
    contract = None
    if isinstance(case_data, dict):
        case_id = case_data.get("case_id")
        contract = case_data.get("output_contract")
    case_id = case_id or "unknown"

    result = canonical_skeleton(case_id)
    result, _ = apply_output_contract(result, contract)
    result["status"] = _initial_status(manifest, case_data)
    result["errors"] = list((manifest or {}).get("errors", []))
    result["degradations"] = list((manifest or {}).get("degradations", []))
    if manifest:
        result["sources"] = [
            {"path": m.get("relpath"), "kind": m.get("kind"), "role": "material",
             "status": m.get("status")} for m in manifest.get("materials", [])]
    result["change_log"].append({
        "at": now_iso(), "phase": "scaffold", "action": "init",
        "detail": "partial skeleton; status=%s" % result["status"]})

    template = {
        "id": "F###",
        "facet": "research_design|variable_definition|statistical_methods|sample_size_power|results_reporting",
        "check_class": "fact_check|citation_support|method_design|statistical_reporting|argumentation_chain|academic_norms",
        "finding_type": "factual_error|insufficient_evidence|normative_issue|advisory|pending_review",
        "severity": "critical|major|minor|advisory|pending",
        "claim": "\u5ba1\u67e5\u4e3b\u5f20\uff08\u53ef\u590d\u6838\u7684\u4e00\u53e5\u5224\u65ad\uff09",
        "locator": "p.N \u00b6M | materials/x.txt \u00b6K",
        "quote": "\u5fc5\u987b\u9010\u5b57\u6458\u81ea\u539f\u6587",
        "basis": {"rule_id": "checklist rule id", "rule": "\u89e6\u53d1\u7684\u89c4\u5219"},
        "suggestion": "\u4fee\u6539\u5efa\u8bae",
        "confidence": "high|medium|low",
        "review_status": "confirmed|needs_review",
        "related_locators": [],
    }
    (work / "findings" / "_TEMPLATE.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_json(out / "result.json", result)
    render_report(result, out)
    write_json(work / "state.json", {
        "case_id": case_id, "phase": "scaffolded", "started_at": result["runtime"]["started_at"],
        "last_updated": now_iso(), "findings_count": 0,
        "checked_facets": [], "unchecked_facets": list(FACETS)})
    print("scaffold: result.json + report.md written; status=%s" % result["status"])
    return 0


# --------------------------------------------------------------------------- #
# findings + finalize
# --------------------------------------------------------------------------- #

def _normalize_finding(obj, index, problems):
    finding = dict(obj)
    for field in MANDATORY_FINDING_FIELDS:
        if field not in finding or finding[field] in (None, ""):
            finding[field] = ""
            problems.append("%s missing %s" % (finding.get("id") or index, field))
    if "id" not in finding or not finding["id"]:
        finding["id"] = "F%03d" % index
    if finding.get("facet") not in FACETS:
        problems.append("%s invalid facet=%r" % (finding["id"], finding.get("facet")))
    if finding.get("finding_type") not in FINDING_TYPES:
        finding["finding_type"] = "pending_review"
        problems.append("%s finding_type coerced to pending_review" % finding["id"])
    if finding.get("severity") not in SEVERITIES:
        finding["severity"] = "pending"
        problems.append("%s severity coerced to pending" % finding["id"])
    if finding.get("check_class") not in CHECK_CLASSES:
        finding["check_class"] = "argumentation_chain"
    finding.setdefault("claim", "")
    finding.setdefault("basis", {"rule_id": "not_specified", "rule": ""})
    finding.setdefault("confidence", "medium")
    finding.setdefault("review_status", "needs_review")
    finding.setdefault("related_locators", [])
    return finding


def _load_findings(work):
    fdir = work / "findings"
    if not fdir.exists():
        return [], []
    findings, problems = [], []
    for p in sorted(fdir.glob("*.json")):
        if p.name.startswith("_"):
            continue
        obj = load_json_or_none(p)
        if isinstance(obj, dict) and obj.get("id"):
            findings.append(_normalize_finding(obj, len(findings) + 1, problems))
        elif obj is not None:
            problems.append("non-object finding file ignored: %s" % p.name)
    findings.sort(key=lambda f: f["id"])
    return findings, problems


def _default_verdict(findings):
    sev = [f.get("severity") for f in findings]
    if "critical" in sev:
        return "\u5b58\u5728\u53ef\u80fd\u4f7f\u4e3b\u8981\u7ed3\u8bba\u5931\u6548\u7684\u786c\u4f24"
    if "major" in sev:
        return "\u5b58\u5728\u9700\u4fee\u6539\u7684\u5b9e\u8d28\u6027\u65b9\u6cd5\u5b66\u95ee\u9898"
    if any(s in ("minor", "advisory") for s in sev):
        return "\u5b58\u5728\u5e94\u4fee\u6539\u4f46\u4e0d\u6539\u53d8\u4e3b\u8981\u7ed3\u8bba\u7684\u95ee\u9898"
    if any(f.get("finding_type") == "pending_review" for f in findings):
        return "\u5b58\u5728\u5f85\u590d\u6838\u9879"
    return "\u672a\u53d1\u73b0\u786e\u8bc4\u7684\u7f3a\u9677"


def _compute_summary(findings, meta):
    counts = {"total": len(findings), **{s: 0 for s in SEVERITIES}}
    by_facet = {f: 0 for f in FACETS}
    by_type = {t: 0 for t in FINDING_TYPES}
    by_class = {c: 0 for c in CHECK_CLASSES}
    for f in findings:
        counts[f.get("severity", "pending")] = counts.get(f.get("severity"), 0) + 1
        by_facet[f.get("facet")] = by_facet.get(f.get("facet"), 0) + 1
        by_type[f.get("finding_type", "pending_review")] = \
            by_type.get(f.get("finding_type"), 0) + 1
        by_class[f.get("check_class")] = by_class.get(f.get("check_class"), 0) + 1
    verdict = meta.get("overall_verdict") or _default_verdict(findings)
    return {"verdict": verdict,
            "overall_assessment": meta.get("overall_assessment", ""),
            "counts": counts, "by_facet": by_facet,
            "by_finding_type": by_type, "by_check_class": by_class}


def _numeric_checks(segments, meta):
    checks = []
    resolutions = meta.get("numeric_check_resolutions", {}) or {}
    n_locs = {}
    counter = [0]

    def next_id():
        counter[0] += 1
        return "NC-%d" % counter[0]

    for seg in segments:
        loc = seg.get("locator", "")
        text = seg.get("text", "")
        sig_neg = bool(SIG_WORDS_NEG.search(text))
        sig_pos = (not sig_neg) and bool(SIG_WORDS_POS.search(text))
        ci_matches = CI_RE.findall(text)
        for pv in P_RE.findall(text):
            try:
                pnum = float(pv)
            except ValueError:
                continue
            if sig_pos and pnum >= 0.05:
                checks.append({"id": next_id(), "kind": "p_vs_wording", "locator": loc,
                               "candidate": "p=%s \u4e0e\u201c\u663e\u8457\u201d\u8868\u8ff0\u5e76\u5b58"
                                            "\uff08p\u22650.05\uff09" % pv,
                               "status": "candidate", "finding_id": None})
            if sig_neg and pnum < 0.05:
                checks.append({"id": next_id(), "kind": "p_vs_wording", "locator": loc,
                               "candidate": "p=%s \u4e0e\u201c\u4e0d\u663e\u8457\u201d\u8868\u8ff0\u5e76\u5b58"
                                            "\uff08p<0.05\uff09" % pv,
                               "status": "candidate", "finding_id": None})
            for lo, hi in ci_matches:
                try:
                    lo_f, hi_f = float(lo), float(hi)
                except ValueError:
                    continue
                if pnum < 0.05 and (lo_f <= 0 <= hi_f or lo_f <= 1 <= hi_f):
                    checks.append({"id": next_id(), "kind": "p_vs_ci", "locator": loc,
                                   "candidate": "p=%s<0.05 \u4e14 95%% CI [%s,%s] "
                                                "\u8de8\u65e0\u6548\u503c" % (pv, lo, hi),
                                   "status": "candidate", "finding_id": None})
        for nv in N_RE.findall(text):
            n_locs.setdefault(int(nv), set()).add(loc)
    if len(n_locs) >= 2:
        vals = sorted(n_locs.keys())
        locs = sorted({l for s in n_locs.values() for l in s})
        checks.append({"id": next_id(), "kind": "n_inconsistency", "locator": "; ".join(locs),
                       "candidate": "\u6750\u6599\u4e2d\u51fa\u73b0\u591a\u4e2a N/n \u503c: %s"
                                    "\uff08\u53ef\u80fd\u4e3a\u603b\u6837\u672c vs \u5206\u7ec4\u6837\u672c\uff09"
                                    % vals, "status": "candidate", "finding_id": None})
    for c in checks:
        r = resolutions.get(c["id"])
        if isinstance(r, dict):
            c["status"] = r.get("status", c["status"])
            c["finding_id"] = r.get("finding_id")
    return checks


def _resolve_status(meta, manifest, findings, case_data, unchecked_facets):
    if meta.get("status"):
        return meta["status"]
    has_materials = bool(manifest and manifest.get("materials"))
    if not case_data and not has_materials:
        return "input_error"
    if not has_materials:
        return "insufficient_evidence"
    if unchecked_facets:
        return "partial"
    return "ok"


def _apply_meta(result, meta):
    result["result_status"] = meta.get("result_status") or result["result_status"]
    result["uncertainty"] = meta.get("uncertainty") or []
    result["pending_human"] = meta.get("pending_human") or []
    result["limitations"] = meta.get("limitations") or []
    rr = result["review_report"]
    checked = meta.get("checked_facets") or []
    rr["checked_facets"] = checked
    rr["unchecked_facets"] = [f for f in FACETS if f not in checked]
    result["runtime"]["model_requests_used"] = meta.get("model_requests_used", 0)


def finalize(args):
    inp = Path(args.input)
    work = Path(args.work)
    out = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    case_data = load_json_or_none(inp / "case.json")
    manifest = load_json_or_none(work / "manifest.json")
    meta = load_json_or_none(work / "meta.json") or {}
    segments_data = load_json_or_none(work / "segments.json") or {"segments": []}

    case_id = None
    contract = None
    if isinstance(case_data, dict):
        case_id = case_data.get("case_id")
        contract = case_data.get("output_contract")

    prev = load_json_or_none(out / "result.json")
    started_at = (prev or {}).get("runtime", {}).get("started_at") or now_iso()
    prev_log = (prev or {}).get("change_log", [])

    result = canonical_skeleton(case_id or "unknown")
    result["runtime"]["started_at"] = started_at
    result, _ = apply_output_contract(result, contract)

    findings, problems = _load_findings(work)
    result["review_report"]["findings"] = findings
    result["evidence"]["evidence_map"] = {
        f["id"]: {"locator": f["locator"], "quote": f["quote"],
                  "source": f.get("source", ""), "basis": f.get("basis", {})}
        for f in findings}

    result["summary"] = _compute_summary(findings, meta)
    result["errors"] = list((manifest or {}).get("errors", [])) + \
        list(meta.get("errors") or []) + problems
    result["degradations"] = list((manifest or {}).get("degradations", [])) + \
        list(meta.get("degradations") or [])

    if manifest:
        result["sources"] = [
            {"path": m.get("relpath"), "kind": m.get("kind"), "role": "material",
             "status": m.get("status")} for m in manifest.get("materials", [])]
    elif meta.get("sources"):
        result["sources"] = meta["sources"]

    result["numeric_checks"] = _numeric_checks(segments_data.get("segments", []), meta)
    _apply_meta(result, meta)
    result["status"] = _resolve_status(
        meta, manifest, findings, case_data, result["review_report"]["unchecked_facets"])

    change_log = list(prev_log)
    change_log.append({"at": now_iso(), "phase": "finalize", "action": "merge",
                       "detail": "%d findings; status=%s; numeric_check_candidates=%d"
                       % (len(findings), result["status"], len(result["numeric_checks"]))})
    result["change_log"] = change_log

    result["runtime"]["finished_at"] = now_iso()
    try:
        start = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ")
        result["runtime"]["elapsed_seconds"] = round(
            (datetime.now(timezone.utc) - start.replace(tzinfo=timezone.utc)).total_seconds(), 1)
    except Exception:
        result["runtime"]["elapsed_seconds"] = None

    write_json(out / "result.json", result)
    render_report(result, out)
    write_json(work / "state.json", {
        "case_id": result["case_id"], "phase": "finalized", "started_at": started_at,
        "last_updated": now_iso(), "findings_count": len(findings),
        "checked_facets": result["review_report"]["checked_facets"],
        "unchecked_facets": result["review_report"]["unchecked_facets"]})
    print("finalize: %d findings; status=%s; result_status=%s"
          % (len(findings), result["status"], result["result_status"]))
    return 0


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def render_report(result, out):
    lines = ["# \u7814\u7a76\u65b9\u6cd5\u4e0e\u7edf\u8ba1\u5ba1\u67e5\u62a5\u544a", "",
             "- case_id: `%s`" % result["case_id"],
             "- status: `%s`" % result["status"],
             "- result_status: %s" % result["result_status"],
             "- \u603b\u4f53\u7ed3\u8bba: %s" % result["summary"]["verdict"],
             "- \u603b\u4f53\u8bc4\u4f30: %s" % result["summary"].get("overall_assessment", ""),
             ""]
    counts = result["summary"]["counts"]
    lines += ["## \u6458\u8981", "",
              "| \u4e25\u91cd\u5ea6 | \u6570\u91cf |", "|---|---|"]
    for s in SEVERITIES:
        lines.append("| %s | %d |" % (s, counts.get(s, 0)))
    lines.append("| total | %d |" % counts.get("total", 0))
    lines += ["", "## \u9010\u6761\u5ba1\u67e5\u610f\u89c1", ""]
    findings = result["review_report"]["findings"]
    if not findings:
        lines.append("\uff08\u6682\u65e0\u5df2\u786e\u8ba4\u7684\u5ba1\u67e5\u610f\u89c1\uff09")
    for f in findings:
        lines.append("### %s \u2014 %s \u00b7 %s \u00b7 %s"
                     % (f["id"], f.get("facet", ""), f.get("severity", ""),
                        f.get("finding_type", "")))
        lines.append("- \u68c0\u67e5\u9762: `%s`" % f.get("check_class", ""))
        lines.append("- \u4f4d\u7f6e: `%s`" % f.get("locator", ""))
        lines.append("- \u539f\u6587\u6458\u5f55:\n\n  > %s" % f.get("quote", ""))
        lines.append("- \u5ba1\u67e5\u4e3b\u5f20: %s" % f.get("claim", ""))
        basis = f.get("basis") or {}
        lines.append("- \u5224\u65ad\u4f9d\u636e: `%s` %s" % (basis.get("rule_id", ""),
                                                               basis.get("rule", "")))
        lines.append("- \u4fee\u6539\u5efa\u8bae: %s" % f.get("suggestion", ""))
        lines.append("- \u7f6e\u4fe1\u5ea6: %s \u00b7 \u590d\u6838\u72b6\u6001: %s"
                     % (f.get("confidence", ""), f.get("review_status", "")))
        lines.append("")
    lines += ["## \u6570\u503c\u4e00\u81f4\u6027\u5019\u9009\uff08\u5f85\u4eba\u5de5\u590d\u6838\uff09", ""]
    checks = result.get("numeric_checks", [])
    if not checks:
        lines.append("\uff08\u65e0\u5019\u9009\uff09")
    for c in checks:
        lines.append("- `%s` [%s] %s \u2014 %s" % (c["id"], c.get("status", ""),
                                                   c.get("locator", ""), c.get("candidate", "")))
    lines += ["", "## \u5f85\u590d\u6838\u4e0e\u4e0d\u786e\u5b9a\u6027", ""]
    lines += ["- pending_human: %s" % (result.get("pending_human") or "\u65e0"),
              "- uncertainty: %s" % (result.get("uncertainty") or "\u65e0"),
              "- limitations: %s" % (result.get("limitations") or "\u65e0"), ""]
    lines += ["## \u53d8\u66f4\u8bb0\u5f55", ""]
    for entry in result.get("change_log", []):
        lines.append("- %s [%s] %s: %s" % (entry.get("at", ""), entry.get("phase", ""),
                                           entry.get("action", ""), entry.get("detail", "")))
    lines.append("")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _add_io(sp):
    sp.add_argument("--input", default=DEFAULT_INPUT, help="input dir (default /input)")
    sp.add_argument("--output", default=DEFAULT_OUTPUT, help="output dir (default /app/output)")
    sp.add_argument("--work", default=DEFAULT_WORK, help="work dir (default /app/work)")


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Research-methods & statistics audit helper (standard library only).")
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_text in (
            ("discover", discover, "scan input, extract text, build locator index + manifest"),
            ("scaffold", scaffold, "write honest partial result.json + report.md early"),
            ("finalize", finalize, "merge findings, numeric pre-check, validate + render"),
            ("run", None, "discover -> scaffold -> finalize")):
        sp = sub.add_parser(name, help=help_text)
        _add_io(sp)
        sp.set_defaults(fn=fn)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "run":
        discover(args)
        scaffold(args)
        finalize(args)
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
