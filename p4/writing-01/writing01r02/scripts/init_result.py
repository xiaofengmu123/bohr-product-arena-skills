#!/usr/bin/env python3
"""init_result.py -- 早落盘：立即写出合法、诚实的 partial result.json 骨架。

设计目标（时间预算与早落盘变体）：
  运行第 0 步就调用本脚本，无论材料是否读完、无论后续是否成功，
  都保证 /app/output/result.json 已经是一份可被 json.load 读取、
  且覆盖 case_id + output_contract 全部字段的合法产物。

只依赖 Python 标准库；任何输入异常都不崩溃，永远尝试写出最小合法结果。
"""
import argparse
import datetime
import json
import os
import sys


def _stdout(text):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(text)
    sys.stdout.flush()


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def read_json_safe(path):
    """读取 JSON：容忍 BOM、常见编码、坏 JSON；失败返回 ({}, [error])，不抛异常。"""
    errors = []
    if not path or not os.path.exists(path):
        return {}, ["missing file: %s" % (path or "<none>")]
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return {}, ["read failed: %s (%s)" % (path, exc)]
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return {}, ["undecodable file: %s" % path]
    try:
        return json.loads(text), errors
    except (json.JSONDecodeError, ValueError) as exc:
        errors.append("bad json: %s (%s)" % (path, exc))
        try:
            # 截断重试一次，能救回部分结构就救
            return json.loads(text[:200000]), errors
        except (json.JSONDecodeError, ValueError):
            return {}, errors


def discover_case_id(case, path):
    if isinstance(case, dict):
        for key in ("id", "case_id", "caseId", "challenge_id", "challengeId"):
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, int):
                return str(val)
    if path:
        base = os.path.basename(path)
        if base.lower().endswith(".json"):
            return base[:-5]
        return base
    return "unknown"


def discover_output_contract(case):
    """回填题面 output_contract 的键；缺省使用三字段（目标产物/事实来源映射/版本边界）。"""
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict) and oc:
            return dict(oc)
        if isinstance(oc, list) and oc:
            return {str(k): None for k in oc}
    return {"deliverable": None, "fact_source_map": [], "version_limits": None}


def build_skeleton(case_id, oc, read_errors, ts):
    defaults = {"deliverable": None, "fact_source_map": [], "version_limits": None}
    result = {
        "case_id": case_id,
        "status": "partial",  # 早落盘：先如实声明 partial，成稿后再改 complete
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [
            {"ts": ts, "step": "bootstrap", "note": "落盘 partial 骨架；材料尚未读取/抽取"}
        ],
        "errors": list(read_errors),
        "degradations": [],
        "runtime": {"bootstrap_by": "init_result.py", "started_utc": ts},
    }
    # output_contract 原样回填全部键：不改名、不删除未知键；空值用默认占位
    for key, val in oc.items():
        if key in result:
            continue
        result[key] = val if val not in (None, "", [], {}) else defaults.get(key, None)
    for key, val in defaults.items():
        result.setdefault(key, val)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="早落盘：写 result.json 骨架")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--out", default="/app/output/result.json")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    out_abs = os.path.abspath(args.out)
    try:
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        os.makedirs(args.work, exist_ok=True)
    except OSError as exc:
        _stdout(json.dumps({"ok": False, "error": "mkdir failed: %s" % exc},
                           ensure_ascii=False) + "\n")
        return 1

    case, read_errors = read_json_safe(args.case)
    case_id = discover_case_id(case, args.case)
    oc = discover_output_contract(case)
    ts = now_utc()
    result = build_skeleton(case_id, oc, read_errors, ts)

    try:
        with open(out_abs, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        _stdout(json.dumps({"ok": False, "error": "write failed: %s" % exc},
                           ensure_ascii=False) + "\n")
        return 1

    meta = {"case_id": case_id, "output_contract_keys": list(oc.keys()),
            "started_utc": ts, "result_path": out_abs}
    try:
        with open(os.path.join(args.work, "contract_meta.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError:
        pass  # 元信息写失败不影响主产物

    _stdout(json.dumps({"ok": True, "case_id": case_id, "status": "partial",
                        "output_contract_keys": list(oc.keys()),
                        "result": out_abs, "read_errors": read_errors},
                       ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
