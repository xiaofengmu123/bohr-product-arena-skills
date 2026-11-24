#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 对 Skill 脚本做黑盒自检。

纯标准库。在临时目录中验证五类关键场景，全部通过返回 0，任一失败返回 1。
无需真实案例材料：只用最小构造输入，测试脚本契约本身。

用法：
    python3 -B scripts/selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BOOTSTRAP = os.path.join(HERE, "bootstrap.py")
VALIDATE = os.path.join(HERE, "validate.py")


def run_script(path, args, cwd):
    return subprocess.run(
        [sys.executable, "-B", path] + args,
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )


def write(path, content, mode="w", encoding="utf-8"):
    if "b" in mode:
        with open(path, mode) as fh:
            fh.write(content)
    else:
        with open(path, mode, encoding=encoding) as fh:
            fh.write(content)


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name +
          (("  | " + detail) if detail else ""))
    return bool(cond)


def main():
    results = []
    with tempfile.TemporaryDirectory() as root:
        # ---- 场景 1：空输入目录 ----
        d = os.path.join(root, "t1")
        os.makedirs(os.path.join(d, "input"))
        os.makedirs(os.path.join(d, "work"))
        os.makedirs(os.path.join(d, "output"))
        rc = run_script(BOOTSTRAP, [
            "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"),
        ], cwd=d).returncode
        r = load(os.path.join(d, "output", "result.json"))
        results.append(check(
            "empty_input: 退出码0且 status=insufficient_evidence",
            rc == 0 and r.get("status") == "insufficient_evidence"
            and r.get("case_id") is None,
            "status=%s rc=%d" % (r.get("status"), rc)))

        # ---- 场景 2：坏 JSON 的 case.json + 一个文本材料 ----
        d = os.path.join(root, "t2")
        os.makedirs(os.path.join(d, "input", "materials"))
        os.makedirs(os.path.join(d, "work"))
        os.makedirs(os.path.join(d, "output"))
        write(os.path.join(d, "input", "case.json"), "{ not valid json !!")
        write(os.path.join(d, "input", "materials", "paper.md"),
              "# Demo\nSome fact with a number 42.\n")
        rc = run_script(BOOTSTRAP, [
            "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"),
        ], cwd=d).returncode
        r = load(os.path.join(d, "output", "result.json"))
        results.append(check(
            "bad_json: 不崩溃、case_id=null、记录 errors",
            rc == 0 and r.get("case_id") is None
            and r.get("status") in ("partial", "insufficient_evidence")
            and any("parse" in str(e) or "解析" in str(e)
                    for e in r.get("errors", [])),
            "errors=%s" % r.get("errors")))

        # ---- 场景 3：合同字段原样回显 ----
        d = os.path.join(root, "t3")
        os.makedirs(os.path.join(d, "input"))
        os.makedirs(os.path.join(d, "work"))
        os.makedirs(os.path.join(d, "output"))
        case = {
            "case_id": "C-42",
            "output_contract": {
                "introduction": None,
                "related_work": {"by_theme": None, "refs": []},
                "contributions": [],
                "citation_style": "author-year",
            },
        }
        write(os.path.join(d, "input", "case.json"), json.dumps(case))
        rc = run_script(BOOTSTRAP, [
            "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"),
        ], cwd=d).returncode
        r = load(os.path.join(d, "output", "result.json"))
        results.append(check(
            "contract_echo: 顶层字段名原样、结构正确",
            rc == 0 and r.get("case_id") == "C-42"
            and r.get("introduction") is None
            and r.get("related_work") == {"by_theme": None, "refs": []}
            and r.get("contributions") == []
            and r.get("citation_style") == "author-year",
            "case_id=%s related_work=%s" % (r.get("case_id"),
                                            r.get("related_work"))))

        # ---- 场景 4：validate 自愈缺失字段 ----
        d = os.path.join(root, "t4")
        os.makedirs(os.path.join(d, "work"))
        os.makedirs(os.path.join(d, "output"))
        write(os.path.join(d, "work", "case_normalized.json"),
              json.dumps({"case_id": "C-9",
                          "contract": {"summary": None, "refs": []}}))
        write(os.path.join(d, "output", "result.json"),
              json.dumps({"case_id": "C-9", "status": "partial"}))
        rc = run_script(VALIDATE, [
            "--work", os.path.join(d, "work"),
            "--output", os.path.join(d, "output"),
        ], cwd=d).returncode
        r = load(os.path.join(d, "output", "result.json"))
        results.append(check(
            "self_heal: 补合同字段、补标准字段、保留原值",
            rc == 0 and r.get("summary") is None and r.get("refs") == []
            and r.get("case_id") == "C-9" and r.get("status") == "partial"
            and "sources" in r and "uncertainty" in r
            and "change_log" in r,
            "summary=%s refs=%s" % (r.get("summary"), r.get("refs"))))

        # ---- 场景 5：坏编码材料降级读取 ----
        d = os.path.join(root, "t5")
        os.makedirs(os.path.join(d, "input", "materials"))
        os.makedirs(os.path.join(d, "work"))
        os.makedirs(os.path.join(d, "output"))
        write(os.path.join(d, "input", "materials", "notes.md"),
              "résumé 91.5% caf\u00e9".encode("latin-1"),
              mode="wb")
        rc = run_script(BOOTSTRAP, [
            "--input", os.path.join(d, "input"),
            "--output", os.path.join(d, "output"),
            "--work", os.path.join(d, "work"),
        ], cwd=d).returncode
        r = load(os.path.join(d, "output", "result.json"))
        manifest = load(os.path.join(d, "work", "input_manifest.json"))
        results.append(check(
            "bad_encoding: 不崩溃、材料被识别",
            rc == 0 and r.get("status") == "partial"
            and len(manifest.get("files", [])) == 1,
            "files=%d status=%s" % (len(manifest.get("files", [])),
                                    r.get("status"))))

    passed = sum(results)
    total = len(results)
    print("selftest: %d/%d passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
