#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — academic-slides-evidence 的确定性编排入口（纯 Python 标准库）。

按「先骨架、后增量」原则驱动：init 阶段即落盘一份合法的 partial result.json，
之后 evidence / validate / finalize 只做增量填充；任何一步失败都保留已落盘的部分结果。

典型调用（脚本路径一律相对 Skill 根目录）：
  python scripts/main.py --input /input --output /app/output --work /app/work
  python scripts/main.py --phase init     --input /input --output /app/output --work /app/work
  python scripts/main.py --phase evidence --input /input --output /app/output --work /app/work
  python scripts/main.py --phase validate --input /input --output /app/output --work /app/work \
      --pptx /app/output/deck.pptx --pdf /app/output/deck.pdf --png /app/output/s1.png
  python scripts/main.py --phase finalize --input /input --output /app/output --work /app/work

重复运行幂等：各阶段只重算自己负责的字段，未知键保留，change_log 去重追加。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as core  # noqa: E402

# 三个契约字段的规范骨架；若 case.json 自带 output_contract，则以其键为准并补齐。
CANONICAL = {
    "presentation": {
        "pptx_path": None, "pdf_path": None, "png_paths": [],
        "slide_count": None, "page_plan": [],
    },
    "evidence_map": {"schema": "evidence-map/v2", "pages": []},
    "render_record": {
        "source_files": [], "template_file": None, "layouts_used": [],
        "fonts": [], "exports": [], "integrity": None, "media": [],
        "checks": [],
    },
}
LIST_KEYS = ("sources", "uncertainty", "pending_human", "errors",
             "degradations", "change_log")


def _load_result(output: str) -> dict:
    obj, _err = core.read_json_robust(os.path.join(output, "result.json"))
    return obj if isinstance(obj, dict) else {}


def _save_result(output: str, obj: dict) -> None:
    core.write_json(os.path.join(output, "result.json"), obj)


def _state(args) -> dict:
    obj, _ = core.read_json_robust(os.path.join(args.work, "state.json"))
    return obj if isinstance(obj, dict) else {}


def _mark(args, phase: str) -> None:
    st = _state(args)
    st.setdefault("phases", {})
    st["phases"][phase] = core.now_iso()
    core.write_json(os.path.join(args.work, "state.json"), st)


def _log_change(result: dict, phase: str, detail: str, status=None) -> None:
    items = result.setdefault("change_log", [])
    entry = {"at": core.now_iso(), "phase": phase, "detail": detail}
    if status:
        entry["status"] = status
    if items and items[-1].get("phase") == phase and items[-1].get("detail") == detail:
        return  # 幂等：相同阶段重复运行不重复记账
    items.append(entry)


def _merge_list(dst: list, src: list) -> list:
    out = list(dst or [])
    for item in src or []:
        if item not in out:
            out.append(item)
    return out


def _case_id(input_dir: str, inv: dict) -> str:
    if inv.get("case_json"):
        obj, _ = core.read_json_robust(inv["case_json"])
        if isinstance(obj, dict):
            for k in ("case_id", "id", "armiId"):
                if obj.get(k):
                    return str(obj[k])
    if inv.get("task_md"):
        text, _ = core.read_text_robust(inv["task_md"])
        if text:
            m = re.search(r"challenge[_ -]?id[:：]\s*[`\"']?([A-Za-z0-9._-]+)", text)
            if m:
                return m.group(1)
    return "unknown_case"


def _find_one(directory: str, pattern: str):
    hits = sorted(glob.glob(os.path.join(directory, pattern)))
    return hits[0] if hits else None


# --------------------------------------------------------------------------- #
# 阶段实现
# --------------------------------------------------------------------------- #
def phase_init(args) -> dict:
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    result = _load_result(args.output)
    inv = core.discover_input(args.input)
    case_id = _case_id(args.input, inv)

    result.setdefault("case_id", case_id)
    result.setdefault("status", "in_progress")
    result.setdefault("runtime", {})
    result["runtime"]["started_at"] = result["runtime"].get("started_at") or core.now_iso()

    # 契约：case.json 的 output_contract 优先，否则用规范三字段。
    contract = {}
    if inv.get("case_json"):
        case_obj, cerr = core.read_json_robust(inv["case_json"])
        if isinstance(case_obj, dict):
            oc = case_obj.get("output_contract")
            if isinstance(oc, dict):
                contract = oc
        else:
            result.setdefault("errors", []).append(
                {"code": "bad_case_json", "detail": "%s: %s" % (inv["case_json"], cerr or "无法解析")})
            result.setdefault("degradations", []).append(
                {"code": "bad_case_json", "detail": "沿用规范契约继续处理"})
    if not contract:
        contract = json.loads(json.dumps(CANONICAL))
    for key in contract:
        if key not in result:
            result[key] = None

    # 规范三字段补齐（若值为 null 则填充骨架），保留题目契约的未知键不动。
    for key, skeleton in CANONICAL.items():
        if result.get(key) is None:
            result[key] = json.loads(json.dumps(skeleton))
        elif key in ("evidence_map", "render_record") and isinstance(result[key], dict):
            for sub, default in skeleton.items():
                result[key].setdefault(sub, default)
        elif key == "presentation" and isinstance(result[key], dict):
            for sub, default in skeleton.items():
                result[key].setdefault(sub, default)

    # sources
    sources = []
    if inv.get("case_json"):
        sources.append({"rel": "case.json", "path": inv["case_json"], "kind": "data"})
    if inv.get("task_md"):
        sources.append({"rel": "task.md", "path": inv["task_md"], "kind": "markdown"})
    sources += [{"rel": m["rel"], "path": m["path"], "kind": m["kind"],
                 "size_bytes": m["size_bytes"]} for m in inv["materials"]]
    result["sources"] = sources

    for k in ("uncertainty", "pending_human", "errors", "degradations", "change_log"):
        result.setdefault(k, [])

    if not inv.get("case_json") and not inv.get("materials"):
        result["status"] = "insufficient_evidence"
        result["uncertainty"] = _merge_list(
            result["uncertainty"],
            [{"code": "empty_input", "detail": "未发现 case.json 与可读材料"}])

    core.write_json(os.path.join(args.work, "input_inventory.json"),
                    {"inventory": inv, "case_id": case_id})
    _save_result(args.output, result)
    _log_change(result, "init", "落盘结果骨架", status=result.get("status"))
    _save_result(args.output, result)
    _mark(args, "init")
    return result


def _load_extracted_texts(args):
    """收集 work/extracted 与输入目录中的 .txt/.md 文本，返回 (texts, page_maps)。"""
    texts, page_maps = {}, {}
    roots = []
    for d in (os.path.join(args.work, "extracted"), args.work, args.input):
        if os.path.isdir(d):
            roots.append(d)
    seen = set()
    for d in roots:
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith((".txt", ".md")):
                    continue
                p = os.path.join(root, fn)
                rp = os.path.abspath(p)
                if rp in seen:
                    continue
                seen.add(rp)
                t, err = core.read_text_robust(p)
                if t is None:
                    continue
                rel = os.path.relpath(p, args.work if os.path.isdir(args.work) else args.input)
                rel = rel.replace("\\", "/")
                if rel.startswith("../"):
                    rel = os.path.basename(p)
                texts[rel] = t
                page_maps[rel] = core.split_pages(t)
    return texts, page_maps


def phase_evidence(args) -> dict:
    result = _load_result(args.output)
    if not result:
        result = {}
    plan_path = os.path.join(args.work, "page_plan.json")
    plan, _ = core.read_json_robust(plan_path)
    if not isinstance(plan, dict):
        plan = {}
    if not plan.get("pages"):
        pres = result.get("presentation")
        plan = pres.get("page_plan") if isinstance(pres, dict) else {}
    texts, page_maps = _load_extracted_texts(args)
    emap = core.build_evidence_map(plan, texts, page_maps)

    result["evidence_map"] = emap
    pres = result.setdefault("presentation", {})
    if isinstance(pres, dict) and not pres.get("page_plan"):
        pres["page_plan"] = plan.get("pages", []) if isinstance(plan, dict) else []
    result["pending_human"] = _merge_list(result.get("pending_human"),
                                          emap.get("pending_human", []))
    core.write_json(os.path.join(args.work, "evidence_map.json"), emap)
    _save_result(args.output, result)
    _log_change(result, "evidence", "生成页级证据映射（locator+逐字 quote）")
    _save_result(args.output, result)
    _mark(args, "evidence")
    return result


def phase_validate(args) -> dict:
    result = _load_result(args.output)
    if not result:
        result = {}
    rr = result.get("render_record")
    if not isinstance(rr, dict):
        rr = json.loads(json.dumps(CANONICAL["render_record"]))
    result["render_record"] = rr

    pres = result.setdefault("presentation", {})
    pptx = args.pptx or (pres.get("pptx_path") if isinstance(pres, dict) else None) \
        or _find_one(args.output, "*.pptx")
    if pptx and os.path.isfile(pptx):
        info = core.inspect_pptx(pptx)
        rr["integrity"] = info
        pres["pptx_path"] = pptx
        pres["slide_count"] = info.get("slide_count")
        rr["media"] = info.get("media", [])
        rr["checks"] = [{
            "name": "pptx_zip_ok", "auto": True,
            "result": "pass" if info.get("pptx_zip_ok") else "fail",
            "detail": info.get("error"),
        }]
        rr["checks"] += core.overflow_heuristic(pptx)
    else:
        rr["integrity"] = {"pptx_zip_ok": False, "error": "pptx 未找到"}
        rr.setdefault("checks", []).append({
            "name": "pptx_zip_ok", "auto": True, "result": "fail",
            "detail": "pptx 未找到，可能尚未生成"})

    pdf = args.pdf or (pres.get("pdf_path") if isinstance(pres, dict) else None) \
        or _find_one(args.output, "*.pdf")
    pngs = list(args.png or []) or _find_pngs(args.output, pres)
    rr["exports"] = []
    if pdf and os.path.isfile(pdf):
        rr["exports"].append({"path": pdf, "format": "pdf", "ok": core.looks_pdf(pdf)})
        pres["pdf_path"] = pdf
    for p in pngs:
        if os.path.isfile(p):
            rr["exports"].append({"path": p, "format": "png", "ok": core.looks_png(p)})
    if isinstance(pres, dict):
        pres["png_paths"] = [p for p in pngs if os.path.isfile(p)]

    core.write_json(os.path.join(args.work, "render_record.json"), rr)
    _save_result(args.output, result)
    _log_change(result, "validate", "pptx/PDF/PNG 完整性检查")
    _save_result(args.output, result)
    _mark(args, "validate")
    return result


def _find_pngs(directory: str, pres: dict) -> list:
    hits = sorted(glob.glob(os.path.join(directory, "*.png")))
    if hits:
        return hits
    return list(pres.get("png_paths") or []) if isinstance(pres, dict) else []


def phase_finalize(args) -> dict:
    result = _load_result(args.output)
    if not result:
        result = {}
    pres = result.get("presentation") or {}
    emap = result.get("evidence_map") or {}
    ok_pptx = bool(isinstance(pres, dict) and pres.get("pptx_path")
                   and os.path.isfile(pres["pptx_path"]))
    ok_evidence = bool(emap.get("pages") if isinstance(emap, dict) else [])
    ok_preview = bool(isinstance(pres, dict) and pres.get("pdf_path")
                      and os.path.isfile(pres["pdf_path"]))
    if ok_pptx and ok_evidence and ok_preview:
        status = "ok"
    elif ok_pptx or ok_evidence:
        status = "partial"
    else:
        status = result.get("status") or "insufficient_evidence"
    result["status"] = status

    for k in LIST_KEYS:
        result.setdefault(k, [])
    result.setdefault("case_id", "unknown_case")
    rt = result.setdefault("runtime", {})
    rt["finished_at"] = core.now_iso()
    st = _state(args)
    rt["phase_order"] = list((st.get("phases") or {}).keys())
    _log_change(result, "finalize", "收口 status=%s" % status)
    _save_result(args.output, result)
    _mark(args, "finalize")
    return result


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def _parser():
    p = argparse.ArgumentParser(
        description="academic-slides-evidence：学术 PPT 模板适配 + 证据溯源编排")
    p.add_argument("--input", default="/input", help="运行时输入目录")
    p.add_argument("--output", default="/app/output", help="最终产物目录")
    p.add_argument("--work", default="/app/work", help="中间工作目录")
    p.add_argument("--phase", default="all",
                   choices=["all", "init", "evidence", "validate", "finalize"])
    p.add_argument("--pptx", default=None, help="pptx 路径（不填则自动推断）")
    p.add_argument("--pdf", default=None, help="PDF 预览路径（不填则自动推断）")
    p.add_argument("--png", action="append", default=None,
                   help="PNG 预览路径，可多次传入")
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    phase = args.phase
    try:
        if phase in ("all", "init"):
            phase_init(args)
        if phase in ("all", "evidence"):
            phase_evidence(args)
        if phase in ("all", "validate"):
            phase_validate(args)
        if phase in ("all", "finalize"):
            phase_finalize(args)
        return 0
    except Exception as e:  # 兜底：任何异常都尽量留下合法部分结果
        result = _load_result(args.output)
        if not result:
            result = {"case_id": "unknown_case", "status": "failed"}
        result.setdefault("errors", []).append(
            {"code": "unhandled", "phase": phase,
             "detail": "%s: %s" % (type(e).__name__, e)})
        result["status"] = result.get("status") or "partial"
        try:
            _save_result(args.output, result)
        except OSError:
            pass
        sys.stderr.write("main.py[%s] failed: %s: %s\n"
                         % (phase, type(e).__name__, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
