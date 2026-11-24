#!/usr/bin/env python3
"""check_evidence.py — 证据映射验证（仅标准库）。

验证 evidence_map 中每条证据：
  1) locator 指向的源文件是否存在；
  2) locator 的行号是否有效（若有）；
  3) quote 逐字（忽略空白差异）是否真的出现在该位置。

不通过不修改原文件，只输出 verified/unverified 清单，供 Agent 修正或挂起。

用法：
    python3 -B scripts/check_evidence.py --evidence /app/work/evidence_map.json --base /input
"""
import argparse
import json
import re
import sys
from pathlib import Path


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def normalize(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def parse_locator(locator):
    """解析 'path#L12' / 'path:12' / 'path#p3' / 'path'。"""
    if not locator:
        return None, None
    loc = str(locator)
    path_part = loc
    line = None
    m = re.search(r"[#:](?:[LlPp])?\s*(\d+)", loc)
    if m:
        line = int(m.group(1))
        path_part = loc[:m.start()].rstrip()
    else:
        # 形如 file.md#p3 但没匹配到数字（罕见），直接截断
        path_part = loc.split("#")[0].split(":")[0].rstrip()
    return path_part, line


def resolve_source(source, base_dir):
    """把 source 解析为绝对路径；支持绝对路径与相对 base 的路径。"""
    if not source:
        return None
    p = Path(str(source))
    if p.is_absolute():
        return p if p.exists() else None
    cand = Path(base_dir) / str(source)
    if cand.exists():
        return cand
    # 也尝试只取 basename 在 base 下搜索（有限深度）
    for hit in Path(base_dir).rglob(str(p.name)):
        if hit.is_file():
            return hit
    return None


def verify_entry(entry, base_dir):
    src = entry.get("source_id") or entry.get("source") or entry.get("src")
    locator = entry.get("locator")
    quote = entry.get("quote")
    path_part, line = parse_locator(locator)
    target = src if (src and Path(str(src)).is_absolute()) else path_part
    resolved = resolve_source(target or path_part, base_dir)
    status = "unverified"
    detail = []
    if resolved is None:
        status = "missing_source"
        detail.append("源文件未找到：%s" % (target or path_part))
        return status, detail
    text = read_text(resolved)
    if text is None:
        status = "unreadable_source"
        detail.append("源文件不可读：%s" % resolved)
        return status, detail
    lines = text.split("\n")
    if line is not None:
        if line < 1 or line > len(lines):
            status = "bad_locator_line"
            detail.append("行号 %d 超出范围（共 %d 行）" % (line, len(lines)))
            return status, detail
    if quote:
        q = normalize(quote)
        if line is not None:
            candidate = normalize(lines[line - 1])
            if q in candidate or candidate in q:
                status = "verified"
            else:
                status = "quote_mismatch"
                detail.append("quote 与第 %d 行内容不匹配" % line)
        else:
            hay = normalize(text)
            if q in hay:
                status = "verified"
            else:
                status = "quote_mismatch"
                detail.append("quote 未在文件全文出现")
    else:
        status = "verified" if (resolved and (line is None or (1 <= line <= len(lines)))) else "unverified"
        if line is None:
            detail.append("无 quote，仅验证来源存在")
    return status, detail


def load_evidence(path):
    data, err = None, None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                data = json.load(fh)
            break
        except (UnicodeDecodeError, UnicodeError, json.JSONDecodeError, OSError) as exc:
            err = str(exc)
    if data is None:
        return None, err
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        for key in ("evidence_map", "evidence", "evidences", "entries"):
            if key in data:
                return data[key], None
        return [], "no_evidence_map_key"
    return None, "unknown_structure"


def main(argv=None):
    parser = argparse.ArgumentParser(description="验证 evidence_map 的 locator 与 quote")
    parser.add_argument("--evidence", default="/app/work/evidence_map.json",
                        help="evidence_map.json 或含 evidence_map 的 result.json")
    parser.add_argument("--base", default="/input", help="源文件相对解析基准目录")
    parser.add_argument("--output", default="/app/work/checks",
                        help="验证报告输出目录")
    args = parser.parse_args(argv)

    entries, err = load_evidence(args.evidence)
    if entries is None:
        report = {"status": "error", "errors": ["cannot_load_evidence: %s" % err],
                  "entries": []}
    else:
        results = []
        for i, entry in enumerate(entries):
            status, detail = verify_entry(entry, args.base)
            results.append({"index": i, "status": status,
                            "claim": entry.get("claim"),
                            "locator": entry.get("locator"),
                            "detail": detail})
        ok = sum(1 for r in results if r["status"] == "verified")
        report = {"status": "ok", "total": len(results), "verified": ok,
                  "unverified": len(results) - ok, "entries": results}

    out_dir = Path(args.output)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "evidence_report.json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        md = ["# 证据验证报告", "",
              "- 总数：%s" % report.get("total", 0),
              "- 通过：%s" % report.get("verified", 0),
              "- 未通过：%s" % report.get("unverified", 0), ""]
        for r in report.get("entries", []):
            if r["status"] != "verified":
                md.append("- [%s] %s | %s | %s" % (r["status"], r.get("claim"),
                                                    r.get("locator"), "; ".join(r.get("detail", []))))
        with open(out_dir / "evidence_report.md", "w", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")
    except OSError as exc:
        print(json.dumps({"status": "error", "errors": ["cannot_write_report: %s" % exc]},
                         ensure_ascii=False))
        return 0

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
