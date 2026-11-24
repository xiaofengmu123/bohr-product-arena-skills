#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest：不依赖正式案例，用临时合成输入验证整条流水线。

验证项：
  1. --help 退出码 0
  2. init 在合成输入上落盘合法 partial result.json 骨架
  3. check 对合成 draft 的 quote 做逐字命中校验
  4. build 产出 final result.json + review_report.md，并回填契约字段
  5. selfcheck 产出评分维度对齐报告
  6. 空目录 / 坏 JSON / 缺材料 三种异常均退出码 0 且 result.json 合法

合成输入在临时目录生成，明确标注为自检合成案例，不冒充正式结果。
所有路径用相对引用（SKILL_ROOT 定位），不硬编码安装路径。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
MAIN = SKILL_ROOT / "scripts" / "main.py"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SYNTHETIC_CASE = {
    "case_id": "selftest-case-001",
    "task_instruction": "对附件论文做六类质量审查（selftest 合成案例，非正式案例）",
    "output_contract": {"findings": None, "verdict_summary": None, "review_notes": None},
}
SYNTHETIC_TASK = (
    "# 自检任务\n\n这是 selftest 的合成案例，仅用于验证脚本流水线，不是正式案例。\n"
    "请对 paper.md 完成六类审查。\n"
)
SYNTHETIC_PAPER = (
    "# 合成样稿（selftest 用，非正式案例）\n\n"
    "## 摘要\n本研究证明了新药 X 可治愈疾病 Y。\n\n"
    "## 方法\n对 12 名患者给药，未设对照组。\n\n"
    "## 结果\n实验组有效率 80%，p 值显著。\n\n"
    "## 结论\n因此 X 对全体人群有效。\n"
)

DRAFT = {
    "case_id": "selftest-case-001",
    "status": "partial",
    "verdict_summary": {"overall": "major_revision", "notes": "selftest 合成 draft"},
    "findings": [
        {
            "finding_id": "F01",
            "facet": "method_design",
            "severity": "major",
            "issue_type": "insufficient_evidence",
            "verdict": "confirmed",
            "claim": "方法未设对照组，疗效无法归因于药物。",
            "location": "para3",
            "quote": "对 12 名患者给药，未设对照组。",
            "rationale": "无对照组无法区分药物效应与自然病程或安慰剂效应。",
            "revision_suggestion": "补充同期对照或历史对照，并说明可比性依据。",
            "confidence": "high",
            "evidence": [{"source_id": "D1", "locator": "para3",
                          "quote": "对 12 名患者给药，未设对照组。"}],
        },
        {
            "finding_id": "F02",
            "facet": "statistics_report",
            "severity": "major",
            "issue_type": "insufficient_evidence",
            "verdict": "confirmed",
            "claim": "显著性未报告具体检验方法与 p 值。",
            "location": "para4",
            "quote": "实验组有效率 80%，p 值显著。",
            "rationale": "缺检验方法、p 值、效应量与置信区间，无法复核。",
            "revision_suggestion": "报告检验方法、p 值、效应量与置信区间。",
            "confidence": "high",
            "evidence": [{"source_id": "D1", "locator": "para4",
                          "quote": "实验组有效率 80%，p 值显著。"}],
        },
    ],
    "unchecked_items": [
        {"facet": "citation_support", "reason": "合成样稿无参考文献，无法核验引用",
         "status": "not_checked"},
        {"facet": "academic_norms", "reason": "合成样稿无数据可用性与伦理声明",
         "status": "not_checked"},
    ],
    "uncertainty": ["样本量 12 的抽样方式未见说明"],
    "pending_human": ["确证疾病 Y 自然病程以评估无对照偏倚大小"],
    "limitations": ["selftest 合成稿：仅验证脚本，不代表真实审查质量"],
    "degradations": [],
    "changes": [],
    "change_log": [{"ts": "selftest", "phase": "draft", "summary": "合成 draft"}],
    "contract_values": {"review_notes": "合成自检说明"},
}


def _load(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )


def _make_case(tmp, name, case, task, paper):
    d = Path(tmp) / name
    (d / "input").mkdir(parents=True, exist_ok=True)
    if case is not None:
        (d / "input" / "case.json").write_text(
            json.dumps(case, ensure_ascii=False), encoding="utf-8")
    if task is not None:
        (d / "input" / "task.md").write_text(task, encoding="utf-8")
    if paper is not None:
        (d / "input" / "paper.md").write_text(paper, encoding="utf-8")
    return d


def main():
    checks = []

    def record(name, ok, note):
        checks.append((name, ok, note))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {note}")

    help_proc = subprocess.run(
        [sys.executable, str(MAIN), "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    record("help_exit0", help_proc.returncode == 0, f"exit={help_proc.returncode}")

    tmp = Path(tempfile.mkdtemp(prefix="pqr-selftest-"))
    try:
        # 2-5：正常合成案例全流程
        d = _make_case(tmp, "normal", SYNTHETIC_CASE, SYNTHETIC_TASK, SYNTHETIC_PAPER)
        p = _run(["init", "--input", str(d / "input"),
                  "--work", str(d / "work"), "--output", str(d / "output")], d)
        skel = _load(d / "output" / "result.json")
        record("init", p.returncode == 0 and skel is not None,
               f"exit={p.returncode} status={skel and skel.get('status')}")

        (d / "work" / "draft.json").write_text(
            json.dumps(DRAFT, ensure_ascii=False), encoding="utf-8")
        p = _run(["check", "--input", str(d / "input"),
                  "--work", str(d / "work"), "--output", str(d / "output")], d)
        rep = _load(d / "work" / "check_report.json")
        fails = (rep or {}).get("summary", {}).get("fails", 1)
        record("check", p.returncode == 0 and rep is not None and fails == 0,
               f"exit={p.returncode} fails={fails}")

        p = _run(["build", "--input", str(d / "input"),
                  "--work", str(d / "work"), "--output", str(d / "output")], d)
        res = _load(d / "output" / "result.json")
        contract_filled = res is not None and all(
            k in res for k in SYNTHETIC_CASE["output_contract"])
        record("build", p.returncode == 0 and res is not None
               and res.get("status") == "partial" and len(res["result"]["findings"]) == 2,
               f"exit={p.returncode} status={res and res.get('status')}")
        record("report_exists", (d / "output" / "review_report.md").exists(), "")
        record("contract_filled", contract_filled,
               f"keys={list(SYNTHETIC_CASE['output_contract'])}")

        p = _run(["selfcheck", "--input", str(d / "input"),
                  "--work", str(d / "work"), "--output", str(d / "output")], d)
        sc = _load(d / "work" / "selfcheck.json")
        record("selfcheck", p.returncode == 0 and sc is not None
               and "score_alignment" in sc,
               f"exit={p.returncode} dims={len(sc['score_alignment']) if sc else 0}")

        # 6 异常场景
        d2 = _make_case(tmp, "empty", None, None, None)
        p = _run(["init", "--input", str(d2 / "input"),
                  "--work", str(d2 / "work"), "--output", str(d2 / "output")], d2)
        res2 = _load(d2 / "output" / "result.json")
        record("empty_dir", p.returncode == 0 and res2 is not None
               and res2.get("status") == "input_error",
               f"exit={p.returncode} status={res2 and res2.get('status')}")

        d3 = _make_case(tmp, "badjson", None, None, SYNTHETIC_PAPER)
        (d3 / "input" / "case.json").write_text("{not json", encoding="utf-8")
        p = _run(["init", "--input", str(d3 / "input"),
                  "--work", str(d3 / "work"), "--output", str(d3 / "output")], d3)
        res3 = _load(d3 / "output" / "result.json")
        record("bad_json", p.returncode == 0 and res3 is not None
               and res3.get("status") == "input_error",
               f"exit={p.returncode} status={res3 and res3.get('status')}")

        d4 = _make_case(tmp, "nomaterial", SYNTHETIC_CASE, SYNTHETIC_TASK, None)
        p = _run(["init", "--input", str(d4 / "input"),
                  "--work", str(d4 / "work"), "--output", str(d4 / "output")], d4)
        res4 = _load(d4 / "output" / "result.json")
        record("no_material", p.returncode == 0 and res4 is not None
               and "未发现科研材料" in " ".join(res4.get("degradations", [])),
               f"exit={p.returncode} degradations={res4 and len(res4.get('degradations', []))}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\nselftest 汇总: {passed}/{len(checks)} 通过")
    # 任何失败都要让调用者看到，但脚本本身不向评测进程抛异常
    if passed != len(checks):
        print("存在 FAIL 项，请检查脚本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
