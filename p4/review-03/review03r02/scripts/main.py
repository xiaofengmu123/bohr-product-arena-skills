#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""研究方法与统计审查 — 预算账本版 Skill 入口。

纯 Python 标准库实现，无第三方依赖。子命令：

  discover  扫描 /input，抽取文本（PDF/纯文本/docx），构建 manifest/segments/terms
  scaffold  前 3 分钟先落盘一版“冻结的”合法 result.json 骨架（contract-first）
  finalize  合并 findings、跑数值一致性候选检查、校验、渲染 report.md
  status    打印预算账本（已用秒数 / 已用请求 / 各阶段状态）

缺省路径与 Harness 对齐：--input /input，--output /app/output，
--work /app/work。同一输入重复运行，除 runtime 时间戳外的字段保持一致。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

BUDGET_SECONDS = 900
BUDGET_REQUESTS = 64

# 五个检查面（facet）与六类审查面（check_class）
FACETS = [
    "research_design",
    "variable_definition",
    "statistical_methods",
    "sample_size_power",
    "results_reporting",
]
CHECK_CLASSES = [
    "fact_check",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argumentation_chain",
    "academic_norms",
]
SEVERITIES = ["critical", "major", "minor", "advisory", "pending"]
FINDING_TYPES = [
    "factual_error",
    "insufficient_evidence",
    "normative_issue",
    "advisory",
    "pending_review",
]
REQUIRED_FINDING_KEYS = ["id", "facet", "locator", "quote", "severity", "suggestion"]

# 阶段预算（秒 / 请求），总和 = 900 / 64
PHASE_BUDGET = [
    {"id": 0, "label": "discover+scaffold", "seconds": 150, "requests": 6},
    {"id": 1, "label": "facet_review", "seconds": 300, "requests": 22},
    {"id": 2, "label": "recheck_cross", "seconds": 270, "requests": 24},
    {"id": 3, "label": "selftest_finalize", "seconds": 180, "requests": 12},
]

MATERIAL_EXTS = {".pdf", ".txt", ".md", ".csv", ".json", ".docx", ".doc", ".tex", ".rst"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, obj) -> None:
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return default


def read_text_safe(path: Path):
    """utf-8-sig 优先，失败退 latin-1；返回 (text, errors)"""
    errors = []
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read(), errors
        except (UnicodeDecodeError, OSError) as exc:
            errors.append(f"{enc}: {exc}")
    try:
        with open(path, "r", encoding="latin-1", errors="replace") as fh:
            return fh.read(), errors
    except OSError as exc:
        return "", [str(exc)]


def extract_pdf(path: Path):
    """用 pdftotext（外部工具）抽取文本并按换页符切页。返回 (pages, error)"""
    exe = shutil.which("pdftotext")
    if not exe:
        return None, "pdftotext not found"
    try:
        proc = subprocess.run(
            [exe, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            timeout=240,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"pdftotext failed: {exc}"
    text = (proc.stdout or b"").decode("utf-8", errors="replace")
    if not text.strip():
        return None, "empty PDF text (possible scanned PDF, needs OCR)"
    pages = text.split("\f")
    return [p for p in pages], None


def extract_docx(path: Path):
    """仅用标准库读取 word/document.xml 的段落文本。"""
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{W}p"):
        runs = [t.text or "" for t in p.iter(f"{W}t")]
        paras.append("".join(runs))
    return "\n\n".join(paras)


def build_segments(source: str, pages):
    """把按页/按段文本切成可回查的段（页 + 段 + 字符偏移）。"""
    segs = []
    n = 0
    for page_idx, page_text in enumerate(pages, start=1):
        paras = re.split(r"\n\s*\n", page_text)
        for p_idx, para in enumerate(paras, start=1):
            text = para.strip()
            if not text:
                continue
            start = page_text.find(text)
            n += 1
            segs.append(
                {
                    "id": f"seg_{n:04d}",
                    "source": source,
                    "page": page_idx,
                    "para": p_idx,
                    "char_start": start if start >= 0 else 0,
                    "char_end": (start + len(text)) if start >= 0 else len(text),
                    "text": text,
                }
            )
    return segs


STAT_TERMS = [
    ("p_value", re.compile(r"\bp\s*(?:value|val)?\s*(?:=|<|>|≤|≥)\s*0?\.\d+", re.I)),
    ("ci", re.compile(r"\b\d{2}\s*%?\s*CI\b|confidence\s+interval", re.I)),
    ("n_sample", re.compile(r"\b[nN]\s*=\s*\d+")),
    ("test_name", re.compile(r"\b(t-?test|chi-?square|χ2|ANOVA|Mann-?Whitney|Wilcoxon|Kruskal|log-?rank|Cox|Fisher|Poisson|logistic|mixed[- ]effects|GEE)\b", re.I)),
    ("power", re.compile(r"\b(power|sample\s+size|effect\s+size|power\s+analysis)\b", re.I)),
    ("significance", re.compile(r"\bsignifican|\b显著|\b不显著", re.I)),
]


def build_terms(segments):
    terms = []
    for term, rx in STAT_TERMS:
        for seg in segments:
            for m in rx.finditer(seg["text"]):
                terms.append(
                    {
                        "term": term,
                        "locator": f"p.{seg['page']} ¶{seg['para']}",
                        "seg_id": seg["id"],
                        "match": m.group(0)[:80],
                    }
                )
    return terms


def iter_materials(input_dir: Path):
    """发现材料文件（排除 case.json / task.md）。"""
    skip = {"case.json", "task.md"}
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name.lower() in skip:
            continue
        if p.suffix.lower() in MATERIAL_EXTS:
            yield p


def cmd_discover(args):
    input_dir = Path(args.input)
    work_dir = Path(args.work)
    ensure_dir(work_dir / "texts")
    manifest = {
        "input_dir": str(input_dir),
        "case_json_found": (input_dir / "case.json").exists(),
        "task_md_found": (input_dir / "task.md").exists(),
        "materials": [],
        "errors": [],
        "degradations": [],
        "needs_ocr": [],
        "segments_file": str(work_dir / "segments.json"),
        "terms_file": str(work_dir / "terms.json"),
    }

    all_segments = []
    if not input_dir.exists():
        manifest["errors"].append(f"input dir not found: {input_dir}")
    else:
        for p in iter_materials(input_dir):
            rel = str(p.relative_to(input_dir))
            item = {
                "path": rel,
                "kind": p.suffix.lower().lstrip("."),
                "text_file": None,
                "segments": 0,
                "error": None,
            }
            pages = None
            if p.suffix.lower() == ".pdf":
                pages, err = extract_pdf(p)
                if err:
                    item["error"] = err
                    manifest["needs_ocr"].append(rel)
                    manifest["degradations"].append(f"{rel}: {err}")
            elif p.suffix.lower() == ".docx":
                try:
                    pages = [extract_docx(p)]
                except Exception as exc:  # noqa: BLE001
                    item["error"] = f"docx parse failed: {exc}"
                    manifest["degradations"].append(item["error"])
            elif p.suffix.lower() == ".json":
                # JSON 材料也保留原文，供逐字摘录；坏 JSON 降级为原文并记录
                raw, errs = read_text_safe(p)
                try:
                    pages = [json.dumps(json.loads(raw), ensure_ascii=False, indent=2)]
                except Exception as exc:  # noqa: BLE001
                    pages = [raw] if raw else None
                    manifest["degradations"].append(f"{rel}: invalid JSON: {exc}")
                if errs:
                    manifest["degradations"].extend(f"{rel}: {e}" for e in errs)
            else:
                text, errs = read_text_safe(p)
                if errs:
                    manifest["degradations"].extend(f"{rel}: {e}" for e in errs)
                pages = [text] if text else None

            if pages is not None:
                segs = build_segments(rel, pages)
                all_segments.extend(segs)
                item["segments"] = len(segs)
                text_out = work_dir / "texts" / (p.stem + ".txt")
                write_json(text_out, {"source": rel, "pages": pages, "segments": segs})
                item["text_file"] = "texts/" + p.stem + ".txt"
            manifest["materials"].append(item)

    # 去掉 None 占位
    manifest["errors"] = [e for e in manifest["errors"] if e]
    write_json(work_dir / "segments.json", all_segments)
    write_json(work_dir / "terms.json", build_terms(all_segments))
    write_json(work_dir / "manifest.json", manifest)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def load_contract(input_dir: Path):
    """读 case.json 的 output_contract（或 outputContract），返回 (case_id, contract_dict)"""
    case = load_json(input_dir / "case.json", default={})
    if not isinstance(case, dict):
        case = {}
    case_id = case.get("case_id") or case.get("id") or case.get("armiId") or "unknown"
    contract = case.get("output_contract", case.get("outputContract", {}))
    if isinstance(contract, list):
        contract = {str(k): None for k in contract}
    if not isinstance(contract, dict):
        contract = {}
    return case_id, contract


def empty_progress():
    return {f: "pending" for f in FACETS}


def base_result(case_id: str, contract: dict):
    result = {
        "case_id": case_id,
        "status": "partial",
        "summary": {
            "verdict": "unchecked",
            "counts": {"total": 0, "critical": 0, "major": 0, "minor": 0, "advisory": 0, "pending": 0},
            "by_facet": {f: 0 for f in FACETS},
            "by_type": {t: 0 for t in FINDING_TYPES},
        },
        "review_report": {"findings": []},
        "result_status": "unchecked",
        "evidence": {"evidence_map": {}, "segments_file": "segments.json"},
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": now_iso()},
        "numeric_checks": [],
        "progress": empty_progress(),
        "checked_classes": {c: 0 for c in CHECK_CLASSES},
    }
    # 契约字段原样回填：缺值给 null，不改名不删未知键
    for key, val in contract.items():
        result[key] = val if val is not None else None
    result["change_log"] = [
        {"seq": 1, "action": "scaffold", "phase": 0, "note": "contract-first partial skeleton written"}
    ]
    return result


def init_state(case_id: str):
    return {
        "case_id": case_id,
        "started_at": now_iso(),
        "budget": {
            "seconds_total": BUDGET_SECONDS,
            "requests_total": BUDGET_REQUESTS,
            "seconds_used": 0,
            "requests_used": 0,
        },
        "phases": [
            {"id": p["id"], "label": p["label"], "seconds_budget": p["seconds"],
             "requests_budget": p["requests"], "status": "pending", "requests_used": 0}
            for p in PHASE_BUDGET
        ],
        "facets": {f: "pending" for f in FACETS},
        "check_classes": {c: 0 for c in CHECK_CLASSES},
    }


def cmd_scaffold(args):
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    ensure_dir(output_dir)
    ensure_dir(work_dir / "findings")

    case_id, contract = load_contract(input_dir)
    has_case = (input_dir / "case.json").exists()
    manifest = load_json(work_dir / "manifest.json", default={})
    has_material = bool(manifest.get("materials"))

    result = base_result(case_id, contract)
    if not has_case and not has_material:
        # 缺 case.json 且无材料 → input_error；仍写合法产物
        result["status"] = "input_error"
        result["summary"]["verdict"] = "input_error"
        result["result_status"] = "unchecked"
        result["errors"].append("case.json not found and no material")
    elif has_case and not has_material:
        # 有任务但无材料 → insufficient_evidence，不编造
        result["status"] = "insufficient_evidence"
        result["summary"]["verdict"] = "insufficient_evidence"
        result["result_status"] = "unchecked"
        result["limitations"].append("case.json present but no material under /input")

    state = load_json(work_dir / "state.json", default=init_state(case_id))
    state["case_id"] = case_id
    state["phases"][0]["status"] = "done"

    write_json(output_dir / "result.json", result)
    write_json(work_dir / "state.json", state)
    render_report(result, output_dir / "report.md")

    print(json.dumps({"case_id": case_id, "status": result["status"],
                      "contract_fields": list(contract.keys()),
                      "output": str(output_dir / "result.json")}, ensure_ascii=False, indent=2))
    return 0


def p_value_from_text(text):
    """返回 (pvalue_as_float_or_None, operator)。解析失败的返回 (None, None)"""
    m = re.search(r"\bp\s*(?:value|val)?\s*(=|<|>|≤|≥)\s*(0?\.\d+)", text, re.I)
    if not m:
        return None, None
    op = m.group(1)
    try:
        val = float(m.group(2))
    except ValueError:
        return None, None
    if op == "<":
        return (val - 1e-9, "<")  # p<0.05 近似为略小于
    if op == ">":
        return (val + 1e-9, ">")
    return val, op


def ci_excludes_null(text):
    """尝试从同段文本解析 95% CI 两个端点；返回 True/False/None"""
    m = re.search(r"(\d{2}\s*%?\s*CI|CI\s*\d{2}\s*%?)\s*[:=]?", text, re.I)
    if not m:
        return None
    tail = text[m.end():m.end() + 60]
    nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", tail)]
    if len(nums) < 2:
        return None
    lo, hi = nums[0], nums[1]
    # 区间含 0（差值 null）或含 1（比值 null）→ 不排除 null；否则排除
    if (lo <= 0 <= hi) or (lo <= 1 <= hi):
        return False
    return True


def numeric_checks(segments):
    """扫描数值一致性候选，仅供人工复核确认后提升为 finding。"""
    candidates = []
    pos_words = re.compile(r"significan|\b显著\b", re.I)
    neg_words = re.compile(r"not\s+significan|no\s+significan|不显著|无显著|无统计学意义|n\.s\.", re.I)

    for seg in segments:
        pv, op = p_value_from_text(seg["text"])
        has_ci = re.search(r"\d{2}\s*%?\s*CI|confidence\s+interval", seg["text"], re.I)
        if pv is not None:
            neg = neg_words.search(seg["text"])
            pos = pos_words.search(seg["text"])
            if pv < 0.05 and neg:
                candidates.append({
                    "id": f"num_{len(candidates)+1:03d}",
                    "rule": "p_vs_wording",
                    "locator": f"p.{seg['page']} ¶{seg['para']}",
                    "seg_id": seg["id"],
                    "detail": "p<0.05 but wording states not significant",
                    "evidence": seg["text"][:220],
                })
            elif pv >= 0.05 and pos and not neg:
                candidates.append({
                    "id": f"num_{len(candidates)+1:03d}",
                    "rule": "p_vs_wording",
                    "locator": f"p.{seg['page']} ¶{seg['para']}",
                    "seg_id": seg["id"],
                    "detail": "p>=0.05 but wording states significant",
                    "evidence": seg["text"][:220],
                })
            if has_ci:
                excl = ci_excludes_null(seg["text"])
                if excl is True and pv >= 0.05:
                    candidates.append({
                        "id": f"num_{len(candidates)+1:03d}",
                        "rule": "p_vs_ci",
                        "locator": f"p.{seg['page']} ¶{seg['para']}",
                        "seg_id": seg["id"],
                        "detail": "95% CI excludes null but p>=0.05",
                        "evidence": seg["text"][:220],
                    })
                elif excl is False and pv < 0.05:
                    candidates.append({
                        "id": f"num_{len(candidates)+1:03d}",
                        "rule": "p_vs_ci",
                        "locator": f"p.{seg['page']} ¶{seg['para']}",
                        "seg_id": seg["id"],
                        "detail": "95% CI crosses null but p<0.05",
                        "evidence": seg["text"][:220],
                    })

    # 全材料 N/n 值不一致候选（可能是总数 vs 分组数，须人工确认）
    n_values = {}
    for seg in segments:
        for m in re.finditer(r"\b[nN]\s*=\s*(\d+)", seg["text"]):
            val = int(m.group(1))
            key = f"N={val}"
            n_values.setdefault(key, []).append(f"p.{seg['page']} ¶{seg['para']}")
    if len(n_values) > 1:
        candidates.append({
            "id": f"num_{len(candidates)+1:03d}",
            "rule": "n_inconsistency",
            "locator": "; ".join(sorted(n_values.keys())),
            "seg_id": None,
            "detail": "multiple distinct N= values across material (total vs subgroup?)",
            "evidence": "; ".join(f"{k} at {', '.join(v[:3])}" for k, v in sorted(n_values.items())),
        })
    return candidates


def validate_finding(f, out_errors, out_warnings):
    for k in REQUIRED_FINDING_KEYS:
        if k not in f or f.get(k) in (None, ""):
            out_errors.append(f"finding {f.get('id','?')} missing key {k}")
    if f.get("facet") not in FACETS:
        out_errors.append(f"finding {f.get('id','?')} invalid facet {f.get('facet')!r}")
    if f.get("severity") not in SEVERITIES:
        out_errors.append(f"finding {f.get('id','?')} invalid severity {f.get('severity')!r}")
    if f.get("finding_type") not in FINDING_TYPES:
        out_warnings.append(f"finding {f.get('id','?')} missing/unknown finding_type")
    if f.get("check_class") not in CHECK_CLASSES:
        out_warnings.append(f"finding {f.get('id','?')} missing/unknown check_class")


def cmd_finalize(args):
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    work_dir = Path(args.work)

    # 若骨架不存在，先落盘（保证任何调用顺序都有合法产物）
    if not (output_dir / "result.json").exists():
        cmd_scaffold(args)

    result = load_json(output_dir / "result.json", default={})
    if not isinstance(result, dict):
        result = base_result(*load_contract(input_dir))

    segments = load_json(work_dir / "segments.json", default=[])
    findings_dir = work_dir / "findings"
    findings = []
    if findings_dir.exists():
        for fp in sorted(findings_dir.glob("*.json")):
            f = load_json(fp)
            if isinstance(f, dict):
                findings.append(f)
    findings.sort(key=lambda x: str(x.get("id", "")))

    errors, warnings = [], []
    for f in findings:
        validate_finding(f, errors, warnings)

    result["review_report"]["findings"] = findings
    result["numeric_checks"] = numeric_checks(segments)

    # 统计计数
    counts = {"total": len(findings), "critical": 0, "major": 0, "minor": 0, "advisory": 0, "pending": 0}
    by_facet = {f: 0 for f in FACETS}
    by_type = {t: 0 for t in FINDING_TYPES}
    checked_classes = {c: 0 for c in CHECK_CLASSES}
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
        fac = f.get("facet")
        if fac in by_facet:
            by_facet[fac] += 1
        ft = f.get("finding_type")
        if ft in by_type:
            by_type[ft] += 1
        cc = f.get("check_class")
        if cc in checked_classes:
            checked_classes[cc] += 1
    result["summary"]["counts"] = counts
    result["summary"]["by_facet"] = by_facet
    result["summary"]["by_type"] = by_type
    result["checked_classes"] = checked_classes

    # 证据映射：每条 finding 的 locator + 逐字 quote + 状态
    evidence_map = {}
    for f in findings:
        evidence_map[str(f.get("id", ""))] = {
            "locator": f.get("locator", ""),
            "quote": f.get("quote", ""),
            "review_status": f.get("review_status", "unset"),
            "confidence": f.get("confidence", "unset"),
            "finding_type": f.get("finding_type", "unset"),
        }
    result["evidence"]["evidence_map"] = evidence_map
    result["evidence"]["segments_file"] = "segments.json"

    # 合并 agent 写的 meta.json（checked_facets / verdict / uncertainty / limitations 等）
    meta = load_json(work_dir / "meta.json", default={})
    if isinstance(meta, dict):
        if meta.get("overall_verdict"):
            result["summary"]["verdict"] = meta["overall_verdict"]
        if meta.get("status"):
            result["status"] = meta["status"]
        if meta.get("result_status"):
            result["result_status"] = meta["result_status"]
        if meta.get("sources"):
            result["sources"] = meta["sources"]
        if meta.get("uncertainty"):
            result["uncertainty"] = meta["uncertainty"]
        if meta.get("pending_human"):
            result["pending_human"] = meta["pending_human"]
        if meta.get("limitations"):
            result["limitations"] = meta["limitations"]
        if meta.get("errors"):
            result["errors"] = meta["errors"]
        if meta.get("degradations"):
            result["degradations"] = meta["degradations"]
        for f in FACETS:
            if meta.get("checked_facets") and f in meta["checked_facets"]:
                result["progress"][f] = "done"
            elif meta.get("unchecked_facets") and f in meta["unchecked_facets"]:
                result["progress"][f] = "unchecked"

    # 自动推导 verdict：有 hard error 则问题；无 finding 且无 material → 保持 unchecked/不足
    if counts["critical"] > 0:
        result["result_status"] = "problem"
    elif counts["total"] > 0:
        result["result_status"] = result.get("result_status") or "problem"
    if counts["total"] == 0 and result["status"] == "partial":
        result["result_status"] = "unchecked"

    # 变更记录（确定性：不用墙钟时间）
    base_log = [e for e in result.get("change_log", []) if e.get("action") != "finalize"]
    base_log.append({
        "seq": len(base_log) + 1,
        "action": "finalize",
        "phase": 3,
        "note": f"merged {counts['total']} findings, {len(errors)} schema errors, "
                f"{len(result['numeric_checks'])} numeric candidates",
    })
    result["change_log"] = base_log
    result["runtime"]["finished_at"] = now_iso()

    if errors:
        result["errors"] = list(dict.fromkeys(result.get("errors", []) + errors))
    result["limitations"] = result.get("limitations", []) or []
    if warnings:
        result["degradations"] = list(dict.fromkeys(result.get("degradations", []) + warnings))

    write_json(output_dir / "result.json", result)
    render_report(result, output_dir / "report.md")

    # 更新账本状态
    state = load_json(work_dir / "state.json", default=init_state(result["case_id"]))
    if isinstance(state, dict):
        state["facets"] = result["progress"]
        state["check_classes"] = result["checked_classes"]
        if meta.get("phases"):
            state["phases"] = meta["phases"]
        state["budget"]["seconds_used"] = int(time.time()) if False else state["budget"].get("seconds_used", 0)
        write_json(work_dir / "state.json", state)

    print(json.dumps({
        "case_id": result["case_id"],
        "status": result["status"],
        "findings": counts["total"],
        "schema_errors": len(errors),
        "numeric_candidates": len(result["numeric_checks"]),
        "output": str(output_dir / "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args):
    work_dir = Path(args.work)
    state = load_json(work_dir / "state.json", default=None)
    if not isinstance(state, dict):
        print(json.dumps({"error": "no ledger yet; run scaffold first"}, ensure_ascii=False))
        return 0
    budget = state.get("budget", {})
    remaining_s = budget.get("seconds_total", BUDGET_SECONDS) - budget.get("seconds_used", 0)
    remaining_r = budget.get("requests_total", BUDGET_REQUESTS) - budget.get("requests_used", 0)
    print(json.dumps({
        "case_id": state.get("case_id"),
        "remaining_seconds": remaining_s,
        "remaining_requests": remaining_r,
        "phases": state.get("phases", []),
        "facets": state.get("facets", {}),
    }, ensure_ascii=False, indent=2))
    return 0


def render_report(result, path: Path):
    lines = []
    lines.append("# 研究方法与统计审查报告")
    lines.append("")
    lines.append(f"- case_id: `{result.get('case_id','')}`")
    lines.append(f"- status: `{result.get('status','')}`")
    lines.append(f"- result_status: `{result.get('result_status','')}`")
    lines.append(f"- verdict: `{result.get('summary',{}).get('verdict','')}`")
    lines.append("")
    counts = result.get("summary", {}).get("counts", {})
    lines.append(f"共 {counts.get('total',0)} 条问题（critical {counts.get('critical',0)} / "
                 f"major {counts.get('major',0)} / minor {counts.get('minor',0)} / "
                 f"advisory {counts.get('advisory',0)} / pending {counts.get('pending',0)}）。")
    lines.append("")
    lines.append("## 检查面进度")
    for f, v in result.get("progress", {}).items():
        lines.append(f"- `{f}`: {v}")
    lines.append("")
    lines.append("## 问题清单")
    lines.append("")
    for i, f in enumerate(result.get("review_report", {}).get("findings", []), 1):
        lines.append(f"### {i}. {f.get('id','')}")
        lines.append(f"- 检查面: `{f.get('facet','')}` | 类型: `{f.get('finding_type','')}` | "
                     f"严重度: `{f.get('severity','')}`")
        lines.append(f"- 定位: `{f.get('locator','')}`")
        lines.append(f"- 原文摘录: > {f.get('quote','')}")
        lines.append(f"- 判断依据: {f.get('basis','')}")
        lines.append(f"- 修改建议: {f.get('suggestion','')}")
        lines.append("")
    if not result.get("review_report", {}).get("findings"):
        lines.append("（尚无 finding，或已按 partial 骨架落盘。）")
        lines.append("")
    lines.append("## 数值一致性候选（供复核）")
    lines.append("")
    for c in result.get("numeric_checks", []):
        lines.append(f"- `{c.get('id','')}` [{c.get('rule','')}] `{c.get('locator','')}`: "
                     f"{c.get('detail','')}")
    if not result.get("numeric_checks"):
        lines.append("（无候选。）")
        lines.append("")
    lines.append("## 局限与不确定性")
    lines.append("")
    for x in result.get("limitations", []):
        lines.append(f"- {x}")
    for x in result.get("uncertainty", []):
        lines.append(f"- {x}")
    if not result.get("limitations") and not result.get("uncertainty"):
        lines.append("（未声明。）")
    lines.append("")
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="研究方法与统计审查 Skill：discover/scaffold/finalize/status",
    )
    parser.add_argument("command", choices=["discover", "scaffold", "finalize", "status", "help"],
                        nargs="?", default="help")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "help":
        parser.print_help()
        return 0
    handlers = {
        "discover": cmd_discover,
        "scaffold": cmd_scaffold,
        "finalize": cmd_finalize,
        "status": cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
