# -*- coding: utf-8 -*-
"""review_core.py — 论文逻辑与论证审查 Skill 的共享库（纯标准库，确定性实现）。

职责：
  1. 发现输入（case.json / task.md / materials），读取并容错解析；
  2. 抽取文本、切分段落、构建带定位符的语料；
  3. 规范化、排序、编号与校验（供 main.py 与 selftest.py 复用）。

设计约束：
  - 只用 Python 标准库；PDF 抽取依赖外部 `pdftotext`，缺失时降级并记录；
  - 无随机性：所有排序使用确定性的字符串键，不依赖 dict 迭代顺序；
  - 全部 IO 采用 utf-8-sig + errors=replace，坏编码不崩溃。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 词汇表（唯一真源，供脚本与 Agent 一致使用）
# ---------------------------------------------------------------------------
RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

COVERAGE_DIMS = (
    "factual_verification",   # 事实核验
    "citation_support",       # 引用支持
    "method_design",          # 方法设计
    "statistical_reporting",  # 统计报告
    "argument_chain",         # 论证链
    "academic_norm",          # 学术规范
)

COVERAGE_STATES = ("done", "partial", "skipped")

FINDING_TYPES = (
    "factual_error",      # 事实错误
    "overclaim",          # 结论超出数据支撑
    "evidence_gap",       # 证据不足
    "citation_mismatch",  # 引用与观点不符
    "method_mismatch",    # 方法回答不了研究问题
    "statistical_issue",  # 统计口径/方向问题
    "reasoning_gap",      # 推理跳跃/隐含假设未说明
    "causal_inference",   # 相关误作因果
    "inconsistency",      # 章节前后不一致
    "missing_delivery",   # 承诺未交付
    "norm_issue",         # 学术规范问题
    "suggestion",         # 建议性意见
)

SEVERITIES = ("critical", "major", "minor", "suggestion")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

FINDING_STATUSES = ("confirmed", "probable", "pending_review", "ruled_out")
CONFIDENCES = ("high", "medium", "low")
SPEAKERS = ("author", "cited", "assumption")
VERDICTS = ("supported", "overclaimed", "unsupported", "pending", "not_applicable")

MAX_FILE_BYTES = 2_000_000  # 单文件上限，超限跳过并记录


# ---------------------------------------------------------------------------
# 容错读取
# ---------------------------------------------------------------------------
def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    """读取文本文件，返回 {text, chars, error}。坏编码/超限不抛异常。"""
    p = Path(path)
    try:
        size = p.stat().st_size
        if size > max_bytes:
            return {"text": "", "chars": 0,
                    "error": "file_exceeds_limit_%d_bytes" % max_bytes}
        raw = p.read_bytes()
    except OSError as e:
        return {"text": "", "chars": 0, "error": "read_failed:%s" % e}
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            return {"text": text, "chars": len(text), "error": None}
        except UnicodeDecodeError:
            continue
    text = raw.decode("utf-8", errors="replace")
    return {"text": text, "chars": len(text), "error": "bad_encoding_replaced"}


def read_json_tolerant(path: str) -> Tuple[Any, Optional[str]]:
    """容错读 JSON，返回 (data, error)。坏 JSON 返回 ({}, error) 不抛异常。"""
    r = read_text_safe(path, max_bytes=MAX_FILE_BYTES)
    if r["error"]:
        return {}, "unreadable:%s" % r["error"]
    if not r["text"].strip():
        return {}, "empty_file"
    try:
        return json.loads(r["text"]), None
    except Exception as e:
        return {}, "bad_json:%s" % e


def read_json_array(path: str) -> Tuple[List[Any], Optional[str]]:
    """容错读 JSON 数组，非数组返回 (列表, error)。"""
    data, err = read_json_tolerant(path)
    if err:
        return [], err
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"], None
    return [], "not_a_json_array"


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------
_TEXT_EXTS = {".txt", ".md", ".markdown", ".tex", ".csv", ".tsv", ".json", ".jsonl", ".xml"}


def _is_material(name: str) -> bool:
    low = name.lower()
    if low in {"case.json", "task.md", "result.json", "report.md"}:
        return False
    return True


def discover_inputs(input_dir: str) -> Dict[str, Any]:
    """扫描输入目录，返回 {case_json, task_md, materials:[paths]}。"""
    root = Path(input_dir)
    out: Dict[str, Any] = {"case_json": None, "task_md": None, "materials": []}
    if not root.is_dir():
        return out

    def walk(d: Path):
        try:
            return sorted(d.iterdir(), key=lambda x: x.name.lower())
        except OSError:
            return []

    # 先找直接的 case.json / task.md
    for cand in ("case.json", "task.md"):
        p = root / cand
        if p.is_file():
            if cand == "case.json":
                out["case_json"] = str(p)
            else:
                out["task_md"] = str(p)

    for entry in walk(root):
        if entry.is_dir():
            # 一层 materials/ 与常见子目录
            if entry.name.lower() in {"materials", "material", "input", "files", "docs"}:
                for f in walk(entry):
                    if f.is_file() and _is_material(f.name):
                        out["materials"].append(str(f))
            else:
                for f in walk(entry):
                    if f.is_file() and _is_material(f.name):
                        out["materials"].append(str(f))
        elif entry.is_file():
            if entry.name.lower() == "case.json" and out["case_json"] is None:
                out["case_json"] = str(entry)
            elif entry.name.lower() == "task.md" and out["task_md"] is None:
                out["task_md"] = str(entry)
            elif _is_material(entry.name):
                out["materials"].append(str(entry))

    # 去重并保持稳定顺序
    out["materials"] = sorted(set(out["materials"]), key=lambda x: x.lower())
    return out


# ---------------------------------------------------------------------------
# PDF / 文本抽取
# ---------------------------------------------------------------------------
def _pdftotext_available() -> Optional[str]:
    return shutil.which("pdftotext")


def extract_pdf_pages(path: str) -> Tuple[List[str], int, Optional[str]]:
    """用 pdftotext -layout 抽取 PDF；返回 (按页文本列表, 页数, error)。"""
    exe = _pdftotext_available()
    if not exe:
        return [], 0, "pdftotext_not_found"
    try:
        proc = subprocess.run(
            [exe, "-layout", str(path), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except Exception as e:
        return [], 0, "pdftotext_run_failed:%s" % e
    if proc.returncode != 0:
        return [], 0, "pdftotext_exit_%d" % proc.returncode
    raw = proc.stdout
    text = raw.decode("utf-8", errors="replace")
    pages = text.split("\f")
    # 过滤纯空页
    pages = [pg for pg in pages if pg.strip()]
    return pages, len(pages), None


def extract_text(path: str) -> Dict[str, Any]:
    """抽取单个材料为按页文本。返回 {path, pages:[str], n_pages, chars, error, kind}。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        pages, n, err = extract_pdf_pages(path)
        chars = sum(len(pg) for pg in pages)
        return {"path": path, "kind": "pdf", "pages": pages, "n_pages": n,
                "chars": chars, "error": err}
    if suffix in _TEXT_EXTS or suffix == "":
        r = read_text_safe(path)
        return {"path": path, "kind": "text", "pages": [r["text"]] if r["text"] else [],
                "n_pages": 1 if r["text"] else 0, "chars": r["chars"],
                "error": r["error"]}
    return {"path": path, "kind": "unsupported", "pages": [], "n_pages": 0,
            "chars": 0, "error": "unsupported_extension:%s" % (suffix or "<none>")}


# ---------------------------------------------------------------------------
# 段落切分与定位符
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(
    r'^\s*(?:\d+(?:\.\d+)*[\.、\)]?\s*)?'
    r'(?:Abstract|Introduction|Background|Related\s+Work|Methods?|Materials?'
    r'(?:\s+and\s+Methods?)?|Results?(?:\s+and\s+Discussion)?|Discussion|'
    r'Conclusion|References|Acknowledgments?|Supplementary|Appendix|'
    r'Limitations?|Abbreviations?|Highlights?)\b', re.I)


_MD_HEADING_RE = re.compile(r'^#{1,6}\s+\S')


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 90:
        return False
    if _MD_HEADING_RE.match(s):
        return True
    if _HEADING_RE.match(s):
        return True
    # 全大写短行且不含句末标点
    if s.isupper() and len(s) >= 3 and not re.search(r'[.!?:;，。；：]$', s):
        return True
    return False


def _clean_para(lines: List[str]) -> str:
    return " ".join(" ".join(l.split()) for l in lines if l.strip()).strip()


def split_paragraphs(pages: List[str], src: str) -> List[Dict[str, Any]]:
    """把多页文本切成段落记录，返回 [{src,page,para,section,kind,text}]。"""
    records: List[Dict[str, Any]] = []
    section = ""
    para_no = 0
    for page_idx, page in enumerate(pages, start=1):
        if not page.strip():
            continue
        buf: List[str] = []
        for line in page.splitlines():
            if not line.strip():
                if buf:
                    para_no += 1
                    records.append({"src": src, "page": page_idx, "para": para_no,
                                    "section": section, "kind": "paragraph",
                                    "text": _clean_para(buf)})
                    buf = []
                continue
            if _looks_like_heading(line):
                if buf:
                    para_no += 1
                    records.append({"src": src, "page": page_idx, "para": para_no,
                                    "section": section, "kind": "paragraph",
                                    "text": _clean_para(buf)})
                    buf = []
                section = line.strip()
                para_no += 1
                records.append({"src": src, "page": page_idx, "para": para_no,
                                "section": section, "kind": "heading",
                                "text": section})
                continue
            buf.append(line)
        if buf:
            para_no += 1
            records.append({"src": src, "page": page_idx, "para": para_no,
                            "section": section, "kind": "paragraph",
                            "text": _clean_para(buf)})
    return records


def locator(rec: Dict[str, Any]) -> str:
    """生成定位符：有页码为 `p<页> ¶P<段落>`，无页码为 `¶P<段落>`。"""
    pid = "P%04d" % int(rec["para"])
    if rec.get("page"):
        return "p%s %s" % (rec["page"], pid)
    return "%s" % pid


def parse_locator(loc: str) -> Tuple[Optional[int], Optional[int]]:
    """解析定位符，返回 (page, para)；缺项为 None。"""
    m_page = re.search(r'p(\d+)', loc or "")
    m_para = re.search(r'P(\d+)', loc or "")
    page = int(m_page.group(1)) if m_page else None
    para = int(m_para.group(1)) if m_para else None
    return (page, para)


def locator_sort_key(loc: str) -> Tuple[int, int]:
    """定位符的确定性排序键：无页码置后。"""
    page, para = parse_locator(loc)
    return (page if page is not None else 10 ** 9, para if para is not None else 10 ** 9)


# ---------------------------------------------------------------------------
# 语料落盘
# ---------------------------------------------------------------------------
def build_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                    path: str) -> str:
    """把段落记录写成 corpus.md，返回文件路径。"""
    lines = ["# 语料库（逐段审查用；定位符可直接引用）", ""]
    lines.append("来源文件 %d 个，段落 %d 个。" % (len(sources), len(records)))
    lines.append("定位符格式：有页码为 `p<页> ¶P<段落>`，纯文本为 `¶P<段落>`。")
    lines.append("引用原文必须逐字复制，不得改写；标题行 kind=heading。")
    lines.append("")
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_src.setdefault(rec["src"], []).append(rec)
    for src in sources:
        key = src["path"]
        recs = sorted(by_src.get(key, []), key=lambda r: (r["para"],))
        lines.append("")
        lines.append("## 来源: %s" % key)
        lines.append("- kind=%s n_pages=%s chars=%s error=%s" % (
            src.get("kind"), src.get("n_pages"), src.get("chars"), src.get("error")))
        lines.append("")
        for rec in recs:
            lines.append("### %s [%s] %s" % (locator(rec), rec["section"] or "无章节", rec["kind"]))
            lines.append(rec["text"])
            lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 规范化与校验
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """把任意空白折叠为单空格，用于逐字摘录匹配（忽略换行/缩进差异）。"""
    return re.sub(r"\s+", " ", text or "").strip()


def paragraph_index(records: List[Dict[str, Any]]) -> Dict[Tuple[str, int], str]:
    """构建 (src, para) -> 规范化文本 的索引，供 selfcheck 校验定位与摘录。"""
    idx: Dict[Tuple[str, int], str] = {}
    for rec in records:
        idx[(rec["src"], int(rec["para"]))] = normalize(rec["text"])
    return idx


def finding_sort_key(f: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        f.get("source", "") or "",
        locator_sort_key(f.get("locator", "") or ""),
        SEVERITY_RANK.get(f.get("severity", "suggestion"), 99),
        f.get("type", "") or "",
        normalize(f.get("quote", "") or ""),
    )


def claim_sort_key(c: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        c.get("source", "") or "",
        locator_sort_key(c.get("locator", "") or ""),
        normalize(c.get("text", "") or ""),
    )


def commitment_sort_key(c: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        c.get("source", "") or "",
        locator_sort_key(c.get("locator", "") or ""),
        normalize(c.get("promise", "") or ""),
    )


def renumber(items: List[Dict[str, Any]], prefix: str) -> List[Dict[str, Any]]:
    """按确定性排序后重新编号（F001/C001/M001…），保证顺序与输入顺序无关。"""
    out = []
    for i, item in enumerate(items, start=1):
        it = dict(item)
        it["id"] = "%s%03d" % (prefix, i)
        out.append(it)
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_string_enum(value: Any, allowed: Tuple[str, ...], field: str,
                         errors: List[str]) -> Any:
    if value is None or value == "":
        return value
    if value not in allowed:
        errors.append("字段 %s 取值 %r 不在 %s 中" % (field, value, list(allowed)))
    return value


def validate_result(result: Dict[str, Any], records: List[Dict[str, Any]] = None,
                    corpus_paras: Dict[Tuple[str, int], str] = None) -> List[str]:
    """校验 result.json 结构，返回错误列表；records/corpus 用于摘录一致性校验。"""
    errors: List[str] = []
    if not isinstance(result, dict):
        return ["result.json 顶层必须是 JSON 对象"]

    if not result.get("case_id"):
        errors.append("缺少 case_id")
    validate_string_enum(result.get("status"), RESULT_STATUSES, "status", errors)

    review = result.get("review")
    if not isinstance(review, dict):
        errors.append("缺少 review 对象")
    else:
        for f in review.get("findings", []):
            for k in ("id", "type", "severity", "locator", "quote", "claim",
                      "evidence", "reasoning", "suggestion", "status", "confidence"):
                if k not in f:
                    errors.append("finding %s 缺少字段 %s" % (f.get("id", "?"), k))
            validate_string_enum(f.get("type"), FINDING_TYPES, "finding.type", errors)
            validate_string_enum(f.get("severity"), SEVERITIES, "finding.severity", errors)
            validate_string_enum(f.get("status"), FINDING_STATUSES, "finding.status", errors)
            validate_string_enum(f.get("confidence"), CONFIDENCES, "finding.confidence", errors)
            if isinstance(f.get("reasoning"), dict):
                for k in ("affects_main_conclusion", "fixable", "affects_reproducibility"):
                    if k not in f["reasoning"]:
                        errors.append("finding %s reasoning 缺三问字段 %s" % (f.get("id"), k))
            elif not f.get("reasoning"):
                errors.append("finding %s 缺少 reasoning 三问" % f.get("id"))

        for c in review.get("claims", []):
            for k in ("id", "text", "speaker", "locator", "verdict"):
                if k not in c:
                    errors.append("claim %s 缺少字段 %s" % (c.get("id", "?"), k))
            validate_string_enum(c.get("speaker"), SPEAKERS, "claim.speaker", errors)
            validate_string_enum(c.get("verdict"), VERDICTS, "claim.verdict", errors)

        cov = review.get("coverage")
        if not isinstance(cov, dict):
            errors.append("缺少 review.coverage")
        else:
            for dim in COVERAGE_DIMS:
                d = cov.get(dim)
                if not isinstance(d, dict):
                    errors.append("coverage 缺少维度 %s" % dim)
                    continue
                validate_string_enum(d.get("status"), COVERAGE_STATES,
                                     "coverage.%s.status" % dim, errors)
                if not isinstance(d.get("checked"), int) or d["checked"] < 0:
                    errors.append("coverage.%s.checked 必须为非负整数" % dim)

    for k in ("sources", "uncertainty", "limitations", "errors", "degradations",
              "change_log", "pending_review", "pending_human"):
        if k not in result:
            errors.append("缺少顶层字段 %s" % k)

    # 逐字摘录一致性：每个 finding 的 quote 必须能在其定位段落找到
    if records is not None and corpus_paras is not None:
        for f in review.get("findings", []):
            loc = f.get("locator", "")
            _, para = parse_locator(loc)
            src = f.get("source", "")
            key = (src, para) if para is not None else None
            if key is None or key not in corpus_paras:
                if f.get("status") != "pending_review":
                    errors.append("finding %s 定位 %s 无法对应到语料段落" % (f.get("id"), loc))
                continue
            q = normalize(f.get("quote", ""))
            if q and q not in corpus_paras[key]:
                errors.append("finding %s 的逐字摘录在定位段落 %s 中找不到" % (f.get("id"), loc))
    return errors


def make_skeleton(case_id: str, task_meta: Dict[str, Any],
                  contract: Dict[str, Any]) -> Dict[str, Any]:
    """生成最小合法 result.json 骨架（保证任何异常下仍可解析）。"""
    cov = {}
    for dim in COVERAGE_DIMS:
        cov[dim] = {"status": "skipped", "checked": 0, "note": ""}
    base: Dict[str, Any] = {
        "case_id": case_id,
        "status": "partial",
        "task": {"challenge_id": task_meta.get("challenge_id"),
                 "task_title": task_meta.get("task_title")},
        "review": {
            "summary": "骨架版：尚未开始实质审查，需按 SKILL.md 流程补齐。",
            "findings": [],
            "claims": [],
            "commitments": [],
            "coverage": cov,
            "section_map": [],
        },
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [{"ts": now_iso(), "event": "skeleton_written",
                        "note": "脚本脚手架落盘，内容待增量补齐"}],
        "runtime": {"seconds_elapsed": 0.0, "model_requests_used": 0,
                    "budget": {"requests": 64, "seconds": 900}},
    }
    # 原样回填 output_contract 的全部键（缺值 None；不改名、不删未知键）
    for k, v in contract.items():
        if k not in base:
            base[k] = None if v is None else v
    return base


# ---------------------------------------------------------------------------
# report.md 生成（确定性：内容完全由 result.json 派生）
# ---------------------------------------------------------------------------
def render_report(result: Dict[str, Any]) -> str:
    rv = result.get("review", {})
    cov = rv.get("coverage", {})
    lines: List[str] = []
    lines.append("# 论文逻辑与论证审查报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id", ""))
    lines.append("- 状态: `%s`" % result.get("status", ""))
    lines.append("")
    lines.append("## 0. 总评")
    lines.append("")
    lines.append(rv.get("summary", "") or "（未填写）")
    lines.append("")

    lines.append("## 1. 问题清单（findings）")
    lines.append("")
    findings = rv.get("findings", [])
    if not findings:
        lines.append("（无）")
    for f in findings:
        r = f.get("reasoning", {})
        three = "主结论=%s / 可修正=%s / 可复现=%s" % (
            r.get("affects_main_conclusion"), r.get("fixable"),
            r.get("affects_reproducibility"))
        lines.append("### %s [%s/%s] %s" % (
            f.get("id"), f.get("severity"), f.get("type"), f.get("locator")))
        lines.append("- 定位: `%s`（来源 %s）" % (f.get("locator"), f.get("source", "")))
        lines.append("- 原文: %s" % f.get("quote", ""))
        lines.append("- 主张: %s" % f.get("claim", ""))
        lines.append("- 依据: %s" % f.get("evidence", ""))
        lines.append("- 三问: %s" % three)
        lines.append("- 建议: %s" % f.get("suggestion", ""))
        lines.append("- 判定: status=%s confidence=%s" % (f.get("status"), f.get("confidence")))
        lines.append("")

    lines.append("## 2. 主张清单（claims）")
    lines.append("")
    claims = rv.get("claims", [])
    if not claims:
        lines.append("（无）")
    for c in claims:
        lines.append("- %s [%s] %s :: %s" % (
            c.get("id"), c.get("speaker"), c.get("verdict"), c.get("text", "")))
        lines.append("  - 定位: %s；依据: %s" % (c.get("locator"), c.get("evidence", "")))
    lines.append("")

    lines.append("## 3. 承诺—交付对照（commitments）")
    lines.append("")
    comms = rv.get("commitments", [])
    if not comms:
        lines.append("（无）")
    for m in comms:
        lines.append("- %s 承诺: %s" % (m.get("id"), m.get("promise", "")))
        lines.append("  - 定位: %s；交付位置: %s" % (m.get("locator"), m.get("delivered_at")))
    lines.append("")

    lines.append("## 4. 六类检查项覆盖")
    lines.append("")
    lines.append("| 维度 | 状态 | 检查数 | 说明 |")
    lines.append("|---|---|---|---|")
    for dim in COVERAGE_DIMS:
        d = cov.get(dim, {})
        lines.append("| %s | %s | %s | %s |" % (
            dim, d.get("status", "skipped"), d.get("checked", 0), d.get("note", "")))
    lines.append("")

    for title, key in (("## 5. 依据与边界（limitations）", "limitations"),
                       ("## 6. 待复核项（pending_review / pending_human）", "pending_review"),
                       ("## 7. 不确定性（uncertainty）", "uncertainty"),
                       ("## 8. 运行与降级（errors / degradations）", "errors")):
        lines.append(title)
        lines.append("")
        items = result.get(key, [])
        if not items:
            lines.append("（无）")
        for it in items:
            lines.append("- %s" % it)
        lines.append("")
    if result.get("pending_human"):
        lines.append("- 待人工决策（pending_human）:")
        for it in result["pending_human"]:
            lines.append("  - %s" % it)
        lines.append("")

    lines.append("## 9. 变更记录")
    lines.append("")
    for cl in result.get("change_log", []):
        lines.append("- %s %s :: %s" % (cl.get("ts", ""), cl.get("event", ""), cl.get("note", "")))
    lines.append("")
    return "\n".join(lines)


def atomic_write_text(path: str, text: str) -> Optional[str]:
    """原子写盘：先写临时文件再替换；失败返回错误信息。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
        return None
    except OSError as e:
        return "write_failed:%s" % e
