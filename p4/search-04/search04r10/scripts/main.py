# -*- coding: utf-8 -*-
"""main.py — thin CLI entry for the citation-tracing Skill.

用法：
  python3 scripts/main.py discover --input /input --output /app/output --work /app/work
  python3 scripts/main.py merge    --work /app/work --patch /app/work/patch-01.json [--reason ...]
  python3 scripts/main.py stage    --work /app/work --output /app/output
  python3 scripts/main.py finalize --work /app/work --output /app/output
  python3 scripts/main.py validate --work /app/work [--full]
  python3 scripts/main.py dedupe   --work /app/work    # dry-run 打印合并结果，不写盘
  python3 scripts/main.py rank     --work /app/work    # dry-run 打印排序结果，不写盘

仅依赖 Python 标准库；路径缺省指向运行时约定目录。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core  # noqa: E402


def cmd_discover(args):
    manifest, problems = core.discover_input(args.input)
    case_path = os.path.join(args.input, "case.json")
    if not os.path.isfile(case_path):
        case, err = None, "case_json_missing"
    else:
        case, err = core.load_json_file(case_path)
    degradations = []
    if err:
        degradations.append("case_json:%s" % err)
    ir = core.build_skeleton(case, args.input, args.work, args.output)
    for m in manifest:
        if m.get("oversize"):
            degradations.append("oversize:%s (%d bytes)" % (m["name"], m["size"]))
    if problems:
        ir.setdefault("errors", []).extend(problems)
    if degradations:
        ir.setdefault("degradations", []).extend(degradations)
    if not case:
        ir["status"] = "insufficient_evidence"
        ir.setdefault("pending_human", []).append("case.json 缺失或无法解析，无法确认种子与 output_contract")
    # 骨架必须通过 skeleton 校验；失败也落盘最小合法 JSON。
    probes = core.validate_ir(ir, "skeleton")
    if probes:
        ir.setdefault("errors", []).extend("bootstrap_validation:" + p for p in probes)
    core.save_ir(args.work, ir)
    result = core.materialize(ir)
    os.makedirs(args.output, exist_ok=True)
    core.write_json(os.path.join(args.output, "result.json"), result)
    print("discover ok: %d files, case_id=%s, status=%s"
          % (len(manifest), ir.get("case_id"), ir.get("status")))
    return 0


def cmd_merge(args):
    ir = core.load_ir(args.work)
    if not ir:
        print("merge failed: no_ir_file", file=sys.stderr)
        return 2
    patch, err = core.load_json_file(args.patch)
    if patch is None:
        print("merge failed: patch %s" % err, file=sys.stderr)
        return 2
    if not isinstance(patch, dict):
        print("merge failed: patch_not_dict", file=sys.stderr)
        return 2
    core.deep_merge(ir, patch)
    core.stamp_change_log(ir, "apply patch %s: %s" % (os.path.basename(args.patch), core.summarize_patch(patch)), args.reason)
    core.save_ir(args.work, ir)
    problems = core.validate_ir(ir, "full")
    print("merge ok: %s; problems=%d" % (core.summarize_patch(patch), len(problems)))
    for p in problems:
        print("  - " + p)
    return 0


def cmd_stage(args):
    ok, summary, problems = core.stage_ir(args.work, args.output)
    if not ok:
        print("stage failed: %s" % problems, file=sys.stderr)
        return 2
    print("stage ok: %s; problems=%d" % (summary, len(problems)))
    for p in problems:
        print("  - " + p)
    return 0


def cmd_validate(args):
    ir = core.load_ir(args.work)
    if not ir:
        print("validate failed: no_ir_file", file=sys.stderr)
        return 2
    problems = core.validate_ir(ir, "full" if args.full else "skeleton")
    print("validate: %d problem(s)" % len(problems))
    for p in problems:
        print("  - " + p)
    return 0 if not problems else 1


def _load_papers_edges(args):
    ir = core.load_ir(args.work)
    if not ir:
        print("no_ir_file", file=sys.stderr)
        return None, None, None, None
    res = ir.get("results") or {}
    papers = res.get("papers") or []
    edges = res.get("edges") or []
    seed_id = (ir.get("seed") or {}).get("id")
    return ir, papers, edges, seed_id


def cmd_dedupe(args):
    ir, papers, edges, seed_id = _load_papers_edges(args)
    if ir is None:
        return 2
    if args.apply:
        core.apply_dedupe(ir)
        core.stamp_change_log(ir, "dedupe --apply 身份合并并重映射引用边", "去重与身份合并")
        core.save_ir(args.work, ir)
        merged = (ir.get("results") or {}).get("papers", [])
        evidence = [c for c in (ir.get("results") or {}).get("clusters", []) if c.get("type") == "identity_merge"]
        print("dedupe --apply: %d canonical papers, %d merge groups" % (len(merged), len(evidence)))
        for m in evidence:
            print("  merged %s <- %s" % (m.get("canonical_id"), ", ".join(m.get("members", []))))
        return 0
    merged, evidence = core.dedupe_papers(papers)
    print("dedupe (dry-run): %d input -> %d canonical" % (len(papers), len(merged)))
    for m in evidence:
        print("  merged %s <- %s" % (m["canonical_id"], ", ".join(m["members"])))
    return 0


def cmd_rank(args):
    ir, papers, edges, seed_id = _load_papers_edges(args)
    if ir is None:
        return 2
    ranked = core.rank_papers(list(papers), edges, seed_id)
    if args.apply:
        res = ir.setdefault("results", {})
        res["papers"] = ranked
        core.stamp_change_log(ir, "rank --apply 计算 distance/score 并排序", "排序/筛选")
        core.save_ir(args.work, ir)
        print("rank --apply: %d papers ranked" % len(ranked))
    else:
        print("rank (dry-run): top by distance/score")
        for p in ranked[:20]:
            print("  d=%s s=%s %s" % (p.get("distance"), p.get("score"), p.get("id")))
    return 0


def cmd_path(args):
    ir, papers, edges, seed_id = _load_papers_edges(args)
    if ir is None:
        return 2
    path = core.shortest_path(edges, args.src, args.dst)
    if path is None:
        print("path: no path from %s to %s" % (args.src, args.dst))
        return 1
    print("path length=%d: %s" % (path["length"], " -> ".join(str(n) for n in path["nodes"])))
    if args.apply:
        res = ir.setdefault("results", {})
        entry = {"from": args.src, "to": args.dst, "nodes": path["nodes"], "hops": path["hops"], "length": path["length"]}
        res["paths"] = core._dedup_append(list(res.get("paths") or []), [entry])
        core.stamp_change_log(ir, "记录路径 %s -> %s" % (args.src, args.dst), "路径解释")
        core.save_ir(args.work, ir)
        print("path --apply: written to results.paths")
    return 0


def cmd_selftest_hook(args):
    # 供自检脚本复用：直接调用 core 的函数，不依赖子进程。
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="citation-tracing Skill pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="扫描输入、落盘 IR 与 result.json 骨架")
    d.add_argument("--input", default="/input")
    d.add_argument("--output", default="/app/output")
    d.add_argument("--work", default="/app/work")
    d.set_defaults(func=cmd_discover)

    m = sub.add_parser("merge", help="把 patch JSON 增量并入 IR 并自动记 change_log")
    m.add_argument("--work", default="/app/work")
    m.add_argument("--patch", required=True)
    m.add_argument("--reason", default=None)
    m.set_defaults(func=cmd_merge)

    s = sub.add_parser("stage", help="校验 IR 并重写 result.json")
    s.add_argument("--work", default="/app/work")
    s.add_argument("--output", default="/app/output")
    s.set_defaults(func=cmd_stage)

    f = sub.add_parser("finalize", help="stage + 生成 report/CSV/SVG")
    f.add_argument("--work", default="/app/work")
    f.add_argument("--output", default="/app/output")
    f.set_defaults(func=cmd_finalize)

    v = sub.add_parser("validate", help="仅校验 IR 不写盘")
    v.add_argument("--work", default="/app/work")
    v.add_argument("--full", action="store_true")
    v.set_defaults(func=cmd_validate)

    dd = sub.add_parser("dedupe", help="dry-run 身份合并预览（--apply 写回 IR）")
    dd.add_argument("--work", default="/app/work")
    dd.add_argument("--apply", action="store_true")
    dd.set_defaults(func=cmd_dedupe)

    r = sub.add_parser("rank", help="dry-run 排序预览（--apply 写回 IR）")
    r.add_argument("--work", default="/app/work")
    r.add_argument("--apply", action="store_true")
    r.set_defaults(func=cmd_rank)

    ph = sub.add_parser("path", help="计算最短引用路径（--apply 写回 IR）")
    ph.add_argument("--work", default="/app/work")
    ph.add_argument("--from", dest="src", required=True)
    ph.add_argument("--to", dest="dst", required=True)
    ph.add_argument("--apply", action="store_true")
    ph.set_defaults(func=cmd_path)

    return p


def cmd_finalize(args):
    ok, summary, problems = core.finalize(args.work, args.output)
    if not ok:
        print("finalize failed: %s" % summary, file=sys.stderr)
        return 2
    print("finalize ok: %s; problems=%d" % (summary, len(problems)))
    for p in problems:
        print("  - " + p)
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())