#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检：结构、frontmatter、脚本语法、标准库约束与端到端 dry-run。

检查项:
  - SKILL.md 存在且 YAML frontmatter 含 name/description/version；
  - 正文含输入/输出契约、执行流程、自检清单、降级策略、预算数字等关键要素；
  - scripts/scaffold.py 与 scripts/evidence_check.py 存在、可解析、仅标准库、--help 退出码 0；
  - 包文件数 ≤14、总体积 ≤1.2MB；
  - 文本不含疑似照抄/编造评分标记；
  - 临时目录 dry-run：scaffold.py 落骨架 → evidence_check.py 校验，全程退出码 0。

退出码：0 表示全部通过；1 表示存在失败项。用法见 --help。
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(HERE)

STDLIB = {
    "argparse", "ast", "base64", "collections", "copy", "csv", "datetime", "difflib",
    "enum", "functools", "glob", "hashlib", "io", "itertools", "json", "math", "os",
    "pathlib", "pickle", "platform", "random", "re", "shutil", "statistics", "string",
    "subprocess", "sys", "tempfile", "textwrap", "time", "timeit", "types", "typing",
    "unicodedata", "unittest", "urllib", "uuid", "warnings", "xml", "zoneinfo",
    "dataclasses", "contextlib", "secrets", "getpass", "html", "http", "email",
    "sqlite3", "logging", "configparser", "gzip", "zipfile", "tarfile", "bz2", "lzma",
    "struct", "array", "binascii", "codecs", "locale", "threading", "queue", "socket",
    "ssl", "select", "signal", "weakref", "operator",
}
if hasattr(sys, "stdlib_module_names"):
    STDLIB |= set(sys.stdlib_module_names)

REQUIRED = [
    "输入契约", "输出契约", "执行流程", "自检清单", "降级", "900", "64",
    "result.json", "/app/output", "scaffold.py", "evidence_check.py", "selftest.py",
]
BANNED = ["赛道榜首", "71.6667", "71.6", "得分 8", "榜首"]


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read(), None
    except OSError as e:
        return None, str(e)


def check_frontmatter(root):
    path = os.path.join(root, "SKILL.md")
    if not os.path.isfile(path):
        return False, "SKILL.md 不存在"
    text, err = read_text(path)
    if err is not None:
        return False, "SKILL.md 不可读: %s" % err
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return False, "缺少闭合的 YAML frontmatter"
    fm = m.group(1)
    missing = [k for k in ("name", "description", "version")
               if not re.search(r"(?m)^%s:\s*\S" % k, fm)]
    if missing:
        return False, "frontmatter 缺字段: %s" % ", ".join(missing)
    return True, "frontmatter 含 name/description/version"


def check_sections(root):
    text, err = read_text(os.path.join(root, "SKILL.md"))
    if err is not None:
        return False, "SKILL.md 不可读: %s" % err
    m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.S)
    body = m.group(1) if m else (text or "")
    missing = [k for k in REQUIRED if k not in body]
    if missing:
        return False, "正文缺关键要素: %s" % ", ".join(missing)
    return True, "关键要素齐全（%d 项）" % len(REQUIRED)


def check_scripts_exist(root):
    scripts = os.path.join(root, "scripts")
    missing = [s for s in ("scaffold.py", "evidence_check.py")
               if not os.path.isfile(os.path.join(scripts, s))]
    if missing:
        return False, "缺少脚本: %s" % ", ".join(missing)
    return True, "scaffold.py / evidence_check.py 存在"


def check_stdlib(scripts_dir):
    bad = []
    if not os.path.isdir(scripts_dir):
        return False, "scripts 目录不存在"
    for name in sorted(os.listdir(scripts_dir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(scripts_dir, name)
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                src = f.read()
            tree = ast.parse(src)
        except SyntaxError as e:
            bad.append("%s(语法错误 %s)" % (name, e))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    if top not in STDLIB:
                        bad.append("%s(import %s)" % (name, top))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top not in STDLIB:
                        bad.append("%s(from %s import)" % (name, top))
    if bad:
        return False, "非标准库/语法问题: %s" % "; ".join(bad)
    return True, "脚本仅标准库且可解析"


def check_budget(root):
    files = []
    for r, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in fs:
            files.append(os.path.join(r, f))
    count = len(files)
    total = sum(os.path.getsize(p) for p in files)
    ok = count <= 14 and total <= 1_200_000
    return ok, "文件数 %d/14，体积 %d/1.2MB" % (count, total)


def check_no_fabrication(root):
    hits = []
    for r, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in fs:
            if not f.endswith((".md", ".py")):
                continue
            if f == "selftest.py":
                continue  # 本文件是守卫本身，跳过以免命中自身词表
            p = os.path.join(r, f)
            t, err = read_text(p)
            if err is not None or t is None:
                continue
            for b in BANNED:
                if b in t:
                    hits.append("%s:%s" % (f, b))
    if hits:
        return False, "疑似照抄/编造评分标记: %s" % "; ".join(hits)
    return True, "无编造评分标记"


def check_help(scripts_dir):
    bad = []
    for name in ("scaffold.py", "evidence_check.py", "selftest.py"):
        p = os.path.join(scripts_dir, name)
        if not os.path.isfile(p):
            continue
        pr = subprocess.run([sys.executable, p, "--help"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
        if pr.returncode != 0:
            bad.append(name)
    if bad:
        return False, "--help 非 0: %s" % ", ".join(bad)
    return True, "各脚本 --help 退出码 0"


def dry_run(scripts_dir):
    with tempfile.TemporaryDirectory() as td:
        case_path = os.path.join(td, "case.json")
        task_path = os.path.join(td, "task.md")
        out_path = os.path.join(td, "result.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"id": "dry-run-case", "armiId": "ARMI:test",
                       "output_contract": {"report_goal": None, "pages": []}}, f)
        with open(task_path, "w", encoding="utf-8") as f:
            f.write("# task\n")
        scaffold = os.path.join(scripts_dir, "scaffold.py")
        p = subprocess.run([sys.executable, scaffold, "--input", case_path,
                            "--task", task_path, "--output", out_path],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        if p.returncode != 0:
            return False, "scaffold 退出码 %s: %s" % (p.returncode, p.stderr.strip())
        try:
            with open(out_path, encoding="utf-8") as f:
                r = json.load(f)
        except Exception as e:
            return False, "result.json 不可解析: %s" % e
        if r.get("case_id") != "dry-run-case" or r.get("status") != "partial":
            return False, "骨架 case_id/status 不符"
        if not isinstance(r.get("output_contract"), dict):
            return False, "缺 output_contract 快照"
        for k in ("report_goal", "pages"):
            if k not in r:
                return False, "output_contract 字段未平铺: %s" % k
        ev = os.path.join(scripts_dir, "evidence_check.py")
        p2 = subprocess.run([sys.executable, ev, "--result", out_path, "--work", td],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=30)
        if p2.returncode != 0:
            return False, "evidence_check 退出码 %s: %s" % (p2.returncode, p2.stderr.strip())
        return True, "端到端 dry-run 通过"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill", default=DEFAULT_ROOT, help="SKILL 包根目录（默认 scripts/ 的父目录）")
    args = ap.parse_args()
    root = os.path.abspath(args.skill)
    scripts_dir = os.path.join(root, "scripts")

    checks = []

    def run(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, "异常: %r" % e
        checks.append({"check": name, "ok": ok, "detail": detail})

    run("frontmatter", lambda: check_frontmatter(root))
    run("required_sections", lambda: check_sections(root))
    run("scripts_exist", lambda: check_scripts_exist(root))
    run("stdlib_only", lambda: check_stdlib(scripts_dir))
    run("file_budget", lambda: check_budget(root))
    run("no_fabrication", lambda: check_no_fabrication(root))
    run("help_exit0", lambda: check_help(scripts_dir))
    run("dry_run", lambda: dry_run(scripts_dir))

    ok = all(c["ok"] for c in checks)
    print(json.dumps({"ok": ok, "root": root, "checks": checks},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
