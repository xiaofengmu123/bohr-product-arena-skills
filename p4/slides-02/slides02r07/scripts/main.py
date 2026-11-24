#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术汇报演示稿构建 · 骨架脚本（鲁棒变体）

在任意输入条件下生成一版合法、可复核的 /app/output/result.json 骨架。
只依赖 Python 标准库。核心目标：绝不因坏输入而崩溃。

- 空输入目录          -> 退出码 0，status=insufficient_evidence
- case.json 缺失      -> status=insufficient_evidence，记录缺口
- case.json 坏 JSON   -> status=input_error，记录 errors，仍产出合法结果
- 坏编码              -> 依次尝试 utf-8-sig / utf-8 / gb18030 / latin-1
- 缺参数              -> --input/--output/--work 缺省 /input、/app/output、/app/work
- 超大文件            -> 按类型跳过并写入 degradations，不读入内存
- 写盘失败            -> 回退到 work 目录，再失败才以清晰消息退出（不抛堆栈）

调用：
    python3 scripts/main.py --input /input --output /app/output --work /app/work
"""

import argparse
import datetime
import json
import os
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

TEXT_MAX_BYTES = 5 * 1024 * 1024
BINARY_MAX_BYTES = 50 * 1024 * 1024
TOTAL_SCAN_MAX_BYTES = 500 * 1024 * 1024

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

RESERVED_FIELDS = (
    "case_id", "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime",
)

DEFAULT_CONTRACT_FIELDS = (
    "presentation_file", "preview_files", "speaker_notes",
    "page_plan", "evidence_map", "version_records",
)

TEXT_EXTS = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".tex",
    ".bib", ".xml", ".html", ".htm", ".py", ".log", ".cfg", ".ini",
    ".rtf", ".rst", ".toml",
}
BINARY_EXTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pptx",
    ".docx", ".xlsx", ".tif", ".tiff", ".bmp", ".eps", ".ps", ".pptm",
    ".potx", ".doc", ".ppt",
}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in BINARY_EXTS:
        return "binary"
    return "binary"


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read(), None
    except OSError as e:
        return None, "read_error:%s" % e


def decode_text(data):
    if data is None:
        return None, "no_data"
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None, "undecodable"


def parse_json_bytes(data):
    text, _ = decode_text(data)
    if text is None:
        return None, "undecodable"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "bad_json:%s" % e


def read_text_safe(path):
    data, err = read_bytes(path)
    if err:
        return None, err
    if data and len(data) > TEXT_MAX_BYTES:
        return None, "oversized_text"
    text, _ = decode_text(data)
    if text is None:
        return None, "undecodable"
    return text, None


def find_file(input_dir, names):
    for n in names:
        p = os.path.join(input_dir, n)
        if os.path.isfile(p):
            return p
    if os.path.isdir(input_dir):
        for root, dirs, files in os.walk(input_dir):
            for n in sorted(files):
                if n in names:
                    return os.path.join(root, n)
    return None


def discover_inputs(input_dir):
    manifest = []
    errors = []
    if not os.path.isdir(input_dir):
        errors.append("input_dir_missing:%s" % input_dir)
        return manifest, errors
    total = 0
    scan_stopped = False
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError as e:
                errors.append("stat_error:%s:%s" % (name, e))
                continue
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            kind = classify(name)
            cap = TEXT_MAX_BYTES if kind == "text" else BINARY_MAX_BYTES
            entry = {
                "relpath": rel, "abs_path": full, "size_bytes": size,
                "kind": kind, "skipped": False, "reason": None,
            }
            if scan_stopped:
                entry["skipped"] = True
                entry["reason"] = "scan_budget_exceeded"
            elif size > cap:
                entry["skipped"] = True
                entry["reason"] = "oversized:%d>%d" % (size, cap)
            total += size
            if total > TOTAL_SCAN_MAX_BYTES and not scan_stopped:
                scan_stopped = True
                errors.append("total_scan_budget_exceeded")
            manifest.append(entry)
    return manifest, errors


def extract_contract_fields(case_obj):
    if isinstance(case_obj, dict):
        oc = case_obj.get("output_contract")
        if oc is None:
            oc = case_obj.get("outputContract")
        if isinstance(oc, dict):
            fields = {k: None for k in oc if isinstance(k, str)}
            if fields:
                return fields, "from_output_contract"
        elif isinstance(oc, list):
            fields = {}
            for item in oc:
                if isinstance(item, str):
                    fields[item] = None
                elif isinstance(item, dict):
                    nm = item.get("name") or item.get("field") or item.get("key")
                    if isinstance(nm, str):
                        fields[nm] = None
            if fields:
                return fields, "from_output_contract"
    return {f: None for f in DEFAULT_CONTRACT_FIELDS}, "inferred_default"


def extract_case_id(case_obj):
    if isinstance(case_obj, dict):
        for k in ("case_id", "caseId", "id"):
            v = case_obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def build_result(case_id, contract_fields, case_status, task_present, errors, degradations, tstamp):
    result = {"case_id": case_id}
    result.update(contract_fields)
    if case_status == "missing":
        status = "insufficient_evidence"
    elif case_status == "bad":
        status = "input_error"
    else:
        status = "partial"
    result["status"] = status
    result["sources"] = []
    uncertainty = {"unresolved": [], "missing_artifacts": [], "notes": ""}
    if case_status == "missing":
        uncertainty["missing_artifacts"].append("case.json")
    if not task_present:
        uncertainty["missing_artifacts"].append("task.md")
    if case_status == "ok" and case_id == "":
        uncertainty["unresolved"].append("case_id 缺失，需人工确认")
    result["uncertainty"] = uncertainty
    result["pending_human"] = []
    if case_status == "ok" and case_id == "":
        result["pending_human"].append("确认 case_id")
    result["change_log"] = [{
        "t": tstamp, "step": "scaffold", "action": "write_partial_skeleton",
        "status": status,
        "reason": "鲁棒骨架：保留 output_contract 全部字段与保留字段；后续只做增量改进",
    }]
    result["errors"] = list(errors)
    result["degradations"] = list(degradations)
    result["runtime"] = {"started_at": tstamp, "updated_at": tstamp}
    return result


def write_json(path, obj):
    try:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
        return None
    except Exception as e:
        return "write_error:%s" % e


def run_scaffold(input_dir, output_dir, work_dir, case_file=None, task_file=None):
    degradations = []
    case_path = case_file or find_file(input_dir, ["case.json"])
    task_path = task_file or find_file(input_dir, ["task.md"])

    case_obj = None
    case_status = "missing"
    errors = []
    if case_path is None:
        case_status = "missing"
    else:
        data, err = read_bytes(case_path)
        if err:
            case_status = "bad"
            errors.append("case_read_error:%s" % err)
        elif data and len(data) > TEXT_MAX_BYTES:
            case_status = "bad"
            errors.append("case_oversized")
        else:
            case_obj, perr = parse_json_bytes(data)
            if perr:
                case_status = "bad"
                errors.append("case_parse:%s" % perr)
            else:
                case_status = "ok"

    task_present = task_path is not None and os.path.isfile(task_path)
    task_excerpt = ""
    if task_present:
        task_text, terr = read_text_safe(task_path)
        if terr:
            degradations.append("task_read:%s" % terr)
        else:
            task_excerpt = task_text[:500]

    case_id = extract_case_id(case_obj) if case_obj is not None else ""
    contract_fields, contract_source = extract_contract_fields(case_obj)
    if contract_source == "inferred_default":
        degradations.append("output_contract 缺失，按已知契约字段推断，需在 uncertainty 标注")

    manifest, scan_errors = discover_inputs(input_dir)
    errors.extend(scan_errors)

    tstamp = now_iso()
    result = build_result(case_id, contract_fields, case_status, task_present, errors, degradations, tstamp)

    summary = {
        "case_id": case_id,
        "case_json": {"present": case_status == "ok", "path": case_path or None, "parse_status": case_status},
        "task_md": {"present": task_present, "path": task_path or None, "excerpt": task_excerpt},
        "contract_source": contract_source,
        "contract_fields": sorted(contract_fields.keys()),
        "input_dir": input_dir,
        "files_total": len(manifest),
        "files_not_skipped": sum(1 for m in manifest if not m["skipped"]),
    }

    result_path = os.path.join(output_dir, "result.json")
    summary_path = os.path.join(work_dir, "case_summary.json")
    manifest_path = os.path.join(work_dir, "input_manifest.json")

    write_err = write_json(result_path, result)
    if write_err:
        fallback = os.path.join(work_dir, "result.json")
        write_err2 = write_json(fallback, result)
        if write_err2:
            combined = errors + [write_err, write_err2]
            print("FATAL: 无法写 result.json: %s; %s" % (write_err, write_err2), file=sys.stderr)
            return {"result_path": None, "summary_path": summary_path, "manifest_path": manifest_path,
                    "result": result, "summary": summary, "manifest": manifest,
                    "errors": combined, "exit_code": 1}
        degradations.append("result_written_to_work_fallback:%s" % write_err)
        result_path = fallback
        result["degradations"] = list(degradations)
    write_json(summary_path, summary)
    write_json(manifest_path, manifest)

    return {"result_path": result_path, "summary_path": summary_path, "manifest_path": manifest_path,
            "result": result, "summary": summary, "manifest": manifest,
            "errors": errors, "exit_code": 0}


def main(argv=None):
    p = argparse.ArgumentParser(description="鲁棒骨架生成（stdlib only）")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)
    p.add_argument("--case-file", default=None)
    p.add_argument("--task-file", default=None)
    args = p.parse_args(argv)
    out = run_scaffold(args.input, args.output, args.work, args.case_file, args.task_file)
    if out["exit_code"] == 0:
        print("scaffold ok: status=%s case_id=%r result=%s" %
              (out["result"].get("status"), out["result"].get("case_id"), out["result_path"]))
    else:
        print("scaffold degraded: %s" % "; ".join(out["errors"]), file=sys.stderr)
    return out["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
