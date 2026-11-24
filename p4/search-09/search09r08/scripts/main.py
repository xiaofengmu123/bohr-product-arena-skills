#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litqa-scoremap 运行脚手架：skeleton / validate / self-test。

只依赖 Python 标准库；确定性、无随机数、无外部服务。
职责：
  1. 发现 /input 下 case.json、task.md 与材料文件（不假设固定文件名/键名）；
  2. 从 case.json 递归定位 output_contract（键名大小写不敏感、任意嵌套）；
  3. 立即写出一份合法 partial 结果骨架 result.json（含 case_id、原样回填的
     output_contract 全部字段、status/sources/uncertainty/change_log 等），
     保证任何中途异常都不留下空产物；
  4. 校验最终 result.json 的结构、契约字段回填与证据引文完整性。

用法（在 SKILL.md 所在目录下运行；脚本内部使用相对路径调用其它模块）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py self-test
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import litqa  # noqa: E402

DEFAULT_INPUT = os.environ.get("SKILL_INPUT_DIR", "/input")
DEFAULT_OUTPUT = os.environ.get("SKILL_OUTPUT_DIR", "/app/output")
DEFAULT_WORK = os.environ.get("SKILL_WORK_DIR", "/app/work")
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2MB，超限跳过并记录
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CONTRACT_KEY_RE = re.compile(
    r"^(output[_-]?contract|output[_-]?schema|result[_-]?schema|output[_-]?format|contract|schema)$",
    re.I,
)

REQUIRED_ARRAY_KEYS = (
    "sources", "uncertainty", "pending_human", "change_log",
    "errors", "degradations", "limitations", "artifacts",
    "candidates", "screening", "assessment", "evidence_records",
    "omissions", "conflicts", "queries", "rubric_alignment",
)


# ---------------------------------------------------------------------------
# 基础 I/O（容错）
# ---------------------------------------------------------------------------

def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path, max_bytes=MAX_FILE_BYTES):
    """以 utf-8-sig 优先读取文本；坏编码/超大文件降级并返回错误列表。"""
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
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return None, ["read_failed: %s: %s" % (p, exc)]
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc), errors
        except (UnicodeDecodeError, OSError) as exc:
            errors.append("decode(%s)_failed: %s: %s" % (enc, p, exc))
    return None, errors


def load_json_lenient(path):
    """读 JSON；坏 JSON 返回 None 并记录错误，绝不抛异常。"""
    text, errors = read_text(path)
    if text is None:
        return None, errors
    try:
        return json.loads(text), errors
    except (json.JSONDecodeError, ValueError) as exc:
        return None, errors + ["bad_json: %s: %s" % (path, exc)]


def atomic_write_json(path, data):
    """先写临时文件再 rename，避免留下半截 JSON。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(p))


def atomic_write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------------------
# 输入发现与契约定位
# ---------------------------------------------------------------------------

def discover_input(input_dir):
    """扫描输入目录，返回可识别的文件清单（不假设固定文件名）。"""
    root = Path(input_dir)
    found = {"case_json": [], "task_md": [], "materials": [], "other": []}
    if not root.is_dir():
        return found, ["input_dir_missing: %s" % root]
    try:
        entries = sorted(p for p in root.rglob("*") if p.is_file())
    except OSError as exc:
        return found, ["input_dir_read_failed: %s: %s" % (root, exc)]
    for p in entries:
        name = p.name.lower()
        if name == "case.json":
            found["case_json"].append(p)
        elif name == "task.md":
            found["task_md"].append(p)
        elif name in (".ds_store", "thumbs.db"):
            continue
        else:
            found["materials"].append(p)
    return found, []


def find_contract(obj, depth=0):
    """递归查找 output_contract（大小写不敏感，任意嵌套）。"""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if CONTRACT_KEY_RE.match(str(key)):
                return value
            hit = find_contract(value, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = find_contract(value, depth + 1)
            if hit is not None:
                return hit
    return None


def _default_for(value):
    """根据契约里的类型描述给出默认值；无法判断则 None。"""
    if isinstance(value, dict):
        typ = str(value.get("type", "")).lower()
        if "array" in typ:
            return []
        if "object" in typ:
            return {}
        return None
    if isinstance(value, list):
        return []
    return None


def contract_fields(contract):
    """把契约字段转为 (field, default) 列表；不改名、不删未知键。"""
    if isinstance(contract, dict):
        props = contract.get("properties")
        source = props if isinstance(props, dict) else contract
        return [(str(k), _default_for(v)) for k, v in source.items()]
    if isinstance(contract, list):
        return [(str(k), None) for k in contract if isinstance(k, str)]
    return []


def extract_case_id(data, input_dir):
    """从 case.json 取 case_id，缺失则退回目录名。"""
    if isinstance(data, dict):
        for key in ("case_id", "caseId", "id", "caseID"):
            if key in data and isinstance(data[key], (str, int)):
                return str(data[key])
    return Path(input_dir).name or "unknown"


# ---------------------------------------------------------------------------
# 骨架与结果
# ---------------------------------------------------------------------------

def build_skeleton(case_id, contract, errors=None):
    """构造一份合法 partial 骨架。"""
    now = time.time()
    result = {"case_id": case_id, "status": "partial"}
    for field, default in contract_fields(contract):
        if field not in result:
            result[field] = default
    result.update({
        "artifacts": [],
        "research_question": None,
        "search_scope": None,
        "queries": [],
        "candidates": [],
        "screening": [],
        "assessment": [],
        "grade": None,
        "evidence_records": [],
        "omissions": [],
        "conflicts": [],
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "rubric_alignment": [],
        "change_log": [{
            "time_utc": utc_now(),
            "event": "skeleton",
            "detail": "partial skeleton written; contract fields backfilled with null/[]",
        }],
        "errors": list(errors or []),
        "degradations": [],
        "limitations": [],
        "runtime": {"start_epoch": now, "skeleton_epoch": now, "finish_epoch": None},
    })
    return result


def _input_sources(found):
    sources = []
    for kind, key in (("case_json", "case.json"), ("task_md", "task.md")):
        for p in found.get(kind, []):
            sources.append({
                "id": key,
                "type": "input",
                "path_or_uri": str(p),
                "locator": None,
                "quote": None,
                "note": "input file",
            })
    return sources


def cmd_skeleton(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK
    for d in (work_dir, output_dir):
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print("WARN: mkdir failed: %s: %s" % (d, exc))

    found, disc_errors = discover_input(input_dir)
    all_errors = list(disc_errors)
    case_path = found["case_json"][0] if found["case_json"] else None
    data = None
    if case_path is not None:
        data, jerrs = load_json_lenient(case_path)
        all_errors.extend(jerrs)

    case_id = extract_case_id(data, input_dir)
    contract = find_contract(data) if isinstance(data, (dict, list)) else None

    status = "partial"
    if case_path is None:
        status = "insufficient_evidence"
    if data is None and case_path is not None:
        status = "input_error"
    if not found["materials"] and not found["task_md"] and case_path is None:
        status = "input_error"

    result = build_skeleton(case_id, contract, all_errors)
    result["status"] = status
    result["sources"] = _input_sources(found)

    out_path = Path(output_dir) / "result.json"
    try:
        atomic_write_json(out_path, result)
    except OSError as exc:
        # 写盘失败：尽力保留最小合法产物，不让进程崩溃
        print("FATAL: cannot write result.json: %s" % exc)
        result["errors"].append("result_write_failed: %s" % exc)
    print("skeleton_written=%s status=%s case_id=%s contract_fields=%d errors=%d"
          % (out_path, result["status"], result["case_id"],
             len(contract_fields(contract)), len(result["errors"])))
    return 0


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _collect_text_corpus(found, work_dir):
    """收集可用于引文核验的纯文本（材料 + 已抽取文本）。"""
    corpus = []
    paths = []
    for p in found.get("materials", []):
        if p.suffix.lower() in (".txt", ".md", ".csv", ".xml", ".json"):
            text, errs = read_text(p)
            if text is not None:
                corpus.append(text)
                paths.append(str(p))
    if work_dir:
        wd = Path(work_dir)
        if wd.is_dir():
            for p in sorted(wd.rglob("*.txt")):
                text, errs = read_text(p)
                if text is not None:
                    corpus.append(text)
                    paths.append(str(p))
    return corpus, paths


def cmd_validate(args):
    input_dir = args.input or DEFAULT_INPUT
    output_dir = args.output or DEFAULT_OUTPUT
    work_dir = args.work or DEFAULT_WORK

    report = []
    n_err = n_warn = 0

    rp = Path(output_dir) / "result.json"
    if not rp.exists():
        report.append("ERROR: result.json missing: %s" % rp)
        print("\n".join(report))
        return 0

    data, jerrs = load_json_lenient(rp)
    if data is None:
        report.append("ERROR: result.json unparseable: %s" % "; ".join(jerrs))
        print("\n".join(report))
        return 0
    if not isinstance(data, dict):
        report.append("ERROR: result.json top-level is not an object")
        print("\n".join(report))
        return 0

    def err(msg):
        nonlocal n_err
        n_err += 1
        report.append("ERROR: " + msg)

    def warn(msg):
        nonlocal n_warn
        n_warn += 1
        report.append("WARN: " + msg)

    # case_id
    if not data.get("case_id"):
        err("case_id missing or empty")
    # status
    status = data.get("status")
    if status not in ALLOWED_STATUS:
        err("status invalid: %r (allowed %s)" % (status, sorted(ALLOWED_STATUS)))
    if status in ("ok", "partial") and not data.get("assessment"):
        warn("status=%s but assessment is empty" % status)

    # contract fields backfilled
    found, _ = discover_input(input_dir)
    case_path = found["case_json"][0] if found["case_json"] else None
    if case_path is not None:
        case_data, _ = load_json_lenient(case_path)
        contract = find_contract(case_data) if isinstance(case_data, (dict, list)) else None
        for field, _default in contract_fields(contract):
            if field not in data:
                err("output_contract field missing from result: %s" % field)
        data["_contract_fields_seen"] = len(contract_fields(contract))

    # required array keys
    for key in REQUIRED_ARRAY_KEYS:
        if key not in data:
            err("required key missing: %s" % key)
        elif not isinstance(data[key], list):
            err("required key not a list: %s" % key)

    # runtime
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        err("runtime missing or not object")
    else:
        for k in ("start_epoch", "skeleton_epoch", "finish_epoch"):
            if k not in runtime:
                err("runtime.%s missing" % k)

    # change_log entries
    clog = data.get("change_log") or []
    for i, entry in enumerate(clog):
        if not isinstance(entry, dict) or "event" not in entry:
            err("change_log[%d] malformed (needs 'event')" % i)

    # assessment items
    assessment = data.get("assessment") or []
    need_keys = ("dimension", "checkpoint", "verdict", "judgment")
    crit_keys = ("locator", "quote")
    n_assess = 0
    n_quote_missing = 0
    n_evidence_abstain = 0
    for i, item in enumerate(assessment):
        if not isinstance(item, dict):
            err("assessment[%d] is not an object" % i)
            continue
        n_assess += 1
        for k in need_keys:
            if not item.get(k):
                err("assessment[%d].%s missing" % (i, k))
        if item.get("verdict") == "证据不足":
            n_evidence_abstain += 1
        if item.get("critical"):
            for k in crit_keys:
                if not item.get(k):
                    n_quote_missing += 1
                    err("assessment[%d] critical but %s missing" % (i, k))

    # quote verification
    corpus, corpus_paths = _collect_text_corpus(found, work_dir)
    if corpus:
        joined = "\n".join(corpus)
        n_verified = 0
        n_failed = 0
        for i, item in enumerate(assessment):
            q = (item.get("quote") or "").strip()
            if q and q != "not_reported":
                res = litqa.verify_quote_in_text(joined, q)
                if res == "missing":
                    n_failed += 1
                    err("assessment[%d] quote not found in extracted text" % i)
                else:
                    n_verified += 1
        report.append("INFO: quote verify: %d checked, %d matched, %d missing" % (
            n_verified + n_failed, n_verified, n_failed))
    else:
        warn("no extracted text corpus; quote verification skipped")

    report.append("SUMMARY: errors=%d warnings=%d assessment_items=%d "
                  "evidence_abstain=%d contract_fields=%s" % (
                      n_err, n_warn, n_assess, n_evidence_abstain,
                      data.get("_contract_fields_seen", "n/a")))
    if "_contract_fields_seen" in data:
        # 不留内部字段在最终产物里
        del data["_contract_fields_seen"]
    print("\n".join(report))
    return 0


# ---------------------------------------------------------------------------
# 自检（4 场景：空目录 / 坏 JSON / 缺输入 / 正常契约）
# ---------------------------------------------------------------------------

def _run_skeleton_ns(input_dir, output_dir, work_dir):
    ns = argparse.Namespace(input=input_dir, output=output_dir, work=work_dir)
    return cmd_skeleton(ns)


def _run_validate_ns(input_dir, output_dir, work_dir):
    ns = argparse.Namespace(input=input_dir, output=output_dir, work=work_dir)
    return cmd_validate(ns)


def cmd_self_test(args):
    tmp = Path(tempfile.mkdtemp(prefix="litqa_selftest_"))
    cases = []

    # 1) 空输入目录
    d = tmp / "empty"
    (d / "input").mkdir(parents=True)
    cases.append(("empty_dir", d / "input", d / "out", d / "work",
                  ("insufficient_evidence", "input_error")))

    # 2) 坏 JSON
    d = tmp / "badjson"
    (d / "input").mkdir(parents=True)
    (d / "input" / "case.json").write_text("{ this is not json", encoding="utf-8")
    cases.append(("bad_json", d / "input", d / "out", d / "work",
                  ("input_error", "partial")))

    # 3) 缺输入目录
    d = tmp / "missing"
    cases.append(("missing_input", d / "no_such_dir", d / "out", d / "work",
                  ("input_error",)))

    # 4) 正常契约（happy path）
    d = tmp / "good"
    (d / "input").mkdir(parents=True)
    good_case = {
        "case_id": "case-demo-01",
        "output_contract": {
            "properties": {
                "title": {"type": "string"},
                "items": {"type": "array"},
                "meta": {"type": "object"},
                "score": {"type": "number"},
            },
            "required": ["title", "items"],
        },
    }
    (d / "input" / "case.json").write_text(
        json.dumps(good_case, ensure_ascii=False), encoding="utf-8")
    cases.append(("happy_path", d / "input", d / "out", d / "work", ("partial",)))

    all_ok = True
    print("self-test (temp root: %s)" % tmp)
    for name, indir, outdir, workdir, allowed in cases:
        rc = _run_skeleton_ns(indir, outdir, workdir)
        rp = Path(outdir) / "result.json"
        status = None
        if rp.exists():
            data, _ = load_json_lenient(rp)
            if isinstance(data, dict):
                status = data.get("status")
        ok = (rc == 0) and rp.exists() and (status in allowed if status else False)
        # happy path: additionally verify contract fields backfilled
        if name == "happy_path" and ok:
            data, _ = load_json_lenient(rp)
            ok = ok and all(k in data for k in ("title", "items", "meta", "score"))
            ok = ok and data.get("items") == [] and data.get("score") is None
        _run_validate_ns(indir, outdir, workdir)
        all_ok = all_ok and ok
        print("  [%s] %s -> rc=%d status=%s" % ("PASS" if ok else "FAIL", name, rc, status))

    print("self-test %s" % ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="litqa-scoremap scaffold")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("skeleton", help="write partial result skeleton immediately")
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)
    p.set_defaults(func=cmd_skeleton)

    p = sub.add_parser("validate", help="validate final result.json structure and evidence")
    p.add_argument("--input", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--work", default=None)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("self-test", help="run 4-scenario robustness self-check")
    p.set_defaults(func=cmd_self_test)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
