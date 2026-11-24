"""Entry point for frontier-trend-review Skill.

Stages:
  prepare   scan /input, parse case.json + task.md, write result.json skeleton
  analyze   deterministic baseline over extracted materials
  selfcheck delegate to selftest.py

Defaults: --input /input --output /app/output --work /app/work
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core  # noqa: E402

STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
SUPPORT_ENUM = ("reported", "not_reported", "inferred")
EVIDENCE_STATUS_ENUM = ("reported", "not_reported", "source_conflict", "inferred")
EVENT_ENUM = ("emergence", "merge", "split", "decline", "terminology_drift")
WORK_KIND_ENUM = ("origin", "method", "milestone", "benchmark")
GAP_ENUM = ("open_question", "method_failure", "unresolved_conflict", "coverage_absence")


def parse_args(argv):
    p = argparse.ArgumentParser(description="frontier-trend-review runner")
    p.add_argument("stage", choices=("prepare", "analyze", "selfcheck"))
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    p.add_argument("--force", action="store_true")
    return p.parse_args(argv)


def empty_result():
    return {
        "case_id": "unknown",
        "status": "partial",
        "research_question": {"question": "", "scope": "", "sub_questions": [], "domain_note": ""},
        "protocol": {"inclusion_criteria": [], "exclusion_criteria": [], "search_scope": "", "date_definition_note": ""},
        "time_trend": {"unit_note": "", "distribution": [], "windows": [], "claims": []},
        "topic_evolution": {"stages": [], "term_mapping": [], "events": [], "claims": []},
        "representative_works": {"criteria": [], "stages": []},
        "research_gaps": {"gaps": [], "excluded_not_gaps": []},
        "evidence_map": [],
        "conflicts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": core.now_iso(), "finished_at": None, "elapsed_seconds": 0, "stages": []},
    }


def apply_output_contract(result, contract):
    """Merge every field declared by output_contract into result at top level.

    Contract keys are preserved verbatim; missing values become null (lists/dicts
    kept as-is only when they are literal values in the contract).
    """
    result["_output_contract"] = contract
    if isinstance(contract, dict):
        for k, v in contract.items():
            if k in result:
                continue
            if isinstance(v, (list, dict)):
                result[k] = v
            else:
                result[k] = None
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str) and item not in result:
                result[item] = None
            elif isinstance(item, dict):
                for k in item.keys():
                    if k not in result:
                        result[k] = None
    return result


def case_id_of(case, task_text):
    if isinstance(case, dict):
        for key in ("case_id", "id", "challenge_id"):
            v = case.get(key)
            if v:
                return str(v)
    if task_text:
        m = re.search(r"challenge[_\-]?id\s*[:：`]\s*`?([A-Za-z0-9\-_]+)`?", task_text, re.IGNORECASE)
        if m:
            return m.group(1)
    return "unknown"


def log_change(result, action, reason, before=None, after=None):
    result.setdefault("change_log", []).append({
        "time": core.now_iso(), "action": action, "reason": reason,
        "before": before, "after": after,
    })


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_or_empty(path):
    data, err = core.load_json(path)
    return data, err


def read_material_files(input_dir, work_dir):
    """Scan /input and extract text into /app/work/materials/. Returns materials."""
    files = core.scan_files(input_dir)
    materials = []
    os.makedirs(os.path.join(work_dir, "materials"), exist_ok=True)
    too_many = len(files) > core.MAX_FILES
    files = files[:core.MAX_FILES]
    for i, full in enumerate(files):
        mid = core.make_material_id(i)
        name = os.path.basename(full)
        if name.lower() in ("case.json", "task.md"):
            continue
        path_rel = core.relpath(full, input_dir)
        text, err = None, "unknown"
        kind = "other"
        if core.is_text_like(name):
            kind = "text"
            text, err = core.read_text(full)
        elif core.is_pdf(name):
            kind = "pdf"
            text, err = core.pdftotext(full)
            if text is None:
                err = "needs_pdf_tool: " + err
        else:
            err = "skipped_non_text"
        out_path = None
        if text:
            out_path = os.path.join(work_dir, "materials", mid + ".txt")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)
        year = core.extract_year(text, name) if text else None
        title = core.extract_title(text, name) if text else None
        cites = core.extract_citations(text) if text else None
        materials.append(core.build_doc_record(
            mid, path_rel, path_rel, text, err, year, title, cites))
        materials[-1]["kind"] = kind
        materials[-1]["text_file"] = core.relpath(out_path, work_dir) if out_path else None
    return materials, {"truncated": too_many, "total_seen": len(core.scan_files(input_dir))}


def stage_prepare(args):
    work_dir, output_dir, input_dir = args.work, args.output, args.input
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    case_path = os.path.join(input_dir, "case.json")
    task_path = os.path.join(input_dir, "task.md")
    case, case_err = load_or_empty(case_path)
    task_text, task_err = core.read_text(task_path) if os.path.isfile(task_path) else (None, "missing")

    result = empty_result()
    result["case_id"] = case_id_of(case or {}, task_text or "")
    contract = None
    if isinstance(case, dict):
        contract = case.get("output_contract") or case.get("outputContract")
    apply_output_contract(result, contract)

    materials, scan_note = read_material_files(input_dir, work_dir)
    result["sources"] = [
        {
            "id": m["id"], "source_file": m["source_file"], "kind": m["kind"],
            "year": m["year"], "title": m["title"], "citations": m["citations"],
            "char_count": m["char_count"], "error": m["error"], "text_file": m["text_file"],
        }
        for m in materials
    ]
    if case_err and case_err not in ("ok", "degraded", "missing"):
        result["errors"].append("case.json: %s" % case_err)
    if task_err and task_err not in ("ok", "degraded", "missing"):
        result["errors"].append("task.md: %s" % task_err)
    case_ok = case_err in ("ok", "degraded")
    task_ok = task_err in ("ok", "degraded")
    if not case_ok and not task_ok:
        result["status"] = "input_error"
        result["limitations"].append("case.json 与 task.md 均缺失或不可解析，无法确定任务")
    elif not materials:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("未发现可解析材料，无法区分研究空白与检索遗漏")
    else:
        result["status"] = "partial"
    for m in materials:
        if m["error"] and m["error"] not in ("ok", "empty", "degraded"):
            result["degradations"].append("%s: %s" % (m["source_file"], m["error"]))
    if scan_note["truncated"]:
        result["degradations"].append(
            "file count over limit %d; sampled first %d" % (core.MAX_FILES, len(materials)))

    manifest = {
        "case_id": result["case_id"],
        "output_contract": contract,
        "materials": materials,
        "scan_note": scan_note,
        "generated_at": core.now_iso(),
    }
    write_json(os.path.join(work_dir, "manifest.json"), manifest)

    log_change(result, "prepare", "skeleton written", before=None, after="partial skeleton")
    result["runtime"]["stages"].append({"stage": "prepare", "status": "ok", "at": core.now_iso()})
    write_json(os.path.join(output_dir, "result.json"), result)
    print("prepare ok: %d materials, case_id=%s, status=%s" % (
        len(materials), result["case_id"], result["status"]))
    return 0


def stage_analyze(args):
    work_dir, output_dir = args.work, args.output
    manifest_path = os.path.join(work_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        print("analyze: manifest.json missing; run prepare first")
        return 1
    manifest, _ = load_or_empty(manifest_path)
    result_path = os.path.join(output_dir, "result.json")
    result, rerr = load_or_empty(result_path)
    if rerr not in ("ok", "degraded") or not isinstance(result, dict):
        result = empty_result()

    materials = manifest.get("materials", [])
    mat_dir = os.path.join(work_dir, "materials")
    existing_texts = {}
    if os.path.isdir(mat_dir):
        for name in sorted(os.listdir(mat_dir)):
            if name.endswith(".txt"):
                existing_texts[name[:-4]] = os.path.join(mat_dir, name)

    docs = []
    for m in materials:
        text = None
        cand = existing_texts.get(m.get("id"))
        if cand and os.path.isfile(cand):
            text, _ = core.read_text(cand)
        elif m.get("text") is not None:
            text = m.get("text")
        if text is None and not m.get("error"):
            m["error"] = "no_text"
        docs.append({
            "id": m["id"], "source_file": m["source_file"], "text": text or "",
            "year": m.get("year"), "title": m.get("title"), "citations": m.get("citations"),
        })

    # 1. year distribution
    dist = {}
    for d in docs:
        y = d["year"]
        if y is None:
            continue
        dist.setdefault(y, []).append(d["id"])
    distribution = [
        {"year": int(y), "count": len(ids), "doc_ids": sorted(ids)}
        for y, ids in sorted(dist.items())
    ]
    unit_note = "机器基线：按各材料 metadata/正文首现年份统计；年份来源见 sources；未下趋势结论"

    # 2. term frequency -> term_mapping
    term_rows = core.term_frequency(docs)
    term_mapping = [
        {"canonical": r["term"], "variants": [], "first_year": r["first_year"],
         "last_year": r["last_year"], "count": r["count"], "docs": r["docs"], "machine": True}
        for r in term_rows
    ]

    # 3. evidence map + gap / conflict candidates
    evidence = []
    gaps = []
    conflicts = []
    eid = 0
    for d in docs:
        text = d["text"]
        if not text:
            continue
        loc_base = d["source_file"] if d["source_file"] else d["id"]
        for item in core.find_matches(text, core.GAP_PATTERNS):
            eid += 1
            ev = {
                "id": "E%03d" % eid, "claim": "文献自称存在未解决问题/限制/未来工作（候选）",
                "locator": "%s#L%d-L%d" % (loc_base, item["line_start"], item["line_end"]),
                "quote": item["sentence"], "source_file": d["source_file"],
                "status": "reported", "confidence": "low", "machine": True, "doc_id": d["id"],
            }
            evidence.append(ev)
            gaps.append({
                "id": "gap_%02d" % (len(gaps) + 1), "type": "open_question",
                "description": item["sentence"][:200], "exclusion_evidence": "",
                "evidence_ids": [ev["id"]], "machine": True,
            })
        for item in core.find_matches(text, core.CONFLICT_PATTERNS):
            eid += 1
            ev = {
                "id": "E%03d" % eid, "claim": "材料内出现对比/冲突/争议表述（候选）",
                "locator": "%s#L%d-L%d" % (loc_base, item["line_start"], item["line_end"]),
                "quote": item["sentence"], "source_file": d["source_file"],
                "status": "reported", "confidence": "low", "machine": True, "doc_id": d["id"],
            }
            evidence.append(ev)
            conflicts.append({"evidence_ids": [ev["id"]], "description": item["sentence"][:200], "machine": True})

    # 4. representative works candidates by kind
    work_buckets = {k: [] for k in WORK_KIND_ENUM}
    for d in docs:
        if not d["text"]:
            continue
        kinds = core.classify_kind(d["text"])
        for k in kinds:
            if len(work_buckets[k]) < 8:
                work_buckets[k].append({
                    "doc_id": d["id"], "title": d["title"], "year": d["year"],
                    "rationale": "机器候选：正文含 %s 类关键词" % k, "kind": k, "machine": True,
                })

    # 5. merge into result
    result["time_trend"]["unit_note"] = unit_note
    result["time_trend"]["distribution"] = distribution
    result["time_trend"]["claims"] = result.get("time_trend", {}).get("claims") or []
    result["topic_evolution"]["term_mapping"] = (
        result.get("topic_evolution", {}).get("term_mapping") or term_mapping)
    result["research_gaps"]["gaps"] = (
        result.get("research_gaps", {}).get("gaps") or gaps)
    result["conflicts"] = (result.get("conflicts") or {"items": [], "resolution": ""})
    if isinstance(result["conflicts"], list):
        result["conflicts"] = {"items": [], "resolution": ""}
    result["conflicts"]["items"] = result["conflicts"].get("items") or []
    for c in conflicts:
        if c not in result["conflicts"]["items"]:
            result["conflicts"]["items"].append(c)
    if not evidence:
        evidence = result.get("evidence_map") or []
    result["evidence_map"] = evidence

    # keep manual additions if already present (idempotent re-run)
    existing = {w["doc_id"] + w["kind"]: w for s in result.get("representative_works", {}).get("stages", []) for w in s.get("works", [])}
    stages = result.get("representative_works", {}).get("stages") or []
    if not stages:
        stages = [{"stage": "machine_candidates", "period": "all", "basis": "关键词分类候选，待核验", "works": []}]
    seen_keys = set(existing.keys())
    for k, works in work_buckets.items():
        for w in works:
            key = w["doc_id"] + w["kind"]
            if key not in seen_keys:
                stages[0]["works"].append(w)
                seen_keys.add(key)
    result["representative_works"]["stages"] = stages
    result["representative_works"]["criteria"] = (
        result.get("representative_works", {}).get("criteria")
        or ["覆盖各阶段", "不以被引排序为准", "含长尾方法首创", "kind: origin/method/milestone/benchmark"])

    # 6. write deliverables
    report = render_report(result, docs, distribution, term_mapping, gaps, conflicts)
    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    write_json(os.path.join(output_dir, "evidence_map.json"), evidence)
    write_tables(output_dir, docs, distribution)

    log_change(result, "analyze", "mechanical baseline computed", before=None, after="machine baseline")
    result["runtime"]["stages"].append({"stage": "analyze", "status": "ok", "at": core.now_iso()})
    write_json(os.path.join(output_dir, "result.json"), result)
    print("analyze ok: %d docs, %d evidence, %d gaps, %d conflicts" % (
        len(docs), len(evidence), len(gaps), len(conflicts)))
    return 0


def render_report(result, docs, distribution, term_mapping, gaps, conflicts):
    lines = ["# 研究前沿与趋势分析（机器基线）", "", "> 本报告为确定性脚本生成的机械基线；深度结论需按 SKILL.md 第 4 步核验后写入。", ""]
    lines.append("## 案例")
    lines.append("- case_id: %s" % result.get("case_id"))
    lines.append("- status: %s" % result.get("status"))
    lines.append("- 材料数: %d" % len(docs))
    lines.append("")
    lines.append("## 1. 时间趋势（年度分布）")
    if distribution:
        lines.append("| year | count | doc_ids |")
        lines.append("|-|-|-|")
        for row in distribution:
            lines.append("| %s | %d | %s |" % (row["year"], row["count"], ", ".join(row["doc_ids"])))
    else:
        lines.append("无出版年份元数据，见 unit_note。")
    lines.append("")
    lines.append("## 2. 主题演化（术语频率机器候选）")
    if term_mapping:
        lines.append("| term | count | first_year | last_year |")
        lines.append("|-|-|-|-|")
        for r in term_mapping[:30]:
            lines.append("| %s | %d | %s | %s |" % (r["canonical"], r["count"], r["first_year"], r["last_year"]))
    else:
        lines.append("暂无候选术语。")
    lines.append("")
    lines.append("## 3. 研究空白（候选句）")
    for g in gaps:
        lines.append("- %s" % g["description"])
    lines.append("")
    lines.append("## 4. 冲突（候选句）")
    for c in conflicts:
        lines.append("- %s" % c["description"])
    lines.append("")
    lines.append("## 证据边界")
    lines.append("所有候选均标注 machine=true，需人工/Agent 核验后方可作为结论。")
    return "\n".join(lines) + "\n"


def write_tables(output_dir, docs, distribution):
    tdir = os.path.join(output_dir, "tables")
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, "documents.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("id,source_file,year,title,citations,char_count,error\n")
        for d in docs:
            title = (d.get("title") or "").replace(",", " ").replace('"', "'")
            err = (d.get("error") or "ok").replace(",", " ")
            f.write("%s,%s,%s,\"%s\",%s,%d,%s\n" % (
                d["id"], d["source_file"], d.get("year") or "", title,
                d.get("citations") or "", len(d.get("text") or ""), err))
    with open(os.path.join(tdir, "year_distribution.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write("year,count,doc_ids\n")
        for row in distribution:
            f.write("%s,%d,\"%s\"\n" % (row["year"], row["count"], ";".join(row["doc_ids"])))


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.stage == "prepare":
        return stage_prepare(args)
    if args.stage == "analyze":
        return stage_analyze(args)
    if args.stage == "selfcheck":
        here = os.path.dirname(os.path.abspath(__file__))
        selftest = os.path.join(here, "selftest.py")
        import subprocess
        proc = subprocess.run([sys.executable, "-B", selftest, "--input", args.input,
                               "--output", args.output, "--work", args.work])
        return proc.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
