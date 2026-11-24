#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：标准库限定 + 契约往返 + 确定性渲染。退出码 0 表示全部通过。"""
import importlib.util
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import main as core  # noqa: E402

IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))")


def stdlib_only(files):
    bad = []
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                m = IMPORT_RE.match(line)
                if not m:
                    continue
                mod = m.group(1) or m.group(2)
                top = mod.split(".")[0]
                if top == "main":
                    continue
                try:
                    spec = importlib.util.find_spec(top)
                except (ImportError, ValueError):
                    bad.append("%s: %s" % (fp, mod))
                    continue
                if spec is None:
                    continue
                origin = (spec.origin or "").replace("\\", "/")
                if "site-packages" in origin or "dist-packages" in origin:
                    bad.append("%s: %s (第三方)" % (fp, mod))
    return bad


def make_case():
    return {
        "case_id": "selftest-demo",
        "output_contract": {
            "title": {"required": True},
            "summary": {"required": True},
            "proposal": {"required": True},
            "artifacts": {"required": True},
        },
    }


def make_complete_result(case):
    r = core.init_skeleton(case)
    r["status"] = "complete"
    r["title"] = "示例：某课题开题方案（自检样例）"
    r["summary"] = "示意内容，仅用于自检，非正式案例结果。"
    r["background"] = {"known": ["K1"], "gap": ["G1"], "to_verify": ["T1"]}
    r["problems"] = [{"id": "P1", "question": "Q1", "falsifiable": "若X不成立则否",
                      "gap_link": "G1"}]
    r["objectives"] = [{"problem_ref": "P1", "goal": "G",
                        "criteria": "指标/样本/对照/时间点"}]
    r["methods"] = [{"step": "S1", "input": "I", "output": "O", "depends_on": []}]
    r["plan"] = {"period": "24个月", "budget": "N",
                 "milestones": [{"m": "M1", "date": "Q1"}],
                 "critical_path": ["S1"], "parallelizable": []}
    r["risks"] = [{"category": "数据获取", "trigger": "T", "impact": "I",
                   "alternative": "A", "decision_point": "D"}]
    r["expected_outcomes"] = [{"outcome": "O", "verification": "V"}]
    r["references"] = [{"claim": "C", "source_id": "S1", "locator": "L"}]
    r["boundaries"] = {"in_scope": ["x"], "out_of_scope": ["y"]}
    r["sources"] = [{"id": "S1", "locator": "demo.md#p1", "quote": "逐字引用", "claim": "C"}]
    r["artifacts"] = []
    r["uncertainty"] = []
    r["pending_human"] = [{"item": "demo", "why": "示例", "action": "确认"}]
    r["change_log"] = [{"at": "2026-01-01T00:00:00Z", "action": "selftest", "reason": "示例"}]
    return r


def test_contract_roundtrip(case):
    r = core.init_skeleton(case)
    for field, _req in core.extract_contract(case):
        assert field in r, "契约字段缺失: %s" % field
    for k in core.ENVELOPE:
        assert k in r, "信封字段缺失: %s" % k
    for k in core.SEVEN_ELEMENTS:
        assert k in r, "七要素缺失: %s" % k
    fails, _warns = core.check_result(case, r)
    assert not fails, "partial 骨架不应有 FAIL: %s" % fails
    return r


def test_complete_and_determinism(case):
    r = make_complete_result(case)
    fails, warns = core.check_result(case, r)
    assert not fails, "完整结果不应有 FAIL: %s" % fails
    assert not warns, "完整结果不应有 WARN: %s" % warns
    d1 = tempfile.mkdtemp(prefix="selftest1_")
    d2 = tempfile.mkdtemp(prefix="selftest2_")
    p1, e1 = core.render(r, d1)
    p2, e2 = core.render(r, d2)
    with open(p1, "rb") as f:
        assert f.read() == open(p2, "rb").read(), "proposal.md 两次渲染不一致"
    with open(e1, "rb") as f:
        assert f.read() == open(e2, "rb").read(), "evidence.md 两次渲染不一致"
    a = core.init_skeleton(case)
    b = core.init_skeleton(case)
    assert (json.dumps(a, ensure_ascii=False, sort_keys=True)
            == json.dumps(b, ensure_ascii=False, sort_keys=True)), "init 骨架非确定性"
    return True


def main():
    files = [os.path.join(HERE, "main.py"), os.path.join(HERE, "selftest.py")]
    case = make_case()
    results = []

    bad = stdlib_only(files)
    results.append(("stdlib-only", "OK" if not bad else "; ".join(bad), not bad))

    try:
        test_contract_roundtrip(case)
        results.append(("contract-roundtrip", "OK", True))
    except AssertionError as e:
        results.append(("contract-roundtrip", str(e), False))

    try:
        test_complete_and_determinism(case)
        results.append(("complete+determinism", "OK", True))
    except AssertionError as e:
        results.append(("complete+determinism", str(e), False))

    all_pass = all(r[2] for r in results)
    for name, msg, passed in results:
        print(("PASS  " if passed else "FAIL  ") + name + ("" if passed else " -> %s" % msg))
    print("SELFTEST %s" % ("ok" if all_pass else "failed"))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
