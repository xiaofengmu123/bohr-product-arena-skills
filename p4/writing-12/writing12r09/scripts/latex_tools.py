#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""latex_tools.py — LaTeX 编译、交叉引用校验与日志分类（仅标准库）。

职责：
  1. compile：以固定引擎与轮次编译 .tex，落盘 compile_report.json；
  2. refcheck：从 .log/.aux 提取未定义引用与引用计数；
  3. classify-log：把 .log 中的报错归类为可执行修复策略的分类。

所有子命令在缺引擎/缺文件时退出码 0 并写 degradation，绝不崩溃。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ENGINES = ("pdflatex", "xelatex", "lualatex")
MAX_LOG_BYTES = 8 * 1024 * 1024

# (category, regex)：匹配以 `! ` 开头的经典报错行。
ERROR_PATTERNS = [
    ("missing_package", re.compile(
        r"^! (?:LaTeX|Package) Error: File [`']?([^'\s]+)\.(?:sty|cls|def|cfg|fd)'? not found")),
    ("missing_package", re.compile(r"^! LaTeX Error: File [`']([^'\s]+)' not found")),
    ("undefined_control_sequence", re.compile(r"^! Undefined control sequence")),
    ("math_error", re.compile(
        r"^! (?:Missing \$ inserted|Display math should end with \$\$|Extra \}|"
        r"Missing \} inserted|Double superscript|Double subscript)")),
    ("runaway_argument", re.compile(r"^! (?:Paragraph ended before|Runaway argument)")),
    ("too_many_braces", re.compile(r"^! Too many }")),
    ("emergency_stop", re.compile(r"^! Emergency stop")),
    ("fatal", re.compile(r"^!  ==> Fatal error")),
]
FILE_LINE_PREFIX = re.compile(r"^(?:\./)?([^:\s()]+\.(?:tex|sty|cls|bib|cfg)):(\d+):\s*(.*)$")
LINE_CTX = re.compile(r"l\.(\d+)")
FILE_OPEN = re.compile(r"\(([^\s()]+\.(?:tex|sty|cls|bib|cfg))")
UNDEF_REF = re.compile(r"LaTeX Warning: Reference [`']?([^']+)' on page (\d+) undefined")
UNDEF_CITE = re.compile(r"LaTeX Warning: Citation [`']?([^']+)' on page (\d+) undefined")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path, cap=MAX_LOG_BYTES):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > cap:
        raw = raw[:cap]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def find_engine(preferred=None):
    if preferred:
        path = shutil.which(preferred)
        if path:
            return preferred, path
    for name in ENGINES:
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


def classify_log(log_path):
    """Parse a .log file into categorized errors and warnings."""
    out = {"log": log_path, "errors": [], "warnings": []}
    text, err = read_text(log_path)
    if text is None:
        out["error"] = err or "unreadable"
        return out
    lines = text.splitlines()
    current_file = os.path.basename(log_path).replace(".log", ".tex")
    i = 0
    while i < len(lines):
        line = lines[i]

        # 先处理带 file:line 前缀的行（-file-line-error 输出）
        fm = FILE_LINE_PREFIX.match(line)
        if fm and "!" in fm.group(3):
            current_file = fm.group(1)
            message = fm.group(3).strip()
            out["errors"].append({
                "category": _categorize(message),
                "file": fm.group(1),
                "line": int(fm.group(2)),
                "message": message,
            })
            i += 1
            continue

        # 追踪当前文件
        fo = FILE_OPEN.search(line)
        if fo:
            current_file = fo.group(1)

        # 未定义引用/引用（warning）
        m = UNDEF_REF.search(line)
        if m:
            out["warnings"].append({
                "category": "undefined_reference", "file": current_file,
                "line": None, "name": m.group(1), "page": m.group(2),
                "message": line.strip(),
            })
        m = UNDEF_CITE.search(line)
        if m:
            out["warnings"].append({
                "category": "undefined_citation", "file": current_file,
                "line": None, "name": m.group(1), "page": m.group(2),
                "message": line.strip(),
            })

        # 经典 `! ` 报错
        if line.startswith("!"):
            message = line.strip()
            line_no = None
            for j in range(i, min(i + 6, len(lines))):
                lm = LINE_CTX.search(lines[j])
                if lm:
                    line_no = int(lm.group(1))
                    break
            out["errors"].append({
                "category": _categorize(message),
                "file": current_file,
                "line": line_no,
                "message": message,
            })
        i += 1
    return out


def _categorize(message):
    for cat, pat in ERROR_PATTERNS:
        if pat.search(message):
            return cat
    return "other_error"


def _run_latex(engine_path, jobname, outdir, cwd, halt, main_basename):
    cmd = [engine_path, "-interaction=nonstopmode"]
    if halt:
        cmd.append("-halt-on-error")
    cmd += ["-file-line-error", "-output-directory", outdir,
            "-jobname", jobname, main_basename]
    return cmd


def cmd_compile(args):
    main = os.path.abspath(args.main)
    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    if not os.path.isfile(main):
        rep = _report_fail(main, outdir, None, "main_not_found: %s" % main)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    cwd = os.path.dirname(main)
    basename = os.path.basename(main)
    jobname = args.jobname or os.path.splitext(basename)[0]
    engine, engine_path = find_engine(args.engine)
    if engine is None:
        rep = _report_fail(main, outdir, None, "engine_missing")
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    passes = max(1, args.passes)
    exit_codes = []
    for _ in range(passes):
        cmd = _run_latex(engine_path, jobname, outdir, cwd, args.halt, basename)
        proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        exit_codes.append(proc.returncode)

    # 若存在 .aux 的 \citation 且有 .bib，则 bibtex + 两遍 latex 收敛引用
    bibtex_run = False
    aux = os.path.join(outdir, jobname + ".aux")
    if os.path.isfile(aux):
        aux_text, _ = read_text(aux)
        if aux_text and r"\citation" in aux_text:
            bib_files = [f for f in os.listdir(cwd) if f.lower().endswith(".bib")]
            bib = shutil.which("bibtex")
            if bib and bib_files:
                subprocess.run([bib, jobname], cwd=outdir,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                bibtex_run = True
                for _ in range(2):
                    cmd = _run_latex(engine_path, jobname, outdir, cwd, args.halt, basename)
                    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT)
                    exit_codes.append(proc.returncode)

    log_path = os.path.join(outdir, jobname + ".log")
    cls = classify_log(log_path) if os.path.isfile(log_path) else {"errors": [], "warnings": []}
    success = all(rc == 0 for rc in exit_codes) and not cls.get("errors")

    rep = {
        "main": main,
        "outdir": outdir,
        "jobname": jobname,
        "engine": engine,
        "engine_path": engine_path,
        "passes_requested": passes,
        "passes_run": len(exit_codes),
        "exit_codes": exit_codes,
        "bibtex_run": bibtex_run,
        "success": success,
        "log": log_path if os.path.isfile(log_path) else None,
        "pdf": os.path.join(outdir, jobname + ".pdf"),
        "errors": cls.get("errors", []),
        "warnings": cls.get("warnings", []),
        "generated_at": utcnow(),
    }
    if not success and rep["passes_run"]:
        rep["degradation"] = "compile_failed_or_errors"
    write_json(os.path.join(outdir, "compile_report.json"), rep)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


def _report_fail(main, outdir, engine, degradation):
    rep = {
        "main": main,
        "outdir": outdir,
        "engine": engine,
        "success": False,
        "degradation": degradation,
        "passes_run": 0,
        "exit_codes": [],
        "errors": [],
        "warnings": [],
        "generated_at": utcnow(),
    }
    write_json(os.path.join(outdir, "compile_report.json"), rep)
    return rep


def _first_file(directory, ext):
    if not os.path.isdir(directory):
        return None
    matches = sorted(f for f in os.listdir(directory) if f.lower().endswith(ext))
    return os.path.join(directory, matches[0]) if matches else None


def cmd_refcheck(args):
    d = os.path.abspath(args.dir)
    log = args.log or _first_file(d, ".log")
    aux = _first_file(d, ".aux")
    report = {
        "dir": d, "log": log, "aux": aux,
        "undefined_refs": [], "undefined_cites": [],
        "defined_labels": 0, "defined_cites": 0,
    }
    if log and os.path.isfile(log):
        cls = classify_log(log)
        for w in cls.get("warnings", []):
            if w.get("category") == "undefined_reference":
                report["undefined_refs"].append(w)
            elif w.get("category") == "undefined_citation":
                report["undefined_cites"].append(w)
    if aux and os.path.isfile(aux):
        t, _ = read_text(aux)
        if t:
            report["defined_labels"] = t.count(r"\newlabel")
            report["defined_cites"] = t.count(r"\bibcite")
    write_json(os.path.join(d, "refcheck.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_classify(args):
    cls = classify_log(args.log)
    print(json.dumps(cls, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="latex_tools.py",
        description="LaTeX 编译、交叉引用校验与日志分类（仅标准库）。")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compile", help="以固定引擎与轮次编译 .tex")
    c.add_argument("--main", required=True)
    c.add_argument("--outdir", required=True)
    c.add_argument("--passes", type=int, default=3)
    c.add_argument("--halt", action="store_true",
                   help="首遍用 -halt-on-error 停在第一条错误")
    c.add_argument("--engine", default=None, help="pdflatex/xelatex/lualatex")
    c.add_argument("--jobname", default=None)
    c.set_defaults(func=cmd_compile)

    r = sub.add_parser("refcheck", help="校验交叉引用与引用计数")
    r.add_argument("--dir", required=True)
    r.add_argument("--log", default=None)
    r.set_defaults(func=cmd_refcheck)

    cl = sub.add_parser("classify-log", help="分类 .log 中的报错")
    cl.add_argument("--log", required=True)
    cl.set_defaults(func=cmd_classify)
    return p


def main(argv=None):
    reconfigure_stdio()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
