#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
latex_tools.py — LaTeX 编译 / 引用校验 / 错误分类。

三个子命令：
  compile      多遍编译（halt-on-error + nonstopmode），记录 engine/宏包/轮次，
               产出 compile_report.json；缺引擎时记 engine_missing，不谎报成功。
  refcheck     解析 .log/.aux，检查 undefined/multiply-defined 引用与引用、
               未使用 label、缺失 .bib 条目。
  classify-log 把 LaTeX 日志分类为「文件:行号 + 类别 + 根因 + 修复建议」，
               并做级联判定（首错=根因，后续错误标 cascade）。

仅依赖 Python 标准库。所有命令对非致命失败退出码 0；classify-log 在日志
存在但无引擎时可正常解析本地日志文件，不依赖实时编译。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ALLOWED_ENGINES = ("pdflatex", "xelatex", "lualatex")
DEFAULT_ENGINE = "pdflatex"

# 阻断性错误类别 → 正则（按优先级排序，不锚定行首，兼容 `! msg` 与 `file:line: msg` 两种格式）
ERROR_PATTERNS = [
    ("missing_document_class", re.compile(
        r"File `([^']+)\.cls' not found", re.I)),
    ("missing_package", re.compile(
        r"File `([^']+)\.sty' not found", re.I)),
    ("file_not_found", re.compile(
        r"File `([^']+)' not found", re.I)),
    ("undefined_control_sequence", re.compile(
        r"Undefined control sequence", re.I)),
    ("math_error", re.compile(
        r"Missing [$\]] inserted|Double superscript|Double subscript|Extra \}|"
        r"Display math should end with \$\$|Extra alignment tab has been changed to \\cr|"
        r"Missing number, treated as zero", re.I)),
    ("bad_environment", re.compile(
        r"Environment .* undefined", re.I)),
    ("runaway_argument", re.compile(r"Runaway argument\?", re.I)),
    ("font_error", re.compile(
        r"(?:Font|T1|OT1|fontspec|Font file).*?(?:not found|undefined)", re.I)),
    ("capacity_exceeded", re.compile(
        r"TeX capacity exceeded|Dimension too large", re.I)),
    ("emergency_stop", re.compile(r"Emergency stop", re.I)),
]

# 非阻断警告 → 正则（不计入级联根因）
WARNING_PATTERNS = [
    ("undefined_reference", re.compile(
        r"(?:Reference|Citation) `([^']+)' on page \d+ undefined", re.I)),
    ("multiply_defined", re.compile(
        r"(?:Label|Citation) `([^']+)' multiply defined", re.I)),
    ("unused_reference", re.compile(
        r"There were undefined references|There were multiply-defined labels", re.I)),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def find_engine(engine):
    """返回可执行引擎名，缺引擎返回 None。"""
    if engine not in ALLOWED_ENGINES:
        engine = DEFAULT_ENGINE
    if shutil.which(engine):
        return engine
    # 兜底：优先 xelatex 支持 Unicode，其次 pdflatex
    for cand in ("xelatex", "pdflatex", "lualatex"):
        if cand != engine and shutil.which(cand):
            return cand
    return None


def run_engine(engine, main, outdir, jobname, passes):
    """运行 LaTeX 引擎多遍，返回 (report, log_path, ok)。"""
    main_abs = os.path.abspath(main)
    outdir_abs = os.path.abspath(outdir)
    os.makedirs(outdir_abs, exist_ok=True)
    workdir = os.path.dirname(main_abs)
    base = jobname or os.path.splitext(os.path.basename(main_abs))[0]
    log_path = os.path.join(outdir_abs, base + ".log")

    resolved = find_engine(engine)
    report = {
        "engine_requested": engine,
        "engine_resolved": resolved,
        "main": main_abs,
        "outdir": outdir_abs,
        "jobname": base,
        "passes_requested": passes,
        "passes_run": 0,
        "compiled": False,
        "engine_missing": resolved is None,
        "log_path": log_path,
        "steps": [],
    }
    if resolved is None:
        report["error"] = "no LaTeX engine found on PATH (checked %s)" % ", ".join(ALLOWED_ENGINES)
        report["fix"] = "install a TeX distribution (TeX Live / MiKTeX) and put %s on PATH" % DEFAULT_ENGINE
        return report, None, False

    for i in range(1, passes + 1):
        cmd = [
            resolved, "-halt-on-error", "-interaction=nonstopmode",
            "-file-line-error", "-synctex=1",
            "-output-directory", outdir_abs,
            "-jobname", base, main_abs,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=workdir, capture_output=True, text=True, timeout=120,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            report["steps"].append({"pass": i, "ok": False, "error": "engine timeout after 120s"})
            report["error"] = "compile timeout on pass %d" % i
            break
        except OSError as exc:
            report["steps"].append({"pass": i, "ok": False, "error": str(exc)})
            report["error"] = "engine launch failed: %s" % exc
            break
        ok = (proc.returncode == 0)
        report["steps"].append({
            "pass": i, "ok": ok, "returncode": proc.returncode,
            "stderr_tail": (proc.stderr or "")[-500:],
        })
        report["passes_run"] = i
        if not ok:
            report["error"] = "compile failed on pass %d (returncode=%d)" % (i, proc.returncode)
            break
    if report["passes_run"] == passes and all(s["ok"] for s in report["steps"]):
        report["compiled"] = True
        report["pdf_path"] = os.path.join(outdir_abs, base + ".pdf")
    return report, log_path, report["compiled"]


def cmd_compile(args):
    os.makedirs(args.outdir, exist_ok=True)
    report, log_path, ok = run_engine(
        args.engine, args.main, args.outdir, args.jobname, args.passes)
    # 若编译过但 log 在 outdir，记录下来；失败时附加错误分类
    if log_path and os.path.isfile(log_path):
        errors, warnings = classify_log_text(_read(log_path), args.main)
        report["classified_errors"] = errors
        report["classified_warnings"] = warnings
    report_path = os.path.join(args.outdir, "compile_report.json")
    _atomic_write_json(report_path, report)
    print(json.dumps({
        "compiled": ok,
        "engine": report["engine_resolved"],
        "passes_run": report["passes_run"],
        "pdf_path": report.get("pdf_path"),
        "report_path": report_path,
        "error": report.get("error"),
    }, ensure_ascii=False, indent=2))
    return 0


def _read(path):
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc, errors="replace") as fh:
                return fh.read()
        except (OSError, UnicodeError):
            continue
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _atomic_write_json(path, obj):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".report-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def classify_log_text(text, main_path=None):
    """把日志文本分类为阻断错误 + 非阻断警告，并做级联判定。"""
    errors = []
    warnings = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        matched = False
        for category, pattern in ERROR_PATTERNS:
            m = pattern.search(stripped)
            if m:
                loc = _extract_loc(stripped, main_path)
                errors.append({
                    "category": category,
                    "severity": "error",
                    "message": stripped[:300],
                    "file": loc.get("file"),
                    "line": loc.get("line"),
                    "root_cause": _root_cause(category, stripped),
                    "fix": _fix_hint(category, stripped),
                })
                matched = True
                break
        if matched:
            continue
        for category, pattern in WARNING_PATTERNS:
            m = pattern.search(stripped)
            if m:
                loc = _extract_loc(stripped, main_path)
                warnings.append({
                    "category": category,
                    "severity": "warning",
                    "message": stripped[:300],
                    "file": loc.get("file"),
                    "line": loc.get("line"),
                    "root_cause": _root_cause(category, stripped),
                    "fix": _fix_hint(category, stripped),
                })
                break
    # 级联判定：阻断错误中第一条为根因，其余标 cascade；警告不计入根因链
    for idx, err in enumerate(errors):
        err["cascade"] = idx > 0
    for w in warnings:
        w["cascade"] = False
    return errors, warnings


def _extract_loc(line, main_path):
    """从 `path:line:` 前缀提取定位（兼容 ./main.tex:12: 与 main.tex:12:）。"""
    loc = {}
    m = re.match(r"^(?:\./)?([^:\s]+):(\d+):", line)
    if m:
        loc["file"] = m.group(1)
        loc["line"] = int(m.group(2))
    elif main_path:
        loc["file"] = os.path.basename(main_path)
    return loc


def _root_cause(category, line):
    causes = {
        "missing_document_class": "文档类文件缺失，模板未安装或路径未加入 TEXINPUTS",
        "missing_package": "宏包文件缺失，未安装对应宏包或加载顺序/版本不匹配",
        "file_not_found": "被引用文件缺失（input/include/usepackage 目标不存在）",
        "undefined_control_sequence": "命令未定义：宏包未加载、拼写错误或命令环境不匹配",
        "undefined_reference": "交叉引用/引用未定义：缺少对应 \\label/\\bibitem 或多遍编译未完成",
        "multiply_defined": "label/citation 被重复定义：存在重复 \\label 或重复 .bib 条目",
        "unused_reference": "存在未解析的引用/标签（多遍编译未收敛或标签缺失）",
        "math_error": "数学模式语法错误：括号/上下标/分隔符不配对",
        "bad_environment": "环境名未定义或 \\begin/\\end 不配对",
        "runaway_argument": "参数未闭合：缺少右花括号或环境结束标记",
        "font_error": "字体文件/字库缺失，或 fontspec 找不到声明字体",
        "capacity_exceeded": "TeX 容量超限或长度超界（如 Dimension too large）",
        "emergency_stop": "致命错误导致引擎紧急停止",
    }
    return causes.get(category, "未归类的编译错误")


def _fix_hint(category, line):
    hints = {
        "missing_document_class": "确认模板 .cls 在 /input 或 TEXINPUTS；缺模板时交付可编辑 .tex 并记录 template_missing",
        "missing_package": "减少非必要宏包、检查加载顺序；仍缺则记录 missing_package，不谎报编译成功",
        "file_not_found": "补齐被引用文件，或改用绝对/相对正确路径",
        "undefined_control_sequence": "补加载定义该命令的宏包，或修正拼写",
        "undefined_reference": "补 \\label/\\bibitem，并多遍编译至引用收敛",
        "multiply_defined": "删除重复 \\label 或去重 .bib 条目",
        "unused_reference": "核对 \\label/\\bibitem 与正文引用是否一一对应",
        "math_error": "成对检查 $…$ / \\(…\\) / \\[…\\]，修正上下标与括号",
        "bad_environment": "核对环境名拼写，确保 \\begin/\\end 成对",
        "runaway_argument": "定位并闭合缺失的花括号/环境",
        "font_error": "安装缺失字体，或在模板字体声明处回退到可用字体",
        "capacity_exceeded": "检查是否引入超大长度/循环；必要时简化表图",
        "emergency_stop": "以上一条非 cascade 错误为根因修复",
    }
    return hints.get(category, "定位文件:行号后按对应类别修复")


def cmd_classify(args):
    text = _read(args.log)
    if not text:
        print(json.dumps({"error": "log not readable: " + args.log, "errors": []},
                         ensure_ascii=False, indent=2))
        return 0
    errors, warnings = classify_log_text(text, args.main)
    out = {
        "log": args.log,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "categories": sorted({e["category"] for e in errors}),
        "errors": errors,
        "warnings": warnings,
        "cascade_root": errors[0] if errors else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------
# refcheck：解析 .log/.aux 的引用与引用完整性
# --------------------------------------------------------------------------

def _collect_files(directory, ext):
    out = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if name.endswith(ext):
            out.append(os.path.join(directory, name))
    return out


def cmd_refcheck(args):
    directory = args.dir
    logs = _collect_files(directory, ".log")
    auxs = _collect_files(directory, ".aux")
    text = "\n".join(_read(p) for p in logs + auxs)

    undefined_refs = sorted(set(re.findall(
        r"LaTeX Warning: Reference `([^']+)' on page \d+ undefined", text)))
    undefined_cites = sorted(set(re.findall(
        r"LaTeX Warning: Citation `([^']+)' on page \d+ undefined", text)))
    multiply = sorted(set(re.findall(
        r"LaTeX Warning: (?:Label|Citation) `([^']+)' multiply defined", text)))
    # 未使用 label（.log 中 LaTeX Warning: There were undefined references 之外，
    # 仅在 .aux 有定义而正文无 ref 时较难判定；这里给 multiply + undefined 两类保守结论）
    report = {
        "dir": os.path.abspath(directory),
        "log_count": len(logs),
        "aux_count": len(auxs),
        "undefined_refs": undefined_refs,
        "undefined_cites": undefined_cites,
        "multiply_defined": multiply,
        "clean": not (undefined_refs or undefined_cites or multiply),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="latex_tools.py",
        description="LaTeX 编译 / 引用校验 / 错误分类工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_compile = sub.add_parser("compile", help="多遍编译并产出 compile_report.json")
    p_compile.add_argument("--main", required=True, help="主 .tex 文件路径")
    p_compile.add_argument("--outdir", required=True, help="编译产物输出目录")
    p_compile.add_argument("--passes", type=int, default=3, help="最大编译轮次（默认 3）")
    p_compile.add_argument("--engine", default=DEFAULT_ENGINE, help="引擎（pdflatex/xelatex/lualatex）")
    p_compile.add_argument("--jobname", default=None, help="job 名（默认取主文件名）")
    p_compile.set_defaults(func=cmd_compile)

    p_ref = sub.add_parser("refcheck", help="校验 .log/.aux 中的引用完整性")
    p_ref.add_argument("--dir", required=True, help="含 .log/.aux 的目录")
    p_ref.set_defaults(func=cmd_refcheck)

    p_cls = sub.add_parser("classify-log", help="把日志分类为文件:行号+类别+根因+修复")
    p_cls.add_argument("--log", required=True, help="LaTeX .log 文件")
    p_cls.add_argument("--main", default=None, help="主 .tex（用于缺行号时的回退定位）")
    p_cls.set_defaults(func=cmd_classify)
    return parser


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, 2) else exc.code
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
