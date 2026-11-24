#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-audit 自检脚本（selftest.py）。

覆盖（对应评分维度 D/E 与定向升级变体 #自检与确定性）：
  S1 空输入目录    -> skeleton 退出码 0，status=insufficient_evidence，JSON 合法；
  S2 坏 JSON       -> skeleton 退出码 0，status=input_error，JSON 合法；
  S3 缺输入目录    -> skeleton 退出码 0，status=input_error，JSON 合法；
  S4 正常案例      -> skeleton + validate 通过，契约字段回填完整；
  S5 确定性        -> 同一输入两次 skeleton 产物逐字节一致；
  S6 证据闭环      -> locators -> absorb -> grade -> verify 全链路确定且逐字核验正确；
  S7 结构自检      -> 脚本文件齐全、无外部依赖、SKILL.md frontmatter 合法。

用法：
  python3 -B scripts/selftest.py
  python3 -B scripts/main.py self-test   # 同一实现

退出码：全部通过 0，任一失败 1；逐项打印 [PASS]/[FAIL]。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"
LITQA = HERE / "litqa.py"

CHECKS = []


def check(sid, name, ok, detail=""):
    CHECKS.append({"id": sid, "name": name, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print("[%s] %s %s" % (mark, sid, name), flush=True)
    if detail and not ok:
        print("      detail: %s" % detail, flush=True)


def run_py(script, args, cwd):
    """用当前解释器 -B 运行脚本，返回 (returncode, stdout, stderr)。"""
    cmd = [sys.executable, "-B", str(script)] + args
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def make_fixture(root, case_id, contract, material_text, task_text):
    """构造一个合成案例目录，返回该目录。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "case.json").write_text(
        json.dumps({"case_id": case_id, "output_contract": contract},
                   ensure_ascii=False), encoding="utf-8")
    (root / "task.md").write_text(task_text, encoding="utf-8")
    (root / "materials").mkdir(exist_ok=True)
    if material_text is not None:
        (root / "materials" / "paper.txt").write_text(
            material_text, encoding="utf-8")
    return root


MATERIAL = (
    "BACKGROUND\n\n"
    "This is a randomized controlled trial of drug X versus placebo.\n\n"
    "METHODS\n\n"
    "We enrolled 240 participants across three sites. Allocation was "
    "concealed using sealed envelopes.\n\n"
    "RESULTS\n\n"
    "The primary outcome differed significantly between groups "
    "(p = 0.03).\n\n"
    "DISCUSSION\n\n"
    "Loss to follow-up was 12 percent.\n"
)
QUOTE = "The primary outcome differed significantly between groups"
CONTRACT = {
    "properties": {
        "candidates": {"type": "array"},
        "screening_log": {"type": "array"},
        "quality_summary": {"type": "string"},
    }
}
ASSESSMENT_FRAGMENT = {
    "research_question": "Is drug X superior to placebo?",
    "assessment": [
        {
            "dimension": "research_design",
            "checkpoint": "design_type",
            "critical": True,
            "verdict": "clear_risk",
            "judgment": "RCT stated but allocation concealment details partial.",
            "locator": "paper.txt#para:4",
            "quote": "We enrolled 240 participants across three sites.",
            "basis": "METHODS paragraph",
        },
        {
            "dimension": "evidence_sufficiency",
            "checkpoint": "sample_size",
            "critical": False,
            "verdict": "no_risk_indicator",
            "judgment": "Sample size reported.",
            "locator": "paper.txt#para:6",
            "quote": QUOTE,
            "basis": "RESULTS paragraph",
        },
    ],
    "sources": [
        {"id": "paper.txt", "type": "material", "path_or_uri": "paper.txt",
         "locator": "paper.txt#para:1", "quote": "", "note": "fixture"},
    ],
}


def test_skeleton_fixtures():
    tmp = Path(tempfile.mkdtemp(prefix="litqa_selftest_"))

    # S1 空目录（目录存在但无 case.json）
    empty = tmp / "empty"; empty.mkdir()
    out1 = tmp / "out1"; work1 = tmp / "work1"
    rc, out, err = run_py(MAIN, ["skeleton", "--input", str(empty),
                                 "--output", str(out1), "--work", str(work1)],
                          HERE)
    res = out1 / "result.json"
    ok1 = rc == 0 and res.is_file()
    status1 = None
    if ok1:
        status1 = json.loads(res.read_text(encoding="utf-8")).get("status")
    check("S1", "empty input dir degrades cleanly",
          ok1 and status1 == "insufficient_evidence",
          "rc=%s status=%s" % (rc, status1))

    # S2 坏 JSON
    bad = tmp / "badjson"; bad.mkdir()
    (bad / "case.json").write_text("{not valid json", encoding="utf-8")
    out2 = tmp / "out2"; work2 = tmp / "work2"
    rc, out, err = run_py(MAIN, ["skeleton", "--input", str(bad),
                                 "--output", str(out2), "--work", str(work2)],
                          HERE)
    res = out2 / "result.json"
    ok2 = rc == 0 and res.is_file()
    status2 = None
    if ok2:
        status2 = json.loads(res.read_text(encoding="utf-8")).get("status")
    check("S2", "bad JSON degrades cleanly",
          ok2 and status2 == "input_error",
          "rc=%s status=%s" % (rc, status2))

    # S3 缺输入目录
    missing = tmp / "missing" / "no_such_input"
    out3 = tmp / "out3"; work3 = tmp / "work3"
    rc, out, err = run_py(MAIN, ["skeleton", "--input", str(missing),
                                 "--output", str(out3), "--work", str(work3)],
                          HERE)
    res = out3 / "result.json"
    ok3 = rc == 0 and res.is_file()
    status3 = None
    if ok3:
        status3 = json.loads(res.read_text(encoding="utf-8")).get("status")
    check("S3", "missing input dir degrades cleanly",
          ok3 and status3 == "input_error",
          "rc=%s status=%s" % (rc, status3))

    # S4 正常案例 + 契约回填（output/work 放在输入目录之外，模拟真实 harness）
    norm = make_fixture(tmp / "norm", "test-case-1", CONTRACT, MATERIAL,
                        "# task\n")
    out4 = tmp / "out4"; work4 = tmp / "work4"
    rc, out, err = run_py(MAIN, ["skeleton", "--input", str(norm),
                                 "--output", str(out4), "--work", str(work4)],
                          HERE)
    res = out4 / "result.json"
    ok4a = rc == 0 and res.is_file()
    data4 = json.loads(res.read_text(encoding="utf-8")) if ok4a else {}
    fields_ok = all(k in data4 for k in
                    ("candidates", "screening_log", "quality_summary"))
    rc_v = run_py(MAIN, ["validate", "--input", str(norm),
                         "--output", str(out4)], HERE)[0]
    check("S4", "normal case skeleton+validate+contract echo",
          ok4a and fields_ok and data4.get("case_id") == "test-case-1"
          and rc_v == 0,
          "contract_fields=%s validate_rc=%s" %
          (sorted(k for k in data4 if k in
                  ("candidates", "screening_log", "quality_summary")), rc_v))

    # S5 确定性：两次 skeleton 逐字节一致（不同 output/work 目录）
    outA = tmp / "outA"; workA = tmp / "workA"
    outB = tmp / "outB"; workB = tmp / "workB"
    rc1 = run_py(MAIN, ["skeleton", "--input", str(norm),
                        "--output", str(outA), "--work", str(workA)], HERE)
    rc2 = run_py(MAIN, ["skeleton", "--input", str(norm),
                        "--output", str(outB), "--work", str(workB)], HERE)
    a = (outA / "result.json").read_bytes()
    b = (outB / "result.json").read_bytes()
    check("S5", "deterministic: two skeleton runs byte-identical",
          rc1[0] == 0 and rc2[0] == 0 and a == b,
          "bytesA=%d bytesB=%d equal=%s" % (len(a), len(b), a == b))

    # S6 证据闭环：locators -> absorb -> grade -> verify
    rc, out, err = run_py(LITQA, ["locators", "--input", str(norm / "materials"),
                                  "--work", str(work4)], HERE)
    idx = work4 / "evidence_index.json"
    rc_a = run_py(LITQA, ["absorb", "--result", str(out4 / "result.json"),
                          "--fragment", str(write_fragment(tmp))], HERE)[0]
    rc_g = run_py(LITQA, ["grade", "--result", str(out4 / "result.json")],
                  HERE)[0]
    vf_out = out4 / "verification_report.json"
    rc_vf = run_py(LITQA, ["verify", "--index", str(idx),
                           "--result", str(out4 / "result.json"),
                           "--out", str(vf_out)], HERE)[0]
    vf = json.loads(vf_out.read_text(encoding="utf-8")) if vf_out.is_file() else {}
    res_final = json.loads((out4 / "result.json").read_text(encoding="utf-8"))
    check("S6", "evidence loop locators/absorb/grade/verify",
          rc == 0 and rc_a == 0 and rc_g == 0 and rc_vf == 0
          and idx.is_file() and vf.get("found", 0) >= 2
          and res_final.get("overall_grade") in
          ("low_risk", "moderate_risk", "high_risk", "unassessable"),
          "verify_found=%s overall=%s" % (vf.get("found"),
                                          res_final.get("overall_grade")))

    return tmp


def write_fragment(tmp):
    p = tmp / "fragment.json"
    p.write_text(json.dumps(ASSESSMENT_FRAGMENT, ensure_ascii=False),
                 encoding="utf-8")
    return str(p)


def test_structure():
    # S7 结构自检
    skill = HERE.parent / "SKILL.md"
    ok_front = False
    if skill.is_file():
        txt = skill.read_text(encoding="utf-8")
        head = txt.split("\n", 6)
        ok_front = (txt.startswith("---") and "name:" in "\n".join(head[:6])
                    and "version:" in "\n".join(head[:6]))
    has_scripts = MAIN.is_file() and LITQA.is_file() and (HERE / "selftest.py").is_file()
    # 无外部依赖：静态检查 import 语句不引入第三方包
    stdlib_ok = True
    for sf in (MAIN, LITQA):
        for line in sf.read_text(encoding="utf-8").splitlines():
            if line.startswith("import ") or line.startswith("from "):
                mod = line.split()[1].split(".")[0]
                if mod not in ("argparse", "json", "os", "re", "sys",
                               "hashlib", "tempfile", "subprocess", "pathlib",
                               "litqa", "selftest"):
                    stdlib_ok = False
    check("S7", "structure: files present, stdlib only, frontmatter valid",
          has_scripts and stdlib_ok and ok_front,
          "scripts=%s stdlib=%s frontmatter=%s" %
          (has_scripts, stdlib_ok, ok_front))


def main(argv=None):
    print("litqa-audit selftest", flush=True)
    tmp = test_skeleton_fixtures()
    test_structure()
    failed = [c for c in CHECKS if not c["ok"]]
    print("\nRESULT: %d/%d checks passed" % (len(CHECKS) - len(failed),
                                             len(CHECKS)), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
