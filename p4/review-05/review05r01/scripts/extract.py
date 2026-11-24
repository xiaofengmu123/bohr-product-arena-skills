#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract.py — deterministic discovery, parsing and observation extraction.

Standard library only. This module owns three responsibilities:

1. Discovery   — classify files under the input root without assuming names.
2. Parsing     — robustly read text (utf-8-sig -> utf-8 -> latin-1 fallback)
                 and convert PDFs to text via ``pdftotext`` or the platform
                 ``pdf-text-tool``, recording degradations instead of guessing.
3. Observation — emit rule-based *candidates*. An observation states "this
                 pattern occurs here; verify it", never "this is a violation".
                 Final verdicts are the reviewing Agent's job (main.py merge).

Determinism contract: files are sorted, rules run in a fixed order, ids are
assigned sequentially, and quotes are capped. Re-running on the same input
yields identical observations.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".tex", ".rst", ".csv", ".tsv", ".json",
    ".yaml", ".yml", ".py", ".bib", ".xml", ".html", ".htm", ".ts",
}
PDF_SUFFIXES = {".pdf"}

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB; larger files are skipped and recorded
QUOTE_LIMIT = 400                 # cap verbatim quotes so result.json stays small
DEFAULT_HITS_PER_RULE = 3


# --------------------------------------------------------------------------- #
# 1. Robust reading / PDF conversion
# --------------------------------------------------------------------------- #

def read_json_robust(path: Path):
    """Return (obj, error). Never raises; bad JSON is reported, not fatal."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"cannot read file: {exc}"
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return json.loads(raw.decode(enc)), None
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None, "undecodable or invalid JSON"


def read_text_robust(path: Path):
    """Return (text, error). Tries utf-8-sig, utf-8, gb18030, latin-1."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return "", f"cannot read file: {exc}"
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return "", "undecodable text"


def pdf_to_text(pdf_path: Path, out_txt: Path):
    """Convert one PDF to text. Returns (ok, error). Tries pdftotext first,
    then the platform pdf-text-tool. Writes the text file next to the PDF name."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            subprocess.run(
                [pdftotext, "-layout", str(pdf_path), str(out_txt)],
                check=True, timeout=180,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if out_txt.exists() and out_txt.stat().st_size > 0:
                return True, None
        except (OSError, subprocess.SubprocessError) as exc:
            pass
    tool = shutil.which("pdf-text-tool")
    if tool:
        try:
            subprocess.run(
                [tool, "--input", str(pdf_path), "--output", str(out_txt)],
                check=True, timeout=180,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if out_txt.exists() and out_txt.stat().st_size > 0:
                return True, None
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"pdf-text-tool failed: {exc}"
    return False, "no PDF text extractor available (pdftotext / pdf-text-tool)"


# --------------------------------------------------------------------------- #
# 2. Discovery
# --------------------------------------------------------------------------- #

def discover(root: Path):
    """Classify files under root. Returns list of dicts in sorted path order."""
    items = []
    if not root.exists():
        return items
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        name = p.name.lower()
        if name == "case.json" or (name.startswith("case") and name.endswith(".json")):
            role = "case"
        elif name == "task.md" or (name.startswith("task") and name.endswith(".md")):
            role = "task"
        elif p.suffix.lower() in TEXT_SUFFIXES:
            role = "material"
        elif p.suffix.lower() in PDF_SUFFIXES:
            role = "pdf"
        else:
            role = "artifact"
        items.append({"path": p, "name": p.name, "role": role, "size": size})
    return items


def load_case(root: Path):
    """Return (case, case_id, contract, errors)."""
    errors = []
    case_item = next((it for it in discover(root) if it["role"] == "case"), None)
    if case_item is None:
        return None, "unknown", {}, ["/input/case.json not found"]
    case, err = read_json_robust(case_item["path"])
    if err:
        return None, "unknown", {}, [f"case.json unreadable: {err}"]
    case_id = None
    for key in ("case_id", "id", "challenge_id", "task_id"):
        val = case.get(key)
        if isinstance(val, str) and val.strip():
            case_id = val.strip()
            break
    if not case_id:
        case_id = case_item["path"].stem
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {}
    return case, case_id, contract, errors


def _looks_like_list(key: str) -> bool:
    k = key.lower()
    return (
        k.endswith("s")
        or any(t in k for t in ("list", "items", "array", "findings", "sources",
                                "references", "fields", "keys", "tags"))
    )


def default_for(contract: dict) -> dict:
    """Echo every contract key. Missing values become null / [] without renaming
    or dropping keys."""
    out = {}
    for key, value in contract.items():
        if value is None:
            out[key] = [] if _looks_like_list(key) else None
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------- #
# 3. Line/page annotation
# --------------------------------------------------------------------------- #

def annotate_lines(text: str):
    """Return (lines, saw_ff). Each line keeps page, number, text, start, end."""
    lines = []
    page = 1
    saw_ff = False
    i = 0
    n = len(text)
    line_start = 0

    def push(end):
        nonlocal i
        lines.append({
            "page": page, "num": len(lines) + 1,
            "text": text[line_start:end], "start": line_start, "end": end,
        })
        i += 1

    while i < n:
        ch = text[i]
        if ch == "\f":
            saw_ff = True
            push(i)
            page += 1
            line_start = i
        elif ch == "\n":
            push(i)
            line_start = i
        elif ch == "\r":
            end = i
            if i + 1 < n and text[i + 1] == "\n":
                i += 1
            push(end)
            line_start = i
        else:
            i += 1
    if line_start < n:
        lines.append({
            "page": page, "num": len(lines) + 1,
            "text": text[line_start:n], "start": line_start, "end": n,
        })
    return lines, saw_ff


def span_to_locator(lines, start: int, end: int, saw_ff: bool) -> str:
    lo = hi = None
    for i, ln in enumerate(lines):
        if ln["end"] <= start:
            continue
        if ln["start"] >= end:
            break
        if lo is None:
            lo = i
        hi = i
    if lo is None:
        return "position unavailable"
    a = lines[lo]["num"]
    b = lines[hi]["num"]
    if saw_ff:
        pa, pb = lines[lo]["page"], lines[hi]["page"]
        if pa == pb:
            return f"page {pa} · line {a}-{b}"
        return f"page {pa}-{pb} · line {a}-{b}"
    if a == b:
        return f"line {a}"
    return f"line {a}-{b}"


def _clip(s: str, limit: int = QUOTE_LIMIT) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit] + " …"


# --------------------------------------------------------------------------- #
# 4. Rule-based observations (candidates, not verdicts)
# --------------------------------------------------------------------------- #

def _emit(text, lines, saw_ff, file, start, end, rule, category, dtype,
          sev, note):
    q = _clip(text[start:end])
    return {
        "rule": rule,
        "category": category,
        "type_default": dtype,
        "severity_default": sev,
        "file": file,
        "locator": span_to_locator(lines, start, end, saw_ff),
        "quote": q,
        "note": note,
    }


def _scan_pattern(obs, text, lines, saw_ff, file, pattern, rule, category,
                  dtype, sev, note, hits=DEFAULT_HITS_PER_RULE):
    for m in list(re.finditer(pattern, text))[:hits]:
        obs.append(_emit(text, lines, saw_ff, file, m.start(), m.end(),
                         rule, category, dtype, sev, note))


def _doc_has(text, patterns) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _missing_near(text, primary, context, distance=240):
    """Spans of `primary` matches lacking any `context` match within `distance`."""
    spans = []
    for m in re.finditer(primary, text, re.IGNORECASE):
        lo = max(0, m.start() - distance)
        hi = min(len(text), m.end() + distance)
        window = text[lo:hi]
        if not any(re.search(c, window, re.IGNORECASE) for c in context):
            spans.append((m.start(), m.end()))
    return spans


def build_observations(file: str, text: str):
    """Run all deterministic rules on one text file. Returns observations."""
    obs = []
    lines, saw_ff = annotate_lines(text)

    # ---- citation -------------------------------------------------------- #
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"\[\s*\d+(?:\s*[,，、\-–]\s*\d+)*\s*\]", "cite_bracket_marker",
        "citation", "insufficient_evidence", "info",
        "Bracket citation markers detected; verify a reference list exists and "
        "each number resolves to a real entry.",
        hits=5)
    if re.search(r"\[\s*\d+\s*\]", text) and re.search(
            r"\([A-Z][A-Za-z]*(?: and [A-Z][A-Za-z]*| et al\.)?,\s*\d{4}\)", text):
        _scan_pattern(
            obs, text, lines, saw_ff, file,
            r"\([A-Z][A-Za-z]*(?: and [A-Z][A-Za-z]*| et al\.)?,\s*\d{4}\)",
            "cite_mixed_styles", "citation", "suggestion", "info",
            "Both numeric and author-year citation styles appear; verify the "
            "target journal requires one consistent style.", hits=2)
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"[“\"][^”\"]{25,}[”\"]", "quote_unattributed",
        "citation", "insufficient_evidence", "minor",
        "Long quoted span detected; confirm it is attributed to a source and "
        "that the source is cited where the quotation appears.", hits=4)

    # ---- privacy --------------------------------------------------------- #
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "pii_email",
        "privacy", "norm_violation", "major",
        "Email-like token; confirm it is not identifiable personal data or "
        "obtain/reference authorization if it is.", hits=5)
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"(?:\+?86[-\s]?)?1[3-9]\d{9}", "pii_phone",
        "privacy", "norm_violation", "major",
        "Mobile-phone-like token; check consent/de-identification.", hits=5)
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"\b\d{17}[\dXx]\b", "pii_id_number",
        "privacy", "norm_violation", "critical",
        "18-digit ID-like token; restricted personal data — de-identify or "
        "confirm explicit authorization.", hits=3)

    # ---- ethics ---------------------------------------------------------- #
    human_pat = r"\b(participants?|patients?|subjects?|volunteers?|recruit(?:ed|ment)?)\b|受试者|患者|参与者|志愿者|被试"
    human_ctx = [r"\b(IRB|institutional review board|ethics committee|informed consent)\b",
                 r"伦理委员会|知情同意|伦理批件|伦理审查"]
    if _doc_has(text, [human_pat]) and not _doc_has(text, human_ctx):
        _scan_pattern(
            obs, text, lines, saw_ff, file, human_pat,
            "human_subjects_no_ethics_statement", "ethics",
            "insufficient_evidence", "major",
            "Human-subject terms appear but no ethics/consent statement is "
            "detected; verify the approval number and consent are stated.", hits=3)
    animal_pat = r"\b(mice|rats?|animals?|rodents?|primates?)\b|小鼠|大鼠|动物|灵长"
    animal_ctx = [r"\b(IACUC|animal ethics|animal welfare|ARRIVE)\b", r"动物伦理|实验动物"]
    if _doc_has(text, [animal_pat]) and not _doc_has(text, animal_ctx):
        _scan_pattern(
            obs, text, lines, saw_ff, file, animal_pat,
            "animal_subjects_no_ethics_statement", "ethics",
            "insufficient_evidence", "major",
            "Animal terms appear but no animal-ethics statement is detected; "
            "verify protocol approval and reporting guideline.", hits=3)
    if re.search(r"(approved by|批件号|伦理批件|approved the protocol)", text, re.IGNORECASE) \
            and not re.search(r"[A-Za-z]{0,6}-?\d{4,}", text):
        _scan_pattern(
            obs, text, lines, saw_ff, file,
            r"(approved by|批件号|伦理批件|approved the protocol)",
            "ethics_approval_no_number", "ethics", "insufficient_evidence",
            "minor",
            "Approval is asserted but no identifier is visible; an approval "
            "number must be verifiable, not merely asserted.", hits=2)

    # ---- copyright ------------------------------------------------------- #
    reuse_pat = r"\b(Figure|Fig\.?|Table)\s*\d+|图\s*\d+|表\s*\d+"
    reuse_src = r"adapted from|reproduced|modified from|reprinted|转载|改编自|引自|经许可"
    if _doc_has(text, [reuse_pat]) and _doc_has(text, [reuse_src]):
        for s, e in _missing_near(
                text, reuse_src,
                [r"permission|license|authori[sz]ed|CC BY|copyright holder|授权|许可|版权方|已获授权"]):
            obs.append(_emit(text, lines, saw_ff, file, s, e,
                             "reused_figure_no_license", "copyright",
                             "norm_violation", "major",
                             "Reuse is stated but no permission/license is "
                             "evident nearby; add source, license and permission."))
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"©|\(c\)\s+\d{4}|copyright\s+©?", "copyright_mark_no_license_type",
        "copyright", "insufficient_evidence", "info",
        "Copyright marker found; confirm the license type / holder is stated "
        "and compatible with reuse.", hits=3)

    # ---- submission / norms ---------------------------------------------- #
    if _doc_has(text, [r"\b(abstract|introduction|conclusion|methods)\b", r"摘要|引言|结论|方法"]) \
            and not _doc_has(text, [r"\b(competing interests|conflict of interest|disclosure)\b",
                                    r"利益冲突|竞争性利益|声明"]):
        _scan_pattern(
            obs, text, lines, saw_ff, file, r"\b(abstract|introduction|conclusion)\b|摘要|引言|结论",
            "coi_statement_missing", "submission", "norm_violation", "minor",
            "Paper-like structure detected but no conflict-of-interest/"
            "disclosure statement; most journals require one.", hits=1)
    fund_pat = r"\b(grant|funding|funded by|supported by|award)\b|基金|资助|项目编号|课题号"
    ack_pat = r"\b(acknowledg(?:e|e)ments?|acknowledgment)\b|致谢"
    if _doc_has(text, [fund_pat]) and not _doc_has(text, [ack_pat]):
        _scan_pattern(
            obs, text, lines, saw_ff, file, fund_pat,
            "funding_ack_mismatch", "submission", "norm_violation", "minor",
            "Funding/grant terms appear without an acknowledgment section; "
            "confirm funder name and grant number are stated.", hits=2)

    # ---- method / statistics --------------------------------------------- #
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"data not shown|not shown|available (?:upon request|on request)|personal communication|as described (?:previously|elsewhere)|数据未显示|如前所述|备索|私下交流",
        "method_nonreproducible", "method", "insufficient_evidence", "minor",
        "Non-reproducible phrasing; verify whether the missing detail is "
        "provided elsewhere or must be added.", hits=4)
    pval_pat = r"[pP]\s*[<≤=]\s*0?\.\d+"
    if _doc_has(text, [pval_pat, r"显著性|significant"]) and not _doc_has(
            text, [r"\b(t[- ]test|ANOVA|chi[- ]square|Mann[- ]Whitney|Wilcoxon|Kruskal|χ²|χ2)\b",
                   r"t检验|方差分析|卡方检验|秩和检验"]):
        _scan_pattern(
            obs, text, lines, saw_ff, file, pval_pat,
            "stats_significance_no_test", "statistics", "insufficient_evidence",
            "major",
            "A p-value/significance claim appears without a named statistical "
            "test; the test, statistic, df and correction must be stated.", hits=4)
    if _doc_has(text, [r"\bsignificant\b|显著"]) and re.search(r"\b(p\s*[<≤=]\s*0?\.\d+){2,}", text):
        _scan_pattern(
            obs, text, lines, saw_ff, file, r"significan\w*|显著",
            "stats_multiple_comparisons", "statistics", "suggestion", "minor",
            "Multiple significance claims detected; confirm multiple-comparison "
            "correction is described.", hits=2)

    # ---- academic integrity ---------------------------------------------- #
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"\b(fabrication|falsification|plagiarism|duplicate publication|self[- ]plagiarism|salami (?:slicing|publication))\b|捏造|篡改|造假|抄袭|重复发表|一稿多投",
        "integrity_flag_terms", "academic_integrity", "fact_error", "critical",
        "Integrity-related term; determine context — is it a disclosure, an "
        "allegation, or a red flag in the work itself?", hits=5)
    _scan_pattern(
        obs, text, lines, saw_ff, file,
        r"\b(identical|same image|duplicated|reused|recycled)\b|同一图片|重复使用|复用",
        "image_reuse_flag", "academic_integrity", "insufficient_evidence",
        "major",
        "Image-reuse phrasing; verify images/figures are not duplicated without "
        "disclosure.", hits=4)

    return obs


# --------------------------------------------------------------------------- #
# 5. Top-level extraction
# --------------------------------------------------------------------------- #

def parse_materials(root: Path, work: Path):
    """Extract text from all materials. Returns (parsed, degradations, errors)."""
    parsed = {}
    degradations = []
    errors = []
    out_dir = work / "parsed"
    out_dir.mkdir(parents=True, exist_ok=True)
    for it in discover(root):
        if it["role"] not in ("material", "pdf", "artifact"):
            continue
        rel = it["path"].relative_to(root).as_posix()
        if it["size"] > MAX_FILE_BYTES:
            degradations.append(f"skipped oversized file ({it['size']} bytes): {rel}")
            continue
        if it["role"] == "pdf":
            txt_path = out_dir / (it["path"].stem + ".txt")
            ok, err = pdf_to_text(it["path"], txt_path)
            if not ok:
                degradations.append(f"PDF not parsed: {rel}: {err}")
                parsed[rel] = {"kind": "pdf", "text_path": None, "chars": 0,
                               "pages": 0, "lines": 0, "error": err}
                continue
            text, terr = read_text_robust(txt_path)
            if terr:
                errors.append(f"read extracted text {rel}: {terr}")
                continue
            lines, saw_ff = annotate_lines(text)
            parsed[rel] = {"kind": "pdf", "text_path": txt_path.as_posix(),
                           "chars": len(text),
                           "pages": (lines[-1]["page"] if lines else 0),
                           "lines": len(lines)}
        else:
            text, terr = read_text_robust(it["path"])
            if terr:
                errors.append(f"read text {rel}: {terr}")
                continue
            lines, saw_ff = annotate_lines(text)
            parsed[rel] = {"kind": "text", "text_path": it["path"].as_posix(),
                           "chars": len(text),
                           "pages": 1 if lines else 0,
                           "lines": len(lines)}
    return parsed, degradations, errors


def load_all_texts(root: Path, parsed: dict):
    """Return {relpath: (text, lines, saw_ff)} for already-parsed materials."""
    texts = {}
    for rel, meta in parsed.items():
        p = Path(meta["text_path"])
        if not p or not p.exists():
            continue
        text, err = read_text_robust(p)
        if err:
            continue
        texts[rel] = (text,) + annotate_lines(text)
    return texts


def run_extraction(root: Path, work: Path):
    """Full deterministic extraction. Returns a dict for evidence.json."""
    case, case_id, contract, case_errors = load_case(root)
    files = discover(root)
    parsed, degradations, parse_errors = parse_materials(root, work)
    texts = load_all_texts(root, parsed)
    observations = []
    for rel in sorted(texts):
        text = texts[rel][0]
        observations.extend(build_observations(rel, text))
    for i, ob in enumerate(observations, 1):
        ob["id"] = f"OBS-{i:03d}"
    return {
        "case_id": case_id,
        "contract": default_for(contract),
        "contract_missing": (contract == {}),
        "files": [
            {"path": it["path"].as_posix(), "role": it["role"],
             "size": it["size"]} for it in files
        ],
        "parsed": parsed,
        "observations": observations,
        "errors": case_errors + parse_errors,
        "degradations": degradations,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Deterministic extraction for audit skill")
    ap.add_argument("--root", default="/input")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()
    data = run_extraction(Path(args.root), Path(args.work))
    out = Path(args.work) / "evidence.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, out)
    print(f"wrote {out} ({len(data['observations'])} observations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
