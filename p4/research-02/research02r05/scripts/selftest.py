#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的自检脚本（仅 Python 标准库）。

覆盖三种场景，验证 main.py 的骨架与 validate_result.py 的契约校验：
  A) 正常输入：嵌套 output_contract + txt 材料 + 伪 PDF（触发降级） + 坏 JSON 材料
  B) 空输入目录：应得 insufficient_evidence 且 result.json 合法
  C) 坏 case.json：应得 input_error 且 result.json 仍可解析

运行：python3 -B scripts/selftest.py
退出码：0 = 全部通过；1 = 存在失败项。
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main as main_mod
import validate_result as validate_mod


def write(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def load(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def make_demo_input(root):
    inp = os.path.join(root, "demo", "input")
    case = {
        "case_id": "demo-case-001",
        "research_question": "评估某干预在目标人群中的效果与证据边界",
        "output_contract": {
            "type": "object",
            "required": ["research_question", "review_draft"],
            "properties": {
                "research_question": {"type": "string"},
                "review_draft": {"type": "string"},
                "screening_log": {"type": "array", "items": {"type": "object"}},
                "evidence_table": {"type": "array", "items": {"type": "object"}},
                "themes": {"type": "array", "items": {"type": "object"}},
                "summary": {
                    "type": "object",
                    "properties": {
                        "main_finding": {"type": "string"},
                        "confidence": {"type": "string"},
                    },
                },
            },
        },
        "materials": ["materials/paper1.txt", "materials/note.md"],
    }
    write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# 任务\n请完成综述。\n")
    write(os.path.join(inp, "materials", "paper1.txt"),
          "Study A: n=120, 干预组改善显著（p<0.01）。\n")
    write(os.path.join(inp, "materials", "note.md"),
          "Study B: 样本有限，结论仅为提示性。\n")
    # 伪 PDF：触发 pdftotext 失败并记入 degradations（不伪造抽取结果）
    write(os.path.join(inp, "materials", "fake.pdf"), "%PDF-1.4 not-a-real-pdf")
    # 坏 JSON 材料：应被容错记录，不中断主流程
    write(os.path.join(inp, "materials", "bad_record.json"), "{not valid json")
    return inp


def run_case_a():
    root = tempfile.mkdtemp(prefix="selftest_a_")
    inp = make_demo_input(root)
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    rc = main_mod.main(["--input", inp, "--output", out, "--work", work])
    record("A1 main 退出码为 0", rc == 0, "rc=%s" % rc)
    r = load(os.path.join(out, "result.json"))
    record("A2 case_id 正确", r.get("case_id") == "demo-case-001", str(r.get("case_id")))
    record("A3 status=partial", r.get("status") == "partial", str(r.get("status")))
    for k in ("research_question", "review_draft", "screening_log",
              "evidence_table", "themes", "summary"):
        record("A4 契约字段存在: " + k, k in r, "")
    record("A5 嵌套 summary 为对象", isinstance(r.get("summary"), dict), str(r.get("summary")))
    record("A6 sources/uncertainty/change_log 存在",
           isinstance(r.get("sources"), list)
           and isinstance(r.get("uncertainty"), dict)
           and isinstance(r.get("change_log"), list)
           and bool(r.get("change_log")))
    record("A7 伪 PDF 降级被记录",
           any("fake.pdf" in d or "pdftotext" in d for d in r.get("degradations", [])),
           str(r.get("degradations", [])))
    inv = load(os.path.join(work, "inventory.json"))
    record("A8 inventory 材料数>=4", len(inv.get("materials", [])) >= 4,
           str(len(inv.get("materials", []))))
    rc_v = validate_mod.main(["--output", out, "--case", os.path.join(inp, "case.json"),
                              "--work", work])
    record("A9 validate 退出码为 0（partial 阶段）", rc_v == 0, "rc=%s" % rc_v)
    shutil.rmtree(root, ignore_errors=True)


def run_case_b():
    root = tempfile.mkdtemp(prefix="selftest_b_")
    inp = os.path.join(root, "empty")
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    rc = main_mod.main(["--input", inp, "--output", out, "--work", work])
    record("B1 空输入退出码为 0", rc == 0, "rc=%s" % rc)
    r = load(os.path.join(out, "result.json"))
    record("B2 空输入 status=insufficient_evidence",
           r.get("status") == "insufficient_evidence", str(r.get("status")))
    record("B3 空输入仍为合法 JSON", isinstance(r, dict), "")
    shutil.rmtree(root, ignore_errors=True)


def run_case_c():
    root = tempfile.mkdtemp(prefix="selftest_c_")
    inp = os.path.join(root, "bad")
    out = os.path.join(root, "out")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "case.json"), "{broken json")
    write(os.path.join(inp, "materials", "p.txt"), "some text")
    rc = main_mod.main(["--input", inp, "--output", out, "--work", work])
    record("C1 坏 case.json 退出码为 0（容错）", rc == 0, "rc=%s" % rc)
    r = load(os.path.join(out, "result.json"))
    record("C2 坏 case.json status=input_error",
           r.get("status") == "input_error", str(r.get("status")))
    record("C3 坏 case.json 记录 error",
           any("case.json" in e or "解析" in e for e in r.get("errors", [])),
           str(r.get("errors", [])))
    record("C4 坏 case.json 仍产出默认契约字段",
           all(k in r for k in ("research_question", "review_draft", "screening_log")),
           "")
    shutil.rmtree(root, ignore_errors=True)


def main():
    run_case_a()
    run_case_b()
    run_case_c()
    failed = [n for n, ok, _d in RESULTS if not ok]
    print("\n自检完成：%d/%d 通过" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("失败项：" + ", ".join(failed))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
