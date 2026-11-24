#!/usr/bin/env python3
"""sci-figure-editor orchestrator.

Stages:
  init      write a legal partial result.json skeleton immediately (first 3 min)
  render    validate figure_spec.json, render deterministic SVG, merge evidence
  finalize  detect/describe export files, run selftest, set honest status
  all       init -> render -> finalize (default)

Pure standard library. Deterministic for scientific content: wall-clock only
enters runtime/change_log, which selftest excludes from the content hash.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import spec_to_svg  # noqa: E402


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def find_case(input_dir, explicit):
    if explicit and os.path.isfile(explicit):
        return explicit
    cands = sorted(glob.glob(os.path.join(input_dir, "*.json")))
    for c in cands:
        if os.path.basename(c).lower() == "case.json":
            return c
    return cands[0] if cands else None


def extract_case_id(case_path):
    if not case_path:
        return "unknown"
    try:
        data = read_json(case_path)
    except Exception:
        return "unknown"
    for key in ("case_id", "caseId", "id"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def blank_result(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "editable_source_files": [],
        "export_files": [],
        "provenance": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {},
    }


def log(result, step, action, detail=""):
    result.setdefault("change_log", []).append({
        "step": step, "time": now_iso(), "action": action, "detail": detail,
    })


def append_unique(items, item, reason, impact, mitigation):
    """Append a note only if no entry with the same `item` key exists, so that
    repeated runs stay idempotent and do not mutate the deterministic core."""
    for it in items:
        if it.get("item") == item:
            return
    items.append({"item": item, "reason": reason,
                  "impact": impact, "mitigation": mitigation})


def stage_init(result, result_path, case_path, work_dir):
    if os.path.isfile(result_path):
        try:
            result = read_json(result_path)
        except Exception as e:
            result = blank_result("unknown")
            result["errors"].append("result.json 损坏，重建骨架: %s" % e)
            result["status"] = "partial"
    case_id = extract_case_id(case_path)
    if not result.get("case_id") or result["case_id"] in ("", "unknown"):
        result["case_id"] = case_id
    result.setdefault("status", "partial")
    result.setdefault("editable_source_files", [])
    result.setdefault("export_files", [])
    result.setdefault("provenance", [])
    result.setdefault("sources", [])
    result.setdefault("uncertainty", [])
    result.setdefault("pending_human", [])
    result.setdefault("change_log", [])
    result.setdefault("errors", [])
    result.setdefault("degradations", [])
    result.setdefault("runtime", {})
    result["runtime"]["start_iso"] = now_iso()
    log(result, "init", "落盘 partial 结果骨架", "case_id=%s" % result["case_id"])
    write_json(result_path, result)
    return result


def load_spec(work_dir):
    spec_path = os.path.join(work_dir, "figure_spec.json")
    if not os.path.isfile(spec_path):
        return None, spec_path
    spec = read_json(spec_path)
    spec_to_svg.validate_spec(spec)
    return spec, spec_path


def merge_evidence(result, work_dir):
    ev_path = os.path.join(work_dir, "entities.json")
    if not os.path.isfile(ev_path):
        return result
    try:
        ev = read_json(ev_path)
    except Exception as e:
        result["errors"].append("entities.json 解析失败: %s" % e)
        return result
    seen = {p.get("element") for p in result.get("provenance", [])}
    for ent in ev.get("entities", []):
        if ent.get("id") in seen:
            continue
        result.setdefault("provenance", []).append({
            "element": ent.get("id"),
            "source_ref": ent.get("source_ref", ""),
            "locator": ent.get("locator", ""),
            "quote": ent.get("quote", ""),
            "mapping": ent.get("mapping", ""),
            "version": ent.get("version", "1.0"),
            "confirmed": bool(ent.get("quote")),
        })
        seen.add(ent.get("id"))
    for rel in ev.get("relations", []):
        if rel.get("id") in seen:
            continue
        result.setdefault("provenance", []).append({
            "element": rel.get("id"),
            "source_ref": rel.get("source_ref", ""),
            "locator": rel.get("locator", ""),
            "quote": rel.get("quote", ""),
            "mapping": "%s --%s--> %s" % (
                rel.get("from"), rel.get("kind"), rel.get("to")),
            "version": rel.get("version", "1.0"),
            "confirmed": bool(rel.get("quote")),
        })
        seen.add(rel.get("id"))
    for lg in ev.get("legend", []):
        if lg.get("id") in seen:
            continue
        result.setdefault("provenance", []).append({
            "element": lg.get("id"),
            "source_ref": lg.get("source_ref", ""),
            "locator": lg.get("locator", ""),
            "quote": lg.get("quote", ""),
            "mapping": lg.get("mapping", ""),
            "version": lg.get("version", "1.0"),
            "confirmed": bool(lg.get("quote")),
        })
        seen.add(lg.get("id"))
    existing_sources = {s.get("ref") for s in result.get("sources", [])}
    for src in ev.get("sources", []):
        ref = src.get("ref") or src.get("path")
        if ref in existing_sources:
            continue
        result.setdefault("sources", []).append({
            "ref": ref, "path": src.get("path", ""),
            "locator": src.get("locator", ""), "used_for": src.get("used_for", ""),
        })
        existing_sources.add(ref)
    return result


def probe_png_size(path):
    try:
        with open(path, "rb") as fh:
            sig = fh.read(24)
        if sig[:8] != b"\x89PNG\r\n\x1a\n":
            return None, None
        w = int.from_bytes(sig[16:20], "big")
        h = int.from_bytes(sig[20:24], "big")
        return w, h
    except Exception:
        return None, None


def describe_exports(result, output_dir):
    entries = []
    for pat in ("*.png", "*.pdf"):
        for p in sorted(glob.glob(os.path.join(output_dir, pat))):
            name = os.path.basename(p)
            fmt = os.path.splitext(name)[1].lstrip(".").lower()
            w = h = None
            font_check = "unknown"
            if fmt == "png":
                w, h = probe_png_size(p)
            elif fmt == "pdf":
                with open(p, "rb") as fh:
                    head = fh.read(5)
                if head == b"%PDF-":
                    font_check = "unknown"
                else:
                    font_check = "failed"
            entries.append({
                "path": name, "format": fmt,
                "width_px": w, "height_px": h,
                "dpi": None, "color_space": None,
                "font_check": font_check, "note": "",
            })
    result["export_files"] = entries
    return result


def stage_render(result, result_path, output_dir, work_dir):
    spec, spec_path = load_spec(work_dir)
    if spec is None:
        result["degradations"].append(
            {"stage": "render", "reason": "缺 figure_spec.json",
             "impact": "无 SVG 源文件", "note": "先抽取实体关系再重跑 render"})
        log(result, "render", "跳过渲染", "缺 figure_spec.json")
        write_json(result_path, result)
        return result
    fid = spec.get("figure_id", "figure")
    svg_name = fid if fid.endswith(".svg") else fid + ".svg"
    svg_path = os.path.join(output_dir, svg_name)
    colors = spec_to_svg.assign_colors(spec)
    pos = spec_to_svg.layout(spec)
    svg = spec_to_svg.render(spec, colors, pos)
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    result["editable_source_files"] = [{
        "path": svg_name, "kind": "svg", "layers": True,
        "editable_text": True, "font_replaceable": True,
        "note": "由 figure_spec.json 确定性渲染；改 spec 重跑即可重绘",
    }]
    result = merge_evidence(result, work_dir)
    log(result, "render", "渲染分层 SVG", "spec=%s entities=%d relations=%d" % (
        os.path.basename(spec_path), len(spec.get("entities", [])),
        len(spec.get("relations", []))))
    write_json(result_path, result)
    return result


def stage_finalize(result, result_path, output_dir, work_dir, input_dir):
    result = describe_exports(result, output_dir)
    has_svg = bool(result.get("editable_source_files"))
    has_export = bool(result.get("export_files"))
    if has_svg and has_export:
        result["status"] = "complete"
    else:
        result["status"] = "partial"
        if not has_svg:
            append_unique(result["uncertainty"],
                          "可编辑源文件", "未渲染 SVG",
                          "不可编辑", "补 figure_spec.json 后重跑 render")
        if not has_export:
            append_unique(result["uncertainty"],
                          "导出件", "未检测到 PNG/PDF",
                          "无法在目标场景打开", "用环境工具导出后重跑 finalize")
    log(result, "finalize", "导出检测与状态判定", "status=%s" % result["status"])
    # Persist BEFORE selftest so it checks the latest on-disk result.
    write_json(result_path, result)
    # Selftest is advisory here: failures are recorded, never silently dropped.
    st = os.path.join(HERE, "selftest.py")
    if os.path.isfile(st):
        try:
            p = subprocess.run(
                [sys.executable, st, "--output-dir", output_dir,
                 "--work-dir", work_dir, "--input-dir", input_dir],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
            result["runtime"]["selftest_rc"] = p.returncode
            if p.returncode != 0:
                result["degradations"].append({
                    "stage": "selftest", "reason": "自检未通过",
                    "impact": "见 selftest 输出", "note": (p.stdout or "")[-2000:]})
        except Exception as e:
            result["errors"].append("selftest 运行失败: %s" % e)
    write_json(result_path, result)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--task", default="/input/task.md")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--stage", default="all",
                    choices=["init", "render", "finalize", "all"])
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    result_path = os.path.join(a.out, "result.json")
    input_dir = os.path.dirname(a.case) or "/input"
    case_path = find_case(input_dir, a.case)

    if a.stage in ("init", "all"):
        result = blank_result(extract_case_id(case_path))
        result = stage_init(result, result_path, case_path, a.work)
    else:
        if os.path.isfile(result_path):
            result = read_json(result_path)
        else:
            result = blank_result(extract_case_id(case_path))

    if a.stage in ("render", "all"):
        result = stage_render(result, result_path, a.out, a.work)
    if a.stage in ("finalize", "all"):
        result = stage_finalize(result, result_path, a.out, a.work, input_dir)

    print(json.dumps({
        "ok": True, "stage": a.stage, "case_id": result.get("case_id"),
        "status": result.get("status"),
        "result": result_path,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
