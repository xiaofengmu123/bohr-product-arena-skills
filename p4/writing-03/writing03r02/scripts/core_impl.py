#!/usr/bin/env python3
"""core_impl — 确定性底层实现（仅标准库）。

职责：
1. 输入发现与容错读取（BOM / 坏编码 / 坏 JSON / 超大文件）。
2. 模板格式规格反推（docx 的 styles.xml/sectPr、tex 的 preamble）。
3. 原稿内容单元盘点（标题/段落/表格/图/公式/脚注/引用/目录），用于转换保真度核验。
4. 规格-原稿差距清单生成（把“认得准/排得对/转得全/交得出”落成逐项可勾选清单）。
5. result.json 骨架落盘与增量合并（幂等、只增不重建）。

所有函数确定性运行；不联网、不调用外部程序、不产生随机数。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WPML_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

MAX_TEXT_FILE = 2 * 1024 * 1024  # 2MB 上限，超限跳过并记录
SUPPORTED_TEXT = {".md", ".txt", ".json", ".yaml", ".yml", ".tex", ".csv", ".bib", ".xml"}


def _w(tag: str) -> str:
    return "{%s}%s" % (W_NS, tag)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_text_tolerant(path: str, limit: int = MAX_TEXT_FILE) -> tuple[str, list[str]]:
    """按 utf-8-sig 优先、gbk/utf-8 回退读取文本；返回 (text, degradations)。"""
    degs: list[str] = []
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size > limit:
        degs.append("skip_oversize:%s(%d bytes)" % (os.path.basename(path), size))
        return "", degs
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            raw = data.decode(enc)
            break
        except (UnicodeDecodeError, OSError) as exc:
            degs.append("decode_%s_failed:%s" % (enc, os.path.basename(path)))
            continue
    if raw is None:
        degs.append("undecodable:" + os.path.basename(path))
        return "", degs
    return raw, degs


def load_json_tolerant(path: str) -> tuple[object, list[str]]:
    """坏 JSON 不崩溃；返回 (obj|None, degradations)。"""
    text, degs = read_text_tolerant(path)
    if not text.strip():
        return None, degs + ["empty_json:" + os.path.basename(path)]
    try:
        return json.loads(text), degs
    except (ValueError, TypeError) as exc:
        degs.append("invalid_json:%s:%s" % (os.path.basename(path), exc))
        return None, degs


def classify_file(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext == ".docx":
        return "docx"
    if ext == ".tex":
        return "tex"
    if ext == ".pdf":
        return "pdf"
    if ext in (".doc", ".odt", ".rtf"):
        return "office"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".tif", ".tiff"):
        return "image"
    if ext in SUPPORTED_TEXT:
        return "text"
    return "other"


def discover_input(input_dir: str) -> dict:
    """扫描输入目录，不假设固定文件名。返回发现清单。"""
    found = {"files": [], "degradations": [], "case": None, "task": None}
    if not os.path.isdir(input_dir):
        found["degradations"].append("missing_input_dir:" + input_dir)
        return found
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as exc:
        found["degradations"].append("listdir_failed:%s" % exc)
        return found
    for name in names:
        full = os.path.join(input_dir, name)
        if not os.path.isfile(full):
            continue
        kind = classify_file(name)
        found["files"].append({"name": name, "path": full, "kind": kind,
                               "size": os.path.getsize(full)})
        low = name.lower()
        if low == "case.json" and found["case"] is None:
            found["case"] = full
        if low == "task.md" and found["task"] is None:
            found["task"] = full
    return found


# ---------------------------------------------------------------------------
# 模板规格反推：docx
# ---------------------------------------------------------------------------

def _half_points_to_pt(v: str) -> str:
    try:
        return "%.1fpt" % (int(v) / 2.0)
    except (ValueError, TypeError):
        return v


def _line_spacing(attrs) -> str:
    line = attrs.get(_w("line"))
    rule = attrs.get(_w("lineRule"), "auto")
    if line is None:
        return ""
    try:
        ln = int(line)
    except ValueError:
        return line
    if rule in ("exact", "atLeast"):
        return "%.1fpt(%s)" % (ln / 20.0, rule)
    if ln == 240:
        return "1.0x"
    return "%.2fx" % (ln / 240.0)


def _style_summary(style_el) -> dict | None:
    """提取单个 style 的字体/字号/行距/缩进。"""
    sid = style_el.get(_w("styleId"), "")
    name_el = style_el.find(_w("name"))
    name = name_el.get(_w("val"), "") if name_el is not None else ""
    rpr = style_el.find(_w("rPr"))
    ppr = style_el.find(_w("pPr"))
    font = ""
    if rpr is not None:
        fonts = rpr.find(_w("rFonts"))
        if fonts is not None:
            font = (fonts.get(_w("ascii"), "") or fonts.get(_w("hAnsi"), "")
                    or fonts.get(_w("eastAsia"), ""))
        sz = rpr.find(_w("sz"))
        size = _half_points_to_pt(sz.get(_w("val"))) if sz is not None and sz.get(_w("val")) else ""
    else:
        size = ""
    spacing = ""
    indent = ""
    if ppr is not None:
        sp = ppr.find(_w("spacing"))
        if sp is not None:
            spacing = _line_spacing(sp.attrib)
        ind = ppr.find(_w("ind"))
        if ind is not None:
            left = ind.get(_w("left")) or ind.get(_w("firstLine")) or ""
            indent = left
    return {"styleId": sid, "name": name, "font": font, "size": size,
            "lineSpacing": spacing, "indent": indent}


def _is_heading(name: str) -> bool:
    low = name.lower()
    return low.startswith("heading") or "标题" in name or "標題" in name or "chapter" in low


def extract_docx_spec(path: str) -> dict:
    """从 docx 反推结构化的格式规格。"""
    spec = {"format": "docx", "page": {}, "defaults": {}, "styles": {},
            "headings": {}, "captions": {}, "numbering_notes": [], "degradations": []}
    try:
        zf = zipfile.ZipFile(path)
        names = set(zf.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        spec["degradations"].append("bad_docx:%s:%s" % (os.path.basename(path), exc))
        return spec

    def _get_text_of(xml_name: str) -> str | None:
        if xml_name not in names:
            return None
        try:
            return zf.read(xml_name).decode("utf-8", "replace")
        except (OSError, zipfile.BadZipFile):
            return None

    # 页面尺寸与页边距
    doc_xml = _get_text_of("word/document.xml")
    if doc_xml:
        try:
            root = ET.fromstring(doc_xml)
            sect = root.find(".//" + _w("sectPr"))
            if sect is not None:
                pg = sect.find(_w("pgSz"))
                if pg is not None:
                    spec["page"]["size"] = {"w": pg.get(_w("w")), "h": pg.get(_w("h"))}
                mar = sect.find(_w("pgMar"))
                if mar is not None:
                    spec["page"]["margins"] = {
                        "top": mar.get(_w("top")), "bottom": mar.get(_w("bottom")),
                        "left": mar.get(_w("left")), "right": mar.get(_w("right")),
                        "header": mar.get(_w("header")), "footer": mar.get(_w("footer"))}
        except ET.ParseError as exc:
            spec["degradations"].append("doc_xml_parse_failed:%s" % exc)

    # 样式与默认字体
    styles_xml = _get_text_of("word/styles.xml")
    if styles_xml:
        try:
            sroot = ET.fromstring(styles_xml)
            dd = sroot.find(_w("docDefaults"))
            if dd is not None:
                rprd = dd.find(_w("rPrDefault"))
                if rprd is not None:
                    rpr = rprd.find(_w("rPr"))
                    if rpr is not None:
                        fonts = rpr.find(_w("rFonts"))
                        if fonts is not None:
                            spec["defaults"]["font"] = (fonts.get(_w("ascii"), "")
                                                        or fonts.get(_w("hAnsi"), "")
                                                        or fonts.get(_w("eastAsia"), ""))
                        sz = rpr.find(_w("sz"))
                        if sz is not None and sz.get(_w("val")):
                            spec["defaults"]["size"] = _half_points_to_pt(sz.get(_w("val")))
            for st in sroot.findall(_w("style")):
                s = _style_summary(st)
                spec["styles"][s["styleId"]] = s
                if _is_heading(s["name"]):
                    spec["headings"][s["name"]] = s
                if "caption" in s["name"].lower() or "题注" in s["name"] or "註" in s["name"]:
                    spec["captions"][s["name"]] = s
        except ET.ParseError as exc:
            spec["degradations"].append("styles_xml_parse_failed:%s" % exc)

    # 多级编号
    num_xml = _get_text_of("word/numbering.xml")
    if num_xml:
        try:
            nroot = ET.fromstring(num_xml)
            lvl_format = {}
            for lvl in nroot.findall(".//" + _w("lvl")):
                ilvl = lvl.get(_w("ilvl"))
                fmt = lvl.find(_w("numFmt"))
                lvl_format[ilvl] = fmt.get(_w("val")) if fmt is not None else "?"
            if lvl_format:
                spec["numbering_notes"].append("docx numbering levels: %s" %
                                               json.dumps(lvl_format, ensure_ascii=False))
        except ET.ParseError:
            spec["degradations"].append("numbering_xml_parse_failed")
    return spec


# ---------------------------------------------------------------------------
# 模板规格反推：tex
# ---------------------------------------------------------------------------

def extract_tex_spec(path: str) -> dict:
    """从 LaTeX preamble 反推格式规格（确定性正则，不执行 latex）。"""
    text, degs = read_text_tolerant(path)
    spec = {"format": "tex", "documentclass": None, "font": {}, "geometry": {},
            "packages": [], "bibliographystyle": None, "degradations": degs}
    m = re.search(r"\\documentclass(?:\[([^\]]*)\])?\s*\{([^}]+)\}", text)
    if m:
        opts = m.group(1) or ""
        spec["documentclass"] = m.group(2)
        if re.search(r"\b1[012]pt\b", opts):
            spec["font"]["size"] = re.search(r"\b(1[012]pt)\b", opts).group(1)
        if "twoside" in opts:
            spec["font"]["twoside"] = True
    gm = re.search(r"\\geometry\s*\{([^}]*)\}", text, re.S)
    if gm:
        body = gm.group(1)
        for key in ("top", "bottom", "left", "right", "inner", "outer"):
            km = re.search(r"%s\s*=\s*([0-9.]+[a-z]+)" % key, body)
            if km:
                spec["geometry"][key] = km.group(1)
    for pkg in re.findall(r"\\usepackage(?:\[[^\]]*\])?\s*\{([^}]+)\}", text):
        for p in pkg.split(","):
            spec["packages"].append(p.strip())
    fs = re.search(r"\\setmainfont\s*\{([^}]+)\}", text)
    if fs:
        spec["font"]["mainfont"] = fs.group(1)
    bs = re.search(r"\\bibliographystyle\s*\{([^}]+)\}", text)
    if bs:
        spec["bibliographystyle"] = bs.group(1)
    return spec


# ---------------------------------------------------------------------------
# 内容单元盘点（转换保真度基线）
# ---------------------------------------------------------------------------

def inventory_docx(path: str) -> dict:
    inv = {"format": "docx", "units": {}, "degradations": []}
    try:
        zf = zipfile.ZipFile(path)
        names = set(zf.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        inv["degradations"].append("bad_docx:%s" % exc)
        return inv
    doc = None
    if "word/document.xml" in names:
        try:
            doc = zf.read("word/document.xml").decode("utf-8", "replace")
        except OSError:
            inv["degradations"].append("document_read_failed")
    if doc:
        try:
            root = ET.fromstring(doc)
            bodies = list(root.iter(_w("body")))
            if bodies:
                body = bodies[0]
                inv["units"]["paragraphs"] = len(list(body.iter(_w("p"))))
                inv["units"]["tables"] = len(list(body.iter(_w("tbl"))))
                inv["units"]["drawings"] = len(list(body.iter(_w("drawing"))))
                inv["units"]["hyperlinks"] = len(list(body.iter(_w("hyperlink"))))
                heads = [p for p in body.iter(_w("p"))
                         if p.find(_w("pPr")) is not None
                         and p.find(_w("pPr")).find(_w("pStyle")) is not None]
                head_styles = []
                for p in heads:
                    st = p.find(_w("pPr")).find(_w("pStyle")).get(_w("val"), "")
                    if st and st.lower() not in head_styles:
                        head_styles.append(st)
                inv["units"]["heading_styles"] = head_styles
                # 目录域
                instr = [t.text or "" for t in body.iter(_w("instrText"))]
                inv["units"]["toc_fields"] = [x for x in instr if "TOC" in x.upper()]
        except ET.ParseError as exc:
            inv["degradations"].append("document_parse_failed:%s" % exc)
    # 脚注
    if "word/footnotes.xml" in names:
        try:
            froot = ET.fromstring(zf.read("word/footnotes.xml").decode("utf-8", "replace"))
            notes = [f for f in froot.iter(_w("footnote"))
                     if f.get(_w("type")) not in ("separator", "continuationSeparator")]
            inv["units"]["footnotes"] = len(notes)
        except (ET.ParseError, OSError):
            inv["degradations"].append("footnotes_parse_failed")
    return inv


def inventory_tex(path: str) -> dict:
    text, degs = read_text_tolerant(path)
    inv = {"format": "tex", "units": {}, "degradations": degs}
    inv["units"]["sections"] = len(re.findall(r"\\section\*?\{", text))
    inv["units"]["subsections"] = len(re.findall(r"\\subsection\*?\{", text))
    inv["units"]["subsubsections"] = len(re.findall(r"\\subsubsection\*?\{", text))
    inv["units"]["chapters"] = len(re.findall(r"\\chapter\*?\{", text))
    inv["units"]["tables"] = len(re.findall(r"\\begin\s*\{table", text))
    inv["units"]["figures"] = len(re.findall(r"\\begin\s*\{figure", text))
    inv["units"]["includegraphics"] = len(re.findall(r"\\includegraphics", text))
    inv["units"]["equations"] = len(re.findall(r"\\begin\s*\{(equation|align|alignat|gather|multline)", text))
    inv["units"]["footnotes"] = len(re.findall(r"\\footnote\s*\{", text))
    inv["units"]["citations"] = len(re.findall(r"\\cite[tp]?\s*[\[{]", text)) + len(re.findall(r"\\bibitem", text))
    inv["units"]["tableofcontents"] = len(re.findall(r"\\tableofcontents", text))
    return inv


def inventory_text(path: str) -> dict:
    text, degs = read_text_tolerant(path)
    lines = text.splitlines()
    inv = {"format": "text", "units": {"lines": len(lines),
                                        "chars": len(text)}, "degradations": degs}
    inv["units"]["markdown_headings"] = len(re.findall(r"^#{1,6}\s", text, re.M))
    inv["units"]["fenced_blocks"] = len(re.findall(r"^```", text, re.M))
    return inv


def inventory_file(path: str, kind: str | None = None) -> dict:
    kind = kind or classify_file(path)
    if kind == "docx":
        return inventory_docx(path)
    if kind == "tex":
        return inventory_tex(path)
    if kind == "text":
        return inventory_text(path)
    return {"format": kind, "units": {}, "degradations":
            ["no_stdlib_inventory:" + kind]}


# ---------------------------------------------------------------------------
# 规格-原稿差距清单
# ---------------------------------------------------------------------------

def extract_docx_text(path: str) -> tuple[str, list[str]]:
    """从 docx 抽取全部可见文本（正文/表格/脚注），用于摘录定位校验。"""
    texts: list[str] = []
    degs: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
        names = set(zf.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        return "", ["bad_docx:" + str(exc)]
    for xml_name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
        if xml_name not in names:
            continue
        try:
            root = ET.fromstring(zf.read(xml_name).decode("utf-8", "replace"))
        except (ET.ParseError, OSError):
            degs.append("text_extract_parse_failed:" + xml_name)
            continue
        texts.extend(t.text for t in root.iter(_w("t")) if t and t.text)
    return " ".join(texts), degs


def build_checklist(spec: dict, inventory: dict) -> dict:
    """把“认得准/排得对/转得全/交得出”落成逐项清单。"""
    items = []
    sf = spec.get("format", "?")
    items.append({"id": "spec_format", "group": "认得准", "label": "模板格式已识别",
                  "detail": "模板格式=%s" % sf, "needs_measure": False})
    if spec.get("page", {}).get("margins"):
        items.append({"id": "spec_margins", "group": "认得准", "label": "页边距",
                      "detail": json.dumps(spec["page"]["margins"], ensure_ascii=False)})
    if spec.get("defaults", {}).get("font") or spec.get("defaults", {}).get("size"):
        items.append({"id": "spec_default_font", "group": "认得准", "label": "正文默认字体字号",
                      "detail": "%s %s" % (spec["defaults"].get("font", ""),
                                           spec["defaults"].get("size", ""))})
    for name, h in spec.get("headings", {}).items():
        items.append({"id": "spec_heading_" + name, "group": "排得对", "label": "标题样式",
                      "detail": "%s: %s %s %s" % (name, h.get("font", ""), h.get("size", ""),
                                                  h.get("lineSpacing", ""))})
    if spec.get("bibliographystyle"):
        items.append({"id": "spec_bibstyle", "group": "排得对", "label": "参考文献样式",
                      "detail": spec["bibliographystyle"]})
    if spec.get("numbering_notes"):
        items.append({"id": "spec_numbering", "group": "排得对", "label": "多级编号",
                      "detail": "; ".join(spec["numbering_notes"])})
    units = inventory.get("units", {})
    for key, label in (("headings", "标题"), ("paragraphs", "段落"), ("tables", "表格"),
                       ("drawings", "图"), ("footnotes", "脚注"), ("equations", "公式"),
                       ("citations", "引用"), ("includegraphics", "插图")):
        if key in units:
            items.append({"id": "preserve_" + key, "group": "转得全", "label": "保留" + label,
                          "detail": "原稿单元数=%s" % units[key], "needs_measure": True})
    items.append({"id": "export_editable", "group": "交得出", "label": "可编辑产物",
                  "detail": "docx/tex/md 其一", "needs_measure": False})
    items.append({"id": "export_preview", "group": "交得出", "label": "PDF/PNG 预览",
                  "detail": "字体嵌入、页码/目录可刷新"})
    return {"checklist": items, "template_format": sf}


# ---------------------------------------------------------------------------
# result.json 骨架与增量合并
# ---------------------------------------------------------------------------

FALLBACK_CONTRACT = ["deliverable", "fact_source_map", "version_boundary"]


def build_skeleton(case_id: str, output_contract: dict | None,
                   contract_fields: list[str] | None = None) -> dict:
    """构造 partial 骨架；output_contract 字段原样回填键，缺值 null/[]。"""
    contract = output_contract if isinstance(output_contract, dict) else {}
    fields = contract_fields or list(contract.keys()) or FALLBACK_CONTRACT
    skeleton = {
        "case_id": case_id or None,
        "status": "partial",
        "runtime": {"started": utcnow_iso(), "seconds": 0, "requests_used": 0,
                    "phase_timestamps": {"skeleton": utcnow_iso()}},
        "errors": [],
        "degradations": [],
        "sources": [],
        "uncertainty": {"claims_requiring_confirmation": [], "conflicting_evidence": [],
                        "material_gaps": [], "limitations": []},
        "pending_human": [],
        "change_log": [{"ts": utcnow_iso(), "action": "skeleton",
                        "detail": "partial 骨架落盘"}],
    }
    if isinstance(contract, dict):
        skeleton["output_contract"] = contract  # 原样保留原始契约，供核验
    # 主产物 / 契约字段：键原样回填；deliverable 恒为结构化对象，fact/version 恒为列表
    for f in fields:
        if f == "deliverable":
            d = {"content": None, "artifact_paths": {}, "format_spec_applied": None}
            if f in contract:
                if isinstance(contract[f], dict):
                    d.update(contract[f])
                elif contract[f] is not None:
                    d["requirement"] = contract[f]
            skeleton[f] = d
        elif f == "fact_source_map":
            skeleton[f] = contract.get(f) if isinstance(contract.get(f), list) else []
        elif f == "version_boundary":
            skeleton[f] = contract.get(f) if isinstance(contract.get(f), list) else []
        else:
            skeleton[f] = contract.get(f) if f in contract else None
    if "deliverable" not in fields:
        skeleton["deliverable"] = {"content": None, "artifact_paths": {}, "format_spec_applied": None}
    if "fact_source_map" not in fields:
        skeleton["fact_source_map"] = []
    if "version_boundary" not in fields:
        skeleton["version_boundary"] = []
    return skeleton


def merge_result(path: str, updates: dict, action: str, detail: str = "") -> dict:
    """读入现有 result.json，浅层合并 updates，追加 change_log 后写回。"""
    existing, _deg = load_json_tolerant(path)
    if not isinstance(existing, dict):
        existing = {}
    for key, value in updates.items():
        existing[key] = value
    log = existing.get("change_log")
    if not isinstance(log, list):
        log = []
    log.append({"ts": utcnow_iso(), "action": action, "detail": detail})
    existing["change_log"] = log
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return existing


def write_json(path: str, obj: object) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="模板规格反推 + 内容单元盘点 + 骨架落盘")
    ap.add_argument("--input", default="/input", help="输入目录")
    ap.add_argument("--out", default="/app/output", help="输出目录")
    ap.add_argument("--work", default="/app/work", help="工作目录")
    ap.add_argument("--skeleton-only", action="store_true", help="只落盘骨架")
    args = ap.parse_args(argv)

    for d in (args.out, args.work):
        os.makedirs(d, exist_ok=True)

    disc = discover_input(args.input)
    case_obj, case_degs = (None, [])
    if disc.get("case"):
        case_obj, case_degs = load_json_tolerant(disc["case"])

    case_id = None
    output_contract = None
    if isinstance(case_obj, dict):
        case_id = case_obj.get("case_id") or case_obj.get("id") or case_obj.get("caseId")
        output_contract = case_obj.get("output_contract")

    result_path = os.path.join(args.out, "result.json")
    skeleton = build_skeleton(case_id, output_contract)
    skeleton["degradations"] = disc.get("degradations", []) + case_degs
    skeleton["discovered"] = disc.get("files", [])
    write_json(result_path, skeleton)

    if args.skeleton_only:
        print(json.dumps({"ok": True, "case_id": case_id, "status": "partial",
                          "skeleton": result_path}, ensure_ascii=False))
        return 0

    spec_checklist = {"template_specs": [], "inventories": [], "checklist": []}
    for f in disc.get("files", []):
        kind = f["kind"]
        if kind == "docx":
            spec = extract_docx_spec(f["path"])
            inv = inventory_docx(f["path"])
            spec_checklist["template_specs"].append(
                {"path": f["path"], "name": f["name"], "spec": spec})
            spec_checklist["inventories"].append(
                {"path": f["path"], "name": f["name"], "inventory": inv})
            spec_checklist["checklist"].append(build_checklist(spec, inv))
        elif kind == "tex":
            spec = extract_tex_spec(f["path"])
            inv = inventory_tex(f["path"])
            spec_checklist["template_specs"].append(
                {"path": f["path"], "name": f["name"], "spec": spec})
            spec_checklist["inventories"].append(
                {"path": f["path"], "name": f["name"], "inventory": inv})
            spec_checklist["checklist"].append(build_checklist(spec, inv))
        elif kind in ("text", "office"):
            inv = inventory_file(f["path"], kind)
            spec_checklist["inventories"].append(
                {"path": f["path"], "name": f["name"], "inventory": inv})

    work_plan = os.path.join(args.work, "work_plan.json")
    write_json(work_plan, {"case_id": case_id, "input": disc["files"],
                           "spec_checklist": spec_checklist,
                           "generated": utcnow_iso()})
    merge_result(result_path, {"work_plan_path": work_plan,
                               "spec_checklist_path": os.path.join(args.work, "work_plan.json")},
                 "inventory", "模板规格/内容单元盘点完成")
    print(json.dumps({"ok": True, "case_id": case_id, "status": "partial",
                      "work_plan": work_plan, "result": result_path},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
