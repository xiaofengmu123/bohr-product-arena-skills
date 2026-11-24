#!/usr/bin/env python3
"""Evidence-marker verification for manuscript.md.

Checks
------
1. Every ``[S:key]`` marker in the manuscript resolves to a source whose
   ``ref_id``/``key``/``id`` appears in result.json ``sources`` or in
   material_index.json material ids.
2. Every ``[C:key]`` marker resolves to a reference definition line
   ``[C:key]: ...`` in the manuscript references section.
3. Reports defined-but-never-cited ``[C:key]`` entries (suspicious).
4. Reports bare numeric claims lacking any ``[S:]`` marker (heuristic).

Exit code 0 always (a report, not a gate); prints JSON to stdout and a
human-readable summary to stderr. Invalid inputs are reported, not raised.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from main import read_json_safe, read_text  # noqa: E402  (sibling helper)

S_MARK = re.compile(r"\[S:([A-Za-z0-9_.\-/]+)\]")
C_MARK = re.compile(r"\[C:([A-Za-z0-9_.\-]+)\]")
C_DEF = re.compile(r"^\s*\[C:([A-Za-z0-9_.\-]+)\]\s*:\s*(.+)$", re.MULTILINE)
# numbers that look like a claim (with optional units / % / CI)
NUM_CLAIM = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?(?:\s*(?:%|‰|mm|cm|µm|nm|kg|g|mg|µg|s|ms|min|h|L|mL|"
    r"µL|M|mM|µM|nM|°C|K|Hz|kHz|MHz|GHz|bp|nt|kDa|Da|n\s*=|CI|95%|±)\b)?",
    re.IGNORECASE,
)


def _src_keys(result):
    keys = set()
    for s in result.get("sources", []) or []:
        if not isinstance(s, dict):
            continue
        for k in ("ref_id", "key", "id"):
            if s.get(k):
                keys.add(str(s[k]))
    return keys


def _mat_keys(index_path):
    keys = set()
    obj, err = read_json_safe(index_path)
    if err or not isinstance(obj, dict):
        return keys
    for m in obj.get("materials", []) or []:
        if isinstance(m, dict) and m.get("id"):
            keys.add(str(m["id"]))
    return keys


def analyze(manuscript_text, result, mat_keys):
    s_used = set()
    c_used = set()
    c_defined = {}
    for line in manuscript_text.splitlines():
        m = C_DEF.match(line)
        if m:
            # a definition line is not a citation *use*
            c_defined[m.group(1)] = m.group(2).strip()
            continue
        s_used.update(S_MARK.findall(line))
        c_used.update(C_MARK.findall(line))

    src_keys = _src_keys(result)
    dangling_s = sorted(k for k in s_used if k not in src_keys and k not in mat_keys)
    dangling_c = sorted(k for k in c_used if k not in c_defined)
    unused_c = sorted(k for k in c_defined if k not in c_used)

    # heuristic: numbers outside reference definitions and [C:] lines
    bare_numbers = []
    lines = manuscript_text.splitlines()
    for i, line in enumerate(lines, 1):
        if C_DEF.search(line):
            continue
        if "[S:" in line:
            continue
        for m in NUM_CLAIM.finditer(line):
            bare_numbers.append({"line": i, "number": m.group(0).strip()})

    return {
        "s_used": sorted(s_used),
        "c_used": sorted(c_used),
        "c_defined": {k: c_defined[k] for k in sorted(c_defined)},
        "dangling_sources": dangling_s,
        "dangling_citations": dangling_c,
        "unused_citations": unused_c,
        "bare_numeric_claims": bare_numbers,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify evidence markers in manuscript")
    ap.add_argument("--draft", default="/app/output/manuscript.md")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--index", default="/app/work/material_index.json")
    args = ap.parse_args(argv)

    report = {"ok": False, "draft": args.draft}
    text, terr = read_text(args.draft)
    if text is None:
        report["error"] = "manuscript unreadable: %s" % terr
        print(json.dumps(report, ensure_ascii=False))
        return 0
    result, rerr = read_json_safe(args.result)
    if result is None or not isinstance(result, dict):
        report["error"] = "result.json unreadable: %s" % rerr
        print(json.dumps(report, ensure_ascii=False))
        return 0

    mat_keys = _mat_keys(args.index)
    a = analyze(text, result, mat_keys)
    a["ok"] = not (a["dangling_sources"] or a["dangling_citations"])
    report.update(a)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
