#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学位论文与长文档写作 Skill —— 编排器。

子命令：
  bootstrap  --input DIR --output DIR --work DIR   写合法 partial 骨架 result.json
  finalize   --output DIR --work DIR               校验并补齐 result.json，加 content_hash
  selftest                                         离线自检（临时目录，不碰正式产物）

仅依赖 Python 标准库。相对路径调用：python -B scripts/main.py <subcommand>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

FILE_SIZE_LIMIT = 2 * 1024 * 1024  # 单文本文件 2MB 上限

LIST_FIELDS = ("sources", "uncertainty", "pending_human", "limitations",
               "errors", "degradations", "change_log")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_change(result: dict, content: str, reason: str) -> None:
    result.setdefault("change_log", []).append(
        {"time": now_iso(), "content": content, "reason": reason}
    )


def read_json_safe(path: str):
    """容错读 JSON：BOM/坏编码降级，失败返回 (None, error)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, "read_json_failed: %s: %s" % (path, exc)


def read_text_safe(path: str, limit: int = FILE_SIZE_LIMIT):
    """容错读文本：超限跳过，坏编码 replace 降级，失败返回 (None, error)。"""
    try:
        size = os.path.getsize(path)
        if size > limit:
            return None, "oversize: %s (%d bytes > %d)" % (path, size, limit)
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(), None
    except Exception as exc:  # noqa: BLE001
        return None, "read_failed: %s: %s" % (path, exc)


def discover_input(input_dir: str) -> dict:
    """扫描输入目录，不假设固定文件名；返回清单。"""
    found = {"case_json": None, "task_md": None, "files": [], "materials": []}
    if not os.path.isdir(input_dir):
        return found
    for root, dirs, files in os.walk(input_dir):
        dirs.sort()
        files.sort()
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, input_dir).replace("\\", "/")
            found["files"].append(rel)
            lower = name.lower()
            if lower == "case.json":
                found["case_json"] = path
            elif lower == "task.md":
                found["task_md"] = path
            elif lower not in ("case.json", "task.md") and not lower.startswith("."):
                found["materials"].append(path)
    return found


def extract_output_contract(case):
    """从 case.json 中定位 output_contract（顶层或常见嵌套位置）。"""
    if not isinstance(case, dict):
        return None
    if isinstance(case.get("output_contract"), (dict, list)):
        return case["output_contract"]
    for key in ("task", "spec", "contract", "interface"):
        val = case.get(key)
        if isinstance(val, dict) and isinstance(val.get("output_contract"), (dict, list)):
            return val["output_contract"]
    return None


def contract_fields(contract) -> dict:
    """把 output_contract 的各种形状归一化为 {field: default}。

    - dict: {field: default} 或 {field: {type: ...}}
    - list: ["field1", ...] 或 [{"field": ...}, ...]
    缺值默认 None；type=array/list 默认 []；type=object 默认 {}。未知键原样保留。
    """
    fields: dict = {}
    if isinstance(contract, dict):
        src = contract
        if isinstance(contract.get("fields"), dict):
            src = contract["fields"]
        for key, val in src.items():
            if isinstance(val, dict):
                t = val.get("type")
                if t in ("array", "list"):
                    fields[key] = []
                elif t == "object":
                    fields[key] = {}
                else:
                    fields[key] = None
            elif isinstance(val, list):
                fields[key] = []
            else:
                fields[key] = val
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields[item] = None
            elif isinstance(item, dict):
                fields.update(contract_fields(item))
    return fields


def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "caseId", "id"):
        val = case.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def fresh_result() -> dict:
    return {
        "case_id": None,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": now_iso(), "seconds": 0.0, "model_requests": 0},
    }


def write_result(output_dir: str, result: dict) -> str:
    """原子写 result.json：先写 .tmp 再 replace。"""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "result.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def bootstrap(input_dir: str, output_dir: str, work_dir: str) -> dict:
    """Step 0：落盘合法 partial 骨架，任何异常都不崩溃。"""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    found = discover_input(input_dir)
    result = fresh_result()

    contract = None
    fields: dict = {}
    if found["case_json"]:
        case, err = read_json_safe(found["case_json"])
        if err:
            result["errors"].append(err)
            result["status"] = "input_error"
        elif isinstance(case, dict):
            result["case_id"] = extract_case_id(case)
            contract = extract_output_contract(case)
            fields = contract_fields(contract)
            if contract is None:
                result["degradations"].append(
                    "case.json 中未找到 output_contract；仅保留标准字段"
                )
        else:
            result["errors"].append("case.json 不是对象")
    else:
        result["errors"].append("缺少 /input/case.json")
        result["status"] = "insufficient_evidence" if not found["materials"] else "partial"

    for key, default in fields.items():
        result.setdefault(key, default)

    if not found["materials"]:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("输入目录下未发现任何材料文件")

    manifest = {
        "input_dir": input_dir,
        "found": found,
        "contract_fields": sorted(fields.keys()),
        "has_contract": contract is not None,
    }
    manifest_path = os.path.join(work_dir, "input_manifest.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append("write_manifest_failed: %s" % exc)

    log_change(result, "bootstrap 写入合法 partial 骨架", "Step 0")
    write_result(output_dir, result)
    return result


def finalize(output_dir: str, work_dir: str) -> dict:
    """Step N：校验并补齐 result.json；缺字段补默认值，失败不崩溃。"""
    os.makedirs(output_dir, exist_ok=True)
    result_path = os.path.join(output_dir, "result.json")

    if not os.path.exists(result_path):
        result = fresh_result()
        result["errors"].append("finalize 前 result.json 不存在，已写最小骨架")
        log_change(result, "finalize 发现缺失，写入最小骨架", "finalize")
    else:
        result, err = read_json_safe(result_path)
        if err or not isinstance(result, dict):
            result = fresh_result()
            result["status"] = "input_error"
            if err:
                result["errors"].append(err)

    result.setdefault("status", "partial")
    for key in LIST_FIELDS:
        result.setdefault(key, [])
    result.setdefault("case_id", None)
    result.setdefault("runtime", {"started_at": now_iso(),
                                  "seconds": 0.0, "model_requests": 0})

    # 用 input_manifest 回填 output_contract 字段（若在写作过程中丢失）
    manifest_path = os.path.join(work_dir, "input_manifest.json")
    manifest, merr = read_json_safe(manifest_path)
    if merr is None and isinstance(manifest, dict):
        for key in manifest.get("contract_fields", []):
            result.setdefault(key, None)

    rt = result.setdefault("runtime", {})
    rt.setdefault("started_at", now_iso())
    rt.setdefault("seconds", 0.0)
    rt.setdefault("model_requests", 0)

    if not result.get("change_log"):
        log_change(result, "finalize 补齐缺省字段", "finalize")

    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    result["content_hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    write_result(output_dir, result)
    return result


def selftest() -> int:
    """离线自检：临时目录中验证骨架/坏输入/契约回填/finalize 确定性。"""
    import shutil
    import tempfile

    failures = []
    base = tempfile.mkdtemp(prefix="wskill_selftest_")
    try:
        # 1) 空输入目录 → insufficient_evidence
        empty_in = os.path.join(base, "empty_in")
        os.makedirs(empty_in)
        r = bootstrap(empty_in, os.path.join(base, "out1"), os.path.join(base, "w1"))
        if r["status"] != "insufficient_evidence":
            failures.append("空输入未返回 insufficient_evidence")

        # 2) 坏 JSON → 记录 errors，不崩溃
        bad_in = os.path.join(base, "bad_in")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        r2 = bootstrap(bad_in, os.path.join(base, "out2"), os.path.join(base, "w2"))
        if not r2.get("errors"):
            failures.append("坏 JSON 未记录 errors")

        # 3) output_contract 回填正确 + finalize 补齐
        good_in = os.path.join(base, "good_in")
        mat_dir = os.path.join(good_in, "materials")
        os.makedirs(mat_dir)
        with open(os.path.join(good_in, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "case_id": "demo-1",
                "output_contract": {
                    "manuscript": None,
                    "references": [],
                    "evidence_map": {"type": "object"},
                },
            }, fh, ensure_ascii=False)
        with open(os.path.join(good_in, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo task\n")
        with open(os.path.join(mat_dir, "paper.md"), "w", encoding="utf-8") as fh:
            fh.write("## 1.1 引言\n图 2-1 显示……\n")
        r3 = bootstrap(good_in, os.path.join(base, "out3"), os.path.join(base, "w3"))
        if r3.get("case_id") != "demo-1":
            failures.append("case_id 提取失败")
        if "manuscript" not in r3 or "references" not in r3 or "evidence_map" not in r3:
            failures.append("output_contract 字段未回填")
        if r3.get("references") != []:
            failures.append("array 型契约字段默认值错误")
        f3 = finalize(os.path.join(base, "out3"), os.path.join(base, "w3"))
        if not f3.get("content_hash"):
            failures.append("finalize 未生成 content_hash")

        # 4) 确定性：同一输入两次 bootstrap 产物内容一致
        r4 = bootstrap(good_in, os.path.join(base, "out4"), os.path.join(base, "w4"))
        def strip_volatile(x):
            x = dict(x)
            x.pop("change_log", None)
            x["runtime"] = {}
            x.pop("content_hash", None)
            return json.dumps(x, ensure_ascii=False, sort_keys=True)
        if strip_volatile(r3) != strip_volatile(r4):
            failures.append("两次 bootstrap 产物不一致")

        # 5) 契约字段未知键不删除（回填覆盖原默认）
        if r3.get("evidence_map") != {}:
            failures.append("object 型契约字段默认值错误")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("selftest FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("selftest: 5 passed, 0 failed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="学位论文与长文档写作 Skill 编排器")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap", help="写合法 partial 骨架 result.json")
    b.add_argument("--input", default="/input")
    b.add_argument("--output", default="/app/output")
    b.add_argument("--work", default="/app/work")

    f = sub.add_parser("finalize", help="校验并补齐 result.json")
    f.add_argument("--output", default="/app/output")
    f.add_argument("--work", default="/app/work")

    sub.add_parser("selftest", help="离线自检")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "bootstrap":
        bootstrap(args.input, args.output, args.work)
    elif args.cmd == "finalize":
        finalize(args.output, args.work)
    elif args.cmd == "selftest":
        return selftest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
