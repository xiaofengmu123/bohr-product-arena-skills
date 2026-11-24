#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 文献综述 Skill 的确定性入口（仅标准库）。

用法：
  python -B scripts/main.py --input /input --output /app/output --work /app/work

职责：
1) 发现 /input 下的 case.json、task.md 与材料文件；
2) 解析 output_contract 并原样回填全部字段（缺值 null/[]，不改名不删键）；
3) 立即写出一版合法 result.json（诚实状态）作为安全骨架；
4) 扫描/去重材料、计算哈希、抽取 PDF 文本；
5) 产出 work/inventory.json 与各主产物的骨架占位文件。

退出码：0 表示骨架已落盘；2 仅在写盘彻底失败时返回。材料缺失/坏 JSON
一律降级记录，不当作进程崩溃。
"""

import argparse
import json
import os
import sys

import core_impl as C


def build_parser():
    ap = argparse.ArgumentParser(description="文献综述合成 Skill 脚手架")
    ap.add_argument("--input", default="/input", help="原始输入目录（默认 /input）")
    ap.add_argument("--output", default="/app/output", help="最终产物目录（默认 /app/output）")
    ap.add_argument("--work", default="/app/work", help="中间产物目录（默认 /app/work）")
    ap.add_argument("--max-file-bytes", type=int, default=C.MAX_FILE_BYTES,
                    help="单文件正文读取上限字节数")
    return ap


def initial_status(args, case_path, materials, case_ok):
    """按输入状况给出诚实的初始状态（Agent 后续可升级为 ok/partial 等）。"""
    if not os.path.isdir(args.input):
        return "input_error"
    if case_path and not case_ok and not materials:
        return "input_error"
    if not case_path and not materials:
        return "insufficient_evidence"
    return "partial"


def main(argv=None):
    args = build_parser().parse_args(argv)
    errors, degradations = [], []
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    # 1) 发现输入
    case_path, task_path, materials = C.discover_input(args.input)
    if not os.path.isdir(args.input):
        errors.append("输入目录不存在或不可读: " + args.input)
    if not case_path:
        errors.append("未找到 case.json")
    if not task_path:
        degradations.append("未找到 task.md")

    # 2) 解析 case.json（坏 JSON/坏编码均降级，绝不崩溃）
    case = None
    case_ok = True
    if case_path:
        case, errs = C.read_json(case_path, max_bytes=args.max_file_bytes)
        if errs:
            case_ok = False
            errors.extend(errs)
            case = case if isinstance(case, dict) else None
    if not isinstance(case, dict):
        case = {}
        if case_path:
            case_ok = False
            errors.append("case.json 不可解析，已按空对象降级: " + case_path)

    case_id = C.extract_case_id(case, case_path)
    question = C.extract_question(case)

    # 3) 归一化 output_contract；找不到则用内置兜底字段集
    contract = C.find_key_recursive(
        case, ("output_contract", "outputcontract", "output_schema", "outputschema"))
    fields = C.normalize_contract(contract)
    if not fields:
        fields = [(k, v) for k, v in C.DEFAULT_CONTRACT_FIELDS.items()]
        degradations.append("未找到 output_contract，已采用内置默认字段集")

    # 4) 先落盘骨架（安全第一；任何后续异常都不留空产物）
    status = initial_status(args, case_path, materials, case_ok)
    skeleton = C.build_skeleton(case_id, question, fields,
                                errors=errors or None,
                                degradations=degradations or None,
                                status=status)
    werr = C.write_json(os.path.join(args.output, "result.json"), skeleton)
    if werr:
        C.write_json(os.path.join(args.work, "result.json.fallback"), skeleton)
        print("[lit-review] 无法写 /app/output/result.json: " + werr, file=sys.stderr)
        return 2

    # 5) 材料盘点、去重、抽取
    inventory, dup_groups, ext_errors = C.scan_materials(materials, args.work, args.max_file_bytes)
    degradations.extend(ext_errors)
    C.write_json(os.path.join(args.work, "inventory.json"), {
        "case_id": case_id,
        "case_path": case_path,
        "task_path": task_path,
        "materials": inventory,
        "duplicate_groups": dup_groups,
    })

    # 6) 主产物骨架占位
    C.write_artifacts_skeleton(args.output, args.work, case_id, question, inventory)

    # 7) 增量回写盘点结果到 result.json
    skeleton["degradations"] = degradations or None
    skeleton["change_log"].append({
        "time": C.now_ts(),
        "action": "inventory_done",
        "detail": "已盘点 {0} 个材料文件，去重分组 {1} 个".format(len(inventory), len(dup_groups)),
    })
    C.write_json(os.path.join(args.output, "result.json"), skeleton)

    C.log("骨架完成: case_id={0} materials={1} status={2}".format(
        case_id, len(inventory), skeleton["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
