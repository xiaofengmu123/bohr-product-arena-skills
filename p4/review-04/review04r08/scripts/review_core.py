# -*- coding: utf-8 -*-
"""review_core.py — 论文逻辑与论证审查 Skill 的共享实现（纯标准库）。

职责：
  1) 输入发现（case.json / task.md / materials），不假设固定文件名；
  2) 容错读取（UTF-8-SIG / UTF-8 / UTF-16 / GBK / latin-1 逐级回退，坏 JSON 不崩溃）；
  3) PDF 文本抽取（优先 pdftotext -layout，失败则记录降级、留给 Agent 用平台 pdf-text-tool）；
  4) 语料切分与段落级定位符（p<页> ¶P<段落>），确定性、无随机性；
  5) 结果骨架、增量合并、自检校验、原子写盘。

设计取舍（区别于"仅输出一份报告"的朴素做法）：
  - 所有函数确定性优先，scaffold 两次运行产出 byte 级一致的 result.json（ts 留空）；
  - 单文件 2MB 上限、单来源段落数上限，超限记录 degradations 而非崩溃。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 常量：状态、类型、严重度、置信度（与 SKILL.md / references 对齐）
# --------------------------------------------------------------------------
RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

FINDING_TYPES = (
    "factual_error",
    "overclaim",
    "citation_unsupported",
    "method_mismatch",
    "statistical_issue",
    "logical_gap",
    "cross_section_inconsistency",
    "academic_norm",
    "suggestion",
)
SEVERITIES = ("critical", "major", "minor", "suggestion")
FINDING_STATUSES = ("confirmed", "probable", "pending_review", "ruled_out")
CONFIDENCES = ("high", "medium", "low")
CLAIM_SPEAKERS = ("author", "cited", "assumption")
CLAIM_VERDICTS = ("supported", "overclaimed", "unsupported", "pending")
CHAIN_VERDICTS = ("sound", "gap", "fallacy", "pending")

COVERAGE_DIMS = (
    "factual_verification",      # 事实核验
    "citation_support",          # 引用支持
    "method_design",             # 方法设计
    "statistical_reporting",     # 统计报告
    "argument_chain",            # 论证链
    "academic_integrity",        # 学术规范
    "cross_section_consistency", # 跨章节一致性（承诺—交付对照）
)

MAX_FILE_BYTES = 2 * 1024 * 1024      # 单文件读取上限
MAX_PARAS_PER_SRC = 4000              # 单来源段落上限
LONG_PARA_CHARS = 4000                # 超长段落按换行再切
TEXT_EXTS = (".txt", ".md", ".tex", ".csv", ".json", ".rst", ".html")
PDF_EXTS = (".pdf",)

SECTION_KEYWORDS = (
    "abstract", "摘要",
    "introduction", "引言", "背景", "background",
    "related work", "相关工作",
    "method", "methods", "materials and methods", "方法", "材料与方法", "实验设计",
    "result", "results", "结果", "实验结果",
    "discussion", "讨论",
    "conclusion", "conclusions", "结论", "总结",
    "limitation", "limitations", "局限", "局限性",
    "references", "参考文献",
    "appendix", "附录", "supplementary",
)


def _reconfigure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# --------------------------------------------------------------------------
# 容错读取
# --------------------------------------------------------------------------
def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    """读取文本文件，逐级回退编码。返回 {path, text, encoding, error, chars, skipped}。"""
    p = Path(path)
    out: Dict[str, Any] = {"path": str(p), "text": "", "encoding": None,
                           "error": None, "chars": 0, "skipped": False}
    try:
        if not p.is_file():
            out["error"] = "file not found"
            return out
        size = p.stat().st_size
        if size > max_bytes:
            out["skipped"] = True
            out["error"] = "size %d > %d bytes, skipped" % (size, max_bytes)
            return out
        raw = p.read_bytes()
    except OSError as e:
        out["error"] = "read error: %s" % e
        return out
    text, encoding, err = _decode_bytes(raw)
    out.update({"text": text, "encoding": encoding, "error": err, "chars": len(text)})
    return out


def _decode_bytes(raw: bytes) -> Tuple[str, Optional[str], Optional[str]]:
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, None
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", "decode fallback used"


def read_json_tolerant(path: str, max_bytes: int = MAX_FILE_BYTES) -> Tuple[Any, Optional[str]]:
    """容错读 JSON：BOM / 坏编码 / 语法错误均返回 (data|None, error)。"""
    r = read_text_safe(path, max_bytes)
    if r["error"] and not r["text"]:
        return None, r["error"]
    text = r["text"].lstrip("\ufeff")
    if not text.strip():
        return None, "empty JSON"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        # 常见尾逗号容错：去掉尾逗号重试一次
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            return json.loads(cleaned), None
        except json.JSONDecodeError as e2:
            return None, "JSON parse error: %s" % e2


def atomic_write_text(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def write_json_atomic(path: str, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------------
# 输入发现
# --------------------------------------------------------------------------
def discover_input(input_dir: str) -> Dict[str, Any]:
    """扫描输入目录，返回 {case_json, task_md, materials, others}（路径按字典序排序保证确定性）。"""
    discovered: Dict[str, Any] = {"case_json": None, "task_md": None, "materials": [], "others": []}
    ip = Path(input_dir)
    if not ip.is_dir():
        return discovered
    files = sorted([f for f in ip.rglob("*") if f.is_file()])
    for f in files:
        name = f.name.lower()
        if name == "case.json" and discovered["case_json"] is None:
            discovered["case_json"] = str(f)
        elif name == "task.md" and discovered["task_md"] is None:
            discovered["task_md"] = str(f)
        elif f.suffix.lower() in TEXT_EXTS + PDF_EXTS:
            discovered["materials"].append(str(f))
        else:
            discovered["others"].append(str(f))
    return discovered


def _contract_keys(case_data: Any) -> Dict[str, Any]:
    """提取 case.json 里 output_contract 的键（缺值回填用）。返回 {key: contract_value}。"""
    if not isinstance(case_data, dict):
        return {}
    c = case_data.get("output_contract")
    out: Dict[str, Any] = {}
    if isinstance(c, dict):
        out = dict(c)
    elif isinstance(c, list):
        for item in c:
            if isinstance(item, str):
                out[item] = None
            elif isinstance(item, dict) and item.get("key") or item.get("name"):
                out[item.get("key") or item.get("name")] = None
    return out


def _derive_task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None, "parse_error": None}
    if discovered.get("case_json"):
        data, err = read_json_tolerant(discovered["case_json"])
        if err:
            meta["parse_error"] = err
        if isinstance(data, dict):
            meta["task_title"] = data.get("title") or data.get("task_title")
            meta["challenge_id"] = (data.get("challenge_id") or data.get("challengeId")
                                    or data.get("id"))
    if discovered.get("task_md"):
        r = read_text_safe(discovered["task_md"], max_bytes=200_000)
        for line in r["text"].splitlines()[:60]:
            ls = line.strip()
            if ls.startswith("#") and ls.lstrip("#").strip():
                meta.setdefault("task_title", ls.lstrip("#").strip())
                break
    return meta


def _derive_case_id(discovered: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, err = read_json_tolerant(discovered["case_json"])
        if not err and isinstance(data, dict):
            for key in ("case_id", "id", "caseId", "caseid"):
                v = data.get(key)
                if isinstance(v, str) and v:
                    return v
        return Path(discovered["case_json"]).stem
    if discovered.get("task_md"):
        return Path(discovered["task_md"]).stem
    return "unknown_case"


# --------------------------------------------------------------------------
# PDF 抽取
# --------------------------------------------------------------------------
def extract_pdf(pdf_path: str, work_dir: str) -> Tuple[str, int, Optional[str]]:
    """用 pdftotext -layout 抽取。返回 (text, n_pages, degradation_note)。失败返回空文本并记录原因。"""
    src = Path(pdf_path)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("pdftotext")
    if not exe:
        return "", 0, "pdftotext not found; fill corpus via platform pdf-text-tool"
    out_txt = work / (src.stem + ".pdftotext.txt")
    err_file = work / (src.stem + ".pdftotext.err")
    try:
        with open(err_file, "wb") as ferr:
            proc = subprocess.run(
                [exe, "-layout", str(src), str(out_txt)],
                stdout=subprocess.DEVNULL, stderr=ferr, timeout=120,
            )
        if proc.returncode != 0:
            err = ""
            try:
                err = err_file.read_text(encoding="utf-8", errors="replace")[:300]
            except OSError:
                pass
            return "", 0, "pdftotext exit %d: %s" % (proc.returncode, err.strip())
        text = out_txt.read_text(encoding="utf-8", errors="replace")
        n_pages = max(1, text.count("\x0c") + 1)
        return text, n_pages, None
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", 0, "pdftotext error: %s" % e


# --------------------------------------------------------------------------
# 语料切分与定位符
# --------------------------------------------------------------------------
def locator(page: Optional[int], para: int) -> str:
    """生成段落级定位符：p3 ¶P0012（文本来源无页码时省略 p 部分）。"""
    p_part = "p%d " % page if page is not None else ""
    return "%s¶P%04d" % (p_part, para)


def _looks_like_heading(line: str) -> Optional[str]:
    s = line.strip()
    if not s or len(s) > 90:
        return None
    m = re.match(r"^#{1,6}\s+(.+)$", s)
    if m:
        return m.group(1).strip()
    if re.match(r"^\d{1,2}(\.\d{1,2})*[\.、)）]?\s+\S+", s) and not s.endswith(
            (".", "。", "!", "？", "?", "！", ":", "：", ";", "；")):
        return s
    low = s.lower()
    for kw in SECTION_KEYWORDS:
        if low == kw or low.startswith(kw + " ") or low.startswith(kw + ":"):
            return s
    return None


def _split_blocks(text: str) -> List[str]:
    blocks = re.split(r"\n[ \t]*\n", text)
    return [b for b in (x.strip() for x in blocks) if b]


def _finalize_block(block: str) -> List[str]:
    """超长段落按换行边界再切，保持逐字（不丢换行）。"""
    if len(block) <= LONG_PARA_CHARS:
        return [block]
    lines = block.splitlines()
    chunks: List[str] = []
    cur: List[str] = []
    n = 0
    for ln in lines:
        cur.append(ln)
        n += len(ln) + 1
        if n >= LONG_PARA_CHARS:
            chunks.append("\n".join(cur))
            cur, n = [], 0
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def build_corpus(discovered: Dict[str, Any], work_dir: str) -> Dict[str, Any]:
    """抽取全部材料为带定位符的段落记录。返回 {records, sources, degradation_notes}。"""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    degradations: List[str] = []

    # task.md 先进语料，便于 Agent 在单一位置读全题意
    task_files = [discovered.get("task_md")] if discovered.get("task_md") else []
    material_files = list(discovered.get("materials", []))
    all_files = [(f, "task" if f == discovered.get("task_md") else "material")
                 for f in (task_files + material_files) if f]

    for src_path, kind in all_files:
        src = Path(src_path)
        lower = src.suffix.lower()
        source_rec = {"path": src_path, "kind": kind, "n_pages": 0,
                      "chars": 0, "error": None}
        text = ""
        n_pages = 0
        err = None
        if lower in PDF_EXTS:
            text, n_pages, err = extract_pdf(src_path, work_dir)
            if err:
                degradations.append("PDF 抽取失败 %s: %s" % (src_path, err))
        else:
            r = read_text_safe(src_path)
            text, err = r["text"], r["error"]
            if r["skipped"]:
                degradations.append("文件超限跳过 %s: %s" % (src_path, r["error"]))
        source_rec.update({"n_pages": n_pages, "chars": len(text), "error": err})
        sources.append(source_rec)
        if not text:
            continue

        # 按页切分（PDF 以 \x0c 分页；文本整体作单页）
        pages: List[Tuple[Optional[int], str]] = []
        if lower in PDF_EXTS:
            page_texts = text.split("\x0c")
            for i, pt in enumerate(page_texts, start=1):
                if pt.strip():
                    pages.append((i, pt))
        else:
            pages.append((None, text))

        section = "front matter"
        para_idx = 0
        for page, page_text in pages:
            blocks = _split_blocks(page_text)
            for block in blocks:
                if para_idx >= MAX_PARAS_PER_SRC:
                    degradations.append("段落超限（>%d）截断 %s" % (MAX_PARAS_PER_SRC, src_path))
                    break
                heading = _looks_like_heading(block)
                is_heading = heading is not None and "\n" not in block.strip()
                if is_heading:
                    section = heading
                para_idx += 1
                records.append({
                    "src": src_path,
                    "kind": "heading" if is_heading else kind,
                    "page": page,
                    "para": para_idx,
                    "section": section,
                    "text": block,
                    "locator": locator(page, para_idx),
                })
                # 超长段落拆分：同一段继续编号，保持逐字
                if not is_heading and len(block) > LONG_PARA_CHARS:
                    for sub in _finalize_block(block)[1:]:
                        if para_idx >= MAX_PARAS_PER_SRC:
                            break
                        para_idx += 1
                        records.append({
                            "src": src_path, "kind": kind, "page": page, "para": para_idx,
                            "section": section, "text": sub, "locator": locator(page, para_idx),
                        })
    return {"records": records, "sources": sources, "degradations": degradations}


def write_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                    degradations: List[str], path: str) -> None:
    lines = ["# 语料库（逐段审查，定位符可直接引用）", ""]
    lines.append("来源 %d 个，段落 %d 个。定位符格式：`p<页> ¶P<段落>`；文本来源无页码时省略 p。"
                 % (len(sources), len(records)))
    lines.append("引用原文必须逐字复制，不得改写；`¶P` 编号在同一来源内全局递增。")
    if degradations:
        lines.append("")
        lines.append("## 降级记录")
        for d in degradations:
            lines.append("- %s" % d)
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_src.setdefault(rec["src"], []).append(rec)
    for src in sources:
        recs = by_src.get(src["path"], [])
        lines.append("")
        lines.append("## 来源: %s" % src["path"])
        lines.append("- kind=%s n_pages=%s chars=%s error=%s" % (
            src["kind"], src["n_pages"], src["chars"], src["error"]))
        lines.append("")
        for rec in recs:
            lines.append("### %s [%s] %s" % (rec["locator"], rec["section"], rec["kind"]))
            lines.append(rec["text"])
            lines.append("")
    atomic_write_text(path, "\n".join(lines))


# --------------------------------------------------------------------------
# 结果骨架
# --------------------------------------------------------------------------
def default_coverage() -> Dict[str, Any]:
    return {d: {"status": "skipped", "checked": 0, "note": ""} for d in COVERAGE_DIMS}


def new_skeleton(case_id: str, task_meta: Dict[str, Any], contract: Dict[str, Any],
                 sources: List[Dict[str, Any]], section_map: List[Dict[str, Any]],
                 output_dir: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "case_id": case_id,
        "status": "partial",
        "task": {"challenge_id": task_meta.get("challenge_id"),
                 "task_title": task_meta.get("task_title")},
        "artifacts": {"result_json": "%s/result.json" % output_dir,
                      "report_md": "%s/report.md" % output_dir},
        "review": {
            "summary": "",
            "findings": [],
            "claims": [],
            "commitments": [],
            "argument_chains": [],
            "coverage": default_coverage(),
            "section_map": section_map,
        },
        "sources": sources,
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"seconds_elapsed": 0.0, "model_requests_used": 0,
                    "budget": {"requests": 64, "seconds": 900}},
    }
    contract_keys: List[str] = []
    for k, v in contract.items():
        contract_keys.append(k)
        if k not in result:
            result[k] = [] if isinstance(v, (list, dict)) else None
    result["__contract_keys__"] = contract_keys
    result["change_log"].append({"ts": "", "event": "skeleton_written",
                                 "note": "scaffold 生成合法 partial 骨架"})
    return result


def build_section_map(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 (source, section) 归组，避免多文件时 ¶P 编号歧义。"""
    m: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        key = "%s|%s" % (rec["src"], rec["section"] or "unnamed")
        entry = m.setdefault(key, {
            "source": rec["src"],
            "section": rec["section"] or "unnamed",
            "paras": [],
            "pages": [],
        })
        para_id = "P%04d" % rec["para"]
        if para_id not in entry["paras"]:
            entry["paras"].append(para_id)
        if rec["page"] is not None and rec["page"] not in entry["pages"]:
            entry["pages"].append(rec["page"])
    return list(m.values())


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def validate_result(result: Any) -> Tuple[List[str], List[str]]:
    """返回 (errors, warnings)。errors 为硬失败（缺必需字段），warnings 为可改进项。"""
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(result, dict):
        return ["result.json 不是 JSON 对象"], []
    if not result.get("case_id"):
        errors.append("缺少 case_id")
    if result.get("status") not in RESULT_STATUSES:
        errors.append("status 必须是 %s 之一" % "/".join(RESULT_STATUSES))
    if not isinstance(result.get("sources"), list):
        errors.append("sources 必须是数组")
    for key in ("uncertainty", "pending_review", "pending_human", "limitations",
                "errors", "degradations", "change_log"):
        if not isinstance(result.get(key), list):
            errors.append("%s 必须是数组" % key)
    runtime = result.get("runtime")
    if not isinstance(runtime, dict) or not isinstance(runtime.get("budget"), dict):
        errors.append("runtime.budget 缺失")
    review = result.get("review")
    if not isinstance(review, dict):
        errors.append("review 缺失")
    else:
        if not isinstance(review.get("summary"), str):
            warnings.append("review.summary 建议补充")
        for field in ("findings", "claims", "commitments", "argument_chains"):
            if not isinstance(review.get(field), list):
                errors.append("review.%s 必须是数组" % field)
        cov = review.get("coverage")
        if isinstance(cov, dict):
            for d in COVERAGE_DIMS:
                if d not in cov:
                    errors.append("coverage 缺维度 %s" % d)
        else:
            errors.append("coverage 缺失")
        if not isinstance(review.get("section_map"), list):
            warnings.append("review.section_map 建议为数组")
    # output_contract 键回填校验
    for k in result.get("__contract_keys__", []):
        if k not in result:
            errors.append("output_contract 键缺失: %s" % k)
    # 逐条 findings 字段检查（警告级，不阻断）
    findings = result.get("review", {}).get("findings", [])
    need = ("id", "type", "severity", "locator", "source", "quote", "claim",
            "evidence", "reasoning", "suggestion", "status", "confidence")
    for i, f in enumerate(findings):
        if isinstance(f, dict):
            missing = [k for k in need if not f.get(k)]
            if missing:
                warnings.append("finding #%d 缺字段 %s" % (i + 1, ",".join(missing)))
            if f.get("severity") not in SEVERITIES:
                warnings.append("finding #%d 严重度非法: %s" % (i + 1, f.get("severity")))
    return errors, warnings


# --------------------------------------------------------------------------
# 增量合并
# --------------------------------------------------------------------------
def _load_array_file(path: Optional[str]) -> Optional[List[Any]]:
    if not path:
        return None
    data, err = read_json_tolerant(path)
    if err:
        raise ValueError("读取 %s 失败: %s" % (path, err))
    if not isinstance(data, list):
        raise ValueError("%s 内容不是 JSON 数组" % path)
    return data


def merge_update(result: Dict[str, Any], *, findings: Optional[List[Any]] = None,
                 claims: Optional[List[Any]] = None,
                 commitments: Optional[List[Any]] = None,
                 argument_chains: Optional[List[Any]] = None,
                 coverage: Optional[Dict[str, Any]] = None,
                 summary: Optional[str] = None,
                 status: Optional[str] = None,
                 note: str = "") -> Dict[str, Any]:
    review = result.setdefault("review", {})
    if findings is not None:
        review["findings"] = findings
    if claims is not None:
        review["claims"] = claims
    if commitments is not None:
        review["commitments"] = commitments
    if argument_chains is not None:
        review["argument_chains"] = argument_chains
    if coverage is not None:
        merged = default_coverage()
        merged.update(coverage)
        review["coverage"] = merged
    if summary is not None:
        review["summary"] = summary
    if status is not None:
        if status not in RESULT_STATUSES:
            raise ValueError("status 非法: %s" % status)
        result["status"] = status
    result.setdefault("change_log", []).append(
        {"ts": "", "event": "incremental_update",
         "note": note or "findings/claims/coverage 增量合并"})
    return result


def _sync_report_status(report_path: str, case_id: str, status: str) -> None:
    p = Path(report_path)
    if not p.is_file():
        return
    text = p.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"^- 状态:.*$", "- 状态: %s" % status, text, count=1, flags=re.M)
    atomic_write_text(report_path, text)
