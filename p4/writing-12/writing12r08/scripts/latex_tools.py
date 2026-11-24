#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
latex_tools.py — 编译、日志分类与交叉引用校验（仅标准库）。

- compile        : 探测 TeX 引擎，循环编译，解析 log，产出 compile_report.json
- classify-log   : 将 .log 报错归类为可操作的根因类别
- refcheck       : 校验 .aux/.log 中 undefined references/citations
所有命令在非致命失败时退出码 0，并把诊断写入报告（绝不假装编译成功）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_impl import reconfigure_stdio, utcnow, write_json

ENGINE_CANDIDATES = ["latexmk", "pdflatex", "xelatex", "lualatex", "tectonic"]


def which_engine():
    for name in ENGINE_CANDIDATES:
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


def _run(cmd, cwd):
    """Run command, return (returncode, stdout+stderr merged text)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=300,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        return proc.returncode, out
    except FileNotFoundError:
        return 127, "command_not_found: " + cmd[0]
    except subprocess.TimeoutExpired as exc:
        return 124, "timeout: %s" % exc


def find_log(outdir, main_name):
    base = os.path.splitext(os.path.basename(main_name))[0]
    candidates = [os.path.join(outdir, base + ".log")]
    for extra in (".aux", ".bbl", ".blg"):
        candidates.append(os.path.join(outdir, base + extra))
    return candidates


def _classify_line(line):
    low = line.lower()
    if "file `" in low and ("not found" in low or "cannot find" in low):
        return "missing_file"
    if low.startswith("! latex error: file") and "not found" in low:
        return "missing_file"
    if low.startswith("! undefined control sequence"):
        return "undefined_control_sequence"
    if low.startswith("! ") and ("package" in low and "not found" in low):
        return "missing_package"
    if low.startswith("! ") and ("class" in low and "not found" in low):
        return "missing_file"
    if low.startswith("! ") and any(k in low for k in ("math", "superscript", "subscript", "misplaced")):
        return "math_error"
    if low.startswith("! ") and ("reference" in low or "label" in low):
        return "undefined_reference"
    if low.startswith("! latex error: "):
        if "control sequence" in low:
            return "undefined_control_sequence"
        return "latex_error"
    if "undefined control sequence" in low:
        return "undefined_control_sequence"
    if "package" in low and ("not found" in low or "missing" in low):
        return "missing_package"
    if re.search(r"\\begin\{[^}]+\} on input line", line):
        return "environment_error"
    return None


def classify_log(log_path, main_path=None):
    """解析 .log，返回 {'errors': [...], 'warnings': [...], 'categories': {...}}。"""
    errors, warnings, categories = [], [], {}
    try:
        text = open(log_path, encoding="utf-8-sig", errors="replace").read()
    except OSError as exc:
        return {"errors": [{"raw": "unreadable_log", "reason": str(exc)}],
                "warnings": [], "categories": {}}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        cat = _classify_line(stripped)
        if cat:
            entry = {"line": i + 1, "raw": stripped, "category": cat,
                     "context": "\n".join(lines[max(0, i - 2):i + 3])}
            errors.append(entry)
            categories[cat] = categories.get(cat, 0) + 1
        elif ("warning" in stripped.lower()
              and ("reference" in stripped.lower() or "citation" in stripped.lower())):
            warnings.append({"line": i + 1, "raw": stripped})
    return {"errors": errors, "warnings": warnings, "categories": categories,
            "log_path": log_path, "main_path": main_path}


def parse_undefined_refs(log_text):
    refs, cites = set(), set()
    for m in re.finditer(r"LaTeX Warning: Reference `([^']+)' on page", log_text):
        refs.add(m.group(1))
    for m in re.finditer(r"LaTeX Warning: Citation `([^']+)' on page", log_text):
        cites.add(m.group(1))
    return sorted(refs), sorted(cites)


def refcheck(dirpath, bib=None):
    """扫描目录下 .log 文件，返回 undefined_refs / undefined_cites。"""
    report = {"dir": dirpath, "undefined_refs": [], "undefined_cites": [], "files": []}
    if not os.path.isdir(dirpath):
        report["error"] = "dir_not_found"
        return report
    for name in sorted(os.listdir(dirpath)):
        if not name.lower().endswith(".log"):
            continue
        path = os.path.join(dirpath, name)
        try:
            text = open(path, encoding="utf-8-sig", errors="replace").read()
        except OSError:
            continue
        refs, cites = parse_undefined_refs(text)
        report["files"].append({"file": name, "undefined_refs": refs, "undefined_cites": cites})
        report["undefined_refs"].extend(refs)
        report["undefined_cites"].extend(cites)
    report["undefined_refs"] = sorted(set(report["undefined_refs"]))
    report["undefined_cites"] = sorted(set(report["undefined_cites"]))
    return report


def compile_tex(main_path, outdir, passes=3):
    """编译 main_path 到 outdir。返回 compile_report（含成功标志与轮次）。"""
    main_abs = os.path.abspath(main_path)
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    engine, engine_path = which_engine()
    report = {
        "main": main_abs, "outdir": outdir, "engine": engine, "engine_path": engine_path,
        "requested_passes": passes, "passes_run": 0, "success": False,
        "exit_codes": [], "log_files": [], "classification": None,
        "refcheck": None, "generated_at": utcnow(),
    }
    if engine is None:
        report["error"] = "engine_missing: no LaTeX engine found on PATH"
        _write_report(outdir, report)
        return report

    cwd = os.path.dirname(main_abs)
    base = os.path.splitext(os.path.basename(main_abs))[0]
    log_path = os.path.join(outdir, base + ".log")

    if engine == "latexmk":
        cmd = [engine, "-pdf", "-interaction=nonstopmode", "-halt-on-error",
               "-output-directory=" + outdir, os.path.basename(main_abs)]
        rc, out = _run(cmd, cwd)
        report["exit_codes"].append(rc)
        report["passes_run"] = 1
        # latexmk 单次调用内部完成多遍；记录失败即停。
    else:
        for i in range(int(passes)):
            cmd = [engine, "-interaction=nonstopmode", "-halt-on-error",
                   "-output-directory=" + outdir, os.path.basename(main_abs)]
            rc, out = _run(cmd, cwd)
            report["exit_codes"].append(rc)
            report["passes_run"] = i + 1
            if rc != 0:
                break

    report["success"] = bool(report["exit_codes"]) and all(
        c == 0 for c in report["exit_codes"])
    pdf_path = os.path.join(outdir, base + ".pdf")
    if not os.path.isfile(pdf_path):
        report["success"] = False
        report.setdefault("error", "pdf_not_produced")
    report["pdf"] = pdf_path if os.path.isfile(pdf_path) else None
    report["log_files"] = [p for p in find_log(outdir, main_abs) if os.path.isfile(p)]
    if os.path.isfile(log_path):
        report["classification"] = classify_log(log_path, main_abs)
    report["refcheck"] = refcheck(outdir)
    _write_report(outdir, report)
    return report


def _write_report(outdir, report):
    write_json(os.path.join(outdir, "compile_report.json"), report)


def cmd_compile(args):
    report = compile_tex(args.main, args.outdir, args.passes)
    print(json.dumps({"success": report.get("success"),
                      "engine": report.get("engine"),
                      "error": report.get("error"),
                      "passes_run": report.get("passes_run"),
                      "report": os.path.join(args.outdir, "compile_report.json")},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_classify(args):
    if not os.path.isfile(args.log):
        print(json.dumps({"error": "log_not_found"}, ensure_ascii=False, indent=2))
        return 0
    result = classify_log(args.log, args.main)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_refcheck(args):
    result = refcheck(args.dir, args.bib)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="LaTeX 编译、日志分类与引用校验。")
    sub = parser.add_subparsers(dest="command", required=True)
    p1 = sub.add_parser("compile")
    p1.add_argument("--main", required=True)
    p1.add_argument("--outdir", required=True)
    p1.add_argument("--passes", type=int, default=3)
    p1.set_defaults(func=cmd_compile)
    p2 = sub.add_parser("classify-log")
    p2.add_argument("--log", required=True)
    p2.add_argument("--main", default=None)
    p2.set_defaults(func=cmd_classify)
    p3 = sub.add_parser("refcheck")
    p3.add_argument("--dir", required=True)
    p3.add_argument("--bib", default=None)
    p3.set_defaults(func=cmd_refcheck)
    return parser


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(json.dumps({"fatal": str(exc)}, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
