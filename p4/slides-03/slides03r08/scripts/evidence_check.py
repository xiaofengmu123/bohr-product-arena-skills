#!/usr/bin/env python3
"""evidence_check.py — 校验页级证据映射：逐页主张、证据强度、locator、逐字 quote。

输出 JSON 报告；默认退出码 0，--strict 时发现 error 则退出 1。
仅依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

VALID_STRENGTH = {"high", "medium", "low"}


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def load_source_texts(work_dir):
    """从 /app/work 读取可核验的源文本（sources.json 或 *.txt）。"""
    texts = {}
    sources_file = os.path.join(work_dir, "sources.json")
    if os.path.isfile(sources_file):
        data = load_json(sources_file)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    key = item.get("path") or item.get("name") or item.get("locator")
                    val = item.get("text") or item.get("content")
                    if key and isinstance(val, str):
                        texts[norm(str(key))] = norm(val)
        elif isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, str):
                    texts[norm(key)] = norm(val)
                elif isinstance(val, dict) and isinstance(val.get("text"), str):
                    texts[norm(key)] = norm(val["text"])
    for dirpath, _, filenames in os.walk(work_dir):
        for fn in filenames:
            if fn.endswith(".txt"):
                full = os.path.join(dirpath, fn)
                try:
                    with open(full, "r", encoding="utf-8-sig") as fh:
                        texts[norm(full)] = norm(fh.read())
                except (OSError, UnicodeDecodeError):
                    continue
    return texts


def check(result, source_texts):
    errors = []
    warnings = []
    pages = result.get("pages")
    evidence_map = result.get("evidence_map")

    if not isinstance(pages, list) or not pages:
        errors.append("pages 缺失或为空")
    if not isinstance(evidence_map, list) or not evidence_map:
        errors.append("evidence_map 缺失或为空")

    if isinstance(pages, list):
        seen_claims = set()
        for i, page in enumerate(pages):
            pid = (page.get("page_index", i + 1) if isinstance(page, dict) else i + 1)
            if not isinstance(page, dict):
                errors.append("pages[{}] 不是对象".format(i))
                continue
            claim = norm(page.get("single_claim"))
            if not claim:
                errors.append("page {}: single_claim 缺失或为空".format(pid))
            elif claim in seen_claims:
                warnings.append("page {}: single_claim 与前面页面重复".format(pid))
            seen_claims.add(claim)
            strength = (page.get("evidence_strength") or "").lower()
            if strength not in VALID_STRENGTH:
                errors.append("page {}: evidence_strength 非法（须 high/medium/low）".format(pid))
            if not isinstance(page.get("figures"), list):
                errors.append("page {}: figures 缺失或非数组".format(pid))
            if not norm(page.get("speaker_notes")):
                errors.append("page {}: speaker_notes 缺失或为空".format(pid))
            if not isinstance(page.get("source_refs"), list) or not page["source_refs"]:
                errors.append("page {}: source_refs 缺失或为空".format(pid))

    em_count = len(evidence_map) if isinstance(evidence_map, list) else 0
    quote_unverifiable = 0
    if isinstance(evidence_map, list):
        for i, item in enumerate(evidence_map):
            if not isinstance(item, dict):
                errors.append("evidence_map[{}] 不是对象".format(i))
                continue
            pid = item.get("page_index", i + 1)
            locator = norm(item.get("locator"))
            quote = norm(item.get("quote"))
            if not locator:
                errors.append("evidence_map[{}] (page {}): locator 缺失".format(i, pid))
            if not quote:
                errors.append("evidence_map[{}] (page {}): quote 缺失（须逐字引用源材料）".format(i, pid))
            if locator and quote and source_texts:
                hit = any(quote in text for text in source_texts.values())
                if not hit:
                    quote_unverifiable += 1
                    warnings.append(
                        "evidence_map[{}] (page {}): quote 未在已知源文本中命中，请核对 locator 与逐字引用".format(i, pid))

    if source_texts and em_count and quote_unverifiable:
        warnings.append("{}/{} 条 quote 未能在 /app/work 源文本中命中".format(quote_unverifiable, em_count))

    return {"errors": errors, "warnings": warnings}


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验页级证据映射")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--report", default=None, help="报告输出路径（JSON）")
    ap.add_argument("--strict", action="store_true", help="有 error 时退出码 1")
    args = ap.parse_args(argv)

    result = load_json(args.result)
    if result is None:
        report = {"ok": False, "errors": ["cannot read result: {}".format(args.result)], "warnings": []}
    else:
        source_texts = load_source_texts(args.work)
        check_result = check(result, source_texts)
        report = {
            "ok": len(check_result["errors"]) == 0,
            "result": args.result,
            "sources_available": len(source_texts),
            "errors": check_result["errors"],
            "warnings": check_result["warnings"],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    if args.strict and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
