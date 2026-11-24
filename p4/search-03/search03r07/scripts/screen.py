#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""screen.py — 确定性词面信号、去重聚类、基线排序脚手架。

职责（阶段 3）：
  1) 读 case_normalized.json 与当前 result.json
  2) 对每条候选计算确定性词面信号（研究问题/纳排条件词面重叠、摘要/年份/DOI/venue 完整度）
  3) 规范化标题、检测 preprint/published 版本与近似重复，聚类去重
  4) 写 scaffold.json（base_score 为词面先验，仅供 Agent 参考，不得当最终分数）
  5) 回填 result.json.results 的元数据（保证 coverage 完整）

仅标准库，Python 3.7+。任何异常下退出码 0 并写降级记录。
"""
import argparse
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"
STOPWORDS = {"a", "an", "the", "and", "or", "of", "for", "in", "on", "to", "with",
             "is", "are", "was", "were", "be", "by", "as", "at", "from", "using",
             "study", "analysis", "effect", "effects", "the", "into", "their", "its",
             "this", "that", "these", "those"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        print(f"screen: cannot load {path}: {e}")
        return None


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def norm_text(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokens(s):
    return [t for t in norm_text(s).split() if t and t not in STOPWORDS]


def _norm_title(title):
    return norm_text(title)


def term_set(*parts):
    toks = set()
    for p in parts:
        if p is None:
            continue
        if isinstance(p, list):
            for x in p:
                toks.update(tokens(x))
        elif isinstance(p, dict):
            toks.update(tokens(json.dumps(p, ensure_ascii=False)))
        else:
            toks.update(tokens(p))
    return toks


def lexical_signals(rec, question_terms):
    title_tok = set(tokens(rec.get("title")))
    abst_tok = set(tokens(rec.get("abstract")))
    # overlap fraction vs question terms
    denom = len(title_tok | abst_tok) or 1
    hit = len((title_tok | abst_tok) & question_terms)
    overlap = hit / max(1, len(question_terms)) if question_terms else 0.0
    title_hit = len(title_tok & question_terms) / max(1, len(question_terms)) if question_terms else 0.0
    has_abstract = bool(rec.get("abstract"))
    has_doi = bool(rec.get("doi"))
    has_year = rec.get("year") is not None
    has_authors = bool(rec.get("authors"))
    venue = (rec.get("venue") or "").lower()
    url = (rec.get("url") or "").lower()
    is_preprint = any(k in venue + " " + url for k in ("arxiv", "preprint", "biorxiv", "medrxiv", "ssrn"))
    is_review = any(k in norm_text(rec.get("title") + " " + (rec.get("abstract") or "") + " " + venue)
                    for k in ("review", "meta-analysis", "meta analysis", "systematic", "survey"))
    completeness = sum([has_abstract, has_doi, has_year, has_authors]) / 4.0
    return {
        "overlap": round(overlap, 4),
        "title_hit": round(title_hit, 4),
        "has_abstract": has_abstract,
        "has_doi": has_doi,
        "has_year": has_year,
        "has_authors": has_authors,
        "is_preprint": is_preprint,
        "is_review": is_review,
        "completeness": round(completeness, 4),
        "norm_title": _norm_title(rec.get("title")),
        "title_tokens": sorted(title_tok),
    }


def base_score(sig):
    # 词面先验：重叠 + 完整度，绝不代替语义判断，仅作参考顺序
    return round(min(1.0, 0.55 * sig["overlap"] + 0.25 * sig["completeness"] + 0.10 * sig["title_hit"]), 4)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def cluster_duplicates(records):
    """Deterministic dedup: same normalized title / near-dup title / preprint-vs-published."""
    groups = []
    used = set()
    n = len(records)
    for i in range(n):
        if i in used:
            continue
        a = records[i]
        used.add(i)
        members = [a["record_id"]]
        for j in range(i + 1, n):
            if j in used:
                continue
            b = records[j]
            ta, tb = set(a["_sig"]["title_tokens"]), set(b["_sig"]["title_tokens"])
            sim = jaccard(ta, tb)
            same_norm = a["_sig"]["norm_title"] and a["_sig"]["norm_title"] == b["_sig"]["norm_title"]
            preprint_pair = (a["_sig"]["is_preprint"] != b["_sig"]["is_preprint"])
            if same_norm or (sim >= 0.82 and preprint_pair) or sim >= 0.92:
                members.append(b["record_id"])
                used.add(j)
        if len(members) > 1:
            # canonical: prefer published (non-preprint) then highest completeness then smallest record_id
            ordered = sorted(members, key=lambda rid: (
                1 if _rec(records, rid)["_sig"]["is_preprint"] else 0,
                -_rec(records, rid)["_sig"]["completeness"],
                rid,
            ))
            canonical = ordered[0]
            groups.append({
                "group_id": f"dup_{len(groups) + 1:03d}",
                "kind": "version_dup" if any(
                    _rec(records, r)["_sig"]["is_preprint"] for r in members) else "near_dup",
                "records": ordered,
                "canonical": canonical,
                "decision": "keep canonical; mark others as duplicate_of canonical (preprint 与正式版本视为同一研究)",
            })
    return groups


def _rec(records, rid):
    for r in records:
        if r["record_id"] == rid:
            return r
    return records[0]


def _backfill(result, records):
    """Ensure result.results covers every candidate and metadata is complete."""
    existing = {r.get("record_id"): r for r in result.get("results", []) if isinstance(r, dict)}
    results = []
    for rec in records:
        entry = existing.get(rec["record_id"])
        if entry is None:
            entry = {}
        for key in ("record_id", "title", "authors", "year", "venue", "doi", "url", "source_type"):
            if entry.get(key) is None and rec.get(key) is not None:
                entry[key] = rec[key]
        entry.setdefault("record_id", rec["record_id"])
        entry.setdefault("relevance", "unknown")
        entry.setdefault("tier", "pending")
        entry.setdefault("priority_score", None)
        entry.setdefault("rank", None)
        entry.setdefault("reasons", [])
        entry.setdefault("evidence", [])
        entry.setdefault("confidence", "low")
        entry.setdefault("criteria_matched", [])
        entry.setdefault("conflict", None)
        entry.setdefault("dedupe", None)
        results.append(entry)
    result["results"] = results
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="screen: lexical signals, dedup clustering, scaffold")
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"screen: cannot create dirs: {e}")
        return 0

    norm = _load(work_dir / "case_normalized.json") or {}
    result = _load(output_dir / "result.json") or {"case_id": norm.get("case_id"), "results": []}
    candidates = norm.get("candidates") or []
    degradations = result.setdefault("degradations", [])

    if not candidates:
        scaffold = {"case_id": norm.get("case_id"), "records": [], "dedupe_groups": [],
                    "tie_break": "tier(retain<pending<exclude) then priority_score desc, evidence_completeness desc, year desc, norm_title asc, record_id asc",
                    "note": "无候选，无可计算信号。"}
        _save(work_dir / "scaffold.json", scaffold)
        _save(output_dir / "result.json", result)
        print("screen: no candidates; scaffold written.")
        return 0

    question_terms = term_set(norm.get("question"), norm.get("inclusion"))
    records = []
    for rec in candidates:
        if not isinstance(rec, dict):
            rec = {}
        sig = lexical_signals(rec, question_terms)
        rec["_sig"] = sig
        rec["base_score"] = base_score(sig)
        records.append(rec)

    dedupe_groups = cluster_duplicates(records)

    scaffold = {
        "case_id": norm.get("case_id"),
        "records": [
            {"record_id": r["record_id"], "base_score": r["base_score"], "signals": r["_sig"]}
            for r in records
        ],
        "dedupe_groups": dedupe_groups,
        "tie_break": "tier(retain<pending<exclude) then priority_score desc, evidence_completeness desc, year desc, norm_title asc, record_id asc",
        "note": "base_score 为词面先验，仅供 Agent 参考，禁止直接当最终 priority_score。",
        "generated_at": now_iso(),
    }
    _save(work_dir / "scaffold.json", scaffold)

    # backfill result.json coverage + dedupe_groups
    result = _backfill(result, records)
    result["dedupe_groups"] = dedupe_groups
    result.setdefault("change_log", []).append(
        {"ts": now_iso(), "step": "screen", "action": "lexical_signals_and_dedupe"})
    result.setdefault("status_notes", []).append(
        f"screen：计算 {len(records)} 条词面信号，发现 {len(dedupe_groups)} 个重复/版本簇（仅先验，最终裁定由 Agent 完成）。")
    _save(output_dir / "result.json", result)
    print(f"screen: signals={len(records)} dedupe_groups={len(dedupe_groups)} "
          f"degradations={len(degradations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())