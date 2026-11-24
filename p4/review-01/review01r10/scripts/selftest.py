#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检：空输入 / 坏 JSON / 正常文本 / --help 四场景。任何 FAIL 则退出码 1。

用法（在 Skill 根目录）：
  python3 -B scripts/selftest.py
该脚本只做离线自检，不写正式测试产物；失败即退出码 1 提醒作者修复。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_core as ac  # noqa: E402

MAIN = Path(__file__).resolve().parent / "main.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, "-B", str(MAIN), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def _load(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tmp():
    base = Path(tempfile.mkdtemp(prefix="paper_audit_selftest_"))
    for d in ("input", "work", "output"):
        (base / d).mkdir(parents=True, exist_ok=True)
    return base


def _args(base):
    return ["--input", str(base / "input"), "--work", str(base / "work"),
            "--output", str(base / "output")]


def scenario_help():
    return _run("--help").returncode == 0


def scenario_empty():
    base = _tmp()
    r = _run("scan", *_args(base))
    res = _load(base / "output" / "result.json")
    return (r.returncode == 0 and res is not None
            and res["status"] in ("insufficient_evidence", "partial")
            and all(f in res["result"]["coverage"] for f in ac.FACETS))


def scenario_bad_json():
    base = _tmp()
    (base / "input" / "case.json").write_text("{ not valid json", encoding="utf-8")
    (base / "input" / "material.txt").write_text(
        "第一段内容。\n\n第二段内容。", encoding="utf-8")
    r = _run("scan", *_args(base))
    res = _load(base / "output" / "result.json")
    idx = _load(base / "work" / "index.json")
    return (r.returncode == 0 and res is not None and idx is not None
            and len(idx.get("errors", [])) > 0 and len(res.get("errors", [])) > 0
            and len(idx.get("sources", [])) == 1)


def scenario_normal():
    base = _tmp()
    (base / "input" / "case.json").write_text(
        json.dumps({"case_id": "T01", "output_contract": {"summary": "x", "issues": []}},
                   ensure_ascii=False), encoding="utf-8")
    (base / "input" / "paper.md").write_text(
        "摘要：本文提出一种新方法。\n\n方法：样本量为 30。\n\n结果：p 值为 0.03。",
        encoding="utf-8")
    _run("scan", *_args(base))
    draft = {
        "status": "ok",
        "findings": [{
            "finding_id": "F01", "facet": "statistics_report", "severity": "major",
            "issue_type": "standards_issue", "verdict": "confirmed",
            "claim": "统计报告缺少置信区间与效应量",
            "location": "p1#para3", "quote": "结果：p 值为 0.03。",
            "rationale": "仅报告 p 值，未报告置信区间/效应量/多重比较校正。",
            "revision_suggestion": "补充置信区间、效应量及校正后的显著性说明。",
            "confidence": "high", "evidence": [],
        }],
        "unchecked_items": [{"facet": "citation_support", "reason": "材料无参考文献，无法核验"}],
        "not_applicable": [],
        "uncertainty": [], "pending_human": [],
    }
    (base / "work" / "draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    r = _run("build", *_args(base))
    res = _load(base / "output" / "result.json")
    report_ok = (base / "output" / "review_report.md").exists()
    f = res["result"]["findings"][0]
    return (r.returncode == 0 and res is not None and res["status"] == "ok"
            and res["case_id"] == "T01" and "summary" in res and "issues" in res
            and f.get("quote_verified") is True and f.get("quote_hash")
            and res["result"]["coverage"]["statistics_report"] == "checked"
            and res["result"]["coverage"]["citation_support"] == "unchecked"
            and report_ok)


def main():
    cases = [
        ("help", scenario_help),
        ("empty", scenario_empty),
        ("bad_json", scenario_bad_json),
        ("normal", scenario_normal),
    ]
    failures = []
    for name, fn in cases:
        try:
            ok = bool(fn())
            print(("PASS" if ok else "FAIL"), name)
            if not ok:
                failures.append(name)
        except Exception as exc:
            print("ERROR", name, repr(exc))
            failures.append(name)
    if failures:
        print("selftest FAILED:", failures)
        return 1
    print("selftest OK (4/4)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
