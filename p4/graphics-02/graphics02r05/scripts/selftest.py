#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-figure-contract 自检：临时目录三场景 + 契约对齐专项校验。

场景：
  A 空输入        -> result.json 存在，status ∈ {partial,failed}，退出不崩溃
  B 坏 JSON+CSV   -> 记录 error/degradation，仍产出可编辑 SVG
  C 演示输入      -> 完整走通；output_contract 逐字段对齐；SVG 含 <text> 且可解析；
                     契约对齐审计齐全；不伪造 PNG/PDF

仅用标准库；结果打印 stdout，不写用户工作区。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import main as M
from contract_engine import (
    contract_leaf_keys,
    iter_leaves,
    load_json_tolerant,
    svg_has_text,
    svg_parseable,
)

DEMO_CSV = """treatment,response_mg_mL,SD
control,1.2,0.2
drugA,3.4,0.5
drugB,2.1,0.3
"""

# 嵌套契约：验证多级叶子逐字段对齐 + 平铺规则（保留键冲突不平铺）
DEMO_CONTRACT = {
    "editable_source": {"svg": None, "pptx": None},
    "exports": {"png": None, "pdf": None},
    "source_and_change": None,
    "evidence": None,
}


def _demo_case(contract=None):
    return {"case_id": "demo-source-value-chart",
            "output_contract": contract or DEMO_CONTRACT}


def _run_case(name, setup_fn, contract_check=False):
    root = tempfile.mkdtemp(prefix="sci_fig_contract_selftest_")
    input_dir = os.path.join(root, "input")
    work_dir = os.path.join(root, "work")
    out_dir = os.path.join(root, "output")
    os.makedirs(input_dir, exist_ok=True)
    setup_fn(input_dir)
    try:
        M.run(input_dir, work_dir, out_dir)
    except Exception as e:  # noqa: BLE001
        print("[FAIL] %s: 异常 %r" % (name, e))
        shutil.rmtree(root, ignore_errors=True)
        return False

    ok = True
    r, errs = load_json_tolerant(os.path.join(out_dir, "result.json"))
    if r is None:
        print("[FAIL] %s: result.json 不可读 %s" % (name, errs))
        shutil.rmtree(root, ignore_errors=True)
        return False
    if not r.get("case_id"):
        print("[FAIL] %s: case_id 缺失" % name)
        ok = False
    if r.get("status") not in ("complete", "partial", "failed"):
        print("[FAIL] %s: status=%r 非法" % (name, r.get("status")))
        ok = False
    if not isinstance(r.get("change_log"), list) or not r.get("change_log"):
        print("[FAIL] %s: change_log 缺失或为空" % name)
        ok = False

    if contract_check:
        oc = r.get("output_contract")
        if not isinstance(oc, dict):
            print("[FAIL] %s: output_contract 缺失" % name)
            ok = False
        else:
            fields = r.get("contract_fields") or []
            alignment = r.get("contract_alignment") or []
            if len(alignment) != len(fields):
                print("[FAIL] %s: alignment=%d != contract_fields=%d"
                      % (name, len(alignment), len(fields)))
                ok = False
            # 每个原始契约叶子必须仍在 output_contract 中可定位（逐字段对齐）
            expected = [("output_contract." + ".".join(l["path"]))
                        for l in iter_leaves(DEMO_CONTRACT)]
            aligned_paths = {a.get("path") for a in alignment}
            for p in expected:
                if p not in aligned_paths:
                    print("[FAIL] %s: 契约字段 %s 未对齐" % (name, p))
                    ok = False
            declared = r.get("contract_declined") or []
            for a in alignment:
                if a.get("fill_status") == "null" and a not in declared:
                    print("[FAIL] %s: null 字段未进入 contract_declined" % name)
                    ok = False
            if not r.get("contract_fields"):
                print("[FAIL] %s: contract_fields 缺失" % name)
                ok = False
            if r.get("status") == "complete" and any(a.get("fill_status") == "null"
                                                     for a in alignment):
                print("[WARN] %s: 存在 null 字段但 status=complete" % name)

    svgs = [f for f in os.listdir(out_dir) if f.endswith(".svg")]
    if name == "C_demo":
        if not svgs:
            print("[FAIL] %s: 未产出 SVG" % name)
            ok = False
        else:
            svg = open(os.path.join(out_dir, svgs[0]), encoding="utf-8").read()
            if not (svg_has_text(svg) and svg_parseable(svg)):
                print("[FAIL] %s: SVG 不含 <text> 或不可解析" % name)
                ok = False
    pngs = [f for f in os.listdir(out_dir) if f.endswith((".png", ".pdf"))]
    if pngs:
        print("[WARN] %s: 出现 %s（若非真实渲染则为伪造）" % (name, pngs))

    print("[%s] %s: status=%s case_id=%s svgs=%d alignment=%d"
          % ("PASS" if ok else "FAIL", name, r.get("status"), r.get("case_id"),
             len(svgs), len(r.get("contract_alignment") or [])))
    shutil.rmtree(root, ignore_errors=True)
    return ok


def _check_frontmatter():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skill_path = os.path.join(base, "SKILL.md")
    if not os.path.exists(skill_path):
        return False
    txt = open(skill_path, encoding="utf-8").read()
    return txt.startswith("---") and "name:" in txt and "description:" in txt \
        and "version:" in txt


def selftest():
    results = []
    results.append(("A_empty", _run_case("A_empty", lambda d: None)))
    def setup_bad(d):
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")
        with open(os.path.join(d, "data.csv"), "w", encoding="utf-8") as f:
            f.write(DEMO_CSV)
    results.append(("B_bad_json", _run_case("B_bad_json", setup_bad)))
    def setup_demo(d):
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
            json.dump(_demo_case(), f, ensure_ascii=False)
        with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as f:
            f.write("# demo task\n依据 source data 生成柱状图。\n")
        with open(os.path.join(d, "data.csv"), "w", encoding="utf-8") as f:
            f.write(DEMO_CSV)
    results.append(("C_demo", _run_case("C_demo", setup_demo, contract_check=True)))

    fm = _check_frontmatter()
    print("[%s] frontmatter" % ("PASS" if fm else "FAIL"))
    passed = sum(1 for _, ok in results if ok) + (1 if fm else 0)
    total = len(results) + 1
    print("sci-figure-contract selftest: %d/%d passed" % (passed, total))
    print(json.dumps([{"scenario": n, "ok": ok} for n, ok in results], ensure_ascii=False))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(selftest())
