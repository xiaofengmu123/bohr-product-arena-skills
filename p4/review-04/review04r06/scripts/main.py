# -*- coding: utf-8 -*-
"""main.py — 论文逻辑与论证审查 Skill 的入口脚本（纯标准库，确定性）。

三种模式：
  scaffold  发现输入、抽取并切分语料，落盘"结果骨架 + 中间表示 + 报告骨架"。
             任何后续异常都不会留下半截产物：这一版已合法可解析。
  update    把 Agent 填写好的 work/review_model.json（唯一手改中间表示）规范化、
             校验后合并进 result.json，并确定性重建 evidence_map.json / plan.json / report.md。
  selfcheck 校验 result.json、evidence_map 与 report.md 的合法性，退出码 0=通过、1=有问题。

调用示例（路径相对 SKILL.md 所在目录）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update   --output /app/output --work /app/work
  python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import review_core as rc

for _s in (sys.stdout, sys.stderr):
    try:
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

_START = time.time()


def _p(*a: Any) -> None:
    print(*a)


def _elapsed() -> float:
    return round(time.time() - _START, 2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="论文逻辑与论证审查 Skill 脚本")
    sub = p.add_subparsers(dest="mode", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    s1 = sub.add_parser("scaffold")
    common(s1)
    s1.add_argument("--force", action="store_true")

    s2 = sub.add_parser("update")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--work", default="/app/work")
    s2.add_argument("--model", default=None, help="review_model.json 路径，默认 work/review_model.json")
    s2.add_argument("--status", default=None, choices=rc.RESULT_STATUSES)

    s3 = sub.add_parser("selfcheck")
    s3.add_argument("--output", default="/app/output")
    s3.add_argument("--work", default="/app/work")
    return p


def _load_prev(out_dir: Path) -> Dict[str, Any]:
    result_path = out_dir / "result.json"
    if result_path.exists():
        data, err = rc.read_json_tolerant(str(result_path))
        if err is None and isinstance(data, dict):
            return data
    return rc.minimal_result()


def cmd_scaffold(args: argparse.Namespace) -> None:
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    discovered = rc.discover_input(args.input)
    meta = rc.task_meta(discovered)
    case_id = rc.derive_case_id(discovered, meta)
    contract = rc.contract_extra(discovered)
    records, sources = rc.build_records(args.input, discovered)

    has_input = bool(discovered.get("case_json") or discovered.get("task_md")
                     or discovered.get("materials"))
    has_material_text = any(r["kind"] == "material" for r in records)
    if not has_input:
        status = "insufficient_evidence"
    elif not has_material_text and not discovered.get("case_json"):
        status = "insufficient_evidence"
    else:
        status = "partial"

    result = rc.build_result_skeleton(case_id, meta, contract, sources, records, status)
    result["runtime"]["seconds_elapsed"] = _elapsed()

    # 中间表示（供 Agent 阅读与填写，供脚本重建最终产物）
    rc.atomic_write_json(str(work_dir / "corpus.json"), records)
    rc.write_corpus_md(records, sources, str(work_dir / "corpus.md"))
    rc.atomic_write_json(str(work_dir / "meta.json"), {
        "case_id": case_id, "challenge_id": meta.get("challenge_id"),
        "task_title": meta.get("task_title"), "status": status,
        "n_sources": len(sources), "n_paragraphs": len(records),
        "n_material_paragraphs": sum(1 for r in records if r["kind"] == "material"),
        "discovered": discovered,
    })
    rc.atomic_write_json(str(work_dir / "plan.json"), rc.build_plan({}))
    rc.atomic_write_json(str(work_dir / "review_model.json"), rc.empty_model())
    rc.atomic_write_json(str(work_dir / "evidence_map.json"), [])

    rc.atomic_write_json(str(out_dir / "result.json"), result)
    rc.atomic_write_text(str(out_dir / "report.md"), rc.render_report(result, records))
    _p("scaffold OK: case_id=%s status=%s sources=%d paragraphs=%d material_paras=%d"
       % (case_id, status, len(sources), len(records),
          sum(1 for r in records if r["kind"] == "material")))


def cmd_update(args: argparse.Namespace) -> None:
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    result = _load_prev(out_dir)
    model_path = Path(args.model) if args.model else (work_dir / "review_model.json")
    records = rc.load_records(str(work_dir / "corpus.json"))

    model, err = rc.read_json_tolerant(str(model_path))
    if err is not None or not isinstance(model, dict):
        result["errors"] = list(result.get("errors") or []) + [
            "review_model 不可读: %s" % (err or "文件为空或非对象")]
        result["status"] = result.get("status") or "partial"
        result["change_log"] = list(result.get("change_log") or []) + [
            {"ts": rc.now_iso(), "event": "update_failed",
             "note": "review_model 不可读，保留旧结果"}]
        result["runtime"]["seconds_elapsed"] = _elapsed()
        rc.atomic_write_json(str(out_dir / "result.json"), result)
        rc.atomic_write_text(str(out_dir / "report.md"), rc.render_report(result, records))
        _p("update: review_model 不可读，保留旧结果（错误已记录）")
        return

    errors, warnings, nm = rc.validate_review_model(model)
    result["review"]["summary"] = nm["summary"]
    result["review"]["findings"] = nm["findings"]
    result["review"]["claims"] = nm["claims"]
    result["review"]["commitments"] = nm["commitments"]
    result["review"]["coverage"] = nm["coverage"]
    result["evidence_map"] = rc.build_evidence_map(nm, records)
    result["uncertainty"] = nm["uncertainty"]
    result["pending_review"] = nm["pending_review"]
    result["pending_human"] = nm["pending_human"]
    result["limitations"] = nm["limitations"]

    if errors:
        result["errors"] = list(result.get("errors") or []) + errors
    if warnings:
        result["degradations"] = list(result.get("degradations") or []) + \
            ["model_warning: " + w for w in warnings]

    result["status"] = args.status or rc.derive_status(nm)
    result["change_log"] = list(result.get("change_log") or []) + [
        {"ts": rc.now_iso(), "event": "incremental_update",
         "note": "findings=%d claims=%d commitments=%d errors=%d warnings=%d"
                 % (len(nm["findings"]), len(nm["claims"]), len(nm["commitments"]),
                    len(errors), len(warnings))}]
    result["runtime"]["seconds_elapsed"] = _elapsed()

    rc.atomic_write_json(str(out_dir / "result.json"), result)
    rc.atomic_write_json(str(work_dir / "plan.json"), rc.build_plan(nm["coverage"]))
    rc.atomic_write_json(str(work_dir / "evidence_map.json"), result["evidence_map"])
    rc.atomic_write_text(str(out_dir / "report.md"), rc.render_report(result, records))
    _p("update OK: status=%s findings=%d claims=%d commitments=%d errors=%d"
       % (result["status"], len(nm["findings"]), len(nm["claims"]),
          len(nm["commitments"]), len(errors)))


def cmd_selfcheck(args: argparse.Namespace) -> None:
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    issues: List[str] = []
    result_path = out_dir / "result.json"
    if not result_path.exists():
        issues.append("result.json 不存在")
    else:
        result, err = rc.read_json_tolerant(str(result_path))
        if err is not None or not isinstance(result, dict):
            issues.append("result.json 不可解析: %s" % (err or "空"))
        else:
            issues.extend(rc.check_result(result))
            records = rc.load_records(str(work_dir / "corpus.json"))
            issues.extend(rc.check_evidence_refs(result, records))
    if not (out_dir / "report.md").exists():
        issues.append("report.md 不存在")
    if issues:
        for i in issues:
            _p("FAIL:", i)
        _p("selfcheck: %d 处问题" % len(issues))
        sys.exit(1)
    _p("selfcheck OK")


def main() -> None:
    args = build_parser().parse_args()
    if args.mode == "scaffold":
        cmd_scaffold(args)
    elif args.mode == "update":
        cmd_update(args)
    elif args.mode == "selfcheck":
        cmd_selfcheck(args)
    else:
        build_parser().print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
