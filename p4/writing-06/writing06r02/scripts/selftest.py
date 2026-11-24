#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paper-structure-review 自检脚本。

覆盖三类场景：空输入目录、坏 JSON、正常稿件样例；并校验骨架与验证步骤的契约。
只依赖 Python 标准库；不读取任何真实案例、不生成正式测试产物。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

SAMPLE_PAPER = """# Title\n\n## 1. Introduction\n\nThis study aims to test whether X improves Y.\n\n## 2. Methods\n\nWe recruited n = 40 participants.\n\n## 3. Results\n\nThe treatment significantly improved Y (p = 0.02).\n\n## 4. Discussion\n\nX causes Y in this cohort.\n\n## 5. Conclusion\n\nX improves Y.\n\nWe recruited n = 40 participants.\n"""


def run_main(input_dir, work, out, case_path, task_path):
    return subprocess.run(
        [sys.executable, MAIN,
         "--input", input_dir, "--work", work, "--output", out,
         "--case", case_path, "--task", task_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def check(name, cond, detail=""):
    print(("%s  %s  %s" % ("PASS" if cond else "FAIL", name, detail)))
    return bool(cond)


def main() -> int:
    fails = 0
    with tempfile.TemporaryDirectory(prefix="psr-self-") as root:
        # 场景 1：空输入目录 → 退出码 0，写 insufficient 骨架。
        d1 = os.path.join(root, "empty")
        os.makedirs(d1, exist_ok=True)
        work = os.path.join(root, "w1")
        out = os.path.join(root, "o1")
        r = run_main(d1, work, out, os.path.join(d1, "case.json"),
                     os.path.join(d1, "task.md"))
        r1 = None
        if os.path.isfile(os.path.join(out, "result.json")):
            r1 = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        ev1 = None
        if os.path.isfile(os.path.join(work, "evidence.json")):
            ev1 = json.load(open(os.path.join(work, "evidence.json"), encoding="utf-8"))
        fails += 0 if check("empty-dir exit 0", r.returncode == 0, "rc=%s" % r.returncode) else 1
        fails += 0 if check("empty-dir result exists", r1 is not None) else 1
        fails += 0 if check("empty-dir status", r1 and r1.get("status") == "in_progress") else 1
        fails += 0 if check("empty-dir evidence error",
                            ev1 and "empty_input_dir" in ev1.get("errors", [])) else 1

        # 场景 2：坏 JSON → 不崩溃，case_id 降级为 unknown。
        d2 = os.path.join(root, "badjson")
        os.makedirs(d2, exist_ok=True)
        with open(os.path.join(d2, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not json !!")
        work2 = os.path.join(root, "w2")
        out2 = os.path.join(root, "o2")
        r2 = run_main(d2, work2, out2, os.path.join(d2, "case.json"),
                      os.path.join(d2, "task.md"))
        r2j = None
        if os.path.isfile(os.path.join(out2, "result.json")):
            r2j = json.load(open(os.path.join(out2, "result.json"), encoding="utf-8"))
        fails += 0 if check("bad-json exit 0", r2.returncode == 0) else 1
        fails += 0 if check("bad-json case_id unknown", r2j and r2j.get("case_id") == "unknown") else 1

        # 场景 3：正常稿件 → 抽取候选锚点，校验可回溯。
        d3 = os.path.join(root, "paper")
        os.makedirs(d3, exist_ok=True)
        with open(os.path.join(d3, "paper.md"), "w", encoding="utf-8") as f:
            f.write(SAMPLE_PAPER)
        with open(os.path.join(d3, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "demo-case", "id": "demo-case",
                       "output_contract": {"diagnosis": None, "edit_plan": None}},
                      f, ensure_ascii=False)
        work3 = os.path.join(root, "w3")
        out3 = os.path.join(root, "o3")
        r3 = run_main(d3, work3, out3, os.path.join(d3, "case.json"),
                      os.path.join(d3, "task.md"))
        r3j = json.load(open(os.path.join(out3, "result.json"), encoding="utf-8"))
        ev = json.load(open(os.path.join(work3, "evidence.json"), encoding="utf-8"))
        paper_feat = next((f for f in ev["features"] if f["file"] == "paper.md"), {})
        fails += 0 if check("paper exit 0", r3.returncode == 0) else 1
        fails += 0 if check("paper case_id", r3j.get("case_id") == "demo-case") else 1
        fails += 0 if check("contract keys kept",
                            set(("diagnosis", "edit_plan")).issubset(r3j.keys())) else 1
        fails += 0 if check("sections detected", len(paper_feat.get("sections", [])) >= 5) else 1
        fails += 0 if check("claim candidates", len(paper_feat.get("claim_sentences", [])) >= 2) else 1
        fails += 0 if check("number candidates", len(paper_feat.get("number_sentences", [])) >= 1) else 1
        fails += 0 if check("redundancy hint", len(paper_feat.get("redundancy_hints", [])) >= 1) else 1

        # 场景 4：校验脚本对缺字段的 result.json 报错。
        bad_result = os.path.join(out3, "result.json")
        data = json.load(open(bad_result, encoding="utf-8"))
        data.pop("sources")
        json.dump(data, open(bad_result, "w", encoding="utf-8"), ensure_ascii=False)
        sys.path.insert(0, HERE)
        import core_impl as core
        vf = core.verify_contract(bad_result, d3)
        fails += 0 if check("verify flags missing sources", vf.get("ok") is False) else 1

    print("\nselftest: %s" % ("ALL PASS" if fails == 0 else "%d FAIL" % fails))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
