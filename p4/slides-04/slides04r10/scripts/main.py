#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — 学术幻灯片 Skill 入口（薄 CLI 编排层，不含重逻辑）。

命令：init(落 partial 骨架) / survey(清点) / lint(IR 校验) /
      compile(编译产出) / audit(回读复核) / selfcheck(终检)。
所有命令先落可解析结果，任何一步失败保留部分产物；退出码恒 0（错误写入 result）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from core import (  # noqa: E402
    STATUS, SLIDE_RE, SLIDE_RELS_RE, MASTER_RE, THEME_RE, LAYOUT_RE,
    ir_skeleton, normalize_ir, lint_pages, inspect_template, select_layouts,
    bind_template, deck_info, write_pdf, write_png_preview, pdf_page_count,
    audit, sha256, read_zip, normalize_text, build_standalone_deck,
)

VERSION = "0.2.0"
TASK_LABEL = "学术 PPT 模板与格式适配"
DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

ROOT_FIELDS = ["case_id", "status", "editable_source", "export_artifact",
               "content_map", "compliance_checks", "exceptions"]
LIST_FIELDS = {"content_map", "compliance_checks", "exceptions", "sources",
               "uncertainty", "pending_human", "errors", "degradations", "change_log"}
DICT_FIELDS = {"editable_source", "export_artifact", "runtime"}

_STATE = {"errors": [], "degradations": [], "changes": [], "steps": [], "started": time.time()}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def step(name: str) -> None:
    _STATE["steps"].append(name)


def degrade(item: str) -> None:
    _STATE["degradations"].append(item)


def fail_hard(item: str) -> None:
    _STATE["errors"].append(item)


def read_json(path: Path):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    if not raw.strip():
        return None, f"{Path(path).name} 为空文件"
    try:
        return json.loads(raw.decode("utf-8-sig")), None
    except UnicodeDecodeError:
        return None, f"{Path(path).name} 非 UTF-8 文本"
    except json.JSONDecodeError as exc:
        return None, f"{Path(path).name} JSON 解析失败: {exc}"


def write_json(path: Path, obj) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return True
    except OSError as exc:
        fail_hard(f"write failed {path}: {exc.__class__.__name__}")
        return False


def write_text(path: Path, text: str) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(str(text).encode("utf-8"))
        return True
    except OSError as exc:
        fail_hard(f"write failed {path}: {exc.__class__.__name__}")
        return False


def write_bytes(path: Path, data: bytes) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
        return True
    except OSError as exc:
        fail_hard(f"write failed {path}: {exc.__class__.__name__}")
        return False


def ensure_dir(path: Path):
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def resolve_dirs(args):
    input_root = Path(args.input).expanduser() if getattr(args, "input", None) else DEFAULT_INPUT
    work = Path(args.work).expanduser() if getattr(args, "work", None) else DEFAULT_WORK
    output = Path(args.output).expanduser() if getattr(args, "output", None) else DEFAULT_OUTPUT
    ok, err = ensure_dir(work)
    if not ok:
        work = Path.cwd() / "skill_work"
        ensure_dir(work)
        degrade(f"work dir unusable ({err}), fallback {work}")
    ok, err = ensure_dir(output)
    if not ok:
        output = work / "output_fallback"
        ensure_dir(output)
        degrade(f"output dir unusable ({err}), fallback {output}")
    return input_root, work, output


def load_case(input_root: Path) -> dict:
    case, err = read_json(input_root / "case.json")
    if err:
        fail_hard(f"case.json unavailable: {err}")
    return case if isinstance(case, dict) else {}


def contract_keys(case: dict) -> list:
    raw = case.get("output_contract")
    keys = []
    if isinstance(raw, dict):
        keys = list(raw.keys())
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field"):
                    if isinstance(item.get(cand), str) and item[cand].strip():
                        keys.append(item[cand].strip())
                        break
    if not keys:
        degrade("output_contract missing, fallback to 7 contract fields")
        keys = list(ROOT_FIELDS)
    ordered, seen = [], set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def default_value(key: str):
    if key in LIST_FIELDS:
        return []
    if key in DICT_FIELDS:
        return {}
    return None


def fill_contract(keys: list, payload: dict):
    filled, missing = {}, []
    for key in keys:
        if key in payload:
            filled[key] = payload[key]
        else:
            filled[key] = default_value(key)
            missing.append(key)
    return filled, missing


def classify_materials(input_root: Path, case: dict) -> list:
    materials, seen = [], set()
    if input_root.is_dir():
        number = 0
        for path in sorted(input_root.rglob("*")):
            if not path.is_file() or path.name == "case.json":
                continue
            rel = path.relative_to(input_root).as_posix()
            number += 1
            materials.append({"source_id": f"S{number}", "rel_path": rel,
                              "kind": classify_suffix(path.suffix),
                              "size": path.stat().st_size})
            seen.add(rel)
    # case.json 内联 materials 作为补充来源（示例/无文件挂载时）
    inline = case.get("materials")
    if isinstance(inline, dict):
        number = len(materials)
        for name, value in inline.items():
            rel = f"materials/{name}"
            if rel in seen or not isinstance(name, str):
                continue
            number += 1
            materials.append({"source_id": f"S{number}", "rel_path": rel,
                              "kind": "text", "size": len(str(value)), "inline": True})
    return materials


def classify_suffix(suffix: str) -> str:
    suffix = suffix.lower()
    if suffix == ".pptx":
        return "pptx"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".gif"}:
        return "image"
    if suffix in {".csv", ".tsv", ".xlsx"}:
        return "table"
    return "text"


def build_corpus(input_root: Path, work: Path, materials: list, case: dict) -> dict:
    """聚合可引用的文本：/input 文本、agent 提取到 /app/work/extracted/ 的文本、case.json 内联。"""
    texts = {}
    for material in materials:
        if material.get("inline"):
            val = (case.get("materials") or {}).get(material["rel_path"].split("/", 1)[-1])
            if isinstance(val, str):
                texts[material["source_id"]] = val
            continue
        if material["kind"] != "text":
            continue
        try:
            texts[material["source_id"]] = (input_root / material["rel_path"]).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
    extracted_dir = work / "extracted"
    if extracted_dir.is_dir():
        for path in sorted(extracted_dir.glob("*.txt")):
            try:
                texts[f"X:{path.stem}"] = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                continue
    return {sid: normalize_text(text) for sid, text in texts.items()}


def envelope(materials: list, keys: list, missing: list, extra: dict = None) -> dict:
    extra = extra or {}
    return {
        "sources": materials,
        "uncertainty": list(extra.get("uncertainty") or []),
        "pending_human": list(extra.get("pending_human") or []),
        "errors": list(_STATE["errors"]),
        "degradations": list(_STATE["degradations"]),
        "change_log": list(_STATE["changes"]),
        "runtime": {"skill_version": VERSION, "task": TASK_LABEL,
                    "steps": list(_STATE["steps"]), "contract_keys": list(keys),
                    "contract_missing": list(missing),
                    "elapsed_ms": int((time.time() - _STATE["started"]) * 1000),
                    "generated_at": now_iso()},
    }


def find_template(input_root: Path, materials: list, ir: dict) -> tuple:
    tpl_path = (ir.get("template") or {}).get("path") if isinstance(ir.get("template"), dict) else None
    candidates = []
    if tpl_path:
        candidates += [input_root / tpl_path, Path(tpl_path)]
    for material in materials:
        if material["kind"] == "pptx":
            candidates.append(input_root / material["rel_path"])
    for cand in candidates:
        if cand.is_file() and cand.suffix.lower() == ".pptx":
            return cand.read_bytes(), cand.name
    return None, None


def content_map_from_pages(pages: list) -> list:
    return [{
        "n": p["n"], "title": p["title"], "claim": p["claim"], "role": p["role"],
        "evidence": [{"type": "section", "id": ev["source_id"], "locator": ev["locator"],
                      "quote": ev["quote"], "quote_status": ev["quote_status"]}
                     for ev in p["evidence"]],
        "assets": p["assets"], "notes": p["notes"],
    } for p in pages]


def write_report(output: Path, case_id, ir: dict, pages: list, checks: list,
                 inheritance: dict, substitutions: list, missing: list) -> None:
    lines = [f"# 学术幻灯片交付报告（{case_id}）", ""]
    plan = ir.get("plan") if isinstance(ir.get("plan"), dict) else {}
    lines.append(f"- 模式：{ir.get('mode', 'adapt')}")
    lines.append(f"- 目的/听众/时长：{plan.get('purpose')} / {plan.get('audience')} / {plan.get('duration_min')} 分钟")
    lines.append(f"- 页数：{len(pages)}")
    lines.append("")
    lines.append("## 页级计划")
    lines.append("| n | role | title | claim | evidence |")
    lines.append("|---|---|---|---|---|")
    for p in pages:
        loc = ", ".join(ev["locator"] for ev in p["evidence"])
        lines.append(f"| {p['n']} | {p['role']} | {p['title']} | {p['claim']} | {loc} |")
    lines.append("")
    lines.append("## 合规检查")
    for check in checks:
        lines.append(f"- {check['id']}: {check['verdict']}（{check['measured']}）")
    if inheritance:
        lines.append("")
        lines.append("## 模板继承")
        lines.append(f"- master_unchanged={inheritance.get('master_unchanged')}, "
                     f"theme_unchanged={inheritance.get('theme_unchanged')}")
        lines.append(f"- 版式映射：{inheritance.get('layout_map')}")
    if substitutions:
        lines.append("")
        lines.append("## 版式替代")
        for s in substitutions:
            lines.append(f"- 第 {s['n']} 页：want {s['wanted']} → use {s['used']}（{s['reason']}）")
    if _STATE["degradations"]:
        lines.append("")
        lines.append("## 降级与缺口")
        for item in _STATE["degradations"]:
            lines.append(f"- {item}")
    if missing:
        lines.append("")
        lines.append("## 契约缺键")
        lines.append("- " + ", ".join(missing))
    write_text(output / "report.md", "\n".join(lines) + "\n")


# ------------------------------------------------------------------ 子命令

def cmd_init(args):
    step("init")
    input_root, work, output = resolve_dirs(args)
    case = load_case(input_root)
    keys = contract_keys(case)
    materials = classify_materials(input_root, case)
    case_id = case.get("case_id") or "unknown-case"
    write_json(work / "index.json", {"case_id": case_id, "materials": materials,
                                     "contract_keys": keys, "generated_at": now_iso()})
    write_json(work / "deck.skeleton.json", ir_skeleton(case_id))
    payload = {"case_id": case_id, "status": "partial", "editable_source": {},
               "export_artifact": {}, "content_map": [], "compliance_checks": [], "exceptions": []}
    result, missing = fill_contract(keys, payload)
    env = envelope(materials, keys, missing)
    for key, value in env.items():
        result.setdefault(key, value)
    degrade("init: 骨架已落盘，正文需 agent 读材料后填写 deck.json")
    write_json(output / "result.json", result)
    print(json.dumps({"stage": "init", "case_id": case_id, "materials": len(materials),
                      "contract_keys": keys, "output": str(output)}, ensure_ascii=False))


def cmd_survey(args):
    step("survey")
    input_root, work, _out = resolve_dirs(args)
    case = load_case(input_root)
    materials = classify_materials(input_root, case)
    tools = {name: shutil.which(name) for name in ("soffice", "libreoffice", "pandoc", "python")}
    print(f"case_id: {case.get('case_id')}")
    for material in materials:
        print(f"  [{material['source_id']}] {material['rel_path']} ({material['kind']}, {material['size']}B)")
    templates = [m["rel_path"] for m in materials if m["kind"] == "pptx"]
    print(f"pptx templates: {templates or 'none'}")
    print(f"PATH tools: {[k for k, v in tools.items() if v]}")
    print("workflow: init -> 读材料/写 deck.json -> lint -> compile -> audit -> selfcheck")


def cmd_lint(args):
    step("lint")
    input_root, work, _out = resolve_dirs(args)
    case = load_case(input_root)
    materials = classify_materials(input_root, case)
    corpus = build_corpus(input_root, work, materials, case)
    ir, err = read_json(work / "deck.json")
    if err:
        report = {"checked_at": now_iso(), "items": [{"id": "deck.json", "level": "FAIL", "detail": err}],
                  "fail": 1, "warn": 0}
        write_json(work / "lint_report.json", report)
        print(f"[FAIL] deck.json: {err}")
        return
    pages, warnings = normalize_ir(ir, corpus)
    template_bytes, _ = find_template(input_root, materials, ir)
    items = lint_pages(pages, ir.get("plan") or {}, ir.get("mode", "adapt"), template_bytes is not None)
    for w in warnings:
        items.append({"id": "normalize", "level": "WARN", "detail": w})
    report = {"checked_at": now_iso(), "items": items,
              "fail": sum(1 for i in items if i["level"] == "FAIL"),
              "warn": sum(1 for i in items if i["level"] == "WARN")}
    write_json(work / "lint_report.json", report)
    for item in items:
        print(f"[{item['level']}] {item['id']}: {item['detail']}")
    print(json.dumps({"stage": "lint", "fail": report["fail"], "warn": report["warn"]}, ensure_ascii=False))


def cmd_compile(args):
    step("compile")
    input_root, work, output = resolve_dirs(args)
    case = load_case(input_root)
    keys = contract_keys(case)
    materials = classify_materials(input_root, case)
    corpus = build_corpus(input_root, work, materials, case)
    ir, err = read_json(work / "deck.json")
    case_id = case.get("case_id") or (ir.get("case_id") if isinstance(ir, dict) else "unknown-case")

    if not isinstance(ir, dict):
        degrade(f"deck.json 不可用（{err or '非对象'}），产出兜底 deck")
        ir = ir_skeleton(case_id)
    pages, warnings = normalize_ir(ir, corpus)
    template_bytes, template_name = find_template(input_root, materials, ir)
    items = lint_pages(pages, ir.get("plan") or {}, ir.get("mode", "adapt"), template_bytes is not None)

    inheritance, substitutions, template_info = None, [], None
    mode = ir.get("mode", "adapt") if isinstance(ir, dict) else "adapt"
    if template_bytes:
        template_info = inspect_template(template_bytes)
        if template_info.get("valid"):
            layout_map, substitutions = select_layouts(pages, template_info)
            try:
                pptx_bytes, inheritance = bind_template(template_bytes, pages, layout_map)
            except Exception as exc:  # noqa: BLE001
                degrade(f"bind_template 失败: {exc.__class__.__name__}: {exc}")
                pptx_bytes = None
                inheritance = {"ok": False, "error": str(exc)}
        else:
            degrade("模板 pptx 无法解析，回退内置母版")
            pptx_bytes = None
    else:
        degrade("未找到输入模板 pptx，无法真继承（adapt 模式）")
        pptx_bytes = None

    used_fallback = False
    if pptx_bytes is None:
        try:
            pptx_bytes = build_standalone_deck(pages)
            used_fallback = True
            inheritance = {"ok": False, "reason": "built-in standalone deck, not template-inherited"}
            degrade("无可用模板，回退内置母版（非模板继承，已如实标注）")
        except Exception as exc:  # noqa: BLE001
            degrade(f"内置母版兜底也失败: {exc.__class__.__name__}: {exc}")
            pptx_bytes = None

    pdf_bytes = write_pdf(pages)
    png_bytes = write_png_preview(pages)

    base_layouts = set()
    if template_info and template_info.get("valid"):
        base_layouts = set(template_info.get("layouts") and [l["name"] for l in template_info["layouts"]] or [])
    evidence_total = sum(len(p["evidence"]) for p in pages)
    checks, info, mismatches = audit(pptx_bytes or b"", pdf_bytes, png_bytes, pages,
                                     inheritance, base_layouts, evidence_total)

    if pptx_bytes:
        write_bytes(output / "presentation.pptx", pptx_bytes)
    write_bytes(output / "presentation.pdf", pdf_bytes)
    write_bytes(output / "preview.png", png_bytes)
    write_json(output / "evidence_map.json", {"pages": content_map_from_pages(pages)})
    write_json(output / "template_map.json", {
        "template": {"path": template_name or "", "mode": mode},
        "inheritance": inheritance,
        "substitutions": substitutions,
    })

    exceptions = []
    if used_fallback:
        exceptions.append({"item": "template_inheritance", "reason": "无可用输入模板，使用内置母版兜底",
                           "impact": "未继承外部模板母版/主题，母版 sha256 与输入模板不可比"})
    if pptx_bytes is None:
        exceptions.append({"item": "presentation.pptx", "reason": "模板与内置母版均不可用",
                           "impact": "无可编辑 pptx 产物"})
    if any(c["verdict"] == "fail" for c in checks):
        exceptions.append({"item": "compliance", "reason": "存在 fail 项",
                           "impact": "见 compliance_checks"})
    for w in warnings:
        exceptions.append({"item": "normalize", "reason": w, "impact": "IR 规范化提示"})
    exceptions.append({"item": "png_preview", "reason": "stdlib 结构预览，非字体级渲染",
                       "impact": "权威渲染请用 office-renderer 生成"})
    exceptions.append({"item": "speaker_notes", "reason": "讲稿写入 content_map.notes 与 report.md，未嵌入 notesSlide",
                       "impact": "备注以外部文件形式保留"})

    editable_source = {"path": "presentation.pptx", "format": "pptx",
                       "slides": info.get("slides"), "mode": mode,
                       "template": {"path": template_name or "",
                                    "masters": info.get("masters") or [],
                                    "layouts_used": [l["layout"] for l in (inheritance or {}).get("layout_map", [])] if inheritance else []},
                       "sha256": sha256(pptx_bytes) if pptx_bytes else None}
    export_artifact = {
        "pdf": {"path": "presentation.pdf", "pages": pdf_page_count(pdf_bytes), "engine": "stdlib-text-fallback"},
        "png": {"path": "preview.png", "count": 1, "engine": "stdlib-structural-preview"},
    }
    fail_count = sum(1 for i in items if i["level"] == "FAIL")
    if pptx_bytes is None:
        status = "partial"
    elif not corpus:
        status = "insufficient_evidence"
    elif fail_count == 0 and all(c["verdict"] != "fail" for c in checks):
        status = "ok"
    else:
        status = "partial"
    payload = {
        "case_id": case_id, "status": status,
        "editable_source": editable_source, "export_artifact": export_artifact,
        "content_map": content_map_from_pages(pages),
        "compliance_checks": checks, "exceptions": exceptions,
    }
    result, missing = fill_contract(keys, payload)
    if missing:
        degrade("contract keys missing: " + ", ".join(missing))
    env = envelope(materials, keys, missing, extra={
        "uncertainty": (ir.get("uncertainties") if isinstance(ir, dict) else None) or [],
        "pending_human": (ir.get("pending_human") if isinstance(ir, dict) else None) or [],
    })
    for key, value in env.items():
        result.setdefault(key, value)
    result["coverage"] = {"slides": len(pages), "content_map": len(pages),
                          "compliance_checks": len(checks),
                          "evidence_verbatim": sum(1 for p in pages for ev in p["evidence"]
                                                   if ev["quote_status"] == "verbatim"),
                          "evidence_total": evidence_total}
    write_report(output, case_id, ir if isinstance(ir, dict) else {}, pages, checks,
                 inheritance, substitutions, missing)
    write_json(output / "result.json", result)
    print(json.dumps({"stage": "compile", "status": status, "mode": mode, "slides": len(pages),
                      "contract_missing": missing, "output": str(output),
                      "files": sorted(p.name for p in output.iterdir() if p.is_file())},
                     ensure_ascii=False))


def cmd_audit(args):
    step("audit")
    input_root, work, output = resolve_dirs(args)
    case = load_case(input_root)
    materials = classify_materials(input_root, case)
    corpus = build_corpus(input_root, work, materials, case)
    ir, err = read_json(work / "deck.json")
    pages, _w = normalize_ir(ir if isinstance(ir, dict) else {}, corpus)
    pptx_path = output / "presentation.pptx"
    pptx_bytes = pptx_path.read_bytes() if pptx_path.is_file() else b""
    pdf_bytes = (output / "presentation.pdf").read_bytes() if (output / "presentation.pdf").is_file() else b""
    png_bytes = (output / "preview.png").read_bytes() if (output / "preview.png").is_file() else b""
    template_bytes, _name = find_template(input_root, materials, ir if isinstance(ir, dict) else {})
    template_info = inspect_template(template_bytes) if template_bytes else {"valid": False}
    base_layouts = set()
    if template_info.get("valid"):
        base_layouts = set(l["name"] for l in template_info["layouts"])
    inheritance = {"ok": True, "master_unchanged": None, "theme_unchanged": None, "layout_map": []}
    if pptx_bytes and template_bytes:
        info = deck_info(pptx_bytes)
        tpl_info = inspect_template(template_bytes)
        inheritance["master_unchanged"] = info.get("master_sha256") == tpl_info.get("master_sha256")
        inheritance["theme_unchanged"] = info.get("theme_sha256") == tpl_info.get("theme_sha256")
    checks, _info, mismatches = audit(pptx_bytes, pdf_bytes, png_bytes, pages, inheritance,
                                      base_layouts, sum(len(p["evidence"]) for p in pages))
    report = {"checked_at": now_iso(), "checks": checks, "round_trip_mismatches": mismatches}
    write_json(work / "audit.json", report)
    write_json(output / "audit.json", report)
    for check in checks:
        print(f"[{check['verdict'].upper()}] {check['id']}: {check['measured']}")
    print(json.dumps({"stage": "audit", "pass": sum(1 for c in checks if c["verdict"] == "pass"),
                      "warn": sum(1 for c in checks if c["verdict"] == "warn"),
                      "fail": sum(1 for c in checks if c["verdict"] == "fail")}, ensure_ascii=False))


def cmd_selfcheck(args):
    step("selfcheck")
    input_root, work, output = resolve_dirs(args)
    case = load_case(input_root)
    keys = contract_keys(case)
    result, err = read_json(output / "result.json")
    items = []

    def add(level, check_id, detail):
        items.append({"level": level, "id": check_id, "detail": detail})

    if err:
        add("FAIL", "result.json", err)
    elif not isinstance(result, dict):
        add("FAIL", "result.json", "not a JSON object")
    else:
        missing = [key for key in keys if key not in result]
        add("PASS" if not missing else "FAIL", "contract.keys",
            "all contract keys present" if not missing else f"missing: {missing}")
        add("PASS" if result.get("case_id") == case.get("case_id") else "WARN", "case_id",
            f"result.case_id={result.get('case_id')!r}")
        editable = result.get("editable_source") or {}
        path = output / str(editable.get("path", "presentation.pptx"))
        add("PASS" if path.is_file() and path.stat().st_size > 0 else "FAIL", "editable_source",
            f"{path.name} present" if path.is_file() else "pptx missing")
        pdf = (result.get("export_artifact") or {}).get("pdf") or {}
        pdf_path = output / str(pdf.get("path", "presentation.pdf"))
        add("PASS" if pdf_path.is_file() and pdf_path.read_bytes()[:5] == b"%PDF-" else "FAIL",
            "export.pdf", f"{pdf_path.name} present" if pdf_path.is_file() else "pdf missing")
        add("PASS" if result.get("status") in STATUS else "FAIL", "status", f"status={result.get('status')!r}")
        for env_field in ("sources", "uncertainty", "pending_human", "change_log"):
            add("PASS" if env_field in result else "WARN", f"envelope.{env_field}",
                f"{env_field} present" if env_field in result else "missing")
    report = {"checked_at": now_iso(), "items": items,
              "pass": sum(1 for i in items if i["level"] == "PASS"),
              "warn": sum(1 for i in items if i["level"] == "WARN"),
              "fail": sum(1 for i in items if i["level"] == "FAIL")}
    write_json(work / "selfcheck.json", report)
    try:
        write_json(output / "selfcheck.json", report)
    except Exception:
        pass
    for item in items:
        print(f"[{item['level']}] {item['id']}: {item['detail']}")
    print(json.dumps({"stage": "selfcheck", "pass": report["pass"], "warn": report["warn"],
                      "fail": report["fail"]}, ensure_ascii=False))


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py",
                                     description=f"{TASK_LABEL}：IR → 真继承 pptx → PDF/PNG → 回读审计。")
    sub = parser.add_subparsers(dest="cmd", metavar="{init,survey,lint,compile,audit,selfcheck}")
    for name, text in (("init", "落 partial 骨架 + 索引"), ("survey", "清点材料/工具"),
                       ("lint", "IR 静态校验"), ("compile", "编译产出 pptx/pdf/png/result"),
                       ("audit", "回读产物 round-trip"), ("selfcheck", "终检契约")):
        instance = sub.add_parser(name, help=text)
        instance.add_argument("--input", default=None)
        instance.add_argument("--work", default=None)
        instance.add_argument("--output", default=None)
    return parser


def emergency_finalize(args, tb: str):
    try:
        _input, work, output = resolve_dirs(args)
        result = {"case_id": "unknown-case", "status": "input_error", "editable_source": {},
                  "export_artifact": {}, "content_map": [], "compliance_checks": [],
                  "exceptions": [{"item": "emergency",
                                  "reason": tb.strip().splitlines()[-1] if tb.strip() else "unknown",
                                  "impact": "见 errors"}],
                  "sources": [], "uncertainty": [], "pending_human": [],
                  "errors": list(_STATE["errors"]), "degradations": list(_STATE["degradations"]),
                  "change_log": list(_STATE["changes"]),
                  "runtime": {"skill_version": VERSION, "emergency": True}}
        write_json(output / "result.json", result)
        write_json(work / "emergency.json", result)
    except BaseException:
        pass
    print(json.dumps({"stage": "emergency", "status": "input_error",
                      "error": tb.strip().splitlines()[-1] if tb.strip() else "unknown"},
                     ensure_ascii=False))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args, _unknown = parser.parse_known_args(argv)
    except SystemExit:
        return 0
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    handlers = {"init": cmd_init, "survey": cmd_survey, "lint": cmd_lint,
                "compile": cmd_compile, "audit": cmd_audit, "selfcheck": cmd_selfcheck}
    try:
        handlers[args.cmd](args)
    except BaseException:
        emergency_finalize(args, traceback.format_exc())
    return 0


if __name__ == "__main__":
    sys.exit(main())
