#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开题报告与研究计划书 Skill 的命令行入口。

子命令：
  discover   扫描输入目录，打印识别到的 case / 材料 / 契约字段
  init       读取 case.json，落盘 partial 骨架（前 3 分钟内先做这一步）
  validate   对 result.json 做顺序校验，打印 FAIL/WARN 并返回退出码
  render     从 result.json 生成 proposal.md / evidence_map.md / alignment.md
  merge      装配最终 result.json（补齐信封字段 + artifacts）
  check      validate 的别名（兼容旧调用习惯）
  all        init -> validate -> render -> merge 一键流程

仅依赖 Python 标准库。默认路径 /input、/app/output、/app/work；可被 --input/
--out/--work 覆盖，便于本地自检（selftest.py 亦如此）。
"""

from __future__ import annotations

import argparse
import os
import sys

# 强制 UTF-8 stdout/stderr，避免 Windows GBK 控制台把 JSON/中文输出写坏。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402


def _resolve_out(input_dir: str) -> str:
    """由输入目录推导输出目录（/input -> /app/output；本地则 <input>/../output）。"""
    base = os.path.abspath(input_dir).rstrip(os.sep)
    if base in ("/input",):
        return "/app/output"
    return os.path.join(os.path.dirname(base), "output")


def cmd_discover(a) -> int:
    info = core.discover_inputs(a.input)
    print(json_dump({
        "input_dir": info["input_dir"],
        "case_path": info["case_path"],
        "task_path": info["task_path"],
        "materials": info["materials"],
        "errors": info["errors"],
    }))
    if info.get("case"):
        contract = core.extract_contract(info["case"])
        print(json_dump({"contract": contract}))
    return 0 if not info["errors"] else 1


def cmd_init(a) -> int:
    info = core.discover_inputs(a.input)
    if not info.get("case"):
        # 空输入/坏输入也落盘诚实骨架
        case_id = "unknown"
        contract = {"fields": list(core.DEFAULT_CONTRACT_FIELDS), "declared": False, "source": "default"}
        note = "case.json 缺失或不可读，落盘 insufficient_evidence 骨架"
        status = "insufficient_evidence"
    else:
        case = info["case"]
        case_id = (case.get("case_id") or case.get("id") or "unknown") if isinstance(case, dict) else "unknown"
        contract = core.extract_contract(case)
        note = "由 case.json 落盘的 partial 骨架"
        status = "partial"
    skeleton = core.make_skeleton(case_id, contract["fields"], note)
    skeleton["status"] = status
    if info["errors"]:
        skeleton["degradations"] = list(info["errors"])
    out_path = os.path.join(a.out, "result.json")
    os.makedirs(a.out, exist_ok=True)
    core.dump_json(skeleton, out_path)
    print(json_dump({"ok": True, "out": out_path, "case_id": case_id,
                     "contract": contract, "status": skeleton["status"]}))
    return 0


def cmd_validate(a) -> int:
    result_path = os.path.join(a.out, "result.json")
    result, err = core.load_json_loose(result_path)
    if err or not isinstance(result, dict):
        print(json_dump({"ok": False, "errors": [f"result_unreadable:{err}"], "fail": 1}))
        return 1
    # 契约字段解析顺序：result 记录 > 重新发现 case.json > 默认契约。
    fields = result.get("contract_fields")
    if not isinstance(fields, list) or not fields:
        info = core.discover_inputs(a.input)
        fields = (core.extract_contract(info["case"])["fields"]
                  if info.get("case") else list(core.DEFAULT_CONTRACT_FIELDS))
    issues = core.validate(result, fields, a.out)
    fails = core.fail_count(issues)
    warns = sum(1 for i in issues if i["level"] == "WARN")
    print(json_dump({"ok": fails == 0, "fail": fails, "warn": warns, "issues": issues}))
    return 1 if fails else 0


def cmd_render(a) -> int:
    result_path = os.path.join(a.out, "result.json")
    result, err = core.load_json_loose(result_path)
    if err or not isinstance(result, dict):
        print(json_dump({"ok": False, "error": f"result_unreadable:{err}"}))
        return 1
    files = core.render(result, a.out)
    print(json_dump({"ok": True, "rendered": files}))
    return 0


def cmd_merge(a) -> int:
    result_path = os.path.join(a.out, "result.json")
    result, err = core.load_json_loose(result_path)
    if err or not isinstance(result, dict):
        print(json_dump({"ok": False, "error": f"result_unreadable:{err}"}))
        return 1
    case_id = result.get("case_id") or "unknown"
    fields = result.get("contract_fields") or list(core.DEFAULT_CONTRACT_FIELDS)
    files = core.render(result, a.out)
    final = core.assemble_final(case_id, fields, result, files)
    core.dump_json(final, result_path)
    print(json_dump({"ok": True, "artifacts": files, "status": final.get("status")}))
    return 0


def cmd_all(a) -> int:
    rc = 0
    rc |= cmd_init(a)
    rc |= cmd_validate(a)
    rc |= cmd_merge(a)
    return rc


def json_dump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", default="/input")
    parser.add_argument("--out", default=None)
    parser.add_argument("--work", default="/app/work")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="main.py",
                                 description="开题报告与研究计划书 Skill 工具链")
    sub = ap.add_subparsers(dest="command")
    for name, fn in (
        ("discover", cmd_discover), ("init", cmd_init), ("validate", cmd_validate),
        ("render", cmd_render), ("merge", cmd_merge), ("check", cmd_validate),
        ("all", cmd_all),
    ):
        p = sub.add_parser(name, help=fn.__doc__ or name)
        _add_common(p)
        p.set_defaults(fn=fn)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.out is None:
        a.out = _resolve_out(a.input)
    if a.command is None:
        ap.print_help()
        return 0
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
