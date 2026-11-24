#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-plan 中间表示与可复核：索引、核验、分级、装配。

本文件实现本 Skill 的设计重心——"中间表示与可复核"。一次评价被拆成三个
可持久化、可重放的中间产物，最终 result.json 由它们确定性装配而来：

  plan.json              研究问题 + 检索计划 + 筛选判据 + 检索记录（候选/筛选/遗漏/来源）
  evidence_index.json    每个文档的段落/句子锚点（locator + 原文 + 字符偏移 + 规范化文本）
  assessment_matrix.json 量表实例：维度—判断—证据—等级 的行式矩阵（证据用锚点引用）

派生产物（由脚本确定性计算，不手写）：
  verification_report.json  每条 quote 的逐字核验结果
  grade.json                风险等级聚合与推导过程（阈值显式可查）

命令（在 SKILL.md 所在目录下，用相对路径）：
  python3 -B scripts/litqa.py index    --work /app/work [--text-dir /app/work/extracted]
  python3 -B scripts/litqa.py verify   --work /app/work
  python3 -B scripts/litqa.py grade    --work /app/work
  python3 -B scripts/litqa.py assemble --input /input --work /app/work --output /app/output

只依赖 Python 标准库；确定性运行（无随机数、固定阈值）。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

# 复用 main.py 的 I/O 与输入发现（main.py 在 __main__ 下才运行）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from main import (  # noqa: E402
    DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_WORK, MAX_FILE_BYTES, TEXT_SUFFIXES,
    ALLOWED_STATUS, now_utc, now_epoch, read_text, load_json_lenient,
    atomic_write_json, discover_input, find_contract, contract_defaults,
    extract_case_id, SCHEMA_PLAN, SCHEMA_MATRIX, SCHEMA_INDEX, SCHEMA_RESULT,
    STRUCTURAL_DEFAULTS, REQUIRED_ROW_FIELDS, _index_anchor_map,
)

# 判定词表 → 严重度（分级阈值在 references/rubric.md 中定义，此处为实现）
SEVERITY = {
    "low_risk": 0, "adequate": 0, "absent_sign": 0, "no_sign": 0,
    "reported": 0, "absent": 0,
    "moderate_risk": 1, "partial": 1, "possible": 1, "unclear": 1,
    "present": 1,
    "high_risk": 2, "inadequate": 2, "definite": 2,
    "critical_risk": 3,
}
UNASSESSABLE = {"not_reported", "insufficient_evidence", "not_applicable",
                "na", "n/a", ""}
RISK_ORDER = {"insufficient_evidence": 0, "low": 1, "moderate": 2,
              "high": 3, "critical": 4}
SEVERITY_TO_RISK = {0: "low", 1: "moderate", 2: "high", 3: "critical"}


def normalize(text):
    return re.sub(r"\s+", " ", str(text)).strip()


# ---------------------------------------------------------------------------
# index：文本 → evidence_index.json（段落/句子锚点）
# ---------------------------------------------------------------------------

def _split_pages(text):
    if "\f" in text:
        return [p for p in text.split("\f")]
    # 常见页码标记兜底："Page N" 单独成行
    parts = re.split(r"\n\s*[Pp]age\s+\d+(?:\s+of\s+\d+)?\s*\n", text)
    return parts if len(parts) > 1 else [text]


def _split_paragraphs(page_text):
    raw = [p.strip() for p in re.split(r"\n\s*\n", page_text)]
    out = []
    for p in raw:
        if not p:
            continue
        if len(p) > 2000:
            buf = ""
            for s in re.split(r"(?<=[.!?。！？；])\s+", p):
                s = s.strip()
                if not s:
                    continue
                if buf and len(buf) + len(s) > 1200:
                    out.append(buf)
                    buf = s
                else:
                    buf = (buf + " " + s).strip()
            if buf:
                out.append(buf)
        else:
            out.append(p)
    return out


def _split_sentences(par_text):
    sents = [s.strip() for s in re.split(r"(?<=[.!?。！？；])\s+", par_text)]
    return [s for s in sents if s]


def run_index(work_dir, text_dir=None):
    """扫描文本目录，产出 evidence_index.json。返回 (index, issues)。"""
    issues = []
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    src = Path(text_dir) if text_dir else (work / "extracted")
    files = []
    if src.is_dir():
        files = sorted(p for p in src.rglob("*")
                       if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES)
    if not files:
        # 回退：从 /input 找文本材料
        found, _ = discover_input(DEFAULT_INPUT)
        files = sorted(p for p in found["materials"]
                       if p.suffix.lower() in TEXT_SUFFIXES)
    if not files:
        issues.append("no_text_files_for_index: %s" % src)

    documents = []
    for doc_idx, fp in enumerate(files):
        text, errs = read_text(fp)
        issues.extend(errs)
        if text is None:
            continue
        doc_id = "d%d" % doc_idx
        pages = []
        for page_no, page_text in enumerate(_split_pages(text), start=1):
            anchors = []
            pars = _split_paragraphs(page_text)
            for par_no, par in enumerate(pars):
                par_loc = "%s.p%d.par%d" % (doc_id, page_no, par_no)
                try:
                    char_start = page_text.index(par)
                except ValueError:
                    char_start = -1
                anchors.append({
                    "locator": par_loc, "kind": "paragraph",
                    "text": par, "norm": normalize(par),
                    "char_start": char_start, "char_end": char_start + len(par),
                })
                for s_no, sent in enumerate(_split_sentences(par)):
                    anchors.append({
                        "locator": "%s.s%d" % (par_loc, s_no), "kind": "sentence",
                        "text": sent, "norm": normalize(sent),
                        "char_start": -1, "char_end": -1,
                    })
            pages.append({"page": page_no, "anchors": anchors})
        title_hint = ""
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "%%", "<")):
                title_hint = line[:200]
                break
        documents.append({
            "id": doc_id, "source_path": str(fp), "title_hint": title_hint,
            "pages": pages,
        })

    index = {
        "schema": SCHEMA_INDEX,
        "created_utc": now_utc(),
        "source_dir": str(src),
        "documents": documents,
    }
    atomic_write_json(work / "evidence_index.json", index)
    return index, issues


# ---------------------------------------------------------------------------
# verify：assessment_matrix 的 quote 逐字核验
# ---------------------------------------------------------------------------

def run_verify(work_dir):
    """核对每条 quote 是否逐字存在于所标锚点。返回 (report, issues)。"""
    issues = []
    work = Path(work_dir)
    matrix_path = work / "assessment_matrix.json"
    index_path = work / "evidence_index.json"

    matrix, mi = load_json_lenient(matrix_path)
    index, ii = load_json_lenient(index_path)
    issues.extend(mi + ii)
    if matrix is None:
        return {"schema": "litqa.verification/1.0", "all_verified": False,
                "total": 0, "verified": 0, "unverified": 0,
                "abstained_no_quote": 0, "items": [],
                "error": "matrix_unreadable"}, issues
    if index is None:
        return {"schema": "litqa.verification/1.0", "all_verified": False,
                "total": 0, "verified": 0, "unverified": 0,
                "abstained_no_quote": 0, "items": [],
                "error": "index_missing"}, issues

    anchors = _index_anchor_map(index)
    rows = matrix.get("rows", []) if isinstance(matrix, dict) else []
    items = []
    verified = unverified = abstained = 0

    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = r.get("id", "?")
        quote = str(r.get("quote") or "").strip()
        loc = str(r.get("locator") or "")
        if not quote:
            abstained += 1
            items.append({"row_id": rid, "locator": loc, "quote": "",
                          "verified": None, "match_type": "no_quote",
                          "matched_locator": None})
            continue
        qn = normalize(quote)
        anchor = anchors.get(loc)
        if anchor is not None and qn and qn in anchor.get("norm", ""):
            verified += 1
            items.append({"row_id": rid, "locator": loc, "quote": quote,
                          "verified": True, "match_type": "anchor_exact",
                          "matched_locator": loc})
            continue
        # 找不到锚点或不在锚点内：全文兜底搜索，指出实际命中位置
        hit = None
        for aloc, a in anchors.items():
            if qn and qn in a.get("norm", ""):
                hit = aloc
                break
        if hit:
            verified += 1
            items.append({"row_id": rid, "locator": loc, "quote": quote,
                          "verified": True, "match_type": "found_elsewhere",
                          "matched_locator": hit})
        else:
            unverified += 1
            items.append({"row_id": rid, "locator": loc, "quote": quote,
                          "verified": False, "match_type": "not_found",
                          "matched_locator": None})

    total = len(items)
    report = {
        "schema": "litqa.verification/1.0",
        "checked_utc": now_utc(),
        "total": total,
        "verified": verified,
        "unverified": unverified,
        "abstained_no_quote": abstained,
        "all_verified": unverified == 0,
        "items": items,
    }
    atomic_write_json(work / "verification_report.json", report)
    return report, issues


# ---------------------------------------------------------------------------
# grade：判定词 → 严重度 → 维度/总体风险等级（阈值显式推导）
# ---------------------------------------------------------------------------

def _severity_of(verdict):
    v = str(verdict).strip()
    if v in UNASSESSABLE:
        return None
    return SEVERITY.get(v, None)


def run_grade(work_dir):
    """按维度聚合严重度，写出 grade.json 与显式推导。返回 (grade, issues)。"""
    issues = []
    work = Path(work_dir)
    matrix, mi = load_json_lenient(work / "assessment_matrix.json")
    issues.extend(mi)
    if matrix is None:
        return {"schema": "litqa.grade/1.0", "error": "matrix_unreadable"}, issues

    rows = matrix.get("rows", []) if isinstance(matrix, dict) else []
    dims = {}
    order = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        dim = str(r.get("dimension") or "unspecified")
        sev = _severity_of(r.get("verdict"))
        if dim not in dims:
            dims[dim] = {"assessable": 0, "unassessable": 0, "severity_sum": 0.0,
                         "max_severity": None, "critical_hits": 0, "verdict_counts": {}}
            order.append(dim)
        d = dims[dim]
        v = str(r.get("verdict") or "")
        d["verdict_counts"][v] = d["verdict_counts"].get(v, 0) + 1
        if sev is None:
            d["unassessable"] += 1
        else:
            d["assessable"] += 1
            d["severity_sum"] += sev
            d["max_severity"] = sev if d["max_severity"] is None else max(d["max_severity"], sev)
            if r.get("critical") and sev >= 2:
                d["critical_hits"] += 1

    dim_results = {}
    for dim in order:
        d = dims[dim]
        if d["assessable"] == 0:
            risk = "insufficient_evidence"
        else:
            # 关键检查点出现高危即升维：max_severity 与 critical_hits 取更严
            base = d["max_severity"]
            if d["critical_hits"] > 0 and base < 2:
                base = 2
            risk = SEVERITY_TO_RISK.get(base, "unclear")
        d["risk"] = risk
        dim_results[dim] = {
            "risk": risk,
            "max_severity": d["max_severity"],
            "assessable": d["assessable"],
            "unassessable": d["unassessable"],
            "critical_hits": d["critical_hits"],
            "verdict_counts": d["verdict_counts"],
        }

    assessable_risks = [v["risk"] for v in dim_results.values()
                        if v["risk"] != "insufficient_evidence"]
    if not assessable_risks:
        overall = "insufficient_evidence"
    else:
        overall = max(assessable_risks, key=lambda r: RISK_ORDER.get(r, 0))

    derivation = (
        "overall = max(per-dimension risk); " +
        "; ".join("%s=%s" % (k, v["risk"]) for k, v in dim_results.items()) +
        " -> overall=%s" % overall
    )

    grade = {
        "schema": "litqa.grade/1.0",
        "computed_utc": now_utc(),
        "dimensions": dim_results,
        "overall": overall,
        "derivation": derivation,
        "thresholds": {
            "severity_map": SEVERITY,
            "unassessable": sorted(UNASSESSABLE),
            "per_dimension": "risk = severity_to_risk(max_severity); critical_hit(sev>=2) upgrades to at least high",
            "overall": "max over assessable dimensions; all-unassessable -> insufficient_evidence",
        },
    }
    atomic_write_json(work / "grade.json", grade)
    return grade, issues


# ---------------------------------------------------------------------------
# assemble：中间表示 → result.json + assessment.md（可重放装配）
# ---------------------------------------------------------------------------

def _derive_status(matrix, plan, grade):
    explicit = None
    if isinstance(matrix, dict) and matrix.get("status") in ALLOWED_STATUS:
        explicit = matrix["status"]
    elif isinstance(plan, dict) and plan.get("status") in ALLOWED_STATUS:
        explicit = plan["status"]
    if explicit:
        return explicit
    if grade.get("overall") == "insufficient_evidence":
        return "insufficient_evidence"
    if isinstance(matrix, dict) and not matrix.get("rows"):
        return "partial"
    return "ok"


def _render_markdown(result):
    lines = []
    lines.append("# 文献研究质量评价结果")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- overall risk: `%s`" % result.get("grade", {}).get("overall"))
    lines.append("")
    rq = result.get("research_question") or {}
    if rq.get("text"):
        lines.append("## 研究问题")
        lines.append("")
        lines.append(str(rq.get("text")))
        lines.append("")
    rp = result.get("retrieval_plan") or {}
    if rp.get("query"):
        lines.append("## 检索计划")
        lines.append("")
        lines.append("- query: `%s`" % rp.get("query"))
        lines.append("")
    lines.append("## 评价表（维度—判断—证据—等级）")
    lines.append("")
    lines.append("| # | 维度 | 检查点 | 判定 | 判断 | 证据定位 | 逐字引文 |")
    lines.append("|---|------|--------|------|------|----------|----------|")
    for i, row in enumerate(result.get("assessment", []), start=1):
        lines.append("| %d | %s | %s | `%s` | %s | `%s` | %s |" % (
            i,
            str(row.get("dimension", "")).replace("|", "\\|"),
            str(row.get("checkpoint", "")).replace("|", "\\|"),
            str(row.get("verdict", "")).replace("|", "\\|"),
            str(row.get("judgment", "")).replace("|", "\\|").replace("\n", " "),
            str(row.get("locator", "")).replace("|", "\\|"),
            str(row.get("quote", "")).replace("|", "\\|").replace("\n", " "),
        ))
    lines.append("")
    g = result.get("grade") or {}
    if g.get("derivation"):
        lines.append("## 分级推导")
        lines.append("")
        lines.append(str(g.get("derivation")))
        lines.append("")
    lines.append("## 状态与缺口")
    lines.append("")
    lines.append("已记录：uncertainty=%d, pending_human=%d, limitations=%d, errors=%d, degradations=%d"
                 % (len(result.get("uncertainty", [])), len(result.get("pending_human", [])),
                    len(result.get("limitations", [])), len(result.get("errors", [])),
                    len(result.get("degradations", []))))
    lines.append("")
    return "\n".join(lines)


def run_assemble(input_dir, work_dir, output_dir):
    """读取中间表示 + case.json，装配 result.json 与 assessment.md。"""
    issues = []
    work = Path(work_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 输入
    case_data = None
    if Path(input_dir).is_dir():
        found, fi = discover_input(input_dir)
        issues.extend(fi)
        if found["case_json"]:
            case_data, ci = load_json_lenient(found["case_json"][0])
            issues.extend(ci)
    case_id = extract_case_id(case_data, input_dir)
    contract = find_contract(case_data) if isinstance(case_data, (dict, list)) else None

    plan, pi = load_json_lenient(work / "plan.json")
    matrix, mi = load_json_lenient(work / "assessment_matrix.json")
    grade, gi = load_json_lenient(work / "grade.json")
    verify, vi = load_json_lenient(work / "verification_report.json")
    index, ii = load_json_lenient(work / "evidence_index.json")
    issues.extend(pi + mi + gi + vi + ii)

    plan = plan if isinstance(plan, dict) else {}
    matrix = matrix if isinstance(matrix, dict) else {}
    grade = grade if isinstance(grade, dict) else {}
    verify = verify if isinstance(verify, dict) else {}
    index = index if isinstance(index, dict) else {}

    status = _derive_status(matrix, plan, grade)

    t0 = now_epoch()
    start_epoch = t0
    # 若已存在骨架，保留其 start/skeleton 时间戳
    existing, _ = load_json_lenient(out / "result.json")
    if isinstance(existing, dict) and isinstance(existing.get("runtime"), dict):
        start_epoch = existing["runtime"].get("start_epoch", t0)

    result = {
        "schema": SCHEMA_RESULT,
        "case_id": case_id,
        "status": status,
    }
    result.update(contract_defaults(contract))
    result.update({
        "research_question": plan.get("research_question", {}),
        "retrieval_plan": plan.get("retrieval_plan", {}),
        "candidates": plan.get("candidates", []),
        "screening": plan.get("screening", []),
        "evidence_records": plan.get("evidence_records", []),
        "omissions": plan.get("omissions", []),
        "sources": plan.get("sources", []),
        "uncertainty": plan.get("uncertainty", []),
        "pending_human": plan.get("pending_human", []),
        "errors": list(plan.get("errors", [])) + list(issues),
        "degradations": plan.get("degradations", []),
        "limitations": plan.get("limitations", []),
        "change_log": plan.get("change_log", []),
        "assessment": matrix.get("rows", []),
        "grade": grade,
        "intermediates": {
            "plan": "plan.json",
            "evidence_index": "evidence_index.json",
            "assessment_matrix": "assessment_matrix.json",
            "grade": "grade.json",
            "verification_report": "verification_report.json",
            "document_count": len(index.get("documents", [])),
        },
        "verification": {
            "all_verified": verify.get("all_verified"),
            "total": verify.get("total"),
            "verified": verify.get("verified"),
            "unverified": verify.get("unverified"),
            "abstained_no_quote": verify.get("abstained_no_quote"),
        },
        "runtime": {
            "start_epoch": start_epoch,
            "skeleton_epoch": start_epoch,
            "finish_epoch": now_epoch(),
        },
    })
    # 骨架里已存在 change_log 时追加装配事件
    if isinstance(existing, dict) and isinstance(existing.get("change_log"), list):
        result["change_log"] = existing["change_log"] + result["change_log"]
    result["change_log"].append({
        "time_utc": now_utc(), "event": "assemble",
        "detail": "由中间表示 plan/evidence_index/assessment_matrix 确定性装配",
    })

    atomic_write_json(out / "result.json", result)
    (out / "assessment.md").write_text(_render_markdown(result), encoding="utf-8")
    return result, issues


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(prog="litqa.py", description="中间表示与可复核")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_idx = sub.add_parser("index", help="文本 → evidence_index.json")
    p_idx.add_argument("--work", default=DEFAULT_WORK)
    p_idx.add_argument("--text-dir", default=None)

    p_ver = sub.add_parser("verify", help="逐字核验 quote")
    p_ver.add_argument("--work", default=DEFAULT_WORK)

    p_gr = sub.add_parser("grade", help="聚合风险等级")
    p_gr.add_argument("--work", default=DEFAULT_WORK)

    p_as = sub.add_parser("assemble", help="中间表示 → result.json")
    p_as.add_argument("--input", default=DEFAULT_INPUT)
    p_as.add_argument("--work", default=DEFAULT_WORK)
    p_as.add_argument("--output", default=DEFAULT_OUTPUT)

    args = parser.parse_args(argv)

    if args.cmd == "index":
        index, issues = run_index(args.work, args.text_dir)
        print("index: %d document(s), %d issue(s)"
              % (len(index.get("documents", [])), len(issues)))
        for i in issues:
            print("  [note ] %s" % i)
        return 0

    if args.cmd == "verify":
        report, issues = run_verify(args.work)
        print("verify: total=%d verified=%d unverified=%d abstained=%d all_verified=%s"
              % (report.get("total"), report.get("verified"), report.get("unverified"),
                 report.get("abstained_no_quote"), report.get("all_verified")))
        for item in report.get("items", []):
            if item.get("verified") is False:
                print("  [FAIL ] row=%s locator=%s match=%s"
                      % (item.get("row_id"), item.get("locator"), item.get("match_type")))
        return 0

    if args.cmd == "grade":
        grade, issues = run_grade(args.work)
        print("grade: overall=%s" % grade.get("overall"))
        print("  %s" % grade.get("derivation"))
        return 0

    if args.cmd == "assemble":
        result, issues = run_assemble(args.input, args.work, args.output)
        print("assemble: status=%s case_id=%s assessment_rows=%d issues=%d"
              % (result["status"], result["case_id"],
                 len(result.get("assessment", [])), len(issues)))
        for i in issues:
            print("  [note ] %s" % i)
        return 0

    parser.error("unknown command: %s" % args.cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
