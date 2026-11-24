"""finalize.py -- merge intermediate artifacts, run numeric candidate checks, validate, render.

Reads /app/work/{manifest,segments,figure_model,plan,state,meta}.json and
/app/work/findings/*.json (plus optional findings.jsonl). Merges into
/app/output/result.json, preserving every unknown top-level key (output_contract
backfill). Renders /app/output/report.md.

Stdlib only. Exit 0 on success (validation issues are recorded, not fatal).
"""
import argparse
import copy
import glob
import hashlib
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

KNOWN_TOP_KEYS = {
    "case_id", "status", "summary", "review_report", "result_status", "evidence",
    "sources", "uncertainty", "pending_human", "limitations", "errors",
    "degradations", "change_log", "runtime", "numeric_checks", "output_contract",
}
FACET_IDS = ["research_design", "variable_definition", "statistical_methods",
             "sample_size_power", "results_reporting"]
FACET_TITLES = {"research_design": "研究设计", "variable_definition": "变量定义",
                "statistical_methods": "统计方法", "sample_size_power": "样本量与检验效能",
                "results_reporting": "结果报告"}
CHECK_CLASSES = {"fact_check", "citation_support", "method_design",
                 "statistical_reporting", "argumentation_chain", "academic_norms"}
FINDING_TYPES = {"factual_error", "insufficient_evidence", "normative_issue",
                 "advisory", "pending_review"}
SEVERITIES = {"critical", "major", "minor", "advisory", "pending"}
REVIEW_STATUSES = {"confirmed", "needs_review", "not_reported", "not_verified"}


def log(msg):
    print("[finalize] " + msg, flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Merge findings and intermediate artifacts into result.json + report.md.")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    return p.parse_args(argv)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def load_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def sha12(text):
    return hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:12]


def load_findings(work):
    findings = []
    for p in sorted(glob.glob(os.path.join(work, "findings", "*.json"))):
        f = load_json(p)
        if isinstance(f, dict):
            f.setdefault("_path", os.path.basename(p))
            findings.append(f)
    jl = os.path.join(work, "findings.jsonl")
    if os.path.isfile(jl):
        with open(jl, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    f = json.loads(line)
                    if isinstance(f, dict):
                        findings.append(f)
                except Exception:  # noqa: BLE001
                    pass
    findings.sort(key=lambda x: str(x.get("id", "")))
    return findings


def build_seg_maps(segments):
    by_id, by_locator = {}, {}
    for s in segments:
        by_id[s["id"]] = s
        by_locator.setdefault(s["locator"], s)
    return by_id, by_locator


def validate_findings(findings, by_id, by_locator, errors):
    issues = []
    seen = set()
    for f in findings:
        fid = f.get("id")
        if not fid:
            issues.append({"finding": "<no-id>", "issue": "missing id"})
            continue
        if fid in seen:
            issues.append({"finding": fid, "issue": "duplicate id"})
        seen.add(fid)
        for k in ("id", "facet", "locator", "quote", "severity", "suggestion"):
            if not f.get(k):
                issues.append({"finding": fid, "issue": "missing_%s" % k})
        if f.get("facet") not in FACET_IDS:
            issues.append({"finding": fid, "issue": "invalid_facet", "value": f.get("facet")})
        if f.get("severity") not in SEVERITIES:
            issues.append({"finding": fid, "issue": "invalid_severity", "value": f.get("severity")})
        if f.get("finding_type") not in FINDING_TYPES:
            issues.append({"finding": fid, "issue": "invalid_finding_type",
                           "value": f.get("finding_type")})
        if f.get("check_class") not in CHECK_CLASSES:
            issues.append({"finding": fid, "issue": "invalid_check_class",
                           "value": f.get("check_class")})
        if f.get("review_status") not in REVIEW_STATUSES:
            issues.append({"finding": fid, "issue": "invalid_review_status",
                           "value": f.get("review_status")})
        # source-segment hash: fill if missing, flag if mismatched
        seg = by_id.get(f.get("segment_id")) or by_locator.get(f.get("locator"))
        if seg is not None:
            want = sha12(seg["text"])
            got = f.get("source_sha")
            if not got:
                f["source_sha"] = want
            elif got != want:
                issues.append({"finding": fid, "issue": "source_sha_mismatch",
                               "value": got, "expected": want})
    for i in issues:
        errors.append({"kind": "finding_validation", **i})
    return issues


# ---- numeric candidate checks (candidates only; agent must confirm) ----

P_RE = re.compile(r"(?i)\bp\s*(=|<=|>=|<|>)\s*(0?\.\d+)")
CI_RE = re.compile(r"(?i)95\s*%\s*C\.?I\.?\s*[:\-]?\s*([-+]?\d+\.?\d*)\s*(?:to|,|–|-)\s*([-+]?\d+\.?\d*)")
N_RE = re.compile(r"(?i)\b([Nn])\s*[=:]\s*(\d[\d,]*)")
SIG_WORDS_POS = re.compile(r"(显著|significant(?:ly)?|有统计学意义|difference)", re.I)
SIG_WORDS_NEG = re.compile(r"(不显著|无显著|未显著|not significant|no significant|无统计学意义)", re.I)


def p_is_sig(pval):
    try:
        return float(pval) <= 0.05
    except Exception:  # noqa: BLE001
        return None


def numeric_checks(segments):
    checks = []
    n_values = {}
    cid = 0
    for s in segments:
        text = s["text"]
        # rule 1: p vs significance wording
        for pm in P_RE.finditer(text):
            sig = p_is_sig(pm.group(2))
            if sig is None:
                continue
            neg = bool(SIG_WORDS_NEG.search(text))
            pos = bool(SIG_WORDS_POS.search(text))
            if (sig and neg) or (not sig and pos):
                cid += 1
                checks.append({
                    "id": "nc%03d" % cid, "kind": "p_vs_wording",
                    "segment_id": s["id"], "locator": s["locator"],
                    "matched_text": norm(text)[:200],
                    "detail": "p%s%s => significant=%s; wording pos=%s neg=%s"
                              % (pm.group(1), pm.group(2), sig, pos, neg),
                    "status": "candidate"})
        # rule 2: p and CI co-occur (direction left to agent)
        if P_RE.search(text) and CI_RE.search(text):
            cm = CI_RE.search(text)
            cid += 1
            checks.append({
                "id": "nc%03d" % cid, "kind": "p_vs_ci",
                "segment_id": s["id"], "locator": s["locator"],
                "matched_text": norm(text)[:200],
                "detail": "p and 95%% CI co-occur (CI %s-%s); confirm they reference the same outcome"
                          % (cm.group(1), cm.group(2)),
                "status": "candidate"})
        # rule 3: N/n values
        for nm in N_RE.finditer(text):
            key = nm.group(1).lower()
            val = nm.group(2).replace(",", "")
            n_values.setdefault(key, {})
            n_values[key].setdefault(val, []).append(s["locator"])
    for key, vals in n_values.items():
        if len(vals) > 1:
            cid += 1
            detail = "; ".join("N=%s @ %s" % (v, ", ".join(locs)) for v, locs in vals.items())
            checks.append({
                "id": "nc%03d" % cid, "kind": "n_inconsistency",
                "segment_id": None, "locator": None,
                "matched_text": None,
                "detail": "multiple %s values: %s" % (key, detail),
                "status": "candidate"})
    return checks


def derive_status(result, meta, plan):
    if meta.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"):
        return meta["status"]
    status = result.get("status", "partial")
    if not result.get("review_report", {}).get("findings") and not meta.get("checked_facets"):
        return "insufficient_evidence" if status == "insufficient_evidence" else status
    return status


def derive_overall(result, meta, plan, findings):
    if meta.get("overall_verdict"):
        return meta["overall_verdict"]
    if result.get("status") in ("input_error", "insufficient_evidence") and not findings:
        return result.get("status")
    checked = set(meta.get("checked_facets", []))
    if plan and checked != set(FACET_IDS):
        # verdict should reflect findings regardless; mark partial coverage below
        pass
    sev = [f.get("severity") for f in findings]
    rstatus = [f.get("review_status") for f in findings]
    if any(x in ("needs_review", "pending") or f.get("finding_type") == "pending_review"
           for x, f in zip(rstatus, findings)):
        return "待复核（存在无法确认项）"
    if any(x in ("critical",) for x in sev):
        return "需要重大修改（存在使主要结论失效的硬伤）"
    if any(x in ("major",) for x in sev):
        return "需要修改"
    if findings:
        return "有小问题或建议，可修改后接受"
    return "未发现明确问题（按已检查面）"


def derive_result_status(result, meta, plan, findings):
    if isinstance(meta.get("result_status"), dict) and meta["result_status"].get("overall"):
        return meta["result_status"]
    checked = set(meta.get("checked_facets", []))
    unchecked = [f for f in FACET_IDS if f not in checked]
    if unchecked and not findings:
        overall = "未检查"
    else:
        has_pending = any(f.get("review_status") in ("needs_review",)
                          or f.get("finding_type") == "pending_review"
                          or f.get("severity") == "pending" for f in findings)
        has_issue = any(f.get("severity") in ("critical", "major", "minor") for f in findings)
        if has_pending:
            overall = "待复核"
        elif has_issue:
            overall = "问题"
        else:
            overall = "通过"
    return {
        "overall": overall,
        "pass": [f.get("id") for f in findings if f.get("severity") == "advisory"],
        "issue": [f.get("id") for f in findings if f.get("severity") in ("critical", "major", "minor")],
        "needs_review": [f.get("id") for f in findings
                         if f.get("review_status") == "needs_review"
                         or f.get("finding_type") == "pending_review"],
        "unchecked": unchecked,
    }


def render_report(result, plan, meta):
    lines = []
    lines.append("# 研究方法与统计审查报告")
    lines.append("")
    lines.append("- case_id: `%s`" % (result.get("case_id") or "-"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- verdict: **%s**" % result.get("summary", {}).get("verdict", "-"))
    c = result.get("summary", {}).get("counts", {})
    lines.append("- 问题数: %s" % c.get("total", 0))
    lines.append("")
    findings = result.get("review_report", {}).get("findings", [])
    if findings:
        lines.append("## 审查意见（逐条）")
        lines.append("")
        cur = None
        for f in findings:
            facet = f.get("facet")
            if facet != cur:
                cur = facet
                lines.append("### %s" % FACET_TITLES.get(facet, facet))
                lines.append("")
            lines.append("#### %s `[%s]` %s" % (f.get("id"), f.get("severity"), f.get("locator")))
            lines.append("")
            lines.append("- 类型: `%s` / `%s`（置信度 %s，复核状态 %s）"
                         % (f.get("finding_type"), f.get("check_class"),
                            f.get("confidence"), f.get("review_status")))
            lines.append("- 原文摘录: > %s" % (f.get("quote") or "-"))
            lines.append("- 依据: %s" % (f.get("basis") or "-"))
            lines.append("- 建议: %s" % (f.get("suggestion") or "-"))
            if f.get("source_locators"):
                lines.append("- 冲突定位: %s" % "; ".join(f["source_locators"]))
            lines.append("")
    else:
        lines.append("## 审查意见")
        lines.append("")
        lines.append("（暂无落盘的 finding）")
        lines.append("")
    rs = result.get("result_status", {})
    lines.append("## 结果状态")
    lines.append("")
    lines.append("- 总体: **%s**" % rs.get("overall", "-"))
    lines.append("- 未检查面: %s" % ", ".join(rs.get("unchecked", [])) or "无")
    lines.append("")
    nc = result.get("numeric_checks", [])
    if nc:
        lines.append("## 数值一致性候选检查（待人工复核）")
        lines.append("")
        for n in nc:
            lines.append("- `%s` [%s] %s: %s" % (n.get("id"), n.get("kind"),
                                                  n.get("locator") or "-", n.get("detail")))
        lines.append("")
    for sec in ("limitations", "uncertainty", "pending_human", "sources", "change_log",
                "errors", "degradations"):
        items = result.get(sec, [])
        if items:
            lines.append("## %s" % sec)
            lines.append("")
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        lines.append("- %s" % json.dumps(it, ensure_ascii=False))
                    else:
                        lines.append("- %s" % it)
            else:
                lines.append("- %s" % items)
            lines.append("")
    return "\n".join(lines)


def main(argv=None):
    args = parse_args(argv)
    ensure_dir(args.output)
    work = args.work
    started = time.time()

    manifest = load_json(os.path.join(work, "manifest.json"), {})
    segments = load_json(os.path.join(work, "segments.json"), {}).get("segments", [])
    figure_model = load_json(os.path.join(work, "figure_model.json"), {})
    plan = load_json(os.path.join(work, "plan.json"), None)
    state = load_json(os.path.join(work, "state.json"), {})
    meta = load_json(os.path.join(work, "meta.json"), {})

    # start from previous result (output_contract backfill + change_log history + status)
    existing = load_json(os.path.join(args.output, "result.json"), None) or {}
    result = {
        "case_id": existing.get("case_id") or manifest.get("case_id"),
        "status": "partial",
        "summary": {"verdict": "unchecked", "counts": {"total": 0}},
        "review_report": {"findings": []},
        "result_status": {"overall": "未检查", "pass": [], "issue": [],
                          "needs_review": [], "unchecked": list(FACET_IDS)},
        "evidence": {"evidence_map": []},
        "sources": [], "uncertainty": [], "pending_human": [], "limitations": [],
        "errors": [], "degradations": [], "change_log": [],
        "runtime": {"started": time.time(), "seconds": None},
        "numeric_checks": [],
    }
    for k, v in existing.items():
        result[k] = copy.deepcopy(v)  # existing (incl. contract fields) wins over defaults

    errors = []
    for e in manifest.get("errors", []):
        errors.append(copy.deepcopy(e))
    result["errors"] = errors
    result["degradations"] = copy.deepcopy(manifest.get("degradations", []))
    for e in meta.get("errors", []):
        result["errors"].append(copy.deepcopy(e))
    for d in meta.get("degradations", []):
        result["degradations"].append(copy.deepcopy(d))

    findings = load_findings(work)
    by_id, by_locator = build_seg_maps(segments)
    validate_findings(findings, by_id, by_locator, result["errors"])

    checks = numeric_checks(segments)
    # apply agent decisions from meta.numeric_decisions (list of {id, status})
    decided = {}
    for d in meta.get("numeric_decisions", []):
        if isinstance(d, dict) and d.get("id"):
            decided[d["id"]] = d.get("status")
    for chk in checks:
        if chk["id"] in decided:
            chk["status"] = decided[chk["id"]]
    result["numeric_checks"] = checks

    # counts
    by_facet = {f: 0 for f in FACET_IDS}
    by_type, by_severity = {}, {}
    for f in findings:
        by_facet[f.get("facet", "")] = by_facet.get(f.get("facet", ""), 0) + 1
        by_type[f.get("finding_type", "")] = by_type.get(f.get("finding_type", ""), 0) + 1
        by_severity[f.get("severity", "")] = by_severity.get(f.get("severity", ""), 0) + 1
    result["review_report"] = {"findings": findings}
    result["status"] = derive_status(result, meta, plan)
    result["summary"] = {
        "verdict": derive_overall(result, meta, plan, findings),
        "overall_assessment": meta.get("overall_assessment", ""),
        "counts": {"total": len(findings), "by_facet": by_facet,
                   "by_type": by_type, "by_severity": by_severity},
    }
    result["result_status"] = derive_result_status(result, meta, plan, findings)
    result["evidence"] = {"evidence_map": [
        {"finding_id": f.get("id"), "locator": f.get("locator"), "quote": f.get("quote"),
         "source_sha": f.get("source_sha"), "basis": f.get("basis"),
         "sources": f.get("sources", [])}
        for f in findings
    ]}
    result["sources"] = meta.get("sources", [])
    result["uncertainty"] = meta.get("uncertainty", [])
    result["pending_human"] = meta.get("pending_human", [])
    result["limitations"] = meta.get("limitations", [])
    # merge scaffold limitations if none from meta
    if not result["limitations"] and existing.get("limitations"):
        result["limitations"] = copy.deepcopy(existing["limitations"])

    result["change_log"] = copy.deepcopy(existing.get("change_log", []))
    for cl in meta.get("change_log", []):
        result["change_log"].append(copy.deepcopy(cl))
    result["change_log"].append({"time": time.time(), "action": "finalize",
                                 "reason": "merged findings=%d" % len(findings)})
    result["runtime"] = existing.get("runtime", {})
    result["runtime"]["seconds"] = round(time.time() - started, 2)
    result["runtime"]["findings"] = len(findings)

    if state:
        result["_state"] = {"phase": state.get("phase"), "findings_written": len(findings)}

    save_json(os.path.join(args.output, "result.json"), result)
    with open(os.path.join(args.output, "report.md"), "w", encoding="utf-8") as f:
        f.write(render_report(result, plan, meta))

    log("status=%s verdict=%s findings=%d numeric_candidates=%d"
        % (result["status"], result["summary"]["verdict"], len(findings), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
