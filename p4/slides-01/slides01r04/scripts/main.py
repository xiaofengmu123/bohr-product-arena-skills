#!/usr/bin/env python3
"""slides-01 Skill 通用入口：契约骨架 → 页级计划 → 合并成稿 → 页数/证据自检。

用法（相对本脚本目录调用，见 SKILL.md §8）：
  python3 -B scripts/main.py --cmd skeleton   --input /input --work /app/work --output /app/output
  python3 -B scripts/main.py --cmd plan       --input /input --work /app/work --output /app/output
  python3 -B scripts/main.py --cmd finalize   --input /input --work /app/work --output /app/output
  python3 -B scripts/main.py --cmd pdfcheck   --output /app/output
  python3 -B scripts/main.py --cmd audit      --work /app/work --output /app/output

每个命令退出码：
  0 表示成功（即使 status=partial / insufficient_evidence / input_error 也算“成功运行”）
  2 表示用法错误
只依赖标准库；本文件保持为薄分发层，重逻辑在 core_impl.py。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import core_impl as C  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _resolve(args):
    """解析 --input/--work/--output；目录不可写时降级到可写位置并返回 degradation。"""
    degradations = []
    input_root = Path(args.input).expanduser() if args.input else C.DEFAULT_INPUT
    work = Path(args.work).expanduser() if args.work else C.DEFAULT_WORK
    wanted = Path(args.output).expanduser() if args.output else C.DEFAULT_OUTPUT

    ok, err = C.ensure_dir(work)
    if not ok:
        fallback = Path.cwd() / "skill_work"
        if C.ensure_dir(fallback)[0]:
            degradations.append(f"work 目录 {work} 不可写（{err}），降级到 {fallback}")
            work = fallback
    output = wanted
    ok, err = C.ensure_dir(output)
    if not ok:
        for cand in (work / "output_fallback", Path.cwd() / "skill_output"):
            if C.ensure_dir(cand)[0]:
                degradations.append(f"output 目录 {wanted} 不可写（{err}），降级到 {cand}")
                output = cand
                break
    return input_root, work, output, degradations


def _load_case(input_root: Path):
    case_path, _task_path, _mats = C.discover_materials(input_root)
    if case_path is None:
        return {}, None, "case.json 不存在"
    case, err = C.read_json(case_path)
    if err:
        return {}, case_path, f"case.json 不可用: {err}"
    if not isinstance(case, dict):
        return {}, case_path, "case.json 顶层不是对象"
    return case, case_path, None


def _case_id(case: dict) -> str:
    if isinstance(case.get("case_id"), str) and case["case_id"].strip():
        return case["case_id"].strip()
    if isinstance(case.get("id"), str) and case["id"].strip():
        return case["id"].strip()
    return "unknown-case"


def _envelope(case_id: str, status: str) -> dict:
    return {
        "case_id": case_id,
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started": C.now_iso(), "tool": "slides-01-skill",
                    "version": C.VERSION},
    }


def cmd_skeleton(input_root, work, output, degradations):
    """第一步：立刻写合法 result.json 骨架（partial），不留空产物。"""
    case, case_path, err = _load_case(input_root)
    status = C.STATUS_PARTIAL
    if err:
        status = C.STATUS_INPUT_ERROR
    case_id = _case_id(case)
    keys = C.contract_keys(case)

    envelope = _envelope(case_id, status)
    if err:
        envelope["errors"].append(err)
    envelope["degradations"].extend(degradations)
    if case_path is None:
        envelope["limitations"].append("未发现 case.json，无法读取 output_contract")
        envelope["pending_human"].append("请提供 /input/case.json 后重跑")

    filled, missing = C.fill_contract(keys, envelope)
    filled.update(envelope)  # 信封字段一定写入，即使不在 contract 中
    filled["case_id"] = case_id
    filled["status"] = status
    filled["runtime"]["missing_contract_fields"] = missing

    result_path = output / "result.json"
    ok = C.write_json(result_path, filled)
    print(C._json_summary({
        "cmd": "skeleton", "case_id": case_id, "status": status,
        "contract_fields": keys, "missing_contract_fields": missing,
        "result": str(result_path), "written": ok,
    }))
    return 0 if ok else 1


def cmd_plan(input_root, work, output, degradations):
    """第二步：写 work/draft.json 的页级计划骨架（内容由 Agent 逐项填，脚本不代写）。"""
    case, case_path, err = _load_case(input_root)
    case_id = _case_id(case)

    draft = {
        "case_id": case_id,
        "audience": "",          # 目标听众（从 task.md 提取）
        "duration_min": None,    # 时长上限
        "slide_limit": None,     # 页数上限
        "mainline": [],          # 页序叙事骨架：每项 {order, claim, role, evidence_ids}
        "content_map": [],       # 每页：{slide, title, claim, role, evidence[], notes, evidence_status}
        "evidence_index": {},    # evidence_id -> {locator, quote, status}
        "limitations": [],
        "uncertainty": [],
        "notes": [],
    }
    # 若已有 draft.json 且为 dict，保留其内容（增量，不覆盖已完成工作）
    draft_path = work / "draft.json"
    if draft_path.is_file():
        prev, perr = C.read_json(draft_path)
        if perr is None and isinstance(prev, dict):
            for k, v in prev.items():
                if v not in (None, "", [], {}):
                    draft[k] = v

    if err:
        draft["notes"].append(err)
    draft["notes"].extend(degradations)

    ok = C.write_json(draft_path, draft)
    print(C._json_summary({
        "cmd": "plan", "case_id": case_id, "draft": str(draft_path),
        "written": ok, "notes": draft["notes"],
    }))
    return 0 if ok else 1


def cmd_finalize(input_root, work, output, degradations):
    """第三步：把 Agent 填好的 draft.json 合并进 result.json 并校验。"""
    case, case_path, err = _load_case(input_root)
    case_id = _case_id(case)
    keys = C.contract_keys(case)
    envelope = _envelope(case_id, C.STATUS_PARTIAL)

    draft_path = work / "draft.json"
    draft = {}
    if draft_path.is_file():
        d, derr = C.read_json(draft_path)
        if derr:
            envelope["errors"].append(f"draft.json 不可用: {derr}")
        elif isinstance(d, dict):
            draft = d
    else:
        envelope["errors"].append("未找到 work/draft.json，输出按契约缺省值")

    # 主产物指向（契约字段 editable_source / export_artifact）
    editable = draft.get("editable_source")
    if isinstance(editable, dict):
        envelope["editable_source"] = editable
    export = draft.get("export_artifact")
    if isinstance(export, dict):
        envelope["export_artifact"] = export

    # content_map：规范证据条目
    cm = draft.get("content_map")
    if isinstance(cm, list):
        norm = []
        for item in cm:
            if not isinstance(item, dict):
                continue
            evs = item.get("evidence")
            if isinstance(evs, list):
                item = dict(item)
                item["evidence"] = [C.normalize_evidence(e) for e in evs]
            norm.append(item)
        envelope["content_map"] = norm

    for key in C.ENVELOPE_LIST_FIELDS:
        if isinstance(draft.get(key), list):
            envelope[key] = list(draft[key])
    for key in C.ENVELOPE_DICT_FIELDS:
        if isinstance(draft.get(key), dict):
            envelope[key] = dict(draft[key])
    envelope["degradations"].extend(degradations)
    if err:
        envelope["errors"].append(err)
        envelope["status"] = C.STATUS_INPUT_ERROR

    # 状态诚实化：有 errors 且无内容 → input_error/insufficient
    if envelope["status"] == C.STATUS_PARTIAL:
        if not isinstance(envelope.get("content_map"), list) or not envelope["content_map"]:
            envelope["status"] = C.STATUS_INSUFFICIENT if not err else C.STATUS_INPUT_ERROR

    filled, missing = C.fill_contract(keys, envelope)
    filled.update(envelope)
    filled["case_id"] = case_id
    filled["runtime"]["missing_contract_fields"] = missing
    filled["runtime"]["finished"] = C.now_iso()

    result_path = output / "result.json"
    ok = C.write_json(result_path, filled)
    print(C._json_summary({
        "cmd": "finalize", "case_id": case_id, "status": filled["status"],
        "slides": len(filled.get("content_map", [])),
        "result": str(result_path), "written": ok,
        "missing_contract_fields": missing,
    }))
    return 0 if ok else 1


def cmd_pdfcheck(output, _work, degradations):
    """第四步：校验导出 PDF 与 pptx 页数是否一致，失败记 degradation（不崩溃）。"""
    result_path = output / "result.json"
    res, err = C.read_json(result_path)
    report = {"cmd": "pdfcheck", "ok": True, "degradations": []}

    if err or not isinstance(res, dict):
        report["ok"] = False
        report["degradations"].append(f"result.json 不可用: {err or '非对象'}")
        print(C._json_summary(report))
        return 0

    editable = res.get("editable_source") or {}
    export = res.get("export_artifact") or {}
    pptx_rel = (editable or {}).get("path") or (export or {}).get("pptx", {}).get("path")
    pdf_rel = None
    if isinstance(export, dict):
        pdf_rel = (export.get("pdf") or {}).get("path") if isinstance(export.get("pdf"), dict) else export.get("pdf_path")

    slides = None
    pages = None
    if pptx_rel:
        pptx_path = output / pptx_rel
        slides = C.pptx_slide_count(pptx_path)
        if slides is None:
            report["degradations"].append(f"无法统计 pptx 页数: {pptx_rel}")
    if pdf_rel:
        pdf_path = output / pdf_rel
        if not C.pdf_is_valid(pdf_path):
            report["degradations"].append(f"PDF 头非法或不存在: {pdf_rel}")
        else:
            pages = C.pdf_page_count(pdf_path)
            if pages is None:
                report["degradations"].append(f"PDF 页数无法可靠统计（可能对象流压缩）: {pdf_rel}")
                res.setdefault("uncertainty", []).append(
                    {"about": "pdf_page_count", "reason": "PDF 页对象被压缩，无法精确统计", "mitigation": "人工打开确认页数"})

    if slides is not None and pages is not None and slides != pages:
        msg = f"页数不一致: pptx {slides} 页 vs PDF {pages} 页"
        report["degradations"].append(msg)
        res.setdefault("degradations", []).append(msg)
    report["slides"] = slides
    report["pages"] = pages
    report["degradations"].extend(degradations)

    if report["degradations"]:
        report["ok"] = False
        # 回写 result.json，把校验降级/不确定性落盘
        C.write_json(result_path, res)
    print(C._json_summary(report))
    return 0


def cmd_audit(_input, work, _output, degradations):
    """第五步：审计每页证据（locator + quote 是否齐全），报告缺口，不编造。"""
    draft_path = work / "draft.json"
    draft, err = C.read_json(draft_path)
    report = {"cmd": "audit", "ok": True, "gaps": []}
    if err or not isinstance(draft, dict):
        report["ok"] = False
        report["gaps"].append(f"draft.json 不可用: {err or '非对象'}")
        print(C._json_summary(report))
        return 0

    cm = draft.get("content_map")
    if not isinstance(cm, list) or not cm:
        report["gaps"].append("content_map 为空：尚无页级计划")
        report["ok"] = False
    else:
        for i, item in enumerate(cm):
            if not isinstance(item, dict):
                report["gaps"].append(f"content_map[{i}] 非对象")
                continue
            evs = item.get("evidence")
            if not isinstance(evs, list) or not evs:
                report["gaps"].append(f"slide {item.get('slide', i)} 无 evidence")
                continue
            for e in evs:
                e = C.normalize_evidence(e)
                if e["status"] == C.EVIDENCE_NOT_REPORTED:
                    continue  # 已诚实标注缺失，不算 gap
                if not e["locator"]:
                    report["gaps"].append(f"slide {item.get('slide', i)} 证据缺 locator")
                if e["status"] == C.EVIDENCE_VERIFIED and not e["quote"]:
                    report["gaps"].append(f"slide {item.get('slide', i)} 证据标 verified 但缺逐字 quote")

    if report["gaps"]:
        report["ok"] = False
    report["degradations"] = degradations
    print(C._json_summary(report))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="slides-01 论文转学术 PPT Skill 入口（契约骨架/计划/成稿/自检）",
    )
    parser.add_argument("--cmd", required=True,
                        choices=["skeleton", "plan", "finalize", "pdfcheck", "audit"],
                        help="要执行的子命令")
    parser.add_argument("--input", default=None, help="输入目录（缺省 /input）")
    parser.add_argument("--work", default=None, help="工作目录（缺省 /app/work）")
    parser.add_argument("--output", default=None, help="输出目录（缺省 /app/output）")
    args = parser.parse_args(argv)

    input_root, work, output, degradations = _resolve(args)
    handlers = {
        "skeleton": cmd_skeleton,
        "plan": cmd_plan,
        "finalize": cmd_finalize,
        "pdfcheck": cmd_pdfcheck,
        "audit": cmd_audit,
    }
    if args.cmd == "pdfcheck":
        return cmd_pdfcheck(output, work, degradations)
    if args.cmd == "audit":
        return cmd_audit(input_root, work, output, degradations)
    return handlers[args.cmd](input_root, work, output, degradations)


if __name__ == "__main__":
    sys.exit(main())
