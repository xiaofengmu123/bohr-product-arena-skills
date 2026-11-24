#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — LaTeX 写作与编译 Skill 的确定性工具库。

本模块是 Skill 包的唯一“重逻辑”实现，供 main.py（命令行入口）与 selftest.py
（自检）共同调用。设计目标只有一条：**同一份 /input、同一运行环境，两次运行
得到字节级一致的 /app/output/result.json 与主产物**。

为此遵守以下铁律（任何改动不得破坏）：
  1. 仅使用 Python 标准库；外部程序（pdftotext / TeX 引擎）用 shutil.which 探测，
     探测不到时如实记录，绝不声称成功。
  2. 确定性：所有目录/文件/字典/列表遍历先排序；JSON 一律 sort_keys=True 且
     ensure_ascii=False；不使用墙钟时间、随机数、对象 id、进程号。
  3. 时间戳一律用 `run_id`（对 /input 的规范化内容求 SHA-256 得到的确定性 token）
     代替；墙钟时间只允许出现在 humans 不可见的调试打印中，绝不写入产物文件。
  4. 事实只来自材料：`quote` 只返回文件中的逐字片段；缺失/冲突显式标记，不补造。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024          # 2 MiB，超限跳过并记录
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst",
    ".xml", ".html", ".htm", ".tsv",
}
BINARY_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".eps"}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")
SCHEMA_VERSION = "1.0.0"
PRODUCER = "latex-writing-compile"

# 默认运行时路径（与平台 Harness 契约一致）
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

# 常见 TeX 引擎探测顺序（skill 在 SKILL.md 中要求优先使用平台 latex-compiler 工具；
# 本地脚本只负责兜底与纯文本诊断，不做“假装编译成功”）。
ENGINE_CANDIDATES = ("tectonic", "xelatex", "lualatex", "pdflatex", "latexmk")


def reconfigure_stdio():
    """确保 stdout/stderr 为 UTF-8，避免非 UTF-8 终端下输出崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def utcnow_human_only():
    """仅供 stderr 调试打印使用；禁止写入任何产物文件（会破坏确定性）。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj):
    """确定性 JSON 字符串：键排序、紧凑分隔、UTF-8 不转义。"""
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )


def read_bytes(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds 2MiB"
    return raw, None


def read_text(path):
    """读取 UTF-8 文本，容错 BOM / 坏编码。返回 (text, error)。"""
    raw, err = read_bytes(path)
    if raw is None:
        return None, err or "unreadable"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def read_json(path):
    """容错 JSON 读取。返回 (obj, errors_list)。"""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[: exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json(path, obj):
    """确定性写盘：目录自动创建，键排序。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(obj) + "\n")
    return path


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    raw, err = read_bytes(path)
    if raw is None:
        return hashlib.sha256(err.encode("utf-8")).hexdigest()
    return sha256_bytes(raw)


def discover_input(root):
    """扫描 /input 下的所有文件（排序稳定）。不假设固定文件名或键名。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "path": full,
                "rel": os.path.relpath(full, root).replace("\\", "/"),
                "size": size,
                "ext": ext,
                "is_text": ext in TEXT_EXTS,
                "sha256": sha256_file(full),
            })
    return files


def run_id_for(root):
    """对输入目录内容求确定性 token：relpath 排序 + 逐文件内容哈希。"""
    h = hashlib.sha256()
    for item in discover_input(root):
        h.update(item["rel"].encode("utf-8"))
        h.update(b"\x00")
        h.update(item["sha256"].encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def find_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    return None


def contract_keys(obj):
    """提取 output_contract 需原样回填的键（保持出现顺序，去重）。"""
    if not isinstance(obj, dict):
        return []
    contract = obj.get("output_contract")
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(list(item.keys()))
    seen, ordered = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def build_skeleton(root):
    """从 /input/case.json 构造 result.json 骨架（先落盘，后增量改进）。"""
    case_path = os.path.join(root, "case.json")
    notes = []
    case_id = None
    keys = []
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(obj)
        keys = contract_keys(obj)
        if not keys:
            notes.append("output_contract_not_found: metadata-only result")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    run_id = run_id_for(root)
    skeleton = {"case_id": case_id}
    for key in keys:
        skeleton[key] = None
    skeleton.update({
        "status": "partial" if keys else "input_error",
        "artifacts": {},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "at": run_id,
            "step": "bootstrap",
            "note": "result skeleton written before analysis; contract fields default to null",
        }],
        "errors": notes,
        "degradations": [],
        "limitations": [],
        "runtime": {
            "run_id": run_id,
            "deterministic": True,
            "producer": PRODUCER,
            "schema_version": SCHEMA_VERSION,
        },
    })
    return skeleton, keys


# --------------------------------------------------------------------------- #
# 证据溯源
# --------------------------------------------------------------------------- #

def index_evidence(root, out_path):
    """为 /input 下每个文本文件建立行索引（rel、行数、行内容、sha256），
    供 quote 与人工复核使用。输出到 work/source_index.json。"""
    entries = []
    for item in discover_input(root):
        if not item["is_text"]:
            continue
        text, err = read_text(item["path"])
        if text is None:
            entries.append({**item, "lines": [], "error": err})
            continue
        lines = text.splitlines()
        entries.append({**item, "line_count": len(lines), "lines": lines,
                        "read_error": err})
    write_json(out_path, {"input_root": root, "files": entries})
    return out_path


def quote_file(path, start=1, end=None):
    """返回文件 [start, end] 行的逐字片段（含行号前缀）。返回 (text, err)。"""
    text, err = read_text(path)
    if text is None:
        return None, err
    lines = text.splitlines()
    start = max(1, int(start))
    end = len(lines) if end is None else min(len(lines), int(end))
    if start > len(lines):
        return "", "start_beyond_eof"
    if end < start:
        return "", "empty_range"
    rendered = []
    for i in range(start - 1, end):
        rendered.append("%d\t%s" % (i + 1, lines[i]))
    return "\n".join(rendered), None


def normalize_locator(path, root):
    """把绝对/相对路径规范为相对 /input 的 locator（#L 行号由调用方拼接）。"""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    return rel.replace("\\", "/")


# --------------------------------------------------------------------------- #
# LaTeX 编译与诊断（本地兜底；SKILL.md 首选平台 latex-compiler 工具）
# --------------------------------------------------------------------------- #

def find_engine(pref="auto"):
    order = list(ENGINE_CANDIDATES)
    if pref != "auto":
        order = [pref] + [x for x in order if x != pref]
    for name in order:
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


def compile_tex(main, outdir, passes=3, engine_pref="auto"):
    """本地编译兜底。返回 compile_report（确定性的命令与轮次记录）。"""
    main = os.path.abspath(main)
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    engine, engine_path = find_engine(engine_pref)
    if engine is None:
        return {
            "ok": False,
            "engine_missing": True,
            "engine": None,
            "command": ["(no TeX engine found on PATH)"],
            "passes": 0,
            "exit_codes": [],
            "log_path": None,
            "pdf_path": None,
            "errors": [{"file": None, "line": None,
                        "category": "engine_missing",
                        "message": "no TeX engine on PATH; use platform latex-compiler tool"}],
            "note": "degradation recorded; editable .tex still delivered",
        }

    jobdir = os.path.dirname(main) or "."
    exit_codes, last_log = [], None
    for _ in range(max(1, int(passes))):
        cmd = [engine_path, "-halt-on-error", "-interaction=nonstopmode",
               "-file-line-error", "-output-directory=%s" % outdir, main]
        try:
            proc = subprocess.run(
                cmd, cwd=jobdir, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=240,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            exit_codes.append("error:%s" % exc)
            break
        exit_codes.append(proc.returncode)

    jobname = os.path.splitext(os.path.basename(main))[0]
    log_path = os.path.join(outdir, jobname + ".log")
    pdf_path = os.path.join(outdir, jobname + ".pdf")
    if not os.path.isfile(log_path):
        # tectonic 写 build 目录，不落 output 目录；尝试在 jobdir 找
        alt = os.path.join(jobdir, jobname + ".log")
        if os.path.isfile(alt):
            log_path = alt
        else:
            log_path = None
    if not os.path.isfile(pdf_path):
        alt = os.path.join(jobdir, jobname + ".pdf")
        if os.path.isfile(alt):
            pdf_path = alt
        else:
            pdf_path = None

    errors = classify_log(log_path, main) if log_path else []
    ok = bool(pdf_path) and os.path.getsize(pdf_path or "") > 0
    report = {
        "ok": ok,
        "engine_missing": False,
        "engine": engine,
        "command": [engine] + ["-halt-on-error", "-interaction=nonstopmode",
                               "-file-line-error", "-output-directory=%s" % outdir, main],
        "passes": len(exit_codes),
        "exit_codes": [str(x) for x in exit_codes],
        "log_path": log_path,
        "pdf_path": pdf_path if ok else None,
        "errors": errors,
        "note": "local engine fallback; prefer platform latex-compiler tool",
    }
    return report


_ERROR_RE = re.compile(
    r"^(?P<loc>\.?[^\s:]+(?::\d+)?):?\s*(?P<msg>.*)$"
)
# TeX 行号消息形如 "./main.tex:12: Undefined control sequence." 或 "! ..." + "l.12"
LINE_HINT_RE = re.compile(r"(?:^|[\s(])l\.(\d+)(?:\s|$)")
FILE_LINE_RE = re.compile(r"^(.*?):(\d+):\s*(.*)$")

CATEGORY_RULES = [
    (re.compile(r"File .* not found|LaTeX Error: File .*not found", re.I), "missing_file"),
    (re.compile(r"Package .* not found|not installed|! LaTeX Error: File `", re.I), "missing_package"),
    (re.compile(r"Undefined control sequence", re.I), "undefined_control_sequence"),
    (re.compile(r"Missing [$\}\]].*inserted|Missing \$|Math \w+ error", re.I), "math_error"),
    (re.compile(r"Citation .* undefined|There were undefined references|Citation undefined", re.I), "undefined_citation"),
    (re.compile(r"Reference .* undefined|Reference .* on page", re.I), "undefined_reference"),
    (re.compile(r"Too many .*|Runaway argument|Extra \}|Missing \}|Paragraph ended", re.I), "structure_error"),
    (re.compile(r"Emergency stop", re.I), "fatal_stop"),
    (re.compile(r"Overfull \\hbox", re.I), "overfull_box"),
    (re.compile(r"Underfull \\hbox|Underfull \\vbox", re.I), "underfull_box"),
]


def _categorize(message):
    for pattern, cat in CATEGORY_RULES:
        if pattern.search(message or ""):
            return cat
    return "other"


def classify_log(log_path, main_path=None):
    """解析 .log，把每条报错定位到 文件:行号 并分类，给出根因提示。"""
    if not log_path or not os.path.isfile(log_path):
        return []
    text, err = read_text(log_path)
    if text is None:
        return [{"file": None, "line": None, "category": "unreadable_log",
                 "message": err}]
    lines = text.splitlines()
    errors = []
    i = 0
    n = len(lines)
    # 逐行扫描以 "!" 或 "LaTeX Warning: ... undefined" 开头的错误
    while i < n:
        line = lines[i].rstrip()
        if line.startswith("!"):
            message = line[1:].strip()
            # 紧随其后的 "l.123 ..." 或 file:line 提示
            line_no, file_hint = None, main_path
            for j in range(i + 1, min(i + 6, n)):
                m = FILE_LINE_RE.match(lines[j].strip())
                if m:
                    file_hint = m.group(1) or main_path
                    line_no = m.group(2)
                    break
                m = LINE_HINT_RE.search(lines[j])
                if m:
                    line_no = m.group(1)
                    break
            if "LaTeX Error" in message:
                # 常见形式: ! LaTeX Error: File `x.sty' not found.
                mm = re.search(r"File [`']([^`']+)'", message)
                if mm:
                    message += " (missing: %s)" % mm.group(1)
            errors.append({
                "file": file_hint,
                "line": int(line_no) if line_no and line_no.isdigit() else None,
                "category": _categorize(message),
                "message": message,
                "root_cause": _root_cause(_categorize(message), message),
            })
            i += 1
            continue
        if "undefined" in line.lower() and "LaTeX Warning" in line:
            m = re.search(r"Citation [`']([^`']+)' .* undefined", line)
            if m:
                errors.append({"file": None, "line": None,
                               "category": "undefined_citation",
                               "message": line.strip(),
                               "root_cause": "cite key not in .bib or .bib not run"})
            m = re.search(r"Reference [`']([^`']+)' .* undefined", line)
            if m:
                errors.append({"file": None, "line": None,
                               "category": "undefined_reference",
                               "message": line.strip(),
                               "root_cause": "label missing or needs one more pass"})
        i += 1
    # 去重（按 message 排序保序）
    seen, uniq = set(), []
    for e in errors:
        key = canonical_json(e)
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return uniq


def _root_cause(category, message):
    return {
        "missing_package": "macro package unavailable in this TeX distribution",
        "missing_file": "referenced file/class absent from input or template bundle",
        "undefined_control_sequence": "command not defined: typo, missing \\usepackage, or missing definition",
        "math_error": "math-mode delimiter/alignment broken",
        "undefined_citation": "cite key absent from .bib, or bibliography not compiled",
        "undefined_reference": "label absent, moved, or needs another compile pass",
        "structure_error": "brace/argument structure unbalanced",
        "fatal_stop": "hard error escalated by -halt-on-error",
        "overfull_box": "content wider than box (layout, not fatal)",
        "underfull_box": "box looser than ideal (layout warning)",
        "other": "unclassified; read surrounding lines before editing",
    }.get(category, "unclassified")


def refcheck(dir_, bib=None):
    """从 .log/.aux 检查未解析的 \\ref 与 \\cite。返回结构化清单。"""
    logs = []
    for root, _, names in os.walk(dir_):
        for name in sorted(names):
            if name.endswith(".log") or name.endswith(".aux"):
                logs.append(os.path.join(root, name))
    undefined_refs, undefined_cites = [], []
    for lg in logs:
        text, _ = read_text(lg)
        if text is None:
            continue
        for m in re.finditer(r"Reference [`']([^`']+)' .*? undefined", text):
            undefined_refs.append(m.group(1))
        for m in re.finditer(r"Citation [`']([^`']+)' .*? undefined", text):
            undefined_cites.append(m.group(1))
    if bib and os.path.isfile(bib):
        keys_in_bib = set()
        for m in re.finditer(r"@\w+\s*\{\s*([^,]+),", read_text(bib)[0] or ""):
            keys_in_bib.add(m.group(1).strip())
        missing_in_bib = sorted(set(undefined_cites) - keys_in_bib)
    else:
        missing_in_bib = sorted(set(undefined_cites))
    return {
        "undefined_refs": sorted(set(undefined_refs)),
        "undefined_cites": sorted(set(undefined_cites)),
        "cites_missing_from_bib": missing_in_bib,
    }


# --------------------------------------------------------------------------- #
# 模板必填项与源码结构检查（映射「结果完整性/改得对」维度）
# --------------------------------------------------------------------------- #

def _environments_balance(text):
    """检查 \\begin{...}/\\end{...} 是否配对。返回 (ok, detail)。"""
    begins = re.findall(r"\\begin\{([^}]+)\}", text)
    ends = re.findall(r"\\end\{([^}]+)\}", text)
    from collections import Counter
    b, e = Counter(begins), Counter(ends)
    only_begin = sorted((b - e).keys())
    only_end = sorted((e - b).keys())
    ok = not only_begin and not only_end
    detail = "unclosed: %s; stray end: %s" % (
        ",".join(only_begin) or "-", ",".join(only_end) or "-")
    return ok, detail


def _math_dollars_ok(text):
    """粗查 $ 配对（不处理 \\$ 转义，只作提示）。"""
    n = text.count("$")
    return n % 2 == 0, "dollar_count=%d" % n


def _cite_keys_used(text):
    keys = set()
    for m in re.finditer(r"\\cite(?:\[[^\]]*\])?\{([^}]*)\}", text):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                keys.add(k)
    return keys


def _bib_keys(bib_text):
    return set(re.findall(r"@\w+\s*\{\s*([^,\s}]+)", bib_text))


def check_tex_requirements(tex_path, bib_path=None):
    """对 .tex 源码做模板必填项与结构完整性检查。返回结构化报告，
    用于 P2/P3 阶段确认标题/作者/摘要/关键词/引用样式/文件结构逐项覆盖。"""
    text, err = read_text(tex_path)
    if text is None:
        return {"tex": tex_path, "error": err, "checks": []}

    checks = []
    def add(cid, ok, label):
        checks.append({"id": cid, "ok": bool(ok),
                       "detail": ("present: " + label) if ok else ("missing: " + label)})

    add("documentclass", "\\documentclass" in text, "\\documentclass 文档类声明")
    add("begin_document", "\\begin{document}" in text, "\\begin{document}")
    add("end_document", "\\end{document}" in text, "\\end{document}")
    add("title", bool(re.search(r"\\title\s*\{", text)), "\\title{} 标题")
    add("author", bool(re.search(r"\\author\s*\{", text)), "\\author{} 作者")
    add("abstract", bool(re.search(r"\\begin\{abstract\}", text)), "摘要环境")
    add("keywords", bool(re.search(r"keywords|\{keywords\}", text, re.I)), "关键词块")
    add("bibliography", bool(re.search(
        r"\\bibliography\s*\{|\\printbibliography|\\addbibresource", text)),
        "参考文献入口")

    opens, closes = text.count("{"), text.count("}")
    add("brace_balanced", opens == closes,
        "花括号配对 open=%d close=%d" % (opens, closes))

    env_ok, env_detail = _environments_balance(text)
    add("environments_balanced", env_ok, "环境配对 (%s)" % env_detail)

    math_ok, math_detail = _math_dollars_ok(text)
    add("math_dollars_even", math_ok, "$ 配对 (%s)" % math_detail)

    used = _cite_keys_used(text)
    missing_in_bib = []
    bib_keys = set()
    if bib_path and os.path.isfile(bib_path):
        btext, berr = read_text(bib_path)
        if btext is not None:
            bib_keys = _bib_keys(btext)
    if used:
        missing_in_bib = sorted(used - bib_keys)
    add("cite_keys_resolvable", not missing_in_bib,
        "正文 cite 键可解析 (missing: %s)" % (", ".join(missing_in_bib) or "无"))

    hard_ok = all(c["ok"] for c in checks)
    return {"tex": tex_path, "hard_ok": hard_ok, "checks": checks,
            "cite_keys_used": sorted(used), "cite_keys_missing_from_bib": missing_in_bib}


# --------------------------------------------------------------------------- #
# 结果规范化与自检
# --------------------------------------------------------------------------- #
REQUIRED_META = ("case_id", "status", "sources", "uncertainty", "pending_human",
                 "change_log", "errors", "degradations", "limitations", "runtime")
LIST_FIELDS = ("sources", "uncertainty", "pending_human", "errors",
               "degradations", "limitations")


def _sort_key(item):
    return canonical_json(item)


def normalize_result(result, keys):
    """把 result.json 规范化为确定性形态：
    - 补齐 output_contract 全部键（缺失=null）；
    - 补齐必需元数据字段；
    - 列表排序、runtime.run_id 回填、状态合法化。"""
    out = dict(result) if isinstance(result, dict) else {}
    out["case_id"] = str(out.get("case_id") or "unknown")
    for key in keys:
        out.setdefault(key, None)
    for field in REQUIRED_META:
        out.setdefault(field, [] if field in LIST_FIELDS else ({} if field == "runtime" else None))
    if not isinstance(out.get("runtime"), dict):
        out["runtime"] = {}
    out["runtime"].setdefault("run_id", "")
    out["runtime"]["deterministic"] = True
    out["runtime"]["producer"] = PRODUCER
    out["runtime"]["schema_version"] = SCHEMA_VERSION
    if out.get("status") not in ALLOWED_STATUS:
        out["status"] = "partial"
    for field in LIST_FIELDS:
        if not isinstance(out.get(field), list):
            out[field] = []
        else:
            out[field] = sorted(out[field], key=_sort_key)
    if not isinstance(out.get("sources"), list):
        out["sources"] = []
    if not isinstance(out.get("change_log"), list):
        out["change_log"] = []
    # change_log 保持 agent 给定的顺序（顺序由 agent 确定性控制），只回填 at
    for entry in out["change_log"]:
        if isinstance(entry, dict) and not entry.get("at"):
            entry["at"] = out["runtime"].get("run_id", "")
    if not isinstance(out.get("artifacts"), dict):
        out["artifacts"] = {}
    return out


def selfcheck(result, keys, output_dir=None):
    """返回 (checklist, hard_ok)。checklist 逐条对应 references/checklist.md。"""
    checks = []
    hard_ok = True

    def add(cid, ok, detail):
        nonlocal hard_ok
        if not ok:
            hard_ok = False
        checks.append({"id": cid, "ok": bool(ok), "detail": detail})

    add("json_parses", isinstance(result, dict), "result.json 可被解析为对象")
    add("case_id_present", bool(result.get("case_id")), "case_id 非空")
    add("status_valid", result.get("status") in ALLOWED_STATUS, "status 取值合法")
    missing_keys = [k for k in keys if k not in result]
    add("contract_keys_complete", not missing_keys,
        "contract 键缺失: %s" % (", ".join(missing_keys) or "无"))
    for m in ("sources", "uncertainty", "pending_human", "change_log"):
        add("meta_%s" % m, m in result, "元数据字段 %s 存在" % m)
    src_ok = all(isinstance(s, dict) and s.get("locator") and s.get("quote")
                 for s in result.get("sources", []))
    add("sources_have_locator_quote", src_ok,
        "每条 source 均含 locator + 逐字 quote")
    det = (result.get("runtime") or {}).get("deterministic")
    add("determinism_marked", det is True, "runtime.deterministic 标记为 true")
    add("run_id_present", bool((result.get("runtime") or {}).get("run_id")),
        "runtime.run_id 存在（确定性 token）")
    if output_dir:
        art = result.get("artifacts") or {}
        main = art.get("main_tex")
        main_ok = bool(main) and os.path.isfile(
            os.path.join(output_dir, os.path.basename(str(main))))
        add("main_tex_exists", main_ok, "可编辑主产物 main.tex 存在")
    return checks, hard_ok


def render_checklist(checks):
    lines = []
    for c in checks:
        lines.append("- [%s] %s — %s" % ("x" if c["ok"] else " ", c["id"], c["detail"]))
    return "\n".join(lines)


def build_manifest(output_dir, input_dir, run_id=None):
    """为 /app/output 下所有产物生成确定性清单（relpath 排序 + sha256），
    供第三方独立复核“同输入是否得到同一份产物”。跳过 manifest 自身。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(output_dir):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, output_dir).replace("\\", "/")
            if rel == "build_manifest.json":
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            files.append({"rel": rel, "size": size, "sha256": sha256_file(full)})
    return {
        "run_id": run_id or run_id_for(input_dir),
        "producer": PRODUCER,
        "schema_version": SCHEMA_VERSION,
        "deterministic": True,
        "artifacts": files,
    }
