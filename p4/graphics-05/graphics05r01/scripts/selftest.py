#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package self-check for the graphical-abstract Skill.

Runs without the real /input case: verifies SKILL.md frontmatter, script
importability, empty-input degradation, a minimal synthetic pipeline run, SVG
well-formedness and result.json schema. Optionally validates an existing result
with `--check-result /app/output/result.json`.

Exit code 0 = all checks passed; 1 = at least one failed. No network, no
third-party packages; the synthetic input is clearly marked and is not a
formal case answer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import build_svg  # noqa: E402
import main as core  # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print("[PASS] %s" % name)
    else:
        FAIL.append(name)
        print("[FAIL] %s%s" % (name, (" — " + detail) if detail else ""))


def check_frontmatter():
    path = os.path.join(PKG, "SKILL.md")
    ok = os.path.isfile(path)
    detail = ""
    if ok:
        text = core.read_text(path)
        head = text.split("---", 2)
        fm = head[1] if len(head) >= 3 and head[0].strip() == "" else ""
        has_name = "name:" in fm
        has_desc = "description:" in fm
        has_ver = "version:" in fm
        ok = has_name and has_desc and has_ver
        detail = "name=%s desc=%s version=%s" % (has_name, has_desc, has_ver)
    check("SKILL.md exists with name/description/version frontmatter", ok, detail)


def check_scripts_importable():
    check("scripts importable (main + build_svg)", core is not None and build_svg is not None)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _validate_result_json(output_dir):
    rp = os.path.join(output_dir, "result.json")
    if not os.path.isfile(rp):
        return False, "result.json missing"
    result = core.load_json(rp)
    if not isinstance(result, dict):
        return False, "result.json not an object"
    for k in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if k not in result:
            return False, "result.json missing key %s" % k
    return True, ""


def _validate_svg(output_dir):
    svg = None
    for name in os.listdir(output_dir):
        if name.endswith(".svg"):
            svg = os.path.join(output_dir, name)
            break
    if not svg:
        return False, "no SVG produced"
    try:
        root = ET.parse(svg).getroot()
    except ET.ParseError as exc:
        return False, "SVG unparseable: %s" % exc
    tags = root.tag
    text_nodes = root.findall(".//{http://www.w3.org/2000/svg}text")
    layers = [g.get("id") for g in root.iter() if str(g.get("id", "")).startswith("layer-")]
    ok = "svg" in tags.lower() and len(text_nodes) > 0 and len(layers) >= 3
    return ok, "texts=%d layers=%s" % (len(text_nodes), layers)


def run_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        i, o, w = (os.path.join(tmp, "in"), os.path.join(tmp, "out"), os.path.join(tmp, "work"))
        os.makedirs(i)
        try:
            summary = core.run_pipeline(i, o, w)
        except Exception as exc:  # pragma: no cover
            check("empty input degrades gracefully", False, traceback.format_exc())
            return
        ok_json, detail = _validate_result_json(o)
        check("empty input writes valid result.json", ok_json, detail)
        check("empty input status is failed/partial", summary.get("status") in ("failed", "partial"), summary.get("status"))


def run_synthetic_case():
    with tempfile.TemporaryDirectory() as tmp:
        i, o, w = (os.path.join(tmp, "in"), os.path.join(tmp, "out"), os.path.join(tmp, "work"))
        os.makedirs(i)
        # Clearly-marked synthetic selftest input; NOT a formal case answer.
        case = {
            "id": "selftest-synthetic",
            "case_id": "selftest-synthetic",
            "task": "synthetic pipeline check",
            "output_contract": {"custom_note": None},
            "materials": [{"name": "selftest-note.md", "type": "text"}],
        }
        _write(os.path.join(i, "case.json"), json.dumps(case, ensure_ascii=False))
        _write(os.path.join(i, "task.md"), "# synthetic selftest\n\nsample only.\n")

        # Pre-populate a minimal but evidence-bearing spec to exercise the builder.
        spec = {
            "case_id": "selftest-synthetic",
            "reading_order": ["problem", "method", "results", "conclusion"],
            "panels": [{"id": p, "title": p} for p in ("problem", "method", "results", "conclusion")],
            "entities": [
                {"id": "e1", "label": "A", "panel": "problem", "source_ref": "s1", "locator": "selftest-note.md#L1", "quote": "sample only."},
                {"id": "e2", "label": "B", "panel": "results", "source_ref": "s1", "locator": "selftest-note.md#L1", "quote": "sample only."},
            ],
            "relations": [
                {"id": "r1", "from": "e1", "to": "e2", "kind": "arrow", "label": "causes", "source_ref": "s1", "locator": "selftest-note.md#L1", "quote": "sample only."}
            ],
            "data": [],
            "legend": [],
            "graphical_abstract": {
                "question": {"text": "q", "evidence_ref": "s1"},
                "method": {"text": "m", "evidence_ref": "s1"},
                "results": {"text": "r", "evidence_ref": "s1"},
                "conclusion": {"text": "c", "evidence_ref": "s1"},
            },
            "sources": [{"source_id": "s1", "path": "selftest-note.md", "type": "text", "title": "synthetic"}],
        }
        os.makedirs(w, exist_ok=True)
        core.save_json(os.path.join(w, "figure_spec.json"), spec)
        core.save_json(os.path.join(w, "evidence_map.json"), {
            "claims": [{"claim_id": "c1", "text": "A causes B", "kind": "conclusion",
                        "support_level": "direct", "locator": "selftest-note.md#L1",
                        "quote": "sample only.", "source_ref": "s1", "maps_to": ["r1"],
                        "confidence": "high", "note": "synthetic"}],
            "conflicts": [], "unresolved": [],
        })

        try:
            summary = core.run_pipeline(i, o, w)
        except Exception as exc:  # pragma: no cover
            check("synthetic case pipeline runs", False, traceback.format_exc())
            return
        ok_json, detail = _validate_result_json(o)
        check("synthetic case writes valid result.json", ok_json, detail)
        ok_svg, detail = _validate_svg(o)
        check("synthetic case produces layered editable SVG", ok_svg, detail)
        check("synthetic case status completed", summary.get("status") == "completed", summary.get("status"))


def check_existing_result(path):
    output_dir = os.path.dirname(os.path.abspath(path))
    ok_json, detail = _validate_result_json(output_dir)
    check("existing result.json schema", ok_json, detail)
    result = core.load_json(path) or {}
    issues, status = core.validate_result(result, output_dir)
    check("existing result validation clean", status != "failed" and not issues, "; ".join(issues[:5]) or status)
    # Evidence provenance: every claim needs locator + quote.
    claims = (result.get("evidence_map") or {}).get("claims") or []
    if claims:
        ok = all(c.get("locator") and c.get("quote") for c in claims)
        check("every claim has locator + quote", ok, "checked %d claims" % len(claims))
    else:
        check("every claim has locator + quote", False, "no claims present")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Graphical-abstract Skill self-check")
    ap.add_argument("--check-result", default=None, help="also validate an existing result.json")
    args = ap.parse_args(argv)

    print("Package root: %s" % PKG)
    check_frontmatter()
    check_scripts_importable()
    run_empty_input()
    run_synthetic_case()
    if args.check_result:
        check_existing_result(args.check_result)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
