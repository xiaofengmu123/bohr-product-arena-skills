# -*- coding: utf-8 -*-
"""citation_lib.py — 引文追踪 Skill 的确定性核心库（仅 Python 标准库）。

设计约束：
  * 无网络、无第三方依赖、无外部数据。所有函数对相同输入给出相同输出；
    时间戳只写入 runtime.* 与 change_log[].time，绝不写入 SVG/CSV/report 正文，
    因此同一输入两次运行得到的内容指纹（见 selftest）一致。
  * 所有文件读写采用原子写（tmp + os.replace），任何中途异常都不会留下半截 JSON。
  * 路径用相对/运行参数传入，不写死 /input、/app/output。

主要职责：
  1. 输入发现（编码容错、2MB 上限、坏 JSON 降级）
  2. output_contract 提取 + 契约对齐骨架构造
  3. 安全增量修改（JSON 路径 set/append）+ change_log 自动追加
  4. 论文身份去重、边方向规范、排序、最短路径、图谱摘要
  5. 渲染 report.md / papers.csv / edges.csv / citation_graph.svg
  6. 校验与自检支持
"""

import copy
import csv
import datetime
import html
import io
import json
import math
import os
import re

SCHEMA_VERSION = "2.0.0"
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
MAX_FILE_BYTES = 2 * 1024 * 1024
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
_MISSING = object()

# 标准结果 schema（无论 output_contract 是否存在都落盘）
STANDARD_FIELDS = (
    "case_id", "schema_version", "status", "contract", "results", "sources",
    "uncertainty", "change_log", "seed", "seed_key", "research_question",
    "limitations", "pending_human", "errors", "degradations", "runtime",
)


# --------------------------------------------------------------------------
# 时间与文本/JSON 读取
# --------------------------------------------------------------------------

def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """容错读取文本文件，返回 (text, encoding, error)。超限跳过。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return ("", None, str(e))
    if size > max_bytes:
        return ("", None, "size %d exceeds cap %d" % (size, max_bytes))
    last_err = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return (fh.read(), enc, None)
        except (UnicodeDecodeError, ValueError) as e:
            last_err = str(e)
        except OSError as e:
            return ("", None, str(e))
    return ("", None, last_err)


def parse_json(text):
    """解析 JSON 字符串，返回 (obj, error)。"""
    try:
        return (json.loads(text), None)
    except (ValueError, TypeError) as e:
        return (None, str(e))


def read_json_file(path):
    """读取并解析 JSON 文件，返回 (obj, error)。坏文件不抛异常。"""
    if not os.path.isfile(path):
        return (None, "missing file: %s" % path)
    text, _enc, err = read_text(path)
    if err is not None:
        return (None, "read error: %s" % err)
    return parse_json(text)


# --------------------------------------------------------------------------
# 输入发现
# --------------------------------------------------------------------------

def scan_input(input_dir):
    """递归列出输入目录文件（稳定排序），记录大小与是否超限。"""
    files = []
    if not input_dir or not os.path.isdir(input_dir):
        return files
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(root, n)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            files.append({
                "rel": rel,
                "path": p,
                "size": size,
                "skipped_size": size > MAX_FILE_BYTES,
                "ext": os.path.splitext(n)[1].lower(),
            })
    return files


# --------------------------------------------------------------------------
# output_contract 提取与骨架构造
# --------------------------------------------------------------------------

def get_output_contract(case):
    """从 case 字典提取 output_contract，找不到返回 None。"""
    if not isinstance(case, dict):
        return None
    for key in ("output_contract", "outputContract", "output", "contract"):
        v = case.get(key)
        if isinstance(v, dict):
            # 若键名是 output，优先其内部 contract 子键
            if key == "output":
                for sub in ("contract", "output_contract"):
                    if isinstance(v.get(sub), (dict, list)):
                        return v[sub]
                continue
            return v
        if isinstance(v, list):
            return v
    return None


def contract_fields(contract):
    """返回 output_contract 的字段名有序列表（dict 键 / list 中的 name 或字符串）。"""
    out = []
    if isinstance(contract, dict):
        for k in contract.keys():
            out.append(str(k))
    elif isinstance(contract, list):
        for it in contract:
            if isinstance(it, str):
                out.append(it)
            elif isinstance(it, dict):
                nm = it.get("name") or it.get("field") or it.get("key")
                if nm is not None:
                    out.append(str(nm))
    return out


def default_for_type(spec):
    """按契约 spec 推断占位默认值：array->[]，object->{}，其余->null。"""
    if isinstance(spec, dict):
        if "default" in spec:
            return copy.deepcopy(spec["default"])
        t = str(spec.get("type", "")).lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        return None
    if isinstance(spec, str):
        t = spec.lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        return None
    # 字面量默认值（list/dict/scalar）
    return copy.deepcopy(spec)


def extract_case_id(case, input_dir=None):
    if isinstance(case, dict):
        for k in ("case_id", "caseId", "id", "armiId"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if input_dir:
        p = os.path.join(input_dir, "case.json")
        if os.path.isfile(p):
            return os.path.basename(os.path.normpath(input_dir)) or "case"
    return "unknown_case"


def new_results():
    return {
        "papers": [],
        "edges": [],
        "clusters": [],
        "paths": [],
        "pruned": [],
        "graph_summary": {
            "nodes": 0, "edges": 0, "layers": 0,
            "hub_nodes": [], "bridge_nodes": [], "recent_hotspots": [],
        },
    }


def build_skeleton(case, input_dir=None):
    """构造与 output_contract 逐字段对齐的结果骨架（partial）。"""
    case_id = extract_case_id(case, input_dir)
    contract = get_output_contract(case)
    fields = contract_fields(contract)
    mirror = contract if isinstance(contract, (dict, list)) else None

    skel = {}
    skel["case_id"] = case_id
    skel["schema_version"] = SCHEMA_VERSION
    skel["status"] = "partial"
    skel["contract"] = {
        "source": "case.json#output_contract" if contract is not None else "absent",
        "fields": list(fields),
        "mirror": mirror,
        "satisfied": False,
        "missing": list(fields),
        "unfilled": list(fields),
    }

    # 原样回填 output_contract 的全部字段（顶层键），缺值用类型化占位。
    if isinstance(contract, dict):
        for k in fields:
            skel[k] = default_for_type(contract.get(k))
    elif isinstance(contract, list):
        for it in contract:
            if isinstance(it, dict):
                nm = it.get("name") or it.get("field") or it.get("key")
                if nm is not None:
                    skel[str(nm)] = default_for_type(it)
            elif isinstance(it, str):
                skel[it] = None

    skel["results"] = new_results()
    skel["sources"] = []
    skel["uncertainty"] = {"conflicts": [], "missing": [], "coverage_gaps": [], "notes": []}
    skel["change_log"] = []
    skel["seed"] = None
    skel["seed_key"] = None
    skel["research_question"] = None
    skel["limitations"] = []
    skel["pending_human"] = []
    skel["errors"] = []
    skel["degradations"] = []
    skel["runtime"] = {
        "started_utc": now_utc(),
        "updated_utc": now_utc(),
        "seconds": 0.0,
        "input_dir": input_dir,
        "input_files": [],
        "request_budget": {"max_requests": 64, "used_hint": None},
        "steps": [],
    }
    append_change(skel, "bootstrap", "created contract-aligned skeleton", "step0 mandatory early persistence")
    return skel


def recompute_contract(result):
    """按契约重算 satisfied/missing/unfilled，供每次修改后调用。"""
    c = result.get("contract")
    if not isinstance(c, dict):
        c = {"source": "absent", "fields": [], "mirror": None}
        result["contract"] = c
    fields = list(c.get("fields") or [])
    missing = [f for f in fields if f not in result]
    unfilled = [f for f in fields if f in result and result[f] is None]
    c["missing"] = missing
    c["unfilled"] = unfilled
    c["satisfied"] = len(missing) == 0
    return c


def validate(result):
    """结构校验，返回 (ok, problems)。"""
    problems = []
    if not isinstance(result, dict):
        return (False, ["result is not an object"])
    if not result.get("case_id"):
        problems.append("missing case_id")
    if result.get("status") not in STATUSES:
        problems.append("invalid status: %r" % result.get("status"))
    contract = result.get("contract")
    if isinstance(contract, dict):
        for f in contract.get("fields") or []:
            if f not in result:
                problems.append("contract field missing: %s" % f)
    for key in ("results", "sources", "uncertainty", "change_log"):
        if key not in result:
            problems.append("missing standard field: %s" % key)
    if not isinstance(result.get("results", {}).get("papers"), list):
        problems.append("results.papers must be a list")
    if not isinstance(result.get("results", {}).get("edges"), list):
        problems.append("results.edges must be a list")
    return (len(problems) == 0, problems)


# --------------------------------------------------------------------------
# 路径读写（JSON 路径：点号分层，或 / 开头的 RFC6901 指针；数字下标为数组）
# --------------------------------------------------------------------------

def parse_path(path):
    if path is None:
        return []
    path = str(path)
    if path == "":
        return []
    if path.startswith("/"):
        return [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
    return path.split(".")


def get_by_path(obj, path, default=None):
    cur = obj
    for p in parse_path(path):
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return default
        elif isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur


def set_by_path(obj, path, value):
    parts = parse_path(path)
    if not parts:
        return obj
    cur = obj
    for p in parts[:-1]:
        if isinstance(cur, list):
            i = int(p)
            while len(cur) <= i:
                cur.append({})
            cur = cur[i]
        else:
            if p not in cur or not isinstance(cur[p], (dict, list)):
                cur[p] = {}
            cur = cur[p]
    last = parts[-1]
    if isinstance(cur, list):
        i = int(last)
        while len(cur) <= i:
            cur.append(None)
        cur[i] = value
    else:
        cur[last] = value
    return obj


def append_by_path(obj, path, value):
    parts = parse_path(path)
    if not parts:
        return obj
    cur = obj
    for p in parts[:-1]:
        if isinstance(cur, list):
            i = int(p)
            while len(cur) <= i:
                cur.append({})
            cur = cur[i]
        else:
            if p not in cur or not isinstance(cur[p], (dict, list)):
                cur[p] = {}
            cur = cur[p]
    last = parts[-1]
    if isinstance(cur, list):
        i = int(last)
        while len(cur) <= i:
            cur.append(None)
        target = cur[i]
        if not isinstance(target, list):
            target = []
            cur[i] = target
        target.append(value)
    else:
        lst = cur.get(last)
        if not isinstance(lst, list):
            lst = []
            cur[last] = lst
        lst.append(value)
    return obj


def _brief(value, maxlen=60):
    s = str(value)
    return s if len(s) <= maxlen else s[:maxlen] + "..."


# --------------------------------------------------------------------------
# 修改指令应用与 change_log
# --------------------------------------------------------------------------

def append_change(result, action, detail, reason):
    log = result.setdefault("change_log", [])
    entry = {
        "seq": len(log) + 1,
        "time": now_utc(),
        "action": action,
        "detail": detail,
        "reason": reason or "",
    }
    log.append(entry)
    return entry


def apply_instruction(result, instr):
    """应用单条修改指令并自动追加 change_log、重算契约、更新 runtime。"""
    if not isinstance(instr, dict):
        raise ValueError("instruction must be an object")
    op = instr.get("op")
    reason = instr.get("reason") or instr.get("note") or ""
    detail = ""

    if op == "set":
        path = instr.get("path")
        if path is None:
            raise ValueError("set requires path")
        value = instr.get("value")
        set_by_path(result, path, value)
        detail = "set %s = %s" % (path, _brief(value))

    elif op == "append":
        path = instr.get("path")
        if path is None:
            raise ValueError("append requires path")
        append_by_path(result, path, instr.get("value"))
        detail = "append to %s" % path

    elif op == "upsert_paper":
        paper = instr.get("paper")
        if not isinstance(paper, dict):
            raise ValueError("upsert_paper requires paper object")
        pid = paper.get("id") or stable_paper_id(paper)
        paper = dict(paper)
        paper["id"] = pid
        upsert_by_id(result["results"].setdefault("papers", []), paper, idkey="id")
        detail = "upsert paper %s" % pid

    elif op == "add_edge":
        edge = instr.get("edge")
        if not isinstance(edge, dict):
            raise ValueError("add_edge requires edge object")
        edges = result["results"].setdefault("edges", [])
        key = (edge.get("source"), edge.get("target"), edge.get("relation", "cites"))
        dup = any(
            (e.get("source"), e.get("target"), e.get("relation", "cites")) == key
            for e in edges
        )
        if dup:
            detail = "skip duplicate edge %s->%s" % (key[0], key[1])
        else:
            edge.setdefault("relation", "cites")
            edges.append(edge)
            detail = "add edge %s->%s" % (key[0], key[1])

    elif op == "merge_papers":
        kept = instr.get("kept_id")
        merged = instr.get("merged_ids") or []
        evidence = instr.get("evidence")
        n = merge_papers(result["results"].setdefault("papers", []), kept, merged, evidence)
        detail = "merge %d paper(s) into %s" % (n, kept)

    elif op == "dedupe":
        before = len(result["results"].get("papers", []))
        result["results"]["papers"] = dedupe_papers(result["results"].get("papers", []))
        after = len(result["results"]["papers"])
        detail = "dedupe papers %d -> %d" % (before, after)

    elif op == "set_status":
        st = instr.get("status")
        if st not in STATUSES:
            raise ValueError("invalid status: %r" % st)
        result["status"] = st
        detail = "status = %s" % st

    elif op == "set_contract":
        field = instr.get("field")
        if field is None:
            raise ValueError("set_contract requires field")
        result[str(field)] = instr.get("value")
        detail = "set contract field %s" % field

    elif op == "rank":
        rank_papers(result)
        detail = "re-ranked papers"

    elif op == "paths":
        build_paths(result)
        detail = "rebuilt paths"

    elif op == "summary":
        compute_graph_summary(result)
        detail = "recomputed graph summary"

    else:
        raise ValueError("unknown op: %r" % op)

    recompute_contract(result)
    result.setdefault("runtime", {})["updated_utc"] = now_utc()
    append_change(result, op, detail, reason)
    return result


# --------------------------------------------------------------------------
# 身份与去重
# --------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(s):
    return _SLUG_RE.sub("-", str(s).lower()).strip("-")


def norm_id(v):
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = s.replace("https://doi.org/", "").replace("http://doi.org/", "").replace("doi:", "")
    return s.strip()


def norm_title(t):
    if t is None:
        return ""
    s = str(t).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[:\-,.;()\[\]{}\"']", " ", s)
    return " ".join(s.split())


def first_author_surname(authors):
    if isinstance(authors, list) and authors:
        parts = str(authors[0]).split()
        return parts[-1] if parts else str(authors[0])
    if isinstance(authors, str) and authors:
        parts = authors.split()
        return parts[-1]
    return ""


def stable_paper_id(paper):
    for k in ("doi", "arxiv_id", "arxiv"):
        v = norm_id(paper.get(k))
        if v:
            return "doi:" + v if k == "doi" else "arxiv:" + v
    title = norm_title(paper.get("title"))
    year = paper.get("year") or paper.get("publication_year")
    surname = first_author_surname(paper.get("authors"))
    if title:
        base = "%s-%s-%s" % (surname or "anon", year or "noyear", title)
        return "id:" + slug(base)[:80]
    return "id:" + slug(str(paper.get("id") or "unnamed"))


def paper_identity_keys(p):
    keys = []
    doi = norm_id(p.get("doi"))
    if doi:
        keys.append(("doi", doi))
    ax = norm_id(p.get("arxiv_id") or p.get("arxiv"))
    if ax:
        keys.append(("arxiv", ax))
    title = norm_title(p.get("title"))
    if title:
        keys.append(("title", title))
        year = p.get("year") or p.get("publication_year")
        surname = first_author_surname(p.get("authors"))
        if surname and year:
            keys.append(("title_author_year", "%s|%s|%s" % (title, surname, year)))
    return keys


def dedupe_papers(papers):
    """按 DOI → arXiv → 标题(+作者+年份) 优先级合并同文不同版本。"""
    if not papers:
        return papers
    groups = {}
    keep = []
    for p in papers:
        keys = paper_identity_keys(p)
        matched = None
        matched_key = None
        for k in keys:
            if k in groups:
                matched = groups[k]
                matched_key = k
                break
        if matched is None:
            idx = len(keep)
            keep.append(dict(p))
            for k in keys:
                groups.setdefault(k, idx)
        else:
            canonical = keep[matched]
            canonical.setdefault("merged", [])
            canonical["merged"].append({
                "from_id": p.get("id"),
                "key_type": matched_key[0] if matched_key else None,
                "key_value": matched_key[1] if matched_key else None,
                "title": p.get("title"),
            })
            for f, v in p.items():
                if f == "merged":
                    continue
                if canonical.get(f) in (None, "", []) and v not in (None, "", []):
                    canonical[f] = v
    return keep


def upsert_by_id(items, item, idkey="id"):
    iid = item.get(idkey)
    for i, it in enumerate(items):
        if it.get(idkey) == iid:
            merged = dict(it)
            for k, v in item.items():
                if v is not None:
                    merged[k] = v
            items[i] = merged
            return i
    items.append(dict(item))
    return len(items) - 1


def merge_papers(papers, kept_id, merged_ids, evidence=None):
    """把 merged_ids 合并进 kept_id，保留合并证据。返回实际合并数量。"""
    kept_paper = None
    for p in papers:
        if p.get("id") == kept_id:
            kept_paper = p
            break
    if kept_paper is None:
        raise ValueError("kept_id not found: %s" % kept_id)
    n = 0
    kept_paper.setdefault("merged", [])
    for mid in merged_ids:
        for i, p in enumerate(papers):
            if p.get("id") == mid and p is not kept_paper:
                kept_paper["merged"].append({
                    "from_id": mid,
                    "title": p.get("title"),
                    "evidence": evidence,
                })
                for f, v in p.items():
                    if f == "merged":
                        continue
                    if kept_paper.get(f) in (None, "", []) and v not in (None, "", []):
                        kept_paper[f] = v
                papers[i] = None
                n += 1
                break
    result = [p for p in papers if p is not None]
    papers[:] = result
    return n


# --------------------------------------------------------------------------
# 排序、最短路径、图谱摘要
# --------------------------------------------------------------------------

def _seed_id(result):
    seed_key = result.get("seed_key")
    if isinstance(seed_key, str) and seed_key:
        return norm_id(seed_key)
    seed = result.get("seed")
    if isinstance(seed, dict):
        return norm_id(seed.get("id") or seed.get("doi") or seed.get("arxiv_id"))
    return ""


def rank_papers(result):
    """按 溯源距离、边证据、被引度、年份、来源置信度 综合排序（非“高被引=高相关”）。"""
    papers = result["results"].setdefault("papers", [])
    edges = result["results"].setdefault("edges", [])
    seed = _seed_id(result)
    # 计算从种子出发的最短距离（方向感知 BFS）
    adj = {}
    for e in edges:
        adj.setdefault(e.get("source"), []).append(e.get("target"))
    dist = {}
    if seed:
        frontier = [seed]
        dist[seed] = 0
        qi = 0
        while qi < len(frontier):
            src = frontier[qi]
            qi += 1
            for t in adj.get(src, []):
                if t not in dist:
                    dist[t] = dist[src] + 1
                    frontier.append(t)
    indeg = {}
    outdeg = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        outdeg[s] = outdeg.get(s, 0) + 1
        indeg[t] = indeg.get(t, 0) + 1
    for p in papers:
        pid = norm_id(p.get("id") or p.get("doi") or p.get("arxiv_id"))
        d = dist.get(pid, 99)
        year = p.get("year") or p.get("publication_year") or 0
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = 0
        ev = p.get("evidence") or []
        conf = 0
        if isinstance(ev, list):
            for e in ev:
                if isinstance(e, dict) and e.get("quote"):
                    conf += 1
        elif isinstance(ev, dict) and ev.get("quote"):
            conf = 1
        score = (
            (40 if d == 0 else 15 if d == 1 else 8 if d == 2 else 3)
            + min(indeg.get(pid, 0), 10)
            + min(outdeg.get(pid, 0), 5)
            + min(year - 2000, 24) * 0.1
            + min(conf, 5)
        )
        p["rank_distance"] = d
        p["rank_indegree"] = indeg.get(pid, 0)
        p["rank_outdegree"] = outdeg.get(pid, 0)
        p["rank_score"] = round(score, 2)
    papers.sort(key=lambda p: (-p.get("rank_score", 0), str(p.get("title", ""))))
    return papers


def shortest_path(edges, src, dst):
    """有向图最短路径（BFS），返回 [src,...,dst] 或 None。"""
    adj = {}
    for e in edges:
        adj.setdefault(e.get("source"), []).append(e.get("target"))
    if src == dst:
        return [src]
    parent = {src: None}
    q = [src]
    qi = 0
    while qi < len(q):
        u = q[qi]
        qi += 1
        for v in adj.get(u, []):
            if v not in parent:
                parent[v] = u
                if v == dst:
                    path = [v]
                    while parent[path[-1]] is not None:
                        path.append(parent[path[-1]])
                    return list(reversed(path))
                q.append(v)
    return None


def build_paths(result):
    """为关键远端文献写逐跳最短路径（路径解释的确定性落地）。"""
    papers = result["results"].get("papers", [])
    edges = result["results"].get("edges", [])
    seed = _seed_id(result)
    if not seed:
        return []
    paths = []
    for p in papers:
        pid = norm_id(p.get("id") or p.get("doi") or p.get("arxiv_id"))
        d = p.get("rank_distance")
        if pid == seed or d is None or d <= 0:
            continue
        if d <= 2:  # 仅对可达的近端关键节点生成路径，避免噪声
            sp = shortest_path(edges, seed, pid)
            if sp and len(sp) >= 2:
                hops = []
                for i in range(len(sp) - 1):
                    hops.append({
                        "from": sp[i], "to": sp[i + 1],
                        "relation": "cites",
                        "direction": "source cites target",
                    })
                paths.append({
                    "start": seed, "end": pid, "length": len(sp) - 1,
                    "hops": hops,
                    "kind": "shortest_path",
                    "target_title": p.get("title"),
                })
    result["results"]["paths"] = paths
    return paths


def compute_graph_summary(result):
    papers = result["results"].get("papers", [])
    edges = result["results"].get("edges", [])
    ids = [norm_id(p.get("id") or p.get("doi") or p.get("arxiv_id")) for p in papers]
    ids = [i for i in ids if i]
    indeg = {}
    outdeg = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        outdeg[s] = outdeg.get(s, 0) + 1
        indeg[t] = indeg.get(t, 0) + 1
    hubs = sorted(ids, key=lambda i: -outdeg.get(i, 0))[:3]
    bridges = sorted(ids, key=lambda i: -(outdeg.get(i, 0) + indeg.get(i, 0)))[:3]
    hotspots = []
    for p in papers:
        pid = norm_id(p.get("id") or p.get("doi") or p.get("arxiv_id"))
        try:
            year = int(p.get("year") or p.get("publication_year") or 0)
        except (TypeError, ValueError):
            year = 0
        if year >= 2018 and indeg.get(pid, 0) > 0:
            hotspots.append(pid)
    hotspots = hotspots[:3]
    result["results"]["graph_summary"] = {
        "nodes": len(papers),
        "edges": len(edges),
        "layers": 1 + max((p.get("rank_distance", 0) for p in papers if p.get("rank_distance") is not None), default=0),
        "hub_nodes": hubs,
        "bridge_nodes": bridges,
        "recent_hotspots": hotspots,
    }


# --------------------------------------------------------------------------
# 渲染：report.md / papers.csv / edges.csv / citation_graph.svg
# --------------------------------------------------------------------------

def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def render_papers_csv(papers):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "title", "year", "authors", "doi", "arxiv_id", "venue",
                "rank_distance", "rank_score", "merged_from"])
    for p in papers:
        authors = p.get("authors")
        if isinstance(authors, list):
            authors = "; ".join(str(a) for a in authors)
        merged = ";".join(str(m.get("from_id", "")) for m in (p.get("merged") or []))
        w.writerow([
            _fmt(p.get("id")), _fmt(p.get("title")), _fmt(p.get("year")),
            _fmt(authors), _fmt(p.get("doi")), _fmt(p.get("arxiv_id")),
            _fmt(p.get("venue")), _fmt(p.get("rank_distance")),
            _fmt(p.get("rank_score")), merged,
        ])
    return buf.getvalue()


def render_edges_csv(edges):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["source", "target", "relation", "source_label", "target_label",
                "evidence_locator", "direction"])
    for e in edges:
        ev = e.get("evidence") or []
        loc = ""
        if isinstance(ev, list) and ev:
            loc = "; ".join(str(x.get("locator", "")) for x in ev if isinstance(x, dict))
        elif isinstance(ev, dict):
            loc = ev.get("locator", "")
        w.writerow([
            _fmt(e.get("source")), _fmt(e.get("target")), _fmt(e.get("relation", "cites")),
            _fmt(e.get("source_label")), _fmt(e.get("target_label")), loc,
            _fmt(e.get("direction", "source cites target")),
        ])
    return buf.getvalue()


def render_svg(papers, edges, seed_key=""):
    """确定性 SVG：种子居中，其余节点等距圆周分布，有向边画箭头。"""
    ids = []
    for p in papers:
        pid = norm_id(p.get("id") or p.get("doi") or p.get("arxiv_id"))
        if pid:
            ids.append(pid)
    ids = sorted(set(ids))
    seed = norm_id(seed_key)
    if seed and seed in ids:
        ids.remove(seed)
        ordered = [seed] + ids
    else:
        ordered = ids
    n = len(ordered)
    W = max(520, 120 + 70 * math.ceil(math.sqrt(n)))
    H = W
    cx, cy = W / 2, H / 2
    R = min(W, H) / 2 - 60 if n > 1 else 0
    pos = {}
    for i, pid in enumerate(ordered):
        if i == 0 and n == 1:
            pos[pid] = (cx, cy)
        elif i == 0:
            pos[pid] = (cx, cy)
        else:
            ang = 2 * math.pi * (i - 1) / (n - 1) if n > 1 else 0
            pos[pid] = (cx + R * math.cos(ang), cy + R * math.sin(ang))
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d">' % (W, H, W, H)
    )
    parts.append(
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" '
        'orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#333"/></marker></defs>'
    )
    parts.append('<rect width="100%" height="100%" fill="#ffffff"/>')
    # 边（先画线再画节点，避免遮挡箭头）
    for e in edges:
        s, t = norm_id(e.get("source")), norm_id(e.get("target"))
        if s in pos and t in pos:
            x1, y1 = pos[s]
            x2, y2 = pos[t]
            parts.append(
                '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                'stroke="#999" stroke-width="1" marker-end="url(#arrow)"/>' % (x1, y1, x2, y2)
            )
    # 节点
    for pid, (x, y) in pos.items():
        label = html.escape(pid[:14])
        parts.append('<circle cx="%.1f" cy="%.1f" r="14" fill="#eef3fb" stroke="#3b6fb5" stroke-width="1"/>' % (x, y))
        parts.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" dominant-baseline="central" '
            'font-size="8" fill="#222">%s</text>' % (x, y, label)
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render_report(result):
    L = []
    L.append("# 引文追踪报告")
    L.append("")
    L.append("- case_id: `%s`" % _fmt(result.get("case_id")))
    L.append("- status: `%s`" % _fmt(result.get("status")))
    L.append("- schema_version: `%s`" % _fmt(result.get("schema_version")))
    L.append("- research_question: %s" % (_fmt(result.get("research_question")) or "（未填写）"))
    L.append("- seed_key: `%s`" % _fmt(result.get("seed_key")))
    L.append("")
    L.append("## 契约对齐")
    L.append("")
    c = result.get("contract") or {}
    L.append("- 来源: `%s`" % _fmt(c.get("source")))
    L.append("- 已满足: `%s`" % ("是" if c.get("satisfied") else "否"))
    L.append("- 缺失键: %s" % (_fmt(c.get("missing")) or "无"))
    L.append("- 未填键: %s" % (_fmt(c.get("unfilled")) or "无"))
    L.append("")
    L.append("## 文献（papers）")
    L.append("")
    papers = result.get("results", {}).get("papers", [])
    if not papers:
        L.append("（无）")
    for p in papers:
        L.append("- `%s` — %s (%s)  [dist=%s, score=%s]" % (
            _fmt(p.get("id")), _fmt(p.get("title")), _fmt(p.get("year")),
            _fmt(p.get("rank_distance")), _fmt(p.get("rank_score"))))
        merged = p.get("merged") or []
        if merged:
            L.append("  - 合并自: %s" % ", ".join(_fmt(m.get("from_id")) for m in merged))
    L.append("")
    L.append("## 引用边（edges，source cites target）")
    L.append("")
    edges = result.get("results", {}).get("edges", [])
    if not edges:
        L.append("（无）")
    for e in edges:
        L.append("- `%s` → `%s` (%s)" % (_fmt(e.get("source")), _fmt(e.get("target")), _fmt(e.get("relation", "cites"))))
    L.append("")
    L.append("## 路径（paths）")
    L.append("")
    paths = result.get("results", {}).get("paths", [])
    if not paths:
        L.append("（无）")
    for pth in paths:
        chain = " → ".join(h.get("from", "") for h in pth.get("hops", [])) + " → " + pth.get("end", "")
        L.append("- %s (长度 %s)" % (chain, _fmt(pth.get("length"))))
    L.append("")
    L.append("## 局限 / 待人工 / 错误 / 降级")
    L.append("")
    for key, label in (("limitations", "limitations"), ("pending_human", "pending_human"),
                       ("errors", "errors"), ("degradations", "degradations")):
        L.append("### %s" % label)
        items = result.get(key) or []
        if not items:
            L.append("（无）")
        for it in items:
            L.append("- %s" % _fmt(it))
        L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 原子写与 finalize
# --------------------------------------------------------------------------

def atomic_write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def write_text(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def load_result(result_path):
    obj, err = read_json_file(result_path)
    if err is not None:
        raise ValueError("cannot read result.json: %s" % err)
    if not isinstance(obj, dict):
        raise ValueError("result.json is not an object")
    return obj


def finalize_artifacts(result, output_dir):
    """校验、重算契约与摘要、写全部可复核产物。返回产物路径列表。"""
    recompute_contract(result)
    rank_papers(result)
    build_paths(result)
    compute_graph_summary(result)
    ok, problems = validate(result)
    if problems:
        result.setdefault("errors", []).extend(
            ["finalize validation: %s" % p for p in problems if p not in result["errors"]]
        )
    result["runtime"]["updated_utc"] = now_utc()
    result["runtime"]["valid"] = ok
    result["runtime"]["problems"] = problems

    result_path = atomic_write_json(os.path.join(output_dir, "result.json"), result)
    artifacts = [result_path]
    papers = result.get("results", {}).get("papers", [])
    edges = result.get("results", {}).get("edges", [])
    seed_key = result.get("seed_key", "")

    report_path = write_text(os.path.join(output_dir, "report.md"), render_report(result))
    artifacts.append(report_path)

    csv_path = write_text(os.path.join(output_dir, "papers.csv"), render_papers_csv(papers))
    artifacts.append(csv_path)

    edges_path = write_text(os.path.join(output_dir, "edges.csv"), render_edges_csv(edges))
    artifacts.append(edges_path)

    svg_path = write_text(
        os.path.join(output_dir, "citation_graph.svg"),
        render_svg(papers, edges, seed_key),
    )
    artifacts.append(svg_path)
    return artifacts
