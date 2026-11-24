#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-viz-evidence 主控脚本（仅标准库）。

职责：
  1) 输入发现：不假设固定文件名/列名/键名，扫描 case.json、task.md、CSV/SQLite 等材料；
  2) 契约提取：从 case.json（output_contract/outputContract/contract/result_schema/
     expected_output）或 task.md 中提取，生成"结构保留、叶值置 null/[]"的回填模板；
  3) 先落盘合法骨架：init 立即写 /app/output/result.json（status=partial），任何中途
     异常都不留空产物；之后用 update 做增量改进并维护 change_log；
  4) 证据溯源：evidence add/verify 维护 evidence_map，逐条机械复核 locator+quote；
  5) check 自检：校验 result.json 结构、证据与 SVG/PDF/PNG/CSV 产物格式。

子命令：
  init       发现输入、提取契约、写 result.json 骨架与 work/plan.md
  update     以 --set / --set-key+--set-value / --log / --file 增量更新 result.json
  evidence   新增(add)/校验(verify) evidence_map 记录
  check      校验 result.json 与 /app/output 下的产物；--fail 时遇 FAIL 才非零退出

缺省参数：--input /input --output /app/output --work /app/work。任何命令退出码恒为 0
（仅 check --fail 且存在 FAIL 项时退出 1），保证 Harness 不被中断。
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evidence  # noqa: E402  证据溯源核心（locator 解析 + quote 机械复核）

MAX_FILE_BYTES = 2 * 1024 * 1024
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
RESERVED_KEYS = ("case_id", "status", "sources", "evidence_map", "figure_map",
                 "uncertainty", "change_log", "errors", "degradations",
                 "runtime", "artifacts")
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")
DATA_EXTS = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def read_text_robust(path, limit=MAX_FILE_BYTES):
    """utf-8-sig 优先，逐级降级到 gb18030/latin-1；返回 (text, encoding, error)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "unreadable: %s" % e
    if size > limit:
        return "", None, "file_too_large(%d > %d)" % (size, limit)
    last = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as e:
            last = "%s: %s" % (enc, e)
        except OSError as e:
            return "", None, "unreadable: %s" % e
    return "", None, "undecodable: " + last


def atomic_write_text(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=os.path.splitext(path)[1])
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return path


def load_json_robust(path):
    """读取 JSON；坏 JSON 不抛异常，返回 (obj, error)。"""
    text, _enc, err = read_text_robust(path)
    if err:
        return {}, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return {}, "bad_json: %s" % e


def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_json_value(v):
    v = (v or "").strip()
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def parse_set_arg(s):
    """--set 支持 'status="ok"'、'a=1' 或 JSON 对象 '{"a":1}'。"""
    s = s.strip()
    if s.startswith("{"):
        return json.loads(s)
    if "=" in s:
        k, v = s.split("=", 1)
        return {k.strip(): parse_json_value(v)}
    raise ValueError("--set 需要 key=value 或 JSON 对象，收到: %r" % s)


def deep_merge(base, extra):
    for k, v in extra.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def set_path(obj, dotted, value):
    parts = [p for p in dotted.split(".") if p]
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return obj


def get_path(obj, dotted, default=None):
    cur = obj
    for p in [p for p in dotted.split(".") if p]:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def has_path(obj, dotted):
    """契约字段是否在 result 中出现（值允许为 null/[]，只要键存在）。"""
    cur = obj
    for p in [p for p in dotted.split(".") if p]:
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    return True


def file_sha256(path, limit=MAX_FILE_BYTES):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    files = {"case_json": None, "task_md": None, "materials": [], "data": []}
    deg = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            base = name.lower()
            try:
                size = os.path.getsize(p)
            except OSError as e:
                deg.append("unreadable: %s (%s)" % (rel, e))
                continue
            if base == "case.json":
                files["case_json"] = p
            elif base == "task.md":
                files["task_md"] = p
            elif base.endswith(DATA_EXTS):
                files["data"].append(p)
            elif base.endswith(TEXT_EXTS):
                files["materials"].append(p)
            if size > MAX_FILE_BYTES:
                deg.append("file_too_large(skipped): %s (%d bytes)" % (rel, size))
    return files, deg


# --------------------------------------------------------------------------- #
# 契约提取
# --------------------------------------------------------------------------- #
CONTRACT_KEYS = ("output_contract", "outputcontract", "contract", "result_schema",
                 "resultschema", "expected_output", "expectedoutput")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_contract(obj, path=""):
    """在 JSON 对象中查找含 contract/schema 语义的字段，返回 (value, path)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kn = _norm(k)
            if kn in CONTRACT_KEYS:
                return v, (path + "." + k).strip(".")
            if isinstance(v, (dict, list)):
                found, fp = find_contract(v, (path + "." + k).strip("."))
                if found is not None:
                    return found, fp
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                found, fp = find_contract(v, "%s[%d]" % (path, i))
                if found is not None:
                    return found, fp
    return None, ""


def contract_template(value):
    """结构保留、叶值置 null/[] 的回填模板；不删未知键、不改层级。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(v, dict):
                out[k] = contract_template(v)
            elif isinstance(v, list):
                out[k] = []
            else:
                out[k] = None
        return out
    if isinstance(value, list):
        return []
    return None


def extract_contract(files):
    """从 case.json / task.md / 其他 json 材料中提取契约模板。返回 (template, source, note)。"""
    template, source, note = None, "", ""
    for cand in ([files["case_json"]] + files["materials"]):
        if not cand:
            continue
        obj, err = load_json_robust(cand)
        if err:
            note += "%s: %s; " % (os.path.basename(cand), err)
            continue
        val, fp = find_contract(obj)
        if val is not None:
            template = contract_template(val)
            source = "%s#%s" % (os.path.basename(cand), fp)
            break
    if template is None and files["task_md"]:
        text, _enc, err = read_text_robust(files["task_md"])
        if not err:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
            if m:
                try:
                    val, fp = find_contract(json.loads(m.group(1)))
                    if val is not None:
                        template = contract_template(val)
                        source = "task.md#json-fence"
                except json.JSONDecodeError:
                    note += "task.md json fence bad; "
    return template, source, note


# --------------------------------------------------------------------------- #
# 骨架构建
# --------------------------------------------------------------------------- #
def pick_case_id(case_obj, input_dir):
    for k in ("case_id", "id", "challenge_id", "challengeId"):
        if isinstance(case_obj, dict) and case_obj.get(k):
            return str(case_obj[k])
    name = os.path.basename(os.path.abspath(input_dir)) or "case"
    dig = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return "%s-%s" % (name, dig)


def build_skeleton(case_id, template):
    skel = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "evidence_map": [],
        "figure_map": [],
        "uncertainty": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {"started_at": utcnow_iso(), "finished_at": None, "seconds": None,
                    "evidence_verified_at": None},
        "artifacts": {},
    }
    if isinstance(template, dict):
        for k, v in template.items():
            if k not in RESERVED_KEYS:
                skel[k] = v
    return skel


def append_change_log(result, step, action, files=None):
    seq = 1
    for e in result.get("change_log", []):
        if isinstance(e, dict) and isinstance(e.get("seq"), int):
            seq = max(seq, e["seq"] + 1)
    result.setdefault("change_log", []).append({
        "ts": utcnow_iso(), "seq": seq, "step": step, "action": action,
        "files": list(files or []),
    })
    return result


# --------------------------------------------------------------------------- #
# 子命令实现
# --------------------------------------------------------------------------- #
def cmd_init(args):
    files, deg = discover_input(args.input)
    case_obj, case_err = load_json_robust(files["case_json"]) if files["case_json"] else ({}, "case_json_missing")
    template, contract_src, contract_note = extract_contract(files)
    case_id = pick_case_id(case_obj, args.input)
    result = build_skeleton(case_id, template)

    result["degradations"].extend(deg)
    if case_err:
        result["errors"].append("case_json: %s" % case_err)
    if contract_note:
        result["degradations"].append("contract_extract: " + contract_note.strip("; "))
    if template is None:
        result["uncertainty"].append({
            "item": "output_contract 未发现",
            "reason": "case.json/task.md 中未找到 contract/schema 字段",
            "pending_human": "output_contract 字段清单",
        })
    result["sources"].extend([
        {"locator": "case.json", "quote": case_id, "kind": "case_id"},
    ])
    append_change_log(result, "init", "init 骨架落盘; contract=%s" % (contract_src or "none"))

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    atomic_write_json(os.path.join(args.work, "contract.json"),
                      {"template": template, "source": contract_src,
                       "found": template is not None,
                       "reserved_keys": list(RESERVED_KEYS)})
    plan = ("# plan\n\n- 阶段 A: 骨架已落盘 (status=partial)\n"
            "- 阶段 B: 读数据字典→清洗口径→统计→选图→编码→标注→导出\n"
            "- 阶段 C: evidence verify + check 自检\n\n")
    atomic_write_text(os.path.join(args.work, "plan.md"), plan)
    print(json.dumps({"status": "partial", "case_id": case_id,
                      "contract_source": contract_src, "output": args.output},
                     ensure_ascii=False))
    return 0


def cmd_update(args):
    result, err = load_json_robust(os.path.join(args.output, "result.json"))
    if err:
        result = {}
    if not isinstance(result, dict):
        result = {}
    if not result:
        result = build_skeleton("unknown", None)
        result["errors"].append("update: result.json 缺失或损坏，重建最小骨架: %s" % err)
    patch = {}
    if args.set:
        try:
            patch = parse_set_arg(args.set)
        except ValueError as e:
            result.setdefault("errors", []).append(str(e))
    if args.set_key is not None:
        patch[args.set_key] = parse_json_value(args.set_value)
    if "status" in patch and patch["status"] not in STATUS_VALUES:
        result.setdefault("errors", []).append("invalid status: %r" % patch["status"])
        patch.pop("status", None)
    if patch:
        deep_merge(result, patch)
    append_change_log(result, args.step or "update", args.log or str(patch or "noop"),
                      files=args.file)
    if args.status:
        result["status"] = args.status
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    print(json.dumps({"status": result.get("status"), "change_log_seq":
                      result["change_log"][-1]["seq"] if result.get("change_log") else 0},
                     ensure_ascii=False))
    return 0


def cmd_evidence(args):
    result, err = load_json_robust(os.path.join(args.output, "result.json"))
    if err or not isinstance(result, dict):
        print(json.dumps({"error": "result.json 缺失或损坏: %s" % err}, ensure_ascii=False))
        return 0
    if args.sub == "add":
        rec = evidence.add_record(
            result.setdefault("evidence_map", []),
            claim=args.claim, claim_type=args.claim_type, locator=args.locator,
            quote=args.quote, kind=args.kind, derivation=args.derivation,
            rule=args.rule, confidence=args.confidence, verified=None)
        if args.locator and args.quote:
            srcs = result.setdefault("sources", [])
            if not any(s.get("locator") == args.locator and s.get("quote") == args.quote
                       for s in srcs if isinstance(s, dict)):
                srcs.append({"locator": args.locator, "quote": args.quote,
                             "kind": args.kind or "value"})
        append_change_log(result, "evidence", "add %s: %s" % (rec.get("id"), (args.claim or "")[:60]))
        atomic_write_json(os.path.join(args.output, "result.json"), result)
        print(json.dumps(rec, ensure_ascii=False))
        return 0
    if args.sub == "verify":
        records, issues = evidence.verify_records(result.get("evidence_map", []), args.input)
        result["evidence_map"] = records
        result["runtime"]["evidence_verified_at"] = utcnow_iso()
        n_bad = sum(1 for r in records if not r.get("verified"))
        append_change_log(result, "evidence", "verify: %d/%d 未命中" % (n_bad, len(records)))
        atomic_write_json(os.path.join(args.output, "result.json"), result)
        print(json.dumps({"total": len(records), "verified": len(records) - n_bad,
                          "unverified": n_bad, "issues": issues}, ensure_ascii=False))
        return 0
    print(json.dumps({"error": "unknown evidence subcommand: %s" % args.sub}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# check 自检
# --------------------------------------------------------------------------- #
def _read_bytes_head(path, n=8):
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return None


def _collect_keys(template, prefix="", out=None):
    if out is None:
        out = []
    if isinstance(template, dict):
        for k, v in template.items():
            kp = (prefix + "." + k).strip(".")
            out.append(kp)
            _collect_keys(v, kp, out)
    return out


def cmd_check(args):
    issues = []  # (level, code, message)
    out = args.output
    rpath = os.path.join(out, "result.json")
    result = None
    if not os.path.isfile(rpath):
        issues.append(("FAIL", "result_missing", rpath))
    else:
        result, err = load_json_robust(rpath)
        if err or not isinstance(result, dict):
            issues.append(("FAIL", "result_invalid", "result.json: %s" % err))
            result = {}
    result = result or {}

    # 结构
    if "case_id" not in result or not result.get("case_id"):
        issues.append(("FAIL", "case_id_missing", "case_id 缺失"))
    if result.get("status") not in STATUS_VALUES:
        issues.append(("FAIL", "status_invalid", "status=%r" % result.get("status")))
    for k in ("sources", "evidence_map", "figure_map", "uncertainty", "change_log"):
        if not isinstance(result.get(k), list):
            issues.append(("WARN", "reserved_not_list", "%s 应为数组" % k))

    # 契约字段
    cjson = os.path.join(args.work, "contract.json")
    contract = {}
    if os.path.isfile(cjson):
        contract, cerr = load_json_robust(cjson)
        if cerr:
            issues.append(("WARN", "contract_file_bad", cerr))
    template = contract.get("template") if isinstance(contract, dict) else None
    if template is not None:
        missing = [k for k in _collect_keys(template) if not has_path(result, k)]
        if missing:
            issues.append(("FAIL", "contract_missing", "缺少契约字段: %s" % ", ".join(missing[:12])))
    elif args.require_contract and not isinstance(contract, dict):
        issues.append(("WARN", "contract_unknown", "work/contract.json 缺失，无法核对契约字段"))

    # 证据
    if args.verify:
        if result.get("evidence_map") and args.input:
            records, vissues = evidence.verify_records(result["evidence_map"], args.input)
            result["evidence_map"] = records
            result["runtime"]["evidence_verified_at"] = utcnow_iso()
            atomic_write_json(rpath, result)
            for vi in vissues:
                issues.append(("WARN", "evidence_unverified", vi))
    em = result.get("evidence_map", [])
    for i, r in enumerate(em):
        if not isinstance(r, dict) or not r.get("locator") or not r.get("quote"):
            issues.append(("FAIL", "evidence_incomplete", "evidence_map[%d] 缺 locator/quote" % i))
        if isinstance(r, dict) and r.get("verified") is False and not args.verify:
            issues.append(("WARN", "evidence_unverified", "evidence_map[%d] verified=false" % i))
    ids = {r.get("id") for r in em if isinstance(r, dict)}
    for i, f in enumerate(result.get("figure_map", [])):
        if isinstance(f, dict) and f.get("evidence_id") and f["evidence_id"] not in ids:
            issues.append(("FAIL", "figure_map_orphan", "figure_map[%d] 指向不存在证据 %s" % (i, f.get("evidence_id"))))

    # 产物格式
    artifacts = result.get("artifacts", {})
    art_paths = []
    if isinstance(artifacts, dict):
        for k, v in artifacts.items():
            p = v.get("path") if isinstance(v, dict) else v
            if isinstance(p, str) and not os.path.isabs(p):
                p = os.path.join(out, p)
            if isinstance(p, str):
                art_paths.append((k, p))
    for k, p in art_paths:
        head = _read_bytes_head(p, 16)
        if head is None:
            issues.append(("FAIL", "artifact_missing", "%s: %s" % (k, p)))
            continue
        lp = p.lower()
        if lp.endswith(".svg"):
            try:
                ET.parse(p)
            except ET.ParseError as e:
                issues.append(("FAIL", "svg_invalid", "%s: %s" % (k, e)))
        elif lp.endswith(".pdf"):
            if not head.startswith(b"%PDF-"):
                issues.append(("FAIL", "pdf_invalid", "%s" % k))
        elif lp.endswith((".png", ".jpg", ".jpeg")):
            if head[:8] != b"\x89PNG\r\n\x1a\n" and head[:2] != b"\xff\xd8":
                issues.append(("FAIL", "raster_invalid", "%s" % k))

    fails = [i for i in issues if i[0] == "FAIL"]
    warns = [i for i in issues if i[0] == "WARN"]
    print(json.dumps({"result": "FAIL" if fails else ("WARN" if warns else "OK"),
                      "fail_count": len(fails), "warn_count": len(warns),
                      "issues": issues}, ensure_ascii=False, indent=2))
    return 1 if (args.fail and fails) else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--output", default="/app/output")
    common.add_argument("--work", default="/app/work")
    p = argparse.ArgumentParser(description="sci-viz-evidence 主控脚本", parents=[common])
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init", parents=[common], help="发现输入、提取契约、写骨架")

    up = sub.add_parser("update", parents=[common], help="增量更新 result.json")
    up.add_argument("--set", help="key=value 或 JSON 对象")
    up.add_argument("--set-key")
    up.add_argument("--set-value")
    up.add_argument("--status", choices=STATUS_VALUES)
    up.add_argument("--log", default="")
    up.add_argument("--step", default="update")
    up.add_argument("--file", action="append", default=[])

    ev = sub.add_parser("evidence", parents=[common], help="evidence_map 操作")
    ev.add_argument("sub", choices=("add", "verify"))
    ev.add_argument("--claim", default="")
    ev.add_argument("--claim-type", default="value")
    ev.add_argument("--locator", default="")
    ev.add_argument("--quote", default="")
    ev.add_argument("--kind", default="value")
    ev.add_argument("--derivation", default="")
    ev.add_argument("--rule", default="")
    ev.add_argument("--confidence", default="certain")

    ch = sub.add_parser("check", parents=[common], help="自检")
    ch.add_argument("--require-contract", action="store_true")
    ch.add_argument("--verify", action="store_true")
    ch.add_argument("--fail", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "update":
            return cmd_update(args)
        if args.cmd == "evidence":
            return cmd_evidence(args)
        if args.cmd == "check":
            return cmd_check(args)
        build_parser().print_help()
        return 0
    except Exception as e:  # 任何异常也不中断 Harness
        print(json.dumps({"error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
