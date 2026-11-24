"""core_impl.py - research-methods stats audit pipeline (Python stdlib only).

Subcommands (dispatched by scripts/main.py):
  discover   scan input, extract page/paragraph text, build segments/terms and manifest
  scaffold   write a minimal legal result.json skeleton (partial) and state.json
  finalize   merge findings, validate schema + verbatim quote + locator, run numeric
             candidate checks, refresh result.json and render report.md
  selftest   self-check the package in a transient temp dir (no shipped test data)

Design rules:
  - deterministic: no random state, stable ordering, sorted JSON keys
  - degrade, never fabricate: missing files/software/encoding are recorded as
    errors/degradations, never as invented facts
  - output_contract fields are mirrored verbatim into result.json top level
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
SEGMENTS_REF = "/app/work/segments.json"

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

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
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "advisory": 3, "pending": 4}

REVIEW_STATUSES = ["checked", "needs_review"]
CONFIDENCES = ["high", "medium", "low"]

MATERIAL_EXTS = {".pdf", ".txt", ".md", ".csv", ".json", ".docx", ".xml", ".html", ".rst"}

# Statistical keywords used to locate methods/results passages.
STAT_TERMS = [
    "p value", "p-value", "p =", "p=", "p <", "p>", "confidence interval", "95% CI",
    "t-test", "t test", "chi-square", "chi2", "chi-squared", "ANOVA", "Mann-Whitney",
    "Wilcoxon", "Kruskal", "Fisher exact", "logistic regression", "Cox regression",
    "Kaplan-Meier", "log-rank", "hazard ratio", "odds ratio", "risk ratio",
    "effect size", "power", "sample size", "standard deviation", "standard error",
    "mean", "median", "IQR", "interquartile", "Bonferroni", "Holm", "FDR",
    "multiple comparison", "mixed model", "mixed-effects", "GEE", "degrees of freedom",
    "df =", "significance", "significant", "not significant", "null hypothesis",
    "primary endpoint", "primary outcome", "randomized", "randomised", "blinding",
    "blinded", "placebo", "control group", "intervention", "missing data",
    "intention-to-treat", "per-protocol", "sensitivity analysis", "covariate",
    "confounder", "interaction", "adjusted", "unadjusted", "effect estimate",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_sec() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# file reading helpers
# ---------------------------------------------------------------------------
def load_json(path: str):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def read_text_robust(path: str, max_bytes: int = 20_000_000):
    """Return (text, encoding) or (None, reason)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "unreadable: %s" % exc
    if size > max_bytes:
        return None, "exceeds_size_limit"
    try:
        data = open(path, "rb").read()
    except OSError as exc:
        return None, "unreadable: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def extract_pdf_text(path: str, work_dir: str):
    """Return (text, errors). Uses pdftotext; empty text => scanned candidate."""
    if shutil_which("pdftotext") is None:
        return None, ["pdftotext_missing"]
    out_txt = os.path.join(work_dir, "_pdftotext_out.txt")
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", path, out_txt],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001
        return None, ["pdftotext_error: %s" % str(exc)[:200]]
    if proc.returncode != 0:
        return None, ["pdftotext_failed: %s" % (proc.stderr or "").strip()[:200]]
    text, enc = read_text_robust(out_txt)
    if text is None:
        return None, ["pdftotext_output_unreadable: %s" % enc]
    if os.path.exists(out_txt):
        try:
            os.remove(out_txt)
        except OSError:
            pass
    return text, []


def shutil_which(name: str):
    import shutil
    return shutil.which(name)


def extract_docx_text(path: str):
    """Return (text, errors). Minimal stdlib docx paragraph extraction."""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        return None, ["docx_unzip_failed: %s" % str(exc)[:200]]
    parts = re.split(r"</w:p>", xml)
    paragraphs = []
    for part in parts:
        txt = re.sub(r"<[^>]+>", "", part)
        txt = (
            txt.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
        )
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt:
            paragraphs.append(txt)
    return "\n\n".join(paragraphs), []


def split_paragraphs(text: str):
    """Split on blank lines into cleaned paragraphs."""
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for block in blocks:
        b = re.sub(r"[ \t\r]+", " ", block).strip()
        if b:
            out.append(b)
    return out


def make_segment(seg_idx: int, file: str, text: str, page=None, para=1) -> dict:
    locator = "p.%d ¶%d" % (page, para) if page else "¶%d" % para
    return {
        "id": "s%05d" % seg_idx,
        "file": file,
        "locator": locator,
        "page": page,
        "para": para,
        "text": text,
    }


# ---------------------------------------------------------------------------
# term scanning
# ---------------------------------------------------------------------------
def scan_terms(segments):
    hits = []
    for term in STAT_TERMS:
        low = term.lower()
        for seg in segments:
            txt_low = seg["text"].lower()
            pos = txt_low.find(low)
            if pos < 0:
                continue
            start = max(0, pos - 40)
            end = min(len(seg["text"]), pos + len(term) + 60)
            hits.append(
                {
                    "term": term,
                    "file": seg["file"],
                    "locator": seg["locator"],
                    "context": seg["text"][start:end],
                }
            )
    hits.sort(key=lambda h: (h["file"], h["locator"], h["term"]))
    return hits


# ---------------------------------------------------------------------------
# case metadata
# ---------------------------------------------------------------------------
def read_case_meta(input_dir: str):
    errors = []
    degradations = []
    case_id = None
    output_contract = None
    has_case = False
    case_path = os.path.join(input_dir, "case.json")
    if os.path.exists(case_path):
        try:
            case = load_json(case_path)
            has_case = True
            case_id = (
                case.get("case_id")
                or case.get("id")
                or case.get("caseId")
                or os.path.basename(input_dir)
            )
            output_contract = case.get("output_contract")
            if output_contract is not None and not isinstance(output_contract, dict):
                degradations.append("output_contract is not an object; ignored")
                output_contract = None
        except Exception as exc:  # noqa: BLE001
            errors.append("case.json unreadable: %s" % str(exc)[:200])
            case_id = None
    else:
        degradations.append("case.json missing")
    return case_id, output_contract, has_case, errors, degradations


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------
def cmd_discover(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    case_id, output_contract, has_case, errors, degradations = read_case_meta(input_dir)

    task_text = None
    task_path = os.path.join(input_dir, "task.md")
    if os.path.exists(task_path):
        text, enc = read_text_robust(task_path)
        if text is None:
            degradations.append("task.md unreadable: %s" % enc)
        else:
            task_text = text
    else:
        degradations.append("task.md missing")

    materials = []
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            if fname in ("case.json", "task.md"):
                continue
            p = os.path.join(root, fname)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            ext = os.path.splitext(fname)[1].lower()
            if ext in MATERIAL_EXTS:
                materials.append((rel, p, ext))

    segments = []
    materials_meta = []
    seg_idx = 0
    for rel, p, ext in materials:
        item = {"file": rel, "path": p, "ext": ext, "status": "ok", "notes": []}
        segs = []
        if ext == ".pdf":
            text, errs = extract_pdf_text(p, work_dir)
            if text is None:
                item["status"] = "needs_ocr"
                item["notes"] = errs
                degradations += ["%s: %s" % (rel, e) for e in errs]
            elif not text.strip():
                item["status"] = "needs_ocr"
                item["notes"] = ["empty_text"]
                degradations.append("%s: empty PDF text (scanned candidate)" % rel)
            else:
                pages = text.split("\f")
                for pi, page in enumerate(pages, 1):
                    for k, para in enumerate(split_paragraphs(page), 1):
                        segs.append(make_segment(seg_idx, rel, para, page=pi, para=k))
                        seg_idx += 1
        elif ext == ".docx":
            text, errs = extract_docx_text(p)
            if text is None:
                item["status"] = "error"
                item["notes"] = errs
                degradations += ["%s: %s" % (rel, e) for e in errs]
            else:
                for k, para in enumerate(split_paragraphs(text), 1):
                    segs.append(make_segment(seg_idx, rel, para, para=k))
                    seg_idx += 1
        else:
            text, enc = read_text_robust(p)
            if text is None:
                item["status"] = "error"
                item["notes"] = [enc]
                degradations.append("%s: %s" % (rel, enc))
            else:
                item["notes"] = ["encoding: %s" % enc]
                for k, para in enumerate(split_paragraphs(text), 1):
                    segs.append(make_segment(seg_idx, rel, para, para=k))
                    seg_idx += 1
        if segs:
            item["segment_count"] = len(segs)
        segments.extend(segs)
        materials_meta.append(item)

    manifest = {
        "case_id": case_id,
        "has_case": has_case,
        "has_output_contract": output_contract is not None,
        "output_contract": output_contract,
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "task_md": bool(task_text),
        "materials": materials_meta,
        "material_count": len(materials),
        "segment_count": len(segments),
        "errors": errors,
        "degradations": degradations,
        "generated_at": now_iso(),
    }
    write_json(os.path.join(work_dir, "manifest.json"), manifest)
    write_json(os.path.join(work_dir, "segments.json"), {"segments": segments})
    write_json(os.path.join(work_dir, "terms.json"), {"hits": scan_terms(segments)})

    meta = initial_meta(case_id, has_case, output_contract, errors, degradations)
    write_json(os.path.join(work_dir, "meta.json"), meta)

    print(
        "discover: case_id=%s has_case=%s materials=%d segments=%d errors=%d degradations=%d"
        % (case_id, has_case, len(materials), len(segments), len(errors), len(degradations))
    )
    return 0


def initial_meta(case_id, has_case, output_contract, errors, degradations):
    return {
        "case_id": case_id,
        "has_case": has_case,
        "has_output_contract": output_contract is not None,
        "status": STATUS_PARTIAL,
        "overall_verdict": "unchecked",
        "overall_assessment": "",
        "checked_facets": [],
        "unchecked_facets": list(FACETS),
        "covered_check_classes": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "updated_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------
def empty_counter():
    return {"total": 0, "critical": 0, "major": 0, "minor": 0, "advisory": 0, "pending": 0}


def mirror_output_contract(result: dict, output_contract):
    if isinstance(output_contract, dict):
        for key, value in output_contract.items():
            if key not in result:
                result[key] = value
    return result


def build_skeleton(case_id, output_contract, has_case, has_materials, errors, degradations):
    if not has_case and not has_materials:
        status = STATUS_INPUT_ERROR
    elif not has_materials:
        status = STATUS_INSUFFICIENT
    else:
        status = STATUS_PARTIAL

    skeleton = {
        "case_id": case_id,
        "status": status,
        "summary": {
            "verdict": "unchecked",
            "counts": empty_counter(),
            "facet_counts": {f: 0 for f in FACETS},
            "check_class_counts": {c: 0 for c in CHECK_CLASSES},
            "finding_type_counts": {t: 0 for t in FINDING_TYPES},
        },
        "review_report": {"findings": [], "coverage": {}},
        "result_status": {
            "overall": "未检查",
            "by_facet": {f: "未检查" for f in FACETS},
            "unchecked": list(FACETS),
        },
        "evidence": {"evidence_map": [], "segments_ref": ""},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "change_log": [
            {"at": now_iso(), "action": "scaffold", "detail": "partial skeleton written"}
        ],
        "runtime": {"start_epoch": epoch_sec(), "start_at": now_iso(), "end_epoch": None, "end_at": None},
        "numeric_checks": [],
    }
    skeleton = mirror_output_contract(skeleton, output_contract)
    return skeleton


def cmd_scaffold(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    manifest_path = os.path.join(work_dir, "manifest.json")
    manifest = None
    if os.path.exists(manifest_path):
        try:
            manifest = load_json(manifest_path)
        except Exception:  # noqa: BLE001
            manifest = None

    if manifest is not None:
        case_id = manifest.get("case_id")
        output_contract = manifest.get("output_contract")
        has_case = manifest.get("has_case", False)
        errors = list(manifest.get("errors", []))
        degradations = list(manifest.get("degradations", []))
        has_materials = manifest.get("material_count", 0) > 0
    else:
        case_id, output_contract, has_case, errors, degradations = read_case_meta(input_dir)
        has_materials = any(
            os.path.splitext(f)[1].lower() in MATERIAL_EXTS
            for _, _, files in os.walk(input_dir)
            for f in files
            if f not in ("case.json", "task.md")
        )

    result = build_skeleton(case_id, output_contract, has_case, has_materials, errors, degradations)
    result["evidence"]["segments_ref"] = SEGMENTS_REF

    write_json(os.path.join(output_dir, "result.json"), result)

    state = {
        "phase": "scaffold",
        "case_id": case_id,
        "checked_facets": [],
        "unchecked_facets": list(FACETS),
        "covered_check_classes": [],
        "findings_written": 0,
        "updated_at": now_iso(),
    }
    write_json(os.path.join(work_dir, "state.json"), state)

    print("scaffold: status=%s case_id=%s" % (result["status"], case_id))
    return 0


# ---------------------------------------------------------------------------
# findings validation
# ---------------------------------------------------------------------------
def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def validate_finding(finding: dict, segments, seg_by_locator):
    errs = []
    for req in ("id", "facet", "locator", "quote", "severity", "suggestion"):
        if not finding.get(req):
            errs.append("missing_%s" % req)
    if finding.get("facet") not in FACETS:
        errs.append("bad_facet")
    if finding.get("finding_type") not in FINDING_TYPES:
        errs.append("bad_finding_type")
    if finding.get("check_class") not in CHECK_CLASSES:
        errs.append("bad_check_class")
    if finding.get("severity") not in SEVERITIES:
        errs.append("bad_severity")
    if finding.get("review_status") not in REVIEW_STATUSES:
        errs.append("bad_review_status")

    locator = finding.get("locator", "")
    loc_ok = locator in seg_by_locator

    quote = finding.get("quote", "")
    q_ok = False
    nq = normalize_ws(quote)
    if nq:
        for seg in segments:
            if nq and nq in normalize_ws(seg["text"]):
                q_ok = True
                break

    return {"locator_resolves": bool(loc_ok), "quote_verbatim": bool(q_ok), "errors": errs}


def load_findings(work_dir: str):
    fdir = os.path.join(work_dir, "findings")
    findings = []
    load_errors = []
    if os.path.isdir(fdir):
        for fname in sorted(os.listdir(fdir)):
            if not fname.endswith(".json"):
                continue
            try:
                obj = load_json(os.path.join(fdir, fname))
                if isinstance(obj, dict):
                    findings.append(obj)
                else:
                    load_errors.append("%s: not an object" % fname)
            except Exception as exc:  # noqa: BLE001
                load_errors.append("%s: %s" % (fname, str(exc)[:200]))
    findings.sort(key=lambda f: (f.get("facet", ""), SEVERITY_RANK.get(f.get("severity"), 9), str(f.get("id", ""))))
    return findings, load_errors


# ---------------------------------------------------------------------------
# numeric candidate checks
# ---------------------------------------------------------------------------
P_RE = re.compile(r"\bp\s*(?:value)?\s*(=|==|<|>|<=|>=|≤|≥|≈)\s*(0?\.\d+|[01](?:\.0+)?)", re.IGNORECASE)

SIG_NEG = [
    "not significant", "non-significant", "non significant", "no significant",
    "不显著", "无统计学意义", "差异无统计学意义", "无统计学差异", "无显著性",
]
SIG_POS = [
    "significant", "significantly", "显著", "有统计学意义", "差异有统计学意义",
    "统计学差异", "statistically significant",
]

CI_RE = re.compile(r"\bCI[^\d]{0,30}(-?\d+(?:\.\d+)?)\s*(?:to|,|–|—|-)\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
RATIO_HINT = re.compile(r"\b(odds ratio|hazard ratio|risk ratio|OR|HR|RR)\b", re.IGNORECASE)
N_RE = re.compile(r"\b([Nn])\s*=\s*(\d+)")


def p_is_significant(op, val):
    if op in ("<", "<=", "≤"):
        return val <= 0.05
    if op in ("=", "==", "≈"):
        return val < 0.05
    if op in (">", ">=", "≥"):
        return None if val < 0.05 else False
    return val < 0.05


def wording_signal(text):
    low = text.lower()
    for neg in SIG_NEG:
        if neg.lower() in low:
            return "neg"
    for pos in SIG_POS:
        if pos.lower() in low:
            return "pos"
    return None


def run_numeric_checks(segments):
    candidates = []
    seen = set()

    def add(kind, locators, detail):
        key = (kind, tuple(sorted(set(locators))))
        if key in seen:
            return
        seen.add(key)
        candidates.append({"type": kind, "locators": list(key[1]), "detail": detail, "status": "candidate"})

    # 1. p-value vs significance wording in the same segment
    for seg in segments:
        text = seg["text"]
        wsig = wording_signal(text)
        if wsig is None:
            continue
        for m in P_RE.finditer(text):
            op = m.group(1)
            val = float(m.group(2))
            sig = p_is_significant(op, val)
            if sig is None:
                continue
            if (sig and wsig == "neg") or (not sig and wsig == "pos"):
                add(
                    "p_vs_wording",
                    [seg["locator"]],
                    "p %s %s vs wording '%s'" % (op, m.group(2), wsig),
                )

    # 2. p-value vs CI null straddling in the same segment
    for seg in segments:
        text = seg["text"]
        ci = CI_RE.search(text)
        if ci is None:
            continue
        lo, hi = float(ci.group(1)), float(ci.group(2))
        if lo > hi:
            lo, hi = hi, lo
        null = 1.0 if RATIO_HINT.search(text) else 0.0
        straddles = lo < null < hi
        for m in P_RE.finditer(text):
            sig = p_is_significant(m.group(1), float(m.group(2)))
            if sig is None:
                continue
            if (sig and straddles) or (not sig and not straddles):
                add(
                    "p_vs_ci",
                    [seg["locator"]],
                    "CI %.3g..%.3g (null %.3g) vs p %s %s" % (lo, hi, null, m.group(1), m.group(2)),
                )

    # 3. N/n value inconsistency across the whole material
    nvals = {}
    for seg in segments:
        for m in N_RE.finditer(seg["text"]):
            letter = m.group(1)
            value = m.group(2)
            nvals.setdefault(letter, {}).setdefault(value, []).append(seg["locator"])
    for letter in sorted(nvals):
        if len(nvals[letter]) > 1:
            detail = "; ".join(
                "%s=%s @ %s" % (letter, v, ", ".join(sorted(set(locs))))
                for v, locs in sorted(nvals[letter].items())
            )
            locators = sorted({l for locs in nvals[letter].values() for l in locs})
            add("N_inconsistency", locators, detail)

    candidates.sort(key=lambda c: (c["type"], tuple(c["locators"])))
    return candidates


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------
def cmd_finalize(input_dir, output_dir, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    manifest_path = os.path.join(work_dir, "manifest.json")
    manifest = None
    if os.path.exists(manifest_path):
        try:
            manifest = load_json(manifest_path)
        except Exception:  # noqa: BLE001
            manifest = None

    segments = []
    seg_path = os.path.join(work_dir, "segments.json")
    if os.path.exists(seg_path):
        try:
            segments = load_json(seg_path).get("segments", [])
        except Exception:  # noqa: BLE001
            segments = []
    seg_by_locator = {s["locator"]: s for s in segments}

    result_path = os.path.join(output_dir, "result.json")
    result = None
    if os.path.exists(result_path):
        try:
            result = load_json(result_path)
        except Exception:  # noqa: BLE001
            result = None
    if result is None:
        if manifest is not None:
            result = build_skeleton(
                manifest.get("case_id"),
                manifest.get("output_contract"),
                manifest.get("has_case", False),
                manifest.get("material_count", 0) > 0,
                manifest.get("errors", []),
                manifest.get("degradations", []),
            )
        else:
            case_id, oc, has_case, errs, degs = read_case_meta(input_dir)
            result = build_skeleton(case_id, oc, has_case, True, errs, degs)
        result["evidence"]["segments_ref"] = SEGMENTS_REF

    findings, load_errors = load_findings(work_dir)

    for f in findings:
        f["validation"] = validate_finding(f, segments, seg_by_locator)

    # numeric candidate checks (recomputed deterministically each finalize)
    result["numeric_checks"] = run_numeric_checks(segments)

    # counts
    counts = empty_counter()
    facet_counts = {f: 0 for f in FACETS}
    class_counts = {c: 0 for c in CHECK_CLASSES}
    type_counts = {t: 0 for t in FINDING_TYPES}
    for f in findings:
        counts["total"] += 1
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        if f.get("facet") in facet_counts:
            facet_counts[f["facet"]] += 1
        if f.get("check_class") in class_counts:
            class_counts[f["check_class"]] += 1
        if f.get("finding_type") in type_counts:
            type_counts[f["finding_type"]] += 1

    checked_facets = [f for f in FACETS if facet_counts.get(f, 0) > 0]
    unchecked = [f for f in FACETS if facet_counts.get(f, 0) == 0]
    covered_classes = [c for c in CHECK_CLASSES if class_counts.get(c, 0) > 0]

    result["review_report"]["findings"] = findings
    result["review_report"]["coverage"] = {
        "checked_facets": checked_facets,
        "unchecked_facets": unchecked,
        "covered_check_classes": covered_classes,
        "uncovered_check_classes": [c for c in CHECK_CLASSES if c not in covered_classes],
    }
    result["summary"] = {
        "verdict": result.get("summary", {}).get("verdict", "unchecked"),
        "counts": counts,
        "facet_counts": facet_counts,
        "check_class_counts": class_counts,
        "finding_type_counts": type_counts,
    }
    by_facet = {}
    for f in FACETS:
        by_facet[f] = "已检查" if f in checked_facets else "未检查"
    result["result_status"] = {
        "overall": "问题" if counts["total"] > 0 else "未检查",
        "by_facet": by_facet,
        "unchecked": unchecked,
    }
    result["evidence"]["evidence_map"] = [
        {
            "finding_id": f.get("id"),
            "facet": f.get("facet"),
            "locator": f.get("locator"),
            "quote": f.get("quote"),
            "file": seg_by_locator.get(f.get("locator"), {}).get("file") if f.get("locator") in seg_by_locator else None,
        }
        for f in findings
    ]
    result["evidence"]["segments_ref"] = SEGMENTS_REF

    for err in load_errors:
        if err not in result.get("errors", []):
            result.setdefault("errors", []).append("finding_load: %s" % err)

    if result.get("runtime") is None:
        result["runtime"] = {}
    result["runtime"]["end_epoch"] = epoch_sec()
    result["runtime"]["end_at"] = now_iso()

    # keep case_id consistent
    if manifest is not None and manifest.get("case_id") and not result.get("case_id"):
        result["case_id"] = manifest["case_id"]

    write_json(result_path, result)

    report_path = os.path.join(output_dir, "report.md")
    write_text(report_path, render_report(result))

    # update meta.json computed coverage (preserve agent narrative fields)
    meta_path = os.path.join(work_dir, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            meta = load_json(meta_path)
        except Exception:  # noqa: BLE001
            meta = {}
    meta.update(
        {
            "case_id": result.get("case_id"),
            "status": result.get("status"),
            "checked_facets": checked_facets,
            "unchecked_facets": unchecked,
            "covered_check_classes": covered_classes,
            "numeric_check_count": len(result["numeric_checks"]),
            "finding_count": counts["total"],
            "updated_at": now_iso(),
        }
    )
    write_json(meta_path, meta)

    print(
        "finalize: findings=%d numeric_candidates=%d checked_facets=%d"
        % (counts["total"], len(result["numeric_checks"]), len(checked_facets))
    )
    return 0


def write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def render_report(result):
    L = []
    L.append("# 研究方法与统计审查报告")
    L.append("")
    L.append("- case_id: `%s`" % result.get("case_id"))
    L.append("- status: `%s`" % result.get("status"))
    L.append("- 结论：`%s`" % result.get("summary", {}).get("verdict", "unchecked"))
    counts = result.get("summary", {}).get("counts", {})
    L.append("- 意见总数：%s（critical %s / major %s / minor %s / advisory %s / pending %s）" % (
        counts.get("total", 0), counts.get("critical", 0), counts.get("major", 0),
        counts.get("minor", 0), counts.get("advisory", 0), counts.get("pending", 0),
    ))
    L.append("")
    coverage = result.get("review_report", {}).get("coverage", {})
    L.append("## 检查面覆盖")
    L.append("")
    L.append("- 已检查 facet：%s" % (", ".join(coverage.get("checked_facets", [])) or "无"))
    L.append("- 未检查 facet：%s" % (", ".join(coverage.get("unchecked_facets", [])) or "无"))
    L.append("- 已覆盖 check_class：%s" % (", ".join(coverage.get("covered_check_classes", [])) or "无"))
    L.append("")
    L.append("## 审查意见")
    L.append("")
    findings = result.get("review_report", {}).get("findings", [])
    if not findings:
        L.append("（暂无落盘意见）")
        L.append("")
    for f in findings:
        L.append("### %s · %s" % (f.get("id"), f.get("facet")))
        L.append("")
        L.append("- 定位：`%s`" % f.get("locator"))
        L.append("- 缺陷类型：`%s`；check_class：`%s`；严重度：`%s`；置信度：`%s`"
                 % (f.get("finding_type"), f.get("check_class"), f.get("severity"), f.get("confidence")))
        L.append("- 原文摘录：> %s" % f.get("quote"))
        if f.get("basis"):
            L.append("- 依据：%s" % f.get("basis"))
        if f.get("suggestion"):
            L.append("- 修改建议：%s" % f.get("suggestion"))
        L.append("")
    nc = result.get("numeric_checks", [])
    if nc:
        L.append("## 数值一致性候选（需人工复核确认）")
        L.append("")
        for c in nc:
            L.append("- `%s` @ %s — %s" % (c.get("type"), ", ".join(c.get("locators", [])), c.get("detail")))
        L.append("")
    for section, key in (("局限性", "limitations"), ("不确定性", "uncertainty"),
                         ("待人工复核", "pending_human"), ("错误", "errors"),
                         ("降级", "degradations")):
        items = result.get(key, [])
        if items:
            L.append("## %s" % section)
            L.append("")
            for it in items:
                L.append("- %s" % it)
            L.append("")
    L.append("## 变更记录")
    L.append("")
    for ch in result.get("change_log", []):
        L.append("- `%s` %s：%s" % (ch.get("at"), ch.get("action"), ch.get("detail")))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
def run_selftest():
    HERE = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(HERE)
    failures = []
    ok_count = 0

    def check(cond, label):
        nonlocal ok_count
        if cond:
            ok_count += 1
            print("  PASS  %s" % label)
        else:
            failures.append(label)
            print("  FAIL  %s" % label)

    print("== selftest: package structure ==")
    skill_md = os.path.join(root, "SKILL.md")
    check(os.path.exists(skill_md), "SKILL.md exists")
    fm_ok = False
    if os.path.exists(skill_md):
        text = open(skill_md, encoding="utf-8").read()
        fm_ok = text.startswith("---") and "name:" in text.split("---")[1] and "version:" in text.split("---")[1]
    check(fm_ok, "SKILL.md YAML frontmatter (name/version) present")
    for script in ("main.py", "core_impl.py", "selftest.py"):
        check(os.path.exists(os.path.join(HERE, script)), "scripts/%s exists" % script)

    print("== selftest: --help exits 0 ==")
    for sub in (["--help"], ["discover", "--help"], ["scaffold", "--help"], ["finalize", "--help"]):
        rc = subprocess.run(
            [sys.executable, "-B", os.path.join(HERE, "main.py")] + sub,
            capture_output=True, text=True, timeout=60,
        ).returncode
        check(rc == 0, "main.py %s -> exit 0" % " ".join(sub))

    import tempfile
    base = tempfile.mkdtemp(prefix="skill-selftest-")

    def run_pipeline(inp, out, work):
        for sub in ("discover", "scaffold", "finalize"):
            rc = subprocess.run(
                [sys.executable, "-B", os.path.join(HERE, "main.py"), sub,
                 "--input", inp, "--output", out, "--work", work],
                capture_output=True, text=True, timeout=180,
            ).returncode
            if rc != 0:
                return rc
        return 0

    def make_synthetic(d):
        os.makedirs(os.path.join(d, "materials"), exist_ok=True)
        case = {
            "case_id": "selftest-case",
            "task": "research methods review",
            "output_contract": {
                "deliverable": "review_list",
                "facets": ["research_design", "variable_definition", "statistical_methods",
                           "sample_size_power", "results_reporting"],
                "required_evidence": True,
            },
        }
        write_json(os.path.join(d, "case.json"), case)
        write_text(os.path.join(d, "task.md"), "# 自检任务（合成示例，仅测试用）\n\n请审查研究方法与统计。\n")
        write_text(
            os.path.join(d, "materials", "sample.txt"),
            "## Methods\nPatients were randomized 1:1.\n\n"
            "Continuous outcomes in two independent groups were analyzed with a paired t-test.\n\n"
            "## Results\nA total of N=120 participants were enrolled (n=60 per group).\n\n"
            "The primary outcome difference was 2.3 (95% CI 0.1 to 4.5). p=0.06, and the "
            "difference was statistically significant.\n",
        )

    print("== selftest: synthetic pipeline (transient, not shipped) ==")
    d1 = os.path.join(base, "normal")
    make_synthetic(d1)
    rc = run_pipeline(d1, os.path.join(d1, "out"), os.path.join(d1, "work"))
    check(rc == 0, "synthetic input runs discover/scaffold/finalize")
    res_path = os.path.join(d1, "out", "result.json")
    parsed = None
    if os.path.exists(res_path):
        try:
            parsed = load_json(res_path)
        except Exception:  # noqa: BLE001
            parsed = None
    check(parsed is not None and parsed.get("case_id") == "selftest-case", "result.json valid with case_id")
    if parsed is not None:
        check(parsed.get("deliverable") == "review_list", "output_contract field mirrored to top level")
        check(len(parsed.get("numeric_checks", [])) >= 2, "numeric candidate checks produced")
        check(os.path.exists(os.path.join(d1, "out", "report.md")), "report.md rendered")

    print("== selftest: degradation cases ==")
    d2 = os.path.join(base, "no-materials")
    os.makedirs(d2, exist_ok=True)
    write_json(os.path.join(d2, "case.json"), {"case_id": "empty-case", "output_contract": {"x": 1}})
    write_text(os.path.join(d2, "task.md"), "# task\n")
    run_pipeline(d2, os.path.join(d2, "out"), os.path.join(d2, "work"))
    r2 = load_json(os.path.join(d2, "out", "result.json")) if os.path.exists(os.path.join(d2, "out", "result.json")) else None
    check(r2 is not None and r2.get("status") == "insufficient_evidence", "no materials -> insufficient_evidence")

    d3 = os.path.join(base, "no-case")
    os.makedirs(d3, exist_ok=True)
    run_pipeline(d3, os.path.join(d3, "out"), os.path.join(d3, "work"))
    r3 = load_json(os.path.join(d3, "out", "result.json")) if os.path.exists(os.path.join(d3, "out", "result.json")) else None
    check(r3 is not None and r3.get("status") == "input_error", "no case.json and no materials -> input_error")

    d4 = os.path.join(base, "bad-json")
    os.makedirs(d4, exist_ok=True)
    write_text(os.path.join(d4, "case.json"), "{ not valid json")
    write_text(os.path.join(d4, "task.md"), "# task\n")
    write_text(os.path.join(d4, "materials.txt"), "N=10, p=0.04 significant\n")
    run_pipeline(d4, os.path.join(d4, "out"), os.path.join(d4, "work"))
    r4 = load_json(os.path.join(d4, "out", "result.json")) if os.path.exists(os.path.join(d4, "out", "result.json")) else None
    check(r4 is not None and r4.get("status") == "partial" and len(r4.get("errors", [])) >= 1,
          "bad case.json -> partial + error recorded, no crash")

    print("== selftest: determinism ==")
    d5 = os.path.join(base, "det-a")
    d6 = os.path.join(base, "det-b")
    make_synthetic(d5)
    make_synthetic(d6)
    run_pipeline(d5, os.path.join(d5, "out"), os.path.join(d5, "work"))
    run_pipeline(d6, os.path.join(d6, "out"), os.path.join(d6, "work"))
    ra = load_json(os.path.join(d5, "out", "result.json"))
    rb = load_json(os.path.join(d6, "out", "result.json"))
    for r in (ra, rb):
        r.pop("runtime", None)
        r.pop("change_log", None)
    check(json.dumps(ra, sort_keys=True, ensure_ascii=False) ==
          json.dumps(rb, sort_keys=True, ensure_ascii=False),
          "same input twice -> identical result.json (runtime/change_log excluded)")

    print("== selftest result ==")
    print("PASS %d / %d" % (ok_count, ok_count + len(failures)))
    if failures:
        print("FAILED: " + "; ".join(failures))
        return 1
    print("all checks passed")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _add_path_args(parser, suppress=False):
    default = argparse.SUPPRESS if suppress else DEFAULT_INPUT
    parser.add_argument("--input", default=default, help="input dir (default %s)" % DEFAULT_INPUT)
    default = argparse.SUPPRESS if suppress else DEFAULT_OUTPUT
    parser.add_argument("--output", default=default, help="output dir (default %s)" % DEFAULT_OUTPUT)
    default = argparse.SUPPRESS if suppress else DEFAULT_WORK
    parser.add_argument("--work", default=default, help="work dir (default %s)" % DEFAULT_WORK)


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Research-methods stats audit: discover / scaffold / finalize / selftest.",
    )
    _add_path_args(p)
    sub = p.add_subparsers(dest="command")
    sp = sub.add_parser("discover", help="scan input and build segments/terms/manifest")
    _add_path_args(sp, suppress=True)
    sp = sub.add_parser("scaffold", help="write minimal legal result.json skeleton")
    _add_path_args(sp, suppress=True)
    sp = sub.add_parser("finalize", help="merge findings, validate, numeric checks, render report")
    _add_path_args(sp, suppress=True)
    sp = sub.add_parser("selftest", help="self-check the package")
    _add_path_args(sp, suppress=True)
    return p


def cmd_main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "discover":
        return cmd_discover(args.input, args.output, args.work)
    if args.command == "scaffold":
        return cmd_scaffold(args.input, args.output, args.work)
    if args.command == "finalize":
        return cmd_finalize(args.input, args.output, args.work)
    if args.command == "selftest":
        return run_selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(cmd_main(sys.argv[1:]))
