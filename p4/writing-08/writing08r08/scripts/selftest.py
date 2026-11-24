#!/usr/bin/env python3
"""selftest.py — 交付前离线自检：空输入、坏 JSON、最小案例、重复运行一致性。

在临时目录构造四种输入，依次调用 skeleton/inventory/validate 三脚本，
验证"空/坏输入不崩溃、契约字段回填、重复运行一致"。只依赖标准库。
用法：python3 scripts/selftest.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run_script(name, args):
    cmd = [sys.executable, "-B", os.path.join(HERE, name)] + args
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace"), \
        proc.stderr.decode("utf-8", errors="replace")


def load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    return bool(cond)


results = []


def test_help():
    for s in ("skeleton.py", "inventory.py", "validate.py"):
        rc, out, err = run_script(s, ["--help"])
        check("help_%s" % s, rc == 0 and "usage" in (out + err).lower(),
              "%s --help 退出码 %d" % (s, rc))


def test_empty_input():
    d = tempfile.mkdtemp(prefix="selftest_empty_")
    try:
        inp = os.path.join(d, "input"); out = os.path.join(d, "output"); wrk = os.path.join(d, "work")
        os.makedirs(inp)
        rc, _, _ = run_script("skeleton.py", ["--input", inp, "--output", out, "--work", wrk])
        r = load(os.path.join(out, "result.json"))
        check("empty_no_crash", rc == 0, "空输入 skeleton 退出码 %d" % rc)
        check("empty_status", r is not None and r.get("status") == "input_error",
              "空输入 status=%r" % (r.get("status") if r else None))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_bad_json():
    d = tempfile.mkdtemp(prefix="selftest_badjson_")
    try:
        inp = os.path.join(d, "input"); out = os.path.join(d, "output"); wrk = os.path.join(d, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json ")
        rc, _, _ = run_script("skeleton.py", ["--input", inp, "--output", out, "--work", wrk])
        r = load(os.path.join(out, "result.json"))
        check("badjson_no_crash", rc == 0, "坏 JSON skeleton 退出码 %d" % rc)
        check("badjson_status", r is not None and r.get("status") == "input_error",
              "坏 JSON status=%r" % (r.get("status") if r else None))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_minimal_case():
    d = tempfile.mkdtemp(prefix="selftest_min_")
    try:
        inp = os.path.join(d, "input"); out = os.path.join(d, "output"); wrk = os.path.join(d, "work")
        os.makedirs(inp)
        case = {
            "case_id": "demo-1",
            "output_contract": {"deliverable": "string", "files": "array",
                                "meta": "object"},
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# 测试任务\n")
        with open(os.path.join(inp, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write("lr=0.001\nbatch_size: 32\nseed: 42\n")

        rc, _, _ = run_script("skeleton.py", ["--input", inp, "--output", out, "--work", wrk])
        r = load(os.path.join(out, "result.json"))
        check("min_skeleton_rc", rc == 0, "最小案例 skeleton 退出码 %d" % rc)
        contract_ok = (r is not None and r.get("case_id") == "demo-1"
                       and "deliverable" in r and "files" in r and "meta" in r
                       and r.get("files") == [] and r.get("meta") == {})
        check("min_contract_backfill", contract_ok, "output_contract 字段原样回填")

        run_script("inventory.py", ["--input", inp, "--output", out, "--work", wrk])
        facts = load(os.path.join(wrk, "extracted_facts.json"))
        check("min_inventory_facts", facts is not None and len(facts.get("facts", [])) > 0,
              "最小案例抽取候选事实 %d 条" % (len(facts["facts"]) if facts else -1))

        # 填充证据映射 + 产物 + ok 状态后 validate 应通过
        if r is not None:
            with open(os.path.join(out, "manuscript.md"), "w", encoding="utf-8") as fh:
                fh.write("# Methods\n\nlr=0.001 [S1]\n")
            r["manuscript"] = "manuscript.md"
            r["evidence_map"] = {"S1": {"locator": "materials/config.yaml#L1", "quote": "lr=0.001"}}
            r["artifacts"] = ["manuscript.md"]
            r["status"] = "ok"
            r["pending_human"] = []
            r["limitations"] = ["材料仅含最小配置，训练/测试条件未知，按 not_reported 处理"]
            r["change_log"].append({"step": 3, "action": "test", "status": "ok"})
            with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as fh:
                json.dump(r, fh, ensure_ascii=False, indent=2)
        rc, _, _ = run_script("validate.py", ["--input", inp, "--output", out, "--work", wrk])
        check("min_validate_pass", rc == 0, "填充后 validate 退出码 %d" % rc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_determinism():
    d = tempfile.mkdtemp(prefix="selftest_det_")
    try:
        case = {"case_id": "det", "output_contract": {"x": "string"}}
        outs = []
        for i in range(2):
            inp = os.path.join(d, "input%d" % i); out = os.path.join(d, "output%d" % i)
            wrk = os.path.join(d, "work%d" % i)
            os.makedirs(inp)
            with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
                json.dump(case, fh)
            run_script("skeleton.py", ["--input", inp, "--output", out, "--work", wrk])
            r = load(os.path.join(out, "result.json"))
            if r:
                r.pop("runtime", None)
                cl = r.pop("change_log", None)
                outs.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
        check("deterministic_layout", len(outs) == 2 and outs[0] == outs[1],
              "两次运行（忽略时间戳/change_log）结构一致")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    test_help()
    test_empty_input()
    test_bad_json()
    test_minimal_case()
    test_determinism()
    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    print(json.dumps({"passed": passed, "total": len(results),
                      "failed": failed}, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
