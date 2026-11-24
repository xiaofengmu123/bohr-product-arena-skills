#!/usr/bin/env python3
"""build.py — slides-01 渲染层：把 Deck IR 渲染为可编辑 pptx 或 SVG+PNG+PDF 降级产物。

与 core.py 解耦：本文件不负责内容组织/校验，只负责把已验证的 IR 变成文件。
- pptx 路径：加载给定模板的 master/layout 后 add_slide(layout)，真继承主题，
  只在占位符内填文字；讲解备注写入 notes_slide（与页面文字分离）。
- 导出：优先 soffice 转 PDF；否则手写最小合法 PDF（纯文本预览，如实降级）。
- 无 python-pptx：SVG + 可选 PNG + 最小 PDF，如实写入 degradation。
纯 Python 标准库；python-pptx / matplotlib / soffice 均为“有则用、无则降级”。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from core import write_text, ensure_dir


def _slide_lines(s) -> list:
    """每页展示文本：claim 作标题，bullets 作要点。"""
    return [str(s.get("claim") or "")] + [str(b) for b in (s.get("bullets") or [])]


# ---------------- pptx（真模板继承） ----------------

def render_pptx(ir, template_path, out_path) -> tuple:
    """用模板 master/layout 渲染 IR；返回 (ok, info)。"""
    try:
        from pptx import Presentation
    except ImportError:
        return False, "python-pptx 不可用（office-renderer 未安装）"
    except Exception as exc:  # 极少见：装坏/版本不兼容
        return False, f"python-pptx 加载失败: {exc.__class__.__name__}"

    slides = ir.get("slides") or []
    meta = ir.get("meta") or {}
    tpl = Path(template_path) if template_path else None

    try:
        if tpl and tpl.exists():
            prs = Presentation(str(tpl))
            inherited = True
        else:
            prs = Presentation()
            inherited = False
    except Exception as exc:
        return False, f"模板打开失败: {exc.__class__.__name__}: {exc}"

    layouts = prs.slide_layouts  # 来自模板 master，保证主题/母版继承
    n_layouts = len(layouts)

    def pick_layout(role):
        # 0=标题版式；1=标题+内容；无内容版式时回退 0
        if role == "title" and n_layouts > 0:
            return layouts[0]
        if n_layouts > 1:
            return layouts[1]
        return layouts[0]

    for s in slides:
        try:
            slide = prs.slides.add_slide(pick_layout(s.get("role")))
        except Exception:
            # 某些模板版式不允许再添加，回退第一版式
            slide = prs.slides.add_slide(layouts[0])

        # 只填占位符，不新建文本框：这才是“真继承”而非“空白+贴字”
        ph = _placeholder_by(slide, "title") or _placeholder_by(slide, "body")
        if ph is not None:
            ph.text = str(s.get("claim") or "")
            # 标题占位符只放 claim；正文占位符追加 bullets
            if ph.placeholder_format.type == 2 or _is_body(ph):
                for b in (s.get("bullets") or []):
                    p = ph.text_frame.add_paragraph()
                    p.text = str(b)

        # 讲解备注与页面文字分离
        notes = s.get("notes") or {}
        try:
            nf = slide.notes_slide.notes_text_frame
            nf.text = "\n".join(filter(None, [
                notes.get("transition", ""),
                "强调: " + notes.get("emphasis", "") if notes.get("emphasis") else "",
                "备问: " + notes.get("q_backup", "") if notes.get("q_backup") else "",
            ]))
        except Exception:
            pass

    try:
        prs.save(str(out_path))
    except Exception as exc:
        return False, f"pptx 保存失败: {exc.__class__.__name__}: {exc}"

    info = {
        "slides": len(slides),
        "template_inherited": inherited,
        "template": str(tpl) if tpl else None,
        "layout_count": n_layouts,
    }
    return True, info


def _placeholder_by(slide, name):
    for ph in slide.placeholders:
        try:
            if ph.placeholder_format.type == 1 and name == "title":
                return ph
            if ph.placeholder_format.type == 2 and name == "body":
                return ph
        except Exception:
            continue
    return None


def _is_body(ph) -> bool:
    try:
        return ph.placeholder_format.type == 2
    except Exception:
        return False


def export_pdf_soffice(pptx_path, out_path) -> tuple:
    """用 LibreOffice headless 把 pptx 转 PDF。返回 (ok, info|err)。"""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return False, "soffice 不可用"
    out_dir = Path(out_path).parent
    try:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", str(out_dir), str(pptx_path)],
            capture_output=True, timeout=240)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"soffice 转换失败: {exc.__class__.__name__}"
    stderr_txt = (proc.stderr or b"").decode("utf-8", errors="replace")[:200]
    if proc.returncode != 0:
        return False, f"soffice 转换失败(rc={proc.returncode}): {stderr_txt}"
    pdf = out_dir / (Path(pptx_path).stem + ".pdf")
    if out_path and str(pdf) != str(out_path):
        try:
            shutil.move(str(pdf), str(out_path))
            pdf = Path(out_path)
        except OSError:
            pass
    if pdf.exists():
        return True, {"pages": pdf_page_count(pdf), "path": str(pdf)}
    return False, "soffice 未产出 PDF"


# ---------------- SVG / PNG 降级 ----------------

def render_svg(ir, out_dir) -> tuple:
    """每页一个独立 SVG（纯标准库）。返回 (paths, err)。"""
    out_dir = Path(out_dir)
    ok, err = ensure_dir(out_dir)
    if not ok:
        return [], err
    paths = []
    W, H = 960, 540
    for s in (ir or {}).get("slides", []):
        idx = s.get("index", 0)
        claim = _xml_escape(str(s.get("claim") or ""))
        bullets = [_xml_escape(str(b)) for b in (s.get("bullets") or [])]
        body = "".join(
            f'<text x="60" y="{120 + i * 34}" font-family="DejaVu Sans, sans-serif" '
            f'font-size="20" fill="#333333">{b}</text>\n'
            for i, b in enumerate(bullets[:10]))
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}">\n'
            f'<rect width="{W}" height="{H}" fill="#ffffff"/>\n'
            f'<rect x="0" y="0" width="{W}" height="64" fill="#1f4e79"/>\n'
            f'<text x="40" y="42" font-family="DejaVu Sans, sans-serif" '
            f'font-size="26" font-weight="bold" fill="#ffffff">{claim}</text>\n'
            f"{body}"
            f'<text x="40" y="{H-24}" font-family="DejaVu Sans, sans-serif" '
            f'font-size="13" fill="#888888">slide {idx} · {_xml_escape(str(s.get("role") or ""))}</text>\n'
            f"</svg>\n")
        p = out_dir / f"slide-{int(idx):02d}.svg"
        if not write_text(p, svg):
            return paths, "SVG 写盘失败"
        paths.append(str(p))
    return paths, None


def _xml_escape(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_pngs(ir, out_dir) -> tuple:
    """有 matplotlib 时给每页 PNG 预览；无则降级。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return [], "matplotlib 不可用（无 PNG 预览）"
    out_dir = Path(out_dir)
    ok, err = ensure_dir(out_dir)
    if not ok:
        return [], err
    paths = []
    for s in (ir or {}).get("slides", []):
        idx = s.get("index", 0)
        fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        ax.text(0.05, 0.92, str(s.get("claim") or ""), fontsize=18,
                weight="bold", va="top")
        for j, b in enumerate((s.get("bullets") or [])[:10]):
            ax.text(0.05, 0.85 - j * 0.055, "• " + str(b), fontsize=13, va="top")
        p = out_dir / f"slide-{int(idx):02d}.png"
        try:
            fig.savefig(p, dpi=100, bbox_inches="tight")
            paths.append(str(p))
        finally:
            plt.close(fig)
    return paths, None


# ---------------- 最小合法 PDF（纯文本预览） ----------------

def write_minimal_pdf(ir, out_path) -> tuple:
    """手写多页 PDF：每页含 claim+bullets 文本，页数与 slides 一致。

    仅作“真实 PDF 预览/页数核验”用途；版面为纯文本，非矢量渲染。
    返回 (ok, pages|err)。
    """
    slides = ir.get("slides") or []
    if not slides:
        return False, "slides 为空，无法生成 PDF"

    def esc(t):
        return (str(t).replace("\\", r"\\").replace("(", r"\(")
                .replace(")", r"\)"))

    def ascii_only(t):
        # Helvetica 仅覆盖 ASCII，非 ASCII 转占位，避免字体崩坏
        return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(t))

    objects = []  # 每项 (obj_num, body_bytes)
    # 固定对象 1..4
    objects.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    kids = []
    page_obj_nums = []
    for i, s in enumerate(slides):
        page_obj = 5 + 2 * i
        content_obj = 6 + 2 * i
        page_obj_nums.append(page_obj)
        kids.append(f"{page_obj} 0 R".encode())
        title = ascii_only(s.get("claim") or "")
        lines = [ascii_only(b) for b in (s.get("bullets") or [])]
        stream = _content_stream(esc, title, lines)
        objects.append((page_obj,
                        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                         f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                         f"/Contents {content_obj} 0 R >>").encode()))
        objects.append((content_obj,
                        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                        + stream + b"endstream"))

    pages_obj = (b"<< /Type /Pages /Kids [" + b" ".join(kids)
                 + b"] /Count " + str(len(slides)).encode() + b" >>")
    objects.insert(1, (2, pages_obj))
    objects.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"))
    objects.append((4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))

    # 按对象号排序
    objects.sort(key=lambda x: x[0])
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for num, obj in objects:
        offsets.append(len(body))
        body += f"{num} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(body)
    count = len(objects) + 1
    body += f"xref\n0 {count}\n".encode()
    body += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        body += f"{off:010d} 00000 n \n".encode()
    body += (f"trailer\n<< /Size {count} /Root 1 0 R >>\n"
             f"startxref\n{xref_pos}\n%%EOF\n").encode()

    try:
        Path(out_path).write_bytes(bytes(body))
    except OSError as exc:
        return False, f"PDF 写盘失败: {exc.__class__.__name__}"
    return True, len(slides)


def _content_stream(esc, title, lines) -> bytes:
    parts = ["BT", "/F1 18 Tf", "56 720 Td", f"({esc(title[:90])}) Tj", "ET"]
    y = 690
    for line in lines[:18]:
        parts += ["BT", "/F2 11 Tf", f"56 {y} Td", f"({esc(line[:95])}) Tj", "ET"]
        y -= 22
    return ("\n".join(parts) + "\n").encode("latin-1", errors="replace")


# ---------------- 页数校验 ----------------

def pdf_page_count(path) -> int:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return 0
    return len(re.findall(rb"/Type\s*/Page(?!s)", data))
