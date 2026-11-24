#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Experiment parameter optimization Skill — orchestrator (standard library only).

Subcommands
    init      discover inputs, write /app/work/input_digest.json, plan.json skeleton
              and immediately finalize an /app/output/result.json skeleton
              (status=partial). This is the fail-safe: it must run first.
    finalize  merge /app/work/plan.json into /app/output/result.json, render
              editable /app/output/plan.md, refresh rubric_trace/audit artifacts.
    doe       deterministic candidate-point generation from plan.json
              (fixed seed, reproducible across runs).
    audit     recompute rubric audit only (delegates to validate_plan.RUBRIC).

Defaults: --input /input --output /app/output --work /app/work.
All paths may be overridden for local testing. Relative invocation from the Skill
root (/skills/weekly-skill) keeps every reference portable.
"""

import argparse
import json
import os
import random
import sys
import time

try:
    import validate_plan as vp  # same directory, only when audit is requested
except Exception:  # pragma: no cover - import happens lazily in audit()
    vp = None

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_INPUT_BYTES = 2 * 1024 * 1024
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
             ".xml", ".html", ".tex", ".rst", ".log", ".bib", ".jsonl"}

PLAN_CONTENT_KEYS = [
    "research_question", "objective", "assumptions", "variables",
    "parameters", "controls", "procedures", "resources",
    "response_metrics", "interpretation_criteria", "search_strategy",
    "cost_budget", "basis", "risks", "exception_handling", "confirmations",
]
PLAN_META_KEYS = ["title", "sources", "evidence_map", "uncertainty",
                  "limitations", "pending_human", "errors", "degradations",
                  "runtime", "change_log", "rubric_trace"]


def _reconfigure_streams():
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def read_text_safe(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def json_load_safe(path):
    raw = read_text_safe(path)
    if raw is None:
        return None, "cannot read file"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: %s" % exc
    except Exception as exc:  # pragma: no cover - defensive
        return None, "parse error: %s" % exc


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def discover_inputs(input_dir):
    found = []
    if not os.path.isdir(input_dir):
        return found
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            abs_path = os.path.join(root, name)
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                continue
            rel = os.path.relpath(abs_path, input_dir)
            if size > MAX_INPUT_BYTES:
                found.append((rel, abs_path, size, "skipped:too_large"))
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "text" if ext in TEXT_EXTS else "binary"
            found.append((rel, abs_path, size, kind))
    return found


def extract_case_id(case_obj):
    if isinstance(case_obj, dict):
        for key in ("case_id", "caseId", "case", "id", "challenge_id",
                    "challengeId", "armiId", "task_id"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def make_input_digest(input_dir):
    files = discover_inputs(input_dir)
    digest = {"input_dir": input_dir, "files": [], "case_id": None,
              "case_json_error": None, "task_md_preview": None,
              "materials": []}
    for rel, abs_path, size, kind in files:
        entry = {"path": rel, "size": size, "kind": kind}
        if kind == "text":
            text = read_text_safe(abs_path)
            if text is None:
                entry["readable"] = False
            else:
                entry["readable"] = True
                entry["preview"] = text[:2000]
        digest["files"].append(entry)

    for entry in digest["files"]:
        base = os.path.basename(entry["path"]).lower()
        full = os.path.join(input_dir, entry["path"])
        if base in ("case.json", "input.json"):
            obj, err = json_load_safe(full)
            digest["case_json_error"] = err
            if obj is not None:
                digest["case_id"] = extract_case_id(obj)
        if base in ("task.md", "task.txt", "readme.md"):
            text = read_text_safe(full)
            if text is not None:
                digest["task_md_preview"] = text[:2000]
        if base not in ("case.json", "input.json", "task.md", "task.txt",
                        "readme.md") and entry.get("kind") == "text":
            digest["materials"].append(entry["path"])
    return digest


def new_skeleton_plan(case_id):
    return {
        "case_id": case_id or "",
        "status": "partial",
        "title": "",
        "research_question": "",
        "objective": "",
        "assumptions": [],
        "variables": [],
        "parameters": [],
        "controls": [],
        "procedures": [],
        "resources": [],
        "response_metrics": [],
        "interpretation_criteria": [],
        "search_strategy": {
            "method": "", "initial_points": [], "per_round": "",
            "stop_criteria": [], "candidate_count": 8, "seed": 0,
        },
        "cost_budget": {
            "items": [], "total": "", "time_hours": None,
            "tradeoff_note": "", "acceptance_criteria": [],
        },
        "basis": [],
        "risks": [],
        "exception_handling": [],
        "confirmations": [],
        "sources": [],
        "evidence_map": {"schema_version": "2.0.0", "claims": [],
                         "conflict_count": 0, "unverified_count": 0},
        "uncertainty": {"unverified_values": [], "missing_data": [],
                        "assumption_risk": []},
        "limitations": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {"seconds": None, "started_at": utcnow(),
                    "finished_at": None, "model_requests": None},
        "change_log": [{"time": utcnow(), "entry": "骨架落盘"}],
        "rubric_trace": [],
    }


def read_plan(work):
    path = os.path.join(work, "plan.json")
    obj, err = json_load_safe(path)
    if err is not None:
        raise SystemExit("plan.json unreadable: %s" % err)
    return obj


def _fmt_val(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return ""
    return str(v)


def _md_block(title, seq, field_mapper):
    lines = ["## %s" % title]
    if not seq:
        lines.append("（无）")
        return lines
    if isinstance(seq, list) and seq and isinstance(seq[0], dict):
        keys = list(seq[0].keys())
        lines.append("| " + " | ".join(keys) + " |")
        lines.append("|" + "---|" * len(keys))
        for item in seq:
            cells = [_fmt_val(item.get(k, "")) for k in keys]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        for item in seq:
            lines.append("- " + _fmt_val(item))
    return lines


def render_plan_md(plan):
    lines = []
    lines.append("# 实验参数优化方案：%s" % (plan.get("title") or "（未命名）"))
    lines.append("")
    lines.append("- case_id: `%s`" % plan.get("case_id", ""))
    lines.append("- status: `%s`" % plan.get("status", ""))
    lines.append("")
    for key, label in [("research_question", "研究问题"),
                       ("objective", "优化目标")]:
        val = plan.get(key, "")
        lines.append("## %s\n\n%s\n" % (label, val or "（待补）"))
    for title, key, mapper in [
            ("假设", "assumptions", None),
            ("变量", "variables", None),
            ("关键参数", "parameters", None),
            ("对照", "controls", None),
            ("步骤", "procedures", None),
            ("资源", "resources", None),
            ("响应指标", "response_metrics", None),
            ("判读标准", "interpretation_criteria", None),
            ("依据", "basis", None),
            ("风险", "risks", None),
            ("异常处置", "exception_handling", None),
            ("确认项", "confirmations", None),
            ("不确定性", "uncertainty", None),
            ("限制", "limitations", None),
            ("待确认", "pending_human", None)]:
        val = plan.get(key, [])
        lines.extend(_md_block(title, val, mapper))
        lines.append("")
    ss = plan.get("search_strategy", {})
    lines.append("## 搜索策略")
    lines.append("- method: `%s`" % ss.get("method", ""))
    lines.append("- initial_points: %s" % _fmt_val(ss.get("initial_points", [])))
    lines.append("- per_round: %s" % _fmt_val(ss.get("per_round", "")))
    lines.append("- stop_criteria: %s" % _fmt_val(ss.get("stop_criteria", [])))
    lines.append("- candidate_count: %s, seed: %s" %
                 (ss.get("candidate_count", ""), ss.get("seed", "")))
    cb = plan.get("cost_budget", {})
    lines.append("\n## 成本预算")
    for k in ("items", "total", "time_hours", "tradeoff_note",
              "acceptance_criteria"):
        lines.append("- %s: %s" % (k, _fmt_val(cb.get(k, ""))))
    lines.append("\n## 变更记录")
    for cl in plan.get("change_log", []):
        lines.append("- %s | %s" % (cl.get("time", ""), cl.get("entry", "")))
    return "\n".join(lines) + "\n"


def cmd_init(args):
    ensure_dir(args.work)
    ensure_dir(args.output)
    digest = make_input_digest(args.input)
    case_id = digest.get("case_id")
    plan = new_skeleton_plan(case_id)
    write_json(os.path.join(args.work, "input_digest.json"), digest)
    write_json(os.path.join(args.work, "plan.json"), plan)
    write_json(os.path.join(args.work, "candidates.json"),
               {"case_id": case_id, "seed": 0, "candidates": [],
                "note": "candidates generated later by `doe`"})
    print(json.dumps({"stage": "init", "case_id": case_id,
                      "files": len(digest["files"]),
                      "plan": os.path.join(args.work, "plan.json")},
                     ensure_ascii=False))
    # Immediately write the partial skeleton to /app/output (fail-safe).
    cmd_finalize(args)


def merge_plan_to_result(plan, work, output):
    result = {
        "case_id": plan.get("case_id", ""),
        "status": plan.get("status", "partial"),
        "plan": plan,
        "artifacts": {
            "plan.md": os.path.abspath(os.path.join(output, "plan.md")),
            "plan.json": os.path.abspath(os.path.join(output, "plan.json")),
            "audit_report.json": os.path.abspath(
                os.path.join(output, "audit_report.json")),
        },
        "contract": {
            "schema_version": "2.0.0",
            "contract_source": "references/output_contract.md",
            "fields_covered": ["case_id", "status", "plan", "artifacts",
                               "sources", "evidence_map", "uncertainty",
                               "limitations", "pending_human", "change_log",
                               "errors", "degradations", "runtime",
                               "rubric_trace", "rubric_audit"],
        },
        "sources": plan.get("sources", []),
        "evidence_map": plan.get("evidence_map",
                                 {"schema_version": "2.0.0", "claims": [],
                                  "conflict_count": 0, "unverified_count": 0}),
        "uncertainty": plan.get("uncertainty", {}),
        "limitations": plan.get("limitations", []),
        "pending_human": plan.get("pending_human", []),
        "change_log": plan.get("change_log", []),
        "errors": plan.get("errors", []),
        "degradations": plan.get("degradations", []),
        "runtime": plan.get("runtime", {
            "seconds": None, "started_at": utcnow(),
            "finished_at": None, "model_requests": None}),
        "rubric_trace": plan.get("rubric_trace", []),
        "rubric_audit": plan.get("rubric_audit",
                                 {"pass": 0, "warn": 0, "fail": 0, "na": 0,
                                  "failures": []}),
    }
    return result


def cmd_finalize(args):
    plan = read_plan(args.work)
    result = merge_plan_to_result(plan, args.work, args.output)
    write_json(os.path.join(args.output, "plan.json"), plan)
    with open(os.path.join(args.output, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write(render_plan_md(plan))
    write_json(os.path.join(args.output, "result.json"), result)
    _try_audit(args)
    print(json.dumps({"stage": "finalize", "case_id": result["case_id"],
                      "status": result["status"],
                      "result": os.path.join(args.output, "result.json")},
                     ensure_ascii=False))


def _try_audit(args):
    try:
        cmd_audit(args)
    except Exception as exc:  # audit must never break finalize
        print("audit skipped: %s" % exc, file=sys.stderr)


def deterministic_candidates(plan, seed):
    params = [p for p in plan.get("parameters", []) if isinstance(p, dict)]
    ss = plan.get("search_strategy", {})
    count = int(ss.get("candidate_count", 8) or 8)
    rng = random.Random(int(seed))
    cands = []
    for idx, p in enumerate(params):
        fr = p.get("feasible_range") or {}
        lo = fr.get("min")
        hi = fr.get("max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            step = p.get("step")
            if not isinstance(step, (int, float)) or step <= 0:
                step = max((hi - lo) / max(count - 1, 1), 1e-9)
            val = lo
            for _ in range(min(count, 1000)):
                cands.append({"parameter": p.get("name", ""),
                              "unit": p.get("unit", ""),
                              "value": round(float(val), 9),
                              "feasible_range": {"min": lo, "max": hi},
                              "source_seed": seed, "index": idx})
                val += step
                if val > hi + 1e-12:
                    break
    # Deterministic ordering key: (index, value). Same input -> same output.
    cands.sort(key=lambda c: (c["index"], c["value"]))
    return cands


def cmd_doe(args):
    plan = read_plan(args.work)
    seed = args.seed if args.seed is not None else \
        plan.get("search_strategy", {}).get("seed", 0)
    cands = deterministic_candidates(plan, seed)
    out = {"case_id": plan.get("case_id", ""), "seed": seed,
           "method": plan.get("search_strategy", {}).get("method", ""),
           "candidate_count": len(cands), "candidates": cands,
           "generated_at": utcnow()}
    write_json(os.path.join(args.work, "candidates.json"), out)
    plan.setdefault("search_strategy", {})["seed"] = seed
    plan.setdefault("search_strategy", {})["candidate_count"] = len(cands)
    write_json(os.path.join(args.work, "plan.json"), plan)
    print(json.dumps({"stage": "doe", "seed": seed, "count": len(cands)},
                     ensure_ascii=False))


def cmd_audit(args):
    if vp is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "validate_plan", os.path.join(os.path.dirname(
                os.path.abspath(__file__)), "validate_plan.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    else:
        mod = vp
    result_path = os.path.join(args.output, "result.json")
    report_path = os.path.join(args.work, "audit_report.json")
    obj, err = json_load_safe(result_path)
    if err is not None:
        print("audit failed: %s" % err, file=sys.stderr)
        return
    report = mod.compute_rubric_audit(obj)
    write_json(report_path, report)
    write_json(os.path.join(args.output, "audit_report.json"), report)
    obj["rubric_audit"] = {
        "pass": report["summary"]["pass"], "warn": report["summary"]["warn"],
        "fail": report["summary"]["fail"], "na": report["summary"]["na"],
        "failures": report["failures"]}
    write_json(result_path, obj)
    print(json.dumps({"stage": "audit", "summary": report["summary"]},
                     ensure_ascii=False))


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["init", "finalize", "doe", "audit"])
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    p.add_argument("--seed", type=int, default=None)
    return p


def main(argv=None):
    _reconfigure_streams()
    args = build_parser().parse_args(argv)
    if args.command == "init":
        cmd_init(args)
    elif args.command == "finalize":
        cmd_finalize(args)
    elif args.command == "doe":
        cmd_doe(args)
    elif args.command == "audit":
        cmd_audit(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
