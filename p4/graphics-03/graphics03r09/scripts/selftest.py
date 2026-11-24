#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 包自检（仅标准库，确定性）。

在临时目录中运行一遍 validate → build → check → skeleton，验证：
  1. 规格校验能识别阻断错误与警告；
  2. SVG 可被 XML 解析且含命名图层；
  3. result.json 契约校验（正例与反例）；
  4. 骨架落盘字段齐全且 status=partial；
  5. main.py CLI 可被调用（--help / skeleton）。

fixture 为合成示例（明确标注，非正式案例）。全部通过退出码 0，否则 1。
"""
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402

FIXTURE = {
    "case_id": "selftest-fixture",
    "title": "示例：受体激活胞内级联（合成示例，非正式案例）",
    "layout": {"orientation": "top_to_bottom",
               "compartments": ["extracellular", "membrane", "cytosol", "nucleus"]},
    "entities": [
        {"id": "e1", "label": "Ligand", "kind": "small_molecule",
         "compartment": "extracellular",
         "sources": [{"locator": "fixture.md#L1", "quote": "配体结合受体"}]},
        {"id": "e2", "label": "EGFR", "kind": "protein", "compartment": "membrane",
         "transmembrane": True, "aliases": ["ERBB1"],
         "sources": [{"locator": "fixture.md#L2", "quote": "EGFR 跨膜受体"}]},
        {"id": "e3", "label": "RAS", "kind": "protein", "compartment": "cytosol",
         "sources": [{"locator": "fixture.md#L3", "quote": "RAS 被激活"}]},
        {"id": "e4", "label": "TF", "kind": "gene", "compartment": "nucleus",
         "name_style": "italic",
         "sources": [{"locator": "fixture.md#L4", "quote": "转录因子入核"}]},
    ],
    "relations": [
        {"id": "r1", "from": "e1", "to": "e2", "type": "binding",
         "evidence": "stated",
         "sources": [{"locator": "fixture.md#L1", "quote": "配体结合受体"}]},
        {"id": "r2", "from": "e2", "to": "e3", "type": "activation",
         "sources": [{"locator": "fixture.md#L3", "quote": "RAS 被激活"}]},
        {"id": "r3", "from": "e3", "to": "e4", "type": "transport",
         "sources": [{"locator": "fixture.md#L4", "quote": "转录因子入核"}]},
    ],
    "legend": {"mappings": [
        {"visual": "实线箭头", "semantic": "activation"},
        {"visual": "T 形端点", "semantic": "inhibition"},
    ]},
    "data_mappings": [],
    "uncertainty": [],
}


def log(pass_, msg):
    print("[%s] %s" % ("PASS" if pass_ else "FAIL", msg))
    return pass_


def main():
    ok = True
    tmp = tempfile.mkdtemp(prefix="pathway_selftest_")
    spec_p = os.path.join(tmp, "spec.json")
    svg_p = os.path.join(tmp, "figure.svg")
    report_p = os.path.join(tmp, "report.json")
    result_p = os.path.join(tmp, "result.json")
    with open(spec_p, "w", encoding="utf-8") as fh:
        json.dump(FIXTURE, fh, ensure_ascii=False)

    # 1) validate
    errors, warnings, norm = core.validate_spec(FIXTURE)
    ok &= log(len(errors) == 0, "validate 无阻断错误（errors=%d）" % len(errors))
    ok &= log(norm["case_id"] == "selftest-fixture", "validate 保留 case_id")
    ok &= log(len(norm["entities"]) == 4, "validate 实体数=4")

    # 2) build
    try:
        core.build_svg(FIXTURE, svg_p)
        tree = ET.parse(svg_p)
        root = tree.getroot()
        ids = {g.get("id") for g in root.iter() if g.tag.endswith("g") and g.get("id")}
        need = {"layer-background", "layer-compartments", "layer-relations",
                "layer-entities", "layer-legend"}
        ok &= log(need.issubset(ids), "SVG 含全部命名图层 %s" % sorted(need))
        ok &= log(os.path.getsize(svg_p) > 500, "SVG 非空（%d bytes）" % os.path.getsize(svg_p))
    except Exception as exc:  # noqa: BLE001
        ok &= log(False, "build 异常: %r" % exc)

    # 3) skeleton
    res = core.write_skeleton(result_p, "selftest-fixture", "skeleton check")
    ok &= log(res["status"] == "partial", "skeleton status=partial")
    ok &= log(res.get("case_id") == "selftest-fixture", "skeleton case_id 正确")
    ok &= log(len(res.get("change_log", [])) >= 1, "skeleton change_log 非空")
    ok &= log(len(res.get("limitations", [])) >= 1, "skeleton limitations 非空")

    # 4) check 正例：引用真实存在的 SVG
    good = dict(res)
    good["editable_source_files"] = [{"path": "figure.svg", "format": "svg"}]
    good["export_artifacts"] = [{"path": "figure.svg", "format": "svg"}]
    errors, warnings = core.check_result(good, tmp)
    ok &= log(len(errors) == 0, "check 正例无错误（errors=%d）" % len(errors))

    # 5) check 反例：引用不存在的文件 + 非法 status
    bad = dict(res)
    bad["status"] = "weird"
    bad["editable_source_files"] = [{"path": "missing.svg", "format": "svg"}]
    errors, warnings = core.check_result(bad, tmp)
    ok &= log(any("status" in e for e in errors), "check 反例识别非法 status")
    ok &= log(any("missing.svg" in e for e in errors), "check 反例识别缺失文件")

    # 6) CLI 冒烟：main.py --help 与 skeleton 子命令
    for argv in (["--help"], ["skeleton", result_p, "--case-id", "cli-fixture"]):
        rc = subprocess.run([sys.executable, os.path.join(HERE, "main.py")] + argv,
                            capture_output=True, text=True).returncode
        ok &= log(rc == 0, "main.py %s 退出码 0" % (" ".join(argv)))

    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
