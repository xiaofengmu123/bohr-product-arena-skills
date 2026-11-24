# -*- coding: utf-8 -*-
"""selftest.py — 端到端自检（纯标准库，无随机性）。

覆盖：
  1) 合成输入端到端：scaffold → 填写 review_model → update → selfcheck
  2) 空输入目录降级：不崩溃、status=insufficient_evidence
  3) 坏 JSON 降级：记录 errors/degradations、不崩溃
  4) 确定性：相同输入两次运行，产物（除 change_log 时间戳与 runtime 秒数外）完全一致

用法：python3 -B scripts/selftest.py
退出码：0=全部通过，1=存在失败。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

PY = sys.executable
SCRIPT_DIR = Path(__file__).resolve().parent
MAIN = str(SCRIPT_DIR / "main.py")

for _s in (sys.stdout, sys.stderr):
    try:
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

FAILURES: List[str] = []


def _run(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-B", MAIN] + args, cwd=str(cwd),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _check(cond: bool, msg: str) -> None:
    if not cond:
        FAILURES.append(msg)
        print("FAIL:", msg)
    else:
        print("PASS:", msg)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _strip_runtime(obj: Any) -> Any:
    """去掉 change_log 时间戳与 runtime 秒数，使确定性比较只比对内容。"""
    if isinstance(obj, dict):
        return {k: _strip_runtime(v) for k, v in obj.items()
                if k not in ("change_log", "runtime")}
    if isinstance(obj, list):
        return [_strip_runtime(x) for x in obj]
    return obj


def _synthetic_input(root: Path) -> Path:
    inp = root / "input"
    _write(inp / "case.json", json.dumps({
        "case_id": "demo-case-001",
        "challenge_id": "challenge-demo",
        "title": "演示：因果表述审查",
        "output_contract": {"custom_field": None, "extra_keys": []},
    }, ensure_ascii=False))
    _write(inp / "task.md", "# 论文逻辑与论证审查\n\n请审查材料中的因果表述。\n")
    _write(inp / "materials" / "paper.md",
           "# 摘要\n\n我们的治疗显著改善了预后。\n\n"
           "# 引言\n\n本研究承诺评估治疗与预后的因果关系。\n\n"
           "# 结果\n\n横断面调查显示治疗组预后更好（r=0.3）。\n\n"
           "# 讨论\n\n因此治疗改善了预后。\n\n")
    return inp


def test_end_to_end(root: Path) -> None:
    inp = _synthetic_input(root)
    out = root / "output"
    work = root / "work"

    r = _run(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)], root)
    _check(r.returncode == 0, "scaffold 退出码为 0")
    _check((out / "result.json").exists(), "scaffold 产出 result.json")
    _check((work / "corpus.md").exists(), "scaffold 产出 corpus.md")
    _check((work / "review_model.json").exists(), "scaffold 产出 review_model.json 中间表示")

    model = {
        "version": 1,
        "summary": "该文将相关关系表述为因果，需降级结论强度。",
        "coverage": {d: {"status": "done", "checked": 1, "note": ""}
                     for d in ("factual_verification", "citation_support", "method_design",
                               "statistical_reporting", "argument_chain", "academic_norms")},
        "claims": [{"id": "C001", "text": "治疗改善了预后", "speaker": "author",
                    "section": "讨论", "locator": "¶P0007", "verdict": "overclaimed",
                    "evidence": "结果章节仅给出横断面相关 r=0.3"}],
        "commitments": [{"id": "M001", "promise": "评估治疗与预后的因果关系",
                         "locator": "¶P0003", "delivered_at": "¶P0005",
                         "status": "partial"}],
        "findings": [{
            "id": "F001", "type": "causality_conflation", "severity": "major",
            "locator": "¶P0007", "quote": "因此治疗改善了预后。",
            "claim": "治疗改善预后为因果结论",
            "evidence": "结果章节仅有横断面相关（r=0.3），未控制混杂或做干预设计",
            "reasoning": "影响主结论：是；可修正：是；影响可复现：是",
            "suggestion": "将‘改善了预后’改为‘与更好的预后相关’，并注明需前瞻设计验证因果",
            "evidence_refs": ["P0007"],
        }],
        "uncertainty": [], "pending_review": ["r=0.3 未核对原始数据"],
        "pending_human": [], "limitations": ["仅审查文本层，未重算统计量"],
    }
    _write(work / "review_model.json", json.dumps(model, ensure_ascii=False))

    r = _run(["update", "--output", str(out), "--work", str(work)], root)
    _check(r.returncode == 0, "update 退出码为 0")
    result, _err = _load(out / "result.json")
    _check(result.get("case_id") == "demo-case-001", "case_id 回填正确")
    _check(result.get("custom_field") is None and result.get("extra_keys") == [],
           "output_contract 原样回填")
    _check(len(result["review"]["findings"]) == 1, "findings 合并进 result.json")
    _check(result["evidence_map"] and result["evidence_map"][0]["refs"],
           "evidence_map 由脚本确定性派生")
    _check((work / "plan.json").exists(), "plan.json 由 coverage 派生")
    _check((out / "report.md").exists(), "report.md 同步生成")

    r = _run(["selfcheck", "--output", str(out), "--work", str(work)], root)
    _check(r.returncode == 0, "selfcheck 通过")


def test_determinism(root: Path) -> None:
    d = root / "det"
    inp = _synthetic_input(d)
    out = d / "output"
    work = d / "work"
    for _ in range(2):
        _run(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)], d)
        _run(["update", "--output", str(out), "--work", str(work)], d)
        ra, _ = _load(out / "result.json")
    # 同一目录重跑一次，比较内容（去时间戳与 runtime）
    _run(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)], d)
    _run(["update", "--output", str(out), "--work", str(work)], d)
    rb, _ = _load(out / "result.json")
    _check(_strip_runtime(ra) == _strip_runtime(rb), "相同输入两次运行内容一致（无随机性）")


def test_empty_input(root: Path) -> None:
    inp = root / "empty_input"
    inp.mkdir(parents=True, exist_ok=True)
    out = root / "empty_out"
    work = root / "empty_work"
    r = _run(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)], root)
    _check(r.returncode == 0, "空输入 scaffold 退出码 0")
    result, _ = _load(out / "result.json")
    _check(result.get("status") == "insufficient_evidence", "空输入 status=insufficient_evidence")


def test_bad_json(root: Path) -> None:
    inp = root / "bad_input"
    _write(inp / "case.json", "{ not valid json ")
    _write(inp / "task.md", "# 任务\n")
    out = root / "bad_out"
    work = root / "bad_work"
    r = _run(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)], root)
    _check(r.returncode == 0, "坏 JSON scaffold 退出码 0")
    result, _ = _load(out / "result.json")
    _check(result.get("status") in ("partial", "input_error", "insufficient_evidence"),
           "坏 JSON 仍产出合法 status")
    _check(any("case.json" in (s.get("path") or "") for s in result.get("sources", [])),
           "坏 JSON 的 case.json 仍作为来源记录")


def _load(path: Path) -> tuple:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="paper-review-selftest-"))
    try:
        test_end_to_end(tmp)
        test_determinism(tmp)
        test_empty_input(tmp)
        test_bad_json(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("")
    if FAILURES:
        print("SELFTEST FAILED: %d 处失败" % len(FAILURES))
        return 1
    print("SELFTEST ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
