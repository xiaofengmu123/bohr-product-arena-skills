# -*- coding: utf-8 -*-
"""selftest.py — Skill 包自检（纯标准库，无需任何外部依赖）。

覆盖三类场景：
  1) 正常输入：case.json（含 output_contract）+ task.md + materials/*.md → partial 骨架
  2) 坏 JSON / 坏编码材料 → 不崩溃，记录 errors/degradations
  3) 空输入目录 → insufficient_evidence 且仍写合法 result.json
最后执行 selfcheck 确认产物合法。退出码 0 表示全部通过。

用法（在 SKILL.md 所在目录）：
  python3 -B scripts/selftest.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
MAIN = SCRIPTS_DIR / "main.py"


def run(args, cwd=None):
    return subprocess.run([sys.executable, "-B", str(MAIN)] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, timeout=300)


def check(name, cond, detail=""):
    print("%-42s %s%s" % (name, "OK" if cond else "FAIL", ("  " + detail) if detail and not cond else ""))
    return bool(cond)


def make_case(tmp: Path) -> Path:
    case = tmp / "case1"
    (case / "materials").mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({
        "id": "case-001",
        "challenge_id": "challenge-16d4414d",
        "title": "测试案例",
        "output_contract": {"report_fields": ["summary", "findings"], "extra_meta": None}
    }, ensure_ascii=False), encoding="utf-8")
    (case / "task.md").write_text("# 测试任务标题\n\n请审查给定论文的逻辑与证据。\n", encoding="utf-8")
    (case / "materials" / "paper.md").write_text(
        "# 引言\n\n本文提出方法 A，结果显示显著优于基线。\n\n## 方法\n\n对照组设置见下表。\n\n## 结果\n\np 值为 0.04。\n\n## 讨论\n\n因此方法 A 是因果有效的。\n",
        encoding="utf-8")
    return case


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="skill_selftest_"))
    ok = True

    # 场景 1：正常输入
    case = make_case(tmp)
    out = case / "out"
    work = case / "work"
    r = run(["scaffold", "--input", str(case), "--output", str(out), "--work", str(work)])
    ok &= check("scaffold 正常输入 rc=0", r.returncode == 0, r.stderr.strip())
    result_path = out / "result.json"
    ok &= check("result.json 存在", result_path.exists())
    if result_path.exists():
        data = json.loads(result_path.read_text(encoding="utf-8"))
        ok &= check("status=partial", data.get("status") == "partial")
        ok &= check("case_id 回填", data.get("case_id") == "case-001")
        ok &= check("output_contract 键回填", "report_fields" in data and "extra_meta" in data)
        ok &= check("六维 coverage 齐全", all(
            d in data.get("review", {}).get("coverage", {})
            for d in ("factual_verification", "citation_support", "method_design",
                      "statistical_reporting", "argumentation_chain", "academic_norms")))
        ok &= check("corpus.md 有定位符", "L1" in (work / "corpus.md").read_text(encoding="utf-8"))

    # 场景 2：坏 JSON / 坏编码
    case2 = tmp / "case2"
    (case2 / "materials").mkdir(parents=True)
    (case2 / "case.json").write_text("{ not valid json", encoding="utf-8")
    (case2 / "task.md").write_text("# 任务\n\n坏编码材料见 materials。\n", encoding="utf-8")
    (case2 / "materials" / "bad.txt").write_bytes(b"\xff\xfe\x00bad \xc3\x28 utf8")
    out2 = case2 / "out"
    work2 = case2 / "work"
    r = run(["scaffold", "--input", str(case2), "--output", str(out2), "--work", str(work2)])
    ok &= check("坏输入 scaffold rc=0", r.returncode == 0, r.stderr.strip())
    data2 = json.loads((out2 / "result.json").read_text(encoding="utf-8"))
    ok &= check("坏 JSON 后仍合法", data2.get("status") in ("partial", "insufficient_evidence", "input_error"))

    # 场景 3：空输入目录
    case3 = tmp / "case3"
    case3.mkdir()
    out3 = case3 / "out"
    work3 = case3 / "work"
    r = run(["scaffold", "--input", str(case3), "--output", str(out3), "--work", str(work3)])
    ok &= check("空输入 rc=0", r.returncode == 0, r.stderr.strip())
    data3 = json.loads((out3 / "result.json").read_text(encoding="utf-8"))
    ok &= check("空输入 status=insufficient_evidence", data3.get("status") == "insufficient_evidence")

    # 场景 4：update + selfcheck（在场景 1 产物上继续）
    findings = [{
        "id": "F001", "type": "overclaim", "severity": "major", "locator": "L10",
        "quote": "因此方法 A 是因果有效的。", "claim": "方法 A 因果有效",
        "evidence": "相关设计不足以推出因果结论", "reasoning": "影响主结论；可降级修正；影响可复现",
        "suggestion": "把因果表述降级为相关性", "status": "confirmed", "confidence": "high"
    }]
    claims = [{"id": "C001", "text": "本文提出方法 A", "speaker": "author",
               "section": "引言", "locator": "L2", "verdict": "supported", "evidence": "方法节有对应"}]
    f = work / "findings.json"
    c = work / "claims.json"
    f.write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
    c.write_text(json.dumps(claims, ensure_ascii=False), encoding="utf-8")
    r = run(["update", "--output", str(out), "--work", str(work),
             "--findings", str(f), "--claims", str(c), "--summary", "总评示例",
             "--status", "partial"])
    ok &= check("update rc=0", r.returncode == 0, r.stderr.strip())
    r = run(["selfcheck", "--output", str(out)])
    ok &= check("selfcheck rc=0", r.returncode == 0, r.stdout.strip() + r.stderr.strip())
    r = run(["budget", "--work", str(work), "--output", str(out)])
    ok &= check("budget rc=0", r.returncode == 0, r.stderr.strip())

    print("")
    print("selftest tmp dir: %s" % tmp)
    print("selftest: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
