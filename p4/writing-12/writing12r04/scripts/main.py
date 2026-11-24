#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py — LaTeX 写作与编译 Skill 的编排入口。

职责：
  1. 扫描 /input，发现 case.json / task.md 与材料文件（不假设固定文件名/键名）；
  2. 抽取 case_id 与 output_contract 字段清单；
  3. 立即写一版合法 result.json 骨架（先落盘，再增量改进）；
  4. index/quote 建立证据索引与逐字取证；
  5. finalize/selfcheck 校验契约完整性、证据一致性与诚实性。

仅依赖 Python 标准库。除 selfcheck 外，其余子命令对非致命失败一律退出码 0；
selfcheck 发现 CRITICAL 问题时退出码 1。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml",
             ".rst", ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst"}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
ALLOWED_FACT_STATES = {"verified", "inferred", "unverified"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")
REQUIRED_KEYS = ("case_id", "status", "sources", "uncertainty", "change_log")


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
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
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


def default_meta():
    return {
        "sources": [],
        "facts": [],
        "uncertainty": [],
        "pending_human": [],
        "limitations": [],
        "errors": [],
        "degradations": [],
        "change_log": [],
        "runtime": {"started_at": utcnow(), "producer": "main.py"},
    }


def build_skeleton(root):
    """Build result.json skeleton from /input/case.json. Returns (skeleton, keys)."""
    case_path = os.path.join(root, "case.json")
    notes = []
    case_id = None
    keys = []
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(obj)
        keys = contract_keys(obj)
        if not keys:
            notes.append("output_contract_not_found: contract fields default to null")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    skeleton = {"case_id": case_id}
    for key in keys:
        skeleton[key] = None
    meta = default_meta()
    skeleton.update(meta)
    skeleton["status"] = "partial" if keys else "input_error"
    skeleton["errors"] = notes
    skeleton["change_log"].append({
        "time": utcnow(),
        "step": "bootstrap",
        "note": "result skeleton written before analysis; contract fields default to null",
    })
    return skeleton, keys


def build_index(root):
    entries = []
    for f in discover_input(root):
        entry = {"path": f["path"], "rel": f["rel"], "size": f["size"],
                 "ext": f["ext"], "pdf": f["ext"] == ".pdf",
                 "lines": None, "error": None}
        if f["is_text"]:
            text, err = read_text(f["path"])
            if text is None:
                entry["error"] = err
            else:
                entry["lines"] = text.splitlines()
                entry["error"] = err
        elif f["ext"] == ".pdf":
            entry["error"] = ("pdf: extract with pdf-text-tool, save as UTF-8 "
                              "text under /app/work/text/, then re-index")
        entries.append(entry)
    return entries


def _keys_from_inventory(work):
    inv_path = os.path.join(work, "inventory.json")
    if not os.path.isfile(inv_path):
        return None
    inv, _ = read_json(inv_path)
    if isinstance(inv, dict) and isinstance(inv.get("contract_keys"), list):
        return inv["contract_keys"]
    return None


def _load_index(work):
    p = os.path.join(work, "source_index.json")
    if not os.path.isfile(p):
        return None
    obj, _ = read_json(p)
    return obj if isinstance(obj, dict) else None


def _quote_in_index(locator, quote, index):
    """Return (ok, reason). locator format: relpath#L12 or relpath#L12-L15."""
    m = re.match(r"^(.*?)#L(\d+)(?:-L?(\d+))?$", locator or "")
    if not m:
        return True, "locator format not parseable; substring check skipped"
    rel, start = m.group(1).replace("\\", "/"), int(m.group(2))
    end = int(m.group(3) or m.group(2))
    for f in index.get("files", []) or []:
        if f.get("rel") == rel or (f.get("rel") or "").endswith(rel):
            lines = f.get("lines")
            if not isinstance(lines, list):
                return False, "source file has no indexed lines (PDF or too large)"
            if start < 1 or start > len(lines):
                return False, "line %d out of range 1..%d" % (start, len(lines))
            window = "\n".join(lines[start - 1:end])
            q = (quote or "").strip()
            if quote in window or q in window.replace("\u3000", " "):
                return True, "matched"
            return False, "quote not found in %s lines %d-%d" % (rel, start, end)
    return False, "locator %r not found in index" % rel


def _check_facts(obj, work, issues):
    facts = obj.get("facts", [])
    if not isinstance(facts, list):
        issues.append({"severity": "warning", "where": "facts",
                       "msg": "facts should be a list"})
        return
    index = _load_index(work)
    pending_ids = set()
    for p in obj.get("pending_human", []) or []:
        if isinstance(p, dict) and p.get("fact_id") is not None:
            pending_ids.add(str(p["fact_id"]))
    for i, f in enumerate(facts):
        where = "facts[%d]" % i
        if not isinstance(f, dict):
            issues.append({"severity": "warning", "where": where,
                           "msg": "fact must be an object"})
            continue
        state = f.get("state")
        if state not in ALLOWED_FACT_STATES:
            issues.append({"severity": "critical", "where": where,
                           "msg": "invalid state %r (allowed: verified/inferred/unverified)" % state})
            continue
        if state == "verified":
            if not f.get("locator") or not f.get("quote"):
                issues.append({"severity": "critical", "where": where,
                               "msg": "verified fact requires locator + verbatim quote"})
            elif index is not None:
                ok, reason = _quote_in_index(f.get("locator"), f.get("quote"), index)
                if not ok:
                    issues.append({"severity": "warning", "where": where,
                                   "msg": "quote not reproducible in source_index: %s" % reason})
        elif state == "inferred":
            if not f.get("note"):
                issues.append({"severity": "warning", "where": where,
                               "msg": "inferred fact should explain inference basis in note"})
        elif state == "unverified":
            fid = str(f.get("id", i))
            if fid not in pending_ids:
                issues.append({"severity": "critical", "where": where,
                               "msg": "unverified fact %s has no matching pending_human entry" % fid})


def _check_sources(obj, issues):
    sources = obj.get("sources", [])
    if not isinstance(sources, list):
        issues.append({"severity": "warning", "where": "sources",
                       "msg": "sources should be a list"})
        return
    for i, s in enumerate(sources):
        if not isinstance(s, dict) or not s.get("locator") or not s.get("quote"):
            issues.append({"severity": "warning", "where": "sources[%d]" % i,
                           "msg": "source should have locator + quote"})


def _check_status_honesty(obj, keys, issues):
    status = obj.get("status")
    if status == "ok":
        for k in keys:
            if obj.get(k) is None:
                issues.append({"severity": "warning", "where": "status",
                               "msg": "status=ok but contract field %r is null" % k})
        blockers = [p for p in (obj.get("pending_human") or [])
                    if isinstance(p, dict) and p.get("severity") == "blocker"]
        if blockers:
            issues.append({"severity": "warning", "where": "status",
                           "msg": "status=ok but %d blocker pending_human items remain" % len(blockers)})
    elif status == "insufficient_evidence":
        gaps = (obj.get("limitations") or []) + (obj.get("pending_human") or [])
        if not gaps:
            issues.append({"severity": "warning", "where": "status",
                           "msg": "status=insufficient_evidence but no limitations/pending_human explains the gap"})


def _print_issues(issues):
    if not issues:
        print(json.dumps({"result": "selfcheck_ok", "issues": []},
                         ensure_ascii=False, indent=2))
        return
    crit = [i for i in issues if i["severity"] == "critical"]
    warn = [i for i in issues if i["severity"] == "warning"]
    print(json.dumps({"result": "critical" if crit else ("warnings" if warn else "ok"),
                      "critical": crit, "warnings": warn},
                     ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

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


def cmd_index(args):
    entries = build_index(args.root)
    out = {"root": args.root, "generated_at": utcnow(), "files": entries}
    write_json(args.out, out)
    print(json.dumps({
        "indexed_files": len(entries),
        "text_files": sum(1 for e in entries if e.get("lines") is not None),
        "pdf_files": sum(1 for e in entries if e.get("pdf")),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_quote(args):
    text, err = read_text(args.file)
    if text is None:
        print(json.dumps({"file": args.file, "error": err},
                         ensure_ascii=False, indent=2))
        return 0
    lines = text.splitlines()
    start = max(1, args.start)
    end = args.end if args.end is not None else start
    end = min(len(lines), max(start, end))
    window = lines[start - 1:end]
    print(json.dumps({"file": args.file, "start": start, "end": end,
                      "lines": window, "text": "\n".join(window)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_finalize(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    result, _ = read_json(result_path)
    if not isinstance(result, dict):
        result, keys = build_skeleton(args.input)
    else:
        case_obj, _ = read_json(os.path.join(args.input, "case.json"))
        keys = contract_keys(case_obj) if case_obj else []
    result.setdefault("case_id", "unknown")
    for k in keys:
        result.setdefault(k, None)
    for k, v in default_meta().items():
        result.setdefault(k, v)
    result.setdefault("status", "partial")
    if not isinstance(result.get("change_log"), list):
        result["change_log"] = []
    result["change_log"].append({
        "time": utcnow(), "step": "finalize",
        "note": "contract keys ensured; %d output_contract fields" % len(keys),
    })
    write_json(result_path, result)
    print(json.dumps({
        "result_path": result_path, "contract_keys": keys,
        "status": result.get("status"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_selfcheck(args):
    result_path = os.path.join(args.output, "result.json")
    issues = []
    if not os.path.isfile(result_path):
        _print_issues([{"severity": "critical", "where": result_path,
                        "msg": "result.json missing"}])
        return 1
    obj, errs = read_json(result_path)
    if obj is None:
        _print_issues([{"severity": "critical", "where": result_path,
                        "msg": "result.json not valid JSON: %s" % "; ".join(errs)}])
        return 1
    for e in errs:
        issues.append({"severity": "warning", "where": result_path,
                       "msg": "json read note: %s" % e})
    if not isinstance(obj, dict):
        _print_issues([{"severity": "critical", "where": result_path,
                        "msg": "result.json must be a JSON object"}])
        return 1

    if not obj.get("case_id"):
        issues.append({"severity": "critical", "where": "case_id",
                       "msg": "case_id missing/empty"})
    elif obj.get("case_id") == "unknown":
        issues.append({"severity": "warning", "where": "case_id",
                       "msg": "case_id is 'unknown'; verify input"})

    status = obj.get("status")
    if status not in ALLOWED_STATUS:
        issues.append({"severity": "critical", "where": "status",
                       "msg": "invalid status %r" % status})

    for k in REQUIRED_KEYS:
        if k not in obj:
            issues.append({"severity": "critical", "where": "result.json",
                           "msg": "missing required key %r" % k})

    keys = _keys_from_inventory(args.work)
    if keys is None:
        case_obj, _ = read_json(os.path.join(args.input, "case.json"))
        keys = contract_keys(case_obj) if case_obj else []
    for k in keys:
        if k not in obj:
            issues.append({"severity": "critical", "where": "contract",
                           "msg": "output_contract key %r missing (must echo verbatim, null allowed)" % k})

    _check_facts(obj, args.work, issues)
    _check_sources(obj, issues)
    _check_status_honesty(obj, keys, issues)

    _print_issues(issues)
    critical = [i for i in issues if i["severity"] == "critical"]
    return 1 if critical else 0


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="LaTeX writing skill orchestrator")
    sub = p.add_subparsers(dest="cmd")

    def add_io(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    sp = sub.add_parser("bootstrap", help="write result skeleton + inventory")
    add_io(sp)
    sp.set_defaults(func=cmd_bootstrap)

    sp = sub.add_parser("discover", help="list input files")
    sp.add_argument("--input", default="/input")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("index", help="build source evidence index")
    sp.add_argument("--root", default="/input")
    sp.add_argument("--out", default="/app/work/source_index.json")
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("quote", help="verbatim-quote lines from a file")
    sp.add_argument("--file", required=True)
    sp.add_argument("--start", type=int, required=True)
    sp.add_argument("--end", type=int, default=None)
    sp.set_defaults(func=cmd_quote)

    sp = sub.add_parser("finalize", help="ensure contract keys, finalize result.json")
    add_io(sp)
    sp.set_defaults(func=cmd_finalize)

    sp = sub.add_parser("selfcheck", help="validate result.json (exit 1 on critical)")
    add_io(sp)
    sp.set_defaults(func=cmd_selfcheck)

    return p


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 1 if args.cmd == "selfcheck" else 0


if __name__ == "__main__":
    sys.exit(main())
