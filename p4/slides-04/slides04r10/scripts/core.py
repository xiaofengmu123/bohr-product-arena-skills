#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.py — 学术幻灯片 Skill 的核心引擎（纯标准库）。

架构定位：这是一个「编译器式」流水线。Deck IR 是唯一的中间表示（single source of truth），
pptx、PDF/PNG 预览、evidence_map 与 report 全部从同一份 IR 派生，保证页级一致。

流水线：normalize(IR) -> lint(IR) -> bind(IR, 模板) -> emit(pptx/pdf/png) -> audit(再解析产物回核)。
校验顺序与参考实现不同：先 lint 后编译，再「回读产物」做 round-trip 核对，而不是只信输入稿。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

# ------------------------------------------------------------------ 常量

NS_P = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

DEFAULT_W, DEFAULT_H = 12192000, 6858000  # 16:9 EMU

ROLES = {"title", "section", "background", "method", "result",
         "discussion", "limitation", "conclusion", "other"}
REQUIRED_ROLES = ("title", "background", "method", "result", "conclusion")
STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

SLIDE_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")
SLIDE_RELS_RE = re.compile(r"^ppt/slides/_rels/slide\d+\.xml\.rels$")
LAYOUT_RE = re.compile(r"^ppt/slideLayouts/slideLayout\d+\.xml$")
MASTER_RE = re.compile(r"^ppt/slideMasters/slideMaster\d+\.xml$")
THEME_RE = re.compile(r"^ppt/theme/theme\d+\.xml$")

# 各页面角色 → 首选版式类型（依序降级），type 取自模板 <p:sldLayout type="...">
ROLE_LAYOUT_PREF = {
    "title":      ("title", "titleOnly", None),
    "section":    ("sectionHeader", "title", None),
    "background": ("titleAndContent", "titleOnly", "twoContent", None),
    "method":     ("titleAndContent", "twoContent", "obj", None),
    "result":     ("titleAndContent", "obj", "twoContent", None),
    "discussion": ("titleAndContent", "twoContent", None),
    "limitation": ("titleAndContent", "twoContent", None),
    "conclusion": ("titleAndContent", "sectionHeader", None),
    "other":      ("titleAndContent", None),
}


# ------------------------------------------------------------------ 基础工具

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def xml_escape(text) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def read_zip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def write_zip(parts: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(parts):  # 排序保证重复运行字节级一致
            payload = parts[name] if isinstance(parts[name], bytes) else parts[name].encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return buffer.getvalue()


# ------------------------------------------------------------------ 证据引擎

def normalize_text(text) -> str:
    """去掉空白与大小写差异，用于逐字命中；保留标点，避免把转述当直引。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


def build_corpus(texts: dict) -> dict:
    """texts: {source_id -> 原文}。返回 {source_id -> 归一化文本}。"""
    return {sid: normalize_text(text) for sid, text in (texts or {}).items()}


def quote_status(quote, corpus: dict) -> str:
    """verbatim=逐字命中；paraphrase=有词但未逐字命中；missing=无引文。"""
    needle = normalize_text(quote)
    if not needle:
        return "missing"
    if any(needle in body for body in (corpus or {}).values()):
        return "verbatim"
    return "paraphrase"


# ------------------------------------------------------------------ Deck IR

def ir_skeleton(case_id: str = "unknown-case") -> dict:
    return {
        "case_id": case_id,
        "mode": "adapt",
        "plan": {"purpose": "", "audience": "", "duration_min": 10, "narrative": ""},
        "template": {"path": ""},
        "pages": [
            {"n": 1, "role": "title", "title": "标题页", "claim": "一句话主张",
             "bullets": ["要点"], "evidence": [{"source_id": "S1", "locator": "para.1", "quote": "逐字原文"}],
             "assets": [], "notes": ""}
        ],
        "uncertainties": [],
        "pending_human": [],
    }


def normalize_ir(ir: dict, corpus: dict) -> tuple:
    """把 agent 手写的工作稿归一化为可编译的 IR；顺带给证据打分 quote_status。"""
    warnings = []
    if not isinstance(ir, dict):
        return [], ["deck 不是 JSON 对象"]
    pages = ir.get("pages") if isinstance(ir.get("pages"), list) else []
    out = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        role = page.get("role") if page.get("role") in ROLES else "other"
        evidence = []
        for ev in (page.get("evidence") or []):
            if not isinstance(ev, dict):
                continue
            sid = str(ev.get("source_id") or "")
            locator = str(ev.get("locator") or "")
            quote = str(ev.get("quote") or "")
            evidence.append({
                "source_id": sid, "locator": locator, "quote": quote,
                "quote_status": quote_status(quote, corpus),
            })
        out.append({
            "n": int(page.get("n") or index + 1),
            "role": role,
            "title": str(page.get("title") or "").strip(),
            "claim": str(page.get("claim") or "").strip(),
            "bullets": [str(b) for b in (page.get("bullets") or []) if str(b).strip()],
            "evidence": evidence,
            "assets": page.get("assets") if isinstance(page.get("assets"), list) else [],
            "notes": str(page.get("notes") or "").strip(),
        })
    return out, warnings


def lint_pages(pages: list, plan: dict, mode: str, template_found: bool) -> list:
    """IR 级静态校验（先于编译）。返回 {id, level, detail} 列表。"""
    items = []

    def add(check_id, level, detail):
        items.append({"id": check_id, "level": level, "detail": detail})

    if mode not in {"create", "edit", "adapt"}:
        add("mode", "FAIL", f"mode={mode!r} 必须是 create/edit/adapt")
    if not isinstance(plan, dict):
        add("plan", "FAIL", "缺 plan 对象")
        plan = {}
    for field in ("purpose", "audience", "narrative"):
        if not plan.get(field):
            add(f"plan.{field}", "WARN", f"plan 缺 {field}")
    duration = plan.get("duration_min")
    if not isinstance(duration, (int, float)) or duration <= 0:
        add("plan.duration_min", "WARN", "缺汇报时长（分钟），无法校验页数-时长匹配")
    if len(pages) < 3:
        add("pages", "FAIL", f"页数不足（{len(pages)}），至少 3 页")
    if duration and pages:
        ratio = len(pages) / float(duration)
        if not (0.3 <= ratio <= 3.0):
            add("page_duration", "WARN", f"{len(pages)} 页 / {duration} 分钟 = {ratio:.2f} 页/分钟，超出 0.3–3.0")

    roles = {p["role"] for p in pages}
    missing = sorted(set(REQUIRED_ROLES) - roles)
    if missing:
        add("role_coverage", "WARN", f"叙事角色缺失: {missing}")

    for page in pages:
        sid = f"pages[{page['n'] - 1}]"
        if not page["title"]:
            add(f"{sid}.title", "FAIL", "缺标题")
        if not page["claim"]:
            add(f"{sid}.claim", "FAIL", "每页需要一句话主张 claim")
        if len(page["bullets"]) > 7:
            add(f"{sid}.bullets", "WARN", f"单页 {len(page['bullets'])} 条要点，疑似整段搬运")
        if not page["evidence"]:
            add(f"{sid}.evidence", "FAIL", "每页需要证据（locator+quote）")
        else:
            un_hit = [ev for ev in page["evidence"] if ev["quote_status"] != "verbatim"]
            if un_hit:
                add(f"{sid}.evidence.quote", "WARN",
                    f"{len(un_hit)}/{len(page['evidence'])} 条引文未逐字命中材料（paraphrase/missing），需人工确认")

    if mode in {"edit", "adapt"} and not template_found:
        add("template", "WARN", "adapt/edit 模式未找到输入模板 pptx，将回退内置母版")
    return items


# ------------------------------------------------------------------ 模板检查与版式选择

def inspect_template(template_bytes: bytes) -> dict:
    try:
        parts = read_zip(template_bytes)
    except (zipfile.BadZipFile, OSError):
        return {"valid": False, "layouts": [], "masters": [], "themes": [], "error": "not a zip/pptx"}
    layouts = []
    for name in sorted(parts):
        if not LAYOUT_RE.match(name):
            continue
        xml = parts[name].decode("utf-8", "replace")
        m = re.search(r'<p:sldLayout[^>]*type="([^"]+)"', xml)
        ltype = m.group(1) if m else "custom"
        phs = sorted(set(re.findall(r'<p:ph[^>]*type="([^"]+)"', xml)))
        layouts.append({"name": name, "type": ltype, "placeholders": phs})
    masters = sorted(n for n in parts if MASTER_RE.match(n))
    themes = sorted(n for n in parts if THEME_RE.match(n))
    layout_names = [l["name"] for l in layouts]
    return {
        "valid": bool(masters),
        "layouts": layouts,
        "masters": masters,
        "themes": themes,
        "master_sha256": {n: sha256(parts[n]) for n in masters},
        "layout_sha256": {n: sha256(parts[n]) for n in layout_names},
        "theme_sha256": {n: sha256(parts[n]) for n in themes},
    }


def select_layouts(pages: list, info: dict) -> tuple:
    """按页面角色选版式；无法命中首选时用最接近版式并记录替代关系（不谎称继承）。"""
    layouts = info.get("layouts") or []
    if not layouts:
        return {}, [{"n": p["n"], "role": p["role"], "wanted": "any", "used": None,
                     "reason": "template has no slideLayout"} for p in pages]
    by_type = {}
    for layout in layouts:
        by_type.setdefault(layout["type"], layout["name"])
    first = layouts[0]["name"]
    layout_map, substitutions = {}, []
    for page in pages:
        prefs = ROLE_LAYOUT_PREF.get(page["role"], ("titleAndContent", None))
        used, wanted = None, prefs[0] if prefs[0] else first
        for pref in prefs:
            if pref is None:
                continue
            if pref in by_type:
                used = by_type[pref]
                break
        if used is None:
            used = first
            substitutions.append({"n": page["n"], "role": page["role"], "wanted": wanted,
                                  "used": used, "reason": f"no layout of type {wanted}; fell back to first"})
        elif used != by_type.get(prefs[0]):
            substitutions.append({"n": page["n"], "role": page["role"], "wanted": prefs[0],
                                  "used": used, "reason": f"type {prefs[0]} unavailable"})
        layout_map[page["n"]] = used
    return layout_map, substitutions


# ------------------------------------------------------------------ 幻灯片 OOXML

def _p_para(text: str, size: int = 1800, bold: bool = False, bullet: bool = False,
            scheme: str = "tx2", major: bool = False, level: int = 0) -> str:
    """正文段落。typeface='+mn-lt'/'+mj-lt' 继承主题字体；schemeClr 继承主题色。"""
    latin = "+mj-lt" if major else "+mn-lt"
    ea = "+mj-ea" if major else "+mn-ea"
    bu = '<a:buChar char="•"/>' if bullet else '<a:buNone/>'
    return (
        f'<a:p><a:pPr lvl="{level}">{bu}'
        f'<a:defRPr sz="{size}" b="{1 if bold else 0}">'
        f'<a:solidFill><a:schemeClr val="{scheme}"/></a:solidFill>'
        f'<a:latin typeface="{latin}"/><a:ea typeface="{ea}"/></a:defRPr></a:pPr>'
        f'<a:r><a:rPr lang="en-US" sz="{size}" b="{1 if bold else 0}" dirty="0">'
        f'<a:solidFill><a:schemeClr val="{scheme}"/></a:solidFill>'
        f'<a:latin typeface="{latin}"/><a:ea typeface="{ea}"/></a:rPr>'
        f'<a:t>{xml_escape(text)}</a:t></a:r></a:p>'
    )


def _text_box(shape_id: int, name: str, x: int, y: int, cx: int, cy: int, paras: str) -> str:
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{xml_escape(name)}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        "<p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '<a:noFill/><a:ln><a:noFill/></a:ln>'
        "</p:spPr>"
        f'<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr><a:lstStyle/>{paras}</p:txBody>'
        "</p:sp>"
    )


def slide_xml(title: str, bullets: list, page_no: int, total: int, slide_w: int, slide_h: int) -> str:
    margin = int(slide_w * 0.05)
    title_paras = _p_para(title or "（未命名页）", 2800, True, False, "tx1", major=True)
    body_paras = "".join(_p_para(b, 1800, False, True, "tx2", level=0) for b in (bullets or []))
    if not bullets:
        body_paras = _p_para("（本页无要点）", 1800, False, False, "tx2")
    footer_paras = _p_para(f"{page_no} / {total}", 1000, False, False, "tx2")
    shapes = [
        _text_box(2, "Title", margin, int(slide_h * 0.06), slide_w - 2 * margin, int(slide_h * 0.16), title_paras),
        _text_box(3, "Content", margin, int(slide_h * 0.26), slide_w - 2 * margin, int(slide_h * 0.62), body_paras),
        _text_box(4, "PageNo", slide_w - margin - int(slide_w * 0.08), int(slide_h * 0.93),
                  int(slide_w * 0.08), int(slide_h * 0.05), footer_paras),
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sld {NS_P}><p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "".join(shapes)
        + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )


def bind_template(template_bytes: bytes, pages: list, layout_map: dict) -> tuple:
    """真继承：保留模板母版/版式/主题部件原样，仅追加新内容页；新页 rels 指向模板既有版式。"""
    parts = read_zip(template_bytes)
    pres_xml = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    pres_rels = parts.get("ppt/_rels/presentation.xml.rels", b"").decode("utf-8", "replace")
    ctypes = parts.get("[Content_Types].xml", b"").decode("utf-8", "replace")

    m = re.search(r'<p:sldSz[^>]*cx="(\d+)"[^>]*cy="(\d+)"', pres_xml)
    if not m:
        m = re.search(r'<p:sldSz[^>]*cy="(\d+)"[^>]*cx="(\d+)"', pres_xml)
    slide_w, slide_h = (int(m.group(1)), int(m.group(2))) if m else (DEFAULT_W, DEFAULT_H)

    base_master = {n: sha256(parts[n]) for n in parts if MASTER_RE.match(n)}
    base_theme = {n: sha256(parts[n]) for n in parts if THEME_RE.match(n)}
    base_layout = {n: sha256(parts[n]) for n in parts if LAYOUT_RE.match(n)}

    # 只继承样式：移除模板原有内容页（母版/版式/主题原样保留）
    for name in list(parts):
        if SLIDE_RE.match(name) or SLIDE_RELS_RE.match(name):
            parts.pop(name, None)
    pres_xml = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", "<p:sldIdLst></p:sldIdLst>", pres_xml, flags=re.S)
    pres_rels = re.sub(r'<Relationship[^>]*Target="slides/slide\d+\.xml"[^>]*/>', "", pres_rels)
    ctypes = re.sub(r'<Override PartName="/ppt/slides/slide\d+\.xml"[^>]*/>', "", ctypes)

    max_rel = max([int(x) for x in re.findall(r'Id="rId(\d+)"', pres_rels)] or [0])
    total = max(1, len(pages))
    sld_ids, used_layouts = [], []
    for index, page in enumerate(pages):
        slide_name = f"ppt/slides/slide{index + 1}.xml"
        layout_path = layout_map.get(page["n"]) or "ppt/slideLayouts/slideLayout1.xml"
        used_layouts.append(layout_path)
        rel_id = max_rel + index + 1
        parts[slide_name] = slide_xml(
            page["title"], page["bullets"], page["n"], total, slide_w, slide_h).encode("utf-8")
        parts[f"ppt/slides/_rels/slide{index + 1}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideLayout" '
            f'Target="../slideLayouts/{layout_path.split("/")[-1]}"/></Relationships>'
        ).encode("utf-8")
        pres_rels = pres_rels.replace(
            "</Relationships>",
            f'<Relationship Id="rId{rel_id}" Type="{RT}/slide" Target="slides/slide{index + 1}.xml"/></Relationships>')
        sld_ids.append(f'<p:sldId id="{256 + index}" r:id="rId{rel_id}"/>')
        if f"/ppt/slides/slide{index + 1}.xml" not in ctypes:
            ctypes = ctypes.replace(
                "</Types>",
                f'<Override PartName="/ppt/slides/slide{index + 1}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/></Types>')

    if "<p:sldIdLst>" in pres_xml:
        pres_xml = pres_xml.replace("</p:sldIdLst>", "".join(sld_ids) + "</p:sldIdLst>")
    elif "<p:sldMasterIdLst>" in pres_xml:
        pres_xml = pres_xml.replace(
            "<p:sldMasterIdLst>",
            "<p:sldIdLst>" + "".join(sld_ids) + "</p:sldIdLst><p:sldMasterIdLst>")
    else:
        pres_xml = pres_xml.replace(
            "</p:presentation>", "<p:sldIdLst>" + "".join(sld_ids) + "</p:sldIdLst></p:presentation>")

    parts["ppt/presentation.xml"] = pres_xml.encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = pres_rels.encode("utf-8")
    parts["[Content_Types].xml"] = ctypes.encode("utf-8")
    pptx = write_zip(parts)

    new_master = {n: sha256(parts[n]) for n in parts if MASTER_RE.match(n)}
    new_theme = {n: sha256(parts[n]) for n in parts if THEME_RE.match(n)}
    new_layout = {n: sha256(parts[n]) for n in parts if LAYOUT_RE.match(n)}
    inheritance = {
        "ok": True,
        "master_unchanged": new_master == base_master,
        "theme_unchanged": new_theme == base_theme,
        "layout_unchanged": new_layout == base_layout,
        "master_sha256": new_master,
        "theme_sha256": new_theme,
        "layout_map": [{"n": page["n"], "layout": layout_map.get(page["n"])} for page in pages],
        "slide_w": slide_w, "slide_h": slide_h,
    }
    return pptx, inheritance


def build_standalone_deck(pages: list) -> bytes:
    """无外部模板时的内置母版兜底：自带 1 母版 + 1 版式 + 1 主题 + N 页（非模板继承）。"""
    n = max(1, len(pages))
    master = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<p:sldMaster {NS_P}><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
              '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
              '</p:spTree></p:cSld>'
              '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
              '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
              '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="2800"/></a:lvl1pPr></p:titleStyle>'
              '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle>'
              '<p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles></p:sldMaster>')
    layout = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<p:sldLayout {NS_P} type="titleAndContent" preserve="1"><p:cSld name="Title and Content"><p:spTree>'
              '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
              '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
              '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
    theme = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Std">'
             '<a:themeElements><a:clrScheme name="Std"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
             '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
             '<a:accent1><a:srgbClr val="1F77B4"/></a:accent1><a:accent2><a:srgbClr val="D62728"/></a:accent2>'
             '<a:accent3><a:srgbClr val="2CA02C"/></a:accent3><a:accent4><a:srgbClr val="9467BD"/></a:accent4>'
             '<a:accent5><a:srgbClr val="FF7F0E"/></a:accent5><a:accent6><a:srgbClr val="17BECF"/></a:accent6>'
             '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
             '<a:fontScheme name="Std"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
             '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
             '<a:fmtScheme name="Std"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
             '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
             '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
             '<a:ln w="25400"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
             '<a:ln w="38100"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
             '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
             '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
             '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
             '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
             '</a:fmtScheme></a:themeElements></a:theme>')
    parts = {
        "[Content_Types].xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            + "".join(f'<Override PartName="/ppt/slides/slide{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
                      for i in range(n))
            + "</Types>"),
        "_rels/.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/officeDocument" Target="ppt/presentation.xml"/></Relationships>'),
        "ppt/presentation.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentation {NS_P} saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            "<p:sldIdLst>" + "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 2}"/>' for i in range(n)) + "</p:sldIdLst>"
            f'<p:sldSz cx="{DEFAULT_W}" cy="{DEFAULT_H}"/></p:presentation>'),
        "ppt/_rels/presentation.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
            + "".join(f'<Relationship Id="rId{i + 2}" Type="{RT}/slide" Target="slides/slide{i + 1}.xml"/>'
                      for i in range(n))
            + "</Relationships>"),
        "ppt/slideMasters/slideMaster1.xml": master,
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            f'<Relationship Id="rId2" Type="{RT}/theme" Target="../theme/theme1.xml"/></Relationships>'),
        "ppt/slideLayouts/slideLayout1.xml": layout,
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>'),
        "ppt/theme/theme1.xml": theme,
    }
    for index, page in enumerate(pages):
        parts[f"ppt/slides/slide{index + 1}.xml"] = slide_xml(
            page["title"], page["bullets"], page["n"], n, DEFAULT_W, DEFAULT_H).encode("utf-8")
        parts[f"ppt/slides/_rels/slide{index + 1}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>'
        ).encode("utf-8")
    return write_zip(parts)


def deck_info(data: bytes) -> dict:
    """再解析产物：页数/标题/版式引用/母版/主题指纹。供 audit 做 round-trip。"""
    try:
        parts = read_zip(data)
    except (zipfile.BadZipFile, OSError):
        return {"valid": False, "slides": 0, "error": "not a zip/pptx"}
    slides = sorted(n for n in parts if SLIDE_RE.match(n))
    pres_xml = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    declared = len(re.findall(r"<p:sldId ", pres_xml)) or len(slides)
    titles = []
    for name in slides:
        xml = parts[name].decode("utf-8", "replace")
        found = re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)
        titles.append(found[0] if found else "")
    layouts = sorted(n for n in parts if LAYOUT_RE.match(n))
    masters = sorted(n for n in parts if MASTER_RE.match(n))
    themes = sorted(n for n in parts if THEME_RE.match(n))
    slide_rel_layouts = []
    for name in slides:
        rel_name = f"ppt/slides/_rels/{name.split('/')[-1]}.rels"
        rel_xml = parts.get(rel_name, b"").decode("utf-8", "replace")
        m = re.search(r'Target="\.\./slideLayouts/(slideLayout\d+\.xml)"', rel_xml)
        slide_rel_layouts.append(m.group(1) if m else None)
    return {
        "valid": bool(slides) and bool(masters),
        "slides": declared,
        "slide_names": slides,
        "titles": titles,
        "layouts": layouts,
        "masters": masters,
        "themes": themes,
        "slide_rel_layouts": slide_rel_layouts,
        "master_sha256": {n: sha256(parts[n]) for n in masters},
        "layout_sha256": {n: sha256(parts[n]) for n in layouts},
        "theme_sha256": {n: sha256(parts[n]) for n in themes},
    }


# ------------------------------------------------------------------ 导出：PDF 与 PNG

def _pdf_escape(text: str) -> str:
    ascii_text = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(text))
    return ascii_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(pages: list) -> bytes:
    """文本层 PDF 兜底：页数 = 幻灯片数（非 ASCII 转 '?'，权威渲染走 office-renderer）。"""
    n = max(1, len(pages))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(n))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for page in pages:
        lines = [str(page.get("title") or "")]
        lines += ["- " + str(item) for item in (page.get("bullets") or [])]
        stream = ["BT", "/F1 20 Tf", "72 760 Td", "28 TL"]
        for index, line in enumerate(lines[:24]):
            if index:
                stream.append("T*")
            stream.append(f"({_pdf_escape(line[:110])}) Tj")
        stream.append("ET")
        data = "\n".join(stream).encode("latin-1", "replace")
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                       b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
                       + f"{len(objects) + 2} 0 R".encode() + b" >>")
        objects.append(b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream")
    payload = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode())
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref_at = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode())
    return bytes(payload)


def pdf_page_count(data: bytes) -> int:
    if not data.startswith(b"%PDF-"):
        return 0
    return max(0, data.count(b"/Type /Page") - data.count(b"/Type /Pages"))


# 5x7 位图字模（列序），仅用于 PNG 结构预览；其余字符落 '?'
_FONT = {
    ' ': (".....", ".....", ".....", ".....", "....."),
    'A': (".##..", "#..#.", "#..#.", "#####", "#..#."),
    'B': ("####.", "#..#.", "####.", "#..#.", "####."),
    'C': (".####", "#....", "#....", "#....", ".####"),
    'D': ("###..", "#..#.", "#..#.", "#..#.", "###.."),
    'E': ("#####", "#....", "####.", "#....", "#####"),
    'F': ("#####", "#....", "####.", "#....", "#...."),
    'G': (".####", "#....", "#.###", "#..#.", ".####"),
    'H': ("#..#.", "#..#.", "#####", "#..#.", "#..#."),
    'I': ("###..", ".#...", ".#...", ".#...", "###.."),
    'J': ("..###", "...#.", "...#.", "#..#.", ".##.."),
    'K': ("#..#.", "#.#..", "##...", "#.#..", "#..#."),
    'L': ("#....", "#....", "#....", "#....", "#####"),
    'M': ("#...#", "##.##", "#.#.#", "#...#", "#...#"),
    'N': ("#...#", "##..#", "#.#.#", "#..##", "#...#"),
    'O': (".###.", "#...#", "#...#", "#...#", ".###."),
    'P': ("####.", "#...#", "####.", "#....", "#...."),
    'Q': (".###.", "#...#", "#...#", "#.#.#", ".###."),
    'R': ("####.", "#...#", "####.", "#.#..", "#..##"),
    'S': (".####", "#....", ".###.", "....#", "####."),
    'T': ("#####", ".#...", ".#...", ".#...", ".#..."),
    'U': ("#...#", "#...#", "#...#", "#...#", ".###."),
    'V': ("#...#", "#...#", "#...#", ".#.#.", "..#.."),
    'W': ("#...#", "#...#", "#.#.#", "##.##", "#...#"),
    'X': ("#...#", ".#.#.", "..#..", ".#.#.", "#...#"),
    'Y': ("#...#", ".#.#.", "..#..", "..#..", "..#.."),
    'Z': ("#####", "...#.", "..#..", ".#...", "#####"),
    '0': (".###.", "#..##", "#.#.#", "##..#", ".###."),
    '1': ("..#..", ".##..", "..#..", "..#..", "#####"),
    '2': (".###.", "#...#", "...#.", "..#..", "#####"),
    '3': (".###.", "#...#", "..##.", "#...#", ".###."),
    '4': ("...#.", "..##.", ".#.#.", "#####", "...#."),
    '5': ("#####", "#....", "####.", "....#", "####."),
    '6': (".###.", "#....", "####.", "#...#", ".###."),
    '7': ("#####", "....#", "...#.", "..#..", "..#.."),
    '8': (".###.", "#...#", ".###.", "#...#", ".###."),
    '9': (".###.", "#...#", ".####", "....#", ".###."),
    '-': (".....", ".....", "#####", ".....", "....."),
    '.': (".....", ".....", ".....", ".....", ".#..."),
    ':': (".....", ".#...", ".....", ".#...", "....."),
    '/': ("....#", "...#.", "..#..", ".#...", "#...."),
    '(': ("...#.", "..#..", ".#...", "..#..", "...#."),
    ')': (".#...", "..#..", "...#.", "..#..", ".#..."),
    '?': (".###.", "#...#", "...#.", "..#..", "..#.."),
    '!': ("..#..", "..#..", "..#..", ".....", "..#.."),
    ',': (".....", ".....", ".....", ".#...", "#...."),
    '+': (".....", "..#..", "#####", "..#..", "....."),
    '=': (".....", "#####", ".....", "#####", "....."),
    '%': ("#...#", "....#", "...#.", "..#..", "#...#"),
}


def _draw_text(rgb: bytearray, width: int, height: int, text: str, x0: int, y0: int,
               scale: int, color: bytes, max_chars: int = 60) -> None:
    cx = x0
    for ch in str(text)[:max_chars].upper():
        glyph = _FONT.get(ch) or _FONT.get('?')
        if cx + 5 * scale + scale > width:
            break
        for col in range(5):
            colstr = glyph[col]
            for row in range(7):
                if row < len(colstr) and colstr[row] == '#':
                    px, py = cx + col * scale, y0 + row * scale
                    for dx in range(scale):
                        for dy in range(scale):
                            if 0 <= px + dx < width and 0 <= py + dy < height:
                                idx = ((py + dy) * width + (px + dx)) * 3
                                rgb[idx:idx + 3] = color
        cx += 6 * scale


def _png_bytes(width: int, height: int, rgb: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def write_png_preview(pages: list) -> bytes:
    """结构预览 PNG：白底 + 标题条 + 标题文字 + 要点条 + 页码。非 ASCII 转 '?'。"""
    width, height = 1280, 720
    rgb = bytearray(b"\xff\xff\xff" * width * height)
    accent = bytes((31, 119, 180))
    dark = bytes((20, 40, 70))
    gray = bytes((214, 226, 236))
    total = max(1, len(pages))
    for pi, page in enumerate(pages):
        y0 = pi * 180
        if y0 + 180 > height:
            break
        for y in range(y0, y0 + 28):
            for x in range(0, width):
                rgb[((y * width) + x) * 3:(y * width + x) * 3 + 3] = accent
        _draw_text(rgb, width, height, str(page.get("title") or "Slide"), 24, y0 + 46, 3, dark)
        for bi in range(min(len(page.get("bullets") or []), 4)):
            for x in range(40, 40 + 620):
                for y in range(y0 + 96 + bi * 26, y0 + 96 + bi * 26 + 14):
                    rgb[((y * width) + x) * 3:(y * width + x) * 3 + 3] = gray
        _draw_text(rgb, width, height, f"{pi + 1}/{total}", 1160, 690, 2, dark, max_chars=10)
    return _png_bytes(width, height, rgb)


# ------------------------------------------------------------------ 审计（回读产物做 round-trip）

def audit(pptx_bytes: bytes, pdf_bytes: bytes, png_bytes: bytes, pages: list,
          inheritance: dict, base_layouts: set, evidence_total: int) -> tuple:
    info = deck_info(pptx_bytes)
    pdf_pages = pdf_page_count(pdf_bytes)
    checks = [
        {"id": "pptx_valid", "required": "可解压且含 presentation.xml 与 slideN.xml",
         "measured": f"slides={info.get('slides')}, masters={len(info.get('masters') or [])}",
         "verdict": "pass" if info.get("valid") else "fail"},
        {"id": "slide_count", "required": "页数与 IR 计划一致",
         "measured": f"pptx={info.get('slides')} plan={len(pages)} pdf={pdf_pages}",
         "verdict": "pass" if info.get("slides") == len(pages) == pdf_pages else "fail"},
        {"id": "pdf_pages", "required": "PDF 页数 == 幻灯片数",
         "measured": f"{pdf_pages} 页",
         "verdict": "pass" if pdf_pages == len(pages) else "fail"},
        {"id": "editable_source", "required": "pptx 为真实 ZIP/OOXML",
         "measured": "PK 头" if pptx_bytes[:2] == b"PK" else "非 ZIP",
         "verdict": "pass" if pptx_bytes[:2] == b"PK" else "fail"},
        {"id": "png_preview", "required": "存在真实渲染预览（结构预览/office 渲染）",
         "measured": "PNG 头" if png_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "无 PNG",
         "verdict": "pass" if png_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "warn"},
    ]
    if inheritance:
        checks.append({
            "id": "template_inheritance", "required": "母版/主题 sha256 与模板一致",
            "measured": f"master_unchanged={inheritance.get('master_unchanged')}, theme_unchanged={inheritance.get('theme_unchanged')}",
            "verdict": "pass" if inheritance.get("master_unchanged") and inheritance.get("theme_unchanged") else "fail"})
        own_layouts = {n.split("/")[-1] for n in (info.get("layouts") or [])}
        bad_refs = [r for r in (info.get("slide_rel_layouts") or []) if r not in own_layouts]
        checks.append({
            "id": "layout_ref", "required": "新页 rels 指向产物内既有版式",
            "measured": f"rel_layouts={info.get('slide_rel_layouts')}, 非法引用={bad_refs}",
            "verdict": "pass" if not bad_refs and all(info.get("slide_rel_layouts") or []) else "fail"})
    if evidence_total:
        verbatim = sum(1 for p in pages for ev in p["evidence"] if ev["quote_status"] == "verbatim")
        checks.append({
            "id": "evidence_quotes", "required": "证据引文逐字命中材料",
            "measured": f"{verbatim}/{evidence_total} 条逐字命中",
            "verdict": "pass" if verbatim == evidence_total else "warn"})
    # round-trip：回读产物标题与 IR 标题一致
    mismatches = []
    emitted = info.get("titles") or []
    for i, page in enumerate(pages):
        got = emitted[i] if i < len(emitted) else None
        if got != page["title"]:
            mismatches.append({"n": page["n"], "planned": page["title"], "emitted": got})
    checks.append({
        "id": "round_trip", "required": "回读产物标题与 IR 一致",
        "measured": f"mismatches={len(mismatches)}",
        "verdict": "pass" if not mismatches else "fail"})
    return checks, info, mismatches
