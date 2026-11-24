#!/usr/bin/env python3
"""Verify [S:]/[C:] evidence markers and chapter/figure/table consistency.

Usage:
    python3 scripts/evidence_map.py --draft /app/output/manuscript.md \
        --index /app/work/material_index.json --work-dir /app/work

Outputs:
    /app/work/evidence_map.json   (machine-readable, deterministic)
    /app/work/evidence_report.md  (human-readable checklist)

Exit code is 0 even when issues are found (issues are reported, not fatal);
the calling agent decides how to fix them.
"""
import argparse
import json
import os
import re
import sys

S_RE = re.compile(r"\[S:([^\]\s]+)\]")
C_RE = re.compile(r"\[C:([^\]\s]+)\]")
DEF_RE = re.compile(r"^[-*+ ]*\[C:([^\]\n]+)\]:", re.MULTILINE)
MISSING_RE = re.compile(r"\[(缺失|待确认|冲突待确认|无法解析)\]")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
FIGURE_RE = re.compile(r"\b(?:Figure|Fig\.?)\s+(\d+)", re.IGNORECASE)
TABLE_RE = re.compile(r"\bTable\s+(\d+)", re.IGNORECASE)
NUM_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:%|[μµmµnm]|mol|mg|kg|s|min|h|day|ms|Hz|kHz|MHz|GHz|L|mL|V|A|W|C|K|Pa|bar|rpm|×|x)?\b")


def read_text_loose(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


def load_json_loose(path):
    text = read_text_loose(path)
    if text is None:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def split_definitions(draft):
    def_keys = set()
    for m in DEF_RE.finditer(draft):
        def_keys.add(m.group(1).strip())
    body = DEF_RE.sub("", draft)
    return body, def_keys


def material_ids(index):
    return {m.get("id") for m in index.get("materials", [])}


def quote_for(index, token):
    for m in index.get("materials", []):
        for kc in m.get("key_claims", []):
            if isinstance(kc, dict) and kc.get("locator") == token:
                return kc.get("quote")
    # fall back to material-level claim if no sub-locator matches
    base = token.split("#", 1)[0]
    for m in index.get("materials", []):
        if m.get("id") == base:
            for kc in m.get("key_claims", []):
                if isinstance(kc, dict) and kc.get("locator") == base:
                    return kc.get("quote")
    return None


def near_source(draft, start, end, radius=160):
    before = draft[max(0, start - radius):start]
    after = draft[end:end + radius]
    return ("[S:" in before) or ("[S:" in after)


def build_report(draft_path, index_path):
    draft = read_text_loose(draft_path)
    index = load_json_loose(index_path)
    report = {
        "draft": draft_path,
        "material_index": index_path,
        "draft_present": draft is not None,
        "headings": [],
        "figure_numbers": [],
        "table_numbers": [],
        "source_markers": [],
        "citation_usage": [],
        "dangling_sources": [],
        "dangling_citations": [],
        "unused_citations": [],
        "missing_markers": [],
        "review_numbers": [],
        "status": "ok",
    }
    if draft is None:
        report["status"] = "draft_missing"
        return report

    report["headings"] = [h.strip() for h in HEADING_RE.findall(draft)]
    report["figure_numbers"] = sorted(set(int(x) for x in FIGURE_RE.findall(draft)))
    report["table_numbers"] = sorted(set(int(x) for x in TABLE_RE.findall(draft)))

    ids = material_ids(index)

    # --- source markers ---
    for m in S_RE.finditer(draft):
        token = m.group(1).strip()
        base = token.split("#", 1)[0]
        resolved = base in ids
        report["source_markers"].append({
            "token": token,
            "material_id": base,
            "resolved": resolved,
            "quote": quote_for(index, token),
        })
        if not resolved:
            report["dangling_sources"].append(token)

    # --- citation usage vs definitions ---
    body, def_keys = split_definitions(draft)
    used_keys = set(C_RE.findall(body))
    for key in sorted(def_keys | used_keys):
        report["citation_usage"].append({
            "key": key,
            "defined": key in def_keys,
            "used": key in used_keys,
        })
    report["dangling_citations"] = sorted(used_keys - def_keys)
    report["unused_citations"] = sorted(def_keys - used_keys)

    # --- missing/conflict markers (informational, expected when honest) ---
    seen = set()
    for m in MISSING_RE.finditer(draft):
        snippet = draft[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
        key = (m.group(1), snippet)
        if key not in seen:
            seen.add(key)
            report["missing_markers"].append({
                "kind": m.group(1),
                "context": snippet,
            })

    # --- numbers lacking a nearby [S:] marker (advisory review list) ---
    for m in NUM_RE.finditer(draft):
        if not near_source(draft, m.start(), m.end()):
            report["review_numbers"].append(m.group(0))
    report["review_numbers"] = sorted(set(report["review_numbers"]))

    if report["dangling_sources"] or report["dangling_citations"]:
        report["status"] = "issues_found"
    return report


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def render_markdown(report):
    lines = []
    lines.append("# Evidence map report\n")
    lines.append("- draft: `%s`" % report["draft"])
    lines.append("- status: **%s**" % report["status"])
    lines.append("- headings: %s" % (", ".join(report["headings"]) or "(none)"))
    lines.append("- figures referenced: %s" % (report["figure_numbers"] or "(none)"))
    lines.append("- tables referenced: %s" % (report["table_numbers"] or "(none)"))
    lines.append("")
    lines.append("## Dangling sources (fail)")
    for s in report["dangling_sources"]:
        lines.append("- [ ] `[S:%s]` not resolvable to a material id" % s)
    if not report["dangling_sources"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Dangling citations (fail)")
    for c in report["dangling_citations"]:
        lines.append("- [ ] `[C:%s]` used but not defined" % c)
    if not report["dangling_citations"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Unused citations (review)")
    for c in report["unused_citations"]:
        lines.append("- [ ] `[C:%s]` defined but never used" % c)
    if not report["unused_citations"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Missing / conflict markers")
    for item in report["missing_markers"]:
        lines.append("- [ ] [%s] %s" % (item["kind"], item["context"]))
    if not report["missing_markers"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Numbers without nearby [S:] (review)")
    shown = report["review_numbers"][:40]
    lines.append("- %s" % (", ".join(shown) or "none"))
    if len(report["review_numbers"]) > 40:
        lines.append("- ... %d more" % (len(report["review_numbers"]) - 40))
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", default="/app/output/manuscript.md")
    ap.add_argument("--index", default="/app/work/material_index.json")
    ap.add_argument("--work-dir", "--work", default="/app/work")
    a = ap.parse_args(argv)

    os.makedirs(a.work_dir, exist_ok=True)
    report = build_report(a.draft, a.index)
    write_json(os.path.join(a.work_dir, "evidence_map.json"), report)
    with open(os.path.join(a.work_dir, "evidence_report.md"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(render_markdown(report))

    print(json.dumps({
        "ok": True,
        "status": report["status"],
        "dangling_sources": len(report["dangling_sources"]),
        "dangling_citations": len(report["dangling_citations"]),
        "unused_citations": len(report["unused_citations"]),
        "missing_markers": len(report["missing_markers"]),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
