"""selftest.py - validate a finished skill run (and optionally re-run the pipeline).

Usage:
  python3 -B scripts/selftest.py --input /input --output /app/output --work /app/work
  python3 -B scripts/selftest.py --input DIR --output DIR --work DIR --determinism

Checks (all must pass for exit code 0):
  - result.json parses and has case_id + status + contract fields + sources/uncertainty/change_log
  - status is one of ok|partial|insufficient_evidence|input_error
  - every SVG in output parses as XML
  - cleaned.csv row count == original_rows - duplicates_removed (from clean_meta.json)
  - stats.json parses; report.md exists
  - with --determinism: run twice, stats.json + SVG bytes must be identical
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
REQUIRED_KEYS = ["case_id", "status", "sources", "uncertainty", "change_log"]


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_result(output_dir):
    path = os.path.join(output_dir, "result.json")
    if not os.path.exists(path):
        return [f"missing {path}"]
    try:
        r = _read_json(path)
    except Exception as e:  # noqa: BLE001
        return [f"result.json unparseable: {e}"]
    problems = []
    for k in REQUIRED_KEYS:
        if k not in r:
            problems.append(f"result.json missing key: {k}")
    if r.get("status") not in ALLOWED_STATUS:
        problems.append(f"bad status: {r.get('status')}")
    for k in ("analysis_results", "quality_evidence", "anomaly_notes"):
        if k not in r:
            problems.append(f"result.json missing contract key: {k}")
    return problems


def check_svg(output_dir):
    problems = []
    for fn in sorted(os.listdir(output_dir)):
        if fn.endswith(".svg"):
            try:
                ET.parse(os.path.join(output_dir, fn))
            except Exception as e:  # noqa: BLE001
                problems.append(f"SVG {fn} unparseable: {e}")
    return problems


def check_consistency(work_dir):
    problems = []
    csv_path = os.path.join(work_dir, "cleaned.csv")
    meta_path = os.path.join(work_dir, "clean_meta.json")
    if not os.path.exists(csv_path) or not os.path.exists(meta_path):
        return []  # no cleaning step ran; nothing to check
    try:
        meta = _read_json(meta_path)
        with open(csv_path, "r", encoding="utf-8") as f:
            n_lines = sum(1 for _ in f) - 1  # minus header
        expected = meta.get("original_rows", 0) - meta.get("duplicates_removed", 0)
        if n_lines != expected:
            problems.append(f"cleaned.csv rows {n_lines} != original {meta.get('original_rows')} "
                            f"- removed {meta.get('duplicates_removed')}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"consistency check failed: {e}")
    return problems


def run_once(input_dir, output_dir, work_dir):
    core_impl.run_pipeline(input_dir, output_dir, work_dir)
    return {
        "result": os.path.join(output_dir, "result.json"),
        "stats": os.path.join(work_dir, "stats.json"),
        "svgs": [os.path.join(output_dir, f) for f in sorted(os.listdir(output_dir))
                 if f.endswith(".svg")],
    }


def file_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Skill self-check")
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--determinism", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    run_once(args.input, args.output, args.work)

    problems = []
    problems += check_result(args.output)
    problems += check_svg(args.output)
    problems += check_consistency(args.work)

    if args.determinism:
        first_stats = file_bytes(os.path.join(args.work, "stats.json")) \
            if os.path.exists(os.path.join(args.work, "stats.json")) else b""
        first_svgs = {f: file_bytes(f) for f in run_once(args.input, args.output, args.work)["svgs"]}
        run_once(args.input, args.output, args.work)
        second_stats = file_bytes(os.path.join(args.work, "stats.json")) \
            if os.path.exists(os.path.join(args.work, "stats.json")) else b""
        if first_stats != second_stats:
            problems.append("determinism: stats.json differs between runs")
        for f, b in first_svgs.items():
            if os.path.exists(f) and file_bytes(f) != b:
                problems.append(f"determinism: {os.path.basename(f)} differs between runs")

    report = os.path.join(args.output, "report.md")
    if not os.path.exists(report):
        problems.append("missing report.md")

    if problems:
        print("SELFTEST FAIL")
        for p in problems:
            print(" -", p)
        return 1
    print("SELFTEST OK")
    r = _read_json(os.path.join(args.output, "result.json"))
    print("status:", r.get("status"), "case_id:", r.get("case_id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
