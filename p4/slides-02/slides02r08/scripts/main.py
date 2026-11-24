#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-deck-architect 入口：页级计划 → 真 pptx → 一致 PDF → 七字段契约 + 证据映射。

子命令（均返回 0，任何异常走 emergency 兜底）：
  scaffold  读 case.json/task.md，3 分钟内落盘 partial 骨架 + work 索引
  survey    盘点材料 / 工具 / 流程
  audit     校验 work/draft.json（结构 + 逐字引文命中）
  build     生成 presentation.pptx / presentation.pdf + result.json + evidence_map.json
  finalize  终检：写 selfcheck.json，按缺口设 status
目录可用 --input/--work/--output 覆盖，默认 /input、/app/work、/app/output。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core_impl import (  # noqa: E402
    MODES, ROLES, assemble_deck, audit_draft, build_report, compliance_checks,
    content_map_from_slides, dimension_map, draft_skeleton, inherit_deck,
    inspect_deck, patch_deck, pdf_page_count, quote_hit, render_pdf_text,
    sha256, slides_from_draft,
)

VERSION = "1.0.0"
TASK_LABEL = "学术 PPT 生成与编辑 (slides-02)"
DEFAULT_MODE = "edit"

ROOT_FIELDS = ["case_id", "status", "editable_source", "export_artifact",
               "content_map", "compliance_checks", "exceptions"]
LIST_FIELDS = {"content_map", "compliance_checks", "exceptions", "sources",
               "uncertainty", "pending_human", "errors", "degradations",
               "change_log", "changes"}
DICT_FIELDS = {"editable_source", "export_artifact", "runtime", "result",
               "dimension_map"}
STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_STATE = {"errors": [], "degradations": [], "change_log": [], "steps": [],
          "rework": {"pages_reworked": 0, "reorders": 0, "export_rechecks": 0},
          "started": time.time()}


def load_persisted(work: Path):
    obj, err = read_json(work / "state.json")
    if err or not isinstance(obj, dict):
        return
    logs = obj.get("change_log")
    if isinstance(logs, list):
        _STATE["change_log"] = logs
    rework = obj.get("rework")
    if isinstance(rework, dict):
        _STATE["rework"].update(rework)


def persist_state(work: Path):
    write_json(work / "state.json",
               {"change_log": _STATE["change_log"], "rework": _STATE["rework"]})


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log_step(name: str):
    _STATE["steps"].append(name)


def record(action: str, step: str = "", reason: str = "", status: str = "ok"):
    _STATE["change_log"].append({
        "t": now_iso(), "step": step or _STATE["steps"][-1] if _STATE["steps"] else "",
        "action": action, "reason": reason, "status": status})


def degrade(item: str):
    _STATE["degradations"].append(item)


def fail_hard(item: str):
    _STATE["errors"].append(item)


def read_json(path: Path):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return None, f"{Path(path).name} 读取失败: {exc.__class__.__name__}"
    if not raw.strip():
        return None, f"{Path(path).name} 为空文件"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, f"{Path(path).name} 非 UTF-8 文本"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{Path(path).name} JSON 解析失败: {exc}"


def write_json(path: Path, obj) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(
            (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
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
    input_root = Path(args.input).expanduser() if getattr(args, "input", None) else Path("/input")
    work = Path(args.work).expanduser() if getattr(args, "work", None) else Path("/app/work")
    wanted = Path(args.output).expanduser() if getattr(args, "output", None) else Path("/app/output")
    ok, err = ensure_dir(work)
    if not ok:
        fallback = Path.cwd() / "skill_work"
        if ensure_dir(fallback)[0]:
            degrade(f"work dir {work} 不可用({err})，回退 {fallback}")
            work = fallback
    output = wanted
    ok, err = ensure_dir(output)
    if not ok:
        for candidate in (work / "output_fallback", Path.cwd() / "skill_output"):
            if ensure_dir(candidate)[0]:
                degrade(f"output dir {wanted} 不可用({err})，回退 {candidate}")
                output = candidate
                break
    return input_root, work, output


def load_case(input_root: Path) -> dict:
    case, err = read_json(input_root / "case.json")
    if err:
        fail_hard(f"case.json 不可用: {err}")
    return case if isinstance(case, dict) else {}


def contract_keys(case: dict) -> list:
    raw = case.get("output_contract")
    keys = []
    if isinstance(raw, dict):
        keys = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                for cand in ("name", "key", "field", "field_id"):
                    if isinstance(item.get(cand), str) and item[cand].strip():
                        keys.append(item[cand].strip())
                        break
    if not keys:
        degrade("output_contract 缺失，回退 list 七字段")
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


def fill_contract(keys, payload):
    filled, missing = {}, []
    for key in keys:
        if key in payload:
            filled[key] = payload[key]
        else:
            filled[key] = default_value(key)
            missing.append(key)
    return filled, missing


def classify_materials(input_root: Path) -> list:
    materials = []
    if not input_root.is_dir():
        return materials
    number = 0
    for path in sorted(input_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_root).as_posix()
        if rel in {"case.json", "task.md"}:
            continue
        number += 1
        suffix = path.suffix.lower()
        kind = ("pptx" if suffix == ".pptx" else "pdf" if suffix == ".pdf"
                else "image" if suffix in {".png", ".jpg", ".jpeg"} else
                "table" if suffix in {".csv", ".tsv", ".xlsx"} else "text")
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        materials.append({"source_id": f"S{number}", "rel_path": rel, "kind": kind,
                          "size": size})
    return materials


def build_corpus(input_root: Path, materials: list) -> dict:
    corpus = {}
    for material in materials:
        if material["kind"] != "text":
            continue
        try:
            text = (input_root / material["rel_path"]).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        corpus[material["source_id"]] = re.sub(r"\s+", "", text).lower()
    return corpus


def build_deck(draft, materials, input_root: Path):
    mode = draft.get("mode", DEFAULT_MODE) if isinstance(draft, dict) else DEFAULT_MODE
    slides = slides_from_draft(draft if isinstance(draft, dict) else {})
    template = (draft or {}).get("template") or {}
    template_bytes = None
    template_path = template.get("path")
    if template_path:
        for candidate in (input_root / template_path, Path(template_path)):
            if candidate.is_file() and candidate.suffix.lower() == ".pptx":
                template_bytes = candidate.read_bytes()
                break
    if template_bytes is None:
        for material in materials:
            if material["kind"] == "pptx":
                template_bytes = (input_root / material["rel_path"]).read_bytes()
                template["path"] = material["rel_path"]
                break
    inheritance = None
    if template_bytes and mode in {"adapt", "edit"}:
        if mode == "edit":
            edits = draft.get("edits") if isinstance(draft.get("edits"), list) else []
            outcome = patch_deck(template_bytes, edits)
            if outcome.get("ok"):
                pptx = outcome["pptx"]
                inheritance = {"ok": True, "layout_used": "existing",
                               "master_sha256": inspect_deck(template_bytes).get("master_sha256"),
                               "changed": outcome.get("changed", [])}
            else:
                degrade("patch_deck 未命中目标文本，回退 inherit 模式")
                outcome = inherit_deck(template_bytes, slides,
                                       (template.get("layouts_used") or [None])[0],
                                       clean_slides=True)
                inheritance = outcome
                pptx = outcome["pptx"]
        else:
            outcome = inherit_deck(template_bytes, slides,
                                   (template.get("layouts_used") or [None])[0],
                                   clean_slides=True)
            inheritance = outcome
            pptx = outcome.get("pptx")
        if not pptx:
            degrade("模板继承失败，回退内置母版生成")
            pptx = assemble_deck(slides)
    else:
        pptx = assemble_deck(slides)
        mode = "create"
    if isinstance(inheritance, dict) and inheritance.get("master_sha256") and template_bytes:
        base_master = inspect_deck(template_bytes).get("master_sha256") or {}
        inherited_master = inheritance["master_sha256"]
        inheritance["master_unchanged"] = all(
            base_master.get(name) == value for name, value in inherited_master.items())
        if not inheritance["master_unchanged"]:
            degrade("模板母版 sha256 发生变化，可能非真继承")
    return pptx, slides, mode, template, inheritance


def envelope(index: dict, keys: list, missing: list) -> dict:
    return {
        "sources": index.get("materials", []),
        "uncertainty": [],
        "pending_human": [],
        "errors": list(_STATE["errors"]),
        "degradations": list(_STATE["degradations"]),
        "change_log": list(_STATE["change_log"]),
        "runtime": {
            "skill_version": VERSION, "task": TASK_LABEL,
            "steps": list(_STATE["steps"]),
            "contract_keys": list(keys), "contract_missing": list(missing),
            "elapsed_ms": int((time.time() - _STATE["started"]) * 1000),
            "generated_at": now_iso(),
        },
    }


def base_index(input_root: Path, case: dict) -> dict:
    materials = classify_materials(input_root)
    tools = {"which": {}}
    for name in ("soffice", "libreoffice", "pandoc", "python", "python3"):
        found = shutil.which(name)
        if found:
            tools["which"][name] = found
    return {"case_id": case.get("case_id"), "task_instruction": case.get("task_instruction"),
            "input_root": str(input_root), "materials": materials, "tools": tools,
            "generated_at": now_iso()}


def cmd_scaffold(args):
    log_step("scaffold")
    input_root, work, output = resolve_dirs(args)
    load_persisted(work)
    case = load_case(input_root)
    keys = contract_keys(case)
    index = base_index(input_root, case)
    write_json(work / "index.json", index)
    write_json(work / "draft.skeleton.json", draft_skeleton())
    payload = {"case_id": case.get("case_id") or "unknown-case", "status": "partial",
               "editable_source": {}, "export_artifact": {}, "content_map": [],
               "compliance_checks": [], "exceptions": []}
    result, missing = fill_contract(keys, payload)
    env = envelope(index, keys, missing)
    for key, value in env.items():
        if key not in result:
            result[key] = value
    record("scaffold 落 partial 骨架", reason="前 3 分钟先落盘，之后只做增量", status="partial")
    degrade("scaffold 仅占位字段，内容需 Agent 读取材料后回填")
    result["change_log"] = list(_STATE["change_log"])
    result["degradations"] = list(_STATE["degradations"])
    write_json(output / "result.json", result)
    persist_state(work)
    print(json.dumps({"stage": "scaffold", "case_id": result.get("case_id"),
                      "materials": len(index["materials"]), "contract_keys": keys,
                      "mode_default": DEFAULT_MODE, "output": str(output)},
                     ensure_ascii=False))


def cmd_survey(args):
    log_step("survey")
    input_root, work, _output = resolve_dirs(args)
    load_persisted(work)
    case = load_case(input_root)
    index = base_index(input_root, case)
    write_json(work / "index.json", index)
    persist_state(work)
    print(f"case_id: {index.get('case_id')}")
    print(f"mode default: {DEFAULT_MODE}")
    for material in index["materials"]:
        print(f"  [{material['source_id']}] {material['rel_path']} ({material['kind']}, {material['size']}B)")
    templates = [m["rel_path"] for m in index["materials"] if m["kind"] == "pptx"]
    print(f"pptx templates: {templates or 'none'}")
    print(f"PATH tools: {sorted((index.get('tools') or {}).get('which', {}))}")
    print("workflow: scaffold -> survey -> (Agent 填 draft.json) -> audit -> build -> finalize")


def cmd_audit(args):
    log_step("audit")
    input_root, work, _output = resolve_dirs(args)
    load_persisted(work)
    case = load_case(input_root)
    materials = classify_materials(input_root)
    corpus = build_corpus(input_root, materials)
    draft, err = read_json(work / "draft.json")
    report = {"checked_at": now_iso(), "items": [], "fail": 0, "warn": 0}
    if err:
        report["items"].append({"id": "draft", "level": "FAIL", "detail": err})
        report["fail"] = 1
    else:
        fail, warn, items = audit_draft(draft, corpus, materials)
        report.update({"items": items, "fail": fail, "warn": warn})
    write_json(work / "audit_report.json", report)
    record(f"audit 校验草稿：{report['fail']} FAIL / {report['warn']} WARN",
           reason="结构 + 逐字引文命中", status="ok" if report["fail"] == 0 else "partial")
    persist_state(work)
    for item in report["items"]:
        print(f"[{item['level']}] {item['id']}: {item['detail']}")
    print(json.dumps({"stage": "audit", "fail": report["fail"], "warn": report["warn"]},
                     ensure_ascii=False))


def cmd_build(args):
    log_step("build")
    input_root, work, output = resolve_dirs(args)
    load_persisted(work)
    case = load_case(input_root)
    keys = contract_keys(case)
    materials = classify_materials(input_root)
    corpus = build_corpus(input_root, materials)
    index = base_index(input_root, case)
    draft, err = read_json(work / "draft.json")
    fail_count, warn_count = 1, 0
    if not isinstance(draft, dict):
        degrade(f"draft 不可用({err or '非对象'})，生成兜底 deck")
        draft = {}
    else:
        fail_count, warn_count, items = audit_draft(draft, corpus, materials)
    try:
        pptx_bytes, slides, mode, template, inheritance = build_deck(draft, materials, input_root)
    except Exception as exc:  # noqa: BLE001
        degrade(f"pptx build 失败: {exc.__class__.__name__}: {exc}")
        slides = slides_from_draft(draft) or [{"title": "Fallback", "bullets": [], "role": "other"}]
        pptx_bytes = assemble_deck(slides)
        mode, template, inheritance = "create", {}, None
    write_bytes(output / "presentation.pptx", pptx_bytes)
    pdf_bytes = render_pdf_text(slides)
    write_bytes(output / "presentation.pdf", pdf_bytes)
    checks, info = compliance_checks(pptx_bytes, pdf_bytes, slides, inheritance, mode)
    content_map = content_map_from_slides(slides, corpus)
    editable_source = {
        "path": "presentation.pptx", "format": "pptx", "slides": info.get("slides"),
        "mode": mode,
        "template": {"path": template.get("path", ""),
                     "master": (info.get("masters") or [""])[0],
                     "layouts_used": template.get("layouts_used") or []},
        "sha256": sha256(pptx_bytes),
    }
    export_artifact = {"pdf": {"path": "presentation.pdf", "pages": pdf_page_count(pdf_bytes),
                               "engine": "stdlib-text-fallback"}}
    payload = {
        "case_id": case.get("case_id") or "unknown-case",
        "status": "ok" if fail_count == 0 and all(c["verdict"] == "pass" for c in checks) else "partial",
        "editable_source": editable_source,
        "export_artifact": export_artifact,
        "content_map": content_map,
        "compliance_checks": checks,
        "exceptions": [] if info.get("valid") else [
            {"item": "pptx 结构校验未通过", "reason": "zip/OOXML 解析异常",
             "impact": "演示文稿可能无法打开"}],
        "result": {"plan": (draft or {}).get("plan") or {},
                   "slides": slides, "inheritance": inheritance,
                   "deck_info": {k: info.get(k) for k in ("slides", "layouts", "masters", "themes")}},
    }
    result, missing = fill_contract(keys, payload)
    if missing:
        degrade("契约缺键: " + ", ".join(missing))
    env = envelope(index, keys, missing)
    for key, value in env.items():
        if key not in result:
            result[key] = value
    if result.get("status") not in STATUS:
        result["status"] = "partial"
    result["coverage"] = {"slides": len(slides), "content_map": len(content_map),
                          "compliance_checks": len(checks)}
    result["result"] = payload.get("result")
    write_json(output / "evidence_map.json",
               {"slides": content_map, "standard": "每页证据带 locator+逐字 quote+quote_verified"})
    if mode == "edit":
        write_json(output / "changes.json", {
            "edits": (draft or {}).get("edits") or [],
            "keep_unchanged_slides": (draft or {}).get("keep_unchanged_slides") or [],
            "changed": inheritance.get("changed", []) if isinstance(inheritance, dict) else [],
        })
    write_text(output / "report.md", build_report(result.get("case_id"), draft, slides, checks,
                                                  inheritance, missing, mode,
                                                  _STATE["degradations"]))
    record(f"build 生成 {len(slides)} 页 {mode} deck",
           reason=f"pptx+pdf+evidence_map，checks={len(checks)}",
           status=result.get("status"))
    result["change_log"] = list(_STATE["change_log"])
    result["degradations"] = list(_STATE["degradations"])
    result["errors"] = list(_STATE["errors"])
    write_json(output / "result.json", result)
    persist_state(work)
    print(json.dumps({"stage": "build", "status": result.get("status"), "mode": mode,
                      "slides": len(slides), "contract_keys": keys, "contract_missing": missing,
                      "files": sorted(p.name for p in output.rglob("*") if p.is_file()),
                      "output": str(output)}, ensure_ascii=False))


def cmd_finalize(args):
    log_step("finalize")
    input_root, work, output = resolve_dirs(args)
    load_persisted(work)
    case = load_case(input_root)
    keys = contract_keys(case)
    result, err = read_json(output / "result.json")
    items = []

    def add(level, check_id, dimension, detail):
        items.append({"level": level, "id": check_id, "dimension": dimension, "detail": detail})

    if err or not isinstance(result, dict):
        add("FAIL", "result.json", "effect", err or "not an object")
    else:
        missing = [k for k in keys if k not in result]
        add("PASS" if not missing else "FAIL", "contract.keys", "effect",
            "全部契约键存在" if not missing else f"缺失: {missing}")
        add("PASS" if result.get("case_id") == case.get("case_id") else "WARN", "case_id",
            "effect", f"result.case_id={result.get('case_id')!r}")
        editable = result.get("editable_source") or {}
        pptx_path = output / str(editable.get("path", "presentation.pptx"))
        add("PASS" if pptx_path.is_file() and pptx_path.stat().st_size > 0 else "FAIL",
            "editable_source", "effect",
            f"{pptx_path.name} 存在" if pptx_path.is_file() else "pptx 缺失")
        pdf = (result.get("export_artifact") or {}).get("pdf") or {}
        pdf_path = output / str(pdf.get("path", "presentation.pdf"))
        add("PASS" if pdf_path.is_file() and pdf_path.read_bytes()[:5] == b"%PDF-" else "FAIL",
            "export.pdf", "effect",
            f"{pdf_path.name} 存在" if pdf_path.is_file() else "pdf 缺失")
        add("PASS" if result.get("status") in STATUS else "FAIL", "status", "effect",
            f"status={result.get('status')!r}")
        cm = result.get("content_map") or []
        add("PASS" if all(x.get("claim") and x.get("evidence") for x in cm) else "WARN",
            "evidence.map", "effect", f"{len(cm)} 页 content_map")
        logs = result.get("change_log") or []
        add("PASS" if logs else "WARN", "change_log", "benefit",
            f"{len(logs)} 条变更记录（效率/返工证据）")
    report = {"checked_at": now_iso(), "items": items,
              "pass": sum(1 for i in items if i["level"] == "PASS"),
              "warn": sum(1 for i in items if i["level"] == "WARN"),
              "fail": sum(1 for i in items if i["level"] == "FAIL")}
    write_json(work / "selfcheck.json", report)
    write_json(output / "selfcheck.json", report)
    write_json(output / "dimension_map.json", dimension_map(report, _STATE["rework"]))
    record(f"finalize 终检：{report['pass']} PASS / {report['warn']} WARN / {report['fail']} FAIL",
           reason="selfcheck + dimension_map", status="ok" if report["fail"] == 0 else "partial")
    if isinstance(result, dict):
        result["change_log"] = list(_STATE["change_log"])
        write_json(output / "result.json", result)
    persist_state(work)
    for item in items:
        print(f"[{item['level']}] {item['id']}: {item['detail']}")
    print(json.dumps({"stage": "finalize", "pass": report["pass"], "warn": report["warn"],
                      "fail": report["fail"]}, ensure_ascii=False))


def emergency_finalize(args, tb: str):
    output = Path.cwd()
    try:
        _input, work, output = resolve_dirs(args)
        result = {"case_id": "unknown-case", "status": "input_error", "editable_source": {},
                  "export_artifact": {}, "content_map": [], "compliance_checks": [],
                  "exceptions": [{"item": "emergency",
                                  "reason": tb.strip().splitlines()[-1] if tb.strip() else "unknown",
                                  "impact": "见 errors"}],
                  "sources": [], "uncertainty": [], "pending_human": [],
                  "errors": list(_STATE["errors"]),
                  "degradations": list(_STATE["degradations"]),
                  "change_log": list(_STATE["change_log"]),
                  "runtime": {"skill_version": VERSION, "emergency": True}}
        write_bytes(output / "presentation.pptx",
                    assemble_deck([{"title": "Emergency", "bullets": [], "role": "other"}]))
        write_bytes(output / "presentation.pdf",
                    render_pdf_text([{"title": "Emergency", "bullets": []}]))
        write_json(work / "emergency_result.json", result)
        write_json(output / "result.json", result)
    except BaseException:
        pass
    print(json.dumps({"stage": "emergency", "status": "input_error",
                      "error": tb.strip().splitlines()[-1] if tb.strip() else "unknown"},
                     ensure_ascii=False))


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py",
                                     description=f"{TASK_LABEL}：真 pptx + 一致 PDF + 七字段契约 + 证据映射。")
    sub = parser.add_subparsers(dest="cmd", metavar="{scaffold,survey,audit,build,finalize}")

    def common(instance):
        instance.add_argument("--input", default=None)
        instance.add_argument("--work", default=None)
        instance.add_argument("--output", default=None)

    for name, text in (("scaffold", "落 partial 骨架 + 索引"), ("survey", "材料/工具"),
                       ("audit", "校验 draft.json"), ("build", "pptx+pdf+result"),
                       ("finalize", "终检 + selfcheck")):
        common(sub.add_parser(name, help=text))
    return parser


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
    handlers = {"scaffold": cmd_scaffold, "survey": cmd_survey, "audit": cmd_audit,
                "build": cmd_build, "finalize": cmd_finalize}
    try:
        handlers[args.cmd](args)
    except BaseException:
        emergency_finalize(args, traceback.format_exc())
    return 0


if __name__ == "__main__":
    sys.exit(main())
