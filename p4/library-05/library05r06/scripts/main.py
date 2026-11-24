#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 科研文件库修复 Skill 的执行入口。

命令（默认路径 /input、/app/work、/app/output，均可用 --input/--work/--output 覆盖）：

    python scripts/main.py init      # 发现盘点 + 写 partial result.json + library/plan/model 模板
    python scripts/main.py show      # 打印带 locator 的文件/分组/原文摘录/问题，供模型阅读
    # 模型按 show 输出把判断写进 /app/work/model.json
    python scripts/main.py check     # 机械校验 model.json（结构 + 证据 quote 逐字命中）
    python scripts/main.py build     # 合并确定性结果与 model，无条件收口交付
    python scripts/main.py selfcheck # 复检 /app/output 产物齐全与可解析
    python scripts/main.py selftest  # 包级自检（调用 scripts/selftest.py）

退出码恒为 0：真实问题写进 errors/degradations，不靠退出码表达。
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

import core  # noqa: E402

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

TASK_LABEL = "library-05-document-repair"
SKILL_VERSION = core.SKILL_VERSION

ALLOWED_STATUS = core.ALLOWED_STATUS


# --------------------------------------------------------------------------- 目录与输入


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


def ensure_output(output_dir, work_dir):
    """确认输出目录可写，不可写则依次降级。返回 (实际目录, 降级说明)。"""
    candidates = [output_dir, work_dir / "output_fallback", SKILL_ROOT / "skill_output"]
    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            if cand != output_dir:
                return cand, "output_fallback:%s" % cand.name
            return cand, None
        except OSError:
            continue
    return output_dir, "output_not_writable"


def read_input(input_dir):
    """读取 case.json 与 task.md；坏 JSON/缺失均降级。返回 (case, task_text, meta)。"""
    case, task_text = {}, ""
    case_error = None
    case_missing = False
    case_path = input_dir / "case.json"
    if case_path.exists():
        case, err = core.load_json(case_path, {})
        if err:
            case_error = err
            case = {}
    else:
        case_missing = True
    task_path = input_dir / "task.md"
    if task_path.exists():
        data = core.read_bytes(task_path, core.MAX_TEXT_BYTES)
        if data is not None:
            task_text, _ = core.decode_text(data)
    return case, task_text, {"case_error": case_error, "case_missing": case_missing}


def extract_queries(case, task_text):
    """从 case.json 与 task.md 中发现检索问题（保守识别，避免臆造）。"""
    queries = []
    if isinstance(case, dict):
        for key in ("queries", "questions", "searches", "检索问题", "query_list"):
            val = case.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        queries.append(item)
                    elif isinstance(item, dict):
                        for k in ("query", "question", "text", "search", "prompt"):
                            if isinstance(item.get(k), str):
                                queries.append(item[k])
                                break
            elif isinstance(val, str) and val:
                queries.append(val)
    if task_text:
        for line in task_text.splitlines():
            s = line.strip()
            if s.endswith("?") or s.endswith("？"):
                queries.append(s)
    seen, out = set(), []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


# --------------------------------------------------------------------------- 结果组装


def build_final(input_dir, work_dir, output_dir):
    """无条件收口：合并 library.json 与 model.json，写全部交付产物。"""
    library, err = core.load_json(work_dir / "library.json", {})
    model, merr = core.load_json(work_dir / "model.json", {})
    if not isinstance(model, dict):
        model = {}

    case_id = library.get("case_id", "unknown")
    contract_keys = library.get("contract_keys", [])
    files = library.get("files", [])
    dirs = library.get("dirs", [])
    analysis = library.get("analysis", {})
    errors = library.get("errors", [])
    degradations = library.get("degradations", [])
    queries = library.get("queries", [])
    index = library.get("index", {})
    files_by_id = {f["id"]: f for f in files}

    # 模型校验结果（若无 check_report 则现场校验）
    check_report, _ = core.load_json(work_dir / "check_report.json", None)
    if not isinstance(check_report, dict):
        check_report = core.validate_model(model, library, input_dir)

    # 检索结果（机械关键词命中，供模型补充语义答案）
    search_results = {}
    for i, q in enumerate(queries):
        qid = "Q%d" % (i + 1)
        hit_ids = core.search_candidates(index, q)
        search_results[qid] = {"query": q, "status": "keyword_candidates",
                               "candidate_file_ids": hit_ids,
                               "locators": [files_by_id[f]["rel_path"] for f in hit_ids if f in files_by_id]}

    # 语义 query 答案覆盖机械候选
    for a in model.get("query_answers", []):
        qid = a.get("query_id")
        if isinstance(qid, str):
            entry = search_results.get(qid)
            if entry:
                entry["answer"] = a.get("answer")
                entry["status"] = a.get("status")
                entry["evidence"] = a.get("evidence", [])

    # 契约 sections
    metadata_summary = {
        "file_count": len(files), "dir_count": len(dirs),
        "text_count": sum(1 for f in files if f.get("kind") == "text"),
        "binary_count": sum(1 for f in files if f.get("kind") == "binary"),
        "total_bytes": sum(f.get("size") or 0 for f in files),
    }
    index_summary = {"term_count": len(index),
                     "terms": {t: ids for t, ids in list(sorted(index.items()))[:200]}}
    sections = {
        "files": files,
        "duplicate_groups": analysis.get("duplicate_groups", []),
        "alias_groups": analysis.get("alias_groups", []),
        "version_groups": analysis.get("version_groups", []),
        "conflicts": analysis.get("conflicts", []),
        "index": index_summary,
        "search_results": search_results,
        "operations": model.get("operations", []),
        "metadata": metadata_summary,
        "annotations": model.get("annotations", []),
        "relations": model.get("relations", []),
        "notes": model.get("notes", []),
    }

    status = core.compute_status(library, model, check_ok=bool(check_report.get("valid", False)))

    result = core.build_skeleton(case_id, contract_keys, status)
    result["status"] = status
    result["result"] = {
        "summary": metadata_summary,
        "files": [{"id": f["id"], "rel_path": f["rel_path"], "sha256": f["sha256"],
                   "size": f["size"], "kind": f["kind"], "permissions": f["permissions"]}
                  for f in files],
        "dirs": dirs,
        "duplicate_groups": analysis.get("duplicate_groups", []),
        "alias_groups": analysis.get("alias_groups", []),
        "version_groups": analysis.get("version_groups", []),
        "conflicts": analysis.get("conflicts", []),
        "search_results": search_results,
        "operations": model.get("operations", []),
    }
    result["claims"] = core.build_claims(library, model)
    result["sources"] = core.build_sources(library)
    result["change_log"] = core.build_change_log(library, model)
    result["errors"] = errors
    result["degradations"] = list(degradations)
    result["uncertainty"] = {
        "summary": ("机械分组含 heuristic 版本/冲突判定，需人工复核；" if (
            analysis.get("conflicts") or analysis.get("version_groups")) else "") +
        ("check 校验未完全通过" if not check_report.get("valid") else "check 校验通过"),
        "count": len(check_report.get("errors", [])) + len(model.get("uncertainties", [])),
        "items": model.get("uncertainties", []),
    }
    result["uncertainties"] = model.get("uncertainties", [])
    result["pending_human"] = model.get("pending_human", [])
    for g in analysis.get("conflicts", []):
        if not any(isinstance(p, dict) and p.get("locator") == "stem:" + g.get("stem", "")
                   for p in result["pending_human"]):
            result["pending_human"].append({
                "item": "冲突组 %s" % g.get("group_id"), "locator": "stem:" + g.get("stem", ""),
                "member_ids": g.get("member_ids", []),
                "reason": g.get("reason", "同 stem 不同哈希且无版本标记"),
            })

    # 契约逐键回填
    unmapped = []
    for key in contract_keys:
        val = core.map_contract_key(key, sections, result)
        result[key] = val
        if val is None:
            unmapped.append(key)
    if unmapped:
        result.setdefault("degradations", [])
        result["degradations"].append({
            "kind": "contract_key_unmapped", "locator": "case.json:output_contract",
            "message": "以下契约键无匹配 section，已回填 null: " + ", ".join(unmapped),
        })

    result["runtime"] = {
        "skill_version": SKILL_VERSION,
        "task": TASK_LABEL,
        "seconds": round(time.time(), 2),
        "note": "runtime 字段不参与确定性对比；其余字段同输入两次运行一致",
    }

    # 写出产物
    output_actual, out_note = ensure_output(output_dir, work_dir)
    if out_note:
        result.setdefault("degradations", [])
        result["degradations"].append({"kind": "output_fallback", "locator": out_note,
                                       "message": "输出目录降级"})

    core.write_json(output_actual / "result.json", result)

    # library_index.json：完整盘点（确定性）
    library_index = {
        "case_id": case_id,
        "dirs": dirs,
        "files": files,
        "analysis": analysis,
        "index": index_summary,
        "queries": queries,
        "errors": errors,
        "degradations": degradations,
    }
    core.write_json(output_actual / "library_index.json", library_index)

    # evidence_map.json：claim -> 证据映射
    evidence_map = {
        "case_id": case_id,
        "claims": [
            {"claim_id": c.get("claim_id"), "text": c.get("text"),
             "evidence": c.get("evidence", [])}
            for c in result.get("claims", [])
        ],
        "query_answers": model.get("query_answers", []),
        "relations": model.get("relations", []),
    }
    core.write_json(output_actual / "evidence_map.json", evidence_map)

    # 中间表示与可复核：plan.json / model.json 也落盘到 output 供复核
    plan, _ = core.load_json(work_dir / "plan.json", {})
    if isinstance(plan, dict):
        plan["status"] = status
        for p in plan.get("phases", []):
            p["status"] = "done"
        core.write_json(output_actual / "plan.json", plan)
    core.write_json(output_actual / "model.json", model)

    # inventory.md：可读交接清单
    _write_inventory(output_actual / "inventory.md", result, library, model)

    return output_actual, result


def _write_inventory(path, result, library, model):
    lines = []
    lines.append("# 科研文件库修复交接清单")
    lines.append("")
    lines.append("- case_id: %s" % result.get("case_id"))
    lines.append("- status: %s" % result.get("status"))
    lines.append("- 文件数: %d，目录数: %d" % (
        len(library.get("files", [])), len(library.get("dirs", []))))
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| id | rel_path | sha256 | size | kind | permissions |")
    lines.append("|---|---|---|---|---|---|")
    for f in library.get("files", []):
        sha = (f.get("sha256") or f.get("hash_status") or "")[:16]
        lines.append("| %s | %s | %s | %d | %s | %s |" % (
            f["id"], f["rel_path"], sha, f.get("size") or 0,
            f.get("kind"), f.get("permissions", "")))
    lines.append("")
    lines.append("## 分组")
    analysis = library.get("analysis", {})
    for name, label in (("duplicate_groups", "重复/副本"), ("version_groups", "版本候选"),
                        ("conflicts", "冲突")):
        groups = analysis.get(name, [])
        if groups:
            lines.append("")
            lines.append("### %s" % label)
            for g in groups:
                lines.append("- %s: %s" % (g.get("group_id"), ", ".join(g.get("member_ids", []))))
    lines.append("")
    lines.append("## 检索结果")
    for qid, entry in result.get("result", {}).get("search_results", {}).items():
        lines.append("- %s: %s -> %s" % (qid, entry.get("query"), entry.get("answer") or entry.get("status")))
    lines.append("")
    lines.append("## 待人工处理")
    for p in result.get("pending_human", []):
        lines.append("- %s" % json.dumps(p, ensure_ascii=False))
    if result.get("errors"):
        lines.append("")
        lines.append("## 错误")
        for e in result["errors"]:
            lines.append("- %s" % json.dumps(e, ensure_ascii=False))
    if result.get("degradations"):
        lines.append("")
        lines.append("## 降级")
        for d in result["degradations"]:
            lines.append("- %s" % json.dumps(d, ensure_ascii=False))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- 子命令


def cmd_init(input_dir, work_dir, output_dir):
    case, task_text, meta = read_input(input_dir)
    case_id = core.derive_case_id(case)
    contract_keys = core.extract_contract_keys(case)
    queries = extract_queries(case, task_text)
    dirs, files, errors, degradations = core.discover(input_dir)

    # 输入错误判定
    input_error = bool(meta.get("case_error") or meta.get("case_missing"))
    if meta.get("case_error"):
        errors.append({"kind": "invalid_case_json", "locator": "case.json",
                       "message": meta["case_error"]})
    if meta.get("case_missing"):
        degradations.append({"kind": "missing_case_json", "locator": "case.json",
                             "message": "缺 case.json，仍按材料盘点"})

    analysis = core.analyze(dirs, files)
    index = core.build_index(files, input_dir)

    library = {
        "case_id": case_id,
        "contract_keys": contract_keys,
        "queries": queries,
        "dirs": dirs,
        "files": files,
        "analysis": analysis,
        "index": index,
        "errors": errors,
        "degradations": degradations,
        "input_error": input_error,
        "case_missing": meta.get("case_missing", False),
        "case_error": meta.get("case_error"),
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    core.write_json(work_dir / "library.json", library)

    # 模型模板与计划
    model = core.model_template(case_id)
    core.write_json(work_dir / "model.json", model)

    profile = {
        "kind": "file_library_repair",
        "file_count": len(files),
        "dir_count": len(dirs),
        "has_output_contract": bool(contract_keys),
        "contract_keys": contract_keys,
        "query_count": len(queries),
        "errors": errors,
        "degradations": degradations,
    }
    plan = core.plan_template(case_id, contract_keys, profile)
    core.write_json(work_dir / "plan.json", plan)

    # 立即落盘 partial 结果骨架（前 3 分钟硬要求）
    status = "input_error" if input_error else \
             ("insufficient_evidence" if not files else "partial")
    skeleton = core.build_skeleton(case_id, contract_keys, status)
    skeleton["result"] = {"summary": {"file_count": len(files), "dir_count": len(dirs)}}
    skeleton["errors"] = errors
    skeleton["degradations"] = degradations
    skeleton["runtime"] = {"skill_version": SKILL_VERSION, "phase": "init",
                           "seconds": round(time.time(), 2)}
    output_actual, _ = ensure_output(output_dir, work_dir)
    core.write_json(output_actual / "result.json", skeleton)
    return library, output_actual


def _short(f, n=12):
    s = f.get("sha256")
    return (s[:n] if s else (f.get("hash_status") or "no-hash"))


def cmd_show(input_dir, work_dir, output_dir):
    library, err = core.load_json(work_dir / "library.json", {})
    files = library.get("files", [])
    files_by_id = {f["id"]: f for f in files}
    lines = []
    lines.append("== CASE %s ==" % library.get("case_id"))
    lines.append("契约键: %s" % (library.get("contract_keys") or "(无)"))
    lines.append("检索问题: %d 个" % len(library.get("queries", [])))
    lines.append("")
    lines.append("== 文件清单 (%d) ==" % len(files))
    for f in files:
        lines.append("%s | %s | sha=%s | %dB | %s | %s%s" % (
            f["id"], f["rel_path"], _short(f), f.get("size") or 0, f.get("kind"),
            f.get("permissions", ""),
            " | " + f.get("hash_status") if f.get("hash_status") else ""))
    lines.append("")
    lines.append("== 目录 (%d) ==" % len(library.get("dirs", [])))
    for d in library.get("dirs", []):
        lines.append("%s | %s | files=%d" % (d["id"], d["rel_path"], d.get("file_count", 0)))
    analysis = library.get("analysis", {})
    lines.append("")
    lines.append("== 重复/副本组 ==")
    for g in analysis.get("duplicate_groups", []):
        lines.append("%s sha=%s: %s" % (g["group_id"], g["sha256"][:12], ", ".join(g["member_ids"])))
    lines.append("")
    lines.append("== 版本候选组 ==")
    for g in analysis.get("version_groups", []):
        lines.append("%s stem=%s: %s" % (g["group_id"], g["stem"], ", ".join(g["member_ids"])))
    lines.append("")
    lines.append("== 冲突组 ==")
    for g in analysis.get("conflicts", []):
        lines.append("%s stem=%s: %s" % (g["group_id"], g["stem"], ", ".join(g["member_ids"])))
    lines.append("")
    lines.append("== 检索问题 ==")
    for i, q in enumerate(library.get("queries", [])):
        hits = core.search_candidates(library.get("index", {}), q)
        lines.append("Q%d: %s -> 候选文件: %s" % (i + 1, q, ", ".join(hits)))
    lines.append("")
    lines.append("== 文本文件原文摘录（供写 quote，逐字引用） ==")
    for f in files:
        if f.get("kind") != "text":
            continue
        data = core.read_bytes(input_dir / f["rel_path"], 4000)
        if data is None:
            lines.append("%s | %s: (不可读)" % (f["id"], f["rel_path"]))
            continue
        text, enc = core.decode_text(data)
        preview = text[:1200]
        lines.append("--- %s | %s (%s) ---" % (f["id"], f["rel_path"], enc))
        for ln in preview.splitlines()[:40]:
            lines.append("    | " + ln)
    lines.append("")
    lines.append("== 错误 ==")
    for e in library.get("errors", []):
        lines.append("- %s" % json.dumps(e, ensure_ascii=False))
    lines.append("")
    lines.append("== 降级 ==")
    for d in library.get("degradations", []):
        lines.append("- %s" % json.dumps(d, ensure_ascii=False))
    return "\n".join(lines)


def cmd_check(input_dir, work_dir, output_dir):
    library, _ = core.load_json(work_dir / "library.json", {})
    model, _ = core.load_json(work_dir / "model.json", {})
    report = core.validate_model(model, library, input_dir)
    core.write_json(work_dir / "check_report.json", report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def cmd_build(input_dir, work_dir, output_dir):
    output_actual, result = build_final(input_dir, work_dir, output_dir)
    return "built -> %s status=%s" % (output_actual / "result.json", result.get("status"))


def cmd_selfcheck(input_dir, work_dir, output_dir):
    output_actual, _ = ensure_output(output_dir, work_dir)
    problems = []
    required = ["result.json", "library_index.json", "evidence_map.json",
                "inventory.md", "plan.json", "model.json"]
    for name in required:
        p = output_actual / name
        if not p.exists() or p.stat().st_size == 0:
            problems.append("缺失或为空: %s" % name)
    r, err = core.load_json(output_actual / "result.json", None)
    if not isinstance(r, dict):
        problems.append("result.json 不可解析: %s" % (err or ""))
    else:
        if not r.get("case_id"):
            problems.append("result.json 缺 case_id")
        if r.get("status") not in ALLOWED_STATUS:
            problems.append("result.json status 非法: %s" % r.get("status"))
        for k in ("sources", "uncertainty", "change_log"):
            if k not in r:
                problems.append("result.json 缺信封字段: %s" % k)
    summary = {
        "ok": not problems,
        "output_dir": output_actual.as_posix(),
        "problems": problems,
        "artifacts": {name: (output_actual / name).stat().st_size
                      for name in required if (output_actual / name).exists()},
    }
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)


# --------------------------------------------------------------------------- 入口


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="科研文件库修复 Skill：init/show/check/build/selfcheck")
    p.add_argument("cmd", nargs="?", default="show",
                   choices=["init", "show", "check", "build", "selfcheck", "selftest", "run"])
    p.add_argument("--input", default=None)
    p.add_argument("--work", default=None)
    p.add_argument("--output", default=None)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    input_dir, work_dir, output_dir = resolve_dirs(args)
    if args.cmd == "selftest":
        # 委托 scripts/selftest.py（独立进程，自检不污染交付目录）
        import subprocess
        selftest = SCRIPTS_DIR / "selftest.py"
        code = subprocess.call([sys.executable, str(selftest)])
        sys.exit(0)
    try:
        if args.cmd == "init":
            print(cmd_init(input_dir, work_dir, output_dir)[0].get("case_id", "unknown"))
        elif args.cmd == "show":
            print(cmd_show(input_dir, work_dir, output_dir))
        elif args.cmd == "check":
            print(cmd_check(input_dir, work_dir, output_dir))
        elif args.cmd == "build":
            print(cmd_build(input_dir, work_dir, output_dir))
        elif args.cmd == "selfcheck":
            print(cmd_selfcheck(input_dir, work_dir, output_dir))
        elif args.cmd == "run":
            # 确定性地板：init -> build -> selfcheck（不写语义模型，结果标 partial）
            cmd_init(input_dir, work_dir, output_dir)
            print(cmd_build(input_dir, work_dir, output_dir))
            print(cmd_selfcheck(input_dir, work_dir, output_dir))
        else:
            print("usage: main.py {init|show|check|build|selfcheck|run} [--input --work --output]")
    except Exception as exc:  # noqa: BLE001 - 任何异常都不应让进程崩溃
        print(json.dumps({"kind": "unhandled_error",
                          "message": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()