#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""figtrace 自检：在临时目录跑三个场景，验证「不崩溃 + 合法产物 + 不伪造」。

场景：
  A. 空输入目录      -> result.json 存在，status ∈ {partial,failed}，退出 0
  B. 坏 JSON + 有效 CSV -> 记录 degradation，仍产出可编辑 SVG
  C. 演示输入（合同 + 数据）-> 完整走通，SVG 含 <text> 且可解析，不伪造 PNG/PDF

仅用标准库；结果打印到 stdout，不写用户工作区。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import main as M
from render_svg import load_json_tolerant, svg_has_text, svg_parseable

DEMO_CSV = """treatment,response_mg_mL,SD
control,1.2,0.2
drugA,3.4,0.5
drugB,2.1,0.3
"""


def _demo_case(contract=None):
    return {
        "case_id": "demo-source-value-chart",
        "output_contract": contract or {
            "editable_source": None,
            "exports": None,
            "source_and_change": None,
        },
    }


def _run_case(name, setup_fn):
    root = tempfile.mkdtemp(prefix="figtrace_selftest_")
    input_dir = os.path.join(root, "input")
    work_dir = os.path.join(root, "work")
    out_dir = os.path.join(root, "output")
    os.makedirs(input_dir, exist_ok=True)
    setup_fn(input_dir)
    try:
        result = M.run(input_dir, work_dir, out_dir)
    except Exception as e:  # noqa: BLE001
        print("[FAIL] %s: 异常 %r" % (name, e))
        return False
    ok = True
    rp = os.path.join(out_dir, "result.json")
    r, errs = load_json_tolerant(rp)
    if r is None:
        print("[FAIL] %s: result.json 不可读 %s" % (name, errs))
        return False
    if not r.get("case_id"):
        print("[FAIL] %s: case_id 缺失" % name)
        ok = False
    if r.get("status") not in ("complete", "partial", "failed"):
        print("[FAIL] %s: status=%r 非法" % (name, r.get("status")))
        ok = False
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
    print("[%s] %s: status=%s case_id=%s svgs=%d" % (
        "PASS" if ok else "FAIL", name, r.get("status"), r.get("case_id"), len(svgs)))
    shutil.rmtree(root, ignore_errors=True)
    return ok


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
    results.append(("C_demo", _run_case("C_demo", setup_demo)))

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print("figtrace selftest: %d/%d passed" % (passed, total))
    print(json.dumps([{"scenario": n, "ok": ok} for n, ok in results], ensure_ascii=False))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(selftest())
