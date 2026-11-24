"""core_impl.py — 科研选题与创新性评估 Skill 的实现核心。

仅依赖 Python 标准库。所有阶段共用这里的鲁棒原语：
  - 路径解析与缺省参数
  - 容错文本/JSON 读取（BOM、坏编码、坏 JSON、超大文件）
  - PDF / DOCX 文本抽取（pdftotext 缺失时降级）
  - 输出契约回填与结果骨架
  - fragment 深合并、合法性校验、report.md 生成
  - 自检（空输入、坏 JSON、缺参数、超大文件、坏编码）

设计取舍（相对“四平八稳综述”的差异点）：先落盘一版合法的 partial 结果，
之后所有阶段只做增量改进；任何异常都记录到 errors/degradations/limitations，
从不编造数值、DOI、分数或运行结果。
"""

import os
import sys
import json
import time
import hashlib
import shutil
import zipfile
import subprocess
import tempfile
import re
from copy import deepcopy
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

MAX_TEXT_BYTES = 2 * 1024 * 1024          # 文本文件超限跳过
MAX_PDF_BYTES = 100 * 1024 * 1024         # PDF 文件超限跳过（放宽）
MAX_EXTRACTED_TEXT = 5 * 1024 * 1024      # 抽取文本截断上限

TEXT_EXTS = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".log", ".bib", ".tex", ".rst", ".ini", ".cfg",
}

# 四项交付 + 固定顶层字段的骨架默认值（确保任何失败都有合法结果）
DELIVERABLE_DEFAULTS = {
    "research_question": None,
    "search_protocol": None,
    "prior_work": None,
    "novelty": None,
    "feasibility": None,
    "evidence_matrix": [],
    "conflicts": [],
    "synthesis": {
        "conclusions": [],
        "research_gaps": [],
        "boundary_statement": "",
    },
}


def utcnow_iso():
    """UTC ISO 时间串；Windows 上避免 %z 兼容性问题。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_slash(path):
    """统一 source_id / locator 中的路径分隔符为 /。"""
    return path.replace("\\", "/").replace("//", "/")


def ensure_dir(path):
    """创建目录；失败不抛异常，返回 False。"""
    if not path:
        return False
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def safe_stat(path):
    """返回 (size_bytes, error) 二元组，任何异常都不上抛。"""
    try:
        return os.path.getsize(path), None
    except OSError as exc:
        return None, "stat_error:" + str(exc)


# ---------------------------------------------------------------------------
# 容错读取
# ---------------------------------------------------------------------------

def read_text_robust(path, max_bytes=MAX_TEXT_BYTES):
    """容错读文本。返回 (text, encoding, error)。

    - 文件不存在 / 无法 stat / 超限 -> (None, None, error)
    - 依次尝试 utf-8-sig、utf-8（strict），失败则 latin-1 兜底
    - 兜底成功时返回 encoding='latin-1-fallback'，调用方据此登记降级
    """
    size, err = safe_stat(path)
    if err:
        return None, None, err
    if size is not None and size > max_bytes:
        return None, None, "oversized"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), enc, None
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as exc:
            return None, enc, "read_error:" + str(exc)
    # 兜底：latin-1 对任意字节序列都能解码
    try:
        with open(path, "r", encoding="latin-1", errors="replace") as fh:
            return fh.read(), "latin-1-fallback", None
    except OSError as exc:
        return None, None, "read_error:" + str(exc)


def read_json_robust(path, max_bytes=MAX_TEXT_BYTES):
    """容错读 JSON。返回 (obj_or_None, error_dict_or_None)。"""
    text, enc, err = read_text_robust(path, max_bytes)
    if err:
        return None, {"read_error": err}
    if enc == "latin-1-fallback":
        return _parse_json(text, {"encoding_fallback": "latin-1"})
    return _parse_json(text, None)


def _parse_json(text, extra_note):
    try:
        return json.loads(text), extra_note
    except (ValueError, TypeError) as exc:
        note = {"parse_error": str(exc)}
        if extra_note:
            note.update(extra_note)
        return None, note


def write_text(path, content):
    """原子写文本（先写 .tmp 再替换），失败返回 False 不抛异常。"""
    try:
        ensure_dir(os.path.dirname(path))
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def write_json(path, obj):
    """原子写 JSON，禁 NaN/Inf；失败返回 False。"""
    try:
        sanitized = sanitize_non_finite(obj)
        text = json.dumps(sanitized, ensure_ascii=False, indent=2, allow_nan=False)
        return write_text(path, text + "\n")
    except (ValueError, TypeError, OSError):
        return False


def sanitize_non_finite(obj):
    """递归替换 NaN/Inf 为 None，保证 JSON 可序列化。"""
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize_non_finite(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_non_finite(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# 输入契约：case.json / output_contract
# ---------------------------------------------------------------------------

def load_case(input_dir):
    """读取 case.json。返回 (case_or_None, error_or_None)。

    error 为 dict，含 key 与原因；读取失败不中断，后续以空契约回退。
    """
    if not os.path.isdir(input_dir):
        return None, {"input_error": "input directory missing: " + str(input_dir)}
    case_path = os.path.join(input_dir, "case.json")
    if not os.path.exists(case_path):
        return None, {"missing": "case.json not found"}
    case, err = read_json_robust(case_path)
    if err:
        return None, {"case_json": err}
    return case, None


def get_output_contract(case):
    """提取 output_contract。dict -> 深拷贝；list -> 键名->None；缺省 -> {}。"""
    if not isinstance(case, dict):
        return {}
    c = case.get("output_contract")
    if isinstance(c, dict):
        return deepcopy(c)
    if isinstance(c, list):
        return {str(k): None for k in c}
    return {}


def get_case_id(case, input_dir):
    """保留输入标识；缺失时按输入目录哈希回退并标 unresolved-。"""
    if isinstance(case, dict):
        for key in ("case_id", "id", "challenge_id", "task_id", "case", "name", "armiId"):
            val = case.get(key)
            if isinstance(val, (str, int, float)) and str(val).strip():
                return str(val).strip()
    digest = hashlib.sha1(os.path.abspath(str(input_dir)).encode("utf-8")).hexdigest()[:12]
    return "unresolved-" + digest


def default_for_contract(value):
    """按契约值形态给出顶层缺省：list->[] dict->{} 标量->原值 None->null。"""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def tool_probe():
    """探测可用外部工具，只报告存在性，不编造版本。"""
    tools = {}
    for name in ("pdftotext", "pdftoppm"):
        tools[name] = shutil.which(name) is not None
    return tools


def build_skeleton(case, input_dir, output_dir, work_dir):
    """构造 result.json 骨架：契约字段齐全，四项交付为 null/[]，status=partial。"""
    contract = get_output_contract(case)
    result = {
        "case_id": get_case_id(case, input_dir),
        "status": "partial",
        "output_contract": deepcopy(contract),
    }
    # 契约每个键原样回填到顶层，缺值给 null/[]，不改名不删未知键
    for key, val in contract.items():
        result[key] = default_for_contract(val)
    # 固定主字段
    for key, default in DELIVERABLE_DEFAULTS.items():
        result[key] = deepcopy(default)
    result.update({
        "sources": [],
        "uncertainty": {"bias": [], "gaps": [], "unverifiable": [], "notes": []},
        "pending_human": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "change_log": [],
        "runtime": {
            "started_at": utcnow_iso(),
            "finished_at": None,
            "duration_seconds": None,
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
            "tools": tool_probe(),
            "work_dir": norm_slash(str(work_dir)),
        },
    })
    return result


def append_log(result, phase, action, detail=None):
    entry = {"at": utcnow_iso(), "phase": phase, "action": action}
    if detail is not None:
        entry["detail"] = detail
    logs = result.setdefault("change_log", [])
    if not isinstance(logs, list):
        logs = []
        result["change_log"] = logs
    logs.append(entry)


# ---------------------------------------------------------------------------
# 材料发现与文本抽取
# ---------------------------------------------------------------------------

def classify_file(name, ext):
    low = name.lower()
    if low in ("case.json", "task.md"):
        return "task_meta"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    if ext == ".docx":
        return "office_docx"
    if ext in (".xlsx", ".pptx", ".doc", ".ppt", ".xls"):
        return "office_unsupported"
    return "other"


def discover_materials(input_dir):
    """递归发现输入文件，返回有序 item 列表（不含目录）。"""
    items = []
    if not os.path.isdir(input_dir):
        return items
    for root, _dirs, files in os.walk(input_dir):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, input_dir)
            ext = os.path.splitext(fn)[1].lower()
            size, stat_err = safe_stat(full)
            items.append({
                "path": full,
                "rel": norm_slash(rel),
                "name": fn,
                "ext": ext,
                "kind": classify_file(fn, ext),
                "size_bytes": size,
                "stat_error": stat_err,
            })
    return items


def source_id_for(rel):
    """稳定 source_id：以归一化相对路径为主键，保证可回查。"""
    return norm_slash(rel)


def extract_pdf_text(pdf_path, out_txt_path):
    """用 pdftotext 抽取文字。返回 (text_or_None, error_or_None)。"""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext_not_found"
    try:
        ensure_dir(os.path.dirname(out_txt_path))
        proc = subprocess.run(
            [exe, "-layout", pdf_path, out_txt_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "pdftotext_run_error:" + str(exc)
    if proc.returncode != 0 or not os.path.exists(out_txt_path):
        return None, "pdftotext_failed_rc:%d" % proc.returncode
    text, _enc, err = read_text_robust(out_txt_path, MAX_EXTRACTED_TEXT)
    if err:
        return None, "pdftotext_read_error:" + str(err)
    if text is not None and not text.strip():
        return None, "pdftotext_empty_text"
    return text, None


def extract_docx_text(path):
    """基础 DOCX 抽取：解压 word/document.xml 并去标签。失败返回 (None, error)。"""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        return None, "docx_extract_error:" + str(exc)
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text, None


def process_materials(input_dir, work_dir):
    """发现 + 抽取文本。返回 inventory dict（含 items 与逐文件抽取结果）。"""
    text_dir = os.path.join(work_dir, "text")
    ensure_dir(text_dir)
    items = discover_materials(input_dir)
    for item in items:
        kind = item["kind"]
        item["extraction"] = None
        item["read_status"] = "not_attempted"
        size = item["size_bytes"] or 0
        if item["stat_error"]:
            item["read_status"] = "unreadable"
            item["extraction"] = {"error": item["stat_error"]}
            continue
        if kind == "text" or kind == "task_meta":
            if size > MAX_TEXT_BYTES:
                item["read_status"] = "skipped_oversized"
                continue
            text, enc, err = read_text_robust(item["path"])
            if err:
                item["read_status"] = "unreadable"
                item["extraction"] = {"error": err}
                continue
            item["read_status"] = "read"
            item["extraction"] = {"encoding": enc, "chars": len(text)}
        elif kind == "pdf":
            if size > MAX_PDF_BYTES:
                item["read_status"] = "skipped_oversized"
                continue
            out_txt = os.path.join(text_dir, norm_slash(item["rel"]).replace("/", "__") + ".txt")
            text, err = extract_pdf_text(item["path"], out_txt)
            if err:
                item["read_status"] = "text_unavailable"
                item["extraction"] = {"error": err}
                continue
            item["read_status"] = "extracted"
            item["extraction"] = {"text_file": norm_slash(out_txt), "chars": len(text)}
        elif kind == "office_docx":
            text, err = extract_docx_text(item["path"])
            if err:
                item["read_status"] = "unreadable"
                item["extraction"] = {"error": err}
                continue
            item["read_status"] = "extracted"
            item["extraction"] = {"chars": len(text)}
        else:
            item["read_status"] = "unsupported"
    return {"input_dir": norm_slash(str(input_dir)), "items": items}


def inventory_to_sources(inventory):
    """把 inventory 转为 result.json 的 sources 清单。"""
    sources = []
    for item in inventory.get("items", []):
        src = {
            "source_id": source_id_for(item["rel"]),
            "path": item["rel"],
            "kind": item["kind"],
            "size_bytes": item["size_bytes"],
            "status": item["read_status"],
        }
        ext = item.get("extraction")
        if ext:
            src["extraction"] = ext
        sources.append(src)
    return sources


def count_readable(inventory):
    return sum(
        1 for it in inventory.get("items", [])
        if it.get("read_status") in ("read", "extracted")
    )


# ---------------------------------------------------------------------------
# 深合并与校验
# ---------------------------------------------------------------------------

def deep_merge(base, override):
    """把 fragment 深合并进骨架。list 以 fragment 为准（幂等重跑）；change_log 追加去重。"""
    if not isinstance(override, dict):
        return base
    for key, val in override.items():
        if key == "change_log":
            base.setdefault("change_log", [])
            if isinstance(val, list):
                seen = {(e.get("at"), e.get("action")) for e in base["change_log"] if isinstance(e, dict)}
                for entry in val:
                    if isinstance(entry, dict):
                        sig = (entry.get("at"), entry.get("action"))
                        if sig not in seen:
                            base["change_log"].append(entry)
                            seen.add(sig)
            continue
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], val)
        else:
            base[key] = deepcopy(val)
    return base


def validate_result(result, contract):
    """校验契约与可核验性。返回 (problems, warnings) 两个列表。"""
    problems, warnings = [], []
    for key in contract:
        if key not in result:
            problems.append("contract_key_missing:" + key)
    # 四项交付是否齐全（任一为 None 视为缺口）
    for key in ("research_question", "search_protocol", "prior_work", "novelty", "feasibility"):
        if result.get(key) in (None, {}, ""):
            warnings.append("deliverable_missing:" + key)
    ev = result.get("evidence_matrix")
    if isinstance(ev, list) and ev:
        for i, row in enumerate(ev):
            if not isinstance(row, dict):
                warnings.append("evidence_matrix[%d]:not_dict" % i)
                continue
            loc = row.get("locator", "")
            quote = row.get("quote", "")
            if not isinstance(loc, str) or not loc.strip():
                warnings.append("evidence_matrix[%d]:empty_locator" % i)
            if not isinstance(quote, str) or not quote.strip():
                warnings.append("evidence_matrix[%d]:empty_quote" % i)
    return problems, warnings


def status_from_result(result, problems, warnings, readable):
    if problems:
        return "partial"
    if readable == 0:
        return "insufficient_evidence"
    if warnings:
        return "partial"
    return "ok"


# ---------------------------------------------------------------------------
# 阶段 0：skeleton
# ---------------------------------------------------------------------------

def run_skeleton(input_dir=DEFAULT_INPUT, output_dir=DEFAULT_OUTPUT, work_dir=DEFAULT_WORK):
    started = time.time()
    input_dir = input_dir or DEFAULT_INPUT
    output_dir = output_dir or DEFAULT_OUTPUT
    work_dir = work_dir or DEFAULT_WORK
    ensure_dir(output_dir)
    ensure_dir(work_dir)
    ensure_dir(os.path.join(work_dir, "fragments"))
    ensure_dir(os.path.join(work_dir, "text"))

    input_exists = os.path.isdir(input_dir)
    case, case_err = load_case(input_dir)
    result = build_skeleton(case, input_dir, output_dir, work_dir)

    if not input_exists:
        result["status"] = "input_error"
        result["limitations"].append("input directory missing; no materials to assess")
    if case_err:
        result["errors"].append({"stage": "skeleton", "case.json": case_err})
        result["limitations"].append("case.json missing or unreadable; continuing with empty contract")
    append_log(result, "skeleton", "wrote_partial_skeleton",
               {"input_exists": input_exists, "status": result["status"]})

    manifest = {
        "input_dir": norm_slash(str(input_dir)),
        "input_exists": input_exists,
        "case_file": "case.json" if (input_exists and os.path.exists(os.path.join(input_dir, "case.json"))) else None,
        "case_error": case_err,
        "generated_at": utcnow_iso(),
    }
    ok_result = write_json(os.path.join(output_dir, "result.json"), result)
    ok_manifest = write_json(os.path.join(work_dir, "input_manifest.json"), manifest)
    if not ok_result or not ok_manifest:
        print("[skeleton] WARN: could not write skeleton files under %s" % output_dir, file=sys.stderr)
    print("[skeleton] status=%s case_id=%s" % (result["status"], result["case_id"]))
    return 0


# ---------------------------------------------------------------------------
# 阶段 1：evidence
# ---------------------------------------------------------------------------

def run_evidence(input_dir=DEFAULT_INPUT, output_dir=DEFAULT_OUTPUT, work_dir=DEFAULT_WORK):
    input_dir = input_dir or DEFAULT_INPUT
    output_dir = output_dir or DEFAULT_OUTPUT
    work_dir = work_dir or DEFAULT_WORK
    ensure_dir(output_dir)
    ensure_dir(work_dir)

    result_path = os.path.join(output_dir, "result.json")
    result, rerr = read_json_robust(result_path)
    if rerr or not isinstance(result, dict):
        # 骨架未生成或损坏：重建，保证证据阶段也能独立运行
        case, _ = load_case(input_dir)
        result = build_skeleton(case, input_dir, output_dir, work_dir)
        result["errors"].append({"stage": "evidence", "skeleton_rebuilt": rerr})

    inventory = process_materials(input_dir, work_dir)
    result["sources"] = inventory_to_sources(inventory)
    readable = count_readable(inventory)

    # 汇总降级与错误
    for item in inventory.get("items", []):
        if item.get("read_status") == "skipped_oversized":
            result["degradations"].append(
                {"source_id": source_id_for(item["rel"]), "reason": "oversized", "size_bytes": item["size_bytes"]})
        elif item.get("read_status") == "text_unavailable":
            ext = (item.get("extraction") or {}).get("error")
            result["degradations"].append({"source_id": source_id_for(item["rel"]), "reason": ext})
        elif item.get("read_status") == "unreadable":
            ext = (item.get("extraction") or {}).get("error")
            result["degradations"].append({"source_id": source_id_for(item["rel"]), "reason": ext})

    if readable == 0:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("no readable materials found; cannot produce evidence-based conclusions")
    elif result.get("status") in (None, "input_error"):
        result["status"] = "partial"

    result["runtime"]["tools"] = tool_probe()
    append_log(result, "evidence", "discovered_materials",
               {"files": len(inventory.get("items", [])), "readable": readable})

    write_json(os.path.join(work_dir, "material_inventory.json"), inventory)
    write_json(result_path, result)
    print("[evidence] files=%d readable=%d status=%s" % (len(inventory.get("items", [])), readable, result["status"]))
    return 0


# ---------------------------------------------------------------------------
# 阶段 4：finalize
# ---------------------------------------------------------------------------

def load_fragments(work_dir):
    """读 /app/work/fragments/*.json，返回 (fragments, errors)。"""
    fragments, errors = [], []
    frag_dir = os.path.join(work_dir, "fragments")
    if not os.path.isdir(frag_dir):
        return fragments, [{"fragments_dir": "missing"}]
    for fn in sorted(os.listdir(frag_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(frag_dir, fn)
        obj, err = read_json_robust(path)
        if err:
            errors.append({"fragment": fn, "error": err})
            continue
        if not isinstance(obj, dict):
            errors.append({"fragment": fn, "error": "not_object"})
            continue
        fragments.append((fn, obj))
    return fragments, errors


def build_report(result):
    """从 result 生成人类可读 report.md（确定性文本）。"""
    lines = [
        "# 科研选题与创新性评估报告",
        "",
        "- case_id: `%s`" % result.get("case_id"),
        "- status: `%s`" % result.get("status"),
        "- generated_at: %s" % utcnow_iso(),
        "",
        "## 四项交付摘要",
        "",
    ]
    for key, label in (
        ("research_question", "研究问题定义"),
        ("prior_work", "已有工作比较"),
        ("novelty", "创新点识别"),
        ("feasibility", "可行性判断"),
        ("search_protocol", "检索协议"),
    ):
        val = result.get(key)
        if isinstance(val, dict) and val.get("status"):
            lines.append("- **%s**：%s" % (label, val.get("status")))
        elif isinstance(val, dict):
            lines.append("- **%s**：已填写（无 status 字段）" % label)
        else:
            lines.append("- **%s**：缺失（null）" % label)
    lines.append("")
    ev = result.get("evidence_matrix") or []
    lines.append("## 证据矩阵")
    lines.append("")
    lines.append("- 证据条目数：%d" % (len(ev) if isinstance(ev, list) else 0))
    conflicts = result.get("conflicts") or []
    lines.append("- 冲突证据登记数：%d" % (len(conflicts) if isinstance(conflicts, list) else 0))
    lines.append("")
    synth = result.get("synthesis") or {}
    lines.append("## 综合结论")
    lines.append("")
    if isinstance(synth, dict) and synth.get("boundary_statement"):
        lines.append(str(synth.get("boundary_statement")))
    else:
        lines.append("_尚未形成边界陈述。_")
    lines.append("")
    lines.append("## 不确定性 / 局限")
    lines.append("")
    unc = result.get("uncertainty") or {}
    for k in ("gaps", "unverifiable", "bias"):
        v = unc.get(k) if isinstance(unc, dict) else None
        if v:
            lines.append("- %s: %d 项" % (k, len(v) if isinstance(v, list) else 1))
    lims = result.get("limitations") or []
    if lims:
        lines.append("- limitations: %d 项" % len(lims))
    lines.append("")
    lines.append("## 运行状态")
    lines.append("")
    rt = result.get("runtime") or {}
    lines.append("- python: %s; platform: %s" % (rt.get("python_version"), rt.get("platform")))
    lines.append("- started: %s; duration: %ss" % (rt.get("started_at"), rt.get("duration_seconds")))
    return "\n".join(lines) + "\n"


def run_finalize(input_dir=DEFAULT_INPUT, output_dir=DEFAULT_OUTPUT, work_dir=DEFAULT_WORK):
    started = time.time()
    input_dir = input_dir or DEFAULT_INPUT
    output_dir = output_dir or DEFAULT_OUTPUT
    work_dir = work_dir or DEFAULT_WORK
    ensure_dir(output_dir)
    ensure_dir(work_dir)

    result_path = os.path.join(output_dir, "result.json")
    result, rerr = read_json_robust(result_path)
    if rerr or not isinstance(result, dict):
        case, _ = load_case(input_dir)
        result = build_skeleton(case, input_dir, output_dir, work_dir)
        result["errors"].append({"stage": "finalize", "skeleton_rebuilt": rerr})

    # 重新校准契约（以防骨架之后 case.json 变化）
    case, case_err = load_case(input_dir)
    contract = get_output_contract(case)
    result["output_contract"] = deepcopy(contract)
    for key, val in contract.items():
        result.setdefault(key, default_for_contract(val))

    fragments, frag_errors = load_fragments(work_dir)
    for fn, frag in fragments:
        deep_merge(result, frag)
        append_log(result, "finalize", "merged_fragment", {"fragment": fn})
    for fe in frag_errors:
        result["errors"].append({"stage": "finalize", **fe})

    problems, warnings = validate_result(result, contract)
    for w in warnings:
        result["degradations"].append({"reason": w})
        result["limitations"].append(w)
    for p in problems:
        result["errors"].append({"stage": "finalize", "problem": p})

    # 是否可读材料
    inventory_path = os.path.join(work_dir, "material_inventory.json")
    readable = 0
    inv, _ = read_json_robust(inventory_path)
    if isinstance(inv, dict):
        readable = count_readable(inv)
    elif os.path.isdir(input_dir):
        # 回退：现场统计
        readable = count_readable(process_materials(input_dir, work_dir))

    result["status"] = status_from_result(result, problems, warnings, readable)

    result["runtime"]["finished_at"] = utcnow_iso()
    result["runtime"]["duration_seconds"] = round(time.time() - started, 3)
    result["runtime"]["tools"] = tool_probe()

    # 任何问题都要先写合法结果，再决定退出码
    ok = write_json(result_path, result)
    if not ok:
        print("[finalize] FATAL: could not write result.json", file=sys.stderr)
        return 0
    write_text(os.path.join(output_dir, "report.md"), build_report(result))

    print("[finalize] status=%s fragments=%d problems=%d warnings=%d" % (
        result["status"], len(fragments), len(problems), len(warnings)))
    return 2 if problems else 0


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def _tmp_case(tmp, name, content_bytes=None, text=None):
    d = os.path.join(tmp, name)
    ensure_dir(d)
    if text is not None:
        write_text(os.path.join(d, "case.json"), text)
    if content_bytes is not None:
        with open(os.path.join(d, "case.json"), "wb") as fh:
            fh.write(content_bytes)
    return d


def run_selftest():
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    with tempfile.TemporaryDirectory(prefix="skill-selftest-") as tmp:
        # 1) 输入目录不存在
        missing_in = os.path.join(tmp, "no_input")
        out1 = os.path.join(tmp, "out1")
        work1 = os.path.join(tmp, "work1")
        rc = run_skeleton(missing_in, out1, work1)
        r, e = read_json_robust(os.path.join(out1, "result.json"))
        check("missing_input_exit0", rc == 0)
        check("missing_input_status", bool(r and r.get("status") == "input_error" and r.get("case_id")))
        check("missing_input_legal_json", e is None)

        # 2) 空输入目录
        empty_in = _tmp_case(tmp, "empty_in")
        out2 = os.path.join(tmp, "out2")
        work2 = os.path.join(tmp, "work2")
        run_skeleton(empty_in, out2, work2)
        rc = run_evidence(empty_in, out2, work2)
        r2, _ = read_json_robust(os.path.join(out2, "result.json"))
        check("empty_input_exit0", rc == 0)
        check("empty_input_insufficient", bool(r2 and r2.get("status") == "insufficient_evidence"))

        # 3) 坏 JSON case.json
        bad_in = _tmp_case(tmp, "bad_in", text="{not valid json")
        out3 = os.path.join(tmp, "out3")
        work3 = os.path.join(tmp, "work3")
        rc = run_skeleton(bad_in, out3, work3)
        r3, _ = read_json_robust(os.path.join(out3, "result.json"))
        check("bad_json_exit0", rc == 0)
        check("bad_json_error_recorded", bool(r3 and any(e.get("case.json") for e in r3.get("errors", []))))

        # 4) 超大文件 + 坏编码文件
        big_in = _tmp_case(tmp, "big_in", text='{"output_contract": {"research_summary": null}}')
        with open(os.path.join(big_in, "huge.md"), "wb") as fh:
            fh.write(b"x" * (MAX_TEXT_BYTES + 1024))
        with open(os.path.join(big_in, "legacy.txt"), "wb") as fh:
            fh.write("caf\xe9 \xe9tude".encode("latin-1"))
        out4 = os.path.join(tmp, "out4")
        work4 = os.path.join(tmp, "work4")
        run_skeleton(big_in, out4, work4)
        run_evidence(big_in, out4, work4)
        r4, _ = read_json_robust(os.path.join(out4, "result.json"))
        check("oversized_skipped_recorded", bool(r4 and any(
            d.get("reason") == "oversized" for d in r4.get("degradations", []))))
        check("bad_encoding_read", bool(r4 and any(
            s.get("status") == "read" and s.get("source_id") == "legacy.txt" for s in r4.get("sources", []))))

        # 5) 全流程：文本材料 + fragment + 契约回填
        full_in = _tmp_case(tmp, "full_in",
                            text='{"id": "case-selftest", "output_contract": {"research_summary": null, "refs": []}}')
        write_text(os.path.join(full_in, "task.md"), "# task\n研究问题：给定材料。\n")
        write_text(os.path.join(full_in, "materials", "paper.txt"), "paper content\n")
        out5 = os.path.join(tmp, "out5")
        work5 = os.path.join(tmp, "work5")
        run_skeleton(full_in, out5, work5)
        run_evidence(full_in, out5, work5)
        frag = {
            "research_question": {"testable_statement": "x", "status": "complete"},
            "evidence_matrix": [{"locator": "materials/paper.txt#L1", "quote": "paper content"}],
            "conflicts": [],
            "synthesis": {"boundary_statement": "仅到材料范围"},
        }
        write_json(os.path.join(work5, "fragments", "01_question.json"), frag)
        rc = run_finalize(full_in, out5, work5)
        r5, _ = read_json_robust(os.path.join(out5, "result.json"))
        check("pipeline_finalize_exit0", rc == 0)
        check("case_id_preserved", bool(r5 and r5.get("case_id") == "case-selftest"))
        check("contract_echoed", bool(r5 and "research_summary" in r5 and r5.get("refs") == []))
        check("fragment_merged", bool(r5 and (r5.get("research_question") or {}).get("status") == "complete"))
        check("report_exists", os.path.exists(os.path.join(out5, "report.md")))

        # 6) 幂等：finalize 重跑结果一致
        run_finalize(full_in, out5, work5)
        r6, _ = read_json_robust(os.path.join(out5, "result.json"))
        check("rerun_stable_case_id", bool(r6 and r6.get("case_id") == r5.get("case_id")))

    n_ok = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, ("| " + detail) if detail else ""))
    print("selftest: %d/%d passed" % (n_ok, len(checks)))
    return 0 if n_ok == len(checks) else 1
