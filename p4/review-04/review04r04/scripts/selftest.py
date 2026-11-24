# -*- coding: utf-8 -*-
"""selftest.py — Skill 自检脚本（纯标准库，确定性，不使用真实案例）。

用合成的最小输入验证三件事：
  1. 空输入目录       -> scaffold 产出合法 insufficient_evidence，进程不崩溃。
  2. 坏 JSON / 坏编码 -> 记录到 errors/degradations，继续处理其余材料，不崩溃。
  3. 正常小案例       -> scaffold -> update -> selfcheck 全链路 OK。

合成输入在运行时生成于临时目录，不预置、不冒充正式案例答案。
退出码 0 表示全部通过，非 0 表示存在失败项。

用法：
  python3 -B scripts/selftest.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import main as main_mod  # noqa: E402


def _run_mode(mode: str, **kwargs: Any) -> Tuple[int, Dict[str, Any]]:
    class _Args:
        pass

    args = _Args()
    args.mode = mode
    for key, val in kwargs.items():
        setattr(args, key, val)
    # 未提供参数时给缺省
    defaults = {"input": "/input", "output": "/app/output", "work": "/app/work",
                "force": False, "findings": None, "claims": None, "commitments": None,
                "uncertainty": None, "coverage": None, "summary": None, "status": None,
                "quiet": False}
    for key, val in defaults.items():
        if not hasattr(args, key):
            setattr(args, key, val)

    if mode == "scaffold":
        main_mod.cmd_scaffold(args)
        return 0, {}
    if mode == "update":
        main_mod.cmd_update(args)
        return 0, {}
    if mode == "selfcheck":
        code = main_mod.cmd_selfcheck(args)
        return code, {}
    raise ValueError("unknown mode: %s" % mode)


def _read_result(output_dir: Path) -> Dict[str, Any]:
    with open(output_dir / "result.json", "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 用例 1：空输入目录
# ---------------------------------------------------------------------------
def case_empty_input() -> Tuple[str, List[str]]:
    name = "empty-input"
    issues: List[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, out, work = root / "input", root / "output", root / "work"
        inp.mkdir(parents=True, exist_ok=True)
        _run_mode("scaffold", input=str(inp), output=str(out), work=str(work))
        result = _read_result(out)
        if result.get("status") != "insufficient_evidence":
            issues.append("空输入应返回 insufficient_evidence，实际 %s" % result.get("status"))
        if not result.get("limitations"):
            issues.append("insufficient_evidence 时应给出 limitations")
        code, _ = _run_mode("selfcheck", output=str(out), quiet=True)
        if code != 0:
            issues.append("空输入 selfcheck 未通过")
    return name, issues


# ---------------------------------------------------------------------------
# 用例 2：坏 JSON + 坏编码材料
# ---------------------------------------------------------------------------
def case_bad_inputs() -> Tuple[str, List[str]]:
    name = "bad-inputs"
    issues: List[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, out, work = root / "input", root / "output", root / "work"
        (inp / "materials").mkdir(parents=True, exist_ok=True)
        _write(inp / "case.json", "{ this is not valid json ")
        _write(inp / "task.md", "# 坏编码测试\n\n## 任务\n\n审阅材料。\n")
        # 坏编码：utf-8 无法解码的字节
        (inp / "materials" / "bad.txt").write_bytes(b"\xff\xfe\x00\x41\x00\x42 bad bytes")
        _write(inp / "materials" / "good.md", "# 正常材料\n\n本文提出一个方法。\n\n## 结论\n\n该方法有效。\n")

        _run_mode("scaffold", input=str(inp), output=str(out), work=str(work))
        result = _read_result(out)
        if not result.get("errors"):
            issues.append("坏 JSON 应写入 errors")
        if not result.get("degradations"):
            issues.append("坏编码/解析失败应写入 degradations")
        # 正常材料仍应被抽取（不因个别坏文件崩溃）
        paragraphs = 0
        with open(work / "corpus.md", "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
            paragraphs = text.count("### ")
        if paragraphs < 2:
            issues.append("正常材料应仍被抽取成段落，实际段落标记 %d" % paragraphs)
        code, _ = _run_mode("selfcheck", output=str(out), quiet=True)
        if code != 0:
            issues.append("坏输入场景 selfcheck 未通过")
    return name, issues


# ---------------------------------------------------------------------------
# 用例 3：正常小案例（合成，标注为示例）
# ---------------------------------------------------------------------------
def case_normal_flow() -> Tuple[str, List[str]]:
    name = "normal-flow"
    issues: List[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, out, work = root / "input", root / "output", root / "work"
        (inp / "materials").mkdir(parents=True, exist_ok=True)

        case = {
            "id": "demo-case-01",
            "title": "合成自检案例（非正式案例）",
            "challenge_id": "demo",
            "output_contract": {"review": None, "report": None},
        }
        _write(inp / "case.json", json.dumps(case, ensure_ascii=False))
        _write(inp / "task.md", "# 论文逻辑与论证审查（自检）\n\n## 任务\n\n审查材料。\n")
        _write(inp / "materials" / "paper.md",
               "# 合成论文（自检用，非正式案例）\n\n## 摘要\n\n本文提出一种新方法，显著优于现有方法。\n\n"
               "## 引言\n\n现有方法存在局限。本文拟解决该问题。\n\n"
               "## 方法\n\n采用标准流程。\n\n## 结果\n\n结果见表。\n\n## 讨论\n\n结果表明新方法有效。\n")

        _run_mode("scaffold", input=str(inp), output=str(out), work=str(work))
        result = _read_result(out)
        if result.get("case_id") != "demo-case-01":
            issues.append("case_id 应来自 case.json，实际 %s" % result.get("case_id"))
        if result.get("status") != "partial":
            issues.append("有材料时应先 partial，实际 %s" % result.get("status"))
        # output_contract 回填：review/report 键不得缺失
        for key in ("review", "report"):
            if key not in result:
                issues.append("output_contract 键 %s 未回填" % key)

        # update：写入 findings / claims / uncertainty，再自检
        findings = [{
            "id": "F001", "type": "overclaim", "severity": "major",
            "locator": "\u00b6P0002", "quote": "显著优于现有方法",
            "claim": "新方法显著优于现有方法",
            "evidence": "摘要未见与现有方法的定量对比数据",
            "reasoning": "影响主结论强度；可修正（补对比数据或降强度）；影响可复现（缺对照口径）",
            "suggestion": "将'显著优于'改为'初步表明优于'，或在结果节补充对照统计量",
            "status": "probable", "confidence": "high", "speaker": "author",
        }]
        claims = [{
            "id": "C001", "text": "新方法显著优于现有方法", "speaker": "author",
            "section": "摘要", "locator": "\u00b6P0002", "verdict": "overclaimed",
            "evidence": "无定量对比",
        }]
        uncertainty = [{
            "id": "U001", "scope": "coverage.factual_verification", "kind": "missing_material",
            "statement": "方法细节不足，无法核验实验步骤",
            "reason": "合成材料未提供实验细节", "impact": "事实核验维度降为 partial",
            "resolution": "补充方法节细节后复核",
        }]
        coverage = {
            "factual_verification": {"status": "partial", "checked": 1,
                                     "note": "受 U001 限制", "reason": "U001"},
            "citation_support": {"status": "done", "checked": 0, "note": "无引用"},
            "method_design": {"status": "done", "checked": 0, "note": ""},
            "statistical_reporting": {"status": "done", "checked": 0, "note": ""},
            "argument_chain": {"status": "done", "checked": 1, "note": ""},
            "academic_norms": {"status": "done", "checked": 0, "note": ""},
        }
        for name_f, data in (("findings.json", findings), ("claims.json", claims),
                             ("uncertainty.json", uncertainty)):
            _write(work / name_f, json.dumps(data, ensure_ascii=False))
        _write(work / "coverage.json", json.dumps(coverage, ensure_ascii=False))

        _run_mode("update", output=str(out), work=str(work),
                  findings=str(work / "findings.json"), claims=str(work / "claims.json"),
                  uncertainty=str(work / "uncertainty.json"),
                  coverage=str(work / "coverage.json"), summary="合成自检：一条过度结论待降级。",
                  status="partial")
        code, _ = _run_mode("selfcheck", output=str(out), quiet=True)
        if code != 0:
            issues.append("正常流程 selfcheck 未通过")
        result2 = _read_result(out)
        if len(result2.get("review", {}).get("findings", [])) != 1:
            issues.append("findings 应写入 1 条")
        if len(result2.get("uncertainty", [])) != 1:
            issues.append("uncertainty 应写入 1 条")

    return name, issues


def run_selftest() -> int:
    cases = [case_empty_input, case_bad_inputs, case_normal_flow]
    all_ok = True
    for case_fn in cases:
        name, issues = case_fn()
        if issues:
            all_ok = False
            print("FAIL [%s]" % name)
            for issue in issues:
                print("  - %s" % issue)
        else:
            print("PASS [%s]" % name)

    print("selftest %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run_selftest())
