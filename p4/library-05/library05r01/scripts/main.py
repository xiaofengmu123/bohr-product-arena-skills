#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文档库修复 Skill 的执行入口（五命令主路径，纯标准库）。

按 SKILL.md 顺序执行：

    python scripts/main.py init      # 扫描盘点，写 partial result.json 骨架 + work/library.json + draft 模板
    python scripts/main.py show      # 打印带 locator 的文件/分组/问题/错误清单，供模型阅读
    # 模型阅读 show 输出后，把语义判断写进 <work>/draft.json
    python scripts/main.py check     # 机械校验 draft.json（结构 + source_id 存在 + quote 逐字命中）
    python scripts/main.py build     # 合并确定性结果与 draft，无条件收口交付
    python scripts/main.py selfcheck # 只复检 <output> 下产物是否齐全且可解析

三个子命令均支持 --input/--work/--output 覆盖默认目录；默认路径也能直接运行。
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

import libcore  # noqa: E402
import rules  # noqa: E402

PROFILE = "business"
TASK_LABEL = "library-05-document-repair"
SKILL_VERSION = "1.0.0"
DELIVERABLE_NAME = "library_index.json"

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")


def _resolve_dirs(args) -> tuple:
    """解析 --input/--work/--output，缺省指向运行时目录。"""
    input_dir = Path(args.input) if getattr(args, "input", None) else DEFAULT_INPUT
    work_dir = Path(args.work) if getattr(args, "work", None) else DEFAULT_WORK
    output_dir = Path(args.output) if getattr(args, "output", None) else DEFAULT_OUTPUT
    return input_dir, work_dir, output_dir


def _ensure_writable(work_dir: Path, output_dir: Path) -> tuple:
    """确认 work/output 可写；不可写则降级到 work 下 fallback。返回 (实际 output, 降级说明)。"""
    degradations = []
    for d in (work_dir, output_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            degradations.append(f"mkdir_failed:{d}:{exc}")
    actual_output = output_dir
    if not _writable(actual_output):
        actual_output = work_dir / "output_fallback"
        degradations.append(f"output_fallback:{actual_output.name}")
    try:
        actual_output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        degradations.append(f"fallback_mkdir_failed:{exc}")
    return actual_output, degradations


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _write_json(path: Path, obj) -> None:
    """确定性写 JSON（无时间戳、稳定 key 顺序、ensure_ascii=False）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_library(work_dir: Path):
    try:
        return json.loads((work_dir / "library.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _conflict_resolved(conflicts: list, case: dict, draft: dict) -> bool:
    """检查冲突是否已被显式关系（case.json 或 draft）覆盖。

    覆盖判定：冲突的 stem 或成员文件出现在显式关系记录的字符串表示中。
    全部覆盖返回 True；否则保留 partial 并留给 pending_human 人工裁定。
    """
    if not conflicts:
        return True
    explicit_texts = []
    if isinstance(case, dict):
        for key in ("relations", "explicit_relations", "aliases", "version_notes"):
            explicit_texts.append(json.dumps(case.get(key), ensure_ascii=False))
    if isinstance(draft, dict):
        for note in draft.get("notes", []):
            if isinstance(note, dict) and note.get("kind") in ("version", "alias"):
                explicit_texts.append(json.dumps(note, ensure_ascii=False))
    combined = " ".join(t for t in explicit_texts if t)
    for g in conflicts:
        tokens = [g.get("stem", "")] + g.get("member_ids", [])
        if not any(t and t in combined for t in tokens):
            return False
    return True


def _determine_status(library: dict, case: dict, draft: dict) -> str:
    """根据材料、case 解析与 draft 状态诚实判定 status。"""
    no_draft = rules.is_template_draft(draft)
    # 无任何文件（目录为空）→ insufficient_evidence
    if not library.get("files"):
        return "insufficient_evidence"
    # 有材料但缺 case.json → input_error（仍盘点材料）
    if not library.get("case_json_present"):
        return "input_error"
    # case.json 存在但坏 JSON / 不可解析 → input_error
    if library.get("case_parse") != "ok":
        return "input_error"
    if no_draft:
        return "partial"
    analysis = library.get("analysis", {})
    # 冲突未显式覆盖 → 保持 partial，避免把未裁定当成完成
    if analysis.get("conflicts") and not _conflict_resolved(analysis.get("conflicts"), case, draft):
        return "partial"
    queries = analysis.get("queries", [])
    draft_queries = draft.get("queries", []) if isinstance(draft, dict) else []
    if queries and draft_queries and all(
        isinstance(q, dict) and q.get("status") in ("answered", "no_direct_match")
        for q in draft_queries
    ):
        return "ok"
    return "partial"


def cmd_init(args) -> int:
    input_dir, work_dir, output_dir = _resolve_dirs(args)
    actual_output, degradations = _ensure_writable(work_dir, output_dir)
    t0 = time.monotonic()

    case = libcore.load_case(input_dir)
    library = libcore.build_library(input_dir, case)

    case_id = rules.derive_case_id(case, library)
    contract_keys = libcore.extract_contract(case) if isinstance(case, dict) else []

    # 落盘 library.json（确定性盘点）
    _write_json(work_dir / "library.json", library)

    # 落盘 draft.json 模板（模型后续填充）
    template = dict(rules.DRAFT_TEMPLATE)
    template["case_id"] = case_id
    _write_json(work_dir / "draft.json", template)

    # 初始状态：先落骨架，模型后续增量改进
    status = _determine_status(library, case, template)

    # 落盘 partial result.json 骨架（前 3 分钟内必须已有合法骨架）
    skeleton = rules.build_result(case_id, contract_keys, status)
    skeleton["result"] = {
        "summary": {"file_count": len(library.get("files", []))},
        "partial": True,
    }
    skeleton["sources"] = rules.build_sources(library)
    skeleton["errors"] = library.get("errors", [])
    skeleton["degradations"] = degradations + ([library.get("case_parse")] if library.get("case_parse") != "ok" else [])
    skeleton["runtime"] = {
        "deterministic": True,
        "init_seconds": round(time.monotonic() - t0, 3),
        "contract_keys": len(contract_keys),
    }
    _write_json(actual_output / "result.json", skeleton)

    print(json.dumps({
        "status": status,
        "case_id": case_id,
        "contract_keys": contract_keys,
        "file_count": len(library.get("files", [])),
        "work": str(work_dir / "library.json"),
        "output": str(actual_output / "result.json"),
        "degradations": degradations,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args) -> int:
    input_dir, work_dir, output_dir = _resolve_dirs(args)
    library = _load_library(work_dir)
    if library is None:
        print("ERROR: 未找到 work/library.json，请先运行 init")
        return 0
    files = library.get("files", [])
    analysis = library.get("analysis", {})
    print("== files (%d) ==" % len(files))
    for f in files:
        print(f"{f['id']}\t{f['rel_path']}\t{f['kind']}\t{f['size']}\t"
              f"sha={ (f['sha256'] or '')[:12] }\t{f['hash_status']}\tperms={f['perms']}")
    print("\n== alias_groups (sha256 相同) ==")
    for g in analysis.get("alias_groups", []):
        print(json.dumps(g, ensure_ascii=False))
    print("\n== version_groups (归一化 stem 相同, 同哈希) ==")
    for g in analysis.get("version_groups", []):
        print(json.dumps(g, ensure_ascii=False))
    print("\n== conflicts (同 stem 不同哈希) ==")
    for g in analysis.get("conflicts", []):
        print(json.dumps(g, ensure_ascii=False))
    print("\n== explicit_relations (来自 case.json) ==")
    for r in analysis.get("explicit_relations", []):
        print(json.dumps(r, ensure_ascii=False))
    print("\n== queries (来自 case.json) ==")
    for q in analysis.get("queries", []):
        print(json.dumps(q, ensure_ascii=False))
    print("\n== normalized_stems ==")
    print(json.dumps(analysis.get("normalized_stems", {}), ensure_ascii=False, indent=2))
    print("\n== errors ==")
    print(json.dumps(library.get("errors", []), ensure_ascii=False, indent=2))
    return 0


def cmd_check(args) -> int:
    input_dir, work_dir, output_dir = _resolve_dirs(args)
    library = _load_library(work_dir)
    draft = rules.load_draft(work_dir / "draft.json")
    if library is None:
        print("ERROR: 未找到 work/library.json，请先运行 init")
        return 0
    report = rules.validate_draft(draft, library, input_dir)
    _write_json(work_dir / "check_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_build(args) -> int:
    input_dir, work_dir, output_dir = _resolve_dirs(args)
    actual_output, degradations = _ensure_writable(work_dir, output_dir)

    library = _load_library(work_dir)
    if library is None:
        # 兜底：没有 library 就重新 init，保证至少交付合法 result.json
        case = libcore.load_case(input_dir)
        library = libcore.build_library(input_dir, case)
        _write_json(work_dir / "library.json", library)

    draft = rules.load_draft(work_dir / "draft.json")
    no_draft = rules.is_template_draft(draft)

    case = libcore.load_case(input_dir)
    case_id = rules.derive_case_id(case, library)
    contract_keys = libcore.extract_contract(case) if isinstance(case, dict) else []

    # 状态收口（模板 draft 视作未填，防把示例占位符当成结论）
    if no_draft:
        status = _determine_status(library, case, {})
    else:
        status = _determine_status(library, case, draft)

    if no_draft:
        degradations.append("no_draft")

    errors = list(library.get("errors", []))
    if isinstance(draft, dict) and draft.get("error"):
        errors.append({"kind": "draft", "detail": draft.get("error")})

    draft_for_build = draft if (isinstance(draft, dict) and not no_draft) else {}
    result = rules.assemble_result(
        case_id, contract_keys, status, library,
        draft_for_build, degradations, errors,
    )

    _write_json(actual_output / "result.json", result)
    _write_json(actual_output / "library_index.json", library)
    _write_json(actual_output / "evidence_map.json",
                rules.build_evidence_map(result, library, draft_for_build))
    _write_inventory_md(actual_output / "inventory.md", library, result)

    print(json.dumps({
        "status": status,
        "case_id": case_id,
        "output": str(actual_output),
        "degradations": degradations,
    }, ensure_ascii=False, indent=2))
    return 0


def _write_inventory_md(path: Path, library: dict, result: dict) -> None:
    lines = ["# 科研文档库修复交接清单", "",
             f"- case_id: {result.get('case_id')}",
             f"- status: {result.get('status')}",
             f"- 文件数: {len(library.get('files', []))}",
             ""]
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| ID | 相对路径 | 类型 | 大小 | sha256(前12) | 哈希状态 |")
    lines.append("|---|---|---|---|---|---|")
    for f in library.get("files", []):
        lines.append(
            f"| {f['id']} | {f['rel_path']} | {f['kind']} | {f['size']} "
            f"| {(f['sha256'] or '')[:12]} | {f['hash_status']} |"
        )
    lines.append("")
    lines.append("## 分组")
    lines.append("")
    for key, title in (("alias_groups", "重复/别名"), ("version_groups", "候选版本"),
                       ("conflicts", "来源冲突")):
        lines.append(f"### {title}")
        for g in library.get("analysis", {}).get(key, []):
            lines.append(f"- {json.dumps(g, ensure_ascii=False)}")
        lines.append("")
    lines.append("## 待人工确认")
    lines.append("")
    for p in result.get("pending_human", []):
        lines.append(f"- {json.dumps(p, ensure_ascii=False)}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def cmd_selfcheck(args) -> int:
    input_dir, work_dir, output_dir = _resolve_dirs(args)
    output_dir_actual, _ = _ensure_writable(work_dir, output_dir)
    issues = []
    try:
        result = json.loads((output_dir_actual / "result.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.append("缺少 result.json")
        result = None
    except (json.JSONDecodeError, OSError) as exc:
        issues.append(f"result.json 无法解析：{exc}")
        result = None

    if result is not None:
        for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
            if key not in result:
                issues.append(f"result.json 缺字段 {key}")
        if result.get("status") not in rules.ALLOWED_STATUS:
            issues.append(f"status 非法：{result.get('status')}")

    for name in ("library_index.json", "evidence_map.json", "inventory.md"):
        p = output_dir_actual / name
        if not p.exists() or p.stat().st_size == 0:
            issues.append(f"缺产物或为空：{name}")

    report = {"ok": not issues, "issues": issues,
              "output": str(output_dir_actual)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="科研文档库修复 Skill：确定性盘点、去重、版本/别名、检索与交接。",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("init", cmd_init), ("show", cmd_show), ("check", cmd_check),
                     ("build", cmd_build), ("selfcheck", cmd_selfcheck)):
        sp = sub.add_parser(name)
        sp.add_argument("--input", default=None)
        sp.add_argument("--work", default=None)
        sp.add_argument("--output", default=None)
        sp.set_defaults(func=fn)
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # 兜底：任何异常都不应让进程非 0 退出
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        # 尽力写出最小合法产物
        try:
            _, work_dir, output_dir = _resolve_dirs(args)
            actual_output, _ = _ensure_writable(work_dir, output_dir)
            skeleton = rules.build_result("unknown", [], "input_error")
            skeleton["errors"] = [{"kind": "uncaught", "detail": str(exc)}]
            _write_json(actual_output / "result.json", skeleton)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
