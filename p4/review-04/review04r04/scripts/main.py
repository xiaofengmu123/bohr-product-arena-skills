# -*- coding: utf-8 -*-
"""main.py — 论文逻辑与论证审查 Skill 入口（纯标准库）。

三种模式：
  scaffold   发现输入、抽取文本、切分语料，落盘 result.json 骨架 + report.md 骨架
             （先交一版合法可解析的 partial，任何后续异常都不留半截产物）
  update     把 Agent 产出的 findings / claims / commitments / uncertainty / coverage
             合并进 result.json（增量改进，避免整份 JSON 手写出错）
  selfcheck  校验 result.json 与 report.md 的合法性，打印问题清单

调用示例（路径相对 SKILL.md 所在目录）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update --output /app/output --work /app/work \
      --findings /app/work/findings.json --claims /app/work/claims.json \
      --uncertainty /app/work/uncertainty.json --summary "..." --status partial
  python3 -B scripts/main.py selfcheck --output /app/output

缺省值：--input=/input --output=/app/output --work=/app/work
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import review_core as rc


def _argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="论文逻辑与论证审查 Skill 脚本")
    sub = parser.add_subparsers(dest="mode", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--input", default="/input", help="输入目录（含 case.json/task.md/materials）")
        sp.add_argument("--output", default="/app/output", help="最终产物目录")
        sp.add_argument("--work", default="/app/work", help="中间工作目录")

    s1 = sub.add_parser("scaffold", help="抽取语料并落盘结果骨架")
    add_common(s1)
    s1.add_argument("--force", action="store_true", help="覆盖已存在的 result.json")

    s2 = sub.add_parser("update", help="合并 Agent 产物增量更新")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--work", default="/app/work")
    s2.add_argument("--findings", default=None, help="findings JSON 数组文件")
    s2.add_argument("--claims", default=None, help="claims JSON 数组文件")
    s2.add_argument("--commitments", default=None, help="commitments JSON 数组文件")
    s2.add_argument("--uncertainty", default=None, help="uncertainty JSON 数组文件")
    s2.add_argument("--coverage", default=None, help="coverage JSON 对象文件")
    s2.add_argument("--summary", default=None, help="review.summary 文本或文件路径")
    s2.add_argument("--status", default=None, choices=rc.RESULT_STATUSES)

    s3 = sub.add_parser("selfcheck", help="自检产物合法性")
    s3.add_argument("--output", default="/app/output")
    s3.add_argument("--quiet", action="store_true", help="仅在有问题时输出")

    return parser


# ---------------------------------------------------------------------------
# 通用读写
# ---------------------------------------------------------------------------
def _load_result(output_dir: str) -> Dict[str, Any]:
    result_path = Path(output_dir) / "result.json"
    if not result_path.exists():
        raise SystemExit("result.json 不存在，请先运行 scaffold（--output=%s）" % output_dir)
    data, err = rc.read_json_tolerant(str(result_path))
    if not isinstance(data, dict):
        raise SystemExit("result.json 解析失败: %s" % err)
    return data


def _write_result(output_dir: str, result: Dict[str, Any]) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rc.atomic_write_json(str(out / "result.json"), result)
    rc.atomic_write_text(str(out / "report.md"), rc.render_report(result))


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------
def cmd_scaffold(args: argparse.Namespace) -> Dict[str, Any]:
    started = time.time()
    discovered = rc.discover_input(args.input)
    task_meta = rc.load_task_meta(discovered)
    case_id = rc.derive_case_id(discovered, task_meta)
    contract = rc.contract_extra(discovered)
    sources, records, degradations, errors = rc.build_corpus(discovered)

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    rc.write_corpus_md(records, sources, str(work / "corpus.md"))
    rc.atomic_write_json(str(work / "meta.json"), {
        "case_id": case_id,
        "task": task_meta,
        "discovered": discovered,
        "n_sources": len(sources),
        "n_paragraphs": len(records),
        "scaffold_seconds": round(time.time() - started, 2),
    })

    result = rc.build_skeleton(case_id, task_meta, sources, contract, degradations, errors)
    result["review"]["section_map"] = rc.build_section_map(records)
    result["runtime"]["seconds_elapsed"] = round(time.time() - started, 2)
    _write_result(args.output, result)

    print("scaffold done: case_id=%s status=%s sources=%d paragraphs=%d degradations=%d errors=%d"
          % (case_id, result["status"], len(sources), len(records), len(degradations), len(errors)))
    return result


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
def _read_optional_array(path: Optional[str], label: str) -> Optional[List[Any]]:
    if not path:
        return None
    data, err = rc.read_json_tolerant(path)
    if err is not None or not isinstance(data, list):
        print("warning: %s 无法读取或非数组，跳过（%s）: %s" % (label, path, err))
        return None
    return data


def _read_summary(summary: Optional[str], work: Path) -> Optional[str]:
    if summary is None:
        return None
    candidate = Path(summary)
    if candidate.exists() and candidate.is_file():
        return rc.read_text_safe(str(candidate), max_bytes=50_000)["text"].strip()
    return summary.strip()


def cmd_update(args: argparse.Namespace) -> Dict[str, Any]:
    started = time.time()
    result = _load_result(args.output)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    changed: List[str] = []

    findings = _read_optional_array(args.findings, "findings")
    if findings is not None:
        result.setdefault("review", {})["findings"] = findings
        changed.append("findings=%d" % len(findings))

    claims = _read_optional_array(args.claims, "claims")
    if claims is not None:
        result.setdefault("review", {})["claims"] = claims
        changed.append("claims=%d" % len(claims))

    commitments = _read_optional_array(args.commitments, "commitments")
    if commitments is not None:
        result.setdefault("review", {})["commitments"] = commitments
        changed.append("commitments=%d" % len(commitments))

    uncertainty = _read_optional_array(args.uncertainty, "uncertainty")
    if uncertainty is not None:
        result["uncertainty"] = uncertainty
        changed.append("uncertainty=%d" % len(uncertainty))

    if args.coverage:
        data, err = rc.read_json_tolerant(args.coverage)
        if err is None and isinstance(data, dict):
            coverage = result.setdefault("review", {}).setdefault("coverage", {})
            for dim, entry in data.items():
                if isinstance(entry, dict):
                    coverage[dim] = entry
            changed.append("coverage")
        else:
            print("warning: coverage 无法读取，跳过（%s）" % err)

    summary = _read_summary(args.summary, work)
    if summary:
        result.setdefault("review", {})["summary"] = summary
        changed.append("summary")

    if args.status:
        result["status"] = args.status
        changed.append("status=%s" % args.status)

    result["change_log"].append({
        "ts": rc.utcnow(), "event": "update", "note": "增量写回: %s" % ", ".join(changed),
        "status": args.status or result.get("status"),
    })
    result["runtime"]["seconds_elapsed"] = round(
        result.get("runtime", {}).get("seconds_elapsed", 0.0) + (time.time() - started), 2)

    _write_result(args.output, result)
    print("update done: %s (status=%s)" % (", ".join(changed) or "no-op", result["status"]))
    return result


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------
def cmd_selfcheck(args: argparse.Namespace) -> int:
    result_path = Path(args.output) / "result.json"
    if not result_path.exists():
        print("FAIL: result.json 不存在: %s" % result_path)
        return 1

    data, err = rc.read_json_tolerant(str(result_path))
    if err is not None or not isinstance(data, dict):
        print("FAIL: result.json 解析失败: %s" % err)
        return 1

    issues = rc.validate_result(data)
    issues.extend(rc.validate_report(str(Path(args.output) / "report.md"), data.get("case_id", "")))

    if issues:
        print("FAIL: selfcheck 发现 %d 个问题:" % len(issues))
        for issue in issues:
            print("  - %s" % issue)
        return 1

    if not args.quiet:
        print("OK: result.json 与 report.md 校验通过（status=%s, findings=%d, "
              "uncertainty=%d, pending_review=%d, limitations=%d）" % (
                  data.get("status"), len(data.get("review", {}).get("findings", [])),
                  len(data.get("uncertainty", [])), len(data.get("pending_review", [])),
                  len(data.get("limitations", []))))
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    rc._reconfigure_streams()
    args = _argparse().parse_args(argv)
    if args.mode == "scaffold":
        cmd_scaffold(args)
        return 0
    if args.mode == "update":
        cmd_update(args)
        return 0
    if args.mode == "selfcheck":
        return cmd_selfcheck(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
