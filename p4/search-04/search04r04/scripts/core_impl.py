#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 引文追踪与关联文献发现 Skill 的实现库与命令行入口。

仅依赖 Python 标准库；可作模块导入（`import core_impl`），也可作 CLI 运行。
设计目标：确定性、可复核、诚实降级，任何异常都不留下空/损坏的 result.json。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

VERSION = "1.0.0"
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
_PROCESS_START = time.time()  # 模块级兜底，便于直接 import 调用

VALID_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")
TEXT_SUFFIXES = (
    ".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml",
    ".bib", ".ris", ".tex", ".html", ".htm",
)
MAX_TEXT_BYTES = 2_000_000

# 契约键若形似集合，缺省回填 [] 而非 null。
_LIST_HINT = re.compile(
    r"^(papers?|edges?|clusters?|paths?|sources?|results?|candidates?|references?|"
    r"nodes?|links?|items?|records?|errors?|degradations?|uncertaint(y|ies)|"
    r"pending_human|limitations?|change_?log|pruned|missing?|gaps?|merged?|merge_?log|"
    r"findings?|outputs?|evidence|topics?)$",
    re.I,
)

_CONF_ORDER = {"confirmed": 5, "verified": 4, "inferred": 3, "unverified": 2, "not_reported": 1}

RELEVANCE_WEIGHT = {
    "method_source": 100,
    "data_source": 95,
    "foundational": 90,
    "follow_up": 85,
    "critique": 80,
    "parallel": 70,
    "bridge": 60,
    "hotspot": 50,
    "thematic": 40,
    "other": 20,
}

# --------------------------------------------------------------------------- #
# 时间与工具
# --------------------------------------------------------------------------- #

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# --------------------------------------------------------------------------- #
# 输入发现与容错读取
# --------------------------------------------------------------------------- #

def list_input_files(input_dir: str):
    """递归扫描输入目录，返回绝对路径列表（确定性顺序）。"""
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__", ".venv", "node_modules"))
        for n in sorted(names):
            files.append(os.path.join(root, n))
    return files


def rel_locator(path: str, input_dir: str) -> str:
    try:
        rel = os.path.relpath(path, input_dir)
    except ValueError:
        rel = os.path.basename(path)
    return rel.replace("\\", "/")


def read_text(path: str, max_bytes: int = MAX_TEXT_BYTES):
    """utf-8-sig 优先容错读取；返回 (text|None, info)。"""
    info = {"path": path, "ok": False, "encoding": None, "skipped": False, "error": None}
    try:
        size = os.path.getsize(path)
    except OSError as e:
        info["error"] = str(e)
        return None, info
    if size > max_bytes:
        info["skipped"] = True
        info["error"] = f"file exceeds {max_bytes} bytes, skipped"
        return None, info
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
            info["ok"] = True
            info["encoding"] = enc
            return text, info
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as e:
            info["error"] = str(e)
            return None, info
    # 最后兜底：替换坏字节，绝不崩溃。
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        info["ok"] = True
        info["encoding"] = "utf-8-replace"
        return text, info
    except OSError as e:
        info["error"] = str(e)
        return None, info


def load_case(input_dir: str):
    """发现并读取 case.json / task.md，不假设固定文件名或键名。"""
    out = {"case": None, "task_text": None, "files": [], "read_errors": [], "degradations": []}
    if not os.path.isdir(input_dir):
        out["degradations"].append(f"input directory missing: {input_dir}")
        return out
    for path in list_input_files(input_dir):
        base = os.path.basename(path).lower()
        rel = rel_locator(path, input_dir)
        out["files"].append(rel)
        if base == "case.json":
            text, info = read_text(path)
            if text is None:
                out["read_errors"].append(info)
                continue
            if info["encoding"] in ("latin-1", "utf-8-replace"):
                out["degradations"].append(f"case.json read with fallback encoding {info['encoding']}")
            try:
                out["case"] = json.loads(text)
            except (json.JSONDecodeError, ValueError) as e:
                out["read_errors"].append({"path": rel, "error": f"bad JSON: {e}"})
                out["case"] = None
        elif base == "task.md":
            text, info = read_text(path)
            if text is not None:
                out["task_text"] = text
            else:
                out["read_errors"].append(info)
    return out


def extract_contract_keys(case):
    """尽力提取 output_contract 的键（dict / list / JSON-schema properties / 字符串）。"""
    if not isinstance(case, dict):
        return []
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        props = oc.get("properties")
        if isinstance(props, dict) and props:
            keys = [str(k) for k in props]
        else:
            keys = [str(k) for k in oc if k not in (
                "$schema", "title", "type", "required", "description", "additionalProperties", "definitions",
            )]
        if not keys:
            keys = [str(k) for k in oc]
        return _dedup_preserve_order(keys)
    if isinstance(oc, list):
        keys = []
        for item in oc:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict) and "name" in item:
                keys.append(str(item["name"]))
        return _dedup_preserve_order(keys)
    if isinstance(oc, str) and oc.strip():
        return [oc.strip()]
    # 兜底：找形似主产物的字段。
    for hint in ("results", "papers", "candidates", "references", "edges", "paths", "clusters", "findings"):
        if hint in case:
            return [hint]
    return []


def _dedup_preserve_order(items):
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def default_for_key(key: str):
    return [] if _LIST_HINT.match(key or "") else None


# --------------------------------------------------------------------------- #
# 骨架与落盘
# --------------------------------------------------------------------------- #

def build_skeleton(case, input_dir: str, files=None):
    case_id = None
    contract = []
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or case.get("challenge_id")
        contract = extract_contract_keys(case)
    skeleton = {
        "case_id": case_id,
        "status": "insufficient_evidence",
        "status_detail": "",
        "seed": None,
        "seed_key": None,
        "research_question": None,
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "results": {
            "papers": [],
            "edges": [],
            "clusters": [],
            "paths": [],
            "pruned": [],
        },
        "runtime": {
            "started_at": now_iso(),
            "finished_at": None,
            "duration_s": None,
            "input_dir": input_dir,
            "input_files": list(files or []),
            "tool_version": VERSION,
        },
    }
    for k in contract:
        skeleton[k] = default_for_key(k)
    return skeleton


def _err_entry(err):
    if isinstance(err, dict):
        return err
    return {"detail": str(err)}


def write_result(result, output_dir: str):
    """原子写入 result.json（先写 .tmp 再替换）。"""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "result.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def write_result_safe(result, output_dir: str, work_dir: str):
    """逐级降级落盘；绝不抛异常，返回最终路径或 None。"""
    last_err = None
    for target in (output_dir, work_dir, os.getcwd()):
        try:
            return write_result(result, target)
        except OSError as e:
            last_err = e
    try:
        sys.stdout.write(json.dumps(
            {"case_id": result.get("case_id"), "status": "input_error", "error": str(last_err)},
            ensure_ascii=False,
        ))
    except Exception:
        pass
    return None


def load_result(output_dir: str):
    path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(path):
        return None
    text, info = read_text(path, max_bytes=64_000_000)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def save_state(work_dir: str, state):
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, "state.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def load_state(work_dir: str):
    path = os.path.join(work_dir, "state.json")
    if not os.path.isfile(path):
        return {}
    text, _info = read_text(path, max_bytes=8_000_000)
    if text is None:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}


def append_change(result, action: str, reason: str):
    result.setdefault("change_log", []).append({
        "time": now_iso(),
        "action": action,
        "reason": reason or "",
    })


# --------------------------------------------------------------------------- #
# 记录合并（增量改进的落点）
# --------------------------------------------------------------------------- #

def _loc_key(e):
    return e.get("locator") if isinstance(e, dict) else e


def union_keep_order(a, b, key=None):
    out = list(a or [])
    seen = set()
    for x in out:
        try:
            seen.add(key(x) if key else x)
        except (TypeError, ValueError):
            seen.add(repr(x))
    for x in (b or []):
        try:
            k = key(x) if key else x
        except (TypeError, ValueError):
            k = repr(x)
        if k not in seen:
            out.append(x)
            seen.add(k)
    return out


def upsert_record(dst, src):
    """同 id 重录：后到标量覆盖旧值；证据/来源清单按 locator 并集。"""
    for k, v in src.items():
        if k == "id":
            continue
        if k in ("evidence", "source_ids", "merged_from"):
            key = _loc_key if k == "evidence" else None
            dst[k] = union_keep_order(dst.get(k), v, key=key)
        else:
            dst[k] = v
    return dst


def _record_key(rec, kind):
    if isinstance(rec, dict):
        if rec.get("id") is not None:
            return ("id", rec["id"])
        if kind == "sources" and rec.get("locator") is not None:
            return ("locator", rec["locator"])
    return ("value", repr(rec))


def merge_records(result, kind, records, reason):
    if kind in ("papers", "edges", "clusters", "paths", "pruned"):
        bucket = result.setdefault("results", {})
        lst = bucket.setdefault(kind, [])
    elif kind == "sources":
        lst = result.setdefault("sources", [])
    elif kind == "uncertainty":
        lst = result.setdefault("uncertainty", [])
    else:
        lst = result.setdefault(kind, [])
    index = {}
    for i, item in enumerate(lst):
        index[_record_key(item, kind)] = i
    n_new = n_upd = 0
    for rec in _as_list(records):
        key = _record_key(rec, kind)
        if key in index:
            lst[index[key]] = upsert_record(lst[index[key]], rec)
            n_upd += 1
        else:
            lst.append(dict(rec))
            index[key] = len(lst) - 1
            n_new += 1
    append_change(result, f"record:{kind}", reason or f"merge {kind}: +{n_new} / ~{n_upd}")
    return result


# --------------------------------------------------------------------------- #
# 身份规范化与去重
# --------------------------------------------------------------------------- #

def normalize_doi(v):
    if not v:
        return None
    s = str(v).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s or None


def normalize_arxiv(v):
    if not v:
        return None
    s = str(v).strip().lower()
    s = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", s)
    s = re.sub(r"^arxiv:\s*", "", s)
    s = re.sub(r"v\d+$", "", s)
    return s or None


def normalize_title(v):
    if not v:
        return None
    s = str(v).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s or None


def first_author_surname(paper):
    authors = paper.get("authors")
    if isinstance(authors, str):
        first = authors.split(";")[0].split(",")[0]
    elif isinstance(authors, list) and authors:
        first = authors[0]
    else:
        first = paper.get("first_author")
    if not first:
        return None
    tokens = str(first).strip().split()
    return tokens[-1].lower() if tokens else str(first).strip().lower()


def identity_key(paper):
    """由强到弱返回候选身份键：DOI → arXiv → 标题+一作姓+年份 → 标题。"""
    keys = []
    doi = normalize_doi(paper.get("doi"))
    if doi:
        keys.append(("doi", doi))
    arxiv = normalize_arxiv(paper.get("arxiv"))
    if arxiv:
        keys.append(("arxiv", arxiv))
    title = normalize_title(paper.get("title"))
    surname = first_author_surname(paper)
    year = paper.get("year")
    if title and surname and year:
        keys.append(("title_author_year", f"{title} || {surname} || {year}"))
    if title:
        keys.append(("title", title))
    return keys


def better_confidence(a, b):
    return a if _CONF_ORDER.get(a, 0) >= _CONF_ORDER.get(b, 0) else b


def confidence_score(conf):
    return _CONF_ORDER.get(conf, 0)


def merge_paper(dst, src, via=None):
    """身份合并：保留非空字段、更强置信度，证据并集，记录合并证据。"""
    for f in ("title", "doi", "arxiv", "year", "authors", "journal", "volume", "issue",
              "pages", "type", "url", "venue", "publisher", "relevance_class", "notes"):
        if not dst.get(f) and src.get(f):
            dst[f] = src[f]
    dst["confidence"] = better_confidence(dst.get("confidence"), src.get("confidence"))
    dst["evidence"] = union_keep_order(dst.get("evidence"), src.get("evidence"), key=_loc_key)
    dst["source_ids"] = union_keep_order(dst.get("source_ids"), src.get("source_ids"))
    if via:
        dst.setdefault("merged_from", []).append(via)
    return dst


def dedupe(papers):
    """按身份键优先级合并同文不同版本；返回 (去重后列表, 合并证据)。"""
    keymap = {}
    canon = {}
    order = []
    merge_evidence = []
    for p in papers:
        matched = None
        matched_how = None
        for ktype, kval in identity_key(p):
            if (ktype, kval) in keymap:
                matched = keymap[(ktype, kval)]
                matched_how = f"{ktype}:{kval}"
                break
        if matched is None:
            cid = p.get("id") or _make_id(p)
            c = dict(p)
            c["id"] = cid
            canon[cid] = c
            order.append(cid)
            for ktype, kval in identity_key(p):
                keymap.setdefault((ktype, kval), cid)
        else:
            src_id = p.get("id") or matched_how
            via = {"id": src_id, "keys": [{"type": k, "value": v} for k, v in identity_key(p)]}
            merge_paper(canon[matched], p, via=via)
            merge_evidence.append({"kept": matched, "merged": src_id, "via": matched_how})
    return [canon[c] for c in order], merge_evidence


def _make_id(paper):
    slug = re.sub(r"[^a-z0-9]+", "-", (normalize_title(paper.get("title")) or "paper").strip())[:48]
    return slug or "paper"


# --------------------------------------------------------------------------- #
# 排序与路径
# --------------------------------------------------------------------------- #

def bfs_distances(edges, seed_id):
    """无向 BFS 层数（仅 relation == cites 的边计入引用距离）。"""
    adj = {}
    for e in edges or []:
        if e.get("relation") not in (None, "cites"):
            continue
        s, t = e.get("source"), e.get("target")
        if s and t:
            adj.setdefault(s, set()).add(t)
            adj.setdefault(t, set()).add(s)
    if not seed_id:
        return {}
    dist = {seed_id: 0}
    q = deque([seed_id])
    while q:
        u = q.popleft()
        for v in sorted(adj.get(u, ())):
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


def rank_papers(papers, edges, seed_id):
    """确定性排序：显式 rank 优先 → 得分降序 → id 升序；不是"高被引=高相关"。"""
    dist = bfs_distances(edges, seed_id)
    scored = []
    for p in papers:
        pid = p.get("id")
        cls = p.get("relevance_class") or "other"
        base = RELEVANCE_WEIGHT.get(cls, 20)
        d = dist.get(pid)
        d_pen = 0 if d is None else min(int(d), 5) * 6
        conf = confidence_score(p.get("confidence"))
        year = p.get("year")
        rec = 0.0
        if isinstance(year, (int, float)):
            rec = max(0.0, min(5.0, (float(year) - 1990.0) / 8.0))
        explicit = p.get("rank")
        score = round(base - d_pen + conf + rec, 3)
        scored.append(dict(p, rank_score=score, distance=d))
    scored.sort(key=lambda x: (
        0 if x.get("rank") is not None else 1,
        x.get("rank") if x.get("rank") is not None else 0,
        -x.get("rank_score", 0),
        str(x.get("id") or ""),
    ))
    return scored


def shortest_path(edges, source_id, target_id):
    """有向最短路径（source cites ... cites target）；返回 id 列表或 None。"""
    adj = {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if s and t:
            adj.setdefault(s, []).append(t)
    if source_id == target_id:
        return [source_id]
    prev = {source_id: None}
    q = deque([source_id])
    while q:
        u = q.popleft()
        if u == target_id:
            break
        for v in adj.get(u, ()):
            if v not in prev:
                prev[v] = u
                q.append(v)
    if target_id not in prev:
        return None
    path = []
    cur = target_id
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return list(reversed(path))


def _edge_between(edges, a, b):
    for e in edges or []:
        if e.get("source") == a and e.get("target") == b:
            return e
    return None


def build_path_object(edges, source_id, target_id, pid):
    path = shortest_path(edges, source_id, target_id)
    if path is None:
        return {"id": pid, "from": source_id, "to": target_id, "reachable": False,
                "hops": [], "length": None, "confidence": "not_reported",
                "note": "no directed citation path found"}
    hops = []
    for a, b in zip(path[:-1], path[1:]):
        e = _edge_between(edges, a, b)
        hops.append({
            "from": a, "to": b,
            "edge_id": e.get("id") if e else None,
            "evidence": (e.get("evidence") or []) if e else [],
        })
    conf = "confirmed" if all(h.get("evidence") for h in hops) else "inferred"
    return {"id": pid, "from": source_id, "to": target_id, "reachable": True,
            "hops": hops, "length": len(path) - 1, "confidence": conf,
            "note": ""}


# --------------------------------------------------------------------------- #
# 产物：报告 / CSV / SVG
# --------------------------------------------------------------------------- #

def _fmt_year(p):
    y = p.get("year")
    return str(y) if y is not None else ""


def _fmt_meta(p):
    parts = []
    if p.get("doi"):
        parts.append(p["doi"])
    if p.get("arxiv"):
        parts.append("arXiv:" + p["arxiv"])
    return "; ".join(parts)


def build_report(result):
    L = []
    L.append("# 引文追踪与关联文献发现：结果报告")
    L.append("")
    L.append(f"- case_id: `{result.get('case_id')}`")
    L.append(f"- status: `{result.get('status')}`")
    if result.get("status_detail"):
        L.append(f"- status_detail: {result['status_detail']}")
    seed = result.get("seed") if isinstance(result.get("seed"), dict) else {}
    L.append(f"- seed_key: `{result.get('seed_key')}`")
    if seed.get("title"):
        L.append(f"- seed title: {seed.get('title')}")
    if result.get("research_question"):
        L.append(f"- research_question: {result.get('research_question')}")
    L.append("")
    papers = result.get("results", {}).get("papers", [])
    edges = result.get("results", {}).get("edges", [])
    paths = result.get("results", {}).get("paths", [])
    L.append(f"## 文献（{len(papers)} 篇）")
    L.append("")
    L.append("| # | id | 标题 | 年份 | 类别 | 置信度 | 距离 | 证据定位 |")
    L.append("|---|----|------|------|------|--------|------|----------|")
    for i, p in enumerate(papers, 1):
        title = (p.get("title") or "").replace("|", "\\|")
        locs = "; ".join((e.get("locator") or "") for e in (p.get("evidence") or []))
        L.append(f"| {i} | {p.get('id')} | {title} | {_fmt_year(p)} | "
                 f"{p.get('relevance_class')} | {p.get('confidence')} | {p.get('distance')} | {locs} |")
    L.append("")
    L.append(f"## 引用边（{len(edges)} 条，source cites target）")
    L.append("")
    L.append("| id | source | target | relation | direction | 证据定位 |")
    L.append("|----|--------|--------|----------|-----------|----------|")
    for e in edges:
        locs = "; ".join((ev.get("locator") or "") for ev in (e.get("evidence") or []))
        L.append(f"| {e.get('id')} | {e.get('source')} | {e.get('target')} | "
                 f"{e.get('relation')} | {e.get('direction')} | {locs} |")
    L.append("")
    L.append(f"## 路径（{len(paths)} 条）")
    L.append("")
    for pth in paths:
        hop = " → ".join([h.get("from") for h in pth.get("hops", [])] + [pth.get("to")])
        L.append(f"- `{pth.get('id')}`: {hop}（可达={pth.get('reachable')}, 长度={pth.get('length')}）")
    L.append("")
    unc = result.get("uncertainty", [])
    L.append(f"## 不确定性（{len(unc)} 条）")
    L.append("")
    for u in unc:
        L.append(f"- [{u.get('type')}] {u.get('subject')}: {u.get('detail')}（影响：{u.get('affects')}）")
    L.append("")
    ph = result.get("pending_human", [])
    L.append(f"## 待人工复核（{len(ph)} 条）")
    L.append("")
    for h in ph:
        L.append(f"- {h}")
    L.append("")
    lim = result.get("limitations", [])
    L.append(f"## 局限性（{len(lim)} 条）")
    L.append("")
    for h in lim:
        L.append(f"- {h}")
    L.append("")
    L.append("> 本报告由 `scripts/core_impl.py finalize` 从 result.json 确定性生成；")
    L.append("> 证据以 locator + 逐字 quote 存于 result.json，报告仅作阅读摘要。")
    return "\n".join(L) + "\n"


def build_papers_csv(result):
    papers = result.get("results", {}).get("papers", [])
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["id", "title", "authors", "year", "doi", "arxiv", "type",
                     "relevance_class", "confidence", "distance", "rank_score",
                     "source_ids", "evidence_locators"])
    for p in papers:
        authors = p.get("authors")
        if isinstance(authors, list):
            authors = "; ".join(str(a) for a in authors)
        locs = "; ".join((e.get("locator") or "") for e in (p.get("evidence") or []))
        src_ids = "; ".join(str(x) for x in (p.get("source_ids") or []))
        writer.writerow([p.get("id"), p.get("title"), authors, _fmt_year(p),
                         p.get("doi"), p.get("arxiv"), p.get("type"),
                         p.get("relevance_class"), p.get("confidence"), p.get("distance"),
                         p.get("rank_score"), src_ids, locs])
    return buf.getvalue()


def build_edges_csv(result):
    edges = result.get("results", {}).get("edges", [])
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["id", "source", "target", "relation", "direction",
                     "source_label", "target_label", "confidence", "evidence_locators"])
    for e in edges:
        locs = "; ".join((ev.get("locator") or "") for ev in (e.get("evidence") or []))
        writer.writerow([e.get("id"), e.get("source"), e.get("target"), e.get("relation"),
                         e.get("direction"), e.get("source_label"), e.get("target_label"),
                         e.get("confidence"), locs])
    return buf.getvalue()


def short_label(pid, p, n=22):
    t = (p.get("title") or pid or "").strip()
    return (t[:n] + "…") if len(t) > n else t


def build_svg(result):
    papers = result.get("results", {}).get("papers", [])
    edges = result.get("results", {}).get("edges", [])
    seed_key = result.get("seed_key")
    pmap = {p.get("id"): p for p in papers if p.get("id")}

    dist = bfs_distances(edges, seed_key) if seed_key else {}
    max_d = max(dist.values()) if dist else 0
    for p in papers:
        if p.get("id") not in dist:
            dist[p.get("id")] = max_d + 1

    layers = {}
    for p in papers:
        layers.setdefault(dist.get(p.get("id"), 0), []).append(p)
    for k in layers:
        layers[k].sort(key=lambda p: str(p.get("id") or ""))

    node_w, node_h = 210, 60
    gap_x, gap_y = 70, 26
    margin = 40
    max_rows = max((len(v) for v in layers.values()), default=1)
    n_layers = len(layers)
    width = margin * 2 + (n_layers - 1) * (node_w + gap_x) + node_w
    height = margin * 2 + (max_rows - 1) * (node_h + gap_y) + node_h

    pos = {}
    layer_keys = sorted(layers)
    for li, lk in enumerate(layer_keys):
        for ri, p in enumerate(layers[lk]):
            x = margin + li * (node_w + gap_x)
            y = margin + ri * (node_h + gap_y)
            pos[p.get("id")] = (x, y)

    color = {
        "forward": "#2b6cb0",
        "backward": "#2f855a",
        "direction_unknown": "#b7791f",
        "parallel": "#6b46c1",
    }

    parts = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                 f'viewBox="0 0 {width} {height}" font-family="sans-serif">')
    parts.append("<defs><marker id=\"arr\" viewBox=\"0 0 10 10\" refX=\"9\" refY=\"5\" "
                 "markerWidth=\"8\" markerHeight=\"8\" orient=\"auto-start-reverse\">"
                 "<path d=\"M 0 0 L 10 5 L 0 10 z\" fill=\"#555\"/></marker></defs>")
    parts.append("<rect x=\"0\" y=\"0\" width=\"100%\" height=\"100%\" fill=\"white\"/>")

    # edges first（画在节点下方）
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s not in pos or t not in pos:
            continue
        rel = e.get("relation") or "cites"
        direction = e.get("direction") if rel == "cites" else "parallel"
        stroke = color.get(direction, "#555")
        dash = " stroke-dasharray=\"6 4\"" if rel != "cites" else ""
        x1, y1 = pos[s]
        x2, y2 = pos[t]
        sx = x1 + node_w
        sy = y1 + node_h / 2
        tx = x2
        ty = y2 + node_h / 2
        parts.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" '
                     f'stroke="{stroke}" stroke-width="1.4" marker-end="url(#arr)"{dash}>'
                     f'<title>{xml_escape(f"{e.get('id')}: {s} -{rel}-> {t}")}</title></line>')

    # nodes
    for p in papers:
        pid = p.get("id")
        if pid not in pos:
            continue
        x, y = pos[pid]
        is_seed = bool(seed_key and pid == seed_key)
        fill = "#fef3c7" if is_seed else "#edf2f7"
        stroke = "#b45309" if is_seed else "#4a5568"
        label = short_label(pid, p)
        parts.append(f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" rx="6" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        parts.append(f'<text x="{x + 8}" y="{y + 20}" font-size="12" font-weight="bold" '
                     f'fill="#1a202c">{xml_escape(pid)}</text>')
        parts.append(f'<text x="{x + 8}" y="{y + 38}" font-size="10" fill="#4a5568">'
                     f'{xml_escape(label)}</text>')
        meta = _fmt_meta(p)
        parts.append(f'<text x="{x + 8}" y="{y + 52}" font-size="9" fill="#718096">'
                     f'{xml_escape(meta[:44])}</text>')
        parts.append(f'<title>{xml_escape(str(p.get("title") or pid))}</title>')
    parts.append("</svg>\n")
    return "\n".join(parts)


def write_artifact(path, content, encoding="utf-8"):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(content)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# 校验与 finalize
# --------------------------------------------------------------------------- #

def validate_result(result, contract_keys=None):
    msgs = []
    if not isinstance(result, dict):
        return False, ["result.json is not an object"]
    if not result.get("case_id"):
        msgs.append("case_id missing/empty")
    if result.get("status") not in VALID_STATUS:
        msgs.append(f"status not in {VALID_STATUS}: {result.get('status')}")
    papers = result.get("results", {}).get("papers", [])
    for p in papers:
        if not p.get("id"):
            msgs.append("paper without id")
    edges = result.get("results", {}).get("edges", [])
    for e in edges:
        if not (e.get("source") and e.get("target")):
            msgs.append(f"edge missing source/target: {e.get('id')}")
    for k in (contract_keys or []):
        if k not in result:
            msgs.append(f"output_contract key missing: {k}")
    return (len(msgs) == 0), msgs


def finalize(output_dir: str, work_dir: str):
    result = load_result(output_dir)
    state = load_state(work_dir)
    contract_keys = state.get("contract_keys") or []
    if result is None:
        result = build_skeleton(None, output_dir, files=[])
        result["status"] = "input_error"
        result["status_detail"] = "result.json missing or corrupt at finalize"
        result["errors"].append({"detail": "result.json missing or corrupt"})
    result.setdefault("results", {})
    result["results"].setdefault("papers", [])
    result["results"].setdefault("edges", [])
    result["results"].setdefault("clusters", [])
    result["results"].setdefault("paths", [])
    result["results"].setdefault("pruned", [])
    for k in contract_keys:
        result.setdefault(k, default_for_key(k))

    ok, msgs = validate_result(result, contract_keys)
    runtime = result.setdefault("runtime", {})
    runtime["finished_at"] = now_iso()
    try:
        runtime["duration_s"] = round(time.time() - _PROCESS_START, 3)
    except Exception:
        runtime["duration_s"] = None
    runtime["validation_ok"] = ok
    runtime["validation"] = msgs

    write_result(result, output_dir)

    report = build_report(result)
    write_artifact(os.path.join(output_dir, "report.md"), report)
    write_artifact(os.path.join(output_dir, "papers.csv"), build_papers_csv(result), encoding="utf-8-sig")
    write_artifact(os.path.join(output_dir, "edges.csv"), build_edges_csv(result), encoding="utf-8-sig")
    write_artifact(os.path.join(output_dir, "citation_graph.svg"), build_svg(result))

    summary = {
        "ok": ok,
        "status": result.get("status"),
        "papers": len(result["results"]["papers"]),
        "edges": len(result["results"]["edges"]),
        "paths": len(result["results"]["paths"]),
        "validation": msgs,
    }
    write_artifact(os.path.join(work_dir, "finalize_summary.json"),
                   json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #

def cmd_bootstrap(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    os.makedirs(work_dir, exist_ok=True)
    loaded = load_case(input_dir)
    case = loaded["case"]
    task_text = loaded["task_text"]
    skeleton = build_skeleton(case, input_dir, files=loaded["files"])
    skeleton["runtime"]["input_files"] = loaded["files"]
    for err in loaded["read_errors"]:
        skeleton["errors"].append(_err_entry(err))
    skeleton["degradations"].extend(loaded["degradations"])
    if case is None and task_text is None:
        skeleton["status"] = "input_error"
        skeleton["status_detail"] = "case.json 与 task.md 均缺失或不可读"
    elif case is None:
        skeleton["status"] = "input_error"
        skeleton["status_detail"] = "case.json 缺失或 JSON 无法解析"
    else:
        # 初始态：尚待核实种子身份，先诚实标记为 insufficient_evidence。
        skeleton["status"] = "insufficient_evidence"
        skeleton["status_detail"] = "骨架已落盘；种子身份与扩展待增量补充"
    contract_keys = extract_contract_keys(case) if isinstance(case, dict) else []
    append_change(skeleton, "bootstrap", f"建立骨架，contract_keys={contract_keys}")
    save_state(work_dir, {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "started_at": now_iso(),
        "contract_keys": contract_keys,
        "case_id": skeleton.get("case_id"),
        "tool_version": VERSION,
    })
    path = write_result_safe(skeleton, output_dir, work_dir)
    print(f"bootstrap ok status={skeleton['status']} path={path}")
    return 0


def _load_json_arg(args):
    if getattr(args, "json_file", None):
        text, _info = read_text(args.json_file, max_bytes=8_000_000)
        if text is None:
            raise ValueError("cannot read --json-file")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"bad JSON in --json-file: {e}")
    raw = getattr(args, "json", None)
    if raw is None:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"bad JSON in --json: {e}")


def cmd_record(args):
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    result = load_result(output_dir)
    if result is None:
        result = build_skeleton(None, output_dir, files=[])
        result["status"] = "input_error"
        result["status_detail"] = "record 前 result.json 缺失，已重建最小骨架"
        result["errors"].append({"detail": "result.json missing at record"})
    try:
        data = _load_json_arg(args)
    except ValueError as e:
        print(f"record error: {e}", file=sys.stderr)
        return 2
    merge_records(result, args.kind, data, args.reason)
    path = write_result_safe(result, output_dir, work_dir)
    n = len(_as_list(data))
    print(f"record ok kind={args.kind} items={n} path={path}")
    return 0


def cmd_dedupe(args):
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    result = load_result(output_dir)
    if result is None:
        print("no result.json to dedupe", file=sys.stderr)
        return 2
    papers = result.get("results", {}).get("papers", [])
    deduped, evidence = dedupe(papers)
    result["results"]["papers"] = deduped
    result.setdefault("merge_log", [])
    result["merge_log"] = union_keep_order(result["merge_log"], evidence, key=lambda e: (e.get("kept"), e.get("merged")))
    append_change(result, "dedupe", f"身份合并：{len(papers)} -> {len(deduped)} 条")
    write_result_safe(result, output_dir, work_dir)
    print(f"dedupe ok {len(papers)} -> {len(deduped)}")
    return 0


def cmd_rank(args):
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    result = load_result(output_dir)
    if result is None:
        print("no result.json to rank", file=sys.stderr)
        return 2
    papers = result.get("results", {}).get("papers", [])
    edges = result.get("results", {}).get("edges", [])
    seed = result.get("seed_key") or (result.get("seed") or {}).get("id") if isinstance(result.get("seed"), dict) else result.get("seed_key")
    ranked = rank_papers(papers, edges, seed)
    result["results"]["papers"] = ranked
    append_change(result, "rank", f"重排 {len(ranked)} 条（显式 rank 优先，非高被引=高相关）")
    write_result_safe(result, output_dir, work_dir)
    print(f"rank ok {len(ranked)} papers")
    return 0


def cmd_paths(args):
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    result = load_result(output_dir)
    if result is None:
        print("no result.json for paths", file=sys.stderr)
        return 2
    edges = result.get("results", {}).get("edges", [])
    pid = getattr(args, "id", None) or f"path-{int(time.time())}"
    obj = build_path_object(edges, args.from_, args.to, pid)
    result.setdefault("results", {}).setdefault("paths", [])
    existing = [p for p in result["results"]["paths"] if p.get("from") == args.from_ and p.get("to") == args.to]
    if existing:
        result["results"]["paths"][result["results"]["paths"].index(existing[0])] = obj
    else:
        result["results"]["paths"].append(obj)
    append_change(result, "paths", f"路径 {args.from_} -> {args.to} 可达={obj['reachable']}")
    write_result_safe(result, output_dir, work_dir)
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_finalize(args):
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    summary = finalize(output_dir, work_dir)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary.get("ok") else 1


def cmd_selftest(args):
    import runpy
    selftest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest.py")
    if not os.path.isfile(selftest_path):
        print("selftest.py not found", file=sys.stderr)
        return 2
    rc = runpy.run_path(selftest_path, run_name="__main__")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(prog="core_impl.py", description="引文追踪 Skill 实现")
    sub = p.add_subparsers(dest="command", required=True)

    def _common(sp):
        # 允许 --input/--output/--work 出现在子命令前后。
        sp.add_argument("--input", default=None)
        sp.add_argument("--output", default=None)
        sp.add_argument("--work", default=None)
        return sp

    _common(sub.add_parser("bootstrap", help="Step 0：先落盘合法 partial 骨架"))

    r = _common(sub.add_parser("record", help="增量合并 papers/edges/sources/uncertainty 等"))
    r.add_argument("--kind", required=True,
                   choices=["papers", "edges", "sources", "uncertainty", "clusters", "paths", "pruned"])
    r.add_argument("--json", default=None, help="内联 JSON（数组或对象）")
    r.add_argument("--json-file", default=None, help="JSON 文件路径")
    r.add_argument("--reason", default=None)

    _common(sub.add_parser("dedupe", help="论文身份合并（DOI→arXiv→标题+一作+年份→标题）"))

    _common(sub.add_parser("rank", help="确定性重排序"))

    ps = _common(sub.add_parser("paths", help="最短有向引用路径"))
    ps.add_argument("--from", dest="from_", required=True)
    ps.add_argument("--to", required=True)
    ps.add_argument("--id", default=None)

    _common(sub.add_parser("finalize", help="生成 report.md / papers.csv / edges.csv / SVG 并校验"))
    _common(sub.add_parser("selftest", help="运行自检（空输入/坏 JSON/坏编码/完整管线/确定性）"))
    return p


def main(argv=None):
    global _PROCESS_START
    _PROCESS_START = time.time()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        code = e.code
        if code is None or code == 0:
            return 0
        return code if isinstance(code, int) else 2
    handlers = {
        "bootstrap": cmd_bootstrap,
        "record": cmd_record,
        "dedupe": cmd_dedupe,
        "rank": cmd_rank,
        "paths": cmd_paths,
        "finalize": cmd_finalize,
        "selftest": cmd_selftest,
    }
    try:
        return handlers[args.command](args)
    except SystemExit as e:
        code = e.code
        if code is None or code == 0:
            return 0
        return code if isinstance(code, int) else 2
    except Exception:
        # 任何异常都不让进程以 traceback 崩溃；已有 partial 产物时保留并记录错误。
        traceback.print_exc()
        try:
            output_dir = getattr(args, "output", None) or DEFAULT_OUTPUT
            work_dir = getattr(args, "work", None) or DEFAULT_WORK
            existing = load_result(output_dir)
            if existing is None:
                minimal = build_skeleton(None, output_dir, files=[])
                minimal["status"] = "input_error"
                minimal["status_detail"] = "internal error"
                minimal["errors"].append({"detail": traceback.format_exc(limit=3)})
                write_result_safe(minimal, output_dir, work_dir)
            else:
                existing.setdefault("errors", []).append({"detail": traceback.format_exc(limit=3)})
                append_change(existing, "error", "内部异常已记录，保留既有 partial 产物")
                write_result_safe(existing, output_dir, work_dir)
        except Exception:
            try:
                sys.stdout.write(json.dumps({"status": "input_error", "error": "fatal"}, ensure_ascii=False))
            except Exception:
                pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
