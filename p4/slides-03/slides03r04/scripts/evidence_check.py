#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence_check.py — 校验 result.json 的页级主张、证据台账与溯源可核验性。

检查项（阻塞 error / 提示 warning）:
  - case_id 存在、status 取值合法；
  - pages 每页：page_index/title/single_claim 非空，evidence_strength/confidence 取值合法，
    source_refs 非空，speaker_notes 不与 single_claim 逐字重复；
  - claims 每条：claim/locator/quote 非空，quote 尽量在 --work 文本语料中定位；
  - evidence_map 存在且为对象；sources 为列表且每项含 path。

退出码：0 表示无 error（可带 warning）；1 表示存在 error。用法见 --help。
"""

import argparse
import json
import os
import re
import sys

STATUS_ENUM = {"complete", "partial", "insufficient_evidence", "input_error", "failed"}
STRENGTH_ENUM = {"high", "medium", "low"}
CONFIDENCE_ENUM = {"established", "indicated", "speculative", "not_reported"}
TEXT_EXTS = {".txt", ".md", ".json", ".jsonl", ".csv", ".tsv"}
MAX_FILE = 2_000_000


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read(), None
    except OSError as e:
        return None, str(e)


def load_json(path):
    text, err = read_text(path)
    if err is not None:
        return None, err
    if text is None:
        return None, "missing"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "json_error: %s" % e


def norm(s):
    return re.sub(r"\s+", "", s or "")


def build_corpus(work):
    corpus = []
    if not work or not os.path.isdir(work):
        return corpus
    for root, dirs, files in os.walk(work):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in TEXT_EXTS:
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) > MAX_FILE:
                    continue
            except OSError:
                continue
            text, err = read_text(path)
            if err is None and text is not None:
                corpus.append((path, norm(text)))
    return corpus


def find_quote(quote, corpus):
    q = norm(quote)
    if not q:
        return None
    for path, text in corpus:
        if q in text:
            return path
    return None


def check(result, corpus):
    errors, warnings, verified = [], [], []

    if not isinstance(result, dict):
        errors.append("result.json 不是 JSON 对象")
        return errors, warnings, verified

    if not result.get("case_id"):
        errors.append("缺少 case_id")

    status = result.get("status")
    if status is None:
        warnings.append("缺少 status")
    elif status not in STATUS_ENUM:
        errors.append("status 取值非法: %r" % status)

    pages = result.get("pages")
    if pages is None:
        warnings.append("缺少 pages（partial 阶段可接受）")
    elif not isinstance(pages, list):
        errors.append("pages 应为数组")
    else:
        for i, p in enumerate(pages):
            prefix = "pages[%d]" % i
            if not isinstance(p, dict):
                errors.append("%s 不是对象" % prefix)
                continue
            pi = p.get("page_index")
            if pi is None and pi != 0:
                warnings.append("%s 缺 page_index" % prefix)
            if not p.get("title"):
                warnings.append("%s 缺 title" % prefix)
            claim = p.get("single_claim")
            if not claim:
                errors.append("%s 缺 single_claim（每页唯一主张）" % prefix)
            s = p.get("evidence_strength")
            if s is not None and s not in STRENGTH_ENUM:
                errors.append("%s evidence_strength 非法: %r" % (prefix, s))
            c = p.get("confidence")
            if c is not None and c not in CONFIDENCE_ENUM:
                warnings.append("%s confidence 非法: %r" % (prefix, c))
            refs = p.get("source_refs")
            if not refs:
                warnings.append("%s 缺 source_refs" % prefix)
            notes = p.get("speaker_notes")
            if notes and claim and norm(notes) == norm(claim):
                warnings.append("%s speaker_notes 与 single_claim 逐字重复" % prefix)

    claims = result.get("claims")
    if claims is None:
        warnings.append("缺少 claims 台账（partial 阶段可接受）")
    elif not isinstance(claims, list):
        errors.append("claims 应为数组")
    else:
        for i, c in enumerate(claims):
            prefix = "claims[%d]" % i
            if not isinstance(c, dict):
                errors.append("%s 不是对象" % prefix)
                continue
            if not c.get("claim"):
                errors.append("%s 缺 claim" % prefix)
            if not c.get("locator"):
                errors.append("%s 缺 locator" % prefix)
            q = c.get("quote")
            if not q:
                errors.append("%s 缺 quote（逐字原文，未改写）" % prefix)
            else:
                hit = find_quote(q, corpus)
                if hit:
                    verified.append({"claim": (c.get("claim") or "")[:80], "quote_file": hit})
                else:
                    warnings.append("%s quote 未在 --work 语料中定位（可能为提取差异）" % prefix)

    em = result.get("evidence_map")
    if em is None:
        warnings.append("缺少 evidence_map（partial 阶段可接受）")
    elif not isinstance(em, dict):
        errors.append("evidence_map 应为对象")

    sources = result.get("sources")
    if sources is None:
        warnings.append("缺少 sources")
    elif not isinstance(sources, list):
        errors.append("sources 应为数组")
    else:
        for i, s in enumerate(sources):
            if isinstance(s, dict) and not s.get("path"):
                warnings.append("sources[%d] 缺 path" % i)

    return errors, warnings, verified


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--work", default="/app/work", help="用于 quote 定位的文本语料目录")
    ap.add_argument("--quiet", action="store_true", help="仅输出 JSON 报告到 stdout")
    args = ap.parse_args()

    result, err = load_json(args.result)
    if err is not None:
        print(json.dumps({"ok": False, "errors": ["result.json 不可读: %s" % err]},
                         ensure_ascii=False, indent=2))
        return 2

    corpus = build_corpus(args.work)
    errors, warnings, verified = check(result, corpus)
    report = {
        "ok": not errors,
        "case_id": result.get("case_id") if isinstance(result, dict) else None,
        "status": result.get("status") if isinstance(result, dict) else None,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "corpus_files": len(corpus),
        "errors": errors,
        "warnings": warnings,
        "verified_quotes": verified,
    }
    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
