# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：在临时目录构建夹具，验证契约回填、确定性、交叉引用与降级。

不依赖正式测试案例；全部夹具在临时目录内生成，跑完即删。退出码 0 = 通过，1 = 有失败。
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import reviewkit as rk

_PASS = 0
_FAIL = 1


def _strip_nd(obj):
    """剥离时间戳字段，用于确定性比较。"""
    if isinstance(obj, dict):
        return {k: _strip_nd(v) for k, v in obj.items() if k not in ("change_log", "runtime")}
    if isinstance(obj, list):
        return [_strip_nd(v) for v in obj]
    return obj


def _make_fixture(root: str) -> None:
    d = Path(root)
    (d / "materials").mkdir(parents=True, exist_ok=True)
    (d / "case.json").write_text(json.dumps({
        "id": "case-001",
        "title": "样例案例",
        "output_contract": {"findings_report": {"kind": "markdown"}},
    }, ensure_ascii=False), encoding="utf-8")
    (d / "task.md").write_text(
        "# 样例任务\n- challenge_id: `challenge-001`\n", encoding="utf-8")
    (d / "materials" / "paper.md").write_text(
        "# Abstract\n\nOur method improves accuracy by 50%.\n\n"
        "# Introduction\n\nMany studies show X.\n\n"
        "# Methods\n\nWe ran 3 trials with a control group.\n\n"
        "# Results\n\nAccuracy was 90% in the treatment group.\n\n"
        "# Conclusion\n\nOur method is best.\n", encoding="utf-8")


def run() -> int:
    fails = 0
    tmp = Path(tempfile.mkdtemp(prefix="selftest-review-"))
    fixture = tmp / "input"
    out = tmp / "output"
    work = tmp / "work"
    _make_fixture(str(fixture))

    def check(name: str, cond: bool) -> None:
        nonlocal fails
        print(("[PASS] " if cond else "[FAIL] ") + name)
        if not cond:
            fails += 1

    # 1. scaffold 与契约回填
    records, sources, meta, case_id, contract = rk.build_corpus(str(fixture))
    r1 = rk.make_result_skeleton(case_id, meta, contract, sources, records)
    check("scaffold case_id", r1["case_id"] == "case-001")
    check("output_contract 原样回填", "findings_report" in r1
          and r1["findings_report"] == {"kind": "markdown"})
    check("coverage 六维齐全", len(r1["audit"]["coverage"]) == 6)
    check("段落切分与 section 识别", len(records) >= 6)
    check("sources 含材料与任务", any(s["kind"] == "material" for s in sources)
          and any(s["kind"] == "task" for s in sources))

    # 2. 确定性（剥离时间戳后字节一致）
    r2 = rk.make_result_skeleton(case_id, meta, contract, sources, records)
    check("重复运行确定性", _strip_nd(r1) == _strip_nd(r2))

    # 3. merge 与交叉引用
    target = next(r for r in records if "50%" in r["text"])
    ledger = rk.empty_ledger()
    ledger["summary"] = "测试总评"
    ledger["status"] = "partial"
    ledger["claims"] = [{
        "id": "C001", "text": "method improves accuracy by 50%",
        "speaker": "author", "section": "Abstract", "locator": rk.locator(target),
        "quote": "Our method improves accuracy by 50%.",
        "verdict": "overclaimed", "evidence": "仅有摘要表述，无基线对照",
    }]
    ledger["findings"] = [{
        "id": "F001", "claim_id": "C001", "type": "overclaim", "severity": "major",
        "locator": rk.locator(target), "quote": "Our method improves accuracy by 50%.",
        "claim": "improves accuracy by 50%", "evidence": "无基线对照与置信区间",
        "reasoning": "影响主结论；可修正；可复现",
        "suggestion": "补充基线并改写为相对提升幅度",
        "status": "confirmed", "confidence": "high",
    }]
    merged = rk.merge(copy.deepcopy(r1), ledger)
    check("merge 写入 findings", len(merged["audit"]["findings"]) == 1)
    crit = [i for i in rk.validate_result(merged) if i.get("level") == "critical"]
    check("合并后 selfcheck 无 critical", not crit)

    # 4. 悬空 claim_id 报警
    ledger2 = rk.empty_ledger()
    ledger2["findings"] = [{
        "id": "F002", "claim_id": "C099", "type": "overclaim", "severity": "minor",
        "locator": "p1\u00b6P0001", "quote": "x", "claim": "x", "evidence": "x",
        "reasoning": "x", "suggestion": "x", "status": "probable", "confidence": "low",
    }]
    m2 = rk.merge(copy.deepcopy(r1), ledger2)
    issues2 = rk.validate_result(m2)
    check("悬空 claim_id 被标记",
          any("claim_id" in i.get("msg", "") for i in issues2))

    # 5. 空输入
    empty = tmp / "empty"
    empty.mkdir()
    recs_e, srcs_e, meta_e, cid_e, ct_e = rk.build_corpus(str(empty))
    re = rk.make_result_skeleton(cid_e, meta_e, ct_e, srcs_e, recs_e)
    check("空输入不崩溃且降级", re["case_id"] == "unknown_case"
          and re["status"] == "partial")

    # 6. 坏 JSON
    bad = tmp / "bad"
    bad.mkdir()
    (bad / "case.json").write_text("{not json", encoding="utf-8")
    recs_b, srcs_b, meta_b, cid_b, ct_b = rk.build_corpus(str(bad))
    check("坏 JSON 容错", cid_b is not None)

    # 7. 坏编码
    enc = tmp / "enc"
    (enc / "materials").mkdir(parents=True)
    (enc / "materials" / "x.txt").write_bytes("caf\xe9 latin1 \xff".encode("latin-1"))
    recs_c, srcs_c, meta_c, cid_c, ct_c = rk.build_corpus(str(enc))
    check("坏编码容错", len(recs_c) >= 1
          and any(s.get("error") == "bad_encoding_fallback_latin1" for s in srcs_c))

    # 8. CLI 冒烟（scaffold + selfcheck）
    main_py = Path(__file__).resolve().parent / "main.py"
    if main_py.exists():
        shutil.rmtree(str(out), ignore_errors=True)
        shutil.rmtree(str(work), ignore_errors=True)
        r = subprocess.run([sys.executable, "-B", str(main_py), "scaffold",
                            "--input", str(fixture), "--output", str(out),
                            "--work", str(work)],
                           capture_output=True, text=True, timeout=120)
        check("CLI scaffold 退出码 0", r.returncode == 0
              and Path(out, "result.json").is_file())
        r = subprocess.run([sys.executable, "-B", str(main_py), "selfcheck",
                            "--output", str(out)],
                           capture_output=True, text=True, timeout=120)
        check("CLI selfcheck 退出码 0", r.returncode == 0)

    shutil.rmtree(str(tmp), ignore_errors=True)
    print("")
    print("selftest %s (%d failed)" % ("PASS" if fails == 0 else "FAIL", fails))
    return _FAIL if fails else _PASS


if __name__ == "__main__":
    sys.exit(run())
