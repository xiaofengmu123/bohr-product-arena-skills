# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检：demo 全流程 + 空输入 + 坏 JSON + 确定性。

只依赖标准库；退出码 0 表示全部通过，非 0 表示存在失败。
用法：python scripts/selftest.py
"""

import hashlib
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as C  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _check(ok, message, failures):
    if not ok:
        failures.append(message)
    print(("[PASS] " if ok else "[FAIL] ") + message)
    return bool(ok)


def _make_case(tmp, include_material=True):
    input_dir = os.path.join(tmp, "input")
    materials = os.path.join(input_dir, "materials")
    os.makedirs(materials, exist_ok=True)
    case = {
        "case_id": "self-1",
        "output_contract": {
            "experimental_plan": None,
            "evidence_and_risks": None,
            "confirmation_items": None,
        },
        "candidates": [
            {"plan_id": "P1", "name": "A", "dimension_ratings": {"goal_fit": 3, "risk": 3, "cost": 3, "feasibility": 3, "evidence": 3},
             "feasibility": {"equipment_available": "yes"}},
            {"plan_id": "P2", "name": "B", "dimension_ratings": {"goal_fit": 4, "risk": 4, "cost": 4, "feasibility": 4, "evidence": 4},
             "feasibility": {"equipment_available": "yes"}},
        ],
    }
    _write(os.path.join(input_dir, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(input_dir, "task.md"), "# self task\n")
    if include_material:
        _write(os.path.join(materials, "src.md"), "# src\n\np1: 证据片段（自检）\n")
    return input_dir


def _assemble_minimal(work_dir, input_dir, out_dir):
    C.run_inspect(input_dir, work_dir, out_dir)
    C.run_init_models(work_dir)
    plans = {
        "schema": "plans_model", "schema_version": C.SCHEMA_VERSION, "case_id": "self-1",
        "candidates": [
            {"plan_id": "P1", "name": "A", "version": "v1",
             "seven_fields": {f: "填:%s" % f for f in C.SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 3, "risk": 3, "cost": 3, "feasibility": 3, "evidence": 3},
             "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes", "notes": ""},
             "cost": {"method": "computed", "currency": "CNY", "total": None,
                      "lines": [{"item": "机时", "kind": "machine_time", "unit_cost": 100, "quantity": 10}]},
             "time": {"unit": "h", "override_total": None, "parts": {"prep": 1, "run": 2}},
             "risks": [], "evidence_refs": ["E1"], "infeasible_reasons": []},
            {"plan_id": "P2", "name": "B", "version": "v1",
             "seven_fields": {f: "填:%s" % f for f in C.SEVEN_FIELDS},
             "dimension_ratings": {"goal_fit": 4, "risk": 4, "cost": 4, "feasibility": 4, "evidence": 4},
             "feasibility": {"equipment_available": "yes", "lead_time_ok": "yes", "notes": ""},
             "cost": {"method": "computed", "currency": "CNY", "total": None,
                      "lines": [{"item": "耗材", "kind": "consumable", "amount": 500}]},
             "time": {"unit": "h", "override_total": None, "parts": {"prep": 1, "run": 3}},
             "risks": [], "evidence_refs": ["E1"], "infeasible_reasons": []},
        ],
        "evidence_registry": [{"id": "E1", "claim": "自检证据", "locator": "materials/src.md#p1",
                               "quote": "证据片段（自检）", "kind": "manual"}],
    }
    comparison = {
        "schema": "comparison_model", "schema_version": C.SCHEMA_VERSION, "case_id": "self-1",
        "comparison_frame": {"objectives": ["self"], "hard_constraints": [], "criteria": list(C.CRITERIA)},
        "weights": dict(C.DEFAULT_WEIGHTS),
        "thresholds": [{"path": "cost_total", "op": "gt", "value": 100000, "action": "eliminate", "reason": "超预算"}],
        "selection": {"chosen": "P2", "ranking": ["P2", "P1"], "rationale": "自检",
                      "rejected": [], "sensitivity": {}},
        "confirmations": [], "uncertainty": [], "change_log": [],
    }
    _write(os.path.join(work_dir, "plans_model.json"), json.dumps(plans, ensure_ascii=False))
    _write(os.path.join(work_dir, "comparison_model.json"), json.dumps(comparison, ensure_ascii=False))
    C.run_assemble(input_dir, work_dir, out_dir)
    return out_dir


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")

    # 1. demo 全流程
    demo = C.run_demo(os.path.join(tmp, "demo"))
    res = C.run_validate(os.path.join(demo["output_dir"], "result.json"))
    _check(res.get("valid") is True, "demo result.json 校验通过", failures)
    _check(res.get("status") in ("ok", "partial"), "demo status 合法", failures)
    for name in ("result.json", "comparison_matrix.csv", "comparison_matrix.svg",
                 "comparison_matrix.pdf", "plan_comparison.md",
                 "plans_model.json", "comparison_model.json"):
        _check(os.path.exists(os.path.join(demo["output_dir"], name)),
               "demo 产物存在: %s" % name, failures)
    try:
        ET.parse(os.path.join(demo["output_dir"], "comparison_matrix.svg"))
        _check(True, "demo SVG 可解析", failures)
    except Exception as exc:
        _check(False, "demo SVG 可解析: %s" % exc, failures)
    with open(os.path.join(demo["output_dir"], "comparison_matrix.pdf"), "rb") as fh:
        head = fh.read(5)
        fh.seek(-6, 2)
        tail = fh.read(6)
    _check(head == b"%PDF-" and b"%%EOF" in tail, "demo PDF 头尾合法", failures)

    # 2. 确定性：同一输入跑两次 result.json 内容一致
    d1 = _assemble_minimal(os.path.join(tmp, "w1"), _make_case(os.path.join(tmp, "c1")),
                           os.path.join(tmp, "o1"))
    d2 = _assemble_minimal(os.path.join(tmp, "w2"), _make_case(os.path.join(tmp, "c1")),
                           os.path.join(tmp, "o2"))
    h1 = _sha256_file(os.path.join(d1, "result.json"))
    h2 = _sha256_file(os.path.join(d2, "result.json"))
    _check(h1 == h2, "确定性：两次运行 result.json 哈希一致", failures)

    # 3. 空输入
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty, exist_ok=True)
    C.run_inspect(empty, os.path.join(tmp, "we"), os.path.join(tmp, "oe"))
    with open(os.path.join(tmp, "oe", "result.json"), encoding="utf-8") as fh:
        skeleton = json.load(fh)
    _check(skeleton.get("status") == "input_error", "空输入 → input_error", failures)

    # 4. 坏 JSON
    bad = os.path.join(tmp, "bad", "input")
    os.makedirs(bad, exist_ok=True)
    _write(os.path.join(bad, "case.json"), "{ not valid json !!!")
    _write(os.path.join(bad, "task.md"), "# bad\n")
    C.run_inspect(bad, os.path.join(tmp, "wb"), os.path.join(tmp, "ob"))
    with open(os.path.join(tmp, "ob", "result.json"), encoding="utf-8") as fh:
        bad_res = json.load(fh)
    _check(bad_res.get("status") in ("partial", "insufficient_evidence", "input_error"),
           "坏 JSON → 合法降级结果，不崩溃", failures)
    _check(any(d.get("code") == "case_json_unreadable" for d in bad_res.get("degradations") or []),
           "坏 JSON 记录 degradations", failures)

    # 5. 证据溯源与契约字段
    with open(os.path.join(d1, "result.json"), encoding="utf-8") as fh:
        final = json.load(fh)
    _check(final.get("case_id") == "self-1", "case_id 保留", failures)
    _check(all(k in final for k in ("experimental_plan", "evidence_and_risks", "confirmation_items")),
           "output_contract 全部字段回填", failures)
    _check(final.get("sources") and final["sources"][0].get("quote"), "sources 带逐字 quote", failures)
    _check(final.get("selection", {}).get("sensitivity", {}).get("base_ranking"),
           "selection 含敏感性", failures)

    print("")
    if failures:
        print("SELFTEST FAILED: %d issue(s)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("SELFTEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
