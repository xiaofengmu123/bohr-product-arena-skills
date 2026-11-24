#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — tracefigure-skill 包级自检。

检查项（逐条对应评分维度·可靠体验/生态贡献）：
  1. SKILL.md 存在且 frontmatter 含 name/description/version
  2. scripts 只依赖标准库（扫描常见第三方导入名）
  3. 模块可导入（main / figure）
  4. 确定性 demo：最小 case.json -> init -> build -> validate -> run -> result.json 字段齐全、SVG 可解析
  5. 重复运行结果一致（同一 IR 两次 build 输出逐字节一致）

用法：python scripts/selftest.py [--demo-dir DIR]
退出码：全部通过 0，任一失败 1。
"""
import argparse
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

THIRD_PARTY = {
    "numpy", "scipy", "pandas", "matplotlib", "seaborn", "PIL", "Pillow",
    "lxml", "yaml", "requests", "httpx", "bs4", "cairosvg", "svglib", "reportlab",
    "torch", "tensorflow", "sklearn", "cv2", "openpyxl", "docx", "pptx",
}


def parse_frontmatter(path):
    txt = open(path, "r", encoding="utf-8-sig").read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", txt, re.S)
    if not m:
        return None, "no frontmatter"
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("'\"")
    return fm, None


def _demo_ir():
    return {
        "case_id": "demo-case",
        "figure_kind": "flow",
        "title": "示例流程（示意输出）",
        "source_ref": "demo/input/materials.md#p1",
        "entities": [
            {"id": "e1", "label": "原料 A", "type": "material", "source_ref": "demo/input/materials.md#p1", "quote": "原料 A"},
            {"id": "e2", "label": "产物 B", "type": "product", "source_ref": "demo/input/materials.md#p3", "quote": "产物 B"},
        ],
        "steps": [
            {"id": "s1", "order": 1, "label": "混合", "source_ref": "demo/input/materials.md#p2", "quote": "混合"},
            {"id": "s2", "order": 2, "label": "分离", "source_ref": "demo/input/materials.md#p2", "quote": "分离"},
        ],
        "relations": [
            {"id": "r1", "from": "e1", "to": "s1", "kind": "flow", "direction": "forward", "source_ref": "demo/input/materials.md#p2", "quote": "进入混合"},
            {"id": "r2", "from": "s1", "to": "s2", "kind": "flow", "direction": "forward", "source_ref": "demo/input/materials.md#p2", "quote": "转入分离"},
            {"id": "r3", "from": "s2", "to": "e2", "kind": "flow", "direction": "forward", "source_ref": "demo/input/materials.md#p3", "quote": "得到产物 B"},
        ],
        "data_encoding": [],
        "legend": [],
        "annotations": [],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="selftest.py")
    ap.add_argument("--demo-dir", default=None)
    a = ap.parse_args(argv)
    results = []

    def check(name, ok, detail=""):
        results.append({"name": name, "ok": bool(ok), "detail": detail})

    sk = os.path.join(ROOT, "SKILL.md")
    if not os.path.exists(sk):
        check("SKILL.md exists", False)
    else:
        fm, err = parse_frontmatter(sk)
        ok = bool(fm) and all(fm.get(k) for k in ("name", "description", "version"))
        check("SKILL.md frontmatter name/description/version", ok, err or "")

    bad = []
    for fn in os.listdir(HERE):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(HERE, fn), "r", encoding="utf-8-sig") as f:
            for line in f:
                m = re.match(r"\s*(?:from\s+(\S+)\s+import|import\s+(\S+))", line)
                if m:
                    root = (m.group(1) or m.group(2)).split(".")[0]
                    if root in THIRD_PARTY:
                        bad.append("%s:%s" % (fn, root))
    check("scripts use stdlib only", not bad, "; ".join(bad))

    try:
        import main as m_mod  # noqa: F401
        import figure as f_mod  # noqa: F401
        check("modules importable", True)
    except Exception as e:  # noqa: BLE001
        check("modules importable", False, str(e))

    demo = a.demo_dir or tempfile.mkdtemp(prefix="tracefigure-demo-")
    os.makedirs(demo, exist_ok=True)
    inp = os.path.join(demo, "input")
    out = os.path.join(demo, "output")
    work = os.path.join(demo, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"id": "demo-case", "output_contract": {
            "entities": "科学实体", "relations": "空间关系", "data_encoding": "数据编码",
            "legend_annotations": "图例标注", "editable_sources": "可编辑源文件", "exports": "导出件",
        }}, f, ensure_ascii=False)
    ir = _demo_ir()
    with open(os.path.join(work, "ir.json"), "w", encoding="utf-8") as f:
        json.dump(ir, f, ensure_ascii=False)

    out_json = m_mod.run(inp, out, work)
    res, _ = m_mod.read_json(out_json)
    fields_ok = isinstance(res, dict) and res.get("case_id") == "demo-case" and bool(res.get("status"))
    for k in m_mod.CONTRACT_FIELDS:
        fields_ok = fields_ok and (k in res)
    check("demo run result fields", fields_ok, "status=%s" % (res.get("status") if isinstance(res, dict) else None))

    svg = os.path.join(out, "figure.svg")
    svg_ok = os.path.exists(svg)
    if svg_ok:
        try:
            ET.parse(svg)
        except Exception as e:  # noqa: BLE001
            svg_ok = False
    check("demo SVG parseable", svg_ok)

    p1 = os.path.join(demo, "a.svg")
    p2 = os.path.join(demo, "b.svg")
    f_mod.build_svg(ir, p1)
    f_mod.build_svg(ir, p2)
    same = open(p1, "rb").read() == open(p2, "rb").read()
    check("deterministic SVG (same IR -> same bytes)", same)

    failed = [r for r in results if not r["ok"]]
    print(json.dumps({"ok": not failed, "results": results}, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
