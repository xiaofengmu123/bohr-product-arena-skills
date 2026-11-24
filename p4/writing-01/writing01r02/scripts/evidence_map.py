#!/usr/bin/env python3
"""evidence_map.py -- 交叉核验稿件中的 [S:] 来源标记与 [C:] 引用标记。

输入：
  --draft   /app/output/manuscript.md
  --index   /app/work/material_index.json（材料索引，见 references/conventions.md）
  --work    /app/work

输出：
  /app/work/evidence_map.json + 一份人类可读报告（stdout）。

核验点：
  1. 悬空来源：正文出现 [S:x]，但 x 不在材料索引的 material id 或 locator 中。
  2. 悬空引用：正文出现 [C:k]，但 k 既不在参考文献定义区，也不在索引 citations 中。
  3. 定义未用：参考文献区定义了 [C:k]，但正文未引用（可疑装饰性/错配候选）。
  4. 未标注数值：正文出现「数字 + 常见单位」片段且同段没有 [S:] 标记（仅作提示）。

脚本只做机械核对与提示，不做事实真假判断；冲突/缺失由 Agent 写入 uncertainty。
只依赖 Python 标准库。
"""
import argparse
import json
import os
import re
import sys

S_USE_RE = re.compile(r"\[S:([A-Za-z0-9_./#\-\u4e00-\u9fff]+)\]")
C_USE_RE = re.compile(r"\[C:([A-Za-z0-9_\-]+)\]")
C_DEF_LINE_RE = re.compile(r"^\s*\[C:([A-Za-z0-9_\-]+)\]\s+\S")
MD_REF_DEF_RE = re.compile(r"^\s*\[([A-Za-z0-9_\-]+)\]:\s*\S")
NUMBER_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:%|mg|g|kg|mL|L|mm|cm|nm|μm|µg|s|min|h|day|d|°C|K|Hz|kHz|MHz|M|mol|μM|mM|U/L|IU|mmHg|Pa|bar)\b",
    re.IGNORECASE)


def _stdout(text):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(text)
    sys.stdout.flush()


def read_text(path):
    if not path or not os.path.exists(path):
        return ""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_json_safe(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        return json.loads(read_text(path))
    except (json.JSONDecodeError, ValueError):
        return {}


def collect_allowed_sources(index):
    """返回 {允许的 [S:] 键}：material id + 每个 locator。"""
    allowed = set()
    materials = index.get("materials", []) if isinstance(index, dict) else []
    if not isinstance(materials, list):
        return allowed
    for mat in materials:
        if not isinstance(mat, dict):
            continue
        mid = mat.get("id")
        if isinstance(mid, str) and mid:
            allowed.add(mid)
        locs = mat.get("locators", [])
        if isinstance(locs, list):
            for loc in locs:
                if isinstance(loc, dict):
                    lid = loc.get("locator")
                    if isinstance(lid, str) and lid:
                        allowed.add(lid)
                elif isinstance(loc, str):
                    allowed.add(loc)
    return allowed


def collect_citation_keys(index):
    """从索引 citations 收集定义键。"""
    keys = set()
    if not isinstance(index, dict):
        return keys
    cites = index.get("citations", [])
    if isinstance(cites, list):
        for c in cites:
            if isinstance(c, dict) and c.get("key"):
                keys.add(c["key"])
            elif isinstance(c, str):
                keys.add(c)
    for mat in index.get("materials", []) or []:
        if isinstance(mat, dict):
            for c in (mat.get("citations") or []):
                if isinstance(c, dict) and c.get("key"):
                    keys.add(c["key"])
                elif isinstance(c, str):
                    keys.add(c)
    return keys


def parse_draft(draft_text):
    s_used, c_used, c_defined, numbers = [], [], [], []
    seen = {"s": set(), "c_use": set(), "c_def": set()}
    for lineno, line in enumerate(draft_text.splitlines(), 1):
        # 参考文献定义行（[C:key] 内容… 或 [key]: 内容…）优先判定为定义
        m = C_DEF_LINE_RE.match(line) or MD_REF_DEF_RE.match(line)
        if m:
            key = m.group(1)
            if key not in seen["c_def"]:
                seen["c_def"].add(key)
                c_defined.append(key)
            continue
        for s in S_USE_RE.findall(line):
            if s not in seen["s"]:
                seen["s"].add(s)
                s_used.append(s)
        for c in C_USE_RE.findall(line):
            if c not in seen["c_use"]:
                seen["c_use"].add(c)
                c_used.append(c)
        # 数值提示：按句子切分，句子含「数字+单位」却无 [S:] 时记录
        if NUMBER_RE.search(line) and not line.strip().startswith(("##", ">", "|")):
            for sent in re.split(r"[。．.!?;；\n]", line):
                sent = sent.strip()
                if NUMBER_RE.search(sent) and not S_USE_RE.search(sent) and sent:
                    numbers.append({"line": lineno, "text": sent[:160]})
    return s_used, c_used, c_defined, numbers


def main(argv=None):
    ap = argparse.ArgumentParser(description="稿件来源/引用交叉核验")
    ap.add_argument("--draft", default="/app/output/manuscript.md")
    ap.add_argument("--index", default="/app/work/material_index.json")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    draft_text = read_text(args.draft)
    index = read_json_safe(args.index)

    allowed_sources = collect_allowed_sources(index)
    index_cite_keys = collect_citation_keys(index)
    s_used, c_used, c_defined, numbers = parse_draft(draft_text)

    dangling_sources = [s for s in s_used if s not in allowed_sources]
    defined = set(c_defined) | index_cite_keys
    dangling_citations = [c for c in c_used if c not in defined]
    unused_citations = sorted(c for c in defined if c not in set(c_used))

    report = {
        "draft": args.draft,
        "s_used_count": len(s_used),
        "c_used_count": len(c_used),
        "c_defined_count": len(defined),
        "dangling_sources": dangling_sources,
        "dangling_citations": dangling_citations,
        "unused_citations": unused_citations,
        "unlabeled_number_hints": numbers[:50],
        "unlabeled_number_hint_total": len(numbers),
    }

    out_path = os.path.join(args.work, "evidence_map.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    lines = ["evidence_map report",
             "  [S:] used=%d dangling=%d" % (len(s_used), len(dangling_sources)),
             "  [C:] used=%d defined=%d dangling=%d unused=%d"
             % (len(c_used), len(defined), len(dangling_citations),
                len(unused_citations)),
             "  unlabeled-number hints=%d" % len(numbers)]
    if dangling_sources:
        lines.append("  dangling_sources: %s" % ", ".join(dangling_sources))
    if dangling_citations:
        lines.append("  dangling_citations: %s" % ", ".join(dangling_citations))
    if unused_citations:
        lines.append("  unused_citations: %s" % ", ".join(unused_citations))
    _stdout("\n".join(lines) + "\n")

    rc = 1 if (dangling_sources or dangling_citations) else 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
