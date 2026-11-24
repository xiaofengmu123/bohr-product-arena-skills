#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 的命令行入口。

五命令主路径（评测 Agent 按 SKILL.md 顺序执行）：

    python scripts/main.py init       # 扫描盘点 + 落盘 result.json 骨架 + draft 模板
    python scripts/main.py show       # 打印带 locator 的盘点/分组/问题，供模型阅读
    # 模型阅读 show 输出后，把语义判断写进 /app/work/draft.json
    python scripts/main.py check      # 机械校验 draft.json（结构 + 溯源逐字命中）
    python scripts/main.py build      # 合并确定性结果与 draft，无条件收口交付
    python scripts/main.py selfcheck  # 只复检 /app/output 下产物

子命令支持 --input/--work/--output 覆盖默认目录；默认路径也能直接运行。
退出码恒为 0：真实问题写进 result.json 的 errors/degradations/limitations，不靠退出码表达。
重逻辑在 core.py（盘点/分组/检索/组装/校验），本文件只做目录、输入读取与命令编排。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import core  # noqa: E402

SKILL_VERSION = "1.1.0"
SKILL_NAME = "library-doc-repair"
DELIVERABLE = "library_index.json"

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")


# --------------------------------------------------------------------------- 目录

def resolve_dirs(args) -> tuple:
    input_dir = Path(getattr(args, "input", None) or DEFAULT_INPUT)
    work_dir = Path(getattr(args, "work", None) or DEFAULT_WORK)
    output_dir = Path(getattr(args, "output", None) or DEFAULT_OUTPUT)
    for d in (work_dir, output_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return input_dir, work_dir, output_dir


def ensure_output(output_dir: Path, work_dir: Path) -> tuple:
    for cand in (output_dir, work_dir / "output_fallback", SKILL_ROOT / "skill_output"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            if cand != output_dir:
                return cand, f"output_fallback:{cand.name}"
            return cand, None
        except OSError:
            continue
    return output_dir, "output_not_writable"


# --------------------------------------------------------------------------- 输入读取

def read_input(input_dir: Path) -> dict:
    """读取 case.json 与 task.md，坏 JSON/坏编码降级不崩溃。"""
    out = {"case": None, "case_text": None, "task_text": None, "case_error": None,
           "case_missing": False, "task_missing": False}
    case_path = input_dir / "case.json"
    if case_path.exists():
        try:
            raw = case_path.read_bytes()
            text, _enc = core.decode_text(raw)
            out["case_text"] = text
            out["case"] = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            out["case_error"] = f"{type(exc).__name__}:{exc}"
            out["case"] = None
    else:
        out["case_missing"] = True
        out["case_error"] = "case.json missing"
    task_path = input_dir / "task.md"
    if task_path.exists():
        try:
            raw = task_path.read_bytes()
            out["task_text"], _enc = core.decode_text(raw)
        except OSError as exc:
            out["task_text"] = None
    else:
        out["task_missing"] = True
    return out


def probe_capabilities() -> dict:
    tools = {}
    for name in ("pdftotext", "pdftoppm", "pdftohtml", "tesseract", "soffice"):
        tools[name] = shutil.which(name) is not None
    return {"external_tools": tools, "stdlib_only": True}


def summarize_metadata(files: list, case) -> dict:
    types: dict = {}
    parents: dict = {}
    readable = 0
    total_size = 0
    for f in files:
        types[f["type"]] = types.get(f["type"], 0) + 1
        parent = Path(f["rel_path"]).parent.as_posix()
        parents[parent] = parents.get(parent, 0) + 1
        readable += 1 if f["readable"] else 0
        total_size += f["size"]
    case_meta = {}
    if isinstance(case, dict):
        for k, v in case.items():
            if k in ("output_contract", "content"):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                case_meta[str(k)] = v
    return {
        "file_count": len(files),
        "total_size": total_size,
        "readable_count": readable,
        "types": {k: types[k] for k in sorted(types)},
        "directories": {k: parents[k] for k in sorted(parents)},
        "case_fields": case_meta,
    }


# --------------------------------------------------------------------------- 库组装

def assemble_library(input_dir: Path, read: dict) -> dict:
    case = read["case"]
    files, scan_errors = core.build_file_records(input_dir)
    case_id = core.derive_case_id(case) if case else "unknown"
    contract_keys = core.extract_contract(case)
    queries = core.extract_queries(case, read["task_text"])
    meta_ids = {f["id"] for f in files if f["is_meta"]}
    alias_groups = core._group_by_hash(files)
    version_groups, conflicts = core._group_by_stem(files, meta_ids)
    relations = core.extract_explicit_relations(files, input_dir)
    index = core.build_index(files, relations, input_dir)
    caps = probe_capabilities()
    metadata = summarize_metadata(files, case)

    # 坏 JSON 或缺 case.json 时，case 为 None → input_error；否则正常。
    input_error = read["case_error"] is not None and case is None

    query_candidates = {q["query_id"]: core.retrieve(q["question"], index, top_n=8)
                        for q in queries}

    change_log = [
        {"op": "scan", "before": None, "after": f"found {len(files)} files",
         "reason": "deterministic walk of input dir", "locator": str(input_dir)},
        {"op": "hash", "before": None, "after": f"{sum(1 for f in files if f['sha256'])} files hashed",
         "reason": "sha256 streaming", "locator": str(input_dir)},
        {"op": "group", "before": None,
         "after": f"{len(alias_groups)} alias, {len(version_groups)} version, {len(conflicts)} conflict, {len(relations)} relation",
         "reason": "stem/hash/pattern rules", "locator": str(input_dir)},
    ]

    return {
        "case_id": case_id,
        "root": str(input_dir),
        "contract_keys": contract_keys,
        "input_error": input_error,
        "case_error": read["case_error"],
        "case_missing": read["case_missing"],
        "task_missing": read["task_missing"],
        "files": files,
        "scan_errors": scan_errors,
        "queries": queries,
        "analysis": {
            "alias_groups": alias_groups,
            "version_groups": version_groups,
            "conflicts": conflicts,
            "explicit_relations": relations,
        },
        "index": {k: v for k, v in index.items() if k != "corpus"},
        "query_candidates": query_candidates,
        "capabilities": caps,
        "metadata": metadata,
        "change_log": change_log,
    }


# --------------------------------------------------------------------------- 命令

def run_init(input_dir: Path, work_dir: Path, output_dir: Path, started: float) -> dict:
    read = read_input(input_dir)
    library = assemble_library(input_dir, read)
    work_dir.mkdir(parents=True, exist_ok=True)
    core.to_json(library, work_dir / "library.json")
    core.to_json({
        "case_id": library["case_id"],
        "contract_keys": library["contract_keys"],
        "status": "partial",
        "instructions": "阅读 show 输出后，把语义判断写进 work/draft.json（结构见 SKILL.md）",
    }, work_dir / "meta.json")
    draft_template = {
        "case_id": library["case_id"],
        "notes": [],
        "queries": [
            {"query_id": q["query_id"], "question": q["question"], "answer": "",
             "status": "insufficient_evidence", "evidence": []}
            for q in library["queries"]
        ],
        "contract_values": {},
    }
    core.to_json(draft_template, work_dir / "draft.json")

    output_dir, fb = ensure_output(output_dir, work_dir)
    if fb:
        library["change_log"].append({"op": "output_fallback", "before": None,
                                      "after": str(output_dir), "reason": fb, "locator": str(output_dir)})
    # 初始骨架状态：正常案例为 partial（等 draft），退化案例如实反映。
    initial_status = core.determine_status(library, None)
    skeleton = core.build_skeleton(library["case_id"], library["contract_keys"], initial_status)
    core.fill_deterministic(library, skeleton)
    skeleton["runtime"] = {"started": int(started), "command": "init"}
    core.to_json(skeleton, output_dir / "result.json")
    return library


def run_build(input_dir: Path, work_dir: Path, output_dir: Path, started: float) -> dict:
    library_path = work_dir / "library.json"
    if not library_path.exists():
        library = assemble_library(input_dir, read_input(input_dir))
    else:
        library = core.load_json(library_path)
    draft = None
    no_draft = False
    draft_path = work_dir / "draft.json"
    if draft_path.exists():
        try:
            draft = core.load_json(draft_path)
        except (OSError, json.JSONDecodeError):
            draft = None
    if isinstance(draft, dict):
        meaningful = bool(draft.get("notes")) or \
            any(q.get("answer") for q in (draft.get("queries") or [])) or \
            bool(draft.get("contract_values"))
        if not meaningful:
            draft = None
            no_draft = True
    else:
        draft = None
    read = read_input(input_dir)
    runtime = {
        "started": int(started),
        "finished": int(time.time()),
        "wall_seconds": round(time.time() - started, 3),
        "skill_version": SKILL_VERSION,
        "commands": ["init", "show", "check", "build", "selfcheck"],
    }
    result = core.build_result(library, draft, runtime)
    if no_draft:
        result["degradations"].append({
            "kind": "no_draft",
            "detail": "draft.json 为空或未填写语义判断，build 仅合并确定性盘点，status 已降级为 partial",
        })
    output_dir, fb = ensure_output(output_dir, work_dir)
    if fb:
        result["degradations"].append({"kind": "output_fallback", "detail": fb})
    files_by_id = {f["id"]: f for f in library["files"]}
    emap = core.write_evidence_map(result, files_by_id, input_dir, read["case_text"], read["task_text"])
    lib_index = {
        "case_id": library["case_id"],
        "files": library["files"],
        "analysis": library["analysis"],
        "index": library["index"],
        "capabilities": library["capabilities"],
        "metadata": library["metadata"],
    }
    core.to_json(lib_index, output_dir / DELIVERABLE)
    core.to_json(emap, output_dir / "evidence_map.json")
    (output_dir / "inventory.md").write_text(
        core.write_inventory_md(library), encoding="utf-8", newline="\n")
    core.to_json(result, output_dir / "result.json")
    return result


def run_selfcheck(output_dir: Path) -> dict:
    report = {"ok": True, "items": []}
    result_path = output_dir / "result.json"
    if not result_path.exists():
        return {"ok": False, "items": [{"id": "result.json", "ok": False, "problem": "missing"}]}
    try:
        result = core.load_json(result_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "items": [{"id": "result.json", "ok": False,
                                        "problem": f"unparseable: {exc}"}]}
    for key in ("case_id", "status", "sources", "uncertainty", "change_log", "limitations"):
        if key not in result:
            report["items"].append({"id": f"result.{key}", "ok": False, "problem": "missing"})
            report["ok"] = False
    if result.get("status") not in core.ALLOWED_STATUS:
        report["items"].append({"id": "result.status", "ok": False, "problem": "bad status"})
        report["ok"] = False
    for name in (DELIVERABLE, "evidence_map.json", "inventory.md"):
        p = output_dir / name
        if not p.exists() or p.stat().st_size == 0:
            report["items"].append({"id": name, "ok": False, "problem": "missing/empty"})
            report["ok"] = False
    if result.get("status") == "ok" and result.get("pending_human"):
        report["items"].append({"id": "status", "ok": False,
                                "problem": "status=ok but pending_human non-empty"})
        report["ok"] = False
    if report["ok"]:
        report["items"].append({"id": "selfcheck", "ok": True, "problem": "all artifacts present"})
    return report


# --------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="main.py", description="科研文档库修复 Skill")
    p.add_argument("command", choices=["init", "show", "check", "build", "selfcheck"])
    p.add_argument("--input", default=None, help="输入目录（默认 /input）")
    p.add_argument("--work", default=None, help="工作目录（默认 /app/work）")
    p.add_argument("--output", default=None, help="输出目录（默认 /app/output）")
    return p


def dispatch(args) -> int:
    input_dir, work_dir, output_dir = resolve_dirs(args)
    started = time.time()
    try:
        if args.command == "init":
            run_init(input_dir, work_dir, output_dir, started)
            print("init: partial result.json written")
        elif args.command == "show":
            lp = work_dir / "library.json"
            if lp.exists():
                print(core.render_show(core.load_json(lp)))
            else:
                print(core.render_show(assemble_library(input_dir, read_input(input_dir))))
        elif args.command == "check":
            lp = work_dir / "library.json"
            if not lp.exists():
                print("check: run init first")
                return 0
            library = core.load_json(lp)
            read = read_input(input_dir)
            dp = work_dir / "draft.json"
            draft = core.load_json(dp) if dp.exists() else None
            report = core.check_draft(draft, library, input_dir, read["case_text"], read["task_text"])
            core.to_json(report, work_dir / "check_report.json")
            print("check:", "PASS" if report["ok"] else "FAIL", "-", report["summary"])
        elif args.command == "build":
            result = run_build(input_dir, work_dir, output_dir, started)
            print(f"build: status={result['status']} -> {output_dir / 'result.json'}")
        elif args.command == "selfcheck":
            report = run_selfcheck(output_dir)
            core.to_json(report, output_dir / "selfcheck_report.json")
            print("selfcheck:", "PASS" if report["ok"] else "FAIL")
    except Exception as exc:  # 兜底：不崩溃，退出码仍 0
        print(f"error (degraded): {type(exc).__name__}: {exc}")
        return 0
    return 0


def cli(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return dispatch(args)


if __name__ == "__main__":
    sys.exit(cli())
