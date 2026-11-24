#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic helper pipeline for the citation-fact-audit skill.

Standard library only. The same input directory plus the same findings files
produce byte-identical result.json and report.md: no wall-clock timestamps, no
random numbers, stable ordering, and content-hashed finding ids.

Subcommands
-----------
discover   Scan /input, extract page-marked PDF text, write work/manifest.json
scaffold   Write a valid partial result.json skeleton plus work/state.json
finalize   Merge work/findings/*.json and work/meta.json into result.json and
           report.md (deterministic, idempotent)
selftest   Run internal self-checks (help / empty / bad-json / determinism)

With no subcommand, discover + scaffold + finalize run in one sweep, which
refreshes the outputs from whatever findings are currently on disk.
"""

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_NAME = "citation-fact-audit"
VERSION = "1.0.0"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000  # 2 MB per material file

MATERIAL_EXTS = {".pdf", ".txt", ".md", ".csv", ".json", ".xml", ".tex"}
META_NAMES = {"case.json", "task.md", "manifest.json", "result.json", "report.md"}

CATEGORIES = [
    "fact_check",
    "citation_support",
    "method_design",
    "statistical_report",
    "argumentation",
    "academic_norm",
]
CATEGORY_LABELS = {
    "fact_check": "事实核验",
    "citation_support": "引用支持",
    "method_design": "方法设计",
    "statistical_report": "统计报告",
    "argumentation": "论证链",
    "academic_norm": "学术规范",
}
CATEGORY_ALIASES = {
    "事实核验": "fact_check",
    "事实": "fact_check",
    "fact": "fact_check",
    "factual": "fact_check",
    "fact_source": "fact_check",
    "引用支持": "citation_support",
    "citation": "citation_support",
    "citation_support": "citation_support",
    "方法设计": "method_design",
    "method": "method_design",
    "method_design": "method_design",
    "统计报告": "statistical_report",
    "数值与统计一致性": "statistical_report",
    "统计": "statistical_report",
    "statistical": "statistical_report",
    "numerical_statistical": "statistical_report",
    "论证链": "argumentation",
    "论证": "argumentation",
    "argumentation": "argumentation",
    "学术规范": "academic_norm",
    "规范": "academic_norm",
    "norm": "academic_norm",
    "academic_norm": "academic_norm",
}
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "suggestion": 3}

# Keys a finding must carry to be merged; id/finding_type are auto-recovered.
REQUIRED_FINDING_KEYS = ["category", "verdict", "severity", "locator", "quote", "suggestion"]


def eprint(*args):
    print(*args, file=sys.stderr)


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """Read a text file tolerating BOM and broken encodings.

    Returns (text, warning). warning is None on clean decode, otherwise a
    short degradation note (the text is still returned with replacement
    characters rather than crashing).
    """
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        return None, "read failed: %s" % exc
    if len(data) > max_bytes:
        return None, "file exceeds size limit (%d bytes)" % max_bytes
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "decoded as latin-1 (invalid UTF-8 bytes)"


def load_json_lenient(path):
    """Load JSON, returning (obj_or_None, error_or_None)."""
    text, warn = read_text(path)
    if text is None:
        return None, warn or "unreadable"
    try:
        return json.loads(text), warn
    except Exception as exc:  # noqa: BLE001 - degrade, never crash
        return None, "invalid JSON: %s" % exc


def _pformfeed_count(data):
    return data.count(b"\f")


def extract_pdf(src, texts_dir, rel):
    """Extract a PDF to a page-marked UTF-8 text file without shell pipes.

    The output name is a deterministic function of ``rel``, so re-running
    discover in the same work directory overwrites instead of appending
    ``-2``, ``-3`` suffixes (which would break run-to-run reproducibility).
    """
    src = Path(src)
    texts_dir = Path(texts_dir)
    texts_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem or "doc"
    h = _rel_hash(rel)
    out_name = texts_dir / ("%s__%s.txt" % (h, stem))
    raw_name = texts_dir / ("%s__%s.raw.tmp" % (h, stem))
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return {
            "status": "needs_ocr",
            "pages": 0,
            "chars": 0,
            "lines": 0,
            "text_file": None,
            "degradation": "pdftotext not found on PATH",
        }
    try:
        run = subprocess.run(
            [pdftotext, "-enc", "UTF-8", str(src), str(raw_name)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "pages": 0,
            "chars": 0,
            "lines": 0,
            "text_file": None,
            "degradation": "pdftotext error: %s" % exc,
        }
    if run.returncode != 0 or not Path(raw_name).exists():
        return {
            "status": "failed",
            "pages": 0,
            "chars": 0,
            "lines": 0,
            "text_file": None,
            "degradation": "pdftotext exited %d" % run.returncode,
        }
    raw = Path(raw_name).read_bytes()
    pages = raw.split(b"\f")
    if pages and pages[-1] == b"":
        pages = pages[:-1]
    total_chars = sum(len(p.strip()) for p in pages)
    with open(out_name, "w", encoding="utf-8") as fh:
        for idx, page in enumerate(pages, 1):
            fh.write("----- PAGE %d / %d -----\n" % (idx, len(pages)))
            fh.write(page.decode("utf-8", "replace").strip("\n"))
            fh.write("\n")
    try:
        Path(raw_name).unlink()
    except OSError:
        pass
    status = "ok" if total_chars > 0 else "needs_ocr"
    return {
        "status": status,
        "pages": len(pages),
        "chars": total_chars,
        "lines": 0,
        "text_file": str(Path(texts_dir.name) / Path(out_name).name),
        "degradation": None if total_chars > 0 else "extracted text is empty (scanned PDF?)",
    }


def _rel_hash(rel):
    """Deterministic 8-hex hash of a material's input-relative path."""
    return hashlib.sha256(str(rel).replace("\\", "/").encode("utf-8")).hexdigest()[:8]


def discover(input_dir, work_dir):
    """Scan input, extract material text, and write work/manifest.json."""
    inp = Path(input_dir)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    texts_dir = work / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)

    errors = []
    degradations = []
    case = {}
    task_md = ""

    if not inp.exists():
        errors.append("input directory does not exist: %s" % inp)
        manifest = _build_manifest(
            inp, work, case, task_md, [], errors, degradations, fatal=True
        )
        _write_json(work / "manifest.json", manifest)
        return manifest

    case_path = inp / "case.json"
    if case_path.is_file():
        case, warn = load_json_lenient(case_path)
        if case is None:
            errors.append("case.json unreadable: %s" % warn)
            case = {}
        elif warn:
            degradations.append("case.json: %s" % warn)
        if not isinstance(case, dict):
            errors.append("case.json is not a JSON object")
            case = {}
    else:
        degradations.append("case.json missing")

    task_path = inp / "task.md"
    if task_path.is_file():
        text, warn = read_text(task_path)
        task_md = text or ""
        if warn:
            degradations.append("task.md: %s" % warn)
    else:
        degradations.append("task.md missing")

    materials = []
    for p in sorted(inp.rglob("*")):
        if not p.is_file():
            continue
        if p.name in META_NAMES:
            continue
        if p.suffix.lower() not in MATERIAL_EXTS:
            continue
        rel = str(p.relative_to(inp))
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            degradations.append("skipped large material: %s (%d bytes)" % (rel, size))
            continue
        entry = {
            "path": rel,
            "kind": "pdf" if p.suffix.lower() == ".pdf" else "text",
            "bytes": size,
            "pages": None,
            "chars": 0,
            "lines": None,
            "status": "ok",
            "text_file": None,
            "degradation": None,
        }
        if entry["kind"] == "pdf":
            info = extract_pdf(p, texts_dir, rel)
            entry.update(info)
        else:
            text, warn = read_text(p, max_bytes=MAX_FILE_BYTES)
            if text is None:
                entry["status"] = "failed"
                entry["degradation"] = warn
            else:
                name = texts_dir / ("%s__%s" % (_rel_hash(rel), p.name))
                with open(name, "w", encoding="utf-8") as fh:
                    fh.write(text)
                entry["chars"] = len(text)
                entry["lines"] = text.count("\n") + 1
                entry["text_file"] = str(Path(texts_dir.name) / name.name)
                if warn:
                    entry["degradation"] = warn
                    degradations.append("%s: %s" % (rel, warn))
        materials.append(entry)

    manifest = _build_manifest(inp, work, case, task_md, materials, errors, degradations, fatal=False)
    _write_json(work / "manifest.json", manifest)
    return manifest


def _build_manifest(inp, work, case, task_md, materials, errors, degradations, fatal):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    if not isinstance(contract, dict):
        contract = {}
    case_id = (
        (case.get("case_id") if isinstance(case, dict) else None)
        or (case.get("id") if isinstance(case, dict) else None)
        or "unknown_case"
    )
    return {
        "skill": SKILL_NAME,
        "version": VERSION,
        "input_dir": str(inp),
        "work_dir": str(work),
        "case_id": case_id,
        "output_contract": copy.deepcopy(contract),
        "task_md": task_md,
        "materials": materials,
        "errors": errors,
        "degradations": degradations,
        "fatal": bool(fatal),
    }


def _write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _load_json(path):
    return load_json_lenient(path)[0]


def _runtime_block(manifest):
    materials = manifest.get("materials", [])
    total_pages = sum(m.get("pages") or 0 for m in materials)
    total_chars = sum(m.get("chars") or 0 for m in materials)
    return {
        "engine": SKILL_NAME,
        "script_version": VERSION,
        "python": sys.version.split()[0],
        "deterministic": True,
        "budget": {"seconds": 900, "model_requests": 64},
        "stats": {
            "materials": len(materials),
            "pages": total_pages,
            "chars": total_chars,
        },
    }


def compute_status(manifest, meta, finding_count):
    if manifest.get("fatal"):
        return "input_error", "unchecked"
    has_material = any(
        m.get("status") in ("ok", "needs_ocr", "failed") for m in manifest.get("materials", [])
    )
    if not has_material:
        return "insufficient_evidence", "insufficient"
    if isinstance(meta, dict) and meta.get("overall_verdict"):
        final = bool(meta.get("final", True))
        return ("ok" if final else "partial"), meta["overall_verdict"]
    if finding_count:
        return "partial", "issues_found"
    return "partial", "unchecked"


def scaffold(input_dir, output_dir, work_dir):
    """Write a valid partial result.json skeleton. Never raises on bad input."""
    manifest = _load_json(Path(work_dir) / "manifest.json")
    if not isinstance(manifest, dict):
        manifest = discover(input_dir, work_dir)
    status, verdict = compute_status(manifest, None, 0)
    result = {
        "case_id": manifest.get("case_id") or "unknown_case",
        "status": status,
        "output_contract": copy.deepcopy(manifest.get("output_contract", {})),
        "summary": {
            "verdict": verdict,
            "counts": {
                "total": 0,
                "critical": 0,
                "major": 0,
                "minor": 0,
                "suggestion": 0,
                "by_category": {c: 0 for c in CATEGORIES},
            },
        },
        "review_report": {"findings": []},
        "evidence": {"evidence_map": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": list(manifest.get("errors", [])),
        "degradations": list(manifest.get("degradations", [])),
        "change_log": [],
        "runtime": _runtime_block(manifest),
    }
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "result.json", result)
    state = {
        "stage": "scaffold",
        "findings_written": 0,
        "finalize_count": 0,
        "version": VERSION,
    }
    _write_json(Path(work_dir) / "state.json", state)
    return result


def normalize_category(value):
    key = str(value or "").strip()
    if key in CATEGORIES:
        return key
    return CATEGORY_ALIASES.get(key, key)


def stable_finding_id(finding):
    """Content-hashed id so a finding keeps the same id across runs."""
    base = "|".join(
        str(finding.get(k, "")) for k in ("category", "locator", "quote")
    )
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:8].upper()
    return "F-" + digest


def _load_findings(work_dir):
    fdir = Path(work_dir) / "findings"
    loaded = []
    load_errors = []
    if not fdir.is_dir():
        return loaded, load_errors
    for p in sorted(fdir.glob("*.json")):
        obj, err = load_json_lenient(p)
        if obj is None or not isinstance(obj, dict):
            load_errors.append("%s: %s" % (p.name, err or "not a JSON object"))
            continue
        obj["_file"] = p.name
        loaded.append(obj)
    return loaded, load_errors


def _finding_sort_key(finding):
    cat = normalize_category(finding.get("category", ""))
    cat_idx = CATEGORIES.index(cat) if cat in CATEGORIES else len(CATEGORIES)
    sev = SEVERITY_RANK.get(str(finding.get("severity", "")).strip(), 9)
    return (cat_idx, sev, str(finding.get("locator", "")), str(finding.get("id", "")))


def finalize(input_dir, output_dir, work_dir):
    """Merge findings + meta.json into result.json and report.md."""
    work = Path(work_dir)
    manifest = _load_json(work / "manifest.json")
    if not isinstance(manifest, dict):
        manifest = discover(input_dir, work_dir)
    meta = _load_json(work / "meta.json")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}

    raw_findings, load_errors = _load_findings(work)
    findings = []
    seen_ids = set()
    for raw in raw_findings:
        finding = copy.deepcopy(raw)
        finding.pop("_file", None)
        missing = [k for k in REQUIRED_FINDING_KEYS if not finding.get(k)]
        if missing:
            load_errors.append("%s: missing keys %s" % (raw.get("_file", "?"), ",".join(missing)))
            continue
        finding["category"] = normalize_category(finding.get("category", ""))
        if finding.get("id"):
            fid = str(finding["id"])
            if fid in seen_ids:
                fid = fid + "-" + str(len([i for i in seen_ids if i == fid]) + 1)
            finding["id"] = fid
        else:
            finding["id"] = stable_finding_id(finding)
        seen_ids.add(finding["id"])
        if not finding.get("finding_type"):
            finding["finding_type"] = "unspecified"
        findings.append(finding)

    findings.sort(key=_finding_sort_key)

    counts = {
        "total": len(findings),
        "critical": 0,
        "major": 0,
        "minor": 0,
        "suggestion": 0,
        "by_category": {c: 0 for c in CATEGORIES},
    }
    for f in findings:
        sev = str(f.get("severity", "")).strip()
        if sev in counts:
            counts[sev] += 1
        cat = f.get("category")
        if cat in counts["by_category"]:
            counts["by_category"][cat] += 1

    status, verdict = compute_status(manifest, meta, len(findings))

    evidence_map = []
    for f in findings:
        evidence_map.append(
            {
                "finding_id": f["id"],
                "category": f.get("category"),
                "locator": f.get("locator"),
                "quote": f.get("quote"),
                "basis": f.get("basis"),
                "source_ref": f.get("source_ref"),
                "severity": f.get("severity"),
                "verdict": f.get("verdict"),
            }
        )

    def field(name, default):
        value = meta.get(name, default)
        return value if isinstance(value, list) else default

    result = {
        "case_id": manifest.get("case_id") or "unknown_case",
        "status": status,
        "output_contract": copy.deepcopy(manifest.get("output_contract", {})),
        "summary": {"verdict": verdict, "counts": counts},
        "review_report": {"findings": findings},
        "evidence": {"evidence_map": evidence_map},
        "sources": field("sources", []),
        "uncertainty": field("uncertainty", []),
        "pending_human": field("pending_human", []),
        "limitations": field("limitations", []),
        "errors": list(manifest.get("errors", [])) + load_errors + field("errors", []),
        "degradations": list(manifest.get("degradations", [])) + field("degradations", []),
        "change_log": field("change_log", []),
        "runtime": _runtime_block(manifest),
    }

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "result.json", result)
    (out / "report.md").write_text(render_report(result), encoding="utf-8")

    state = _load_json(work / "state.json") or {}
    if not isinstance(state, dict):
        state = {}
    state["stage"] = "finalize"
    state["findings_written"] = len(findings)
    state["finalize_count"] = int(state.get("finalize_count", 0)) + 1
    state["version"] = VERSION
    _write_json(work / "state.json", state)
    return result


def render_report(result):
    lines = []
    lines.append("# 引用与事实核验报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- verdict: `%s`" % result["summary"].get("verdict"))
    lines.append("- generated_by: %s v%s" % (SKILL_NAME, VERSION))
    lines.append("")
    counts = result["summary"]["counts"]
    lines.append("## 摘要")
    lines.append("")
    lines.append(
        "total=%s critical=%s major=%s minor=%s suggestion=%s"
        % (counts["total"], counts["critical"], counts["major"], counts["minor"], counts["suggestion"])
    )
    lines.append("")
    lines.append("## 问题清单")
    lines.append("")
    findings = result["review_report"]["findings"]
    if not findings:
        lines.append("（无已落盘的审查结论，或材料不足以形成结论。）")
        lines.append("")
    current = None
    for f in findings:
        cat = f.get("category", "other")
        if cat != current:
            current = cat
            label = CATEGORY_LABELS.get(cat, cat)
            lines.append("### %s（%s）" % (cat, label))
            lines.append("")
        lines.append("#### [%s] %s / %s — %s" % (f.get("id"), f.get("severity"), f.get("verdict"), f.get("locator")))
        lines.append("")
        lines.append("- 原文（逐字）: `%s`" % f.get("quote"))
        lines.append("- 转述: %s" % (f.get("claim") or "（未填写）"))
        lines.append("- 判定依据: %s" % (f.get("basis") or "（未填写）"))
        lines.append("- 来源: %s" % (f.get("source_ref") or "not_verified"))
        lines.append("- 建议: %s" % f.get("suggestion"))
        if f.get("review_status") == "needs_review":
            lines.append("- 待复核: 是")
        lines.append("")
    for section, title in (
        ("sources", "来源"),
        ("uncertainty", "不确定性"),
        ("pending_human", "待人工复核"),
        ("limitations", "限制"),
        ("errors", "错误"),
        ("degradations", "降级"),
        ("change_log", "变更记录"),
    ):
        items = result.get(section, [])
        lines.append("## %s" % title)
        lines.append("")
        if items:
            for item in items:
                if isinstance(item, dict):
                    lines.append("- " + json.dumps(item, ensure_ascii=False))
                else:
                    lines.append("- " + str(item))
        else:
            lines.append("（无）")
        lines.append("")
    return "\n".join(lines)


def selftest():
    """Internal self-checks. Returns (passed_count, failed_messages)."""
    failures = []

    # 1. parser help is available (mirrors `--help` exit code 0).
    parser = build_parser()
    if not parser.format_help().startswith("usage:"):
        failures.append("argparse help is malformed")

    tmp = Path(__import__("tempfile").mkdtemp(prefix="cfaselftest-"))
    try:
        # 2. Empty input -> insufficient_evidence, parseable result.
        empty_in = tmp / "empty_in"
        empty_in.mkdir()
        work = tmp / "w_empty"
        out = tmp / "o_empty"
        discover(empty_in, work)
        scaffold(empty_in, out, work)
        finalize(empty_in, out, work)
        res = json.loads((out / "result.json").read_text(encoding="utf-8"))
        if res.get("status") != "insufficient_evidence":
            failures.append("empty input status=%s" % res.get("status"))

        # 3. Bad JSON case.json -> no crash, errors recorded, material still read.
        bad_in = tmp / "bad_in"
        bad_in.mkdir()
        (bad_in / "case.json").write_text("{ not json", encoding="utf-8")
        (bad_in / "material.md").write_text("# Title\n\nClaim without support.\n", encoding="utf-8")
        work2 = tmp / "w_bad"
        out2 = tmp / "o_bad"
        discover(bad_in, work2)
        scaffold(bad_in, out2, work2)
        finalize(bad_in, out2, work2)
        res2 = json.loads((out2 / "result.json").read_text(encoding="utf-8"))
        if res2.get("status") not in ("partial", "ok"):
            failures.append("bad-json status=%s" % res2.get("status"))
        if not any("case.json" in str(e) for e in res2.get("errors", [])):
            failures.append("bad-json error not recorded")

        # 4. Determinism: two finalize runs on identical inputs -> byte-identical.
        det_in = tmp / "det_in"
        det_in.mkdir()
        (det_in / "case.json").write_text(
            json.dumps({"case_id": "CASE-DET-01", "output_contract": {"required_outputs": ["result.json"], "note": "keep me"}}),
            encoding="utf-8",
        )
        (det_in / "paper.md").write_text("# Study\n\nP1: method described.\nP2: N=20, p<0.05.\n", encoding="utf-8")
        work_a = tmp / "w_det_a"
        work_b = tmp / "w_det_b"
        out_a = tmp / "o_det_a"
        out_b = tmp / "o_det_b"
        for wk in (work_a, work_b):
            discover(det_in, wk)
            scaffold(det_in, out_a if wk == work_a else out_b, wk)
            fdir = wk / "findings"
            fdir.mkdir()
            (fdir / "f1.json").write_text(
                json.dumps({
                    "category": "citation_support",
                    "finding_type": "unsupported_claim",
                    "severity": "major",
                    "verdict": "unsupported",
                    "locator": "paper.md, para 2",
                    "quote": "Claim without support.",
                    "claim": "The claim lacks a citation.",
                    "basis": "No reference is attached to the sentence.",
                    "source_ref": "not_verified",
                    "suggestion": "Add a supporting reference.",
                    "confidence": "medium",
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            (wk / "meta.json").write_text(
                json.dumps({"overall_verdict": "issues_found", "final": True, "change_log": [{"stage": "review", "summary": "one finding"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
        finalize(det_in, out_a, work_a)
        finalize(det_in, out_b, work_b)
        ra = (out_a / "result.json").read_bytes()
        rb = (out_b / "result.json").read_bytes()
        if ra != rb:
            failures.append("result.json differs between identical runs")
        ma = (out_a / "report.md").read_bytes()
        mb = (out_b / "report.md").read_bytes()
        if ma != mb:
            failures.append("report.md differs between identical runs")
        det = json.loads(ra.decode("utf-8"))
        if det.get("output_contract", {}).get("required_outputs") != ["result.json"]:
            failures.append("output_contract not echoed verbatim")
        if det["summary"]["counts"]["total"] != 1:
            failures.append("finding count mismatch")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return len(failures) == 0, failures


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic helper pipeline for %s" % SKILL_NAME,
        epilog="commands: discover | scaffold | finalize | selftest "
               "(default: discover + scaffold + finalize)",
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="input directory (default /input)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="output directory (default /app/output)")
    parser.add_argument("--work", default=DEFAULT_WORK, help="work directory (default /app/work)")
    parser.add_argument("--version", action="version", version="%s %s" % (SKILL_NAME, VERSION))
    parser.add_argument(
        "command",
        nargs="?",
        choices=["discover", "scaffold", "finalize", "selftest"],
        default=None,
        help="subcommand; omit to run the full deterministic sweep",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "discover":
        discover(args.input, args.work)
        print("discover done -> %s" % (Path(args.work) / "manifest.json"))
    elif args.command == "scaffold":
        scaffold(args.input, args.output, args.work)
        print("scaffold done -> %s" % (Path(args.output) / "result.json"))
    elif args.command == "finalize":
        finalize(args.input, args.output, args.work)
        print("finalize done -> %s" % (Path(args.output) / "result.json"))
    elif args.command == "selftest":
        ok, failures = selftest()
        if ok:
            print("selftest: OK")
            return 0
        eprint("selftest FAILED:")
        for f in failures:
            eprint("  - " + f)
        return 1
    else:
        # Default sweep: discover + scaffold + finalize in one deterministic pass.
        discover(args.input, args.work)
        scaffold(args.input, args.output, args.work)
        result = finalize(args.input, args.output, args.work)
        print(
            "sweep done: status=%s verdict=%s findings=%d -> %s"
            % (result["status"], result["summary"]["verdict"], result["summary"]["counts"]["total"], Path(args.output) / "result.json")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
