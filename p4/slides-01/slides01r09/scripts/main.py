#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-01 通用入口：契约发现 + 先落盘骨架 + 可选构建/校验。

四个独立单次工具（SKILL.md 约定按需调用，不串成流水线）：
  python3 -B scripts/main.py scaffold          # 立即写合法 partial 骨架
  python3 -B scripts/main.py build             # 由 work/plan.json 生成 presentation.pptx
  python3 -B scripts/main.py check             # 校验 result.json 契约与主产物文件
  python3 -B scripts/main.py stats             # 只读检查 pptx/pdf 结构（不改任何文件）

默认 --input /input --work /app/work --output /app/output；本地可用参数覆盖。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from slides_lib import (  # noqa: E402
    CANONICAL_FIELDS, build_deck, build_skeleton, discover_materials,
    ensure_dir, extract_contract_keys, inherit_deck, is_zip_pptx, now_iso,
    pdf_page_count, pptx_stats, read_json, validate_draft, validate_result,
    write_json, write_text,
)

DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")


def resolve_dirs(args) -> tuple:
    inp = Path(args.input).expanduser() if args.input else DEFAULT_INPUT
    work = Path(args.work).expanduser() if args.work else DEFAULT_WORK
    out = Path(args.output).expanduser() if args.output else DEFAULT_OUTPUT
    for d in (work, out):
        ensure_dir(d)
    return inp, work, out


def cmd_scaffold(args) -> int:
    inp, work, out = resolve_dirs(args)
    case, err = read_json(inp / "case.json")
    if err or not isinstance(case, dict):
        case_id = "unknown-case"
        keys = list(CANONICAL_FIELDS)
        errors = [f"case.json unavailable: {err}" if err else "case.json 非对象"]
        status = "input_error"
    else:
        case_id = str(case.get("case_id") or case.get("id") or "unknown-case")
        keys, note = extract_contract_keys(case)
        errors = [note] if note else []
        status = "partial"

    # 仅当字段确实在契约中才补规范占位，避免误解未知字段语义。
    canonical = {}
    if "editable_source" in keys:
        canonical["editable_source"] = {
            "path": "presentation.pptx", "format": "pptx", "slides": 0,
            "template": {"provided": False, "inherited": False},
        }
    if "render_record" in keys or "export_artifact" in keys:
        key = "render_record" if "render_record" in keys else "export_artifact"
        canonical[key] = {
            "source": "presentation.pptx", "export_pdf": None,
            "export_png": None, "fonts": [], "template": None, "changes": [],
        }
    if "evidence_map" in keys or "content_map" in keys:
        key = "evidence_map" if "evidence_map" in keys else "content_map"
        canonical[key] = []
    if "compliance_checks" in keys:
        canonical["compliance_checks"] = []

    skeleton = build_skeleton(case_id, keys, status=status, errors=errors, payload=canonical)

    materials = discover_materials(inp)
    skeleton["sources"] = [
        {"rel": m["rel"], "bytes": m["bytes"], "kind": m["kind"]} for m in materials]
    skeleton.setdefault("change_log", []).append({
        "at": now_iso(), "action": "scaffold",
        "note": "落盘 partial 骨架：先交粗稿，再增量改进",
    })

    write_json(out / "result.json", skeleton)
    write_json(work / "plan.json", {
        "case_id": case_id, "status": status, "created_at": now_iso(), "slides": [],
        "notes": "逐页填充 page/claim/role/evidence[{locator,quote}]/figures/notes",
    })
    write_text(work / "contract_keys.txt",
               "output_contract 字段（按 case.json 原样顺序）：\n" + "\n".join(keys) + "\n")
    print(f"[scaffold] case_id={case_id} status={status} 契约字段={len(keys)} 材料={len(materials)}")
    print(f"[scaffold] 已写 {out / 'result.json'} 与 {work / 'plan.json'}")
    return 0


def _load_plan(work: Path):
    plan, err = read_json(work / "plan.json")
    if err or not isinstance(plan, dict):
        return None, f"work/plan.json 不可读: {err or '非对象'}"
    slides = plan.get("slides")
    if not isinstance(slides, list) or not slides:
        return None, "plan.json 无 slides 列表"
    return plan, None


def cmd_build(args) -> int:
    inp, work, out = resolve_dirs(args)
    plan, err = _load_plan(work)
    if err:
        print(f"[build] {err}")
        return 1

    # 证据校验先于成稿：阻止无 locator/quote 的页被静默写入 pptx。
    issues = validate_draft(plan)
    if issues:
        print("[build] 草稿未通过证据校验，拒绝生成：")
        for it in issues:
            print(f"  - {it}")
        return 2

    slides = [{"title": str(p.get("title") or p.get("claim") or f"第 {p.get('page', i + 1)} 页"),
               "body": [str(b) for b in (p.get("bullets") or p.get("points") or [])
                        if str(b).strip()] or [str(p.get("claim", ""))]}
              for i, p in enumerate(plan["slides"])]

    template = getattr(args, "template", None)
    tpl_path = None
    if template:
        cand = Path(template).expanduser()
        if cand.exists():
            tpl_path = cand
    if not tpl_path:
        # 自动从输入目录找 .pptx 模板。
        for m in discover_materials(inp):
            if m["kind"] == "slide_template" and Path(m["path"]).exists():
                tpl_path = Path(m["path"])
                break

    out_pptx = out / "presentation.pptx"
    if tpl_path:
        ok, err = inherit_deck(tpl_path, out_pptx, slides)
        mode = "inherit"
    else:
        ok, err = build_deck(slides, out_pptx, title=str(plan.get("title") or "Academic presentation"))
        mode = "minimal"

    if not ok:
        print(f"[build] 失败: {err}")
        return 3
    print(f"[build] 已写 {out_pptx}（mode={mode}，模板={str(tpl_path) if tpl_path else '无'}）")
    write_json(out / "plan.json", plan)
    print(f"[build] 已同步 {out / 'plan.json'}")
    return 0


def cmd_check(args) -> int:
    inp, work, out = resolve_dirs(args)
    result, err = read_json(out / "result.json")
    if err or not isinstance(result, dict):
        print(f"[check] result.json 不可读: {err}")
        return 1
    case, _ = read_json(inp / "case.json")
    keys, _ = extract_contract_keys(case if isinstance(case, dict) else {})
    if not keys:
        keys = [k for k in result if k not in {
            "status", "sources", "uncertainty", "pending_human",
            "errors", "degradations", "change_log", "runtime"}]
    issues = validate_result(result, keys, output_dir=out)

    src = result.get("editable_source")
    if isinstance(src, dict) and src.get("path"):
        p = Path(src["path"])
        if not p.is_absolute():
            p = out / p
        if p.exists() and p.suffix.lower() == ".pptx" and not is_zip_pptx(p):
            issues.append(f"pptx 结构非法（非 zip 或缺 slide 部件）: {p}")

    rr = result.get("render_record") or result.get("export_artifact")
    pdf = rr.get("export_pdf") if isinstance(rr, dict) else None
    if pdf:
        pp = Path(pdf)
        if not pp.is_absolute():
            pp = out / pp
        if pp.exists():
            cnt, perr = pdf_page_count(pp)
            if perr:
                issues.append(f"PDF 校验失败: {perr}")
            elif (isinstance(src, dict) and isinstance(src.get("slides"), int)
                  and src["slides"] > 0 and cnt is not None and cnt != src["slides"]):
                issues.append(f"PDF 页数({cnt})与 slides 数({src['slides']})不一致")

    checks = result.get("compliance_checks")
    if not isinstance(checks, list):
        checks = []
    checks.append({"at": now_iso(), "mode": "check", "passed": not issues, "issues": issues})
    result["compliance_checks"] = checks
    write_json(out / "result.json", result)
    print(f"[check] 通过={not issues} 问题={len(issues)}")
    for it in issues:
        print(f"  - {it}")
    return 0 if not issues else 2


def cmd_stats(args) -> int:
    inp, work, out = resolve_dirs(args)
    rc = 0
    for name in ("presentation.pptx",):
        p = out / name
        if not p.exists():
            print(f"[stats] 缺 {name}")
            rc = 1
            continue
        stats, err = pptx_stats(p)
        if err:
            print(f"[stats] {name} 非法: {err}")
            rc = 1
        else:
            print(f"[stats] {name}: slides={stats['slides']} masters={stats['masters']} "
                  f"layouts={stats['layouts']} ct={stats['has_content_types']}")
            if stats["first_slide_preview"]:
                print(f"[stats]   首页预览: {stats['first_slide_preview'][:120]}")
    for name in ("presentation.pdf",):
        p = out / name
        if not p.exists():
            print(f"[stats] 缺 {name}（可用 office-renderer 导出后再查）")
            continue
        cnt, err = pdf_page_count(p)
        print(f"[stats] {name}: 页数(启发式)={cnt}" + (f" 注意: {err}" if err else ""))
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="main.py", description="slides-01 入口")
    ap.add_argument("cmd", nargs="?", choices=["scaffold", "build", "check", "stats"],
                    default=None, help="模式；也可用 --mode 指定")
    ap.add_argument("--mode", choices=["scaffold", "build", "check", "stats"], default="scaffold")
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--template", default=None)
    args = ap.parse_args(argv)
    mode = args.cmd or args.mode
    started = time.time()
    handlers = {"scaffold": cmd_scaffold, "build": cmd_build,
                "check": cmd_check, "stats": cmd_stats}
    try:
        rc = handlers[mode](args)
    except Exception as exc:  # noqa: BLE001 —— 兜底不得崩
        print(f"[fatal] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        rc = 9
    print(f"[done] elapsed={time.time() - started:.2f}s rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
