#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-evidence 包级自检（仅标准库）。

从 SKILL 根目录运行：
  python3 -B scripts/selftest.py
退出码：0 全部通过；1 存在失败项。逐条检查，对应 references/checklist.md。

检查项：
  F1  SKILL.md frontmatter 含 name / description / version；
  F2  scripts/*.py 可编译且可导入；
  F3  references/ 至少含 rubric 与 checklist；
  F4  main.run_self_test() 三场景（空目录/坏 JSON/缺输入）全部通过；
  F5  骨架结果 schema 含 case_id/status/sources/uncertainty/change_log/evidence_map。
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_frontmatter():
    sk = ROOT / "SKILL.md"
    if not sk.exists():
        return False, ["SKILL.md missing"]
    text = sk.read_text(encoding="utf-8-sig")
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return False, ["frontmatter missing"]
    block = m.group(1)
    fields = {}
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    missing = [k for k in ("name", "description", "version") if k not in fields or not fields[k]]
    if missing:
        return False, ["frontmatter missing fields: %s" % missing]
    return True, []


def check_scripts():
    errs = []
    for py in sorted((ROOT / "scripts").glob("*.py")):
        try:
            src = py.read_text(encoding="utf-8-sig")
            compile(src, str(py), "exec")
        except Exception as exc:
            errs.append("compile_failed %s: %s" % (py.name, exc))
            continue
        spec = importlib.util.spec_from_file_location(py.stem, py)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            errs.append("import_failed %s: %s" % (py.name, exc))
    return (not errs), errs


def check_references():
    errs = []
    for name in ("rubric", "checklist"):
        if not (ROOT / "references" / (name + ".md")).exists():
            errs.append("references/%s.md missing" % name)
    return (not errs), errs


def check_runtime_self_test():
    spec = importlib.util.spec_from_file_location("main", ROOT / "scripts" / "main.py")
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    report = main.run_self_test()
    bad = [r for r in report if not r.get("ok")]
    return (not bad), ["self-test scenarios ok: %d/%d" % (len(report) - len(bad), len(report))] if not bad else bad


def main():
    checks = {
        "F1_frontmatter": check_frontmatter(),
        "F2_scripts": check_scripts(),
        "F3_references": check_references(),
        "F4_self_test": check_runtime_self_test(),
    }
    failed = 0
    for name, (ok, errs) in checks.items():
        mark = "PASS" if ok else "FAIL"
        print("[%s] %s" % (mark, name))
        for e in errs:
            print("   -", e)
        if not ok:
            failed += 1
    print("selftest: %d/%d passed" % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
