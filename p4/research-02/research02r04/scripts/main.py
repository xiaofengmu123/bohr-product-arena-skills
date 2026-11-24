#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 文献综述 Skill 的确定性脚手架（Phase 0）。

职责：
1) 扫描 /input 下的 case.json、task.md 与材料文件；
2) 解析 case.json 中的 output_contract，回填其全部字段（缺值 null/[]，不改名、不删键）；
3) **立即**写出一版合法 result.json（status=partial）作为安全骨架；
4) 对 PDF 调 pdftotext 抽取文本、计算 SHA-256 供去重；
5) 写 /app/work/inventory.json 与空模板，供 Agent 后续筛选/证据/草稿使用。

本文件只负责 Phase 0 骨架；筛选、证据抽取、主题归纳、草稿由 Agent 按 SKILL.md 执行。
空输入、坏 JSON、坏编码、缺软件均不崩溃且退出码 0；重复运行结果一致。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

import core_impl as ci


def find_case_file(input_dir):
    """先找 case.json；找不到再找任一含 output_contract 的 .json。"""
    p = os.path.join(input_dir, "case.json")
    if os.path.isfile(p):
        return p
    candidates = []
    for root, _dirs, files in os.walk(input_dir):
        for f in sorted(files):
            if f.lower().endswith(".json") and f.lower() != "result.json":
                candidates.append(os.path.join(root, f))
    for c in candidates:
        data, _errs = ci.read_json(c)
        if data is not None and ci.find_output_contract(data) is not None:
            return c
    return candidates[0] if candidates else None


def scan_materials(input_dir):
    """返回材料列表；每条含 path/rel/name/type/ext/size/sha256，超限跳过并记录。"""
    items = []
    for root, _dirs, files in os.walk(input_dir):
        for f in sorted(files):
            if f.lower() in ci.META_NAMES:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext not in ci.MATERIAL_EXTS:
                continue
            path = os.path.join(root, f)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            rel = os.path.relpath(path, input_dir)
            if size > ci.MAX_FILE_BYTES:
                items.append({
                    "path": path, "rel": rel, "name": f, "type": "skipped",
                    "ext": ext, "size": size, "sha256": None,
                    "reason": "超过大小上限 %d 字节" % ci.MAX_FILE_BYTES,
                })
                continue
            if ext in ci.PDF_EXTS:
                typ = "pdf"
            elif ext in ci.TEXT_EXTS:
                typ = "text"
            else:
                typ = "record"
            items.append({
                "path": path, "rel": rel, "name": f, "type": typ, "ext": ext,
                "size": size, "sha256": ci.sha256_file(path),
            })
    return items


def extract_pdfs(items, work_dir, degradations):
    """用 pdftotext 抽取 PDF 文本；失败记入 degradations，不伪造解析结果。"""
    pdfs = [i for i in items if i["type"] == "pdf"]
    report = {}
    if not pdfs:
        return report
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        degradations.append({
            "kind": "tool_missing", "tool": "pdftotext",
            "detail": "未找到 pdftotext，PDF 无法抽取；不伪造解析结果",
        })
        for i in pdfs:
            report[i["path"]] = {"status": "failed", "reason": "pdftotext_missing"}
        return report
    out_dir = os.path.join(work_dir, "extracted")
    os.makedirs(out_dir, exist_ok=True)
    for it in pdfs:
        base = os.path.splitext(it["name"])[0]
        out = os.path.join(out_dir, base + ".txt")
        try:
            proc = subprocess.run(
                [pdftotext, "-layout", it["path"], out],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if proc.returncode == 0 and os.path.isfile(out):
                report[it["path"]] = {"status": "ok", "out": out,
                                       "bytes": os.path.getsize(out)}
            else:
                report[it["path"]] = {"status": "failed",
                                       "reason": "returncode=%s" % proc.returncode}
        except (OSError, subprocess.TimeoutExpired) as e:
            report[it["path"]] = {"status": "failed", "reason": str(e)}
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="文献综述 Skill Phase 0 脚手架")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    for d in (args.output, args.work):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            ci.log("无法创建目录 %s: %s" % (d, e))

    errors, degradations, change_log = [], [], []
    ci.append_change_log(change_log, "phase0", "start", "读取输入并写结果骨架")

    is_dir = os.path.isdir(args.input)
    case_path = find_case_file(args.input) if is_dir else None
    case, question, fields = None, None, []
    status, case_id = "partial", "unknown"

    if case_path is None:
        status = "insufficient_evidence"
        case_id = "unknown"
        errors.append("输入目录无 case.json 且无含 output_contract 的 JSON")
        ci.append_change_log(change_log, "phase0", "no_case",
                             "未发现 case.json，按 insufficient_evidence 处理")
    else:
        case, case_errs = ci.read_json(case_path)
        if case is None:
            status = "input_error"
            errors.extend(case_errs or ["case.json 无法解析"])
            case_id = ci.extract_case_id(None, case_path)
            ci.append_change_log(change_log, "phase0", "bad_case", "; ".join(errors))
        else:
            case_id = ci.extract_case_id(case, case_path)
            question = ci.extract_question(case)
            fields, _spec = ci.parse_contract(case)
            if not fields:
                fields = list(ci.DEFAULT_CONTRACT_FIELDS.items())
                degradations.append({
                    "kind": "contract_missing",
                    "detail": "未发现 output_contract，使用兜底字段并如实标注",
                })
            ci.append_change_log(change_log, "phase0", "case_ok",
                                 "case_id=%s, contract 字段 %d 个" % (case_id, len(fields)))

    # 第一版骨架：立即落盘，任何后续异常都不留空产物。
    result = ci.build_result(case_id, fields, question, status=status,
                             errors=errors, degradations=degradations,
                             change_log=change_log)
    result_path = os.path.join(args.output, "result.json")
    ci.write_json(result_path, result)
    ci.log("已写 result.json 骨架: status=%s, case_id=%s" % (status, case_id))

    # 扫描与去重（按 SHA-256；无 hash 的按 (name,size)）。
    items = scan_materials(args.input) if is_dir else []
    seen, dedup = {}, []
    for it in items:
        key = it.get("sha256") or (it["name"], it["size"])
        if key in seen:
            it["duplicate_of"] = seen[key]["rel"]
            it["decision"] = "duplicate"
        else:
            seen[key] = it
            it["decision"] = "candidate"
        dedup.append(it)

    ext_report = extract_pdfs(items, args.work, degradations)
    for it in items:
        if it["type"] == "skipped":
            degradations.append({
                "kind": "size_skipped", "source": it["rel"],
                "detail": it.get("reason", "超过大小上限"),
            })
        r = ext_report.get(it["path"])
        if r and r["status"] == "failed":
            degradations.append({
                "kind": "extraction_failed", "source": it["rel"],
                "detail": r.get("reason", "unknown"),
            })

    # 中间产物：inventory + 空模板（不预写正式结果）。
    inventory = {
        "input_dir": args.input,
        "case_file": case_path,
        "materials": dedup,
        "extraction": {os.path.relpath(k, args.input): v
                       for k, v in ext_report.items()},
    }
    ci.write_json(os.path.join(args.work, "inventory.json"), inventory)
    ci.write_json(os.path.join(args.work, "templates", "screening_log.json"), {
        "_note": "筛选标准必须先于纳排决定填写；本文件为模板，不含正式结果",
        "screening_criteria": {
            "research_question": question,
            "population_object": None,
            "inclusion": [],
            "exclusion": [],
            "dedup_rules": ["按 SHA-256 去重", "同题名不同文件人工核对后合并"],
        },
        "records": [],
    })
    ci.write_text(os.path.join(args.work, "templates", "evidence_table.jsonl"), "")
    ci.write_text(os.path.join(args.work, "templates", "evidence_fields.json"),
                  json.dumps({
                      "required": ci.EVIDENCE_REQUIRED_FIELDS,
                      "strength": sorted(ci.ALLOWED_STRENGTH),
                      "strength_verbs": ci.STRENGTH_VERBS,
                  }, ensure_ascii=False, indent=2))

    # 回写最新 errors/degradations 与材料数。
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = {"timestamp": ci.now_ts(), "phase": "phase0",
                          "materials": len(items)}
    ci.write_json(result_path, result)
    ci.log("Phase 0 完成: 材料 %d 项, 抽取报告已写" % len(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
