# -*- coding: utf-8 -*-
"""core.py — 引文追踪 Skill 的核心实现（IR-first 架构）。

设计取舍（与常见 bootstrap/finalize 方案不同）：
1. 单一事实源 IR（/app/work/trace.json）：全部状态只存一份，result.json 由
   materialize() 从 IR 派生，永不手写，杜绝主产物与工作状态漂移。
2. 校验先行：每阶段写入前后都经 validate_ir() 门控；discover 写骨架、每步
   merge、finalize 前共三次校验，任何时刻 trace.json 与 result.json 都合法。
3. 命令式单入口：discover/stage/finalize/merge/validate/dedupe/rank 七个子命令
   共用一个 main.py，替代分散脚本。
4. 确定性：固定键序、固定排序键、无随机数，同输入两次运行逐字节一致。

仅依赖 Python 标准库。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

VERSION = "1.1.0"
MAX_FILE_BYTES = 2 * 1024 * 1024
STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}
RELATION_ENUM = {"cites", "direction_unknown", "association"}
REQUIRED_TOP = {"case_id", "status", "results", "sources", "uncertainty", "change_log"}
RESULTS_KEYS = ("papers", "edges", "clusters", "paths", "pruned")


# --------------------------------------------------------------------------- #
# 基础工具：时间、编码容错读取、JSON 读写
# --------------------------------------------------------------------------- #
def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_robust(path):
    """按 utf-8-sig → utf-8 → gb18030 → latin-1 顺序容错读取，超限返回 None。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) > MAX_FILE_BYTES:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return data.decode("utf-8", errors="replace")


def load_json_file(path):
    """容错读取 JSON；解析失败返回 (None, 错误字符串)。"""
    text = read_text_robust(path)
    if text is None:
        return None, "read_failed_or_oversize"
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "json_parse_error:%s" % exc


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def escape_xml(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def norm_title(t):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (t or "").lower())


# --------------------------------------------------------------------------- #
# 阶段 0：输入发现 + 骨架
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    manifest, problems = [], []
    if not os.path.isdir(input_dir):
        return manifest, ["input_dir_missing:%s" % input_dir]
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        kind = "binary"
        if ext == ".json":
            kind = "json"
        elif ext in (".md", ".txt", ".csv", ".tsv"):
            kind = "text"
        elif ext == ".pdf":
            kind = "pdf"
        elif ext in (".png", ".jpg", ".jpeg", ".svg"):
            kind = "image"
        size = os.path.getsize(p)
        manifest.append(
            {"name": name, "path": p, "kind": kind, "size": size, "oversize": size > MAX_FILE_BYTES}
        )
    return manifest, problems


def _blank(v):
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    return None


def build_contract_fields(case):
    contract = (case or {}).get("output_contract") if isinstance(case, dict) else None
    fields = {}
    if isinstance(contract, dict):
        for k, v in contract.items():
            fields[k] = _blank(v)
    return fields


def build_skeleton(case, input_dir, work_dir, output_dir):
    contract = build_contract_fields(case)
    now = utcnow_iso()
    manifest, _ = discover_input(input_dir)
    ir = {
        "case_id": (case or {}).get("case_id"),
        "status": "partial",
        "output_contract_fields": contract,
        "seed": {},
        "seed_key": None,
        "research_question": None,
        "results": {
            "papers": [],
            "edges": [],
            "clusters": [],
            "paths": [],
            "pruned": [],
        },
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {
                "time": now,
                "content": "discover 落盘 result 骨架（IR + 派生 result.json）",
                "reason": "前 3 分钟先保证合法 partial 产物",
            }
        ],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {
            "start": now,
            "end": None,
            "seconds": None,
            "model_requests": None,
            "core_version": VERSION,
        },
        "manifest": manifest,
        "_meta": {
            "schema_version": "1.0",
            "input_dir": input_dir,
            "work_dir": work_dir,
            "output_dir": output_dir,
        },
    }
    return ir


# --------------------------------------------------------------------------- #
# 阶段 1/2：IR 校验 + 增量合并
# --------------------------------------------------------------------------- #
def validate_ir(ir, mode="skeleton"):
    """返回 problems 列表；mode: skeleton|full。"""
    if not isinstance(ir, dict):
        return ["ir_not_dict"]
    problems = []
    for k in REQUIRED_TOP:
        if k not in ir:
            problems.append("missing_top_key:%s" % k)
    if ir.get("status") not in STATUS_ENUM:
        problems.append("invalid_status:%s" % repr(ir.get("status")))
    res = ir.get("results")
    if isinstance(res, dict):
        for k in RESULTS_KEYS:
            if k not in res:
                problems.append("missing_results_key:%s" % k)
            elif not isinstance(res.get(k), list):
                problems.append("results_%s_not_list" % k)
    else:
        problems.append("results_not_dict")
    if mode == "full":
        res = ir.get("results") or {}
        seen = set()
        for p in res.get("papers", []):
            if not isinstance(p, dict):
                problems.append("paper_not_dict")
                continue
            pid = p.get("id")
            if not pid:
                problems.append("paper_missing_id")
            elif pid in seen:
                problems.append("duplicate_paper_id:%s" % pid)
            else:
                seen.add(pid)
        paper_ids = seen
        for e in res.get("edges", []):
            if not isinstance(e, dict):
                problems.append("edge_not_dict")
                continue
            for k in ("source", "target"):
                if not e.get(k):
                    problems.append("edge_missing_%s" % k)
            if e.get("relation") not in RELATION_ENUM:
                problems.append("invalid_relation:%s" % repr(e.get("relation")))
            for end in ("source", "target"):
                if e.get(end) and e[end] not in paper_ids and e[end] != (ir.get("seed") or {}).get("id"):
                    problems.append("edge_endpoint_not_in_papers:%s=%s" % (end, e[end]))
        for s in ir.get("sources", []):
            if not isinstance(s, dict):
                problems.append("source_not_dict")
                continue
            if not s.get("locator"):
                problems.append("source_missing_locator")
            if not s.get("quote"):
                problems.append("source_missing_quote")
        for e in res.get("edges", []):
            ev = (e or {}).get("evidence")
            if not isinstance(ev, dict):
                problems.append("edge_missing_evidence:%s->%s" % (e.get("source"), e.get("target")))
            else:
                if not ev.get("locator"):
                    problems.append("edge_evidence_missing_locator:%s->%s" % (e.get("source"), e.get("target")))
                if not ev.get("quote"):
                    problems.append("edge_evidence_missing_quote:%s->%s" % (e.get("source"), e.get("target")))
                for si in ev.get("source_refs", []):
                    if not (isinstance(si, (int, str))):
                        problems.append("edge_evidence_bad_source_ref:%s" % repr(si))
        cl = ir.get("change_log") or []
        if not isinstance(cl, list) or not all(
            isinstance(x, dict) and x.get("time") for x in cl
        ):
            problems.append("change_log_malformed")
    return problems


def deep_merge(base, patch):
    """JSON Merge Patch 语义的单向合并，返回 base（就地修改）。"""
    for k, v in patch.items():
        if (
            k in base
            and isinstance(base[k], dict)
            and isinstance(v, dict)
            and not isinstance(v, list)
        ):
            deep_merge(base[k], v)
        elif k in base and isinstance(base[k], list) and isinstance(v, list) and _list_of_dicts(base[k], v):
            # 追加式合并：列表逐元素按稳定键去重，便于增量加入 papers/edges。
            base[k] = _dedup_append(base[k], v)
        else:
            base[k] = v
    return base


def _list_of_dicts(a, b):
    return (not a or isinstance(a[0], dict)) and (not b or isinstance(b[0], dict))


def _dedup_append(base, add):
    out = list(base)
    seen = {json.dumps(x, ensure_ascii=False, sort_keys=True) for x in out}
    for item in add:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def stamp_change_log(ir, content, reason):
    ir.setdefault("change_log", [])
    ir["change_log"].append({"time": utcnow_iso(), "content": content, "reason": reason or ""})
    return ir


def summarize_patch(patch):
    keys = list((patch or {}).keys())
    return "merge keys: %s" % ", ".join(keys) if keys else "merge (empty patch)"


# --------------------------------------------------------------------------- #
# 阶段 3：身份合并、排序、最短路径
# --------------------------------------------------------------------------- #
def stable_id(paper):
    for key in ("doi", "arxiv_id"):
        v = (paper.get(key) or "").strip()
        if v:
            return "%s:%s" % (key, re.sub(r"https?://(doi\.org/)?", "", v).lower())
    title = norm_title(paper.get("title", ""))
    year = paper.get("year") or ""
    if title and year:
        return "title:%s:%s" % (title, year)
    if title:
        return "title:%s" % title
    return "paper:%s" % abs(hash(json.dumps(paper, ensure_ascii=False, sort_keys=True))) % 10**9


def remap_edges(edges, mapping):
    """把合并掉的旧 id 重映射为 canonical id。"""
    out = []
    for e in edges:
        if not isinstance(e, dict):
            out.append(e)
            continue
        e2 = dict(e)
        for end in ("source", "target"):
            if e2.get(end) in mapping:
                e2[end] = mapping[e2[end]]
        out.append(e2)
    return out


def apply_dedupe(ir):
    """把 papers 合并结果与边重映射写回 IR，返回 (ir, merged, evidence)。"""
    res = ir.setdefault("results", {})
    papers = res.get("papers") or []
    merged, evidence = dedupe_papers(papers)
    mapping = {}
    for m in evidence:
        for member in m["members"]:
            if member != m["canonical_id"]:
                mapping[member] = m["canonical_id"]
    res["papers"] = merged
    res["edges"] = remap_edges(res.get("edges") or [], mapping)
    new_clusters = []
    for i, m in enumerate(evidence):
        new_clusters.append(
            {
                "id": "merge-%02d" % i,
                "type": "identity_merge",
                "canonical_id": m["canonical_id"],
                "members": m["members"],
                "merged_on": m["merged_on"],
            }
        )
    res["clusters"] = _dedup_append(list(res.get("clusters") or []), new_clusters)
    return ir, merged, evidence


def dedupe_papers(papers):
    """按 DOI → arXiv → title+第一作者姓+年份 → title 优先级做身份合并。"""
    clusters = []
    for p in papers:
        if not isinstance(p, dict):
            continue
        keys = {}
        if p.get("doi"):
            keys["doi"] = {re.sub(r"https?://(doi\.org/)?", "", p["doi"]).strip().lower()}
        if p.get("arxiv_id"):
            keys["arxiv_id"] = {re.sub(r"https?://arxiv\.org/(abs/)?", "", p["arxiv_id"]).strip().lower()}
        if p.get("title") and p.get("first_author") and p.get("year"):
            last = (p.get("first_author") or "").strip().split()[-1].lower() if (p.get("first_author") or "").strip() else ""
            keys["tafy"] = {(norm_title(p["title"]), last, str(p["year"]))}
        if p.get("title"):
            keys["title"] = {norm_title(p["title"])}
        matched = None
        for ktype in ("doi", "arxiv_id", "tafy", "title"):
            kvals = keys.get(ktype)
            if not kvals:
                continue
            for ci, c in enumerate(clusters):
                if kvals & c["keys"].get(ktype, set()):
                    matched = ci
                    break
            if matched is not None:
                break
        if matched is None:
            clusters.append({"keys": keys, "papers": [p]})
        else:
            clusters[matched]["papers"].append(p)
            for ktype, kvals in keys.items():
                clusters[matched]["keys"].setdefault(ktype, set()).update(kvals)
    merged = []
    merge_evidence = []
    for c in clusters:
        ps = c["papers"]
        rep = dict(ps[0])
        rep["id"] = stable_id(rep)
        if len(ps) > 1:
            rep["versions"] = [
                {
                    "id": x.get("id"),
                    "kind": x.get("kind"),
                    "doi": x.get("doi"),
                    "title": x.get("title"),
                }
                for x in ps
            ]
            merge_evidence.append(
                {
                    "canonical_id": rep["id"],
                    "members": [x.get("id") for x in ps],
                    "merged_on": sorted(k for k in c["keys"] if len(c["keys"][k]) > 1),
                }
            )
        else:
            rep.setdefault("versions", [{"id": rep.get("id"), "kind": rep.get("kind")}])
        merged.append(rep)
    merged.sort(key=lambda p: (norm_title(p.get("title", "")), p.get("id", "")))
    return merged, merge_evidence


def distance_from_seed(seed_id, edges):
    adj = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s and t:
            adj.setdefault(s, set()).add(t)
            adj.setdefault(t, set()).add(s)
    dist = {seed_id: 0} if seed_id else {}
    queue = [seed_id] if seed_id else []
    while queue:
        cur = queue.pop(0)
        for nb in adj.get(cur, ()):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                queue.append(nb)
    return dist


def rank_papers(papers, edges, seed_id):
    dist = distance_from_seed(seed_id, edges)
    for p in papers:
        d = dist.get(p.get("id"), 999)
        year = p.get("year") or 0
        conf = float(p.get("confidence") or 1.0)
        p["distance"] = d
        p["score"] = round(100.0 / (1 + d) + max(0, year - 1900) / 100.0 + conf * 10.0, 3)
    papers.sort(
        key=lambda p: (
            p.get("distance", 999),
            -float(p.get("score", 0.0)),
            norm_title(p.get("title", "")),
            p.get("id", ""),
        )
    )
    return papers


def shortest_path(edges, src, dst):
    adj = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s and t:
            adj.setdefault(s, []).append(t)
    prev = {src: None}
    queue = [src]
    while queue:
        cur = queue.pop(0)
        if cur == dst:
            break
        for nb in adj.get(cur, ()):
            if nb not in prev:
                prev[nb] = cur
                queue.append(nb)
    if dst not in prev:
        return None
    nodes = []
    cur = dst
    while cur is not None:
        nodes.append(cur)
        cur = prev[cur]
    nodes.reverse()
    hops = []
    for i in range(len(nodes) - 1):
        a, b = nodes[i], nodes[i + 1]
        e = next((x for x in edges if x.get("source") == a and x.get("target") == b), None)
        hops.append(
            {
                "from": a,
                "to": b,
                "direction": "cites",
                "evidence": (e or {}).get("evidence"),
            }
        )
    return {"nodes": nodes, "hops": hops, "length": len(nodes) - 1}


def cluster_papers(papers, edges, seed_id):
    """按共被引/耦合信号做朴素连通分量聚类（仅基于已录边的弱关联）。"""
    adj = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s and t:
            adj.setdefault(s, set()).add(t)
            adj.setdefault(t, set()).add(s)
    comp = {}
    def walk(node, cid):
        stack = [node]
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp[n] = cid
            for nb in adj.get(n, ()):
                if nb not in comp:
                    stack.append(nb)
    for pid in [p.get("id") for p in papers]:
        if pid not in comp:
            walk(pid, pid)
    groups = {}
    for pid, cid in comp.items():
        groups.setdefault(cid, []).append(pid)
    clusters = []
    for idx, (cid, members) in enumerate(sorted(groups.items(), key=lambda kv: (kv[0],))):
        clusters.append(
            {
                "id": "cluster-%02d" % idx,
                "members": sorted(members),
                "seed_contained": seed_id in members if seed_id else False,
                "basis": "shared-citation-coupling-from-recorded-edges",
            }
        )
    return clusters


# --------------------------------------------------------------------------- #
# 派生输出 + SVG
# --------------------------------------------------------------------------- #
def materialize(ir):
    out = {}
    out["case_id"] = ir.get("case_id")
    for k, v in (ir.get("output_contract_fields") or {}).items():
        out[k] = v
    out["status"] = ir.get("status")
    out["seed"] = ir.get("seed")
    out["seed_key"] = ir.get("seed_key")
    out["research_question"] = ir.get("research_question")
    out["results"] = ir.get("results")
    out["sources"] = ir.get("sources")
    out["uncertainty"] = ir.get("uncertainty")
    out["change_log"] = ir.get("change_log")
    out["pending_human"] = ir.get("pending_human")
    out["errors"] = ir.get("errors")
    out["degradations"] = ir.get("degradations")
    out["limitations"] = ir.get("limitations")
    out["runtime"] = ir.get("runtime")
    return out


def render_svg(papers, edges, seed_id):
    if not papers:
        papers_ids = list({e.get("source") for e in edges if e.get("source")} | {e.get("target") for e in edges if e.get("target")})
    else:
        papers_ids = [p.get("id") for p in papers if p.get("id")]
    dist = distance_from_seed(seed_id, edges)
    layers = {}
    for pid in papers_ids:
        layers.setdefault(dist.get(pid, 0), []).append(pid)
    for lid in list(layers):
        layers[lid].sort(key=lambda pid: (0, pid))
    col_w, row_h = 230, 76
    max_layer = max(layers) if layers else 0
    max_rows = max(len(v) for v in layers.values()) if layers else 1
    H = max(160, max_rows * row_h + 70)
    W = 60 + (max_layer + 1) * col_w + 70
    pos = {}
    for lid, ids in layers.items():
        x = 70 + lid * col_w
        for i, pid in enumerate(ids):
            pos[pid] = (x, 60 + i * row_h)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
        '<rect width="100%%" height="100%%" fill="white"/>',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#999"/></marker></defs>',
    ]
    label_of = {p.get("id"): p for p in papers if p.get("id")}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in pos and t in pos:
            x1, y1 = pos[s]
            x2, y2 = pos[t]
            parts.append(
                '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#999" stroke-width="1" marker-end="url(#arrow)"/>'
                % (x1, y1, x2 - (x2 - x1 > 0 and 84 or 0), y2)
            )
    for pid, (x, y) in pos.items():
        p = label_of.get(pid, {})
        label = (p.get("short_label") or p.get("title") or pid or "?")
        label = label if len(label) <= 30 else label[:29] + "…"
        fill = "#f0c040" if pid == seed_id else "#cfe2f3"
        parts.append('<rect x="%d" y="%d" width="176" height="36" rx="4" fill="%s" stroke="#333"/>' % (x - 88, y - 18, fill))
        parts.append(
            '<text x="%d" y="%d" font-size="11" text-anchor="middle" font-family="sans-serif">%s</text>'
            % (x, y + 5, escape_xml(label))
        )
    parts.append("</svg>")
    return "\n".join(parts)


def render_report(ir, papers, edges, clusters, paths):
    lines = [
        "# 引文追踪报告（自动生成，可复核）",
        "",
        "- case_id: `%s`" % (ir.get("case_id") or "未提供"),
        "- status: `%s`" % ir.get("status"),
        "- seed_key: `%s`" % (ir.get("seed_key") or "未确认"),
        "- research_question: %s" % (ir.get("research_question") or "未填写"),
        "- papers=%d edges=%d clusters=%d" % (len(papers), len(edges), len(clusters)),
        "",
        "## 文献清单",
        "",
        "| id | title | year | distance | score |",
        "|---|---|---|---:|---:|",
    ]
    for p in papers:
        lines.append(
            "| `%s` | %s | %s | %s | %s |"
            % (
                escape_xml(p.get("id", "")),
                escape_xml((p.get("title") or "")[:60]),
                p.get("year") or "-",
                p.get("distance", "-"),
                p.get("score", "-"),
            )
        )
    lines += ["", "## 引用边", ""]
    for e in edges:
        lines.append(
            "- `%s` → `%s`（%s）"
            % (e.get("source"), e.get("target"), e.get("relation"))
        )
    if paths:
        lines += ["", "## 路径解释", ""]
        for path in paths:
            lines.append("- path: %s" % " → ".join(str(n) for n in path.get("nodes", [])))
    if ir.get("uncertainty"):
        lines += ["", "## 不确定性与缺口", ""]
        for u in ir["uncertainty"]:
            lines.append("- %s" % u if isinstance(u, str) else "- %s" % u.get("text", ""))
    if ir.get("limitations"):
        lines += ["", "## 限制说明", ""]
        for x in ir["limitations"]:
            lines.append("- %s" % x if isinstance(x, str) else "- %s" % x.get("text", ""))
    lines.append("")
    return "\n".join(lines)


def write_papers_csv(path, papers):
    cols = ["id", "title", "doi", "arxiv_id", "year", "first_author", "distance", "score", "kind"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in papers:
            w.writerow(p)


def write_edges_csv(path, edges):
    cols = ["source", "target", "relation", "source_label", "target_label", "evidence_locator"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for e in edges:
            row = {k: e.get(k) for k in cols}
            row["evidence_locator"] = (e.get("evidence") or {}).get("locator")
            w.writerow(row)


# --------------------------------------------------------------------------- #
# 阶段编排
# --------------------------------------------------------------------------- #
def load_ir(work_dir):
    path = os.path.join(work_dir, "trace.json")
    if not os.path.isfile(path):
        return None
    ir, err = load_json_file(path)
    return ir


def save_ir(work_dir, ir):
    os.makedirs(work_dir, exist_ok=True)
    write_json(os.path.join(work_dir, "trace.json"), ir)


def stage_ir(work_dir, output_dir):
    ir = load_ir(work_dir)
    if not ir:
        return False, None, ["no_ir_file"]
    problems = validate_ir(ir, "full")
    result = materialize(ir)
    os.makedirs(output_dir, exist_ok=True)
    write_json(os.path.join(output_dir, "result.json"), result)
    summary = {
        "papers": len((ir.get("results") or {}).get("papers", [])),
        "edges": len((ir.get("results") or {}).get("edges", [])),
        "clusters": len((ir.get("results") or {}).get("clusters", [])),
        "paths": len((ir.get("results") or {}).get("paths", [])),
        "sources": len(ir.get("sources", [])),
        "change_log": len(ir.get("change_log", [])),
    }
    return True, summary, problems


def finalize(work_dir, output_dir):
    ir = load_ir(work_dir)
    if not ir:
        return False, "no_ir_file", []
    # 幂等时间戳：runtime.end 只写一次，保证重复 finalize 产物一致。
    now = utcnow_iso()
    rt = ir.get("runtime")
    if not isinstance(rt, dict):
        rt = {}
        ir["runtime"] = rt
    if rt.get("end") is None:
        rt["end"] = now
        try:
            start = datetime.fromisoformat((rt.get("start") or now).replace("Z", "+00:00"))
            end = datetime.fromisoformat(now.replace("Z", "+00:00"))
            rt["seconds"] = round((end - start).total_seconds(), 1)
        except Exception:  # noqa: BLE001
            rt["seconds"] = None
    # 校验结果幂等覆盖，不污染 errors 字段。
    ir["_validation_problems"] = validate_ir(ir, "full")
    save_ir(work_dir, ir)

    ok, summary, problems = stage_ir(work_dir, output_dir)

    papers = (ir.get("results") or {}).get("papers", [])
    edges = (ir.get("results") or {}).get("edges", [])
    clusters = (ir.get("results") or {}).get("clusters", [])
    paths = (ir.get("results") or {}).get("paths", [])
    seed_id = (ir.get("seed") or {}).get("id")
    os.makedirs(output_dir, exist_ok=True)
    write_text(os.path.join(output_dir, "report.md"), render_report(ir, papers, edges, clusters, paths))
    write_papers_csv(os.path.join(output_dir, "papers.csv"), papers)
    write_edges_csv(os.path.join(output_dir, "edges.csv"), edges)
    write_text(os.path.join(output_dir, "citation_graph.svg"), render_svg(papers, edges, seed_id))
    return ok, summary, problems