#!/usr/bin/env python3
"""Standard-library-only OOXML (.pptx) writer + PDF helpers.

Purpose: build a real, editable slide deck deterministically from a JSON page
plan (draft.json), including speaker notes, without python-pptx or any
third-party dependency. Also provides PDF page counting and best-effort
LibreOffice conversion for the rendered preview.

The writer only lays out text the agent has already written. It does not read
or summarise the paper: content is the agent's responsibility.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

EMU_PER_INCH = 914400
SLIDE_W = 12192000   # 16:9, 13.333in
SLIDE_H = 6858000    # 7.5in

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _esc(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


# --------------------------------------------------------------------------- #
# XML parts
# --------------------------------------------------------------------------- #

def _content_types(n_slides: int) -> bytes:
    overrides = [
        f'<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        f'<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        f'<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        f'<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
        f'<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        f'<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for i in range(1, n_slides + 1):
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
        overrides.append(
            f'<Override PartName="/ppt/notesSlides/notesSlide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
        )
    body = "".join(overrides)
    return (
        _XML_DECL
        + '<Types xmlns="' + CT_NS + '">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + body
        + "</Types>"
    ).encode("utf-8")


def _root_rels() -> bytes:
    return (
        _XML_DECL
        + '<Relationships xmlns="' + REL_NS + '">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        + '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        + "</Relationships>"
    ).encode("utf-8")


def _presentation(n_slides: int) -> bytes:
    slides = "".join(
        f'<p:sldId id="{255 + i}" r:id="rId{1 + i}"/>' for i in range(1, n_slides + 1)
    )
    return (
        _XML_DECL
        + f'<p:presentation xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
        + '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        + f'<p:sldIdLst>{slides}</p:sldIdLst>'
        + f'<p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}"/>'
        + '<p:notesSz cx="6858000" cy="9144000"/>'
        + "</p:presentation>"
    ).encode("utf-8")


def _presentation_rels(n_slides: int) -> bytes:
    rels = ['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>']
    for i in range(1, n_slides + 1):
        rels.append(f'<Relationship Id="rId{1 + i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>')
    base = n_slides + 1
    for i in range(1, n_slides + 1):
        rels.append(f'<Relationship Id="rId{base + i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="notesSlides/notesSlide{i}.xml"/>')
    return (
        _XML_DECL + '<Relationships xmlns="' + REL_NS + '">' + "".join(rels) + "</Relationships>"
    ).encode("utf-8")


def _theme() -> bytes:
    accents = ["1F4E79", "2E74B5", "6C8EBF", "9DC3E6", "C55A11", "548235"]
    clr = (
        '<a:clrScheme name="Skill">'
        '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
        '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
        '<a:dk2><a:srgbClr val="1F4E79"/></a:dk2>'
        '<a:lt2><a:srgbClr val="EEECE1"/></a:lt2>'
        + "".join(f'<a:accent{i+1}><a:srgbClr val="{c}"/></a:accent{i+1}>' for i, c in enumerate(accents))
        + '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
        + '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
        + "</a:clrScheme>"
    )
    font = (
        '<a:fontScheme name="Skill">'
        '<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
        "</a:fontScheme>"
    )
    fmt = (
        '<a:fmtScheme name="Skill">'
        '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
        '<a:lnStyleLst><a:ln w="6350" cap="flat" cmpd="sng" algn="ctr">'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:prstDash val="solid"/><a:miter lim="800000"/></a:ln></a:lnStyleLst>'
        '<a:effectStyleLst><a:effectStyle><a:effectLst/>'
        '<a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>'
        '<a:sp3d><a:bevelT w="25400" h="25400"/></a:sp3d></a:effectStyle></a:effectStyleLst>'
        '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
        "</a:fmtScheme>"
    )
    return (
        _XML_DECL
        + f'<a:theme xmlns:a="{A_NS}" name="Skill Theme">'
        + "<a:themeElements>"
        + clr + font + fmt
        + "</a:themeElements>"
        + "<a:objectDefaults/>"
        + "<a:extraClrSchemeLst/>"
        + "</a:theme>"
    ).encode("utf-8")


def _master() -> bytes:
    clr_map = (
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" '
        'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" '
        'accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    )
    return (
        _XML_DECL
        + f'<p:sldMaster xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
        + '<p:cSld><p:spTree>'
        + '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        + '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        + '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "</p:spTree></p:cSld>"
        + clr_map
        + '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        + "</p:sldMaster>"
    ).encode("utf-8")


def _master_rels() -> bytes:
    return (
        _XML_DECL + '<Relationships xmlns="' + REL_NS + '">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
        + "</Relationships>"
    ).encode("utf-8")


def _layout() -> bytes:
    return (
        _XML_DECL
        + f'<p:sldLayout xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}" type="blank" preserve="1">'
        + '<p:cSld name="Blank"><p:spTree>'
        + '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        + '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        + '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "</p:spTree></p:cSld>"
        + "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
        + "</p:sldLayout>"
    ).encode("utf-8")


def _layout_rels() -> bytes:
    return (
        _XML_DECL + '<Relationships xmlns="' + REL_NS + '">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
        + "</Relationships>"
    ).encode("utf-8")


def _run(text: str, size: int = 1800, bold: bool = False, color: str | None = None) -> str:
    attrs = [f'lang="zh-CN"', f'sz="{size}"']
    if bold:
        attrs.append('b="1"')
    rpr = " ".join(attrs)
    fill = f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>' if color else ""
    return f'<a:r><a:rPr {rpr}>{fill}</a:rPr><a:t>{_esc(text)}</a:t></a:r>'


def _title_shape(title: str) -> str:
    return (
        '<p:sp><p:nvSpPr>'
        '<p:cNvPr id="2" name="Title 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="457200" y="228600"/>'
        f'<a:ext cx="{SLIDE_W - 2 * 457200}" cy="1000000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square" rtlCol="0" anchor="ctr"/>'
        "<a:lstStyle/><a:p><a:pPr algn=\"l\"/>"
        + _run(title, size=2800, bold=True, color="1F4E79")
        + "</a:p></p:txBody></p:sp>"
    )


def _body_shape(items: list, y: int, height: int) -> str:
    paras = []
    for it in items:
        text = it.get("text", "")
        level = int(it.get("level", 0))
        size = int(it.get("size", 1800))
        bold = bool(it.get("bold", False))
        mar_l = 342900 + level * 457200
        if level == 0:
            ppr = (
                f'<a:pPr marL="{mar_l}" indent="-342900" algn="l">'
                '<a:buFont typeface="Arial" pitchFamily="34" charset="0"/>'
                '<a:buChar char="•"/></a:pPr>'
            )
        else:
            ppr = (
                f'<a:pPr marL="{mar_l}" indent="-228600" algn="l">'
                '<a:buFont typeface="Arial" pitchFamily="34" charset="0"/>'
                '<a:buChar char="–"/></a:pPr>'
            )
        paras.append("<a:p>" + ppr + _run(text, size=size, bold=bold) + "</a:p>")
    body = '<a:bodyPr wrap="square" rtlCol="0"><a:normAutofit/></a:bodyPr>'
    return (
        '<p:sp><p:nvSpPr>'
        '<p:cNvPr id="3" name="Content 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="685800" y="' + str(y) + '"/>'
        '<a:ext cx="' + str(SLIDE_W - 2 * 685800) + '" cy="' + str(height) + '"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        "<p:txBody>" + body + "<a:lstStyle/>" + "".join(paras) + "</p:txBody></p:sp>"
    )


def _footer_shape(text: str) -> str:
    if not text:
        return ""
    return (
        '<p:sp><p:nvSpPr>'
        '<p:cNvPr id="4" name="Footer 3"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="457200" y="6400800"/>'
        f'<a:ext cx="{SLIDE_W - 2 * 457200}" cy="360000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square" rtlCol="0"/>'
        "<a:lstStyle/><a:p><a:pPr algn=\"l\"/>"
        + _run(text, size=1000, color="808080")
        + "</a:p></p:txBody></p:sp>"
    )


def _slide(slide: dict) -> bytes:
    title = str(slide.get("title", "") or "")
    items = slide.get("body") or []
    if isinstance(items, str):
        items = [{"text": items}]
    footer = str(slide.get("footer", "") or "")
    y = 1371600
    height = 5029200 if footer else 5200000
    tree = (
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    )
    return (
        _XML_DECL
        + f'<p:sld xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
        + '<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>'
        + "<a:effectLst/></p:bgPr></p:bg><p:spTree>" + tree
        + _title_shape(title)
        + _body_shape(items, y, height)
        + _footer_shape(footer)
        + "</p:spTree></p:cSld>"
        + "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
        + "</p:sld>"
    ).encode("utf-8")


def _slide_rels() -> bytes:
    return (
        _XML_DECL + '<Relationships xmlns="' + REL_NS + '">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        + "</Relationships>"
    ).encode("utf-8")


def _notes(n: int, text: str) -> bytes:
    return (
        _XML_DECL
        + f'<p:notes xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
        + '<p:cSld><p:spTree>'
        + '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        + '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        + '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Notes Placeholder 1"/>'
        + '<p:cNvSpPr/><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
        + "<p:spPr/>"
        + '<p:txBody><a:bodyPr/><a:lstStyle/><a:p>'
        + _run(str(text or ""), size=1200)
        + "</a:p></p:txBody></p:sp>"
        + "</p:spTree></p:cSld>"
        + "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
        + "</p:notes>"
    ).encode("utf-8")


def _notes_rels(n: int) -> bytes:
    return (
        _XML_DECL + '<Relationships xmlns="' + REL_NS + '">'
        + f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="../slides/slide{n}.xml"/>'
        + "</Relationships>"
    ).encode("utf-8")


def _core(title: str, author: str, created: str) -> bytes:
    return (
        _XML_DECL
        + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        + 'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        + 'xmlns:dcterms="http://purl.org/dc/terms/" '
        + 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        + f"<dc:title>{_esc(title)}</dc:title>"
        + f"<dc:creator>{_esc(author)}</dc:creator>"
        + f'<dcterms:created xsi:type="dcterms:W3CDTF">{_esc(created)}</dcterms:created>'
        + f'<cp:lastModifiedBy>{_esc(author)}</cp:lastModifiedBy>'
        + "</cp:coreProperties>"
    ).encode("utf-8")


def _app(n_slides: int) -> bytes:
    return (
        _XML_DECL
        + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        + 'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        + "<Application>Skill pptxlib</Application>"
        + f"<Slides>{n_slides}</Slides>"
        + "</Properties>"
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def normalize_slide(slide: dict) -> dict:
    """Accept several input shapes and return the canonical {title, body, notes, footer}."""
    out = {
        "title": str(slide.get("title", "") or ""),
        "notes": str(slide.get("notes", "") or slide.get("speaker_notes", "") or ""),
        "footer": str(slide.get("footer", "") or ""),
    }
    body = slide.get("body") or slide.get("bullet_points") or []
    if isinstance(body, str):
        body = [body]
    norm = []
    for it in body:
        if isinstance(it, str):
            norm.append({"text": it, "bold": False, "level": 0, "size": 1800})
        else:
            norm.append({
                "text": str(it.get("text", "") or ""),
                "bold": bool(it.get("bold", False)),
                "level": int(it.get("level", 0) or 0),
                "size": int(it.get("size", 1800) or 1800),
            })
    out["body"] = norm
    return out


def build_pptx(slides, out_path, title="Academic presentation", author="slides-skill",
               created=None):
    """Build a valid .pptx from a list of slide dicts. Returns (path, n_slides)."""
    from datetime import datetime, timezone

    created = created or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    norm = [normalize_slide(s) for s in slides]
    n = len(norm)
    if n == 0:
        norm = [{"title": title or "Presentation", "body": [], "notes": "", "footer": ""}]
        n = 1
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types(n))
        z.writestr("_rels/.rels", _root_rels())
        z.writestr("docProps/core.xml", _core(title, author, created))
        z.writestr("docProps/app.xml", _app(n))
        z.writestr("ppt/presentation.xml", _presentation(n))
        z.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels(n))
        z.writestr("ppt/theme/theme1.xml", _theme())
        z.writestr("ppt/slideMasters/slideMaster1.xml", _master())
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _master_rels())
        z.writestr("ppt/slideLayouts/slideLayout1.xml", _layout())
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _layout_rels())
        for i, s in enumerate(norm, start=1):
            z.writestr(f"ppt/slides/slide{i}.xml", _slide(s))
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", _slide_rels())
            z.writestr(f"ppt/notesSlides/notesSlide{i}.xml", _notes(i, s["notes"]))
            z.writestr(f"ppt/notesSlides/_rels/notesSlide{i}.xml.rels", _notes_rels(i))
    return str(out), n


# --------------------------------------------------------------------------- #
# PDF helpers
# --------------------------------------------------------------------------- #

def pdf_page_count(path) -> int | None:
    """Count /Type /Page occurrences. Returns None when not a valid PDF header."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if not data.startswith(b"%PDF"):
        return None
    return len(re.findall(rb"/Type\s*/Page\b", data))


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for cand in (
        "/usr/bin/soffice", "/usr/bin/libreoffice",
        "/opt/libreoffice/program/soffice",
    ):
        if Path(cand).is_file():
            return cand
    return None


def convert_to_pdf(pptx_path, out_dir) -> tuple[str | None, str | None]:
    """Best-effort LibreOffice headless conversion. Returns (pdf_path, error)."""
    soffice = find_soffice()
    if not soffice:
        return None, "soffice/libreoffice not found on PATH"
    try:
        proc = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", str(out_dir), str(pptx_path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    pdf = Path(out_dir) / (Path(pptx_path).stem + ".pdf")
    if pdf.is_file() and pdf.read_bytes().startswith(b"%PDF"):
        return str(pdf), None
    return None, (proc.stdout or "") + (proc.stderr or "")


def render_svg(slides, out_dir) -> list[str]:
    """Best-effort text preview when no PDF renderer is available. Writes one
    editable SVG per slide (text only; images are not embedded)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, s in enumerate(slides, start=1):
        n = normalize_slide(s)
        rows = [f'<rect width="1280" height="720" fill="#FFFFFF"/>']
        rows.append(f'<text x="60" y="84" font-size="42" font-weight="bold" '
                    f'fill="#1F4E79" font-family="sans-serif">{_esc(n["title"])}</text>')
        y = 160
        for b in n["body"]:
            size_px = max(18, int(round(int(b["size"]) * 4 / 3 / 100)))
            weight = "bold" if b["bold"] else "normal"
            rows.append(f'<text x="90" y="{y}" font-size="{size_px}" '
                        f'font-weight="{weight}" fill="#000000" '
                        f'font-family="sans-serif">• {_esc(b["text"])}</text>')
            y += size_px + 16
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
            'viewBox="0 0 1280 720">\n' + "\n".join(rows) + "\n</svg>\n"
        )
        p = out_dir / f"slide_{i:02d}.svg"
        p.write_text(svg, encoding="utf-8")
        paths.append(p.name)
    return paths


def validate_pptx(path) -> tuple[bool, str]:
    """Structural sanity check: zip readable and every XML part well-formed."""
    from xml.etree import ElementTree as ET

    path = Path(path)
    if not path.is_file():
        return False, f"{path.name} missing"
    try:
        with ZipFile(path) as z:
            names = z.namelist()
            if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                return False, "missing mandatory parts"
            for name in names:
                if name.endswith((".xml", ".rels")):
                    try:
                        ET.fromstring(z.read(name))
                    except ET.ParseError as exc:
                        return False, f"malformed XML in {name}: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"
    return True, "ok"
