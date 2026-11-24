#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-evidence 证据与分级工具（locators / verify / grade）。

只依赖 Python 标准库，并 import 同目录 main.py 的公共函数。

用法（在 SKILL.md 所在目录下运行，相对路径）：
  python3 -B scripts/litqa.py locators --input /input --work /app/work
  python3 -B scripts/litqa.py verify --input /input --work /app/work --output /app/output
  python3 -B scripts/litqa.py grade --output /app/output
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import main  # noqa: E402


def norm(s):
    """折叠空白用于逐字引文比较（不改变字面字符，只归一空白）。"""
    return " ".join((s or "").split())


# ---------------------------------------------------------------------------
# 文本抽取与段落定位
# ---------------------------------------------------------------------------


def _pdftotext(src, dst):
    """尽力用 pdftotext 抽取 PDF；不可用则返回 False（调用方记 degradation）。"""
    try:
        subprocess.run(["pdftotext", "-enc", "UTF-8", src, dst],
                       capture_output=True, timeout=180)
        return Path(dst).exists() and Path(dst).stat().st_size > 0
    except Exception:
        return False


def extract_texts(input_dir, work_dir):
    """把 /input 材料转为 (source_id, text) 列表；PDF 尝试 pdftotext。"""
    found, errs = main.discover_input(input_dir)
    texts = []
    for p in found["materials"]:
        ext = p.suffix.lower()
        try:
            rel = str(p.relative_to(Path(input_dir))).replace("\\", "/")
        except ValueError:
            rel = p.name
        rel = rel.replace("\\", "/")
        if ext in (".txt", ".md", ".csv", ".xml", ".html"):
            text, e2 = main.read_text(str(p))
            if text is not None:
                texts.append({"source_id": rel, "text": text, "kind": "text", "path": str(p)})
            else:
                errs.extend(e2)
        elif ext == ".pdf":
            dst = Path(work_dir) / (p.stem + ".pdftotext.txt")
            if _pdftotext(str(p), str(dst)):
                text, e2 = main.read_text(str(dst))
                if text is not None:
                    texts.append({"source_id": rel, "text": text, "kind": "pdf_text", "path": str(p)})
                else:
                    errs.extend(e2)
            else:
                errs.append("pdf_not_extracted: %s" % p)
        else:
            errs.append("unsupported_material: %s" % p)
    return texts, errs


def split_paragraphs(text):
    """按空行分块；返回 [(block, char_start, char_end)]，块内保留原换行。"""
    blocks = re.split(r"\n\s*\n", text)
    paras = []
    offset = 0
    for b in blocks:
        if not b.strip():
            continue
        start = text.find(b, offset)
        if start < 0:
            start = offset
        paras.append((b, start, start + len(b)))
        offset = start + len(b)
    return paras


def build_locators(texts):
    """为每个文本段落生成稳定定位：<source_id>#p<序号>。"""
    locators = []
    for t in texts:
        for i, (block, start, end) in enumerate(split_paragraphs(t["text"]), 1):
            locators.append({
                "locator": "%s#p%d" % (t["source_id"], i),
                "source_id": t["source_id"],
                "paragraph": i,
                "text_norm": norm(block),
                "char_start": start,
                "char_end": end,
            })
    return locators


def run_locators(input_dir, work_dir):
    """locators 命令：抽取文本并写 /app/work/{texts,locators}.json。"""
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    texts, errs = extract_texts(input_dir, work_dir)
    locators = build_locators(texts)
    main.atomic_write_json(str(work / "texts.json"), texts)
    main.atomic_write_json(str(work / "locators.json"), locators)
    print(json.dumps({"texts": len(texts), "locators": len(locators), "errors": errs},
                     ensure_ascii=False, indent=2))
    return texts, locators, errs


# ---------------------------------------------------------------------------
# 逐字证据核验
# ---------------------------------------------------------------------------


def _haystacks_for(loc, loc_map, src_map):
    """返回该 locator 对应的全部可比对文本（段落级或文件级）。"""
    haystacks = []
    if loc in loc_map:
        haystacks.append(loc_map[loc])
    elif "#" in loc:
        base, token = loc.split("#", 1)
        nums = re.findall(r"\d+", token)
        base_texts = [loc_map[l] for l in loc_map if l.startswith(base + "#p")]
        if nums:
            want = set(int(n) for n in nums)
            base_texts = [loc_map[l] for l in loc_map
                          if l.startswith(base + "#p") and int(re.search(r"p(\d+)", l).group(1)) in want]
        haystacks.extend(base_texts)
    elif loc in src_map:
        haystacks.append(src_map[loc])
    return haystacks


def verify_quotes(result, locators):
    """核验 assessment 与 sources 中每条 quote 是否逐字出现在所标 locator。"""
    loc_map = {l["locator"]: l["text_norm"] for l in locators}
    src_map = {}
    for l in locators:
        src_map.setdefault(l["source_id"], "")
    checks = {"pass": 0, "fail": 0, "unchecked": 0, "failures": [], "unverifiable": []}

    def check_one(kind, idx, item):
        loc = item.get("locator")
        quote = item.get("quote")
        if not loc or not quote:
            checks["unchecked"] += 1
            return
        nq = norm(quote)
        if not nq:
            checks["unchecked"] += 1
            return
        hay = _haystacks_for(loc, loc_map, src_map)
        if not hay:
            checks["fail"] += 1
            checks["failures"].append({"id": "%s[%d]" % (kind, idx), "locator": loc,
                                       "quote_preview": nq[:80], "reason": "locator 无对应文本"})
            return
        if any(nq in h for h in hay):
            checks["pass"] += 1
        else:
            checks["fail"] += 1
            checks["failures"].append({"id": "%s[%d]" % (kind, idx), "locator": loc,
                                       "quote_preview": nq[:80], "reason": "quote 未逐字匹配"})

    for i, item in enumerate(result.get("assessment") or []):
        check_one("assessment", i, item)
    for i, item in enumerate(result.get("sources") or []):
        check_one("sources", i, item)
    return checks


def run_verify(input_dir, work_dir, output_dir):
    """verify 命令：读 result.json，核验后写 /app/work/verify_report.json。"""
    work = Path(work_dir)
    loc_file = work / "locators.json"
    if loc_file.exists():
        locators, errs = main.load_json_lenient(str(loc_file))
        locators = locators or []
        if errs:
            locators = []
    else:
        texts, _ = extract_texts(input_dir, work_dir)
        locators = build_locators(texts)
    out = Path(output_dir)
    rp = out / "result.json"
    result, _ = main.load_json_lenient(str(rp))
    result = result if isinstance(result, dict) else {}
    report = verify_quotes(result, locators)
    report["locator_count"] = len(locators)
    main.atomic_write_json(str(work / "verify_report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


# ---------------------------------------------------------------------------
# 分级聚合（阈值与 references/rubric.md 严格一致）
# ---------------------------------------------------------------------------

CRITICAL_BIAS_KEYWORDS = ("选择偏倚", "混杂", "selection", "confounding")
THRESHOLDS = {
    "high": "任一 critical 条目 verdict ∈ {contradicted, not_reported}，或关键偏倚领域（选择/混杂）verdict=present",
    "some": "任一非关键条目 verdict ∈ {contradicted, not_reported}，或 present≥1（非关键），或 possible≥2，或 insufficient≥3",
    "unclear": "无可用评价条目，或 critical 条目中 evidence 不足占比 ≥ 50%",
    "low": "其余情况：关键条目均有证据支持，无明确/可能存在偏倚，无未报告关键项",
}


def _grade_level(bad_crit, bad_any, bias_present_crit, bias_present, bias_possible,
                 bias_insufficient, crit_total, crit_insuff, item_total):
    if bad_crit > 0 or bias_present_crit > 0:
        return "high", "存在 critical 条目不支持/未报告，或关键偏倚领域明确存在偏倚"
    if bad_any > 0 or bias_present > 0 or bias_possible >= 2 or bias_insufficient >= 3:
        return "some", "存在非关键条目不支持/未报告，或偏倚可能/未报告证据不足达到阈值"
    if item_total == 0 or (crit_total > 0 and crit_insuff / crit_total >= 0.5):
        return "unclear", "无可用评价条目或关键条目证据不足占比过半，不足以分级"
    return "low", "关键条目均有证据支持，且无明确/可能存在偏倚、无未报告关键项"


def grade_result(result):
    """在 result 上写入 grade 并追加 change_log；确定性，无随机数。"""
    items = result.get("assessment") or []
    bad_crit = bad_any = bias_present = bias_present_crit = bias_possible = bias_insufficient = 0
    crit_total = crit_insuff = 0
    for it in items:
        dim = str(it.get("dimension", "")).lower()
        verdict = str(it.get("verdict", "")).lower()
        crit = bool(it.get("critical"))
        if dim == "bias_risk":
            cp = str(it.get("checkpoint", ""))
            is_crit = any(k in cp for k in CRITICAL_BIAS_KEYWORDS)
            if verdict == "present":
                bias_present += 1
                if is_crit:
                    bias_present_crit += 1
            elif verdict == "possible":
                bias_possible += 1
            elif verdict == "insufficient":
                bias_insufficient += 1
        else:
            if crit:
                crit_total += 1
            if verdict in ("contradicted", "not_reported"):
                bad_any += 1
                if crit:
                    bad_crit += 1
            if crit and verdict in ("insufficient_evidence", "insufficient"):
                crit_insuff += 1
    level, reason = _grade_level(bad_crit, bad_any, bias_present_crit, bias_present,
                                 bias_possible, bias_insufficient, crit_total,
                                 crit_insuff, len(items))
    result["grade"] = {
        "risk_level": level,
        "rationale": reason,
        "counts": {
            "critical_bad": bad_crit, "any_bad": bad_any,
            "bias_present": bias_present, "bias_present_critical": bias_present_crit,
            "bias_possible": bias_possible, "bias_insufficient": bias_insufficient,
            "critical_total": crit_total, "critical_insufficient": crit_insuff,
            "item_total": len(items),
        },
        "thresholds": THRESHOLDS,
    }
    main.append_change_log(result, "grade_computed", "P3", "按阈值聚合风险等级",
                           {"risk_level": level})
    return result


def run_grade(output_dir):
    """grade 命令：读 result.json → 聚合分级 → 原子写回。"""
    out = Path(output_dir)
    rp = out / "result.json"
    result, errs = main.load_json_lenient(str(rp))
    if not isinstance(result, dict):
        print(json.dumps({"error": "result.json 不可读", "detail": errs}, ensure_ascii=False))
        return 1
    result = grade_result(result)
    main.atomic_write_json(str(rp), result)
    print(json.dumps(result.get("grade"), ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli(argv=None):
    parser = argparse.ArgumentParser(description="litqa-evidence 证据与分级工具")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("locators")
    p.add_argument("--input", default=main.DEFAULT_INPUT)
    p.add_argument("--work", default=main.DEFAULT_WORK)
    p = sub.add_parser("verify")
    p.add_argument("--input", default=main.DEFAULT_INPUT)
    p.add_argument("--work", default=main.DEFAULT_WORK)
    p.add_argument("--output", default=main.DEFAULT_OUTPUT)
    p = sub.add_parser("grade")
    p.add_argument("--output", default=main.DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.command == "locators":
        run_locators(args.input, args.work)
        return 0
    if args.command == "verify":
        run_verify(args.input, args.work, args.output)
        return 0
    if args.command == "grade":
        return run_grade(args.output)
    return 2


if __name__ == "__main__":
    sys.exit(cli())
