#!/usr/bin/env python3
"""Academic-writing Skill bootstrap (stdlib only).

Reads /input/case.json + /input/task.md and any bundled materials, writes an
honest PARTIAL result.json early, plus material_index.json / contract_meta.json
under the work dir.  Designed so that no malformed or missing input can crash
the process: every failure is recorded in errors/degradations and the run still
exits 0 with a valid result.json.

Usage:
    python3 scripts/main.py [--case /input/case.json] [--task /input/task.md]
                            [--output /app/output] [--work /app/work]
                            [--max-bytes 2097152]
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

VERSION = "2.0.0"
MAX_MATERIAL_BYTES_DEFAULT = 2 * 1024 * 1024  # 2 MB per file
MAX_PREVIEW_CHARS = 4000
MAX_ITEMS_PER_FILE = 500

STATUS_SET = {
    "in_progress",
    "partial",
    "complete",
    "failed",
    "insufficient_evidence",
    "input_error",
}

DEFAULT_CONTRACT = {
    "deliverable": {
        "format": "editable_markdown",
        "path": "manuscript.md",
        "note": "可编辑章节稿：保留章节层级、图注编号与引用格式；非提纲/建议/素材堆叠",
    },
    "fact_source_map": {
        "format": "json",
        "path": "evidence_map.json",
        "note": "关键结论→材料locator+逐字quote；[S:]与[C:]三向对齐",
    },
    "version_limits": {
        "note": "改动留痕、人工确认项、材料缺口、异常与limitations均须显式标注",
    },
}

# material kind by extension
KIND_MAP = {
    ".md": "text", ".txt": "text", ".csv": "text", ".tsv": "text",
    ".json": "text", ".tex": "text", ".bib": "text", ".yaml": "text",
    ".yml": "text", ".rst": "text", ".html": "text", ".xml": "text",
    ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".bmp": "image", ".tif": "image", ".tiff": "image",
    ".webp": "image", ".svg": "image",
    ".docx": "office", ".xlsx": "office", ".pptx": "office", ".doc": "office",
    ".xls": "office", ".ppt": "office",
}

NUMBER_RE = re.compile(
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"\s*(%|‰|°C|℃|K|mm|cm|m|km|s|ms|min|h|kg|g|mg|μg|ug|mol|mmol|M|mM|μM|uM|nM|"
    r"L|mL|μL|uL|Hz|kHz|MHz|GHz|N|Pa|kPa|MPa|nm|μm|um|Å|eV|keV|MeV|GeV|IU|U)?"
)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"(?:arXiv\s*:|arxiv\.org/abs/)\s*([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.I)
URL_RE = re.compile(r"https?://[^\s)\]]+")

CONTROL_NAMES = {"case.json", "task.md"}


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text_robust(path, limit=None):
    """Return (text_or_None, encoding_used_or_None, error_or_None). Never raises."""
    if not os.path.exists(path):
        return None, None, "missing_file"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, None, "stat_failed:%s" % exc
    if limit is not None and size > limit:
        return None, None, "too_large:%d>%d" % (size, limit)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return None, None, "open_failed:%s" % exc
    # latin-1 never fails, so we should not get here; keep a defensive fallback
    return None, None, "decode_failed"


def load_json_robust(path):
    """Return (dict_or_None, error_or_None). Never raises on malformed JSON."""
    text, _enc, err = read_text_robust(path)
    if text is None:
        return None, err
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, "bad_json:%s" % exc
    if not isinstance(data, dict):
        return None, "json_not_object"
    return data, None


def ensure_dir(path):
    """mkdir -p with a safe fallback; returns (dir, degradation_or_None)."""
    try:
        os.makedirs(path, exist_ok=True)
        return path, None
    except OSError as exc:
        fallback = os.path.join(os.getcwd(), "skill_runtime_output")
        try:
            os.makedirs(fallback, exist_ok=True)
            return fallback, "makedirs_failed:%s;using_fallback" % exc
        except OSError as exc2:
            return ".", "makedirs_and_fallback_failed:%s" % exc2


def write_json(path, obj):
    """Write UTF-8 JSON atomically-ish; returns error string or None."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        return None
    except OSError as exc:
        return "write_failed:%s:%s" % (path, exc)


def extract_from_text(text, ref_id):
    """Light, deterministic evidence extraction: numbers + references found."""
    numbers = []
    refs = []
    lines = text.splitlines()
    for idx, raw in enumerate(lines):
        line = raw.rstrip("\r")
        lineno = idx + 1
        for m in NUMBER_RE.finditer(line):
            if len(numbers) >= MAX_ITEMS_PER_FILE:
                break
            numbers.append({
                "value": m.group(1),
                "unit": m.group(2) or "",
                "locator": "%s@L%d" % (ref_id, lineno),
                "quote": line.strip()[:300],
            })
        for doi in DOI_RE.findall(line):
            if len(refs) >= MAX_ITEMS_PER_FILE:
                break
            refs.append({"type": "doi", "value": doi.rstrip(".,;")[:300]})
        for arx in ARXIV_RE.findall(line):
            if len(refs) >= MAX_ITEMS_PER_FILE:
                break
            refs.append({"type": "arxiv", "value": "arXiv:%s" % arx[:50]})
    return numbers, refs


def scan_materials(input_dir, max_bytes):
    """Walk input_dir, index files, skip oversized/unreadable. Never raises."""
    mats = {}
    degradations = []
    if not os.path.isdir(input_dir):
        return mats, ["input_dir_missing:%s" % input_dir]
    for root, _dirs, files in os.walk(input_dir):
        _dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            if name.lower() in CONTROL_NAMES:
                continue  # case.json / task.md handled separately
            try:
                size = os.path.getsize(full)
            except OSError as exc:
                degradations.append("stat_failed:%s:%s" % (rel, exc))
                continue
            if size > max_bytes:
                degradations.append("skipped_oversize:%s:%d>%d" % (rel, size, max_bytes))
                mats[rel] = {
                    "ref_id": _ref_id(rel), "source": full, "rel_path": rel,
                    "size": size, "kind": KIND_MAP.get(os.path.splitext(name)[1].lower(), "unknown"),
                    "parse_status": "skipped_oversize", "error": "oversize",
                    "key_claims": [], "numbers": [], "citations": [], "flags": [],
                }
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = KIND_MAP.get(ext, "unknown")
            entry = {
                "ref_id": _ref_id(rel), "source": full, "rel_path": rel,
                "size": size, "kind": kind, "encoding": None,
                "parse_status": "indexed", "error": None,
                "preview": "", "key_claims": [], "numbers": [], "citations": [],
                "flags": [], "agent_notes": "",
            }
            if kind == "text":
                text, enc, err = read_text_robust(full, limit=max_bytes)
                if text is None:
                    entry["parse_status"] = "unreadable"
                    entry["error"] = err
                    degradations.append("unreadable:%s:%s" % (rel, err))
                else:
                    entry["encoding"] = enc
                    entry["preview"] = text[:MAX_PREVIEW_CHARS]
                    numbers, refs = extract_from_text(text, entry["ref_id"])
                    entry["numbers"] = numbers
                    entry["citations"] = refs
                    if enc not in ("utf-8", "utf-8-sig"):
                        degradations.append("non_utf8:%s:%s" % (rel, enc))
            else:
                entry["parse_status"] = "needs_parse"
                entry["error"] = "requires_%s_tool" % kind
            mats[rel] = entry
    return mats, degradations


def _ref_id(rel_path):
    """Deterministic short id, e.g. paper.pdf -> mat-paper-pdf."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", rel_path).strip("-")
    if not slug:
        slug = "material"
    return "mat-" + slug[:60]


def merge_index(old, new):
    """Preserve agent annotations from an existing index while refreshing scan data."""
    merged = dict(old)
    for rel, entry in new.items():
        if rel in merged:
            prev = merged[rel]
            for field in ("key_claims", "agent_notes"):
                if prev.get(field):
                    entry[field] = prev[field]
            for field in ("flags", "numbers", "citations"):
                if prev.get(field):
                    entry[field] = prev[field]
        merged[rel] = entry
    return merged


def build_result(case, contract, case_id, errors, degradations, existing,
                 artifacts, material_count):
    out = {}
    out["case_id"] = case_id
    out["output_contract"] = contract
    for key, val in contract.items():
        out[key] = val  # echo every contract field at top level
    out["status"] = (existing or {}).get("status", "partial")
    if out["status"] not in STATUS_SET:
        out["status"] = "partial"
    prev = existing or {}
    out["sources"] = prev.get("sources", [])
    out["uncertainty"] = prev.get("uncertainty", [])
    out["pending_human"] = prev.get("pending_human", [])
    out["change_log"] = list(prev.get("change_log", []))
    out["change_log"].append({
        "ts": now_ts(), "step": "bootstrap",
        "note": "落盘partial骨架；材料%d个；错误%d条；降级%d条" % (
            material_count, len(errors), len(degradations)),
    })
    out["errors"] = errors
    out["degradations"] = degradations
    out["runtime"] = {
        "tool": "scripts/main.py", "version": VERSION, "ts": now_ts(),
    }
    out["artifacts"] = artifacts
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--max-bytes", type=int, default=MAX_MATERIAL_BYTES_DEFAULT)
    args = ap.parse_args(argv)

    errors = []
    degradations = []

    out_dir, deg = ensure_dir(args.output)
    if deg:
        degradations.append(deg)
    work_dir, deg = ensure_dir(args.work)
    if deg:
        degradations.append(deg)

    # --- case.json ---
    case, case_err = load_json_robust(args.case)
    if case_err:
        errors.append("case.json:%s" % case_err)
    case = case or {}
    case_id = case.get("id") or case.get("case_id") or os.path.basename(args.case)

    # --- task.md (informational) ---
    task_text, _enc, task_err = read_text_robust(args.task)
    if task_err:
        degradations.append("task.md:%s" % task_err)

    # --- output_contract ---
    contract = case.get("output_contract")
    if isinstance(contract, dict) and contract:
        # missing contract values -> None; never drop unknown keys
        contract = {k: (contract[k] if contract[k] is not None else None)
                    for k in contract}
    else:
        contract = dict(DEFAULT_CONTRACT)

    # --- scan materials ---
    input_dir = os.path.dirname(os.path.abspath(args.case)) or "/input"
    mats, mat_deg = scan_materials(input_dir, args.max_bytes)
    degradations.extend(mat_deg)

    # --- merge with existing index (incremental, never wipe agent work) ---
    index_path = os.path.join(work_dir, "material_index.json")
    old_index, idx_err = load_json_robust(index_path)
    if old_index is None:
        old_index = {}
    merged = merge_index(old_index, mats)

    artifacts = {
        "manuscript": "manuscript.md",
        "material_index": "material_index.json",
        "evidence_map": "evidence_map.json",
        "consistency_report": "consistency_report.json",
    }

    existing_result = None
    result_path = os.path.join(out_dir, "result.json")
    existing_result, res_err = load_json_robust(result_path)
    if res_err and os.path.exists(result_path):
        degradations.append("existing_result_unreadable:%s" % res_err)

    result = build_result(case, contract, case_id, errors, degradations,
                          existing_result, artifacts, len(merged))

    # --- write outputs; each failure recorded, none fatal ---
    w1 = write_json(os.path.join(work_dir, "material_index.json"), merged)
    if w1:
        degradations.append(w1)
    meta = {
        "case_id": case_id, "contract": contract,
        "input_dir": input_dir, "case_path": args.case,
        "task_path": args.task, "ts": now_ts(),
        "material_count": len(merged), "status_hint": result["status"],
    }
    w2 = write_json(os.path.join(work_dir, "contract_meta.json"), meta)
    if w2:
        degradations.append(w2)
    w3 = write_json(result_path, result)
    if w3:
        degradations.append(w3)
        result["status"] = "failed"
        # last-resort write to cwd
        fb = os.path.join(os.getcwd(), "result_fallback.json")
        degradations.append(write_json(fb, result) or "fallback_ok")

    print(json.dumps({
        "ok": True,
        "case_id": case_id,
        "status": result["status"],
        "material_count": len(merged),
        "errors": len(errors),
        "degradations": len(degradations),
        "out_dir": out_dir,
        "work_dir": work_dir,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
