#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 的命令行入口（五个子命令 + run 基线）。

推荐执行顺序（Agent 按 SKILL.md 执行）：

    python scripts/main.py init      # 扫描盘点 → work/scan.json + partial result.json + draft 模板
    python scripts/main.py show      # 打印带 locator 的盘点/分组/特殊文件/问题
    # Agent 阅读 show 输出后，把语义判断写进 /app/work/draft.json
    python scripts/main.py check     # 机械校验 draft（结构 + source 存在 + quote 命中）
    python scripts/main.py build     # 合并确定性结果与 draft，无条件收口交付
    python scripts/main.py selfcheck # 复检 /app/output 产物

所有子命令支持 --input/--work/--output 覆盖默认目录；默认路径（/input、/app/work、
/app/output）也必须可直接运行。退出码恒为 0：真实问题写入 errors/degradations。
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

import libcore as C  # noqa: E402

DRAFT_TEMPLATE = {
    "case_id": "",
    "notes": [],
    "queries": [],
    "relations": [],
}

ARTIFACTS = ("library_index.json", "evidence_map.json", "inventory.md")


# ---------------------------------------------------------------- 命令行

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", help="输入目录（默认 /input）")
    common.add_argument("--work", help="工作目录（默认 /app/work）")
    common.add_argument("--output", help="输出目录（默认 /app/output）")

    p = argparse.ArgumentParser(
        prog="main.py",
        parents=[common],
        description=f"科研文档库修复 Skill 执行入口 (engine={C.ENGINE_ID} v{C.SKILL_VERSION})",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common], help="扫描盘点并写 partial result 骨架与 draft 模板")
    sub.add_parser("show", parents=[common], help="打印带 locator 的盘点/分组/特殊文件/检索问题")
    sub.add_parser("check", parents=[common], help="机械校验 work/draft.json")
    sub.add_parser("build", parents=[common], help="合并且无条件收口交付")
    sub.add_parser("selfcheck", parents=[common], help="复检 /app/output 产物")
    sub.add_parser("run", parents=[common], help="确定性基线：init + 检索 + build + selfcheck（无需 draft）")
    return p


# ---------------------------------------------------------------- 子命令

def cmd_init(input_dir: Path, work_dir: Path, output_dir: Path) -> None:
    started = time.time()
    scan = C.build_scan_payload(input_dir)
    C.write_json(work_dir / "scan.json", scan)
    C.write_json(work_dir / "draft.json", DRAFT_TEMPLATE)

    case, case_err = C.load_case(input_dir)
    extra_errors = []
    if case_err:
        extra_errors.append({"code": "case_unreadable", "detail": case_err})
    degrade = ["no_draft"]  # 骨架阶段尚无模型 draft
    result = C.make_result(case, scan, None, {}, degrade, extra_errors)
    result[C.RUNTIME_KEY] = {
        "phase": "init", "started": started, "ended": time.time(),
        "duration_sec": round(time.time() - started, 3),
    }
    C.write_json(output_dir / "result.json", result)
    print(f"[init] files={len(scan.get('files', []))} "
          f"partial={output_dir / 'result.json'}")


def cmd_show(input_dir: Path, work_dir: Path, output_dir: Path) -> None:
    scan_path = work_dir / "scan.json"
    scan, err = C.read_json_tolerant(scan_path)
    if err or not isinstance(scan, dict):
        scan = C.build_scan_payload(input_dir)
    files = scan.get("files", [])
    analysis = scan.get("analysis", {})

    print("== scan summary ==")
    print(C.summarize_scan(scan))
    print()

    print("== files ==")
    for f in files:
        sha = (f.get("sha256") or "")[:C.SHA_PREFIX]
        flags = ",".join(f.get("flags", []))
        print(f"{f['id']}  {f['rel_path']}  size={f.get('size')}  "
              f"sha256={sha or '-'}  kind={f.get('kind')}  enc={f.get('encoding')}  "
              f"mode={f.get('mode_oct') or '-'}" + (f"  [{flags}]" if flags else ""))

    print()
    print("== deterministic groups ==")
    print("alias_groups (sha256 相同):")
    for g in analysis.get("alias_groups", []):
        print("  " + ", ".join(g))
    print("version_groups (stem 相同、哈希不同、带版本标记):")
    for g in analysis.get("version_groups", []):
        print("  " + ", ".join(g))
    print("conflicts (stem 相同、哈希不同、无版本标记):")
    for g in analysis.get("conflicts", []):
        print("  " + ", ".join(g))

    print()
    print("== input manifests ==")
    for sid, src in scan.get("sources", {}).items():
        print(f"--- {sid} ({src.get('rel_path')}) sha256={ (src.get('sha256') or '')[:C.SHA_PREFIX] } ---")
        text = src.get("text") or ""
        print(text[:C.PREVIEW_CAP * 3])
        if len(text) > C.PREVIEW_CAP * 3:
            print(f"... truncated ({len(text)} chars total)")

    print()
    print("== special file contents (metadata/notes/relations/workorder) ==")
    special_ids = set(scan.get("special_file_ids", []))
    for f in files:
        if f["id"] in special_ids and f.get("text"):
            print(f"--- {f['id']} {f['rel_path']} ---")
            print(f["text"][:C.PREVIEW_CAP * 2])
            if len(f["text"]) > C.PREVIEW_CAP * 2:
                print(f"... truncated ({len(f['text'])} chars total)")

    case, _ = C.load_case(input_dir)
    queries = C.extract_queries(case)
    if queries:
        print()
        print("== query candidates from case.json ==")
        for qid, text in queries:
            print(f"{qid}: {text}")


def cmd_check(work_dir: Path) -> None:
    scan, serr = C.read_json_tolerant(work_dir / "scan.json")
    draft, derr = C.read_json_tolerant(work_dir / "draft.json")
    errors = []
    if serr:
        errors.append({"code": "scan_unreadable", "detail": serr})
        scan = {"files": [], "sources": {}, "analysis": {}}
    if derr:
        errors.append({"code": "draft_unreadable", "detail": derr})
        draft = None
    report = C.validate_draft(draft, scan)
    report["ok"] = report["ok"] and not errors
    report["errors"] = errors + report.get("errors", [])
    C.write_json(work_dir / "check_report.json", report)
    print(f"[check] ok={report['ok']} errors={len(report['errors'])} "
          f"warnings={len(report['warnings'])} counts={report['counts']}")


def cmd_build(input_dir: Path, work_dir: Path, output_dir: Path,
              skill_root: Path) -> None:
    started = time.time()
    scan, serr = C.read_json_tolerant(work_dir / "scan.json")
    if serr or not isinstance(scan, dict):
        scan = C.build_scan_payload(input_dir)
    draft, derr = C.read_json_tolerant(work_dir / "draft.json")
    degrade = []
    extra_errors = []
    if derr:
        extra_errors.append({"code": "draft_unreadable", "detail": derr})
        draft = None
        degrade.append("no_draft")
    if not (isinstance(draft, dict) and (draft.get("notes") or draft.get("relations")
                                         or draft.get("queries"))):
        degrade.append("no_draft")

    out_dir, degrade_note = C.ensure_output(output_dir, work_dir, skill_root)
    if degrade_note:
        degrade.append(degrade_note)

    files = scan.get("files", [])
    analysis = scan.get("analysis", {})

    # library_index.json：文件元数据（去文本层，只留预览）+ 分析
    index_files = []
    for f in files:
        fr = {k: v for k, v in f.items() if k != "text"}
        if f.get("text"):
            fr["preview"] = f["text"][:240]
        index_files.append(fr)
    library_index = {
        "engine": C.ENGINE_ID,
        "task_label": C.TASK_LABEL,
        "file_count": len(files),
        "total_bytes": sum((f.get("size") or 0) for f in files),
        "files": index_files,
        "analysis": analysis,
        "special_file_ids": scan.get("special_file_ids", []),
    }

    case, case_err = C.load_case(input_dir)
    if case_err:
        extra_errors.append({"code": "case_unreadable", "detail": case_err})
    result = C.make_result(case, scan, draft,
                           {"library_index": "library_index.json",
                            "evidence_map": "evidence_map.json",
                            "inventory": "inventory.md"},
                           degrade, extra_errors)

    evidence_map = {
        "engine": C.ENGINE_ID,
        "case_id": result.get("case_id"),
        "claims": result.get("claims", []),
        "map": {c.get("claim_id"): c.get("evidence", [])
                for c in result.get("claims", [])},
    }
    inventory_md_lines = ["# 科研文档库盘点交接清单", ""]
    inventory_md_lines.append(f"- engine: {C.ENGINE_ID} v{C.SKILL_VERSION}")
    inventory_md_lines.append(f"- case_id: {result.get('case_id')}")
    inventory_md_lines.append(f"- status: {result.get('status')}")
    inventory_md_lines.append(f"- files: {len(files)}")
    inventory_md_lines.append("")
    inventory_md_lines.append("## 文件清单")
    for f in files:
        sha = (f.get("sha256") or "")[:C.SHA_PREFIX]
        inventory_md_lines.append(f"- {f['id']} {f['rel_path']} "
                                  f"({f.get('kind')}, {f.get('size')} B, sha256={sha or '-'})")
    inventory_md_lines.append("")
    inventory_md_lines.append("## 分组")
    inventory_md_lines.append(f"- alias_groups: {analysis.get('alias_groups', [])}")
    inventory_md_lines.append(f"- version_groups: {analysis.get('version_groups', [])}")
    inventory_md_lines.append(f"- conflicts: {analysis.get('conflicts', [])}")
    if result.get("pending_human"):
        inventory_md_lines.append("")
        inventory_md_lines.append("## 待人工确认")
        for p in result["pending_human"]:
            inventory_md_lines.append(f"- {p.get('subject')}: {p.get('reason')}")

    result[C.RUNTIME_KEY] = {
        "phase": "build", "started": started, "ended": time.time(),
        "duration_sec": round(time.time() - started, 3),
    }

    C.write_json(out_dir / "result.json", result)
    C.write_json(out_dir / "library_index.json", library_index)
    C.write_json(out_dir / "evidence_map.json", evidence_map)
    (out_dir / "inventory.md").write_text("\n".join(inventory_md_lines) + "\n",
                                          encoding="utf-8")
    print(f"[build] status={result['status']} out={out_dir / 'result.json'} "
          f"degrade={degrade}")


def cmd_selfcheck(output_dir: Path) -> None:
    problems = []
    res, err = C.read_json_tolerant(output_dir / "result.json")
    if err or not isinstance(res, dict):
        problems.append(f"result.json 不可解析：{err or '非对象'}")
    else:
        if not res.get("case_id"):
            problems.append("result.json 缺 case_id")
        if res.get("status") not in C.ALLOWED_STATUS:
            problems.append(f"status 非法：{res.get('status')}")
        for key in ("sources", "uncertainty", "change_log", "claims", "errors", "degradations"):
            if key not in res:
                problems.append(f"result.json 缺信封字段 {key}")
    for name in ARTIFACTS:
        p = output_dir / name
        if not p.exists() or p.stat().st_size == 0:
            problems.append(f"{name} 缺失或为空")
    print(f"[selfcheck] {'ok' if not problems else 'problems=' + str(len(problems))}")
    for x in problems:
        print("  -", x)


def cmd_run(input_dir: Path, work_dir: Path, output_dir: Path,
            skill_root: Path) -> None:
    """确定性基线：无 draft 也不依赖模型，跑完整盘点 + 关键词检索 + 交付。"""
    cmd_init(input_dir, work_dir, output_dir)
    cmd_build(input_dir, work_dir, output_dir, skill_root)
    cmd_selfcheck(output_dir)


# ---------------------------------------------------------------- 入口

def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    input_dir, work_dir, output_dir = C.resolve_dirs(
        getattr(args, "input", None) or None,
        getattr(args, "work", None) or None,
        getattr(args, "output", None) or None,
    )
    try:
        if args.command == "init":
            cmd_init(input_dir, work_dir, output_dir)
        elif args.command == "show":
            cmd_show(input_dir, work_dir, output_dir)
        elif args.command == "check":
            cmd_check(work_dir)
        elif args.command == "build":
            cmd_build(input_dir, work_dir, output_dir, SKILL_ROOT)
        elif args.command == "selfcheck":
            cmd_selfcheck(output_dir)
        elif args.command == "run":
            cmd_run(input_dir, work_dir, output_dir, SKILL_ROOT)
        else:  # pragma: no cover
            print(f"[warn] 未知子命令 {args.command}")
    except Exception as exc:  # 兜底：永远不靠退出码表达失败
        print(f"[error] {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())