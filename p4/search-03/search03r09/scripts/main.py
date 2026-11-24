#!/usr/bin/env python3
"""文献筛选与智能排序 Skill —— 确定性管线入口（仅 Python 标准库）。

子命令:
  discover   输入发现 + 容错解析 + 落盘 partial 骨架 result.json
  screen     确定性词面信号 + 去重聚类 + 基线排序脚手架（非最终分数）
  check      契约校验 + 证据逐字溯源 + 自检报告（selfcheck.json）

同一输入两次运行，discover/screen 产物逐字节一致。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
TIERS = ("retain", "pending", "exclude")
TIER_ORDER = {"retain": 0, "pending": 1, "exclude": 2}
RELEVANCES = ("direct", "indirect", "irrelevant", "unknown")
CONFIDENCES = ("high", "medium", "low")

TITLE_LIKE = {"title", "name", "paper_title", "article_title", "headline", "document_title"}
ABSTRACT_LIKE = {"abstract", "summary", "abstract_text"}
AUTHOR_LIKE = {"authors", "author", "creators", "creator"}
YEAR_LIKE = {"year", "pub_year", "publication_year", "date", "published"}
VENUE_LIKE = {"venue", "journal", "source", "publication", "publisher", "conference", "journal_name"}
DOI_LIKE = {"doi"}
URL_LIKE = {"url", "link", "pdf_url", "external_url", "href"}
PAPER_KEY_HINTS = {
    "papers", "candidates", "records", "results", "items", "documents",
    "hits", "articles", "publications", "search_results", "references",
}
QUESTION_KEYS = ("research_question", "question", "query", "research_query",
                 "task", "topic", "research_topic", "prompt")
CRITERIA_KEYS = ("inclusion_criteria", "exclusion_criteria", "criteria",
                 "inclusion", "exclusion", "screening_criteria", "eligibility")
CONTRACT_KEYS = ("output_contract", "output_schema", "expected_fields",
                 "expected_output", "output_format", "contract", "schema")
CASE_ID_KEYS = ("case_id", "caseId", "id", "challenge_id", "challengeId")

REQUIRED_TOP = ("case_id", "status", "results", "sources", "uncertainty", "change_log")
RESULT_REQUIRED = ("record_id", "title", "authors", "year", "venue", "doi", "url",
                   "source_type", "relevance", "tier", "priority_score", "rank",
                   "reasons", "evidence", "confidence", "criteria_matched",
                   "conflict", "dedupe")


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path: str, max_bytes: int = 2 * 1024 * 1024):
    """容错读文本：BOM / 坏编码降级；超限返回 too_large。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "missing"
    if size > max_bytes:
        return None, "too_large"
    raw = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                raw = fh.read()
            return raw, "ok"
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read(), "replaced"


def load_json(path: str):
    text, status = read_text(path)
    if text is None:
        return None, status, None
    try:
        return json.loads(text), "ok", None
    except json.JSONDecodeError as exc:
        return None, "bad_json", str(exc)


def write_json(path: str, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def enumerate_files(root: str):
    """按相对路径排序地列出所有文件（确定性）。"""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append((rel, full))
    out.sort(key=lambda x: x[0])
    return out


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def tokenize(s: str):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (s or "").lower())
    return set(t for t in s.split() if t)


def norm_title(s):
    if s is None:
        return ""
    t = str(s).lower()
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def norm_doi(s):
    if not s:
        return None
    t = str(s).strip().lower()
    t = re.sub(r"^https?://(dx\.)?doi\.org/", "", t)
    return t


def _get(d: dict, keys):
    lower = {str(k).lower(): k for k in d}
    for key in keys:
        if key.lower() in lower:
            return d[lower[key.lower()]]
    return None


def _as_str(v):
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    if isinstance(v, (list, tuple)):
        parts = [str(x) for x in v if x is not None]
        return "; ".join(parts) or None
    return str(v)


def _as_authors(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    parts = re.split(r";|、|\n", s)
    if len(parts) == 1:
        parts = re.split(r",\s*", s)
    return [p.strip() for p in parts if p.strip()]


def _as_year(v):
    if v is None:
        return None
    s = str(v)
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        return int(m.group(0))
    return None


def _guess_source_type(rec: dict) -> str:
    venue = (_as_str(_get(rec, VENUE_LIKE)) or "").lower()
    url = (_as_str(_get(rec, URL_LIKE)) or "").lower()
    doi = (_as_str(_get(rec, DOI_LIKE)) or "").lower()
    if "arxiv" in venue or "arxiv" in url:
        return "preprint"
    if "biorxiv" in venue or "medrxiv" in url or "preprint" in venue or "preprint" in url:
        return "preprint"
    if "conference" in venue or "proc" in venue or "symposium" in venue or "workshop" in venue:
        return "conference"
    if venue or doi:
        return "journal_article"
    return "unknown"


def _looks_like_record(d: dict) -> bool:
    kl = {str(k).lower() for k in d}
    if kl & TITLE_LIKE:
        return True
    if (kl & ABSTRACT_LIKE) and (kl & (AUTHOR_LIKE | DOI_LIKE | YEAR_LIKE)):
        return True
    return False


def _collect_candidate_lists(obj, path, out, seen):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in PAPER_KEY_HINTS and isinstance(v, list):
                if id(v) not in seen:
                    seen.add(id(v))
                    out.append((f"{path}#{k}", v))
            if isinstance(v, (dict, list)):
                _collect_candidate_lists(v, path, out, seen)
    elif isinstance(obj, list):
        if id(obj) not in seen and obj and all(isinstance(x, dict) for x in obj) \
                and any(_looks_like_record(x) for x in obj):
            seen.add(id(obj))
            out.append((path, obj))
        for v in obj:
            if isinstance(v, (dict, list)):
                _collect_candidate_lists(v, path, out, seen)


def _norm_record(item: dict, idx: int) -> dict:
    rec = {
        "record_id": f"rec_{idx:04d}",
        "title": _as_str(_get(item, TITLE_LIKE)),
        "authors": _as_authors(_get(item, AUTHOR_LIKE)),
        "year": _as_year(_get(item, YEAR_LIKE)),
        "venue": _as_str(_get(item, VENUE_LIKE)),
        "doi": _as_str(_get(item, DOI_LIKE)),
        "url": _as_str(_get(item, URL_LIKE)),
        "source_type": _guess_source_type(item),
        "abstract": _as_str(_get(item, ABSTRACT_LIKE)),
    }
    return rec


def _find_contract(obj):
    """在 case.json 中寻找 output_contract / output_schema 等。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in CONTRACT_KEYS:
                return v
    return None


def _contract_fields(contract) -> dict:
    """从任意形态的契约提取 字段名 -> 默认空值。"""
    fields = {}

    def add(name, spec):
        if not name or not isinstance(name, str):
            return
        if isinstance(spec, dict):
            t = str(spec.get("type", "")).lower()
            if t in ("array", "list"):
                fields[name] = []
            elif t == "object":
                fields[name] = {}
            else:
                fields[name] = None
        elif isinstance(spec, list):
            fields[name] = []
        else:
            fields[name] = None

    if isinstance(contract, dict):
        props = contract.get("properties")
        if isinstance(props, dict):
            for name, spec in props.items():
                add(name, spec)
        elif isinstance(contract.get("fields"), (dict, list)):
            fields.update(_contract_fields(contract["fields"]))
        elif "type" not in contract:
            for name, spec in contract.items():
                add(name, spec)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                add(item, None)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("key")
                add(name, item)
    return fields


def _guess_role(filename: str, rel: str) -> str:
    base = os.path.basename(filename).lower()
    rel_l = rel.lower()
    if base == "case.json":
        return "case"
    if base in ("task.md", "task.txt", "instructions.md", "readme.md"):
        return "task"
    if "material" in rel_l or base.endswith(".pdf"):
        return "material"
    return "other"


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------
def run_discover(args) -> int:
    errors, degradations, omissions = [], [], []
    started = utcnow()
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)

    # 1) 枚举输入文件（确定性）
    sources = []
    case_path = None
    task_path = None
    for rel, full in enumerate_files(args.input):
        ext = os.path.splitext(full)[1].lower()
        kind = {"json": "json", "jsonl": "text", "md": "markdown", "pdf": "pdf",
                "txt": "text"}.get(ext, "other")
        role = _guess_role(full, rel)
        sources.append({"path": rel, "kind": kind, "role": role})
        if role == "case" and case_path is None:
            case_path = full
        if role == "task" and task_path is None:
            task_path = full

    # 2) 读 case.json 与 task.md
    case_obj, case_status, case_err = (None, "missing", None)
    if case_path is None:
        # 兜底：找任意 json 里含候选记录的
        for rel, full in enumerate_files(args.input):
            if rel.lower().endswith(".json"):
                case_path = full
                break
    if case_path is not None:
        case_obj, case_status, case_err = load_json(case_path)
        if case_status == "bad_json":
            degradations.append(f"case.json 无法解析: {case_err}；改用 task.md 继续")
        elif case_status in ("too_large", "missing"):
            degradations.append(f"case.json 读取失败: {case_status}")
    task_text = None
    if task_path is not None:
        task_text, _ = read_text(task_path)

    # 3) 提取 case_id
    case_id = None
    if isinstance(case_obj, dict):
        for k in CASE_ID_KEYS:
            if k in case_obj and case_obj[k] not in (None, ""):
                case_id = str(case_obj[k])
                break
    if case_id is None and task_text:
        m = re.search(r"challenge[-_]?id[:：\s]+([A-Za-z0-9_-]+)", task_text)
        if m:
            case_id = m.group(1)
    if case_id is None:
        m = re.search(r"case[-_]?id[:：\s]+([A-Za-z0-9_-]+)", task_text or "")
        if m:
            case_id = m.group(1)

    # 4) 提取科研问题 / 纳排标准
    research_question = None
    criteria = {}
    if isinstance(case_obj, dict):
        research_question = _as_str(_get(case_obj, QUESTION_KEYS))
        for k, v in case_obj.items():
            if str(k).lower() in CRITERIA_KEYS:
                if isinstance(v, dict):
                    for ck, cv in v.items():
                        criteria[str(ck)] = _as_str(cv)
                elif isinstance(v, list):
                    criteria[str(k)] = [_as_str(x) for x in v]
                else:
                    criteria[str(k)] = _as_str(v)

    # 5) 提取候选文献
    candidates = []
    if isinstance(case_obj, (dict, list)):
        lists = []
        _collect_candidate_lists(case_obj, "case.json", lists, set())
        seen_ids = set()
        idx = 0
        for _loc, lst in lists:
            for item in lst:
                if not isinstance(item, dict):
                    continue
                if id(item) in seen_ids:
                    continue
                seen_ids.add(id(item))
                rec = _norm_record(item, idx)
                idx += 1
                candidates.append(rec)

    # 6) 提取 output_contract
    contract = _find_contract(case_obj) if isinstance(case_obj, dict) else None
    contract_fields = _contract_fields(contract) if contract is not None else {}

    # 7) 判定初始 status
    has_input = case_path is not None or task_path is not None
    if not has_input:
        status = "insufficient_evidence"
        omissions.append("未发现 case.json 或 task.md")
    elif not candidates:
        status = "insufficient_evidence"
        omissions.append("未从 case.json 中发现任何候选文献记录")
    else:
        status = "partial"

    # 8) 落盘 partial 骨架（先合法结果，后增量改进）
    result = {
        "case_id": case_id,
        "status": status,
        "results": [],
        "dedupe_groups": [],
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "omissions": omissions,
        "errors": errors,
        "degradations": degradations,
        "warnings": [],
        "change_log": [{"ts": started, "step": "discover", "action": "partial 骨架已落盘"}],
        "runtime": {"started_at": started, "finished_at": None,
                    "duration_s": None, "requests_used": None},
    }
    if contract is not None:
        result["output_contract"] = contract
        for name, default in contract_fields.items():
            result.setdefault(name, default)

    write_json(os.path.join(args.output, "result.json"), result)

    norm = {
        "case_id": case_id,
        "research_question": research_question,
        "criteria": criteria,
        "candidates": candidates,
        "output_contract": contract,
        "contract_fields": sorted(contract_fields.keys()),
        "sources": sources,
    }
    write_json(os.path.join(args.work, "case_normalized.json"), norm)
    print(f"discover: status={status} candidates={len(candidates)} "
          f"contract_fields={len(contract_fields)}")
    return 0


# --------------------------------------------------------------------------
# screen
# --------------------------------------------------------------------------
def run_screen(args) -> int:
    norm_path = os.path.join(args.work, "case_normalized.json")
    norm, status, err = load_json(norm_path)
    if norm is None:
        print(f"screen: 缺 {norm_path} ({status})，先运行 discover")
        return 1
    candidates = norm.get("candidates") or []
    q_terms = sorted(tokenize(norm.get("research_question") or ""))

    rows = []
    by_title = {}
    by_doi = {}
    for rec in candidates:
        nt = norm_title(rec.get("title"))
        doi = norm_doi(rec.get("doi"))
        title_tokens = tokenize(rec.get("title") or "")
        abs_tokens = tokenize(rec.get("abstract") or "")
        title_matches = sorted(t for t in q_terms if t in title_tokens)
        abstract_matches = sorted(t for t in q_terms if t in abs_tokens)
        denom = 2 * len(q_terms) or 1
        base = (2 * len(title_matches) + len(abstract_matches)) / denom
        base_score = round(min(1.0, base), 4)
        rows.append({
            "record_id": rec["record_id"],
            "normalized_title": nt,
            "lexical": {
                "base_score": base_score,
                "title_matches": title_matches,
                "abstract_matches": abstract_matches,
            },
            "dedupe_group": None,
            "near_duplicate_of": [],
            "version_role": None,
        })
        if nt:
            by_title.setdefault(nt, []).append(rec["record_id"])
        if doi:
            by_doi.setdefault(doi, []).append(rec["record_id"])

    # 并查集：title / doi 聚类合并
    parent = {rec["record_id"]: rec["record_id"] for rec in candidates}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for group in by_title.values():
        for i in range(1, len(group)):
            union(group[0], group[i])
    for group in by_doi.values():
        for i in range(1, len(group)):
            union(group[0], group[i])

    groups = {}
    for rec in candidates:
        groups.setdefault(find(rec["record_id"]), []).append(rec["record_id"])

    dedupe_groups = []
    group_ids = {}
    gi = 0
    for root, members in groups.items():
        if len(members) < 2:
            continue
        gid = f"dg_{gi:04d}"
        gi += 1
        for m in members:
            group_ids[m] = gid
        dedupe_groups.append({
            "group_id": gid,
            "record_ids": sorted(members),
            "basis": "auto",
            "decision": "merge_after_review",
            "rationale": "规范化标题或 DOI 相同，判为同源变体，待复核裁定保留版本",
        })

    # 版本角色：组内 preprint vs published
    row_by_id = {r["record_id"]: r for r in rows}
    for gid, members in groups.items():
        if len(members) < 2:
            continue
        published = None
        preprint = None
        for m in members:
            r = row_by_id[m]
            st = next((c["source_type"] for c in candidates if c["record_id"] == m), "unknown")
            if st == "preprint":
                preprint = m
            elif published is None:
                published = m
        if published and preprint:
            row_by_id[published]["version_role"] = "published"
            row_by_id[preprint]["version_role"] = "preprint"
            for dg in dedupe_groups:
                if dg["group_id"] == gid:
                    dg["canonical"] = published
                    dg["decision"] = "keep_published_prefer_canonical"
                    break

    # 近似重复（不自动合并，交 Agent 裁定）：标题相似度 >= 0.9
    n = len(rows)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = rows[i], rows[j]
            if not a["normalized_title"] or not b["normalized_title"]:
                continue
            if a["record_id"] in group_ids and group_ids[a["record_id"]] == group_ids.get(b["record_id"]):
                continue
            ratio = difflib.SequenceMatcher(None, a["normalized_title"],
                                            b["normalized_title"]).ratio()
            if ratio >= 0.9:
                a["near_duplicate_of"].append(b["record_id"])
                b["near_duplicate_of"].append(a["record_id"])

    for r in rows:
        r["dedupe_group"] = group_ids.get(r["record_id"])

    scaffold = {
        "research_question": norm.get("research_question"),
        "q_terms": q_terms,
        "note": "词面先验，仅作 Agent 判断参考，禁止直接当最终 priority_score",
        "candidates": rows,
        "dedupe_groups": dedupe_groups,
    }
    write_json(os.path.join(args.work, "scaffold.json"), scaffold)
    print(f"screen: candidates={len(rows)} dedupe_groups={len(dedupe_groups)}")
    return 0


# --------------------------------------------------------------------------
# check（契约校验 + 证据溯源 + 自检）
# --------------------------------------------------------------------------
def _eval_pointer(obj, pointer: str):
    """解析 'papers[0].abstract' 风格的定位表达式。"""
    try:
        tokens = re.findall(r"[^.\[\]]+|\[\d+\]", pointer)
        cur = obj
        for tok in tokens:
            if tok.startswith("[") and tok.endswith("]"):
                cur = cur[int(tok[1:-1])]
            else:
                if isinstance(cur, list):
                    cur = cur[int(tok)]
                else:
                    cur = cur[tok]
        return cur
    except Exception:
        return None


def _pdf_text(path: str):
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "-layout", path, "-"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        return None
    return None


def _norm_contains(haystack: str, needle: str) -> bool:
    return normalize_ws(needle) in normalize_ws(haystack)


def _verify_quote(locator: str, quote: str, input_dir: str) -> str:
    if not locator or not quote:
        return "unverified"
    file_part, _, pointer = locator.partition("#")
    file_part = (file_part or "case.json").replace("\\", "/").lstrip("./")
    full = os.path.join(input_dir, file_part)
    if not os.path.exists(full):
        return "unresolvable"
    ext = os.path.splitext(full)[1].lower()
    if ext == ".pdf":
        text = _pdf_text(full)
        if text is None:
            return "unverified_pdf"
        return "ok" if _norm_contains(text, quote) else "quote_not_found"
    text, status = read_text(full)
    if text is None:
        return "unresolvable"
    if pointer:
        try:
            val = _eval_pointer(json.loads(text), pointer)
            if val is not None:
                s = _as_str(val)
                if s and quote in s:
                    return "ok"
                if s and _norm_contains(s, quote):
                    return "normalized_match"
                return "quote_not_found"
        except Exception:
            pass
    return "ok" if _norm_contains(text, quote) else "quote_not_found"


def run_check(args) -> int:
    result_path = os.path.join(args.output, "result.json")
    result, status, err = load_json(result_path)
    if result is None:
        print(f"check: 无法读取 {result_path} ({status})")
        return 1
    norm, _ns, _ne = load_json(os.path.join(args.work, "case_normalized.json"))
    candidates = (norm or {}).get("candidates") or []
    contract_fields = (norm or {}).get("contract_fields") or []

    errors, warnings = [], []

    # 1) 顶层必需字段
    for k in REQUIRED_TOP:
        if k not in result:
            errors.append(f"缺少顶层字段 {k}")
    if result.get("status") not in STATUSES:
        errors.append(f"非法 status: {result.get('status')}")
    # contract 全字段回填
    for name in contract_fields:
        if name not in result:
            errors.append(f"缺少 output_contract 字段 {name}")

    # 2) 覆盖完整性
    results = result.get("results") or []
    result_by_id = {}
    for r in results:
        rid = r.get("record_id")
        if rid in result_by_id:
            errors.append(f"record_id 重复: {rid}")
        result_by_id[rid] = r
    cand_by_id = {c["record_id"]: c for c in candidates}
    for c in candidates:
        if c["record_id"] not in result_by_id:
            errors.append(f"候选 {c['record_id']} 缺失结果: {c.get('title')}")
    for rid in result_by_id:
        if candidates and rid not in cand_by_id:
            warnings.append(f"结果含未知 record_id: {rid}")

    # 3) 逐条字段 + 元数据保真 + 证据溯源
    for r in results:
        for f in RESULT_REQUIRED:
            if f not in r:
                errors.append(f"{r.get('record_id')}: 缺少字段 {f}")
        if r.get("relevance") not in RELEVANCES:
            errors.append(f"{r.get('record_id')}: 非法 relevance {r.get('relevance')}")
        if r.get("tier") not in TIERS:
            errors.append(f"{r.get('record_id')}: 非法 tier {r.get('tier')}")
        if r.get("confidence") not in CONFIDENCES:
            warnings.append(f"{r.get('record_id')}: 非法 confidence {r.get('confidence')}")
        ps = r.get("priority_score")
        if not isinstance(ps, (int, float)) or not (0.0 <= float(ps) <= 1.0):
            errors.append(f"{r.get('record_id')}: priority_score 须在 0-1 区间")
        if not isinstance(r.get("rank"), int) or r.get("rank", 0) < 1:
            warnings.append(f"{r.get('record_id')}: rank 应为正整数")
        # 元数据保真：与输入一致，缺失一致填 null
        cand = cand_by_id.get(r.get("record_id"))
        if cand is not None:
            for f in ("title", "year", "doi", "venue"):
                expected = cand.get(f)
                got = r.get(f)
                if expected is not None and got != expected:
                    errors.append(f"{r.get('record_id')}: {f} 与输入不一致 "
                                  f"(输入 {expected!r}, 结果 {got!r})")
                if expected is None and got is not None:
                    warnings.append(f"{r.get('record_id')}: {f} 输入缺失却填了 {got!r}")
        # 证据溯源
        ev = r.get("evidence") or []
        if not isinstance(ev, list) or not ev:
            errors.append(f"{r.get('record_id')}: evidence 须为非空列表")
        for e in ev:
            if not isinstance(e, dict) or not e.get("locator") or not e.get("quote"):
                errors.append(f"{r.get('record_id')}: evidence 缺 locator/逐字 quote")
                continue
            verdict = _verify_quote(e["locator"], e["quote"], args.input)
            e["verified"] = verdict
            if verdict in ("quote_not_found",):
                errors.append(f"{r.get('record_id')}: 证据无法溯源 "
                              f"({e['locator']})")
            elif verdict in ("unverified_pdf", "unresolvable", "normalized_match"):
                warnings.append(f"{r.get('record_id')}: 证据溯源 {verdict} "
                                f"({e['locator']})")

    # 4) tier 顺序 + priority 降序
    def _safe_float(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    seq = [TIER_ORDER.get(r.get("tier"), 3) for r in results]
    if seq != sorted(seq):
        errors.append("tier 顺序应为 retain → pending → exclude")
    for a, b in zip(results, results[1:]):
        if a.get("tier") == b.get("tier"):
            pa, pb = _safe_float(a.get("priority_score")), _safe_float(b.get("priority_score"))
            if pa < pb:
                warnings.append(f"同 tier 内 priority_score 未降序: "
                                f"{a.get('record_id')} < {b.get('record_id')}")

    # 5) 规范化 rank（自动修正为 1..N，不改顺序）
    changed_rank = False
    for pos, r in enumerate(results, 1):
        if r.get("rank") != pos:
            r["rank"] = pos
            changed_rank = True
    if changed_rank:
        result.setdefault("change_log", []).append(
            {"ts": utcnow(), "step": "check", "action": "rank 顺序归一化为 1..N"})

    # 6) status 诚实性启发
    if errors and result.get("status") == "ok":
        result["status"] = "partial"
        result.setdefault("change_log", []).append(
            {"ts": utcnow(), "step": "check", "action": "存在 error，status 降为 partial"})
        warnings.append("存在 error，status 已由 ok 降为 partial")
    if not candidates and result.get("status") not in ("insufficient_evidence", "input_error"):
        warnings.append("候选为空但 status 非 insufficient_evidence")

    # 7) 回填 runtime
    runtime = result.setdefault("runtime", {})
    runtime["finished_at"] = utcnow()
    started = runtime.get("started_at")
    if started:
        try:
            from datetime import datetime as _dt
            t0 = _dt.fromisoformat(started.replace("Z", "+00:00"))
            t1 = _dt.fromisoformat(runtime["finished_at"].replace("Z", "+00:00"))
            runtime["duration_s"] = round((t1 - t0).total_seconds(), 1)
        except Exception:
            runtime["duration_s"] = None

    result["errors"] = [e for e in errors]
    result["warnings"] = warnings
    write_json(result_path, result)

    selfcheck = {
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "checked_at": utcnow(),
        "summary": "PASS" if not errors else "FAIL",
    }
    write_json(os.path.join(args.work, "selfcheck.json"), selfcheck)
    print(f"check: errors={len(errors)} warnings={len(warnings)}")
    return 0 if not errors else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="文献筛选与智能排序 Skill 管线（discover/screen/check）")
    p.add_argument("command", choices=["discover", "screen", "check"])
    p.add_argument("--input", default=os.environ.get("SKILL_INPUT", "/input"))
    p.add_argument("--work", default=os.environ.get("SKILL_WORK", "/app/work"))
    p.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", "/app/output"))
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        return run_discover(args)
    if args.command == "screen":
        return run_screen(args)
    if args.command == "check":
        return run_check(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
