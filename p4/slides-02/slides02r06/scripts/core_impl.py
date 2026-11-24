"""core_impl.py — traceable-slide-builder 标准库实现。

被 main.py 与 selftest.py 复用，只依赖 Python 标准库。职责：
- 容错读写 JSON（BOM / 坏编码 / 坏 JSON 降级，不崩溃）
- 扫描 /input 材料（单文件 2MB 上限）
- 生成 result.json 骨架与结构化中间产物空骨架
- 增量记录（record）到 plan.json / figure_model.json / evidence_map.json
- 逐字 quote 核对、中间产物校验、pptx/pdf 结构校验
- 合并中间产物写入终版 result.json
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024

RESERVED_RESULT_KEYS = [
    "status", "sources", "uncertainty", "pending_human",
    "errors", "degradations", "change_log", "runtime",
]

ALLOWED_BINDING = {"redraw", "crop_cite", "bitmap_no_data"}
ALLOWED_ROLES = {
    "title", "motivation", "problem", "method", "result",
    "analysis", "limitation", "conclusion", "appendix",
}

_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# 基础 IO
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path):
    """读取 JSON，容忍 BOM / 坏编码 / 坏 JSON；失败返回 None 而非抛异常。"""
    if not path or not os.path.isfile(path):
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return json.load(fh)
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            continue
    return None


def write_json(path, obj):
    """原子写 JSON（先写临时文件再替换），目录不存在时自动创建。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def read_text_file(path, max_bytes=MAX_SOURCE_FILE_BYTES):
    """读取文本文件，容忍 BOM / 坏编码；超限只读前 max_bytes 字节。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(max_bytes) if size > max_bytes else fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    return None


# ---------------------------------------------------------------------------
# 输入扫描
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """扫描输入目录，返回文件清单（含大小与相对路径），跳过目录。"""
    out = []
    if not os.path.isdir(input_dir):
        return out
    for root, _dirs, files in os.walk(input_dir):
        for name in files:
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            out.append({
                "path": full,
                "rel": os.path.relpath(full, input_dir),
                "size": size,
                "ext": os.path.splitext(name)[1].lower(),
            })
    out.sort(key=lambda x: x["rel"])
    return out


# ---------------------------------------------------------------------------
# 骨架与中间产物
# ---------------------------------------------------------------------------

def _reserved_default(key):
    if key in ("sources", "pending_human", "errors", "degradations", "change_log"):
        return []
    if key in ("uncertainty", "runtime"):
        return {}
    return None


def scaffold_result(case_id, output_contract):
    """生成 result.json 骨架：case_id + 全部契约字段(null) + 保留字段。"""
    result = {"case_id": str(case_id), "status": "partial"}
    if isinstance(output_contract, dict):
        for key in output_contract:
            result.setdefault(key, None)
    for key in RESERVED_RESULT_KEYS:
        result.setdefault(key, _reserved_default(key))
    return result


def empty_case_summary(case_id, contract_keys, files):
    return {
        "schema_version": "1.0",
        "case_id": str(case_id),
        "contract_keys": list(contract_keys or []),
        "input_files": files,
        "constraints": {
            "audience": None,
            "time_budget_min": None,
            "slide_count": None,
            "template": None,
            "language": None,
        },
        "extraction_notes": [],
    }


def empty_plan():
    return {"schema_version": "1.0", "title": None, "pages": []}


def empty_figure_model():
    return {
        "schema_version": "1.0",
        "redraw_policy": (
            "only redraw when source numbers are verbatim-quoted; "
            "otherwise crop original or cite-only"
        ),
        "figures": [],
    }


def empty_evidence_map():
    return {"schema_version": "1.0", "evidence": []}


def empty_version_records():
    return {"schema_version": "1.0", "records": []}


# ---------------------------------------------------------------------------
# 增量记录
# ---------------------------------------------------------------------------

_RECORD_FILES = {
    "evidence": "evidence_map.json",
    "figure": "figure_model.json",
    "plan": "plan.json",
}


def record(kind, data, work_dir):
    """把一条记录写入对应中间产物，返回写入路径。"""
    fname = _RECORD_FILES.get(kind)
    if not fname:
        raise ValueError("kind 必须是 evidence|figure|plan 之一，得到 %r" % (kind,))
    path = os.path.join(work_dir, fname)
    obj = load_json(path)
    if kind == "plan":
        obj = obj or empty_plan()
        pages = obj.setdefault("pages", [])
        index = data.get("index")
        if index is None:
            raise ValueError("plan 记录需要 index 字段")
        existing = next((p for p in pages if p.get("index") == index), None)
        if existing:
            existing.update(data)
        else:
            pages.append(dict(data))
        pages.sort(key=lambda p: p.get("index", 0))
    elif kind == "evidence":
        obj = obj or empty_evidence_map()
        obj.setdefault("evidence", []).append(dict(data))
    elif kind == "figure":
        obj = obj or empty_figure_model()
        obj.setdefault("figures", []).append(dict(data))
    write_json(path, obj)
    return path


# ---------------------------------------------------------------------------
# 文本规范化与逐字核对
# ---------------------------------------------------------------------------

def normalize_ws(text):
    if text is None:
        return ""
    return _WS.sub(" ", str(text)).strip()


def verify_quotes(source_text, evidence_list):
    """校验每项证据的 quote 是否逐字出现在源文（空白归一化后比对）。"""
    norm_source = normalize_ws(source_text)
    mismatches = []
    for ev in evidence_list or []:
        quote = ev.get("quote")
        if not quote:
            continue
        if normalize_ws(quote) and normalize_ws(quote) not in norm_source:
            mismatches.append({
                "id": ev.get("id"),
                "claim": ev.get("claim"),
                "quote": quote,
                "loc": ev.get("loc"),
            })
    return mismatches


# ---------------------------------------------------------------------------
# 中间产物校验
# ---------------------------------------------------------------------------

def validate_plan(plan):
    gaps = []
    pages = (plan or {}).get("pages") or []
    if not pages:
        gaps.append(("error", "plan.json", "pages 为空，未形成页计划"))
    for page in pages:
        idx = page.get("index")
        if not page.get("role"):
            gaps.append(("error", "plan page %s" % idx, "缺少 narrative role"))
        elif page.get("role") not in ALLOWED_ROLES:
            gaps.append(("warning", "plan page %s" % idx, "role %r 不在预设集合" % page.get("role")))
        if not (page.get("claim") or page.get("bullets")):
            gaps.append(("error", "plan page %s" % idx, "缺少 claim 且无 bullets"))
        for fig in (page.get("figures") or []):
            if not fig:
                gaps.append(("warning", "plan page %s" % idx, "figures 含空引用"))
    return gaps


def validate_figure_model(fm):
    gaps = []
    figures = (fm or {}).get("figures") or []
    for fig in figures:
        ref = fig.get("ref") or fig.get("id") or "?"
        if not fig.get("locator"):
            gaps.append(("error", "figure %s" % ref, "缺少 locator"))
        if not fig.get("caption_quote") and fig.get("caption_unavailable") is not True:
            gaps.append(("warning", "figure %s" % ref, "缺少 caption_quote（仅允许显式 caption_unavailable=true）"))
        binding = fig.get("data_binding")
        if binding and binding not in ALLOWED_BINDING:
            gaps.append(("error", "figure %s" % ref, "data_binding %r 不在 %s" % (binding, sorted(ALLOWED_BINDING))))
    return gaps


def validate_evidence(em):
    gaps = []
    evidence = (em or {}).get("evidence") or []
    for ev in evidence:
        eid = ev.get("id") or "?"
        if not ev.get("loc"):
            gaps.append(("error", "evidence %s" % eid, "缺少 loc"))
        if not ev.get("quote"):
            gaps.append(("error", "evidence %s" % eid, "缺少逐字 quote"))
    return gaps


# ---------------------------------------------------------------------------
# 产物检测与结构校验
# ---------------------------------------------------------------------------

def detect_artifacts(output_dir):
    """检测 /app/output 下的主产物与预览文件，返回文件名（相对 output）。"""
    art = {"presentation_file": None, "preview_files": [], "speaker_notes": None}
    if not os.path.isdir(output_dir):
        return art
    for name in sorted(os.listdir(output_dir)):
        full = os.path.join(output_dir, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        lower = name.lower()
        if ext == ".pptx" and art["presentation_file"] is None:
            art["presentation_file"] = name
        elif ext in (".pdf", ".png", ".jpg", ".jpeg", ".svg"):
            art["preview_files"].append(name)
        elif ext in (".md", ".txt") and ("note" in lower or "speaker" in lower):
            if art["speaker_notes"] is None:
                art["speaker_notes"] = name
    return art


def check_pptx(path):
    """用 zipfile 结构校验 pptx（不依赖 python-pptx）。"""
    msgs = []
    if not path or not os.path.isfile(path):
        return {"ok": False, "path": path, "messages": ["文件不存在"], "slide_count": None}
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            has_ct = any(n.endswith("[Content_Types].xml") for n in names)
            slides = sorted(n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n))
            if not has_ct:
                msgs.append("缺少 [Content_Types].xml，可能不是合法 PPTX")
            if not slides:
                msgs.append("未发现 ppt/slides/slide*.xml")
            return {"ok": has_ct and bool(slides), "path": path, "messages": msgs, "slide_count": len(slides)}
    except (zipfile.BadZipFile, OSError) as exc:
        return {"ok": False, "path": path, "messages": ["ZIP 打开失败: %s" % exc], "slide_count": None}


def check_pdf(path):
    msgs = []
    if not path or not os.path.isfile(path):
        return {"ok": False, "path": path, "messages": ["文件不存在"]}
    try:
        with open(path, "rb") as fh:
            head = fh.read(5)
        ok = head.startswith(b"%PDF-")
        msgs.append("头部 %PDF- 正常" if ok else "头部不以 %PDF- 开头")
        return {"ok": ok, "path": path, "messages": msgs}
    except OSError as exc:
        return {"ok": False, "path": path, "messages": [str(exc)]}


# ---------------------------------------------------------------------------
# sources 推导与终版合并
# ---------------------------------------------------------------------------

def derive_sources(evidence_map, figure_model):
    sources = []
    seen = set()

    def add(ref, loc, kind, used_in):
        key = (str(ref), str(loc), str(kind))
        if key in seen:
            return
        seen.add(key)
        sources.append({"ref": ref, "loc": loc, "kind": kind, "used_in": used_in})

    for ev in (evidence_map or {}).get("evidence") or []:
        add(ev.get("ref") or ev.get("loc"), ev.get("loc"), ev.get("kind") or "claim", [ev.get("page")])
    for fig in (figure_model or {}).get("figures") or []:
        add(fig.get("ref"), fig.get("locator"), "figure", fig.get("used_in_pages") or [])
    return sources


def fill_if_none(obj, key, value):
    if obj.get(key) is None:
        obj[key] = value


def finalize(result, plan, figure_model, evidence_map, artifacts, status):
    """把中间产物合并进 result.json，保留契约字段，覆盖保留字段。"""
    result["status"] = status
    fill_if_none(result, "presentation_file", artifacts.get("presentation_file"))
    fill_if_none(result, "preview_files", artifacts.get("preview_files"))
    fill_if_none(result, "speaker_notes", artifacts.get("speaker_notes"))
    fill_if_none(result, "page_plan", plan)
    fill_if_none(result, "evidence_map", evidence_map)
    result["figure_model"] = figure_model
    result["sources"] = derive_sources(evidence_map, figure_model)
    return result


# ---------------------------------------------------------------------------
# 终检
# ---------------------------------------------------------------------------

def run_validate(input_dir, work_dir, output_dir):
    """执行终检并写 self_check.json；返回 (self_check, passed)。"""
    result = load_json(os.path.join(output_dir, "result.json"))
    plan = load_json(os.path.join(work_dir, "plan.json")) or {}
    fm = load_json(os.path.join(work_dir, "figure_model.json")) or {}
    em = load_json(os.path.join(work_dir, "evidence_map.json")) or {}
    qc = load_json(os.path.join(work_dir, "quote_check.json"))

    items = []
    if not result:
        items.append({"check": "result.json", "status": "fail", "message": "缺失 result.json"})
    else:
        if not result.get("case_id"):
            items.append({"check": "result.json", "status": "fail", "message": "缺少 case_id"})
        if not result.get("status"):
            items.append({"check": "result.json", "status": "fail", "message": "缺少 status"})

    for severity, where, msg in validate_plan(plan) + validate_figure_model(fm) + validate_evidence(em):
        items.append({
            "check": "intermediate:%s" % where,
            "status": "fail" if severity == "error" else "warn",
            "message": msg,
        })

    art = detect_artifacts(output_dir)
    if art["presentation_file"]:
        pptx_check = check_pptx(os.path.join(output_dir, art["presentation_file"]))
        items.append({
            "check": "artifact:pptx",
            "status": "pass" if pptx_check["ok"] else "fail",
            "message": pptx_check["messages"] + ["slide_count=%s" % pptx_check["slide_count"]],
        })
    else:
        items.append({"check": "artifact:pptx", "status": "fail", "message": "无 .pptx 演示文件"})

    pdfs = [p for p in art["preview_files"] if p.lower().endswith(".pdf")]
    if pdfs:
        for p in pdfs:
            pdf_check = check_pdf(os.path.join(output_dir, p))
            items.append({
                "check": "artifact:pdf:%s" % p,
                "status": "pass" if pdf_check["ok"] else "fail",
                "message": pdf_check["messages"],
            })
    else:
        items.append({"check": "artifact:pdf", "status": "warn", "message": "无 PDF 预览"})

    if qc:
        for mismatch in qc.get("mismatches") or []:
            items.append({"check": "verbatim-quote", "status": "fail", "message": mismatch})

    errors = [i for i in items if i["status"] == "fail"]
    warnings = [i for i in items if i["status"] == "warn"]
    self_check = {
        "schema_version": "1.0",
        "generated": now_iso(),
        "checks": items,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "passed": len(errors) == 0,
    }
    write_json(os.path.join(work_dir, "self_check.json"), self_check)
    return self_check, len(errors) == 0
