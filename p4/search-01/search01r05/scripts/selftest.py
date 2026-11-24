#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检脚本（作者提交前运行，纯标准库）。

覆盖三件事：健壮性（空输入 / 坏 JSON / 缺输入不崩溃）、
输出契约回填（dict/list/字符串三种契约逐一对齐）、
端到端最小闭环（解析 -> 规划 -> 导入 -> 组装 -> 自检全绿）。

使用方法（在 Skill 根目录）：
    python3 scripts/selftest.py
返回码：全部通过为 0，否则为 1。
"""
import json
import os
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import core as C  # noqa: E402

PASS = 0
FAIL = 0
LINES = []


def record(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        LINES.append("[PASS] " + label)
    else:
        FAIL += 1
        LINES.append("[FAIL] " + label)


def ns(**kw):
    d = dict(input=C.DEFAULT_INPUT, output=C.DEFAULT_OUTPUT,
             work=C.DEFAULT_WORK, inline=None, file=None)
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_io_robustness():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    # 1) 空输入目录
    empty_in = os.path.join(tmp, "empty_in")
    os.makedirs(empty_in, exist_ok=True)
    cs = C.parse_case(empty_in)
    record("no_case_json" in cs["degradations"] and "no_task_md" in cs["degradations"],
           "空输入目录：解析降级不崩溃")
    # 2) 坏 JSON
    bad_in = os.path.join(tmp, "bad_in")
    os.makedirs(bad_in, exist_ok=True)
    with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    with open(os.path.join(bad_in, "task.md"), "w", encoding="utf-8") as f:
        f.write("# t\n")
    _, deg = C.read_json(os.path.join(bad_in, "case.json"), {})
    record(any(x.startswith("bad_json") for x in deg), "坏 JSON：标记 bad_json 降级")
    # 3) 坏编码（GB18030 内容按 utf-8 读）
    enc_in = os.path.join(tmp, "enc_in")
    os.makedirs(enc_in, exist_ok=True)
    with open(os.path.join(enc_in, "case.json"), "wb") as f:
        f.write('{"title":"测试".encode?}'.encode("gb18030"))
    text, _deg = C.read_text(os.path.join(enc_in, "case.json"))
    record(bool(text), "坏编码：回退解码仍能读出文本")
    return tmp


def test_contract_normalization():
    d = C.normalize_contract({"fields": {"case_id": "标识", "results": {"required": True, "type": "array"}}})
    names = [x["name"] for x in d]
    record("case_id" in names and "results" in names, "契约 dict 形式归一化")
    l = C.normalize_contract(["case_id", "results", "evidence"])
    record([x["name"] for x in l] == ["case_id", "results", "evidence"], "契约 list 形式归一化")
    s = C.normalize_contract("输出 `case_id` 与 `results` 字段")
    record([x["name"] for x in s] == ["case_id", "results"], "契约字符串（反引号字段名）归一化")
    js = C.normalize_contract('{"required": ["case_id"], "properties": {"results": {"type": "array"}}}')
    record(any(x["name"] == "case_id" and x["required"] for x in js), "JSON-schema 形式归一化")


def test_scoring_and_dedup():
    r1 = C.normalize_record({"title": "A Study on X", "doi": "10.1000/abc", "database": "crossref",
                             "relevance": 0.9, "authors": ["A"], "year": 2020, "journal": "J"})
    r2 = C.normalize_record({"title": "A Study on X!", "doi": "https://doi.org/10.1000/abc",
                             "database": "crossref", "relevance": 0.8})
    record(C.dedup_key(r1) == C.dedup_key(r2), "去重：DOI 规范化后视为同一记录")
    s = C.score_record(r1)
    record(0.0 <= s <= 1.0, "打分：score 落在 [0,1]")
    deduped = C.dedup_records([r1, r2])
    record(len(deduped) == 1, "去重后仅保留一条")


def test_end_to_end():
    tmp = tempfile.mkdtemp(prefix="skill_e2e_")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)
    contract = {"output_contract": {
        "fields": {"case_id": {"required": True}, "results": {"type": "array"},
                   "evidence": {}, "status": {}, "change_log": {},
                   "custom_missing": {}},
    }}
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(contract, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 案例\n\n研究问题：Test topic X\n")

    a = ns(input=inp, output=out, work=work)
    # run 骨架流水线
    import main as M
    M.cmd_skeleton(a)
    M.cmd_parse(a)
    M.cmd_plan(a)
    rec = {"title": "A Real Study of Topic X", "doi": "10.1000/xyz", "authors": ["Doe, J"],
           "year": 2024, "journal": "Journal of Tests", "database": "crossref",
           "relevance": 0.95, "relevance_reason": "直接研究 Topic X，摘要命中核心概念",
           "evidence": [{"locator": "abstract", "quote": "we study topic X", "source": "crossref"}]}
    a.inline = json.dumps([rec], ensure_ascii=False)
    M.cmd_import(a)
    a.inline = json.dumps({"query": "topic X", "source": "paper_search",
                           "database": "crossref", "rationale": "核心概念直查"}, ensure_ascii=False)
    M.cmd_evidence(a)
    M.cmd_assemble(a)

    with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
        r = json.load(f)
    record(bool(r.get("case_id")), "端到端：case_id 存在")
    record(len(r.get("results", [])) == 1, "端到端：results 1 条")
    record("custom_missing" in r, "端到端：契约未知字段 custom_missing 原样保留")
    report = r.get("contract_report", {})
    record([x["name"] for x in report.get("fields", [])] and
           all(x["name"] in r for x in report.get("fields", [])),
           "端到端：契约字段逐一对齐")

    ok, checks, _ = C.selfcheck(out, inp, work)
    record(ok, "端到端：selfcheck 全绿")
    if not ok:
        for mark, label in checks:
            LINES.append("   [%s] %s" % (mark, label))
    # 重复组装一致性（结果/分数/排序确定性）
    r_before = json.dumps(r.get("results"))
    M.cmd_assemble(a)
    with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
        r2 = json.load(f)
    record(json.dumps(r2.get("results")) == r_before, "端到端：重复组装结果确定（结果/分数/排序一致）")
    return tmp


def test_cli_help():
    try:
        import main as M
        M.main(["--help"])
        record(True, "CLI：main.py --help 正常返回")
    except SystemExit as e:
        record(e.code in (0, None), "CLI：main.py --help 退出码 0")


def main():
    test_io_robustness()
    test_contract_normalization()
    test_scoring_and_dedup()
    test_end_to_end()
    test_cli_help()
    print("\n".join(LINES))
    print("=" * 40)
    print("selftest: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())