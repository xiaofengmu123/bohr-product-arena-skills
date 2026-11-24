"""core_impl.py — evidence-bound research-methods & statistics audit pipeline.

Pure standard-library implementation. Commands (via `python scripts/main.py`):

    discover   scan /input, extract text (PDF via pdftotext, docx via zipfile,
               txt/md/csv/json via utf-8-sig with fallback), build
               segments.json / manifest.json / terms.json / texts/*.txt.
    scaffold   write a legal partial result.json skeleton early (<=3 minutes).
    finalize   merge findings, verify verbatim quotes against segments, run
               numeric consistency candidate checks, validate schema, render
               report.md.
    pipeline   discover -> scaffold -> finalize (one-shot local run).
    selftest   build a generated fixture in a temp dir, run the full pipeline,
               assert structural invariants (also exposed as scripts/selftest.py).

Design principle: evidence provenance is first-class. Every finding must carry a
locator + verbatim quote; finalize mechanically verifies the quote against the
extracted text (SHA-1 + char span), and downgrades any unverifiable quote to
needs_review instead of pretending it is confirmed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET

VERSION = "1.0.0"

FACETS = [
    "research_design",
    "variable_definition",
    "statistical_methods",
    "sample_size_power",
    "results_reporting",
]
CHECK_CLASSES = [
    "fact_check",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argumentation_chain",
    "academic_norms",
]
FINDING_TYPES = [
    "factual_error",
    "insufficient_evidence",
    "normative_issue",
    "advisory",
    "pending_review",
]
SEVERITIES = ["critical", "major", "minor", "advisory", "pending"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
OVERALLS = ["通过", "问题", "待复核", "未检查"]

# Top-level keys managed by finalize; everything else is passed through, which
# preserves output_contract and any hoisted contract keys verbatim.
RESERVED_KEYS = {
    "case_id",
    "status",
    "summary",
    "result_status",
    "review_report",
    "evidence",
    "sources",
    "uncertainty",
    "pending_human",
    "limitations",
    "errors",
    "degradations",
    "change_log",
    "runtime",
}

MAX_MATERIAL_BYTES = 2 * 1024 * 1024  # skip larger files, record degradation

STAT_TERMS = [
    "t-test", "t test", "t检验", "检验", "anova", "chi-square", "chi square",
    "χ2", "χ²", "chi2", "logistic", "regression", "回归", "cox", "kaplan",
    "log-rank", "logrank", "mann-whitney", "wilcoxon", "kruskal", "fisher",
    "p-value", "p value", "p =", "p=", "p <", "p<", "p ≤", "p≤", "confidence",
    "95% ci", "置信区间", "odds ratio", "hazard ratio", "risk ratio",
    "effect size", "效应量", "power", "把握度", "检验效能", "sample size",
    "样本量", "n =", "n=", "standard deviation", "标准差", "mean ±", "均值",
    "median", "iqr", "中位数", "missing", "缺失", "multiple comparison",
    "多重比较", "bonferroni", "holm", "fdr", "sensitivity analysis",
    "敏感性分析", "randomization", "随机", "blinding", "盲法", "control group",
    "对照组", "intention-to-treat", "itt", "per-protocol", "consort", "strobe",
    "prisma",
]


# --------------------------------------------------------------------------- #
# small deterministic utilities
# --------------------------------------------------------------------------- #

def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _sha1(text: str) -> str:
    return hashlib.sha1(_norm_ws(text).encode("utf-8", "replace")).hexdigest()


def _norm_ws(text: str) -> str:
    """Collapse all whitespace runs to a single space; used for verbatim matching."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def _read_text_bytes(data: bytes) -> str:
    """Decode text bytes with a small, deterministic fallback chain."""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def _read_text_file(path: str) -> str:
    with open(path, "rb") as fh:
        return _read_text_bytes(fh.read())


def _load_json(path: str, default=None):
    try:
        with open(path, "rb") as fh:
            return json.loads(_read_text_bytes(fh.read()))
    except (OSError, ValueError, TypeError):
        return default


def _dump_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _safe_rel(root: str, path: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = os.path.basename(path)
    return rel.replace(os.sep, "/")


def _fmt_span(text: str, offset: int, length: int, width: int = 60) -> str:
    lo = max(0, offset - width)
    hi = min(len(text), offset + length + width)
    return text[lo:hi]


# --------------------------------------------------------------------------- #
# text extraction
# --------------------------------------------------------------------------- #

def _extract_pdf(pdf_path: str, txt_path: str) -> tuple[str, list[str]]:
    """Extract PDF text via pdftotext into txt_path; return (error, [page texts])."""
    exe = shutil.which("pdftotext")
    if not exe:
        return "pdftotext_not_found", []
    try:
        proc = subprocess.run(
            [exe, "-layout", "-enc", "UTF-8", pdf_path, txt_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"pdftotext_error:{exc.__class__.__name__}", []
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()[:200]
        return f"pdftotext_rc={proc.returncode}:{err}", []
    if not os.path.exists(txt_path):
        return "pdftotext_no_output", []
    raw = _read_text_file(txt_path)
    pages = re.split(r"\f", raw)
    pages = [p.strip() for p in pages]
    # drop a single trailing empty page marker
    while pages and not pages[-1]:
        pages.pop()
    if not pages or not any(pages):
        return "empty_or_scanned", []
    return "", pages


def _extract_docx(path: str) -> str:
    ns_w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(ns_w + "p"):
        parts = [t.text or "" for t in p.iter(ns_w + "t")]
        paras.append("".join(parts))
    return "\n\n".join(paras)


def _extract_csv(path: str) -> str:
    rows = []
    with open(path, "rb") as fh:
        data = _read_text_bytes(fh.read())
    reader = csv.reader(io.StringIO(data))
    for row in reader:
        rows.append("\t".join(row))
    return "\n".join(rows)


def _material_text(path: str, ext: str) -> tuple[str, list[tuple[int, str]]]:
    """Return (error, [(page_no_or_None, text)]) for a material file.

    For PDFs the page number is meaningful; for flat text files page is None and
    the whole file becomes one chunk that is later split into paragraphs.
    """
    if ext == ".pdf":
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".pdftotext.txt")
        os.close(fd)
        try:
            err, pages = _extract_pdf(path, tmp)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if err:
            return err, []
        return "", list(enumerate(pages, start=1))
    try:
        if ext == ".docx":
            text = _extract_docx(path)
        elif ext == ".csv":
            text = _extract_csv(path)
        elif ext in (".txt", ".md", ".json", ".jsonl", ".xml", ".html", ".htm"):
            text = _read_text_file(path)
        else:
            # best-effort text decode for unknown extensions
            text = _read_text_file(path)
    except (OSError, zipfile.BadZipFile, ET.ParseError, ValueError) as exc:
        return f"extract_error:{exc.__class__.__name__}", []
    if not text.strip():
        return "empty_text", []
    return "", [(None, text)]


def _split_paragraphs(text: str) -> list[str]:
    """Split a chunk into paragraphs on blank lines; keep deterministic."""
    parts = re.split(r"\n\s*\n", text)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out


# --------------------------------------------------------------------------- #
# discover
# --------------------------------------------------------------------------- #

def _find_materials(input_dir: str) -> list[str]:
    skip_names = {"case.json", "task.md", "task.json", "output_contract.json"}
    files = []
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            if name.lower() in skip_names:
                continue
            files.append(os.path.join(root, name))
    return files


def run_discover(args) -> dict:
    _ensure_dir(args.work)
    _ensure_dir(args.output)
    texts_dir = _ensure_dir(os.path.join(args.work, "texts"))

    case = _load_json(os.path.join(args.input, "case.json"), {}) or {}
    task_md = ""
    task_path = os.path.join(args.input, "task.md")
    if os.path.exists(task_path):
        task_md = _read_text_file(task_path)

    case_id = ""
    if isinstance(case, dict):
        case_id = str(case.get("case_id") or "")

    manifest = {
        "input_dir": args.input,
        "work_dir": args.work,
        "output_dir": args.output,
        "case_id": case_id,
        "has_case_json": isinstance(case, dict) and bool(case),
        "has_task_md": bool(task_md.strip()),
        "materials": [],
        "errors": [],
        "degradations": [],
        "generated_at": _iso_now(),
    }

    if not os.path.exists(os.path.join(args.input, "case.json")):
        manifest["errors"].append({"kind": "missing_case_json", "detail": "case.json 不存在"})
    if not os.path.exists(task_path):
        manifest["degradations"].append({"kind": "missing_task_md", "detail": "task.md 不存在"})

    material_files = _find_materials(args.input)
    if not material_files:
        manifest["degradations"].append({"kind": "no_materials", "detail": "输入目录无材料文件"})

    segments = []
    texts_map = {}
    mat_idx = 0

    for path in material_files:
        mat_idx += 1
        rel = _safe_rel(args.input, path)
        ext = os.path.splitext(path)[1].lower()
        size = os.path.getsize(path) if os.path.exists(path) else 0
        entry = {
            "id": f"MAT-{mat_idx:03d}",
            "rel": rel,
            "path": path,
            "ext": ext,
            "size": size,
            "status": "read",
            "pages": None,
            "paragraphs": 0,
            "needs_ocr": False,
            "error": None,
        }
        if size > MAX_MATERIAL_BYTES:
            entry["status"] = "skipped"
            entry["error"] = "file_too_large"
            manifest["degradations"].append(
                {"kind": "file_too_large", "path": rel, "detail": f"超过 {MAX_MATERIAL_BYTES} 字节上限"}
            )
            manifest["materials"].append(entry)
            continue

        err, chunks = _material_text(path, ext)
        if err:
            entry["status"] = "error"
            entry["error"] = err
            if err in ("empty_or_scanned", "empty_text", "pdftotext_no_output"):
                entry["needs_ocr"] = True
                manifest["degradations"].append(
                    {"kind": "needs_ocr" if err in ("empty_or_scanned", "pdftotext_no_output") else "empty_text",
                     "path": rel, "detail": err}
                )
            elif "pdftotext" in err:
                manifest["degradations"].append({"kind": "pdftotext_missing", "path": rel, "detail": err})
            else:
                manifest["errors"].append({"kind": "extract_error", "path": rel, "detail": err})
            manifest["materials"].append(entry)
            continue

        full_text = ""
        page_count = 0
        for page_no, chunk in chunks:
            page_count += 1
            paras = _split_paragraphs(chunk)
            for pno, para in enumerate(paras, start=1):
                if page_no is not None:
                    locator = f"p.{page_no} ¶{pno}"
                    seg_src = f"{rel}#p.{page_no}"
                else:
                    locator = f"¶{pno}"
                    seg_src = rel
                seg_id = f"seg-{mat_idx:03d}-{page_count:03d}-{pno:03d}"
                segments.append({
                    "id": seg_id,
                    "material": rel,
                    "source": seg_src,
                    "page": page_no,
                    "para": pno,
                    "locator": locator,
                    "text": para,
                })
                full_text += para + "\n\n"
            entry["paragraphs"] += len(paras)

        txt_name = f"mat-{mat_idx:03d}{os.path.splitext(rel)[0].replace(os.sep, '__').replace('/', '__')}.txt"
        txt_path = os.path.join(texts_dir, txt_name)
        with open(txt_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(full_text)
        texts_map[rel] = txt_name
        entry["pages"] = page_count if ext == ".pdf" else None
        manifest["materials"].append(entry)

    # terms.json: keyword hits per segment for method/result section location
    terms = {}
    for seg in segments:
        low = seg["text"].lower()
        for term in STAT_TERMS:
            if term.lower() in low:
                terms.setdefault(term, []).append(seg["id"])

    _dump_json(os.path.join(args.work, "segments.json"), segments)
    _dump_json(os.path.join(args.work, "manifest.json"), manifest)
    _dump_json(os.path.join(args.work, "terms.json"), terms)
    _dump_json(os.path.join(args.work, "texts_map.json"), texts_map)

    print(f"[discover] case_id={case_id!r} materials={len(material_files)} segments={len(segments)}")
    for d in manifest["degradations"]:
        print(f"[degradation] {d['kind']}: {d.get('path') or d['detail']}")
    for e in manifest["errors"]:
        print(f"[error] {e['kind']}: {e.get('path') or e['detail']}")
    return manifest


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #

def _backfill_contract(case: dict) -> dict:
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        contract = {}

    def normalize(value):
        if value is None:
            return None
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return value
        return str(value)

    return normalize(contract)


def run_scaffold(args) -> dict:
    _ensure_dir(args.work)
    _ensure_dir(args.output)
    _ensure_dir(os.path.join(args.work, "findings"))

    case = _load_json(os.path.join(args.input, "case.json"), {}) or {}
    if not isinstance(case, dict):
        case = {}
    case_id = str(case.get("case_id") or "")

    contract = _backfill_contract(case)
    result = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract,
        "summary": {
            "verdict": "unchecked",
            "counts": {"total": 0, "critical": 0, "major": 0, "minor": 0,
                       "advisory": 0, "pending": 0},
            "by_facet": {f: 0 for f in FACETS},
            "by_finding_type": {t: 0 for t in FINDING_TYPES},
        },
        "result_status": {
            "overall": "未检查",
            "facets": {f: "未检查" for f in FACETS},
        },
        "review_report": {"findings": []},
        "evidence": {"evidence_map": {}, "numeric_checks": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [{"stage": "scaffold", "action": "initialized",
                        "note": "partial 结果骨架，后续增量改进", "ts": _iso_now()}],
        "runtime": {"started_at": _iso_now(), "finished_at": None,
                    "elapsed_seconds": None, "script_version": VERSION},
    }

    # hoist non-reserved contract keys to top level (never overwrite reserved)
    for key, value in contract.items():
        if key not in RESERVED_KEYS and key not in result:
            result[key] = value

    meta = {
        "status": "partial",
        "overall_verdict": "unchecked",
        "overall_assessment": "",
        "checked_facets": [],
        "unchecked": list(FACETS),
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
    }
    state = {"case_id": case_id, "stage": "scaffolded", "findings_written": 0,
             "last_finalize_at": None}
    _dump_json(os.path.join(args.work, "meta.json"), meta)
    _dump_json(os.path.join(args.work, "state.json"), state)
    _dump_json(os.path.join(args.output, "result.json"), result)
    print(f"[scaffold] result.json skeleton written (case_id={case_id!r}, status=partial)")
    return result


# --------------------------------------------------------------------------- #
# numeric consistency candidate checks
# --------------------------------------------------------------------------- #

_P_RE = re.compile(
    r"[pP]\s*(?:-?\s*value)?\s*([=<>≤≥])\s*(0?\.\d+)"
)
_SIG_POS = re.compile(
    r"significan\w*|显著(?:性)?(?:差异|相关|关联|升高|降低|更高|更低)?|有统计学意义|"
    r"统计学显著|存在差异|有差异",
    re.IGNORECASE,
)
_SIG_NEG = re.compile(
    r"non[\s-]?significant|not\s+significant|no\s+significant|\bns\b|无显著|不显著|"
    r"未达(?:到)?(?:统计学)?显著|没有统计学意义|无统计学意义|未见(?:到)?显著",
    re.IGNORECASE,
)
_CI_RE = re.compile(
    r"(?:95\s*%\s*CI|CI)[^0-9\-()]{0,30}(-?\d+(?:\.\d+)?)\s*(?:,|to|–|—|~|and)\s*"
    r"(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_N_RE = re.compile(r"\b(?:[Nn])\s*=\s*(\d+)\b")


def _p_is_significant(op: str, value: str, alpha: float = 0.05) -> bool:
    v = float(value)
    if op in ("<", "≤"):
        return v < alpha
    if op in (">", "≥"):
        return False
    return v < alpha


def _run_numeric_checks(segments: list[dict]) -> list[dict]:
    checks = []
    # 1) p vs significance wording, per segment
    for seg in segments:
        for m in _P_RE.finditer(seg["text"]):
            op, value = m.group(1), m.group(2)
            significant = _p_is_significant(op, value)
            has_neg = bool(_SIG_NEG.search(seg["text"]))
            has_pos = bool(_SIG_POS.search(seg["text"]))
            if has_neg:
                wording = "non-significant"
            elif has_pos:
                wording = "significant"
            else:
                wording = None
            if significant and wording == "non-significant":
                checks.append({
                    "id": f"NC-P-{len(checks) + 1:03d}",
                    "kind": "p_vs_wording",
                    "locator": seg["locator"],
                    "segments": [seg["id"]],
                    "detail": {"p_match": m.group(0), "p_significant": True,
                               "wording": wording},
                    "status": "candidate",
                })
            if not significant and wording == "significant":
                checks.append({
                    "id": f"NC-P-{len(checks) + 1:03d}",
                    "kind": "p_vs_wording",
                    "locator": seg["locator"],
                    "segments": [seg["id"]],
                    "detail": {"p_match": m.group(0), "p_significant": False,
                               "wording": wording},
                    "status": "candidate",
                })

    # 2) p vs CI conclusion, per segment (only where a ratio/difference marker exists)
    ratio_markers = ("odds ratio", "or", "hr", "rr", "hazard ratio", "risk ratio",
                     "相对危险", "比值比", "风险比", "优势比")
    diff_markers = ("mean difference", "md", "smd", "β", "beta", "coefficient",
                    "difference", "均数差", "差值", "系数")
    for seg in segments:
        low = seg["text"].lower()
        ci_m = _CI_RE.search(seg["text"])
        p_m = _P_RE.search(seg["text"])
        if not ci_m or not p_m:
            continue
        try:
            lo, hi = float(ci_m.group(1)), float(ci_m.group(2))
        except ValueError:
            continue
        null = None
        if any(mk in low for mk in ratio_markers):
            null = 1.0
        elif any(mk in low for mk in diff_markers):
            null = 0.0
        if null is None:
            continue
        ci_excludes_null = (lo > null and hi > null) or (lo < null and hi < null)
        op, value = p_m.group(1), p_m.group(2)
        p_sig = _p_is_significant(op, value)
        if p_sig != ci_excludes_null:
            checks.append({
                "id": f"NC-CI-{len(checks) + 1:03d}",
                "kind": "p_vs_ci",
                "locator": seg["locator"],
                "segments": [seg["id"]],
                "detail": {"p_match": p_m.group(0), "ci_match": ci_m.group(0),
                           "ci_bounds": [lo, hi], "null_value": null,
                           "ci_excludes_null": ci_excludes_null,
                           "p_significant": p_sig},
                "status": "candidate",
            })

    # 3) N/n value inconsistencies across the whole material
    n_locations = {}
    for seg in segments:
        for m in _N_RE.finditer(seg["text"]):
            val = int(m.group(1))
            n_locations.setdefault(val, []).append(
                {"locator": seg["locator"], "segment": seg["id"], "match": m.group(0)}
            )
    distinct = sorted(n_locations)
    if len(distinct) > 1:
        checks.append({
            "id": f"NC-N-{len(checks) + 1:03d}",
            "kind": "n_inconsistency",
            "locator": "; ".join(sorted({loc["locator"] for locs in n_locations.values() for loc in locs})[:8]),
            "segments": sorted({loc["segment"] for locs in n_locations.values() for loc in locs}),
            "detail": {"distinct_n_values": distinct,
                       "locations": [n_locations[v] for v in distinct]},
            "status": "candidate",
        })
    return checks


# --------------------------------------------------------------------------- #
# finding load + evidence verification
# --------------------------------------------------------------------------- #

def _load_findings(work_dir: str) -> list[dict]:
    fdir = os.path.join(work_dir, "findings")
    findings = []
    if os.path.isdir(fdir):
        for name in sorted(os.listdir(fdir)):
            if not name.endswith(".json"):
                continue
            obj = _load_json(os.path.join(fdir, name), None)
            if isinstance(obj, dict):
                obj.setdefault("_file", name)
                findings.append(obj)
    findings.sort(key=lambda f: str(f.get("id", f.get("_file", ""))))
    return findings


def _verify_quote(finding: dict, segments: list[dict]) -> dict:
    """Verify a finding's quote verbatim against extracted segments."""
    quote = _norm_ws(str(finding.get("quote") or ""))
    evidence = {
        "source": None,
        "segment_id": None,
        "quote_sha1": _sha1(quote),
        "char_offset": None,
        "char_length": len(quote) if quote else 0,
        "context": None,
        "evidence_kind": "primary",
        "verification": "none",
    }
    if not quote:
        return evidence

    locator = str(finding.get("locator") or "")
    candidates = [s for s in segments if s.get("locator") == locator]
    if not candidates:
        candidates = segments

    for seg in candidates:
        norm = _norm_ws(seg["text"])
        idx = norm.find(quote)
        if idx >= 0:
            # char offset in the original (un-normalized) text is approximate;
            # report offset within normalized text and provide context.
            evidence["source"] = seg.get("material")
            evidence["segment_id"] = seg.get("id")
            evidence["char_offset"] = idx
            evidence["char_length"] = len(quote)
            evidence["context"] = _fmt_span(norm, idx, len(quote))
            evidence["verification"] = "exact"
            return evidence
    return evidence


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #

def _empty_summary() -> dict:
    return {
        "verdict": "unchecked",
        "counts": {"total": 0, "critical": 0, "major": 0, "minor": 0,
                   "advisory": 0, "pending": 0},
        "by_facet": {f: 0 for f in FACETS},
        "by_finding_type": {t: 0 for t in FINDING_TYPES},
    }


def _compute_verdict(findings: list[dict], meta: dict) -> tuple[str, dict]:
    summary = _empty_summary()
    for f in findings:
        sev = f.get("severity") if f.get("severity") in SEVERITIES else "pending"
        ftype = f.get("finding_type") if f.get("finding_type") in FINDING_TYPES else "pending_review"
        facet = f.get("facet") if f.get("facet") in FACETS else None
        summary["counts"]["total"] += 1
        if sev in summary["counts"]:
            summary["counts"][sev] += 1
        if facet:
            summary["by_facet"][facet] = summary["by_facet"].get(facet, 0) + 1
        summary["by_finding_type"][ftype] = summary["by_finding_type"].get(ftype, 0) + 1

    verdict = meta.get("overall_verdict") or "unchecked"
    if verdict == "unchecked":
        if summary["counts"]["critical"]:
            verdict = "critical_issues"
        elif summary["counts"]["major"]:
            verdict = "issues_found"
        elif summary["counts"]["total"]:
            verdict = "minor_issues"
        else:
            checked = set(meta.get("checked_facets") or [])
            verdict = "passed" if len(checked) >= len(FACETS) else "unchecked"
    summary["verdict"] = verdict
    return verdict, summary


def _compute_result_status(verdict: str, findings: list[dict], meta: dict,
                           has_uncertainty: bool) -> tuple[str, dict]:
    facets = {f: "未检查" for f in FACETS}
    for f in meta.get("checked_facets") or []:
        if f in facets:
            facets[f] = "已检查"
    for f in meta.get("unchecked") or []:
        if f in facets:
            facets[f] = "未检查"
    overall = "未检查"
    if verdict == "passed":
        overall = "通过"
    elif findings:
        overall = "问题"
    elif has_uncertainty:
        overall = "待复核"
    return overall, facets


def run_finalize(args) -> dict:
    _ensure_dir(args.work)
    _ensure_dir(args.output)

    current = _load_json(os.path.join(args.output, "result.json"), {}) or {}
    if not isinstance(current, dict):
        current = {}
    meta = _load_json(os.path.join(args.work, "meta.json"), {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    segments = _load_json(os.path.join(args.work, "segments.json"), []) or []
    manifest = _load_json(os.path.join(args.work, "manifest.json"), {}) or {}

    # carry over every non-reserved top-level key (preserves output_contract and
    # any hoisted contract keys / agent-added fields)
    carried = {k: v for k, v in current.items() if k not in RESERVED_KEYS}

    findings = _load_findings(args.work)
    numeric_checks = _run_numeric_checks(segments)

    evidence_map = {}
    uncertainties = list(meta.get("uncertainty") or [])
    degradations = list(meta.get("degradations") or [])
    errors = list(meta.get("errors") or [])
    for m in manifest.get("degradations") or []:
        if m not in degradations:
            degradations.append(m)
    for e in manifest.get("errors") or []:
        if e not in errors:
            errors.append(e)

    finalized_findings = []
    for raw in findings:
        f = dict(raw)
        f.pop("_file", None)
        required = ["id", "facet", "locator", "quote", "severity", "suggestion"]
        missing = [k for k in required if not f.get(k)]
        if missing:
            errors.append({"kind": "finding_missing_required",
                           "finding_id": f.get("id") or "?",
                           "detail": f"缺字段: {', '.join(missing)}"})
            f["severity"] = "pending"
            f["review_status"] = "needs_review"
        if f.get("facet") not in FACETS:
            f["facet"] = None
        if f.get("check_class") not in CHECK_CLASSES:
            f["check_class"] = None
        if f.get("finding_type") not in FINDING_TYPES:
            f["finding_type"] = "pending_review"
        if f.get("severity") not in SEVERITIES:
            f["severity"] = "pending"
        if f.get("review_status") not in ("confirmed", "needs_review"):
            f["review_status"] = "confirmed"
        if f.get("confidence") not in ("high", "medium", "low"):
            f["confidence"] = "medium"

        evidence = _verify_quote(f, segments)
        if evidence["verification"] == "none":
            f["review_status"] = "needs_review"
            f["confidence"] = "low"
            uncertainties.append({
                "id": f.get("id"),
                "item": "quote 未在抽取文本中逐字命中",
                "reason": "quote 可能改写、拼写/空格差异，或 locator 指向的段落与文本不符",
                "resolution": "回到原文核对 locator 并改为逐字摘录",
            })
            degradations.append({"kind": "quote_not_verified", "finding_id": f.get("id"),
                                 "locator": f.get("locator")})
        evidence_map[f["id"]] = [evidence]
        finalized_findings.append(f)

    # re-attach evidence after dedup/cleanup
    for f in finalized_findings:
        f["evidence"] = (evidence_map.get(f["id"]) or [None])[0]

    verdict, summary = _compute_verdict(finalized_findings, meta)
    has_uncertainty = bool(uncertainties or meta.get("uncertainty"))
    overall, facets = _compute_result_status(verdict, finalized_findings, meta, has_uncertainty)

    status = meta.get("status") or "partial"
    if status not in STATUSES:
        status = "partial"
    # honest status overrides based on material availability
    if not segments:
        if manifest.get("errors") and not manifest.get("materials"):
            status = "input_error"
        elif not manifest.get("materials") or all(
            m.get("status") in ("error", "skipped") for m in manifest.get("materials", [])
        ):
            status = "insufficient_evidence"

    old_runtime = current.get("runtime") or {}
    started = old_runtime.get("started_at") or _iso_now()
    finished = _iso_now()
    try:
        start_dt = _dt.datetime.fromisoformat(started)
        elapsed = round((_dt.datetime.now(_dt.timezone.utc) - start_dt).total_seconds(), 1)
    except (ValueError, OSError, TypeError):
        elapsed = None

    result = dict(carried)
    result.update({
        "case_id": current.get("case_id") if "case_id" in current else
                   str((_load_json(os.path.join(args.input, "case.json"), {}) or {}).get("case_id") or ""),
        "status": status,
        "summary": summary,
        "result_status": {"overall": overall, "facets": facets},
        "review_report": {"findings": finalized_findings},
        "evidence": {"evidence_map": evidence_map, "numeric_checks": numeric_checks},
        "sources": meta.get("sources") or current.get("sources") or [],
        "uncertainty": uncertainties,
        "pending_human": meta.get("pending_human") or current.get("pending_human") or [],
        "limitations": meta.get("limitations") or current.get("limitations") or [],
        "errors": errors,
        "degradations": degradations,
        "change_log": (current.get("change_log") or []) + [{
            "stage": "finalize",
            "action": "merged_findings",
            "note": f"findings={len(finalized_findings)} numeric_checks={len(numeric_checks)}",
            "ts": finished,
        }],
        "runtime": {"started_at": started, "finished_at": finished,
                    "elapsed_seconds": elapsed, "script_version": VERSION},
    })

    _dump_json(os.path.join(args.output, "result.json"), result)
    _render_report(result, meta, segments, args.output)

    state = _load_json(os.path.join(args.work, "state.json"), {}) or {}
    state["findings_written"] = len(finalized_findings)
    state["last_finalize_at"] = finished
    state["stage"] = "finalized"
    _dump_json(os.path.join(args.work, "state.json"), state)

    print(f"[finalize] findings={len(finalized_findings)} checks={len(numeric_checks)} "
          f"unverified_quotes={sum(1 for f in finalized_findings if f['evidence']['verification']=='none')} "
          f"status={status} verdict={verdict}")
    return result


def _render_report(result: dict, meta: dict, segments: list[dict], out_dir: str) -> None:
    lines = []
    lines.append("# 研究方法与统计审查报告")
    lines.append("")
    lines.append(f"- case_id: `{result.get('case_id') or '（未提供）'}`")
    lines.append(f"- status: `{result.get('status')}`")
    lines.append(f"- 总评: `{result.get('summary', {}).get('verdict')}` / "
                 f"`{result.get('result_status', {}).get('overall')}`")
    counts = result.get("summary", {}).get("counts", {})
    lines.append(f"- 意见数: {counts.get('total', 0)}（critical {counts.get('critical', 0)} / "
                 f"major {counts.get('major', 0)} / minor {counts.get('minor', 0)} / "
                 f"advisory {counts.get('advisory', 0)} / pending {counts.get('pending', 0)}）")
    lines.append("")
    lines.append("## 逐条审查意见")
    findings = result.get("review_report", {}).get("findings", [])
    if not findings:
        lines.append("（无意见；若材料不足见 limitations。）")
    else:
        for f in findings:
            lines.append("")
            lines.append(f"### {f.get('id')} — {f.get('title') or f.get('facet') or ''}")
            lines.append(f"- 检查面: `{f.get('facet')}`；类别: `{f.get('finding_type')}`；"
                         f"严重度: `{f.get('severity')}`；状态: `{f.get('review_status')}`")
            lines.append(f"- 位置: `{f.get('locator')}`")
            ev = f.get("evidence") or {}
            lines.append(f"- 逐字摘录: > {f.get('quote') or '（空）'}")
            lines.append(f"- 证据校验: `{ev.get('verification')}`，"
                         f"sha1 `{(ev.get('quote_sha1') or '')[:12]}`，"
                         f"来源 `{ev.get('source') or '?'}`")
            if f.get("rationale"):
                lines.append(f"- 依据: {f['rationale']}")
            if f.get("suggestion"):
                lines.append(f"- 建议: {f['suggestion']}")
    lines.append("")
    lines.append("## 数值一致性候选检查（需人工确认）")
    checks = result.get("evidence", {}).get("numeric_checks", [])
    if not checks:
        lines.append("（无候选。）")
    for c in checks:
        lines.append(f"- `{c.get('id')}` [{c.get('kind')}] `{c.get('locator')}` — "
                     f"`{json.dumps(c.get('detail'), ensure_ascii=False)}`")
    for section, items in [("不确定性", result.get("uncertainty")),
                           ("待人工复核", result.get("pending_human")),
                           ("局限", result.get("limitations")),
                           ("错误", result.get("errors")),
                           ("降级", result.get("degradations"))]:
        lines.append("")
        lines.append(f"## {section}")
        if items:
            for it in items:
                lines.append(f"- {json.dumps(it, ensure_ascii=False)}")
        else:
            lines.append("（无。）")
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# pipeline + selftest
# --------------------------------------------------------------------------- #

def run_pipeline(args) -> dict:
    run_discover(args)
    run_scaffold(args)
    return run_finalize(args)


def _fixture_case():
    return {
        "case_id": "selftest-001",
        "task_note": "generated self-test fixture, not a formal case",
        "output_contract": {
            "review_format": "itemized",
            "include_evidence": True,
            "optional_note": None,
            "facet_labels": {
                "research_design": "研究设计",
                "variable_definition": "变量定义",
            },
        },
    }


_FIXTURE_TASK = "# 自检任务\n对合成材料做研究方法与统计审查。\n"
_FIXTURE_TEXT = (
    "Objective. The trial compared treatment and control in a randomized design.\n\n"
    "Methods. Continuous outcomes were compared using an independent samples t-test, "
    "and categorical outcomes using chi-square tests. The study enrolled N = 120 "
    "participants.\n\n"
    "Results. A total of n = 118 participants were analyzed.\n\n"
    "The primary difference was significant (p = 0.06).\n\n"
    "The odds ratio was 1.35 (95% CI 1.05 to 1.75), but the result was not "
    "significant (p = 0.12).\n\n"
    "Discussion. We conclude the treatment is effective.\n"
)


def _build_fixture(tmp: str) -> str:
    inp = _ensure_dir(os.path.join(tmp, "input"))
    _ensure_dir(os.path.join(inp, "materials"))
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(_fixture_case(), fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write(_FIXTURE_TASK)
    with open(os.path.join(inp, "materials", "paper.txt"), "w", encoding="utf-8") as fh:
        fh.write(_FIXTURE_TEXT)
    return inp


def run_selftest(args) -> dict:
    import tempfile

    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    inp = _build_fixture(tmp)
    work = os.path.join(tmp, "work")
    output = os.path.join(tmp, "output")

    class A:
        pass

    a1 = A(); a1.input = inp; a1.work = work; a1.output = output
    run_discover(a1)
    run_scaffold(a1)

    findings_dir = os.path.join(work, "findings")
    good = {
        "id": "F-001",
        "facet": "statistical_methods",
        "check_class": "statistical_reporting",
        "finding_type": "factual_error",
        "severity": "major",
        "title": "p 值与显著性措辞矛盾",
        "locator": "¶4",
        "quote": "The primary difference was significant (p = 0.06).",
        "suggestion": "将措辞改为与 p=0.06 一致，或报告精确 p 值并避免宣称显著。",
        "rationale": "p=0.06 > 0.05，与 significant 措辞相反。",
        "confidence": "high",
        "review_status": "confirmed",
    }
    bad = {
        "id": "F-002",
        "facet": "results_reporting",
        "check_class": "statistical_reporting",
        "finding_type": "pending_review",
        "severity": "pending",
        "title": "改写后的非逐字摘录",
        "locator": "¶4",
        "quote": "The difference was NOT significant at all.",
        "suggestion": "改为逐字摘录。",
        "rationale": "故意改写以测试证据校验。",
        "confidence": "low",
        "review_status": "needs_review",
    }
    _dump_json(os.path.join(findings_dir, "F-001.json"), good)
    _dump_json(os.path.join(findings_dir, "F-002.json"), bad)
    meta = _load_json(os.path.join(work, "meta.json"), {}) or {}
    meta["checked_facets"] = FACETS
    meta["unchecked"] = []
    meta["status"] = "partial"
    meta["overall_verdict"] = "issues_found"
    meta["limitations"] = [{"kind": "synthetic_fixture", "detail": "自检合成材料，非正式案例"}]
    _dump_json(os.path.join(work, "meta.json"), meta)

    result = run_finalize(a1)

    # --- assertions ---
    failures = []
    def check(cond, msg):
        if not cond:
            failures.append(msg)

    with open(os.path.join(output, "result.json"), "r", encoding="utf-8") as fh:
        parsed = json.load(fh)
    check(parsed.get("case_id") == "selftest-001", "case_id 未回填")
    check(parsed.get("output_contract", {}).get("review_format") == "itemized",
          "output_contract 未原样回填")
    check(parsed.get("include_evidence") is True, "output_contract 键未提升到顶层")
    check(parsed.get("optional_note") is None, "output_contract null 键未保留")
    check(parsed.get("facet_labels", {}).get("research_design") == "研究设计",
          "output_contract 嵌套键未保留")
    findings = parsed.get("review_report", {}).get("findings", [])
    check(len(findings) == 2, f"findings 数量应为 2，实际 {len(findings)}")
    f1 = next((f for f in findings if f["id"] == "F-001"), None)
    f2 = next((f for f in findings if f["id"] == "F-002"), None)
    check(f1 and f1["evidence"]["verification"] == "exact", "F-001 应逐字命中")
    check(f2 and f2["evidence"]["verification"] == "none", "F-002 应判定未命中")
    check(f2 and f2["review_status"] == "needs_review", "F-002 未命中应降级 needs_review")
    check(f1 and len(f1["evidence"].get("quote_sha1", "")) == 40, "quote_sha1 应为 40 位")
    checks = parsed.get("evidence", {}).get("numeric_checks", [])
    kinds = {c["kind"] for c in checks}
    check("p_vs_wording" in kinds, "缺少 p_vs_wording 候选检查")
    check("n_inconsistency" in kinds, "缺少 n_inconsistency 候选检查")
    check(os.path.exists(os.path.join(output, "report.md")), "report.md 未生成")
    check(parsed.get("sources") is not None and parsed.get("change_log"), "sources/change_log 缺失")

    # determinism: strip time-bearing fields, compare two finalize runs
    run_finalize(a1)
    with open(os.path.join(output, "result.json"), "r", encoding="utf-8") as fh:
        parsed2 = json.load(fh)

    def strip_time(obj):
        if isinstance(obj, dict):
            return {k: strip_time(v) for k, v in obj.items()
                    if k not in ("runtime", "change_log")}
        if isinstance(obj, list):
            return [strip_time(v) for v in obj]
        return obj

    check(strip_time(parsed) == strip_time(parsed2), "重复运行结果不一致（确定性失败）")

    print(f"[selftest] tmp={tmp}")
    if failures:
        for f in failures:
            print(f"[selftest FAIL] {f}")
        return {"ok": False, "failures": failures, "tmp": tmp}
    print("[selftest] all assertions passed")
    return {"ok": True, "failures": [], "tmp": tmp}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="evidence-bound research-methods & statistics audit pipeline",
    )
    p.add_argument("command", nargs="?", default="pipeline",
                   choices=["discover", "scaffold", "finalize", "pipeline", "selftest"],
                   help="subcommand to run (default: pipeline)")
    p.add_argument("--input", default="/input", help="input dir (default: /input)")
    p.add_argument("--output", default="/app/output", help="output dir (default: /app/output)")
    p.add_argument("--work", default="/app/work", help="work dir (default: /app/work)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "selftest":
            run_selftest(args)
            return 0
        if args.command == "discover":
            run_discover(args)
        elif args.command == "scaffold":
            run_scaffold(args)
        elif args.command == "finalize":
            run_finalize(args)
        else:
            run_pipeline(args)
        return 0
    except SystemExit:
        raise
    except Exception as exc:  # keep process alive: report and exit non-zero
        print(f"[error] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
