#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the academic-translation-bilingual-writer Skill package.

Standard library only. Verifies the package structure and the result.json
contract, then runs an end-to-end demo in a temporary directory using only the
public CLI of the sibling scripts. Prints a PASS/FAIL checklist and exits 0 on
pass, non-zero otherwise.

Run from the Skill root (the directory containing SKILL.md), or pass
--skill-root explicitly. This script is an authoring-time check; at evaluation
runtime the platform drives the Agent, not this script.
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)

LIMITS = {"files": 14, "total_bytes": 1_200_000, "per_file_bytes": 500_000}

DEMO_CASE = {
    "case_id": "selftest-demo-0001",
    "task": "将给定的英文摘要翻译为中文，保持术语、数字与引用一致。",
    "direction": "EN->ZH",
    "output_form": "纯译文",
    "output_contract": {
        "deliverables": [],
        "fact_source_map": [],
        "version_boundary": {
            "modifications": [],
            "human_confirm": [],
            "material_gaps": [],
            "anomalies": [],
            "editable": True,
        },
    },
}

DEMO_TASK = "# demo task\n\n将摘要译为中文，术语与数字保持一致。\n"

DEMO_SOURCE = (
    "Machine learning models can predict patient outcomes with high accuracy.\n\n"
    "We recruited 120 patients (mean age 58.4 years, 45% female). The 5-year\n"
    "survival rate was 87.5% [12,13], significantly higher than the control\n"
    "group (P < 0.05). Approximately 20% of cases did not complete follow-up.\n"
)

DEMO_TARGET = (
    "机器学习模型可以高精度地预测患者结局。\n\n"
    "我们招募了 120 名患者（平均年龄 58.4 岁，45% 为女性）。5 年生存率为\n"
    "87.5% [12,13]，显著高于对照组（P < 0.05）。约 20% 的病例未完成随访。\n"
)

DEMO_GLOSSARY = {
    "terms": [
        {"canonical": "机器学习", "variants": ["machine learning", "ML"],
         "context": "AI", "discipline": "computer science"},
        {"canonical": "生存率", "variants": ["survival rate"],
         "context": "oncology", "discipline": "medicine"},
    ]
}


def read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_frontmatter(text: str):
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    block = text[3:end].strip()
    meta = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip("'\"")
    return meta


def run_py(script: str, args, cwd=None, timeout=60):
    return subprocess.run(
        [sys.executable, "-B", script] + args,
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


def check_package(checks):
    # 1. SKILL.md present with valid frontmatter
    skill_path = os.path.join(SKILL_ROOT, "SKILL.md")
    if os.path.isfile(skill_path):
        text = read_text(skill_path)
        meta = parse_frontmatter(text)
        checks.append(("SKILL.md exists", True, skill_path))
        checks.append(("SKILL.md frontmatter name/description/version",
                       bool(meta and meta.get("name") and meta.get("description")
                            and meta.get("version")),
                       json.dumps(meta, ensure_ascii=False) if meta else "no frontmatter"))
        body_lines = [ln for ln in text.splitlines() if ln.strip()]
        checks.append(("SKILL.md body <= 180 non-empty lines", len(body_lines) <= 180,
                       "%d lines" % len(body_lines)))
    else:
        checks.append(("SKILL.md exists", False, skill_path))

    # 2. scripts compile (bytecode written to temp dir, never into the package)
    scripts = [os.path.join(HERE, n) for n in
               ("main.py", "check_translation.py", "selftest.py")]
    for s in scripts:
        name = os.path.basename(s)
        if os.path.isfile(s):
            try:
                cfile = os.path.join(tempfile.gettempdir(), name + ".pyc")
                py_compile.compile(s, cfile=cfile, doraise=True)
                checks.append(("script compiles: %s" % name, True, s))
            except py_compile.PyCompileError as exc:
                checks.append(("script compiles: %s" % name, False, str(exc)))
        else:
            checks.append(("script compiles: %s" % name, False, "missing"))

    # 3. references mentioned in SKILL.md exist
    if os.path.isfile(skill_path):
        text = read_text(skill_path)
        for ref in ("references/checklist.md", "references/traceability.md"):
            if ref in text:
                full = os.path.join(SKILL_ROOT, ref)
                checks.append(("referenced file exists: %s" % ref,
                               os.path.isfile(full), full))

    # 4. size / file-count limits
    total, count = 0, 0
    for root, _dirs, names in os.walk(SKILL_ROOT):
        for name in names:
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            total += size
            count += 1
            if size > LIMITS["per_file_bytes"]:
                checks.append(("file within 500KB: %s" % name, False, "%d bytes" % size))
    checks.append(("file count <= %d" % LIMITS["files"], count <= LIMITS["files"],
                   "%d files" % count))
    checks.append(("total size <= %.0fKB" % (LIMITS["total_bytes"] / 1000),
                   total <= LIMITS["total_bytes"], "%d bytes" % total))
    return checks


def run_demo(checks):
    tmp = tempfile.mkdtemp(prefix="selftest-")
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump(DEMO_CASE, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write(DEMO_TASK)

    main = os.path.join(HERE, "main.py")
    checker = os.path.join(HERE, "check_translation.py")

    r = run_py(main, ["brief", "--input", inp, "--out", os.path.join(work, "brief.json")])
    checks.append(("main.py brief exit 0", r.returncode == 0, r.stderr.strip() or r.stdout.strip()[:120]))

    result = os.path.join(out, "result.json")
    r = run_py(main, ["init", "--input", inp, "--out", result, "--work", work])
    checks.append(("main.py init exit 0", r.returncode == 0, r.stderr.strip() or r.stdout.strip()[:120]))

    data = json.loads(read_text(result))
    checks.append(("skeleton has case_id", data.get("case_id") == "selftest-demo-0001",
                   str(data.get("case_id"))))
    checks.append(("skeleton status=in_progress", data.get("status") == "in_progress",
                   str(data.get("status"))))
    for field in ("deliverables", "fact_source_map", "version_boundary",
                  "sources", "uncertainty", "pending_human", "limitations", "change_log"):
        checks.append(("skeleton field present: %s" % field, field in data, field))

    src = os.path.join(tmp, "source.md")
    tgt = os.path.join(tmp, "target.md")
    gloss = os.path.join(tmp, "glossary.json")
    with open(src, "w", encoding="utf-8") as f:
        f.write(DEMO_SOURCE)
    with open(tgt, "w", encoding="utf-8") as f:
        f.write(DEMO_TARGET)
    with open(gloss, "w", encoding="utf-8") as f:
        json.dump(DEMO_GLOSSARY, f, ensure_ascii=False)
    report = os.path.join(work, "translation_report.json")
    r = run_py(checker, ["--source", src, "--target", tgt,
                         "--glossary", gloss, "--out", report])
    checks.append(("check_translation.py exit 0 on clean demo", r.returncode == 0,
                   r.stderr.strip() or r.stdout.strip()[:120]))
    rep = json.loads(read_text(report))
    checks.append(("checker reports ok=true on clean demo", rep.get("ok") is True,
                   "ok=%s" % rep.get("ok")))
    checks.append(("checker produces manual_verify queue", isinstance(rep.get("manual_verify"), list),
                   "%d items" % len(rep.get("manual_verify", []))))

    r = run_py(main, ["validate", "--out", result, "--case", os.path.join(inp, "case.json")])
    checks.append(("main.py validate exit 0", r.returncode == 0,
                   r.stderr.strip() or r.stdout.strip()[:120]))

    r = run_py(main, ["finalize", "--out", result, "--status", "success"])
    checks.append(("main.py finalize exit 0", r.returncode == 0, r.stderr.strip()))
    data = json.loads(read_text(result))
    checks.append(("finalize sets status=success", data.get("status") == "success",
                   str(data.get("status"))))
    checks.append(("change_log has init+finalize", len(data.get("change_log", [])) >= 2,
                   "%d entries" % len(data.get("change_log", []))))
    return checks


def main(argv=None) -> int:
    global SKILL_ROOT
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--skill-root", default=SKILL_ROOT,
                   help="directory containing SKILL.md (default: parent of scripts/)")
    args = p.parse_args(argv)
    SKILL_ROOT = os.path.abspath(args.skill_root)

    checks = []
    check_package(checks)
    run_demo(checks)

    failed = [c for c in checks if not c[1]]
    print("=" * 72)
    print("Skill self-test: %d checks, %d failed" % (len(checks), len(failed)))
    print("=" * 72)
    for name, ok, detail in checks:
        print("[%s] %s  %s" % ("PASS" if ok else "FAIL", name,
                                "" if ok else ("-> " + str(detail))))
    if failed:
        print("\nFAIL")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
