"""citation_lib.py - 引文追踪的纯函数库（仅标准库，无外部依赖）。

职责：论文身份键与合并、去重、排序、最短路径、合法性校验、SVG 渲染、
容错读写。所有函数无副作用（写文件除外，均通过显式路径），保证 finalize
对同一组中间表示（plan / paper_registry / edges / evidence_ledger）产出
确定性的结果，从而支撑“中间表示与可复核”的设计目标。
"""
import csv
import json
import os
import re
from collections import defaultdict, deque
from datetime import datetime, timezone

# ---------------------------------------------------------------- 容错 IO
_TEXT_ENCS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


def read_text(path, default=None):
    if not isinstance(path, str) or not os.path.exists(path):
        return default
    for enc in _TEXT_ENCS:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return f.read()
        except (UnicodeDecodeError, OSError, ValueError):
            continue
    return default


def load_json(path, default=None):
    txt = read_text(path)
    if txt is None:
        return default
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        return default


def write_json(path, obj):
    d = os.path.dirname(os.path.abspath(str(path)))
    os.makedirs(d, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, str(path))


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_text(path, text):
    d = os.path.dirname(os.path.abspath(str(path)))
    os.makedirs(d, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------- 身份键
def _as_text(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        return str(v)
    return str(v).strip()


def normalize_title(t):
    s = _as_text(t).lower()
    s = re.sub(r"[\W_]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def first_author_surname(p):
    """从 authors/author 字段取第一作者，近似取末尾词作为姓氏。"""
    p = p or {}
    raw = p.get("authors", p.get("author"))
    if isinstance(raw, list) and raw:
        raw = raw[0]
    raw = _as_text(raw)
    if not raw:
        return ""
    parts = re.split(r"[,;]|\band\b|\s+", raw, flags=re.IGNORECASE)
    parts = [x for x in parts if x]
    return normalize_title(parts[-1]) if parts else ""


def key_of(paper):
    """身份键优先级固定：DOI → arXiv → title+第一作者姓+年份 → title。

    返回 (key_type, key_value)；无法识别返回 ("none", None)。
    该优先级即去重规则，任何实现都必须与此一致。
    """
    p = paper or {}
    doi = p.get("doi") or p.get("DOI")
    if doi:
        return ("doi", _as_text(doi).lower().strip("."))
    arx = p.get("arxiv") or p.get("arxiv_id") or p.get("arXiv")
    if arx:
        v = _as_text(arx).lower()
        v = re.sub(r"v\d+$", "", v).strip()
        return ("arxiv", v)
    title = normalize_title(p.get("title"))
    first = first_author_surname(p)
    year = _as_text(p.get("year"))
    if title and first and year:
        return ("title_author_year", (title, first, year))
    if title:
        return ("title", title)
    return ("none", None)


_CONF = {"doi": 1.0, "arxiv": 0.9, "title_author_year": 0.8, "title": 0.6, "none": 0.4}


# ---------------------------------------------------------------- 去重合并
def pick_canonical(members):
    """从同一身份簇的多版本成员里选代表版本。

    优先级：有 DOI 者 > 期刊版 > 最早年份 > 首个成员。
    """
    if not members:
        return {}
    canon = members[0]
    for m in members:
        if key_of(m)[0] == "doi":
            canon = m
            break
    if key_of(canon)[0] != "doi":
        for m in members:
            if _as_text(m.get("type", "")).lower() in ("journal", "article"):
                canon = m
                break
    # 无 DOI 时优先年份最小者（最早版本），避免取增补版为代表
    if key_of(canon)[0] != "doi":
        best = min(members, key=lambda m: _int_year(m.get("year")))
        canon = best
    return canon


def _int_year(y):
    try:
        return int(y)
    except (TypeError, ValueError):
        return 9999


def _arxiv_norm(v):
    v = _as_text(v).lower()
    return re.sub(r"v\d+$", "", v).strip()


def _doi_norm(v):
    return _as_text(v).lower().strip(".")


def _union_find_members(members):
    """多遍 union-find：DOI / arXiv / title+作者+年份 / title 任一键命中即合并。

    返回 (groups: {root: [index...]}, merge_keys: {index: {key_type...}})。
    这解决“同一工作一版有 DOI、另一版只有 arXiv”的跨键对齐问题。
    """
    n = len(members)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    passes = []
    for kt, extract in (
        ("doi", lambda m: _doi_norm(m.get("doi") or m.get("DOI"))),
        ("arxiv", lambda m: _arxiv_norm(m.get("arxiv") or m.get("arxiv_id") or m.get("arXiv"))),
        ("title_author_year", lambda m: None),
        ("title", lambda m: None),
    ):
        groups = defaultdict(list)
        if kt == "title_author_year":
            for i, m in enumerate(members):
                t, f, y = normalize_title(m.get("title")), first_author_surname(m), _as_text(m.get("year"))
                if t and f and y:
                    groups[(t, f, y)].append(i)
        elif kt == "title":
            for i, m in enumerate(members):
                t = normalize_title(m.get("title"))
                if t:
                    groups[t].append(i)
        else:
            for i, m in enumerate(members):
                v = extract(m)
                if v:
                    groups[v].append(i)
        passes.append((kt, groups))

    merge_keys = {}
    for kt, groups in passes:
        for _k, idxs in groups.items():
            if len(idxs) < 2:
                continue
            for i in idxs:
                merge_keys.setdefault(i, set()).add(kt)
            for other in idxs[1:]:
                union(idxs[0], other)

    grouped = defaultdict(list)
    for i in range(n):
        grouped[find(i)].append(i)
    return grouped, merge_keys


def _merge_evidence(keys_used, n_members):
    if not keys_used:
        return "无合并键（独立簇）"
    labels = {
        "doi": "DOI", "arxiv": "arXiv id",
        "title_author_year": "标题+第一作者姓+年份", "title": "规范化标题",
    }
    used = "、".join(labels[k] for k in ("doi", "arxiv", "title_author_year", "title") if k in keys_used)
    return "依据 %s 合并 %d 个版本" % (used, n_members)


def dedupe_paper_registry(registry):
    """对 paper_registry 扁平化，多遍 union-find 产出去重后的身份簇。

    返回 {"clusters": [...], "singleton_count": n, "merged_pairs": n}。
    任一键级（DOI/arXiv/标题+作者+年份/标题）命中即归并，避免
    单遍 key_of 因“版本间键类型不同”漏合并。
    """
    reg = registry or {}
    clusters = reg.get("clusters") or []
    members = []
    for cl in clusters:
        members.extend(cl.get("members") or [])
    members = [m for m in members if isinstance(m, dict)]

    grouped, merge_keys = _union_find_members(members)

    out = []
    merged_pairs = 0
    for i, root in enumerate(sorted(grouped, key=lambda r: min(grouped[r])), 1):
        idxs = grouped[root]
        ms = [members[j] for j in idxs]
        keys_used = set()
        for j in idxs:
            keys_used |= merge_keys.get(j, set())
        strongest = next((k for k in ("doi", "arxiv", "title_author_year", "title")
                          if k in keys_used), None)
        if len(ms) > 1:
            merged_pairs += 1
        if len(ms) == 1:
            confidence = 1.0
        elif strongest in ("doi", "arxiv"):
            confidence = 0.98
        elif strongest == "title_author_year":
            confidence = 0.9
        else:
            confidence = 0.7
        out.append({
            "canonical_id": "P%d" % i,
            "members": ms,
            "merge_key_used": strongest,
            "merge_evidence": _merge_evidence(keys_used, len(ms)),
            "confidence": round(confidence, 3),
        })
    return {
        "clusters": out,
        "singleton_count": len(out) - merged_pairs,
        "merged_pairs": merged_pairs,
    }


# ---------------------------------------------------------------- 图算法
def _adjacency(edges):
    adj = defaultdict(list)     # source -> [(target, edge_id)]
    rev = defaultdict(list)     # target -> [source]
    for e in edges:
        s, t, eid = e.get("source"), e.get("target"), e.get("id")
        if s and t:
            adj[s].append((t, eid))
            rev[t].append(s)
    return adj, rev


def distances(edges, seed_id):
    adj, _ = _adjacency(edges)
    dist = {seed_id: 0}
    q = deque([seed_id])
    while q:
        u = q.popleft()
        for v, _ in adj.get(u, []):
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


def rank_papers(papers, edges, seed_id):
    """排序：距离层数主导，被引次数仅作弱信号（高被引 != 高相关）。

    返回 [(score, paper_id)]，按分数降序。
    """
    adj, rev = _adjacency(edges)
    dist = distances(edges, seed_id)
    cited = {pid: len(rev.get(pid, [])) for pid in {p.get("id") for p in papers}}
    scored = []
    for p in papers:
        pid = p.get("id")
        d = dist.get(pid, 99)
        if pid == seed_id:
            base = 100.0
        elif d == 1:
            base = 60.0
        elif d == 2:
            base = 30.0
        elif d == 3:
            base = 12.0
        else:
            base = 4.0
        base += min(cited.get(pid, 0), 20) * 0.5
        yr = _int_year(p.get("year"))
        if 2015 <= yr <= 2026:
            base += min(yr - 2014, 10) * 0.2
        kt = key_of(p)[0]
        base *= _CONF.get(kt, 0.6)
        scored.append((round(base, 3), pid))
    scored.sort(key=lambda x: (-x[0], str(x[1])))
    return scored


def shortest_path(edges, src, dst):
    """在“source cites target”的有向边上求 src 到 dst 的最短引用链。

    返回 (nodes, edge_ids)；不可达返回 (None, None)。
    """
    adj, _ = _adjacency(edges)
    if src == dst:
        return [src], []
    prev = {src: (None, None)}
    q = deque([src])
    while q:
        u = q.popleft()
        if u == dst:
            break
        for v, eid in adj.get(u, []):
            if v not in prev:
                prev[v] = (u, eid)
                q.append(v)
    if dst not in prev:
        return None, None
    nodes, edge_ids = [dst], []
    cur = dst
    while prev[cur][0] is not None:
        par, eid = prev[cur]
        nodes.append(par)
        edge_ids.append(eid)
        cur = par
    nodes.reverse()
    edge_ids.reverse()
    return nodes, edge_ids


# ---------------------------------------------------------------- 校验
def validate_result(r):
    issues = []
    if not isinstance(r, dict):
        return ["result 不是 dict"]
    if not r.get("case_id"):
        issues.append("缺少 case_id")
    if r.get("status") not in ("ok", "partial", "insufficient_evidence", "input_error"):
        issues.append("status 非法: %r" % r.get("status"))
    for k in ("results", "sources", "uncertainty", "change_log"):
        if k not in r:
            issues.append("缺少字段: " + k)
    res = r.get("results")
    if isinstance(res, dict):
        for k in ("papers", "edges", "clusters", "paths"):
            if k not in res:
                issues.append("results 缺少字段: " + k)
    return issues


def evidence_coverage(edges, ledger):
    """给定边集与证据台账，返回 (total, covered)。covered=有逐字 quote 的边。"""
    entries = ledger.get("entries") or [] if isinstance(ledger, dict) else []
    quoted = set()
    for e in entries:
        if isinstance(e, dict) and e.get("target") and e.get("quote"):
            quoted.add(e.get("target_id") or e.get("target"))
    total = len(edges)
    covered = 0
    for e in edges:
        eid = e.get("id")
        if eid in quoted:
            covered += 1
        elif e.get("evidence") and e["evidence"].get("quote"):
            covered += 1
    return total, covered


# ---------------------------------------------------------------- SVG
def render_graph_svg(papers, edges, seed_id, out_path):
    """确定性分层布局的引用图 SVG（source cites target，箭头指向被引方）。"""
    ids = [p.get("id") for p in papers if p.get("id")]
    dist = distances(edges, seed_id)
    layers = defaultdict(list)
    for pid in ids:
        layers[min(dist.get(pid, 5), 5)].append(pid)
    pos = {}
    ncols = max(len(layers[k]) for k in layers) if layers else 1
    cell_w, cell_h = 190, 96
    width = max(len(layers), 6) * cell_w + 40
    height = ncols * cell_h + 80
    for layer in sorted(layers):
        nodes = sorted(layers[layer], key=lambda x: str(x))
        for i, pid in enumerate(nodes):
            pos[pid] = (40 + layer * cell_w, 60 + i * cell_h)

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
        "<style>text{font-family:sans-serif;font-size:12px} .edge{stroke:#999;stroke-width:1.2} .node{fill:#eef3fb;stroke:#2f6fb0;stroke-width:1.6} .seed{fill:#fde9d9;stroke:#c55a11}</style>",
        ('<defs><marker id="arr" markerWidth="9" markerHeight="9" refX="8" refY="3" '
         'orient="auto"><path d="M0,0 L9,3 L0,6 z" fill="#666"/></marker></defs>'),
    ]
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in pos and t in pos:
            x1, y1 = pos[s]
            x2, y2 = pos[t]
            lines.append(
                '<line class="edge" x1="%d" y1="%d" x2="%d" y2="%d" marker-end="url(#arr)"/>'
                % (x1, y1, x2, y2))
    for pid, (x, y) in pos.items():
        cls = "seed" if pid == seed_id else "node"
        lines.append('<circle class="%s" cx="%d" cy="%d" r="26"/>' % (cls, x, y))
        lines.append('<text x="%d" y="%d" text-anchor="middle" dominant-baseline="middle">%s</text>' % (x, y, pid))
    lines.append("</svg>")
    write_text(out_path, "\n".join(lines))


# ---------------------------------------------------------------- CSV
def write_papers_csv(papers, ranked, dist, out_path):
    rank_map = {pid: i + 1 for i, (_, pid) in enumerate(ranked)}
    with open(str(out_path), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "rank", "distance", "title", "year", "venue", "type",
                    "doi", "arxiv", "authors", "merge_key_used", "source", "locator"])
        for p in papers:
            pid = p.get("id")
            w.writerow([
                pid, rank_map.get(pid, 0), dist.get(pid, 99), p.get("title"),
                p.get("year"), p.get("venue"), p.get("type"),
                p.get("doi"), p.get("arxiv"),
                "; ".join(p.get("authors") or []) if isinstance(p.get("authors"), list) else p.get("authors"),
                p.get("merge_key_used"),
                p.get("source"), p.get("locator"),
            ])


def write_edges_csv(edges, out_path):
    with open(str(out_path), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "source", "target", "relation", "direction",
                    "source_label", "target_label", "evidence_locator", "evidence_quote"])
        for e in edges:
            ev = e.get("evidence") or {}
            w.writerow([
                e.get("id"), e.get("source"), e.get("target"), e.get("relation"),
                e.get("direction"), e.get("source_label"), e.get("target_label"),
                ev.get("locator"), ev.get("quote"),
            ])