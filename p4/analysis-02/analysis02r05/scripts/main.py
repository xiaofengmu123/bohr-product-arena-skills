"""main.py - contract-first statistical analysis skill (standard library only).

Subcommands
-----------
  skeleton   Build an honest partial result.json skeleton inside 3 minutes,
             keyed field-by-field to /input/case.json's `output_contract`.
  survey     List input materials and detected tools.
  inspect    Infer schema / data dictionary for every readable table.
  analyze    Deterministic clean + describe + infer + effects + CI pipeline.
  emit       Merge analysis.json into result.json (contract fields first).
  audit      Verify contract alignment + JSON validity + determinism markers.

Invariants
----------
  * result.json top level = `case_id` + every `output_contract` key (in the
    contract's original order) + envelope keys.
  * The 3-minute partial skeleton is a full, parseable result.json, not an
    empty file.  Everything after it is incremental improvement.
  * No randomness, no clocks in computed values: repeated runs are identical.
"""

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
import traceback

import stats

HERE = os.path.dirname(os.path.abspath(__file__))


def _stamp(seq):
    """Deterministic change-log time: ordinal only, so repeated runs match.

    Wall-clock time would break byte-identical reproducibility; the platform
    records actual wall-clock timestamps independently.
    """
    return "T%03d" % seq

# Envelope keys that always accompany the contract fields.
ENVELOPE = ["status", "sources", "uncertainty", "pending_human", "change_log",
            "errors", "degradations", "runtime"]

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip bigger files, record degradation


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------
def _read_text(path):
    data = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                data = fh.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if data is None:
        with open(path, "rb") as fh:
            data = fh.read().decode("utf-8", errors="replace")
    return data


def find_case(input_dir):
    for name in ("case.json",):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            return p
    return None


def load_case(input_dir):
    path = find_case(input_dir)
    if not path:
        return None, None, "case.json not found"
    try:
        raw = _read_text(path)
        case = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return None, None, "bad case.json: %s" % exc
    return case, path, None


def _type_default(value):
    """Infer a typed empty skeleton from a contract value (no fabrication)."""
    if isinstance(value, dict):
        return {k: _type_default(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("array", "list"):
            return []
        if low in ("object", "dict", "map"):
            return {}
        if low in ("number", "int", "float", "integer"):
            return None
        return None
    return None


def build_skeleton(case):
    """Return {case_id, contract_fields..., envelope...} for the partial save."""
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        case_id = "unknown-case"
    contract = case.get("output_contract")
    skeleton = {"case_id": case_id}
    if isinstance(contract, dict):
        for key, val in contract.items():
            if key in skeleton:
                continue
            skeleton[key] = _type_default(val)
    elif isinstance(contract, list):
        for key in contract:
            if isinstance(key, str) and key not in skeleton:
                skeleton[key] = None
    for key in ENVELOPE:
        if key not in skeleton:
            skeleton[key] = [] if key in (
                "sources", "uncertainty", "pending_human", "change_log",
                "errors", "degradations") else ({} if key == "runtime" else STATUS_PARTIAL)
    return skeleton


def discover_files(input_dir):
    out = []
    skip = {"case.json", "task.md"}
    for name in sorted(os.listdir(input_dir)):
        if name in skip:
            continue
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        size = os.path.getsize(p)
        out.append({"name": name, "path": p, "size": size,
                    "ext": os.path.splitext(name)[1].lower(),
                    "skipped": size > MAX_FILE_BYTES})
    return out


def _sniff_delimiter(text):
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        for ch in ("\t", ",", ";", "|"):
            if ch in text[:8192]:
                return ch
        return ","


def load_table(path):
    """Load csv/tsv/json/sqlite into a list-of-dicts table; else None+error."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv", ".txt", ".dat"):
        text = _read_text(path)
        delim = "\t" if ext == ".tsv" else _sniff_delimiter(text)
        try:
            rows = list(csv.DictReader(io.StringIO(text), delimiter=delim))
        except Exception as exc:  # noqa: BLE001
            return None, "csv parse failed: %s" % exc
        return rows, None
    if ext == ".json":
        try:
            obj = json.loads(_read_text(path))
        except Exception as exc:  # noqa: BLE001
            return None, "json parse failed: %s" % exc
        if isinstance(obj, list):
            rows = obj
        elif isinstance(obj, dict):
            rows = None
            for v in obj.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    rows = v
                    break
            if rows is None:
                return None, "json structure not a table"
        else:
            return None, "json not table-like"
        return rows, None
    if ext in (".sqlite", ".db", ".sqlite3", ".db3"):
        tables = {}
        try:
            con = sqlite3.connect(path)
            cur = con.cursor()
            names = [r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for tname in names:
                try:
                    cur2 = con.cursor()
                    cur2.execute('SELECT * FROM "%s"' % tname)
                    cols = [d[0] for d in cur2.description]
                    tables[tname] = [dict(zip(cols, row)) for row in cur2.fetchall()]
                except Exception as exc:  # noqa: BLE001
                    tables[tname] = {"_error": str(exc)}
            con.close()
        except Exception as exc:  # noqa: BLE001
            return None, "sqlite open failed: %s" % exc
        return tables, None
    if ext in (".xlsx", ".xls", ".docx", ".pdf", ".png", ".jpg", ".jpeg"):
        return None, "binary/office format not parseable with stdlib"
    return None, "unsupported extension %s" % ext


def _numeric(rows, col):
    vals = []
    ok = 0
    for r in rows:
        v = r.get(col)
        if v is None or v == "":
            continue
        try:
            vals.append(float(v))
            ok += 1
        except (TypeError, ValueError):
            return None, 0
    return vals, ok


def infer_schema(rows):
    cols = list(rows[0].keys()) if rows else []
    schema = {}
    for col in cols:
        vals = [r.get(col) for r in rows]
        non_null = [v for v in vals if v not in (None, "", "NA", "NaN", "nan")]
        n_num = 0
        try:
            floats = []
            for v in non_null:
                floats.append(float(v))
                n_num += 1
        except (TypeError, ValueError):
            pass
        n_unique = len(set(str(v) for v in non_null))
        kind = "numeric" if n_num >= max(1, int(0.9 * len(non_null))) else \
            ("categorical" if n_unique <= max(20, len(non_null) * 0.5) else "text")
        schema[col] = {
            "kind": kind, "non_null": len(non_null),
            "missing": len(vals) - len(non_null), "n_unique": n_unique,
            "unit_unknown": kind == "numeric",
        }
    return schema


# ---------------------------------------------------------------------------
# Analysis pipeline (deterministic)
# ---------------------------------------------------------------------------
def _pick_columns(rows, schema, plan):
    numeric = [c for c, s in schema.items() if s["kind"] == "numeric"]
    categorical = [c for c, s in schema.items() if s["kind"] == "categorical"]
    if plan and plan.get("measure_col") in schema:
        measure = plan["measure_col"]
    elif numeric:
        measure = numeric[0]
    else:
        measure = None
    if plan and plan.get("group_col") in schema:
        group = plan["group_col"]
    elif categorical:
        group = categorical[0]
    else:
        group = None
    return measure, group


def _clean_table(rows, measure, group, change_log):
    """Missing / duplicate / outlier handling.  Never silently deletes rows."""
    n_before = len(rows)
    out = []
    missing_count = 0
    for r in rows:
        v = r.get(measure)
        if v in (None, "", "NA", "NaN", "nan", "N/A", "na"):
            missing_count += 1
            continue
        out.append(dict(r))
    if missing_count:
        change_log.append({
            "id": "CL%d" % (len(change_log) + 1),
            "time": _stamp(len(change_log) + 1),
            "action": "drop_missing_measure",
            "before": n_before, "after": n_before - missing_count,
            "reason": "rows without a usable %r value excluded; count kept, not imputed as 0" % measure,
        })
    # duplicates (canonical key: order-independent, hash-salt-free)
    seen = set()
    unique = []
    dup = 0
    for r in out:
        key = json.dumps(r, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        unique.append(dict(r))
    if dup:
        change_log.append({
            "id": "CL%d" % (len(change_log) + 1),
            "time": _stamp(len(change_log) + 1),
            "action": "drop_exact_duplicates",
            "before": len(out), "after": len(unique),
            "reason": "exact-duplicate rows removed; first occurrence kept",
        })
    out = unique
    # outliers: flag, do not delete
    if measure:
        vals = stats._as_floats([r.get(measure) for r in out])
        flags = stats.iqr_outlier_flags(vals) if vals else {"n_outliers": 0}
        if flags.get("n_outliers", 0):
            change_log.append({
                "id": "CL%d" % (len(change_log) + 1),
                "time": _stamp(len(change_log) + 1),
                "action": "flag_iqr_outliers",
                "before": "n/a", "after": flags["n_outliers"],
                "reason": "IQR 1.5 outliers flagged in quality_checks, retained in analysis",
            })
    else:
        flags = {"n_outliers": 0}
    return out, missing_count, dup, flags


def _groups(rows, measure, group):
    gmap = {}
    for r in rows:
        g = r.get(group) if group else "all"
        if g is None or g == "":
            g = "NA"
        gmap.setdefault(str(g), []).append(r.get(measure))
    return gmap


def run_analyze(input_dir, work_dir, plan=None):
    """Run the deterministic pipeline; write analysis.json + cleaned csv."""
    case, case_path, case_err = load_case(input_dir)
    if case_err:
        return {"status": STATUS_INPUT_ERROR, "error": case_err, "ok": False}
    files = discover_files(input_dir)
    tables = []
    load_errors = []
    for f in files:
        if f["skipped"]:
            load_errors.append("%s: size limit" % f["name"])
            continue
        rows, err = load_table(f["path"])
        if err:
            load_errors.append("%s: %s" % (f["name"], err))
            continue
        if isinstance(rows, dict):  # sqlite: table name -> rows
            for tname, trows in rows.items():
                if isinstance(trows, dict) and "_error" in trows:
                    load_errors.append("%s.%s: %s" % (f["name"], tname, trows["_error"]))
                else:
                    tables.append({"file": f["name"], "table": tname,
                                   "rows": trows, "schema": infer_schema(trows)})
        else:
            tables.append({"file": f["name"], "table": None, "rows": rows,
                           "schema": infer_schema(rows)})
    if not tables:
        return {"status": STATUS_INSUFFICIENT, "error": "no readable table",
                "load_errors": load_errors, "ok": False}

    # choose first table with a numeric column
    target = None
    for t in tables:
        if any(s["kind"] == "numeric" for s in t["schema"].values()):
            target = t
            break
    if target is None:
        target = tables[0]
    schema = target["schema"]
    measure, group = _pick_columns(target["rows"], schema, plan or {})
    change_log = []
    cleaned, n_missing, n_dup, outliers = _clean_table(
        target["rows"], measure, group, change_log)

    analysis = {
        "status": STATUS_PARTIAL, "case_id": case.get("case_id", "unknown-case"),
        "table": {"file": target["file"], "table": target["table"]},
        "design": {"measure_col": measure, "group_col": group,
                   "unit_unknown": bool(measure)},
        "cleaning": {"n_before": len(target["rows"]), "n_after": len(cleaned),
                     "n_missing_measure": n_missing,
                     "n_duplicates_dropped": n_dup,
                     "n_outliers_flagged": outliers.get("n_outliers", 0),
                     "outlier_fences": {k: outliers.get(k) for k in
                                        ("q1", "q3", "iqr", "lower_fence",
                                         "upper_fence")}},
        "descriptive": [], "inferential": [], "effects": [], "quality": [],
        "change_log": change_log,
    }

    gmap = _groups(cleaned, measure, group)
    group_names = sorted(gmap.keys())
    if measure:
        for gname in group_names:
            d = stats.describe(gmap[gname])
            d["group"] = gname
            d["unit_unknown"] = True
            analysis["descriptive"].append(d)
            analysis["quality"].append({
                "group": gname, "normality": stats.jarque_bera(gmap[gname]),
            })

    # inferential selection
    if measure and group and len(group_names) == 2:
        a, b = gmap[group_names[0]], gmap[group_names[1]]
        nb = stats.jarque_bera(a)
        nc = stats.jarque_bera(b)
        hom = stats.brown_forsythe([a, b])
        normal = nb.get("normal") and nc.get("normal")
        if not hom.get("applicable"):
            hom = {"test": "brown_forsythe", "applicable": False,
                   "reason": "not assessable"}
        if normal and hom.get("homogeneous"):
            test = stats.student_t(a, b)
        elif normal and not hom.get("homogeneous"):
            test = stats.welch_t(a, b)
        else:
            test = stats.mann_whitney_u(a, b)
        test["group_names"] = group_names
        test["normality_group1"] = nb
        test["normality_group2"] = nc
        test["variance_homogeneity"] = hom
        test["method_rationale"] = _rationale(normal, hom)
        analysis["inferential"].append(test)
        if test.get("test") == "mann_whitney_u":
            eff = stats.hedges_g(a, b)  # still report for direction/magnitude
            eff["note"] = "nonparametric test; effect size is descriptive"
        else:
            eff = stats.cohens_d(a, b)
        n1, n2 = len(stats._as_floats(a)), len(stats._as_floats(b))
        ci = stats.effect_ci(eff["value"], n1, n2)
        eff["ci"] = ci
        if not hom.get("homogeneous"):
            glass = stats.glass_delta(a, b)
            eff["glass_delta_when_heterogeneous"] = glass
        eff["group_names"] = group_names
        analysis["effects"].append(eff)
        analysis["inferential"][-1]["mean_diff_ci"] = stats.mean_diff_ci(a, b)
    elif measure and group and len(group_names) >= 3:
        hom = stats.brown_forsythe([gmap[g] for g in group_names])
        normal_all = all(
            stats.jarque_bera(gmap[g]).get("normal") for g in group_names)
        if normal_all and hom.get("homogeneous"):
            test = stats.one_way_anova([gmap[g] for g in group_names])
        else:
            test = stats.kruskal_wallis([gmap[g] for g in group_names])
        test["group_names"] = group_names
        test["variance_homogeneity"] = hom
        test["method_rationale"] = ("anova" if normal_all and hom.get("homogeneous")
                                    else "nonparametric_fallback")
        analysis["inferential"].append(test)
        if test.get("test") == "one_way_anova":
            analysis["effects"].append({
                "effect": "eta_squared", "value": test["eta_squared"],
                "omega_squared": test["omega_squared"], "group_names": group_names,
                "note": "variance-explained proportion, not a group difference CI",
            })
    elif measure and not group:
        # single-sample / descriptive only: give a mean CI
        analysis["effects"].append(stats.mean_ci(gmap.get("all", [])))

    # cleaned csv
    os.makedirs(work_dir, exist_ok=True)
    cleaned_path = os.path.join(work_dir, "cleaned.csv")
    if cleaned:
        cols = list(cleaned[0].keys())
        with open(cleaned_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(cleaned)
        analysis["cleaned_csv"] = cleaned_path

    analysis["ok"] = True
    analysis["status"] = STATUS_PARTIAL
    analysis_path = os.path.join(work_dir, "analysis.json")
    with open(analysis_path, "w", encoding="utf-8") as fh:
        json.dump(analysis, fh, ensure_ascii=False, indent=2, default=_json_default)
    return analysis


def _rationale(normal, hom):
    if normal and hom.get("homogeneous"):
        return "normal + homogeneous variance -> Student t"
    if normal and not hom.get("homogeneous"):
        return "normal + heterogeneous variance -> Welch t"
    return "non-normal or small sample -> Mann-Whitney U"


def _json_default(obj):
    if isinstance(obj, float):
        if math_isfinite(obj):
            return obj
        return None
    return str(obj)


def math_isfinite(x):
    try:
        import math as _m
        return _m.isfinite(x)
    except TypeError:
        return False


# ---------------------------------------------------------------------------
# emit / audit
# ---------------------------------------------------------------------------
_FIELD_MAP = {
    "result": "analysis", "results": "analysis", "analysis": "analysis",
    "findings": "analysis", "descriptive": "descriptive",
    "inferential": "inferential", "effects": "effects",
    "quality": "quality", "quality_checks": "quality",
    "interpretation": "interpretation", "cleaning": "cleaning",
}


def _deep_set(obj, key, value):
    if isinstance(obj, dict):
        if key in obj:
            obj[key] = value
            return True
    return False


def run_emit(input_dir, work_dir, output_dir):
    case, _, err = load_case(input_dir)
    skeleton = build_skeleton(case) if case else {"case_id": "unknown-case"}
    analysis_path = os.path.join(work_dir, "analysis.json")
    analysis = {}
    if os.path.isfile(analysis_path):
        with open(analysis_path, encoding="utf-8") as fh:
            analysis = json.load(fh)
    # 1) keep contract key order, fill mapped fields
    for key in list(skeleton.keys()):
        if key == "case_id":
            continue
        mapped = _FIELD_MAP.get(key.lower())
        if mapped and mapped in analysis:
            skeleton[key] = analysis[mapped]
    # 2) envelope
    skeleton["case_id"] = analysis.get("case_id", case.get("case_id", "unknown-case")
                                       if case else "unknown-case")
    skeleton["status"] = _final_status(skeleton, analysis)
    skeleton["sources"] = analysis.get("sources", _default_sources(input_dir))
    skeleton["uncertainty"] = _default_uncertainty(analysis)
    skeleton["pending_human"] = analysis.get("pending_human", [])
    skeleton["change_log"] = analysis.get("change_log", [])
    skeleton["errors"] = analysis.get("errors", [])
    skeleton["degradations"] = analysis.get("degradations", [])
    skeleton["runtime"] = {
        "language": "python", "stdlib_only": True, "deterministic": True,
        "seed": None, "seconds": None,
    }
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(skeleton, fh, ensure_ascii=False, indent=2, default=_json_default)
    return skeleton


def _final_status(skeleton, analysis):
    if analysis.get("status") == STATUS_INSUFFICIENT:
        return STATUS_INSUFFICIENT
    if analysis.get("ok") is False:
        return analysis.get("status", STATUS_PARTIAL)
    has_results = bool(analysis.get("descriptive") or analysis.get("inferential")
                       or analysis.get("effects"))
    return STATUS_OK if has_results else STATUS_PARTIAL


def _default_sources(input_dir):
    out = []
    sid = 0
    for name in ("case.json", "task.md"):
        p = os.path.join(input_dir, name)
        if os.path.isfile(p):
            sid += 1
            out.append({"source_id": "S%d" % sid, "path": p,
                        "kind": "text", "rel_path": name})
    return out


def _default_uncertainty(analysis):
    u = []
    if analysis.get("design", {}).get("unit_unknown"):
        u.append("field unit not stated in dictionary; reported as unit_unknown")
    if analysis.get("cleaning", {}).get("n_missing_measure"):
        u.append("rows missing the measure column were excluded, count recorded")
    return u


def run_audit(input_dir, output_dir):
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(result_path):
        return {"ok": False, "errors": ["result.json missing"]}
    try:
        with open(result_path, encoding="utf-8") as fh:
            result = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "errors": ["result.json unparseable: %s" % exc]}
    case, _, _ = load_case(input_dir)
    contract = case.get("output_contract") if case else None
    contract_keys = list(contract.keys()) if isinstance(contract, dict) else []
    alignment = []
    missing = []
    for key in contract_keys:
        present = key in result
        alignment.append({"field": key, "present": present,
                          "value_type": type(result.get(key)).__name__})
        if not present:
            missing.append(key)
    checks = {
        "json_parseable": True,
        "has_case_id": bool(result.get("case_id")),
        "has_status": result.get("status") in
        (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR),
        "contract_fields_present": not missing,
        "contract_alignment": alignment,
        "missing_contract_fields": missing,
        "change_log_present": "change_log" in result,
        "sources_present": "sources" in result,
        "uncertainty_present": "uncertainty" in result,
    }
    checks["ok"] = checks["json_parseable"] and checks["has_case_id"] \
        and checks["has_status"] and checks["contract_fields_present"]
    return checks


def write_skeleton(output_dir, skeleton):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(skeleton, fh, ensure_ascii=False, indent=2, default=_json_default)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="contract-first statistical analysis")
    ap.add_argument("command", choices=["skeleton", "survey", "inspect",
                                        "analyze", "emit", "audit"])
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--plan", default=None, help="optional JSON plan file")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)

    if args.command == "skeleton":
        case, _, err = load_case(args.input)
        if err:
            case = {"case_id": "unknown-case", "output_contract": {}}
        skeleton = build_skeleton(case)
        path = write_skeleton(args.output, skeleton)
        with open(os.path.join(args.work, "index.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": skeleton["case_id"],
                       "contract_keys": list(skeleton.keys())}, fh, ensure_ascii=False)
        print("skeleton written:", path)
        return 0

    if args.command == "survey":
        case, case_path, err = load_case(args.input)
        files = discover_files(args.input)
        survey = {"case_path": case_path, "case_error": err,
                  "materials": files,
                  "tools_detected": ["python-stdlib", "sqlite3", "csv", "json"]}
        with open(os.path.join(args.work, "survey.json"), "w", encoding="utf-8") as fh:
            json.dump(survey, fh, ensure_ascii=False, indent=2)
        print(json.dumps(survey, ensure_ascii=False, indent=2))
        return 0

    if args.command == "inspect":
        files = discover_files(args.input)
        report = {"materials": []}
        for f in files:
            if f["skipped"]:
                report["materials"].append({"file": f["name"], "error": "size limit"})
                continue
            rows, err = load_table(f["path"])
            if err:
                report["materials"].append({"file": f["name"], "error": err})
                continue
            if isinstance(rows, dict):
                for tname, trows in rows.items():
                    report["materials"].append({
                        "file": f["name"], "table": tname,
                        "n_rows": len(trows) if isinstance(trows, list) else None,
                        "schema": infer_schema(trows) if isinstance(trows, list) else None,
                    })
            else:
                report["materials"].append({
                    "file": f["name"], "n_rows": len(rows),
                    "schema": infer_schema(rows)})
        with open(os.path.join(args.work, "schema.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "analyze":
        plan = None
        if args.plan and os.path.isfile(args.plan):
            with open(args.plan, encoding="utf-8") as fh:
                plan = json.load(fh)
        analysis = run_analyze(args.input, args.work, plan)
        print(json.dumps(analysis, ensure_ascii=False, indent=2, default=_json_default))
        return 0

    if args.command == "emit":
        run_emit(args.input, args.work, args.output)
        print("result.json emitted to", os.path.join(args.output, "result.json"))
        return 0

    if args.command == "audit":
        report = run_audit(args.input, args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        # Never leave a zero-artifact state: best-effort skeleton.
        try:
            out = os.environ.get("OUTPUT_DIR", "/app/output")
            os.makedirs(out, exist_ok=True)
            write_skeleton(out, {"case_id": "unknown-case", "status": STATUS_PARTIAL,
                                 "errors": [traceback.format_exc()],
                                 "sources": [], "uncertainty": [],
                                 "change_log": [], "pending_human": [],
                                 "degradations": ["hand-written fallback"]})
        except Exception:  # noqa: BLE001
            pass
        sys.exit(0)
