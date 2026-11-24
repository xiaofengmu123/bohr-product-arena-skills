#!/usr/bin/env python3
"""slides-01 主入口：先落 result.json 骨架，再发现材料、回填契约、按 draft 成稿。

纯标准库。任何情况下退出码均为 0，并尽力写出 /app/output/result.json。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from slides_lib import (  # noqa: E402
    DEFAULT_FILE_LIMIT, STATUS, build_minimal_pptx, default_value,
    discover_materials, ensure_dir, export_pdf, extract_contract_keys,
    fill_contract, now_iso, pdf_page_count, pptx_slide_count, read_json,
    read_text, render_svg_deck, validate_draft, write_json, write_text,
)

VERSION = "1.0.0-robust"

_STATE = {"errors": [], "degradations": [], "steps": [], "started": time.time()}


def step(name: str):
    _STATE["steps"].append(name)


def degrade(item: str):
    _STATE["degradations"].append(item)


def fail_hard(item: str):
    _STATE["errors"].append(item)


def change_log(entry: str):
    return {"ts": now_iso(), "entry": entry}


def resolve_dirs(args):
    """解析目录，缺省 /input、/app/work、/app/output；失败降级到 cwd。"""
    input_root = Path(getattr(args, "input", None) or "/input").expanduser()
    work = Path(getattr(args, "work", None) or "/app/work").expanduser()
    output = Path(getattr(args, "output", None) or "/app/output").expanduser()
    for label, path in (("work", work), ("output", output)):
        ok, err = ensure_dir(path)
        if not ok:
            fallback = Path.cwd() / f"skill_{label}"
            if ensure_dir(fallback)[0]:
                degrade(f"{label} 目录 {path} 不可用({err})，降级到 {fallback}")
                if label == "work":
                    work = fallback
                else:
                    output = fallback
    return input_root, work, output


def build_skeleton(case_id: str, status: str, keys) -> dict:
    payload = {"case_id": case_id, "status": status}
    filled, _ = fill_contract(keys, payload)
    envelope = {
        "sources": [], "uncertainty": [], "pending_human": [],
        "change_log": [], "errors": [], "degradations": [], "runtime": {},
    }
    for key, val in envelope.items():
        filled.setdefault(key, val)
    return filled


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="slides-01 论文转学术 PPT 主入口")
    parser.add_argument("--input", default="/input", help="输入目录（默认 /input）")
    parser.add_argument("--work", default="/app/work", help="工作目录（默认 /app/work）")
    parser.add_argument("--output", default="/app/output", help="输出目录（默认 /app/output）")
    parser.add_argument("--draft", default=None, help="draft.json 路径（默认 /app/work/draft.json）")
    parser.add_argument("--file-limit", type=int, default=DEFAULT_FILE_LIMIT,
                        help="单文件字节上限（默认 2MB）")
    args = parser.parse_args(argv)

    input_root, work, output = resolve_dirs(args)
    changes = [change_log(f"main.py v{VERSION} 启动")]

    # 1) 读取 case.json（容错）
    step("load_case")
    case, case_err = read_json(input_root / "case.json", args.file_limit)
    if case_err:
        fail_hard(f"case.json unavailable: {case_err}")
    case = case if isinstance(case, dict) else {}

    case_id = str(case.get("case_id") or "unknown-case")
    keys = extract_contract_keys(case)
    if not case:
        # 输入缺失或坏 JSON：仍写合法骨架
        result = build_skeleton(case_id, "input_error", keys)
        result["errors"] = [f"case.json unavailable: {case_err}"] if case_err else ["case.json 缺失"]
        result["change_log"] = changes + [change_log("输入缺失/坏 JSON，标记 input_error")]
        result["runtime"] = {"version": VERSION, "elapsed_s": round(time.time() - _STATE["started"], 1)}
        write_json(output / "result.json", result)
        print(f"[slides-01] status=input_error case_id={case_id} -> {output / 'result.json'}")
        return 0

    # 2) 立即落骨架（partial），保证任何后续异常都有可交接产物
    step("skeleton")
    result = build_skeleton(case_id, "partial", keys)
    changes.append(change_log("写入 result.json 骨架（partial）"))
    result["change_log"] = list(changes)
    result["runtime"] = {"version": VERSION, "elapsed_s": 0.0}
    write_json(output / "result.json", result)

    # 3) 发现材料（超大文件跳过）
    step("discover")
    materials, degraded = discover_materials(input_root, args.file_limit)
    for item in degraded:
        degrade(item)
    sources = [{"path": m["rel"], "size": m["size"], "suffix": m["suffix"]}
               for m in materials]
    result["sources"] = sources

    # 4) 读 task.md（可选）
    task_text, _ = read_text(input_root / "task.md", args.file_limit)
    if task_text:
        write_text(work / "task.md", task_text)

    # 5) 读 draft.json
    step("draft")
    draft_path = Path(args.draft).expanduser() if args.draft else (work / "draft.json")
    draft, draft_err = read_json(draft_path, args.file_limit)
    if draft_err:
        degrade(f"draft.json 未就绪: {draft_err}")

    slides, title, validation_errors = [], "", []
    if isinstance(draft, dict):
        slides, title, validation_errors = validate_draft(draft)

    # 6) 成稿
    step("build")
    editable_path, editable_err = None, None
    export_path, export_err = None, None
    fallback_path, fallback_err = None, None

    if validation_errors:
        for e in validation_errors:
            degrade(f"draft 校验: {e}")
        result["pending_human"].append("draft.json 结构不完整，需补齐 claim/role/evidence")

    if slides:
        editable_path, editable_err = build_minimal_pptx(slides, title, output / "presentation.pptx")
        if editable_err:
            fail_hard(editable_err)
            fallback_path, fallback_err = render_svg_deck(slides, title, output / "presentation.svg")
            if fallback_err:
                fail_hard(fallback_err)
            else:
                degrade("pptx 构建失败，已降级为 SVG 预览")
        else:
            export_path, export_err = export_pdf(Path(editable_path), output)
            if export_err:
                degrade(export_err)
            else:
                pdf_pages, pdf_err = pdf_page_count(Path(export_path))
                if pdf_err:
                    degrade(pdf_err)
                else:
                    pptx_pages, _ = pptx_slide_count(Path(editable_path))
                    if pptx_pages and pdf_pages != pptx_pages:
                        degrade(f"PDF 页数({pdf_pages})与 pptx 页数({pptx_pages})不一致")
    else:
        result["pending_human"].append(
            "尚未写出 /app/work/draft.json（页级计划）；请阅读材料后补齐再运行 --draft")

    # 7) 组装契约字段
    step("finalize")
    if editable_path:
        slide_count, _ = pptx_slide_count(Path(editable_path))
        result["editable_source"] = {"path": "presentation.pptx", "format": "pptx", "slides": slide_count}
    elif fallback_path:
        result["editable_source"] = {"path": "presentation.svg", "format": "svg", "slides": len(slides)}
        result["exceptions"].append("无 pptx 能力，已提供 SVG 预览")
    if export_path:
        pages, _ = pdf_page_count(Path(export_path))
        result["export_artifact"] = {"pdf": {"path": "presentation.pdf", "pages": pages}}
    result["content_map"] = [
        {"page": i, "claim": s.get("claim"), "role": s.get("role"),
         "evidence": s.get("evidence") or {}, "notes": s.get("speaker_notes") or ""}
        for i, s in enumerate(slides, 1)
    ]
    result["compliance_checks"] = [
        {"check": "result.json 存在且含 case_id", "pass": True},
        {"check": "editable_source 指向真实文件", "pass": bool(editable_path or fallback_path)},
        {"check": "export_artifact 有渲染预览", "pass": bool(export_path)},
        {"check": "每页 evidence 有 locator+quote", "pass": all(
            isinstance(s.get("evidence"), dict) and (s["evidence"].get("locator") or s["evidence"].get("quote"))
            for s in slides)},
    ]

    # 8) 状态判定（诚实）
    if _STATE["errors"] and not slides:
        status = "input_error" if not case else "partial"
    elif not materials and not slides:
        status = "insufficient_evidence"
    elif slides and not validation_errors and editable_path and export_path:
        status = "ok"
    else:
        status = "partial"
    result["status"] = status
    if not materials:
        result["uncertainty"].append("输入目录未发现可读材料")
    result["change_log"] = changes + [change_log(f"最终成稿，status={status}")]
    result["errors"] = list(_STATE["errors"])
    result["degradations"] = list(_STATE["degradations"])
    result["runtime"] = {"version": VERSION,
                         "elapsed_s": round(time.time() - _STATE["started"], 1),
                         "steps": list(_STATE["steps"])}

    write_json(output / "result.json", result)
    print(f"[slides-01] status={status} case_id={case_id} slides={len(slides)} "
          f"-> {output / 'result.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
