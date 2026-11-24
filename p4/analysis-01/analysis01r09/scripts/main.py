#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-table-clean: auditable, deterministic cleaning CLI (stdlib only, exit 0 on bad input).

Phases: bootstrap (partial skeleton) / full (clean + report + evidence) / selfcheck.
"""
import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import unicodedata

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
TABULAR_SUFFIXES = (".csv", ".tsv", ".txt", ".tab", ".dat", ".psv")
DICT_HINTS = ("dict", "dictionary", "field", "codebook", "schema", "数据字典", "字段")
RULES_HINTS = ("rule", "qc", "unit", "quality", "规则", "质控", "单位")
WORKORDER_HINTS = ("workorder", "work_order", "order", "instructions", "工单", "步骤")

DEFAULT_NOT_DETECTED = ("nd", "n.d.", "n.d", "not detected", "undetected", "未检测", "<dl", "<lod")
DEFAULT_BELOW_LOD = ("below lod", "below detection limit", "< detection", "低于检出限", "<lod")
DEFAULT_MISSING = ("", " ", "-", "--", "na", "n/a", "null", "none", "nan", "missing",
                   "缺失", "-999", "-9999", "-99999", "999", "9999", "#n/a", "#null")
DEFAULT_TRUE_ZERO = ("0", "0.0", "0.00", "-0", "-0.0")


# --------------------------------------------------------------------------
# Small utilities (deterministic)
# --------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def norm_key(s):
    """Case/whitespace/width-insensitive key for near-duplicate detection."""
    s = unicodedata.normalize("NFKC", "" if s is None else str(s))
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def list_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for fn in filenames:
            out.append(os.path.join(dirpath, fn))
    return sorted(out)


def read_text_robust(path, max_bytes=2 * 1024 * 1024):
    """Read text tolerating BOM / bad encodings; returns dict."""
    info = {"path": path, "encoding": None, "degraded": False, "error": None, "text": ""}
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            info["error"] = "file too large (%d bytes), skipped" % size
            return info
        with open(path, "rb") as fh:
            raw = fh.read()
        for enc in ("utf-8-sig", "gb18030", "latin-1"):
            try:
                info["text"] = raw.decode(enc)
                info["encoding"] = enc
                if enc in ("gb18030", "latin-1"):
                    info["degraded"] = True
                return info
            except (UnicodeDecodeError, ValueError):
                continue
        info["text"] = raw.decode("utf-8", errors="replace")
        info["encoding"] = "utf-8(replace)"
        info["degraded"] = True
    except OSError as exc:
        info["error"] = str(exc)
    return info


def parse_json_robust(text, default):
    try:
        return json.loads(text), "ok"
    except (ValueError, TypeError):
        # Strip BOM / whitespace and retry once.
        try:
            return json.loads(text.lstrip("\ufeff").strip()), "recovered"
        except (ValueError, TypeError):
            return default, "error"


# --------------------------------------------------------------------------
# Input discovery + case loading
# --------------------------------------------------------------------------
def discover_inputs(input_dir):
    files = list_files(input_dir)
    manifest = {"case_json": None, "task_md": None, "data": [],
                "dictionary": [], "rules": [], "workorder": [], "other": []}
    for f in files:
        base = os.path.basename(f).lower()
        if base == "case.json":
            manifest["case_json"] = f
        elif base == "task.md":
            manifest["task_md"] = f
        elif base.endswith(TABULAR_SUFFIXES):
            if any(h in base for h in DICT_HINTS):
                manifest["dictionary"].append(f)
            elif any(h in base for h in RULES_HINTS):
                manifest["rules"].append(f)
            elif any(h in base for h in WORKORDER_HINTS):
                manifest["workorder"].append(f)
            else:
                manifest["data"].append(f)
        else:
            manifest["other"].append(f)
    # Classify tabular files by filename hints first, then header content.
    known = manifest["dictionary"] + manifest["rules"] + manifest["workorder"] + manifest["data"]
    unclassified = [f for f in files if f.endswith(TABULAR_SUFFIXES) and f not in known]
    for f in unclassified:
        txt = read_text_robust(f)["text"][:2048]
        low = txt.lower()
        if any(h in low for h in ("字段", "field", "unit", "单位", "codebook")):
            manifest["dictionary"].append(f)
        elif any(h in low for h in ("qc", "rule", "规则", "质控", "range")):
            manifest["rules"].append(f)
        elif any(h in low for h in ("workorder", "步骤", "工单", "step")):
            manifest["workorder"].append(f)
        else:
            manifest["data"].append(f)
    return manifest


def load_case(input_dir):
    manifest = discover_inputs(input_dir)
    case = {"case_id": None, "output_contract": {}, "raw": {}, "manifest": manifest,
            "read_error": None}
    path = manifest["case_json"]
    if path is None:
        for f in manifest["other"]:
            if os.path.basename(f).lower() == "case.json":
                path = f
                break
    if path is None:
        case["read_error"] = "no case.json found in input directory"
        return case
    info = read_text_robust(path)
    parsed, status = parse_json_robust(info["text"], {})
    case["raw"] = parsed if isinstance(parsed, dict) else {}
    case["case_id"] = (case["raw"].get("case_id") or case["raw"].get("id")
                       or case["raw"].get("challenge_id")
                       or os.path.basename(os.path.dirname(path)) or os.path.basename(path))
    contract = case["raw"].get("output_contract")
    if isinstance(contract, dict):
        case["output_contract"] = contract
    if status != "ok":
        case["read_error"] = "case.json parsed with status=%s" % status
    errs = [case["read_error"]] if case["read_error"] else []
    if info.get("degraded"):
        errs.append("case.json encoding degraded")
    if info.get("error"):
        errs.append(info["error"])
    if errs:
        case["read_error"] = "; ".join(errs)
    return case


def contract_default(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


CONTRACT_ARTIFACT_HINTS = (
    (("clean", "table", "data"), "cleaned_data"),
    (("report", "quality", "qc"), "quality_report"),
    (("profile", "stat", "summar"), "data_profile"),
    (("evidence", "trace", "proof", "map"), "evidence_map"),
    (("rule", "config"), "rules"),
)


# --------------------------------------------------------------------------
# Tabular parsing
# --------------------------------------------------------------------------
def sniff_delimiter(sample):
    candidates = [",", "\t", ";", "|"]
    counts = {d: sample.count(d) for d in candidates}
    best = max(candidates, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


def read_table(path):
    """Parse a delimited table, preserving original cell text."""
    out = {"path": path, "header": [], "rows": [], "dialect": None,
           "encoding": None, "errors": []}
    info = read_text_robust(path)
    out["encoding"] = info.get("encoding")
    if info.get("error"):
        out["errors"].append(info["error"])
        return out
    text = info["text"]
    if not text.strip():
        out["errors"].append("empty file")
        return out
    delim = sniff_delimiter(text[:8192])
    out["dialect"] = delim
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        rows = [r for r in reader]
    except csv.Error as exc:
        out["errors"].append("csv parse error: %s" % exc)
        return out
    rows = [r for r in rows if r and any(c.strip() for c in r)]
    if not rows:
        out["errors"].append("no non-empty rows")
        return out
    header = [c.strip("\ufeff").strip() for c in rows[0]]
    # Deduplicate blank/duplicate header names while keeping traceability.
    seen = {}
    cleaned_header = []
    for i, h in enumerate(header):
        name = h if h else "col_%d" % (i + 1)
        if name in seen:
            seen[name] += 1
            name = "%s_%d" % (name, seen[name])
        else:
            seen[name] = 1
        cleaned_header.append(name)
    out["header"] = cleaned_header
    for r in rows[1:]:
        if not r:
            continue
        padded = r + [""] * (len(cleaned_header) - len(r))
        out["rows"].append(dict(zip(cleaned_header, padded[:len(cleaned_header)])))
    return out


def to_number(value):
    """Best-effort numeric parse: thousands separators + scientific notation."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(",", "").replace("\u00a0", "").replace(" ", "")
    try:
        f = float(s)
        return f
    except ValueError:
        return None


def classify_missing(value, field_rules=None):
    """Return one of: value | not_detected | below_lod | recorded_missing | true_zero."""
    s = "" if value is None else str(value).strip()
    rules = field_rules or {}
    not_detected = rules.get("not_detected_tokens", DEFAULT_NOT_DETECTED)
    below_lod = rules.get("below_lod_tokens", DEFAULT_BELOW_LOD)
    missing = rules.get("missing_sentinels", DEFAULT_MISSING)
    true_zero = rules.get("true_zero_tokens", DEFAULT_TRUE_ZERO)

    low = s.lower()
    if not s or low in {t.strip().lower() for t in missing}:
        return "recorded_missing"
    if low in {t.strip().lower() for t in not_detected}:
        return "not_detected"
    if low in {t.strip().lower() for t in below_lod} or low.startswith("<"):
        return "below_lod"
    if low in {t.strip().lower() for t in true_zero}:
        return "true_zero"
    return "value"


# --------------------------------------------------------------------------
# Rules loading + validation
# --------------------------------------------------------------------------
def load_rules(work_dir, output_dir):
    """Read rules.json (work first, then output); return dict + source path."""
    for base in (work_dir, output_dir):
        p = os.path.join(base, "rules.json")
        if os.path.isfile(p):
            info = read_text_robust(p)
            parsed, status = parse_json_robust(info["text"], {})
            if isinstance(parsed, dict):
                return parsed, p
    return {}, None


def validate_rules(rules):
    errors = []
    if not isinstance(rules, dict):
        return ["rules.json is not a JSON object"]
    fields = rules.get("fields")
    if fields is not None and not isinstance(fields, dict):
        errors.append("rules.fields must be an object")
    pks = rules.get("primary_keys")
    if pks is not None and (not isinstance(pks, list) or not all(isinstance(x, str) for x in pks)):
        errors.append("rules.primary_keys must be a list of strings")
    return errors


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------
def field_rules_for(rules, field):
    fields = rules.get("fields") or {}
    fr = fields.get(field) or {}
    return fr if isinstance(fr, dict) else {}


def normalize_primary(value):
    return norm_key(value)


def detect_duplicates(rows, primary_keys):
    """Group rows by normalized primary-key tuple. Returns groups (list of lists)."""
    groups = {}
    for idx, row in enumerate(rows):
        key = tuple(normalize_primary(row.get(k, "")) for k in primary_keys)
        groups.setdefault(key, []).append(idx)
    return [idxes for idxes in groups.values() if len(idxes) > 1]


def apply_cleaning(table, rules, out_dir):
    """Apply rules + conservative defaults. Returns dict of artifacts (in-memory)."""
    header = table["header"]
    rows = table["rows"]
    fields = rules.get("fields") or {}
    primary_keys = rules.get("primary_keys") or []
    dup_keep = rules.get("duplicate_keep", "first")

    cleaned_rows = []          # originals + __meta + __converted
    flags = []                 # per-row flag dicts
    conversions = []           # conversion log entries
    outlier_log = []
    missing_stats = {h: {"recorded_missing": 0, "not_detected": 0,
                         "below_lod": 0, "true_zero": 0, "value": 0,
                         "outliers": 0} for h in header}
    unit_map = {}

    for idx, row in enumerate(rows):
        out_row = {"__row_id": idx}
        row_flags = {"missing": [], "outliers": [], "converted": []}
        for h in header:
            val = row.get(h, "")
            out_row[h] = val  # preserve original text exactly
            fr = field_rules_for(rules, h)
            cat = classify_missing(val, fr)
            missing_stats[h][cat] += 1
            if cat != "value":
                row_flags["missing"].append("%s:%s" % (h, cat))

            num = to_number(val)
            lo = fr.get("physical_min")
            hi = fr.get("physical_max")
            if num is not None and (lo is not None or hi is not None):
                lo = to_number(lo)
                hi = to_number(hi)
                if (lo is not None and num < lo) or (hi is not None and num > hi):
                    missing_stats[h]["outliers"] += 1
                    action = fr.get("outlier_action", "flag")
                    row_flags["outliers"].append("%s:%s" % (h, action))
                    outlier_log.append({
                        "row_id": idx, "field": h, "value": val,
                        "bounds": [lo, hi], "action": action,
                        "note": "physical bounds from rules.json field '%s'" % h,
                    })
            factor = fr.get("convert_factor")
            if factor is not None:
                n = to_number(factor)
                if n is not None and num is not None:
                    conv_val = num * n
                    col_name = h + "__converted"
                    out_row[col_name] = repr(conv_val)  # full precision, no silent truncation
                    row_flags["converted"].append("%s->%s" % (h, col_name))
                    from_unit = fr.get("unit") or h
                    to_unit = fr.get("convert_to") or col_name
                    unit_map.setdefault(h, {"from_unit": from_unit, "to_unit": to_unit,
                                            "factor": n, "formula": "%s * %s" % (h, n)})
                    conversions.append({"row_id": idx, "field": h, "from_unit": from_unit,
                                        "to_unit": to_unit, "factor": n,
                                        "original": val, "converted": conv_val})
        cleaned_rows.append(out_row)
        flags.append(row_flags)

    duplicate_groups = detect_duplicates(cleaned_rows, primary_keys) if primary_keys else []
    for gi, group in enumerate(duplicate_groups):
        for pos, idx in enumerate(group):
            keep = (dup_keep == "first" and pos == 0) or \
                   (dup_keep == "last" and pos == len(group) - 1)
            cleaned_rows[idx]["__duplicate_group"] = gi + 1
            cleaned_rows[idx]["__duplicate_kept"] = bool(keep)
            flags[idx].setdefault("duplicates", []).append(
                "group=%d kept=%s" % (gi + 1, keep))

    for idx, row in enumerate(cleaned_rows):
        row["__missing_flags"] = "|".join(sorted(set(flags[idx]["missing"])))
        row["__outlier_flags"] = "|".join(sorted(set(flags[idx]["outliers"])))
        row["__converted_flags"] = "|".join(sorted(set(flags[idx]["converted"])))

    meta_cols = ["__row_id", "__missing_flags", "__outlier_flags",
                 "__converted_flags", "__duplicate_group", "__duplicate_kept"]
    conv_cols = sorted({c for r in cleaned_rows for c in r if c.endswith("__converted")})
    all_cols = list(header)
    for c in meta_cols + conv_cols:
        if c not in all_cols:
            all_cols.append(c)

    return {
        "header": all_cols,
        "rows": cleaned_rows,
        "missing_stats": missing_stats,
        "duplicate_groups": duplicate_groups,
        "conversions": conversions,
        "outlier_log": outlier_log,
        "unit_map": unit_map,
        "n_rows_in": len(rows),
        "n_rows_out": len(rows),
        "n_duplicate_groups": len(duplicate_groups),
    }


# --------------------------------------------------------------------------
# Artifact writers
# --------------------------------------------------------------------------
def write_cleaned_csv(cleaned, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cleaned["header"])
        w.writeheader()
        for row in cleaned["rows"]:
            w.writerow({k: row.get(k, "") for k in cleaned["header"]})


def write_profile(cleaned, path):
    profile = {
        "n_rows_in": cleaned["n_rows_in"],
        "n_rows_out": cleaned["n_rows_out"],
        "n_duplicate_groups": cleaned["n_duplicate_groups"],
        "fields": {},
        "conversions": cleaned["conversions"],
        "outlier_log": cleaned["outlier_log"],
    }
    for h, s in cleaned["missing_stats"].items():
        profile["fields"][h] = dict(
            recorded_missing=s["recorded_missing"], not_detected=s["not_detected"],
            below_lod=s["below_lod"], true_zero=s["true_zero"], value=s["value"],
            outliers=s["outliers"],
            missing_total=s["recorded_missing"] + s["not_detected"] + s["below_lod"])
    profile["unit_map"] = cleaned["unit_map"]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2, sort_keys=True)


def write_report(cleaned, table_path, profile_path, path):
    """Markdown field-level report; every number recomputed from cleaned rows."""
    lines = ["# Data Quality Report",
             "- input rows: %d" % cleaned["n_rows_in"],
             "- output rows: %d" % cleaned["n_rows_out"],
             "- duplicate groups: %d" % cleaned["n_duplicate_groups"], "",
             "## Field-level issues",
             "| field | missing | not_detected | below_lod | true_zero | values | outliers |",
             "|---|---|---|---|---|---|---|"]
    for h in sorted(cleaned["missing_stats"]):
        s = cleaned["missing_stats"][h]
        lines.append("| %s | %d | %d | %d | %d | %d | %d |" % (
            h, s["recorded_missing"], s["not_detected"], s["below_lod"],
            s["true_zero"], s["value"], s["outliers"]))
    lines += ["", "## Unit conversions"]
    if cleaned["unit_map"]:
        lines += ["", "| field | from | to | factor | formula |", "|---|---|---|---|---|"]
        for h, m in cleaned["unit_map"].items():
            lines.append("| %s | %s | %s | %s | %s |" % (
                h, m["from_unit"], m["to_unit"], m["factor"], m["formula"]))
    else:
        lines += ["", "None recorded."]
    lines += ["", "## Artifacts", "",
              "- cleaned table: `%s`" % table_path,
              "- data profile: `%s`" % profile_path]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_evidence_map(table, rules_src, evidence_path):
    """Scaffold evidence map: every claim points to a locator + verbatim quote slot."""
    ev = {
        "inputs": {
            "table": {"path": table["path"], "quote": None,
                      "note": "main tabular source; header row verbatim below"},
            "header_sample": table["header"][:20],
            "rules": {"path": rules_src, "quote": None,
                      "note": "field-level rules applied"},
        },
        "claims": [],
    }
    for h in table["header"]:
        ev["claims"].append({
            "claim": "field '%s' processed conservatively" % h,
            "locator": "%s#header" % table["path"],
            "quote": h,
            "note": "missing classified four-way; no imputation",
        })
    with open(evidence_path, "w", encoding="utf-8") as fh:
        json.dump(ev, fh, ensure_ascii=False, indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# result.json
# --------------------------------------------------------------------------
def build_skeleton(case, started):
    contract = case.get("output_contract") or {}
    result = {"case_id": case.get("case_id"), "status": "partial"}
    for k, v in contract.items():
        result[k] = v if (v is not None and v != "" and v != [] and v != {}) else contract_default(v)
    result["sources"] = []
    result["uncertainty"] = []
    result["change_log"] = []
    result["errors"] = []
    result["degradations"] = []
    result["pending_human"] = []
    result["runtime"] = {"started_at": started, "finished_at": None, "seconds": None}
    result["artifacts"] = {}
    if case.get("read_error"):
        result["errors"].append(case["read_error"])
        if result["status"] != "insufficient_evidence":
            result["status"] = "partial"
    return result


def fill_contract_keys(result, contract, artifacts):
    for key, val in contract.items():
        current = result.get(key)
        empty = current is None or current == [] or current == {} or current == ""
        if not empty:
            continue
        low = str(key).lower()
        for hints, art in CONTRACT_ARTIFACT_HINTS:
            if any(h in low for h in hints):
                if art in artifacts:
                    result[key] = artifacts[art]
                break


def save_result(result, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------
def phase_bootstrap(args):
    started = now_iso()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case = load_case(args.input)
    result = build_skeleton(case, started)
    if case["manifest"]["case_json"] is None:
        result["status"] = "insufficient_evidence"
        result["errors"].append("no case.json found in %s" % args.input)
    result["change_log"].append({
        "time": now_iso(), "step": "bootstrap",
        "action": "wrote partial result.json skeleton; status=%s" % result["status"],
    })
    save_result(result, os.path.join(args.output, "result.json"))
    return result, case


def phase_full(args):
    started = now_iso()
    result, case = phase_bootstrap(args)
    result["change_log"].append({
        "time": now_iso(), "step": "full",
        "action": "full phase started",
    })
    manifest = case["manifest"]
    errors = result.setdefault("errors", [])
    degradations = result.setdefault("degradations", [])
    pending = result.setdefault("pending_human", [])

    rules, rules_src = load_rules(args.work, args.output)
    rule_errors = validate_rules(rules)
    errors.extend("rules.json: %s" % e for e in rule_errors)

    table = None
    for p in manifest["data"]:
        t = read_table(p)
        if t["errors"]:
            degradations.append("%s: %s" % (p, "; ".join(t["errors"])))
            continue
        table = t
        break

    artifacts = {}
    if table is None:
        result["status"] = "insufficient_evidence"
        errors.append("no readable main data table found")
        pending.append("provide a readable main tabular source (CSV/TSV)")
    else:
        cleaned = apply_cleaning(table, rules, args.output)
        artifacts = {
            "cleaned_data": os.path.join(args.output, "cleaned_data.csv"),
            "data_profile": os.path.join(args.output, "data_profile.json"),
            "quality_report": os.path.join(args.output, "quality_report.md"),
            "evidence_map": os.path.join(args.output, "evidence_map.json"),
            "rules": os.path.join(args.output, "rules.json"),
        }
        write_cleaned_csv(cleaned, artifacts["cleaned_data"])
        write_profile(cleaned, artifacts["data_profile"])
        write_report(cleaned, artifacts["cleaned_data"], artifacts["data_profile"],
                     artifacts["quality_report"])
        write_evidence_map(table, rules_src, artifacts["evidence_map"])
        # Persist the exact rules applied (or an empty honest config).
        with open(artifacts["rules"], "w", encoding="utf-8") as fh:
            json.dump(rules if rules else {"note": "no external rules.json; conservative defaults only"},
                      fh, ensure_ascii=False, indent=2, sort_keys=True)
        result["artifacts"] = artifacts
        result["status"] = "ok"
        result["change_log"].append({
            "time": now_iso(), "step": "full",
            "action": "cleaned %d rows; wrote %s" % (
                cleaned["n_rows_out"],
                ", ".join(os.path.basename(v) for v in artifacts.values())),
        })

    contract = case.get("output_contract") or {}
    fill_contract_keys(result, contract, artifacts)
    result["runtime"]["finished_at"] = now_iso()
    save_result(result, os.path.join(args.output, "result.json"))
    return result


def _recompute_from_csv(clean_path):
    t = read_table(clean_path)
    if t["errors"] or not t["header"]:
        return None
    counts = {"rows": len(t["rows"]), "dup_groups": set(), "outliers": 0}
    for row in t["rows"]:
        dg = row.get("__duplicate_group", "").strip()
        if dg:
            counts["dup_groups"].add(dg)
        if row.get("__outlier_flags", "").strip():
            counts["outliers"] += 1
    counts["dup_groups"] = len(counts["dup_groups"])
    return counts


def phase_selfcheck(args):
    result_path = os.path.join(args.output, "result.json")
    issues = []
    if not os.path.isfile(result_path):
        issues.append("result.json missing")
        return _report_selfcheck(issues, result_path)

    info = read_text_robust(result_path)
    result, status = parse_json_robust(info["text"], {})
    if status == "error" or not isinstance(result, dict):
        issues.append("result.json not valid JSON object")
        return _report_selfcheck(issues, result_path)

    if not result.get("case_id"):
        issues.append("missing case_id")
    if result.get("status") not in STATUSES:
        issues.append("invalid status: %r" % result.get("status"))
    for f in ("sources", "uncertainty", "change_log"):
        if not isinstance(result.get(f), list):
            issues.append("%s must be a list" % f)

    # Verify output_contract keys survived (every key in case.json must appear).
    case = load_case(args.input)
    for k in (case.get("output_contract") or {}):
        if k not in result:
            issues.append("output_contract key missing from result.json: %s" % k)

    # Verify artifacts exist and report<->table consistency.
    artifacts = result.get("artifacts") or {}
    clean_path = artifacts.get("cleaned_data")
    if clean_path and os.path.isfile(clean_path):
        recomputed = _recompute_from_csv(clean_path)
        profile_path = artifacts.get("data_profile")
        if recomputed and profile_path and os.path.isfile(profile_path):
            pinfo = read_text_robust(profile_path)
            profile, pstatus = parse_json_robust(pinfo["text"], {})
            if pstatus != "error" and isinstance(profile, dict):
                if profile.get("n_rows_out") != recomputed["rows"]:
                    issues.append("report/profile row count mismatch with cleaned_data.csv")
                if profile.get("n_duplicate_groups") != recomputed["dup_groups"]:
                    issues.append("duplicate-group count mismatch with cleaned_data.csv")
        for key in ("quality_report", "data_profile", "evidence_map", "rules"):
            p = artifacts.get(key)
            if p and not os.path.isfile(p):
                issues.append("artifact missing: %s" % p)
    else:
        issues.append("cleaned_data.csv missing (partial/insufficient results are allowed)")

    return _report_selfcheck(issues, result_path)


def _report_selfcheck(issues, result_path):
    if issues:
        print("SELFCHECK FAIL")
        for i in issues:
            print(" - %s" % i)
        return 1
    print("SELFCHECK OK")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phase", choices=("bootstrap", "full", "selfcheck"),
                   default="full", help="which phase to run")
    p.add_argument("--input", default="/input", help="input directory")
    p.add_argument("--output", default="/app/output", help="output directory")
    p.add_argument("--work", default="/app/work", help="work directory")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.phase == "bootstrap":
            phase_bootstrap(args)
        elif args.phase == "full":
            phase_full(args)
        else:
            return phase_selfcheck(args)
    except OSError as exc:
        # Last-resort: write a minimal honest result.json and still exit 0.
        try:
            os.makedirs(args.output, exist_ok=True)
            save_result({
                "case_id": None, "status": "input_error",
                "sources": [], "uncertainty": [],
                "change_log": [{"time": now_iso(), "step": "failure",
                                "action": "OSError: %s" % exc}],
                "errors": ["OSError: %s" % exc],
                "degradations": [], "pending_human": [],
                "runtime": {"seconds": None}, "artifacts": {},
            }, os.path.join(args.output, "result.json"))
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
