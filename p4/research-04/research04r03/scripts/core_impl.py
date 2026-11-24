#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyze.py —— 阶段 P1：确定性机械基线抽取。

从 /app/work/materials/ 的逐字副本抽取：年份/标题/被引、年度分布表、时间窗、
候选术语（含首末年份）、空白候选句、冲突候选句、代表作候选（按 kind）。
全部候选标 machine=true、confidence=low，供后续深度分析核验。

确定性：不随机、不依赖墙钟；所有排序与序列化固定；输出字节级可复现。
产物：result.json（增量合并机械字段）、report.md、evidence_map.json、
tables/documents.csv、tables/year_distribution.csv、work/determinism/mechanical.json。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys

PIPELINE_VERSION = "1.0.0"

# —— 年份抽取 ——
YEAR_RE = re.compile(r"(19|20)\d{2}")
META_YEAR_KEYS = ("year", "publication_year", "publicationYear", "pub_year", "pubdate",
                  "publication_date", "published_year", "date_published", "date",
                  "published", "publishedDate", "出版年", "年份")
META_TITLE_KEYS = ("title", "Title", "篇名", "题名")
CITE_RE = re.compile(r"(?:cited\s*by|citations?|citation\s*count|被引|引用[次数量]?)"
                     r"[^0-9]{0,15}(\d{1,7})", re.IGNORECASE)

# —— 空白候选句 ——
GAP_PATTERNS = re.compile(
    r"future\s+work|further\s+(research|study|investigation)|remains?\s+(open|unclear|unknown|"
    r"unresolved|poorly\s+understood)|open\s+question|not\s+yet|still\s+(lacking|unknown|unclear)|"
    r"unresolved|poorly\s+understood|limitation|gap|needs?\s+further|warrants?\s+(further|investigation)|"
    r"未来工作|进一步研究|尚待|尚未|仍存在|仍不清楚|研究局限|局限|不足|未知|未解决|有待|缺乏|"
    r"尚未解决|需要进一步|仍然缺乏|空白", re.IGNORECASE)

# —— 冲突候选句 ——
CONFLICT_PATTERNS = re.compile(
    r"however|in\s+contrast|versus|vs\.|contradict|discrepan|on\s+the\s+other\s+hand|"
    r"conflicting|inconsistent|debated|controversial|"
    r"然而|与此相反|相反|矛盾|不一致|争议|存在分歧|不一致的结果", re.IGNORECASE)

# —— 代表作分类词 ——
METHOD_TERMS = {"method", "framework", "approach", "algorithm", "model", "architecture",
                "pipeline", "protocol", "方法", "框架", "算法", "模型", "架构", "流程", "方案"}
BENCH_TERMS = {"benchmark", "dataset", "corpus", "evaluation", "baseline", "gold standard",
               "state-of-the-art", "sota", "基准", "数据集", "语料", "评测", "金标准", "白板"}

# —— 术语提取 ——
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "have",
    "has", "been", "not", "but", "its", "can", "using", "use", "used", "based", "which",
    "into", "these", "their", "than", "also", "such", "more", "our", "they", "will",
    "each", "about", "most", "new", "within", "between", "however", "thus", "therefore",
    "while", "after", "before", "where", "when", "through", "during", "among", "than",
    "could", "should", "would", "may", "might", "both", "some", "any", "all", "only",
    "via", "per", "due", "well", "less", "over", "under", "against", "although", "because",
}

METHOD_RE = re.compile("|".join(sorted((re.escape(t) for t in METHOD_TERMS), key=len, reverse=True)),
                       re.IGNORECASE)
BENCH_RE = re.compile("|".join(sorted((re.escape(t) for t in BENCH_TERMS), key=len, reverse=True)),
                      re.IGNORECASE)
KW_RE = re.compile(r"(?:keywords?|关键词|主题词|subject\s*terms?)\s*[:：]\s*(.+)", re.IGNORECASE)
QUOTE_RE = re.compile(r"[\"“”‘’「」『』]([^\"“”‘’「」『』]{4,40})[\"“”‘’「」『』]")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{3,}")


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": "))


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: str):
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                return fh.read(), enc
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, "r", encoding="latin-1", errors="replace", newline="") as fh:
        return fh.read(), "latin-1(replace)"


def extract_year(text: str, rel: str):
    """按优先级抽取年份，返回 (year_or_None, source)。确定性：同文本同结果。"""
    meta = None
    if rel.endswith((".json", ".jsonl")):
        try:
            meta = json.loads(text)
        except Exception:  # noqa: BLE001
            meta = None
    if isinstance(meta, dict):
        for key in META_YEAR_KEYS:
            val = meta.get(key)
            m = YEAR_RE.search(str(val)) if val is not None else None
            if m:
                y = int(m.group(0))
                if 1900 <= y <= 2100:
                    return y, "metadata:%s" % key
        for key in META_TITLE_KEYS:
            if isinstance(meta.get(key), str) and meta.get(key).strip():
                # 标题通常不含年份，这里仅占位，继续下方文本抽取
                break
    # tier2: 前 2000 字符内的“括号年份”或 arXiv 式年份
    head = text[:2000]
    for m in re.finditer(r"(?:^|[\s,;(\[（])((?:19|20)\d{2})(?:[\s,;)\]}）]|$)", head):
        y = int(m.group(1))
        if 1990 <= y <= 2100:
            return y, "header"
    # tier3: 全文首个 4 位年份
    m = YEAR_RE.search(text)
    if m:
        y = int(m.group(0))
        if 1900 <= y <= 2100:
            return y, "body"
    return None, "unknown"


def extract_title(text: str, rel: str):
    if rel.endswith((".json", ".jsonl")):
        try:
            meta = json.loads(text)
            if isinstance(meta, dict):
                for key in META_TITLE_KEYS:
                    val = meta.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()[:300]
        except Exception:  # noqa: BLE001
            pass
    for line in text.splitlines():
        s = re.sub(r"^[#>*\s]+", "", line).strip()
        if len(s) >= 8 and not s.lower().startswith(("http", "doi:", "arxiv", "figure", "table")):
            return s[:300]
    return None


def extract_citations(text: str):
    m = CITE_RE.search(text)
    return int(m.group(1)) if m else None


def split_sentences(text: str, max_chars: int = 800):
    sents, buf = [], []
    for ch in text:
        buf.append(ch)
        if ch in "。！？!?;；" or (ch == "\n" and len(buf) > 200):
            sents.append("".join(buf))
            buf = []
    if buf:
        sents.append("".join(buf))
    return [s.strip() for s in sents if s.strip()]


def locate_span(text: str, snippet: str):
    idx = text.find(snippet)
    if idx < 0 and len(snippet) > 60:
        idx = text.find(snippet[:60])
    if idx < 0:
        return None
    start = text.count("\n", 0, idx) + 1
    end = text.count("\n", 0, idx + len(snippet)) + 1
    return start, end


def extract_terms(text: str):
    """返回 [(term, variants, frequency), ...] 的确定性候选列表（有界）。"""
    lower = text.lower()
    terms = {}

    def add(term):
        t = term.strip()
        if not (2 <= len(t) <= 40):
            return
        if t in terms:
            terms[t]["freq"] += 1
        else:
            terms[t] = {"freq": 1, "variants": []}

    # 关键词行
    for m in KW_RE.finditer(text):
        for part in re.split(r"[,;，；、/|]", m.group(1)):
            add(part.strip())
    # 引号短语
    for m in QUOTE_RE.finditer(text):
        add(m.group(1))
    # 英文词（去除停用词与纯数字）
    counts = {}
    for w in WORD_RE.findall(lower):
        if w in STOPWORDS or w.isdigit():
            continue
        counts[w] = counts.get(w, 0) + 1
    for w, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:150]:
        if c >= 2:
            add(w)
    # 归一：按 (freq desc, term asc) 排序，取前 300
    items = sorted(terms.items(), key=lambda kv: (-kv[1]["freq"], kv[0]))[:300]
    return [(term, v["freq"]) for term, v in items]


def build_windows(distribution):
    years = [d["year"] for d in distribution if d["year"] is not None]
    years = sorted(set(years))
    if not years:
        return []
    span = years[-1] - years[0]
    if span <= 8:
        return [{"start": y, "end": y, "years": [y],
                 "doc_count": next((d["count"] for d in distribution if d["year"] == y), 0),
                 "basis": "annual（跨度<=8年，逐年切片）"} for y in years]
    n = 4
    width = max(1, math.ceil((span + 1) / n))
    out, start = [], years[0]
    while start <= years[-1]:
        end = min(start + width - 1, years[-1])
        ws = [y for y in years if start <= y <= end]
        if ws:
            cnt = sum(next((d["count"] for d in distribution if d["year"] == y), 0) for y in ws)
            out.append({"start": ws[0], "end": ws[-1], "years": ws, "doc_count": cnt,
                        "basis": "等宽%d窗（跨度>8年，宽度=%d年）" % (n, width)})
        start = end + 1
    return out


def build_evidence(kind, claim, quote, rel, text):
    span = locate_span(text, quote)
    return {
        "id": None,  # 稍后统一编号
        "claim": claim,
        "locator": "%s#L%s-L%s" % (rel, span[0], span[1]) if span else "%s#(locator-unresolved)" % rel,
        "quote": quote,
        "source_file": rel,
        "status": "reported",
        "confidence": "low",
        "kind": kind,
        "machine": True,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P1: deterministic mechanical extraction")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    manifest_path = os.path.join(args.work, "manifest.json")
    result_path = os.path.join(args.output, "result.json")
    if not os.path.exists(manifest_path):
        print("[analyze] 缺少 manifest.json，请先运行 scripts/prepare.py", file=sys.stderr)
        return 2
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    if os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as fh:
            try:
                result = json.load(fh)
            except Exception:  # noqa: BLE001
                result = {}
    else:
        result = {}

    result.setdefault("case_id", "unknown")
    result.setdefault("status", "partial")

    docs, evidence = [], []
    doc_texts = {}
    term_first = {}   # term -> {first_year, last_year, doc_ids}
    term_freq = {}
    gap_cands, conflict_cands = [], []

    mat_map = manifest.get("mat_map", {})
    for rel, mat_name in sorted(mat_map.items()):
        mat_path = os.path.join(args.work, "materials", mat_name)
        text, enc = read_text(mat_path)
        year, year_src = extract_year(text, rel)
        title = extract_title(text, rel)
        cites = extract_citations(text)
        doc_id = "D%03d" % (len(docs) + 1)
        doc_texts[doc_id] = text
        docs.append({"doc_id": doc_id, "path": rel, "title": title, "year": year,
                     "year_source": year_src, "citations": cites,
                     "n_chars": len(text), "encoding": enc})

        # 术语
        for term, freq in extract_terms(text):
            key = term.lower()
            term_freq.setdefault(key, {"term": term, "freq": 0, "doc_ids": []})
            term_freq[key]["freq"] += freq
            if doc_id not in term_freq[key]["doc_ids"]:
                term_freq[key]["doc_ids"].append(doc_id)
            if key not in term_first:
                term_first[key] = {"term": term, "first_year": year, "last_year": year}
            else:
                if year is not None:
                    if term_first[key]["first_year"] is None or year < term_first[key]["first_year"]:
                        term_first[key]["first_year"] = year
                    if term_first[key]["last_year"] is None or year > term_first[key]["last_year"]:
                        term_first[key]["last_year"] = year

        # 候选句
        for sent in split_sentences(text):
            if GAP_PATTERNS.search(sent):
                gap_cands.append(build_evidence("gap_candidate",
                                                "机械识别的研究空白候选句", sent, rel, text))
            if CONFLICT_PATTERNS.search(sent):
                conflict_cands.append(build_evidence("conflict_candidate",
                                                     "机械识别的冲突证据候选句", sent, rel, text))

    # 年度分布
    dist = {}
    for d in docs:
        if d["year"] is not None:
            dist.setdefault(d["year"], []).append(d["doc_id"])
    distribution = [{"year": y, "count": len(ids), "doc_ids": sorted(ids)}
                    for y, ids in sorted(dist.items())]

    # 时间窗
    windows = build_windows(distribution)

    # 术语映射
    term_mapping = []
    for key, info in sorted(term_first.items(), key=lambda kv: kv[0]):
        tf = term_freq.get(key, {})
        term_mapping.append({
            "canonical": info["term"],
            "variants": [info["term"]],
            "first_year": info["first_year"],
            "last_year": info["last_year"],
            "frequency": tf.get("freq", 0),
            "doc_ids": sorted(tf.get("doc_ids", [])),
            "machine": True,
            "note": "机械候选术语，需人工归并同义/变体后核验",
        })

    # 代表作候选
    rep_cands = []
    peak_years = set()
    if distribution:
        maxc = max(d["count"] for d in distribution)
        peak_years = {d["year"] for d in distribution if d["count"] >= maxc}

    cites_list = [d["citations"] for d in docs if d["citations"] is not None]
    cite_p90 = sorted(cites_list)[int(0.9 * (len(cites_list) - 1))] if len(cites_list) >= 5 else None

    for d in docs:
        text = doc_texts.get(d["doc_id"], "")
        # origin：该 doc 是否某术语的首现
        origin_terms = [info["term"] for key, info in term_first.items()
                        if info["first_year"] == d["year"] and key in term_freq
                        and d["doc_id"] in term_freq[key]["doc_ids"]]
        reasons = []
        if origin_terms:
            reasons.append(("origin", "候选术语首现：%s" % "、".join(origin_terms[:3])))
        if METHOD_RE.search(text):
            reasons.append(("method", "含方法类术语"))
        if BENCH_RE.search(text):
            reasons.append(("benchmark", "含基准/数据集类术语"))
        if cite_p90 is not None and d["citations"] is not None and d["citations"] >= cite_p90:
            reasons.append(("milestone", "被引数位于语料前10%%（>=%d）" % cite_p90))
        if d["year"] in peak_years:
            reasons.append(("milestone", "年份 %d 处于产出峰值" % d["year"]))
        for kind, rationale in reasons:
            rep_cands.append({"doc_id": d["doc_id"], "title": d["title"], "year": d["year"],
                              "kind": kind, "rationale": rationale, "machine": True,
                              "evidence_ids": []})

    # 编号 evidence_map
    evidence = gap_cands + conflict_cands
    for i, ev in enumerate(evidence, 1):
        ev["id"] = "E%03d" % i

    # 汇总机械字段写入 result
    result["time_trend"]["unit_note"] = "年份口径待核验：机械抽取按 %s；在线发表/见刊/预印本口径由深度分析在 protocol.date_definition_note 写明。" % (
        "metadata->header->body 优先级")
    result["time_trend"]["distribution"] = distribution
    result["time_trend"]["windows"] = windows
    result["topic_evolution"]["term_mapping"] = term_mapping
    result["representative_works"]["criteria"] = [
        "origin：某候选术语的首现文献（覆盖长尾方法首创）",
        "method：含方法类术语的文献",
        "benchmark：含基准/数据集类术语的文献",
        "milestone：被引位于语料前10%%或处于产出峰值年份",
        "以上均为机械候选，需按 kind 核验并写明口径，禁止只用总被引排序",
    ]
    result["representative_works"]["stages"] = [
        {"stage": "candidates", "period": None, "basis": "机械候选（待按阶段分桶核验）",
         "works": rep_cands},
    ]
    result["research_gaps"]["gaps"] = [
        {"id": "GAP-CAND", "type": "open_question", "description": "机械识别的空白候选句，需核验为 open_question/method_failure/unresolved_conflict/coverage_absence 之一",
         "exclusion_evidence": "机械候选，尚未写检索式+排除条件，待深度分析补 exclusion_evidence",
         "evidence_ids": [ev["id"] for ev in gap_cands], "machine": True},
    ]
    result["evidence_map"] = evidence
    result["conflicts"] = conflict_cands and [
        {"items": [ev["id"] for ev in conflict_cands], "resolution": "未决",
         "note": "机械识别冲突候选，需两方都记录并给出分辨率或保持未决"}
    ] or []
    result["change_log"] = result.get("change_log", []) + [{
        "time": "T+180s", "action": "analyze.mechanical",
        "reason": "确定性机械抽取落盘，供深度分析核验",
        "before": "skeleton", "after": "time_trend/topic_evolution/representative_works/research_gaps 填机械候选",
    }]

    # 更新 sources 的年份/标题/被引
    for s in result.get("sources", []):
        if s.get("kind") != "material":
            continue
        for d in docs:
            if d["path"] == s.get("path"):
                s["year"] = d["year"]
                s["title"] = d["title"]
                s["citations"] = d["citations"]
                s["doc_id"] = d["doc_id"]
                break

    # canonical hash
    result.setdefault("determinism", {})
    result["determinism"]["canonical_sha256"] = ""
    body = canonical_json(result)
    result["determinism"]["canonical_sha256"] = sha256_of_text(body)
    with open(result_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(result))
        fh.write("\n")

    # evidence_map.json
    with open(os.path.join(args.output, "evidence_map.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json({"evidence_map": evidence}))
        fh.write("\n")

    # tables/*.csv（确定性：行排序、lineterminator="\n"）
    csv.register_dialect("unix", delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    with open(os.path.join(args.output, "tables", "documents.csv"), "w", encoding="utf-8-sig",
              newline="") as fh:
        w = csv.writer(fh, dialect="unix")
        w.writerow(["doc_id", "path", "title", "year", "year_source", "citations", "n_chars"])
        for d in docs:
            w.writerow([d["doc_id"], d["path"], d["title"] or "", d["year"] or "",
                        d["year_source"], d["citations"] or "", d["n_chars"]])
    with open(os.path.join(args.output, "tables", "year_distribution.csv"), "w",
              encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, dialect="unix")
        w.writerow(["year", "count", "doc_ids"])
        for d in distribution:
            w.writerow([d["year"], d["count"], ";".join(d["doc_ids"])])

    # report.md
    report = io.StringIO()
    report.write("# 研究前沿与趋势分析 —— 机械基线报告（machine=true）\n\n")
    report.write("> 本报告由 scripts/analyze.py 确定性生成；所有结论均为机械候选，"
                 "需经深度分析核验后方可作为正式结论。年份口径待核验。\n\n")
    report.write("## 年度分布\n\n")
    if distribution:
        report.write("| 年份 | 产出量 | 文档 |\n|-|-|-|\n")
        for d in distribution:
            report.write("| %s | %d | %s |\n" % (d["year"], d["count"], ";".join(d["doc_ids"])))
    else:
        report.write("无年份元数据，distribution 为空（见 pending_human）。\n")
    report.write("\n## 时间窗\n\n")
    for wd in windows:
        report.write("- %d–%d（%d 篇；%s）\n" % (wd["start"], wd["end"], wd["doc_count"], wd["basis"]))
    report.write("\n## 候选术语（前 50）\n\n| 术语 | 首年 | 末年 | 频次 |\n|-|-|-|-|\n")
    for tm in term_mapping[:50]:
        report.write("| %s | %s | %s | %d |\n" % (tm["canonical"], tm["first_year"] or "-",
                                                   tm["last_year"] or "-", tm["frequency"]))
    report.write("\n## 空白候选句（%d）\n\n" % len(gap_cands))
    for ev in gap_cands:
        report.write("- %s — `%s`\n" % (ev["locator"], ev["quote"][:120]))
    report.write("\n## 冲突候选句（%d）\n\n" % len(conflict_cands))
    for ev in conflict_cands:
        report.write("- %s — `%s`\n" % (ev["locator"], ev["quote"][:120]))
    report.write("\n## 代表作候选（%d）\n\n" % len(rep_cands))
    for rc in rep_cands:
        report.write("- %s | %s | %s | %s | %s\n" % (rc["doc_id"], rc["title"] or "-",
                                                      rc["year"] or "-", rc["kind"], rc["rationale"]))
    with open(os.path.join(args.output, "report.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(report.getvalue())

    # mechanical.json 侧车（供 selftest --determinism 复跑比对）
    mechanical = {
        "pipeline_version": PIPELINE_VERSION,
        "input_fingerprint": manifest.get("input_fingerprint"),
        "sources": result.get("sources", []),
        "distribution": distribution,
        "windows": windows,
        "term_mapping": term_mapping,
        "gap_candidates": gap_cands,
        "conflict_candidates": conflict_cands,
        "representative_candidates": rep_cands,
    }
    det_dir = os.path.join(args.work, "determinism")
    os.makedirs(det_dir, exist_ok=True)
    mech_path = os.path.join(det_dir, "mechanical.json")
    with open(mech_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(mechanical))
        fh.write("\n")
    with open(os.path.join(det_dir, "mechanical.sha256"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(sha256_of_text(canonical_json(mechanical)))
        fh.write("\n")

    print("[analyze] docs=%d distribution=%d terms=%d gap=%d conflict=%d rep=%d" % (
        len(docs), len(distribution), len(term_mapping), len(gap_cands),
        len(conflict_cands), len(rep_cands)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
