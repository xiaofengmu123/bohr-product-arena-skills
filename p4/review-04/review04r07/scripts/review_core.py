# -*- coding: utf-8 -*-
"""review_core.py — 论文逻辑与论证审查 Skill 的核心实现（仅标准库）。

职责：
  * 输入发现与容错读取（坏 JSON、坏编码、超限文件、缺目录均降级不崩溃）
  * PDF / DOCX / 文本抽取与逐段切分（定位符 `p<页> ¶P<段落>`）
  * 结果骨架、增量合并、自检；findings 的逐字 quote 自动回验语料，防编造
  * 原子写盘；任何异常都会落最小合法结果，不留半截 result.json

本文件不依赖第三方库；路径统一用 pathlib，跨平台可用。所有随机性均已消除：
除 change_log / runtime 时间戳外，同一输入重复运行产生相同的审查内容。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 常量与枚举
# ---------------------------------------------------------------------------
RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

FINDING_TYPES = (
    "overclaim",                  # 结论强度超出数据/证据支撑
    "citation_unsupported",        # 引用文献不支持所引观点
    "method_mismatch",             # 方法无法回答研究问题
    "stat_misdirection",           # 统计口径/方向与结论不符
    "missing_delivery",            # 引言承诺未在结果/讨论交付
    "causal_from_correlational",   # 相关性设计误作因果表述
    "inconsistency",               # 跨章节/图表/摘要结论不一致
    "evidence_gap",                # 主张缺少必要证据
    "norm_issue",                  # 学术规范问题
    "suggestion",                  # 建议性意见（非缺陷）
)

SEVERITIES = ("critical", "major", "minor", "suggestion")
FINDING_STATUSES = ("confirmed", "probable", "pending_review", "ruled_out")
CONFIDENCES = ("high", "medium", "low")

COVERAGE_DIMS = (
    "factual_verification",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argument_chain",
    "academic_norms",
)

COVERAGE_LABELS = {
    "factual_verification": "事实核验",
    "citation_support": "引用支持",
    "method_design": "方法设计",
    "statistical_reporting": "统计报告",
    "argument_chain": "论证链",
    "academic_norms": "学术规范",
}

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024          # 单文件上限 2MB
MAX_CORPUS_BYTES = 20 * 1024 * 1024       # 抽取语料总量上限 20MB
MAX_MATERIAL_FILES = 500                  # 材料文件数上限
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

MATERIAL_EXTS = {".pdf", ".txt", ".md", ".markdown", ".tex", ".docx", ".doc"}
MATERIAL_BASENAMES = {"materials.md", "paper.md", "notes.md", "readme.md", "manuscript.md"}

SECTION_PATTERNS = (
    (re.compile(r"\babstract\b", re.I), "abstract"),
    (re.compile(r"\bintroduction\b|\bbackground\b", re.I), "introduction"),
    (re.compile(r"\bmethods?\b|\bmaterials\b|\bexperimental\b|\bprocedure\b", re.I), "methods"),
    (re.compile(r"\bresults?\b|\bfindings\b", re.I), "results"),
    (re.compile(r"\bdiscussion\b", re.I), "discussion"),
    (re.compile(r"\bconclusions?\b", re.I), "conclusion"),
    (re.compile(r"\breferences?\b", re.I), "references"),
)

_WS_RE = re.compile(r"\s+")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_ws(s: str) -> str:
    return _WS_RE.sub(" ", s or "").strip()


# ---------------------------------------------------------------------------
# 容错读取
# ---------------------------------------------------------------------------
def read_bytes_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    p = Path(path)
    out: Dict[str, Any] = {"path": str(p), "bytes": b"", "truncated": False,
                           "error": None, "size": 0}
    try:
        if not p.exists():
            out["error"] = "file_not_found"
            return out
        if not p.is_file():
            out["error"] = "not_a_file"
            return out
        size = p.stat().st_size
        out["size"] = size
        with open(p, "rb") as f:
            data = f.read(max_bytes)
            extra = f.read(1)
            out["truncated"] = bool(extra)
        out["bytes"] = data
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    return out


def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    raw = read_bytes_safe(path, max_bytes)
    if raw["error"] or not raw["bytes"]:
        return {"path": raw["path"], "text": "", "bytes": 0,
                "truncated": raw["truncated"], "encoding": None,
                "error": raw["error"] or "empty_file"}
    data = raw["bytes"]
    text: Optional[str] = None
    used: Optional[str] = None
    for enc in TEXT_ENCODINGS:
        try:
            text = data.decode(enc)
            used = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
        used = "utf-8(replace)"
    if text.startswith("\ufeff"):
        text = text[1:]
    return {"path": raw["path"], "text": text, "bytes": len(data),
            "truncated": raw["truncated"], "encoding": used, "error": None}


def read_json_tolerant(path: str, max_bytes: int = MAX_FILE_BYTES):
    """读取 JSON，坏编码/坏 JSON 均降级为 ({}, error)，绝不抛异常。"""
    t = read_text_safe(path, max_bytes)
    if t["error"]:
        return ({}, "read_error:" + (t["error"] or "unknown"))
    text = t["text"]
    candidates = [text.strip()]
    try:
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            candidates.append(text[first:last + 1])
    except Exception:  # noqa: BLE001
        pass
    last_err: Optional[str] = None
    for cand in candidates:
        if not cand:
            continue
        try:
            obj = json.loads(cand)
            return (obj, None)
        except json.JSONDecodeError as e:
            last_err = str(e)
    return ({}, "json_decode_error:" + (last_err or "unknown"))


# ---------------------------------------------------------------------------
# PDF / DOCX 抽取
# ---------------------------------------------------------------------------
def extract_pdf_text(pdf_path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    pdf = Path(pdf_path)
    out: Dict[str, Any] = {"text": "", "pages": 0, "error": None, "tool": None}
    exe = shutil.which("pdftotext")
    if not exe:
        out["error"] = "pdftotext_not_found"
        return out
    out["tool"] = exe
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="pdftxt_", suffix=".txt")
        os.close(fd)
        proc = subprocess.run(
            [exe, "-enc", "UTF-8", str(pdf), tmp],
            capture_output=True, timeout=180,
        )
        if proc.returncode != 0:
            out["error"] = "pdftotext_exit_" + str(proc.returncode)
            return out
        t = read_text_safe(tmp, max_bytes)
        out["text"] = t["text"]
        out["pages"] = (out["text"].count("\f") + 1) if out["text"] else 0
        if t["truncated"]:
            out["error"] = "pdf_text_truncated"
    except FileNotFoundError:
        out["error"] = "pdftotext_not_found"
    except subprocess.TimeoutExpired:
        out["error"] = "pdftotext_timeout"
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    finally:
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
    return out


def _extract_docx_plain(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    """无 python-docx 时的兜底：docx 是 zip，读 word/document.xml 去标签。"""
    out: Dict[str, Any] = {"text": "", "error": None}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            target = None
            if "word/document.xml" in names:
                target = "word/document.xml"
            else:
                hits = [n for n in names if n.endswith("document.xml")]
                if hits:
                    target = hits[0]
            if target is None:
                out["error"] = "docx_no_document_xml"
                return out
            xml = z.read(target).decode("utf-8", errors="replace")
        xml = xml.replace("</w:p>", "\n")
        text = re.sub(r"<[^>]+>", "", xml)
        for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                     ("&quot;", '"'), ("&apos;", "'")):
            text = text.replace(a, b)
        out["text"] = text[:max_bytes]
    except Exception as e:  # noqa: BLE001
        out["error"] = "docx_extract_error:" + str(e)[:150]
    return out


# ---------------------------------------------------------------------------
# 段落切分与章节启发
# ---------------------------------------------------------------------------
def split_paragraphs(text: str) -> List[str]:
    if not text:
        return []
    chunks = re.split(r"\n\s*\n", text)
    out = []
    for chunk in chunks:
        c = chunk.strip()
        if c:
            out.append(c)
    return out


def detect_section(line: str) -> Optional[str]:
    s = (line or "").strip()
    if not s or len(s) > 80:
        return None
    for pat, name in SECTION_PATTERNS:
        if pat.search(s):
            return name
    return None


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------
def _is_material(p: Path) -> bool:
    name = p.name.lower()
    return p.suffix.lower() in MATERIAL_EXTS or name in MATERIAL_BASENAMES


def discover_inputs(input_dir: str) -> Dict[str, Any]:
    d = Path(input_dir)
    info: Dict[str, Any] = {
        "case_json": None, "task_md": None, "materials": [],
        "warnings": [], "input_exists": (d.exists() and d.is_dir()),
    }
    if not info["input_exists"]:
        info["warnings"].append("input_dir_missing:" + str(d))
        return info
    for name in ("case.json", "case.JSON"):
        p = d / name
        if p.is_file():
            info["case_json"] = str(p)
            break
    for name in ("task.md", "TASK.md", "task.MD"):
        p = d / name
        if p.is_file():
            info["task_md"] = str(p)
            break
    skip = {Path(info["case_json"]).name if info["case_json"] else "case.json",
            Path(info["task_md"]).name if info["task_md"] else "task.md"}
    seen = set()
    # 1) 顶层材料文件（非递归，避免误入大目录树）
    for p in sorted(d.iterdir()):
        if not p.is_file() or p.name in skip:
            continue
        if _is_material(p):
            seen.add(str(p))
            info["materials"].append(str(p))
    # 2) materials/ 递归，带文件数上限
    matdir = d / "materials"
    if matdir.is_dir():
        for p in sorted(matdir.rglob("*")):
            if len(info["materials"]) >= MAX_MATERIAL_FILES:
                info["warnings"].append("material_file_cap_reached")
                break
            if not p.is_file() or p.name in skip:
                continue
            sp = str(p)
            if sp in seen:
                continue
            seen.add(sp)
            if _is_material(p):
                info["materials"].append(sp)
    return info


# ---------------------------------------------------------------------------
# 语料构建
# ---------------------------------------------------------------------------
def locator(rec: Dict[str, Any]) -> str:
    s = ""
    if rec.get("page"):
        s += "p%d " % rec["page"]
    s += "\u00b6%s" % rec.get("id", "?")
    return s.strip()


def build_records(materials: List[str], work_dir: str,
                  max_file_bytes: int = MAX_FILE_BYTES,
                  max_corpus_bytes: int = MAX_CORPUS_BYTES):
    records: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    degradations: List[Dict[str, Any]] = []
    total_chars = 0
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    for mp in materials:
        p = Path(mp)
        src_entry: Dict[str, Any] = {
            "path": mp, "kind": "material", "n_pages": 0, "chars": 0,
            "error": None,
        }
        try:
            size = p.stat().st_size if p.exists() else 0
        except OSError:
            size = 0
        if size > max_file_bytes:
            degradations.append({"path": mp, "reason": "file_too_large",
                                 "detail": "size=%d limit=%d" % (size, max_file_bytes)})
            src_entry["error"] = "skipped_too_large"
            sources.append(src_entry)
            continue

        ext = p.suffix.lower()
        pages: List[Dict[str, Any]] = []
        is_pdf = (ext == ".pdf")
        text: Optional[str] = None

        if ext == ".pdf":
            r = extract_pdf_text(mp, max_bytes=max_file_bytes)
            if r["error"]:
                degradations.append({"path": mp, "reason": "pdf_extract_failed",
                                     "detail": r["error"]})
                src_entry["error"] = r["error"]
            else:
                text = r["text"]
                chunks = text.split("\f") if "\f" in text else [text]
                for pi, chunk in enumerate(chunks, 1):
                    pages.append({"page": pi, "paras": split_paragraphs(chunk)})
        elif ext in (".docx", ".doc"):
            r = _extract_docx_plain(mp, max_bytes=max_file_bytes)
            if r["error"]:
                degradations.append({"path": mp, "reason": "docx_extract_skipped",
                                     "detail": r["error"]})
                src_entry["error"] = r["error"]
            else:
                text = r["text"]
                pages = [{"page": None, "paras": split_paragraphs(text)}]
        else:
            t = read_text_safe(mp, max_bytes=max_file_bytes)
            if t["error"]:
                degradations.append({"path": mp, "reason": "text_read_failed",
                                     "detail": t["error"]})
                src_entry["error"] = t["error"]
            else:
                if t["truncated"]:
                    degradations.append({"path": mp, "reason": "text_truncated",
                                         "detail": "read %d bytes" % t["bytes"]})
                text = t["text"]
                pages = [{"page": None, "paras": split_paragraphs(text)}]

        n_chars = 0
        current_section: Optional[str] = None
        stop = False
        for pg in pages:
            for para_text in pg["paras"]:
                if total_chars + len(para_text) > max_corpus_bytes:
                    degradations.append({"path": mp, "reason": "corpus_size_cap",
                                         "detail": "stop at %d chars" % max_corpus_bytes})
                    stop = True
                    break
                first_line = para_text.splitlines()[0] if para_text else ""
                det = detect_section(first_line)
                if det:
                    current_section = det
                para_id = "P%04d" % (len(records) + 1)
                rec = {
                    "id": para_id, "src": mp, "page": pg.get("page"),
                    "section": current_section, "kind": "paragraph",
                    "text": para_text,
                }
                records.append(rec)
                total_chars += len(para_text)
                n_chars += len(para_text)
            if stop:
                break
        src_entry["n_pages"] = len(pages) if is_pdf else 0
        src_entry["chars"] = n_chars
        sources.append(src_entry)
    return records, sources, degradations


def build_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                    path: str) -> None:
    lines = ["# 语料库（供逐段审查，段落 ID 可直接引用）", ""]
    lines.append("来源文件 %d 个，段落 %d 个。" % (len(sources), len(records)))
    lines.append("定位符格式：`p<页> \u00b6P<段落>`；文本来源无页码时省略 p 部分。")
    lines.append("引用原文必须逐字复制，不得改写；update 会自动回验 quote 是否在语料中。")
    lines.append("")
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_src.setdefault(rec["src"], []).append(rec)
    for src in sources:
        key = src["path"]
        recs = by_src.get(key, [])
        lines.append("")
        lines.append("## 来源: %s" % key)
        lines.append("- kind=%s n_pages=%s chars=%s error=%s" % (
            src.get("kind"), src.get("n_pages"), src.get("chars"), src.get("error")))
        lines.append("")
        for rec in recs:
            lines.append("### %s [%s] %s" % (locator(rec), rec["section"] or "?", rec["kind"]))
            lines.append(rec["text"])
            lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 原子写盘
# ---------------------------------------------------------------------------
def write_json_atomic(obj: Any, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))


def write_text_atomic(text: str, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------------------
# 元数据与契约
# ---------------------------------------------------------------------------
def _load_task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None, "case_json": None}
    if discovered.get("case_json"):
        data, err = read_json_tolerant(discovered["case_json"])
        meta["case_json"] = discovered["case_json"]
        if err:
            meta["_parse_error"] = err
        if isinstance(data, dict):
            meta["task_title"] = data.get("title") or data.get("task_title")
            meta["challenge_id"] = (data.get("challenge_id") or data.get("challengeId")
                                    or data.get("id"))
    if discovered.get("task_md"):
        meta["task_md"] = discovered["task_md"]
        r = read_text_safe(discovered["task_md"], max_bytes=200_000)
        for line in r["text"].splitlines()[:80]:
            ls = line.strip()
            if ls.startswith("#") and ls.lstrip("#").strip():
                meta.setdefault("task_title", ls.lstrip("#").strip())
                break
    return meta


def _derive_case_id(discovered: Dict[str, Any], task_meta: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            for key in ("case_id", "id", "caseId", "caseid"):
                v = data.get(key)
                if isinstance(v, str) and v:
                    return v
        return Path(discovered["case_json"]).stem
    return "unknown_case"


def _contract_extra(discovered: Dict[str, Any]) -> Dict[str, Any]:
    if not discovered.get("case_json"):
        return {}
    data, _ = read_json_tolerant(discovered["case_json"])
    if isinstance(data, dict):
        c = data.get("output_contract")
        if isinstance(c, dict):
            return dict(c)
    return {}


def _default_coverage() -> Dict[str, Dict[str, Any]]:
    return {d: {"status": "skipped", "checked": 0, "note": ""} for d in COVERAGE_DIMS}


# ---------------------------------------------------------------------------
# 报告渲染（report.md 由 result.json 单向生成，保证同步）
# ---------------------------------------------------------------------------
def _render_report(data: Dict[str, Any]) -> str:
    lines = ["# 论文逻辑与论证审查报告", ""]
    lines.append("- case_id: %s" % data.get("case_id"))
    lines.append("- 状态: %s" % data.get("status"))
    lines.append("")
    review = data.get("review") or {}
    lines.append("## 0. 方法与范围")
    lines.append(review.get("summary") or "（未填写总评）")
    lines.append("")
    lines.append("## 1. 来源")
    for s in data.get("sources", []):
        lines.append("- `%s` kind=%s pages=%s chars=%s %s" % (
            s.get("path"), s.get("kind"), s.get("n_pages"), s.get("chars"),
            ("error=" + s["error"]) if s.get("error") else ""))
    if not data.get("sources"):
        lines.append("（无可用来源）")
    lines.append("")
    lines.append("## 2. 主张清单")
    claims = review.get("claims") or []
    for c in claims:
        lines.append("- **%s** [%s] speaker=%s verdict=%s" % (
            c.get("id"), c.get("locator") or "?", c.get("speaker"), c.get("verdict")))
        if c.get("text"):
            lines.append("  - 原文: %s" % c["text"])
        if c.get("evidence"):
            lines.append("  - 依据: %s" % c["evidence"])
    if not claims:
        lines.append("（无）")
    lines.append("")
    lines.append("## 3. 问题清单（findings）")
    for f in review.get("findings") or []:
        lines.append("### %s %s / %s" % (f.get("id"), f.get("type"), f.get("severity")))
        lines.append("- 定位: %s" % (f.get("locator") or "?"))
        lines.append("- 原文: %s" % f.get("quote"))
        if f.get("span"):
            lines.append("- 字符区间: %s" % f.get("span"))
        lines.append("- 主张: %s" % f.get("claim"))
        lines.append("- 依据: %s" % f.get("evidence"))
        lines.append("- 理由: %s" % f.get("reasoning"))
        lines.append("- 建议: %s" % f.get("suggestion"))
        lines.append("- 状态/置信度: %s / %s" % (f.get("status"), f.get("confidence")))
        if f.get("quote_match") is False:
            lines.append("- ⚠ quote 未在语料中核到: %s" % f.get("quote_verify_note"))
        lines.append("")
    if not review.get("findings"):
        lines.append("（无）")
        lines.append("")
    lines.append("## 4. 六类检查项覆盖情况")
    lines.append("| 维度 | 状态 | 检查数 | 说明 |")
    lines.append("|---|---|---|---|")
    cov = review.get("coverage") or {}
    for d in COVERAGE_DIMS:
        c = cov.get(d) or {}
        lines.append("| %s | %s | %s | %s |" % (
            COVERAGE_LABELS[d], c.get("status", "skipped"),
            c.get("checked", 0), c.get("note", "")))
    lines.append("")
    lines.append("## 5. 依据与边界")
    lines.append("- uncertainty: %s" % json.dumps(data.get("uncertainty", []), ensure_ascii=False))
    lines.append("- pending_review: %s" % json.dumps(data.get("pending_review", []), ensure_ascii=False))
    lines.append("- pending_human: %s" % json.dumps(data.get("pending_human", []), ensure_ascii=False))
    lines.append("")
    lines.append("## 6. 待复核项与限制")
    lines.append("- limitations: %s" % json.dumps(data.get("limitations", []), ensure_ascii=False))
    lines.append("- errors: %s" % json.dumps(data.get("errors", []), ensure_ascii=False))
    lines.append("- degradations: %s" % json.dumps(data.get("degradations", []), ensure_ascii=False))
    lines.append("")
    lines.append("## 7. 变更记录")
    for cl in data.get("change_log", []):
        lines.append("- %s %s: %s" % (cl.get("ts"), cl.get("event"), cl.get("note")))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------
def scaffold(input_dir: str = DEFAULT_INPUT, output_dir: str = DEFAULT_OUTPUT,
             work_dir: str = DEFAULT_WORK, force: bool = False,
             max_file_bytes: int = MAX_FILE_BYTES,
             max_corpus_bytes: int = MAX_CORPUS_BYTES) -> Dict[str, Any]:
    t0 = time.time()
    discovered = discover_inputs(input_dir)
    task_meta = _load_task_meta(discovered)
    case_id = _derive_case_id(discovered, task_meta)
    contract = _contract_extra(discovered)
    records, sources, degradations = build_records(
        discovered["materials"], work_dir, max_file_bytes, max_corpus_bytes)

    errors: List[str] = []
    if not discovered["input_exists"]:
        errors.append("input_dir_missing:" + str(input_dir))
    if task_meta.get("_parse_error"):
        errors.append("case_json_parse:" + str(task_meta["_parse_error"]))
    for w in discovered["warnings"]:
        degradations.append({"path": str(input_dir), "reason": w, "detail": ""})

    if not discovered["input_exists"]:
        status = "input_error"
    elif errors:
        status = "input_error"
    elif not sources or all(not s.get("chars") for s in sources):
        status = "insufficient_evidence"
    else:
        status = "partial"

    # section_map：章节 → 段落/页 的粗映射（仅导航辅助）
    section_map: List[Dict[str, Any]] = []
    for rec in records:
        sec = rec.get("section")
        if not section_map or section_map[-1].get("section") != sec:
            section_map.append({"section": sec or "unlabeled", "paras": [], "pages": []})
        section_map[-1]["paras"].append(rec["id"])
        if rec.get("page") and rec["page"] not in section_map[-1]["pages"]:
            section_map[-1]["pages"].append(rec["page"])

    result: Dict[str, Any] = {}
    for k, v in contract.items():  # 原样回填 output_contract 全部键
        result[k] = v
    out_dir_abs = str(Path(output_dir).resolve())
    work_dir_abs = str(Path(work_dir).resolve())
    result.update({
        "case_id": case_id,
        "status": status,
        "task": {"challenge_id": task_meta.get("challenge_id"),
                 "task_title": task_meta.get("task_title")},
        "artifacts": {
            "result_json": out_dir_abs + os.sep + "result.json",
            "report_md": out_dir_abs + os.sep + "report.md",
        },
        "review": {
            "summary": "",
            "findings": [],
            "claims": [],
            "commitments": [],
            "coverage": _default_coverage(),
            "section_map": section_map,
        },
        "sources": sources,
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [{"ts": _utcnow(), "event": "scaffold",
                        "note": "initial skeleton, sources=%d" % len(sources)}],
        "runtime": {"seconds_elapsed": round(time.time() - t0, 2),
                    "model_requests_used": 0,
                    "budget": {"requests": 64, "seconds": 900}},
    })

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    build_corpus_md(records, sources, work_dir_abs + os.sep + "corpus.md")
    write_json_atomic({"records": records, "sources": sources},
                      work_dir_abs + os.sep + "corpus.json")
    write_json_atomic(result, out_dir_abs + os.sep + "result.json")
    write_text_atomic(_render_report(result), out_dir_abs + os.sep + "report.md")
    write_json_atomic({"started_at": t0, "case_id": case_id, "status": status},
                      work_dir_abs + os.sep + "meta.json")
    return result


# ---------------------------------------------------------------------------
# quote 回验（防编造）
# ---------------------------------------------------------------------------
def verify_quote(quote: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    q = normalize_ws(quote)
    if len(q) < 6:
        return {"found": False, "reason": "quote_too_short_or_empty",
                "locator": None, "span": None, "src": None}
    for rec in records:
        raw = rec.get("text", "")
        idx = raw.find(quote)
        if idx >= 0:
            return {"found": True, "locator": locator(rec),
                    "span": [idx, idx + len(quote)], "src": rec.get("src")}
        nidx = normalize_ws(raw).find(q)
        if nidx >= 0:
            return {"found": True, "locator": locator(rec),
                    "span": None, "src": rec.get("src")}
    return {"found": False, "reason": "quote_not_in_corpus",
            "locator": None, "span": None, "src": None}


def normalize_finding(f: Dict[str, Any], idx: int,
                      records: List[Dict[str, Any]],
                      problems: List[Dict[str, Any]]) -> Dict[str, Any]:
    fid = f.get("id") or ("F%03d" % (idx + 1))
    ftype = f.get("type", "suggestion")
    if ftype not in FINDING_TYPES:
        problems.append({"id": fid, "issue": "unknown_type", "detail": str(ftype)})
        ftype = "suggestion"
    sev = f.get("severity", "minor")
    if sev not in SEVERITIES:
        problems.append({"id": fid, "issue": "unknown_severity", "detail": str(sev)})
        sev = "minor"
    fstatus = f.get("status", "pending_review")
    if fstatus not in FINDING_STATUSES:
        fstatus = "pending_review"
    conf = f.get("confidence", "medium")
    if conf not in CONFIDENCES:
        conf = "medium"
    quote = f.get("quote", "")
    v = verify_quote(quote, records)
    out = {
        "id": fid, "type": ftype, "severity": sev,
        "locator": f.get("locator") or v.get("locator") or "",
        "span": f.get("span") or v.get("span"),
        "quote": quote, "claim": f.get("claim", ""),
        "evidence": f.get("evidence", ""), "reasoning": f.get("reasoning", ""),
        "suggestion": f.get("suggestion", ""),
        "status": fstatus, "confidence": conf,
        "quote_match": v["found"],
    }
    if not v["found"]:
        out["quote_verify_note"] = v.get("reason", "quote_not_in_corpus")
        problems.append({"id": fid, "issue": "quote_not_verifiable",
                         "detail": v.get("reason", "")})
    elif f.get("locator") and f.get("locator") != v.get("locator"):
        out["locator_verify_note"] = ("agent_locator=%s corpus_match=%s"
                                      % (f["locator"], v["locator"]))
        problems.append({"id": fid, "issue": "locator_mismatch",
                         "detail": out["locator_verify_note"]})
    return out


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
def update(output_dir: str = DEFAULT_OUTPUT, work_dir: str = DEFAULT_WORK,
           findings_file: Optional[str] = None, claims_file: Optional[str] = None,
           summary: Optional[str] = None, status: Optional[str] = None,
           max_file_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    out_json = Path(output_dir) / "result.json"
    data, err = read_json_tolerant(str(out_json), max_bytes=50 * 1024 * 1024)
    if err or not isinstance(data, dict):
        data = {"case_id": "unknown_case", "status": "partial"}

    corpus: Dict[str, Any] = {"records": []}
    cj = Path(work_dir) / "corpus.json"
    if cj.exists():
        cd, _ = read_json_tolerant(str(cj), max_bytes=50 * 1024 * 1024)
        if isinstance(cd, dict):
            corpus = cd
    records = corpus.get("records", [])
    problems: List[Dict[str, Any]] = []
    review = data.setdefault("review", {})
    if not isinstance(review, dict):
        review = {}
        data["review"] = review

    if findings_file:
        fdata, ferr = read_json_tolerant(findings_file, max_bytes=10 * 1024 * 1024)
        findings = fdata if isinstance(fdata, list) else (
            fdata.get("findings") if isinstance(fdata, dict) else [])
        if ferr:
            problems.append({"issue": "findings_file_parse", "detail": ferr})
        norm_findings = []
        for i, f in enumerate(findings if isinstance(findings, list) else []):
            norm_findings.append(normalize_finding(f, i, records, problems))
        review["findings"] = norm_findings

    if claims_file:
        cdata, cerr = read_json_tolerant(claims_file, max_bytes=10 * 1024 * 1024)
        claims = cdata if isinstance(cdata, list) else (
            cdata.get("claims") if isinstance(cdata, dict) else [])
        if cerr:
            problems.append({"issue": "claims_file_parse", "detail": cerr})
        review["claims"] = claims if isinstance(claims, list) else []

    if summary is not None:
        s = summary
        if s.startswith("@"):
            r = read_text_safe(s[1:], max_bytes=50_000)
            s = r["text"] if not r["error"] else summary
        review["summary"] = s.strip()

    if status in RESULT_STATUSES:
        data["status"] = status

    log = data.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        data["change_log"] = log
    log.append({"ts": _utcnow(), "event": "update",
                "note": "merged; verify_problems=%d" % len(problems)})

    pr = data.setdefault("pending_review", [])
    if not isinstance(pr, list):
        pr = []
        data["pending_review"] = pr
    for p in problems:
        pr.append({"issue": p["issue"], "detail": p.get("detail", ""),
                   "id": p.get("id")})

    meta: Dict[str, Any] = {}
    mj = Path(work_dir) / "meta.json"
    if mj.exists():
        md, _ = read_json_tolerant(str(mj))
        if isinstance(md, dict):
            meta = md
    started = meta.get("started_at")
    runtime = data.setdefault("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
        data["runtime"] = runtime
    if started:
        try:
            runtime["seconds_elapsed"] = round(time.time() - float(started), 2)
        except (TypeError, ValueError):
            pass
    runtime.setdefault("model_requests_used", 0)
    runtime.setdefault("budget", {"requests": 64, "seconds": 900})

    write_json_atomic(data, str(out_json))
    write_text_atomic(_render_report(data), str(Path(output_dir) / "report.md"))
    return data


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------
def validate_result(data: Any, output_dir: str) -> List[str]:
    problems: List[str] = []
    if not isinstance(data, dict):
        return ["result_json_not_object"]
    if not data.get("case_id"):
        problems.append("missing_case_id")
    if data.get("status") not in RESULT_STATUSES:
        problems.append("invalid_status:" + str(data.get("status")))
    review = data.get("review")
    if not isinstance(review, dict):
        problems.append("missing_review_object")
    else:
        findings = review.get("findings")
        if not isinstance(findings, list):
            problems.append("review.findings_not_list")
        else:
            for f in findings:
                fid = f.get("id", "?")
                if not f.get("locator"):
                    problems.append("finding_%s_missing_locator" % fid)
                if not f.get("quote"):
                    problems.append("finding_%s_missing_quote" % fid)
                if f.get("severity") not in SEVERITIES:
                    problems.append("finding_%s_bad_severity" % fid)
                if f.get("type") not in FINDING_TYPES:
                    problems.append("finding_%s_bad_type" % fid)
                if not f.get("suggestion"):
                    problems.append("finding_%s_missing_suggestion" % fid)
                if f.get("quote_match") is False:
                    problems.append("finding_%s_quote_unverified" % fid)
        coverage = review.get("coverage")
        if not isinstance(coverage, dict):
            problems.append("missing_coverage")
        else:
            for d in COVERAGE_DIMS:
                if d not in coverage:
                    problems.append("coverage_missing_" + d)
    for key in ("sources", "uncertainty", "pending_human", "change_log"):
        if key not in data:
            problems.append("missing_" + key)
    return problems


def selfcheck(output_dir: str = DEFAULT_OUTPUT) -> int:
    out_json = Path(output_dir) / "result.json"
    if not out_json.exists():
        print("FAIL result.json not found: %s" % out_json)
        return 1
    data, err = read_json_tolerant(str(out_json), max_bytes=50 * 1024 * 1024)
    if err or not isinstance(data, dict):
        print("FAIL result.json unparseable: %s" % err)
        return 1
    problems = validate_result(data, output_dir)
    if not (Path(output_dir) / "report.md").exists():
        problems.append("report.md_missing")
    if problems:
        print("PROBLEMS (%d):" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    nf = len((data.get("review") or {}).get("findings") or [])
    print("OK: result.json valid, %d findings, status=%s" % (nf, data.get("status")))
    return 0
