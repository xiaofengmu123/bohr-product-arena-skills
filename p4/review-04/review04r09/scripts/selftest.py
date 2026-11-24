# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：在临时目录构造最小输入，验证 scaffold/update/selfcheck 与异常降级。

不预置任何正式案例答案；样例论文为合成的自检夹具，仅用于验证脚本机制。
退出码：全部通过为 0，任一失败为 1。
用法：
  python3 -B scripts/selftest.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402  （仅复用常量，不执行 CLI）
main._reconfigure_streams()

PY = sys.executable
MAIN = str(Path(__file__).resolve().parent / "main.py")

SAMPLE_PAPER = """# 自检样例论文（合成夹具，非正式案例）

## 摘要

本文提出一种方法，在 n=10 的样本上观察到显著改善。

## 引言

引言承诺将检验该方法是否提升准确率与召回率两个指标。

## 方法

采用随机对照设计，设置实验组与对照组。

## 结果

实验组的准确率高于对照组（p<0.05）。

## 讨论

该方法已被证明能够普遍提升所有任务的表现。
"""


def run_main(*args: str, cwd=None):
    return subprocess.run(
        [PY, MAIN, *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=cwd)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fail(name: str, msg: str) -> bool:
    print("FAIL %s: %s" % (name, msg))
    return False


def test_empty_input(tmp: Path) -> bool:
    name = "empty-input"
    inp = tmp / name / "input"
    out = tmp / name / "output"
    work = tmp / name / "work"
    inp.mkdir(parents=True)
    r = run_main("scaffold", "--input", str(inp), "--output", str(out), "--work", str(work))
    if r.returncode != 0:
        return fail(name, "scaffold 退出码 %d: %s" % (r.returncode, r.stderr))
    data = read_json(str(out / "result.json"))
    if data.get("status") not in ("insufficient_evidence", "input_error"):
        return fail(name, "空输入应得 insufficient_evidence/input_error，实际 %r" % data.get("status"))
    if not data.get("case_id"):
        return fail(name, "case_id 为空")
    if not (out / "report.md").exists():
        return fail(name, "report.md 未生成")
    print("PASS %s (status=%s)" % (name, data.get("status")))
    return True


def test_bad_json(tmp: Path) -> bool:
    name = "bad-json"
    inp = tmp / name / "input"
    out = tmp / name / "output"
    work = tmp / name / "work"
    inp.mkdir(parents=True)
    (inp / "case.json").write_text("{ not valid json ]", encoding="utf-8")
    (inp / "materials").mkdir()
    (inp / "materials" / "paper.md").write_text(SAMPLE_PAPER, encoding="utf-8")
    r = run_main("scaffold", "--input", str(inp), "--output", str(out), "--work", str(work))
    if r.returncode != 0:
        return fail(name, "scaffold 退出码 %d（坏 JSON 不应崩溃）: %s" % (r.returncode, r.stderr))
    data = read_json(str(out / "result.json"))
    if not data.get("errors"):
        return fail(name, "坏 JSON 未记录到 errors")
    if data.get("status") != "partial":
        return fail(name, "有材料时应为 partial，实际 %r" % data.get("status"))
    print("PASS %s (errors=%d, paragraphs=%s)" % (name, len(data["errors"]),
          len((data.get("review") or {}).get("section_map") or [])))
    return True


def test_normal_flow(tmp: Path) -> bool:
    name = "normal-flow"
    inp = tmp / name / "input"
    out = tmp / name / "output"
    work = tmp / name / "work"
    inp.mkdir(parents=True)
    (inp / "materials").mkdir()
    (inp / "materials" / "paper.md").write_text(SAMPLE_PAPER, encoding="utf-8")
    case = {
        "id": "selfcase-001",
        "challenge_id": "challenge-self",
        "title": "自检样例任务",
        "output_contract": {"extra_field": "kept", "answer_summary": None},
    }
    (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text("# 自检样例任务\n\n请审查给定论文。\n", encoding="utf-8")

    r = run_main("scaffold", "--input", str(inp), "--output", str(out), "--work", str(work))
    if r.returncode != 0:
        return fail(name, "scaffold 退出码 %d: %s" % (r.returncode, r.stderr))
    data = read_json(str(out / "result.json"))
    if data.get("case_id") != "selfcase-001":
        return fail(name, "case_id 未从 case.json 回填：%r" % data.get("case_id"))
    if data.get("extra_field") != "kept":
        return fail(name, "output_contract 键未回填")
    if "answer_summary" not in data:
        return fail(name, "output_contract 的缺值键未保留（应为 null）")
    if not (work / "corpus.md").exists():
        return fail(name, "corpus.md 未生成")

    # 增量写回一批 findings / claims / coverage
    findings = [{
        "type": "overclaim", "severity": "major", "locator": "¶P0001",
        "quote": "该方法已被证明能够普遍提升所有任务的表现。",
        "claim": "方法普遍提升所有任务表现", "evidence": "仅 n=10 单任务样本",
        "reasoning": "影响主结论；可修正；可复现性受影响",
        "suggestion": "将“已被证明”改为“在本样本中初步提示”并补大样本验证。",
        "status": "confirmed", "confidence": "high",
    }]
    claims = [{
        "text": "本文提出一种方法", "speaker": "author", "locator": "¶P0001",
        "verdict": "supported", "evidence": "摘要声明",
    }]
    coverage = {k: {"status": "partial", "checked": 1, "note": "样例"} for k in main.COVERAGE_DIMS}
    (work / "findings.json").write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
    (work / "claims.json").write_text(json.dumps(claims, ensure_ascii=False), encoding="utf-8")
    (work / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False), encoding="utf-8")

    r = run_main("update", "--output", str(out), "--work", str(work),
                 "--findings", str(work / "findings.json"),
                 "--claims", str(work / "claims.json"),
                 "--coverage", str(work / "coverage.json"),
                 "--summary", "自检样例总评。", "--status", "partial")
    if r.returncode != 0:
        return fail(name, "update 退出码 %d: %s" % (r.returncode, r.stderr))
    data = read_json(str(out / "result.json"))
    if len(data["review"]["findings"]) != 1 or data["review"]["findings"][0]["id"] != "F001":
        return fail(name, "findings 未合并或 ID 未生成")
    if data["review"]["coverage"]["factual_verification"]["status"] != "partial":
        return fail(name, "coverage 未合并")

    r = run_main("selfcheck", "--output", str(out))
    if r.returncode != 0:
        return fail(name, "selfcheck 未通过：%s" % r.stdout)
    print("PASS %s (findings=%d, claims=%d)" % (name,
          len(data["review"]["findings"]), len(data["review"]["claims"])))
    return True


def test_determinism(tmp: Path) -> bool:
    name = "determinism"
    runs = []
    for i in (1, 2):
        inp = tmp / name / str(i) / "input"
        out = tmp / name / str(i) / "output"
        work = tmp / name / str(i) / "work"
        inp.mkdir(parents=True)
        (inp / "materials").mkdir()
        (inp / "materials" / "paper.md").write_text(SAMPLE_PAPER, encoding="utf-8")
        (inp / "case.json").write_text(
            json.dumps({"id": "det-1", "output_contract": {"x": 1}}, ensure_ascii=False),
            encoding="utf-8")
        r = run_main("scaffold", "--input", str(inp), "--output", str(out), "--work", str(work))
        if r.returncode != 0:
            return fail(name, "scaffold 退出码 %d" % r.returncode)
        runs.append(read_json(str(out / "result.json")))
    a, b = runs
    if sorted(a.keys()) != sorted(b.keys()):
        return fail(name, "两次运行 result.json 键集不一致")
    # 两次运行分处不同临时目录，绝对路径必然不同；比较相对输入目录的路径与顺序
    rel_a = sorted(Path(s["path"]).name for s in a["sources"])
    rel_b = sorted(Path(s["path"]).name for s in b["sources"])
    if rel_a != rel_b:
        return fail(name, "两次运行 sources 文件名集不一致：%r vs %r" % (rel_a, rel_b))
    if (a["review"]["section_map"] != b["review"]["section_map"]):
        return fail(name, "两次运行 section_map 不一致")
    print("PASS %s (sources=%d, paragraphs=%d)" % (name,
          len(a["sources"]), sum(len(s["paras"]) for s in a["review"]["section_map"])))
    return True


def main_selftest() -> int:
    print("selftest: python=%s" % PY)
    tmp = Path(tempfile.mkdtemp(prefix="paper-review-selftest-"))
    try:
        ok = True
        ok &= test_empty_input(tmp)
        ok &= test_bad_json(tmp)
        ok &= test_normal_flow(tmp)
        ok &= test_determinism(tmp)
        print("selftest result: %s" % ("ALL PASS" if ok else "SOME FAILED"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main_selftest())
