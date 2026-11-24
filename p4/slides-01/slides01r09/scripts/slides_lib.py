#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-01 核心库（纯 Python 标准库）。

职责边界（本文件不做任何科学判断，也不生成内容）：
  1. 输入发现与文本读取：utf-8-sig 容错、2MB 单文件上限、坏编码降级。
  2. output_contract 动态解析：不假设固定字段名；dict / list[str] / list[dict] 三态。
  3. result.json 骨架：先落盘一版合法的 partial，再增量填充。
  4. 证据校验：每页 page/claim/role + evidence[{locator, quote}]，quote 须逐字。
  5. PDF 页数启发式统计（标注为启发式）；pptx 结构检查与统计。
  6. 纯标准库 PPTX：有模板真继承（复制包、追加 slide、复用其 layout/master/theme），
     无模板时生成最小合法 16:9 空白 deck。

内容写作、主张抽取、证据定位由执行 Agent 依据真实材料完成。
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"
TASK_LABEL = "论文转学术 PPT（slides-01）"

STATUS_SET = {"ok", "partial", "insufficient_evidence", "input_error"}
ROLES = {"title", "background", "method", "result", "discussion",
         "limitation", "conclusion", "other"}

# 统一信封：无论 output_contract 是否声明，result.json 都必须携带。
# task.md Runtime contract 显式要求 status / sources / uncertainty / change records。
ENVELOPE_FIELDS = [
    "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "change_log", "runtime",
]

# 仅在 output_contract 缺失/为空时的规范回退字段（对应题目三张输出表）。
CANONICAL_FIELDS = [
    "case_id", "editable_source", "evidence_map",
    "render_record", "compliance_checks", "exceptions",
]

_LIST_HINTS = ("map", "checks", "sources", "uncertainty", "pending", "errors",
               "degradations", "change", "exceptions", "slides", "pages",
               "records", "items", "content")
_DICT_HINTS = ("source", "artifact", "record", "runtime", "template", "meta",
               "info", "export", "editable", "render", "font")

_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'


# ---------------------------------------------------------------------------
# 基础 IO
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_dir(path: Path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def read_json(path: Path):
    """读取 JSON；坏 JSON / 坏编码 / 空文件返回 err，绝不抛异常。返回 (obj, err)。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    if not raw.strip():
        return None, f"{Path(path).name} 为空文件"
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if text is None:
        return None, f"{Path(path).name} 无法以常见编码解码"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{Path(path).name} JSON 解析失败: {exc}"


def write_json(path: Path, obj) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return True
    except OSError as exc:
        print(f"[error] write failed {path}: {exc.__class__.__name__}", file=sys.stderr)
        return False


def write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(str(text).encode("utf-8"))
        return True
    except OSError as exc:
        print(f"[error] write failed {path}: {exc.__class__.__name__}", file=sys.stderr)
        return False


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
    return {
        ".pdf": "pdf",
        ".pptx": "slide_template", ".ppt": "slide_template", ".odp": "slide_template",
        ".md": "text", ".txt": "text", ".tex": "text", ".json": "text",
        ".yaml": "text", ".yml": "text", ".csv": "text", ".xml": "text",
        ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
        ".webp": "image", ".tif": "image", ".tiff": "image", ".bmp": "image",
        ".docx": "office", ".doc": "office", ".xlsx": "office",
    }.get(s, "other")


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
            "path": str(path), "rel": rel, "bytes": size,
            "kind": _detect_kind(path.suffix),
        })
    return materials


# ---------------------------------------------------------------------------
# 输出契约
# ---------------------------------------------------------------------------

def extract_contract_keys(case: dict):
    """动态解析 output_contract 字段名。返回 (keys, note)。"""
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
    k = str(key).lower()
    if any(h in k for h in _LIST_HINTS):
        return []
    if any(h in k for h in _DICT_HINTS):
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


def build_skeleton(case_id: str, keys: list, status: str = "partial", errors=None,
                   payload=None):
    """生成一版立即可交付的 partial 骨架（契约字段齐全 + 统一信封）。

    payload：调用方已准备好的契约字段值，先并入骨架，再对仍缺失的字段回填默认值，
    使 degradations 里的「未提供值」记录只针对真正未填的字段。
    """
    skeleton = {"case_id": case_id, "status": status}
    if isinstance(payload, dict):
        skeleton.update(payload)
    filled, missing = fill_contract(keys, skeleton)
    skeleton.update(filled)
    for env in ENVELOPE_FIELDS:
        if env not in skeleton:
            skeleton[env] = default_value_for(env)
    if missing:
        skeleton.setdefault("degradations", []).append(
            "契约字段未提供值，已用默认值占位: " + ", ".join(missing))
    if errors:
        skeleton.setdefault("errors", []).extend(errors)
    skeleton["runtime"] = skeleton.get("runtime") or {}
    skeleton["runtime"]["started_at"] = now_iso()
    return skeleton


# ---------------------------------------------------------------------------
# 证据与结果校验
# ---------------------------------------------------------------------------

def validate_draft(draft: dict):
    """校验页级草稿：每页 page/claim/role + 至少一条 evidence(locator + 逐字 quote)。"""
    issues = []
    if not isinstance(draft, dict):
        return ["draft.json 顶层不是对象"]
    pages = draft.get("slides", draft.get("pages"))
    if not isinstance(pages, list) or not pages:
        return ["draft.json 无 slides/pages 列表或列表为空"]
    for i, page in enumerate(pages):
        tag = page.get("page", i + 1) if isinstance(page, dict) else i + 1
        if not isinstance(page, dict):
            issues.append(f"第 {tag} 页不是对象")
            continue
        for req in ("page", "claim", "role"):
            if not str(page.get(req, "")).strip():
                issues.append(f"第 {tag} 页缺字段 {req}")
        role = page.get("role")
        if role and role not in ROLES:
            issues.append(f"第 {tag} 页 role 非法: {role!r}")
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
        src = result.get("editable_source")
        if isinstance(src, dict) and src.get("path"):
            p = Path(src["path"])
            if not p.is_absolute():
                p = output_dir / p
            if not p.exists():
                issues.append(f"主产物文件不存在: {p}")
    return issues


# ---------------------------------------------------------------------------
# PDF / PPTX 文件检查
# ---------------------------------------------------------------------------

def pdf_page_count(path: Path):
    """启发式统计 PDF 页数：匹配 /Type /Page（排除 /Pages）。返回 (count, err)。

    已知限制：压缩对象流（object streams）可能漏计；返回值仅作自检，最终以
    渲染工具为准。文件合法但计数为 0 时，调用方应把该限制写入 degradations。
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    if not raw.startswith(b"%PDF"):
        return None, f"{Path(path).name} 不是合法 PDF（缺少 %PDF 头）"
    count = len(re.findall(rb"/Type\s*/Page(?![sA-Za-z])", raw))
    return count, None


def is_zip_pptx(path: Path) -> bool:
    """粗检 pptx：是 zip 且含 [Content_Types].xml 与至少一张 slide。"""
    try:
        if not zipfile.is_zipfile(str(path)):
            return False
        with zipfile.ZipFile(str(path)) as zf:
            names = zf.namelist()
        return "[Content_Types].xml" in names and any(
            re.fullmatch(r"ppt/slides/slide\d+\.xml", n) for n in names)
    except Exception:
        return False


def pptx_stats(path: Path):
    """读取 pptx 结构统计（slides/masters/layouts 数量 + 首页文本预览）。返回 (stats, err)。"""
    try:
        if not zipfile.is_zipfile(str(path)):
            return None, "不是合法 zip/pptx"
        with zipfile.ZipFile(str(path)) as zf:
            names = zf.namelist()
        slides = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        masters = sorted(n for n in names if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n))
        layouts = sorted(n for n in names if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n))
        preview = ""
        if slides:
            with zipfile.ZipFile(str(path)) as zf:
                data = zf.read(slides[0]).decode("utf-8", "replace")
            texts = re.findall(r"<a:t>(.*?)</a:t>", data, re.S)
            preview = " | ".join(t.strip() for t in texts[:8])
        return {
            "slides": len(slides),
            "masters": len(masters),
            "layouts": len(layouts),
            "has_content_types": "[Content_Types].xml" in names,
            "first_slide_preview": preview,
        }, None
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}"


# ---------------------------------------------------------------------------
# PPTX 生成（纯标准库 OOXML）
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _para(text: str, bullet: bool = True) -> str:
    bu = '<a:buChar char="•"/>' if bullet else '<a:buNone/>'
    return (
        '<a:p><a:pPr marL="228600" indent="-228600">' + bu + '</a:pPr>'
        + '<a:r><a:rPr lang="en-US" dirty="0"/><a:t>' + _esc(text) + '</a:t></a:r></a:p>'
    )


DEFAULT_TITLE_XFRM = '<a:xfrm><a:off x="838200" y="365125"/><a:ext cx="10515600" cy="1325563"/></a:xfrm>'
DEFAULT_BODY_XFRM = '<a:xfrm><a:off x="838200" y="1825625"/><a:ext cx="10515600" cy="4333875"/></a:xfrm>'

_SLIDE_NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
             'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')


def _slide_xml(title: str, body: list, title_xfrm: str, body_xfrm: str) -> str:
    paras = "".join(_para(str(t)) for t in body if str(t).strip())
    if not paras:
        paras = _para("（待依据材料补齐要点，禁止占位交付）", bullet=False)
    return _XML_DECL + (
        '<p:sld ' + _SLIDE_NS + '><p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/>'
        '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr>' + title_xfrm + '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/>' + _para(title, bullet=False) + '</p:txBody></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content Placeholder 2"/><p:cNvSpPr/>'
        '<p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
        '<p:spPr>' + body_xfrm + '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/>' + paras + '</p:txBody></p:sp>'
        '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )


def build_deck(slides: list, out_path, title: str = "Academic presentation"):
    """无模板兜底：生成最小合法 16:9 可编辑 pptx。返回 (ok, err)。"""
    if not slides:
        return False, "无页内容，拒绝生成空 pptx"
    try:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        n = len(slides)
        slide_ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{1 + i}"/>' for i in range(1, n + 1))
        slide_rels = "".join(
            f'<Relationship Id="rId{1 + i}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
            f'Target="slides/slide{i}.xml"/>' for i in range(1, n + 1))
        slide_overrides = "".join(
            f'<Override PartName="/ppt/slides/slide{i}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for i in range(1, n + 1))

        parts = {
            "[Content_Types].xml": _XML_DECL + (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
                '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
                '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                + slide_overrides + "</Types>"),
            "_rels/.rels": _XML_DECL + (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                "</Relationships>"),
            "docProps/core.xml": _XML_DECL + (
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + _esc(title) + '</dc:title>'
                '<dc:creator>slides-01</dc:creator><cp:lastModifiedBy>slides-01</cp:lastModifiedBy></cp:coreProperties>'),
            "docProps/app.xml": _XML_DECL + (
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                '<Application>slides-01</Application><PresentationFormat>On-screen Show (16:9)</PresentationFormat></Properties>'),
            "ppt/presentation.xml": _XML_DECL + (
                '<p:presentation ' + _SLIDE_NS + '>'
                '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
                '<p:sldIdLst>' + slide_ids + '</p:sldIdLst>'
                '<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
                '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>'),
            "ppt/_rels/presentation.xml.rels": _XML_DECL + (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
                + slide_rels + "</Relationships>"),
            "ppt/slideMasters/slideMaster1.xml": _XML_DECL + (
                '<p:sldMaster ' + _SLIDE_NS + '><p:cSld><p:spTree>'
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title Placeholder"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
                '<p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
                '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Body Placeholder"/><p:cNvSpPr/><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
                '<p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
                '</p:spTree></p:cSld>'
                '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
                'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
                '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>'),
            "ppt/slideMasters/_rels/slideMaster1.xml.rels": _XML_DECL + (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
                "</Relationships>"),
            "ppt/slideLayouts/slideLayout1.xml": _XML_DECL + (
                '<p:sldLayout ' + _SLIDE_NS + ' type="obj"><p:cSld name="Title and Content"><p:spTree>'
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
                '<p:spPr>' + DEFAULT_TITLE_XFRM + '</p:spPr>'
                '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
                '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content Placeholder 2"/><p:cNvSpPr/><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
                '<p:spPr>' + DEFAULT_BODY_XFRM + '</p:spPr>'
                '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
                '</p:spTree></p:cSld><p:clrMapOvr><a:overrideClrMapping bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" '
                'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
                'hlink="hlink" folHlink="folHlink"/></p:clrMapOvr></p:sldLayout>'),
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _XML_DECL + (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
                "</Relationships>"),
            "ppt/theme/theme1.xml": _XML_DECL + (
                '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office">'
                '<a:themeElements>'
                '<a:clrScheme name="Office">'
                '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
                '<a:dk2><a:srgbClr val="1F497D"/></a:dk2><a:lt2><a:srgbClr val="EEECE1"/></a:lt2>'
                '<a:accent1><a:srgbClr val="4F81BD"/></a:accent1><a:accent2><a:srgbClr val="C0504D"/></a:accent2>'
                '<a:accent3><a:srgbClr val="9BBB59"/></a:accent3><a:accent4><a:srgbClr val="8064A2"/></a:accent4>'
                '<a:accent5><a:srgbClr val="4BACC6"/></a:accent5><a:accent6><a:srgbClr val="F79646"/></a:accent6>'
                '<a:hlink><a:srgbClr val="0000FF"/></a:hlink><a:folHlink><a:srgbClr val="800080"/></a:folHlink>'
                '</a:clrScheme>'
                '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
                '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
                '<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
                '<a:lnStyleLst><a:ln w="9525" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
                '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
                '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
                '</a:fmtScheme></a:themeElements></a:theme>'),
        }
        for i, slide in enumerate(slides, start=1):
            parts[f"ppt/slides/slide{i}.xml"] = _slide_xml(
                str(slide.get("title") or f"Slide {i}"),
                slide.get("body") if isinstance(slide.get("body"), list) else [str(slide.get("body"))],
                DEFAULT_TITLE_XFRM, DEFAULT_BODY_XFRM)
            parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = _XML_DECL + (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                "</Relationships>")
        with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in parts.items():
                zf.writestr(name, content.encode("utf-8"))
        return True, None
    except Exception as exc:  # noqa: BLE001 —— 兜底不得崩
        return False, f"{exc.__class__.__name__}: {exc}"


def _pick_layout(parts: dict, layout_names: list, layout_index=None):
    """优先选择含 'Title and Content' 的版式；否则取指定序号或第一个。"""
    if layout_index is not None and 0 <= layout_index < len(layout_names):
        return layout_names[layout_index]
    for name in layout_names:
        data = parts.get(name, b"").decode("utf-8", "replace")
        if "Title and Content" in data:
            return name
    return layout_names[0]


def _layout_placeholders(layout_xml_bytes: bytes):
    """从版式 XML 中提取 title / body(idx=1) 占位符的 xfrm，实现按占位符约束排版。"""
    text = layout_xml_bytes.decode("utf-8", "replace")
    title_xfrm, body_xfrm = DEFAULT_TITLE_XFRM, DEFAULT_BODY_XFRM
    for chunk in re.split(r"<p:sp>", text):
        ph = re.search(r"<p:ph([^>]*?)/>", chunk)
        if not ph:
            continue
        attrs = ph.group(1)
        xfrm = re.search(r"<a:xfrm>.*?</a:xfrm>", chunk, re.S)
        xfrm_str = xfrm.group(0) if xfrm else None
        if 'type="title"' in attrs and xfrm_str:
            title_xfrm = xfrm_str
        elif 'idx="1"' in attrs and xfrm_str:
            body_xfrm = xfrm_str
    return title_xfrm, body_xfrm


def inherit_deck(template_path, out_path, slides: list, layout_index=None):
    """真继承模板：复制模板包，追加 slide 并复用其 layout/master/theme。返回 (ok, err)。"""
    tpl = Path(template_path)
    if not zipfile.is_zipfile(str(tpl)):
        return False, "模板不是合法 zip/pptx"
    try:
        with zipfile.ZipFile(str(tpl)) as zf:
            parts = {n: zf.read(n) for n in zf.namelist()}
    except Exception as exc:
        return False, f"读取模板失败: {exc.__class__.__name__}: {exc}"
    if not slides:
        return False, "无页内容，拒绝生成空 pptx"

    layout_names = sorted(
        (n for n in parts if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)),
        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    if not layout_names:
        return False, "模板无 slideLayout，无法继承版式"
    chosen = _pick_layout(parts, layout_names, layout_index)
    layout_idx = int(re.search(r"(\d+)\.xml$", chosen).group(1))
    title_xfrm, body_xfrm = _layout_placeholders(parts.get(chosen, b""))

    existing = [int(re.search(r"slide(\d+)\.xml$", n).group(1))
                for n in parts if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
    next_id = (max(existing) + 1) if existing else 1

    pres_rels = parts.get("ppt/_rels/presentation.xml.rels", b"").decode("utf-8", "replace")
    rids = re.findall(r'Id="(rId\d+)"', pres_rels)
    next_rel = (max(int(re.search(r"(\d+)", r).group(1)) for r in rids) + 1) if rids else 1

    slide_ids, slide_rels, slide_overrides = [], [], []
    for k in range(len(slides)):
        i = next_id + k
        slide_ids.append(f'<p:sldId id="{255 + i}" r:id="rId{next_rel + k}"/>')
        slide_rels.append(
            f'<Relationship Id="rId{next_rel + k}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
            f'Target="slides/slide{i}.xml"/>')
        slide_overrides.append(
            f'<Override PartName="/ppt/slides/slide{i}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')

    def _insert(xml: str, pattern: str, fragment: str):
        if pattern not in xml:
            return xml + fragment
        return xml.replace(pattern, fragment + pattern, 1)

    # 注册新 slide 到三类部件。
    ct = parts.get("[Content_Types].xml", b"").decode("utf-8", "replace")
    ct = _insert(ct, "</Types>", "".join(slide_overrides))
    parts["[Content_Types].xml"] = ct.encode("utf-8")

    pres_xml = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    if "<p:sldIdLst>" in pres_xml:
        pres_xml = _insert(pres_xml, "</p:sldIdLst>", "".join(slide_ids))
    else:
        pres_xml = pres_xml.replace("</p:presentation>",
                                    "<p:sldIdLst>" + "".join(slide_ids) + "</p:sldIdLst></p:presentation>", 1)
    parts["ppt/presentation.xml"] = pres_xml.encode("utf-8")

    pres_rels = _insert(pres_rels, "</Relationships>", "".join(slide_rels))
    parts["ppt/_rels/presentation.xml.rels"] = pres_rels.encode("utf-8")

    for k, slide in enumerate(slides):
        i = next_id + k
        body = slide.get("body") if isinstance(slide.get("body"), list) else [str(slide.get("body"))]
        parts[f"ppt/slides/slide{i}.xml"] = _slide_xml(
            str(slide.get("title") or f"Slide {i}"), body, title_xfrm, body_xfrm).encode("utf-8")
        parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = (_XML_DECL + (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout{layout_idx}.xml"/>'
            "</Relationships>")).encode("utf-8")

    try:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in parts.items():
                zf.writestr(name, content)
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"
