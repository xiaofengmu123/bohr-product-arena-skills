#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_deck.py — pptx/PDF/PNG 结构与一致性确定性校验。

只依赖 Python 标准库。产出 render_record 并可选合并回 result.json 的
render_record 字段；同时校验 evidence_map 的“locator + 逐字 quote”契约。

检查项（可自动测的边界）：
  * pptx 是合法 zip 且 testzip() 通过（integrity）；
  * 页数、母版/版式/备注的 rels 继承关系可查；
  * 媒体文件清单与尺寸；
  * 字体 typeface 收集（slide XML + theme XML）；
  * 文本框溢出启发式（字符宽估算 vs 版心，仅提示，不替代真实渲染）；
  * PDF 以 %PDF- 开头；PNG 为合法 PNG 并读 IHDR 尺寸；
  * evidence_map 每条含 locator + 非空 verbatim quote（证据溯源硬要求）。

只能人工核验的项（本脚本不冒充）：排版美观度、图号与正文引用一致性、
字体真实回退效果、图表拉伸变形 —— 这些写入 checklist，由 Agent 逐页人工复核。

退出码：0=通过；2=发现硬性问题（仍写记录）或参数错误（argparse 默认）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import zipfile
from statistics import median

# 文本宽估算系数（pt/字符）：ASCII 约 0.5，CJK 约 1.0；仅启发式。
ASCII_WIDTH_FACTOR = 0.5
CJK_WIDTH_FACTOR = 1.0
OVERFLOW_RATIO = 0.95  # 单段估算宽度超过版心该比例即提示

RE_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
RE_LAYOUT_REF = re.compile(r"slideLayout(\d+)\.xml")
RE_MASTER_REF = re.compile(r"slideMaster(\d+)\.xml")
RE_NOTES_REF = re.compile(r"notesSlide(\d+)\.xml")
RE_LAYOUT_NAME = re.compile(r'<(?:\w+:)?cSld[^>]*\bname="([^"]*)"')
RE_TYPEFACE = re.compile(r'<(?:\w+:)?(?:latin|ea|cs)[^>]*typeface="([^"]+)"')
RE_SLDSZ = re.compile(r"<p:sldSz[^>]*/>")
RE_ATTR = re.compile(r'\b(cx|cy)="(\d+)"')
RE_SZ = re.compile(r'\bsz="(\d+)"')
RE_TEXT = re.compile(r"<a:t>(.*?)</a:t>", re.S)


def read_zip_text(zf, name):
    try:
        data = zf.read(name)
    except KeyError:
        return None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def natural_sort_key(s):
    return int(s) if s.isdigit() else s


def check_pptx(pptx_path):
    """返回 (record, issues)。record 含页数/版式/字体/媒体/溢出启发式。"""
    record = {
        "path": pptx_path,
        "slide_count": 0,
        "slide_masters": [],
        "layouts_used": [],
        "notes_slides": [],
        "media": [],
        "fonts": [],
        "overflow_candidates": [],
    }
    issues = []
    if not pptx_path or not os.path.isfile(pptx_path):
        issues.append({"code": "pptx_missing", "detail": str(pptx_path)})
        return record, issues
    if not zipfile.is_zipfile(pptx_path):
        issues.append({"code": "pptx_not_zip", "detail": str(pptx_path)})
        return record, issues

    try:
        zf = zipfile.ZipFile(pptx_path)
    except zipfile.BadZipFile as exc:
        issues.append({"code": "pptx_bad_zip", "detail": str(exc)})
        return record, issues

    with zf:
        bad = zf.testzip()
        if bad is not None:
            issues.append({"code": "pptx_zip_corrupt_member", "detail": bad})

        names = zf.namelist()
        slide_nums = []
        for n in names:
            m = RE_SLIDE.match(n)
            if m:
                slide_nums.append(int(m.group(1)))
        record["slide_count"] = len(slide_nums)
        slide_nums.sort()

        # 母版（presentation rels）
        pres_rels = read_zip_text(zf, "ppt/_rels/presentation.xml.rels")
        if pres_rels:
            record["slide_masters"] = sorted(set(int(x) for x in RE_MASTER_REF.findall(pres_rels)))

        # 版式继承：逐页 rels -> slideLayoutN；再从 layout XML 取显示名。
        layout_names = {}
        for ln in range(1, 30):
            lxml = read_zip_text(zf, "ppt/slideLayouts/slideLayout%d.xml" % ln)
            if lxml:
                m = RE_LAYOUT_NAME.search(lxml)
                layout_names[ln] = m.group(1) if m else "slideLayout%d" % ln

        used = {}
        for sn in slide_nums:
            rels = read_zip_text(zf, "ppt/slides/_rels/slide%d.xml.rels" % sn)
            if rels:
                for ln in sorted(set(int(x) for x in RE_LAYOUT_REF.findall(rels))):
                    used.setdefault(ln, {"layout_num": ln,
                                         "layout_name": layout_names.get(ln, "layout%d" % ln),
                                         "slides": []})
                    used[ln]["slides"].append(sn)
                note_nums = sorted(set(int(x) for x in RE_NOTES_REF.findall(rels)))
                if note_nums:
                    record["notes_slides"].extend(note_nums)
        record["layouts_used"] = [used[k] for k in sorted(used)]
        record["notes_slides"] = sorted(set(record["notes_slides"]))

        # 媒体
        for n in names:
            if n.startswith("ppt/media/"):
                try:
                    info = zf.getinfo(n)
                    record["media"].append({"name": n, "size": info.file_size})
                except KeyError:
                    continue
        record["media"].sort(key=lambda m: m["name"])

        # 字体：slide XML + theme XML
        typefaces = set()
        for n in names:
            if n.endswith(".xml") and ("slides/" in n or "theme" in n or "layouts/" in n):
                xml = read_zip_text(zf, n)
                if xml:
                    typefaces.update(RE_TYPEFACE.findall(xml))
        record["fonts"] = sorted(typefaces)

        # 版心尺寸 + 溢出启发式
        pres_xml = read_zip_text(zf, "ppt/presentation.xml")
        slide_w_pt = None
        if pres_xml:
            m = RE_SLDSZ.search(pres_xml)
            if m:
                attrs = dict(RE_ATTR.findall(m.group(0)))
                if "cx" in attrs:
                    slide_w_pt = int(attrs["cx"]) / 12700.0

        if slide_w_pt:
            limit = slide_w_pt * OVERFLOW_RATIO
            for sn in slide_nums:
                sxml = read_zip_text(zf, "ppt/slides/slide%d.xml" % sn)
                if not sxml:
                    continue
                sizes = [int(x) / 100.0 for x in RE_SZ.findall(sxml)]
                fpt = median(sizes) if sizes else 18.0
                for txt in RE_TEXT.findall(sxml):
                    txt = txt.strip()
                    if not txt:
                        continue
                    ascii_n = sum(1 for c in txt if ord(c) < 128)
                    cjk_n = len(txt) - ascii_n
                    est = ascii_n * fpt * ASCII_WIDTH_FACTOR + cjk_n * fpt * CJK_WIDTH_FACTOR
                    if est > limit:
                        record["overflow_candidates"].append({
                            "slide": sn,
                            "text": txt[:40],
                            "est_width_pt": round(est, 1),
                            "limit_pt": round(limit, 1),
                            "font_pt_median": fpt,
                        })
    return record, issues


def check_pdf(pdf_path):
    record = {"path": pdf_path, "valid": False, "detail": None}
    if not pdf_path or not os.path.isfile(pdf_path):
        record["detail"] = "missing"
        return record
    try:
        with open(pdf_path, "rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        record["detail"] = "unreadable:%s" % exc
        return record
    if head.startswith(b"%PDF-"):
        record["valid"] = True
    else:
        record["detail"] = "bad_magic"
    return record


def check_png(png_path):
    record = {"path": png_path, "valid": False, "width": None, "height": None,
              "detail": None}
    if not png_path or not os.path.isfile(png_path):
        record["detail"] = "missing"
        return record
    try:
        with open(png_path, "rb") as fh:
            data = fh.read(33)
    except OSError as exc:
        record["detail"] = "unreadable:%s" % exc
        return record
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        record["valid"] = True
        record["width"], record["height"] = struct.unpack(">II", data[16:24])
    else:
        record["detail"] = "bad_magic"
    return record


def check_evidence(result):
    """evidence_map 每条必须含 page 与 refs；每条 ref 必须含 locator + 非空 quote。"""
    issues = []
    if not isinstance(result, dict):
        return issues
    em = result.get("evidence_map")
    if em is None:
        issues.append({"code": "evidence_map_missing", "detail": "契约字段不存在"})
        return issues
    if not isinstance(em, list):
        issues.append({"code": "evidence_map_not_list", "detail": type(em).__name__})
        return issues
    for i, entry in enumerate(em):
        if not isinstance(entry, dict):
            issues.append({"code": "evidence_entry_not_dict", "index": i})
            continue
        if not entry.get("page"):
            issues.append({"code": "evidence_page_missing", "index": i})
        refs = entry.get("refs")
        if not isinstance(refs, list) or not refs:
            issues.append({"code": "evidence_refs_empty", "index": i})
            continue
        for j, ref in enumerate(refs):
            if not isinstance(ref, dict):
                issues.append({"code": "evidence_ref_not_dict", "index": i, "ref": j})
                continue
            if not ref.get("locator"):
                issues.append({"code": "evidence_locator_missing", "index": i, "ref": j})
            if not ref.get("quote") or not str(ref.get("quote", "")).strip():
                issues.append({"code": "evidence_quote_empty", "index": i, "ref": j})
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser(description="pptx/PDF/PNG 结构确定性校验")
    parser.add_argument("--pptx", default=None, help="pptx 路径")
    parser.add_argument("--pdf", default=None, help="导出 PDF 路径")
    parser.add_argument("--png", action="append", default=[], help="导出 PNG 路径，可多次")
    parser.add_argument("--template", default=None, help="模板 pptx 路径（记入 render_record）")
    parser.add_argument("--result", default=None, help="result.json 路径（合并 render_record）")
    parser.add_argument("--output", default="/app/output", help="产物目录，默认 /app/output")
    parser.add_argument("--now", default="now", help="时间戳注入；'fixed' 为确定性自检哨兵")
    args = parser.parse_args(argv)

    if args.now == "fixed":
        now = "1970-01-01T00:00:00Z"
    elif args.now in (None, "now"):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        now = args.now

    pptx_rec, issues = check_pptx(args.pptx)
    pdf_rec = check_pdf(args.pdf)
    png_recs = [check_png(p) for p in args.png]

    exports = [{"kind": "pptx", "path": args.pptx, "status": "ok" if not issues else "fail"}]
    if args.pdf:
        exports.append({"kind": "pdf", "path": args.pdf,
                        "status": "ok" if pdf_rec["valid"] else "fail"})
    for r in png_recs:
        exports.append({"kind": "png", "path": r["path"],
                        "status": "ok" if r["valid"] else "fail"})

    if not pdf_rec["valid"] and args.pdf:
        issues.append({"code": "pdf_invalid", "detail": pdf_rec["detail"]})
    for r in png_recs:
        if not r["valid"]:
            issues.append({"code": "png_invalid", "detail": "%s:%s" % (r["path"], r["detail"])})

    source_files = []
    evidence_issues = []
    if args.result and os.path.isfile(args.result):
        try:
            with open(args.result, "r", encoding="utf-8-sig") as fh:
                result = json.load(fh)
        except (ValueError, OSError):
            result = {}
        src = result.get("sources") if isinstance(result, dict) else None
        if isinstance(src, dict) and isinstance(src.get("materials"), list):
            source_files = [m.get("path") for m in src["materials"] if m.get("path")]
        evidence_issues = check_evidence(result)

    render_record = {
        "source_files": source_files,
        "template_file": args.template,
        "layouts_used": pptx_rec["layouts_used"],
        "fonts": pptx_rec["fonts"],
        "exports": exports,
        "integrity": {
            "pptx_zip": "fail" if any(i["code"].startswith("pptx_") for i in issues) else "ok",
            "pdf_magic": "ok" if pdf_rec["valid"] else ("n/a" if not args.pdf else "fail"),
            "png_magic": "ok" if all(r["valid"] for r in png_recs) else ("n/a" if not png_recs else "fail"),
        },
        "media": pptx_rec["media"],
        "overflow_candidates": pptx_rec["overflow_candidates"],
        "evidence_issues": evidence_issues,
        "issues": issues,
        "generated_at": now,
    }

    os.makedirs(args.output, exist_ok=True)
    rr_path = os.path.join(args.output, "render_record.json")
    with open(rr_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(render_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    # 合并回 result.json 的 render_record 字段（幂等：只替换该字段）
    if args.result:
        try:
            with open(args.result, "r", encoding="utf-8-sig") as fh:
                result = json.load(fh)
        except (ValueError, OSError):
            result = {}
        if isinstance(result, dict):
            result["render_record"] = render_record
            with open(args.result, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    print("validate_deck: slides=%d layouts=%d fonts=%d hard_issues=%d evidence_issues=%d -> %s" % (
        pptx_rec["slide_count"], len(pptx_rec["layouts_used"]),
        len(pptx_rec["fonts"]), len(issues), len(evidence_issues), rr_path))
    return 2 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
