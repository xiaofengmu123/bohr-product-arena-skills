#!/usr/bin/env python3
"""slides-01 核心库（纯标准库）。

职责：鲁棒输入读取、材料发现、契约回填、draft 校验、最小可编辑 PPTX 构建、
PDF 页数校验、SVG 降级渲染。所有函数不抛未捕获异常，失败以 (result, error) 返回。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# 默认契约（当 case.json 未提供 output_contract 时回退）
ROOT_FIELDS = ["case_id", "status", "editable_source", "export_artifact",
               "content_map", "compliance_checks", "exceptions"]
LIST_FIELDS = {"content_map", "compliance_checks", "exceptions", "sources",
               "uncertainty", "pending_human", "change_log", "errors", "degradations", "changes"}
DICT_FIELDS = {"editable_source", "export_artifact", "runtime"}
STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ROLES = {"title", "background", "method", "result", "discussion", "limitation",
         "conclusion", "other"}
DEFAULT_FILE_LIMIT = 2 * 1024 * 1024  # 单文件 2MB 上限


# --------------------------------------------------------------------------- #
# 时间与 IO 辅助
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def xml_escape(text: str) -> str:
    """转义 XML 文本内容，剔除控制字符，防止非法 XML 使 pptx 无法打开。"""
    if not isinstance(text, str):
        text = str(text)
    out = []
    for ch in text:
        cp = ord(ch)
        if ch == "&":
            out.append("&amp;")
        elif ch == "<":
            out.append("&lt;")
        elif ch == ">":
            out.append("&gt;")
        elif ch == '"':
            out.append("&quot;")
        elif cp in (0x9, 0xA, 0xD) or cp >= 0x20:
            out.append(ch)
        # 其余控制字符直接丢弃
    return "".join(out)


def read_bytes_limited(path: Path, limit: int = DEFAULT_FILE_LIMIT):
    """读取文件字节；超限返回 (None, 说明)，不读入内存。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"{path.name} 读取失败: {exc.__class__.__name__}"
    if size > limit:
        return None, f"{path.name} 超过 {limit // (1024 * 1024)}MB 上限，跳过"
    try:
        return path.read_bytes(), None
    except OSError as exc:
        return None, f"{path.name} 读取失败: {exc.__class__.__name__}"


def read_json(path: Path, limit: int = DEFAULT_FILE_LIMIT):
    """容错读 JSON：BOM/坏编码/坏 JSON 都返回 (None, err) 而不崩溃。"""
    raw, err = read_bytes_limited(path, limit)
    if err:
        return None, err
    if not raw.strip():
        return None, f"{path.name} 为空文件"
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return None, f"{path.name} 无法识别编码"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{path.name} JSON 解析失败: {exc}"


def read_text(path: Path, limit: int = DEFAULT_FILE_LIMIT):
    """容错读文本：多编码回退，超限跳过。"""
    raw, err = read_bytes_limited(path, limit)
    if err:
        return None, err
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return None, f"{path.name} 无法识别编码"


def write_json(path: Path, obj) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return True
    except OSError:
        return False


def write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(str(text).encode("utf-8"))
        return True
    except OSError:
        return False


def ensure_dir(path: Path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


# --------------------------------------------------------------------------- #
# 材料发现
# --------------------------------------------------------------------------- #
def discover_materials(input_root: Path, file_limit: int = DEFAULT_FILE_LIMIT):
    """扫描输入目录，返回 (materials, degraded)。超大文件跳过并记降级。"""
    materials, degraded = [], []
    if not input_root.is_dir():
        return materials, degraded
    try:
        paths = sorted(input_root.rglob("*"))
    except OSError as exc:
        return materials, [f"扫描 {input_root} 失败: {exc.__class__.__name__}"]
    for path in paths:
        if not path.is_file():
            continue
        rel = path.relative_to(input_root).as_posix()
        if rel == "case.json":
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > file_limit:
            degraded.append(f"{rel} 超限({size} bytes)跳过")
            continue
        materials.append({
            "path": path.as_posix(),
            "rel": rel,
            "size": size,
            "suffix": path.suffix.lower(),
        })
    return materials, degraded


# --------------------------------------------------------------------------- #
# 契约回填
# --------------------------------------------------------------------------- #
def extract_contract_keys(case: dict):
    """从 case['output_contract'] 提取字段名；缺失回退 ROOT_FIELDS。"""
    keys = []
    raw = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str) and item[cand].strip():
                        keys.append(item[cand].strip())
                        break
    ordered, seen = [], set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    if not ordered:
        ordered = list(ROOT_FIELDS)
    return ordered


def default_value(key: str):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    return None


def fill_contract(keys, payload):
    filled, missing = {}, []
    for key in keys:
        if key in payload:
            filled[key] = payload[key]
        else:
            filled[key] = default_value(key)
            missing.append(key)
    return filled, missing


# --------------------------------------------------------------------------- #
# draft 校验
# --------------------------------------------------------------------------- #
def validate_draft(draft):
    """校验 agent 撰写的 draft.json 结构，返回 (slides, title, errors)。"""
    errors = []
    if not isinstance(draft, dict):
        return [], "", ["draft 必须是 JSON 对象"]
    title = draft.get("title") or ""
    slides = draft.get("slides")
    if not isinstance(slides, list) or not slides:
        return [], title, ["draft.slides 缺失或为空"]
    for i, s in enumerate(slides, 1):
        if not isinstance(s, dict):
            errors.append(f"slide #{i} 不是对象")
            continue
        if not str(s.get("claim") or "").strip():
            errors.append(f"slide #{i} 缺 claim")
        role = s.get("role")
        if role and role not in ROLES:
            errors.append(f"slide #{i} role 非法: {role}")
        ev = s.get("evidence")
        if ev is not None and not isinstance(ev, dict):
            errors.append(f"slide #{i} evidence 不是对象")
    return slides, title, errors


# --------------------------------------------------------------------------- #
# 最小可编辑 PPTX 构建（纯标准库 ZIP + OOXML）
# --------------------------------------------------------------------------- #
_NS_A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
_NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_NS_P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _content_types(slide_count: int) -> str:
    overrides = [
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>',
        '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>',
        '<Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for i in range(1, slide_count + 1):
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
        overrides.append(
            f'<Override PartName="/ppt/notesSlides/notesSlide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            + "".join(overrides) + "</Types>")


def _root_rels() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>")


def _app_props(slide_count: int) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            '<Application>Microsoft Office PowerPoint</Application>'
            f'<Slides>{slide_count}</Slides>'
            '<PresentationFormat>On-screen Show (4:3)</PresentationFormat>'
            "</Properties>")


def _core_props(title: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{xml_escape(title)}</dc:title>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now_iso()}</dcterms:created>'
            "</cp:coreProperties>")


def _presentation(slide_count: int) -> str:
    ids = "".join(
        f'<p:sldId id="{255 + i}" r:id="rId{2 + i}"/>' for i in range(slide_count))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentation {_NS_A} {_NS_R} {_NS_P}>'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:notesMasterIdLst><p:notesMasterId r:id="rId2"/></p:notesMasterIdLst>'
            f'<p:sldIdLst>{ids}</p:sldIdLst>'
            '<p:sldSz cx="12192000" cy="6858000"/>'
            '<p:notesSz cx="6858000" cy="9144000"/>'
            '<p:defaultTextStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr></p:defaultTextStyle>'
            "</p:presentation>")


def _presentation_rels(slide_count: int) -> str:
    rels = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>',
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster" Target="notesMasters/notesMaster1.xml"/>',
    ]
    for i in range(slide_count):
        rels.append(
            f'<Relationship Id="rId{3 + i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i + 1}.xml"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">' + "".join(rels) + "</Relationships>")


def _pres_props() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentationPr {_NS_A} {_NS_R} {_NS_P}/>')


def _view_props() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:viewPr {_NS_A} {_NS_R} {_NS_P}>'
            '<p:normalViewPr><p:restoredLeft sz="15620" auto="1"/><p:restoredTop sz="94660"/></p:normalViewPr>'
            "</p:viewPr>")


def _theme() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<a:theme {_NS_A} name="Office">'
            "<a:themeElements>"
            '<a:clrScheme name="Office">'
            '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
            '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
            '<a:dk2><a:srgbClr val="1F497D"/></a:dk2>'
            '<a:lt2><a:srgbClr val="EEECE1"/></a:lt2>'
            '<a:accent1><a:srgbClr val="4F81BD"/></a:accent1>'
            '<a:accent2><a:srgbClr val="C0504D"/></a:accent2>'
            '<a:accent3><a:srgbClr val="9BBB59"/></a:accent3>'
            '<a:accent4><a:srgbClr val="8064A2"/></a:accent4>'
            '<a:accent5><a:srgbClr val="4BACC6"/></a:accent5>'
            '<a:accent6><a:srgbClr val="F79646"/></a:accent6>'
            '<a:hlink><a:srgbClr val="0000FF"/></a:hlink>'
            '<a:folHlink><a:srgbClr val="800080"/></a:folHlink>'
            "</a:clrScheme>"
            '<a:fontScheme name="Office">'
            '<a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
            '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
            "</a:fontScheme>"
            '<a:fmtScheme name="Office">'
            '<a:fillStyleLst>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:tint val="50000"/><a:satMod val="300000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:shade val="20000"/><a:satMod val="300000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="16200000" scaled="1"/></a:gradFill>'
            '<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:tint val="90000"/><a:satMod val="200000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:shade val="80000"/><a:satMod val="200000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="16200000" scaled="0"/></a:gradFill>'
            "</a:fillStyleLst>"
            '<a:lnStyleLst>'
            '<a:ln w="9525" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"><a:shade val="95000"/><a:satMod val="105000"/></a:schemeClr></a:solidFill><a:prstDash val="solid"/></a:ln>'
            '<a:ln w="25400" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
            '<a:ln w="38100" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
            "</a:lnStyleLst>"
            '<a:effectStyleLst>'
            '<a:effectStyle><a:effectLst/></a:effectStyle>'
            '<a:effectStyle><a:effectLst/></a:effectStyle>'
            '<a:effectStyle><a:effectLst/></a:effectStyle>'
            "</a:effectStyleLst>"
            '<a:bgFillStyleLst>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:tint val="40000"/><a:satMod val="350000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:shade val="20000"/><a:satMod val="350000"/></a:schemeClr></a:gs></a:gsLst><a:path path="circle"><a:fillToRect l="50000" t="-80000" r="50000" b="180000"/></a:path></a:gradFill>'
            '<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:tint val="80000"/><a:satMod val="300000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:shade val="30000"/><a:satMod val="300000"/></a:schemeClr></a:gs></a:gsLst><a:path path="circle"><a:fillToRect l="50000" t="50000" r="50000" b="50000"/></a:path></a:gradFill>'
            "</a:bgFillStyleLst>"
            "</a:fmtScheme>"
            "</a:themeElements>"
            "</a:theme>")


def _grp_sp_pr() -> str:
    return ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            "<p:grpSpPr/>")


def _slide_master() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sldMaster {_NS_A} {_NS_R} {_NS_P}>'
            "<p:cSld><p:bg><p:bgRef idx=\"1001\"><a:schemeClr val=\"bg1\"/></p:bgRef></p:bg>"
            f"<p:spTree>{_grp_sp_pr()}"
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Body Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
            "</p:spTree></p:cSld>"
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            "</p:sldMaster>")


def _slide_master_rels() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
            "</Relationships>")


def _slide_layout() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sldLayout {_NS_A} {_NS_R} {_NS_P}>'
            f"<p:cSld><p:spTree>{_grp_sp_pr()}"
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content Placeholder 2"/><p:cNvSpPr/><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
            "</p:spTree></p:cSld>"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
            "</p:sldLayout>")


def _slide_layout_rels() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
            "</Relationships>")


def _notes_master() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:notesMaster {_NS_A} {_NS_R} {_NS_P}>'
            f"<p:cSld><p:spTree>{_grp_sp_pr()}"
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Body Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
            "</p:spTree></p:cSld>"
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            "</p:notesMaster>")


def _notes_master_rels() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
            "</Relationships>")


def _para(text: str, size: int = 1800, bold: bool = False, bullet: bool = False) -> str:
    ppr = ""
    if bullet:
        ppr = '<a:pPr lvl="0" marL="342900" indent="-342900"><a:buFont typeface="Arial"/><a:buChar char="•"/></a:pPr>'
    rpr = f'<a:rPr lang="en-US" sz="{size}"{" b=\"1\"" if bold else ""}/>'
    return f"<a:p>{ppr}<a:r>{rpr}<a:t>{xml_escape(text)}</a:t></a:r></a:p>"


def _slide_xml(slide: dict, index: int) -> str:
    claim = str(slide.get("claim") or f"Slide {index}")
    bullets = slide.get("bullet_points") or []
    if not isinstance(bullets, list):
        bullets = []
    body = "".join(_para(str(b), 1600, False, True) for b in bullets if str(b).strip())
    if not body:
        body = "<a:p/>"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sld {_NS_A} {_NS_R} {_NS_P}>'
            f"<p:cSld><p:spTree>{_grp_sp_pr()}"
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
            "<p:spPr/>"
            f'<p:txBody><a:bodyPr/><a:lstStyle/>{_para(claim, 2800, True, False)}</p:txBody>'
            "</p:sp>"
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
            f"<p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody></p:sp>"
            "</p:spTree></p:cSld>"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
            "</p:sld>")


def _slide_rels(index: int) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{index}.xml"/>'
            "</Relationships>")


def _notes_slide_xml(slide: dict, index: int) -> str:
    notes = str(slide.get("speaker_notes") or "").strip()
    body = ""
    for line in notes.split("\n"):
        body += _para(line, 1400, False, False)
    if not body:
        body = "<a:p/>"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:notes {_NS_A} {_NS_R} {_NS_P}>'
            f"<p:cSld><p:spTree>{_grp_sp_pr()}"
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
            f'<p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody></p:sp>'
            "</p:spTree></p:cSld>"
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
            "</p:notes>")


def _notes_slide_rels() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster" Target="../notesMasters/notesMaster1.xml"/>'
            '</Relationships>')


def build_minimal_pptx(slides, title, out_path: Path):
    """用纯标准库构建一个可打开、可编辑的最小 pptx。返回 (path, err)。"""
    if not slides:
        return None, "无 slide，无法构建 pptx"
    out_path = Path(out_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, f"无法创建目录: {exc.__class__.__name__}"
    n = len(slides)
    parts = {
        "[Content_Types].xml": _content_types(n),
        "_rels/.rels": _root_rels(),
        "docProps/app.xml": _app_props(n),
        "docProps/core.xml": _core_props(title),
        "ppt/presentation.xml": _presentation(n),
        "ppt/_rels/presentation.xml.rels": _presentation_rels(n),
        "ppt/presProps.xml": _pres_props(),
        "ppt/viewProps.xml": _view_props(),
        "ppt/theme/theme1.xml": _theme(),
        "ppt/slideMasters/slideMaster1.xml": _slide_master(),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _slide_master_rels(),
        "ppt/slideLayouts/slideLayout1.xml": _slide_layout(),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _slide_layout_rels(),
        "ppt/notesMasters/notesMaster1.xml": _notes_master(),
        "ppt/notesMasters/_rels/notesMaster1.xml.rels": _notes_master_rels(),
    }
    for i, slide in enumerate(slides, 1):
        parts[f"ppt/slides/slide{i}.xml"] = _slide_xml(slide, i)
        parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = _slide_rels(i)
        parts[f"ppt/notesSlides/notesSlide{i}.xml"] = _notes_slide_xml(slide, i)
        parts[f"ppt/notesSlides/_rels/notesSlide{i}.xml.rels"] = _notes_slide_rels()
    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in parts.items():
                zf.writestr(name, content)
        return str(out_path), None
    except (OSError, zipfile.BadZipFile) as exc:
        return None, f"pptx 写入失败: {exc.__class__.__name__}: {exc}"


def pptx_slide_count(path: Path):
    """统计 pptx 内 slide 文件数；损坏返回 (0, err)。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist()
                     if re.match(r"ppt/slides/slide\d+\.xml$", n)]
            return len(names), None
    except (OSError, zipfile.BadZipFile) as exc:
        return 0, f"{path.name} 不是有效 pptx: {exc.__class__.__name__}"


# --------------------------------------------------------------------------- #
# PDF 页数校验
# --------------------------------------------------------------------------- #
def pdf_page_count(path: Path):
    """读取 PDF 头部并统计页对象；损坏返回 (0, err)。"""
    raw, err = read_bytes_limited(path, 8 * 1024 * 1024)
    if err:
        return 0, err
    if not raw.startswith(b"%PDF"):
        return 0, f"{path.name} 不是有效 PDF（无 %PDF 头）"
    count = len(re.findall(rb"/Type\s*/Page\b", raw))
    return count, None


def find_soffice():
    """查找 LibreOffice 可执行文件；找不到返回 None。"""
    for name in ("soffice", "libreoffice", "soffice.com", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    for env_name in ("SOFFICE_PATH", "LIBREOFFICE_PATH"):
        val = __import__("os").environ.get(env_name)
        if val and Path(val).exists():
            return val
    return None


def export_pdf(pptx_path: Path, out_dir: Path, timeout: int = 120):
    """用 LibreOffice 导出 PDF；不可用返回 (None, reason)。"""
    soffice = find_soffice()
    if not soffice:
        return None, "未找到 LibreOffice/soffice，跳过 PDF 导出"
    try:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir",
             str(out_dir), str(pptx_path)],
            capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"soffice 运行失败: {exc.__class__.__name__}"
    pdf_path = out_dir / (Path(pptx_path).stem + ".pdf")
    if pdf_path.exists():
        return str(pdf_path), None
    return None, f"soffice 未生成 PDF（退出码 {proc.returncode}）"


# --------------------------------------------------------------------------- #
# SVG 降级渲染
# --------------------------------------------------------------------------- #
def render_svg_deck(slides, title, out_path: Path):
    """在无法生成 pptx 时，输出一页式 SVG 预览（纯标准库）。"""
    if not slides:
        return None, "无 slide，无法渲染 SVG"
    y = 0
    blocks = []
    for i, slide in enumerate(slides, 1):
        claim = xml_escape(str(slide.get("claim") or f"Slide {i}"))
        bullets = slide.get("bullet_points") or []
        lines = "".join(
            f'<text x="60" y="{y + 160 + k * 36}" font-size="24" fill="#333">{xml_escape(str(b))}</text>'
            for k, b in enumerate(bullets[:8]) if str(b).strip())
        notes = xml_escape(str(slide.get("speaker_notes") or "")[:120])
        blocks.append(
            f'<text x="40" y="{y + 90}" font-size="34" font-weight="bold" fill="#1F497D">{claim}</text>'
            + lines +
            f'<text x="60" y="{y + 520}" font-size="16" fill="#777">备注: {notes}</text>'
            f'<line x1="40" y1="{y + 560}" x2="920" y2="{y + 560}" stroke="#ccc" stroke-width="1"/>')
        y += 600
    svg = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="{y + 40}" viewBox="0 0 960 {y + 40}">'
           '<rect width="960" height="' + str(y + 40) + '" fill="#ffffff"/>'
           f'<text x="40" y="40" font-size="20" fill="#666">{xml_escape(title)}</text>'
           + "".join(blocks) + "</svg>")
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(svg.encode("utf-8"))
        return str(out_path), None
    except OSError as exc:
        return None, f"SVG 写入失败: {exc.__class__.__name__}"
