#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""results-discussion-writing helper (standard library only).

Subcommands (run from SKILL.md directory, relative paths):
  init   normalize input -> input_summary.json + partial result.json skeleton
  check  verify result.json structure, forbidden terms, evidence, tone markers

Examples:
  python scripts/main.py init --case /input/case.json --task /input/task.md \
      --input-dir /input --work /app/work --out /app/output
  python scripts/main.py check --result /app/output/result.json \
      --summary /app/work/input_summary.json --out /app/output
"""
import argparse
import json
import os
import re
import sys
import time

DEFAULTS = {
    "case": "/input/case.json",
    "task": "/input/task.md",
    "input_dir": "/input",
    "work": "/app/work",
    "out": "/app/output",
}
DEFAULT_CONTRACT = ["deliverable", "facts_sources", "version_boundaries"]
TEXT_EXT = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".tex", ".html"}
MAX_TEXT_BYTES = 2_000_000

FORBIDDEN_TERMS = [
    "首次", "国际领先", "国内领先", "填补空白", "颠覆性", "革命性", "首创",
    "世界第一", "全球唯一", "前无古人", "里程碑式",
]
# 因果/结论性动词：出现在结果段时若无支撑则记为语气风险
CAUSAL_MARKERS = ["导致", "引起", "促进", "抑制", "机制", "因果", "证明", "证实",
                  "表明机制", "提示机制"]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def read_text(path, max_bytes=MAX_TEXT_BYTES):
    """Tolerant UTF-8 text read (BOM/bad encoding); return None if missing/oversize."""
    if not path or not os.path.exists(path):
        return None
    try:
        if os.path.getsize(path) > max_bytes:
            return None
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read()
        except (UnicodeDecodeError, ValueError, OSError):
            continue
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def load_json(path):
    """Parse JSON with BOM/bad-encoding tolerance; return {} on failure."""
    txt = read_text(path, max_bytes=MAX_TEXT_BYTES)
    if txt is None:
        return {}
    txt = txt.lstrip("\ufeff").strip()
    if not txt:
        return {}
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        # 尽力提取首个 {...} 子串
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except (ValueError, TypeError):
                return {}
        return {}


def _first_str(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def extract_case_id(case, fallback="unknown-case"):
    return _first_str(case, ("case_id", "caseId", "id", "challenge_id")) or fallback


def extract_contract(case):
    """Return ordered list of required field names from output_contract, else default."""
    oc = case.get("output_contract") if isinstance(case, dict) else None
    if isinstance(oc, dict):
        keys = [k for k in oc.keys()]
        return keys if keys else list(DEFAULT_CONTRACT)
    if isinstance(oc, list):
        names = []
        for item in oc:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                names.append(item.get("name") or item.get("field") or item.get("key") or "")
        names = [n for n in names if n]
        return names if names else list(DEFAULT_CONTRACT)
    return list(DEFAULT_CONTRACT)


def discover_materials(input_dir):
    """List non-hidden files; read preview for text kinds; mark PDFs for external parse."""
    mats = []
    if not input_dir or not os.path.isdir(input_dir):
        return mats
    for name in sorted(os.listdir(input_dir)):
        path = os.path.join(input_dir, name)
        if not os.path.isfile(path) or name.startswith("."):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        ext = os.path.splitext(name)[1].lower()
        kind = "text" if ext in TEXT_EXT else ("pdf" if ext == ".pdf" else "other")
        preview = None
        if kind == "text":
            preview = read_text(path)
            if preview is not None:
                preview = preview[:2000]
        mats.append({
            "name": name, "path": path, "ext": ext, "kind": kind,
            "size": size, "preview": preview,
            "note": "PDF: 用平台 pdf-text-tool 解析后再读" if kind == "pdf" else None,
        })
    return mats


def build_summary(case, task_text, materials, work_dir):
    summary = {
        "case_id": extract_case_id(case),
        "output_contract": extract_contract(case),
        "task_preview": (task_text or "")[:3000],
        "materials": materials,
        "instruction_hints": {},
        "generated_at": _now(),
    }
    if isinstance(case, dict):
        for k in ("audience", "style", "format", "genre", "constraints",
                  "output_language", "task", "instruction", "requirements"):
            if k in case and case[k] not in (None, "", []):
                summary["instruction_hints"][k] = case[k]
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)
        with open(os.path.join(work_dir, "input_summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary


def _placeholder(field_name):
    return {} if field_name == "version_boundaries" else ([] if field_name in (
        "facts_sources", "sources", "uncertainty", "pending_human",
        "change_log", "errors", "degradations") else None)


def build_skeleton(case_id, contract, out_dir, errors=None):
    result = {"case_id": case_id, "status": "partial"}
    for f in contract:
        result[f] = _placeholder(f)
    result["sources"] = []
    result["uncertainty"] = []
    result["pending_human"] = []
    result["change_log"] = [{"ts": _now(), "action": "init", "detail": "落盘 partial 骨架"}]
    result["errors"] = errors or []
    result["degradations"] = []
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "result.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return out_path


def cmd_init(args):
    errors = []
    case = load_json(args.case) if os.path.exists(args.case) else {}
    task_text = read_text(args.task) or ""
    if not case:
        errors.append("case.json 缺失或不可解析")
    if not task_text:
        errors.append("task.md 缺失或为空")
    materials = discover_materials(args.input_dir)
    if not materials:
        errors.append("输入目录无可用材料文件")
    case_id = extract_case_id(case)
    contract = extract_contract(case)
    summary = build_summary(case, task_text, materials, args.work)
    result_path = build_skeleton(case_id, contract, args.out, errors=errors)
    if errors:
        # 材料不足时诚实降级
        r = load_json(result_path)
        r["status"] = "partial"
        r["degradations"] = r.get("degradations", []) + \
            [{"ts": _now(), "detail": e} for e in errors]
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(r, fh, ensure_ascii=False, indent=2)
    print(json.dumps({
        "case_id": case_id, "contract": contract, "materials": len(materials),
        "result": result_path, "errors": errors,
    }, ensure_ascii=False))
    return 0


def _load_result(path):
    return load_json(path) if path and os.path.exists(path) else {}


def check_structure(result, summary):
    problems = []
    if not isinstance(result, dict):
        return ["result.json 不是对象"]
    if not result.get("case_id"):
        problems.append("缺 case_id")
    if "status" not in result:
        problems.append("缺 status")
    contract = (summary or {}).get("output_contract") or list(DEFAULT_CONTRACT)
    for f in contract:
        if f not in result:
            problems.append("缺 output_contract 字段: %s" % f)
    for f in ("sources", "uncertainty", "pending_human", "change_log"):
        if f not in result:
            problems.append("缺附加字段: %s" % f)
    return problems


def check_forbidden(result):
    hits = []
    blob = json.dumps(result, ensure_ascii=False)
    for term in FORBIDDEN_TERMS:
        for m in re.finditer(re.escape(term), blob):
            s = max(0, m.start() - 20)
            hits.append({"term": term, "context": blob[s:m.end() + 20]})
    return hits


def _manuscript_text(result, result_path=None):
    d = result.get("deliverable")
    if isinstance(d, dict):
        p = d.get("file") or d.get("path")
        if p and not os.path.isabs(p):
            base = os.path.dirname(result_path) if result_path else DEFAULTS["out"]
            p = os.path.join(base, os.path.basename(p))
        return read_text(p) or ""
    if isinstance(d, str) and d:
        return d
    return ""


def check_evidence(result):
    gaps = []
    fs = result.get("facts_sources")
    if isinstance(fs, list):
        for i, item in enumerate(fs):
            if not isinstance(item, dict):
                gaps.append({"index": i, "issue": "非对象"})
                continue
            if not (item.get("locator") or item.get("source") or item.get("位置")):
                gaps.append({"index": i, "issue": "缺 locator/source"})
            if not (item.get("quote") or item.get("text") or item.get("原文") or
                    item.get("note")):
                gaps.append({"index": i, "issue": "缺 quote（或 note 说明原因）"})
    return gaps


def check_tone(result, result_path=None):
    flags = []
    text = _manuscript_text(result, result_path)
    if not text:
        return flags
    # 结果段与讨论段粗切分：标题在奇数位，其下内容在紧随的偶数位
    seg = re.split(r"#{1,6}\s*(讨论|Discussion|结果|Results|局限|Limitations)", text)
    results_part = ""
    for i in range(1, len(seg) - 1, 2):
        if re.search(r"结果|Results", seg[i]):
            results_part += seg[i + 1] + " "
    if not results_part:
        results_part = text
    for term in CAUSAL_MARKERS:
        for m in re.finditer(re.escape(term), results_part):
            s = max(0, m.start() - 40)
            ctx = results_part[s:m.end() + 40].replace("\n", " ")
            # 已有统计/推测词则视为有边界，不报
            if re.search(r"p\s*[<=]|检验|可能|推测|提示|相关|回归|OR\s*=|HR\s*=", ctx):
                continue
            flags.append({"term": term, "context": ctx,
                          "advice": "结果段出现结论性动词，确认是否有数据/检验支撑，否则降级为描述或移入讨论"})
    return flags


def cmd_check(args):
    result = _load_result(args.result)
    summary = load_json(args.summary) if os.path.exists(args.summary) else {}
    problems = check_structure(result, summary)
    forbidden = check_forbidden(result)
    evidence_gaps = check_evidence(result)
    tone_flags = check_tone(result, args.result)
    report = {
        "ts": _now(),
        "structural_ok": not problems,
        "structural_errors": problems,
        "forbidden_terms": forbidden,
        "evidence_gaps": evidence_gaps,
        "tone_flags": tone_flags,
        "verdict": "pass" if not (problems or forbidden or evidence_gaps) else "fix",
    }
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "check_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="归一化输入并落 partial 骨架")
    p_init.add_argument("--case", default=DEFAULTS["case"])
    p_init.add_argument("--task", default=DEFAULTS["task"])
    p_init.add_argument("--input-dir", default=DEFAULTS["input_dir"])
    p_init.add_argument("--work", default=DEFAULTS["work"])
    p_init.add_argument("--out", default=DEFAULTS["out"])
    p_check = sub.add_parser("check", help="校验 result.json")
    p_check.add_argument("--result", default=os.path.join(DEFAULTS["out"], "result.json"))
    p_check.add_argument("--summary", default=os.path.join(DEFAULTS["work"], "input_summary.json"))
    p_check.add_argument("--out", default=DEFAULTS["out"])
    args = ap.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "check":
        return cmd_check(args)
    ap.error("未知子命令: %s" % args.cmd)


if __name__ == "__main__":
    sys.exit(main())
