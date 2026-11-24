#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-figure-robust 主入口：输入发现 -> 结果骨架 -> 交付自检。

只依赖 Python 标准库。对空输入目录、坏 JSON、坏编码、缺参数、超大文件、
写盘失败做优雅降级：任何情况下都尽量产出合法 JSON，且进程退出码恒为 0
（错误信息写入 errors/degradations，不抛异常、不崩溃）。

子命令：
    bootstrap   读 /input 并落盘 result.json 骨架（默认命令；S0，前 3 分钟执行）
    check       校验 result.json 与 /app/output 交付物（S5）

用法（相对 Skill 根目录调用）：
    python scripts/main.py bootstrap --input /input --out /app/output --work /app/work
    python scripts/main.py check --out /app/output --work /app/work
缺省路径 /input、/app/output、/app/work；可用 INPUT_DIR/OUTPUT_DIR/WORK_DIR
与 MAX_FILE_BYTES 环境变量覆盖。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 单文本文件 2MB 上限，超限跳过并记录

ALLOWED_STATUS = {
    "complete", "partial", "failed", "insufficient_evidence", "input_error",
}

# 当 case.json 未提供 output_contract 时的内置回退契约，与 SKILL.md 交付要求一致。
DEFAULT_CONTRACT = {
    "editable_source": "可编辑源文件（分层 SVG 或 PPTX/EPS，文字保持文本节点而非转曲）",
    "rendered_png": "真实渲染的 PNG 预览（>=300 dpi、嵌入字体、明确背景与色彩空间）",
    "rendered_pdf": "真实渲染的 PDF 预览（矢量文字可选中）",
    "data_mapping": "图元到源数据字段/行号的双向映射",
    "annotation_checks": "图例、轴标签、误差线、显著性、样本量与图注的校验记录",
}

_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")

_MAGIC_PREFIXES = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".pdf": b"%PDF-",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": b"GIF8",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def read_bytes_safe(path: str, limit: int, errors: list) -> bytes | None:
    """读文件字节；超限/不可读返回 None 并写 errors，绝不抛异常。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        errors.append({"path": path, "kind": "unreadable", "detail": str(e)})
        return None
    if size > limit:
        errors.append({
            "path": path, "kind": "oversized",
            "detail": "%d bytes > limit %d" % (size, limit),
        })
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        errors.append({"path": path, "kind": "unreadable", "detail": str(e)})
        return None


def decode_safe(data: bytes) -> tuple[str, str, list]:
    """多编码容错解码；全部失败则 latin-1 兜底（永不失败）并记录降级。"""
    last = ""
    for enc in _ENCODINGS:
        try:
            return data.decode(enc), enc, []
        except (UnicodeDecodeError, LookupError) as e:
            last = str(e)
    return data.decode("latin-1", errors="replace"), "latin-1", [
        {"kind": "bad_encoding", "detail": "utf-8/gb18030 均失败，latin-1 兜底: %s" % last}
    ]


def read_text_safe(path: str, limit: int, errors: list) -> str | None:
    data = read_bytes_safe(path, limit, errors)
    if data is None:
        return None
    text, _enc, enc_errs = decode_safe(data)
    errors.extend(enc_errs)
    return text


def read_json_safe(path: str, limit: int, errors: list):
    """读 JSON；坏 JSON 返回 None 并写 errors，绝不抛异常。"""
    data = read_bytes_safe(path, limit, errors)
    if data is None:
        return None
    text, _enc, enc_errs = decode_safe(data)
    errors.extend(enc_errs)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        errors.append({"path": path, "kind": "bad_json", "detail": str(e)})
        return None


def write_json_safe(path: str, obj, errors: list | None) -> bool:
    """确定性写 JSON（sort_keys，UTF-8，LF 结尾）；失败记录 errors，绝不抛异常。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        return True
    except (OSError, TypeError, ValueError) as e:
        if errors is not None:
            errors.append({"path": path, "kind": "write_failed", "detail": str(e)})
        return False


def _classify_kind(ext: str) -> str:
    ext = (ext or "").lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".csv", ".tsv", ".xlsx", ".xls"):
        return "tabular"
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp"):
        return "image"
    if ext == ".svg":
        return "svg"
    if ext in (".md", ".txt", ".json", ".yaml", ".yml"):
        return "text"
    return "other"


def discover_input(input_dir: str, errors: list) -> list:
    """列出输入目录全部文件（按路径排序，确定性）；不可读目录记录 errors。"""
    out = []
    try:
        if not os.path.isdir(input_dir):
            return out
        for root, _dirs, files in os.walk(input_dir):
            for name in sorted(files):
                p = os.path.join(root, name)
                try:
                    size = os.path.getsize(p)
                except OSError as e:
                    errors.append({"path": p, "kind": "unreadable", "detail": str(e)})
                    continue
                out.append({
                    "path": p, "name": name, "size": size,
                    "ext": os.path.splitext(name)[1].lower(),
                    "kind": _classify_kind(os.path.splitext(name)[1]),
                })
    except OSError as e:
        errors.append({"path": input_dir, "kind": "unreadable_dir", "detail": str(e)})
    return out


def extract_case_id(case) -> str | None:
    if not isinstance(case, dict):
        return None
    for k in ("case_id", "id", "caseId", "challenge_id", "challengeId"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return None


def extract_output_contract(case):
    """提取 output_contract 原样键；缺省返回 None。未知键保留、不重命名、不删除。"""
    if not isinstance(case, dict):
        return None
    for k in ("output_contract", "contract", "outputContract"):
        v = case.get(k)
        if isinstance(v, dict):
            return v
    return None


def build_skeleton(case, case_path, task_path, files, errors) -> dict:
    case_id = extract_case_id(case) if case is not None else None
    contract = extract_output_contract(case) if case is not None else None

    if contract is None:
        contract = dict(DEFAULT_CONTRACT)
        errors.append({
            "kind": "no_output_contract",
            "detail": "case.json 缺失、无法解析或未提供 output_contract，回退到 Skill 内置默认契约",
        })

    # 逐字段占位：键与输入契约完全一致，值先填 null，执行阶段增量填充。
    filled = {k: None for k in contract.keys()}

    sources = []
    if case is not None and case_path:
        sources.append({"path": case_path, "kind": "case_json", "role": "输入事实来源与契约"})
    if task_path:
        sources.append({"path": task_path, "kind": "task_md", "role": "任务说明"})
    for f in files:
        if f["name"] in ("case.json", "task.md"):
            continue
        sources.append({
            "path": f["path"], "kind": f["kind"], "name": f["name"],
            "size": f["size"], "role": "材料",
        })

    return {
        "case_id": case_id,
        "status": "partial" if case_id else "insufficient_evidence",
        "output_contract": filled,
        "contract_spec": contract,
        "sources": sources,
        "uncertainty": [],
        "pending_human": [],
        "errors": list(errors),
        "degradations": [],
        "evidence_map": [],
        "annotation_checks": {},
        "change_log": [
            {
                "step": "S0_bootstrap",
                "time_utc": utcnow(),
                "status": "done",
                "note": "结果骨架已落盘；output_contract 全部字段已按输入契约占位为 null，待后续步骤增量填充",
            }
        ],
        "runtime": {"schema": "sci-figure-robust/1.0.0", "stdlib_only": True},
    }


def run_bootstrap(args) -> dict:
    errors = []
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    case_path = os.path.join(args.input, "case.json")
    case = read_json_safe(case_path, args.max_bytes, errors)
    task_path = os.path.join(args.input, "task.md")
    read_text_safe(task_path, args.max_bytes, errors)  # 仅确认可读并记录异常，正文由 Agent 自行读取
    files = discover_input(args.input, errors)

    result = build_skeleton(case, case_path if case is not None else None,
                            task_path if os.path.exists(task_path) else None, files, errors)

    write_json_safe(os.path.join(args.out, "result.json"), result, errors)
    write_json_safe(os.path.join(args.work, "contract.json"), {
        "case_id": result["case_id"],
        "output_contract": result["contract_spec"],
        "input_files": files,
    }, errors)
    write_json_safe(os.path.join(args.work, "bootstrap_report.json"), {
        "ok": True, "time_utc": utcnow(), "case_id": result["case_id"],
        "status": result["status"], "errors": errors,
    }, errors)
    return result


def _check_file(path: str, ext: str, report: dict) -> None:
    if not os.path.exists(path):
        report["ok"] = False
        report["failures"].append("产物缺失: %s" % path)
        return
    if ext == ".svg":
        text = read_text_safe(path, DEFAULT_MAX_BYTES, report["errors"])
        low = (text or "").lower()
        if "<svg" in low and "<text" in low:
            report["checks"].append({"name": "SVG 可编辑", "path": path, "pass": True})
        else:
            report["ok"] = False
            report["failures"].append("SVG 缺失 <text> 文本节点（可能已转曲）: %s" % path)
        return
    magic = _MAGIC_PREFIXES.get(ext)
    if magic is None:
        return  # 未知格式不强制魔数
    with open(path, "rb") as f:
        head = f.read(len(magic))
    if head == magic:
        report["checks"].append({"name": "魔数校验", "path": path, "pass": True})
    else:
        report["ok"] = False
        report["failures"].append("格式魔数不匹配: %s" % path)


def check_result(args) -> dict:
    report = {
        "ok": True, "result_path": args.result or os.path.join(args.out, "result.json"),
        "checks": [], "warnings": [], "failures": [], "errors": [],
        "time_utc": utcnow(),
    }
    result = read_json_safe(report["result_path"], args.max_bytes, report["errors"])
    if result is None:
        report["ok"] = False
        report["failures"].append("result.json 缺失或无法解析")
        return report
    if not isinstance(result, dict):
        report["ok"] = False
        report["failures"].append("result.json 顶层不是对象")
        return report

    def check(cond, name, detail=""):
        report["checks"].append({"name": name, "pass": bool(cond), "detail": detail})
        if not cond:
            report["ok"] = False
            report["failures"].append(name + ((": " + detail) if detail else ""))

    check(bool(result.get("case_id")), "case_id 非空", str(result.get("case_id")))
    check(result.get("status") in ALLOWED_STATUS, "status 合法", str(result.get("status")))
    check(isinstance(result.get("output_contract"), dict), "output_contract 为对象")
    check(isinstance(result.get("sources"), list), "sources 为列表")
    check(isinstance(result.get("uncertainty"), list), "uncertainty 为列表")
    check(isinstance(result.get("change_log"), list) and len(result["change_log"]) > 0,
          "change_log 非空列表")

    contract = result.get("output_contract")
    if isinstance(contract, dict):
        for key, val in sorted(contract.items()):
            if val is None:
                report["warnings"].append("契约字段未填充（需在 uncertainty/change_log 说明）: %s" % key)
            elif isinstance(val, str):
                p = val if os.path.isabs(val) else os.path.join(args.out, val)
                _check_file(p, os.path.splitext(p)[1].lower(), report)
            elif isinstance(val, (dict, list)):
                if val:
                    report["checks"].append({"name": "结构化契约字段", "field": key, "pass": True})
                else:
                    report["warnings"].append("结构化契约字段为空: %s" % key)

    # SVG 源文件若出现在主产物字段外也做一次可编辑性检查（sources 内 *.svg 不重复）
    for src in result.get("sources", []):
        if isinstance(src, dict) and str(src.get("path", "")).lower().endswith(".svg"):
            p = src["path"]
            if os.path.exists(p):
                _check_file(p, ".svg", report)
    return report


def resolve_dirs(args) -> None:
    args.input = args.input or os.environ.get("INPUT_DIR") or DEFAULT_INPUT
    args.out = args.out or os.environ.get("OUTPUT_DIR") or DEFAULT_OUTPUT
    args.work = args.work or os.environ.get("WORK_DIR") or DEFAULT_WORK
    args.max_bytes = args.max_bytes or _env_int("MAX_FILE_BYTES", DEFAULT_MAX_BYTES)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", default=None, help="输入目录（默认 /input，可 INPUT_DIR）")
    p.add_argument("--out", "--output", dest="out", default=None,
                   help="输出目录（默认 /app/output，可 OUTPUT_DIR）")
    p.add_argument("--work", default=None, help="工作目录（默认 /app/work，可 WORK_DIR）")
    p.add_argument("--max-bytes", type=int, default=None,
                   help="单文本文件大小上限（默认 2MB，可 MAX_FILE_BYTES）")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="sci-figure-robust：结果骨架与交付自检")
    sub = ap.add_subparsers(dest="command")
    _add_common(sub.add_parser("bootstrap", help="读输入并写 result.json 骨架（默认）"))
    c = sub.add_parser("check", help="校验 result.json 与交付物")
    _add_common(c)
    c.add_argument("--result", default=None, help="result.json 路径（默认 <out>/result.json）")
    ap.set_defaults(command="bootstrap")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    resolve_dirs(args)
    try:
        if args.command == "check":
            report = check_result(args)
            write_json_safe(os.path.join(args.work, "check_report.json"), report, None)
            print(json.dumps(report, ensure_ascii=False))
            return 0
        result = run_bootstrap(args)
        print(json.dumps({
            "ok": True, "case_id": result["case_id"], "status": result["status"],
            "result": os.path.join(args.out, "result.json"),
            "contract_fields": sorted(result["output_contract"].keys()),
        }, ensure_ascii=False))
        return 0
    except Exception as e:  # 最终安全网：进程绝不因内部错误崩溃
        try:
            print(json.dumps({
                "ok": False, "fatal": str(e),
                "trace": traceback.format_exc(limit=3),
            }, ensure_ascii=False))
        except Exception:
            print('{"ok": false, "fatal": "unknown"}')
        return 0


if __name__ == "__main__":
    sys.exit(main())
