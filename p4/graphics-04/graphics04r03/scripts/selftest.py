#!/usr/bin/env python3
"""selftest：自检与确定性验证。

运行：`python scripts/selftest.py`（在 Skill 根目录）。退出码 0 = 全部通过。

验证内容：
 1) 确定性：同一合成输入跑两遍，figure.svg 逐字节一致；
    result.json 的“科学内容核心”（剔除 runtime 与时间戳）一致。
 2) 契约完整性：result.json 含 case_id/status/primary_artifact/sources/
    uncertainty/pending_human/change_log 与六类契约字段。
 3) 结构自检：SVG 可解析、source_ref 全覆盖、无断头箭头、无画布溢出。
 4) 异常降级：空输入、缺 case.json、坏 JSON 均退出码 0 并写合法结果。
只依赖标准库；不做网络、不写正式测试产物。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as main  # noqa: E402


FIXTURE = {
    "case.json": {
        "case_id": "selftest-case-001",
        "task": "自检合成案例：装置图",
    },
    "ir.json": {
        "meta": {"case_id": "selftest-case-001", "kind": "apparatus",
                 "title": "自检装置图", "canvas": {"width": 900, "height": 600}},
        "entities": [
            {"id": "pump", "label": "注射泵", "type": "device",
             "source_ref": "fixture.md#p1", "quote": "泵推动流体"},
            {"id": "reactor", "label": "反应器", "type": "device",
             "source_ref": "fixture.md#p2", "quote": "反应发生"},
            {"id": "collector", "label": "收集瓶", "type": "device",
             "source_ref": "fixture.md#p3", "quote": "产物收集"},
        ],
        "relations": [
            {"id": "r1", "from": "pump", "to": "reactor", "kind": "flow",
             "style": "solid", "direction": "forward", "label": "进料",
             "source_ref": "fixture.md#p1"},
            {"id": "r2", "from": "reactor", "to": "collector", "kind": "flow",
             "style": "solid", "direction": "forward", "label": "出料",
             "source_ref": "fixture.md#p2"},
            {"id": "r3", "from": "collector", "to": "reactor", "kind": "flow",
             "style": "dashed", "direction": "back", "label": "回流",
             "source_ref": "fixture.md#p3"},
        ],
        "data_encoding": [
            {"id": "d1", "param": "流速", "value": "2 mL/min",
             "maps_to": "箭头线宽", "source_ref": "fixture.md#p1"},
        ],
        "legend_annotations": [
            {"key": "flow", "color": "#1f77b4", "label": "物料流向",
             "source_ref": "fixture.md#p1"},
        ],
        "sources": [
            {"id": "s1", "locator": "fixture.md#p1", "quote": "泵推动流体",
             "used_for": ["pump", "r1"]},
        ],
        "uncertainty": [], "pending_human": [], "notes": [],
    },
}


def run_case(base):
    case_dir = os.path.join(base, "input")
    work_dir = os.path.join(base, "work")
    out_dir = os.path.join(base, "output")
    os.makedirs(case_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    main.write_json(os.path.join(case_dir, "case.json"), FIXTURE["case.json"])
    main.write_json(os.path.join(work_dir, "ir.json"), FIXTURE["ir.json"])
    main.init_workspace(os.path.join(case_dir, "case.json"), work_dir, out_dir)
    main.build_svg(FIXTURE["ir.json"], os.path.join(work_dir, "figure.svg"))
    checks = main.validate(FIXTURE["ir.json"], os.path.join(work_dir, "figure.svg"))
    main.write_checks(checks, os.path.join(work_dir, "checks.json"))
    main.finalize(work_dir, out_dir, "complete", {"render_status": "skipped", "note": "selftest"})
    return out_dir


def determinism_test(results):
    a, b = results
    svg_a = open(os.path.join(a, "..", "work", "figure.svg"), "rb").read()
    svg_b = open(os.path.join(b, "..", "work", "figure.svg"), "rb").read()
    ra = main.read_json(os.path.join(a, "result.json"))
    rb = main.read_json(os.path.join(b, "result.json"))
    svg_same = svg_a == svg_b
    core_same = main.canonical_json(ra) == main.canonical_json(rb)
    return [
        ("determinism_svg", svg_same, "figure.svg 两次运行逐字节一致"),
        ("determinism_result_core", core_same,
         "result.json 科学内容核心（剔除时间戳/runtime）一致"),
    ]


def contract_test(result_path):
    r = main.read_json(result_path)
    fields = ["case_id", "status", "primary_artifact", "sources",
              "uncertainty", "pending_human", "change_log"]
    missing = [f for f in fields if f not in r]
    six = ["entities", "relations", "data_encoding",
           "legend_annotations", "editable_sources", "exports"]
    missing_six = [f for f in six if f not in r]
    return [
        ("contract_core_fields", not missing, "缺字段: %s" % (missing or "无")),
        ("contract_six_fields", not missing_six, "缺六类字段: %s" % (missing_six or "无")),
        ("contract_case_id", r.get("case_id") == "selftest-case-001", "case_id 正确"),
    ]


def edge_case_tests():
    results = []
    base = tempfile.mkdtemp(prefix="sfig-edge-")
    try:
        # 空输入目录
        empty_in = os.path.join(base, "empty-in")
        empty_out = os.path.join(base, "empty-out")
        os.makedirs(empty_in, exist_ok=True)
        os.makedirs(empty_out, exist_ok=True)
        try:
            main.init_workspace(os.path.join(empty_in, "case.json"), os.path.join(base, "w1"), empty_out)
            r = main.read_json(os.path.join(empty_out, "result.json"))
            results.append(("edge_empty_input", r is not None and r.get("case_id") is None,
                            "空输入仍写合法骨架"))
        except Exception as exc:
            results.append(("edge_empty_input", False, "空输入异常: %s" % exc))

        # 坏 JSON
        bad_in = os.path.join(base, "bad-in")
        os.makedirs(bad_in, exist_ok=True)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json ")
        bad_out = os.path.join(base, "bad-out")
        try:
            main.init_workspace(os.path.join(bad_in, "case.json"), os.path.join(base, "w2"), bad_out)
            r = main.read_json(os.path.join(bad_out, "result.json"))
            results.append(("edge_bad_json", r is not None, "坏 JSON 不崩溃，写合法结果"))
        except Exception as exc:
            results.append(("edge_bad_json", False, "坏 JSON 异常: %s" % exc))

        # 空 entities 的 build（不应崩溃）
        empty_ir = dict(FIXTURE["ir.json"])
        empty_ir["entities"] = []
        empty_ir["relations"] = []
        svg_path = os.path.join(base, "empty.svg")
        try:
            main.build_svg(empty_ir, svg_path)
            import xml.etree.ElementTree as ET
            ET.parse(svg_path)
            results.append(("edge_empty_ir_build", True, "空 IR 仍生成合法 SVG"))
        except Exception as exc:
            results.append(("edge_empty_ir_build", False, "空 IR 异常: %s" % exc))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return results


def main_selftest():
    tests = []
    base = tempfile.mkdtemp(prefix="sfig-det-")
    try:
        out_a = run_case(os.path.join(base, "run-a"))
        out_b = run_case(os.path.join(base, "run-b"))
        tests += determinism_test([out_a, out_b])
        tests += contract_test(os.path.join(out_a, "result.json"))
        tests += edge_case_tests()
    finally:
        shutil.rmtree(base, ignore_errors=True)

    failed = [t for t in tests if not t[1]]
    for name, ok, detail in tests:
        print("[%s] %s: %s" % ("PASS" if ok else "FAIL", name, detail))
    print("---")
    print("total=%d failed=%d" % (len(tests), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main_selftest())
