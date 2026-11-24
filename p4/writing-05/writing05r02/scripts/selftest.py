#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 交付前自检（纯标准库，不依赖真实案例）。

检查项：
  1. SKILL.md 存在且 YAML frontmatter 含 name / description / version
  2. 脚本仅用标准库、可被 python 编译（py_compile）
  3. 文件数与总体积在约束内（<=14 文件、<=1.2MB）
  4. 运行 scripts/main.py selftest 的库级自检（空目录/坏JSON/坏编码/幂等/locator）
  5. main.py --help 退出码 0

用法：python scripts/selftest.py
"""
from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def find_files(root):
    files = []
    for dirpath, _dirs, fns in os.walk(root):
        for fn in fns:
            files.append(os.path.join(dirpath, fn))
    return files


def check_frontmatter(skill_path):
    if not os.path.exists(skill_path):
        return False, "SKILL.md 缺失"
    with open(skill_path, "r", encoding="utf-8") as f:
        head = f.read(4000)
    if not head.lstrip().startswith("---"):
        return False, "frontmatter 不以 --- 开头"
    body = head.lstrip()[3:]
    idx = body.find("---")
    if idx < 0:
        return False, "frontmatter 无闭合 ---"
    fm = body[:idx]
    for key in ("name:", "description:", "version:"):
        if key not in fm:
            return False, "frontmatter 缺 %s" % key
    return True, "ok"


def main():
    results = []
    skill = os.path.join(ROOT, "SKILL.md")
    ok, detail = check_frontmatter(skill)
    results.append(("SKILL.md frontmatter", ok, detail))

    # 脚本编译检查
    for fn in ("main.py", "core_impl.py", "selftest.py"):
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            results.append(("compile %s" % fn, False, "缺失"))
            continue
        try:
            py_compile.compile(p, doraise=True)
            results.append(("compile %s" % fn, True, "ok"))
        except Exception as e:  # noqa
            results.append(("compile %s" % fn, False, str(e)))

    # 文件数 / 体积
    files = find_files(ROOT)
    total = sum(os.path.getsize(f) for f in files)
    results.append(("file_count<=14", len(files) <= 14, "%d files" % len(files)))
    results.append(("total_size<=1.2MB", total <= 1_200_000, "%.1f KB" % (total / 1024)))

    # main.py 体积 <30KB
    mp = os.path.join(HERE, "main.py")
    if os.path.exists(mp):
        sz = os.path.getsize(mp)
        results.append(("main.py<30KB", sz < 30_000, "%.1f KB" % (sz / 1024)))

    # 运行库级 selftest
    r = subprocess.run([sys.executable, os.path.join(HERE, "main.py"), "selftest"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    lib_ok = r.returncode == 0
    detail = (r.stdout or "").strip()[:300] or (r.stderr or "").strip()[:300]
    results.append(("main.py selftest", lib_ok, detail))

    # --help 退出码 0
    r2 = subprocess.run([sys.executable, os.path.join(HERE, "main.py"), "--help"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    results.append(("main.py --help", r2.returncode == 0, "rc=%d" % r2.returncode))

    print(json.dumps([{"check": n, "ok": o, "detail": d} for n, o, d in results],
                     ensure_ascii=False, indent=2))
    all_ok = all(o for _, o, _ in results)
    print("ALL_OK=%s" % ("true" if all_ok else "false"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
