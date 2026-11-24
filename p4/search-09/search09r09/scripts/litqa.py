#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa 证据定位 / 逐字核验 / 阈值分级（仅标准库，确定性）。

子命令：
  locators --text FILE --out FILE [--id ID]
      从纯文本生成稳定段落/句子索引（把规范化文本复制到 --out，打印元信息）。
  verify   --result RESULT --work DIR [--out FILE]
      逐条核对 assessment[*].quote 是否逐字存在于所标 locator（先按句、查不到回退整段）。
      报告写入 FILE（默认 work/verify_report.json），退出码恒为 0。
  grade    --result RESULT [--inplace]
      按显式阈值把 assessment 聚合为总体风险等级；--inplace 写回 result.json 的 grade 字段。

locator 语法：<源ID>#<锚点>；源ID 对应 result.json.text_index 的键，锚点支持：
  pN / pNsM / pN-M / raw:OFF:LEN
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

WS = re.compile(r"\s+")
SENT_BOUND = re.compile(r"(?<=[.!?。！？])")
MAX_FILE_BYTES = 2 * 1024 * 1024


def normalize_ws(s):
    return WS.sub(" ", s).strip()


def read_text_simple(path, max_bytes=MAX_FILE_BYTES):
    p = Path(path)
    if not p.exists():
        return None
    try:
        if p.stat().st_size > max_bytes:
            return None
    except OSError:
        return None
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, OSError):
            continue
    return None


def load_json(path):
    text = read_text_simple(path)
    if text is None:
        return None, ["unreadable:%s" % path]
    try:
        return json.loads(text), []
    except (json.JSONDecodeError, ValueError) as exc:
        return None, ["bad_json:%s:%s" % (path, exc)]


def split_paragraphs(text):
    parts = re.split(r"\n\s*\n", text)
    return [normalize_ws(p) for p in parts if normalize_ws(p)]


def split_sentences(para):
    p = normalize_ws(para)
    if not p:
        return []
    toks = [t.strip() for t in SENT_BOUND.split(p)]
    return [t for t in toks if t]


def build_index(text):
    paras = split_paragraphs(text)
    sents = [split_sentences(p) for p in paras]
    return {
        "chars": len(text),
        "n_paragraphs": len(paras),
        "n_sentences": sum(len(s) for s in sents),
        "paragraphs": paras,
        "sentences": sents,
        "full_norm": normalize_ws(text),
    }


def split_locator(locator):
    loc = (locator or "").strip()
    if "#" in loc:
        src, anchor = loc.split("#", 1)
        return src.strip(), anchor.strip()
    return loc, ""


def resolve_anchor(index, anchor):
    paras = index["paragraphs"]
    sents = index["sentences"]
    a = (anchor or "").strip()
    if not a:
        return index["full_norm"], None
    m = re.fullmatch(r"p(\d+)", a)
    if m:
        n = int(m.group(1)) - 1
        if 0 <= n < len(paras):
            return paras[n], None
        return None, "para_out_of_range:%s" % a
    m = re.fullmatch(r"p(\d+)-(\d+)", a)
    if m:
        x = int(m.group(1)) - 1
        y = int(m.group(2)) - 1
        if 0 <= x <= y < len(paras):
            return " ".join(paras[x:y + 1]), None
        return None, "para_range_out_of_range:%s" % a
    m = re.fullmatch(r"p(\d+)s(\d+)", a)
    if m:
        n = int(m.group(1)) - 1
        k = int(m.group(2)) - 1
        if 0 <= n < len(sents) and 0 <= k < len(sents[n]):
            return sents[n][k], None
        if 0 <= n < len(paras):
            return paras[n], "sentence_fallback_to_para:%s" % a
        return None, "sentence_out_of_range:%s" % a
    m = re.fullmatch(r"raw:(\d+):(\d+)", a)
    if m:
        off = int(m.group(1))
        ln = int(m.group(2))
        full = index["full_norm"]
        if 0 <= off and off + ln <= len(full):
            return full[off:off + ln], None
        return None, "raw_out_of_range:%s" % a
    return None, "unrecognized_anchor:%s" % a


def verify_quotes(result, work_dir):
    """逐条核对 quote 是否逐字存在于 locator 指向的文本片段。"""
    text_index = result.get("text_index") or {}
    assessment = result.get("assessment") or []
    cache = {}
    items = []
    for i, it in enumerate(assessment):
        loc = it.get("locator") or ""
        quote = it.get("quote") or ""
        basis = str(it.get("basis") or "").strip().lower()
        src, anchor = split_locator(loc)
        base = {"index": i, "locator": loc, "quote": quote}
        if not quote:
            if basis in ("not_reported", "insufficient_evidence"):
                items.append(dict(base, ok=None, note="no_quote_basis_%s" % basis))
            else:
                items.append(dict(base, ok=False, note="empty_quote"))
            continue
        rel = text_index.get(src)
        if not rel:
            items.append(dict(base, ok=False, note="no_text_index_for_source:%s" % src))
            continue
        if src not in cache:
            path = Path(work_dir) / rel
            text = read_text_simple(path)
            if text is None:
                cache[src] = None
                items.append(dict(base, ok=False, note="text_unavailable:%s" % path))
                continue
            cache[src] = build_index(text)
        idx = cache[src]
        if idx is None:
            items.append(dict(base, ok=False, note="source_unavailable:%s" % src))
            continue
        span, note = resolve_anchor(idx, anchor)
        if span is None:
            items.append(dict(base, ok=False, note=note))
            continue
        norm_span = normalize_ws(span)
        norm_quote = normalize_ws(quote)
        ok = norm_quote in norm_span
        tail = "" if ok else " quote_not_found"
        items.append(dict(base, ok=ok, note=(note or "") + tail))
    n = len(items)
    n_ok = sum(1 for x in items if x["ok"] is True)
    n_fail = sum(1 for x in items if x["ok"] is False)
    n_na = sum(1 for x in items if x["ok"] is None)
    return {
        "checked": n,
        "ok": n_ok,
        "fail": n_fail,
        "not_applicable": n_na,
        "items": items,
        "summary": {"pass_ratio": round(n_ok / n, 4) if n else None},
    }


RISK = {
    "absent": 0, "adequate": 0, "low": 0, "not_applicable": 0,
    "suspect": 1, "partial": 1, "moderate": 1,
    "present": 2, "inadequate": 2, "high": 2,
}
UNCERTAIN = {"insufficient_evidence", "not_reported", "uncertain", "unclear", "unknown"}


def grade_assessment(assessment):
    points = 0
    critical_fails = 0
    uncertain = 0
    checked = 0
    for it in assessment:
        v = str(it.get("verdict") or "").strip().lower()
        crit = bool(it.get("critical"))
        if v == "not_applicable":
            continue
        checked += 1
        if v in UNCERTAIN:
            uncertain += 1
            continue
        r = RISK.get(v, 0)
        points += r
        if crit and r >= 2:
            critical_fails += 1
    if critical_fails > 0 or points >= 4:
        level = "high"
    elif points >= 2:
        level = "moderate"
    elif uncertain > 0:
        level = "uncertain"
    else:
        level = "low"
    return {
        "level": level,
        "risk_points": points,
        "critical_fails": critical_fails,
        "uncertain_count": uncertain,
        "checked_items": checked,
        "rule": ("任一 critical 项 present/inadequate，或 risk_points>=4 → high；"
                 ">=2 → moderate；有 insufficient_evidence/not_reported 且其余无风险 → uncertain；否则 low"),
    }


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_locators(args):
    text = read_text_simple(args.text)
    if text is None:
        print(json.dumps({"error": "unreadable_or_missing_text:%s" % args.text}, ensure_ascii=False))
        return 1
    idx = build_index(text)
    out = args.out
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"error": "write_failed:%s:%s" % (out, exc)}, ensure_ascii=False))
        return 1
    meta = {
        "id": args.id or Path(args.text).stem,
        "text_file": out,
        "chars": idx["chars"],
        "n_paragraphs": idx["n_paragraphs"],
        "n_sentences": idx["n_sentences"],
        "first_paragraphs": [
            {"p": i + 1, "chars": len(idx["paragraphs"][i]), "head": idx["paragraphs"][i][:120]}
            for i in range(min(5, len(idx["paragraphs"])))
        ],
    }
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args):
    result, errs = load_json(args.result)
    if result is None:
        print(json.dumps({"error": errs}, ensure_ascii=False, indent=2))
        return 0
    report = verify_quotes(result, args.work)
    report["errors"] = errs
    out = args.out or str(Path(args.work) / "verify_report.json")
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        report["write_error"] = str(exc)
    print(json.dumps({
        "checked": report["checked"], "ok": report["ok"], "fail": report["fail"],
        "not_applicable": report["not_applicable"], "report": out,
        "fails": [x for x in report["items"] if x["ok"] is False],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_grade(args):
    result, errs = load_json(args.result)
    if result is None:
        print(json.dumps({"error": errs}, ensure_ascii=False, indent=2))
        return 0
    grade = grade_assessment(result.get("assessment") or [])
    if args.inplace:
        result["grade"] = grade
        tmp = args.result + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, args.result)
            grade["written"] = args.result
        except OSError as exc:
            grade["write_error"] = str(exc)
    print(json.dumps(grade, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="litqa.py", description="证据定位 / 逐字核验 / 阈值分级")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_loc = sub.add_parser("locators", help="生成段落/句子索引")
    p_loc.add_argument("--text", required=True)
    p_loc.add_argument("--out", required=True)
    p_loc.add_argument("--id")

    p_ver = sub.add_parser("verify", help="逐字核验 quote 是否存在于 locator")
    p_ver.add_argument("--result", required=True)
    p_ver.add_argument("--work", required=True)
    p_ver.add_argument("--out")

    p_gra = sub.add_parser("grade", help="阈值聚合总体风险等级")
    p_gra.add_argument("--result", required=True)
    p_gra.add_argument("--inplace", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "locators":
        return cmd_locators(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    if args.cmd == "grade":
        return cmd_grade(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
