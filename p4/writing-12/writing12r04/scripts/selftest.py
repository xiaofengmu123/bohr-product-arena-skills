#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 包自检脚本。

验证：
  1. SKILL.md 存在且 YAML frontmatter 含 name/description/version；
  2. 所有脚本 `--help` 可执行（退出码 0）；
  3. 空输入、坏 JSON、合法 output_contract 三类场景的降级行为正确；
  4. 证据 index/quote 可逐字取证；
  5. latex_tools.py 的日志分类能识别典型错误；
  6. 无编造评测分数的自述；
  7. 包体量与文件数在上限内。

运行：python3 scripts/selftest.py [--root 包根目录]
退出码：0 全部通过；1 有失败项。
所有临时产物写入系统临时目录并自动清理，不生成、不冒充正式案例结果。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

TESTS = []


def root_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, cwd, timeout=90):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as exc:
        return -1, "", "%s: %s" % (type(exc).__name__, exc)


def py():
    return sys.executable


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


@test("SKILL.md frontmatter 完整")
def t_frontmatter(root):
    p = os.path.join(root, "SKILL.md")
    if not os.path.isfile(p):
        return False, "SKILL.md missing"
    text = open(p, encoding="utf-8").read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return False, "no YAML frontmatter"
    fm = m.group(1)
    missing = [k for k in ("name", "description", "version")
               if not re.search(r"^%s:" % k, fm, re.M)]
    if missing:
        return False, "frontmatter missing keys: %s" % ", ".join(missing)
    name_m = re.search(r"^name:\s*(\S+)", fm, re.M)
    if name_m and not re.match(r"^[a-z0-9][a-z0-9-]*$", name_m.group(1)):
        return False, "invalid skill name %r" % name_m.group(1)
    return True, "frontmatter ok"


@test("scripts --help 可执行")
def t_help(root):
    scripts_dir = os.path.join(root, "scripts")
    if not os.path.isdir(scripts_dir):
        return False, "scripts/ missing"
    scripts = sorted(f for f in os.listdir(scripts_dir) if f.endswith(".py"))
    if not scripts:
        return False, "no python scripts"
    fails = []
    for s in scripts:
        rc, _, err = run([py(), "-B", os.path.join("scripts", s), "--help"], root)
        if rc != 0:
            fails.append("%s(rc=%d)" % (s, rc))
    return not fails, "checked %d scripts; fails=%s" % (len(scripts), fails or "none")


@test("空输入优雅降级")
def t_empty(root):
    with tempfile.TemporaryDirectory() as td:
        in_dir = os.path.join(td, "in")
        os.makedirs(in_dir)
        out_dir = os.path.join(td, "out")
        work_dir = os.path.join(td, "work")
        rc, _, err = run([py(), "-B", os.path.join(root, "scripts", "main.py"),
                          "bootstrap", "--input", in_dir, "--output", out_dir,
                          "--work", work_dir], td)
        if rc != 0:
            return False, "bootstrap rc=%d err=%s" % (rc, err)
        rp = os.path.join(out_dir, "result.json")
        if not os.path.isfile(rp):
            return False, "result.json missing"
        obj = json.load(open(rp, encoding="utf-8"))
        if obj.get("case_id") != "unknown" or obj.get("status") != "input_error":
            return False, "unexpected case_id/status: %r/%r" % (
                obj.get("case_id"), obj.get("status"))
        return True, "empty input -> status=input_error"


@test("坏 JSON 优雅降级")
def t_badjson(root):
    with tempfile.TemporaryDirectory() as td:
        in_dir = os.path.join(td, "in")
        os.makedirs(in_dir)
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json ")
        out_dir = os.path.join(td, "out")
        work_dir = os.path.join(td, "work")
        rc, _, err = run([py(), "-B", os.path.join(root, "scripts", "main.py"),
                          "bootstrap", "--input", in_dir, "--output", out_dir,
                          "--work", work_dir], td)
        if rc != 0:
            return False, "bootstrap rc=%d err=%s" % (rc, err)
        obj = json.load(open(os.path.join(out_dir, "result.json"), encoding="utf-8"))
        if obj.get("status") != "input_error" or not obj.get("errors"):
            return False, "unexpected status/errors: %r" % obj
        return True, "bad json -> status=input_error with errors recorded"


@test("output_contract 原样回填")
def t_contract(root):
    with tempfile.TemporaryDirectory() as td:
        in_dir = os.path.join(td, "in")
        os.makedirs(in_dir)
        case = {"case_id": "demo-case",
                "output_contract": {"title": "T", "abstract": "A", "body": None}}
        with open(os.path.join(in_dir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        with open(os.path.join(in_dir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n")
        out_dir = os.path.join(td, "out")
        work_dir = os.path.join(td, "work")
        rc, _, err = run([py(), "-B", os.path.join(root, "scripts", "main.py"),
                          "bootstrap", "--input", in_dir, "--output", out_dir,
                          "--work", work_dir], td)
        if rc != 0:
            return False, "bootstrap rc=%d %s" % (rc, err)
        rc, _, err = run([py(), "-B", os.path.join(root, "scripts", "main.py"),
                          "finalize", "--input", in_dir, "--output", out_dir,
                          "--work", work_dir], td)
        if rc != 0:
            return False, "finalize rc=%d %s" % (rc, err)
        rc, out, err = run([py(), "-B", os.path.join(root, "scripts", "main.py"),
                            "selfcheck", "--input", in_dir, "--output", out_dir,
                            "--work", work_dir], td)
        if rc != 0:
            return False, "selfcheck rc=%d out=%s err=%s" % (rc, out, err)
        obj = json.load(open(os.path.join(out_dir, "result.json"), encoding="utf-8"))
        for k in ("title", "abstract", "body"):
            if k not in obj:
                return False, "contract key %r missing" % k
        return True, "contract keys echoed, selfcheck passed"


@test("证据 index + quote 逐字取证")
def t_evidence(root):
    with tempfile.TemporaryDirectory() as td:
        in_dir = os.path.join(td, "in")
        os.makedirs(in_dir)
        with open(os.path.join(in_dir, "mat.md"), "w", encoding="utf-8") as fh:
            fh.write("line one\nline two: 42 km/s\nline three\n")
        idx = os.path.join(td, "idx.json")
        rc, _, err = run([py(), "-B", os.path.join(root, "scripts", "main.py"),
                          "index", "--root", in_dir, "--out", idx], td)
        if rc != 0:
            return False, "index rc=%d %s" % (rc, err)
        obj = json.load(open(idx, encoding="utf-8"))
        if not any(f.get("rel") == "mat.md" and f.get("lines")
                   for f in obj.get("files", [])):
            return False, "mat.md not indexed with lines"
        rc, out, err = run([py(), "-B", os.path.join(root, "scripts", "main.py"),
                            "quote", "--file", os.path.join(in_dir, "mat.md"),
                            "--start", "2", "--end", "2"], td)
        if rc != 0:
            return False, "quote rc=%d %s" % (rc, err)
        q = json.loads(out)
        if "42 km/s" not in q.get("text", ""):
            return False, "quote text mismatch: %r" % q
        return True, "index+quote verified"


@test("编译日志分类")
def t_classify(root):
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "test.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("! Undefined control sequence.\nl.10 \\foo\n"
                     "! LaTeX Error: File `fancyhdr.sty' not found.\nl.3\n")
        rc, out, err = run([py(), "-B", os.path.join(root, "scripts",
                            "latex_tools.py"), "classify-log", "--log", log], td)
        if rc != 0:
            return False, "classify rc=%d %s" % (rc, err)
        obj = json.loads(out)
        cats = {e.get("category") for e in obj.get("errors", [])}
        need = {"undefined_control_sequence", "missing_package"}
        if not need.issubset(cats):
            return False, "categories missing: %r (want %r)" % (cats, need)
        return True, "categories=%s" % sorted(cats)


@test("无编造评测分数自述")
def t_nofabrication(root):
    paths = [os.path.join(root, "SKILL.md")]
    ref_dir = os.path.join(root, "references")
    if os.path.isdir(ref_dir):
        paths += [os.path.join(ref_dir, f) for f in os.listdir(ref_dir)
                  if f.endswith(".md")]
    pat = re.compile(r"(本包得分|得分\s*\d+(\.\d+)?\s*分|benchmark\s*score\s*[:=]\s*\d)")
    hits = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        for i, line in enumerate(open(p, encoding="utf-8"), 1):
            if pat.search(line):
                hits.append("%s:%d" % (os.path.basename(p), i))
    if hits:
        return False, "suspicious fabricated-score lines: %s" % hits
    return True, "no fabricated-score claims"


@test("包体量/文件数上限")
def t_size(root):
    total = 0
    count = 0
    too_big = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            total += sz
            count += 1
            if sz > 500 * 1024:
                too_big.append(f)
    if total > 2 * 1024 * 1024:
        return False, "total %d bytes > 2MB" % total
    if count > 50:
        return False, "file count %d > 50" % count
    if too_big:
        return False, "files >500KB: %s" % too_big
    return True, "%d files, %d bytes" % (count, total)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="selftest.py",
                                 description="Skill package self-test")
    ap.add_argument("--root", default=root_dir())
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    results = []
    for name, fn in TESTS:
        try:
            ok, detail = fn(root)
        except Exception as exc:
            ok, detail = False, "exception: %s: %s" % (type(exc).__name__, exc)
        results.append((name, ok, detail))
        print("[%s] %s — %s" % ("PASS" if ok else "FAIL", name, detail))
    fails = [r for r in results if not r[1]]
    print("\n%d/%d passed" % (len(results) - len(fails), len(results)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
