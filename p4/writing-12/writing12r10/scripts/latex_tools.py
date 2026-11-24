#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
latex_tools.py — 编译编排、日志错误分类与交叉引用校验。

与参考包不同之处：
  * 把"编译记录"做成可复算的 build_manifest.json：记录引擎、宏包探测、
    字体依赖、精确编译命令、轮次与每轮 log 的 SHA-256 指纹；
  * 报错级联采用"根因判定"：按 文件:行号 首次出现排序，把同一未定义命令 /
    缺失文件引发的下游报错归并为 cascade，只保留根因 + 修复策略；
  * 不依赖平台私有工具：优先探测 PATH 中的 TeX 引擎，找不到则如实记录
    engine_missing，仍可分类已有 .log，绝不声称编译成功。

仅依赖 Python 标准库。
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
import core_impl  # noqa: E402

ENGINE_PRIORITY = ["latexmk", "pdflatex", "xelatex", "lualatex", "tectonic"]
# 错误分类 -> 修复策略（供 Agent 参考，不含具体改动）
FIX_HINTS = {
    "missing_package": "install the package or replace with an available equivalent",
    "missing_file": "restore/include the referenced file, or correct the path",
    "undefined_control_sequence": "define the macro or fix the typo/load the required package",
    "math_error": "fix brace/grouping, $ pairing, or sub/superscript structure",
    "undefined_reference": "ensure \\label exists and rerun to resolve; fix key spelling",
    "undefined_citation": "add the .bib entry, rerun bibtex/biber, or fix the cite key",
    "font_error": "install the font or substitute an available font family",
    "capacity_exceeded": "restructure the offending construct or raise the relevant limit",
    "runaway_argument": "fix the unbalanced brace / missing \\end{...}",
    "unknown": "inspect surrounding lines for the root cause",
}


def find_engine():
    """返回第一个可用的 TeX 引擎，找不到返回 None。"""
    for eng in ENGINE_PRIORITY:
        if shutil.which(eng):
            return eng
    return None


def detect_packages(tex_text):
    r"""从 \usepackage 与 \documentclass 提取宏包/文档类清单。"""
    pkgs = set()
    for m in re.finditer(r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{([^}]+)\}", tex_text):
        for p in m.group(1).split(","):
            p = p.strip()
            if p:
                pkgs.add(p)
    cls = re.findall(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", tex_text)
    return {"class": cls, "packages": sorted(pkgs)}


def run_compile(main_tex, outdir, passes=2):
    """用可用引擎编译 main.tex，返回结构化 compile_report 与 build_manifest。

    找不到引擎时 engine_missing=True 且不声称编译成功；仍尝试分类现有 .log。
    """
    main_tex = os.path.abspath(main_tex)
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    workdir = os.path.dirname(main_tex)
    tex_name = os.path.basename(main_tex)
    engine = find_engine()

    report = {
        "main": main_tex,
        "outdir": outdir,
        "engine": engine,
        "engine_missing": engine is None,
        "passes_requested": passes,
        "passes": [],
        "success": False,
        "root_causes": [],
        "errors": [],
    }
    tex_text, _ = core_impl.read_text(main_tex)
    tex_text = tex_text or ""
    manifest = {
        "generated_at": core_impl.utcnow(),
        "main": os.path.relpath(main_tex, workdir).replace("\\", "/"),
        "engine": engine,
        "engine_missing": engine is None,
        "document": detect_packages(tex_text),
        "fonts": list(dict.fromkeys(re.findall(r"\\(?:setmainfont|setCJKmainfont|setmathfont)\s*\{([^}]*)\}", tex_text))),
        "command": None,
        "passes": [],
        "fingerprints": [],
    }

    if engine is None:
        report["errors"].append({"category": "engine_missing",
                                 "note": "no TeX engine in PATH (%s)" % ", ".join(ENGINE_PRIORITY)})
        log = _find_log(workdir, os.path.splitext(tex_name)[0])
        if log:
            report["errors"].extend(parse_log_errors(log, tex_text))
        return report, manifest

    base = os.path.splitext(tex_name)[0]
    # 多轮：latexmk 自带收敛；单引擎则重复 passes 次以收敛引用。
    if engine == "latexmk":
        cmd = [engine, "-pdf", "-interaction=nonstopmode", "-halt-on-error",
               "-file-line-error", "-cd", tex_name]
    else:
        cmd = [engine, "-interaction=nonstopmode", "-halt-on-error",
               "-file-line-error", tex_name]
    manifest["command"] = " ".join(cmd)

    for i in range(1, passes + 1):
        try:
            proc = subprocess.run(cmd, cwd=workdir, capture_output=True,
                                  text=True, timeout=180)
        except FileNotFoundError:
            report["engine_missing"] = True
            report["errors"].append({"category": "engine_missing",
                                     "note": "engine disappeared: %s" % engine})
            manifest["engine_missing"] = True
            break
        except subprocess.TimeoutExpired:
            report["errors"].append({"category": "timeout", "pass": i})
            break
        out = (proc.stdout or "") + (proc.stderr or "")
        log = _find_log(workdir, base) or ""
        log_text = log if log else out
        manifest["passes"].append({"pass": i, "exit_code": proc.returncode})
        manifest["fingerprints"].append(core_impl.sha256_hex(log_text))
        report["passes"].append({
            "pass": i, "exit_code": proc.returncode,
            "sha256": core_impl.sha256_hex(log_text),
        })
        errs = parse_log_errors(log_text, tex_text) if log_text else []
        report["errors"].extend(errs)
        if proc.returncode == 0:
            report["success"] = True
            break
    report["root_causes"] = root_cause(errors=report["errors"])
    _write_manifest(outdir, manifest)
    _write_report(outdir, report)
    return report, manifest


def _find_log(workdir, base):
    candidates = [os.path.join(workdir, base + ".log"),
                  os.path.join(workdir, "build", base + ".log"),
                  os.path.join(workdir, "out", base + ".log")]
    for c in candidates:
        if os.path.isfile(c):
            return core_impl.read_text(c)[0]
    return None


def _write_report(outdir, report):
    core_impl.write_json(os.path.join(outdir, "compile_report.json"), report)


def _write_manifest(outdir, manifest):
    core_impl.write_json(os.path.join(outdir, "build_manifest.json"), manifest)


# --------------------------------------------------------------------------- #
# 日志错误分类与根因判定                                                       #
# --------------------------------------------------------------------------- #

ERROR_LINE_RE = re.compile(r"^(.*?):(\d+):\s*(.*)$")  # file:line: message
LOG_ERR_RE = re.compile(r"^! (.*)$")                  # ! Error message
L_POS_RE = re.compile(r"^l\.(\d+)\s+(.*)$")           # l.123 ...


def classify_message(msg):
    m = msg or ""
    # 缺宏包/文档类：消息同时提到 .sty/.cls/.def 与"找不到/not found"
    if re.search(r"\.(?:sty|cls|def)\b", m, re.I) and re.search(r"not found|can't find|No file", m, re.I):
        return "missing_package"
    if re.search(r"can't find file|not found|No file", m, re.I):
        return "missing_file"
    if "Undefined control sequence" in m:
        return "undefined_control_sequence"
    if re.search(r"Missing [$\}] inserted|Extra \}|Double (super|sub)script|Missing \\right", m):
        return "math_error"
    if re.search(r"Reference .* undefined", m):
        return "undefined_reference"
    if re.search(r"Citation .* undefined", m):
        return "undefined_citation"
    if re.search(r"Font .* not found|fontspec error", m, re.I):
        return "font_error"
    if re.search(r"capacity exceeded|Dimension too large", m, re.I):
        return "capacity_exceeded"
    if re.search(r"Runaway argument|Paragraph ended before", m):
        return "runaway_argument"
    return "unknown"


def parse_log_errors(log_text, tex_text=None):
    """从 log 中抽取结构化错误：文件:行号 + 分类 + 原文。每条 `!` 报错独立成项。"""
    errors, current = [], None
    for line in (log_text or "").splitlines():
        m = ERROR_LINE_RE.match(line)
        if m and re.search(r"(error|Error|! )", m.group(3)):
            current = {
                "file": m.group(1).strip(),
                "line": int(m.group(2)) if m.group(2).isdigit() else None,
                "raw": line.strip(),
                "category": classify_message(m.group(3)),
                "message": m.group(3).strip(),
            }
            errors.append(current)
            continue
        lm = LOG_ERR_RE.match(line)
        if lm:
            current = {
                "file": None, "line": None, "raw": line.strip(),
                "category": classify_message(lm.group(1)),
                "message": lm.group(1).strip(),
            }
            errors.append(current)
            continue
        pm = L_POS_RE.match(line)
        if pm and current is not None:
            if current.get("line") is None:
                current["line"] = int(pm.group(1))
            current.setdefault("source_snippet", pm.group(2).strip())
    for e in errors:
        e["hint"] = FIX_HINTS.get(e["category"], FIX_HINTS["unknown"])
    return errors


def root_cause(errors):
    """报错级联根因判定：按 文件:行号 排序，把同源下游报错归并为 cascade。"""
    if not errors:
        return []
    ordered = sorted(errors, key=lambda e: (e.get("file") or "", e.get("line") or 10**9))
    roots, seen_keys = [], set()
    for e in ordered:
        key = (e.get("file"), e.get("line"), e.get("category"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        # 相同 file:line 且不同 category 的后续项视为 cascade
        cascade = [o for o in errors if o is not e and o.get("file") == e.get("file")
                   and o.get("line") == e.get("line")]
        roots.append({
            "file": e.get("file"), "line": e.get("line"),
            "category": e["category"], "message": e.get("message", ""),
            "hint": e.get("hint", ""),
            "cascade_count": len(cascade),
        })
    return roots


# --------------------------------------------------------------------------- #
# 交叉引用校验                                                                #
# --------------------------------------------------------------------------- #

LABEL_RE = re.compile(r"\\label\s*\{([^}]*)\}")
REF_ANY_RE = re.compile(r"\\(?:ref|eqref|pageref|autoref|cref|Cref)\s*\{([^}]*)\}")
CITE_ANY_RE = re.compile(r"\\(?:cite|citet|citep|citeauthor|citeyear)\s*\{([^}]*)\}")


def refcheck(tex_path, log_path=None, bib_path=None):
    r"""校验 \label / \ref / \cite 是否闭合。返回报告。"""
    tex_text, _ = core_impl.read_text(tex_path) or ("", None)
    labels = set(LABEL_RE.findall(tex_text or ""))
    refs = set()
    for m in REF_ANY_RE.finditer(tex_text or ""):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                refs.add(k)
    cites = set()
    for m in CITE_ANY_RE.finditer(tex_text or ""):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                cites.add(k)
    undefined_refs = sorted(r for r in refs if r not in labels)
    undefined_cites = sorted(cites)  # 需 bib 才能判；无 bib 全部标记为待确认
    if bib_path and os.path.isfile(bib_path):
        bib_text, _ = core_impl.read_text(bib_path) or ("", None)
        bib_keys = set(re.findall(r"@\w+\s*\{([^,}]+)", bib_text or ""))
        undefined_cites = sorted(c for c in cites if c not in bib_keys)
    elif log_path and os.path.isfile(log_path):
        log_text, _ = core_impl.read_text(log_path) or ("", None)
        cited_in_log = set(re.findall(r"Citation [`']([^'`]+)' on page", log_text or ""))
        undefined_cites = sorted(c for c in cites if c not in cited_in_log)
    else:
        undefined_cites = []  # 无法判定时不误报
    return {
        "labels": sorted(labels),
        "refs": sorted(refs),
        "cites": sorted(cites),
        "undefined_refs": undefined_refs,
        "undefined_cites": undefined_cites,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="latex_tools.py",
                                 description="LaTeX 编译编排与日志分类（仅标准库）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_compile = sub.add_parser("compile", help="compile a main.tex")
    p_compile.add_argument("--main", required=True)
    p_compile.add_argument("--outdir", required=True)
    p_compile.add_argument("--passes", type=int, default=2)

    p_classify = sub.add_parser("classify-log", help="classify errors in a log file")
    p_classify.add_argument("--log", required=True)
    p_classify.add_argument("--main", default=None)

    p_ref = sub.add_parser("refcheck", help="check refs/cites in a tex file")
    p_ref.add_argument("--tex", required=True)
    p_ref.add_argument("--log", default=None)
    p_ref.add_argument("--bib", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "compile":
        report, manifest = run_compile(args.main, args.outdir, args.passes)
        print(json.dumps({"success": report["success"],
                          "engine": report["engine"],
                          "engine_missing": report["engine_missing"],
                          "passes": report["passes"],
                          "root_causes": report["root_causes"]},
                         ensure_ascii=False, indent=2))
        return 0 if report["success"] or report["engine_missing"] else 1
    if args.cmd == "classify-log":
        text, err = core_impl.read_text(args.log)
        if text is None:
            print(json.dumps({"error": err}, ensure_ascii=False))
            return 1
        tex_text = core_impl.read_text(args.main)[0] if args.main else None
        errors = parse_log_errors(text, tex_text)
        print(json.dumps({"errors": errors, "root_causes": root_cause(errors)},
                         ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "refcheck":
        print(json.dumps(refcheck(args.tex, args.log, args.bib),
                         ensure_ascii=False, indent=2))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
