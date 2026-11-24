#!/usr/bin/env python3
"""core_impl: deterministic scaffolding + contract verification for format-template-adaptation.

Standard library only. All paths default to /input, /app/output, /app/work.
The Agent performs semantic writing; these routines provide the structural
intermediate representations (template profile, content-unit anchor) and the
idempotent partial-skeleton / verification loop.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024

STATUS_VALUES = {"partial", "complete", "failed"}

CONTRACT_FALLBACK = {
    "deliverable": None,
    "fact_source_map": [],
    "version_boundary": {
        "change_diff": [],
        "human_confirmation": [],
        "material_gaps": [],
        "anomalies": [],
    },
}

RESERVED = {
    "status": "partial",
    "sources": [],
    "uncertainty": {
        "claims_requiring_confirmation": [],
        "conflicting_evidence": [],
        "material_gaps": [],
        "limitations": [],
    },
    "pending_human": [],
    "change_log": [],
}

# (domain, [spec_names]) — the checkable spec sheet, mirroring 认得准/排得对/转得全/交得出
SPEC_DOMAINS = [
    ("page", ["page_size", "margins", "columns", "header_footer"]),
    ("typography", ["font_family", "font_size", "line_spacing", "paragraph_spacing"]),
    ("heading", ["heading_levels", "numbering_rule", "style_binding", "toc_bookmark"]),
    ("figure_table", ["caption_position", "caption_font_size", "numbering_rule", "cross_reference", "table_style", "reference_style"]),
    ("export", ["export_format", "font_embedding", "page_numbers", "toc_refresh", "editability"]),
]

WORK_DUMP_BLOCKLIST = {
    "material_index.json", "errors.json", "spec_scaffold.json",
    "auto_spec_hints.json", "content_unit_scaffold.json",
}

TEXT_EXT = {".txt", ".md", ".json", ".csv", ".tsv", ".tex", ".bib", ".xml", ".html", ".htm", ".yaml", ".yml"}


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def read_text_safe(path):
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return None, str(e)
    if len(data) > MAX_TEXT_FILE_BYTES:
        return None, f"file exceeds {MAX_TEXT_FILE_BYTES} bytes"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc, errors="strict"), None
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "fallback replace decode"


def read_json_safe(path):
    text, err = read_text_safe(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        try:
            return json.loads(text.lstrip("\ufeff")), None
        except json.JSONDecodeError as e2:
            return None, f"bad json: {e2}"


def normalize_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def discover_materials(root):
    root = Path(root)
    items = []
    if not root.exists():
        return items, f"input root not found: {root}"
    try:
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            it = {"path": str(p), "name": p.name, "size": size, "skipped": None}
            if size > MAX_TEXT_FILE_BYTES:
                it["skipped"] = "oversized"
            items.append(it)
    except OSError as e:
        return items, str(e)
    return items, None


def extract_docx_text(path):
    try:
        parts = []
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            for member in ("word/document.xml", "word/footnotes.xml", "word/comments.xml"):
                if member in names:
                    xml = z.read(member).decode("utf-8", "replace")
                    xml = xml.replace("</w:p>", "\n")
                    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
                    parts.append(f"--- {member} ---\n" + re.sub(r"<[^>]+>", "", xml))
        return "\n".join(parts), None
    except (zipfile.BadZipFile, OSError, KeyError) as e:
        return None, str(e)


def extract_pptx_text(path):
    try:
        parts = []
        with zipfile.ZipFile(path) as z:
            for n in sorted(z.namelist()):
                if re.match(r"ppt/slides/slide\d+\.xml$", n):
                    xml = z.read(n).decode("utf-8", "replace")
                    xml = xml.replace("</a:p>", "\n")
                    parts.append(f"--- {n} ---\n" + re.sub(r"<[^>]+>", "", xml))
        return "\n".join(parts), None
    except (zipfile.BadZipFile, OSError) as e:
        return None, str(e)


def extract_material_text(path):
    ext = Path(path).suffix.lower()
    if ext in TEXT_EXT:
        return read_text_safe(path)
    if ext == ".docx":
        return extract_docx_text(path)
    if ext == ".pptx":
        return extract_pptx_text(path)
    if ext == ".pdf":
        return None, "PDF requires platform pdf-text-tool (not performed by this script)"
    return None, f"unsupported extension: {ext}"


def build_material_index(roots):
    """Discover and extract readable material text across one or more roots."""
    index = {"materials": [], "corpus": {}, "errors": []}
    seen = set()
    for root in roots:
        items, err = discover_materials(root)
        if err:
            index["errors"].append(err)
            continue
        for it in items:
            key = it["path"]
            if key in seen:
                continue
            seen.add(key)
            if it["name"] in WORK_DUMP_BLOCKLIST:
                continue
            if it.get("skipped"):
                index["errors"].append(f"{key}: {it['skipped']}")
                continue
            text, err2 = extract_material_text(key)
            if text is None:
                index["errors"].append(f"{key}: {err2}")
                index["materials"].append({"path": key, "name": it["name"], "extractable": False, "error": err2})
                continue
            index["corpus"][key] = normalize_text(text)
            index["materials"].append({"path": key, "name": it["name"], "size": it["size"], "extractable": True, "chars": len(text)})
    return index


def build_spec_scaffold():
    rows = []
    i = 0
    for domain, names in SPEC_DOMAINS:
        for name in names:
            i += 1
            rows.append({
                "spec_id": f"spec-{i:03d}",
                "domain": domain,
                "spec_name": name,
                "required_value": None,
                "measured_value": None,
                "compliance": "pending",
                "source_locator": None,
                "quote": None,
                "confidence": "low",
                "note": "auto-scaffold; fill from template or mark not_specified",
            })
    return rows


def auto_spec_hints(raw_text):
    """Conservative regex hints. All output is confidence=low and requires human confirmation."""
    if not raw_text:
        return []
    hints = []
    patterns = [
        ("typography", "font_size", r"(\d+(?:\.\d+)?)\s*(?:pt|磅|号)", "font size hint"),
        ("page", "margins", r"(上|下|左|右|上下|左右)\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(cm|厘米|mm|毫米|in|英寸)", "margin hint"),
        ("typography", "line_spacing", r"(1\.5|2|单倍|双倍|1\.5倍|2倍)\s*(倍行距|行距|倍)", "line spacing hint"),
        ("page", "columns", r"(单|双|\d+)\s*(栏|列|columns?|column?)", "column hint"),
    ]
    for domain, name, pat, label in patterns:
        for m in re.finditer(pat, raw_text, re.IGNORECASE):
            s = max(0, m.start() - 30)
            e = min(len(raw_text), m.end() + 30)
            hints.append({
                "domain": domain,
                "spec_name": name,
                "measured_value": m.group(0).strip(),
                "confidence": "low",
                "auto": True,
                "label": label,
                "quote": raw_text[s:e].strip(),
                "locator": f"auto-match@{m.start()}",
            })
    return hints


def build_content_unit_scaffold():
    return {
        "units": [],
        "note": "fill each unit with unit_id/unit_type/source_locator/quote/dest_locator/preserved/transform",
        "unit_types": ["paragraph", "heading", "table", "figure", "formula", "footnote", "comment", "revision", "field", "toc", "reference_entry"],
    }


def resolve_case_id(case):
    if isinstance(case, dict):
        for k in ("case_id", "id", "caseId", "case.id"):
            if k in case and isinstance(case[k], str) and case[k]:
                return case[k]
    return ""


def resolve_output_contract(case):
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict) and oc:
            return dict(oc), False
        for k in ("output", "outputs", "result_contract"):
            v = case.get(k)
            if isinstance(v, dict) and v:
                return dict(v), False
    return dict(CONTRACT_FALLBACK), True


def build_skeleton(case_path):
    case, err = read_json_safe(case_path)
    if case is None:
        return None, err
    case_id = resolve_case_id(case)
    contract, fallback = resolve_output_contract(case)
    result = {"case_id": case_id, "status": "partial"}
    for k, v in contract.items():
        result[k] = v
    for k, v in RESERVED.items():
        result[k] = json.loads(json.dumps(v))
    result["change_log"].append({"ts": now_iso(), "action": "scaffold", "detail": "partial skeleton written"})
    result["_meta"] = {
        "contract_fields": list(contract.keys()),
        "contract_fallback_used": fallback,
        "budget": {"seconds": 900, "model_requests": 64, "max_tokens_per_response": 16384},
        "generated_by": "scripts/main.py scaffold",
    }
    return result, None


def merge_result(existing, new_result):
    """Idempotent incremental merge: never overwrite non-empty Agent-filled fields."""
    if not isinstance(existing, dict):
        existing = {}
    merged = dict(existing)
    for k, v in new_result.items():
        if k == "change_log":
            merged.setdefault("change_log", [])
            for entry in v:
                if not any(e.get("ts") == entry.get("ts") and e.get("action") == entry.get("action")
                           for e in merged["change_log"]):
                    merged["change_log"].append(entry)
        elif k not in merged or merged[k] in (None, "", [], {}):
            merged[k] = v
    return merged


def verify_contract(result_path, case_path, roots, report_path):
    report = {"ok": True, "exit_code": 0, "errors": [], "warnings": [], "checks": {}}
    result, err = read_json_safe(result_path)
    if result is None:
        report["ok"] = False
        report["exit_code"] = 2
        report["errors"].append(f"result.json unreadable: {err}")
        return report
    if not isinstance(result, dict):
        report["ok"] = False
        report["exit_code"] = 2
        report["errors"].append("result.json top-level must be object")
        return report

    case, _ = read_json_safe(case_path)
    case_id_expected = resolve_case_id(case) if isinstance(case, dict) else ""
    contract, _ = resolve_output_contract(case) if isinstance(case, dict) else (dict(CONTRACT_FALLBACK), True)

    # 1 case_id
    got_cid = result.get("case_id")
    if not got_cid:
        report["errors"].append("missing case_id")
        report["exit_code"] = 2
        report["checks"]["case_id"] = False
    elif case_id_expected and got_cid != case_id_expected:
        report["errors"].append(f"case_id mismatch: result={got_cid!r} expected={case_id_expected!r}")
        report["exit_code"] = 2
        report["checks"]["case_id"] = False
    else:
        report["checks"]["case_id"] = True

    # 2 status
    st = result.get("status")
    if st not in STATUS_VALUES:
        report["errors"].append(f"invalid status: {st!r}")
        report["exit_code"] = 2
    report["checks"]["status"] = st in STATUS_VALUES

    # 3 contract fields (verbatim, all present)
    missing = [k for k in contract.keys() if k not in result]
    if missing:
        report["errors"].append(f"missing contract fields: {missing}")
        report["exit_code"] = 2
    report["checks"]["contract_fields"] = len(missing) == 0

    # 4 reserved fields
    for k in ("sources", "uncertainty", "change_log", "pending_human"):
        if k not in result:
            report["errors"].append(f"missing reserved field: {k}")
            report["exit_code"] = 2
    report["checks"]["reserved_fields"] = all(k in result for k in ("sources", "uncertainty", "change_log", "pending_human"))

    # 5 quote locatability against material corpus
    index = build_material_index(roots)
    fs = result.get("fact_source_map") or []
    n_checked = n_ok = n_missing = 0
    for item in fs:
        if not isinstance(item, dict):
            continue
        q = item.get("quote")
        if not q or not isinstance(q, str) or len(normalize_text(q)) < 6:
            continue
        n_checked += 1
        nq = normalize_text(q)
        found = any(nq in corpus for corpus in index["corpus"].values())
        if found:
            n_ok += 1
        else:
            n_missing += 1
            report["warnings"].append(
                f"quote not locatable: claim_id={item.get('claim_id')!r} locator={item.get('locator')!r}")
    report["checks"]["quote_locatable"] = {"checked": n_checked, "located": n_ok, "missing": n_missing}

    # 6 placeholder / fabrication markers
    blob = json.dumps(result, ensure_ascii=False)
    for marker in ("TODO", "FIXME", "占位", "待填", "placeholder", "lorem"):
        if marker in blob:
            report["warnings"].append(f"placeholder marker found: {marker}")
            break

    # 7 referenced artifacts exist
    dlv = result.get("deliverable")
    if isinstance(dlv, dict):
        for key in ("artifact_path", "preview_path"):
            ap = dlv.get(key)
            if ap and isinstance(ap, str) and not Path(ap).exists():
                report["warnings"].append(f"{key} not found: {ap}")

    if report["exit_code"] == 0 and report["warnings"]:
        report["exit_code"] = 1
    report["ok"] = report["exit_code"] != 2
    return report


def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
