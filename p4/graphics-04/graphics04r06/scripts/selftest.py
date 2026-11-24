#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— Skill 自检脚本（仅标准库，可独立运行）。

在提交前执行：
    cd /skills/weekly-skill && python scripts/selftest.py

覆盖：
  1. 端到端确定性管线：init -> inventory -> template -> model -> plan -> render -> check -> finalize
  2. 坏 JSON 降级、空输入降级（insufficient_evidence 诚实状态，不崩溃）
  3. SVG 可解析、分层齐全、source_ref 覆盖、无断头箭头、无画布溢出
  4. 重复运行逐字节一致（确定性）
  5. result.json 含 case_id / status / sources / uncertainty / change_log
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C  # noqa: E402


def _fail(msg):
    print("FAIL: %s" % msg)
    sys.exit(1)


def _ok(msg):
    print("ok: %s" % msg)


def build_demo_case(root):
    """构造一个合成案例，用于验证管线与确定性。不是正式答案。"""
    case_dir = os.path.join(root, "case")
    os.makedirs(os.path.join(case_dir, "materials"), exist_ok=True)
    case = {
        "case_id": "demo-0001",
        "output_contract": {
            "entities": [], "relations": [], "data_encoding": [],
            "legend_annotations": [], "editable_sources": [], "exports": [],
        },
        "materials": ["materials/method.md"],
    }
    with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    method = (
        "反应釜 A 通过管路接收原料液，产物从出口进入冷却器 B。\n"
        "第一步：投料；第二步：升温至 37°C；第三步：冷却出料。\n"
    )
    with open(os.path.join(case_dir, "materials", "method.md"), "w", encoding="utf-8") as f:
        f.write(method)

    model = {
        "case_id": "demo-0001",
        "figure_kind": "flow",
        "title": "合成案例流程（示例，非正式结果）",
        "canvas": {"width": 900, "height": 600, "unit": "px"},
        "entities": [
            {"id": "e1", "label": "反应釜 A", "type": "vessel", "source_ref": "q1"},
            {"id": "e2", "label": "冷却器 B", "type": "device", "source_ref": "q2"},
        ],
        "relations": [
            {"id": "r1", "from": "e1", "to": "e2", "kind": "material",
             "direction": "forward", "source_ref": "q3"},
        ],
        "steps": [
            {"id": "s1", "order": 1, "label": "投料", "kind": "main", "source_ref": "q4"},
            {"id": "s2", "order": 2, "label": "升温至 37°C", "kind": "main", "source_ref": "q5"},
            {"id": "s3", "order": 3, "label": "冷却出料", "kind": "terminate", "source_ref": "q6"},
        ],
        "data_encoding": [
            {"param": "温度", "mapping": "color=#d62728", "target_id": "e1", "source_ref": "q5"},
        ],
        "legend": [],
        "evidence": [
            {"id": "q1", "locator": "materials/method.md#L1", "quote": "反应釜 A 通过管路接收原料液"},
            {"id": "q2", "locator": "materials/method.md#L1", "quote": "产物从出口进入冷却器 B"},
            {"id": "q3", "locator": "materials/method.md#L1", "quote": "产物从出口进入冷却器 B"},
            {"id": "q4", "locator": "materials/method.md#L2", "quote": "第一步：投料"},
            {"id": "q5", "locator": "materials/method.md#L2", "quote": "升温至 37°C"},
            {"id": "q6", "locator": "materials/method.md#L2", "quote": "第三步：冷却出料"},
        ],
        "uncertainty": [],
        "pending_human": [],
    }
    with open(os.path.join(case_dir, "model_prepared.json"), "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False)
    return case_dir, model


def run_pipeline(case_dir, out, work):
    """直接调用 core_impl，等价于 main.py 各子命令，便于断言。"""
    case = C.load_case(os.path.join(case_dir, "case.json"))
    cid = C.extract_case_id(case)
    C.init_result(case, cid, os.path.join(out, "result.json"))
    C.write_json(os.path.join(work, "case.json"), case)
    inv = {"materials": C.discover_materials(case_dir)}
    C.write_json(os.path.join(work, "inventory.json"), inv)
    model = C.normalize_model(C.read_json(os.path.join(case_dir, "model_prepared.json")))
    C.write_json(os.path.join(work, "figure_model.json"), model)
    plan = C.compute_layout(model)
    C.write_json(os.path.join(work, "plan.json"), plan)
    svg = C.build_svg(plan, model)
    C.write_text(os.path.join(out, "figure.svg"), svg)
    C.write_text(os.path.join(work, "figure.svg"), svg)
    checks = C.validate_all(model, plan, svg)
    C.write_json(os.path.join(work, "checks.json"), checks)
    C.merge_result(os.path.join(out, "result.json"), model=model, checks=checks,
                   inventory=inv["materials"], note="selftest merge")
    C.finalize_result(os.path.join(out, "result.json"), work, out, "complete")
    return model, plan, svg, checks


def main():
    tmp = tempfile.mkdtemp(prefix="scix-selftest-")
    try:
        # 1) 端到端管线
        case_dir, model = build_demo_case(tmp)
        out1, work1 = os.path.join(tmp, "o1"), os.path.join(tmp, "w1")
        os.makedirs(out1); os.makedirs(work1)
        model, plan, svg, checks = run_pipeline(case_dir, out1, work1)
        if not C.checks_critical_passed(checks):
            _fail("critical 自检未通过: %s" % json.dumps(checks, ensure_ascii=False))
        _ok("端到端管线 critical 全通过")

        # 2) SVG 结构
        root = ET.fromstring(svg)
        ids = {el.get("id") for el in root.iter() if el.get("id")}
        for layer in ("layer-relations", "layer-entities", "layer-steps", "layer-legend"):
            if layer not in ids:
                _fail("缺图层 %s" % layer)
        if 'data-source-ref="q1"' not in svg:
            _fail("实体 source_ref 未写入 SVG")
        if "arrow-" not in svg:
            _fail("缺箭头 marker")
        _ok("SVG 分层/箭头/source_ref 齐全")

        # 3) result.json 契约
        r = C.read_json(os.path.join(out1, "result.json"))
        for k in ("case_id", "status", "sources", "uncertainty", "change_log",
                  "entities", "relations", "data_encoding", "legend_annotations",
                  "editable_sources", "exports", "pending_human"):
            if k not in r:
                _fail("result.json 缺字段 %s" % k)
        if r["case_id"] != "demo-0001" or r["status"] != "complete":
            _fail("result.json case_id/status 错误")
        if not any(s.get("quote") for s in r["sources"]):
            _fail("sources 缺逐字 quote")
        _ok("result.json 契约字段与 quote 齐全")

        # 4) 确定性：重复运行逐字节一致
        out2, work2 = os.path.join(tmp, "o2"), os.path.join(tmp, "w2")
        os.makedirs(out2); os.makedirs(work2)
        _, _, svg2, _ = run_pipeline(case_dir, out2, work2)
        if svg != svg2:
            _fail("两次运行 SVG 不一致（非确定性布局）")
        _ok("重复运行逐字节一致")

        # 5) 坏 JSON 降级
        bad_dir = os.path.join(tmp, "bad")
        os.makedirs(bad_dir)
        with open(os.path.join(bad_dir, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        c = C.load_case(os.path.join(bad_dir, "case.json"))
        if c.get("case_id") != "unknown-case" or "_read_error" not in c:
            _fail("坏 JSON 未降级")
        _ok("坏 JSON 降级为 unknown-case + _read_error")

        # 6) 空输入降级
        empty_dir = os.path.join(tmp, "empty")
        os.makedirs(empty_dir)
        if C.discover_materials(empty_dir) != []:
            _fail("空目录应返回空材料清单")
        _ok("空输入返回空清单（不崩溃）")

        print("SELFTEST PASSED (demo, not a real case result)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
