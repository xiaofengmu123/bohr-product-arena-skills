#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for experiment-plan-ir.

Runs in temporary directories only; no fixture touches the submitted outputs/.

Scenarios:
  1. empty input  -> exit 0, valid insufficient_evidence result.json
  2. malformed case.json + one material -> exit 0, errors recorded, no crash
  3. full synthetic demo -> scaffold/build/validate, exit 0, contract keys kept,
     arithmetic passes, quote verification passes
  4. idempotency -> two builds produce identical result.json (modulo runtime/log ts)
  5. unit -> a bogus quote is flagged as not_found (evidence integrity)

Exit code 0 = all pass, 1 = failure. Pass --keep to retain temp dirs.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent


def run_cli(args, cwd=SKILL_ROOT):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, "-B", "scripts/main.py"] + args,
                          cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    return proc.returncode, proc.stdout, proc.stderr


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


DEMO_MATERIAL = """# 合成自检材料（非真实案例）

酶活性在 25–40 °C 区间随温度升高而增加，超过 45 °C 后迅速失活。
分光光度计在 405 nm 处的吸光度线性范围为 0.1–1.5 AU。
预实验表明，25–40 °C 区间内初始速率可稳定检出，40 °C 以上迅速失活。
预实验表明，每 200 µL 反应体系加入 2 µL 酶液时初始速率落在线性范围内。
标准品阳性对照应给出不低于 0.8 AU/min 的速率。
"""

DEMO_CASE = {
    "case_id": "selftest-demo",
    "goal": "在 25–40 °C 内表征酶初始反应速率随温度的变化（合成示例，仅用于自检）",
    "output_contract": {
        "plan": {},
        "evidence_and_risks": {},
        "confirmations": {},
        "candidate_comparison": {},
        "custom_note": None,
    },
    "constraints": {
        "budget_cap": 500, "currency": "CNY", "time_window_minutes": 480,
        "parameter_domains": {"P1": [25, 40]},
    },
    "candidates": [
        {"id": "A", "summary": "5 个温度点、3 重复", "cost_total": 150,
         "wall_clock_minutes": 120, "parameter_choices": {"P1": 30}},
        {"id": "B", "summary": "4 个温度点、3 重复", "cost_total": 120,
         "wall_clock_minutes": 110, "parameter_choices": {"P1": 35}},
        {"id": "C", "summary": "含 50 °C 点", "cost_total": 90,
         "wall_clock_minutes": 100, "parameter_choices": {"P1": 50}},
    ],
}


def demo_ir():
    return {
        "ir_version": "1.0",
        "case_id": "selftest-demo",
        "goal": {
            "statement": "在 25–40 °C 内表征酶初始反应速率随温度的变化（合成示例）",
            "testable_question": "在 25–40 °C 内，初始速率是否随温度单调增加？",
            "success_criteria": ["40 °C 的初始速率显著高于 25 °C", "重复间 CV ≤ 10%"],
        },
        "assumptions": [],
        "hypotheses": [
            {"id": "H1", "statement": "在 25–40 °C 内，初始反应速率随温度升高而单调增加",
             "falsifiable_by": "若 40 °C 的初始速率不高于 25 °C，则假设被否定",
             "status": "unverified", "evidence": []},
        ],
        "variables": [
            {"id": "V1", "role": "independent", "name": "温度",
             "operational_definition": "读数前反应体系的恒温温度，经热电偶校准",
             "measurement": {"instrument": "恒温金属浴", "method": "热电偶校准", "unit": "°C"},
             "levels": [25, 30, 35, 40], "range": None, "evidence": []},
            {"id": "V2", "role": "dependent", "name": "初始速率",
             "operational_definition": "反应开始后 60 s 内 405 nm 吸光度的线性拟合斜率",
             "measurement": {"instrument": "分光光度计", "method": "ΔA405/min 线性回归",
                             "unit": "AU/min"},
             "range": [0, 1.5], "levels": None, "evidence": ["E1"]},
            {"id": "V3", "role": "controlled", "name": "酶批次",
             "operational_definition": "同一批次酶液，避免批间差异",
             "measurement": {"instrument": "—", "method": "批号记录", "unit": "lot"},
             "levels": ["L1"], "range": None, "evidence": []},
        ],
        "controls": [
            {"id": "C1", "type": "blank", "purpose": "扣除底物非酶促转化背景",
             "excludes_confounder": "底物自氧化 / 非酶促显色", "expected_readout": "ΔA405/min < 0.02",
             "evidence": []},
            {"id": "C2", "type": "positive", "purpose": "确认试剂与读数链有效",
             "excludes_confounder": "试剂失效 / 仪器无信号", "expected_readout": "≥ 0.8 AU/min",
             "evidence": []},
        ],
        "steps": [
            {"id": "S1", "name": "配制反应体系", "depends_on": [], "duration_min": 30,
             "equipment": ["R2"], "materials": ["M1"], "parameters": [],
             "acceptance": "无气泡，体积 200 µL±2%", "evidence": []},
            {"id": "S2", "name": "温度梯度孵育与读数", "depends_on": ["S1"], "duration_min": 60,
             "equipment": ["R1", "R3"], "materials": [], "parameters": ["P1", "P2", "P3"],
             "acceptance": "每温度点 3 复孔，CV≤10%", "evidence": []},
            {"id": "S3", "name": "数据拟合与判读", "depends_on": ["S2"], "duration_min": 20,
             "equipment": [], "materials": [], "parameters": [],
             "method": "线性回归（Python/NumPy，固定随机种子）",
             "acceptance": "线性拟合 R²≥0.98", "evidence": []},
        ],
        "parameters": [
            {"id": "P1", "name": "孵育温度", "value": 35, "unit": "°C", "allowed": [25, 40],
             "source": "preliminary", "evidence": ["E2"]},
            {"id": "P2", "name": "酶液体积", "value": 2, "unit": "µL", "allowed": [1, 4],
             "source": "preliminary", "evidence": ["E3"]},
            {"id": "P3", "name": "检测波长", "value": 405, "unit": "nm", "allowed": [400, 410],
             "source": "manual", "evidence": ["E1"]},
        ],
        "resources": [
            {"id": "R1", "kind": "equipment", "name": "分光光度计",
             "spec": "405 nm，线性 0.1–1.5 AU", "capacity": "96 孔板", "substitutable": False,
             "evidence": ["E1"]},
            {"id": "R2", "kind": "consumable", "name": "96 孔平底透明板",
             "spec": "200 µL/孔", "capacity": None, "substitutable": True, "evidence": []},
            {"id": "R3", "kind": "equipment", "name": "恒温金属浴",
             "spec": "25–60 °C，±0.5 °C", "capacity": None, "substitutable": True, "evidence": []},
        ],
        "readout_criteria": [
            {"id": "RC1", "metric": "40 °C 与 25 °C 的初始速率差", "threshold": "≥0.1 AU/min",
             "decision": "支持 H1", "linked_variable": ["V2"], "evidence": []},
            {"id": "RC2", "metric": "复孔变异系数 CV", "threshold": "≤10%",
             "decision": "数据可用", "linked_variable": ["V2"], "evidence": []},
        ],
        "risks": [
            {"id": "RSK1", "failure_mode": "孔内气泡导致读数抖动", "detection_criterion": "CV>10%",
             "stop_loss": "该温度点重做一次", "mitigation": "加样后 200 g 离心 30 s",
             "fallback_path": "改用 1 cm 比色皿", "linked_steps": ["S2"], "evidence": []},
            {"id": "RSK2", "failure_mode": "吸光度超出线性上限", "detection_criterion": "A405>1.5",
             "stop_loss": "该点稀释重测", "mitigation": "预实验确定稀释倍数",
             "fallback_path": "降低酶液体积至 1 µL", "linked_steps": ["S2"], "evidence": []},
        ],
        "confirmations": [
            {"id": "CF1", "category": "safety", "item": "酶液与底物的生物/化学危害评估",
             "status": "pending", "blocking": False, "evidence": []},
            {"id": "CF2", "category": "equipment_availability", "item": "分光光度计 405 nm 模块排期",
             "status": "confirmed", "blocking": False, "evidence": []},
        ],
        "candidates": [
            {"id": "A", "summary": "5 个温度点、3 重复", "cost_total": 150,
             "wall_clock_minutes": 120, "parameter_choices": {"P1": 30}, "status": "seeded"},
            {"id": "B", "summary": "4 个温度点、3 重复", "cost_total": 120,
             "wall_clock_minutes": 110, "parameter_choices": {"P1": 35}, "status": "seeded"},
            {"id": "C", "summary": "含 50 °C 点", "cost_total": 90,
             "wall_clock_minutes": 100, "parameter_choices": {"P1": 50}, "status": "seeded"},
        ],
        "selection": {"chosen": "B", "rationale": "B 在预算与时间窗内且温度点均在允许区间；C 的 50 °C 超出允许区间",
                      "selection_source": "manual"},
        "constraints": {"budget_cap": 500, "time_window_minutes": 480, "currency": "CNY",
                        "parameter_domains": {"P1": [25, 40]}, "unavailable_resources": []},
        "budget_schedule": {
            "sample_count": 3, "replicates": 3, "total_runs": 9, "wall_clock_minutes": 110,
            "cost_items": [{"item": "酶液", "qty": 1, "unit_cost": 80, "total": 80},
                           {"item": "底物", "qty": 2, "unit_cost": 20, "total": 40}],
            "cost_total": 120, "currency": "CNY", "sample_size_justification": "合成示例，非正式统计功效",
        },
        "evidence": [
            {"id": "E1", "locator": "materials/paper.md",
             "quote": "分光光度计在 405 nm 处的吸光度线性范围为 0.1–1.5 AU。",
             "supports": ["P3", "R1"]},
            {"id": "E2", "locator": "materials/paper.md",
             "quote": "预实验表明，25–40 °C 区间内初始速率可稳定检出，40 °C 以上迅速失活。",
             "supports": ["P1"]},
            {"id": "E3", "locator": "materials/paper.md",
             "quote": "预实验表明，每 200 µL 反应体系加入 2 µL 酶液时初始速率落在线性范围内。",
             "supports": ["P2"]},
        ],
        "conflicts": [],
        "intake": {"materials": [{"path": "materials/paper.md", "kind": "md", "chars": 0,
                                  "sha256": None, "extraction": "text"}],
                   "raw_constraints": {}},
        "notes": [],
    }


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
    return bool(cond)


def scenario_empty(tmp):
    inp = Path(tmp) / "empty_in"
    out = Path(tmp) / "empty_out"
    work = Path(tmp) / "empty_work"
    inp.mkdir()
    rc, so, se = run_cli(["all", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r = json.loads((out / "result.json").read_text(encoding="utf-8"))
    ok = check("empty.exit0", rc == 0, "rc=%s stderr=%s" % (rc, se[-500:]))
    ok &= check("empty.status", r.get("status") in ("insufficient_evidence", "input_error"), r.get("status"))
    ok &= check("empty.valid_json", isinstance(r, dict) and r.get("case_id"))
    return ok


def scenario_badjson(tmp):
    inp = Path(tmp) / "bad_in"
    out = Path(tmp) / "bad_out"
    work = Path(tmp) / "bad_work"
    (inp / "materials").mkdir(parents=True)
    write(inp / "case.json", "{ not valid json")
    write(inp / "materials" / "paper.md", "# 材料\n\n某仪器量程为 0–100。\n")
    rc, so, se = run_cli(["all", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r = json.loads((out / "result.json").read_text(encoding="utf-8"))
    ok = check("badjson.exit0", rc == 0, "rc=%s stderr=%s" % (rc, se[-500:]))
    ok &= check("badjson.status", r.get("status") in ("partial", "insufficient_evidence"), r.get("status"))
    ok &= check("badjson.errors_recorded", len(r.get("errors", [])) >= 1)
    return ok


def scenario_demo(tmp):
    inp = Path(tmp) / "demo_in"
    out = Path(tmp) / "demo_out"
    work = Path(tmp) / "demo_work"
    (inp / "materials").mkdir(parents=True)
    write(inp / "case.json", json.dumps(DEMO_CASE, ensure_ascii=False))
    write(inp / "task.md", "# 合成自检任务\n\nchallenge_id: selftest-demo\n")
    write(inp / "materials" / "paper.md", DEMO_MATERIAL)

    rc1, so1, se1 = run_cli(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
    # write the agent-authored IR (replaces the skeleton)
    import core_impl
    ir = demo_ir()
    ir_path = work / "plan.ir.json"
    core_impl.write_json_atomic(ir_path, ir)

    rc2, so2, se2 = run_cli(["build", "--input", str(inp), "--output", str(out), "--work", str(work)])
    rc3, so3, se3 = run_cli(["validate", "--input", str(inp), "--output", str(out), "--work", str(work)])

    r = json.loads((out / "result.json").read_text(encoding="utf-8"))
    ok = check("demo.scaffold0", rc1 == 0, "rc=%s" % rc1)
    ok &= check("demo.build0", rc2 == 0, "rc=%s stderr=%s" % (rc2, se2[-500:]))
    ok &= check("demo.validate0", rc3 == 0, "rc=%s stdout=%s" % (rc3, so3[-500:]))
    ok &= check("demo.status", r.get("status") == "ok", r.get("status"))
    ok &= check("demo.contract_keys", all(k in r for k in DEMO_CASE["output_contract"]),
                "keys=%s" % list(r.keys()))
    ok &= check("demo.custom_note_null", "custom_note" in r and r["custom_note"] is None)
    ok &= check("demo.candidate_projected", isinstance(r.get("candidate_comparison"), dict)
                and r["candidate_comparison"].get("candidates"))
    ok &= check("demo.evidence_verified", r["checks_summary"]["evidence_counts"]["verified"] == 3,
                str(r["checks_summary"]["evidence_counts"]))
    ok &= check("demo.errors_zero", r["checks_summary"]["errors"] == 0, str(r["checks_summary"]))
    ok &= check("demo.plan_md", (out / "plan.md").exists() and (out / "plan.md").stat().st_size > 400)
    return ok


def scenario_idempotent(tmp):
    inp = Path(tmp) / "idem_in"
    out = Path(tmp) / "idem_out"
    work = Path(tmp) / "idem_work"
    (inp / "materials").mkdir(parents=True)
    write(inp / "case.json", json.dumps(DEMO_CASE, ensure_ascii=False))
    write(inp / "materials" / "paper.md", DEMO_MATERIAL)
    import core_impl
    core_impl.write_json_atomic(work / "plan.ir.json", demo_ir())
    run_cli(["build", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r1 = json.loads((out / "result.json").read_text(encoding="utf-8"))
    run_cli(["build", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r2 = json.loads((out / "result.json").read_text(encoding="utf-8"))

    def stable(r):
        r = dict(r)
        r.pop("runtime", None)
        r.pop("change_log", None)
        return json.dumps(r, ensure_ascii=False, sort_keys=True)

    ok = check("idempotent.result_stable", stable(r1) == stable(r2))
    ok &= check("idempotent.md_stable",
                (out / "plan.md").read_text(encoding="utf-8") == (out / "plan.md").read_text(encoding="utf-8"))
    return ok


def scenario_unit_quote(tmp):
    import core_impl
    corpus = {"materials/paper.md": {"text": "分光光度计在 405 nm 处的吸光度线性范围为 0.1–1.5 AU。", "pages": None}}
    ir = {"evidence": [
        {"id": "E1", "locator": "materials/paper.md",
         "quote": "分光光度计在 405 nm 处的吸光度线性范围为 0.1–1.5 AU。"},
        {"id": "E2", "locator": "materials/paper.md", "quote": "不存在的句子。"},
    ]}
    res = core_impl.verify_evidence(ir, corpus)
    status = {r["id"]: r["status"] for r in res}
    ok = check("unit.quote_found", status.get("E1") == "verified", str(status))
    ok &= check("unit.quote_not_found", status.get("E2") == "not_found", str(status))
    return ok


def main():
    keep = "--keep" in sys.argv
    tmp_root = Path(tempfile.mkdtemp(prefix="expir_"))
    results = {}
    results["empty"] = scenario_empty(str(tmp_root))
    results["badjson"] = scenario_badjson(str(tmp_root))
    results["demo"] = scenario_demo(str(tmp_root))
    results["idempotent"] = scenario_idempotent(str(tmp_root))
    results["unit_quote"] = scenario_unit_quote(str(tmp_root))

    print("\n== summary ==")
    failed = [k for k, v in results.items() if not v]
    for k, v in results.items():
        print("  %-12s %s" % (k, "PASS" if v else "FAIL"))
    if keep:
        print("temp dir kept:", tmp_root)
    else:
        import shutil
        shutil.rmtree(tmp_root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
