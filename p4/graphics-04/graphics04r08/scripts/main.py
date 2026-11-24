#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — tracefigure-skill 结果生命周期管理。

子命令：
  init      写入 in_progress 结果骨架（前 3 分钟内必须先落盘）
  merge     把 /app/work/{ir,checks,exports_meta}.json 合并进六类契约字段
  finalize  写最终 status、artifacts 与结束时间
  run       一键 init -> build+validate -> merge -> finalize（确定性，供演示/CI）

仅标准库。缺省路径对齐运行契约：/input、/app/output、/app/work。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

CONTRACT_FIELDS = [
    "entities", "relations", "data_encoding",
    "legend_annotations", "editable_sources", "exports",
]
COMMON_FIELDS = [
    "sources", "uncertainty", "pending_human", "limitations", "checks", "artifacts",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    if not path or not os.path.exists(path):
        return None, "missing: %s" % path
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, "bad json %s: %s" % (path, e)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def touch_log(obj, action, status):
    obj.setdefault("change_log", []).append({"ts": now_iso(), "action": action, "status": status})
    return obj


def find_case(input_dir):
    for name in ("case.json", "case.yaml", "case.yml"):
        p = os.path.join(input_dir, name)
        if os.path.exists(p):
            return p
    return None


def init_case(input_dir, out_path):
    """落盘 in_progress 骨架：case_id + 空契约字段 + 首条 change_log。"""
    case_path = find_case(input_dir)
    case, err = read_json(case_path) if case_path else (None, "no case.json in %s" % input_dir)
    case_id = None
    declared = []
    if isinstance(case, dict):
        case_id = case.get("id") or case.get("case_id") or case.get("challenge_id")
        oc = case.get("output_contract")
        if isinstance(oc, dict):
            declared = [k for k in oc.keys() if isinstance(k, str)]
        elif isinstance(oc, list):
            declared = [k for k in oc if isinstance(k, str)]
    if not case_id:
        case_id = "unknown"
    skel = {
        "case_id": case_id,
        "title": "",
        "status": "in_progress",
        "figure_kind": None,
    }
    for k in declared:
        if k not in skel:
            skel[k] = [] if k in CONTRACT_FIELDS else None
    for k in CONTRACT_FIELDS:
        skel.setdefault(k, [])
    for k in COMMON_FIELDS:
        skel.setdefault(k, [])
    skel["runtime"] = {
        "start_ts": now_iso(), "end_ts": None,
        "time_budget_s": 900, "request_budget": 64, "token_budget_per_response": 16384,
    }
    skel["change_log"] = []
    touch_log(skel, "init skeleton (early partial deliverable, <180s)", "in_progress")
    write_json(out_path, skel)
    return skel, err


def merge_work(work_dir, out_path):
    """合并 IR/checks/exports 到 result.json 的契约字段。"""
    res, err = read_json(out_path)
    if not isinstance(res, dict):
        res = {"case_id": "unknown"}
    ir, _ = read_json(os.path.join(work_dir, "ir.json"))
    if isinstance(ir, dict):
        for k in CONTRACT_FIELDS:
            if ir.get(k):
                res[k] = ir[k]
        for k in ("title", "figure_kind", "case_id"):
            if ir.get(k):
                res[k] = ir[k]
        for k in ("sources", "uncertainty", "pending_human", "limitations"):
            if ir.get(k):
                res[k] = ir[k]
    exports_meta, _ = read_json(os.path.join(work_dir, "exports_meta.json"))
    if isinstance(exports_meta, dict):
        if exports_meta.get("editable_sources"):
            res["editable_sources"] = exports_meta["editable_sources"]
        if exports_meta.get("exports"):
            res["exports"] = exports_meta["exports"]
    checks, _ = read_json(os.path.join(work_dir, "checks.json"))
    if isinstance(checks, (dict, list)):
        res["checks"] = checks
    partial, _ = read_json(os.path.join(work_dir, "partial.json"))
    if isinstance(partial, dict):
        for k, v in partial.items():
            if k != "case_id":
                res[k] = v
    touch_log(res, "merge work dir IR/checks/exports", res.get("status", "in_progress"))
    write_json(out_path, res)
    return res, err


def finalize(out_path, status, artifacts=None):
    res, err = read_json(out_path)
    if not isinstance(res, dict):
        res = {"case_id": "unknown"}
    res["status"] = status
    rt = res.setdefault("runtime", {})
    rt["end_ts"] = now_iso()
    if artifacts:
        lst = res.setdefault("artifacts", [])
        for a in artifacts:
            if isinstance(a, dict) and a.get("path") and a not in lst:
                lst.append(a)
    touch_log(res, "finalize status=%s" % status, status)
    write_json(out_path, res)
    return res, err


def run(input_dir, output_dir, work_dir):
    """一键确定性跑通：init -> build -> validate -> merge -> finalize。"""
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(output_dir, "result.json")
    skel, err = init_case(input_dir, out)
    ir_path = os.path.join(work_dir, "ir.json")
    ir, ir_err = read_json(ir_path)
    svg_path = os.path.join(output_dir, "figure.svg")
    if isinstance(ir, dict):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import figure
        figure.build_svg(ir, svg_path)
        issues = figure.validate_svg(ir, svg_path)
        figure.write_json(os.path.join(work_dir, "checks.json"), {
            "svg": svg_path,
            "critical": sum(1 for i in issues if i["level"] == "critical"),
            "warning": sum(1 for i in issues if i["level"] == "warning"),
            "issues": issues,
        })
    merge_work(work_dir, out)
    artifacts = []
    if os.path.exists(svg_path):
        artifacts.append({"path": svg_path, "format": "svg", "note": "editable layered source"})
    artifacts.append({"path": out, "format": "json", "note": "result.json"})
    status = "partial" if (err or ir_err or not isinstance(ir, dict)) else "complete"
    finalize(out, status, artifacts)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py", description="tracefigure-skill result lifecycle")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="write in_progress skeleton")
    p_init.add_argument("--case", default=DEFAULT_INPUT)
    p_init.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p_merge = sub.add_parser("merge", help="merge work dir into result.json")
    p_merge.add_argument("--work", default=DEFAULT_WORK)
    p_merge.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p_fin = sub.add_parser("finalize", help="set final status")
    p_fin.add_argument("--status", default="partial")
    p_fin.add_argument("--out", default=os.path.join(DEFAULT_OUTPUT, "result.json"))
    p_run = sub.add_parser("run", help="one-shot deterministic pipeline")
    p_run.add_argument("--input", default=DEFAULT_INPUT)
    p_run.add_argument("--output", default=DEFAULT_OUTPUT)
    p_run.add_argument("--work", default=DEFAULT_WORK)
    a = ap.parse_args(argv)

    if a.cmd == "init":
        _, err = init_case(a.case, a.out)
        print(json.dumps({"ok": True, "error": err}, ensure_ascii=False))
        return 0
    if a.cmd == "merge":
        _, err = merge_work(a.work, a.out)
        print(json.dumps({"ok": True, "error": err}, ensure_ascii=False))
        return 0
    if a.cmd == "finalize":
        _, err = finalize(a.out, a.status)
        print(json.dumps({"ok": True, "error": err}, ensure_ascii=False))
        return 0
    if a.cmd == "run":
        out = run(a.input, a.output, a.work)
        print(json.dumps({"ok": True, "out": out}, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
