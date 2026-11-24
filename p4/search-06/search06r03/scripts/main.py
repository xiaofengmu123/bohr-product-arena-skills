# -*- coding: utf-8 -*-
"""Entry point for the literature-tracking Skill.

Usage (paths are relative to the skill root, e.g. /skills/weekly-skill):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work \
                                      --findings /app/work/findings.json
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'

``scaffold`` must be run within the first minutes of an execution: it writes a
valid *partial* result.json (case_id + every output_contract field + honest
status) so a crash still leaves a legal deliverable.  ``run`` does scaffold +
deterministic material inventory + finalize with empty findings.  ``finalize``
merges the agent's findings (the only place substantive literature judgements
live) and writes result.json + report.md.

The scripts never fabricate literature content: missing inputs yield
``insufficient_evidence`` or ``input_error``, not invented records.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline  # noqa: E402


def _args(parser):
    parser.add_argument("--input", default="/input", help="input dir (default /input)")
    parser.add_argument("--output", default="/app/output", help="output dir (default /app/output)")
    parser.add_argument("--work", default="/app/work", help="work dir (default /app/work)")
    parser.add_argument("--findings", default="/app/work/findings.json",
                        help="agent-written findings JSON")
    return parser


def cmd_scaffold(a):
    """Write the partial skeleton result.json immediately."""
    case_id, contract_keys, data, errors = pipeline.extract_case(a.input)
    status = pipeline.initial_status(data, errors)
    result = pipeline.make_skeleton(case_id, contract_keys, status=status)
    result["sources"] = []
    result["errors"] = list(errors)
    out_dir = Path(a.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = pipeline.write_json(str(out_dir / "result.json"), result)
    print("scaffold -> %s (case_id=%s status=%s contract=%s)" %
          (path, case_id, status, ",".join(contract_keys) or "(none)"))
    return 0


def cmd_inventory(a):
    """Deterministic inventory of input materials (no literature judgement)."""
    case_id, contract_keys, data, errors = pipeline.extract_case(a.input)
    inv = {
        "case_id": case_id,
        "case_path": data.get("case_path"),
        "task_path": data.get("task_path"),
        "materials": data.get("materials") or [],
        "material_count": len(data.get("materials") or []),
        "contract_keys": contract_keys,
        "errors": errors,
    }
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    path = pipeline.write_json(str(work / "inventory.json"), inv)
    print(json.dumps(inv, ensure_ascii=False, indent=2, sort_keys=True))
    print("inventory -> %s" % path)
    return 0


def cmd_dedup(a):
    """Deduplicate a records list and write deterministic groups + classification."""
    if not Path(a.records).exists():
        print("records file missing: %s" % a.records, file=sys.stderr)
        return 2
    records, errs = pipeline.load_json(a.records)
    if records is None:
        print("; ".join(errs), file=sys.stderr)
        return 2
    if not isinstance(records, list):
        records = records.get("records", []) if isinstance(records, dict) else []
    result = pipeline.dedup_records(records)
    result["errors"] = errs
    path = pipeline.write_json(a.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print("dedup -> %s" % path)
    return 0


def cmd_normalize(a):
    """Normalise a single record and print its canonical id + keys."""
    try:
        rec = json.loads(a.record)
    except Exception as exc:
        print("bad record JSON: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps({
        "canonical_id": pipeline.canonical_id(rec),
        "title": pipeline.norm_title(rec.get("title")),
        "doi": pipeline.norm_doi(rec.get("doi") or rec.get("DOI")),
        "arxiv": pipeline.norm_arxiv(rec.get("arxiv") or rec.get("arXiv") or rec.get("arxiv_id")),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_finalize(a):
    """Merge findings and write final result.json + report.md."""
    result, rpath, mpath, errors = pipeline.finalize(
        a.input, a.output, a.work, findings_path=a.findings)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print("finalize -> %s" % rpath)
    print("report   -> %s" % mpath)
    for e in errors:
        print("error: %s" % e, file=sys.stderr)
    return 0


def cmd_run(a):
    """scaffold + inventory + finalize-with-empty-findings (deterministic dry run)."""
    rc = cmd_scaffold(a)
    rc2 = cmd_inventory(a)
    work = Path(a.work)
    empty_findings = work / "findings.empty.json"
    pipeline.write_json(str(empty_findings), {"status": "insufficient_evidence",
                                              "pending_human": ["analysis not yet run"]})
    a.findings = str(empty_findings)
    rc3 = cmd_finalize(a)
    return rc or rc2 or rc3


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scripts/main.py",
        description="Deterministic literature-tracking skill runner.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold", help="write partial result skeleton")
    _args(p); p.set_defaults(fn=cmd_scaffold)

    p = sub.add_parser("inventory", help="inventory input materials")
    _args(p); p.set_defaults(fn=cmd_inventory)

    p = sub.add_parser("dedup", help="deduplicate a records list")
    p.add_argument("--records", required=True, help="records JSON path")
    p.add_argument("--out", required=True, help="output dedup JSON path")
    p.set_defaults(fn=cmd_dedup)

    p = sub.add_parser("normalize", help="normalise one record")
    p.add_argument("--record", required=True, help="record as JSON string")
    p.set_defaults(fn=cmd_normalize)

    p = sub.add_parser("finalize", help="merge findings and write results")
    _args(p); p.set_defaults(fn=cmd_finalize)

    p = sub.add_parser("run", help="scaffold + inventory + finalize (dry run)")
    _args(p); p.set_defaults(fn=cmd_run)

    a = parser.parse_args(argv)
    try:
        return a.fn(a)
    except OSError as exc:
        # Last-resort write-failure fallback: emit a minimal legal JSON and stay alive.
        print("fatal: %s" % exc, file=sys.stderr)
        try:
            out_dir = Path(a.output)
            out_dir.mkdir(parents=True, exist_ok=True)
            pipeline.write_json(str(out_dir / "result.json"),
                                {"case_id": "unknown-case", "status": "input_error",
                                 "errors": [str(exc)]})
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
