#!/usr/bin/env python3
"""Entry point for the review-response revision workflow.

Usage (from the skill root, so relative imports resolve):

    python scripts/main.py scaffold --input /input --out /app/output --work /app/work
    python scripts/main.py check    --input /input --out /app/output --work /app/work
    python scripts/main.py render   --input /input --out /app/output --work /app/work
    python scripts/main.py merge    --input /input --out /app/output --work /app/work
    python scripts/main.py all      --input /input --out /app/output --work /app/work

`all` runs scaffold -> check -> render -> merge in one pass and always exits 0
for an empty or malformed input (writing an honest minimal result.json), so a
partial deliverable is never lost.
"""

import argparse
import json
import os
import sys

import core_impl as core


def _run(a):
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)

    inputs = core.discover(a.input, a.case, a.task)
    case_id = inputs.get("case_id") or "unknown"
    if inputs.get("case_json") is not None and case_id == "unknown":
        case_id = str(inputs.get("case_json", {}).get("id") or "unknown")

    result_path = os.path.join(a.out, "result.json")
    ledger_path = os.path.join(a.work, "revision_ledger.json")

    if a.command in ("scaffold", "all"):
        result = core.result_skeleton(case_id)
        core.append_change(result, "scaffold", "Result skeleton created; case_id=%s" % case_id)
        core.write_json(result_path, result)

        ledger = core.ledger_skeleton(case_id)
        ledger["manuscript_source"] = inputs.get("manuscript_path")
        ledger["comments_source"] = inputs.get("comments_path")
        if inputs.get("comments_text"):
            ledger["comments"] = core.split_comments(inputs["comments_text"])
        core.write_json(ledger_path, ledger)

        result["sources"] = core.build_sources(inputs)
        result["deliverable"]["title"] = inputs.get("case_id") or "Revised manuscript"
        core.write_json(result_path, result)

    if a.command in ("check", "all"):
        result = core.load_json_robust(result_path) or core.result_skeleton(case_id)
        ledger = core.load_json_robust(ledger_path) or core.ledger_skeleton(case_id)
        baseline = inputs.get("manuscript_text") or ""
        revised_path = os.path.join(a.out, "revised_manuscript.md")
        revised = baseline
        if os.path.exists(revised_path):
            revised, _ = core.read_text(revised_path)
        report = core.run_checks(ledger, baseline, revised or "")
        core.write_json(ledger_path, ledger)  # persist anchor_status updates
        core.write_json(os.path.join(a.work, "check_report.json"), report)
        result["version_boundary"]["anomalies"] = report.get("anomalies", [])
        result["errors"] = report.get("errors", [])
        core.append_change(result, "check", "Checks: %s" % json.dumps(report["summary"], ensure_ascii=False))
        core.write_json(result_path, result)

    if a.command in ("render", "all"):
        ledger = core.load_json_robust(ledger_path) or core.ledger_skeleton(case_id)
        baseline = inputs.get("manuscript_text") or ""
        core.write_text(os.path.join(a.out, "revised_manuscript.auto.md"), core.apply_edits(baseline, ledger))
        core.write_text(os.path.join(a.out, "response_letter.md"), core.render_response_letter(ledger, case_id))
        core.write_text(os.path.join(a.out, "revision_locations.md"), core.render_locations(ledger))
        core.write_text(os.path.join(a.out, "evidence_ledger.md"), core.render_evidence(ledger))

    if a.command in ("merge", "all"):
        result = core.load_json_robust(result_path) or core.result_skeleton(case_id)
        ledger = core.load_json_robust(ledger_path) or core.ledger_skeleton(case_id)
        inputs["material_missing"] = [
            k for k in ("manuscript_path", "comments_path") if not inputs.get(k)
        ]
        result = core.merge_result(result, ledger, a.out, inputs)
        core.write_json(result_path, result)

    print(json.dumps({"ok": True, "command": a.command, "case_id": case_id,
                      "result": result_path, "ledger": ledger_path}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Reviewer-response revision workflow")
    ap.add_argument("command", nargs="?", default="scaffold",
                    choices=["scaffold", "check", "render", "merge", "all"])
    ap.add_argument("--input", default="/input", help="input directory")
    ap.add_argument("--case", default=None, help="explicit case.json path (overrides --input scan)")
    ap.add_argument("--task", default=None, help="explicit task.md path (overrides --input scan)")
    ap.add_argument("--out", default="/app/output", help="output directory")
    ap.add_argument("--work", default="/app/work", help="working directory")
    a = ap.parse_args()

    try:
        return _run(a)
    except Exception as exc:  # last-resort: never leave the process with no result
        try:
            os.makedirs(a.out, exist_ok=True)
            core.write_json(os.path.join(a.out, "result.json"),
                            core.minimal_result((a.case or "unknown"), exc))
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
