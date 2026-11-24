"""main.py - one-shot orchestrator for the research KB evidence QA skill.

Pipeline (runs in this order):
  0. write a legal partial result skeleton to /app/output/result.json FIRST
  1. run six-dimension inventory (scripts/inventory.py)
  2. merge any /app/work/answers.json and emit final result (scripts/finalize.py)

The skeleton guarantees a valid partial artifact within the first minutes even
if a later stage fails. Pure standard library; deterministic ordering.

Usage:
    python3 scripts/main.py --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import inventory  # noqa: E402
import finalize  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Research KB evidence QA: skeleton -> inventory -> finalize.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    start_iso = finalize.now_iso()

    # Stage 0: legal partial skeleton first (recoverable baseline)
    case, case_path, case_err = finalize.load_case(args.input)
    skeleton = finalize.write_skeleton(case, args.output, args.work)
    # marker so finalize can reconstruct the skeleton step in the change log
    skel_meta = os.path.join(args.work, ".skeleton_meta.json")
    with open(skel_meta, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"start_utc": start_iso, "path": skeleton}, sort_keys=True))
    print("skeleton: %s (status=partial, case_id=%s)" % (
        skeleton, finalize.resolve_case_id(case)))

    # Stage 1: inventory (failure here must NOT wipe the skeleton)
    inv = None
    try:
        inv = inventory.run_inventory(args.input, args.work)
    except Exception as exc:  # noqa: BLE001
        print("inventory failed: %s (skeleton preserved)" % exc, file=sys.stderr)
        # degrade: write inventory.json as empty-with-error so finalize can still run
        inv = inventory._assemble(
            args.input, [], ["inventory_stage: %s" % exc], [],
            [{"limitation": "inventory stage crashed",
              "impact": "no file metadata; result stays partial",
              "files": [], "needs_human": True, "blocking": True}],
            [], [], [], {}, start_iso)
        inventory._write(inv, args.work)

    # Stage 2/3: merge answers + inventory into final result
    meta = {"action": "full pipeline via main.py", "status": "ok"}
    path, result = finalize.run_finalize(args.input, args.output, args.work,
                                         start_iso=start_iso, meta=meta)
    print("result: %s (status=%s)" % (path, result.get("status")))
    return 0


if __name__ == "__main__":
    sys.exit(main())