# -*- coding: utf-8 -*-
"""core_impl.py —— 科研选题与创新性评估 Skill 的核心实现（仅 Python 标准库）。

职责：
  1) 材料发现与容错读取（文本 / JSON / PDF 抽取 / 办公二进制降级）；
  2) 结果骨架（Phase 0）与输入清单；
  3) 证据抽取与 sources 刷新（Phase 1）；
  4) fragment 深合并 + 契约校验 + report.md 生成（Phase 4）。

本文件不含命令行入口，入口见 main.py。所有函数均为纯函数或仅依赖
标准库的确定性实现；重复运行同一输入得到相同输出。
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time

# ---------------------------------------------------------------------------
# 常量与契约
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 2 * 1024 * 1024          # 单文件 2MB 上限，超限跳过并记录
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")
STATUS_VALUES = {"ok", "partial", "insufficient_evidence", "input_error", "degraded"}
STRENGTH_VALUES = {"reported", "inferred", "assumed", "missing", "conflicting", "unverifiable"}

# 材料类别（按扩展名识别，不按标签猜测内容）
TEXT_EXT = {".md", ".txt", ".tex", ".rst", ".csv", ".tsv", ".xml", ".html", ".htm",
            ".json", ".jsonl", ".yaml", ".yml", ".bib", ".ris", ".enl"}
PDF_EXT = {".pdf"}
OFFICE_EXT = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".ods", ".odp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp", ".webp", ".svg"}

# 列表型顶层字段：fragment 合并时追加（而非覆盖）
LIST_FIELDS = {"evidence_matrix", "conflicts", "sources", "uncertainty", "limitations",
               "pending_human", "errors", "degradations", "change_log"}

# result.json 契约默认字段（四项交付 + 证据 + 来源 + 不确定性 + 限制 + 运行记录）
DEFAULT_FIELDS = {
    "research_question": None,
    "search_protocol": None,
    "prior_work": None,
    "novelty": None,
    "feasibility": None,
    "evidence_matrix": [],
    "conflicts": [],
    "synthesis": None,
    "sources": [],
    "uncertainty": [],
    "limitations": [],
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "change_log": [],
    "runtime": {},
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_iso():
    """UTC ISO 时间戳（去微秒，确定性友好）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sanitize_json(obj):
    """递归清理不可 JSON 化 / NaN / Infinity 的值，改为 None 或字符串。"""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = k if isinstance(k, str) else str(k)
            out[key] = _sanitize_json(v)
        return out
    return str(obj)


def contains_nonfinite(obj):
    """递归检测 NaN / Infinity，用于契约校验。"""
    if isinstance(obj, float):
        return math.isnan(obj) or math.isinf(obj)
    if isinstance(obj, (list, tuple)):
        return any(contains_nonfinite(x) for x in obj)
    if isinstance(obj, dict):
        return any(contains_nonfinite(v) for v in obj.values())
    return False


def write_json(path, obj):
    """原子化写 JSON（先写临时文件再替换），保证中途失败不留下半成品。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_sanitize_json(obj), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_text_file(path):
    """容错读文本：按 utf-8-sig→utf-8→gb18030→latin-1 依次尝试，返回 (text, error)。"""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, "stat_failed: %s" % exc
    if size > MAX_FILE_BYTES:
        return None, "oversize:%d" % size
    last = None
    for enc in TEXT_ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), None
        except (UnicodeDecodeError, UnicodeError) as exc:
            last = "%s: %s" % (enc, exc)
        except OSError as exc:
            return None, "read_failed: %s" % exc
    return None, "decode_failed: %s" % (last or "unknown")


def load_json_file(path):
    """容错读 JSON（含 JSONL 首行回退），返回 (obj, error)。"""
    text, err = read_text_file(path)
    if err is not None:
        return None, err
    text = text.strip()
    if not text:
        return None, "empty_file"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        # JSONL：逐行解析为列表
        lines = [ln for ln in text.splitlines() if ln.strip()]
        records, perr = [], None
        for ln in lines:
            try:
                records.append(json.loads(ln))
            except json.JSONDecodeError as e2:
                perr = str(e2)
        if records:
            return records, ("jsonl_partial:%s" % perr) if perr else None
        return None, "bad_json: %s" % exc


def classify_file(name):
    """按扩展名识别类别，返回 (kind, is_text)。"""
    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_EXT:
        return ("json" if ext in {".json", ".jsonl"} else "text"), True
    if ext in PDF_EXT:
        return "pdf", False
    if ext in OFFICE_EXT:
        return "office", False
    if ext in IMAGE_EXT:
        return "image", False
    return "other", False


# ---------------------------------------------------------------------------
# 材料发现与 PDF 抽取
# ---------------------------------------------------------------------------

def _safe_rel(root, path):
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    return rel


def pdftotext_extract(pdf_path, work_dir):
    """用 pdftotext 抽取 PDF 文本，返回 (txt_path, error)。缺失工具时返回 degradation。"""
    tool = shutil.which("pdftotext")
    if tool is None:
        return None, "pdftotext_unavailable"
    out_dir = os.path.join(work_dir, "extracted")
    os.makedirs(out_dir, exist_ok=True)
    base = hashlib.sha1(pdf_path.encode("utf-8")).hexdigest()[:16]
    out = os.path.join(out_dir, base + ".txt")
    try:
        proc = subprocess.run(
            [tool, "-layout", pdf_path, out],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "pdftotext_error: %s" % exc
    if proc.returncode != 0:
        return None, "pdftotext_rc=%d: %s" % (proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        return None, "pdftotext_empty"
    return out, None


def discover_inputs(input_dir, work_dir):
    """扫描输入目录，返回 inventory 结构；不假设固定文件名。"""
    inventory = {
        "input_dir": os.path.abspath(input_dir),
        "exists": os.path.isdir(input_dir),
        "case_json_path": None,
        "task_md_path": None,
        "materials": [],
        "skipped": [],
        "errors": [],
    }
    if not inventory["exists"]:
        inventory["errors"].append("input_dir_missing")
        return inventory

    for root, _dirs, files in os.walk(input_dir):
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = _safe_rel(input_dir, path)
            lower = name.lower()
            if lower == "case.json" and rel == "case.json":
                inventory["case_json_path"] = path
            if lower == "task.md" and (rel == "task.md" or rel.endswith("/task.md")):
                inventory["task_md_path"] = path

            try:
                size = os.path.getsize(path)
            except OSError as exc:
                inventory["skipped"].append({"path": rel, "reason": "stat_failed: %s" % exc})
                continue
            if size > MAX_FILE_BYTES:
                inventory["skipped"].append({"path": rel, "reason": "oversize:%d" % size})
                continue

            kind, is_text = classify_file(name)
            item = {
                "source_id": rel,
                "path": path,
                "kind": kind,
                "is_text": is_text,
                "size_bytes": size,
                "extracted_text": None,
                "text_error": None,
                "role": None,
            }
            if kind == "pdf":
                txt, terr = pdftotext_extract(path, work_dir)
                if terr:
                    item["text_error"] = terr
                else:
                    item["extracted_text"] = txt
            inventory["materials"].append(item)

    # case.json / task.md 若超大或已进入 materials，不影响：单独定位其绝对路径即可。
    return inventory


def case_id_from(case_obj, input_dir):
    """case_id 优先取 case.json 的标识，缺失时按输入目录哈希回退。"""
    if isinstance(case_obj, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "caseId"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    digest = hashlib.sha1(os.path.abspath(input_dir).encode("utf-8")).hexdigest()[:12]
    return "unresolved-" + digest


def output_contract_of(case_obj):
    """提取 output_contract；无则返回空 dict（契约字段由默认 schema 兜底）。"""
    if isinstance(case_obj, dict) and isinstance(case_obj.get("output_contract"), dict):
        return case_obj["output_contract"]
    return {}


# ---------------------------------------------------------------------------
# Phase 0：结果骨架
# ---------------------------------------------------------------------------

def build_skeleton(input_dir, output_dir, work_dir):
    """先落盘合法 partial 骨架；任何中途异常都至少有部分结果。"""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    case_obj, case_err = None, "case.json_missing"
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        case_obj, case_err = load_json_file(case_path)

    errors, degradations = [], []
    if case_err:
        errors.append({"step": "phase0", "detail": "case.json: %s" % case_err})

    contract = output_contract_of(case_obj)
    case_id = case_id_from(case_obj, input_dir)

    skeleton = {
        "case_id": case_id,
        "status": "partial",
        "output_contract": contract,
    }
    # 深拷贝默认字段，避免多次运行共享同一列表/字典引用
    def _fresh(val):
        if isinstance(val, list):
            return list(val)
        if isinstance(val, dict):
            return dict(val)
        return val
    skeleton.update({k: _fresh(v) for k, v in DEFAULT_FIELDS.items()})
    # 契约键回填到顶层：缺值给 null/[]，不改名不删未知键
    for key in contract:
        if key not in skeleton:
            skeleton[key] = None

    if not os.path.isdir(input_dir):
        skeleton["status"] = "input_error"
        errors.append({"step": "phase0", "detail": "input_dir_missing"})
    elif case_err and not os.path.isfile(case_path):
        skeleton["status"] = "input_error"
    elif case_err and os.path.isfile(case_path):
        # 坏 JSON：仍算 input_error，但已尽力产出骨架
        skeleton["status"] = "input_error"

    skeleton["runtime"] = {
        "started_at": now_iso(),
        "phase0_at": now_iso(),
        "phases": {"skeleton": "done"},
    }
    skeleton["change_log"] = [{
        "at": now_iso(), "step": "phase0", "action": "skeleton_written",
        "detail": "partial 骨架落盘，status=%s" % skeleton["status"],
    }]
    skeleton["errors"] = errors
    skeleton["degradations"] = degradations

    write_json(os.path.join(output_dir, "result.json"), skeleton)
    write_json(os.path.join(work_dir, "input_manifest.json"),
               {"case_id": case_id, "input_dir": os.path.abspath(input_dir),
                "case_json_status": case_err or "ok", "written_at": now_iso()})
    return skeleton


# ---------------------------------------------------------------------------
# Phase 1：证据抽取与 sources 刷新
# ---------------------------------------------------------------------------

def build_evidence(input_dir, output_dir, work_dir):
    """材料发现 + PDF 抽字 + sources 刷新；缺材料时降级 insufficient_evidence。"""
    out_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(out_path):
        result, _ = load_json_file(out_path)
        if not isinstance(result, dict):
            result = build_skeleton(input_dir, output_dir, work_dir)
    else:
        result = build_skeleton(input_dir, output_dir, work_dir)

    inventory = discover_inputs(input_dir, work_dir)
    write_json(os.path.join(work_dir, "material_inventory.json"), inventory)

    sources, degradations, errors = [], list(result.get("degradations") or []), list(result.get("errors") or [])
    for m in inventory["materials"]:
        src = {
            "source_id": m["source_id"],
            "path": m["source_id"],
            "kind": m["kind"],
            "size_bytes": m["size_bytes"],
            "text_available": bool(m["is_text"] or m.get("extracted_text")),
            "extracted_text": m.get("extracted_text"),
            "text_error": m.get("text_error"),
        }
        if m.get("text_error"):
            src["text_available"] = False
            degradations.append({
                "step": "phase1",
                "source_id": m["source_id"],
                "detail": "text_unavailable: %s" % m["text_error"],
            })
        sources.append(src)

    for skip in inventory["skipped"]:
        degradations.append({"step": "phase1", "source_id": skip["path"], "detail": "skipped: %s" % skip["reason"]})
    for e in inventory["errors"]:
        errors.append({"step": "phase1", "detail": e})

    result["sources"] = sources
    result["degradations"] = degradations
    result["errors"] = errors

    readable = [s for s in sources if s.get("text_available")]
    has_task = inventory["task_md_path"] is not None
    if result.get("status") not in {"input_error"}:
        if not readable and not has_task:
            result["status"] = "insufficient_evidence"
            result["limitations"] = list(result.get("limitations") or []) + [{
                "scope": "evidence", "cause": "no_readable_material",
                "severity": "blocking", "mitigation": "request readable materials",
                "affects": ["research_question", "prior_work", "novelty", "feasibility"],
            }]
        elif any(d.get("detail", "").startswith(("text_unavailable", "pdftotext_unavailable"))
                 for d in degradations):
            result["status"] = "degraded"
        else:
            result["status"] = "partial" if result.get("status") in {"partial", None} else result.get("status", "partial")

    rt = result.get("runtime") or {}
    rt["phase1_at"] = now_iso()
    rt["phases"] = dict((rt.get("phases") or {})); rt["phases"]["evidence"] = "done"
    result["runtime"] = rt
    result["change_log"] = list(result.get("change_log") or []) + [{
        "at": now_iso(), "step": "phase1", "action": "sources_refreshed",
        "detail": "%d source(s), %d readable" % (len(sources), len(readable)),
    }]

    write_json(out_path, result)
    return result


# ---------------------------------------------------------------------------
# Phase 4：fragment 合并 + 校验 + report
# ---------------------------------------------------------------------------

def _deep_merge(base, override):
    """深合并：dict 递归合并；list 追加；标量/None 覆盖。"""
    if isinstance(override, dict):
        if not isinstance(base, dict):
            base = {}
        for k, v in override.items():
            base[k] = _deep_merge(base.get(k), v)
        return base
    if isinstance(override, list):
        if not isinstance(base, list):
            base = []
        return base + list(override)
    return override


def load_fragments(work_dir):
    """读取 /work/fragments/*.json，按文件名排序；坏片段记录到 errors 不崩溃。"""
    frag_dir = os.path.join(work_dir, "fragments")
    frags, errors = [], []
    if os.path.isdir(frag_dir):
        for name in sorted(os.listdir(frag_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(frag_dir, name)
            obj, err = load_json_file(path)
            if err:
                errors.append({"step": "phase4", "fragment": name, "detail": err})
                continue
            if isinstance(obj, dict):
                frags.append((name, obj))
    return frags, errors


def validate_result(obj):
    """契约校验，返回 problems 列表（blocking / warning）。"""
    problems = []
    if not isinstance(obj, dict):
        return [{"level": "blocking", "detail": "result not a dict"}]
    if not obj.get("case_id"):
        problems.append({"level": "blocking", "detail": "missing case_id"})
    if obj.get("status") not in STATUS_VALUES:
        problems.append({"level": "blocking", "detail": "invalid status: %r" % obj.get("status")})
    if not isinstance(obj.get("output_contract"), dict):
        problems.append({"level": "warning", "detail": "output_contract missing or not dict"})
    if contains_nonfinite(obj):
        problems.append({"level": "blocking", "detail": "contains NaN/Infinity"})
    for key in ("sources", "uncertainty", "limitations", "pending_human", "errors",
                "degradations", "change_log"):
        if not isinstance(obj.get(key), list):
            problems.append({"level": "warning", "detail": "field %s missing or not list" % key})
    for key in ("research_question", "search_protocol", "prior_work", "novelty",
                "feasibility", "synthesis"):
        if obj.get(key) is None:
            problems.append({"level": "warning", "detail": "field %s not filled" % key})
    # 证据矩阵定位非空
    ev = obj.get("evidence_matrix") or []
    if isinstance(ev, list):
        for i, e in enumerate(ev):
            if not isinstance(e, dict) or not e.get("locator") or not e.get("quote"):
                problems.append({"level": "warning", "detail": "evidence_matrix[%d] missing locator/quote" % i})
    return problems


def render_report(result):
    """生成人类可读 report.md 摘要。"""
    lines = []
    lines.append("# 科研选题与创新性评估报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    lines.append("- 生成时间: %s" % now_iso())
    lines.append("")
    for key in ("research_question", "search_protocol", "prior_work", "novelty", "feasibility"):
        val = result.get(key)
        lines.append("## %s" % key)
        if val is None:
            lines.append("_未填写（材料不足或未完成）_")
        elif isinstance(val, dict):
            for k, v in val.items():
                if k in ("decomposition", "comparison_matrix", "claims", "premises"):
                    n = len(v) if isinstance(v, list) else ("有" if v else "无")
                    lines.append("- %s: %s 项" % (k, n))
                elif isinstance(v, (str, int, float, bool)) and not isinstance(v, bool):
                    lines.append("- %s: %s" % (k, v))
        lines.append("")
    ev = result.get("evidence_matrix") or []
    lines.append("## 证据矩阵（%d 条）" % len(ev))
    for e in ev[:50]:
        if isinstance(e, dict):
            lines.append("- `%s` `%s` — %s" % (e.get("locator", "?"), e.get("strength", "?"),
                                                (e.get("quote") or "")[:120]))
    lines.append("")
    unc = result.get("uncertainty") or []
    lim = result.get("limitations") or []
    lines.append("## 不确定性与限制")
    lines.append("- uncertainty: %d 条" % len(unc))
    lines.append("- limitations: %d 条" % len(lim))
    for x in lim:
        if isinstance(x, dict):
            lines.append("  - [%s] %s → %s" % (x.get("severity", "?"), x.get("scope", "?"), x.get("affects", "?")))
    lines.append("")
    ph = result.get("pending_human") or []
    if ph:
        lines.append("## 待人工确认（%d 条）" % len(ph))
        for x in ph:
            lines.append("- %s" % (x if isinstance(x, str) else x.get("detail", str(x))))
    lines.append("")
    return "\n".join(lines) + "\n"


def finalize(input_dir, output_dir, work_dir):
    """合并 fragments → 校验 → 写最终 result.json 与 report.md。"""
    out_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(out_path):
        result, _ = load_json_file(out_path)
        if not isinstance(result, dict):
            result = build_skeleton(input_dir, output_dir, work_dir)
    else:
        result = build_skeleton(input_dir, output_dir, work_dir)

    frags, ferrs = load_fragments(work_dir)
    for _name, frag in frags:
        for key, val in frag.items():
            result[key] = _deep_merge(result.get(key), val)

    result["errors"] = list(result.get("errors") or []) + ferrs

    # 诚实状态：四项交付 + 综合未填 → partial；缺材料 → insufficient_evidence
    missing_deliverables = [k for k in ("research_question", "prior_work", "novelty", "feasibility")
                            if result.get(k) is None]
    if result.get("status") not in {"input_error", "insufficient_evidence"}:
        if missing_deliverables:
            result["status"] = "partial"
        elif result.get("status") != "degraded":
            result["status"] = "ok"

    problems = validate_result(result)
    blocking = [p for p in problems if p["level"] == "blocking"]
    warnings = [p for p in problems if p["level"] == "warning"]
    result["errors"] = list(result.get("errors") or []) + [
        {"step": "phase4", "level": p["level"], "detail": p["detail"]} for p in problems]
    if blocking:
        result["status"] = "input_error" if result.get("status") in {"partial", None} else result["status"]

    rt = result.get("runtime") or {}
    rt["phase4_at"] = now_iso()
    rt["phases"] = dict((rt.get("phases") or {})); rt["phases"]["finalize"] = "done"
    rt["problems"] = {"blocking": len(blocking), "warning": len(warnings)}
    result["runtime"] = rt
    result["change_log"] = list(result.get("change_log") or []) + [{
        "at": now_iso(), "step": "phase4", "action": "finalized",
        "detail": "merged %d fragment(s), status=%s, blocking=%d, warning=%d" % (
            len(frags), result["status"], len(blocking), len(warnings)),
    }]

    write_json(out_path, result)
    write_report = open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8")
    write_report.write(render_report(result))
    write_report.close()
    return result, 2 if blocking else 0


# ---------------------------------------------------------------------------
# 通用 CLI 处理（供 main.py 调用）
# ---------------------------------------------------------------------------

def run_phase(phase, input_dir, output_dir, work_dir):
    if phase == "skeleton":
        build_skeleton(input_dir, output_dir, work_dir)
        return 0
    if phase == "evidence":
        build_evidence(input_dir, output_dir, work_dir)
        return 0
    if phase == "finalize":
        _result, code = finalize(input_dir, output_dir, work_dir)
        return code
    raise ValueError("unknown phase: %s" % phase)
