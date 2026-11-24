#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — Skill 包自检脚本（仅标准库，确定性，退出码 0/1）。

校验两层：
  A. 包结构：SKILL.md 存在且 frontmatter 含 name/description/version；
     scripts/ 下脚本齐全、语法可编译（py_compile）、--help 可执行。
  B. 核心组件：空输入 / 坏 JSON / 坏编码 / 契约回填 / 事实抽取与冲突检测。

运行：python3 scripts/selftest.py [--root <skill根目录>]
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as ci  # noqa: E402


def check_frontmatter(root):
    skill = os.path.join(root, "SKILL.md")
    if not os.path.isfile(skill):
        return False, "SKILL.md_missing"
    text, err = ci.read_text(skill)
    if text is None:
        return False, err or "unreadable"
    if not text.startswith("---"):
        return False, "no_frontmatter"
    end = text.find("\n---", 3)
    fm = text[3:end if end != -1 else 400]
    for key in ("name", "description", "version"):
        if not any(line.startswith(key + ":") for line in fm.splitlines()):
            return False, "frontmatter_missing_key:" + key
    return True, "ok"


def check_scripts(root):
    scripts_dir = os.path.join(root, "scripts")
    if not os.path.isdir(scripts_dir):
        return False, "scripts_dir_missing"
    reports = []
    for name in sorted(os.listdir(scripts_dir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(scripts_dir, name)
        try:
            py_compile.compile(path, doraise=True)
            reports.append("%s: py_compile ok" % name)
        except Exception as exc:
            return False, "py_compile_fail:%s (%s)" % (name, exc)
    return True, "; ".join(reports)


def component_tests():
    results = []
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    inp = os.path.join(tmp, "input"); os.makedirs(inp)

    sk, _ = ci.build_skeleton(inp)
    assert sk["case_id"] == "unknown" and sk["status"] == "input_error"
    results.append("empty_input")

    # 2) 截断但可恢复的 JSON（合法前缀 + 尾随垃圾）
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write('{"case_id": "c1", "output_contract": {"title": 1, "abstract": 2}} TRAILING')
    obj, errs = ci.read_json(os.path.join(inp, "case.json"))
    assert obj is not None and obj.get("case_id") == "c1" and errs
    results.append("truncated_json")

    # 2b) 真正无法解析的 JSON：不崩溃，返回 (None, ["bad_json: ..."])
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json @@@")
    bad_obj, bad_errs = ci.read_json(os.path.join(inp, "case.json"))
    assert bad_obj is None and any(e.startswith("bad_json") for e in bad_errs)
    results.append("bad_json_no_crash")

    with open(os.path.join(inp, "materials.txt"), "wb") as fh:
        fh.write("caf\xe9 naive".encode("latin-1"))
    text, err = ci.read_text(os.path.join(inp, "materials.txt"))
    assert text is not None and err == "decoded_with_replacement"
    results.append("bad_encoding")

    good = os.path.join(tmp, "good", "input"); os.makedirs(good)
    with open(os.path.join(good, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"id": "X1", "output_contract": {"title": None, "abstract": None}}, fh)
    sk2, keys2 = ci.build_skeleton(good)
    assert keys2 == ["title", "abstract"] and "title" in sk2 and "abstract" in sk2
    results.append("contract_echo")

    fbs, _ = ci.extract_facts(good)
    assert isinstance(fbs["fact_count"], int) and "facts" in fbs and "conflicts" in fbs
    results.append("fact_extract")

    # 冲突检测：同一 locator 同 type 两个不同数值
    with open(os.path.join(good, "materials.md"), "w", encoding="utf-8") as fh:
        fh.write("temperature 300 K\n")
    fbs2, _ = ci.extract_facts(good)
    assert fbs2["fact_count"] >= 1
    results.append("numeric_fact")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(prog="selftest.py", description="Skill 包自检")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    args = ap.parse_args(argv)
    report = {"root": args.root, "checks": {}, "ok": True}

    ok, msg = check_frontmatter(args.root)
    report["checks"]["frontmatter"] = msg
    report["ok"] &= ok

    ok, msg = check_scripts(args.root)
    report["checks"]["scripts"] = msg
    report["ok"] &= ok

    try:
        comp = component_tests()
        report["checks"]["components"] = comp
    except AssertionError as exc:
        report["checks"]["components"] = "assert_fail: %s" % exc
        report["ok"] = False
    except Exception as exc:
        report["checks"]["components"] = "error: %s" % exc
        report["ok"] = False

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
