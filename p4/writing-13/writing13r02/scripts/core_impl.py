#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事实台账：来源快照 + 逐字引证登记 + 校验 + 导出 fact_source_map。

设计目标：让每个数字 / 术语 / 引用 / 图表 / 关键论断都能回溯到材料的
locator + 逐字 quote，并显式标注 tense（已完成 completed / 拟开展 planned /
外部 external）。冲突不隐藏、缺证据标 unmapped，绝不编造。

子命令：
  snapshot --input-dir DIR --out FILE   生成来源清单（大小 + SHA256）
  add      --db FILE ...                登记一条事实
  list     --db FILE                    打印台账
  verify   --db FILE [--result FILE]    校验台账与 result.json 覆盖
  export   --db FILE --out FILE         导出 fact_source_map.json
仅依赖 Python 标准库。
"""
import argparse
import hashlib
import json
import os
import sys
import time

KIND_ALLOWED = {"number", "term", "citation", "figure", "assertion", "policy", "data"}
TENSE_ALLOWED = {"completed", "planned", "external", "n/a"}
STATUS_ALLOWED = {"mapped", "unmapped", "conflict"}
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # 超限跳过并记录


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def resolve_dir(kind):
    plat = {"input": "/input", "output": "/app/output", "work": "/app/work"}[kind]
    local = {"input": "input", "output": "output", "work": "work"}[kind]
    return plat if os.path.isdir(plat) else local


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as exc:
        return "error:" + str(exc)


def load_db(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_db(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cmd_snapshot(a):
    indir = a.input_dir or resolve_dir("input")
    entries = []
    skipped = []
    if not os.path.isdir(indir):
        print(json.dumps({"ok": False, "error": "input dir not found: %s" % indir},
                         ensure_ascii=False))
        return 1
    for root, dirs, files in os.walk(indir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            rel = os.path.relpath(path, indir)
            if size > MAX_SNAPSHOT_BYTES:
                skipped.append({"path": rel, "reason": "size>2MB"})
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".docx", ".xlsx", ".pptx"):
                ftype = "binary-" + ext.lstrip(".")
            else:
                ftype = "text"
            entries.append({
                "path": rel,
                "type": ftype,
                "size_bytes": size,
                "sha256": sha256_file(path)
            })
    manifest = {"input_dir": indir, "generated_at": utcnow(), "files": entries, "skipped": skipped}
    out = a.out or os.path.join(resolve_dir("work"), "source_manifest.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "out": out, "files": len(entries), "skipped": len(skipped)},
                     ensure_ascii=False))
    return 0


def cmd_add(a):
    db = a.db or os.path.join(resolve_dir("work"), "ledger.json")
    data = load_db(db)
    kind = a.kind if a.kind in KIND_ALLOWED else "assertion"
    tense = a.tense if a.tense in TENSE_ALLOWED else "n/a"
    status = a.status if a.status in STATUS_ALLOWED else "mapped"
    rec = {
        "ts": utcnow(),
        "claim": a.claim or "",
        "kind": kind,
        "source_ref": a.ref or "",
        "locator": a.locator or "",
        "quote": a.quote or "",
        "tense": tense,
        "status": status,
        "confidence": a.confidence or "",
        "note": a.note or ""
    }
    data.append(rec)
    save_db(db, data)
    print(json.dumps({"ok": True, "db": db, "count": len(data)}, ensure_ascii=False))
    return 0


def cmd_list(a):
    db = a.db or os.path.join(resolve_dir("work"), "ledger.json")
    data = load_db(db)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(a):
    db = a.db or os.path.join(resolve_dir("work"), "ledger.json")
    data = load_db(db)
    problems = []
    mapped = 0
    unmapped = 0
    conflicts = 0
    for i, rec in enumerate(data):
        if rec.get("status") == "unmapped":
            unmapped += 1
        elif rec.get("status") == "conflict":
            conflicts += 1
        else:
            mapped += 1
        if rec.get("status") == "mapped":
            if not (rec.get("quote") and rec.get("source_ref") and rec.get("locator")):
                problems.append({
                    "index": i,
                    "problem": "mapped 事实缺 quote/source_ref/locator",
                    "claim": rec.get("claim", "")[:120]
                })
        if rec.get("tense") not in TENSE_ALLOWED:
            problems.append({
                "index": i,
                "problem": "tense 非法",
                "claim": rec.get("claim", "")[:120]
            })

    result_json = {}
    if a.result:
        rp = a.result
    else:
        rp = os.path.join(resolve_dir("output"), "result.json")
    try:
        with open(rp, "r", encoding="utf-8-sig") as f:
            result_json = json.load(f)
    except (OSError, ValueError):
        pass
    fsmap = result_json.get("fact_source_map", []) if isinstance(result_json, dict) else []
    coverage_note = "ledger=%d result.fact_source_map=%d" % (len(data), len(fsmap))

    report = {
        "ok": len(problems) == 0,
        "ledger_count": len(data),
        "mapped": mapped,
        "unmapped": unmapped,
        "conflicts": conflicts,
        "problems": problems,
        "coverage": coverage_note
    }
    out = os.path.join(resolve_dir("work"), "verify_report.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


def cmd_export(a):
    db = a.db or os.path.join(resolve_dir("work"), "ledger.json")
    data = load_db(db)
    out = a.out or os.path.join(resolve_dir("work"), "fact_source_map.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "out": out, "count": len(data)}, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("snapshot", help="生成来源清单")
    p.add_argument("--input-dir", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("add", help="登记一条事实")
    p.add_argument("--db", default=None)
    p.add_argument("--claim")
    p.add_argument("--kind", default="assertion")
    p.add_argument("--ref", default="")
    p.add_argument("--locator", default="")
    p.add_argument("--quote", default="")
    p.add_argument("--tense", default="n/a")
    p.add_argument("--status", default="mapped")
    p.add_argument("--confidence", default="")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="打印台账")
    p.add_argument("--db", default=None)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("verify", help="校验台账")
    p.add_argument("--db", default=None)
    p.add_argument("--result", default=None)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("export", help="导出 fact_source_map")
    p.add_argument("--db", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_export)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
