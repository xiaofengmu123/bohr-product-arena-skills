#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 包级自检（仅标准库）。

在隔离临时目录里跑一遍最小闭环，验证：
  1) 合成 MIR 能 validate + render，产出可解析、带命名图层的 SVG；
  2) 图例与图面自洽（使用的关系类型/实体种类都出现在 legend）；
  3) 渲染确定性（两次渲染逐字节一致）；
  4) 空输入目录 skeleton 不崩溃、status=partial、change_log 非空；
  5) 坏 JSON 输入可降级不崩溃；
  6) check 能识别缺失产物 / 非法 status。

退出码：0=全部通过；非 0=存在失败项。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402
import main as M  # noqa: E402

SYNTHETIC_MIR = {
    "schema_version": "1.0.0",
    "case_id": "selftest-case",
    "title": "合成最小机制图（自检样例，非正式案例）",
    "entities": [
        {"id": "lig", "label": "Ligand", "kind": "small_molecule", "compartment": "extracellular",
         "sources": [{"locator": "demo/materials.md#L1", "quote": "Ligand binds receptor", "material": "materials.md"}]},
        {"id": "rcp", "label": "Receptor", "kind": "receptor", "compartment": "membrane", "transmembrane": True,
         "sources": [{"locator": "demo/materials.md#L2", "quote": "receptor activates kinase", "material": "materials.md"}]},
        {"id": "kin", "label": "Kinase", "kind": "protein", "compartment": "cytosol",
         "sources": [{"locator": "demo/materials.md#L3", "quote": "kinase phosphorylates TF", "material": "materials.md"}]},
        {"id": "tf", "label": "TF", "kind": "gene", "compartment": "nucleus",
         "sources": [{"locator": "demo/materials.md#L4", "quote": "TF induces target gene", "material": "materials.md"}]},
    ],
    "relations": [
        {"id": "r1", "source": "lig", "target": "rcp", "type": "binding",
         "sources": [{"locator": "demo/materials.md#L1", "quote": "Ligand binds receptor", "material": "materials.md"}]},
        {"id": "r2", "source": "rcp", "target": "kin", "type": "activation",
         "sources": [{"locator": "demo/materials.md#L2", "quote": "receptor activates kinase", "material": "materials.md"}]},
        {"id": "r3", "source": "kin", "target": "tf", "type": "modification", "modifier": "phosphorylation",
         "sources": [{"locator": "demo/materials.md#L3", "quote": "kinase phosphorylates TF", "material": "materials.md"}]},
        {"id": "r4", "source": "tf", "target": "kin", "type": "inhibition", "direction": "feedback",
         "sources": [{"locator": "demo/materials.md#L5", "quote": "TF represses kinase", "material": "materials.md"}]},
    ],
    "legend": [],
    "data_mappings": [],
}


def _fail(name, detail):
    print("FAIL  %-26s  %s" % (name, detail))
    return 1


def _pass(name):
    print("PASS  %-26s" % name)
    return 0


def run():
    failures = 0
    tmp = tempfile.mkdtemp(prefix="mech_selftest_")
    try:
        # 1) validate + render
        errors, warnings = C.validate_mir(SYNTHETIC_MIR)
        if errors:
            failures += _fail("validate", "; ".join(errors))
        else:
            failures += _pass("validate")
        svg1 = C.render_svg(SYNTHETIC_MIR)
        if not C.svg_is_valid(svg1):
            failures += _fail("render:svg-parse", "SVG 无法解析")
        else:
            failures += _pass("render:svg-parse")
        layers = C.svg_layer_ids(svg1)
        need = ["layer-background", "layer-relations", "layer-entities", "layer-legend"]
        missing = [n for n in need if n not in layers]
        if missing:
            failures += _fail("render:layers", "缺图层: %s" % missing)
        else:
            failures += _pass("render:layers")

        # 2) 图例自洽：使用的关系类型与实体种类都应出现在图例文本里
        for token in ("激活", "抑制", "结合", "修饰", "基因", "蛋白", "受体", "小分子"):
            if token not in svg1:
                failures += _fail("legend:self-consistent", "图例缺条目: %s" % token)
                break
        else:
            failures += _pass("legend:self-consistent")

        # 3) 确定性
        svg2 = C.render_svg(SYNTHETIC_MIR)
        if svg1 != svg2:
            failures += _fail("render:determinism", "两次渲染不一致")
        else:
            failures += _pass("render:determinism")

        # 4) 空输入目录 skeleton
        empty_in = os.path.join(tmp, "empty_in")
        empty_out = os.path.join(tmp, "empty_out")
        empty_work = os.path.join(tmp, "empty_work")
        os.makedirs(empty_in)
        rc = M.cmd_skeleton(argparse.Namespace(input=empty_in, out=empty_out,
                                               work=empty_work, force=False))
        rpath = os.path.join(empty_out, "result.json")
        res = C.read_json_tolerant(rpath)
        if rc != 0 or res is None:
            failures += _fail("skeleton:empty-input", "rc=%s res=%s" % (rc, res is not None))
        elif res.get("status") != "partial" or not res.get("change_log"):
            failures += _fail("skeleton:empty-input", "status/change_log 不符合")
        else:
            failures += _pass("skeleton:empty-input")

        # 5) 坏 JSON 容错
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json ")
        if C.read_json_tolerant(bad) is not None:
            failures += _fail("read:bad-json", "坏 JSON 未降级为 None")
        else:
            failures += _pass("read:bad-json")

        # 6) check 识别缺失产物 / 非法 status
        broken = C.empty_result("x")
        broken["editable_source_files"] = [{"path": "missing.svg", "format": "svg"}]
        errs, _ = C.check_result(broken, empty_out)
        if not any("不存在" in e for e in errs):
            failures += _fail("check:missing-file", "未识别缺失源文件")
        else:
            failures += _pass("check:missing-file")
        broken2 = C.empty_result("y")
        broken2["status"] = "bogus"
        errs2, _ = C.check_result(broken2, empty_out)
        if not any("status 非法" in e for e in errs2):
            failures += _fail("check:invalid-status", "未识别非法 status")
        else:
            failures += _pass("check:invalid-status")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 60)
    if failures:
        print("SELFTEST FAILED: %d 项失败" % failures)
    else:
        print("SELFTEST OK")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp", default="")
    args = ap.parse_args()
    return run()


if __name__ == "__main__":
    sys.exit(main())
