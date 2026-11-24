# -*- coding: utf-8 -*-
"""core_impl.py — 科研选题评估 Skill 的确定性实现层。

仅依赖 Python 标准库。职责：
  1. 容错读取输入（case.json / task.md / 材料），不假设固定文件名与键名；
  2. PDF 用平台预装 pdftotext 抽字，Office 二进制用 zipfile+XML 抽文字，均降级；
  3. 产出确定性的 result.json（canonical JSON + 稳定 case_id + 固定字段顺序）；
  4. 骨架先行：先落一版合法 partial 结果，再增量合并 fragment；
  5. 契约校验：output_contract 全键回填、无 NaN、证据 locator+quote 非空；
  6. 报告生成与确定性自检支持。

确定性约定（本 Skill 的核心取舍）：
  - result.json 一律经 canonical_dumps(allow_nan=False) 写出：键排序 + ensure_ascii=False
    + 紧凑分隔符；NaN/Infinity 直接报错而不是静默写出。
  - 文件发现按相对路径排序；case_id 回退哈希只依赖 相对路径+字节数+前 4KB 内容，
    不含随机数、不含挂钟时间。
  - result.json 不写挂钟时间戳；耗时仅记录到 work/run.log 供排查，不进入主产物，
    从而保证同一 /app/work 中间态两次 finalize 得到字节一致的 result.json。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# 预算与常量（与 SKILL.md 第 4 节一致）
# ---------------------------------------------------------------------------
BUDGET_SECONDS = 900
BUDGET_REQUESTS = 64
MAX_TOKENS_PER_RESPONSE = 16384
MAX_FILE_BYTES = 2 * 1024 * 1024
SKELETON_BUDGET_SECONDS = 180  # 前 3 分钟必须落盘骨架

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_INPUT_ERROR = "input_error"
ALLOWED_STATUS = (STATUS_OK, STATUS_PARTIAL, STATUS_INSUFFICIENT, STATUS_INPUT_ERROR)

# 四项交付 + 证据矩阵等默认契约；case.json 提供 output_contract 时以其键为准，
# 未提供时按本表生成。类型同时用于 skeleton 的空值初始化。
DEFAULT_CONTRACT = {
    "research_question": None,
    "search_protocol": None,
    "prior_work": None,
    "novelty": None,
    "feasibility": None,
    "evidence_matrix": [],
    "conflicts": [],
    "synthesis": None,
    "sources": [],
    "uncertainty": None,
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "limitations": [],
}

# fragment 固定顺序（模型/Agent 按此顺序写入，finalize 按此顺序深合并）
FRAGMENT_IDS = [
    "00_meta",
    "01_question",
    "02_prior",
    "03_novelty",
    "04_feasibility",
    "05_synthesis",
]

PHASES = [
    {"phase": "phase0_skeleton", "purpose": "落盘 partial 骨架", "budget_minutes": 3,
     "budget_requests": 6, "output": "result.json(skeleton) + input_manifest.json"},
    {"phase": "phase1_evidence", "purpose": "发现材料/抽 PDF/刷新 sources", "budget_minutes": 3,
     "budget_requests": 8, "output": "material_inventory.json"},
    {"phase": "phase2_deliverables", "purpose": "四项交付写 fragment", "budget_minutes": 5,
     "budget_requests": 30, "output": "fragments/01..04_*.json"},
    {"phase": "phase3_synthesis", "purpose": "证据矩阵/冲突/不确定性/边界", "budget_minutes": 3,
     "budget_requests": 14, "output": "fragments/05_synthesis.json"},
    {"phase": "phase4_finalize", "purpose": "合并/校验/收尾", "budget_minutes": 1,
     "budget_requests": 6, "output": "result.json + report.md"},
]

TEXT_EXTS = {".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".html", ".htm", ".yaml", ".yml", ".log", ".tex", ".bib"}
OFFICE_EXTS = {".docx", ".pptx", ".xlsx"}


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def canonical_dumps(obj):
    """确定性 JSON 序列化：键排序、无 BOM、allow_nan=False。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def read_text(path):
    """容错文本读取：utf-8-sig -> utf-8 -> latin-1。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def safe_json_load(path):
    """返回 (data, error)；坏 JSON 不抛异常。"""
    try:
        text = read_text(path)
    except OSError as exc:
        return None, "read failed: %s" % exc
    try:
        return json.loads(text), None
    except (ValueError, TypeError) as exc:
        return None, "bad JSON: %s" % exc


def relpath_fwd(path, base):
    rel = os.path.relpath(path, base)
    return rel.replace("\\", "/")


def slugify(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


# ---------------------------------------------------------------------------
# 输入发现与 case_id
# ---------------------------------------------------------------------------
def discover_files(input_dir):
    """按相对路径排序发现输入文件，返回 list[dict]。跳过超过 2MB 的文件。"""
    out = []
    for root, dirs, files in os.walk(input_dir):
        for name in files:
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            rel = relpath_fwd(p, input_dir)
            entry = {"path": p, "rel": rel, "bytes": size}
            if size > MAX_FILE_BYTES:
                entry["skipped"] = True
                entry["reason"] = "size > %d bytes" % MAX_FILE_BYTES
            else:
                entry["skipped"] = False
                with open(p, "rb") as fh:
                    entry["head_sha256"] = hashlib.sha256(fh.read(4096)).hexdigest()
            out.append(entry)
    out.sort(key=lambda e: e["rel"])
    return out


def input_digest(entries):
    """基于 相对路径+字节数+前 4KB 内容 的稳定哈希（不含随机、不含时间）。"""
    h = hashlib.sha256()
    for e in entries:
        h.update(e["rel"].encode("utf-8"))
        h.update(b":")
        h.update(str(e["bytes"]).encode("ascii"))
        h.update(b":")
        if not e.get("skipped"):
            h.update(e.get("head_sha256", "").encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def resolve_case_id(case_data, entries):
    """优先保留输入自带标识，否则用输入目录哈希回退并标 unresolved-。"""
    for key in ("case_id", "caseId", "case_identifier"):
        v = case_data.get(key) if isinstance(case_data, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    if isinstance(case_data, dict):
        for key in ("id", "challenge_id"):
            v = case_data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return "unresolved-" + input_digest(entries)[:12]


def resolve_contract(case_data):
    """返回 (contract, error)。output_contract 必须原样回显；缺省用 DEFAULT_CONTRACT。"""
    if isinstance(case_data, dict) and "output_contract" in case_data:
        oc = case_data["output_contract"]
        if isinstance(oc, dict):
            return dict(oc), None
        if isinstance(oc, list):
            return {str(k): None for k in oc}, None
        return dict(DEFAULT_CONTRACT), "output_contract 非对象/列表，已回退默认契约"
    return dict(DEFAULT_CONTRACT), None


def empty_for(key):
    """契约键的空值初始化：已知键按 DEFAULT_CONTRACT 类型，未知键给 None。"""
    if key in DEFAULT_CONTRACT:
        v = DEFAULT_CONTRACT[key]
        return [] if isinstance(v, list) else None
    return None


# ---------------------------------------------------------------------------
# 骨架
# ---------------------------------------------------------------------------
def build_skeleton(input_dir, work_dir, output_dir, case_data, case_error, entries, contract, contract_error):
    """先落一版合法 partial 结果 + 输入清单。任何后续失败都至少有合法部分结果。"""
    errors = []
    degradations = []
    case_path = os.path.join(input_dir, "case.json")
    case_missing = not os.path.isfile(case_path)
    # 区分：case.json 缺失/输入目录缺失 -> input_error；case.json 存在但坏 JSON ->
    # 记 error 并继续（fallback 契约 + unresolved case_id），不因坏 JSON 放弃材料。
    if case_error:
        errors.append(case_error)
    if contract_error:
        degradations.append(contract_error)

    if not os.path.isdir(input_dir) or case_missing:
        status = STATUS_INPUT_ERROR
    else:
        status = STATUS_PARTIAL

    result = {
        "case_id": resolve_case_id(case_data, entries) if case_data else "unresolved-" + input_digest(entries)[:12],
        "status": status,
        "output_contract": contract,
    }
    # 标准字段始终就位（主产物 + 元信息），再并上 output_contract 的全部键：
    # 缺值给 null/[]，不改名、不删未知键，且保证 sources/uncertainty/pending_human/
    # errors/degradations/limitations 等元字段不因自定义契约而丢失。
    all_keys = list(DEFAULT_CONTRACT.keys())
    for key in contract:
        if key not in all_keys:
            all_keys.append(key)
    for key in all_keys:
        result[key] = empty_for(key)
    # 与契约并列的元字段（确定性内容，无挂钟时间）
    result["runtime"] = {
        "budget_seconds": BUDGET_SECONDS,
        "budget_requests": BUDGET_REQUESTS,
        "max_tokens_per_response": MAX_TOKENS_PER_RESPONSE,
        "skeleton_budget_seconds": SKELETON_BUDGET_SECONDS,
        "phases": PHASES,
    }
    result["change_log"] = [{
        "seq": 1,
        "step": "skeleton",
        "action": "落盘 partial 骨架，契约字段全量就位为空值",
        "reason": "先保证任何中断都不留下不完整产物",
    }]
    if errors:
        result["errors"] = errors
    if degradations:
        result["degradations"] = degradations

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    manifest = {
        "input_dir": input_dir,
        "case_id": result["case_id"],
        "files": entries,
        "contract_keys": sorted(contract.keys()),
    }
    with open(os.path.join(work_dir, "input_manifest.json"), "w", encoding="utf-8") as fh:
        fh.write(canonical_dumps(manifest))
    write_result(result, output_dir)
    return result


def write_result(result, output_dir):
    path = os.path.join(output_dir, "result.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(canonical_dumps(result))
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# 证据阶段：材料清单 + PDF/Office 抽字
# ---------------------------------------------------------------------------
def extract_pdf_text(pdf_path, work_dir, name_hint=None):
    """返回 (text, ok, degrade)。pdftotext 缺失或失败时降级。"""
    pdftotext = shutil_which("pdftotext")
    if not pdftotext:
        return None, False, "pdftotext 不可用，PDF 标 text_unavailable"
    dest_dir = os.path.join(work_dir, "extracted")
    os.makedirs(dest_dir, exist_ok=True)
    stem = name_hint or os.path.basename(pdf_path)
    dest = os.path.join(dest_dir, slugify(stem) + ".txt")
    try:
        proc = subprocess.run([pdftotext, "-enc", "UTF-8", pdf_path, dest],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, False, "pdftotext 运行失败: %s" % exc
    if proc.returncode != 0 or not os.path.isfile(dest):
        return None, False, "pdftotext 退出码 %s" % proc.returncode
    text = read_text(dest)
    return text, True, None


def extract_office_text(path):
    """docx/pptx/xlsx：zipfile + XML 抽可见文字（标准库实现，无外部依赖）。"""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    chunks = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if path.lower().endswith(".docx"):
                target = ["word/document.xml"]
            elif path.lower().endswith(".pptx"):
                target = sorted([n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)])
            else:  # xlsx
                target = [n for n in names if n == "xl/sharedStrings.xml"]
            for name in target:
                try:
                    data = zf.read(name)
                except KeyError:
                    continue
                try:
                    root = ElementTree.fromstring(data)
                except ElementTree.ParseError:
                    continue
                for t in root.iter():
                    tag = t.tag.split("}")[-1]
                    if tag == "t" and t.text:
                        chunks.append(t.text)
    except (zipfile.BadZipFile, OSError):
        return "", False
    return "\n".join(chunks), bool(chunks)


def shutil_which(name):
    import shutil
    return shutil.which(name)


def run_evidence(input_dir, work_dir, output_dir):
    """发现材料、抽 PDF/Office 文字、刷新 sources，写 material_inventory.json。"""
    entries = discover_files(input_dir)
    case_data, case_error = safe_json_load(os.path.join(input_dir, "case.json"))
    degradations = []
    sources = []
    materials = []

    for e in entries:
        rel = e["rel"]
        ext = os.path.splitext(rel)[1].lower()
        base = {"source_id": "src-%03d" % (len(sources) + 1), "path": rel,
                "type": "case_input" if rel in ("case.json", "task.md") else "material",
                "bytes": e["bytes"], "extracted_chars": None, "status": "ok"}
        if e.get("skipped"):
            base["status"] = "skipped"
            base["note"] = e.get("reason", "too large")
            degradations.append("%s: %s" % (rel, base["note"]))
            sources.append(base)
            continue
        if rel in ("case.json", "task.md"):
            try:
                base["extracted_chars"] = len(read_text(e["path"]))
            except OSError:
                base["status"] = "unreadable"
            sources.append(base)
            continue
        if ext == ".pdf":
            text, ok, deg = extract_pdf_text(e["path"], work_dir, name_hint=rel)
            base["extracted_chars"] = len(text) if text is not None else 0
            if not ok:
                base["status"] = "text_unavailable"
                degradations.append("%s: %s" % (rel, deg))
            else:
                materials.append({"source_id": base["source_id"], "rel": rel,
                                  "extracted_file": "extracted/" + slugify(rel) + ".txt",
                                  "chars": base["extracted_chars"]})
        elif ext in OFFICE_EXTS:
            text, ok = extract_office_text(e["path"])
            base["extracted_chars"] = len(text)
            if not ok:
                base["status"] = "text_unavailable"
                degradations.append("%s: Office 文本抽取为空" % rel)
            else:
                dest_dir = os.path.join(work_dir, "extracted")
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, slugify(rel) + ".txt")
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(text)
                materials.append({"source_id": base["source_id"], "rel": rel,
                                  "extracted_file": "extracted/" + slugify(rel) + ".txt",
                                  "chars": base["extracted_chars"]})
        elif ext in TEXT_EXTS:
            try:
                base["extracted_chars"] = len(read_text(e["path"]))
            except OSError:
                base["status"] = "unreadable"
            materials.append({"source_id": base["source_id"], "rel": rel,
                              "extracted_file": None, "chars": base["extracted_chars"]})
        else:
            base["status"] = "unsupported"
            base["note"] = "extension %s not handled" % ext
            degradations.append("%s: %s" % (rel, base["note"]))
        sources.append(base)

    inventory = {"input_dir": input_dir, "materials": materials,
                 "sources": sources, "degradations": degradations}
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "material_inventory.json"), "w", encoding="utf-8") as fh:
        fh.write(canonical_dumps(inventory))

    # 刷新骨架的 sources / degradations / status
    result_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(result_path):
        result, _ = safe_json_load(result_path)
    else:
        result = None
    if result is not None:
        result["sources"] = sources
        if degradations:
            result["degradations"] = list(dict.fromkeys(result.get("degradations", []) + degradations))
        if not materials and case_data is not None:
            result["status"] = STATUS_INSUFFICIENT
        write_result(result, output_dir)
    else:
        # 骨架缺失：就地重建
        contract, contract_error = resolve_contract(case_data)
        build_skeleton(input_dir, work_dir, output_dir, case_data, case_error,
                       entries, contract, contract_error)
        result_path2 = os.path.join(output_dir, "result.json")
        result, _ = safe_json_load(result_path2)
        result["sources"] = sources
        if degradations:
            result["degradations"] = list(dict.fromkeys(result.get("degradations", []) + degradations))
        if not materials and case_data is not None:
            result["status"] = STATUS_INSUFFICIENT
        write_result(result, output_dir)

    return inventory


# ---------------------------------------------------------------------------
# fragment 深合并
# ---------------------------------------------------------------------------
def deep_merge(base, patch):
    for k, v in patch.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_fragments(work_dir):
    """读取 fragments（按文件名排序），返回 (merged, present_ids)。"""
    frag_dir = os.path.join(work_dir, "fragments")
    merged = {}
    present = []
    if not os.path.isdir(frag_dir):
        return merged, present
    for name in sorted(os.listdir(frag_dir)):
        if not name.endswith(".json"):
            continue
        data, err = safe_json_load(os.path.join(frag_dir, name))
        if err or not isinstance(data, dict):
            continue
        present.append(name[:-5])
        deep_merge(merged, data)
    return merged, present


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def derive_status(result, case_missing, materials_present):
    """状态由内容诚实推导，不由手写指定，保证同输入同状态。"""
    if case_missing:
        return STATUS_INPUT_ERROR
    if not materials_present:
        return STATUS_INSUFFICIENT
    if (result.get("research_question") is not None
            and result.get("prior_work") is not None
            and result.get("novelty") is not None
            and result.get("feasibility") is not None
            and (result.get("evidence_matrix") or [])):
        return STATUS_OK
    return STATUS_PARTIAL


def validate(result, contract):
    """返回 list[str] 校验错误。status=ok 才强制四项交付与证据齐全。"""
    errs = []
    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        errs.append("case_id 缺失")
    if result.get("status") not in ALLOWED_STATUS:
        errs.append("status 非法: %r" % result.get("status"))
    for key in contract:
        if key not in result:
            errs.append("契约键未回填到顶层: %s" % key)
    if result.get("status") == STATUS_OK:
        for d in ("research_question", "prior_work", "novelty", "feasibility"):
            if result.get(d) is None:
                errs.append("四项交付缺失: %s" % d)
    ev = result.get("evidence_matrix") or []
    for i, row in enumerate(ev):
        if not isinstance(row, dict):
            errs.append("evidence_matrix[%d] 非对象" % i)
            continue
        if not row.get("source_id"):
            errs.append("evidence_matrix[%d] 缺 source_id" % i)
        if not row.get("locator"):
            errs.append("evidence_matrix[%d] 缺 locator" % i)
        if not row.get("quote"):
            errs.append("evidence_matrix[%d] 缺逐字 quote" % i)
    return errs


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
def render_report(result):
    lines = ["# 科研选题与创新性评估报告", "",
             "- case_id: `%s`" % result.get("case_id"),
             "- status: `%s`" % result.get("status"), ""]

    rq = result.get("research_question") or {}
    lines.append("## 1. 研究问题（可检验陈述）")
    lines.append("")
    lines.append("- 可检验陈述：%s" % (rq.get("testable_statement") or "（未给出）"))
    for k, label in (("object", "研究对象"), ("baseline", "比较基线"),
                     ("expected", "预期结论"), ("falsification", "可证伪条件")):
        if rq.get(k):
            lines.append("- %s：%s" % (label, rq[k]))

    nv = result.get("novelty") or {}
    claims = nv.get("claims") or []
    lines.append("")
    lines.append("## 2. 创新点判定（共 %d 条）" % len(claims))
    lines.append("")
    for c in claims:
        lines.append("- [%s] %s — 覆盖情况：%s%s" % (
            c.get("category", "?"), c.get("claim", "?"), c.get("verdict", "?"),
            ("（covered_by: %s）" % c["covered_by"]) if c.get("covered_by") else ""))

    fe = result.get("feasibility") or {}
    pre = fe.get("preconditions") or []
    lines.append("")
    lines.append("## 3. 可行性前提（共 %d 条）" % len(pre))
    lines.append("")
    for p in pre:
        lines.append("- [%s] %s：%s" % (p.get("state", "?"), p.get("item", "?"), p.get("basis", "")))

    syn = result.get("synthesis") or {}
    lines.append("")
    lines.append("## 4. 综合结论")
    lines.append("")
    for c in (syn.get("conclusions") or []):
        lines.append("- %s" % c)
    gaps = syn.get("research_gaps") or []
    if gaps:
        lines.append("")
        lines.append("## 5. 研究空白")
        lines.append("")
        for g in gaps:
            lines.append("- %s" % g)
    b = syn.get("boundary_statement")
    if b:
        lines.append("")
        lines.append("## 6. 边界说明")
        lines.append("")
        lines.append(b)

    ev = result.get("evidence_matrix") or []
    lines.append("")
    lines.append("## 7. 证据矩阵（%d 条）" % len(ev))
    lines.append("")
    for row in ev:
        lines.append("- `%s` @ %s：%s" % (
            row.get("source_id", "?"), row.get("locator", "?"), (row.get("quote") or "")[:80]))

    un = result.get("uncertainty") or {}
    if un.get("gaps") or un.get("unverifiable") or un.get("biases"):
        lines.append("")
        lines.append("## 8. 不确定性")
        lines.append("")
        for k, label in (("biases", "偏倚"), ("gaps", "空白"), ("unverifiable", "无法确定")):
            for x in (un.get(k) or []):
                lines.append("- [%s] %s" % (label, x))

    errs = result.get("errors") or []
    degs = result.get("degradations") or []
    ph = result.get("pending_human") or []
    lim = result.get("limitations") or []
    if errs or degs or ph or lim:
        lines.append("")
        lines.append("## 9. 错误 / 降级 / 待确认 / 限制")
        lines.append("")
        for x in errs:
            lines.append("- [error] %s" % x)
        for x in degs:
            lines.append("- [degraded] %s" % x)
        for x in ph:
            lines.append("- [pending_human] %s" % x)
        for x in lim:
            lines.append("- [limitation] %s" % x)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# finalize：合并 fragment + 校验 + 写最终 result.json / report.md
# ---------------------------------------------------------------------------
def run_finalize(input_dir, work_dir, output_dir):
    entries = discover_files(input_dir)
    case_data, case_error = safe_json_load(os.path.join(input_dir, "case.json"))
    contract, contract_error = resolve_contract(case_data)

    result_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(result_path):
        result, load_err = safe_json_load(result_path)
    else:
        result, load_err = None, "skeleton 缺失"

    if result is None or not isinstance(result, dict):
        result = build_skeleton(input_dir, work_dir, output_dir, case_data, case_error,
                                entries, contract, contract_error)

    fragments, present_ids = load_fragments(work_dir)
    deep_merge(result, fragments)

    # 契约：若 case.json 更新过 output_contract，重合并回显，保证不回填丢失；
    # 同时确保标准元字段（sources/uncertainty/… ）始终存在。
    result["output_contract"] = contract
    for key in list(DEFAULT_CONTRACT.keys()) + [k for k in contract if k not in DEFAULT_CONTRACT]:
        if key not in result:
            result[key] = empty_for(key)

    # 状态由内容推导（同输入同状态）
    case_missing = not os.path.isfile(os.path.join(input_dir, "case.json"))
    sources = result.get("sources") or []
    materials_present = any(
        isinstance(s, dict) and s.get("type") == "material" and s.get("status") == "ok"
        for s in sources)
    result["status"] = derive_status(result, case_missing, materials_present)

    # change_log 幂等重建（确定性：同 fragments 同日志），不追加
    log = [{"seq": 1, "step": "skeleton",
            "action": "落盘 partial 骨架，契约字段全量就位为空值",
            "reason": "先保证任何中断都不留下不完整产物"}]
    seq = 2
    ordered = [fid for fid in FRAGMENT_IDS if fid in present_ids]
    extra = sorted([pid for pid in present_ids if pid not in FRAGMENT_IDS])
    for fid in ordered + extra:
        log.append({"seq": seq, "step": fid, "action": "深合并 fragment", "reason": "增量改进"})
        seq += 1
    log.append({"seq": seq, "step": "finalize",
                "action": "合并+校验+写 report.md", "reason": "收尾"})
    result["change_log"] = log

    errs = validate(result, contract)
    if errs:
        result["errors"] = list(dict.fromkeys((result.get("errors") or []) + errs))
        if result["status"] == STATUS_OK:
            result["status"] = STATUS_PARTIAL
    if contract_error and contract_error not in (result.get("degradations") or []):
        result["degradations"] = (result.get("degradations") or []) + [contract_error]

    os.makedirs(output_dir, exist_ok=True)
    write_result(result, output_dir)
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_report(result))

    # 运行耗时只写 work/run.log，不进入 result.json（保证主产物确定性）
    with open(os.path.join(work_dir, "run.log"), "a", encoding="utf-8") as fh:
        fh.write("finalize ok; validation_errors=%d; fragments=%s\n" % (len(errs), ordered + extra))

    return 2 if errs else 0


def main(argv=None):
    """core_impl 直接调用入口（供 main.py 委托）。"""
    argv = sys.argv[1:] if argv is None else argv
    print("core_impl 是库模块，请通过 scripts/main.py 调用：skeleton/evidence/finalize")
    return 0
