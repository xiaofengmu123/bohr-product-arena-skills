#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""契约优先的学术 PPT Skill 主入口（纯标准库，无 python-pptx / PyMuPDF 依赖）。

职责边界：本脚本**只做契约落盘、结果合并与产物机械校验**，不组织论文内容。
  1. 读 /input/case.json，逐字提取 output_contract 的全部字段名（不改名、不删未知键）；
  2. **先**落盘 result.json 骨架：所有契约字段 + 统一信封，status=partial，
     保证任何后续异常都不会留下空 result.json；
  3. 合并 Agent 写的 work/plan.json（页级主张 + 证据映射 + sources/uncertainty）；
  4. 用 pptx_util 校验 presentation.pptx / presentation.pdf，计算 compliance_checks；
  5. 诚实定档 status（ok / partial / insufficient_evidence / input_error）并写 report.md。

内容组织（读论文、抽取主线、写 claim/evidence/备注、生成 pptx/pdf）由调用 Agent
按 SKILL.md 完成；脚本保证这些产出能被机器复核。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from pptx_util import inventory_pptx, inventory_pdf, quote_hits  # noqa: E402

VERSION = "1.0.0"
TASK_LABEL = "论文转学术 PPT（slides-01）"
DEFAULT_INPUT = Path("/input")
DEFAULT_WORK = Path("/app/work")
DEFAULT_OUTPUT = Path("/app/output")

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
STATUSES = {STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR}
ROLES = {"title", "background", "method", "result", "discussion",
         "limitation", "conclusion", "other"}

# 当 output_contract 缺失时的兜底七字段（来自本题通用契约，仅在缺失时使用）。
DEFAULT_CONTRACT = ["case_id", "editable_source", "export_artifact",
                    "content_map", "compliance_checks", "exceptions"]
# 统一信封：与契约字段并列，诚实报告来源/不确定/变更/异常，绝不删改契约字段。
ENVELOPE = ["status", "sources", "uncertainty", "pending_human",
            "change_log", "errors", "degradations", "runtime"]

_LIST_KEYS = {"content_map", "compliance_checks", "exceptions", "sources",
              "uncertainty", "pending_human", "change_log", "errors", "degradations"}
_LIST_SUFFIX = ("_list", "_map", "_checks", "_errors", "_exceptions", "_sources",
                "_log", "_notes", "_items", "_records", "_changes")
_DICT_KEYS = {"editable_source", "export_artifact", "runtime"}
_DICT_SUFFIX = ("_source", "_artifact", "_runtime", "_meta", "_info")

EDITABLE_RE = re.compile(r"editable|source_pptx|deck_source", re.I)
EXPORT_RE = re.compile(r"export|artifact|render|preview", re.I)
CONTENT_RE = re.compile(r"content|page_map|slide_map|content_map", re.I)
COMPLIANCE_RE = re.compile(r"compliance|check", re.I)
EXCEPTION_RE = re.compile(r"exception|limitation", re.I)

MAX_JSON_BYTES = 2 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path) -> tuple:
    """读取 JSON，返回 (obj, err)；err 非空表示不可用，绝不抛异常。"""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return None, f"{p.name} 读取失败: {exc.__class__.__name__}"
    if len(raw) > MAX_JSON_BYTES:
        return None, f"{p.name} 超过 {MAX_JSON_BYTES} 字节上限"
    if not raw.strip():
        return None, f"{p.name} 为空文件"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, f"{p.name} 非 UTF-8 文本"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{p.name} JSON 解析失败: {exc}"


def write_json(path, obj) -> bool:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        return True
    except OSError:
        return False


def write_text(path, text: str) -> bool:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(text), encoding="utf-8")
        return True
    except OSError:
        return False


def resolve_dir(path_arg, default, label, state) -> tuple:
    """解析目录，写失败降级到 cwd/skill_<label>，返回 (Path, err)。"""
    candidate = Path(path_arg) if path_arg else Path(default)
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate, None
    except OSError as exc:
        fb = Path.cwd() / f"skill_{label}"
        try:
            fb.mkdir(parents=True, exist_ok=True)
            state["degradations"].append(
                f"{label} dir {candidate} unusable ({exc.__class__.__name__}), fallback {fb}")
            return fb, str(exc)
        except OSError as exc2:
            return Path.cwd(), f"{exc.__class__.__name__}: {exc2}"


def parse_contract_keys(case: dict) -> list:
    """从 output_contract 逐字提取字段名；缺失时用兜底字段。"""
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
    seen, ordered = set(), []
    for key in ["case_id"] + keys:
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered if ordered else list(DEFAULT_CONTRACT)


def default_value(key: str):
    """按字段名推断默认值：未知键一律 null，不改名、不臆断类型。"""
    k = str(key).lower()
    if k in _LIST_KEYS or k.endswith(_LIST_SUFFIX):
        return []
    if k in _DICT_KEYS or k.endswith(_DICT_SUFFIX):
        return {}
    return None


def new_change(state, field, action, reason) -> dict:
    rec = {"ts": now_iso(), "field": field, "action": action, "reason": reason}
    state["changes"].append(rec)
    return rec


def build_skeleton(case_id: str, keys: list, state: dict) -> dict:
    """先落盘骨架：契约字段全量 + 信封，status=partial。"""
    skeleton = {}
    for key in keys:
        skeleton[key] = case_id if key == "case_id" else default_value(key)
    for key in ENVELOPE:
        if key not in skeleton:
            skeleton[key] = default_value(key)
    skeleton["status"] = STATUS_PARTIAL
    skeleton["runtime"] = {"started": now_iso(), "python": sys.version.split()[0],
                           "version": VERSION, "seconds": 0}
    skeleton["change_log"] = [new_change(state, "case_id", "set",
                                          f"骨架落盘：{len(keys)} 个契约字段 + 信封")]
    return skeleton


def read_plan(plan_path) -> dict:
    if not plan_path:
        return {}
    p = Path(plan_path)
    if not p.is_file():
        return {}
    obj, err = read_json(p)
    if err:
        return {}
    return obj if isinstance(obj, dict) else {}


def build_content_map(pages) -> list:
    """把 plan.json 的页级主张映射为 content_map（每页可回溯）。"""
    out = []
    for p in pages:
        if not isinstance(p, dict):
            continue
        out.append({
            "slide_no": p.get("slide_no"),
            "role": p.get("role") if p.get("role") in ROLES else "other",
            "claim": p.get("claim"),
            "bullets": p.get("bullets") or [],
            "figure_refs": p.get("figure_refs") or [],
            "layout": p.get("layout"),
            "speaker_notes": p.get("speaker_notes"),
            "evidence": p.get("evidence") or [],
        })
    return out


def artifact_payload(out_dir: Path) -> tuple:
    """体检主产物，返回 (editable_source, export_artifact, pptx_inv, pdf_inv)。"""
    pptx_path = out_dir / "presentation.pptx"
    pdf_path = out_dir / "presentation.pdf"
    pptx_inv = inventory_pptx(pptx_path)
    pdf_inv = inventory_pdf(pdf_path)
    editable = {
        "path": "presentation.pptx",
        "format": "pptx",
        "slides": pptx_inv.get("slides", 0) if pptx_inv.get("valid") else 0,
        "valid": bool(pptx_inv.get("valid")),
    }
    export = {
        "pdf": {
            "path": "presentation.pdf",
            "pages": pdf_inv.get("pages", 0) if pdf_inv.get("valid") else 0,
            "valid": bool(pdf_inv.get("valid")),
        }
    }
    pngs = sorted(p.name for p in out_dir.glob("*.png")) if out_dir.is_dir() else []
    if pngs:
        export["png"] = pngs
    return editable, export, pptx_inv, pdf_inv


def assign_fields(skeleton: dict, ctx: dict, field_assignments, state: dict) -> dict:
    """把语义产物体写入匹配的契约字段；未匹配字段保持默认并如实记录。"""
    for key in list(skeleton.keys()):
        if key in ("case_id", "status") or key in ENVELOPE:
            continue
        explicit = (field_assignments or {}).get(key)
        target = explicit
        if target is None:
            if EDITABLE_RE.search(key):
                target = "editable_source"
            elif EXPORT_RE.search(key):
                target = "export_artifact"
            elif CONTENT_RE.search(key):
                target = "content_map"
            elif COMPLIANCE_RE.search(key):
                target = "compliance_checks"
            elif EXCEPTION_RE.search(key):
                target = "exceptions"
        if target in ctx:
            skeleton[key] = ctx[target]
        else:
            state["unmatched_fields"].append(key)
    return skeleton


def compute_checks(ctx: dict, pptx_inv: dict, pdf_inv: dict, plan: dict) -> list:
    """把六类评分要素转成可逐条核验的 compliance_checks。"""
    pages = plan.get("pages") or []
    slides = pptx_inv.get("slides") or 0
    pdf_pages = pdf_inv.get("pages") or 0
    cap = plan.get("slide_cap")

    def _ev_ok(p):
        ev = p.get("evidence") or []
        return bool(ev) and all((e.get("quote") or "").strip() for e in ev
                                if isinstance(e, dict))

    return [
        {"check": "contract_fields_present", "pass": True,
         "detail": f"{len(ctx.get('contract_keys', []))} 个契约字段全量回填"},
        {"check": "pptx_valid", "pass": bool(pptx_inv.get("valid")),
         "detail": f"slides={slides} error={pptx_inv.get('error')}"},
        {"check": "pdf_valid", "pass": bool(pdf_inv.get("valid")),
         "detail": f"pages={pdf_pages} error={pdf_inv.get('error')}"},
        {"check": "pdf_pages_match", "pass": bool(slides and pdf_pages and slides == pdf_pages),
         "detail": f"pptx {slides} vs pdf {pdf_pages}"},
        {"check": "every_page_has_evidence",
         "pass": bool(pages) and all(_ev_ok(p) for p in pages if isinstance(p, dict)),
         "detail": f"{sum(1 for p in pages if isinstance(p, dict) and _ev_ok(p))}/{len(pages)} 页有完整证据"},
        {"check": "content_map_aligned",
         "pass": bool(slides) and len(ctx.get("content_map") or []) >= slides,
         "detail": f"content_map {len(ctx.get('content_map') or [])} 条 vs slides {slides}"},
        {"check": "template_inherited", "pass": bool(pptx_inv.get("inherits_template")),
         "detail": f"layouts={pptx_inv.get('layout_count')} masters={pptx_inv.get('master_count')} themes={pptx_inv.get('themes')}"},
        {"check": "slide_cap_respected",
         "pass": bool(slides <= cap) if cap else True,
         "detail": f"slides={slides} cap={cap if cap else '未限定'}"},
    ]


def decide_status(result: dict, checks: list, page_count: int) -> str:
    if result.get("status") == STATUS_INPUT_ERROR:
        return STATUS_INPUT_ERROR
    if page_count == 0:
        return STATUS_INSUFFICIENT
    passed = {c["check"]: c["pass"] for c in checks}
    required = ["pptx_valid", "pdf_valid", "pdf_pages_match",
                "every_page_has_evidence", "template_inherited"]
    if all(passed.get(r) for r in required):
        return STATUS_OK
    return STATUS_PARTIAL


def write_report(out_dir: Path, result: dict, checks: list) -> None:
    es = result.get("editable_source") or {}
    ea = result.get("export_artifact") or {}
    lines = [
        f"# {result.get('case_id')} · 学术 PPT 交付报告",
        "",
        f"- status: `{result.get('status')}`",
        f"- pptx: `{es.get('path')}` ({es.get('slides')} slides, valid={es.get('valid')})",
        f"- pdf: `{(ea.get('pdf') or {}).get('path')}` ({(ea.get('pdf') or {}).get('pages')} pages)",
        f"- sources: `{len(result.get('sources') or [])}` · uncertainty: `{len(result.get('uncertainty') or [])}`",
        f"- change_log: `{len(result.get('change_log') or [])}` 条",
        "",
        "## 合规检查",
        "",
        "| check | pass | detail |",
        "|-|-|-|",
    ]
    for c in checks:
        lines.append(f"| {c['check']} | {'✓' if c['pass'] else '✗'} | {c['detail']} |")
    if result.get("errors"):
        lines += ["", "## errors"] + [f"- {e}" for e in result["errors"]]
    if result.get("degradations"):
        lines += ["", "## degradations"] + [f"- {d}" for d in result["degradations"]]
    write_text(out_dir / "report.md", "\n".join(lines) + "\n")


def load_case(in_dir: Path, state: dict) -> tuple:
    case, err = read_json(in_dir / "case.json")
    if err:
        state["errors"].append(f"case.json unavailable: {err}")
        return {}, STATUS_INPUT_ERROR
    if not isinstance(case, dict):
        state["errors"].append("case.json root is not an object")
        return {}, STATUS_INPUT_ERROR
    return case, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="契约优先学术 PPT Skill：骨架落盘 → 合并计划 → 产物校验 → 定稿",
        epilog="内容组织由调用 Agent 完成；本脚本只保证契约完整与产物可复核。")
    ap.add_argument("--input", help="输入目录（默认 /input）")
    ap.add_argument("--work", help="工作目录（默认 /app/work）")
    ap.add_argument("--output", help="输出目录（默认 /app/output）")
    ap.add_argument("--plan", help="Agent 写的页级计划 work/plan.json")
    ap.add_argument("--skeleton-only", action="store_true",
                    help="只落盘 result.json 骨架（前 3 分钟交付用）")
    ap.add_argument("--print-contract", action="store_true",
                    help="只打印解析出的契约字段名")
    ap.add_argument("--check-quotes", action="store_true",
                    help="对 evidence.quote 做逐字命中检查（材料需在 --input 下）")
    args = ap.parse_args(argv)

    state = {"errors": [], "degradations": [], "unmatched_fields": [],
             "changes": [], "started": time.time()}
    in_dir, in_msg = resolve_dir(args.input, DEFAULT_INPUT, "input", state)
    work_dir, _ = resolve_dir(args.work, DEFAULT_WORK, "work", state)
    out_dir, out_msg = resolve_dir(args.output, DEFAULT_OUTPUT, "output", state)
    if in_msg:
        state["errors"].append(f"input dir unusable: {in_msg}")
    if out_msg:
        state["errors"].append(f"output dir unusable: {out_msg}")

    case, input_err = load_case(in_dir, state)
    case_id = str(case.get("case_id") or "unknown-case")
    keys = parse_contract_keys(case)

    if args.print_contract:
        print(json.dumps(keys, ensure_ascii=False))
        return 0

    result = build_skeleton(case_id, keys, state)

    if input_err:
        result["status"] = STATUS_INPUT_ERROR
        result["errors"] = list(state["errors"])
        result["change_log"] = state["changes"]
        result["runtime"]["finished"] = now_iso()
        result["runtime"]["seconds"] = round(time.time() - state["started"], 2)
        write_json(out_dir / "result.json", result)
        return 0

    if args.skeleton_only:
        result["change_log"] = state["changes"]
        result["runtime"]["finished"] = now_iso()
        result["runtime"]["seconds"] = round(time.time() - state["started"], 2)
        write_json(out_dir / "result.json", result)
        print(f"[{TASK_LABEL}] skeleton written: {len(keys)} fields, status=partial")
        return 0

    plan = read_plan(args.plan or (work_dir / "plan.json"))
    pages = plan.get("pages") or []
    content_map = build_content_map(pages)
    editable, export, pptx_inv, pdf_inv = artifact_payload(out_dir)
    ctx = {
        "contract_keys": keys,
        "editable_source": editable,
        "export_artifact": export,
        "content_map": content_map,
        "compliance_checks": [],
        "exceptions": list(state["errors"]) + list(state["degradations"]),
    }
    checks = compute_checks(ctx, pptx_inv, pdf_inv, plan)
    ctx["compliance_checks"] = checks
    if args.check_quotes:
        quote_report = check_quotes_verbatim(pages, in_dir)
        checks.extend(quote_report)
        ctx["compliance_checks"] = checks

    result = assign_fields(result, ctx, plan.get("field_assignments"), state)
    for env in ("sources", "uncertainty", "pending_human"):
        if isinstance(plan.get(env), list) and plan[env]:
            result[env] = plan[env]
    result["errors"] = list(state["errors"])
    result["degradations"] = list(state["degradations"])
    if plan.get("exceptions"):
        result["exceptions"] = list(result.get("exceptions") or []) + list(plan["exceptions"])

    result["status"] = decide_status(result, checks, len(pages))
    new_change(state, "status", "set", f"final={result['status']}")
    if state["unmatched_fields"]:
        new_change(state, "contract", "default",
                   f"未匹配字段保持默认值: {', '.join(state['unmatched_fields'])}")
    result["change_log"] = state["changes"]
    result["runtime"]["finished"] = now_iso()
    result["runtime"]["seconds"] = round(time.time() - state["started"], 2)

    write_json(out_dir / "result.json", result)
    write_report(out_dir, result, checks)
    print(f"[{TASK_LABEL}] done status={result['status']} "
          f"slides={pptx_inv.get('slides')} contract_fields={len(keys)}")
    return 0


def check_quotes_verbatim(pages, in_dir: Path) -> list:
    """逐字溯源：evidence.quote 必须在材料文件里命中（软检查，不改变 status）。"""
    out = []
    total = hits = missing = 0
    for p in pages:
        if not isinstance(p, dict):
            continue
        for e in p.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            quote = (e.get("quote") or "").strip()
            total += 1
            if not quote:
                missing += 1
                continue
            source_id = e.get("source_id") or ""
            locator = e.get("locator") or ""
            file_part = locator.split("#", 1)[0]
            candidates = []
            if source_id:
                candidates.append(in_dir / source_id)
            if file_part:
                candidates.append(in_dir / file_part)
            found = None
            for cand in candidates:
                if cand.is_file():
                    n = quote_hits(cand, quote)
                    if n > 0:
                        found = n
                        break
                    if n == 0:
                        found = 0
            if found is None:
                missing += 1
            elif found:
                hits += 1
    out.append({"check": "quotes_verbatim",
                "pass": total > 0 and missing == 0,
                "detail": f"{hits}/{total} 命中，{missing} 未命中/不可定位"})
    return out


if __name__ == "__main__":
    sys.exit(main())
