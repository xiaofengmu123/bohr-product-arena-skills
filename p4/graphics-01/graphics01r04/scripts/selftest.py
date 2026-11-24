#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-figure-editor self-test.

Verifies package structure, frontmatter, script executability, the full
render->export->validate pipeline on a *synthetic* input generated at test
time, and honest degradation on an empty input directory. No prepared answers
or real-case outputs are bundled or produced outside the temp directory.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def check_frontmatter():
    issues = []
    skill_path = os.path.join(ROOT, "SKILL.md")
    if not os.path.exists(skill_path):
        return ["SKILL.md missing at %s" % skill_path]
    with open(skill_path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    if not text.startswith("---"):
        issues.append("SKILL.md missing YAML frontmatter")
        return issues
    end = text.find("\n---", 3)
    if end < 0:
        issues.append("SKILL.md frontmatter not closed")
        return issues
    fm = text[3:end].strip()
    for key in ("name:", "description:", "version:"):
        if key not in fm:
            issues.append("frontmatter missing %s" % key.rstrip(":"))
    return issues


def run_main(args, timeout=120):
    p = subprocess.run(
        [sys.executable, os.path.join(HERE, "main.py")] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    return p


def synthetic_case(tmp, case_id="syn-01"):
    case = {"case_id": case_id, "id": case_id, "task": "render a two-node pathway"}
    with open(os.path.join(tmp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(tmp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# synthetic task\n")
    return case


def synthetic_spec():
    return {
        "figure": {
            "title": "Synthetic two-node pathway",
            "canvas": {"width_px": 1200, "height_px": 700, "dpi": 150, "font_size_px": 16},
        },
        "entities": [
            {"id": "e1", "label": "Receptor A", "kind": "node", "shape": "roundrect",
             "source_ref": "syn.txt#p1", "quote": "Receptor A is activated", "confidence": "confirmed"},
            {"id": "e2", "label": "Kinase B", "kind": "node", "shape": "rect",
             "source_ref": "syn.txt#p1", "quote": "activates Kinase B", "confidence": "confirmed"},
        ],
        "relations": [
            {"id": "r1", "from": "e1", "to": "e2", "type": "activation",
             "source_ref": "syn.txt#p1", "quote": "Receptor A ... activates Kinase B", "confidence": "confirmed"},
        ],
        "legends": [
            {"id": "l1", "label": "activation", "color": "#1f77b4", "source_ref": "syn.txt#p1", "quote": "activation", "confidence": "confirmed"},
        ],
        "sources": [{"ref": "syn.txt", "path": "syn.txt", "locator": "p1", "used_for": "entities and relation"}],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None, help="validate an existing result.json")
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--work", default=None)
    a = ap.parse_args()

    issues = check_frontmatter()

    # Existing-result validation mode (used at runtime).
    if a.result and os.path.exists(a.result):
        sys.path.insert(0, HERE)
        import core_impl as core
        with open(a.result, "r", encoding="utf-8-sig") as f:
            result = json.load(f)
        out_dir = a.output_dir or os.path.dirname(a.result)
        for i in core.validate_result(result, out_dir):
            issues.append(i)
        print(json.dumps({"ok": len(issues) == 0, "issues": issues}, ensure_ascii=False))
        return 0 if not issues else 1

    # Full pipeline self-test on synthetic input.
    tmp = tempfile.mkdtemp(prefix="scifig-selftest-")
    indir = os.path.join(tmp, "input")
    outdir = os.path.join(tmp, "output")
    workdir = os.path.join(tmp, "work")
    os.makedirs(indir)
    synthetic_case(indir)
    with open(os.path.join(indir, "syn.txt"), "w", encoding="utf-8") as f:
        f.write("Receptor A is activated and activates Kinase B.\n")

    # 1) skeleton run (no spec)
    p = run_main(["--input", indir, "--out", outdir, "--work", workdir])
    if p.returncode != 0:
        issues.append("skeleton run exit %d: %s" % (p.returncode, p.stderr))
    rpath = os.path.join(outdir, "result.json")
    if not os.path.exists(rpath):
        issues.append("skeleton did not write result.json")
    else:
        with open(rpath, "r", encoding="utf-8-sig") as f:
            r = json.load(f)
        if r.get("status") != "partial":
            issues.append("skeleton status should be partial, got %r" % r.get("status"))

    # 2) spec run (render + export + validate)
    with open(os.path.join(workdir, "figure_spec.json"), "w", encoding="utf-8") as f:
        json.dump(synthetic_spec(), f, ensure_ascii=False)
    p = run_main(["--input", indir, "--out", outdir, "--work", workdir,
                  "--spec", os.path.join(workdir, "figure_spec.json")])
    if p.returncode != 0:
        issues.append("spec run exit %d: %s" % (p.returncode, p.stderr))
    with open(rpath, "r", encoding="utf-8-sig") as f:
        r = json.load(f)
    if r.get("status") not in ("complete", "partial"):
        issues.append("unexpected status %r" % r.get("status"))
    if not r.get("editable_source_files"):
        issues.append("no editable_source_files")
    svg = os.path.join(outdir, "figure01.svg")
    if not os.path.exists(svg):
        issues.append("figure01.svg missing")
    else:
        try:
            ET.parse(svg)
        except Exception as exc:
            issues.append("SVG parse failed: %s" % exc)
    pdf = os.path.join(outdir, "figure01.pdf")
    if not os.path.exists(pdf):
        issues.append("figure01.pdf missing (stdlib fallback should always produce it)")
    else:
        with open(pdf, "rb") as f:
            data = f.read()
        if not data[:5].startswith(b"%PDF-"):
            issues.append("PDF header invalid")
        if b"%%EOF" not in data:
            issues.append("PDF missing %%EOF trailer")
    png = os.path.join(outdir, "figure01.png")
    if not os.path.exists(png):
        issues.append("figure01.png missing")
    else:
        with open(png, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                issues.append("PNG header invalid")
    # provenance must carry verbatim quotes
    for i, pr in enumerate(r.get("provenance", [])):
        if not pr.get("source_ref") or not pr.get("quote"):
            issues.append("provenance[%d] lacks source_ref/quote" % i)

    # 3) empty-input degradation: exit 0, valid result.json, status failed/partial
    empty = os.path.join(tmp, "empty")
    emptyout = os.path.join(tmp, "emptyout")
    os.makedirs(empty)
    p = run_main(["--input", empty, "--out", emptyout, "--work", os.path.join(tmp, "emptywork")])
    if p.returncode != 0:
        issues.append("empty-input run exit %d" % p.returncode)
    erpath = os.path.join(emptyout, "result.json")
    if not os.path.exists(erpath):
        issues.append("empty-input did not write result.json")
    else:
        with open(erpath, "r", encoding="utf-8-sig") as f:
            er = json.load(f)
        if er.get("status") not in ("failed", "partial"):
            issues.append("empty-input status should be failed/partial, got %r" % er.get("status"))

    print(json.dumps({"ok": len(issues) == 0, "issues": issues, "tmp": tmp}, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
