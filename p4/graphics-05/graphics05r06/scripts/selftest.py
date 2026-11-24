#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""graphical-abstract skill 自检（仅 Python 标准库）。

检查项：
  1. SKILL.md 存在且 YAML frontmatter 含 name/description/version；
  2. demo 输入上跑通 init → 填 plan → build → validate；
  3. result.json 契约：case_id + status + output_contract 字段 + sources/uncertainty/
     pending_human/change_log；
  4. SVG 存在、可解析、含分层 layer-* 分组；
  5. 确定性：不改 plan 重跑 build，SVG 字节一致（同 seed 复现）。

退出码 0 表示通过，1 表示失败。运行：python scripts/selftest.py
"""

import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import core_impl as C  # noqa: E402


def check_skill_md():
    p = os.path.join(ROOT, "SKILL.md")
    if not os.path.isfile(p):
        return False, "SKILL.md missing"
    text, err = C.read_text(p)
    if err:
        return False, "SKILL.md unreadable: %s" % err
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return False, "SKILL.md missing YAML frontmatter"
    fm = m.group(1)
    for key in ("name:", "description:", "version:"):
        if key not in fm:
            return False, "SKILL.md frontmatter missing %s" % key
    return True, "SKILL.md frontmatter ok"


def build_demo(tmpdir):
    indir = os.path.join(tmpdir, "input")
    work = os.path.join(tmpdir, "work")
    out = os.path.join(tmpdir, "output")
    os.makedirs(os.path.join(indir, "materials"), exist_ok=True)
    case = {"case_id": "demo-case-0001",
            "output_contract": {"figure": None, "abstract_text": None}}
    C.write_json(os.path.join(indir, "case.json"), case)
    with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as f:
        f.write("# Demo\ngenerate a graphical abstract from the material.\n")
    with open(os.path.join(indir, "materials", "paper.txt"), "w", encoding="utf-8") as f:
        f.write("Factor A activates factor B. The measured yield was 3.2%.\n")
    return indir, work, out


def run_demo():
    tmp = tempfile.mkdtemp(prefix="ga-selftest-")
    try:
        indir, work, out = build_demo(tmp)
        main_mod = importlib.import_module("main")
        main_mod.run_init(indir, work, out)
        plan_path = os.path.join(work, "plan.json")
        plan, err = C.load_json(plan_path)
        if err or not isinstance(plan, dict):
            return False, "init did not write plan.json: %s" % err
        plan["title"] = "Demo graphical abstract"
        plan["entities"] = [
            {"id": "A", "label": "因子A", "kind": "molecule",
             "panel": "p_question", "sources": ["s1"]},
            {"id": "B", "label": "因子B", "kind": "molecule",
             "panel": "p_method", "sources": ["s1"]},
        ]
        plan["relations"] = [
            {"id": "r1", "from": "A", "to": "B", "kind": "activates",
             "directed": True, "label": "激活", "sources": ["s1"]},
        ]
        plan["data"] = [
            {"id": "d1", "label": "产率", "value": 3.2, "unit": "%",
             "encoding": "bar", "sources": ["s1"]},
        ]
        plan["sources"] = [
            {"id": "s1", "locator": "materials/paper.txt#L1",
             "quote": "Factor A activates factor B. The measured yield was 3.2%.",
             "file": "materials/paper.txt"},
        ]
        C.write_json(plan_path, plan)
        r1 = main_mod.run_build(work, out)
        issues = main_mod.run_validate(work, out)
        res, _ = C.load_json(os.path.join(out, "result.json"), {})
        if not isinstance(res, dict):
            return False, "result.json not written"
        for key in ("case_id", "status", "sources", "uncertainty",
                    "pending_human", "change_log"):
            if key not in res:
                return False, "result.json missing %s" % key
        if res.get("case_id") != "demo-case-0001":
            return False, "wrong case_id: %s" % res.get("case_id")
        for key in ("figure", "abstract_text"):
            if key not in res:
                return False, "result.json missing output_contract field %s" % key
        svg_path = (res.get("editable_source") or {}).get("path")
        if not svg_path or not os.path.isfile(svg_path):
            return False, "svg missing"
        try:
            root = ET.parse(svg_path).getroot()
        except Exception as e:
            return False, "svg parse failed: %s" % e
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            return False, "svg root tag wrong: %s" % root.tag
        svgtext = open(svg_path, encoding="utf-8").read()
        for layer in ("layer-background", "layer-entities", "layer-relations",
                      "layer-data", "layer-title"):
            if ('id="%s"' % layer) not in svgtext:
                return False, "svg missing layer %s" % layer
        h1 = hashlib.sha256(open(svg_path, "rb").read()).hexdigest()
        main_mod.run_build(work, out)
        h2 = hashlib.sha256(open(svg_path, "rb").read()).hexdigest()
        if h1 != h2:
            return False, "non-deterministic rebuild (svg hash differs)"
        return True, "demo pipeline ok (%d issues)" % len(issues)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    results = []
    ok = True
    passed, msg = check_skill_md()
    results.append(("SKILL.md", passed, msg))
    ok = ok and passed
    passed, msg = run_demo()
    results.append(("demo-pipeline", passed, msg))
    ok = ok and passed
    for name, p, m in results:
        print(("[PASS] " if p else "[FAIL] ") + name + ": " + m)
    print("selftest %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
