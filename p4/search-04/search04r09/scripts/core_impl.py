"""core_impl.py -- 引文追踪 Skill 的确定性脚本内核（仅标准库）。

职责边界：脚本只做可复核的确定性工程（输入发现、骨架落盘、身份去重、
结构排序、最短引用路径、CSV/SVG/报告产物、校验、自检）。文献是否真实、
引用方向是否正确、关联是否确有依据等科学判断由 Agent 依照 SKILL.md 完成，
脚本不补造任何文献或数值。

子命令（经 scripts/main.py 转发）：
  bootstrap  --input DIR --output DIR --work DIR   创建 result.json 骨架与 state.json
  finalize   --output DIR                         校验 result.json 并重生成 CSV/SVG/报告
  dedupe     --papers FILE --out FILE             论文身份合并
  rank       --papers FILE --edges FILE --seed ID --out FILE  结构排序
  path       --edges FILE --source ID --target ID --out FILE  最短引用路径
  selftest                                          合成输入自检
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
from collections import defaultdict, deque

VERSION = "1.1.0"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件读取上限

NOW_TZ = None
try:
    from datetime import datetime, timezone

    def utcnow() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
except Exception:  # pragma: no cover - 极老运行时降级
    def utcnow() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# 通用 IO（容错）
# ---------------------------------------------------------------------------

def read_text(path: str):
    """utf-8-sig 优先，失败降级到 errors=replace；返回 (text, error)。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return "", str(exc)
    if len(raw) > MAX_FILE_BYTES:
        return "", "skipped:file_too_large(>2MB)"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "bad_encoding:fallback_replace"


def read_json(path: str):
    """返回 (obj, error)。坏 JSON 不抛异常。"""
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, "bad_json:%s" % str(exc).splitlines()[0] if str(exc) else "bad_json"


def write_json_atomic(path: str, obj) -> str:
    """先写临时文件再替换，任何异常都不留半成品；返回错误字符串或空串。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return ""
    except OSError as exc:
        return str(exc)


# ---------------------------------------------------------------------------
# 输入发现
# ---------------------------------------------------------------------------

def scan_input(input_dir: str):
    """列出输入文件，超限跳过并记 degradation。"""
    files, degraded = [], []
    base = os.path.abspath(input_dir)
    if not os.path.isdir(base):
        return files, degraded, "input_dir_missing"
    for name in sorted(os.listdir(base)):
        full = os.path.join(base, name)
        if not os.path.isfile(full):
            continue
        sz = os.path.getsize(full)
        if sz > MAX_FILE_BYTES:
            degraded.append({"file": name, "issue": "oversize", "bytes": sz})
            continue
        files.append({"name": name, "path": full, "bytes": sz})
    return files, degraded, None


def find_output_contract(case: dict):
    """在 case.json 中定位 output_contract（容忍键名与层级差异）。"""
    if not isinstance(case, dict):
        return None
    for key in ("output_contract", "outputContract", "contract"):
        if isinstance(case.get(key), dict):
            return case[key]
    # 部分案例把契约字段平铺在 meta 下
    for key in ("meta", "metadata"):
        m = case.get(key)
        if isinstance(m, dict) and isinstance(m.get("output_contract"), dict):
            return m["output_contract"]
    return None


def load_case(input_dir: str, files):
    """读取 case.json / task.md；返回 (case, task_text, errors, degradations)。"""
    errors, degradations = [], []
    case, task_text = None, None
    for f in files:
        if f["name"].lower() in ("case.json", "input.json"):
            obj, err = read_json(f["path"])
            if err:
                errors.append({"scope": "input", "issue": err, "file": f["name"]})
                continue
            if isinstance(obj, dict):
                case = obj
            else:
                errors.append({"scope": "input", "issue": "case_not_object", "file": f["name"]})
        elif f["name"].lower() in ("task.md", "task.txt"):
            text, err = read_text(f["path"])
            if err:
                degradations.append({"scope": "input", "issue": err, "file": f["name"]})
            task_text = text
    return case, task_text, errors, degradations


# ---------------------------------------------------------------------------
# 确定性的身份与文本规范化
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_NONALNUM = re.compile(r"[^a-z0-9]+")
_LEADING_ARTICLES = re.compile(r"^(the|a|an)\s+")


def _ascii_fold(s: str) -> str:
    try:
        import unicodedata
        return "".join(
            c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
        )
    except Exception:
        return s


def norm_title(title):
    if title is None:
        return ""
    s = _ascii_fold(str(title)).lower()
    s = _WS.sub(" ", s).strip()
    s = _LEADING_ARTICLES.sub("", s)
    s = _NONALNUM.sub(" ", s).strip()
    return s


def norm_doi(doi):
    if not doi:
        return ""
    d = str(doi).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:", "https://dx.doi.org/"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.strip()
    return d if d else ""


def norm_arxiv(arxiv):
    if not arxiv:
        return ""
    a = str(arxiv).strip().lower()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv:"):
        if a.startswith(prefix):
            a = a[len(prefix):]
    # 老式 id 去掉类别前缀：hep-th/9901001 -> 9901001（保留类别以减小误并）
    return a.strip()


def norm_author_surname(author):
    if not author:
        return ""
    s = str(author).strip()
    # "Last, First" 或 "Last, F." 格式：取逗号前的姓
    if "," in s:
        s = s.split(",", 1)[0]
    parts = s.split()
    if not parts:
        return ""
    last = parts[-1]
    return _ascii_fold(last).lower().strip(".,;")


def paper_identity_keys(p: dict):
    """身份键按优先级排列：DOI -> arXiv -> title+一作姓+年份 -> title。"""
    if not isinstance(p, dict):
        return []
    keys = []
    d = norm_doi(p.get("doi"))
    if d:
        keys.append(("doi", d))
    a = norm_arxiv(p.get("arxiv_id") or p.get("arxiv") or p.get("eprint"))
    if a:
        keys.append(("arxiv", a))
    t = norm_title(p.get("title"))
    if t:
        au = norm_author_surname(_first_author(p.get("authors")))
        yr = p.get("year") or p.get("date_year")
        if au and yr:
            keys.append(("title_author_year", "%s|%s|%s" % (t, au, yr)))
        keys.append(("title", t))
    return keys


def _first_author(authors):
    if isinstance(authors, (list, tuple)) and authors:
        first = authors[0]
        if isinstance(first, dict):
            n = first.get("name") or first.get("surname") or first.get("family")
            if n:
                return n
            g, f = first.get("given", ""), first.get("family", "")
            return ("%s %s" % (g, f)).strip()
        return first
    if isinstance(authors, str) and authors:
        return authors.split(";")[0].split(",")[0].strip()
    return None


def dedupe_papers(papers):
    """身份合并：共享任一高优先级键即视为同一工作。

    返回 (unique_papers, clusters)。合并证据保留键类型/键值/成员 id。
    同名不同文靠更长的键区分（title+作者+年份 > title）。
    """
    if not papers:
        return [], []
    parent = list(range(len(papers)))
    group_key = defaultdict(list)  # (kind, value) -> [idx]

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i, p in enumerate(papers):
        for kind, val in paper_identity_keys(p):
            group_key[(kind, val)].append(i)

    for _, idxs in group_key.items():
        base = idxs[0]
        for j in idxs[1:]:
            union(base, j)

    buckets = defaultdict(list)
    for i in range(len(papers)):
        buckets[find(i)].append(i)

    merged, clusters = [], []
    for members in buckets.values():
        members = sorted(members)
        cluster = [papers[i] for i in members]
        canonical = _pick_canonical(cluster)
        evid = []
        for i in members:
            for kind, val in paper_identity_keys(papers[i]):
                evid.append({"key_type": kind, "key_value": val, "member_id": papers[i].get("id")})
        merged.append({
            "id": canonical.get("id") or ("merged-%d" % members[0]),
            "title": canonical.get("title"),
            "authors": canonical.get("authors"),
            "year": canonical.get("year") or canonical.get("date_year"),
            "venue": canonical.get("venue") or canonical.get("journal"),
            "doi": canonical.get("doi"),
            "arxiv_id": canonical.get("arxiv_id") or canonical.get("arxiv") or canonical.get("eprint"),
            "aliases": sorted({p.get("id") for p in cluster if p.get("id")}),
            "merged_count": len(members),
            "preferred_record": canonical.get("id"),
            "merge_evidence": evid,
            "raw_refs": cluster,
        })
        clusters.append({
            "canonical_id": merged[-1]["id"],
            "members": [papers[i].get("id") for i in members],
            "key_types": sorted({e["key_type"] for e in evid}),
        })
    return merged, clusters


def _pick_canonical(cluster):
    """优先级：有 DOI > 有 arXiv > 标题最长（信息更全）。确定性。"""

    def rank(p):
        s = 0
        if norm_doi(p.get("doi")):
            s += 4
        if norm_arxiv(p.get("arxiv_id") or p.get("arxiv")):
            s += 2
        if p.get("title"):
            s += 1
        return s

    return max(cluster, key=lambda p: (rank(p), len(str(p.get("title") or "")), json.dumps(p, sort_keys=True, default=str)))


# ---------------------------------------------------------------------------
# 结构排序与最短路径
# ---------------------------------------------------------------------------

def _edges_from(obj):
    for e in obj or []:
        if isinstance(e, dict):
            yield e


def bfs_depth(edges, seed_id, directed=True):
    depth = {}
    adj = defaultdict(list)
    for e in _edges_from(edges):
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        adj[s].append(t)
        if not directed or e.get("direction") == "direction_unknown":
            adj[t].append(s)
    if seed_id:
        q = deque([(seed_id, 0)])
        depth[seed_id] = 0
        while q:
            node, d = q.popleft()
            for nb in adj.get(node, []):
                if nb not in depth:
                    depth[nb] = d + 1
                    q.append((nb, d + 1))
    return depth, adj


def rank_papers(papers, edges, seed_id=None):
    """结构排序（不是"高被引=高相关"）。

    score 只量化可客观计算的结构/来源置信度：
      - depth：离种子的引用跳数（越近越可复核）
      - indeg：本文被集合内其他论文引用的次数
      - outdeg：本文引用集合内论文的次数
      - source_confidence：evidence locator+quote 是否齐全
    主题/方法相关度必须由 Agent 依据证据判断并写入 papers[].relevance_why，
    脚本不得代劳。
    """
    depth, _ = bfs_depth(edges, seed_id)
    indeg, outdeg = defaultdict(int), defaultdict(int)
    for e in _edges_from(edges):
        s, t = e.get("source"), e.get("target")
        if s and t:
            outdeg[s] += 1
            indeg[t] += 1
    out = []
    for p in papers:
        pid = p.get("id")
        d = depth.get(pid, depth.get(str(pid), 99))
        conf = 0
        if p.get("evidence_locator") or p.get("source_locator") or p.get("locator"):
            conf += 1
        if p.get("evidence_quote") or p.get("quote") or p.get("is_verified"):
            conf += 1
        try:
            year = int(p.get("year") or p.get("date_year") or 0)
        except (TypeError, ValueError):
            year = 0
        recency = 1.0 if year >= (time.localtime().tm_year - 3) else 0.0
        score = round(
            100
            - 18 * min(int(d) if isinstance(d, int) else 99, 5)
            + 8 * min(indeg[pid], 6)
            - 4 * min(outdeg[pid], 6)
            + 12 * conf
            + 5 * recency,
            2,
        )
        out.append(dict(p, rank_score=score, _depth=(d if isinstance(d, int) else None),
                        _indeg=indeg[pid], _outdeg=outdeg[pid]))
    out.sort(key=lambda x: (-x["rank_score"], str(x.get("id", ""))))
    return out


def shortest_path(edges, source, target):
    """有向引用图上的最短路：逐跳标注方向与证据；direction_unknown 边视为双向并标出。"""
    adj = defaultdict(list)
    for e in _edges_from(edges):
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        adj[s].append((t, e, "cites"))
        if e.get("direction") == "direction_unknown":
            adj[t].append((s, e, "direction_unknown"))
    prev = {source: None}
    q = deque([source])
    while q:
        node = q.popleft()
        if node == target:
            break
        for nb, e, dirn in adj.get(node, []):
            if nb not in prev:
                prev[nb] = (node, e, dirn)
                q.append(nb)
    if target not in prev:
        return None
    hops, cur = [], target
    while prev[cur] is not None:
        node, e, dirn = prev[cur]
        hops.append({
            "from": node, "to": cur, "direction": dirn,
            "evidence_locator": e.get("evidence_locator") or e.get("locator"),
            "evidence_quote": e.get("evidence_quote") or e.get("quote"),
        })
        cur = node
    hops.reverse()
    # 校验 hops 确实从 source 出发（source 与 target 断开时已在上方返回 None）
    if hops and hops[0]["from"] != source:
        return None
    return hops


def node_depth(edges, seed_id):
    depth, _ = bfs_depth(edges, seed_id)
    return depth


# ---------------------------------------------------------------------------
# 产物生成
# ---------------------------------------------------------------------------

def _git(x, default=""):
    if x is None:
        return default
    if isinstance(x, (list, tuple)):
        return ";".join(str(v) for v in x)
    if isinstance(x, dict):
        return json.dumps(x, ensure_ascii=False, sort_keys=True)
    return str(x)


def _fetch_papers_edges(result):
    res = result.get("results", {}) if isinstance(result.get("results", {}), dict) else {}
    papers = res.get("papers") or result.get("papers") or []
    edges = res.get("edges") or result.get("edges") or []
    return papers, edges


def _paper_cell(p, key):
    if key in ("authors", "roles", "keywords"):
        return _git(p.get(key))
    if key in ("source_locator", "locator", "evidence_locator"):
        return _git(p.get(key) or p.get("evidence_locator") or p.get("locator"))
    if key == "quote":
        return _git(p.get(key) or p.get("evidence_quote"))
    return _git(p.get(key))


def write_papers_csv(papers, path):
    cols = ["id", "title", "authors", "year", "venue", "doi", "arxiv_id",
            "roles", "source_locator", "quote"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for p in papers:
            w.writerow([_paper_cell(p, c) for c in cols])
    return path


def write_edges_csv(edges, path):
    cols = ["source", "target", "relation", "source_label", "target_label",
            "direction", "evidence_locator", "evidence_quote"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for e in edges:
            w.writerow([
                _git(e.get("source")), _git(e.get("target")),
                _git(e.get("relation", "cites")),
                _git(e.get("source_label")), _git(e.get("target_label")),
                _git(e.get("direction", "cites")),
                _git(e.get("evidence_locator") or e.get("locator")),
                _git(e.get("evidence_quote") or e.get("quote")),
            ])
    return path


_ROLE_COLORS = {
    "seed": "#b71c1c",
    "foundation": "#0d47a1",
    "bridge": "#4a148c",
    "hot": "#e65100",
    "relevant": "#1b5e20",
    "default": "#37474f",
}


def build_svg(papers, edges, seed_id=None, title="citation graph"):
    """确定性分层布局 SVG（按 BFS 深度分层，层内按 id 排序，无随机）。"""
    if not papers:
        return None
    depth = node_depth(edges, seed_id)
    max_dep = max([depth.get(p.get("id"), 0) for p in papers] or [0])
    layers = defaultdict(list)
    for p in papers:
        pid = p.get("id") or "?"
        d = depth.get(pid)
        d = d if isinstance(d, int) else max_dep + 1
        layers[d].append(p)
    for d in layers:
        layers[d].sort(key=lambda p: str(p.get("id", "")))

    X_SPACING, Y_SPACING = 280, 74
    positions = {}
    max_n = max((len(v) for v in layers.values()), default=1)
    width = (max_dep + 2) * X_SPACING + 80
    height = max_n * Y_SPACING + 80
    node_ids = {p.get("id") for p in papers}
    for d, ps in layers.items():
        for i, p in enumerate(ps):
            positions[p.get("id")] = (80 + d * X_SPACING, 40 + i * Y_SPACING + Y_SPACING / 2)

    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (width, height, width, height)]
    parts.append('<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="6" '
                 'refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#546e7a"/></marker></defs>')
    parts.append('<rect width="100%" height="100%" fill="#fafafa"/>')
    parts.append('<text x="20" y="24" font-family="sans-serif" font-size="15" '
                 'fill="#263238">%s</text>' % html.escape(title or "citation graph"))

    # edges (only those with both ends in node_ids, arrow source->target)
    seen = set()
    for e in sorted(_edges_from(edges), key=lambda e: (str(e.get("source")), str(e.get("target")))):
        s, t = e.get("source"), e.get("target")
        if s not in positions or t not in positions:
            continue
        k = (s, t, e.get("direction"))
        if k in seen:
            continue
        seen.add(k)
        x1, y1 = positions[s]
        x2, y2 = positions[t]
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#90a4ae" stroke-width="1.2" marker-end="url(#arr)"/>'
                     % (x1, y1, x2, y2))

    for p in papers:
        pid = p.get("id") or "?"
        if pid not in positions:
            continue
        x, y = positions[pid]
        role = p.get("role") or "default"
        color = _ROLE_COLORS.get(str(role).lower(), _ROLE_COLORS["default"])
        label = str(p.get("title") or pid)
        label = (label[:42] + "…") if len(label) > 42 else label
        parts.append('<ellipse cx="%.1f" cy="%.1f" rx="86" ry="20" fill="%s" opacity="0.92"/>'
                     % (x, y, color))
        parts.append('<text x="%.1f" y="%.1f" font-family="sans-serif" font-size="11" '
                     'fill="#ffffff" text-anchor="middle">%s</text>'
                     % (x, y + 4, html.escape(label)))
    parts.append("</svg>")
    return "\n".join(parts)


def write_report(output_dir, result):
    lines = ["# 引文追踪结果报告（自动生成，可复核）", ""]
    lines.append("- case_id: `%s`" % _git(result.get("case_id")))
    lines.append("- status: `%s`" % _git(result.get("status")))
    lines.append("- research_question: %s" % _git(result.get("research_question"), "（未填写）"))
    seed = result.get("seed")
    lines.append("- seed: %s" % (_git(seed) if seed else "（未识别）"))
    lines.append("")
    papers, edges = _fetch_papers_edges(result)
    lines.append("## 统计")
    lines.append("- 文献数（去重后）: %d" % len(papers))
    lines.append("- 有向引用边: %d" % len(edges))
    lines.append("- 路径数: %d" % len((result.get("results", {}).get("paths") or [])))
    lines.append("")
    if result.get("sources"):
        lines.append("## 来源与证据")
        for s in result["sources"]:
            lines.append("- `%s` — %s" % (_git(s.get("locator")), html.unescape(_git(s.get("quote"), ""))[:200]))
        lines.append("")
    if result.get("uncertainty"):
        lines.append("## 不确定性与冲突")
        for u in result["uncertainty"]:
            lines.append("- `%s`: %s" % (_git(u.get("type") or u.get("issue")), _git(u.get("detail"), "")))
        lines.append("")
    if result.get("pending_human"):
        lines.append("## 待人工确认")
        for h in result["pending_human"]:
            lines.append("- %s" % _git(h))
        lines.append("")
    if result.get("limitations"):
        lines.append("## 已知限制")
        for item in result["limitations"]:
            lines.append("- %s" % _git(item))
        lines.append("")
    if result.get("change_log"):
        lines.append("## 变更记录（最近 5 条）")
        for c in result["change_log"][-5:]:
            lines.append("- %s：%s（%s）" % (_git(c.get("time")), _git(c.get("content")), _git(c.get("reason"), "")))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# bootstrap / finalize / selftest
# ---------------------------------------------------------------------------

def bootstrap(input_dir, output_dir, work_dir):
    errors, degradations = [], []
    files, deg_in, scan_err = scan_input(input_dir)
    degradations.extend(deg_in)
    if scan_err:
        errors.append({"scope": "input", "issue": scan_err})
    case, task_text, errs, degs = load_case(input_dir, files)
    errors.extend(errs)
    degradations.extend(degs)

    if not files:
        status = "insufficient_evidence"
    elif case is None:
        status = "input_error"
    else:
        status = "partial"

    case_id = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id") or case.get("challenge_id")
    else:
        case_id = None
    now = utcnow()
    result = {"case_id": case_id or "unknown", "status": status}

    oc = find_output_contract(case) if isinstance(case, dict) else None
    if isinstance(oc, dict):
        result["output_contract"] = oc  # 快照，保证不改名/不删未知键
        for k, v in oc.items():
            if k in ("case_id",):
                continue
            if v is not None:
                result[k] = v
            else:
                result[k] = [] if isinstance(v, list) else None

    result.update({
        "sources": [],
        "uncertainty": [],
        "change_log": [{"time": now, "content": "bootstrap：落盘结果骨架", "reason": "3 分钟内先产出合法 partial 结果"}],
        "results": {"papers": [], "edges": [], "clusters": [], "paths": [], "pruned": []},
        "seed": None,
        "seed_key": None,
        "research_question": task_text[:2000] if task_text else None,
        "limitations": [],
        "pending_human": [],
        "errors": errors,
        "degradations": degradations,
        "artifacts": [],
        "runtime": {"version": VERSION, "started_at": now, "updated_at": now},
    })

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    err_out = write_json_atomic(os.path.join(output_dir, "result.json"), result)
    if err_out:
        # 写盘失败也给它留一条，但不让进程崩溃
        errors.append({"scope": "output", "issue": err_out})
        result["errors"] = errors
        write_json_atomic(os.path.join(output_dir, "result.json"), result)

    state = {
        "case_id": result["case_id"],
        "status": status,
        "input_files": [f["name"] for f in files],
        "started_at": now,
        "change_count": len(result["change_log"]),
    }
    err_state = write_json_atomic(os.path.join(work_dir, "state.json"), state)
    if err_state:
        result.setdefault("degradations", []).append({"scope": "work", "issue": err_state})
        write_json_atomic(os.path.join(output_dir, "result.json"), result)
    return 0


def finalize(output_dir):
    path = os.path.join(output_dir, "result.json")
    r, err = read_json(path)
    if err or not isinstance(r, dict):
        # 无结果时也能交付最小合法文件
        r = r if isinstance(r, dict) else {"case_id": "unknown", "status": "input_error"}
        r.setdefault("errors", []).append({"scope": "output", "issue": err or "result_missing"})
    r.setdefault("case_id", "unknown")
    r.setdefault("status", "input_error" if r.get("case_id") == "unknown" else "partial")
    # 诚实性修正：case 无法识别且不是输入错误时，降为证据不足
    if r.get("case_id") == "unknown" and r.get("status") not in ("input_error", "insufficient_evidence"):
        r["status"] = "insufficient_evidence"
    now = utcnow()
    r.setdefault("runtime", {})
    r["runtime"]["updated_at"] = now
    r["runtime"]["version"] = VERSION

    papers, edges = _fetch_papers_edges(r)
    artifacts = []
    try:
        if papers:
            p_csv = os.path.join(output_dir, "papers.csv")
            write_papers_csv(papers, p_csv)
            artifacts.append({"path": "papers.csv", "kind": "csv", "rows": len(papers),
                              "detail": "去重后文献清单，可导入 Excel"})
    except OSError as exc:
        r.setdefault("errors", []).append({"scope": "output", "issue": str(exc)})
    try:
        if edges:
            e_csv = os.path.join(output_dir, "edges.csv")
            write_edges_csv(edges, e_csv)
            artifacts.append({"path": "edges.csv", "kind": "csv", "rows": len(edges),
                              "detail": "统一 source cites target 的有向引用边"})
    except OSError as exc:
        r.setdefault("errors", []).append({"scope": "output", "issue": str(exc)})
    try:
        if papers or edges:
            svg = build_svg(papers, edges, r.get("seed_key"), "citation graph")
            if svg:
                svg_path = os.path.join(output_dir, "citation_graph.svg")
                with open(svg_path, "w", encoding="utf-8") as fh:
                    fh.write(svg)
                artifacts.append({"path": "citation_graph.svg", "kind": "svg",
                                  "detail": "可编辑引用网络图（分层布局）"})
    except OSError as exc:
        r.setdefault("errors", []).append({"scope": "output", "issue": str(exc)})

    rep = write_report(output_dir, r)
    rep_path = os.path.join(output_dir, "report.md")
    try:
        with open(rep_path, "w", encoding="utf-8") as fh:
            fh.write(rep)
        artifacts.append({"path": "report.md", "kind": "markdown", "detail": "自动汇总报告"})
    except OSError as exc:
        r.setdefault("errors", []).append({"scope": "output", "issue": str(exc)})

    r["artifacts"] = artifacts
    # 状态诚实校验：缺关键主产物则不能标 ok
    if not papers and r.get("status") == "ok":
        r["status"] = "insufficient_evidence"
    write_json_atomic(path, r)
    return 0


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def _mk(root, name, content):
    p = os.path.join(root, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)
    return p


def run_selftest():
    root = tempfile.mkdtemp(prefix="ct_selftest_")
    failures = []

    def check(name, cond, detail=""):
        if not cond:
            failures.append("%s: %s" % (name, detail))
        print(("PASS " if cond else "FAIL ") + name + ((" - " + detail) if detail else ""))

    # 1) 空输入目录
    empty_in = os.path.join(root, "empty_in")
    out1 = os.path.join(root, "out1")
    work1 = os.path.join(root, "work1")
    os.makedirs(empty_in, exist_ok=True)
    bootstrap(empty_in, out1, work1)
    r1, _ = read_json(os.path.join(out1, "result.json"))
    check("empty_input", (r1 or {}).get("status") == "insufficient_evidence",
          "status=%s" % (r1 or {}).get("status"))

    # 2) 坏 JSON
    bad_in = os.path.join(root, "bad_in")
    out2 = os.path.join(root, "out2")
    work2 = os.path.join(root, "work2")
    os.makedirs(bad_in, exist_ok=True)
    _mk(bad_in, "case.json", "{ not valid json")
    _mk(bad_in, "task.md", "# task\n")
    bootstrap(bad_in, out2, work2)
    r2, _ = read_json(os.path.join(out2, "result.json"))
    check("bad_json", (r2 or {}).get("status") == "input_error",
          "status=%s" % (r2 or {}).get("status"))

    # 3) 合成案例：output_contract 回填 + 去重 + 排序 + 路径 + 产物 + 确定性
    # 附加：作者姓规范化（"Last, First" 与 "First Last" 应归一为同一姓）
    check("author_surname_norm",
          norm_author_surname("Vaswani, Ashish") == norm_author_surname("Ashish Vaswani") == "vaswani",
          "got %r vs %r" % (norm_author_surname("Vaswani, Ashish"),
                            norm_author_surname("Ashish Vaswani")))

    case = {
        "case_id": "synth-1",
        "id": "synth-1",
        "output_contract": {"candidate_papers": None, "evidence_records": [], "review_notes": "mock"},
    }
    in3 = os.path.join(root, "in3")
    out3a = os.path.join(root, "out3a")
    work3a = os.path.join(root, "work3a")
    os.makedirs(in3, exist_ok=True)
    _mk(in3, "case.json", json.dumps(case))
    _mk(in3, "task.md", "# trace seed A\n")
    bootstrap(in3, out3a, work3a)

    # 填充 papers/edges 模拟 Agent 增量结果（含同一论文 pre-print/journal 两个版本）
    papers = [
        {"id": "A", "title": "Attention Is All You Need", "authors": ["Vaswani, Ashish"], "year": 2017,
         "doi": "10.5555/3295222.3295349", "role": "seed", "evidence_locator": "materials/x.pdf#p1",
         "evidence_quote": "We propose the Transformer."},
        {"id": "B", "title": "Attention is all you need", "authors": ["Ashish Vaswani"], "year": 2017,
         "arxiv_id": "1706.03762", "role": "foundation", "evidence_locator": "materials/x.pdf#p2",
         "evidence_quote": "Transformer architecture."},
        {"id": "C", "title": "BERT: Pre-training of Deep Bidirectional Transformers", "authors": ["Devlin, Jacob"],
         "year": 2019, "doi": "10.18653/v1/N19-1423", "role": "hot",
         "evidence_locator": "materials/y.pdf#p3", "evidence_quote": "Bidirectional encoder."},
    ]
    edges = [
        {"source": "C", "target": "A", "relation": "cites", "direction": "cites",
         "evidence_locator": "materials/y.pdf#p4", "evidence_quote": "based on Transformer."},
        {"source": "B", "target": "C", "relation": "cites", "direction": "direction_unknown",
         "evidence_locator": "materials/z.pdf#p5", "evidence_quote": ""},
    ]
    r3, _ = read_json(os.path.join(out3a, "result.json"))
    r3["results"]["papers"] = papers
    r3["results"]["edges"] = edges
    r3["seed"] = papers[0]["title"]
    r3["seed_key"] = "A"
    r3["status"] = "partial"
    r3["change_log"].append({"time": utcnow(), "content": "合成去重/排序/路径测试", "reason": "自检"})
    write_json_atomic(os.path.join(out3a, "result.json"), r3)

    merged, clusters = dedupe_papers(papers)
    check("dedupe_merge", len(merged) == 2, "merged=%d (expect 2: A/B 同文)" % len(merged))
    path = shortest_path(edges, "C", "A")
    check("shortest_path", bool(path) and len(path) == 1, "hops=%d" % (len(path) if path else 0))

    finalize(out3a)
    r3f, _ = read_json(os.path.join(out3a, "result.json"))
    check("finalize_artifacts", os.path.isfile(os.path.join(out3a, "papers.csv"))
          and os.path.isfile(os.path.join(out3a, "citation_graph.svg")), "artifacts present")
    svg_text, _ = read_text(os.path.join(out3a, "citation_graph.svg"))
    check("svg_wellformed", svg_text.strip().startswith("<svg") and svg_text.strip().endswith("</svg>"),
          "svg parse ok")

    # 确定性：同输入重复 finalize，产物逐字节一致（时间戳只进 result.json，不进产物）
    def _sha(fname):
        try:
            with open(os.path.join(out3a, fname), "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return None

    before = {f: _sha(f) for f in ("papers.csv", "edges.csv", "citation_graph.svg")}
    finalize(out3a)
    after = {f: _sha(f) for f in ("papers.csv", "edges.csv", "citation_graph.svg")}
    check("deterministic", before == after, "artifact hashes stable across rerun")

    print("\nselftest: %s (%d failures)" % ("ALL PASS" if not failures else "FAIL", len(failures)))
    return 0 if not failures else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="citation-tracing", description="引文追踪确定性脚本内核")
    sub = ap.add_subparsers(dest="cmd")

    def add_io(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("bootstrap", help="落盘 result.json 骨架")
    add_io(sp)

    sp = sub.add_parser("finalize", help="校验并重生成 CSV/SVG/报告")
    sp.add_argument("--output", default="/app/output")

    sp = sub.add_parser("dedupe", help="论文身份合并")
    sp.add_argument("--papers", required=True)
    sp.add_argument("--out", required=True)

    sp = sub.add_parser("rank", help="结构排序")
    sp.add_argument("--papers", required=True)
    sp.add_argument("--edges", required=True)
    sp.add_argument("--seed", default=None)
    sp.add_argument("--out", required=True)

    sp = sub.add_parser("path", help="最短引用路径")
    sp.add_argument("--edges", required=True)
    sp.add_argument("--source", required=True)
    sp.add_argument("--target", required=True)
    sp.add_argument("--out", required=True)

    sub.add_parser("selftest", help="自检")

    args = ap.parse_args(argv)

    if args.cmd == "bootstrap":
        return bootstrap(args.input, args.output, args.work)
    if args.cmd == "finalize":
        return finalize(args.output)
    if args.cmd == "dedupe":
        papers, err = read_json(args.papers)
        if err or not isinstance(papers, list):
            print("dedupe: bad papers input", file=sys.stderr)
            return 2
        merged, clusters = dedupe_papers(papers)
        write_json_atomic(args.out, {"unique": merged, "clusters": clusters})
        return 0
    if args.cmd == "rank":
        papers, e1 = read_json(args.papers)
        edges, e2 = read_json(args.edges)
        if e1 or e2 or not isinstance(papers, list) or not isinstance(edges, list):
            print("rank: bad input", file=sys.stderr)
            return 2
        write_json_atomic(args.out, rank_papers(papers, edges, args.seed))
        return 0
    if args.cmd == "path":
        edges, e1 = read_json(args.edges)
        if e1 or not isinstance(edges, list):
            print("path: bad edges input", file=sys.stderr)
            return 2
        p = shortest_path(edges, args.source, args.target)
        write_json_atomic(args.out, {"found": bool(p), "hops": p or []})
        return 0
    if args.cmd == "selftest":
        return run_selftest()

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())