#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文献综述 Skill 的自检脚本（仅 Python 标准库）。

用法：
  python -B scripts/selftest.py --input /input --output /app/output [--determinism] [--report /app/work/selftest_report.json]

行为：
  - 逐项检查 result.json 契约、主产物、证据回链、筛选顺序、status 一致性、
    伪造 DOI 标记、manifest 哈希一致性；
  - 可选 `--determinism`：把 Phase 0 骨架跑两遍，断言 result.json 字节级一致；
  - 每项输出 [PASS]/[FAIL]/[WARN] 并映射到评分维度；
  - 存在 FAIL 时退出码为 1（无 FAIL 为 0），WARN 不影响退出码。
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

import core_impl as core

DIM = {
    "completion": "任务完成度",
    "correctness": "结果正确性",
    "completeness": "结果完整性",
    "verifiability": "结果可核验性",
    "usability": "调用易用性",
    "stability": "运行稳定性",
    "exception": "异常处理",
}

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
PLACEHOLDER_MARKERS = [
    "example.com/doi", "doi: tbd", "doi:tbd", "10.0000/placeholder",
    "10.xxxx/", "待补doi", "placeholder doi", "your-doi",
]


# ---------------------------------------------------------------------------
# 读取辅助
# ---------------------------------------------------------------------------
def load_output(output_dir):
    p = os.path.join(output_dir, "result.json")
    if not os.path.isfile(p):
        return None, ["result.json 不存在"]
    return core.read_json(p)


def load_json_lines(path):
    rows, errors = [], []
    if not os.path.isfile(path):
        return rows, ["文件不存在: " + path]
    text, _enc, err = core.read_text(path)
    if text is None:
        return rows, ["无法读取: " + (err or "未知错误")]
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as e:
            errors.append("第 %d 行 JSON 解析失败: %s" % (lineno, e))
    return rows, errors


def source_id_set(sources):
    """从 sources 字段提取 source_id 集合；sources 可为对象列表或字符串列表。"""
    out = set()
    if isinstance(sources, list):
        for s in sources:
            if isinstance(s, dict):
                for k in ("source_id", "id", "key", "ref_id", "filename"):
                    v = s.get(k)
                    if isinstance(v, str) and v.strip():
                        out.add(v.strip())
                        break
            elif isinstance(s, str) and s.strip():
                out.add(s.strip())
    return out


# ---------------------------------------------------------------------------
# 检查项：每项返回 (level, message)，level in {"pass","fail","warn"}
# ---------------------------------------------------------------------------
def chk_input(case_path):
    if case_path:
        return "pass", "case.json 已发现: " + case_path
    return "warn", "未发现 case.json（若输入目录确实无该文件则属输入缺失）"


def chk_result(result, errs):
    if result is None:
        return "fail", "result.json 缺失或不可解析: " + "; ".join(errs or ["未知错误"])
    return "pass", "result.json 为合法 JSON"


def chk_case_id(result):
    v = result.get("case_id") if isinstance(result, dict) else None
    if isinstance(v, str) and v.strip():
        return "pass", "case_id=" + v
    return "fail", "case_id 缺失或为空"


def chk_status(result):
    v = result.get("status") if isinstance(result, dict) else None
    if v in core.ALLOWED_STATUS:
        return "pass", "status=" + v
    return "fail", "status 非法: %r（允许 %s）" % (v, sorted(core.ALLOWED_STATUS))


def chk_contract(result, fields):
    if fields is None:
        return "warn", "输入未发现 output_contract，跳过契约字段检查"
    missing = [name for name, _d in fields if name not in result]
    if missing:
        return "fail", "契约字段缺失: " + ", ".join(missing)
    return "pass", "output_contract 全部 %d 个字段已回填" % len(fields)


def chk_honest(result):
    required = ["status", "sources", "uncertainty", "pending_human", "change_log"]
    missing = [k for k in required if k not in result]
    if missing:
        return "fail", "诚实字段缺失: " + ", ".join(missing)
    return "pass", "status/sources/uncertainty/pending_human/change_log 齐全"


def chk_artifacts(output_dir, result):
    names = ["review.md", "screening_log.json", "evidence_table.jsonl",
             "themes.md", "conflicts_and_gaps.md", "manifest.json"]
    missing = [n for n in names if not os.path.isfile(os.path.join(output_dir, n))]
    if missing:
        return "fail", "主产物缺失: " + ", ".join(missing)
    return "pass", "6 个主产物（含 manifest.json）齐全"


def chk_screening(output_dir):
    p = os.path.join(output_dir, "screening_log.json")
    data, errs = core.read_json(p)
    if data is None:
        return "fail", "screening_log.json 不可解析: " + "; ".join(errs)
    if "criteria" not in data:
        return "fail", "screening_log 缺少 criteria（筛选标准须先于决定写明）"
    if "decisions" not in data:
        return "fail", "screening_log 缺少 decisions"
    criteria = data.get("criteria") or {}
    decisions = data.get("decisions") or []
    defined = bool(criteria.get("defined_before_decisions", True))
    if decisions and not defined:
        return "fail", "已有筛选决定但 criteria.defined_before_decisions 为 false"
    if decisions and not (criteria.get("include") or criteria.get("exclude")
                          or criteria.get("population")):
        return "warn", "已有筛选决定但纳入/排除标准为空，需补写标准"
    return "pass", "筛选标准先于决定记录（decisions=%d）" % len(decisions)


def chk_evidence(output_dir):
    p = os.path.join(output_dir, "evidence_table.jsonl")
    rows, errs = load_json_lines(p)
    if errs:
        return "fail", "evidence_table.jsonl 存在坏行: " + "; ".join(errs[:3])
    if not rows:
        return "warn", "evidence_table.jsonl 为空（若 status=ok 则必须非空）"
    bad = []
    ids = set()
    for i, r in enumerate(rows, 1):
        for key in ("evidence_id", "source_id", "locator", "quote", "claim",
                    "strength", "field"):
            if key not in r or r[key] in (None, ""):
                bad.append("第 %d 行缺 %s" % (i, key))
        if r.get("strength") not in core.STRENGTH_LEVELS:
            bad.append("第 %d 行 strength 非法: %r" % (i, r.get("strength")))
        ids.add(r.get("evidence_id"))
    if bad:
        return "fail", "证据行字段问题: " + "; ".join(bad[:4])
    return "pass", "evidence_table 共 %d 行、%d 个唯一 evidence_id" % (len(rows), len(ids))


def chk_citations(output_dir):
    review_path = os.path.join(output_dir, "review.md")
    text, _enc, err = core.read_text(review_path)
    if text is None:
        return "fail", "review.md 不可读: " + (err or "未知错误")
    cited = set(re.findall(r"\[S(\d+)\]", text))
    if not cited:
        return "warn", "review.md 未发现 [S#] 引用（草稿为空或未标注）"
    rows, errs = load_json_lines(os.path.join(output_dir, "evidence_table.jsonl"))
    ids = {r.get("evidence_id") for r in rows}
    dangling = [c for c in cited if ("S" + c) not in ids]
    if dangling:
        return "fail", "review.md 引用无法解析回证据行: " + ", ".join(sorted(dangling)[:8])
    return "pass", "review.md 的 %d 个 [S#] 引用全部可解析" % len(cited)


def chk_sources(output_dir, result):
    rows, errs = load_json_lines(os.path.join(output_dir, "evidence_table.jsonl"))
    if errs:
        return "warn", "证据表有坏行，跳过 source 解析检查"
    result_sources = source_id_set((result or {}).get("sources"))
    screen_data, _e = core.read_json(os.path.join(output_dir, "screening_log.json"))
    screen_sources = set()
    if isinstance(screen_data, dict):
        screen_sources |= source_id_set(screen_data.get("sources"))
        for d in (screen_data.get("decisions") or []):
            if isinstance(d, dict):
                for k in ("source_id", "id", "ref_id"):
                    if isinstance(d.get(k), str) and d.get(k).strip():
                        screen_sources.add(d[k].strip())
    known = result_sources | screen_sources
    used = {r.get("source_id") for r in rows if r.get("source_id")}
    missing = sorted(s for s in used if s and s not in known)
    if not known and used:
        return "warn", "证据表使用 %d 个 source_id，但 sources/screening 未提供清单" % len(used)
    if missing:
        return "fail", "证据行 source_id 无法解析: " + ", ".join(missing[:8])
    return "pass", "证据行 source_id 均可解析（known=%d, used=%d）" % (len(known), len(used))


def chk_no_fabrication(output_dir, result):
    review_path = os.path.join(output_dir, "review.md")
    text, _enc, _err = core.read_text(review_path)
    text = text or ""
    blob = text + "\n" + json.dumps(result or {}, ensure_ascii=False)
    for marker in PLACEHOLDER_MARKERS:
        if marker.lower() in blob.lower():
            return "fail", "发现占位 DOI/伪造标记: " + marker
    # 若出现形如 DOI 的串但不符合真实 DOI 模式，标记（宽泛提示）
    for token in re.findall(r"doi[:\s=]*([^\s,;，。]+)", blob, flags=re.IGNORECASE):
        t = token.strip()
        if t and not DOI_PATTERN.search(t):
            return "warn", "疑似非法 DOI 串: " + t[:60]
    return "pass", "未发现占位/伪造 DOI 标记"


def chk_status_consistent(output_dir, result):
    st = result.get("status") if isinstance(result, dict) else None
    rows, _e = load_json_lines(os.path.join(output_dir, "evidence_table.jsonl"))
    uncertainty = result.get("uncertainty") or []
    pending = result.get("pending_human") or []
    if st == "ok":
        if not rows:
            return "fail", "status=ok 但 evidence_table 为空"
        if not (uncertainty or pending):
            return "warn", "status=ok 但 uncertainty/pending_human 均为空"
    elif st == "insufficient_evidence":
        if rows:
            return "warn", "status=insufficient_evidence 但存在证据行，需核对"
        if not (uncertainty or pending):
            return "fail", "status=insufficient_evidence 但未列缺口"
    elif st == "partial":
        if not (uncertainty or pending):
            return "warn", "status=partial 但未列缺口（应说明已完成/未完成项）"
    elif st == "input_error":
        if not (result.get("errors")):
            return "warn", "status=input_error 但 errors 为空"
    return "pass", "status 与产物完备度一致"


def chk_manifest(output_dir, result):
    p = os.path.join(output_dir, "manifest.json")
    data, errs = core.read_json(p)
    if data is None:
        return "fail", "manifest.json 不可解析: " + "; ".join(errs)
    files = data.get("files") or {}
    bad = []
    for name, expected in files.items():
        actual = core.sha256_file(os.path.join(output_dir, name))
        if actual is None:
            bad.append(name + " 无法读取")
        elif actual != expected:
            bad.append(name + " 哈希不一致")
    if bad:
        return "fail", "manifest 哈希不一致: " + "; ".join(bad[:4])
    return "pass", "manifest 覆盖 %d 个产物且哈希一致" % len(files)


def chk_determinism(input_dir):
    tmp = tempfile.mkdtemp(prefix="lr-det-")
    try:
        dirs = [os.path.join(tmp, "a"), os.path.join(tmp, "b")]
        hashes = []
        for d in dirs:
            out = os.path.join(d, "output")
            work = os.path.join(d, "work")
            core.build_skeleton(input_dir, out, work)
            hashes.append(core.sha256_file(os.path.join(out, "result.json")))
        if hashes[0] == hashes[1]:
            return "pass", "两次骨架 result.json 字节级一致（sha256=%s）" % hashes[0][:12]
        return "fail", "两次骨架 result.json 不一致"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 检查清单：id -> (维度, 描述, 函数)
# ---------------------------------------------------------------------------
def build_checks(case_path, result, errs, fields, output_dir, input_dir, do_determinism):
    checks = [
        ("C01", "usability", "case.json 可发现", lambda: chk_input(case_path)),
        ("C02", "usability", "result.json 合法", lambda: chk_result(result, errs)),
        ("C03", "usability", "case_id 存在", lambda: chk_case_id(result)),
        ("C04", "usability", "status 取值合法", lambda: chk_status(result)),
        ("C05", "usability", "contract 字段全回填", lambda: chk_contract(result, fields)),
        ("C06", "usability", "诚实字段齐全", lambda: chk_honest(result)),
        ("C07", "completion", "主产物齐全", lambda: chk_artifacts(output_dir, result)),
        ("C08", "completion", "筛选标准先于决定", lambda: chk_screening(output_dir)),
        ("C09", "correctness", "证据行字段与 strength 合法", lambda: chk_evidence(output_dir)),
        ("C10", "correctness", "review.md 引用可解析", lambda: chk_citations(output_dir)),
        ("C11", "verifiability", "source_id 可解析", lambda: chk_sources(output_dir, result)),
        ("C12", "correctness", "无伪造 DOI/数值标记", lambda: chk_no_fabrication(output_dir, result)),
        ("C13", "exception", "status 与完备度一致", lambda: chk_status_consistent(output_dir, result)),
        ("C14", "stability", "manifest 哈希一致", lambda: chk_manifest(output_dir, result)),
    ]
    if do_determinism:
        checks.append(("C15", "stability", "确定性（两次骨架一致）",
                       lambda: chk_determinism(input_dir)))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="文献综述 Skill 自检")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--determinism", action="store_true",
                        help="把 Phase 0 骨架跑两遍断言 result.json 一致")
    parser.add_argument("--report", default=None,
                        help="自检报告 JSON 输出路径（默认仅打印）")
    args = parser.parse_args(argv)

    case_path = core.find_case_file(args.input)
    case_data = core.read_json(case_path)[0] if case_path else None
    fields = core.contract_fields(core.find_output_contract(case_data)) \
        if (case_data is not None and core.find_output_contract(case_data) is not None) \
        else None
    result, errs = load_output(args.output)

    checks = build_checks(case_path, result, errs, fields, args.output, args.input,
                          args.determinism)

    rows = []
    n_fail = n_warn = 0
    print("== 文献综述 Skill 自检 ==")
    for cid, dim, desc, fn in checks:
        try:
            level, msg = fn()
        except Exception as e:  # noqa: BLE001
            level, msg = "fail", "检查执行异常: " + str(e)
        if level == "fail":
            n_fail += 1
        elif level == "warn":
            n_warn += 1
        tag = {"pass": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]"}[level]
        line = "%s %s %s(%s): %s" % (tag, cid, desc, DIM.get(dim, dim), msg)
        print(line)
        rows.append({"id": cid, "dimension": dim, "description": desc,
                     "level": level, "message": msg})

    print("== 汇总 ==")
    print("PASS=%d  WARN=%d  FAIL=%d" % (len(checks) - n_fail - n_warn, n_warn, n_fail))

    if args.report:
        core.write_json_atomic(args.report, {
            "result_status": (result or {}).get("status"),
            "checks": rows,
            "summary": {"pass": len(checks) - n_fail - n_warn,
                        "warn": n_warn, "fail": n_fail},
        })

    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
