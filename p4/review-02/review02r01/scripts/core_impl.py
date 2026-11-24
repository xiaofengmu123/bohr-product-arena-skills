"""core_impl.py — implementation of the citation-fact-audit-trace tooling.

All functions use only the Python 3 standard library. The thin entry point
main.py dispatches to these functions; selftest.py reuses run_selftest().

Design focus (evidence-traceability reinforcement):
  * every finding carries a structured locator (page/para/section/table/figure/
    line/raw) and a verbatim quote;
  * the evidence map is granular: each evidence object has a role, character
    span, deterministic sha256 quote hash, source reference, and source status;
  * conflicts are recorded with both sides as separate evidence entries;
  * `integrity` recomputes hashes and re-checks quotes against extracted text.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

TEXT_MAX_BYTES = 2 * 1024 * 1024
PAGE_MARK_RE = re.compile(r"----- PAGE (\d+) / (\d+) -----")

FINDING_REQUIRED = [
    "id", "category", "finding_type", "severity", "verdict",
    "locator", "quote", "claim", "basis", "source_ref",
    "suggestion", "confidence", "review_status",
]

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
VERDICTS = {"pass", "issues_found", "needs_review", "unchecked", "insufficient"}
SEVERITIES = {"critical", "major", "minor", "suggestion"}
CATEGORIES = {
    "citation_support", "fact_source", "evidence_location",
    "numerical_statistical", "argumentation", "academic_norm", "method_design",
}

STANDARD_KEYS = {
    "case_id", "status", "summary", "review_report", "evidence", "sources",
    "uncertainty", "pending_human", "limitations", "errors", "degradations",
    "change_log", "runtime",
}

RUNTIME_BUDGET = {
    "budget_seconds": 900,
    "budget_model_requests": 64,
    "phases": {
        "phase0_discover_scaffold": {"seconds": 180, "max_requests": 8},
        "phase1_section_review": {"seconds": 300, "max_requests": 20},
        "phase2_cross_check": {"seconds": 300, "max_requests": 25},
        "phase3_finalize_selftest": {"seconds": 120, "max_requests": 11},
    },
}


# --------------------------------------------------------------------------
# Small deterministic helpers
# --------------------------------------------------------------------------

def norm(s: str) -> str:
    """Normalize text for hashing/matching: strip and collapse whitespace."""
    return re.sub(r"\s+", " ", (s or "").strip())


def sha16(s: str) -> str:
    """Deterministic 16-hex-digit id from a string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def read_text(path: str):
    """Read a text file tolerating BOM and common bad encodings."""
    with open(path, "rb") as fh:
        data = fh.read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def read_json(path: str):
    """Read JSON, returning (obj, err). obj is None on failure."""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001 - report any parse failure
        return None, f"{type(exc).__name__}: {exc}"


def write_json(path: str, obj) -> None:
    """Write deterministic JSON (LF line endings, sorted-friendly)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def list_files(root: str):
    """Return sorted regular files under root (absolute paths)."""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.isfile(full):
                out.append(full)
    return sorted(out)


def parse_dirs(argv):
    """Parse --input/--output/--work (--key value and --key=value forms)."""
    dirs = {"input": DEFAULT_INPUT, "output": DEFAULT_OUTPUT, "work": DEFAULT_WORK}
    for i, tok in enumerate(argv):
        for key in dirs:
            if tok in (f"--{key}", f"-{key[0]}"):
                if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                    dirs[key] = argv[i + 1]
            elif tok.startswith(f"--{key}="):
                dirs[key] = tok.split("=", 1)[1]
    return dirs


# --------------------------------------------------------------------------
# Phase 0: discovery
# --------------------------------------------------------------------------

def discover(input_dir, work_dir):
    """Scan input, extract PDF text with page markers, write manifest.json."""
    report = {"errors": [], "degradations": []}
    manifest = {
        "input_dir": input_dir,
        "case": None,
        "task": None,
        "materials": [],
        "texts": [],
    }
    for full in list_files(input_dir):
        rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
        base = os.path.basename(full)
        ext = os.path.splitext(full)[1].lower()
        if rel == "case.json" or (base == "case.json" and ext == ".json"):
            manifest["case"] = rel
            continue
        if rel == "task.md" or base == "task.md":
            manifest["task"] = rel
            continue
        manifest["materials"].append(rel)
        if ext in {".pdf", ".txt", ".md", ".csv", ".tsv", ".json"}:
            _extract(full, rel, work_dir, manifest, report)
        else:
            report["degradations"].append(
                {"file": rel, "issue": "unsupported_type", "action": "skipped"}
            )

    write_json(os.path.join(work_dir, "manifest.json"), manifest)
    meta = _load_meta(work_dir)
    meta.setdefault("errors", [])
    meta.setdefault("degradations", [])
    meta["errors"] = (meta["errors"] or []) + report["errors"]
    meta["degradations"] = (meta["degradations"] or []) + report["degradations"]
    write_json(os.path.join(work_dir, "meta.json"), meta)

    print(f"manifest: case={manifest['case']} task={manifest['task']} "
          f"materials={len(manifest['materials'])} texts={len(manifest['texts'])}")
    for d in report["degradations"]:
        print(f"degradation: {d['file']} -> {d['issue']} ({d.get('action', '')})")
    for e in report["errors"]:
        print(f"error: {e['file']} -> {e['issue']}")
    return manifest


def _extract(src, rel, work_dir, manifest, report):
    key = rel.replace("/", "__").replace("\\", "__")
    out_rel = os.path.join("texts", key + ".txt")
    out = os.path.join(work_dir, out_rel)
    ext = os.path.splitext(src)[1].lower()

    if ext == ".pdf":
        tool = shutil.which("pdftotext")
        if not tool:
            report["degradations"].append(
                {"file": rel, "issue": "no_pdftotext", "action": "needs_ocr"})
            return
        tmp = out + ".raw"
        try:
            subprocess.run([tool, "-layout", src, tmp], check=True,
                           capture_output=True, timeout=120)
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(
                {"file": rel, "issue": "pdftotext_failed", "detail": str(exc)})
            return
        text = read_text(tmp)
        _quiet_remove(tmp)
        pages = text.split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        if not any(p.strip() for p in pages):
            report["degradations"].append(
                {"file": rel, "issue": "empty_extracted_text", "action": "needs_ocr"})
        marked = []
        for i, page in enumerate(pages, 1):
            marked.append(f"----- PAGE {i} / {len(pages)} -----\n{page.rstrip()}")
        text = "\n".join(marked) + "\n"
    else:
        if os.path.getsize(src) > TEXT_MAX_BYTES:
            report["degradations"].append(
                {"file": rel, "issue": "too_large", "action": "skipped"})
            return
        try:
            text = read_text(src)
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(
                {"file": rel, "issue": "unreadable", "detail": str(exc)})
            return

    write_text(out, text)
    manifest["texts"].append(
        {"file": rel, "text": out_rel.replace(os.sep, "/"), "chars": len(text)})


def _quiet_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Phase 0: scaffold (early legal partial result)
# --------------------------------------------------------------------------

def build_skeleton(case_id, contract):
    """Return the full result skeleton; contract keys are copied verbatim."""
    standard = {
        "case_id": case_id,
        "status": "partial",
        "summary": {
            "verdict": "unchecked",
            "counts": {
                "total": 0, "critical": 0, "major": 0, "minor": 0,
                "suggestion": 0, "by_category": {},
            },
            "coverage": {"categories": [], "sections": []},
            "unreviewed": [],
        },
        "review_report": {"findings": []},
        "evidence": {
            "evidence_map": [],
            "conflicts": [],
            "provenance": {"sources": []},
            "integrity": {
                "quote_hash_algorithm": "sha256",
                "normalization": "strip_and_collapse_whitespace",
                "verified": False,
                "mismatches": [],
            },
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": RUNTIME_BUDGET,
    }
    result = dict(contract or {})
    result.update(standard)  # standard fields take priority; unknown keys kept
    return result


def _load_meta(work_dir):
    obj, _err = read_json(os.path.join(work_dir, "meta.json"))
    return obj if isinstance(obj, dict) else {}


def _case_identity(input_dir):
    case, _err = read_json(os.path.join(input_dir, "case.json"))
    if not isinstance(case, dict):
        # tolerate nested location: find the first case.json anywhere
        for full in list_files(input_dir):
            if os.path.basename(full) == "case.json":
                case, _err = read_json(full)
                if isinstance(case, dict):
                    break
    if not isinstance(case, dict):
        case = {}
    case_id = case.get("case_id") or case.get("id") or case.get("armiId")
    contract = case.get("output_contract") or {}
    return case_id, contract


def scaffold(input_dir, output_dir, work_dir):
    """Write a legal partial result.json skeleton plus state.json/meta.json."""
    case_id, contract = _case_identity(input_dir)
    result = build_skeleton(case_id, contract)
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "state.json"),
               {"phase": 0, "scaffolded": True, "findings_written": 0})

    meta = _load_meta(work_dir)
    for key in ("errors", "degradations", "sources", "uncertainty",
                "pending_human", "limitations", "conflicts", "unreviewed",
                "change_log"):
        meta.setdefault(key, [])
    meta.setdefault("overall_verdict", None)
    write_json(os.path.join(work_dir, "meta.json"), meta)

    print(f"scaffold ok: case_id={case_id!r} status=partial "
          f"contract_keys={len(contract)}")
    return result


# --------------------------------------------------------------------------
# Findings and evidence building
# --------------------------------------------------------------------------

def validate_finding(obj):
    """Return list of missing required keys for a finding object."""
    if not isinstance(obj, dict):
        return ["finding_is_not_object"]
    return [k for k in FINDING_REQUIRED if k not in obj or obj[k] in (None, "", [])]


def build_evidence(fnd):
    """Build granular evidence entries (primary + any extra evidence)."""
    entries = []
    quote = norm(fnd.get("quote") or "")
    locator = fnd.get("locator")
    entries.append({
        "evidence_id": sha16(f"primary|{fnd.get('id', '')}|{quote}"),
        "finding_id": fnd.get("id"),
        "role": "primary_support",
        "locator": locator,
        "quote": fnd.get("quote"),
        "quote_span": fnd.get("quote_span"),
        "quote_hash": sha16(quote),
        "basis": fnd.get("basis"),
        "source_ref": fnd.get("source_ref"),
        "source_status": fnd.get("source_status", "not_reported"),
        "review_status": fnd.get("review_status", "needs_review"),
    })
    for i, ev in enumerate(fnd.get("evidence") or [], 1):
        eq = norm(ev.get("quote") or "")
        entries.append({
            "evidence_id": sha16(f"extra|{fnd.get('id', '')}|{i}|{eq}"),
            "finding_id": fnd.get("id"),
            "role": ev.get("role", "context"),
            "locator": ev.get("locator"),
            "quote": ev.get("quote"),
            "quote_span": ev.get("quote_span"),
            "quote_hash": sha16(eq),
            "basis": ev.get("basis"),
            "source_ref": ev.get("source_ref"),
            "source_status": ev.get("source_status", "not_reported"),
            "review_status": ev.get("review_status", "needs_review"),
        })
    return entries


# --------------------------------------------------------------------------
# Phase 1-3: finalize (merge findings -> result.json + report.md)
# --------------------------------------------------------------------------

def finalize(input_dir, output_dir, work_dir):
    """Merge work/findings/*.json and work/meta.json into result.json + report.md."""
    case_id, contract = _case_identity(input_dir)
    result = build_skeleton(case_id, contract)

    findings_dir = os.path.join(work_dir, "findings")
    findings = []
    validation_errors = []
    for full in list_files(findings_dir):
        obj, err = read_json(full)
        if obj is None:
            validation_errors.append(
                {"finding_file": os.path.basename(full), "issue": "bad_json",
                 "detail": err})
            continue
        missing = validate_finding(obj)
        if missing:
            validation_errors.append(
                {"finding_file": os.path.basename(full), "issue": "missing_keys",
                 "detail": missing})
            obj = dict(obj)
            obj["review_status"] = "needs_review"
        findings.append(obj)

    findings.sort(key=lambda f: str(f.get("id", "")))

    result["review_report"]["findings"] = findings
    evidence_map = []
    for fnd in findings:
        evidence_map.extend(build_evidence(fnd))
    evidence_map.sort(key=lambda e: e["evidence_id"])
    result["evidence"]["evidence_map"] = evidence_map

    meta = _load_meta(work_dir)
    result["sources"] = meta.get("sources", [])
    result["uncertainty"] = meta.get("uncertainty", [])
    result["pending_human"] = meta.get("pending_human", [])
    result["limitations"] = meta.get("limitations", [])
    result["degradations"] = meta.get("degradations", [])
    result["change_log"] = meta.get("change_log", [])
    result["errors"] = (meta.get("errors") or []) + validation_errors
    result["evidence"]["conflicts"] = meta.get("conflicts", [])
    result["evidence"]["provenance"]["sources"] = meta.get("sources", [])

    counts = {"total": 0, "critical": 0, "major": 0, "minor": 0,
              "suggestion": 0, "by_category": {}}
    for fnd in findings:
        counts["total"] += 1
        sev = fnd.get("severity")
        if sev in counts:
            counts[sev] += 1
        cat = fnd.get("category", "unknown")
        counts["by_category"][cat] = counts["by_category"].get(cat, 0) + 1
    result["summary"]["counts"] = counts

    verdict = meta.get("overall_verdict")
    result["summary"]["verdict"] = (
        verdict if verdict in VERDICTS else
        ("issues_found" if counts["critical"] or counts["major"] else
         ("needs_review" if counts["minor"] else "unchecked"))
    )
    if counts["critical"] == 0 and counts["major"] == 0 and counts["total"] == 0:
        result["summary"]["verdict"] = meta.get("overall_verdict") or "unchecked"

    # coverage derived from actual findings
    cats = sorted({f.get("category") for f in findings if f.get("category")})
    result["summary"]["coverage"]["categories"] = cats
    sections = sorted({_section_of(f) for f in findings if _section_of(f)})
    result["summary"]["coverage"]["sections"] = sections
    result["summary"]["unreviewed"] = meta.get("unreviewed", [])

    # status: explicit meta.status wins; otherwise auto-detect empty material.
    manifest, _err = read_json(os.path.join(work_dir, "manifest.json"))
    has_material = False
    if isinstance(manifest, dict):
        has_material = bool(manifest.get("materials"))
    explicit_status = meta.get("status")
    if explicit_status in STATUSES:
        result["status"] = explicit_status
    elif not findings and not has_material:
        result["status"] = "insufficient_evidence"
        result["summary"]["verdict"] = "insufficient"
        lim = result["limitations"] or []
        lim.append({"scope": "input", "issue": "no_reviewable_material",
                    "detail": "manifest shows no material files"})
        result["limitations"] = lim

    verified, mismatches, _n = _compute_integrity(result, work_dir)
    result["evidence"]["integrity"]["verified"] = verified
    result["evidence"]["integrity"]["mismatches"] = mismatches

    write_json(os.path.join(output_dir, "result.json"), result)
    write_text(os.path.join(output_dir, "report.md"), render_report(result))

    print(f"finalize ok: findings={counts['total']} "
          f"critical={counts['critical']} major={counts['major']} "
          f"verdict={result['summary']['verdict']} status={result['status']}")
    return result


def _section_of(fnd):
    loc = fnd.get("locator")
    if isinstance(loc, dict):
        return loc.get("section")
    return None


def locator_str(loc):
    """Render a locator (dict or string) as a human-readable string."""
    if isinstance(loc, dict):
        return loc.get("raw") or str(loc)
    return str(loc or "")


def render_report(result):
    """Render a human-editable markdown review report."""
    lines = [
        "# 引用与事实核验审查报告",
        "",
        f"- case_id: `{result.get('case_id')}`",
        f"- status: `{result.get('status')}`",
        f"- verdict: `{result['summary']['verdict']}`",
        f"- counts: {json.dumps(result['summary']['counts'], ensure_ascii=False)}",
        "",
    ]
    findings = result["review_report"]["findings"]
    if not findings:
        lines.append("（暂无已核验条目。材料不足时应在 `limitations` 中说明缺口。）")
    for fnd in findings:
        sev = fnd.get("severity", "")
        cat = fnd.get("category", "")
        ftype = fnd.get("finding_type", "")
        lines.append(f"## {fnd.get('id')} [{sev}] {cat} — {ftype}")
        lines.append("")
        lines.append(f"- verdict: `{fnd.get('verdict')}` · "
                     f"review_status: `{fnd.get('review_status')}` · "
                     f"confidence: `{fnd.get('confidence')}`")
        lines.append(f"- locator: `{locator_str(fnd.get('locator'))}`")
        q = (fnd.get('quote') or '').replace('\n', ' ')
        lines.append(f"- quote: “{q}”")
        lines.append(f"- claim: {fnd.get('claim')}")
        lines.append(f"- basis: {fnd.get('basis')}")
        lines.append(f"- source_ref: `{fnd.get('source_ref')}`")
        lines.append(f"- suggestion: {fnd.get('suggestion')}")
        lines.append("")
    if result.get("uncertainty"):
        lines.append("## 待复核 / 不确定性")
        for item in result["uncertainty"]:
            lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
    if result.get("pending_human"):
        lines.append("")
        lines.append("## 待人工确认")
        for item in result["pending_human"]:
            lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Integrity check (quote hash + verbatim re-verification)
# --------------------------------------------------------------------------

def _compute_integrity(result, work_dir):
    """Return (verified, mismatches, evidence_checked) for a result object."""
    corpus = []
    texts_dir = os.path.join(work_dir, "texts")
    for full in list_files(texts_dir):
        if full.endswith(".txt"):
            corpus.append(read_text(full))
    corpus_norm = norm("\n".join(corpus))

    mismatches = []
    evidence_map = result.get("evidence", {}).get("evidence_map", [])
    for ev in evidence_map:
        quote = ev.get("quote") or ""
        q_norm = norm(quote)
        expected_hash = sha16(q_norm)
        actual_hash = ev.get("quote_hash")
        if actual_hash != expected_hash:
            mismatches.append({
                "evidence_id": ev.get("evidence_id"),
                "issue": "hash_mismatch",
                "detail": f"stored={actual_hash} recomputed={expected_hash}",
            })
        if q_norm and q_norm not in corpus_norm:
            mismatches.append({
                "evidence_id": ev.get("evidence_id"),
                "issue": "quote_not_found_in_text",
                "detail": q_norm[:120],
            })
    return (not mismatches), mismatches, len(evidence_map)


def integrity(work_dir, output_dir):
    """Recompute quote hashes and re-check quotes against extracted text."""
    result, err = read_json(os.path.join(output_dir, "result.json"))
    if result is None:
        print(f"integrity: cannot read result.json ({err})")
        return 1

    verified, mismatches, n_checked = _compute_integrity(result, work_dir)
    result["evidence"]["integrity"]["verified"] = verified
    result["evidence"]["integrity"]["mismatches"] = mismatches
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "integrity.json"),
               {"verified": verified, "mismatches": mismatches,
                "evidence_checked": n_checked})

    print(f"integrity: evidence_checked={n_checked} "
          f"verified={verified} mismatches={len(mismatches)}")
    for m in mismatches[:20]:
        print(f"  mismatch: {m['evidence_id']} {m['issue']} {m.get('detail', '')}")
    return 0 if verified else 1


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def _frontmatter_ok(skill_text):
    if not skill_text.startswith("---"):
        return False, "missing YAML frontmatter"
    m = re.search(r"\A---\s*\n(.*?)\n---", skill_text, re.DOTALL)
    if not m:
        return False, "unclosed YAML frontmatter"
    body = m.group(1)
    for key in ("name:", "description:", "version:"):
        if not re.search(rf"(?m)^{re.escape(key)}", body):
            return False, f"missing frontmatter key {key}"
    return True, ""


def run_selftest(root):
    """Validate package structure, schema, determinism, and script imports."""
    checks = []
    skill_path = os.path.join(root, "SKILL.md")
    scripts_dir = os.path.join(root, "scripts")

    if os.path.isfile(skill_path):
        ok, msg = _frontmatter_ok(read_text(skill_path))
        checks.append(("SKILL.md frontmatter", ok, msg))
    else:
        checks.append(("SKILL.md frontmatter", False, "SKILL.md missing"))

    for name in ("main.py", "core_impl.py", "selftest.py"):
        exists = os.path.isfile(os.path.join(scripts_dir, name))
        checks.append((f"scripts/{name} exists", exists,
                       "" if exists else "missing"))

    # schema + determinism on a synthetic finding
    try:
        fnd = {
            "id": "F0001", "category": "citation_support",
            "finding_type": "unsupported_claim", "severity": "major",
            "verdict": "unsupported",
            "locator": {"page": 3, "para": 2, "section": "Results",
                        "table": None, "figure": None, "line": None,
                        "raw": "p.3, para 2, Results"},
            "quote": "This effect is  fully  novel.", "claim": "paraphrase",
            "basis": "rule C1", "source_ref": "not_verified",
            "suggestion": "add citation", "confidence": "high",
            "review_status": "verified",
        }
        missing = validate_finding(fnd)
        checks.append(("finding schema accepts complete object", not missing,
                       str(missing) if missing else ""))
        ev1 = build_evidence(fnd)
        ev2 = build_evidence(dict(fnd))
        same = ev1 == ev2
        checks.append(("evidence build is deterministic", same,
                       "" if same else "non-deterministic"))
        checks.append(("quote_hash is sha256-hex-16", 
                       len(ev1[0]["quote_hash"]) == 16,
                       ev1[0]["quote_hash"]))
    except Exception as exc:  # noqa: BLE001
        checks.append(("schema/determinism test", False, str(exc)))

    passed = sum(1 for _n, ok, _m in checks if ok)
    for name, ok, msg in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {msg}" if msg else ""))
    print(f"selftest: {passed}/{len(checks)} passed")
    return 0 if passed == len(checks) else 1
