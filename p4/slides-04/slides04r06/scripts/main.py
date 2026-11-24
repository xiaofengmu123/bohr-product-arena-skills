#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""academic-slides-evidence-pipeline 主入口。

把「论文/科研材料 + PPT 模板」组织成可复核的学术汇报，核心是两层结构化中间表示：
  plan.json         —— 内容/论证模型（说什么、页级主张、讲解顺序）
  figure_model.json —— 图形/排版模型（图在哪、多大、怎么等比放进容器、图号与正文引用）

只用 Python 标准库。确定性、幂等、失败可解释；任何一步异常都保留已落盘的部分结果。

子命令：
  init     发现输入、写输入清单、落 result.json 骨架 + plan/figure_model 骨架（前 3 分钟内先跑）
  plan     校验 /app/work/plan.json 的结构完整性
  figures  按材料发现图片、算原生尺寸与宽高比、生成 figure_model.json（等比缩放规则）
  evidence 由 plan.json 的 refs 在提取文本中定位逐字 quote，生成页级 evidence_map
  verify   校验 result.json / 中间表示 / pptx-pdf-png 产物完整性，输出复核报告
  finalize 依据 verify 结果诚实地把 status 从 in_progress 收敛到最终状态并落 change_log
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import struct
import sys
import zipfile
from pathlib import Path

VERSION = "1.0.0"
PLAN_SCHEMA = "academic-slides/plan@1"
FIG_SCHEMA = "academic-slides/figure-model@1"
RESULT_SCHEMA = "academic-slides/result@1"

TEXT_CAP = 2 * 1024 * 1024          # 单文本文件上限 2MB，超限跳过并记录
MAX_EVIDENCE_QUOTE = 500            # 逐字 quote 上限，防证据体无限膨胀

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".tif", ".tiff", ".webp"}
DOC_EXT = {".pdf", ".txt", ".md", ".csv", ".bib", ".tex", ".json", ".xml", ".tsv"}
OFFICE_EXT = {".pptx", ".docx", ".xlsx"}
MATERIAL_EXT = IMG_EXT | DOC_EXT | OFFICE_EXT

STATUSES = {"ok", "partial", "insufficient_evidence", "input_error", "failed", "in_progress"}
PAGE_ROLES = {"cover", "story", "method", "result", "limit", "conclusion", "appendix"}
SCALE_MODES = {"fit", "fill_crop", "none", "pending"}
LOCATOR_RE = re.compile(r"^(?P<path>[^#\r\n]+)#(?P<loc>L\d+(?:-L?\d+)?|P\d+|S\d+)$")


# --------------------------------------------------------------------------- #
# 基础 I/O（BOM/坏编码/坏 JSON 容错，不崩溃）
# --------------------------------------------------------------------------- #
def read_bytes_capped(path: Path, cap: int = TEXT_CAP):
    try:
        data = path.read_bytes()
    except OSError as e:
        return None, f"read_failed:{e}"
    if len(data) > cap:
        return None, f"size_over_cap:{len(data)}>{cap}"
    return data, None


def read_text(path: Path, cap: int = TEXT_CAP):
    data, err = read_bytes_capped(path, cap)
    if err:
        return None, err
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), None
        except (UnicodeDecodeError, LookupError):
            continue
    return None, "undecodable_text"


def read_json(path: Path):
    data, err = read_bytes_capped(path)
    if err:
        return None, [err]
    for enc in ("utf-8-sig", "utf-8"):
        try:
            text = data.decode(enc)
            return json.loads(text), None
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            last = f"{type(e).__name__}:{e}"
        except LookupError:
            last = "codec_missing"
    return None, [last]


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# 输入发现（不假设固定文件名/键名，递归扫描 + 读取 case.json 中的路径线索）
# --------------------------------------------------------------------------- #
def discover_input(input_dir: Path):
    case_path = input_dir / "case.json"
    task_path = input_dir / "task.md"
    case, case_err = read_json(case_path) if case_path.is_file() else (None, ["case.json_missing"])
    task, task_err = read_text(task_path) if task_path.is_file() else (None, ["task.md_missing"])

    materials, skips = [], []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if p in (case_path, task_path):
            continue
        if p.suffix.lower() in MATERIAL_EXT:
            materials.append(p)
        elif p.suffix.lower() in {".yaml", ".yml", ".log", ".jsonl"}:
            materials.append(p)
        else:
            skips.append(str(p))
    return case, case_err, task, task_err, materials, skips


def extract_case_id(case):
    if not isinstance(case, dict):
        return None
    for key in ("case_id", "id", "challenge_id", "armiId", "benchmarkId"):
        if isinstance(case.get(key), str) and case[key].strip():
            return case[key].strip()
    return None


# --------------------------------------------------------------------------- #
# result.json 骨架 + 幂等合并
# --------------------------------------------------------------------------- #
def result_skeleton(case_id: str, work_dir: Path, output_dir: Path):
    work = str(work_dir)
    return {
        "schema": RESULT_SCHEMA,
        "case_id": case_id,
        "status": "in_progress",
        "presentation": {
            "pptx_path": None,
            "pdf_path": None,
            "png_paths": [],
            "slide_count": None,
            "page_plan": [],
        },
        "evidence_map": [],
        "render_record": {
            "source_files": [],
            "template_file": None,
            "layouts_used": [],
            "fonts": [],
            "exports": [],
            "integrity": {},
            "media": [],
        },
        "intermediate": {
            "plan": f"{work}/plan.json",
            "figure_model": f"{work}/figure_model.json",
            "inventory": f"{work}/input_inventory.json",
        },
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": None, "finished_at": None, "duration_sec": None, "steps": []},
        "change_log": [],
    }


def merge_result(existing, skeleton):
    """幂等合并：已有非空字段保留，只补空缺；change_log 只追加真实变化。"""
    if not isinstance(existing, dict):
        return skeleton, True
    out = dict(skeleton)
    changed = False
    for k, v in existing.items():
        if k not in out:
            out[k] = v
            continue
        if k == "case_id" and v:
            out[k] = v
            changed = True
        elif k in ("presentation", "render_record", "intermediate", "runtime") and isinstance(v, dict) and isinstance(out[k], dict):
            merged = dict(out[k])
            for kk, vv in v.items():
                if vv not in (None, [], {}, "") or kk not in merged:
                    merged[kk] = vv
            if merged != out[k]:
                changed = True
            out[k] = merged
        elif isinstance(v, list) and v:
            out[k] = v
        elif isinstance(v, str) and v and k == "status":
            out[k] = v
    return out, changed


def log_change(result, event, detail=""):
    result["change_log"].append(
        {"ts": now_iso(), "event": event, "detail": detail}
    )
    return result


# --------------------------------------------------------------------------- #
# 中间表示：plan.json / figure_model.json
# --------------------------------------------------------------------------- #
def plan_skeleton(case_id: str):
    return {
        "schema": PLAN_SCHEMA,
        "case_id": case_id,
        "created_at": now_iso(),
        "updated_at": None,
        "narrative": {
            "research_question": None,
            "approach": None,
            "key_results": [],
            "limitations": [],
        },
        "pages": [],
        "uncertainty": [],
    }


def figure_model_skeleton(case_id: str):
    return {
        "schema": FIG_SCHEMA,
        "case_id": case_id,
        "created_at": now_iso(),
        "updated_at": None,
        "figures": [],
        "rules": {
            "scale": (
                "scale=min(cw/nw,ch/nh) for fit; scale=max(cw/nw,ch/nh) for fill_crop; "
                "never change native aspect; unknown dims => pending"
            ),
            "numbering": "图号按材料出现顺序编号，caption 与正文引用必须同源同号",
        },
    }


# --- 图片原生尺寸（纯标准库解析，不依赖 PIL） ---
def read_image_dims(path: Path):
    data, err = read_bytes_capped(path, cap=64 * 1024 * 1024)
    if err:
        return None, err
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return (w, h, "px"), None
    if data[:3] == b"GIF" and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return (w, h, "px"), None
    if data[:2] == b"\xff\xd8":
        i, n = 2, len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return (w, h, "px"), None
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
        return None, "jpeg_sof_not_found"
    if data[:2] == b"BM" and len(data) >= 26:
        w, h = struct.unpack("<ii", data[18:26])
        return (abs(w), abs(h), "px"), None
    if data[:2] == b"II" or data[:2] == b"MM":
        return None, "tiff_unsupported_stdlib"
    return None, "unknown_format"


def svg_viewbox(path: Path):
    text, err = read_text(path)
    if err:
        return None, err
    vb = re.search(r'viewBox\s*=\s*["\']\s*([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)', text)
    if vb:
        w, h = float(vb.group(3)), float(vb.group(4))
        return (w, h, "pt"), None
    wm = re.search(r'width\s*=\s*["\']\s*([\d.]+)', text)
    hm = re.search(r'height\s*=\s*["\']\s*([\d.]+)', text)
    if wm and hm:
        return (float(wm.group(1)), float(hm.group(1)), "pt"), None
    return None, "svg_no_viewbox"


def aspect_of(dims):
    if not dims:
        return None
    w, h, _unit = dims
    if h == 0:
        return None
    return round(w / h, 4)


def build_figure_model(work_dir: Path, materials, case_id: str):
    model = figure_model_skeleton(case_id)
    for m in materials:
        suffix = m.suffix.lower()
        if suffix not in IMG_EXT:
            continue
        dims, err = (None, "non_image")
        if suffix == ".svg":
            dims, err = svg_viewbox(m)
        else:
            dims, err = read_image_dims(m)
        if suffix in {".svg"} and err is None:
            kind = "vector"
        elif err is None:
            kind = "raster"
        else:
            kind = "unknown"
        fig = {
            "id": f"fig:{len(model['figures']) + 1}",
            "source": str(m),
            "kind": kind,
            "native_size": None if not dims else {"w": dims[0], "h": dims[1], "unit": dims[2]},
            "aspect_ratio": aspect_of(dims),
            "caption": None,
            "number": len(model["figures"]) + 1,
            "target_container": None,
            "scale_mode": "fit" if dims else "pending",
            "refs": [],
            "evidence": {"locator": None, "quote": None},
            "status": "ok" if dims else ("unknown_dimensions" if err != "non_image" else "missing"),
        }
        model["figures"].append(fig)
    model["updated_at"] = now_iso()
    return model


# --------------------------------------------------------------------------- #
# 证据溯源：locator + 逐字 quote
# --------------------------------------------------------------------------- #
def _normalize_ws(s: str):
    return re.sub(r"\s+", " ", s).strip()


def resolve_locator(locator: str, work_dir: Path):
    """locator 语法：path#L12-L18（行）/ path#L12（单行）/ path#P3（段）/ path#S2（节标题）"""
    m = LOCATOR_RE.match(locator or "")
    if not m:
        return {"locator": locator, "quote": None, "status": "unresolved", "error": "locator_format"}
    rel, loc = m.group("path"), m.group("loc")
    target = None
    for cand in (Path(work_dir) / rel,):
        if cand.is_file():
            target = cand
            break
    if target is None:
        base = Path(rel).name
        for f in Path(work_dir).rglob("*"):
            if f.is_file() and f.name == base:
                target = f
                break
    if target is None:
        return {"locator": locator, "path": rel, "quote": None, "status": "unresolved", "error": "file_not_found"}
    text, err = read_text(target)
    if err:
        return {"locator": locator, "path": str(target), "quote": None, "status": "unresolved", "error": err}
    lines = text.splitlines()
    if loc.startswith("L"):
        parts = loc[1:].split("-")
        a = int(parts[0])
        b = int(parts[1].lstrip("L")) if len(parts) > 1 else a
        if a < 1 or b < a or b > len(lines):
            return {"locator": locator, "path": str(target), "quote": None, "status": "unresolved", "error": "line_range_out_of_bounds"}
        quote = _normalize_ws("\n".join(lines[a - 1:b]))[:MAX_EVIDENCE_QUOTE]
        return {"locator": locator, "path": str(target), "quote": quote, "status": "resolved", "line_start": a, "line_end": b}
    if loc.startswith("P"):
        paragraphs = re.split(r"\n\s*\n", text)
        idx = int(loc[1:]) - 1
        if idx < 0 or idx >= len(paragraphs):
            return {"locator": locator, "path": str(target), "quote": None, "status": "unresolved", "error": "paragraph_out_of_range"}
        return {"locator": locator, "path": str(target), "quote": _normalize_ws(paragraphs[idx])[:MAX_EVIDENCE_QUOTE], "status": "resolved"}
    if loc.startswith("S"):
        idx = int(loc[1:])
        headings = [i for i, ln in enumerate(lines) if re.match(r"^#{1,6}\s", ln.strip())]
        if idx < 1 or idx > len(headings):
            return {"locator": locator, "path": str(target), "quote": None, "status": "unresolved", "error": "section_out_of_range"}
        return {"locator": locator, "path": str(target), "quote": _normalize_ws(lines[headings[idx - 1]])[:MAX_EVIDENCE_QUOTE], "status": "resolved"}
    return {"locator": locator, "path": str(target), "quote": None, "status": "unresolved", "error": "locator_format"}


def build_evidence_map(plan_path: Path, work_dir: Path):
    plan, err = read_json(plan_path)
    if err:
        return [], [f"plan_read_failed:{err}"]
    if not isinstance(plan, dict):
        return [], ["plan_not_dict"]
    evidence_map, problems = [], []
    for page in plan.get("pages", []):
        refs, page_status = [], "missing"
        resolved = 0
        for ref in page.get("refs", []) or []:
            r = resolve_locator(ref, work_dir)
            if r["status"] == "resolved":
                resolved += 1
                refs.append({"locator": r["locator"], "path": r.get("path"), "quote": r["quote"], "status": "resolved"})
            else:
                refs.append({"locator": ref, "quote": None, "status": "unresolved", "error": r.get("error")})
                problems.append({"page": page.get("index"), "ref": ref, "error": r.get("error")})
        if resolved and resolved == len(refs):
            page_status = "complete"
        elif resolved:
            page_status = "partial"
        evidence_map.append({
            "page": page.get("index"),
            "claim": page.get("claim"),
            "refs": refs,
            "evidence_status": page_status,
        })
    return evidence_map, problems


# --------------------------------------------------------------------------- #
# pptx / pdf / png 完整性检查（可编辑性与渲染可复核）
# --------------------------------------------------------------------------- #
def inspect_pptx(path: Path):
    try:
        if not zipfile.is_zipfile(path):
            return {"ok": False, "error": "not_zip"}
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            slides = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
            masters = sorted(n for n in names if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n))
            layouts = sorted(n for n in names if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n))
            notes = sorted(n for n in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n))
            media = sorted(n for n in names if n.startswith("ppt/media/"))
        return {"ok": True, "slides": len(slides), "slide_masters": len(masters),
                "slide_layouts": len(layouts), "notes": len(notes), "media": media}
    except (zipfile.BadZipFile, OSError) as e:
        return {"ok": False, "error": str(e)}


def inspect_pdf(path: Path):
    try:
        with open(path, "rb") as f:
            head = f.read(5)
        return {"ok": head.startswith(b"%PDF-"), "magic": head[:5].decode("latin-1", "replace")}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def inspect_png(path: Path):
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        return {"ok": head == b"\x89PNG\r\n\x1a\n"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #
def cmd_init(args):
    input_dir, work_dir, output_dir = Path(args.input), Path(args.work), Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    case, case_err, task, task_err, materials, skips = discover_input(input_dir)
    case_id = extract_case_id(case) or (input_dir.name if input_dir.is_dir() else "unknown")
    errors = (case_err or []) + (task_err or [])

    inventory = {
        "input_dir": str(input_dir),
        "case_json": str(input_dir / "case.json") if (input_dir / "case.json").is_file() else None,
        "task_md": str(input_dir / "task.md") if (input_dir / "task.md").is_file() else None,
        "materials": [{"path": str(m), "bytes": m.stat().st_size if m.is_file() else None,
                       "sha256": sha256_file(m), "ext": m.suffix.lower()} for m in materials],
        "skipped": skips,
        "errors": errors,
    }
    write_json(work_dir / "input_inventory.json", inventory)

    skeleton = result_skeleton(case_id, work_dir, output_dir)
    result_path = output_dir / "result.json"
    if result_path.is_file():
        existing, err = read_json(result_path)
        merged, _ = merge_result(existing, skeleton)
        merged = log_change(merged, "init", "idempotent merge; filled fields preserved")
    else:
        merged = log_change(skeleton, "init", "skeleton written")
    merged["sources"] = [{"path": str(m), "sha256": sha256_file(m)} for m in materials]
    merged["render_record"]["source_files"] = [str(m) for m in materials]
    merged["runtime"]["started_at"] = merged["runtime"].get("started_at") or now_iso()
    if case_err or task_err:
        merged["degradations"] = merged.get("degradations", []) + [{"step": "init", "detail": e} for e in errors]
    write_json(result_path, merged)

    plan_path = work_dir / "plan.json"
    if not plan_path.is_file():
        write_json(plan_path, plan_skeleton(case_id))
    fig_path = work_dir / "figure_model.json"
    if not fig_path.is_file():
        write_json(fig_path, figure_model_skeleton(case_id))

    print(json.dumps({"case_id": case_id, "status": merged["status"],
                      "materials": len(materials), "errors": len(errors),
                      "result": str(result_path)}, ensure_ascii=False))
    return 0


def cmd_plan(args):
    path = Path(args.plan)
    plan, err = read_json(path)
    problems = []
    if err:
        problems.append(f"read_failed:{err}")
    if isinstance(plan, dict):
        if plan.get("schema") != PLAN_SCHEMA:
            problems.append("schema_tag_mismatch")
        for p in plan.get("pages", []) or []:
            for field in ("index", "title", "claim", "role", "layout_key"):
                if p.get(field) in (None, ""):
                    problems.append(f"page[{p.get('index')}].{field}_missing")
            if p.get("role") not in PAGE_ROLES:
                problems.append(f"page[{p.get('index')}].role_unknown:{p.get('role')}")
            if not isinstance(p.get("refs"), list):
                problems.append(f"page[{p.get('index')}].refs_not_list")
    else:
        problems.append("plan_not_dict")
    print(json.dumps({"plan": str(path), "schema": (plan or {}).get("schema") if isinstance(plan, dict) else None,
                      "pages": len((plan or {}).get("pages", [])), "problems": problems}, ensure_ascii=False))
    return 0 if not problems else 2


def cmd_figures(args):
    work_dir, input_dir = Path(args.work), Path(args.input)
    _, _, _, _, materials, _ = discover_input(input_dir)
    case, _ = read_json(input_dir / "case.json")
    case_id = extract_case_id(case) or (input_dir.name if input_dir.is_dir() else "unknown")
    model = build_figure_model(work_dir, materials, case_id)
    write_json(work_dir / "figure_model.json", model)
    print(json.dumps({"figures": len(model["figures"]),
                      "with_dims": sum(1 for f in model["figures"] if f["native_size"]),
                      "out": str(work_dir / "figure_model.json")}, ensure_ascii=False))
    return 0


def cmd_evidence(args):
    plan_path = Path(args.plan)
    work_dir, output_dir = Path(args.work), Path(args.output)
    evidence_map, problems = build_evidence_map(plan_path, work_dir)
    result_path = output_dir / "result.json"
    result, err = read_json(result_path)
    if err or not isinstance(result, dict):
        result = result_skeleton("unknown", work_dir, output_dir)
    plan, plan_err = read_json(plan_path)
    result["evidence_map"] = evidence_map
    result["presentation"]["page_plan"] = (
        plan.get("pages", []) if not plan_err and isinstance(plan, dict) else []
    )
    if problems:
        result["uncertainty"] = list(result.get("uncertainty", [])) + [
            {"code": "unresolved_ref", **p} for p in problems
        ]
    result = log_change(result, "evidence", f"{len(evidence_map)} pages, {len(problems)} unresolved refs")
    write_json(result_path, result)
    print(json.dumps({"pages": len(evidence_map), "unresolved": len(problems)}, ensure_ascii=False))
    return 0


def cmd_verify(args):
    output_dir, work_dir = Path(args.output), Path(args.work)
    report = {"checks": [], "passed": 0, "failed": 0, "warnings": 0}
    def check(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        if ok:
            report["passed"] += 1
        else:
            report["failed"] += 1

    result_path = output_dir / "result.json"
    result, err = read_json(result_path)
    check("result.json_exists", result_path.is_file() and result is not None, f"err={err}")
    if isinstance(result, dict):
        check("result.schema", result.get("schema") == RESULT_SCHEMA)
        check("result.case_id", bool(result.get("case_id")))
        check("result.status_valid", result.get("status") in STATUSES, str(result.get("status")))
        for k in ("presentation", "evidence_map", "render_record", "sources", "uncertainty", "change_log"):
            check(f"result.{k}", k in result, "" if k in result else "missing")

    for name, schema in (("plan.json", PLAN_SCHEMA), ("figure_model.json", FIG_SCHEMA)):
        obj, e = read_json(work_dir / name)
        check(f"{name}_exists", obj is not None, f"err={e}")
        if isinstance(obj, dict):
            check(f"{name}_schema", obj.get("schema") == schema, str(obj.get("schema")))

    pres = result.get("presentation", {}) if isinstance(result, dict) else {}
    pptx = pres.get("pptx_path")
    if pptx:
        info = inspect_pptx(Path(pptx))
        check("pptx_zip", info.get("ok"), info.get("error", ""))
        if info.get("ok"):
            check("pptx_master", info["slide_masters"] >= 1, f"{info['slide_masters']} master(s)")
            check("pptx_layout", info["slide_layouts"] >= 1, f"{info['slide_layouts']} layout(s)")
            check("pptx_slide_count", info["slides"] >= 1, f"{info['slides']} slides")
            check("pptx_notes", info["notes"] >= 1, f"{info['notes']} notes slides")
    else:
        report["warnings"] += 1
        report["checks"].append({"name": "pptx_present", "ok": False, "detail": "pptx_path null (partial only)"})

    pdf = pres.get("pdf_path")
    if pdf:
        info = inspect_pdf(Path(pdf))
        check("pdf_magic", info.get("ok"), info.get("magic", info.get("error", "")))
    for png in pres.get("png_paths", []) or []:
        check(f"png:{Path(png).name}", inspect_png(Path(png)).get("ok"))

    write_json(work_dir / "verify_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 2


def cmd_finalize(args):
    output_dir, work_dir = Path(args.output), Path(args.work)
    result_path = output_dir / "result.json"
    result, err = read_json(result_path)
    if err or not isinstance(result, dict):
        result = result_skeleton("unknown", work_dir, output_dir)
    pres = result.get("presentation", {})
    pptx, pdf = pres.get("pptx_path"), pres.get("pdf_path")
    has_evidence = bool(result.get("evidence_map"))
    if pptx and pdf:
        status = "ok"
    elif pptx or pdf or has_evidence:
        status = "partial"
    elif not (work_dir / "input_inventory.json").is_file():
        status = "insufficient_evidence"
    else:
        status = "partial"
    result["status"] = status
    result["runtime"]["finished_at"] = now_iso()
    result = log_change(result, "finalize", f"status -> {status}")
    write_json(result_path, result)
    print(json.dumps({"status": status, "result": str(result_path)}, ensure_ascii=False))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="academic-slides-evidence-pipeline")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    common(sub.add_parser("init", help="发现输入 + 落 result.json 骨架"))
    common(sub.add_parser("figures", help="生成 figure_model.json"))
    sp = sub.add_parser("plan", help="校验 plan.json")
    sp.add_argument("--plan", default="/app/work/plan.json")
    sp = sub.add_parser("evidence", help="由 refs 生成 evidence_map")
    common(sp)
    sp.add_argument("--plan", default="/app/work/plan.json")
    common(sub.add_parser("verify", help="校验产物完整性"))
    common(sub.add_parser("finalize", help="收敛 status 并落 change_log"))
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {
        "init": cmd_init,
        "plan": cmd_plan,
        "figures": cmd_figures,
        "evidence": cmd_evidence,
        "verify": cmd_verify,
        "finalize": cmd_finalize,
    }
    try:
        return handlers[args.cmd](args)
    except Exception as e:  # 绝不因单步失败而崩溃：打印并给出非零退出码
        print(json.dumps({"error": f"{type(e).__name__}:{e}"}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
