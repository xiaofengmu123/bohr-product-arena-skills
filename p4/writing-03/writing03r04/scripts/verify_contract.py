#!/usr/bin/env python3
"""契约 + 摘录可核验性自检。

仅依赖 Python 标准库。检查 result.json 是否满足运行契约：
  - case_id 存在且与 case.json 一致
  - output_contract 全部字段 + status/sources/uncertainty/change_log 保留字段齐全
  - 无 TODO/占位符等未完成标记
  - fact_source_map 中每条 quote 能在材料全文定位；未定位者列入 unlocatable_quotes
退出码: 0 = 通过; 1 = 存在软警告(如摘录无法定位); 2 = 硬失败(缺字段/JSON 无效/占位符)。
"""
import argparse
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RESERVED = ["status", "sources", "uncertainty", "change_log"]
FALLBACK_CONTRACT = ["deliverable", "fact_source_map", "version_boundary"]
MARKERS = ["TODO", "FIXME", "TBD", "PLACEHOLDER", "XXX", "待补", "占位"]
MAX_MATERIAL_BYTES = 2 * 1024 * 1024
EXCLUDE_BASENAMES = {"case.json", "task.md", "result.json", "verification_report.json", "change_log.json"}


def _now_utc():
    return datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat(timespec="seconds")


def read_text_robust(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, ["read-failed: %s" % exc]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc), []
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), ["decode-fallback"]


def read_json_robust(path):
    text, errors = read_text_robust(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except json.JSONDecodeError as exc:
        return None, errors + ["json-invalid: %s" % exc]


def normalize(text):
    if text is None:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    return "".join(ch for ch in text if not ch.isspace())


def discover(root):
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = None
            out.append({"path": path, "rel": os.path.relpath(path, root), "bytes": size})
    return out


def load_corpus(roots):
    """加载材料全文用于 quote 定位；排除 case/task/result 等非材料文件。"""
    corpus = []
    seen = set()
    for root in roots:
        for f in discover(root):
            if f["bytes"] is not None and f["bytes"] > MAX_MATERIAL_BYTES:
                continue
            if os.path.basename(f["path"]).lower() in EXCLUDE_BASENAMES:
                continue
            key = os.path.abspath(f["path"])
            if key in seen:
                continue
            seen.add(key)
            text, _errors = read_text_robust(f["path"])
            if text:
                corpus.append({"rel": f["rel"], "path": f["path"], "norm": normalize(text)})
    return corpus


def locate_quote(quote, corpus):
    q = normalize(quote)
    if len(q) < 4:
        return None  # 过短无法可靠定位，跳过
    for c in corpus:
        if q in c["norm"]:
            return {"file": c["rel"], "path": c["path"]}
    return None


def collect_quotes(obj, item_id="?", out=None):
    """递归收集所有键名为 quote 的非空字符串，按 (item_id, quote) 去重。"""
    if out is None:
        out = []
    if isinstance(obj, dict):
        quote = obj.get("quote")
        if isinstance(quote, str) and quote.strip():
            key = (item_id, quote)
            if key not in out:
                out.append(key)
        for value in obj.values():
            collect_quotes(value, item_id, out)
    elif isinstance(obj, list):
        for value in obj:
            collect_quotes(value, item_id, out)
    return out


def run_checks(result, case_data, corpus):
    hard, warn, ok = [], [], []

    if result is None:
        return ["result.json 缺失或 JSON 无效"], [], [], []

    case_id = result.get("case_id")
    if not case_id:
        hard.append("missing case_id")
    else:
        ok.append("case_id present: %s" % case_id)

    if isinstance(case_data, dict):
        expected = case_data.get("case_id") or case_data.get("id") or case_data.get("caseId")
        if expected and case_id != expected:
            hard.append("case_id mismatch: result=%s case=%s" % (case_id, expected))
        elif expected:
            ok.append("case_id matches case.json")

    if isinstance(case_data, dict) and isinstance(case_data.get("output_contract"), dict):
        contract_keys = list(case_data["output_contract"].keys())
    else:
        contract_keys = FALLBACK_CONTRACT
    result_contract = result.get("output_contract") if isinstance(result.get("output_contract"), dict) else {}
    for key in contract_keys:
        if key in result or key in result_contract:
            ok.append("contract field present: %s" % key)
        else:
            hard.append("missing contract field: %s" % key)

    for key in RESERVED:
        if key in result:
            ok.append("reserved field present: %s" % key)
        else:
            hard.append("missing reserved field: %s" % key)

    raw = json.dumps(result, ensure_ascii=False)
    upper = raw.upper()
    found_markers = [m for m in MARKERS if m.upper() in upper]
    if found_markers:
        hard.append("placeholder markers found: %s" % ", ".join(found_markers))
    else:
        ok.append("no placeholder markers")

    unlocatable = []
    for item_id, quote in collect_quotes(result.get("fact_source_map", [])):
        found = locate_quote(quote, corpus)
        if found:
            ok.append("quote locatable: %s -> %s" % (item_id, found["file"]))
        else:
            unlocatable.append({"item_id": item_id, "quote": quote[:120]})
    if unlocatable:
        warn.append("%d quote(s) 未能在材料全文定位" % len(unlocatable))

    status = result.get("status")
    if status == "complete":
        if unlocatable:
            warn.append("status=complete 但存在 %d 条未定位摘录" % len(unlocatable))
        deliverable = result.get("deliverable")
        if isinstance(deliverable, dict):
            artifacts = deliverable.get("artifact_path") or deliverable.get("artifacts") or []
            if not artifacts:
                warn.append("status=complete 但 deliverable.artifact_path 为空")
    if status not in ("complete", "partial", "failed", "insufficient_evidence", "input_error", None):
        warn.append("unrecognized status: %s" % status)

    return hard, warn, ok, unlocatable


def main(argv=None):
    ap = argparse.ArgumentParser(description="契约 + 摘录可核验性自检。")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--report", default="/app/output/verification_report.json")
    ap.add_argument("--material-root", action="append", default=[])
    args = ap.parse_args(argv)

    roots = list(args.material_root) or ["/input", "/app/work"]
    corpus = load_corpus(roots)
    case_data, _case_errors = read_json_robust(args.case)
    result, _result_errors = read_json_robust(args.result)

    hard, warn, ok, unlocatable = run_checks(result, case_data, corpus)

    if hard:
        code = 2
    elif warn:
        code = 1
    else:
        code = 0

    report = {
        "ts": _now_utc(),
        "result_path": args.result,
        "case_path": args.case,
        "material_roots": roots,
        "corpus_files": [c["rel"] for c in corpus],
        "exit_code": code,
        "pass_count": len(ok),
        "warning_count": len(warn),
        "hard_failure_count": len(hard),
        "passes": ok,
        "warnings": warn,
        "hard_failures": hard,
        "unlocatable_quotes": unlocatable,
    }

    outdir = os.path.dirname(args.report)
    if outdir:
        try:
            os.makedirs(outdir, exist_ok=True)
        except OSError:
            pass
    try:
        tmp = args.report + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, args.report)
    except OSError as exc:
        print("report write failed: %s" % exc, file=sys.stderr)

    print(json.dumps({
        "exit_code": code,
        "pass": len(ok),
        "warnings": len(warn),
        "hard_failures": len(hard),
        "unlocatable_quotes": len(unlocatable),
    }, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
