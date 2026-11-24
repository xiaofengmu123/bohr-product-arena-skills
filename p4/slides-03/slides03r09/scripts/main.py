#!/usr/bin/env python3
"""学术汇报内容组织 Skill 的落盘、证据校验与契约自检脚本（仅 Python 标准库）。

子命令：
  scaffold   生成或增量合并 /app/output/result.json 骨架（--update 增量合并）
  validate   校验 result.json 的契约完整性与合法性
  evidence   校验页级证据溯源（每页/每条须有 locator 与逐字 quote）
"""
import argparse
import datetime as _dt
import json
import os
import sys

VALID_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}
DEFAULT_CONTRACT = {
    "report_goal": None,
    "audience": None,
    "narrative": None,
    "pages": [],
    "editable_deck": None,
    "evidence_map": [],
    "qa_prep": [],
    "rendering": None,
}


def read_json(path):
    """按 utf-8-sig 容错读取 JSON，兼容 BOM。"""
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def now_iso():
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def empty_for(value):
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None


def load_case(case_path):
    if case_path and os.path.isfile(case_path):
        try:
            return read_json(case_path)
        except Exception:
            return {}
    return {}


def contract_of(case):
    return case.get("output_contract") or dict(DEFAULT_CONTRACT)


def case_id_of(case):
    return case.get("id") or case.get("armiId") or "unknown"


def build_skeleton(case):
    contract = contract_of(case)
    skeleton = {}
    for key, val in contract.items():
        skeleton[key] = val if val is not None else empty_for(val)
    skeleton["case_id"] = case_id_of(case)
    skeleton["status"] = "partial"
    skeleton["sources"] = []
    skeleton["uncertainty"] = []
    skeleton["change_log"] = []
    return skeleton


def log(action, scope):
    return {"ts": now_iso(), "action": action, "scope": scope}


def cmd_scaffold(args):
    case = load_case(args.case)
    out_path = os.path.join(args.out, "result.json")
    if args.update and os.path.isfile(out_path):
        try:
            existing = read_json(out_path)
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        skeleton = build_skeleton(case)
        for key, val in skeleton.items():
            existing.setdefault(key, val)
        existing.setdefault("change_log", [])
        existing["change_log"].append(log("scaffold --update", "merge contract skeleton"))
        data = existing
    else:
        data = build_skeleton(case)
        data["change_log"].append(log("scaffold", "init partial skeleton"))
    write_json(out_path, data)
    print(json.dumps({"ok": True, "case_id": data.get("case_id"), "status": data.get("status"),
                      "result": os.path.abspath(out_path)}, ensure_ascii=False))
    return 0


def cmd_validate(args):
    problems = []
    try:
        result = read_json(args.result)
    except Exception as exc:
        print("ERROR result.json 无法解析: %s" % exc)
        return 1
    if not isinstance(result, dict):
        print("ERROR result.json 顶层应为对象")
        return 1
    if not result.get("case_id"):
        problems.append("缺少 case_id")
    if result.get("status") not in VALID_STATUS:
        problems.append("status 取值非法，应为 %s" % sorted(VALID_STATUS))
    for field in ("sources", "uncertainty", "change_log"):
        if not isinstance(result.get(field), list):
            problems.append("%s 应为数组" % field)
    if args.case and os.path.isfile(args.case):
        case = load_case(args.case)
        contract = case.get("output_contract")
        if contract:
            for key in contract:
                if key not in result:
                    problems.append("缺少 output_contract 字段: %s" % key)
    pages = result.get("pages")
    if pages is not None and not isinstance(pages, list):
        problems.append("pages 应为数组")
    report = {"ok": not problems, "problems": problems}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_evidence(args):
    problems = []
    try:
        result = read_json(args.result)
    except Exception as exc:
        print("ERROR result.json 无法解析: %s" % exc)
        return 1
    pages = result.get("pages") or []
    if not isinstance(pages, list):
        print("ERROR pages 应为数组")
        return 1
    for page in pages:
        idx = page.get("page_index", "?")
        refs = page.get("source_refs") or []
        if not refs:
            problems.append("page %s: 缺少 source_refs" % idx)
        for ref in refs:
            if not ref.get("locator"):
                problems.append("page %s: source_ref 缺 locator" % idx)
            if not ref.get("quote"):
                problems.append("page %s: source_ref 缺逐字 quote" % idx)
    emap = result.get("evidence_map") or []
    if not isinstance(emap, list):
        problems.append("evidence_map 应为数组")
    else:
        for i, entry in enumerate(emap):
            for key in ("claim", "locator", "quote"):
                if not entry.get(key):
                    problems.append("evidence_map[%d]: 缺 %s" % (i, key))
    verbatim_checked = False
    if args.sources and os.path.isfile(args.sources):
        try:
            src = read_json(args.sources)
        except Exception:
            src = []
        text_pool = _load_text_pool(src)
        for page in pages:
            for ref in (page.get("source_refs") or []):
                _check_verbatim(ref, text_pool, problems, "page %s" % page.get("page_index", "?"))
        for i, entry in enumerate(emap):
            _check_verbatim(entry, text_pool, problems, "evidence_map[%d]" % i)
        verbatim_checked = True
    report = {"ok": not problems, "checked_pages": len(pages), "checked_evidence": len(emap),
              "verbatim_checked": verbatim_checked, "problems": problems}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _load_text_pool(sources):
    pool = {}
    for item in (sources if isinstance(sources, list) else []):
        path = item.get("path") if isinstance(item, dict) else None
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    pool[path] = fh.read()
            except Exception:
                pass
    return pool


def _check_verbatim(item, text_pool, problems, where):
    quote = (item.get("quote") or "").strip()
    if not quote:
        return
    locator = (item.get("locator") or "")
    file_part = locator.split("#")[0].split("：")[0].strip()
    candidates = [text for path, text in text_pool.items()
                  if file_part and (file_part in path or path.endswith(file_part.split("/")[-1]))]
    if not candidates:
        # 无法把 locator 定位到已读取文件时不作逐字判定，避免误报
        return
    if not any(quote in text for text in candidates):
        problems.append("%s: quote 未在来源文件中逐字找到 (%s)" % (where, locator))


def build_parser():
    ap = argparse.ArgumentParser(prog="main.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold", help="生成/合并 result.json 骨架")
    p.add_argument("--case", default="/input/case.json")
    p.add_argument("--task", default="/input/task.md")
    p.add_argument("--out", default="/app/output")
    p.add_argument("--update", action="store_true", help="增量合并，不覆盖已有字段")
    p.set_defaults(fn=cmd_scaffold)

    p = sub.add_parser("validate", help="校验 result.json 契约完整性")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--case", default="/input/case.json")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("evidence", help="校验页级证据溯源 locator+quote")
    p.add_argument("--result", default="/app/output/result.json")
    p.add_argument("--sources", default=None, help="可选 sources.json，用于逐字核验")
    p.set_defaults(fn=cmd_evidence)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
