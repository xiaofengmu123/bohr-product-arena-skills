#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-scoremap 定位/核验/分级/抽取辅助（只依赖标准库）。

子命令（由 main.py 或 Agent 直接调用）：
  litqa.py locators --text <file> --source <id> --out <file>
      把纯文本切成稳定段落，生成 locator 索引（<source>#pN）。
  litqa.py verify --result /app/output/result.json --work /app/work
      逐条核验 assessment 中的 quote 是否逐字存在于抽取文本。
  litqa.py grade --result /app/output/result.json
      按偏倚风险条目做确定性聚合，输出建议等级与理由（阈值见 references/rubric.md）。
  litqa.py extract --pdf <pdf> --out <txt>
      尝试调用 pdftotext 抽取文本；工具缺失时优雅降级并记录。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

WHITESPACE_RE = re.compile(r"\s+")

# 偏倚风险判定值 -> 严重度（仅用于 grade 的确定性聚合，非最终结论）
VERDICT_SEVERITY = {
    "明确存在": 3,
    "可能存在": 2,
    "证据不足": 1,
    "未见迹象": 0,
    "不适用": 0,
}


def normalize_ws(text):
    """折叠任意空白为单空格（用于宽松逐字匹配）。"""
    return WHITESPACE_RE.sub(" ", (text or "")).strip()


def split_paragraphs(text):
    """按空行切段；段内折叠空白；返回稳定段落列表（保留原文序）。"""
    if not text:
        return []
    chunks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = []
    for ch in chunks:
        ch = ch.strip()
        if ch:
            out.append(ch)
    return out


def make_locators(text, source_id):
    """生成 [{"locator": "src#pN", "text": paragraph}, ...]。"""
    return [
        {"locator": "%s#p%d" % (source_id, i + 1), "text": para}
        for i, para in enumerate(split_paragraphs(text))
    ]


def verify_quote_in_text(text, quote):
    """逐字核验：优先精确子串，其次空白折叠后匹配，最后判定 missing。"""
    q = (quote or "").strip()
    if not q:
        return "empty"
    if q in (text or ""):
        return "exact"
    qn = normalize_ws(q)
    if qn and qn in normalize_ws(text or ""):
        return "normalized"
    return "missing"


def extract_pdf_text(pdf_path, out_txt):
    """调用 pdftotext（若可用）；失败返回 (ok, message)。"""
    pdf = Path(pdf_path)
    if not pdf.exists():
        return False, "pdf_missing: %s" % pdf
    out = Path(out_txt)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(pdf), str(out)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except FileNotFoundError:
        return False, "pdftotext_unavailable"
    except subprocess.TimeoutExpired:
        return False, "pdftotext_timeout"
    except OSError as exc:
        return False, "pdftotext_error: %s" % exc
    if proc.returncode != 0:
        return False, "pdftotext_exit_%d: %s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
    return out.exists() and out.stat().st_size > 0, "ok"


def grade_from_assessment(items):
    """按偏倚风险条目做确定性聚合；阈值与弃权规则见 references/rubric.md。

    规则（只作交叉检查，最终等级与理由由 Agent 在 assessment.md 记录）：
      - 关键偏倚条目任一 `明确存在` -> 高；
      - 关键偏倚条目 >=2 个 `可能存在` -> 高；
      - 关键偏倚条目任一 `可能存在` -> 中；
      - 偏倚条目 >50% 为 `证据不足` -> 证据不足；
      - 其余 -> 低。
    """
    bias = [it for it in items if it.get("dimension") == "偏倚风险"]
    if not bias:
        return {"suggested": "证据不足", "reason": "无偏倚风险条目，无法聚合", "counts": {}}
    crit = [it for it in bias if it.get("critical")]
    crit_sev = [VERDICT_SEVERITY.get(it.get("verdict"), 1) for it in crit]
    n_insuff = sum(1 for it in bias if it.get("verdict") == "证据不足")
    total = len(bias)

    if any(s == 3 for s in crit_sev):
        level = "高"
        reason = "存在关键偏倚领域明确存在"
    elif sum(1 for s in crit_sev if s == 2) >= 2:
        level = "高"
        reason = ">=2 个关键偏倚领域可能存在"
    elif any(s == 2 for s in crit_sev):
        level = "中"
        reason = "存在关键偏倚领域可能存在"
    elif n_insuff > total * 0.5:
        level = "证据不足"
        reason = ">50% 偏倚条目证据不足"
    else:
        level = "低"
        reason = "未检出明确/可能偏倚，证据覆盖充分"

    return {
        "suggested": level,
        "reason": reason,
        "counts": {"total": total, "critical": len(crit), "insufficient": n_insuff},
    }


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_locators(args):
    text, errs = read_text(args.text)
    if text is None:
        print("ERROR: %s" % "; ".join(errs))
        return 1
    locs = make_locators(text, args.source)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(locs, fh, ensure_ascii=False, indent=2)
    print("locators=%d source=%s out=%s" % (len(locs), args.source, out))
    return 0


def cmd_verify(args):
    rp = Path(args.result)
    if not rp.exists():
        print("ERROR: result.json missing: %s" % rp)
        return 1
    data, errs = read_json_lenient(rp)
    if data is None:
        print("ERROR: %s" % "; ".join(errs))
        return 1
    corpus = []
    work = Path(args.work) if args.work else None
    if work and work.is_dir():
        for p in sorted(work.rglob("*.txt")):
            text, _ = read_text(p)
            if text is not None:
                corpus.append(text)
    joined = "\n".join(corpus)
    n_ok = n_missing = 0
    for i, item in enumerate(data.get("assessment") or []):
        q = (item.get("quote") or "").strip()
        if not q or q == "not_reported":
            continue
        res = verify_quote_in_text(joined, q)
        if res == "missing":
            n_missing += 1
            print("MISSING assessment[%d] %s | %s" % (i, item.get("checkpoint", ""), q[:80]))
        else:
            n_ok += 1
    print("verify: ok=%d missing=%d (corpus_chars=%d)" % (n_ok, n_missing, len(joined)))
    return 0 if n_missing == 0 else 2


def cmd_grade(args):
    rp = Path(args.result)
    if not rp.exists():
        print("ERROR: result.json missing: %s" % rp)
        return 1
    data, errs = read_json_lenient(rp)
    if data is None:
        print("ERROR: %s" % "; ".join(errs))
        return 1
    g = grade_from_assessment(data.get("assessment") or [])
    print(json.dumps(g, ensure_ascii=False, indent=2))
    return 0


def cmd_extract(args):
    ok, msg = extract_pdf_text(args.pdf, args.out)
    print("extract: ok=%s %s out=%s" % (ok, msg, args.out))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 共享 I/O（避免与 main 重复依赖）
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=2 * 1024 * 1024):
    errors = []
    p = Path(path)
    if not p.exists():
        return None, ["missing: %s" % p]
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, ["stat_failed: %s: %s" % (p, exc)]
    if size > max_bytes:
        return None, ["oversize(%d): %s" % (size, p)]
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), errors
        except UnicodeDecodeError as exc:
            errors.append("decode(%s)_failed: %s" % (enc, exc))
    return None, errors


def read_json_lenient(path):
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except (json.JSONDecodeError, ValueError) as exc:
        return None, errors + ["bad_json: %s: %s" % (path, exc)]


def main(argv=None):
    parser = argparse.ArgumentParser(description="litqa-scoremap locators/verify/grade/extract")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("locators")
    p.add_argument("--text", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_locators)

    p = sub.add_parser("verify")
    p.add_argument("--result", required=True)
    p.add_argument("--work", default=None)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("grade")
    p.add_argument("--result", required=True)
    p.set_defaults(func=cmd_grade)

    p = sub.add_parser("extract")
    p.add_argument("--pdf", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_extract)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
