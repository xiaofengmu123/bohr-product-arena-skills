#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-evidence 证据锚定工具（确定性，仅依赖 Python 标准库）。

提供三类能力：
  - locators：对抽取的纯文本生成稳定段落/句子定位（char_span 可回溯）；
  - verify：对 evidence_map 逐字核验引文，输出 match 状态与 quote_sha256；
  - grade：按阈值把 verdict 列表聚合为可解释的总体风险等级。

用法（在 SKILL 根目录下运行）：
  python3 -B scripts/litqa.py locators --work /app/work
  python3 -B scripts/litqa.py verify   --input /input --work /app/work --output /app/output
  python3 -B scripts/litqa.py grade    --output /app/output
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")
MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".txt", ".md", ".jsonl", ".csv", ".tsv", ".xml"}
VERDICTS = {"low_risk", "some_concerns", "high_risk", "insufficient_evidence", "not_applicable"}


# ---------------------------------------------------------------------------
# 文本归一化与定位
# ---------------------------------------------------------------------------

def normalize_text(s):
    """折叠空白、去首尾，用于鲁棒逐字匹配；不改动原文。"""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def sha256_hex(s):
    return hashlib.sha256(normalize_text(s).encode("utf-8")).hexdigest()


def _split_paragraphs(text):
    """按空行分段落，返回段落文本与全局起始偏移。"""
    paras, offsets, pos = [], [], 0
    for chunk in re.split(r"\n\s*\n", text):
        if chunk.strip() == "":
            pos += len(chunk) + (2 if "\n\n" in text[pos:pos + len(chunk) + 2] else 1)
            continue
        paras.append(chunk)
        offsets.append(pos)
        pos += len(chunk)
    return paras, offsets


def build_locator_index(text, source_id, region="fulltext"):
    """对单份文本生成稳定定位：source_id + region + paragraph/sentence + char_span。"""
    index = []
    paras, offsets = _split_paragraphs(text)
    for pi, para in enumerate(paras):
        p_start = offsets[pi]
        p_end = p_start + len(para)
        sentences = re.split(r"(?<=[。.!?？])\s+", para)
        if len(sentences) == 1:
            index.append({
                "source_id": source_id, "region": region, "paragraph": pi + 1,
                "sentence": 1, "char_start": p_start, "char_end": p_end,
                "text": para.strip(),
                "locator": "%s#%s.p%d.s1" % (source_id, region, pi + 1),
            })
            continue
        rel = 0
        for si, sent in enumerate(sentences):
            seg = sent.strip()
            if not seg:
                continue
            # 句子在段落内的相对起点：用 find 从 rel 起确定
            off = para.find(seg, rel)
            if off < 0:
                off = rel
            s_start = p_start + off
            s_end = s_start + len(seg)
            index.append({
                "source_id": source_id, "region": region, "paragraph": pi + 1,
                "sentence": si + 1, "char_start": s_start, "char_end": s_end,
                "text": seg,
                "locator": "%s#%s.p%d.s%d" % (source_id, region, pi + 1, si + 1),
            })
            rel = off + len(seg)
    return index


# ---------------------------------------------------------------------------
# 文本索引与逐字核验
# ---------------------------------------------------------------------------

def read_text(path, max_bytes=MAX_FILE_BYTES):
    errors = []
    p = Path(path)
    if not p.exists():
        return None, ["missing: %s" % p]
    if p.stat().st_size > max_bytes:
        return None, ["oversize: %s" % p]
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), errors
        except (UnicodeDecodeError, UnicodeError) as exc:
            errors.append("decode(%s)_failed: %s" % (enc, p))
    return None, errors


def load_text_index(dirs):
    """把若干目录下可读文本建立 {source_id: (text, path)} 索引。"""
    index, errors = {}, []
    roots = [Path(d) for d in dirs if Path(d).is_dir()]
    for root in roots:
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in TEXT_EXTS:
                continue
            text, errs = read_text(p)
            errors.extend(errs)
            if text is None:
                continue
            # source_id 去扩展名 + 去目录，保留可回指性
            source_id = p.name
            for ext in TEXT_EXTS:
                if source_id.lower().endswith(ext):
                    source_id = source_id[:-len(ext)]
                    break
            if source_id in index:
                source_id = "%s@%s" % (p.parent.name, source_id)
            index[source_id] = (text, str(p))
    return index, errors


def _resolve_span(ev, text):
    """据 evidence 的 locator/char_span 找到核验窗口。"""
    loc = ev.get("locator") or {}
    span = None
    if isinstance(loc, dict):
        cs, ce = loc.get("char_start"), loc.get("char_end")
        if isinstance(cs, int) and isinstance(ce, int) and 0 <= cs <= ce <= len(text):
            span = (cs, ce)
    cs = ev.get("char_span")
    if span is None and isinstance(cs, dict):
        a, b = cs.get("start"), cs.get("end")
        if isinstance(a, int) and isinstance(b, int) and 0 <= a <= b <= len(text):
            span = (a, b)
    if span is None:
        loc = ev.get("locator")
        if isinstance(loc, str):
            m = re.search(r"\.p(\d+)", loc)
            if m:
                paras, _ = _split_paragraphs(text)
                pi = int(m.group(1)) - 1
                if 0 <= pi < len(paras):
                    span = (0, 0)  # 段落核验采用全文匹配（下）
                    return paras[pi], (None, None)
    if span and span[1] is not None:
        return text[span[0]:span[1]], span
    return text, (None, None)


def verify_evidence_batch(evidence_list, text_index, idx_errors=None):
    """逐字核验 evidence_map；返回带 match/verified/quote_sha256 的记录列表。"""
    out = []
    for ev in evidence_list:
        quote = ev.get("quote")
        rec = {
            "evidence_id": ev.get("evidence_id"),
            "claim_id": ev.get("claim_id"),
            "quote_sha256": sha256_hex(quote or ""),
            "quote_normalized": normalize_text(quote),
            "match": "unverifiable",
            "verified": False,
            "note": "",
        }
        loc = ev.get("locator")
        source_id = None
        if isinstance(loc, dict):
            source_id = loc.get("file") or loc.get("source_id")
        elif isinstance(loc, str):
            source_id = loc.split("#", 1)[0] if "#" in loc else loc
        if not source_id:
            source_id = ev.get("source_id")
        text = None
        if source_id and source_id in text_index:
            text, _path = text_index[source_id]
        if text is None:
            # 模糊：按 basename 前缀匹配一次
            for sid, (t, _) in text_index.items():
                if source_id and (sid == source_id or sid.startswith(source_id) or source_id in sid):
                    text = t
                    rec["note"] = "source_resolved_fuzzy"
                    break
        if text is None:
            rec["match"] = "unverifiable"
            rec["note"] = "source_text_unavailable"
            out.append(rec)
            continue
        window, _ = _resolve_span(ev, text)
        q = normalize_text(quote)
        if q == "":
            rec["match"] = "none"
            rec["note"] = "empty_quote"
        elif q in normalize_text(window):
            rec["match"] = "verbatim"
            rec["verified"] = True
        elif q in normalize_text(text):
            rec["match"] = "verbatim"
            rec["verified"] = True
            rec["note"] = "matched_in_fulltext_not_span"
        elif re.search(re.escape(q), normalize_text(text), re.I):
            rec["match"] = "fuzzy"
            rec["note"] = "case_insensitive_match"
        else:
            rec["match"] = "none"
            rec["note"] = "quote_not_found"
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# 分级（阈值聚合，确定性）
# ---------------------------------------------------------------------------

def grade_assessment(assessment):
    """把 assessment 的 verdict 聚合为总体风险等级与理由。"""
    applicable, counts = [], {"low_risk": 0, "some_concerns": 0, "high_risk": 0,
                              "insufficient_evidence": 0}
    crit_high = crit_insuff = 0
    for item in assessment:
        v = item.get("verdict")
        if v == "not_applicable" or v is None:
            continue
        applicable.append(item)
        if v in counts:
            counts[v] += 1
        if item.get("critical") and v == "high_risk":
            crit_high += 1
        if item.get("critical") and v == "insufficient_evidence":
            crit_insuff += 1
    n = len(applicable)
    if n == 0:
        return {"grade": "unclear", "n_applicable": 0, "counts": counts,
                "rationale": "无可适用检查点，无法分级"}
    score = 2 * counts["high_risk"] + counts["some_concerns"] + counts["insufficient_evidence"]
    ratio = score / float(n)
    if crit_high >= 1:
        grade = "high"
    elif counts["insufficient_evidence"] > max(1, n // 2):
        grade = "unclear"
    elif ratio >= 0.5 or counts["high_risk"] >= 1:
        grade = "moderate"
    else:
        grade = "low"
    rationale = ("n=%d; score=%.2f; high=%d; some=%d; insuff=%d; crit_high=%d"
                 % (n, ratio, counts["high_risk"], counts["some_concerns"],
                    counts["insufficient_evidence"], crit_high))
    return {"grade": grade, "n_applicable": n, "counts": counts, "rationale": rationale,
            "thresholds": {"critical_high->high": 1, "insuff_dominant->unclear": ">n/2",
                           "moderate": "score/n>=0.5 or high>=1", "low": "otherwise"}}


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_locators(args):
    work = Path(args.work)
    index, errors = load_text_index([work])
    out_index = {}
    for sid, (text, _path) in index.items():
        out_index[sid] = build_locator_index(text, sid)
    atomic_write(Path(work) / "locator_index.json", {"sources": out_index, "errors": errors})
    print(json.dumps({"sources": len(out_index), "errors": len(errors)}, ensure_ascii=False))
    return 0


def cmd_verify(args):
    out = Path(args.output)
    result, errs = _load_json(Path(out) / "result.json")
    if result is None:
        print(json.dumps({"error": errs}, ensure_ascii=False))
        return 1
    index, idx_errors = load_text_index([args.input, args.work])
    checks = verify_evidence_batch(result.get("evidence_map", []), index, idx_errors)
    for ev in result.get("evidence_map", []):
        for c in checks:
            if c.get("evidence_id") == ev.get("evidence_id"):
                ev["match"] = c["match"]
                ev["verified"] = c["verified"]
                if "quote_sha256" not in ev:
                    ev["quote_sha256"] = c["quote_sha256"]
    result["verification_report"] = {"quote_checks": checks, "generated_utc": _utc()}
    atomic_write(Path(out) / "result.json", result)
    atomic_write(Path(args.work) / "verification_report.json", {"quote_checks": checks})
    print(json.dumps({"n_checked": len(checks),
                      "verbatim": sum(1 for c in checks if c["match"] == "verbatim"),
                      "none": sum(1 for c in checks if c["match"] == "none"),
                      "unverifiable": sum(1 for c in checks if c["match"] == "unverifiable")},
                     ensure_ascii=False))
    return 0


def cmd_grade(args):
    out = Path(args.output)
    result, errs = _load_json(Path(out) / "result.json")
    if result is None:
        print(json.dumps({"error": errs}, ensure_ascii=False))
        return 1
    g = grade_assessment(result.get("assessment", []))
    result.setdefault("uncertainty", [])
    result["risk_grade"] = g
    atomic_write(Path(out) / "result.json", result)
    print(json.dumps(g, ensure_ascii=False, indent=2))
    return 0


def _load_json(path):
    p = Path(path)
    if not p.exists():
        return None, ["missing: %s" % p]
    try:
        return json.loads(p.read_text(encoding="utf-8-sig")), []
    except Exception as exc:
        return None, ["bad_json: %s: %s" % (p, exc)]


def _utc():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(p))


def _build_parser():
    p = argparse.ArgumentParser(description="litqa-evidence 证据锚定工具")
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("locators")
    lp.add_argument("--work", default=DEFAULT_WORK)
    vp = sub.add_parser("verify")
    vp.add_argument("--input", default=DEFAULT_INPUT)
    vp.add_argument("--work", default=DEFAULT_WORK)
    vp.add_argument("--output", default=DEFAULT_OUTPUT)
    gp = sub.add_parser("grade")
    gp.add_argument("--output", default=DEFAULT_OUTPUT)
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.cmd == "locators":
        return cmd_locators(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    if args.cmd == "grade":
        return cmd_grade(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
