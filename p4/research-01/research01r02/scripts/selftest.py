#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 在临时目录自建 demo 输入，验证全链路与降级路径。

用法：python3 -B scripts/selftest.py
- 不写任何正式案例产物、不预置正式答案，全部在 tempfile 下运行；
- 覆盖：正常全链路、空输入、坏 JSON、CLI 冒烟、确定性（证据 id 稳定）；
- 退出码 0 表示全部通过，1 表示存在失败。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as R

PASS = 0
FAIL = 0
MSGS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        MSGS.append("PASS  %-28s %s" % (name, detail))
    else:
        FAIL += 1
        MSGS.append("FAIL  %-28s %s" % (name, detail))


def make_demo(root):
    os.makedirs(os.path.join(root, "input", "materials"), exist_ok=True)
    case = {
        "case_id": "demo-1",
        "output_contract": {
            "findings": {"primary": None, "count": None},
            "tags": None,
        },
    }
    with open(os.path.join(root, "input", "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(root, "input", "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# demo task\n\n综合材料回答研究问题，并给出证据定位。\n")
    with open(os.path.join(root, "input", "materials", "paper.md"),
              "w", encoding="utf-8") as fh:
        fh.write("## 方法\n\n本研究发现 X 组的中位生存期为 12.3 个月。\n\n"
                 "## 结果\n\n对照组为 8.1 个月。\n")
    return case


def write_work(work, cards, payload):
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "decomposition.md"), "w", encoding="utf-8") as fh:
        fh.write("# 拆解\n\n- SQ1：X 组生存期证据（独立）\n"
                 "- SQ2：对照组生存期证据（依赖 SQ1 作对照基线）\n")
    with open(os.path.join(work, "search_plan.md"), "w", encoding="utf-8") as fh:
        fh.write("# 检索计划\n\n顺序：先查材料内 SQ1，再查 SQ2；"
                 "来源范围：/input/materials；停止条件：两条结论均有逐字定位或显式缺口即停止。\n")
    with open(os.path.join(work, "synthesis.md"), "w", encoding="utf-8") as fh:
        fh.write("## 综合\n\nX 组优于对照组，但样本量与置信区间未报告。\n")
    with open(os.path.join(work, "evidence_cards.jsonl"), "w", encoding="utf-8") as fh:
        for card in cards:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")
    with open(os.path.join(work, "payload.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def test_full():
    root = tempfile.mkdtemp(prefix="skill-full-")
    make_demo(root)
    i, o, w = (os.path.join(root, "input"), os.path.join(root, "output"),
               os.path.join(root, "work"))
    cards = [
        {"id": "E1", "subquestion": "SQ1", "source": "materials/paper.md",
         "locator": "## 方法", "quote": "本研究发现 X 组的中位生存期为 12.3 个月。",
         "claim": "X 组中位生存期 12.3 个月", "judgment": "supports",
         "reason": "逐字来自材料", "conclusion_ids": ["C1"],
         "trace": {"tool": "read", "params": {"file": "paper.md"}, "outcome": "ok"}},
        {"id": "E2", "subquestion": "SQ2", "source": "materials/paper.md",
         "locator": "## 结果", "quote": "对照组为 8.1 个月。",
         "claim": "对照组中位生存期 8.1 个月", "judgment": "supports",
         "reason": "逐字来自材料", "conclusion_ids": ["C1"],
         "trace": {"tool": "read", "params": {"file": "paper.md"}, "outcome": "ok"}},
    ]
    payload = {
        "research_product": {
            "type": "synthesis",
            "question": "X 组与对照组生存期对比",
            "summary": "X 组中位生存期（12.3 个月）高于对照组（8.1 个月）。",
            "conclusions": [{
                "id": "C1", "text": "X 组中位生存期高于对照组",
                "evidence_ids": ["E1", "E2"], "strength": "established"}],
        },
        "uncertainty": {
            "gaps": [{"what": "样本量与置信区间未报告", "why": "材料未给出"}],
            "conflicts": [{"sources": ["materials/paper.md", "外部摘要"],
                           "claim": "生存期数值", "status": "resolved",
                           "resolution": "以材料逐字值为准"}],
        },
        "status": "ok",
    }
    write_work(w, cards, payload)

    d = R.run_discover(i)
    check("discover finds case.json", d.get("case_json") is not None)
    check("discover contract fields", "findings" in (d.get("contract_fields") or []),
          str(d.get("contract_fields")))

    s = R.run_skeleton(i, o, w)
    check("skeleton wrote", s.get("wrote") is True)
    res = R.load_json(os.path.join(o, "result.json"))[0]
    check("skeleton status partial", res.get("status") == "partial", res.get("status"))
    check("skeleton contract kept", "findings" in res and "tags" in res)

    ck = R.run_checkpoint(o, w, "P1", 4, "demo")
    check("checkpoint ok", ck.get("ok") is True, str(ck))

    m = R.run_merge(o, w, os.path.join(w, "payload.json"))
    check("merge ok", m.get("ok") is True, str(m))
    res = R.load_json(os.path.join(o, "result.json"))[0]
    check("merge conclusions", len(res.get("research_product", {}).get("conclusions", [])) == 1)
    check("merge contract kept", "findings" in res)

    f = R.run_finalize(i, o, w)
    check("finalize ok", f.get("ok") is True, str(f))
    check("report.md exists", os.path.exists(os.path.join(o, "report.md")))
    check("evidence_map.json exists", os.path.exists(os.path.join(o, "evidence_map.json")))
    res = R.load_json(os.path.join(o, "result.json"))[0]
    check("finalize evidence rows", len(res.get("evidence_matrix", [])) == 2,
          str(len(res.get("evidence_matrix", []))))
    check("finalize status ok", res.get("status") == "ok", res.get("status"))

    sc = R.run_selfcheck(i, o, w)
    check("selfcheck all pass", sc.get("ok") is True, sc.get("summary"))

    # 幂等/确定性：再次 finalize，证据 id 与顺序不变
    R.run_finalize(i, o, w)
    res2 = R.load_json(os.path.join(o, "result.json"))[0]
    ids1 = [e.get("id") for e in res.get("evidence_matrix", [])]
    ids2 = [e.get("id") for e in res2.get("evidence_matrix", [])]
    check("deterministic evidence ids", ids1 == ids2, str((ids1, ids2)))

    shutil.rmtree(root, ignore_errors=True)


def test_empty():
    root = tempfile.mkdtemp(prefix="skill-empty-")
    i = os.path.join(root, "input")
    os.makedirs(i, exist_ok=True)
    o, w = os.path.join(root, "output"), os.path.join(root, "work")
    s = R.run_skeleton(i, o, w)
    check("empty wrote skeleton", s.get("wrote") is True)
    res = R.load_json(os.path.join(o, "result.json"))[0]
    check("empty status insufficient", res.get("status") == "insufficient_evidence",
          res.get("status"))
    sc = R.run_selfcheck(i, o, w)
    check("empty selfcheck returns dict", isinstance(sc, dict))
    shutil.rmtree(root, ignore_errors=True)


def test_bad_json():
    root = tempfile.mkdtemp(prefix="skill-bad-")
    i = os.path.join(root, "input")
    os.makedirs(i, exist_ok=True)
    with open(os.path.join(i, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ 这不是合法 JSON ")
    o, w = os.path.join(root, "output"), os.path.join(root, "work")
    R.run_skeleton(i, o, w)
    res = R.load_json(os.path.join(o, "result.json"))[0]
    check("badjson status input_error", res.get("status") == "input_error", res.get("status"))
    check("badjson errors recorded", bool(res.get("errors")), str(res.get("errors")))
    shutil.rmtree(root, ignore_errors=True)


def test_cli():
    root = tempfile.mkdtemp(prefix="skill-cli-")
    make_demo(root)
    i, o, w = (os.path.join(root, "input"), os.path.join(root, "output"),
               os.path.join(root, "work"))
    mainpy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    p = subprocess.run([sys.executable, "-B", mainpy, "discover", "--input", i],
                       capture_output=True, text=True)
    check("cli discover exit 0", p.returncode == 0, str(p.returncode))
    obj = json.loads(p.stdout)
    check("cli discover json", isinstance(obj, dict))
    p2 = subprocess.run([sys.executable, "-B", mainpy, "selfcheck",
                         "--input", i, "--output", o, "--work", w],
                        capture_output=True, text=True)
    check("cli selfcheck exit 0", p2.returncode == 0, str(p2.returncode))
    shutil.rmtree(root, ignore_errors=True)


def main():
    test_full()
    test_empty()
    test_bad_json()
    test_cli()
    for msg in MSGS:
        print(msg)
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
