#!/usr/bin/env python3
"""paper-structure-review 入口：发现材料 -> 落盘骨架 -> 增量补丁 -> 校验契约。

子命令：
  build      发现输入并写一份 partial result.json 骨架（先落盘原则）
  discover   列出 /input 材料文件、大小与类型
  patch <f>  把补丁 JSON 对象合并进 result.json（安全增量更新）
  validate   校验 result.json 契约与可回溯性，写 verify_report.json

仅依赖 Python 标准库。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CASE = "/input/case.json"
DEFAULT_TASK = "/input/task.md"
DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_RESULT = "/app/output/result.json"

# 当 case.json 未给出 output_contract 时的默认产物字段
DEFAULT_CONTRACT = ["diagnosis", "edit_plan", "source_map", "version_boundary"]

VALID_STATUS = {"complete", "partial", "insufficient_evidence", "input_error", "failed"}


def env(name, default):
    return os.environ.get(name, default)


def read_text(path, limit=2_000_000):
    """容错读取 UTF-8（带 BOM）文本；失败返回空串，不抛异常。"""
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def read_json(path):
    """容错读取 JSON；坏 JSON/缺失返回 None，不抛异常。"""
    txt = read_text(path)
    if not txt.strip():
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def log(action, status="ok"):
    return {"time": iso_now(), "action": action, "status": status}


def guess_kind(name):
    low = name.lower()
    if low.endswith((".md", ".markdown", ".txt", ".tex", ".latex")):
        return "text"
    if low.endswith(".pdf"):
        return "pdf"
    if low.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp")):
        return "image"
    if low.endswith(".json"):
        return "json"
    if low.endswith(".csv"):
        return "csv"
    if low.endswith((".docx", ".doc", ".pptx", ".xlsx")):
        return "office"
    return "other"


def discover_files(input_dir):
    items = []
    if not os.path.isdir(input_dir):
        return items
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        items.append({"name": name, "path": p, "size": size, "kind": guess_kind(name)})
    return items


def load_contract_keys(case):
    if isinstance(case, dict):
        c = case.get("output_contract") or case.get("outputContract")
        if isinstance(c, dict):
            return list(c.keys())
        if isinstance(c, list):
            return [str(x) for x in c]
    return list(DEFAULT_CONTRACT)


def build_result(case, task, input_dir, result_path, work_dir):
    out_dir = os.path.dirname(result_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    case_id = None
    if isinstance(case, dict):
        case_id = case.get("case_id") or case.get("id")
    contract = load_contract_keys(case)
    result = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [log("skeleton: 落盘 partial 骨架", "ok")],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "runtime": {
            "started_at": iso_now(),
            "input_files": discover_files(input_dir),
        },
    }
    for key in contract:
        result.setdefault(key, None)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def patch_result(result_path, patch_path):
    """把补丁 JSON 对象合并进 result.json；case_id/change_log 不被覆盖。"""
    result = read_json(result_path) or {}
    patch = read_json(patch_path)
    if not isinstance(patch, dict):
        return None, "补丁不是 JSON 对象"
    for k, v in patch.items():
        if k in ("change_log", "case_id"):
            continue
        result[k] = v
    result.setdefault("change_log", []).append(log("patch: merge keys %s" % sorted(patch.keys()), "ok"))
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result, None


def validate_result(result_path):
    result = read_json(result_path)
    if result is None:
        return {"ok": False, "problems": ["result.json 不存在或不是合法 JSON"], "warnings": []}
    problems, warnings = [], []
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["根节点应为对象"], "warnings": []}
    for field in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if field not in result:
            problems.append("缺少字段 %s" % field)
    if result.get("status") not in VALID_STATUS:
        warnings.append("status 值不在标准枚举内：%r" % result.get("status"))
    sources = result.get("sources")
    if isinstance(sources, list):
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                problems.append("sources[%d] 非对象" % i)
                continue
            if not s.get("source_ref") and not s.get("quote"):
                warnings.append("sources[%d] 缺 source_ref 与 quote（claim_id=%r）" % (i, s.get("claim_id")))
    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "status": result.get("status"),
        "case_id": result.get("case_id"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="paper-structure-review 入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("build", "discover", "validate"):
        sp = sub.add_parser(name)
        sp.add_argument("--case", default=env("CASE_JSON", DEFAULT_CASE))
        sp.add_argument("--task", default=env("TASK_MD", DEFAULT_TASK))
        sp.add_argument("--input", default=env("INPUT_DIR", DEFAULT_INPUT))
        sp.add_argument("--work", default=env("WORK_DIR", DEFAULT_WORK))
        sp.add_argument("--out", default=env("RESULT_JSON", DEFAULT_RESULT))

    pp = sub.add_parser("patch")
    pp.add_argument("patch_file")
    pp.add_argument("--out", default=env("RESULT_JSON", DEFAULT_RESULT))

    a = ap.parse_args(argv)

    if a.cmd == "discover":
        print(json.dumps({"ok": True, "input_dir": a.input, "files": discover_files(a.input)},
                         ensure_ascii=False, indent=2))
        return 0

    if a.cmd == "build":
        case = read_json(a.case)
        task = read_text(a.task)
        build_result(case, task, a.input, a.out, a.work)
        cid = case.get("case_id") or case.get("id") if isinstance(case, dict) else None
        print(json.dumps({"ok": True, "result": a.out, "case_id": cid}, ensure_ascii=False))
        return 0

    if a.cmd == "patch":
        result, err = patch_result(a.out, a.patch_file)
        if err:
            print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "result": a.out}, ensure_ascii=False))
        return 0

    if a.cmd == "validate":
        report = validate_result(a.out)
        out_dir = os.path.dirname(a.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        report_path = os.path.join(out_dir, "verify_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
