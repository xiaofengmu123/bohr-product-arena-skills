# -*- coding: utf-8 -*-
"""core.py — 论文"论点—证据—推理链"审查 Skill 的共享工具（纯标准库）。

本模块只做确定性、可复核的机械工作：发现输入、抽取文本、切分段落、构建语料、
读写 JSON、生成报告、校验不变量。它不生成任何审查结论；审查判断由调用本 Skill
的 Agent 完成并写入 findings/claims。脚本无随机性，同一输入两次运行结果一致
（change_log 时间戳为运行时刻的真实时间，不影响审查内容）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
COVERAGE_DIMS = (
    "factual_verification",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argumentation_chain",
    "academic_norms",
)
SEVERITIES = ("critical", "major", "minor", "advisory")
FINDING_STATUSES = ("confirmed", "probable", "pending_review", "ruled_out")
CONFIDENCES = ("high", "medium", "low")
SPEAKERS = ("author", "cited", "assumption")
VERDICTS = ("supported", "overclaimed", "unsupported", "pending")
CHANGE_EVENTS = ("scaffold_written", "incremental_update", "finalize")

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".txt", ".tex", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".html", ".json"}
PDF_SUFFIXES = {".pdf"}

# 阶段预算：总和 900 秒、64 次请求，逐阶段硬截止，超时即停并如实标注。
PHASE_PLAN: List[Dict[str, Any]] = [
    {"phase": "P0_scaffold", "seconds": 150, "requests": 3,
     "deliverable": "合法 partial 骨架（result.json / report.md / corpus.md）"},
    {"phase": "P1_claims", "seconds": 150, "requests": 12,
     "deliverable": "claims[] 与 commitments[]（区分 author/cited/assumption）"},
    {"phase": "P2_review", "seconds": 300, "requests": 30,
     "deliverable": "六维 findings[]（逐条 locator+quote+type+severity）"},
    {"phase": "P3_consistency", "seconds": 120, "requests": 10,
     "deliverable": "跨章节承诺—交付对照结论"},
    {"phase": "P4_finalize", "seconds": 180, "requests": 9,
     "deliverable": "selfcheck 通过 + 诚实 status 收尾"},
]


# --------------------------------------------------------------------------
# 文本与 JSON 容错读取
# --------------------------------------------------------------------------
def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    """按 utf-8-sig/utf-8/gb18030/latin-1 顺序容错读取，返回 {text, encoding, error}。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as e:
        return {"text": "", "encoding": None, "error": "stat: %s" % e}
    if size == 0:
        return {"text": "", "encoding": "utf-8", "error": None}
    if size > max_bytes:
        return {"text": "", "encoding": None, "error": "size %d > limit %d" % (size, max_bytes)}
    raw = None
    try:
        raw = p.read_bytes()
    except OSError as e:
        return {"text": "", "encoding": None, "error": "read: %s" % e}
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return {"text": raw.decode(enc), "encoding": enc, "error": None}
        except UnicodeDecodeError:
            continue
    return {"text": raw.decode("utf-8", errors="replace"), "encoding": "utf-8(replace)",
            "error": "undecodable bytes replaced"}


def read_json_tolerant(path: str) -> Tuple[Any, Optional[str]]:
    """容错读取 JSON；解析失败返回 (None, err)，绝不抛异常。"""
    r = read_text_safe(path, max_bytes=MAX_FILE_BYTES)
    if r["error"]:
        return None, r["error"]
    try:
        return json.loads(r["text"]), None
    except Exception as e:  # noqa: BLE001
        return None, "json: %s" % e


# --------------------------------------------------------------------------
# 输入发现
# --------------------------------------------------------------------------
def _walk_text_files(root: Path) -> List[str]:
    out: List[str] = []
    if not root.exists():
        return out
    try:
        for child in sorted(root.rglob("*")):
            if child.is_file() and (child.suffix.lower() in TEXT_SUFFIXES
                                    or child.suffix.lower() in PDF_SUFFIXES):
                out.append(str(child))
    except OSError:
        pass
    return out


def discover_inputs(input_dir: str) -> Dict[str, Any]:
    """扫描输入目录，不假设固定文件名。返回 case_json/task_md/materials/notes/all_files。"""
    base = Path(input_dir)
    case_json = None
    task_md = None
    materials: List[str] = []
    notes: List[str] = []
    all_files: List[str] = []

    def _pick(names):
        for n in names:
            p = base / n
            if p.is_file():
                return str(p)
        return None

    case_json = _pick(("case.json", "case.JSON", "input.json"))
    task_md = _pick(("task.md", "TASK.md", "README.md"))

    # 材料：优先 materials/ 子目录，再回退到输入目录下直接存在的文本/PDF。
    mat_dir = base / "materials"
    if mat_dir.is_dir():
        for f in _walk_text_files(mat_dir):
            materials.append(f)
            all_files.append(f)
    for f in _walk_text_files(base):
        if f == case_json or f == task_md:
            continue
        if f not in all_files:
            materials.append(f)
            all_files.append(f)

    if case_json and case_json not in all_files:
        all_files.append(case_json)
    if task_md and task_md not in all_files:
        all_files.append(task_md)
    # notes.md 之类的辅助文件若在材料目录外且未被收录，兜底扫描一次
    for cand in ("notes.md", "evidence.md", "extra.md"):
        p = base / cand
        if p.is_file() and str(p) not in all_files:
            notes.append(str(p))
            all_files.append(str(p))
    return {"case_json": case_json, "task_md": task_md, "materials": materials,
            "notes": notes, "all_files": sorted(set(all_files))}


# --------------------------------------------------------------------------
# 文本抽取与段落切分
# --------------------------------------------------------------------------
def run_pdftotext(pdf_path: str, work_dir: str) -> Dict[str, Any]:
    """调用外部 pdftotext；缺失/失败时返回错误，不崩溃。"""
    out = Path(work_dir) / ("_extract_" + Path(pdf_path).stem + ".txt")
    try:
        cp = subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(pdf_path), str(out)],
            capture_output=True, timeout=180)
        if cp.returncode != 0:
            err = (cp.stderr or b"").decode("utf-8", errors="replace").strip()[:500]
            return {"text": "", "pages": 0, "error": err or "pdftotext rc=%s" % cp.returncode}
        if not out.exists():
            return {"text": "", "pages": 0, "error": "pdftotext produced no output"}
        r = read_text_safe(str(out))
        return {"text": r["text"], "pages": 0, "error": r["error"]}
    except FileNotFoundError:
        return {"text": "", "pages": 0, "error": "pdftotext not found"}
    except Exception as e:  # noqa: BLE001
        return {"text": "", "pages": 0, "error": "pdftotext: %s" % e}


def extract_text(path: str, work_dir: str) -> Dict[str, Any]:
    """根据扩展名抽取文本：文本文件直接读取，PDF 走 pdftotext。"""
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        res = run_pdftotext(path, work_dir)
        res["kind"] = "pdf"
        res["has_pages"] = True
        if res["pages"] == 0:
            res["pages"] = res["text"].count("\f") + 1 if res["text"] else 0
        return res
    r = read_text_safe(path)
    return {"text": r["text"], "pages": 0, "kind": "text", "has_pages": False,
            "error": r["error"]}


_HEADING_RE = re.compile(r"^(#{1,6}\s+\S|\d{1,2}(\.\d{1,2}){0,3}[\.、．]\s*\S)")


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return bool(_HEADING_RE.match(s)) and len(s) <= 90


def _section_label(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^#{1,6}\s+", "", s)
    s = re.sub(r"^\d{1,2}(\.\d{1,2}){0,3}[\.、．]\s*", "", s)
    return s[:80] or "(未分节)"


def segment_text(text: str, src: str, has_pages: bool) -> List[Dict[str, Any]]:
    """按空行切段并分配稳定定位符：PDF 为 p<页>¶<段>，文本为 L<段>。"""
    records: List[Dict[str, Any]] = []
    if not text.strip():
        return records
    if has_pages and "\f" in text:
        pages = text.split("\f")
    else:
        pages = [text]
    section = "(未分节)"
    para_no = 0
    for pi, page_text in enumerate(pages, 1):
        for raw in re.split(r"\n\s*\n", page_text):
            lines = [ln.rstrip() for ln in raw.splitlines()]
            lines = [ln for ln in lines if ln.strip()]
            if not lines:
                continue
            first = lines[0].strip()
            if _is_heading(first):
                section = _section_label(first)
            body = "\n".join(lines).strip()
            if not body:
                continue
            para_no += 1
            loc = ("p%d¶%d" % (pi, para_no)) if has_pages else ("L%d" % para_no)
            records.append({"src": src, "page": pi if has_pages else None,
                            "para": para_no, "section": section,
                            "locator": loc, "text": body})
    return records


# --------------------------------------------------------------------------
# 落盘工具（原子写）
# --------------------------------------------------------------------------
def atomic_write_text(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def atomic_write_json(path: str, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_result(output_dir: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    p = Path(output_dir) / "result.json"
    if not p.exists():
        return None, "result.json missing"
    data, err = read_json_tolerant(str(p))
    if err or not isinstance(data, dict):
        return None, err or "result.json not an object"
    return data, None


# --------------------------------------------------------------------------
# 结果骨架 / 覆盖 / 报告渲染
# --------------------------------------------------------------------------
def empty_coverage() -> Dict[str, Any]:
    return {d: {"status": "pending", "checked": 0, "note": ""} for d in COVERAGE_DIMS}


def make_skeleton(case_id: str, task_meta: Dict[str, Any],
                  contract_extra: Dict[str, Any], sources: List[Dict[str, Any]],
                  section_map: List[Dict[str, Any]], degradations: List[str],
                  errors: List[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "case_id": case_id,
        "status": "partial",
        "task": {"challenge_id": task_meta.get("challenge_id"),
                 "task_title": task_meta.get("task_title")},
        "review": {
            "summary": "",
            "findings": [],
            "claims": [],
            "commitments": [],
            "coverage": empty_coverage(),
            "section_map": section_map,
        },
        "sources": sources,
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [{"ts": now_utc(), "event": "scaffold_written",
                        "note": "partial 骨架：仅完成输入发现与语料切分，尚未开始审查"}],
        "runtime": {"seconds_elapsed": 0.0, "model_requests_used": 0,
                    "budget": {"requests": 64, "seconds": 900}},
    }
    # 原样回填 output_contract 的键（值逐字复制，缺值/null 保持原样），不改名不删未知键
    for k, v in contract_extra.items():
        result.setdefault(k, v)
    return result


def _journal_path(work_dir: str) -> Path:
    return Path(work_dir) / "journal.jsonl"


def append_journal(work_dir: str, phase: str, event: str, note: str) -> None:
    p = _journal_path(work_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": now_utc(), "phase": phase, "event": event, "note": note},
                      ensure_ascii=False)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_journal(work_dir: str) -> List[Dict[str, Any]]:
    p = _journal_path(work_dir)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:  # noqa: BLE001
            continue
    return out


def render_report(result: Dict[str, Any]) -> str:
    review = result.get("review", {}) if isinstance(result.get("review"), dict) else {}
    findings = review.get("findings", []) or []
    claims = review.get("claims", []) or []
    commitments = review.get("commitments", []) or []
    coverage = review.get("coverage", {}) or {}

    lines: List[str] = []
    lines.append("# 论文逻辑与论证审查报告")
    lines.append("")
    lines.append("- case_id: %s" % result.get("case_id"))
    lines.append("- 状态: %s" % result.get("status"))
    lines.append("")
    lines.append("## 0. 总评")
    lines.append("")
    lines.append(review.get("summary") or "（尚未填写总评）")
    lines.append("")
    lines.append("## 1. 问题清单（findings）")
    lines.append("")
    if findings:
        lines.append("| ID | 类型 | 严重度 | 定位 | 状态 | 原文摘录 | 修改建议 |")
        lines.append("|---|---|---|---|---|---|---|")
        for f in findings:
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                f.get("id"), f.get("type"), f.get("severity"), f.get("locator"),
                f.get("status"), _md_escape(f.get("quote")), _md_escape(f.get("suggestion"))))
    else:
        lines.append("（暂无已确认问题）")
    lines.append("")
    lines.append("## 2. 主张清单（claims）")
    lines.append("")
    if claims:
        for c in claims:
            lines.append("- [%s] %s — speaker=%s verdict=%s locator=%s" % (
                c.get("id"), c.get("text"), c.get("speaker"), c.get("verdict"), c.get("locator")))
    else:
        lines.append("（暂无）")
    lines.append("")
    lines.append("## 3. 引言承诺与交付（commitments）")
    lines.append("")
    if commitments:
        for c in commitments:
            lines.append("- [%s] %s → %s" % (c.get("id"), c.get("promise"),
                                              c.get("delivered_at") or "未交付"))
    else:
        lines.append("（暂无）")
    lines.append("")
    lines.append("## 4. 六类检查项覆盖")
    lines.append("")
    lines.append("| 维度 | 状态 | 检查数 | 说明 |")
    lines.append("|---|---|---|---|")
    for d in COVERAGE_DIMS:
        cov = coverage.get(d, {}) if isinstance(coverage.get(d), dict) else {}
        lines.append("| %s | %s | %s | %s |" % (
            d, cov.get("status", "pending"), cov.get("checked", 0),
            _md_escape(cov.get("note", ""))))
    lines.append("")
    lines.append("## 5. 待复核项与限制")
    lines.append("")
    for head, key in (("待复核（pending_review）", "pending_review"),
                      ("需人工决定（pending_human）", "pending_human"),
                      ("不确定性（uncertainty）", "uncertainty"),
                      ("限制（limitations）", "limitations")):
        items = result.get(key, []) or []
        lines.append("- %s: %d 条" % (head, len(items)))
        for it in items:
            lines.append("  - %s" % _md_escape(str(it)))
        lines.append("")
    lines.append("## 6. 变更记录")
    lines.append("")
    for e in (result.get("change_log", []) or []):
        lines.append("- [%s] %s — %s" % (e.get("ts"), e.get("event"), e.get("note", "")))
    lines.append("")
    return "\n".join(lines)


def _md_escape(s: Any) -> str:
    if s is None:
        return ""
    return str(s).replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------
def validate_result(result: Dict[str, Any]) -> List[Dict[str, str]]:
    issues: List[Dict[str, str]] = []
    if not isinstance(result.get("case_id"), str) or not result["case_id"]:
        issues.append({"level": "fail", "msg": "case_id 缺失或为空"})
    if result.get("status") not in RESULT_STATUSES:
        issues.append({"level": "fail", "msg": "status 非法: %r" % result.get("status")})
    if not isinstance(result.get("sources"), list):
        issues.append({"level": "warn", "msg": "sources 缺失或非数组"})
    if not isinstance(result.get("uncertainty"), list):
        issues.append({"level": "warn", "msg": "uncertainty 缺失或非数组"})
    if not isinstance(result.get("change_log"), list):
        issues.append({"level": "warn", "msg": "change_log 缺失或非数组"})
    review = result.get("review")
    if not isinstance(review, dict):
        issues.append({"level": "fail", "msg": "review 缺失或非对象"})
        return issues
    if "summary" not in review:
        issues.append({"level": "warn", "msg": "review.summary 缺失"})
    for key in ("findings", "claims", "commitments"):
        if not isinstance(review.get(key), list):
            issues.append({"level": "fail", "msg": "review.%s 缺失或非数组" % key})
    coverage = review.get("coverage")
    if not isinstance(coverage, dict):
        issues.append({"level": "fail", "msg": "review.coverage 缺失或非对象"})
    else:
        for d in COVERAGE_DIMS:
            if d not in coverage:
                issues.append({"level": "warn", "msg": "coverage 缺维度 %s" % d})
    for i, f in enumerate(review.get("findings", []) or []):
        tag = "findings[%d]" % i
        for key in ("id", "type", "severity", "locator", "quote", "claim", "evidence",
                    "reasoning", "suggestion", "status", "confidence"):
            if key not in f or f.get(key) in (None, ""):
                issues.append({"level": "fail", "msg": "%s 缺字段 %s" % (tag, key)})
        if f.get("severity") not in SEVERITIES:
            issues.append({"level": "warn", "msg": "%s severity 非法: %r" % (tag, f.get("severity"))})
        if f.get("status") not in FINDING_STATUSES:
            issues.append({"level": "warn", "msg": "%s status 非法: %r" % (tag, f.get("status"))})
    return issues
