"""citation_lib.py — citation-tracing helper library (Python standard library only).

Deterministic, side-effect-light helpers used by `main.py` and `selftest.py`,
and importable at runtime by the Agent through `python -c "import citation_lib ..."`.

Design rules kept here (mirrored in SKILL.md / references/RULES.md):
  * edge direction is always `source` cites `target`; unknown direction is
    recorded as `relation: direction_unknown` and never guessed;
  * identity merge priority: DOI -> arXiv -> title+first-author+year -> title;
  * every claim/edge/number carries `locator` + verbatim `quote`;
  * no network access, no fake citations, no guessed values.
"""
from __future__ import annotations

import csv
import datetime
import json
import math
import os
import re
import unicodedata

VERSION = "2.0.0"
SKILL_NAME = "citation-tracing"

MAX_TEXT_BYTES = 2 * 1024 * 1024          # skip single files larger than 2 MB
MAX_INPUT_FILES = 1000                    # safety cap on directory scan
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "latin-1")


# --------------------------------------------------------------------------- #
# time / text / io
# --------------------------------------------------------------------------- #
def now_utc() -> str:
    """RFC3339 UTC timestamp with second precision."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text_robust(path: str, max_bytes: int = MAX_TEXT_BYTES):
    """Read a text file defensively.

    Returns ``(text, error)``; exactly one of the two is meaningful.
    Never raises: oversized files, binary content and bad encodings are
    reported as errors instead of crashing the caller.
    """
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, f"stat_failed:{exc}"
    if size > max_bytes:
        return None, f"oversized:{size} bytes > {max_bytes}"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, f"read_failed:{exc}"
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    last_err = None
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
            # heuristics to reject obviously binary content
            if "\x00" in text:
                last_err = f"binary_nul_bytes"
                continue
            return text, None
        except (UnicodeDecodeError, ValueError) as exc:
            last_err = f"{enc}:{exc}"
            continue
    return None, f"decode_failed:{last_err}"


def read_json(path: str):
    """Read a JSON file defensively, returning ``(obj, None)`` or ``(None, err)``."""
    text, err = read_text_robust(path)
    if err is not None:
        return None, err
    try:
        return json.loads(text), None
    except (ValueError, RecursionError) as exc:
        return None, f"bad_json:{exc}"


def list_input_files(directory: str):
    """Enumerate files recursively, sorted for deterministic behavior."""
    out = []
    if not directory or not os.path.isdir(directory):
        return out
    try:
        for root, _dirs, files in os.walk(directory):
            for name in files:
                full = os.path.join(root, name)
                out.append(os.path.abspath(full))
                if len(out) >= MAX_INPUT_FILES:
                    return sorted(out)
    except OSError:
        pass
    return sorted(out)


def ensure_dir(path: str):
    """Create directory if missing; never raises on failure (returns bool)."""
    if not path:
        return False
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def atomic_write_text(path: str, text: str) -> str:
    """Write text using a temp file + replace; returns '' on success or an error."""
    directory = os.path.dirname(os.path.abspath(path))
    ok = ensure_dir(directory)
    if not ok and not os.path.isdir(directory):
        return "mkdir_failed"
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return ""
    except OSError as exc:
        for p in (tmp,):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        return f"write_failed:{exc}"


def json_dumps(obj) -> str:
    """Deterministic JSON serialization (sorted keys, stable across runs)."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# input discovery / contract handling
# --------------------------------------------------------------------------- #
def locate_input(directory: str):
    """Find case.json / task.md / material files without assuming names."""
    files = list_input_files(directory)
    pkg = {"case_json": None, "task_md": None, "materials": [], "other": []}
    for f in files:
        base = os.path.basename(f).lower()
        if base == "case.json":
            pkg["case_json"] = pkg["case_json"] or f
        elif base in ("task.md", "task.txt"):
            pkg["task_md"] = pkg["task_md"] or f
        elif base.endswith((".pdf", ".json", ".csv", ".txt", ".md", ".bib", ".xml")):
            if base not in ("case.json",):
                pkg["materials"].append(f)
        else:
            pkg["other"].append(f)
    return pkg


def infer_default(key: str, spec):
    """Infer an honest empty value for one output-contract field."""
    k = str(key).lower()
    if isinstance(spec, dict):
        t = str(spec.get("type", "")).lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        if t in ("integer", "number", "float"):
            return 0
        if t == "boolean":
            return None
    # name heuristics
    if any(tok in k for tok in ("papers", "edges", "clusters", "paths", "sources",
                                "items", "results", "list", "records", "row")):
        return []
    if any(tok in k for tok in ("count", "total", "size", "num")):
        return 0
    return None


def fill_contract_defaults(contract):
    """Expand an output_contract (dict/list) into a dict of empty defaults.

    Handles three common shapes without crashing:
      * ``{field: schema}``   -> recurse into schema/spec
      * ``["field", ...]``    -> each field gets an inferred default
      * ``[{name, type}]``    -> field objects
    Unknown shapes degrade to ``{}``.
    """
    if isinstance(contract, dict):
        out = {}
        for key, spec in contract.items():
            if isinstance(spec, dict) and spec.get("type") in ("array", "list"):
                out[str(key)] = []
            elif isinstance(spec, dict) and spec.get("type") in ("object", "dict", "map"):
                out[str(key)] = {}
            elif isinstance(spec, dict) and "default" in spec:
                out[str(key)] = spec["default"]
            elif isinstance(spec, dict):
                out[str(key)] = infer_default(key, spec)
            elif isinstance(spec, (list, tuple)):
                out[str(key)] = []
            else:
                out[str(key)] = infer_default(key, spec)
        return out
    if isinstance(contract, list):
        out = {}
        for item in contract:
            if isinstance(item, str):
                out[item] = infer_default(item, None)
            elif isinstance(item, dict) and "name" in item:
                out[str(item["name"])] = infer_default(item.get("name"), item)
            elif isinstance(item, dict) and "key" in item:
                out[str(item["key"])] = infer_default(item.get("key"), item)
        return out
    return {}


# --------------------------------------------------------------------------- #
# identity normalization / deduplication
# --------------------------------------------------------------------------- #
def norm_text(s):
    """NFKC-normalize, lowercase, collapse whitespace."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def lower_keep(s):
    return norm_text(s)


def norm_doi(s):
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    s = s.rstrip(".,;")
    return s.strip()


def norm_arxiv(s):
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", s)
    s = re.sub(r"^arxiv:\s*", "", s)
    s = re.sub(r"v\d+$", "", s)
    return s.strip()


def paper_identity_keys(paper):
    """Return ordered candidate identity keys for one paper record.

    Priority matches SKILL.md: DOI -> arXiv -> title+first-author+year -> title.
    Two different records that share *any* prefix-tagged key are merged.
    """
    keys = []
    if not isinstance(paper, dict):
        return keys
    doi = norm_doi(paper.get("doi") or paper.get("DOI") or paper.get("url_doi"))
    if doi:
        keys.append(("doi", doi))
    arx = norm_arxiv(paper.get("arxiv") or paper.get("arXiv") or paper.get("arxiv_id"))
    if arx:
        keys.append(("arxiv", arx))
    title = norm_text(paper.get("title"))
    year = str(paper.get("year") or paper.get("date") or "").strip()[:4]
    authors = paper.get("authors") or paper.get("author") or []
    first_author = ""
    if isinstance(authors, (list, tuple)) and authors:
        a0 = authors[0]
        if isinstance(a0, dict):
            first_author = norm_text(a0.get("family") or a0.get("surname") or a0.get("last") or a0.get("name"))
        else:
            first_author = norm_text(f"{authors[0]}".split(",")[0])
    if title:
        if first_author and year and len(year) == 4:
            keys.append(("title_firstauthor_year", f"{title}|{first_author}|{year}"))
        keys.append(("title", title))
    return keys


def dedupe_papers(papers):
    """Merge paper records that represent the same work (union of identity keys).

    Returns ``(merged, merges, mapping)`` where:
      * ``merged``: canonical records with stable ``id``, ``merged_ids`` and
        ``identity_evidence`` documentation;
      * ``merges``: audit list of every multi-record merge;
      * ``mapping``: original id -> canonical id (for edge remapping).

    Deterministic: processing order and representative selection are stable.
    """
    if not isinstance(papers, list):
        return [], [], {}
    orig_ids = []
    for i, p in enumerate(papers):
        if isinstance(p, dict) and p.get("id"):
            orig_ids.append(str(p["id"]))
        else:
            orig_ids.append(f"raw{i}")
    parent = list(range(len(papers)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    keymap = {}
    for i, p in enumerate(papers):
        for kind, key in paper_identity_keys(p):
            if key in keymap:
                union(i, keymap[key])
            else:
                keymap[key] = i

    clusters = {}
    for i in range(len(papers)):
        clusters.setdefault(find(i), []).append(i)

    merged, merges, mapping = [], [], {}
    order = sorted(clusters.keys())
    for pos, root in enumerate(order, start=1):
        members = sorted(clusters[root])
        rep = _pick_representative([papers[m] for m in members])
        canonical = f"P{pos:05d}"
        for m in members:
            mapping[orig_ids[m]] = canonical
        merged_rec = dict(rep) if isinstance(rep, dict) else {}
        merged_rec["id"] = canonical
        merged_rec["merged_ids"] = [orig_ids[m] for m in members]
        merged_rec["n_merged"] = len(members)
        # keep identity documentation
        keys = []
        for m in members:
            for kind, key in paper_identity_keys(papers[m]):
                keys.append({"kind": kind, "value": key, "from": orig_ids[m]})
        merged_rec["identity_evidence"] = keys
        merged.append(merged_rec)
        if len(members) > 1:
            merges.append({
                "id": canonical,
                "members": [orig_ids[m] for m in members],
                "evidence": keys,
                "reason": "shared identity key(s)",
            })
    return merged, merges, mapping


def remap_edges(edges, mapping):
    """Rewrite ``source``/``target`` of edges using an id->canonical-id map.

    Unknown ids are left unchanged (never guessed). Returns a new list.
    """
    if not isinstance(edges, list) or not isinstance(mapping, dict):
        return edges or []
    out = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        rec = dict(e)
        for field in ("source", "target"):
            if field in rec and str(rec[field]) in mapping:
                rec[field] = mapping[str(rec[field])]
        out.append(rec)
    return out


def _pick_representative(records):
    """Choose the record with the most populated fields; tie-break by index."""
    if not records:
        return {}
    best = records[0]
    best_score = -1
    for r in records:
        if not isinstance(r, dict):
            continue
        score = sum(1 for v in r.values() if v not in (None, "", [], {}))
        if score > best_score:
            best, best_score = r, score
    return best


# --------------------------------------------------------------------------- #
# graph / ranking / paths
# --------------------------------------------------------------------------- #
def build_graph(edges):
    """Build undirected adjacency from directed edges.

    ``edges`` are dicts with ``source``/``target`` and optional ``relation``.
    Nodes without a resolvable id are skipped without crashing.
    """
    graph = {}
    if not isinstance(edges, list):
        return graph
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("source")
        tgt = e.get("target")
        if not src or not tgt:
            continue
        src, tgt = str(src), str(tgt)
        graph.setdefault(src, []).append({"node": tgt, "relation": e.get("relation", "cites"),
                                          "direction": "out"})
        graph.setdefault(tgt, []).append({"node": src, "relation": e.get("relation", "cites"),
                                          "direction": "in"})
    return graph


def shortest_paths(graph, seed_id, max_depth=3):
    """BFS from ``seed_id`` returning ``{node: {hops, path, relations}}``."""
    if not seed_id or seed_id not in graph:
        return {}
    dist = {seed_id: {"hops": 0, "path": [seed_id], "relations": []}}
    frontier = [seed_id]
    level = 0
    while frontier and level < max_depth:
        level += 1
        nxt = []
        for node in frontier:
            for nbr in sorted(graph.get(node, []), key=lambda x: x["node"]):
                if nbr["node"] not in dist:
                    prev = dist[node]
                    dist[nbr["node"]] = {
                        "hops": level,
                        "path": prev["path"] + [nbr["node"]],
                        "relations": prev["relations"] + [nbr["relation"]],
                    }
                    nxt.append(nbr["node"])
        frontier = nxt
    return dist


def _year_of(p):
    if not isinstance(p, dict):
        return None
    for key in ("year", "date", "published_date"):
        v = p.get(key)
        if v:
            m = re.search(r"(18|19|20)\d{2}", str(v))
            if m:
                return int(m.group(0))
    return None


def rank_papers(papers, graph, seed_id, now_year=None):
    """Score papers: distance from seed, citation degree, recency, source confidence.

    Not "highly cited == highly relevant": distance and source confidence are
    weighted more heavily than raw citation counts. Returns a sorted list of
    paper records augmented with ``rank`` and ``score``. Deterministic.
    """
    if not isinstance(papers, list):
        return []
    if now_year is None:
        now_year = datetime.datetime.now().year
    dist = shortest_paths(graph, seed_id, max_depth=5)
    scored = []
    for p in papers:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        d = dist.get(pid, {}).get("hops", None)
        if pid == seed_id:
            dist_score = 1.0
        else:
            dist_score = (1.0 / (1 + d)) if d is not None else 0.05
        deg = 0
        if pid in graph:
            deg = sum(1 for _ in graph[pid])
        year = _year_of(p)
        recency = 0.0
        if year is not None:
            recency = max(0.0, 1.0 - (now_year - year) / 30.0)
        conf = 1.0
        if isinstance(p.get("source_confidence"), (int, float)):
            conf = float(min(1.0, max(0.0, p["source_confidence"])))
        score = 0.45 * dist_score + 0.20 * min(1.0, deg / 10.0) + 0.15 * recency + 0.20 * conf
        rec = dict(p)
        rec["score"] = round(score, 6)
        scored.append(rec)
    scored.sort(key=lambda x: (-x["score"], str(x.get("id") or ""), str(x.get("title") or "")))
    for i, p in enumerate(scored, start=1):
        p["rank"] = i
    return scored


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #
def write_csv(path, fieldnames, rows):
    """Write CSV (UTF-8 with BOM for spreadsheet compatibility). Returns error or ''."""
    directory = os.path.dirname(os.path.abspath(path))
    if not ensure_dir(directory):
        return "mkdir_failed"
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return ""
    except OSError as exc:
        return f"write_failed:{exc}"


def _xml_esc(s):
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def render_svg(nodes, edges, seed_id, max_nodes=120):
    """Render a deterministic SVG citation graph (pure stdlib, no fonts needed).

    Layout: BFS depth rings around the seed, nodes sorted by id for stability.
    Returns SVG text; caller decides whether to write it.
    """
    graph = build_graph(edges)
    dist = shortest_paths(graph, seed_id, max_depth=6)
    nodes = [n for n in nodes if isinstance(n, dict) and n.get("id")]
    # order deterministically
    nodes.sort(key=lambda n: (dist.get(str(n["id"]), {}).get("hops", 99), str(n["id"])))
    if len(nodes) > max_nodes:
        nodes = nodes[:max_nodes]
    node_ids = [str(n["id"]) for n in nodes]

    cx, cy, ring_gap, node_r = 600.0, 400.0, 130.0, 22.0
    pos = {}
    for n in nodes:
        nid = str(n["id"])
        hops = dist.get(nid, {}).get("hops", 6)
        ring = min(hops, 6)
        radius = ring * ring_gap
        angle = 0.0
        if nid == seed_id:
            pos[nid] = (cx, cy)
        else:
            # stable angle: index within ring is unknown here, use hash-free ranking
            # place around circle using a deterministic sweep per ring below
            pass
    # assign ring members deterministically
    rings = {}
    for n in nodes:
        nid = str(n["id"])
        if nid == seed_id:
            continue
        hops = dist.get(nid, {}).get("hops", 6)
        ring = min(hops, 6)
        rings.setdefault(ring, []).append(nid)
    for ring, ids in rings.items():
        radius = ring * ring_gap
        n = len(ids)
        for j, nid in enumerate(ids):
            angle = 2 * math.pi * j / n - math.pi / 2
            pos[nid] = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))

    arrow = ('<marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" '
             'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
             '<path d="M 0 0 L 10 5 L 0 10 z" fill="#7a7a7a"/></marker>')
    body = []
    body.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" '
                f'viewBox="0 0 1200 800" font-family="sans-serif">')
    body.append('<rect width="1200" height="800" fill="#ffffff"/>')
    body.append('<defs>' + arrow + '</defs>')
    body.append(f'<title>citation-graph seed={_xml_esc(seed_id)}</title>')

    # edges
    drawn = set()
    edge_lines = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        a, b = str(e.get("source") or ""), str(e.get("target") or "")
        if not a or not b or a not in pos or b not in pos:
            continue
        key = tuple(sorted((a, b)))
        if key in drawn:
            continue
        drawn.add(key)
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        # arrow points from source (cites) to target
        rel = e.get("relation", "cites")
        style = "stroke:#b0b0b0;stroke-width:1.2"
        if rel == "direction_unknown":
            style = "stroke:#c9a227;stroke-width:1.2;stroke-dasharray:4,3"
        edge_lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" style="%s" marker-end="url(#arrow)"/>'
                          % (x1, y1, x2, y2, style))
    body.extend(edge_lines)

    # nodes
    for n in nodes:
        nid = str(n["id"])
        if nid not in pos:
            continue
        x, y = pos[nid]
        if nid == seed_id:
            fill = "#1f6feb"
        else:
            fill = "#2ea043" if dist.get(nid, {}).get("hops") == 1 else "#6a737d"
        label = _xml_esc(n.get("short_label") or nid)[:40]
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{node_r}" fill="{fill}" '
                    f'stroke="#333" stroke-width="1"/>')
        body.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" '
                    f'font-size="12" fill="#ffffff">{label}</text>')
    body.append("</svg>")
    return "\n".join(body)


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate_result(result):
    """Check a result dict for structural completeness. Returns ``(ok, issues)``."""
    issues = []
    if not isinstance(result, dict):
        return False, ["result is not a dict"]
    case_id = result.get("case_id")
    if not case_id or not isinstance(case_id, (str, int)):
        issues.append("case_id missing or empty")
    status = result.get("status")
    if status not in ALLOWED_STATUS:
        issues.append(f"status must be one of {sorted(ALLOWED_STATUS)}")
    if not isinstance(result.get("sources", []), list):
        issues.append("sources must be a list")
    if not isinstance(result.get("change_log", []), list):
        issues.append("change_log must be a list")
    for key in ("uncertainty", "pending_human", "errors", "degradations", "limitations"):
        v = result.get(key)
        if v is not None and not isinstance(v, (dict, list)):
            issues.append(f"{key} must be a dict/list when present")
    return (len(issues) == 0), issues