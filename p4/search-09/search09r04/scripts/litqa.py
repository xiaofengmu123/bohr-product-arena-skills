#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-veritas 证据定位、逐字核验与分级聚合（确定性、只依赖标准库）。

子命令：
  locators   --input /input --work /app/work
             扫描材料，抽取纯文本并生成稳定段落定位，写 /app/work/locators.json。
  verify     --work /app/work --result /app/output/result.json
             对 result.json 中 evidence_level=direct 的条目做逐字引文核验。
  grade      --result /app/output/result.json
             按阈值把 assessment 聚合为 per_dimension/per_source/overall 等级。

文本抽取：.txt/.md/.json/.xml/.csv 直接读取；.pdf 尝试调用 pdftotext -layout
（不可用则在 work/locators.json 记录 degradation，不编造文本）。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import unicodedata
import time
from pathlib import Path

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
MAX_FILE_BYTES = 2 * 1024 * 1024
EVIDENCE_LEVELS = {"direct", "paraphrase", "inference", "absent"}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def norm(s):
    """归一化：NFC、空白折叠、去首尾空白。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def read_text(path, max_bytes=MAX_FILE_BYTES):
    errors = []
    p = Path(path)
    if not p.exists():
        return None, ["missing: %s" % p]
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, ["stat_failed: %s: %s" % (p, exc)]
    if size > max_bytes:
        return None, ["oversize(%d>%d): %s" % (size, max_bytes, p)]
    raw = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            raw = p.read_bytes().decode(enc)
            break
        except (UnicodeDecodeError, OSError) as exc:
            errors.append("decode(%s)_failed: %s: %s" % (enc, p, exc))
    if raw is None:
        return None, errors
    return raw, errors


def atomic_write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(p))


def load_json_lenient(path):
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, errors + ["bad_json: %s: %s" % (path, exc)]


def now_utc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def has_tool(name):
    return shutil.which(name) is not None


# ---------------------------------------------------------------------------
# 文本抽取
# ---------------------------------------------------------------------------


def pdf_to_text(pdf_path, work_dir):
    """用 pdftotext -layout 抽取；不可用返回 (None, degradation)。"""
    if not has_tool("pdftotext"):
        return None, "pdftotext_not_available"
    out_txt = Path(work_dir) / (pdf_path.stem + ".pdftotext.txt")
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(out_txt)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "pdftotext_failed: %s" % exc
    if proc.returncode != 0 or not out_txt.exists():
        return None, "pdftotext_rc=%d" % proc.returncode
    text, errors = read_text(out_txt)
    if text is None:
        return None, "pdftotext_read: %s" % "; ".join(errors)
    return text, None


def extract_material_text(path, work_dir):
    """返回 (text, degradations)；PDF 优先 pdftotext，其余直接读文本。"""
    degradations = []
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, deg = pdf_to_text(path, work_dir)
        if deg:
            degradations.append(deg)
        return text, degradations
    text, errors = read_text(path)
    if text is None:
        degradations.extend(errors)
    return text, degradations


def split_paragraphs(text):
    """稳定段落切分：按连续空行/换行分隔，去空段。"""
    if not text:
        return []
    parts = re.split(r"\n\s*\n+|\r\n\s*\r\n+", text)
    out = []
    for part in parts:
        p = norm(part)
        if p:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# locators
# ---------------------------------------------------------------------------


def locators_cmd(args):
    input_dir = args.input or DEFAULT_INPUT
    work_dir = args.work or DEFAULT_WORK
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    materials = []
    if Path(input_dir).is_dir():
        for p in sorted(Path(input_dir).rglob("*")):
            if not p.is_file():
                continue
            name = p.name.lower()
            if p.suffix.lower() in (".pdf", ".txt", ".md", ".json", ".xml", ".csv", ".tsv") \
                    or "material" in name:
                materials.append(p)

    docs = {}
    degradations = []
    for idx, mp in enumerate(materials):
        sid = "src%d" % (idx + 1)
        text, degs = extract_material_text(mp, Path(work_dir))
        degradations.extend({"source_id": sid, "path": str(mp), "degradation": d}
                            for d in degs)
        paragraphs = split_paragraphs(text) if text else []
        docs[sid] = {
            "path": str(mp),
            "name": mp.name,
            "kind": "pdf" if mp.suffix.lower() == ".pdf" else "text",
            "paragraphs": paragraphs,
        }

    out = {
        "generated_utc": now_utc_iso(),
        "sources": {sid: {"path": d["path"], "name": d["name"], "kind": d["kind"],
                           "n_paragraphs": len(d["paragraphs"])} for sid, d in docs.items()},
        "documents": docs,
        "degradations": degradations,
    }
    out_path = Path(work_dir) / "locators.json"
    atomic_write_json(out_path, out)
    print("locators written: %s (%d sources, %d degradations)"
          % (out_path, len(docs), len(degradations)))
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def parse_locator(locator):
    """locator 形如 src1#p3 或 src1#p3-p5 或 src1。返回 (sid, para_indexes|None)。"""
    if not isinstance(locator, str) or not locator.strip():
        return None, None
    s = locator.strip()
    m = re.match(r"^([A-Za-z0-9_.-]+)(?:#p(\d+)(?:-(\d+))?)?$", s)
    if not m:
        return None, None
    sid = m.group(1)
    if m.group(2) is None:
        return sid, None
    start = int(m.group(2))
    end = int(m.group(3)) if m.group(3) else start
    return sid, (start, end)


def quote_in_paragraphs(quote, paragraphs, start, end):
    """在指定段落范围逐字核验（归一化后子串匹配）。"""
    q = norm(quote)
    if not q:
        return False
    for i in range(start, min(end, len(paragraphs)) + 1):
        if q in paragraphs[i - 1]:
            return True
    return False


def verify_cmd(args):
    work_dir = args.work or DEFAULT_WORK
    result_path = args.result or (Path(DEFAULT_OUTPUT) / "result.json")
    loc_path = Path(work_dir) / "locators.json"

    locators, _ = load_json_lenient(loc_path)
    if locators is None:
        print("ERROR locators.json missing: %s" % loc_path)
        return 2
    docs = locators.get("documents", {})

    result, errors = load_json_lenient(result_path)
    if result is None:
        print("ERROR result.json unparseable: %s" % result_path)
        return 2

    checks = 0
    passed = 0
    failed = []
    assessment = result.get("assessment", [])
    if not isinstance(assessment, list):
        assessment = []
    for i, item in enumerate(assessment):
        if not isinstance(item, dict):
            continue
        lvl = item.get("evidence_level")
        if lvl != "direct":
            continue
        locator = item.get("locator")
        quote = item.get("quote")
        sid, para = parse_locator(locator)
        if sid is None or sid not in docs:
            failed.append({"index": i, "reason": "bad_locator", "locator": locator})
            continue
        checks += 1
        paragraphs = docs[sid].get("paragraphs", [])
        ok = False
        if para is None:
            joined = norm(" ".join(paragraphs))
            ok = norm(quote) in joined
        else:
            start, end = para
            ok = quote_in_paragraphs(quote, paragraphs, start, end)
        if ok:
            passed += 1
        else:
            failed.append({"index": i, "source_id": sid, "locator": locator,
                           "reason": "quote_not_found"})

    # 把核验结果写回 result.json 的 uncertainty（只读统计，不改动用户判断）
    summary = {"checks": checks, "passed": passed, "failed": len(failed),
               "failed_items": failed[:50]}
    for f in failed:
        print("FAIL assessment[%d]: %s (%s)" % (f["index"], f["reason"], f.get("locator")))
    print("verify: %d direct quotes checked, %d passed, %d failed"
          % (checks, passed, len(failed)))
    out_path = Path(work_dir) / "verify_report.json"
    atomic_write_json(out_path, {"generated_utc": now_utc_iso(), **summary})
    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# grade
# ---------------------------------------------------------------------------


# 归一化判定词 → 分级等级（等级顺序：低风险最好，证据不足最差）
CANONICAL = {
    "low": "low", "low_risk": "low", "low risk": "low", "adequate": "low",
    "moderate": "moderate", "some_concerns": "moderate", "some concerns": "moderate",
    "moderate_risk": "moderate", "possible": "moderate",
    "high": "high", "high_risk": "high", "high risk": "high",
    "critical": "high", "critical_risk": "high", "critical risk": "high",
    "unclear": "unclear", "not_assessed": "unclear", "conflict": "unclear",
    "insufficient_evidence": "insufficient_evidence", "not_reported": "insufficient_evidence",
    "unable_to_assess": "insufficient_evidence", "absent": "insufficient_evidence",
}
# 分级排序（数值越小越差）
ORDER = {"insufficient_evidence": 0, "unclear": 1, "high": 2, "moderate": 3, "low": 4}
NAME_BY_RANK = {v: k for k, v in ORDER.items()}


def _canonical(verdict):
    v = str(verdict or "").strip().lower()
    return CANONICAL.get(v, "unclear")


def _dim_grade(entries):
    if not entries:
        return "insufficient_evidence"
    critical = [e for e in entries if e.get("critical")]
    noncrit = [e for e in entries if not e.get("critical")]
    # 关键条目：证据 absent 或判定不足 → 维度不足
    for e in critical:
        if e.get("evidence_level") == "absent" or _canonical(e.get("verdict")) == "insufficient_evidence":
            return "insufficient_evidence"
    pool = critical or noncrit
    ranks = [ORDER[_canonical(e.get("verdict"))] for e in pool]
    if not ranks:
        return "unclear"
    return NAME_BY_RANK[min(ranks)]


def _overall_grade(dim_grades, source_grades):
    vals = list(dim_grades.values()) or list(source_grades.values()) or ["insufficient_evidence"]
    ranks = [ORDER.get(v, ORDER["unclear"]) for v in vals]
    lo = min(ranks) if ranks else ORDER["insufficient_evidence"]
    # 任何维度/来源不足 → 整体封顶
    if "insufficient_evidence" in dim_grades.values() \
            or "insufficient_evidence" in source_grades.values():
        return "insufficient_evidence"
    return NAME_BY_RANK[lo]


def grade_cmd(args):
    result_path = args.result or (Path(DEFAULT_OUTPUT) / "result.json")
    result, errors = load_json_lenient(result_path)
    if result is None:
        print("ERROR result.json unparseable: %s" % result_path)
        return 2

    assessment = result.get("assessment", [])
    if not isinstance(assessment, list):
        assessment = []
    per_dim = {}
    per_source = {}
    for item in assessment:
        if not isinstance(item, dict):
            continue
        dim = item.get("dimension") or "unknown"
        sid = item.get("source_id") or "unknown"
        per_dim.setdefault(dim, []).append(item)
        per_source.setdefault(sid, []).append(item)

    dim_grades = {k: _dim_grade(v) for k, v in sorted(per_dim.items())}
    source_grades = {k: _dim_grade(v) for k, v in sorted(per_source.items())}
    overall = _overall_grade(dim_grades, source_grades)

    result["grades"] = {
        "per_dimension": dim_grades,
        "per_source": source_grades,
        "overall": overall,
    }
    # 若关键证据缺失，同步下调 status
    status = result.get("status")
    if status in ("ok", "partial") and overall in (
            "insufficient_evidence", "unclear"):
        result["status"] = "partial"
    result.setdefault("change_log", []).append({
        "time_utc": now_utc_iso(), "event": "grade",
        "detail": "overall=%s; dims=%s" % (overall, json.dumps(dim_grades, ensure_ascii=False)),
    })

    atomic_write_json(Path(result_path), result)
    print("grade: overall=%s; dimensions=%s" % (overall, json.dumps(dim_grades, ensure_ascii=False)))
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(prog="litqa.py", description="litqa-veritas 证据工具")
    sub = p.add_subparsers(dest="cmd", required=True)
    lo = sub.add_parser("locators", help="生成稳定段落定位")
    lo.add_argument("--input", default=None)
    lo.add_argument("--work", default=None)
    lo.set_defaults(func=locators_cmd)
    ve = sub.add_parser("verify", help="逐字引文核验")
    ve.add_argument("--work", default=None)
    ve.add_argument("--result", default=None)
    ve.set_defaults(func=verify_cmd)
    gr = sub.add_parser("grade", help="阈值聚合分级")
    gr.add_argument("--result", default=None)
    gr.set_defaults(func=grade_cmd)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
