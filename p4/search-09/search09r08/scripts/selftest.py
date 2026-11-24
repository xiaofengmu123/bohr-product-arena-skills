#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-scoremap 交付前自检（独立入口，只依赖标准库）。

运行：python3 -B scripts/selftest.py
内容：
  1. 验证 frontmatter 三要素（name / description / version）存在；
  2. 验证文件体积与数量约束（总文件数 <= 14，总体积 <= 1.2MB，main.py < 30KB）；
  3. 运行 scripts/main.py self-test（空目录 / 坏 JSON / 缺输入 / 正常契约 四场景）；
  4. 运行 scripts/main.py --help，确认退出码 0。

全部通过退出码 0；任一失败退出码 1。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
MAX_TOTAL_BYTES = 1.2 * 1024 * 1024
MAX_FILES = 14
MAX_MAIN_BYTES = 30 * 1024


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_bytes().decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def check_frontmatter(skill_md):
    text = read_text(skill_md)
    if text is None:
        return ["SKILL.md unreadable"]
    if not text.startswith("---"):
        return ["SKILL.md missing YAML frontmatter (no leading ---)"]
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ["SKILL.md frontmatter unterminated"]
    block = m.group(1)
    errors = []
    for key in ("name", "description", "version"):
        if not re.search(r"^%s\s*:" % re.escape(key), block, re.M):
            errors.append("frontmatter missing '%s'" % key)
    return errors


def check_sizes():
    errors = []
    files = [p for p in SKILL_ROOT.rglob("*") if p.is_file()]
    if len(files) > MAX_FILES:
        errors.append("file count %d > %d" % (len(files), MAX_FILES))
    total = sum(p.stat().st_size for p in files)
    if total > MAX_TOTAL_BYTES:
        errors.append("total bytes %d > %d" % (total, MAX_TOTAL_BYTES))
    main = SKILL_ROOT / "scripts" / "main.py"
    if main.exists() and main.stat().st_size > MAX_MAIN_BYTES:
        errors.append("main.py bytes %d > %d" % (main.stat().st_size, MAX_MAIN_BYTES))
    return errors


def run_main(*args):
    proc = subprocess.run(
        [sys.executable, "-B", str(SKILL_ROOT / "scripts" / "main.py")] + list(args),
        cwd=str(SKILL_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def main():
    checks = []
    checks.append(("frontmatter", check_frontmatter(SKILL_ROOT / "SKILL.md")))
    checks.append(("sizes", check_sizes()))

    rc, out = run_main("--help")
    checks.append(("main.py --help", [] if rc == 0 else ["--help exit=%d" % rc]))

    rc, out = run_main("self-test")
    checks.append(("main.py self-test", [] if rc == 0 else ["self-test exit=%d\n%s" % (rc, out[-2000:])]))

    all_ok = True
    for name, errs in checks:
        status = "PASS" if not errs else "FAIL"
        if errs:
            all_ok = False
        print("[%s] %s" % (status, name))
        for e in errs:
            print("      - %s" % e)

    print("selftest %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
