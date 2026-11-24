#!/usr/bin/env python3
"""Source snapshot + fact ledger for grant-proposal writing (standard library only).

Commands:
  snapshot   Walk an input directory, record size+SHA256 per file, emit a
             deterministic source_manifest.json (sorted) with input_digest.
  add        Append one fact record to a JSONL ledger; auto-assigns a stable id.
  list       Print the ledger sorted by id.
  verify     Cross-check the ledger against the manifest: coverage, quote/locator
             presence, mapped-source resolution, orphan consistency links.
  export     Convert the ledger to a fact_source_map.json for result.json merge.

Determinism: records are appended in reading order but every export/list/verify
output sorts by id; no wall-clock or random values are written.
"""
import argparse
import hashlib
import json
import os
import sys

import re

SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容",
    "技术路线", "创新点", "研究基础与可行性",
]
VALID_ELEMENTS = set(SEVEN_ELEMENTS) | {"考核指标", "其他"}
VALID_KINDS = {"数字", "术语", "引用", "图表", "论断", "政策口径"}
VALID_STATUS = {"mapped", "unmapped", "conflict"}
VALID_TEMPORAL = {"已完成", "拟开展", "未知"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB per-file cap

# Common aliases -> canonical element name (tolerant input, deterministic output).
ELEMENT_ALIASES = {
    "立项依据": "立项依据", "研究意义": "立项依据", "国内外现状": "立项依据",
    "科学问题": "科学问题", "关键科学问题": "科学问题", "拟解决的关键科学问题": "科学问题",
    "研究目标": "研究目标", "目标": "研究目标",
    "研究内容": "研究内容", "内容": "研究内容",
    "技术路线": "技术路线", "研究方案": "技术路线", "技术方案": "技术路线", "路线": "技术路线",
    "创新点": "创新点", "创新": "创新点", "特色与创新": "创新点",
    "研究基础": "研究基础与可行性", "研究基础与可行性": "研究基础与可行性",
    "可行性": "研究基础与可行性", "工作基础": "研究基础与可行性", "工作条件": "研究基础与可行性",
    "考核指标": "考核指标", "指标": "考核指标", "预期成果": "考核指标",
}

_PAGE_RE = re.compile(r":(页\d+|[pP]?\d+)$")


def canonicalize_element(name):
    if not name:
        return "其他"
    return ELEMENT_ALIASES.get(name, ELEMENT_ALIASES.get(name.strip(), "其他"))


def source_file(ref):
    """Strip a trailing ':<page>' locator from a 'file:page' source_ref."""
    if not ref:
        return ref
    m = _PAGE_RE.search(ref)
    return ref[:m.start()] if m else ref


def dump_json(obj, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def guess_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".md", ".markdown", ".txt", ".rst"):
        return "text"
    if ext in (".json", ".jsonl", ".csv", ".tsv"):
        return "data"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff"):
        return "image"
    if ext in (".tex", ".docx", ".doc", ".odt"):
        return "document"
    return "other"


def cmd_snapshot(args):
    if not os.path.isdir(args.input_dir):
        return {"files": [], "skipped": [], "input_digest": None,
                "error": "input_dir 不存在: %s" % args.input_dir}
    files, skipped = [], []
    for root, dirs, names in os.walk(args.input_dir):
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, args.input_dir).replace("\\", "/")
            size = os.path.getsize(full)
            if size > MAX_FILE_SIZE:
                skipped.append({"path": rel, "reason": "超过 2MB 上限"})
                continue
            files.append({
                "path": rel,
                "size": size,
                "sha256": sha256_file(full),
                "type": guess_type(full),
            })
    files.sort(key=lambda f: f["path"])
    skipped.sort(key=lambda f: f["path"])
    digest_src = "".join("%s\x00%s\x00%d\n" % (f["path"], f["sha256"], f["size"]) for f in files)
    input_digest = hashlib.sha256(digest_src.encode("utf-8")).hexdigest()
    manifest = {"files": files, "skipped": skipped, "input_digest": input_digest}
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        dump_json(manifest, args.out)
    else:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def load_ledger(path):
    if not path or not os.path.isfile(path):
        return []
    recs = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    return recs


def next_id(recs):
    return "F%03d" % (len(recs) + 1)


def cmd_add(args):
    recs = load_ledger(args.ledger)
    rec = {
        "id": args.id or next_id(recs),
        "element": args.element,
        "kind": args.kind,
        "claim": args.claim,
        "source_ref": args.ref,
        "locator": args.loc,
        "quote": args.quote,
        "status": args.status,
        "temporal": args.temporal,
        "goal_ref": args.goal_ref,
        "content_ref": args.content_ref,
        "route_ref": args.route_ref,
        "metric_ref": args.metric_ref,
        "note": args.note,
    }
    recs.append(rec)
    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)), exist_ok=True)
    with open(args.ledger, "w", encoding="utf-8", newline="\n") as f:
        for r in sorted(recs, key=lambda x: x.get("id", "")):
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(rec, ensure_ascii=False))
    return rec


def cmd_list(args):
    recs = load_ledger(args.ledger)
    recs.sort(key=lambda x: x.get("id", ""))
    print(json.dumps(recs, ensure_ascii=False, indent=2, sort_keys=True))
    return recs


def cmd_verify(args):
    recs = load_ledger(args.ledger)
    manifest = None
    if args.manifest and os.path.isfile(args.manifest):
        with open(args.manifest, "r", encoding="utf-8-sig") as f:
            manifest = json.load(f)
    manifest_paths = set()
    if manifest:
        manifest_paths = {f["path"] for f in manifest.get("files", [])}

    errors, warnings = [], []
    element_counts = {e: 0 for e in SEVEN_ELEMENTS}
    status_counts = {"mapped": 0, "unmapped": 0, "conflict": 0}
    temporal_counts = {"已完成": 0, "拟开展": 0, "未知": 0}
    orphan_links = []

    for r in recs:
        if r.get("element") in element_counts:
            element_counts[r["element"]] += 1
        st = r.get("status")
        if st in status_counts:
            status_counts[st] += 1
        tp = r.get("temporal")
        if tp in temporal_counts:
            temporal_counts[tp] += 1
        if st == "mapped":
            if not r.get("quote"):
                errors.append("%s: mapped 记录缺少逐字 quote" % r.get("id"))
            if not r.get("locator"):
                errors.append("%s: mapped 记录缺少 locator" % r.get("id"))
            if r.get("source_ref") and manifest_paths and source_file(r["source_ref"]) not in manifest_paths:
                warnings.append("%s: source_ref 不在来源清单中" % r.get("id"))
        if r.get("goal_ref") or r.get("content_ref") or r.get("route_ref") or r.get("metric_ref"):
            refs = [x for x in (r.get("goal_ref"), r.get("content_ref"),
                                r.get("route_ref"), r.get("metric_ref")) if x]
            if len(refs) < 2:
                orphan_links.append(r.get("id"))

    missing_elements = [e for e in SEVEN_ELEMENTS if element_counts[e] == 0]
    if missing_elements:
        warnings.append("未覆盖的要素: %s" % ", ".join(missing_elements))
    if orphan_links:
        warnings.append("一致性链接不完整（目标↔内容↔路线↔指标未两两对应）: %s" % ", ".join(orphan_links))

    report = {
        "total": len(recs),
        "element_counts": element_counts,
        "status_counts": status_counts,
        "temporal_counts": temporal_counts,
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        dump_json(report, args.out)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def cmd_export(args):
    recs = load_ledger(args.ledger)
    recs.sort(key=lambda x: x.get("id", ""))
    element_counts = {e: 0 for e in SEVEN_ELEMENTS}
    status_counts = {"mapped": 0, "unmapped": 0, "conflict": 0}
    temporal_counts = {"已完成": 0, "拟开展": 0, "未知": 0}
    for r in recs:
        if r.get("element") in element_counts:
            element_counts[r["element"]] += 1
        if r.get("status") in status_counts:
            status_counts[r["status"]] += 1
        if r.get("temporal") in temporal_counts:
            temporal_counts[r["temporal"]] += 1
    export = {
        "fact_source_map": recs,
        "stats": {
            "total": len(recs),
            "elements": element_counts,
            "status": status_counts,
            "temporal": temporal_counts,
        },
    }
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        dump_json(export, args.out)
    else:
        print(json.dumps(export, ensure_ascii=False, indent=2))
    return export


def main(argv=None):
    ap = argparse.ArgumentParser(description="来源快照 + 事实台账")
    sub = ap.add_subparsers(dest="command", required=True)

    sn = sub.add_parser("snapshot", help="生成来源清单（大小+SHA256+input_digest）")
    sn.add_argument("--input-dir", required=True)
    sn.add_argument("--out", default=None)
    sn.set_defaults(func=lambda a: cmd_snapshot(a))

    ad = sub.add_parser("add", help="登记一条事实")
    ad.add_argument("--ledger", required=True)
    ad.add_argument("--id", default=None)
    ad.add_argument("--element", required=True, help="七要素之一或 考核指标/其他")
    ad.add_argument("--kind", required=True, help="数字/术语/引用/图表/论断/政策口径")
    ad.add_argument("--claim", required=True)
    ad.add_argument("--ref", default=None, help="文件:页")
    ad.add_argument("--loc", default=None, help="段/表/图")
    ad.add_argument("--quote", default=None, help="逐字片段")
    ad.add_argument("--status", default="mapped", choices=sorted(VALID_STATUS))
    ad.add_argument("--temporal", default="未知", choices=sorted(VALID_TEMPORAL))
    ad.add_argument("--goal-ref", default=None)
    ad.add_argument("--content-ref", default=None)
    ad.add_argument("--route-ref", default=None)
    ad.add_argument("--metric-ref", default=None)
    ad.add_argument("--note", default=None)
    ad.set_defaults(func=lambda a: cmd_add(a))

    ls = sub.add_parser("list", help="列出台账（按 id 排序）")
    ls.add_argument("--ledger", required=True)
    ls.set_defaults(func=lambda a: cmd_list(a))

    vf = sub.add_parser("verify", help="交叉核对台账与来源清单")
    vf.add_argument("--ledger", required=True)
    vf.add_argument("--manifest", default=None)
    vf.add_argument("--out", default=None)
    vf.set_defaults(func=lambda a: cmd_verify(a))

    ex = sub.add_parser("export", help="导出 fact_source_map.json")
    ex.add_argument("--ledger", required=True)
    ex.add_argument("--out", default=None)
    ex.set_defaults(func=lambda a: cmd_export(a))

    args = ap.parse_args(argv)
    # Normalize enum values with tolerant matching (aliases / full-width / case).
    if args.command == "add":
        args.element = canonicalize_element(args.element)
        if args.kind not in VALID_KINDS:
            args.kind = "论断"
    return 0 if args.func(args) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
