#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文质量审查 Skill 主入口（仅标准库）。

子命令（Agent 按 SKILL.md 顺序调用）：
  scan | init   扫描 /input，建来源索引，立刻落盘一版合法 partial result.json
  show          打印带 locator 的原文，供 Agent 逐字抄录 quote
  verify        机械校验 work/draft.json 的 quote 是否逐字命中（产 verify_report.json）
  build         读取 draft，验证并成稿 review_report.md + 最终 result.json
  selfcheck     只复检最终产物，不重做

运行时路径默认 /input、/app/work、/app/output；本地用 --input/--work/--output 覆盖。
所有子命令恒以退出码 0 结束：异常被捕获并尽量落盘最小合法 result.json。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_core as ac  # noqa: E402


def _parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="论文质量审查 Skill 助手（scan/show/verify/build/selfcheck）")
    p.add_argument("command", nargs="?", default=None,
                   choices=["scan", "init", "show", "verify", "build", "selfcheck"])
    p.add_argument("--input", dest="input", default="/input")
    p.add_argument("--work", dest="work", default="/app/work")
    p.add_argument("--output", dest="output", default="/app/output")
    p.add_argument("--source", dest="source", default=None,
                   help="show 只打印指定 source_id（如 D1）")
    p.add_argument("--max-chars", dest="max_chars", type=int, default=12000,
                   help="show 单次打印上限字符数")
    p.add_argument("--rescan", dest="rescan", action="store_true",
                   help="scan 时强制重新抽取（默认复用 work/text/<sid>.txt）")
    return p


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return path


def _load_json(path: Path):
    if not path.exists():
        return None
    text, _ = ac.read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _emergency(output: Path, case_id, errors, status="input_error"):
    obj = ac.make_skeleton(case_id, None, status)
    obj["errors"] = errors
    try:
        _write_json(output / "result.json", obj)
    except Exception:
        pass
    return obj


def cmd_scan(args):
    start = time.time()
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    try:
        state = ac.scan_input(inp, work, rescan=args.rescan)
        _write_json(work / "index.json", state)

        status = "partial"
        if not state["sources"] and not (inp / "case.json").exists() and not (inp / "task.md").exists():
            status = "insufficient_evidence"
        skeleton = ac.make_skeleton(state["case_id"], state["output_contract"], status)
        skeleton["sources"] = ac.summarize_sources(state["sources"])
        skeleton["errors"] = state["errors"]
        skeleton["degradations"] = state["degradations"]
        skeleton["runtime"]["seconds"] = round(time.time() - start, 2)
        _write_json(out / "result.json", skeleton)
        print(json.dumps({
            "ok": True, "case_id": state["case_id"], "sources": len(state["sources"]),
            "paras": sum(len(s["paras"]) for s in state["sources"]),
            "degradations": state["degradations"],
        }, ensure_ascii=False))
    except Exception as exc:
        _emergency(out, None, [{"stage": "scan", "detail": str(exc)}], "input_error")
        print(f"scan error: {exc}", file=sys.stderr)
    return 0


def cmd_show(args):
    work = Path(args.work)
    index = _load_json(work / "index.json")
    if not index:
        print("no index.json; run scan first")
        return 0
    total = 0
    for s in index.get("sources", []):
        if args.source and s["source_id"] != args.source:
            continue
        print(f"\n== {s['source_id']} [{s.get('kind')}] {s.get('path')} "
              f"({s.get('pages', 0)} pages, {len(s.get('paras', []))} paras) ==")
        for p in s.get("paras", []):
            line = f"[{ac.locator(p['page'], p['idx'])}] {p['text']}"
            if total + len(line) > args.max_chars:
                print(f"... (truncated at {args.max_chars} chars; use --source to narrow)")
                return 0
            print(line)
            total += len(line)
    return 0


def cmd_verify(args):
    work = Path(args.work)
    index = _load_json(work / "index.json")
    draft = _load_json(work / "draft.json")
    if not index or not draft:
        print("need both work/index.json and work/draft.json; run scan then write draft.json")
        return 0
    report = []
    for f in draft.get("findings") or []:
        problems = ac.validate_finding(f)
        hit = ac.verify_finding(f, index.get("sources", []))
        mismatch = False
        if hit:
            lp = ac.parse_locator(f.get("location"))
            lq = ac.parse_locator(hit.get("locator"))
            mismatch = bool(lp and lq and lp != lq)
        report.append({
            "finding_id": f.get("finding_id"),
            "quote_verified": bool(hit),
            "quote_locator": (hit or {}).get("locator"),
            "location_mismatch": mismatch,
            "problems": problems,
        })
    _write_json(work / "verify_report.json", report)
    bad = [r for r in report if r["problems"] or not r["quote_verified"]]
    print(json.dumps({"all_ok": not bad, "findings": len(report), "bad": len(bad)}, ensure_ascii=False))
    for r in bad:
        print("  FAIL", r["finding_id"], "problems=", r["problems"],
              "quote_locator=", r["quote_locator"])
    for r in report:
        if r["location_mismatch"]:
            print("  WARN", r["finding_id"], "location != quote_locator:", r["quote_locator"])
    return 0


def cmd_build(args):
    start = time.time()
    work, out = Path(args.work), Path(args.output)
    index = _load_json(work / "index.json")
    if not index:
        _emergency(out, None, [{"stage": "build", "detail": "index.json missing; run scan first"}], "input_error")
        print("build: index.json missing")
        return 0
    draft = _load_json(work / "draft.json")
    if not draft:
        draft = {
            "status": "partial",
            "findings": [],
            "unchecked_items": [{"facet": f, "reason": "no draft.json; analysis not run"} for f in ac.FACETS],
            "not_applicable": [],
        }
    previous = _load_json(out / "result.json")
    result = ac.build_result(index, draft, previous)
    result["runtime"]["seconds"] = round(time.time() - start, 2)
    _write_json(out / "result.json", result)
    _write_json(out / "review_report.md", ac.render_report(result))
    print(json.dumps({
        "ok": True, "status": result["status"], "findings": len(result["result"]["findings"]),
        "coverage": result["result"]["coverage"],
        "overall_verdict": result["result"]["verdict_summary"]["overall_verdict"],
    }, ensure_ascii=False))
    return 0


def cmd_selfcheck(args):
    work, out = Path(args.work), Path(args.output)
    result = _load_json(out / "result.json")
    report = []

    def add(name, ok, detail=""):
        report.append({"check": name, "ok": bool(ok), "detail": detail})

    if result is None:
        add("result.json readable", False, "missing or bad JSON")
    else:
        add("case_id present", bool(result.get("case_id")))
        add("status valid", result.get("status") in ac.STATUSES, str(result.get("status")))
        cov = (result.get("result") or {}).get("coverage") or {}
        add("coverage covers six facets", all(f in cov for f in ac.FACETS),
            ",".join(sorted(cov.keys())))
        findings = (result.get("result") or {}).get("findings") or []
        bad = [f.get("finding_id") for f in findings
               if f.get("verdict") == "confirmed" and not f.get("quote_verified")]
        add("all confirmed findings quote-verified", not bad, ",".join(map(str, bad)))
        missing = [f.get("finding_id") for f in findings
                   if any(k not in f or f[k] in (None, "") for k in ac.FINDING_REQUIRED)]
        add("findings have required fields", not missing, ",".join(map(str, missing)))
        index = _load_json(work / "index.json")
        if index:
            keys = ac.contract_keys_of(index.get("output_contract"))
            if keys:
                absent = [k for k in keys if k not in result]
                add("contract keys echoed", not absent, ",".join(map(str, absent)))
            else:
                add("contract keys echoed", True, "no contract keys")
    _write_json(out / "selfcheck.json", report)
    ok = all(r["ok"] for r in report)
    print(json.dumps({"all_ok": ok, "checks": report}, ensure_ascii=False))
    return 0


_HANDLERS = {
    "scan": cmd_scan, "init": cmd_scan, "show": cmd_show,
    "verify": cmd_verify, "build": cmd_build, "selfcheck": cmd_selfcheck,
}


def main(argv=None):
    args = _parser().parse_args(argv)
    if not args.command:
        _parser().print_help()
        return 0
    try:
        return _HANDLERS[args.command](args)
    except SystemExit:
        raise
    except Exception as exc:
        try:
            _emergency(Path(args.output), None,
                       [{"stage": args.command, "detail": str(exc)}], "input_error")
        except Exception:
            pass
        print(f"{args.command} error: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # 兜底：无论如何退出码 0
        print(f"fatal: {exc}", file=sys.stderr)
        sys.exit(0)
