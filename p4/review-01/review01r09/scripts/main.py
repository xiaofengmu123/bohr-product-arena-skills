#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 主入口（仅 Python 标准库）。

子命令（Agent 按 SKILL.md 顺序调用）：
  init      扫描 /input，建索引，立刻落盘 draft.json 模板 + 合法 partial result.json
  show      打印带 locator 的原文，供 Agent 逐字引用
  check     校验 /app/work/draft.json（quote 命中 / 字段 / 枚举 / 覆盖）
  build     读取 draft.json，生成 review_report.md + 最终 result.json
  selfcheck 只复检最终 result.json 与 review_report.md，不重做

默认路径与评测环境一致：/input、/app/work、/app/output；
本地可用 --input/--work/--output 覆盖。所有子命令退出码恒为 0。
解析/抽取逻辑在 core_impl.py；本文件只做扫描、装配、渲染与命令分派。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from core_impl import (
    FACETS, SEVERITIES, ISSUE_TYPES, VERDICTS, CONFIDENCES, STATUSES,
    FINDING_KEYS, read_text, normalize, classify_kind, extract_or_reuse,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# 基础 IO
# --------------------------------------------------------------------------- #
def _ts(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _out_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    try:
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(path):
            os.remove(path)
        shutil.copyfile(tmp, path)
        os.remove(tmp)
    return path


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def _load_json(path):
    if not path.exists():
        return None
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _is_relative(path, base):
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _uniq(items):
    seen = set()
    out = []
    for x in items:
        key = json.dumps(x, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


# --------------------------------------------------------------------------- #
# 输入扫描
# --------------------------------------------------------------------------- #
def _scan_input(input_dir, work, rescan):
    state = {"case_id": None, "task_instruction": "", "output_contract": None,
             "contract_keys": [], "degradations": [], "errors": []}
    case_path = input_dir / "case.json"
    if case_path.exists():
        text, problem = read_text(case_path)
        if text is None:
            state["errors"].append({"file": "case.json", "error": problem})
            state["degradations"].append("case.json unreadable")
        else:
            if problem:
                state["degradations"].append(f"case.json encoding: {problem}")
            case = None
            try:
                case = json.loads(text)
            except Exception as exc:
                state["errors"].append({"file": "case.json",
                                        "error": f"bad JSON: {exc}"})
                state["degradations"].append("case.json bad JSON; contract unknown")
            if isinstance(case, dict):
                state["case_id"] = (case.get("case_id") or case.get("id")
                                    or case.get("task_id"))
                state["task_instruction"] = (case.get("task_instruction")
                                             or case.get("task")
                                             or case.get("instruction") or "")
                oc = case.get("output_contract")
                state["output_contract"] = oc
                if isinstance(oc, dict):
                    state["contract_keys"] = list(oc.keys())
                elif isinstance(oc, list):
                    state["contract_keys"] = [str(x) for x in oc
                                              if isinstance(x, str)]
    else:
        state["degradations"].append("case.json missing")

    sources = []
    sid = 0
    skip = {"case.json", "task.md"}
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or path.name in skip:
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        sid += 1
        source_id = f"D{sid}"
        kind = classify_kind(path.name)
        paras, problems = extract_or_reuse(path, kind, work, source_id, rescan)
        rel = str(path.relative_to(input_dir)) if _is_relative(path, input_dir) else str(path)
        sources.append({"source_id": source_id, "path": rel, "kind": kind,
                        "paragraphs": paras, "problems": problems})
    state["sources"] = sources
    return state


def _initial_status(state):
    if state.get("errors") and not state.get("sources"):
        return "input_error"
    if not state.get("sources"):
        return "insufficient_evidence"
    return "partial"


# --------------------------------------------------------------------------- #
# 结果装配
# --------------------------------------------------------------------------- #
def _counts(findings):
    c = {"total": len(findings)}
    for k in SEVERITIES:
        c[k] = sum(1 for f in findings if f.get("severity") == k)
    for k in VERDICTS:
        c[k] = sum(1 for f in findings if f.get("verdict") == k)
    return c


def _coverage(findings, unchecked, draft_cov):
    cov = {}
    covered = {f.get("facet") for f in findings if f.get("facet") in FACETS}
    for f in FACETS:
        cov[f] = "checked" if f in covered else (draft_cov or {}).get(f) or "unchecked"
    return cov


def _load_prev_change_log(out_dir):
    prev = _load_json(out_dir / "result.json")
    if isinstance(prev, dict) and isinstance(prev.get("change_log"), list):
        return list(prev["change_log"])
    return []


def _new_change_log_entry(log, draft, phase):
    prev_status = log[-1].get("to_status") if log else None
    return log + [{
        "at": _ts(time.time()),
        "phase": phase,
        "from_status": prev_status,
        "to_status": draft.get("status"),
        "findings": len(draft.get("findings") or []),
    }]


def assemble_result(index, draft, started_at, finished_at, change_log):
    result = {"case_id": index.get("case_id")}
    oc = index.get("output_contract")
    if isinstance(oc, dict):
        for k, v in oc.items():
            result[k] = v
    elif isinstance(oc, list):
        for x in oc:
            if isinstance(x, str):
                result[x] = None
    result["status"] = draft.get("status") or "partial"
    result["report"] = "review_report.md"
    result["sources"] = [{"source_id": s["source_id"], "path": s["path"],
                          "kind": s["kind"],
                          "paragraph_count": len(s.get("paragraphs", [])),
                          "problems": s.get("problems", [])}
                         for s in index.get("sources", [])]
    result["uncertainty"] = draft.get("uncertainty") or []
    result["pending_human"] = draft.get("pending_human") or []
    result["errors"] = list(index.get("errors") or [])
    result["degradations"] = _uniq((index.get("degradations") or [])
                                   + (draft.get("degradations") or []))
    result["changes"] = draft.get("changes") or []
    result["change_log"] = change_log
    result["runtime"] = {
        "started_at": _ts(started_at),
        "finished_at": _ts(finished_at),
        "duration_seconds": round(finished_at - started_at, 2),
    }
    findings = draft.get("findings") or []
    unchecked = draft.get("unchecked_items") or []
    vs = draft.get("verdict_summary") or {}
    result["result"] = {
        "findings": findings,
        "unchecked_items": unchecked,
        "verdict_summary": {
            "overall": vs.get("overall", "not_assessed"),
            "counts": _counts(findings),
            "coverage": _coverage(findings, unchecked, vs.get("coverage")),
        },
    }
    return result


# --------------------------------------------------------------------------- #
# 报告渲染
# --------------------------------------------------------------------------- #
def render_report(result):
    r = result["result"]
    vs = r["verdict_summary"]
    c = vs.get("counts", {})
    lines = ["# 论文质量审查报告", "",
             f"- case_id: {result.get('case_id')}",
             f"- status: {result.get('status')}",
             f"- 生成时间: {(result.get('runtime') or {}).get('finished_at', '')}",
             "", "## 判定摘要", "",
             f"- 总体结论: {vs.get('overall')}",
             f"- 问题数: total={c.get('total')} critical={c.get('critical')} major={c.get('major')} minor={c.get('minor')} suggestion={c.get('suggestion')}",
             f"- 判定分布: confirmed={c.get('confirmed')} to_review={c.get('to_review')} not_checked={c.get('not_checked')}",
             "", "## 覆盖", "",
             "| facet | 状态 |", "|---|---|"]
    for f in FACETS:
        lines.append(f"| {f} | {vs.get('coverage', {}).get(f, 'unchecked')} |")
    lines += ["", "## 问题清单", ""]
    for fi, f in enumerate(r["findings"]):
        fid = f.get("finding_id") or f"F{fi + 1:02d}"
        lines.append(f"### {fid} [{f.get('facet')} | {f.get('severity')} | "
                     f"{f.get('verdict')}] — {f.get('location', '')}")
        lines += ["",
                  f"- **问题**: {f.get('claim', '')}",
                  f"- **原文**: \"{f.get('quote', '')}\"",
                  f"- **判断依据**: {f.get('rationale', '')}",
                  f"- **修改建议**: {f.get('revision_suggestion', '')}",
                  f"- **置信度**: {f.get('confidence', '')}"]
        ev = f.get("evidence") or []
        if ev:
            lines.append("- **证据**:")
            for e in ev:
                lines.append(f"  - {e.get('source_id', '')} {e.get('locator', '')}: "
                             f"\"{e.get('quote', '')}\"")
        lines.append("")
    lines += ["## 未检查项", ""]
    for u in r["unchecked_items"]:
        lines.append(f"- {u.get('facet', '')}: {u.get('reason', '')}")
    lines += ["", "## 依据与边界", "", "### sources", ""]
    for s in result.get("sources", []):
        extra = f" 问题: {'; '.join(s.get('problems', []))}" if s.get("problems") else ""
        lines.append(f"- {s.get('source_id')} ({s.get('kind')}): {s.get('path')} "
                     f"({s.get('paragraph_count')} 段){extra}")
    lines.append("")
    for key, label in (("uncertainty", "不确定性"), ("pending_human", "待人工复核"),
                       ("degradations", "降级"), ("errors", "错误"), ("changes", "变更")):
        vals = result.get(key) or []
        if not vals:
            continue
        lines += [f"### {label}", ""]
        for v in vals:
            lines.append(f"- {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_init(args):
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    state = _scan_input(inp, work, args.rescan)
    index = dict(state)
    index["scanned_at"] = _ts(started)
    index["input_dir"] = str(inp)
    _out_json(work / "index.json", index)

    draft = {
        "status": _initial_status(state),
        "uncertainty": [], "pending_human": [], "changes": [],
        "degradations": list(state.get("degradations", [])),
        "findings": [],
        "unchecked_items": [{"facet": f, "reason": "未检查"} for f in FACETS],
        "verdict_summary": {"overall": "not_assessed",
                            "coverage": {f: "unchecked" for f in FACETS}},
    }
    _out_json(work / "draft.json", draft)

    change_log = _new_change_log_entry(_load_prev_change_log(out), draft, "init")
    result = assemble_result(index, draft, started, time.time(), change_log)
    _out_json(out / "result.json", result)
    n_para = sum(len(s["paragraphs"]) for s in state["sources"])
    print(f"init: {len(state['sources'])} sources, {n_para} paragraphs; "
          f"partial result.json + draft.json written")


def cmd_show(args):
    work = Path(args.work)
    index = _load_json(work / "index.json") or {"sources": []}
    budget, printed = args.max_chars, 0
    for s in index.get("sources", []):
        if args.source and s["source_id"] != args.source:
            continue
        print(f"\n===== {s['source_id']} ({s['kind']}) {s['path']} =====")
        for p in s.get("paragraphs", []):
            line = f"[{p['locator']}] {p['text']}"
            if printed + len(line) > budget:
                print(f"... truncated at {budget} chars (--max-chars 可调大)")
                return
            print(line)
            printed += len(line) + 1
    print(f"\n(printed {printed} chars)")


def _all_paras(index):
    return [p for s in index.get("sources", []) for p in s.get("paragraphs", [])]


def cmd_check(args):
    work = Path(args.work)
    index = _load_json(work / "index.json") or {"sources": []}
    draft = _load_json(work / "draft.json")
    report = {"checked_at": _ts(time.time()), "quote_checks": [],
              "evidence_checks": [], "field_checks": [], "enum_checks": [],
              "coverage": {}, "failures": 0, "warnings": 0}
    if draft is None:
        report["failures"] = 1
        report["summary"] = "FAIL: draft.json missing (run init first)"
        _out_json(work / "check_report.json", report)
        print("check: FAIL draft.json missing")
        return
    findings = draft.get("findings") or []
    all_paras = _all_paras(index)
    by_source = {s["source_id"]: s.get("paragraphs", []) for s in index.get("sources", [])}

    for fi, f in enumerate(findings):
        fid = f.get("finding_id") or f"F{fi + 1:02d}"
        missing = [k for k in FINDING_KEYS if k not in f]
        if missing:
            report["field_checks"].append({"finding_id": fid, "missing": missing})
            report["failures"] += 1
        for k, allowed in (("facet", FACETS), ("severity", SEVERITIES),
                           ("issue_type", ISSUE_TYPES), ("verdict", VERDICTS),
                           ("confidence", CONFIDENCES)):
            v = f.get(k)
            if v is not None and v not in allowed:
                report["enum_checks"].append({"finding_id": fid, "field": k,
                                              "value": v, "allowed": list(allowed)})
                report["failures"] += 1
        q = f.get("quote")
        if not q or not q.strip():
            report["quote_checks"].append({"finding_id": fid, "ok": False,
                                           "reason": "empty quote"})
            report["failures"] += 1
        else:
            qn = normalize(q)
            hits = [p["locator"] for p in all_paras if qn and qn in p["norm"]]
            if not hits:
                report["quote_checks"].append({"finding_id": fid, "ok": False,
                                               "reason": "quote not found verbatim"})
                report["failures"] += 1
            else:
                entry = {"finding_id": fid, "ok": True, "paragraphs": hits}
                if f.get("location") not in hits:
                    entry["location_mismatch"] = f.get("location")
                    report["warnings"] += 1
                report["quote_checks"].append(entry)
        for e in f.get("evidence") or []:
            sid, eq = e.get("source_id"), e.get("quote")
            if not eq:
                report["evidence_checks"].append({"finding_id": fid,
                                                  "source_id": sid, "ok": False,
                                                  "reason": "empty quote"})
                report["warnings"] += 1
                continue
            eqn = normalize(eq)
            found = any(eqn and eqn in p["norm"] for p in by_source.get(sid, []))
            report["evidence_checks"].append({"finding_id": fid, "source_id": sid,
                                              "locator": e.get("locator"), "ok": found})
            if not found:
                report["warnings"] += 1

    report["coverage"] = _coverage(findings, draft.get("unchecked_items") or [],
                                   (draft.get("verdict_summary") or {}).get("coverage"))
    nf, nw = report["failures"], report["warnings"]
    report["summary"] = (f"PASS ({nw} warnings)" if nf == 0
                         else f"FAIL ({nf} failures, {nw} warnings)")
    _out_json(work / "check_report.json", report)
    print(f"check: {report['summary']}")


def cmd_build(args):
    work, out = Path(args.work), Path(args.output)
    index = _load_json(work / "index.json") or {
        "case_id": None, "output_contract": None, "contract_keys": [],
        "degradations": ["index.json missing; re-run init"],
        "errors": [], "sources": []}
    draft = _load_json(work / "draft.json") or {
        "status": "partial", "findings": [], "unchecked_items": [],
        "uncertainty": [], "pending_human": [], "changes": [],
        "verdict_summary": {"overall": "not_assessed", "coverage": {}}}
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    change_log = _new_change_log_entry(_load_prev_change_log(out), draft, "build")
    result = assemble_result(index, draft, started, time.time(), change_log)
    _out_json(out / "result.json", result)
    _write_text(out / "review_report.md", render_report(result))
    n = len(result["result"]["findings"])
    print(f"build: {n} findings -> review_report.md + result.json")


def cmd_selfcheck(args):
    work, out = Path(args.work), Path(args.output)
    index = _load_json(work / "index.json") or {"sources": []}
    result = _load_json(out / "result.json")
    report = {"checked_at": _ts(time.time()), "checks": [], "failures": 0,
              "warnings": 0}

    def add(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            report["failures"] += 1

    if result is None:
        add("result.json parses", False, "missing or bad JSON")
        report["summary"] = f"FAIL ({report['failures']} failures)"
        _out_json(work / "selfcheck.json", report)
        print("selfcheck: FAIL result.json missing")
        return
    add("result.json parses", True)
    add("case_id present", result.get("case_id") is not None, str(result.get("case_id")))
    add("status valid", result.get("status") in STATUSES, str(result.get("status")))
    for k in ("sources", "uncertainty", "pending_human", "change_log", "runtime"):
        add(f"{k} present", k in result)
    res = result.get("result") or {}
    add("result.findings is list", isinstance(res.get("findings"), list))
    add("result.unchecked_items is list", isinstance(res.get("unchecked_items"), list))
    add("verdict_summary present", isinstance(res.get("verdict_summary"), dict))
    rp = out / "review_report.md"
    add("review_report.md exists", rp.exists())
    if rp.exists():
        rt, _ = read_text(rp)
        if rt is None:
            add("review_report.md readable", False)
        else:
            add("review_report.md readable", True)
            miss = [f.get("finding_id") for f in res.get("findings", [])
                    if f.get("finding_id") and f.get("finding_id") not in rt]
            add("all findings in report", not miss,
                "" if not miss else "missing: " + ", ".join(miss))
    all_paras = _all_paras(index)
    bad = 0
    for f in res.get("findings", []):
        q = f.get("quote") or ""
        if normalize(q) and not any(normalize(q) in p["norm"] for p in all_paras):
            bad += 1
    add("quotes still verifiable", bad == 0, f"{bad} quotes not found")
    nf = report["failures"]
    report["summary"] = (f"PASS ({report['warnings']} warnings)" if nf == 0
                         else f"FAIL ({nf} failures)")
    _out_json(work / "selfcheck.json", report)
    print(f"selfcheck: {report['summary']}")


# --------------------------------------------------------------------------- #
# 入口（退出码恒 0）
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="论文质量审查 Skill 助手（init/show/check/build/selfcheck）")
    p.add_argument("command", nargs="?", default=None,
                   choices=["init", "show", "check", "build", "selfcheck"])
    p.add_argument("--input", dest="input", default="/input")
    p.add_argument("--work", dest="work", default="/app/work")
    p.add_argument("--output", dest="output", default="/app/output")
    p.add_argument("--source", dest="source", default=None,
                   help="show 只打印指定 source_id（如 D1）")
    p.add_argument("--max-chars", dest="max_chars", type=int, default=12000,
                   help="show 单次打印上限字符数")
    p.add_argument("--rescan", dest="rescan", action="store_true",
                   help="init 时强制重新抽取文本（默认复用 work/text）")
    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    if args.command is None:
        p.print_help()
        return 0
    try:
        {"init": cmd_init, "show": cmd_show, "check": cmd_check,
         "build": cmd_build, "selfcheck": cmd_selfcheck}[args.command](args)
        return 0
    except Exception as exc:
        try:
            out = Path(args.output)
            out.mkdir(parents=True, exist_ok=True)
            minimal = {"case_id": None, "status": "input_error",
                       "report": "review_report.md", "sources": [],
                       "uncertainty": [], "pending_human": [],
                       "errors": [{"error": f"{args.command} failed: {exc}"}],
                       "degradations": [f"{args.command} crashed: {exc}"],
                       "changes": [], "change_log": [], "runtime": {},
                       "result": {"findings": [], "unchecked_items": [],
                                  "verdict_summary": {"overall": "not_assessed",
                                                      "counts": {}, "coverage": {}}}}
            _out_json(out / "result.json", minimal)
        except Exception:
            pass
        print(f"{args.command}: error: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
