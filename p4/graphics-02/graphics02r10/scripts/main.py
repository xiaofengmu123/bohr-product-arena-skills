#!/usr/bin/env python3
"""Entry point for the sci-figure Skill.

Usage (relative to the Skill root, run by the Agent):
    python scripts/main.py --phase bootstrap
    python scripts/main.py --phase extract
    python scripts/main.py --phase build [--table t_fig3a --kind bar]
    python scripts/main.py --phase render
    python scripts/main.py --phase validate
    python scripts/main.py --phase finalize
    python scripts/main.py --phase all

Defaults target the runtime layout: /input/case.json, /input, /app/work,
/app/output.  Any unexpected exception is captured and folded into a valid
partial/failed result.json; the process exits 0 rather than crashing.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl  # noqa: E402

PHASES = ["bootstrap", "extract", "build", "render", "validate", "finalize"]


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="main.py", description="sci-figure deterministic pipeline")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--input", dest="input_dir", default="/input")
    ap.add_argument("--work", dest="work_dir", default="/app/work")
    ap.add_argument("--output", dest="output_dir", default="/app/output")
    ap.add_argument("--phase", default="all",
                    help="comma list or 'all' of: %s" % ", ".join(PHASES))
    ap.add_argument("--table", default=None, help="parsed table name for build scaffold")
    ap.add_argument("--kind", default=None, help="figure kind for build scaffold")
    ap.add_argument("--name", default="figure", help="figure id for build scaffold")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv or sys.argv[1:])
    for d in (a.work_dir, a.output_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    phases = PHASES if a.phase == "all" else [p.strip() for p in a.phase.split(",") if p.strip()]
    log = []
    try:
        if "build" in phases and (a.table or a.kind):
            ir = core_impl.scaffold_ir(a.work_dir, name=a.name,
                                       kind=a.kind or "bar", table_name=a.table)
            log.append("scaffold %s glyphs=%d" % ((ir.get("figure") or {}).get("kind"),
                                                  len(ir.get("glyphs", []))))
        log.extend(core_impl.pipeline(a.case, a.input_dir, a.work_dir, a.output_dir, phases))
        print("OK " + "; ".join(log))
        return 0
    except Exception as exc:
        # Last-resort honest failure: never leave a directory with no result.json.
        try:
            core_impl.write_json(os.path.join(a.output_dir, "result.json"), {
                "case_id": None, "status": "failed", "output_contract": {},
                "sources": [{"kind": "case", "path": a.case}],
                "uncertainty": [], "pending_human": [], "errors": [str(exc)],
                "degradations": [], "evidence_map": [], "annotation_checks": {},
                "change_log": [{"step": "crash", "time_utc": core_impl.now_utc(),
                                "status": "failed", "note": str(exc)}],
                "runtime": {"version": core_impl.VERSION, "started_utc": core_impl.now_utc(),
                            "finished_utc": core_impl.now_utc()},
            })
        except Exception:
            pass
        print("FAILED %s" % exc, file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
