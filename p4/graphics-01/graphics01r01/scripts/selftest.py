#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检：在不含作者环境的前提下验证核心链路。

覆盖：
1. figure_spec.json -> SVG 渲染（图层 / 可编辑文本 / 溯源属性）。
2. SVG -> PNG/PDF 导出（渲染器缺失时须如实记录 failed，不崩溃）。
3. result.json 契约校验（字段、evidence_map 的 locator+quote、产物可打开性）。
4. 空输入目录 -> 合法 partial 骨架，退出码 0。
5. 坏 JSON -> 降级不崩溃。

只依赖标准库。运行：python -B scripts/selftest.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402
import main as M  # noqa: E402


DEMO_SPEC = {
    "title": "selftest: A activates B, B inhibits C",
    "canvas": {"width": 900, "height": 480},
    "font_family": "DejaVu Sans, Arial, sans-serif",
    "entities": [
        {"id": "nA", "label": "Kinase A", "kind": "node", "shape": "roundrect",
         "x": 80, "y": 200, "w": 150, "h": 52, "fill": "#e8f0fb",
         "evidence_id": "ev_1", "source_ref": "demo.pdf"},
        {"id": "nB", "label": "Substrate B", "kind": "node", "shape": "roundrect",
         "x": 350, "y": 200, "w": 170, "h": 52, "fill": "#eef7ee",
         "evidence_id": "ev_2", "source_ref": "demo.pdf"},
        {"id": "nC", "label": "Target C", "kind": "node", "shape": "roundrect",
         "x": 650, "y": 200, "w": 150, "h": 52, "fill": "#fdeeee",
         "evidence_id": "ev_3", "source_ref": "demo.pdf"},
    ],
    "relations": [
        {"id": "r1", "from": "nA", "to": "nB", "type": "activates",
         "label": "phosphorylates", "evidence_id": "ev_2", "source_ref": "demo.pdf"},
        {"id": "r2", "from": "nB", "to": "nC", "type": "inhibits",
         "label": "", "evidence_id": "ev_3", "source_ref": "demo.pdf"},
    ],
    "legend": {"title": "Legend", "x": 620, "y": 300, "w": 240,
               "evidence_id": "ev_4", "source_ref": "demo.pdf",
               "items": [
                   {"label": "Activates", "color": "#333333", "marker": "rect"},
                   {"label": "Inhibits", "color": "#333333", "marker": "circle"},
               ]},
    "charts": [],
}

DEMO_EVIDENCE = [
    {"id": "ev_1", "claim_kind": "entity", "claim": "Kinase A 存在",
     "source_ref": "demo.pdf", "locator": {"page": 1, "anchor": "para 1"},
     "quote": "Kinase A phosphorylates B.", "status": "confirmed",
     "basis": "text", "maps_to": ["nA"], "notes": ""},
    {"id": "ev_2", "claim_kind": "relation", "claim": "A 激活 B",
     "source_ref": "demo.pdf", "locator": {"page": 1, "anchor": "para 1"},
     "quote": "Kinase A phosphorylates B.", "status": "confirmed",
     "basis": "text", "maps_to": ["r1"], "notes": ""},
]


def _check(name, cond, detail=""):
    return {"name": name, "ok": bool(cond), "detail": detail}


def run():
    checks = []
    tmp = tempfile.mkdtemp(prefix="selftest-sci-fig-")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(out); os.makedirs(work)

    # 1) 渲染
    svg = os.path.join(out, "figure.svg")
    C.render_svg(DEMO_SPEC, svg)
    inv = C.svg_inventory(svg)
    checks.append(_check("svg-render", os.path.exists(svg)))
    checks.append(_check("svg-editable-text", inv["texts"] >= 3, str(inv)))
    checks.append(_check("svg-layers", inv["groups"] >= 1, str(inv)))
    checks.append(_check("svg-traced", inv["traced_elements"] >= 5, str(inv)))

    # 2) 导出（渲染器可能缺失，只检查“如实记录”）
    records = C.export_svg(svg, out, work)
    ok_png = any(r.get("format") == "png" and not r.get("failed") for r in records)
    failed_any = any(r.get("failed") for r in records)
    checks.append(_check("export-records", len(records) == 2,
                         "%d records" % len(records)))
    checks.append(_check("export-honest", ok_png or failed_any,
                         "png_ok=%s failed=%s" % (ok_png, failed_any)))
    for r in records:
        if not r.get("failed") and r.get("path"):
            assert os.path.exists(os.path.join(out, r["path"])), r

    # 3) 契约校验
    res = C.make_skeleton({"case_id": "selftest-case"}, C.now_iso())
    C.save_json(os.path.join(out, "result.json"), res)
    C.save_json(os.path.join(work, "evidence.json"), DEMO_EVIDENCE)
    spec_path = os.path.join(work, "figure_spec.json")
    C.save_json(spec_path, DEMO_SPEC)
    C.merge_result(os.path.join(out, "result.json"),
                   os.path.join(work, "evidence.json"), spec_path, work)
    # 回填产物路径再校验
    res = C.load_json(os.path.join(out, "result.json"))
    res["editable_source_files"] = [{"path": "figure.svg", "kind": "svg",
                                     "layers": True, "editable_text": True,
                                     "note": "selftest"}]
    res["export_files"] = records
    C.save_json(os.path.join(out, "result.json"), res)
    issues, warnings = C.validate_result(os.path.join(out, "result.json"), out)
    checks.append(_check("validate-contract", not issues, str(issues)[:300]))
    checks.append(_check("evidence-quote", all(e.get("quote") for e in
                                               C.load_json(os.path.join(out, "result.json"))["evidence_map"])))

    # 4) 空输入目录
    empty = os.path.join(tmp, "empty-input")
    os.makedirs(empty)
    rc = M.main(["scaffold", "--input", empty, "--out",
                 os.path.join(tmp, "empty-out"), "--work", os.path.join(tmp, "empty-work")])
    empty_res = C.load_json(os.path.join(tmp, "empty-out", "result.json"))
    checks.append(_check("empty-input-rc0", rc == 0))
    checks.append(_check("empty-input-partial", empty_res and empty_res.get("status") == "partial"))

    # 5) 坏 JSON 降级
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    checks.append(_check("bad-json-degrade", C.load_json(bad, "FALLBACK") == "FALLBACK"))

    # 汇总
    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"ok": not failed, "total": len(checks),
                      "passed": len(checks) - len(failed),
                      "checks": checks}, ensure_ascii=False, indent=2))
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
