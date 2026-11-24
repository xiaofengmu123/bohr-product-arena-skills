#!/usr/bin/env python3
"""main.py — slides-01 薄入口：契约骨架 → 校验 → 渲染 → 组装 → 自检。

Agent 内容工作（读材料、写 deck_ir.json）由 Agent 自己做，脚本不替你组织内容；
本入口只负责“契约/渲染/校验/交付”这层确定性机器工作。

用法（相对路径，全部可独立执行）：
  python3 -B scripts/main.py --skeleton            # 60 秒内落盘合法 result.json 骨架 + IR 桩
  python3 -B scripts/main.py --validate-ir         # 校验 work/deck_ir.json
  python3 -B scripts/main.py --verify-quotes       # 用 work/materials.txt 核验逐字 quote
  python3 -B scripts/main.py --render              # IR -> pptx + PDF（或 SVG 降级）
  python3 -B scripts/main.py --assemble            # 组装最终 result.json
  python3 -B scripts/main.py --scan                # 列出输入材料
  python3 -B scripts/main.py --selftest            # 本地自检（空目录/坏JSON/IR/引用/PDF）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core  # noqa: E402
import build  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def resolve_dirs(args) -> tuple:
    """解析 --input/--work/--output，写盘失败时降级到可写目录。"""
    input_root = Path(args.input).expanduser() if args.input else Path("/input")
    work = Path(args.work).expanduser() if args.work else Path("/app/work")
    output = Path(args.output).expanduser() if args.output else Path("/app/output")
    for p in (work, output):
        ok, err = core.ensure_dir(p)
        if not ok:
            fallback = Path.cwd() / p.name
            core.ensure_dir(fallback)
            if p == work:
                work = fallback
            else:
                output = fallback
    return input_root, work, output


def load_case(input_root: Path) -> tuple:
    case, err = core.read_json(input_root / "case.json")
    if err:
        return None, err
    return (case if isinstance(case, dict) else {}), None


def discover_materials(input_root: Path) -> list:
    """扫描输入材料（跳过 case.json），分类为 sources 供结果溯源。"""
    sources = []
    if not input_root.is_dir():
        return sources
    for p in sorted(input_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(input_root).as_posix()
        if rel == "case.json":
            continue
        suffix = p.suffix.lower()
        kind = ("pdf" if suffix == ".pdf" else
                "pptx" if suffix in (".pptx", ".potx") else
                "image" if suffix in (".png", ".jpg", ".jpeg", ".svg", ".gif") else
                "text" if suffix in (".md", ".txt", ".tex", ".csv", ".tsv", ".json", ".yaml", ".yml") else
                "other")
        sources.append({"source_id": rel, "kind": kind, "path": rel})
    return sources


def find_template(input_root: Path, sources: list):
    for s in sources:
        if s["kind"] == "pptx":
            return input_root / s["path"]
    return None


def cmd_skeleton(args, rt):
    input_root, work, output = resolve_dirs(args)
    rt.step("skeleton")
    case, err = load_case(input_root)
    status = "partial"
    case_id = "unknown-case"
    payload = {"editable_source": {}, "export_artifact": {}, "content_map": [],
               "compliance_checks": [], "exceptions": [], "sources": [],
               "uncertainty": [], "pending_human": [], "errors": [],
               "degradations": [], "changes": [], "change_log": []}
    if case is None:
        status = "input_error"
        rt.error(f"case.json 不可用: {err}")
        payload["errors"] = list(rt.errors)
    else:
        case_id = case.get("case_id") or "unknown-case"
    keys = core.discover_contract_keys(case or {})
    result, _missing = core.fill_contract(keys, payload)
    for k in core.ENVELOPE:
        if k not in result:
            result[k] = payload.get(k, core.default_value(k))
    result["case_id"] = case_id
    result["status"] = status
    result["runtime"] = rt.snapshot()

    if not core.write_json(output / "result.json", result):
        rt.error("result.json 骨架写盘失败")
    ir = core.ir_stub()
    if not core.write_json(work / "deck_ir.json", ir):
        rt.error("deck_ir.json 桩写盘失败")
    core.write_text(work / "materials.txt", "")

    print(json.dumps({"case_id": case_id, "status": status,
                      "contract_keys": keys, "sources": discover_materials(input_root)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_scan(args, rt):
    input_root, _work, _output = resolve_dirs(args)
    rt.step("scan")
    case, err = load_case(input_root)
    keys = core.discover_contract_keys(case or {}) if case else list(core.FALLBACK_CONTRACT)
    print(json.dumps({
        "case_id": (case or {}).get("case_id") if case else "unknown-case",
        "contract_keys": keys,
        "sources": discover_materials(input_root),
    }, ensure_ascii=False, indent=2))
    return 0


def load_ir(args):
    _i, work, _o = resolve_dirs(args)
    ir_path = Path(args.ir) if args.ir else (work / "deck_ir.json")
    ir, err = core.read_json(ir_path)
    if err:
        return None, err
    return ir, None


def load_materials(args):
    _i, work, _o = resolve_dirs(args)
    mp = Path(args.materials) if args.materials else (work / "materials.txt")
    try:
        return mp.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return "", f"materials.txt 读取失败: {exc.__class__.__name__}"


def cmd_validate(args, rt):
    rt.step("validate_ir")
    ir, err = load_ir(args)
    if err:
        print(json.dumps({"ok": False, "issues": [
            {"level": "error", "code": "IR_READ", "message": err}]},
            ensure_ascii=False, indent=2))
        return 1
    ok, issues = core.validate_ir(ir)
    print(json.dumps({"ok": ok, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_verify(args, rt):
    rt.step("verify_quotes")
    ir, err = load_ir(args)
    if err:
        print(json.dumps({"ok": False, "issues": [
            {"level": "error", "code": "IR_READ", "message": err}]},
            ensure_ascii=False, indent=2))
        return 1
    mat, merr = load_materials(args)
    if merr:
        print(json.dumps({"ok": False, "issues": [
            {"level": "error", "code": "MAT_READ", "message": merr}]},
            ensure_ascii=False, indent=2))
        return 1
    ok, issues = core.validate_ir(ir, materials_text=mat)
    print(json.dumps({"ok": ok, "issues": issues}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_render(args, rt):
    rt.step("render")
    input_root, work, output = resolve_dirs(args)
    ir, err = load_ir(args)
    if err:
        rt.error(f"IR 读取失败: {err}")
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1

    editable, export, exceptions, degradations = {}, {}, [], []
    template = Path(args.template) if args.template else find_template(
        input_root, discover_materials(input_root))

    pptx_path = output / "presentation.pptx"
    ok, info = build.render_pptx(ir, str(template) if template else None, pptx_path)
    if ok:
        editable = {"path": "presentation.pptx", "format": "pptx",
                    "slides": info.get("slides", 0)}
        rt.change(f"render: pptx 生成，模板继承={info.get('template_inherited')}")
        # PDF：优先 soffice 真渲染
        pdf_ok, pdf_info = build.export_pdf_soffice(pptx_path, output / "presentation.pdf")
        if pdf_ok:
            export["pdf"] = {"path": "presentation.pdf", "pages": pdf_info.get("pages", 0)}
        else:
            degradations.append(f"soffice 不可用，改用最小文本 PDF 预览: {pdf_info if isinstance(pdf_info, str) else ''}")
            pdf2, p2 = build.write_minimal_pdf(ir, output / "presentation.pdf")
            if pdf2:
                export["pdf"] = {"path": "presentation.pdf", "pages": p2}
            else:
                rt.error(f"PDF 降级失败: {p2}")
    else:
        # 无 python-pptx：SVG + PNG + 最小 PDF 降级，如实标注
        degradations.append(f"python-pptx 不可用，改用 SVG+PNG+PDF 降级: {info if isinstance(info, str) else 'unknown'}")
        svg_dir = output / "slides_svg"
        svgs, serr = build.render_svg(ir, svg_dir)
        if serr:
            rt.error(serr)
        pngs, _perr = build.render_pngs(ir, output / "slides_png")
        pdf_ok2, p2 = build.write_minimal_pdf(ir, output / "presentation.pdf")
        if pdf_ok2:
            export["pdf"] = {"path": "presentation.pdf", "pages": p2}
        else:
            rt.error(p2)
        editable = {"path": "slides_svg/", "format": "svg", "slides": len(ir.get("slides", [])),
                    "note": "无 pptx 能力的降级可编辑产物（逐页 SVG）"}
        export["svg"] = svgs
        export["png"] = pngs
        exceptions.append({
            "code": "NO_PPTX_RENDERER",
            "message": "运行时无 python-pptx，已降级为 SVG+PNG+PDF；如需可编辑 pptx 请提供 office-renderer",
        })

    # 页数一致性核验
    slides_n = len(ir.get("slides", []))
    pdf_pages = (export.get("pdf") or {}).get("pages", 0)
    checks = [{
        "check": "页数一致（pptx/slides vs PDF）",
        "pass": slides_n > 0 and pdf_pages == slides_n,
        "detail": f"slides={slides_n}, pdf_pages={pdf_pages}",
    }]
    if pdf_pages != slides_n:
        degradations.append(f"页数不一致: slides={slides_n}, pdf={pdf_pages}")

    # 落盘渲染中间结果供 --assemble 复用
    core.write_json(work / "artifacts.json", {
        "editable_source": editable, "export_artifact": export,
        "exceptions": exceptions, "degradations": degradations, "checks": checks,
    })
    print(json.dumps({"ok": True, "editable_source": editable,
                      "export_artifact": export, "checks": checks,
                      "degradations": degradations},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_assemble(args, rt):
    rt.step("assemble")
    input_root, work, output = resolve_dirs(args)
    case, _err = load_case(input_root)
    keys = core.discover_contract_keys(case or {})
    ir, err = load_ir(args)
    if err:
        rt.error(f"IR 读取失败: {err}")
        ir = core.ir_stub()
    artifacts, _aerr = core.read_json(work / "artifacts.json")
    if not isinstance(artifacts, dict):
        artifacts = {}
        rt.error("artifacts.json 缺失，请先运行 --render")

    # 校验并写入 compliance_checks（含 IR 校验结果；加载材料文本做逐字核验）
    mat, _merr = load_materials(args)
    ok, issues = core.validate_ir(ir, materials_text=mat)
    checks = list(artifacts.get("checks", []))
    checks.append({"check": "Deck IR 校验", "pass": ok,
                   "detail": "; ".join(i["message"] for i in issues[:6])})
    for i in issues:
        if i["level"] == "error":
            rt.error(i["message"])
        else:
            rt.degrade(i["message"])

    status = "ok" if (ok and not rt.errors and artifacts.get("editable_source")) else "partial"
    if case is None:
        status = "input_error"
    if artifacts and not artifacts.get("editable_source"):
        status = "insufficient_evidence" if status == "partial" else status

    result, _missing = core.assemble_result(
        case or {}, keys, ir,
        {"editable_source": artifacts.get("editable_source", {}),
         "export_artifact": artifacts.get("export_artifact", {}),
         "exceptions": artifacts.get("exceptions", []),
         "uncertainty": artifacts.get("uncertainty", []),
         "pending_human": artifacts.get("pending_human", [])},
        checks, rt, status,
        sources=discover_materials(input_root))

    # 把 render 阶段的 degradations 并入
    for d in artifacts.get("degradations", []):
        if d not in result["degradations"]:
            result["degradations"].append(d)

    if not core.write_json(output / "result.json", result):
        rt.error("result.json 写盘失败")
    print(json.dumps({"case_id": result["case_id"], "status": result["status"],
                      "missing_contract_keys": []},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------- 自检 ----------------

def selftest() -> int:
    fails = []
    tmp = Path(tempfile.mkdtemp(prefix="slides01_selftest_"))
    rt = core.Runtime()

    def check(name, cond, detail=""):
        print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail else ""))
        if not cond:
            fails.append(name)

    # 1) 空输入目录 -> 合法 result.json，status=input_error，退出码 0
    empty_in = tmp / "empty_in"
    empty_in.mkdir()
    out1 = tmp / "out1"
    work1 = tmp / "work1"
    args1 = argparse.Namespace(input=str(empty_in), work=str(work1), output=str(out1))
    rt1 = core.Runtime()
    rc1 = cmd_skeleton(args1, rt1)
    r1, _ = core.read_json(out1 / "result.json")
    check("empty_input_skeleton_rc0", rc1 == 0)
    check("empty_input_status", isinstance(r1, dict) and r1.get("status") == "input_error")
    check("empty_input_case_id", isinstance(r1, dict) and r1.get("case_id") == "unknown-case")

    # 2) 坏 JSON -> 不抛异常，status=input_error
    bad_in = tmp / "bad_in"
    bad_in.mkdir()
    (bad_in / "case.json").write_text("{not json", encoding="utf-8")
    args2 = argparse.Namespace(input=str(bad_in), work=str(tmp / "work2"), output=str(tmp / "out2"))
    rc2 = cmd_skeleton(args2, core.Runtime())
    check("badjson_rc0", rc2 == 0)

    # 3) 输出契约回填（dict / list / 缺省三种）
    case_dict = {"case_id": "c1", "output_contract": {"a": None, "editable_source": None}}
    keys3 = core.discover_contract_keys(case_dict)
    filled3, missing3 = core.fill_contract(keys3, {"editable_source": {}})
    check("contract_dict_keys", keys3 == ["a", "editable_source"], str(keys3))
    check("contract_dict_default", filled3.get("a") is None and missing3 == ["a"])
    keys4 = core.discover_contract_keys({"output_contract": ["x", {"name": "y"}]})
    check("contract_list_keys", keys4 == ["x", "y"], str(keys4))
    keys5 = core.discover_contract_keys({})
    check("contract_fallback", keys5 == core.FALLBACK_CONTRACT)

    # 4) IR 校验：合法通过；非法（缺 claim/坏 role/编造图号）报错
    ir_ok = core.ir_stub()
    ir_ok["meta"].update({"title": "T", "slide_cap": 3})
    ir_ok["spine"].update({"research_question": "Q", "approach_claim": "A", "conclusion": "C"})
    ir_ok["figure_index"] = [{"id": "Figure 1", "source_id": "p.pdf",
                              "locator": "p2", "caption_quote": "abc"}]
    ir_ok["slides"] = [{
        "index": 1, "role": "result", "claim": "Figure 1 显示 42% 提升",
        "bullets": ["b"], "figure_ref": "Figure 1",
        "notes": {"transition": "t", "emphasis": "e"},
        "evidence": [{"source_id": "p.pdf", "locator": "p2", "quote": "abc 42%"}]}]
    ok6, issues6 = core.validate_ir(ir_ok, materials_text="abc 42% Figure 1")
    check("ir_valid_passes", ok6, "; ".join(i["message"] for i in issues6))
    ir_bad = json.loads(json.dumps(ir_ok))
    ir_bad["slides"][0]["claim"] = ""
    ir_bad["slides"][0]["role"] = "bogus"
    ir_bad["slides"][0]["figure_ref"] = "Figure 9"
    ok7, issues7 = core.validate_ir(ir_bad, materials_text="abc 42% Figure 1")
    check("ir_invalid_reported", not ok7 and len(issues7) >= 3, str([i["code"] for i in issues7]))

    # 5) 逐字引用：命中通过，改写不通过
    quote_ok = core.quote_matches("results show  42%  gain", "results show 42% gain")
    quote_bad = core.quote_matches("results show 99% gain", "results show 42% gain")
    check("quote_verbatim_ok", quote_ok)
    check("quote_verbatim_bad_rejected", not quote_bad)

    # 6) 最小 PDF：可写、页数一致、%PDF- 开头
    pdf_path = tmp / "t.pdf"
    ok8, pages8 = build.write_minimal_pdf(ir_ok, pdf_path)
    head = pdf_path.read_bytes()[:5] if pdf_path.exists() else b""
    check("minimal_pdf_write", ok8 and pages8 == 1, f"pages={pages8}")
    check("minimal_pdf_header", head == b"%PDF-")
    check("minimal_pdf_pagecount", build.pdf_page_count(pdf_path) == 1)

    # 7) SVG 降级可写
    svgs, serr = build.render_svg(ir_ok, tmp / "svg")
    check("svg_fallback", bool(svgs) and not serr, str(serr))

    # 8) SKILL.md frontmatter 齐全
    skill_path = Path(__file__).resolve().parent.parent / "SKILL.md"
    fm_ok = False
    if skill_path.exists():
        txt = skill_path.read_text(encoding="utf-8", errors="replace")
        fm_ok = txt.startswith("---") and "name:" in txt[:400] \
            and "description:" in txt[:400] and "version:" in txt[:400]
    check("skill_frontmatter", fm_ok, str(skill_path))

    print(f"\nselftest: {len(fails)} failed / {''.join(fails)}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="slides-01 契约/渲染/自检入口")
    ap.add_argument("--input", default=None)
    ap.add_argument("--work", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--ir", default=None)
    ap.add_argument("--materials", default=None)
    ap.add_argument("--template", default=None)
    ap.add_argument("--skeleton", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--validate-ir", action="store_true")
    ap.add_argument("--verify-quotes", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--assemble", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    rt = core.Runtime()
    try:
        if args.scan:
            return cmd_scan(args, rt)
        if args.validate_ir:
            return cmd_validate(args, rt)
        if args.verify_quotes:
            return cmd_verify(args, rt)
        if args.render:
            return cmd_render(args, rt)
        if args.assemble:
            return cmd_assemble(args, rt)
        # 默认：先落盘骨架（保证不空手）
        return cmd_skeleton(args, rt)
    except Exception:
        rt.error("未捕获异常: " + traceback.format_exc(limit=2).replace("\n", " | "))
        _i, _w, output = resolve_dirs(args)
        core.write_json(output / "result.json", {
            "case_id": "unknown-case", "status": "input_error",
            "errors": list(rt.errors), "runtime": rt.snapshot(),
        })
        return 1


if __name__ == "__main__":
    sys.exit(main())
