# -*- coding: utf-8 -*-
"""core_impl.py — 引文追踪 Skill 的确定性实现与可复用工具库。

只依赖 Python 标准库。既作为 CLI 后端（经 main.py 调用），也可 import 复用：
  from core_impl import dedupe, rank_papers, shortest_path, normalize_doi

子命令：bootstrap / finalize / validate / tool(dedupe|rank|path|quote-hash)
"""
import csv
import datetime
import hashlib
import json
import math
import os
import re
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
ENCODINGS = ("utf-8-sig", "utf-8", "latin-1")


# ---------------------------------------------------------------------------
# 时间与通用 IO
# ---------------------------------------------------------------------------

def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path, obj):
    """原子写入 JSON（先写临时文件再替换）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 输入发现与容错读取
# ---------------------------------------------------------------------------

def read_text_file(path, max_bytes=MAX_FILE_BYTES):
    """容错读取文本文件。返回 (text_or_None, encoding_or_status)。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "unreadable"
    if size > max_bytes:
        return None, "too_large"
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return f.read(), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None, "oserror"
    return None, "decode_failed"


def safe_json_load(text):
    if text is None:
        return None, "no_text"
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as e:
        return None, str(e)


def discover_inputs(input_dir):
    files = {}
    degradations = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing"]
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as e:
        return files, ["listdir_error:" + str(e)]
    for name in names:
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        ext = os.path.splitext(name)[1].lower()
        text, enc = read_text_file(p)
        files[name] = {"path": p, "ext": ext, "size": size, "encoding": enc, "text": text}
        if enc in ("unreadable", "too_large", "oserror", "decode_failed"):
            degradations.append(name + ":" + enc)
    return files, degradations


# ---------------------------------------------------------------------------
# output_contract 回填
# ---------------------------------------------------------------------------

def echo_contract(contract):
    """原样回填键：dict 保留键、值填 null/[]/{}；list 把字符串项当键。"""
    if isinstance(contract, dict):
        out = {}
        for k, v in contract.items():
            if isinstance(v, list):
                out[k] = []
            elif isinstance(v, dict):
                out[k] = {}
            else:
                out[k] = None
        return out
    if isinstance(contract, list):
        out = {}
        for item in contract:
            if isinstance(item, str):
                out[item] = None
        return out
    return {}


# ---------------------------------------------------------------------------
# bootstrap：落盘 partial 骨架 + state
# ---------------------------------------------------------------------------

def minimal_result(case_id="unknown", status="input_error", now=None):
    now = now or utcnow_iso()
    return {
        "case_id": case_id,
        "status": status,
        "output_contract": {},
        "seed": None,
        "seed_key": None,
        "research_question": None,
        "results": {"papers": [], "edges": [], "clusters": [], "paths": [], "pruned": []},
        "evidence_map": {"version": "1.1", "items": []},
        "sources": [],
        "uncertainty": {},
        "change_log": [{
            "at": now, "elapsed_s": 0, "action": "minimal",
            "detail": "最小合法产物", "reason": "异常降级"
        }],
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": now, "budget_s": 900, "budget_requests": 64,
                    "elapsed_s": 0, "requests_used": 0},
    }


def build_skeleton(input_dir, output_dir, work_dir, now=None):
    now = now or utcnow_iso()
    files, degradations = discover_inputs(input_dir)

    case_data = None
    case_error = None
    case_json = files.get("case.json")
    if case_json is not None:
        if case_json.get("text") is not None:
            case_data, case_error = safe_json_load(case_json["text"])
            if case_error:
                case_data = None
        else:
            case_error = case_json.get("encoding", "unreadable")

    status = "partial"
    errors = []
    case_id = "unknown"
    contract = {}
    seed_raw = None
    research_question = None
    task_text = (files.get("task.md") or {}).get("text")

    if not files:
        status = "insufficient_evidence"
        errors.append("输入目录为空或不存在")
    elif case_error:
        status = "input_error"
        errors.append("case.json 无法解析: " + str(case_error))
    elif case_data is None and "case.json" in files:
        status = "input_error"
        errors.append("case.json 存在但内容为空或不可读")
    elif case_data is not None:
        case_id = str(case_data.get("case_id") or case_data.get("id") or "unknown")
        contract = echo_contract(case_data.get("output_contract"))
        seed_raw = (case_data.get("seed") or case_data.get("seed_paper")
                    or case_data.get("seed_papers"))
        research_question = case_data.get("research_question") or case_data.get("question")

    result = {
        "case_id": case_id,
        "status": status,
        "output_contract": contract,
        "seed": seed_raw,
        "seed_key": None,
        "research_question": research_question,
        "results": {"papers": [], "edges": [], "clusters": [], "paths": [], "pruned": []},
        "evidence_map": {"version": "1.1", "items": []},
        "sources": [],
        "uncertainty": {},
        "change_log": [{
            "at": now, "elapsed_s": 0, "action": "bootstrap",
            "detail": "落盘 partial 结果骨架", "reason": "先写合法骨架，后续增量改进"
        }],
        "limitations": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "runtime": {"started_at": now, "budget_s": 900, "budget_requests": 64,
                    "elapsed_s": 0, "requests_used": 0},
    }

    state = {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "work_dir": work_dir,
        "files": {k: {kk: vv for kk, vv in v.items() if kk != "text"}
                  for k, v in files.items()},
        "case_data": case_data,
        "task_text": task_text,
        "case_error": case_error,
        "degradations": degradations,
        "started_at": now,
    }

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    write_json(os.path.join(output_dir, "result.json"), result)
    write_json(os.path.join(work_dir, "state.json"), state)
    return result, state


# ---------------------------------------------------------------------------
# 身份规范化与去重
# ---------------------------------------------------------------------------

def normalize_doi(s):
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r'^https?://(dx\.)?doi\.org/', '', s, flags=re.I)
    s = re.sub(r'^doi:\s*', '', s, flags=re.I)
    s = s.strip()
    return s.lower() if s else None


def normalize_arxiv(s):
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r'^https?://arxiv\.org/(abs|pdf)/', '', s, flags=re.I)
    s = re.sub(r'^arxiv:\s*', '', s, flags=re.I)
    m = re.search(r'(\d{4}\.\d{4,5})', s)
    if m:
        return m.group(1)
    m = re.search(r'([a-z\-]+(?:\.[A-Z]{2})?/\d{7})', s, flags=re.I)
    if m:
        return m.group(1)
    return s.lower().strip() if s else None


def title_key(s):
    if not s:
        return None
    s = str(s).strip().lower()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'[^\w\s]', '', s)
    return s.strip()


def first_author_surname(authors):
    if isinstance(authors, list) and authors:
        a = authors[0]
    elif isinstance(authors, str) and authors:
        a = authors.split(';')[0].split(',')[0]
    else:
        a = ''
    a = str(a).strip()
    if ',' in a:
        surname = a.split(',')[0]
    else:
        parts = a.split()
        surname = parts[-1] if parts else a
    return re.sub(r'[^a-z0-9]+', '', surname.lower())


def identity_key(paper, mode):
    if mode == "doi":
        return normalize_doi(paper.get("doi"))
    if mode == "arxiv":
        return normalize_arxiv(paper.get("arxiv"))
    if mode == "title_author_year":
        t = title_key(paper.get("title"))
        if not t:
            return None
        surname = first_author_surname(paper.get("authors"))
        year = paper.get("year")
        return json.dumps([t, surname, str(year) if year else ""], ensure_ascii=False)
    if mode == "title":
        return title_key(paper.get("title"))
    return None


def _choose_canonical(ids, by_id):
    def score(pid):
        p = by_id[pid]
        s = 0
        if p.get("doi"):
            s += 4
        if p.get("arxiv"):
            s += 2
        t = str(p.get("type") or "").lower()
        if t in ("article", "journal"):
            s += 3
        elif t == "conference":
            s += 2
        elif t == "preprint":
            s += 1
        if p.get("venue"):
            s += 1
        return s
    return max(ids, key=lambda pid: (score(pid), -len(str(pid)), str(pid)))


def dedupe(papers):
    papers = [p for p in (papers or []) if isinstance(p, dict) and p.get("id")]
    if not papers:
        return {"papers": [], "merge_groups": [], "merge_evidence": []}
    by_id = {p["id"]: p for p in papers}
    parent = {p["id"]: p["id"] for p in papers}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    merge_evidence = []

    def union(a, b, key_type, key_value):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
            merge_evidence.append({"key_type": key_type, "key_value": key_value,
                                   "member_a": a, "member_b": b})

    for key_type in ("doi", "arxiv", "title_author_year", "title"):
        buckets = {}
        for p in papers:
            k = identity_key(p, key_type)
            if k:
                buckets.setdefault(k, []).append(p["id"])
        for k, ids in buckets.items():
            if len(ids) > 1:
                for j in range(1, len(ids)):
                    union(ids[0], ids[j], key_type, k)

    groups = {}
    for p in papers:
        groups.setdefault(find(p["id"]), []).append(p["id"])

    merge_groups = []
    merged = []
    for root, ids in groups.items():
        ids = sorted(ids)
        canonical = _choose_canonical(ids, by_id)
        merge_groups.append({"canonical_id": canonical, "members": ids, "size": len(ids)})
        merged.append(by_id[canonical])

    merged.sort(key=lambda p: str(p.get("id")))
    merge_groups.sort(key=lambda g: str(g["canonical_id"]))
    merge_evidence.sort(key=lambda e: (e["key_type"], str(e["key_value"])))
    return {"papers": merged, "merge_groups": merge_groups, "merge_evidence": merge_evidence}


# ---------------------------------------------------------------------------
# 排序与最短路径
# ---------------------------------------------------------------------------

def rank_papers(papers, edges, seed_id, weights=None):
    papers = [p for p in (papers or []) if isinstance(p, dict) and p.get("id")]
    edges = [e for e in (edges or []) if isinstance(e, dict)]
    weights = weights or {"distance": -2.0, "cited_by": 1.5, "year": 0.5, "confidence": 1.0}

    adj = {p["id"]: [] for p in papers}
    indeg = {p["id"]: 0 for p in papers}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in adj and t in adj:
            adj[s].append(t)
            adj[t].append(s)
            if e.get("relation", "cites") == "cites":
                indeg[t] = indeg.get(t, 0) + 1

    dist = {}
    if seed_id in adj:
        dist[seed_id] = 0
        frontier = [seed_id]
        seen = {seed_id}
        while frontier:
            nxt = []
            for n in frontier:
                for m in adj[n]:
                    if m not in seen:
                        seen.add(m)
                        dist[m] = dist[n] + 1
                        nxt.append(m)
            frontier = nxt

    years = [int(p.get("year") or 0) for p in papers]
    max_year = max(years, default=0)
    min_year = min(years, default=0)
    yr = max_year - min_year if max_year > min_year else 1

    rows = []
    for p in papers:
        d = dist.get(p["id"], 999)
        cited = indeg.get(p["id"], 0)
        year = int(p.get("year") or 0)
        year_norm = (year - min_year) / yr if yr else 0.5
        conf = 0.8 if p.get("source_ids") else 0.5
        score = (weights["distance"] * d + weights["cited_by"] * min(cited, 10)
                 + weights["year"] * year_norm * 10 + weights["confidence"] * conf * 5)
        rows.append({"id": p["id"], "distance": d, "cited_by": cited, "year": year,
                     "source_confidence": round(conf, 2), "score": round(score, 4)})

    rows.sort(key=lambda r: (-r["score"], r["distance"], -r["cited_by"], str(r["id"])))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def shortest_path(edges, start, end):
    adj = {}
    for e in (edges or []):
        if not isinstance(e, dict):
            continue
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        if e.get("relation", "cites") == "cites":
            adj.setdefault(s, []).append(t)
        else:
            adj.setdefault(s, []).append(t)
            adj.setdefault(t, []).append(s)

    if not start or not end:
        return {"unreachable": True, "reason": "缺少 start/end"}
    prev = {start: None}
    frontier = [start]
    while frontier:
        nxt = []
        for n in frontier:
            for m in adj.get(n, []):
                if m not in prev:
                    prev[m] = n
                    nxt.append(m)
        frontier = nxt
    if end not in prev:
        return {"unreachable": True, "start": start, "end": end}

    path = []
    cur = end
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    hops = [{"from": path[i], "to": path[i + 1], "direction": "cites"}
            for i in range(len(path) - 1)]
    return {"reachable": True, "hops": hops, "length": len(hops)}


# ---------------------------------------------------------------------------
# finalize：校验 + 生成可复核产物
# ---------------------------------------------------------------------------

def validate_result(result):
    issues = []
    if not isinstance(result, dict):
        return ["result 不是对象"]
    if not result.get("case_id"):
        issues.append("缺少 case_id")
    if result.get("status") not in STATUSES:
        issues.append("status 非法")
    if "output_contract" not in result:
        issues.append("缺少 output_contract")
    if not isinstance(result.get("results"), dict):
        issues.append("results 不是对象")
    return issues


def _escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _escape_md(s):
    return str(s).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _join_ids(v):
    if isinstance(v, list):
        return ";".join(str(x) for x in v)
    return str(v or "")


def _flatten_authors(p):
    a = p.get("authors")
    if isinstance(a, list):
        return ";".join(str(x) for x in a)
    return str(a or "")


def write_report(output_dir, result):
    papers = result.get("results", {}).get("papers", [])
    edges = result.get("results", {}).get("edges", [])
    ev = result.get("evidence_map", {}).get("items", [])
    lines = []
    lines.append("# 引文追踪报告 — " + str(result.get("case_id")))
    lines.append("")
    lines.append("- 状态：`" + str(result.get("status")) + "`")
    lines.append("- 种子：`" + str(result.get("seed_key") or "未确定") + "`")
    lines.append("- 研究问题：" + str(result.get("research_question") or "（未提供）"))
    lines.append("")
    lines.append("## 文献清单（" + str(len(papers)) + " 篇）")
    lines.append("")
    if papers:
        lines.append("| id | 标题 | 年份 | 类型 | 角色 | DOI/arXiv |")
        lines.append("|---|---|---|---|---|---|")
        for p in papers:
            ident = p.get("doi") or p.get("arxiv") or ""
            lines.append("| " + str(p.get("id", "")) + " | " + _escape_md(p.get("title", ""))
                         + " | " + str(p.get("year", "")) + " | " + str(p.get("type", ""))
                         + " | " + str(p.get("role", "")) + " | " + str(ident) + " |")
    else:
        lines.append("（尚无文献节点）")
    lines.append("")
    lines.append("## 引用/关联边（" + str(len(edges)) + " 条）")
    lines.append("")
    if edges:
        lines.append("| source | target | relation | direction | basis | locator |")
        lines.append("|---|---|---|---|---|---|")
        for e in edges:
            lines.append("| " + str(e.get("source", "")) + " | " + str(e.get("target", ""))
                         + " | " + str(e.get("relation", "")) + " | " + str(e.get("direction", ""))
                         + " | " + str(e.get("basis", "")) + " | " + str(e.get("locator", "")) + " |")
    else:
        lines.append("（尚无边）")
    lines.append("")
    lines.append("## 证据地图（" + str(len(ev)) + " 条）")
    lines.append("")
    if ev:
        lines.append("| evidence_id | claim | locator | 逐字 quote | 置信度 |")
        lines.append("|---|---|---|---|---|")
        for it in ev:
            q = str(it.get("quote") or "").replace("\n", " ")[:80]
            lines.append("| " + str(it.get("evidence_id", "")) + " | "
                         + _escape_md(it.get("claim", "")) + " | " + str(it.get("locator", ""))
                         + " | " + _escape_md(q) + " | " + str(it.get("confidence", "")) + " |")
    else:
        lines.append("（尚无证据）")
    lines.append("")

    unc = result.get("uncertainty") or {}
    if unc:
        lines.append("## 不确定性")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(unc, ensure_ascii=False, indent=2))
        lines.append("```")
    for section, key in (("待人工确认", "pending_human"), ("限制与遗漏", "limitations")):
        items = result.get(key) or []
        if items:
            lines.append("")
            lines.append("## " + section)
            for x in items:
                lines.append("- " + str(x))
    lines.append("")
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_papers_csv(output_dir, result):
    papers = result.get("results", {}).get("papers", [])
    cols = ["id", "title", "authors", "year", "doi", "arxiv", "venue", "type",
            "role", "identity_key", "identity_key_type", "source_ids", "evidence_ids"]
    with open(os.path.join(output_dir, "papers.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for p in papers:
            w.writerow([
                p.get("id", ""), p.get("title", ""), _flatten_authors(p), p.get("year", ""),
                p.get("doi", ""), p.get("arxiv", ""), p.get("venue", ""), p.get("type", ""),
                p.get("role", ""), p.get("identity_key", ""), p.get("identity_key_type", ""),
                _join_ids(p.get("source_ids")), _join_ids(p.get("evidence_ids")),
            ])


def write_edges_csv(output_dir, result):
    edges = result.get("results", {}).get("edges", [])
    cols = ["source", "target", "relation", "direction", "basis",
            "source_label", "target_label", "evidence_ids", "locator"]
    with open(os.path.join(output_dir, "edges.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for e in edges:
            w.writerow([
                e.get("source", ""), e.get("target", ""), e.get("relation", ""),
                e.get("direction", ""), e.get("basis", ""), e.get("source_label", ""),
                e.get("target_label", ""), _join_ids(e.get("evidence_ids")), e.get("locator", ""),
            ])


def write_svg(output_dir, result):
    papers = {p.get("id"): p for p in result.get("results", {}).get("papers", [])
              if isinstance(p, dict)}
    edges = [e for e in result.get("results", {}).get("edges", []) if isinstance(e, dict)]
    path = os.path.join(output_dir, "citation_graph.svg")
    width, height = 1200, 900
    lines = []
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d">' % (width, height, width, height))
    lines.append('<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="8" refY="3" '
                 'orient="auto"><path d="M0,0 L8,3 L0,6 z" fill="#555"/></marker></defs>')
    lines.append('<rect width="100%" height="100%" fill="#fafafa"/>')
    lines.append('<text x="24" y="28" font-family="sans-serif" font-size="14" fill="#333">'
                 '引文网络（实线=引用 source→target，虚线=关联）</text>')

    if not papers:
        lines.append('<text x="24" y="60" font-family="sans-serif" font-size="13" fill="#888">'
                     '（无文献节点）</text>')
        lines.append('</svg>')
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return

    seed = result.get("seed_key")
    if not seed:
        sd = result.get("seed")
        if isinstance(sd, dict):
            seed = sd.get("id")
    if not seed or seed not in papers:
        seed = sorted(papers.keys())[0]

    adj = {pid: [] for pid in papers}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in adj and t in adj:
            adj[s].append(t)
            adj[t].append(s)

    depth = {seed: 0}
    frontier = [seed]
    seen = {seed}
    while frontier:
        nxt = []
        for n in frontier:
            for m in adj.get(n, []):
                if m not in seen:
                    seen.add(m)
                    depth[m] = depth[n] + 1
                    nxt.append(m)
        frontier = nxt
    for pid in papers:
        depth.setdefault(pid, 999)

    layers = {}
    for pid, d in depth.items():
        layers.setdefault(d, []).append(pid)

    cx, cy = width / 2.0, height / 2.0
    base_r, dr = 110.0, 150.0
    pos = {}
    for d in sorted(layers.keys()):
        ids = sorted(layers[d])
        if d == 0:
            pos[ids[0]] = (cx, cy)
            continue
        n = len(ids)
        r = base_r + d * dr
        for i, pid in enumerate(ids):
            ang = 2 * math.pi * i / n - math.pi / 2
            pos[pid] = (cx + r * math.cos(ang), cy + r * math.sin(ang))

    role_color = {"seed": "#d62728", "foundational": "#1f77b4", "followup": "#2ca02c",
                  "parallel": "#ff7f0e", "source": "#9467bd", "bridge": "#e377c2",
                  "hotspot": "#bcbd22"}

    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s not in pos or t not in pos:
            continue
        x1, y1 = pos[s]
        x2, y2 = pos[t]
        if e.get("relation") == "associated":
            lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#999" '
                         'stroke-dasharray="5,4" stroke-width="1"/>' % (x1, y1, x2, y2))
        else:
            lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#777" '
                         'stroke-width="1.2" marker-end="url(#arr)"/>' % (x1, y1, x2, y2))

    for pid, (x, y) in pos.items():
        p = papers[pid]
        color = role_color.get(p.get("role", ""), "#7f7f7f")
        if pid == seed:
            color = "#d62728"
        label = str(p.get("title") or pid)[:28]
        lines.append('<circle cx="%.1f" cy="%.1f" r="14" fill="%s" stroke="#333" '
                     'stroke-width="1"/>' % (x, y, color))
        lines.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-family="sans-serif" '
                     'font-size="10" fill="#111">%s</text>' % (x, y - 18, _escape(pid)))
        lines.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-family="sans-serif" '
                     'font-size="9" fill="#333">%s</text>' % (x, y + 28, _escape(label)))
    lines.append('</svg>')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def finalize(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    rp = os.path.join(output_dir, "result.json")
    text, enc = read_text_file(rp)
    result, err = safe_json_load(text)
    if result is None:
        result = minimal_result("unknown", "input_error")
        result["errors"].append("finalize:result.json 缺失或不可读: " + str(err or enc))
        write_json(rp, result)
    for msg in validate_result(result):
        result.setdefault("errors", []).append("validate:" + msg)
    if result.get("status") not in STATUSES:
        result["status"] = "partial"
    write_report(output_dir, result)
    write_papers_csv(output_dir, result)
    write_edges_csv(output_dir, result)
    write_svg(output_dir, result)
    write_json(rp, result)
    return result


# ---------------------------------------------------------------------------
# tool 子命令
# ---------------------------------------------------------------------------

def load_json_file(path):
    if not path:
        return None
    text, enc = read_text_file(path)
    if text is None:
        raise ValueError("无法读取 %s: %s" % (path, enc))
    obj, err = safe_json_load(text)
    if err:
        raise ValueError("JSON 解析失败 %s: %s" % (path, err))
    return obj


def tool_cli(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: main.py tool {dedupe|rank|path|quote-hash} [options]")
        return 0
    name = argv[0]
    args = parse_args(argv[1:])
    try:
        if name == "dedupe":
            papers = load_json_file(args.get("papers"))
            out = dedupe(papers)
        elif name == "rank":
            papers = load_json_file(args.get("papers"))
            edges = load_json_file(args.get("edges")) or []
            out = rank_papers(papers, edges, args.get("seed"))
        elif name == "path":
            edges = load_json_file(args.get("edges")) or []
            out = shortest_path(edges, args.get("start"), args.get("end"))
        elif name == "quote-hash":
            out = {"quote_sha256": quote_hash(args.get("text") or "")}
        else:
            print(json.dumps({"error": "unknown tool"}, ensure_ascii=False))
            return 2
    except Exception as e:
        out = {"error": str(e)}

    s = json.dumps(out, ensure_ascii=False, indent=2)
    outpath = args.get("out")
    if outpath:
        with open(outpath, "w", encoding="utf-8") as f:
            f.write(s)
    else:
        print(s)
    return 0


def quote_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# CLI 入口与参数解析
# ---------------------------------------------------------------------------

def parse_args(argv):
    args = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if "=" in key:
                key, val = key.split("=", 1)
                args[key] = val
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                args[key] = argv[i + 1]
                i += 1
            else:
                args[key] = True
        elif a.startswith("-") and len(a) > 1:
            args[a[1:]] = True
        i += 1
    return args


def bootstrap_cli(args):
    try:
        build_skeleton(args.get("input", DEFAULT_INPUT),
                       args.get("output", DEFAULT_OUTPUT),
                       args.get("work", DEFAULT_WORK))
    except Exception as e:
        try:
            r = minimal_result("unknown", "input_error")
            r["errors"].append("bootstrap_exception:" + str(e))
            os.makedirs(args.get("output", DEFAULT_OUTPUT), exist_ok=True)
            write_json(os.path.join(args.get("output", DEFAULT_OUTPUT), "result.json"), r)
        except Exception:
            pass
    return 0


def finalize_cli(args):
    try:
        finalize(args.get("output", DEFAULT_OUTPUT))
    except Exception as e:
        try:
            r = minimal_result("unknown", "input_error")
            r["errors"].append("finalize_exception:" + str(e))
            os.makedirs(args.get("output", DEFAULT_OUTPUT), exist_ok=True)
            write_json(os.path.join(args.get("output", DEFAULT_OUTPUT), "result.json"), r)
        except Exception:
            pass
    return 0


def validate_cli(args):
    output_dir = args.get("output", DEFAULT_OUTPUT)
    rp = os.path.join(output_dir, "result.json")
    text, enc = read_text_file(rp)
    result, err = safe_json_load(text)
    if result is None:
        print("FAIL: result.json 不可读或非法: " + str(err or enc))
        return 1
    issues = validate_result(result)
    if issues:
        print("FAIL: " + "; ".join(issues))
        return 1
    print("ok " + str(result.get("status")))
    return 0


def print_help():
    print("""citation-tracing 脚本工具（仅 Python 标准库）

用法:
  python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
      扫描 /input，回填 output_contract，落盘 partial result.json 骨架与 work/state.json
  python3 scripts/main.py tool dedupe --papers <json> [--out <json>]
      按 DOI→arXiv→标题+作者姓+年份→标题 优先级做身份合并
  python3 scripts/main.py tool rank --papers <json> --edges <json> --seed <id> [--out <json>]
      距离/被引/年份/来源置信度加权排序（非“高被引=高相关”）
  python3 scripts/main.py tool path --edges <json> --start <id> --end <id>
      沿 citation 方向 BFS 求最短引用链
  python3 scripts/main.py tool quote-hash --text "逐字原文"
      计算 quote 的 sha256，供 evidence_map 校验
  python3 scripts/main.py finalize --output /app/output
      校验 result.json 并生成 report.md/papers.csv/edges.csv/citation_graph.svg
  python3 scripts/main.py validate --output /app/output
      仅校验 result.json
  python3 scripts/selftest.py
      空目录/坏 JSON/缺输入/坏编码自检，全部退出码 0
""")


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print_help()
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "bootstrap":
        return bootstrap_cli(parse_args(rest))
    if cmd == "finalize":
        return finalize_cli(parse_args(rest))
    if cmd == "validate":
        return validate_cli(parse_args(rest))
    if cmd == "tool":
        return tool_cli(rest)
    print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
