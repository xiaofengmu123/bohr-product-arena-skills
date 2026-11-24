#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 机制图 Skill 入口（仅 Python 标准库）。

子命令（均在 Skill 根目录 /skills/weekly-skill 下以相对路径调用）：
  skeleton  读 /input 发现案例，落盘 result.json 骨架 + 空 MIR（前 3 分钟必做）
  validate  校验 MIR（spec.json），阻断错误退出码 1、警告退出码 2
  render    由 MIR 生成分层 SVG
  merge     把 MIR 与渲染结果合并进 result.json
  check     校验 result.json 契约与文件存在性
  pipeline  一键：skeleton -> [validate -> render -> merge] -> check
  selftest  委托 scripts/selftest.py（包级自检）

预算契约由 SKILL.md 约定（900 秒 / 64 次模型请求），本脚本只负责落盘与确定性产物。
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


# ---- 输入发现 ----------------------------------------------------------------

def discover_case_id(input_dir):
    """从 case.json / task.md 提取 case_id，失败返回 unknown，不抛异常。"""
    case_path = os.path.join(input_dir, "case.json")
    data = C.read_json_tolerant(case_path)
    if isinstance(data, dict):
        for key in ("case_id", "id", "challenge_id"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        if isinstance(data.get("content"), str):
            m = re.search(r"challenge[-_]?[A-Za-z0-9]+", data["content"])
            if m:
                return m.group(0)
    task_path = os.path.join(input_dir, "task.md")
    try:
        with open(task_path, "r", encoding="utf-8-sig") as fh:
            txt = fh.read()
        m = re.search(r"challenge[-_]?[A-Za-z0-9]+", txt)
        if m:
            return m.group(0)
    except OSError:
        pass
    return "unknown"


def list_materials(input_dir, max_bytes=2 * 1024 * 1024):
    """发现材料文件（只列文本类；PDF 由平台 pdf-text-tool 读取，脚本不自行解析）。"""
    found = []
    if not os.path.isdir(input_dir):
        return found
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in (".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml"):
            try:
                size = os.path.getsize(p)
            except OSError:
                size = None
            found.append({"name": name, "path": p, "ext": ext, "bytes": size,
                          "readable": (size is not None and size <= max_bytes)})
        elif ext == ".pdf":
            found.append({"name": name, "path": p, "ext": ext, "bytes": None,
                          "readable": False, "note": "PDF，用平台 pdf-text-tool 读取"})
    return found


# ---- 子命令实现 --------------------------------------------------------------

def cmd_skeleton(args):
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    case_id = discover_case_id(args.input)
    result_path = os.path.join(args.out, "result.json")
    if os.path.exists(result_path) and not args.force:
        result = C.read_json_tolerant(result_path) or C.empty_result(case_id)
    else:
        result = C.empty_result(case_id)
    C.append_change(result, "骨架落盘",
                    "发现 case_id=%s；先写 partial 骨架保证超时可交付" % case_id)
    materials = list_materials(args.input)
    result["materials"] = materials  # 附加字段：材料发现清单
    C.write_json(result_path, result)

    mir_path = os.path.join(args.work, "spec.json")
    if not os.path.exists(mir_path):
        C.write_json(mir_path, {
            "schema_version": C.SCHEMA_VERSION,
            "case_id": case_id,
            "title": None,
            "entities": [],
            "relations": [],
            "legend": [],
            "data_mappings": [],
        })
    print(json.dumps({"ok": True, "case_id": case_id, "result": result_path,
                      "mir": mir_path, "materials": len(materials)}, ensure_ascii=False))
    return 0


def cmd_validate(args):
    mir = C.read_json_tolerant(args.mir)
    if mir is None:
        report = {"ok": False, "errors": ["无法读取 MIR: %s" % args.mir], "warnings": []}
        C.write_json(args.out, report)
        print(json.dumps(report, ensure_ascii=False))
        return 1
    errors, warnings = C.validate_mir(mir)
    report = {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
    C.write_json(args.out, report)
    print(json.dumps(report, ensure_ascii=False))
    return 1 if errors else (2 if warnings else 0)


def cmd_render(args):
    mir = C.read_json_tolerant(args.mir)
    if mir is None:
        print(json.dumps({"ok": False, "errors": ["无法读取 MIR: %s" % args.mir]}, ensure_ascii=False))
        return 1
    errors, _ = C.validate_mir(mir)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False))
        return 1
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    svg = C.render_svg(mir)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    meta = {"ok": True, "svg": args.out, "bytes": len(svg.encode("utf-8")),
            "sha256": C.svg_sha256(svg), "layers": C.svg_layer_ids(svg)}
    if args.meta:
        C.write_json(args.meta, meta)
    print(json.dumps(meta, ensure_ascii=False))
    return 0


def cmd_merge(args):
    mir = C.read_json_tolerant(args.mir)
    result = C.read_json_tolerant(args.result)
    if mir is None:
        print(json.dumps({"ok": False, "errors": ["无法读取 MIR: %s" % args.mir]}, ensure_ascii=False))
        return 1
    if result is None:
        result = C.empty_result(mir.get("case_id"))
    result["case_id"] = mir.get("case_id") or result.get("case_id") or "unknown"
    result["title"] = mir.get("title") or result.get("title")
    result["schema_version"] = C.SCHEMA_VERSION
    entities, relations, legend, data_mappings, sources = C.mirror_from_mir(mir)
    result["entities"] = entities
    result["relations"] = relations
    result["legend"] = legend
    result["data_mappings"] = data_mappings
    result["sources"] = sources
    result["uncertainty"] = mir.get("uncertainty") or result.get("uncertainty") or []
    result["pending_human"] = mir.get("pending_human") or result.get("pending_human") or []

    svg_path = args.svg
    if svg_path and os.path.exists(svg_path):
        rel = os.path.basename(svg_path)
        svg_txt = open(svg_path, "r", encoding="utf-8").read()
        entry = {"path": rel, "format": "svg", "generator": "scripts/render.py",
                 "bytes": len(svg_txt.encode("utf-8")), "sha256": C.svg_sha256(svg_txt),
                 "layers": C.svg_layer_ids(svg_txt)}
        if not any(f.get("path") == rel for f in result.get("editable_source_files", [])):
            result.setdefault("editable_source_files", []).append(entry)
    if args.export_meta:
        em = C.read_json_tolerant(args.export_meta) or []
        for e in (em if isinstance(em, list) else []):
            if isinstance(e, dict) and e.get("path"):
                e["path"] = os.path.basename(e["path"])
            if e not in result.get("export_artifacts", []):
                result.setdefault("export_artifacts", []).append(e)
    # 若状态仍是 partial 但已有实体与 SVG，则由 Agent 在收尾时显式升级，此处不自动改判。
    C.append_change(result, "合并 MIR 与渲染产物",
                    "entities=%d relations=%d" % (len(entities), len(relations)))
    C.write_json(args.result, result)
    print(json.dumps({"ok": True, "result": args.result,
                      "entities": len(entities), "relations": len(relations)},
                     ensure_ascii=False))
    return 0


def cmd_check(args):
    result = C.read_json_tolerant(args.result)
    if result is None:
        print(json.dumps({"ok": False, "errors": ["无法读取 result.json: %s" % args.result]},
                         ensure_ascii=False))
        return 1
    errors, warnings = C.check_result(result, args.output_dir)
    report = {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
    print(json.dumps(report, ensure_ascii=False))
    return 1 if errors else (2 if warnings else 0)


def cmd_pipeline(args):
    rc = cmd_skeleton(args)
    if rc != 0:
        return rc
    mir_path = os.path.join(args.work, "spec.json")
    svg_path = os.path.join(args.out, "mechanism.svg")
    if os.path.exists(mir_path):
        vr = cmd_validate(argparse.Namespace(mir=mir_path,
                                            out=os.path.join(args.work, "validation.json")))
        if vr == 1:
            return vr
        rr = cmd_render(argparse.Namespace(mir=mir_path, out=svg_path,
                                           meta=os.path.join(args.work, "svg.meta.json")))
        if rr == 0:
            cmd_merge(argparse.Namespace(
                mir=mir_path, result=os.path.join(args.out, "result.json"),
                svg=svg_path, export_meta=os.path.join(args.work, "exports.meta.json")))
    return cmd_check(argparse.Namespace(
        result=os.path.join(args.out, "result.json"), output_dir=args.out))


def cmd_selftest(args):
    p = os.path.join(HERE, "selftest.py")
    if not os.path.exists(p):
        print(json.dumps({"ok": False, "errors": ["selftest.py 不存在"]}, ensure_ascii=False))
        return 1
    import subprocess
    rc = subprocess.call([sys.executable, p, "--tmp", args.tmp or ""])
    return rc


def main():
    ap = argparse.ArgumentParser(prog="mechanism-graphics")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("skeleton", help="落盘 result.json 骨架与空 MIR")
    p.add_argument("--input", default="/input", help="输入目录")
    p.add_argument("--out", default="/app/output", help="输出目录")
    p.add_argument("--work", default="/app/work", help="工作目录")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_skeleton)

    p = sub.add_parser("validate", help="校验 MIR")
    p.add_argument("--mir", default="/app/work/spec.json")
    p.add_argument("--out", default="/app/work/validation.json")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("render", help="渲染分层 SVG")
    p.add_argument("--mir", default="/app/work/spec.json")
    p.add_argument("--out", default="/app/output/mechanism.svg")
    p.add_argument("--meta", default=None)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("merge", help="合并 MIR/渲染产物到 result.json")
    p.add_argument("--mir", default="/app/work/spec.json")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--svg", default="/app/output/mechanism.svg")
    p.add_argument("--export-meta", default=None)
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("check", help="校验 result.json 契约")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--output-dir", default="/app/output")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("pipeline", help="一键流水线")
    p.add_argument("--input", default="/input", help="输入目录")
    p.add_argument("--out", default="/app/output", help="输出目录")
    p.add_argument("--work", default="/app/work", help="工作目录")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("selftest", help="运行包级自检")
    p.add_argument("--tmp", default=None)
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
