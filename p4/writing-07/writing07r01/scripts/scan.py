#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan.py — 扫描输入目录，发现材料、抽取合同字段与 case_id，写入 input_manifest.json。

用法:
    python3 -B scripts/scan.py --input /input --work /app/work
缺省: --input /input --work /app/work
仅用标准库，确定性输出（固定排序，无随机成分）。
"""
import argparse
import json
import os
import sys

TEXT_EXTS = {
    ".md", ".txt", ".json", ".jsonl", ".tex", ".latex", ".bib", ".csv", ".tsv",
    ".yaml", ".yml", ".rst", ".html", ".htm", ".xml", ".markdown", ".mmd",
}
MAX_BYTES = 2 * 1024 * 1024  # 2MB
CONTRACT_KEYS_PRIORITY = [
    "output_contract", "output", "expected_output", "fields",
    "required_fields", "contract",
]
CASE_ID_KEYS = ["case_id", "caseId", "id"]
SCHEMA_KEYS = {"type", "description", "required", "items", "properties",
               "enum", "default", "title", "format", "minLength", "maxLength"}


def log(msg):
    sys.stderr.write(msg + "\n")


def read_text_file(path):
    """返回 (text, encoding, size, status)。binary/oversize/unreadable 返回 (None, None, size, status)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, None, 0, "unreadable: %s" % e
    if size > MAX_BYTES:
        return None, None, size, "oversize"
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return None, None, size, "unreadable: %s" % e
    if b"\x00" in data[:1024]:
        return None, None, size, "binary"
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc), enc, size, "ok"
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace", size, "repaired"


def find_key_values(obj, path=""):
    """递归产出 (path, value) 对，用于查找嵌套键。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = path + "/" + k if path else k
            yield p, v
            yield from find_key_values(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from find_key_values(v, "%s[%d]" % (path, i))


def looks_like_schema(v):
    if not isinstance(v, dict):
        return False
    keys = set(v.keys())
    if not keys:
        return False
    return keys.issubset(SCHEMA_KEYS)


def default_for(v):
    """最佳努力为合同字段生成缺省值：leaf->null，列表->[]，对象->按子键结构，schema 按 type 判定。"""
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        if looks_like_schema(v):
            t = v.get("type")
            if t == "array":
                return []
            if t == "object":
                props = v.get("properties")
                if isinstance(props, dict):
                    return {k: default_for(vv) for k, vv in props.items()}
                return {}
            return None
        return {k: default_for(vv) for k, vv in v.items()}
    return None


def normalize_contract(raw):
    """返回 (fields, defaults)。fields 为顶层字段名列表，defaults 为字段->缺省值。"""
    if raw is None:
        return [], {}
    if isinstance(raw, dict):
        fields = [k for k in raw.keys()]
        defaults = {k: default_for(v) for k, v in raw.items()}
        return fields, defaults
    if isinstance(raw, list):
        fields = [str(x) for x in raw if isinstance(x, str)]
        defaults = {f: None for f in fields}
        return fields, defaults
    # 字符串/数字等：单字段
    fields = [str(raw)]
    return fields, {str(raw): None}


def extract_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for k in CASE_ID_KEYS:
        if k in obj and obj[k] is not None:
            return obj[k]
    # 兜底：递归查找首个 case_id
    for _, v in find_key_values(obj):
        if isinstance(v, dict) and "case_id" in v and v["case_id"] is not None:
            return v["case_id"]
    return None


def extract_contract(obj):
    """按优先级抽取第一个命中的 output_contract。"""
    found = {}
    for path, v in find_key_values(obj):
        key = path.rstrip("/").split("/")[-1]
        key = key.split("[")[0]  # 去掉列表索引
        if key in CONTRACT_KEYS_PRIORITY:
            found.setdefault(key, v)
    for k in CONTRACT_KEYS_PRIORITY:
        if k in found:
            return k, found[k]
    return None, None


def build_manifest(input_dir):
    errors = []
    degradations = []
    files = []
    if not os.path.exists(input_dir):
        return {
            "input_dir": input_dir,
            "files": files,
            "case_id": None,
            "contract_key": None,
            "contract_fields": [],
            "contract_defaults": {},
            "errors": [{"item": "input_dir", "reason": "input dir does not exist"}],
            "degradations": degradations,
        }

    for root, dirs, names in os.walk(input_dir):
        dirs.sort()
        names.sort()
        for name in names:
            full = os.path.join(root, name)
            if os.path.islink(full):
                degradations.append({"item": full, "reason": "symlink skipped"})
                continue
            rel = os.path.relpath(full, input_dir).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            is_text_candidate = ext in TEXT_EXTS or name in {
                "case", "task", "notes", "paper", "materials", "manuscript",
                "abstract", "draft", "facts", "notes",
            }
            text, enc, size, status = read_text_file(full)
            if status == "oversize":
                degradations.append({"item": rel, "reason": "oversize >2MB skipped"})
                files.append({"path": full, "relpath": rel, "size": size,
                              "kind": "file", "encoding": None, "status": "oversize"})
                continue
            if status == "binary" or status.startswith("unreadable"):
                kind = "pdf" if ext == ".pdf" else "binary"
                files.append({"path": full, "relpath": rel, "size": size,
                              "kind": kind, "encoding": None,
                              "status": status, "needs_parse": ext == ".pdf"})
                if status.startswith("unreadable"):
                    degradations.append({"item": rel, "reason": status})
                continue
            if status == "repaired":
                degradations.append({"item": rel, "reason": "encoding repaired"})
            entry = {"path": full, "relpath": rel, "size": size,
                     "kind": "text", "encoding": enc, "status": status,
                     "preview": text[:400] if text else ""}
            files.append(entry)

    # 定位 case.json 与 task.md
    case_json = None
    task_md = None
    for f in files:
        base = os.path.basename(f["relpath"])
        if base == "case.json":
            case_json = f
        elif base == "task.md":
            task_md = f

    case_id = None
    contract_key = None
    contract_fields = []
    contract_defaults = {}
    case_obj = None
    if case_json:
        text, enc, size, status = read_text_file(case_json["path"])
        if status == "ok" or status == "repaired":
            try:
                case_obj = json.loads(text)
            except (json.JSONDecodeError, TypeError) as e:
                errors.append({"item": "case.json", "reason": "bad JSON: %s" % e})
                case_json["json_status"] = "bad_json"
        else:
            errors.append({"item": "case.json", "reason": "unreadable: %s" % status})
            case_json["json_status"] = "unreadable"
    else:
        degradations.append({"item": "case.json", "reason": "not found"})

    if case_obj is not None:
        case_id = extract_case_id(case_obj)
        contract_key, raw = extract_contract(case_obj)
        contract_fields, contract_defaults = normalize_contract(raw)

    return {
        "input_dir": input_dir,
        "files": files,
        "case_json_found": case_json is not None,
        "task_md_found": task_md is not None,
        "case_id": case_id,
        "contract_key": contract_key,
        "contract_fields": contract_fields,
        "contract_defaults": contract_defaults,
        "errors": errors,
        "degradations": degradations,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="scan input dir and emit manifest")
    p.add_argument("--input", default="/input")
    p.add_argument("--work", default="/app/work")
    args = p.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    manifest = build_manifest(args.input)

    # case_normalized.json：Agent 据此回填合同字段
    normalized = {
        "case_id": manifest["case_id"],
        "contract_key": manifest["contract_key"],
        "contract_fields": manifest["contract_fields"],
        "contract_defaults": manifest["contract_defaults"],
    }

    def dump(path, obj):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")

    dump(os.path.join(args.work, "input_manifest.json"), manifest)
    dump(os.path.join(args.work, "case_normalized.json"), normalized)

    log("scan complete: files=%d case_id=%s contract_fields=%d errors=%d"
        % (len(manifest["files"]), manifest["case_id"],
           len(manifest["contract_fields"]), len(manifest["errors"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
