#!/usr/bin/env python3
"""检索覆盖率与遗漏分析核心脚本（仅标准库，确定性执行）。

子命令:
  scaffold  落盘最小合法 result.json 骨架
  inspect   盘点 /input 文件与候选记录
  run       冻结参考集 + 逐条匹配 + 计算覆盖率
  merge     应用 updates 文件，计算边际收益并定稿

全部命令缺省 --input/--output/--work 指向 /input、/app/output、/app/work。
"""
import argparse
import csv
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from difflib import SequenceMatcher

# ---- 阈值（与 references/method.md 保持一致） ----
TITLE_HIT = 0.88
TITLE_ADJUD = 0.70
YEAR_TOL = 1
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)
PMID_RE = re.compile(r"\bPMID:?\s*(\d{1,9})\b", re.I)
ARXIV_RE = re.compile(r"\barXiv:?\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)

TITLE_KEYS = ("title", "Title", "article_title", "articleTitle", "name")
DOI_KEYS = ("doi", "DOI")
PMID_KEYS = ("pmid", "PMID", "pubmed_id", "pubmedId")
ARXIV_KEYS = ("arxiv", "arXiv", "arxiv_id", "eprint")
YEAR_KEYS = ("year", "Year", "pub_year", "publication_year", "published_year", "issued")
AUTHOR_KEYS = ("authors", "author", "Authors", "author_list", "creator", "creators")

HIT_CSV_COLS = [
    "ref_id", "title", "year", "first_author", "doi", "pmid", "arxiv",
    "ref_flags", "status", "matched_by", "match_title_sim",
    "candidate_title", "candidate_doi", "candidate_year", "evidence_quote",
    "source", "loc",
]


def eprint(*a):
    print(*a, file=sys.stderr)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def read_text(path):
    data = read_bytes(path)
    if data is None:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace")


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_json_or(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


# ---- 文本/标识符规范化 ----
def norm_text(s):
    s = (s or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def norm_identifier(s):
    return (s or "").strip().lower().rstrip(".,;)]")


def title_sim(a, b):
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def surname(name):
    n = (name or "").strip().rstrip(".")
    if not n:
        return ""
    parts = [p for p in re.split(r"[\s,]+", n) if p]
    if not parts:
        return ""
    if "," in n and len(parts) >= 2:
        return norm_text(parts[0])
    return norm_text(parts[-1])


def find_year(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        y = int(v)
        return y if 1800 <= y <= 2100 else None
    if isinstance(v, str):
        m = re.search(r"(18|19|20)\d{2}", v)
        return int(m.group(0)) if m else None
    if isinstance(v, (list, tuple)):
        for item in v:
            y = find_year(item)
            if y:
                return y
        return None
    if isinstance(v, dict):
        for k in ("date-parts", "date_parts", "year", "value"):
            if k in v:
                y = find_year(v[k])
                if y:
                    return y
        for item in v.values():
            y = find_year(item)
            if y:
                return y
    return None


def first_str(d, keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            v = d[k]
            if isinstance(v, (list, tuple)):
                for item in v:
                    if isinstance(item, str) and item.strip():
                        return item.strip()
            elif isinstance(v, str) and v.strip():
                return v.strip()
            elif isinstance(v, (int, float)):
                return str(v)
    return None


def as_list(d, keys):
    if not isinstance(d, dict):
        return []
    for k in keys:
        if k in d and d[k] is not None:
            v = d[k]
            if isinstance(v, (list, tuple)):
                out = [x.strip() for x in v if isinstance(x, str) and x.strip()]
                if out:
                    return out
            elif isinstance(v, str) and v.strip():
                return [v.strip()]
    return []


def record_from_dict(d, source, loc=""):
    if not isinstance(d, dict):
        return None
    title = first_str(d, TITLE_KEYS)
    doi = first_str(d, DOI_KEYS)
    pmid = first_str(d, PMID_KEYS)
    arxiv = first_str(d, ARXIV_KEYS)
    authors = as_list(d, AUTHOR_KEYS)
    year = None
    for k in YEAR_KEYS:
        if k in d:
            year = find_year(d[k])
            if year:
                break
    if not (title or doi or pmid or arxiv):
        return None
    return {
        "title": title or "",
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "year": year,
        "identifiers": {
            "doi": norm_identifier(doi) if doi else None,
            "pmid": norm_identifier(pmid) if pmid else None,
            "arxiv": norm_identifier(arxiv) if arxiv else None,
        },
        "source": source,
        "loc": loc,
        "quote": (title or doi or pmid or arxiv or "").strip(),
    }


def iter_records_from_json(obj, source, depth=0):
    out = []
    if depth > 10 or obj is None:
        return out
    if isinstance(obj, dict):
        r = record_from_dict(obj, source)
        if r is not None:
            out.append(r)
        for v in obj.values():
            out.extend(iter_records_from_json(v, source, depth + 1))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(iter_records_from_json(v, source, depth + 1))
    return out


def _ident_record(ident_kind, ident_val, source, loc):
    return {
        "title": "", "authors": [], "first_author": "", "year": None,
        "identifiers": {"doi": None, "pmid": None, "arxiv": None, ident_kind: ident_val},
        "source": source, "loc": loc, "quote": ident_val,
    }


def extract_from_text(text, source):
    records, errors = [], []
    try:
        obj = json.loads(text)
        return iter_records_from_json(obj, source), []
    except Exception:
        pass
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        loc = "%s:%d" % (source, i + 1)
        if line[0] in "[{":
            try:
                records.extend(iter_records_from_json(json.loads(line), source))
                continue
            except Exception:
                pass
        for m in DOI_RE.finditer(line):
            records.append(_ident_record("doi", norm_identifier(m.group(1)), source, loc))
        for m in PMID_RE.finditer(line):
            records.append(_ident_record("pmid", m.group(1), source, loc))
        for m in ARXIV_RE.finditer(line):
            records.append(_ident_record("arxiv", m.group(1), source, loc))
    return records, errors


_HEADER_MAP = {
    "title": TITLE_KEYS, "article_title": TITLE_KEYS, "name": TITLE_KEYS,
    "doi": DOI_KEYS, "pmid": PMID_KEYS, "pubmed_id": PMID_KEYS,
    "arxiv": ARXIV_KEYS, "authors": AUTHOR_KEYS, "author": AUTHOR_KEYS,
    "author_list": AUTHOR_KEYS, "year": YEAR_KEYS, "pub_year": YEAR_KEYS,
    "publication_year": YEAR_KEYS,
}


def extract_from_csv(text, source):
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except Exception as e:
        return [], [{"path": source, "degradation": "csv_parse_failed", "detail": str(e)[:200]}]
    if not rows:
        return [], []
    header = [norm_text(h) for h in rows[0]]
    col = {}
    for idx, h in enumerate(header):
        for canon, keys in _HEADER_MAP.items():
            if h in keys and canon not in col:
                col[canon] = idx
    if not col:
        return [], [{"path": source, "degradation": "csv_no_recognized_header"}]
    records = []
    for r, row in enumerate(rows[1:], start=2):
        d = {}
        if "title" in col and len(row) > col["title"]:
            d["title"] = row[col["title"]]
        if "doi" in col and len(row) > col["doi"]:
            d["doi"] = row[col["doi"]]
        if "pmid" in col and len(row) > col["pmid"]:
            d["pmid"] = row[col["pmid"]]
        if "arxiv" in col and len(row) > col["arxiv"]:
            d["arxiv"] = row[col["arxiv"]]
        if "authors" in col and len(row) > col["authors"]:
            d["authors"] = [a for a in re.split(r"[;|]", row[col["authors"]]) if a.strip()]
        if "year" in col and len(row) > col["year"]:
            d["year"] = row[col["year"]]
        rec = record_from_dict(d, source, "%s:%d" % (source, r))
        if rec:
            records.append(rec)
    return records, []


def pdf_to_text(path):
    try:
        p = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, timeout=120, check=False)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None
    return None


def extract_records_from_file(path, input_dir):
    rel = os.path.relpath(path, input_dir)
    size = os.path.getsize(path)
    errors = []
    if size > MAX_FILE_BYTES:
        return [], [{"path": rel, "degradation": "file_too_large_skipped", "size": size}]
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            text = pdf_to_text(path)
            if text is None:
                return [], [{"path": rel, "degradation": "pdf_no_text_layer_or_tool_missing"}]
            recs, errs = extract_from_text(text, rel)
            return recs, errs
        if ext == ".csv":
            raw = read_text(path)
            if raw is None:
                return [], [{"path": rel, "degradation": "unreadable_encoding"}]
            return extract_from_csv(raw, rel)
        raw = read_text(path)
        if raw is None:
            return [], [{"path": rel, "degradation": "unreadable_encoding"}]
        return extract_from_text(raw, rel)
    except Exception as e:
        return [], [{"path": rel, "degradation": "extract_failed", "detail": str(e)[:200]}]


# ---- 参考集构建 ----
def record_key(rec):
    ids = rec["identifiers"]
    for ident in ("doi", "pmid", "arxiv"):
        if ids.get(ident):
            return (ident, ids[ident])
    return ("title_author_year", norm_text(rec["title"]),
            surname(rec.get("first_author") or ""), rec.get("year"))


def build_reference_set(records):
    grouped = {}
    for rec in records:
        key = record_key(rec)
        grouped.setdefault(key, []).append(rec)
    title_groups = {}
    for rec in records:
        t = norm_text(rec["title"])
        if len(t) >= 10:
            title_groups.setdefault(t, set())
            for ident in ("doi", "pmid", "arxiv"):
                v = rec["identifiers"].get(ident)
                if v:
                    title_groups[t].add((ident, v))
    refs = []
    n = 0
    for key in sorted(grouped, key=repr):
        recs = grouped[key]
        n += 1
        ref_id = "R%03d" % n
        keep = dict(recs[0])
        keep["ref_id"] = ref_id
        flags = []
        if not any(keep["identifiers"].values()):
            flags.append("missing_identifier")
        if len(recs) > 1:
            flags.append("duplicate")
            keep["duplicate_count"] = len(recs)
        t = norm_text(keep["title"])
        if t in title_groups and len(title_groups[t]) > 1:
            flags.append("source_conflict")
        blob = " ".join([keep["title"], keep.get("quote", "")]).lower()
        if any(w in blob for w in ("retract", "withdrawn", "retraction")):
            flags.append("retracted_or_unavailable")
        keep["flags"] = flags
        refs.append(keep)
    return refs


def freeze_version(refs):
    h = hashlib.sha256()
    for r in refs:
        canon = [
            r.get("ref_id"), r["identifiers"].get("doi"),
            r["identifiers"].get("pmid"), r["identifiers"].get("arxiv"),
            norm_text(r["title"]), r.get("year"),
        ]
        h.update(json.dumps(canon, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return "sha256:" + h.hexdigest()[:16]


# ---- 匹配与覆盖率 ----
def match_reference(rec, results):
    ids = rec["identifiers"]
    for ident in ("doi", "pmid", "arxiv"):
        v = ids.get(ident)
        if not v:
            continue
        for cand in results:
            if cand["identifiers"].get(ident) == v:
                return "hit", "identifier:" + ident, cand, 1.0, cand.get("quote", "")
    t = norm_text(rec["title"])
    fa = surname(rec.get("first_author") or "")
    y = rec.get("year")
    best = None
    for cand in results:
        ct = norm_text(cand["title"])
        if not ct:
            continue
        sim = title_sim(rec["title"], cand["title"])
        cfa = surname(cand.get("first_author") or "")
        cy = cand.get("year")
        if sim >= TITLE_HIT:
            author_ok = True
            if fa and cfa:
                author_ok = (fa == cfa)
            year_ok = True
            if y is not None and cy is not None:
                year_ok = abs(y - cy) <= YEAR_TOL
            if author_ok and year_ok:
                return "hit", "title_author_year", cand, sim, cand.get("quote", "")
        if best is None or sim > best[0]:
            best = (sim, cand)
    if best and best[0] >= TITLE_ADJUD:
        return "adjudicate", "title_partial", best[1], best[0], best[1].get("quote", "")
    return "miss", None, None, 0.0, None


def compute_coverage(refs, results):
    matrix = []
    hits = 0
    adjud = 0
    excluded = 0
    for rec in refs:
        flags = rec.get("flags", [])
        if ("retracted_or_unavailable" in flags) or ("fulltext_unavailable" in flags):
            excluded += 1
            matrix.append({
                "ref_id": rec["ref_id"], "title": rec["title"], "year": rec["year"],
                "first_author": rec.get("first_author", ""),
                "doi": rec["identifiers"].get("doi"), "pmid": rec["identifiers"].get("pmid"),
                "arxiv": rec["identifiers"].get("arxiv"), "ref_flags": flags,
                "status": "excluded", "matched_by": None, "match_title_sim": None,
                "candidate_title": None, "candidate_doi": None, "candidate_year": None,
                "evidence_quote": None, "source": rec.get("source"), "loc": rec.get("loc"),
            })
            continue
        status, matched_by, cand, sim, ev = match_reference(rec, results)
        if status == "hit":
            hits += 1
        elif status == "adjudicate":
            adjud += 1
        matrix.append({
            "ref_id": rec["ref_id"], "title": rec["title"], "year": rec["year"],
            "first_author": rec.get("first_author", ""),
            "doi": rec["identifiers"].get("doi"), "pmid": rec["identifiers"].get("pmid"),
            "arxiv": rec["identifiers"].get("arxiv"), "ref_flags": flags,
            "status": status, "matched_by": matched_by, "match_title_sim": round(sim, 6) if sim is not None else None,
            "candidate_title": (cand or {}).get("title") or None,
            "candidate_doi": (cand or {}).get("identifiers", {}).get("doi"),
            "candidate_year": (cand or {}).get("year"),
            "evidence_quote": ev, "source": rec.get("source"), "loc": rec.get("loc"),
        })
    eligible = len(refs) - excluded
    denom = eligible
    numer = hits
    return matrix, {
        "denominator": denom,
        "numerator": numer,
        "coverage_rate": round(numer / denom, 6) if denom else None,
        "omission_rate": round((denom - numer) / denom, 6) if denom else None,
        "excluded": excluded,
        "adjudications": adjud,
        "hit_rule": ("identifier exact > title+first_author+year (sim>=%.2f) > "
                     "title adjudication (sim>=%.2f) > miss" % (TITLE_HIT, TITLE_ADJUD)),
        "thresholds": {"title_hit": TITLE_HIT, "title_adjudication": TITLE_ADJUD,
                       "year_tolerance": YEAR_TOL},
    }


def omissions_from_matrix(matrix):
    out = []
    for row in matrix:
        if row["status"] in ("miss", "adjudicate"):
            out.append({
                "ref_id": row["ref_id"], "status": row["status"],
                "category": "unattributed", "signals": [], "evidence": None,
                "query": None, "field": None, "source": None, "time_window": None,
            })
    return out


# ---- 输入发现 ----
def load_case(input_dir):
    case, errors = {}, []
    p = os.path.join(input_dir, "case.json")
    if os.path.isfile(p):
        raw = read_text(p)
        if raw is None:
            errors.append({"path": "case.json", "degradation": "unreadable_encoding"})
        else:
            try:
                case = json.loads(raw)
            except Exception as e:
                errors.append({"path": "case.json", "degradation": "bad_json",
                               "detail": str(e)[:200]})
                case = {}
    return case, errors


def detect_case_id(case, input_dir):
    for k in ("case_id", "id", "challenge_id", "armiId"):
        if isinstance(case, dict) and case.get(k):
            return str(case[k])
    h = hashlib.sha256()
    for name in ("task.md", "case.json"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            data = read_bytes(p)
            if data:
                h.update(data)
    return "case-" + h.hexdigest()[:12]


def detect_output_contract(case):
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        return case["output_contract"]
    return {}


def resolve_globs(input_dir, patterns):
    out = []
    for pat in (patterns or []):
        full = os.path.join(input_dir, pat)
        matches = sorted(glob.glob(full))
        if not matches and os.path.isfile(full):
            matches = [full]
        for m in matches:
            if os.path.isfile(m) and m not in out:
                out.append(m)
    return out


REF_PAT = re.compile(r"(ref|reference|gold|known|include|target|set|ground|truth|answer)", re.I)
RES_PAT = re.compile(r"(result|search|query|response|crossref|europe|pubmed|hit|retriev|output)", re.I)


def auto_detect(input_dir, kind):
    pat = REF_PAT if kind == "ref" else RES_PAT
    out = []
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p) and name not in ("case.json", "task.md") and pat.search(name):
            out.append(p)
    return out


def extract_many(paths, input_dir):
    records, degradations = [], []
    for p in paths:
        recs, errs = extract_records_from_file(p, input_dir)
        records.extend(recs)
        degradations.extend(errs)
    return records, degradations


# ---- 子命令 ----
def cmd_scaffold(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case, errs = load_case(args.input)
    result = {
        "case_id": detect_case_id(case, args.input),
        "status": "partial",
        "output_contract": detect_output_contract(case),
        "research_question": None,
        "search_scope": None,
        "reference_set": None,
        "search_sessions": [],
        "coverage": None,
        "hit_matrix": [],
        "omissions": [],
        "supplementary_searches": [],
        "marginal_gain": None,
        "residual_omissions": [],
        "evidence_map": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"at": now_iso(), "step": "scaffold", "note": "initial partial skeleton"}],
        "errors": errs,
        "degradations": [],
        "limitations": [],
        "runtime": {"tool": "scripts/analyze.py", "python": sys.version.split()[0]},
    }
    write_json(os.path.join(args.output, "result.json"), result)
    print("scaffold ok ->", os.path.join(args.output, "result.json"))
    return 0


def cmd_inspect(args):
    os.makedirs(args.work, exist_ok=True)
    case, errs = load_case(args.input)
    files, all_records, degradations = [], [], list(errs)
    if os.path.isdir(args.input):
        for name in sorted(os.listdir(args.input)):
            p = os.path.join(args.input, name)
            if not os.path.isfile(p):
                continue
            size = os.path.getsize(p)
            if name in ("case.json", "task.md"):
                files.append({"path": name, "kind": "control", "size": size})
                continue
            if size > MAX_FILE_BYTES:
                files.append({"path": name, "kind": "skipped", "size": size,
                              "candidate_records": 0})
                degradations.append({"path": name, "degradation": "file_too_large_skipped",
                                     "size": size})
                continue
            recs, errs = extract_records_from_file(p, args.input)
            files.append({"path": name, "kind": ("records" if recs else "material"),
                          "size": size, "candidate_records": len(recs)})
            all_records.extend(recs)
            degradations.extend(errs)
    inspect = {
        "case_id": detect_case_id(case, args.input),
        "output_contract_keys": sorted(detect_output_contract(case).keys()),
        "files": files,
        "candidate_record_count": len(all_records),
        "degradations": degradations,
        "hint": "确定独立参考集(refs)与检索结果(results)文件后，调用 run --refs/--results",
    }
    write_json(os.path.join(args.work, "inspect.json"), inspect)
    print(json.dumps(inspect, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result = load_json_or(os.path.join(args.output, "result.json"), {})
    if not isinstance(result, dict):
        result = {}
    case, case_errs = load_case(args.input)
    case_id = result.get("case_id") or detect_case_id(case, args.input)
    oc = result.get("output_contract") or detect_output_contract(case)
    ref_files = resolve_globs(args.input, args.refs) or auto_detect(args.input, "ref")
    res_files = resolve_globs(args.input, args.results) or auto_detect(args.input, "result")
    ref_records, ref_errs = extract_many(ref_files, args.input)
    res_records, res_errs = extract_many(res_files, args.input)
    refs = build_reference_set(ref_records)
    matrix, coverage = compute_coverage(refs, res_records)
    omissions = omissions_from_matrix(matrix)
    degradations = list(case_errs) + ref_errs + res_errs

    refset = {
        "version": freeze_version(refs),
        "size": len(refs),
        "eligible": coverage["denominator"],
        "excluded": coverage["excluded"],
        "records": refs,
    }
    write_json(os.path.join(args.work, "reference_set.json"), refset)
    core = {
        "case_id": case_id,
        "reference_set": refset,
        "coverage": coverage,
        "hit_matrix": matrix,
        "omissions": omissions,
        "ref_files": [os.path.relpath(p, args.input) for p in ref_files],
        "res_files": [os.path.relpath(p, args.input) for p in res_files],
        "results_record_count": len(res_records),
        "degradations": degradations,
    }
    write_json(os.path.join(args.work, "coverage_core.json"), core)
    write_hit_matrix_csv(os.path.join(args.output, "hit_matrix.csv"), matrix)

    change_log = result.get("change_log") or []
    change_log.append({"at": now_iso(), "step": "run",
                       "note": "coverage %s/%s, refs=%d results=%d"
                       % (coverage["numerator"], coverage["denominator"],
                          len(refs), len(res_records))})
    result.update({
        "case_id": case_id,
        "status": "partial" if matrix else "insufficient_evidence",
        "output_contract": oc,
        "reference_set": refset,
        "coverage": coverage,
        "hit_matrix": matrix,
        "omissions": omissions,
        "sources": {
            "refs": [os.path.relpath(p, args.input) for p in ref_files],
            "results": [os.path.relpath(p, args.input) for p in res_files],
            "control": ["case.json", "task.md"],
        },
        "errors": result.get("errors") or [],
        "degradations": degradations,
        "change_log": change_log,
        "runtime": result.get("runtime") or {"tool": "scripts/analyze.py",
                                             "python": sys.version.split()[0]},
    })
    write_json(os.path.join(args.output, "result.json"), result)
    print("run ok: coverage %s/%s (rate %s), refs=%d, results=%d, adjudicate=%d"
          % (coverage["numerator"], coverage["denominator"],
             coverage["coverage_rate"], len(refs), len(res_records),
             coverage["adjudications"]))
    return 0


def cmd_merge(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result = load_json_or(os.path.join(args.output, "result.json"), {})
    if not isinstance(result, dict):
        result = {}
    core = load_json_or(os.path.join(args.work, "coverage_core.json"), {})
    if not isinstance(core, dict):
        core = {}
    updates = {}
    degradations = []
    if args.updates and os.path.isfile(args.updates):
        updates = load_json_or(args.updates, {})
        if not isinstance(updates, dict):
            updates = {}
            degradations.append({"path": args.updates, "degradation": "updates_not_object"})
    elif args.updates:
        degradations.append({"path": args.updates, "degradation": "updates_missing"})
    matrix = list(core.get("hit_matrix") or result.get("hit_matrix") or [])
    refset = core.get("reference_set") or result.get("reference_set") or {}
    coverage = dict(core.get("coverage") or result.get("coverage") or {})

    adjud = updates.get("adjudications") or []
    adj_map = {a.get("ref_id"): a for a in adjud if isinstance(a, dict) and a.get("ref_id")}
    for row in matrix:
        a = adj_map.get(row.get("ref_id"))
        if a:
            row["status"] = a.get("status", row.get("status"))
            row["matched_by"] = a.get("matched_by", "manual")
            row["adjudication_note"] = a.get("evidence")

    attr = updates.get("omission_attributions") or []
    attr_map = {a.get("ref_id"): a for a in attr if isinstance(a, dict) and a.get("ref_id")}
    omissions = []
    for row in matrix:
        if row.get("status") in ("miss", "adjudicate"):
            o = {"ref_id": row["ref_id"], "status": row["status"],
                 "category": "unattributed", "signals": [], "evidence": None,
                 "query": None, "field": None, "source": None, "time_window": None}
            a = attr_map.get(row["ref_id"])
            if a:
                for k in ("category", "signals", "evidence", "query",
                          "field", "source", "time_window"):
                    if k in a:
                        o[k] = a[k]
            omissions.append(o)

    supp = updates.get("supplementary_searches") or []
    pre_omission_ids = {o["ref_id"] for o in omissions}
    new_hit_ids = set()
    for s in supp:
        for rid in (s.get("new_hit_ref_ids") or []):
            new_hit_ids.add(rid)
    real_new = sorted(new_hit_ids & pre_omission_ids)
    for row in matrix:
        if row.get("ref_id") in real_new:
            row["status"] = "hit"
            row["matched_by"] = "supplementary"

    eligible = [r for r in matrix if r.get("status") != "excluded"]
    numer = sum(1 for r in eligible if r.get("status") == "hit")
    denom = len(eligible)
    coverage.update({
        "denominator": denom,
        "numerator": numer,
        "coverage_rate": round(numer / denom, 6) if denom else None,
        "omission_rate": round((denom - numer) / denom, 6) if denom else None,
        "adjudications_remaining": sum(1 for r in eligible if r.get("status") == "adjudicate"),
    })

    residual = [dict(o) for o in omissions if o["ref_id"] not in real_new]
    res_map = {r.get("ref_id"): r for r in (updates.get("residual_omissions") or [])
               if isinstance(r, dict) and r.get("ref_id")}
    for o in residual:
        r = res_map.get(o["ref_id"])
        if r:
            o["reason"] = r.get("reason")
            o["remediable"] = r.get("remediable")
            o["needs"] = r.get("needs")
    marginal = {
        "pre_omissions": len(omissions),
        "new_hits": len(real_new),
        "post_omissions": len(residual),
        "marginal_gain_rate": round(len(real_new) / len(omissions), 6) if omissions else 0.0,
        "new_hit_ref_ids": real_new,
    }

    if not matrix:
        status = "insufficient_evidence"
    elif any(o.get("category") == "unattributed" for o in omissions):
        status = updates.get("status", "partial")
    else:
        status = updates.get("status", "ok")

    case, _ = load_case(args.input)
    change_log = result.get("change_log") or []
    change_log.append({"at": now_iso(), "step": "merge",
                       "note": "coverage %s/%s, new_hits=%d" % (numer, denom, len(real_new))})
    result.update({
        "case_id": result.get("case_id") or core.get("case_id") or detect_case_id(case, args.input),
        "status": status,
        "output_contract": result.get("output_contract") or detect_output_contract(case),
        "research_question": updates.get("research_question", result.get("research_question")),
        "search_scope": updates.get("search_scope", result.get("search_scope")),
        "search_sessions": updates.get("search_sessions", result.get("search_sessions", [])),
        "reference_set": refset,
        "coverage": coverage,
        "hit_matrix": matrix,
        "omissions": omissions,
        "supplementary_searches": supp,
        "marginal_gain": marginal,
        "residual_omissions": residual,
        "evidence_map": updates.get("evidence_map", result.get("evidence_map", [])),
        "sources": updates.get("sources", result.get("sources", [])),
        "uncertainty": updates.get("uncertainty", result.get("uncertainty", [])),
        "pending_human": updates.get("pending_human", result.get("pending_human", [])),
        "limitations": updates.get("limitations", result.get("limitations", [])),
        "errors": result.get("errors") or [],
        "degradations": (result.get("degradations") or []) + degradations,
        "change_log": change_log,
        "runtime": result.get("runtime") or {"tool": "scripts/analyze.py",
                                             "python": sys.version.split()[0]},
    })
    write_json(os.path.join(args.output, "result.json"), result)
    write_hit_matrix_csv(os.path.join(args.output, "hit_matrix.csv"), matrix)
    write_json(os.path.join(args.output, "evidence_map.json"), result.get("evidence_map", []))
    write_coverage_report(os.path.join(args.output, "coverage_report.md"), result)
    print("merge ok: coverage %s/%s (rate %s), new_hits=%d, status=%s"
          % (numer, denom, coverage.get("coverage_rate"), len(real_new), status))
    return 0


def write_hit_matrix_csv(path, matrix):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HIT_CSV_COLS)
        for row in matrix:
            w.writerow([row.get(c, "") for c in HIT_CSV_COLS])


def write_coverage_report(path, result):
    cov = result.get("coverage") or {}
    lines = [
        "# 检索覆盖率与遗漏分析报告",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- research_question: %s" % (result.get("research_question") or "not_reported"),
        "",
        "## 覆盖结论",
        "",
        "- 参考集规模: %d（合格 %d，排除 %d）" % (
            (result.get("reference_set") or {}).get("size", 0),
            cov.get("denominator", 0), cov.get("excluded", 0)),
        "- 命中数: %d / 合格 %d" % (cov.get("numerator", 0), cov.get("denominator", 0)),
        "- coverage_rate: %s" % (cov.get("coverage_rate", "not_reported")),
        "- omission_rate: %s" % (cov.get("omission_rate", "not_reported")),
        "- 命中规则: %s" % (cov.get("hit_rule", "not_reported")),
        "",
        "## 漏检归因",
        "",
    ]
    om = result.get("omissions") or []
    if om:
        lines.append("| ref_id | category | query | field | source | time_window | evidence |")
        lines.append("|---|---|---|---|---|---|---|")
        for o in om:
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                o.get("ref_id"), o.get("category"), o.get("query"), o.get("field"),
                o.get("source"), o.get("time_window"), o.get("evidence")))
    else:
        lines.append("（无漏检记录）")
    lines += ["", "## 补充检索与边际收益", ""]
    mg = result.get("marginal_gain")
    if mg:
        lines.append("- 补检前遗漏: %d；新增命中: %d；补检后遗漏: %d；边际增益率: %s" % (
            mg.get("pre_omissions"), mg.get("new_hits"),
            mg.get("post_omissions"), mg.get("marginal_gain_rate")))
    for s in (result.get("supplementary_searches") or []):
        lines.append("- S%s: `%s` (field=%s, source=%s) → %s" % (
            s.get("id"), s.get("query"), s.get("field"), s.get("source"),
            s.get("new_hit_ref_ids")))
    lines += ["", "## 残余遗漏", ""]
    res = result.get("residual_omissions") or []
    if res:
        for o in res:
            lines.append("- %s: %s (remediable=%s, needs=%s)" % (
                o.get("ref_id"), o.get("reason"), o.get("remediable"), o.get("needs")))
    else:
        lines.append("（无）")
    lines += ["", "## 限制与不确定性", ""]
    for u in (result.get("limitations") or []):
        lines.append("- %s" % u)
    for u in (result.get("uncertainty") or []):
        lines.append("- %s" % u)
    lines.append("")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))


# ---- CLI ----
def build_parser():
    p = argparse.ArgumentParser(
        prog="analyze.py",
        description="检索覆盖率与遗漏分析：scaffold / inspect / run / merge")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("scaffold", help="落盘最小合法 result.json 骨架")
    ip = sub.add_parser("inspect", help="盘点输入文件与候选记录")
    rp = sub.add_parser("run", help="冻结参考集+匹配+覆盖率")
    rp.add_argument("--refs", nargs="*", help="参考集文件或 glob（相对 /input）")
    rp.add_argument("--results", nargs="*", help="检索结果文件或 glob（相对 /input）")
    mp = sub.add_parser("merge", help="应用 updates 文件并定稿")
    mp.add_argument("--updates", help="updates JSON 路径（裁决/归因/补检）")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "inspect":
        return cmd_inspect(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "merge":
        return cmd_merge(args)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
