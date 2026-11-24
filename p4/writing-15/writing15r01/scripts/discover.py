#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""输入发现与容错解析（仅 Python 标准库）。

职责：
1. 扫描输入目录，识别 case.json / task.md / organizer-edit-workorder.json 及科研材料；
2. 文本容错读取（BOM、坏编码、坏 JSON 降级，单文件大小上限）；
3. 输出稳定的结构化发现结果，供 main.py 与 Agent 复用。

本模块不生成学术正文、不虚构材料内容；解析失败只记录不崩溃。
"""

import json
import os

DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 单文本文件大小上限（2MB）
TEXT_EXTS = {
    ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".tsv",
    ".tex", ".bib", ".xml", ".html", ".rst", ".log", ".py",
}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}
# 关键文件名候选（按优先级，不假设唯一）
CASE_NAMES = ("case.json", "case_info.json", "input.json")
TASK_NAMES = ("task.md", "task.txt", "readme.md")
WORKORDER_NAMES = ("organizer-edit-workorder.json", "edit-workorder.json", "workorder.json")


def read_text_robust(path, max_bytes=DEFAULT_MAX_BYTES):
    """容错读取文本，返回 (text, encoding, errors)。超限返回 (None, None, [reason])。"""
    errors = []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, None, ["stat_failed:%s" % exc]
    if size > max_bytes:
        return None, None, ["size_exceeded:%d" % size]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, None, ["read_failed:%s" % exc]
    if raw.startswith(b"%PDF"):
        return None, None, ["binary_pdf_requires_pdf_tool"]
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), enc, errors
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", ["decoded_with_errors"]


def safe_json_load(path):
    """读取 JSON，失败时返回 (None, [error])；不抛异常。"""
    text, enc, errors = read_text_robust(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except (ValueError, TypeError) as exc:
        # 尝试去掉 BOM/首尾空白后再次解析
        try:
            return json.loads(text.lstrip("\ufeff").strip()), errors + ["json_retry_ok"]
        except (ValueError, TypeError):
            return None, errors + ["json_parse_failed:%s" % exc]


def _rel(path, root):
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return path


def scan_inputs(input_dir):
    """扫描输入目录，返回结构化发现结果。"""
    root = os.path.abspath(input_dir)
    report = {
        "input_dir": root,
        "case_json_path": None,
        "task_md_path": None,
        "workorder_path": None,
        "materials": [],
        "ignored": [],
        "errors": [],
    }
    if not os.path.isdir(root):
        report["errors"].append("input_dir_missing:%s" % root)
        return report

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = _rel(path, root)
            lower = name.lower()
            if lower in CASE_NAMES and report["case_json_path"] is None:
                report["case_json_path"] = path
                continue
            if lower in TASK_NAMES and report["task_md_path"] is None:
                report["task_md_path"] = path
                continue
            if lower in WORKORDER_NAMES and report["workorder_path"] is None:
                report["workorder_path"] = path
                continue
            ext = os.path.splitext(lower)[1]
            if ext in TEXT_EXTS:
                text, enc, errs = read_text_robust(path)
                item = {
                    "path": path,
                    "rel": rel,
                    "ext": ext,
                    "encoding": enc,
                    "errors": errs,
                    "size": os.path.getsize(path) if os.path.exists(path) else 0,
                }
                if text is not None:
                    item["text"] = text
                report["materials"].append(item)
            else:
                report["ignored"].append({"path": path, "rel": rel, "reason": "non_text_or_unsupported_ext"})
    return report
