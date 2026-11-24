#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""screen.py - 确定性词面信号、去重聚类、基线排序脚手架。

0 次模型请求；只产出脚手架先验（base_score/dedupe_groups/lexical_hits），
绝不作为最终相关性判断。最终 relevance/tier/priority_score 由 Agent 依据材料填写。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_title(text):
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"\[\s*(preprint|version\s+\d+|v\d+)\s*\]", " ", t)
    t = re.sub(r"\((preprint|version\s+\d+|v\d+)\)", " ", t)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def token_set(text):
    return set(norm_title(text).split())


def jaccard(a, b):
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def looks_preprint(rec):
    t = (rec.get("title") or "") + " " + (rec.get("venue") or "") + " " + \
        (rec.get("source_type") or "")
    tl = t.lower()
    marks = ("preprint", "arxiv", "biorxiv", "medrxiv", "ssrn", "working paper",
             "in press", "v1", "version 1")
    return any(m in tl for m in marks)


def looks_published(rec):
    has_venue = bool(rec.get("venue"))
    has_doi = bool(rec.get("doi"))
    has_vol_pages = any(k in (rec.get("raw") or {}) for k in
                        ("volume", "pages", "issue", "journal"))
    return (has_venue or has_vol_pages) and has_doi


def make_dedupe_groups(records):
    """归一标题 + Jaccard>=0.9 聚类；组内裁定 canonical/duplicate/version_variant。"""
    groups = []
    used = set()
    for i, rec in enumerate(records):
        if i in used:
            continue
        ti = token_set(rec.get("title"))
        group = [i]
        for j in range(i + 1, len(records)):
            if j in used:
                continue
            tj = token_set(records[j].get("title"))
            sim = jaccard(ti, tj)
            same_doi = rec.get("doi") and rec.get("doi") == records[j].get("doi")
            if sim >= 0.9 or (same_doi and sim >= 0.5):
                group.append(j)
                used.add(j)
        used.add(i)
        if len(group) > 1:
            groups.append(group)
    out = []
    for group in groups:
        members = []
        for idx in group:
            r = records[idx]
            members.append({
                "record_id": r.get("record_id"),
                "title": r.get("title"),
                "doi": r.get("doi"),
                "venue": r.get("venue"),
                "source_type": r.get("source_type"),
                "is_preprint": looks_preprint(r),
                "is_published": looks_published(r),
            })
        # 裁定 canonical：优先 published 且非 preprint；其次有 DOI；再其次按出现顺序
        canonical_idx = group[0]
        for idx in group:
            r = records[idx]
            if looks_published(r) and not looks_preprint(r):
                canonical_idx = idx
                break
        if canonical_idx == group[0]:
            for idx in group:
                if records[idx].get("doi"):
                    canonical_idx = idx
                    break
        out.append({
            "members": members,
            "canonical_record_id": records[canonical_idx].get("record_id"),
            "rule": "prefer published-over-preprint, then DOI, then first-appearance",
            "note": "preprint vs published / 多版本，需 Agent 在步骤 5 复核并填 conflict/dedupe",
        })
    return out


def lexical_signal(rec, question_tokens, extra_terms):
    """确定性词面先验（仅 base_score，不是最终分）。"""
    text = " ".join(filter(None, [rec.get("title"), rec.get("abstract"),
                                  rec.get("venue"), " ".join(rec.get("authors") or [])]))
    tokens = token_set(text)
    if not question_tokens:
        base = 0.0
    else:
        inter = len(question_tokens & tokens)
        base = inter / len(question_tokens)
    hits = sorted(question_tokens & tokens)[:12]
    return {"base_score": round(min(1.0, base), 4), "lexical_hits": hits}


def main(argv=None):
    ap = argparse.ArgumentParser(description="文献筛选词面脚手架")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args(argv)

    npath = os.path.join(args.work, "case_normalized.json")
    if not os.path.isfile(npath):
        print("screen: missing %s (run discover.py first)" % npath)
        return 1
    with open(npath, "r", encoding="utf-8") as fh:
        cn = json.load(fh)

    records = cn.get("candidates", [])
    # 研究问题词面 + 纳排标准中的领域词作为先验词典
    qtext = (cn.get("research_question") or "") + " "
    crit = cn.get("criteria") or {}
    for key in ("inclusion", "exclusion", "raw"):
        items = crit.get(key) or []
        if isinstance(items, list):
            qtext += " ".join(str(x) for x in items) + " "
    question_tokens = token_set(qtext)
    # 过滤停用词，避免纯虚词命中
    stop = set("the a an of and or in on for to with we our this that these those "
               "study research paper evidence based using method objective aim "
               "inclusion exclusion criteria".split())
    question_tokens = {t for t in question_tokens if t not in stop and len(t) > 1}

    scored = []
    for rec in records:
        sig = lexical_signal(rec, question_tokens, None)
        scored.append({"record_id": rec.get("record_id"), **sig})

    dedupe_groups = make_dedupe_groups(records)

    scaffold = {
        "generated_at": now_iso(),
        "case_id": cn.get("case_id"),
        "base_prior": scored,
        "dedupe_groups": dedupe_groups,
        "tie_break": ["priority_score desc", "evidence_completeness desc", "year desc",
                      "norm_title asc", "record_id asc"],
        "warning": "base_score 是确定性词面先验，仅作检索/排序参考，不得直接作为最终 priority_score",
        "model_requests": 0,
    }
    spath = os.path.join(args.work, "scaffold.json")
    with open(spath, "w", encoding="utf-8") as fh:
        json.dump(scaffold, fh, ensure_ascii=False, indent=2)
    print("screen: candidates=%d dedupe_groups=%d lexical_prior=deterministic" %
          (len(records), len(dedupe_groups)))
    print("  wrote: %s" % spath)
    return 0


if __name__ == "__main__":
    sys.exit(main())