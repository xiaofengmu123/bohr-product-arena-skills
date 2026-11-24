# -*- coding: utf-8 -*-
"""main.py — 论文"论点—证据—推理链"审查 Skill 的入口脚本（纯标准库）。

子命令：
  scaffold  发现输入、抽取文本、切分语料，写预算计划与合法的 partial 骨架
  update    把 Agent 产出的 findings/claims/commitments/coverage 合并进 result.json
  selfcheck 校验 result.json 与 report.md 的合法性（evidence-lock 等不变量）
  budget    读取工作目录的 journal，报告各阶段预算使用情况

用法（路径相对 SKILL.md 所在目录；缺省 --input=/input --output=/app/output --work=/app/work）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update --output /app/output --work /app/work \\
      --findings /app/work/findings.json --claims /app/work/claims.json --status partial
  python3 -B scripts/main.py selfcheck --output /app/output
  python3 -B scripts/main.py budget --work /app/work
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import core


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="论文论点—证据—推理链审查 Skill 脚本")
    sub = p.add_subparsers(dest="mode", required=True)

    s1 = sub.add_parser("scaffold", help="发现输入、切分语料、落盘 partial 骨架")
    s1.add_argument("--input", default="/input")
    s1.add_argument("--output", default="/app/output")
    s1.add_argument("--work", default="/app/work")
    s1.add_argument("--force", action="store_true")

    s2 = sub.add_parser("update", help="合并 Agent 审查结果，增量更新")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--work", default="/app/work")
    s2.add_argument("--findings", default=None)
    s2.add_argument("--claims", default=None)
    s2.add_argument("--commitments", default=None)
    s2.add_argument("--coverage", default=None)
    s2.add_argument("--summary", default=None)
    s2.add_argument("--status", default=None, choices=core.RESULT_STATUSES)
    s2.add_argument("--note", default=None)

    s3 = sub.add_parser("selfcheck", help="自检产物合法性")
    s3.add_argument("--output", default="/app/output")

    s4 = sub.add_parser("budget", help="报告阶段预算使用")
    s4.add_argument("--work", default="/app/work")
    s4.add_argument("--output", default="/app/output")
    return p


# --------------------------------------------------------------------------
# scaffold
# --------------------------------------------------------------------------
def _load_task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None, "case_json": None}
    if discovered.get("case_json"):
        data, err = core.read_json_tolerant(discovered["case_json"])
        meta["case_json"] = discovered["case_json"]
        if isinstance(data, dict):
            meta["task_title"] = data.get("title") or data.get("task_title")
            meta["challenge_id"] = data.get("challenge_id") or data.get("challengeId")
        if err:
            meta["_case_parse_error"] = err
    if discovered.get("task_md"):
        r = core.read_text_safe(discovered["task_md"], max_bytes=200_000)
        for line in r["text"].splitlines()[:80]:
            ls = line.strip()
            if ls.startswith("#") and ls.lstrip("#").strip():
                meta.setdefault("task_title", ls.lstrip("#").strip())
                break
    return meta


def _derive_case_id(discovered: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = core.read_json_tolerant(discovered["case_json"])
        if isinstance(data, dict):
            for key in ("id", "case_id", "caseId", "caseid"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return Path(discovered["case_json"]).stem
    if discovered.get("task_md"):
        return Path(discovered["task_md"]).stem
    return "unknown_case"


def _contract_extra(discovered: Dict[str, Any]) -> Dict[str, Any]:
    if not discovered.get("case_json"):
        return {}
    data, _ = core.read_json_tolerant(discovered["case_json"])
    if isinstance(data, dict):
        c = data.get("output_contract")
        if isinstance(c, dict):
            return dict(c)
    return {}


def _build_corpus_md(records: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> str:
    lines = ["# 语料库（供逐段审查，定位符可直接引用）", "",
             "来源文件 %d 个，段落 %d 个。" % (len(sources), len(records)),
             "定位符：PDF 为 `p<页>¶<段>`，纯文本为 `L<段>`；引用原文必须逐字复制，不得改写。", ""]
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        by_src.setdefault(rec["src"], []).append(rec)
    for src in sources:
        key = src["path"]
        recs = by_src.get(key, [])
        lines.append("")
        lines.append("## 来源: %s" % key)
        lines.append("- kind=%s n_pages=%s chars=%s error=%s" % (
            src.get("kind"), src.get("n_pages"), src.get("chars"), src.get("error")))
        lines.append("")
        for rec in recs:
            lines.append("### %s [%s] %s" % (rec["locator"], rec["section"], src.get("kind")))
            lines.append(rec["text"])
            lines.append("")
    return "\n".join(lines)


def cmd_scaffold(args: argparse.Namespace) -> int:
    t0 = time.time()
    discovered = core.discover_inputs(args.input)
    task_meta = _load_task_meta(discovered)
    case_id = _derive_case_id(discovered)
    contract_extra = _contract_extra(discovered)

    out_dir = Path(args.output)
    work_dir = Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    if (out_dir / "result.json").exists() and not args.force:
        print("result.json 已存在；使用 --force 覆盖。", file=sys.stderr)
        return 2

    records: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    degradations: List[str] = []
    errors: List[str] = []

    # 任务描述本身也入库，便于跨章节对照与承诺抽取
    for path, kind in ((discovered.get("task_md"), "task"), (discovered.get("case_json"), "task")):
        if not path:
            continue
        r = core.read_text_safe(path)
        sources.append({"path": path, "kind": kind, "n_pages": 0,
                        "chars": len(r["text"]), "error": r["error"]})
        if r["error"]:
            errors.append("read %s: %s" % (path, r["error"]))
        records.extend(core.segment_text(r["text"], path, has_pages=False))

    for path in discovered.get("materials", []):
        ext = core.extract_text(path, str(work_dir))
        if ext["error"] and ext["kind"] == "pdf":
            degradations.append("PDF 抽取失败 %s: %s" % (path, ext["error"]))
        elif ext["error"]:
            errors.append("读取失败 %s: %s" % (path, ext["error"]))
        n_pages = ext.get("pages", 0) or 0
        sources.append({"path": path, "kind": "material", "n_pages": n_pages,
                        "chars": len(ext.get("text", "")), "error": ext.get("error")})
        records.extend(core.segment_text(ext.get("text", ""), path, ext.get("has_pages", False)))

    # 没有任何论文/材料源（无论任务说明是否有文本）→ 材料不足，仍写合法结果
    material_sources = [s for s in sources if s["kind"] == "material"]
    if not material_sources:
        result = core.make_skeleton(case_id, task_meta, contract_extra, sources, [],
                                    degradations, errors)
        result["status"] = "insufficient_evidence"
        result["limitations"].append("未发现论文/材料文本，无法开展逐条审查。")
        result["change_log"].append({"ts": core.now_utc(), "event": "scaffold_written",
                                     "note": "材料不足，状态置为 insufficient_evidence"})
        core.atomic_write_json(str(out_dir / "result.json"), result)
        core.atomic_write_text(str(out_dir / "report.md"), core.render_report(result))
        _write_work(work_dir, records, sources, result, case_id, contract_extra)
        print("scaffold: insufficient_evidence（无可用材料），已落盘最小合法产物")
        return 0

    # section_map：按段落顺序去重
    seen_sections: List[str] = []
    for rec in records:
        if rec["section"] not in seen_sections:
            seen_sections.append(rec["section"])
    section_map = [{"section": s, "pages": sorted({r["page"] for r in records
                                                   if r["section"] == s and r.get("page")})}
                   for s in seen_sections]

    corpus_md = _build_corpus_md(records, sources)
    core.atomic_write_text(str(work_dir / "corpus.md"), corpus_md)
    core.atomic_write_json(str(work_dir / "meta.json"), {
        "case_id": case_id, "task": task_meta, "contract_keys": list(contract_extra.keys()),
        "n_sources": len(sources), "n_records": len(records)})

    result = core.make_skeleton(case_id, task_meta, contract_extra, sources, section_map,
                                degradations, errors)
    result["runtime"]["seconds_elapsed"] = round(time.time() - t0, 2)
    core.atomic_write_json(str(out_dir / "result.json"), result)
    core.atomic_write_text(str(out_dir / "report.md"), core.render_report(result))
    core.atomic_write_json(str(work_dir / "budget.json"), {
        "plan": core.PHASE_PLAN, "start_ts": core.now_utc()})
    core.append_journal(str(work_dir), "P0_scaffold", "scaffold_written",
                        "骨架落盘：%d 源 %d 段" % (len(sources), len(records)))

    print("scaffold ok: case_id=%s sources=%d records=%d status=partial" % (
        case_id, len(sources), len(records)))
    print("  corpus:  %s" % (work_dir / "corpus.md"))
    print("  result:  %s" % (out_dir / "result.json"))
    return 0


def _write_work(work_dir: Path, records: List[Dict[str, Any]], sources: List[Dict[str, Any]],
                result: Dict[str, Any], case_id: str, contract_extra: Dict[str, Any]) -> None:
    core.atomic_write_text(str(work_dir / "corpus.md"),
                           _build_corpus_md(records, sources))
    core.atomic_write_json(str(work_dir / "meta.json"),
                           {"case_id": case_id, "contract_keys": list(contract_extra.keys()),
                            "n_sources": len(sources), "n_records": len(records)})
    core.atomic_write_json(str(work_dir / "budget.json"),
                           {"plan": core.PHASE_PLAN, "start_ts": core.now_utc()})
    core.append_journal(str(work_dir), "P0_scaffold", "scaffold_written", "无材料骨架")


# --------------------------------------------------------------------------
# update
# --------------------------------------------------------------------------
def _read_json_array_file(path: Optional[str]) -> Optional[List[Any]]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    data, err = core.read_json_tolerant(str(p))
    if err:
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    return None


def _clean_findings(raw: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """evidence-lock：缺 locator 或 quote 的 finding 拒绝入库，避免不可复核条目。"""
    out: List[Dict[str, Any]] = []
    if not raw:
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        loc = str(item.get("locator", "")).strip()
        quote = str(item.get("quote", "")).strip()
        if not loc or not quote:
            continue  # 丢弃不可复核条目，调用方会收到警告
        out.append(item)
    return out


def cmd_update(args: argparse.Namespace) -> int:
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    result, err = core.load_result(str(out_dir))
    if result is None:
        print("update: %s" % err, file=sys.stderr)
        return 1

    findings = _read_json_array_file(args.findings)
    claims = _read_json_array_file(args.claims)
    commitments = _read_json_array_file(args.commitments)
    coverage_raw = None
    if args.coverage:
        cov, cerr = core.read_json_tolerant(args.coverage)
        if isinstance(cov, dict) and not cerr:
            coverage_raw = cov

    review = result.setdefault("review", {})
    before_f = len(review.get("findings", []) or [])
    if findings is not None:
        cleaned = _clean_findings(findings)
        dropped = len(findings) - len(cleaned)
        review["findings"] = cleaned
        if dropped:
            print("update: 丢弃 %d 条缺 locator/quote 的不可复核 finding" % dropped,
                  file=sys.stderr)
        after_f = len(cleaned)
    else:
        after_f = before_f
    if claims is not None:
        review["claims"] = [c for c in claims if isinstance(c, dict)]
    if commitments is not None:
        review["commitments"] = [c for c in commitments if isinstance(c, dict)]
    if coverage_raw is not None:
        cov = review.setdefault("coverage", {})
        for d in core.COVERAGE_DIMS:
            if d in coverage_raw:
                cov[d] = coverage_raw[d]
    if args.summary:
        txt = args.summary
        if txt.startswith("@") and Path(txt[1:]).exists():
            txt = core.read_text_safe(txt[1:])["text"]
        review["summary"] = txt
    if args.status:
        result["status"] = args.status
    note = args.note or ("findings %d→%d, claims %d, commitments %d" % (
        before_f, after_f, len(review.get("claims", []) or []),
        len(review.get("commitments", []) or [])))
    result.setdefault("change_log", []).append(
        {"ts": core.now_utc(), "event": "incremental_update", "note": note})
    if not isinstance(result.get("runtime"), dict):
        result["runtime"] = {"seconds_elapsed": 0.0, "model_requests_used": 0,
                             "budget": {"requests": 64, "seconds": 900}}

    core.atomic_write_json(str(out_dir / "result.json"), result)
    core.atomic_write_text(str(out_dir / "report.md"), core.render_report(result))
    core.append_journal(str(work_dir), "P1-P4", "incremental_update", note)
    print("update ok: status=%s findings=%d claims=%d" % (
        result["status"], len(review.get("findings", []) or []),
        len(review.get("claims", []) or [])))
    return 0


# --------------------------------------------------------------------------
# selfcheck
# --------------------------------------------------------------------------
def cmd_selfcheck(args: argparse.Namespace) -> int:
    out_dir = Path(args.output)
    result, err = core.load_result(str(out_dir))
    if result is None:
        print("selfcheck: FAIL - %s" % err)
        return 1
    issues = core.validate_result(result)
    fails = [i for i in issues if i["level"] == "fail"]
    warns = [i for i in issues if i["level"] == "warn"]
    for i in fails:
        print("[FAIL] %s" % i["msg"])
    for i in warns:
        print("[WARN] %s" % i["msg"])
    report_path = out_dir / "report.md"
    if not report_path.exists():
        print("[FAIL] report.md missing")
        fails.append({"level": "fail", "msg": "report.md missing"})
    else:
        rtext = core.read_text_safe(str(report_path))["text"]
        cid = str(result.get("case_id", ""))
        if cid and cid not in rtext:
            print("[WARN] report.md 未含 case_id")
            warns.append({"level": "warn", "msg": "report.md 未含 case_id"})
    # 六维覆盖全部标注
    coverage = result.get("review", {}).get("coverage", {}) if isinstance(result.get("review"), dict) else {}
    for d in core.COVERAGE_DIMS:
        st = coverage.get(d, {}).get("status") if isinstance(coverage.get(d), dict) else None
        if st not in ("done", "partial", "skipped"):
            print("[WARN] coverage.%s 未标注 status" % d)
    if fails:
        print("selfcheck: FAIL (%d fail, %d warn)" % (len(fails), len(warns)))
        return 1
    print("selfcheck: OK (status=%s, %d warnings)" % (result.get("status"), len(warns)))
    return 0


# --------------------------------------------------------------------------
# budget
# --------------------------------------------------------------------------
def cmd_budget(args: argparse.Namespace) -> int:
    plan = core.PHASE_PLAN
    print("阶段预算（总 900 秒 / 64 请求）：")
    print("| 阶段 | 计划秒 | 计划请求 | 交付物 |")
    print("|---|---|---|---|")
    for ph in plan:
        print("| %s | %d | %d | %s |" % (ph["phase"], ph["seconds"], ph["requests"],
                                         ph["deliverable"]))
    journal = core.read_journal(args.work)
    print("")
    print("journal 记录 %d 条：" % len(journal))
    for e in journal[-20:]:
        print("- [%s] %s %s — %s" % (e.get("ts"), e.get("phase"), e.get("event"),
                                     e.get("note", "")))
    result, _ = core.load_result(args.output)
    if result and isinstance(result.get("runtime"), dict):
        rt = result["runtime"]
        print("")
        print("runtime: seconds_elapsed=%s model_requests_used=%s" % (
            rt.get("seconds_elapsed"), rt.get("model_requests_used")))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    _reconfigure_stdio()
    args = _argparse().parse_args(argv)
    if args.mode == "scaffold":
        return cmd_scaffold(args)
    if args.mode == "update":
        return cmd_update(args)
    if args.mode == "selfcheck":
        return cmd_selfcheck(args)
    if args.mode == "budget":
        return cmd_budget(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
