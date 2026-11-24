#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-audit 证据与分级核心库（纯标准库、确定性、无随机数与墙钟时间）。

职责：
  - locators：把抽取的纯文本切成稳定段落，生成可定位证据索引
              （locator = "<source>#para:N"，每条带逐字 text）；
  - verify  ：把 result.json 中每条 assessment 的 quote 与证据索引逐字核验；
  - absorb  ：把 Agent 产出的评价片段确定性地合并进 result.json；
  - grade   ：按 rubric 阈值把 verdict 聚合为维度等级与总体等级。

确定性约定：所有输出经 json.dump(sort_keys=True, ensure_ascii=False) 原子写盘，
列表项按规范键排序，不记录墙钟时间，同一输入两次运行产物逐字节一致。

用法（在 SKILL.md 所在目录下运行）：
  python3 -B scripts/litqa.py locators --input <file_or_dir> --work /app/work
  python3 -B scripts/litqa.py absorb --result /app/output/result.json --fragment <json>
  python3 -B scripts/litqa.py grade --result /app/output/result.json
  python3 -B scripts/litqa.py verify --index /app/work/evidence_index.json \
      --result /app/output/result.json --out /app/output/verification_report.json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过
TEXT_SUFFIXES = {".txt", ".md", ".jsonl", ".json", ".csv", ".xml"}

# 判定词表（受限取值，保证双人复核可比）
ALLOWED_VERDICTS = {
    "clear_risk",           # 明确存在缺陷/偏倚
    "possible_risk",        # 可能存在
    "no_risk_indicator",    # 未见迹象
    "insufficient_evidence"  # 证据不足，弃权
}

# 分级阈值（与 references/rubric.md 保持一致）
VERDICT_SCORE = {
    "clear_risk": 3.0,
    "possible_risk": 2.0,
    "insufficient_evidence": 1.0,
    "no_risk_indicator": 0.0,
}
UNASSESSABLE_FRACTION = 0.5   # 维度内弃权占比 >= 此值判 unassessable
LOW_CEIL = 0.75               # 平均分 < 0.75 -> low_risk
MODERATE_CEIL = 1.75          # 平均分 < 1.75 -> moderate_risk，否则 high_risk

CHANGE_LOG_ORDER = [
    "skeleton", "discover", "locators", "absorb", "grade", "verify",
    "validate", "finalize",
]


# ---------------------------------------------------------------------------
# 确定性 I/O
# ---------------------------------------------------------------------------

def atomic_write_json(path, data):
    """先写临时文件再 rename，避免半截 JSON；键排序保证确定性。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(str(tmp), str(p))


def read_json_lenient(path):
    """读 JSON；坏 JSON 返回 None，绝不抛异常。"""
    p = Path(path)
    if not p.is_file():
        return None, ["missing: %s" % p]
    try:
        raw = p.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            return None, ["oversize: %s" % p]
        return json.loads(raw.decode("utf-8-sig")), []
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return None, ["bad_json: %s: %s" % (p, exc)]


def read_text_lenient(path):
    """utf-8-sig 优先读取文本，坏编码降级 latin-1。"""
    p = Path(path)
    if not p.is_file():
        return None, ["missing: %s" % p]
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return None, ["oversize: %s" % p]
        raw = p.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return raw.decode(enc), []
            except UnicodeDecodeError:
                continue
        return None, ["undecodable: %s" % p]
    except OSError as exc:
        return None, ["read_failed: %s: %s" % (p, exc)]


def canonical_key(item):
    """把任意 JSON 值转成稳定排序键。"""
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def upsert_change_log(result, step, event, detail):
    """按固定顺序 upsert change_log 条目（幂等，重复运行不漂移）。"""
    entries = [e for e in result.get("change_log", [])
               if e.get("step") != step]
    entries.append({"step": step, "event": event, "detail": detail})
    order = {name: idx for idx, name in enumerate(CHANGE_LOG_ORDER)}
    entries.sort(key=lambda e: (order.get(e.get("step"), 999), canonical_key(e)))
    result["change_log"] = entries


def upsert_phase(result, phase, state, detail=""):
    """按阶段名 upsert runtime.phases（幂等）。"""
    phases = [p for p in result.get("runtime", {}).get("phases", [])
              if p.get("phase") != phase]
    entry = {"phase": phase, "state": state}
    if detail:
        entry["detail"] = detail
    phases.append(entry)
    phases.sort(key=lambda p: canonical_key(p.get("phase", "")))
    result.setdefault("runtime", {})["phases"] = phases


# ---------------------------------------------------------------------------
# locators：稳定段落定位
# ---------------------------------------------------------------------------

def normalize_ws(text):
    """折叠空白（换行/制表/空格 -> 单空格），用于逐字核验时容错。"""
    return re.sub(r"\s+", " ", text or "").strip()


def split_paragraphs(text):
    """按空行切段；若整篇无空行则按行切，保证短摘要也有细粒度定位。"""
    paras = re.split(r"\n\s*\n", text)
    if len(paras) == 1 and "\n" in text:
        paras = text.splitlines()
    return [p.strip() for p in paras if p.strip()]


def build_locators(text, source_id):
    """返回段落定位列表；char 偏移用于人工回到原文。"""
    locators = []
    cursor = 0
    for idx, para in enumerate(split_paragraphs(text), start=1):
        pos = text.find(para, cursor)
        if pos < 0:
            pos = cursor
        locators.append({
            "locator": "%s#para:%d" % (source_id, idx),
            "source": source_id,
            "para": idx,
            "char_start": pos,
            "char_end": pos + len(para),
            "text": para,
        })
        cursor = pos + len(para) if pos >= cursor else cursor
    return locators


def discover_text_files(target):
    """扫描文件或目录，返回文本文件路径列表（相对路径，确定性排序）。"""
    root = Path(target)
    files = []
    if root.is_file():
        files = [root]
    elif root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
                files.append(p)
    return files


def cmd_locators(args):
    """生成证据索引 evidence_index.json 与带锚点的可读文本。"""
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    all_locators = []
    degradations = []
    skipped = 0
    for f in discover_text_files(args.input):
        text, errs = read_text_lenient(f)
        if text is None:
            degradations.extend(errs)
            skipped += 1
            continue
        try:
            rel = os.path.relpath(str(f), str(args.input))
        except ValueError:
            rel = f.name
        locs = build_locators(text, rel.replace("\\", "/"))
        all_locators.extend(locs)
        # 写出带 [loc] 锚点的可读副本，便于 Agent 引用定位
        anchor_lines = []
        for l in locs:
            anchor_lines.append("[loc:%s]" % l["locator"])
            anchor_lines.append(l["text"])
            anchor_lines.append("")
        anchor_path = work / ("%s.located.txt" % f.stem)
        anchor_path.write_text("\n".join(anchor_lines), encoding="utf-8")
    index = {
        "generated_by": "litqa.locators",
        "deterministic": True,
        "source_count": len(discover_text_files(args.input)),
        "skipped": skipped,
        "degradations": degradations,
        "locators": all_locators,
    }
    out_index = Path(args.index) if getattr(args, "index", None) else \
        work / "evidence_index.json"
    atomic_write_json(out_index, index)
    print(json.dumps({"index": str(out_index),
                      "locator_count": len(all_locators),
                      "skipped": skipped}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# verify：逐字引文核验
# ---------------------------------------------------------------------------

def verify_entries(index, entries):
    """对每条 assessment 核验 quote 是否逐字存在于 locator 所指段落。"""
    locmap = {l["locator"]: l["text"] for l in index.get("locators", [])}
    report = []
    for e in entries:
        loc = e.get("locator", "")
        quote = e.get("quote", "") or ""
        dim = e.get("dimension", "")
        cp = e.get("checkpoint", "")
        if not loc or loc not in locmap:
            report.append({"dimension": dim, "checkpoint": cp, "locator": loc,
                           "quote": quote, "found": False,
                           "reason": "locator_not_found"})
            continue
        if not quote.strip():
            report.append({"dimension": dim, "checkpoint": cp, "locator": loc,
                           "quote": quote, "found": False,
                           "reason": "empty_quote"})
            continue
        text = locmap[loc]
        exact = quote in text
        found_ws = normalize_ws(quote) in normalize_ws(text)
        report.append({
            "dimension": dim, "checkpoint": cp, "locator": loc, "quote": quote,
            "found": found_ws, "exact": exact,
            "reason": "" if found_ws else "quote_not_found",
        })
    return report


def cmd_verify(args):
    """读取证据索引与 result.json，输出 verification_report.json。"""
    index, ierrs = read_json_lenient(args.index)
    if index is None:
        report = {"ok": False, "errors": ierrs, "entries": []}
        if getattr(args, "out", None):
            atomic_write_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        return
    result, rerrs = read_json_lenient(args.result)
    if result is None:
        report = {"ok": False, "errors": rerrs, "entries": []}
        if getattr(args, "out", None):
            atomic_write_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        return
    entries = result.get("assessment", [])
    ver = verify_entries(index, entries)
    found = sum(1 for v in ver if v.get("found"))
    report = {
        "ok": all(v.get("found") for v in ver) and bool(ver),
        "total": len(ver),
        "found": found,
        "missing": len(ver) - found,
        "entries": sorted(ver, key=canonical_key),
    }
    if getattr(args, "out", None):
        atomic_write_json(args.out, report)
    print(json.dumps({"total": report["total"], "found": found,
                      "missing": report["missing"]}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# absorb：确定性合并评价片段
# ---------------------------------------------------------------------------

LIST_KEYS = [
    "assessment", "candidates", "screening", "evidence_records", "omissions",
    "sources", "uncertainty", "pending_human", "errors", "degradations",
    "limitations",
]
SCALAR_KEYS = ["research_question", "search_scope", "method_note"]


def cmd_absorb(args):
    """把 fragment 中的评价内容确定性合并进 result.json。"""
    result, rerrs = read_json_lenient(args.result)
    if result is None:
        print(json.dumps({"ok": False, "errors": rerrs}, ensure_ascii=False))
        return
    frag, ferrs = read_json_lenient(args.fragment)
    if frag is None:
        print(json.dumps({"ok": False, "errors": ferrs}, ensure_ascii=False))
        return
    for key in LIST_KEYS:
        if isinstance(frag.get(key), list):
            merged = [dict(x) if isinstance(x, dict) else x for x in frag[key]]
            merged.sort(key=canonical_key)
            result[key] = merged
    for key in SCALAR_KEYS:
        if key in frag:
            result[key] = frag[key]
    upsert_change_log(result, "absorb", "merge_fragment",
                      "merged %d list field(s)" % sum(
                          1 for k in LIST_KEYS if isinstance(frag.get(k), list)))
    upsert_phase(result, "absorb", "done")
    atomic_write_json(args.result, result)
    print(json.dumps({"ok": True, "result": args.result}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# grade：阈值聚合分级
# ---------------------------------------------------------------------------

def grade_dimension(checkpoints):
    """按 rubric 阈值把同一维度的 verdict 列表聚合为维度等级。"""
    if not checkpoints:
        return {"grade": "unassessable", "rationale": "no checkpoints",
                "checkpoints_scored": 0}
    scored = [c for c in checkpoints if c.get("verdict") in VERDICT_SCORE]
    n = len(checkpoints)
    if not scored:
        return {"grade": "unassessable",
                "rationale": "no valid verdicts", "checkpoints_scored": 0}
    unassessable = sum(1 for c in scored
                       if c["verdict"] == "insufficient_evidence")
    if unassessable / n >= UNASSESSABLE_FRACTION:
        return {"grade": "unassessable",
                "rationale": "abstained %.0f%% of checkpoints"
                % (100.0 * unassessable / n),
                "checkpoints_scored": len(scored)}
    hard_fail = any(c.get("critical") and c["verdict"] == "clear_risk"
                    for c in scored)
    mean = sum(VERDICT_SCORE[c["verdict"]] for c in scored) / len(scored)
    if hard_fail:
        return {"grade": "high_risk",
                "rationale": "critical checkpoint flagged clear_risk",
                "checkpoints_scored": len(scored)}
    if mean < LOW_CEIL:
        grade = "low_risk"
    elif mean < MODERATE_CEIL:
        grade = "moderate_risk"
    else:
        grade = "high_risk"
    return {"grade": grade, "rationale": "mean risk score %.2f" % mean,
            "checkpoints_scored": len(scored)}


def cmd_grade(args):
    """聚合维度等级与总体等级，确定性写回 result.json。"""
    result, rerrs = read_json_lenient(args.result)
    if result is None:
        print(json.dumps({"ok": False, "errors": rerrs}, ensure_ascii=False))
        return
    by_dim = {}
    for e in result.get("assessment", []):
        by_dim.setdefault(e.get("dimension", "unspecified"), []).append(e)
    grades = []
    for dim in sorted(by_dim):
        g = grade_dimension(by_dim[dim])
        g["dimension"] = dim
        g["supporting_checkpoints"] = sorted(
            [e.get("checkpoint", "") for e in by_dim[dim]])
        grades.append(g)
    grades.sort(key=canonical_key)
    result["grades"] = grades
    if grades and all(g["grade"] == "unassessable" for g in grades):
        overall = "unassessable"
    elif any(g["grade"] == "high_risk" for g in grades):
        overall = "high_risk"
    elif any(g["grade"] == "moderate_risk" for g in grades):
        overall = "moderate_risk"
    else:
        overall = "low_risk"
    result["overall_grade"] = overall
    upsert_change_log(result, "grade", "aggregate_grades",
                      "overall_grade=%s" % overall)
    upsert_phase(result, "grade", "done", "overall_grade=%s" % overall)
    atomic_write_json(args.result, result)
    print(json.dumps({"ok": True, "overall_grade": overall,
                      "grades": grades}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description="litqa-audit 证据定位 / 逐字核验 / 合并 / 分级")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp_loc = sub.add_parser("locators", help="生成稳定段落证据索引")
    sp_loc.add_argument("--input", required=True)
    sp_loc.add_argument("--work", default="/app/work")
    sp_loc.add_argument("--index", default=None)

    sp_abs = sub.add_parser("absorb", help="确定性合并评价片段")
    sp_abs.add_argument("--result", required=True)
    sp_abs.add_argument("--fragment", required=True)

    sp_gr = sub.add_parser("grade", help="阈值聚合分级")
    sp_gr.add_argument("--result", required=True)

    sp_vf = sub.add_parser("verify", help="逐字引文核验")
    sp_vf.add_argument("--index", required=True)
    sp_vf.add_argument("--result", required=True)
    sp_vf.add_argument("--out", default=None)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "locators":
        cmd_locators(args)
    elif args.cmd == "absorb":
        cmd_absorb(args)
    elif args.cmd == "grade":
        cmd_grade(args)
    elif args.cmd == "verify":
        cmd_verify(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
