#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-driven-writing 主入口（仅 Python 标准库）。

子命令（不带参数时打印帮助）：
  scaffold  读取输入，立即落盘 partial result.json 骨架（status=in_progress），
            并写 /app/work/manifest.json。不覆盖已存在且 status != in_progress 的结果。
  extract   抽取材料文本（PDF 优先 pdftotext -layout，缺工具则记录 limitation），
            写 /app/work/texts/*.txt、/app/work/locator_index.json、/app/work/source_map.json。
  analyze   基于抽取文本生成论断切分模板 /app/work/analysis.json 与可引用候选扫描。
  validate  校验 result.json 契约字段与 evidence_map 的逐字 quote 回查，写
            /app/work/validation_report.json。
  finalize  执行 validate，按校验结果推进 status，补 change_log，并渲染可读版
            /app/output/evidence_report.md（不新增材料外事实）。

路径缺省值：--input /input --output /app/output --work /app/work。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evidence import (  # noqa: E402
    DOC_KIND_BY_EXT,
    build_locator_index,
    extract_candidates,
    locator_paragraph_map,
    new_claim_entry,
    new_support_entry,
    now_iso,
    read_text_robust,
    validate_claim,
)

DEFAULT_CONTRACT_KEYS = ("deliverable", "sources", "change_log")
ALLOWED_STATUS = ("in_progress", "complete", "partial", "failed", "input_error", "insufficient_evidence")
TOP_KEYS = ("case_id", "status", "deliverable", "sources", "uncertainty",
            "pending_human", "change_log", "limitations")


def _log(msg):
    print(msg, file=sys.stderr)


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path):
    txt, enc = read_text_robust(path)
    if txt is None:
        return None, enc
    try:
        return json.loads(txt), enc
    except Exception as e:  # noqa: BLE001
        return None, "bad_json:%s" % e


def _load_json(path):
    obj, _ = _read_json(path)
    return obj


def _find_case_file(inp):
    if not os.path.isdir(inp):
        return None
    names = os.listdir(inp)
    for want in ("case.json", "case.yaml", "case.yml"):
        if want in names:
            return os.path.join(inp, want)
    for n in sorted(names):
        if n.lower().endswith(".json"):
            return os.path.join(inp, n)
    return None


def _discover_case_id(case, task_text):
    for k in ("case_id", "caseId", "id"):
        if isinstance(case, dict) and case.get(k):
            return str(case[k])
    if task_text:
        m = re.search(r"challenge_id:\s*[`'\"]?([A-Za-z0-9\-_.]+)", task_text)
        if m:
            return m.group(1)
    return "unknown"


def _discover_contract(case):
    if not isinstance(case, dict):
        return {}
    for k in ("output_contract", "outputContract", "contract", "result_schema"):
        if isinstance(case.get(k), dict):
            return case[k]
    return {}


def _merge_contract_keys(result, contract):
    """回显 output_contract 全部键；缺失值给 null，不改名、不删未知键。"""
    for k in contract:
        if k not in result:
            result[k] = None
    return result


def _load_result(path):
    obj, _ = _read_json(path)
    return obj if isinstance(obj, dict) else None


def _iter_materials(inp, skip_names=("output", "work", ".git")):
    """递归发现材料文件（限深，跳过产物目录）。"""
    found = []
    if not os.path.isdir(inp):
        return found
    for root, dirs, files in os.walk(inp):
        dirs[:] = [d for d in dirs if d not in skip_names]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, inp).replace("\\", "/")
            ext = os.path.splitext(f)[1].lower()
            if f in ("case.json", "task.md") or f.startswith("."):
                continue
            found.append({"path": full, "rel": rel, "kind": DOC_KIND_BY_EXT.get(ext, "other")})
    return found


def run_scaffold(a):
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.output, exist_ok=True)
    out_path = os.path.join(a.output, "result.json")

    existing = _load_result(out_path)
    if existing and existing.get("status") and existing.get("status") != "in_progress":
        _log("result.json 已存在且 status=%s，scaffold 不覆盖（只做增量改进）" % existing.get("status"))
        return {"ok": True, "skipped": True, "status": existing.get("status")}

    case_path = _find_case_file(a.input)
    case = {}
    case_err = None
    if case_path:
        case, case_err = _read_json(case_path)
        if case is None:
            case = {}
            case_err = case_err or "empty"

    task_path = os.path.join(a.input, "task.md")
    task_text = None
    if os.path.isfile(task_path):
        task_text, _ = read_text_robust(task_path)
        task_text = task_text or ""

    case_id = _discover_case_id(case, task_text or "")
    contract = _discover_contract(case)
    contract_keys = list(contract.keys()) or list(DEFAULT_CONTRACT_KEYS)

    status = "in_progress"
    if case_path is None:
        status = "input_error"

    result = {
        "case_id": case_id,
        "status": status,
        "deliverable": {
            "draft": "",
            "claim_list": [],
            "evidence_map": [],
            "summary": "",
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "limitations": [],
    }
    result = _merge_contract_keys(result, contract)
    result["change_log"].append({
        "time": now_iso(), "action": "scaffold",
        "detail": "读取 case_id=%s，output_contract 键=%s" % (case_id, contract_keys or "default"),
    })
    if case_err:
        result["limitations"].append("case.json 解析异常：%s" % case_err)
        result["change_log"].append({"time": now_iso(), "action": "scaffold", "detail": "case 解析降级：%s" % case_err})
    if case_path is None:
        result["limitations"].append("未找到 case.json/case.yaml，无法确定 case_id 与契约")

    _write_json(out_path, result)

    manifest = {
        "created": now_iso(),
        "input": a.input,
        "case_file": os.path.relpath(case_path, a.input).replace("\\", "/") if case_path else None,
        "case_id": case_id,
        "contract_keys": contract_keys,
        "status": status,
        "materials": _iter_materials(a.input),
    }
    _write_json(os.path.join(a.work, "manifest.json"), manifest)
    return {"ok": True, "case_id": case_id, "status": status,
            "materials": len(manifest["materials"]), "contract_keys": contract_keys}


def run_extract(a):
    os.makedirs(os.path.join(a.work, "texts"), exist_ok=True)
    manifest_path = os.path.join(a.work, "manifest.json")
    manifest = _load_json(manifest_path) or {}
    materials = manifest.get("materials") or _iter_materials(a.input)

    pdf2txt = shutil.which("pdftotext")
    source_map = {}
    limitations = []
    errors = []
    counter = {}

    for m in materials:
        full, rel = m["path"], m["rel"]
        stem = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff]+", "_", os.path.splitext(rel)[0]).strip("_") or "doc"
        n = counter.get(stem, 0) + 1
        counter[stem] = n
        txt_name = stem if n == 1 else "%s_%d" % (stem, n)
        txt_path = os.path.join(a.work, "texts", txt_name + ".txt")

        if m["kind"] == "pdf":
            if not pdf2txt:
                limitations.append("无 pdftotext，PDF 未抽取文本：%s" % rel)
                continue
            r = subprocess.run([pdf2txt, "-layout", full, txt_path],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or not os.path.isfile(txt_path):
                errors.append("pdftotext 失败(%d)：%s" % (r.returncode, rel))
                continue
            source_map[rel] = txt_path
        elif m["kind"] in ("text", "markdown", "csv", "json", "jsonl", "yml", "xml", "latex"):
            txt, err = read_text_robust(full)
            if txt is None:
                limitations.append("文本读取失败(%s)：%s" % (err, rel))
                continue
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(txt)
            source_map[rel] = txt_path
        else:
            limitations.append("跳过不支持类型(%s)：%s" % (m["kind"], rel))

    # 建立 locator 索引
    docs = []
    for rel, txt_path in sorted(source_map.items()):
        txt, err = read_text_robust(txt_path)
        if txt is None:
            limitations.append("抽取文本不可读(%s)：%s" % (err, rel))
            continue
        idx = build_locator_index(rel, txt)
        idx["txt_file"] = os.path.relpath(txt_path, a.work).replace("\\", "/")
        docs.append(idx)

    locator_index = {"documents": docs, "created": now_iso()}
    _write_json(os.path.join(a.work, "locator_index.json"), locator_index)
    _write_json(os.path.join(a.work, "source_map.json"), source_map)

    manifest = manifest or {}
    manifest["extract"] = {
        "time": now_iso(),
        "documents": len(docs),
        "limitations": limitations,
        "errors": errors,
    }
    _write_json(manifest_path, manifest)

    out = os.path.join(a.output, "result.json")
    res = _load_result(out)
    if isinstance(res, dict):
        res.setdefault("limitations", [])
        for x in limitations + errors:
            res["limitations"].append(x)
        res["change_log"].append({"time": now_iso(), "action": "extract",
                                  "detail": "抽取 %d 个文档，%d 条限制" % (len(docs), len(limitations) + len(errors))})
        _write_json(out, res)

    return {"ok": True, "documents": len(docs), "limitations": limitations, "errors": errors}


def run_analyze(a):
    loc_path = os.path.join(a.work, "locator_index.json")
    loc = _load_json(loc_path) or {}
    docs = loc.get("documents") or []
    analysis = {
        "created": now_iso(),
        "claim_units": [],
        "candidates": [],
        "doc_summary": [{"source_doc": d.get("source_doc"),
                         "paragraph_count": d.get("paragraph_count"),
                         "section_count": len(d.get("sections") or []),
                         "locator_count": len(d.get("locators") or {})} for d in docs],
    }
    # 可引用候选扫描（提示性，不替代人工判定）
    smap = _load_json(os.path.join(a.work, "source_map.json")) or {}
    for d in docs:
        txt_file = d.get("txt_file")
        if not txt_file:
            continue
        full = os.path.join(a.work, txt_file)
        txt, _ = read_text_robust(full)
        if txt:
            analysis["candidates"].extend(extract_candidates(txt, d.get("source_doc")))
    _write_json(os.path.join(a.work, "analysis.json"), analysis)
    return {"ok": True, "documents": len(docs), "candidates": len(analysis["candidates"])}


def _build_doc_texts(a):
    smap = _load_json(os.path.join(a.work, "source_map.json")) or {}
    doc_texts = {}
    for rel, txt_path in smap.items():
        txt, _ = read_text_robust(txt_path)
        if txt is None:
            continue
        for key in (rel, os.path.basename(rel), os.path.splitext(os.path.basename(rel))[0]):
            doc_texts.setdefault(key, txt)
    return doc_texts


def run_validate(a):
    out = os.path.join(a.output, "result.json")
    res = _load_result(out)
    report = {"time": now_iso(), "ok": False, "issues": [], "warnings": []}
    if not isinstance(res, dict):
        report["issues"].append("result.json 缺失或非法")
        _write_json(os.path.join(a.work, "validation_report.json"), report)
        return {"ok": False, "issues": report["issues"]}

    for k in TOP_KEYS:
        if k not in res:
            report["issues"].append("result.json 缺字段：%s" % k)
    if res.get("case_id") in (None, ""):
        report["issues"].append("result.json 缺 case_id")
    if res.get("status") not in ALLOWED_STATUS:
        report["issues"].append("result.json status 非法：%r" % res.get("status"))

    # 回显 output_contract 键：从 manifest 读取
    manifest = _load_json(os.path.join(a.work, "manifest.json")) or {}
    for k in manifest.get("contract_keys") or DEFAULT_CONTRACT_KEYS:
        if k not in res:
            report["issues"].append("result.json 缺 output_contract 键：%s" % k)

    doc_texts = _build_doc_texts(a)
    loc_index = _load_json(os.path.join(a.work, "locator_index.json")) or {}
    known_locators = locator_paragraph_map(loc_index)
    emap = res.get("deliverable", {}).get("evidence_map") or res.get("evidence_map") or []
    for entry in emap:
        report["issues"].extend(validate_claim(entry, doc_texts, known_locators or None))

    report["ok"] = not report["issues"]
    _write_json(os.path.join(a.work, "validation_report.json"), report)
    return {"ok": report["ok"], "issues": report["issues"], "warnings": report["warnings"]}


def _render_report(res, path):
    """从 result.json 渲染可读版证据报告（不新增事实）。"""
    d = res.get("deliverable", {}) or {}
    emap = d.get("evidence_map") or res.get("evidence_map") or []
    lines = ["# 文献证据驱动写作：证据报告", "",
             "> 本文件由 result.json 渲染，仅为可读版；事实与来源以 result.json 为准。", ""]
    lines.append("- case_id：%s" % res.get("case_id", ""))
    lines.append("- status：%s" % res.get("status", ""))
    lines.append("")
    if d.get("summary"):
        lines += ["## 摘要", "", str(d["summary"]), ""]
    lines += ["## 论断—证据映射", ""]
    for e in emap:
        lines.append("### %s　%s" % (e.get("claim_id", "?"), e.get("claim", "")))
        lines.append("- 论断类型：%s；适用边界：%s" % (e.get("claim_kind") or "未标注", e.get("claim_scope") or "未标注"))
        for s in e.get("support") or []:
            lines.append("- 来源：`%s` @ `%s`" % (s.get("source_doc") or "?", s.get("locator") or "?"))
            lines.append("  - 原文：> %s" % (s.get("quote") or "（缺）"))
            lines.append("  - 支撑关系：%s；证据类型：%s；强度：%s（%s）" % (
                s.get("relation") or "?", s.get("evidence_type") or "?", s.get("strength") or "?",
                s.get("strength_rationale") or "未说明"))
            lines.append("  - verdict：%s" % (s.get("verdict") or "?"))
        if e.get("final_wording"):
            lines.append("- 成稿表述：%s" % e["final_wording"])
        if e.get("uncertainty"):
            lines.append("- 不确定性：%s" % e["uncertainty"])
        lines.append("")
    if res.get("uncertainty"):
        lines += ["## 不确定性", ""]
        lines += ["- %s" % x for x in res["uncertainty"]]
        lines.append("")
    if res.get("pending_human"):
        lines += ["## 待人工确认", ""]
        lines += ["- %s" % x for x in res["pending_human"]]
        lines.append("")
    if res.get("limitations"):
        lines += ["## 局限与缺口", ""]
        lines += ["- %s" % x for x in res["limitations"]]
        lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def run_finalize(a):
    v = run_validate(a)
    out = os.path.join(a.output, "result.json")
    res = _load_result(out) or {}
    if res.get("status") in (None, "", "in_progress"):
        res["status"] = a.status if a.status else ("complete" if v["ok"] else "partial")
    res["change_log"] = res.get("change_log") or []
    res["change_log"].append({"time": now_iso(), "action": "finalize",
                              "detail": "校验 %d 条问题，status=%s" % (len(v["issues"]), res["status"])})
    _write_json(out, res)
    _render_report(res, os.path.join(a.output, "evidence_report.md"))
    return {"ok": True, "status": res["status"], "issues": v["issues"]}


def build_parser():
    ap = argparse.ArgumentParser(description="evidence-driven-writing 主入口")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, help_ in (
        ("scaffold", "立即落盘 partial result.json 骨架"),
        ("extract", "抽取材料文本并建 locator 索引"),
        ("analyze", "生成论断切分模板与候选扫描"),
        ("validate", "校验 result.json 契约与逐字 quote"),
        ("finalize", "校验并推进 status、渲染报告"),
    ):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=None)
    p = sub.choices["finalize"]
    p.add_argument("--status", default=None, choices=ALLOWED_STATUS)
    return ap


_FUNC = {
    "scaffold": run_scaffold,
    "extract": run_extract,
    "analyze": run_analyze,
    "validate": run_validate,
    "finalize": run_finalize,
}


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    fn = _FUNC.get(a.cmd)
    if fn is None:
        ap.print_help()
        return 2
    try:
        r = fn(a)
        print(json.dumps(r, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001
        _log("command %s 失败：%s" % (a.cmd, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
