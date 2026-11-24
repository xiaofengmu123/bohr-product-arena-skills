#!/usr/bin/env python3
"""Shared helpers for sci-figure-workflow (Python standard library only).

Design goals: deterministic, side-effect-safe except for the atomic JSON
writer, and honest about degraded inputs. No third-party dependencies.
"""
import datetime
import json
import os
import struct
import xml.etree.ElementTree as ET

MAX_MATERIAL_BYTES = 2 * 1024 * 1024  # 2 MB per material file; skip + record beyond


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_MATERIAL_BYTES):
    """Read a text file with BOM/bad-encoding tolerance; returns (text, err)."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "unreadable: %s" % exc
    if size > max_bytes:
        return None, "oversize: %d bytes > %d" % (size, max_bytes)
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except (UnicodeDecodeError, ValueError) as exc:
            last_err = exc
    return None, "undecodable: %s" % last_err


def read_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, "bad json: %s" % exc


def write_json(path, obj):
    """Atomically write JSON (utf-8, indent 2, ensure_ascii=False)."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def discover_input(input_dir):
    """Walk input_dir without assuming fixed names. Returns (files, skipped)."""
    files, skipped = [], []
    if not os.path.isdir(input_dir):
        return files, [{"path": input_dir, "reason": "directory missing"}]
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = sorted(dirs)
        for name in sorted(names):
            abs_path = os.path.join(root, name)
            if not os.path.isfile(abs_path):
                continue
            rel = os.path.relpath(abs_path, input_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(abs_path)
            except OSError as exc:
                skipped.append({"path": rel, "reason": "unreadable: %s" % exc})
                continue
            if size > MAX_MATERIAL_BYTES:
                skipped.append({"path": rel, "reason": "oversize %d > %d" % (size, MAX_MATERIAL_BYTES)})
                continue
            files.append({"path": rel, "abs": abs_path, "size": size})
    return files, skipped


def allowed_statuses():
    return {"complete", "partial", "insufficient_evidence", "failed"}


def svg_check(path):
    """Return (ok, detail, features) for an SVG file; features has text/group counts."""
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        tag = root.tag.split("}")[-1].lower()
        if tag != "svg":
            return False, "root=<%s> not svg" % root.tag, None
        ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
        texts = len(list(root.iter(ns + "text")))
        groups = len(list(root.iter(ns + "g")))
        return True, "svg ok", {"text_elements": texts, "group_elements": groups}
    except Exception as exc:
        return False, "xml error: %s" % exc, None


def pdf_check(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(5)
        return head == b"%PDF-", "header=%r" % head
    except OSError as exc:
        return False, "unreadable: %s" % exc


def png_check(path):
    """Return (ok, detail) and (width, height) parsed from the IHDR chunk."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(33)
    except OSError as exc:
        return False, "unreadable: %s" % exc, (None, None)
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return False, "bad png signature", (None, None)
    width, height = struct.unpack(">II", data[16:24])
    return True, "png ok", (width, height)
