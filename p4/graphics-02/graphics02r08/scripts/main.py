#!/usr/bin/env python3
"""scix-graphics 入口：输入发现 -> 契约抽取 -> result.json 骨架 -> 表格解析 -> 自检。

纯标准库实现。运行时默认 /input -> /app/output、/app/work，
可用环境变量 INPUT_DIR / OUTPUT_DIR / WORK_DIR 覆盖。
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MAX_FILE_SIZE = 2 * 1024 * 1024  # 单文件上限，超限跳过并记录

STATUS_OK = {"complete", "partial", "failed", "in_progress"}


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    """按 utf-8-sig 读取并容错坏编码。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return None
    return None


def read_json_robust(path):
    """坏 JSON 返回 None，不崩溃。"""
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        # 尝试截断到最后一个完整对象（常见尾部损坏）
        for cut in range(len(text) - 1, -1, -1):
            if text[cut] in "}]":
                try:
                    return json.loads(text[: cut + 1])
                except (ValueError, TypeError):
                    continue
        return None


def discover_input(input_dir):
    """扫描输入目录，不假设固定文件名；超限文件跳过。"""
    inventory = {"files": [], "skipped": []}
    if not os.path.isdir(input_dir):
        inventory["error"] = "input_dir_missing: %s" % input_dir
        return inventory
    try:
        names = sorted(os.listdir(input_dir))
    except OSError as e:
        inventory["error"] = "listdir_failed: %s" % e
        return inventory
    for name in names:
        p = os.path.join(input_dir, name)
        if os.path.isdir(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size > MAX_FILE_SIZE:
            inventory["skipped"].append({"name": name, "reason": "size>%d" % MAX_FILE_SIZE})
            continue
        inventory["files"].append({
            "name": name,
            "path": p,
            "size": size,
            "ext": os.path.splitext(name)[1].lower(),
        })
    return inventory


def extract_case(case_path, input_dir):
    """抽取 case_id 与 output_contract；坏 JSON 降级为空契约。"""
    data = read_json_robust(case_path) if os.path.exists(case_path) else None
    if not isinstance(data, dict):
        data = {}
    case_id = data.get("case_id") or data.get("id") or data.get("caseId") or ""
    contract = data.get("output_contract") or data.get("outputContract") or {}
    if isinstance(contract, list):
        keys = []
        for item in contract:
            if isinstance(item, dict):
                k = item.get("name") or item.get("field") or item.get("key") or ""
                if k:
                    keys.append(k)
        contract = {k: None for k in keys}
    if not isinstance(contract, dict):
        contract = {}
    return case_id, contract, data


def build_skeleton(case_id, contract, inventory, out_dir, work_dir, force=False):
    """落盘 result.json 骨架（幂等；非骨架结果存在且非 --force 时不覆盖）。"""
    result_path = os.path.join(out_dir, "result.json")
    existing = read_json_robust(result_path)
    if existing and not force:
        if existing.get("case_id") and existing.get("status") in {"complete", "partial", "failed"}:
            # 已有一版真实结果，不覆盖，仅返回
            return existing, False
    # 契约字段全部回填为 null（保留键，不改名不删除）
    contract_out = {}
    for k in contract:
        contract_out[k] = None
    result = {
        "case_id": case_id or None,
        "output_contract": contract_out,
        "status": "partial" if case_id else "failed",
        "products": [],
        "sources": {
            "input_dir": "/input",
            "files": [f["name"] for f in inventory.get("files", [])],
            "parsers": ["stdlib-csv/tsv/md/whitespace", "pdf-text-tool/ocr-tool (由 Agent 调用)"],
            "scripts": ["scripts/main.py", "scripts/figure.py", "scripts/selftest.py"],
            "export_params": {"png_dpi": 300, "color_space": "RGB", "background": "white"},
        },
        "uncertainty": [],
        "pending_human": [],
        "annotation_checks": [],
        "change_log": [{
            "step": "S0_skeleton",
            "time_utc": utcnow(),
            "status": "partial" if case_id else "failed",
            "note": "骨架落盘；契约字段待生产，先置 null。case_id=%r" % (case_id or None),
        }],
    }
    if not case_id:
        result["uncertainty"].append("case.json 缺 case_id/id，无法建立输入标识")
    if not contract:
        result["uncertainty"].append("case.json 无 output_contract，result.json 只含通用字段")
    if inventory.get("error"):
        result["uncertainty"].append("输入目录异常: %s" % inventory["error"])
    if inventory.get("skipped"):
        result["uncertainty"].append("超限跳过文件: %s" %
                                     ", ".join(s["name"] for s in inventory["skipped"]))
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(work_dir, "contract.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": case_id or None,
                   "output_contract": contract_out,
                   "raw_keys": sorted(contract.keys())}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(work_dir, "inventory.json"), "w", encoding="utf-8") as f:
        json.dump(inventory, f, ensure_ascii=False, indent=2)
    return result, True


# ---------------------------------------------------------------- table parsing

def sniff_delimiter(text):
    first = text.splitlines()[0] if text else ""
    if "," in first and "\t" not in first:
        return ","
    if "\t" in first:
        return "\t"
    if first.strip().startswith("|"):
        return "md"
    return "ws"


def infer_cell(v):
    if v is None:
        return "empty"
    s = str(v).strip()
    if s == "" or s.lower() in {"na", "n/a", "nan", "null", "-", "—", "none"}:
        return "empty"
    try:
        int(s)
        return "int"
    except ValueError:
        pass
    try:
        float(s.replace(",", ""))
        return "float"
    except ValueError:
        pass
    return "str"


def parse_table(text, name):
    delim = sniff_delimiter(text)
    rows = []
    if delim == "md":
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                rows.append(cells)
        if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in rows[1] if c.strip()):
            rows.pop(1)  # markdown 分隔行
        header = rows[0] if rows else []
        body = rows[1:] if len(rows) > 1 else []
    else:
        try:
            reader = csv.reader(io.StringIO(text), delimiter=delim if delim != "ws" else None,
                                skipinitialspace=True)
            rows = [r for r in reader]
        except (csv.Error, ValueError):
            rows = [re.split(r"\s{2,}|\t", line.strip()) for line in text.splitlines() if line.strip()]
        # 空白分隔需要进一步按单空格切分
        if delim == "ws":
            rows = [re.split(r"\s+", line.strip()) for line in text.splitlines() if line.strip()]
        header = rows[0] if rows else []
        body = rows[1:] if len(rows) > 1 else []
    if not header:
        return None
    cols = []
    for j, h in enumerate(header):
        vals = [r[j] if j < len(r) else "" for r in body]
        types = [infer_cell(v) for v in vals]
        nonempty = [v for v in vals if infer_cell(v) != "empty"]
        numeric = [v for v in vals if infer_cell(v) in {"int", "float"}]
        coltype = "numeric" if (len(numeric) >= max(1, 0.8 * len(nonempty))) else "string"
        cols.append({
            "index": j, "name": h or "col%d" % j, "type": coltype,
            "n": len(nonempty), "missing": sum(1 for t in types if t == "empty"),
            "sample": nonempty[:5],
        })
    # 量级/单位疑点检查：同一数值列内跨度超过 1e4 或出现负值+大正值的混合
    issues = []
    for c in cols:
        if c["type"] == "numeric":
            vals = [float(str(v).replace(",", "")) for v in c["sample"]]
            if len(vals) >= 2:
                mx, mn = max(vals), min(vals)
                if mx != 0 and mn != 0 and abs(mx / mn) > 1e4:
                    issues.append("magnitude_spread %s: max=%.3g min=%.3g (量级疑点，核对单位)" % (c["name"], mx, mn))
                if mn < 0 < mx:
                    issues.append("sign_mixed %s: 正负值混合，核对是否应为对数/变化量" % c["name"])
    numeric_cols = [c["name"] for c in cols if c["type"] == "numeric"]
    str_cols = [c["name"] for c in cols if c["type"] == "string"]
    mapping_hint = {
        "x_candidates": str_cols[:1],
        "y_candidates": numeric_cols,
        "error_candidates": [c for c in numeric_cols if re.search(r"sd|sem|ci|err|std|±", c, re.I)],
        "note": "首选第一个字符串列为分组(x)，数值列为 y；误差列按列名匹配 SD/SEM/CI/err",
    }
    return {"name": name, "header": header, "rows": body, "columns": cols,
            "issues": issues, "mapping_hint": mapping_hint}


def extract_all_tables(inventory, input_dir, work_dir):
    tables = []
    for f in inventory.get("files", []):
        if f["ext"] in {".csv", ".tsv", ".txt", ".dat", ".md", ".tab"}:
            text = read_text(f["path"])
            if text is None:
                continue
            if len(text) > MAX_FILE_SIZE:
                continue
            t = parse_table(text, f["name"])
            if t:
                t["source_file"] = f["name"]
                tables.append(t)
    # 也尝试从 task.md 抽取 markdown 表格
    for f in inventory.get("files", []):
        if f["name"].lower() in {"task.md", "case.md", "notes.md"}:
            text = read_text(f["path"])
            if not text:
                continue
            m = re.findall(r"(?s)\|.*\|(?:\r?\n\|.*\|)+", text)
            for i, block in enumerate(m):
                t = parse_table(block, "%s#table%d" % (f["name"], i + 1))
                if t:
                    t["source_file"] = f["name"]
                    tables.append(t)
    with open(os.path.join(work_dir, "tables.json"), "w", encoding="utf-8") as fp:
        json.dump(tables, fp, ensure_ascii=False, indent=2)
    return tables


# ---------------------------------------------------------------- incremental update

def apply_set(result, set_items, log_items, status):
    for item in set_items:
        if "=" not in item:
            continue
        k, _, v = item.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        # 支持点路径 a.b.c
        if "." in k:
            obj = result
            parts = k.split(".")
            for p in parts[:-1]:
                if not isinstance(obj.get(p), dict):
                    obj[p] = {}
                obj = obj[p]
            obj[parts[-1]] = _coerce(v)
        else:
            result[k] = _coerce(v)
    if status:
        if status in STATUS_OK:
            result["status"] = status
    for note in log_items:
        result.setdefault("change_log", []).append({
            "step": "incremental", "time_utc": utcnow(), "status": result.get("status"), "note": note,
        })
    return result


def _coerce(v):
    if v in {"null", "None", "none", ""}:
        return None
    if v in {"true", "True"}:
        return True
    if v in {"false", "False"}:
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    if v.startswith("[") and v.endswith("]"):
        try:
            return json.loads(v)
        except ValueError:
            pass
    if v.startswith("{") and v.endswith("}"):
        try:
            return json.loads(v)
        except ValueError:
            pass
    return v


def main():
    ap = argparse.ArgumentParser(description="scix-graphics 入口")
    ap.add_argument("--input", default=os.environ.get("INPUT_DIR", "/input"))
    ap.add_argument("--case", default=None)
    ap.add_argument("--out", default=os.environ.get("OUTPUT_DIR", "/app/output"))
    ap.add_argument("--work", default=os.environ.get("WORK_DIR", "/app/work"))
    ap.add_argument("--extract", action="store_true", help="仅解析表格并写出 tables.json")
    ap.add_argument("--force", action="store_true", help="强制重建骨架")
    ap.add_argument("--skip-check", action="store_true")
    ap.add_argument("--set", action="append", default=[], dest="set_items", help="key=value 增量写入 result.json")
    ap.add_argument("--log", action="append", default=[], dest="log_items", help="追加 change_log 条目")
    ap.add_argument("--status", default=None, help="更新 status")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    case_path = args.case or os.path.join(args.input, "case.json")
    result_path = os.path.join(args.out, "result.json")

    # 增量更新模式：先读已有结果，否则新建
    result = read_json_robust(result_path)
    if not isinstance(result, dict):
        result = {}

    inventory = discover_input(args.input)
    case_id, contract, raw_case = extract_case(case_path, args.input)

    if args.set_items or args.log_items or args.status:
        if not result.get("case_id") and case_id:
            result["case_id"] = case_id
        result = apply_set(result, args.set_items, args.log_items, args.status)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps({"ok": True, "mode": "update", "case_id": result.get("case_id"),
                          "status": result.get("status")}, ensure_ascii=False))
        return 0

    # 全量流水线
    result, created = build_skeleton(case_id, contract, inventory, args.out, args.work, args.force)
    tables = extract_all_tables(inventory, args.input, args.work)

    # 回填骨架 sources 中的表格信息
    if tables:
        result["sources"]["tables"] = [{"file": t["source_file"], "columns": len(t["columns"]),
                                        "issues": t["issues"]} for t in tables]
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 自检
    report = None
    if not args.skip_check:
        selftest = os.path.join(HERE, "selftest.py")
        if os.path.exists(selftest):
            import subprocess
            r = subprocess.run([sys.executable, selftest, "--result", result_path, "--work", args.work],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            report = {"rc": r.returncode, "stdout": (r.stdout or "")[-800:], "stderr": (r.stderr or "")[-800:]}
            if r.stdout:
                sys.stdout.write(r.stdout)
    summary = {
        "ok": True,
        "mode": "pipeline",
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "contract_fields": len(result.get("output_contract", {})),
        "files_found": len(inventory.get("files", [])),
        "tables_found": len(tables),
        "selftest": report,
        "created": created,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
