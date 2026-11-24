"""core_impl.py - pipeline helpers for the citation-fact-audit Skill.

Only the Python standard library is used. The code is deterministic: no
network, no randomness, no hidden state. Every function either returns a
value or records an explicit error/degradation instead of crashing.

Pipeline phases (invoked through main.py):
  discover  -> scan /input, extract PDF text to /app/work/texts, write manifest
  scaffold  -> write a legal partial result.json skeleton to /app/output early
  finalize  -> merge findings + meta, verify quotes, recompute counts, render report

Quote verification is the distinctive guardrail: every finding's `quote` is
looked up verbatim (after whitespace normalisation) in the extracted page
text. A finding that cannot be traced back to the material is marked
`quote_verified=false` and enters `limitations`, never silently accepted.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

# Keep console output portable across Linux and Windows terminals.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
VERDICTS = {"pass", "issues_found", "needs_review", "unchecked", "insufficient"}
SEVERITIES = {"critical", "major", "minor", "suggestion"}
CATEGORIES = (
    "citation_support",
    "fact_source",
    "evidence_location",
    "numerical_statistical",
    "argumentation",
    "academic_norm",
    "method_design",
)
FINDING_TYPES = {
    "factual_error",
    "inconsistency",
    "unsupported_claim",
    "overreach",
    "source_unreliable",
    "missing_control",
    "norm_violation",
    "evidence_insufficient",
    "suggestion",
}
COVERAGE_STATES = {
    "reviewed",
    "partial",
    "not_reviewed",
    "not_applicable",
    "not_available",
    "needs_review",
}
REQUIRED_FINDING_KEYS = (
    "id",
    "category",
    "finding_type",
    "severity",
    "verdict",
    "locator",
    "quote",
    "suggestion",
)
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".tex", ".bib"}
MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2MB per text file; larger files are skipped


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _norm(s):
    """Normalise whitespace so verbatim quotes can be matched across line wraps."""
    if s is None:
        return ""
    s = s.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def read_text(path):
    """Read UTF-8 text with BOM tolerance, falling back to latin-1 for bad bytes."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1") as fh:
                return fh.read()
        except Exception:
            return None


def read_json(path):
    """Return (object, None) on success or (None, error_string) on failure."""
    if not path or not os.path.isfile(path):
        return None, "missing file: %s" % (path,)
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except ValueError as exc:
        return None, "invalid JSON: %s" % (exc,)
    except Exception as exc:  # noqa: BLE001 - degrade, do not crash
        return None, "read error: %s" % (exc,)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def _run_pdftotext(pdf_path, out_path):
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext not found"
    try:
        proc = subprocess.run(
            [exe, "-layout", pdf_path, out_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        return None, "pdftotext failed: %s" % (exc,)
    if proc.returncode != 0:
        return None, "pdftotext exit %s: %s" % (proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
    return out_path, None


def extract_pdf(pdf_path, out_txt_path):
    """Extract a PDF to page-marked UTF-8 text. Returns (rel_text_path, page_count, error)."""
    raw = out_txt_path + ".raw"
    ok, err = _run_pdftotext(pdf_path, raw)
    if err:
        return None, 0, err
    text = read_text(raw)
    if os.path.isfile(raw):
        try:
            os.remove(raw)
        except OSError:
            pass
    if text is None:
        return None, 0, "empty or unreadable PDF text"
    pages = text.split("\f")
    pages = [p for p in pages if p.strip()]
    if not pages:
        return None, 0, "scanned PDF: no extractable text layer"
    total = len(pages)
    lines = []
    for idx, page in enumerate(pages, start=1):
        lines.append("----- PAGE %d / %d -----" % (idx, total))
        lines.append(page.strip())
    with open(out_txt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out_txt_path, total, None


def split_pages(raw):
    """Split page-marked text into per-page chunks with normalised copies."""
    parts = re.split(r"-{5}\s*PAGE\s+(\d+)\s*/\s*\d+\s*-{5}", raw or "")
    pages = []
    if len(parts) > 1:
        for i in range(1, len(parts), 2):
            num = int(parts[i])
            content = parts[i + 1] if i + 1 < len(parts) else ""
            pages.append({"num": num, "raw": content, "norm": _norm(content)})
    if not pages:
        pages = [{"num": 1, "raw": raw or "", "norm": _norm(raw or "")}]
    return pages


def load_texts(manifest):
    """Load every extracted text file listed in the manifest for quote lookup."""
    texts = []
    for t in manifest.get("texts", []):
        path = t.get("abs") or t.get("path")
        raw = read_text(path)
        if raw is None:
            continue
        texts.append(
            {
                "rel": t.get("rel"),
                "path": path,
                "raw": raw,
                "full_norm": _norm(raw),
                "pages": split_pages(raw),
            }
        )
    return texts


def _page_hint(locator):
    m = re.search(r"(?i)\bp\.?\s*(\d+)", locator or "")
    return int(m.group(1)) if m else None


def verify_quote(finding, texts):
    """Check the finding's quote appears verbatim (normalised) in the material."""
    quote = finding.get("quote") or ""
    nq = _norm(quote)
    if not nq or len(nq) < 4 or nq == "not_reported":
        return False, None
    page = _page_hint(finding.get("locator"))
    if page is not None:
        for text in texts:
            for pg in text["pages"]:
                if pg["num"] == page and nq in pg["norm"]:
                    return True, "%s p.%d" % (text["rel"], page)
    for text in texts:
        if nq in text["full_norm"]:
            return True, text["rel"]
    return False, None


# --------------------------------------------------------------------------- #
# discover
# --------------------------------------------------------------------------- #
def _walk_files(input_dir):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            files.append(
                {
                    "rel": os.path.relpath(path, input_dir).replace("\\", "/"),
                    "abs": path,
                    "size": size,
                }
            )
    return files


def discover(input_dir, work_dir):
    errors = []
    degradations = []
    files = _walk_files(input_dir)
    case_file = None
    case_id = None
    output_contract = None
    task_file = None
    materials = []
    texts = []
    texts_dir = os.path.join(work_dir, "texts")
    os.makedirs(texts_dir, exist_ok=True)

    if not os.path.isdir(input_dir):
        errors.append("input directory missing: %s" % (input_dir,))
    elif not files:
        errors.append("input directory empty: no files under %s" % (input_dir,))

    for f in files:
        name = os.path.basename(f["rel"]).lower()
        if name == "case.json":
            case_file = f["rel"]
            obj, err = read_json(f["abs"])
            if err:
                errors.append("case.json unreadable: %s" % (err,))
            elif isinstance(obj, dict):
                case_id = obj.get("case_id")
                output_contract = obj.get("output_contract")
                if not isinstance(output_contract, dict):
                    output_contract = None
        elif name in ("task.md", "task.txt"):
            task_file = f["rel"]
        else:
            ext = os.path.splitext(name)[1].lower()
            materials.append({"rel": f["rel"], "abs": f["abs"], "size": f["size"], "kind": ext or "unknown"})

    for m in materials:
        ext = (m["kind"] or "").lower()
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", m["rel"])
        stem = re.sub(r"(?i)\.(pdf|txt|md|csv|json|xml|html|tex|bib)$", "", stem)
        out_rel = "texts/%s.txt" % stem
        out_abs = os.path.join(work_dir, out_rel)
        if m["size"] < 0:
            degradations.append({"file": m["rel"], "reason": "unreadable size", "disposition": "skipped"})
        elif ext == ".pdf":
            _, pages, err = extract_pdf(m["abs"], out_abs)
            if err:
                degradations.append({"file": m["rel"], "reason": err, "disposition": "needs_ocr"})
            else:
                texts.append({"rel": out_rel, "abs": out_abs, "source": m["rel"], "pages": pages})
        elif ext in TEXT_EXTENSIONS:
            if m["size"] > MAX_TEXT_BYTES:
                degradations.append({"file": m["rel"], "reason": "size %d > %d bytes" % (m["size"], MAX_TEXT_BYTES), "disposition": "skipped"})
                continue
            raw = read_text(m["abs"])
            if raw is None:
                degradations.append({"file": m["rel"], "reason": "unreadable encoding", "disposition": "skipped"})
            else:
                try:
                    with open(out_abs, "w", encoding="utf-8") as fh:
                        fh.write(raw)
                    texts.append({"rel": out_rel, "abs": out_abs, "source": m["rel"], "pages": 1})
                except OSError as exc:
                    errors.append("write text failed for %s: %s" % (m["rel"], exc))
        else:
            degradations.append({"file": m["rel"], "reason": "unsupported extension %s" % (ext,), "disposition": "skipped"})

    manifest = {
        "input_dir": input_dir,
        "generated_at": _now(),
        "files": files,
        "case_file": case_file,
        "case_id": case_id,
        "output_contract": output_contract,
        "task_file": task_file,
        "materials": materials,
        "texts": texts,
        "errors": errors,
        "degradations": degradations,
    }
    write_json(os.path.join(work_dir, "manifest.json"), manifest)
    return manifest


def load_manifest(work_dir):
    obj, err = read_json(os.path.join(work_dir, "manifest.json"))
    return obj if isinstance(obj, dict) else {}, err


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #
def fresh_coverage():
    sections = ["<unmapped>"]
    return {
        "categories": list(CATEGORIES),
        "sections": sections,
        "matrix": {c: {s: "not_reviewed" for s in sections} for c in CATEGORIES},
    }


def scaffold(case_id, output_contract, work_dir, output_dir):
    now = _now()
    result = {
        "case_id": case_id if case_id else "unknown",
        "status": "partial",
        "summary": {
            "verdict": "unchecked",
            "counts": {
                "total": 0,
                "critical": 0,
                "major": 0,
                "minor": 0,
                "suggestion": 0,
                "quote_verified": 0,
                "quote_unverified": 0,
                "by_category": {c: 0 for c in CATEGORIES},
            },
        },
        "review_report": {
            "findings": [],
            "coverage_matrix": fresh_coverage(),
        },
        "evidence": {"evidence_map": []},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [
            {
                "scope": "whole_document",
                "detail": "initial skeleton: no findings reviewed yet; all categories marked not_reviewed",
            }
        ],
        "errors": [],
        "degradations": [],
        "change_log": [{"time": now, "event": "scaffold", "detail": "partial result skeleton written before analysis"}],
        "runtime": {
            "started_at": now,
            "started_epoch": time.time(),
            "finished_at": None,
            "wall_seconds": None,
            "budget_seconds": 900,
            "budget_requests": 64,
            "model_requests_used": None,
        },
    }
    if isinstance(output_contract, dict):
        result["output_contract"] = output_contract
        for key, value in output_contract.items():
            if key not in result:
                result[key] = value
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(
        os.path.join(work_dir, "state.json"),
        {"phase": "scaffold", "findings_written": 0, "updated_at": now},
    )
    return result


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #
def load_findings(work_dir):
    fdir = os.path.join(work_dir, "findings")
    out = []
    if not os.path.isdir(fdir):
        return out
    for name in sorted(os.listdir(fdir)):
        if not name.endswith(".json"):
            continue
        obj, err = read_json(os.path.join(fdir, name))
        if isinstance(obj, dict):
            out.append(obj)
        else:
            out.append({"id": "broken:" + name, "error": err or "invalid finding file"})
    out.sort(key=lambda f: str(f.get("id", "")))
    return out


def _derive_status(manifest, meta, findings):
    if isinstance(meta, dict) and meta.get("overall_status") in STATUSES:
        return meta["overall_status"]
    materials = manifest.get("materials") or []
    if not materials and not findings:
        return "insufficient_evidence"
    if not findings:
        return "partial"
    needs = any(f.get("review_status") == "needs_review" for f in findings)
    if needs or manifest.get("degradations"):
        return "partial"
    return "ok"


def _derive_verdict(meta, counts, findings, status):
    if isinstance(meta, dict) and meta.get("overall_verdict") in VERDICTS:
        return meta["overall_verdict"]
    if not findings:
        return "insufficient" if status == "insufficient_evidence" else "unchecked"
    if counts["critical"] or counts["major"] or counts["minor"]:
        return "issues_found"
    if any(f.get("review_status") == "needs_review" for f in findings):
        return "needs_review"
    if counts["suggestion"]:
        return "pass"
    return "unchecked"


def _compute_counts(findings):
    counts = {
        "total": 0,
        "critical": 0,
        "major": 0,
        "minor": 0,
        "suggestion": 0,
        "quote_verified": 0,
        "quote_unverified": 0,
        "by_category": {c: 0 for c in CATEGORIES},
    }
    for f in findings:
        counts["total"] += 1
        sev = f.get("severity") if f.get("severity") in SEVERITIES else "minor"
        counts[sev] = counts.get(sev, 0) + 1
        cat = f.get("category")
        if cat in counts["by_category"]:
            counts["by_category"][cat] += 1
        if f.get("quote_verified"):
            counts["quote_verified"] += 1
        else:
            counts["quote_unverified"] += 1
    return counts


def build_evidence_map(findings):
    return [
        {
            "finding_id": f.get("id"),
            "locator": f.get("locator"),
            "quote": f.get("quote"),
            "basis": f.get("basis"),
            "source_ref": f.get("source_ref"),
            "quote_verified": bool(f.get("quote_verified")),
        }
        for f in findings
    ]


def _pick(obj, key, default):
    val = obj.get(key) if isinstance(obj, dict) else None
    return val if val is not None else default


def finalize(work_dir, output_dir):
    result_path = os.path.join(output_dir, "result.json")
    result, err = read_json(result_path)
    if not isinstance(result, dict):
        return None, err or "result.json missing: run scaffold first"
    manifest, _ = load_manifest(work_dir)
    meta, _ = read_json(os.path.join(work_dir, "meta.json"))
    if not isinstance(meta, dict):
        meta = {}

    findings = load_findings(work_dir)
    texts = load_texts(manifest)

    for f in findings:
        # quote verification is always recomputed from the extracted material
        verified, where = verify_quote(f, texts)
        f["quote_verified"] = bool(verified)
        f["quote_match_locator"] = where if where else None
        defaults = {
            "claim": "not_reported",
            "basis": "not_reported",
            "source_ref": "not_verified",
            "confidence": "medium",
            "review_status": "needs_review",
            "evidence_strength": "not_reported",
        }
        for key, value in defaults.items():
            if not f.get(key):
                f[key] = value

    counts = _compute_counts(findings)
    coverage = meta.get("coverage_matrix") if isinstance(meta.get("coverage_matrix"), dict) else result.get("review_report", {}).get("coverage_matrix", fresh_coverage())

    result["case_id"] = result.get("case_id") or manifest.get("case_id") or meta.get("case_id") or "unknown"
    result["review_report"]["findings"] = findings
    result["review_report"]["coverage_matrix"] = coverage
    result["summary"]["counts"] = counts
    result["evidence"]["evidence_map"] = build_evidence_map(findings)

    result["sources"] = _pick(meta, "sources", result.get("sources", []))
    result["uncertainty"] = _pick(meta, "uncertainty", result.get("uncertainty", []))
    result["pending_human"] = _pick(meta, "pending_human", result.get("pending_human", []))
    result["limitations"] = _pick(meta, "limitations", result.get("limitations", []))
    result["errors"] = _pick(meta, "errors", result.get("errors", [])) or list(manifest.get("errors", []))
    result["degradations"] = _pick(meta, "degradations", result.get("degradations", [])) or list(manifest.get("degradations", []))

    # explicit uncertainty for unverifiable quotes (no fabrication guardrail)
    unverified = [f for f in findings if not f.get("quote_verified") and (f.get("quote") or "").strip()]
    existing_unc = result["uncertainty"] if isinstance(result["uncertainty"], list) else []
    existing_ids = {u.get("id") for u in existing_unc if isinstance(u, dict)}
    auto_unc = []
    for f in unverified:
        uid = "U-quote-%s" % (f.get("id"),)
        if uid in existing_ids:
            continue
        auto_unc.append(
            {
                "id": uid,
                "type": "quote_not_traceable",
                "description": "quote could not be matched verbatim in extracted text; verify manually before relying on this finding",
                "affects": [f.get("id")],
                "resolution": "pending_human",
            }
        )
    if auto_unc:
        result["uncertainty"] = existing_unc + auto_unc
        result["pending_human"] = result.get("pending_human", []) + [u["id"] for u in auto_unc]

    result["status"] = _derive_status(manifest, meta, findings)
    result["summary"]["verdict"] = _derive_verdict(meta, counts, findings, result["status"])

    now = _now()
    started = result.get("runtime", {}).get("started_epoch")
    if started:
        result["runtime"]["finished_at"] = now
        result["runtime"]["wall_seconds"] = round(time.time() - float(started), 1)
    log = result.setdefault("change_log", [])
    log.append({"time": now, "event": "finalize", "detail": "findings=%d, status=%s, verdict=%s" % (len(findings), result["status"], result["summary"]["verdict"])})

    write_json(result_path, result)
    report_path = render_report(result, output_dir)
    state = {
        "phase": "finalize",
        "findings_written": len(findings),
        "quote_verified": counts["quote_verified"],
        "quote_unverified": counts["quote_unverified"],
        "status": result["status"],
        "verdict": result["summary"]["verdict"],
        "updated_at": now,
    }
    write_json(os.path.join(work_dir, "state.json"), state)
    return result, report_path


def render_report(result, output_dir):
    lines = []
    lines.append("# 引用与事实核验报告")
    lines.append("")
    lines.append("- case_id: `%s`" % (result.get("case_id"),))
    lines.append("- status: `%s`" % (result.get("status"),))
    lines.append("- verdict: `%s`" % (result.get("summary", {}).get("verdict"),))
    counts = result.get("summary", {}).get("counts", {})
    lines.append("- counts: total=%s critical=%s major=%s minor=%s suggestion=%s" % (
        counts.get("total"), counts.get("critical"), counts.get("major"),
        counts.get("minor"), counts.get("suggestion")))
    lines.append("- quote_verified=%s quote_unverified=%s" % (counts.get("quote_verified"), counts.get("quote_unverified")))
    lines.append("")
    findings = result.get("review_report", {}).get("findings", [])
    lines.append("## 结论清单（%d 条）" % len(findings))
    lines.append("")
    for f in findings:
        lines.append("### %s · %s · %s" % (f.get("id"), f.get("category"), f.get("severity")))
        lines.append("")
        lines.append("- 类型: `%s`；判定: `%s`；复核状态: `%s`" % (f.get("finding_type"), f.get("verdict"), f.get("review_status")))
        lines.append("- 定位: `%s`" % (f.get("locator"),))
        lines.append("- 原文摘录: %s" % (f.get("quote"),))
        lines.append("- 转述: %s" % (f.get("claim"),))
        lines.append("- 依据: %s" % (f.get("basis"),))
        lines.append("- 来源: `%s`；quote_verified=`%s`" % (f.get("source_ref"), f.get("quote_verified")))
        lines.append("- 修改建议: %s" % (f.get("suggestion"),))
        lines.append("")
    for section, key in (
        ("不确定性与待复核", "uncertainty"),
        ("限制", "limitations"),
        ("人工待办", "pending_human"),
        ("来源", "sources"),
        ("错误", "errors"),
        ("降级", "degradations"),
        ("变更记录", "change_log"),
    ):
        lines.append("## %s" % section)
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(result.get(key, []), ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    report_path = os.path.join(output_dir, "report.md")
    os.makedirs(output_dir, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return report_path


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #
def main(argv):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="citation-fact-audit pipeline (discover/scaffold/finalize)",
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="input directory (default /input)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="output directory (default /app/output)")
    parser.add_argument("--work", default=DEFAULT_WORK, help="work directory (default /app/work)")
    parser.add_argument("--phase", choices=["all", "discover", "scaffold", "finalize"], default="all")
    args = parser.parse_args(argv)

    for d in (args.output, args.work):
        os.makedirs(d, exist_ok=True)

    manifest = None
    if args.phase in ("all", "discover"):
        manifest = discover(args.input, args.work)
        print("[discover] files=%d materials=%d texts=%d errors=%d degradations=%d" % (
            len(manifest["files"]), len(manifest["materials"]), len(manifest["texts"]),
            len(manifest["errors"]), len(manifest["degradations"])))
        for e in manifest["errors"]:
            print("  ERROR: %s" % e)
        for d in manifest["degradations"]:
            print("  DEGRADED: %s -> %s" % (d["file"], d["disposition"]))
    if args.phase in ("all", "scaffold"):
        if manifest is None:
            manifest, _ = load_manifest(args.work)
        case_id = manifest.get("case_id") if isinstance(manifest, dict) else None
        oc = manifest.get("output_contract") if isinstance(manifest, dict) else None
        scaffold(case_id, oc, args.work, args.output)
        print("[scaffold] result.json skeleton written (status=partial, verdict=unchecked)")
    if args.phase in ("all", "finalize"):
        result, err = finalize(args.work, args.output)
        if result is None:
            print("[finalize] ERROR: %s" % err, file=sys.stderr)
            return 2
        print("[finalize] status=%s verdict=%s findings=%d" % (
            result["status"], result["summary"]["verdict"], len(result["review_report"]["findings"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
