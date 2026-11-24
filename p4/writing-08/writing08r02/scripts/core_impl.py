#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""methods-experiments-writing 核心实现（纯标准库，确定性执行）。

子命令：
  skeleton    读取 /input/case.json 与 task.md，立即写出合法的 partial result.json
              骨架与 state.json（前 3 分钟必须完成；空输入/坏 JSON 也留合法产物）。
  inventory   扫描 /input，分类材料并抽取带 locator + 逐字 quote 的候选事实，
              写 materials_inventory.json 与 extracted_facts.json。
  checkpoint  早落盘增量接口：追加 change_log、更新 runtime.phase、可更新 status。
  validate    校验 result.json 契约、状态、证据与产物路径，写 validation_report.json。

所有命令退出码均为 0（报告性失败不改退出码，由调用方读取报告），保证不因
数据问题崩溃；文本按 utf-8-sig 优先读取，容忍 BOM/坏编码/坏 JSON。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FACTS_TOTAL = 2000
MAX_FACTS_PER_LINE = 8
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}

# 文件分类
EXT_CATEGORIES = {
    ".pdf": "pdf",
    ".md": "text", ".txt": "text", ".rst": "text", ".tex": "text", ".org": "text",
    ".json": "config", ".yaml": "config", ".yml": "config", ".toml": "config",
    ".ini": "config", ".cfg": "config", ".properties": "config",
    ".csv": "table", ".tsv": "table",
    ".py": "code", ".sh": "code", ".ipynb": "code", ".r": "code", ".R": "code",
    ".log": "log", ".out": "log", ".err": "log",
    ".docx": "office", ".doc": "office", ".xlsx": "office", ".pptx": "office",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".svg": "image",
    ".tiff": "image", ".tif": "image", ".gif": "image", ".webp": "image",
}

# 候选事实抽取模式（正则）。全部只做候选，最终以模型逐条对照原文为准。
NUMBER_UNIT_RE = re.compile(
    r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*(?:%|ms|s|min|h|hrs|GB|MB|KB|TB|GiB|MiB|px|mm|cm|Hz|kHz|MHz|GHz|epochs?|steps?|layers?|dims?|K|M|B)\b",
    re.I,
)
VERSION_RE = re.compile(r"\b(?:v|V)?\d+\.\d+(?:\.\d+)?(?:[-+.][0-9A-Za-z]+)?\b")
SEED_RE = re.compile(r"(?:random[_-]?seed|seed)\s*[=:]\s*[-+]?\d+", re.I)
KV_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_.-]*)\s*[=:]\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|True|False|[A-Za-z0-9_./-]+)\b"
)
PATH_RE = re.compile(r"(?:/(?:[\w.+-]+/)*[\w.+-]+|(?:[A-Za-z]:)?(?:\\[\w. +-]+)+|\.{1,2}/[\w./+-]+)")
CMD_RE = re.compile(
    r"^\s*(?:python\d?(?:\.\d+)?|pip\d?|bash|sh|conda|docker|mamba|torchrun|accelerate|sbatch|make)\b.*"
)


def _now_utc():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _read_text(path):
    """utf-8-sig 优先，容忍 BOM/坏编码；始终返回 str 或空串。"""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _load_json(path):
    """返回 (obj, error_string)。error_string 为 None 表示成功。"""
    try:
        text = _read_text(path)
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001 任何解析异常都降级，不崩溃
        return None, "{}: {}".format(type(e).__name__, e)


def _ensure_dir(d):
    os.makedirs(d, exist_ok=True)


def _write_json(path, obj):
    _ensure_dir(os.path.dirname(path) if os.path.dirname(path) else ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def _resolve_dirs(args):
    inp = args.input or "/input"
    out = args.output or "/app/output"
    work = args.work or "/app/work"
    return inp, out, work


def _case_id(case):
    for k in ("case_id", "id", "challenge_id"):
        v = case.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return "unknown"


def _output_contract(case):
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        return oc
    # 兼容嵌套/别名
    for k in ("outputContract", "output_contracts"):
        v = case.get(k)
        if isinstance(v, dict):
            return v
    return {}


# --------------------------------------------------------------------------- #
# skeleton：先落盘 partial 骨架
# --------------------------------------------------------------------------- #
def cmd_skeleton(inp, out, work):
    errors = []
    case_path = os.path.join(inp, "case.json")
    case, e = _load_json(case_path)
    if e:
        errors.append("case.json: " + e)
    case = case if isinstance(case, dict) else {}
    task = ""
    task_path = os.path.join(inp, "task.md")
    if os.path.exists(task_path):
        try:
            task = _read_text(task_path)
        except Exception as ex:  # noqa: BLE001
            errors.append("task.md: " + repr(ex))

    cid = _case_id(case)
    oc = _output_contract(case)

    standard = {
        "case_id": cid,
        "status": "partial",
        "artifacts": [],
        "sources": [],
        "uncertainty": [],
        "change_log": [
            {
                "step": "skeleton",
                "at": _now_utc(),
                "status": "partial",
                "note": "第 0 步早落盘：先写合法 partial 骨架，再增量完善。",
            }
        ],
        "errors": errors,
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {
            "started_at": _now_utc(),
            "phase": "skeleton",
            "budget_seconds": 900,
            "budget_requests": 64,
        },
    }
    result = dict(standard)
    # 回填 output_contract 全部字段：不覆盖标准字段（如 status），未知值给 null
    for k in oc:
        if k not in result:
            result[k] = None
    result["case_id"] = cid  # 保证 case_id 以标准逻辑为准

    _ensure_dir(out)
    _ensure_dir(work)
    result_path = _write_json(os.path.join(out, "result.json"), result)
    state = {
        "case_id": cid,
        "output_contract_keys": sorted(oc.keys()),
        "phase": "skeleton",
        "started_at": _now_utc(),
        "input_dir": inp,
        "output_dir": out,
        "work_dir": work,
        "task_chars": len(task),
    }
    _write_json(os.path.join(work, "state.json"), state)
    print(
        json.dumps(
            {
                "case_id": cid,
                "output_contract_keys": sorted(oc.keys()),
                "status": "partial",
                "result": result_path,
            },
            ensure_ascii=False,
        )
    )


# --------------------------------------------------------------------------- #
# inventory：发现材料 + 候选事实抽取
# --------------------------------------------------------------------------- #
def _extract_facts_from_text(text, relpath):
    """从单文件文本逐行抽取候选事实，返回 facts 列表。"""
    facts = []
    seen = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        quote = line.strip()
        if len(quote) > 400:
            quote = quote[:400]
        matches = []
        for kind, regex in (
            ("seed", SEED_RE),
            ("number_unit", NUMBER_UNIT_RE),
            ("version", VERSION_RE),
            ("kv", KV_RE),
            ("path", PATH_RE),
            ("command", CMD_RE),
        ):
            for m in regex.finditer(line):
                value = m.group(0).strip()
                key = (kind, value)
                if key not in seen and value:
                    seen.add(key)
                    matches.append({"kind": kind, "value": value})
        matches = matches[:MAX_FACTS_PER_LINE]
        for m in matches:
            facts.append(
                {
                    "locator": "{}#L{}".format(relpath, lineno),
                    "kind": m["kind"],
                    "value": m["value"],
                    "quote": quote,
                }
            )
            if len(facts) >= MAX_FACTS_TOTAL:
                return facts
    return facts


def _pdf_to_text(pdf_path, work):
    """尝试用 pdftotext 抽取文本；失败返回 None。"""
    try:
        tmp = os.path.join(work, "pdf_extracted", os.path.basename(pdf_path) + ".txt")
        _ensure_dir(os.path.dirname(tmp))
        proc = subprocess.run(
            ["pdftotext", "-layout", pdf_path, tmp],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if proc.returncode == 0 and os.path.exists(tmp):
            return _read_text(tmp)
    except Exception:  # noqa: BLE001 pdftotext 不存在或超时均降级
        return None
    return None


def cmd_inventory(inp, out, work):
    inventory = []
    facts = []
    degradations = []
    _ensure_dir(work)

    if not os.path.isdir(inp):
        _write_json(
            os.path.join(work, "materials_inventory.json"),
            {"materials": [], "facts": 0, "degradations": ["input dir missing: " + inp]},
        )
        _write_json(os.path.join(work, "extracted_facts.json"), {"facts": []})
        print(json.dumps({"materials": 0, "facts": 0}, ensure_ascii=False))
        return

    for root, dirs, files in os.walk(inp):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            relpath = os.path.relpath(path, inp).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            category = EXT_CATEGORIES.get(ext, "other")
            entry = {
                "path": relpath,
                "category": category,
                "size": size,
                "facts": 0,
                "note": "",
            }
            if size > MAX_FILE_BYTES:
                entry["note"] = "skipped: over {} bytes".format(MAX_FILE_BYTES)
                degradations.append("skipped oversize file: " + relpath)
                inventory.append(entry)
                continue
            if category in ("text", "config", "code", "log", "table"):
                text = _read_text(path)
                fs = _extract_facts_from_text(text, relpath)
                facts.extend(fs)
                entry["facts"] = len(fs)
            elif category == "pdf":
                text = _pdf_to_text(path, work)
                if text:
                    fs = _extract_facts_from_text(text, relpath)
                    facts.extend(fs)
                    entry["facts"] = len(fs)
                    entry["note"] = "pdftotext extracted"
                else:
                    entry["note"] = "pdf text not extracted by inventory; use pdf-text-tool"
                    degradations.append("pdf not auto-extracted: " + relpath)
            elif category in ("office", "image"):
                entry["note"] = "use office-renderer/ocr-tool for content"
                degradations.append("needs tool: " + relpath)
            inventory.append(entry)

    _write_json(
        os.path.join(work, "materials_inventory.json"),
        {
            "materials": inventory,
            "facts": len(facts),
            "degradations": degradations,
            "generated_at": _now_utc(),
        },
    )
    _write_json(
        os.path.join(work, "extracted_facts.json"),
        {"facts": facts, "generated_at": _now_utc()},
    )
    print(
        json.dumps(
            {"materials": len(inventory), "facts": len(facts), "degradations": len(degradations)},
            ensure_ascii=False,
        )
    )


# --------------------------------------------------------------------------- #
# checkpoint：早落盘增量接口
# --------------------------------------------------------------------------- #
def cmd_checkpoint(inp, out, work, phase, status, note):
    _ensure_dir(out)
    _ensure_dir(work)
    result_path = os.path.join(out, "result.json")
    result, e = _load_json(result_path)
    if result is None or not isinstance(result, dict):
        result = {
            "case_id": "unknown",
            "status": "partial",
            "artifacts": [],
            "sources": [],
            "uncertainty": [],
            "change_log": [],
            "errors": [e] if e else [],
            "degradations": [],
            "limitations": [],
            "pending_human": [],
            "runtime": {"phase": "skeleton", "budget_seconds": 900, "budget_requests": 64},
        }
    if status in STATUSES:
        result["status"] = status
    change_log = result.get("change_log")
    if not isinstance(change_log, list):
        change_log = []
    change_log.append(
        {
            "step": phase or "checkpoint",
            "at": _now_utc(),
            "status": status or result.get("status"),
            "note": note or "",
        }
    )
    result["change_log"] = change_log
    runtime = result.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    runtime["phase"] = phase
    result["runtime"] = runtime
    _write_json(result_path, result)

    state_path = os.path.join(work, "state.json")
    state, _ = _load_json(state_path)
    if not isinstance(state, dict):
        state = {}
    state["phase"] = phase
    state["last_checkpoint"] = _now_utc()
    _write_json(state_path, state)
    print(
        json.dumps(
            {"status": result.get("status"), "phase": phase, "change_log_entries": len(change_log)},
            ensure_ascii=False,
        )
    )


# --------------------------------------------------------------------------- #
# validate：契约/状态/证据/产物自检
# --------------------------------------------------------------------------- #
def _check(name, ok, detail):
    return {"name": name, "ok": bool(ok), "detail": str(detail) if detail else ""}


def cmd_validate(inp, out, work):
    report = {"checks": [], "passed": True, "at": _now_utc()}
    result_path = os.path.join(out, "result.json")

    def add(check):
        report["checks"].append(check)
        if not check["ok"]:
            report["passed"] = False

    result, e = _load_json(result_path)
    if result is None or not isinstance(result, dict):
        add(_check("result_json_parse", False, e))
        _write_json(os.path.join(out, "validation_report.json"), report)
        print("FAIL result.json 不可解析")
        return

    cid = result.get("case_id")
    add(_check("case_id_present", isinstance(cid, str) and bool(cid), cid))
    status = result.get("status")
    add(_check("status_valid", status in STATUSES, status))

    case, _ = _load_json(os.path.join(inp, "case.json"))
    case = case if isinstance(case, dict) else {}
    oc = _output_contract(case)
    missing_oc = [k for k in oc if k not in result]
    add(_check("output_contract_fields_present", not missing_oc, "missing=" + ",".join(missing_oc)))

    for std in ("sources", "uncertainty", "change_log"):
        add(_check("std_" + std, std in result, std))

    change_log = result.get("change_log")
    if isinstance(change_log, list) and change_log:
        add(_check("change_log_nonempty", True, "entries={}".format(len(change_log))))
    else:
        add(_check("change_log_nonempty", False, "empty or missing"))

    # 主产物存在性：artifacts 中相对路径按 /app/output 解析
    artifacts = result.get("artifacts")
    if isinstance(artifacts, list):
        for a in artifacts:
            if isinstance(a, str):
                p = a if os.path.isabs(a) else os.path.join(out, a)
                add(_check("artifact_exists:" + a, os.path.exists(p), a))
    else:
        add(_check("artifacts_is_list", False, type(artifacts).__name__))

    # 证据映射：evidence_map.json 存在且每项 locator+quote 非空
    em_path = os.path.join(out, "evidence_map.json")
    if os.path.exists(em_path):
        em, ee = _load_json(em_path)
        if ee or not isinstance(em, dict):
            add(_check("evidence_map_valid", False, ee))
        else:
            bad = [
                k
                for k, v in em.items()
                if not (isinstance(v, dict) and v.get("locator") and v.get("quote"))
            ]
            add(_check("evidence_map_entries_complete", not bad, "bad=" + ",".join(bad[:5])))
    else:
        add(_check("evidence_map_exists", False, em_path))

    _write_json(os.path.join(out, "validation_report.json"), report)
    print("PASS" if report["passed"] else "FAIL")
    print(
        json.dumps(
            {"passed": report["passed"], "checks": len(report["checks"]), "report": os.path.join(out, "validation_report.json")},
            ensure_ascii=False,
        )
    )


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def run(argv=None):
    parser = argparse.ArgumentParser(prog="main.py", description="methods-experiments-writing helper")
    parser.add_argument("subcommand", nargs="?", default="help", choices=["skeleton", "inventory", "checkpoint", "validate", "help"])
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--phase", default="checkpoint")
    parser.add_argument("--status", default=None)
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    inp, out, work = _resolve_dirs(args)

    if args.subcommand == "skeleton":
        cmd_skeleton(inp, out, work)
    elif args.subcommand == "inventory":
        cmd_inventory(inp, out, work)
    elif args.subcommand == "checkpoint":
        cmd_checkpoint(inp, out, work, args.phase, args.status, args.note)
    elif args.subcommand == "validate":
        cmd_validate(inp, out, work)
    else:
        parser.print_help()


if __name__ == "__main__":
    run()
