#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""latex_tools.py — LaTeX 编译、错误分类与引用校验。

compile      在指定 .tex 目录运行 LaTeX 引擎（halt-on-error），解析 log，
             按「文件:行号 + 分类 + 根因」归类错误，写 compile_report.json。
refcheck     校验 \label/\ref、\cite 与 .bib/.bbl 的对应关系。
classify-log 解析已有 .log，输出错误/警告分类（不调用引擎）。

仅依赖 Python 标准库。缺引擎时记录 engine_missing 并退出码 0（不伪造成功）。
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

SCHEMA_VERSION = "1.0.0"
ENGINES = ("pdflatex", "xelatex", "lualatex", "latexmk")

# --- 错误分类正则：按优先级排序，先命中先归类 -------------------------------
ERROR_PATTERNS = [
    ("missing_package", r"! LaTeX Error: File `([^']+)\.(?:sty|cls)' not found"),
    ("missing_package", r"! LaTeX Error: File `([^']+)' not found"),
    ("file_not_found", r"! I can't find file `([^']+)'"),
    ("undefined_control_sequence", r"! Undefined control sequence"),
    ("math_error", r"! Missing [$\]] inserted"),
    ("math_error", r"! Double superscript"),
    ("math_error", r"! Double subscript"),
    ("math_error", r"! Extra alignment tab"),
    ("math_error", r"! Missing \\right"),
    ("math_error", r"! Missing \\left"),
    ("math_error", r"! Display math should end"),
    ("argument_error", r"! Missing \\begin\{document\}"),
    ("argument_error", r"! Extra \}"),
    ("argument_error", r"! Extra \{"),
    ("argument_error", r"! Paragraph ended before"),
    ("argument_error", r"! Misplaced alignment tab"),
    ("argument_error", r"! Too many \}"),
    ("argument_error", r"! You can't use `macro parameter character"),
    ("capacity_error", r"! TeX capacity exceeded"),
    ("runaway", r"! Runaway argument"),
    ("emergency_stop", r"! Emergency stop"),
    ("font_error", r"! Font [^\n]* not loadable"),
    ("font_error", r"! Package fontspec Error"),
    ("language_error", r"! Package babel Error"),
    ("package_error", r"! Package ([A-Za-z0-9]+) Error"),
    ("class_error", r"! Class ([A-Za-z0-9]+) Error"),
]

WARNING_PATTERNS = [
    ("undefined_reference", r"LaTeX Warning: Reference `([^']+)' on page (\d+) undefined"),
    ("undefined_reference", r"LaTeX Warning: There were undefined references"),
    ("undefined_citation", r"LaTeX Warning: Citation `([^']+)' on page (\d+) undefined"),
    ("undefined_citation", r"LaTeX Warning: There were undefined citations"),
    ("overfull", r"Overfull \\hbox"),
    ("underfull", r"Underfull \\hbox"),
    ("multiply_defined", r"LaTeX Warning: Label `([^']+)' multiply defined"),
    ("float_lost", r"LaTeX Warning: Float too large"),
]

LINE_RE = re.compile(r"^(.+?):(\d+):\s*(.*)$")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _read(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_json(path):
    txt = _read(path)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return None


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def _engine_command(engine, tex_dir, tex_file, halt=True):
    flags = ["-interaction=nonstopmode"]
    if halt:
        flags.append("-halt-on-error")
    # 输出目录保留在源目录，保证 .aux/.log 与主文件同处；PDF 预览在收尾阶段拷贝。
    if engine in ("pdflatex", "xelatex", "lualatex"):
        return [engine] + flags + [tex_file], tex_dir
    if engine == "latexmk":
        halt_flag = "-halt-on-error" if halt else ""
        return ([engine, "-pdf", halt_flag, tex_file] if halt_flag
                else [engine, "-pdf", tex_file]), tex_dir
    return None, tex_dir


def _log_path(main_tex):
    return os.path.splitext(main_tex)[0] + ".log"


def classify_log_lines(log_text):
    """Return (errors, warnings) with file/line/category/root_cause."""
    errors = []
    warnings = []
    for raw_line in (log_text or "").splitlines():
        line = raw_line.strip()
        for category, pattern in ERROR_PATTERNS:
            m = re.search(pattern, line)
            if m:
                loc = _locate(line, raw_line)
                errors.append({
                    "category": category,
                    "file": loc.get("file"),
                    "line": loc.get("line"),
                    "raw": line,
                    "detail": m.group(1) if m.groups() else "",
                })
                break
        else:
            for category, pattern in WARNING_PATTERNS:
                m = re.search(pattern, line)
                if m:
                    loc = _locate(line, raw_line)
                    warnings.append({
                        "category": category,
                        "file": loc.get("file"),
                        "line": loc.get("line"),
                        "raw": line,
                        "detail": m.group(1) if m.groups() else "",
                    })
                    break
    return errors, warnings


def _locate(line, raw_line):
    """从 LaTeX 错误上下文行里抽取文件:行号。"""
    file_, line_no = None, None
    m = LINE_RE.match(line)
    if m:
        file_, line_no = m.group(1), m.group(2)
    # LaTeX 常见 "l.123 text" 形式，靠后续上下文补；此处保守返回 None。
    return {"file": file_, "line": int(line_no) if line_no and line_no.isdigit() else None}


def root_causes(errors):
    """合并同类错误为根因条目，避免只报第一条。"""
    by_cat = {}
    for e in errors:
        key = (e["category"], e.get("detail", ""))
        by_cat.setdefault(key, []).append(e)
    causes = []
    for i, ((cat, detail), items) in enumerate(by_cat.items(), 1):
        first = items[0]
        loc = ""
        if first.get("file") and first.get("line"):
            loc = "%s:%s" % (first["file"], first["line"])
        causes.append({
            "id": "RC%02d" % i,
            "category": cat,
            "detail": detail,
            "file_line": loc or None,
            "count": len(items),
            "samples": [it["raw"][:200] for it in items[:3]],
        })
    return causes


def _new_report(case_id="unknown"):
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "engine": {"distribution": None, "version": None, "command": None},
        "passes": [],
        "root_causes": [],
        "refcheck": {"undefined_refs": [], "undefined_cites": [], "unused_labels": []},
        "final_status": "not_compiled",
        "limitations": [],
    }


def cmd_compile(args):
    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    main_tex = args.main
    if not os.path.isfile(main_tex):
        print(json.dumps({"error": "main.tex not found: %s" % main_tex}, ensure_ascii=False))
        return 0
    tex_dir = os.path.dirname(os.path.abspath(main_tex))
    base = os.path.basename(main_tex)
    report = _load_report(args) or _new_report(args.case_id)

    engine = args.engine or _pick_engine()
    report["engine"]["command"] = engine or None
    report["engine"]["distribution"] = engine or None
    report["engine"]["version"] = _engine_version(engine) if engine else None

    if engine is None:
        report["final_status"] = "degraded"
        report["limitations"] = [{
            "kind": "engine_missing",
            "note": "no pdflatex/xelatex/lualatex/latexmk found; editable .tex still delivered, but no compile",
        }]
        _write_json(os.path.join(args.work, "compile_report.json"), report)
        print(json.dumps({"final_status": "degraded",
                          "reason": "engine_missing"}, ensure_ascii=False, indent=2))
        return 0

    passes = max(1, args.passes)
    last_errors = []
    all_root = []
    for p in range(1, passes + 1):
        cmd, cwd = _engine_command(engine, tex_dir, base, halt=True)
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=args.timeout)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            exit_code = -9
        except OSError as exc:
            exit_code = -1
        log_text = _read(_log_path(os.path.join(tex_dir, base))) or ""
        errs, warns = classify_log_lines(log_text)
        causes = root_causes(errs)
        all_root = causes if causes else all_root
        last_errors = errs
        report["passes"].append({
            "pass": p,
            "engine": engine,
            "exit_code": exit_code,
            "error_count": len(errs),
            "warning_count": len(warns),
            "root_causes": causes,
            "warnings": warns,
            "log": _log_path(os.path.join(tex_dir, base)),
        })
        if exit_code != 0:
            break  # halt-on-error 已停在首个致命错误；根因已分类记录。

    report["root_causes"] = all_root
    report["final_status"] = "compiled" if (last_errors == [] and report["passes"][-1]["exit_code"] == 0) else "not_compiled"
    # refcheck
    refs = cmd_refcheck_impl(tex_dir, getattr(args, "bib", None))
    report["refcheck"] = refs
    if refs["undefined_refs"] or refs["undefined_cites"]:
        report["limitations"] = list(report.get("limitations", [])) + [{
            "kind": "undefined_reference",
            "note": "undefined refs/cites remain; see refcheck",
        }]

    _write_json(os.path.join(args.work, "compile_report.json"), report)

    # 拷贝 PDF 预览到 outdir
    pdf = os.path.join(tex_dir, os.path.splitext(base)[0] + ".pdf")
    if os.path.isfile(pdf) and report["final_status"] == "compiled":
        try:
            shutil.copy2(pdf, os.path.join(args.outdir, os.path.basename(pdf)))
        except OSError:
            pass

    print(json.dumps({
        "final_status": report["final_status"],
        "passes": len(report["passes"]),
        "root_causes": report["root_causes"],
        "refcheck": refs,
    }, ensure_ascii=False, indent=2))
    return 0


def _load_report(args):
    path = os.path.join(args.work, "compile_report.json")
    return _read_json(path)


def _pick_engine():
    for e in ("xelatex", "pdflatex", "lualatex", "latexmk"):
        if shutil.which(e):
            return e
    return None


def _engine_version(engine):
    if engine is None:
        return None
    try:
        proc = subprocess.run([engine, "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=15)
        first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        return first.strip()[:120] or None
    except Exception:
        return None


def cmd_classify(args):
    log_text = _read(args.log)
    if log_text is None:
        print(json.dumps({"error": "log not found: %s" % args.log}, ensure_ascii=False))
        return 0
    errs, warns = classify_log_lines(log_text)
    print(json.dumps({
        "log": args.log,
        "errors": errs,
        "warnings": warns,
        "root_causes": root_causes(errs),
    }, ensure_ascii=False, indent=2))
    return 0


# --- refcheck ---------------------------------------------------------------
_LABEL_DEF_RE = re.compile(r"\\newlabel\{([^}]+)\}")
_BIB_DEF_RE = re.compile(r"\\bibcite\{([^}]+)\}")
_REF_USE_RE = re.compile(r"\\(?:eqref|ref|pageref|autoref|cref|Cref|vref|nameref)\{([^}]+)\}")
_CITE_USE_RE = re.compile(r"\\(?:cite|citet|citep|parencite|textcite|autocite|footcite|citealp|citealt)\{([^}]+)\}")


def _collect_tex_files(directory):
    out = []
    for dirpath, _, filenames in os.walk(directory):
        for name in filenames:
            if name.endswith(".tex"):
                out.append(os.path.join(dirpath, name))
    return out


def cmd_refcheck_impl(directory, bib_path=None):
    labels_defined = set()
    cites_defined = set()
    labels_used = set()
    cites_used = set()

    for dirpath, _, filenames in os.walk(directory):
        for name in filenames:
            full = os.path.join(dirpath, name)
            if name.endswith(".aux"):
                txt = _read(full) or ""
                labels_defined.update(_LABEL_DEF_RE.findall(txt))
            if name.endswith(".bbl"):
                txt = _read(full) or ""
                cites_defined.update(_BIB_DEF_RE.findall(txt))

    for tex in _collect_tex_files(directory):
        txt = _read(tex) or ""
        # 去掉注释行再统计使用
        clean = "\n".join(l for l in txt.splitlines() if not l.lstrip().startswith("%"))
        labels_used.update(_REF_USE_RE.findall(clean))
        for chunk in _CITE_USE_RE.findall(clean):
            for key in chunk.split(","):
                key = key.strip()
                if key:
                    cites_used.add(key)

    undefined_refs = sorted(labels_used - labels_defined)
    undefined_cites = sorted(cites_used - cites_defined)
    unused_labels = sorted(labels_defined - labels_used)
    return {
        "undefined_refs": undefined_refs,
        "undefined_cites": undefined_cites,
        "unused_labels": unused_labels,
    }


def cmd_refcheck(args):
    result = cmd_refcheck_impl(args.dir, args.bib)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="latex_tools.py",
                                     description="LaTeX compile/classify/refcheck")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("compile")
    p.add_argument("--main", required=True)
    p.add_argument("--outdir", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--case-id", default="unknown")
    p.add_argument("--passes", type=int, default=3)
    p.add_argument("--engine", default=None)
    p.add_argument("--timeout", type=int, default=120)

    p = sub.add_parser("classify-log")
    p.add_argument("--log", required=True)

    p = sub.add_parser("refcheck")
    p.add_argument("--dir", required=True)
    p.add_argument("--bib", default=None)
    return parser


def main(argv=None):
    reconfigure_stdio()
    args = build_parser().parse_args(argv)
    handlers = {
        "compile": cmd_compile,
        "classify-log": cmd_classify,
        "refcheck": cmd_refcheck,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
