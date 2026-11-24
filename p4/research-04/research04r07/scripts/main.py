# -*- coding: utf-8 -*-
"""Entry point for the research-frontier-trend Skill (robustness variant).

Usage (all defaults target the runtime contract, but any local dir works):

    python3 -B scripts/main.py
    python3 -B scripts/main.py --input /input --output /app/output --work /app/work
    python3 -B scripts/main.py --help

The pipeline is intentionally "fail-safe": it writes a valid `result.json`
skeleton before any analysis, then only refines it.  Every phase is wrapped so
a crash in one phase cannot prevent a minimal valid deliverable.  Exit code is
0 for all recoverable situations; only a truly unknown CLI is treated as a
hard error (but even unknown flags are ignored with a warning).

Phases:
  1. discover    - scan input, resolve case.json / task.md, write skeleton
  2. extract    - extract material text (txt/md/json/csv, PDF via pdftotext)
  3. baseline   - deterministic year/term/candidate extraction -> artifacts
  4. selfcheck  - validate contract, enums and artifact completeness
  5. finalize   - set honest status, write final result.json

The LLM agent then upgrades the mechanical `machine=true` candidates to
verified claims per SKILL.md and re-runs selfcheck before declaring `ok`.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core_impl as core  # noqa: E402


def _reconfigure_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Robust research-frontier trend analysis pipeline (stdlib only).",
    )
    parser.add_argument("--input", default=core.DEFAULT_INPUT, help="input dir (default /input)")
    parser.add_argument("--output", default=core.DEFAULT_OUTPUT, help="output dir (default /app/output)")
    parser.add_argument("--work", default=core.DEFAULT_WORK, help="work dir (default /app/work)")
    parser.add_argument("--skip-selfcheck", action="store_true", help="skip final validation pass")
    # Unknown flags are tolerated for robustness; they are reported, not fatal.
    return parser.parse_known_args(argv)


def phase_discover(input_dir, output_dir, work_dir):
    """Phase 1: discover inputs and write the skeleton immediately."""
    result = {
        "manifest": core.discover_inputs(input_dir),
        "case_obj": None,
        "task_text": None,
        "case_id": "unknown",
        "output_dir": output_dir,
        "work_dir": work_dir,
    }
    man = result["manifest"]
    if man["case_path"]:
        obj, err = core.load_json(man["case_path"])
        result["case_obj"] = obj if isinstance(obj, dict) else None
        if err:
            result["manifest"]["notes"].append("case.json: %s" % err)
    if man["task_path"]:
        res = core.read_text_file(man["task_path"])
        if res["ok"]:
            result["task_text"] = res["text"]
        else:
            result["manifest"]["notes"].append("task.md: %s" % res["reason"])

    result["case_id"] = core.extract_case_id(result["case_obj"], result["task_text"], man)
    contract = (result["case_obj"] or {}).get("output_contract")
    result["skeleton"] = core.make_skeleton(
        result["case_id"], contract if isinstance(contract, dict) else None,
        input_dir, output_dir)
    if not isinstance(contract, dict):
        result["skeleton"]["errors"].append(
            "no output_contract in case.json; using fixed fields only")

    ok, err = core.write_json_atomic(
        os.path.join(output_dir, "result.json"), result["skeleton"])
    if not ok:
        result["skeleton"]["degradations"].append("skeleton write: %s" % err)
    # Also persist the discovery manifest for reproducibility / recovery.
    man_dump = {
        "exists": man["exists"],
        "case_path": man["case_path"],
        "task_path": man["task_path"],
        "files": [{"rel": f["rel"], "kind": f["kind"], "size": f["size"]} for f in man["files"]],
        "notes": man["notes"],
    }
    core.write_json_atomic(os.path.join(work_dir, "manifest.json"), man_dump)
    core.log_change(result["skeleton"], "skeleton", "first partial deliverable",
                    before=None, after="result.json skeleton written")
    result["skeleton"]["runtime"]["phases"]["discover"] = core.utcnow_iso()
    return result


def phase_extract(discovery, work_dir, output_dir):
    """Phase 2: extract text for every material, recording degradations."""
    skeleton = discovery["skeleton"]
    docs = []
    for entry in discovery["manifest"]["files"]:
        got = core.extract_material_text(entry, work_dir)
        doc = {
            "entry": entry,
            "text": got["text"],
            "method": got["method"],
            "error": got["error"],
        }
        docs.append(doc)
        for note in got["notes"]:
            skeleton["degradations"].append(note)
        if got["text"] is None:
            skeleton["limitations"].append("%s: no extractable text (%s)" % (
                entry["rel"], got["error"] or "unknown"))
    if not discovery["manifest"]["exists"]:
        skeleton["limitations"].append("input directory not found: %s" %
                                       discovery["manifest"].get("_dir", ""))
    skeleton["runtime"]["phases"]["extract"] = core.utcnow_iso()
    return docs


def phase_baseline(docs, discovery, output_dir, work_dir):
    """Phase 3: deterministic mechanical baseline -> result.json + artifacts."""
    skeleton = discovery["skeleton"]
    man = discovery["manifest"]

    year_counts = {}
    year_docids = {}
    term_first_last = {}
    documents = []
    evidence = []
    gap_candidates = []
    conflict_candidates = []
    works_by_year = {}

    for i, doc in enumerate(docs):
        entry = doc["entry"]
        did = "doc_%03d" % (i + 1)
        text = doc["text"] or ""
        years = core.extract_years(text)
        # Prefer a metadata/header year (first 1500 chars) over body years.
        head_years = [y for y, _ in core.extract_years(text[:1500])]
        doc_year = head_years[-1] if head_years else (years[-1][0] if years else None)

        documents.append({
            "doc_id": did,
            "file": entry["rel"],
            "title": core.title_guess(entry, text),
            "year": doc_year,
            "kind_hint": entry["kind"],
            "text_bytes": entry["size"],
            "method": doc["method"],
            "extractable": bool(text),
        })

        if doc_year is not None:
            year_counts[doc_year] = year_counts.get(doc_year, 0) + 1
            year_docids.setdefault(doc_year, []).append(did)
            works_by_year.setdefault(doc_year, []).append({
                "doc_id": did, "title": core.title_guess(entry, text),
                "year": doc_year, "kind": None,
                "rationale": "machine candidate, unverified",
            })

        for item in core.extract_terms(text):
            term = item["term"]
            cnt = item["count"]
            rec = term_first_last.setdefault(term, {"count": 0, "first": None, "last": None})
            rec["count"] += cnt
            rec["first"] = doc_year if (rec["first"] is None or (doc_year is not None and doc_year < rec["first"])) else rec["first"]
            rec["last"] = doc_year if (rec["last"] is None or (doc_year is not None and doc_year > rec["last"])) else rec["last"]

        for cand in core.find_candidate_sentences(text, core.FUTURE_PATTERNS):
            span = core.line_span(text, cand["sentence"])
            eid = "E%03d" % (len(evidence) + 1)
            evidence.append({
                "id": eid, "claim": "候选研究空白句（未核验）",
                "locator": ("%s#%s" % (entry["rel"], span)) if span else entry["rel"],
                "quote": cand["sentence"][:core.MAX_QUOTE_LEN],
                "source_file": entry["rel"],
                "status": "not_reported",
                "confidence": "low",
                "machine": True,
                "note": "pattern hit: %s" % cand["pattern_hit"],
            })
            gap_candidates.append({"id": "G%03d" % (len(gap_candidates) + 1),
                                   "type": "open_question",
                                   "description": "候选空白句，待核验：%s" % cand["sentence"][:160],
                                   "exclusion_evidence": None,
                                   "evidence_ids": [eid],
                                   "machine": True})

        for cand in core.find_candidate_sentences(text, core.CONFLICT_PATTERNS):
            span = core.line_span(text, cand["sentence"])
            eid = "E%03d" % (len(evidence) + 1)
            evidence.append({
                "id": eid, "claim": "候选冲突句（未核验）",
                "locator": ("%s#%s" % (entry["rel"], span)) if span else entry["rel"],
                "quote": cand["sentence"][:core.MAX_QUOTE_LEN],
                "source_file": entry["rel"],
                "status": "not_reported",
                "confidence": "low",
                "machine": True,
                "note": "pattern hit: %s" % cand["pattern_hit"],
            })
            conflict_candidates.append({
                "id": "C%03d" % (len(conflict_candidates) + 1),
                "left": {"quote": cand["sentence"][:200], "locator": entry["rel"]},
                "right": None,
                "resolution": None,
                "evidence_ids": [eid],
                "machine": True,
            })

    distribution = [{"year": int(y), "count": year_counts[y],
                     "doc_ids": sorted(year_docids.get(y, []))}
                    for y in sorted(year_counts)]
    term_mapping = [{"canonical": t, "variants": [], "first_year": rec["first"],
                     "last_year": rec["last"], "count": rec["count"], "machine": True}
                    for t, rec in sorted(term_first_last.items(),
                                         key=lambda kv: -kv[1]["count"])[:core.MAX_TERMS]]
    stages = sorted(works_by_year.items())

    skeleton["time_trend"]["distribution"] = distribution
    skeleton["time_trend"]["unit_note"] = (
        "年份为机器抽取（题录/正文首个年份优先），尚未人工归一；"
        "在线发表/见刊/预印本口径待案例元数据确认")
    skeleton["topic_evolution"]["term_mapping"] = term_mapping
    skeleton["representative_works"]["criteria"] = [
        "覆盖各阶段（按年份分桶）", "method/origin 长尾工作优先于总被引",
        "每个阶段至少一个候选", "kind 待人工核验（machine=true）"]
    skeleton["representative_works"]["stages"] = [
        {"stage": "stage_%d" % (i + 1), "period": "%s" % y,
         "works": [w for w in works], "basis": "machine year bucket, unverified"}
        for i, (y, works) in enumerate(stages)]
    skeleton["research_gaps"]["gaps"] = gap_candidates
    skeleton["conflicts"]["items"] = conflict_candidates
    skeleton["evidence_map"] = evidence
    skeleton["sources"] = [{
        "source_id": d["doc_id"], "file": d["file"], "title": d["title"],
        "year": d["year"], "method": d["method"],
        "included": bool(d["extractable"]),
    } for d in documents]

    if not docs:
        skeleton["limitations"].append("no material files found; four artifact families left empty")
    if not distribution:
        skeleton["limitations"].append("no usable year metadata; time_trend.distribution empty")
    if not evidence:
        skeleton["limitations"].append("no candidate sentences matched; evidence_map empty")

    # Write derived artifacts.
    core.write_text_atomic(os.path.join(output_dir, "evidence_map.json"),
                           json_dumps(evidence))
    ok1, e1 = core.write_csv_table(os.path.join(output_dir, "tables", "documents.csv"),
                                   ["doc_id", "file", "title", "year", "kind_hint", "extractable"],
                                   [[d["doc_id"], d["file"], d["title"], d["year"],
                                     d["kind_hint"], d["extractable"]] for d in documents])
    ok2, e2 = core.write_csv_table(os.path.join(output_dir, "tables", "year_distribution.csv"),
                                   ["year", "count", "doc_ids"],
                                   [[d["year"], d["count"], ";".join(d["doc_ids"])] for d in distribution])
    if not ok1:
        skeleton["degradations"].append("documents.csv: %s" % e1)
    if not ok2:
        skeleton["degradations"].append("year_distribution.csv: %s" % e2)

    report = build_report(skeleton, documents, distribution)
    okr, er = core.write_text_atomic(os.path.join(output_dir, "report.md"), report)
    if not okr:
        skeleton["degradations"].append("report.md: %s" % er)

    core.log_change(skeleton, "baseline", "deterministic mechanical extraction",
                    before="skeleton", after="distribution/term_mapping/candidates written")
    skeleton["runtime"]["phases"]["baseline"] = core.utcnow_iso()
    return documents, distribution


def json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)


def build_report(skeleton, documents, distribution):
    lines = [
        "# 研究前沿与趋势分析 —— 机械基线报告（machine=true，待人工核验）",
        "",
        "- case_id: `%s`" % skeleton.get("case_id"),
        "- status: `%s`" % skeleton.get("status"),
        "- 生成时间: `%s`" % core.utcnow_iso(),
        "",
        "> 本报告由脚本确定性生成，仅提供候选证据与统计表；结论须由 Agent 逐条核验后方可去掉 machine 标记。",
        "",
        "## 1. 年度分布（候选，年份未归一）",
        "",
        "| year | count | doc_ids |",
        "|-|-|-|",
    ]
    if distribution:
        for d in distribution:
            lines.append("| %s | %s | %s |" % (d["year"], d["count"], ", ".join(d["doc_ids"])))
    else:
        lines.append("| - | 0 | (no year metadata) |")
    lines += [
        "",
        "## 2. 文档清单",
        "",
        "| doc_id | file | year | extractable |",
        "|-|-|-|-|",
    ]
    for d in documents:
        lines.append("| %s | %s | %s | %s |" % (d["doc_id"], d["file"], d["year"], d["extractable"]))
    lines += [
        "",
        "## 3. 候选证据（quote 为逐字引用，status 待核验）",
        "",
    ]
    for ev in skeleton.get("evidence_map", []):
        lines.append("- `%s` %s: 「%s」" % (ev["id"], ev["source_file"], ev["quote"][:120]))
    lines += [
        "",
        "## 4. 说明",
        "",
        "- 年份抽取优先级：题录/正文前 1500 字符 > 全文出现年份；口径待确认。",
        "- 术语为原始词频，同义/变体归并需 Agent 完成后写入 term_mapping。",
        "- 空白与冲突均为模式匹配候选，未做排除证据；`coverage_absence` 与 `open_question` 待区分。",
        "",
    ]
    return "\n".join(lines)


def phase_selfcheck(output_dir, discovery):
    """Phase 4: validate the result.json and record failures honestly."""
    skeleton = discovery["skeleton"]
    obj, err = core.load_json(os.path.join(output_dir, "result.json"),
                              max_bytes=core.RESULT_MAX_BYTES)
    if err is not None:
        skeleton["errors"].append("result.json unreadable during selfcheck: %s" % err)
        return []
    checks = core.validate_result(obj)
    fails = core.hard_failures(checks)
    for c in fails:
        skeleton["errors"].append("selfcheck FAIL: %s (%s)" % (c["check"], c["detail"]))
    if not fails:
        skeleton["limitations"].append("selfcheck passed structural checks; claims still need agent verification")
    skeleton["runtime"]["phases"]["selfcheck"] = core.utcnow_iso()
    return checks


def phase_finalize(discovery, output_dir):
    """Phase 5: set honest status and write final result.json."""
    skeleton = discovery["skeleton"]
    man = discovery["manifest"]

    if not man["exists"] or not man["files"]:
        skeleton["status"] = "insufficient_evidence"
    elif man["case_path"] and skeleton.get("case_id") == "unknown" and man["case_obj"] is None:
        # case.json present but unreadable and no fallback id
        skeleton["status"] = "input_error"
    elif not skeleton["time_trend"]["distribution"] and not skeleton["evidence_map"]:
        skeleton["status"] = "insufficient_evidence"
    else:
        skeleton["status"] = "partial"  # agent upgrades to ok after verification

    core.log_change(skeleton, "finalize", "honest status set from mechanical evidence",
                    before="partial", after=skeleton["status"])
    ok, err = core.write_json_atomic(os.path.join(output_dir, "result.json"), skeleton)
    if not ok:
        # Last-resort write into the work dir so a valid deliverable still exists.
        fallback = os.path.join(discovery["work_dir"], "result.fallback.json")
        core.write_json_atomic(fallback, skeleton)
        skeleton["degradations"].append("final result.json write failed (%s); fallback at %s" % (err, fallback))
    return skeleton


def run_pipeline(input_dir, output_dir, work_dir, skip_selfcheck=False):
    """Full pipeline; returns the final result dict. Never raises."""
    out_dir, deg_out, note_out = core.ensure_dir(output_dir, "output")
    wk_dir, deg_wk, note_wk = core.ensure_dir(work_dir, "work")

    discovery = phase_discover(input_dir, out_dir, wk_dir)
    skeleton = discovery["skeleton"]
    if note_out:
        skeleton["degradations"].append(note_out)
    if note_wk:
        skeleton["degradations"].append(note_wk)

    docs = phase_extract(discovery, wk_dir, out_dir)
    phase_baseline(docs, discovery, out_dir, wk_dir)
    if not skip_selfcheck:
        phase_selfcheck(out_dir, discovery)
    final = phase_finalize(discovery, out_dir)
    return final


def main(argv=None):
    _reconfigure_stdout()
    args, unknown = parse_args(argv)
    if unknown:
        print("[warn] ignored unknown flags: %s" % " ".join(unknown), file=sys.stderr)
    try:
        final = run_pipeline(args.input, args.output, args.work,
                             skip_selfcheck=args.skip_selfcheck)
        print("case_id=%s status=%s" % (final.get("case_id"), final.get("status")))
        return 0
    except Exception as exc:  # noqa: BLE001 - absolute last resort
        print("[error] pipeline crashed: %s" % exc, file=sys.stderr)
        # Try to write a minimal valid result somewhere before exiting.
        minimal = core.make_skeleton("unknown", None, args.input, args.output)
        minimal["status"] = "input_error"
        minimal["errors"].append("pipeline crash: %s" % exc)
        minimal["limitations"].append("minimal fallback result; see errors")
        out_dir, _, _ = core.ensure_dir(args.output, "output")
        core.write_json_atomic(os.path.join(out_dir, "result.json"), minimal)
        return 1


if __name__ == "__main__":
    sys.exit(main())
