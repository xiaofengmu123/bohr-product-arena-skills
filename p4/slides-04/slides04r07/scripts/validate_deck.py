#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-robust-adapt 渲染与文件校验（仅 Python 标准库）。

对交付物做确定性/启发式检查：
  - pptx：合法 zip、核心部件、页数、母版/版式/占位符继承链、备注、媒体清单、
    文本溢出启发式；
  - pdf ：%PDF- 头、页数（尽力统计）；
  - png ：PNG 魔数、IHDR 尺寸。

方法边界（写入 SKILL.md 与 checklist.md）：
  本脚本只能做**确定性/启发式**检查。"版式是否美观、字号是否可读、图文是否
  语义一致"必须由人工或模型核对；脚本只产出可疑项清单，不下结论。
  缺文件一律记 failed 而非抛异常；退出码 0（参数错误除外，为 2）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile


def _read_zip_text(z, name: str):
    try:
        return z.read(name).decode("utf-8", "replace")
    except KeyError:
        return None


def _find_rel_target(z, rels_name: str, rel_kind: str):
    """从 .rels 文件里找 Type 含 rel_kind 的第一个 Relationship 的 Target。"""
    data = _read_zip_text(z, rels_name)
    if not data:
        return None
    for m in re.finditer(r"<Relationship[^>]*/?>", data):
        tag = m.group(0)
        if "/{}".format(rel_kind) in tag:
            tm = re.search(r'Target="([^"]+)"', tag)
            if tm:
                return tm.group(1).rsplit("/", 1)[-1]
    return None


def pptx_report(path: str):
    r = {"path": path, "exists": False, "is_zip": False,
         "errors": [], "warnings": []}
    if not os.path.isfile(path):
        r["errors"].append("文件不存在")
        return r
    r["exists"] = True
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            r["is_zip"] = True
            r["has_content_types"] = "[Content_Types].xml" in names
            slides = sorted(n for n in names
                            if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
            r["slide_count"] = len(slides)
            r["masters"] = sorted(n for n in names
                                  if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n))
            r["layouts"] = sorted(n for n in names
                                  if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n))
            r["notes"] = sorted(n for n in names
                                if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n))
            r["media"] = []
            for n in names:
                if n.startswith("ppt/media/"):
                    try:
                        info = z.getinfo(n)
                        r["media"].append({"name": n, "bytes": info.file_size})
                    except KeyError:
                        pass
            r["slide_layout_chain"] = []
            for sn in slides:
                slide = sn.rsplit("/", 1)[-1][:-4]  # slideN
                rels = "ppt/slides/_rels/{}.xml.rels".format(slide)
                layout = _find_rel_target(z, rels, "slideLayout") if rels in names else None
                master = None
                if layout:
                    lrels = "ppt/slideLayouts/_rels/{}.xml.rels".format(layout[:-4])
                    if lrels in names:
                        master = _find_rel_target(z, lrels, "slideMaster")
                r["slide_layout_chain"].append(
                    {"slide": slide, "layout": layout, "master": master})
            r["text_flags"] = _text_heuristics(z, slides)
    except zipfile.BadZipFile as exc:
        r["is_zip"] = False
        r["errors"].append("非法 zip: {}".format(exc))
    return r


def _text_heuristics(z, slides):
    """启发式：单段长文本/单页密集文本，仅标记可疑，不判对错。"""
    flags = []
    for sn in slides:
        data = _read_zip_text(z, sn) or ""
        runs = re.findall(r"<a:t>(.*?)</a:t>", data, flags=re.S)
        total = sum(len(x) for x in runs)
        long_runs = [x for x in runs if len(x) > 300]
        if long_runs:
            flags.append({"slide": sn, "kind": "long_text_run",
                          "detail": "{} 段 >300 字符（可能溢出，需人工核）".format(len(long_runs))})
        if total > 2000:
            flags.append({"slide": sn, "kind": "dense_slide",
                          "detail": "单页文本 {} 字符，疑似整段粘贴，需人工核".format(total)})
    return flags


def pdf_report(path: str):
    r = {"path": path, "exists": False, "is_pdf": False, "pages": None,
         "errors": []}
    if not os.path.isfile(path):
        r["errors"].append("文件不存在")
        return r
    r["exists"] = True
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError as exc:
        r["errors"].append("读取失败: {}".format(exc))
        return r
    r["is_pdf"] = head.startswith(b"%PDF-")
    if r["is_pdf"]:
        r["pages"] = _count_pdf_pages(path)
    return r


def _count_pdf_pages(path: str):
    """尽力统计 /Type /Page（对象流内页可能漏计，记 uncertainty 由调用方标注）。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        return len(re.findall(rb"/Type\s*/Page[^s]", data))
    except OSError:
        return None


def png_report(path: str):
    r = {"path": path, "exists": False, "is_png": False,
         "width": None, "height": None, "errors": []}
    if not os.path.isfile(path):
        r["errors"].append("文件不存在")
        return r
    r["exists"] = True
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        r["errors"].append("读取失败: {}".format(exc))
        return r
    r["is_png"] = head[:8] == b"\x89PNG\r\n\x1a\n"
    if r["is_png"]:
        try:
            with open(path, "rb") as fh:
                fh.read(16)
                r["width"] = int.from_bytes(fh.read(4), "big")
                r["height"] = int.from_bytes(fh.read(4), "big")
        except (OSError, ValueError):
            pass
    return r


def build_report(args):
    report = {"pptx": None, "pdf": None, "png": [], "consistency": {}}
    if args.pptx:
        report["pptx"] = pptx_report(args.pptx)
    if args.pdf:
        report["pdf"] = pdf_report(args.pdf)
    for png in args.png or []:
        report["png"].append(png_report(png))

    # 一致性：pptx 页数 vs pdf 页数 vs png 数量
    pptx_n = report["pptx"].get("slide_count") if report.get("pptx") else None
    pdf_n = report["pdf"].get("pages") if report.get("pdf") else None
    png_n = len(report["png"])
    report["consistency"] = {
        "pptx_slides": pptx_n,
        "pdf_pages": pdf_n,
        "png_count": png_n,
        "pptx_pdf_match": (pptx_n == pdf_n) if (pptx_n is not None and pdf_n is not None) else None,
        "pptx_png_match": (pptx_n == png_n) if pptx_n is not None else None,
    }
    return report


def main() -> int:
    p = argparse.ArgumentParser(
        prog="validate_deck.py",
        description="确定性校验 pptx/pdf/png 及母版版式继承链，产出 render_validation.json")
    p.add_argument("--pptx", default=None, help="pptx 路径")
    p.add_argument("--pdf", default=None, help="导出 PDF 路径")
    p.add_argument("--png", nargs="*", default=None, help="导出 PNG 路径列表")
    p.add_argument("--output", default="/app/output", help="报告输出目录")
    args = p.parse_args()

    report = build_report(args)
    out_path = os.path.join(args.output, "render_validation.json")
    directory = os.path.dirname(out_path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print("[validate_deck] 报告写入失败: {}".format(exc), file=sys.stderr)
        return 3

    # 打印摘要，便于 Agent 读取
    pptx = report.get("pptx") or {}
    pdf = report.get("pdf") or {}
    print("[validate_deck] pptx_slides={} pptx_zip={} pdf={} pdf_pages={} png={} -> {}".format(
        pptx.get("slide_count"), pptx.get("is_zip"),
        pdf.get("is_pdf"), pdf.get("pages"), len(report["png"]), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
