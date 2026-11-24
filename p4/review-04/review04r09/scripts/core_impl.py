# -*- coding: utf-8 -*-
"""core_impl.py — 论文逻辑与论证审查 Skill 的核心实现（纯标准库）。

被 main.py 引用：输入发现、文本抽取、语料切分、结果骨架构建、报告渲染、
条目规范化等纯逻辑；CLI 编排留在 main.py，便于各自维护与体积控制。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

# 六类检查项：键为机器字段，值为中文标签（result.json 与 report.md 共用）
COVERAGE_DIMS = {
    "factual_verification": "事实核验",
    "citation_support": "引用支持",
    "method_design": "方法设计",
    "statistical_reporting": "统计报告",
    "argument_chain": "论证链",
    "academic_norms": "学术规范",
}
COVERAGE_STATUSES = ("done", "partial", "skipped", "pending")

MATERIAL_EXTS = {".md", ".txt", ".tex", ".markdown", ".rst"}
PDF_EXTS = {".pdf"}
SKIP_FILENAMES = {"case.json", "task.md", "task.txt"}

FINDING_DEFAULTS = {
    "id": None, "type": "other", "severity": "minor", "locator": "",
    "quote": "", "claim": "", "evidence": "", "reasoning": "",
    "suggestion": "", "status": "pending_review", "confidence": "low",
}
CLAIM_DEFAULTS = {
    "id": None, "text": "", "speaker": "author", "section": None,
    "locator": "", "verdict": "pending", "evidence": "",
}
COMMITMENT_DEFAULTS = {
    "id": None, "promise": "", "locator": "", "delivered_at": None,
    "verdict": "pending", "note": "",
}
_ITEM_PREFIX = {"findings": "F", "claims": "C", "commitments": "M"}

_MAX_TEXT_BYTES = 2_000_000


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text_safe(path: str, max_bytes: int = _MAX_TEXT_BYTES) -> Dict[str, Any]:
    """读取文本文件，容错 BOM/编码/超大文件，返回 {text, encoding, error, chars}。"""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except Exception as e:
        return {"text": "", "encoding": None, "error": str(e), "chars": 0}
    if len(raw) > max_bytes:
        return {"text": "", "encoding": None,
                "error": "size %d > %d bytes (skipped)" % (len(raw), max_bytes),
                "chars": len(raw)}
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            return {"text": text, "encoding": enc, "error": None, "chars": len(text)}
        except (UnicodeDecodeError, LookupError):
            continue
    text = raw.decode("latin-1", errors="replace")
    return {"text": text, "encoding": "latin-1(fallback)",
            "error": "decode fallback (latin-1 replace)", "chars": len(text)}


def read_json_tolerant(path: str) -> Tuple[Any, Optional[str]]:
    """容错读 JSON：返回 (数据, 错误)。失败时数据为 None。"""
    if not Path(path).exists():
        return None, "missing file: %s" % path
    r = read_text_safe(path)
    if r["error"]:
        return None, "read error: %s" % r["error"]
    text = r["text"].lstrip("\ufeff").strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "json decode error: %s" % e


def atomic_write_text(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def write_json_atomic(path: str, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def load_json_list(path: Optional[str]) -> Tuple[Optional[List[Any]], Optional[str]]:
    """读 JSON 数组文件；返回 (数组或 None, 错误)。"""
    if not path:
        return None, "no path"
    if not Path(path).exists():
        return None, "missing file: %s" % path
    data, err = read_json_tolerant(path)
    if err:
        return None, err
    if not isinstance(data, list):
        return None, "expected JSON array, got %s" % type(data).__name__
    return data, None


# ---------------------------------------------------------------------------
# 输入发现与文本抽取
# ---------------------------------------------------------------------------
def discover_inputs(input_dir: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """扫描输入目录：识别 case.json / task.md / 材料文件。不假设固定文件名。"""
    base = Path(input_dir)
    found: Dict[str, Any] = {"case_json": None, "task_md": None,
                             "materials": [], "other": []}
    if not base.exists():
        return found, "input dir missing: %s" % input_dir
    try:
        files = sorted(p for p in base.rglob("*") if p.is_file())
    except Exception as e:
        return found, "scan error: %s" % e
    for p in files:
        name = p.name.lower()
        if name == "case.json":
            found["case_json"] = str(p)
        elif name in SKIP_FILENAMES:
            found["task_md"] = str(p)
        elif p.suffix.lower() in MATERIAL_EXTS | PDF_EXTS:
            found["materials"].append(str(p))
        else:
            found["other"].append(str(p))
    return found, None


def pdf_to_text(path: str, work_dir: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """用 pdftotext 抽取 PDF 文本层；不可用时返回错误并降级（不崩溃）。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext not available"
    out = Path(work_dir) / (Path(path).stem + ".pdf.txt")
    try:
        proc = subprocess.run(
            [exe, "-layout", str(path), str(out)],
            capture_output=True, text=True, timeout=120)
    except Exception as e:
        return None, "pdftotext failed: %s" % e
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        return None, "pdftotext rc=%s: %s" % (
            proc.returncode, (proc.stderr or "").strip()[:200])
    return read_text_safe(str(out)), None


def locator(page_no: Optional[int], para_seq: int) -> str:
    pid = "P%04d" % para_seq
    return ("p%d ¶%s" % (page_no, pid)) if page_no else ("¶%s" % pid)


_HEADING_TITLE_RE = re.compile(r"^#{1,6}\s+(.+)$")
_NUM_HEADING_RE = re.compile(r"^\d+(\.\d+)*\s+\S")
_HEADING_WORDS = (
    "abstract", "introduction", "background", "related work", "methods",
    "materials and methods", "materials", "method", "experimental",
    "experiments", "results", "results and discussion", "discussion",
    "conclusion", "conclusions", "limitations", "limitation", "references",
    "bibliography", "appendix", "supplementary", "acknowledg", "highlights",
    "摘要", "引言", "绪论", "背景", "相关工作", "方法", "材料与方法",
    "实验", "结果", "结果与讨论", "讨论", "结论", "局限", "局限性",
    "参考文献", "附录", "致谢", "补充材料",
)


def _heading_title(s: str) -> str:
    m = _HEADING_TITLE_RE.match(s.strip())
    if m:
        return m.group(1).strip()[:80]
    return s.strip().rstrip(".")[:80]


def _is_heading_block(s: str) -> bool:
    t = s.strip()
    if not t or len(t) > 90:
        return False
    if _HEADING_TITLE_RE.match(t):
        return True
    head = _heading_title(t).lower()
    if any(head == w or head.startswith(w + " ") or head.endswith(": " + w)
           for w in _HEADING_WORDS):
        return True
    if _NUM_HEADING_RE.match(head) and "\n" not in t:
        return True
    return False


def _split_blocks(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: List[str] = []
    cur: List[str] = []
    for line in text.split("\n"):
        if not line.strip():
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line.rstrip())
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def split_paragraphs(text: str, src: str, has_pages: bool) -> List[Dict[str, Any]]:
    """把全文切成段落，记录 locator（p页 ¶P段落）、section、page、逐字文本。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pages = text.split("\f") if has_pages else [text]
    records: List[Dict[str, Any]] = []
    seq = 0
    section: Optional[str] = None
    for pi, page_text in enumerate(pages, start=1):
        page_no = pi if has_pages else None
        for block in _split_blocks(page_text):
            s = block.strip()
            if not s:
                continue
            if _is_heading_block(s):
                section = _heading_title(s)
                continue
            seq += 1
            records.append({
                "id": "P%04d" % seq,
                "src": src,
                "page": page_no,
                "section": section,
                "locator": locator(page_no, seq),
                "text": s,
            })
    return records


def build_section_map(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in records:
        sec = r["section"] or "(未分节)"
        item = out.setdefault(sec, {"section": sec, "paras": [], "pages": []})
        item["paras"].append(r["id"])
        if r["page"] is not None and r["page"] not in item["pages"]:
            item["pages"].append(r["page"])
    return list(out.values())


# ---------------------------------------------------------------------------
# task 元信息与输出契约
# ---------------------------------------------------------------------------
def load_task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "challenge_id": None, "task_title": None,
        "case_json": discovered.get("case_json"),
        "_parse_error": None,
    }
    if discovered.get("case_json"):
        data, err = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            meta["task_title"] = data.get("title") or data.get("task_title")
            meta["challenge_id"] = (data.get("challenge_id")
                                    or data.get("challengeId"))
            if err:
                meta["_parse_error"] = err
        else:
            meta["_parse_error"] = err or "case.json is not an object"
    if not meta["task_title"] and discovered.get("task_md"):
        r = read_text_safe(discovered["task_md"], max_bytes=200_000)
        for line in r["text"].splitlines()[:60]:
            ls = line.strip()
            if ls.startswith("#") and ls.lstrip("#").strip():
                meta["task_title"] = ls.lstrip("#").strip()
                break
    return meta


def derive_case_id(discovered: Dict[str, Any], task_meta: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            for key in ("id", "case_id", "caseId", "caseid"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return Path(discovered["case_json"]).stem
    return "unknown_case"


def extract_contract(discovered: Dict[str, Any]) -> Dict[str, Any]:
    """提取 case.json 的 output_contract（若有），缺省返回空 dict。"""
    if not discovered.get("case_json"):
        return {}
    data, _ = read_json_tolerant(discovered["case_json"])
    if not isinstance(data, dict):
        return {}
    c = data.get("output_contract")
    return dict(c) if isinstance(c, dict) else {}


# ---------------------------------------------------------------------------
# 结果骨架与报告
# ---------------------------------------------------------------------------
def build_skeleton(case_id: str, task_meta: Dict[str, Any], contract: Dict[str, Any],
                   sources: List[Dict[str, Any]], records: List[Dict[str, Any]],
                   status: str, errors: List[Dict[str, Any]],
                   degradations: List[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "case_id": case_id,
        "status": status,
        "task": {
            "challenge_id": task_meta.get("challenge_id"),
            "task_title": task_meta.get("task_title"),
        },
        "review": {
            "summary": "",
            "findings": [],
            "claims": [],
            "commitments": [],
            "coverage": {k: {"status": "skipped", "checked": 0, "note": ""}
                         for k in COVERAGE_DIMS},
            "section_map": build_section_map(records),
        },
        "sources": sources,
        "uncertainty": [],
        "pending_review": [],
        "pending_human": [],
        "limitations": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [{"ts": now_iso(), "event": "skeleton_written",
                        "note": "scaffold 初始骨架 status=%s" % status}],
        "runtime": {"seconds_elapsed": 0.0, "model_requests_used": 0,
                    "budget": {"requests": 64, "seconds": 900}},
    }
    # 原样回填 output_contract 的全部键（缺值已由 case.json 给出；未知键不删、不改名）
    for k, v in contract.items():
        result[k] = v
    result.setdefault("output_contract", dict(contract))
    return result


def write_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                    path: str) -> None:
    lines = ["# 语料库（供逐段审查，定位符可直接引用）", ""]
    lines.append("来源文件 %d 个，段落 %d 个。" % (len(sources), len(records)))
    lines.append("定位符格式：`p<页> ¶P<段落>`；无页码的纯文本省略 p 部分。")
    lines.append("引用原文必须逐字复制，不得改写。")
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
        if not recs:
            lines.append("(未抽取到段落)")
            lines.append("")
        for rec in recs:
            lines.append("### %s [%s]" % (rec["locator"], rec["section"] or "-"))
            lines.append(rec["text"])
            lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def render_report(data: Dict[str, Any]) -> str:
    review = data.get("review") or {}
    lines: List[str] = ["# 论文逻辑与论证审查报告", "",
                        "- case_id: %s" % data.get("case_id"),
                        "- 状态: %s" % data.get("status"), "",
                        "## 0. 总评", "",
                        review.get("summary") or "（尚未填写总评）", "",
                        "## 1. 问题清单（findings）", ""]
    findings = review.get("findings") or []
    if not findings:
        lines.append("（暂无记录）")
    for f in findings:
        if not isinstance(f, dict):
            continue
        lines.append("### %s — %s / %s" % (f.get("id", "-"), f.get("type", "-"), f.get("severity", "-")))
        lines.append("- 定位: %s" % (f.get("locator") or "-"))
        lines.append("- 状态/置信度: %s / %s" % (f.get("status", "-"), f.get("confidence", "-")))
        lines.append("- 逐字原文: %s" % (f.get("quote") or "-"))
        lines.append("- 主张: %s" % (f.get("claim") or "-"))
        lines.append("- 判断依据: %s" % (f.get("evidence") or "-"))
        lines.append("- 推理: %s" % (f.get("reasoning") or "-"))
        lines.append("- 修改建议: %s" % (f.get("suggestion") or "-"))
        lines.append("")
    lines += ["## 2. 主张清单（claims）", ""]
    claims = review.get("claims") or []
    if not claims:
        lines.append("（暂无记录）")
    for c in claims:
        if not isinstance(c, dict):
            continue
        lines.append("- %s [%s] %s — %s" % (
            c.get("id", "-"), c.get("speaker", "-"),
            (c.get("locator") or "-"), (c.get("verdict") or "-")))
        lines.append("  %s" % (c.get("text") or "-"))
    lines += ["", "## 3. 六类检查项覆盖情况", "",
              "| 维度 | 状态 | 检查数 | 说明 |", "|---|---|---|---|"]
    cov = review.get("coverage") or {}
    for k, label in COVERAGE_DIMS.items():
        v = cov.get(k) or {}
        lines.append("| %s (%s) | %s | %s | %s |" % (
            label, k, v.get("status", "-"), v.get("checked", 0), v.get("note", "-")))
    lines += ["", "## 4. 依据与边界（sources / errors / degradations）", ""]
    for s in (data.get("sources") or []):
        if isinstance(s, dict):
            lines.append("- %s (kind=%s, pages=%s, chars=%s, error=%s)" % (
                s.get("path", "-"), s.get("kind", "-"),
                s.get("n_pages", "-"), s.get("chars", 0), s.get("error", "-")))
    for e in (data.get("errors") or []):
        lines.append("- error: %s" % json.dumps(e, ensure_ascii=False))
    for d in (data.get("degradations") or []):
        lines.append("- degradation: %s" % json.dumps(d, ensure_ascii=False))
    lines += ["", "## 5. 待复核与限制", ""]
    for key, label in (("pending_review", "待复核"), ("pending_human", "需人工确认"),
                       ("uncertainty", "不确定项"), ("limitations", "限制")):
        items = data.get(key) or []
        lines.append("### %s (%s)" % (label, key))
        if not items:
            lines.append("（无）")
        for it in items:
            lines.append("- %s" % (json.dumps(it, ensure_ascii=False) if not isinstance(it, str) else it))
        lines.append("")
    lines += ["## 6. 变更记录（change_log）", ""]
    for cl in (data.get("change_log") or []):
        if isinstance(cl, dict):
            lines.append("- %s %s: %s" % (cl.get("ts", "-"), cl.get("event", "-"), cl.get("note", "")))
    lines.append("")
    return "\n".join(lines)


def write_meta(path: str, discovered: Dict[str, Any], task_meta: Dict[str, Any],
               n_records: int, case_id: str, status: str) -> None:
    obj = {
        "case_id": case_id,
        "status": status,
        "task_meta": {k: v for k, v in task_meta.items() if not k.startswith("_")},
        "discovered": discovered,
        "n_records": n_records,
        "written_at": now_iso(),
    }
    write_json_atomic(path, obj)


def normalize_items(arr: List[Any], kind: str) -> List[Dict[str, Any]]:
    defaults = (FINDING_DEFAULTS if kind == "findings"
                else CLAIM_DEFAULTS if kind == "claims"
                else COMMITMENT_DEFAULTS)
    prefix = _ITEM_PREFIX[kind]
    out: List[Dict[str, Any]] = []
    for i, x in enumerate(arr, start=1):
        d = dict(x) if isinstance(x, dict) else {}
        for k, v in defaults.items():
            d.setdefault(k, v)
        if not d.get("id"):
            d["id"] = "%s%03d" % (prefix, i)
        out.append(d)
    return out


def load_summary(arg: str) -> Optional[str]:
    p = Path(arg)
    if p.exists():
        r = read_text_safe(arg, max_bytes=50_000)
        return r["text"].strip()
    return arg.strip()
