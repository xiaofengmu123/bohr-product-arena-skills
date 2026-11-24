#!/usr/bin/env python3
"""校验 result.json 结构与质量，输出报告；退出码 0=通过，非 0=存在问题。

校验项：JSON 可解析、case_id 匹配、output_contract 字段齐全、status 合法、
change_log 非空、sources/facts_sources 逐条含 locator+quote、来源可回指材料、
禁词扫描、结果段因果语气提示。--selftest 用临时夹具自检校验器本身。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

VALID_STATUS = {"in_progress", "partial", "complete", "failed", "insufficient_evidence"}

# 无据/空话措辞：除非材料原句逐字引用并标出处，否则应删除或降级
FORBIDDEN_TERMS = [
    "首次", "国际领先", "世界领先", "填补空白", "颠覆性", "革命性", "重大突破",
    "突破性", "首创", "全球第一", "世界第一", "史无前例", "独一无二", "开创性",
    "first-in-class", "world-leading", "groundbreaking", "revolutionary", "unprecedented",
    "best-in-class", "breakthrough",
]

# 结果段不应出现的因果/解释性动词（属讨论段职责），用于提示人工复核
CAUSAL_VERBS_RESULTS = ["导致", "引起", "说明", "证明", "证实", "意味着", "揭示", "阐明", "因此", "所以"]

RESULTS_MARKERS = ("## Results", "## results", "## 结果", "### Results", "## 实验结果")
DISCUSSION_MARKERS = ("## Discussion", "## discussion", "## 讨论", "### Discussion")


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def read_text(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def scan_forbidden(text):
    hits = []
    for term in FORBIDDEN_TERMS:
        for m in re.finditer(re.escape(term), text):
            line = text[:m.start()].count("\n") + 1
            hits.append({"term": term, "line": line})
    return hits


def split_sections(text):
    """把 manuscript 切成 results / discussion / other。"""
    results, discussion, other = [], [], []
    cur = "other"
    for line in text.splitlines():
        if any(m in line for m in RESULTS_MARKERS):
            cur = "results"
        elif any(m in line for m in DISCUSSION_MARKERS):
            cur = "discussion"
        if cur == "results":
            results.append(line)
        elif cur == "discussion":
            discussion.append(line)
        else:
            other.append(line)
    return "\n".join(results), "\n".join(discussion), "\n".join(other)


def check(argv=None):
    ap = argparse.ArgumentParser(description="Validate result.json structure and quality")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--summary", default="/app/work/input_summary.json")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings (forbidden terms, missing locator/quote, causal verbs in Results) as failures too")
    a = ap.parse_args(argv)

    problems = []
    notes = []

    if not os.path.exists(a.result):
        return {
            "ok": False, "fatal": "result.json not found",
            "problems": [{"severity": "error", "msg": "result.json not found", "path": a.result}],
            "notes": [],
        }

    try:
        result = load_json(a.result)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "ok": False, "fatal": "result.json unparseable",
            "problems": [{"severity": "error", "msg": str(e)}],
            "notes": [],
        }

    summary = {}
    if os.path.exists(a.summary):
        try:
            summary = load_json(a.summary)
        except (OSError, json.JSONDecodeError) as e:
            notes.append({"severity": "warn", "msg": "summary unreadable: %s" % e})

    def add(severity, msg, **kw):
        problems.append({"severity": severity, "msg": msg, **kw})

    # 1. case_id
    cid = result.get("case_id")
    if not cid:
        add("error", "missing case_id")
    elif summary.get("case_id") and cid != summary.get("case_id"):
        add("error", "case_id mismatch", expect=summary.get("case_id"), got=cid)

    # 2. output_contract 字段
    contract = summary.get("output_contract") or {}
    missing_keys = [k for k in contract if k not in result]
    if missing_keys:
        add("error", "missing output_contract fields", keys=missing_keys)
    else:
        notes.append({"severity": "ok", "msg": "contract fields present: %s" % ", ".join(sorted(contract))})

    # 3. status
    status = result.get("status")
    if status not in VALID_STATUS:
        add("error", "invalid/missing status", status=status)

    # 4. change_log
    if not isinstance(result.get("change_log"), list) or not result.get("change_log"):
        add("warn", "change_log empty or missing")

    # 5. sources / facts_sources locator+quote
    for key in ("sources", "facts_sources"):
        arr = result.get(key)
        if arr is None:
            continue
        if not isinstance(arr, list):
            add("warn", "%s should be a list" % key)
            continue
        for i, item in enumerate(arr):
            if not isinstance(item, dict):
                add("warn", "%s[%d] not an object" % (key, i))
                continue
            if not item.get("locator"):
                add("warn", "%s[%d] missing locator" % (key, i))
            if not item.get("quote"):
                add("warn", "%s[%d] missing verbatim quote" % (key, i))

    # 6. 来源可回指材料
    mat_files = {m.get("file") for m in summary.get("materials", []) if m.get("file")}
    for key in ("sources", "facts_sources"):
        for item in (result.get(key) or []):
            if not isinstance(item, dict):
                continue
            loc = item.get("locator", "")
            if loc and mat_files and not any(loc.startswith(mf) for mf in mat_files):
                add("warn", "%s locator not in material inventory" % key, locator=loc)

    # 7. 禁词扫描 + 结果段语气提示（manuscript 正文）
    deliv = result.get("deliverable")
    ms_path = None
    if isinstance(deliv, dict):
        ms_path = deliv.get("file") or deliv.get("path")
    elif isinstance(deliv, str) and deliv.endswith((".md", ".txt")):
        ms_path = deliv
    if ms_path:
        if not os.path.isabs(ms_path):
            ms_path = os.path.join(os.path.dirname(a.result), ms_path)
        if os.path.exists(ms_path):
            ms_text = read_text(ms_path)
            for h in scan_forbidden(ms_text):
                add("warn", "forbidden/unsupported term in manuscript (verbatim source quote allowed only)", term=h["term"], line=h["line"])
            res_txt, _disc_txt, _other = split_sections(ms_text)
            for verb in CAUSAL_VERBS_RESULTS:
                if verb in res_txt:
                    add("warn", "causal/interpretive verb in Results section, move to Discussion or weaken", verb=verb)
        else:
            add("warn", "deliverable file not found", path=ms_path)
    else:
        notes.append({"severity": "info", "msg": "no editable manuscript path detected; deliverable may be inline"})

    # 8. limitations 与 status 一致性
    if status == "failed" and not result.get("limitations"):
        add("warn", "status=failed but limitations empty")

    err = [p for p in problems if p["severity"] == "error"]
    warn = [p for p in problems if p["severity"] == "warn"]
    ok = (not err) if not a.strict else (not problems)
    report = {
        "ok": ok,
        "strict": a.strict,
        "errors": len(err),
        "warnings": len(warn),
        "problems": problems,
        "notes": notes,
    }
    return report


def selftest():
    """临时夹具：构造合法 result + manuscript，验证校验器能跑通且能识别常见问题。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="check_result_selftest_")
    result = {
        "case_id": "selftest", "deliverable": {"file": "results_discussion.md"},
        "facts_sources": [{"locator": "data.txt#L1", "quote": "mean = 3.2 (SD 0.4)", "used_in": "## Results"}],
        "version_boundaries": {}, "status": "complete",
        "sources": [], "uncertainty": [], "pending_human": [],
        "change_log": [{"ts": "t", "action": "x", "detail": "y"}], "limitations": [],
    }
    ms = "## Results\nMean was 3.2 (SD 0.4).\n\n## Discussion\nThis suggests a mechanism.\n"
    rp = os.path.join(tmp, "result.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(tmp, "results_discussion.md"), "w", encoding="utf-8") as fh:
        fh.write(ms)
    rep = check(["--result", rp, "--summary", os.path.join(tmp, "no_summary.json")])
    assert rep["ok"], rep
    print(json.dumps({"selftest": "ok", "notes": rep["notes"]}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    if argv and "--selftest" in argv:
        return selftest()
    rep = check(argv)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
