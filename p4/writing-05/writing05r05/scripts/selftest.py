#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：在临时目录构造空输入/坏 JSON/最小合法案例/重复运行，跑 main.py 并断言结果。

只依赖标准库；退出码 0 = 全部通过，1 = 有失败。可反复运行，不触碰真实输入。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run_main(args):
    return subprocess.run(
        [sys.executable, MAIN] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  | " + detail) if detail else ""))
    return bool(cond)


def main():
    ok = True
    base = tempfile.mkdtemp(prefix="evw_selftest_")
    try:
        # 1. 空输入目录：scaffold 合法骨架，finalize 诚实降级。
        empty_in = os.path.join(base, "empty_in")
        empty_out = os.path.join(base, "empty_out")
        empty_work = os.path.join(base, "empty_work")
        os.makedirs(empty_in)
        r = run_main(["scaffold", "--input", empty_in, "--output", empty_out, "--work", empty_work])
        res = json.load(open(os.path.join(empty_out, "result.json"), encoding="utf-8"))
        ok &= check("空输入目录 scaffold 落盘 in_progress", r.returncode == 0 and res.get("status") == "in_progress")
        r = run_main(["finalize", "--input", empty_in, "--output", empty_out, "--work", empty_work])
        res2 = json.load(open(os.path.join(empty_out, "result.json"), encoding="utf-8"))
        ok &= check("空输入目录 finalize 降级 insufficient_evidence", r.returncode == 0 and res2.get("status") == "insufficient_evidence")

        # 2. 坏 JSON：不崩溃，记录 errors。
        bad_in = os.path.join(base, "bad_in")
        bad_out = os.path.join(base, "bad_out")
        bad_work = os.path.join(base, "bad_work")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "x", "broken": ')
        r = run_main(["scaffold", "--input", bad_in, "--output", bad_out, "--work", bad_work])
        res = json.load(open(os.path.join(bad_out, "result.json"), encoding="utf-8"))
        ok &= check("坏 JSON 容错", r.returncode == 0 and isinstance(res.get("errors"), list) and len(res["errors"]) >= 1)

        # 3. 最小合法案例 + 契约键齐全回显 + patch 合并 + 报告渲染。
        good_in = os.path.join(base, "good_in")
        good_out = os.path.join(base, "good_out")
        good_work = os.path.join(base, "good_work")
        os.makedirs(good_in)
        case = {
            "case_id": "test-case-1",
            "output_contract": {
                "deliverable": {"type": "object"},
                "sources": {"type": "array"},
                "change_log": {"type": "array"},
                "custom_field": {"type": "string"},
            },
            "task": "把材料改写成符合受众的稿件",
        }
        with open(os.path.join(good_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(case, ensure_ascii=False))
        with open(os.path.join(good_in, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task")
        with open(os.path.join(good_in, "material.txt"), "w", encoding="utf-8") as fh:
            fh.write("示例材料文本")
        r = run_main(["scaffold", "--input", good_in, "--output", good_out, "--work", good_work])
        res = json.load(open(os.path.join(good_out, "result.json"), encoding="utf-8"))
        ok &= check("case_id 解析（case_id 优先）", res.get("case_id") == "test-case-1")
        ok &= check("契约键齐全回显（含未知键 custom_field）",
                    all(k in res for k in ("deliverable", "sources", "change_log", "custom_field")))
        patch = {
            "status": "complete",
            "deliverable": {"title": "t"},
            "sources": [{"id": "S1", "quote": "x", "locator": "p1", "source_doc": "material.txt"}],
            "change_log": [],
        }
        with open(os.path.join(good_work, "result_patch.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(patch, ensure_ascii=False))
        r = run_main(["finalize", "--input", good_in, "--output", good_out, "--work", good_work,
                      "--merge", os.path.join(good_work, "result_patch.json")])
        res = json.load(open(os.path.join(good_out, "result.json"), encoding="utf-8"))
        ok &= check("patch 合并且保留契约键", res.get("status") == "complete"
                    and "custom_field" in res and res.get("deliverable", {}).get("title") == "t")
        ok &= check("evidence_report.md 生成", os.path.exists(os.path.join(good_out, "evidence_report.md")))

        # 4. 重复 scaffold 不覆盖已 finalize 结果。
        r = run_main(["scaffold", "--input", good_in, "--output", good_out, "--work", good_work])
        res_after = json.load(open(os.path.join(good_out, "result.json"), encoding="utf-8"))
        ok &= check("重复 scaffold 不覆盖 finalize 结果", res_after.get("status") == "complete")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
