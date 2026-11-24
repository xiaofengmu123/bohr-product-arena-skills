#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-01 通用工具库：只依赖 Python 标准库。

职责边界：
  - 输入发现与文本读取（utf-8-sig 容错、大小上限、坏编码降级）
  - output_contract 动态解析（不假设固定字段名，未知字段保留原样回填）
  - result.json 骨架生成（先落盘合法 partial，再增量填充）
  - 证据条目校验（每页 claim/role + locator + 逐字 quote）
  - PDF 页数启发式统计（标注为启发式，失败即 degradation）
  - 结果契约校验（所有 output_contract 字段齐全、主产物文件存在）
本文件不含任务科学判断，也不生成任何内容；内容由执行 Agent 依据真实材料写入。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"
TASK_LABEL = "论文转学术 PPT（slides-01）"

# 统一信封字段：无论 output_contract 是否声明，都写入 result.json。
# 运行契约（task.md Runtime contract）显式要求 status/sources/uncertainty/change records。
ENVELOPE_FIELDS = [
    "status",
    "sources",
    "uncertainty",
    "pending_human",
    "errors",
    "degradations",
    "change_log",
    "runtime",
]

# 题目“Skill 运行后的输出要求”三张表对应的规范回退字段（仅在 output_contract 缺失时使用）。
CANONICAL_FIELDS = [
    "case_id",
    "editable_source",
    "evidence_map",
    "render_record",
    "compliance_checks",
    "exceptions",
]

STATUS_SET = {"ok", "partial", "insufficient_evidence", "input_error"}
ROLES = {"title", "background", "method", "result", "discussion",
         "limitation", "conclusion", "other"}

# 关键字 → 默认值类型（用于未知 output_contract 字段的回填）。
_LIST_HINTS = ("map", "checks", "sources", "uncertainty", "pending", "errors",
               "degradations", "changes", "change", "exceptions", "slides",
               "pages", "records", "list", "items", "content")
_DICT_HINTS = ("source", "artifact", "record", "runtime", "template", "meta",
               "info", "export", "editable", "render", "font")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _mk_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path):
    """读取 JSON；返回 (obj, err)。坏 JSON / 坏编码 / 空文件均返回 err 而不抛异常。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    if not raw.strip():
        return None, f"{Path(path).name} 为空文件"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    else:
        return None, f"{Path(path).name} 无法以常见编码解码"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{Path(path).name} JSON 解析失败: {exc}"


def write_json(path: Path, obj) -> bool:
    try:
        _mk_parent(path)
        path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return True
    except OSError as exc:
        print(f"[error] write failed {path}: {exc.__class__.__name__}", file=sys.stderr)
        return False


def write_text(path: Path, text: str) -> bool:
    try:
        _mk_parent(path)
        path.write_bytes(str(text).encode("utf-8"))
        return True
    except OSError as exc:
        print(f"[error] write failed {path}: {exc.__class__.__name__}", file=sys.stderr)
        return False


def ensure_dir(path: Path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def read_text_file(path: Path, max_bytes: int = 2_000_000):
    """读取文本材料；超限跳过，坏编码按 latin-1 容错。返回 (text, err)。"""
    try:
        size = Path(path).stat().st_size
    except OSError as exc:
        return None, f"{Path(path).name} stat 失败: {exc.__class__.__name__}"
    if size > max_bytes:
        return None, f"{Path(path).name} 超过 {max_bytes} 字节上限（{size} 字节），已跳过"
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, ValueError):
            continue
    return None, f"{Path(path).name} 无法解码"


def _detect_kind(suffix: str) -> str:
    s = suffix.lower()
    if s in {".pdf"}:
        return "pdf"
    if s in {".pptx", ".ppt", ".odp"}:
        return "slide_template"
    if s in {".md", ".txt", ".tex", ".json", ".yaml", ".yml", ".csv", ".xml"}:
        return "text"
    if s in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"}:
        return "image"
    if s in {".docx", ".doc", ".xlsx"}:
        return "office"
    return "other"


def discover_materials(input_root: Path) -> list:
    """扫描输入目录，返回材料清单（不含 case.json 本身）。"""
    materials = []
    if not input_root.is_dir():
        return materials
    for path in sorted(input_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_root).as_posix()
        if rel == "case.json":
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        materials.append({
            "path": path,
            "rel": rel,
            "bytes": size,
            "kind": _detect_kind(path.suffix),
        })
    return materials


def pdf_page_count(path: Path):
    """启发式统计 PDF 页数：匹配 /Type /Page（排除 /Pages）。返回 (count, err)。

    该方法对压缩对象流（object streams）可能漏计，属已知限制；返回 0 且文件为
    合法 PDF 时，调用方应把“页数需用渲染工具复核”写入 degradations。
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    if not raw.startswith(b"%PDF"):
        return None, f"{Path(path).name} 不是合法 PDF（缺少 %PDF 头）"
    count = len(re.findall(rb"/Type\s*/Page(?![sA-Za-z])", raw))
    return count, None


def extract_contract_keys(case: dict):
    """从 case.json 动态解析 output_contract 字段名。返回 (keys, err)。

    支持三种形态：dict（取 key 顺序）、list[str]、list[dict]（取 name/key/field 任一）。
    缺 output_contract 时回退到 CANONICAL_FIELDS 并给出说明。
    """
    if not isinstance(case, dict):
        return list(CANONICAL_FIELDS), "case.json 顶层不是对象，回退规范字段"
    raw = case.get("output_contract")
    keys = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    v = item.get(cand)
                    if isinstance(v, str) and v.strip():
                        keys.append(v.strip())
                        break
    ordered, seen = [], set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
    if ordered:
        return ordered, None
    return list(CANONICAL_FIELDS), "output_contract 缺失或为空，回退规范字段"


def default_value_for(key: str):
    key_l = str(key).lower()
    if any(h in key_l for h in _LIST_HINTS):
        return []
    if any(h in key_l for h in _DICT_HINTS):
        return {}
    return None


def fill_contract(keys: list, payload: dict):
    """按契约字段原样回填；缺失字段给默认值（null/[]/{}），不改名、不删未知字段。"""
    filled, missing = {}, []
    for key in keys:
        if key in payload:
            filled[key] = payload[key]
        else:
            filled[key] = default_value_for(key)
            missing.append(key)
    return filled, missing


def build_skeleton(case_id: str, keys: list, status: str = "partial", errors=None):
    """生成一版立即可交付的 partial 骨架（所有契约字段齐备 + 统一信封）。"""
    skeleton = {"case_id": case_id, "status": status}
    filled, missing = fill_contract(keys, skeleton)
    skeleton.update(filled)
    for env in ENVELOPE_FIELDS:
        if env not in skeleton:
            skeleton[env] = default_value_for(env)
    if missing:
        skeleton.setdefault("degradations", []).append(
            f"契约字段未提供值，已用默认值占位: {', '.join(missing)}"
        )
    if errors:
        skeleton.setdefault("errors", []).extend(errors)
    skeleton.setdefault("runtime", {})
    skeleton["runtime"]["started_at"] = now_iso()
    return skeleton


def validate_draft(draft: dict):
    """校验页级草稿：每页必须有 page/claim/role，且至少一条 evidence 带 locator + quote。"""
    issues = []
    if not isinstance(draft, dict):
        return ["draft.json 顶层不是对象"]
    pages = draft.get("slides", draft.get("pages"))
    if not isinstance(pages, list) or not pages:
        issues.append("draft.json 无 slides/pages 列表或列表为空")
        return issues
    for i, page in enumerate(pages):
        tag = page.get("page", i + 1)
        if not isinstance(page, dict):
            issues.append(f"第 {tag} 页不是对象")
            continue
        for req in ("page", "claim", "role"):
            if not str(page.get(req, "")).strip():
                issues.append(f"第 {tag} 页缺字段 {req}")
        if str(page.get("role", "")).strip() and page.get("role") not in ROLES:
            issues.append(f"第 {tag} 页 role 不在允许集合: {page.get('role')}")
        evs = page.get("evidence", [])
        if not isinstance(evs, list) or not evs:
            issues.append(f"第 {tag} 页无 evidence")
            continue
        for j, ev in enumerate(evs):
            if not isinstance(ev, dict):
                issues.append(f"第 {tag} 页 evidence[{j}] 不是对象")
                continue
            if not str(ev.get("locator", "")).strip():
                issues.append(f"第 {tag} 页 evidence[{j}] 缺 locator")
            if not str(ev.get("quote", "")).strip():
                issues.append(f"第 {tag} 页 evidence[{j}] 缺 quote（须逐字命中材料）")
    return issues


def validate_result(result: dict, keys: list, output_dir: Path = None):
    """校验 result.json：契约字段齐全、状态合法、主产物存在、证据条目完整。"""
    issues = []
    if not isinstance(result, dict):
        return ["result.json 顶层不是对象"]
    for key in keys:
        if key not in result:
            issues.append(f"result.json 缺契约字段 {key}")
    if result.get("status") not in STATUS_SET:
        issues.append(f"status 取值非法: {result.get('status')!r}")
    if result.get("status") != "input_error" and not result.get("case_id"):
        issues.append("case_id 为空")
    evs = result.get("evidence_map", result.get("content_map"))
    if isinstance(evs, list) and evs:
        issues.extend(validate_draft({"slides": evs}))
    if output_dir is not None:
        for key in ("editable_source",):
            src = result.get(key)
            if isinstance(src, dict) and src.get("path"):
                p = Path(src["path"])
                if not p.is_absolute():
                    p = output_dir / p
                if not p.exists():
                    issues.append(f"主产物文件不存在: {p}")
    return issues


def is_zip_pptx(path: Path) -> bool:
    """粗检 pptx：是 zip 且含 [Content_Types].xml。"""
    try:
        import zipfile
        if not zipfile.is_zipfile(str(path)):
            return False
        with zipfile.ZipFile(str(path)) as zf:
            names = zf.namelist()
        return "[Content_Types].xml" in names and any(
            n.startswith("ppt/slides/slide") and n.endswith(".xml") for n in names
        )
    except Exception:
        return False
