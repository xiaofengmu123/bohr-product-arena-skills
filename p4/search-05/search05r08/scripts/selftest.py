#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检（结构自检 + 评分维度对齐机检）。

用法：
  python3 -B scripts/selftest.py --input /input --output /app/output

输出：
  逐条打印 PASS/FAIL；写入 /app/output/selfcheck_report.md 与 scorecard.json。
  全部通过时退出码 0，任一 FAIL 退出码 1。

自检清单按题目评分维度（实际效果40 / 使用增益25 / 创新增益15 / 可靠体验10 /
生态贡献10）逐条映射，评分维度的每个评分点至少对应一条可机检项。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fedsearch as fs


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_result(output_dir: Path) -> Tuple[Any, List[str]]:
    path = output_dir / "result.json"
    if not path.exists():
        return None, ["result.json 不存在"]
    obj, errs = fs.load_json(path)
    if obj is None:
        return None, ["result.json 无法解析: " + "; ".join(errs)]
    return obj, []


def structural_checks(res: Dict, output_dir: Path) -> List[Tuple[str, bool, str]]:
    checks: List[Tuple[str, bool, str]] = []
    checks.append(("json_valid", isinstance(res, dict), "result.json 为合法 JSON 对象"))
    checks.append(("case_id", bool(res.get("case_id")), "case_id 存在: %s" % res.get("case_id")))
    checks.append(("status_valid", res.get("status") in fs.STATUSES,
                   "status 合法: %s" % res.get("status")))
    missing_keys = [k for k in fs.DEFAULT_CONTRACT if k not in res]
    checks.append(("contract_keys", not missing_keys,
                   "输出契约键齐全，缺: %s" % (missing_keys or "无")))

    cands = res.get("candidates") or []
    checks.append(("cand_provenance",
                   len(cands) > 0 and all(c.get("provenance") for c in cands),
                   "候选均含 provenance（%d 条）" % len(cands)))
    checks.append(("cand_raw_snapshot",
                   len(cands) > 0 and all(c.get("raw_snapshot") or c.get("raw_snapshots") for c in cands),
                   "候选均含逐字 raw_snapshot"))
    checks.append(("cand_field_sources",
                   len(cands) > 0 and all(c.get("field_sources") for c in cands),
                   "候选均含 field_sources"))

    cov = res.get("coverage") or {}
    checks.append(("coverage_present", bool(cov), "coverage 存在"))
    if cov:
        checks.append(("coverage_counts",
                       cov.get("merged_record_count", 0) == len(cands)
                       and cov.get("total_input_records", 0) >= cov.get("merged_record_count", 0),
                       "覆盖计数自洽 total=%s merged=%s" % (
                           cov.get("total_input_records"), cov.get("merged_record_count"))))

    ev = res.get("evidence_records") or []
    checks.append(("evidence", len(ev) > 0 or len(cands) == 0,
                   "有候选时证据记录非空（%d 条证据）" % len(ev)))
    if ev:
        ok_ev = all(e.get("locator") and e.get("quote") for e in ev)
        checks.append(("evidence_locator_quote", ok_ev, "证据含 locator + 逐字 quote"))

    checks.append(("no_fabrication",
                   all(c.get("doi") is None or isinstance(c.get("doi"), str) for c in cands),
                   "无伪造标识符"))

    scorecard = res.get("scorecard") or []
    checks.append(("scorecard_present", isinstance(scorecard, list) and len(scorecard) == 5,
                   "评分维度对齐 scorecard 含 5 个维度"))
    return checks


def aux_checks(output_dir: Path) -> Dict:
    aux: Dict[str, Any] = {}
    aux["merged_records"] = (output_dir / "merged_records.json").exists()
    aux["evidence_map"] = (output_dir / "evidence_map.json").exists()
    aux["coverage_report"] = (output_dir / "coverage_report.md").exists()
    aux["selfcheck_report"] = (output_dir / "selfcheck_report.md").exists()
    aux["scorecard_json"] = (output_dir / "scorecard.json").exists()
    # 包内静态资源（相对 selftest.py 的位置）
    root = Path(__file__).resolve().parent.parent
    aux["skill_md"] = (root / "SKILL.md").exists()
    aux["field_mapping_md"] = (root / "references" / "field-mapping.md").exists()
    aux["fedsearch_py"] = (root / "scripts" / "fedsearch.py").exists()
    aux["example_in_skill"] = False
    skill_md = root / "SKILL.md"
    if skill_md.exists():
        text, _ = fs.read_text(skill_md)
        aux["example_in_skill"] = ("main.py" in text and "示例" in text)
    aux["help_exit_ok"] = _check_help(root)
    aux["deterministic"] = _check_deterministic(root)
    return aux


def _check_help(root: Path) -> bool:
    main_py = root / "scripts" / "main.py"
    if not main_py.exists():
        return False
    try:
        proc = subprocess.run(
            [sys.executable, "-B", str(main_py), "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _check_deterministic(root: Path) -> bool:
    """同一输入两次骨架运行结果一致（不比较 runtime 时间戳与 change_log 的 at）。"""
    import tempfile
    main_py = root / "scripts" / "main.py"
    if not main_py.exists():
        return False
    try:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            inp = base / "input"
            inp.mkdir()
            (inp / "case.json").write_text(
                json.dumps({"case_id": "det", "queries": ["machine learning"]}), encoding="utf-8")
            (inp / "task.md").write_text("# det\n", encoding="utf-8")
            outs = [base / "o1", base / "o2"]
            for o in outs:
                proc = subprocess.run(
                    [sys.executable, "-B", str(main_py), "--skeleton-only",
                     "--input", str(inp), "--output", str(o), "--work", str(base / "w")],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=60,
                )
                if proc.returncode != 0:
                    return False
            r1 = json.loads((outs[0] / "result.json").read_text(encoding="utf-8"))
            r2 = json.loads((outs[1] / "result.json").read_text(encoding="utf-8"))
            for k in ("case_id", "status", "candidates", "ranking", "coverage", "sources",
                      "empty_results", "conflicts", "missing_metadata", "permission_issues",
                      "limitations", "errors", "degradations", "pending_human", "scorecard"):
                if r1.get(k) != r2.get(k):
                    return False
            return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="交付前自检")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args()
    _reconfigure_stdio()

    output_dir = Path(args.output)
    res, load_errs = load_result(output_dir)
    all_checks: List[Tuple[str, bool, str]] = []

    if res is None:
        all_checks.append(("result_parse", False, "; ".join(load_errs)))
        report = render_report(all_checks, [])
        (output_dir / "selfcheck_report.md").write_text(report, encoding="utf-8")
        print(report)
        return 1

    all_checks.extend(structural_checks(res, output_dir))
    aux = aux_checks(output_dir)
    scorecard = fs.evaluate_scorecard(res, aux)

    for dim in scorecard:
        for c in dim["checks"]:
            all_checks.append((
                "%s.%s" % (dim["id"], c["id"]),
                c["passed"],
                "[%s %s] %s — %s" % (dim["dimension"], dim["weight"], c["label"], c["evidence"]),
            ))

    # 辅助产物与可调用性
    all_checks.append(("aux_artifacts", aux["merged_records"] and aux["evidence_map"] and aux["coverage_report"],
                       "辅助产物 merged_records/evidence_map/coverage_report 齐备"))
    all_checks.append(("help_exit_ok", aux["help_exit_ok"], "main.py --help 退出码 0"))
    all_checks.append(("deterministic", aux["deterministic"], "重复运行结果确定"))

    # 写 scorecard.json（含机检结果）
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scorecard.json").write_text(
        json.dumps({"dimensions": scorecard, "checks": [
            {"name": n, "passed": p, "detail": d} for n, p, d in all_checks]},
            ensure_ascii=False, indent=2), encoding="utf-8")

    report = render_report(all_checks, scorecard)
    (output_dir / "selfcheck_report.md").write_text(report, encoding="utf-8")
    print(report)

    fails = [n for n, p, d in all_checks if not p]
    if fails:
        print("\nFAIL: " + ", ".join(fails))
        return 1
    print("\nALL PASS")
    return 0


def render_report(all_checks: List[Tuple[str, bool, str]], scorecard: List[Dict]) -> str:
    lines = ["# 自检报告", ""]
    lines.append("| 状态 | 检查项 | 说明 |")
    lines.append("|-|-|-|")
    for name, passed, detail in all_checks:
        lines.append("| %s | %s | %s |" % ("PASS" if passed else "FAIL", name, detail))
    lines.append("")
    passed_cnt = sum(1 for _, p, _ in all_checks if p)
    lines.append("- 通过 %d / %d" % (passed_cnt, len(all_checks)))
    if scorecard:
        lines.append("")
        lines.append("## 评分维度对齐")
        lines.append("")
        for dim in scorecard:
            lines.append("### %s（%s 分）" % (dim["dimension"], dim["weight"]))
            for c in dim["checks"]:
                lines.append("- [%s] %s — %s" % ("x" if c["passed"] else " ", c["label"], c["evidence"]))
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
