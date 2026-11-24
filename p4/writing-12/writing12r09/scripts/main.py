#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — LaTeX 写作 Skill 的引导、回填、自检与证据定位（仅标准库）。

职责：
  1. 扫描 /input，发现 case.json / task.md 与材料文件；
  2. 抽取 case_id 与 output_contract 字段清单；
  3. 立即写一版合法 /app/output/result.json 骨架（先落盘，再增量改进）；
  4. finalize / selfcheck 校验 result.json 满足输出契约；
  5. evidence index/quote 建立材料定位与逐字引用。

非致命失败一律退出码 0；selfcheck 发现问题时退出码 1。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")
BASE_LIST_FIELDS = ("sources", "uncertainty", "pending_human", "change_log",
                    "errors", "degradations", "limitations")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reconfigure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def read_text(path):
    """Read UTF-8 text tolerating BOM/encoding issues. Returns (text, error)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds 2MiB"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def read_json(path):
    """Tolerant JSON read. Returns (obj, errors_list)."""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[:exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def discover_input(root):
    files = []
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            files.append({
                "path": full,
                "rel": os.path.relpath(full, root).replace("\\", "/"),
                "size": size,
                "ext": ext,
                "is_text": ext in TEXT_EXTS,
            })
    return files


def find_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    return None


def contract_keys(obj):
    """Return output_contract keys to echo back verbatim, preserving order."""
    if not isinstance(obj, dict):
        return []
    contract = obj.get("output_contract")
    keys = []
    if isinstance(contract, dict):
        keys = list(contract.keys())
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                keys.append(item)
            elif isinstance(item, dict):
                keys.extend(list(item.keys()))
    return keys


def build_skeleton(root):
    """Build result.json skeleton from /input/case.json. Returns (skeleton, keys)."""
    case_path = os.path.join(root, "case.json")
    notes, case_id, keys, parsed_ok = [], None, [], False
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        parsed_ok = obj is not None
        case_id = find_case_id(obj)
        keys = contract_keys(obj)
        if parsed_ok and not keys:
            notes.append("output_contract_not_found: metadata-only result")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    status = "partial" if parsed_ok else "input_error"
    skeleton = {"case_id": case_id}
    for key in keys:
        skeleton[key] = None
    skeleton.update({
        "status": status,
        "artifacts": [],
        "main_artifact": None,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utcnow(),
            "step": "bootstrap",
            "note": "result skeleton written before analysis; contract fields default to null",
        }],
        "errors": notes,
        "degradations": [],
        "limitations": [],
        "runtime": {"started_at": utcnow(), "producer": "main.py"},
    })
    return skeleton, keys


def _ensure_base(obj):
    if not isinstance(obj.get("artifacts"), list):
        obj["artifacts"] = []
    if "main_artifact" not in obj:
        obj["main_artifact"] = None
    for field in BASE_LIST_FIELDS:
        if not isinstance(obj.get(field), list):
            obj[field] = []
    if not isinstance(obj.get("runtime"), dict):
        obj["runtime"] = {}
    return obj


def _load_contract_keys(args):
    inv = os.path.join(args.work, "inventory.json")
    if os.path.isfile(inv):
        obj, _ = read_json(inv)
        if isinstance(obj, dict) and isinstance(obj.get("contract_keys"), list):
            return obj["contract_keys"]
    case_path = os.path.join(args.input, "case.json")
    if os.path.isfile(case_path):
        obj, _ = read_json(case_path)
        if isinstance(obj, dict):
            return contract_keys(obj)
    return []


# ---------------- 子命令 ----------------

def cmd_bootstrap(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    skeleton, keys = build_skeleton(args.input)
    write_json(os.path.join(args.output, "result.json"), skeleton)
    inventory = {
        "input_root": args.input,
        "contract_keys": keys,
        "files": discover_input(args.input),
        "generated_at": utcnow(),
    }
    write_json(os.path.join(args.work, "inventory.json"), inventory)
    print(json.dumps({
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_keys": keys,
        "file_count": len(inventory["files"]),
        "result_path": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args):
    files = discover_input(args.input)
    print(json.dumps({"input_root": args.input, "files": files},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    keys = _load_contract_keys(args)
    if os.path.isfile(result_path):
        obj, errs = read_json(result_path)
        if not isinstance(obj, dict):
            obj, sk = build_skeleton(args.input)
            obj["errors"] = list(obj.get("errors") or []) + \
                (errs or ["result.json_invalid"])
            keys = keys or sk
    else:
        obj, sk = build_skeleton(args.input)
        keys = keys or sk

    obj = _ensure_base(obj)
    obj.setdefault("case_id", "unknown")
    obj.setdefault("status", "partial")
    if obj.get("status") not in ALLOWED_STATUS:
        obj["status"] = "partial"
    for key in keys:
        if key not in obj:
            obj[key] = None
    obj["runtime"]["ended_at"] = utcnow()
    change_log = obj.setdefault("change_log", [])
    if not any(isinstance(e, dict) and e.get("step") == "finalize" for e in change_log):
        change_log.append({
            "time": utcnow(),
            "step": "finalize",
            "note": "finalize: contract keys ensured, runtime closed",
        })
    write_json(result_path, obj)
    print(json.dumps({
        "case_id": obj.get("case_id"),
        "status": obj.get("status"),
        "contract_keys": keys,
        "result_path": result_path,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_selfcheck(args):
    result_path = os.path.join(args.output, "result.json")
    problems = []
    if not os.path.isfile(result_path):
        problems.append("result.json_not_found")
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
        return 1
    obj, errs = read_json(result_path)
    problems.extend("result_json:" + e for e in errs)
    if not isinstance(obj, dict):
        problems.append("result.json_not_object")
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False, indent=2))
        return 1
    if not obj.get("case_id"):
        problems.append("case_id_missing")
    if obj.get("status") not in ALLOWED_STATUS:
        problems.append("status_invalid:" + str(obj.get("status")))
    for field in ("sources", "uncertainty", "change_log"):
        if not isinstance(obj.get(field), list):
            problems.append(field + "_not_list")
    keys = _load_contract_keys(args)
    missing = [k for k in keys if k not in obj]
    if missing:
        problems.append("contract_keys_missing:" + ",".join(missing))
    artifacts = obj.get("artifacts") or []
    for a in artifacts:
        if not os.path.isfile(os.path.join(args.output, a)):
            problems.append("artifact_missing:" + a)
    if artifacts and not obj.get("main_artifact"):
        problems.append("main_artifact_missing_when_artifacts_present")
    rep = {
        "ok": not problems,
        "case_id": obj.get("case_id"),
        "status": obj.get("status"),
        "problems": problems,
        "contract_keys": keys,
    }
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def cmd_evidence_index(args):
    files = [f for f in discover_input(args.root) if f["is_text"]]
    index = {"root": args.root, "generated_at": utcnow(), "files": []}
    for f in files:
        text, err = read_text(f["path"])
        entry = {"rel": f["rel"], "size": f["size"], "error": err}
        if text is not None:
            entry["lines"] = [
                {"n": i, "text": ln} for i, ln in enumerate(text.splitlines(), 1)
            ]
        index["files"].append(entry)
    write_json(args.out, index)
    print(json.dumps({
        "indexed_files": len(index["files"]),
        "out": args.out,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_evidence_quote(args):
    text, err = read_text(args.file)
    if text is None:
        print(json.dumps({"error": err or "unreadable"}, ensure_ascii=False, indent=2))
        return 0
    lines = text.splitlines()
    quoted = []
    for n in range(args.start, min(args.end, len(lines)) + 1):
        quoted.append({"n": n, "text": lines[n - 1]})
    print(json.dumps({"file": args.file, "lines": quoted}, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="LaTeX 写作 Skill：引导落盘 result.json、发现输入、收尾、自检与证据定位。")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("bootstrap", "finalize", "selfcheck"):
        sp = sub.add_parser(name, help={
            "bootstrap": "扫描输入并立即落盘 result.json 骨架",
            "finalize": "补齐 contract 键并关闭 runtime",
            "selfcheck": "校验 result.json 契约（发现问题退出码 1）",
        }[name])
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")
        sp.set_defaults(func=globals()["cmd_" + name])

    sp = sub.add_parser("discover", help="列出输入目录下的全部文件")
    sp.add_argument("--input", default="/input")
    sp.set_defaults(func=cmd_discover)

    ev = sub.add_parser("evidence", help="建立材料定位与逐字引用")
    evsub = ev.add_subparsers(dest="evcmd", required=True)
    idx = evsub.add_parser("index", help="按行索引文本材料")
    idx.add_argument("--root", default="/input")
    idx.add_argument("--out", default="/app/work/source_index.json")
    idx.set_defaults(func=cmd_evidence_index)
    q = evsub.add_parser("quote", help="取出指定行范围的逐字片段")
    q.add_argument("--file", required=True)
    q.add_argument("--start", type=int, required=True)
    q.add_argument("--end", type=int, required=True)
    q.set_defaults(func=cmd_evidence_quote)
    return p


def main(argv=None):
    reconfigure_stdio()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
