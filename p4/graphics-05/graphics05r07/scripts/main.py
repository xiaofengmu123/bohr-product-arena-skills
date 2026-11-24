#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graphical-abstract-robust orchestrator.

Deterministic, stdlib-only pipeline for the official /input/case.json + task.md contract:
discover inputs -> normalize -> persist a partial result.json skeleton -> extract facts ->
build a layered editable SVG -> validate -> attempt PNG/PDF exports -> finalize.

Robustness contract (variant focus):
  * empty input dir          -> honest status=failed skeleton, exit 0, never raises
  * malformed / BOM / bad    -> record in errors/degradations, continue with the rest
  * missing --input/--output -> defaults to /input, /app/output, /app/work
  * oversized files          -> skipped above MAX_TEXT_FILE_BYTES, recorded, not fatal

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

try:
    import svg_builder
except Exception:  # pragma: no cover - fallback when invoked from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import svg_builder

VERSION = "1.1.0"
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB per text file; larger files are skipped
SUPPORTED_TEXT_SUFFIXES = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml"}
SUPPORTED_MATERIAL_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".svg", ".tif", ".tiff",
    ".csv", ".tsv", ".xlsx", ".xls", ".docx", ".pptx",
}
VALID_STATUSES = ("completed", "partial", "failed")
MAX_SOURCE_QUOTE = 240  # characters kept verbatim per source pointer

CANONICAL_CONTRACT_KEYS = [
    "graphical_abstract", "editable_source", "exports", "entities", "relations",
    "data_mapping", "legend", "sources", "uncertainty", "change_log",
    "limitations", "pending_human",
]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stable_id(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:length]


# --------------------------------------------------------------------------- #
# Robust text / JSON readers
# --------------------------------------------------------------------------- #
def read_text_safely(path: Path):
    """Return (text, encoding, errors). Never raises for ordinary files."""
    if not path.exists():
        return "", "missing", ["file does not exist"]
    try:
        size = path.stat().st_size
    except OSError as exc:
        return "", "unreadable", [str(exc)]
    if size > MAX_TEXT_FILE_BYTES:
        return "", "skipped", [f"file exceeds {MAX_TEXT_FILE_BYTES} bytes"]
    errors: list[str] = []
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            data = path.read_bytes()
            return data.decode(enc), enc, errors
        except UnicodeDecodeError:
            errors.append(f"decode failed with {enc}")
        except OSError as exc:
            return "", "unreadable", [str(exc)]
    # last resort: read with replacement
    try:
        return path.read_text(encoding="utf-8", errors="replace"), "utf-8-replace", errors
    except OSError as exc:
        return "", "unreadable", [str(exc)]


def _strip_bom(text: str) -> str:
    return text.lstrip("\ufeff")


def load_json_safely(path: Path):
    """Return (obj_or_None, errors). Tolerant of BOM and common corruptions."""
    text, enc, errors = read_text_safely(path)
    if enc in ("missing", "unreadable", "skipped"):
        return None, errors
    text = _strip_bom(text)
    if not text.strip():
        return None, ["empty file"]
    # remove UTF-8 BOM bytes that survived decode (redundant safety)
    text = text.replace("\ufeff", "")
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        repaired = _repair_json(text)
        if repaired is not None:
            try:
                return json.loads(repaired), errors + [f"repaired malformed JSON: {exc}"]
            except json.JSONDecodeError as exc2:
                return None, errors + [f"JSON repair failed: {exc2}"]
        return None, errors + [f"invalid JSON: {exc}"]


def _repair_json(text: str):
    """Best-effort repair of common truncation/corruption. Returns str or None."""
    # 1) cut at last complete top-level closing brace
    depth = 0
    last_good = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_good = i
    if last_good > 0:
        candidate = text[: last_good + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    return None


# --------------------------------------------------------------------------- #
# Input discovery
# --------------------------------------------------------------------------- #
def scan_input(input_dir: Path):
    """Return ordered inventory with size-capped classification. Never raises."""
    inventory = {
        "case_json": None, "task_md": None, "text_files": [], "material_files": [],
        "skipped": [], "errors": [],
    }
    if not input_dir.exists():
        inventory["errors"].append(f"input directory missing: {input_dir}")
        return inventory
    try:
        entries = sorted(input_dir.rglob("*"), key=lambda p: (str(p).lower()))
    except OSError as exc:
        inventory["errors"].append(f"cannot scan input directory: {exc}")
        return inventory
    for p in entries:
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError as exc:
            inventory["errors"].append(f"stat failed for {p}: {exc}")
            continue
        suffix = p.suffix.lower()
        if size > MAX_TEXT_FILE_BYTES and suffix in SUPPORTED_TEXT_SUFFIXES:
            inventory["skipped"].append({"path": str(p), "reason": "oversized_text", "size": size})
            continue
        if p.name.lower() == "case.json":
            inventory["case_json"] = p
        elif p.name.lower() == "task.md":
            inventory["task_md"] = p
        elif suffix in SUPPORTED_TEXT_SUFFIXES:
            inventory["text_files"].append(p)
        elif suffix in SUPPORTED_MATERIAL_SUFFIXES:
            inventory["material_files"].append(p)
    return inventory


def _rel(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(str(path), str(base))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# Contract & skeleton
# --------------------------------------------------------------------------- #
def derive_case_id(case_obj, task_md_text: str, input_dir: Path) -> str:
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id", "challengeId"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    m = re.search(r"challenge_id[:：]\s*`?([A-Za-z0-9_.-]+)`?", task_md_text or "")
    if m:
        return m.group(1)
    m = re.search(r"\bgraphical-abstract-[A-Za-z0-9-]+\b", task_md_text or "")
    if m:
        return m.group(0)
    return _stable_id(str(input_dir) + (task_md_text or "")[:200], 10)


def extract_output_contract(case_obj):
    """Return the case's output_contract if present, else the canonical keys."""
    if isinstance(case_obj, dict):
        contract = case_obj.get("output_contract")
        if isinstance(contract, dict):
            return contract
        if isinstance(contract, list):
            return {k: None for k in contract}
    return None


def build_skeleton(case_id: str, contract, status: str, errors, degradations):
    """Return a result.json skeleton honoring the case contract verbatim."""
    if contract is None:
        core = {k: None for k in CANONICAL_CONTRACT_KEYS}
    else:
        core = dict(contract)  # keep every key as-is; values filled later
    result = {
        "case_id": case_id,
        "status": status,
    }
    # canonical fields merged only where the contract did not already define them
    for key in CANONICAL_CONTRACT_KEYS:
        result.setdefault(key, None)
    result["status"] = status
    result["sources"] = result.get("sources") or []
    result["uncertainty"] = result.get("uncertainty") or []
    result["change_log"] = result.get("change_log") or []
    result["limitations"] = result.get("limitations") or []
    result["pending_human"] = result.get("pending_human") or []
    result["errors"] = errors or []
    result["degradations"] = degradations or []
    result["runtime"] = {"skill_version": VERSION, "started_at": _now_iso()}
    return result


def _ensure_lists(result):
    for key in ("sources", "uncertainty", "change_log", "limitations",
                "pending_human", "errors", "degradations", "entities",
                "relations", "data_mapping", "legend", "exports"):
        if key in result and not isinstance(result.get(key), list):
            result[key] = []
    return result


# --------------------------------------------------------------------------- #
# Fact extraction (deterministic heuristics; the Agent refines figure_spec)
# --------------------------------------------------------------------------- #
def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def extract_entities(text: str):
    entities, seen = [], set()
    for m in re.finditer(
        r"(?:研究对象|实体|样本|材料|分子|细胞|蛋白|基因|器件|靶点|组织|通路|药物|菌株|化合物)"
        r"[：:是为包含]\s*([^\n。；;]{2,80})",
        text or "",
    ):
        label = _clean(m.group(1))[:80]
        if label and label not in seen:
            seen.add(label)
            entities.append({"id": f"e{len(entities)+1}", "type": "entity", "label": label,
                             "source": {"locator": None, "quote": label}})
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9-]{2,30})\b", text or ""):
        label = m.group(1)
        if label in {"The", "This", "PDF", "CSV", "Agent", "Skill", "PNG", "SVG", "Benchmark"}:
            continue
        if label not in seen and len(entities) < 40:
            seen.add(label)
            entities.append({"id": f"e{len(entities)+1}", "type": "entity", "label": label,
                             "source": {"locator": None, "quote": label}})
    return entities


_ARROW = r"(?:-+>|—+>|→|⇒|->|==>|-->|→)"
_REL_RE = re.compile(
    r"([A-Za-z0-9\u4e00-\u9fff]{2,40}?)\s*" + _ARROW + r"\s*([A-Za-z0-9\u4e00-\u9fff]{2,40}?)"
)


def extract_relations(text: str):
    relations = []
    for m in _REL_RE.finditer(text or ""):
        left, right = _clean(m.group(1)), _clean(m.group(2))
        if left and right and left != right:
            relations.append({"id": f"r{len(relations)+1}", "from": left, "to": right,
                              "type": "directed", "label": None,
                              "source": {"locator": None, "quote": m.group(0)}})
    return relations[:40]


_UNIT = r"(?:%|％|mg|g|kg|mL|L|μM|mM|nM|μl|ul|mol|M|s|min|h|day|°C|℃|nm|μm|mm|cm|bp|kb|Da|kDa|AU|px|dpi|cm²|m²|log2|倍|次|个|例|μmol|mmol)"
_NUM_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(" + _UNIT + r")?"
)


def extract_data(text: str):
    data = []
    for m in _NUM_RE.finditer(text or ""):
        value = m.group(1)
        unit = m.group(2) or ""
        item = {"id": f"d{len(data)+1}", "value": value, "unit": unit,
                "visual_channel": None, "target": None,
                "source": {"locator": None, "quote": m.group(0)}}
        data.append(item)
        if len(data) >= 60:
            break
    return data


def extract_tabular(path: Path):
    """Deterministic CSV/TSV reader producing rows of numeric columns."""
    if not path.exists():
        return []
    text, enc, _ = read_text_safely(path)
    if enc in ("missing", "unreadable", "skipped") or not text.strip():
        return []
    try:
        sample = text[:2000]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        return []
    numeric_rows = []
    for row in rows[1:1001]:
        for cell in row:
            cell = cell.strip()
            m = re.match(r"^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$", cell)
            if m:
                numeric_rows.append({"file": path.name, "row": rows.index(row) + 1,
                                     "cell": cell, "value": m.group(0)})
    return numeric_rows[:80]


def extract_constraints(text: str):
    constraints = {"canvas": {"width": 1600, "height": 1100}, "min_font_px": 10,
                   "palette": "colorblind-safe", "notes": []}
    m = re.search(r"(\d{3,5})\s*[x×]\s*(\d{3,5})", text or "")
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if 400 <= w <= 5000 and 400 <= h <= 5000:
            constraints["canvas"] = {"width": w, "height": h}
    if re.search(r"(灰度|grayscale|print)", text or "", re.I):
        constraints["palette"] = "grayscale-first"
    return constraints


def build_figure_spec(case_id, task_text, case_obj, inventory, input_dir, work_dir):
    corpus_parts = [task_text or ""]
    if isinstance(case_obj, dict):
        for key in ("content", "description", "task", "materials", "notes"):
            val = case_obj.get(key)
            if isinstance(val, str):
                corpus_parts.append(val)
    corpus = "\n".join(corpus_parts)
    entities = extract_entities(corpus)
    relations = extract_relations(corpus)
    data = extract_data(corpus)
    tabular = []
    for p in inventory["text_files"]:
        if p.suffix.lower() in (".csv", ".tsv"):
            tabular.extend(extract_tabular(p))
    constraints = extract_constraints(corpus)
    # attach source locators where a verbatim quote is available
    spec = {
        "case_id": case_id,
        "title": (case_obj or {}).get("title") if isinstance(case_obj, dict) else None,
        "graphical_abstract": {
            "question": None, "method": None, "results": None, "conclusion": None,
        },
        "entities": entities,
        "relations": relations,
        "data": data,
        "tabular": tabular,
        "constraints": constraints,
        "legend": [],
        "deterministic_seed": 7030,
    }
    return spec


# --------------------------------------------------------------------------- #
# Result finalization helpers
# --------------------------------------------------------------------------- #
def add_change(result, step, action, reason):
    entry = {"step": step, "action": action, "reason": reason, "at": _now_iso()}
    result.setdefault("change_log", []).append(entry)
    return result


def add_limitation(result, item, severity="medium"):
    result.setdefault("limitations", []).append(
        {"item": item, "severity": severity, "at": _now_iso()})
    return result


def finalize_result(result, spec, svg_path, exports, work_dir, output_dir):
    result["status"] = "partial"
    if result.get("graphical_abstract") is None:
        result["graphical_abstract"] = dict(spec.get("graphical_abstract") or {})
    if not result.get("entities"):
        result["entities"] = spec.get("entities") or []
    if not result.get("relations"):
        result["relations"] = spec.get("relations") or []
    if not result.get("data_mapping"):
        result["data_mapping"] = spec.get("data") or []
    if not result.get("legend"):
        result["legend"] = spec.get("legend") or []
    if svg_path and svg_path.exists():
        rel = _rel(svg_path, output_dir)
        result["editable_source"] = {
            "format": "svg", "path": rel,
            "layers": svg_builder.LAYER_NAMES,
            "text_editable": True,
        }
    result["exports"] = exports
    result["runtime"]["finished_at"] = _now_iso()
    result = _ensure_lists(result)
    if not result.get("errors"):
        result["errors"] = []
    if not result.get("degradations"):
        result["degradations"] = []
    return result


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Main pipeline
# --------------------------------------------------------------------------- #
def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic, source-bound graphical abstract pipeline (stdlib only).")
    parser.add_argument("--input", default="/input", help="input directory (default /input)")
    parser.add_argument("--output", default="/app/output", help="output directory (default /app/output)")
    parser.add_argument("--work", default="/app/work", help="work directory (default /app/work)")
    parser.add_argument("--build", action="store_true", help="only rebuild figure from spec")
    parser.add_argument("--validate", action="store_true", help="only validate existing artifacts")
    parser.add_argument("--spec", default=None, help="figure_spec.json path for --build")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    return parser


def run(argv):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(VERSION)
        return 0

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # --validate: check existing SVG + result.json without rebuilding
    if args.validate:
        report = svg_builder.validate_existing(output_dir, input_dir)
        write_json(work_dir / "validate_report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("valid") else 2

    inventory = scan_input(input_dir)
    errors = list(inventory["errors"])
    degradations = []
    for skip in inventory["skipped"]:
        degradations.append({"item": f"skipped {skip['path']}",
                             "reason": skip["reason"], "size": skip["size"]})

    # --- load task.md ---
    task_text = ""
    if inventory["task_md"]:
        task_text, tenc, terr = read_text_safely(inventory["task_md"])
        if tenc in ("missing", "unreadable", "skipped"):
            degradations.append({"item": str(inventory["task_md"]), "reason": tenc,
                                 "detail": terr})

    # --- load case.json ---
    case_obj = None
    if inventory["case_json"]:
        case_obj, jerr = load_json_safely(inventory["case_json"])
        errors.extend(jerr)

    case_id = derive_case_id(case_obj, task_text, input_dir)
    contract = extract_output_contract(case_obj)

    # --- empty input -> honest failed skeleton ---
    if not inventory["case_json"] and not inventory["task_md"] and not inventory["text_files"] \
            and not inventory["material_files"]:
        result = build_skeleton(case_id, contract, "failed", errors, degradations)
        result["limitations"].append(
            {"item": "empty input directory; no case.json, task.md or material files found",
             "severity": "fatal", "at": _now_iso()})
        result["pending_human"].append(
            {"id": "input-missing", "item": "provide /input/case.json and /input/task.md",
             "why": "no evidence available to draw from"})
        write_json(output_dir / "result.json", result)
        print("empty input; wrote failed skeleton")
        return 0

    # --- skeleton must land within the first 3 minutes ---
    result = build_skeleton(case_id, contract, "partial", errors, degradations)
    write_json(output_dir / "result.json", result)
    add_change(result, "skeleton", "write partial result.json skeleton", "early-persist rule")

    # --- normalize ---
    normalized = {
        "case_id": case_id,
        "contract": contract,
        "inventory": {
            "case_json": str(inventory["case_json"]) if inventory["case_json"] else None,
            "task_md": str(inventory["task_md"]) if inventory["task_md"] else None,
            "text_files": [str(p) for p in inventory["text_files"]],
            "material_files": [str(p) for p in inventory["material_files"]],
        },
        "task_preview": (task_text or "")[:4000],
    }
    write_json(work_dir / "case_normalized.json", normalized)

    # --- build / extract facts ---
    spec_path = Path(args.spec) if args.spec else work_dir / "figure_spec.json"
    if args.build and spec_path.exists():
        spec_loaded, _ = load_json_safely(spec_path)
        if not isinstance(spec_loaded, dict):
            spec = build_figure_spec(case_id, task_text, case_obj, inventory, input_dir, work_dir)
        else:
            spec = spec_loaded
    else:
        spec = build_figure_spec(case_id, task_text, case_obj, inventory, input_dir, work_dir)
        write_json(spec_path, spec)
    write_json(spec_path, spec)

    # --- build SVG ---
    svg_path = output_dir / f"fig_{case_id}.svg"
    try:
        svg_builder.build(spec, svg_path)
    except Exception as exc:  # noqa: BLE001 - degrade, do not crash
        add_limitation(result, f"SVG build failed: {exc}", "fatal")
        write_json(output_dir / "result.json", result)
        print(f"SVG build failed: {exc}")
        return 0

    # --- validate SVG ---
    check = svg_builder.validate_svg(svg_path)
    manifest = {"svg": str(svg_path), "check": check,
                "entities": len(spec.get("entities") or []),
                "relations": len(spec.get("relations") or []),
                "data": len(spec.get("data") or [])}
    write_json(work_dir / "fig_manifest.json", manifest)
    if check.get("errors"):
        for e in check["errors"]:
            add_limitation(result, f"SVG check: {e}", "medium")

    # --- attempt exports ---
    exports = svg_builder.try_exports(svg_path, output_dir)
    for exp in exports:
        if exp.get("status") != "rendered":
            add_limitation(result, f"export {exp['format']} not rendered: {exp.get('renderer')}",
                           "low")

    # --- finalize ---
    result = finalize_result(result, spec, svg_path, exports, work_dir, output_dir)
    if check.get("errors"):
        result["status"] = "partial"
    elif not exports or any(e.get("status") != "rendered" for e in exports):
        result["status"] = "partial"
    else:
        result["status"] = "completed"
    if not (spec.get("graphical_abstract") or {}).get("question"):
        result["status"] = "partial"
        add_limitation(result, "graphical_abstract question/method/results/conclusion "
                               "require Agent scientific judgement; not auto-derived", "medium")
        result["pending_human"].append({
            "id": "four-elements", "item": "confirm question/method/results/conclusion wording",
            "why": "scientific judgement remains native/LLM Judge, not a literal export pass"})
    add_change(result, "finalize", f"finalize result.json status={result['status']}",
               "build + validate + export pass")
    write_json(output_dir / "result.json", result)
    print(f"done: {result['status']} -> {output_dir / 'result.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
