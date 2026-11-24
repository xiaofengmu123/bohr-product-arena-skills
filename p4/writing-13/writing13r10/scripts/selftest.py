#!/usr/bin/env python3
"""Self-test for the fund-proposal writing Skill package.

Checks:
  1. scripts are stdlib-only (no third-party imports at top level)
  2. SKILL.md has valid name/description/version YAML frontmatter
  3. main.py bootstrap/inventory/record/check/finalize run end-to-end on a
     synthetic demo input and always produce a legal result.json
  4. result.json carries the required contract fields and honest status

Exit 0 = all PASS; exit 1 = at least one FAIL.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

REQUIRED_RESULT_FIELDS = [
    "case_id", "status", "artifact", "fact_source_map", "version_boundary",
    "sources", "uncertainty", "pending_human", "change_log",
]


def check_stdlib_only(path: str, allowed=()):
    problems = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"syntax error in {path}: {exc}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in sys.stdlib_module_names \
                        and alias.name not in allowed:
                    problems.append(f"non-stdlib import {alias.name} in {path}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] not in sys.stdlib_module_names \
                    and node.module not in allowed:
                problems.append(f"non-stdlib import {node.module} in {path}")
    return problems


def check_frontmatter(path: str):
    problems = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    if not text.startswith("---"):
        return ["SKILL.md missing YAML frontmatter"]
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return ["SKILL.md frontmatter not closed"]
    block = text[3:end].strip()
    for key in ("name:", "description:", "version:"):
        if key not in block:
            problems.append(f"frontmatter missing {key}")
    return problems


def run_main(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-B", os.path.join(HERE, "main.py")] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd or ROOT)


def make_demo_input(base: str) -> str:
    indir = os.path.join(base, "input")
    os.makedirs(indir, exist_ok=True)
    case = {
        "case_id": "demo-case-0001",
        "id": "demo-case-0001",
        "task": "为给定科研材料撰写立项依据章节（示例输入）",
        "output_contract": {
            "artifact": "章节稿",
            "fact_source_map": "事实与来源映射",
            "version_boundary": "版本与边界",
        },
        "required_fields": ["artifact", "fact_source_map", "version_boundary"],
    }
    with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(indir, "material.md"), "w", encoding="utf-8") as fh:
        fh.write("# 示例材料\n\n本文档仅用于自检，不冒充正式案例。\n")
    return indir


def main():
    failures = []
    checks = []

    # 1. stdlib-only (allow the three local modules in this package)
    local_mods = {"main", "evidence", "selftest"}
    for name in ("main.py", "evidence.py", "selftest.py"):
        problems = check_stdlib_only(os.path.join(HERE, name), allowed=local_mods)
        checks.append((f"stdlib-only {name}", not problems, problems))

    # 2. frontmatter
    fm = check_frontmatter(os.path.join(ROOT, "SKILL.md"))
    checks.append(("SKILL.md frontmatter", not fm, fm))

    # 3. end-to-end run on synthetic input
    with tempfile.TemporaryDirectory(prefix="skill_selftest_") as base:
        indir = make_demo_input(base)
        outdir = os.path.join(base, "output")
        workdir = os.path.join(base, "work")
        common = ["--input", indir, "--output", outdir, "--work", workdir,
                  "--case", os.path.join(indir, "case.json")]

        r = run_main(["bootstrap"] + common)
        if r.returncode != 0:
            checks.append(("bootstrap exit 0", False, [r.stderr]))
        res_path = os.path.join(outdir, "result.json")
        res_exists = os.path.exists(res_path)
        checks.append(("bootstrap writes result.json", res_exists, []))
        if res_exists:
            with open(res_path, "r", encoding="utf-8") as fh:
                res = json.load(fh)
            missing = [f for f in REQUIRED_RESULT_FIELDS if f not in res]
            checks.append(("result.json required fields", not missing, missing))
            checks.append(("case_id from case.json",
                           res.get("case_id") == "demo-case-0001", [res.get("case_id")]))
            checks.append(("contract_hint preserved",
                           "contract_hint" in res, []))

        run_main(["inventory"] + common)
        patch_path = os.path.join(base, "patch.json")
        with open(patch_path, "w", encoding="utf-8") as fh:
            json.dump({"evidence_bank": [
                {"claim": "示例材料存在", "kind": "claim",
                 "source_file": "material.md", "locator": "material.md#L1",
                 "quote": "# 示例材料", "status": "mapped"}]}, fh,
                ensure_ascii=False)
        run_main(["record", "--json-file", patch_path] + common)
        r = run_main(["check"] + common)
        # check may return 1 when gaps exist; both 0/1 acceptable for legality.
        run_main(["finalize", "--status", "partial"] + common)

        with open(res_path, "r", encoding="utf-8") as fh:
            res = json.load(fh)
        legal = res.get("case_id") and res.get("status") in (
            "in_progress", "done", "partial", "failed")
        checks.append(("final result.json legal", bool(legal),
                       [res.get("case_id"), res.get("status")]))
        checks.append(("fact_source_map mirrored",
                       len(res.get("fact_source_map", [])) == 1,
                       [len(res.get("fact_source_map", []))]))

    # 4. no fabricated benchmark scores in the human-readable docs
    import re
    suspicious = []
    decimal_score = re.compile(r"\d{2}\.\d+")
    for dirpath, _dirnames, filenames in os.walk(ROOT):
        for fn in filenames:
            if fn.endswith(".md"):
                p = os.path.join(dirpath, fn)
                with open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
                    text = fh.read()
                for m in decimal_score.findall(text):
                    suspicious.append(f"{fn}: decimal score {m}")
                if "榜首" in text:
                    suspicious.append(f"{fn}: leaderboard-rank claim")
    checks.append(("no fabricated scores", not suspicious, suspicious))

    print("== Self-test report ==")
    all_ok = True
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}")
        if detail:
            for d in detail[:8]:
                print(f"        - {d}")
        if not ok:
            all_ok = False
    print("==", "ALL PASS" if all_ok else "SOME FAILED", "==")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
