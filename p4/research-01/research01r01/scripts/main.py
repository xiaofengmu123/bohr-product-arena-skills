#!/usr/bin/env python3
"""main.py — deep-research skill runtime CLI (standard library only).

Subcommands:
  discover    print the input inventory as JSON
  skeleton    write an early valid result.json + /app/work/run_state.json
  merge       deep-merge a payload JSON into result.json (safe incremental update)
  finalize    assemble result.json, report.md, evidence_map.json; copy work notes
  selfcheck   validate result.json and print the six-point coverage checklist

Common options (defaults match the runtime contract):
  --input  INPUT_DIR   default /input
  --output OUTPUT_DIR  default /app/output
  --work   WORK_DIR    default /app/work
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as R


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def _run_state(args, case_id, contract, inv):
    return {
        "case_id": case_id,
        "contract": contract,
        "inventory": inv,
        "started_at": R.epoch_now(),
        "input_dir": args.input,
        "output_dir": args.output,
        "work_dir": args.work,
    }


def _load_run_state(args):
    obj, _, _ = R.load_json(work_path(args, "run_state.json"))
    return obj if isinstance(obj, dict) else None


def cmd_discover(args):
    print(json.dumps(R.discover_input(args.input), ensure_ascii=False, indent=2))


def cmd_skeleton(args):
    R.ensure_dir(args.output)
    R.ensure_dir(args.work)
    inv = R.discover_input(args.input)
    case_obj, errs, degs = (None, [], [])
    if inv.get("case_json"):
        case_obj, errs, degs = R.load_json(inv["case_json"])
    case_id = R.extract_case_id(case_obj) if isinstance(case_obj, dict) else ""
    contract = R.build_contract(case_obj if isinstance(case_obj, dict) else None)

    result_path = out_path(args, "result.json")
    summary = {"case_id": case_id, "contract_fields": sorted(contract.keys()),
               "wrote": False, "already_exists": False,
               "errors": [], "degradations": []}

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = R.load_json(result_path)
        if not isinstance(existing, dict):
            result = R.new_skeleton(case_id, contract, note="repaired unreadable result.json")
            summary["errors"].extend(R.write_json(result_path, result))
            summary["wrote"] = True
    else:
        note = "skeleton before research begins"
        if not inv["exists"] or inv["total_files"] == 0:
            note = "empty input directory; no materials found"
        result = R.new_skeleton(case_id, contract, note=note)
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0:
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif inv.get("case_json") and not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        summary["errors"].extend(R.write_json(result_path, result))
        summary["wrote"] = not summary["errors"]

    state = _run_state(args, case_id, contract, inv)
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_merge(args):
    result_path = out_path(args, "result.json")
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False,
                          "errors": errs or ["payload not a JSON object"],
                          "degradations": degs}, ensure_ascii=False, indent=2))
        return

    result, _e2, _d2 = R.load_json(result_path)
    if not isinstance(result, dict):
        state = _load_run_state(args) or {}
        contract = state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT)
        case_id = state.get("case_id") or R.extract_case_id(payload)
        result = R.new_skeleton(case_id, contract, note="created during merge")

    result = R.deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload),
    })
    errs.extend(R.write_json(result_path, result))
    print(json.dumps({"ok": not errs, "errors": errs,
                      "degradations": degs + _d2}, ensure_ascii=False, indent=2))


def cmd_finalize(args):
    result_path = out_path(args, "result.json")
    result, errs, degs = R.load_json(result_path)
    if not isinstance(result, dict):
        state = _load_run_state(args) or {}
        result = R.new_skeleton(
            state.get("case_id") or "", state.get("contract") or copy.deepcopy(R.DEFAULT_CONTRACT),
            note="created during finalize")
    state = _load_run_state(args) or {}
    started = float(state.get("started_at", R.epoch_now()))
    result.setdefault("runtime_seconds", round(max(0.0, R.epoch_now() - started), 1))
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "finalize", "detail": "assembled report + evidence_map",
    })

    emap = R.build_evidence_map(result, args.input)
    errs.extend(R.write_json(result_path, result))
    errs.extend(R.write_json(out_path(args, "evidence_map.json"), emap))
    errs.extend(R.write_text(out_path(args, "report.md"), _render_report(result, emap)))

    for name in ("decomposition.md", "search_plan.md", "synthesis.md"):
        text, e, _d = R.read_text(work_path(args, name))
        if text:
            errs.extend(R.write_text(out_path(args, name), text))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs,
                      "evidence_verified": emap["quote_integrity"]["verified"],
                      "evidence_total": emap["quote_integrity"]["total"]},
                     ensure_ascii=False, indent=2))


def _render_report(result, emap):
    rp = result.get("research_product") or {}
    concl = rp.get("conclusions") or []
    lines = [
        "# 深度研究报告",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- 研究问题: %s" % rp.get("question", ""),
        "",
        "## 综合结论",
        "",
        rp.get("summary", "") or "（无）",
        "",
        "## 结论清单（含证据定位）",
        "",
    ]
    idx = {c["conclusion_id"]: c for c in emap.get("conclusion_index", [])}
    for c in concl:
        cid = c.get("id")
        lines.append("- **%s** [%s] %s" % (cid, c.get("strength", ""), c.get("text", "")))
        for ev in idx.get(cid, {}).get("evidence", []):
            loc = ev.get("locator") or {}
            lines.append("  - %s @ %s | %s%s" % (
                ev.get("evidence_id"), ev.get("source", ""),
                loc.get("unit_id") or loc.get("unit_type") or "?",
                "" if ev.get("verified") else "（未回源）"))
            q = ev.get("quote") or ""
            lines.append("    > %s" % (q[:240] + ("…" if len(q) > 240 else "")))
    lines += [
        "",
        "## 不确定性与研究空白",
        "",
    ]
    uc = result.get("uncertainty") or {}
    for key, label in (("conflicts", "冲突"), ("biases", "偏倚"),
                       ("gaps", "空白"), ("undetermined", "无法确定")):
        items = uc.get(key) or []
        if items:
            lines.append("### %s" % label)
            for it in items:
                lines.append("- %s" % (json.dumps(it, ensure_ascii=False) if not isinstance(it, str) else it))
    if result.get("pending_human"):
        lines.append("### 需人工确认")
        for it in result["pending_human"]:
            lines.append("- %s" % it)
    if result.get("limitations"):
        lines.append("### 限制")
        for it in result["limitations"]:
            lines.append("- %s" % it)
    lines.append("")
    return "\n".join(lines)


def cmd_selfcheck(args):
    result, errs, _degs = R.load_json(out_path(args, "result.json"))
    if not isinstance(result, dict):
        result = {}
        errs = errs or ["result.json missing or not a JSON object"]
    checks = R.validate_result(result, args.input, args.work)
    lines = ["# 自检报告（对应评分维度）", ""]
    ok_all = True
    for ch in checks:
        mark = "[x]" if ch["pass"] else "[ ]"
        if not ch["pass"]:
            ok_all = False
        lines.append("- %s %s — %s%s" % (mark, ch["label"], ch["detail"],
                                         ("；" + ("通过" if ch["pass"] else "未通过"))))
    if errs:
        lines.append("")
        lines.append("- [ ] result.json 加载错误: %s" % "; ".join(errs))
        ok_all = False
    lines += ["", "结论: %s" % ("PASS" if ok_all else "FAIL"), ""]
    report = "\n".join(lines)
    print(report)
    R.write_text(out_path(args, "selfcheck.md"), report)
    print(json.dumps({"valid": ok_all, "errors": errs, "checks": checks},
                     ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="deep-research skill runtime helper")
    add_common(parser)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_sub(name, help_text):
        sp = sub.add_parser(name, help=help_text)
        add_common(sp)
        return sp

    add_sub("discover", "print input inventory")
    add_sub("skeleton", "write early partial result.json + run_state")
    m = add_sub("merge", "deep-merge a payload into result.json")
    m.add_argument("--payload", required=True)
    add_sub("finalize", "assemble result + report + evidence_map")
    add_sub("selfcheck", "validate result.json and print checklist")
    args = parser.parse_args(argv)
    fn = {"discover": cmd_discover, "skeleton": cmd_skeleton,
          "merge": cmd_merge, "finalize": cmd_finalize,
          "selfcheck": cmd_selfcheck}[args.cmd]
    fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
