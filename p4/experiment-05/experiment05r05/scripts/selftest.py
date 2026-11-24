#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：在临时 fixture 上跑通 骨架→画像→更新→校验→报告→定稿 全链路。

覆盖：空输入、坏 JSON、缺 case.json、含额外 output_contract 键的最小案例、确定性。
仅使用 Python 标准库；退出码 0=全绿，1=失败。

用法：python scripts/selftest.py
"""

import copy
import json
import os
import shutil
import sys
import tempfile

import main as skill_main

FIXTURE_CSV = "x,force_N,batch\n0,1.0,A\n1,1.2,A\n2,1.1,A\n3,50.0,B\n4,1.3,A\n"


def strip_volatile(obj):
    """复制并抹去时间戳字段，用于确定性比较。"""
    o = copy.deepcopy(obj)
    if isinstance(o, dict):
        for k in ("ts", "started_at", "finished_at", "elapsed_seconds"):
            if k in o:
                o[k] = ""
        for v in o.values():
            strip_volatile(v)
    elif isinstance(o, list):
        for v in o:
            strip_volatile(v)
    return o


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run():
    results = []
    root = tempfile.mkdtemp(prefix="selftest_")

    def add(cond, label):
        results.append(cond)
        if not cond:
            print("  FAIL: %s" % label)

    try:
        # ---- 1. 空输入 ----
        empty_in = os.path.join(root, "empty_in")
        empty_out = os.path.join(root, "empty_out")
        os.makedirs(empty_in)
        r = skill_main.build_skeleton(empty_in, empty_out)
        add(r["status"] == "insufficient_evidence", "empty: status insufficient_evidence")
        add(all(k in r for k in skill_main.TOP_KEYS), "empty: 16 个顶层键齐全")
        ok, issues = skill_main.validate(os.path.join(empty_out, "result.json"))
        add(ok, "empty: validate OK (%d issues)" % len(issues))

        # ---- 2. 坏 JSON case.json ----
        bad_in = os.path.join(root, "bad_in")
        bad_out = os.path.join(root, "bad_out")
        write(os.path.join(bad_in, "case.json"), "{not valid json")
        write(os.path.join(bad_in, "data.csv"), FIXTURE_CSV)
        r = skill_main.build_skeleton(bad_in, bad_out)
        add(any(e.get("locator") == "case.json" for e in r.get("errors", [])), "badjson: 记录 errors")
        add(r["status"] in skill_main.STATUSES, "badjson: status 合法")

        # ---- 3. 含 output_contract 额外键的最小案例 ----
        mini_in = os.path.join(root, "mini_in")
        mini_out = os.path.join(root, "mini_out")
        write(os.path.join(mini_in, "case.json"), json.dumps({
            "case_id": "challenge-8354c69a",
            "output_contract": {"fields": ["extra_field", "case_id", "status"]},
        }))
        write(os.path.join(mini_in, "task.md"), "# task\nchallenge_id: challenge-8354c69a\n")
        write(os.path.join(mini_in, "data.csv"), FIXTURE_CSV)
        r = skill_main.build_skeleton(mini_in, mini_out)
        add(r["case_id"] == "challenge-8354c69a", "mini: case_id 解析")
        add("extra_field" in r, "mini: output_contract 额外键已回填")
        ok, issues = skill_main.validate(os.path.join(mini_out, "result.json"), os.path.join(mini_in, "case.json"))
        add(ok, "mini: validate OK（含额外键）")

        # ---- 4. inspect_data ----
        import inspect_data
        rv = inspect_data.main(["--input", mini_in, "--output", mini_out])
        add(rv == 0, "inspect: 退出码 0")
        prof = skill_main.load_json_safe(os.path.join(mini_out, "data_profile.json"))
        add(prof is not None and any(f["rel"] == "data.csv" for f in prof.get("files", [])), "inspect: 画像包含 data.csv")

        # ---- 5. update + 证据完整性 ----
        patch = {"interpretation": {"trends": [{
            "id": "t1", "claim": "力随位移上升", "direction": "增", "interval": "x=0..4",
            "control": "batch A", "fit_method": "线性", "magnitude": "0.1 N/mm", "unit": "N/mm",
            "evidence": [{"locator": "data.csv#L2-L6", "quote": "1.0,1.2,1.1,1.3"}],
        }]}}
        write(os.path.join(root, "patch.json"), json.dumps(patch))
        skill_main.apply_update(os.path.join(mini_out, "result.json"), os.path.join(root, "patch.json"), "P2-interpretation", "写 trends", "填充趋势")
        r2 = skill_main.load_json_safe(os.path.join(mini_out, "result.json"))
        add(len(r2["interpretation"]["trends"]) == 1, "update: trends 已写入")
        ok, issues = skill_main.validate(os.path.join(mini_out, "result.json"), os.path.join(mini_in, "case.json"))
        add(ok, "update: validate OK（证据齐全）")

        # ---- 6. 缺证据项应报错 ----
        patch_bad = {"interpretation": {"anomalies": [{"id": "a1", "claim": "异常", "object": "x", "observed": 1, "expected": 1, "deviation": 0, "threshold_method": "z", "threshold_value": 3, "exceeds": False, "relation_to_control": "n/a"}]}}
        write(os.path.join(root, "patch_bad.json"), json.dumps(patch_bad))
        skill_main.apply_update(os.path.join(mini_out, "result.json"), os.path.join(root, "patch_bad.json"), "P2", "写 anomalies", "缺证据测试")
        ok_bad, issues_bad = skill_main.validate(os.path.join(mini_out, "result.json"), os.path.join(mini_in, "case.json"))
        add(not ok_bad and any("evidence" in i["path"] for i in issues_bad), "validate: 缺证据被拦截")

        # ---- 7. report / finalize ----
        skill_main.build_report(os.path.join(mini_out, "result.json"), mini_out)
        add(os.path.isfile(os.path.join(mini_out, "report.md")), "report: report.md 存在")
        add(os.path.isfile(os.path.join(mini_out, "evidence_map.json")), "report: evidence_map.json 存在")
        rf = skill_main.finalize(os.path.join(mini_out, "result.json"))
        add(rf["runtime"].get("finished_at"), "finalize: runtime.finished_at 写入")

        # ---- 8. 确定性（抹去时间戳后一致）----
        d1 = skill_main.build_skeleton(mini_in, os.path.join(root, "det1"))
        d2 = skill_main.build_skeleton(mini_in, os.path.join(root, "det2"))
        add(strip_volatile(d1) == strip_volatile(d2), "determinism: 骨架两次一致（时间戳除外）")

        # ---- 9. 缺 case.json ----
        miss_in = os.path.join(root, "miss_in")
        miss_out = os.path.join(root, "miss_out")
        os.makedirs(miss_in)
        write(os.path.join(miss_in, "data.csv"), FIXTURE_CSV)
        r = skill_main.build_skeleton(miss_in, miss_out)
        add(bool(r["case_id"]), "missing: 缺 case.json 仍得到 case_id（目录名兜底）")

        passed = all(results)
        print("SELFTEST %s: %d/%d checks passed" % ("OK" if passed else "FAIL", sum(1 for x in results if x), len(results)))
        return 0 if passed else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run())
