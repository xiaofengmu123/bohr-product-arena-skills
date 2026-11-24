#!/usr/bin/env python3
"""Systematic literature review runtime helper (standard library only).

Subcommands
-----------
  discover   print the input inventory as JSON
  skeleton   write an early valid result.json + run_state.json
  merge      deep-merge a payload JSON into result.json (incremental update)
  finalize   assemble result.json, review.md, screening_log.json, evidence_map.json
  selfcheck  validate result.json and report coverage + screening + PRISMA checks

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
import resultlib as R


def add_common(parser):
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))


def out_path(args, name):
    return os.path.join(args.output, name)


def work_path(args, name):
    return os.path.join(args.work, name)


def load_run_state(args):
    obj, _, _ = R.load_json(work_path(args, "run_state.json"))
    return obj if isinstance(obj, dict) else {}


def load_result(args):
    result, errs, degs = R.load_json(out_path(args, "result.json"))
    state = load_run_state(args)
    contract = state.get("contract") or R.build_contract(None)
    if not isinstance(result, dict):
        result = R.new_skeleton(state.get("case_id", ""), contract,
                                note="repaired unreadable result.json")
        result["status"] = R.STATUS_INSUFFICIENT
    return result, state, contract, errs, degs


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
    summary = {
        "case_id": case_id, "contract_fields": sorted(contract.keys()),
        "wrote": False, "already_exists": False, "errors": [], "degradations": [],
    }

    if os.path.exists(result_path):
        summary["already_exists"] = True
        existing, e2, _ = R.load_json(result_path)
        if not isinstance(existing, dict):
            R.write_json(result_path, R.new_skeleton(
                case_id, contract, note="repaired unreadable result.json"))
            summary["wrote"] = True
    else:
        result = R.new_skeleton(case_id, contract,
                                note="skeleton before systematic review begins")
        result["errors"] = list(errs)
        result["degradations"] = list(degs)
        if not inv["exists"] or inv["total_files"] == 0:
            result["status"] = R.STATUS_INSUFFICIENT
            result.setdefault("limitations", []).append(
                "no case.json/task.md/materials found under %s" % args.input)
        elif not inv.get("case_json"):
            result["status"] = R.STATUS_PARTIAL
            result.setdefault("limitations", []).append(
                "case.json not found; review_question taken from task.md if readable")
        elif bool(inv.get("case_json")) and not isinstance(case_obj, dict):
            result["status"] = R.STATUS_INPUT_ERROR
            result.setdefault("limitations", []).append(
                "case.json present but unparseable; contract fields fall back to defaults")
        write_errs = R.write_json(result_path, result)
        summary["wrote"] = not bool(write_errs)
        summary["errors"].extend(write_errs)

    state = {
        "case_id": case_id, "contract": contract, "inventory": inv,
        "started_at": R.epoch_now(), "input_dir": args.input,
        "output_dir": args.output, "work_dir": args.work,
    }
    summary["errors"].extend(errs)
    summary["degradations"].extend(degs)
    summary["errors"].extend(R.write_json(work_path(args, "run_state.json"), state))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_merge(args):
    payload, errs, degs = R.load_json(args.payload)
    if not isinstance(payload, dict):
        print(json.dumps({
            "ok": False, "errors": errs or ["payload not a JSON object"],
            "degradations": degs,
        }, ensure_ascii=False, indent=2))
        return
    result, _, contract, e2, d2 = load_result(args)
    result = R.deep_merge(result, payload)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "merge",
        "detail": "merged payload %s" % os.path.basename(args.payload),
    })
    errs.extend(R.write_json(out_path(args, "result.json"), result))
    print(json.dumps({"ok": not errs, "errors": errs, "degradations": degs + d2},
                     ensure_ascii=False, indent=2))


def read_work_files(args):
    """Read structured work files written by the agent. Returns (data, errors)."""
    data, errors = {}, []
    for name in ("protocol.md", "notes.md", "search_strategy.md", "synthesis.md"):
        path = work_path(args, name)
        if not os.path.exists(path):
            continue
        try:
            raw = open(path, "rb").read()
        except OSError as exc:
            errors.append("cannot read %s: %s" % (path, exc))
            continue
        text, _, degs = R.decode_text(raw, path)
        if degs:
            errors.append("decode degradation in %s" % name)
        data[name] = text

    for name in ("protocol.json", "synthesis.json"):
        obj, errs, _ = R.load_json(work_path(args, name))
        if isinstance(obj, dict):
            data[name.split(".")[0]] = obj
        errors.extend(errs)

    for name, key in (("screening.jsonl", "screening"),
                      ("quality_appraisal.jsonl", "quality_appraisal"),
                      ("evidence_cards.jsonl", "evidence_cards")):
        lines, errs, _ = R.load_jsonl(work_path(args, name))
        if isinstance(lines, list):
            data[key] = lines
        errors.extend(errs)
    return data, errors


def build_review_md(result):
    """Render a human-readable review from assembled result fields."""
    lines = []
    lines.append("# 系统性文献综述")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id", "unknown"))
    lines.append("- status: `%s`" % result.get("status", "partial"))
    lines.append("")
    lines.append("## 研究问题")
    lines.append("")
    lines.append(str(result.get("review_question") or "（未填写）"))
    sub = result.get("subquestions") or []
    if sub:
        lines.append("")
        for i, item in enumerate(sub, 1):
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            lines.append("%d. %s" % (i, text))
    lines.append("")
    lines.append("## 纳排标准")
    lines.append("")
    lines.append("- 纳入标准：" + json.dumps(result.get("inclusion_criteria") or [],
                                          ensure_ascii=False))
    lines.append("- 排除标准：" + json.dumps(result.get("exclusion_criteria") or [],
                                          ensure_ascii=False))
    flow = result.get("prisma_flow") or {}
    if flow:
        lines.append("")
        lines.append("## 筛选流程（PRISMA）")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(flow, ensure_ascii=False, indent=2))
        lines.append("```")
    included = [r for r in (result.get("screening_records") or [])
                if isinstance(r, dict) and (r.get("decision") or "").lower() == "include"]
    if included:
        lines.append("")
        lines.append("## 纳入研究")
        lines.append("")
        for record in included:
            lines.append("- %s" % (record.get("source") or record.get("record_id") or "（未命名）"))
    synth = result.get("synthesis") or {}
    lines.append("")
    lines.append("## 综合结论")
    lines.append("")
    for i, concl in enumerate(synth.get("conclusions") or [], 1):
        text = concl.get("text") if isinstance(concl, dict) else str(concl)
        lines.append("%d. %s" % (i, text))
    if synth.get("conflicts"):
        lines.append("")
        lines.append("## 冲突证据")
        lines.append("")
        for item in synth["conflicts"]:
            text = item.get("text") if isinstance(item, dict) else str(item)
            lines.append("- %s" % text)
    if synth.get("gaps"):
        lines.append("")
        lines.append("## 研究空白")
        lines.append("")
        for item in synth["gaps"]:
            text = item.get("text") if isinstance(item, dict) else str(item)
            lines.append("- %s" % text)
    lines.append("")
    return "\n".join(lines)


def cmd_finalize(args):
    result, _, contract, e0, d0 = load_result(args)
    errors, degradations = list(e0), list(d0)
    work, werr = read_work_files(args)
    errors.extend(werr)

    proto = work.get("protocol") or {}
    screening = work.get("screening") or []
    quality = work.get("quality_appraisal") or []
    evidence = work.get("evidence_cards") or []
    synth = work.get("synthesis") or {}

    if proto:
        result["review_question"] = proto.get("review_question", result.get("review_question", ""))
        result["subquestions"] = proto.get("subquestions", result.get("subquestions", []))
        result["search_strategy"] = proto.get("search_strategy", result.get("search_strategy", {}))
        result["inclusion_criteria"] = proto.get(
            "inclusion_criteria", result.get("inclusion_criteria", []))
        result["exclusion_criteria"] = proto.get(
            "exclusion_criteria", result.get("exclusion_criteria", []))
    if screening:
        result["screening_records"] = screening
    result["prisma_flow"] = R.compute_prisma(result.get("screening_records") or [])
    if quality:
        result["quality_appraisal"] = quality
    if evidence:
        result["evidence_map"] = evidence
    if synth:
        result["synthesis"] = {
            "conclusions": synth.get("conclusions") or [],
            "conflicts": synth.get("conflicts") or [],
            "gaps": synth.get("gaps") or [],
        }
        if isinstance(synth.get("uncertainty"), dict):
            result["uncertainty"] = synth["uncertainty"]

    result["status"] = R.decide_status(result)
    result.setdefault("change_log", []).append({
        "ts": R.iso_now(), "action": "finalize",
        "detail": "assembled domain fields from /app/work; errors=%d" % len(errors),
    })
    result["errors"] = list(result.get("errors") or []) + errors

    errors.extend(R.write_json(out_path(args, "result.json"), result))
    errors.extend(R.write_json(out_path(args, "screening_log.json"),
                               result.get("screening_records") or []))
    errors.extend(R.write_json(out_path(args, "evidence_map.json"),
                               result.get("evidence_map") or []))
    review_md = build_review_md(result)
    try:
        R.ensure_dir(args.output)
        with open(out_path(args, "review.md"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(review_md + "\n")
    except OSError as exc:
        errors.append("cannot write review.md: %s" % exc)

    print(json.dumps({
        "ok": not errors, "status": result["status"],
        "errors": errors, "degradations": degradations,
        "included": (result.get("prisma_flow") or {}).get("included", 0),
        "evidence_cards": len(result.get("evidence_map") or []),
    }, ensure_ascii=False, indent=2))


def cmd_selfcheck(args):
    result, _, contract, load_errs, load_degs = load_result(args)
    checks = []

    def add(item_id, ok, label, detail=""):
        checks.append({"id": item_id, "ok": bool(ok), "label": label, "detail": detail})

    add("parse", not load_errs, "result.json 可被 json.load 解析", "; ".join(load_errs))
    add("case_id", bool((result.get("case_id") or "") not in ("", "unknown")),
        "case_id 存在且非 unknown", str(result.get("case_id")))
    missing_contract = [k for k in contract if k not in result]
    add("contract_fields", not missing_contract, "output_contract 全部字段回填",
        "missing: %s" % ", ".join(missing_contract) if missing_contract else "")
    add("status", result.get("status") in R.STATUSES, "status 取值合法", str(result.get("status")))

    add("review_question", bool(result.get("review_question")),
        "review_question 非空", "问题拆解")
    add("subquestions", bool(result.get("subquestions")),
        "subquestions 至少 1 项", "%d 项" % len(result.get("subquestions") or []))
    add("inclusion", bool(result.get("inclusion_criteria")),
        "inclusion_criteria 非空", "条款化纳排")
    add("exclusion", bool(result.get("exclusion_criteria")),
        "exclusion_criteria 非空", "条款化纳排")

    qb = (result.get("search_strategy") or {}).get("query_blocks")
    if isinstance(qb, list) and qb:
        qb_ok = True
        qb_detail = "%d blocks" % len(qb)
        for block in qb:
            if not isinstance(block, dict):
                qb_ok = False
                qb_detail += "; non-dict block"
                continue
            if not (block.get("database") and block.get("query") and block.get("intent")):
                qb_ok = False
                qb_detail += "; block missing database/query/intent: %s" % block.get(
                    "database", "?")
    else:
        qb_ok = False
        qb_detail = "no query_blocks"
    add("search_strategy", qb_ok, "每个 query_block 含 database+query+intent", qb_detail)

    screening = result.get("screening_records") or []
    scr_ok = bool(screening)
    scr_detail = "%d 条筛选记录" % len(screening)
    for record in screening:
        if not isinstance(record, dict):
            scr_ok = False
            scr_detail += "; 非对象记录"
            continue
        decision = (record.get("decision") or "").lower()
        if decision == "exclude" and not (record.get("reason") and record.get("criterion_id")):
            scr_ok = False
            scr_detail += "; exclude 缺 reason/criterion_id: %s" % record.get(
                "record_id", "?")
        if not record.get("record_id") and not record.get("source"):
            scr_ok = False
            scr_detail += "; 记录缺 record_id/source"
    add("screening", scr_ok, "两轮筛选逐条留痕，exclude 带 reason+criterion_id", scr_detail)

    computed = R.compute_prisma(screening)
    stored = result.get("prisma_flow") or {}
    prisma_ok = computed == stored
    add("prisma", prisma_ok, "PRISMA 计数可由筛选记录重算",
        "computed=%s stored=%s" % (json.dumps(computed, ensure_ascii=False),
                                   json.dumps(stored, ensure_ascii=False)) if not prisma_ok else "")

    quality = result.get("quality_appraisal") or []
    qa_ok = True
    qa_detail = "%d 条质量评价" % len(quality)
    for item in quality:
        if not isinstance(item, dict):
            qa_ok = False
            qa_detail += "; 非对象记录"
            continue
        dims = item.get("dimensions") or []
        if not item.get("overall_grade"):
            qa_ok = False
            qa_detail += "; 缺 overall_grade: %s" % item.get("record_id", "?")
        for dim in dims:
            if isinstance(dim, dict) and not (dim.get("basis_locator") and dim.get("quote")):
                qa_ok = False
                qa_detail += "; 维度缺 basis_locator/quote"
    add("quality", qa_ok or not quality, "质量评价逐维度 basis_locator+quote+等级", qa_detail)

    evidence = result.get("evidence_map") or []
    evidence_ids = {e.get("evidence_id") for e in evidence if isinstance(e, dict)}
    ev_ok = True
    ev_detail = "%d 条证据" % len(evidence)
    for item in evidence:
        if not isinstance(item, dict):
            ev_ok = False
            ev_detail += "; 非对象记录"
            continue
        if not (item.get("locator") and item.get("quote")):
            ev_ok = False
            ev_detail += "; 证据缺 locator/quote: %s" % item.get("evidence_id", "?")
    synth = result.get("synthesis") or {}
    for concl in synth.get("conclusions") or []:
        if not isinstance(concl, dict):
            continue
        ids = concl.get("evidence_ids") or []
        for eid in ids:
            if eid not in evidence_ids:
                ev_ok = False
                ev_detail += "; 结论引用未命中证据: %s" % eid
    add("evidence_backlink", ev_ok, "结论 evidence_ids 命中 evidence_map，逐字 quote", ev_detail)

    conflicts = synth.get("conflicts") or []
    cf_ok = True
    cf_detail = "%d 条冲突" % len(conflicts)
    for item in conflicts:
        if not isinstance(item, dict):
            cf_ok = False
            cf_detail += "; 非对象记录"
            continue
        sources = item.get("sources") or []
        if len(sources) < 2 and not (item.get("source_a") and item.get("source_b")):
            cf_ok = False
            cf_detail += "; 冲突少于两个来源"
    add("conflicts", cf_ok, "conflicts 逐条并列两个来源", cf_detail)

    gaps = synth.get("gaps") or []
    gp_ok = all(isinstance(g, dict) and (g.get("scope") or g.get("text"))
                for g in gaps)
    add("gaps", gp_ok or not gaps, "gaps 落位到具体缺口", "%d 条空白" % len(gaps))

    honest = ("sources", "uncertainty", "change_log", "limitations",
              "errors", "degradations", "pending_human", "runtime_seconds")
    missing_honest = [k for k in honest if k not in result]
    add("honest_fields", not missing_honest, "诚实字段齐全",
        "missing: %s" % ", ".join(missing_honest) if missing_honest else "")

    passed = sum(1 for c in checks if c["ok"])
    failed = [c for c in checks if not c["ok"]]
    report = {"passed": passed, "total": len(checks),
              "failed": [c["id"] for c in failed], "items": checks,
              "load_errors": load_errs, "degradations": load_degs}
    print(json.dumps(report, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("discover", cmd_discover), ("skeleton", cmd_skeleton),
                       ("merge", cmd_merge), ("finalize", cmd_finalize),
                       ("selfcheck", cmd_selfcheck)):
        sp = sub.add_parser(name, help=func.__doc__)
        add_common(sp)
        if name == "merge":
            sp.add_argument("--payload", required=True, help="payload JSON path")
        sp.set_defaults(func=func)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
