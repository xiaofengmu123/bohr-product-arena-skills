#!/usr/bin/env python3
"""Self-test: checkable checklist + determinism verification for the grant Skill.

Runs the SKILL.md checklist automatically against /app/output and /app/work:
  - result.json present, valid, and complete (required keys);
  - artifact.file exists and element_matrix.md present when referenced;
  - every mapped fact carries a locator + verbatim quote;
  - every mapped fact's source_ref resolves into the source manifest;
  - seven grant elements are covered;
  - banned unverifiable superlatives are reported in the manuscript;
  - determinism: runtime.result_digest matches a canonical re-serialization, and
    the raw result contains no wall-clock timestamps or random fields.

Exit code 0 only when all hard checks pass. Standard library only.
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from main import canonical_digest  # noqa: E402
from ledger import source_file  # noqa: E402

SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容",
    "技术路线", "创新点", "研究基础与可行性",
]
REQUIRED_KEYS = [
    "case_id", "status", "artifact", "fact_source_map", "version_boundary",
    "sources", "uncertainty", "change_log", "runtime",
]
BANNED_SUPERLATIVES = ["首次", "国际领先", "填补空白", "国际首次", "世界领先", "首创"]
VOLATILE_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"),  # wall-clock timestamps
    re.compile(r"\b(datetime|timestamp|now|random|seed)\b", re.IGNORECASE),
]


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def read_text(path):
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        return ""


def check(result, out_dir, work_dir):
    checks = []  # (name, pass:bool, detail:str, hard:bool)
    def add(name, ok, detail, hard=True):
        checks.append({"name": name, "pass": bool(ok), "detail": detail, "hard": hard})

    if result is None:
        add("result.json", False, "result.json 不存在或无法解析", True)
        return checks

    missing = [k for k in REQUIRED_KEYS if k not in result]
    add("必需字段", not missing,
        ("缺少: %s" % ", ".join(missing)) if missing else "字段齐全", True)

    add("case_id", bool(result.get("case_id")), str(result.get("case_id") or "空"), True)

    art = result.get("artifact") or {}
    art_path = art.get("path")
    if art_path:
        full = os.path.join(out_dir, art_path)
        add("artifact.path", os.path.isfile(full), full, True)
    else:
        add("artifact.path", False, "artifact.path 为空", True)

    matrix = art.get("element_matrix")
    if matrix:
        full = os.path.join(out_dir, matrix)
        add("element_matrix", os.path.isfile(full), full, False)

    facts = result.get("fact_source_map", [])
    add("事实台账非空", len(facts) > 0, "共 %d 条" % len(facts), False)

    missing_quote, missing_loc, bad_source = [], [], []
    manifest = load_json(os.path.join(work_dir, "source_manifest.json"))
    manifest_paths = set()
    if manifest:
        manifest_paths = {f.get("path") for f in manifest.get("files", [])}
    for r in facts:
        if r.get("status") == "mapped":
            if not r.get("quote"):
                missing_quote.append(r.get("id"))
            if not r.get("locator"):
                missing_loc.append(r.get("id"))
            if r.get("source_ref") and manifest_paths and source_file(r.get("source_ref")) not in manifest_paths:
                bad_source.append(r.get("id"))
    add("mapped 均有 quote", not missing_quote,
        ("缺 quote: %s" % ", ".join(missing_quote)) if missing_quote else "通过", True)
    add("mapped 均有 locator", not missing_loc,
        ("缺 locator: %s" % ", ".join(missing_loc)) if missing_loc else "通过", True)
    add("source_ref 可解析", not bad_source,
        ("无法解析: %s" % ", ".join(bad_source)) if bad_source else "通过", False)

    elem = {e: False for e in SEVEN_ELEMENTS}
    for r in facts:
        if r.get("element") in elem:
            elem[r["element"]] = True
    uncovered = [e for e, v in elem.items() if not v]
    add("七要素覆盖", not uncovered,
        ("未覆盖: %s" % ", ".join(uncovered)) if uncovered else "全覆盖", False)

    st = result.get("status")
    add("status 诚实", st in {"done", "partial", "failed", "in_progress", "insufficient_evidence", "input_error"},
        str(st), True)

    # Determinism: canonical digest re-check + volatile scan.
    stored = (result.get("runtime") or {}).get("result_digest")
    recomputed = canonical_digest(result) if result else None
    add("result_digest 一致", bool(stored) and stored == recomputed,
        ("存储: %s\n重算: %s" % (stored, recomputed)) if stored != recomputed else "通过", True)

    raw = read_text(os.path.join(out_dir, "result.json"))
    volatile_hits = []
    for pat in VOLATILE_PATTERNS:
        for m in pat.findall(raw):
            volatile_hits.append(m)
    add("无墙钟/随机字段", not volatile_hits,
        ("命中: %s" % ", ".join(sorted(set(volatile_hits)))) if volatile_hits else "通过", True)

    # Banned superlative scan in manuscript and matrix (soft warnings).
    banned = []
    for rel in [art_path, matrix]:
        if not rel:
            continue
        text = read_text(os.path.join(out_dir, rel))
        for word in BANNED_SUPERLATIVES:
            if word in text:
                banned.append("%s(%s)" % (word, rel))
    add("被禁修饰词", not banned,
        ("出现: %s；须删除或改为有逐字引用支撑" % ", ".join(sorted(set(banned)))) if banned else "通过", False)

    return checks


def main(argv=None):
    ap = argparse.ArgumentParser(description="基金申请书写作 Skill 自检")
    ap.add_argument("--input", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--work", default=None)
    args = ap.parse_args(argv)

    out_dir = args.output or ("/app/output" if os.path.isdir("/app/output") else "output")
    work_dir = args.work or ("/app/work" if os.path.isdir("/app/work") else "work")
    input_dir = args.input or ("/input" if os.path.isdir("/input") else "input")

    result = load_json(os.path.join(out_dir, "result.json"))
    checks = check(result, out_dir, work_dir)
    hard_fail = [c for c in checks if not c["pass"] and c["hard"]]

    for c in checks:
        mark = "[PASS]" if c["pass"] else "[FAIL]"
        print("%s %s: %s" % (mark, c["name"], c["detail"]))

    print("-" * 60)
    total_pass = sum(1 for c in checks if c["pass"])
    print("通过 %d/%d 项；硬性失败 %d 项" % (total_pass, len(checks), len(hard_fail)))
    if input_dir and os.path.isdir(input_dir):
        print("输入目录: %s" % input_dir)
    return 0 if not hard_fail else 1


if __name__ == "__main__":
    sys.exit(main())
