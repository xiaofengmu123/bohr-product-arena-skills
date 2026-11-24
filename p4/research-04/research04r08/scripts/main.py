# -*- coding: utf-8 -*-
"""main.py — 研究前沿趋势 Skill 的确定性入口：发现输入、落骨架、机械抽取。

用法（相对 SKILL.md 所在目录，脚本路径使用相对路径 scripts/main.py）：
  python3 scripts/main.py                       # 默认 prepare + extract
  python3 scripts/main.py --input /input --output /app/output --work /app/work
  python3 scripts/main.py --force               # 忽略既有 manifest 重新抽取
  python3 scripts/main.py log --action A --reason R [--before B --after C]

产物：
  /app/work/manifest.json                       材料清单与每篇文档的机械字段
  /app/work/materials/mat_NNN.ext               抽取后的材料文本（locator 指向这里）
  /app/output/result.json                       骨架 / 机械基线（此后 Agent 增量升级）
  /app/output/evidence_map.json                 证据映射（machine_candidate 起步）
  /app/output/tables/documents.csv              文档字段表
  /app/output/tables/year_distribution.csv      年度分布表
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as ci  # noqa: E402


DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

ALIGNMENT_DIMS = {
    "A1": "任务完成度(15)", "A2": "结果正确性(15)", "A3": "结果完整性(5)", "A4": "结果可核验性(5)",
    "U1": "质量增益(15)", "U2": "效率增益(5)", "U3": "增益可验证性(5)",
    "I1": "新增能力(5)", "I2": "方法改进(5)", "I3": "创新证据(5)",
    "R1": "调用易用(3)", "R2": "运行稳定(4)", "R3": "异常处理(3)",
    "C1": "文档完整(3)", "C2": "资源复用(4)", "C3": "分享价值(3)",
}


def empty_skeleton(case_id: str, contract: dict, contract_keys: list) -> dict:
    base = {
        "case_id": case_id,
        "status": "partial",
        "research_question": {"question": None, "scope": None, "sub_questions": [], "domain_note": None},
        "protocol": {"inclusion_criteria": [], "exclusion_criteria": [],
                     "search_scope": None, "date_definition_note": None},
        "time_trend": {"unit_note": None, "distribution": [], "windows": [], "claims": []},
        "topic_evolution": {"stage_divisions": [], "term_mapping": [], "events": [], "claims": []},
        "representative_works": {"criteria": [], "stages": []},
        "research_gaps": {"gaps": [], "excluded_not_gaps": []},
        "evidence_map": [],
        "conflicts": {"items": [], "resolution": None},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": ci.now_iso(), "finished_at": None, "elapsed_s": None, "model_requests": None},
        "machine": {"baseline": False, "unverified": [], "gap_candidates": [],
                    "conflict_candidates": [], "rep_candidates": [], "term_stats": []},
        "alignment": {k: {"label": v, "status": "pending", "evidence": ""} for k, v in ALIGNMENT_DIMS.items()},
        "output_contract_keys": contract_keys,
    }
    merged = dict(contract)  # 先回填契约字段（缺值 null/[]，保留未知键）
    merged.update(base)      # 固定字段覆盖同名键（固定字段优先，契约同名键已保留值）
    for k in contract_keys:
        if k in base:
            continue
        if merged.get(k) is None:
            merged[k] = None
    return merged


def log_change(result: dict, action: str, reason: str, before=None, after=None) -> None:
    entry = {"time": ci.now_iso(), "action": action, "reason": reason,
             "before": before, "after": after}
    if "change_log" not in result:
        result["change_log"] = []
    result["change_log"].append(entry)


def load_result(output_dir: str) -> dict:
    p = Path(output_dir) / "result.json"
    if p.exists():
        rd = ci.load_json_safe(str(p))
        if rd["obj"] is not None:
            return rd["obj"]
    return None


def _ensure_material_id(counter: list, ext: str) -> str:
    counter[0] += 1
    return "mat_%03d%s" % (counter[0], ext)


def prepare(args) -> dict:
    """扫描输入、解析 case.json/task.md、抽取材料文本、写骨架。"""
    discovered = ci.discover_inputs(args.input)
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(os.path.join(args.work, "materials"), exist_ok=True)

    degradations, errors = [], []
    case_obj, case_err, task_text = None, None, ""

    if discovered["case_json"]:
        jr = ci.load_json_safe(discovered["case_json"]["path"])
        case_obj, case_err = jr["obj"], jr["error"]
        if case_err:
            errors.append("case.json 解析失败: %s" % case_err)
    else:
        degradations.append("未发现 /input/case.json")

    if discovered["task_md"]:
        rd = ci.read_text_safe(discovered["task_md"]["path"])
        task_text = rd["text"] or ""
        if rd["skipped"]:
            degradations.append("task.md 读取跳过: %s" % rd["error"])
    else:
        degradations.append("未发现 /input/task.md")

    if not discovered["exists"] or not discovered["files"]:
        degradations.append("输入目录为空或不存在")

    case_id = ci.case_id_from(case_obj, task_text)
    contract, contract_keys = ci.merge_contract(case_obj)
    result = empty_skeleton(case_id, contract, contract_keys)
    result["degradations"].extend(degradations)
    result["errors"].extend(errors)

    # 抽取材料文本到 /app/work/materials/
    manifest = {"case_id": case_id, "root": discovered["root"], "files": [], "materials": []}
    counter = [0]
    skipped = 0
    for f in discovered["files"]:
        if f is discovered["case_json"] or f is discovered["task_md"]:
            continue
        if f["size"] > ci.MAX_FILE_BYTES:
            skipped += 1
            result["degradations"].append("超限跳过 %s (%d bytes)" % (f["rel"], f["size"]))
            continue
        if skipped + counter[0] >= ci.MAX_FILES:
            skipped += 1
            result["degradations"].append("达到 %d 文件上限，跳过 %s" % (ci.MAX_FILES, f["rel"]))
            continue
        ext = f["ext"] if f["ext"] in ci.TEXT_EXTS | ci.JSON_EXTS | ci.PDF_EXTS else ".txt"
        mat_id = _ensure_material_id(counter, ext)
        text, meta = "", None
        note = None
        if f["ext"] in ci.PDF_EXTS:
            pr = ci.extract_pdf_text(f["path"], args.work)
            text, note = pr["text"], (None if pr["ok"] else "PDF解析失败: %s" % pr["error"])
            if not pr["ok"]:
                result["degradations"].append("PDF 不可读 %s: %s" % (f["rel"], pr["error"]))
        elif f["ext"] in ci.JSON_EXTS:
            jr = ci.load_json_safe(f["path"])
            if jr["obj"] is None:
                note = "JSON 解析失败: %s" % jr["error"]
                result["degradations"].append(note + " (%s)" % f["rel"])
            else:
                records = ci.json_records(jr["obj"])
                for ri, rec in enumerate(records):
                    rec_text = ci.record_text(rec)
                    sub_id = "%s__r%02d" % (mat_id, ri + 1) if len(records) > 1 else mat_id
                    _append_material(manifest, sub_id, f, rec_text, rec, None)
                continue
        else:
            rd = ci.read_text_safe(f["path"])
            text = rd["text"] or ""
            if rd["skipped"]:
                note = "读取跳过: %s" % rd["error"]
                result["degradations"].append(note + " (%s)" % f["rel"])
            elif rd["error"]:
                note = "解码降级: %s" % rd["error"]
        _append_material(manifest, mat_id, f, text, meta, note)

    # 写材料文本文件与 manifest
    for m in manifest["materials"]:
        if m.get("text"):
            out_path = os.path.join(args.work, m["locator_rel"])
            Path(out_path).write_text(m["text"], encoding="utf-8", errors="replace")
        m.pop("text", None)
    ci.write_json(os.path.join(args.work, "manifest.json"), manifest)
    result["sources"] = [m for m in manifest["materials"]]
    log_change(result, "prepare", "发现输入并落盘骨架", None, {"materials": len(manifest["materials"]), "skipped": skipped})
    ci.write_json(os.path.join(args.output, "result.json"), result)
    return result


def _append_material(manifest: dict, mat_id: str, f: dict, text: str, meta, note) -> None:
    locator_rel = ("materials/" + mat_id).replace("\\", "/")
    manifest["materials"].append({
        "doc_id": mat_id,
        "source_rel": f["rel"],
        "source_path": f["path"],
        "locator_rel": locator_rel,
        "ext": f["ext"],
        "text": text,
        "meta": meta,
        "note": note,
    })


def extract(args) -> dict:
    """确定性抽取：年份/术语/候选句/代表作候选，更新 result.json 与 CSV。"""
    result = load_result(args.output)
    if result is None:
        result = empty_skeleton("unknown", {}, [])
    mpath = os.path.join(args.work, "manifest.json")
    jr = ci.load_json_safe(mpath)
    manifest = jr["obj"] or {"materials": []}

    docs, distribution_counts, term_years, term_counts = [], Counter(), {}, Counter()
    gap_candidates, conflict_candidates, rep_candidates = [], [], []
    # 幂等：重建 machine_candidate，保留 Agent 已核验（非 machine_candidate）的证据
    ev = [e for e in (result.get("evidence_map") or []) if e.get("status") != "machine_candidate"]
    ev_start = len(ev)

    for m in manifest.get("materials", []):
        doc_id = m.get("doc_id", "doc")
        text = ""
        src = os.path.join(args.work, m.get("locator_rel", ""))
        if os.path.exists(src):
            rd = ci.read_text_safe(src)
            text = rd["text"] or ""
        meta = m.get("meta")
        yr = ci.record_year(text, meta)
        title = ci.record_title(text, meta)
        cited = ci.record_cited(meta)
        doc = {"doc_id": doc_id, "source_rel": m.get("source_rel"), "title": title,
               "year": yr["year"], "year_source": yr["source"], "year_confidence": yr["confidence"],
               "cited_by": cited}
        docs.append(doc)
        if yr["year"]:
            distribution_counts[yr["year"]] += 1
        terms = ci.tokenize_terms(text)
        for t in set(terms):
            term_counts[t] += 1
            if yr["year"]:
                term_years.setdefault(t, []).append(yr["year"])
        sentences = ci.split_sentences(text)
        gaps, conflicts = ci.extract_candidates(sentences)
        locator = (m.get("locator_rel") or "") 
        for g in gaps:
            eid = "E%03d" % (ev_start + len(ev) + 1)
            ev.append({"id": eid, "claim": "空白候选句（待 Agent 核验）",
                       "locator": "%s#L%d-L%d" % (locator, g["line_start"], g["line_end"]),
                       "quote": g["text"], "source_file": locator,
                       "status": "machine_candidate", "confidence": None, "kind": "gap_candidate"})
            gap_candidates.append({"doc_id": doc_id, "evidence_id": eid, "quote": g["text"],
                                   "locator": "%s#L%d-L%d" % (locator, g["line_start"], g["line_end"])})
        for c in conflicts:
            eid = "E%03d" % (ev_start + len(ev) + 1)
            ev.append({"id": eid, "claim": "冲突候选句（待 Agent 核验）",
                       "locator": "%s#L%d-L%d" % (locator, c["line_start"], c["line_end"]),
                       "quote": c["text"], "source_file": locator,
                       "status": "machine_candidate", "confidence": None, "kind": "conflict_candidate"})
            conflict_candidates.append({"doc_id": doc_id, "evidence_id": eid, "quote": c["text"],
                                        "locator": "%s#L%d-L%d" % (locator, c["line_start"], c["line_end"])})
        if title:
            rep_candidates.append({"doc_id": doc_id, "title": title, "year": yr["year"],
                                   "year_source": yr["source"], "cited_by": cited})

    distribution = [{"year": y, "count": c, "doc_ids": [d["doc_id"] for d in docs if d["year"] == y]}
                    for y, c in sorted(distribution_counts.items())]
    term_stats = [{"term": t, "doc_count": term_counts.get(t, 0),
                   "first_year": min(ys), "last_year": max(ys),
                   "years": sorted(set(ys))}
                  for t, ys in sorted(term_years.items(), key=lambda x: -len(set(x[1])))[:200]]

    result["time_trend"]["distribution"] = distribution
    if not result["time_trend"].get("unit_note"):
        result["time_trend"]["unit_note"] = "由 scripts/main.py 机械抽取；年份口径见 protocol.date_definition_note，待 Agent 确认"
    result["evidence_map"] = ev
    result["machine"] = {
        "baseline": True,
        "unverified": ["time_trend.claims", "topic_evolution", "representative_works", "research_gaps"],
        "gap_candidates": gap_candidates,
        "conflict_candidates": conflict_candidates,
        "rep_candidates": rep_candidates,
        "term_stats": term_stats,
        "document_count": len(docs),
    }
    result["status"] = "partial"
    if len(docs) == 0:
        result["status"] = "insufficient_evidence"
        if not any(x.startswith("无科研材料") for x in result.get("limitations", [])):
            result.setdefault("limitations", []).append("无科研材料，无法进行时间趋势/主题演化/代表作/研究空白分析")
    log_change(result, "extract", "机械基线完成", None,
               {"docs": len(docs), "distribution_points": len(distribution),
                "gap_candidates": len(gap_candidates), "conflict_candidates": len(conflict_candidates)})
    ci.write_json(os.path.join(args.output, "result.json"), result)

    # evidence_map.json（独立可读产物）
    ci.write_json(os.path.join(args.output, "evidence_map.json"),
                  {"case_id": result["case_id"], "entries": ev, "generated_by": "machine_baseline"})

    # tables
    os.makedirs(os.path.join(args.output, "tables"), exist_ok=True)
    ci.write_csv(os.path.join(args.output, "tables", "documents.csv"),
                 ["doc_id", "source_rel", "title", "year", "year_source", "year_confidence", "cited_by"], docs)
    ci.write_csv(os.path.join(args.output, "tables", "year_distribution.csv"),
                 ["year", "count", "doc_ids"], [{"year": d["year"], "count": d["count"],
                                                 "doc_ids": ";".join(d["doc_ids"])} for d in distribution])
    ci.write_csv(os.path.join(args.output, "tables", "term_first_last_year.csv"),
                 ["term", "doc_count", "first_year", "last_year", "years"],
                 [{"term": t["term"], "doc_count": t["doc_count"], "first_year": t["first_year"],
                   "last_year": t["last_year"], "years": ";".join(map(str, t["years"]))} for t in term_stats])

    # report.md 骨架（可编辑主产物；Agent 后续升级为四类结论）
    write_report_skeleton(args.output, result, docs, distribution, term_stats[:30],
                          gap_candidates, conflict_candidates, rep_candidates)
    return result


def write_report_skeleton(output_dir, result, docs, distribution, top_terms,
                          gap_candidates, conflict_candidates, rep_candidates) -> None:
    lines = []
    lines.append("# 研究前沿与趋势分析（机械基线，待 Agent 核验）")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`（scripts/main.py 机械基线；四类结论待深度分析升级）" % result.get("status"))
    lines.append("- 材料文档数: %d" % len(docs))
    lines.append("")
    lines.append("## 1. 时间趋势（年度分布，机械抽取）")
    lines.append("")
    lines.append("年份口径待写入 protocol.date_definition_note；下表为逐年年份计数，仅作拐点判断的候选支撑。")
    lines.append("")
    if distribution:
        lines.append("| year | count |")
        lines.append("|-|-|")
        for d in distribution:
            lines.append("| %s | %d |" % (d["year"], d["count"]))
    else:
        lines.append("无出版年份元数据，待人工（pending_human）。")
    lines.append("")
    lines.append("## 2. 主题演化（高频术语候选，首末年份）")
    lines.append("")
    if top_terms:
        for t in top_terms:
            lines.append("- `%s`：doc_count=%d，first=%s，last=%s" %
                         (t["term"], t["doc_count"], t["first_year"], t["last_year"]))
    else:
        lines.append("无术语候选。")
    lines.append("")
    lines.append("## 3. 代表性工作候选")
    lines.append("")
    if rep_candidates:
        for r in rep_candidates:
            lines.append("- `%s`（doc_id=%s，year=%s，year_source=%s，cited_by=%s）" %
                         (r.get("title"), r.get("doc_id"), r.get("year"), r.get("year_source"), r.get("cited_by")))
    else:
        lines.append("无候选。")
    lines.append("")
    lines.append("## 4. 研究空白 / 冲突候选句（待核验，未写排除证据前不算空白）")
    lines.append("")
    lines.append("- 空白候选句 %d 条；冲突候选句 %d 条。详见 result.json 的 machine 字段与 evidence_map（machine_candidate）。" %
                 (len(gap_candidates), len(conflict_candidates)))
    lines.append("")
    lines.append("## 5. 边界")
    lines.append("")
    lines.append("本文件为机械基线，所有语义判断（趋势/演化/代表作/空白）均未核验；未标注排除证据的“空白”仅为候选句，不等于研究空白。")
    Path(os.path.join(output_dir, "report.md")).write_text("\n".join(lines), encoding="utf-8")


def cmd_log(args) -> None:
    result = load_result(args.output)
    if result is None:
        result = empty_skeleton("unknown", {}, [])
    log_change(result, args.action, args.reason, args.before, args.after)
    ci.write_json(os.path.join(args.output, "result.json"), result)
    print("change_log 已追加: %s" % args.action)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="main.py", description="研究前沿趋势 Skill 确定性入口")
    ap.add_argument("--input", default=os.environ.get("SKILL_INPUT", DEFAULT_INPUT))
    ap.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", DEFAULT_OUTPUT))
    ap.add_argument("--work", default=os.environ.get("SKILL_WORK", DEFAULT_WORK))
    ap.add_argument("--force", action="store_true", help="忽略既有 manifest 重新抽取")
    ap.add_argument("step", nargs="?", default="all", choices=["all", "prepare", "extract", "log"])
    ap.add_argument("--action", default=None)
    ap.add_argument("--reason", default=None)
    ap.add_argument("--before", default=None)
    ap.add_argument("--after", default=None)
    args = ap.parse_args(argv)

    if args.step == "log":
        if not args.action or not args.reason:
            print("log 需要 --action 与 --reason", file=sys.stderr)
            return 2
        cmd_log(args)
        return 0

    if args.force:
        for f in ("manifest.json",):
            p = os.path.join(args.work, f)
            if os.path.exists(p):
                os.remove(p)

    if args.step in ("all", "prepare"):
        prepare(args)
    if args.step in ("all", "extract"):
        extract(args)
    print("done: step=%s input=%s output=%s" % (args.step, args.input, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
