#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化 Skill 主控脚本（仅标准库）。

职责：
  1) 发现输入：不假设固定文件名/列名/键名，扫描 case.json、task.md、CSV/SQLite 材料；
  2) 提取 output_contract：从 case.json（output_contract/outputContract/contract/
     result_schema/expected_output）或 task.md 提取，叶值置 null/[] 作回填模板；
  3) 先落盘合法骨架：init 立即写 /app/output/result.json（status=partial）；
     之后用 update 增量改进并维护 change_log；
  4) check 自检：校验 result.json 结构与 SVG/PDF/PNG/CSV 产物格式。

子命令：
  init     发现输入、提取契约、写 result.json 骨架与 work/plan.md、work/contract.json
  update   以 --set / --set-key+--set-value / --status / --log 增量更新 result.json
  check    校验 result.json 与 /app/output 产物；--fail 时遇 FAIL 才非零退出

任何命令退出码恒为 0（仅 check --fail 且存在 FAIL 项时退出 1），保证 Harness 不被中断。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件读取上限 2MB
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
RESERVED_KEYS = ("case_id", "status", "sources", "uncertainty", "change_log",
                 "errors", "degradations", "runtime", "artifacts")
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")
DATA_EXTS = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def read_text_robust(path, limit=MAX_FILE_BYTES):
    """utf-8-sig 优先，逐级降级 gb18030/latin-1；返回 (text, encoding, error)。"""
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
    """--set 支持 'status="ok"' 或 JSON 对象 '{"a":1,"b.c":2}'。"""
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


def find_contract(obj, path=""):
    """递归查找契约字段，返回 (value, path)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if kl in CONTRACT_KEYS and (isinstance(v, (dict, list))):
                return v, (path + "." + k).lstrip(".")
            r, p = find_contract(v, path + "." + k)
            if r is not None:
                return r, p
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            r, p = find_contract(v, "%s[%d]" % (path, i))
            if r is not None:
                return r, p
    return None, None


def leaf_value(node):
    """契约叶值的空占位：数组语义给 []，否则 null。"""
    if isinstance(node, dict):
        t = str(node.get("type", "")).lower()
        if "array" in t or "list" in t or "items" in node:
            return []
        return None
    if isinstance(node, list):
        return []
    if isinstance(node, str):
        s = node.lower()
        return [] if ("array" in s or "list" in s) else None
    return None


def to_template(node):
    """契约结构原样保留，叶值置 null/[]。"""
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):  # JSON schema 对象
            return {k: to_template(v) for k, v in props.items()}
        if "type" in node or "items" in node or "enum" in node:  # 叶规格
            return leaf_value(node)
        return {k: to_template(v) for k, v in node.items()}
    if isinstance(node, list):
        out = {}
        for it in node:
            if isinstance(it, str):
                out[it] = None
            elif isinstance(it, dict):
                nm = it.get("name") or it.get("key") or it.get("field") or it.get("title")
                tpl = to_template(it)
                if nm:
                    out[nm] = tpl
                elif isinstance(tpl, dict):
                    out.update(tpl)
            else:
                out[str(it)] = None
        return out
    return leaf_value(node)


def extract_contract_from_text(text):
    """从 task.md 文本中尝试提取 JSON 契约块。"""
    if not text:
        return None, None
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        v, p = find_contract(obj)
        if v is not None:
            return v, "task.md"
    for key in CONTRACT_KEYS:
        if re.search(re.escape(key), text, re.I):
            return {}, "task.md:contract_keyword_only"
    return None, None


MINIMAL_CONTRACT = {"summary": None, "figure": None, "statistics": None,
                    "cleaning": None, "data": None}


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
def compute_case_id(case_obj, input_dir):
    for k in ("case_id", "id", "challenge_id"):
        if isinstance(case_obj, dict) and case_obj.get(k):
            return str(case_obj[k])
    for f in ("case.json", "task.md"):
        p = os.path.join(input_dir, f)
        if os.path.isfile(p):
            text, _e, _r = read_text_robust(p)
            h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
            return os.path.basename(os.path.abspath(input_dir)) + "-" + h
    return os.path.basename(os.path.abspath(input_dir)) or "unknown"


def cmd_init(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    files, deg = discover_input(args.input)

    case_obj, case_err = {}, None
    if files["case_json"]:
        case_obj, case_err = load_json_robust(files["case_json"])

    contract, contract_path = None, None
    if isinstance(case_obj, dict):
        contract, contract_path = find_contract(case_obj)
    if contract is None and files["task_md"]:
        text, _e, _r = read_text_robust(files["task_md"])
        contract, contract_path = extract_contract_from_text(text)
    if contract is None:
        contract = MINIMAL_CONTRACT
        contract_path = "fallback:minimal"

    template = to_template(contract) if contract is not None else {}
    template = template if isinstance(template, dict) else {}

    case_id = compute_case_id(case_obj, args.input)
    result = {"case_id": case_id, "status": "partial"}
    for k in RESERVED_KEYS:
        result.setdefault(k, [] if k in ("sources", "uncertainty", "change_log",
                                         "errors", "degradations", "artifacts") else None)
    result["runtime"] = {"started_at": utcnow_iso(), "seconds": None, "finished_at": None}
    # 契约字段回填（保留键优先以本 Skill 语义为准）
    for k, v in template.items():
        if k not in RESERVED_KEYS:
            result[k] = v
    result["change_log"] = [{
        "ts": utcnow_iso(), "seq": 1, "step": "init",
        "action": "落盘 partial 结果骨架", "files": ["result.json", "work/plan.md"],
    }]
    if deg:
        result["degradations"] = deg
    if case_err:
        result["errors"] = [case_err]
    if contract_path and contract_path.startswith("fallback"):
        result["uncertainty"] = [{"item": "output_contract", "reason": "未找到契约",
                                  "pending_human": True}]
    if isinstance(case_obj, dict) and set(case_obj) & set(RESERVED_KEYS):
        result["uncertainty"] = (result.get("uncertainty") or []) + [
            {"item": "reserved_keys", "reason": "契约与保留键重名，以本 Skill 语义为准",
             "pending_human": False}]

    atomic_write_json(os.path.join(args.output, "result.json"), result)
    atomic_write_json(os.path.join(args.work, "contract.json"),
                      {"path": contract_path, "contract": contract, "template": template})
    plan = ["# plan", "- 阶段A: init 完成，骨架已落盘", "- 阶段B: inspect + 清洗/统计/选图/plot",
            "- 阶段C: check + 补齐证据/uncertainty"]
    plan.append("")
    plan.append("contract_path: %s" % contract_path)
    plan.append("case_json: %s" % files["case_json"])
    plan.append("task_md: %s" % files["task_md"])
    plan.append("data_files: %s" % json.dumps(files["data"], ensure_ascii=False))
    atomic_write_text(os.path.join(args.work, "plan.md"), "\n".join(plan) + "\n")
    atomic_write_json(os.path.join(args.work, "inventory.json"),
                      {"files": {k: v for k, v in files.items()},
                       "degradations": deg, "case_id": case_id})
    print(json.dumps({"case_id": case_id, "status": "partial",
                      "contract_path": contract_path,
                      "result": os.path.join(args.output, "result.json")},
                     ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# update
# --------------------------------------------------------------------------- #
def cmd_update(args):
    path = os.path.join(args.output, "result.json")
    result, err = load_json_robust(path)
    if err:
        result = {"case_id": "unknown", "status": "partial", "errors": [err]}
    result.setdefault("change_log", [])

    if args.set:
        for k, v in parse_set_arg(args.set).items():
            if "." in k:
                set_path(result, k, v)
            else:
                result[k] = v
    if args.set_key:
        set_path(result, args.set_key, parse_json_value(args.set_value if args.set_value is not None else "null"))
    if args.status:
        if args.status not in STATUS_VALUES:
            args.status = "partial"
        result["status"] = args.status

    seq = max([x.get("seq", 0) for x in result["change_log"]] + [0]) + 1
    files = [f.strip() for f in (args.files or "").split(",") if f.strip()]
    result["change_log"].append({
        "ts": utcnow_iso(), "seq": seq, "step": args.step or "update",
        "action": args.log or (args.set_key and "set %s" % args.set_key) or "incremental update",
        "files": files,
    })
    result["runtime"] = result.get("runtime") or {}
    result["runtime"]["updated_at"] = utcnow_iso()

    atomic_write_json(path, result)
    print(json.dumps({"ok": True, "seq": seq, "status": result.get("status")},
                     ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def check_artifact(p, issues):
    if not os.path.isfile(p):
        issues.append(("FAIL", "artifact_missing", p))
        return
    base = os.path.basename(p).lower()
    try:
        with open(p, "rb") as fh:
            head = fh.read(8)
    except OSError as e:
        issues.append(("FAIL", "artifact_unreadable", "%s (%s)" % (p, e)))
        return
    if base.endswith(".svg"):
        try:
            import xml.etree.ElementTree as ET
            ET.parse(p)
        except Exception as e:
            issues.append(("FAIL", "svg_invalid", "%s (%s)" % (p, e)))
    elif base.endswith(".pdf"):
        if not head.startswith(b"%PDF"):
            issues.append(("FAIL", "pdf_invalid", p))
    elif base.endswith(".png"):
        if not head.startswith(b"\x89PNG"):
            issues.append(("FAIL", "png_invalid", p))
    elif base.endswith(".json"):
        obj, err = load_json_robust(p)
        if err:
            issues.append(("FAIL", "json_invalid", "%s (%s)" % (p, err)))


def cmd_check(args):
    path = os.path.join(args.output, "result.json")
    issues = []
    if not os.path.isfile(path):
        issues.append(("FAIL", "result_missing", path))
    else:
        result, err = load_json_robust(path)
        if err:
            issues.append(("FAIL", "result_invalid_json", err))
            result = {}
        if not result.get("case_id"):
            issues.append(("FAIL", "case_id_missing", path))
        if result.get("status") not in STATUS_VALUES:
            issues.append(("FAIL", "status_invalid", str(result.get("status"))))
        for key in ("sources", "uncertainty", "change_log"):
            if not isinstance(result.get(key), list):
                issues.append(("FAIL", "missing_list_%s" % key, path))
        if args.require_contract:
            cpath = os.path.join(args.work, "contract.json")
            cobj, cerr = load_json_robust(cpath)
            if cerr:
                issues.append(("WARN", "contract_file_unreadable", cerr))
            else:
                template = cobj.get("template") or {}
                if isinstance(template, dict):
                    for k in template:
                        if k not in RESERVED_KEYS and k not in result:
                            issues.append(("FAIL", "contract_field_missing", k))
        for art in (result.get("artifacts") or []):
            p = art.get("path") if isinstance(art, dict) else art
            if isinstance(p, str) and p:
                check_artifact(p, issues)

    fails = [i for i in issues if i[0] == "FAIL"]
    for level, code, detail in issues:
        print("%s %s %s" % (level, code, detail))
    print("SUMMARY passes=%d fails=%d warns=%d" % (
        sum(1 for i in issues if i[0] == "PASS"), len(fails),
        sum(1 for i in issues if i[0] == "WARN")))
    if args.fail and fails:
        return 1
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(description="科研数据可视化 Skill 主控脚本")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_io(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("init", help="发现输入、提取契约、写骨架")
    add_io(sp)

    sp = sub.add_parser("update", help="增量更新 result.json")
    add_io(sp)
    sp.add_argument("--set", help='key=value 或 JSON 对象，如 status="ok"')
    sp.add_argument("--set-key")
    sp.add_argument("--set-value")
    sp.add_argument("--status", choices=STATUS_VALUES)
    sp.add_argument("--log")
    sp.add_argument("--step")
    sp.add_argument("--files")

    sp = sub.add_parser("check", help="自检 result.json 与产物")
    add_io(sp)
    sp.add_argument("--require-contract", action="store_true")
    sp.add_argument("--fail", action="store_true", help="存在 FAIL 时退出码 1")

    args = p.parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "update":
            return cmd_update(args)
        if args.cmd == "check":
            return cmd_check(args)
        p.error("未知子命令: %s" % args.cmd)
    except Exception as e:  # 任何异常不崩溃，退出码 0
        sys.stderr.write("ERROR(handled): %s\n" % e)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
