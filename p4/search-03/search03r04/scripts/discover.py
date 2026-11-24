#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discover.py - 输入发现、容错解析、契约回填，写 result.json 骨架与 case_normalized.json。

只依赖标准库；确定性输出；任何异常都不崩溃，而是降级并写入 degradations/errors。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024
CONTRACT_KEYS = ("output_contract", "output_schema", "expected_fields",
                 "expected_output", "result_schema", "required_fields")
PAPER_ID_KEYS = ("record_id", "id", "paper_id", "pubmed_id", "pmid", "arxiv_id", "doi")
RESEARCH_Q_KEYS = ("research_question", "question", "research_query", "query",
                   "task_prompt", "prompt", "research_problem", "goal", "objective")
CRITERIA_KEYS = ("inclusion_criteria", "exclusion_criteria", "inclusion",
                 "exclusion", "criteria", "filters", "eligibility",
                 "screening_criteria", "inclusion_exclusion")
CANDIDATE_LIST_KEYS = ("papers", "candidates", "documents", "records", "results",
                       "items", "references", "literature", "entries", "search_results")
PAPER_HINTS = ("title", "abstract", "summary", "content", "snippet", "text",
               "excerpt", "full_text", "doi", "year", "authors", "author")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """容错读取文本。返回 (text_or_None, degradation_or_None)。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, {"kind": "read_error", "path": path, "note": str(exc)}
    if size > max_bytes:
        return None, {"kind": "oversize", "path": path, "bytes": size}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, {"kind": "read_error", "path": path, "note": str(exc)}
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), {"kind": "bad_encoding", "path": path}


def parse_json_text(text):
    """尽力解析 JSON；失败返回 (None, error_note)。"""
    if text is None:
        return None, "empty"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        # 容错：剥离可能包裹的 Markdown 围栏
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
        if m:
            try:
                return json.loads(m.group(1)), None
            except json.JSONDecodeError:
                pass
        return None, "bad_json"


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def norm_ws(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def is_paper_like(obj):
    """候选判定：有 title 即算候选（保证 title-only 条目不漏覆盖）；或 DOI+元数据也算。"""
    if not isinstance(obj, dict):
        return False
    has_title = bool(norm_ws(obj.get("title")))
    has_doi = bool(norm_ws(obj.get("doi")))
    has_content = any(norm_ws(obj.get(k)) for k in
                      ("abstract", "summary", "content", "snippet", "text",
                       "excerpt", "authors", "author", "year", "venue"))
    return has_title or (has_doi and has_content)


def walk_papers(node, out, seen_ids, locator_parts=()):
    """递归收集 paper-like dict，带 locator。"""
    if isinstance(node, dict):
        if is_paper_like(node):
            jid = id(node)
            if jid not in seen_ids:
                seen_ids.add(jid)
                out.append((node, ".".join(locator_parts)))
        for key, val in node.items():
            if key in ("materials", "sources"):
                continue
            walk_papers(val, out, seen_ids, locator_parts + (str(key),))
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            walk_papers(item, out, seen_ids, locator_parts + ("[%d]" % idx,))


def find_case_file(input_dir):
    for name in ("case.json", "input.json", "case_normalized.json"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            return p
    return None


def find_task_file(input_dir):
    for name in ("task.md", "README.md", "notes.md"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            return p
    return None


def list_materials(input_dir):
    out = []
    try:
        roots = [os.path.join(input_dir, "materials"), input_dir]
    except Exception:
        roots = [input_dir]
    seen = set()
    exts = (".pdf", ".md", ".txt", ".json", ".jsonl", ".csv", ".xml")
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not fn.lower().endswith(exts):
                    continue
                if fn in ("case.json", "input.json", "task.md", "case_normalized.json",
                          "scaffold.json", "result.json", "selfcheck.json"):
                    continue
                full = os.path.join(dirpath, fn)
                rp = os.path.relpath(full, input_dir)
                if rp in seen:
                    continue
                seen.add(rp)
                kind = "pdf" if fn.lower().endswith(".pdf") else "text"
                out.append((full, kind))
    return sorted(out, key=lambda x: x[0].replace("\\", "/").lower())


def extract_case(doc):
    """从 case.json(dict) 提取研究问题、候选、纳排标准、契约、case_id。"""
    if not isinstance(doc, dict):
        doc = {}
    case_id = None
    for k in ("case_id", "id", "challenge_id", "task_id"):
        if doc.get(k):
            case_id = str(doc[k])
            break
    if case_id is None:
        case_id = "unknown_case"

    research_question = None
    for k in RESEARCH_Q_KEYS:
        if doc.get(k):
            research_question = norm_ws(doc[k])
            break
    if research_question is None:
        # 在嵌套键中粗找
        for k in ("task", "research", "context", "input", "data"):
            sub = doc.get(k)
            if isinstance(sub, dict):
                for qk in RESEARCH_Q_KEYS:
                    if sub.get(qk):
                        research_question = norm_ws(sub[qk])
                        break
            if research_question:
                break

    candidates, seen_ids = [], set()
    # 1) 已知列表键优先：保持原输入顺序与 locator
    for k in CANDIDATE_LIST_KEYS:
        if k in doc and isinstance(doc[k], list):
            for idx, item in enumerate(doc[k]):
                if is_paper_like(item):
                    candidates.append((item, "%s[%d]" % (k, idx)))
            if candidates:
                break
    criteria = {"inclusion": [], "exclusion": []}
    # 2) 已知键为空则递归扫描整棵树（保底）
    if not candidates:
        walk_papers(doc, candidates, seen_ids, ())
    raw_criteria_found = False
    for k in CRITERIA_KEYS:
        if k in doc:
            raw_criteria_found = True
            val = doc[k]
            if isinstance(val, dict):
                inc = as_list(val.get("include") or val.get("include_criteria")
                              or val.get("inclusion"))
                exc = as_list(val.get("exclude") or val.get("exclude_criteria")
                              or val.get("exclusion"))
                if inc:
                    criteria["inclusion"] = inc
                if exc:
                    criteria["exclusion"] = exc
                if not inc and not exc:
                    criteria["raw"] = val
            elif isinstance(val, list):
                items = [norm_ws(x) for x in val if norm_ws(x)]
                if k.startswith("inclusion"):
                    criteria["inclusion"] = items
                elif k.startswith("exclusion"):
                    criteria["exclusion"] = items
                else:
                    criteria.setdefault("raw", items)
    if not raw_criteria_found:
        # 兜底：inclusion_exclusion 文本
        for k in ("inclusion_exclusion", "eligibility_text"):
            if doc.get(k):
                criteria["raw"] = as_list(doc[k])

    # 契约：合并所有契约键
    contract = {}
    for k in CONTRACT_KEYS:
        if k in doc:
            val = doc[k]
            if isinstance(val, dict):
                for ck in val.keys():
                    contract.setdefault(ck, None)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        contract.setdefault(item, None)
                    elif isinstance(item, dict):
                        for ck in item.keys():
                            contract.setdefault(ck, None)
    return {"case_id": case_id, "research_question": research_question,
            "candidates": candidates, "criteria": criteria, "contract": contract,
            "doc": doc}


def normalize_candidate(raw, idx, record_locator):
    rec = {}
    rec["record_id"] = None
    for k in PAPER_ID_KEYS:
        if isinstance(raw.get(k), str) and raw[k].strip():
            rec["record_id"] = raw[k].strip()
            break
    if rec["record_id"] is None:
        rec["record_id"] = "rec_%04d" % idx
    rec["title"] = norm_ws(raw.get("title"))
    authors = raw.get("authors")
    if isinstance(authors, list):
        authors = [norm_ws(a.get("name") if isinstance(a, dict) else a) for a in authors]
        authors = [a for a in authors if a]
    elif isinstance(authors, str):
        authors = [authors]
    else:
        authors = raw.get("author")
        authors = authors if isinstance(authors, list) else ([authors] if authors else [])
    rec["authors"] = [norm_ws(a) for a in authors if norm_ws(a)] or None
    rec["year"] = raw.get("year")
    rec["venue"] = norm_ws(raw.get("venue") or raw.get("journal") or
                           raw.get("source") or raw.get("conference")) or None
    rec["doi"] = norm_ws(raw.get("doi")) or None
    rec["url"] = norm_ws(raw.get("url") or raw.get("link") or raw.get("pdf_url")) or None
    rec["abstract"] = norm_ws(raw.get("abstract") or raw.get("summary") or
                              raw.get("content") or raw.get("snippet") or
                              raw.get("text") or raw.get("excerpt")) or None
    st = norm_ws(raw.get("source_type") or raw.get("type") or raw.get("entry_type"))
    rec["source_type"] = st or None
    rec["source_locator"] = record_locator
    rec["raw"] = raw
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description="文献筛选 Skill 输入发现")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)

    degradations, errors = [], []
    sources = []
    change_log = [{"ts": now_iso(), "step": "discover", "action": "start"}]

    case_path = find_case_file(args.input)
    task_path = find_task_file(args.input)
    materials = list_materials(args.input)

    case_text, case_deg = None, None
    case_doc = {}
    if case_path:
        case_text, case_deg = read_text(case_path)
        if case_deg:
            degradations.append({"where": "case.json", **case_deg})
        sources.append({"path": case_path, "kind": "json", "role": "task_input"})
        case_doc, parse_err = parse_json_text(case_text) if case_text else ({}, "empty")
        if case_doc is None:
            case_doc = {}
        if parse_err:
            errors.append({"where": "case.json", "note": parse_err})
            degradations.append({"where": "case.json", "kind": "bad_json",
                                 "note": "falling back to task.md text"})
    else:
        degradations.append({"kind": "missing_case_json", "note": "no case.json found"})

    task_text, task_deg = None, None
    if task_path:
        task_text, task_deg = read_text(task_path)
        if task_deg:
            degradations.append({"where": "task.md", **task_deg})
        sources.append({"path": task_path, "kind": "markdown", "role": "task_description"})

    for full, kind in materials:
        sources.append({"path": full, "kind": kind, "role": "material"})

    ex = extract_case(case_doc)

    # case_id 兜底来自 task.md 的 challenge_id
    if ex["case_id"] == "unknown_case" and task_text:
        m = re.search(r"challenge[_-]?id[:：]\s*`?([0-9a-zA-Z_-]+)`?", task_text)
        if m:
            ex["case_id"] = m.group(1)
    # 研究问题兜底：从 task.md 提取标题
    if not ex["research_question"] and task_text:
        m = re.search(r"^#\s+(.+)$", task_text, re.M)
        if m:
            ex["research_question"] = norm_ws(m.group(1))

    normalized = [normalize_candidate(raw, i, loc) for i, (raw, loc) in enumerate(ex["candidates"])]

    # input_state 判定；input_error 保留给 Agent 在软件/权限/接口失败时手工设置
    if not case_path and not task_path and not materials:
        input_state = "empty"
    elif not normalized:
        input_state = "no_candidates"
    else:
        input_state = "ok"

    cand_out = [dict(c) for c in normalized]

    case_normalized = {
        "case_id": ex["case_id"],
        "research_question": ex["research_question"],
        "criteria": ex["criteria"],
        "output_contract": ex["contract"],
        "candidates": cand_out,
        "sources": sources,
        "degradations": degradations,
        "input_state": input_state,
        "task_md_path": task_path,
        "case_json_path": case_path,
        "discovered_at": now_iso(),
    }
    npath = os.path.join(args.work, "case_normalized.json")
    with open(npath, "w", encoding="utf-8") as fh:
        json.dump(case_normalized, fh, ensure_ascii=False, indent=2)

    # ---- 落盘 result.json 骨架（前 3 分钟铁律触发点）----
    status = {"empty": "insufficient_evidence", "no_candidates": "insufficient_evidence",
              "error": "input_error"}.get(input_state, "partial")
    if normalized and input_state == "ok" and not degradations and not errors:
        status = "partial"  # 尚未逐条判断：诚实记为 partial，Agent 后续升级为 ok/partial
    om_str = ""
    if input_state in ("empty", "no_candidates"):
        om_str = ("未发现候选文献" if input_state == "no_candidates"
                   else "输入目录为空（无 case.json/task.md/材料）")
    elif input_state == "error":
        om_str = "case.json 与 task.md 均无法解析"

    skeleton = {
        "case_id": ex["case_id"],
        "status": status,
        # 契约回填：先放所有契约键（缺值 null），再放本 Skill 默认键
        "results": [],
        "evidence": [],
        "status_notes": "" if not om_str else om_str,
        "omissions": [] if not om_str else [{"item": "candidates", "note": om_str}],
        "dedupe_groups": [],
        "sources": sources,
        "uncertainty": [],
        "limitations": [] if not degradations else [
            {"scope": deg.get("where", "input"), "note": deg.get("kind", str(deg))}
            for deg in degradations
        ],
        "pending_human": [],
        "errors": [dict(e) for e in errors],
        "degradations": [dict(d) for d in degradations],
        "change_log": change_log,
        "method": {"scoring": "0-1 语义优先，词面仅先验", "tie_break":
                   ["priority_score desc", "evidence_completeness desc", "year desc",
                    "norm_title asc", "record_id asc"], "deterministic": True,
                   "k_value": None, "note": "k 在步骤 5 依据纳排标准写入"},
        "runtime": {"started_at": now_iso(), "finished_at": None,
                    "model_requests_estimate": None, "elapsed_seconds": None},
    }
    # 契约键透传（不覆盖已定义的本 Skill 默认键）
    for ck, cv in ex["contract"].items():
        if ck not in skeleton:
            skeleton[ck] = cv if cv is not None else None

    rpath = os.path.join(args.output, "result.json")
    with open(rpath, "w", encoding="utf-8") as fh:
        json.dump(skeleton, fh, ensure_ascii=False, indent=2)

    print("discover: case_id=%s input_state=%s candidates=%d status=%s" %
          (ex["case_id"], input_state, len(normalized), status))
    print("  wrote: %s" % npath)
    print("  wrote: %s" % rpath)
    if degradations:
        print("  degradations: %d" % len(degradations))
    return 0


if __name__ == "__main__":
    sys.exit(main())