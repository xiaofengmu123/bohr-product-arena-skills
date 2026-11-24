#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检（仅标准库）：在临时目录里跑完整 init/show/check/build 流程。

验证项：
  1. `--help` 退出码 0；
  2. 空输入目录 -> insufficient_evidence + 合法 result.json，退出码 0；
  3. 坏 JSON case.json -> input_error + 合法 result.json，退出码 0；
  4. 正常样稿 -> check 无 FAIL、build 产出合法 result.json 与 review_report.md；
  5. 确定性：同一输入跑两遍，剥离 runtime 后 result.json 字节一致；
  6. 包内无 __pycache__/.pyc；
  7. SKILL.md 引用的相对路径全部存在。

退出码：全部通过为 0，任一失败为 1（仅自检脚本如此；main.py 恒退出 0）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
MAIN = SCRIPTS / "main.py"
SKILL_ROOT = SCRIPTS.parent

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def run(*argv, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", str(MAIN)] + list(argv),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd)


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def make_demo(root):
    inp = root / "input"
    inp.mkdir(parents=True)
    (inp / "case.json").write_text(json.dumps({
        "case_id": "demo-001",
        "task_instruction": "对样稿做六类质量审查",
        "output_contract": {"findings": "list", "summary": "string"},
    }, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text(
        "# 论文质量审查（合成示例，仅用于自检）\n", encoding="utf-8")
    (inp / "paper.md").write_text(
        "结论段：本研究表明 A 与 B 显著相关，p 值为 0.03。\n\n"
        "方法段：我们招募了 20 名参与者，未设置对照组。\n\n"
        "结果段：平均误差为 2.1。\n", encoding="utf-8")
    return inp


def make_draft(work):
    draft = {
        "case_id": "demo-001",
        "status": "partial",
        "findings": [{
            "finding_id": "F01",
            "facet": "method_design",
            "severity": "major",
            "issue_type": "insufficient_evidence",
            "verdict": "confirmed",
            "claim": "样本量说明与对照设置缺失",
            "location": "para2",
            "quote": "我们招募了 20 名参与者，未设置对照组。",
            "rationale": "无对照组，且未说明样本量计算依据。",
            "revision_suggestion": "补充对照设置与样本量依据。",
            "confidence": "high",
            "evidence": [{
                "source_id": "D1", "locator": "para2",
                "quote": "我们招募了 20 名参与者，未设置对照组。",
            }],
        }],
        "unchecked_items": [
            {"facet": "citation_support", "reason": "合成样稿无参考文献"},
            {"facet": "statistics_report", "reason": "合成样稿统计报告不全"},
            {"facet": "argument_chain", "reason": "合成样稿未检查"},
            {"facet": "academic_norms", "reason": "合成样稿未检查"},
        ],
        "verified_facets": ["fact_check"],
        "uncertainty": ["合成样例，仅验证接口与确定性"],
        "pending_human": ["待人工复核"],
        "changes": [{"step": "draft", "field": "findings", "action": "add F01"}],
        "change_log": "合成样例草稿",
    }
    (work / "draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")


def canonical_bytes(obj):
    obj = json.loads(json.dumps(obj, ensure_ascii=False, sort_keys=False))
    obj.pop("runtime", None)
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def full_run(inp, work, out):
    for d in (work, out):
        d.mkdir(parents=True, exist_ok=True)
    r1 = run("init", "--input", str(inp), "--work", str(work),
             "--output", str(out))
    make_draft(work)
    r2 = run("check", "--input", str(inp), "--work", str(work),
             "--output", str(out))
    r3 = run("build", "--input", str(inp), "--work", str(work),
             "--output", str(out))
    return r1, r2, r3


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pqr_selftest_"))
    try:
        # 1. --help
        h = run("--help")
        record("--help 退出码 0", h.returncode == 0, "rc=%d" % h.returncode)

        # 2. 空输入目录
        empty = tmp / "empty"
        (empty / "input").mkdir(parents=True)
        w2, o2 = tmp / "w2", tmp / "o2"
        r = run("init", "--input", str(empty / "input"), "--work", str(w2),
                "--output", str(o2))
        ok = r.returncode == 0
        status = None
        if ok and (o2 / "result.json").exists():
            status = load(o2 / "result.json").get("status")
            ok = ok and status == "insufficient_evidence"
        record("空输入 -> insufficient_evidence 且退出码 0", ok,
               "rc=%d status=%s" % (r.returncode, status))

        # 3. 坏 JSON
        bad = tmp / "bad"
        (bad / "input").mkdir(parents=True)
        (bad / "input" / "case.json").write_text("{not json", encoding="utf-8")
        w3, o3 = tmp / "w3", tmp / "o3"
        r = run("init", "--input", str(bad / "input"), "--work", str(w3),
                "--output", str(o3))
        ok = r.returncode == 0
        status = None
        if ok and (o3 / "result.json").exists():
            status = load(o3 / "result.json").get("status")
            ok = ok and status == "input_error"
        record("坏 JSON -> input_error 且退出码 0", ok,
               "rc=%d status=%s" % (r.returncode, status))

        # 4. 正常样稿
        inp = make_demo(tmp / "demo")
        w4, o4 = tmp / "w4", tmp / "o4"
        r1, r2, r3 = full_run(inp, w4, o4)
        check_ok = (w4 / "check_report.json").exists() and \
            load(w4 / "check_report.json").get("ok") is True
        res = load(o4 / "result.json")
        shape_ok = (res.get("case_id") == "demo-001"
                    and isinstance(res.get("result", {}).get("findings"), list)
                    and len(res["result"]["findings"]) == 1
                    and res.get("findings") == res["result"]["findings"]
                    and isinstance(res.get("summary"), str)
                    and "overall=issues" in res["summary"]
                    and (o4 / "review_report.md").exists())
        record("正常样稿 check 无 FAIL", check_ok,
               "rc=%d/%d/%d" % (r1.returncode, r2.returncode, r3.returncode))
        record("正常样稿 result.json 契约完整", shape_ok)

        # 5. 确定性：同输入跑两遍（第二次用副本）
        copy = tmp / "demo2"
        shutil.copytree(inp, copy / "input")
        w5, o5 = tmp / "w5", tmp / "o5"
        full_run(copy / "input", w5, o5)
        b1 = canonical_bytes(res)
        b2 = canonical_bytes(load(o5 / "result.json"))
        record("确定性：两次运行剥离 runtime 后字节一致", b1 == b2,
               "len1=%d len2=%d" % (len(b1), len(b2)))

        # 6. 无 __pycache__/.pyc
        bad_artifacts = list(SKILL_ROOT.rglob("__pycache__")) + \
            list(SKILL_ROOT.rglob("*.pyc"))
        record("包内无 __pycache__/.pyc", not bad_artifacts,
               str(bad_artifacts))

        # 7. SKILL.md 引用路径存在
        skill = SKILL_ROOT / "SKILL.md"
        if skill.exists():
            text = skill.read_text(encoding="utf-8")
            refs = set(re.findall(r"(?:scripts|references)/[A-Za-z0-9_\-./]+",
                                  text))
            missing = [r for r in refs if not (SKILL_ROOT / r).exists()]
            record("SKILL.md 引用路径全部存在", not missing, str(missing))
        else:
            record("SKILL.md 存在", False, "missing")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print("\n自检总结：%d 通过 / %d 失败" % (
        len(RESULTS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
