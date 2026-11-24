#!/usr/bin/env python3
"""Implementation of the research-methods-stats-audit skill helper.

Subcommands:
  discover  scan /input, extract PDF/docx/text, build segments + terms + manifest
  scaffold  write a legal partial result.json skeleton early (echo output_contract)
  finalize  merge findings, run numeric consistency candidates, render report.md
  selftest  self-check on synthetic input (no external data, no randomness)

Pure Python standard library. Ordering is deterministic: files and findings are
processed in sorted order and JSON is written without reordering keys.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree

VERSION = "1.1.0"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

SUPPORTED_TEXT = {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".xml", ".rst"}
SUPPORTED_DOCX = {".docx"}
SUPPORTED_PDF = {".pdf"}
MAX_FILE_BYTES = 2 * 1024 * 1024

FACETS = [
    "research_design", "variable_definition", "statistical_methods",
    "sample_size_power", "results_reporting",
]
FINDING_TYPES = [
    "factual_error", "insufficient_evidence", "normative_issue",
    "advisory", "pending_review",
]
SEVERITIES = ["critical", "major", "minor", "advisory", "pending"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
RESULT_STATUSES = ["通过", "问题", "待复核", "未检查"]

FINDING_REQUIRED = ["id", "facet", "locator", "quote", "severity", "suggestion"]

# Statistical keyword hits used to locate method/results passages.
TERMS = [
    ("p-value", r"\bp\s*[=<>≤≥]\s*0?\.\d+"),
    ("ci", r"\b(90|95|99)\s*%\s*CI\b"),
    ("n-per-group", r"\bn\s*=\s*\d+"),
    ("N-total", r"\bN\s*=\s*\d+"),
    ("sample-size", r"sample size|样本量|样本大小"),
    ("power", r"\bpower\b|把握度|检验效能|statistical power"),
    ("alpha", r"α\s*=\s*0?\.\d+|significance level|显著性水平"),
    ("effect-size", r"effect size|效应量|效应大小"),
    ("t-test", r"\bt[- ]?test\b|t 检验|t检验|student'?s t"),
    ("anova", r"\bANOVA\b|方差分析|analysis of variance"),
    ("chi-square", r"chi[- ]?square|χ\s*2|χ²|卡方|Chi2"),
    ("fisher", r"\bFisher\b"),
    ("wilcoxon", r"Wilcoxon|Mann[- ]Whitney|曼-惠特尼|秩和检验"),
    ("kruskal", r"Kruskal"),
    ("log-rank", r"log[- ]?rank"),
    ("cox", r"\bCox\b|比例风险"),
    ("regression", r"regression|回归"),
    ("logistic", r"logistic|Logistic"),
    ("poisson", r"Poisson|泊松"),
    ("multiplicity", r"Bonferroni|Holm|FDR|Benjamini|多重比较|multiple comparison"),
    ("significance", r"significant|significance|显著"),
    ("randomization", r"randomi[sz]ed|randomly assign|随机"),
    ("blinding", r"blind|盲法|双盲|单盲"),
    ("missing", r"missing|缺失|缺失值"),
    ("dropout", r"drop[- ]?out|失访|剔除|脱落"),
    ("sd", r"\bSD\b|standard deviation|标准差"),
    ("mean", r"\bmean\b|均值|平均数"),
    ("median", r"median|中位数"),
    ("normality", r"normal(ly)? distribut|正态|Shapiro|Kolmogorov"),
    ("consort", r"CONSORT|STROBE|PRISMA|TRIPOD|STARD"),
]


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path):
    data = read_bytes(path)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), "utf-8(replace)"


def read_json_file(path):
    try:
        text, _enc = read_text(path)
        return json.loads(text), None
    except (OSError, ValueError) as exc:  # noqa: BLE001 - degrade, never crash
        return None, "%s: %s" % (type(exc).__name__, exc)


def write_json(obj, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sanitize(name):
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name)


# --------------------------------------------------------------------------- #
# discovery helpers
# --------------------------------------------------------------------------- #
def scan_inputs(input_dir):
    """Return {case_json, task_md, materials} using only real file contents."""
    case_json = None
    task_md = None
    materials = []
    if not os.path.isdir(input_dir):
        return case_json, task_md, materials
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            abs_path = os.path.join(root, fn)
            rel = os.path.relpath(abs_path, input_dir)
            low = fn.lower()
            if low == "case.json" and case_json is None:
                case_json = abs_path
            elif low == "task.md" and task_md is None:
                task_md = abs_path
            else:
                ext = os.path.splitext(low)[1]
                if ext in SUPPORTED_TEXT | SUPPORTED_DOCX | SUPPORTED_PDF:
                    materials.append({"abs": abs_path, "rel": rel.replace("\\", "/")})
    return case_json, task_md, materials


def pdf_to_text(path):
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "tool_missing"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        proc = subprocess.run(
            [exe, "-enc", "UTF-8", path, tmp],
            capture_output=False, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=180,
        )
        if proc.returncode != 0:
            return None, "error:pdftotext_rc=%d" % proc.returncode
        text, _enc = read_text(tmp)
        if not text.strip():
            return None, "needs_ocr"
        return text, "ok"
    except Exception as exc:  # noqa: BLE001
        return None, "error:%s" % exc
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def docx_to_text(path):
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for p in root.iter(ns + "p"):
            line = "".join(t.text or "" for t in p.iter(ns + "t")).strip()
            if line:
                paragraphs.append(line)
        text = "\n".join(paragraphs)
        return (text, "ok") if text.strip() else (None, "needs_ocr")
    except Exception as exc:  # noqa: BLE001
        return None, "error:%s" % exc


def split_paragraphs(text):
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for block in blocks:
        block = block.strip()
        if block:
            out.append(block)
    return out


def build_segments(materials, work_dir):
    """Extract text and build a page/paragraph locator index."""
    segments = []
    texts_dir = os.path.join(work_dir, "texts")
    os.makedirs(texts_dir, exist_ok=True)
    degradations = []
    seg_id = 0

    for mat in sorted(materials, key=lambda m: m["rel"]):
        rel = mat["rel"]
        ext = os.path.splitext(rel)[1].lower()
        try:
            size = os.path.getsize(mat["abs"])
        except OSError:
            size = None
        if size is not None and size > MAX_FILE_BYTES:
            degradations.append(
                {"source": rel, "reason": "file too large (>2MB), skipped"})
            continue

        if ext == ".pdf":
            text, st = pdf_to_text(mat["abs"])
            if st != "ok":
                degradations.append({"source": rel, "reason": st,
                                     "note": "needs_ocr" if st == "needs_ocr" else ""})
                continue
            pages = text.split("\f")
            for pi, page in enumerate(pages, 1):
                for qi, para in enumerate(split_paragraphs(page), 1):
                    seg_id += 1
                    segments.append({
                        "id": "seg-%04d" % seg_id, "source": rel,
                        "page": pi, "para": qi,
                        "locator": "p.%d ¶%d" % (pi, qi), "text": para,
                    })
        elif ext == ".docx":
            text, st = docx_to_text(mat["abs"])
            if st != "ok":
                degradations.append({"source": rel, "reason": st})
                continue
            for qi, para in enumerate(split_paragraphs(text), 1):
                seg_id += 1
                segments.append({
                    "id": "seg-%04d" % seg_id, "source": rel,
                    "page": None, "para": qi, "locator": "¶%d" % qi,
                    "text": para,
                })
        elif ext in SUPPORTED_TEXT:
            text, _enc = read_text(mat["abs"])
            for qi, para in enumerate(split_paragraphs(text), 1):
                seg_id += 1
                segments.append({
                    "id": "seg-%04d" % seg_id, "source": rel,
                    "page": None, "para": qi, "locator": "¶%d" % qi,
                    "text": para,
                })

    # Write raw extracted texts with markers, for verbatim-quote lookup.
    by_source = {}
    for s in segments:
        by_source.setdefault(s["source"], []).append(s)
    for source in sorted(by_source):
        out_path = os.path.join(texts_dir, sanitize(source) + ".txt")
        with open(out_path, "w", encoding="utf-8") as f:
            for s in by_source[source]:
                f.write("---- %s ----\n%s\n\n" % (s["locator"], s["text"]))

    return segments, degradations


def build_terms(segments):
    hits = []
    for s in segments:
        matched = [label for label, rx in TERMS
                   if re.search(rx, s["text"], re.IGNORECASE)]
        if matched:
            hits.append({"seg": s["id"], "source": s["source"],
                         "locator": s["locator"], "terms": matched})
    return hits


# --------------------------------------------------------------------------- #
# numeric consistency candidates (NOT conclusions)
# --------------------------------------------------------------------------- #
P_CMP = re.compile(r"\b[pP]\s*(?:value\s*)?[=<>≤≥]\s*(0?\.\d{1,6})")
CI_RANGE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:,|to|–|—|-)\s*(-?\d+(?:\.\d+)?)")
N_RE = re.compile(r"\bN\s*=\s*(\d{1,7})")
NN_RE = re.compile(r"\bn\s*=\s*(\d{1,7})")
RATIO_HINT = re.compile(r"\b(HR|OR|RR|hazard ratio|odds ratio|risk ratio|relative risk)\b", re.I)
DIFF_HINT = re.compile(r"\b(MD|SMD|mean difference|difference|β|B|coefficient)\b", re.I)
POS_SIG = re.compile(r"\bsignificant(?:ly)?\b", re.I)
NEG_SIG = re.compile(r"\bnot\s+significant|\bnon[- ]significant|\bno\s+significant", re.I)


def _ci_significant(text, null):
    m = CI_RANGE.search(text)
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2))
    if lo > hi:
        lo, hi = hi, lo
    if null == 1:
        return (lo > 1 and hi > 1) or (lo < 1 and hi < 1)
    return (lo > 0 and hi > 0) or (lo < 0 and hi < 0)


def compute_numeric_checks(segments):
    checks = []
    idx = 0
    for s in segments:
        pvals = P_CMP.findall(s["text"])
        has_ci = re.search(r"\d{2}\s*%\s*CI", s["text"], re.I)
        pos = bool(POS_SIG.search(s["text"]))
        neg = bool(NEG_SIG.search(s["text"]))
        for pv in pvals:
            p = float(pv)
            if p <= 0.05 and neg:
                idx += 1
                checks.append({
                    "id": "NC-%03d" % idx, "rule": "p_vs_wording",
                    "locator": s["locator"], "source": s["source"],
                    "detail": "p=%.4g 但同段措辞为“不显著”，请确认是否同一结局" % p,
                    "evidence": {"p": pv, "snippet": s["text"][:240]},
                })
            if p > 0.05 and pos and not neg:
                idx += 1
                checks.append({
                    "id": "NC-%03d" % idx, "rule": "p_vs_wording",
                    "locator": s["locator"], "source": s["source"],
                    "detail": "p=%.4g 但同段措辞为“显著”，请确认是否同一结局" % p,
                    "evidence": {"p": pv, "snippet": s["text"][:240]},
                })
            if has_ci and pvals:
                ratio = bool(RATIO_HINT.search(s["text"]))
                diff = bool(DIFF_HINT.search(s["text"]))
                if ratio != diff:  # exactly one indicator type
                    null = 1 if ratio else 0
                    ci_sig = _ci_significant(s["text"], null)
                    if ci_sig is not None:
                        p_sig = p <= 0.05
                        if p_sig != ci_sig:
                            idx += 1
                            checks.append({
                                "id": "NC-%03d" % idx, "rule": "p_vs_ci",
                                "locator": s["locator"], "source": s["source"],
                                "detail": "p=%.4g 与 95%%CI 结论相反（null=%d），请确认区间与结局" % (p, null),
                                "evidence": {"p": pv, "snippet": s["text"][:240]},
                            })

    # N / n value inconsistencies across the whole material (candidates only).
    for label, rx in (("N-total", N_RE), ("n-per-group", NN_RE)):
        buckets = {}
        for s in segments:
            for m in rx.finditer(s["text"]):
                v = int(m.group(1))
                buckets.setdefault(v, []).append({
                    "locator": s["locator"], "source": s["source"],
                    "snippet": s["text"][max(0, m.start() - 30):m.end() + 40],
                })
        distinct = sorted(buckets)
        if len(distinct) >= 2:
            idx += 1
            checks.append({
                "id": "NC-%03d" % idx, "rule": "n_inconsistency",
                "locator": ", ".join(b["locator"] for b in buckets[distinct[0]][:2]),
                "source": "", "detail":
                    "全材料出现多个 %s 值 %s，可能为总样本量 vs 分组样本量，需人工确认是否矛盾"
                    % (label, distinct),
                "evidence": {"values": distinct,
                             "occurrences": {str(v): buckets[v] for v in distinct}},
            })
    return checks


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_discover(args):
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    case_json, task_md, materials = scan_inputs(args.input)
    errors = []
    if case_json is None:
        errors.append("case.json not found under input dir")
    if task_md is None:
        errors.append("task.md not found under input dir")

    segments, degradations = build_segments(materials, args.work)
    terms = build_terms(segments)

    manifest = {
        "input_dir": args.input,
        "case_json": os.path.relpath(case_json, args.input).replace("\\", "/") if case_json else None,
        "task_md": os.path.relpath(task_md, args.input).replace("\\", "/") if task_md else None,
        "materials": [{"rel": m["rel"], "bytes": os.path.getsize(m["abs"]) if os.path.exists(m["abs"]) else None}
                      for m in sorted(materials, key=lambda m: m["rel"])],
        "n_segments": len(segments),
        "errors": errors,
        "degradations": degradations,
        "generated_at": now_iso(),
    }
    write_json(manifest, os.path.join(args.work, "manifest.json"))
    write_json(segments, os.path.join(args.work, "segments.json"))
    write_json(terms, os.path.join(args.work, "terms.json"))
    print("discover: %d materials, %d segments, %d errors, %d degradations"
          % (len(materials), len(segments), len(errors), len(degradations)))
    for e in errors:
        print("  error: %s" % e)
    for d in degradations:
        print("  degrade: %s (%s)" % (d["source"], d["reason"]))
    return 0


def load_case(input_dir):
    case_json, _task_md, _materials = scan_inputs(input_dir)
    if case_json is None:
        return {"case_id": "unknown", "output_contract": None,
                "error": "case.json missing"}
    obj, err = read_json_file(case_json)
    if err:
        return {"case_id": "unknown", "output_contract": None,
                "error": "case.json unparsable: %s" % err}
    if not isinstance(obj, dict):
        return {"case_id": "unknown", "output_contract": None,
                "error": "case.json is not a JSON object"}
    case_id = obj.get("case_id") or obj.get("id") or "unknown"
    return {"case_id": case_id, "output_contract": obj.get("output_contract"),
            "error": None}


def base_result(case_id, contract, status, error=None):
    result = {"case_id": case_id}
    if isinstance(contract, dict):
        for key, value in contract.items():
            result[key] = value
    result.update({
        "status": status,
        "summary": {
            "verdict": "未检查",
            "overall_assessment": "",
            "counts": {
                "total": 0, "factual_error": 0, "insufficient_evidence": 0,
                "normative_issue": 0, "advisory": 0, "pending_review": 0,
                "critical": 0, "major": 0, "minor": 0,
            },
            "by_facet": {f: 0 for f in FACETS},
            "checked_facets": [],
            "unchecked": list(FACETS),
        },
        "review_report": {"findings": []},
        "result_status": "未检查",
        "evidence": {"evidence_map": {}},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [error] if error else [],
        "degradations": [],
        "change_log": [{"at": now_iso(), "action": "scaffold",
                        "detail": "partial skeleton created"}],
        "runtime": {"started_at": now_iso(), "finished_at": None, "seconds": 0.0},
        "numeric_checks": [],
    })
    return result


def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case = load_case(args.input)
    case_json, _task_md, materials = scan_inputs(args.input)

    if case_json is None and not materials:
        status = "input_error"
    elif not materials:
        status = "insufficient_evidence"
    else:
        status = "partial"

    errors = []
    if case.get("error"):
        errors.append(case["error"])
    if case_json is None:
        errors.append("case.json not found")

    result = base_result(case["case_id"], case["output_contract"], status)
    result["errors"] = errors
    result["limitations"] = []
    if status == "insufficient_evidence":
        result["limitations"].append("input contains no readable material files")
        result["summary"]["verdict"] = "材料不足"
    if status == "input_error":
        result["summary"]["verdict"] = "未检查"
    write_json(result, os.path.join(args.output, "result.json"))
    write_json({"phase": "scaffold", "_t0": time.time()},
               os.path.join(args.work, "state.json"))
    print("scaffold: case_id=%s status=%s -> result.json skeleton"
          % (result["case_id"], status))
    return 0


def load_findings(work_dir):
    fdir = os.path.join(work_dir, "findings")
    findings = []
    if not os.path.isdir(fdir):
        return findings
    for fn in sorted(os.listdir(fdir)):
        if not fn.lower().endswith(".json"):
            continue
        obj, err = read_json_file(os.path.join(fdir, fn))
        if err or not isinstance(obj, dict):
            continue
        findings.append(obj)
    findings.sort(key=lambda f: (str(f.get("facet", "")), str(f.get("id", ""))))
    return findings


def validate_findings(findings):
    errors = []
    for f in findings:
        missing = [k for k in FINDING_REQUIRED if not str(f.get(k, "")).strip()]
        if missing:
            errors.append("finding %s missing required keys: %s"
                          % (f.get("id", "?"), ", ".join(missing)))
            f.setdefault("severity", "pending")
            f.setdefault("review_status", "needs_review")
        f.setdefault("finding_type", "pending_review")
        f.setdefault("check_class", "statistical_reporting")
        f.setdefault("confidence", "medium")
        f.setdefault("review_status", "confirmed")
        f.setdefault("source", "")
        f.setdefault("basis", "")
        f.setdefault("suggestion", "")
    return findings, errors


def build_summary(findings):
    counts = {
        "total": len(findings), "factual_error": 0, "insufficient_evidence": 0,
        "normative_issue": 0, "advisory": 0, "pending_review": 0,
        "critical": 0, "major": 0, "minor": 0,
    }
    by_facet = {f: 0 for f in FACETS}
    for f in findings:
        ft = f.get("finding_type")
        if ft in counts:
            counts[ft] += 1
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        facet = f.get("facet")
        if facet in by_facet:
            by_facet[facet] += 1
    return {"counts": counts, "by_facet": by_facet}


def derive_result_status(findings, status):
    if not findings:
        return "通过" if status == "ok" else "未检查"
    sev = {f.get("severity") for f in findings}
    ft = {f.get("finding_type") for f in findings}
    if "critical" in sev or "major" in sev:
        return "问题"
    if "pending_review" in ft or "pending" in sev:
        return "待复核"
    if ft & {"factual_error", "insufficient_evidence", "normative_issue"}:
        return "问题"
    return "通过"  # only advisory suggestions remain


def build_evidence_map(findings):
    return {f["id"]: {"source": f.get("source", ""),
                      "locator": f.get("locator", ""),
                      "quote": f.get("quote", "")} for f in findings}


def build_sources(manifest):
    src = []
    for key in ("case_json", "task_md"):
        if manifest.get(key):
            src.append(manifest[key])
    for m in manifest.get("materials", []):
        src.append(m["rel"])
    return sorted(set(src))


def cmd_finalize(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    if not os.path.exists(result_path):
        cmd_scaffold(args)
    result, err = read_json_file(result_path)
    if err or not isinstance(result, dict):
        result = base_result("unknown", None, "partial", "result.json unreadable, rebuilt")

    segments, _ = read_json_file(os.path.join(args.work, "segments.json"))
    if not isinstance(segments, list):
        segments = []
    manifest, _ = read_json_file(os.path.join(args.work, "manifest.json"))
    if not isinstance(manifest, dict):
        manifest = {}
    meta, _ = read_json_file(os.path.join(args.work, "meta.json"))
    if not isinstance(meta, dict):
        meta = {}

    findings = load_findings(args.work)
    findings, ferrors = validate_findings(findings)
    numeric = compute_numeric_checks(segments)

    summary = build_summary(findings)
    summary["verdict"] = meta.get("overall_verdict") or "未检查"
    summary["overall_assessment"] = meta.get("overall_assessment") or ""
    summary["checked_facets"] = meta.get("checked_facets", [])
    summary["unchecked"] = meta.get("unchecked",
                                    [f for f in FACETS if f not in summary["checked_facets"]])

    result["summary"] = summary
    result["review_report"] = {"findings": findings}
    result["result_status"] = (meta.get("result_status")
                               or derive_result_status(findings, result.get("status", "partial")))
    result["evidence"] = {"evidence_map": build_evidence_map(findings)}
    result["sources"] = meta.get("sources") or build_sources(manifest)
    result["uncertainty"] = meta.get("uncertainty", [])
    result["pending_human"] = meta.get("pending_human", [])
    result["numeric_checks"] = numeric

    prev_errors = result.get("errors") or []
    prev_lim = result.get("limitations") or []
    merged_errors = list(prev_errors)
    for e in (list(manifest.get("errors", [])) + list(meta.get("errors", [])) + ferrors):
        if e not in merged_errors:
            merged_errors.append(e)
    merged_lim = list(prev_lim)
    for item in (meta.get("limitations") or []):
        if item not in merged_lim:
            merged_lim.append(item)
    degradations = list(manifest.get("degradations", [])) + list(meta.get("degradations", []))
    result["errors"] = merged_errors
    result["limitations"] = merged_lim
    result["degradations"] = degradations

    if not findings and not meta.get("status"):
        result["status"] = result.get("status") or "partial"
    elif meta.get("status"):
        result["status"] = meta["status"]
    elif findings:
        result["status"] = "ok" if meta.get("status") == "ok" else "partial"
    if result.get("status") not in STATUSES:
        result["status"] = "partial"

    state, _ = read_json_file(os.path.join(args.work, "state.json"))
    t0 = state.get("_t0") if isinstance(state, dict) else None
    runtime = result.get("runtime") or {}
    runtime["finished_at"] = now_iso()
    runtime["seconds"] = round(time.time() - t0, 1) if t0 else runtime.get("seconds", 0.0)
    result["runtime"] = runtime

    log = result.get("change_log") or []
    log.append({"at": now_iso(), "action": "finalize",
                "detail": "merged %d findings, %d numeric candidates"
                          % (len(findings), len(numeric))})
    result["change_log"] = log

    write_json(result, result_path)
    render_report(result, os.path.join(args.output, "report.md"))
    print("finalize: %d findings, %d numeric candidates, status=%s, result_status=%s"
          % (len(findings), len(numeric), result["status"], result["result_status"]))
    return 0


def render_report(result, path):
    lines = []
    lines.append("# 研究方法与统计审查报告\n")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- 结论: `%s`" % (result.get("summary", {}).get("verdict", "")))
    lines.append("- result_status: `%s`" % result.get("result_status"))
    lines.append("")

    counts = result.get("summary", {}).get("counts", {})
    lines.append("## 摘要\n")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for k, v in counts.items():
        lines.append("| %s | %s |" % (k, v))
    lines.append("")

    if result.get("summary", {}).get("overall_assessment"):
        lines.append("**总体评价**：%s\n"
                     % result["summary"]["overall_assessment"])

    lines.append("## 逐条发现\n")
    findings = result.get("review_report", {}).get("findings", [])
    if not findings:
        lines.append("（无已确认发现）\n")
    for f in findings:
        lines.append("### %s — `%s`（%s / %s）\n"
                     % (f.get("id"), f.get("facet"), f.get("finding_type"),
                        f.get("severity")))
        lines.append("- 位置：`%s`（来源 `%s`）" % (f.get("locator"), f.get("source", "")))
        lines.append("- 逐字摘录：")
        lines.append("")
        lines.append("> %s" % f.get("quote", ""))
        lines.append("")
        if f.get("basis"):
            lines.append("- 依据：%s" % f["basis"])
        if f.get("suggestion"):
            lines.append("- 修改建议：%s" % f["suggestion"])
        lines.append("")

    checks = result.get("numeric_checks", [])
    if checks:
        lines.append("## 数值一致性候选（待人工确认）\n")
        for c in checks:
            lines.append("- `%s` %s: %s" % (c.get("id"), c.get("rule"), c.get("detail")))
        lines.append("")

    for title, key in (("不确定性", "uncertainty"), ("待人工复核", "pending_human"),
                       ("局限", "limitations"), ("错误", "errors"),
                       ("降级记录", "degradations")):
        items = result.get(key, [])
        if items:
            lines.append("## %s\n" % title)
            for it in items:
                lines.append("- %s" % (it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)))
            lines.append("")

    lines.append("## 证据映射\n")
    emap = result.get("evidence", {}).get("evidence_map", {})
    if emap:
        lines.append("| id | source | locator | quote |")
        lines.append("|---|---|---|---|")
        for fid, ev in emap.items():
            lines.append("| %s | %s | %s | %s |"
                         % (fid, ev.get("source", ""), ev.get("locator", ""),
                            (ev.get("quote", "") or "")[:120]))
    else:
        lines.append("（无）")
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def _mk_case(base, case_obj=None, bad_json=False, with_material=True):
    inp = os.path.join(base, "input")
    outp = os.path.join(base, "output")
    work = os.path.join(base, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(outp, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    if bad_json:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json")
    elif case_obj is not None:
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case_obj, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# selftest task\n")
    if with_material:
        os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
        with open(os.path.join(inp, "materials", "paper.txt"), "w", encoding="utf-8") as f:
            f.write("Methods. N = 100 patients were randomized.\n\n"
                    "Results. The difference was significant (p = 0.04, 95% CI 1.2 to 3.1).\n\n"
                    "Conclusion. The effect was not significant.\n")
    return argparse.Namespace(input=inp, output=outp, work=work)


def cmd_selftest(_args):
    checks = []
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        # 1. normal case with material
        a1 = _mk_case(os.path.join(tmp, "c1"), {"case_id": "selftest-1"})
        assert cmd_discover(a1) == 0
        assert cmd_scaffold(a1) == 0
        assert cmd_finalize(a1) == 0
        r1, err = read_json_file(os.path.join(a1.output, "result.json"))
        assert err is None, "result.json not parseable"
        assert r1["case_id"] == "selftest-1"
        assert r1["status"] == "partial"
        assert r1["result_status"] == "未检查"  # no findings and review not finalized
        assert os.path.exists(os.path.join(a1.output, "report.md"))
        assert isinstance(r1["numeric_checks"], list)
        checks.append("discover+scaffold+finalize ok")

        # 2. output_contract echo
        a2 = _mk_case(os.path.join(tmp, "c2"),
                      {"case_id": "selftest-2",
                       "output_contract": {"a": 1, "b": None, "c": [1, 2]}})
        assert cmd_scaffold(a2) == 0
        assert cmd_finalize(a2) == 0
        r2, _ = read_json_file(os.path.join(a2.output, "result.json"))
        assert r2["a"] == 1 and r2["b"] is None and r2["c"] == [1, 2]
        checks.append("output_contract echo ok")

        # 3. empty material -> insufficient_evidence
        a3 = _mk_case(os.path.join(tmp, "c3"), {"case_id": "selftest-3"},
                      with_material=False)
        assert cmd_discover(a3) == 0
        assert cmd_scaffold(a3) == 0
        r3, _ = read_json_file(os.path.join(a3.output, "result.json"))
        assert r3["status"] == "insufficient_evidence"
        checks.append("empty-material degradation ok")

        # 4. bad JSON must not crash
        a4 = _mk_case(os.path.join(tmp, "c4"), bad_json=True, with_material=False)
        assert cmd_scaffold(a4) == 0
        r4, _ = read_json_file(os.path.join(a4.output, "result.json"))
        assert r4["case_id"] == "unknown" and r4["errors"]
        checks.append("bad-json tolerance ok")

        # 5. finding validation: a well-formed finding survives finalize
        a5 = _mk_case(os.path.join(tmp, "c5"), {"case_id": "selftest-5"})
        assert cmd_discover(a5) == 0
        assert cmd_scaffold(a5) == 0
        fdir = os.path.join(a5.work, "findings")
        os.makedirs(fdir, exist_ok=True)
        finding = {
            "id": "RR-01", "facet": "results_reporting",
            "check_class": "statistical_reporting",
            "finding_type": "factual_error", "severity": "critical",
            "confidence": "high", "review_status": "confirmed",
            "locator": "¶3", "source": "materials/paper.txt",
            "quote": "The effect was not significant.",
            "basis": "p=0.04 与结论措辞相反", "suggestion": "核对结论措辞",
        }
        with open(os.path.join(fdir, "RR-01.json"), "w", encoding="utf-8") as f:
            json.dump(finding, f, ensure_ascii=False)
        assert cmd_finalize(a5) == 0
        r5, _ = read_json_file(os.path.join(a5.output, "result.json"))
        assert len(r5["review_report"]["findings"]) == 1
        assert r5["result_status"] == "问题"
        checks.append("finding validation ok")

        # 6. numeric candidates fire on a deliberately conflicting text
        a6 = _mk_case(os.path.join(tmp, "c6"), {"case_id": "selftest-6"})
        with open(os.path.join(a6.input, "materials", "paper.txt"), "w", encoding="utf-8") as f:
            f.write("Methods. N = 100 patients.\n\n"
                    "Results. The difference was not significant (p = 0.02).\n\n"
                    "Discussion. N = 120 patients.\n")
        assert cmd_discover(a6) == 0
        assert cmd_scaffold(a6) == 0
        assert cmd_finalize(a6) == 0
        r6, _ = read_json_file(os.path.join(a6.output, "result.json"))
        rules = {c["rule"] for c in r6["numeric_checks"]}
        assert "p_vs_wording" in rules and "n_inconsistency" in rules
        checks.append("numeric candidates ok")

        print("selftest ok: " + "; ".join(checks))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="main.py", add_help=False)
    parser.add_argument("command", choices=["discover", "scaffold", "finalize", "selftest"])
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)
    dispatch = {
        "discover": cmd_discover,
        "scaffold": cmd_scaffold,
        "finalize": cmd_finalize,
        "selftest": cmd_selftest,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
