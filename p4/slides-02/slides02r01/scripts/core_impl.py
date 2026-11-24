#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""record.py — 增量记录 change/source/evidence/version，原子写 result.json。

每条 evidence 必须带 locator + 逐字 quote。quote 自动在 $WORK_DIR/corpus/*.txt
或 $WORK_DIR/*.txt 等提取正文中比对，能比对到则标 quote_verified=true，
比对不到仍记录但标 false，并把该条写入 uncertainty，不静默通过。
只依赖 Python 标准库。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

QUOTE_TYPES = ("verbatim", "number", "paraphrase")
CONFIDENCE = ("high", "med", "low")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def find_result(path, output_dir):
    return path or os.path.join(output_dir, "result.json")


def load_result(path):
    if not os.path.isfile(path):
        return None, "missing:" + path
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, "bad json: %s" % e


def save_result(path, result):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def collect_corpus(work_dir):
    """返回可核验 quote 的正文语料 [(文件名, 全文), ...]。"""
    texts = []
    seen = set()
    roots = [os.path.join(work_dir, "corpus"), work_dir]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if p in seen or not os.path.isfile(p):
                continue
            seen.add(p)
            if name.lower().endswith((".txt", ".md", ".json")):
                try:
                    with open(p, "r", encoding="utf-8-sig") as f:
                        texts.append((name, f.read()))
                except Exception:  # noqa: BLE001
                    pass
    return texts


def verify_quote(quote, corpus):
    """在语料中逐字比对 quote，返回 (verified, 命中文件名)。"""
    if not quote:
        return False, None
    q = quote.strip()
    if not q:
        return False, None
    for name, text in corpus:
        if q in text:
            return True, name
    return False, None


def ensure_list(result, key):
    if not isinstance(result.get(key), list):
        result[key] = []
    return result[key]


def cmd_change(args, result):
    log = ensure_list(result, "change_log")
    log.append({
        "t": now_iso(), "step": args.step, "action": args.action,
        "status": args.status or "", "reason": args.reason or "",
    })
    return "change appended"


def cmd_source(args, result):
    srcs = ensure_list(result, "sources")
    srcs.append({
        "ref": args.ref, "loc": args.loc, "kind": args.kind,
        "quote": args.quote or None,
        "used_in": [x for x in (args.used_in.split(",") if args.used_in else []) if x],
    })
    return "source appended"


def cmd_evidence(args, result):
    corpus = [] if args.no_verify else collect_corpus(args.work)
    verified, hit = verify_quote(args.quote, corpus)
    entry = {
        "page": args.page,
        "role": args.role,
        "claim": args.claim,
        "refs": [x for x in (args.refs.split(",") if args.refs else []) if x],
        "locator": args.loc or None,
        "quote": args.quote or None,
        "quote_type": args.quote_type,
        "quote_verified": verified,
        "quote_hit": hit,
        "confidence": args.confidence,
        "checked": False,
        "t": now_iso(),
    }
    ev = ensure_list(result, "evidence_map")
    for i, e in enumerate(ev):
        if isinstance(e, dict) and e.get("page") == args.page and e.get("role") == args.role:
            ev[i] = entry
            return "evidence updated (page=%s role=%s verified=%s)" % (args.page, args.role, verified)
    ev.append(entry)
    if not verified and args.quote:
        unc = result.setdefault("uncertainty", {"unresolved": [], "missing_artifacts": [], "notes": ""})
        if not isinstance(unc, dict):
            unc = {"unresolved": [], "missing_artifacts": [], "notes": ""}
            result["uncertainty"] = unc
        unres = unc.setdefault("unresolved", [])
        if not isinstance(unres, list):
            unres = []
            unc["unresolved"] = unres
        note = "quote 未逐字比对到正文: page=%s loc=%s" % (args.page, args.loc or "?")
        if note not in unres:
            unres.append(note)
    return "evidence appended (verified=%s)" % verified


def cmd_version(args, result):
    ver = ensure_list(result, "version_records")
    ver.append({
        "name": args.name, "note": args.note,
        "files": [x for x in (args.files.split(",") if args.files else []) if x],
        "t": now_iso(),
    })
    return "version appended"


def main(argv=None):
    ap = argparse.ArgumentParser(description="增量记录 result.json（evidence 带 locator+quote）")
    ap.add_argument("--result", default=None, help="result.json 路径（默认 $OUTPUT_DIR/result.json）")
    ap.add_argument("--work", default=None, help="工作目录（供 quote 语料发现）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("change")
    p.add_argument("--step", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--status", default="")
    p.add_argument("--reason", default="")

    p = sub.add_parser("source")
    p.add_argument("--ref", required=True)
    p.add_argument("--loc", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--quote", default=None)
    p.add_argument("--used-in", default="")

    p = sub.add_parser("evidence")
    p.add_argument("--page", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--claim", required=True)
    p.add_argument("--refs", default="")
    p.add_argument("--loc", default=None)
    p.add_argument("--quote", default=None)
    p.add_argument("--quote-type", default="verbatim", choices=QUOTE_TYPES)
    p.add_argument("--confidence", default="high", choices=CONFIDENCE)
    p.add_argument("--no-verify", action="store_true", help="跳过 quote 比对")

    p = sub.add_parser("version")
    p.add_argument("--name", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--files", default="")

    args = ap.parse_args(argv)
    output_dir = os.environ.get("OUTPUT_DIR", "/app/output")
    work_dir = args.work or os.environ.get("WORK_DIR", "/app/work")
    args.work = work_dir  # 统一交给子命令使用
    result_path = find_result(args.result, output_dir)
    result, err = load_result(result_path)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1

    handlers = {"change": cmd_change, "source": cmd_source,
                "evidence": cmd_evidence, "version": cmd_version}
    msg = handlers[args.cmd](args, result)
    save_result(result_path, result)
    print(json.dumps({"ok": True, "result_path": result_path, "msg": msg}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
