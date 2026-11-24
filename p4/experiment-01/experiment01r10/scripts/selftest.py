"""selftest.py — 本 Skill 的冒烟自检：空输入 / 坏 JSON / 缺输入 / 正常样例四类。

退出码 0 = 全部通过；1 = 存在失败。仅用标准库，不写正式测试产物。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as ci  # noqa: E402


class Args:
    def __init__(self, input_dir, output_dir, work_dir):
        self.input = input_dir
        self.output = output_dir
        self.work = work_dir


def run_case(name, setup, expect_status, expect_validate):
    root = tempfile.mkdtemp(prefix="selftest_%s_" % name)
    input_dir = os.path.join(root, "input")
    output_dir = os.path.join(root, "output")
    work_dir = os.path.join(root, "work")
    os.makedirs(input_dir, exist_ok=True)
    setup(input_dir)
    args = Args(input_dir, output_dir, work_dir)
    rc_probe = ci._probe(args)
    rc_scaffold = ci._scaffold(args)
    result = {}
    if os.path.exists(os.path.join(output_dir, "result.json")):
        result = json.load(open(os.path.join(output_dir, "result.json"), encoding="utf-8-sig"))
    status_ok = result.get("status") in (expect_status if isinstance(expect_status, tuple) else (expect_status,))
    rc_validate = ci._validate(args)
    vok = (rc_validate == 0) is expect_validate
    ok = rc_probe == 0 and rc_scaffold == 0 and status_ok and vok
    shutil.rmtree(root, ignore_errors=True)
    return ok, {"status": result.get("status"), "rc_validate": rc_validate}


def setup_empty(d):
    pass


def setup_bad_json(d):
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ]")


def setup_missing(d):
    # 目录已存在，无 case.json / task.md
    with open(os.path.join(d, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("no contract here")


def setup_normal(d):
    case = {
        "case_id": "demo-001",
        "goal": "评估某抑制剂对某细胞系增殖的影响（自检样例，非正式案例）",
        "output_contract": {
            "plan": {},
            "evidence_and_risks": {},
            "confirmations": {},
        },
        "candidate_plans": [
            {"id": "P1", "text": "剂量 0-10 uM，n=3"},
            {"id": "P2", "text": "剂量 0-100 uM，n=6"},
        ],
    }
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(d, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 任务\nchallenge_id: `demo-001`\n")
    os.makedirs(os.path.join(d, "materials"))
    with open(os.path.join(d, "materials", "spec.txt"), "w", encoding="utf-8") as f:
        f.write("device range 0-50 uM; price list per unit 10 CNY\n")
    with open(os.path.join(d, "materials", "booking_contract.txt"), "w", encoding="utf-8") as f:
        f.write("booking window 7 days; quote total 5000 CNY\n")


def main():
    cases = [
        ("empty", setup_empty, "insufficient_evidence", True),
        ("bad_json", setup_bad_json, ("insufficient_evidence", "partial"), True),
        ("missing_input", setup_missing, "insufficient_evidence", True),
        ("normal", setup_normal, "partial", True),
    ]
    fails = 0
    for name, setup, status, expect_validate in cases:
        ok, info = run_case(name, setup, status, expect_validate)
        print("[%s] %s  status=%s validate_rc=%s" % ("PASS" if ok else "FAIL", name, info["status"], info["rc_validate"]))
        if not ok:
            fails += 1
    print("selftest: %d/%d passed" % (len(cases) - fails, len(cases)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
