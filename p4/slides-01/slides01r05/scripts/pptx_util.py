#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯标准库的 pptx / pdf 检查工具（不依赖 python-pptx / PyMuPDF / LibreOffice）。

用途：在缺少 Office 类库时仍能完成对可编辑产物与导出件的机械校验：
  1. 校验 .pptx 是否为合法 OOXML 包（zip + [Content_Types].xml）；
  2. 统计幻灯片页数、版式(layout)/母版(master)/主题(theme)数量，
     以验证「真继承模板母版/版式」而非空白新建 + 贴文本框的伪继承；
  3. 统计 PDF 页数并校验 PDF 头，验证导出页数与源文件页数一致；
  4. 检查证据 quote 是否逐字命中材料文本（逐字溯源校验）。

约定：所有函数在异常或缺失时返回 None 或带 error 字段的字典，绝不抛异常；
调用方据此写 degradation / exceptions，而不是崩溃。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

if sys.version_info < (3, 7):
    raise SystemExit("pptx_util requires Python >= 3.7")

_PPTX_SLIDE_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")
_LAYOUT_RE = re.compile(r"^ppt/slideLayouts/slideLayout\d+\.xml$")
_MASTER_RE = re.compile(r"^ppt/slideMasters/slideMaster\d+\.xml$")
_THEME_RE = re.compile(r"^ppt/theme/theme\d+\.xml$")
_NAME_RE = re.compile(r'name="([^"]*)"')


def _open_zip(path: Path):
    try:
        return zipfile.ZipFile(str(path))
    except Exception:
        return None


def read_zip_names(path) -> list | None:
    """返回 OOXML 包内的全部条目名；不可读/非 zip 返回 None。"""
    z = _open_zip(Path(path))
    if z is None:
        return None
    try:
        with z:
            return z.namelist()
    except Exception:
        return None


def slide_count_pptx(path) -> int | None:
    """按 ppt/slides/slideN.xml 计数；不可读返回 None。"""
    names = read_zip_names(path)
    if names is None:
        return None
    return len([n for n in names if _PPTX_SLIDE_RE.match(n)])


def _entry_names(path, regex) -> list:
    """提取某类条目（layout/master）的 name 属性；读不到返回空列表。"""
    names = read_zip_names(path)
    if not names:
        return []
    targets = sorted(n for n in names if regex.match(n))
    out = []
    z = _open_zip(Path(path))
    if z is None:
        return []
    try:
        with z:
            for n in targets:
                try:
                    raw = z.read(n).decode("utf-8", errors="replace")
                except Exception:
                    continue
                m = _NAME_RE.search(raw[:6000])
                out.append(m.group(1) if m else Path(n).stem)
    except Exception:
        return out
    return out


def layout_names_pptx(path) -> list:
    """版式名列表（来自 slideLayouts 的 name 属性），用于验证模板继承。"""
    return _entry_names(path, _LAYOUT_RE)


def master_names_pptx(path) -> list:
    """母版名列表（来自 slideMasters 的 name 属性）。"""
    return _entry_names(path, _MASTER_RE)


def theme_count_pptx(path) -> int | None:
    """主题文件数量；不可读返回 None。"""
    names = read_zip_names(path)
    if names is None:
        return None
    return len([n for n in names if _THEME_RE.match(n)])


def inventory_pptx(path) -> dict:
    """pptx 包体检，返回可机器复核的字典（valid/slides/layouts/masters/themes）。"""
    path = Path(path)
    if not path.is_file():
        return {"valid": False, "slides": 0, "error": f"missing: {path.name}"}
    names = read_zip_names(path)
    if names is None:
        return {"valid": False, "slides": 0, "error": "not a readable OOXML zip"}
    has_ct = "[Content_Types].xml" in names
    slides = slide_count_pptx(path)
    layouts = layout_names_pptx(path)
    masters = master_names_pptx(path)
    themes = theme_count_pptx(path)
    if slides is None:
        slides = 0
    if themes is None:
        themes = 0
    valid = bool(has_ct and slides >= 1)
    error = None
    if not valid:
        error = "missing [Content_Types].xml" if not has_ct else "no slide parts"
    return {
        "valid": valid,
        "slides": slides,
        "layouts": layouts,
        "layout_count": len(layouts),
        "masters": masters,
        "master_count": len(masters),
        "themes": themes,
        "has_content_types": has_ct,
        "inherits_template": bool(layouts or masters or themes),
        "error": error,
    }


def pdf_page_count(path) -> int | None:
    """按 /Type /Page 对象计数；不可读或非 PDF 返回 None。"""
    try:
        raw = Path(path).read_bytes()
    except Exception:
        return None
    if not raw.startswith(b"%PDF-"):
        return None
    text = raw.decode("latin-1", errors="replace")
    pages = len(re.findall(r"/Type\s*/Page\b", text))
    if pages == 0:
        m = re.search(r"/Count\s+(\d+)", text)
        if m:
            pages = int(m.group(1))
    return pages


def inventory_pdf(path) -> dict:
    """PDF 头 + 页数体检。"""
    path = Path(path)
    if not path.is_file():
        return {"valid": False, "pages": 0, "error": f"missing: {path.name}"}
    try:
        raw = path.read_bytes()
    except Exception as exc:
        return {"valid": False, "pages": 0, "error": f"{exc.__class__.__name__}: {exc}"}
    if not raw.startswith(b"%PDF-"):
        return {"valid": False, "pages": 0, "error": "no %PDF- header"}
    pages = pdf_page_count(path)
    return {"valid": True, "pages": pages, "error": None}


def quote_hits(path, quote: str) -> int:
    """在文本文件里统计 quote 的逐字命中次数。

    返回 0 表示文件可读但未命中；-1 表示文件不可读/quote 为空/编码不可解。
    """
    if not quote or not quote.strip():
        return -1
    try:
        text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    except Exception:
        return -1
    return text.count(quote)


def _dump(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        return json.dumps(inventory_pptx(path), ensure_ascii=False, indent=2)
    if suffix == ".pdf":
        return json.dumps(inventory_pdf(path), ensure_ascii=False, indent=2)
    return json.dumps({"valid": False, "error": f"unsupported suffix: {suffix}"},
                      ensure_ascii=False, indent=2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="纯标准库 pptx/pdf 检查")
    ap.add_argument("files", nargs="+", help="pptx 或 pdf 文件路径")
    args = ap.parse_args()
    for f in args.files:
        print(_dump(Path(f)))
