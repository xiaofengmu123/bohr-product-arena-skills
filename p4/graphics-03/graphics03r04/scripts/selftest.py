#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the weekly-skill package (standard library only).

Verifies:
  - SKILL.md exists with valid name/description/version frontmatter
  - all scripts compile and respond to --help
  - build_svg produces a parseable, layered, deterministic SVG from a synthetic spec
  - main.py scaffold writes a valid partial skeleton
  - main.py check accepts a valid result.json (exit 0) and rejects a broken one (exit 1)

The synthetic spec/result used here are clearly-labeled self-test fixtures, NOT
prepared answers for the official cases. Exit 0 = all checks pass.
"""
import os
import subprocess
import sys
import tempfile
import json
import re
import shutil
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable

FAILURES = []


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print("[%s] %s%s" % (mark, name, ("  | " + detail if detail and not cond else "")))
    if not cond:
        FAILURES.append(name)


def main():
    print("weekly-skill selftest (root=%s)" % ROOT)

    # 1. SKILL.md frontmatter
    skill_path = os.path.join(ROOT, "SKILL.md")
    check("SKILL.md exists", os.path.exists(skill_path))
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        check("SKILL.md has frontmatter", bool(fm))
        if fm:
            body = fm.group(1)
            has_name = re.search(r"^name:\s*\S+", body, re.M)
            has_desc = re.search(r"^description:\s*\S", body, re.M)
            has_ver = re.search(r"^version:\s*\S", body, re.M)
            check("frontmatter has name/description/version",
                  bool(has_name and has_desc and has_ver))

    # 2. scripts compile and respond
    scripts = ["main.py", "build_svg.py", "selftest.py"]
    for s in scripts:
        p = os.path.join(HERE, s)
        if not os.path.exists(p):
            check("script %s exists" % s, False)
            continue
        r = run([PY, "-m", "py_compile", p])
        check("script %s compiles" % s, r.returncode == 0, r.stderr.strip())

    r = run([PY, os.path.join(HERE, "main.py"), "--help"])
    check("main.py --help exit 0", r.returncode == 0, r.stderr.strip())

    # 3. synthetic end-to-end build + determinism
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        synthetic_spec = {
            "case_id": "selftest-synthetic",
            "title": "selftest synthetic pathway (fixture, not an official case)",
            "entities": [
                {"id": "e1", "label": "Ligand", "type": "protein", "compartment": "extracellular",
                 "evidence_status": "direct",
                 "evidence": [{"locator": "fixture.md#p1", "quote": "ligand binds"}]},
                {"id": "e2", "label": "Receptor", "type": "protein", "compartment": "membrane",
                 "transmembrane": True, "evidence_status": "direct",
                 "evidence": [{"locator": "fixture.md#p2", "quote": "receptor"}]},
                {"id": "e3", "label": "Kinase", "type": "protein", "compartment": "cytosol",
                 "evidence_status": "direct",
                 "evidence": [{"locator": "fixture.md#p3", "quote": "kinase"}]},
                {"id": "e4", "label": "TF", "type": "gene", "compartment": "nucleus",
                 "evidence_status": "missing"},
            ],
            "relations": [
                {"id": "r1", "source": "e1", "target": "e2", "type": "binding", "direction": "forward",
                 "evidence_status": "direct",
                 "evidence": [{"locator": "fixture.md#p1", "quote": "ligand binds"}]},
                {"id": "r2", "source": "e2", "target": "e3", "type": "activation", "direction": "forward",
                 "evidence_status": "inferred",
                 "evidence": [{"locator": "fixture.md#p2", "quote": "receptor activates kinase"}]},
                {"id": "r3", "source": "e3", "target": "e4", "type": "transport", "direction": "forward",
                 "evidence_status": "direct",
                 "evidence": [{"locator": "fixture.md#p3", "quote": "translocates"}]},
            ],
            "legend": [],
            "data_mappings": [],
        }
        spec_path = os.path.join(tmp, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(synthetic_spec, f, ensure_ascii=False)

        out1 = os.path.join(tmp, "out1")
        out2 = os.path.join(tmp, "out2")
        r1 = run([PY, os.path.join(HERE, "main.py"), "build", "--spec", spec_path, "--out", out1])
        r2 = run([PY, os.path.join(HERE, "main.py"), "build", "--spec", spec_path, "--out", out2])
        check("build exit 0", r1.returncode == 0, r1.stderr.strip())
        svg1 = os.path.join(out1, "mechanism.svg")
        svg2 = os.path.join(out2, "mechanism.svg")
        check("build produces mechanism.svg", os.path.exists(svg1))
        if os.path.exists(svg1):
            try:
                tree = ET.parse(svg1)
                root = tree.getroot()
                layer_ids = {g.get("id") for g in root.iter("{http://www.w3.org/2000/svg}g")}
                want = {"layer-compartments", "layer-relations", "layer-entities",
                        "layer-labels", "layer-legend", "layer-data"}
                check("svg has named layers", want.issubset(layer_ids),
                      "missing=%s" % (want - layer_ids))
            except Exception as e:
                check("svg parses", False, str(e))
        if os.path.exists(svg1) and os.path.exists(svg2):
            with open(svg1, "rb") as f:
                b1 = f.read()
            with open(svg2, "rb") as f:
                b2 = f.read()
            check("build is deterministic", b1 == b2)

        # 4. scaffold -> valid partial result
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"case_id": "selftest-synthetic"}, f, ensure_ascii=False)
        r = run([PY, os.path.join(HERE, "main.py"), "scaffold",
                 "--case", case_path, "--out", out1, "--work", tmp])
        check("scaffold exit 0", r.returncode == 0, r.stderr.strip())
        result_path = os.path.join(out1, "result.json")
        check("scaffold writes result.json", os.path.exists(result_path))

        # 5. valid result.json passes check (warning-free)
        clean_entities = [
            {"id": "e1", "label": "Ligand", "type": "protein", "compartment": "extracellular",
             "evidence_status": "direct",
             "evidence": [{"locator": "fixture.md#p1", "quote": "ligand binds"}]},
            {"id": "e2", "label": "Receptor", "type": "protein", "compartment": "membrane",
             "transmembrane": True, "evidence_status": "direct",
             "evidence": [{"locator": "fixture.md#p2", "quote": "receptor"}]},
        ]
        clean_relations = [
            {"id": "r1", "source": "e1", "target": "e2", "type": "binding", "direction": "forward",
             "evidence_status": "direct",
             "evidence": [{"locator": "fixture.md#p1", "quote": "ligand binds"}]},
        ]
        valid_result = {
            "case_id": "selftest-synthetic",
            "status": "partial",
            "editable_source_files": [{"path": "mechanism.svg", "format": "svg",
                                       "layers": ["compartments", "relations", "entities",
                                                  "labels", "legend", "data"]}],
            "export_artifacts": [],
            "entities": clean_entities,
            "relations": clean_relations,
            "legend": [],
            "data_mappings": [],
            "sources": [],
            "uncertainty": [],
            "pending_human": [],
            "limitations": ["selftest fixture: partial result, no export artifacts"],
            "degradations": [],
            "errors": [],
            "change_log": [{"time": "2026-11-22T00:00:00Z", "action": "skeleton",
                            "status": "partial", "detail": "骨架落盘"}],
            "runtime": {"started_at": "x", "finished_at": None, "seconds": 0, "requests_used": 0},
        }
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(valid_result, f, ensure_ascii=False)
        r = run([PY, os.path.join(HERE, "main.py"), "check",
                 "--result", result_path, "--out", out1])
        check("check accepts valid result (exit 0)", r.returncode == 0, r.stdout + r.stderr)

        # 6. warning path: missing-evidence entity displayed + success without exports
        warn_result = dict(valid_result)
        warn_result["status"] = "success"
        warn_result["limitations"] = []
        warn_result["entities"] = warn_result["entities"] + [
            {"id": "e9", "label": "UnknownFactor", "type": "protein", "compartment": "cytosol",
             "evidence_status": "missing", "display": True}]
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(warn_result, f, ensure_ascii=False)
        r = run([PY, os.path.join(HERE, "main.py"), "check",
                 "--result", result_path, "--out", out1])
        check("check flags warnings (exit 2)", r.returncode == 2, "rc=%s" % r.returncode)

        # 7. broken result.json rejected (blocking)
        broken = dict(valid_result)
        broken["case_id"] = ""
        broken["change_log"] = []
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(broken, f, ensure_ascii=False)
        r = run([PY, os.path.join(HERE, "main.py"), "check",
                 "--result", result_path, "--out", out1])
        check("check rejects broken result (exit 1)", r.returncode == 1,
              "rc=%s" % r.returncode)

    finally:
        for d in (out1, out2, tmp):
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

    print("\n%d check(s) failed" % len(FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
