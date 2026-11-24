#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 对 Skill 包本体做自检（不运行正式案例）。

检查项（对应"可靠体验"与"生态贡献"评分维度）：
  1. SKILL.md 存在，且 YAML frontmatter 含 name/description/version；
  2. SKILL.md 正文（frontmatter 之后）不超过 180 行；
  3. SKILL.md 引用的 scripts/*.py 与 references/*.md 均真实存在；
  4. 每个脚本仅 import 标准库（AST 检查）；
  5. 每个脚本 `--help` 退出码为 0；
  6. 包内无预写测试产物（.pptx/.pdf/.png/.jpg/.jpeg）与伪造 result.json。

退出码：0 全部通过；1 存在失败项。
用法：python scripts/selftest.py [--root <skill 包根目录>]
"""

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    block = m.group(1)
    fields = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip().strip("\"'")
    body = text[m.end():]
    return fields, body


def stdlib_names():
    if hasattr(sys, "stdlib_module_names"):
        return set(sys.stdlib_module_names)
    return {
        "argparse", "ast", "json", "os", "re", "struct", "subprocess", "sys",
        "zipfile", "datetime", "pathlib", "xml", "shutil", "io", "math",
        "random", "collections", "itertools", "functools", "string", "time",
        "unicodedata", "hashlib", "base64", "csv", "glob", "gzip", "tempfile",
        "__future__",
    }


def stdlib_only(script):
    try:
        tree = ast.parse(Path(script).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None, ["cannot parse"]
    std = stdlib_names()
    bad = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in std:
                    bad.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top not in std:
                    bad.add(top)
    return tree, sorted(bad)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Skill 包本体自检")
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent

    skill = root / "SKILL.md"
    if not skill.is_file():
        print("FAIL: SKILL.md not found at %s" % skill)
        return 1

    text = skill.read_text(encoding="utf-8-sig")
    fields, body = parse_frontmatter(text)
    failures = []
    checks = {}

    checks["frontmatter_ok"] = bool(
        fields and fields.get("name") and fields.get("description") and fields.get("version")
    )
    if not checks["frontmatter_ok"]:
        failures.append("SKILL.md frontmatter missing name/description/version")

    body_lines = body.count("\n") + (1 if body else 0)
    checks["body_lines"] = body_lines
    checks["body_within_180"] = body_lines <= 180
    if not checks["body_within_180"]:
        failures.append("SKILL.md body %d lines > 180" % body_lines)

    ref_scripts = sorted(set(re.findall(r"scripts/[\w.\-]+\.py", text)))
    ref_docs = sorted(set(re.findall(r"references/[\w.\-]+\.md", text)))
    missing = []
    for rel in ref_scripts + ref_docs:
        if not (root / rel).is_file():
            missing.append(rel)
    checks["referenced_files_exist"] = not missing
    if missing:
        failures.append("missing referenced files: %s" % ", ".join(missing))

    scripts_dir = root / "scripts"
    scripts = sorted(scripts_dir.glob("*.py")) if scripts_dir.is_dir() else []
    checks["script_count"] = len(scripts)
    for sp in scripts:
        _tree, bad = stdlib_only(sp)
        if bad:
            failures.append("%s imports non-stdlib: %s" % (sp.name, ", ".join(bad)))
        try:
            r = subprocess.run(
                [sys.executable, str(sp), "--help"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                failures.append("%s --help exit=%d stderr=%s" % (
                    sp.name, r.returncode, (r.stderr or "").strip()[:120]))
        except (subprocess.TimeoutExpired, OSError) as e:
            failures.append("%s --help failed: %s" % (sp.name, e))

    forbidden_ext = {".pptx", ".pdf", ".png", ".jpg", ".jpeg"}
    artifacts = []
    for f in root.rglob("*"):
        if f.is_file() and f.suffix.lower() in forbidden_ext:
            artifacts.append(str(f.relative_to(root)))
        if f.is_file() and f.name == "result.json":
            artifacts.append(str(f.relative_to(root)))
    checks["no_prebuilt_artifacts"] = not artifacts
    if artifacts:
        failures.append("prebuilt artifacts found: %s" % ", ".join(artifacts))

    print("== selftest: %s ==" % root)
    for k, v in checks.items():
        print("  [%s] %s = %s" % ("PASS" if v else "FAIL", k, v))
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  - %s" % f)
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
