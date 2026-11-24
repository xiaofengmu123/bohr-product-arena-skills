#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-robust-adapt 主流程脚本（仅 Python 标准库）。

在 DeepSeek Harness 下由 Agent 调用，负责在**最早时刻**落盘一版合法的
result.json 骨架，并对输入做鲁棒发现与解析。之后各阶段通过 merge 子命令
做增量合并，避免中途异常留下不完整产物。

鲁棒性契约（本变体的侧重点）：
  空输入目录、坏 JSON、坏编码、缺参数、超大文件、写盘失败，全部优雅降级，
  进程不崩溃；重复运行幂等（init 合并而非盲目覆盖）。

退出码（供 SKILL.md 与调用方判断）：
  0  正常结束。即使 status 为 insufficient_evidence / input_error，
     也不抛异常——这是健壮性契约的一部分。
  2  命令行参数错误（argparse 自动产生）。
  3  输出目录不可写，且最小降级产物也无法落盘。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件上限 2MB，超限跳过并记录

# 题目 output_contract 的三大件；缺值一律 null/[]，不删除未知键。
CONTRACT_KEYS = ("presentation", "evidence_map", "render_record")


def now_iso() -> str:
    """本地时区 ISO-8601 时间戳，用于 change_log。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_text_robust(path: str, max_bytes: int = MAX_FILE_BYTES):
    """按 utf-8-sig → utf-8 → latin-1 容错读取；超限/失败返回 (None, error)。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "读取失败: {}: {}".format(path, exc)
    if len(raw) > max_bytes:
        return None, "文件超限({}B>{}B): {}".format(len(raw), max_bytes, path)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return None, "无法解码: {}".format(path)


def load_json_robust(path: str):
    """解析 JSON。返回 (obj, errors, degradations)；解析失败返回 (None, [err], [])。"""
    text, err = read_text_robust(path)
    if err is not None:
        return None, [err], []
    try:
        return json.loads(text), [], []
    except (ValueError, TypeError) as exc:
        return None, ["JSON 解析失败: {}: {}".format(path, exc)], []


def classify_file(name: str) -> str:
    low = name.lower()
    if low == "case.json":
        return "case_json"
    if low.startswith("task") and low.endswith(".md"):
        return "task_md"
    if low.endswith((".pdf", ".png", ".jpg", ".jpeg", ".svg", ".pptx",
                     ".potx", ".docx", ".csv", ".txt", ".md", ".json",
                     ".emf", ".wmf", ".xlsx")):
        return "material"
    return "other"


def scan_input(input_dir: str, errors, degradations):
    """遍历输入目录，产出清单；超大/不可读文件跳过并记录。"""
    inv = {
        "input_dir": input_dir,
        "files": [],
        "counts": {"case_json": 0, "task_md": 0, "material": 0,
                   "other": 0, "skipped": 0},
    }
    if not os.path.isdir(input_dir):
        errors.append("输入目录不存在: {}".format(input_dir))
        return inv
    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir)
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                degradations.append({"path": rel, "detail": "无法取大小: {}".format(exc)})
                inv["counts"]["skipped"] += 1
                continue
            kind = classify_file(name)
            if size > MAX_FILE_BYTES:
                degradations.append({"path": rel, "bytes": size,
                                     "detail": "oversized_skipped"})
                inv["counts"]["skipped"] += 1
                inv["files"].append({"path": rel, "kind": kind, "bytes": size,
                                     "status": "skipped"})
                continue
            inv["counts"][kind] = inv["counts"].get(kind, 0) + 1
            inv["files"].append({"path": rel, "kind": kind, "bytes": size,
                                 "status": "ok"})
    return inv


def find_case_json(inv, input_dir: str):
    for f in inv["files"]:
        if f["kind"] == "case_json" and f["status"] == "ok":
            return os.path.join(input_dir, f["path"])
    return None


def guess_case_id(inv, input_dir: str):
    """从 task.md 尽力提取 challenge_id / case_id，失败返回 None。"""
    for f in inv["files"]:
        if f["kind"] == "task_md" and f["status"] == "ok":
            text, _ = read_text_robust(os.path.join(input_dir, f["path"]))
            if text:
                m = re.search(r"challenge_id[:\s`]*([A-Za-z0-9_-]+)", text)
                if m:
                    return m.group(1)
                m = re.search(r"case_id[:\s`]*([A-Za-z0-9_-]+)", text)
                if m:
                    return m.group(1)
    return None


def _normalize_default(v):
    if v is None:
        return None
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {k: _normalize_default(x) for k, x in v.items()}
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return 0
    return None


def make_skeleton(case_id: str, contract, started: str):
    """构建完整 result.json 骨架：所有字段先给 null/[]，后续只填充。"""
    result = {
        "case_id": case_id,
        "status": "in_progress",
        "started_at": started,
        "finished_at": None,
        "presentation": {
            "pptx_path": None, "pdf_path": None, "png_paths": [],
            "slide_count": None,
            "page_plan": [],  # 每页 {index,title,claim,layout,notes,refs}
        },
        "evidence_map": {
            "pages": [],   # 每页 {page,claim,refs[],evidence_status}
            "unresolved": [],
        },
        "render_record": {
            "source_files": [], "template_file": None, "layouts_used": [],
            "fonts": [], "exports": [], "integrity": {}, "media": [],
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"requests": None, "seconds": None, "rework_pages": 0},
    }
    # 若 case.json 自带 output_contract：原样补齐全部键，缺值给空，不改名不删键
    if isinstance(contract, dict):
        for key, val in contract.items():
            if key not in result:
                result[key] = _normalize_default(val)
    return result


def ensure_keys(dst, template):
    """只补缺失的结构键，不动已填值（幂等合并的辅助）。"""
    for key, val in template.items():
        if key not in dst:
            dst[key] = val
        elif isinstance(val, dict) and isinstance(dst.get(key), dict):
            ensure_keys(dst[key], val)


def deep_merge(dst, src):
    """递归合并 src 进 dst，src 非 dict 时覆盖。返回 dst。"""
    for key, val in src.items():
        if key in dst and isinstance(dst[key], dict) and isinstance(val, dict):
            deep_merge(dst[key], val)
        else:
            dst[key] = val
    return dst


def write_json_atomic(path: str, obj):
    """原子写 JSON：先写 .tmp 再 replace；失败返回 (False, err)。"""
    directory = os.path.dirname(path) or "."
    tmp = None
    try:
        os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True, None
    except OSError as exc:
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False, str(exc)


def read_result(path):
    ok = os.path.isfile(path)
    if not ok:
        return False, None, "结果不存在: {}".format(path)
    obj, errors, _ = load_json_robust(path)
    if obj is None or not isinstance(obj, dict):
        return False, None, (errors[0] if errors else "结果不可读")
    return True, obj, None


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    started = now_iso()
    errors = []
    degradations = []

    inv = scan_input(args.input, errors, degradations)
    case_path = find_case_json(inv, args.input)
    case_obj = None
    contract = None
    case_id = "unknown"

    if case_path:
        case_obj, json_errors, _ = load_json_robust(case_path)
        errors.extend(json_errors)
        if isinstance(case_obj, dict):
            case_id = str(case_obj.get("case_id") or case_obj.get("id")
                          or os.path.basename(case_path))
            contract = case_obj.get("output_contract")
            if contract is not None and not isinstance(contract, dict):
                degradations.append({"detail": "output_contract 非对象，忽略"})
                contract = None
        else:
            errors.append("case.json 无法解析，case_id 回退为文件名")
            case_id = os.path.basename(case_path)
    else:
        errors.append("未发现 case.json")
        case_id = guess_case_id(inv, args.input) or "unknown"

    # 诚实状态：无任何有效材料 → insufficient_evidence；case.json 坏 → input_error
    status = "in_progress"
    material_count = inv["counts"].get("material", 0)
    has_any = inv["counts"].get("case_json", 0) + inv["counts"].get("task_md", 0) + material_count
    if has_any == 0:
        status = "insufficient_evidence"
    if case_path and case_obj is None:
        status = "input_error"

    fresh = make_skeleton(case_id, contract, started)
    fresh["status"] = status
    fresh["errors"] = errors
    fresh["degradations"] = degradations

    result_path = os.path.join(args.output, "result.json")
    ok_existing, existing, _ = read_result(result_path)
    if ok_existing:
        result = existing
        result["case_id"] = case_id
        result["started_at"] = started
        result.setdefault("errors", [])
        result.setdefault("degradations", [])
        for e in errors:
            if e not in result["errors"]:
                result["errors"].append(e)
        for d in degradations:
            if d not in result["degradations"]:
                result["degradations"].append(d)
        if result.get("status") not in ("ok", "partial", "failed"):
            result["status"] = fresh["status"]
        ensure_keys(result, fresh)
        result.setdefault("change_log", []).append({
            "at": now_iso(), "event": "init",
            "detail": "幂等重跑：保留已填字段，仅补齐结构与刷新元数据",
            "status": result["status"],
        })
    else:
        result = fresh
        result["change_log"].append({
            "at": now_iso(), "event": "init",
            "detail": "落盘初始骨架", "status": result["status"],
        })

    wok, werr = write_json_atomic(result_path, result)
    if not wok:
        fallback = os.path.join(args.work, "result.json")
        minimal = {"case_id": case_id, "status": "failed",
                   "errors": ["主输出不可写: {}".format(werr)],
                   "change_log": [{"at": now_iso(), "event": "write_failed",
                                   "detail": werr}]}
        wok2, werr2 = write_json_atomic(fallback, minimal)
        if not wok2:
            print("[fatal] 无法落盘结果: {}; 降级也失败: {}".format(werr, werr2),
                  file=sys.stderr)
            return 3
        print("[degraded] 主输出不可写，已降级到 {}".format(fallback), file=sys.stderr)

    inv_path = os.path.join(args.work, "input_inventory.json")
    write_json_atomic(inv_path, inv)  # 失败不致命

    print("[init] case_id={} status={} files={} -> {}".format(
        case_id, result["status"], len(inv["files"]), result_path))
    return 0


def cmd_merge(args) -> int:
    result_path = os.path.join(args.output, "result.json")
    ok, obj, err = read_result(result_path)
    if not ok:
        obj = make_skeleton("unknown", None, now_iso())
        obj["status"] = "partial"
        obj["errors"].append("merge 前结果缺失: {}".format(err))
    patch, perr, _ = load_json_robust(args.patch)
    if patch is None or not isinstance(patch, dict):
        print("[merge] 补丁不可解析: {}".format(perr[0] if perr else "未知"),
              file=sys.stderr)
        return 2
    deep_merge(obj, patch)
    obj.setdefault("change_log", []).append({
        "at": now_iso(), "event": "merge",
        "detail": os.path.basename(args.patch),
    })
    wok, werr = write_json_atomic(result_path, obj)
    if not wok:
        print("[merge] 写回失败: {}".format(werr), file=sys.stderr)
        return 3
    print("[merge] {} -> {}".format(args.patch, result_path))
    return 0


def cmd_finalize(args) -> int:
    result_path = os.path.join(args.output, "result.json")
    ok, obj, err = read_result(result_path)
    if not ok:
        obj = make_skeleton("unknown", None, now_iso())
        obj["status"] = "failed"
        obj["errors"].append(err)
    obj["status"] = args.status
    obj["finished_at"] = now_iso()
    if args.runtime is not None:
        obj.setdefault("runtime", {})["seconds"] = args.runtime
    if args.requests is not None:
        obj.setdefault("runtime", {})["requests"] = args.requests
    obj.setdefault("change_log", []).append({
        "at": now_iso(), "event": "finalize", "status": args.status,
    })
    wok, werr = write_json_atomic(result_path, obj)
    if not wok:
        print("[finalize] 写回失败: {}".format(werr), file=sys.stderr)
        return 3
    print("[finalize] status={} -> {}".format(args.status, result_path))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="slides-robust-adapt 主流程：鲁棒发现输入并落盘/合并 result.json")
    p.add_argument("command", nargs="?", default="init",
                   choices=["init", "merge", "finalize"],
                   help="子命令：init（默认）/merge/finalize")
    p.add_argument("--input", default=DEFAULT_INPUT, help="输入目录（默认 /input）")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    p.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    p.add_argument("--patch", default=None, help="merge：待合并的 JSON 补丁路径")
    p.add_argument("--status", default="ok",
                   choices=["ok", "partial", "failed",
                            "insufficient_evidence", "input_error"],
                   help="finalize：最终 status")
    p.add_argument("--runtime", type=float, default=None, help="finalize：耗时（秒）")
    p.add_argument("--requests", type=int, default=None, help="finalize：模型请求数")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init":
        return cmd_init(args)
    if args.command == "merge":
        if not args.patch:
            sys.stderr.write("merge 子命令需要 --patch\n")
            return 2
        return cmd_merge(args)
    if args.command == "finalize":
        return cmd_finalize(args)
    return cmd_init(args)


if __name__ == "__main__":
    sys.exit(main())
