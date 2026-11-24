#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""result.json 完整性自检，逐条对应评分维度；--fix 可补位缺省字段。

检查内容（error 阻断 / warn 提醒）：
  1. result.json 存在且为合法 JSON
  2. case_id 非空
  3. status 取值合法
  4. artifact.path 指向真实文件（status 为 done/partial 且已设 path 时）
  5. 七要素 element_map 齐全
  6. consistency_matrix.mapping 非空（目标↔内容↔路线↔指标 对应）
  7. 每条事实有 source_ref + quote，或显式 unmapped/conflict
  8. 每条事实有合法 tense（已完成/拟开展/外部）
  9. 无可核验修饰词（首次/国际领先/填补空白等）未标注来源
 10. sources 含 SHA256
 11. change_log 非空且首条为 skeleton
 12. version_boundary 四键齐全
 13. uncertainty / pending_human 存在

用法：
  python scripts/selftest.py [--result /app/output/result.json] [--work /app/work] [--fix]
"""
import argparse
import json
import os
import sys

SEVEN_ELEMENTS = [
    "立项依据", "科学问题", "研究目标", "研究内容",
    "技术路线", "创新点", "研究基础与可行性"
]
STATUS_ALLOWED = {"partial", "done", "failed", "insufficient_evidence", "in_progress"}
TENSE_ALLOWED = {"completed", "planned", "external", "n/a"}
INFLATION_WORDS = ["首次", "国际领先", "国内领先", "填补空白", "颠覆性", "重大突破", "世界一流"]


def resolve_dir(kind):
    plat = {"output": "/app/output", "work": "/app/work"}[kind]
    local = {"output": "output", "work": "work"}[kind]
    return plat if os.path.isdir(plat) else local


def load(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        return {"__error__": str(exc)}


def check(level, name, passed, message):
    return {"level": level, "name": name, "passed": passed, "message": message}


def run_checks(r):
    checks = []
    if "__error__" in r:
        return [check("error", "parse", False, "result.json 不可读: %s" % r["__error__"])]

    checks.append(check("error", "case_id", bool(r.get("case_id")),
                        "case_id 非空" if r.get("case_id") else "case_id 缺失"))

    st = r.get("status", "")
    checks.append(check("error", "status", st in STATUS_ALLOWED,
                        "status=%s 合法" % st if st in STATUS_ALLOWED else "status 非法: %r" % st))

    artifact = r.get("artifact") or {}
    art_path = artifact.get("path")
    if art_path and st in ("done", "partial"):
        checks.append(check("error", "artifact_exists", os.path.exists(art_path),
                            "artifact.path 存在" if os.path.exists(art_path)
                            else "artifact.path 不存在: %s" % art_path))
    elif not art_path and st == "done":
        checks.append(check("error", "artifact_path", False, "status=done 但 artifact.path 未设"))
    else:
        checks.append(check("warn", "artifact_path", True, "artifact.path 暂未设（partial 阶段可接受）"))

    element_map = r.get("element_map") or {}
    missing_el = [e for e in SEVEN_ELEMENTS if e not in element_map]
    checks.append(check("error", "seven_elements", not missing_el,
                        "七要素齐全" if not missing_el else "缺要素: %s" % ", ".join(missing_el)))

    cm = r.get("consistency_matrix") or {}
    mapping = cm.get("mapping") or []
    checks.append(check("warn", "consistency_matrix", bool(mapping),
                        "一致性矩阵 %d 条" % len(mapping) if mapping else "一致性矩阵为空"))

    fsmap = r.get("fact_source_map") or []
    bad_facts = []
    untensed = []
    inflation = []
    for i, f in enumerate(fsmap):
        if f.get("status") == "mapped":
            if not (f.get("quote") and f.get("source_ref") and f.get("locator")):
                bad_facts.append(i)
        if f.get("tense") not in TENSE_ALLOWED:
            untensed.append(i)
        claim = f.get("claim", "") or ""
        hit = [w for w in INFLATION_WORDS if w in claim]
        if hit and f.get("status") != "unmapped":
            inflation.append((i, hit))
    checks.append(check("error", "fact_evidence", not bad_facts,
                        "mapped 事实均有 quote+source_ref+locator" if not bad_facts
                        else "%d 条 mapped 事实缺证据字段: %s" % (len(bad_facts), bad_facts[:10])))
    checks.append(check("warn", "fact_tense", not untensed,
                        "所有事实有 tense 标注" if not untensed
                        else "%d 条事实缺 tense: %s" % (len(untensed), untensed[:10])))
    checks.append(check("warn", "no_inflation_unflagged", not inflation,
                        "无可核验修饰词未标注" if not inflation
                        else "发现可核验修饰词: %s" % inflation[:10]))

    sources = r.get("sources") or []
    no_sha = [s.get("path") for s in sources if isinstance(s, dict) and not s.get("sha256")]
    checks.append(check("warn", "sources_sha256", not no_sha,
                        "sources 均含 SHA256" if not no_sha else "缺 SHA256: %s" % no_sha[:5]))

    clog = r.get("change_log") or []
    first_ok = bool(clog) and clog[0].get("action") == "skeleton"
    checks.append(check("error", "change_log", bool(clog) and first_ok,
                        "change_log 完整且首条为 skeleton" if bool(clog) and first_ok
                        else "change_log 缺失或首条非 skeleton"))

    vb = r.get("version_boundary") or {}
    vb_keys = ["modification_diff", "manual_confirm", "material_gaps", "anomalies"]
    missing_vb = [k for k in vb_keys if k not in vb]
    checks.append(check("error", "version_boundary", not missing_vb,
                        "version_boundary 四键齐全" if not missing_vb
                        else "version_boundary 缺: %s" % ", ".join(missing_vb)))

    has_u = "uncertainty" in r
    has_p = "pending_human" in r
    checks.append(check("warn", "uncertainty_pending", has_u and has_p,
                        "uncertainty/pending_human 存在" if has_u and has_p
                        else "缺 uncertainty 或 pending_human"))
    return checks


def ensure_fix(r):
    r.setdefault("case_id", "unknown-case")
    r.setdefault("status", "partial")
    r.setdefault("artifact", {"path": None, "kind": None, "description": ""})
    r.setdefault("fact_source_map", [])
    r.setdefault("element_map", {})
    r.setdefault("consistency_matrix", {"mapping": [], "issues": []})
    r.setdefault("sources", [])
    r.setdefault("uncertainty", [])
    r.setdefault("pending_human", [])
    r.setdefault("change_log", [])
    r.setdefault("version_boundary", {
        "modification_diff": [], "manual_confirm": [], "material_gaps": [], "anomalies": []
    })
    vb = r["version_boundary"]
    for k in ("modification_diff", "manual_confirm", "material_gaps", "anomalies"):
        vb.setdefault(k, [])
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--result", default=None, help="result.json 路径")
    ap.add_argument("--work", default=None, help="工作目录")
    ap.add_argument("--fix", action="store_true", help="补位缺省字段后写回")
    a = ap.parse_args(argv)

    rp = a.result or os.path.join(resolve_dir("output"), "result.json")
    workdir = a.work or resolve_dir("work")
    r = load(rp)
    checks = run_checks(r)

    if a.fix and "__error__" not in r:
        r = ensure_fix(r)
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        checks = run_checks(r)

    passed = all(not (c["level"] == "error" and not c["passed"]) for c in checks)
    report = {"passed": bool(passed), "checks": checks, "result_path": rp}
    # 将自检结果写入 result.json（若无解析错误）
    if "__error__" not in r:
        r["selftest"] = report
        try:
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "selftest_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
