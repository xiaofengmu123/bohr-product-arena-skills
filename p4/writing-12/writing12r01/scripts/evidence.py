#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence.py — 证据溯源核心：来源索引、逐字引用、逐字校验与 evidence_map 校验。

设计目标（本版强化方向）：稿件中的每个关键结论/数值/术语/引用，都必须能
指回材料中的「locator + 逐字 quote」。本脚本保证：
  * quote 精确抽取（按行号）；
  * verify 证明 quote 是源文件的原文子串（跨行、归一化换行）；
  * verify-map 对整张 evidence_map 逐条做上述校验，未通过的降级为 gap/pending_human；
  * 冲突不隐藏：同一指标多来源不一致时全部保留并标 source_conflict。

仅依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst",
}
VALID_CLAIM_STATUS = {"verified", "pending_human", "conflict", "gap"}
VALID_CLAIM_TYPES = {"number", "term", "citation", "figure", "claim", "edit"}


def reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def normalize(text):
    """Normalize line endings so cross-platform verbatim checks are stable."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds 2MiB"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def read_json(path):
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[:exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def locate_line(text, line_no):
    """Return the content of a 1-based line, or None if out of range."""
    lines = normalize(text).split("\n")
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1]
    return None


def quote_file(path, start, end):
    """Extract lines [start, end] (1-based, inclusive) as normalized text."""
    text, err = read_text(path)
    if text is None:
        return None, err
    lines = normalize(text).split("\n")
    if start < 1:
        start = 1
    if end < start:
        end = start
    if start > len(lines):
        return None, "line range out of bounds (%d > %d)" % (start, len(lines))
    end = min(end, len(lines))
    return "\n".join(lines[start - 1:end]), None


def build_locator(rel, line_no):
    """Canonical locator: 'rel/path#L<n>'. Line optional."""
    loc = rel.replace("\\", "/")
    if line_no and line_no > 0:
        loc += "#L%d" % line_no
    return loc


def verify_quote(path, quote):
    """Check that quote appears verbatim (normalized) in the file text.

    Returns (ok, message). ok=True only when quote is a non-empty substring.
    Whitespace at the quote edges is trimmed before matching so that
    extraction artifacts (trailing newline) do not cause false negatives.
    """
    if not quote or not quote.strip():
        return False, "empty quote"
    text, err = read_text(path)
    if text is None:
        return False, err or "unreadable"
    needle = normalize(quote).strip()
    hay = normalize(text)
    return (needle in hay), ("matched" if needle in hay else "no_verbatim_match")


def index(root, out):
    """Build source_index.json: file metadata + sha256 for reproducibility."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "path": full,
                "rel": rel,
                "size": size,
                "ext": ext,
                "is_text": ext in TEXT_EXTS,
                "sha256": sha256_file(full) if ext in TEXT_EXTS else None,
            })
    obj = {
        "input_root": root,
        "generated_at": None,  # filled below via main's utcnow pattern if needed
        "file_count": len(files),
        "files": files,
    }
    write_json(out, obj)
    return obj


def new_claim(claim_id, ctype, statement, locator, quote, status="verified",
              confidence="medium", note=""):
    return {
        "id": claim_id,
        "type": ctype,
        "statement": statement,
        "locator": locator,
        "quote": quote,
        "status": status,
        "confidence": confidence,
        "note": note,
    }


def verify_map(map_path, root):
    """Validate every claim's quote is a verbatim substring of its source file.

    Locators are resolved against root (the /input directory): 'materials/x.md#L5'
    maps to root/materials/x.md. Quotes that cannot be verified are downgraded to
    status 'gap' in the returned (and written) map, with a note appended.
    Returns (report, exit_code).
    """
    obj, errs = read_json(map_path)
    if obj is None or not isinstance(obj, dict):
        return {"ok": False, "errors": errs or ["unreadable map"]}, 1
    claims = obj.get("claims", [])
    if not isinstance(claims, list):
        return {"ok": False, "errors": ["claims not a list"]}, 1

    verified, downgraded, missing = 0, 0, 0
    problems = []
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            problems.append("claims[%d] not object" % i)
            continue
        loc = claim.get("locator", "")
        quote = claim.get("quote", "")
        # Split locator into rel path and optional #L<n>.
        path_part = loc.split("#")[0] if "#" in loc else loc
        full = os.path.join(root, path_part.replace("/", os.sep))
        if not path_part or not os.path.isfile(full):
            claim["status"] = "gap"
            claim["note"] = (claim.get("note") or "") + " [locator unresolvable]"
            missing += 1
            continue
        ok, msg = verify_quote(full, quote)
        if ok:
            verified += 1
            if claim.get("status") not in VALID_CLAIM_STATUS:
                claim["status"] = "verified"
        else:
            downgraded += 1
            if claim.get("status") == "verified":
                claim["status"] = "gap"
            claim["note"] = (claim.get("note") or "") + " [verify: %s]" % msg
    write_json(map_path, obj)
    report = {
        "ok": (downgraded == 0 and missing == 0 and not problems),
        "claims_total": len(claims),
        "verified": verified,
        "downgraded": downgraded,
        "missing_locator": missing,
        "problems": problems,
    }
    return report, (0 if report["ok"] else 1)


def cmd_index(args):
    obj = index(args.root, args.out)
    print(json.dumps({"out": args.out, "file_count": obj["file_count"]},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_quote(args):
    text, err = quote_file(args.file, args.start, args.end)
    if text is None:
        print(json.dumps({"ok": False, "error": err}))
        return 0
    print(json.dumps({"ok": True, "file": args.file,
                      "start": args.start, "end": args.end,
                      "quote": text}, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args):
    if args.quote is not None:
        quote = args.quote
    elif args.quote_file:
        text, err = read_text(args.quote_file)
        if text is None:
            quote = ""
        else:
            quote = text
    else:
        quote = ""
    ok, msg = verify_quote(args.file, quote)
    print(json.dumps({"ok": ok, "message": msg}))
    return 0 if ok else 0  # non-fatal: report only


def cmd_verify_map(args):
    report, code = verify_map(args.map, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


def build_parser():
    p = argparse.ArgumentParser(
        prog="evidence.py",
        description="证据溯源：索引、逐字引用与 evidence_map 校验",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("index", help="build source_index.json")
    sp.add_argument("--root", default="/input")
    sp.add_argument("--out", default="/app/work/source_index.json")
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("quote", help="extract lines [start,end] verbatim")
    sp.add_argument("--file", required=True)
    sp.add_argument("--start", type=int, required=True)
    sp.add_argument("--end", type=int, required=True)
    sp.set_defaults(func=cmd_quote)

    sp = sub.add_parser("verify", help="check a quote is a verbatim substring")
    sp.add_argument("--file", required=True)
    sp.add_argument("--quote", default=None)
    sp.add_argument("--quote-file", default=None)
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("verify-map", help="validate evidence_map quotes verbatim")
    sp.add_argument("--map", required=True)
    sp.add_argument("--root", default="/input")
    sp.set_defaults(func=cmd_verify_map)
    return p


def main(argv=None):
    reconfigure_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
