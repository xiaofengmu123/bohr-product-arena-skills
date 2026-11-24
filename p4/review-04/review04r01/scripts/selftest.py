# -*- coding: utf-8 -*-
"""selftest.py — 证据溯源论文审查 Skill 的自检脚本（纯标准库）。

覆盖场景：
  1. 空输入目录     → 仍写合法 result.json，status=insufficient_evidence，不崩溃
  2. 缺输入目录     → status=input_error，合法结果
  3. 坏 JSON case   → 记录 errors/degradations，继续处理其余材料
  4. 正常文本材料   → 切段、定位符、output_contract 回填、update 合并、selfcheck 通过
  5. 确定性         → 同一输入两次 scaffold 结果一致（忽略时间戳）

用法：python3 -B scripts/selftest.py
退出码：0 全部通过；1 存在失败。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import main
import review_core as rc


def _run_cli(argv):
    try:
        return main.run(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def _load_result(output_dir: Path):
    p = output_dir / "result.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _make_tree(root: Path) -> None:
    (root / "materials").mkdir(parents=True, exist_ok=True)


def _case_json(root: Path, contract=None, bad=False) -> None:
    payload = {
        "case_id": "case-xyz",
        "challenge_id": "challenge-16d4414d",
        "title": "示例任务（仅自检）",
    }
    if contract is not None:
        payload["output_contract"] = contract
    text = json.dumps(payload, ensure_ascii=False)
    if bad:
        text = text[:20] + "}{not-json"
    (root / "case.json").write_text(text, encoding="utf-8")


def _task_md(root: Path) -> None:
    (root / "task.md").write_text("# 示例任务标题（仅自检）\n\n这是题面，不是论文材料。\n", encoding="utf-8")


def _paper_txt(root: Path) -> None:
    body = (
        "摘要：本研究发现新算法显著优于基线，误差降低 90%。\n\n"
        "引言：本文承诺分析三种噪声条件下的鲁棒性。\n\n"
        "方法：采用 12 个样本的对照实验设计。\n\n"
        "结果：实验表明两种条件显著，第三种条件未报告。\n\n"
        "讨论：因此该算法在所有条件下均稳健。\n\n"
        "结论：综上，本方法具有普适性。\n"
    )
    (root / "materials" / "paper.txt").write_text(body, encoding="utf-8")


def _batch_json(work: Path) -> None:
    batch = {
        "summary": "自检批次总评",
        "findings": [
            {
                "id": "F001", "type": "overclaim", "severity": "major",
                "locator": "¶P0005", "quote": "因此该算法在所有条件下均稳健。",
                "claim": "方法在所有条件下稳健",
                "evidence": "结果章节仅报告两种条件，第三种条件未报告",
                "reasoning": "影响主结论：是；可修正：补充第三种条件结果或降级结论；影响可复现：是",
                "suggestion": "将讨论句改为“在已报告的两种条件下稳健”，并补充第三种条件结果",
                "status": "confirmed", "confidence": "high", "evidence_refs": ["E001"],
            }
        ],
        "claims": [
            {
                "id": "C001", "text": "新算法显著优于基线，误差降低 90%",
                "speaker": "author", "section": "abstract", "locator": "¶P0001",
                "verdict": "pending", "evidence": "未见显著性检验数值", "evidence_refs": ["E001"],
            }
        ],
        "evidence_map": {
            "items": [
                {
                    "id": "E001", "source": "/input/materials/paper.txt",
                    "locator": "¶P0005", "quote": "因此该算法在所有条件下均稳健。",
                    "kind": "direct_quote", "status": "verified",
                    "supports": ["F001", "C001"], "contradicts": [],
                }
            ],
        },
    }
    (work / "batch.json").write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")


def scenario_empty() -> str:
    root = Path(tempfile.mkdtemp(prefix="slt_empty_"))
    inp, out, work = root / "input", root / "output", root / "work"
    inp.mkdir(parents=True)
    _run_cli(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r = _load_result(out)
    if r is None:
        return "FAIL(empty): result.json 缺失"
    if r.get("status") != "insufficient_evidence":
        return "FAIL(empty): status=%s 期望 insufficient_evidence" % r.get("status")
    issues = rc.validate_result(r)
    if issues:
        return "FAIL(empty): 校验问题 %s" % issues[:3]
    shutil.rmtree(root, ignore_errors=True)
    return "PASS(empty)"


def scenario_missing_input() -> str:
    root = Path(tempfile.mkdtemp(prefix="slt_missing_"))
    inp = root / "nonexistent"
    out, work = root / "output", root / "work"
    _run_cli(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r = _load_result(out)
    if r is None:
        return "FAIL(missing): result.json 缺失"
    if r.get("status") != "input_error":
        return "FAIL(missing): status=%s 期望 input_error" % r.get("status")
    shutil.rmtree(root, ignore_errors=True)
    return "PASS(missing)"


def scenario_bad_json() -> str:
    root = Path(tempfile.mkdtemp(prefix="slt_badjson_"))
    inp, out, work = root / "input", root / "output", root / "work"
    _make_tree(inp)
    _case_json(inp, bad=True)
    _task_md(inp)
    _paper_txt(inp)
    _run_cli(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r = _load_result(out)
    if r is None:
        return "FAIL(badjson): result.json 缺失"
    if not (r.get("errors") or r.get("degradations")):
        return "FAIL(badjson): 未记录 errors/degradations"
    if len((r.get("review") or {}).get("section_map") or []) < 1:
        return "FAIL(badjson): 未抽取材料段落"
    if r.get("case_id") in (None, "", "unknown_case"):
        # case.json 坏掉，应降级到文件名 stem
        if r.get("case_id") != "case":
            return "FAIL(badjson): case_id 降级异常 %r" % r.get("case_id")
    shutil.rmtree(root, ignore_errors=True)
    return "PASS(badjson)"


def scenario_normal() -> str:
    root = Path(tempfile.mkdtemp(prefix="slt_normal_"))
    inp, out, work = root / "input", root / "output", root / "work"
    _make_tree(inp)
    _case_json(inp, contract={"review_items": [], "notes": None})
    _task_md(inp)
    _paper_txt(inp)
    rc1 = _run_cli(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
    r = _load_result(out)
    if r is None:
        return "FAIL(normal): result.json 缺失"
    # output_contract 回填
    if "review_items" not in r or "notes" not in r:
        return "FAIL(normal): output_contract 键未回填"
    if r.get("review_items") != []:
        return "FAIL(normal): output_contract 值被改动"
    sm = (r.get("review") or {}).get("section_map") or []
    if len(sm) < 3:
        return "FAIL(normal): section_map 过少 %d" % len(sm)
    # update 合并
    _batch_json(work)
    rc2 = _run_cli(["update", "--output", str(out), "--work", str(work),
                    "--review", str(work / "batch.json"), "--status", "partial"])
    r2 = _load_result(out)
    if r2 is None:
        return "FAIL(normal): update 后 result.json 缺失"
    findings = (r2.get("review") or {}).get("findings") or []
    evidence = ((r2.get("review") or {}).get("evidence_map") or {}).get("items") or []
    byf = ((r2.get("review") or {}).get("evidence_map") or {}).get("by_finding") or {}
    if len(findings) != 1 or len(evidence) != 1:
        return "FAIL(normal): 合并数量异常 f=%d e=%d" % (len(findings), len(evidence))
    if "F001" not in byf or "E001" not in byf.get("F001", []):
        return "FAIL(normal): by_finding 索引未重建"
    if r2.get("status") != "partial":
        return "FAIL(normal): status 未生效 %r" % r2.get("status")
    # selfcheck
    rc3 = _run_cli(["selfcheck", "--output", str(out)])
    r3 = _load_result(out)
    issues = rc.validate_result(r3)
    if issues:
        return "FAIL(normal): selfcheck 后仍有校验问题 %s" % issues[:3]
    if not (out / "report.md").exists():
        return "FAIL(normal): report.md 缺失"
    if rc1 != 0 or rc2 != 0 or rc3 != 0:
        return "FAIL(normal): 退出码异常 %s/%s/%s" % (rc1, rc2, rc3)
    shutil.rmtree(root, ignore_errors=True)
    return "PASS(normal)"


def scenario_determinism() -> str:
    root = Path(tempfile.mkdtemp(prefix="slt_det_"))
    inp = root / "input"
    _make_tree(inp)
    _case_json(inp, contract={"k": "v"})
    _task_md(inp)
    _paper_txt(inp)
    outs = []
    for i in (1, 2):
        out = root / ("out%d" % i)
        work = root / ("work%d" % i)
        _run_cli(["scaffold", "--input", str(inp), "--output", str(out), "--work", str(work)])
        r = _load_result(out)
        r["runtime"] = {}
        r["change_log"] = []
        outs.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
    shutil.rmtree(root, ignore_errors=True)
    if outs[0] != outs[1]:
        return "FAIL(determinism): 两次 scaffold 结果不一致"
    return "PASS(determinism)"


def main_selftest() -> int:
    rc._configure_stdio()
    print("== 证据溯源论文审查 Skill 自检 ==")
    results = [
        scenario_empty(),
        scenario_missing_input(),
        scenario_bad_json(),
        scenario_normal(),
        scenario_determinism(),
    ]
    failed = 0
    for r in results:
        print("  " + r)
        if not r.startswith("PASS"):
            failed += 1
    print("== 结果: %d/%d 通过 ==" % (len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_selftest())
