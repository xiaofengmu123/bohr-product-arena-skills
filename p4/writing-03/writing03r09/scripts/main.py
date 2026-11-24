#!/usr/bin/env python3
"""学术格式与模板适配 Skill 的确定性执行器（仅 Python 标准库）。

子命令:
  scaffold  读取 case.json，落盘幂等的 partial 骨架（增量合并，不覆盖已有内容）
  verify    契约完整性 + 摘录可核验性 + 占位符自检，产出 verification_report.json

无子命令时按 scaffold -> verify 顺序执行完整流程。
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

STATUSES = ("partial", "complete", "insufficient_evidence", "failed", "input_error")

FALLBACK_CONTRACT = ["deliverable", "fact_source_map", "version_boundary"]

STD_FIELDS = {
    "deliverable": {},
    "fact_source_map": [],
    "version_boundary": {},
    "sources": [],
    "uncertainty": {
        "claims_requiring_confirmation": [],
        "conflicting_evidence": [],
        "material_gaps": [],
        "limitations": [],
    },
    "pending_human": [],
    "change_log": [],
    "errors": [],
    "degradations": [],
    "runtime": {},
}

TEXT_EXTS = (".txt", ".md", ".tex", ".rst", ".json", ".csv", ".html", ".xml", ".log")
MAX_FILE = 2 * 1024 * 1024  # 单文本文件上限 2MB
PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|TBD|PLACEHOLDER|LOREM)\b|待补|占位符", re.IGNORECASE)


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    """utf-8-sig 容错读取；坏 JSON 或不可读返回 (None, error_str)。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except OSError as e:
        return None, "无法读取 %s: %s" % (path, e)
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, "JSON 解析失败 %s: %s" % (path, e)


def contract_fields(case):
    """返回 output_contract 的字段名列表；缺失时用三段式兜底。"""
    if not isinstance(case, dict):
        return list(FALLBACK_CONTRACT)
    oc = case.get("output_contract")
    if isinstance(oc, dict):
        keys = [str(k) for k in oc.keys()]
        return keys or list(FALLBACK_CONTRACT)
    if isinstance(oc, list):
        keys = [str(x) for x in oc if isinstance(x, str)]
        return keys or list(FALLBACK_CONTRACT)
    return list(FALLBACK_CONTRACT)


def _copy(v):
    return json.loads(json.dumps(v))


def build_skeleton(case):
    """构造 partial 骨架；case 为 None 时给 input_error 最小骨架。"""
    skel = {"status": "partial", "case_id": None}
    if isinstance(case, dict):
        cid = case.get("case_id")
        skel["case_id"] = cid if isinstance(cid, str) else None
        for k in contract_fields(case):
            if k not in skel:
                skel[k] = _copy(STD_FIELDS[k]) if k in STD_FIELDS else None
        for k, v in STD_FIELDS.items():
            skel.setdefault(k, _copy(v))
    else:
        for k in FALLBACK_CONTRACT:
            if k not in skel:
                skel[k] = _copy(STD_FIELDS[k]) if k in STD_FIELDS else None
        for k, v in STD_FIELDS.items():
            skel.setdefault(k, _copy(v))
        skel["status"] = "input_error"
    skel["runtime"] = {"started": now_iso()}
    return skel


def _append_log(log, action, detail, ts=None):
    if not isinstance(log, list):
        log = []
    for e in log:
        if isinstance(e, dict) and e.get("action") == action and e.get("detail") == detail:
            return log
    log.append({"ts": ts or now_iso(), "action": action, "detail": detail})
    return log


def merge_result(existing, incoming):
    """幂等增量合并：已有非空值不覆盖；change_log 按 (action, detail) 去重追加。"""
    if not isinstance(existing, dict):
        return incoming
    merged = _copy(existing)
    for k, v in incoming.items():
        if k == "change_log":
            for e in (v or []):
                if isinstance(e, dict):
                    _append_log(merged.setdefault("change_log", []),
                                e.get("action"), e.get("detail"), e.get("ts"))
        elif k == "case_id":
            merged[k] = v  # 输入中的 case_id 为权威值
        elif k == "runtime":
            merged.setdefault(k, v)
        elif merged.get(k) in (None, "", [], {}):
            merged[k] = v
    return merged


def collect_material_text(roots):
    """收集材料文本（≤2MB/文件），返回 {abs_path: text}。"""
    texts = {}
    for root in roots or []:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for fn in files:
                p = os.path.join(dirpath, fn)
                if not fn.endswith(TEXT_EXTS):
                    continue
                try:
                    if os.path.getsize(p) > MAX_FILE:
                        continue
                    with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                        texts[p] = f.read()
                except OSError:
                    continue
    return texts


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def verify_quotes(result, texts):
    problems = []
    fsm = result.get("fact_source_map") or []
    if isinstance(fsm, dict):
        fsm = [fsm]
    for i, claim in enumerate(fsm):
        if not isinstance(claim, dict):
            continue
        quote = claim.get("quote")
        if not quote:
            continue
        q = _norm(quote)
        if not q:
            continue
        found = any(q in _norm(t) for t in texts.values())
        if not found:
            problems.append({
                "claim_id": claim.get("claim_id", i),
                "quote": quote,
                "locator": claim.get("locator"),
                "reason": "quote 无法在材料文本中定位",
            })
    return problems


def verify(result, case, material_texts):
    report = {"ok": True, "errors": [], "warnings": [], "checks": {}}
    cid = result.get("case_id")
    if not cid:
        report["errors"].append("缺 case_id")
        report["ok"] = False
    elif isinstance(case, dict):
        ecid = case.get("case_id")
        if ecid and cid != ecid:
            report["errors"].append("case_id 与 case.json 不一致: %r != %r" % (cid, ecid))
            report["ok"] = False
    for k in contract_fields(case):
        if k not in result:
            report["errors"].append("缺 output_contract 字段: %s" % k)
            report["ok"] = False
    if result.get("status") not in STATUSES:
        report["warnings"].append("status 非法: %r" % result.get("status"))
    unlocated = verify_quotes(result, material_texts)
    report["checks"]["unlocatable_quotes"] = unlocated
    if unlocated:
        report["errors"].append("有 %d 条 quote 无法定位" % len(unlocated))
        report["ok"] = False
    ph = [m for m in PLACEHOLDER_RE.findall(json.dumps(result, ensure_ascii=False))]
    if ph:
        report["warnings"].append("发现占位符标记: %s" % ", ".join(sorted(set(ph))))
    report["checks"]["placeholder_markers"] = sorted(set(ph))
    report["timestamp"] = now_iso()
    return report


def cmd_scaffold(args):
    os.makedirs(args.out, exist_ok=True)
    case, err = load_json(args.case)
    errors = []
    if err:
        errors.append(err)
    skel = build_skeleton(case)
    if errors:
        skel["errors"] = errors
        skel["status"] = "input_error" if not isinstance(case, dict) else skel["status"]
    out_path = os.path.join(args.out, "result.json")
    existing, _ = load_json(out_path)
    merged = merge_result(existing, skel)
    merged.setdefault("errors", [])
    for e in errors:
        if e not in merged["errors"]:
            merged["errors"].append(e)
    _append_log(merged["change_log"], "scaffold", "落盘 partial 骨架")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return 0


def cmd_verify(args):
    roots = args.material_root or ["/input", "/app/work"]
    result, rerr = load_json(args.result)
    if rerr:
        result = {}
    case, _ = load_json(args.case)
    texts = collect_material_text(roots)
    report = verify(result, case, texts)
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    if not report["ok"]:
        return 2
    if report["warnings"]:
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    sp = sub.add_parser("scaffold", help="落盘幂等 partial 骨架")
    sp.add_argument("--case", default="/input/case.json")
    sp.add_argument("--out", default="/app/output")
    vp = sub.add_parser("verify", help="契约与摘录自检")
    vp.add_argument("--result", default="/app/output/result.json")
    vp.add_argument("--case", default="/input/case.json")
    vp.add_argument("--report", default="/app/output/verification_report.json")
    vp.add_argument("--material-root", action="append", default=[])
    args = ap.parse_args(argv)
    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    # 无子命令：scaffold -> verify
    rc = cmd_scaffold(args)
    if rc != 0:
        return rc
    return cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
