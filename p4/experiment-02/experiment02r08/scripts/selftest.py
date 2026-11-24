#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feasibility Gate Review — 交付前自检（S5）。

自检不是"再读一遍自己写的字"，而是对产物做可失败的机器校验，重点有三类：
  1. 结构：契约字段、七类维度、枚举值、修订链；
  2. 溯源：每条 verdict 需要证据的 finding，其 quote 必须是 /input 中对应文件里的
     逐字片段（空白归一后子串匹配；支持 JSON 片段定位与行区间）；
  3. 诚实：无占位符、无"未写当满足"、缺口有 limitations、对照分数不由本 Skill 生成。

用法：
    python3 scripts/selftest.py --input /input --output /app/output
    python3 scripts/selftest.py --output /app/output --strict   # 有 FAIL 时退出码 1

退出码默认 0：报告写入 <output>/selftest_report.json，FAIL 不阻断流水线。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

DIMS = ("assumptions_variables", "controls", "equipment_materials", "time_cost",
        "parameter_ranges", "risks_exceptions", "ethics_safety")
VERDICTS = {"ok", "conditional", "blocked", "missing", "unconfirmed", "conflict"}
SEVERITIES = {"blocker", "major", "minor", "info"}
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}
NEED_EVIDENCE = {"ok", "conditional", "blocked", "conflict"}
PLACEHOLDERS = ("TODO", "TBD", "待补充", "待填写", "<fill", "xxx", "XXX", "???")
REQUIRED_TOP = ("case_id", "status", "review", "sources", "uncertainty", "pending_human",
                "change_log", "limitations")
ASSERTIVE_WORDS = ("默认满足", "默认已具备", "视为已满足", "假定满足", "假定已具备")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except OSError as exc:
        return None, "unreadable: %s" % exc
    except json.JSONDecodeError as exc:
        return None, "bad_json: %s@%s" % (exc.msg, exc.pos)


def norm_ws(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def find_file(input_dir, name):
    if not name:
        return None
    p = os.path.join(input_dir, name)
    if os.path.isfile(p):
        return p
    base = os.path.basename(name)
    for root, dirs, files in os.walk(input_dir):
        for f in files:
            if f.lower() == base.lower():
                return os.path.join(root, f)
    return None


def read_text(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def json_at_path(text, fragment):
    """按 a.b[0].c 风格路径取 JSON 子对象；失败返回 None。"""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    tokens = re.findall(r"([^.\[\]]+)|\[(\d+)\]", fragment)
    if not tokens:
        return obj
    node = obj
    for key, idx in tokens:
        if idx:
            if not isinstance(node, list) or int(idx) >= len(node):
                return None
            node = node[int(idx)]
        else:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
    return node


def verify_quote(input_dir, locator, quote):
    """返回 {status, detail}；status: exact_path|in_file|fragmented|not_found|unverifiable。"""
    quote = (quote or "").strip()
    if not quote:
        return {"status": "unverifiable", "detail": "quote 为空"}
    if not input_dir or not os.path.isdir(input_dir):
        return {"status": "unverifiable", "detail": "输入目录不可用，无法核验引文"}

    loc = (locator or "").strip()
    file_part, _, fragment = loc.partition("#")
    path = find_file(input_dir, file_part) if file_part else None
    text = read_text(path) if path else None

    if text is None:
        # 无有效文件定位：退化为在全部文本材料中检索，并如实标注。
        hit = scan_all(input_dir, quote)
        if hit:
            return {"status": "in_file", "detail": "locator 未解析到具体文件，引文在 %s 中命中" % hit}
        return {"status": "not_found", "detail": "locator 文件不存在且全量材料未命中: %s" % (file_part or loc)}

    parts = [p for p in re.split(r"[…\.]{2,}", quote) if len(p.strip()) >= 2] or [quote]

    segments = []
    if fragment:
        obj = json_at_path(text, fragment)
        if obj is not None:
            segments.append(norm_ws(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)))
        m = re.match(r"^L(\d+)(?:-L?(\d+))?$", fragment)
        if m:
            lines = text.splitlines()
            a = max(1, int(m.group(1)))
            b = int(m.group(2)) if m.group(2) else a
            segments.append(norm_ws(" ".join(lines[a - 1:b])))
    segments.append(norm_ws(text))

    for seg in segments:
        if all(norm_ws(p) in seg for p in parts):
            kind = "fragmented" if len(parts) > 1 else ("exact_path" if seg is not segments[-1] else "in_file")
            return {"status": kind, "detail": "命中 %s#%s" % (os.path.basename(path), fragment or "-")}
    return {"status": "not_found", "detail": "引文不在 %s 的定位片段或全文内" % os.path.basename(path)}


def scan_all(input_dir, quote):
    parts = [p for p in re.split(r"[…\.]{2,}", quote) if len(p.strip()) >= 2] or [quote]
    for root, dirs, files in os.walk(input_dir):
        for f in files:
            if os.path.splitext(f)[1].lower() not in (".md", ".txt", ".json", ".jsonl", ".csv"):
                continue
            txt = read_text(os.path.join(root, f))
            if txt and all(norm_ws(p) in norm_ws(txt) for p in parts):
                return f
    return None


def iter_strings(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_strings(v, "%s[%d]" % (path, i))
    elif isinstance(obj, str):
        yield path, obj


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def replay_digest(result):
    clone = {k: v for k, v in result.items() if k not in ("runtime", "replay_digest")}
    return hashlib.sha256(canonical(clone).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 检查项
# --------------------------------------------------------------------------- #
def c01_parse(ctx):
    r = ctx["result"]
    if r is None:
        return False, "result.json 缺失或不可解析: %s" % ctx["load_error"]
    missing = [k for k in REQUIRED_TOP if k not in r]
    return (not missing), ("缺少顶层字段: " + ", ".join(missing)) if missing else "顶层字段齐备"


def c02_status(ctx):
    r = ctx["result"]
    st = r.get("status")
    if st not in STATUSES:
        return False, "status 非法: %r" % st
    if st in ("partial", "insufficient_evidence", "input_error"):
        if not (r.get("uncertainty") or r.get("limitations")):
            return False, "非 ok 状态必须在 uncertainty/limitations 说明缺口"
    if st == "ok" and (r.get("errors") or []):
        return None, "status=ok 但 errors 非空，请确认是否应降级为 partial"
    return True, "status=%s" % st


def c03_case_id(ctx):
    cid = ctx["result"].get("case_id")
    if not cid:
        return False, "case_id 为空"
    if cid == "unknown":
        return None, "case_id=unknown（输入未提供标识，已在 uncertainty 注明即可）"
    return True, "case_id=%s" % cid


def c04_contract(ctx):
    r, led = ctx["result"], ctx["ledger"]
    fields = ((led or {}).get("output_contract") or {}).get("fields") or {}
    if not fields:
        return None, "输入未提供 output_contract，跳过回填核验"
    miss = [k for k in fields if k not in r and k not in (r.get("contract_echo") or {})]
    return (not miss), ("未回填契约字段: " + ", ".join(miss)) if miss else "契约字段全部回填(%d)" % len(fields)


def c06_dimensions(ctx):
    r = ctx["result"]
    review = r.get("review") or {}
    need = 2 if r.get("status") == "ok" else 1
    bad, counts = [], {}
    for d in DIMS:
        items = review.get(d) if isinstance(review.get(d), list) else []
        counts[d] = len(items)
        if len(items) < need:
            bad.append("%s=%d<%d" % (d, len(items), need))
    detail = "各维条数: " + ", ".join("%s:%d" % (k, v) for k, v in counts.items())
    return (not bad), detail + ("; 不足: " + ", ".join(bad) if bad else "")


def c07_finding_shape(ctx):
    r = ctx["result"]
    bad = []
    for d in DIMS:
        for i, f in enumerate((r.get("review") or {}).get(d) or []):
            if not isinstance(f, dict):
                bad.append("%s[%d] 非对象" % (d, i))
                continue
            if not f.get("id"):
                bad.append("%s[%d] 缺 id" % (d, i))
            if f.get("verdict") not in VERDICTS:
                bad.append("%s[%d] verdict=%r" % (d, i, f.get("verdict")))
            if f.get("severity") not in SEVERITIES:
                bad.append("%s[%d] severity=%r" % (d, i, f.get("severity")))
            if not str(f.get("claim") or "").strip():
                bad.append("%s[%d] claim 为空" % (d, i))
    return (not bad), ("; ".join(bad[:8]) if bad else "finding 结构与枚举合法")


def c09_evidence(ctx):
    r = ctx["result"]
    total, checked, failed, missing = 0, 0, [], []
    for d in DIMS:
        for f in (r.get("review") or {}).get(d) or []:
            if not isinstance(f, dict) or f.get("verdict") not in NEED_EVIDENCE:
                continue
            total += 1
            ev = f.get("evidence") or []
            if not ev:
                missing.append(str(f.get("id")))
                continue
            for e in ev:
                checked += 1
                res = verify_quote(ctx["input"], e.get("locator"), e.get("quote"))
                if res["status"] == "not_found":
                    failed.append("%s@%s" % (f.get("id"), e.get("locator")))
    if total == 0:
        return False, "没有任何需要证据的 finding"
    ok = not missing and not failed
    detail = "需证据 finding=%d, 引文核验=%d, 缺证据=%d, 未命中=%d" % (total, checked, len(missing), len(failed))
    if missing:
        detail += "; 缺证据: " + ", ".join(missing[:6])
    if failed:
        detail += "; 未命中: " + ", ".join(failed[:6])
    return ok, detail


def c10_arithmetic(ctx):
    r = ctx["result"]
    ar = r.get("arithmetic") or {}
    deriv = ar.get("derivations") or []
    bad = []
    for d in DIMS:
        for f in (r.get("review") or {}).get(d) or []:
            if isinstance(f, dict) and f.get("evidence_grade") == "E2":
                a = f.get("arithmetic") or {}
                if a.get("expr") in (None, "") or a.get("result") is None:
                    bad.append(str(f.get("id")))
    if bad:
        return False, "E2 判断缺 expr/result: " + ", ".join(bad[:6])
    if not deriv and not (ar.get("not_computable")):
        return False, "arithmetic.derivations 为空且未说明 not_computable"
    return True, "derivations=%d, not_computable=%d" % (len(deriv), len(ar.get("not_computable") or []))


def c11_no_assumption(ctx):
    r = ctx["result"]
    hits = []
    for d in DIMS:
        for f in (r.get("review") or {}).get(d) or []:
            if not isinstance(f, dict) or f.get("verdict") != "ok":
                continue
            claim = str(f.get("claim") or "")
            if any(w in claim for w in ASSERTIVE_WORDS):
                hits.append(str(f.get("id")) + ":" + claim[:40])
            if not (f.get("evidence") or []):
                hits.append(str(f.get("id")) + ":verdict=ok 但无证据")
    return (not hits), ("疑似'未写当满足': " + "; ".join(hits[:6])) if hits else "无'未写当满足'迹象"


def c12_derivations(ctx):
    ar = ctx["result"].get("arithmetic") or {}
    deriv = ar.get("derivations") or []
    nc = ar.get("not_computable") or []
    if not deriv and not nc:
        return False, "既无算式也未声明不可计算"
    units = [d for d in deriv if d.get("unit") in (None, "")]
    return (not units), ("算式缺 unit: " + str(len(units))) if units else "算式=%d, 不可计算声明=%d" % (len(deriv), len(nc))


def c13_comparison(ctx):
    plans = (ctx["ledger"] or {}).get("plans") or []
    comp = ctx["result"].get("plan_comparison") or []
    if len(plans) >= 2 and len(comp) < 2:
        return False, "输入含 %d 个方案但 plan_comparison=%d" % (len(plans), len(comp))
    if len(plans) == 1:
        return True, "单方案，无需排序比较"
    return True, "plan_comparison=%d" % len(comp)


def c14_confirmation(ctx):
    r, led = ctx["result"], ctx["ledger"] or {}
    conf = r.get("confirmation") or {}
    miss = [k for k in ("safety", "ethics", "equipment_availability", "expert_confirmation") if k not in conf]
    if miss:
        return False, "confirmation 缺键: " + ", ".join(miss)
    ethics_relevant = bool((led.get("ethics") or {}).get("value")) or bool((led.get("samples") or {}).get("items"))
    if ethics_relevant and not (conf.get("ethics") or []):
        return False, "输入涉及伦理/样本但 ethics 确认为空"
    return True, "确认项齐备（伦理相关=%s）" % ethics_relevant


def c15_gaps(ctx):
    r = ctx["result"]
    keys = ("errors", "degradations", "uncertainty", "pending_human", "limitations")
    bad = [k for k in keys if not isinstance(r.get(k), list)]
    if bad:
        return False, "缺列表字段: " + ", ".join(bad)
    if r.get("status") != "ok" and not (r.get("uncertainty") or r.get("limitations")):
        return False, "非 ok 状态未说明缺口"
    return True, "缺口字段齐备"


def c16_changelog(ctx):
    log = ctx["result"].get("change_log") or []
    if not log:
        return False, "change_log 为空"
    revs = [x.get("rev") for x in log if isinstance(x, dict)]
    if any(not isinstance(x, int) for x in revs):
        return False, "change_log.rev 非整数"
    increasing = all(revs[i] < revs[i + 1] for i in range(len(revs) - 1))
    if not increasing:
        return False, "change_log rev 非严格递增: %s" % revs
    if ctx["result"].get("status") == "ok" and len(revs) < 2:
        return None, "ok 状态仅 1 条修订，建议保留骨架+增量的修订链"
    return True, "修订 %d 条: %s" % (len(revs), revs)


def c17_rubric(ctx):
    r = ctx["result"]
    cov = r.get("rubric_coverage") or {}
    need = {"actual_effect": 4, "usability_gain": 3, "innovation_gain": 3,
            "reliability": 3, "ecosystem": 3}
    bad = []
    for dim, n in need.items():
        pts = (cov.get(dim) or {}).get("points") or {}
        if len(pts) < n:
            bad.append("%s points=%d<%d" % (dim, len(pts), n))
            continue
        for name, spec in pts.items():
            if not isinstance(spec, dict) or not spec.get("where") or not spec.get("check"):
                bad.append("%s.%s 缺 where/check" % (dim, name))
    return (not bad), ("; ".join(bad[:6]) if bad else "5 维 %d 个评分点均有落点与自检项" %
                       sum(len(((cov.get(d) or {}).get("points") or {})) for d in need))


def c18_placeholder(ctx):
    hits = []
    for path, s in iter_strings(ctx["result"]):
        for tok in PLACEHOLDERS:
            if tok in s:
                hits.append(path + "~" + tok)
                break
    return (not hits), ("发现占位符: " + ", ".join(hits[:8])) if hits else "无占位符"


def c20_artifacts(ctx):
    arts = ctx["result"].get("artifacts") or []
    if not arts:
        return None, "未登记产物（可选）"
    missing = []
    for a in arts:
        p = a.get("path") if isinstance(a, dict) else str(a)
        if not p:
            continue
        full = p if os.path.isabs(p) else os.path.join(ctx["output"], p)
        if not os.path.exists(full):
            missing.append(p)
    return (not missing), ("产物不存在: " + ", ".join(missing)) if missing else "产物 %d 个均存在" % len(arts)


def c19_replay(ctx):
    d = replay_digest(ctx["result"])
    ctx["digest"] = d
    stored = ctx["result"].get("replay_digest")
    if stored and stored != d:
        return None, "replay_digest 已过期（骨架后被改动），用 --seal 更新为 %s" % d
    if not stored:
        return None, "尚未封装 replay_digest，用 --seal 写入 %s" % d
    return True, "digest=%s（同输入同口径可复跑比对）" % d


def c21_baseline(ctx):
    bc = ctx["result"].get("baseline_comparison") or {}
    if bc.get("provided_by") != "platform":
        return False, "baseline_comparison 必须声明由平台提供"
    if re.search(r'"score"\s*:\s*\d|分值\s*\d', canonical(bc)):
        return False, "本 Skill 不得写入对照分数"
    return True, "对照分数未由本 Skill 生成"


CHECKS = [
    ("C01", "result.json 存在且字段齐备", c01_parse),
    ("C02", "status 取值与诚实性", c02_status),
    ("C03", "case_id 可追溯", c03_case_id),
    ("C04", "output_contract 字段回填", c04_contract),
    ("C06", "七类维度逐项覆盖", c06_dimensions),
    ("C07", "finding 结构与枚举", c07_finding_shape),
    ("C09", "证据 locator+逐字 quote 核验", c09_evidence),
    ("C10", "E2 判断带算式", c10_arithmetic),
    ("C11", "无'未写当满足'", c11_no_assumption),
    ("C12", "算术派生可复核", c12_derivations),
    ("C13", "多方案比较", c13_comparison),
    ("C14", "确认项与伦理相关", c14_confirmation),
    ("C15", "缺口与降级说明", c15_gaps),
    ("C16", "修订链单调", c16_changelog),
    ("C17", "评分维度覆盖表", c17_rubric),
    ("C18", "无占位符", c18_placeholder),
    ("C19", "复跑摘要", c19_replay),
    ("C20", "登记产物存在", c20_artifacts),
    ("C21", "对照分数不伪造", c21_baseline),
]


def main():
    ap = argparse.ArgumentParser(description="交付前自检")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--work", default=DEFAULT_WORK)
    ap.add_argument("--strict", action="store_true", help="有 FAIL 时退出码 1")
    ap.add_argument("--seal", action="store_true", help="把 replay_digest 写回 result.json，供复跑比对")
    args = ap.parse_args()

    result, err = load_json(os.path.join(args.output, "result.json"))
    ledger, _ = load_json(os.path.join(args.work, "case_ledger.json"))
    ctx = {"result": result if isinstance(result, dict) else None, "load_error": err,
           "input": args.input, "output": args.output, "ledger": ledger if isinstance(ledger, dict) else {}}

    rows = []
    for cid, title, fn in CHECKS:
        try:
            ok, detail = fn(ctx)
        except Exception as exc:  # 自检自身不得崩溃
            ok, detail = False, "check_error: %s" % exc
        rows.append({"id": cid, "title": title,
                     "result": "PASS" if ok is True else ("WARN" if ok is None else "FAIL"),
                     "detail": detail})

    n_pass = sum(1 for r in rows if r["result"] == "PASS")
    n_fail = sum(1 for r in rows if r["result"] == "FAIL")
    n_warn = len(rows) - n_pass - n_fail
    report = {
        "generated_by": "scripts/selftest.py",
        "case_id": (result or {}).get("case_id"),
        "status": (result or {}).get("status"),
        "summary": {"pass": n_pass, "fail": n_fail, "warn": n_warn, "total": len(rows)},
        "checks": rows,
        "replay_digest": replay_digest(result) if isinstance(result, dict) else None,
        "limits": ["自检只证明结构、引文与算术一致性，不证明实验方案本身科学正确。",
                   "基线对照分数由平台生成，本报告不包含增益数值。"],
    }
    try:
        os.makedirs(args.output, exist_ok=True)
        if args.seal and isinstance(result, dict) and report["replay_digest"]:
            result["replay_digest"] = report["replay_digest"]
            with open(os.path.join(args.output, "result.json"), "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
        with open(os.path.join(args.output, "selftest_report.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("write selftest_report failed: %s\n" % exc)

    for r in rows:
        print("%-4s %-5s %s | %s" % (r["id"], r["result"], r["title"], r["detail"][:110]))
    print("PASS=%d FAIL=%d WARN=%d digest=%s" % (n_pass, n_fail, n_warn, report["replay_digest"]))
    if args.strict and n_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
