#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core_impl.py — 学术论文写作 Skill 的核心实现（纯标准库，无第三方依赖）。

职责边界（重要）：本模块只做"确定性、可复核"的机械工作，不生成任何科研内容：

  1. bootstrap_result   读取 /input，落盘 result.json 骨架 + 材料索引 + 契约元信息；
  2. validate_evidence  校验稿件 [S:]/[C:] 标记与材料索引一一对应，并强制
                        "每条结论带 locator + 逐字 quote"；
  3. check_consistency  检查章节 / 图表编号 / 引用定义 / 待核标记，输出待核清单；
  4. selfcheck          在临时目录构建最小夹具，跑通全流程并自检退出码。

事实内容由调用方 Agent 依据原始材料撰写；本模块只负责"可核验性"这一层，任何
数值、文献、结论都不在此产生，缺材料、缺软件只如实标注，绝不补造。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

# 单文件解析上限：超限跳过并记录，避免超大材料拖垮运行时
MAX_FILE_BYTES = 2 * 1024 * 1024

# output_contract 未定义时的默认契约字段（含义见 references/conventions.md）
DEFAULT_CONTRACT_FIELDS: Tuple[str, ...] = (
    "deliverable", "fact_source_map", "version_limits",
)
# result.json 的固定附加字段
AUX_FIELDS: Tuple[str, ...] = (
    "status", "sources", "uncertainty", "pending_human",
    "change_log", "errors", "degradations", "runtime",
)
VALID_STATUS: Tuple[str, ...] = (
    "complete", "partial", "insufficient_evidence", "input_error", "failed",
)
# 证据映射中的 claim 类型（比参考实现更细的证据结构）
CLAIM_TYPES: Tuple[str, ...] = (
    "number", "unit", "sample_size", "statistic", "figure",
    "table", "citation", "conclusion", "term", "procedure",
)

SRC_RE = re.compile(r"\[S:([^\]\s]+)\]")
CIT_RE = re.compile(r"\[C:([^\]\s]+)\]")
FLAG_RE = re.compile(r"\[(缺失|待确认|冲突待确认|无法解析|待核)\]")

# 章节完整度检查：中英任一门语言命中即视为存在
REQUIRED_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("摘要", "abstract"),
    ("引言", "introduction"),
    ("方法", "method"),
    ("结果", "result"),
    ("讨论", "discussion"),
    ("结论", "conclusion"),
    ("参考文献", "reference"),
)


# --------------------------------------------------------------------------
# 基础 IO（容错：BOM、坏编码、坏 JSON、超限文件都不崩）
# --------------------------------------------------------------------------
def now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path: str) -> Dict[str, Any]:
    """读取 UTF-8 文本，返回 {'ok','text','error','bytes'}。"""
    res: Dict[str, Any] = {"ok": False, "text": None, "error": None, "bytes": 0}
    if not path or not os.path.isfile(path):
        res["error"] = "not_a_file"
        return res
    try:
        size = os.path.getsize(path)
        res["bytes"] = size
        if size > MAX_FILE_BYTES:
            res["error"] = "too_large"
            return res
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        res["error"] = f"io_error:{exc}"
        return res
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            res["text"] = raw.decode(enc)
            res["ok"] = True
            return res
        except UnicodeDecodeError:
            continue
    res["text"] = raw.decode("utf-8", errors="replace")
    res["ok"] = True
    res["error"] = "decode_fallback"
    return res


def load_json(path: str) -> Optional[Any]:
    """容错读取 JSON；失败返回 None（不抛异常）。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def write_json(path: str, obj: Any) -> None:
    """原子写 JSON（先写临时文件再替换），失败不抛。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


# --------------------------------------------------------------------------
# 输入发现与契约回填
# --------------------------------------------------------------------------
def classify_kind(ext: str) -> str:
    ext = (ext or "").lower()
    table = {
        ".pdf": "pdf", ".md": "markdown", ".txt": "text", ".csv": "csv",
        ".tsv": "tsv", ".json": "json", ".jsonl": "jsonl", ".docx": "docx",
        ".doc": "doc", ".xlsx": "xlsx", ".pptx": "pptx", ".png": "image",
        ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
        ".svg": "image", ".tex": "latex", ".bib": "bibtex", ".html": "html",
    }
    return table.get(ext, "other")


def discover_inputs(input_dir: str) -> Dict[str, Any]:
    """扫描输入目录（含一层子目录，如 materials/），不假设固定文件名。"""
    out: Dict[str, Any] = {"case_path": None, "task_path": None, "materials": []}
    if not input_dir or not os.path.isdir(input_dir):
        return out
    files: List[str] = []
    try:
        for root, dirs, names in os.walk(input_dir):
            dirs.sort()
            for name in sorted(names):
                files.append(os.path.join(root, name))
    except OSError:
        return out
    for p in files:
        low = os.path.basename(p).lower()
        if low == "case.json" and out["case_path"] is None:
            out["case_path"] = p
        elif low == "task.md" and out["task_path"] is None:
            out["task_path"] = p
        else:
            out["materials"].append(p)
    if out["case_path"] is None:  # 兜底：第一个 .json
        for p in out["materials"]:
            if p.lower().endswith(".json"):
                out["case_path"] = p
                break
    if out["task_path"] is None:  # 兜底：第一个 .md
        for p in out["materials"]:
            if p.lower().endswith(".md"):
                out["task_path"] = p
                break
    return out


def extract_case_id(case: Any, case_path: Optional[str]) -> str:
    if isinstance(case, dict):
        for key in ("case_id", "id"):
            v = case.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if case_path:
        stem = os.path.splitext(os.path.basename(case_path))[0]
        if stem:
            return stem
    return "unknown_case"


def extract_contract_fields(case: Any) -> List[str]:
    """从 case.json 的 output_contract 取字段名；缺失时用默认字段。"""
    if isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        keys = [k for k in case["output_contract"] if isinstance(k, str) and k.strip()]
        if keys:
            return keys
    return list(DEFAULT_CONTRACT_FIELDS)


def inventory_materials(paths: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for i, p in enumerate(paths, 1):
        item: Dict[str, Any] = {
            "material_id": f"M{i}",
            "path": p,
            "rel_path": os.path.basename(p),
            "ext": os.path.splitext(p)[1].lower(),
        }
        try:
            item["bytes"] = os.path.getsize(p)
        except OSError:
            item["bytes"] = None
        item["kind"] = classify_kind(item["ext"])
        item["parse_status"] = "unparsed"
        item["claims"] = []      # 由 Agent 在 Step 1 逐步填实
        item["citations"] = []   # 由 Agent 在 Step 1 逐步填实
        item["flags"] = []
        items.append(item)
    return items


# --------------------------------------------------------------------------
# 1) 骨架落盘：前 3 分钟必做，先写合法的 partial 结果
# --------------------------------------------------------------------------
def bootstrap_result(input_dir: str, output_dir: str, work_dir: str,
                     now: Optional[str] = None) -> Dict[str, Any]:
    now = now or now_ts()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    inputs = discover_inputs(input_dir)
    case = load_json(inputs["case_path"]) if inputs["case_path"] else None
    task_res = read_text(inputs["task_path"]) if inputs["task_path"] else {"ok": False, "error": "missing"}
    case_id = extract_case_id(case, inputs["case_path"])
    contract_fields = extract_contract_fields(case)
    materials = inventory_materials(inputs["materials"])

    degradations: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    if inputs["case_path"] is None:
        errors.append({"where": "input", "what": "case.json 缺失"})
    if not task_res.get("ok"):
        degradations.append({"where": "input", "what": f"task.md 缺失或读取失败: {task_res.get('error')}"})

    # 诚实状态：材料不足 -> insufficient_evidence；否则骨架属于 partial
    status = "insufficient_evidence" if (inputs["case_path"] is None and not materials) else "partial"

    # 材料索引（骨架阶段 claims/citations 为空，由 Agent 逐条填实）
    index: Dict[str, Any] = {
        "case_id": case_id,
        "materials": materials,
        "conflicts": [],
        "gaps": [],
        "version": 2,
    }
    # 契约元信息
    meta: Dict[str, Any] = {
        "case_id": case_id,
        "contract_fields": contract_fields,
        "has_task_md": bool(inputs["task_path"]),
        "has_case_json": bool(inputs["case_path"]),
        "material_count": len(materials),
        "input_dir": input_dir,
    }
    # result.json 骨架：case_id + output_contract 全字段 + 固定附加字段
    result: Dict[str, Any] = {"case_id": case_id, "status": status}
    for f in contract_fields:
        result[f] = None
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{
        "ts": now, "step": "bootstrap", "note":
        f"落盘骨架 status={status}；deliverable 尚未产出，材料 {len(materials)} 份待索引",
    }]
    result["errors"] = errors
    result["degradations"] = degradations
    result["runtime"] = {
        "budget_seconds": 900,
        "budget_requests": 64,
        "max_tokens_per_response": 16384,
        "started_at": now,
        "steps": [{"name": "bootstrap", "at": now}],
    }

    write_json(os.path.join(work_dir, "material_index.json"), index)
    write_json(os.path.join(work_dir, "contract_meta.json"), meta)
    write_json(os.path.join(output_dir, "result.json"), result)
    return {
        "ok": True, "case_id": case_id, "status": status,
        "contract_fields": contract_fields, "material_count": len(materials),
        "result_path": os.path.join(output_dir, "result.json"),
        "index_path": os.path.join(work_dir, "material_index.json"),
    }


# --------------------------------------------------------------------------
# 标记抽取
# --------------------------------------------------------------------------
def extract_markers(text: str) -> Tuple[List[str], List[str], List[str]]:
    """返回 (source_keys, citation_keys, flags)。"""
    sources = [m.group(1) for m in SRC_RE.finditer(text)]
    citations = [m.group(1) for m in CIT_RE.finditer(text)]
    flags = [m.group(1) for m in FLAG_RE.finditer(text)]
    return sources, citations, flags


# --------------------------------------------------------------------------
# 2) 证据溯源校验：强制 locator + 逐字 quote，悬空即告警
# --------------------------------------------------------------------------
def _collect_index(index: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    claims: Dict[str, Any] = {}
    citations: Dict[str, Any] = {}
    if isinstance(index, dict):
        for mat in index.get("materials", []) or []:
            if not isinstance(mat, dict):
                continue
            for c in mat.get("claims", []) or []:
                if isinstance(c, dict) and c.get("claim_id"):
                    claims[str(c["claim_id"])] = c
            for cit in mat.get("citations", []) or []:
                if isinstance(cit, dict) and cit.get("key"):
                    citations[str(cit["key"])] = cit
    return claims, citations


def validate_evidence(manuscript_path: str, index_path: str, work_dir: str,
                      now: Optional[str] = None) -> Dict[str, Any]:
    now = now or now_ts()
    os.makedirs(work_dir, exist_ok=True)
    txt_res = read_text(manuscript_path)
    txt = txt_res.get("text") or ""
    index = load_json(index_path)
    claims, citations = _collect_index(index)

    sources, cites, flags = extract_markers(txt)

    src_report: List[Dict[str, Any]] = []
    for key in sources:
        claim = claims.get(key)
        if claim is None:
            src_report.append({"key": key, "status": "dangling",
                               "reason": "未在材料索引的 claims 中定义"})
        else:
            problems = []
            if not claim.get("locator"):
                problems.append("no_locator")
            if not str(claim.get("quote", "")).strip():
                problems.append("no_quote")
            src_report.append({
                "key": key,
                "status": "verified" if not problems else "missing_trace",
                "type": claim.get("type", "unknown"),
                "problems": problems,
                "has_locator": bool(claim.get("locator")),
                "quote_len": len(str(claim.get("quote", ""))),
                "locator": claim.get("locator"),
                "quote": claim.get("quote"),
            })

    cit_report: List[Dict[str, Any]] = []
    for key in cites:
        cit = citations.get(key)
        if cit is None:
            cit_report.append({"key": key, "status": "dangling",
                               "reason": "未在材料索引的 citations 中定义"})
        else:
            problems = []
            for field in ("authors", "year"):
                if not str(cit.get(field, "")).strip():
                    problems.append(f"no_{field}")
            cit_report.append({"key": key,
                               "status": "verified" if not problems else "incomplete",
                               "problems": problems, "entry": cit})

    # 反向：已定义但正文未使用（可疑装饰性/错配引用）
    used_src = set(sources)
    used_cit = set(cites)
    unused_claims = [k for k in claims if k not in used_src]
    unused_citations = [k for k in citations if k not in used_cit]

    def _cnt(entries: List[Dict[str, Any]], st: str) -> int:
        return sum(1 for e in entries if e.get("status") == st)

    summary = {
        "sources_total": len(sources),
        "sources_verified": _cnt(src_report, "verified"),
        "sources_dangling": _cnt(src_report, "dangling"),
        "sources_missing_trace": _cnt(src_report, "missing_trace"),
        "citations_total": len(cites),
        "citations_verified": _cnt(cit_report, "verified"),
        "citations_dangling": _cnt(cit_report, "dangling"),
        "citations_incomplete": _cnt(cit_report, "incomplete"),
        "unused_claims": len(unused_claims),
        "unused_citations": len(unused_citations),
        "flags": flags,
    }
    report: Dict[str, Any] = {
        "generated_at": now,
        "draft": os.path.basename(manuscript_path) if manuscript_path else None,
        "index": os.path.basename(index_path) if index_path else None,
        "sources": src_report,
        "citations": cit_report,
        "flags": flags,
        "unused_claims": unused_claims,
        "unused_citations": unused_citations,
        "summary": summary,
    }
    write_json(os.path.join(work_dir, "evidence_map_validation.json"), report)
    return report


# --------------------------------------------------------------------------
# 3) 一致性检查：章节 / 图表编号 / 引用 / 待核标记 + 收口三查
# --------------------------------------------------------------------------
def _extract_numbers(text: str, pattern: str) -> List[int]:
    nums: List[int] = []
    for m in re.finditer(pattern, text):
        try:
            nums.append(int(m.group(1)))
        except ValueError:
            continue
    return nums


def _find_gaps(nums: List[int]) -> List[int]:
    if not nums:
        return []
    mx = max(nums)
    present = set(nums)
    return [n for n in range(1, mx + 1) if n not in present]


def check_consistency(manuscript_path: str, index_path: Optional[str],
                      work_dir: str, result_path: Optional[str] = None,
                      now: Optional[str] = None) -> Dict[str, Any]:
    now = now or now_ts()
    os.makedirs(work_dir, exist_ok=True)
    txt_res = read_text(manuscript_path)
    txt = txt_res.get("text") or ""
    txt_lower = txt.lower()

    # 章节完整度：中英任一命中即可
    missing_sections = [zh for zh, en in REQUIRED_SECTIONS
                        if zh not in txt and en not in txt_lower]

    # 图表编号连续
    fig_nums = _extract_numbers(txt, r"(?:图|Figure|Fig\.?)\s*(\d+)")
    tab_nums = _extract_numbers(txt, r"(?:表|Table)\s*(\d+)")
    fig_gaps = _find_gaps(fig_nums)
    tab_gaps = _find_gaps(tab_nums)

    sources, cites, flags = extract_markers(txt)
    dangling_cites = []
    index = load_json(index_path) if index_path else None
    if index:
        _, citations = _collect_index(index)
        dangling_cites = [k for k in cites if k not in citations]
    else:
        dangling_cites = cites  # 无索引时全部视为待核

    # 收口三查之一：result.json 可被 json.load
    result_loadable = False
    result_missing_keys: List[str] = []
    if result_path and os.path.isfile(result_path):
        r = load_json(result_path)
        if isinstance(r, dict):
            result_loadable = True
            for k in ("case_id", "status", "sources", "uncertainty", "change_log"):
                if k not in r:
                    result_missing_keys.append(k)
    else:
        result_missing_keys = ["result.json 不存在"]

    # 收口三查之三：uncertainty 是否覆盖全部待核标记
    uncovered_flags = []
    if result_path and os.path.isfile(result_path):
        r = load_json(result_path)
        if isinstance(r, dict):
            unc = json.dumps(r.get("uncertainty", []), ensure_ascii=False)
            uncovered_flags = [f for f in set(flags) if f not in unc]

    report: Dict[str, Any] = {
        "generated_at": now,
        "draft": os.path.basename(manuscript_path) if manuscript_path else None,
        "missing_sections": missing_sections,
        "figure_count": len(set(fig_nums)),
        "figure_gaps": fig_gaps,
        "table_count": len(set(tab_nums)),
        "table_gaps": tab_gaps,
        "citation_keys": cites,
        "dangling_citations": dangling_cites,
        "flags": flags,
        "result_loadable": result_loadable,
        "result_missing_keys": result_missing_keys,
        "uncovered_flags": uncovered_flags,
        "ok": (not missing_sections and not fig_gaps and not tab_gaps
               and not dangling_cites and result_loadable
               and not result_missing_keys and not uncovered_flags),
    }
    write_json(os.path.join(work_dir, "consistency_report.json"), report)
    return report


# --------------------------------------------------------------------------
# 4) 收口合并：把 evidence/check 报告合并回 result.json（不覆盖模型手写字段）
# --------------------------------------------------------------------------
def merge_reports(result_path: str, work_dir: str, now: Optional[str] = None) -> Dict[str, Any]:
    now = now or now_ts()
    result = load_json(result_path)
    if not isinstance(result, dict):
        result = {"case_id": "unknown_case"}
    ev = load_json(os.path.join(work_dir, "evidence_map_validation.json"))
    chk = load_json(os.path.join(work_dir, "consistency_report.json"))

    # fact_source_map：指向证据校验报告并内嵌摘要（不覆盖模型已写的详细映射）
    if ev and not isinstance(result.get("fact_source_map"), dict):
        result["fact_source_map"] = {
            "path": os.path.join(work_dir, "evidence_map_validation.json"),
            "summary": ev.get("summary", {}),
        }

    # sources：只回填"已校验通过"的条目，保留 locator + 逐字 quote
    if ev:
        srcs: List[Dict[str, Any]] = []
        for e in ev.get("sources", []) or []:
            if e.get("status") == "verified":
                srcs.append({
                    "claim_id": e.get("key"),
                    "type": e.get("type"),
                    "locator": e.get("locator"),
                    "quote": e.get("quote"),
                })
        result["sources"] = srcs

    # uncertainty：追加悬空/缺溯源/引用问题与待核标记
    unc: List[Dict[str, Any]] = list(result.get("uncertainty", []) or [])
    if ev:
        for e in ev.get("sources", []) or []:
            if e.get("status") == "dangling":
                unc.append({"what": f"悬空来源 [S:{e.get('key')}]", "status": "dangling",
                            "evidence": e.get("reason", "")})
            elif e.get("status") == "missing_trace":
                unc.append({"what": f"缺 locator/逐字 quote：[S:{e.get('key')}]",
                            "status": "missing_trace",
                            "evidence": ",".join(e.get("problems", []))})
        for e in ev.get("citations", []) or []:
            if e.get("status") != "verified":
                unc.append({"what": f"引用问题 [C:{e.get('key')}]", "status": e.get("status"),
                            "evidence": e.get("reason") or ",".join(e.get("problems", []))})
        for f in ev.get("flags", []) or []:
            unc.append({"what": f"待核标记 [{f}]", "status": "flag", "evidence": "稿件占位标记"})
    result["uncertainty"] = unc

    # version_limits：合并差异/缺口/异常（保留模型手写的 pending_human 与 diff）
    vl = result.get("version_limits")
    if not isinstance(vl, dict):
        vl = {"diff": [], "pending_human": list(result.get("pending_human", []) or []),
              "gaps": [], "anomalies": []}
    if chk:
        gaps = list(chk.get("missing_sections", []) or [])
        gaps += [f"图缺号:{g}" for g in chk.get("figure_gaps", []) or []]
        gaps += [f"表缺号:{g}" for g in chk.get("table_gaps", []) or []]
        vl["gaps"] = gaps
        vl["anomalies"] = [f"悬空引用 [C:{k}]" for k in chk.get("dangling_citations", []) or []]
    result["version_limits"] = vl

    # 若状态仍是 partial，且无缺口、无悬空，可提升为 complete；否则保持 partial
    if result.get("status") == "partial":
        ok = (not (ev and (ev.get("summary") or {}).get("sources_dangling"))
              and not (ev and (ev.get("summary") or {}).get("sources_missing_trace"))
              and not (chk and (chk.get("missing_sections") or chk.get("figure_gaps")
                                or chk.get("table_gaps") or chk.get("dangling_citations")))
              and result.get("deliverable"))
        if ok:
            result["status"] = "complete"

    cl = list(result.get("change_log", []) or [])
    cl.append({"ts": now, "step": "merge",
               "note": f"合并 evidence/check 报告，status={result.get('status')}"})
    result["change_log"] = cl

    write_json(result_path, result)
    return {"ok": True, "result_path": result_path, "status": result.get("status")}


# --------------------------------------------------------------------------
# 5) 自检：最小夹具跑通全流程
# --------------------------------------------------------------------------
def selfcheck() -> Dict[str, Any]:
    """在临时目录构建最小夹具，验证三个核心函数可运行且产物合法。"""
    checks: List[Dict[str, Any]] = []
    root = tempfile.mkdtemp(prefix="acadwrite_selftest_")
    try:
        in_dir = os.path.join(root, "input")
        out_dir = os.path.join(root, "output")
        work_dir = os.path.join(root, "work")
        mat_dir = os.path.join(in_dir, "materials")
        os.makedirs(mat_dir, exist_ok=True)

        # 夹具输入
        write_json(os.path.join(in_dir, "case.json"), {
            "id": "case-demo-001",
            "output_contract": {"deliverable": None, "fact_source_map": None, "version_limits": None},
        })
        with open(os.path.join(in_dir, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# demo task\n撰写一段结果。\n")
        with open(os.path.join(mat_dir, "exp1.md"), "w", encoding="utf-8") as fh:
            fh.write("n=12，p=0.03。\n")

        # 1) 骨架落盘
        boot = bootstrap_result(in_dir, out_dir, work_dir, now="2026-01-01T00:00:00Z")
        r = load_json(os.path.join(out_dir, "result.json"))
        checks.append({"name": "bootstrap_result_json", "ok": isinstance(r, dict)
                       and r.get("case_id") == "case-demo-001"
                       and r.get("status") == "partial"
                       and "deliverable" in r and "change_log" in r,
                       "detail": (r or {}).get("status")})

        # 2) 模拟 Agent 填实材料索引 + 写稿件
        idx = load_json(os.path.join(work_dir, "material_index.json"))
        idx["materials"][0]["claims"] = [{
            "claim_id": "M1-C1", "type": "statistic",
            "locator": {"path": "materials/exp1.md", "page": 1, "span": "L1"},
            "quote": "n=12，p=0.03。",
        }]
        idx["materials"][0]["citations"] = [{
            "key": "ref1", "authors": "Smith et al.", "year": 2020,
            "journal": "J. Test", "doi": "unknown",
        }]
        write_json(os.path.join(work_dir, "material_index.json"), idx)
        with open(os.path.join(out_dir, "manuscript.md"), "w", encoding="utf-8") as fh:
            fh.write("样本量 n=12 [S:M1-C1]，p=0.03 [S:M1-C1][C:ref1]。\n\n## 参考文献\n[C:ref1]\n")

        # 3) 证据溯源校验
        ev = validate_evidence(os.path.join(out_dir, "manuscript.md"),
                               os.path.join(work_dir, "material_index.json"), work_dir,
                               now="2026-01-01T00:00:00Z")
        checks.append({"name": "validate_evidence_verified",
                       "ok": ev["summary"]["sources_verified"] >= 1
                       and ev["summary"]["sources_dangling"] == 0
                       and ev["summary"]["citations_dangling"] == 0,
                       "detail": ev["summary"]})

        # 4) 一致性检查
        chk = check_consistency(os.path.join(out_dir, "manuscript.md"),
                                os.path.join(work_dir, "material_index.json"),
                                work_dir, result_path=os.path.join(out_dir, "result.json"),
                                now="2026-01-01T00:00:00Z")
        checks.append({"name": "consistency_report", "ok": isinstance(chk, dict)
                       and chk.get("result_loadable") is True,
                       "detail": {"missing_sections": chk.get("missing_sections")}})

        # 5) 收口合并
        merged = merge_reports(os.path.join(out_dir, "result.json"), work_dir,
                               now="2026-01-01T00:00:00Z")
        r3 = load_json(os.path.join(out_dir, "result.json"))
        checks.append({"name": "merge_reports", "ok": isinstance(r3, dict)
                       and isinstance(r3.get("fact_source_map"), dict)
                       and len(r3.get("sources", [])) >= 1
                       and any(e.get("step") == "merge" for e in r3.get("change_log", [])),
                       "detail": {"status": merged.get("status"),
                                  "sources": len(r3.get("sources", []))}})

        # 6) 空目录降级
        empty_dir = os.path.join(root, "empty_in")
        os.makedirs(empty_dir, exist_ok=True)
        empty_out = os.path.join(root, "empty_out")
        empty_work = os.path.join(root, "empty_work")
        boot2 = bootstrap_result(empty_dir, empty_out, empty_work, now="2026-01-01T00:00:00Z")
        r2 = load_json(os.path.join(empty_out, "result.json"))
        checks.append({"name": "empty_input_degrades",
                       "ok": isinstance(r2, dict)
                       and r2.get("status") == "insufficient_evidence"
                       and "errors" in r2,
                       "detail": boot2.get("status")})
    except Exception as exc:  # noqa: BLE001 —— 自检失败也要返回结果
        checks.append({"name": "unexpected_exception", "ok": False, "detail": repr(exc)})
    finally:
        shutil.rmtree(root, ignore_errors=True)

    ok_all = all(c.get("ok") for c in checks)
    return {"ok": ok_all, "checks": checks, "failed": [c["name"] for c in checks if not c.get("ok")]}
