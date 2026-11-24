# -*- coding: utf-8 -*-
"""review_core.py — 论文逻辑与论证审查 Skill 的共享核心（纯标准库）。

职责：
  1. 容错读取：坏 JSON / 坏编码 / BOM / 超长文件均降级，不崩溃。
  2. 输入发现：不假设固定文件名，扫描 /input 下的 case.json / task.md / materials。
  3. 文本抽取：txt / md / docx 直接读，PDF 尝试 pdftotext + pdfinfo（失败即降级）。
  4. 段落切分：逐段编号 P0001...，带页码与章节标签，供逐字引用。
  5. 契约骨架：result.json 的合法骨架 + output_contract 原样回填。
  6. 校验：selfcheck 用的不变量检查。
  7. 原子写盘：临时文件 + os.replace，避免留半截产物。

本模块只依赖 Python 标准库，无随机性，重复运行结果确定。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 枚举与常量
# ---------------------------------------------------------------------------
SIX_DIMENSIONS = (
    "factual_verification",   # 事实核验
    "citation_support",       # 引用支持
    "method_design",          # 方法设计
    "statistical_reporting",  # 统计报告
    "argument_chain",         # 论证链
    "academic_norms",         # 学术规范
)

RESULT_STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

FINDING_TYPES = (
    "factual_error",          # 事实错误
    "overclaim",              # 结论超出数据/证据强度
    "unsupported_citation",   # 引用不支持所引观点
    "method_mismatch",        # 研究方法回答不了研究问题
    "statistical_misreport",  # 统计口径与结论方向/描述不一致
    "argument_gap",           # 推理跳跃/隐含假设/替代解释未排除
    "causal_overreach",       # 相关性与因果性混用
    "commitment_unfulfilled", # 引言承诺未在结果/讨论交付
    "inconsistency",          # 摘要/正文/图表/口径前后不一致
    "norm_issue",             # 学术规范问题
    "advisory",               # 建议性意见（非缺陷）
)

SEVERITIES = ("critical", "major", "minor", "advisory")

FINDING_STATUSES = ("confirmed", "probable", "pending_review", "ruled_out")

CONFIDENCES = ("high", "medium", "low")

SPEAKERS = ("author", "cited", "assumption", "control")

CLAIM_VERDICTS = ("supported", "overclaimed", "unsupported", "pending")

COMMITMENT_STATUSES = ("delivered", "unfulfilled", "pending")

COVERAGE_STATUSES = ("done", "partial", "skipped")

MAX_FILE_BYTES = 2_000_000
MAX_MATERIALS = 300
MAX_PARAGRAPH_CHARS = 1100

MATERIAL_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".json", ".csv"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _reconfigure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_bytes_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Tuple[bytes, Optional[str]]:
    """读取文件字节；超过上限截断并记录 truncation。"""
    try:
        data = Path(path).read_bytes()
        if len(data) > max_bytes:
            return data[:max_bytes], "file exceeds %d bytes, truncated to %d bytes" % (
                max_bytes, max_bytes)
        return data, None
    except Exception as exc:  # noqa: BLE001
        return b"", "read failed: %s" % exc


def decode_text(data: bytes) -> Tuple[str, Optional[str]]:
    """按 utf-8-sig / utf-8 / gb18030 依次解码，最后 latin-1 兜底。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc), None
        except UnicodeDecodeError:
            continue
    try:
        return data.decode("latin-1"), "decoded with latin-1 fallback"
    except Exception as exc:  # noqa: BLE001
        return data.decode("utf-8", errors="replace"), "decode fallback failed: %s" % exc


def read_text_safe(path: str, max_bytes: int = MAX_FILE_BYTES) -> Dict[str, Any]:
    raw, read_err = read_bytes_safe(path, max_bytes)
    text, decode_err = decode_text(raw)
    return {
        "path": str(path),
        "text": text,
        "chars": len(text),
        "read_error": read_err,
        "decode_error": decode_err,
        "truncated": bool(read_err and "truncat" in (read_err or "").lower()),
    }


def read_json_tolerant(path: str) -> Tuple[Any, Optional[str]]:
    """读取 JSON，坏 JSON 返回 (None, 错误信息)，绝不抛异常。"""
    info = read_text_safe(path)
    if info["read_error"] and not info["text"]:
        return None, info["read_error"]
    try:
        return json.loads(info["text"]), None
    except Exception as exc:  # noqa: BLE001
        return None, "json parse failed: %s" % exc


def atomic_write_text(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix="." + p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# 外部工具调用（PDF）
# ---------------------------------------------------------------------------
def _run(cmd: List[str], timeout: int = 300) -> Tuple[int, bytes, bytes]:
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=False)
        return proc.returncode, proc.stdout or b"", proc.stderr or b""
    except FileNotFoundError:
        return 127, b"", b"tool not found"
    except Exception as exc:  # noqa: BLE001
        return 1, b"", str(exc).encode("utf-8", "replace")


def pdfinfo_pages(path: str) -> int:
    rc, out, _err = _run(["pdfinfo", str(path)], 60)
    if rc == 0:
        match = re.search(rb"Pages:\s+(\d+)", out)
        if match:
            return int(match.group(1))
    return 0


def pdf_to_text_pages(path: str) -> Dict[str, Any]:
    """抽取 PDF 文本，返回 pages 列表（尽力按页切分）。失败返回空 pages + error。"""
    n_pages = pdfinfo_pages(path)
    rc, out, err = _run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], 300)
    if rc != 0:
        rc, out, err = _run(["pdftotext", "-enc", "UTF-8", str(path), "-"], 300)
    if rc != 0:
        msg = (err or b"pdftotext failed").decode("utf-8", "replace").strip()
        return {"pages": [], "n_pages": n_pages, "error": msg or "pdftotext failed",
                "tool": None}

    text, _derr = decode_text(out)
    pages = text.split("\f")
    if n_pages and len(pages) >= n_pages - 1:
        pages = pages[:n_pages]
        while len(pages) < n_pages:
            pages.append("")
        return {"pages": pages, "n_pages": n_pages, "error": None, "tool": "pdftotext"}
    effective_pages = len(pages) if len(pages) > 1 else 0
    return {"pages": [text], "n_pages": n_pages or effective_pages, "error": None,
            "tool": "pdftotext"}


def docx_to_text(path: str) -> Dict[str, Any]:
    """极简 .docx 抽取（zipfile + 正则去标签），失败降级。"""
    try:
        import zipfile
        raw = Path(path).read_bytes()
        with zipfile.ZipFile(__import__("io").BytesIO(raw)) as zf:
            xml = zf.read("word/document.xml")
        body = xml.decode("utf-8", errors="replace")
        # 段落分隔
        body = re.sub(r"</w:p>", "\n\n", body)
        body = re.sub(r"<[^>]+>", "", body)
        import html
        text = html.unescape(body)
        return {"text": text, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"text": "", "error": "docx parse failed: %s" % exc}


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------
def discover_input(input_dir: str) -> Dict[str, Any]:
    """扫描输入目录，返回 case_json / task_md / materials（不假设固定文件名）。"""
    discovered: Dict[str, Any] = {"case_json": None, "task_md": None, "materials": []}
    base = Path(input_dir)
    if not base.exists():
        return discovered

    # 1) rglob 全量扫描（限制数量，防止意外大目录）
    count = 0
    for p in sorted(base.rglob("*")):
        if count >= 2000:
            break
        if not p.is_file():
            continue
        count += 1
        name = p.name.lower()
        if name == "case.json" and discovered["case_json"] is None:
            discovered["case_json"] = str(p)
        elif name == "task.md" and discovered["task_md"] is None:
            discovered["task_md"] = str(p)
        elif p.suffix.lower() in MATERIAL_EXTS and name not in ("case.json", "task.md"):
            if "/materials/" in p.as_posix() or p.parent.name.lower() == "materials":
                discovered["materials"].append(str(p))

    # 2) 顶层散落的材料文件（无 materials/ 目录时兜底）
    if not discovered["materials"] and base.exists():
        for p in sorted(base.glob("*")):
            if p.is_file() and p.suffix.lower() in MATERIAL_EXTS:
                if str(p) not in (discovered.get("case_json"), discovered.get("task_md")):
                    discovered["materials"].append(str(p))

    discovered["materials"] = discovered["materials"][:MAX_MATERIALS]
    return discovered


# ---------------------------------------------------------------------------
# 段落切分
# ---------------------------------------------------------------------------
_HEADING_RES = [
    re.compile(r"^#{1,6}\s+(.+)$"),
    re.compile(r"^(第[一二三四五六七八九十百0-9]+[章节部分])[、.\s:：]*(.*)$"),
    re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,2})[\s.、:：]+(.{2,80})$"),
    re.compile(
        r"^(Abstract|Introduction|Background|Methods?|Materials?|Results?|"
        r"Discussion|Conclusion|Limitations?|References?|Acknowledgments?|"
        r"Appendix|Supplementary)[:.\s]*$",
        re.IGNORECASE,
    ),
]


def _detect_heading(block: str) -> Optional[str]:
    """保守地识别标题：短块且命中标题模式，用于 section 标签，不影响正文。"""
    stripped = block.strip()
    if not stripped or len(stripped) > 90:
        return None
    for pat in _HEADING_RES:
        match = pat.match(stripped)
        if match:
            title = (match.group(1) or "").strip()
            if title:
                return title
    return None


def _split_long_block(block: str, limit: int = MAX_PARAGRAPH_CHARS) -> List[str]:
    """超长块按句边界拆成 ≤limit 的片段，保留可读性。"""
    block = block.strip()
    if not block:
        return []
    if len(block) <= limit:
        return [block]
    parts: List[str] = []
    current = ""
    for piece in re.split(r"(?<=[。．.!?;；])\s+", block):
        piece = piece.strip()
        if not piece:
            continue
        if len(current) + len(piece) + 1 <= limit:
            current = (current + " " + piece).strip() if current else piece
        else:
            if current:
                parts.append(current)
            while len(piece) > limit:
                parts.append(piece[:limit])
                piece = piece[limit:]
            current = piece
    if current:
        parts.append(current)
    return parts or [block]


def split_paragraphs(text: str, src: str, page: int = 0) -> List[Dict[str, Any]]:
    """把文本切分为带段落 ID 的记录；page>0 时带页码，否则仅段落号。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    records: List[Dict[str, Any]] = []
    section = ""
    for block in blocks:
        for sub in _split_long_block(block):
            heading = _detect_heading(sub)
            if heading:
                section = heading
            records.append({
                "src": src,
                "text": sub,
                "section": section,
                "page": page,
                "heading": bool(heading),
            })
    for idx, rec in enumerate(records, 1):
        rec["para_id"] = "P%04d" % idx
    return records


def locator(rec: Dict[str, Any]) -> str:
    if rec.get("page"):
        return "p%d \u00b6%s" % (rec["page"], rec["para_id"])
    return "\u00b6%s" % rec["para_id"]


def build_section_map(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    smap: List[Dict[str, Any]] = []
    index: Dict[str, int] = {}
    for rec in records:
        sec = rec.get("section") or "(未分节)"
        if sec not in index:
            index[sec] = len(smap)
            smap.append({"section": sec, "paras": [], "pages": []})
        entry = smap[index[sec]]
        entry["paras"].append(rec["para_id"])
        if rec.get("page") and rec["page"] not in entry["pages"]:
            entry["pages"].append(rec["page"])
    return smap


# ---------------------------------------------------------------------------
# 语料构建
# ---------------------------------------------------------------------------
def build_corpus(discovered: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """返回 (sources, records, degradations, errors)。"""
    sources: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    degradations: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    # case.json：仅作元数据源，不切段
    if discovered.get("case_json"):
        p = discovered["case_json"]
        data, err = read_json_tolerant(p)
        info = read_text_safe(p)
        sources.append({
            "path": p, "kind": "case", "n_pages": 0, "chars": info["chars"],
            "error": err, "tool": None, "truncated": info["truncated"],
        })
        if err:
            errors.append({"path": p, "error": err, "handled": True})

    # task.md：任务说明，切段供参考
    if discovered.get("task_md"):
        _add_text_source(discovered["task_md"], "task", sources, records, degradations)

    # 材料
    for p in discovered.get("materials", []):
        ext = Path(p).suffix.lower()
        if ext == ".pdf":
            _add_pdf_source(p, sources, records, degradations)
        elif ext in (".docx", ".doc"):
            res = docx_to_text(p)
            if res["text"]:
                _add_text_source(p, "material", sources, records, degradations, pre_text=res["text"])
            else:
                sources.append({"path": p, "kind": "material", "n_pages": 0, "chars": 0,
                                "error": res["error"], "tool": None, "truncated": False})
                degradations.append({"path": p, "error": res["error"],
                                     "fallback": "use platform office-renderer/ocr-tool", "handled": True})
        elif ext in (".txt", ".md", ".markdown", ".csv"):
            _add_text_source(p, "material", sources, records, degradations)
        elif ext == ".json":
            _add_text_source(p, "material", sources, records, degradations)
        else:
            sources.append({"path": p, "kind": "material", "n_pages": 0, "chars": 0,
                            "error": "unsupported extension %s" % ext, "tool": None, "truncated": False})
            degradations.append({"path": p, "error": "unsupported extension %s" % ext,
                                 "fallback": "convert to pdf/txt/md first", "handled": True})

    return sources, records, degradations, errors


def _add_text_source(path: str, kind: str, sources: List[Dict[str, Any]],
                     records: List[Dict[str, Any]], degradations: List[Dict[str, Any]],
                     pre_text: Optional[str] = None) -> None:
    info = read_text_safe(path) if pre_text is None else {
        "path": str(path), "text": pre_text, "chars": len(pre_text),
        "read_error": None, "decode_error": None, "truncated": False,
    }
    sources.append({
        "path": path, "kind": kind, "n_pages": 0, "chars": info["chars"],
        "error": info.get("read_error") or info.get("decode_error"), "tool": None,
        "truncated": info["truncated"],
    })
    if info["text"].strip():
        records.extend(split_paragraphs(info["text"], path, 0))
    else:
        degradations.append({"path": path, "error": "no text extracted", "handled": True})
    if info.get("decode_error"):
        degradations.append({"path": path, "error": info["decode_error"],
                             "fallback": "encoding fallback used, verify text integrity", "handled": True})


def _add_pdf_source(path: str, sources: List[Dict[str, Any]], records: List[Dict[str, Any]],
                    degradations: List[Dict[str, Any]]) -> None:
    res = pdf_to_text_pages(path)
    total_chars = sum(len(pg) for pg in res["pages"])
    sources.append({
        "path": path, "kind": "material", "n_pages": res["n_pages"], "chars": total_chars,
        "error": res.get("error"), "tool": res.get("tool"), "truncated": False,
    })
    if res.get("error"):
        degradations.append({
            "path": path, "error": res["error"],
            "fallback": "use platform pdf-text-tool / ocr-tool, then append text into work/corpus.md",
            "handled": True,
        })
    for idx, page_text in enumerate(res["pages"], 1):
        if page_text.strip():
            records.extend(split_paragraphs(page_text, path, idx))


# ---------------------------------------------------------------------------
# 语料与报告渲染
# ---------------------------------------------------------------------------
def write_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]], path: str) -> None:
    lines = ["# 语料库（供逐段审查，段落 ID 可直接引用）", ""]
    lines.append("来源 %d 个，段落 %d 个。" % (len(sources), len(records)))
    lines.append("定位符格式：`p<页> ¶P<段落>`；无页码的文本来源省略 `p<页>` 部分。")
    lines.append("引用原文必须逐字复制，不得改写、不得转述。")
    lines.append("")
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_src.setdefault(rec["src"], []).append(rec)
    for src in sources:
        key = src["path"]
        recs = by_src.get(key, [])
        lines.append("")
        lines.append("## 来源: %s" % key)
        lines.append("- kind=%s n_pages=%s chars=%s error=%s tool=%s" % (
            src.get("kind"), src.get("n_pages"), src.get("chars"), src.get("error"),
            src.get("tool")))
        lines.append("")
        for rec in recs:
            head = "### %s" % locator(rec)
            if rec.get("section"):
                head += " | %s" % rec["section"]
            lines.append(head)
            lines.append(rec["text"])
            lines.append("")
    atomic_write_text(path, "\n".join(lines))


def render_report(result: Dict[str, Any]) -> str:
    """由 result.json 确定性生成人类可读报告。"""
    review = result.get("review", {})
    lines: List[str] = []
    lines.append("# 论文逻辑与论证审查报告")
    lines.append("")
    lines.append("- case_id: %s" % result.get("case_id"))
    lines.append("- 状态: %s" % result.get("status"))
    lines.append("")

    lines.append("## 0. 总评")
    lines.append(review.get("summary") or "（未填写）")
    lines.append("")

    lines.append("## 1. 问题清单（findings）")
    findings = review.get("findings", [])
    if findings:
        for f in findings:
            lines.append("### %s [%s / %s] %s" % (f.get("id"), f.get("type"),
                                                   f.get("severity"), f.get("locator")))
            lines.append("- 定位: %s" % f.get("locator"))
            lines.append("- 逐字原文: > %s" % (f.get("quote") or ""))
            lines.append("- 被核查主张: %s" % f.get("claim"))
            lines.append("- 判断依据: %s" % f.get("evidence"))
            lines.append("- 推理（影响主结论/可修正/可复现）: %s" % f.get("reasoning"))
            lines.append("- 修改建议: %s" % f.get("suggestion"))
            lines.append("- 状态/置信度/speaker: %s / %s / %s" % (f.get("status"),
                                                                  f.get("confidence"),
                                                                  f.get("speaker")))
            lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 2. 主张清单（claims）")
    claims = review.get("claims", [])
    if claims:
        for c in claims:
            lines.append("- %s [%s] %s -> %s" % (c.get("id"), c.get("speaker"),
                                                  c.get("locator"), c.get("verdict")))
            lines.append("  - 主张: %s" % c.get("text"))
            lines.append("  - 证据: %s" % c.get("evidence"))
            lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 3. 承诺—交付对照（commitments）")
    commitments = review.get("commitments", [])
    if commitments:
        for k in commitments:
            lines.append("- %s [%s] %s -> %s" % (k.get("id"), k.get("status"),
                                                  k.get("locator"), k.get("delivered_at")))
            lines.append("  - 承诺: %s" % k.get("promise"))
            lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 4. 六类检查项覆盖情况")
    lines.append("| 维度 | 状态 | 检查数 | 说明 |")
    lines.append("|---|---|---|---|")
    coverage = review.get("coverage", {})
    for dim in SIX_DIMENSIONS:
        c = coverage.get(dim, {}) if isinstance(coverage, dict) else {}
        lines.append("| %s | %s | %s | %s |" % (dim, c.get("status", "skipped"),
                                                 c.get("checked", 0),
                                                 (c.get("note") or c.get("reason") or "")))
    lines.append("")

    lines.append("## 5. 不确定性与待复核（uncertainty / pending_review）")
    uncertainty = result.get("uncertainty", [])
    if uncertainty:
        for u in uncertainty:
            lines.append("- %s [%s] scope=%s" % (u.get("id"), u.get("kind"), u.get("scope")))
            lines.append("  - 事项: %s" % u.get("statement"))
            lines.append("  - 原因: %s" % u.get("reason"))
            lines.append("  - 影响: %s" % u.get("impact"))
            lines.append("  - 消除方式: %s" % u.get("resolution"))
            lines.append("")
    pending = result.get("pending_review", [])
    if pending:
        lines.append("待复核项（pending_review）：")
        for u in pending:
            lines.append("- %s [%s] scope=%s: %s" % (u.get("id"), u.get("kind"),
                                                      u.get("scope"), u.get("statement")))
            lines.append("")
    if not uncertainty and not pending:
        lines.append("（无）")
        lines.append("")

    lines.append("## 6. 依据与边界（limitations）")
    limitations = result.get("limitations", [])
    if limitations:
        for li in limitations:
            lines.append("- %s [%s] scope=%s: %s（影响: %s；消除: %s）" % (
                li.get("id"), li.get("kind"), li.get("scope"), li.get("statement"),
                li.get("impact"), li.get("resolution")))
            lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 7. 降级与错误")
    degradations = result.get("degradations", [])
    errors = result.get("errors", [])
    if degradations:
        lines.append("降级（degradations）：")
        for d in degradations:
            lines.append("- %s: %s" % (d.get("path"), d.get("error")))
        lines.append("")
    if errors:
        lines.append("错误（errors）：")
        for e in errors:
            lines.append("- %s: %s" % (e.get("path"), e.get("error")))
        lines.append("")
    if not degradations and not errors:
        lines.append("（无）")
        lines.append("")

    lines.append("## 8. 变更记录")
    for entry in result.get("change_log", []):
        lines.append("- %s [%s] %s: %s" % (entry.get("ts"), entry.get("event"),
                                            entry.get("status"), entry.get("note")))
    lines.append("")

    lines.append("## 9. 来源")
    for src in result.get("sources", []):
        lines.append("- %s (kind=%s, pages=%s, chars=%s%s)" % (
            src.get("path"), src.get("kind"), src.get("n_pages"), src.get("chars"),
            (" error=%s" % src.get("error")) if src.get("error") else ""))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 骨架与契约
# ---------------------------------------------------------------------------
def load_task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None, "case_json": None}
    if discovered.get("case_json"):
        data, err = read_json_tolerant(discovered["case_json"])
        meta["case_json"] = discovered["case_json"]
        meta["task_title"] = None
        meta["challenge_id"] = None
        meta["_parse_error"] = err
        if isinstance(data, dict):
            meta["task_title"] = data.get("title") or data.get("task_title")
            meta["challenge_id"] = (data.get("challenge_id") or data.get("challengeId")
                                    or data.get("id"))
    if discovered.get("task_md"):
        meta["task_md"] = discovered["task_md"]
        info = read_text_safe(discovered["task_md"], max_bytes=200_000)
        for line in info["text"].splitlines()[:60]:
            stripped = line.strip()
            if stripped.startswith("#") and stripped.lstrip("#").strip():
                meta.setdefault("task_title", stripped.lstrip("#").strip())
                break
    return meta


def derive_case_id(discovered: Dict[str, Any], task_meta: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            for key in ("id", "case_id", "caseId", "caseid"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    return val
        return Path(discovered["case_json"]).stem
    return "unknown_case"


def contract_extra(discovered: Dict[str, Any]) -> Dict[str, Any]:
    """case.json 显式给出 output_contract 时原样回填其键（缺值 null/[]）。"""
    if not discovered.get("case_json"):
        return {}
    data, _ = read_json_tolerant(discovered["case_json"])
    if not isinstance(data, dict):
        return {}
    contract = data.get("output_contract")
    if isinstance(contract, dict):
        return dict(contract)
    # 某些案例把字段名写成 outputContract
    contract = data.get("outputContract")
    return dict(contract) if isinstance(contract, dict) else {}


def build_skeleton(case_id: str, task_meta: Dict[str, Any], sources: List[Dict[str, Any]],
                   contract: Dict[str, Any], degradations: List[Dict[str, Any]],
                   errors: List[Dict[str, Any]]) -> Dict[str, Any]:
    has_material = any(s.get("kind") == "material" and not s.get("error") for s in sources)
    status = "partial" if has_material else "insufficient_evidence"
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
            "coverage": {
                dim: {"status": "skipped", "checked": 0, "note": "骨架阶段未开始",
                      "reason": "scaffold-not-started"}
                for dim in SIX_DIMENSIONS
            },
            "section_map": [],
        },
        "sources": sources,
        "uncertainty": [],
        "pending_review": [],
        "limitations": [],
        "errors": errors,
        "degradations": degradations,
        "change_log": [{
            "ts": utcnow(), "event": "scaffold", "note": "结果骨架已落盘", "status": status,
        }],
        "runtime": {
            "seconds_elapsed": 0.0, "model_requests_used": 0,
            "budget": {"requests": 64, "seconds": 900},
        },
    }
    # output_contract 原样回填：不改名、不删未知键，缺值给 null
    for key, val in contract.items():
        if key not in result:
            result[key] = val if val not in (None, "", {}, []) else None

    # 无可用材料时：显式记录 limitation，保证 insufficient_evidence 自洽
    if status == "insufficient_evidence":
        result["limitations"].append({
            "id": "L000",
            "scope": "whole-review",
            "kind": "missing_material",
            "statement": "未发现任何可解析的科研材料（/input 为空、无 materials，或文本抽取全部失败）",
            "reason": "discover_input 未找到材料文件，或所有材料均无文本层/抽取失败",
            "impact": "六类检查项全部 skipped，无法形成任何 finding/claim",
            "resolution": "补齐材料后重新 scaffold；扫描版 PDF 先用平台 pdf-text-tool / ocr-tool 抽取并补入 work/corpus.md",
        })
    return result


# ---------------------------------------------------------------------------
# 校验（selfcheck）
# ---------------------------------------------------------------------------
def validate_result(result: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not isinstance(result, dict):
        return ["result is not a JSON object"]

    if not result.get("case_id"):
        issues.append("case_id 缺失或为空")
    if result.get("status") not in RESULT_STATUSES:
        issues.append("status 非法: %r" % result.get("status"))

    review = result.get("review")
    if not isinstance(review, dict):
        issues.append("review 缺失或非对象")
        return issues

    findings = review.get("findings")
    if not isinstance(findings, list):
        issues.append("review.findings 非数组")
    else:
        for f in findings:
            fid = f.get("id", "?")
            if not isinstance(f, dict):
                issues.append("finding %s 非对象" % fid)
                continue
            for key in ("id", "type", "severity", "locator", "quote", "claim",
                        "evidence", "reasoning", "suggestion", "status", "confidence"):
                if not f.get(key):
                    issues.append("finding %s 缺字段 %s" % (fid, key))
            if f.get("type") not in FINDING_TYPES:
                issues.append("finding %s type 非法: %r" % (fid, f.get("type")))
            if f.get("severity") not in SEVERITIES:
                issues.append("finding %s severity 非法: %r" % (fid, f.get("severity")))
            if f.get("status") not in FINDING_STATUSES:
                issues.append("finding %s status 非法: %r" % (fid, f.get("status")))
            if f.get("confidence") not in CONFIDENCES:
                issues.append("finding %s confidence 非法: %r" % (fid, f.get("confidence")))

    coverage = review.get("coverage")
    if not isinstance(coverage, dict):
        issues.append("review.coverage 缺失或非对象")
    else:
        for dim in SIX_DIMENSIONS:
            if dim not in coverage:
                issues.append("coverage 缺维度 %s" % dim)
                continue
            c = coverage[dim]
            if not isinstance(c, dict) or c.get("status") not in COVERAGE_STATUSES:
                issues.append("coverage %s status 非法" % dim)
            elif c.get("status") in ("partial", "skipped") and not (c.get("note") or c.get("reason")):
                issues.append("coverage %s 为 partial/skipped 但缺 note/reason" % dim)

    for key in ("claims", "commitments", "section_map"):
        if not isinstance(review.get(key), list):
            issues.append("review.%s 非数组" % key)

    for key, expect_list in (("sources", True), ("uncertainty", True), ("pending_review", True),
                             ("limitations", True), ("errors", True), ("degradations", True),
                             ("change_log", True), ("runtime", False)):
        if key not in result:
            issues.append("顶层缺字段 %s" % key)
        elif expect_list and not isinstance(result[key], list):
            issues.append("顶层 %s 非数组" % key)
        elif not expect_list and not isinstance(result[key], dict):
            issues.append("顶层 %s 非对象" % key)

    for arr_name in ("uncertainty", "pending_review", "limitations"):
        for u in result.get(arr_name, []):
            uid = u.get("id", "?") if isinstance(u, dict) else "?"
            if not isinstance(u, dict):
                issues.append("%s 中 %s 非对象" % (arr_name, uid))
                continue
            for key in ("id", "reason", "impact"):
                if not u.get(key):
                    issues.append("%s %s 缺字段 %s" % (arr_name, uid, key))

    if result.get("status") == "insufficient_evidence":
        if not result.get("limitations"):
            issues.append("status=insufficient_evidence 时必须给出 limitations")

    return issues


def validate_report(path: str, case_id: str) -> List[str]:
    issues: List[str] = []
    p = Path(path)
    if not p.exists():
        issues.append("report.md 不存在: %s" % path)
        return issues
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        issues.append("report.md 读取失败: %s" % exc)
        return issues
    if not text.strip():
        issues.append("report.md 为空")
    if case_id and ("case_id" not in text or case_id not in text):
        issues.append("report.md 缺少 case_id 标识")
    return issues
