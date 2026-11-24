#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""latex_tools.py — LaTeX 编译、日志分类与交叉引用校验工具。

职责：
  1. compile：探测可用 TeX 引擎，按固定轮次编译（含 -halt-on-error 验证轮），
     产出 compile_report.json 与 .log；
  2. classify-log：把 .log/.blg 中的报错定位到「文件:行号」并分类、给根因与修复策略；
  3. refcheck：校验 \\ref/\\eqref/\\cite 是否全部解析。

仅依赖 Python 标准库。非致命失败（如缺引擎/缺宏包）退出码 0，但如实记录
degradations/engine_missing，绝不声称编译成功；compile 实际运行且编译失败时退出码 1。
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

MAX_FILE_BYTES = 2 * 1024 * 1024
ENGINE_ORDER = ["xelatex", "pdflatex", "lualatex", "tectonic"]

TAXONOMY = [
    ("missing_documentclass", [r"File `([^']+)\.cls' not found"],
     "documentclass file missing",
     "provide/install the .cls, or switch to an available documentclass; record in degradations"),
    ("missing_package", [r"File `([^']+)\.sty' not found"],
     "required .sty not installed",
     "remove/replace \\usepackage, or install the package; record in degradations"),
    ("undefined_control_sequence", [r"Undefined control sequence"],
     "macro/command not defined (typo or missing package)",
     "check spelling; add \\newcommand or the required \\usepackage"),
    ("math_error", [r"Missing \$ inserted", r"Extra \}, or forgotten \$",
                    r"Double superscript", r"Double subscript", r"Missing \} inserted",
                    r"Display math should end with \$\$"],
     "math delimiter/brace mismatch",
     "check $...$, \\(...\\), align environments, and braces around sub/superscripts"),
    ("runaway_argument", [r"Runaway argument"],
     "unclosed brace/argument",
     "locate and close the unmatched { }"),
    ("emergency_stop", [r"Emergency stop"],
     "fatal stop after an upstream error",
     "fix the first reported error, then recompile (ignore cascade)"),
    ("font_error", [r"Font .* not loadable", r"! Font .* not found"],
     "required font missing",
     "substitute an available system font or install the font; record in degradations"),
    ("undefined_reference", [r"Reference `([^']+)' on page \d+ undefined"],
     "\\ref target missing",
     "add the missing \\label or correct the \\ref name; recompile twice"),
    ("undefined_citation", [r"Citation `([^']+)' on page \d+ undefined"],
     "\\cite key not present in .bib",
     "add the bib entry or correct the cite key; run bibtex then recompile"),
    ("package_error", [r"Package .* Error"],
     "package-level error",
     "read the package message and follow its own hint"),
    ("too_many_errors", [r"That makes 100 errors"],
     "error cascade (100 errors)",
     "fix the earliest independent root-cause errors; ignore the cascade"),
    ("overfull", [r"Overfull \\hbox", r"Overfull \\vbox"],
     "box overflows margins (warning)",
     "adjust column width/line breaking; never hide by scaling"),
    ("underfull", [r"Underfull \\hbox", r"Underfull \\vbox"],
     "box underfull (warning)",
     "minor; adjust spacing only if visible"),
]


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds 2MiB"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def find_engines():
    found = []
    for name in ENGINE_ORDER:
        path = shutil.which(name)
        if path:
            found.append({"name": name, "path": path})
    return found


def classify_message(msg, kind):
    """Return (category, hint_dict) for a log message."""
    for category, patterns, root_cause, repair in TAXONOMY:
        for pat in patterns:
            if re.search(pat, msg or ""):
                return category, {"root_cause": root_cause, "repair": repair}
    return "unknown", {"root_cause": "unclassified message",
                       "repair": "inspect full log context and upstream errors"}


def _severity(msg):
    low = (msg or "").lower()
    if "error" in low or "emergency" in low or "fatal" in low:
        return "error"
    return "warning"


def _find_lineno(lines, i):
    for j in range(i, min(i + 6, len(lines))):
        m = re.match(r"^l\.(\d+)\s?(.*)$", lines[j])
        if m:
            return int(m.group(1)), lines[j].strip()
    return None, ""


def _embedded_lineno(msg):
    m = re.search(r"on input line (\d+)", msg or "")
    return int(m.group(1)) if m else None


FILELINE_RE = re.compile(r"^(\.?[\\/]?[^\s:()]+\.(?:tex|sty|cls|bib)):(\d+):\s*!?\s*(.*)$")
BANG_RE = re.compile(r"^!\s*(.*)$")
WARN_RE = re.compile(r"^(.*?(?:LaTeX|Package [\w.-]+)\s+(?:Warning|Error):\s*.*)$")
OPEN_RE = re.compile(r"^\(([^()]*(?:\.tex|\.sty|\.cls|\.bib))")


def _err(category, fname, linenum, msg, hint, severity):
    return {"category": category, "file": fname, "line": linenum,
            "message": (msg or "").strip(), "severity": severity,
            "root_cause": hint["root_cause"], "repair": hint["repair"]}


def parse_log(log_text, main_path=None):
    """Parse a .log/.blg, returning classified error objects."""
    errors = []
    if not log_text:
        return errors
    current_file = main_path or ""
    lines = log_text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        m = OPEN_RE.match(line)
        if m:
            current_file = m.group(1)
            i += 1
            continue
        m = FILELINE_RE.match(line)
        if m:
            fname, linenum, msg = m.group(1), int(m.group(2)), m.group(3)
            category, hint = classify_message(msg, "")
            errors.append(_err(category, fname, linenum, msg, hint, _severity(msg)))
            i += 1
            continue
        m = BANG_RE.match(line)
        if m:
            msg = m.group(1)
            linenum, _ = _find_lineno(lines, i)
            category, hint = classify_message(msg, "!")
            errors.append(_err(category, current_file, linenum, msg, hint, "error"))
            i += 1
            continue
        m = WARN_RE.match(line)
        if m:
            msg = m.group(1)
            linenum = _embedded_lineno(msg)
            category, hint = classify_message(msg, "")
            errors.append(_err(category, current_file, linenum, msg, hint, _severity(msg)))
            i += 1
            continue
        i += 1
    return errors


def _find_bib(main_dir):
    for root, _, files in os.walk(main_dir):
        for f in files:
            if f.lower().endswith(".bib"):
                return os.path.join(root, f)
    return None


def _engine_flags(outdir, jobname, halt):
    flags = ["-interaction=nonstopmode", "-file-line-error",
             "-output-directory=" + outdir, "-jobname=" + jobname]
    if halt:
        flags.append("-halt-on-error")
    return flags


def run_compile(engine_name, main_path, outdir, work, passes):
    main_path = os.path.abspath(main_path)
    outdir = os.path.abspath(outdir)
    work = os.path.abspath(work)
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    engines = find_engines()
    engine = None
    if engine_name and engine_name != "auto":
        for e in engines:
            if e["name"] == engine_name:
                engine = e
                break
    else:
        engine = engines[0] if engines else None

    jobname = os.path.splitext(os.path.basename(main_path))[0]
    report = {
        "engine_requested": engine_name,
        "engine_found": engine["name"] if engine else None,
        "engine_path": engine["path"] if engine else None,
        "main": main_path,
        "jobname": jobname,
        "outdir": outdir,
        "passes": passes,
        "returncodes": [],
        "halt_on_error_returncode": None,
        "log_file": os.path.join(outdir, jobname + ".log"),
        "pdf_file": os.path.join(outdir, jobname + ".pdf"),
        "compile_success": False,
        "engine_missing": engine is None,
        "errors": [],
        "degradations": [],
        "engine_version": None,
        "bibtex_used": False,
        "bibtex_available": shutil.which("bibtex") is not None,
        "generated_at": utcnow(),
    }

    if engine is None:
        report["degradations"].append({
            "type": "engine_missing",
            "note": "no TeX engine found; deliver editable .tex + compile plan, do not claim success",
        })
        write_json(os.path.join(work, "compile_report.json"), report)
        return report

    try:
        v = subprocess.run([engine["path"], "--version"],
                           capture_output=True, text=True, timeout=30)
        first = (v.stdout or v.stderr or "").splitlines()
        report["engine_version"] = first[0] if first else None
    except Exception:
        pass

    main_dir = os.path.dirname(main_path)
    base = os.path.basename(main_path)
    bib_file = _find_bib(main_dir)
    bibtex_run = False

    # 诊断轮：nonstopmode 完整收集错误，用于级联分类
    for p in range(1, passes + 1):
        cmd = [engine["path"]] + _engine_flags(outdir, jobname, halt=False) + [base]
        rc = _run_one(cmd, main_dir)
        report["returncodes"].append(rc)
        if p == 1 and bib_file and not bibtex_run and rc == 0:
            aux_path = os.path.join(outdir, jobname + ".aux")
            aux_text, _ = read_text(aux_path) if os.path.isfile(aux_path) else (None, None)
            if aux_text and "\\citation{" in aux_text and report["bibtex_available"]:
                _run_one([shutil.which("bibtex"), jobname], outdir)
                bibtex_run = True
                report["bibtex_used"] = True

    # 验证轮：halt-on-error 判定「至少一次编译通过」
    verify_cmd = [engine["path"]] + _engine_flags(outdir, jobname, halt=True) + [base]
    halt_rc = _run_one(verify_cmd, main_dir)
    report["halt_on_error_returncode"] = halt_rc

    log_text = ""
    if os.path.isfile(report["log_file"]):
        log_text, _ = read_text(report["log_file"])
    if not log_text:
        log_text = "(no .log produced; engine output unavailable)"
    report["errors"] = parse_log(log_text, main_path)

    pdf_ok = os.path.isfile(report["pdf_file"])
    report["compile_success"] = (halt_rc == 0) and pdf_ok
    if not report["compile_success"]:
        report["degradations"].append({
            "type": "compile_failed",
            "note": "halt-on-error returncode %r, pdf present %s" % (halt_rc, pdf_ok),
        })

    with open(os.path.join(work, jobname + ".compile.log"), "w",
              encoding="utf-8", errors="replace") as fh:
        fh.write(log_text)
    write_json(os.path.join(work, "compile_report.json"), report)
    return report


def _run_one(cmd, cwd):
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=240)
        return proc.returncode
    except subprocess.TimeoutExpired:
        return "timeout"
    except OSError as exc:
        return "os_error: %s" % exc


def cmd_compile(args):
    report = run_compile(args.engine, args.main, args.outdir, args.work, args.passes)
    print(json.dumps({
        "compile_success": report["compile_success"],
        "engine_missing": report["engine_missing"],
        "engine": report["engine_found"],
        "pdf_file": report["pdf_file"],
        "error_count": len(report["errors"]),
        "report_path": os.path.join(args.work, "compile_report.json"),
    }, ensure_ascii=False, indent=2))
    if report["engine_missing"]:
        return 0
    return 0 if report["compile_success"] else 1


def cmd_classify(args):
    log_text, err = read_text(args.log)
    if log_text is None:
        print(json.dumps({"errors": [], "note": err}, ensure_ascii=False, indent=2))
        return 0
    errors = parse_log(log_text, args.main)
    print(json.dumps({"log": args.log, "errors": errors,
                      "error_count": len(errors)}, ensure_ascii=False, indent=2))
    return 0


def cmd_refcheck(args):
    result = refcheck(args.dir, args.bib)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def refcheck(dir_path, bib=None):
    dir_path = os.path.abspath(dir_path)
    labels = {}
    cites = {}
    tex_files = []
    for root, _, files in os.walk(dir_path):
        for f in files:
            full = os.path.join(root, f)
            if f.endswith(".tex"):
                tex_files.append(full)
            elif f.endswith(".aux"):
                text, _ = read_text(full)
                if text:
                    for m in re.finditer(r"\\newlabel\{([^}]+)\}", text):
                        labels[m.group(1)] = True
                    for m in re.finditer(r"\\bibcite\{([^}]+)\}", text):
                        cites[m.group(1)] = True

    refs = set()
    used_cites = set()
    ref_re = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|pageref|vref|nameref)\*?\{([^}]+)\}")
    cite_re = re.compile(r"\\(?:cite|citep|citet|citealp|citeauthor|citeyear|citeal)\*?(?:\[[^\]]*\])*\{([^}]+)\}")
    for full in tex_files:
        text, _ = read_text(full)
        if text:
            for m in ref_re.finditer(text):
                refs.add(m.group(1))
            for m in cite_re.finditer(text):
                for k in m.group(1).split(","):
                    k = k.strip()
                    if k:
                        used_cites.add(k)

    undefined_refs = sorted(r for r in refs if r not in labels)
    undefined_cites = sorted(c for c in used_cites if c not in cites)
    return {
        "dir": dir_path,
        "bib": bib,
        "defined_refs": sorted(labels),
        "used_refs": sorted(refs),
        "undefined_refs": undefined_refs,
        "defined_cites": sorted(cites),
        "used_cites": sorted(used_cites),
        "undefined_cites": undefined_cites,
        "tex_files": tex_files,
    }


def build_parser():
    p = argparse.ArgumentParser(prog="latex_tools.py",
                                description="LaTeX compile/log/refcheck tools")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("compile", help="compile a .tex and write compile_report.json")
    sp.add_argument("--main", required=True, help="path to main .tex")
    sp.add_argument("--outdir", default="/app/output")
    sp.add_argument("--work", default="/app/work")
    sp.add_argument("--engine", default="auto", help="xelatex/pdflatex/lualatex/tectonic or auto")
    sp.add_argument("--passes", type=int, default=3, help="diagnostic pass count (default 3)")
    sp.set_defaults(func=cmd_compile)

    sp = sub.add_parser("classify-log", help="classify errors in a .log")
    sp.add_argument("--log", required=True)
    sp.add_argument("--main", default=None)
    sp.set_defaults(func=cmd_classify)

    sp = sub.add_parser("refcheck", help="check undefined refs/cites")
    sp.add_argument("--dir", required=True)
    sp.add_argument("--bib", default=None)
    sp.set_defaults(func=cmd_refcheck)

    return p


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
