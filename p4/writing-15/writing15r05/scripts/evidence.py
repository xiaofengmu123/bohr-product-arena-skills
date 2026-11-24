#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据溯源工具（只依赖标准库）。

用途：把“论断 → 原文”的映射变成可复核、可自动校验的结构，防止 Agent 新增材料外事实。

子命令：
  seed      扫描 sources 下文本材料，抽取候选事实（数字、DOI、引用键、术语-缩写对），
            生成 evidence_map.json 草稿供 Agent 逐条填充 quote。
  validate  校验 evidence_map.json：每条 quote 是否逐字出现在其 file 的原文中、locator 行号
            是否匹配、同一 claim 是否出现来源冲突。输出 validation_report.json / .md。
            退出码恒为 0；不通过项写入 issues，由 Agent 修正或标注。

evidence_map.json 结构：
  {"entries": [{"id": "e1", "claim": "论断", "file": "materials/ch1.md",
                "locator": "materials/ch1.md#L12", "quote": "逐字原文"}]}
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TEXT_EXTS = {".md", ".txt", ".tex", ".rst", ".bib"}
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>\)\];,]+")
NUM_RE = re.compile(r"(?<!\w)-?\d+(?:\.\d+)?\s*(?:%|％|kg|g|mg|mL|ml|L|℃|°C|mm|cm|m|s|min|h|d|Hz|kHz|MHz|GHz|例|个|次|倍|人)?")
ACRONYM_RE = re.compile(r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\-\s]{1,40})[（(]([A-Z][A-Z0-9]{1,10})[）)]")
CITE_KEY_RE = re.compile(r"\[@?([A-Za-z][\w:\-]{1,40})\]")


def read_text(path):
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return None


def norm_ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def list_text_files(root):
    root = Path(root)
    return sorted([p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_EXTS])


def cmd_seed(args):
    root = Path(args.sources)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    for f in list_text_files(root):
        text = read_text(f)
        if text is None:
            continue
        rel = f.relative_to(root).as_posix() if root in f.parents else f.name
        lines = text.splitlines()
        for i, ln in enumerate(lines, 1):
            loc = f"{rel}#L{i}"
            for m in DOI_RE.finditer(ln):
                entries.append({"id": None, "claim": None, "file": rel, "locator": loc, "quote": norm_ws(ln), "hint": f"DOI {m.group(0)}"})
            for m in ACRONYM_RE.finditer(ln):
                entries.append({"id": None, "claim": None, "file": rel, "locator": loc, "quote": norm_ws(ln), "hint": f"术语 {m.group(1).strip()}（{m.group(2)}）"})
            for m in CITE_KEY_RE.finditer(ln):
                entries.append({"id": None, "claim": None, "file": rel, "locator": loc, "quote": norm_ws(ln), "hint": f"引用 {m.group(1)}"})
    # 去重（file+quote）
    uniq = {}
    for e in entries:
        uniq[(e["file"], e["quote"])] = e
    final = [{"id": f"e{idx+1}", "claim": None, "file": e["file"], "locator": e["locator"], "quote": e["quote"], "hint": e["hint"]}
             for idx, e in enumerate(sorted(uniq.values(), key=lambda x: (x["file"], x["quote"])))]
    (out / "evidence_map.json").write_text(json.dumps({"entries": final}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "entries": len(final), "path": str(out / "evidence_map.json")}, ensure_ascii=False))
    return 0


def load_map(path):
    obj, err = None, None
    try:
        raw = Path(path).read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        obj = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except OSError as e:
        err = f"read error: {e}"
    except json.JSONDecodeError as e:
        err = f"bad JSON: {e}"
    if err:
        return None, err
    entries = obj.get("entries") if isinstance(obj, dict) else None
    if not isinstance(entries, list):
        return None, "evidence_map.json 缺少 entries 列表"
    return entries, None


def cmd_validate(args):
    map_path = Path(args.map)
    root = Path(args.sources)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    entries, err = load_map(map_path)
    if err:
        report = {"ok": False, "error": err, "issues": []}
    else:
        issues = []
        cache = {}
        claim_map = {}
        for e in entries:
            if not isinstance(e, dict):
                issues.append({"id": None, "check": "entry_shape", "severity": "error", "message": "entry 非对象"})
                continue
            eid = e.get("id") or "?"
            for key in ("claim", "file", "quote"):
                if not e.get(key):
                    issues.append({"id": eid, "check": "missing_field", "severity": "warning", "message": f"缺字段 {key}"})
            rel = e.get("file") or ""
            candidates = []
            if rel and Path(rel).is_absolute():
                candidates.append(Path(rel))
            else:
                candidates.append(root / rel)
                candidates.append(root / Path(rel).name)
            fp = next((c for c in candidates if c.exists()), candidates[0])
            if rel not in cache:
                cache[rel] = read_text(fp) if fp.exists() else None
            text = cache.get(rel)
            if text is None:
                issues.append({"id": eid, "check": "file_missing", "severity": "error", "message": f"来源文件不可读: {rel}"})
                continue
            quote = norm_ws(e.get("quote") or "")
            if quote and norm_ws(text).find(quote) == -1:
                issues.append({"id": eid, "check": "quote_not_found", "severity": "error", "message": f"quote 未在原文中逐字出现: {rel}"})
            # locator 行号校验
            loc = e.get("locator") or ""
            m = re.search(r"#L(\d+)", loc)
            if m and quote:
                line_no = int(m.group(1))
                lines = text.splitlines()
                if 1 <= line_no <= len(lines):
                    if norm_ws(quote) not in norm_ws(lines[line_no - 1]):
                        issues.append({"id": eid, "check": "locator_mismatch", "severity": "warning", "message": f"locator 行号与 quote 所在行不符: {loc}"})
                else:
                    issues.append({"id": eid, "check": "locator_out_of_range", "severity": "warning", "message": f"locator 行号越界: {loc}"})
            # claim 冲突
            claim = norm_ws(e.get("claim") or "")
            if claim:
                claim_map.setdefault(claim, []).append((eid, rel, e.get("quote")))
        for claim, locs in claim_map.items():
            if len({q for _, _, q in locs}) > 1:
                issues.append({
                    "id": None, "check": "source_conflict", "severity": "warning",
                    "message": f"同一论断多来源且 quote 不一致: {claim[:60]}",
                    "locator": [f"{f}" for _, f, _ in locs],
                })
        report = {"ok": True, "entries": len(entries), "issue_count": len(issues), "issues": issues}

    (out / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 证据校验报告", "", f"- 条目数: {report.get('entries')}", f"- 问题数: {report.get('issue_count')}", ""]
    for it in report.get("issues", []):
        lines.append(f"- [{it['severity']}] {it['message']}（id={it.get('id')}）")
    (out / "validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report.get("ok"), "issue_count": report.get("issue_count")}, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="证据溯源工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("seed", help="生成 evidence_map.json 草稿")
    sp.add_argument("--sources", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(func=cmd_seed)
    vp = sub.add_parser("validate", help="校验 evidence_map.json")
    vp.add_argument("--map", required=True)
    vp.add_argument("--sources", required=True)
    vp.add_argument("--out", required=True)
    vp.set_defaults(func=cmd_validate)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(f"unhandled {type(e).__name__}: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
