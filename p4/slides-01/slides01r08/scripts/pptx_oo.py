#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准库最小 PPTX（OOXML）构建器：无 python-pptx、无模板时的降级兜底。

设计取舍：
  - 只保证“可打开、可编辑、可放映”的最低真实文件，不做母版/版式继承（那是模板路径）。
  - 每页一个标题 + 若干要点文本；讲解备注不嵌入（避免 notes 部件拖垮文件有效性），
    由执行 Agent 写入同目录 notes.md，并在 render_record 标注该降级。
  - 返回 (ok, err)；任何异常都不会抛给上层，调用方记入 degradations。

优先顺序（SKILL.md 已约定）：给定模板真继承 > python-pptx > 本构建器。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

CONTENT_TYPES = _XML_DECL + (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
    '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
    '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    "{SLIDES}"
    "</Types>"
)

ROOT_RELS = _XML_DECL + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    "</Relationships>"
)

PRESENTATION_RELS = _XML_DECL + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    "<SLIDE_RELS>"
    "</Relationships>"
)

SLIDE_MASTER_RELS = _XML_DECL + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
    "</Relationships>"
)

SLIDE_LAYOUT_RELS = _XML_DECL + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
    "</Relationships>"
)

SLIDE_RELS = _XML_DECL + (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
    "</Relationships>"
)

PRESENTATION = _XML_DECL + (
    '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
    '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
    "<SLIDE_ID_LST>"
    '<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
    '<p:notesSz cx="6858000" cy="9144000"/>'
    "</p:presentation>"
)

SLIDE_MASTER = _XML_DECL + (
    '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
    '<p:cSld><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title Placeholder"/><p:cNvSpPr/><p:nvPr>'
    '<p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
    '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Body Placeholder"/><p:cNvSpPr/><p:nvPr>'
    '<p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
    '</p:spTree></p:cSld>'
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
    'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    "</p:sldMaster>"
)

SLIDE_LAYOUT = _XML_DECL + (
    '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="obj">'
    '<p:cSld name="Title and Content"><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
    '<p:spPr><a:xfrm><a:off x="838200" y="365125"/><a:ext cx="10515600" cy="1325563"/></a:xfrm></p:spPr>'
    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
    '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content Placeholder 2"/><p:cNvSpPr/><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
    '<p:spPr><a:xfrm><a:off x="838200" y="1825625"/><a:ext cx="10515600" cy="4333875"/></a:xfrm></p:spPr>'
    '<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
    '</p:spTree></p:cSld>'
    '<p:clrMapOvr><a:overrideClrMapping bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
    'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
    'hlink="hlink" folHlink="folHlink"/></p:clrMapOvr>'
    "</p:sldLayout>"
)

THEME = _XML_DECL + (
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office">'
    '<a:themeElements>'
    '<a:clrScheme name="Office">'
    '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="1F497D"/></a:dk2><a:lt2><a:srgbClr val="EEECE1"/></a:lt2>'
    '<a:accent1><a:srgbClr val="4F81BD"/></a:accent1><a:accent2><a:srgbClr val="C0504D"/></a:accent2>'
    '<a:accent3><a:srgbClr val="9BBB59"/></a:accent3><a:accent4><a:srgbClr val="8064A2"/></a:accent4>'
    '<a:accent5><a:srgbClr val="4BACC6"/></a:accent5><a:accent6><a:srgbClr val="F79646"/></a:accent6>'
    '<a:hlink><a:srgbClr val="0000FF"/></a:hlink><a:folHlink><a:srgbClr val="800080"/></a:folHlink>'
    '</a:clrScheme>'
    '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
    '<a:fmtScheme name="Office">'
    '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    '<a:lnStyleLst><a:ln w="9525" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
    '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
    '</a:fmtScheme>'
    '</a:themeElements>'
    '</a:theme>'
)

CORE = _XML_DECL + (
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    '<dc:title>Academic presentation</dc:title><dc:creator>slides-01 skill</dc:creator>'
    '<cp:lastModifiedBy>slides-01</cp:lastModifiedBy>'
    "</cp:coreProperties>"
)

APP = _XML_DECL + (
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    '<Application>slides-01</Application><PresentationFormat>On-screen Show (16:9)</PresentationFormat>'
    "</Properties>"
)


def _esc(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _para(text: str, level: int = 0, bullet: bool = True) -> str:
    bu = '<a:buChar char="•"/>' if bullet and level == 0 else ('<a:buNone/>' if not bullet else '')
    lvl = f' lvl="{level}"' if level else ""
    return (
        f'<a:p><a:pPr lvl="{level}" marL="{228600 + 228600 * level}" indent="-228600">{bu}</a:pPr>'
        f'<a:r><a:rPr lang="en-US" dirty="0"/><a:t>{_esc(text)}</a:t></a:r></a:p>'
    )


def _slide_xml(slide: dict, idx: int) -> str:
    title = str(slide.get("title") or f"Slide {idx}")
    body = slide.get("body") or []
    if isinstance(body, str):
        body = [body]
    paras = "".join(_para(str(t)) for t in body if str(t).strip())
    body_xml = paras or _para("（本页要点未填写：请依据材料补齐，禁止占位交付）", bullet=False)
    return _XML_DECL + (
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="838200" y="365125"/><a:ext cx="10515600" cy="1325563"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/>{_para(title, bullet=False)}</p:txBody></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content"/><p:cNvSpPr/><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="838200" y="1825625"/><a:ext cx="10515600" cy="4333875"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/>{body_xml}</p:txBody></p:sp>'
        '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
        "</p:sld>"
    )


def build_deck(slides, out_path, title: str = "") -> tuple:
    """构建最小可编辑 pptx。slides: [{title, body:[...]}]。返回 (ok, err)。"""
    if not slides:
        return False, "无页内容，拒绝生成空 pptx"
    try:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        slide_ids = []
        slide_rels = []
        slide_overrides = []
        n = len(slides)
        for i in range(1, n + 1):
            slide_ids.append(
                f'<p:sldId id="{255 + i}" r:id="rId{1 + i}"/>'
            )
            slide_rels.append(
                f'<Relationship Id="rId{1 + i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
            )
            slide_overrides.append(
                f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            )

        presentation = PRESENTATION.replace(
            "<SLIDE_ID_LST>",
            f"<p:sldIdLst>{''.join(slide_ids)}</p:sldIdLst>",
        )
        presentation_rels = PRESENTATION_RELS.replace(
            "<SLIDE_RELS>", "".join(slide_rels)
        )
        content_types = CONTENT_TYPES.replace(
            "{SLIDES}", "".join(slide_overrides)
        )

        parts = {
            "[Content_Types].xml": content_types,
            "_rels/.rels": ROOT_RELS,
            "docProps/core.xml": CORE,
            "docProps/app.xml": APP,
            "ppt/presentation.xml": presentation,
            "ppt/_rels/presentation.xml.rels": presentation_rels,
            "ppt/slideMasters/slideMaster1.xml": SLIDE_MASTER,
            "ppt/slideMasters/_rels/slideMaster1.xml.rels": SLIDE_MASTER_RELS,
            "ppt/slideLayouts/slideLayout1.xml": SLIDE_LAYOUT,
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels": SLIDE_LAYOUT_RELS,
            "ppt/theme/theme1.xml": THEME,
        }
        for i, slide in enumerate(slides, start=1):
            parts[f"ppt/slides/slide{i}.xml"] = _slide_xml(slide, i)
            parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = SLIDE_RELS

        with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in parts.items():
                zf.writestr(name, content.encode("utf-8"))
        return True, None
    except Exception as exc:  # noqa: BLE001 —— 兜底不得崩
        return False, f"{exc.__class__.__name__}: {exc}"
