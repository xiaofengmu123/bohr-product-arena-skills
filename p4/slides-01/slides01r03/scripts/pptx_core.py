#!/usr/bin/env python3
"""Deterministic OOXML (.pptx) builder and validators. Standard library only.

Why this module exists
----------------------
The slides-01 task requires an *editable* .pptx plus a page-consistent PDF.
python-pptx / lxml may be absent in the runtime, and their output is not
byte-stable across versions.  Building the OOXML package directly with
``zipfile`` + string templates gives us:

1. true template inheritance -- the template's slideMasters / slideLayouts /
   theme / media are copied verbatim and only the slide parts are replaced;
2. byte-identical output for identical inputs -- fixed zip entry order, a
   fixed timestamp, no environment-dependent fields;
3. full control over placeholder geometry, so long text is placed into the
   layout's own title/body/picture placeholders instead of floating boxes.

Content decisions (claims, evidence, quotes) are authored by the calling
agent in ``work/draft.json``; this module only does deterministic mechanics.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}

REL_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
REL_LAYOUT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
REL_MASTER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster"
REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

ROLES = {"title", "background", "method", "result", "discussion",
         "limitation", "conclusion", "other"}

ROLE_LAYOUT_PREFS = {
    "title": ["Title Slide", "标题幻灯片", "Title"],
    "background": ["Title and Content", "标题和内容", "Title and Content"],
    "method": ["Title and Content", "标题和内容"],
    "result": ["Title and Content", "标题和内容", "Two Content", "两栏内容"],
    "discussion": ["Title and Content", "标题和内容"],
    "limitation": ["Title and Content", "标题和内容"],
    "conclusion": ["Title and Content", "标题和内容", "Section Header", "节标题"],
    "other": ["Title and Content", "标题和内容"],
}

# Standard PowerPoint 16:9 geometry, in EMU (914400 EMU per inch).
EMU_TITLE = {"x": 838200, "y": 365125, "cx": 10515600, "cy": 1325563}
EMU_BODY = {"x": 838200, "y": 1825625, "cx": 10515600, "cy": 4381500}
EMU_SUBTITLE = {"x": 838200, "y": 2482375, "cx": 10515600, "cy": 1325563}

ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # fixed timestamp for reproducible archives

MAX_TEXT_FILE = 2 * 1024 * 1024  # skip material text files above 2 MB


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------
def _qn(ns: str, tag: str) -> str:
    return "{%s}%s" % (NS[ns], tag)


def esc(text: Any) -> str:
    """Escape text for XML element content."""
    s = "" if text is None else str(text)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def has_cjk(text: str) -> bool:
    return any('\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf' for ch in text)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_hash(obj: Any) -> str:
    canon = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()


def quote_in_text(quote: str, text: str) -> bool:
    """True when a whitespace-normalized quote appears verbatim in the text."""
    q = normalize_ws(quote)
    if not q:
        return False
    return q in normalize_ws(text)


def read_bytes(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# ZIP packing / unpacking (deterministic)
# ---------------------------------------------------------------------------
def write_zip(path: Path, parts: Dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for name in sorted(parts):
            zi = zipfile.ZipInfo(filename=name, date_time=ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o100644 << 16  # regular file, rw-r--r--
            zi.create_system = 3               # unix, avoids platform variance
            zf.writestr(zi, parts[name])


def read_zip(path: Path) -> Dict[str, bytes]:
    parts: Dict[str, bytes] = {}
    with zipfile.ZipFile(str(path), "r") as zf:
        for name in zf.namelist():
            if not name.endswith("/"):
                parts[name] = zf.read(name)
    return parts


def parts_fingerprint(parts: Dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(parts):
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        h.update(parts[name])
        h.update(b"\x00")
    return h.hexdigest()


def is_valid_pptx(path: Path) -> Tuple[bool, str]:
    if not path.is_file():
        return False, "missing file: %s" % path
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            names = zf.namelist()
            if "[Content_Types].xml" not in names:
                return False, "[Content_Types].xml missing"
            if "ppt/presentation.xml" not in names:
                return False, "ppt/presentation.xml missing"
            slides = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            if not slides:
                return False, "no slide parts"
    except zipfile.BadZipFile as exc:
        return False, "not a zip/pptx: %s" % exc
    return True, "ok"


def pptx_slide_count(path: Path) -> int:
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            return len([n for n in zf.namelist()
                        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)])
    except (zipfile.BadZipFile, OSError):
        return 0


def pdf_page_count(path: Path) -> int:
    """Heuristic page count for rendered PDFs (stdlib only).

    Counts ``/Type /Page`` objects, which is reliable for LibreOffice output.
    Falls back to the maximum ``/Count`` on a pages-tree node. This is used
    only for a consistency check, never as an authoritative claim.
    """
    data = read_bytes(path)
    if not data or not data.startswith(b"%PDF-"):
        return 0
    n_pages = len(re.findall(rb"/Type\s*/Page(?![sS])", data))
    if n_pages > 0:
        return n_pages
    counts = [int(c) for c in re.findall(rb"/Count\s+(\d+)", data)]
    return max(counts) if counts else 0


# ---------------------------------------------------------------------------
# Layout discovery (read-only parse of template parts)
# ---------------------------------------------------------------------------
def _infer_ph_type(ptype: Optional[str], name: str, idx: Optional[str]) -> str:
    if ptype:
        return ptype
    low = name.lower()
    if "title" in low or "标题" in name:
        return "title"
    if "picture" in low or "图片" in name or "clip" in low:
        return "pic"
    if "subtitle" in low or "副标题" in name:
        return "subTitle"
    if "content" in low or "body" in low or "内容" in name or "text" in low:
        return "body"
    if idx == "0":
        return "title"
    return "body"


def parse_layout(xml_bytes: bytes) -> Tuple[str, List[Dict[str, Any]]]:
    """Return (layout_name, placeholders). Each placeholder has
    type / idx / name / geo(x,y,cx,cy)."""
    root = ET.fromstring(xml_bytes)
    name = root.get("name") or ""
    phs: List[Dict[str, Any]] = []
    for sp in root.iter(_qn("p", "sp")):
        ph = sp.find(".//" + _qn("p", "ph"))
        if ph is None:
            continue
        cnvpr = sp.find(".//" + _qn("p", "cNvPr"))
        nm = cnvpr.get("name") if cnvpr is not None else ""
        raw_type = ph.get("type")
        idx = ph.get("idx")
        ptype = _infer_ph_type(raw_type, nm, idx)
        geo = None
        xfrm = sp.find(".//" + _qn("a", "xfrm"))
        if xfrm is not None:
            off = xfrm.find(_qn("a", "off"))
            ext = xfrm.find(_qn("a", "ext"))
            if off is not None and ext is not None:
                try:
                    geo = {"x": int(off.get("x")), "y": int(off.get("y")),
                           "cx": int(ext.get("cx")), "cy": int(ext.get("cy"))}
                except (TypeError, ValueError):
                    geo = None
        phs.append({"type": ptype, "idx": idx, "name": nm, "geo": geo})
    return name, phs


def choose_layout(layouts: Dict[str, List[Dict[str, Any]]], role: str,
                  requested: Optional[str] = None) -> str:
    """Pick a layout filename for a page. Deterministic preference order."""
    if requested:
        wanted = requested.strip().lower()
        for fname, _ in layouts.items():
            if wanted in fname.lower():
                return fname
    prefs = ROLE_LAYOUT_PREFS.get(role, ROLE_LAYOUT_PREFS["other"])
    for pref in prefs:
        for fname in sorted(layouts):
            if pref.lower() in fname.lower():
                return fname
    return sorted(layouts)[0] if layouts else ""


# ---------------------------------------------------------------------------
# Slide XML generation
# ---------------------------------------------------------------------------
def _run(text: str, sz: int, bold: bool, lang: str) -> str:
    rpr = '<a:rPr lang="%s" altLang="en-US" sz="%d" dirty="0"%s/>' % (
        lang, sz, ' b="1"' if bold else "")
    return '<a:r>%s<a:t>%s</a:t></a:r>' % (rpr, esc(text))


def _bullet_paragraph(text: str, sz: int, lang: str) -> str:
    ppr = ('<a:pPr marL="285750" indent="-285750">'
           '<a:buFont typeface="Arial" pitchFamily="34" charset="0"/>'
           '<a:buChar char="&#8226;"/></a:pPr>')
    return '<a:p>%s%s</a:p>' % (ppr, _run(text, sz, False, lang))


def _plain_paragraph(text: str, sz: int, bold: bool, lang: str) -> str:
    return '<a:p><a:pPr/>%s</a:p>' % _run(text, sz, bold, lang)


def _shape_xml(shape_id: int, name: str, ph_type: str, idx: Optional[str],
               geo: Optional[Dict[str, int]], text_paras: str) -> str:
    if geo is None:
        if ph_type in ("title", "ctrTitle"):
            geo = EMU_TITLE
        elif ph_type == "subTitle":
            geo = EMU_SUBTITLE
        else:
            geo = EMU_BODY
    ph_attrs = ' type="%s"' % ph_type
    if idx is not None:
        ph_attrs += ' idx="%s"' % idx
    return (
        '<p:sp>'
        '<p:nvSpPr><p:cNvPr id="%d" name="%s"/>'
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph%s/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr>'
        '<a:lstStyle/>%s</p:txBody>'
        '</p:sp>'
    ) % (shape_id, esc(name), ph_attrs, geo["x"], geo["y"], geo["cx"], geo["cy"],
         text_paras)


def _picture_xml(shape_id: int, name: str, rel_id: str,
                 geo: Optional[Dict[str, int]]) -> str:
    if geo is None:
        geo = {"x": 7315200, "y": 1825625, "cx": 4572000, "cy": 3429000}
    return (
        '<p:pic>'
        '<p:nvPicPr><p:cNvPr id="%d" name="%s"/>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        '<p:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '</p:pic>'
    ) % (shape_id, esc(name), rel_id, geo["x"], geo["y"], geo["cx"], geo["cy"])


def build_slide_xml(page: Dict[str, Any], layout_phs: List[Dict[str, Any]],
                    img_rel_ids: List[str], lang: str,
                    slide_num: int) -> str:
    role = page.get("role") or "other"
    title = str(page.get("title") or page.get("claim") or "")
    claim = str(page.get("claim") or "")
    bullets = [str(b) for b in (page.get("bullets") or []) if str(b).strip()]
    figures = page.get("figures") or []

    title_ph = next((p for p in layout_phs if p["type"] in ("title", "ctrTitle")), None)
    body_phs = [p for p in layout_phs if p["type"] in ("body", "subTitle", "obj")]
    pic_phs = [p for p in layout_phs if p["type"] in ("pic", "clipArt")]

    shapes: List[str] = []
    sid = 2
    if title:
        geo = title_ph["geo"] if title_ph else None
        ph_type = (title_ph["type"] if title_ph else
                   ("ctrTitle" if role == "title" else "title"))
        idx = title_ph["idx"] if title_ph else "0"
        # Title slide: put the claim into the subtitle/body placeholder.
        if role == "title":
            paras = _plain_paragraph(title, 4000, True, lang)
        else:
            paras = _plain_paragraph(title, 2800, True, lang)
        shapes.append(_shape_xml(sid, "Title %d" % (slide_num + 1), ph_type, idx,
                                 geo, paras))
        sid += 1

    if role == "title" and claim:
        sub = next((p for p in body_phs if p["type"] == "subTitle"), None)
        if sub is None and body_phs:
            sub = body_phs[0]
        if sub is not None:
            shapes.append(_shape_xml(sid, "Subtitle %d" % (slide_num + 1),
                                     sub["type"], sub["idx"], sub["geo"],
                                     _plain_paragraph(claim, 1800, False, lang)))
            sid += 1
            body_phs = [p for p in body_phs if p is not sub]
    elif bullets:
        body = body_phs[0] if body_phs else None
        if body is not None:
            paras = "".join(_bullet_paragraph(b, 1800, lang) for b in bullets)
            shapes.append(_shape_xml(sid, "Content %d" % (slide_num + 1),
                                     body["type"], body["idx"], body["geo"], paras))
            sid += 1

    for i, fig in enumerate(figures):
        if i < len(img_rel_ids):
            geo = pic_phs[i]["geo"] if i < len(pic_phs) else None
            shapes.append(_picture_xml(sid, "Figure %d" % (i + 1),
                                       img_rel_ids[i], geo))
            sid += 1

    return (
        XML_DECL +
        '<p:sld xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
        '<p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '%s'
        '</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
        '</p:sld>'
    ) % (NS["a"], NS["r"], NS["p"], "".join(shapes))


# ---------------------------------------------------------------------------
# Minimal from-scratch deck (fallback when no template is supplied)
# ---------------------------------------------------------------------------
def minimal_content_types(slide_count: int, media_exts: List[str]) -> str:
    parts = [
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
    ]
    for ext in sorted(set(media_exts)):
        ctype = "image/png" if ext == "png" else "image/jpeg"
        parts.append('<Default Extension="%s" ContentType="%s"/>' % (esc(ext), ctype))
    parts += [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
    ]
    for i in range(1, slide_count + 1):
        parts.append('<Override PartName="/ppt/slides/slide%d.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % i)
    parts += [
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    return (XML_DECL + '<Types xmlns="%s">%s</Types>') % (NS["ct"], "".join(parts))


def minimal_root_rels() -> str:
    return (
        XML_DECL +
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    ) % NS["rel"]


def minimal_presentation_xml(slide_count: int) -> str:
    slide_ids = "".join(
        '<p:sldId id="%d" r:id="rIdS%d"/>' % (255 + i, i) for i in range(1, slide_count + 1))
    return (
        XML_DECL +
        '<p:presentation xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        '<p:sldIdLst>%s</p:sldIdLst>'
        '<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
        '<p:notesSz cx="6858000" cy="9144000"/>'
        '</p:presentation>'
    ) % (NS["a"], NS["r"], NS["p"], slide_ids)


def minimal_presentation_rels(slide_count: int) -> str:
    rels = ['<Relationship Id="rId1" Type="%s" Target="slideMasters/slideMaster1.xml"/>' % REL_MASTER]
    for i in range(1, slide_count + 1):
        rels.append('<Relationship Id="rIdS%d" Type="%s" Target="slides/slide%d.xml"/>'
                    % (i, REL_SLIDE, i))
    return (XML_DECL + '<Relationships xmlns="%s">%s</Relationships>'
            ) % (NS["rel"], "".join(rels))


def minimal_slide_master() -> str:
    tx = '<p:txStyles>'
    for style in ("titleStyle", "bodyStyle", "otherStyle"):
        tx += ('<p:%s><a:lvl1pPr algn="l" rtl="0"><a:defRPr sz="1800"/>'
               '</a:lvl1pPr></p:%s>') % (style, style)
    tx += '</p:txStyles>'
    return (
        XML_DECL +
        '<p:sldMaster xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
        '<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>'
        '<a:effectLst/></p:bgPr></p:bg>'
        '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree>'
        '</p:cSld>'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst>'
        '<p:sldLayoutId id="2147483649" r:id="rId1"/>'
        '<p:sldLayoutId id="2147483650" r:id="rId2"/>'
        '</p:sldLayoutIdLst>%s</p:sldMaster>'
    ) % (NS["a"], NS["r"], NS["p"], tx)


def _layout(title: str, ph_specs: List[Tuple[str, str, str, Dict[str, int]]]) -> str:
    shapes = ""
    sid = 2
    for name, ptype, idx, geo in ph_specs:
        shapes += _shape_xml(sid, name, ptype, idx, geo, "")
        sid += 1
    return (
        XML_DECL +
        '<p:sldLayout xmlns:a="%s" xmlns:r="%s" xmlns:p="%s" type="obj" preserve="1">'
        '<p:cSld name="%s"><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        '%s</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
        '</p:sldLayout>'
    ) % (NS["a"], NS["r"], NS["p"], esc(title), shapes)


def minimal_slide_layout1() -> str:
    return _layout("Title Slide", [
        ("Title 1", "ctrTitle", "0", EMU_TITLE),
        ("Subtitle 2", "subTitle", "1", EMU_SUBTITLE),
    ])


def minimal_slide_layout2() -> str:
    return _layout("Title and Content", [
        ("Title 1", "title", "0", EMU_TITLE),
        ("Content 2", "body", "1", EMU_BODY),
    ])


def minimal_theme() -> str:
    accents = "".join('<a:accent%d><a:srgbClr val="%s"/></a:accent%d>' % (i, c, i)
                      for i, c in enumerate(
                          ["4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47"], 1))
    fill = ('<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>')
    ln = ('<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
          '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
          '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>')
    eff = ('<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>'
           '<a:effectStyle><a:effectLst/></a:effectStyle>'
           '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>')
    bg = ('<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
          '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
          '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>')
    return (
        XML_DECL +
        '<a:theme xmlns:a="%s" name="Office">'
        '<a:themeElements>'
        '<a:clrScheme name="Office">'
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        '<a:dk2><a:srgbClr val="44546A"/></a:dk2>'
        '<a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
        '%s'
        '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
        '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
        '</a:clrScheme>'
        '<a:fontScheme name="Office">'
        '<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
        '</a:fontScheme>'
        '<a:fmtScheme name="Office">%s%s%s%s</a:fmtScheme>'
        '</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/>'
        '</a:theme>'
    ) % (NS["a"], accents, fill, ln, eff, bg)


def minimal_core_props(title: str) -> str:
    return (
        XML_DECL +
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:title>%s</dc:title><dc:creator>slides-01 skill</dc:creator>'
        '<cp:lastModifiedBy>slides-01 skill</cp:lastModifiedBy>'
        '</cp:coreProperties>'
    ) % esc(title)


def minimal_app_props(slide_count: int) -> str:
    return (
        XML_DECL +
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Slides>%d</Slides><Notes>0</Notes><HiddenSlides>0</HiddenSlides>'
        '<ScaleCrop>false</ScaleCrop><HeadingPairs>'
        '<vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Theme</vt:lpstr></vt:variant>'
        '<vt:variant><vt:i4>1</vt:i4></vt:variant></vt:vector></HeadingPairs>'
        '<TitlesOfParts><vt:vector size="1" baseType="lpstr"><vt:lpstr>Office Theme</vt:lpstr></vt:vector></TitlesOfParts>'
        '<Company></Company><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged>'
        '<AppVersion>16.0000</AppVersion></Properties>'
    ) % slide_count


def minimal_slide_master_rels() -> str:
    return (
        XML_DECL +
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="%s" Target="../slideLayouts/slideLayout1.xml"/>'
        '<Relationship Id="rId2" Type="%s" Target="../slideLayouts/slideLayout2.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
        '</Relationships>'
    ) % (NS["rel"], REL_LAYOUT, REL_LAYOUT)


def minimal_layout_rels(master_rels: str) -> str:
    return (
        XML_DECL +
        '<Relationships xmlns="%s">'
        '<Relationship Id="rId1" Type="%s" Target="../slideMasters/slideMaster1.xml"/>'
        '</Relationships>'
    ) % (NS["rel"], REL_MASTER)


# ---------------------------------------------------------------------------
# Draft validation
# ---------------------------------------------------------------------------
def validate_draft(draft: Any) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(draft, dict):
        return False, ["draft must be a JSON object"]
    pages = draft.get("pages")
    if not isinstance(pages, list) or not pages:
        errors.append("draft.pages must be a non-empty list")
        return False, errors
    for i, page in enumerate(pages, 1):
        if not isinstance(page, dict):
            errors.append("page %d is not an object" % i)
            continue
        role = page.get("role")
        if role not in ROLES:
            errors.append("page %d: invalid role %r" % (i, role))
        if not str(page.get("claim") or "").strip():
            errors.append("page %d: claim is empty" % i)
        ev = page.get("evidence")
        if not isinstance(ev, list) or not ev:
            errors.append("page %d: evidence must be a non-empty list" % i)
        else:
            for j, e in enumerate(ev, 1):
                if not isinstance(e, dict):
                    errors.append("page %d evidence %d: not an object" % (i, j))
                    continue
                for key in ("source_id", "locator", "quote"):
                    if not str(e.get(key) or "").strip():
                        errors.append("page %d evidence %d: %s is empty" % (i, j, key))
    return (not errors), errors


# ---------------------------------------------------------------------------
# Template inheritance (the primary, graded path)
# ---------------------------------------------------------------------------
def _parse_relationships(xml_bytes: bytes) -> List[Dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    out = []
    for rel in root:
        out.append({"id": rel.get("Id"), "type": rel.get("Type"),
                    "target": rel.get("Target"), "mode": rel.get("TargetMode")})
    return out


def _build_content_types_inherited(kept: Dict[str, bytes], slide_count: int,
                                   media_exts: List[str]) -> str:
    defaults = {}
    overrides = {}
    ext_ct = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg",
              "gif": "image/gif", "svg": "image/svg+xml"}
    for name in kept:
        if name in ("[Content_Types].xml",):
            continue
        if name.endswith(".rels"):
            defaults["rels"] = "application/vnd.openxmlformats-package.relationships+xml"
        elif name.endswith(".xml"):
            ext = name.rsplit(".", 1)[-1]
            defaults[ext] = "application/xml"
        elif name.endswith(".png"):
            defaults["png"] = "image/png"
        elif name.lower().endswith((".jpg", ".jpeg")):
            defaults["jpeg"] = "image/jpeg"
    ct_map = {
        "/ppt/presentation.xml": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
        "/docProps/app.xml": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    }
    for name in kept:
        if name.endswith(".xml") and name.startswith("ppt/slideMasters/"):
            overrides["/" + name] = "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"
        elif name.endswith(".xml") and name.startswith("ppt/slideLayouts/"):
            overrides["/" + name] = "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"
        elif name.endswith(".xml") and name.startswith("ppt/theme/"):
            overrides["/" + name] = "application/vnd.openxmlformats-officedocument.theme+xml"
        elif name.endswith(".xml") and name.startswith("ppt/notesMasters/"):
            overrides["/" + name] = "application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"
        elif name.endswith(".xml") and name.startswith("ppt/notesSlides/"):
            overrides["/" + name] = "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"
        elif name.endswith(".xml") and name.startswith("ppt/presProps"):
            overrides["/" + name] = "application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"
        elif name.endswith(".xml") and name.startswith("ppt/viewProps"):
            overrides["/" + name] = "application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"
        elif name.endswith(".xml") and name.startswith("ppt/tableStyles"):
            overrides["/" + name] = "application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"
    for ext in media_exts:
        defaults[ext] = ext_ct.get(ext, "image/png")
    for i in range(1, slide_count + 1):
        overrides["/ppt/slides/slide%d.xml" % i] = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
    # presentation.xml override must exist
    overrides["/ppt/presentation.xml"] = ct_map["/ppt/presentation.xml"]
    parts = []
    for ext in sorted(defaults):
        parts.append('<Default Extension="%s" ContentType="%s"/>' % (esc(ext), defaults[ext]))
    for pn in sorted(overrides):
        parts.append('<Override PartName="%s" ContentType="%s"/>' % (esc(pn), overrides[pn]))
    return (XML_DECL + '<Types xmlns="%s">%s</Types>') % (NS["ct"], "".join(parts))


def build_presentation_parts(draft: Dict[str, Any], template_path: Optional[Path],
                             input_root: Path) -> Tuple[Dict[str, bytes], bool, List[str]]:
    """Build the final OOXML part dict.

    Returns (parts, inherited_template, notes).  Deterministic: given the same
    draft + template bytes the returned dict is identical.
    """
    pages = draft.get("pages") or []
    title = str(draft.get("title") or "Presentation")
    notes: List[str] = []
    lang = "zh-CN" if has_cjk(title) or any(
        has_cjk(str(p.get("claim") or "")) or has_cjk(str(p.get("title") or ""))
        for p in pages) else "en-US"

    media_exts: List[str] = []
    figure_bufs: List[bytes] = []

    # Gather figure bytes from input_root (paths in draft are relative to /input).
    for page in pages:
        for fig in (page.get("figures") or []):
            p = fig.get("path")
            if not p:
                continue
            fp = (input_root / str(p)).resolve()
            try:
                if fp.is_file() and str(fp).startswith(str(input_root.resolve())):
                    data = fp.read_bytes()
                    if data:
                        figure_bufs.append(data)
                        media_exts.append(fp.suffix.lstrip(".").lower() or "png")
                        continue
            except OSError:
                pass
            notes.append("figure not embeddable or out of input root: %s" % p)

    if template_path is not None and template_path.is_file():
        tpl = read_zip(template_path)
        if not tpl:
            notes.append("template could not be read: %s" % template_path)
            tpl = None
    else:
        tpl = None

    if tpl is None:
        return _build_minimal_parts(pages, title, lang, media_exts, figure_bufs), False, notes

    # ---- discover layouts / masters ----
    layouts: Dict[str, List[Dict[str, Any]]] = {}
    for name in sorted(tpl):
        if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", name):
            lname, phs = parse_layout(tpl[name])
            layouts[name] = phs
    if not layouts:
        notes.append("template has no usable slideLayouts; falling back to minimal deck")
        return _build_minimal_parts(pages, title, lang, media_exts, figure_bufs), False, notes

    # ---- keep template parts except slides ----
    kept = {k: v for k, v in tpl.items()
            if not re.fullmatch(r"ppt/slides/slide\d+\.xml", k)
            and not re.fullmatch(r"ppt/slides/_rels/slide\d+\.xml\.rels", k)
            and not k.startswith("ppt/notesSlides/")}

    # ---- presentation.xml.rels: keep non-slide relationships ----
    rels_raw = tpl.get("ppt/_rels/presentation.xml.rels")
    rels = _parse_relationships(rels_raw) if rels_raw else []
    kept_rels = [r for r in rels if not (r.get("target") or "").startswith("slides/")
                 and not (r.get("target") or "").startswith("notesSlides/")]
    used_ids = {r["id"] for r in kept_rels}
    master_ids = [r["id"] for r in kept_rels if (r.get("type") or "").endswith("/slideMaster")]

    # slide relationship ids: avoid collisions with any existing id
    base = 100
    slide_rel_ids: List[str] = []
    for i in range(1, len(pages) + 1):
        rid = "rIdS%d" % (base + i - 1)
        while rid in used_ids:
            base += 1000
            rid = "rIdS%d" % (base + i - 1)
        slide_rel_ids.append(rid)
        used_ids.add(rid)

    # ---- sldSz ----
    sld_sz = '<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
    if tpl.get("ppt/presentation.xml"):
        m = re.search(rb'<p:sldSz[^>]*/>', tpl["ppt/presentation.xml"])
        if m:
            sld_sz = m.group(0).decode("utf-8", "replace")

    # ---- media parts ----
    media_parts: Dict[str, bytes] = {}
    media_by_slide: Dict[int, List[str]] = {}
    k = 1
    for si, page in enumerate(pages, 1):
        for fig in (page.get("figures") or []):
            p = fig.get("path")
            if not p:
                continue
            fp = (input_root / str(p)).resolve()
            try:
                if fp.is_file() and str(fp).startswith(str(input_root.resolve())):
                    data = fp.read_bytes()
                    if data:
                        ext = fp.suffix.lstrip(".").lower() or "png"
                        media_name = "ppt/media/image%d.%s" % (k, ext)
                        media_parts[media_name] = data
                        media_by_slide.setdefault(si, []).append(media_name)
                        media_exts.append(ext)
                        k += 1
            except OSError:
                pass

    # ---- slides ----
    slide_parts: Dict[str, bytes] = {}
    slide_rels_parts: Dict[str, bytes] = {}
    for i, page in enumerate(pages, 1):
        lfname = choose_layout(layouts, page.get("role") or "other",
                               page.get("layout"))
        phs = layouts.get(lfname) or []
        rel_entries = ['<Relationship Id="rId1" Type="%s" Target="../slideLayouts/%s"/>'
                       % (REL_LAYOUT, lfname.rsplit("/", 1)[-1])]
        img_ids: List[str] = []
        for j, mname in enumerate(media_by_slide.get(i, []), 1):
            rid = "rId%d" % (j + 1)
            rel_entries.append('<Relationship Id="%s" Type="%s" Target="../media/%s"/>'
                               % (rid, REL_IMAGE, mname.rsplit("/", 1)[-1]))
            img_ids.append(rid)
        xml = build_slide_xml(page, phs, img_ids, lang, i - 1)
        slide_parts["ppt/slides/slide%d.xml" % i] = xml.encode("utf-8")
        slide_rels_parts["ppt/slides/_rels/slide%d.xml.rels" % i] = (
            (XML_DECL + '<Relationships xmlns="%s">%s</Relationships>')
            % (NS["rel"], "".join(rel_entries))
        ).encode("utf-8")

    # ---- presentation.xml ----
    master_lst = '<p:sldMasterIdLst>%s</p:sldMasterIdLst>' % "".join(
        '<p:sldMasterId id="%d" r:id="%s"/>' % (2147483648 + n, rid)
        for n, rid in enumerate(master_ids))
    slide_lst = '<p:sldIdLst>%s</p:sldIdLst>' % "".join(
        '<p:sldId id="%d" r:id="%s"/>' % (255 + i, rid)
        for i, rid in enumerate(slide_rel_ids, 1))
    pres_xml = (
        XML_DECL +
        '<p:presentation xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
        '%s%s%s<p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    ) % (NS["a"], NS["r"], NS["p"], master_lst, slide_lst, sld_sz)
    kept["ppt/presentation.xml"] = pres_xml.encode("utf-8")

    # ---- presentation.xml.rels ----
    rel_lines = ['<Relationship Id="%s" Type="%s" Target="%s"%s/>'
                 % (r["id"], r["type"], r["target"],
                    ' TargetMode="External"' if r.get("mode") == "External" else "")
                 for r in kept_rels]
    for i, rid in enumerate(slide_rel_ids, 1):
        rel_lines.append('<Relationship Id="%s" Type="%s" Target="slides/slide%d.xml"/>'
                         % (rid, REL_SLIDE, i))
    kept["ppt/_rels/presentation.xml.rels"] = (
        (XML_DECL + '<Relationships xmlns="%s">%s</Relationships>')
        % (NS["rel"], "".join(rel_lines))
    ).encode("utf-8")

    # ---- merge slides + media ----
    kept.update(slide_parts)
    kept.update(slide_rels_parts)
    kept.update(media_parts)

    # ---- content types ----
    kept["[Content_Types].xml"] = _build_content_types_inherited(
        kept, len(pages), media_exts).encode("utf-8")

    # ---- docProps: rewrite app.xml slide count, keep core.xml ----
    if "docProps/app.xml" in kept:
        app = kept["docProps/app.xml"].decode("utf-8", "replace")
        app = re.sub(r"<Slides>\d+</Slides>", "<Slides>%d</Slides>" % len(pages), app)
        kept["docProps/app.xml"] = app.encode("utf-8")

    return kept, True, notes


def _build_minimal_parts(pages: List[Dict[str, Any]], title: str, lang: str,
                         media_exts: List[str], figure_bufs: List[bytes]) -> Dict[str, bytes]:
    n = len(pages)
    layouts = {
        "ppt/slideLayouts/slideLayout1.xml": parse_layout(
            minimal_slide_layout1().encode("utf-8"))[1],
        "ppt/slideLayouts/slideLayout2.xml": parse_layout(
            minimal_slide_layout2().encode("utf-8"))[1],
    }
    parts: Dict[str, bytes] = {
        "[Content_Types].xml": minimal_content_types(n, media_exts).encode("utf-8"),
        "_rels/.rels": minimal_root_rels().encode("utf-8"),
        "ppt/presentation.xml": minimal_presentation_xml(n).encode("utf-8"),
        "ppt/_rels/presentation.xml.rels": minimal_presentation_rels(n).encode("utf-8"),
        "ppt/slideMasters/slideMaster1.xml": minimal_slide_master().encode("utf-8"),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": minimal_slide_master_rels().encode("utf-8"),
        "ppt/slideLayouts/slideLayout1.xml": minimal_slide_layout1().encode("utf-8"),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": minimal_layout_rels("").encode("utf-8"),
        "ppt/slideLayouts/slideLayout2.xml": minimal_slide_layout2().encode("utf-8"),
        "ppt/slideLayouts/_rels/slideLayout2.xml.rels": minimal_layout_rels("").encode("utf-8"),
        "ppt/theme/theme1.xml": minimal_theme().encode("utf-8"),
        "docProps/core.xml": minimal_core_props(title).encode("utf-8"),
        "docProps/app.xml": minimal_app_props(n).encode("utf-8"),
    }
    media = {}
    k = 1
    for i, page in enumerate(pages, 1):
        phs = layouts.get("ppt/slideLayouts/slideLayout2.xml") or []
        if (page.get("role") or "other") == "title":
            phs = layouts.get("ppt/slideLayouts/slideLayout1.xml") or []
        lfname = "slideLayout1.xml" if (page.get("role") or "other") == "title" else "slideLayout2.xml"
        img_ids: List[str] = []
        rel_entries = ['<Relationship Id="rId1" Type="%s" Target="../slideLayouts/%s"/>'
                       % (REL_LAYOUT, lfname)]
        for fig in (page.get("figures") or []):
            p = fig.get("path")
            if not p or k - 1 >= len(figure_bufs):
                continue
            ext = (Path(str(p)).suffix.lstrip(".").lower() or "png")
            media_name = "ppt/media/image%d.%s" % (k, ext)
            media[media_name] = figure_bufs[k - 1]
            rid = "rId%d" % (len(rel_entries) + 1)
            rel_entries.append('<Relationship Id="%s" Type="%s" Target="../media/%s"/>'
                               % (rid, REL_IMAGE, media_name.rsplit("/", 1)[-1]))
            img_ids.append(rid)
            k += 1
        xml = build_slide_xml(page, phs, img_ids, lang, i - 1)
        parts["ppt/slides/slide%d.xml" % i] = xml.encode("utf-8")
        parts["ppt/slides/_rels/slide%d.xml.rels" % i] = (
            (XML_DECL + '<Relationships xmlns="%s">%s</Relationships>')
            % (NS["rel"], "".join(rel_entries))
        ).encode("utf-8")
    parts.update(media)
    return parts


# ---------------------------------------------------------------------------
# Input discovery and fingerprint
# ---------------------------------------------------------------------------
def discover_materials(input_root: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not input_root.is_dir():
        return out
    for path in sorted(input_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_root).as_posix()
        if rel == "case.json":
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        ext = path.suffix.lstrip(".").lower()
        kind = "other"
        if ext in ("pdf",):
            kind = "pdf"
        elif ext in ("md", "txt", "tex"):
            kind = "text"
        elif ext in ("pptx", "ppt", "key"):
            kind = "template"
        elif ext in ("png", "jpg", "jpeg", "gif", "svg", "tif", "tiff", "bmp", "webp"):
            kind = "image"
        elif ext in ("json", "csv", "xlsx", "xls"):
            kind = "data"
        out.append({
            "path": rel, "kind": kind, "size": size,
            "sha256": _file_sha256(path),
            "skipped": size > MAX_TEXT_FILE and kind in ("text", "pdf"),
        })
    return out


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def input_fingerprint(input_root: Path) -> str:
    h = hashlib.sha256()
    if not input_root.is_dir():
        return h.hexdigest()
    for path in sorted(input_root.rglob("*")):
        if not path.is_file():
            continue
        h.update(path.relative_to(input_root).as_posix().encode("utf-8"))
        h.update(b"\x00")
        try:
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\x00")
    return h.hexdigest()
