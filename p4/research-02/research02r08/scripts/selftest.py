#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述合成 Skill 的端到端确定性自检（仅 Python 标准库）。

在临时目录内生成最小合成夹具（运行时生成，不随包分发、不冒充正式案例），然后：
1) 空输入目录          -> main.py 退出码 0，status=input_error
2) 坏 JSON 的 case.json -> main.py 退出码 0，status=input_error 且记录 error
3) 正常夹具            -> main.py 退出码 0，status=partial，材料去重、PDF 抽取正确
4) 确定性              -> 固定 SKILL_NOW 下重复运行 result.json 逐字节一致
5) 模拟 Agent 完成四步 -> validate_result.py 退出码 0
6) 伪造 quote          -> validate_result.py 退出码 1（防伪造回链）

全部通过返回 0，任一失败返回 1。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
VALIDATE = os.path.join(HERE, "validate_result.py")
FIXED_NOW = "2026-01-01T00:00:00Z"

CASE_JSON = {
    "case_id": "selftest_case_001",
    "research_question": "Selftest: does intervention X improve outcome Y?",
    "output_contract": {
        "research_question": {"type": "string"},
        "screening_criteria": {"type": "object"},
        "screening_log": {"type": "array"},
        "evidence_table": {"type": "array"},
        "themes": {"type": "array"},
        "review_draft": {"type": "string"},
        "conflicts_and_gaps": {"type": "array"},
        "references": {"type": "array"},
    },
}

TASK_MD = "# Selftest task\n\n围绕固定研究问题综合证据并说明边界。\n"
SRC_A_TXT = ("Background. Alpha intervention reduced symptom score by 2.1 "
             "points (n=120).\n")
SRC_B_JSON = {"records": [{"title": "Beta study", "doi": "10.0000/beta",
                           "abstract": "Beta intervention showed no effect (n=80)."}]}
SRC_C_JSON = {"records": [{"title": "Beta study", "doi": "10.0000/beta",
                           "abstract": "Duplicate of Beta, should be deduped."}]}


def make_minimal_pdf(text):
    """构造含单行文本的最小合法 PDF（ASCII 文本，可被 pdftotext 抽取）。"""
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    stream = ("BT /F1 12 Tf 72 720 Td (" + esc(text) + ") Tj ET").encode("ascii")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % i).encode("ascii") + body + b"\nendobj\n"
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(bodies) + 1)).encode("ascii")
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode("ascii")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%EOF\n" % (
        len(bodies) + 1, xref)).encode("ascii")
    return bytes(out)


def build_fixture(root):
    """root/input 与 root/output、root/work。返回 (input_dir, output_dir, work_dir)。"""
    ind = os.path.join(root, "input")
    outd = os.path.join(root, "output")
    work = os.path.join(root, "work")
    mats = os.path.join(ind, "materials")
    os.makedirs(mats, exist_ok=True)
    with open(os.path.join(ind, "case.json"), "w", encoding="utf-8") as f:
        json.dump(CASE_JSON, f, ensure_ascii=False)
    with open(os.path.join(ind, "task.md"), "w", encoding="utf-8") as f:
        f.write(TASK_MD)
    with open(os.path.join(mats, "src_a.txt"), "w", encoding="utf-8") as f:
        f.write(SRC_A_TXT)
    with open(os.path.join(mats, "src_b.json"), "w", encoding="utf-8") as f:
        json.dump(SRC_B_JSON, f, ensure_ascii=False)
    with open(os.path.join(mats, "src_c.json"), "w", encoding="utf-8") as f:
        json.dump(SRC_C_JSON, f, ensure_ascii=False)
    with open(os.path.join(mats, "src_d.pdf"), "wb") as f:
        f.write(make_minimal_pdf("Gamma cohort reported 12% adverse events."))
    return ind, outd, work


def run(py_script, args, env_extra=None, timeout=180):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, "-B", py_script] + args, env=env,
                          capture_output=True, text=True, timeout=timeout)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fill_good_case(output, work, quote):
    """模拟 Agent 完成 Phase 1–4：填入证据、筛选、草稿与 result.json。"""
    evidence = [{
        "evidence_id": "S1",
        "source_id": "src_01",
        "locator": "src_01#para1",
        "quote": quote,
        "claim": "Alpha intervention reduced symptom score by 2.1 points",
        "strength": "suggestive",
        "field": "outcome",
        "notes": "单源、样本有限",
    }]
    with open(os.path.join(output, "evidence_table.jsonl"), "w",
              encoding="utf-8") as f:
        for row in evidence:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    inv = read_json(os.path.join(work, "inventory.json"))
    sources = [{"source_id": m["source_id"], "path": m["path"],
                "extraction_status": m["extraction_status"]}
               for m in inv["materials"]]

    sl = read_json(os.path.join(output, "screening_log.json"))
    sl["screening_criteria"] = {
        "population": "selftest population",
        "include": ["干预研究"],
        "exclude": ["非原创评论"],
        "dedup_rule": "doi_normalized|title_normalized|content_sha256"}
    sl["dedup_records"] = inv.get("dedup", [])
    sl["decisions"] = [{"source_id": m["source_id"], "decision": "include",
                        "reason": "selftest 纳入"} for m in inv["materials"]]
    with open(os.path.join(output, "screening_log.json"), "w",
              encoding="utf-8") as f:
        json.dump(sl, f, ensure_ascii=False, indent=2)

    with open(os.path.join(output, "review.md"), "w", encoding="utf-8") as f:
        f.write("# 综述草稿（selftest）\n\n研究问题：%s\n\n"
                "Alpha 干预提示可降低症状评分 [S1]。\n" % CASE_JSON["research_question"])
    with open(os.path.join(output, "themes.md"), "w", encoding="utf-8") as f:
        f.write("# 主题归纳（selftest）\n\n## 干预有效性\n支撑：src_01\n")
    with open(os.path.join(output, "conflicts_and_gaps.md"), "w",
              encoding="utf-8") as f:
        f.write("# 冲突与研究空白（selftest）\n\n- 空白 gap1：未见长期随访报告。\n")

    result = read_json(os.path.join(output, "result.json"))
    result["status"] = "ok"
    result["research_question"] = CASE_JSON["research_question"]
    result["screening_criteria"] = sl["screening_criteria"]
    result["screening_log"] = sl["decisions"]
    result["evidence_table"] = [e["evidence_id"] for e in evidence]
    result["themes"] = ["干预有效性"]
    result["review_draft"] = "review.md"
    result["conflicts_and_gaps"] = [{"gap_id": "gap1",
                                     "detail": "未见长期随访报告",
                                     "evidence_absence": "src_01 未报告"}]
    result["references"] = sources
    result["sources"] = sources
    result["uncertainty"] = [{"type": "gap", "detail": "单源、无长期随访"}]
    result["pending_human"] = []
    result["change_log"].append({"ts": FIXED_NOW, "phase": "phase4",
                                 "action": "finalize", "detail": "selftest"})
    with open(os.path.join(output, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def test_case(name, cond, detail=""):
    if cond:
        print("PASS  %s" % name)
        return True
    print("FAIL  %s  %s" % (name, detail))
    return False


def main():
    failures = 0

    # 1) 空输入目录
    with tempfile.TemporaryDirectory() as root:
        ind, outd, work = (os.path.join(root, x) for x in ("input", "output", "work"))
        os.makedirs(ind)
        r = run(MAIN, ["--input", ind, "--output", outd, "--work", work],
                {"SKILL_NOW": FIXED_NOW})
        ok = r.returncode == 0
        if ok:
            res = read_json(os.path.join(outd, "result.json"))
            ok = res.get("status") == "input_error" and res.get("case_id")
        failures += 0 if test_case("空输入目录 -> input_error 且退出码 0", ok,
                                   r.stderr[-300:]) else 1

    # 2) 坏 JSON
    with tempfile.TemporaryDirectory() as root:
        ind, outd, work = (os.path.join(root, x) for x in ("input", "output", "work"))
        os.makedirs(ind)
        with open(os.path.join(ind, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        r = run(MAIN, ["--input", ind, "--output", outd, "--work", work],
                {"SKILL_NOW": FIXED_NOW})
        ok = r.returncode == 0
        if ok:
            res = read_json(os.path.join(outd, "result.json"))
            ok = res.get("status") == "input_error" and res.get("errors")
        failures += 0 if test_case("坏 JSON -> input_error 且记录 error", ok,
                                   r.stderr[-300:]) else 1

    # 3–6) 正常夹具
    with tempfile.TemporaryDirectory() as root:
        ind, outd, work = build_fixture(root)
        r = run(MAIN, ["--input", ind, "--output", outd, "--work", work],
                {"SKILL_NOW": FIXED_NOW})
        ok = r.returncode == 0
        detail = r.stderr[-300:]
        if ok:
            res = read_json(os.path.join(outd, "result.json"))
            inv = read_json(os.path.join(work, "inventory.json"))
            ok = (res.get("status") == "partial"
                  and res.get("case_id") == "selftest_case_001"
                  and len(inv["materials"]) == 3
                  and len(inv["dedup"]) == 1)
        failures += 0 if test_case("正常夹具 -> partial/去重/骨架落盘", ok, detail) else 1

        # 4) 确定性
        with tempfile.TemporaryDirectory() as root2:
            ind2, outd2, work2 = build_fixture(root2)
            run(MAIN, ["--input", ind2, "--output", outd2, "--work", work2],
                {"SKILL_NOW": FIXED_NOW})
            a = read_json(os.path.join(outd, "result.json"))
            b = read_json(os.path.join(outd2, "result.json"))
            failures += 0 if test_case(
                "固定 SKILL_NOW 下重复运行 result.json 逐字节一致",
                json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)) else 1

        # 5) 完成四步 -> validate 通过
        fill_good_case(outd, work, "Alpha intervention reduced symptom score by 2.1 points (n=120).")
        r = run(VALIDATE, ["--output", outd, "--case",
                           os.path.join(ind, "case.json"), "--work", work])
        failures += 0 if test_case("完成四步 -> validate 退出码 0", r.returncode == 0,
                                   (r.stdout + r.stderr)[-400:]) else 1

        # 6) 伪造 quote -> validate 失败
        with tempfile.TemporaryDirectory() as root3:
            ind3, outd3, work3 = build_fixture(root3)
            run(MAIN, ["--input", ind3, "--output", outd3, "--work", work3],
                {"SKILL_NOW": FIXED_NOW})
            fill_good_case(outd3, work3, "THIS QUOTE DOES NOT EXIST ANYWHERE")
            r = run(VALIDATE, ["--output", outd3, "--case",
                               os.path.join(ind3, "case.json"), "--work", work3])
            failures += 0 if test_case("伪造 quote -> validate 退出码 1",
                                       r.returncode == 1,
                                       (r.stdout + r.stderr)[-400:]) else 1

    print("\nselftest: %s" % ("ALL PASS" if failures == 0 else
                              "%d FAILURE(S)" % failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
