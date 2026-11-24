#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static checks for a mind-map JSON: uniqueness, cycles, edge refs, evidence.

Standard library only. Always exits 0 unless the input file is unreadable
(exit 2); findings are reported as JSON so the caller can merge them into
result.json uncertainty/degradations/pending_human honestly.

Usage:
  python3 -B scripts/evidence_check.py --input /app/work/mindmap.json \
      --materials-dir /input --report /app/output/evidence_report.json
"""
import argparse
import json
import sys
from pathlib import Path

EVIDENCE_KINDS = {"evidence", "conclusion", "method", "result", "risk"}


def read_json(path):
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: bad JSON: {exc}", file=sys.stderr)
        sys.exit(2)


def collect_files(base):
    files = []
    try:
        for p in sorted(Path(base).rglob("*")):
            if p.is_file():
                files.append(str(p))
    except OSError:
        pass
    return files


def norm_locator(loc):
    """Normalize a locator to its file part for existence checking."""
    loc = str(loc or "")
    for sep in ("#", ":"):
        if sep in loc:
            loc = loc.split(sep, 1)[0]
    return loc.strip()


def check(mindmap, materials_dir=None):
    errors = []
    warnings = []
    conflicts = []
    unverified = []
    nodes = mindmap.get("nodes") or []
    edges = mindmap.get("edges") or []

    ids = set()
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            errors.append({"item": f"node[{i}]", "problem": "not an object"})
            continue
        nid = str(n.get("id", f"n{i}"))
        if nid in ids:
            errors.append({"item": nid, "problem": "duplicate node id"})
        ids.add(nid)
        if n.get("kind") in EVIDENCE_KINDS and not n.get("evidence") and not n.get("inferred"):
            warnings.append({"item": nid, "problem": "fact-bearing node lacks evidence"})
            unverified.append(nid)
        ev = n.get("evidence") or []
        seen_quotes = {}
        for e in ev:
            if not isinstance(e, dict) or not e.get("locator") or not e.get("quote"):
                warnings.append({"item": nid, "problem": "evidence entry missing locator or quote"})
                continue
            loc = e.get("locator")
            quote = str(e.get("quote"))
            if quote in seen_quotes and seen_quotes[quote] != loc:
                conflicts.append({
                    "item": nid,
                    "type": "source_conflict",
                    "locators": [seen_quotes[quote], loc],
                    "quote": quote[:200],
                })
            seen_quotes[quote] = loc

    # parent references and cycles
    for n in nodes:
        if not isinstance(n, dict):
            continue
        pid = n.get("parent")
        nid = str(n.get("id", ""))
        if pid is not None and str(pid) not in ids:
            errors.append({"item": nid, "problem": f"parent {pid!r} not found"})
        if pid is not None and str(pid) == nid:
            errors.append({"item": nid, "problem": "self-parent"})

    # cycle detection via DFS
    adj = {}
    for n in nodes:
        if isinstance(n, dict):
            nid = str(n.get("id", ""))
            pid = n.get("parent")
            if pid is not None and str(pid) in ids:
                adj.setdefault(str(pid), []).append(nid)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in ids}
    cycle = []

    def dfs(u, path):
        color[u] = GRAY
        for v in adj.get(u, []):
            if color[v] == GRAY:
                cycle.append({"from": u, "to": v})
            elif color[v] == WHITE:
                dfs(v, path)
        color[u] = BLACK

    for nid in ids:
        if color[nid] == WHITE:
            dfs(nid, [])
    if cycle:
        errors.append({"item": "parent_graph", "problem": "cycle detected", "cycle": cycle})

    # edges reference existing nodes
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            errors.append({"item": f"edge[{i}]", "problem": "not an object"})
            continue
        for end in ("from", "to"):
            if str(e.get(end, "")) not in ids:
                errors.append({"item": e.get("id", f"edge[{i}]"),
                               "problem": f"edge {end} node not found"})

    locators = set()
    for n in nodes:
        if isinstance(n, dict):
            for e in n.get("evidence") or []:
                if isinstance(e, dict) and e.get("locator"):
                    locators.add(str(e["locator"]))
    for e in edges:
        if isinstance(e, dict):
            for ev in e.get("evidence") or []:
                if isinstance(ev, dict) and ev.get("locator"):
                    locators.add(str(ev["locator"]))

    available = set(collect_files(materials_dir)) if materials_dir else set()
    available_norm = [a.replace("\\", "/") for a in available]
    missing_locators = []
    for loc in sorted(locators):
        f = norm_locator(loc).replace("\\", "/")
        if materials_dir and f and not any(
            a == f or a.endswith("/" + f) for a in available_norm
        ):
            missing_locators.append(loc)
            warnings.append({"item": loc, "problem": "locator file not found under materials dir"})

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "errors": errors,
        "warnings": warnings,
        "conflicts": conflicts,
        "unverified": unverified,
        "missing_locators": missing_locators,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--materials-dir", default=None)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    mindmap = read_json(args.input)
    report = check(mindmap, args.materials_dir)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "nodes": report["node_count"],
        "edges": report["edge_count"],
        "errors": len(report["errors"]),
        "warnings": len(report["warnings"]),
        "conflicts": len(report["conflicts"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
