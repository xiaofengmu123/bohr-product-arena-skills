#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic scientific-figure skill entrypoint.

Standard library only. Subcommands:
  bootstrap  read case.json + task.md -> result.json skeleton + work/contract.json
  extract    parse a tabular material (CSV/TSV/Markdown/whitespace) -> table JSON
  render     render a chart spec -> editable SVG + deterministic PNG/PDF + mapping
  merge      fill result.json contract fields with artifact paths / objects
  validate   check result.json + artifacts against the contract
  finish     set an honest status and finalize result.json
  run        bootstrap -> extract(auto) -> render(auto) -> validate -> finish

Determinism contract: same input + same --seed yields byte-identical SVG/PNG/PDF
and identical result.json except change_log[*].time_utc and runtime timestamps.
See references/determinism.md.
"""

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import sys
import time

VERSION = "1.0.0"
DEFAULT_SEED = 20261122
SIZE_LIMIT = 2 * 1024 * 1024

RESERVED_KEYS = {
    "case_id", "status", "output_contract", "output_contract_keys",
    "sources", "evidence", "uncertainty", "pending_human", "errors",
    "degradations", "change_log", "determinism", "runtime", "artifacts",
}


# --------------------------------------------------------------------------- #
# generic helpers (pure stdlib, deterministic where it matters)
# --------------------------------------------------------------------------- #
def canonical_dump(obj):
    """Deterministic JSON serialization (sorted keys, stable separators)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path, limit=SIZE_LIMIT):
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, "unreadable"
    if size > limit:
        return None, "too_large(%d)" % size
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read(), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(), "utf-8-replace"


def read_json(path):
    text, enc = read_text(path)
    if text is None:
        return None, enc
    try:
        return json.loads(text), enc
    except Exception as exc:  # noqa: BLE001 - deliberate tolerance
        return None, "bad_json:%s" % exc


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(canonical_dump(obj) + "\n")


def write_bytes(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def safe_name(name, fallback="figure"):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("._")
    return slug or fallback


def rel_to(output_dir, path):
    try:
        return os.path.relpath(path, output_dir).replace(os.sep, "/")
    except ValueError:
        return os.path.basename(path)


# --------------------------------------------------------------------------- #
# input discovery & contract
# --------------------------------------------------------------------------- #
def discover_files(input_dir, suffixes=(".csv", ".tsv", ".txt", ".md")):
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):  # sorted -> deterministic
            if fn.startswith("."):
                continue
            p = os.path.join(root, fn)
            if any(fn.lower().endswith(s) for s in suffixes):
                try:
                    if os.path.getsize(p) <= SIZE_LIMIT:
                        out.append(p)
                except OSError:
                    pass
    return out


def find_case(input_dir):
    """Locate the case JSON: case.json, or any *.json with id/output_contract."""
    if not os.path.isdir(input_dir):
        return None, None
    cands = []
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            if fn.lower() == "case.json":
                return os.path.join(root, fn), "case.json"
            if fn.lower().endswith(".json"):
                cands.append(os.path.join(root, fn))
    for p in cands:
        obj, _ = read_json(p)
        if isinstance(obj, dict) and (obj.get("case_id") or obj.get("id")
                                      or "output_contract" in obj):
            return p, os.path.basename(p)
    return None, None


def find_task(input_dir):
    if not os.path.isdir(input_dir):
        return None
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            if fn.lower() == "task.md":
                return os.path.join(root, fn)
    return None


def get_case_id(case):
    for k in ("case_id", "id", "challenge_id"):
        v = case.get(k)
        if v not in (None, "", []):
            return str(v)
    return None


def get_contract(case):
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        return oc
    if isinstance(oc, list):
        return {k: None for k in oc if isinstance(k, str)}
    # sensible default schema for this task family; recorded as fallback
    return {
        "editable_source": None,
        "rendered_png": None,
        "rendered_pdf": None,
        "data_mapping": None,
        "annotation_checks": None,
        "figure_notes": None,
    }


def build_skeleton(case, output_dir, work_dir, seed):
    case_id = get_case_id(case)
    contract = get_contract(case)
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "output_contract_keys": sorted(contract.keys()),
        "sources": [],
        "evidence": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "determinism": {"version": VERSION, "seed": seed, "fingerprint": None},
        "runtime": {"python": sys.version.split()[0], "platform": sys.platform},
        "artifacts": {},
    }
    for key in sorted(contract.keys()):
        result[key] = None
    result["output_contract"] = {k: None for k in sorted(contract.keys())}
    log_change(result, "bootstrap", "ok",
               "skeleton written; contract keys=%s" % ",".join(sorted(contract.keys())))
    result_path = os.path.join(output_dir, "result.json")
    write_json(result_path, result)
    write_json(os.path.join(work_dir, "contract.json"),
               {"case_id": case_id, "output_contract": contract})
    return result, result_path


def log_change(result, step, status, note):
    entry = {"step": step, "time_utc": utc_now(), "status": status,
             "note": note}
    result.setdefault("change_log", []).append(entry)
    return entry


def set_contract_field(result, key, value):
    result[key] = value
    result.setdefault("output_contract", {})[key] = value
    keys = result.setdefault("output_contract_keys", [])
    if key not in keys:
        keys.append(key)
        keys.sort()


# --------------------------------------------------------------------------- #
# table extraction
# --------------------------------------------------------------------------- #
def _parse_float(s):
    s = s.strip()
    if not s:
        return None
    s2 = s.replace(",", "").replace("%", "").strip()
    if s2 in ("", "-", "+"):
        return None
    try:
        return float(s2)
    except ValueError:
        return None


def _is_percent(s):
    return bool(re.search(r"%\s*$", s.strip()))


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return None
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _infer_col(name, cells):
    nonempty = [c for c in cells if c.strip() != ""]
    nums = [_parse_float(c) for c in nonempty]
    numeric = [n for n in nums if n is not None]
    missing = sum(1 for c in cells if c.strip() == "")
    if nonempty and len(numeric) == len(nonempty):
        coltype = "numeric"
        values = [_parse_float(c) if c.strip() != "" else None for c in cells]
    elif nonempty and all(_is_percent(c) for c in nonempty):
        coltype = "percent"
        values = [_parse_float(c) if c.strip() != "" else None for c in cells]
    elif all(re.match(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?$", c.strip())
             for c in nonempty) and nonempty:
        coltype = "date"
        values = cells
    else:
        coltype = "text"
        values = cells
    return coltype, values, missing


def _magnitude_issues(colname, values, rowoff):
    nums = [v for v in values if isinstance(v, (int, float))]
    if len(nums) < 3:
        return []
    med = _median(nums)
    if med is None:
        return []
    devs = [abs(v - med) for v in nums]
    mad = _median(devs) or 0.0
    issues = []
    if mad > 0:
        for i, v in enumerate(values):
            if isinstance(v, (int, float)) and abs(v - med) > 10 * mad:
                issues.append({"type": "magnitude_outlier", "column": colname,
                               "row": rowoff + i, "value": v,
                               "median": med, "mad": mad})
    return issues


def extract_table(path):
    text, enc = read_text(path)
    if text is None:
        return {"source": os.path.basename(path), "error": enc, "columns": [],
                "rows": [], "issues": [{"type": "unreadable", "detail": enc}]}
    first_line = text.split("\n", 1)[0]
    sample = text[:1000]
    if path.lower().endswith(".csv"):
        delim = "\t" if sample.count("\t") > sample.count(",") else ","
    elif path.lower().endswith(".tsv"):
        delim = "\t"
    elif re.search(r"^\s*\|.*\|\s*$", first_line, re.M):
        delim = "markdown"
    elif sample.count("\t") > 0:
        delim = "\t"
    elif sample.count(",") > 0:
        delim = ","
    else:
        delim = None

    if delim == "markdown":
        return _extract_markdown(path, text)

    rows = []
    if delim:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        for r in reader:
            if r:
                rows.append(r)
    else:
        for line in text.splitlines():
            line = line.rstrip()
            if line.strip():
                rows.append(re.split(r"\s{2,}|\t+", line))

    return _rows_to_table(path, rows, delim or "whitespace")


def _extract_markdown(path, text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows = []
    for ln in lines:
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if any(re.fullmatch(r":?-{1,}:?", c) for c in cells if c):
                continue  # separator row
            rows.append(cells)
    return _rows_to_table(path, rows, "markdown")


def _rows_to_table(path, rows, delimiter):
    if not rows:
        return {"source": os.path.basename(path), "delimiter": delimiter,
                "columns": [], "rows": [], "row_count": 0, "issues": []}
    ncol = max(len(r) for r in rows)
    norm = [r + [""] * (ncol - len(r)) for r in rows]
    header = norm[0]
    if all(_parse_float(c) is not None or c.strip() == "" for c in header):
        header = ["col_%d" % (i + 1) for i in range(ncol)]
        body = norm
    else:
        body = norm[1:]
    cols = []
    issues = []
    for j in range(ncol):
        cells = [row[j] for row in body]
        name = str(header[j]).strip() or ("col_%d" % (j + 1))
        ctype, values, missing = _infer_col(name, cells)
        if missing:
            issues.append({"type": "missing", "column": name, "count": missing,
                           "rows": [i for i, c in enumerate(cells) if c.strip() == ""]})
        issues.extend(_magnitude_issues(name, values, 1))
        cols.append({"name": name, "type": ctype, "missing": missing,
                     "values": values})
    hints = []
    numeric_cols = [c for c in cols if c["type"] in ("numeric", "percent")]
    text_cols = [c for c in cols if c["type"] == "text"]
    if len(numeric_cols) >= 1 and text_cols:
        hints.append("column '%s' looks categorical -> x/categories" % text_cols[0]["name"])
    if len(numeric_cols) >= 1:
        hints.append("column(s) %s look numeric -> y/series values"
                     % ", ".join("'%s'" % c["name"] for c in numeric_cols))
    if len(numeric_cols) > 1 and not text_cols:
        hints.append("multiple numeric columns -> treat each as a series")
    return {"source": os.path.basename(path), "delimiter": delimiter,
            "columns": cols, "rows": [dict(zip([c["name"] for c in cols],
                                               row)) for row in body],
            "row_count": len(body), "issues": issues, "hints": hints,
            "encoding": "ok"}


# --------------------------------------------------------------------------- #
# render / merge / validate / finish
# --------------------------------------------------------------------------- #
def cmd_render(spec_path, name, out_dir, work_dir, seed):
    from render_core import render_spec  # local import; still stdlib-only
    spec, enc = read_json(spec_path)
    if not isinstance(spec, dict):
        return {"error": "bad spec: %s" % enc}
    return render_spec(spec, out_dir, name, seed, work_dir)


def cmd_merge(result_path, fields):
    obj, _ = read_json(result_path)
    if not isinstance(obj, dict):
        return {"error": "result.json missing or invalid"}
    contract_keys = set(obj.get("output_contract_keys", []))
    applied, skipped = [], []
    for key, value in fields:
        if key == "case_id":
            obj["case_id"] = value
            applied.append(key)
            continue
        if key in RESERVED_KEYS and key not in contract_keys:
            skipped.append(key)
            continue
        contract_keys.add(key)
        set_contract_field(obj, key, value)
        applied.append(key)
    obj["output_contract_keys"] = sorted(contract_keys)
    log_change(obj, "merge", "ok",
               "filled fields: %s%s" %
               (", ".join(applied),
                ("; skipped: " + ", ".join(skipped)) if skipped else ""))
    write_json(result_path, obj)
    return {"ok": True, "fields": applied, "skipped": skipped}


def _artifact_check(result, output_dir):
    report = {"ok": True, "checks": [], "problems": []}
    contract_keys = result.get("output_contract_keys", [])
    for key in contract_keys:
        val = result.get(key)
        filled = val not in (None, "", [])
        report["checks"].append({"field": key, "filled": bool(filled)})
        if not filled:
            report["problems"].append("contract field '%s' not filled" % key)
    # editable source present?
    src = result.get("editable_source") or result.get("source_file")
    if src:
        p = os.path.join(output_dir, src)
        if os.path.isfile(p):
            txt, _ = read_text(p)
            if txt and ("<text" in txt or p.lower().endswith((".pptx", ".eps"))):
                report["checks"].append({"field": "editable", "status": "ok"})
            else:
                report["problems"].append("editable source not editable/text-based: %s" % src)
    for key in ("rendered_png", "rendered_pdf"):
        val = result.get(key)
        if val:
            p = os.path.join(output_dir, val)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    head = f.read(8)
                if key == "rendered_png" and head[:8] == b"\x89PNG\r\n\x1a\n":
                    report["checks"].append({"field": key, "status": "ok"})
                elif key == "rendered_pdf" and head[:5] == b"%PDF-":
                    report["checks"].append({"field": key, "status": "ok"})
                else:
                    report["problems"].append("bad magic for %s" % key)
    report["ok"] = not report["problems"]
    return report


def cmd_validate(result_path, output_dir, work_dir):
    obj, enc = read_json(result_path)
    if not isinstance(obj, dict):
        return {"ok": False, "error": enc, "problems": ["result.json invalid"]}
    problems = []
    if not obj.get("case_id"):
        problems.append("case_id empty")
    if obj.get("status") not in ("complete", "partial", "failed"):
        problems.append("status not final: %r" % obj.get("status"))
    for key in ("sources", "uncertainty", "change_log"):
        if not isinstance(obj.get(key), list):
            problems.append("missing list field: %s" % key)
    art = _artifact_check(obj, output_dir)
    problems.extend(art["problems"])
    report = {"ok": not problems, "problems": problems,
              "contract_keys": obj.get("output_contract_keys", []),
              "artifact_checks": art["checks"]}
    write_json(os.path.join(work_dir, "check_report.json"), report)
    return report


def _auto_status(result, output_dir):
    contract_keys = result.get("output_contract_keys", [])
    filled = sum(1 for k in contract_keys if result.get(k) not in (None, "", []))
    has_editable = bool(result.get("editable_source")) and os.path.isfile(
        os.path.join(output_dir, str(result.get("editable_source"))))
    has_export = any(result.get(k) for k in ("rendered_png", "rendered_pdf"))
    if filled == 0:
        return "failed" if result.get("errors") else "partial"
    if filled == len(contract_keys) and has_editable:
        return "complete"
    return "partial"


def cmd_finish(result_path, output_dir, work_dir, forced_status=None):
    obj, _ = read_json(result_path)
    if not isinstance(obj, dict):
        return {"error": "result.json missing or invalid"}
    if obj.get("status") in ("complete", "partial", "failed") and not forced_status:
        return {"ok": True, "status": obj["status"], "note": "already final"}
    status = forced_status or _auto_status(obj, output_dir)
    if status not in ("complete", "partial", "failed"):
        status = "partial"
    obj["status"] = status
    if status in ("partial", "failed"):
        limits = obj.setdefault("uncertainty", [])
        if not any(isinstance(x, dict) and x.get("type") == "limits" for x in limits):
            limits.append({"type": "limits",
                           "note": "status=%s; unfilled contract fields or degraded exports" % status})
    log_change(obj, "finish", status, "finalized status=%s" % status)
    write_json(result_path, obj)
    return {"ok": True, "status": status}


def cmd_run(input_dir, output_dir, work_dir, seed):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case_path, _ = find_case(input_dir)
    if case_path:
        case, enc = read_json(case_path)
        if not isinstance(case, dict):
            case = {}
            err = enc
        else:
            err = None
    else:
        case, err = {}, "no case json found"
    result, result_path = build_skeleton(case, output_dir, work_dir, seed)
    if err:
        result["errors"].append({"type": "case_json", "detail": err})
    # material files
    for p in discover_files(input_dir):
        rel = rel_to(input_dir, p)
        result["sources"].append({"file": rel, "sha256": sha256_file(p)})
        if p.lower().endswith((".csv", ".tsv", ".txt", ".md")):
            try:
                t = extract_table(p)
                t["file"] = rel
                out = os.path.join(work_dir, "t_" + safe_name(os.path.basename(p)) + ".json")
                write_json(out, t)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append({"type": "extract", "file": rel,
                                         "detail": str(exc)})
    # auto-render any chart spec
    spec = os.path.join(work_dir, "chart_spec.json")
    specs = [spec] if os.path.isfile(spec) else []
    for sp in specs:
        try:
            rep = cmd_render(sp, "figure", output_dir, work_dir, seed)
            if "error" not in rep:
                for k, v in rep.get("contract_fields", {}).items():
                    set_contract_field(result, k, v)
                result["artifacts"].update(rep.get("artifacts", {}))
        except Exception as exc:  # noqa: BLE001
            result["errors"].append({"type": "render", "detail": str(exc)})
    files = sorted([(s.get("file"), s.get("sha256")) for s in result.get("sources", [])])
    spec_docs = []
    for sp in specs:
        obj, _ = read_json(sp)
        spec_docs.append(obj)
    result["determinism"]["fingerprint"] = sha256_bytes(
        canonical_dump({"version": VERSION, "seed": seed, "files": files,
                        "specs": spec_docs}))
    write_json(result_path, result)
    cmd_validate(result_path, output_dir, work_dir)
    cmd_finish(result_path, output_dir, work_dir)
    return result_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", nargs="?", default="run",
                    choices=["run", "bootstrap", "extract", "render", "merge",
                             "validate", "finish"])
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--result", default=None)
    ap.add_argument("--name", default="figure")
    ap.add_argument("--spec", default=None)
    ap.add_argument("--file", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--field", action="append", default=[])
    ap.add_argument("--value", action="append", default=[])
    ap.add_argument("--status", default=None)
    a = ap.parse_args(argv)

    os.makedirs(a.output, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    if a.cmd == "bootstrap":
        case_path, _ = find_case(a.input)
        case = {}
        err = None
        if case_path:
            case, enc = read_json(case_path)
            if not isinstance(case, dict):
                case, err = {}, enc
        else:
            err = "no case json found"
        result, result_path = build_skeleton(case, a.output, a.work, a.seed)
        if err:
            result["errors"].append({"type": "case_json", "detail": err})
            write_json(result_path, result)
        print(canonical_dump({"ok": True, "result": result_path, "case_id": result.get("case_id")}))
        return 0

    if a.cmd == "extract":
        if not a.file:
            print(canonical_dump({"error": "--file required"}), file=sys.stderr)
            return 2
        t = extract_table(a.file)
        if a.name and a.name != "figure":
            out = a.out or os.path.join(a.work, "t_" + safe_name(a.name) + ".json")
        else:
            out = a.out or os.path.join(a.work, "table.json")
        write_json(out, t)
        print(canonical_dump({"ok": True, "out": out, "columns": len(t.get("columns", []))}))
        return 0

    if a.cmd == "render":
        spec = a.spec or os.path.join(a.work, "chart_spec.json")
        out_dir = a.out or a.output
        rep = cmd_render(spec, a.name, out_dir, a.work, a.seed)
        print(canonical_dump({"ok": "error" not in rep, "report": rep}))
        return 0 if "error" not in rep else 1

    result_path = a.result or os.path.join(a.output, "result.json")

    if a.cmd == "merge":
        if len(a.field) != len(a.value):
            print(canonical_dump({"error": "--field/--value count mismatch"}),
                  file=sys.stderr)
            return 2
        rep = cmd_merge(result_path, list(zip(a.field, a.value)))
        print(canonical_dump(rep))
        return 0 if "error" not in rep else 1

    if a.cmd == "validate":
        rep = cmd_validate(result_path, a.output, a.work)
        print(canonical_dump(rep))
        return 0 if rep.get("ok") else 1

    if a.cmd == "finish":
        rep = cmd_finish(result_path, a.output, a.work, a.status)
        print(canonical_dump(rep))
        return 0 if "error" not in rep else 1

    # run
    result_path = cmd_run(a.input, a.output, a.work, a.seed)
    print(canonical_dump({"ok": True, "result": result_path}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
