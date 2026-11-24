#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 的执行入口（薄编排层）。

五命令主路径（评测 Agent 应照 SKILL.md 顺序执行）：

    python scripts/main.py scan        # 事实层：扫描盘点 + infer + 立即落盘 partial 结果
    python scripts/main.py survey      # 打印带 locator 的事实/分组/问题，供模型阅读
    # 模型阅读 survey 输出后，把语义判断写进 /app/work/judgments.json
    python scripts/main.py adjudicate  # 判定层：逐条证据落地校验（quote 逐字命中）
    python scripts/main.py seal        # 收口：合并事实+推断+已落地判定，无条件交付
    python scripts/main.py verify      # 复检 /app/output 产物

三个子命令均支持 --input/--work/--output 覆盖默认目录；默认路径也能直接跑。
退出码恒为 0：真实问题写入 result.json 的 errors/degradations，不靠退出码表达。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pipeline  # noqa: E402

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")


# ---------------------------------------------------------------- 目录与运行时

def resolve_dirs(args):
    input_dir = Path(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    work_dir = Path(args.work) if getattr(args, "work", None) else DEFAULT_WORK
    output_dir = Path(args.output) if getattr(args, "output", None) else DEFAULT_OUTPUT
    for d in (work_dir, output_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return input_dir, work_dir, output_dir


def new_runtime(cmd: str, t0: float) -> dict:
    return {
        "command": cmd,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "duration_seconds": round(time.time() - t0, 3),
        "engine_version": pipeline.ENGINE_VERSION,
    }


def judgments_template(meta: dict) -> dict:
    return {
        "case_id": meta.get("case_id", "unknown"),
        "notes": [],
        "queries": [{"query_id": q["query_id"], "answer": None, "status": "insufficient_evidence",
                     "evidence": []} for q in meta.get("queries", [])],
    }


# ---------------------------------------------------------------- 命令实现

def cmd_scan(input_dir, work_dir, output_dir):
    t0 = time.time()
    facts = pipeline.scan(input_dir, work_dir)
    pipeline.dump_json(facts, work_dir / "facts.json")
    inferences = pipeline.infer(facts, work_dir)
    pipeline.dump_json(inferences, work_dir / "inferences.json")
    # 判定层模板（模型随后填写）；已存在则不覆盖
    tmpl_path = work_dir / "judgments.json"
    if not tmpl_path.exists():
        pipeline.dump_json(judgments_template(facts.get("case_meta", {})), tmpl_path)
    # 前 3 分钟硬规则：立即落盘 partial 结果骨架
    rt = new_runtime("scan", t0)
    pipeline.seal(facts, inferences, {}, {"engine_version": pipeline.ENGINE_VERSION,
                                          "notes": [], "queries": [],
                                          "summary": {"grounded": 0, "ungrounded": 0,
                                                      "degraded": 0, "invalid": 0}},
                  output_dir, rt)
    print(json.dumps({"phase": "scan", "files": len(facts["files"]),
                      "status": "partial_skeleton_written",
                      "output": str(output_dir / "result.json")}, ensure_ascii=False))
    return 0


def cmd_survey(input_dir, work_dir, output_dir):
    t0 = time.time()
    facts = _load_or(work_dir / "facts.json", lambda: pipeline.scan(input_dir, work_dir))
    pipeline.dump_json(facts, work_dir / "facts.json")
    inferences = _load_or(work_dir / "inferences.json", lambda: pipeline.infer(facts, work_dir))
    pipeline.dump_json(inferences, work_dir / "inferences.json")

    print("== case_id: %s ==" % facts.get("case_id"))
    print("== 文件（%d）==" % len(facts.get("files", [])))
    for f in facts.get("files", []):
        print("  %s  %s  size=%s  sha256=%s  %s  text=%s" % (
            f["id"], f["rel_path"], f.get("size"), (f.get("sha256") or "-")[:10],
            f.get("mime") or "-", "yes" if f.get("has_text_layer") else "no"))
    for group_name, key in (("重复/别名", "alias_groups"), ("版本候选", "version_groups"),
                            ("冲突", "conflicts"), ("显式关系", "explicit_relations")):
        groups = inferences.get(key, [])
        print("== %s（%d）==" % (group_name, len(groups)))
        for g in groups:
            print("  " + json.dumps(g, ensure_ascii=False))
    queries = facts.get("case_meta", {}).get("queries", [])
    print("== 检索问题（%d）==" % len(queries))
    for q in queries:
        print("  %s: %s" % (q.get("query_id"), q.get("text")))
    if facts.get("errors"):
        print("== errors ==")
        for e in facts["errors"]:
            print("  " + json.dumps(e, ensure_ascii=False))
    if facts.get("degradations"):
        print("== degradations ==")
        for d in facts["degradations"]:
            print("  " + json.dumps(d, ensure_ascii=False))
    print("== 下一步：把语义判断写入 %s ==" % (work_dir / "judgments.json"))
    return 0


def cmd_adjudicate(input_dir, work_dir, output_dir):
    t0 = time.time()
    facts = _load_or(work_dir / "facts.json", lambda: pipeline.scan(input_dir, work_dir))
    inferences = _load_or(work_dir / "inferences.json", lambda: pipeline.infer(facts, work_dir))
    judgments = _load_or(work_dir / "judgments.json", lambda: {"notes": [], "queries": []})
    report = pipeline.adjudicate(facts, inferences, judgments, work_dir)
    pipeline.dump_json(report, work_dir / "validation_report.json")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


def cmd_seal(input_dir, work_dir, output_dir):
    t0 = time.time()
    facts = _load_or(work_dir / "facts.json", lambda: pipeline.scan(input_dir, work_dir))
    pipeline.dump_json(facts, work_dir / "facts.json")
    inferences = _load_or(work_dir / "inferences.json", lambda: pipeline.infer(facts, work_dir))
    pipeline.dump_json(inferences, work_dir / "inferences.json")
    judgments = _load_or(work_dir / "judgments.json", lambda: None)
    if judgments is None:
        judgments = {"case_id": facts.get("case_id"), "notes": [], "queries": []}
    report = _load_or(work_dir / "validation_report.json",
                      lambda: pipeline.adjudicate(facts, inferences, judgments, work_dir))
    pipeline.dump_json(report, work_dir / "validation_report.json")
    rt = new_runtime("seal", t0)
    result = pipeline.seal(facts, inferences, judgments, report, output_dir, rt)
    print(json.dumps({"phase": "seal", "status": result["status"],
                      "claims": len(result.get("claims", [])),
                      "pending_human": len(result.get("pending_human", []))},
                     ensure_ascii=False))
    return 0


def cmd_verify(input_dir, work_dir, output_dir):
    t0 = time.time()
    summary = pipeline.verify(output_dir)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _load_or(path: Path, factory):
    if path.exists():
        try:
            return pipeline.read_json(path)
        except Exception:
            pass
    obj = factory()
    if obj is not None:
        pipeline.dump_json(obj, path)
    return obj


# ---------------------------------------------------------------- 入口

def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", default=None, help="输入目录（默认 /input）")
    p.add_argument("--work", default=None, help="工作目录（默认 /app/work）")
    p.add_argument("--output", default=None, help="输出目录（默认 /app/output）")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="科研文档库修复 Skill：scan/survey/adjudicate/seal/verify 五命令主路径。")
    _add_common_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    for cmd, help_text in (("scan", "扫描盘点 + 立即落盘 partial 结果"),
                           ("survey", "打印带 locator 的事实/分组/问题"),
                           ("adjudicate", "逐条证据落地校验 judgments.json"),
                           ("seal", "合并并收口交付 /app/output"),
                           ("verify", "复检 /app/output 产物")):
        sp = sub.add_parser(cmd, help=help_text)
        _add_common_args(sp)

    args = parser.parse_args(argv)
    try:
        input_dir, work_dir, output_dir = resolve_dirs(args)
        handlers = {
            "scan": cmd_scan,
            "survey": cmd_survey,
            "adjudicate": cmd_adjudicate,
            "seal": cmd_seal,
            "verify": cmd_verify,
        }
        return handlers[args.command](input_dir, work_dir, output_dir)
    except Exception as exc:  # 任何异常都不以退出码表达
        print("ERROR(handled, exit 0): %s" % exc, file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
