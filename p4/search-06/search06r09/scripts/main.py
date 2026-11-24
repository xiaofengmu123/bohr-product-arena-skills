# -*- coding: utf-8 -*-
"""Entry point for the literature-tracking Skill.

Subcommands (paths are relative to the skill root, e.g. /skills/weekly-skill):

    python3 scripts/main.py scaffold  --input /input --output /app/output --work /app/work
    python3 scripts/main.py run       --input /input --output /app/output --work /app/work
    python3 scripts/main.py finalize  --input /input --output /app/output --work /app/work \
                                      --findings /app/work/findings.json
    python3 scripts/main.py dedup     --records /app/work/records.json --out /app/work/dedup.json
    python3 scripts/main.py normalize --record '{"doi":"10.1000/XYZ","title":"A Paper"}'

`scaffold` writes a valid partial result.json (case_id + every output_contract
field + honest status) and must run in the first minutes. `run` does scaffold
plus a deterministic material inventory plus finalize with empty findings.
`finalize` merges the agent's findings, enriches them with dedup and version
classification, then writes result.json and report.md.

The scripts never fabricate literature: missing inputs yield
`insufficient_evidence` / `input_error`, never invented records.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as pl


DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULT_INPUT)
    common.add_argument("--output", default=DEFAULT_OUTPUT)
    common.add_argument("--work", default=DEFAULT_WORK)

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="literature-tracking Skill: scaffold / run / finalize / dedup / normalize")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scaffold", parents=[common],
                   help="write a valid partial result.json skeleton")
    sub.add_parser("run", parents=[common],
                   help="scaffold + material inventory + finalize empty findings")

    p_fin = sub.add_parser("finalize", parents=[common],
                           help="merge findings and write final outputs")
    p_fin.add_argument("--findings", default=None)

    p_ded = sub.add_parser("dedup", help="deduplicate a records.json list")
    p_ded.add_argument("--records", required=True)
    p_ded.add_argument("--out", required=True)

    p_nor = sub.add_parser("normalize", help="print normalized keys of one record")
    p_nor.add_argument("--record", required=True)
    return parser


def _record_change_log(result, findings_log):
    """Preserve the earliest skeleton_written entry, then append agent events."""
    merged = []
    prev_log = (result.get("change_log") or [])
    if prev_log and isinstance(prev_log[0], dict) and \
            prev_log[0].get("event") == "skeleton_written":
        merged = list(prev_log)
    else:
        merged = [{"event": "skeleton_written", "time": pl.utc_now(),
                   "status": "partial"}]
    merged.extend(findings_log or [])
    return merged


def cmd_scaffold(args):
    case_id, contract_keys, data, errors = pl.extract_case(args.input)
    status = pl.initial_status(data, errors)
    result = pl.make_skeleton(case_id, contract_keys, status)
    result["change_log"] = [{"event": "skeleton_written", "time": pl.utc_now(),
                             "status": status}]
    result["errors"] = errors
    result["degradations"] = [{"file": m, "reason": "not yet read"} for m in data["materials"]]
    err = pl.write_json(Path(args.output) / "result.json", result)
    print("scaffold case_id=%s status=%s contract=%s materials=%d%s" % (
        case_id, status, contract_keys, len(data["materials"]),
        (" err=" + err) if err else ""))
    return 0


def _inventory(data, work_dir):
    items = []
    for m in data["materials"]:
        p = Path(m)
        try:
            size = p.stat().st_size
        except OSError:
            size = None
        items.append({"path": m, "bytes": size, "type": p.suffix.lower()})
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    pl.write_json(Path(work_dir) / "inventory.json", {"materials": items})
    return items


def cmd_run(args):
    cmd_scaffold(args)
    _, _, data, _ = pl.extract_case(args.input)
    _inventory(data, args.work)
    Path(args.work).mkdir(parents=True, exist_ok=True)
    findings_path = Path(args.work) / "findings.json"
    pl.write_json(findings_path,
                  {"status": "partial", "records": [], "evidence": [],
                   "uncertainty": [], "pending_human": ["analysis not yet performed"]})
    args.findings = str(findings_path)
    return cmd_finalize(args)


def cmd_dedup(args):
    records, errs = pl.load_json(args.records)
    if errs:
        print("dedup failed: %s" % "; ".join(errs))
        return 2
    if not isinstance(records, list):
        records = []
    merged = pl.dedup(records)
    err = pl.write_json(args.out, {"groups": len(merged), "records": merged})
    print("dedup in=%d out=%d%s" % (len(records), len(merged),
                                    (" err=" + err) if err else ""))
    return 0


def cmd_normalize(args):
    try:
        rec = json.loads(args.record)
    except Exception as exc:
        print("bad --record JSON: %s" % exc)
        return 2
    print(json.dumps({
        "doi": pl.normalize_doi(rec.get("doi")),
        "arxiv": pl.normalize_arxiv(rec.get("arxiv")),
        "title": pl.normalize_title(rec.get("title")),
        "canonical_id": pl.canonical_id(rec),
        "version_label": pl.version_label(rec),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    started = time.time()
    case_id, contract_keys, data, errors = pl.extract_case(args.input)

    findings = {}
    if args.findings:
        findings, ferrs = pl.load_json(args.findings)
        errors.extend(ferrs)

    records = findings.get("records") or []
    if not isinstance(records, list):
        records = []
    merged = pl.dedup(records)
    snapshot = findings.get("snapshot")
    tracking = findings.get("tracking") if isinstance(findings.get("tracking"), dict) else {}
    if snapshot is None:
        snapshot = tracking.get("snapshot")
    classified = pl.classify(merged, snapshot)

    status = findings.get("status") or pl.initial_status(data, errors)
    if status not in pl.STATUSES:
        status = "partial"

    result = pl.make_skeleton(case_id, contract_keys, status)
    result["search_results"] = merged
    result["ranking"] = findings.get("ranking") or []
    result["evidence"] = findings.get("evidence") or []
    result["status_notes"] = findings.get("status_notes") or {
        "empty": None, "conflicts": [], "permission_errors": []}
    result["tracking"] = {
        "subscription": findings.get("subscription"),
        "window": findings.get("window"),
        "snapshot": snapshot,
        "new_items": classified["new_items"],
        "updates": classified["updates"],
        "retractions": classified["retractions"],
        "corrections": classified["corrections"],
        "reminders": findings.get("reminders") or [],
    }
    result["dedup"] = {
        "priority": ["doi", "arxiv", "title"],
        "records_in": len(records),
        "groups_out": len(merged),
    }
    result["sources"] = findings.get("sources") or []
    result["uncertainty"] = findings.get("uncertainty") or []
    result["errors"] = errors
    result["degradations"] = findings.get("degradations") or []
    result["pending_human"] = findings.get("pending_human") or []
    result["change_log"] = _record_change_log(result, findings.get("change_log"))
    result["runtime"] = {"seconds": round(time.time() - started, 3), "utc": pl.utc_now()}

    err = pl.write_json(Path(args.output) / "result.json", result)
    if err:
        result["errors"].append(err)

    Path(args.work).mkdir(parents=True, exist_ok=True)
    pl.write_json(Path(args.work) / "dedup.json",
                  {"groups": len(merged), "records": merged})
    _write_report(result, Path(args.output) / "report.md")

    print("finalize case_id=%s status=%s new=%d updated=%d retracted=%d corrected=%d%s" % (
        case_id, status, len(classified["new_items"]), len(classified["updates"]),
        len(classified["retractions"]), len(classified["corrections"]),
        (" err=" + err) if err else ""))
    return 0


def _write_report(result, path):
    t = result.get("tracking") or {}
    sub = t.get("subscription") or {}
    win = t.get("window") or {}
    lines = ["# 文献发现与追踪结果", "",
             "- case_id: `%s`" % result.get("case_id"),
             "- status: `%s`" % result.get("status")]
    if isinstance(sub, dict) and sub.get("query"):
        lines.append("- 订阅: %s" % sub["query"])
    if isinstance(win, dict):
        lines.append("- 时间窗: %s → %s" % (win.get("start"), win.get("end")))
    lines += ["",
              "- 新增 %d 条；更新 %d 条；撤稿 %d 条；更正 %d 条" % (
                  len(t.get("new_items") or []), len(t.get("updates") or []),
                  len(t.get("retractions") or []), len(t.get("corrections") or [])),
              "- 去重：入 %d 条，出 %d 组" % (
                  (result.get("dedup") or {}).get("records_in"),
                  (result.get("dedup") or {}).get("groups_out"))]
    for section, key in (("新增", "new_items"), ("更新", "updates"),
                         ("撤稿/更正", "retractions")):
        items = (t.get(key) or [])
        if not items:
            continue
        lines += ["", "## %s" % section]
        for it in items:
            title = it.get("title") or "(无标题)"
            doi = it.get("doi") or it.get("arxiv") or ""
            lines.append("- %s%s" % (title, (" — " + doi) if doi else ""))
    ev = result.get("evidence") or []
    if ev:
        lines += ["", "## 证据（%d 条）" % len(ev)]
        for e in ev:
            loc = e.get("locator") or ""
            lines.append("- %s: %s" % (loc, (e.get("quote") or "")[:120]))
    unc = result.get("uncertainty") or []
    if unc:
        lines += ["", "## 不确定性", "- " + "；".join(str(u) for u in unc)]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        Path(path).write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {"scaffold": cmd_scaffold, "run": cmd_run, "finalize": cmd_finalize,
                "dedup": cmd_dedup, "normalize": cmd_normalize}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
