#!/usr/bin/env python3
"""Structured intermediate representations for format/template adaptation.

Creates four auditable JSON files under /app/work/intermediates/:

  format_spec.json    -- template specification (recognize accurately)
  content_units.json  -- source content-unit inventory (transfer completely)
  plan.json           -- style mapping + verification checks (lay out correctly)
  fidelity_report.json-- before/after integrity self-check (deliver verifiably)

Without --finalize, missing files are scaffolded in place (idempotent merge).
With --finalize, intermediate paths and the fidelity conclusion are merged back
into result.json and change_log is appended. Standard library only.
"""
import argparse
import datetime as _dt
import json
import os
import sys

INTER_DIR = "intermediates"

FORMAT_SPEC = {
    "template": {"name": None, "version": None, "source_locator": None,
                 "status": "unknown", "reason": None},
    "page": {"page_size": None, "orientation": None, "columns": None,
             "margins": {"top": None, "bottom": None, "left": None, "right": None},
             "line_spacing": None, "body_font_size_pt": None, "body_font": None},
    "headings": [],           # {"level":1,"style":null,"numbering":null,"font_size_pt":null,"bold":null,"alignment":null}
    "figures": {"caption_position": None, "caption_font_size_pt": None,
                "numbering_style": None, "cross_ref_style": None},
    "tables": {"style": None, "caption_position": None, "caption_font_size_pt": None,
               "numbering_style": None},
    "references": {"style": None, "author_truncation": None, "punctuation": None,
                   "sort_order": None, "mixed_language_rules": None},
    "headers_footers": {"header": None, "footer": None, "page_number_position": None},
    "export": {"format": None, "font_embedding": None, "toc_required": None,
               "editable_source_required": None},
    "check_items": [],        # {"id","item","value","locator","verified":false}
    "conflicts": [],          # {"item","values":[],"locators":[],"resolution":null}
    "uncertain": [],          # {"item","reason"}
}

CONTENT_UNITS = {
    "source_document": {"path": None, "title": None, "kind": None},
    "unit_types": ["heading", "paragraph", "figure", "table", "formula", "footnote",
                   "comment", "revision", "field_code", "toc", "reference"],
    "units": [],
    # unit shape: {"id","type","locator","excerpt","target_style","status","notes"}
    # status in: pending | kept | mapped | missing | dropped
}

PLAN = {
    "goal": None,
    "audience": None,
    "genre": None,
    "target_template": None,
    "style_map": [],          # {"source_style","target_style","applies_to","evidence_locator"}
    "anchors": [],            # {"unit_id","source_locator","target_anchor"}
    "verification_checks": [],  # {"id","check","method","result":null}
    "evidence_notes": [],
}

FIDELITY = {
    "conclusion": "pending",  # pending | pass | fail
    "before_counts": None,    # {"units":0,"figures":0,"tables":0,"formulas":0,"footnotes":0,"references":0}
    "after_counts": None,
    "lost_units": [],         # {"id","type","locator","reason"}
    "modified_units": [],     # {"id","type","locator","change","intentional":null}
    "issues": [],
    "artifact_checks": [],    # {"file","path","check","result":null}
}

SCHEMAS = {
    "format_spec.json": FORMAT_SPEC,
    "content_units.json": CONTENT_UNITS,
    "plan.json": PLAN,
    "fidelity_report.json": FIDELITY,
}


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def load_json(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.loads(f.read())
        except (UnicodeDecodeError, OSError):
            continue
        except json.JSONDecodeError:
            break
    raise OSError("cannot read/parse %s" % path)


def merge(existing, template):
    if not isinstance(existing, dict) or not isinstance(template, dict):
        return template
    for k, v in template.items():
        if k not in existing:
            existing[k] = v
        elif isinstance(existing[k], dict) and isinstance(v, dict):
            existing[k] = merge(existing[k], v)
    return existing


def scaffold_intermediates(work_dir):
    inter_dir = os.path.join(work_dir, INTER_DIR)
    os.makedirs(inter_dir, exist_ok=True)
    paths = {}
    for name, schema in SCHEMAS.items():
        p = os.path.join(inter_dir, name)
        if os.path.exists(p):
            try:
                existing = load_json(p)
            except OSError:
                existing = {}
            data = merge(existing, schema)
        else:
            data = schema
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        paths[name] = p
    return paths


def finalize(paths, result_path):
    if not os.path.exists(result_path):
        print(json.dumps({"ok": False, "error": "result.json 不存在，先运行 scaffold_result.py"},
                         ensure_ascii=False))
        return 1
    try:
        result = load_json(result_path)
    except OSError:
        result = {}
    inter = result.setdefault("intermediates", {})
    if not isinstance(inter, dict):
        inter = {}
        result["intermediates"] = inter
    for name, p in paths.items():
        key = name[:-5] if name.endswith(".json") else name
        inter[key] = p
    # Fold fidelity conclusion into status only when it is decisive.
    fid_path = paths.get("fidelity_report.json")
    if fid_path and os.path.exists(fid_path):
        try:
            fid = load_json(fid_path)
            conclusion = fid.get("conclusion")
            if conclusion == "fail":
                result.setdefault("uncertainty", {})
                result["uncertainty"].setdefault("limitations", [])
                result["uncertainty"]["limitations"].append(
                    "fidelity_report 结论为 fail，存在丢失或被误改单元，详见 intermediates/fidelity_report.json")
        except OSError:
            pass
    log = result.setdefault("change_log", [])
    if isinstance(log, list):
        log.append({"ts": _now(), "action": "intermediates_finalize",
                    "detail": "中间产物路径与保真度结论合并回 result.json"})
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "intermediates": paths}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--finalize", action="store_true",
                    help="merge intermediate paths/fidelity into result.json")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    paths = scaffold_intermediates(a.work)
    if a.finalize:
        return finalize(paths, a.result)
    print(json.dumps({"ok": True, "intermediates": paths}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
