#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 4 — deep-merge fragments, validate the contract + evidence_map,
and write the final result.json + report.md.

Fragments live in /app/work/fragments/ and may contain, per file:
    <deliverable-key>           — content object (replaces skeleton field)
    "evidence_map": {"claims": [...]}   — appended to the global claims list
    "conflicts": [...]          — appended
    "uncertainty": {...}        — lists merged

Validation checks (honest, non-destructive):
  1. JSON contains no NaN/Infinity (sanitized to null with a recorded error).
  2. Every output_contract key is present at the top level.
  3. Every claim_id referenced by a deliverable's `claims` list exists in
     evidence_map.claims.
  4. Every claim has an evidence list; each evidence entry has source_id and
     locator, and a non-empty verbatim quote unless quote_status is
     missing/not_reported.

Exit codes: 0 = merged & valid; 2 = merged but validation failures found
(the agent should fix and rerun).  result.json is always written.
"""

import argparse
import json
import math
import os
import sys
import time

DELIVERABLES = ("research_question", "search_protocol", "prior_work",
                "novelty", "feasibility")
NO_QUOTE_STATUSES = ("missing", "not_reported")


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_json_tolerant(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None, "unreadable", "no file"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return json.loads(raw.decode(enc)), "ok", None
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
    return None, "bad", "unparseable"


def sanitize(obj, problems, path="$"):
    """Replace non-finite floats with None (recording each occurrence)."""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            problems.append("non-finite number at %s" % path)
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize(v, problems, "%s.%s" % (path, k))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, problems, "%s[%d]" % (path, i))
                for i, v in enumerate(obj)]
    return obj


def deep_merge(base, update):
    """Recursively merge update into base (dicts merge; lists extend)."""
    for key, val in update.items():
        if key not in base or base[key] is None:
            base[key] = val
        elif isinstance(base[key], dict) and isinstance(val, dict):
            deep_merge(base[key], val)
        elif isinstance(base[key], list) and isinstance(val, list):
            base[key].extend(val)
        else:
            base[key] = val
    return base


def collect_referenced_claims(result):
    """Return a set of claim_ids referenced from deliverables."""
    refs = set()
    for key in DELIVERABLES + ("synthesis",):
        obj = result.get(key)
        if isinstance(obj, dict):
            for cid in obj.get("claims") or []:
                if isinstance(cid, str):
                    refs.add(cid)
    # novelty nested claims often carry their own claim_id.
    novelty = result.get("novelty")
    if isinstance(novelty, dict):
        for item in novelty.get("claims") or []:
            if isinstance(item, dict) and isinstance(item.get("claim_id"), str):
                refs.add(item["claim_id"])
    return refs


ALLOWED_QUOTE_STATUS = ("verified", "inferred", "missing", "conflicting",
                        "not_reported")
ALLOWED_RELATION = ("supports", "contradicts", "contextualizes", "unavailable")


def validate(result, errors, pending):
    """Return (had_failure: bool). Records issues into errors/pending lists."""
    failed = False
    claims = result.get("evidence_map", {}).get("claims") or []
    claim_ids = {c.get("claim_id") for c in claims if isinstance(c, dict)}
    valid_source_ids = {s.get("source_id") for s in result.get("sources") or []
                        if isinstance(s, dict) and s.get("source_id")}

    # 2. contract keys present at top level
    contract = result.get("output_contract") or {}
    for key in contract:
        if key not in result:
            result[key] = None
            errors.append("contract key missing, filled null: %s" % key)

    # 3. referenced claims must exist
    for cid in sorted(collect_referenced_claims(result)):
        if cid not in claim_ids:
            failed = True
            errors.append("referenced claim missing from evidence_map: %s" % cid)

    # 4. evidence quality
    for claim in claims:
        cid = claim.get("claim_id", "(no id)")
        ev = claim.get("evidence")
        if not isinstance(ev, list) or len(ev) == 0:
            failed = True
            pending.append("claim %s has no evidence entry" % cid)
            continue
        for e in ev:
            if not isinstance(e, dict):
                failed = True
                errors.append("claim %s has non-object evidence" % cid)
                continue
            if not e.get("source_id"):
                failed = True
                errors.append("claim %s evidence lacks source_id" % cid)
            elif valid_source_ids and e["source_id"] not in valid_source_ids:
                failed = True
                errors.append("claim %s evidence source_id not in sources: %s"
                              % (cid, e["source_id"]))
            if not e.get("locator"):
                failed = True
                errors.append("claim %s evidence lacks locator" % cid)
            qstatus = e.get("quote_status")
            quote = e.get("quote")
            if qstatus is not None and qstatus not in ALLOWED_QUOTE_STATUS:
                failed = True
                errors.append("claim %s evidence has bad quote_status: %r"
                              % (cid, qstatus))
            rel = e.get("relation")
            if rel is not None and rel not in ALLOWED_RELATION:
                failed = True
                errors.append("claim %s evidence has bad relation: %r"
                              % (cid, rel))
            if qstatus not in NO_QUOTE_STATUSES and not quote:
                failed = True
                errors.append("claim %s evidence lacks verbatim quote "
                              "(quote_status=%r)" % (cid, qstatus))
    return failed


def compute_status(result, validation_failed):
    cur = result.get("status")
    if cur in ("input_error", "insufficient_evidence"):
        return cur
    if validation_failed:
        return "partial"
    states = [result.get(k, {}).get("status") for k in
              ("research_question", "prior_work", "novelty", "feasibility")
              if isinstance(result.get(k), dict)]
    synthesis = result.get("synthesis") or {}
    if states and all(s == "complete" for s in states) and \
            len(synthesis.get("conclusions") or []) > 0:
        return "ok"
    return "partial"


def render_report(result):
    lines = []
    lines.append("# 科研选题与创新性评估报告（证据溯源版）")
    lines.append("")
    lines.append("- case_id: `%s`" % (result.get("case_id") or "(unresolved)"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- generated_at: %s" % utcnow())
    lines.append("")

    rq = result.get("research_question") or {}
    if isinstance(rq, dict):
        lines.append("## 1. 研究问题（可检验表述）")
        if rq.get("testable_statement"):
            lines.append("")
            lines.append(str(rq["testable_statement"]))
        if rq.get("falsification_condition"):
            lines.append("")
            lines.append("- 可证伪条件：%s" % rq["falsification_condition"])
        if rq.get("gaps"):
            lines.append("")
            lines.append("- 缺口：%s" % "; ".join(map(str, rq["gaps"])))
        lines.append("")

    sp = result.get("search_protocol") or {}
    if isinstance(sp, dict) and sp.get("query"):
        lines.append("## 2. 检索协议")
        lines.append("")
        lines.append("- 检索式：`%s`" % sp.get("query"))
        inc = sp.get("inclusion_criteria") or []
        exc = sp.get("exclusion_criteria") or []
        if inc:
            lines.append("- 纳入：%s" % "; ".join(map(str, inc)))
        if exc:
            lines.append("- 排除：%s" % "; ".join(map(str, exc)))
        lines.append("")

    pw = result.get("prior_work") or {}
    matrix = pw.get("comparison_matrix") if isinstance(pw, dict) else None
    if matrix:
        lines.append("## 3. 已有工作比较")
        lines.append("")
        for row in matrix:
            if isinstance(row, dict):
                lines.append("- **%s**（%s）：任务=%s；方法=%s；评价=%s%s" % (
                    row.get("work_id", "?"),
                    row.get("source_id", "?"),
                    row.get("task", "未报告"),
                    row.get("method", "未报告"),
                    row.get("evaluation", "未报告"),
                    ("；不可比原因=%s" % row["incomparable_reason"])
                    if row.get("incomparable_reason") else "",
                ))
        lines.append("")

    nv = result.get("novelty") or {}
    nclaims = nv.get("claims") if isinstance(nv, dict) else None
    if nclaims:
        lines.append("## 4. 创新点判定")
        lines.append("")
        for item in nclaims:
            if isinstance(item, dict):
                lines.append("- %s（%s）：verdict=`%s`%s" % (
                    item.get("claim_id", "?"),
                    item.get("type", "未分类"),
                    item.get("verdict", "?"),
                    ("；covered_by=%s" % item["covered_by"])
                    if item.get("covered_by") else "",
                ))
        lines.append("")

    fb = result.get("feasibility") or {}
    pre = fb.get("preconditions") if isinstance(fb, dict) else None
    if pre:
        lines.append("## 5. 可行性前提")
        lines.append("")
        for p in pre:
            if isinstance(p, dict):
                lines.append("- %s：`%s`（风险=%s）" % (
                    p.get("dimension", "?"), p.get("status", "?"),
                    p.get("risk", "未评估")))
        if fb.get("conditional_conclusion"):
            lines.append("")
            lines.append("结论：%s" % fb["conditional_conclusion"])
        lines.append("")

    conflicts = result.get("conflicts") or []
    if conflicts:
        lines.append("## 6. 冲突证据")
        lines.append("")
        for c in conflicts:
            if isinstance(c, dict):
                lines.append("- %s：取舍=%s" % (
                    c.get("topic", "?"), c.get("resolution", "未说明")))
        lines.append("")

    syn = result.get("synthesis") or {}
    if isinstance(syn, dict):
        if syn.get("conclusions"):
            lines.append("## 7. 综合结论")
            lines.append("")
            for c in syn["conclusions"]:
                lines.append("- %s" % c)
            lines.append("")
        if syn.get("research_gaps"):
            lines.append("## 8. 研究空白")
            lines.append("")
            for g in syn["research_gaps"]:
                lines.append("- %s" % g)
            lines.append("")
        if syn.get("boundary_statement"):
            lines.append("## 9. 边界说明")
            lines.append("")
            lines.append(str(syn["boundary_statement"]))
            lines.append("")

    pending = result.get("pending_human") or []
    if pending:
        lines.append("## 10. 待人工确认")
        lines.append("")
        for p in pending:
            lines.append("- %s" % p)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**证据溯源**：每条结论的 claim_id 可在 `result.json` 的 "
                 "`evidence_map.claims` 中按 `locator`+`quote` 回到原文位置；"
                 "`quote_status=missing/not_reported` 的条目已同时登记于 "
                 "`uncertainty.gaps` 与 `pending_human`。")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Merge fragments, validate, write result.json + report.md.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    output_dir = os.path.abspath(args.output)
    work_dir = os.path.abspath(args.work)
    frag_dir = os.path.join(work_dir, "fragments")
    result_path = os.path.join(output_dir, "result.json")

    result, rstate, rerr = load_json_tolerant(result_path)
    if rstate != "ok" or not isinstance(result, dict):
        print("FINALIZE: result.json missing/unreadable; run skeleton.py first",
              file=sys.stderr)
        return 1

    # ---- Merge fragments ------------------------------------------------
    frags = []
    if os.path.isdir(frag_dir):
        for name in sorted(os.listdir(frag_dir)):
            if not name.endswith(".json"):
                continue
            frag, fstate, ferr = load_json_tolerant(os.path.join(frag_dir, name))
            if fstate == "ok" and isinstance(frag, dict):
                frags.append((name, frag))
            else:
                result.setdefault("errors", []).append(
                    "fragment unreadable %s: %s" % (name, ferr))

    merged_claims = list((result.get("evidence_map") or {}).get("claims") or [])
    for name, frag in frags:
        fm = frag.pop("evidence_map", None) or {}
        if isinstance(fm.get("claims"), list):
            merged_claims.extend(fm["claims"])
        deep_merge(result, frag)

    result.setdefault("evidence_map", {})["claims"] = merged_claims

    # ---- Sanitize + validate -------------------------------------------
    problems = []
    result = sanitize(result, problems)
    errors = list(result.get("errors") or [])
    pending = list(result.get("pending_human") or [])
    for p in problems:
        errors.append(p)
    validation_failed = validate(result, errors, pending)
    result["errors"] = errors
    result["pending_human"] = pending

    # ---- Status + log ----------------------------------------------------
    result["status"] = compute_status(result, validation_failed)
    log = list(result.get("change_log") or [])
    log.append({"time": utcnow(), "phase": "phase4",
                "action": "finalized",
                "detail": "merged %d fragments; validation_failed=%s"
                          % (len(frags), validation_failed)})
    result["change_log"] = log
    result["runtime"] = dict(result.get("runtime") or {})
    result["runtime"]["phase4_at"] = utcnow()

    # ---- Write result.json + report.md ----------------------------------
    os.makedirs(output_dir, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, allow_nan=False)
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_report(result) + "\n")

    if validation_failed:
        print("FINALIZE: merged with validation failures (see errors/"
              "pending_human); exit 2", file=sys.stderr)
        return 2
    print("FINALIZE: ok; status=%s; %d claims" % (result["status"],
                                                   len(merged_claims)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
