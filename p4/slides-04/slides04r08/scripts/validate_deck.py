#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_deck.py — 校验可编辑 pptx 及其 PDF/PNG 导出件（仅标准库）。

不做真实渲染，只做确定性文件结构检查：
  * pptx：合法 zip、ppt/presentation.xml 存在、页数、母版/版式文件、
    幻灯片→版式、版式→母式的 relationship 继承、备注页、媒体文件与尺寸、
    画布尺寸、文本框边界越界启发式、最小字号（防"压字号硬塞"）、字体集合；
  * pdf：%PDF- 头与近似页数；
  * png：签名与 IHDR 宽高。

结果合并进 result.json 的 render_record；integrity 显式列出
"可自动测（auto_verifiable）"与"只能人工核（human_required）"，
作为方法边界证据。

退出码：
  0 全部检查通过
  1 存在告警/失败项（仍写 render_record）
  2 参数错误，或主 pptx 不存在/不是合法 zip
"""

import argparse
import json
import re
import struct
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def localname(tag):
    return tag.rsplit("}", 1)[-1]


def read_zip_text(zf, name):
    try:
        return zf.read(name).decode("utf-8", errors="replace")
    except KeyError:
        return None


def iter_rels(zf, rels_name):
    text = read_zip_text(zf, rels_name)
    if not text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for rel in root.iter():
        if localname(rel.tag) == "Relationship":
            out.append({
                "Id": rel.get("Id"),
                "Type": rel.get("Type", ""),
                "Target": rel.get("Target", ""),
            })
    return out


def scan_slides(zf, slides, slide_size_emu):
    """扫描每页：越界形状、字体集合、最小字号。"""
    overflow = []
    fonts = set()
    min_font = None
    cxmax = (slide_size_emu or {}).get("cx")
    cymax = (slide_size_emu or {}).get("cy")
    for s in slides:
        m = re.search(r"slide(\d+)\.xml$", s)
        num = m.group(1) if m else "?"
        text = read_zip_text(zf, s)
        if not text:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        for el in root.iter():
            ln = localname(el.tag)
            if ln == "rPr":
                sz = el.get("sz")
                if sz:
                    try:
                        v = int(sz)
                        if min_font is None or v < min_font:
                            min_font = v
                    except ValueError:
                        pass
                for attr in ("latin", "ea", "cs"):
                    tf = el.get(attr)
                    if tf:
                        fonts.add(tf)
            elif ln == "xfrm":
                off = None
                ext = None
                for child in el:
                    cln = localname(child.tag)
                    if cln == "off":
                        off = (int(child.get("x", 0)), int(child.get("y", 0)))
                    elif cln == "ext":
                        ext = (int(child.get("cx", 0)), int(child.get("cy", 0)))
                if off and ext and cxmax and cymax:
                    x2 = off[0] + ext[0]
                    y2 = off[1] + ext[1]
                    if x2 > cxmax or y2 > cymax or off[0] < 0 or off[1] < 0:
                        overflow.append({
                            "slide": "slide%s" % num,
                            "x": off[0], "y": off[1], "cx": ext[0], "cy": ext[1],
                            "x2": x2, "y2": y2,
                            "slide_cx": cxmax, "slide_cy": cymax,
                        })
    return overflow, sorted(fonts), min_font


def check_pptx(path):
    p = Path(path)
    rep = {
        "path": str(p),
        "exists": p.is_file(),
        "valid_zip": False,
        "slide_count": 0,
        "slides": [],
        "masters": [],
        "layouts": [],
        "slide_layout_rels": {},
        "layout_master_rels": {},
        "notes_slides": [],
        "media": [],
        "slide_size_emu": None,
        "overflow_shapes": [],
        "min_font_centipoints": None,
        "fonts_used": [],
        "errors": [],
    }
    if not p.is_file():
        rep["errors"].append("pptx not found")
        return rep
    try:
        with zipfile.ZipFile(p) as zf:
            names = set(zf.namelist())
            if "ppt/presentation.xml" not in names:
                rep["errors"].append("missing ppt/presentation.xml")
                return rep
            rep["valid_zip"] = True
            slides = sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
            rep["slides"] = slides
            rep["slide_count"] = len(slides)
            rep["masters"] = sorted(n for n in names if re.match(r"ppt/slideMasters/slideMaster\d+\.xml$", n))
            rep["layouts"] = sorted(n for n in names if re.match(r"ppt/slideLayouts/slideLayout\d+\.xml$", n))
            rep["notes_slides"] = sorted(n for n in names if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n))
            rep["media"] = [
                {"name": n, "bytes": zf.getinfo(n).file_size}
                for n in names if n.startswith("ppt/media/")
            ]
            pres_xml = read_zip_text(zf, "ppt/presentation.xml")
            if pres_xml:
                try:
                    root = ET.fromstring(pres_xml)
                    for el in root.iter():
                        if localname(el.tag) == "sldSz":
                            rep["slide_size_emu"] = {
                                "cx": int(el.get("cx", 0)),
                                "cy": int(el.get("cy", 0)),
                            }
                            break
                except ET.ParseError:
                    rep["errors"].append("presentation.xml unparsable")
            for s in slides:
                m = re.search(r"slide(\d+)\.xml$", s)
                num = m.group(1)
                rels = iter_rels(zf, "ppt/slides/_rels/slide%d.xml.rels" % int(num))
                rep["slide_layout_rels"]["slide%s" % num] = [
                    r for r in rels if r["Type"].endswith("/slideLayout")
                ]
            for lay in rep["layouts"]:
                m = re.search(r"slideLayout(\d+)\.xml$", lay)
                num = m.group(1)
                rels = iter_rels(zf, "ppt/slideLayouts/_rels/slideLayout%d.xml.rels" % int(num))
                rep["layout_master_rels"]["layout%s" % num] = [
                    r for r in rels if r["Type"].endswith("/slideMaster")
                ]
            rep["overflow_shapes"], rep["fonts_used"], rep["min_font_centipoints"] = scan_slides(
                zf, slides, rep["slide_size_emu"]
            )
    except (zipfile.BadZipFile, OSError) as e:
        rep["errors"].append("bad zip: %s" % e)
    return rep


def check_pdf(path):
    p = Path(path)
    rep = {"path": str(p), "exists": p.is_file(), "valid": False, "pages": None, "errors": []}
    if not p.is_file():
        rep["errors"].append("pdf not found")
        return rep
    try:
        with p.open("rb") as f:
            head = f.read(8)
        if not head.startswith(b"%PDF-"):
            rep["errors"].append("bad %PDF- header")
            return rep
        rep["valid"] = True
        data = p.read_bytes()
        rep["pages"] = data.count(b"/Type/Page") + data.count(b"/Type /Page")
    except OSError as e:
        rep["errors"].append(str(e))
    return rep


def check_png(path):
    p = Path(path)
    rep = {"path": str(p), "exists": p.is_file(), "valid": False, "width": None, "height": None, "errors": []}
    if not p.is_file():
        rep["errors"].append("png not found")
        return rep
    try:
        with p.open("rb") as f:
            sig = f.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                rep["errors"].append("bad PNG signature")
                return rep
            _length, ctype = struct.unpack(">I4s", f.read(8))
            if ctype != b"IHDR":
                rep["errors"].append("missing IHDR")
                return rep
            w, h, *_ = struct.unpack(">IIBBBBB", f.read(13))
            rep.update(valid=True, width=w, height=h)
    except OSError as e:
        rep["errors"].append(str(e))
    return rep


def load_json(path):
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError, OSError):
        return {}


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验 pptx 及其 PDF/PNG 导出件")
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--png", nargs="*", default=[])
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--result", default="/app/output/result.json")
    args = ap.parse_args(argv)

    pptx = check_pptx(args.pptx)
    pdf = check_pdf(args.pdf) if args.pdf else None
    pngs = [check_png(x) for x in (args.png or [])]

    slide_inherits = pptx["slide_count"] > 0 and all(
        bool(v) for v in pptx["slide_layout_rels"].values()
    )
    used_layouts = set()
    for rels in pptx["slide_layout_rels"].values():
        for r in rels:
            used_layouts.add(Path(r["Target"]).name)

    layout_inherits = True
    layout_checked = 0
    for t in used_layouts:
        m = re.search(r"slideLayout(\d+)\.xml$", t)
        if not m:
            layout_inherits = False
            break
        rels = pptx["layout_master_rels"].get("layout%s" % m.group(1), [])
        if not rels:
            layout_inherits = False
            break
        layout_checked += 1
    masters_inherited = slide_inherits and layout_inherits and layout_checked > 0

    warnings = []
    if not pptx["exists"] or not pptx["valid_zip"]:
        warnings.append("pptx missing or invalid")
    if pptx["slide_count"] == 0:
        warnings.append("no slides")
    if not masters_inherited:
        warnings.append("master/layout inheritance not fully detected")
    if pptx["slide_count"] > 0 and not pptx["notes_slides"]:
        warnings.append("no notes slides (speaker notes missing)")
    if pptx["overflow_shapes"]:
        warnings.append("%d shapes exceed slide bounds" % len(pptx["overflow_shapes"]))
    if pptx["min_font_centipoints"] is not None and pptx["min_font_centipoints"] < 800:
        warnings.append("min font size below 8pt (readability risk)")
    if pdf and not pdf["valid"]:
        warnings.append("PDF preview invalid or missing")
    if args.png and any(not x["valid"] for x in pngs):
        warnings.append("one or more PNG previews invalid")

    integrity = {
        "pptx_valid_zip": pptx["valid_zip"],
        "slide_count": pptx["slide_count"],
        "masters_inherited": masters_inherited,
        "notes_present": bool(pptx["notes_slides"]),
        "overflow_count": len(pptx["overflow_shapes"]),
        "min_font_pt": (pptx["min_font_centipoints"] / 100.0) if pptx["min_font_centipoints"] is not None else None,
        "pdf_valid": pdf["valid"] if pdf else None,
        "pdf_pages": pdf["pages"] if pdf else None,
        "png_count_valid": sum(1 for x in pngs if x["valid"]),
        "auto_verifiable": [
            "pptx zip validity", "slide count", "master/layout relationship inheritance",
            "notes slide presence", "media presence & size", "slide canvas size",
            "shape bounding-box overflow (geometry only)", "minimum font size",
            "font set used", "PDF %PDF- header & approx page count", "PNG signature & dimensions",
        ],
        "human_required": [
            "visual text overflow / clipping after render",
            "color contrast & print readability",
            "Chinese/English font pairing aesthetics",
            "image aspect-ratio distortion (needs rendered side-by-side)",
            "figure/table number vs in-text citation correctness",
            "speaker-note content correctness",
        ],
    }

    render_record = {
        "checked_at": now_iso(),
        "template_file": pptx["path"],
        "layouts_used": sorted(used_layouts),
        "fonts": pptx["fonts_used"],
        "exports": [],
        "integrity": integrity,
        "media": pptx["media"],
        "overflow_shapes": pptx["overflow_shapes"],
        "min_font_centipoints": pptx["min_font_centipoints"],
        "warnings": warnings,
        "pptx_checks": {
            "exists": pptx["exists"],
            "valid_zip": pptx["valid_zip"],
            "slide_count": pptx["slide_count"],
            "masters": pptx["masters"],
            "layouts": pptx["layouts"],
            "notes_slides": pptx["notes_slides"],
            "slide_size_emu": pptx["slide_size_emu"],
            "errors": pptx["errors"],
        },
        "pdf_check": pdf,
        "png_checks": pngs,
    }
    if pdf:
        render_record["exports"].append({
            "kind": "pdf", "path": pdf["path"], "valid": pdf["valid"], "pages": pdf["pages"],
        })
    for x in pngs:
        render_record["exports"].append({
            "kind": "png", "path": x["path"], "valid": x["valid"],
            "width": x["width"], "height": x["height"],
        })

    existing = load_json(args.result)
    if not isinstance(existing, dict):
        existing = {}
    rr = existing.get("render_record")
    if not isinstance(rr, dict):
        rr = {}
    rr.update(render_record)
    existing["render_record"] = rr
    if not isinstance(existing.get("change_log"), list):
        existing["change_log"] = []
    existing["change_log"].append({
        "ts": now_iso(),
        "event": "validate_deck",
        "detail": "deck validation; %d warnings" % len(warnings),
    })
    save_json(args.result, existing)
    save_json(Path(args.output) / "render_record.json", render_record)

    print(json.dumps({"warnings": warnings, "integrity": integrity, "result": args.result},
                     ensure_ascii=False, indent=2))

    if not pptx["exists"] or not pptx["valid_zip"]:
        return 2
    if warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
