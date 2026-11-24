#!/usr/bin/env python3
"""slides-01 entry point: contract skeleton -> deterministic .pptx -> result.json.

Usage (all stdlib, run from the skill root so ``import pptx_core`` resolves):

    python3 -B scripts/main.py --skeleton-only
    python3 -B scripts/main.py --input /input --work /app/work --output /app/output
    python3 -B scripts/main.py --input /input --work /app/work --output /app/output \
        --draft /app/work/draft.json --pdf /app/output/presentation.pdf --repro

The agent authors ``work/draft.json`` (claims / roles / evidence with verbatim
quotes); this script performs only deterministic mechanics: it never invents
scientific content and never overwrites an already-frozen draft.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pptx_core import (  # noqa: E402
    ROLES,
    build_presentation_parts,
    content_hash,
    discover_materials,
    input_fingerprint,
    is_valid_pptx,
    parts_fingerprint,
    pdf_page_count,
    pptx_slide_count,
    quote_in_text,
    read_zip,
    validate_draft,
    write_zip,
)

VERSION = "0.3.0"
TASK_LABEL = "论文转学术 PPT（slides-01）"

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

# "list-7" original contract fields; the runtime contract may add more.
ROOT_FIELDS = ["case_id", "status", "editable_source", "export_artifact",
               "content_map", "compliance_checks", "exceptions"]
ENVELOPE_FIELDS = ["sources", "uncertainty", "pending_human", "errors",
                   "degradations", "change_log", "changes", "runtime"]
LIST_FIELDS = {"content_map", "compliance_checks", "exceptions", "sources",
               "uncertainty", "pending_human", "errors", "degradations",
               "change_log", "changes"}
DICT_FIELDS = {"editable_source", "export_artifact", "runtime"}
STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

FIXED_TS = "1970-01-01T00:00:00+00:00"  # used by --repro for byte-identical result.json


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ts_for(repro: bool) -> str:
    return FIXED_TS if repro else now_iso()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def read_json(path: Path):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, "%s 读取失败: %s" % (path.name, exc.__class__.__name__)
    if not raw.strip():
        return None, "%s 为空文件" % path.name
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "%s 非 UTF-8 文本" % path.name
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, "%s JSON 解析失败: %s" % (path.name, exc)


def write_json(path: Path, obj) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


def write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def ensure_dir(path: Path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, "%s: %s" % (exc.__class__.__name__, exc)


def contract_keys(case) -> list:
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys: list = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str) and item[cand].strip():
                        keys.append(item[cand].strip())
                        break
    if not keys:
        keys = list(ROOT_FIELDS)
    ordered, seen = [], set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def default_value(key: str):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    return None


def build_skeleton(case_id: str, keys: list, repro: bool) -> dict:
    payload = {
        "case_id": case_id,
        "status": "partial",
        "editable_source": {"path": "presentation.pptx", "format": "pptx", "slides": 0},
        "export_artifact": {"pdf": {"path": "presentation.pdf", "pages": 0}},
        "content_map": [],
        "compliance_checks": [],
        "exceptions": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [{"ts": ts_for(repro), "action": "skeleton_written",
                        "reason": "initial hand-offable partial result"}],
        "changes": [],
        "runtime": {"started": ts_for(repro), "deterministic_build": True,
                    "repro_mode": repro, "version": VERSION},
    }
    ordered = {k: payload.get(k, default_value(k)) for k in keys}
    for k in ENVELOPE_FIELDS:
        ordered.setdefault(k, payload.get(k, default_value(k)))
    return ordered


def evidence_checks(draft, work_dir: Path, compliance: list, uncertainty: list):
    pages = draft.get("pages") or []
    verified = 0
    total = 0
    for i, page in enumerate(pages, 1):
        for j, ev in enumerate(page.get("evidence") or [], 1):
            total += 1
            src = str(ev.get("source_id") or "")
            quote = str(ev.get("quote") or "")
            txt_path = None
            for cand in (work_dir / "sources" / src,
                         work_dir / "sources" / (src + ".txt"),
                         work_dir / "sources" / (src + ".md")):
                if cand.is_file():
                    txt_path = cand
                    break
            if txt_path is None:
                compliance.append({
                    "id": "quote_verify_p%d_e%d" % (i, j),
                    "check": "evidence quote is verbatim in extracted source text",
                    "status": "skip",
                    "detail": "no extracted text for source_id=%s in work/sources/" % src})
                continue
            try:
                text = txt_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if quote_in_text(quote, text):
                verified += 1
                compliance.append({
                    "id": "quote_verify_p%d_e%d" % (i, j),
                    "check": "evidence quote is verbatim in extracted source text",
                    "status": "pass",
                    "detail": "quote matched in %s" % src})
            else:
                compliance.append({
                    "id": "quote_verify_p%d_e%d" % (i, j),
                    "check": "evidence quote is verbatim in extracted source text",
                    "status": "fail",
                    "detail": "quote not found in %s (whitespace-normalized)" % src})
                uncertainty.append({
                    "id": "quote_unverified_p%d_e%d" % (i, j),
                    "item": "page %d evidence %d quote not verified verbatim" % (i, j),
                    "reason": "quote substring not found in extracted text; may be a "
                              "paraphrase or an extraction gap",
                    "level": "medium"})
    return verified, total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=TASK_LABEL)
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--work", default=str(DEFAULT_WORK))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--draft", default=None, help="path to work/draft.json")
    ap.add_argument("--template", default=None, help="override template .pptx path")
    ap.add_argument("--pdf", default=None, help="path to an already-rendered PDF to validate")
    ap.add_argument("--skeleton-only", action="store_true",
                    help="write the partial result skeleton and exit immediately")
    ap.add_argument("--repro", action="store_true",
                    help="fixed timestamps so identical input+draft give byte-identical result.json")
    args = ap.parse_args(argv)

    input_root = Path(args.input).expanduser()
    work_dir = Path(args.work).expanduser()
    output_dir = Path(args.output).expanduser()

    errors: list = []
    degradations: list = []
    changes: list = []

    ok, err = ensure_dir(work_dir)
    if not ok:
        degradations.append("work dir %s unusable (%s)" % (work_dir, err))
    ok, err = ensure_dir(output_dir)
    if not ok:
        degradations.append("output dir %s unusable (%s)" % (output_dir, err))
        return 1

    # 1) case.json + case_id
    case, case_err = read_json(input_root / "case.json")
    if case_err:
        errors.append("case.json unavailable: %s" % case_err)
        case = {}
    if not isinstance(case, dict):
        case = {}
    case_id = str(case.get("case_id") or "").strip() or "unknown-case"
    keys = contract_keys(case)

    # 2) write a valid partial skeleton FIRST (hand-offable within seconds)
    skeleton = build_skeleton(case_id, keys, args.repro)
    write_json(output_dir / "result.json", skeleton)
    changes.append({"ts": ts_for(args.repro), "action": "skeleton_written",
                    "reason": "partial result written before any long analysis"})

    if args.skeleton_only:
        write_text(output_dir / "report.md",
                   "# slides-01 partial result\n\nskeleton only (no analysis run yet).\n")
        print(json.dumps({"case_id": case_id, "status": "partial",
                          "result": str(output_dir / "result.json")}, ensure_ascii=False))
        return 0

    # 3) input fingerprint + material discovery
    fp = input_fingerprint(input_root)
    materials = discover_materials(input_root)

    # 4) load + validate draft
    draft_path = Path(args.draft).expanduser() if args.draft else (work_dir / "draft.json")
    draft, draft_err = read_json(draft_path)
    if draft_err:
        errors.append("draft.json unavailable: %s" % draft_err)
        draft = {}
    draft_valid, draft_errors = validate_draft(draft) if isinstance(draft, dict) else (False, [])
    if not draft_valid:
        errors.extend(draft_errors)

    # 5) template discovery
    template_path = Path(args.template).expanduser() if args.template else None
    if template_path is None and isinstance(draft, dict):
        t = draft.get("template")
        if t:
            cand = input_root / str(t)
            if cand.is_file():
                template_path = cand
    if template_path is None:
        for m in materials:
            if m["kind"] == "template":
                template_path = input_root / m["path"]
                break

    # 6) build pptx deterministically
    pptx_path = output_dir / "presentation.pptx"
    inherited = False
    output_fp = ""
    if draft_valid and isinstance(draft, dict):
        parts, inherited, notes = build_presentation_parts(draft, template_path, input_root)
        write_zip(pptx_path, parts)
        output_fp = parts_fingerprint(parts)
        degradations.extend(notes)
    else:
        # honest partial: write an empty-but-valid single-slide deck so a file exists
        from pptx_core import _build_minimal_parts  # noqa: F401
        empty = {"pages": [{"role": "other", "claim": "draft missing",
                            "title": "Incomplete", "bullets": [],
                            "evidence": []}]}
        parts, inherited, _ = build_presentation_parts(empty, None, input_root)
        write_zip(pptx_path, parts)
        output_fp = parts_fingerprint(parts)

    pptx_ok, pptx_msg = is_valid_pptx(pptx_path)
    slide_count = pptx_slide_count(pptx_path)
    pptx_sha = sha256_file(pptx_path)

    # 7) PDF consistency check (PDF is rendered externally by office-renderer)
    pdf_path = Path(args.pdf).expanduser() if args.pdf else (output_dir / "presentation.pdf")
    pdf_pages = 0
    pdf_sha = ""
    pdf_matches = False
    if pdf_path.is_file():
        pdf_pages = pdf_page_count(pdf_path)
        pdf_sha = sha256_file(pdf_path)
        pdf_matches = pdf_pages > 0 and pdf_pages == slide_count
        if not pdf_matches:
            degradations.append("PDF page count %d != slide count %d" % (pdf_pages, slide_count))
    else:
        degradations.append("presentation.pdf not present; export via office-renderer "
                            "(LibreOffice headless) then re-run with --pdf")

    # 8) compliance checks
    compliance = [
        {"id": "contract_fields", "check": "all output_contract fields present",
         "status": "pass", "detail": "%d fields" % len(keys)},
        {"id": "case_id", "check": "case_id is non-empty",
         "status": "pass" if case_id and case_id != "unknown-case" else "fail",
         "detail": case_id},
        {"id": "pptx_valid", "check": "editable .pptx opens as valid OOXML package",
         "status": "pass" if pptx_ok else "fail", "detail": pptx_msg},
        {"id": "template_inherited", "check": "template master/layout/theme inherited",
         "status": "pass" if inherited else "warn",
         "detail": "inherited" if inherited else "no template, minimal deck used"},
        {"id": "slide_count", "check": "slide count equals draft page count",
         "status": "pass" if draft_valid and slide_count == len(draft.get("pages") or [])
                  else "warn", "detail": "%d slides" % slide_count},
        {"id": "pdf_pages_match", "check": "PDF page count equals slide count",
         "status": "pass" if pdf_matches else ("skip" if pdf_pages == 0 else "fail"),
         "detail": "%d pdf pages vs %d slides" % (pdf_pages, slide_count)},
        {"id": "deterministic_build", "check": "build output is content-fingerprinted",
         "status": "pass" if output_fp else "fail", "detail": output_fp[:16]},
    ]
    uncertainty: list = []
    if draft_valid:
        verified, total = evidence_checks(draft, work_dir, compliance, uncertainty)
        compliance.append({"id": "evidence_quotes", "check": "evidence quotes verified verbatim",
                           "status": "pass" if total and verified == total
                           else ("fail" if total else "skip"),
                           "detail": "%d/%d verified" % (verified, total)})

    # 9) content_map + sources
    content_map = []
    if draft_valid:
        for i, page in enumerate(draft.get("pages") or [], 1):
            content_map.append({
                "page": i, "role": page.get("role"), "claim": page.get("claim"),
                "title": page.get("title"), "layout": page.get("layout"),
                "evidence": [
                    {"source_id": e.get("source_id"), "locator": e.get("locator"),
                     "quote": e.get("quote"), "kind": e.get("kind")}
                    for e in (page.get("evidence") or [])],
            })
    sources = [{"source_id": m["path"], "path": m["path"], "kind": m["kind"],
                "size": m["size"], "sha256": m["sha256"], "skipped": m["skipped"]}
               for m in materials]

    # 10) status decision
    if case_err or (not draft_valid and draft.get("pages") is None):
        status = "input_error" if case_err and not draft_valid else "partial"
    elif draft_valid and pptx_ok and pdf_matches and not errors:
        status = "ok"
    elif not draft_valid:
        status = "insufficient_evidence" if not draft.get("pages") else "partial"
    else:
        status = "partial"

    changes.append({"ts": ts_for(args.repro), "action": "pptx_built",
                    "reason": "editable source written, %d slides" % slide_count})
    changes.append({"ts": ts_for(args.repro), "action": "pdf_checked",
                    "reason": "pages=%d matches=%s" % (pdf_pages, pdf_matches)})

    runtime = {
        "started": ts_for(args.repro), "finished": ts_for(args.repro),
        "duration_s": 0 if args.repro else round(time.time() - _start_time, 1),
        "input_fingerprint": fp, "draft_fingerprint": content_hash(draft),
        "output_fingerprint": output_fp, "deterministic_build": True,
        "repro_mode": args.repro, "version": VERSION,
        "python": sys.version.split()[0],
    }

    result = {k: None for k in keys}
    for k in keys:
        result[k] = default_value(k)
    result["case_id"] = case_id
    result["status"] = status
    result["editable_source"] = {"path": "presentation.pptx", "format": "pptx",
                                 "slides": slide_count, "sha256": pptx_sha,
                                 "inherited_template": inherited}
    result["export_artifact"] = {"pdf": {"path": "presentation.pdf", "pages": pdf_pages,
                                         "sha256": pdf_sha, "matches_slides": pdf_matches}}
    if "content_map" in result:
        result["content_map"] = content_map
    if "compliance_checks" in result:
        result["compliance_checks"] = compliance
    if "exceptions" in result:
        result["exceptions"] = [d for d in degradations if d.startswith("PDF") or "not embeddable" in d]
    result["sources"] = sources
    result["uncertainty"] = uncertainty
    result["pending_human"] = []
    result["errors"] = errors
    result["degradations"] = degradations
    result["change_log"] = changes
    result["changes"] = changes
    result["runtime"] = runtime

    write_json(output_dir / "result.json", result)
    write_report(output_dir / "report.md", result)

    print(json.dumps({"case_id": case_id, "status": status, "slides": slide_count,
                      "pdf_pages": pdf_pages, "inherited": inherited,
                      "output_fingerprint": output_fp[:16],
                      "result": str(output_dir / "result.json")}, ensure_ascii=False))
    return 0


def write_report(path: Path, result: dict):
    lines = [
        "# slides-01 运行报告",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- slides: %s" % result.get("editable_source", {}).get("slides"),
        "- inherited_template: %s" % result.get("editable_source", {}).get("inherited_template"),
        "- output_fingerprint: `%s`" % str(result.get("runtime", {}).get("output_fingerprint", ""))[:16],
        "",
        "## 自检结果",
        "",
    ]
    for c in result.get("compliance_checks", []):
        lines.append("- [%s] %s — %s" % (
            "x" if c["status"] == "pass" else ("?" if c["status"] == "skip" else " "),
            c["check"], c["detail"]))
    if result.get("uncertainty"):
        lines.append("")
        lines.append("## 不确定项")
        for u in result["uncertainty"]:
            lines.append("- %s: %s" % (u.get("item"), u.get("reason")))
    if result.get("errors"):
        lines.append("")
        lines.append("## 错误")
        for e in result["errors"]:
            lines.append("- %s" % e)
    if result.get("degradations"):
        lines.append("")
        lines.append("## 降级说明")
        for d in result["degradations"]:
            lines.append("- %s" % d)
    write_text(path, "\n".join(lines) + "\n")


_start_time = time.time()

if __name__ == "__main__":
    raise SystemExit(main())
