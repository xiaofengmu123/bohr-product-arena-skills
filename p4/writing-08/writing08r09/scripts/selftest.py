#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：空输入、坏 JSON、最小样例三种场景，全部通过则退出码 0。"""
import json
import os
import subprocess
import sys
import tempfile

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def run(*argv):
    return subprocess.run([sys.executable, MAIN, *argv], capture_output=True, text=True)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check(label, cond):
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"ok: {label}")


def scenario_empty_input():
    with tempfile.TemporaryDirectory() as t:
        inp, out, work = (os.path.join(t, x) for x in ("input", "output", "work"))
        os.makedirs(inp)
        r = run("skeleton", "--input", inp, "--output", out, "--work", work)
        check("empty input exit 0", r.returncode == 0)
        res = read_json(os.path.join(out, "result.json"))
        check("empty input status legal", res["status"] in ("input_error", "insufficient_evidence"))
        check("empty input case_id", bool(res.get("case_id")))
        check("empty input limitations list", isinstance(res.get("limitations"), list))
    print("scenario empty_input passed")


def scenario_bad_json():
    with tempfile.TemporaryDirectory() as t:
        inp, out, work = (os.path.join(t, x) for x in ("input", "output", "work"))
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ this is not json ")
        r = run("skeleton", "--input", inp, "--output", out, "--work", work)
        check("bad json exit 0", r.returncode == 0)
        res = read_json(os.path.join(out, "result.json"))
        check("bad json status input_error", res["status"] == "input_error")
        check("bad json errors recorded", any("case.json" in e for e in res.get("errors", [])))
    print("scenario bad_json passed")


def scenario_minimal():
    with tempfile.TemporaryDirectory() as t:
        inp, out, work = (os.path.join(t, x) for x in ("input", "output", "work"))
        os.makedirs(inp)
        case = {
            "case_id": "demo-case-001",
            "output_contract": {
                "methods_section": "string",
                "parameter_table": "array",
                "evidence_map": "object",
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
            f.write("# 任务\n写方法与实验章节，保留来源。")
        os.makedirs(os.path.join(inp, "materials"))
        with open(os.path.join(inp, "materials", "train.yaml"), "w", encoding="utf-8") as f:
            f.write("lr: 0.001\nseed: 42\nbatch_size: 32\noptimizer: adam\n")

        r = run("skeleton", "--input", inp, "--output", out, "--work", work)
        check("minimal skeleton exit 0", r.returncode == 0)
        res = read_json(os.path.join(out, "result.json"))
        check("minimal case_id", res["case_id"] == "demo-case-001")
        check("minimal status partial", res["status"] == "partial")
        for k in case["output_contract"]:
            check(f"minimal contract field {k}", k in res)

        r = run("inventory", "--input", inp, "--output", out, "--work", work)
        check("minimal inventory exit 0", r.returncode == 0)
        inv = read_json(os.path.join(work, "materials_inventory.json"))
        check("minimal inventory found material", inv["summary"]["total"] >= 1)
        facts = read_json(os.path.join(work, "extracted_facts.json"))
        cats = {f["category"] for f in facts["facts"]}
        check("minimal facts extracted", "hyperparam" in cats or "seed" in cats)
        any_fact = facts["facts"][0]
        check("minimal fact locator", bool(any_fact["locator"]))
        check("minimal fact quote", bool(any_fact["quote"]))

        r = run("validate", "--input", inp, "--output", out, "--work", work)
        check("minimal validate exit 0", r.returncode == 0)
        report = read_json(os.path.join(work, "validation_report.json"))
        check("minimal validate report ok key", "ok" in report)
    print("scenario minimal passed")


def main():
    scenario_empty_input()
    scenario_bad_json()
    scenario_minimal()
    print("selftest: all scenarios passed")


if __name__ == "__main__":
    main()
