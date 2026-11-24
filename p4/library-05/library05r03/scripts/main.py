#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 入口。

五命令主路径（评测 Agent 应照 SKILL.md 顺序执行）：

    python scripts/main.py init       # 扫描盘点 → work/library.json + output/result.json(partial) + draft 模板
    python scripts/main.py show       # 打印带 locator 的文件/分组/问题/错误清单，供模型阅读
    # 模型阅读 show 输出后，把语义裁定写进 /app/work/draft.json
    python scripts/main.py check      # 机械校验 draft.json（结构 + source_id 存在 + quote 命中）
    python scripts/main.py build      # 合并确定性结果与 draft，无条件收口
    python scripts/main.py selfcheck  # 只复检 /app/output 下产物

三个子命令均支持 --input/--work/--output 覆盖默认目录；默认路径也必须直接跑。
退出码恒为 0：真实问题写进 result.json 的 errors/degradations，不靠退出码表达。
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

import core_impl as core  # noqa: E402

VERSION = core.VERSION
DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

DRAFT_TEMPLATE = {
    "case_id": "",
    "notes": [],
    "queries": [],
}

DEGRADE_NOTES = {
    "no_draft": "缺少 work/draft.json：仅交付确定性盘点结果，语义裁定与检索答案未补齐。",
    "no_input": "输入目录不存在或为空。",
    "bad_case_json": "case.json 无法解析，已按 input_error 降级。",
}


# ---------------------------------------------------------------- 目录处理

def resolve_dirs(args) -> tuple:
    input_dir = Path(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    work_dir = Path(args.work) if getattr(args, "work", None) else DEFAULT_WORK
    output_dir = Path(args.output) if getattr(args, "output", None) else DEFAULT_OUTPUT
    for d in (work_dir, output_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return input_dir, work_dir, output_dir


def ensure_output(output_dir: Path, work_dir: Path) -> tuple:
    """确认输出目录可写；不可写则依次降级。返回 (实际目录, 降级说明)。"""
    for cand in (output_dir, work_dir / "output_fallback", SKILL_ROOT / "skill_output"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_probe"
            probe.write_text("", encoding="utf-8", newline="\n")
            if probe.exists():
                probe.unlink()
            if cand != output_dir:
                return cand, f"output_fallback:{cand.name}"
            return cand, None
        except OSError:
            continue
    return output_dir, "output_not_writable"


def _write_json(path: Path, obj) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def _read_json_loose(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _dedup_dicts(items: list) -> list:
    """按 (key,value) 对去重 dict，保持确定性顺序（按键排序）。"""
    seen = set()
    out = []
    for d in items:
        if not isinstance(d, dict):
            continue
        t = tuple(sorted((str(k), str(v)) for k, v in d.items()))
        if t in seen:
            continue
        seen.add(t)
        out.append(d)
    return out


# ---------------------------------------------------------------- 子命令

def cmd_init(input_dir, work_dir, output_dir) -> dict:
    t0 = time.time()
    library = core.scan(input_dir)
    contract_keys = library["contract_keys"]

    # 1) 写 work/library.json
    _write_json(work_dir / "library.json", library)

    # 2) 写 output/result.json 的 partial 骨架（前 3 分钟内落盘的第一版）
    skeleton = core.build_skeleton(library.get("case_id", "unknown"), contract_keys, "partial")
    skeleton["errors"] = list(library.get("errors", []))
    skeleton["degradations"] = []
    if not library.get("case_locator") and not library["files"]:
        skeleton["status"] = "insufficient_evidence"
        skeleton["degradations"].append({"kind": "no_input", "detail": DEGRADE_NOTES["no_input"]})
    elif library.get("case_error"):
        skeleton["status"] = "input_error"
        skeleton["degradations"].append({"kind": "bad_case_json", "detail": DEGRADE_NOTES["bad_case_json"]})
    else:
        skeleton["degradations"].append({"kind": "no_draft", "detail": DEGRADE_NOTES["no_draft"]})

    out_dir, fallback = ensure_output(output_dir, work_dir)
    if fallback:
        skeleton["degradations"].append({"kind": "output_fallback", "detail": fallback})
    _write_json(out_dir / "result.json", skeleton)

    # 3) 写 draft 模板
    tmpl = dict(DRAFT_TEMPLATE)
    tmpl["case_id"] = library.get("case_id", "unknown")
    tmpl["queries"] = [
        {"query_id": q["query_id"], "question": q["text"], "answer": "", "status": "no_direct_match",
         "confidence": "inferred", "evidence": []}
        for q in library.get("questions", [])
    ]
    _write_json(work_dir / "draft.json", tmpl)

    return {
        "case_id": library.get("case_id"),
        "files": len(library["files"]),
        "directories": len(library["directories"]),
        "contract_keys": contract_keys,
        "questions": len(library.get("questions", [])),
        "errors": len(library.get("errors", [])),
        "output_dir": str(out_dir),
        "elapsed": round(time.time() - t0, 2),
    }


def cmd_show(input_dir, work_dir, output_dir) -> None:
    library = _read_json_loose(work_dir / "library.json", None)
    if library is None:
        library = core.scan(input_dir)
        _write_json(work_dir / "library.json", library)

    print("=" * 72)
    print("CASE", library.get("case_id"))
    print("case_locator", library.get("case_locator"))
    print("task_locator", library.get("task_locator"))
    print("contract_keys", json.dumps(library.get("contract_keys", []), ensure_ascii=False))
    print("=" * 72)

    print("\n[DIRECTORIES] (%d)" % len(library.get("directories", [])))
    for d in library.get("directories", []):
        print("  ", d)

    print("\n[FILES] (%d)" % len(library.get("files", [])))
    for f in library.get("files", []):
        h = (f.get("sha256") or "-")[:12]
        print(f"  {f['id']}  kind={f['kind']}  mode={f.get('mode') or '-'}  "
              f"bytes={'' if f.get('bytes') is None else f.get('bytes')}  "
              f"text={'Y' if f.get('text_available') else 'N'}  sha256={h}")
        if f.get("unreadable_reason"):
            print(f"        UNAVAILABLE: {f['unreadable_reason']}")
        print(f"        rel={f['rel_path']}")

    a = library.get("analysis", {})
    print("\n[ALIAS/DUP GROUPS] (%d)" % len(a.get("alias_groups", [])))
    for g in a.get("alias_groups", []):
        print("  ", json.dumps(g, ensure_ascii=False))
    print("\n[VERSION GROUPS] (%d)" % len(a.get("version_groups", [])))
    for g in a.get("version_groups", []):
        print("  ", json.dumps(g, ensure_ascii=False))
    print("\n[CONFLICTS] (%d)" % len(a.get("conflicts", [])))
    for g in a.get("conflicts", []):
        print("  ", json.dumps(g, ensure_ascii=False))
    print("\n[EXPLICIT RELATIONS] (%d)" % len(a.get("explicit_relations", [])))
    for g in a.get("explicit_relations", []):
        print("  ", json.dumps(g, ensure_ascii=False))

    print("\n[META HINTS] (%d)" % len(library.get("meta_hints", [])))
    for h in library.get("meta_hints", []):
        print("  ", json.dumps(h, ensure_ascii=False))

    print("\n[QUESTIONS] (%d)" % len(library.get("questions", [])))
    for q in library.get("questions", []):
        print("  ", json.dumps(q, ensure_ascii=False))

    print("\n[ERRORS] (%d)" % len(library.get("errors", [])))
    for e in library.get("errors", []):
        print("  ", json.dumps(e, ensure_ascii=False))


def cmd_check(input_dir, work_dir, output_dir) -> dict:
    library = _read_json_loose(work_dir / "library.json", None)
    if library is None:
        library = core.scan(input_dir)
    draft = _read_json_loose(work_dir / "draft.json", None)

    report = {
        "has_draft": draft is not None,
        "structure": False,
        "evidence_checks": [],
        "errors": [],
        "warnings": [],
    }
    if not isinstance(draft, dict):
        report["errors"].append("draft 缺失或非 JSON 对象")
        _write_json(work_dir / "check_report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    notes = draft.get("notes", [])
    queries = draft.get("queries", [])
    if not isinstance(notes, list) or not isinstance(queries, list):
        report["errors"].append("draft.notes / draft.queries 必须是 list")
        _write_json(work_dir / "check_report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    report["structure"] = True
    valid_ids = {f["id"] for f in library["files"]} | {"case", "task"}

    def _check_evidence(ev):
        if not isinstance(ev, dict):
            return {"verdicts": {"source_id": False}, "bad": ["malformed_evidence"]}
        return core.verify_evidence(input_dir, library, ev)

    for i, note in enumerate(notes):
        note_id = note.get("target_ids") or note.get("targets") or []
        if isinstance(note_id, str):
            note_id = [note_id]
        for t in note_id:
            if t not in valid_ids:
                report["errors"].append(f"note[{i}] target '{t}' 不存在")
        for j, ev in enumerate(note.get("evidence", [])):
            res = _check_evidence(ev)
            if res["bad"]:
                report["errors"].append(f"note[{i}].evidence[{j}]: {res['bad']}")
            elif res["verdicts"].get("quote") == "no_text_layer":
                report["warnings"].append(f"note[{i}].evidence[{j}]: 无文本层，核心无法复验逐字命中")
            report["evidence_checks"].append(res)

    for i, q in enumerate(queries):
        for j, ev in enumerate(q.get("evidence", [])):
            res = _check_evidence(ev)
            if res["bad"]:
                report["errors"].append(f"query[{i}].evidence[{j}]: {res['bad']}")
            elif res["verdicts"].get("quote") == "no_text_layer":
                report["warnings"].append(f"query[{i}].evidence[{j}]: 无文本层，核心无法复验逐字命中")
            report["evidence_checks"].append(res)

    _write_json(work_dir / "check_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def cmd_build(input_dir, work_dir, output_dir) -> dict:
    t0 = time.time()
    library = _read_json_loose(work_dir / "library.json", None)
    # 为稳收口，library 缺失时重新扫描；确定性保证与 init 结果一致。
    if library is None:
        library = core.scan(input_dir)
        _write_json(work_dir / "library.json", library)

    draft = _read_json_loose(work_dir / "draft.json", None)
    contract_keys = library.get("contract_keys", [])

    degradations = list(library.get("degradations", []))
    extra_errors = []
    if draft is None:
        degradations.append({"kind": "no_draft", "detail": DEGRADE_NOTES["no_draft"]})

    out_dir, fallback = ensure_output(output_dir, work_dir)
    if fallback:
        degradations.append({"kind": "output_fallback", "detail": fallback})
    library["degradations"] = degradations

    runtime = {
        "engine_version": VERSION,
        "skill_name": core.SKILL_NAME,
        "elapsed_seconds": round(time.time() - t0, 3),
    }

    result = core.assemble_result(input_dir, library, draft, contract_keys, extra_errors, runtime)
    result["degradations"] = _dedup_dicts(result["degradations"])

    claims = result["claims"]
    library_index = core.build_library_index(library, draft)
    evidence_map = core.build_evidence_map(library, claims)
    inventory_md = core.build_inventory_md(library, result)

    _write_json(out_dir / "result.json", result)
    _write_json(out_dir / "library_index.json", library_index)
    _write_json(out_dir / "evidence_map.json", evidence_map)
    _write_text(out_dir / "inventory.md", inventory_md)

    print(json.dumps({
        "case_id": result["case_id"],
        "status": result["status"],
        "files": len(library["files"]),
        "claims": len(claims),
        "sources": len(result["sources"]),
        "pending_human": len(result["pending_human"]),
        "degradations": [d.get("kind") for d in result["degradations"]],
        "output_dir": str(out_dir),
    }, ensure_ascii=False, indent=2))
    return result


def cmd_selfcheck(input_dir, work_dir, output_dir) -> dict:
    out_dir = output_dir if (output_dir / "result.json").exists() else (work_dir / "output_fallback")
    checks = {}
    try:
        result = _read_json_loose(out_dir / "result.json", None)
        checks["result_parseable"] = result is not None
    except Exception:  # noqa: BLE001
        result = None
        checks["result_parseable"] = False

    checks["has_case_id"] = isinstance(result, dict) and bool(result.get("case_id"))
    checks["has_status"] = isinstance(result, dict) and result.get("status") in core.ALLOWED_STATUS
    checks["has_envelope"] = isinstance(result, dict) and all(k in result for k in ("sources", "uncertainty", "change_log"))

    library = _read_json_loose(work_dir / "library.json", None)
    if library is None:
        library = core.scan(input_dir)

    contract_keys = library.get("contract_keys", []) if isinstance(library, dict) else []
    checks["contract_fields"] = isinstance(result, dict) and all(k in result for k in contract_keys)

    artifacts = ["result.json", "library_index.json", "evidence_map.json", "inventory.md"]
    checks["artifacts"] = {}
    for name in artifacts:
        p = out_dir / name
        checks["artifacts"][name] = p.exists() and p.stat().st_size > 0 if p.exists() else False

    ev_map = _read_json_loose(out_dir / "evidence_map.json", None)
    ev_ok = isinstance(ev_map, dict) and "claims" in ev_map
    checks["evidence_map_structure"] = ev_ok
    checks["passed"] = bool(checks.get("result_parseable") and checks.get("has_case_id")
                            and checks.get("has_status") and checks.get("has_envelope")
                            and checks.get("contract_fields") and ev_ok)

    _write_json(work_dir / "selfcheck_report.json", checks)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return checks


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研文档库修复 Skill（library-doc-repair）：盘点、去重、版本/别名、检索与交接。",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("init", "show", "check", "build", "selfcheck"):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default=None)
        sp.add_argument("--work", default=None)
        sp.add_argument("--output", default=None)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    input_dir, work_dir, output_dir = resolve_dirs(args)
    cmd = args.cmd
    try:
        if cmd == "init":
            report = cmd_init(input_dir, work_dir, output_dir)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif cmd == "show":
            cmd_show(input_dir, work_dir, output_dir)
        elif cmd == "check":
            cmd_check(input_dir, work_dir, output_dir)
        elif cmd == "build":
            cmd_build(input_dir, work_dir, output_dir)
        elif cmd == "selfcheck":
            cmd_selfcheck(input_dir, work_dir, output_dir)
        else:
            print("unknown command", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 —— 健壮性：任何异常都降级为记录而非崩溃
        err = {"kind": "unhandled_" + cmd, "detail": str(e)}
        try:
            out_dir, _ = ensure_output(output_dir, work_dir)
            skeleton = core.build_skeleton("unknown", [], "partial")
            skeleton["errors"] = [err]
            skeleton["degradations"] = [{"kind": "fatal_recovered", "detail": str(e)}]
            _write_json(out_dir / "result.json", skeleton)
        except Exception:  # noqa: BLE001
            pass
        print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())