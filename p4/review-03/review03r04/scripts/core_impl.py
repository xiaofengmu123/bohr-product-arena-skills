#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py - research-methods & statistics review pipeline (stdlib only).

Subcommands:
  discover  scan /input, extract text, build manifest/segments/texts/terms
  scaffold  write a legal partial result.json + state.json + meta.json early
  finalize  merge findings + meta, run numeric checks, validate, render report.md
  selftest  alias for scripts/selftest.py

Defaults: --input /input --output /app/output --work /app/work
Deterministic: no randomness, no wall-clock in output; stable ordering.
"""

import argparse
import csv
import io
import json
import os
import platform
import re
import subprocess
import sys
import zipfile
from collections import Counter

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
PDFTOOL_TIMEOUT = 120

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
EVIDENCE_GRADES = ["confirmed", "probable", "not_verified", "not_reported"]
BASIS_VALUES = ["verbatim", "inferred", "external", "not_verified"]
REVIEW_STATUSES = ["confirmed", "needs_review"]
CONFIDENCES = ["high", "medium", "low"]

SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".csv", ".json", ".docx"}

# Method / statistical keywords for locating methods-and-results passages.
KEYWORDS = [
    "method", "design", "sample size", "power", "randomiz", "blind",
    "allocation", "recruit", "inclusion", "exclusion", "covariate",
    "outcome", "endpoint", "analysis", "statistic", "test", "regression",
    "model", "anova", "t-test", "t test", "chi", "fisher", "kaplan", "cox",
    "log-rank", "non-parametric", "nonparametric", "wilcoxon", "mann-whitney",
    "kruskal", "confidence interval", "p value", "p-value", "p =", "p<",
    "significan", "multiple comparison", "bonferroni", "holm", "fdr",
    "missing", "effect size", "odds ratio", "hazard ratio", "risk ratio",
    "standard deviation", "mean", "median", "interquartile",
    "方法", "设计", "样本量", "样本", "检验效能", "把握度", "随机", "盲法",
    "分配", "分组", "入组", "招募", "纳入", "排除", "协变量", "结局", "终点",
    "分析", "统计", "检验", "回归", "模型", "方差", "卡方", "置信区间", "p值",
    "p 值", "显著", "多重比较", "缺失", "效应量", "比值比", "风险比",
    "危险比", "标准差", "均数", "中位",
]


# --------------------------------------------------------------------------
# low-level, tolerant IO
# --------------------------------------------------------------------------
def _read_text(path):
    """Return (text, error). Tries utf-8-sig then latin-1 as a last resort."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read(), None
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1") as fh:
                return fh.read(), None
        except Exception as exc:  # noqa: BLE001
            return "", "bad_encoding:%s" % exc
    except Exception as exc:  # noqa: BLE001
        return "", "read_error:%s" % exc


def _load_json(path):
    """Return (obj, error)."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as exc:  # noqa: BLE001
        return None, "bad_json:%s" % exc


def _iso_source_timestamp(paths):
    """Deterministic timestamp derived from the newest input mtime."""
    best = 0.0
    for p in paths:
        try:
            best = max(best, os.path.getmtime(p))
        except OSError:
            pass
    if best <= 0.0:
        return ""
    import datetime

    return datetime.datetime.fromtimestamp(best).strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------
# text extraction
# --------------------------------------------------------------------------
def _extract_pdf(path):
    """Return (list_of_page_texts, error)."""
    if not shutil_which("pdftotext"):
        return None, "no_pdftotext"
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True,
            text=True,
            timeout=PDFTOOL_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return None, "pdftotext_error:%s" % exc
    if proc.returncode != 0:
        return None, "pdftotext_exit:%d:%s" % (
            proc.returncode, (proc.stderr or "")[:200])
    pages = [p for p in proc.stdout.split("\f") if p.strip()]
    if not pages:
        return None, "pdf_no_text"
    return pages, None


def _extract_docx(path):
    """Extract paragraph texts from word/document.xml (stdlib zipfile)."""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, "docx_error:%s" % exc
    # Split paragraphs, then collect text inside <w:t> runs.
    paras = re.split(r"</w:p>", xml)
    out = []
    for para in paras:
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S)
        text = "".join(runs)
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if text.strip():
            out.append(text.strip())
    if not out:
        return None, "docx_no_text"
    return out, None


def _extract_csv(path):
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
    except Exception as exc:  # noqa: BLE001
        return None, "csv_error:%s" % exc
    texts = [" | ".join(c for c in row).strip() for row in rows]
    texts = [t for t in texts if t]
    if not texts:
        return None, "csv_empty"
    return texts, None


def _extract_json(path):
    obj, err = _load_json(path)
    if err:
        return None, err
    if isinstance(obj, dict):
        return [json.dumps(obj, ensure_ascii=False)], None
    if isinstance(obj, list):
        items = []
        for it in obj:
            items.append(json.dumps(it, ensure_ascii=False))
        return items, None
    return [str(obj)], None


def _extract_plain(path):
    text, err = _read_text(path)
    if err:
        return None, err
    paras = [p.strip() for p in re.split(r"\n\s*\n", text)]
    paras = [p for p in paras if p]
    if not paras:
        return None, "empty_text"
    return paras, None


def _extract_material(path, ext):
    """Return (list_of_blocks, error). Each block is (text, kind, locator_suffix)."""
    if ext == ".pdf":
        pages, err = _extract_pdf(path)
        if err:
            return None, err
        blocks = []
        for i, page in enumerate(pages, 1):
            blocks.append((page, "page", "p.%d" % i))
            paras = [p.strip() for p in re.split(r"\n\s*\n", page)]
            for j, para in enumerate([p for p in paras if p], 1):
                blocks.append((para, "paragraph", "p.%d ¶%d" % (i, j)))
        return blocks, None
    if ext == ".docx":
        paras, err = _extract_docx(path)
        if err:
            return None, err
        return [(p, "paragraph", "¶%d" % (i + 1)) for i, p in enumerate(paras)], None
    if ext == ".csv":
        rows, err = _extract_csv(path)
        if err:
            return None, err
        return [(r, "row", "row %d" % (i + 1)) for i, r in enumerate(rows)], None
    if ext == ".json":
        items, err = _extract_json(path)
        if err:
            return None, err
        return [(it, "item", "item %d" % (i + 1)) for i, it in enumerate(items)], None
    # .txt / .md
    paras, err = _extract_plain(path)
    if err:
        return None, err
    return [(p, "paragraph", "¶%d" % (i + 1)) for i, p in enumerate(paras)], None


def shutil_which(name):
    import shutil

    return shutil.which(name)


def _match_terms(text):
    low = text.lower()
    hits = []
    for kw in KEYWORDS:
        if kw.lower() in low:
            hits.append(kw)
    return hits


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------
def discover(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.join(work_dir, "texts"), exist_ok=True)

    manifest = {
        "input_dir": input_dir,
        "case_json": None,
        "task_md": None,
        "case_error": None,
        "files": [],
        "errors": [],
        "degradations": [],
    }

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    if os.path.exists(case_path):
        obj, err = _load_json(case_path)
        manifest["case_json"] = obj
        manifest["case_error"] = err
        if err:
            manifest["errors"].append({"path": "case.json", "error": err})
    else:
        manifest["errors"].append({"path": "case.json", "error": "missing"})
    if os.path.exists(task_path):
        task_text, err = _read_text(task_path)
        manifest["task_md"] = task_text if not err else None
        if err:
            manifest["errors"].append({"path": "task.md", "error": err})

    segments = []
    material_paths = []
    files = []
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            if rel in ("case.json", "task.md"):
                continue
            ext = os.path.splitext(name)[1].lower()
            files.append({"path": rel, "ext": ext, "size": os.path.getsize(path)})

    for rec in files:
        path = os.path.join(input_dir, rec["path"])
        entry = {
            "path": rec["path"],
            "ext": rec["ext"],
            "size": rec["size"],
            "status": "skipped",
            "error": None,
            "segments": 0,
        }
        if rec["ext"] not in SUPPORTED_EXTS:
            entry["error"] = "unsupported_ext"
            manifest["files"].append(entry)
            continue
        if rec["size"] > MAX_FILE_BYTES:
            entry["status"] = "oversize"
            entry["error"] = "oversize_file"
            manifest["degradations"].append(
                {"code": "D_OVERSIZE_FILE", "path": rec["path"], "reason": ">2MB"})
            manifest["files"].append(entry)
            continue

        blocks, err = _extract_material(path, rec["ext"])
        if err:
            entry["error"] = err
            if err in ("pdf_no_text", "no_pdftotext"):
                entry["status"] = "needs_ocr"
                manifest["degradations"].append(
                    {"code": "D_PDF_NO_TEXT" if err == "pdf_no_text"
                     else "D_NO_PDFTOTEXT",
                     "path": rec["path"], "reason": err})
            else:
                entry["status"] = "error"
                manifest["errors"].append({"path": rec["path"], "error": err})
            manifest["files"].append(entry)
            continue

        entry["status"] = "extracted"
        material_paths.append(path)
        full_text = ""
        base = rec["path"].replace("/", "__").replace("\\", "__")
        for text, kind, loc_suffix in blocks:
            seg_id = "S%03d" % (len(segments) + 1)
            segments.append({
                "id": seg_id,
                "source": rec["path"],
                "kind": kind,
                "locator": loc_suffix,
                "text": text,
                "chars": len(text),
                "leaf": kind in ("paragraph", "row", "item"),
            })
            full_text += text + "\n"
        with open(os.path.join(work_dir, "texts", base + ".txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(full_text)
        entry["segments"] = len(blocks)
        manifest["files"].append(entry)

    if not material_paths:
        manifest["degradations"].append(
            {"code": "D_EMPTY_INPUT", "path": None, "reason": "no material files"})

    # terms
    terms = {"hits": {}, "frequency": {}}
    freq = Counter()
    for seg in segments:
        if not seg["leaf"]:
            continue
        hits = _match_terms(seg["text"])
        if hits:
            terms["hits"][seg["id"]] = hits
            for h in hits:
                freq[h] += 1
    terms["frequency"] = dict(sorted(freq.items(), key=lambda kv: (-kv[1], kv[0])))

    with open(os.path.join(work_dir, "segments.json"), "w", encoding="utf-8") as fh:
        json.dump(segments, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(work_dir, "terms.json"), "w", encoding="utf-8") as fh:
        json.dump(terms, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(work_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print("discover: %d material files, %d segments" %
          (len(manifest["files"]), len(segments)))
    if manifest["errors"]:
        print("discover errors: %d" % len(manifest["errors"]))
    if manifest["degradations"]:
        print("discover degradations: %d" % len(manifest["degradations"]))
    return 0


# --------------------------------------------------------------------------
# shared result skeleton
# --------------------------------------------------------------------------
def _apply_contract(result, case_obj):
    """Backfill output_contract keys verbatim; missing -> null."""
    if not isinstance(case_obj, dict):
        return result
    contract = case_obj.get("output_contract")
    if isinstance(contract, dict):
        for k, v in contract.items():
            result[k] = v if v not in (None, "", [], {}) else None
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                result[item] = None
            elif isinstance(item, dict):
                for k, v in item.items():
                    result[k] = v if v not in (None, "", [], {}) else None
    return result


def _zero_summary():
    return {
        "verdict": "unchecked",
        "counts": {s: 0 for s in SEVERITIES},
        "by_facet": {f: 0 for f in FACETS},
        "by_type": {t: 0 for t in FINDING_TYPES},
    }


def _zero_coverage():
    return {
        "facets": {f: {"status": "unchecked", "findings": 0, "notes": ""}
                   for f in FACETS},
        "check_classes": {c: {"status": "unchecked", "findings": 0, "notes": ""}
                          for c in CHECK_CLASSES},
    }


def _base_result(input_dir):
    case_path = os.path.join(input_dir, "case.json")
    case_obj, case_err = _load_json(case_path)
    case_id = "unknown"
    if case_obj is None:
        if case_err == "missing":
            case_id = "unknown"
    elif isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("id") or "unknown"
    else:
        case_id = "unknown"

    result = {
        "case_id": case_id,
        "status": "partial",
        "result_status": "unchecked",
        "summary": _zero_summary(),
        "coverage": _zero_coverage(),
        "review_report": {"findings": []},
        "evidence": {"evidence_map": [], "conflict_index": []},
        "sources": [],
        "uncertainty": {"items": [], "pending_human": []},
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [{"seq": 1, "action": "scaffold", "note": "partial skeleton"}],
        "runtime": {
            "engine": "python-stdlib",
            "python_version": platform.python_version(),
            "deterministic": True,
            "source_timestamp": _iso_source_timestamp(
                [os.path.join(input_dir, n) for n in ("case.json", "task.md")]
                if os.path.isdir(input_dir) else [input_dir]),
            "scripts": ["scripts/main.py"],
        },
        "numeric_checks": {"candidates": []},
        "validation": [],
    }
    result = _apply_contract(result, case_obj)
    return result


# --------------------------------------------------------------------------
# scaffold
# --------------------------------------------------------------------------
def scaffold(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    result = _base_result(input_dir)

    manifest_path = os.path.join(work_dir, "manifest.json")
    if os.path.exists(manifest_path):
        manifest, _ = _load_json(manifest_path)
        if isinstance(manifest, dict):
            for err in manifest.get("errors", []):
                result["errors"].append(err)
            for deg in manifest.get("degradations", []):
                result["degradations"].append(deg)

    meta = {
        "status": "partial",
        "complete": False,
        "overall_verdict": "",
        "overall_assessment": "",
        "result_status": "unchecked",
        "checked_facets": [],
        "checked_classes": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
    }
    with open(os.path.join(work_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(work_dir, "state.json"), "w", encoding="utf-8") as fh:
        json.dump({"phase": "scaffold", "findings_count": 0}, fh,
                  ensure_ascii=False, indent=2)

    _write_result(result, output_dir)
    print("scaffold: result.json written (case_id=%s, status=%s)"
          % (result["case_id"], result["status"]))
    return 0


def _write_result(result, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return out_path


# --------------------------------------------------------------------------
# finalize helpers
# --------------------------------------------------------------------------
def _locator_key(loc):
    nums = [int(x) for x in re.findall(r"\d+", loc or "")]
    return (nums, loc or "")


def _load_findings(work_dir):
    findings = []
    errors = []
    fdir = os.path.join(work_dir, "findings")
    if not os.path.isdir(fdir):
        return findings, errors
    for name in sorted(os.listdir(fdir)):
        path = os.path.join(fdir, name)
        obj, err = _load_json(path)
        if err:
            errors.append({"path": "findings/" + name, "error": err})
            continue
        if isinstance(obj, dict):
            findings.append(obj)
        elif isinstance(obj, list):
            findings.extend([x for x in obj if isinstance(x, dict)])
        else:
            errors.append({"path": "findings/" + name, "error": "not_object"})
    findings.sort(key=lambda f: (_locator_key(f.get("locator", "")),
                                 f.get("id", "")))
    return findings, errors


def _find_pvalues(text):
    """Best-effort p-value extraction."""
    vals = []
    for m in re.finditer(r"[pP]\s*(?:[=<>≤≥]|less than)\s*([0-9]*\.?[0-9]+)",
                         text):
        try:
            vals.append(float(m.group(1)))
        except ValueError:
            pass
    return vals


def _significance_polarity(text):
    """Return 1 if segment asserts significance, -1 if asserts non-significance,
    0 if neither."""
    low = text.lower()
    pos = re.search(r"significan|显著|有统计学意义|差异有", low)
    neg = re.search(r"not\s+significan|no\s+significan|不显著|无统计学意义|"
                    r"无显著|未见显著|没有显著|差异无", low)
    if pos and not neg:
        return 1
    if neg and not pos:
        return -1
    return 0


def _ci_null_indicator(text):
    low = text.lower()
    if re.search(r"\b(or|rr|hr)\b|odds ratio|hazard ratio|risk ratio|比值比|"
                 r"风险比|危险比", low):
        return 1.0
    if re.search(r"mean difference|difference|smd|md\b|coef|β|效应量|均数差",
                 low):
        return 0.0
    return None


def _find_cis(text):
    """Return list of (lo, hi) from CI-like patterns."""
    cis = []
    for m in re.finditer(
            r"(?:95%?\s*)?(?:CI|置信区间)\s*[=:，,（(]?\s*"
            r"(-?\d+(?:\.\d+)?)\s*(?:,|to|–|-|~|，)\s*(-?\d+(?:\.\d+)?)",
            text, re.I):
        try:
            cis.append((float(m.group(1)), float(m.group(2))))
        except ValueError:
            pass
    return cis


def _snippet(text, center, width=160):
    if center is None:
        center = 0
    start = max(0, center - width // 2)
    end = min(len(text), center + width // 2)
    return text[start:end].strip()


def _numeric_checks(segments):
    candidates = []
    leaf = [s for s in segments if s.get("leaf")]
    for seg in leaf:
        text = seg["text"]
        pvals = _find_pvalues(text)
        polarity = _significance_polarity(text)
        if pvals and polarity:
            small = [p for p in pvals if p < 0.05]
            large = [p for p in pvals if p >= 0.05]
            if polarity == 1 and not small and large:
                candidates.append({
                    "rule": "p_wording",
                    "locator": seg["locator"],
                    "source": seg["source"],
                    "segment_id": seg["id"],
                    "matched": ["p=%.4g" % p for p in large],
                    "note": "显著措辞与 p>=0.05 并存（候选，须确认同一结局）",
                    "status": "candidate",
                    "confirmed": False,
                })
            if polarity == -1 and small:
                candidates.append({
                    "rule": "p_wording",
                    "locator": seg["locator"],
                    "source": seg["source"],
                    "segment_id": seg["id"],
                    "matched": ["p=%.4g" % p for p in small],
                    "note": "不显著措辞与 p<0.05 并存（候选，须确认同一结局）",
                    "status": "candidate",
                    "confirmed": False,
                })

        cis = _find_cis(text)
        null = _ci_null_indicator(text)
        if cis and null is not None and pvals:
            for lo, hi in cis:
                crosses = (null > min(lo, hi)) and (null < max(lo, hi))
                has_small = any(p < 0.05 for p in pvals)
                has_large = any(p >= 0.05 for p in pvals)
                if crosses and has_small:
                    candidates.append({
                        "rule": "p_ci",
                        "locator": seg["locator"],
                        "source": seg["source"],
                        "segment_id": seg["id"],
                        "matched": ["CI(%g, %g)" % (lo, hi)],
                        "note": "CI 跨 null 却 p<0.05（候选，须确认指标 null 值）",
                        "status": "candidate",
                        "confirmed": False,
                    })
                if not crosses and has_large:
                    candidates.append({
                        "rule": "p_ci",
                        "locator": seg["locator"],
                        "source": seg["source"],
                        "segment_id": seg["id"],
                        "matched": ["CI(%g, %g)" % (lo, hi)],
                        "note": "CI 未跨 null 却 p>=0.05（候选，须确认指标 null 值）",
                        "status": "candidate",
                        "confirmed": False,
                    })

    # Rule 3: N/n inconsistencies within the same total-sample context.
    groups = {}
    for seg in leaf:
        for m in re.finditer(r"(?i)(\bN\b|\bn\b)\s*[=:]\s*(\d+)", seg["text"]):
            label = m.group(1).lower()
            groups.setdefault(label, {}).setdefault(int(m.group(2)), []).append(
                seg["locator"])
        for m in re.finditer(
                r"(\d+)\s*(?:patients|participants|subjects|samples|"
                r"病例|例|受试者|样本|名患者)", seg["text"]):
            label = "n_phrased"
            groups.setdefault(label, {}).setdefault(int(m.group(1)), []).append(
                seg["locator"])
    for label, nums in groups.items():
        if len(nums) > 1:
            values = sorted(nums)
            candidates.append({
                "rule": "n_inconsistency",
                "locator": None,
                "source": None,
                "segment_id": None,
                "matched": [{"value": v, "locs": nums[v]} for v in values],
                "note": "同语境 %s 出现多个 N/n 数值（候选，须区分总数/分组数）"
                        % label,
                "status": "candidate",
                "confirmed": False,
            })
    return candidates


def _build_summary(findings):
    summary = _zero_summary()
    for f in findings:
        sev = f.get("severity")
        if sev in summary["counts"]:
            summary["counts"][sev] += 1
        fac = f.get("facet")
        if fac in summary["by_facet"]:
            summary["by_facet"][fac] += 1
        typ = f.get("finding_type")
        if typ in summary["by_type"]:
            summary["by_type"][typ] += 1
    return summary


def _build_coverage(findings, meta):
    cov = _zero_coverage()
    checked_facets = set(meta.get("checked_facets", []))
    checked_classes = set(meta.get("checked_classes", []))
    for f in findings:
        fac = f.get("facet")
        if fac in cov["facets"]:
            cov["facets"][fac]["findings"] += 1
        cls = f.get("check_class")
        if cls in cov["check_classes"]:
            cov["check_classes"][cls]["findings"] += 1
    for f in FACETS:
        if f in checked_facets:
            cov["facets"][f]["status"] = "checked"
        elif cov["facets"][f]["findings"]:
            cov["facets"][f]["status"] = "partial"
    for c in CHECK_CLASSES:
        if c in checked_classes:
            cov["check_classes"][c]["status"] = "checked"
        elif cov["check_classes"][c]["findings"]:
            cov["check_classes"][c]["status"] = "partial"
    return cov


def _validate_finding(f, idx, issues):
    fid = f.get("id", "F%03d" % idx)
    for key in ("id", "facet", "check_class", "finding_type", "severity",
                "locator", "quote", "statement", "suggestion", "basis",
                "evidence_grade", "confidence", "review_status"):
        if key not in f or f[key] in (None, ""):
            issues.append({"level": "error", "finding": fid,
                           "message": "missing required field: %s" % key})
    if f.get("facet") not in FACETS:
        issues.append({"level": "error", "finding": fid,
                       "message": "invalid facet: %r" % f.get("facet")})
    if f.get("check_class") not in CHECK_CLASSES:
        issues.append({"level": "error", "finding": fid,
                       "message": "invalid check_class: %r" % f.get("check_class")})
    if f.get("finding_type") not in FINDING_TYPES:
        issues.append({"level": "error", "finding": fid,
                       "message": "invalid finding_type: %r" % f.get("finding_type")})
    if f.get("severity") not in SEVERITIES:
        issues.append({"level": "error", "finding": fid,
                       "message": "invalid severity: %r" % f.get("severity")})
    if f.get("evidence_grade") not in EVIDENCE_GRADES:
        issues.append({"level": "error", "finding": fid,
                       "message": "invalid evidence_grade: %r"
                                  % f.get("evidence_grade")})
    if f.get("basis") not in BASIS_VALUES:
        issues.append({"level": "error", "finding": fid,
                       "message": "invalid basis: %r" % f.get("basis")})
    if f.get("review_status") not in REVIEW_STATUSES:
        issues.append({"level": "error", "finding": fid,
                       "message": "invalid review_status: %r"
                                  % f.get("review_status")})

    ft = f.get("finding_type")
    sev = f.get("severity")
    eg = f.get("evidence_grade")
    # Red-line consistency checks.
    if ft == "insufficient_evidence" and sev == "critical":
        issues.append({"level": "error", "finding": fid,
                       "message": "insufficient_evidence cannot be critical"})
    if ft == "factual_error" and sev in ("advisory", "pending"):
        issues.append({"level": "error", "finding": fid,
                       "message": "factual_error cannot be advisory/pending"})
    if ft == "advisory" and sev != "advisory":
        issues.append({"level": "error", "finding": fid,
                       "message": "advisory finding must have advisory severity"})
    if ft == "pending_review" and sev != "pending":
        issues.append({"level": "error", "finding": fid,
                       "message": "pending_review finding must have pending severity"})
    if eg == "not_verified" and (ft != "pending_review"
                                 or sev != "pending"
                                 or f.get("review_status") != "needs_review"):
        issues.append({"level": "error", "finding": fid,
                       "message": "not_verified must be pending_review/pending/"
                                  "needs_review"})


def _derive_status(meta, findings, manifest):
    files = manifest.get("files", []) if isinstance(manifest, dict) else []
    no_material = not any(f.get("status") == "extracted" for f in files)
    case_json = manifest.get("case_json") if isinstance(manifest, dict) else None
    no_case = case_json is None
    if no_case and no_material:
        return "input_error"
    if no_material:
        return "insufficient_evidence"
    if meta.get("complete") is True and set(FACETS) <= set(
            meta.get("checked_facets", [])):
        return "ok"
    return "partial"


def _derive_result_status(status, findings):
    has_real = any(f.get("severity") in ("critical", "major", "minor")
                   for f in findings)
    if has_real:
        return "issues"
    if any(f.get("severity") == "pending"
           or f.get("review_status") == "needs_review" for f in findings):
        return "needs_review"
    if status == "ok":
        return "pass"
    return "unchecked"


def _build_evidence(findings):
    emap = []
    conflicts = []
    for f in findings:
        emap.append({
            "finding_id": f.get("id"),
            "locator": f.get("locator"),
            "quote": f.get("quote"),
            "basis": f.get("basis"),
            "evidence_grade": f.get("evidence_grade"),
        })
        for cloc in f.get("conflicts", []) or []:
            conflicts.append({"finding_id": f.get("id"), "locator": cloc})
    return {"evidence_map": emap, "conflict_index": conflicts}


# --------------------------------------------------------------------------
# report rendering
# --------------------------------------------------------------------------
def _render_report(result):
    lines = []
    lines.append("# 研究方法与统计审查报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- result_status: `%s`" % result.get("result_status"))
    lines.append("- verdict: %s" % result.get("summary", {}).get("verdict", ""))
    lines.append("")

    summary = result.get("summary", {})
    lines.append("## 摘要")
    lines.append("")
    counts = summary.get("counts", {})
    lines.append("严重度：%s" % "，".join(
        "%s=%d" % (k, counts.get(k, 0)) for k in SEVERITIES))
    lines.append("检查面：%s" % "，".join(
        "%s=%d" % (k, summary.get("by_facet", {}).get(k, 0)) for k in FACETS))
    lines.append("缺陷类型：%s" % "，".join(
        "%s=%d" % (k, summary.get("by_type", {}).get(k, 0))
        for k in FINDING_TYPES))
    lines.append("")

    lines.append("## 审查结论（findings）")
    lines.append("")
    findings = result.get("review_report", {}).get("findings", [])
    if not findings:
        lines.append("（暂无已确认的 finding；未确证项见「不确定性」一节。）")
        lines.append("")
    for f in findings:
        lines.append("### %s  [%s]  %s / %s" % (
            f.get("id"), f.get("severity"), f.get("facet"),
            f.get("finding_type")))
        lines.append("")
        lines.append("- locator: `%s`" % f.get("locator"))
        lines.append("- 证据强度: `%s`（basis=%s，confidence=%s，review=%s）"
                     % (f.get("evidence_grade"), f.get("basis"),
                        f.get("confidence"), f.get("review_status")))
        lines.append("- 问题: %s" % f.get("statement"))
        lines.append("- 建议: %s" % f.get("suggestion"))
        quote = (f.get("quote") or "").replace("\n", " ")
        lines.append("")
        lines.append("> %s" % quote)
        lines.append("")
        if f.get("conflicts"):
            lines.append("- 冲突位置: %s" % "；".join(f.get("conflicts")))
            lines.append("")

    lines.append("## 检查面覆盖")
    lines.append("")
    lines.append("| facet | status | findings |")
    lines.append("|---|---|---|")
    for k, v in result.get("coverage", {}).get("facets", {}).items():
        lines.append("| %s | %s | %d |" % (k, v.get("status"), v.get("findings")))
    lines.append("")
    lines.append("| check_class | status | findings |")
    lines.append("|---|---|---|")
    for k, v in result.get("coverage", {}).get("check_classes", {}).items():
        lines.append("| %s | %s | %d |" % (k, v.get("status"), v.get("findings")))
    lines.append("")

    nc = result.get("numeric_checks", {}).get("candidates", [])
    lines.append("## 数值一致性候选（须人工确认）")
    lines.append("")
    if not nc:
        lines.append("（无候选）")
        lines.append("")
    for c in nc:
        lines.append("- [%s] %s %s" % (c.get("rule"), c.get("locator") or "",
                                      c.get("note")))
    lines.append("")

    un = result.get("uncertainty", {})
    lines.append("## 不确定性与待复核")
    lines.append("")
    if un.get("items"):
        for it in un["items"]:
            lines.append("- %s" % (it if isinstance(it, str)
                                   else json.dumps(it, ensure_ascii=False)))
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("### pending_human")
    lines.append("")
    if un.get("pending_human"):
        for it in un["pending_human"]:
            lines.append("- %s" % (it if isinstance(it, str)
                                   else json.dumps(it, ensure_ascii=False)))
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 局限（limitations）")
    lines.append("")
    if result.get("limitations"):
        for it in result["limitations"]:
            lines.append("- %s" % it)
    else:
        lines.append("（未声明）")
    lines.append("")

    lines.append("## 降级记录（degradations）")
    lines.append("")
    if result.get("degradations"):
        for d in result["degradations"]:
            lines.append("- `%s` %s：%s" % (d.get("code"), d.get("path") or "",
                                           d.get("reason")))
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 来源（sources）")
    lines.append("")
    if result.get("sources"):
        for s in result["sources"]:
            lines.append("- %s" % (s if isinstance(s, str)
                                   else json.dumps(s, ensure_ascii=False)))
    else:
        lines.append("（未列出外部来源）")
    lines.append("")

    lines.append("## 变更记录（change_log）")
    lines.append("")
    for c in result.get("change_log", []):
        lines.append("- [%s] %s：%s" % (c.get("seq"), c.get("action"),
                                        c.get("note")))
    lines.append("")

    lines.append("## 校验（validation）")
    lines.append("")
    if result.get("validation"):
        for v in result["validation"]:
            lines.append("- [%s] %s：%s" % (v.get("level"), v.get("finding"),
                                            v.get("message")))
    else:
        lines.append("（通过）")
    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# finalize
# --------------------------------------------------------------------------
def finalize(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    result = _base_result(input_dir)

    manifest, _ = _load_json(os.path.join(work_dir, "manifest.json"))
    if not isinstance(manifest, dict):
        manifest = {}
    meta, _ = _load_json(os.path.join(work_dir, "meta.json"))
    if not isinstance(meta, dict):
        meta = {}
    segments, _ = _load_json(os.path.join(work_dir, "segments.json"))
    if not isinstance(segments, list):
        segments = []

    findings, find_errs = _load_findings(work_dir)
    result["review_report"]["findings"] = findings
    result["summary"] = _build_summary(findings)
    result["coverage"] = _build_coverage(findings, meta)

    result["evidence"] = _build_evidence(findings)
    result["numeric_checks"]["candidates"] = _numeric_checks(segments)

    # Sources: auto-list actually extracted material files + agent-cited sources.
    sources = []
    seen = set()
    for rec in manifest.get("files", []):
        if rec.get("status") == "extracted":
            key = "material:" + rec.get("path", "")
            if key not in seen:
                sources.append({"kind": "material", "path": rec.get("path")})
                seen.add(key)
    for s in meta.get("sources", []) or []:
        if isinstance(s, dict):
            key = s.get("kind", "") + ":" + s.get("path", s.get("name", ""))
        else:
            key = str(s)
        if key not in seen:
            sources.append(s)
            seen.add(key)
    result["sources"] = sources

    result["uncertainty"] = {
        "items": meta.get("uncertainty", []) or [],
        "pending_human": meta.get("pending_human", []) or [],
    }
    result["limitations"] = meta.get("limitations", []) or []
    result["errors"] = (manifest.get("errors", []) or []) + find_errs
    result["degradations"] = _dedupe_degradations(
        (manifest.get("degradations", []) or [])
        + (meta.get("degradations", []) or []))

    # change_log: scaffold entry + agent entries + finalize entry.
    change_log = [{"seq": 1, "action": "scaffold", "note": "partial skeleton"}]
    for entry in meta.get("change_log", []) or []:
        if isinstance(entry, dict):
            change_log.append(entry)
    change_log.append({"seq": len(change_log) + 1, "action": "finalize",
                       "note": "merged %d findings" % len(findings)})
    result["change_log"] = change_log

    # validation
    issues = []
    for idx, f in enumerate(findings, 1):
        _validate_finding(f, idx, issues)
    result["validation"] = issues

    # status / result_status / verdict
    result["status"] = _derive_status(meta, findings, manifest)
    result["result_status"] = _derive_result_status(result["status"], findings)
    if meta.get("overall_verdict"):
        result["summary"]["verdict"] = meta["overall_verdict"]
    elif result["status"] == "ok":
        result["summary"]["verdict"] = "complete"
    elif result["status"] in ("insufficient_evidence", "input_error"):
        result["summary"]["verdict"] = "unchecked"

    out_path = _write_result(result, output_dir)
    report = _render_report(result)
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report)

    # update state deterministically
    with open(os.path.join(work_dir, "state.json"), "w", encoding="utf-8") as fh:
        json.dump({"phase": "finalized", "findings_count": len(findings),
                   "status": result["status"]}, fh, ensure_ascii=False, indent=2)

    print("finalize: %d findings, status=%s, result_status=%s, validation=%d"
          % (len(findings), result["status"], result["result_status"],
             len(issues)))
    print("finalize: wrote %s" % out_path)
    return 0


def _dedupe_degradations(items):
    seen = set()
    out = []
    for d in items:
        if not isinstance(d, dict):
            continue
        key = (d.get("code"), d.get("path"), d.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Research-methods & statistics review pipeline (stdlib only)")
    sub = parser.add_subparsers(dest="cmd")

    for name in ("discover", "scaffold", "finalize"):
        p = sub.add_parser(name)
        p.add_argument("--input", default=DEFAULT_INPUT)
        p.add_argument("--output", default=DEFAULT_OUTPUT)
        p.add_argument("--work", default=DEFAULT_WORK)

    p = sub.add_parser("selftest")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "selftest":
        here = os.path.dirname(os.path.abspath(__file__))
        selftest = os.path.join(here, "selftest.py")
        if os.path.exists(selftest):
            import runpy

            sys.argv = [selftest]
            runpy.run_path(selftest, run_name="__main__")
            return 0
        print("selftest.py not found next to main.py", file=sys.stderr)
        return 1
    if args.cmd == "discover":
        return discover(args.input, args.output, args.work)
    if args.cmd == "scaffold":
        return scaffold(args.input, args.output, args.work)
    if args.cmd == "finalize":
        return finalize(args.input, args.output, args.work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
