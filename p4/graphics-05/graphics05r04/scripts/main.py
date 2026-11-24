#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-bound graphical abstract pipeline. Standard library only.

Full run:
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work

The first thing run() does is persist a legal partial result.json skeleton, so an
interrupted process never leaves an empty output. Everything else is incremental.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

CANONICAL_STATUSES = ("completed", "partial", "failed")
MAX_FILE_BYTES = 2_000_000
MAX_QUOTE = 320

ENTITY_HINTS = (
    "protein", "gene", "kinase", "receptor", "ligand", "enzyme", "molecule",
    "compound", "nanoparticle", "material", "cell", "tissue", "organ", "sample",
    "device", "electrode", "substrate", "antibody", "cytokine", "ion", "pathway",
)
RELATION_WORDS = (
    "activates", "inhibits", "promotes", "suppresses", "mediates", "converts",
    "converted", "binds", "regulates", "upregulates", "downregulates", "leads to",
    "induces", "blocks", "catalyzes", "interacts", "transports", "促进", "抑制",
    "上调", "下调", "介导", "转化", "结合", "调控", "诱导", "阻断", "催化", "作用于",
)
RELATION_ARROWS = ("→", "⇒", "->", "=>", "—>")
FORMAT_HINTS = (
    "svg", "png", "pdf", "dpi", "分辨率", "尺寸", "字号", "字体", "图例", "比例尺",
    "单位", "灰度", "色盲", "颜色", "colorblind", "grayscale", "figure", "图表",
    "流程图", "示意图", "机制图", "graphical abstract",
)
NUMBER_RE = re.compile(
    r"(?<!\w)(?:[+-]?\d+(?:\.\d+)?|\.\d+)\s*(?:%|‰|μm|µm|nm|mm|cm|m|kg|g|mg|μg|L|mL|μL|"
    r"M|mM|μM|nM|pM|s|min|h|°C|℃|kDa|Da|bp|Hz|V|A|W|MΩ|Ω)?(?!\w)"
)
ABBR_RE = re.compile(r"\(([A-Z][A-Za-z0-9\-]{1,12})\)")

_CJK_SENT_END = "。！？；"
_EN_SENT_END = ".!?;"


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha8(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]


# --------------------------------------------------------------------------- io
def read_text(path: str, max_bytes: int = MAX_FILE_BYTES):
    """Tolerant UTF-8 reader: BOM and bad bytes degrade instead of crashing."""
    if not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > max_bytes:
        return None  # caller records a limitation
    with open(path, "rb") as fh:
        raw = fh.read(max_bytes)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read_json(path: str, default=None):
    text = read_text(path)
    if text is None:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def write_json(path: str, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------- discovery
def discover_inputs(input_dir: str):
    """List readable input files recursively; tolerate an absent or empty dir."""
    found = {"case": None, "task": None, "materials": []}
    if not os.path.isdir(input_dir):
        return found
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            low = name.lower()
            if low == "case.json" and found["case"] is None:
                found["case"] = full
            elif low == "task.md" and found["task"] is None:
                found["task"] = full
            elif not name.startswith("."):
                found["materials"].append(full)
    return found


# -------------------------------------------------------------------- skeleton
def make_skeleton(case_id, contract, output_dir, status="partial", reason=None):
    """Write a legal result.json before any analysis. Never raises."""
    os.makedirs(output_dir, exist_ok=True)
    base = {"case_id": case_id, "status": status}
    if isinstance(contract, dict):
        for key, val in contract.items():
            base[key] = val
    base.setdefault("graphical_abstract",
                    {"question": None, "method": None, "results": None, "conclusion": None})
    base.setdefault("editable_source", {"path": None, "format": "svg",
                                        "layers": [], "text_editable": True})
    base.setdefault("exports", [])
    base.setdefault("entities", [])
    base.setdefault("relations", [])
    base.setdefault("data_mapping", [])
    base.setdefault("legend", [])
    base.setdefault("sources", [])
    base.setdefault("uncertainty", [])
    base.setdefault("pending_human", [])
    base.setdefault("limitations", [])
    base.setdefault("change_log", [])
    base.setdefault("runtime",
                    {"started_at": _now(), "finished_at": None, "seconds": None,
                     "requests_used": None, "renderer": None})
    if reason:
        base.setdefault("limitations", []).append({"kind": "skeleton", "detail": reason})
    base["change_log"] = [{"step": "skeleton", "note": reason or "partial skeleton persisted",
                           "at": _now()}]
    path = os.path.join(output_dir, "result.json")
    try:
        write_json(path, base)
    except OSError:
        pass
    return base


# ------------------------------------------------------------------- sentences
def _is_boundary(ch: str, chars, i: int):
    """Sentence boundary that keeps decimals/abbreviations intact."""
    if ch in _CJK_SENT_END:
        return True
    if ch not in _EN_SENT_END:
        return False
    if ch != ".":
        return True  # '!' '?' ';'
    prev = chars[i - 1] if i > 0 else ""
    nxt = chars[i + 1] if i + 1 < len(chars) else ""
    if prev.isdigit() and nxt.isdigit():
        return False  # decimal point (18.5, p<0.05)
    if nxt == "" or nxt.isspace() or nxt.isupper():
        return True
    return False


def _sentences(text: str):
    out = []
    for part in re.split(r"\n{1,}", text or ""):
        part = part.strip()
        if not part:
            continue
        buf = []
        chars = list(part)
        for i, ch in enumerate(chars):
            if _is_boundary(ch, chars, i):
                sent = "".join(buf).strip(" \t。！？；.!?;")
                if sent:
                    out.append(sent)
                buf = []
            else:
                buf.append(ch)
        sent = "".join(buf).strip(" \t。！？；.!?;")
        if sent:
            out.append(sent)
    return out


def _clip(text: str):
    if len(text) <= MAX_QUOTE:
        return text
    return text[: MAX_QUOTE - 1] + "…"


# ------------------------------------------------------------------- extraction
def extract_evidence(case_id, input_dir, work_dir):
    """Deterministic candidate extraction: entities/relations/data/constraints.

    Every candidate carries locator + verbatim quote and confidence=low. These are
    starting points for the agent, not the final scientific judgement.
    """
    discovered = discover_inputs(input_dir)
    entities, relations, data, constraints, sources, limitations = [], [], [], [], [], []

    def add_text_file(path, text):
        rel = os.path.relpath(path, input_dir).replace("\\", "/")
        sources.append({"path": rel, "kind": "text",
                        "note": "material text; locators are #L<line>"})
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            locator = f"{rel}#L{line_no}"
            for sent in _sentences(line):
                _scan_sentence(sent, locator, rel)

    def _scan_sentence(sent, locator, rel):
        quote = _clip(sent)
        low = sent.lower()
        # entities: abbreviation pattern or entity hint words
        for m in ABBR_RE.finditer(sent):
            label = m.group(1)
            entities.append({
                "id": f"ent-{_sha8(locator + label)}",
                "label": label, "kind": None,
                "evidence": {"locator": locator, "quote": quote},
                "confidence": "low", "source": rel,
            })
        for hint in ENTITY_HINTS:
            if hint in low:
                entities.append({
                    "id": f"ent-{_sha8(locator + hint)}",
                    "label": hint, "kind": None,
                    "evidence": {"locator": locator, "quote": quote},
                    "confidence": "low", "source": rel,
                })
                break  # one candidate per sentence
        # relations: arrows or relation words
        has_arrow = any(a in sent for a in RELATION_ARROWS)
        rel_word = next((w for w in RELATION_WORDS if w in low), None)
        if has_arrow or rel_word:
            relations.append({
                "id": f"rel-{_sha8(locator + (rel_word or 'arrow'))}",
                "from": None, "to": None,
                "label": rel_word or "arrow", "directed": True,
                "evidence": {"locator": locator, "quote": quote},
                "confidence": "low", "source": rel,
            })
        # data: number + optional unit
        for m in NUMBER_RE.finditer(sent):
            data.append({
                "id": f"dat-{_sha8(locator + m.group(0))}",
                "label": m.group(0).strip(), "value": m.group(0).strip(),
                "unit": None, "encoding": "length",
                "evidence": {"locator": locator, "quote": quote},
                "confidence": "low", "source": rel,
            })
        # constraints: format keywords
        for hint in FORMAT_HINTS:
            if hint in low:
                constraints.append({
                    "id": f"con-{_sha8(locator + hint)}",
                    "text": hint,
                    "evidence": {"locator": locator, "quote": quote},
                })
                break

    # task.md first (always present in the harness)
    task_path = discovered["task"]
    if task_path:
        text = read_text(task_path)
        if text is None:
            limitations.append({"kind": "input", "detail": f"task.md too large or unreadable: {task_path}"})
        else:
            rel = os.path.relpath(task_path, input_dir).replace("\\", "/")
            sources.append({"path": rel, "kind": "task", "note": "task text"})
            for line_no, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                for sent in _sentences(line):
                    _scan_sentence(sent, f"{rel}#L{line_no}", rel)

    # material files: text types direct, PDF via pdftotext (honest fallback)
    for mat in discovered["materials"]:
        low = mat.lower()
        if low.endswith((".pdf",)):
            rel = os.path.relpath(mat, input_dir).replace("\\", "/")
            txt_path = os.path.join(work_dir, "parsed", os.path.basename(mat) + ".txt")
            ok = _pdf_to_text(mat, txt_path)
            if ok:
                text = read_text(txt_path)
                if text:
                    sources.append({"path": rel, "kind": "pdf",
                                    "note": "parsed via pdftotext into parsed/*.txt"})
                    for line_no, line in enumerate(text.splitlines(), 1):
                        if not line.strip():
                            continue
                        for sent in _sentences(line):
                            _scan_sentence(sent, f"{rel}#L{line_no}", rel)
                else:
                    limitations.append({"kind": "pdf", "detail": f"pdftotext produced no text: {rel}"})
            else:
                limitations.append({"kind": "pdf",
                                    "detail": f"PDF not parsed by script; use platform pdf-text-tool: {rel}"})
        elif low.endswith((".md", ".txt", ".csv", ".json", ".tsv")):
            text = read_text(mat)
            if text is None:
                limitations.append({"kind": "input", "detail": f"file skipped (>2MB): {mat}"})
            else:
                add_text_file(mat, text)
        else:
            rel = os.path.relpath(mat, input_dir).replace("\\", "/")
            limitations.append({"kind": "input", "detail": f"unsupported material type skipped: {rel}"})

    evidence = {
        "case_id": case_id,
        "entities": entities,
        "relations": relations,
        "data": data,
        "constraints": constraints,
        "sources": sources,
        "limitations": limitations,
        "generated_at": _now(),
    }
    write_json(os.path.join(work_dir, "evidence_bank.json"), evidence)
    return evidence


def _pdf_to_text(pdf_path, txt_path):
    """Try pdftotext; return True on success. No fabrication on failure."""
    try:
        os.makedirs(os.path.dirname(txt_path), exist_ok=True)
        cp = subprocess.run(["pdftotext", "-layout", pdf_path, txt_path],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=120)
        return cp.returncode == 0 and os.path.isfile(txt_path)
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------- figure spec
def build_figure_spec(case_id, evidence, work_dir):
    """Write a figure_spec.json template the agent curates. Reuses agent edits."""
    path = os.path.join(work_dir, "figure_spec.json")
    existing = read_json(path)
    if isinstance(existing, dict) and existing.get("case_id") == case_id:
        # Preserve agent curation; fill only what is missing.
        existing.setdefault("width", 1400)
        existing.setdefault("height", 1000)
        write_json(path, existing)
        return existing
    spec = {
        "case_id": case_id,
        "title": None,
        "width": 1400,
        "height": 1000,
        "graphical_abstract": {"question": None, "method": None,
                               "results": None, "conclusion": None},
        "entities": evidence.get("entities", []),
        "relations": evidence.get("relations", []),
        "data": evidence.get("data", []),
        "legend": [],
        "constraints": evidence.get("constraints", []),
    }
    write_json(path, spec)
    return spec


def load_spec(work_dir):
    spec = read_json(os.path.join(work_dir, "figure_spec.json"), default=None)
    return spec if isinstance(spec, dict) else None


# ------------------------------------------------------------------- validation
def validate(result, spec, evidence, output_dir):
    report = {"errors": [], "warnings": [], "pass": True}
    if not isinstance(result, dict):
        report["errors"].append("result.json is not an object")
        report["pass"] = False
        return report
    if not result.get("case_id"):
        report["errors"].append("missing case_id")
    if result.get("status") not in CANONICAL_STATUSES:
        report["errors"].append(f"status not in {CANONICAL_STATUSES}")
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            report["errors"].append(f"missing {key}")
    # evidence traceability: drawn entities/relations/data need locator+quote
    for group, name in ((spec.get("entities", []), "entities"),
                        (spec.get("relations", []), "relations"),
                        (spec.get("data", []), "data_mapping")):
        for item in group:
            ev = item.get("evidence") if isinstance(item, dict) else None
            if not (isinstance(ev, dict) and ev.get("locator") and ev.get("quote")):
                report["warnings"].append(
                    f"{name}/{item.get('id', '?')} lacks locator+quote; will not be drawn")
    svg_path = os.path.join(output_dir, f"fig_{result.get('case_id', 'x')}.svg")
    if os.path.isfile(svg_path):
        try:
            import xml.etree.ElementTree as ET
            ET.parse(svg_path)
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"SVG unparsable: {exc}")
            report["pass"] = False
    else:
        report["warnings"].append("SVG missing")
    report["pass"] = report["pass"] and not report["errors"]
    return report


# ------------------------------------------------------------------- finalize
def finalize(case_id, contract, spec, evidence, exports, report, output_dir,
             started, status=None):
    result = make_skeleton(case_id, contract, output_dir, status="partial")
    result["graphical_abstract"] = spec.get("graphical_abstract", result["graphical_abstract"])
    result["entities"] = spec.get("entities", [])
    result["relations"] = spec.get("relations", [])
    result["data_mapping"] = spec.get("data", [])
    result["legend"] = spec.get("legend", [])
    result["editable_source"] = {
        "path": f"fig_{case_id}.svg",
        "format": "svg",
        "layers": ["layer-background", "layer-panels", "layer-entities",
                   "layer-relations", "layer-data", "layer-labels", "layer-legend"],
        "text_editable": True,
    }
    result["exports"] = exports
    result["sources"] = evidence.get("sources", [])
    result["limitations"] = evidence.get("limitations", [])
    if report.get("errors"):
        result["status"] = "partial"
    elif status in CANONICAL_STATUSES:
        result["status"] = status
    elif report.get("warnings"):
        result["status"] = "partial"
    else:
        result["status"] = "partial"  # agent promotes to completed after review
    result["runtime"]["finished_at"] = _now()
    result["runtime"]["seconds"] = round(time_elapsed(started), 2)
    result["runtime"]["renderer"] = exports[-1].get("renderer") if exports else None
    errs = report.get("errors", [])
    warns = report.get("warnings", [])
    result["change_log"].append({
        "step": "finalize", "note": f"validation errors={len(errs)} "
                                    f"warnings={len(warns)}", "at": _now()})
    write_json(os.path.join(output_dir, "result.json"), result)
    return result


def time_elapsed(started):
    return (float(_dt.datetime.now(_dt.timezone.utc).timestamp())
            - float(started.timestamp()))


# ------------------------------------------------------------------- run
def run(input_dir=DEFAULT_INPUT, output_dir=DEFAULT_OUTPUT, work_dir=DEFAULT_WORK,
        validate_only=False):
    started = _dt.datetime.now(_dt.timezone.utc)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    discovered = discover_inputs(input_dir)

    case_path = discovered["case"]
    case = read_json(case_path, default=None) if case_path else None
    if not isinstance(case, dict):
        case_id = _sha8(input_dir or "no-input")
        contract = None
        result = make_skeleton(case_id, contract, output_dir, status="failed",
                               reason="no case.json under /input")
        result["change_log"].append({"step": "finalize", "note": "blocked: no case.json",
                                     "at": _now()})
        write_json(os.path.join(output_dir, "result.json"), result)
        return result

    case_id = str(case.get("id") or case.get("case_id") or _sha8(case_path))
    contract = case.get("output_contract")

    # 1. Skeleton first (the 3-minute guarantee).
    make_skeleton(case_id, contract, output_dir, status="partial",
                  reason="skeleton persisted before analysis")

    # 2. Evidence extraction.
    evidence = extract_evidence(case_id, input_dir, work_dir)

    # 3. Figure spec (reuse agent curation on re-runs).
    spec = build_figure_spec(case_id, evidence, work_dir)

    # 4. Build SVG + exports.
    try:
        import build_svg  # noqa: PLC0415  (same directory)
        build_result = build_svg.build(spec, output_dir, work_dir)
    except Exception as exc:  # noqa: BLE001
        build_result = {"svg": None, "exports": [],
                        "errors": [f"build_svg failed: {exc}"]}

    exports = build_result.get("exports", [])
    for e in build_result.get("errors", []):
        evidence.setdefault("limitations", []).append({"kind": "build", "detail": e})

    # 5. Finalize result from curated spec, then validate the written artifact.
    result = finalize(case_id, contract, spec, evidence, exports, {}, output_dir,
                      started, status="partial")
    report = validate(result, spec, evidence, output_dir)
    result["runtime"]["validation_errors"] = report["errors"]
    result["runtime"]["validation_warnings"] = report["warnings"]
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "validation_report.json"), report)
    return result


# ------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Evidence-bound graphical abstract pipeline")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--validate", action="store_true",
                    help="re-run validation/finalize from existing work files")
    args = ap.parse_args(argv)
    result = run(args.input, args.output, args.work, validate_only=args.validate)
    print(json.dumps({"case_id": result.get("case_id"), "status": result.get("status")},
                     ensure_ascii=False))
    return 1 if result.get("status") == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
