#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""方法与实验章节写作 Skill 运行时（纯 Python 标准库）。

子命令：
  skeleton   0–20 秒落盘 result.json 骨架 / state.json / plan.json（必须先跑，前 3 分钟内）
  inventory  扫描 /input，分类材料并抽取带 locator+quote 的候选事实，
             产出 materials_inventory.json / fact_model.json
  validate   校验 result.json 的契约字段、状态、证据与产物路径，
             产出 validation_report.json

路径缺省：--input /input  --output /app/output  --work /app/work
所有脚本确定性执行、只依赖标准库、不联网、不改写输入。
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB
STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
SCHEMA_VERSION = "2.0.0"

# 文本/可尝试抽取的文件后缀（小写）
TEXT_EXT = {
    ".md", ".txt", ".rst", ".tex", ".bib", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".py", ".sh", ".bash", ".ipynb", ".csv", ".tsv",
    ".log", ".out", ".err", ".xml", ".html", ".jsonl", ".c", ".h", ".cpp",
    ".java", ".R", ".r", ".sql",
}
OFFICE_EXT = {".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls", ".odt", ".ods", ".odp"}
PDF_EXT = {".pdf"}

# 六类要素（结果完整性评分点）
SIX_ELEMENTS = [
    ("method_steps", "方法步骤", ["input", "operation", "output", "criterion"]),
    ("experiment_setup", "实验设置", ["train", "validation", "test", "hardware", "software", "seed", "repetition"]),
    ("parameters_sources", "参数与来源", ["name", "value", "unit", "applies_to", "source"]),
    ("dataset_split", "数据集与划分", ["name", "version", "size", "preprocess", "split_ratio", "randomization", "subset_use"]),
    ("baseline_comparison", "对照实验", ["baseline", "version", "budget_match", "metric", "statistical_method"]),
    ("reproducibility_limits", "可复现与限制", ["code_version", "environment", "commands", "data_access", "failure_scenario", "scope"]),
]


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """utf-8-sig 优先，容忍 BOM/坏编码；超限返回 None。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > max_bytes:
        return None
    data = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                data = fh.read()
            break
        except (UnicodeDecodeError, ValueError, OSError):
            continue
    if data is None:
        try:
            with open(path, "r", encoding="latin-1", errors="replace") as fh:
                data = fh.read()
        except OSError:
            return None
    return data


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return default


def write_json(path, obj):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


def write_text(path, text):
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def rel_of(abspath, root):
    try:
        return os.path.relpath(abspath, root)
    except ValueError:
        return os.path.basename(abspath)


# --------------------------------------------------------------------------- #
# skeleton
# --------------------------------------------------------------------------- #
def placeholder_for(spec_value):
    """按 case.json 的 output_contract 值猜测占位类型。"""
    if isinstance(spec_value, dict):
        return {k: placeholder_for(v) for k, v in spec_value.items()}
    if isinstance(spec_value, list):
        return []
    if spec_value is None:
        return None
    if isinstance(spec_value, bool):
        return None
    if isinstance(spec_value, (int, float)):
        return None
    s = str(spec_value)
    if s.lower() in ("string", "str", "text"):
        return None
    if "list" in s.lower() or "array" in s.lower():
        return []
    if "object" in s.lower() or "dict" in s.lower() or "map" in s.lower():
        return {}
    return None


def extract_case_id(case):
    for key in ("case_id", "id", "challenge_id", "caseId", "challengeId"):
        v = case.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def extract_output_contract(case):
    oc = case.get("output_contract")
    if isinstance(oc, dict) and oc:
        return oc
    # 某些包把契约放在 output 或 contract 下
    for key in ("output", "contract", "result_schema", "fields"):
        v = case.get(key)
        if isinstance(v, dict) and v:
            return v
    return {}


def cmd_skeleton(args):
    started = time.time()
    ensure_dir(args.output)
    ensure_dir(args.work)
    case = load_json(os.path.join(args.input, "case.json"), default={})
    if not isinstance(case, dict):
        case = {}
    task_text = read_text(os.path.join(args.input, "task.md")) or ""

    case_id = extract_case_id(case)
    oc = extract_output_contract(case)

    # 顶层回填 output_contract 全部原始字段（键名、数量一致，未知值占位）
    top = {}
    for k, v in oc.items():
        top[k] = placeholder_for(v)
    top["case_id"] = case_id
    top["status"] = "partial"
    top["artifacts"] = []
    top["sources"] = []
    top["uncertainty"] = {}
    top["change_log"] = []
    top["errors"] = []
    top["degradations"] = []
    top["limitations"] = []
    top["pending_human"] = []
    top["runtime"] = {
        "started_at": utcnow(),
        "phase": "skeleton",
        "elapsed_s": 0.0,
        "budget_s": 900,
        "model_requests_used": 0,
        "model_requests_budget": 64,
    }
    top["_schema_version"] = SCHEMA_VERSION
    top["_output_contract_keys"] = list(oc.keys())

    result_path = write_json(os.path.join(args.output, "result.json"), top)

    state = {
        "case_id": case_id,
        "input_root": args.input,
        "output_root": args.output,
        "work_root": args.work,
        "output_contract_keys": list(oc.keys()),
        "output_contract_spec": oc,
        "task_first_3000": task_text[:3000],
        "skeleton_written_at": utcnow(),
        "elapsed_s": round(time.time() - started, 3),
        "phase": "skeleton",
        "has_task": bool(task_text.strip()),
        "has_case": bool(case),
    }
    write_json(os.path.join(args.work, "state.json"), state)

    # 中间表示 #1：plan.json（要素计划 + 图模型 + 预算追踪）
    plan = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow(),
        "case_id": case_id,
        "six_elements": [
            {
                "key": k,
                "label": label,
                "required_fields": fields,
                "section": None,
                "status": "planned",  # planned -> drafted -> verified -> gap
                "evidence_ids": [],
                "gaps": [],
            }
            for k, label, fields in SIX_ELEMENTS
        ],
        "figure_model": {
            "figures": [],
            "tables": [],
            "notes": ["仅在任务要求图表/参数表时填充；数据来源必须指向 locator"],
        },
        "budget": {
            "total_s": 900,
            "model_requests": 64,
            "elapsed_s": 0.0,
            "model_requests_used": 0,
            "phase": "skeleton",
            "next_deadline": "前 3 分钟（180s）内必须完成 result.json 骨架落盘",
        },
    }
    write_json(os.path.join(args.work, "plan.json"), plan)

    # 首版 manuscript.md 骨架（诚实占位，不含编造内容）
    skeleton_md = (
        "# 方法与实验章节（初稿骨架）\n\n"
        f"- case_id: `{case_id}`\n"
        "- 状态：partial（骨架，尚未回填事实）\n"
        "- 说明：本文件由 skeleton 阶段生成，后续阶段增量填入六类要素；"
        "所有事实句必须挂 [S#] 并写入 evidence_map.json。\n"
    )
    write_text(os.path.join(args.output, "manuscript.md"), skeleton_md)

    # 空证据映射（保证文件始终存在且结构合法）
    write_json(os.path.join(args.output, "evidence_map.json"), {})
    write_json(os.path.join(args.output, "preserved_facts.json"), {"facts": []})

    # 中间表示 #2：fact_model.json（事实模型骨架）
    fact_model = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow(),
        "facts": [],
        "conflicts": [],
        "gaps": [],
    }
    write_json(os.path.join(args.work, "fact_model.json"), fact_model)

    print(json.dumps({
        "ok": True,
        "phase": "skeleton",
        "case_id": case_id,
        "output_contract_keys": list(oc.keys()),
        "result": result_path,
    }, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# inventory
# --------------------------------------------------------------------------- #
def iter_files(root):
    out = []
    for base, dirs, files in os.walk(root):
        # 跳过中间/输出目录，避免递归到自身产物
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "work", "output")]
        for f in files:
            full = os.path.join(base, f)
            out.append(full)
    return sorted(out)


def classify(name):
    low = name.lower()
    ext = os.path.splitext(low)[1]
    if ext in PDF_EXT:
        return "pdf"
    if ext in OFFICE_EXT:
        return "office"
    if ext in TEXT_EXT:
        return "text"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
        return "image"
    if ext in (".pt", ".pth", ".ckpt", ".bin", ".npy", ".npz", ".h5", ".pkl", ".zip", ".tar", ".gz"):
        return "binary"
    return "other"


_NUM = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_UNIT = r"(?:%|ms|s|min|h|hr|GB|GiB|MB|MiB|KB|KiB|TB|GHz|MHz|epochs?|steps?|batch|batches|layers?|dim|heads?|seed|folds?|repeats?|iterations?|mm|cm|nm|K|C)"

PATTERNS = {
    "metric": (re.compile(r"(" + _NUM + r")\s*(" + _UNIT + r")(?![A-Za-z])", re.I), "数值+单位"),
    "seed": (re.compile(r"(?:random[_ -]?seed|seed(?:_value)?)\s*[=:]\s*(\d+)", re.I), "随机种子"),
    "learning_rate": (re.compile(r"(?:learning[_ -]?rate|lr)\s*[=:]\s*([0-9eE.+-]+)", re.I), "学习率"),
    "batch_size": (re.compile(r"(?:batch[_ -]?size|batch)\s*[=:]\s*(\d+)", re.I), "批大小"),
    "epoch": (re.compile(r"(?:n?[_ -]?epochs?|num[_ -]?epochs?|max[_ -]?epochs?)\s*[=:]\s*(\d+)", re.I), "训练轮数"),
    "version": (re.compile(r"(?:version|ver|release)\s*[=:]\s*v?(\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?)", re.I), "版本号"),
    "split_ratio": (re.compile(r"(\d{1,3})\s*[:/]\s*(\d{1,3})\s*[:/]\s*(\d{1,3})"), "三路划分比例"),
    "path": (re.compile(r"(?:[A-Za-z]:[\\/])?[\w./-]+\.(?:yaml|yml|json|csv|py|sh|cfg|ini|toml|pt|pth|ckpt|npy|npz|txt|tsv|xlsx|docx|pdf)", re.I), "文件路径"),
    "command": (re.compile(r"^\s*(?:python3?|bash|sh|pip3?|conda|git|docker|make|./)[^\n]{0,200}", re.M), "命令"),
}


def candidates_from_text(text, relpath, root):
    """从单个文本抽取候选事实，每项带 locator + 逐字 quote。"""
    facts = []
    if text is None:
        return facts
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        ln = idx + 1
        strip = line.strip()
        if not strip:
            continue
        locator = f"{relpath}#L{ln}"
        for ptype, (pat, label) in PATTERNS.items():
            for m in pat.finditer(line):
                quote = strip
                groups = m.groups()
                if ptype == "metric" and len(groups) >= 2:
                    value, unit = groups[0], groups[1]
                    facts.append({
                        "type": "metric", "name": label, "value": value,
                        "unit": unit, "applies_to": None,
                        "locator": locator, "quote": quote,
                        "confidence": "verbatim", "status": "confirmed",
                    })
                elif ptype == "split_ratio":
                    facts.append({
                        "type": "split", "name": "划分比例",
                        "value": ":".join(groups), "unit": None, "applies_to": None,
                        "locator": locator, "quote": quote,
                        "confidence": "verbatim", "status": "confirmed",
                    })
                elif ptype == "path":
                    pval = m.group(0).strip()
                    if pval:
                        facts.append({
                            "type": "path", "name": "路径", "value": pval,
                            "unit": None, "applies_to": None,
                            "locator": locator, "quote": quote,
                            "confidence": "verbatim", "status": "confirmed",
                        })
                elif ptype == "command":
                    cval = m.group(0).strip()
                    if cval:
                        facts.append({
                            "type": "command", "name": "命令", "value": cval,
                            "unit": None, "applies_to": None,
                            "locator": locator, "quote": quote,
                            "confidence": "verbatim", "status": "confirmed",
                        })
                else:
                    facts.append({
                        "type": ptype, "name": label, "value": m.group(1),
                        "unit": None, "applies_to": None,
                        "locator": locator, "quote": quote,
                        "confidence": "verbatim", "status": "confirmed",
                    })
    return facts


def extract_pdf_text(path, degradations):
    """尽力用 pdftotext 或题面 pdf-text-tool 抽取；失败记录降级。"""
    candidates = ["pdftotext", "pdf-text-tool"]
    for tool in candidates:
        try:
            proc = subprocess.run(
                [tool, path, "-"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=120,
            )
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout.decode("utf-8", errors="replace")
            if proc.returncode == 0 and proc.stderr and proc.stderr.strip():
                # pdftotext 可能把文本写 stderr 的边界情况
                return proc.stderr.decode("utf-8", errors="replace")
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
    degradations.append({
        "file": path,
        "issue": "PDF 未抽取文本：无可用 pdftotext / pdf-text-tool",
        "suggestion": "改由 Agent 读取原始 PDF 或使用题面提供的 pdf-text-tool / ocr-tool",
    })
    return None


def cmd_inventory(args):
    ensure_dir(args.work)
    ensure_dir(args.output)
    state = load_json(os.path.join(args.work, "state.json"), default={}) or {}

    items = []
    degradations = []
    facts = []
    for full in iter_files(args.input):
        rel = rel_of(full, args.input)
        low = rel.lower()
        if any(part in ("work", "output") for part in rel.split(os.sep)):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        kind = classify(rel)
        item = {
            "relpath": rel,
            "abs": full,
            "kind": kind,
            "bytes": size,
            "skipped": False,
            "reason": None,
            "text_extracted": False,
        }
        text = None
        if kind == "text":
            text = read_text(full)
            if text is None:
                item["skipped"] = True
                item["reason"] = "size_limit" if size > MAX_FILE_BYTES else "undecodable"
            else:
                item["text_extracted"] = True
        elif kind == "office":
            text = None
            degradations.append({
                "file": rel,
                "issue": "Office 文档未在 inventory 阶段解析",
                "suggestion": "由 Agent 直接打开或用题面 office-renderer 转 PDF 后读取",
            })
        elif kind == "pdf":
            text = extract_pdf_text(full, degradations)
            if text is not None:
                item["text_extracted"] = True
        if text:
            facts.extend(candidates_from_text(text, rel, args.input))
        items.append(item)

    # 去重（同一 locator+quote+type+value 只留一条）
    seen = set()
    uniq = []
    for f in facts:
        key = (f["locator"], f["type"], f.get("value"), f["quote"][:80])
        if key in seen:
            continue
        seen.add(key)
        f["fact_id"] = f"F{len(uniq)+1:03d}"
        uniq.append(f)

    inventory = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow(),
        "input_root": args.input,
        "items": items,
        "counts": {},
        "degradations": degradations,
    }
    for it in items:
        inventory["counts"][it["kind"]] = inventory["counts"].get(it["kind"], 0) + 1
    write_json(os.path.join(args.work, "materials_inventory.json"), inventory)

    fact_model = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow(),
        "facts": uniq,
        "conflicts": [],
        "gaps": [],
        "notes": "候选事实仅作定位与提示；Agent 必须逐条回到原文核实后才会进入成稿。",
    }
    write_json(os.path.join(args.work, "fact_model.json"), fact_model)

    # 更新 state
    state.update({
        "phase": "inventory",
        "material_count": len(items),
        "candidate_fact_count": len(uniq),
        "degradations": degradations,
    })
    write_json(os.path.join(args.work, "state.json"), state)

    print(json.dumps({
        "ok": True,
        "phase": "inventory",
        "materials": len(items),
        "candidate_facts": len(uniq),
        "degradations": len(degradations),
    }, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #
def cmd_validate(args):
    ensure_dir(args.work)
    result = load_json(os.path.join(args.output, "result.json"))
    state = load_json(os.path.join(args.work, "state.json"), default={}) or {}
    report = {"checks": [], "passed": 0, "failed": 0}

    def check(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        if ok:
            report["passed"] += 1
        else:
            report["failed"] += 1

    check("result.json 存在且合法", isinstance(result, dict), "missing or invalid")
    if not isinstance(result, dict):
        result = {}

    case_id = result.get("case_id")
    check("case_id 非空", isinstance(case_id, str) and bool(case_id.strip()), str(case_id))

    status = result.get("status")
    check("status 合法", status in STATUSES, str(status))

    keys = state.get("output_contract_keys", [])
    missing_keys = [k for k in keys if k not in result]
    check("output_contract 全部字段回填", not missing_keys, ",".join(missing_keys))

    for fld in ("sources", "uncertainty", "change_log", "errors", "degradations", "limitations", "pending_human"):
        check(f"标准字段 {fld} 存在", fld in result, "")

    artifacts = result.get("artifacts")
    check("artifacts 为列表", isinstance(artifacts, list), str(type(artifacts)))
    bad_paths = []
    for a in (artifacts or []):
        if isinstance(a, str):
            p = a
            if not os.path.isabs(p):
                p = os.path.join(args.output, p)
            if not os.path.exists(p):
                bad_paths.append(a)
    check("artifacts 路径均存在", not bad_paths, ",".join(bad_paths))

    # 主产物
    manuscript = os.path.join(args.output, "manuscript.md")
    check("manuscript.md 存在", os.path.exists(manuscript))

    em = load_json(os.path.join(args.output, "evidence_map.json"))
    check("evidence_map.json 存在", isinstance(em, dict))
    if isinstance(em, dict):
        bad_ev = [k for k, v in em.items() if not (isinstance(v, dict) and v.get("locator") and v.get("quote"))]
        check("evidence_map 每项 locator+quote 非空", not bad_ev, ",".join(bad_ev[:5]))

    pf = load_json(os.path.join(args.output, "preserved_facts.json"))
    check("preserved_facts.json 存在", isinstance(pf, dict))

    # status 与成稿一致性（诚实性）
    if status in ("ok", "partial") and not os.path.exists(manuscript):
        check("ok/partial 状态需有成稿", False, "manuscript.md 缺失")

    report["generated_at"] = utcnow()
    report["case_id"] = case_id
    report["status"] = status
    write_json(os.path.join(args.work, "validation_report.json"), report)

    # 摘要输出
    print(json.dumps({
        "ok": report["failed"] == 0,
        "passed": report["passed"],
        "failed": report["failed"],
        "case_id": case_id,
        "status": status,
    }, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description="方法与实验章节写作 Skill 运行时")
    p.add_argument("subcommand", choices=["skeleton", "inventory", "validate"],
                   help="执行阶段")
    p.add_argument("--input", default=DEFAULTS["input"])
    p.add_argument("--output", default=DEFAULTS["output"])
    p.add_argument("--work", default=DEFAULTS["work"])
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    fn = {"skeleton": cmd_skeleton, "inventory": cmd_inventory, "validate": cmd_validate}[args.subcommand]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
