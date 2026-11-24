#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-staged 证据定位与分级工具（确定性、只依赖标准库）。

三个子命令：
  locators  : 对 /app/work 下的纯文本抽取件生成稳定段落/句子定位索引（locator 表）；
  verify    : 逐条核验 result.json assessment 中的 quote 是否逐字存在于所标 locator；
  grade     : 按 references/rubric.md 的阈值把 assessment 聚合为维度/总体风险等级。

定位语法（见 references/rubric.md）：
  {file_id}#p{N}        第 N 个段落（1 起）
  {file_id}#p{N}s{M}    第 N 个段落内的第 M 个句子（1 起）
  {file_id}#line{N}     文本第 N 行（元数据 JSON/纯文本，1 起）

用法：
  python3 -B scripts/litqa.py locators --input /app/work --out /app/work/locators.json
  python3 -B scripts/litqa.py verify   --result /app/output/result.json --index /app/work/locators.json
  python3 -B scripts/litqa.py grade    --result /app/output/result.json
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ALLOWED_VERDICT = {
    "confirmed", "partial", "absent", "insufficient", "not_applicable",
}
ALLOWED_POLARITY = {"favorable", "adverse", "neutral"}
RISK_LEVELS = ("low", "medium", "high", "insufficient_evidence")

_WS_RE = re.compile(r"\s+")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
# 中英文句末标点后跟空白/引号收尾的粗略切分；不做 NLP 级消歧（文档注明启发式）。
_SENT_SPLIT_RE = re.compile(r"(?<=[。．！？!?\.])\s+(?=[^\s])|(?<=[。．！？!?\.])$")


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_ws(text):
    """折叠所有空白为单空格；统一常见变体引号，便于 PDF 抽取误差下的逐字核验。"""
    if text is None:
        return ""
    t = str(text)
    t = t.replace("\u00a0", " ").replace("\u200b", "")
    for a, b in (("\u201c", '"'), ("\u201d", '"'), ("\u2018", "'"), ("\u2019", "'"),
                 ("\u2013", "-"), ("\u2014", "-"), ("\u2212", "-")):
        t = t.replace(a, b)
    return _WS_RE.sub(" ", t).strip()


def split_sentences(paragraph):
    """把段落按中英文句末标点粗略切句（启发式，只用于定位，不用于语义分析）。"""
    p = normalize_ws(paragraph)
    if not p:
        return []
    parts = _SENT_SPLIT_RE.split(p)
    return [s.strip() for s in parts if s and s.strip()]


def build_file_index(text):
    """把单文件文本切成段落与句子，返回稳定索引结构。"""
    blocks = [normalize_ws(b) for b in _PARAGRAPH_RE.split(text)]
    blocks = [b for b in blocks if b]
    paragraphs = []
    for b in blocks:
        sentences = split_sentences(b)
        paragraphs.append({"text": b, "sentences": sentences})
    lines = [normalize_ws(ln) for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return {"paragraphs": paragraphs, "lines": lines}


def sanitize_file_id(name):
    """由文件名生成稳定 file_id；空/冲突由调用方兜底。"""
    s = re.sub(r"[^0-9A-Za-z_\-.]", "_", name)
    return s or "file"


def build_index_for_files(paths):
    """为多个文本文件建索引；返回 (index, errors)。"""
    index = {}
    errors = []
    used = set()
    for p in paths:
        try:
            raw = Path(p).read_bytes()
        except OSError as exc:
            errors.append("read_failed: %s: %s" % (p, exc))
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("latin-1")
            except UnicodeDecodeError as exc:
                errors.append("decode_failed: %s: %s" % (p, exc))
                continue
        fid = sanitize_file_id(Path(p).stem)
        if fid in used:
            i = 2
            while ("%s_%d" % (fid, i)) in used:
                i += 1
            fid = "%s_%d" % (fid, i)
        used.add(fid)
        index[fid] = {
            "path": str(p),
            "file_id": fid,
            "index": build_file_index(text),
        }
    return index, errors


def _parse_locator(locator):
    """解析 locator，返回 (anchor_kind, n, m)。"""
    m = re.match(r"^#p(\d+)$", locator)
    if m:
        return "paragraph", int(m.group(1)), None
    m = re.match(r"^#p(\d+)s(\d+)$", locator)
    if m:
        return "sentence", int(m.group(1)), int(m.group(2))
    m = re.match(r"^#line(\d+)$", locator)
    if m:
        return "line", int(m.group(1)), None
    return None, None, None


def resolve_text(index, file_id, locator):
    """按 locator 取回文本片段；失败返回 (None, reason)。"""
    entry = index.get(file_id)
    if entry is None:
        return None, "unknown_file_id: %s" % file_id
    fi = entry.get("index", {})
    kind, n, m = _parse_locator(locator)
    if kind == "paragraph":
        paras = fi.get("paragraphs", [])
        if 1 <= n <= len(paras):
            return paras[n - 1]["text"], None
        return None, "paragraph_out_of_range: %s has %d paragraphs" % (file_id, len(paras))
    if kind == "sentence":
        paras = fi.get("paragraphs", [])
        if not (1 <= n <= len(paras)):
            return None, "paragraph_out_of_range: %s has %d paragraphs" % (file_id, len(paras))
        sents = paras[n - 1].get("sentences", [])
        if 1 <= m <= len(sents):
            return sents[m - 1], None
        return None, "sentence_out_of_range: p%d has %d sentences" % (n, len(sents))
    if kind == "line":
        lines = fi.get("lines", [])
        if 1 <= n <= len(lines):
            return lines[n - 1], None
        return None, "line_out_of_range: %s has %d lines" % (file_id, len(lines))
    return None, "unparseable_locator: %s" % locator


def verify_one(index, file_id, locator, quote):
    """核验单条 quote 是否逐字存在于所标 locator。

    返回 (status, detail)：
      pass  : quote 逐字（规范化后）存在于所标位置
      warn  : 所标位置未命中，但在同文件其他位置命中（定位漂移，需修正 locator）
      fail  : quote 在文件任何位置都未逐字命中（疑似编造/错引）
      skip  : quote/locator 为空（弃权，不判错）
    """
    q = normalize_ws(quote)
    lo = (locator or "").strip()
    if not q and not lo:
        return "skip", "弃权：quote 与 locator 均为空"
    if not q or not lo:
        return "fail", "quote 与 locator 必须成对出现（弃权应都为空）"
    if file_id is None or file_id not in index:
        return "fail", "unknown_file_id: %s" % file_id
    text, reason = resolve_text(index, file_id, lo)
    if text is not None and q in normalize_ws(text):
        return "pass", "命中 %s" % lo
    # 所标位置未命中：全文件兜底搜索，区分「定位漂移」与「引文不存在」。
    whole = _document_text(index, file_id)
    if whole is not None and q in normalize_ws(whole):
        return "warn", "所标 %s 未命中，但 quote 在同文件其他位置命中，需修正 locator（%s）" % (
            lo, reason or "定位可解析")
    return "fail", "quote 未在文件任何位置逐字命中（%s）" % (reason or "定位不匹配")


def _document_text(index, file_id):
    entry = index.get(file_id)
    if entry is None:
        return None
    paras = entry["index"].get("paragraphs", [])
    return "\n".join(p["text"] for p in paras)


# ---------------------------------------------------------------------------
# 命令：locators
# ---------------------------------------------------------------------------


def cmd_locators(args):
    root = Path(args.input)
    if not root.is_dir():
        print("FATAL: 输入目录不存在: %s" % root)
        return 2
    text_exts = {".txt", ".md", ".json", ".jsonl", ".xml", ".csv", ".tsv"}
    files = sorted(p for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in text_exts)
    if not files:
        print("WARNING: 目录下无文本文件: %s" % root)
        files = []
    index, errors = build_index_for_files(files)
    out = {"generated_at": utc_now(), "file_count": len(index), "errors": errors, "files": index}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("locators written: %s (files=%d, errors=%d)" % (args.out, len(index), len(errors)))
    return 0


# ---------------------------------------------------------------------------
# 命令：verify
# ---------------------------------------------------------------------------


def cmd_verify(args):
    with open(args.index, "r", encoding="utf-8") as fh:
        idx = json.load(fh)
    files = idx.get("files", {})
    index = {fid: {"path": v["path"], "index": v["index"]} for fid, v in files.items()}

    with open(args.result, "r", encoding="utf-8") as fh:
        result = json.load(fh)

    assessment = result.get("assessment", [])
    report = {"checked": 0, "pass": 0, "warn": 0, "fail": 0, "skip": 0, "items": []}
    for i, item in enumerate(assessment):
        file_id = item.get("source_id")
        lo = (item.get("locator") or "").strip()
        quote = item.get("quote") or ""
        status, detail = verify_one(index, file_id, lo, quote)
        report["checked"] += 1
        report[status] = report.get(status, 0) + 1
        report["items"].append({
            "index": i, "dimension": item.get("dimension"),
            "checkpoint": item.get("checkpoint"),
            "locator": lo, "source_id": file_id, "status": status, "detail": detail,
        })
        item["_verify_status"] = status

    # 回写 result.json（幂等：仅更新 _verify_status 与追加 change_log 一次）
    stamp = {"time_utc": utc_now(), "event": "verify_run",
             "detail": "pass=%d warn=%d fail=%d skip=%d" % (
                 report["pass"], report["warn"], report["fail"], report["skip"])}
    logs = result.get("change_log", [])
    if not any(x.get("event") == "verify_run" and x.get("detail") == stamp["detail"] for x in logs):
        logs.append(stamp)
        result["change_log"] = logs
    with open(args.result, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

    print("verify: checked=%d pass=%d warn=%d fail=%d skip=%d" % (
        report["checked"], report["pass"], report["warn"], report["fail"], report["skip"]))
    for it in report["items"]:
        if it["status"] in ("fail", "warn"):
            print("  [%s] #%d %s/%s: %s" % (
                it["status"], it["index"], it["dimension"], it["checkpoint"], it["detail"]))
    return 1 if report["fail"] else 0


# ---------------------------------------------------------------------------
# 命令：grade
# ---------------------------------------------------------------------------


def grade_assessment(assessment):
    """按 rubric.md 阈值聚合评估项 → (grades, dims)。"""
    dims = {}
    for item in assessment:
        if not isinstance(item, dict):
            continue
        dim = item.get("dimension") or "未分类"
        verdict = item.get("verdict")
        if verdict not in ALLOWED_VERDICT:
            verdict = "insufficient"
        polarity = item.get("polarity")
        if polarity not in ALLOWED_POLARITY:
            polarity = "neutral"
        critical = bool(item.get("critical", False))
        d = dims.setdefault(dim, {
            "total": 0, "critical": 0, "insufficient": 0, "critical_insufficient": 0,
            "critical_adverse_confirmed": 0, "critical_adverse_partial": 0,
            "critical_favorable_absent": 0, "noncritical_adverse_confirmed": 0,
            "critical_evaluated": 0, "clean_critical": 0,
        })
        if verdict == "not_applicable":
            continue
        d["total"] += 1
        if critical:
            d["critical"] += 1
        if verdict == "insufficient":
            d["insufficient"] += 1
            if critical:
                d["critical_insufficient"] += 1
            continue
        if critical:
            d["critical_evaluated"] += 1
            if polarity == "adverse":
                if verdict == "confirmed":
                    d["critical_adverse_confirmed"] += 1
                elif verdict == "partial":
                    d["critical_adverse_partial"] += 1
                elif verdict == "absent":
                    d["clean_critical"] += 1  # 未见偏倚 = 干净
            else:  # favorable / neutral
                if verdict == "confirmed":
                    d["clean_critical"] += 1  # 报告充分 = 干净
                elif verdict == "absent" and polarity == "favorable":
                    d["critical_favorable_absent"] += 1
        else:
            if polarity == "adverse" and verdict == "confirmed":
                d["noncritical_adverse_confirmed"] += 1

    grades = {}
    for dim, d in sorted(dims.items()):
        if d["total"] == 0:
            grade = "insufficient_evidence"
        elif d["insufficient"] > 0.5 * d["total"]:
            grade = "insufficient_evidence"
        elif d["critical_adverse_confirmed"] >= 1:
            grade = "high"
        elif (d["critical_adverse_partial"] >= 1 or d["critical_insufficient"] >= 1
              or d["critical_favorable_absent"] >= 1
              or d["noncritical_adverse_confirmed"] >= 1):
            grade = "medium"
        elif d["critical_evaluated"] > 0 and d["clean_critical"] == d["critical_evaluated"]:
            grade = "low"
        elif d["critical_evaluated"] == 0 and d["total"] > 0:
            grade = "low"
        else:
            grade = "medium"
        grades[dim] = {"grade": grade, "stats": d}

    return grades, dims


def _overall(grades):
    if not grades:
        return "insufficient_evidence"
    graded = {k: v["grade"] for k, v in grades.items()}
    insuf = [g for g in graded.values() if g == "insufficient_evidence"]
    if len(insuf) > len(graded) / 2:
        return "insufficient_evidence"
    order = {"low": 0, "medium": 1, "high": 2}
    worst = "low"
    for g in graded.values():
        if g in order and order[g] > order[worst]:
            worst = g
    return worst


def cmd_grade(args):
    with open(args.result, "r", encoding="utf-8") as fh:
        result = json.load(fh)
    assessment = result.get("assessment", [])
    grades, dims = grade_assessment(assessment)
    overall = _overall(grades)
    result["grades"] = {
        "summary": {"overall": overall, "dimensions": grades},
        "rules": "见 references/rubric.md 分级阈值；overall 取维度最差，超半数维度证据不足则 insufficient_evidence",
    }
    stamp = {"time_utc": utc_now(), "event": "grade_run", "detail": "overall=%s" % overall}
    logs = result.get("change_log", [])
    if not any(x.get("event") == "grade_run" and x.get("detail") == stamp["detail"] for x in logs):
        logs.append(stamp)
        result["change_log"] = logs
    with open(args.result, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"overall": overall, "dimensions": grades}, fh, ensure_ascii=False, indent=2)
    print("grade: overall=%s" % overall)
    for dim, g in sorted(grades.items()):
        print("  %s: %s" % (dim, g["grade"]))
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(prog="litqa.py",
                                     description="证据定位/逐字核验/阈值分级工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_l = sub.add_parser("locators", help="生成段落/句子定位索引")
    p_l.add_argument("--input", default="/app/work")
    p_l.add_argument("--out", default="/app/work/locators.json")
    p_l.set_defaults(func=cmd_locators)

    p_v = sub.add_parser("verify", help="核验 quote 逐字存在性")
    p_v.add_argument("--result", default="/app/output/result.json")
    p_v.add_argument("--index", default="/app/work/locators.json")
    p_v.add_argument("--out", default=None)
    p_v.set_defaults(func=cmd_verify)

    p_g = sub.add_parser("grade", help="按阈值聚合风险等级")
    p_g.add_argument("--result", default="/app/output/result.json")
    p_g.add_argument("--out", default=None)
    p_g.set_defaults(func=cmd_grade)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
