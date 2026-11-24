#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent verifier for sci-cleaning-pipeline (selftest.py).

Runs AFTER the executor (core_impl) and does NOT trust the generator:
it re-parses cleaned_data.csv, recomputes per-column missing/value class
counts from the `__class` annotation columns, and compares them against
data_profile.json and the numbers printed in quality_report.md. It also
validates the result.json contract (case_id, status, flattened
output_contract keys, sources/uncertainty/change_log).

Standard-library only, deterministic, exit code 0. Results are written to
/app/output/selfcheck.json and appended to result.json change_log.
"""

import csv
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl

CLASSES = ("entry_missing", "below_lod", "true_zero", "value", "text")


def _read_json(path):
    if not os.path.exists(path):
        return None, f"missing {os.path.basename(path)}"
    info = core_impl.read_text_robust(path)
    obj, status = core_impl.parse_json_robust(info.get("text", ""), {})
    if status != "ok":
        return None, f"{os.path.basename(path)} is not valid JSON"
    return obj, None


def recompute_from_csv(path):
    """Recompute per-column class counts from the __class annotation columns."""
    if not os.path.exists(path):
        return None, "missing cleaned_data.csv"
    info = core_impl.read_text_robust(path)
    text = info.get("text", "")
    if not text.strip():
        return None, "cleaned_data.csv is empty"
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader]
    if not rows:
        return None, "cleaned_data.csv has no rows"
    header = rows[0]
    if not header or header[0] != "_row_id":
        return None, "cleaned_data.csv missing leading _row_id column"
    n_rest = len(header) - 1
    if n_rest % 2 != 0:
        return None, "cleaned_data.csv header not symmetric (original + __class)"
    n_orig = n_rest // 2
    orig_cols = header[1:1 + n_orig]
    class_cols = header[1 + n_orig:]
    counts = {col: {c: 0 for c in CLASSES} for col in orig_cols}
    for row in rows[1:]:
        for ci, col in enumerate(orig_cols):
            cls_idx = 1 + n_orig + ci
            cls = row[cls_idx] if cls_idx < len(row) else "text"
            if cls in CLASSES:
                counts[col][cls] += 1
    return {"n_rows": len(rows) - 1, "n_cols": n_orig, "columns": counts}, None


def parse_report_counts(md_text):
    """Extract per-column counts from quality_report.md's field-level table."""
    out = {}
    in_table = False
    for line in md_text.splitlines():
        if line.startswith("| 字段"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) < 11:
                continue
            col = parts[0]
            try:
                out[col] = {
                    "total": int(parts[1]), "entry_missing": int(parts[2]),
                    "below_lod": int(parts[3]), "true_zero": int(parts[4]),
                    "value": int(parts[5]), "text": int(parts[6]),
                }
            except (ValueError, IndexError):
                continue
    return out


def run(input_dir, output_dir, work_dir):
    checks = []
    failed = 0

    def add(name, ok, detail):
        nonlocal failed
        if not ok:
            failed += 1
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 1. result.json
    result, err = _read_json(os.path.join(output_dir, "result.json"))
    add("result.json exists and is valid JSON", result is not None,
        err or "loaded")
    if result is None:
        report = {"ok": False, "total": len(checks), "failed": failed,
                  "checks": checks, "recomputed": None}
        core_impl.write_json(os.path.join(output_dir, "selfcheck.json"), report)
        return report

    # 2. case_id / status
    add("case_id present", bool(result.get("case_id")), str(result.get("case_id")))
    add("status is a valid choice", result.get("status") in core_impl.STATUS_CHOICES,
        str(result.get("status")))
    add("sources/uncertainty/change_log are lists",
        all(isinstance(result.get(k), list) for k in ("sources", "uncertainty", "change_log")),
        "checked list fields")

    # 3. output_contract keys all flattened (verbatim, no key dropped)
    case = core_impl.load_case(input_dir)
    contract = case.get("output_contract") or {}
    missing_keys = [k for k in contract if k not in result]
    add("all output_contract keys present at top level", not missing_keys,
        f"contract keys={list(contract)}, missing={missing_keys}")

    # 4. recompute from cleaned_data.csv
    rec, err = recompute_from_csv(os.path.join(output_dir, "cleaned_data.csv"))
    add("cleaned_data.csv parses with symmetric header", rec is not None, err or "ok")

    # 5. data_profile.json cross-check
    profile, perr = _read_json(os.path.join(output_dir, "data_profile.json"))
    add("data_profile.json valid", profile is not None, perr or "loaded")
    if rec is not None and profile is not None:
        prof_cols = profile.get("columns") or {}
        mismatches = []
        for col in rec["columns"]:
            p = prof_cols.get(col)
            if p is None:
                mismatches.append(f"{col}: missing from profile")
                continue
            pc = p.get("counts") or {}
            for cls in CLASSES:
                if pc.get(cls) != rec["columns"][col].get(cls):
                    mismatches.append(
                        f"{col}/{cls}: csv={rec['columns'][col].get(cls)} "
                        f"profile={pc.get(cls)}")
        add("profile counts match recomputed CSV counts", not mismatches,
            "all match" if not mismatches else "; ".join(mismatches[:8]))

    # 6. quality_report.md cross-check
    report_md_path = os.path.join(output_dir, "quality_report.md")
    if os.path.exists(report_md_path):
        info = core_impl.read_text_robust(report_md_path)
        rep_counts = parse_report_counts(info.get("text", ""))
        if rec is not None and rep_counts:
            rep_mismatch = []
            for col in rec["columns"]:
                r = rep_counts.get(col)
                if r is None:
                    rep_mismatch.append(f"{col}: missing from report")
                    continue
                for cls in CLASSES:
                    if r.get(cls) != rec["columns"][col].get(cls):
                        rep_mismatch.append(
                            f"{col}/{cls}: report={r.get(cls)} "
                            f"csv={rec['columns'][col].get(cls)}")
            add("quality_report.md numbers match recomputed CSV", not rep_mismatch,
                "all match" if not rep_mismatch else "; ".join(rep_mismatch[:8]))
        else:
            add("quality_report.md numbers match recomputed CSV", False,
                "report table could not be parsed or CSV unavailable")
    else:
        add("quality_report.md exists", False, "missing quality_report.md")

    # 7. evidence_map.json structural check
    emap, eerr = _read_json(os.path.join(output_dir, "evidence_map.json"))
    add("evidence_map.json valid", emap is not None, eerr or "loaded")
    if emap is not None:
        entries = emap.get("entries") or []
        bad = [i for i, e in enumerate(entries)
               if not (isinstance(e, dict) and e.get("locator") and "quote" in e)]
        add("evidence_map entries carry locator + quote", not bad,
            f"entries={len(entries)}, missing={len(bad)}")

    # 8. runtime sanity
    rt = result.get("runtime") or {}
    add("runtime records started/finished",
        bool(rt.get("started") and rt.get("finished")), str(rt))

    report = {"ok": failed == 0, "total": len(checks), "failed": failed,
              "checks": checks, "recomputed": rec}
    core_impl.write_json(os.path.join(output_dir, "selfcheck.json"), report)

    # Append selfcheck trace to result.json change_log (honest, incremental).
    result.setdefault("change_log", []).append({
        "time": core_impl.now_iso(), "step": "selfcheck",
        "action": f"independent verifier: ok={failed == 0}, total={len(checks)}, "
                  f"failed={failed}",
    })
    if failed:
        result.setdefault("errors", []).append(
            f"selfcheck failed {failed} check(s); see selfcheck.json")
    core_impl.write_json(os.path.join(output_dir, "result.json"), result)
    return report


if __name__ == "__main__":
    d = "/app/output"
    rep = run("/input", d, "/app/work")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0)
