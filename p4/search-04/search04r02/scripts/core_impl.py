# -*- coding: utf-8 -*-
"""core_impl.py — deterministic citation-tracing helper library.

Pure Python standard library only. No network access, no RNG, no matplotlib
dependency. Every command is a small, idempotent operation on
/app/output/result.json (and /app/work/state.json). Repeated runs with the
same inputs produce identical data content; set CITATION_FIXED_TIME to make
byte-for-byte reproducible outputs (used by selftest.py).

Subcommands (dispatched by main.py):
  bootstrap  --input DIR --output DIR --work DIR
  checkpoint --output DIR --work DIR --phase ID --note TEXT
  dedupe     --output DIR
  rank       --output DIR
  path       --output DIR --source ID --target ID
  finalize   --output DIR [--work DIR]
  help

Design invariants
-----------------
1. result.json is always written atomically (temp file + os.replace); a crash
   never leaves a truncated file.
2. bootstrap never exits non-zero: if it cannot read /input it still writes a
   minimal valid skeleton with status "insufficient_evidence".
3. Every mutation appends an entry to change_log with time/action/reason.
4. Evidence-bearing items (papers, edges, clusters, paths, sources) always
   carry a locator and, where a verbatim source exists, a quote.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip larger files, record degradation

# Total execution budget: 900 seconds, 64 model requests.
BUDGET = {
    "seconds": 900,
    "model_requests": 64,
    "tokens_per_response": 16384,
}

# Phase plan with hard windows and per-phase request ceilings. P0 must land a
# partial skeleton in the first 3 minutes (180 s); afterwards only incremental
# edits to the same file are allowed.
PHASES = [
    {
        "id": "P0",
        "name": "bootstrap-skeleton",
        "window_seconds": [0, 180],
        "max_requests": 4,
        "checkpoint": "result.json partial skeleton + state.json",
        "goal": "scan input, echo output_contract, write partial skeleton",
    },
    {
        "id": "P1",
        "name": "seed-and-layer1",
        "window_seconds": [180, 540],
        "max_requests": 30,
        "checkpoint": "seed_key + one-hop forward/backward edges with evidence",
        "goal": "verify seed identity; expand one layer with locator+quote",
    },
    {
        "id": "P2",
        "name": "multi-layer-dedupe-parallel",
        "window_seconds": [540, 780],
        "max_requests": 20,
        "checkpoint": "layer 2-3 + dedupe + parallel works + roles",
        "goal": "multi-layer expansion, identity merge, parallel discovery",
    },
    {
        "id": "P3",
        "name": "rank-path-finalize",
        "window_seconds": [780, 900],
        "max_requests": 8,
        "checkpoint": "ranking + paths + finalize artifacts",
        "goal": "rank, shortest paths, validate, render report/CSV/SVG",
    },
]

# Keys that are treated as lists when echoing an unknown output_contract.
LISTY_KEYS = {
    "papers", "edges", "clusters", "paths", "sources", "results", "records",
    "citations", "references", "nodes", "links", "entries", "documents",
    "candidates", "publications", "items", "materials", "queries",
    "iterations", "versions", "alternatives", "related_works", "followups",
    "cited_by", "reference_list", "pruned",
}

SINGULAR_EXCEPTIONS = {"status", "address", "progress", "analysis", "notes",
                       "access", "biases", "gaps"}

ROLE_COLORS = {
    "seed": "#F5C542",
    "foundation": "#5B8DEF",
    "bridge": "#4CAF50",
    "hot": "#E0533D",
    "leaf": "#9E9E9E",
    "cluster": "#9C6BDE",
}

USAGE = """usage: main.py <subcommand> [options]

subcommands:
  bootstrap    scan /input, echo output_contract, write partial result.json skeleton
  checkpoint   record a phase/checkpoint in state.json and append change_log
  dedupe       merge paper identities (DOI -> arXiv -> title+author+year -> title)
  rank         rank papers by distance/flow/recency/confidence (not citation count alone)
  path         compute a shortest citation path between two paper ids
  finalize     validate result.json and render report.md, papers.csv, edges.csv, SVG
  help         show this message

run: main.py help   (each subcommand also supports --help)
"""


# ----------------------------------------------------------------------------
# Time helpers (injectable clock for determinism)
# ----------------------------------------------------------------------------

def iso_now():
    """ISO-8601 UTC timestamp; override with CITATION_FIXED_TIME for tests."""
    fixed = os.environ.get("CITATION_FIXED_TIME")
    if fixed:
        return fixed
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# Tolerant I/O
# ----------------------------------------------------------------------------

def read_text_tolerant(path):
    """Read a text file tolerating BOM / bad encodings.

    Returns (text, encoding_used, had_bom, error). Never raises.
    """
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        return None, None, False, str(e)
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace"), "utf-8-sig", True, None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc), enc, False, None
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", False, "fallback-latin-1"


def read_json_tolerant(path):
    """Parse a JSON file; returns (data, error). Never raises."""
    text, enc, _bom, err = read_text_tolerant(path)
    if err:
        return None, "read_error: %s" % err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, "json_error: %s" % e
    except Exception as e:  # pragma: no cover - defensive
        return None, "parse_error: %s" % e


def atomic_write_text(path, text):
    """Write text atomically (temp file + replace)."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_result(output_dir):
    """Load result.json if present; else return a bare skeleton dict."""
    path = os.path.join(output_dir, "result.json")
    data, err = read_json_tolerant(path)
    if data is None:
        return {
            "_load_error": err,
            "case_id": None,
            "status": "insufficient_evidence",
            "results": {"papers": [], "edges": [], "clusters": [], "paths": [],
                        "pruned": [], "ranking": []},
            "sources": [],
            "uncertainty": {"conflicts": [], "missing": [], "coverage_gaps": []},
            "change_log": [],
            "limitations": [],
            "pending_human": [],
            "errors": [],
            "degradations": [],
        }
    return data


def save_result(result, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    atomic_write_json(os.path.join(output_dir, "result.json"), result)


def ensure_results_structure(result):
    """Guarantee every expected container exists before mutation."""
    result.setdefault("results", {})
    r = result["results"]
    r.setdefault("papers", [])
    r.setdefault("edges", [])
    r.setdefault("clusters", [])
    r.setdefault("paths", [])
    r.setdefault("pruned", [])
    r.setdefault("ranking", [])
    result.setdefault("sources", [])
    result.setdefault("uncertainty", {"conflicts": [], "missing": [],
                                      "coverage_gaps": []})
    result.setdefault("change_log", [])
    result.setdefault("limitations", [])
    result.setdefault("pending_human", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("runtime", {})
    return result


def append_change(result, action, reason):
    entry = {"time": iso_now(), "action": action, "reason": reason}
    ensure_results_structure(result)
    result["change_log"].append(entry)
    return entry


def append_error(result, error, kind="error"):
    ensure_results_structure(result)
    if kind == "degradation":
        result["degradations"].append({"time": iso_now(), "detail": error})
    else:
        result["errors"].append({"time": iso_now(), "detail": error})


# ----------------------------------------------------------------------------
# Input discovery
# ----------------------------------------------------------------------------

def scan_input(input_dir):
    """Walk /input and build a deterministic material manifest.

    Returns (files, degradations). Never raises. Skips files over MAX_FILE_BYTES.
    """
    files = []
    degradations = []
    if not os.path.isdir(input_dir):
        return files, [{"locator": input_dir,
                        "detail": "input directory missing or not readable"}]
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as e:
        return files, [{"locator": input_dir, "detail": "listdir failed: %s" % e}]
    for name in names:
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        if size > MAX_FILE_BYTES:
            degradations.append({"locator": name,
                                 "detail": "file larger than 2MB, skipped",
                                 "size": size})
            continue
        files.append({"name": name, "path": path, "size": size,
                      "sha256": sha256_file(path)})
    return files, degradations


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def find_case_file(files, input_dir):
    """Locate case.json among discovered files; returns (data, path, error)."""
    candidates = [f for f in files if f["name"].lower() == "case.json"]
    if not candidates:
        candidates = [f for f in files if f["name"].lower().endswith(".json")]
    if not candidates:
        return None, None, "no JSON input file found (expected case.json)"
    # Prefer the file whose content has case_id or output_contract.
    best, best_path, best_err, best_score = None, None, None, -1
    for f in candidates:
        data, err = read_json_tolerant(f["path"])
        if data is None:
            if best_score < 0:
                best_err = err
            continue
        score = 0
        if isinstance(data, dict):
            if data.get("case_id"):
                score += 2
            if "output_contract" in data:
                score += 2
            score += 1
        if score > best_score:
            best, best_path, best_err, best_score = data, f["path"], None, score
    return best, best_path, best_err


def echo_contract(contract):
    """Echo every output_contract key with a sensible default value.

    Never drops or renames keys. Values are placeholder defaults that the agent
    overwrites with real data; the skeleton is only required to be valid.
    """
    if isinstance(contract, dict):
        out = {}
        for key, hint in contract.items():
            out[str(key)] = default_for_contract(str(key), hint)
        return out
    if isinstance(contract, list):
        return {str(name): None for name in contract if isinstance(name, str)}
    return {}


def default_for_contract(key, hint):
    if isinstance(hint, list):
        return []
    if isinstance(hint, dict):
        t = str(hint.get("type", "")).lower()
        if "list" in t or "array" in t:
            return []
        if t in ("str", "string", "text"):
            return ""
        if t in ("int", "integer", "float", "number"):
            return None
        if t == "bool":
            return False
        return None
    k = key.lower()
    if k in LISTY_KEYS:
        return []
    if k.endswith("s") and k not in SINGULAR_EXCEPTIONS:
        return []
    return None


def minimal_skeleton(output_dir, case_id=None, error=None):
    """Last-resort minimal valid result.json, used when bootstrap itself fails."""
    data = {
        "case_id": case_id,
        "status": "insufficient_evidence",
        "results": {"papers": [], "edges": [], "clusters": [], "paths": [],
                    "pruned": [], "ranking": []},
        "sources": [],
        "uncertainty": {"conflicts": [], "missing": [], "coverage_gaps": []},
        "change_log": [{"time": iso_now(), "action": "bootstrap",
                        "reason": "minimal skeleton (bootstrap degraded)"}],
        "limitations": [],
        "pending_human": [],
        "errors": [] if not error else [{"time": iso_now(), "detail": error}],
        "degradations": [],
        "runtime": {"started_at": iso_now(), "ended_at": None,
                    "seconds": None, "model_requests": None},
    }
    try:
        save_result(data, output_dir)
    except OSError as e:
        # Cannot write output; still do not crash.
        sys.stderr.write("minimal_skeleton write failed: %s\n" % e)
    return 0


# ----------------------------------------------------------------------------
# Normalization helpers (identity alignment)
# ----------------------------------------------------------------------------

def normalize_doi(doi):
    if not doi:
        return None
    d = str(doi).strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    d = re.sub(r"\s+", "", d)
    return d or None


def normalize_arxiv(arxiv):
    if not arxiv:
        return None
    a = str(arxiv).strip()
    m = re.search(r"(?:arxiv\.org/(?:abs|pdf)/)?(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?",
                  a, re.IGNORECASE)
    return m.group(1) if m else None


def normalize_title(title):
    if not title:
        return ""
    t = str(title).strip().lower()
    t = re.sub(r"[^\w\s]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def author_last(author):
    if not author:
        return ""
    a = str(author).strip()
    seg = a.split(",")[0].strip() if "," in a else a
    parts = seg.split()
    if not parts:
        return ""
    return re.sub(r"[^a-z]", "", parts[-1].lower())


def paper_key(p):
    """Identity key following priority DOI -> arXiv -> title+author+year -> title."""
    doi = normalize_doi(p.get("doi"))
    if doi:
        return ("doi", doi)
    arx = normalize_arxiv(p.get("arxiv"))
    if arx:
        return ("arxiv", arx)
    title = normalize_title(p.get("title"))
    last = author_last(p.get("first_author"))
    year = p.get("year")
    if title and last and year:
        return ("title_author_year", "%s|%s|%s" % (title, last, year))
    if title:
        return ("title", title)
    return None


def _pick(existing, incoming):
    return incoming if (incoming is not None and incoming != "" and
                        incoming != []) else existing


# ----------------------------------------------------------------------------
# Identity merging
# ----------------------------------------------------------------------------

def dedupe_papers(papers):
    """Merge paper identities and return (merged_papers, id_map, notes).

    id_map maps every old id to its canonical merged id.
    """
    groups = {}
    order = []
    for p in papers:
        pid = p.get("id")
        key = paper_key(p)
        if key is None:
            notes_key = ("__unkeyed__",)
        else:
            notes_key = key
        if notes_key not in groups:
            groups[notes_key] = []
            order.append(notes_key)
        groups[notes_key].append(p)

    merged = []
    id_map = {}
    notes = []
    for gkey in order:
        members = groups[gkey]
        canonical = dict(members[0])
        merged_from = [m.get("id") for m in members if m.get("id")]
        # Prefer the richest record as canonical body (most non-empty fields).
        best = max(members, key=lambda m: sum(1 for v in m.values()
                                              if v not in (None, "", [])))
        canonical = dict(best)
        for m in members:
            for field in ("title", "first_author", "year", "venue", "type",
                          "doi", "arxiv", "url", "notes", "role"):
                if canonical.get(field) in (None, "", []):
                    canonical[field] = m.get(field)
            srcs = set(canonical.get("source_ids") or [])
            srcs |= set(m.get("source_ids") or [])
            canonical["source_ids"] = sorted(srcs)
        canonical["merged_from"] = merged_from
        canonical["merge_key_type"] = (gkey[0] if gkey and gkey[0] !=
                                       "__unkeyed__" else None)
        canonical["merge_key_value"] = (gkey[1] if gkey and len(gkey) > 1 else None)
        new_id = "P%d" % len(merged)
        canonical["id"] = new_id
        for mid in merged_from:
            id_map[mid] = new_id
        if len(members) > 1:
            notes.append({"action": "merge", "canonical_id": new_id,
                          "members": merged_from, "key_type":
                          canonical["merge_key_type"], "key_value":
                          canonical["merge_key_value"]})
        merged.append(canonical)
    return merged, id_map, notes


def flag_possible_duplicates(merged, uncertainty):
    """Conservative second pass: same normalized title but different strong ids."""
    by_title = {}
    for p in merged:
        t = normalize_title(p.get("title"))
        if not t:
            continue
        by_title.setdefault(t, []).append(p)
    for t, group in by_title.items():
        if len(group) < 2:
            continue
        strong_ids = set()
        for p in group:
            for k in ("doi", "arxiv"):
                v = p.get(k)
                if v:
                    strong_ids.add((k, str(v).lower()))
        if len(strong_ids) > 1:
            uncertainty.setdefault("conflicts", []).append({
                "kind": "possible_duplicate_not_merged",
                "title": t,
                "paper_ids": [p.get("id") for p in group],
                "note": "same normalized title, different strong ids; left "
                        "separate pending human review (同名不同文 risk)",
            })


# ----------------------------------------------------------------------------
# Graph helpers
# ----------------------------------------------------------------------------

def compute_distances(papers, edges, seed_id):
    """Undirected BFS hop distance from seed_id to every paper id."""
    adj = {}
    ids = {p.get("id") for p in papers}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in ids and t in ids:
            adj.setdefault(s, set()).add(t)
            adj.setdefault(t, set()).add(s)
    dist = {}
    if seed_id in ids:
        frontier = [seed_id]
        dist[seed_id] = 0
        while frontier:
            nxt = []
            for n in frontier:
                for m in adj.get(n, ()):
                    if m not in dist:
                        dist[m] = dist[n] + 1
                        nxt.append(m)
            frontier = nxt
    return dist


def in_degree(edges):
    d = {}
    for e in edges:
        t = e.get("target")
        d[t] = d.get(t, 0) + 1
    return d


def shortest_path(edges, source, target):
    """BFS over 'source cites target' directed edges.

    Returns dict {start, end, hops, distance} or None if unreachable.
    Direction='unknown' edges are traversed but hops marked unknown.
    """
    adj = {}
    for e in edges:
        adj.setdefault(e.get("source"), []).append(e)
    from collections import deque
    q = deque([source])
    parent = {source: None}
    while q:
        cur = q.popleft()
        if cur == target:
            break
        for e in adj.get(cur, []):
            nxt = e.get("target")
            if nxt is not None and nxt not in parent:
                parent[nxt] = (cur, e)
                q.append(nxt)
    if target not in parent:
        return None
    hops = []
    cur = target
    while parent[cur] is not None:
        prev, e = parent[cur]
        hops.append({
            "from": prev, "to": cur, "relation": e.get("relation", "cites"),
            "direction": e.get("direction", "directed"),
            "locator": e.get("evidence_locator"),
            "quote": e.get("evidence_quote"),
        })
        cur = prev
    hops.reverse()
    return {"id": None, "start": source, "end": target, "hops": hops,
            "distance": len(hops), "method": "shortest_path"}


# ----------------------------------------------------------------------------
# Ranking
# ----------------------------------------------------------------------------

def rank_papers(papers, edges, seed_id):
    """Score = distance + flow + recency + confidence.

    'flow' is within-graph in-degree (connectivity), NOT external citation
    count. High citation count is explicitly not treated as high relevance.
    """
    dist = compute_distances(papers, edges, seed_id)
    indeg = in_degree(edges)
    conf_score = {"high": 1.0, "medium": 0.6, "low": 0.3, "unknown": 0.2}
    scored = []
    for p in papers:
        pid = p.get("id")
        d = dist.get(pid)
        d_norm = (1.0 / (1.0 + d)) if d is not None else 0.05
        flow = float(indeg.get(pid, 0))
        flow_norm = min(flow / 5.0, 1.0)  # saturates quickly; weak signal
        year = p.get("year")
        recency = 0.0
        if isinstance(year, (int, float)):
            recency = max(0.0, min((float(year) - 2000.0) / 30.0, 1.0))
        conf = conf_score.get(str(p.get("confidence", "unknown")).lower(), 0.2)
        # weights: distance dominates, flow small, recency small, confidence small
        score = (0.50 * d_norm + 0.15 * flow_norm + 0.15 * recency + 0.20 * conf)
        scored.append({
            "id": pid,
            "score": round(score, 4),
            "components": {
                "distance": d, "distance_score": round(d_norm, 4),
                "in_graph_in_degree": indeg.get(pid, 0),
                "flow_score": round(flow_norm, 4),
                "recency_score": round(recency, 4),
                "confidence": p.get("confidence", "unknown"),
                "confidence_score": round(conf, 4),
            },
            "rationale": "distance-weighted; citation count used only as "
                         "within-graph connectivity, not external popularity",
        })
    scored.sort(key=lambda x: (-x["score"], x["id"]))
    return scored


# ----------------------------------------------------------------------------
# Subcommand implementations
# ----------------------------------------------------------------------------

def bootstrap(input_dir, output_dir, work_dir):
    """Step 0: scan input, echo contract, write partial skeleton. Always exits 0."""
    try:
        files, degradations = scan_input(input_dir)
        case, case_path, case_err = find_case_file(files, input_dir)

        case_id = None
        contract = {}
        if isinstance(case, dict):
            case_id = case.get("case_id") or case.get("id")
            contract = case.get("output_contract") or {}
        echoed = echo_contract(contract)

        result = {
            "case_id": case_id,
            "status": "partial",
            "seed": None,
            "seed_key": {"type": None, "value": None, "confidence": None},
            "research_question": None,
            "results": {"papers": [], "edges": [], "clusters": [], "paths": [],
                        "pruned": [], "ranking": []},
            "sources": [],
            "uncertainty": {"conflicts": [], "missing": [], "coverage_gaps": []},
            "change_log": [],
            "limitations": [],
            "pending_human": [],
            "errors": [],
            "degradations": [],
            "runtime": {"started_at": iso_now(), "ended_at": None,
                        "seconds": None, "model_requests": None,
                        "budget": BUDGET, "phases": PHASES, "current_phase":
                        "P0"},
        }
        # echo output_contract fields at top level (placeholder defaults)
        for k, v in echoed.items():
            result[k] = v
        append_change(result, "bootstrap",
                      "partial skeleton landed in first 3 minutes; contract "
                      "keys echoed with placeholder defaults")

        for d in degradations:
            append_error(result, d["detail"], kind="degradation")
        if case_err:
            append_error(result, case_err)
        if case_id is None:
            result["status"] = "insufficient_evidence"
            result["pending_human"].append({
                "issue": "case_id missing", "detail":
                "no case.json with case_id found; cannot anchor a seed"})
        if not files:
            result["status"] = "insufficient_evidence"
            result["limitations"].append(
                "input directory empty; no material to trace")

        # Material manifest for auditability.
        result["_input_manifest"] = [
            {"name": f["name"], "size": f["size"], "sha256": f["sha256"]}
            for f in files]

        save_result(result, output_dir)

        # State file for phase machine.
        state = {
            "case_id": case_id,
            "started_at": iso_now(),
            "budget": BUDGET,
            "phases": PHASES,
            "current_phase": "P0",
            "checkpoint_count": 1,
            "last_checkpoint_at": iso_now(),
            "material_count": len(files),
        }
        os.makedirs(work_dir, exist_ok=True)
        atomic_write_json(os.path.join(work_dir, "state.json"), state)
        return 0
    except Exception as e:  # noqa: BLE001 - bootstrap must never crash
        sys.stderr.write("bootstrap exception: %s\n" % e)
        try:
            return minimal_skeleton(output_dir, error="bootstrap exception: %s" % e)
        except Exception:
            return 0


def checkpoint(output_dir, work_dir, phase, note):
    """Record an incremental checkpoint: bump phase, append change_log."""
    result = load_result(output_dir)
    ensure_results_structure(result)
    append_change(result, "checkpoint:%s" % phase,
                  note or "incremental edit checkpoint")
    result.setdefault("runtime", {})
    result["runtime"]["current_phase"] = phase
    result["runtime"]["last_checkpoint_at"] = iso_now()
    save_result(result, output_dir)

    state_path = os.path.join(work_dir, "state.json")
    state, _err = read_json_tolerant(state_path)
    if not isinstance(state, dict):
        state = {}
    state["current_phase"] = phase
    state["last_checkpoint_at"] = iso_now()
    state["checkpoint_count"] = state.get("checkpoint_count", 0) + 1
    os.makedirs(work_dir, exist_ok=True)
    atomic_write_json(state_path, state)
    return 0


def dedupe(output_dir):
    """Merge paper identities in place; remap edges/clusters/paths; flag dupes."""
    result = load_result(output_dir)
    ensure_results_structure(result)
    papers = result["results"]["papers"]
    if not papers:
        append_change(result, "dedupe", "no papers to dedupe")
        save_result(result, output_dir)
        return 0
    merged, id_map, notes = dedupe_papers(papers)
    flag_possible_duplicates(merged, result["uncertainty"])
    for note in notes:
        note["time"] = iso_now()
    result["results"]["papers"] = merged
    for e in result["results"]["edges"]:
        for field in ("source", "target"):
            if e.get(field) in id_map:
                e[field] = id_map[e[field]]
    for c in result["results"]["clusters"]:
        c["member_ids"] = sorted({id_map.get(m, m) for m in
                                  c.get("member_ids", [])})
    for pth in result["results"]["paths"]:
        for h in pth.get("hops", []):
            for field in ("from", "to"):
                if h.get(field) in id_map:
                    h[field] = id_map[h[field]]
        for field in ("start", "end"):
            if pth.get(field) in id_map:
                pth[field] = id_map[pth[field]]
    append_change(result, "dedupe",
                  "merged %d paper records into %d identities" %
                  (len(papers), len(merged)))
    if notes:
        result["results"].setdefault("merge_events", [])
        result["results"]["merge_events"].extend(notes)
    save_result(result, output_dir)
    return 0


def rank(output_dir):
    """Compute and store the distance-weighted ranking."""
    result = load_result(output_dir)
    ensure_results_structure(result)
    papers = result["results"]["papers"]
    edges = result["results"]["edges"]
    seed_id = None
    seed = result.get("seed")
    if isinstance(seed, dict):
        seed_id = seed.get("id")
    if seed_id is None and papers:
        # fallback: the paper with type 'seed'
        for p in papers:
            if p.get("type") == "seed":
                seed_id = p.get("id")
                break
    ranking = rank_papers(papers, edges, seed_id)
    result["results"]["ranking"] = ranking
    append_change(result, "rank",
                  "ranked %d papers (distance-weighted; seed_id=%s)" %
                  (len(ranking), seed_id))
    save_result(result, output_dir)
    return 0


def path(output_dir, source, target):
    """Append a shortest path between two paper ids (dedup by endpoints)."""
    result = load_result(output_dir)
    ensure_results_structure(result)
    edges = result["results"]["edges"]
    pth = shortest_path(edges, source, target)
    if pth is None:
        append_change(result, "path", "no path %s -> %s in current graph" %
                      (source, target))
        result["uncertainty"].setdefault("missing", []).append({
            "kind": "unreachable_path", "source": source, "target": target,
            "note": "no citation chain found in current edge set"})
        save_result(result, output_dir)
        return 0
    existing = {tuple(sorted([p.get("start"), p.get("end")]))
                for p in result["results"]["paths"]
                if p.get("start") and p.get("end")}
    if tuple(sorted([source, target])) not in existing:
        pth["id"] = "PATH%d" % len(result["results"]["paths"])
        result["results"]["paths"].append(pth)
        append_change(result, "path", "shortest path %s -> %s (%d hops)" %
                      (source, target, pth["distance"]))
    else:
        append_change(result, "path", "path %s -> %s already present" %
                      (source, target))
    save_result(result, output_dir)
    return 0


# ----------------------------------------------------------------------------
# Rendering (report, CSV, SVG)
# ----------------------------------------------------------------------------

def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_papers_csv(papers):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "title", "first_author", "year", "venue", "type", "doi",
                "arxiv", "url", "role", "source_ids", "merge_key_type",
                "merge_key_value", "merged_from", "confidence", "notes"])
    for p in papers:
        w.writerow([
            p.get("id", ""), p.get("title", ""), p.get("first_author", ""),
            p.get("year", ""), p.get("venue", ""), p.get("type", ""),
            p.get("doi", ""), p.get("arxiv", ""), p.get("url", ""),
            p.get("role", ""), ";".join(p.get("source_ids") or []),
            p.get("merge_key_type", ""), p.get("merge_key_value", ""),
            ";".join(p.get("merged_from") or []), p.get("confidence", ""),
            p.get("notes", ""),
        ])
    return out.getvalue()


def render_edges_csv(edges):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "source", "target", "relation", "direction",
                "source_label", "target_label", "evidence_locator",
                "evidence_quote", "source_id", "confidence"])
    for e in edges:
        w.writerow([
            e.get("id", ""), e.get("source", ""), e.get("target", ""),
            e.get("relation", "cites"), e.get("direction", "directed"),
            e.get("source_label", ""), e.get("target_label", ""),
            e.get("evidence_locator", ""), e.get("evidence_quote", ""),
            e.get("source_id", ""), e.get("confidence", ""),
        ])
    return out.getvalue()


def render_report(result):
    r = result.get("results", {})
    lines = []
    lines.append("# 引文追踪报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- research_question: %s" % (result.get("research_question") or
                                              "（未填写）"))
    sk = result.get("seed_key") or {}
    lines.append("- seed_key: %s %s (confidence=%s)" %
                 (sk.get("type"), sk.get("value"), sk.get("confidence")))
    lines.append("")
    lines.append("## 结果概览")
    lines.append("")
    lines.append("- papers: %d" % len(r.get("papers", [])))
    lines.append("- edges: %d" % len(r.get("edges", [])))
    lines.append("- clusters: %d" % len(r.get("clusters", [])))
    lines.append("- paths: %d" % len(r.get("paths", [])))
    lines.append("- ranking: %d" % len(r.get("ranking", [])))
    lines.append("")
    if r.get("papers"):
        lines.append("## 文献")
        lines.append("")
        lines.append("| id | 第一作者/年份 | 类型 | 角色 | 标题 |")
        lines.append("|---|---|---|---|---|")
        for p in r["papers"]:
            lines.append("| %s | %s %s | %s | %s | %s |" %
                         (p.get("id"), p.get("first_author", ""),
                          p.get("year", ""), p.get("type", ""),
                          p.get("role", ""), (p.get("title") or "")[:60]))
        lines.append("")
    if r.get("edges"):
        lines.append("## 引用边")
        lines.append("")
        lines.append("每条边方向统一为 source cites target；direction 标 "
                     "directed 或 unknown。")
        lines.append("")
        lines.append("| id | source | target | direction | evidence |")
        lines.append("|---|---|---|---|---|")
        for e in r["edges"]:
            lines.append("| %s | %s | %s | %s | %s |" %
                         (e.get("id"), e.get("source"), e.get("target"),
                          e.get("direction"), e.get("evidence_locator", "")))
        lines.append("")
    if r.get("paths"):
        lines.append("## 证据路径")
        lines.append("")
        for p in r["paths"]:
            hop_desc = " -> ".join(["%s" % h.get("from") for h in p["hops"]] +
                                   [p.get("end")])
            lines.append("- %s (%d hops): %s" %
                         (p.get("id"), p.get("distance"), hop_desc))
        lines.append("")
    un = result.get("uncertainty", {})
    if un.get("conflicts") or un.get("missing") or un.get("coverage_gaps"):
        lines.append("## 不确定性与缺口")
        lines.append("")
        lines.append("- conflicts: %d" % len(un.get("conflicts", [])))
        lines.append("- missing: %d" % len(un.get("missing", [])))
        lines.append("- coverage_gaps: %d" % len(un.get("coverage_gaps", [])))
        lines.append("")
    if result.get("pending_human"):
        lines.append("## 待人工确认")
        lines.append("")
        for it in result["pending_human"]:
            if isinstance(it, dict):
                lines.append("- %s" % (it.get("issue") or it.get("detail") or
                                       json.dumps(it, ensure_ascii=False)))
            else:
                lines.append("- %s" % it)
        lines.append("")
    if result.get("limitations"):
        lines.append("## 局限")
        lines.append("")
        for it in result["limitations"]:
            lines.append("- %s" % it)
        lines.append("")
    if result.get("change_log"):
        lines.append("## 变更记录（最近 10 条）")
        lines.append("")
        for c in result["change_log"][-10:]:
            lines.append("- %s %s — %s" %
                         (c.get("time"), c.get("action"), c.get("reason")))
        lines.append("")
    return "\n".join(lines) + "\n"


def render_svg(result):
    papers = result.get("results", {}).get("papers", [])
    edges = result.get("results", {}).get("edges", [])
    seed_id = None
    seed = result.get("seed")
    if isinstance(seed, dict):
        seed_id = seed.get("id")
    if seed_id is None:
        for p in papers:
            if p.get("type") == "seed":
                seed_id = p.get("id")
                break
    dist = compute_distances(papers, edges, seed_id)

    if not papers:
        return _svg_empty()

    # group by distance
    layers = {}
    for p in papers:
        d = dist.get(p.get("id"))
        dkey = d if d is not None else 99
        layers.setdefault(dkey, []).append(p)
    positions = {}
    x_step = 240.0
    y_step = 86.0
    base_x = 70.0
    for dkey in sorted(layers):
        group = layers[dkey]
        n = len(group)
        x = base_x + (dkey if dkey != 99 else 4) * x_step
        for i, p in enumerate(group):
            y = 40 + y_step * (i - (n - 1) / 2.0)
            positions[p.get("id")] = (x, y)

    max_x = base_x + 4 * x_step + 180
    max_y = 40 + max(len(g) for g in layers.values()) * y_step + 40

    svg = []
    svg.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" '
               'height="%d" viewBox="0 0 %d %d">' % (int(max_x), int(max_y),
                                                     int(max_x), int(max_y)))
    svg.append('<defs>')
    svg.append('<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
               'markerWidth="7" markerHeight="7" orient="auto-start-reverse">')
    svg.append('<path d="M 0 0 L 10 5 L 0 10 z" fill="#666"/>')
    svg.append('</marker>')
    svg.append('</defs>')
    svg.append('<rect x="0" y="0" width="%d" height="%d" fill="#fafafa"/>' %
               (int(max_x), int(max_y)))
    svg.append('<text x="12" y="20" font-family="sans-serif" font-size="13" '
               'fill="#333">seed: %s | nodes=%d | edges=%d</text>' %
               (esc(seed_id or "?"), len(papers), len(edges)))

    # edges first (under nodes)
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s not in positions or t not in positions:
            continue
        x1, y1 = positions[s]
        x2, y2 = positions[t]
        directed = e.get("direction", "directed") == "directed"
        dash = "" if directed else ' stroke-dasharray="5,4"'
        arrow = ' marker-end="url(#arrow)"' if directed else ""
        svg.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                   'stroke="#999" stroke-width="1.2"%s%s/>' %
                   (x1 + 10, y1, x2 - 10, y2, dash, arrow))

    # nodes
    for p in papers:
        pid = p.get("id")
        x, y = positions[pid]
        color = ROLE_COLORS.get(p.get("role", "leaf"), ROLE_COLORS["leaf"])
        label = "%s %s %s" % (pid, p.get("first_author", ""),
                              p.get("year", ""))
        label = label.strip()[:24]
        svg.append('<rect x="%.1f" y="%.1f" width="130" height="30" rx="6" '
                   'fill="%s" stroke="#333" stroke-width="1"/>' %
                   (x - 65, y - 15, color))
        svg.append('<text x="%.1f" y="%.1f" font-family="sans-serif" '
                   'font-size="11" fill="#111" text-anchor="middle">%s</text>' %
                   (x, y + 4, esc(label)))
    svg.append('</svg>')
    return "\n".join(svg)


def _svg_empty():
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120" '
            'viewBox="0 0 320 120">'
            '<rect x="0" y="0" width="320" height="120" fill="#fafafa"/>'
            '<text x="16" y="36" font-family="sans-serif" font-size="14" '
            'fill="#333">no papers in result.json</text>'
            '<text x="16" y="60" font-family="sans-serif" font-size="12" '
            'fill="#666">run dedupe / populate results before finalize</text>'
            '</svg>')


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------

def validate_result(result):
    issues = []
    if not result.get("case_id"):
        issues.append("missing case_id")
    if result.get("status") not in STATUS_VALUES:
        issues.append("status not in %s" % (STATUS_VALUES,))
    r = result.get("results")
    if not isinstance(r, dict):
        issues.append("results is not a dict")
    else:
        for key in ("papers", "edges", "clusters", "paths"):
            if key not in r:
                issues.append("results.%s missing" % key)
        # check edges reference existing paper ids
        ids = {p.get("id") for p in r.get("papers", [])}
        for e in r.get("edges", []):
            for field in ("source", "target"):
                if e.get(field) not in ids:
                    issues.append("edge %s references unknown %s=%s" %
                                  (e.get("id"), field, e.get(field)))
    for key in ("sources", "uncertainty", "change_log"):
        if key not in result:
            issues.append("missing %s" % key)
    return issues


def finalize(output_dir, work_dir=None):
    """Validate result.json and render report.md, papers.csv, edges.csv, SVG."""
    result = load_result(output_dir)
    ensure_results_structure(result)
    issues = validate_result(result)
    result["runtime"]["ended_at"] = iso_now()
    if work_dir:
        state_path = os.path.join(work_dir, "state.json")
        state, _err = read_json_tolerant(state_path)
        if isinstance(state, dict):
            result["runtime"]["model_requests"] = state.get("model_requests")
    result["_validation"] = {"issues": issues, "ok": not issues}
    append_change(result, "finalize",
                  "validation: %d issue(s); rendered artifacts" % len(issues))
    save_result(result, output_dir)

    os.makedirs(output_dir, exist_ok=True)
    atomic_write_text(os.path.join(output_dir, "report.md"),
                      render_report(result))
    atomic_write_text(os.path.join(output_dir, "papers.csv"),
                      render_papers_csv(result["results"]["papers"]))
    atomic_write_text(os.path.join(output_dir, "edges.csv"),
                      render_edges_csv(result["results"]["edges"]))
    atomic_write_text(os.path.join(output_dir, "citation_graph.svg"),
                      render_svg(result))

    print("finalize: status=%s papers=%d edges=%d issues=%d" %
          (result.get("status"), len(result["results"]["papers"]),
           len(result["results"]["edges"]), len(issues)))
    for i in issues:
        print("  validation: %s" % i)
    return 0


# ----------------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------------

def cmd_bootstrap(rest):
    ap = argparse.ArgumentParser(prog="main.py bootstrap")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    a = ap.parse_args(rest)
    try:
        return bootstrap(a.input, a.output, a.work)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("bootstrap crashed: %s\n" % e)
        return minimal_skeleton(a.output, error="bootstrap crashed: %s" % e)


def cmd_checkpoint(rest):
    ap = argparse.ArgumentParser(prog="main.py checkpoint")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--phase", default="P1")
    ap.add_argument("--note", default="")
    a = ap.parse_args(rest)
    return checkpoint(a.output, a.work, a.phase, a.note)


def cmd_dedupe(rest):
    ap = argparse.ArgumentParser(prog="main.py dedupe")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    a = ap.parse_args(rest)
    return dedupe(a.output)


def cmd_rank(rest):
    ap = argparse.ArgumentParser(prog="main.py rank")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    a = ap.parse_args(rest)
    return rank(a.output)


def cmd_path(rest):
    ap = argparse.ArgumentParser(prog="main.py path")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    a = ap.parse_args(rest)
    return path(a.output, a.source, a.target)


def cmd_finalize(rest):
    ap = argparse.ArgumentParser(prog="main.py finalize")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=None)
    a = ap.parse_args(rest)
    return finalize(a.output, a.work)


HANDLERS = {
    "bootstrap": cmd_bootstrap,
    "checkpoint": cmd_checkpoint,
    "dedupe": cmd_dedupe,
    "rank": cmd_rank,
    "path": cmd_path,
    "finalize": cmd_finalize,
}


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    handler = HANDLERS.get(cmd)
    if handler is None:
        sys.stderr.write("unknown subcommand: %r\n%s" % (cmd, USAGE))
        return 2
    return handler(rest)


if __name__ == "__main__":
    sys.exit(main())
