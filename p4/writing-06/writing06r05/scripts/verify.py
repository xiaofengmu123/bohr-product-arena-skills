#!/usr/bin/env python3
"""Contract validation for /app/output/result.json. Stdlib only.

    python scripts/verify.py --case /input/case.json \
        --result /app/output/result.json --out /app/output/verify_report.json
"""
import argparse
import datetime
import json
import os
import sys

DEFAULT_CONTRACT = ["case_id", "status", "diagnosis", "edit_plan", "source_map",
                    "version_boundary", "artifacts", "sources", "uncertainty",
                    "pending_human", "change_log"]
RUNTIME_REQUIRED = ["case_id", "status", "diagnosis", "sources", "uncertainty",
                    "change_log"]
STATUS_ENUM = {"complete", "partial", "insufficient_evidence", "input_error",
               "failed"}
EDIT_VERBS = {"move", "merge", "split", "narrow", "reorder",
              "insert_transition", "reassign_evidence", "add_limitation",
              "insert", "delete", "replace", "clarify"}
SCALAR_NONEMPTY = {"case_id", "status", "diagnosis"}
LIST_FIELDS = {"sources", "source_map", "edit_plan", "artifacts", "change_log",
               "uncertainty", "pending_human"}
OBJ_FIELDS = {"version_boundary"}


def load_json_tolerant(path):
    if not os.path.isfile(path):
        return None, "missing file: {}".format(path)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f), "ok"
        except (UnicodeDecodeError, UnicodeError):
            continue
        except json.JSONDecodeError as e:
            return None, "bad json: {}".format(e)
        except OSError as e:
            return None, "read error: {}".format(e)
    return None, "undecodable"


def discover_contract_keys(case_obj):
    keys = list(DEFAULT_CONTRACT)
    if isinstance(case_obj, dict):
        oc = case_obj.get("output_contract")
        if isinstance(oc, dict):
            for k in oc:
                if k not in keys:
                    keys.append(k)
        elif isinstance(oc, list):
            for k in oc:
                if isinstance(k, str) and k not in keys:
                    keys.append(k)
    return keys


def validate(result, required_keys):
    checks = []

    def add(level, item, detail=""):
        checks.append({"level": level, "item": item, "detail": detail})

    if not isinstance(result, dict):
        add("FAIL", "result_json_type", "result.json 顶层必须是 JSON 对象")
        return checks

    for k in required_keys:
        if k not in result:
            add("FAIL", "missing_field:{}".format(k), "output_contract 字段缺失")
            continue
        v = result[k]
        if k in SCALAR_NONEMPTY:
            if v in (None, ""):
                add("FAIL", "empty_field:{}".format(k), "必需字段为空")
            else:
                add("PASS", "field:{}".format(k), "")
        elif k in LIST_FIELDS:
            if not isinstance(v, list):
                add("FAIL", "type_field:{}".format(k), "应为数组")
            elif not v:
                level = "FAIL" if k == "change_log" else "WARN"
                add(level, "empty_field:{}".format(k),
                    "change_log 不能为空" if k == "change_log"
                    else "字段为空，确认是否确无内容")
            else:
                add("PASS", "field:{}".format(k), "")
        elif k in OBJ_FIELDS:
            if not isinstance(v, dict):
                add("FAIL", "type_field:{}".format(k), "应为对象")
            else:
                add("PASS", "field:{}".format(k), "")
        else:
            # unknown output_contract field: presence + non-null is enough
            if v is None:
                add("FAIL", "null_field:{}".format(k), "output_contract 字段为 null")
            else:
                add("PASS", "field:{}".format(k), "")

    if result.get("case_id") in (None, ""):
        add("FAIL", "case_id", "case_id 为空")
    else:
        add("PASS", "case_id", "")

    st = result.get("status")
    if st not in STATUS_ENUM:
        add("FAIL", "status_enum", "status 非法: {!r}".format(st))
    else:
        add("PASS", "status_enum", "")

    for group in ("sources", "source_map"):
        arr = result.get(group)
        if not isinstance(arr, list):
            continue
        for i, it in enumerate(arr):
            if not isinstance(it, dict):
                add("WARN", "{}[{}]_type".format(group, i), "条目应为对象")
                continue
            missing = [f for f in ("locator", "quote")
                       if not str(it.get(f) or "").strip()]
            if missing:
                add("FAIL", "{}[{}]_trace".format(group, i),
                    "缺 " + ",".join(missing) + "：关键结论必须可回溯")
            else:
                add("PASS", "{}[{}]_trace".format(group, i), "")

    ep = result.get("edit_plan")
    if isinstance(ep, list):
        for i, it in enumerate(ep):
            if not isinstance(it, dict):
                continue
            a = it.get("action")
            if a and a not in EDIT_VERBS:
                add("WARN", "edit_plan[{}]_action".format(i),
                    "动作未登记: {!r}".format(a))
            for f in ("from", "instruction"):
                if not str(it.get(f) or "").strip():
                    add("WARN", "edit_plan[{}]_{}".format(i, f), "建议补全")

    cl = result.get("change_log")
    if isinstance(cl, list) and cl:
        add("PASS", "change_log_nonempty", "{} 条记录".format(len(cl)))
        if not any(isinstance(e, dict) and
                   any(s in str(e.get("action", "")).lower()
                       for s in ("skeleton", "init", "骨架")) for e in cl):
            add("WARN", "change_log_skeleton", "未见骨架落盘记录，需确认已先行落盘")
    else:
        add("FAIL", "change_log_nonempty", "change_log 为空")

    for bad in ("benchmark_score", "fake_score"):
        if bad in result:
            add("FAIL", "forbidden_key:{}".format(bad), "禁止伪造评测分数")
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=os.environ.get("CASE_JSON",
                                                     "/input/case.json"))
    ap.add_argument("--result", default=os.environ.get("RESULT_JSON",
                                                       "/app/output/result.json"))
    ap.add_argument("--out", default=os.environ.get("VERIFY_JSON",
                                                    "/app/output/verify_report.json"))
    a = ap.parse_args()
    case_obj, _ = load_json_tolerant(a.case)
    required = discover_contract_keys(case_obj)
    result, err = load_json_tolerant(a.result)
    if result is None:
        checks = [{"level": "FAIL", "item": "result_load", "detail": err}]
    else:
        checks = validate(result, required)
    report = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "result_path": a.result,
        "required_keys": required,
        "checks": checks,
        "pass": sum(1 for c in checks if c["level"] == "PASS"),
        "warn": sum(1 for c in checks if c["level"] == "WARN"),
        "fail": sum(1 for c in checks if c["level"] == "FAIL"),
        "ok": not any(c["level"] == "FAIL" for c in checks),
    }
    outdir = os.path.dirname(a.out) or "."
    try:
        os.makedirs(outdir, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": report["ok"], "pass": report["pass"],
                      "warn": report["warn"], "fail": report["fail"],
                      "out": a.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
