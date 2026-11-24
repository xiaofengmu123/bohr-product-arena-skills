#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 学术格式与模板适配 Skill 入口（仅标准库）。

流程：init(索引+骨架) → scaffold(180s 内 partial 落盘) → survey/show(材料与原文)
      → 写 /app/work/draft.json → check(机械校验) → build(主交付+result.json+契约校验)
      → selfcheck(复核)。脚本只做结构、契约与可核验性，正文内容由 Agent 基于材料撰写。

命令：
  python scripts/main.py init      # 建索引 + partial result.json + 工作稿模板
  python scripts/main.py scaffold  # 幂等追加骨架 change_log（前 3 分钟内调用）
  python scripts/main.py survey    # 材料/工具/流程概览
  python scripts/main.py show      # 打印带 locator 的原文（事实与引文逐字复制）
  python scripts/main.py check     # 机械校验工作稿（通用 + 格式适配检查）
  python scripts/main.py build     # 主交付 + result.json + format_report.md
  python scripts/main.py selfcheck # 复检 /app/output/result.json 契约完整性
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from core_impl import (  # noqa: E402
    DICT_FIELDS, LIST_FIELDS, ROOT_FIELDS, STATUS, TEXT_KINDS, discover, ensure_dir,
    fill_contract, contract_keys, default_value, markdown_report, norm, now_iso,
    paragraphs, probe_tools, quote_hit, read_json, read_text, validate_common,
    write_json, write_text,
)

PKG_ROOT = SCRIPTS_DIR.parent
VERSION = "1.0.0"
TASK_LABEL = "学术格式与模板适配（writing-03）"
DEFAULT_DELIVERABLE = "adapted_manuscript.md"

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_STATE = {"errors": [], "degradations": [], "changes": [], "steps": [], "started": time.time()}


def step(name: str) -> None:
    _STATE["steps"].append(name)


def degrade(item: str) -> None:
    _STATE["degradations"].append(item)


def fail_hard(item: str) -> None:
    _STATE["errors"].append(item)


def resolve_dirs(args) -> tuple[Path, Path, Path]:
    input_root = Path(args.input).expanduser() if getattr(args, "input", None) else DEFAULT_INPUT
    work = Path(args.work).expanduser() if getattr(args, "work", None) else DEFAULT_WORK
    wanted = Path(args.output).expanduser() if getattr(args, "output", None) else DEFAULT_OUTPUT
    ok, err = ensure_dir(work)
    if not ok:
        fallback = Path.cwd() / "skill_work"
        if ensure_dir(fallback)[0]:
            degrade(f"work dir {work} unusable ({err}), fallback {fallback}")
            work = fallback
    output = wanted
    ok, err = ensure_dir(output)
    if not ok:
        for candidate in (work / "output_fallback", Path.cwd() / "skill_output"):
            if ensure_dir(candidate)[0]:
                degrade(f"output dir {wanted} unusable ({err}), fallback {candidate}")
                output = candidate
                break
    return input_root, work, output


def load_case(input_root: Path) -> dict:
    case, err = read_json(input_root / "case.json")
    if err:
        fail_hard(f"case.json unavailable: {err}")
    return case if isinstance(case, dict) else {}


def build_index(input_root: Path, case: dict) -> dict:
    return {
        "case_id": case.get("case_id"),
        "task_instruction": case.get("task_instruction") or case.get("task"),
        "materials": discover(input_root),
        "tools": probe_tools(),
        "generated_at": now_iso(),
    }


def build_corpus(input_root: Path, work: Path, case: dict) -> dict:
    corpus: dict[str, str] = {}
    for material in discover(input_root):
        if material["kind"] in ("text", "table"):
            text, _err = read_text(input_root / material["rel_path"])
            corpus[material["source_id"]] = norm(text)
    extracted = work / "extracted"
    if extracted.is_dir():
        for offset, path in enumerate(sorted(extracted.rglob("*")), 1):
            if path.is_file() and path.suffix.lower().lstrip(".") in TEXT_KINDS:
                text, _err = read_text(path)
                corpus[f"EX{offset}"] = norm(text)
    corpus["_task"] = norm(str(case.get("task_instruction") or ""))
    return corpus


def draft_skeleton() -> dict:
    return {
        "_说明": "复制为 work/draft.json 后填写；事实/引文逐字复制 show 输出。",
        "deliverable_name": DEFAULT_DELIVERABLE,
        "deliverable_text": "# 标题\n\n（在此写真实正文/修改稿）\n",
        "fact_source_map": [{
            "claim_id": "C1", "claim": "正文中的事实性论断", "source_id": "S1",
            "locator": "para.1", "quote": "逐字原文", "kind": "direct", "strength": "moderate",
        }],
        "version_boundary": {
            "revision_summary": "改了什么", "diff": [], "human_confirms": [],
            "material_gaps": [], "exceptions": [],
        },
        "format_spec": {
            "source_locator": "task.md#格式要求", "columns": None, "font_size": None,
            "line_spacing": None, "margin": None, "heading_levels": [],
            "figure_caption_position": None, "table_caption_position": None,
            "reference_style": None, "export_format": None,
            "items": [],
        },
        "change_log": [],
        "uncertainties": [],
        "pending_human": [],
        "extra_files": {},
    }


def format_checks(draft: dict) -> list[dict]:
    """格式适配专项机械校验：标题层级/图注/交叉引用/参考文献/内容单元。"""
    items: list[dict] = []

    def add(level, check_id, detail):
        items.append({"level": level, "id": check_id, "detail": detail})

    text = str(draft.get("deliverable_text") or "")
    spec = draft.get("format_spec") if isinstance(draft.get("format_spec"), dict) else {}
    headings = re.findall(r"^#{1,6}\s+.+", text, flags=re.M)
    add("PASS" if headings else "WARN", "format.heading_levels", "正文未检测到标题" if not headings else f"检测到 {len(headings)} 个标题")
    if spec.get("heading_levels"):
        levels = sorted({len(h.split()[0]) for h in headings})
        expected = spec.get("heading_levels")
        if isinstance(expected, list) and expected:
            add("PASS" if levels == sorted(expected) else "WARN", "format.heading_depth",
                f"标题层级 {levels} 与规格 {expected} 不一致")
    has_fig_caption = bool(re.search(r"(图|Figure|Fig\.?)\s*\d+", text, flags=re.I))
    has_tab_caption = bool(re.search(r"(表|Table)\s*\d+", text, flags=re.I))
    add("WARN" if not has_fig_caption else "PASS", "format.figure_caption", "未检测到图注编号" if not has_fig_caption else "检测到图注编号")
    add("WARN" if not has_tab_caption else "PASS", "format.table_caption", "未检测到表注编号" if not has_tab_caption else "检测到表注编号")
    xref = re.findall(r"(图|Figure|Fig\.?|表|Table)\s*\d+", text, flags=re.I)
    add("PASS" if xref else "WARN", "format.cross_reference", "未检测到交叉引用" if not xref else f"检测到 {len(xref)} 处交叉引用")
    has_ref = bool(re.search(r"(参考文献|References|\[\d+\])", text, flags=re.I))
    add("WARN" if not has_ref else "PASS", "format.references", "未检测到参考文献区" if not has_ref else "检测到参考文献区")
    return items


def cmd_init(args):
    step("init")
    input_root, work, output = resolve_dirs(args)
    case = load_case(input_root)
    keys, fallback = contract_keys(case)
    index = build_index(input_root, case)
    write_json(work / "index.json", index)
    write_json(work / "draft.skeleton.json", draft_skeleton())
    payload = {"case_id": case.get("case_id") or "unknown-case", "status": "partial",
               "deliverable": DEFAULT_DELIVERABLE, "deliverable_path": DEFAULT_DELIVERABLE,
               "fact_source_map": [], "version_boundary": {}, "result": {}}
    result, missing = fill_contract(keys, payload)
    if fallback:
        for k, v in (("deliverable", DEFAULT_DELIVERABLE), ("fact_source_map", []), ("version_boundary", {})):
            result.setdefault(k, v)
    env = {"sources": index.get("materials", []), "uncertainty": [], "pending_human": [],
           "errors": list(_STATE["errors"]), "degradations": list(_STATE["degradations"]),
           "change_log": [], "runtime": {"skill_version": VERSION, "task": TASK_LABEL,
                                          "steps": list(_STATE["steps"]), "contract_keys": keys,
                                          "contract_fallback": fallback, "contract_missing": missing,
                                          "tools": index.get("tools", {}),
                                          "elapsed_ms": int((time.time() - _STATE["started"]) * 1000),
                                          "generated_at": now_iso()}}
    for key, value in env.items():
        result.setdefault(key, value)
    result["change_log"] = [{"ts": now_iso(), "action": "init", "target": "result.json",
                             "detail": "骨架落盘（status=partial），后续仅增量改进"}]
    write_json(output / "result.json", result)
    print(json.dumps({"stage": "init", "case_id": result.get("case_id"),
                      "materials": len(index["materials"]), "contract_keys": keys,
                      "contract_fallback": fallback, "output": str(output)}, ensure_ascii=False))


def cmd_scaffold(args):
    step("scaffold")
    input_root, work, output = resolve_dirs(args)
    case = load_case(input_root)
    keys, fallback = contract_keys(case)
    result, err = read_json(output / "result.json")
    if err or not isinstance(result, dict):
        result = {"case_id": case.get("case_id") or "unknown-case", "status": "partial"}
    result.setdefault("case_id", case.get("case_id") or "unknown-case")
    result.setdefault("status", "partial")
    result.setdefault("deliverable", DEFAULT_DELIVERABLE)
    result.setdefault("deliverable_path", DEFAULT_DELIVERABLE)
    result.setdefault("fact_source_map", [])
    result.setdefault("version_boundary", {})
    result.setdefault("sources", discover(input_root))
    result.setdefault("uncertainty", {})
    result.setdefault("pending_human", [])
    result.setdefault("errors", list(_STATE["errors"]))
    result.setdefault("degradations", list(_STATE["degradations"]))
    result.setdefault("runtime", {"skill_version": VERSION, "contract_fallback": fallback})
    # 幂等：只追加一条 scaffold 记录，不整体重建既有字段
    change_log = result.get("change_log")
    if not isinstance(change_log, list):
        change_log = []
        result["change_log"] = change_log
    if not any(isinstance(c, dict) and c.get("action") == "scaffold" for c in change_log):
        change_log.append({"ts": now_iso(), "action": "scaffold", "target": "result.json",
                           "detail": "partial 骨架确认并落盘；后续阶段只做增量改进"})
    for key in keys:
        if key not in result:
            result[key] = default_value(key)
    write_json(output / "result.json", result)
    print(json.dumps({"stage": "scaffold", "status": result.get("status"),
                      "contract_keys": keys, "change_log_len": len(change_log)}, ensure_ascii=False))


def cmd_survey(args):
    step("survey")
    input_root, work, _output = resolve_dirs(args)
    index, err = read_json(work / "index.json")
    if err or not isinstance(index, dict):
        index = build_index(input_root, load_case(input_root))
        degrade("index.json missing, survey rebuilt it")
    print(f"case_id: {index.get('case_id')}")
    if index.get("task_instruction"):
        print(f"task_instruction: {index['task_instruction']}")
    print("materials:")
    for material in index.get("materials", []):
        detail = [material.get("kind")]
        for key in ("suffix", "size", "format"):
            if material.get(key) is not None:
                detail.append(f"{key}={material[key]}")
        print(f"  [{material['source_id']}] {material['rel_path']} ({', '.join(map(str, detail))})")
    tools = index.get("tools") or {}
    print(f"python: {tools.get('python') or 'not found'}")
    print(f"latex: {tools.get('latex')} | pandoc: {tools.get('pandoc')} | libreoffice: {tools.get('libreoffice')}")
    print("workflow: init -> scaffold -> survey -> show -> draft.json -> check -> build -> selfcheck")


def cmd_show(args):
    step("show")
    input_root, work, _output = resolve_dirs(args)
    index, err = read_json(work / "index.json")
    if err or not isinstance(index, dict):
        index = build_index(input_root, load_case(input_root))
    budget = max(0, int(args.max_chars))
    printed = 0
    print("=== 材料原文（引文请逐字复制；locator 必带）===")
    for material in index.get("materials", []):
        if material.get("kind") not in ("text", "table"):
            continue
        if args.source and material["source_id"] != args.source:
            continue
        if budget <= 0:
            print("[truncated] budget reached, use --source to view another material")
            break
        text, _err = read_text(input_root / material["rel_path"])
        print(f"\n--- [{material['source_id']}] {material['rel_path']} ---")
        for locator, para in paragraphs(text):
            if budget <= 0:
                print("[truncated]")
                break
            chunk = para[:budget]
            budget -= len(chunk)
            printed += 1
            print(f"[{material['source_id']}|{locator}] {chunk}")
    print(f"\nprinted {printed} paragraphs; copy quote + source_id + locator into draft.json")


def cmd_check(args):
    step("check")
    input_root, work, _output = resolve_dirs(args)
    case = load_case(input_root)
    corpus = build_corpus(input_root, work, case)
    draft, err = read_json(work / "draft.json")
    report = {"checked_at": now_iso(), "items": [], "fail": 0, "warn": 0}
    if err or not isinstance(draft, dict):
        report["items"].append({"id": "draft.json", "level": "FAIL", "detail": err or "not a dict"})
        report["fail"] = 1
    else:
        items, fail, warn = validate_common(draft, corpus, _output)
        try:
            items.extend(format_checks(draft))
        except Exception as exc:  # noqa: BLE001
            items.append({"id": "format_checks", "level": "WARN", "detail": str(exc)})
        fail = sum(1 for i in items if i["level"] == "FAIL")
        warn = sum(1 for i in items if i["level"] == "WARN")
        report.update({"items": items, "fail": fail, "warn": warn})
    write_json(work / "check_report.json", report)
    for item in report["items"]:
        print(f"[{item['level']}] {item['id']}: {item['detail']}")
    print(json.dumps({"stage": "check", "fail": report["fail"], "warn": report["warn"],
                      "report": str(work / "check_report.json")}, ensure_ascii=False))


def cmd_build(args):
    step("build")
    input_root, work, output = resolve_dirs(args)
    case = load_case(input_root)
    keys, fallback = contract_keys(case)
    index = build_index(input_root, case)
    corpus = build_corpus(input_root, work, case)
    draft, err = read_json(work / "draft.json")
    fail_count, warn_count = 1, 0
    if not isinstance(draft, dict):
        degrade(f"draft unavailable ({err or 'not a dict'}); fallback delivery")
        draft = {}
    else:
        _items, fail_count, warn_count = validate_common(draft, corpus, output)
        try:
            fmt_items = format_checks(draft)
        except Exception as exc:  # noqa: BLE001
            fmt_items = [{"id": "format_checks", "level": "WARN", "detail": str(exc)}]
        fail_count += sum(1 for i in fmt_items if i.get("level") == "FAIL")
        warn_count += sum(1 for i in fmt_items if i.get("level") == "WARN")
    deliverable_name = draft.get("deliverable_name") or DEFAULT_DELIVERABLE
    text = str(draft.get("deliverable_text") or "")
    if not text.strip():
        text = (f"# {TASK_LABEL}\n\n（兜底交付）未找到 work/draft.json 或正文为空；"
                "本文件仅保证交付结构成立，请补齐工作稿后重新 build。\n")
        degrade("deliverable produced by fallback path")
    write_text(output / deliverable_name, text)
    extra_files = draft.get("extra_files") if isinstance(draft.get("extra_files"), dict) else {}
    for name, content in extra_files.items():
        if isinstance(name, str) and isinstance(content, str) and not name.startswith("/"):
            if ".." not in Path(name).parts:
                write_text(output / name, content)

    # 契约回填：主产物 + 三段式兜底字段 + 保留字段
    result_business = draft.get("result") if isinstance(draft.get("result"), dict) else {}
    fact_source_map = draft.get("fact_source_map") if isinstance(draft.get("fact_source_map"), list) else []
    version_boundary = draft.get("version_boundary") if isinstance(draft.get("version_boundary"), dict) else {}
    uncertainties = draft.get("uncertainties") if isinstance(draft.get("uncertainties"), list) else []
    pending = draft.get("pending_human") if isinstance(draft.get("pending_human"), list) else []
    change_log = draft.get("change_log") if isinstance(draft.get("change_log"), list) else []
    # 保留 init/scaffold 已落盘的骨架记录，再追加 build 记录（增量改进，不丢历史）
    prior_result, _prior_err = read_json(output / "result.json")
    prior_log = prior_result.get("change_log") if isinstance(prior_result, dict) and isinstance(prior_result.get("change_log"), list) else []
    for entry in prior_log:
        if isinstance(entry, dict) and entry not in change_log:
            change_log.append(entry)
    change_log.append({"ts": now_iso(), "action": "build", "target": "result.json",
                       "detail": "主交付与契约字段回填完成"})
    status = "answered" if (fail_count == 0 and text.strip() and len(re.sub(r"\s+", "", text)) >= 80) else "partial"
    if _STATE["errors"] and not case.get("case_id"):
        status = "input_error"
    payload = {
        "case_id": case.get("case_id") or "unknown-case", "status": status,
        "deliverable": deliverable_name, "deliverable_path": deliverable_name,
        "word_count": len(re.sub(r"\s+", "", text)),
        "fact_source_map": fact_source_map, "version_boundary": version_boundary,
        "result": result_business, "uncertainties": uncertainties, "pending_human": pending,
        "change_log": change_log, "sources": index.get("materials", []),
        "uncertainty": {"claims_requiring_confirmation": [], "conflicting_evidence": [],
                        "material_gaps": [], "limitations": uncertainties},
    }
    result, missing = fill_contract(keys, payload)
    if fallback:
        for k, v in (("deliverable", deliverable_name), ("fact_source_map", fact_source_map),
                     ("version_boundary", version_boundary)):
            result.setdefault(k, v)
    if missing:
        degrade("contract keys missing: " + ", ".join(missing))
    env = {"sources": index.get("materials", []),
           "uncertainty": {"claims_requiring_confirmation": [], "conflicting_evidence": [],
                           "material_gaps": [], "limitations": uncertainties},
           "pending_human": pending, "errors": list(_STATE["errors"]),
           "degradations": list(_STATE["degradations"]),
           "runtime": {"skill_version": VERSION, "task": TASK_LABEL, "steps": list(_STATE["steps"]),
                       "contract_keys": keys, "contract_fallback": fallback, "contract_missing": missing,
                       "tools": index.get("tools", {}),
                       "elapsed_ms": int((time.time() - _STATE["started"]) * 1000),
                       "generated_at": now_iso(), "check": {"fail": fail_count, "warn": warn_count}}}
    for key, value in env.items():
        if key not in result:
            result[key] = value
    if result.get("status") not in STATUS:
        result["status"] = "partial"

    # 可核验性报告：契约逐键存在性 + quote 命中统计
    verification = {"case_id": result.get("case_id"), "checked_at": now_iso(),
                    "contract_keys": keys, "contract_missing": missing,
                    "contract_fallback": fallback, "quote_checks": []}
    for item in (fact_source_map if isinstance(fact_source_map, list) else []):
        if not isinstance(item, dict):
            continue
        src, quote = str(item.get("source_id") or ""), str(item.get("quote") or "")
        hit = bool(src and quote and src in corpus and quote_hit(corpus[src], quote))
        verification["quote_checks"].append({"claim_id": item.get("claim_id"), "source_id": src,
                                             "locator": item.get("locator"), "quote_hit": hit})
    write_json(output / "verification_report.json", verification)

    # 格式报告（模板规格检查项清单，逐项可勾选）
    spec = draft.get("format_spec") if isinstance(draft.get("format_spec"), dict) else {}
    report_lines = [f"- [ ] 栏数：{spec.get('columns', '未指定')}",
                    f"- [ ] 字号：{spec.get('font_size', '未指定')}",
                    f"- [ ] 行距：{spec.get('line_spacing', '未指定')}",
                    f"- [ ] 页边距：{spec.get('margin', '未指定')}",
                    f"- [ ] 标题层级：{spec.get('heading_levels', '未指定')}",
                    f"- [ ] 图注位置：{spec.get('figure_caption_position', '未指定')}",
                    f"- [ ] 表注位置：{spec.get('table_caption_position', '未指定')}",
                    f"- [ ] 参考文献样式：{spec.get('reference_style', '未指定')}",
                    f"- [ ] 导出格式：{spec.get('export_format', '未指定')}",
                    "- [ ] 正文/图表/公式/脚注/批注/域代码/目录内容单元完整",
                    "- [ ] 导出文件可打开、字体嵌入、页码与目录可用、可编辑源文件保留"]
    write_text(output / "format_report.md", markdown_report(report_lines, "模板规格检查清单"))
    result["deliverable"] = deliverable_name
    write_json(output / "result.json", result)
    print(json.dumps({"stage": "build", "status": result.get("status"), "deliverable": deliverable_name,
                      "contract_keys": keys, "contract_missing": missing,
                      "files": sorted(p.name for p in output.rglob("*") if p.is_file()),
                      "output": str(output)}, ensure_ascii=False))


def cmd_selfcheck(args):
    step("selfcheck")
    input_root, work, output = resolve_dirs(args)
    case = load_case(input_root)
    keys, _fallback = contract_keys(case)
    result, err = read_json(output / "result.json")
    items: list[dict] = []

    def add(level, check_id, detail):
        items.append({"level": level, "id": check_id, "detail": detail})

    if err:
        add("FAIL", "result.json", err)
    elif not isinstance(result, dict):
        add("FAIL", "result.json", "not a JSON object")
    else:
        missing = [key for key in keys if key not in result]
        add("PASS" if not missing else "FAIL", "contract.keys",
            "all contract keys present" if not missing else f"missing: {missing}")
        add("PASS" if result.get("case_id") == case.get("case_id") else "WARN", "case_id",
            f"result.case_id={result.get('case_id')!r}")
        deliverable = result.get("deliverable")
        if isinstance(deliverable, str) and deliverable:
            path = output / deliverable
            add("PASS" if path.is_file() and path.stat().st_size > 0 else "FAIL", "deliverable",
                f"{deliverable} present" if path.is_file() else f"{deliverable} missing")
        else:
            add("FAIL", "deliverable", "deliverable 为空")
        add("PASS" if result.get("status") in STATUS else "FAIL", "status", f"status={result.get('status')!r}")
        add("PASS" if isinstance(result.get("change_log"), list) else "WARN", "change_log",
            f"change_log_len={len(result.get('change_log') or [])}")
    report = {"checked_at": now_iso(), "items": items,
              "pass": sum(1 for i in items if i["level"] == "PASS"),
              "warn": sum(1 for i in items if i["level"] == "WARN"),
              "fail": sum(1 for i in items if i["level"] == "FAIL")}
    write_json(work / "selfcheck.json", report)
    write_json(output / "selfcheck.json", report)
    for item in items:
        print(f"[{item['level']}] {item['id']}: {item['detail']}")
    print(json.dumps({"stage": "selfcheck", "pass": report["pass"], "warn": report["warn"],
                      "fail": report["fail"]}, ensure_ascii=False))


def emergency_finalize(args, tb):
    try:
        _input, work, output = resolve_dirs(args)
        result = {"case_id": "unknown-case", "status": "input_error",
                  "deliverable": DEFAULT_DELIVERABLE, "fact_source_map": [],
                  "version_boundary": {}, "sources": [], "uncertainty": {},
                  "pending_human": [], "errors": list(_STATE["errors"]) + [f"uncaught: {tb.strip().splitlines()[-1] if tb.strip() else 'unknown'}"],
                  "degradations": list(_STATE["degradations"]),
                  "change_log": [{"ts": now_iso(), "action": "emergency", "target": "result.json",
                                  "detail": "未捕获异常，应急落盘"}],
                  "runtime": {"skill_version": VERSION, "emergency": True,
                              "elapsed_ms": int((time.time() - _STATE["started"]) * 1000)}}
        write_json(work / "emergency_result.json", result)
        write_text(output / DEFAULT_DELIVERABLE, "（应急交付：见 result.json errors）\n")
        write_json(output / "result.json", result)
    except BaseException:
        pass
    print(json.dumps({"stage": "emergency", "status": "input_error",
                      "error": tb.strip().splitlines()[-1] if tb.strip() else "unknown"}, ensure_ascii=False))


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description=f"{TASK_LABEL}：读材料→规格抽取→核对→契约回填。")
    sub = parser.add_subparsers(dest="cmd", metavar="{init,scaffold,survey,show,check,build,selfcheck}")

    def common(instance):
        instance.add_argument("--input", default=None)
        instance.add_argument("--work", default=None)
        instance.add_argument("--output", default=None)

    for name, text in (("init", "index + partial result + draft skeleton"),
                       ("scaffold", "confirm partial skeleton (call within first 180s)"),
                       ("survey", "materials/tools/workflow"),
                       ("show", "print source paragraphs with locators"),
                       ("check", "validate draft (common + format checks)"),
                       ("build", "build deliverable + result.json + verification"),
                       ("selfcheck", "re-check output contract completeness")):
        common(sub.add_parser(name, help=text))
    show = sub.choices["show"]
    show.add_argument("--source", default=None)
    show.add_argument("--max-chars", default=6000)
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args, _unknown = parser.parse_known_args(argv)
    except SystemExit:
        return 0
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    handlers = {"init": cmd_init, "scaffold": cmd_scaffold, "survey": cmd_survey,
                "show": cmd_show, "check": cmd_check, "build": cmd_build, "selfcheck": cmd_selfcheck}
    try:
        handlers[args.cmd](args)
    except BaseException:
        emergency_finalize(args, traceback.format_exc())
    return 0


if __name__ == "__main__":
    sys.exit(main())
