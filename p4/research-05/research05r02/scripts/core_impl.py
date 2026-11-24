# -*- coding: utf-8 -*-
"""科研选题与创新性评估 Skill 的核心实现（仅标准库）。

本模块是与 CLI 解耦的实现层，提供：
- 容错文件读取（utf-8-sig → utf-8 → latin-1 降级，坏 JSON/坏编码不崩溃）
- 输入材料发现（>2MB 跳过并登记）
- PDF 文字抽取（pdftotext，按换页符还原页码；缺失时如实降级）
- 结果骨架、output_contract 回显、fragment 深合并（幂等）
- 契约校验、report.md 生成
- 900 s / 64 请求的阶段预算与请求台账

设计取舍（本版侧重点：时间预算与早落盘）：
1. Phase 0 必须在 180 s 内落盘一版合法的 partial result.json，之后只做增量改进。
2. finalize 是幂等的“last-chance merge”：无论还剩多少时间，都把已有 fragments
   合并进骨架，保证任何时刻磁盘上都有一份可打开的 result.json。
3. 请求台账是自报计数，用于预算纪律；官方计数由平台记录，脚本不伪造。
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 常量与阶段预算
# ---------------------------------------------------------------------------
BUDGET_TOTAL_SECONDS = 900
BUDGET_TOTAL_REQUESTS = 64
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB，超限跳过

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")

TEXT_EXTS = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".rst", ".tex", ".bib",
}

# 阶段预算：总 900 s / 64 请求，Phase 0 是硬性早落盘门。
PHASE_BUDGET = [
    {
        "id": "phase0", "name": "skeleton", "window": "0-180s",
        "seconds": 180, "requests": 3, "mandatory": True,
        "desc": "先跑 skeleton.py 落盘 partial 结果骨架与 input_manifest，禁止先分析后落盘",
    },
    {
        "id": "phase1", "name": "evidence", "window": "180-360s",
        "seconds": 180, "requests": 8, "mandatory": False,
        "desc": "发现材料、抽 PDF 文字、刷新 sources 与 material_inventory",
    },
    {
        "id": "phase2", "name": "deliverables", "window": "360-690s",
        "seconds": 330, "requests": 40, "mandatory": False,
        "desc": "按序写 01_question / 02_prior / 03_novelty / 04_feasibility 四个 fragment",
    },
    {
        "id": "phase3", "name": "synthesis", "window": "690-840s",
        "seconds": 150, "requests": 10, "mandatory": False,
        "desc": "补 05_synthesis：evidence_matrix / conflicts / uncertainty / synthesis",
    },
    {
        "id": "phase4", "name": "finalize", "window": "840-900s",
        "seconds": 60, "requests": 3, "mandatory": True,
        "desc": "finalize 深合并 fragments、契约校验、写 report.md；可多次重跑做增量修正",
    },
]


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 容错读取
# ---------------------------------------------------------------------------
def read_text(path):
    """返回 (text, encoding, degraded, error)。任何编码问题都降级而不抛异常。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, None, False, "unreadable:%s" % e
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc, False, None
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", True, "fallback_latin1"


def read_json(path):
    """返回 (obj, error, degraded)。坏 JSON 返回错误字符串而不是崩溃。"""
    text, _enc, degraded, err = read_text(path)
    if err:
        return None, err, degraded
    if text is None:
        return None, "unreadable", degraded
    try:
        return json.loads(text), None, degraded
    except (json.JSONDecodeError, ValueError) as e:
        return None, "bad_json:%s" % e, degraded
    except Exception as e:  # 极少数解析异常
        return None, "json_error:%s" % e, degraded


def write_text_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def write_json_file(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean = sanitize_nonfinite(obj)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, ensure_ascii=False, indent=2, allow_nan=False)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 材料发现与 PDF
# ---------------------------------------------------------------------------
def classify(ext):
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    return "binary_unsupported"


def list_input_files(input_dir):
    """返回 (files, oversized, error)。排序保证重复运行结果一致。"""
    files, oversized = [], []
    if not os.path.isdir(input_dir):
        return files, oversized, "input_dir_missing"
    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        names.sort()
        for name in names:
            abs_path = os.path.join(root, name)
            try:
                st = os.stat(abs_path)
            except OSError:
                continue
            rel = os.path.relpath(abs_path, input_dir).replace("\\", "/")
            if st.st_size > MAX_FILE_BYTES:
                oversized.append({"path": rel, "size": st.st_size})
                continue
            files.append({
                "path": rel,
                "abs": abs_path,
                "size": st.st_size,
                "ext": os.path.splitext(name)[1].lower(),
            })
    return files, oversized, None


def source_id(rel_path):
    s = re.sub(r"[^0-9A-Za-z_\-\.]+", "_", rel_path)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "source"


def locator_base(rel_path):
    return rel_path.replace("\\", "/")


def extract_pdf_text(abs_path):
    """优先调用 pdftotext；缺失/失败时返回 (None, reason)，绝不伪造文字。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext_unavailable"
    try:
        proc = subprocess.run(
            [exe, "-layout", abs_path, "-"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return None, "pdftotext_error:%s" % e
    if proc.returncode != 0:
        return None, "pdftotext_exit:%s:%s" % (proc.returncode, (proc.stderr or "")[:200])
    return proc.stdout, None


def extract_pdf_pages(abs_path):
    """把 pdftotext 输出按换页符切回页码，返回 (pages, error)；pages 为 [(page_no, text)]。"""
    text, err = extract_pdf_text(abs_path)
    if err:
        return None, err
    parts = text.split("\f")
    pages = [(i + 1, p.strip("\n")) for i, p in enumerate(parts)]
    while pages and not pages[-1][1].strip():
        pages.pop()
    if not pages:
        return None, "pdf_empty_text"
    return pages, None


# ---------------------------------------------------------------------------
# case_id / output_contract / 骨架
# ---------------------------------------------------------------------------
def determine_case_id(case_json, task_text, files):
    if isinstance(case_json, dict):
        for key in ("case_id", "id", "challenge_id"):
            v = case_json.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if task_text:
        m = re.search(r"challenge_id[:：]\s*[`'\"]?([A-Za-z0-9_.\-]+)", task_text)
        if m:
            return m.group(1)
    key = "|".join(sorted(f["path"] for f in files))
    return "unresolved-" + sha256_text(key)


def apply_output_contract(case_json, result):
    """output_contract 原样回显；其每个键同时回填到顶层（缺值给 null）。"""
    contract = None
    if isinstance(case_json, dict) and "output_contract" in case_json:
        contract = case_json.get("output_contract")
    result["output_contract"] = contract
    if isinstance(contract, dict):
        for key, value in contract.items():
            if key not in result:
                result[key] = value if value is not None else None
    return contract


def build_skeleton(case_id, status, started_at, started_epoch):
    return {
        "case_id": case_id,
        "status": status,
        "output_contract": None,
        "research_question": None,
        "search_protocol": None,
        "prior_work": None,
        "novelty": None,
        "feasibility": None,
        "evidence_matrix": [],
        "conflicts": [],
        "synthesis": {"conclusions": [], "research_gaps": [], "boundary_statement": None},
        "sources": [],
        "uncertainty": {"bias": [], "gaps": [], "unresolved": []},
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limits": ["phase0：骨架已落盘，四项交付与证据矩阵尚未完成"],
        "change_log": [{"step": "skeleton", "at": started_at, "detail": "partial 骨架在 phase0 预算内落盘"}],
        "runtime": {
            "started_at": started_at,
            "started_epoch": started_epoch,
            "budget_seconds": BUDGET_TOTAL_SECONDS,
            "budget_requests": BUDGET_TOTAL_REQUESTS,
            "requests_used": 0,
            "requests_used_note": "self-reported ledger; official count recorded by platform",
            "phase_budget": PHASE_BUDGET,
        },
    }


# ---------------------------------------------------------------------------
# 合并与校验
# ---------------------------------------------------------------------------
def deep_merge(base, patch):
    if isinstance(patch, dict):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                deep_merge(base[key], value)
            elif value is None:
                continue  # fragment 显式 null 不覆盖已有内容
            else:
                base[key] = value
    else:
        base = patch
    return base


def deliverables_complete(result):
    for key in ("research_question", "prior_work", "novelty", "feasibility"):
        v = result.get(key)
        if v is None:
            return False
        if isinstance(v, (dict, list, str)) and not v:
            return False
    return True


def find_nonfinite(obj, path="$"):
    issues = []
    if isinstance(obj, float):
        if not math.isfinite(obj):
            issues.append("%s:nonfinite" % path)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            issues.extend(find_nonfinite(v, "%s.%s" % (path, k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            issues.extend(find_nonfinite(v, "%s[%d]" % (path, i)))
    return issues


def sanitize_nonfinite(obj):
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: sanitize_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_nonfinite(v) for v in obj]
    return obj


def validate_result(result):
    issues = []
    if not isinstance(result, dict):
        return ["result_not_object"]
    if not result.get("case_id"):
        issues.append("missing_case_id")
    if result.get("status") not in STATUSES:
        issues.append("bad_status")
    em = result.get("evidence_matrix")
    if isinstance(em, list):
        for i, e in enumerate(em):
            if not isinstance(e, dict):
                issues.append("evidence[%d].not_object" % i)
                continue
            if not e.get("locator"):
                issues.append("evidence[%d].missing_locator" % i)
            if not e.get("quote"):
                issues.append("evidence[%d].missing_quote" % i)
    issues.extend(find_nonfinite(result))
    return issues


def _add_unique(items, item):
    if item not in items:
        items.append(item)


def write_result(result, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "result.json")
    clean = sanitize_nonfinite(result)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, ensure_ascii=False, indent=2, allow_nan=False)
    return path


# ---------------------------------------------------------------------------
# 请求台账（自报计数，用于预算纪律）
# ---------------------------------------------------------------------------
def get_ledger_count(work_dir):
    path = os.path.join(work_dir, "request_ledger.jsonl")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def bump_request(work_dir, note=""):
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, "request_ledger.jsonl")
    record = {"ts": utc_now_iso(), "note": note or "model_request"}
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return get_ledger_count(work_dir)


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def cmd_skeleton(input_dir, output_dir, work_dir):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    started_at = os.environ.get("SKILL_RUN_STARTED_AT") or utc_now_iso()
    started_epoch = time.time()

    case_json, case_err, _ = read_json(os.path.join(input_dir, "case.json"))
    task_text, _enc, _deg, task_err = read_text(os.path.join(input_dir, "task.md"))
    files, oversized, dir_err = list_input_files(input_dir)

    if dir_err:
        status = "input_error"
    elif not files and not oversized:
        status = "insufficient_evidence"
    else:
        status = "partial"

    case_id = determine_case_id(case_json, task_text or "", files)
    result = build_skeleton(case_id, status, started_at, started_epoch)

    if case_err:
        result["errors"].append({"source": "case.json", "error": case_err})
    if task_err:
        result["errors"].append({"source": "task.md", "error": task_err})
    if oversized:
        result["degradations"].append({"type": "oversized_skipped", "files": oversized})
    if dir_err:
        result["limits"].append("输入目录缺失：%s" % dir_err)

    apply_output_contract(case_json, result)
    write_result(result, output_dir)

    manifest = {
        "case_id": case_id,
        "started_at": started_at,
        "started_epoch": started_epoch,
        "input_dir": input_dir,
        "input_dir_exists": os.path.isdir(input_dir),
        "files": files,
        "oversized": oversized,
    }
    write_json_file(os.path.join(work_dir, "input_manifest.json"), manifest)

    phase_state = {
        "started_at": started_at,
        "started_epoch": started_epoch,
        "phases_done": ["phase0"],
        "last_phase": "phase0",
    }
    write_json_file(os.path.join(work_dir, "phase_state.json"), phase_state)

    print(json.dumps({
        "phase": "phase0", "status": status, "case_id": case_id,
        "result": os.path.join(output_dir, "result.json"),
        "deadline": "skeleton landed within 180s budget",
    }, ensure_ascii=False))
    return 0


def cmd_evidence(input_dir, output_dir, work_dir):
    manifest = read_json(os.path.join(work_dir, "input_manifest.json"))[0] or {}
    files = manifest.get("files")
    if not isinstance(files, list):
        files, oversized, _ = list_input_files(input_dir)
        if oversized:
            pass  # 已由 skeleton 登记
    inventory = {"materials": [], "pdf_pages_files": [], "sources": [], "degradations": []}
    readable_any = False

    for item in files:
        entry = {
            "path": item["path"], "size": item["size"], "ext": item["ext"],
            "kind": classify(item["ext"]), "readable": False,
        }
        kind = entry["kind"]
        if kind == "pdf":
            pages, err = extract_pdf_pages(item["abs"])
            if err:
                entry["text_unavailable"] = True
                inventory["degradations"].append({
                    "type": "pdf_text_unavailable", "path": item["path"], "reason": err,
                })
            else:
                stem = source_id(item["path"])[:16] + "_" + sha256_text(item["path"])
                pages_path = os.path.join(work_dir, "extracted", stem + ".pages.txt")
                parts = []
                for page_no, page_text in pages:
                    parts.append("===== PAGE %d =====\n%s" % (page_no, page_text))
                write_text_file(pages_path, "\n\n".join(parts))
                entry["readable"] = True
                entry["page_count"] = len(pages)
                entry["pages_file"] = "extracted/" + stem + ".pages.txt"
                inventory["pdf_pages_files"].append({
                    "path": item["path"], "pages_file": entry["pages_file"], "page_count": len(pages),
                })
                readable_any = True
        elif kind == "text":
            text, enc, degraded, err = read_text(item["abs"])
            if err or text is None:
                entry["readable"] = False
                inventory["degradations"].append({
                    "type": "unreadable", "path": item["path"], "reason": err or "empty",
                })
            else:
                entry["readable"] = True
                entry["encoding"] = enc
                entry["degraded_encoding"] = bool(degraded)
                entry["text_sha"] = sha256_text(text)
                readable_any = True
        else:
            entry["text_unavailable"] = True
            inventory["degradations"].append({
                "type": "binary_unsupported", "path": item["path"],
            })
        inventory["materials"].append(entry)

    for m in inventory["materials"]:
        if m.get("readable"):
            inventory["sources"].append({
                "source_id": source_id(m["path"]),
                "path": m["path"],
                "kind": m["kind"],
                "locator_base": locator_base(m["path"]),
                "pages_file": m.get("pages_file"),
            })

    write_json_file(os.path.join(work_dir, "material_inventory.json"), inventory)

    result = read_json(os.path.join(output_dir, "result.json"))[0]
    if isinstance(result, dict):
        result["sources"] = inventory["sources"]
        if not readable_any:
            result["status"] = "insufficient_evidence"
            _add_unique(result["uncertainty"]["gaps"], {
                "type": "no_readable_material",
                "detail": "输入目录存在，但没有可读的文本/PDF 材料",
            })
        if inventory["degradations"]:
            result["degradations"].extend(inventory["degradations"])
        _add_unique(result["change_log"], {
            "step": "evidence", "at": utc_now_iso(),
            "detail": "材料清单与 PDF 抽取完成，sources 已刷新",
        })
        write_result(result, output_dir)

    print(json.dumps({
        "phase": "phase1", "materials": len(inventory["materials"]),
        "readable": sum(1 for m in inventory["materials"] if m.get("readable")),
        "degradations": len(inventory["degradations"]),
    }, ensure_ascii=False))
    return 0


def cmd_finalize(input_dir, output_dir, work_dir):
    result_path = os.path.join(output_dir, "result.json")
    loaded = read_json(result_path)
    result = loaded[0] if isinstance(loaded[0], dict) else None
    if result is None:
        started_at = os.environ.get("SKILL_RUN_STARTED_AT") or utc_now_iso()
        result = build_skeleton("unresolved", "input_error", started_at, time.time())
        result["errors"].append({
            "source": "finalize", "error": "skeleton_missing; rebuilt minimal result",
        })

    # 合并 fragments（幂等：fragment 内容不变时重复合并结果一致）
    frag_dir = os.path.join(work_dir, "fragments")
    existing_details = {
        e.get("detail") for e in result.get("change_log", []) if isinstance(e, dict)
    }
    if os.path.isdir(frag_dir):
        for name in sorted(os.listdir(frag_dir)):
            if not name.endswith(".json"):
                continue
            frag, err, _ = read_json(os.path.join(frag_dir, name))
            if isinstance(frag, dict):
                deep_merge(result, frag)
                detail = "merged %s" % name
                if detail not in existing_details:
                    result.setdefault("change_log", []).append({
                        "step": "merge_fragment", "at": utc_now_iso(), "detail": detail,
                    })
                    existing_details.add(detail)
            else:
                result["errors"].append({"source": name, "error": err or "bad_fragment"})

    # 运行统计与预算守卫
    rt = result.setdefault("runtime", {})
    started_epoch = rt.get("started_epoch")
    if not isinstance(started_epoch, (int, float)):
        started_epoch = time.time()
        rt["started_epoch"] = started_epoch
    elapsed = max(0.0, time.time() - started_epoch)
    requests_used = get_ledger_count(work_dir)
    rt["elapsed_seconds"] = round(elapsed, 1)
    rt["requests_used"] = requests_used
    rt["requests_used_note"] = "self-reported ledger; official count recorded by platform"
    rt["overtime"] = elapsed > BUDGET_TOTAL_SECONDS
    rt["over_request_budget"] = requests_used > BUDGET_TOTAL_REQUESTS
    rt["phase_budget"] = PHASE_BUDGET

    # 状态裁决
    issues = validate_result(result)
    status = result.get("status")
    if status in ("insufficient_evidence", "input_error"):
        pass
    elif deliverables_complete(result) and not issues:
        result["status"] = "ok"
    else:
        result["status"] = "partial"

    # 边界自述：partial 必须说清缺了什么
    if result["status"] == "partial":
        missing = [k for k in ("research_question", "prior_work", "novelty", "feasibility")
                   if not result.get(k)]
        if missing:
            _add_unique(result["limits"], "尚未完成交付：%s" % ", ".join(missing))
        if not result.get("evidence_matrix"):
            _add_unique(result["limits"], "evidence_matrix 为空，关键判断尚无 locator+quote 回链")
    elif result["status"] == "ok":
        _add_unique(result["limits"], "四项交付齐全；结论止步于材料证据范围")

    if issues:
        for issue in issues:
            result["errors"].append({"source": "validation", "error": issue})

    write_result(result, output_dir)
    render_report(result, output_dir)

    print(json.dumps({
        "phase": "phase4", "status": result["status"],
        "elapsed_seconds": round(elapsed, 1),
        "requests_used": requests_used,
        "validation_issues": len(issues),
        "overtime": bool(rt.get("overtime")),
    }, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# report.md（人类可读摘要，由 result.json 机械生成）
# ---------------------------------------------------------------------------
def _kv_lines(prefix, obj, lines):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                lines.append("%s- **%s**: %s 项" % (prefix, k, len(v)))
            elif v is None:
                lines.append("%s- **%s**: (未提供)" % (prefix, k))
            else:
                lines.append("%s- **%s**: %s" % (prefix, k, v))
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, dict):
                brief = {k: v for k, v in list(v.items())[:4]}
                lines.append("%s- %s" % (prefix, json.dumps(brief, ensure_ascii=False)))
            else:
                lines.append("%s- %s" % (prefix, v))
    elif obj is None:
        lines.append("%s- (未提供)" % prefix)
    else:
        lines.append("%s- %s" % (prefix, obj))


def render_report(result, output_dir):
    lines = []
    lines.append("# 科研选题与创新性评估报告")
    lines.append("")
    lines.append("- case_id: `%s`" % result.get("case_id"))
    lines.append("- status: `%s`" % result.get("status"))
    rt = result.get("runtime", {}) or {}
    lines.append("- 运行时长: %s s / 预算 %s s；请求: %s / 预算 %s" % (
        rt.get("elapsed_seconds"), rt.get("budget_seconds"),
        rt.get("requests_used"), rt.get("budget_requests"),
    ))
    lines.append("")

    for key, title in (
        ("research_question", "研究问题定义"),
        ("search_protocol", "检索协议与纳排规则"),
        ("prior_work", "已有工作比较"),
        ("novelty", "创新点识别"),
        ("feasibility", "可行性判断"),
    ):
        lines.append("## %s" % title)
        lines.append("")
        _kv_lines("", result.get(key), lines)
        lines.append("")

    lines.append("## 综合结论、研究空白与边界")
    lines.append("")
    _kv_lines("", result.get("synthesis"), lines)
    lines.append("")

    lines.append("## 证据矩阵（%d 条）" % len(result.get("evidence_matrix") or []))
    lines.append("")
    if result.get("evidence_matrix"):
        lines.append("| source_id | locator | field | status | quote |")
        lines.append("|---|---|---|---|---|")
        for e in result["evidence_matrix"]:
            if isinstance(e, dict):
                quote = (e.get("quote") or "").replace("\n", " ").replace("|", "/")
                lines.append("| %s | %s | %s | %s | %s |" % (
                    e.get("source_id") or "-", e.get("locator") or "-",
                    e.get("field") or "-", e.get("status") or "-", quote[:120],
                ))
    lines.append("")

    lines.append("## 冲突证据（%d 条）" % len(result.get("conflicts") or []))
    lines.append("")
    for c in result.get("conflicts") or []:
        if isinstance(c, dict):
            lines.append("- **%s**: %s" % (c.get("topic") or "-", json.dumps(c, ensure_ascii=False)[:300]))
    lines.append("")

    lines.append("## 不确定性")
    lines.append("")
    _kv_lines("", result.get("uncertainty"), lines)
    lines.append("")

    lines.append("## 边界与限制")
    lines.append("")
    for x in result.get("limits") or []:
        lines.append("- %s" % x)
    lines.append("")

    lines.append("## 待人工确认（%d 项）" % len(result.get("pending_human") or []))
    lines.append("")
    for x in result.get("pending_human") or []:
        lines.append("- %s" % (json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x))
    lines.append("")

    lines.append("## 错误 / 降级（errors=%d, degradations=%d）" % (
        len(result.get("errors") or []), len(result.get("degradations") or [])))
    lines.append("")
    for x in result.get("errors") or []:
        lines.append("- error: %s" % (json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x))
    for x in result.get("degradations") or []:
        lines.append("- degradation: %s" % (json.dumps(x, ensure_ascii=False) if not isinstance(x, str) else x))
    lines.append("")

    lines.append("## 变更记录")
    lines.append("")
    for x in result.get("change_log") or []:
        if isinstance(x, dict):
            lines.append("- `%s` %s: %s" % (x.get("step"), x.get("at"), x.get("detail")))
    lines.append("")

    write_text_file(os.path.join(output_dir, "report.md"), "\n".join(lines))


# ---------------------------------------------------------------------------
# 命令分发
# ---------------------------------------------------------------------------
def run(mode, input_dir, output_dir, work_dir):
    if mode == "skeleton":
        return cmd_skeleton(input_dir, output_dir, work_dir)
    if mode == "evidence":
        return cmd_evidence(input_dir, output_dir, work_dir)
    if mode == "finalize":
        return cmd_finalize(input_dir, output_dir, work_dir)
    if mode == "bump":
        count = bump_request(work_dir)
        print(json.dumps({"requests_used": count}, ensure_ascii=False))
        return 0
    raise ValueError("unknown mode: %s" % mode)
