#!/usr/bin/env python3
"""selftest.py — weekly-skill 包级自检（提交前运行，仅标准库）。

覆盖：
  1) SKILL.md 存在且 frontmatter 含 name/description/version；
  2) scripts/main.py、build_svg.py 可导入；
  3) build_svg.build 能从最小 demo spec 生成可被 XML 解析的 SVG；
  4) main.skeleton 能从临时 case.json 落盘合法 result.json（含 case_id 与 change_log）；
  5) validate-spec 能抓出坏 spec（缺 locator/quote、端点悬空）；
  6) check-result 能抓出缺失产物文件。

退出码 0=全部通过，1=失败。所有临时文件写入系统临时目录并清理。
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


DEMO_SPEC = {
    "case_id": "selftest-case",
    "title": "selftest 机制图",
    "compartments": [
        {"id": "extracellular", "name": "细胞外"},
        {"id": "membrane", "name": "细胞膜"},
        {"id": "cytosol", "name": "细胞质"},
        {"id": "nucleus", "name": "细胞核"},
    ],
    "entities": [
        {"id": "ligand", "label": "Ligand", "type": "small_molecule",
         "compartment": "extracellular",
         "evidence": [{"locator": "materials/demo.md#p1", "quote": "配体结合受体", "claim": "配体存在"}]},
        {"id": "rtk", "label": "RTK", "type": "protein", "compartment": "membrane",
         "transmembrane": True,
         "evidence": [{"locator": "materials/demo.md#p2", "quote": "RTK 跨膜", "claim": "跨膜受体"}]},
        {"id": "kinase", "label": "Kinase", "type": "protein", "compartment": "cytosol",
         "evidence": [{"locator": "materials/demo.md#p3", "quote": "激活激酶", "claim": "下游激酶"}]},
        {"id": "tf", "label": "TF", "type": "gene", "compartment": "nucleus",
         "evidence": [{"locator": "materials/demo.md#p4", "quote": "转录因子入核", "claim": "核内转录"}]},
    ],
    "relations": [
        {"id": "r1", "source": "ligand", "target": "rtk", "type": "binding",
         "evidence_strength": "stated",
         "evidence": [{"locator": "materials/demo.md#p1", "quote": "配体结合受体", "claim": "配体-受体结合"}]},
        {"id": "r2", "source": "rtk", "target": "kinase", "type": "activation",
         "evidence_strength": "inferred",
         "evidence": [{"locator": "materials/demo.md#p2", "quote": "受体激活下游", "claim": "激活激酶"}]},
        {"id": "r3", "source": "kinase", "target": "tf", "type": "transport",
         "evidence_strength": "stated",
         "evidence": [{"locator": "materials/demo.md#p3", "quote": "TF 入核", "claim": "核转运"}]},
    ],
    "data_mappings": [
        {"visual": "线宽", "encodes": "证据强度", "scale": "stated>inferred>uncertain",
         "evidence": [{"locator": "materials/demo.md#p1", "quote": "证据强度", "claim": "编码约定"}]},
    ],
}


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    return bool(cond)


def main():
    ok = True
    print("== weekly-skill 自检 ==")

    # 1) SKILL.md frontmatter
    skill_md = os.path.join(ROOT, "SKILL.md")
    ok &= check(os.path.exists(skill_md), "SKILL.md 存在")
    fm_ok = False
    if os.path.exists(skill_md):
        txt = open(skill_md, "r", encoding="utf-8-sig").read()
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", txt, re.S)
        if m:
            fm = m.group(1)
            fm_ok = ("name:" in fm and "description:" in fm and "version:" in fm)
        ok &= check(fm_ok, "frontmatter 含 name/description/version")
        ok &= check(len(txt.splitlines()) <= 220, "SKILL.md 行数合理 (<=220)")

    # 2) 模块可导入
    main_mod = load_module("main_selftest", os.path.join(HERE, "main.py"))
    svg_mod = load_module("build_svg_selftest", os.path.join(HERE, "build_svg.py"))
    ok &= check(main_mod is not None and svg_mod is not None, "main.py / build_svg.py 可导入")

    tmp = tempfile.mkdtemp(prefix="weekly-skill-selftest-")
    try:
        # 3) SVG 可解析
        svg, warnings = svg_mod.build(DEMO_SPEC)
        svg_path = os.path.join(tmp, "demo.svg")
        open(svg_path, "w", encoding="utf-8").write(svg)
        try:
            ET.parse(svg_path)
            svg_ok = True
        except ET.ParseError:
            svg_ok = False
        ok &= check(svg_ok, "build_svg 产出可解析 XML")
        ok &= check('inkscape:groupmode="layer"' in svg, "SVG 含命名图层")
        ok &= check("data-evidence-locators" in svg, "SVG 含 data-* 证据属性")
        ok &= check("图例" in svg, "SVG 含自洽图例")

        # 4) skeleton 落盘
        in_dir = os.path.join(tmp, "input")
        out_dir = os.path.join(tmp, "output")
        work_dir = os.path.join(tmp, "work")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        os.makedirs(work_dir)
        case_path = os.path.join(in_dir, "case.json")
        json.dump({"case_id": "selftest-case-1"}, open(case_path, "w", encoding="utf-8"))
        rc = main_mod.cmd_skeleton(_Ns(case=case_path, output=out_dir, work=work_dir, input=in_dir))
        res_path = os.path.join(out_dir, "result.json")
        res = json.load(open(res_path, "r", encoding="utf-8"))
        ok &= check(rc == 0 and res.get("case_id") == "selftest-case-1",
                    "skeleton 写入合法 result.json 并填 case_id")
        ok &= check(res.get("status") == "partial" and res.get("change_log"),
                    "skeleton 含 status=partial 与 change_log")

        # 5) validate-spec 抓坏 spec
        bad = json.loads(json.dumps(DEMO_SPEC))
        bad["relations"][0]["target"] = "不存在"
        bad_path = os.path.join(tmp, "bad.json")
        json.dump(bad, open(bad_path, "w", encoding="utf-8"))
        errs, warns = main_mod._validate_spec(bad)
        ok &= check(any("无对应 entity" in e for e in errs), "validate-spec 抓端点悬空")
        bad2 = json.loads(json.dumps(DEMO_SPEC))
        bad2["entities"][0]["evidence"] = [{"locator": "x.md#p1"}]  # 缺 quote
        json.dump(bad2, open(bad_path, "w", encoding="utf-8"))
        errs2, _ = main_mod._validate_spec(bad2)
        ok &= check(any("缺 locator/quote" in e for e in errs2), "validate-spec 抓缺 quote")

        # 6) check-result 抓缺失产物
        res["status"] = "success"
        res["editable_source_files"] = [{"path": os.path.join(out_dir, "ghost.svg")}]
        json.dump(res, open(res_path, "w", encoding="utf-8"))
        rc = main_mod.cmd_check_result(_Ns(result=res_path, output_dir=out_dir,
                                           report=os.path.join(tmp, "cr.json")))
        ok &= check(rc == 1, "check-result 对 success+缺失文件 返回 1")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("== 结果: " + ("全部通过" if ok else "存在失败项") + " ==")
    return 0 if ok else 1


class _Ns:
    def __init__(self, **kw):
        self.__dict__.update(kw)


if __name__ == "__main__":
    sys.exit(main())
