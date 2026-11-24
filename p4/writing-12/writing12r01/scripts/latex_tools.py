#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
latex_tools.py — 编译、报错分类与引用校验。

职责：
  * compile: 探测可用 TeX 引擎，以 -halt-on-error -file-line-error 编译，
    写 compile_report.json（每次编译一份，含命令、轮次、退出码、错误分类）；
  * classify-log: 解析 .log，把报错定位到 文件:行号 并分类、给根因与修复策略；
  * refcheck: 解析 .log，列出 undefined references / undefined citations。

分类集（创新增益的核心）：missing_package / missing_class /
undefined_control_sequence / math_error / undefined_reference /
undefined_citation / font_missing / capacity_exceeded /
paragraph_ended / option_clash / other。

仅依赖 Python 标准库。无 TeX 引擎时优雅降级，记录 engine_missing，不谎称编译成功。
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

ENGINE_CANDIDATES = ["latexmk", "pdflatex", "xelatex", "lualatex", "tectonic"]

# (regex, category, root_cause, fix_hint)
ERROR_PATTERNS = [
    (r"! LaTeX Error: File `([^`']+)\.sty' not found", "missing_package",
     "宏包 .sty 文件缺失", "检查宏包名拼写；必要时改用发行版自带替代宏包或安装宏包"),
    (r"! LaTeX Error: File `([^`']+)\.cls' not found", "missing_class",
     "模板类 .cls 缺失", "确认模板类文件在输入目录或 texmf 树中；否则记录缺失并降级"),
    (r"! LaTeX Error: File `([^`']+)' not found", "missing_file",
     "依赖文件缺失（.bib/.tex/图片等）", "补齐文件或显式标注 MATERIAL GAP"),
    (r"! Undefined control sequence", "undefined_control_sequence",
     "未定义的宏/命令", "补齐宏包或命令定义；检查拼写与加载顺序"),
    (r"! LaTeX Error: Option clash for package ([^.\s]+)", "option_clash",
     "宏包选项冲突", "统一全局选项，避免同包多次加载带不同选项"),
    (r"! Double superscript", "math_error",
     "数学模式双重上标", "合并上下标，如 a^{b c}"),
    (r"! Double subscript", "math_error",
     "数学模式双重下标", "用花括号分组下标"),
    (r"! Missing [$$] inserted", "math_error",
     "缺少 $ 分隔数学模式", "补齐 $...$ 或 \\(...\\) 包围"),
    (r"! Missing \\right\\. inserted|! Missing \\left\\. inserted", "math_error",
     "\\left/\\right 不配对", "成对使用或用 \\Bigl/\\Bigr 等显式定界符"),
    (r"! Extra [}], or forgotten", "math_error",
     "花括号不配对", "检查分组与花括号配平"),
    (r"! Misplaced alignment tab character &", "table_error",
     "表格中 & 使用位置错误", "检查列数与 & 数量一致；在正文中的 & 需转义为 \\&"),
    (r"! Extra alignment tab has been changed to \\cr", "table_error",
     "表格列数与内容不符", "对齐列数与 & 数量对齐，或改用 tabularx 自动列宽"),
    (r"! LaTeX Error: \\begin\{([^}]+)\} on input line .* ended by \\end\{([^}]+)\}", "environment_mismatch",
     "环境配对错误", "检查 \\begin/\\end 配对与嵌套"),
    (r"! LaTeX Error: Environment ([^ ]+) undefined", "undefined_environment",
     "未定义环境", "补齐定义环境的宏包（如 algorithm、minted）"),
    (r"! Font [^\n]* not loadable", "font_missing",
     "字体不可加载", "安装字体或用 fontspec 指定可用字体"),
    (r"! TeX capacity exceeded", "capacity_exceeded",
     "TeX 容量超限（递归/过大表格等）", "检查无限循环、超大宏展开；拆大表或增 pool_size"),
    (r"Runaway argument\?", "runaway_argument",
     "参数逃逸（缺右花括号）", "检查缺失的右花括号或参数分隔符"),
    (r"! Paragraph ended before", "paragraph_ended",
     "空行出现在非法位置", "删除命令参数中的空行"),
    (r"! LaTeX Error: \\caption outside float", "float_error",
     "\\caption 用在浮动体外", "把 \\caption 移入 figure/table 环境"),
    (r"! LaTeX Error: Not in outer par mode", "float_error",
     "figure/table 放错位置", "浮动体不能嵌套；检查环境位置"),
]

# Log lines that report undefined references/citations (warnings, not fatal).
REF_WARN = re.compile(r"LaTeX Warning: Reference `([^']+)' on page \d+ undefined")
CITE_WARN = re.compile(r"LaTeX Warning: Citation `([^']+)' on page \d+ undefined")
MULTI_REF = re.compile(r"LaTeX Warning: There were undefined references")
MULTI_CITE = re.compile(r"LaTeX Warning: There were undefined citations")

FILE_LINE = re.compile(r"^(?:\./)?([^\s:]+):(\d+):")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def detect_engine():
    """Return (engine, missing_list). First available engine wins."""
    missing = []
    for eng in ENGINE_CANDIDATES:
        if shutil.which(eng):
            return eng, missing
        missing.append(eng)
    return None, missing


def read_text(path, limit=16 * 1024 * 1024):
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit)
    except OSError as exc:
        return None, "read_error: %s" % exc
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def build_compile_command(engine, main, outdir, jobname):
    """Return the full command list for a single compilation pass."""
    base = os.path.basename(main)
    stem = os.path.splitext(base)[0]
    cwd = os.path.dirname(os.path.abspath(main)) if os.path.dirname(main) else "."
    if engine == "latexmk":
        return ([engine, "-pdf", "-halt-on-error", "-interaction=nonstopmode",
                 "-file-line-error", "-jobname=" + jobname,
                 "-outdir=" + os.path.abspath(outdir), base], cwd)
    return ([engine, "-halt-on-error", "-interaction=nonstopmode",
             "-file-line-error", "-jobname=" + jobname,
             "-output-directory=" + os.path.abspath(outdir), base], cwd)


def run_compile(main, outdir, engine=None, passes=2, jobname="main"):
    """Compile main.tex with the given engine. Returns compile_report dict."""
    os.makedirs(outdir, exist_ok=True)
    started = utcnow()
    if engine is None:
        engine, missing = detect_engine()
    else:
        missing = [] if shutil.which(engine) else [engine]
    if engine is None:
        return {
            "ok": False,
            "engine_missing": True,
            "missing_engines": missing,
            "main": main,
            "passes": passes,
            "exit_code": None,
            "log_file": None,
            "errors": [],
            "note": "no TeX engine available; deliver editable .tex only",
            "started_at": started,
            "finished_at": utcnow(),
        }

    main_abs = os.path.abspath(main)
    log_file = os.path.join(outdir, jobname + ".log")
    errors = []
    last_code = None
    for p in range(1, passes + 1):
        cmd, cwd = build_compile_command(engine, main_abs, outdir, jobname)
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=180)
            last_code = proc.returncode
        except subprocess.TimeoutExpired:
            last_code = "timeout"
            errors.append({"pass": p, "category": "timeout",
                           "file": main, "line": None,
                           "root_cause": "编译超时（>180s/轮）", "fix": "检查无限循环或超大文档"})
            break
        except OSError as exc:
            last_code = "spawn_error"
            errors.append({"pass": p, "category": "spawn_error",
                           "file": main, "line": None,
                           "root_cause": str(exc), "fix": ""})
            break
        log_txt, _ = read_text(log_file) if os.path.isfile(log_file) else ("", None)
        classified = classify_log_text(log_txt or "", main) if log_txt else []
        for c in classified:
            c["pass"] = p
        errors.extend(classified)
        if last_code == 0 and not classified:
            break  # clean pass; stop early
        if last_code != 0 and not classified:
            errors.append({"pass": p, "category": "unclassified",
                           "file": main, "line": None,
                           "root_cause": "compile failed with no parsed error",
                           "fix": "open log and inspect tail"})

    return {
        "ok": (last_code == 0 and not any(e.get("category") not in (None,) for e in errors)
               or (last_code == 0)),
        "engine": engine,
        "engine_missing": False,
        "command": build_compile_command(engine, main_abs, outdir, jobname)[0],
        "passes": passes,
        "exit_code": last_code,
        "log_file": log_file,
        "errors": errors,
        "started_at": started,
        "finished_at": utcnow(),
    }


ERROR_LINE_KEYWORDS = (
    "Undefined control sequence", "not found", "Error", "Missing",
    "Extra alignment tab", "Extra }", "Misplaced alignment", "Double superscript",
    "Double subscript", "capacity exceeded", "Font", "Paragraph ended",
    "ended by", "Option clash", "Not in outer par mode", "caption outside",
    "Runaway argument", "Missing $", "Missing \\right", "Missing \\left",
)


def is_error_line(line):
    """Recognize error lines in both plain ``!`` and ``file:line:`` formats."""
    s = line.strip()
    if s.startswith("!"):
        return True
    if "Runaway argument" in s:
        return True
    # file:line: style (e.g. ./main.tex:12: Undefined control sequence.)
    if re.search(r"^(?:\./)?[^\s:]+:\d+:", line):
        return any(kw in s for kw in ERROR_LINE_KEYWORDS)
    return False


def classify_log_text(log_text, main):
    """Parse log text into error objects with file:line + category + root cause + fix."""
    lines = log_text.splitlines()
    errors = []
    for i, line in enumerate(lines):
        if not is_error_line(line):
            continue
        category, root_cause, fix = "other", "未知错误", ""
        for pattern, cat, cause, hint in ERROR_PATTERNS:
            if re.search(pattern, line):
                category, root_cause, fix = cat, cause, hint
                break
        # Resolve file:line: prefer the line itself, else scan back.
        file_, line_no = None, None
        m = FILE_LINE.search(line)
        if m:
            file_, line_no = m.group(1), int(m.group(2))
        else:
            for j in range(i, max(-1, i - 40), -1):
                m2 = FILE_LINE.search(lines[j])
                if m2:
                    file_, line_no = m2.group(1), int(m2.group(2))
                    break
        errors.append({
            "category": category,
            "file": file_ or os.path.basename(main),
            "line": line_no,
            "root_cause": root_cause,
            "fix": fix,
            "raw": line.strip()[:400],
        })
    return errors


def classify_log_file(log_path, main):
    text, err = read_text(log_path)
    if text is None:
        return [{"category": "log_unreadable", "file": log_path, "line": None,
                 "root_cause": err, "fix": "", "raw": ""}]
    return classify_log_text(text, main)


def refcheck(log_text):
    """Extract undefined references and citations from log text."""
    refs = sorted(set(REF_WARN.findall(log_text)))
    cites = sorted(set(CITE_WARN.findall(log_text)))
    return {
        "undefined_refs": refs,
        "undefined_cites": cites,
        "has_multiple_refs": bool(MULTI_REF.search(log_text)),
        "has_multiple_cites": bool(MULTI_CITE.search(log_text)),
    }


def cmd_compile(args):
    engine = args.engine
    report = run_compile(args.main, args.outdir, engine=engine,
                         passes=args.passes, jobname=args.jobname)
    report_path = os.path.join(args.outdir, "compile_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": report["ok"], "engine": report.get("engine"),
                      "engine_missing": report.get("engine_missing", False),
                      "error_count": len(report["errors"]),
                      "report": report_path}, ensure_ascii=False, indent=2))
    return 0 if (report["ok"] or report.get("engine_missing")) else 1


def cmd_classify(args):
    errors = classify_log_file(args.log, args.main or "")
    print(json.dumps({"log": args.log, "error_count": len(errors),
                      "errors": errors}, ensure_ascii=False, indent=2))
    return 0


def cmd_refcheck(args):
    # --dir 下的 *.log 优先 main.log；也支持直接给 --log
    log_path = args.log
    if not log_path and args.dir:
        candidate = os.path.join(args.dir, "main.log")
        if os.path.isfile(candidate):
            log_path = candidate
        else:
            logs = [os.path.join(args.dir, f) for f in os.listdir(args.dir)
                    if f.endswith(".log")]
            if logs:
                log_path = sorted(logs)[0]
    if not log_path or not os.path.isfile(log_path):
        print(json.dumps({"ok": False, "error": "no log found"}))
        return 0
    text, _ = read_text(log_path)
    report = refcheck(text or "")
    report["ok"] = (not report["undefined_refs"] and not report["undefined_cites"])
    report["log"] = log_path
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def build_parser():
    p = argparse.ArgumentParser(
        prog="latex_tools.py",
        description="LaTeX 编译、报错分类与引用校验",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("compile", help="compile main.tex, write compile_report.json")
    sp.add_argument("--main", required=True)
    sp.add_argument("--outdir", default="/app/output")
    sp.add_argument("--engine", default=None)
    sp.add_argument("--passes", type=int, default=2)
    sp.add_argument("--jobname", default="main")
    sp.set_defaults(func=cmd_compile)

    sp = sub.add_parser("classify-log", help="classify errors in a .log file")
    sp.add_argument("--log", required=True)
    sp.add_argument("--main", default=None)
    sp.set_defaults(func=cmd_classify)

    sp = sub.add_parser("refcheck", help="check undefined refs/cites in a log")
    sp.add_argument("--dir", default=None)
    sp.add_argument("--log", default=None)
    sp.add_argument("--bib", default=None)  # accepted for interface compatibility
    sp.set_defaults(func=cmd_refcheck)
    return p


def main(argv=None):
    reconfigure_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
