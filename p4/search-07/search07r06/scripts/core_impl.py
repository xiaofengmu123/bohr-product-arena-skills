"""core_impl.py -- 检索覆盖率与遗漏分析 Skill 的实现核心。

仅依赖 Python 标准库。本文件包含全部子命令的实现；`scripts/main.py` 是薄入口。

设计原则
--------
1. 永不崩溃：空目录、坏 JSON、坏编码、超大文件、缺文件，一律退出码 0 并记录到
   errors/degradations。
2. 确定性：同一输入重复运行，result.json 关键数值（coverage_rate、hit_matrix 状态、
   中间产物哈希）完全一致；无随机性、无依赖环境的时间排序以外的噪声。
3. 可回放（本版核心）：最终 result.json 永远由 `merge` 从结构化中间产物派生，绝不手写。
   中间产物链为 plan.json -> reference_set.json + search_index.json -> match_model.json
   -> attribution_model.json -> result.json，每个文件带内容哈希，可独立复算。
4. 证据溯源：每条结论/命中/归因都带 locator（源文件 + 定位）与逐字 quote（取自材料
   的短片段，或 Agent 在 updates 文件中提供的原文摘录），不编造。

子命令
------
  scaffold  先落盘一版合法的 result.json 骨架（status=partial，契约键回填 null/[]）
  inspect   盘点 /input 全部文件，写 /app/work/inspect.json
  plan      生成/冻结 /app/work/plan.json（研究问题、范围、命中规则、漏检分类、补检触发）
  build     从材料提取并冻结 reference_set.json 与 search_index.json
  match     按命中规则计算 /app/work/match_model.json 与命中矩阵
  merge     汇合中间产物与 Agent 裁决/归因 updates，写最终 /app/output/result.json
  report    写 coverage_report.md、hit_matrix.csv、evidence_map.json
  full      顺序执行 scaffold->inspect->plan->build->match->merge->report
"""

from __future__ import annotations

import argparse
import calendar
import csv
import difflib
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata

VERSION = "2.0.0"
SKILL_NAME = "search-coverage-analysis"

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_WALK_DEPTH = 8
MAX_TEXT_PREVIEW = 160

TITLE_AUTO = 0.88
TITLE_ADJUDICATE = 0.70
YEAR_TOLERANCE = 1

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

OMISSION_TAXONOMY = [
    {"id": "synonym_uncovered", "label": "查询词未覆盖同义表达",
     "action": "新增同义/上位词/下位词查询式，保留旧查询式对照"},
    {"id": "field_too_narrow", "label": "检索字段限制过窄",
     "action": "放宽字段限制或改用全字段/标题摘要字段组合"},
    {"id": "time_window_truncation", "label": "时间窗截断",
     "action": "扩大或去除时间窗，记录时间窗前后对照"},
    {"id": "source_not_indexed", "label": "来源库未收录",
     "action": "切换或补充来源库，逐库记录命中差异"},
    {"id": "identifier_anomaly", "label": "记录标识异常/无标识/冲突",
     "action": "以标题+作者+年份复核，冲突并存并标 source_conflict"},
    {"id": "retracted_or_unavailable", "label": "撤稿或全文不可得",
     "action": "保留并标注，不纳入可补检命中"},
    {"id": "unknown", "label": "证据不足/待人工裁决",
     "action": "转 pending_human，不猜测归属"},
]

JSON_EXTS = {".json", ".jsonl"}
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".ris", ".bib", ".xml", ".yaml", ".yml"}
# 参考集/检索结果的文件名启发关键词（Agent 应优先显式指定 --refs/--results）
REF_KEYWORDS = ("ref", "gold", "known", "review", "guideline", "truth", "include",
                "criterion", "criteria", "benchmark", "target", "expert", "checklist")
RESULT_KEYWORDS = ("search", "query", "result", "response", "retriev", "crossref",
                   "europepmc", "pubmed", "api", "hit", "matched", "candidate")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _content_hash(obj) -> str:
    return _sha256_text(_canonical_json(obj))


def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        # 最后兜底：即使建目录失败也不崩溃，交由上层记录。
        print(f"[warn] cannot create dir {path}: {exc}", file=sys.stderr)


def _read_text(path: str):
    """按 utf-8-sig -> utf-8 -> latin-1 顺序容错读取；失败返回 (None, error)。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="replace") as f:
                return f.read(), None
        except (OSError, UnicodeDecodeError) as exc:
            last = f"{type(exc).__name__}: {exc}"
    return None, last


def _read_bytes(path: str):
    try:
        with open(path, "rb") as f:
            return f.read(), None
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _atomic_write_text(path: str, text: str) -> None:
    _ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _atomic_write_json(path: str, obj) -> None:
    _atomic_write_text(path, _canonical_json(obj))


def _load_json_file(path: str):
    """解析 JSON 文件；失败返回 (None, error)。"""
    text, err = _read_text(path)
    if err is not None:
        return None, err
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, f"bad JSON: {exc}"


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #

def _norm_doi(value) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi:\s*", "", s, flags=re.IGNORECASE)
    return s.strip().rstrip(".")


def _norm_pmid(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    m = re.search(r"\d{1,9}", s)
    return m.group(0) if m else ""


def _norm_title(value) -> str:
    if value is None:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_author_surname(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if "," in s:  # "Surname, Given"
        s = s.split(",")[0]
    s = re.sub(r"[^a-zA-Z]+", " ", s)
    parts = [p for p in s.split() if p]
    if not parts:
        return ""
    return parts[-1].lower()


def _year_from(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        y = int(value)
        return y if 1000 <= y <= 2100 else None
    if isinstance(value, (list, tuple)):
        for v in value:
            y = _year_from(v)
            if y is not None:
                return y
        return None
    m = re.search(r"(18|19|20)\d{2}", str(value))
    return int(m.group(0)) if m else None


def title_similarity(a: str, b: str) -> float:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    union = ta | tb
    jac = (len(ta & tb) / len(union)) if union else 0.0
    return max(seq, jac)


# --------------------------------------------------------------------------- #
# 记录解析（宽容、启发式；只提取，不臆造）
# --------------------------------------------------------------------------- #

def _get(obj: dict, *names):
    """按键名大小写不敏感取值，支持把 '-' 归一为 '_'。"""
    if not isinstance(obj, dict):
        return None
    low = {}
    for k, v in obj.items():
        key = str(k).lower().replace("-", "_").replace(" ", "_")
        low.setdefault(key, v)
    for n in names:
        key = str(n).lower().replace("-", "_").replace(" ", "_")
        if key in low:
            return low[key]
    return None


def _extract_identifiers(obj: dict) -> dict:
    doi = _get(obj, "doi", "DOI", "prism_doi", "paper_doi")
    pmid = _get(obj, "pmid", "PMID", "pubmed_id", "pubMedId", "medline_id")
    other = []
    for key in ("pii", "arxiv_id", "arxivId", "scopus_id", "wos_id", "isbn", "issn",
                "openalex_id", "semantic_scholar_id", "core_id"):
        v = _get(obj, key)
        if v not in (None, ""):
            other.append({"kind": key, "value": str(v)})
    ids = {}
    if doi:
        ids["doi"] = _norm_doi(doi)
    if pmid:
        ids["pmid"] = _norm_pmid(pmid)
    if other:
        ids["other"] = other
    return ids


def _extract_title(obj: dict):
    t = _get(obj, "title", "paper_title", "article_title")
    if isinstance(t, list):
        for item in t:
            if item not in (None, ""):
                return str(item).strip()
        return None
    if t in (None, ""):
        return None
    return str(t).strip()


def _extract_first_author(obj: dict):
    # 优先 author 列表；其次 authorString / authors / first_author。
    author = _get(obj, "author", "authors", "creators")
    if isinstance(author, list) and author:
        first = author[0]
        if isinstance(first, dict):
            name = _get(first, "name")
            if name:
                return str(name).strip()
            fam = _get(first, "family", "surname", "last_name", "lastname")
            giv = _get(first, "given", "first_name", "firstname", "given_name")
            if fam and giv:
                return f"{giv} {fam}".strip()
            if fam:
                return str(fam).strip()
            if giv:
                return str(giv).strip()
            return None
        if isinstance(first, str) and first.strip():
            return first.strip()
    s = _get(obj, "authorString", "author_string", "first_author",
             "firstAuthor", "creator")
    if isinstance(s, str) and s.strip():
        # "A. Smith, B. Jones, ..." 取第一段
        return s.split(";")[0].split(",")[0].strip()
    return None


def _extract_year(obj: dict):
    for key in ("year", "pubYear", "pub_year", "publication_year", "publicationYear",
                "publication_date", "published_date"):
        y = _year_from(_get(obj, key))
        if y is not None:
            return y
    issued = _get(obj, "issued", "published", "published_print", "pubDate")
    if isinstance(issued, dict):
        dp = _get(issued, "date_parts", "date-parts")
        if isinstance(dp, list):
            y = _year_from(dp)
            if y is not None:
                return y
        for k in ("year", "date", "date_time", "datetime"):
            y = _year_from(_get(issued, k))
            if y is not None:
                return y
    elif issued is not None:
        y = _year_from(issued)
        if y is not None:
            return y
    return None


def _extract_retracted(obj: dict) -> bool:
    """仅当材料显式给出撤稿信号时才标注，不臆测。"""
    def has_signal(v):
        if isinstance(v, str):
            return bool(re.search(r"\bretract(ed|ion)?\b", v, flags=re.IGNORECASE))
        return False

    for key in ("status", "retracted", "isRetracted", "is_retracted",
                "retraction_notice", "publication_status", "note", "notes"):
        if has_signal(_get(obj, key)):
            return True
    return False


def _parse_record_dict(obj: dict, path: str, source_file: str):
    """把任意 JSON 字典尝试解析为一条文献记录；失败返回 None。"""
    ids = _extract_identifiers(obj)
    title = _extract_title(obj)
    first_author = _extract_first_author(obj)
    year = _extract_year(obj)
    has_id = bool(ids.get("doi") or ids.get("pmid") or ids.get("other"))
    if not has_id and title is None:
        return None
    retracted = _extract_retracted(obj)
    record = {
        "source_file": source_file,
        "source_locator": path,
        "identifiers": ids,
        "title": title,
        "first_author": first_author,
        "year": year,
        "retracted_or_unavailable": retracted,
    }
    # 保留一条短原文片段作 evidence（逐字取自材料）
    snippet = title or ids.get("doi") or ids.get("pmid") or (
        ids["other"][0]["value"] if ids.get("other") else None)
    if snippet:
        record["raw_snippet"] = str(snippet)[:MAX_TEXT_PREVIEW]
    return record


def _iter_dict_paths(obj, path="$", depth=0):
    if depth > MAX_WALK_DEPTH or obj is None:
        return
    if isinstance(obj, dict):
        yield obj, path
        for k, v in obj.items():
            yield from _iter_dict_paths(v, f"{path}.{str(k)}", depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_dict_paths(v, f"{path}[{i}]", depth + 1)


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
_PMID_RE = re.compile(r"\bPMID:?\s*(\d{1,9})\b", flags=re.IGNORECASE)


def _extract_text_records(text: str, source_file: str):
    """从自由文本提取标识符记录；不臆造标题/作者/年份。"""
    records = []
    seen = set()
    for m in _DOI_RE.finditer(text):
        doi = _norm_doi(m.group(0))
        if doi and doi not in seen:
            seen.add(doi)
            records.append({
                "source_file": source_file,
                "source_locator": f"{source_file}#text",
                "identifiers": {"doi": doi},
                "title": None,
                "first_author": None,
                "year": None,
                "retracted_or_unavailable": False,
                "raw_snippet": m.group(0),
            })
    for m in _PMID_RE.finditer(text):
        pmid = _norm_pmid(m.group(1))
        key = ("pmid", pmid)
        if pmid and key not in seen:
            seen.add(key)
            records.append({
                "source_file": source_file,
                "source_locator": f"{source_file}#text",
                "identifiers": {"pmid": pmid},
                "title": None,
                "first_author": None,
                "year": None,
                "retracted_or_unavailable": False,
                "raw_snippet": m.group(0),
            })
    return records


def _extract_records_from_file(file_path: str, rel_path: str):
    """从单个文件提取记录；返回 (records, error_or_none)。"""
    size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    if size > MAX_FILE_BYTES:
        return [], f"skipped (>{MAX_FILE_BYTES} bytes)"
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return [], "PDF: 先运行 pdftotext 得到 .txt 再交给脚本（脚本不解析二进制 PDF）"
    text, err = _read_text(file_path)
    if err is not None:
        return [], err
    records = []
    if ext in JSON_EXTS:
        obj, err = _load_json_file(file_path)
        if err is not None:
            return [], err
        for d, path in _iter_dict_paths(obj):
            rec = _parse_record_dict(d, path, rel_path)
            if rec is not None:
                records.append(rec)
    elif ext in TEXT_EXTS or ext in ("", ".pdf"):
        records = _extract_text_records(text, rel_path)
    else:
        records = _extract_text_records(text, rel_path)
    return records, None


def _record_key(rec: dict) -> str:
    ids = rec.get("identifiers", {})
    doi = ids.get("doi")
    pmid = ids.get("pmid")
    if doi:
        return f"doi:{doi}"
    if pmid:
        return f"pmid:{pmid}"
    title = _norm_title(rec.get("title") or "")
    year = rec.get("year") or ""
    author = _norm_author_surname(rec.get("first_author") or "")
    return f"t:{title}|a:{author}|y:{year}"


# --------------------------------------------------------------------------- #
# case.json 处理
# --------------------------------------------------------------------------- #

def _load_case(input_dir: str):
    """读取 case.json；返回 (case_data, error)。"""
    path = os.path.join(input_dir, "case.json")
    if not os.path.exists(path):
        return None, f"missing {path}"
    obj, err = _load_json_file(path)
    if err is not None:
        return None, err
    return obj, None


def _extract_case_id(case_data, input_dir: str) -> str:
    if isinstance(case_data, dict):
        for key in ("case_id", "id", "challenge_id", "challengeId", "armiId"):
            v = case_data.get(key)
            if v not in (None, ""):
                return str(v)
    # 兜底：内容哈希（不虚构 id）
    blob = ""
    for name in ("case.json", "task.md"):
        p = os.path.join(input_dir, name)
        if os.path.exists(p):
            text, _ = _read_text(p)
            blob += text or ""
    return "hash-" + _sha256_text(blob)[:16]


def _extract_contract(case_data):
    if not isinstance(case_data, dict):
        return None
    # 先顶层，再递归兜底（最多 4 层），取第一个名为 output_contract/outputContract 的 dict
    for key in ("output_contract", "outputContract"):
        v = case_data.get(key)
        if isinstance(v, dict):
            return v

    def _walk(obj, depth):
        if depth > 4 or obj is None:
            return None
        if isinstance(obj, dict):
            for key in ("output_contract", "outputContract"):
                v = obj.get(key)
                if isinstance(v, dict):
                    return v
            for v in obj.values():
                found = _walk(v, depth + 1)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = _walk(v, depth + 1)
                if found is not None:
                    return found
        return None

    return _walk(case_data, 0)


def _backfill_value(spec):
    """按 output_contract 的键回填空值：list/array -> [], object/dict/map -> {},
    其余 -> null。"""
    if isinstance(spec, str):
        s = spec.lower()
        if any(w in s for w in ("list", "array", "[]", "sequence", "table")):
            return []
        if any(w in s for w in ("object", "dict", "map", "record", "{}", "matrix")):
            return {}
    if isinstance(spec, dict):
        # 若 spec 是 {field: type}，回填成同名键的空值对象
        return {k: _backfill_value(v) for k, v in spec.items()}
    if isinstance(spec, list):
        return []
    return None


def _backfill_contract(contract: dict) -> dict:
    out = {}
    if not isinstance(contract, dict):
        return out
    for k, spec in contract.items():
        out[k] = _backfill_value(spec)
    return out


# --------------------------------------------------------------------------- #
# 骨架与字段
# --------------------------------------------------------------------------- #

def _empty_coverage() -> dict:
    return {
        "denominator": 0,
        "numerator": 0,
        "coverage_rate": None,
        "omission_rate": None,
        "hit_rule": {
            "identifier_exact": "doi/pmid 归一化后精确相等",
            "title_auto": TITLE_AUTO,
            "title_adjudicate": TITLE_ADJUDICATE,
            "year_tolerance": YEAR_TOLERANCE,
        },
        "note": None,
    }


def _empty_pipeline() -> dict:
    return {
        "dag": ["plan.json", "reference_set.json", "search_index.json",
                "match_model.json", "attribution_model.json", "result.json"],
        "intermediates": {},
        "replay": "python scripts/main.py merge --input /input --output /app/output --work /app/work",
    }


def _empty_result(case_id: str, contract: dict, case_error=None) -> dict:
    result = {
        "case_id": case_id,
        "status": "partial",
        "research_question": None,
        "search_scope": None,
        "reference_set": {"version": None, "count": 0, "records": []},
        "search_sessions": [],
        "coverage": _empty_coverage(),
        "hit_matrix": [],
        "omissions": [],
        "supplementary_searches": [],
        "marginal_gain": None,
        "residual_omissions": [],
        "evidence_map": [],
        "sources": {"reference_set": [], "search_results": [], "plan": []},
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": _now_iso(), "ended_at": None, "seconds": None,
                    "intermediates": {}},
        "pipeline": _empty_pipeline(),
    }
    result.update(_backfill_contract(contract))
    if case_error:
        result["errors"].append({"stage": "case", "detail": case_error})
        result["limitations"].append("case.json 缺失或不可解析，output_contract 与 case_id 可能不完整")
    result["change_log"].append({
        "ts": _now_iso(), "event": "scaffold",
        "detail": "写入 result.json 骨架；status=partial，契约键回填 null/[]",
    })
    return result


# --------------------------------------------------------------------------- #
# 子命令：scaffold / inspect
# --------------------------------------------------------------------------- #

def cmd_scaffold(args) -> int:
    input_dir = args.input
    out_dir = args.output
    work_dir = args.work
    _ensure_dir(out_dir)
    _ensure_dir(work_dir)

    case_data, case_err = _load_case(input_dir)
    case_id = _extract_case_id(case_data, input_dir)
    contract = _extract_contract(case_data) or {}
    result = _empty_result(case_id, contract, case_err)

    # 记录中间产物骨架（供回放）
    result["runtime"]["intermediates"]["scaffold"] = {
        "written": True, "path": os.path.join(out_dir, "result.json"),
    }
    out_path = os.path.join(out_dir, "result.json")
    _atomic_write_json(out_path, result)
    print(f"[scaffold] wrote {out_path} case_id={case_id} contract_keys={sorted(contract.keys()) if contract else []}")
    return 0


def _walk_input(input_dir: str):
    files = []
    if not os.path.isdir(input_dir):
        return files
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            files.append({"path": rel, "abs": full, "size": size,
                          "ext": os.path.splitext(name)[1].lower()})
    return files


def cmd_inspect(args) -> int:
    input_dir = args.input
    work_dir = args.work
    _ensure_dir(work_dir)
    files = _walk_input(input_dir)
    report = {"files": [], "candidate_counts": {}, "parse_errors": [],
              "summary": {"total": len(files), "readable": 0, "candidates": 0}}
    candidates_by_file = {}
    for f in files:
        entry = {"path": f["path"], "size": f["size"], "ext": f["ext"]}
        if f["size"] < 0:
            entry["error"] = "stat failed"
            report["files"].append(entry)
            continue
        if f["size"] > MAX_FILE_BYTES:
            entry["error"] = "oversize skipped"
            report["files"].append(entry)
            continue
        recs, err = _extract_records_from_file(f["abs"], f["path"])
        if err is not None:
            entry["error"] = err
            report["parse_errors"].append({"path": f["path"], "error": err})
        else:
            entry["records"] = len(recs)
            entry["readable"] = True
            report["summary"]["readable"] += 1
            if recs:
                candidates_by_file[f["path"]] = len(recs)
        report["files"].append(entry)
    report["candidate_counts"] = candidates_by_file
    report["summary"]["candidates"] = sum(candidates_by_file.values())
    report["summary"]["total_readable"] = report["summary"]["readable"]

    # 给出参考集/检索结果的启发式建议
    suggestion = {"reference_set": [], "search_results": []}
    for f in files:
        base = os.path.basename(f["path"]).lower()
        if any(k in base for k in REF_KEYWORDS):
            suggestion["reference_set"].append(f["path"])
        elif any(k in base for k in RESULT_KEYWORDS):
            suggestion["search_results"].append(f["path"])
    report["suggestion"] = suggestion

    out_path = os.path.join(work_dir, "inspect.json")
    _atomic_write_json(out_path, report)
    print(f"[inspect] {report['summary']} -> {out_path}")
    print(f"[inspect] suggest refs={suggestion['reference_set']}")
    print(f"[inspect] suggest results={suggestion['search_results']}")
    return 0


# --------------------------------------------------------------------------- #
# 子命令：plan
# --------------------------------------------------------------------------- #

def _default_plan() -> dict:
    return {
        "version": VERSION,
        "frozen": False,
        "created_at": _now_iso(),
        "research_question": None,
        "search_scope": None,
        "reference_set_sources": [],
        "search_result_sources": [],
        "hit_rule": {
            "title_auto": TITLE_AUTO,
            "title_adjudicate": TITLE_ADJUDICATE,
            "year_tolerance": YEAR_TOLERANCE,
            "identifier_exact": "doi/pmid 归一化后精确相等",
            "title_author_year_fuzzy": "标题相似度>=title_auto 且年份容差<=year_tolerance",
        },
        "omission_taxonomy": OMISSION_TAXONOMY,
        "supplementary_triggers": {
            "synonym_uncovered": "存在漏检且归因为 synonym_uncovered 时，新增同义词查询式",
            "field_too_narrow": "存在漏检且归因为 field_too_narrow 时，放宽字段",
            "time_window_truncation": "存在漏检且归因为 time_window_truncation 时，扩大时间窗",
            "source_not_indexed": "存在漏检且归因为 source_not_indexed 时，切换/补充来源库",
        },
        "stop_criteria": {
            "max_rounds": 3,
            "no_new_hits": "一轮补检无任何新增命中即停止",
            "all_attributed": "所有残余遗漏均已归因并给出残余解释",
        },
        "decisions": [],
    }


def cmd_plan(args) -> int:
    work_dir = args.work
    _ensure_dir(work_dir)
    plan_path = os.path.join(work_dir, "plan.json")
    plan = _default_plan()

    if args.plan_file:
        text, err = _read_text(args.plan_file)
        if err is None:
            try:
                override = json.loads(text)
                if isinstance(override, dict):
                    for k, v in override.items():
                        if k in plan:
                            plan[k] = v
                    plan["decisions"] = plan.get("decisions", []) + [
                        {"ts": _now_iso(), "event": "plan_override",
                         "detail": f"merge plan-file {os.path.basename(args.plan_file)}"}]
            except ValueError as exc:
                plan["decisions"].append({"ts": _now_iso(), "event": "plan_error",
                                          "detail": f"bad plan-file: {exc}"})
        else:
            plan["decisions"].append({"ts": _now_iso(), "event": "plan_error",
                                      "detail": f"unreadable plan-file: {err}"})

    plan["frozen"] = bool(args.freeze)
    if plan["frozen"]:
        plan["frozen_at"] = _now_iso()
        plan["content_hash"] = _content_hash(plan)
    _atomic_write_json(plan_path, plan)
    print(f"[plan] wrote {plan_path} frozen={plan['frozen']} research_question={plan['research_question']!r}")
    if not plan["frozen"]:
        print("[plan] NOTE: 未冻结；Agent 应填写 research_question/search_scope/"
              "reference_set_sources/search_result_sources 后用 --freeze 再写一次")
    return 0


# --------------------------------------------------------------------------- #
# 子命令：build
# --------------------------------------------------------------------------- #

def _resolve_sources(input_dir: str, plan, args, keyword_lists):
    """确定参考集与检索结果文件；返回 (refs, results, used_defaults)。"""
    refs, results = [], []
    if args.refs:
        refs = [p for grp in args.refs for p in str(grp).split(",") if p.strip()]
    if args.results:
        results = [p for grp in args.results for p in str(grp).split(",") if p.strip()]
    if not refs and isinstance(plan, dict):
        refs = plan.get("reference_set_sources") or []
    if not results and isinstance(plan, dict):
        results = plan.get("search_result_sources") or []

    used_defaults = False
    if not refs and not results:
        used_defaults = True
        files = _walk_input(input_dir)
        for f in files:
            base = os.path.basename(f["path"]).lower()
            if any(k in base for k in keyword_lists["ref"]):
                refs.append(f["path"])
            elif any(k in base for k in keyword_lists["result"]):
                results.append(f["path"])
    return refs, results, used_defaults


def _glob_files(input_dir: str, patterns):
    """把相对 input 的 glob/路径解析为已存在文件列表（去重、排序、确定性）。"""
    out = []
    import glob as _glob
    for pat in patterns:
        pat = str(pat).strip()
        if not pat:
            continue
        candidates = []
        if os.path.isabs(pat):
            candidates = _glob.glob(pat)
        else:
            candidates = _glob.glob(os.path.join(input_dir, pat))
        for c in candidates:
            if os.path.isfile(c):
                rel = os.path.relpath(c, input_dir).replace("\\", "/")
                out.append((rel, c))
    # 去重保持顺序
    seen = set()
    uniq = []
    for rel, c in out:
        if rel not in seen:
            seen.add(rel)
            uniq.append((rel, c))
    return uniq


def _dedup_records(records, prefix):
    """按 record_key 去重；重复记录标注 duplicate_of；返回有序唯一列表。"""
    by_key = {}
    order = []
    for rec in records:
        key = _record_key(rec)
        if key in by_key:
            by_key[key]["duplicate_of"] = by_key[key].get("duplicate_of") or True
            continue
        by_key[key] = rec
        order.append(key)
    out = []
    for i, key in enumerate(order):
        rec = by_key[key]
        rec = dict(rec)
        rec["id"] = f"{prefix}{i + 1:04d}"
        rec["flags"] = []
        if not rec.get("identifiers"):
            rec["flags"].append("no_identifier")
        if rec.get("retracted_or_unavailable"):
            rec["flags"].append("retracted_or_unavailable")
        out.append(rec)
    return out


def _flag_identifier_conflicts(records):
    """同一标识符对应不同标题/作者时，标 identifier_conflict 并保留双方，不抹平。"""
    by_doi = {}
    for rec in records:
        doi = (rec.get("identifiers") or {}).get("doi")
        if doi:
            by_doi.setdefault(doi, []).append(rec)
        pmid = (rec.get("identifiers") or {}).get("pmid")
        if pmid:
            by_doi.setdefault(f"pmid:{pmid}", []).append(rec)
    for key, group in by_doi.items():
        if len(group) < 2:
            continue
        sigs = {(_norm_title(r.get("title") or ""),
                 _norm_author_surname(r.get("first_author") or "")) for r in group}
        if len(sigs) > 1:
            for r in group:
                if "identifier_conflict" not in r["flags"]:
                    r["flags"].append("identifier_conflict")
                    r["conflict_note"] = f"identifier {key} mapped to multiple title/author signatures"


def cmd_build(args) -> int:
    input_dir = args.input
    work_dir = args.work
    _ensure_dir(work_dir)

    plan_path = os.path.join(work_dir, "plan.json")
    plan = None
    if os.path.exists(plan_path):
        obj, err = _load_json_file(plan_path)
        if err is None:
            plan = obj

    refs, results, used_defaults = _resolve_sources(
        input_dir, plan, args,
        {"ref": REF_KEYWORDS, "result": RESULT_KEYWORDS})

    degradations = []
    ref_records, result_records = [], []
    ref_errors, result_errors = [], []

    for rel, abs_path in _glob_files(input_dir, refs):
        recs, err = _extract_records_from_file(abs_path, rel)
        if err:
            ref_errors.append({"path": rel, "error": err})
        else:
            ref_records.extend(recs)
    for rel, abs_path in _glob_files(input_dir, results):
        recs, err = _extract_records_from_file(abs_path, rel)
        if err:
            result_errors.append({"path": rel, "error": err})
        else:
            result_records.extend(recs)

    if not ref_records and refs:
        degradations.append("指定了参考集来源但未提取到任何记录，检查文件格式与标识符")
    if not result_records and results:
        degradations.append("指定了检索结果来源但未提取到任何记录")

    ref_set = {
        "version": "frozen-" + _now_iso(),
        "built_at": _now_iso(),
        "sources": refs or [],
        "count": 0,
        "records": [],
    }
    search_index = {
        "built_at": _now_iso(),
        "sources": results or [],
        "count": 0,
        "records": [],
    }

    if ref_records:
        records = _dedup_records(ref_records, "R")
        _flag_identifier_conflicts(records)
        ref_set["records"] = records
        ref_set["count"] = len(records)
        ref_set["content_hash"] = _content_hash(records)
    if result_records:
        records = _dedup_records(result_records, "S")
        _flag_identifier_conflicts(records)
        search_index["records"] = records
        search_index["count"] = len(records)
        search_index["content_hash"] = _content_hash(records)

    _atomic_write_json(os.path.join(work_dir, "reference_set.json"), ref_set)
    _atomic_write_json(os.path.join(work_dir, "search_index.json"), search_index)

    # 把来源与错误写回 plan（若存在），供 result 的 sources 使用
    if plan is not None:
        plan["reference_set_sources"] = refs or plan.get("reference_set_sources") or []
        plan["search_result_sources"] = results or plan.get("search_result_sources") or []
        _atomic_write_json(plan_path, plan)

    print(f"[build] reference_set={ref_set['count']} records, sources={refs}")
    print(f"[build] search_index={search_index['count']} records, sources={results}")
    print(f"[build] ref_errors={len(ref_errors)} result_errors={len(result_errors)}")
    if used_defaults:
        print("[build] NOTE: 未显式指定 --refs/--results，按文件名关键词猜测；请核对 inspect.json")
    return 0


# --------------------------------------------------------------------------- #
# 子命令：match
# --------------------------------------------------------------------------- #

def _load_json(path):
    if not os.path.exists(path):
        return None
    obj, err = _load_json_file(path)
    return obj if err is None else None


def _match_one(ref, search_records):
    """返回 (status, matched_result, matched_by, confidence, evidence)。"""
    rid = ref.get("identifiers", {})
    best_fuzzy = None
    for sr in search_records:
        sid = sr.get("identifiers", {})
        # 1) 标识符精确匹配
        if rid.get("doi") and sid.get("doi") and rid["doi"] == sid["doi"]:
            return ("hit", sr, "identifier", 1.0,
                    f"DOI 精确匹配 {rid['doi']}")
        if rid.get("pmid") and sid.get("pmid") and rid["pmid"] == sid["pmid"]:
            return ("hit", sr, "identifier", 1.0,
                    f"PMID 精确匹配 {rid['pmid']}")

        rtitle = ref.get("title")
        stitle = sr.get("title")
        if rtitle is None or stitle is None:
            continue
        sim = title_similarity(rtitle, stitle)
        if best_fuzzy is None or sim > best_fuzzy[0]:
            best_fuzzy = (sim, sr)

    if best_fuzzy is None:
        return ("miss", None, None, 0.0, "无标识符匹配且无可比标题")

    sim, sr = best_fuzzy
    ry = ref.get("year")
    sy = sr.get("year")
    year_ok = True
    if ry is not None and sy is not None:
        year_ok = abs(int(ry) - int(sy)) <= YEAR_TOLERANCE

    ra = _norm_author_surname(ref.get("first_author") or "")
    sa = _norm_author_surname(sr.get("first_author") or "")
    author_ok = True
    if ra and sa and ra != sa:
        author_ok = False

    if sim >= TITLE_AUTO and year_ok and author_ok:
        return ("hit", sr, "title_author_year", round(sim, 4),
                f"标题相似度 {sim:.3f}>=TITLE_AUTO，年份容差<=YEAR_TOLERANCE，作者一致")
    if sim >= TITLE_ADJUDICATE:
        return ("adjudicate", sr, "title_fuzzy", round(sim, 4),
                f"标题相似度 {sim:.3f} 落在 [TITLE_ADJUDICATE,TITLE_AUTO)，需人工裁决")
    return ("miss", sr, None, round(sim, 4),
            f"标题相似度 {sim:.3f} 低于 TITLE_ADJUDICATE，判定未命中")


def cmd_match(args) -> int:
    work_dir = args.work
    out_dir = args.output
    _ensure_dir(work_dir)
    _ensure_dir(out_dir)

    ref_set = _load_json(os.path.join(work_dir, "reference_set.json")) or {"records": []}
    search_index = _load_json(os.path.join(work_dir, "search_index.json")) or {"records": []}
    plan = _load_json(os.path.join(work_dir, "plan.json")) or {}

    title_auto = (plan.get("hit_rule") or {}).get("title_auto", TITLE_AUTO)
    title_adj = (plan.get("hit_rule") or {}).get("title_adjudicate", TITLE_ADJUDICATE)
    year_tol = (plan.get("hit_rule") or {}).get("year_tolerance", YEAR_TOLERANCE)

    ref_records = ref_set.get("records", [])
    search_records = search_index.get("records", [])

    match_rows = []
    for ref in ref_records:
        status, matched, matched_by, confidence, evidence = _match_one(ref, search_records)
        row = {
            "ref_id": ref.get("id"),
            "status": status,
            "matched_by": matched_by,
            "confidence": confidence,
            "matched_result_id": matched.get("id") if matched else None,
            "match_evidence": evidence,
            "ref_title": ref.get("title"),
            "ref_identifiers": ref.get("identifiers", {}),
            "ref_year": ref.get("year"),
            "ref_source_locator": ref.get("source_locator"),
            "ref_raw_snippet": ref.get("raw_snippet"),
            "matched_result_source": matched.get("source_file") if matched else None,
            "matched_result_title": matched.get("title") if matched else None,
        }
        match_rows.append(row)

    # 统计
    denominator = len(ref_records)
    numerator = sum(1 for r in match_rows if r["status"] == "hit")
    adjudicate = sum(1 for r in match_rows if r["status"] == "adjudicate")
    miss = sum(1 for r in match_rows if r["status"] == "miss")

    model = {
        "built_at": _now_iso(),
        "hit_rule": {"title_auto": title_auto, "title_adjudicate": title_adj,
                     "year_tolerance": year_tol},
        "denominator": denominator,
        "numerator": numerator,
        "adjudicate": adjudicate,
        "miss": miss,
        "coverage_rate": (numerator / denominator) if denominator else None,
        "omission_rate": ((denominator - numerator) / denominator) if denominator else None,
        "rows": match_rows,
        "content_hash": _content_hash(match_rows),
    }
    _atomic_write_json(os.path.join(work_dir, "match_model.json"), model)

    # 命中矩阵 CSV
    csv_path = os.path.join(out_dir, "hit_matrix.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ref_id", "status", "matched_by", "confidence", "matched_result_id",
                    "ref_title", "ref_doi", "ref_year", "ref_source_locator",
                    "matched_result_source", "match_evidence"])
        for r in match_rows:
            w.writerow([r["ref_id"], r["status"], r["matched_by"] or "", r["confidence"],
                        r["matched_result_id"] or "", r["ref_title"] or "",
                        (r["ref_identifiers"].get("doi") or r["ref_identifiers"].get("pmid") or ""),
                        r["ref_year"] or "", r["ref_source_locator"] or "",
                        r["matched_result_source"] or "", r["match_evidence"] or ""])

    print(f"[match] denominator={denominator} numerator={numerator} "
          f"coverage={model['coverage_rate']} adjudicate={adjudicate} miss={miss}")
    print(f"[match] wrote {os.path.join(work_dir, 'match_model.json')} and {csv_path}")
    return 0


# --------------------------------------------------------------------------- #
# 子命令：merge
# --------------------------------------------------------------------------- #

def _load_updates(args):
    updates = {}
    if args.updates:
        text, err = _read_text(args.updates)
        if err is None:
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    updates = obj
            except ValueError as exc:
                updates = {"_updates_error": f"bad updates JSON: {exc}"}
        else:
            updates = {"_updates_error": f"unreadable updates: {err}"}
    return updates


def _apply_updates(match_rows, updates):
    adjudications = updates.get("adjudications") or []
    adj_map = {}
    for a in adjudications:
        if isinstance(a, dict) and a.get("ref_id"):
            adj_map[str(a["ref_id"])] = a
    for row in match_rows:
        a = adj_map.get(str(row["ref_id"]))
        if a:
            new_status = a.get("status")
            if new_status in ("hit", "miss"):
                row["status"] = new_status
            row["matched_by"] = a.get("matched_by") or ("adjudication_" + (new_status or "?"))
            row["matched_result_id"] = a.get("matched_result_id")
            row["adjudication_note"] = a.get("note") or a.get("evidence")
            row["match_evidence"] = a.get("evidence") or row.get("match_evidence")
    return match_rows


def cmd_merge(args) -> int:
    input_dir = args.input
    out_dir = args.output
    work_dir = args.work
    _ensure_dir(out_dir)
    _ensure_dir(work_dir)

    case_data, case_err = _load_case(input_dir)
    case_id = _extract_case_id(case_data, input_dir)
    contract = _extract_contract(case_data) or {}

    plan = _load_json(os.path.join(work_dir, "plan.json")) or {}
    ref_set = _load_json(os.path.join(work_dir, "reference_set.json")) or {}
    search_index = _load_json(os.path.join(work_dir, "search_index.json")) or {}
    match_model = _load_json(os.path.join(work_dir, "match_model.json")) or {}
    updates = _load_updates(args)
    attribution_model = _load_json(os.path.join(work_dir, "attribution_model.json")) or {}

    if args.updates and "attributions" in updates:
        attribution_model = {"attributions": updates.get("attributions") or [],
                             "supplementary_searches": updates.get("supplementary_searches") or []}
        _atomic_write_json(os.path.join(work_dir, "attribution_model.json"), attribution_model)

    # 从已有 result.json 读取 change_log 与 runtime，实现增量改进
    prev_path = os.path.join(out_dir, "result.json")
    prev = {}
    if os.path.exists(prev_path):
        obj, err = _load_json_file(prev_path)
        if err is None and isinstance(obj, dict):
            prev = obj

    result = _empty_result(case_id, contract, case_err)
    result["change_log"] = prev.get("change_log", result["change_log"])
    result["runtime"]["started_at"] = prev.get("runtime", {}).get("started_at") or result["runtime"]["started_at"]

    # ---- 主结果汇合 ----
    result["research_question"] = plan.get("research_question")
    result["search_scope"] = plan.get("search_scope")
    result["reference_set"] = {
        "version": ref_set.get("version"),
        "count": ref_set.get("count", 0),
        "sources": ref_set.get("sources", []),
        "content_hash": ref_set.get("content_hash"),
        "records": ref_set.get("records", []),
    }
    result["search_sessions"] = plan.get("search_sessions") or updates.get("search_sessions") or []

    # 应用人工裁决
    rows = list(match_model.get("rows") or [])
    rows = _apply_updates(rows, updates)

    # 补充检索与边际收益
    supplementary = attribution_model.get("supplementary_searches") or []
    result["supplementary_searches"] = supplementary
    new_hit_ids = set()
    for s in supplementary:
        if isinstance(s, dict):
            for rid in (s.get("new_hits") or []):
                new_hit_ids.add(str(rid))
    # 校验 new_hits 必须是参考集中真实存在、原为 miss 的记录（防补检空转）
    valid_new_hits = set()
    ref_by_id = {str(r.get("id")): r for r in ref_set.get("records", [])}
    for rid in new_hit_ids:
        if rid in ref_by_id:
            valid_new_hits.add(rid)
    invalid_new_hits = sorted(new_hit_ids - valid_new_hits)
    if invalid_new_hits:
        result["degradations"].append(
            f"supplementary new_hits 含不在参考集中的 ref_id，已剔除：{invalid_new_hits}")

    for row in rows:
        if row["status"] == "miss" and str(row["ref_id"]) in valid_new_hits:
            row["status"] = "hit"
            row["matched_by"] = "supplementary"
            row["match_evidence"] = (row.get("match_evidence") or "") + "；补充检索新增命中"

    result["hit_matrix"] = rows

    denominator = len(rows)
    numerator = sum(1 for r in rows if r["status"] == "hit")
    coverage = _empty_coverage()
    coverage["denominator"] = denominator
    coverage["numerator"] = numerator
    coverage["coverage_rate"] = round(numerator / denominator, 6) if denominator else None
    coverage["omission_rate"] = round((denominator - numerator) / denominator, 6) if denominator else None
    coverage["hit_rule"] = match_model.get("hit_rule", coverage["hit_rule"])
    result["coverage"] = coverage

    # 漏检清单 + 归因
    attributions = attribution_model.get("attributions") or []
    attr_by_ref = {}
    for a in attributions:
        if isinstance(a, dict) and a.get("ref_id"):
            attr_by_ref.setdefault(str(a["ref_id"]), []).append(a)

    omissions = []
    for row in rows:
        if row["status"] in ("miss", "adjudicate"):
            attrs = attr_by_ref.get(str(row["ref_id"]), [])
            om = {
                "ref_id": row["ref_id"],
                "title": row.get("ref_title"),
                "identifiers": row.get("ref_identifiers", {}),
                "status": row["status"],
                "attribution": attrs if attrs else [],
                "attributed": bool(attrs),
            }
            omissions.append(om)
    result["omissions"] = omissions

    # 边际收益：以补检前漏检数为分母
    before_numerator = sum(1 for r in rows if r["matched_by"] != "supplementary" and r["status"] == "hit")
    # 更稳健：补检前命中 = 当前 hit 中非 supplementary 的；补检前漏检 = denominator - before_numerator
    before_den = denominator
    before_omissions = before_den - before_numerator
    after_numerator = numerator
    result["marginal_gain"] = {
        "before": {"denominator": before_den, "hits": before_numerator,
                   "coverage_rate": round(before_numerator / before_den, 6) if before_den else None},
        "after": {"hits": after_numerator,
                  "coverage_rate": round(after_numerator / before_den, 6) if before_den else None},
        "new_hits": len(valid_new_hits),
        "new_hit_ref_ids": sorted(valid_new_hits),
        "base_for_gain": before_omissions,
        "gain": round((after_numerator - before_numerator) / before_omissions, 6) if before_omissions else None,
        "note": "gain 分母=补检前漏检数；new_hits 必须来自参考集中真实记录且原为 miss",
    }

    result["residual_omissions"] = [
        {"ref_id": o["ref_id"], "title": o["title"],
         "remediable": bool(o["attribution"]) and any(
             a.get("category") not in ("retracted_or_unavailable", "unknown")
             for a in o["attribution"]),
         "reason": "; ".join(a.get("category", "") for a in o["attribution"]) or "待归因"} 
        for o in omissions]

    # ---- 溯源与诚实字段 ----
    result["sources"] = {
        "reference_set": ref_set.get("sources", []),
        "search_results": search_index.get("sources", []),
        "plan": plan.get("reference_set_sources", []) + plan.get("search_result_sources", []),
    }
    result["evidence_map"] = _build_evidence_map(rows, attributions, ref_set, search_index)
    result["uncertainty"] = updates.get("uncertainty") or prev.get("uncertainty") or []
    result["pending_human"] = _build_pending(plan, rows, omissions)
    result["limitations"] = updates.get("limitations") or prev.get("limitations") or []
    if prev.get("errors"):
        result["errors"] = list(prev["errors"])
    if prev.get("degradations"):
        result["degradations"] = list(prev["degradations"])
    if case_err and case_err not in [e.get("detail") for e in result["errors"]]:
        result["errors"].append({"stage": "case", "detail": case_err})

    # ---- 中间产物哈希（可回放）----
    intermediates = {}
    for name in ("plan.json", "reference_set.json", "search_index.json",
                 "match_model.json", "attribution_model.json"):
        p = os.path.join(work_dir, name)
        if os.path.exists(p):
            intermediates[name] = {"sha256": _sha256_file(p),
                                   "path": p, "bytes": os.path.getsize(p)}
    result["pipeline"]["intermediates"] = intermediates
    result["pipeline"]["replay"] = (
        "python scripts/main.py merge --input /input --output /app/output --work /app/work"
        + (" --updates <updates.json>" if args.updates else ""))

    # ---- 契约字段：把可计算的标准字段回填到顶层（coverage_rate 等不在核心顶层字段） ----
    for key, val in (("coverage_rate", coverage["coverage_rate"]),
                     ("omission_rate", coverage["omission_rate"])):
        if key in contract:
            result[key] = val

    # ---- 状态判定 ----
    result["status"] = _decide_status(plan, ref_set, rows, omissions)

    # ---- 收尾 ----
    result["change_log"].append({
        "ts": _now_iso(), "event": "merge",
        "detail": f"汇合中间产物；denominator={denominator} numerator={numerator} "
                  f"coverage={result['coverage']['coverage_rate']} status={result['status']}",
    })
    result["runtime"]["ended_at"] = _now_iso()
    start = result["runtime"]["started_at"]
    try:
        t0 = calendar.timegm(time.strptime(start, "%Y-%m-%dT%H:%M:%SZ"))
        result["runtime"]["seconds"] = round(max(0.0, time.time() - t0), 1)
    except (ValueError, TypeError):
        result["runtime"]["seconds"] = None
    result["runtime"]["intermediates"] = intermediates

    out_path = os.path.join(out_dir, "result.json")
    _atomic_write_json(out_path, result)
    print(f"[merge] wrote {out_path} status={result['status']} "
          f"coverage={result['coverage']['coverage_rate']} omissions={len(omissions)}")
    return 0


def _build_evidence_map(rows, attributions, ref_set, search_index):
    evidence = []
    ref_by_id = {r.get("id"): r for r in ref_set.get("records", [])}
    res_by_id = {r.get("id"): r for r in search_index.get("records", [])}
    for row in rows:
        ref = ref_by_id.get(row.get("ref_id")) or {}
        e = {
            "claim": f"reference {row.get('ref_id')} 判定为 {row.get('status')}（{row.get('matched_by') or '无'}）",
            "ref_id": row.get("ref_id"),
            "locator": ref.get("source_locator"),
            "quote": ref.get("raw_snippet") or row.get("ref_title") or "",
            "match_evidence": row.get("match_evidence"),
        }
        evidence.append(e)
        if row.get("matched_result_id"):
            sr = res_by_id.get(row.get("matched_result_id")) or {}
            evidence.append({
                "claim": f"命中候选 {row.get('matched_result_id')}",
                "ref_id": row.get("ref_id"),
                "locator": sr.get("source_locator"),
                "quote": sr.get("raw_snippet") or sr.get("title") or "",
                "match_evidence": row.get("match_evidence"),
            })
    for a in attributions:
        if isinstance(a, dict):
            evidence.append({
                "claim": f"漏检归因 {a.get('category')}（ref {a.get('ref_id')}）",
                "ref_id": a.get("ref_id"),
                "locator": a.get("evidence_locator") or a.get("locator"),
                "quote": a.get("evidence_quote") or a.get("quote") or "",
            })
    # 去重保持确定性
    seen = set()
    out = []
    for e in evidence:
        key = _content_hash(e)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _build_pending(plan, rows, omissions):
    pending = []
    if not plan.get("research_question"):
        pending.append("research_question 未在 plan.json 填写；需 Agent 明确研究问题与检索范围")
    if not plan.get("search_scope"):
        pending.append("search_scope 未在 plan.json 填写")
    adjudicate = [r for r in rows if r.get("status") == "adjudicate"]
    if adjudicate:
        pending.append(f"{len(adjudicate)} 条记录处于 adjudicate，需人工裁决命中/漏检")
    unattributed = [o for o in omissions if not o.get("attributed")]
    if unattributed:
        pending.append(f"{len(unattributed)} 条漏检未归因，需在 updates.json 提供 attribution")
    return pending


def _decide_status(plan, ref_set, rows, omissions):
    if not ref_set.get("records"):
        return "insufficient_evidence"
    if not plan.get("research_question"):
        return "partial"
    if any(r.get("status") == "adjudicate" for r in rows):
        return "partial"
    if any(not o.get("attributed") for o in omissions):
        return "partial"
    return "ok"


# --------------------------------------------------------------------------- #
# 子命令：report / full
# --------------------------------------------------------------------------- #

def cmd_report(args) -> int:
    out_dir = args.output
    work_dir = args.work
    _ensure_dir(out_dir)

    result = _load_json(os.path.join(out_dir, "result.json"))
    if result is None:
        print("[report] result.json 不存在，先运行 merge", file=sys.stderr)
        return 1

    md = _render_markdown(result)
    _atomic_write_text(os.path.join(out_dir, "coverage_report.md"), md)

    # evidence_map.json（与 result 中 evidence_map 一致）
    _atomic_write_json(os.path.join(out_dir, "evidence_map.json"),
                       result.get("evidence_map", []))

    # hit_matrix.csv 已在 match 阶段写，若缺失则补写
    csv_path = os.path.join(out_dir, "hit_matrix.csv")
    if not os.path.exists(csv_path):
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ref_id", "status", "matched_by", "confidence",
                        "matched_result_id", "ref_title", "match_evidence"])
            for r in result.get("hit_matrix", []):
                w.writerow([r.get("ref_id"), r.get("status"), r.get("matched_by") or "",
                            r.get("confidence"), r.get("matched_result_id") or "",
                            r.get("ref_title") or "", r.get("match_evidence") or ""])
    print(f"[report] wrote coverage_report.md / evidence_map.json / hit_matrix.csv under {out_dir}")
    return 0


def _render_markdown(r) -> str:
    cov = r.get("coverage") or {}
    mg = r.get("marginal_gain") or {}
    lines = [
        "# 检索覆盖率与遗漏分析报告",
        "",
        f"- case_id: `{r.get('case_id')}`",
        f"- status: `{r.get('status')}`",
        f"- 研究问题: {r.get('research_question') or '（未填写）'}",
        "",
        "## 覆盖率",
        "",
        f"- 分母（参考集规模）: {cov.get('denominator')}",
        f"- 分子（命中数）: {cov.get('numerator')}",
        f"- coverage_rate: {cov.get('coverage_rate')}",
        f"- omission_rate: {cov.get('omission_rate')}",
        f"- 命中规则: {cov.get('hit_rule')}",
        "",
        "## 边际收益（补检前后对照）",
        "",
        f"- 补检前 coverage_rate: {mg.get('before', {}).get('coverage_rate')}",
        f"- 补检后 coverage_rate: {mg.get('after', {}).get('coverage_rate')}",
        f"- 新增命中数: {mg.get('new_hits')}（分母=补检前漏检数 {mg.get('base_for_gain')}）",
        f"- gain: {mg.get('gain')}",
        "",
        "## 漏检清单",
        "",
        "| ref_id | 标题 | 状态 | 归因类别 |",
        "|-|-|-|-|",
    ]
    for o in r.get("omissions", []):
        cats = "; ".join(a.get("category", "") for a in o.get("attribution", [])) or "待归因"
        lines.append(f"| {o.get('ref_id')} | {(o.get('title') or '')[:60]} | "
                     f"{o.get('status')} | {cats} |")
    lines += [
        "",
        "## 残余遗漏",
        "",
    ]
    for o in r.get("residual_omissions", []):
        lines.append(f"- {o.get('ref_id')}: 可补救={o.get('remediable')}，原因={o.get('reason')}")
    lines += [
        "",
        "## 待人工处理",
        "",
    ]
    for p in r.get("pending_human", []):
        lines.append(f"- {p}")
    lines += [
        "",
        "## 局限与降级",
        "",
    ]
    for p in r.get("limitations", []):
        lines.append(f"- {p}")
    for p in r.get("degradations", []):
        lines.append(f"- [degradation] {p}")
    lines += [
        "",
        "## 中间产物（可回放）",
        "",
    ]
    for name, meta in (r.get("pipeline", {}).get("intermediates", {})).items():
        lines.append(f"- {name}: sha256={meta.get('sha256')}")
    lines += ["", f"回放方式: `{r.get('pipeline', {}).get('replay')}`"]
    return "\n".join(lines) + "\n"


def cmd_full(args) -> int:
    """顺序执行全流程（happy path 与自测用）。"""
    rc = cmd_scaffold(args)
    if rc:
        return rc
    rc = cmd_inspect(args)
    if rc:
        return rc
    # 首次 plan 冻结默认（Agent 可在后续用 --plan-file 覆盖并重新 freeze）
    args2 = argparse.Namespace(**vars(args))
    args2.plan_file = getattr(args, "plan_file", None)
    args2.freeze = True
    cmd_plan(args2)
    rc = cmd_build(args)
    if rc:
        return rc
    rc = cmd_match(args)
    if rc:
        return rc
    rc = cmd_merge(args)
    if rc:
        return rc
    return cmd_report(args)


# --------------------------------------------------------------------------- #
# CLI 装配
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)

    p = argparse.ArgumentParser(
        prog="main.py",
        description=f"{SKILL_NAME} v{VERSION} - 检索覆盖率与遗漏分析（中间表示可回放）",
        parents=[common],
    )
    p.add_argument("--version", action="version", version=VERSION)

    sub = p.add_subparsers(dest="command")

    sub.add_parser("scaffold", parents=[common], help="先落盘 result.json 骨架")
    sub.add_parser("inspect", parents=[common], help="盘点输入文件")
    pl = sub.add_parser("plan", parents=[common], help="生成/冻结 plan.json")
    pl.add_argument("--plan-file", default=None)
    pl.add_argument("--freeze", action="store_true")
    bd = sub.add_parser("build", parents=[common], help="提取 reference_set 与 search_index")
    bd.add_argument("--refs", action="append", default=None)
    bd.add_argument("--results", action="append", default=None)
    sub.add_parser("match", parents=[common], help="计算命中矩阵")
    mg = sub.add_parser("merge", parents=[common], help="汇合中间产物写 result.json")
    mg.add_argument("--updates", default=None)
    sub.add_parser("report", parents=[common], help="写 md/csv/evidence_map")
    fl = sub.add_parser("full", parents=[common],
                        help="顺序执行 scaffold->inspect->plan->build->match->merge->report")
    fl.add_argument("--refs", action="append", default=None)
    fl.add_argument("--results", action="append", default=None)
    fl.add_argument("--updates", default=None)
    fl.add_argument("--plan-file", default=None)
    return p


def dispatch(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    handlers = {
        "scaffold": cmd_scaffold,
        "inspect": cmd_inspect,
        "plan": cmd_plan,
        "build": cmd_build,
        "match": cmd_match,
        "merge": cmd_merge,
        "report": cmd_report,
        "full": cmd_full,
    }
    try:
        return handlers[args.command](args)
    except Exception as exc:  # noqa: BLE001 - 兜底，确保退出码 0
        print(f"[error] {args.command} failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(dispatch())
