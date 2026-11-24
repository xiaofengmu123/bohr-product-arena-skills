"""Graphical-abstract Skill CLI (standard library only).

Usage (run from /skills/weekly-skill):
    python scripts/main.py --phase skeleton   # <=3 min early checkpoint
    python scripts/main.py --phase build      # figure_spec.json -> SVG + previews
    python scripts/main.py --phase validate   # schema + provenance + exports
    python scripts/main.py --phase finalize   # status / limitations / change_log
    python scripts/main.py --phase run        # all phases (CLI convenience)
    python scripts/main.py --phase ledger     # print stage budget ledger

All phases are idempotent and deterministic. ``skeleton`` is the mandatory
first step: it writes /app/output/result.json (status=partial) and the stage
ledger before any fact extraction, satisfying the early-checkpoint rule.
"""

import argparse
import json
import os
import sys

import core_impl as ci


def _resolve(args, key, default):
    val = getattr(args, key, None)
    return val or default


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Deterministic graphical-abstract pipeline with phase budgets")
    p.add_argument("--phase", choices=["skeleton", "build", "validate",
                                       "finalize", "run", "ledger"],
                   default="skeleton")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--json", action="store_true",
                   help="print machine-readable JSON to stdout")
    args = p.parse_args(argv)

    input_dir = _resolve(args, "input", "/input")
    output_dir = _resolve(args, "output", "/app/output")
    work_dir = _resolve(args, "work", "/app/work")

    out = {}

    if args.phase == "skeleton":
        out = ci.normalize_case(input_dir, work_dir, output_dir)
        out.pop("result", None)
        out.pop("spec", None)
    elif args.phase == "build":
        spec_path = os.path.join(work_dir, "figure_spec.json")
        spec, err = ci.load_json_robust(spec_path)
        if err is not None or not isinstance(spec, dict):
            out = {"ok": False, "error": f"figure_spec.json missing: {err}"}
        else:
            case_id = spec.get("case_id") or "case"
            svg_path = os.path.join(output_dir, f"fig_{case_id}.svg")
            ci.save_svg(ci.build_svg(spec), svg_path)
            exports, renderers = ci.attempt_render(svg_path, output_dir, case_id)
            ci.write_json(os.path.join(work_dir, "fig_manifest.json"), {
                "case_id": case_id, "svg": os.path.basename(svg_path),
                "exports": exports, "renderers": renderers,
                "generated_at": ci._now_iso()})
            # Progressively sync exports into result.json so an interrupted run
            # still records honest preview state.
            result_path = os.path.join(output_dir, "result.json")
            result, _ = ci.load_json_robust(result_path)
            if isinstance(result, dict):
                result["exports"] = exports
                ci.write_json(result_path, result)
            ledger = ci.read_ledger(work_dir) or ci.init_ledger(case_id)
            ci.write_ledger(work_dir, ledger, "build", ok=True,
                            detail=f"renderers={renderers or 'none'}")
            out = {"ok": True, "svg": os.path.basename(svg_path),
                   "exports": exports, "renderers": renderers}
    elif args.phase == "validate":
        result_path = os.path.join(output_dir, "result.json")
        spec_path = os.path.join(work_dir, "figure_spec.json")
        spec, _ = ci.load_json_robust(spec_path)
        case_id = (spec or {}).get("case_id", "case")
        svg_path = os.path.join(output_dir, f"fig_{case_id}.svg")
        rep = ci.validate_output(result_path, svg_path, work_dir)
        ci.write_json(os.path.join(work_dir, "validation_report.json"), rep)
        out = {"ok": rep.get("ok"), "errors": rep["errors"],
               "warnings": rep["warnings"]}
    elif args.phase == "finalize":
        result_path = os.path.join(output_dir, "result.json")
        spec_path = os.path.join(work_dir, "figure_spec.json")
        spec, _ = ci.load_json_robust(spec_path)
        case_id = (spec or {}).get("case_id", "case")
        svg_path = os.path.join(output_dir, f"fig_{case_id}.svg")
        result, _ = ci.load_json_robust(result_path)
        # Prefer the build's manifest as the source of truth for exports; fall
        # back to result.json when build was skipped.
        manifest, _ = ci.load_json_robust(
            os.path.join(work_dir, "fig_manifest.json"))
        exports = ((manifest or {}).get("exports")
                   or (result or {}).get("exports") or [])
        renderers = [e.get("renderer") for e in exports if e.get("renderer")]
        ok, rep = ci.finalize(result_path, svg_path, work_dir, case_id,
                              exports, renderers)
        ledger = ci.read_ledger(work_dir) or ci.init_ledger(case_id)
        ci.write_ledger(work_dir, ledger, "finalize", ok=ok,
                        detail=f"ok={ok}; status={ci.load_json_robust(result_path)[0].get('status')}")
        out = {"ok": ok, "status": rep and "completed" if ok else "partial"}
    elif args.phase == "run":
        out = ci.run_pipeline(input_dir, output_dir, work_dir)
    elif args.phase == "ledger":
        ledger = ci.read_ledger(work_dir)
        out = ledger or {"error": "no ledger; run skeleton first"}

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        # The skeleton phase always exits 0: writing a valid degraded result
        # (status=failed) is a success of the checkpoint, not a process error.
        degraded_ok = args.phase == "skeleton"
        if not degraded_ok and (out.get("ok") is False or out.get("errors")):
            print("PHASE", args.phase, "FAILED:", out)
            return 1
        print("PHASE", args.phase, "OK")
        if args.phase in ("skeleton", "build", "run"):
            for k in ("case_id", "svg", "renderers", "validation"):
                if k in out:
                    print(f"  {k}: {out[k]}")
        if degraded_ok and out.get("errors"):
            print("  degraded:", "; ".join(out["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
