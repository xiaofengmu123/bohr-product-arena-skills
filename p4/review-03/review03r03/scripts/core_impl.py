#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic discovery / scaffold / finalize pipeline for the research-methods
and statistical-review Skill.

Subcommands
-----------
discover   Scan the input directory, extract text (PDF via pdftotext, txt/md/csv/
           json/tsv directly, docx/pptx via zipfile+regex), build the page /
           paragraph locator index, a statistical-keyword hit index, and a
           machine-readable manifest.
scaffold   Write a legal ``result.json`` skeleton immediately (status=partial,
           verdict=unchecked, findings=[]). This is the "save early" step.
finalize   Merge the Agent-written finding files, run deterministic numeric
           candidate checks (p vs. wording, p vs. confidence interval, N/n
           inconsistency), validate every finding, verify quotes against the
           extracted text, render ``report.md``, and write ``result.json``.

Design guarantees (variant: self-test and determinism)
------------------------------------------------------
* Only the Python standard library is imported (pdftotext is invoked only when
  the input contains a PDF; missing pdftotext degrades gracefully).
* Given the same input files and the same ``findings/*.json``, ``finalize``
  writes byte-identical ``result.json`` and ``report.md`` on every run: output
  is serialized with ``sort_keys=True``, ``newline="\\n"`` and no wall-clock
  timestamps in the deliverables. Wall-clock is kept only in ``state.json``.
* The judgment layer (the Agent) is separated from the deterministic machine
  layer (these scripts), so a reviewer can re-run only the machine layer to
  reproduce the exact artifact from the recorded findings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from html import unescape

SCHEMA_VERSION = "2.0.0"
SCRIPT_VERSION = "2.0.0"

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
CONFIDENCES = ["high", "medium", "low"]
REVIEW_STATUSES = ["confirmed", "needs_review", "source_conflict"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
RESULT_STATUSES = ["pass", "issues_found", "needs_review", "not_checked"]

STAT_TERMS = [
    # English
    "p value", "p-value", "confidence interval", "sample size", "power",
    "effect size", "alpha", "t-test", "t test", "welch", "anova",
    "chi-square", "chi square", "fisher", "log-rank", "log rank", "cox",
    "logistic", "poisson", "negative binomial", "wilcoxon", "mann-whitney",
    "kruskal", "bonferroni", "holm", "fdr", "mixed model", "mixed-effects",
    "gee", "repeated measures", "randomized", "randomised", "blinding",
    "blinded", "baseline", "primary endpoint", "primary outcome",
    "secondary endpoint", "covariate", "confounder", "missing", "imputation",
    "intention-to-treat", "per-protocol", "as-treated", "sensitivity analysis",
    "subgroup", "interaction", "multiple comparison", "multiplicity",
    "normality", "variance", "standard deviation", "odds ratio", "risk ratio",
    "hazard ratio", "relative risk", "effect size", "cohen", "correlation",
    "regression", "linear regression", "generalized linear", "survival",
    "kaplan-meier", "censoring", "follow-up", "attrition", "dropout",
    "lost to follow-up", "significance level", "two-sided", "one-sided",
    "null hypothesis", "clinical trial", "cohort", "case-control",
    "cross-sectional", "longitudinal", "randomization",
    # Chinese
    "样本量", "检验效能", "把握度", "置信区间", "显著性", "多重比较",
    "随机化", "盲法", "失访", "缺失值", "随访", "意向性", "敏感性分析",
    "亚组", "混杂", "结局", "暴露", "协变量", "正态", "方差", "t 检验",
    "卡方", "回归", "生存分析",
]

P_RE = re.compile(
    r"\bP\s*(?:-?\s*value)?\s*(=|>|<)\s*(\d*\.?\d+)\b", re.IGNORECASE
)
CI_RE = re.compile(
    r"(?:95\s*%\s*)?CI\s*[:=]?\s*[\[(]?([−]?\d*\.?\d+)\s*(?:–|—|-|to|,)\s*"
    r"([−]?\d*\.?\d+)",
    re.IGNORECASE,
)
RATIO_RE = re.compile(
    r"\b(?:OR|RR|HR|odds ratio|risk ratio|hazard ratio|relative risk|ratio)\b",
    re.IGNORECASE,
)
N_RE = re.compile(r"\b[Nn]\s*=\s*(\d[\d,]*)")
N_PAREN_RE = re.compile(r"\(n\s*=\s*(\d[\d,]*)\)", re.IGNORECASE)

NEG_SIG = [
    "not significant", "non-significant", "nonsignificant",
    "no significant", "不显著", "无显著", "未显著", "无统计学意义",
    "没有统计学意义", "无统计学差异",
]
POS_SIG = [
    "significant", "statistically significant", "显著", "统计学意义",
    "有统计学意义", "有统计学差异",
]
ALPHA_CONTEXT = ["significance level", "significant level", "显著性水平", "检验水准"]

TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".tsv", ".log", ".rtf"}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".docx", ".pptx"}


def eprint(*args):
    print(*args, file=sys.stderr)


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def canonical_bytes(obj):
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return text.encode("utf-8")


def write_json(path, obj):
    ensure_dir(os.path.dirname(path))
    with open(path, "wb") as fh:
        fh.write(canonical_bytes(obj))


def read_json(path):
    """Return (object_or_None, error_or_None). Never raises."""
    if not os.path.isfile(path):
        return None, "not_found"
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}:{exc}"


def read_text(path):
    """Return (text_or_None, error_or_None). Tolerant of BOM and bad encoding."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read(), None
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1") as fh:
                return fh.read(), "decoded_latin1"
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}:{exc}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}:{exc}"


def norm_ws(text):
    return re.sub(r"\s+", " ", text or "").strip()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def input_digest(input_dir, rel_paths):
    h = hashlib.sha256()
    for rel in sorted(rel_paths):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(file_digest(os.path.join(input_dir, rel)).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def split_paragraphs(text):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def extract_pdf_text(path):
    exe = shutil.which("pdftotext")
    if exe is None:
        return None, "pdftotext_not_found"
    try:
        proc = subprocess.run(
            [exe, "-layout", path, "-"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"pdftotext_failed:{type(exc).__name__}"
    if proc.returncode != 0:
        return None, "pdftotext_nonzero"
    text = proc.stdout or ""
    if not text.strip():
        return None, "empty_text_needs_ocr"
    return text, None


def extract_office_text(path, ext):
    try:
        with zipfile.ZipFile(path) as zf:
            if ext == ".docx":
                xml = zf.read("word/document.xml").decode("utf-8", "replace")
                xml = xml.replace("</w:p>", "\n")
                text = re.sub(r"<[^>]+>", "", xml)
                text = unescape(text)
                return text, None
            names = sorted(
                n for n in zf.namelist()
                if re.match(r"ppt/slides/slide\d+\.xml$", n)
            )
            parts = []
            for name in names:
                slide = zf.read(name).decode("utf-8", "replace")
                texts = re.findall(r"<a:t>(.*?)</a:t>", slide, re.S)
                parts.append(" ".join(unescape(t) for t in texts))
            return "\n\n".join(parts), None
    except Exception as exc:  # noqa: BLE001
        return None, f"office_read_failed:{type(exc).__name__}"


def resolve_case_id(case, manifest):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            value = case.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(manifest, dict) and manifest.get("case_id"):
        return str(manifest["case_id"])
    return "unknown_case"


def iter_material_files(input_dir):
    """Sorted, deterministic list of material files (case.json/task.md excluded)."""
    out = []
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            if name in ("case.json", "task.md") or name.startswith("."):
                continue
            abs_path = os.path.join(root, name)
            if not os.path.isfile(abs_path):
                continue
            rel = os.path.relpath(abs_path, input_dir).replace("\\", "/")
            ext = os.path.splitext(name)[1].lower()
            out.append({"abs": abs_path, "rel": rel, "name": name, "ext": ext})
    return out


def _locator_key(locator):
    page = 0
    para = 0
    m = re.search(r"p\.\s*(\d+)", locator or "")
    if m:
        page = int(m.group(1))
    m = re.search(r"¶\s*(\d+)", locator or "")
    if m:
        para = int(m.group(1))
    return (page, para)


def finding_sort_key(f):
    facet = f.get("facet", "")
    fidx = FACETS.index(facet) if facet in FACETS else len(FACETS)
    return (fidx,) + _locator_key(f.get("locator", "")) + (f.get("id", ""),)


def assign_ids(findings):
    for i, f in enumerate(findings, 1):
        f["id"] = f"F{i:03d}"
    return findings


def p_significant(op, value):
    if value == 0:
        value = 0.0001
    if op == "<":
        return value <= 0.05
    if op == ">":
        return value > 0.05
    return value < 0.05


def parse_p_values(text):
    vals = []
    for m in P_RE.finditer(text):
        op, num = m.group(1), m.group(2)
        try:
            f = float(num)
        except ValueError:
            continue
        vals.append({
            "value": f,
            "op": op,
            "significant": p_significant(op, f),
            "text": m.group(0),
        })
    return vals


def parse_cis(text):
    out = []
    for m in CI_RE.finditer(text):
        try:
            lo = float(m.group(1))
            hi = float(m.group(2))
        except ValueError:
            continue
        out.append({"lo": lo, "hi": hi, "text": m.group(0)})
    return out


def significance_phrases(text):
    low = (text or "").lower()
    alpha = any(t in low for t in ALPHA_CONTEXT)
    neg = any(t in low for t in NEG_SIG)
    pos = False
    if not neg:
        pos = any(t in low for t in POS_SIG)
    return {"neg": neg, "pos": pos, "alpha_context": alpha}


def numeric_candidates(segments):
    out = []
    seen_pairs = set()
    for seg in segments:
        text = seg.get("text", "")
        loc = seg.get("locator", "?")
        pvals = parse_p_values(text)
        if not pvals:
            continue
        phrases = significance_phrases(text)
        if not phrases["alpha_context"] and (phrases["neg"] or phrases["pos"]):
            for pv in pvals:
                if pv["significant"] and phrases["neg"]:
                    key = ("p_vs_wording", loc, pv["text"])
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        out.append({
                            "id": "", "rule": "p_vs_wording", "locator": loc,
                            "excerpt": excerpt(text, 180),
                            "detail": f"{pv['text']} 提示 p<0.05，但同段措辞为“不显著/无显著”，需确认是否指向同一结局。",
                            "candidate": True, "needs_review": True,
                        })
                elif (not pv["significant"]) and phrases["pos"]:
                    key = ("p_vs_wording", loc, pv["text"])
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        out.append({
                            "id": "", "rule": "p_vs_wording", "locator": loc,
                            "excerpt": excerpt(text, 180),
                            "detail": f"{pv['text']} 提示 p>=0.05，但同段措辞为“显著”，需确认显著性表述是否越界。",
                            "candidate": True, "needs_review": True,
                        })
        cis = parse_cis(text)
        if cis:
            ratio = bool(RATIO_RE.search(text))
            null = 1.0 if ratio else 0.0
            for ci in cis:
                includes_null = min(ci["lo"], ci["hi"]) <= null <= max(ci["lo"], ci["hi"])
                for pv in pvals:
                    if pv["significant"] and includes_null:
                        key = ("p_vs_ci", loc, pv["text"], ci["text"])
                        if key not in seen_pairs:
                            seen_pairs.add(key)
                            out.append({
                                "id": "", "rule": "p_vs_ci", "locator": loc,
                                "excerpt": excerpt(text, 180),
                                "detail": (
                                    f"{pv['text']} 显著，但 {ci['text']} 包含无效值 "
                                    f"({null})，二者结论相反（候选，需人工确认）。"
                                ),
                                "candidate": True, "needs_review": True,
                            })
                    elif (not pv["significant"]) and (not includes_null):
                        key = ("p_vs_ci", loc, pv["text"], ci["text"])
                        if key not in seen_pairs:
                            seen_pairs.add(key)
                            out.append({
                                "id": "", "rule": "p_vs_ci", "locator": loc,
                                "excerpt": excerpt(text, 180),
                                "detail": (
                                    f"{pv['text']} 不显著，但 {ci['text']} 不含无效值 "
                                    f"({null})，二者结论相反（候选，需人工确认）。"
                                ),
                                "candidate": True, "needs_review": True,
                            })

    nvals = []
    for seg in segments:
        text = seg.get("text", "")
        loc = seg.get("locator", "?")
        for m in N_RE.finditer(text):
            try:
                val = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            nvals.append((loc, val, m.group(0)))
        for m in N_PAREN_RE.finditer(text):
            try:
                val = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            nvals.append((loc, val, m.group(0)))
    distinct = sorted({v for _, v, _ in nvals})
    if len(distinct) > 1:
        samples = nvals[:12]
        detail = "；".join(f"{loc}: {txt}" for loc, _v, txt in samples)
        out.append({
            "id": "", "rule": "n_inconsistency", "locator": samples[0][0],
            "excerpt": detail,
            "detail": (
                f"全材料出现多个不同 N/n 取值 {distinct}。可能是总样本量 vs 分组样本量，"
                "需人工核对是否真矛盾。"
            ),
            "candidate": True, "needs_review": True,
        })

    for i, item in enumerate(out, 1):
        item["id"] = f"NC{i:03d}"
    return out[:30]


def excerpt(text, width):
    t = norm_ws(text)
    return t if len(t) <= width else t[: width - 1] + "…"


def core_skeleton(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "result_status": "not_checked",
        "summary": {
            "verdict": "unchecked",
            "counts": {
                "total": 0, "critical": 0, "major": 0, "minor": 0,
                "advisory": 0, "pending": 0,
            },
            "by_facet": {f: 0 for f in FACETS},
            "by_type": {t: 0 for t in FINDING_TYPES},
        },
        "review_report": {
            "findings": [],
            "checked_facets": [],
            "unchecked": list(FACETS),
        },
        "evidence": {"evidence_map": {}},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "numeric_checks": [],
        "change_log": [],
        "runtime": {
            "schema_version": SCHEMA_VERSION,
            "script_version": SCRIPT_VERSION,
            "input_digest": "",
            "python_version": sys.version.split()[0],
        },
    }


def validate_finding(obj):
    errs = []
    for key in ("facet", "locator", "quote", "severity", "suggestion"):
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            errs.append(f"missing_or_empty:{key}")
    facet = obj.get("facet", "")
    if facet not in FACETS:
        errs.append(f"invalid_facet:{facet}")
    severity = obj.get("severity", "minor")
    if severity not in SEVERITIES:
        obj["severity"] = "pending"
        errs.append(f"coerced_severity:{severity}")
    ftype = obj.get("finding_type", "pending_review")
    if ftype not in FINDING_TYPES:
        obj["finding_type"] = "pending_review"
        errs.append(f"coerced_finding_type:{ftype}")
    cclass = obj.get("check_class", "method_design")
    if cclass not in CHECK_CLASSES:
        obj["check_class"] = "method_design"
        errs.append(f"coerced_check_class:{cclass}")
    conf = obj.get("confidence", "low")
    if conf not in CONFIDENCES:
        obj["confidence"] = "low"
        errs.append(f"coerced_confidence:{conf}")
    rstat = obj.get("review_status", "confirmed")
    if rstat not in REVIEW_STATUSES:
        obj["review_status"] = "needs_review"
        errs.append(f"coerced_review_status:{rstat}")
    return (not any(e.startswith("missing_or_empty") or e.startswith("invalid_facet")
                    for e in errs)), errs


def verify_quote(quote, segments):
    q = norm_ws(quote)
    if not q:
        return False
    for seg in segments:
        if q in norm_ws(seg.get("text", "")):
            return True
    return False


def build_sources(manifest):
    sources = []
    for f in (manifest or {}).get("files", []):
        sources.append({
            "path": f.get("rel"),
            "kind": f.get("kind"),
            "chars": f.get("chars"),
            "pages": f.get("pages"),
            "status": f.get("status"),
        })
    return sources


def compute_change_log(digest, findings, checks_count, material_count):
    findings_digest = sha256_bytes(
        b"".join(canonical_bytes(f) for f in sorted(findings, key=lambda x: x["id"]))
    )
    return [
        {
            "seq": 0, "actor": "script", "action": "discover",
            "version": SCRIPT_VERSION, "input_digest": digest,
            "material_count": material_count,
        },
        {
            "seq": 1, "actor": "script", "action": "scaffold",
            "version": SCRIPT_VERSION, "finding_count": 0,
        },
        {
            "seq": 2, "actor": "script", "action": "merge_findings",
            "version": SCRIPT_VERSION, "finding_count": len(findings),
            "findings_digest": findings_digest,
            "numeric_check_count": checks_count,
        },
    ]


def render_report(result):
    lines = []
    lines.append("# 研究方法与统计审查报告")
    lines.append("")
    lines.append(f"- case_id: `{result.get('case_id', '')}`")
    lines.append(f"- status: `{result.get('status', '')}`")
    lines.append(f"- result_status: `{result.get('result_status', '')}`")
    summary = result.get("summary", {}) or {}
    lines.append(f"- 结论: {summary.get('verdict', '')}")
    lines.append("")
    lines.append("## 汇总")
    counts = summary.get("counts", {}) or {}
    lines.append(f"- 发现总数: {counts.get('total', 0)}")
    lines.append("")
    lines.append("| 检查面 | 条数 |")
    lines.append("|---|---|")
    for facet in FACETS:
        lines.append(f"| {facet} | {(summary.get('by_facet') or {}).get(facet, 0)} |")
    lines.append("")
    lines.append("| 缺陷类型 | 条数 |")
    lines.append("|---|---|")
    for ftype in FINDING_TYPES:
        lines.append(f"| {ftype} | {(summary.get('by_type') or {}).get(ftype, 0)} |")
    lines.append("")
    lines.append("## 逐条发现")
    findings = ((result.get("review_report") or {}).get("findings")) or []
    if not findings:
        lines.append("_（暂无发现）_")
    for f in findings:
        lines.append("")
        lines.append(f"### {f.get('id', '')} · {f.get('facet', '')} · "
                     f"{f.get('finding_type', '')} · {f.get('severity', '')}")
        lines.append(f"- 位置: `{f.get('locator', '')}`")
        lines.append("- 原文摘录（逐字）:")
        lines.append("")
        lines.append("  ```")
        for qline in (f.get("quote", "") or "").splitlines():
            lines.append("  " + qline)
        lines.append("  ```")
        lines.append(f"- 判断依据: {f.get('basis', '')}")
        lines.append(f"- 修改建议: {f.get('suggestion', '')}")
        lines.append(f"- 审查面: {f.get('check_class', '')} · 置信度: "
                     f"{f.get('confidence', '')} · 复核状态: {f.get('review_status', '')}")
    lines.append("")
    lines.append("## 数值一致性候选（脚本检出，待人工确认）")
    checks = result.get("numeric_checks") or []
    if not checks:
        lines.append("_（无候选）_")
    for c in checks:
        lines.append(f"- `{c.get('id', '')}` [{c.get('rule', '')}] "
                     f"`{c.get('locator', '')}`: {c.get('detail', '')}")
    lines.append("")
    lines.append("## 证据映射")
    evidence = ((result.get("evidence") or {}).get("evidence_map")) or {}
    for fid in sorted(evidence):
        ev = evidence[fid]
        lines.append(f"- `{fid}` @ `{ev.get('locator', '')}`: "
                     f"{norm_ws(ev.get('quote', ''))[:120]}")
    lines.append("")
    lines.append("## 来源")
    for s in result.get("sources") or []:
        lines.append(f"- {s.get('path', '')} ({s.get('kind', '')}, "
                     f"{s.get('status', '')})")
    lines.append("")
    lines.append("## 不确定性 / 待复核 / 局限 / 错误 / 降级")
    for label, key in (
        ("不确定性", "uncertainty"), ("待复核", "pending_human"),
        ("局限", "limitations"), ("错误", "errors"), ("降级", "degradations"),
    ):
        items = result.get(key) or []
        lines.append(f"- {label}: {len(items)} 条")
        for it in items:
            if isinstance(it, dict):
                lines.append(f"  - {json.dumps(it, ensure_ascii=False)}")
            else:
                lines.append(f"  - {it}")
    lines.append("")
    lines.append("## 变更记录")
    for entry in result.get("change_log") or []:
        lines.append(f"- seq {entry.get('seq')} [{entry.get('actor')}] "
                     f"{entry.get('action')} v{entry.get('version', '')}")
    lines.append("")
    lines.append(f"运行时: schema={result.get('runtime', {}).get('schema_version')} "
                 f"script={result.get('runtime', {}).get('script_version')} "
                 f"digest={result.get('runtime', {}).get('input_digest', '')[:12]}…")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Subcommand implementations
# --------------------------------------------------------------------------- #

def cmd_discover(args):
    ensure_dir(args.work)
    ensure_dir(args.output)
    texts_dir = os.path.join(args.work, "texts")
    ensure_dir(texts_dir)

    case, case_err = read_json(os.path.join(args.input, "case.json"))
    task_text, task_err = read_text(os.path.join(args.input, "task.md"))

    errors = []
    degradations = []
    has_case = case is not None
    if case_err and os.path.isfile(os.path.join(args.input, "case.json")):
        errors.append({"source": "case.json", "error": case_err})
    if task_err and os.path.isfile(os.path.join(args.input, "task.md")):
        degradations.append({"source": "task.md", "error": task_err})

    materials = iter_material_files(args.input)
    files = []
    segments = []
    terms = {t: [] for t in STAT_TERMS}
    seg_seq = 0

    for mat in materials:
        abs_path = mat["abs"]
        rel = mat["rel"]
        ext = mat["ext"]
        size = os.path.getsize(abs_path)
        if size > 5 * 1024 * 1024:
            degradations.append({"source": rel, "error": "file_too_large_skipped"})
            files.append({"rel": rel, "kind": ext, "status": "skipped", "chars": 0})
            continue

        entry = {"rel": rel, "kind": ext, "status": "ok", "chars": 0, "pages": None}
        if ext in PDF_EXTS:
            text, err = extract_pdf_text(abs_path)
            if err == "empty_text_needs_ocr":
                entry["status"] = "needs_ocr"
                degradations.append({"source": rel, "error": "scanned_pdf_empty_text"})
            elif err:
                entry["status"] = "degraded"
                degradations.append({"source": rel, "error": err})
            if text:
                pages = re.split(r"\x0c", text)
                for pi, page_text in enumerate(pages, 1):
                    paras = split_paragraphs(page_text)
                    for k, para in enumerate(paras, 1):
                        seg_seq += 1
                        seg = {
                            "segment_id": f"P{seg_seq:04d}",
                            "source": rel,
                            "page": pi,
                            "para": k,
                            "locator": f"p.{pi} ¶{k}",
                            "text": para,
                            "char_start": 0,
                            "char_end": len(para),
                        }
                        segments.append(seg)
                        _index_terms(terms, para, seg["segment_id"])
                entry["chars"] = len(text)
                entry["pages"] = len(pages)
        elif ext in OFFICE_EXTS:
            text, err = extract_office_text(abs_path, ext)
            if err:
                entry["status"] = "degraded"
                degradations.append({"source": rel, "error": err})
            if text:
                paras = split_paragraphs(text)
                for k, para in enumerate(paras, 1):
                    seg_seq += 1
                    seg = {
                        "segment_id": f"T{seg_seq:04d}",
                        "source": rel,
                        "page": None,
                        "para": k,
                        "locator": f"{rel} ¶{k}",
                        "text": para,
                        "char_start": 0,
                        "char_end": len(para),
                    }
                    segments.append(seg)
                    _index_terms(terms, para, seg["segment_id"])
                entry["chars"] = len(text)
        else:
            text, err = read_text(abs_path)
            if err and text is None:
                entry["status"] = "skipped"
                degradations.append({"source": rel, "error": f"binary_or_unreadable:{err}"})
            elif err:
                entry["status"] = "degraded"
                degradations.append({"source": rel, "error": err})
            if text:
                paras = split_paragraphs(text)
                for k, para in enumerate(paras, 1):
                    seg_seq += 1
                    seg = {
                        "segment_id": f"T{seg_seq:04d}",
                        "source": rel,
                        "page": None,
                        "para": k,
                        "locator": f"{rel} ¶{k}",
                        "text": para,
                        "char_start": 0,
                        "char_end": len(para),
                    }
                    segments.append(seg)
                    _index_terms(terms, para, seg["segment_id"])
                entry["chars"] = len(text)
        files.append(entry)

    digest_paths = []
    for rel in ("case.json", "task.md"):
        if os.path.isfile(os.path.join(args.input, rel)):
            digest_paths.append(rel)
    digest_paths.extend(f["rel"] for f in files)
    digest = input_digest(args.input, digest_paths)
    case_id = resolve_case_id(case, {})

    manifest = {
        "case_id": case_id,
        "has_case_json": has_case,
        "has_task_md": task_text is not None,
        "material_count": len(files),
        "segment_count": len(segments),
        "input_digest": digest,
        "files": files,
        "errors": errors,
        "degradations": degradations,
        "input_error": (not has_case and len(files) == 0),
    }
    write_json(os.path.join(args.work, "manifest.json"), manifest)
    write_json(os.path.join(args.work, "segments.json"), segments)
    write_json(os.path.join(args.work, "terms.json"), terms)

    state = {
        "phase": "discovered",
        "case_id": case_id,
        "input_digest": digest,
        "started_at_iso": _now_iso(),
    }
    write_json(os.path.join(args.work, "state.json"), state)

    print(
        f"discover: case_id={case_id} materials={len(files)} segments={len(segments)} "
        f"degradations={len(degradations)} errors={len(errors)} "
        f"manifest={os.path.join(args.work, 'manifest.json')}"
    )
    return 0


def _index_terms(terms, text, seg_id):
    low = text.lower()
    for term, ids in terms.items():
        if term in low:
            ids.append(seg_id)


def _now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def cmd_scaffold(args):
    ensure_dir(args.work)
    ensure_dir(args.output)
    case, _err = read_json(os.path.join(args.input, "case.json"))
    manifest, _ = read_json(os.path.join(args.work, "manifest.json"))
    case_id = resolve_case_id(case, manifest)

    result = {}
    contract = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(contract, dict):
        result.update(json.loads(json.dumps(contract)))  # deep copy, verbatim keys
    core = core_skeleton(case_id)
    for key, value in core.items():
        result.setdefault(key, value)

    digest = (manifest or {}).get("input_digest", "")
    result["runtime"]["input_digest"] = digest
    result["change_log"] = [
        {
            "seq": 0, "actor": "script", "action": "scaffold",
            "version": SCRIPT_VERSION, "input_digest": digest,
            "finding_count": 0,
        }
    ]
    if (manifest or {}).get("input_error"):
        result["status"] = "input_error"
    elif (manifest or {}).get("material_count", 0) == 0:
        result["status"] = "insufficient_evidence"

    write_json(os.path.join(args.output, "result.json"), result)
    state = {
        "phase": "scaffolded",
        "case_id": case_id,
        "started_at_iso": _now_iso(),
    }
    write_json(os.path.join(args.work, "state.json"), state)

    print(
        f"scaffold: wrote result.json status={result['status']} findings=0 "
        f"case_id={case_id}"
    )
    return 0


def cmd_finalize(args):
    ensure_dir(args.work)
    ensure_dir(args.output)
    case, _err = read_json(os.path.join(args.input, "case.json"))
    manifest, _ = read_json(os.path.join(args.work, "manifest.json")) or ({}, None)
    manifest = manifest or {}
    segments, _ = read_json(os.path.join(args.work, "segments.json")) or ([], None)
    segments = segments or []
    case_id = resolve_case_id(case, manifest)

    result = {}
    contract = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(contract, dict):
        result.update(json.loads(json.dumps(contract)))
    core = core_skeleton(case_id)
    for key, value in core.items():
        result.setdefault(key, value)

    # Agent-authored non-finding patch (deterministic merge).
    patch, _ = read_json(os.path.join(args.work, "result_patch.json")) or ({}, None)
    patch = patch if isinstance(patch, dict) else {}
    if patch.get("verdict"):
        result["summary"]["verdict"] = str(patch["verdict"])
    if patch.get("result_status") in RESULT_STATUSES:
        result["result_status"] = patch["result_status"]
    for key in ("uncertainty", "pending_human", "limitations"):
        if isinstance(patch.get(key), list):
            result[key] = patch[key]

    # Findings: read, validate, quote-verify, sort, canonical id assignment.
    findings_dir = os.path.join(args.work, "findings")
    errors = list(manifest.get("errors") or [])
    degradations = list(manifest.get("degradations") or [])
    findings = []
    if os.path.isdir(findings_dir):
        for name in sorted(os.listdir(findings_dir)):
            if not name.endswith(".json"):
                continue
            obj, err = read_json(os.path.join(findings_dir, name))
            if obj is None or not isinstance(obj, dict):
                errors.append({"finding_file": name, "error": err})
                continue
            ok, errs = validate_finding(obj)
            quote_ok = verify_quote(obj.get("quote", ""), segments)
            if not quote_ok:
                obj["review_status"] = "needs_review"
                degradations.append({
                    "finding_file": name,
                    "error": "quote_not_verbatim_or_locator_shift",
                })
            if not ok:
                errors.append({"finding_file": name, "errors": errs})
                continue
            if errs:
                degradations.append({"finding_file": name, "notes": errs})
            findings.append(obj)

    findings.sort(key=finding_sort_key)
    findings = assign_ids(findings)

    # Recompute summary.
    counts = {k: 0 for k in ("critical", "major", "minor", "advisory", "pending")}
    counts["total"] = len(findings)
    by_facet = {f: 0 for f in FACETS}
    by_type = {t: 0 for t in FINDING_TYPES}
    for f in findings:
        sev = f.get("severity", "pending")
        counts[sev] = counts.get(sev, 0) + 1
        by_facet[f.get("facet", "")] = by_facet.get(f.get("facet", ""), 0) + 1
        by_type[f.get("finding_type", "")] = by_type.get(f.get("finding_type", ""), 0) + 1

    checked = list(patch.get("checked_facets") or [])
    checked = [f for f in checked if f in FACETS]
    unchecked = [f for f in FACETS if f not in checked]

    result["summary"]["counts"] = counts
    result["summary"]["by_facet"] = by_facet
    result["summary"]["by_type"] = by_type
    result["review_report"]["findings"] = findings
    result["review_report"]["checked_facets"] = checked
    result["review_report"]["unchecked"] = unchecked

    evidence_map = {}
    for f in findings:
        evidence_map[f["id"]] = {
            "locator": f.get("locator", ""),
            "quote": f.get("quote", ""),
            "basis": f.get("basis", ""),
            "finding_type": f.get("finding_type", ""),
            "severity": f.get("severity", ""),
            "review_status": f.get("review_status", ""),
        }
    result["evidence"]["evidence_map"] = evidence_map

    checks = numeric_candidates(segments)
    result["numeric_checks"] = checks

    digest = manifest.get("input_digest", "")
    result["runtime"]["input_digest"] = digest
    result["sources"] = build_sources(manifest)
    if isinstance(patch.get("sources"), list) and patch["sources"]:
        result["sources"] = patch["sources"]
    result["errors"] = errors + [e for e in patch.get("errors", [])
                                 if isinstance(e, (dict, str))]
    result["degradations"] = degradations + [d for d in patch.get("degradations", [])
                                             if isinstance(d, (dict, str))]
    result["change_log"] = compute_change_log(
        digest, findings, len(checks), manifest.get("material_count", 0)
    )

    # Status honesty.
    if manifest.get("input_error"):
        result["status"] = "input_error"
        result["result_status"] = "not_checked"
    elif manifest.get("material_count", 0) == 0:
        result["status"] = "insufficient_evidence"
        result["result_status"] = "not_checked"
    else:
        if patch.get("complete") and len(unchecked) == 0:
            result["status"] = "ok"
        else:
            result["status"] = "partial"
        if result["result_status"] == "not_checked" and len(unchecked) == 0:
            result["result_status"] = _derive_result_status(findings)

    if not result["summary"]["verdict"] or result["summary"]["verdict"] == "unchecked":
        result["summary"]["verdict"] = _derive_verdict(findings, unchecked)

    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.output, "report.md"), render_report(result))

    state = {"phase": "finalized", "case_id": case_id, "ended_at_iso": _now_iso()}
    write_json(os.path.join(args.work, "state.json"), state)

    print(
        f"finalize: findings={len(findings)} candidates={len(checks)} "
        f"status={result['status']} result_status={result['result_status']} "
        f"checked={len(checked)}/{len(FACETS)}"
    )
    return 0


def _derive_result_status(findings):
    if any(f.get("finding_type") == "pending_review" for f in findings):
        return "needs_review"
    if any(f.get("severity") in ("critical", "major", "minor")
           and f.get("finding_type") != "advisory" for f in findings):
        return "issues_found"
    return "pass"


def _derive_verdict(findings, unchecked):
    if unchecked:
        return f"已走查部分检查面，尚有 {len(unchecked)} 面未完成：{', '.join(unchecked)}"
    if not findings:
        return "未发现需修正的缺陷（或材料不足以判断）"
    sev = {f.get("severity") for f in findings}
    if "critical" in sev:
        return "存在影响主要结论的严重缺陷"
    if "major" in sev:
        return "存在需修正的关键缺陷"
    if "minor" in sev:
        return "存在应报告的缺陷，但不改变主要结论"
    return "仅有建议性改进意见"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic discovery/scaffold/finalize pipeline for "
                    "research-methods & statistical review.",
    )
    parser.add_argument("--version", action="version", version=SCRIPT_VERSION)
    sub = parser.add_subparsers(dest="cmd")
    for name, help_text in (
        ("discover", "Scan input and build segments/manifest/terms"),
        ("scaffold", "Write a legal partial result.json skeleton"),
        ("finalize", "Merge findings, run checks, render report.md"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    if args.cmd == "discover":
        return cmd_discover(args)
    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "finalize":
        return cmd_finalize(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
