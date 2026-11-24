#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_evidence_map.py — 页计划+提取文本 → 页级证据映射（仅标准库）。

对 page_plan.json 中每页的 ref{locator, quote}：
  * 定位源文本文件（绝对路径 / 相对 --text 目录 / 按文件名在目录树查找）；
  * 以"空白归一"后的逐字子串匹配判断 quote 是否真实出现在源文本中；
  * 命中 → evidence_status=located；未命中 → not_verified 并写 uncertainty。

结果合并进 result.json 的 evidence_map，并另存 evidence_map.json 供复跑核对。
退出码：0 成功；2 参数错误。
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SIZE_CAP = 2 * 1024 * 1024


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def read_text(path, cap=SIZE_CAP):
    p = Path(path)
    if not p.is_file():
        return None
    if p.stat().st_size > cap:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeError:
            continue
    return None


def load_json(path):
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError, OSError):
        return None


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_ref_file(ref, text_dir):
    f = ref.get("file") or ref.get("locator_file")
    if not f:
        return None
    p = Path(f)
    if p.is_absolute():
        return str(p)
    cand = Path(text_dir) / f
    if cand.is_file():
        return str(cand)
    name = Path(f).name
    for hit in Path(text_dir).rglob(name):
        if hit.is_file():
            return str(hit)
    return str(cand)


def locate_quote(quote, text):
    q = norm(quote)
    if not q:
        return None
    return q in norm(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成页级证据映射")
    ap.add_argument("--plan", default="/app/work/page_plan.json")
    ap.add_argument("--text", default="/app/work/extracted")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--result", default="/app/output/result.json")
    args = ap.parse_args(argv)

    plan = load_json(args.plan)
    if isinstance(plan, dict):
        pages = plan.get("pages") or plan.get("page_plan") or []
    elif isinstance(plan, list):
        pages = plan
    else:
        pages = []

    evidence_map = []
    uncertainty = []
    for pg in pages:
        if not isinstance(pg, dict):
            continue
        index = pg.get("index", pg.get("page"))
        title = pg.get("title")
        claim = pg.get("claim")
        refs = pg.get("refs") or []
        ref_entries = []
        located = 0
        total = 0
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            total += 1
            quote = ref.get("quote") or ""
            file_path = resolve_ref_file(ref, args.text)
            text = read_text(file_path) if file_path else None
            hit = text is not None and bool(locate_quote(quote, text))
            status = "located" if hit else "not_verified"
            if hit:
                located += 1
            else:
                uncertainty.append({
                    "code": "evidence_not_located",
                    "page": index,
                    "detail": "verbatim quote not found in resolved source text",
                    "ref": {
                        "file": file_path,
                        "locator": ref.get("locator"),
                        "quote": quote[:120],
                    },
                })
            ref_entries.append({
                "file": file_path,
                "locator": ref.get("locator"),
                "quote": quote,
                "evidence_status": status,
            })
        if total == 0:
            page_status = "no_refs"
        elif located == total:
            page_status = "located"
        elif located > 0:
            page_status = "partial"
        else:
            page_status = "not_verified"
        evidence_map.append({
            "page": index,
            "title": title,
            "claim": claim,
            "refs": ref_entries,
            "evidence_status": page_status,
        })

    result = load_json(args.result) or {}
    result["evidence_map"] = evidence_map
    if uncertainty:
        existing_u = result.get("uncertainty")
        if not isinstance(existing_u, list):
            existing_u = []
        existing_u.extend(uncertainty)
        result["uncertainty"] = existing_u
    if not isinstance(result.get("change_log"), list):
        result["change_log"] = []
    total_refs = sum(len(e["refs"]) for e in evidence_map)
    result["change_log"].append({
        "ts": now_iso(),
        "event": "build_evidence_map",
        "detail": "%d pages, %d refs, %d not located" % (
            len(evidence_map), total_refs, len(uncertainty)),
    })
    save_json(args.result, result)
    save_json(Path(args.output) / "evidence_map.json", evidence_map)

    print(json.dumps({
        "pages": len(evidence_map),
        "refs": total_refs,
        "not_located": len(uncertainty),
        "result": args.result,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
