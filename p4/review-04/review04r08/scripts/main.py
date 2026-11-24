# -*- coding: utf-8 -*-
"""main.py — 论文逻辑与论证审查 Skill 的入口脚本（纯标准库）。

三种模式：
  scaffold  发现输入、抽取文本、切分语料、落盘 result.json 骨架 + report.md 骨架
  update    把 Agent 产出的 findings/claims/commitments/argument_chains/coverage 合并进 result.json
  selfcheck 校验 result.json 与 report.md 的合法性（含 output_contract 键回填）

调用示例（路径相对 SKILL.md 所在目录）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update --output /app/output --work /app/work \
      --findings /app/work/findings.json --claims /app/work/claims.json --status partial
  python3 -B scripts/main.py selfcheck --output /app/output --work /app/work

设计取舍：
  - scaffold 先落盘"合法可解析的 partial 骨架"，之后任何异常都不留半截产物；
  - change_log 的 ts 留空，保证 scaffold 两次运行 byte 级一致（确定性）；
  - 全部路径用 pathlib，跨平台（Windows 开发 / Linux 运行）无需修改。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import review_core as rc

rc._reconfigure_streams()


def _argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="论文逻辑与论证审查 Skill 脚本")
    sub = p.add_subparsers(dest="mode", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--input", default="/input", help="输入目录（case.json/task.md/materials）")
        sp.add_argument("--output", default="/app/output", help="最终产物目录")
        sp.add_argument("--work", default="/app/work", help="中间工作目录")

    s1 = sub.add_parser("scaffold", help="抽取语料并落盘结果骨架")
    add_common(s1)
    s1.add_argument("--force", action="store_true", help="覆盖已存在的 result.json")

    s2 = sub.add_parser("update", help="合并审查产物增量更新")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--work", default="/app/work")
    s2.add_argument("--findings", default=None)
    s2.add_argument("--claims", default=None)
    s2.add_argument("--commitments", default=None)
    s2.add_argument("--argument-chains", default=None)
    s2.add_argument("--coverage", default=None, help="coverage JSON 对象文件")
    s2.add_argument("--summary", default=None, help="review.summary 文本或 @文件路径")
    s2.add_argument("--status", default=None, choices=rc.RESULT_STATUSES)
    s2.add_argument("--note", default="", help="change_log 备注")

    s3 = sub.add_parser("selfcheck", help="自检产物合法性")
    s3.add_argument("--output", default="/app/output")
    s3.add_argument("--work", default="/app/work")

    return p


# --------------------------------------------------------------------------
# scaffold
# --------------------------------------------------------------------------
def _resolve_summary(summary: Optional[str]) -> str:
    if not summary:
        return ""
    if summary.startswith("@"):
        p = Path(summary[1:])
        if p.is_file():
            return rc.read_text_safe(str(p))["text"].strip()
        return summary[1:]
    return summary


def _load_dict_file(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    data, err = rc.read_json_tolerant(path)
    if err:
        raise ValueError("读取 %s 失败: %s" % (path, err))
    if not isinstance(data, dict):
        raise ValueError("%s 内容不是 JSON 对象" % path)
    return data


def cmd_scaffold(args: argparse.Namespace) -> int:
    t0 = time.monotonic()
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    result_path = out_dir / "result.json"
    if result_path.exists() and not args.force:
        print("result.json 已存在；如需覆盖加 --force", file=sys.stderr)
        return 0

    discovered = rc.discover_input(args.input)
    task_meta = rc._derive_task_meta(discovered)
    case_id = rc._derive_case_id(discovered)

    case_data: Any = None
    if discovered.get("case_json"):
        case_data, _ = rc.read_json_tolerant(discovered["case_json"])
    contract = rc._contract_keys(case_data)

    corpus = rc.build_corpus(discovered, args.work)
    records: List[Dict[str, Any]] = corpus["records"]
    sources: List[Dict[str, Any]] = corpus["sources"]
    degradations: List[str] = corpus["degradations"]

    corpus_path = work_dir / "corpus.md"
    rc.write_corpus_md(records, sources, degradations, str(corpus_path))

    section_map = rc.build_section_map(records)

    result = rc.new_skeleton(case_id, task_meta, contract, sources, section_map,
                             str(out_dir))
    # 输入缺失/异常诚实降级
    if not records and not discovered.get("case_json") and not discovered.get("task_md"):
        result["status"] = "insufficient_evidence"
        result["limitations"].append("未发现任何输入材料（case.json/task.md/materials）。")
    if task_meta.get("parse_error"):
        result["errors"].append("case.json 解析异常: %s" % task_meta["parse_error"])
    if discovered.get("case_json") is None:
        result["degradations"].append("缺少 case.json，case_id 由文件名推导。")
    if degradations:
        result["degradations"].extend(degradations)
    if not records:
        result["limitations"].append("未抽取到任何正文文本，需补充 PDF/OCR 抽取后再审查。")

    meta = {
        "case_id": case_id,
        "task_meta": task_meta,
        "discovered": discovered,
        "contract": contract,
        "n_records": len(records),
        "n_sources": len(sources),
        "corpus_path": str(corpus_path),
    }
    rc.write_json_atomic(str(work_dir / "meta.json"), meta)
    rc.write_json_atomic(str(result_path), result)
    _write_report_skeleton(case_id, str(out_dir / "report.md"))
    result["runtime"]["seconds_elapsed"] = round(time.monotonic() - t0, 2)
    rc.write_json_atomic(str(result_path), result)

    print("scaffold 完成: case_id=%s status=%s 段落=%d 来源=%d" %
          (case_id, result["status"], len(records), len(sources)))
    print("  result.json: %s" % result_path)
    print("  report.md:   %s" % (out_dir / "report.md"))
    print("  corpus.md:   %s" % corpus_path)
    return 0


def _write_report_skeleton(case_id: str, path: str) -> None:
    txt = """# 论文逻辑与论证审查报告

- case_id: {case_id}
- 状态: partial（骨架，待补充审查结论）
- 说明: 本文件与 result.json 同步更新；每条问题必须给出定位符与逐字原文摘录。

## 0. 方法与范围
（填写：审查了哪些章节/页、采用哪六类检查项、有无截断或缺失材料。）

## 1. 主张清单（claims）
（逐条：作者主张 / 引用他人观点 / 假设情形，区分 speaker 与 verdict。）

## 2. 推理链核查（argument_chains）
（逐条：前提位置 → 结论位置 → 推理缺口 / 隐含假设 / 因果混用。）

## 3. 问题清单（findings）
每条需包含：ID、类型、严重度、定位符、逐字原文、主张、判断依据、
三问 reasoning（影响主结论 / 可修正 / 可复现）、可执行修改建议。

## 4. 六类检查项覆盖情况
| 维度 | 状态 | 检查数 | 说明 |
|---|---|---|---|
| 事实核验 | pending | 0 | |
| 引用支持 | pending | 0 | |
| 方法设计 | pending | 0 | |
| 统计报告 | pending | 0 | |
| 论证链 | pending | 0 | |
| 学术规范 | pending | 0 | |
| 跨章节一致性 | pending | 0 | |

## 5. 依据与边界
（原文证据、来源定位；区分事实错误 / 证据不足 / 规范问题 / 建议性意见。）

## 6. 待复核项与限制
（无法确认的条目、材料缺失、软件缺失等，如实列出，不编造。）

## 7. 变更记录
（对应 result.json.change_log。）
""".format(case_id=case_id)
    rc.atomic_write_text(path, txt)


# --------------------------------------------------------------------------
# update
# --------------------------------------------------------------------------
def cmd_update(args: argparse.Namespace) -> int:
    result_path = Path(args.output) / "result.json"
    result, err = rc.read_json_tolerant(str(result_path))
    if err or not isinstance(result, dict):
        print("无法读取 result.json: %s" % err, file=sys.stderr)
        return 2
    try:
        rc.merge_update(
            result,
            findings=rc._load_array_file(args.findings),
            claims=rc._load_array_file(args.claims),
            commitments=rc._load_array_file(args.commitments),
            argument_chains=rc._load_array_file(args.argument_chains),
            coverage=_load_dict_file(args.coverage) if args.coverage else None,
            summary=_resolve_summary(args.summary),
            status=args.status,
            note=args.note,
        )
    except ValueError as e:
        print("合并失败: %s" % e, file=sys.stderr)
        return 2
    rc.write_json_atomic(str(result_path), result)
    rc._sync_report_status(str(Path(args.output) / "report.md"),
                           result.get("case_id", ""), result.get("status", "partial"))
    print("update 完成: status=%s findings=%d claims=%d" % (
        result.get("status"),
        len(result.get("review", {}).get("findings", [])),
        len(result.get("review", {}).get("claims", [])),
    ))
    return 0


# --------------------------------------------------------------------------
# selfcheck
# --------------------------------------------------------------------------
def cmd_selfcheck(args: argparse.Namespace) -> int:
    result_path = Path(args.output) / "result.json"
    report_path = Path(args.output) / "report.md"
    if not result_path.is_file():
        print("FAIL: result.json 不存在: %s" % result_path)
        return 1
    result, err = rc.read_json_tolerant(str(result_path))
    if err:
        print("FAIL: result.json 不可解析: %s" % err)
        return 1
    errors, warnings = rc.validate_result(result)
    if not report_path.is_file():
        warnings.append("report.md 不存在")
    for w in warnings:
        print("WARN: %s" % w)
    if errors:
        for e in errors:
            print("ERROR: %s" % e)
        print("selfcheck FAIL (%d errors)" % len(errors))
        return 1
    print("selfcheck OK: case_id=%s status=%s findings=%d coverage_dims=%d" % (
        result.get("case_id"), result.get("status"),
        len(result.get("review", {}).get("findings", [])),
        len(result.get("review", {}).get("coverage", {})),
    ))
    if warnings:
        print("  %d warning(s) above" % len(warnings))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _argparse().parse_args(argv)
    if args.mode == "scaffold":
        return cmd_scaffold(args)
    if args.mode == "update":
        return cmd_update(args)
    if args.mode == "selfcheck":
        return cmd_selfcheck(args)
    print("未知模式: %s" % args.mode, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
