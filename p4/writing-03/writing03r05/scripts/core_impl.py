#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 学术格式与模板适配 Skill 的共享实现（仅标准库）。

提供：目录解析、材料发现与分类、契约键解析与回填、文本/JSON 容错读取、
段落 locator、规范化、逐字引用命中校验、通用检查与文件写入。

不依赖任何第三方库；所有函数失败时降级而非崩溃。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

# 顶层契约字段族
ROOT_FIELDS = ["case_id", "deliverable", "fact_source_map", "version_boundary"]
LIST_FIELDS = {
    "claims", "uncertainties", "sources", "evidence", "pending_human", "errors",
    "degradations", "changes", "preserved_facts", "evidence_map", "change_log",
    "workorder", "format_compliance", "fact_source_map", "conflicts",
}
DICT_FIELDS = {"result", "runtime", "coverage", "deliverable_meta", "uncertainty", "version_boundary"}
STATUS = {"answered", "partial", "insufficient_evidence", "input_error"}
CHANGE_ACTIONS = {"rewrite", "translate", "move", "merge", "split", "annotate", "format", "create", "delete", "scaffold", "verify"}
TEXT_KINDS = {"text", "table", "csv", "markdown", "md", "tex", "latex", "json", "yaml", "txt"}
BINARY_KINDS = {"pdf", "docx", "doc", "pptx", "xlsx", "png", "jpg", "jpeg", "svg", "gif", "webp", "zip"}

_MAX_TEXT_BYTES = 2 * 1024 * 1024  # 单文件文本读取上限 2MB


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def read_text(path: Path, max_bytes: int = _MAX_TEXT_BYTES) -> tuple[str, str]:
    """容错读取文本；返回 (文本, 错误信息)。BOM/坏编码/超大文件均降级。"""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return "", f"read failed: {exc.__class__.__name__}: {exc}"
    if len(data) > max_bytes:
        data = data[:max_bytes]
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding), ""
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "fallback replace decoding"


def read_json(path: Path) -> tuple[dict | list | None, str]:
    """容错读取 JSON；坏 JSON 返回 (None, 错误)。"""
    if not path.is_file():
        return None, f"missing: {path}"
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as exc:
        return None, f"bad JSON: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{exc.__class__.__name__}: {exc}"


def write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(str(text).encode("utf-8"))
        return True
    except OSError:
        return False


def write_json(path: Path, obj) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


def norm(text: str) -> str:
    """小写 + 空白折叠，用于引用命中的容错匹配。"""
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def paragraphs(text: str) -> list[tuple[str, str]]:
    """把文本切成带 locator 的段落；locator 形如 para.1/para.2/...。"""
    raw = re.split(r"\n\s*\n|\r\n\s*\r\n", str(text or ""))
    out = []
    for idx, chunk in enumerate(raw, 1):
        line = re.sub(r"\s+", " ", chunk).strip()
        if line:
            out.append((f"para.{idx}", line))
    return out


def classify(path: Path, rel_path: str) -> dict:
    suffix = path.suffix.lower().lstrip(".")
    kind = "binary" if suffix in BINARY_KINDS else ("text" if suffix in TEXT_KINDS else "other")
    item = {"source_id": "", "rel_path": rel_path, "path": str(path), "suffix": suffix or "",
            "kind": kind, "size": path.stat().st_size if path.is_file() else 0}
    if suffix == "csv":
        item["kind"] = "table"
    return item


def discover(root: Path) -> list[dict]:
    """递归发现材料并编号 S1..Sn；先文本/表格，后二进制。"""
    files = sorted(p for p in root.rglob("*") if p.is_file())
    text_items, other_items = [], []
    for path in files:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.name
        if "case.json" == path.name or "task.md" == path.name:
            continue
        item = classify(path, rel)
        if item["kind"] in ("text", "table"):
            text_items.append(item)
        else:
            other_items.append(item)
    ordered = text_items + other_items
    for idx, item in enumerate(ordered, 1):
        item["source_id"] = f"S{idx}"
    return ordered


def quote_hit(corpus_norm: str, quote: str, tolerance: int = 0) -> bool:
    """校验 quote 是否能在规范化全文命中（忽略大小写与连续空白）。"""
    q = norm(quote)
    if not q:
        return False
    if q in corpus_norm:
        return True
    if tolerance > 0:
        n = max(0, len(q) - tolerance)
        for start in range(0, len(q) - n + 1, max(1, n // 3 or 1)):
            if q[start:start + n] in corpus_norm:
                return True
    return False


def contract_keys(case: dict) -> tuple[list[str], bool]:
    """解析 output_contract，返回 (键列表, 是否使用兜底)。支持 dict/list 两种形态。"""
    raw = case.get("output_contract") if isinstance(case, dict) else None
    keys: list[str] = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    val = item.get(cand)
                    if isinstance(val, str) and val.strip():
                        keys.append(val.strip())
                        break
    if not keys:
        return list(ROOT_FIELDS), True
    ordered, seen = [], set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered, False


def default_value(key: str):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    return None


def fill_contract(keys: list[str], payload: dict) -> tuple[dict, list[str]]:
    filled, missing = {}, []
    for key in keys:
        if key in payload:
            filled[key] = payload[key]
        else:
            filled[key] = default_value(key)
            missing.append(key)
    return filled, missing


def probe_tools() -> dict:
    tools = {"python": shutil.which("python") or shutil.which("python3") or "",
             "latex": bool(shutil.which("pdflatex") or shutil.which("xelatex") or shutil.which("latex")),
             "pandoc": bool(shutil.which("pandoc")),
             "libreoffice": bool(shutil.which("soffice") or shutil.which("libreoffice"))}
    modules = {}
    for name in ("docx", "openpyxl", "pptx", "PIL", "numpy", "pandas", "matplotlib", "fitz"):
        try:
            __import__(name)
            modules[name] = True
        except Exception:
            modules[name] = False
    tools["python_modules"] = modules
    return tools


def validate_common(draft: dict, corpus: dict, output: Path) -> tuple[list[dict], int, int]:
    """通用机械校验：事实保全、证据映射、改动留痕、引用命中。"""
    items: list[dict] = []

    def add(level, check_id, detail):
        items.append({"level": level, "id": check_id, "detail": detail})

    facts = draft.get("preserved_facts") if isinstance(draft, dict) else []
    evidence = draft.get("evidence_map") if isinstance(draft, dict) else []
    changes = draft.get("change_log") if isinstance(draft, dict) else []
    text = str(draft.get("deliverable_text") or "") if isinstance(draft, dict) else ""

    if not isinstance(draft, dict):
        add("FAIL", "draft", "工作稿不是对象")
        return items, 1, 0

    add("PASS" if text.strip() else "FAIL", "deliverable_text", "正文为空" if not text.strip() else "正文存在")

    for idx, fact in enumerate(facts if isinstance(facts, list) else [], 1):
        if not isinstance(fact, dict):
            add("WARN", f"fact.{idx}", "事实项非对象")
            continue
        if not fact.get("fact_id") or not str(fact.get("value", "")).strip():
            add("FAIL", f"fact.{idx}", "缺 fact_id 或 value")
            continue
        src = str(fact.get("source_id") or "")
        quote = str(fact.get("quote") or "")
        locator = str(fact.get("locator") or "")
        if src and quote and src in corpus:
            add("PASS" if quote_hit(corpus[src], quote) else "FAIL",
                f"fact.{idx}", f"{fact.get('fact_id')}: quote 命中 {src}" if quote_hit(corpus[src], quote) else f"{fact.get('fact_id')}: quote 未在 {src} 命中")
        elif not src or not quote:
            add("WARN", f"fact.{idx}", f"{fact.get('fact_id')}: 缺 source_id 或 quote")

    for idx, item in enumerate(evidence if isinstance(evidence, list) else [], 1):
        if not isinstance(item, dict):
            add("WARN", f"evidence.{idx}", "证据项非对象")
            continue
        src = str(item.get("source_id") or "")
        quote = str(item.get("quote") or "")
        if src and quote and src in corpus:
            add("PASS" if quote_hit(corpus[src], quote) else "FAIL",
                f"evidence.{idx}", f"{item.get('claim_id') or '?'}: quote 命中 {src}" if quote_hit(corpus[src], quote) else f"{item.get('claim_id') or '?'}: quote 未命中 {src}")

    seen_change_ids = set()
    for idx, change in enumerate(changes if isinstance(changes, list) else [], 1):
        if not isinstance(change, dict):
            add("WARN", f"change.{idx}", "改动项非对象")
            continue
        cid = change.get("id") or change.get("ts")
        if cid in seen_change_ids:
            add("WARN", f"change.{idx}", "改动记录 id 重复")
        seen_change_ids.add(cid)
        if change.get("action") not in CHANGE_ACTIONS:
            add("WARN", f"change.{idx}", "action 不在约定集合")

    fail = sum(1 for i in items if i["level"] == "FAIL")
    warn = sum(1 for i in items if i["level"] == "WARN")
    return items, fail, warn


def markdown_report(lines: list[str], title: str) -> str:
    return "# " + title + "\n\n" + "\n".join(lines) + "\n"
