#!/usr/bin/env python3
"""research-frontier-audit — 阶段2：确定性机械基线。

读取 /app/work/manifest.json 与 /app/work/materials/，抽取：
  documents[] / term_index[] / candidates[] / windows[]  -> figure_model.json
  tables/documents.csv, tables/year_distribution.csv
  evidence_map.json, report.md
并把机器基线合并进 result.json（四类产物 + evidence_map + sources，machine=true）。
只依赖 Python 标准库；重复运行结果逐字节一致。
"""
import os
import sys
import re
import json
import csv
import argparse
import datetime
import collections

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}
MAX_BYTES = 2 * 1024 * 1024

STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on",
             "with", "by", "is", "are", "was", "were", "be", "been", "as",
             "at", "from", "that", "this", "these", "those", "we", "our",
             "their", "its", "it", "not", "no", "can", "may", "will",
             "which", "who", "whom", "how", "what", "when", "where", "but",
             "if", "then", "than", "so", "such", "into", "over", "under",
             "about", "between", "through", "during", "using", "based",
             "also", "has", "have", "had", "do", "does", "did",
             "however", "although", "though", "despite", "therefore", "thus",
             "while", "because", "since", "whereas", "still", "yet", "among",
             "within", "without", "other", "more", "most", "one", "two",
             "three", "new", "used", "use", "results", "result", "data",
             "paper", "study", "method", "methods", "show", "shows", "shown",
             "first", "second", "third", "well", "abstract", "introduction",
             "conclusion", "related", "work", "works"}

YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
SENT_SPLIT_RE = re.compile(r"(?<=[。．.!?！？;；])\s*")

PATTERNS = {
    "future_work": re.compile(r"future\s+work|further\s+(work|research|study)|remains?\s+to\s+be|有待|今后|未来工作|进一步研究|还需进一步|尚需|仍需"),
    "limitation": re.compile(r"limitation|we\s+did\s+not|未考虑|局限|不足|无法|受限|限制|没有考虑|不能"),
    "unknown": re.compile(r"unknown|unclear|not\s+yet\s+known|尚不清楚|未知|缺乏|尚未|no\s+evidence|未有证据"),
    "conflict": re.compile(r"however|in\s+contrast|versus|contradict|discrepan|不一致|相反|矛盾|冲突|争议"),
    "gap": re.compile(r"research\s+gap|little\s+is\s+known|no\s+study|缺乏研究|少有研究|未见|尚未见|空白|缺口"),
}
KIND_TITLE_RE = {
    "benchmark": re.compile(r"benchmark|dataset|corpus|evaluation", re.I),
    "method": re.compile(r"method|framework|model|algorithm|approach|system|architecture", re.I),
    "milestone": re.compile(r"survey|review|state.?of.?the.?art|breakthrough|large.?scale", re.I),
}


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_safe(path):
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None, None, "oversize"
    except OSError as e:
        return None, None, "read_error:" + str(e)
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc), enc, None
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace", "decode_partial"


def load_json_safe(path):
    text, _e, err = read_text_safe(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "bad_json:" + str(e)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def find_year_meta(record):
    for k in ("year", "pub_year", "published_year", "publication_year", "date", "年份", "出版年"):
        v = record.get(k)
        if isinstance(v, str):
            m = YEAR_RE.search(v)
            if m and 1900 <= int(m.group(1)) <= 2100:
                return int(m.group(1)), "metadata"
        if isinstance(v, (int, float)):
            y = int(v)
            if 1900 <= y <= 2100:
                return y, "metadata"
    return None, None


def find_title_meta(record):
    for k in ("title", "标题", "name"):
        v = record.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def find_citation_meta(record):
    for k in ("cited_by", "citations", "citation_count", "被引", "引用次数", "cited_by_count"):
        v = record.get(k)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            m = re.search(r"\d+", v)
            if m:
                return int(m.group(0))
    return None


def json_to_records(obj):
    records = []
    if isinstance(obj, list):
        for it in obj:
            if isinstance(it, dict):
                records.append(it)
            else:
                records.append({"text": str(it)})
    elif isinstance(obj, dict):
        for k in ("records", "items", "documents", "results", "papers", "data"):
            v = obj.get(k)
            if isinstance(v, list):
                records = [x if isinstance(x, dict) else {"text": str(x)} for x in v]
                break
        if not records:
            records = [obj]
    return records


def record_text(record):
    parts = []
    for k in ("title", "abstract", "fulltext", "full_text", "text", "summary", "snippet",
              "标题", "摘要", "全文", "正文", "内容"):
        v = record.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    if not parts:
        for v in record.values():
            if isinstance(v, str) and v.strip():
                parts.append(v)
    return "\n".join(parts)


def split_lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def find_year_plain(text):
    """标题/页眉优先，其次全文最常见年份；找不到返回 None。"""
    head = text[:800]
    m = YEAR_RE.search(head)
    if m and 1900 <= int(m.group(1)) <= 2100:
        return int(m.group(1)), "title_header"
    counts = collections.Counter()
    for mm in YEAR_RE.finditer(text):
        y = int(mm.group(1))
        if 1900 <= y <= 2100:
            counts[y] += 1
    if counts:
        return counts.most_common(1)[0][0], "body_frequent"
    return None, "missing"


def find_citation_plain(text):
    for pat in (re.compile(r"(?:cited\s+by|Cited\s+by)\s*[:：]?\s*(\d+)"),
                re.compile(r"(?:citations?|Citations?)\s*[:：]?\s*(\d+)"),
                re.compile(r"被引\s*[:：]?\s*(\d+)"),
                re.compile(r"引用\s*[:：]?\s*(\d+)\s*次")):
        m = pat.search(text)
        if m:
            return int(m.group(1))
    return None


def first_title_plain(text, fallback):
    for line in split_lines(text):
        s = line.strip()
        if 3 <= len(s) <= 240 and (re.search(r"[A-Za-z]", s) or CJK_RE.search(s)):
            return s
    return fallback


def find_line_for_year(lines, year):
    for i, ln in enumerate(lines, 1):
        if str(year) in ln:
            return i, ln
    return 1, lines[0] if lines else ""


def normalize_term(t):
    t = t.strip()
    t = t.lower()
    return t


def extract_terms(text, top_n=30):
    """返回 [(term, count)]，英文单语 + 中文 2-8 字串，去停用词。"""
    cnt = collections.Counter()
    for m in WORD_RE.finditer(text):
        w = m.group(0).lower()
        if w not in STOPWORDS and len(w) >= 3:
            cnt[w] += 1
    for m in CJK_RUN_RE.finditer(text):
        s = m.group(0)
        cnt[s] += 1
    return cnt.most_common(top_n)


def propose_windows(years):
    ys = sorted(set(y for y in years if y is not None))
    if not ys:
        return []
    windows = []
    start = ys[0]
    prev = ys[0]
    for y in ys[1:]:
        if y - prev >= 3:
            windows.append((start, prev))
            start = y
        prev = y
    windows.append((start, prev))
    return windows


def main(argv=None):
    ap = argparse.ArgumentParser(description="阶段2：机械基线抽取")
    ap.add_argument("--input", default=DEFAULTS["input"])
    ap.add_argument("--output", default=DEFAULTS["output"])
    ap.add_argument("--work", default=DEFAULTS["work"])
    args = ap.parse_args(argv)

    started = utcnow()
    manifest, merr = load_json_safe(os.path.join(args.work, "manifest.json"))
    if merr:
        manifest = {"files": []}
        print("manifest 不可读：%s，仅按空清单继续" % merr, file=sys.stderr)

    materials_dir = os.path.join(args.work, "materials")
    errors, degradations = [], []

    documents = []
    for e in manifest.get("files", []):
        if e.get("kind") != "text" or e.get("status") != "read":
            continue
        mat_path = os.path.join(args.work, e.get("material_file", ""))
        text, enc, terr = read_text_safe(mat_path)
        if text is None:
            errors.append("mat_read:%s:%s" % (e.get("material_file"), terr))
            continue
        lines = split_lines(text)
        line_count = len(lines)
        mat_rel = e.get("material_file", "materials/mat_%03d.txt" % e.get("index", 0))

        stripped = text.lstrip()
        if stripped[:1] in ("[", "{"):
            obj, jerr = None, None
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as ex:
                jerr = str(ex)
            if obj is not None:
                records = json_to_records(obj)
                if len(records) > 500:
                    degradations.append("%s JSON 记录 %d>500，仅取前 500" % (mat_rel, len(records)))
                    records = records[:500]
                for ri, rec in enumerate(records):
                    rtext = record_text(rec)
                    year, ysrc = find_year_meta(rec)
                    if year is None:
                        year, ysrc = find_year_plain(rtext)
                    title = find_title_meta(rec) or first_title_plain(rtext, mat_rel)
                    cit = find_citation_meta(rec)
                    if cit is None:
                        cit = find_citation_plain(rtext)
                    doc_id = "%sR%02d" % (e.get("doc_id", "D%03d" % e.get("index", 0)), ri)
                    loc = "%s#record%d" % (mat_rel, ri)
                    documents.append({
                        "doc_id": doc_id, "source_file": mat_rel, "locator": loc,
                        "title": title, "year": year, "year_source": ysrc,
                        "citation_count": cit, "char_count": len(rtext),
                        "is_json_record": True, "machine": True,
                    })
                continue

        year, ysrc = find_year_plain(text)
        title = first_title_plain(text, mat_rel)
        cit = find_citation_plain(text)
        documents.append({
            "doc_id": e.get("doc_id", "D%03d" % e.get("index", 0)),
            "source_file": mat_rel, "locator": "%s#L1-L%d" % (mat_rel, line_count),
            "title": title, "year": year, "year_source": ysrc,
            "citation_count": cit, "char_count": len(text),
            "is_json_record": False, "machine": True,
        })

    documents.sort(key=lambda d: d["doc_id"])
    if not documents:
        degradations.append("无可读文本材料：PDF 未抽取或输入为空")

    # term_index（跨文档首末年份）
    term_occur = collections.defaultdict(lambda: {"docs": [], "count": 0})
    doc_by_id = {d["doc_id"]: d for d in documents}
    for d in documents:
        mat_path = os.path.join(args.work, d["source_file"])
        text, _e, _t = read_text_safe(mat_path)
        if text is None:
            continue
        if d.get("is_json_record"):
            # 记录型文档：读取对应 JSON 再取 record 文本，避免整文件重复计数
            try:
                obj = json.loads(text.lstrip())
                recs = json_to_records(obj)
                idx = int(d["doc_id"].rsplit("R", 1)[1])
                rtext = record_text(recs[idx]) if idx < len(recs) else ""
            except Exception:
                rtext = text
        else:
            rtext = text
        for term, c in extract_terms(rtext):
            term_occur[term]["count"] += c
            if d["doc_id"] not in term_occur[term]["docs"]:
                term_occur[term]["docs"].append(d["doc_id"])

    term_index = []
    for term, info in sorted(term_occur.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        ys = [doc_by_id[x]["year"] for x in info["docs"] if x in doc_by_id and doc_by_id[x]["year"] is not None]
        term_index.append({
            "canonical": term, "variants": [], "count": info["count"],
            "first_year": min(ys) if ys else None, "last_year": max(ys) if ys else None,
            "doc_ids": sorted(info["docs"]), "machine": True,
        })

    # candidates（候选句）
    candidates = []
    for d in documents:
        mat_path = os.path.join(args.work, d["source_file"])
        text, _e, _t = read_text_safe(mat_path)
        if text is None:
            continue
        if d.get("is_json_record"):
            try:
                obj = json.loads(text.lstrip())
                recs = json_to_records(obj)
                idx = int(d["doc_id"].rsplit("R", 1)[1])
                rtext = record_text(recs[idx]) if idx < len(recs) else ""
            except Exception:
                rtext = text
        else:
            rtext = text
        for ln, line in enumerate(split_lines(rtext), 1):
            for sent in SENT_SPLIT_RE.split(line):
                s = sent.strip()
                if not s:
                    continue
                for kind, pat in PATTERNS.items():
                    m = pat.search(s)
                    if m:
                        loc = "%s#L%d" % (d["source_file"], ln)
                        candidates.append({
                            "kind": kind, "doc_id": d["doc_id"], "locator": loc,
                            "quote": s[:500], "matched_pattern": m.group(0), "machine": True,
                        })
                        break  # 一句只记最相关一类
    candidates.sort(key=lambda c: (c["doc_id"], c["locator"], c["kind"]))

    # windows
    windows = propose_windows([d["year"] for d in documents])
    windows_out = []
    for ws, we in windows:
        ids = [d["doc_id"] for d in documents if d["year"] is not None and ws <= d["year"] <= we]
        windows_out.append({"period": "%d-%d" % (ws, we), "doc_count": len(ids), "doc_ids": ids})

    # year distribution
    dist = collections.Counter(d["year"] for d in documents if d["year"] is not None)
    distribution = []
    for y in sorted(dist):
        ids = [d["doc_id"] for d in documents if d["year"] == y]
        distribution.append({"year": y, "count": len(ids), "doc_ids": sorted(ids)})

    # representative works candidates（按窗口、kind 启发式）
    stages = []
    for ws, we in windows:
        wdocs = [d for d in documents if d["year"] is not None and ws <= d["year"] <= we]
        if not wdocs:
            continue
        works = []
        for d in sorted(wdocs, key=lambda x: (x["year"] if x["year"] is not None else 9999, x["doc_id"])):
            kind = "origin"
            if d.get("citation_count") is not None:
                kind = "milestone"
            for k, pat in KIND_TITLE_RE.items():
                if d.get("title") and pat.search(d["title"]):
                    kind = k
                    break
            works.append({"doc_id": d["doc_id"], "title": d["title"], "year": d["year"],
                          "citation_count": d.get("citation_count"),
                          "rationale": "机械启发式：窗口内 origin=最早/milestone=最高被引/benchmark+method=题名匹配；待人工核验口径",
                          "kind": kind, "machine": True})
        # 同窗内确保 origin 取最早、milestone 取最高被引
        by_kind = collections.defaultdict(list)
        for w in works:
            by_kind[w["kind"]].append(w)
        picked = []
        if by_kind.get("origin"):
            picked.append(min(by_kind["origin"], key=lambda x: x["year"] if x["year"] is not None else 9999))
        if by_kind.get("milestone"):
            picked.append(max(by_kind["milestone"], key=lambda x: x["citation_count"] or -1))
        for k in ("benchmark", "method"):
            for w in by_kind.get(k, [])[:1]:
                picked.append(w)
        # 去重并保序
        seen = set()
        picked = [w for w in picked if not (w["doc_id"] in seen or seen.add(w["doc_id"]))]
        stages.append({"stage": "window_%d_%d" % (ws, we), "period": "%d-%d" % (ws, we),
                       "works": picked, "machine": True})

    # evidence_map（文档年份事实 + 候选句）
    evidence_map = []
    eid = 0
    def next_id():
        nonlocal eid
        eid += 1
        return "E%03d" % eid

    for d in documents:
        mat_path = os.path.join(args.work, d["source_file"])
        text, _e, _t = read_text_safe(mat_path)
        if text is None:
            continue
        if d.get("is_json_record"):
            try:
                obj = json.loads(text.lstrip())
                recs = json_to_records(obj)
                idx = int(d["doc_id"].rsplit("R", 1)[1])
                rtext = record_text(recs[idx]) if idx < len(recs) else text
            except Exception:
                rtext = text
        else:
            rtext = text
        if d["year"] is not None:
            li, ln_text = find_line_for_year(split_lines(rtext), d["year"])
            quote = ln_text.strip()[:240] if ln_text.strip() else d["title"]
            loc = "%s#L%d" % (d["source_file"], li)
            evidence_map.append({
                "id": next_id(), "claim": "document %s 出版年份为 %d（year_source=%s）" % (d["doc_id"], d["year"], d["year_source"]),
                "locator": loc, "quote": quote, "source_file": d["source_file"],
                "status": "reported", "confidence": "low", "machine": True,
            })
    # 每条候选句 -> 证据
    for c in candidates:
        evidence_map.append({
            "id": next_id(), "claim": "候选句[%s]：%s" % (c["kind"], c["quote"][:120]),
            "locator": c["locator"], "quote": c["quote"], "source_file": c["locator"].split("#")[0],
            "status": "reported", "confidence": "low", "machine": True,
        })

    # 候选句 -> 证据 id 映射（(locator, quote) 为稳定键）
    ev_for_cand = {}
    for c in candidates:
        for ev in evidence_map:
            if ev.get("locator") == c["locator"] and ev.get("quote") == c["quote"]:
                ev_for_cand[(c["locator"], c["quote"])] = ev["id"]
                break

    figure_model = {
        "schema": "figure-model-v1", "case_id": None,
        "documents": documents, "term_index": term_index,
        "candidates": candidates, "windows": windows_out,
    }

    # 组装 result.json 机器基线
    result_path = os.path.join(args.output, "result.json")
    result, rerr = load_json_safe(result_path)
    if rerr or not isinstance(result, dict):
        result = {}
        errors.append("result.json 读取失败，重建机器基线:" + str(rerr))
    case_id = result.get("case_id") or (manifest.get("case_id") if isinstance(manifest, dict) else None) or "unknown"
    figure_model["case_id"] = case_id

    # time_trend
    unit_note = result.get("time_trend", {}).get("unit_note", "")
    if not unit_note:
        unit_note = "年份口径待核验：metadata 优先，其次标题/页眉，再次全文最常见年份；在线发表/见刊/预印本未区分，待人工"
    time_trend = {
        "unit_note": unit_note,
        "distribution": distribution,
        "windows": windows_out,
        "claims": [{"claim": "机械年度分布（machine=true，未核验年份口径）",
                    "support": "inferred", "evidence_ids": [e["id"] for e in evidence_map if e.get("claim", "").startswith("document ")][:10]}],
    }
    # topic_evolution 机器候选
    term_mapping = [{"canonical": t["canonical"], "variants": t["variants"],
                     "first_year": t["first_year"], "last_year": t["last_year"], "machine": True}
                    for t in term_index]
    events = []
    for t in term_index:
        if t["first_year"] is not None:
            events.append({"type": "emergence", "term": t["canonical"], "period": "%d" % t["first_year"],
                           "cause": "机械候选：术语在该年首次出现，待人工核验是否为真正出现或术语漂移",
                           "evidence_ids": [], "machine": True})
    topic_evolution = {
        "stage_divisions": [{"stage": "window_%d" % (i + 1), "period": w["period"],
                             "basis": "机械按年度空档(>=3年)切窗", "terms": []} for i, w in enumerate(windows_out)],
        "term_mapping": term_mapping, "events": events, "claims": [],
    }
    # representative_works 机器候选
    representative_works = {
        "criteria": ["覆盖各时间窗", "origin=方法/主题最早出现", "milestone=窗内最高被引", "benchmark/method=题名匹配",
                     "长尾方法首创需人工核验，禁止只用总被引排序"],
        "stages": stages,
    }
    # research_gaps 机器候选（来自 gap/future_work/unknown 候选句）
    gaps = []
    for c in candidates:
        if c["kind"] in ("gap", "future_work", "unknown"):
            gtype = "open_question"
            gaps.append({"id": "G%03d" % (len(gaps) + 1), "type": gtype,
                         "description": c["quote"][:240],
                         "exclusion_evidence": "", "evidence_ids": [ev_for_cand.get((c["locator"], c["quote"]), "")],
                         "machine": True})
    research_gaps = {"gaps": gaps, "excluded_not_gaps": []}

    sources = []
    for d in documents:
        sources.append({"doc_id": d["doc_id"], "source_file": d["source_file"],
                        "title": d["title"], "year": d["year"], "year_source": d["year_source"],
                        "citation_count": d["citation_count"], "machine": True})

    result["case_id"] = case_id
    result.setdefault("output_contract", {})
    result["time_trend"] = time_trend
    result["topic_evolution"] = topic_evolution
    result["representative_works"] = representative_works
    result["research_gaps"] = research_gaps
    result["evidence_map"] = evidence_map
    result["sources"] = sources
    if result.get("status") not in ("input_error", "insufficient_evidence"):
        result["status"] = "partial"
    result.setdefault("change_log", []).append({
        "time": started, "action": "machine_baseline", "reason": "阶段2确定性抽取并入 result.json",
        "before": "skeleton", "after": "四类产物机器候选 + evidence_map + sources（machine=true）",
    })
    result.setdefault("errors", [])
    result["errors"] = (result.get("errors") or []) + errors
    result.setdefault("degradations", [])
    result["degradations"] = (result.get("degradations") or []) + degradations
    runtime = result.setdefault("runtime", {})
    runtime.setdefault("started_at", started)
    runtime["updated_at"] = started
    runtime["checkpoint"] = "machine_baseline"

    # 写产物
    write_json(os.path.join(args.output, "figure_model.json"), figure_model)
    write_json(os.path.join(args.output, "evidence_map.json"), {"evidence_map": evidence_map})
    os.makedirs(os.path.join(args.output, "tables"), exist_ok=True)
    write_csv(os.path.join(args.output, "tables", "documents.csv"),
              ["doc_id", "source_file", "title", "year", "year_source", "citation_count", "char_count", "is_json_record"],
              [[d["doc_id"], d["source_file"], d["title"], d["year"], d["year_source"],
                d["citation_count"], d["char_count"], d["is_json_record"]] for d in documents])
    write_csv(os.path.join(args.output, "tables", "year_distribution.csv"),
              ["year", "count", "doc_ids"],
              [[y, c, ";".join(ids)] for y, c, ids in
               [(r["year"], r["count"], r["doc_ids"]) for r in distribution]])
    write_report(os.path.join(args.output, "report.md"), case_id, distribution, term_index,
                 stages, gaps, candidates, windows_out, evidence_map, errors, degradations)
    write_json(os.path.join(args.output, "result.json"), result)
    write_json(os.path.join(args.work, "analyze_summary.json"),
               {"documents": len(documents), "distribution_years": len(distribution),
                "terms": len(term_index), "candidates": len(candidates), "gaps": len(gaps),
                "evidence": len(evidence_map), "errors": errors, "degradations": degradations})

    print(json.dumps({"ok": True, "case_id": case_id,
                      "documents": len(documents), "distribution_years": len(distribution),
                      "terms": len(term_index), "candidates": len(candidates),
                      "evidence": len(evidence_map), "gaps": len(gaps),
                      "errors": errors, "degradations": degradations},
                     ensure_ascii=False, indent=2))
    return 0


def write_report(path, case_id, distribution, term_index, stages, gaps, candidates, windows_out, evidence_map, errors, degradations):
    L = []
    L.append("# 研究前沿与趋势分析（机械基线，待人工核验）\n")
    L.append("- case_id: `%s`\n" % case_id)
    L.append("- 说明：本报告由 `scripts/analyze.py` 确定性生成，全部候选标注 `machine=true`。"
             "人工核验后请在 result.json 更新 `support`/`status`/`confidence` 并删除 `machine` 标记。\n")
    L.append("\n## 1. 年度分布（time_trend.distribution）\n")
    if distribution:
        L.append("| year | count | doc_ids |\n|-|-|-|")
        for r in distribution:
            L.append("| %s | %d | %s |" % (r["year"], r["count"], "; ".join(r["doc_ids"])))
    else:
        L.append("无年份元数据（见 time_trend.unit_note 与 pending_human）。\n")
    if windows_out:
        L.append("\n### 时间窗（机械切窗）\n")
        for w in windows_out:
            L.append("- %s：%d 篇（%s）" % (w["period"], w["doc_count"], "; ".join(w["doc_ids"])))
    L.append("\n## 2. 术语候选（topic_evolution.term_mapping）\n")
    L.append("| term | count | first_year | last_year | doc_ids |\n|-|-|-|-|-|")
    for t in term_index[:60]:
        L.append("| %s | %d | %s | %s | %s |" % (t["canonical"], t["count"], t["first_year"], t["last_year"], "; ".join(t["doc_ids"][:8])))
    L.append("\n## 3. 代表作候选（representative_works.stages）\n")
    for s in stages:
        L.append("### %s（%s）\n" % (s["stage"], s["period"]))
        for w in s["works"]:
            L.append("- [%s] %s（%s，kind=%s）—— %s" % (w["doc_id"], w["title"], w["year"], w["kind"], w["rationale"]))
    L.append("\n## 4. 研究空白候选（research_gaps.gaps）\n")
    if gaps:
        for g in gaps:
            L.append("- %s（type=%s）：%s" % (g["id"], g["type"], g["description"]))
    else:
        L.append("未检出候选句，或材料不足（见 limitations）。\n")
    L.append("\n## 5. 冲突候选句（candidates kind=conflict）\n")
    for c in candidates:
        if c["kind"] == "conflict":
            L.append("- [%s] %s：%s" % (c["doc_id"], c["locator"], c["quote"][:200]))
    L.append("\n## 6. 证据映射\n")
    L.append("共 %d 条机械证据（evidence_map.json，quote 为原文逐字，待 selfcheck 定位）。\n" % len(evidence_map))
    if errors:
        L.append("\n## 7. 错误\n")
        for e in errors:
            L.append("- %s" % e)
    if degradations:
        L.append("\n## 8. 降级\n")
        for d in degradations:
            L.append("- %s" % d)
    write_text(path, "\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
