# -*- coding: utf-8 -*-
"""core_impl.py — 引文追踪 Skill 的核心实现（仅用 Python 标准库）。

子命令：
  bootstrap  扫描 /input，落盘 result.json 骨架与 work/state.json（先写合法 partial 再分析）
  record     增量追加 paper/edge/source/uncertainty/... 并写 change_log
  finalize   校验 result.json，生成 report.md / papers.csv / edges.csv / citation_graph.svg
  rank       确定性排序：距离优先，被引数仅作参考，不把"高被引"当"高相关"
  path       在引用边上做有向 BFS，输出逐跳证据链（可反向做前向追踪）

设计约定：
  - 所有入口保证退出码 0；任何异常都尝试留下最小合法 JSON，不崩溃。
  - 文本以 utf-8-sig 优先容错读取；单文件 >2MB 跳过并记 degradations。
  - 边方向统一为 source cites target；方向未知显式标 direction_unknown，绝不猜测。
  - 每次 record 都 append change_log（时间 + 内容 + 原因），实现"先骨架、后增量"。
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import math
import os
import re
import sys
from collections import deque

STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}
RELATIONS = {"cites", "direction_unknown"}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCAN_DEPTH = 3   # 输入目录扫描深度上限，避免误读深层缓存


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: str):
    """utf-8-sig / utf-8 / latin-1 依次容错；超限返回 None。"""
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError, OSError):
            continue
    return None


def read_json(path: str):
    txt = read_text(path)
    if txt is None:
        return None
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        return None


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _log(result, kind, content, reason=""):
    result.setdefault("change_log", []).append(
        {"time": now_iso(), "content": content, "reason": reason or ""}
    )


def minimal_result() -> dict:
    return {
        "case_id": None,
        "status": "input_error",
        "seed": None,
        "seed_key": None,
        "research_question": None,
        "results": {"papers": [], "edges": [], "clusters": [], "paths": [], "pruned": []},
        "sources": [],
        "uncertainty": [],
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {
            "started_at": now_iso(),
            "budget": {"seconds": 900, "model_requests": 64},
            "steps": {},
        },
    }


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def scan_input(input_dir: str):
    """返回 (文件相对路径清单, degradations 列表)。"""
    files, degradations = [], []
    if not os.path.isdir(input_dir):
        return files, [{"at": now_iso(), "issue": "input_dir_missing", "detail": input_dir}]
    for root, dirs, names in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel_root = os.path.relpath(root, input_dir)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if depth >= MAX_SCAN_DEPTH:
            dirs[:] = []
        for n in sorted(names):
            if n.startswith("."):
                continue
            p = os.path.join(root, n)
            if not os.path.isfile(p):
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            files.append({"path": os.path.relpath(p, input_dir), "abs": p, "size": size})
    return files, degradations


def default_for_value(v):
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    return None


def make_skeleton(case_data, task_md, inventory, input_dir) -> dict:
    case_id = None
    contract = {}
    if isinstance(case_data, dict):
        case_id = (case_data.get("case_id") or case_data.get("id")
                   or case_data.get("challenge_id") or case_data.get("name"))
        c = case_data.get("output_contract")
        if isinstance(c, dict):
            contract = c
        elif isinstance(c, str):
            parsed = None
            try:
                parsed = json.loads(c)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                contract = parsed

    result = minimal_result()
    result["case_id"] = case_id or None
    result["status"] = "partial"  # 正常 bootstrap 落盘的是 partial 骨架，再由 do_bootstrap 按材料情况调整
    # 回填 output_contract 全部字段（顶层 + 嵌套副本）；核心键优先，未知键不删。
    for k, v in contract.items():
        if k in result or k in ("output_contract",):
            continue
        result[k] = default_for_value(v)
    result["output_contract"] = dict(contract)

    # 从 task.md 首行 / 标题提取 research_question 的占位线索（不编造）
    q = None
    if isinstance(task_md, str) and task_md.strip():
        first_line = next((ln.strip() for ln in task_md.splitlines() if ln.strip()), "")
        q = first_line[:200] or None
    result["research_question"] = q
    result["runtime"]["steps"]["bootstrap"] = now_iso()
    result["runtime"]["input_files"] = [f["path"] for f in inventory]
    return result


def do_bootstrap(args) -> int:
    input_dir = args.input
    out_dir = args.output
    work_dir = args.work
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    inventory, degradations = scan_input(input_dir)
    case_data = None
    task_md = None
    for f in inventory:
        base = os.path.basename(f["path"])
        if base == "case.json":
            case_data = read_json(f["abs"])
            if case_data is None:
                degradations.append(
                    {"at": now_iso(), "issue": "bad_json_case", "detail": f["path"]}
                )
        elif base == "task.md":
            task_md = read_text(f["abs"])
            if task_md is None:
                degradations.append(
                    {"at": now_iso(), "issue": "unreadable_task_md", "detail": f["path"]}
                )

    result = make_skeleton(case_data, task_md, inventory, input_dir)
    result["degradations"].extend(degradations)

    # 诚实状态判定
    if not os.path.isdir(input_dir) or not inventory:
        result["status"] = "insufficient_evidence"
    elif case_data is None:
        result["status"] = "input_error"

    _log(result, "bootstrap", "落盘 partial 结果骨架", "先写合法骨架再进入分析，满足前 3 分钟落盘要求")
    write_json(os.path.join(out_dir, "result.json"), result)
    write_json(
        os.path.join(work_dir, "state.json"),
        {
            "input_dir": input_dir,
            "inventory": [{"path": f["path"], "size": f["size"]} for f in inventory],
            "last_modified": now_iso(),
            "steps": {"bootstrap": now_iso()},
        },
    )
    return 0


# --------------------------------------------------------------------------- #
# record（增量改进）
# --------------------------------------------------------------------------- #
KIND_TARGET = {
    "paper": ["results", "papers"],
    "edge": ["results", "edges"],
    "cluster": ["results", "clusters"],
    "path": ["results", "paths"],
    "pruned": ["results", "pruned"],
    "source": ["sources"],
    "uncertainty": ["uncertainty"],
    "limitation": ["limitations"],
    "pending": ["pending_human"],
    "error": ["errors"],
    "degradation": ["degradations"],
}


def _apply_seed(result, payload):
    if not isinstance(payload, dict):
        return
    for k in ("seed", "seed_key", "research_question"):
        if k in payload and payload[k] not in (None, ""):
            result[k] = payload[k]


def _append_at(result, path, payload):
    container = result
    for k in path[:-1]:
        container = container.setdefault(k, {})
    if not isinstance(payload, (dict, list)):
        payload = {"value": payload}
    container.setdefault(path[-1], []).append(payload)


def _touch_state(args):
    sp = os.path.join(args.work, "state.json")
    st = read_json(sp)
    if isinstance(st, dict):
        st["last_modified"] = now_iso()
        st.setdefault("steps", {})["record"] = now_iso()
        write_json(sp, st)


def do_record(args) -> int:
    rpath = os.path.join(args.output, "result.json")
    result = read_json(rpath)
    if not isinstance(result, dict):
        result = minimal_result()

    raw = args.json
    if raw == "-" or raw in (None, ""):
        raw = sys.stdin.read()
    payload = None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        result.setdefault("errors", []).append(
            {"at": now_iso(), "issue": "bad_record_json", "detail": (raw or "")[:200]}
        )
        _log(result, "record", "record 输入 JSON 解析失败", "坏 JSON 已记录，不崩溃")
        write_json(rpath, result)
        return 0

    kind = args.kind
    if kind == "seed":
        _apply_seed(result, payload)
        _log(result, "seed", "写入种子标识 seed/seed_key/research_question", args.reason)
    elif kind == "status":
        val = payload.get("status") if isinstance(payload, dict) else payload
        if val in STATUS_ENUM:
            result["status"] = val
            _log(result, "status", f"状态置为 {val}", args.reason)
        else:
            result.setdefault("errors", []).append(
                {"at": now_iso(), "issue": "bad_status", "detail": str(val)[:200]}
            )
            _log(result, "status", "非法 status 被拒绝", args.reason)
    elif kind in KIND_TARGET:
        _append_at(result, KIND_TARGET[kind], payload)
        _log(result, kind, f"{kind} 追加一条记录", args.reason)
    else:
        result.setdefault("errors", []).append(
            {"at": now_iso(), "issue": "unknown_kind", "detail": kind}
        )
        _log(result, "record", f"未知 kind={kind}", "未知类型原样记录，不崩溃")

    result["runtime"]["steps"]["record"] = now_iso()
    write_json(rpath, result)
    _touch_state(args)
    return 0


# --------------------------------------------------------------------------- #
# 图 helper：去重 / 排序 / 最短路径（确定性）
# --------------------------------------------------------------------------- #
_CITE_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _norm_key(s):
    if not isinstance(s, str):
        return ""
    return _CITE_RE.sub("", s.strip().lower())


def _first_author_surname(authors) -> str:
    if isinstance(authors, str):
        first = next((x for x in re.split(r"[;,]", authors) if x.strip()), "")
    elif isinstance(authors, list) and authors:
        first = str(authors[0])
    else:
        return ""
    parts = [p for p in first.split() if p]
    return _norm_key(parts[-1]) if parts else ""


def paper_keys(p):
    keys = []
    doi = p.get("doi") if isinstance(p, dict) else None
    arx = (p.get("arxiv") or p.get("arXiv") or p.get("arxiv_id")) if isinstance(p, dict) else None
    title = p.get("title") if isinstance(p, dict) else None
    year = p.get("year") if isinstance(p, dict) else None
    authors = p.get("authors") if isinstance(p, dict) else None
    if doi:
        keys.append(("doi", _norm_key(str(doi))))
    if arx:
        keys.append(("arxiv", _norm_key(str(arx))))
    t = _norm_key(str(title)) if title else ""
    if t and year:
        keys.append(("title_first_author_year", f"{t}|{_first_author_surname(authors)}|{year}"))
    if t:
        keys.append(("title", t))
    return keys


def _merge_two(a, b):
    out = dict(a)
    for k, v in b.items():
        if k in ("id", "merge_evidence"):
            continue
        if out.get(k) in (None, "", [], {}):
            out[k] = v
        elif isinstance(out.get(k), list) and isinstance(v, list):
            merged = list(out[k])
            for item in v:
                if item not in merged:
                    merged.append(item)
            out[k] = merged
        elif isinstance(out.get(k), dict) and isinstance(v, dict):
            d = dict(out[k])
            d.update(v)
            out[k] = d
    return out


def dedupe_papers(papers):
    """按共同身份并查集合并。doi/arxiv 为强键，title+第一作者姓+年份 为中键；
    title-only 不自动合并，只记 uncertainty（避免同名不同文误合并）。返回
    (merged_papers, id_remap, merge_evidence, uncertainty_notes)。"""
    papers = [dict(p) for p in papers]
    n = len(papers)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    strong_map = {}
    medium_map = {}
    title_map = {}
    for i, p in enumerate(papers):
        for kt, kv in paper_keys(p):
            if kt in ("doi", "arxiv"):
                if (kt, kv) in strong_map:
                    union(i, strong_map[(kt, kv)])
                else:
                    strong_map[(kt, kv)] = i
            elif kt == "title_first_author_year":
                medium_map.setdefault(kv, []).append(i)
            else:
                title_map.setdefault(kv, []).append(i)

    for inds in medium_map.values():
        for j in inds[1:]:
            union(inds[0], j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    merged, merge_evidence, id_remap = [], [], {}
    unc = []
    # title-only 候选（跨并查集分组仍同名）→ 记录 uncertainty
    for tval, inds in title_map.items():
        roots = {find(i) for i in inds}
        if len(roots) > 1:
            unc.append(
                {
                    "kind": "title_only_duplicate_candidate",
                    "title_key": tval,
                    "ids": [papers[i].get("id") for i in inds],
                    "note": "仅标题相同，未自动合并，需人工核验同名不同文",
                }
            )

    order = sorted(groups.items(), key=lambda kv: min(kv[1]))
    for _, idxs in order:
        if len(idxs) == 1:
            merged.append(papers[idxs[0]])
            id_remap[papers[idxs[0]].get("id")] = papers[idxs[0]].get("id")
            continue
        keep = papers[idxs[0]]
        for j in idxs[1:]:
            keep = _merge_two(keep, papers[j])
        members = [papers[i].get("id") for i in idxs]
        keep.setdefault("merge_evidence", []).append(
            {
                "members": members,
                "keys": [
                    [f"{kt}:{kv}" for kt, kv in paper_keys(papers[i]) if kv]
                    for i in idxs
                ],
                "canonical": keep.get("id"),
            }
        )
        merge_evidence.append(
            {
                "members": members,
                "canonical": keep.get("id"),
                "keys": [
                    [f"{kt}:{kv}" for kt, kv in paper_keys(papers[i]) if kv]
                    for i in idxs
                ],
            }
        )
        merged.append(keep)
        for i in idxs:
            id_remap[papers[i].get("id")] = keep.get("id")
    return merged, id_remap, merge_evidence, unc


def build_adj(edges, reverse=False):
    adj = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        if reverse:
            s, t = e.get("target"), e.get("source")
        else:
            s, t = e.get("source"), e.get("target")
        if s and t:
            adj.setdefault(s, set()).add(t)
    return adj


def shortest_path(edges, source, target, reverse=False):
    adj = build_adj(edges, reverse=reverse)
    if source == target:
        return [source]
    if source not in adj:
        return None
    parent = {source: None}
    q = deque([source])
    while q:
        u = q.popleft()
        if u == target:
            break
        for v in sorted(adj.get(u, ())):
            if v not in parent:
                parent[v] = u
                q.append(v)
    if target not in parent:
        return None
    chain, cur = [], target
    while cur is not None:
        chain.append(cur)
        cur = parent[cur]
    chain.reverse()
    return chain


def explain_chain(edges, chain):
    hops = []
    for a, b in zip(chain, chain[1:]):
        matched = None
        for e in edges:
            if not isinstance(e, dict):
                continue
            if e.get("source") == a and e.get("target") == b:
                matched = dict(e)
                break
            if e.get("source") == b and e.get("target") == a:
                matched = dict(e)
                matched["reverse_lookup"] = True
                break
        hops.append(
            {
                "from": a,
                "to": b,
                "citing": matched.get("source") if matched else a,
                "cited": matched.get("target") if matched else b,
                "relation": matched.get("relation", "direction_unknown") if matched else "not_reported",
                "reverse_lookup": bool(matched and matched.get("reverse_lookup")),
                "evidence": matched.get("evidence", []) if matched else [],
            }
        )
    return hops


def rank_papers(papers, edges, seed):
    """距离优先（越近越相关），被引数与年份仅作次要信号；返回排序后的 id 列表。"""
    adj = build_adj(edges, reverse=False)
    rev = build_adj(edges, reverse=True)

    dist = {}
    if seed:
        q = deque([seed])
        dist[seed] = 0
        while q:
            u = q.popleft()
            nd = dist[u] + 1
            for v in sorted(adj.get(u, ()) | rev.get(u, ())):
                if v not in dist:
                    dist[v] = nd
                    q.append(v)

    scored = []
    for p in papers:
        pid = p.get("id")
        d = dist.get(pid, 99)
        in_deg = len({e.get("source") for e in edges if isinstance(e, dict) and e.get("target") == pid})
        year = p.get("year")
        try:
            year_num = int(year)
        except (TypeError, ValueError):
            year_num = 1900
        try:
            conf = float(p.get("confidence"))
        except (TypeError, ValueError):
            conf = 0.7 if (p.get("evidence") or []) else 0.5
        # 距离权重最大，被引数与新颖度/置信度作小权重；明确不是"高被引=高相关"
        score = (
            100.0 / (1.0 + d)
            + min(in_deg, 20) * 0.8
            + max(0, (year_num - 1900) / 100.0) * 3.0
            + conf * 5.0
        )
        scored.append((score, pid))
    scored.sort(key=lambda x: (-x[0], str(x[1])))
    return [pid for _, pid in scored], dist


# --------------------------------------------------------------------------- #
# finalize
# --------------------------------------------------------------------------- #
def validate_result(r):
    problems = []
    if not r.get("case_id"):
        problems.append("case_id 缺失")
    if r.get("status") not in STATUS_ENUM:
        problems.append("status 不在枚举内")
    for i, p in enumerate(r.get("results", {}).get("papers", [])):
        if not isinstance(p, dict) or not p.get("id"):
            problems.append(f"papers[{i}] 缺 id")
    for i, e in enumerate(r.get("results", {}).get("edges", [])):
        if not isinstance(e, dict):
            problems.append(f"edges[{i}] 非对象")
            continue
        if not (e.get("source") and e.get("target")):
            problems.append(f"edges[{i}] 缺 source/target")
        if e.get("relation") not in RELATIONS:
            problems.append(f"edges[{i}] relation 非法（应为 cites 或 direction_unknown）")
    for i, s in enumerate(r.get("sources", [])):
        if not isinstance(s, dict) or not (s.get("locator") or s.get("quote")):
            problems.append(f"sources[{i}] 缺 locator/quote")
    return problems


def _csv_escape(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    return str(v)


def write_report(r, outdir):
    papers = r.get("results", {}).get("papers", [])
    edges = r.get("results", {}).get("edges", [])
    lines = []
    lines.append(f"# 引文追踪报告 — {r.get('case_id') or '(未提供)'}")
    lines.append("")
    lines.append(f"- status: `{r.get('status')}`")
    lines.append(f"- seed: `{r.get('seed')}`")
    lines.append(f"- seed_key: `{r.get('seed_key')}`")
    lines.append(f"- research_question: {r.get('research_question') or '(未给出)'}")
    lines.append(f"- 生成时间: {now_iso()}")
    lines.append("")
    lines.append(f"## 文献集（去重后，共 {len(papers)} 条）")
    lines.append("")
    if papers:
        lines.append("| id | title | year | venue | doi | level | confidence |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in papers:
            lines.append(
                "| {} | {} | {} | {} | {} | {} | {} |".format(
                    _csv_escape(p.get("id")), _csv_escape(p.get("title")),
                    _csv_escape(p.get("year")), _csv_escape(p.get("venue")),
                    _csv_escape(p.get("doi")), _csv_escape(p.get("level")),
                    _csv_escape(p.get("confidence")),
                )
            )
    else:
        lines.append("(无文献)")
    lines.append("")
    lines.append(f"## 引用边（source cites target，共 {len(edges)} 条）")
    lines.append("")
    if edges:
        lines.append("| source | target | relation | evidence |")
        lines.append("|---|---|---|---|")
        for e in edges:
            ev = e.get("evidence") or []
            ev_txt = "; ".join(
                f"{x.get('locator')}: {str(x.get('quote'))[:80]}" if isinstance(x, dict) else str(x)
                for x in ev[:3]
            )
            lines.append(
                "| {} | {} | {} | {} |".format(
                    _csv_escape(e.get("source")), _csv_escape(e.get("target")),
                    _csv_escape(e.get("relation")), _csv_escape(ev_txt),
                )
            )
    else:
        lines.append("(无边)")
    for sec, data in (
        ("不确定性 / 冲突 / 覆盖缺口", r.get("uncertainty", [])),
        ("待人工确认", r.get("pending_human", [])),
        ("限制", r.get("limitations", [])),
        ("错误", r.get("errors", [])),
        ("降级", r.get("degradations", [])),
    ):
        lines.append("")
        lines.append(f"## {sec}")
        lines.append("")
        if data:
            for item in data:
                lines.append(f"- {_csv_escape(item)}")
        else:
            lines.append("(无)")
    lines.append("")
    lines.append("## change_log")
    lines.append("")
    for c in r.get("change_log", []):
        lines.append(f"- {c.get('time')} | {c.get('content')} | {c.get('reason')}")
    write_text(os.path.join(outdir, "report.md"), "\n".join(lines) + "\n")


def write_csvs(r, outdir):
    papers = r.get("results", {}).get("papers", [])
    edges = r.get("results", {}).get("edges", [])
    pcols = ["id", "title", "authors", "year", "venue", "doi", "arxiv", "level", "confidence", "source"]
    with open(os.path.join(outdir, "papers.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(pcols)
        for p in papers:
            w.writerow([_csv_escape(p.get(c)) if isinstance(p, dict) else "" for c in pcols])

    ecols = ["source", "target", "relation", "source_label", "target_label",
             "evidence_locator", "evidence_quote"]
    with open(os.path.join(outdir, "edges.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(ecols)
        for e in edges:
            if not isinstance(e, dict):
                continue
            ev = (e.get("evidence") or [{}])
            ev = ev[0] if isinstance(ev, list) and ev and isinstance(ev[0], dict) else {}
            w.writerow([
                _csv_escape(e.get("source")), _csv_escape(e.get("target")),
                _csv_escape(e.get("relation")), _csv_escape(e.get("source_label")),
                _csv_escape(e.get("target_label")),
                _csv_escape(ev.get("locator") if isinstance(ev, dict) else None),
                _csv_escape(ev.get("quote") if isinstance(ev, dict) else None),
            ])


def _xml_esc(s):
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_svg(r, outdir, dist=None):
    papers = r.get("results", {}).get("papers", [])
    edges = r.get("results", {}).get("edges", [])
    outpath = os.path.join(outdir, "citation_graph.svg")
    if not papers:
        write_text(
            outpath,
            '<svg xmlns="http://www.w3.org/2000/svg" width="460" height="120" '
            'viewBox="0 0 460 120"><rect width="460" height="120" fill="#ffffff"/>'
            '<text x="20" y="40" font-family="monospace" font-size="14">(no papers)</text></svg>',
        )
        return
    W = H = 900
    cx = cy = W / 2.0
    seed = r.get("seed") or papers[0].get("id")
    levels = {}
    for p in papers:
        lvl = p.get("level", 1)
        if not isinstance(lvl, int) or lvl < 1:
            lvl = 1
        levels.setdefault(lvl, []).append(p.get("id"))
    pos = {seed: (cx, cy)}
    seen = {seed}
    for lvl in sorted(levels):
        ids = sorted([x for x in levels[lvl] if x not in seen], key=str)
        n = len(ids)
        radius = 130.0 * lvl
        for k, pid in enumerate(ids):
            ang = 2 * math.pi * k / max(n, 1) - math.pi / 2
            pos[pid] = (cx + radius * math.cos(ang), cy + radius * math.sin(ang))
            seen.add(pid)
    # 未在 level 分组中的 id
    for p in papers:
        pid = p.get("id")
        if pid not in seen:
            pos[pid] = (cx + 500, cy)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="8" refY="3" '
        'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#555"/></marker></defs>',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
    ]
    for e in edges:
        if not isinstance(e, dict):
            continue
        s, t = e.get("source"), e.get("target")
        if s in pos and t in pos and s != t:
            x1, y1 = pos[s]
            x2, y2 = pos[t]
            parts.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#555" '
                'stroke-width="1.2" marker-end="url(#arr)"/>'
            )
    for p in papers:
        pid = p.get("id")
        if pid not in pos:
            continue
        x, y = pos[pid]
        fill = "#3b7dd8" if pid == seed else ("#e8a33d" if (dist or {}).get(pid, 99) == 1 else "#9aa5b1")
        parts.append(f'<circle cx="{x}" cy="{y}" r="16" fill="{fill}" stroke="#333" stroke-width="1"/>')
        label = _xml_esc(str(pid)[:14])
        parts.append(
            f'<text x="{x}" y="{y + 4}" font-family="monospace" font-size="10" '
            f'text-anchor="middle" fill="#111">{label}</text>'
        )
    parts.append("</svg>")
    write_text(outpath, "\n".join(parts))


def do_finalize(args) -> int:
    rpath = os.path.join(args.output, "result.json")
    result = read_json(rpath)
    if not isinstance(result, dict):
        result = minimal_result()
        result["status"] = "input_error"
    result.setdefault("results", {})
    papers = result["results"].setdefault("papers", [])
    edges = result["results"].setdefault("edges", [])

    # 确定性去重（强键/中键），保留 pre-merge 与 id_remap 以便复核
    if papers:
        merged, id_remap, merge_ev, unc = dedupe_papers(papers)
        if len(merged) < len(papers) or merge_ev or unc:
            result["results"]["papers_pre_merge"] = papers
            result["results"]["papers"] = merged
            if id_remap:
                result["results"]["id_remap"] = id_remap
            for u in unc:
                result.setdefault("uncertainty", []).append(u)
            if merge_ev:
                _log(
                    result, "finalize",
                    f"身份合并 {len(merge_ev)} 组，id_remap 已写入",
                    "DOI/arXiv 强键与 title+姓名+年份 中键合并，title-only 仅记候选",
                )
            papers = merged

    problems = validate_result(result)
    for p_ in problems:
        result.setdefault("errors", []).append({"at": now_iso(), "issue": "validate", "detail": p_})
    result["runtime"]["steps"]["finalize"] = now_iso()
    result.setdefault("limitations", [])
    if not papers:
        result["limitations"].append(
            {"reason": "no_papers", "note": "无文献记录；若确无材料，status 应为 insufficient_evidence"}
        )

    # 排序（确定性，供复核），写入 results.ranking
    if papers:
        ranking, dist = rank_papers(papers, edges, result.get("seed"))
        result["results"]["ranking"] = ranking
    else:
        dist = {}

    _log(result, "finalize", "校验并生成报告/CSV/SVG", "确定性产出，任何校验问题仅记 errors 不中断")
    write_json(rpath, result)
    write_report(result, args.output)
    write_csvs(result, args.output)
    write_svg(result, args.output, dist)
    return 0


# --------------------------------------------------------------------------- #
# rank / path 子命令
# --------------------------------------------------------------------------- #
def do_rank(args) -> int:
    result = read_json(os.path.join(args.output, "result.json"))
    if not isinstance(result, dict):
        print(json.dumps({"ranking": [], "error": "no result.json"}, ensure_ascii=False))
        return 0
    papers = result.get("results", {}).get("papers", [])
    edges = result.get("results", {}).get("edges", [])
    ranking, dist = rank_papers(papers, edges, result.get("seed"))
    out = {
        "ranking": ranking,
        "distances": dist,
        "note": "距离优先；被引数、年份、置信度为次要信号，非'高被引=高相关'",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def do_path(args) -> int:
    result = read_json(os.path.join(args.output, "result.json"))
    edges = (result or {}).get("results", {}).get("edges", [])
    chain = shortest_path(edges, args.source, args.target, reverse=args.reverse)
    if chain is None:
        out = {
            "source": args.source,
            "target": args.target,
            "reverse": args.reverse,
            "reachable": False,
            "note": "无路径（区别直接引用与间接可达；远端不可达明确标注）",
        }
    else:
        out = {
            "source": args.source,
            "target": args.target,
            "reverse": args.reverse,
            "reachable": True,
            "chain": chain,
            "hops": explain_chain(edges, chain),
        }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------- #
# argparse / main
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="引文追踪 Skill 核心脚本")
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("bootstrap", "finalize", "rank"):
        sp = sub.add_parser(name)
        if name != "rank":
            sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        if name == "bootstrap":
            sp.add_argument("--work", default="/app/work")

    rec = sub.add_parser("record")
    rec.add_argument("--kind", required=True, help="paper|edge|cluster|path|pruned|source|uncertainty|limitation|pending|error|degradation|seed|status")
    rec.add_argument("--json", default="-", help="JSON 载荷；默认从 stdin 读取")
    rec.add_argument("--reason", default="", help="change_log 原因")
    rec.add_argument("--output", default="/app/output")
    rec.add_argument("--work", default="/app/work")

    pth = sub.add_parser("path")
    pth.add_argument("--source", required=True)
    pth.add_argument("--target", required=True)
    pth.add_argument("--reverse", action="store_true", help="反向 BFS（前向追踪：谁 cites 种子）")
    pth.add_argument("--output", default="/app/output")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "bootstrap":
            return do_bootstrap(args)
        if args.command == "record":
            return do_record(args)
        if args.command == "finalize":
            return do_finalize(args)
        if args.command == "rank":
            return do_rank(args)
        if args.command == "path":
            return do_path(args)
        return 0
    except Exception as exc:  # noqa: BLE001 —— 兜底：任何异常都不崩溃、不留空文件
        try:
            out = getattr(args, "output", None) or "/app/output"
            os.makedirs(out, exist_ok=True)
            rpath = os.path.join(out, "result.json")
            if not os.path.exists(rpath):
                write_json(rpath, minimal_result())
        except Exception:  # noqa: BLE001
            pass
        print(f"[error] {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())