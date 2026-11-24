# -*- coding: utf-8 -*-
"""Entry point for the literature-tracking Skill (deterministic scaffolding).

All commands are stdlib-only, deterministic and side-effect free with respect
to literature content. The agent performs the actual search/judgment and
writes /app/work/findings.json; these commands only discover inputs,
normalize, deduplicate, validate evidence and assemble result.json/report.md.

Usage (relative paths, e.g. /skills/weekly-skill):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work \
                                      --findings /app/work/findings.json
    python3 scripts/main.py selftest
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402


def cmd_scaffold(args):
    case_id, contract, data, errors = core.extract_case(args.input)
    status = core.initial_status(data, errors)
    result = core.make_skeleton(case_id, contract, status)
    core.ensure_dir(args.work)
    core.ensure_dir(args.output)
    core.write_result(args.output, result)
    core.dump_json(str(Path(args.work) / "scaffold.json"), {
        "case_id": case_id,
        "contract_keys": contract,
        "status": status,
        "errors": errors,
    })
    print("scaffold: case_id=%s status=%s contract=%s" % (case_id, status, contract))
    return 0


def cmd_run(args):
    """One-shot fallback: scaffold + deterministic material inventory + finalize."""
    case_id, contract, data, errors = core.extract_case(args.input)
    status = core.initial_status(data, errors)
    core.ensure_dir(args.work)
    core.ensure_dir(args.output)
    core.make_skeleton(case_id, contract, status)

    inventory = []
    for p in data.get("materials", []):
        try:
            inventory.append(core.file_info(p))
        except Exception as exc:
            errors.append("inventory failed %s: %s" % (p, exc))

    findings = {
        "status": status,
        "search_results": [],
        "ranking": [],
        "evidence": [],
        "status_notes": {"empty": None, "conflicts": [], "permission_errors": []},
        "tracking": {
            "subscription": None, "window": None, "snapshot": None,
            "new_items": [], "updates": [], "retractions": [], "reminders": [],
        },
        "dedup": {},
        "evidence_map": {
            "run_inventory": {
                "claim": "输入目录中的材料清单（不含检索结论）",
                "kind": "inventory",
                "locator": data.get("case_path") or data.get("task_path") or args.input,
                "quote": json.dumps([i["path"] for i in inventory], ensure_ascii=False),
                "basis": "确定性脚本清点 /input 下文件，不执行检索",
            }
        },
        "sources": inventory,
        "uncertainty": [
            {"item": "检索与增量判定", "reason": "run 命令不执行检索，需 Agent 补写 findings.json 后 finalize"}
        ],
        "pending_human": [] if inventory else ["无输入材料可追踪"],
        "errors": errors,
        "degradations": [],
        "change_log": [{"event": "run_inventory", "time": core.utc_now()}],
    }
    findings_path = str(Path(args.work) / "findings.json")
    core.dump_json(findings_path, findings)
    args.findings = findings_path
    return cmd_finalize(args)


def cmd_dedup(args):
    recs, errs = core.load_json(args.records)
    if recs is None or not isinstance(recs, list):
        print("error: --records must be a JSON list; %s" % errs)
        return 2
    dedup = core.dedup_records(recs)
    snapshot_ids = None
    if args.snapshot:
        snap, _ = core.load_json(args.snapshot)
        if isinstance(snap, dict):
            snapshot_ids = snap.get("ids") or snap.get("canonical_ids") or []
        elif isinstance(snap, list):
            snapshot_ids = snap
    if snapshot_ids is not None:
        dedup["groups"] = core.classify_increment(dedup, snapshot_ids)
        dedup["incremental"] = True
        dedup["snapshot_id_count"] = len(snapshot_ids)
    core.dump_json(args.out, dedup)
    print("dedup: records=%d groups=%d duplicates=%d -> %s" % (
        dedup["record_count"], dedup["canonical_id_count"],
        dedup["duplicate_count"], args.out))
    return 0


def cmd_normalize(args):
    try:
        rec = json.loads(args.record)
    except Exception as exc:
        print("error: --record must be valid JSON: %s" % exc)
        return 2
    out = {
        "doi": core.normalize_doi(rec.get("doi")),
        "arxiv": core.normalize_arxiv(rec.get("arxiv")),
        "title": core.normalize_title(rec.get("title")),
        "canonical_id": core.canonical_id(rec),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    case_id, contract, data, errors = core.extract_case(args.input)
    findings, ferrs = core.load_json(args.findings)
    if findings is None:
        findings = {}
    errors.extend(ferrs)
    core.ensure_dir(args.output)

    prev_result, _ = core.load_json(str(Path(args.output) / "result.json"))
    result = core.merge_result(
        case_id, contract, findings, errors=errors,
        prev_change_log=(prev_result or {}).get("change_log"),
    )
    issues = core.validate_evidence_map(result.get("evidence_map"))
    if issues:
        result["errors"] = (result.get("errors") or []) + ["evidence_map: " + i for i in issues]
    result["change_log"] = core.ensure_change_log(
        result.get("change_log"), "finalize", contract_keys=contract
    )
    core.write_result(args.output, result)
    core.write_text(str(Path(args.output) / "report.md"), core.render_report(result))
    print("finalize: status=%s sources=%d evidence_map=%d errors=%d" % (
        result["status"], len(result.get("sources") or []),
        len(result.get("evidence_map") or {}), len(result.get("errors") or [])))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="literature-tracking Skill CLI (stdlib-only, deterministic)")
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("scaffold", "run", "finalize"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default=core.DEFAULT_INPUT)
        sp.add_argument("--output", default=core.DEFAULT_OUTPUT)
        sp.add_argument("--work", default=core.DEFAULT_WORK)
        if name == "finalize":
            sp.add_argument("--findings",
                            default=str(Path(core.DEFAULT_WORK) / "findings.json"))

    dp = sub.add_parser("dedup")
    dp.add_argument("--records", required=True)
    dp.add_argument("--out", default=str(Path(core.DEFAULT_WORK) / "dedup.json"))
    dp.add_argument("--snapshot", default=None)

    np = sub.add_parser("normalize")
    np.add_argument("--record", required=True)

    sub.add_parser("selftest")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.command
    if cmd == "scaffold":
        return cmd_scaffold(args)
    if cmd == "run":
        return cmd_run(args)
    if cmd == "finalize":
        return cmd_finalize(args)
    if cmd == "dedup":
        return cmd_dedup(args)
    if cmd == "normalize":
        return cmd_normalize(args)
    if cmd == "selftest":
        import selftest
        return selftest.main()
    return 2


if __name__ == "__main__":
    sys.exit(main())
