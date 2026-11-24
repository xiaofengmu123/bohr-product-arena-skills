#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the deep-research skill package (standard library only).

Verifies, without touching /input or /app/output:

  1. SKILL.md exists and has valid name/description/version YAML frontmatter.
  2. scripts/main.py runs ``--help`` with exit code 0.
  3. Empty input  -> honest insufficient_evidence result, exit code 0.
  4. Bad JSON / bad encoding -> recorded in errors/degradations, no crash.
  5. Full pipeline (skeleton -> merge -> finalize -> selfcheck) on a clearly
     labelled synthetic case with an output_contract: contract fields echoed,
     six key points detected, evidence backlink rate 1.0.

Usage:
    python scripts/selftest.py [--root DIR]

Exit code 0 on pass, 1 on fail. The synthetic case below is a MINIMAL CALL
EXAMPLE (示意输入), not a prepared answer for any formal test case.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

FAILED = []


def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def check(cond, label):
    log(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def run(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def load_json_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    args = ap.parse_args()
    root = args.root
    skill_md = os.path.join(root, "SKILL.md")
    main_py = os.path.join(root, "scripts", "main.py")

    log("=== 1. package structure ===")
    check(os.path.isfile(skill_md), "SKILL.md exists")
    check(os.path.isfile(main_py), "scripts/main.py exists")

    frontmatter_ok = False
    if os.path.isfile(skill_md):
        with open(skill_md, "r", encoding="utf-8") as f:
            head = f.read(2000)
        m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", head, re.S)
        if m:
            fm = m.group(1)
            frontmatter_ok = all(
                re.search(r"^%s\s*:" % k, fm, re.M) for k in
                ("name", "description", "version"))
    check(frontmatter_ok, "SKILL.md frontmatter has name/description/version")

    log("=== 2. --help ===")
    rc, out, err = run([sys.executable, "-B", main_py, "--help"])
    check(rc == 0, "main.py --help exits 0 (rc=%d)" % rc)

    with tempfile.TemporaryDirectory() as td:
        log("=== 3. empty input ===")
        empty_in = os.path.join(td, "empty_in")
        empty_out = os.path.join(td, "empty_out")
        empty_work = os.path.join(td, "empty_work")
        os.makedirs(empty_in)
        rc, _, _ = run([sys.executable, "-B", main_py, "skeleton",
                        "--input", empty_in, "--output", empty_out,
                        "--work", empty_work])
        res = load_json_file(os.path.join(empty_out, "result.json"))
        check(rc == 0, "empty skeleton exits 0")
        check(res.get("status") == "insufficient_evidence",
              "empty input -> insufficient_evidence")
        check(res.get("case_id") == "", "empty input -> empty case_id")

        log("=== 4. bad JSON / bad encoding ===")
        bad_in = os.path.join(td, "bad_in")
        bad_out = os.path.join(td, "bad_out")
        bad_work = os.path.join(td, "bad_work")
        os.makedirs(os.path.join(bad_in, "materials"))
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as f:
            f.write("this is not json {{{")
        with open(os.path.join(bad_in, "materials", "latin1.txt"), "wb") as f:
            f.write("caf\xe9 r\xe9sum\xe9".encode("latin-1"))
        rc, _, _ = run([sys.executable, "-B", main_py, "skeleton",
                        "--input", bad_in, "--output", bad_out,
                        "--work", bad_work])
        res = load_json_file(os.path.join(bad_out, "result.json"))
        check(rc == 0, "bad-input skeleton exits 0")
        check(res.get("status") == "input_error",
              "unparseable case.json -> input_error")
        check(bool(res.get("errors")), "bad JSON recorded in errors")

        enc_in = os.path.join(td, "enc_in")
        enc_out = os.path.join(td, "enc_out")
        enc_work = os.path.join(td, "enc_work")
        os.makedirs(enc_in)
        with open(os.path.join(enc_in, "case.json"), "wb") as f:
            f.write('{"case_id":"caf\xe9-case","task":"enc"}'.encode("latin-1"))
        rc, _, _ = run([sys.executable, "-B", main_py, "skeleton",
                        "--input", enc_in, "--output", enc_out,
                        "--work", enc_work])
        res = load_json_file(os.path.join(enc_out, "result.json"))
        check(rc == 0 and res.get("case_id") == "café-case",
              "non-UTF-8 case.json decoded via fallback")
        check(bool(res.get("degradations")),
              "encoding fallback recorded in degradations")

        log("=== 5. full synthetic pipeline ===")
        base = os.path.join(td, "case")
        case_in = os.path.join(base, "input")
        case_out = os.path.join(base, "output")
        case_work = os.path.join(base, "work")
        os.makedirs(os.path.join(case_in, "materials"))
        os.makedirs(case_work)

        write_json(os.path.join(case_in, "case.json"), {
            "case_id": "selftest-case",
            "task": "synthetic minimal call example (示意输入，非正式案例)",
            "output_contract": {
                "research_product": None,
                "evidence_matrix": None,
                "uncertainty": None,
                "custom_field": None,
            },
        })
        with open(os.path.join(case_in, "task.md"), "w", encoding="utf-8") as f:
            f.write("# 示意任务\n综合两份示意材料，并说明边界。\n")
        with open(os.path.join(case_in, "materials", "paper_a.md"),
                  "w", encoding="utf-8") as f:
            f.write("# paper_a\n## 2.1\n示例逐字引用 A。\n")
        with open(os.path.join(case_in, "materials", "paper_b.md"),
                  "w", encoding="utf-8") as f:
            f.write("# paper_b\n示例逐字引用 B。\n")

        with open(os.path.join(case_work, "decomposition.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 子问题拆解\n- SQ1: 甲机制（前置）\n- SQ2: 乙比较（依赖 SQ1）\n")
        with open(os.path.join(case_work, "search_plan.md"),
                  "w", encoding="utf-8") as f:
            f.write("# 检索计划\n顺序: SQ1 先、SQ2 后。来源: materials。停止: 证据饱和。\n")

        payload = {
            "status": "partial",
            "research_product": {
                "type": "synthesis",
                "question": "示意研究问题",
                "summary": "示意综合结论，仅用于自检。",
                "conclusions": [
                    {"id": "C1", "text": "结论一", "evidence_ids": ["E1"],
                     "strength": "established"},
                    {"id": "C2", "text": "结论二", "evidence_ids": ["E1", "E2"],
                     "strength": "inferred"},
                ],
            },
            "evidence_matrix": [
                {"id": "E1", "subquestion": "SQ1", "source": "materials/paper_a.md",
                 "locator": "§2.1", "quote": "示例逐字引用 A。", "claim": "支持结论一",
                 "judgment": "supports", "reason": "材料明确陈述",
                 "conclusion_ids": ["C1", "C2"]},
                {"id": "E2", "subquestion": "SQ2", "source": "materials/paper_b.md",
                 "locator": "p1", "quote": "示例逐字引用 B。", "claim": "支持结论二",
                 "judgment": "partial", "reason": "部分支持",
                 "conclusion_ids": ["C2"]},
            ],
            "uncertainty": {
                "conflicts": [{"id": "X1", "claim_a": "甲观点", "source_a": "paper_a",
                               "claim_b": "乙观点", "source_b": "paper_b",
                               "resolution": "并列呈现，材料不足以裁定"}],
                "biases": [], "gaps": ["示例空白：缺时间跨度数据"], "undetermined": [],
            },
            "sources": [{"id": "S1", "path": "materials/paper_a.md",
                         "title": "paper_a", "type": "md"}],
            "pending_human": ["示例：需人工确认某指标单位"],
        }
        write_json(os.path.join(case_work, "payload.json"), payload)

        rc, _, _ = run([sys.executable, "-B", main_py, "skeleton",
                        "--input", case_in, "--output", case_out,
                        "--work", case_work])
        res = load_json_file(os.path.join(case_out, "result.json"))
        check(rc == 0 and res.get("case_id") == "selftest-case",
              "skeleton reads case_id")
        check("custom_field" in res, "output_contract fields echoed (custom_field)")

        rc, _, _ = run([sys.executable, "-B", main_py, "merge",
                        "--payload", os.path.join(case_work, "payload.json"),
                        "--input", case_in, "--output", case_out,
                        "--work", case_work])
        res = load_json_file(os.path.join(case_out, "result.json"))
        check(rc == 0 and len(res.get("evidence_matrix", [])) == 2,
              "merge writes evidence_matrix")

        rc, _, _ = run([sys.executable, "-B", main_py, "finalize",
                        "--input", case_in, "--output", case_out,
                        "--work", case_work])
        check(rc == 0 and os.path.isfile(os.path.join(case_out, "report.md"))
              and os.path.isfile(os.path.join(case_out, "evidence_map.json")),
              "finalize writes report.md + evidence_map.json")

        rc, out, _ = run([sys.executable, "-B", main_py, "selfcheck",
                          "--input", case_in, "--output", case_out,
                          "--work", case_work])
        sc = json.loads(out)
        check(rc == 0 and sc.get("ok"), "selfcheck ok")
        check(sc["metrics"].get("backlink_rate") == 1.0,
              "evidence backlink rate == 1.0")
        check(sc["metrics"].get("locator_quote_rate") == 1.0,
              "locator+quote rate == 1.0")
        six = sc.get("six_key_points", {})
        check(all(six.values()),
              "all six key points detected: %s" % json.dumps(six, ensure_ascii=False))

    log("")
    if FAILED:
        log("RESULT: FAIL (%d failing check(s))" % len(FAILED))
        return 1
    log("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
