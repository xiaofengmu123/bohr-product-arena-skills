#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验结果解释与异常分析 Skill 主入口。

子命令：
  skeleton  --input DIR --output DIR [--work DIR]   落盘合法结果骨架（先落 partial，再增量改进）
  manifest  --input DIR                             计算输入目录指纹（确定性）
  normalize --input PATH                            确定性规范化 result.json（键排序/浮点规整/列表稳定排序）
  validate  --input PATH                            校验 schema 与证据完整性（退出码非 0 表示硬错误）
  finalize  --input PATH                            写 runtime、指纹与 change_log（不改变科学内容）

仅使用 Python 标准库。运行时缺省 --input=/input --output=/app/output --work=/app/work。
确定性约定：科学内容（五段结论、方案、证据、画像、指纹）两次运行必须逐字节一致；
runtime 时间戳与 change_log 时间戳属过程元数据，不参与内容指纹。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"
MAX_FILE_BYTES = 2_000_000

STATUSES = ("ok", "partial", "insufficient_evidence", "input_error")
ANALYSIS_MODES = ("pending", "curve_trace", "tabular", "ml_validation", "workbook_paper", "mixed", "unknown")

TOP_KEYS = (
    "case_id", "status", "task_type", "analysis_mode", "summary",
    "interpretation", "experiment_plan", "evidence_and_risk",
    "confirmation_items", "sources", "uncertainty", "change_log",
    "errors", "degradations", "limitations", "pending_human",
    "runtime", "reproducibility",
)
INTERPRETATION_KEYS = (
    "trends", "anomalies", "possible_causes", "verification_steps", "followup_experiments",
)
PLAN_KEYS = (
    "hypothesis", "testable_question", "variables", "controls",
    "steps", "parameters", "resources", "interpretation_criteria",
)
RISK_KEYS = ("evidence_basis", "risk_and_anomaly_handling", "assumptions")
CONFIRM_KEYS = ("safety", "ethics", "equipment_availability", "expert_confirmation")


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """UTF-8(含 BOM)优先，坏编码降级 latin-1；超限或不可读返回 (None, reason)。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    if size > max_bytes:
        return None, "file exceeds %d bytes" % max_bytes
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            return None, str(exc)
    return None, "unreadable encoding"


def load_json_safe(path):
    text, _ = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_" + os.path.basename(path) + "_" + str(os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def write_text_atomic(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".tmp_" + os.path.basename(path) + "_" + str(os.getpid()))
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def discover_files(root):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            rel = os.path.relpath(full, root).replace("\\", "/")
            if rel.startswith(".tmp_"):
                continue
            out.append({"path": full, "rel": rel, "size_bytes": size,
                        "ext": os.path.splitext(fn)[1].lower()})
    return out


def sha256_dir(root):
    """确定性目录指纹：对排序后的 (rel path, 内容 sha256) 计算摘要。"""
    files = discover_files(root)
    h = hashlib.sha256()
    for f in files:
        h.update(f["rel"].encode("utf-8", "replace"))
        h.update(b"\x00")
        digest = sha256_file(f["path"]) or "unreadable"
        h.update(digest.encode("ascii"))
        h.update(b"\x00")
    return h.hexdigest(), files


def resolve_case_id(input_dir):
    obj = load_json_safe(os.path.join(input_dir, "case.json"))
    if isinstance(obj, dict):
        for key in ("case_id", "id", "challenge_id", "armiId", "armi_id", "armiId"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        content = obj.get("content")
        if isinstance(content, str):
            m = re.search(r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)", content, re.I)
            if m:
                return m.group(1)
    text, _ = read_text(os.path.join(input_dir, "task.md"))
    if text:
        m = re.search(r"challenge[-_ ]?id[:\s]+([A-Za-z0-9_.-]+)", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"challenge[-_ ]([0-9a-fA-F-]{8,})", text)
        if m:
            return m.group(0)
    base = os.path.basename(os.path.abspath(input_dir).rstrip("/\\")) or "unknown"
    return base


def _default_for(value):
    if isinstance(value, dict):
        return {k: _default_for(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def merge_output_contract(result, case_obj):
    """若 case.json 含 output_contract，原样回填全部键（缺值给 null/[]），不改名不删未知键。"""
    if not isinstance(case_obj, dict):
        return []
    contract = case_obj.get("output_contract")
    if contract is None:
        return []
    meta = ("fields", "required", "schema", "description", "title")
    names = []
    if isinstance(contract, dict):
        for key in ("fields", "required", "schema"):
            v = contract.get(key)
            if isinstance(v, list):
                names.extend(x for x in v if isinstance(x, str))
        for k in contract:
            if isinstance(k, str) and k not in meta:
                names.append(k)
    elif isinstance(contract, list):
        names = [x for x in contract if isinstance(x, str)]
    seen = set()
    names = [x for x in names if not (x in seen or seen.add(x))]
    merged = []
    for k in names:
        if k not in result:
            default = None
            if isinstance(contract, dict) and k in contract:
                default = _default_for(contract[k])
            result[k] = default
            merged.append(k)
    return merged


def empty_skeleton(case_id, status="partial"):
    return {
        "case_id": case_id,
        "status": status,
        "task_type": "experiment_result_interpretation",
        "analysis_mode": "pending",
        "summary": "",
        "interpretation": {
            "trends": [],
            "anomalies": [],
            "possible_causes": [],
            "verification_steps": [],
            "followup_experiments": [],
        },
        "experiment_plan": {
            "hypothesis": None,
            "testable_question": None,
            "variables": [],
            "controls": [],
            "steps": [],
            "parameters": [],
            "resources": [],
            "interpretation_criteria": [],
        },
        "evidence_and_risk": {
            "evidence_basis": [],
            "risk_and_anomaly_handling": [],
            "assumptions": [],
        },
        "confirmation_items": {
            "safety": [],
            "ethics": [],
            "equipment_availability": [],
            "expert_confirmation": [],
        },
        "sources": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "limitations": [],
        "pending_human": [],
        "runtime": {
            "started_at": None,
            "finished_at": None,
            "elapsed_seconds": None,
            "model_requests_used": None,
            "environment": "unknown",
        },
        "reproducibility": {
            "input_fingerprint": None,
            "profile_fingerprint": None,
            "content_fingerprint": None,
            "rerun_verified": None,
            "sorting": "deterministic",
            "random_seed": None,
        },
    }


def _log(entry, stage, detail):
    entry.append({"ts": now_ts(), "stage": stage, "detail": detail})


def build_skeleton(input_dir, output_dir, work_dir):
    case_id = resolve_case_id(input_dir)
    result = empty_skeleton(case_id, "partial")
    case_obj = load_json_safe(os.path.join(input_dir, "case.json"))
    merged = merge_output_contract(result, case_obj)
    fp, files = sha256_dir(input_dir)
    result["reproducibility"]["input_fingerprint"] = fp
    result["runtime"]["started_at"] = now_ts()
    _log(result["change_log"], "skeleton",
         "骨架落盘；输入文件数=%d；output_contract 回填=%s" % (len(files), merged or "无"))
    if not files:
        result["status"] = "insufficient_evidence"
        result["limitations"].append("输入目录无可读文件，缺少实验数据材料。")
    os.makedirs(work_dir, exist_ok=True)
    write_json_atomic(os.path.join(output_dir, "result.json"), result)
    write_text_atomic(os.path.join(output_dir, "report.md"),
                      "# 实验结果解释报告（骨架）\n\ncase_id: %s\n\nstatus: %s\n\n"
                      "五段结论、方案、依据与风险、确认项待填充。\n" % (case_id, result["status"]))
    return result


def canonical_float(x, ndigits=12):
    if not isinstance(x, float):
        return x
    if x != x or x in (float("inf"), float("-inf")):
        return x
    return round(x, ndigits)


def canonicalize(obj):
    """确定性规范化：字典键排序、字符串列表排序、字典列表按规范 JSON 稳定排序、浮点规整。"""
    if isinstance(obj, dict):
        return {k: canonicalize(v) for k, v in sorted(obj.items(), key=lambda kv: kv[0])}
    if isinstance(obj, list):
        items = [canonicalize(v) for v in obj]
        if items and all(isinstance(v, dict) for v in items):
            items.sort(key=lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True, default=str))
        elif items and all(isinstance(v, str) for v in items):
            items.sort()
        return items
    if isinstance(obj, float):
        return canonical_float(obj)
    return obj


def content_fingerprint(obj):
    """科学内容指纹：剔除 runtime/change_log 时间戳与自引用指纹后计算。"""
    clone = json.loads(json.dumps(obj, ensure_ascii=False, default=str))
    if isinstance(clone, dict):
        clone["runtime"] = {"__excluded_from_content__": True}
        clone["change_log"] = []
        rep = clone.get("reproducibility")
        if isinstance(rep, dict):
            rep["content_fingerprint"] = None
            rep["rerun_verified"] = None
    clone = canonicalize(clone)
    return sha256_bytes(json.dumps(clone, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def normalize(path):
    obj = load_json_safe(path)
    if obj is None:
        print("normalize: cannot parse JSON: %s" % path, file=sys.stderr)
        return 1
    obj = canonicalize(obj)
    write_json_atomic(path, obj)
    return 0


def _require(cond, msg, issues, hard=True):
    if not cond:
        issues.append({"level": "error" if hard else "warning", "message": msg})
    return bool(cond)


def _check_evidence_item(item, where, issues):
    ok = True
    if not isinstance(item, dict):
        _require(False, "%s: 结论必须是对象(dict)，得到 %s" % (where, type(item).__name__), issues)
        return False
    ev = item.get("evidence")
    if not isinstance(ev, list) or not ev:
        _require(False, "%s: 缺非空 evidence 数组（每项含 locator+quote）" % where, issues)
        ok = False
    else:
        for i, e in enumerate(ev):
            if not isinstance(e, dict) or not str(e.get("locator", "")).strip() or not str(e.get("quote", "")).strip():
                _require(False, "%s: evidence[%d] 缺非空 locator 或 quote" % (where, i), issues)
                ok = False
    return ok


def validate(path):
    obj = load_json_safe(path)
    if obj is None:
        print("validate: cannot parse JSON: %s" % path, file=sys.stderr)
        return 1
    issues = []
    _require(isinstance(obj, dict), "顶层必须是对象", issues)
    if isinstance(obj, dict):
        for k in TOP_KEYS:
            _require(k in obj, "缺顶层字段 %s" % k, issues)
        _require(str(obj.get("case_id", "")).strip(), "case_id 为空", issues)
        _require(obj.get("status") in STATUSES, "status 非法：%r" % obj.get("status"), issues)
        _require(obj.get("analysis_mode") in ANALYSIS_MODES,
                 "analysis_mode 非法：%r" % obj.get("analysis_mode"), issues)
        interp = obj.get("interpretation")
        _require(isinstance(interp, dict), "interpretation 必须是对象", issues)
        if isinstance(interp, dict):
            for k in INTERPRETATION_KEYS:
                _require(k in interp, "interpretation 缺 %s" % k, issues)
                _require(isinstance(interp.get(k), list), "interpretation.%s 必须是数组" % k, issues)
            for k in ("trends", "anomalies"):
                for i, item in enumerate(interp.get(k, []) or []):
                    _check_evidence_item(item, "interpretation.%s[%d]" % (k, i), issues)
            for i, item in enumerate(interp.get("possible_causes", []) or []):
                where = "interpretation.possible_causes[%d]" % i
                if not isinstance(item, dict):
                    _require(False, "%s: 必须是对象" % where, issues)
                    continue
                sup = item.get("supporting_evidence")
                exl = item.get("excluding_evidence")
                _require(isinstance(sup, list) and sup, "%s: supporting_evidence 非空数组" % where, issues)
                _require(isinstance(exl, list), "%s: excluding_evidence 数组（可为空）" % where, issues)
                _require("confound_risk" in item, "%s: 缺 confound_risk" % where, issues)
                _require("testability" in item, "%s: 缺 testability" % where, issues)
            for i, item in enumerate(interp.get("verification_steps", []) or []):
                where = "interpretation.verification_steps[%d]" % i
                _require(isinstance(item, dict) and str(item.get("target", "")).strip(),
                         "%s: 缺非空 target（可执行核查目标）" % where, issues)
            for i, item in enumerate(interp.get("followup_experiments", []) or []):
                where = "interpretation.followup_experiments[%d]" % i
                if isinstance(item, dict):
                    _require(str(item.get("reason", "")).strip(), "%s: 缺非空 reason" % where, issues)
                    _require(str(item.get("expected_outcome", "")).strip(),
                             "%s: 缺非空 expected_outcome" % where, issues)
                else:
                    _require(False, "%s: 必须是对象" % where, issues)
        plan = obj.get("experiment_plan")
        _require(isinstance(plan, dict), "experiment_plan 必须是对象", issues)
        if isinstance(plan, dict):
            for k in PLAN_KEYS:
                _require(k in plan, "experiment_plan 缺 %s" % k, issues)
        risk = obj.get("evidence_and_risk")
        _require(isinstance(risk, dict), "evidence_and_risk 必须是对象", issues)
        if isinstance(risk, dict):
            for k in RISK_KEYS:
                _require(k in risk, "evidence_and_risk 缺 %s" % k, issues)
        conf = obj.get("confirmation_items")
        _require(isinstance(conf, dict), "confirmation_items 必须是对象", issues)
        if isinstance(conf, dict):
            for k in CONFIRM_KEYS:
                _require(k in conf, "confirmation_items 缺 %s" % k, issues)
        empty_lists = [k for k in INTERPRETATION_KEYS if isinstance(interp, dict) and not interp.get(k)]
        if empty_lists:
            issues.append({"level": "warning",
                           "message": "interpretation 空段落: %s（如实空须在 limitations 说明原因）"
                                      % ", ".join(empty_lists)})
    hard = [i for i in issues if i["level"] == "error"]
    for i in issues:
        print("[%s] %s" % (i["level"], i["message"]), file=sys.stderr)
    print("validate: %d error(s), %d warning(s)" % (len(hard), len(issues) - len(hard)))
    return 1 if hard else 0


def finalize(path):
    obj = load_json_safe(path)
    if obj is None:
        print("finalize: cannot parse JSON: %s" % path, file=sys.stderr)
        return 1
    obj = canonicalize(obj)
    rt = obj.get("runtime") or {}
    if not rt.get("started_at"):
        rt["started_at"] = now_ts()
    rt["finished_at"] = now_ts()
    if rt.get("started_at"):
        try:
            t0 = time.mktime(time.strptime(rt["started_at"], "%Y-%m-%dT%H:%M:%SZ"))
            rt["elapsed_seconds"] = max(0, int(time.time() - t0))
        except (ValueError, OverflowError):
            rt["elapsed_seconds"] = None
    rt["environment"] = "%s %s" % (os.name, sys.version.split()[0])
    obj["runtime"] = rt
    rep = obj.setdefault("reproducibility", {})
    rep["content_fingerprint"] = content_fingerprint(obj)
    if rep.get("input_fingerprint") is None:
        rep["input_fingerprint"] = None
    _log(obj["change_log"], "finalize",
         "定稿；content_fingerprint=%s" % rep["content_fingerprint"][:16])
    write_json_atomic(path, obj)
    print("finalize: content_fingerprint=%s" % rep["content_fingerprint"])
    return 0


def cmd_manifest(input_dir):
    fp, files = sha256_dir(input_dir)
    out = {"input_dir": input_dir, "input_fingerprint": fp,
           "files": [{"rel": f["rel"], "size_bytes": f["size_bytes"],
                      "sha256": sha256_file(f["path"])} for f in files]}
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("skeleton", help="落盘合法结果骨架")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--work", default=DEFAULT_WORK)

    p = sub.add_parser("manifest", help="计算输入目录指纹")
    p.add_argument("--input", default=DEFAULT_INPUT)

    p = sub.add_parser("normalize", help="确定性规范化 result.json")
    p.add_argument("--input", required=True)

    p = sub.add_parser("validate", help="校验 schema 与证据完整性")
    p.add_argument("--input", required=True)

    p = sub.add_parser("finalize", help="写 runtime 与指纹")
    p.add_argument("--input", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "skeleton":
        build_skeleton(args.input, args.output, args.work)
        print("skeleton: wrote %s/result.json" % args.output.rstrip("/\\"))
        return 0
    if args.cmd == "manifest":
        return cmd_manifest(args.input)
    if args.cmd == "normalize":
        return normalize(args.input)
    if args.cmd == "validate":
        return validate(args.input)
    if args.cmd == "finalize":
        return finalize(args.input)
    return 2


if __name__ == "__main__":
    sys.exit(main())
