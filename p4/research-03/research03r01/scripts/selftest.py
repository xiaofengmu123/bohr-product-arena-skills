#!/usr/bin/env python3
"""selftest.py — evidence-traceability engine and self-check runner.

This module implements the *evidence-first* design of this Skill: conclusions
are projections of evidence items, and every evidence item is checked against
the actual source text so a non-verbatim quote is flagged rather than trusted.
It is also runnable standalone as the package self-test:

    python -B scripts/selftest.py --input /input --output /app/output --work /app/work

Standard library only.
"""

import argparse
import json
import os

import resultlib as R

# --------------------------------------------------------------------------- #
# Schemas / vocabularies
# --------------------------------------------------------------------------- #
CLAIM_TYPES = ("fact", "estimate", "inference", "not_reported")
CONFIDENCE = ("high", "moderate", "low")
EXTRACTION_STATUS = ("verbatim", "paraphrase_flagged", "abstract_only",
                     "not_verified", "extraction_failed")
SUPPORT_LEVELS = ("supported", "partially_supported", "undetermined", "contested")
DECISIONS = ("include", "exclude", "maybe")
ROUNDS = ("title_abstract", "fulltext")
EVIDENCE_REQUIRED = ("evidence_id", "source_id", "locator", "quote")

# --------------------------------------------------------------------------- #
# Work-file loaders
# --------------------------------------------------------------------------- #


def work_file(work_dir, name):
    return os.path.join(work_dir, name)


def load_protocol(work_dir):
    obj, errors, degradations = R.load_json(work_file(work_dir, "protocol.json"))
    return (obj if isinstance(obj, dict) else {}), errors, degradations


def load_sources(work_dir):
    return R.load_jsonl(work_file(work_dir, "sources.jsonl"))


def load_screening(work_dir):
    return R.load_jsonl(work_file(work_dir, "screening.jsonl"))


def load_appraisal(work_dir):
    return R.load_jsonl(work_file(work_dir, "quality_appraisal.jsonl"))


def load_evidence(work_dir):
    return R.load_jsonl(work_file(work_dir, "evidence.jsonl"))


def load_synthesis(work_dir):
    obj, errors, degradations = R.load_json(work_file(work_dir, "synthesis.json"))
    return (obj if isinstance(obj, dict) else {}), errors, degradations


# --------------------------------------------------------------------------- #
# Validators
# --------------------------------------------------------------------------- #


def _errs(report, messages):
    report["errors"].extend(messages)


def validate_protocol(protocol):
    report = {"kind": "protocol", "errors": [], "warnings": []}
    if not protocol.get("review_question"):
        _errs(report, ["review_question missing or empty"])
    subs = protocol.get("subquestions")
    if not isinstance(subs, list) or not subs:
        _errs(report, ["subquestions must be a non-empty list"])
    else:
        for i, sub in enumerate(subs):
            if not isinstance(sub, dict) or not sub.get("text"):
                _errs(report, ["subquestions[%d] missing text" % i])
    blocks = (protocol.get("search_strategy") or {}).get("query_blocks")
    if not isinstance(blocks, list) or not blocks:
        _errs(report, ["search_strategy.query_blocks missing or empty"])
    else:
        for i, blk in enumerate(blocks):
            for key in ("database", "query", "intent"):
                if not isinstance(blk, dict) or not blk.get(key):
                    _errs(report, ["query_blocks[%d] missing %s" % (i, key)])
    for name in ("inclusion_criteria", "exclusion_criteria"):
        crits = protocol.get(name)
        if not isinstance(crits, list) or not crits:
            _errs(report, ["%s must be a non-empty list" % name])
        else:
            for i, crit in enumerate(crits):
                if not isinstance(crit, dict) or not crit.get("id") or not crit.get("text"):
                    _errs(report, ["%s[%d] missing id/text" % (name, i)])
    return report


def _criteria_ids(protocol):
    ids = set()
    for name in ("inclusion_criteria", "exclusion_criteria"):
        for crit in (protocol.get(name) or []):
            if isinstance(crit, dict) and crit.get("id"):
                ids.add(str(crit["id"]))
    return ids


def validate_screening(records, criteria_ids):
    report = {"kind": "screening", "errors": [], "warnings": [], "counts": {}}
    seen_rounds = set()
    source_ids = set()
    for i, rec in enumerate(records, 1):
        if not isinstance(rec, dict):
            _errs(report, ["record %d is not an object" % i])
            continue
        sid = rec.get("source_id")
        if not sid:
            _errs(report, ["record %d missing source_id" % i])
        else:
            source_ids.add(str(sid))
        if rec.get("round") not in ROUNDS:
            _errs(report, ["record %d invalid round %r" % (i, rec.get("round"))])
        else:
            seen_rounds.add(rec["round"])
        if rec.get("decision") not in DECISIONS:
            _errs(report, ["record %d invalid decision %r" % (i, rec.get("decision"))])
        if rec.get("decision") == "exclude":
            if not (rec.get("reason") or "").strip():
                _errs(report, ["record %d exclude missing reason" % i])
            if not rec.get("criterion_id"):
                _errs(report, ["record %d exclude missing criterion_id" % i])
            elif criteria_ids and str(rec["criterion_id"]) not in criteria_ids:
                _errs(report, ["record %d criterion_id %r not in criteria" %
                               (i, rec["criterion_id"])])
        if rec.get("duplicate_of") and str(rec["duplicate_of"]) not in source_ids:
            _errs(report, ["record %d duplicate_of %r not yet seen" %
                           (i, rec["duplicate_of"])])
    if "title_abstract" not in seen_rounds:
        _errs(report, ["missing title_abstract round records"])
    if "fulltext" not in seen_rounds:
        report["warnings"].append("no fulltext round records (allowed only if no fulltext obtained)")
    report["counts"] = recompute_prisma(records)
    report["source_ids"] = sorted(source_ids)
    return report


def recompute_prisma(records):
    counts = {
        "identified": 0, "deduplicated": 0,
        "screened_title_abstract": 0, "excluded_title_abstract": 0,
        "screened_fulltext": 0, "excluded_fulltext": 0, "included": 0,
    }
    sources = set()
    title_include = set()
    for rec in records:
        if not isinstance(rec, dict) or not rec.get("source_id"):
            continue
        sid = str(rec["source_id"])
        sources.add(sid)
        if rec.get("round") == "title_abstract":
            counts["screened_title_abstract"] += 1
            if rec.get("decision") == "exclude":
                counts["excluded_title_abstract"] += 1
            elif rec.get("decision") == "include":
                title_include.add(sid)
        elif rec.get("round") == "fulltext":
            counts["screened_fulltext"] += 1
            if rec.get("decision") == "exclude":
                counts["excluded_fulltext"] += 1
    counts["identified"] = len(sources)
    included = set()
    for sid in sources:
        decisions = [rec.get("decision") for rec in records
                     if isinstance(rec, dict) and str(rec.get("source_id")) == sid
                     and rec.get("round") == "fulltext"]
        if decisions:
            if "include" in decisions:
                included.add(sid)
        elif sid in title_include:
            included.add(sid)
    counts["included"] = len(included)
    return counts


def validate_appraisal(records):
    report = {"kind": "quality_appraisal", "errors": [], "warnings": []}
    if not records:
        _errs(report, ["no quality appraisal records"])
    for i, rec in enumerate(records, 1):
        if not isinstance(rec, dict) or not rec.get("source_id"):
            _errs(report, ["record %d missing source_id" % i])
            continue
        dims = rec.get("dimensions")
        if not isinstance(dims, list) or not dims:
            _errs(report, ["record %d missing dimensions" % i])
        else:
            for j, dim in enumerate(dims):
                if not isinstance(dim, dict) or not dim.get("dimension"):
                    _errs(report, ["record %d dimensions[%d] missing dimension" % (i, j)])
                if not (dim.get("basis_locator") or dim.get("quote")):
                    _errs(report, ["record %d dimensions[%d] missing basis_locator/quote" % (i, j)])
        if not rec.get("overall"):
            _errs(report, ["record %d missing overall grade" % i])
        if not rec.get("impact_on_conclusions"):
            _errs(report, ["record %d missing impact_on_conclusions" % i])
    return report


def _locator_file(locator):
    if isinstance(locator, dict):
        return locator.get("file") or locator.get("path")
    if isinstance(locator, str):
        return locator.split("::", 1)[0] or locator
    return None


def resolve_source_file(input_dir, locator):
    rel = _locator_file(locator)
    if not rel:
        return None
    rel = rel.replace("\\", "/")
    candidate = os.path.join(input_dir, rel)
    if os.path.isfile(candidate):
        return candidate
    base = os.path.basename(rel)
    for root, _dirs, files in os.walk(input_dir):
        for name in files:
            if name == base:
                return os.path.join(root, name)
    return candidate


def verify_evidence_items(items, input_dir):
    """Check each evidence item's quote against the actual source text.

    Adds ``quote_check`` to each item in place and returns a summary. A quote
    that cannot be found is never silently trusted.
    """
    summary = {"total": 0, "verbatim": 0, "quote_mismatch": 0,
               "no_text_layer": 0, "file_unavailable": 0, "no_locator": 0}
    cache = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        summary["total"] += 1
        locator = item.get("locator")
        path = resolve_source_file(input_dir, locator)
        if path is None:
            status = "no_locator"
        elif not os.path.isfile(path):
            status = "file_unavailable"
        else:
            if path not in cache:
                text, _errs, _degs = R.load_text(path)
                cache[path] = text
            text = cache[path]
            q = R.norm_text(item.get("quote"))
            if not q:
                status = "empty_quote"
            elif not R.norm_text(text):
                status = "no_text_layer"
            elif q in R.norm_text(text):
                status = "verbatim"
            else:
                status = "quote_mismatch"
        item["quote_check"] = {"verified": status == "verbatim", "status": status}
        summary[status] = summary.get(status, 0) + 1
        # Honest downgrade: an unverifiable quote is not "verbatim".
        if status != "verbatim" and item.get("extraction_status") == "verbatim":
            item["extraction_status"] = "not_verified"
        if status != "verbatim" and item.get("confidence") == "high":
            item["confidence"] = "low"
    return summary


def validate_evidence(items):
    report = {"kind": "evidence", "errors": [], "warnings": []}
    if not items:
        _errs(report, ["no evidence items"])
    seen = set()
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            _errs(report, ["record %d is not an object" % i])
            continue
        for key in EVIDENCE_REQUIRED:
            if not item.get(key):
                _errs(report, ["record %d missing %s" % (i, key)])
        eid = item.get("evidence_id")
        if eid and eid in seen:
            _errs(report, ["record %d duplicate evidence_id %r" % (i, eid)])
        if eid:
            seen.add(str(eid))
        if item.get("claim_type") and item["claim_type"] not in CLAIM_TYPES:
            _errs(report, ["record %d invalid claim_type %r" % (i, item["claim_type"])])
        if item.get("confidence") and item["confidence"] not in CONFIDENCE:
            _errs(report, ["record %d invalid confidence %r" % (i, item["confidence"])])
        loc = item.get("locator")
        if isinstance(loc, dict) and not (loc.get("file") or loc.get("path")):
            _errs(report, ["record %d locator missing file" % i])
        if not isinstance(loc, (dict, str)):
            _errs(report, ["record %d locator must be dict or string" % i])
    return report


def build_by_source(items, sources):
    registry = {str(s.get("source_id")): s for s in sources
                if isinstance(s, dict) and s.get("source_id")}
    by_source = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("source_id"):
            continue
        sid = str(item["source_id"])
        by_source.setdefault(sid, {
            "source": registry.get(sid, {"source_id": sid}),
            "evidence_ids": [],
            "count": 0,
        })
        by_source[sid]["evidence_ids"].append(str(item["evidence_id"]))
        by_source[sid]["count"] += 1
    for sid in by_source:
        by_source[sid]["evidence_ids"].sort()
    return by_source


def validate_synthesis(synthesis, evidence_map):
    report = {"kind": "synthesis", "errors": [], "warnings": []}
    evidence_ids = {str(e.get("evidence_id")) for e in evidence_map.get("evidence", [])}
    conclusions = synthesis.get("conclusions")
    if not isinstance(conclusions, list) or not conclusions:
        _errs(report, ["synthesis.conclusions missing or empty"])
    else:
        for i, con in enumerate(conclusions, 1):
            if not isinstance(con, dict) or not con.get("conclusion_id"):
                _errs(report, ["conclusions[%d] missing conclusion_id" % i])
            if not (con.get("statement") or "").strip():
                _errs(report, ["conclusions[%d] missing statement" % i])
            ids = con.get("evidence_ids") or []
            if not ids:
                _errs(report, ["conclusions[%d] missing evidence_ids" % i])
            for eid in ids:
                if str(eid) not in evidence_ids:
                    _errs(report, ["conclusions[%d] evidence_id %r not in evidence_map" % (i, eid)])
            if con.get("support_level") and con["support_level"] not in SUPPORT_LEVELS:
                _errs(report, ["conclusions[%d] invalid support_level %r" % (i, con["support_level"])])
    conflicts = synthesis.get("conflicts") or []
    for i, con in enumerate(conflicts, 1):
        ids = con.get("evidence_ids") or con.get("sources") or []
        if len(ids) < 2:
            _errs(report, ["conflicts[%d] needs >=2 evidence_ids/sources" % i])
    if not (synthesis.get("gaps") or []):
        report["warnings"].append("no research gaps recorded")
    return report


def build_by_conclusion(synthesis):
    by_conclusion = {}
    for con in (synthesis.get("conclusions") or []):
        if isinstance(con, dict) and con.get("conclusion_id"):
            by_conclusion[str(con["conclusion_id"])] = list(con.get("evidence_ids") or [])
    return by_conclusion


# --------------------------------------------------------------------------- #
# Review.md generator (deterministic, human-readable)
# --------------------------------------------------------------------------- #


def render_review(result):
    lines = []
    lines.append("# 系统性文献综述：复核摘要")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id", ""))
    lines.append("- status: `%s`" % result.get("status", ""))
    lines.append("")
    lines.append("## 1. 研究问题")
    lines.append("")
    lines.append(str(result.get("review_question") or "（未提供）"))
    lines.append("")
    subs = result.get("subquestions") or []
    if subs:
        lines.append("### 子问题")
        lines.append("")
        for sub in subs:
            if isinstance(sub, dict):
                dep = "（依赖 %s）" % ",".join(sub.get("depends_on") or []) if sub.get("depends_on") else ""
                lines.append("- %s%s" % (sub.get("text", ""), dep))
            else:
                lines.append("- %s" % sub)
        lines.append("")
    lines.append("## 2. 检索策略")
    lines.append("")
    blocks = ((result.get("search_strategy") or {}).get("query_blocks")) or []
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        lines.append("- 数据库 `%s`：`%s`（%s）" % (
            blk.get("database", "?"), blk.get("query", ""), blk.get("intent", "")))
    if not blocks:
        lines.append("（未记录检索块）")
    lines.append("")
    lines.append("## 3. 纳排标准")
    lines.append("")
    for name, label in (("inclusion_criteria", "纳入"), ("exclusion_criteria", "排除")):
        lines.append("### %s标准" % label)
        lines.append("")
        for crit in (result.get(name) or []):
            if isinstance(crit, dict):
                lines.append("- %s：%s" % (crit.get("id", "?"), crit.get("text", "")))
            else:
                lines.append("- %s" % crit)
        lines.append("")
    lines.append("## 4. 筛选与 PRISMA")
    lines.append("")
    prisma = result.get("prisma_flow") or {}
    lines.append("- 识别：%s；去重后：%s；题录筛选：%s（排除 %s）；全文筛选：%s（排除 %s）；纳入：%s" % (
        prisma.get("identified", 0), prisma.get("deduplicated", 0),
        prisma.get("screened_title_abstract", 0), prisma.get("excluded_title_abstract", 0),
        prisma.get("screened_fulltext", 0), prisma.get("excluded_fulltext", 0),
        prisma.get("included", 0)))
    lines.append("")
    lines.append("## 5. 结论与证据回链")
    lines.append("")
    for con in ((result.get("synthesis") or {}).get("conclusions")) or []:
        if not isinstance(con, dict):
            continue
        lines.append("### %s（%s）" % (con.get("conclusion_id", "?"), con.get("support_level", "?")))
        lines.append("")
        lines.append(str(con.get("statement", "")))
        lines.append("")
        for basis in (con.get("basis") or []):
            if isinstance(basis, dict):
                lines.append("- 证据 `%s` [%s]：" % (basis.get("evidence_id", "?"), basis.get("role", "support")))
                lines.append("  > %s" % (basis.get("quote") or "").replace("\n", " "))
                lines.append("")
        if con.get("boundary"):
            lines.append("边界：%s" % con["boundary"])
            lines.append("")
    lines.append("## 6. 冲突证据")
    lines.append("")
    for conf in ((result.get("synthesis") or {}).get("conflicts")) or []:
        if isinstance(conf, dict):
            lines.append("- %s（证据：%s）" % (conf.get("note", ""), ",".join(str(x) for x in (conf.get("evidence_ids") or conf.get("sources") or []))))
        else:
            lines.append("- %s" % conf)
    lines.append("")
    lines.append("## 7. 研究空白与不确定性")
    lines.append("")
    for gap in ((result.get("synthesis") or {}).get("gaps")) or []:
        if isinstance(gap, dict):
            lines.append("- %s" % (gap.get("note") or gap.get("gap") or ""))
        else:
            lines.append("- %s" % gap)
    uncertainty = result.get("uncertainty") or {}
    if uncertainty:
        lines.append("")
        lines.append("不确定性：%s" % ", ".join("%s=%s" % (k, v) for k, v in uncertainty.items()))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-check runner (also the standalone entry point)
# --------------------------------------------------------------------------- #


def run_selfcheck(input_dir, output_dir, work_dir):
    state_obj, _, _ = R.load_json(os.path.join(work_dir, "run_state.json"))
    state = state_obj if isinstance(state_obj, dict) else {}
    result, errs, degs = R.load_json(os.path.join(output_dir, "result.json"))
    if not isinstance(result, dict):
        result = R.new_skeleton(state.get("case_id") or "",
                                state.get("contract_extras") or {},
                                "rebuilt in selfcheck")
        result["status"] = R.STATUS_INSUFFICIENT

    report = {"errors": list(errs), "warnings": list(degs),
              "checks": [], "pass": 0, "fail": 0}

    def check(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        report["pass" if ok else "fail"] += 1

    check("result_parseable", isinstance(result, dict))
    if not isinstance(result, dict):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    check("case_id", bool(result.get("case_id")) and result.get("case_id") != "unknown",
          "case_id=%r" % result.get("case_id"))

    spec = state.get("contract_spec")
    missing = []
    if isinstance(spec, dict):
        missing = [k for k in spec if k not in result]
    elif isinstance(spec, list):
        missing = [k for k in spec if isinstance(k, str) and k not in result]
    check("contract_fields", not missing, "missing: %s" % missing)

    check("status_valid", result.get("status") in R.STATUSES,
          "status=%r" % result.get("status"))
    check("review_question", bool(result.get("review_question")))
    check("subquestions", isinstance(result.get("subquestions"), list)
          and len(result.get("subquestions") or []) >= 1)

    blocks = (result.get("search_strategy") or {}).get("query_blocks") or []
    check("query_blocks", bool(blocks) and all(
        isinstance(b, dict) and b.get("database") and b.get("query") and b.get("intent")
        for b in blocks))

    inc = result.get("inclusion_criteria") or []
    exc = result.get("exclusion_criteria") or []
    check("criteria", bool(inc) and bool(exc) and all(
        isinstance(c, dict) and c.get("id") and c.get("text") for c in inc + exc))

    criteria_ids = {str(c.get("id")) for c in inc + exc
                    if isinstance(c, dict) and c.get("id")}
    srep = validate_screening(result.get("screening_records") or [], criteria_ids)
    check("screening_records", not srep["errors"], "; ".join(srep["errors"]))

    arep = validate_appraisal(result.get("quality_appraisal") or [])
    check("quality_appraisal", not arep["errors"], "; ".join(arep["errors"]))

    erep = validate_evidence(result["evidence_map"]["evidence"])
    check("evidence", not erep["errors"], "; ".join(erep["errors"]))

    quote_summary = verify_evidence_items(result["evidence_map"]["evidence"], input_dir)
    check("quote_verification", quote_summary.get("quote_mismatch", 0) == 0,
          json.dumps(quote_summary, ensure_ascii=False))

    srep2 = validate_synthesis(result.get("synthesis") or {},
                               result.get("evidence_map") or {})
    check("synthesis", not srep2["errors"], "; ".join(srep2["errors"]))

    check("conflicts", bool(result.get("synthesis", {}).get("conflicts")))
    check("gaps", bool(result.get("synthesis", {}).get("gaps")))
    check("change_log", isinstance(result.get("change_log"), list)
          and bool(result.get("change_log")))
    check("uncertainty_field", "uncertainty" in result)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    parser.add_argument("--output", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    parser.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    args = parser.parse_args(argv)
    run_selfcheck(args.input, args.output, args.work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
