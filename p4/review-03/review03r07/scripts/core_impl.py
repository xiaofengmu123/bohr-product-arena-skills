#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 研究方法与统计审查的稳健、可复核、确定性管线。

纯 Python 标准库实现。设计目标（本变体的重点为鲁棒性）：

1. 任何输入都不崩溃：空输入目录、坏 JSON、坏编码、缺参数、超大文件、
   缺 pdftotext、扫描版 PDF、写盘失败，均优雅降级并尽量落盘一份合法、
   诚实的 result.json。
2. 确定性：同一输入重复运行得到字节级一致的结果（排序键、稳定排序、
   无随机数、不写墙钟时间）。
3. 证据可溯源：每条 finding 携带 locator + 逐字 quote；数值一致性检查
   只输出"候选"，须由 Agent 回读原文确证后才能提升为 finding。

本文件不包含任何"预制答案"或"预判结论"；所有实质审查判断由调用方
（Agent）依据真实材料在 /app/work/findings/ 中写入，本管线只做发现、
骨架、合并、自检与渲染。
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(HERE)
VERSION = "1.0.0"

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}

# ---- 资源与大小上限（超限跳过并记录，不崩溃） ----
MAX_TEXT_BYTES = 8 * 1024 * 1024      # 单文本文件 8MB 上限
MAX_PDF_BYTES = 80 * 1024 * 1024      # 单 PDF 80MB 上限
MAX_JSON_BYTES = 4 * 1024 * 1024      # 单 JSON 4MB 上限
MAX_MATERIALS = 200                   # 最多处理文件数
MAX_SEG_CHARS = 4000                  # segments 中每段保留字符数
MAX_QUOTE_CHARS = 1200                # 单条 quote 上限（超过截断并记录）

FACETS = [
    "research_design",
    "variable_definition",
    "statistical_methods",
    "sample_size_power",
    "results_reporting",
]
CHECK_CLASSES = [
    "fact_check",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argumentation_chain",
    "academic_norms",
]
FINDING_TYPES = [
    "factual_error",
    "insufficient_evidence",
    "normative_issue",
    "advisory",
    "pending_review",
]
SEVERITIES = ["critical", "major", "minor", "advisory", "pending"]
REVIEW_STATUSES = ["confirmed", "needs_review", "not_verified"]
CONFIDENCES = ["high", "medium", "low"]
STATUSES = ["ok", "partial", "insufficient_evidence", "input_error"]
VERDICTS = ["pass", "problems_found", "needs_review", "unchecked",
            "insufficient_evidence", "input_error"]

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".tex", ".bib", ".csv", ".tsv",
    ".json", ".jsonl", ".html", ".htm", ".xml", ".yaml", ".yml", ".log",
}
OFFICE_TEXT_EXTS = {".docx"}
OFFICE_OTHER_EXTS = {".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods"}
PDF_EXT = ".pdf"
CONTROL_NAMES = {"case.json", "task.md"}

# 统计与方法关键词（用于 terms.json 命中定位，帮助 Agent 快速找到章节）
STAT_TERMS = [
    "p value", "p-value", "p =", "p <", "p >", "confidence interval",
    "odds ratio", "hazard ratio", "risk ratio", "relative risk",
    "t test", "t-test", "student's t", "welch", "anova", "chi-square",
    "chi2", "fisher", "wilcoxon", "mann-whitney", "kruskal", "log-rank",
    "kaplan-meier", "cox", "regression", "logistic", "linear mixed",
    "mixed-effects", "mixed model", "gee", "bonferroni", "holm", "fdr",
    "multiple comparison", "multiple testing", "sample size", "power",
    "effect size", "alpha", "significance", "adjust", "adjusted",
    "missing data", "imputation", "sensitivity analysis", "per-protocol",
    "intention-to-treat", "itt", "dropout", "lost to follow-up",
    "randomized", "randomised", "blind", "blinding", "placebo",
    "control group", "intervention", "baseline", "endpoint", "outcome",
    "covariate", "confounder", "standard deviation", "interquartile",
    "median", "mean", "correlation", "spearman", "pearson",
]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _deg(code, message, **extra):
    d = {"code": code, "message": message}
    for k, v in extra.items():
        if v is not None:
            d[k] = v
    return d


def ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def atomic_write_text(text, path):
    """原子写文本：临时文件 + os.replace，失败返回错误列表。"""
    errors = []
    d = os.path.dirname(path) or "."
    if not ensure_dir(d):
        return [_deg("WRITE_FAIL", f"cannot create directory {d}")]
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".txt", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        tmp = None
    except OSError as e:
        errors.append(_deg("WRITE_FAIL", f"cannot write {path}: {e}"))
    finally:
        if tmp is not None and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return errors


def atomic_write_json(obj, path):
    """原子写 JSON，sort_keys 保证确定性。"""
    errors = []
    d = os.path.dirname(path) or "."
    if not ensure_dir(d):
        return [_deg("WRITE_FAIL", f"cannot create directory {d}")]
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
        tmp = None
    except OSError as e:
        errors.append(_deg("WRITE_FAIL", f"cannot write {path}: {e}"))
    finally:
        if tmp is not None and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return errors


def read_text(path, max_bytes=None):
    """按大小上限 + 编码回退读取文本。返回 (text, errors, degradations)。"""
    if max_bytes is None:
        max_bytes = MAX_TEXT_BYTES
    errors, degradations = [], []
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, [_deg("IO_STAT", f"cannot stat {path}: {e}")], []
    if size > max_bytes:
        return None, [], [_deg(
            "SIZE_SKIP", f"{path} ({size} bytes) exceeds {max_bytes} bytes; skipped",
            path=path, size=size)]
    if size == 0:
        return "", [], []
    data = None
    last_err = None
    used_enc = None
    for enc in ENCODINGS:
        try:
            with open(path, "rb") as f:
                raw = f.read()
            data = raw.decode(enc)
            used_enc = enc
            break
        except (UnicodeDecodeError, OSError) as e:
            last_err = e
            continue
    if data is None:
        return None, [_deg("IO_DECODE", f"cannot decode {path}: {last_err}")], degradations
    if used_enc not in ("utf-8-sig", "utf-8"):
        degradations.append(_deg(
            "ENC_FALLBACK", f"{path} decoded as {used_enc} (not UTF-8)", path=path))
    return data, [], degradations


def read_json_lenient(path):
    """容错读 JSON：BOM、首尾修剪、坏 JSON 记录而非崩溃。"""
    errors, degradations = [], []
    text, e2, d2 = read_text(path, MAX_JSON_BYTES)
    errors += e2
    degradations += d2
    if text is None:
        return None, errors, degradations
    if not text.strip():
        return None, errors + [_deg("JSON_EMPTY", f"{path} is empty")], degradations
    text = text.lstrip("\ufeff")
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return None, errors + [_deg("JSON_NOT_OBJECT", f"{path} top-level is not an object")], degradations
        return obj, errors, degradations
    except (json.JSONDecodeError, ValueError) as e:
        # 尝试剪取第一个平衡对象再解析
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(text[start:end + 1])
                if isinstance(obj, dict):
                    degradations.append(_deg(
                        "JSON_SALVAGED",
                        f"{path} trimmed to balanced object (chars {start}-{end})",
                        path=path))
                    return obj, errors, degradations
            except (json.JSONDecodeError, ValueError):
                pass
        lineno = getattr(e, "lineno", None)
        colno = getattr(e, "colno", None)
        return None, errors + [_deg(
            "JSON_BAD", f"{path} invalid JSON: {e}", lineno=lineno, colno=colno)], degradations


# ---------------------------------------------------------------------------
# 输入发现与材料抽取
# ---------------------------------------------------------------------------

def _classify(path, rel):
    ext = os.path.splitext(path)[1].lower()
    if ext == PDF_EXT:
        kind = "pdf"
    elif ext in OFFICE_TEXT_EXTS:
        kind = "docx"
    elif ext in OFFICE_OTHER_EXTS:
        kind = "office_other"
    elif ext in TEXT_EXTS:
        kind = "text"
    else:
        kind = "other"
    return {"rel": rel, "abs": path, "kind": kind}


def scan_materials(input_dir):
    """扫描输入目录（含一层子目录如 materials/），返回 (materials, errors, degradations)。"""
    errors, degradations = [], []
    materials = []
    if not os.path.isdir(input_dir):
        return materials, [_deg("INPUT_MISSING", f"input dir not found: {input_dir}")], degradations
    try:
        entries = sorted(os.listdir(input_dir))
    except OSError as e:
        return materials, [_deg("IO_LIST", f"cannot list {input_dir}: {e}")], degradations
    for name in entries:
        if name in CONTROL_NAMES:
            continue
        p = os.path.join(input_dir, name)
        if os.path.isdir(p):
            try:
                sub = sorted(os.listdir(p))
            except OSError:
                sub = []
            for sn in sub:
                sp = os.path.join(p, sn)
                if os.path.isfile(sp):
                    materials.append(_classify(sp, os.path.join(name, sn)))
            continue
        if os.path.isfile(p):
            materials.append(_classify(p, name))
    if len(materials) > MAX_MATERIALS:
        degradations.append(_deg(
            "TOO_MANY_FILES",
            f"{len(materials)} material files found; keeping first {MAX_MATERIALS}"))
        materials = materials[:MAX_MATERIALS]
    return materials, errors, degradations


def _split_paragraphs(text):
    """按空行切段；超长段再按句界分块。返回 [(text, chunk_no)]。"""
    blocks = re.split(r"\n\s*\n", text.strip("\n"))
    out = []
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        if len(b) <= MAX_SEG_CHARS:
            out.append(b)
            continue
        # 超长段按句界切成 MAX_SEG_CHARS 内的块
        pieces = re.split(r"(?<=[。.!?;；])\s+", b)
        cur = ""
        for pc in pieces:
            if not pc.strip():
                continue
            if len(cur) + len(pc) + 1 > MAX_SEG_CHARS and cur:
                out.append(cur)
                cur = pc
            else:
                cur = (cur + " " + pc) if cur else pc
        if cur:
            out.append(cur)
    return out


def extract_pdf(path, rel, errors, degradations):
    """用 pdftotext 抽取 PDF 页级文本。返回 segments 列表。"""
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        degradations.append(_deg(
            "PDF_NO_TOOL", "pdftotext not found; PDF marked needs_ocr", path=rel))
        return []
    try:
        size = os.path.getsize(path)
    except OSError as e:
        errors.append(_deg("IO_STAT", f"cannot stat {path}: {e}", path=rel))
        return []
    if size > MAX_PDF_BYTES:
        degradations.append(_deg(
            "SIZE_SKIP", f"{rel} ({size} bytes) exceeds {MAX_PDF_BYTES} bytes; skipped",
            path=rel, size=size))
        return []
    try:
        proc = subprocess.run(
            [pdftotext, "-layout", path, "-"],
            capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(_deg("PDF_FAIL", f"pdftotext failed on {rel}: {e}", path=rel))
        return []
    if proc.returncode != 0:
        errors.append(_deg(
            "PDF_FAIL", f"pdftotext exit {proc.returncode} on {rel}",
            path=rel, stderr=proc.stderr.decode("utf-8", "replace")[:300]))
        return []
    text = proc.stdout.decode("utf-8", "replace")
    if not text.strip():
        degradations.append(_deg(
            "PDF_EMPTY", f"{rel}: extracted empty text (likely scanned); needs_ocr", path=rel))
        return []
    pages = text.split("\f")
    segs = []
    for pi, pg in enumerate(pages, 1):
        if not pg.strip():
            continue
        for chunk in _split_paragraphs(pg):
            segs.append({"file": rel, "page": pi, "para": None, "text": chunk})
    return segs


def extract_docx(path, rel, errors, degradations):
    """尽力抽取 docx 文本（zipfile + XML 段落切分），失败仅降级。"""
    segs = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "word/document.xml" not in names:
                degradations.append(_deg("DOCX_STRUCT", f"{rel}: no word/document.xml", path=rel))
                return segs
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError) as e:
        degradations.append(_deg("DOCX_FAIL", f"{rel}: cannot read docx: {e}", path=rel))
        return segs
    # 段落切分：以 </w:p> 为界，去除所有 XML 标签
    paras = re.split(r"</w:p>", xml)
    for raw in paras:
        txt = re.sub(r"<[^>]+>", "", raw)
        # 解常见 XML 实体
        txt = (txt.replace("&lt;", "<").replace("&gt;", ">")
                   .replace("&amp;", "&").replace("&quot;", '"')
                   .replace("&apos;", "'"))
        txt = re.sub(r"[ \t]+", " ", txt).strip()
        if txt:
            segs.append({"file": rel, "page": None, "para": None, "text": txt[:MAX_SEG_CHARS]})
    return segs


def extract_material(mat, idx, work_dir, errors, degradations):
    """抽取单个材料为 segments，并把全文写入 texts/。返回 segments。"""
    texts_dir = os.path.join(work_dir, "texts")
    ensure_dir(texts_dir)
    segs = []
    if mat["kind"] == "pdf":
        segs = extract_pdf(mat["abs"], mat["rel"], errors, degradations)
    elif mat["kind"] == "docx":
        segs = extract_docx(mat["abs"], mat["rel"], errors, degradations)
    elif mat["kind"] == "text":
        text, e2, d2 = read_text(mat["abs"])
        errors += e2
        degradations += d2
        if text is not None:
            for para in _split_paragraphs(text):
                segs.append({"file": mat["rel"], "page": None, "para": None, "text": para})
    else:
        degradations.append(_deg(
            "UNSUPPORTED_KIND",
            f"{mat['rel']}: kind={mat['kind']} not parsed", path=mat["rel"]))
    # 写全文文本（供 Agent 逐字摘录与回查）
    try:
        full = ""
        if mat["kind"] == "text":
            full = read_text(mat["abs"])[0] or ""
        elif mat["kind"] == "docx":
            full = "\n\n".join(s["text"] for s in segs)
        elif mat["kind"] == "pdf":
            full = "\n\n".join(s["text"] for s in segs)
        if full:
            atomic_write_text(full, os.path.join(texts_dir, f"{idx:03d}.txt"))
    except Exception as e:  # noqa: BLE001 - 兜底，绝不让抽取崩溃
        errors.append(_deg("TEXT_WRITE", f"cannot write extracted text for {mat['rel']}: {e}"))
    return segs


def build_terms(segments):
    """按关键词命中生成 terms.json 内容。"""
    terms = {}
    for seg in segments:
        text = seg.get("text", "")
        low = text.lower()
        for term in STAT_TERMS:
            cnt = low.count(term)
            if cnt:
                terms.setdefault(term, []).append({
                    "file": seg.get("file"),
                    "page": seg.get("page"),
                    "count": cnt,
                })
    # 限制每词命中条目，避免超大 terms
    for term in list(terms):
        if len(terms[term]) > 50:
            terms[term] = terms[term][:50]
    return terms


def discover(ctx):
    """命令 discover：扫描输入、抽取文本、建索引、写 manifest/segments/terms。"""
    errors, degradations = [], []
    work = ctx["work"]
    ensure_dir(work)
    materials, e1, d1 = scan_materials(ctx["input"])
    errors += e1
    degradations += d1

    all_segments = []
    manifest_materials = []
    for idx, mat in enumerate(materials, 1):
        mat_segs = extract_material(mat, idx, work, errors, degradations)
        status = "extracted" if mat_segs else "empty_or_unparsed"
        entry = {
            "index": idx,
            "rel": mat["rel"],
            "kind": mat["kind"],
            "segments": len(mat_segs),
            "status": status,
        }
        try:
            entry["size"] = os.path.getsize(mat["abs"])
        except OSError:
            entry["size"] = None
        manifest_materials.append(entry)
        for s in mat_segs:
            s["id"] = f"seg-{idx:03d}-{len(all_segments) + 1:04d}"
            all_segments.append(s)

    manifest = {
        "input": ctx["input"],
        "materials": manifest_materials,
        "material_count": len(manifest_materials),
        "segment_count": len(all_segments),
        "errors": errors,
        "degradations": degradations,
    }
    segments = {"segments": all_segments, "count": len(all_segments)}
    terms_data = build_terms(all_segments)
    terms = {"terms": terms_data, "count": len(terms_data)}

    atomic_write_json(manifest, os.path.join(work, "manifest.json"))
    atomic_write_json(segments, os.path.join(work, "segments.json"))
    atomic_write_json(terms, os.path.join(work, "terms.json"))

    print(json.dumps({
        "command": "discover",
        "material_count": len(manifest_materials),
        "segment_count": len(all_segments),
        "errors": len(errors),
        "degradations": len(degradations),
        "work": work,
    }, ensure_ascii=False, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# 骨架（scaffold）
# ---------------------------------------------------------------------------

def load_case(input_dir):
    """读 case.json / task.md，返回 (case_id, case_obj, errors, degradations)。"""
    errors, degradations = [], []
    case_id = "unknown"
    case_obj = {}
    cj = read_json_lenient(os.path.join(input_dir, "case.json"))
    case_obj = cj[0] or {}
    errors += cj[1]
    degradations += cj[2]
    if isinstance(case_obj, dict):
        for k in ("case_id", "id", "caseId"):
            if case_obj.get(k) not in (None, ""):
                case_id = str(case_obj[k])
                break
    # task.md 非强制；读不到仅记录，不判 input_error
    t = read_text(os.path.join(input_dir, "task.md"))
    if t[0] is None:
        degradations.append(_deg("TASK_MISSING", "task.md not found or unreadable"))
    return case_id, case_obj, errors, degradations


def extract_contract(case_obj):
    """从 case.json 提取 output_contract（原样保留全部键值）。"""
    if not isinstance(case_obj, dict):
        return {}
    for key in ("output_contract", "outputContract", "contract", "result_contract"):
        v = case_obj.get(key)
        if isinstance(v, dict):
            return v
    return {}


def build_runtime():
    """确定性 runtime 对象（记录预算与配置，不写墙钟时间以保可重复）。"""
    return {
        "engine": "stdlib-only",
        "python": sys.version.split()[0],
        "version": VERSION,
        "budget": {"seconds": 900, "model_requests": 64,
                   "tokens_per_response": 16384},
    }


def build_skeleton(case_id, contract, errors, degradations):
    result = {
        "case_id": case_id,
        "status": "partial",
        "summary": {
            "verdict": "unchecked",
            "counts": {
                "total": 0, "critical": 0, "major": 0, "minor": 0,
                "advisory": 0, "pending": 0,
            },
            "by_facet": {f: 0 for f in FACETS},
            "by_finding_type": {t: 0 for t in FINDING_TYPES},
            "by_check_class": {c: 0 for c in CHECK_CLASSES},
        },
        "review_report": {"title": "研究方法与统计审查单", "findings": []},
        "result_status": "未检查",
        "evidence": {"evidence_map": {}},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": ["scaffold 阶段产物：尚未开始实质审查，仅完成输入发现与骨架落盘。"],
        "errors": list(errors),
        "degradations": list(degradations),
        "numeric_checks": [],
        "change_log": [{
            "step": "scaffold",
            "action": "落盘 partial 结果骨架",
            "note": "前 3 分钟内先写合法骨架，之后只做增量改进",
        }],
        "runtime": build_runtime(),
    }
    # 原样回填 output_contract 全部字段：规范字段填真实/空数据，未知键保留
    return apply_contract(result, contract)


def scaffold(ctx):
    """命令 scaffold：立即落盘合法 result.json 骨架与 state/contract/meta。"""
    errors, degradations = [], []
    work = ctx["work"]
    ensure_dir(work)
    case_id, case_obj, e1, d1 = load_case(ctx["input"])
    errors += e1
    degradations += d1
    contract = extract_contract(case_obj)

    # 判定初始状态
    materials, e2, d2 = scan_materials(ctx["input"])
    errors += e2
    degradations += d2
    has_case = bool(case_obj)
    has_materials = bool(materials)
    if not has_case and not has_materials:
        status = "input_error"
    elif not has_materials:
        status = "insufficient_evidence"
    else:
        status = "partial"

    result = build_skeleton(case_id, contract, errors, degradations)
    result["status"] = status
    if status == "input_error":
        result["result_status"] = "未检查"
        result["summary"]["verdict"] = "input_error"
        result["limitations"].append("输入目录为空且缺 case.json，无法进行审查。")
    elif status == "insufficient_evidence":
        result["summary"]["verdict"] = "insufficient_evidence"
        result["limitations"].append("case.json 存在但无科研材料，证据不足以给出审查结论。")

    errs = atomic_write_json(result, os.path.join(ctx["output"], "result.json"))
    errors += errs
    # 快照 contract，供 finalize 合并保留未知键
    atomic_write_json({"case_id": case_id, "output_contract": contract},
                      os.path.join(work, "contract.json"))
    # state：进度记录
    atomic_write_json({
        "phase": "scaffolded",
        "findings_written": 0,
        "checked_facets": {f: "unchecked" for f in FACETS},
        "checked_classes": {c: "unchecked" for c in CHECK_CLASSES},
    }, os.path.join(work, "state.json"))
    # meta：供 Agent 后续填写的审查元信息（含显式未检查标记）
    atomic_write_json({
        "status": status,
        "overall_verdict": "unchecked",
        "overall_assessment": "",
        "checked_facets": {f: False for f in FACETS},
        "checked_classes": {c: False for c in CHECK_CLASSES},
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
    }, os.path.join(work, "meta.json"))

    print(json.dumps({
        "command": "scaffold",
        "case_id": case_id,
        "status": status,
        "material_count": len(materials),
        "output": os.path.join(ctx["output"], "result.json"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# 合并与最终化（finalize）
# ---------------------------------------------------------------------------

def load_findings(findings_dir):
    """读取 findings/ 下每个 JSON（对象或列表），确定性排序。返回 (list, errors, degradations)。"""
    errors, degradations = [], []
    findings = []
    if not os.path.isdir(findings_dir):
        return findings, [], []
    names = sorted(n for n in os.listdir(findings_dir) if n.endswith(".json"))
    for n in names:
        obj, e2, d2 = read_json_lenient(os.path.join(findings_dir, n))
        errors += e2
        degradations += d2
        if isinstance(obj, dict):
            findings.append(obj)
        elif isinstance(obj, list):
            findings.extend(x for x in obj if isinstance(x, dict))
        else:
            errors.append(_deg("FINDING_INVALID", f"findings/{n} is not an object or list"))
    return findings, errors, degradations


def _finding_key(f):
    loc = str(f.get("locator", ""))
    quote = str(f.get("quote", ""))
    fid = str(f.get("id", ""))
    m = re.match(r"^F(\d+)$", fid, re.I)
    if m:
        return (0, int(m.group(1)), loc, quote)
    return (1, 0, loc, quote)


def validate_finding(f, errors, degradations):
    """补齐/规范化一条 finding；严重违规则返回 None。"""
    if not isinstance(f, dict):
        return None
    required = ["id", "facet", "locator", "quote", "severity", "suggestion"]
    missing = [k for k in required if not str(f.get(k, "")).strip()]
    if missing:
        errors.append(_deg(
            "FINDING_MISSING_KEYS",
            f"finding dropped: missing {missing}", id=f.get("id")))
        return None
    if f["facet"] not in FACETS:
        errors.append(_deg(
            "FINDING_BAD_FACET",
            f"finding {f.get('id')}: facet={f['facet']!r} invalid; dropped"))
        return None
    if f["severity"] not in SEVERITIES:
        errors.append(_deg(
            "FINDING_BAD_SEVERITY",
            f"finding {f.get('id')}: severity={f['severity']!r} invalid; dropped"))
        return None
    out = dict(f)
    out["finding_type"] = f.get("finding_type") if f.get("finding_type") in FINDING_TYPES else "pending_review"
    out["check_class"] = f.get("check_class") if f.get("check_class") in CHECK_CLASSES else "method_design"
    out["review_status"] = f.get("review_status") if f.get("review_status") in REVIEW_STATUSES else "needs_review"
    out["confidence"] = f.get("confidence") if f.get("confidence") in CONFIDENCES else "low"
    out["basis"] = f.get("basis", "")
    # quote 超长截断并记录
    q = out["quote"]
    if len(q) > MAX_QUOTE_CHARS:
        out["quote"] = q[:MAX_QUOTE_CHARS] + "…"
        degradations.append(_deg(
            "QUOTE_TRUNCATED", f"finding {out.get('id')}: quote truncated to {MAX_QUOTE_CHARS} chars"))
    return out


def tally(findings):
    counts = {"total": len(findings), "critical": 0, "major": 0, "minor": 0,
              "advisory": 0, "pending": 0}
    by_facet = {f: 0 for f in FACETS}
    by_type = {t: 0 for t in FINDING_TYPES}
    by_class = {c: 0 for c in CHECK_CLASSES}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        by_facet[f["facet"]] = by_facet.get(f["facet"], 0) + 1
        by_type[f["finding_type"]] = by_type.get(f["finding_type"], 0) + 1
        by_class[f["check_class"]] = by_class.get(f["check_class"], 0) + 1
    return counts, by_facet, by_type, by_class


# ---------------------------------------------------------------------------
# 数值一致性候选检查（仅候选，须 Agent 确证）
# ---------------------------------------------------------------------------

P_RE = re.compile(r"\bp\s*(=|<=|<|≤|>=|>|≥)\s*(0?\.\d+)", re.I)
SIG_NEG_RE = re.compile(
    r"\b(?:n\.?s\.?|non[- ]?significant|not significant|no significant"
    r"|no difference|no statistical difference|did not differ)\b", re.I)
SIG_POS_RE = re.compile(r"\bsignificant(?:ly)?\b", re.I)
N_RE = re.compile(r"\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)")
CI_RE = re.compile(r"(\d+\.?\d*)\s*(?:–|-|,| to |–)\s*(\d+\.?\d*)")


def _has_ci_around(text, pos):
    window = text[max(0, pos - 120):pos + 120]
    if not re.search(r"(?:\bCI\b|confidence interval)", window, re.I):
        return None
    nums = CI_RE.findall(window)
    if not nums:
        return None
    try:
        lo, hi = float(nums[0][0]), float(nums[0][1])
    except ValueError:
        return None
    return (min(lo, hi), max(lo, hi))


def compute_numeric_checks(segments):
    """扫描 segments 生成数值一致性候选。返回 candidates 列表。"""
    candidates = []
    n_values = {}
    for seg in segments:
        text = seg.get("text", "")
        loc = f"{seg.get('file','')} p.{seg.get('page') or '?'}"
        ps = [(op, float(v)) for op, v in P_RE.findall(text)]
        sig_neg = bool(SIG_NEG_RE.search(text))
        sig_pos = bool(SIG_POS_RE.search(text)) and not sig_neg
        has_ci = re.search(r"(?:\bCI\b|confidence interval)", text, re.I)
        # 1) p 与显著性措辞相反
        for op, v in ps:
            if v < 0.05 and sig_neg:
                candidates.append({
                    "id": f"nc-pw-{len(candidates)+1:03d}",
                    "kind": "p_vs_wording",
                    "locator": loc,
                    "text": f"p {op} {v} 但同段出现'不显著/无差异'措辞",
                    "note": "候选：需确认 p 值与措辞指向同一结局后提升为 finding",
                })
                break
            if v >= 0.05 and sig_pos:
                candidates.append({
                    "id": f"nc-pw-{len(candidates)+1:03d}",
                    "kind": "p_vs_wording",
                    "locator": loc,
                    "text": f"p {op} {v} 但同段出现'显著'措辞",
                    "note": "候选：需确认 p 值与措辞指向同一结局后提升为 finding",
                })
                break
        # 2) p 与 CI 结论相反（比值指标 null=1，差值指标 null=0）
        if ps and has_ci:
            m = CI_RE.search(text)
            if m:
                try:
                    lo, hi = float(m.group(1)), float(m.group(2))
                    null = 1.0 if re.search(
                        r"(?:odds ratio|\bOR\b|\bRR\b|\bHR\b|risk ratio|hazard ratio)",
                        text, re.I) else 0.0
                    pmin = min(v for _, v in ps)
                    if pmin < 0.05 and min(lo, hi) <= null <= max(lo, hi):
                        candidates.append({
                            "id": f"nc-ci-{len(candidates)+1:03d}",
                            "kind": "p_vs_ci",
                            "locator": loc,
                            "text": f"p<0.05 但 CI [{min(lo,hi)}, {max(lo,hi)}] 含 null={null}",
                            "note": "候选：需确认比值/差值指标与 null 后提升为 finding",
                        })
                    if pmin >= 0.05 and not (min(lo, hi) <= null <= max(lo, hi)):
                        candidates.append({
                            "id": f"nc-ci-{len(candidates)+1:03d}",
                            "kind": "p_vs_ci",
                            "locator": loc,
                            "text": f"p>=0.05 但 CI [{min(lo,hi)}, {max(lo,hi)}] 不含 null={null}",
                            "note": "候选：需确认比值/差值指标与 null 后提升为 finding",
                        })
                except ValueError:
                    pass
        # 3) 收集 N/n 数值
        for raw in N_RE.findall(text):
            key = raw.replace(",", "")
            n_values.setdefault(key, []).append(loc)
    if len(n_values) > 1:
        summary = ", ".join(f"{k}({len(v)}处)" for k, v in sorted(n_values.items()))
        candidates.append({
            "id": f"nc-nn-{len(candidates)+1:03d}",
            "kind": "n_inconsistency",
            "locator": "multiple",
            "text": f"材料中出现多个 N/n 值：{summary}",
            "note": "候选：需确认是总样本量还是分组样本量，矛盾者提升为 finding",
        })
    return candidates


# ---------------------------------------------------------------------------
# 最终结果组装与渲染
# ---------------------------------------------------------------------------

def derive_status(has_materials, has_case, meta, findings, errors):
    if not has_case and not has_materials:
        return "input_error", "input_error"
    if not has_materials:
        return "insufficient_evidence", "insufficient_evidence"
    m_status = meta.get("status") if isinstance(meta, dict) else None
    m_verdict = meta.get("overall_verdict") if isinstance(meta, dict) else None
    checked = meta.get("checked_facets") if isinstance(meta, dict) else None
    status = m_status if m_status in STATUSES else "partial"
    if status == "partial":
        all_checked = isinstance(checked, dict) and checked and all(
            bool(checked.get(f)) for f in FACETS)
        if all_checked:
            status = "ok"
    verdict = m_verdict if m_verdict in VERDICTS else (
        "problems_found" if findings else "unchecked")
    return status, verdict


def build_result(case_id, contract, findings, meta, num_candidates, errors, degradations,
                 has_materials, has_case):
    counts, by_facet, by_type, by_class = tally(findings)
    status, verdict = derive_status(has_materials, has_case, meta, findings, errors)
    evidence_map = {}
    for f in findings:
        evidence_map[f["id"]] = {
            "locator": f["locator"],
            "quote": f["quote"],
            "source_file": f.get("file", f.get("locator", "")),
            "basis": f.get("basis", ""),
        }
    result = {
        "case_id": case_id,
        "status": status,
        "summary": {
            "verdict": verdict,
            "counts": counts,
            "by_facet": by_facet,
            "by_finding_type": by_type,
            "by_check_class": by_class,
        },
        "review_report": {"title": "研究方法与统计审查单", "findings": findings},
        "result_status": _result_status_cn(status, verdict),
        "evidence": {"evidence_map": evidence_map},
        "sources": meta.get("sources", []) if isinstance(meta, dict) else [],
        "uncertainty": meta.get("uncertainty", []) if isinstance(meta, dict) else [],
        "pending_human": meta.get("pending_human", []) if isinstance(meta, dict) else [],
        "limitations": meta.get("limitations", []) if isinstance(meta, dict) else [],
        "errors": list(errors),
        "degradations": list(degradations),
        "numeric_checks": num_candidates,
        "change_log": meta.get("change_log", []) if isinstance(meta, dict) else [],
        "runtime": build_runtime(),
    }
    # 回填 output_contract：规范字段填真实数据，未知键原样保留
    return apply_contract(result, contract)


def _result_status_cn(status, verdict):
    if status == "input_error":
        return "未检查（输入错误）"
    if status == "insufficient_evidence":
        return "未检查（材料不足）"
    if verdict == "pass":
        return "通过"
    if verdict == "problems_found":
        return "问题"
    if verdict == "needs_review":
        return "待复核"
    return "未检查"


# 契约字段 -> 本管线的规范字段映射：契约占位符由真实/空数据填充，
# 其余未知契约键原样保留（不改名、不删除）。
CONTRACT_ALIASES = {
    "case_id": lambda r: r.get("case_id"),
    "status": lambda r: r.get("status"),
    "summary": lambda r: r.get("summary"),
    "review_report": lambda r: r.get("review_report"),
    "findings": lambda r: (r.get("review_report") or {}).get("findings", []),
    "result_status": lambda r: r.get("result_status"),
    "evidence": lambda r: r.get("evidence"),
    "sources": lambda r: r.get("sources"),
    "uncertainty": lambda r: r.get("uncertainty"),
    "pending_human": lambda r: r.get("pending_human"),
    "limitations": lambda r: r.get("limitations"),
    "errors": lambda r: r.get("errors"),
    "degradations": lambda r: r.get("degradations"),
    "numeric_checks": lambda r: r.get("numeric_checks"),
    "change_log": lambda r: r.get("change_log"),
    "runtime": lambda r: r.get("runtime"),
}


def apply_contract(result, contract):
    """把 output_contract 合并进结果：规范字段填真实数据，未知键原样保留。"""
    if not isinstance(contract, dict):
        return result
    for k, v in contract.items():
        if k in CONTRACT_ALIASES:
            result[k] = CONTRACT_ALIASES[k](result)
        elif k not in result:
            result[k] = v
    return result


def merge_unknown(existing, result):
    """保留旧 result.json 中结果对象未覆盖的未知顶层键（增量编辑安全）。"""
    for k, v in existing.items():
        if k not in result:
            result[k] = v
    return result


def _fmt_item(v):
    """把列表项格式化为一行可读文本（dict 取 code + message）。"""
    if isinstance(v, dict):
        code = v.get("code", "")
        msg = v.get("message", "")
        extra = {k: val for k, val in v.items() if k not in ("code", "message") and val is not None}
        s = (f"{code}: {msg}" if code else str(msg))
        if extra:
            s += " | " + ", ".join(f"{k}={val}" for k, val in extra.items())
        return s
    return str(v)


def render_report(result):
    """渲染人类可读 report.md。"""
    L = []
    L.append("# 研究方法与统计审查单\n")
    L.append(f"- case_id: `{result.get('case_id','')}`")
    L.append(f"- status: `{result.get('status','')}`")
    L.append(f"- verdict: `{result['summary'].get('verdict','')}`")
    L.append(f"- result_status: {result.get('result_status','')}\n")
    counts = result["summary"].get("counts", {})
    L.append("## 摘要\n")
    L.append(f"- 总计 {counts.get('total',0)} 条；critical {counts.get('critical',0)}、"
             f"major {counts.get('major',0)}、minor {counts.get('minor',0)}、"
             f"advisory {counts.get('advisory',0)}、pending {counts.get('pending',0)}\n")
    L.append("## 逐条意见\n")
    rr = result.get("review_report")
    findings = rr.get("findings", []) if isinstance(rr, dict) else []
    if not findings:
        L.append("（无已确证 finding；见 limitations / errors。）\n")
    for f in findings:
        L.append(f"### {f.get('id','?')}　{f.get('facet','')} / {f.get('check_class','')}")
        L.append(f"- 类型：`{f.get('finding_type','')}`；严重度：`{f.get('severity','')}`；"
                 f"置信度：`{f.get('confidence','')}`；复核状态：`{f.get('review_status','')}`")
        L.append(f"- 定位：`{f.get('locator','')}`")
        L.append(f"- 依据：{f.get('basis','')}")
        L.append(f"- 修改建议：{f.get('suggestion','')}")
        L.append(f"- 逐字摘录：\n\n  > {f.get('quote','')}\n")
    nc = result.get("numeric_checks", [])
    if nc:
        L.append("## 数值一致性候选（须人工确证）\n")
        for c in nc:
            L.append(f"- [{c.get('kind','')}] `{c.get('locator','')}`：{c.get('text','')} — {c.get('note','')}")
        L.append("")
    for section in ("limitations", "uncertainty", "pending_human", "errors", "degradations"):
        vals = result.get(section, [])
        if vals:
            L.append(f"## {section}\n")
            for v in vals:
                L.append(f"- {_fmt_item(v)}")
            L.append("")
    cl = result.get("change_log", [])
    if cl:
        L.append("## change_log\n")
        for c in cl:
            L.append(f"- {_fmt_item(c)}")
        L.append("")
    return "\n".join(L)


def finalize(ctx):
    """命令 finalize：合并 findings、数值候选、校验、渲染 result.json/report.md。"""
    errors, degradations = [], []
    work = ctx["work"]
    ensure_dir(work)
    case_id, case_obj, e1, d1 = load_case(ctx["input"])
    errors += e1
    degradations += d1
    contract = extract_contract(case_obj)
    snap = read_json_lenient(os.path.join(work, "contract.json"))[0]
    if isinstance(snap, dict) and isinstance(snap.get("output_contract"), dict):
        contract = {**contract, **snap["output_contract"]}
    materials, e2, d2 = scan_materials(ctx["input"])
    errors += e2
    degradations += d2
    has_case = bool(case_obj)
    has_materials = bool(materials)

    findings, e3, d3 = load_findings(os.path.join(work, "findings"))
    errors += e3
    degradations += d3
    valid = []
    for f in sorted(findings, key=_finding_key):
        nf = validate_finding(f, errors, degradations)
        if nf is not None:
            valid.append(nf)
    meta = read_json_lenient(os.path.join(work, "meta.json"))[0]
    if not isinstance(meta, dict):
        meta = {}
    segments = read_json_lenient(os.path.join(work, "segments.json"))[0]
    seg_list = segments.get("segments", []) if isinstance(segments, dict) else []
    num_candidates = compute_numeric_checks(seg_list)

    result = build_result(case_id, contract, valid, meta, num_candidates,
                          errors, degradations, has_materials, has_case)
    existing = read_json_lenient(os.path.join(ctx["output"], "result.json"))[0]
    if isinstance(existing, dict):
        result = merge_unknown(existing, result)

    errs = atomic_write_json(result, os.path.join(ctx["output"], "result.json"))
    errors += errs
    atomic_write_text(render_report(result), os.path.join(ctx["output"], "report.md"))

    print(json.dumps({
        "command": "finalize",
        "case_id": case_id,
        "status": result["status"],
        "findings": len(valid),
        "numeric_candidates": len(num_candidates),
        "errors": len(errors),
        "degradations": len(degradations),
    }, ensure_ascii=False, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# 自检（selftest）
# ---------------------------------------------------------------------------

def _run_cli(argv):
    """进程内运行 main(argv)，捕获 stdout 与退出码（避免子进程管道依赖）。"""
    buf = io.StringIO()
    code = 0
    with redirect_stdout(buf):
        try:
            code = main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return code, buf.getvalue()


def run_selftest():
    """鲁棒性自检：空输入/坏 JSON/坏编码/缺参数/超大文件/确定性/help。"""
    results = []
    tmp = tempfile.mkdtemp(prefix="stats-review-selftest-")

    def scenario(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    def write(path, data, mode="w", enc=None):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        if mode == "wb":
            with open(path, "wb") as f:
                f.write(data)
        else:
            with open(path, mode, encoding=enc or "utf-8") as f:
                f.write(data)

    try:
        # 0) --help 退出码 0
        code, out = _run_cli(["--help"])
        scenario("help_exit_0", code == 0 and "usage" in out.lower(), f"code={code}")

        # 1) 空输入目录（无 case.json 无材料）→ input_error，退出 0
        empty_in = os.path.join(tmp, "s1_in")
        empty_out = os.path.join(tmp, "s1_out")
        empty_work = os.path.join(tmp, "s1_work")
        os.makedirs(empty_in)
        c1 = _run_cli(["scaffold", "--input", empty_in, "--output", empty_out, "--work", empty_work])[0]
        r1 = read_json_lenient(os.path.join(empty_out, "result.json"))[0]
        scenario("empty_input", c1 == 0 and isinstance(r1, dict) and r1.get("status") == "input_error",
                 f"code={c1}, status={r1.get('status') if r1 else None}")

        # 2) 有 case.json 无材料 → insufficient_evidence
        in2 = os.path.join(tmp, "s2_in")
        os.makedirs(in2)
        write(os.path.join(in2, "case.json"),
              json.dumps({"case_id": "s2", "output_contract": {"review_report": None, "findings": []}}))
        out2 = os.path.join(tmp, "s2_out")
        c2 = _run_cli(["scaffold", "--input", in2, "--output", out2, "--work", os.path.join(tmp, "s2_work")])[0]
        r2 = read_json_lenient(os.path.join(out2, "result.json"))[0]
        has_contract = isinstance(r2, dict) and "review_report" in r2 and "findings" in r2
        scenario("no_materials", c2 == 0 and has_contract and r2.get("status") == "insufficient_evidence",
                 f"code={c2}, status={r2.get('status') if r2 else None}")

        # 3) 坏 JSON case.json → 不崩溃，记录 JSON_BAD，退出 0
        in3 = os.path.join(tmp, "s3_in")
        os.makedirs(in3)
        write(os.path.join(in3, "case.json"), "{not valid json!!")
        write(os.path.join(in3, "paper.txt"), "Methods\nSample size was calculated.\n")
        out3 = os.path.join(tmp, "s3_out")
        work3 = os.path.join(tmp, "s3_work")
        c3 = _run_cli(["scaffold", "--input", in3, "--output", out3, "--work", work3])[0]
        r3 = read_json_lenient(os.path.join(out3, "result.json"))[0]
        bad_recorded = any(e.get("code") == "JSON_BAD" for e in (r3.get("errors") or [])) if r3 else False
        scenario("bad_json", c3 == 0 and isinstance(r3, dict) and bad_recorded,
                 f"code={c3}, bad_recorded={bad_recorded}")

        # 4) 坏编码材料（GBK 字节）→ ENC_FALLBACK 降级，不崩溃
        in4 = os.path.join(tmp, "s4_in")
        os.makedirs(in4)
        write(os.path.join(in4, "case.json"), json.dumps({"case_id": "s4"}))
        write(os.path.join(in4, "gbk.txt"), "方法：样本量计算已完成。".encode("gbk"), mode="wb")
        out4 = os.path.join(tmp, "s4_out")
        work4 = os.path.join(tmp, "s4_work")
        c4 = _run_cli(["discover", "--input", in4, "--output", out4, "--work", work4])[0]
        m4 = read_json_lenient(os.path.join(work4, "manifest.json"))[0]
        enc_fallback = any(d.get("code") == "ENC_FALLBACK" for d in (m4.get("degradations") or [])) if m4 else False
        seg4 = read_json_lenient(os.path.join(work4, "segments.json"))[0]
        scenario("bad_encoding", c4 == 0 and enc_fallback and (seg4 or {}).get("count", 0) >= 1,
                 f"code={c4}, fallback={enc_fallback}")

        # 5) 超大文件 → SIZE_SKIP 降级（临时调低上限，避免真实写 8MB）
        in5 = os.path.join(tmp, "s5_in")
        os.makedirs(in5)
        write(os.path.join(in5, "case.json"), json.dumps({"case_id": "s5"}))
        write(os.path.join(in5, "huge.txt"), "x" * 4000)
        out5 = os.path.join(tmp, "s5_out")
        work5 = os.path.join(tmp, "s5_work")
        saved_cap = MAX_TEXT_BYTES
        globals()["MAX_TEXT_BYTES"] = 2000
        try:
            c5 = _run_cli(["discover", "--input", in5, "--output", out5, "--work", work5])[0]
        finally:
            globals()["MAX_TEXT_BYTES"] = saved_cap
        m5 = read_json_lenient(os.path.join(work5, "manifest.json"))[0]
        size_skip = any(d.get("code") == "SIZE_SKIP" for d in (m5.get("degradations") or [])) if m5 else False
        scenario("oversized_file", c5 == 0 and size_skip,
                 f"code={c5}, size_skip={size_skip}")

        # 6) 确定性：同一输入两次 finalize 结果字节级一致
        in6 = os.path.join(tmp, "s6_in")
        os.makedirs(in6)
        write(os.path.join(in6, "case.json"), json.dumps({"case_id": "s6"}))
        write(os.path.join(in6, "paper.txt"),
              "Study design: randomized trial.\nResults: p = 0.04, significant.\n")
        out6 = os.path.join(tmp, "s6_out")
        work6 = os.path.join(tmp, "s6_work")
        find6 = os.path.join(work6, "findings")
        os.makedirs(find6, exist_ok=True)
        write(os.path.join(find6, "F001.json"), json.dumps({
            "id": "F001", "facet": "results_reporting", "check_class": "statistical_reporting",
            "finding_type": "advisory", "locator": "paper.txt p.? ¶1",
            "quote": "p = 0.04, significant", "severity": "advisory",
            "suggestion": "报告精确 p 值与置信区间。", "confidence": "high",
            "review_status": "confirmed", "basis": "正文结果段",
        }))
        write(os.path.join(work6, "meta.json"), json.dumps({
            "status": "partial", "overall_verdict": "problems_found",
            "checked_facets": {f: True for f in FACETS},
            "checked_classes": {c: True for c in CHECK_CLASSES},
        }))
        for _ in range(2):
            _run_cli(["discover", "--input", in6, "--output", out6, "--work", work6])
            _run_cli(["finalize", "--input", in6, "--output", out6, "--work", work6])
            snapA = read_json_lenient(os.path.join(out6, "result.json"))[0]
            _run_cli(["finalize", "--input", in6, "--output", out6, "--work", work6])
            snapB = read_json_lenient(os.path.join(out6, "result.json"))[0]
        determin = snapA == snapB
        r6 = read_json_lenient(os.path.join(out6, "result.json"))[0]
        scenario("deterministic", determin and (r6 or {}).get("summary", {}).get("counts", {}).get("total") == 1,
                 f"identical={determin}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    print(f"selftest: {passed}/{total} passed")
    return 0 if passed == total else 1


# ---------------------------------------------------------------------------
# 入口与参数
# ---------------------------------------------------------------------------

USAGE = """usage: main.py <command> [--input DIR] [--output DIR] [--work DIR] [--help]

commands:
  discover   scan inputs, extract text (PDF page-level), build indexes
  scaffold   write a valid partial result.json skeleton immediately
  finalize   merge findings, run numeric-candidate checks, render report
  selftest   run robustness self-checks (in-process)
  version    print version

options:
  --input DIR    input directory (default: /input)
  --output DIR   output directory (default: /app/output)
  --work DIR     work directory (default: /app/work)
  --help, -h     show this help and exit
"""

COMMANDS = {"discover", "scaffold", "finalize", "selftest", "version", "help"}


def parse_args(argv):
    opts = {
        "command": None,
        "input": DEFAULTS["input"],
        "output": DEFAULTS["output"],
        "work": DEFAULTS["work"],
        "help": False,
        "unknown": [],
        "missing_value": [],
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            opts["help"] = True
        elif a in ("--version",):
            opts["command"] = "version"
        elif a in ("--input", "--output", "--work"):
            key = a[2:]
            if i + 1 >= len(argv):
                opts["missing_value"].append(key)
            else:
                opts[key] = argv[i + 1]
                i += 1
        elif a.startswith("--input="):
            opts["input"] = a.split("=", 1)[1]
        elif a.startswith("--output="):
            opts["output"] = a.split("=", 1)[1]
        elif a.startswith("--work="):
            opts["work"] = a.split("=", 1)[1]
        elif a in COMMANDS and opts["command"] is None:
            opts["command"] = a
        else:
            opts["unknown"].append(a)
        i += 1
    return opts


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    opts = parse_args(list(argv))
    if opts["help"]:
        print(USAGE, end="")
        return 0
    if opts["missing_value"]:
        print(f"error: missing value for --{opts['missing_value'][0]}", file=sys.stderr)
        return 2
    if opts["unknown"]:
        print(f"error: unknown argument: {opts['unknown'][0]}", file=sys.stderr)
        print(USAGE, file=sys.stderr, end="")
        return 2
    cmd = opts["command"]
    if cmd is None or cmd == "help":
        print(USAGE, end="")
        return 0
    if cmd == "version":
        print(f"research-methods-stats-review {VERSION}")
        return 0
    ctx = {"input": opts["input"], "output": opts["output"], "work": opts["work"]}
    if cmd == "discover":
        return discover(ctx)
    if cmd == "scaffold":
        return scaffold(ctx)
    if cmd == "finalize":
        return finalize(ctx)
    if cmd == "selftest":
        return run_selftest()
    print(f"error: unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
