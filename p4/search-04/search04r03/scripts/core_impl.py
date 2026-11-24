# -*- coding: utf-8 -*-
"""core_impl.py - 引文追踪 Skill 的核心实现（仅标准库，确定性）。

本模块不访问网络、不调用外部接口，只做三件事：
  1) 容错读取 /input 下的 case.json、task.md 与材料文件；
  2) 提供去重/身份合并、边方向规范化、排序、最短路径、聚类、
     证据溯源等确定性函数；
  3) 生成 result.json 骨架、校验 result.json，并产出可复核的
     report.md / papers.csv / edges.csv / citation_graph.svg。

确定性约定：同一输入在同一脚本处理下，papers / edges / clusters /
paths / sources / uncertainty / limitations / pending_human 等
"科学内容"必须逐字节一致；仅 runtime 与 change_log 允许含时间戳。
`content_hash()` 只对确定性子集取哈希，供自检脚本验证两次运行一致。
"""
import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DEFAULT_MAX_BYTES = int(os.environ.get("CITATION_MAX_BYTES", "2000000"))

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

# 结果中会随时间变化的字段，确定性哈希时排除。
VOLATILE_KEYS = ("runtime", "change_log")

# 标准顶层 schema；output_contract 若显式给出其它键，会原样回填到顶层。
STANDARD_TOP_LEVEL = {
    "case_id": None,
    "status": "partial",
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
    "pending_human": [],
    "limitations": [],
    "errors": [],
    "degradations": [],
    "change_log": [],
    "runtime": {
        "started_at": None,
        "seconds": 0,
        "model_requests": 0,
    },
}


# ---------------------------------------------------------------------------
# 容错 IO
# ---------------------------------------------------------------------------
def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text_tolerant(path, max_bytes=DEFAULT_MAX_BYTES):
    """读取文本，返回 dict：ok/text/encoding/error/skipped/bytes。"""
    out = {"ok": False, "text": "", "encoding": None, "error": None,
           "skipped": False, "bytes": 0}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        out["error"] = str(exc)
        return out
    out["bytes"] = size
    if size > max_bytes:
        out["skipped"] = True
        out["error"] = "exceeds max_bytes=%d" % max_bytes
        return out
    raw = b""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        out["error"] = str(exc)
        return out
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            out["text"] = raw.decode(enc)
            out["encoding"] = enc
            out["ok"] = True
            return out
        except (UnicodeDecodeError, LookupError):
            continue
    out["text"] = raw.decode("utf-8", errors="replace")
    out["encoding"] = "utf-8(replace)"
    out["ok"] = True
    return out


def scan_input_dir(input_dir):
    """递归发现输入文件，区分 case/task/materials。返回 dict。"""
    found = {"case_file": None, "task_file": None, "materials": [], "all": []}
    if not input_dir or not os.path.isdir(input_dir):
        return found
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            entry = {"path": full, "rel": rel, "size": os.path.getsize(full),
                     "kind": _classify_file(name, rel)}
            found["all"].append(entry)
            low = name.lower()
            if low == "case.json" and found["case_file"] is None:
                found["case_file"] = full
            elif low == "task.md" and found["task_file"] is None:
                found["task_file"] = full
            elif ext in (".pdf", ".json", ".csv", ".txt", ".md", ".xml",
                         ".ris", ".bib", ".tsv"):
                found["materials"].append(entry)
    return found


def _classify_file(name, rel):
    ext = os.path.splitext(name)[1].lower()
    return {
        ".pdf": "pdf", ".json": "json", ".csv": "csv", ".tsv": "tsv",
        ".txt": "text", ".md": "markdown", ".xml": "xml", ".ris": "ris",
        ".bib": "bibtex",
    }.get(ext, "other")


# ---------------------------------------------------------------------------
# 输入元数据与 output_contract
# ---------------------------------------------------------------------------
def load_case_meta(case_path):
    """容错读取 case.json。返回 (meta_dict, errors, degradations)。"""
    meta, errors, degradations = {}, [], []
    if not case_path or not os.path.isfile(case_path):
        errors.append("case.json 不存在: %s" % (case_path or "<none>"))
        return meta, errors, degradations
    rd = read_text_tolerant(case_path)
    if not rd["ok"]:
        errors.append("case.json 读取失败: %s" % rd["error"])
        return meta, errors, degradations
    if rd["encoding"] in ("gb18030", "latin-1", "utf-8(replace)"):
        degradations.append("case.json 编码回退为 %s" % rd["encoding"])
    try:
        meta = json.loads(rd["text"])
    except json.JSONDecodeError as exc:
        errors.append("case.json 不是合法 JSON: %s" % exc)
        return {}, errors, degradations
    if not isinstance(meta, dict):
        errors.append("case.json 顶层不是对象")
        return {}, errors, degradations
    return meta, errors, degradations


def extract_case_id(meta, task_text, input_dir):
    """按优先级取 case_id：case.json > task.md 文本 > 目录名。"""
    for key in ("case_id", "caseId", "id", "challenge_id", "challengeId"):
        if isinstance(meta.get(key), str) and meta.get(key).strip():
            return meta[key].strip()
    m = re.search(r"(?:case[_-]?id|challenge[_-]?id)\s*[:=]\s*[`'\"]?([A-Za-z0-9._-]+)", task_text or "", re.I)
    if m:
        return m.group(1)
    base = os.path.basename(os.path.abspath(input_dir or ""))
    if base:
        return "case:" + base
    return "case:unknown"


def default_contract():
    """无显式 output_contract 时的缺省契约（依据题目交付要求）。"""
    return {
        "literature_candidates": [],
        "sorted_results": [],
        "evidence_records": [],
        "missing_note": None,
    }


def extract_output_contract(meta):
    """提取题目 output_contract 的全部字段；缺值填 null/[]，保留未知键。"""
    raw = meta.get("output_contract")
    contract = {}
    if isinstance(raw, dict):
        for key, spec in raw.items():
            contract[key] = default_for_spec(spec)
    if not contract:
        contract = default_contract()
    return contract, (raw if isinstance(raw, dict) else {})


def default_for_spec(spec):
    if isinstance(spec, dict):
        if "default" in spec:
            return json.loads(json.dumps(spec["default"]))
        typ = str(spec.get("type", "")).lower()
        spec = typ
    if isinstance(spec, str):
        typ = spec.lower().strip()
        if typ in ("list", "array"):
            return []
        if typ in ("dict", "object", "map"):
            return {}
        if typ in ("int", "float", "number", "str", "string", "text"):
            return None
        if typ in ("bool", "boolean"):
            return False
        return None
    if isinstance(spec, list):
        return []
    if isinstance(spec, (int, float)):
        return None
    if isinstance(spec, bool):
        return False
    return None


# ---------------------------------------------------------------------------
# 骨架与落盘
# ---------------------------------------------------------------------------
def build_skeleton(case_id, contract, seed=None, seed_key=None,
                   research_question=None, status="partial"):
    result = json.loads(json.dumps(STANDARD_TOP_LEVEL))
    result["case_id"] = case_id
    result["status"] = status if status in STATUS_VALUES else "partial"
    result["seed"] = seed if isinstance(seed, dict) else {}
    result["seed_key"] = seed_key
    result["research_question"] = research_question
    for key, value in contract.items():
        result[key] = value
    result["runtime"]["started_at"] = _now_iso()
    result["change_log"] = [{
        "time": _now_iso(),
        "content": "bootstrap 写入合法 partial 骨架（%s）" % status,
        "reason": "Step 0 先落盘，保证任何后续异常都不留空文件",
    }]
    return result


def write_json(path, obj):
    """写 JSON：稳定键序 + ensure_ascii=False + 末尾换行。"""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def bootstrap(input_dir, output_dir, work_dir):
    """Step 0：扫描输入、读 case.json、落盘 result.json 骨架 + state.json。

    始终以退出码 0 结束；写盘失败时打印最小合法 JSON 到 stdout。
    """
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    scan = scan_input_dir(input_dir)
    task_text = ""
    if scan["task_file"]:
        rd = read_text_tolerant(scan["task_file"])
        task_text = rd["text"] if rd["ok"] else ""

    meta, errors, degradations = load_case_meta(scan["case_file"])
    case_id = extract_case_id(meta, task_text, input_dir)
    contract, raw_contract = extract_output_contract(meta)

    seed = {}
    seed_key = None
    if isinstance(meta.get("seed"), dict):
        seed = meta["seed"]
    if isinstance(meta.get("seed_key"), str):
        seed_key = meta["seed_key"].strip() or None
    rq = None
    if isinstance(meta.get("research_question"), str):
        rq = meta["research_question"].strip() or None
    elif isinstance(meta.get("question"), str):
        rq = meta["question"].strip() or None

    status = "insufficient_evidence" if (not scan["materials"] and not seed and not task_text) else "partial"
    result = build_skeleton(case_id, contract, seed=seed, seed_key=seed_key,
                            research_question=rq, status=status)
    if raw_contract:
        result["output_contract"] = raw_contract
    if errors:
        result["errors"].extend(errors)
        # 仅当 case.json 存在但无法解析时才判定 input_error；
        # case.json 缺失属于材料不足，不升级为 input_error。
        if scan["case_file"]:
            result["status"] = "input_error"
    if degradations:
        result["degradations"].extend(degradations)

    state = {
        "case_id": case_id,
        "input_dir": input_dir,
        "case_file": scan["case_file"],
        "task_file": scan["task_file"],
        "materials": scan["materials"],
        "seed": seed,
        "seed_key": seed_key,
        "research_question": rq,
        "layer": 0,
        "model_requests_used": 0,
        "started_at": _now_iso(),
        "last_updated": _now_iso(),
        "errors": errors,
        "degradations": degradations,
    }

    result_path = os.path.join(output_dir, "result.json")
    state_path = os.path.join(work_dir, "state.json")
    try:
        write_json(result_path, result)
        write_json(state_path, state)
    except OSError as exc:
        # 写盘失败也要退出码 0，并把最小合法 JSON 打印到 stdout。
        print(json.dumps(build_skeleton(case_id, contract, seed=seed,
                                        seed_key=seed_key, status="input_error"),
                         ensure_ascii=False, sort_keys=True))
        print("write_error: %s" % exc, file=sys.stderr)
        return 0
    print("bootstrap ok: case_id=%s status=%s materials=%d"
          % (case_id, result["status"], len(scan["materials"])))
    return 0


# ---------------------------------------------------------------------------
# 身份归一化与去重
# ---------------------------------------------------------------------------
def normalize_title(title):
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", str(title))
    t = t.lower()
    t = re.sub(r"[^\w]+", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_doi(doi):
    if not doi:
        return ""
    d = str(doi).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d


def normalize_arxiv(arx):
    if not arx:
        return ""
    a = str(arx).strip()
    a = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", a)
    a = re.sub(r"^arxiv:\s*", "", a, flags=re.I)
    return a


def arxiv_versionless(arx):
    return re.sub(r"[vV]\d+$", "", normalize_arxiv(arx))


def first_author_surname(authors):
    if not authors:
        return ""
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, dict):
            return str(first.get("family") or first.get("last") or "").strip().lower()
        if isinstance(first, str):
            return first.split(",")[0].strip().lower()
    if isinstance(authors, str):
        return authors.split(",")[0].split(" ")[-1].strip().lower()
    return ""


def paper_identity_keys(paper):
    """返回身份键列表，按优先级：DOI > arXiv > 标题+作者+年 > 标题+年 > 标题。"""
    keys = []
    doi = normalize_doi(paper.get("doi"))
    if doi:
        keys.append(("doi", "doi:" + doi))
    arx = arxiv_versionless(paper.get("arxiv_id") or paper.get("arxiv"))
    if arx:
        keys.append(("arxiv", "arxiv:" + arx))
    title = normalize_title(paper.get("title"))
    if title:
        year = str(paper.get("year") or "").strip()
        surname = first_author_surname(paper.get("authors"))
        if surname and year:
            keys.append(("title_author_year", "%s|%s|%s" % (title, surname, year)))
        if year:
            keys.append(("title_year", "%s|%s" % (title, year)))
        keys.append(("title", "title:" + title))
    return keys


def title_similarity(a, b):
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    from difflib import SequenceMatcher
    return SequenceMatcher(None, na, nb).ratio()


def dedupe_papers(papers, threshold=0.94):
    """按身份键优先级合并同一论文的不同版本；返回 (merged, merge_evidence)。"""
    if not isinstance(papers, list):
        return [], []
    groups = []          # list of {representative, members:[ids], evidence:[...]}
    key_to_group = {}    # canonical key -> group index

    for idx, paper in enumerate(papers):
        if not isinstance(paper, dict) or not paper.get("id"):
            continue
        matched = None
        for key_type, key in paper_identity_keys(paper):
            if key in key_to_group:
                matched = key_to_group[key]
                break
        if matched is None and paper.get("title"):
            # 标题近似匹配（跨库字段轻微不一致时兜底）。
            nt = normalize_title(paper.get("title"))
            for gi, grp in enumerate(groups):
                rt = normalize_title(grp["representative"].get("title"))
                if rt and title_similarity(nt, rt) >= threshold:
                    matched = gi
                    break
        if matched is None:
            groups.append({"representative": paper, "members": [paper.get("id")],
                           "evidence": []})
            gi = len(groups) - 1
            for key_type, key in paper_identity_keys(paper):
                key_to_group.setdefault(key, gi)
        else:
            grp = groups[matched]
            grp["members"].append(paper.get("id"))
            grp["representative"] = merge_paper_records(grp["representative"], paper)
            for key_type, key in paper_identity_keys(paper):
                key_to_group.setdefault(key, matched)

    merged, evidence = [], []
    for grp in groups:
        rep = grp["representative"]
        rep["versions"] = sorted(set(grp["members"]))
        rep["sources"] = _union_sources(rep.get("sources"))
        merged.append(rep)
        if len(grp["members"]) > 1:
            evidence.append({
                "merged_into": rep.get("id"),
                "member_ids": grp["members"],
                "basis": grp["evidence"],
            })
    merged.sort(key=lambda p: str(p.get("id", "")))
    return merged, evidence


def merge_paper_records(a, b):
    """合并两条记录：标量取先非空，列表并集去重，保留更完整的元数据。"""
    merged = json.loads(json.dumps(a if isinstance(a, dict) else {}))
    b = b if isinstance(b, dict) else {}
    for key, val in b.items():
        if merged.get(key) in (None, "", []) and val not in (None, "", []):
            merged[key] = val
        elif isinstance(merged.get(key), list) and isinstance(val, list):
            merged[key] = _dedup_list(merged[key] + val)
        elif key == "year" and merged.get(key) is None and val is not None:
            merged[key] = val
    return merged


def _dedup_list(items):
    seen, out = set(), []
    for it in items:
        marker = json.dumps(it, sort_keys=True, ensure_ascii=False) \
            if isinstance(it, (dict, list)) else repr(it)
        if marker not in seen:
            seen.add(marker)
            out.append(it)
    return out


def _union_sources(sources):
    return _dedup_list(sources or [])


# ---------------------------------------------------------------------------
# 边方向规范化
# ---------------------------------------------------------------------------
def normalize_edge_direction(edge, seed_id):
    """统一边方向为 source cites target；无法判断标 direction_unknown。"""
    direction = (edge or {}).get("direction", "").lower()
    src = (edge or {}).get("source")
    tgt = (edge or {}).get("target")
    if direction in ("forward", "backward"):
        return direction
    if src == seed_id and tgt != seed_id:
        return "backward"      # 种子 cites 出处
    if tgt == seed_id and src != seed_id:
        return "forward"       # 他者 cites 种子
    if src and tgt and src != tgt:
        return "direction_unknown"
    return "direction_unknown"


def canonical_edge_id(edge):
    src = str(edge.get("source") or "")
    tgt = str(edge.get("target") or "")
    rel = str(edge.get("relation") or "cites")
    return "%s->%s:%s" % (src, tgt, rel)


def dedupe_edges(edges):
    """边去重：同 source/target/relation 只留一条，证据并集。"""
    by_key = {}
    for e in edges or []:
        key = canonical_edge_id(e)
        if key in by_key:
            prev = by_key[key]
            prev["evidence"] = _dedup_list(prev.get("evidence", []) + e.get("evidence", []))
            for label in ("source_label", "target_label"):
                if not prev.get(label) and e.get(label):
                    prev[label] = e[label]
        else:
            by_key[key] = json.loads(json.dumps(e))
    out = sorted(by_key.values(), key=lambda e: canonical_edge_id(e))
    return out


# ---------------------------------------------------------------------------
# 排序 / 路径 / 角色 / 聚类
# ---------------------------------------------------------------------------
def build_adjacency(edges):
    """返回 cites 邻接（source -> targets）与 cited_by（target -> sources）。"""
    cites, cited_by = {}, {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        cites.setdefault(s, set()).add(t)
        cited_by.setdefault(t, set()).add(s)
    return cites, cited_by


def rank_papers(papers, edges, seed_id=None):
    """确定性排序：距离越近、被引越多、来源置信度越高越靠前。

    不是"高被引=高相关"：距离（layer）占主导，被引只作同层内的弱信号。
    """
    import math
    cites, cited_by = build_adjacency(edges)
    layer = {}
    if seed_id:
        layer[seed_id] = 0
        frontier = [seed_id]
        seen = {seed_id}
        depth = 0
        while frontier:
            depth += 1
            nxt = []
            for n in frontier:
                for m in sorted(cites.get(n, set()) | cited_by.get(n, set())):
                    if m not in seen:
                        seen.add(m)
                        layer[m] = depth
                        nxt.append(m)
            frontier = nxt
    scored = []
    for p in papers or []:
        pid = str(p.get("id", ""))
        d = layer.get(pid, 99)
        cited_by_n = len(cited_by.get(pid, ()))
        cites_n = len(cites.get(pid, ()))
        conf = _source_confidence(p.get("sources"))
        score = 10.0 / (d + 1) + math.log1p(cited_by_n) + 0.5 * math.log1p(cites_n) + conf
        p = json.loads(json.dumps(p))
        p["rank_score"] = round(score, 4)
        p["_rank_components"] = {
            "layer": d, "cited_by": cited_by_n, "cites": cites_n,
            "source_confidence": round(conf, 3),
        }
        scored.append(p)
    scored.sort(key=lambda p: (-p["rank_score"], str(p.get("id", ""))))
    for p in scored:
        p.pop("_rank_components", None)
    return scored


def _source_confidence(sources):
    score = 0.0
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        src = str(s.get("kind") or s.get("locator") or "").lower()
        if "doi" in src or "crossref" in src or "pubmed" in src or "europepmc" in src:
            score += 1.0
        elif "arxiv" in src:
            score += 0.8
        else:
            score += 0.4
    return score


def shortest_path(edges, source_id, target_id):
    """无权 BFS 求 source -> target 的 cites 路径，返回逐跳节点列表或 []。"""
    cites, _ = build_adjacency(edges)
    if source_id == target_id:
        return [source_id]
    from collections import deque
    parent = {source_id: None}
    q = deque([source_id])
    while q:
        cur = q.popleft()
        for nxt in sorted(cites.get(cur, ())):
            if nxt not in parent:
                parent[nxt] = cur
                q.append(nxt)
                if nxt == target_id:
                    path, node = [], target_id
                    while node is not None:
                        path.append(node)
                        node = parent[node]
                    return list(reversed(path))
    return []


def detect_roles(edges, seed_id=None):
    """识别奠基节点（仅被引）、桥接节点（入度与出度均高）、热点（近端高被引）。"""
    cites, cited_by = build_adjacency(edges)
    roles = {}
    for e in edges or []:
        for n in (e.get("source"), e.get("target")):
            if n:
                roles.setdefault(n, [])
    for node in roles:
        out_n = len(cites.get(node, ()))
        in_n = len(cited_by.get(node, ()))
        tags = []
        if in_n > 0 and out_n == 0:
            tags.append("foundation")
        if in_n >= 2 and out_n >= 2:
            tags.append("bridge")
        if node == seed_id:
            tags.append("seed")
        if tags:
            roles[node] = tags
    return {k: v for k, v in roles.items() if v}


def cluster_papers(papers, edges):
    """按引用关系的无向连通分量聚类；孤立节点各自成簇。"""
    adj = {}
    ids = sorted(str(p.get("id", "")) for p in papers or [])
    for i in ids:
        adj.setdefault(i, set())
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if s in adj and t in adj and s != t:
            adj[s].add(t)
            adj[t].add(s)
    seen, clusters = set(), []
    for i in ids:
        if i in seen:
            continue
        comp = []
        stack = [i]
        seen.add(i)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in sorted(adj[cur]):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        clusters.append(sorted(comp))
    clusters.sort(key=lambda c: (-len(c), c[0] if c else ""))
    return [{"id": "cluster-%d" % i, "members": c,
             "rationale": "无向引用连通分量"} for i, c in enumerate(clusters, 1)]


# ---------------------------------------------------------------------------
# 证据 / 变更记录
# ---------------------------------------------------------------------------
def make_source(locator, quote, kind=None, url=None, meta=None):
    src = {"locator": locator, "quote": quote}
    if kind:
        src["kind"] = kind
    if url:
        src["url"] = url
    if isinstance(meta, dict) and meta:
        src["meta"] = meta
    return src


def append_change_log(result, content, reason):
    log = result.setdefault("change_log", [])
    log.append({"time": _now_iso(), "content": content, "reason": reason})
    return result


def add_uncertainty(result, utype, detail, status="open"):
    item = {"type": utype, "detail": detail, "status": status}
    if item not in result.setdefault("uncertainty", []):
        result["uncertainty"].append(item)
    return result


# ---------------------------------------------------------------------------
# 校验 / 哈希
# ---------------------------------------------------------------------------
def validate_result(result):
    """返回问题列表；空列表表示合法。"""
    problems = []
    if not isinstance(result, dict):
        return ["result 不是 JSON 对象"]
    if not result.get("case_id"):
        problems.append("缺少 case_id")
    if result.get("status") not in STATUS_VALUES:
        problems.append("status 非法: %r" % result.get("status"))
    for key in ("sources", "uncertainty", "change_log", "pending_human",
                "limitations", "errors", "degradations"):
        if key not in result:
            problems.append("缺少字段 %s" % key)
    res = result.get("results")
    if not isinstance(res, dict):
        problems.append("results 不是对象")
    else:
        for key in ("papers", "edges", "clusters", "paths"):
            if not isinstance(res.get(key), list):
                problems.append("results.%s 不是列表" % key)
        ids = {p.get("id") for p in res.get("papers", []) if isinstance(p, dict)}
        for e in res.get("edges", []):
            if not isinstance(e, dict):
                problems.append("edges 含非对象条目")
                continue
            if not e.get("source") or not e.get("target"):
                problems.append("边缺少 source/target")
            if e.get("relation") != "cites":
                problems.append("边 relation 应为 cites: %r" % e.get("relation"))
            if e.get("source") not in ids or e.get("target") not in ids:
                problems.append("边引用了未收录的节点: %s -> %s"
                                % (e.get("source"), e.get("target")))
    return problems


def deterministic_subset(result):
    """去掉 volatile 字段后的确定性子集。"""
    return {k: v for k, v in result.items() if k not in VOLATILE_KEYS}


def content_hash(result):
    payload = json.dumps(deterministic_subset(result), sort_keys=True,
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# 产物生成（report / csv / svg）
# ---------------------------------------------------------------------------
def load_result(output_dir):
    rd = read_text_tolerant(os.path.join(output_dir, "result.json"))
    if not rd["ok"]:
        return None, [rd["error"]]
    try:
        return json.loads(rd["text"]), []
    except json.JSONDecodeError as exc:
        return None, [str(exc)]


def finalize(output_dir, work_dir=None):
    """读 result.json，校验并生成 report.md / papers.csv / edges.csv / svg。"""
    os.makedirs(output_dir, exist_ok=True)
    result, problems = load_result(output_dir)
    if result is None:
        print("finalize error: %s" % "; ".join(problems))
        return 1
    result["_validate_problems"] = problems

    papers = result.get("results", {}).get("papers", [])
    edges = result.get("results", {}).get("edges", [])
    seed_id = None
    seed = result.get("seed") or {}
    if isinstance(seed, dict):
        seed_id = seed.get("id") or seed.get("doi") or seed.get("title") or None

    _write_papers_csv(os.path.join(output_dir, "papers.csv"), papers)
    _write_edges_csv(os.path.join(output_dir, "edges.csv"), edges)
    _write_report(os.path.join(output_dir, "report.md"), result, problems, seed_id)
    _write_svg(os.path.join(output_dir, "citation_graph.svg"), papers, edges, seed_id)

    if problems:
        print("finalize warn: %d validation problems (result.json 仍已落盘)" % len(problems))
        return 0
    print("finalize ok: papers=%d edges=%d hash=%s"
          % (len(papers), len(edges), content_hash(result)))
    return 0


def _write_papers_csv(path, papers):
    cols = ["id", "title", "authors", "year", "doi", "arxiv_id", "venues",
            "role", "layer", "rank_score", "versions", "source_count"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for p in sorted(papers, key=lambda x: str(x.get("id", ""))):
            w.writerow([
                p.get("id", ""),
                p.get("title", ""),
                ";".join(_flatten_authors(p.get("authors"))),
                p.get("year", ""),
                p.get("doi", ""),
                p.get("arxiv_id", ""),
                ";".join(str(v) for v in (p.get("venues") or [])),
                p.get("role", ""),
                p.get("layer", ""),
                p.get("rank_score", ""),
                ";".join(str(v) for v in (p.get("versions") or [])),
                len(p.get("sources") or []),
            ])


def _flatten_authors(authors):
    out = []
    for a in authors or []:
        if isinstance(a, dict):
            out.append("%s %s" % (a.get("given", ""), a.get("family", "")).strip())
        elif isinstance(a, str):
            out.append(a)
    return [x for x in out if x]


def _write_edges_csv(path, edges):
    cols = ["id", "source", "target", "relation", "direction", "layer",
            "evidence_count"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for e in sorted(edges, key=lambda x: canonical_edge_id(x)):
            w.writerow([
                canonical_edge_id(e),
                e.get("source", ""),
                e.get("target", ""),
                e.get("relation", "cites"),
                e.get("direction", ""),
                e.get("layer", ""),
                len(e.get("evidence") or []),
            ])


def _write_report(path, result, problems, seed_id):
    res = result.get("results", {})
    papers = res.get("papers", [])
    edges = res.get("edges", [])
    lines = [
        "# 引文追踪结果复核报告",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- seed_key: `%s`" % result.get("seed_key"),
        "- research_question: %s" % (result.get("research_question") or "（未声明）"),
        "- 确定性内容哈希: `%s`" % content_hash(result),
        "- 论文数: %d　边数: %d　簇数: %d　路径数: %d"
        % (len(papers), len(edges), len(res.get("clusters", [])),
           len(res.get("paths", []))),
        "",
        "> 本报告由 `scripts/main.py finalize` 确定性生成；生成时刻见 result.json.runtime。",
        "",
        "## 1. 文献清单（按 rank_score 排序）",
        "",
    ]
    for p in papers:
        authors = ", ".join(_flatten_authors(p.get("authors"))[:3])
        if len(_flatten_authors(p.get("authors"))) > 3:
            authors += " 等"
        lines.append("- **%s** — %s (%s)" % (p.get("id"), p.get("title") or "（无题）",
                                             p.get("year") or "年份未知"))
        lines.append("  - 作者: %s" % (authors or "未知"))
        if p.get("doi"):
            lines.append("  - DOI: %s" % p["doi"])
        if p.get("versions"):
            lines.append("  - 版本合并: %s" % ", ".join(map(str, p["versions"])))
        lines.append("  - 来源数: %d　排序分: %s" % (len(p.get("sources") or []),
                                                    p.get("rank_score")))
    lines += ["", "## 2. 引用边", ""]
    for e in edges:
        lines.append("- %s cites %s [%s]（证据 %d 条）"
                     % (e.get("source"), e.get("target"),
                        e.get("direction", "unknown"), len(e.get("evidence") or [])))
    lines += ["", "## 3. 状态与缺口", ""]
    for item in result.get("limitations", []):
        lines.append("- limitation: %s" % item)
    for item in result.get("pending_human", []):
        lines.append("- pending_human: %s" % item)
    for item in result.get("uncertainty", []):
        lines.append("- uncertainty[%s]: %s" % (item.get("type"), item.get("detail")))
    for item in result.get("errors", []):
        lines.append("- error: %s" % item)
    if problems:
        lines += ["", "## 4. 校验问题", ""]
        for p in problems:
            lines.append("- %s" % p)
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _write_svg(path, papers, edges, seed_id):
    """确定性分层布局（BFS 层 + 层内按 id 排序），无随机。"""
    svg = render_svg_deterministic(papers, edges, seed_id)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)


def render_svg_deterministic(papers, edges, seed_id):
    """生成可解析的 SVG（分层布局，层内按 id 稳定排序）。"""
    ids = sorted(str(p.get("id", "")) for p in papers or [])
    cites, cited_by = build_adjacency(edges)
    layer = {}
    frontier = [seed_id] if seed_id and seed_id in ids else (ids[:1] if ids else [])
    if frontier:
        seen = set(frontier)
        for n in frontier:
            layer[n] = 0
        depth = 0
        while frontier:
            depth += 1
            nxt = []
            for n in sorted(frontier):
                for m in sorted(cites.get(n, set()) | cited_by.get(n, set())):
                    if m in ids and m not in seen:
                        seen.add(m)
                        layer[m] = depth
                        nxt.append(m)
            frontier = nxt
    for i in ids:
        if i not in layer:
            layer[i] = max(layer.values(), default=0) + 1 if layer else 0

    by_layer = {}
    for i in ids:
        by_layer.setdefault(layer[i], []).append(i)
    for k in by_layer:
        by_layer[k].sort()

    dx, dy, r = 220, 90, 26
    pos = {}
    max_cols = max(len(v) for v in by_layer.values()) if by_layer else 1
    height = max_cols * dy + 60
    for lv, nodes in by_layer.items():
        for row, n in enumerate(nodes):
            pos[n] = (60 + lv * dx, 60 + row * dy)
    width = 60 + (max(layer.values(), default=0) + 1) * dx + 60

    ET.register_namespace("", "http://www.w3.org/2000/svg")
    root = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width), "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
    })
    ET.SubElement(root, "rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height),
        "fill": "#ffffff",
    })
    defs = ET.SubElement(root, "defs")
    marker = ET.SubElement(defs, "marker", {
        "id": "arrow", "viewBox": "0 0 10 10", "refX": "9", "refY": "5",
        "markerWidth": "6", "markerHeight": "6", "orient": "auto",
    })
    ET.SubElement(marker, "path", {"d": "M0,0 L10,5 L0,10 z", "fill": "#555555"})

    for e in sorted(edges, key=lambda x: canonical_edge_id(x)):
        s, t = e.get("source"), e.get("target")
        if s not in pos or t not in pos:
            continue
        x1, y1 = pos[s]
        x2, y2 = pos[t]
        ET.SubElement(root, "line", {
            "x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2),
            "stroke": "#999999", "stroke-width": "1.2",
            "marker-end": "url(#arrow)",
        })
    for n in ids:
        x, y = pos[n]
        fill = "#f2c94c" if n == seed_id else "#e0e0e0"
        ET.SubElement(root, "circle", {
            "cx": str(x), "cy": str(y), "r": str(r), "fill": fill,
            "stroke": "#333333", "stroke-width": "1",
        })
        label = str(n)
        if len(label) > 14:
            label = label[:13] + "…"
        ET.SubElement(root, "text", {
            "x": str(x), "y": str(y + 4), "text-anchor": "middle",
            "font-size": "11", "font-family": "sans-serif",
        }).text = label
    tree = ET.ElementTree(root)
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue().decode("utf-8")


# ---------------------------------------------------------------------------
# CLI 子命令
# ---------------------------------------------------------------------------
def cmd_bootstrap(args):
    return bootstrap(args.input, args.output, args.work)


def cmd_finalize(args):
    return finalize(args.output, args.work)
