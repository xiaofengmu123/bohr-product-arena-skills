#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成可编辑稿件的确定性预览（纯标准库，不联网）。

从 /app/output/manuscript.md 生成：
  - manuscript.html：自包含 UTF-8 HTML 预览（标题/列表/表格/加粗/行内码/[S#] 原样保留）
  - manuscript.txt ：去除 Markdown 符号的纯文本预览

用法：
    python3 scripts/preview.py --input /app/output/manuscript.md --output /app/output

缺省路径同上。manuscript.md 缺失时退出码 1 并打印提示，不伪造内容；
HTML/文本预览是"可打开的预览"，若任务要求 Office/图产物，其真实 PDF/PNG
仍须由 Agent 用题面 office-renderer / 自绘工具生成（见 SKILL.md 第 3、8 节）。
"""
import argparse
import html
import os
import re
import sys

DEFAULTS = {"input": "/app/output/manuscript.md", "output": "/app/output"}


def render_inline(text):
    """行内标记：加粗/斜体/行内码/链接，输出为转义后的 HTML。"""
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def md_to_html(md_text):
    lines = md_text.splitlines()
    out = []
    in_list = False
    in_table = False
    table_rows = []
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("|") and line.strip().endswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue  # 分隔行
            table_rows.append(cells)
            if not in_table:
                in_table = True
            continue
        else:
            if in_table:
                out.append("<table>")
                for i, row in enumerate(table_rows):
                    tag = "th" if i == 0 else "td"
                    out.append("<tr>" + "".join(f"<{tag}>{render_inline(c)}</{tag}>" for c in row) + "</tr>")
                out.append("</table>")
                table_rows = []
                in_table = False

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(m.group(1))
            out.append(f"<h{level}>{render_inline(m.group(2))}</h{level}>")
            continue
        if re.match(r"^\s*[-*+]\s+", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            item = re.sub(r"^\s*[-*+]\s+", "", line)
            out.append(f"<li>{render_inline(item)}</li>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            if not in_list:
                out.append("<ol>")
                in_list = True
            item = re.sub(r"^\s*\d+\.\s+", "", line)
            out.append(f"<li>{render_inline(item)}</li>")
            continue
        if in_list:
            out.append("</ul>" if "</ul>" not in out[-1] else "")
            in_list = False
        if not line.strip():
            out.append("")
        else:
            out.append(f"<p>{render_inline(line)}</p>")
    if in_table:
        out.append("<table>")
        for i, row in enumerate(table_rows):
            tag = "th" if i == 0 else "td"
            out.append("<tr>" + "".join(f"<{tag}>{render_inline(c)}</{tag}>" for c in row) + "</tr>")
        out.append("</table>")
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    return (
        "<!DOCTYPE html>\n<html lang=\"zh\"><head><meta charset=\"utf-8\">"
        "<title>manuscript preview</title>"
        "<style>body{font-family:system-ui,'Segoe UI',sans-serif;max-width:860px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.6;color:#1a1a1a}"
        "table{border-collapse:collapse;margin:1rem 0}th,td{border:1px solid #ccc;padding:4px 8px;text-align:left}"
        "code{background:#f4f4f4;padding:1px 4px;border-radius:3px}"
        "h1,h2,h3{line-height:1.3}</style></head><body>\n" + body + "\n</body></html>\n"
    )


def md_to_txt(md_text):
    out = []
    for line in md_text.splitlines():
        s = line.rstrip()
        s = re.sub(r"^(#{1,6})\s+", "", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        s = re.sub(r"__([^_]+)__", r"\1", s)
        s = re.sub(r"\*([^*]+)\*", r"\1", s)
        s = re.sub(r"`([^`]+)`", r"\1", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", s)
        out.append(s)
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 manuscript 的 HTML/纯文本预览")
    ap.add_argument("--input", default=DEFAULTS["input"])
    ap.add_argument("--output", default=DEFAULTS["output"])
    args = ap.parse_args(argv)

    if not os.path.exists(args.input):
        print(json_like({"ok": False, "reason": "manuscript.md 不存在，跳过预览"}), file=sys.stderr)
        return 1
    try:
        with open(args.input, "r", encoding="utf-8-sig") as fh:
            md_text = fh.read()
    except OSError as exc:
        print(json_like({"ok": False, "reason": str(exc)}), file=sys.stderr)
        return 1

    os.makedirs(args.output, exist_ok=True)
    html_path = os.path.join(args.output, "manuscript.html")
    txt_path = os.path.join(args.output, "manuscript.txt")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(md_to_html(md_text))
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(md_to_txt(md_text))
    print(json_like({"ok": True, "html": html_path, "txt": txt_path}))
    return 0


def json_like(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
