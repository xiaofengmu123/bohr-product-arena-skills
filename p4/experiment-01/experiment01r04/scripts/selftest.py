"""selftest.py - package self-check for the experiment-plan skill.

Runs fully in-process (no subprocess pipes) so it also works inside the
evaluation sandbox. Checks: YAML frontmatter, stdlib-only imports, script
presence, three degraded inputs (empty dir, bad JSON, missing input), contract
fill, determinism, and that a complete result passes validate while dishonest /
unfunded / out-of-range results are blocked.

Exit code 0 = every check passed. Failures print a diff-friendly list.
"""

import contextlib
import io
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl  # noqa: E402

CHECKS = []


def record(name, passed, detail=""):
    CHECKS.append((name, bool(passed), detail))
    return bool(passed)


# --------------------------------------------------------------------------
# in-process command runner
# --------------------------------------------------------------------------

def run_command(cmd, input_dir, output_dir, work_dir):
    ns = type("Args", (), {"input": input_dir, "output": output_dir, "work": work_dir})()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        if cmd == "probe":
            return core_impl.run_probe(ns)
        if cmd == "scaffold":
            return core_impl.run_scaffold(ns)
        return core_impl.run_validate(ns)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_case(input_dir, case_obj):
    os.makedirs(input_dir, exist_ok=True)
    with open(os.path.join(input_dir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case_obj, fh, ensure_ascii=False)


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_package_layout():
    for name in ("main.py", "core_impl.py", "selftest.py"):
        record("脚本存在: %s" % name, os.path.isfile(os.path.join(HERE, name)))
    record("SKILL.md 存在", os.path.isfile(os.path.join(HERE, "..", "SKILL.md")))


def check_frontmatter():
    path = os.path.join(HERE, "..", "SKILL.md")
    text = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    ok = record("SKILL.md 有 YAML frontmatter", bool(match))
    fm = match.group(1) if match else ""
    for key in ("name", "description", "version"):
        record("frontmatter 含 %s" % key, bool(re.search(r"(?m)^%s\s*:" % key, fm)) if ok else False)


def check_stdlib_only():
    third_party = {
        "numpy", "pandas", "scipy", "matplotlib", "requests", "yaml", "bs4",
        "lxml", "docx", "openpyxl", "pptx", "PIL", "sklearn", "torch",
    }
    offenders = []
    for name in ("main.py", "core_impl.py", "selftest.py"):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                mod = None
                if stripped.startswith("import "):
                    mod = stripped.split()[1].split(".")[0].rstrip(",")
                elif stripped.startswith("from ") and " import " in stripped:
                    mod = stripped.split()[1].split(".")[0]
                if mod and mod in third_party:
                    offenders.append("%s: %s" % (name, mod))
    record("脚本仅依赖标准库", not offenders, ", ".join(offenders))


def check_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "in")
        os.makedirs(inp)
        out = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")
        run_command("probe", inp, out, work)
        rc = run_command("scaffold", inp, out, work)
        status = (read_json(os.path.join(out, "result.json")) or {}).get("status")
        vrc = run_command("validate", inp, out, work)
        record("空输入 scaffold 退出码 0", rc == 0, "rc=%s" % rc)
        record("空输入 status=insufficient_evidence", status == "insufficient_evidence", str(status))
        record("空输入 validate 退出码 0", vrc == 0, "rc=%s" % vrc)


def check_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "in")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        out = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")
        rc = run_command("scaffold", inp, out, work)
        status = (read_json(os.path.join(out, "result.json")) or {}).get("status")
        vrc = run_command("validate", inp, out, work)
        record("坏 JSON scaffold 不崩溃(rc=0)", rc == 0, "rc=%s" % rc)
        record("坏 JSON status=input_error", status == "input_error", str(status))
        record("坏 JSON validate 退出码 0", vrc == 0, "rc=%s" % vrc)


def check_contract_fill():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "in")
        write_case(inp, {
            "case_id": "case-xyz",
            "output_contract": {
                "plan": {}, "evidence_and_risks": {},
                "confirmations": {}, "version_notes": None,
            },
        })
        out = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")
        run_command("scaffold", inp, out, work)
        result = read_json(os.path.join(out, "result.json")) or {}
        record("契约回填: case_id", result.get("case_id") == "case-xyz", str(result.get("case_id")))
        record("契约回填: 保留未知键 version_notes", "version_notes" in result)
        plan = result.get("plan")
        record("契约回填: plan 含六要素模板",
               isinstance(plan, dict) and "controls" in plan and "time_cost" in plan)


def check_determinism():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "in")
        write_case(inp, {
            "case_id": "det-1",
            "output_contract": {"plan": {}, "evidence_and_risks": {}, "confirmations": {}},
        })
        work = os.path.join(tmp, "work")
        out1 = os.path.join(tmp, "o1")
        out2 = os.path.join(tmp, "o2")
        run_command("scaffold", inp, out1, work)
        run_command("scaffold", inp, out2, work)
        with open(os.path.join(out1, "result.json"), "rb") as fh:
            b1 = fh.read()
        with open(os.path.join(out2, "result.json"), "rb") as fh:
            b2 = fh.read()
        record("重复 scaffold 字节一致（确定性）", b1 == b2)


# --------------------------------------------------------------------------
# a fully populated, honest result used to test the validate gate
# --------------------------------------------------------------------------

def complete_result():
    return {
        "case_id": "demo-001",
        "plan": {
            "hypothesis_and_variables": {
                "hypothesis": "药物 X 在 10–50 uM 内剂量依赖抑制增殖",
                "independent_variables": [{
                    "name": "药物 X 浓度", "operational_definition": "培养基终浓度",
                    "unit": "uM", "levels_or_range": "0,10,20,50",
                }],
                "dependent_variables": [{
                    "name": "相对活力", "operational_definition": "48h CCK-8 OD450 相对对照",
                    "unit": "%",
                }],
                "confounders": [{"name": "接种密度", "how_to_control": "统一 5e3/孔"}],
                "operational_definitions": ["活力=处理组OD/溶剂对照OD×100%"],
                "measurement_methods": ["CCK-8, 450 nm"],
            },
            "controls": {
                "groups": [
                    {"id": "C1", "type": "溶剂空白", "purpose": "扣除溶剂效应",
                     "confounder_addressed": "DMSO 毒性", "n_per_group": 6,
                     "justification": "生物学重复"},
                    {"id": "C2", "type": "阳性对照", "purpose": "验证检测灵敏",
                     "confounder_addressed": "无响应假阴性", "n_per_group": 6,
                     "justification": "已知抑制剂"},
                ],
                "randomization": "按孔位随机", "blinding": "读数盲法",
                "replicates": "3 次独立实验",
            },
            "equipment_materials": {
                "items": [
                    {"name": "酶标仪", "spec_or_model": "450 nm 滤光片", "quantity": 1,
                     "unit": "台", "purpose": "OD 读数", "essential": True,
                     "alternative": "同类 450 nm 酶标仪", "basis": "case_given"},
                    {"name": "药物 X", "spec_or_model": "10 mM 母液", "quantity": 1,
                     "unit": "瓶", "purpose": "处理", "essential": True,
                     "alternative": "同靶点抑制剂", "basis": "case_given"},
                ],
                "notes": "记录试剂批号",
            },
            "time_cost": {
                "steps": [
                    {"id": "S1", "name": "铺板", "duration_hours": 2,
                     "equipment": "生物安全柜", "materials": "细胞",
                     "parallel_group": "A", "depends_on": []},
                    {"id": "S2", "name": "加药与培养", "duration_hours": 3,
                     "equipment": "培养箱", "materials": "药物 X",
                     "parallel_group": "A", "depends_on": ["S1"]},
                ],
                "sample_count": 6, "total_hours": 5, "budget": "case_given",
                "budget_items": [{"item": "CCK-8 试剂", "amount": 1,
                                  "currency": "kit", "basis": "case_given"}],
                "schedule_conflicts": [],
            },
            "parameter_ranges": {
                "parameters": [{
                    "name": "药物 X 浓度", "unit": "uM",
                    "allowed_min": 10, "allowed_max": 50,
                    "equipment_range_min": 0, "equipment_range_max": 100,
                    "basis": "case_given", "out_of_range_handling": "超出则稀释重配",
                }],
            },
            "risks_and_anomalies": {
                "risks": [{
                    "failure_mode": "污染", "detection": "培养基浑浊",
                    "threshold": "任一孔浑浊", "stop_point": "整批弃用",
                    "alternative_path": "加双抗重做",
                }],
                "notes": "",
            },
        },
        "evidence_and_risks": {
            "evidence": [{
                "id": "E1", "claim": "检测波长 450 nm",
                "locator": "materials/protocol.md#p2",
                "quote": "read absorbance at 450 nm",
                "source_id": "protocol",
            }],
            "risks": [{
                "id": "R1", "failure_mode": "无信号", "detection": "OD 接近背景",
                "threshold": "OD<0.1", "stop_point": "停止扩样",
                "alternative_path": "延长孵育或更换试剂",
                "probability_estimate": "low", "basis": "assumption",
            }],
        },
        "confirmations": [],
        "status": "ok",
        "sources": [{"id": "protocol", "kind": "material",
                     "locator": "materials/protocol.md", "title": "实验流程", "note": ""}],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "degradations": [],
        "errors": [],
        "change_log": [{"seq": 1, "event": "skeleton_written"}],
        "runtime": {"engine": "core_impl.py", "version": "1.0.0"},
    }


def check_validation_gate():
    keys = ["plan", "evidence_and_risks", "confirmations"]
    report = core_impl.validate_result_object(complete_result(), keys)
    record("完整结果 validate 通过", report["ok"],
           json.dumps(report["errors"], ensure_ascii=False)[:300])

    dishonest = complete_result()
    dishonest["plan"]["controls"] = {}
    report = core_impl.validate_result_object(dishonest, keys)
    record("六要素为空仍标 ok 被拦截", not report["ok"])

    unfunded = complete_result()
    del unfunded["plan"]["parameter_ranges"]["parameters"][0]["basis"]
    report = core_impl.validate_result_object(unfunded, keys)
    record("无 basis 的参数被拦截", not report["ok"])
    record("无 basis 计入 unfounded_claims", len(report["unfounded_claims"]) >= 1)

    out_of_range = complete_result()
    out_of_range["plan"]["parameter_ranges"]["parameters"][0]["equipment_range_max"] = 20
    report = core_impl.validate_result_object(out_of_range, keys)
    record("参数超设备量程被拦截", not report["ok"])
    record("算术冲突被记录", len(report["arithmetic_conflicts"]) >= 1)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    check_package_layout()
    check_frontmatter()
    check_stdlib_only()
    check_empty_input()
    check_bad_json()
    check_contract_fill()
    check_determinism()
    check_validation_gate()

    failed = [c for c in CHECKS if not c[1]]
    for name, passed, detail in CHECKS:
        mark = "PASS" if passed else "FAIL"
        line = "[%s] %s" % (mark, name)
        if detail and not passed:
            line += "  ->  " + detail
        print(line)
    print("\n%d/%d checks passed" % (len(CHECKS) - len(failed), len(CHECKS)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
