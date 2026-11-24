#!/usr/bin/env python3
"""Core implementation for the evidence-driven-writing skill.

Standard library only. The LLM agent performs the semantic work (claim
splitting, evidence matching, grading, hedging); these functions provide the
deterministic scaffolding, material extraction, verbatim-quote verification,
schema validation, hedge checks and final assembly so every result is
reproducible and independently re-checkable.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import zlib

MAX_TEXT_BYTES = 2 * 1024 * 1024

REQUIRED_TOP_KEYS = [
    "case_id", "status", "deliverable", "sources",
    "uncertainty", "pending_human", "change_log",
]

DEFAULT_CONTRACT_KEYS = ["deliverable", "sources", "change_log"]

CLAIM_TYPES = {
    "fact", "statistic", "causal", "mechanism", "comparison",
    "recommendation", "definition", "scope",
}

SUPPORT_VERDICTS = {
    "supports", "partially_supports", "weakens", "contradicts", "no_support",
}

EVIDENCE_GRADES = {
    "A": "强证据：多篇独立原始研究一致或系统综述/荟萃分析；大样本高质量设计；领域共识",
    "B": "中等证据：单篇设计良好的原始研究或指南推荐",
    "C": "弱证据：单篇小样本、临床前、病例报告、亚组分析",
    "D": "不足证据：专家意见、无原始数据的综述陈述、仅摘要",
    "U": "无法核验：元数据无法确认、原文不可得",
}

UNCERTAINTY_FLAGS = {
    "correlation_as_causation", "single_study_as_consensus", "sample_size_small",
    "conflict_hidden", "mechanism_unclear", "applicability_unclear",
    "abstract_only", "metadata_unverified", "secondary_citation",
    "retracted_source", "version_diff",
}

STRONG_VERBS_ZH = ["表明", "证实", "证明", "确立"]
STRONG_VERBS_EN = ["demonstrat", "confirm", "establish", "prove"]
WEAK_MARKERS_ZH = ["可能", "初步", "提示", "有待", "或许"]
WEAK_MARKERS_EN = ["suggest", "may", "might", "propos", "possibly", "preliminary"]

BLOCKING_ISSUES = {
    "quote_not_found", "source_not_loaded", "orphan_claim_ref",
    "bad_grade", "bad_verdict", "claim_missing_evidence", "hedge_too_strong",
}

_TS = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# low-level I/O
# --------------------------------------------------------------------------
def read_bytes(path, max_bytes=MAX_TEXT_BYTES):
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1)
    except OSError as e:
        return None, f"oserror:{e}"
    if len(data) > max_bytes:
        return None, "too_large"
    return data, None


def decode_text(data):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("latin-1", errors="replace")


def read_text_file(path):
    if not path or not os.path.exists(path):
        return ""
    data, err = read_bytes(path)
    if err:
        return ""
    return decode_text(data)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def load_json(path):
    if not path or not os.path.exists(path):
        return None, "missing"
    data, err = read_bytes(path)
    if err:
        return None, err
    text = decode_text(data)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0)), "recovered_partial_json"
            except Exception:
                pass
        return None, f"json_error:{e}"


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_change(log, action, detail):
    log = list(log or [])
    log.append({"time": _TS(), "action": action, "detail": detail})
    return log


# --------------------------------------------------------------------------
# input discovery / case parsing
# --------------------------------------------------------------------------
def scan_input(input_dir):
    found = {"case": None, "task": None, "materials": []}
    if not input_dir or not os.path.isdir(input_dir):
        return found
    try:
        names = os.listdir(input_dir)
    except OSError:
        return found
    for name in names:
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        low = name.lower()
        if low == "case.json":
            found["case"] = p
        elif low == "task.md":
            found["task"] = p
        else:
            found["materials"].append({
                "path": p, "name": name, "ext": os.path.splitext(low)[1],
            })
    return found


def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "caseId", "id"):
        v = case.get(k)
        if v not in (None, ""):
            return str(v)
    return None


def extract_contract(case):
    if not isinstance(case, dict):
        return {}, False
    for k in ("output_contract", "outputContract", "contract", "result_schema"):
        v = case.get(k)
        if isinstance(v, dict):
            return v, True
    return {}, False


def default_contract():
    return {
        "deliverable": {"claims_file": "", "evidence_file": "", "draft": ""},
        "sources": [],
        "change_log": [],
    }


# --------------------------------------------------------------------------
# scaffold
# --------------------------------------------------------------------------
def scaffold(case_path, task_path, work_dir, out_dir):
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    case, case_err = load_json(case_path)
    if not isinstance(case, dict):
        case = {}
    task_text = read_text_file(task_path)[:2000]
    case_id = extract_case_id(case)
    contract, has_contract = extract_contract(case)
    if not contract:
        contract = default_contract()
    dflt = default_contract()

    result = {
        "case_id": case_id,
        "status": "in_progress",
        "deliverable": contract.get("deliverable", dflt["deliverable"]),
        "sources": contract.get("sources", []),
        "change_log": append_change(
            contract.get("change_log", []), "scaffold",
            f"case_id={case_id} contract_keys={list(contract.keys())}"),
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [] if not case_err else [{"scope": "case", "detail": case_err}],
        "degradations": [],
        "runtime": {"started_at": _TS(), "finished_at": None},
    }
    for k, v in contract.items():
        if k not in result:
            result[k] = v

    result_path = os.path.join(out_dir, "result.json")
    if os.path.exists(result_path):
        old, _ = load_json(result_path)
        if isinstance(old, dict) and old.get("status") not in (None, "in_progress"):
            return result_path, "skipped_existing"

    write_json_atomic(result_path, result)
    plan = {
        "case_id": case_id,
        "has_output_contract": has_contract,
        "contract_keys": list(contract.keys()),
        "task_excerpt": task_text,
        "materials": [],
        "gaps": [],
        "created_at": _TS(),
    }
    write_json_atomic(os.path.join(work_dir, "plan.json"), plan)
    return result_path, "ok"


# --------------------------------------------------------------------------
# material extraction
# --------------------------------------------------------------------------
def _pdf_unescape(b):
    s = b.decode("latin-1", errors="replace")
    s = s.strip("()")
    s = s.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")

    def _oct(m):
        return chr(int(m.group(1), 8))

    s = re.sub(r"\\([0-7]{3})", _oct, s)
    return s


def extract_pdf_text_minimal(pdf_path):
    """Crude standard-library-only PDF text extraction (FlateDecode streams)."""
    data, err = read_bytes(pdf_path)
    if err:
        return "", err
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        for tm in re.finditer(rb"\((?:[^()\\]|\\.)*\)\s*Tj", raw):
            s = tm.group(0)
            s = s[:s.rfind(b"Tj")].strip()
            chunks.append(_pdf_unescape(s))
        for tm in re.finditer(rb"\[(?:[^\[\]\\]|\((?:[^()\\]|\\.)*\)|\\.)*\]\s*TJ", raw):
            for pm in re.finditer(rb"\((?:[^()\\]|\\.)*\)", tm.group(0)):
                chunks.append(_pdf_unescape(pm.group(0)))
    return "\n".join(chunks), None


def pdf_to_text(pdf_path, out_path):
    for exe in ("pdftotext",):
        try:
            r = subprocess.run(
                [exe, "-layout", pdf_path, out_path],
                capture_output=True, text=True, errors="replace")
            if r.returncode == 0 and os.path.exists(out_path):
                return True, None
            return False, f"pdftotext_rc:{r.returncode}"
        except FileNotFoundError:
            break
        except Exception as e:
            return False, f"pdftotext_error:{e}"
    text, err = extract_pdf_text_minimal(pdf_path)
    if err:
        return False, err
    if text.strip():
        write_text(out_path, text)
        return False, "pdftotext_missing_minimal_fallback"
    return False, "pdftotext_missing_no_text"


def extract_materials(input_dir, work_dir):
    src_dir = os.path.join(work_dir, "sources")
    os.makedirs(src_dir, exist_ok=True)
    found = scan_input(input_dir)
    manifest = []
    degradations = []
    for m in found["materials"]:
        base = os.path.splitext(m["name"])[0]
        try:
            if m["ext"] == ".pdf":
                txt_path = os.path.join(src_dir, base + ".txt")
                ok, err = pdf_to_text(m["path"], txt_path)
                if ok or (err and "fallback" in err):
                    manifest.append({
                        "source_doc": m["name"], "text_path": txt_path,
                        "kind": "pdf_text", "locator_base": "S/P",
                    })
                if not ok and not (err and "fallback" in err):
                    degradations.append({"source_doc": m["name"], "detail": err})
            elif m["ext"] in (".txt", ".md", ".json", ".csv", ".tex"):
                data, err = read_bytes(m["path"])
                if err:
                    degradations.append({"source_doc": m["name"], "detail": err})
                    continue
                text = decode_text(data)
                txt_path = os.path.join(src_dir, base + ".txt")
                write_text(txt_path, text)
                manifest.append({
                    "source_doc": m["name"], "text_path": txt_path,
                    "kind": "text", "locator_base": "line/P",
                })
            else:
                degradations.append({
                    "source_doc": m["name"], "detail": f"unsupported_ext:{m['ext']}"})
        except Exception as e:
            degradations.append({"source_doc": m["name"], "detail": f"exception:{e}"})
    write_json_atomic(
        os.path.join(src_dir, "manifest.json"),
        {"materials": manifest, "degradations": degradations})
    return manifest, degradations


# --------------------------------------------------------------------------
# claim splitting
# --------------------------------------------------------------------------
def split_sentences(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    parts = re.split(r"(?<=[。！？!?])\s*", text)
    out = [p.strip() for p in parts if len(p.strip()) >= 2]
    if len(out) <= 1 and "\n" in text:
        out = [l.strip() for l in text.split("\n") if l.strip()]
    return out


def find_draft(input_dir, work_dir):
    found = scan_input(input_dir)
    cands = found["materials"]
    for m in cands:
        n = m["name"].lower()
        if any(k in n for k in ("draft", "底稿", "manuscript", "稿件", "初稿", "草稿")):
            return m["path"]
    for ext in (".md", ".txt"):
        for m in cands:
            if m["ext"] == ext:
                return m["path"]
    p = os.path.join(work_dir, "draft.md")
    return p if os.path.exists(p) else None


def split_draft_into_claims(draft_path, work_dir):
    text = read_text_file(draft_path)
    if not text.strip():
        return [], "empty_draft"
    sentences = split_sentences(text)
    claims = []
    for i, s in enumerate(sentences):
        claims.append({
            "claim_id": "C%03d" % (i + 1),
            "claim_text": s,
            "claim_type": None,
            "uncertainty_flags": [],
            "status": "unsupported",
            "final_wording": s,
            "source_claim_id": None,
            "hedge_note": None,
        })
    write_json_atomic(os.path.join(work_dir, "claim_units.json"), {"claims": claims})
    return claims, None


def claims_command(input_dir, work_dir, draft_arg):
    draft = draft_arg or find_draft(input_dir, work_dir)
    if not draft:
        write_json_atomic(os.path.join(work_dir, "claim_units.json"), {"claims": []})
        return 0, "no_draft_found"
    write_text(os.path.join(work_dir, "draft.md"), read_text_file(draft))
    claims, err = split_draft_into_claims(os.path.join(work_dir, "draft.md"), work_dir)
    return len(claims), err


def evidence_skeleton(work_dir):
    path = os.path.join(work_dir, "evidence_map.json")
    if os.path.exists(path):
        return
    write_json_atomic(path, {"evidence_units": []})


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
def normalize_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("-\n", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def quote_verify(quote, text):
    q = normalize_text(quote)
    t = normalize_text(text)
    if len(q) < 6:
        return "quote_too_short", q in t if q else False
    if q in t:
        return "matched", True
    if quote and quote.strip() in text:
        return "matched_raw", True
    return "not_found", False


def _has_en(stems, text):
    for s in stems:
        if re.search(r"(?<![A-Za-z])" + re.escape(s) + r"[A-Za-z]*", text, re.IGNORECASE):
            return True
    return False


def hedge_check(grade, wording):
    if not wording or grade not in EVIDENCE_GRADES:
        return None
    w = wording
    has_strong = any(v in w for v in STRONG_VERBS_ZH) or _has_en(STRONG_VERBS_EN, w)
    has_weak = any(m in w for m in WEAK_MARKERS_ZH) or _has_en(WEAK_MARKERS_EN, w)
    if grade in ("C", "D", "U"):
        if has_strong and not has_weak:
            return "hedge_too_strong"
    if grade in ("A", "B"):
        if has_weak and not has_strong:
            return "hedge_too_weak"
    return None


def verify(work_dir, out_dir):
    claims_doc, _ = load_json(os.path.join(work_dir, "claim_units.json"))
    evidence_doc, _ = load_json(os.path.join(work_dir, "evidence_map.json"))
    manifest_doc, _ = load_json(os.path.join(work_dir, "sources", "manifest.json"))

    claims = (claims_doc or {}).get("claims", []) if isinstance(claims_doc, dict) else []
    evidence = (evidence_doc or {}).get("evidence_units", []) if isinstance(evidence_doc, dict) else []
    manifest = (manifest_doc or {}).get("materials", []) if isinstance(manifest_doc, dict) else []

    issues = []
    quote_checks = []

    source_texts = {}
    for m in manifest:
        tp = m.get("text_path")
        if tp and os.path.exists(tp):
            source_texts[m["source_doc"]] = read_text_file(tp)

    claim_ids = set(c.get("claim_id") for c in claims if c.get("claim_id"))

    for ev in evidence:
        eid = ev.get("evidence_id")
        cids = ev.get("claim_ids") or []
        if not isinstance(cids, list):
            issues.append({"type": "bad_claim_ids", "evidence_id": eid,
                           "detail": "claim_ids must be a list"})
            cids = []
        for cid in cids:
            if cid not in claim_ids:
                issues.append({"type": "orphan_claim_ref", "evidence_id": eid,
                               "claim_id": cid, "detail": "claim_id not found"})
        grade = ev.get("evidence_grade")
        if grade not in EVIDENCE_GRADES:
            issues.append({"type": "bad_grade", "evidence_id": eid,
                           "grade": grade, "detail": "must be A|B|C|D|U"})
        sv = ev.get("support_verdict")
        if sv not in SUPPORT_VERDICTS:
            issues.append({"type": "bad_verdict", "evidence_id": eid,
                           "verdict": sv, "detail": "invalid support_verdict"})
        sd = ev.get("source_doc")
        quote = ev.get("quote") or ""
        if sd not in source_texts:
            issues.append({"type": "source_not_loaded", "evidence_id": eid,
                           "source_doc": sd, "detail": "source text not available"})
            continue
        status, matched = quote_verify(quote, source_texts[sd])
        quote_checks.append({
            "evidence_id": eid, "source_doc": sd, "locator": ev.get("locator"),
            "status": status, "matched": matched, "quote": (quote or "")[:200],
        })
        if not matched:
            issues.append({"type": "quote_not_found", "evidence_id": eid,
                           "source_doc": sd, "status": status,
                           "detail": "quote not found verbatim in source"})

    for c in claims:
        cid = c.get("claim_id")
        has_ev = any(cid in (ev.get("claim_ids") or []) for ev in evidence)
        if not has_ev and c.get("status") not in ("unsupported", "pending_human", "dropped"):
            issues.append({"type": "claim_missing_evidence", "claim_id": cid,
                           "detail": "claim has status '%s' but no evidence" % c.get("status")})

    for ev in evidence:
        grade = ev.get("evidence_grade")
        for cid in (ev.get("claim_ids") or []):
            c = next((x for x in claims if x.get("claim_id") == cid), None)
            if c is not None:
                hc = hedge_check(grade, c.get("final_wording"))
                if hc:
                    issues.append({"type": hc, "evidence_id": ev.get("evidence_id"),
                                   "claim_id": cid, "grade": grade,
                                   "detail": "wording not proportional to evidence grade"})

    report = {
        "quote_checks": quote_checks,
        "issues": issues,
        "issue_count": len(issues),
        "checked_at": _TS(),
    }
    write_json_atomic(os.path.join(work_dir, "validation_report.json"), report)
    return report


# --------------------------------------------------------------------------
# finalize
# --------------------------------------------------------------------------
def finalize(work_dir, out_dir):
    plan, _ = load_json(os.path.join(work_dir, "plan.json"))
    claims_doc, _ = load_json(os.path.join(work_dir, "claim_units.json"))
    evidence_doc, _ = load_json(os.path.join(work_dir, "evidence_map.json"))
    report, rep_err = load_json(os.path.join(work_dir, "validation_report.json"))
    manifest, _ = load_json(os.path.join(work_dir, "sources", "manifest.json"))
    result, _ = load_json(os.path.join(out_dir, "result.json"))
    if not isinstance(result, dict):
        result = {}

    claims = (claims_doc or {}).get("claims", []) if isinstance(claims_doc, dict) else []
    evidence = (evidence_doc or {}).get("evidence_units", []) if isinstance(evidence_doc, dict) else []
    issues = (report or {}).get("issues", []) if isinstance(report, dict) else []
    degradations = (manifest or {}).get("degradations", []) if isinstance(manifest, dict) else []

    sources = []
    for ev in evidence:
        sources.append({
            "key": ev.get("evidence_id"),
            "value": (ev.get("quote") or "")[:200],
            "kind": "evidence",
            "source_doc": ev.get("source_doc"),
            "locator": ev.get("locator"),
            "quote": ev.get("quote"),
            "support_verdict": ev.get("support_verdict"),
            "evidence_grade": ev.get("evidence_grade"),
            "grade_reason": ev.get("grade_reason"),
            "claim_ids": ev.get("claim_ids"),
        })

    uncertainty = []
    pending = []
    for c in claims:
        if c.get("uncertainty_flags"):
            uncertainty.append({
                "claim_id": c.get("claim_id"),
                "flags": c.get("uncertainty_flags"),
                "claim_text": (c.get("claim_text") or "")[:200],
            })
        if c.get("status") == "pending_human":
            pending.append({
                "claim_id": c.get("claim_id"),
                "reason": c.get("hedge_note") or "pending human confirmation",
                "claim_text": (c.get("claim_text") or "")[:200],
            })

    draft_final = os.path.join(work_dir, "draft_final.md")
    draft_out = os.path.join(out_dir, "draft.md")
    draft_path = ""
    if os.path.exists(draft_final):
        shutil.copyfile(draft_final, draft_out)
        draft_path = "/app/output/draft.md"
    else:
        draft_orig = os.path.join(work_dir, "draft.md")
        if os.path.exists(draft_orig):
            shutil.copyfile(draft_orig, draft_out)
            draft_path = "/app/output/draft.md"

    result.pop("_contract_present", None)
    result["case_id"] = result.get("case_id") or (plan or {}).get("case_id")
    result["deliverable"] = {
        "claims_file": "/app/work/claim_units.json",
        "evidence_file": "/app/work/evidence_map.json",
        "draft": draft_path,
        "review_report": "/app/output/review_report.md",
        "claim_count": len(claims),
        "evidence_count": len(evidence),
    }
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["pending_human"] = pending
    result["limitations"] = result.get("limitations", [])
    if degradations:
        result["degradations"] = result.get("degradations", []) + degradations
    else:
        result.setdefault("degradations", [])
    if rep_err:
        result["errors"] = result.get("errors", []) + [
            {"scope": "validation", "detail": rep_err}]
    result["change_log"] = append_change(
        result.get("change_log", []), "finalize",
        f"claims={len(claims)} evidence={len(evidence)} issues={len(issues)}")

    blocking = [i for i in issues if i.get("type") in BLOCKING_ISSUES]
    if not claims:
        result["status"] = "insufficient_evidence"
    elif pending or blocking:
        result["status"] = "partial"
    else:
        result["status"] = "complete"

    result["runtime"] = result.get("runtime", {"started_at": _TS()})
    result["runtime"]["finished_at"] = _TS()

    write_json_atomic(os.path.join(out_dir, "result.json"), result)
    write_review_report(out_dir, result, claims, evidence, issues)
    return result


def write_review_report(out_dir, result, claims, evidence, issues):
    lines = ["# 文献证据驱动写作核验报告", ""]
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- 论断数: %d；证据条数: %d；问题数: %d" % (
        len(claims), len(evidence), len(issues)))
    lines.append("")
    lines.append("## 论断清单")
    lines.append("")
    for c in claims:
        lines.append("- `%s` [%s] %s" % (
            c.get("claim_id"), c.get("claim_type") or "?", c.get("claim_text") or ""))
    lines.append("")
    lines.append("## 证据表")
    lines.append("")
    for ev in evidence:
        lines.append("- `%s` -> claims %s | %s %s | 等级 %s | 判定 %s" % (
            ev.get("evidence_id"), ev.get("claim_ids"),
            ev.get("source_doc"), ev.get("locator"),
            ev.get("evidence_grade"), ev.get("support_verdict")))
        lines.append("  - quote: %s" % ((ev.get("quote") or "")[:200]))
        lines.append("  - reason: %s" % (ev.get("grade_reason") or ""))
    if issues:
        lines.append("")
        lines.append("## 校验问题")
        lines.append("")
        for i in issues:
            lines.append("- [%s] %s" % (i.get("type"), i.get("detail") or ""))
    lines.append("")
    write_text(os.path.join(out_dir, "review_report.md"), "\n".join(lines))


# --------------------------------------------------------------------------
# end-to-end dry run and self-test
# --------------------------------------------------------------------------
def run_pipeline(input_dir, work_dir, out_dir):
    case = os.path.join(input_dir, "case.json")
    task = os.path.join(input_dir, "task.md")
    scaffold(case, task, work_dir, out_dir)
    extract_materials(input_dir, work_dir)
    draft = find_draft(input_dir, work_dir)
    if draft:
        write_text(os.path.join(work_dir, "draft.md"), read_text_file(draft))
        split_draft_into_claims(os.path.join(work_dir, "draft.md"), work_dir)
    else:
        write_json_atomic(os.path.join(work_dir, "claim_units.json"), {"claims": []})
    evidence_skeleton(work_dir)
    verify(work_dir, out_dir)
    finalize(work_dir, out_dir)
    res, _ = load_json(os.path.join(out_dir, "result.json"))
    return res or {}


def selftest():
    base = tempfile.mkdtemp(prefix="edw_selftest_")
    try:
        inp = os.path.join(base, "input"); os.makedirs(inp)
        work = os.path.join(base, "work"); os.makedirs(work)
        out = os.path.join(base, "output"); os.makedirs(out)

        write_json_atomic(os.path.join(inp, "case.json"), {
            "case_id": "demo-001",
            "output_contract": {"deliverable": {"draft": ""}, "sources": [], "change_log": []},
        })
        write_text(os.path.join(inp, "task.md"), "# 测试\n请完成一段有依据的摘要。\n")
        write_text(os.path.join(inp, "paper.txt"),
                   "本研究纳入120例患者。结果显示干预组有效率显著高于对照组（P<0.05）。")
        write_text(os.path.join(inp, "draft.md"), "干预组有效率显著高于对照组。")

        rp, st = scaffold(os.path.join(inp, "case.json"),
                          os.path.join(inp, "task.md"), work, out)
        assert st == "ok", st
        extract_materials(inp, work)

        write_json_atomic(os.path.join(work, "claim_units.json"), {"claims": [{
            "claim_id": "C001", "claim_text": "干预组有效率显著高于对照组。",
            "claim_type": "comparison", "uncertainty_flags": ["sample_size_small"],
            "status": "supported", "final_wording": "干预组有效率显著高于对照组。",
        }]})
        write_json_atomic(os.path.join(work, "evidence_map.json"), {"evidence_units": [{
            "evidence_id": "E001", "claim_ids": ["C001"], "source_doc": "paper.txt",
            "locator": "P1.s1", "quote": "结果显示干预组有效率显著高于对照组（P<0.05）。",
            "support_verdict": "supports", "evidence_grade": "C",
            "grade_reason": "单篇小样本原始研究",
        }]})
        rep = verify(work, out)
        assert rep["issue_count"] == 0, json.dumps(rep, ensure_ascii=False)
        res = finalize(work, out)
        assert res["status"] == "complete", res
        assert os.path.exists(os.path.join(out, "review_report.md"))

        # negative: quote not found + hedge too strong must be caught
        write_json_atomic(os.path.join(work, "evidence_map.json"), {"evidence_units": [{
            "evidence_id": "E001", "claim_ids": ["C001"], "source_doc": "paper.txt",
            "locator": "P1", "quote": "这句话在原文中根本不存在。",
            "support_verdict": "supports", "evidence_grade": "C", "grade_reason": "x",
        }]})
        write_json_atomic(os.path.join(work, "claim_units.json"), {"claims": [{
            "claim_id": "C001", "claim_text": "干预组有效率显著高于对照组。",
            "claim_type": "causal", "uncertainty_flags": [],
            "status": "supported", "final_wording": "本研究证实干预组引起疗效显著提高。",
        }]})
        rep2 = verify(work, out)
        types = {i["type"] for i in rep2["issues"]}
        assert "quote_not_found" in types, types
        assert "hedge_too_strong" in types, types

        # empty input -> insufficient_evidence, no exception
        e_inp = os.path.join(base, "e_input"); os.makedirs(e_inp)
        e_work = os.path.join(base, "e_work"); os.makedirs(e_work)
        e_out = os.path.join(base, "e_out"); os.makedirs(e_out)
        scaffold(os.path.join(e_inp, "case.json"), os.path.join(e_inp, "task.md"),
                 e_work, e_out)
        r = run_pipeline(e_inp, e_work, e_out)
        assert r.get("status") == "insufficient_evidence", r

        # bad JSON must not crash
        b_inp = os.path.join(base, "b_input"); os.makedirs(b_inp)
        b_work = os.path.join(base, "b_work"); os.makedirs(b_work)
        b_out = os.path.join(base, "b_out"); os.makedirs(b_out)
        write_text(os.path.join(b_inp, "case.json"), "{not valid json")
        b_rp, b_st = scaffold(os.path.join(b_inp, "case.json"), None, b_work, b_out)
        assert b_st == "ok", b_st

        print("selftest OK")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(selftest())
