#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研成果转化写作 Skill —— 编排脚本（仅标准库）。

三种模式（默认参数均指向 Harness 运行时路径）：
  prepare   扫描输入、抽取 contract、生成材料索引与证据脚手架，并立即落盘
            /app/output/result.json 的 partial 骨架（第 0 步，须在前 3 分钟完成）。
  finalize  合并执行 Agent 已填写的证据/改动清单与稿件产物，诚实设定 status。
  check     对 result.json 做结构自检，逐条打印通过/失败。

用法示例：
  python3 -B scripts/main.py prepare  --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py check    --input /input --output /app/output --work /app/work
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import evidence  # noqa: E402

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

TEXT_EXT = {".txt", ".md", ".json", ".csv", ".tex", ".xml", ".yaml", ".yml", ".bib", ".ris"}
BINARY_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
IGNORE_NAMES = {"task.md", "case.json"}

# 本 Skill 标准字段（评审必需）；若契约同名字段冲突，契约原值移入 contract_<name>。
RESERVED = {"status", "sources", "uncertainty", "change_log", "artifacts",
            "pending_human", "errors", "degradations", "runtime", "case_id"}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def extract_contract(case):
    """从 case.json 提取 (case_id, {field_name: default})。

    output_contract 可能是 dict（含 fields/properties）或字段名 list。
    缺失时返回空 dict，由执行 Agent 按 task.md 补足。
    """
    if not isinstance(case, dict):
        return "unknown", {}

    case_id = None
    for k in ("case_id", "caseId", "id"):
        v = case.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            case_id = str(v)
            break
    if case_id is None:
        case_id = "unknown"

    contract = None
    for k in ("output_contract", "outputContract", "output", "outputs",
              "required_fields", "requiredFields", "result_schema"):
        if k in case:
            contract = case[k]
            break

    fields = {}
    if isinstance(contract, dict):
        src = contract.get("fields", contract.get("properties", contract))
        if isinstance(src, dict):
            for name, spec in src.items():
                if isinstance(name, str):
                    fields[name] = _default_for(spec)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                fields[item] = None
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                fields[item["name"]] = _default_for(item)
    return case_id, fields


def _default_for(spec):
    """按契约类型给未产出字段一个诚实默认值：标量 null，数组 []，对象 {}。"""
    if isinstance(spec, dict):
        if "default" in spec:
            return spec["default"]
        t = str(spec.get("type", "")).lower()
        if t in ("array", "list"):
            return []
        if t in ("object", "dict", "map"):
            return {}
        if t in ("boolean", "bool"):
            return False
        return None
    return None


def scan_materials(input_dir, work_dir):
    """扫描输入目录材料，返回 (sources, degradations)。

    文本直接记录；PDF 尝试 pdftotext 提取为 work/materials/<name>.txt。
    """
    sources, degradations = [], []
    inp = os.path.abspath(input_dir)
    if not os.path.isdir(inp):
        return sources, ["input dir missing: " + inp]

    work_mat = os.path.join(work_dir, "materials")
    os.makedirs(work_mat, exist_ok=True)

    for root, _dirs, files in os.walk(inp):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, inp).replace(os.sep, "/")
            ext = os.path.splitext(fn)[1].lower()
            if fn in IGNORE_NAMES:
                continue
            if ext in TEXT_EXT:
                sources.append({"path": rel, "kind": ext.lstrip("."), "extracted": True})
            elif ext in BINARY_EXT:
                if ext == ".pdf":
                    txt, errs = extract_pdf(fp, work_mat)
                    if txt:
                        sources.append({"path": rel, "kind": "pdf", "extracted": True,
                                        "extracted_text": txt})
                    else:
                        sources.append({"path": rel, "kind": "pdf", "extracted": False})
                        degradations.extend(errs)
                else:
                    sources.append({"path": rel, "kind": ext.lstrip("."), "extracted": False})
                    degradations.append("binary material not auto-extracted (use platform tool): " + rel)
            # 其余扩展名忽略，仅记录
    return sources, degradations


def extract_pdf(pdf_path, work_mat):
    """用 pdftotext 提取 PDF；缺失/失败返回 ("", [errors])。"""
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_txt = os.path.join(work_mat, base + ".txt")
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", pdf_path, out_txt],
            capture_output=True, timeout=60)
        if proc.returncode == 0 and os.path.isfile(out_txt) and os.path.getsize(out_txt) > 0:
            return os.path.basename(out_txt), []
        return "", ["pdftotext failed (rc={0}) for {1}".format(proc.returncode, pdf_path)]
    except FileNotFoundError:
        return "", ["pdftotext not found; use platform pdf-text-tool for " + pdf_path]
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "", ["pdftotext error for {0}: {1}".format(pdf_path, exc)]


def build_skeleton(case_id, fields, sources, errors, degradations, started):
    """构造 result.json partial 骨架。"""
    result = {"case_id": case_id}
    for name, default in fields.items():
        if name in RESERVED:
            result["contract_" + name] = default
        else:
            result[name] = default
    result.update({
        "status": "partial",
        "artifacts": {},
        "contract_fields": list(fields.keys()),
        "sources": sources,
        "uncertainty": [],
        "change_log": [],
        "pending_human": [],
        "errors": list(errors),
        "degradations": list(degradations),
        "runtime": {
            "started_at": started,
            "ended_at": None,
            "seconds": None,
            "python": sys.version.split()[0],
            "scripts": ["scripts/main.py", "scripts/evidence.py"],
        },
    })
    return result


def write_json(path, obj):
    """写 JSON（先写临时文件再替换，避免中途异常留下半截产物）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def cmd_prepare(args):
    started = now_iso()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    case_path = os.path.join(args.input, "case.json")
    case, case_errs = evidence.load_json_any(case_path)
    case_id, fields = extract_contract(case)

    sources, degradations = scan_materials(args.input, args.work)
    errors = list(case_errs)

    # 材料索引
    index = {
        "case_id": case_id,
        "input_dir": args.input,
        "generated_at": started,
        "output_contract_fields": list(fields.keys()),
        "materials": sources,
        "errors": errors,
        "degradations": degradations,
    }
    write_json(os.path.join(args.work, "materials_index.json"), index)

    # 证据脚手架
    scaffold = {
        "case_id": case_id,
        "facts": [],           # 每项: {locator, quote, number/claim, unit, source}
        "terms": {},           # 术语锁定: {canonical: {abbr, first_expansion, variants}}
        "uncertainty": [],
        "change_log": [],
        "pending_human": [],
        "contract_values": {},  # 可选：执行 Agent 显式回填 output_contract 各字段
    }
    write_json(os.path.join(args.work, "evidence_map.json"), scaffold)

    # 骨架 result.json（第 0 步落盘）
    skeleton = build_skeleton(case_id, fields, sources, errors, degradations, started)
    write_json(os.path.join(args.output, "result.json"), skeleton)

    print(json.dumps({
        "mode": "prepare",
        "case_id": case_id,
        "contract_fields": list(fields.keys()),
        "materials": len(sources),
        "status": "partial",
        "result": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def _load_optional(path):
    if os.path.isfile(path):
        obj, errs = evidence.load_json_any(path)
        return obj, errs
    return None, []


def _parse_iso(s):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


def _autofill_contract(result, fields, artifacts, ev):
    """把已产出的真实数据按关键词回填到仍为空缺的契约字段。

    只在字段为 null/空串/空容器时回填；只拷贝实际存在的产物，绝不造值。
    执行 Agent 也可先在 evidence_map.contract_values 里显式赋值（优先）。
    """
    explicit = (ev or {}).get("contract_values") or {}
    for name in fields:
        if name in explicit and explicit[name] is not None:
            result[name] = explicit[name]

    def fill(name, value):
        if result.get(name) in (None, "", [], {}):
            result[name] = value

    manuscript = artifacts.get("manuscript")
    facts = (ev or {}).get("facts") or []
    terms = (ev or {}).get("terms") or {}
    change_log = result.get("change_log") or []
    uncertainty = result.get("uncertainty") or []
    pending = result.get("pending_human") or []

    for name in fields:
        n = str(name).lower()
        if manuscript and any(k in n for k in ("manuscript", "draft", "article", "report",
                                               "output", "稿", "正文", "改写稿")):
            fill(name, manuscript)
        elif facts and any(k in n for k in ("evidence", "fact", "source", "证据", "事实", "来源")):
            fill(name, facts)
        elif terms and any(k in n for k in ("term", "glossary", "术语")):
            fill(name, terms)
        elif change_log and any(k in n for k in ("change", "diff", "log", "改动", "修改", "对照")):
            fill(name, change_log)
        elif uncertainty and any(k in n for k in ("uncertain", "uncertainty", "不确定", "风险")):
            fill(name, uncertainty)
        elif pending and any(k in n for k in ("pending", "human", "待", "人工", "确认")):
            fill(name, pending)


def cmd_finalize(args):
    result_path = os.path.join(args.output, "result.json")
    result, errs = _load_optional(result_path)
    if not isinstance(result, dict) or not result:
        # 骨架缺失时重建最小合法产物
        case_path = os.path.join(args.input, "case.json")
        case, case_errs = evidence.load_json_any(case_path)
        case_id, fields = extract_contract(case)
        result = build_skeleton(case_id, fields, [], list(case_errs) + errs, [], now_iso())

    # 契约字段名：优先从骨架记录读回，缺失则重新抽取
    fields = {}
    contract_fields = result.get("contract_fields")
    if isinstance(contract_fields, list):
        fields = {str(k): result.get(k) for k in contract_fields}
    else:
        case_path = os.path.join(args.input, "case.json")
        case, _ = evidence.load_json_any(case_path)
        _, fields = extract_contract(case)

    started = result.get("runtime", {}).get("started_at") or now_iso()
    ended = now_iso()

    # 合并证据脚手架
    ev, ev_errs = _load_optional(os.path.join(args.work, "evidence_map.json"))
    if isinstance(ev, dict):
        for key in ("facts", "terms", "uncertainty", "change_log", "pending_human"):
            if key in ev:
                result[key] = ev[key]

    # 识别 output 下的稿件产物（排除 result.json）
    artifacts = {}
    if os.path.isdir(args.output):
        for fn in sorted(os.listdir(args.output)):
            if fn == "result.json" or fn.endswith(".tmp"):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in (".md", ".txt", ".json", ".csv", ".docx", ".pptx", ".xlsx", ".pdf", ".png", ".jpg"):
                artifacts.setdefault("manuscript", fn)
                if ext in (".json",):
                    artifacts.setdefault("structured", fn)
    result["artifacts"] = artifacts

    # 回填契约字段
    _autofill_contract(result, fields, artifacts, ev)

    # 诚实定 status
    manuscript = artifacts.get("manuscript")
    n_sources = len(result.get("sources") or [])
    if manuscript:
        status = "ok"
    elif n_sources == 0:
        status = "insufficient_evidence"
    elif result.get("errors") or result.get("degradations"):
        status = "partial"
    else:
        status = "partial"
    result["status"] = status

    result["errors"] = list(result.get("errors") or []) + list(ev_errs) + list(errs)
    result["runtime"]["ended_at"] = ended
    t0 = _parse_iso(started)
    t1 = _parse_iso(ended)
    if t0 and t1:
        result["runtime"]["seconds"] = max(0, round((t1 - t0).total_seconds()))
    else:
        result["runtime"]["seconds"] = None

    write_json(result_path, result)
    print(json.dumps({"mode": "finalize", "case_id": result.get("case_id"),
                      "status": status, "artifacts": artifacts,
                      "result": result_path}, ensure_ascii=False, indent=2))
    return 0


def cmd_check(args):
    result_path = os.path.join(args.output, "result.json")
    result, errs = _load_optional(result_path)
    ok = True
    checks = []
    def _chk(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        checks.append({"check": name, "pass": bool(cond), "detail": detail})

    is_dict = isinstance(result, dict)
    _chk("result.json exists and is valid JSON", is_dict, "; ".join(errs) if errs else "")
    if not is_dict:
        print(json.dumps({"mode": "check", "all_pass": False, "checks": checks},
                         ensure_ascii=False, indent=2))
        return 1

    _chk("case_id present", bool(result.get("case_id")))
    _chk("status is one of ok/partial/insufficient_evidence/input_error",
         result.get("status") in {"ok", "partial", "insufficient_evidence", "input_error"})
    for key in ("sources", "uncertainty", "change_log"):
        _chk("key '%s' present" % key, key in result)
    _chk("sources is list", isinstance(result.get("sources"), list))
    _chk("change_log is list", isinstance(result.get("change_log"), list))
    _chk("uncertainty is list", isinstance(result.get("uncertainty"), list))
    _chk("artifacts present", "artifacts" in result)
    _chk("runtime present", isinstance(result.get("runtime"), dict))

    for key in ("sources", "uncertainty", "change_log", "pending_human", "errors", "degradations"):
        if key in result and result[key] is None:
            _chk("key '%s' is not null" % key, False, "should be a list")

    print(json.dumps({"mode": "check", "all_pass": ok, "status": result.get("status"),
                      "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="科研成果转化写作 Skill 编排脚本")
    parser.add_argument("mode", choices=["prepare", "finalize", "check"])
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args(argv)

    if args.mode == "prepare":
        return cmd_prepare(args)
    if args.mode == "finalize":
        return cmd_finalize(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
