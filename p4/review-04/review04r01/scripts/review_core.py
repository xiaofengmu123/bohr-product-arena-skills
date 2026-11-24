# -*- coding: utf-8 -*-
"""review_core.py — 证据溯源论文审查 Skill 的核心库（纯标准库）。

职责：
  - 输入发现：扫描 /input，识别 case.json / task.md / materials，不假设固定文件名；
  - 容错读取：文本按 utf-8-sig → utf-8 → gb18030 → latin-1 降级，坏 JSON 记录错误不崩溃；
  - PDF 抽取：优先 pdftotext，失败记录 error/degradation，供 Agent 改用平台 pdf-text-tool；
  - 语料切分：按页/段落切分，生成 locator（p页 ¶P段落）与段内 char_span 偏移；
  - 骨架与渲染：result.json 骨架、report.md 由 result.json 确定性渲染（二者不会漂移）；
  - 契约校验：status / finding / claim / commitment / evidence_map / coverage 全部校验；
  - 证据映射：evidence_map 双向索引自动重建（by_finding / by_claim），保证可复核；
  - 原子写盘：临时文件 + os.replace，失败时降级为最小合法产物。

本文件只依赖 Python 标准库，不引入第三方包。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 常量
RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

FINDING_TYPES = (
    "factual_error",        # 事实错误（数值/名称/单位/年份等与可核验事实不符）
    "overclaim",            # 结论强度超出证据支持范围
    "unsupported_citation", # 引用文献不支持所引观点
    "missing_evidence",     # 主张缺少必要证据
    "method_mismatch",      # 研究方法回答不了研究问题
    "statistical_error",    # 统计口径/报告与结论方向或强度不一致
    "argument_gap",         # 论证链断裂/跳跃/隐含假设未说明
    "causality_conflation", # 相关性与因果性混用
    "cross_section_gap",    # 跨章节承诺—交付断裂
    "consistency_issue",    # 摘要/正文/图表前后不一致
    "norm_violation",       # 学术规范问题（非格式检查）
    "suggestion",           # 建议性意见（明确非缺陷，不误判为缺陷）
)

SEVERITIES = ("critical", "major", "minor", "suggestion")

FINDING_STATUSES = ("confirmed", "probable", "pending_review", "ruled_out")

CONFIDENCES = ("high", "medium", "low")

SPEAKERS = ("author", "cited", "assumption", "mixed")

CLAIM_VERDICTS = ("supported", "overclaimed", "unsupported", "pending", "not_applicable")

EVIDENCE_KINDS = (
    "direct_quote",             # 逐字摘录
    "paraphrase",               # 忠实转述（须与原文可对）
    "data_point",               # 数值/指标
    "citation",                 # 引用文献表述
    "method_statement",         # 方法描述
    "statistical_report",       # 统计量/显著性/效应量口径
    "missing_evidence",         # 应存在但缺失的证据（quote 可为空）
    "cross_section_commitment", # 引言承诺 / 结果交付
    "figure_table",             # 图表与正文对应
)

EVIDENCE_STATUSES = ("verified", "needs_external_check", "incomplete", "conflict")

COMMITMENT_VERDICTS = ("delivered", "partially_delivered", "not_delivered", "pending")

DIMENSIONS = (
    "factual_verification",   # 事实核验
    "citation_support",       # 引用支持
    "method_design",          # 方法设计
    "statistical_report",     # 统计报告
    "argument_chain",         # 论证链
    "academic_norm",          # 学术规范
)

MAX_MATERIAL_BYTES = 2 * 1024 * 1024   # 单材料 2MB 上限
MAX_TASK_BYTES = 200_000               # 题面读取上限
MAX_CORPUS_CHARS = 1_200_000           # 单来源语料字符上限（防超长，截断须记录）
MAX_PARAGRAPH_CHARS = 4000             # 单段落字符上限（超长拆分）

MATERIAL_EXTS = {
    ".pdf", ".txt", ".md", ".tex", ".csv", ".rst", ".json", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".docx", ".doc", ".pptx", ".ppt", ".odt",
}
MATERIAL_DIRS = {"materials", "material", "data", "docs", "documents", "papers", "files", "input"}
TEXT_EXTS = {".txt", ".md", ".tex", ".csv", ".rst", ".json", ".yaml", ".yml", ".xml", ".html", ".htm"}

SECTION_PATTERNS = (
    ("abstract", re.compile(r"^(abstract|摘要)\b", re.IGNORECASE)),
    ("introduction", re.compile(r"^(introduction|引言|绪论|背景|问题提出)")),
    ("methods", re.compile(r"^(methods?|materials?\s*(and|&)\s*methods?|方法|材料与方法|实验|实验设计|研究设计)")),
    ("results", re.compile(r"^(results?|结果)")),
    ("discussion", re.compile(r"^(discussion|讨论)")),
    ("conclusion", re.compile(r"^(conclusion|结论)")),
    ("references", re.compile(r"^(references?|bibliography|参考文献)")),
)


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _configure_stdio() -> None:
    for _s in (sys.stdout, sys.stderr):
        try:
            if hasattr(_s, "reconfigure"):
                _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------- 容错读取
def read_text_safe(path, max_bytes: int = MAX_MATERIAL_BYTES) -> Dict[str, Any]:
    """读取文本并容错降级。返回 {text, error, truncated, chars, bytes}。"""
    out: Dict[str, Any] = {"text": "", "error": None, "truncated": False, "chars": 0, "bytes": 0}
    p = Path(path)
    if not p.exists():
        out["error"] = "missing"
        return out
    try:
        size = p.stat().st_size
    except OSError as e:
        out["error"] = "stat_failed: %s" % e
        return out
    out["bytes"] = size
    truncated = size > max_bytes
    out["truncated"] = truncated
    try:
        with open(p, "rb") as f:
            raw = f.read(max_bytes if truncated else -1)
    except OSError as e:
        out["error"] = "read_failed: %s" % e
        return out

    text = None
    if truncated:
        # 截断尾部可能切断多字节字符，用 replace 保留有效前缀
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = raw.decode(enc, errors="replace")
                break
            except Exception:
                continue
        if text is None:
            text = raw.decode("latin-1", errors="replace")
    else:
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("latin-1", errors="replace")
            out["error"] = "encoding_fallback"
    out["text"] = text
    out["chars"] = len(text)
    return out


def read_json_tolerant(path) -> Tuple[Optional[Any], Optional[str]]:
    r = read_text_safe(path, max_bytes=4 * 1024 * 1024)
    if r["error"] in ("missing", "read_failed", "stat_failed"):
        return None, r["error"]
    try:
        return json.loads(r["text"]), None
    except Exception as e:
        return None, "json_parse_error: %s" % e


# ---------------------------------------------------------------- 原子写盘
def atomic_write_text(path, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, p)


def atomic_write_json(path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------- 输入发现
def discover_input(input_dir: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "case_json": None, "task_md": None,
        "materials": [], "other": [], "skipped": [],
    }
    if not input_dir.exists():
        return out
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(input_dir).as_posix()
        except ValueError:
            rel = p.name
        name = p.name.lower()
        if name == "case.json":
            out["case_json"] = str(p)
            continue
        if name == "task.md":
            out["task_md"] = str(p)
            continue
        if name in (".ds_store", "github_url.txt", "readme.md", "readme.txt"):
            continue
        parts = rel.split("/")
        in_mat_dir = any(d in MATERIAL_DIRS for d in parts[:-1])
        ext = p.suffix.lower()
        if in_mat_dir or ext in MATERIAL_EXTS:
            try:
                size = p.stat().st_size
            except OSError:
                size = MAX_MATERIAL_BYTES + 1
            if size > MAX_MATERIAL_BYTES:
                out["skipped"].append(str(p))
            else:
                out["materials"].append(str(p))
        else:
            out["other"].append(str(p))
    return out


def load_task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None}
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            meta["challenge_id"] = data.get("challenge_id") or data.get("challengeId") or data.get("id")
            meta["task_title"] = data.get("title") or data.get("task_title")
    if not meta["task_title"] and discovered.get("task_md"):
        r = read_text_safe(discovered["task_md"], max_bytes=MAX_TASK_BYTES)
        for line in r["text"].splitlines()[:60]:
            ls = line.strip()
            if ls.startswith("#") and ls.lstrip("#").strip():
                meta["task_title"] = ls.lstrip("#").strip()
                break
    return meta


def derive_case_id(discovered: Dict[str, Any], meta: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            for key in ("id", "case_id", "caseId", "caseid"):
                v = data.get(key)
                if isinstance(v, str) and v:
                    return v
        return Path(discovered["case_json"]).stem
    return "unknown_case"


def task_file_issues(discovered: Dict[str, Any]) -> List[str]:
    """记录 case.json / task.md 的读取或解析问题（降级不崩溃）。"""
    issues: List[str] = []
    if discovered.get("case_json"):
        data, err = read_json_tolerant(discovered["case_json"])
        if err:
            issues.append("case_json 解析失败（%s），case_id/task 元数据可能缺失: %s" % (
                err, discovered["case_json"]))
    if discovered.get("task_md"):
        r = read_text_safe(discovered["task_md"], max_bytes=MAX_TASK_BYTES)
        if r["error"]:
            issues.append("task.md 读取失败（%s）: %s" % (r["error"], discovered["task_md"]))
    return issues


def contract_extra(discovered: Dict[str, Any]) -> Dict[str, Any]:
    """原样回填 case.json 中 output_contract 的全部键（缺值不补、不删键）。"""
    if not discovered.get("case_json"):
        return {}
    data, _ = read_json_tolerant(discovered["case_json"])
    if isinstance(data, dict):
        c = data.get("output_contract")
        if isinstance(c, dict):
            return dict(c)
    return {}


# ---------------------------------------------------------------- PDF 抽取
def extract_pdf_text(pdf_path, work_dir: Path) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"text": "", "pages": 0, "chars": 0, "error": None, "tool": None}
    pdf = Path(pdf_path)
    if not pdf.exists():
        rec["error"] = "missing"
        return rec
    tool = shutil.which("pdftotext")
    if not tool:
        rec["error"] = "pdftotext_missing"
        return rec
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp = work_dir / ("_pdftotext_%s.txt" % pdf.stem)
    try:
        cp = subprocess.run(
            [tool, "-enc", "UTF-8", str(pdf), str(tmp)],
            capture_output=True, timeout=180,
        )
        if cp.returncode == 0 and tmp.exists():
            rec["text"] = tmp.read_text(encoding="utf-8", errors="replace")
            rec["tool"] = "pdftotext"
        else:
            rec["error"] = "pdftotext_failed rc=%s" % cp.returncode
    except Exception as e:
        rec["error"] = "pdftotext_exception: %s" % e
    pages = re.split(r"\f+", rec["text"]) if rec["text"] else []
    pages = [pg for pg in pages if pg.strip()]
    rec["pages"] = len(pages)
    rec["chars"] = len(rec["text"])
    return rec


# ---------------------------------------------------------------- 段落切分
def _clean_heading(s: str) -> str:
    s = s.strip()
    return re.sub(r"^[\d\.\s\uFF0E\u3000\u3001\uFF08\uFF09()\[\]]+", "", s).strip()


def guess_section(text: str) -> str:
    if not text:
        return ""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    head = _clean_heading(first)
    if not head:
        return ""
    for name, pat in SECTION_PATTERNS:
        if pat.match(head):
            return name
    # 短标题行启发（避免把正文首句当标题）
    if len(head) <= 60 and head and not head[-1:] in ".。;；:：":
        if re.search(r"[\u4e00-\u9fff]|^[A-Za-z][A-Za-z\s\-:]{2,}$", head):
            # 仅当整段就一行且行短时才视为标题
            lines = [ln for ln in text.strip().splitlines() if ln.strip()]
            if len(lines) == 1 and len(head) <= 60:
                return head.lower()[:48]
    return ""


def _split_paras(text: str) -> List[str]:
    t = (text or "").strip("\r\n")
    if not t:
        return []
    paras = re.split(r"\n\s*\n", t)
    if len(paras) == 1:
        paras = [ln for ln in re.split(r"\r?\n", t) if ln.strip()]
    out: List[str] = []
    for p in paras:
        p = p.strip()
        if not p:
            continue
        while len(p) > MAX_PARAGRAPH_CHARS:
            out.append(p[:MAX_PARAGRAPH_CHARS])
            p = p[MAX_PARAGRAPH_CHARS:]
        out.append(p)
    return out


def segment_source(path: str, kind: str, text: str, pages: Optional[List[str]]) -> List[Dict[str, Any]]:
    chunks: List[Tuple[Optional[int], str]] = []
    if pages:
        for page_idx, page_text in enumerate(pages, start=1):
            for para in _split_paras(page_text):
                chunks.append((page_idx, para))
    else:
        for para in _split_paras(text):
            chunks.append((None, para))
    records: List[Dict[str, Any]] = []
    current_section = "front"
    pos = 0
    for i, (page, para) in enumerate(chunks, start=1):
        sec = guess_section(para)
        if sec:
            current_section = sec
        start = pos
        end = pos + len(para)
        pos = end + 1  # 段落间隔近似记 1 字符，char_span 仅供段内检索
        records.append({
            "id": "P%04d" % i,
            "src": path,
            "kind": kind,
            "page": page,
            "section": current_section,
            "text": para,
            "char_start": start,
            "char_end": end,
        })
    return records


def locator(rec: Dict[str, Any]) -> str:
    if rec.get("page") is not None:
        return "p%d \u00b6%s" % (rec["page"], rec["id"])
    return "\u00b6%s" % rec["id"]


def _records_for_text(path: str, kind: str, text: str, pages: Optional[List[str]]):
    records = segment_source(path, kind, text, pages)
    total = 0
    kept: List[Dict[str, Any]] = []
    truncated = False
    for rec in records:
        total += len(rec["text"])
        if total > MAX_CORPUS_CHARS:
            truncated = True
            break
        kept.append(rec)
    return kept, truncated


def build_sources_and_records(discovered: Dict[str, Any], work_dir: Path):
    sources: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    for rel in (discovered.get("case_json"), discovered.get("task_md")):
        if rel:
            r = read_text_safe(rel, max_bytes=MAX_TASK_BYTES)
            sources.append({
                "path": rel, "kind": "task", "n_pages": 0,
                "chars": r["chars"], "bytes": r["bytes"],
                "error": r["error"], "truncated": r["truncated"], "tool": None,
            })
    for rel in discovered.get("materials", []):
        ext = Path(rel).suffix.lower()
        if ext == ".pdf":
            ex = extract_pdf_text(rel, work_dir)
            src = {
                "path": rel, "kind": "material", "n_pages": ex["pages"],
                "chars": ex["chars"],
                "bytes": Path(rel).stat().st_size if Path(rel).exists() else 0,
                "error": ex["error"], "truncated": False, "tool": ex["tool"],
            }
            if ex["text"]:
                pages = re.split(r"\f+", ex["text"])
                recs, truncated = _records_for_text(rel, "material", ex["text"], pages)
                src["truncated"] = truncated
                records.extend(recs)
            sources.append(src)
        elif ext in TEXT_EXTS:
            r = read_text_safe(rel)
            src = {
                "path": rel, "kind": "material", "n_pages": 0,
                "chars": r["chars"], "bytes": r["bytes"],
                "error": r["error"], "truncated": r["truncated"], "tool": None,
            }
            if r["text"]:
                recs, truncated = _records_for_text(rel, "material", r["text"], None)
                src["truncated"] = src.get("truncated") or truncated
                records.extend(recs)
            sources.append(src)
        else:
            sources.append({
                "path": rel, "kind": "material", "n_pages": 0, "chars": 0,
                "bytes": Path(rel).stat().st_size if Path(rel).exists() else 0,
                "error": "unsupported_format", "truncated": False, "tool": None,
            })
    return sources, records


# ---------------------------------------------------------------- 语料落盘
def write_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]], path) -> None:
    lines = [
        "# 语料库（逐段审查；定位符可直接引用）",
        "",
        "来源 %d 个，段落 %d 个。" % (len(sources), len(records)),
        "定位符格式：`p<页> ¶P<段落>`；无页码文本省略 `p` 部分。`chars` 为段内近似偏移，供段内检索。",
        "引用原文必须逐字复制；evidence_map 的 quote 必须与本语料逐字一致，禁止改写。",
        "",
    ]
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_src.setdefault(rec["src"], []).append(rec)
    for src in sources:
        key = src["path"]
        recs = by_src.get(key, [])
        lines.append("")
        lines.append("## 来源: %s" % key)
        lines.append("- kind=%s n_pages=%s chars=%s error=%s truncated=%s tool=%s" % (
            src.get("kind"), src.get("n_pages"), src.get("chars"),
            src.get("error"), src.get("truncated"), src.get("tool")))
        lines.append("")
        for rec in recs:
            lines.append("### %s [%s] (chars %d-%d)" % (
                locator(rec), rec["section"], rec["char_start"], rec["char_end"]))
            lines.append(rec["text"])
            lines.append("")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- 骨架
def build_section_map(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    m: Dict[str, Dict[str, Any]] = {}
    for r in records:
        sec = r.get("section") or "body"
        e = m.setdefault(sec, {"section": sec, "paras": [], "pages": []})
        e["paras"].append(r["id"])
        if r.get("page") is not None and r["page"] not in e["pages"]:
            e["pages"].append(r["page"])
    out = []
    for k in sorted(m):
        e = m[k]
        e["pages"] = sorted(e["pages"])
        out.append(e)
    return out


def empty_evidence_map() -> Dict[str, Any]:
    return {"items": [], "by_finding": {}, "by_claim": {}, "conflicts": []}


def skeleton_result(case_id: str, task_meta: Dict[str, Any], contract: Dict[str, Any],
                    sources: List[Dict[str, Any]], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    t0 = time.time()
    dims = {}
    for d in DIMENSIONS:
        dims[d] = {"status": "pending" if records else "skipped", "checked": 0, "note": ""}
    review = {
        "summary": "",
        "findings": [],
        "claims": [],
        "commitments": [],
        "evidence_map": empty_evidence_map(),
        "coverage": dims,
        "section_map": build_section_map(records),
    }
    obj: Dict[str, Any] = {
        "case_id": case_id,
        "status": "partial" if records else "insufficient_evidence",
        "task": {
            "challenge_id": task_meta.get("challenge_id"),
            "task_title": task_meta.get("task_title"),
        },
        "review": review,
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "pending_review": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {
            "seconds_elapsed": 0.0,
            "model_requests_used": 0,
            "budget": {"requests": 64, "seconds": 900},
        },
    }
    # 原样回填 output_contract 全部键（缺值不补、不改名、不删未知键）
    for k, v in (contract or {}).items():
        if k not in obj:
            obj[k] = v
    for s in sources:
        if s.get("error"):
            obj["degradations"].append("source_error %s: %s" % (s["path"], s["error"]))
        if s.get("truncated"):
            obj["limitations"].append("材料超长截断（>%d 字符），未完成该来源全文审查: %s" % (MAX_CORPUS_CHARS, s["path"]))
    if not records:
        obj["limitations"].append("未发现可审查材料文本，status 设为 insufficient_evidence")
    obj["change_log"].append({
        "ts": iso_now(),
        "event": "skeleton_written",
        "note": "scaffold 落盘骨架，status=%s，sources=%d，paragraphs=%d" % (
            obj["status"], len(sources), len(records)),
    })
    obj["runtime"]["seconds_elapsed"] = round(time.time() - t0, 2)
    return obj


# ---------------------------------------------------------------- 证据索引
def rebuild_evidence_indexes(review: Dict[str, Any]) -> Dict[str, Any]:
    """由 items 重建 by_finding / by_claim，保证双向引用始终一致、可复核。"""
    findings = review.get("findings") or []
    claims = review.get("claims") or []
    fids = {f.get("id") for f in findings if isinstance(f, dict) and f.get("id")}
    cids = {c.get("id") for c in claims if isinstance(c, dict) and c.get("id")}
    em = review.setdefault("evidence_map", {})
    if not isinstance(em, dict):
        em = {"items": [], "by_finding": {}, "by_claim": {}, "conflicts": []}
        review["evidence_map"] = em
    em.setdefault("items", [])
    em.setdefault("conflicts", [])
    by_finding: Dict[str, List[str]] = {}
    by_claim: Dict[str, List[str]] = {}
    for e in em.get("items", []):
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        for t in (e.get("supports") or []):
            if t in fids:
                by_finding.setdefault(t, []).append(eid)
            elif t in cids:
                by_claim.setdefault(t, []).append(eid)
        for t in (e.get("contradicts") or []):
            if t in fids:
                by_finding.setdefault(t, []).append(eid)
            elif t in cids:
                by_claim.setdefault(t, []).append(eid)
    for d in by_finding:
        by_finding[d] = sorted(set(by_finding[d]))
    for d in by_claim:
        by_claim[d] = sorted(set(by_claim[d]))
    em["by_finding"] = by_finding
    em["by_claim"] = by_claim
    return em


# ---------------------------------------------------------------- 校验
def _require(issues: List[str], obj: Dict[str, Any], label: str, *keys: str) -> None:
    for k in keys:
        if k not in obj or obj.get(k) in (None, ""):
            issues.append("%s 缺少字段 %s" % (label, k))


def validate_finding(f: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not isinstance(f, dict):
        return ["finding 非对象"]
    fid = f.get("id", "?")
    _require(issues, f, "finding %s" % fid, "id", "type", "severity", "locator", "quote",
             "claim", "evidence", "reasoning", "suggestion", "status", "confidence")
    if f.get("type") not in FINDING_TYPES:
        issues.append("finding %s type 非法: %r" % (fid, f.get("type")))
    if f.get("severity") not in SEVERITIES:
        issues.append("finding %s severity 非法: %r" % (fid, f.get("severity")))
    if f.get("status") not in FINDING_STATUSES:
        issues.append("finding %s status 非法: %r" % (fid, f.get("status")))
    if f.get("confidence") not in CONFIDENCES:
        issues.append("finding %s confidence 非法: %r" % (fid, f.get("confidence")))
    return issues


def validate_claim(c: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not isinstance(c, dict):
        return ["claim 非对象"]
    cid = c.get("id", "?")
    _require(issues, c, "claim %s" % cid, "id", "text", "speaker", "locator", "verdict")
    if c.get("speaker") not in SPEAKERS:
        issues.append("claim %s speaker 非法: %r" % (cid, c.get("speaker")))
    if c.get("verdict") not in CLAIM_VERDICTS:
        issues.append("claim %s verdict 非法: %r" % (cid, c.get("verdict")))
    return issues


def validate_commitment(c: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not isinstance(c, dict):
        return ["commitment 非对象"]
    cid = c.get("id", "?")
    _require(issues, c, "commitment %s" % cid, "id", "promise", "locator", "verdict")
    if c.get("verdict") not in COMMITMENT_VERDICTS:
        issues.append("commitment %s verdict 非法: %r" % (cid, c.get("verdict")))
    return issues


def validate_evidence_item(e: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not isinstance(e, dict):
        return ["evidence item 非对象"]
    eid = e.get("id", "?")
    _require(issues, e, "evidence %s" % eid, "id", "source", "locator", "kind", "status")
    if e.get("kind") not in EVIDENCE_KINDS:
        issues.append("evidence %s kind 非法: %r" % (eid, e.get("kind")))
    if e.get("status") not in EVIDENCE_STATUSES:
        issues.append("evidence %s status 非法: %r" % (eid, e.get("status")))
    if e.get("kind") != "missing_evidence":
        _require(issues, e, "evidence %s" % eid, "quote")
    cs = e.get("char_span")
    if cs is not None and (not isinstance(cs, list) or len(cs) != 2):
        issues.append("evidence %s char_span 应为 [start, end]" % eid)
    return issues


def validate_result(obj: Any) -> List[str]:
    issues: List[str] = []
    if not isinstance(obj, dict):
        return ["result 不是 JSON 对象"]
    _require(issues, obj, "result", "case_id", "status")
    if obj.get("status") not in RESULT_STATUSES:
        issues.append("status 非法: %r" % obj.get("status"))
    rev = obj.get("review")
    if not isinstance(rev, dict):
        issues.append("review 缺失或非对象")
        return issues
    for k in ("summary", "findings", "claims", "commitments", "evidence_map", "coverage", "section_map"):
        if k not in rev:
            issues.append("review.%s 缺失" % k)
    for f in (rev.get("findings") or []):
        issues += validate_finding(f)
    for c in (rev.get("claims") or []):
        issues += validate_claim(c)
    for c in (rev.get("commitments") or []):
        issues += validate_commitment(c)
    cov = rev.get("coverage")
    if isinstance(cov, dict):
        for d in DIMENSIONS:
            if d not in cov:
                issues.append("coverage.%s 缺失" % d)
            else:
                st = cov[d].get("status") if isinstance(cov[d], dict) else None
                if st not in ("done", "partial", "skipped", "pending"):
                    issues.append("coverage.%s.status 非法: %r" % (d, st))
    else:
        issues.append("coverage 非对象")
    # 证据映射一致性
    em = rev.get("evidence_map") or {}
    items = em.get("items") or []
    if isinstance(items, list):
        ids = set()
        for e in items:
            if isinstance(e, dict):
                issues += validate_evidence_item(e)
                if e.get("id"):
                    ids.add(e["id"])
        finding_ids = {f.get("id") for f in (rev.get("findings") or []) if isinstance(f, dict) and f.get("id")}
        claim_ids = {c.get("id") for c in (rev.get("claims") or []) if isinstance(c, dict) and c.get("id")}
        for f in (rev.get("findings") or []):
            if not isinstance(f, dict):
                continue
            for ref in (f.get("evidence_refs") or []):
                if ref not in ids:
                    issues.append("finding %s 引用不存在的证据 %s" % (f.get("id"), ref))
        for e in items:
            if not isinstance(e, dict):
                continue
            for t in (e.get("supports") or []):
                if t not in finding_ids and t not in claim_ids:
                    issues.append("evidence %s supports 目标不存在: %s" % (e.get("id"), t))
            for t in (e.get("contradicts") or []):
                if t not in finding_ids and t not in claim_ids:
                    issues.append("evidence %s contradicts 目标不存在: %s" % (e.get("id"), t))
    for k in ("sources", "uncertainty", "pending_human", "pending_review",
              "limitations", "errors", "degradations", "change_log", "runtime"):
        if k not in obj:
            issues.append("缺少顶层字段 %s" % k)
    return issues


# ---------------------------------------------------------------- 报告渲染
def _cell(s: Any) -> str:
    return (str(s) if s is not None else "").replace("|", "\\|").replace("\n", " ")


def render_report(result: Dict[str, Any]) -> str:
    rev = result.get("review") or {}
    L: List[str] = []
    L += ["# 论文逻辑与论证审查报告", ""]
    L += ["- case_id: %s" % result.get("case_id"),
          "- 状态: %s" % result.get("status"),
          "- 说明: 本文件由 `result.json` 确定性渲染生成，与结构化结果同步；每条问题均含定位符、逐字原文摘录与证据引用。",
          ""]
    L += ["## 0. 方法与范围", "", rev.get("summary") or "（待补充总评）", ""]
    L += ["### 来源", ""]
    for s in (result.get("sources") or []):
        extra = ("，error=%s" % s["error"]) if s.get("error") else ""
        L += ["- `%s`（kind=%s, pages=%s, chars=%s%s）" % (
            s.get("path"), s.get("kind"), s.get("n_pages"), s.get("chars"), extra)]
    L += ["", "### 六维覆盖", "", "| 维度 | 状态 | 检查数 | 说明 |", "|---|---|---|---|"]
    cov = rev.get("coverage") or {}
    for d in DIMENSIONS:
        c = cov.get(d) or {}
        L += ["| %s | %s | %s | %s |" % (d, _cell(c.get("status", "pending")), _cell(c.get("checked", 0)), _cell(c.get("note", "")))]
    L += [""]

    findings = rev.get("findings") or []
    L += ["## 1. 问题清单（findings，%d 条）" % len(findings), ""]
    for f in findings:
        L += ["### %s  %s / %s（%s / %s）" % (
            f.get("id"), f.get("type"), f.get("severity"), f.get("status"), f.get("confidence")), ""]
        L += ["- 定位: `%s`" % f.get("locator"),
              "- 原文: > %s" % (f.get("quote") or "（无）"),
              "- 被核查主张: %s" % (f.get("claim") or "—"),
              "- 判断依据: %s" % (f.get("evidence") or "—"),
              "- 三问推理: %s" % (f.get("reasoning") or "—"),
              "- 修改建议: %s" % (f.get("suggestion") or "—")]
        refs = f.get("evidence_refs") or []
        if refs:
            L += ["- 证据引用: %s" % ", ".join(refs)]
        L += [""]

    claims = rev.get("claims") or []
    L += ["## 2. 主张清单（claims，%d 条）" % len(claims), "",
          "| id | speaker | verdict | locator | text |", "|---|---|---|---|---|"]
    for c in claims:
        L += ["| %s | %s | %s | `%s` | %s |" % (
            _cell(c.get("id")), _cell(c.get("speaker")), _cell(c.get("verdict")),
            _cell(c.get("locator")), _cell((c.get("text") or "")[:120]))]
    L += [""]

    comms = rev.get("commitments") or []
    L += ["## 3. 承诺—交付对照（commitments，%d 条）" % len(comms), "",
          "| id | 承诺 | 交付位置 | 判定 |", "|---|---|---|---|"]
    for c in comms:
        L += ["| %s | %s | %s | %s |" % (
            _cell(c.get("id")), _cell((c.get("promise") or "")[:80]),
            _cell(c.get("delivered_at") or "未交付"), _cell(c.get("verdict") or "—"))]
    L += [""]

    em = rev.get("evidence_map") or {}
    items = em.get("items") or []
    L += ["## 4. 证据映射（evidence_map，%d 条）" % len(items), ""]
    for e in items:
        span = ""
        cs = e.get("char_span")
        if isinstance(cs, list) and len(cs) == 2:
            span = "（chars %s-%s）" % (cs[0], cs[1])
        L += ["### %s  %s / %s" % (e.get("id"), e.get("kind"), e.get("status")), ""]
        L += ["- 来源: `%s` @ `%s`%s" % (e.get("source"), e.get("locator"), span),
              "- 原文: > %s" % (e.get("quote") or "（缺失证据，无原文）"),
              "- 支持: %s" % (", ".join(e.get("supports") or []) or "—"),
              "- 冲突: %s" % (", ".join(e.get("contradicts") or []) or "—")]
        if e.get("note"):
            L += ["- 说明: %s" % e["note"]]
        L += [""]
    conflicts = em.get("conflicts") or []
    if conflicts:
        L += ["### 证据冲突（source_conflict，%d 处）" % len(conflicts), ""]
        for cf in conflicts:
            sa = cf.get("source_a") or {}
            sb = cf.get("source_b") or {}
            L += ["- `%s`: %s（`%s`@`%s` ↔ `%s`@`%s`）→ %s" % (
                cf.get("id"), cf.get("metric"),
                sa.get("source"), sa.get("locator"), sb.get("source"), sb.get("locator"),
                cf.get("resolution") or "待复核")]
        L += [""]

    sm = rev.get("section_map") or []
    L += ["## 5. 章节段落映射（section_map）", ""]
    for s in sm:
        L += ["- %s: %d 段，页 %s" % (
            s.get("section"), len(s.get("paras") or []),
            ", ".join(str(x) for x in (s.get("pages") or [])) or "—")]
    L += [""]

    L += ["## 6. 依据与边界", ""]
    for title, key in (("限制", "limitations"), ("待复核", "pending_review"),
                       ("待人工确认", "pending_human"), ("不确定项", "uncertainty"),
                       ("错误", "errors"), ("降级", "degradations")):
        vals = result.get(key) or []
        if vals:
            L += ["**%s**: %d 条" % (title, len(vals))]
            for v in vals:
                L += ["- %s" % v]
            L += [""]
    L += ["## 7. 变更记录（change_log）", ""]
    for c in (result.get("change_log") or []):
        L += ["- `%s` %s: %s" % (c.get("ts"), c.get("event"), c.get("note"))]
    rt = result.get("runtime") or {}
    L += ["", "运行信息: elapsed=%.2fs, model_requests=%s" % (
        rt.get("seconds_elapsed", 0.0), rt.get("model_requests_used", 0))]
    return "\n".join(L) + "\n"
