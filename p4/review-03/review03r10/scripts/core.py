"""core.py — stdlib-only review engine for the research-methods & statistics audit skill.

This module implements a small, deterministic state machine that the Skill
drives in five named stages:

    scaffold  ->  probe  ->  commit*  ->  finalize  ->  selftest

Design choices (deliberately different from a discover/scaffold/finalize
pipeline):

  * contract-first ordering — ``scaffold`` writes a legal ``result.json``
    envelope *before* any material is read, so a parseable partial deliverable
    exists on disk within the first minutes even if discovery later fails;
  * one unified intermediate representation — a single ``work/ledger.json``
    (segments + term hits + numeric candidates + sources) replaces separate
    ``manifest.json`` / ``terms.json`` / per-finding files;
  * validate-as-you-write — every mutation re-validates the document it
    changes, instead of validating only at the end;
  * rendered-report guarantee — ``report.md`` is rendered deterministically
    FROM ``result.json``, so the human report and the machine record can never
    disagree.

Only the Python standard library is used.
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
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (single source of truth, shared with references/*.md)
# ---------------------------------------------------------------------------

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

FACETS = {
    "research_design",
    "variable_definition",
    "statistical_methods",
    "sample_size_power",
    "results_reporting",
}

CHECK_CLASSES = {
    "fact_check",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argumentation_chain",
    "academic_norms",
}

FINDING_TYPES = {
    "factual_error",
    "insufficient_evidence",
    "normative_issue",
    "advisory",
    "pending_review",
}

SEVERITIES = {"critical", "major", "minor", "advisory", "pending"}

RESULT_STATUSES = {"通过", "问题", "待复核", "未检查"}
VERDICTS = {"通过", "问题", "待复核", "未检查", "材料不足", "输入错误"}

FINDING_REQUIRED = {
    "id",
    "facet",
    "locator",
    "quote",
    "severity",
    "suggestion",
}

# top-level keys the engine itself manages; every other key found in a previous
# result.json is preserved verbatim on later writes.
MANAGED_KEYS = {
    "case_id",
    "status",
    "summary",
    "review_report",
    "result_status",
    "evidence",
    "sources",
    "uncertainty",
    "pending_human",
    "limitations",
    "errors",
    "degradations",
    "change_log",
    "runtime",
    "output_contract",
}

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB per input file

TERM_PATTERNS = {
    "p_value": r"[Pp]\s*[=<>≤≥]\s*0?\.\d+",
    "confidence_interval": r"(?:95\s*%?\s*)?(?:CI|置信区间|confidence\s+interval)",
    "significance_phrase": r"(?:significant|not\s+significant|n\.?s\.?|显著|不显著|无统计学意义|有统计学意义)",
    "sample_size": r"[Nn]\s*=\s*\d+",
    "t_test": r"(?:t[ -]?test|Student'?s?\s+t|t\s*=\s*-?\d+\.?\d*)",
    "anova": r"(?:ANOVA|analysis\s+of\s+variance|方差分析|F\s*=\s*\d)",
    "chi_square": r"(?:chi[ -]?square|χ2|χ²|卡方)",
    "regression": r"(?:regression|logistic|Cox|回归|log[ -]?rank)",
    "nonparametric": r"(?:Mann[ -]?Whitney|Wilcoxon|Kruskal|非参数)",
    "multiple_comparison": r"(?:Bonferroni|Holm|FDR|multiple\s+comparison|多重比较|校正)",
    "effect_size": r"(?:effect\s+size|Cohen'?s|OR\s*=|HR\s*=|RR\s*=|效应量)",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def _read_text(path: Path) -> str:
    """Read a text file tolerantly (BOM, common encodings)."""
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace")


def _load_json(path: Path):
    """Load JSON tolerantly; returns None on any failure."""
    if not path.exists():
        return None
    try:
        return json.loads(_read_text(path))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _dump_json(path: Path, obj) -> bool:
    """Atomically write JSON (tmp + os.replace); never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _log_change(result: dict, event: str) -> None:
    stage = result.get("_stage", "unknown")
    entries = [e for e in result.get("change_log", []) if e.get("stage") != stage]
    entries.append({"at": now_iso(), "event": event, "stage": stage})
    result["change_log"] = entries


def _zero_counts() -> dict:
    return {
        "total": 0,
        "critical": 0,
        "major": 0,
        "minor": 0,
        "advisory": 0,
        "pending": 0,
    }


# ---------------------------------------------------------------------------
# Contract / skeleton
# ---------------------------------------------------------------------------


def _extract_case_id(case: dict, input_dir: Path) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id", "caseId"):
            v = case.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        nested = case.get("case")
        if isinstance(nested, dict):
            v = nested.get("id")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return input_dir.name or "unknown"


def _default_for_value(v):
    if isinstance(v, (list, tuple)):
        return []
    if isinstance(v, dict):
        return {}
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return None
    return ""


def _apply_contract(result: dict, contract: dict) -> dict:
    """Spread output_contract fields at top level, keep a verbatim copy too.

    Colliding keys resolve in favour of the engine-managed fields (honest
    status/case_id are never overwritten); the verbatim contract is preserved
    under ``output_contract`` for auditability.
    """
    if not isinstance(contract, dict):
        return result
    clean = {}
    for k, v in contract.items():
        if k in MANAGED_KEYS or k == "_stage":
            continue
        if v is None or v == "" or v == [] or v == {}:
            clean[k] = _default_for_value(v)
        else:
            clean[k] = v
    result.update(clean)
    result["output_contract"] = json.loads(json.dumps(contract, ensure_ascii=False))
    return result


def build_skeleton(case_id: str, contract: dict) -> dict:
    result: dict = {
        "case_id": case_id,
        "status": "partial",
        "summary": {
            "verdict": "未检查",
            "counts": _zero_counts(),
            "by_facet": {f: 0 for f in sorted(FACETS)},
            "by_type": {t: 0 for t in sorted(FINDING_TYPES)},
        },
        "review_report": {
            "findings": [],
            "coverage": {
                "checked_facets": [],
                "unchecked_facets": sorted(FACETS),
                "check_classes": [],
            },
        },
        "result_status": "未检查",
        "evidence": {
            "evidence_map": {},
            "segments_index": "/app/work/segments.json",
            "numeric_checks": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {
            "started_at": now_iso(),
            "updated_at": now_iso(),
            "engine": "stdlib-only",
        },
        "_stage": "scaffolded",
    }
    _apply_contract(result, contract)
    _log_change(result, "scaffold: skeleton written (contract-first)")
    return result


# ---------------------------------------------------------------------------
# Discovery / extraction
# ---------------------------------------------------------------------------


def _safe_relname(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base)).replace("\\", "/")
    except ValueError:
        return path.name


def _discover_files(input_dir: Path):
    files, skipped = [], []
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = Path(root) / name
            if not p.is_file():
                continue
            if p.stat().st_size > MAX_FILE_BYTES:
                skipped.append(_safe_relname(p, input_dir))
                continue
            files.append(p)
    return sorted(files, key=lambda p: _safe_relname(p, input_dir)), skipped


def _extract_docx(path: Path) -> str:
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    out = []
    for para in root.iter(ns + "p"):
        texts = [t.text or "" for t in para.iter(ns + "t")]
        out.append("".join(texts))
    return "\n".join(out)


def _extract_pdf(path: Path, texts_dir: Path) -> tuple[str, list]:
    """Extract PDF text via pdftotext if available; returns (text, degradations)."""
    degradations = []
    exe = shutil.which("pdftotext")
    if not exe:
        return "", [f"pdftotext not found; {_basename(path)} marked needs_ocr"]
    out = texts_dir / (path.stem + ".pdf.txt")
    try:
        cp = subprocess.run(
            [exe, "-layout", str(path), str(out)],
            capture_output=True,
            timeout=120,
        )
        if cp.returncode != 0:
            msg = (cp.stderr or b"").decode("utf-8", "replace").strip()
            return "", [f"pdftotext failed on {_basename(path)}: {msg[:120]}"]
        text = _read_text(out)
        if not text.strip():
            return "", [f"{_basename(path)} produced no text; needs_ocr"]
        return text, degradations
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", [f"pdftotext error on {_basename(path)}: {exc}"]


def _basename(path: Path) -> str:
    return path.name


def _extract_text(path: Path, texts_dir: Path):
    """Return (text, kind, degradations)."""
    degradations = []
    suf = path.suffix.lower()
    if suf == ".pdf":
        text, deg = _extract_pdf(path, texts_dir)
        return text, "pdf", deg
    if suf == ".docx":
        try:
            return _extract_docx(path), "docx", []
        except Exception as exc:  # noqa: BLE001
            return "", "docx", [f"docx parse failed on {_basename(path)}: {exc}"]
    if suf in {".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml", ".tex"}:
        try:
            return _read_text(path), "text", []
        except OSError as exc:
            return "", "text", [f"read failed on {_basename(path)}: {exc}"]
    return "", "unknown", [f"unsupported type {suf or '(none)'} for {_basename(path)}"]


def _paragraphs(page_text: str):
    """Split one page/unit into paragraph units, preserving verbatim text."""
    page_text = page_text.strip("\n")
    if not page_text:
        return []
    blocks = [b.strip() for b in re.split(r"\n\s*\n", page_text) if b.strip()]
    if len(blocks) <= 1:
        blocks = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    return blocks


def _build_segments(text, source, kind, start_id):
    segments = []
    idx = start_id
    if kind == "pdf":
        pages = text.split("\f")
        for pno, page in enumerate(pages, 1):
            for para_no, block in enumerate(_paragraphs(page), 1):
                idx += 1
                segments.append(
                    {
                        "id": f"S{idx:04d}",
                        "source": source,
                        "kind": kind,
                        "page": pno,
                        "para": para_no,
                        "locator": f"p.{pno} ¶{para_no}",
                        "text": block,
                    }
                )
    else:
        for para_no, block in enumerate(_paragraphs(text), 1):
            idx += 1
            segments.append(
                {
                    "id": f"S{idx:04d}",
                    "source": source,
                    "kind": kind,
                    "page": None,
                    "para": para_no,
                    "locator": f"¶{para_no}",
                    "text": block,
                }
            )
    return segments


def _scan_terms(segments):
    hits = {term: [] for term in TERM_PATTERNS}
    for seg in segments:
        for term, pat in TERM_PATTERNS.items():
            if re.search(pat, seg["text"], re.I):
                hits[term].append(seg["id"])
    return hits


def _excerpt(text: str, length: int = 220) -> str:
    t = " ".join(text.split())
    return t[:length] + ("…" if len(t) > length else "")


def _scan_numeric(segments):
    checks = []
    cid = 0

    def add(rule, seg, note):
        nonlocal cid
        cid += 1
        checks.append(
            {
                "id": f"NC{cid:03d}",
                "rule": rule,
                "locator": seg["locator"],
                "source": seg["source"],
                "text_excerpt": _excerpt(seg["text"]),
                "status": "candidate",
                "disposition": None,
                "note": note,
            }
        )

    # Rule 1: within one segment, p-value vs. significance wording.
    for seg in segments:
        text = seg["text"]
        pvals = re.findall(r"[Pp]\s*=\s*(0?\.\d+)", text)
        if not pvals:
            continue
        sig = bool(re.search(r"(显著|significant|有统计学意义)", text, re.I))
        nsig = bool(re.search(r"(不显著|无统计学意义|not\s+significant|n\.?s\.?)", text, re.I))
        if sig == nsig:  # both or neither -> wording ambiguous
            continue
        for pv in pvals:
            try:
                p = float(pv)
            except ValueError:
                continue
            if p < 0.05 and nsig:
                add("p_vs_wording", seg, f"p={pv} (<0.05) but wording says non-significant")
            elif p >= 0.05 and sig:
                add("p_vs_wording", seg, f"p={pv} (>=0.05) but wording claims significant")

    # Rule 2: a segment carrying both a p-value and a 95% CI -> needs manual check.
    for seg in segments:
        if re.search(r"[Pp]\s*=\s*\d", seg["text"]) and re.search(
            r"(?:95\s*%?\s*)?(?:CI|置信区间|confidence\s+interval)", seg["text"], re.I
        ):
            add("p_and_ci_together", seg, "p-value and 95% CI appear together; confirm they describe the same outcome")

    # Rule 3: multiple distinct N= values across the whole material.
    n_items = []
    for seg in segments:
        for m in re.finditer(r"[Nn]\s*=\s*(\d+)", seg["text"]):
            n_items.append((int(m.group(1)), seg["locator"], seg["source"]))
    distinct = sorted({v for v, _, _ in n_items})
    if len(distinct) > 1:
        locs = []
        seen = set()
        for v, loc, _src in n_items:
            label = f"{loc}: N={v}"
            if label not in seen:
                seen.add(label)
                locs.append(label)
        note = "distinct N values at " + "; ".join(locs[:8]) + ("; …" if len(locs) > 8 else "")
        first = n_items[0]
        checks.append(
            {
                "id": f"NC{cid + 1:03d}",
                "rule": "multiple_n_values",
                "locator": first[1],
                "source": first[2],
                "text_excerpt": "see note",
                "status": "candidate",
                "disposition": None,
                "note": note,
            }
        )
        cid += 1
    return checks


def cmd_probe(input_dir: str, work_dir: str) -> int:
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    texts_dir = work_dir / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)

    files, skipped = _discover_files(input_dir)
    control_names = {"case.json", "task.md"}
    files = [
        p for p in files
        if not (p.parent == input_dir and p.name in control_names)
    ]
    sources, segments, degradations, errors = [], [], [], []
    sid = 0

    for p in files:
        rel = _safe_relname(p, input_dir)
        text, kind, deg = _extract_text(p, texts_dir)
        degradations.extend(deg)
        if text.strip():
            segs = _build_segments(text, rel, kind, sid)
            segments.extend(segs)
            sid += len(segs)
            sources.append({"path": rel, "kind": kind, "segments": len(segs), "status": "extracted"})
        else:
            sources.append({"path": rel, "kind": kind, "segments": 0, "status": "unreadable"})
            errors.append(f"no usable text from {rel}")

    if not files and not skipped:
        errors.append("no material files found under /input")

    ledger = {
        "case_materials": sources,
        "segments": segments,
        "term_hits": _scan_terms(segments),
        "numeric_checks": _scan_numeric(segments),
        "skipped_large": skipped,
        "degradations": degradations,
        "errors": errors,
        "probed_at": now_iso(),
    }
    _dump_json(work_dir / "ledger.json", ledger)
    _dump_json(work_dir / "segments.json", segments)
    _dump_json(
        work_dir / "texts_index.json",
        {s["path"]: {"kind": s["kind"], "segments": s["segments"]} for s in sources},
    )

    print(f"probe: {len(files)} files, {len(segments)} segments, "
          f"{len(ledger['numeric_checks'])} numeric candidates, "
          f"{len(degradations)} degradations, {len(errors)} errors")
    print(f"ledger: {work_dir / 'ledger.json'}")
    print(f"segments: {work_dir / 'segments.json'}")
    return 0


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------


def cmd_scaffold(input_dir: str, output_dir: str, work_dir: str) -> int:
    input_dir, output_dir, work_dir = Path(input_dir), Path(output_dir), Path(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    case = _load_json(input_dir / "case.json") or {}
    case_id = _extract_case_id(case, input_dir)
    contract = case.get("output_contract", {}) if isinstance(case, dict) else {}
    if not isinstance(contract, dict):
        contract = {}

    result = build_skeleton(case_id, contract)
    input_errors = []
    if not isinstance(case, dict) or not case:
        input_errors.append("case.json missing or unparseable")
        result["errors"] = list(input_errors)
        # status stays partial here; finalize decides input_error vs
        # insufficient_evidence once it knows whether materials exist.

    _dump_json(output_dir / "result.json", result)
    _dump_json(
        work_dir / "state.json",
        {
            "seq": 1,
            "case_id": case_id,
            "contract_keys": sorted(contract.keys()),
            "input_errors": input_errors,
            "stage": "scaffolded",
            "updated_at": now_iso(),
        },
    )
    print(f"scaffold: result.json written (case_id={case_id}, "
          f"contract_keys={sorted(contract.keys())}, status={result['status']})")
    return 0


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _validate_finding(f: dict, idx: int) -> list[str]:
    errs = []
    for k in FINDING_REQUIRED:
        v = f.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(f"finding#{idx} missing/empty required field '{k}'")
    facet = f.get("facet")
    if facet not in FACETS:
        errs.append(f"finding#{idx} invalid facet '{facet}'")
    ftype = f.get("finding_type")
    if ftype is not None and ftype not in FINDING_TYPES:
        errs.append(f"finding#{idx} invalid finding_type '{ftype}'")
    sev = f.get("severity")
    if sev not in SEVERITIES:
        errs.append(f"finding#{idx} invalid severity '{sev}'")
    return errs


def _recount(result: dict) -> None:
    findings = result.get("review_report", {}).get("findings", [])
    counts = _zero_counts()
    by_facet = {f: 0 for f in sorted(FACETS)}
    by_type = {t: 0 for t in sorted(FINDING_TYPES)}
    for f in findings:
        counts["total"] += 1
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        by_facet[f.get("facet")] = by_facet.get(f.get("facet"), 0) + 1
        t = f.get("finding_type")
        if t in by_type:
            by_type[t] += 1
    result["summary"]["counts"] = counts
    result["summary"]["by_facet"] = by_facet
    result["summary"]["by_type"] = by_type


def _auto_evidence_map(result: dict) -> None:
    emap = {}
    for f in result.get("review_report", {}).get("findings", []):
        emap[f.get("id", "?")] = {
            "locator": f.get("locator", ""),
            "quote": f.get("quote", ""),
            "facet": f.get("facet", ""),
        }
    result["evidence"]["evidence_map"] = emap


def _compute_status(result: dict) -> str:
    findings = result.get("review_report", {}).get("findings", [])
    sources = result.get("sources", [])
    errors = result.get("errors", [])
    case_json_broken = any("case.json" in str(e) for e in errors)
    if case_json_broken and not sources:
        return "input_error"
    if not sources and not findings:
        return "insufficient_evidence"
    if result.get("summary", {}).get("verdict") == "通过":
        return "ok"
    return "partial"


def _fmt_item(x) -> str:
    if isinstance(x, dict):
        return json.dumps(x, ensure_ascii=False, sort_keys=True)
    return str(x)


def _render_report(result: dict) -> str:
    lines = [
        "# 研究方法与统计审查报告",
        "",
        f"- case_id: `{result.get('case_id', '')}`",
        f"- status: `{result.get('status', '')}`",
        f"- verdict: `{result.get('summary', {}).get('verdict', '')}`",
        f"- result_status: `{result.get('result_status', '')}`",
        "",
    ]
    counts = result.get("summary", {}).get("counts", {})
    lines += [
        "## 概览",
        "",
        f"| 总数 | critical | major | minor | advisory | pending |",
        "|---|---|---|---|---|---|",
        f"| {counts.get('total', 0)} | {counts.get('critical', 0)} | {counts.get('major', 0)} | "
        f"{counts.get('minor', 0)} | {counts.get('advisory', 0)} | {counts.get('pending', 0)} |",
        "",
    ]
    coverage = result.get("review_report", {}).get("coverage", {})
    lines += [
        "已检查面：",
        "",
        "- " + ("、".join(coverage.get("checked_facets", [])) or "（无）"),
        "",
        "未检查面：",
        "",
        "- " + ("、".join(coverage.get("unchecked_facets", [])) or "（无）"),
        "",
    ]
    findings = result.get("review_report", {}).get("findings", [])
    lines += ["## 审查发现", ""]
    if not findings:
        lines += ["（尚无发现条目。）", ""]
    for f in findings:
        lines += [
            f"### {f.get('id', '?')}  ·  {f.get('facet', '')}  ·  {f.get('severity', '')}",
            "",
            f"- 类型：`{f.get('finding_type', '')}`　核对面：`{f.get('check_class', '')}`",
            f"- 定位：`{f.get('locator', '')}`",
            f"- 逐字摘录：",
            "",
            "> " + str(f.get("quote", "")).replace("\n", "\n> "),
            "",
            f"- 判断依据：{f.get('basis', '') or '（未填）'}",
            f"- 修改建议：{f.get('suggestion', '')}",
            "",
        ]
    nc = result.get("evidence", {}).get("numeric_checks", [])
    if nc:
        lines += ["## 数值一致性候选", ""]
        for c in nc:
            lines += [
                f"- `{c.get('id')}` `{c.get('rule')}` `{c.get('status')}` "
                f"disposition=`{c.get('disposition')}`　{c.get('locator')}：{c.get('text_excerpt', '')}",
            ]
        lines += [""]
    for key, title in (
        ("sources", "来源"),
        ("uncertainty", "不确定性"),
        ("pending_human", "待人工复核"),
        ("limitations", "局限"),
        ("errors", "错误"),
        ("degradations", "降级"),
    ):
        items = result.get(key, [])
        lines += [f"## {title}", ""]
        lines += ["- " + (_fmt_item(x) if items else "（无）") for x in items] or ["- （无）"]
        lines += [""]
    lines += ["## 变更记录", ""]
    for c in result.get("change_log", []):
        lines += [f"- {c.get('at', '')}　{c.get('event', '')}"]
    lines += ["", "> 本报告由 result.json 确定性渲染生成，二者内容一致。", ""]
    return "\n".join(lines)


def _validate_document(result: dict) -> list[str]:
    errs = []
    if not isinstance(result.get("case_id"), str) or not result["case_id"]:
        errs.append("case_id missing/empty")
    if result.get("status") not in STATUSES:
        errs.append(f"invalid status '{result.get('status')}'")
    if result.get("result_status") not in RESULT_STATUSES:
        errs.append(f"invalid result_status '{result.get('result_status')}'")
    for i, f in enumerate(result.get("review_report", {}).get("findings", []), 1):
        errs.extend(_validate_finding(f, i))
    cov = result.get("review_report", {}).get("coverage", {})
    covered = set(cov.get("checked_facets", [])) | set(cov.get("unchecked_facets", []))
    missing_facets = FACETS - covered
    if missing_facets:
        errs.append(f"facets not accounted for: {sorted(missing_facets)}")
    return errs


def _write_result_and_report(result: dict, output_dir: Path) -> None:
    result.pop("_stage", None)
    result["runtime"]["updated_at"] = now_iso()
    _dump_json(output_dir / "result.json", result)
    report = _render_report(result)
    (output_dir / "report.md").write_text(report, encoding="utf-8")


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


def cmd_commit(work_dir: str, output_dir: str) -> int:
    work_dir, output_dir = Path(work_dir), Path(output_dir)
    findings_dir = work_dir / "findings"
    result = _load_json(output_dir / "result.json") or {}
    errs = []

    findings = []
    if findings_dir.exists():
        for fp in sorted(findings_dir.glob("*.json")):
            obj = _load_json(fp)
            if obj is None:
                errs.append(f"unreadable finding file {fp.name}")
                continue
            items = obj if isinstance(obj, list) else [obj]
            for it in items:
                if not isinstance(it, dict):
                    errs.append(f"non-object entry in {fp.name}")
                    continue
                if not it.get("id"):
                    it["id"] = f"F{len(findings) + 1:03d}"
                findings.append(it)

    # validate + normalize
    good, dropped = [], []
    for i, f in enumerate(findings, 1):
        verr = _validate_finding(f, i)
        if verr:
            errs.extend(verr)
            dropped.append(f.get("id", f"#{i}"))
            continue
        f.setdefault("finding_type", "pending_review")
        f.setdefault("check_class", "statistical_reporting")
        f.setdefault("confidence", "medium")
        f.setdefault("review_status", "confirmed")
        f.setdefault("basis", "")
        good.append(f)
    good.sort(key=lambda f: (f.get("facet", ""), f.get("id", "")))

    result.setdefault("summary", {}).setdefault("verdict", "未检查")
    result.setdefault("review_report", {})["findings"] = good
    result.setdefault("evidence", {})

    # commit-owned degradations rebuilt idempotently (never accumulate)
    kept = [
        d
        for d in result.get("degradations", [])
        if not (isinstance(d, str) and d.startswith("dropped invalid findings"))
    ]
    if dropped:
        kept.append("dropped invalid findings: " + ", ".join(dropped))
    result["degradations"] = kept

    _dump_json(work_dir / "commit_validation.json", {"errors": errs, "dropped": dropped})
    _recount(result)
    _auto_evidence_map(result)
    result["_stage"] = "committed"
    _log_change(result, f"commit: {len(good)} findings ingested ({len(dropped)} dropped)")
    _write_result_and_report(result, output_dir)

    _dump_json(
        work_dir / "state.json",
        {"seq": 2, "stage": "committed", "findings": len(good), "updated_at": now_iso()},
    )
    print(f"commit: {len(good)} valid findings, {len(dropped)} dropped, "
          f"{len(errs)} validation errors")
    return 0


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


def _dedup(items):
    out, seen = [], set()
    for x in items:
        key = json.dumps(x, ensure_ascii=False, sort_keys=True) if not isinstance(x, str) else x
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def cmd_finalize(work_dir: str, output_dir: str) -> int:
    work_dir, output_dir = Path(work_dir), Path(output_dir)
    result = _load_json(output_dir / "result.json") or {}
    meta = _load_json(work_dir / "meta.json") or {}
    decisions = _load_json(work_dir / "numeric_decisions.json") or {}
    ledger = _load_json(work_dir / "ledger.json") or {}
    state = _load_json(work_dir / "state.json") or {}
    commit_report = _load_json(work_dir / "commit_validation.json") or {}

    # 1) merge model-authored meta (verdict/coverage/assessment)
    if isinstance(meta, dict):
        if meta.get("verdict"):
            result.setdefault("summary", {})["verdict"] = meta["verdict"]
        if meta.get("result_status"):
            result["result_status"] = meta["result_status"]
        cov = result.setdefault("review_report", {}).setdefault("coverage", {})
        cov["checked_facets"] = meta.get("checked_facets", cov.get("checked_facets", []))
        cov["unchecked_facets"] = sorted(
            set(meta.get("unchecked_facets", cov.get("unchecked_facets", [])))
        )
        cov["check_classes"] = meta.get("check_classes", cov.get("check_classes", []))
        if meta.get("overall_assessment"):
            result.setdefault("summary", {})["overall_assessment"] = meta["overall_assessment"]

    # 2) apply numeric-check dispositions (fresh copy from ledger each run)
    if ledger.get("numeric_checks"):
        nc = json.loads(json.dumps(ledger["numeric_checks"], ensure_ascii=False))
        if isinstance(decisions, list):
            dmap = {d.get("id"): d for d in decisions if isinstance(d, dict) and d.get("id")}
            for c in nc:
                d = dmap.get(c.get("id"))
                if d:
                    c["disposition"] = d.get("disposition")
                    c["note"] = d.get("note", c.get("note", ""))
                    c["status"] = "confirmed" if d.get("disposition") == "promote" else c.get("status")
        result.setdefault("evidence", {})["numeric_checks"] = nc
    else:
        result.setdefault("evidence", {}).setdefault("numeric_checks", [])

    # 3) rebuild list fields deterministically from their sources of truth
    meta_errors = meta.get("errors", []) if isinstance(meta, dict) else []
    meta_degradations = meta.get("degradations", []) if isinstance(meta, dict) else []
    input_errors = state.get("input_errors", []) if isinstance(state, dict) else []
    ledger_errors = ledger.get("errors", []) if isinstance(ledger, dict) else []
    ledger_degradations = ledger.get("degradations", []) if isinstance(ledger, dict) else []
    commit_dropped = commit_report.get("dropped", []) if isinstance(commit_report, dict) else []

    if isinstance(meta, dict) and meta.get("sources"):
        result["sources"] = meta["sources"]
    elif ledger.get("case_materials"):
        result["sources"] = ledger["case_materials"]
    else:
        result.setdefault("sources", [])

    if isinstance(meta, dict):
        for k in ("uncertainty", "pending_human", "limitations"):
            if meta.get(k):
                result[k] = meta[k]
            else:
                result.setdefault(k, [])

    degraded = list(ledger_degradations)
    degraded.extend(meta_degradations)
    if commit_dropped:
        degraded.append("dropped invalid findings: " + ", ".join(commit_dropped))
    result["degradations"] = _dedup(degraded)

    errors = list(input_errors)
    errors.extend(ledger_errors)
    errors.extend(meta_errors)
    result["errors"] = errors

    _recount(result)
    _auto_evidence_map(result)

    # 4) honest status
    if isinstance(meta, dict) and meta.get("status") and meta["status"] in STATUSES:
        result["status"] = meta["status"]
    else:
        result["status"] = _compute_status(result)
    if result.get("summary", {}).get("verdict") == "未检查" and not result["review_report"]["findings"]:
        result["result_status"] = "未检查"

    # 5) document-level validation notes (appended after status computed)
    verr = _validate_document(result)
    result["errors"] = _dedup(result["errors"] + verr)

    result["_stage"] = "finalized"
    _log_change(result, f"finalize: {len(result['review_report']['findings'])} findings, status={result['status']}")
    _write_result_and_report(result, output_dir)

    _dump_json(
        work_dir / "state.json",
        {"seq": 3, "stage": "finalized", "status": result["status"],
         "updated_at": now_iso()},
    )
    print(f"finalize: status={result['status']} verdict={result['summary'].get('verdict')} "
          f"findings={len(result['review_report']['findings'])} "
          f"errors={len(result['errors'])}")
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def run_selftest(output_dir: str, work_dir: str = None) -> tuple[bool, list]:
    output_dir = Path(output_dir)
    checks = []
    result = _load_json(output_dir / "result.json")

    def ok(name, detail=""):
        checks.append({"name": name, "pass": True, "detail": detail})

    def bad(name, detail=""):
        checks.append({"name": name, "pass": False, "detail": detail})

    if result is None:
        bad("result.json parseable", "missing or invalid JSON")
        return False, checks
    ok("result.json parseable")

    case_id = result.get("case_id")
    ok("case_id present", str(case_id)) if case_id else bad("case_id present")
    ok("status valid", result.get("status")) if result.get("status") in STATUSES else bad(
        "status valid", f"got {result.get('status')!r}"
    )

    # contract passthrough present
    contract = result.get("output_contract")
    if isinstance(contract, dict):
        ok("output_contract preserved", f"{len(contract)} keys")
    else:
        ok("output_contract preserved", "none supplied")

    for key in ("sources", "uncertainty", "pending_human", "change_log", "limitations"):
        ok(f"field {key} present") if key in result else bad(f"field {key} present")

    findings = result.get("review_report", {}).get("findings", [])
    all_ok = True
    for i, f in enumerate(findings, 1):
        verr = _validate_finding(f, i)
        if verr:
            all_ok = False
            bad(f"finding {f.get('id', i)} required fields", "; ".join(verr))
    if all_ok and findings:
        ok(f"{len(findings)} findings structurally valid")
    elif not findings:
        ok("findings empty (allowed for partial)", "0 findings")

    cov = result.get("review_report", {}).get("coverage", {})
    covered = set(cov.get("checked_facets", [])) | set(cov.get("unchecked_facets", []))
    if FACETS - covered:
        bad("five facets accounted for", f"missing {sorted(FACETS - covered)}")
    else:
        ok("five facets accounted for")

    # summary counts must equal findings length
    total = result.get("summary", {}).get("counts", {}).get("total", -1)
    if total == len(findings):
        ok("summary.counts.total == findings")
    else:
        bad("summary.counts.total == findings", f"{total} vs {len(findings)}")

    report_path = output_dir / "report.md"
    ok("report.md present") if report_path.exists() else bad("report.md present")

    return all(c["pass"] for c in checks), checks


def cmd_selftest(output_dir: str, work_dir: str = None) -> int:
    passed, checks = run_selftest(output_dir, work_dir)
    for c in checks:
        mark = "PASS" if c["pass"] else "FAIL"
        detail = f" — {c['detail']}" if c["detail"] else ""
        print(f"[{mark}] {c['name']}{detail}")
    print(f"selftest: {'ALL PASS' if passed else 'FAILURES PRESENT'}")
    return 0 if passed else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Research-methods & statistics audit engine (stdlib only).",
    )
    p.add_argument(
        "command",
        choices=["scaffold", "probe", "commit", "finalize", "selftest"],
        help="stage to run: scaffold, probe, commit, finalize, selftest",
    )
    p.add_argument("--input", default="/input", help="input directory (default /input)")
    p.add_argument("--output", default="/app/output", help="output directory (default /app/output)")
    p.add_argument("--work", default="/app/work", help="work directory (default /app/work)")
    return p


def dispatch(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.command
    if cmd == "scaffold":
        return cmd_scaffold(args.input, args.output, args.work)
    if cmd == "probe":
        return cmd_probe(args.input, args.work)
    if cmd == "commit":
        return cmd_commit(args.work, args.output)
    if cmd == "finalize":
        return cmd_finalize(args.work, args.output)
    if cmd == "selftest":
        return cmd_selftest(args.output, args.work)
    return 2


if __name__ == "__main__":
    sys.exit(dispatch())
