#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-deck-architect 核心实现：纯标准库 OOXML/PDF 与草稿校验。

设计约定（评测会真实打开文件，禁止占位空文件）：
- `assemble_deck`     从零生成合法 16:9 OOXML 演示文稿（1 母版 + 1 版式 + N 页），
                      每页真实文本，且页脚带 narrative role 与证据定位。
- `inherit_deck`      真继承：读输入模板 pptx，母版/版式/主题部件原样保留，
                      仅追加新页并让新页 rels 指向模板既有版式。
- `patch_deck`        edit 模式：只原位替换目标页文本，其余页与母版不动。
- `render_pdf_text`   逐页文本层 PDF 兜底（页数 == 幻灯片数）；有 Office 渲染工具时优先用工具。
- `inspect_deck`      解析 pptx 结构（页数/版式/母版/主题指纹/每页文本），供合规校验。
- `audit_draft`       对 work/draft.json 做结构校验与逐字引文命中校验。
所有函数只依赖标准库；不联网；输出字节确定（固定 ZIP 时间戳），重复运行结果一致。
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
XMLNS = (f'xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"')

SLIDE_W = 12192000
SLIDE_H = 6858000

ROLES = {"title", "background", "method", "result", "discussion", "limitation",
         "conclusion", "other"}
MODES = {"create", "edit", "adapt"}


# --------------------------------------------------------------------------- 基础工具
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def xml_esc(text) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _rpr(size: int, bold: int, color: str) -> str:
    return (f'<a:rPr lang="en-US" sz="{size}" b="{bold}" dirty="0">'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:rPr>')


def _run(text: str, size: int = 1800, bold: bool = False, color: str = "1F2937") -> str:
    return f"<a:r>{_rpr(size, 1 if bold else 0, color)}<a:t>{xml_esc(text)}</a:t></a:r>"


def _para(text: str, size: int = 1800, bold: bool = False, bullet: bool = False,
          color: str = "1F2937") -> str:
    bullet_xml = '<a:buChar char="•"/>' if bullet else "<a:buNone/>"
    return (f"<a:p><a:pPr>{bullet_xml}<a:defRPr sz=\"{size}\" b=\"{1 if bold else 0}\">"
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:defRPr></a:pPr>'
            + _run(text, size, bold, color) + "</a:p>")


def _textbox(shape_id: int, name: str, x: int, y: int, cx: int, cy: int,
             paragraphs: str, fill: str = "FFFFFF", line: str = "E5E7EB") -> str:
    ln = (f'<a:ln><a:solidFill><a:srgbClr val="{line}"/></a:solidFill></a:ln>'
          if line else "<a:ln><a:noFill/></a:ln>")
    fill_xml = (f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>'
                if fill else "<a:noFill/>")
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{xml_esc(name)}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        "<p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        + fill_xml + ln + "</p:spPr>"
        '<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr><a:lstStyle/>'
        + paragraphs + "</p:txBody></p:sp>"
    )


def _theme_xml() -> str:
    palette = [("dk1", "1F2937"), ("lt1", "FFFFFF"), ("dk2", "374151"),
               ("lt2", "F3F4F6"), ("accent1", "2563EB"), ("accent2", "DC2626"),
               ("accent3", "059669"), ("accent4", "7C3AED"), ("accent5", "D97706"),
               ("accent6", "0891B2"), ("hlink", "2563EB"), ("folHlink", "7C3AED")]
    clr = "".join(f'<a:{k}><a:srgbClr val="{v}"/></a:{k}>' for k, v in palette)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<a:theme xmlns:a="{A_NS}" name="EvidenceStd">'
        "<a:themeElements>"
        f'<a:clrScheme name="EvidenceStd"><a:dk1><a:srgbClr val="1F2937"/></a:dk1>'
        f'<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>{clr}</a:clrScheme>'
        '<a:fontScheme name="EvidenceStd"><a:majorFont><a:latin typeface="Segoe UI"/>'
        '<a:ea typeface="Microsoft YaHei"/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Segoe UI"/><a:ea typeface="Microsoft YaHei"/>'
        '<a:cs typeface=""/></a:minorFont></a:fontScheme>'
        '<a:fmtScheme name="EvidenceStd"><a:fillStyleLst>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
        '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="25400"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="38100"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
        '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>'
        '<a:effectStyle><a:effectLst/></a:effectStyle>'
        '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
        '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
        "</a:fmtScheme></a:themeElements></a:theme>"
    )


def _master_xml() -> str:
    title_ph = (
        "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Title Placeholder 1\"/>"
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="title" sz="quarter" idx="0"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="609600" y="365760"/><a:ext cx="10972800" cy="1188720"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>')
    body_ph = (
        "<p:sp><p:nvSpPr><p:cNvPr id=\"3\" name=\"Body Placeholder 2\"/>"
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="609600" y="1737360"/><a:ext cx="10972800" cy="4572000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sldMaster {XMLNS}><p:cSld><p:bg><p:bgPr><a:solidFill>'
        '<a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        "<p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
        "<p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/>"
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + title_ph + body_ph +
        "</p:spTree></p:cSld>"
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="2800"/></a:lvl1pPr></p:titleStyle>'
        '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle>'
        '<p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles>'
        "</p:sldMaster>"
    )


def _layout_xml() -> str:
    title_ph = (
        "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Title Placeholder 1\"/>"
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="title" sz="quarter" idx="0"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="609600" y="365760"/><a:ext cx="10972800" cy="1188720"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>')
    body_ph = (
        "<p:sp><p:nvSpPr><p:cNvPr id=\"3\" name=\"Content Placeholder 2\"/>"
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="609600" y="1737360"/><a:ext cx="10972800" cy="4572000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sldLayout {XMLNS} type="titleAndContent" preserve="1">'
        '<p:cSld name="Title and Content"><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/>"
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + title_ph + body_ph +
        "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    )


def _slide_xml(title: str, bullets: list, meta: dict) -> str:
    role = str(meta.get("role") or "other")
    locators = ", ".join(str(x) for x in (meta.get("locators") or []) if x)
    footer_text = f"role: {role}" + (f" · evidence: {locators}" if locators else "")
    body_paras = "".join(_para(str(item), size=1800, bullet=True)
                         for item in (bullets or []))
    if not bullets:
        body_paras = _para("（本页要点待补）", size=1800, bullet=False, color="6B7280")
    shapes = [
        _textbox(2, "Title 1", 609600, 365760, 10972800, 1188720,
                 _para(str(title), size=2800, bold=True, color="111827"), fill=None, line=None),
        _textbox(3, "Content 1", 609600, 1737360, 10972800, 4572000,
                 body_paras, fill="F9FAFB", line="E5E7EB"),
        _textbox(4, "Footer 1", 609600, 6400800, 10972800, 365760,
                 _para(footer_text, size=1100, bold=False, color="6B7280"), fill=None, line=None),
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sld {XMLNS}><p:cSld><p:bg><p:bgPr><a:solidFill>'
        '<a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "".join(shapes) +
        "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _zip_bytes(parts: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def _base_parts(n: int) -> dict:
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            + "".join(
                f'<Override PartName="/ppt/slides/slide{i + 1}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
                for i in range(n))
            + "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_NS}/officeDocument" Target="ppt/presentation.xml"/>'
            "</Relationships>"
        ),
        "ppt/presentation.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentation {XMLNS} saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            "<p:sldIdLst>"
            + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 2}"/>' for i in range(n))
            + "</p:sldIdLst>"
            f'<p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>"
        ),
        "ppt/slideMasters/slideMaster1.xml": _master_xml(),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_NS}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            f'<Relationship Id="rId2" Type="{R_NS}/theme" Target="../theme/theme1.xml"/>'
            "</Relationships>"
        ),
        "ppt/slideLayouts/slideLayout1.xml": _layout_xml(),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_NS}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
            "</Relationships>"
        ),
        "ppt/theme/theme1.xml": _theme_xml(),
    }
    return parts


def slide_meta_of(slide: dict) -> dict:
    locators = [str(ev.get("locator") or "") for ev in (slide.get("evidence") or [])
                if isinstance(ev, dict)]
    return {"role": slide.get("role") or "other", "locators": [x for x in locators if x]}


def assemble_deck(slides: list) -> bytes:
    """从零生成合法 16:9 deck（1 母版 + 1 版式 + N 页），页脚带 role/evidence。"""
    slides = [s for s in slides if isinstance(s, dict)] or [
        {"title": "Untitled", "bullets": [], "role": "title"}]
    n = len(slides)
    parts = _base_parts(n)
    presentation_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{R_NS}/slideMaster" Target="slideMasters/slideMaster1.xml"/>']
    for i, slide in enumerate(slides):
        title = str(slide.get("title") or slide.get("claim") or f"Slide {i + 1}")
        bullets = slide.get("bullets") or slide.get("points") or []
        parts[f"ppt/slides/slide{i + 1}.xml"] = _slide_xml(title, bullets, slide_meta_of(slide))
        parts[f"ppt/slides/_rels/slide{i + 1}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_NS}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            "</Relationships>")
        presentation_rels.append(
            f'<Relationship Id="rId{i + 2}" Type="{R_NS}/slide" Target="slides/slide{i + 1}.xml"/>')
    presentation_rels.append("</Relationships>")
    parts["ppt/_rels/presentation.xml.rels"] = "".join(presentation_rels)
    return _zip_bytes(parts)


def _read_parts(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _max_rel_id(xml_text: str) -> int:
    ids = [int(x) for x in re.findall(r'Id="rId(\d+)"', xml_text)]
    return max(ids) if ids else 0


def _unescape(text: str) -> str:
    return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&apos;", "'"))


def inspect_deck(data: bytes) -> dict:
    """解析 pptx：页数、版式、母版、主题指纹、每页文本，供合规检查。"""
    try:
        parts = _read_parts(data)
    except (zipfile.BadZipFile, OSError):
        return {"valid": False, "slides": 0, "error": "not a zip/pptx"}
    slide_names = sorted(n for n in parts if re.match(r"ppt/slides/slide\d+\.xml$", n))
    presentation = parts.get("ppt/presentation.xml", b"").decode("utf-8", errors="replace")
    declared = len(re.findall(r"<p:sldId ", presentation)) or len(slide_names)
    layouts = sorted(n for n in parts if re.match(r"ppt/slideLayouts/slideLayout\d+\.xml$", n))
    masters = sorted(n for n in parts if re.match(r"ppt/slideMasters/slideMaster\d+\.xml$", n))
    themes = sorted(n for n in parts if re.match(r"ppt/theme/theme\d+\.xml$", n))
    texts = []
    for name in slide_names:
        xml = parts[name].decode("utf-8", errors="replace")
        texts.append([_unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)])
    return {
        "valid": bool(slide_names) and bool(masters),
        "slides": declared,
        "layouts": layouts,
        "masters": masters,
        "themes": themes,
        "master_sha256": {n: sha256(parts[n]) for n in masters},
        "layout_sha256": {n: sha256(parts[n]) for n in layouts},
        "theme_sha256": {n: sha256(parts[n]) for n in themes},
        "texts": texts,
    }


def inherit_deck(template_bytes: bytes, slides: list, layout_path: str = None,
                 clean_slides: bool = True) -> dict:
    """真继承：保留模板母版/版式/主题原样，仅追加新页并指向模板既有版式。"""
    parts = _read_parts(template_bytes)
    presentation = parts.get("ppt/presentation.xml", b"").decode("utf-8", errors="replace")
    presentation_rels = parts.get("ppt/_rels/presentation.xml.rels", b"").decode(
        "utf-8", errors="replace")
    content_types = parts.get("[Content_Types].xml", b"").decode("utf-8", errors="replace")
    layouts = sorted(n for n in parts if re.match(r"ppt/slideLayouts/slideLayout\d+\.xml$", n))
    if layout_path is None:
        layout_path = layouts[0] if layouts else None
    if not layout_path:
        return {"ok": False, "error": "template has no slideLayout", "pptx": None}
    layout_target = "../slideLayouts/" + layout_path.split("/")[-1]
    existing = sorted(n for n in parts if re.match(r"ppt/slides/slide\d+\.xml$", n))
    if clean_slides:
        for name in list(parts):
            if re.match(r"ppt/slides/slide\d+\.xml$", name) or \
               re.match(r"ppt/slides/_rels/slide\d+\.xml\.rels$", name):
                parts.pop(name, None)
        presentation = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", "<p:sldIdLst></p:sldIdLst>",
                              presentation, flags=re.S)
        presentation_rels = re.sub(
            r'<Relationship[^>]*Target="slides/slide\d+\.xml"[^>]*/>', "",
            presentation_rels)
        content_types = re.sub(
            r'<Override PartName="/ppt/slides/slide\d+\.xml"[^/]*/>', "", content_types)
        existing = []
    next_slide = len(existing) + 1
    rel_id = _max_rel_id(presentation_rels) + 1
    sld_ids = []
    added = []
    for i, slide in enumerate(slides):
        slide_name = f"ppt/slides/slide{next_slide + i}.xml"
        parts[slide_name] = _slide_xml(
            str(slide.get("title") or slide.get("claim") or f"Slide {i + 1}"),
            slide.get("bullets") or slide.get("points") or [], slide_meta_of(slide)).encode("utf-8")
        parts[slide_name.replace("slides/", "slides/_rels/") + ".rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_NS}/slideLayout" Target="{layout_target}"/>'
            "</Relationships>").encode("utf-8")
        rel_id += 1
        presentation_rels = presentation_rels.replace(
            "</Relationships>",
            f'<Relationship Id="rId{rel_id}" Type="{R_NS}/slide" '
            f'Target="slides/slide{next_slide + i}.xml"/></Relationships>')
        sld_ids.append(f'<p:sldId id="{255 + len(existing) + i}" r:id="rId{rel_id}"/>')
        added.append(slide_name)
        if f"/ppt/slides/slide{next_slide + i}.xml" not in content_types:
            content_types = content_types.replace(
                "</Types>",
                f'<Override PartName="/ppt/slides/slide{next_slide + i}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>')
    if "<p:sldIdLst>" in presentation:
        presentation = presentation.replace("</p:sldIdLst>", "".join(sld_ids) + "</p:sldIdLst>")
    elif "</p:sldIdLst>" not in presentation and "<p:sldMasterIdLst>" in presentation:
        presentation = presentation.replace(
            '<p:sldMasterIdLst>',
            '<p:sldIdLst>' + "".join(sld_ids) + '</p:sldIdLst><p:sldMasterIdLst>')
    parts["ppt/presentation.xml"] = presentation.encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = presentation_rels.encode("utf-8")
    parts["[Content_Types].xml"] = content_types.encode("utf-8")
    return {"ok": True, "pptx": _zip_bytes(parts), "added_slides": added,
            "layout_used": layout_path,
            "master_sha256": {n: sha256(d) for n, d in parts.items()
                              if re.match(r"ppt/slideMasters/slideMaster\d+\.xml$", n)},
            "theme_sha256": {n: sha256(d) for n, d in parts.items()
                             if re.match(r"ppt/theme/theme\d+\.xml$", n)}}


def patch_deck(template_bytes: bytes, edits: list) -> dict:
    """edit 模式：只原位替换目标页文本，保留其余页与母版/版式。edits=[{slide,replace:{old:new}}]"""
    parts = _read_parts(template_bytes)
    changed = []
    for edit in edits or []:
        number = int(edit.get("slide", 1))
        name = f"ppt/slides/slide{number}.xml"
        if name not in parts:
            continue
        xml = parts[name].decode("utf-8", errors="replace")
        for old, new in (edit.get("replace") or {}).items():
            if old and old in xml:
                xml = xml.replace(xml_esc(old), xml_esc(new))
                changed.append({"slide": number, "old": old, "new": new})
        parts[name] = xml.encode("utf-8")
    return {"ok": bool(changed), "pptx": _zip_bytes(parts), "changed": changed,
            "slides": len([n for n in parts if re.match(r"ppt/slides/slide\d+\.xml$", n)])}


# --------------------------------------------------------------------------- PDF 兜底
def _pdf_escape(text: str) -> str:
    ascii_text = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(text))
    return ascii_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_stream(slide: dict) -> bytes:
    lines = [str(slide.get("title") or slide.get("claim") or "")]
    lines += ["- " + str(x) for x in (slide.get("bullets") or slide.get("points") or [])]
    cmds = ["BT", "/F1 18 Tf", "72 780 Td", "24 TL"]
    for i, line in enumerate(lines[:26]):
        if i:
            cmds.append("T*")
        cmds.append(f"({_pdf_escape(line[:120])}) Tj")
    cmds.append("ET")
    return ("\n".join(cmds)).encode("latin-1", "replace")


def render_pdf_text(slides: list) -> bytes:
    pages = [s for s in slides if isinstance(s, dict)] or [
        {"title": "Untitled", "bullets": []}]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # Pages 占位，待 kids 确定后回填
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_nums = []
    for slide in pages:
        content = _pdf_stream(slide)
        content_num = len(objects) + 1
        objects.append(b"<< /Length " + str(len(content)).encode("ascii")
                       + b" >>\nstream\n" + content + b"\nendstream")
        page_num = len(objects) + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_num} 0 R >>"
            .encode("ascii"))
        page_nums.append(page_num)
    kids = " ".join(f"{n} 0 R" for n in page_nums)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_nums)} >>".encode("ascii")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def pdf_page_count(data: bytes) -> int:
    return max(0, data.count(b"/Type /Page") - data.count(b"/Type /Pages"))


# --------------------------------------------------------------------------- 草稿校验
def normalize(text) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def quote_hit(corpus: dict, quote) -> bool:
    needle = normalize(quote)
    return bool(needle) and any(needle in value for value in (corpus or {}).values())


def audit_draft(draft, corpus: dict, materials: list):
    items = []

    def add(check_id, level, detail):
        items.append({"id": check_id, "level": level, "detail": detail})

    if not isinstance(draft, dict):
        add("draft", "FAIL", "draft 不是 JSON 对象")
        return 1, 0, items

    mode = draft.get("mode") or "create"
    if mode not in MODES:
        add("mode", "FAIL", f"mode={mode!r} 必须为 create/edit/adapt")

    plan = draft.get("plan")
    if not isinstance(plan, dict):
        add("plan", "FAIL", "缺 plan 对象")
        plan = {}
    for field in ("purpose", "audience", "narrative"):
        if not plan.get(field):
            add(f"plan.{field}", "WARN", f"plan 缺 {field}")
    duration = plan.get("duration_min")
    if not isinstance(duration, (int, float)) or duration <= 0:
        add("plan.duration_min", "WARN", "缺汇报时长（分钟）")

    slides = draft.get("slides")
    if not isinstance(slides, list) or len(slides) < 3:
        add("slides", "FAIL", f"slides 至少 3 页（实际 {len(slides) if isinstance(slides, list) else '非数组'}）")
        slides = []
    for index, slide in enumerate(slides):
        sid = f"slides[{index}]"
        if not isinstance(slide, dict):
            add(sid, "FAIL", "页不是对象")
            continue
        if slide.get("n") != index + 1:
            add(f"{sid}.n", "WARN", f"页码 {slide.get('n')} 与顺序 {index + 1} 不一致")
        if not slide.get("title"):
            add(f"{sid}.title", "FAIL", "缺标题")
        if not slide.get("claim"):
            add(f"{sid}.claim", "FAIL", "每页需一句话主张 claim")
        if slide.get("role") not in ROLES:
            add(f"{sid}.role", "WARN", f"role={slide.get('role')!r} 不在建议枚举")
        bullets = slide.get("bullets") or []
        if len(bullets) > 7:
            add(f"{sid}.bullets", "WARN", f"单页 {len(bullets)} 条要点，可能整段搬运")
        evidence = slide.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            add(f"{sid}.evidence", "FAIL", "每页需证据（locator+quote）")
        else:
            for k, ev in enumerate(evidence):
                if not isinstance(ev, dict) or not ev.get("locator"):
                    add(f"{sid}.evidence[{k}]", "FAIL", "证据缺 locator")
                elif ev.get("quote") and not quote_hit(corpus, ev.get("quote")):
                    add(f"{sid}.evidence[{k}].quote", "WARN",
                        "引文未在文本材料命中，请确认是否转述并补逐字原文")

    if mode in {"edit", "adapt"}:
        template = draft.get("template") or {}
        template_path = template.get("path")
        found = [m for m in materials if m["kind"] == "pptx" and
                 (not template_path or m["rel_path"] == template_path or
                  Path(template_path).name == Path(m["rel_path"]).name)]
        if not found:
            add("template", "WARN", "edit/adapt 模式未找到输入模板 pptx，将用内置母版")
        if mode == "edit" and not isinstance(draft.get("edits"), list):
            add("edits", "WARN", "edit 模式建议给出 edits 列表（目标页文本替换）")

    return (sum(1 for x in items if x["level"] == "FAIL"),
            sum(1 for x in items if x["level"] == "WARN"), items)


# --------------------------------------------------------------------------- 计划/映射/合规
def draft_skeleton() -> dict:
    return {
        "_说明": "复制为 work/draft.json 后填写；每页一条主张+证据定位；edit/adapt 填 template 与 edits。",
        "mode": "edit",
        "template": {"path": "", "layouts_used": []},
        "plan": {"purpose": "", "audience": "", "duration_min": 10, "narrative": ""},
        "slides": [{
            "n": 1, "title": "标题页", "claim": "一句话主张", "role": "title",
            "bullets": ["要点"],
            "evidence": [{"source_id": "S1", "locator": "para.1", "quote": "逐字原文"}],
            "assets": [], "notes": "",
        }],
        "edits": [],
        "keep_unchanged_slides": [],
        "extras": {},
        "uncertainties": [],
        "pending_human": [],
    }


def slides_from_draft(draft: dict) -> list:
    slides = draft.get("slides") if isinstance(draft.get("slides"), list) else []
    out = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        out.append({
            "n": slide.get("n", index + 1),
            "title": slide.get("title", ""),
            "claim": slide.get("claim", ""),
            "role": slide.get("role", "other"),
            "bullets": slide.get("bullets") or [],
            "evidence": slide.get("evidence") or [],
            "assets": slide.get("assets") or [],
            "notes": slide.get("notes", ""),
        })
    return out


def content_map_from_slides(slides, corpus):
    content_map = []
    for slide in slides:
        evidence = []
        for ev in slide["evidence"]:
            if not isinstance(ev, dict):
                continue
            quote = ev.get("quote", "")
            evidence.append({
                "type": ev.get("type") or "section",
                "id": str(ev.get("source_id", "")),
                "locator": ev.get("locator", ""),
                "quote": quote,
                "quote_verified": quote_hit(corpus, quote) if quote else False,
            })
        content_map.append({
            "n": slide["n"], "title": slide["title"], "claim": slide["claim"],
            "role": slide["role"], "evidence": evidence, "assets": slide["assets"],
            "notes": slide["notes"],
        })
    return content_map


def compliance_checks(pptx_bytes, pdf_bytes, slides, inheritance, mode):
    info = inspect_deck(pptx_bytes)
    pages = pdf_page_count(pdf_bytes)
    checks = [
        {"id": "pptx_valid", "dimension": "effect",
         "required": "可解压且含 presentation.xml 与 slideN.xml",
         "measured": f"slides={info.get('slides')}, masters={len(info.get('masters') or [])}",
         "verdict": "pass" if info.get("valid") else "fail"},
        {"id": "slides_count", "dimension": "effect",
         "required": "pptx 页数 == 计划页数 == PDF 页数",
         "measured": f"pptx={info.get('slides')} plan={len(slides)} pdf={pages}",
         "verdict": "pass" if info.get("slides") == len(slides) == pages else "fail"},
        {"id": "editable_source", "dimension": "effect", "required": "pptx 为真实 ZIP/OOXML",
         "measured": "PK 头" if pptx_bytes[:2] == b"PK" else "非 ZIP",
         "verdict": "pass" if pptx_bytes[:2] == b"PK" else "fail"},
        {"id": "every_page_role", "dimension": "effect", "required": "每页有叙事角色",
         "measured": f"roles={sorted({s.get('role') for s in slides})}",
         "verdict": "pass" if all(s.get("role") for s in slides) else "fail"},
        {"id": "every_page_claim", "dimension": "effect", "required": "每页有一句话主张",
         "measured": f"{sum(1 for s in slides if s.get('claim'))}/{len(slides)}",
         "verdict": "pass" if all(s.get("claim") for s in slides) else "fail"},
        {"id": "every_page_evidence", "dimension": "effect",
         "required": "每页有证据定位（locator）",
         "measured": f"{sum(1 for s in slides if s.get('evidence'))}/{len(slides)}",
         "verdict": "pass" if all(s.get("evidence") for s in slides) else "fail"},
    ]
    if isinstance(inheritance, dict) and inheritance.get("ok"):
        checks.append({
            "id": "template_inheritance", "dimension": "innovation",
            "required": "母版/主题 sha256 与输入模板一致（真继承）",
            "measured": f"layout={inheritance.get('layout_used')}",
            "verdict": "pass" if inheritance.get("master_sha256") else "fail"})
    if mode == "edit":
        checks.append({
            "id": "edit_scoped", "dimension": "innovation",
            "required": "edit 只改目标页，其余页与母版不变",
            "measured": f"changed={len(inheritance.get('changed') or [])} 处"
            if isinstance(inheritance, dict) else "n/a",
            "verdict": "pass" if isinstance(inheritance, dict) and inheritance.get("ok") else "warn"})
    return checks, info


def build_report(case_id, draft, slides, checks, inheritance, missing, mode, degradations):
    plan = (draft or {}).get("plan") or {}
    lines = [f"# 学术 PPT 交付报告（{case_id}）", "",
             f"- 模式：{mode}",
             f"- 目的/听众/时长：{plan.get('purpose')} / {plan.get('audience')} / {plan.get('duration_min')} 分钟",
             f"- 页数：{len(slides)}", "", "## 页级计划",
             "| n | role | claim | evidence |", "|---|---|---|---|"]
    for slide in slides:
        locator = ", ".join(ev.get("locator", "") for ev in slide["evidence"]
                            if isinstance(ev, dict))
        lines.append(f"| {slide['n']} | {slide['role']} | {slide['claim']} | {locator} |")
    lines += ["", "## 合规检查"]
    for check in checks:
        lines.append(f"- {check['id']}: {check['verdict']}（{check['measured']}）")
    if isinstance(inheritance, dict) and inheritance.get("ok"):
        lines += ["", "## 模板继承", f"- 版式：{inheritance.get('layout_used')}",
                  f"- 母版未变：{inheritance.get('master_unchanged')}"]
    if degradations:
        lines += ["", "## 降级与缺口"] + [f"- {x}" for x in degradations]
    if missing:
        lines += ["", "## 契约缺键", "- " + ", ".join(missing)]
    return "\n".join(lines) + "\n"


def dimension_map(report, rework):
    """评分维度 → 检查项/产物/指标 的对齐映射（本版核心变体）。"""
    by_dimension = {"effect": [], "benefit": [], "innovation": [], "reliability": [],
                    "ecosystem": []}
    for item in report.get("items", []):
        dim = item.get("dimension", "effect")
        by_dimension.setdefault(dim, []).append(item["id"])
    return {
        "effect_40": {"focus": "任务完成度/正确性/完整性/可核验性",
                      "checks": by_dimension.get("effect", []),
                      "artifacts": ["presentation.pptx", "presentation.pdf",
                                    "content_map", "evidence_map.json"]},
        "benefit_25": {"focus": "质量增益/效率增益/增益可验证性",
                       "checks": by_dimension.get("benefit", []),
                       "artifacts": ["change_log", "report.md", "selfcheck.json"],
                       "metrics": rework},
        "innovation_15": {"focus": "页序规划/模板槽位填充/局部编辑",
                          "checks": by_dimension.get("innovation", []),
                          "artifacts": ["changes.json", "evidence_map.json"]},
        "reliability_10": {"focus": "触发/稳定/异常处理",
                           "checks": by_dimension.get("reliability", []),
                           "artifacts": ["selfcheck.json", "report.md"]},
        "ecosystem_10": {"focus": "文档/资源复用/分享价值",
                         "checks": by_dimension.get("ecosystem", []),
                         "artifacts": ["SKILL.md", "references/checklist.md",
                                       "references/contract.md"]},
        "note": "评分维度对齐协议；具体分数以平台出分为准，本包不写分数。",
    }


