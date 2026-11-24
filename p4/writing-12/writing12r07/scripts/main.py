#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — LaTeX 写作与编译 Skill 的入口：引导 / 回填 / 终检 / 自检 / 自测。

职责
  1. 扫描 /input，发现 case.json / task.md 与材料文件（不假设固定文件名）；
  2. 抽取 case_id 与 output_contract 字段清单，立即原子写入一版合法的
     /app/output/result.json 骨架（先落盘，再增量改进）；
  3. finalize 保证 result.json 合法、contract 键齐全、status 诚实；
  4. selfcheck 校验产物满足输出契约（发现问题退出码 1）；
  5. selftest 在临时目录跑鲁棒性用例（空输入/坏 JSON/坏编码/缺参/超大文件/
     写盘失败），证明异常路径优雅降级。

仅依赖 Python 标准库。bootstrap / discover / finalize / selftest 对非致命失败
一律退出码 0；selfcheck 发现问题时退出码 1，供调用方决定是否继续修复。

鲁棒性设计（本版重点）
  - 原子写盘：先写 *.tmp 再 os.replace，任何中途异常都不会留下不完整 result.json。
  - 宽容读取：utf-8-sig / utf-8 / replacement 三级回退；单文件超限跳过并记录。
  - 坏 JSON：先整体解析，失败则按截断前缀重试，仍失败记为 bad_json 继续。
  - 全部命令用 try/except 兜底，缺参走默认路径，未知参数报 help 而非崩溃。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB：超过则跳过并记录，不读入内存
TEXT_EXTS = {
    ".md", ".tex", ".bib", ".txt", ".csv", ".json", ".yaml", ".yml", ".rst",
    ".cls", ".sty", ".log", ".aux", ".bbl", ".cfg", ".lst", ".pdf", ".png",
    ".jpg", ".jpeg", ".eps", ".svg", ".docx", ".pptx", ".xlsx",
}
ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
CASE_ID_KEYS = ("case_id", "id", "challenge_id", "benchmarkId", "armiId")
DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def monotonic_ms() -> str:
    return "%.0f" % (time.monotonic() * 1000)


def reconfigure_stdio() -> None:
    """确保 stdout/stderr 为 UTF-8，避免非 UTF-8 终端下 json 输出崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# --------------------------------------------------------------------------
# 宽容的读写原语
# --------------------------------------------------------------------------

def read_bytes(path):
    """读原始字节，超限返回 (None, note)。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, "read_error: %s" % exc
    if len(raw) > MAX_FILE_BYTES:
        return None, "skipped: exceeds %d bytes" % MAX_FILE_BYTES
    return raw, None


def read_text(path):
    """读 UTF-8 文本，容错 BOM/编码。返回 (text, error_note)。"""
    raw, note = read_bytes(path)
    if raw is None:
        return None, note
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "decoded_with_replacement"


def read_json(path):
    """宽容 JSON 读取。返回 (obj, errors_list)。永不抛异常。"""
    text, err = read_text(path)
    if text is None:
        return None, [err or "unreadable"]
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        cut = text[:exc.pos]
        try:
            return json.loads(cut), ["json_truncated_at_%d" % exc.pos]
        except Exception:
            return None, ["bad_json: %s" % exc]


def write_json_atomic(path, obj):
    """原子写 JSON：先写临时文件再替换，杜绝半成品。返回写入路径。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".result-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def sha256_of_bytes(raw) -> str:
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------
# 输入发现与契约抽取
# --------------------------------------------------------------------------

def discover_input(root):
    """递归扫描输入目录，返回确定性排序的文件清单。"""
    files = []
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            ext = os.path.splitext(name)[1].lower()
            raw, note = read_bytes(full) if size <= MAX_FILE_BYTES else (None, "skipped: exceeds %d bytes" % MAX_FILE_BYTES)
            digest = sha256_of_bytes(raw) if raw is not None else None
            files.append({
                "path": full,
                "rel": os.path.relpath(full, root).replace("\\", "/"),
                "size": size,
                "ext": ext,
                "is_text": ext in TEXT_EXTS,
                "sha256": digest,
                "note": note,
            })
    return files


def find_case_id(obj):
    if not isinstance(obj, dict):
        return None
    for key in CASE_ID_KEYS:
        val = obj.get(key)
        if val not in (None, "", [], {}):
            return str(val)
    return None


def contract_keys(obj):
    """返回 output_contract 需要原样回填的键，保持出现顺序。

    兼容三种形态：
      1. dict：取全部键；
      2. list[str]：直接取列表；
      3. list[dict]：取每个 dict 的键（按序去重）。
    其它形态返回 []。
    """
    if not isinstance(obj, dict):
        return []
    contract = obj.get("output_contract")
    keys = []
    seen = set()
    if isinstance(contract, dict):
        for key in contract.keys():
            if key not in seen:
                seen.add(key)
                keys.append(key)
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str) and item not in seen:
                seen.add(item)
                keys.append(item)
            elif isinstance(item, dict):
                for key in item.keys():
                    if key not in seen:
                        seen.add(key)
                        keys.append(key)
    return keys


def build_skeleton(root):
    """从 /input/case.json 构建 result.json 骨架。返回 (skeleton, keys)。"""
    case_path = os.path.join(root, "case.json")
    notes = []
    case_id = None
    keys = []
    if os.path.isfile(case_path):
        obj, errs = read_json(case_path)
        notes.extend(errs)
        case_id = find_case_id(obj)
        keys = contract_keys(obj)
        if not keys:
            notes.append("output_contract_not_found: metadata-only result")
        if obj is None:
            notes.append("case_json_unparseable: used metadata-only skeleton")
    else:
        notes.append("case.json_not_found_in:" + root)

    if case_id is None:
        case_id = "unknown"
        notes.append("case_id_unknown: used 'unknown'")

    skeleton = {"case_id": case_id}
    for key in keys:
        skeleton[key] = None
    status = "input_error" if not keys else "partial"
    skeleton.update({
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{
            "time": utcnow(),
            "step": "bootstrap",
            "note": "result skeleton written before analysis; contract fields default to null",
        }],
        "errors": notes,
        "degradations": [],
        "limitations": [],
        "runtime": {"started_at": utcnow(), "producer": "main.py"},
    })
    return skeleton, keys


# --------------------------------------------------------------------------
# 子命令：bootstrap / discover / finalize / selfcheck / selftest
# --------------------------------------------------------------------------

def cmd_bootstrap(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    skeleton, keys = build_skeleton(args.input)
    write_json_atomic(os.path.join(args.output, "result.json"), skeleton)
    inventory = {
        "input_root": args.input,
        "contract_keys": keys,
        "files": discover_input(args.input),
        "generated_at": utcnow(),
    }
    write_json_atomic(os.path.join(args.work, "inventory.json"), inventory)
    print(json.dumps({
        "case_id": skeleton["case_id"],
        "status": skeleton["status"],
        "contract_keys": keys,
        "file_count": len(inventory["files"]),
        "result_path": os.path.join(args.output, "result.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_discover(args):
    files = discover_input(args.input)
    print(json.dumps({"input_root": args.input, "files": files},
                     ensure_ascii=False, indent=2))
    return 0


def _load_inventory(args):
    inv_path = os.path.join(args.work, "inventory.json")
    obj, errs = read_json(inv_path)
    keys = []
    if isinstance(obj, dict):
        keys = obj.get("contract_keys") or []
    return keys, errs


def _contracts_present(obj, keys):
    missing = []
    for key in keys:
        if key not in obj:
            missing.append(key)
    return missing


def _suggest_status(obj, keys):
    """依据契约键是否有真实值，给出诚实的状态建议。"""
    if not keys:
        return "input_error"
    filled = 0
    for key in keys:
        val = obj.get(key)
        if val not in (None, "", [], {}):
            filled += 1
    if filled == 0:
        return "partial"
    if filled == len(keys):
        return "ok"
    return "partial"


def cmd_finalize(args):
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    result_path = os.path.join(args.output, "result.json")
    keys, _errs = _load_inventory(args)

    obj, errs = read_json(result_path)
    if not isinstance(obj, dict):
        # 结果缺失或坏 JSON：从骨架重建，绝不空手退出
        skeleton, rebuilt_keys = build_skeleton(args.input)
        keys = keys or rebuilt_keys
        obj = skeleton
        errs = errs + ["result_rebuilt_from_skeleton"]

    # 补齐 contract 键与必备元数据键
    for key in keys:
        obj.setdefault(key, None)
    for meta in ("case_id", "status", "sources", "uncertainty", "change_log"):
        if meta not in obj:
            obj[meta] = [] if meta in ("sources", "uncertainty", "change_log") else ("unknown" if meta == "case_id" else "partial")
    obj.setdefault("pending_human", [])
    obj.setdefault("errors", [])
    obj.setdefault("degradations", [])
    obj.setdefault("limitations", [])

    # 状态诚实：显式 --status 优先；否则按契约键填充程度推断
    if args.status:
        obj["status"] = args.status if args.status in ALLOWED_STATUS else "partial"
    elif obj.get("status") not in ALLOWED_STATUS:
        obj["status"] = _suggest_status(obj, keys)

    obj.setdefault("runtime", {})
    obj["runtime"].setdefault("producer", "main.py")
    obj["runtime"]["finished_at"] = utcnow()

    # 追加 finalize 变更记录（时间戳除外，结构确定性）
    obj.setdefault("change_log", [])
    if not isinstance(obj["change_log"], list):
        obj["change_log"] = []
    obj["change_log"].append({
        "time": utcnow(),
        "step": "finalize",
        "note": "contract keys ensured; status=%s" % obj["status"],
    })

    missing = _contracts_present(obj, keys)
    obj["degradations"] = list(obj.get("degradations") or [])
    if missing:
        obj["degradations"].append({"missing_contract_keys": missing})
    obj["errors"] = list(obj.get("errors") or []) + list(errs or [])

    try:
        write_json_atomic(result_path, obj)
    except OSError as exc:
        # 写盘失败：降级到最小合法结果（目录内再次尝试，仍失败则只打印）
        minimal = {
            "case_id": obj.get("case_id", "unknown"),
            "status": "partial",
            "sources": [],
            "uncertainty": ["write_failure: %s" % exc],
            "pending_human": ["result.json could not be persisted"],
            "change_log": [{"time": utcnow(), "step": "finalize", "note": "write failure fallback"}],
        }
        for key in keys:
            minimal.setdefault(key, None)
        try:
            write_json_atomic(result_path, minimal)
        except OSError:
            print(json.dumps(minimal, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps({
        "result_path": result_path,
        "status": obj["status"],
        "contract_keys": keys,
        "missing_contract_keys": missing,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_selfcheck(args):
    """校验 result.json 满足输出契约。发现问题退出码 1。"""
    result_path = os.path.join(args.output, "result.json")
    problems = []
    keys, inv_errs = _load_inventory(args)
    problems.extend("inventory:" + e for e in (inv_errs or []))

    obj, errs = read_json(result_path)
    if errs:
        problems.extend("result_json:" + e for e in errs)
    if not isinstance(obj, dict):
        problems.append("result_json: not an object")
        obj = {}

    if not obj.get("case_id"):
        problems.append("missing field: case_id")
    if obj.get("status") not in ALLOWED_STATUS:
        problems.append("invalid status: %r" % obj.get("status"))
    for field in ("sources", "uncertainty", "change_log"):
        if not isinstance(obj.get(field), list):
            problems.append("field %s must be a list" % field)
    missing = _contracts_present(obj, keys)
    if missing:
        problems.append("missing contract keys: " + ", ".join(missing))

    print(json.dumps({
        "ok": not problems,
        "result_path": result_path,
        "contract_keys": keys,
        "problems": problems,
    }, ensure_ascii=False, indent=2))
    return 1 if problems else 0


# --------------------------------------------------------------------------
# selftest：鲁棒性回归用例，跑在临时目录，不触碰真实 /input /app/output
# --------------------------------------------------------------------------

def _run_self(args_list, work_dir):
    """在子进程里跑 main.py，返回 (exit_code, stdout_tail)。"""
    proc = subprocess.run(
        [sys.executable, "-B", os.path.abspath(__file__)] + args_list,
        cwd=work_dir, capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "")[-800:]


def _assert_valid_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cmd_selftest(args):
    """运行健壮性测试矩阵。全部通过退出码 0，否则 1。"""
    tests = []

    def add(name, fn):
        tests.append((name, fn))

    base = tempfile.mkdtemp(prefix="latex-skill-selftest-")

    # 1. 空输入目录
    def t_empty():
        d = os.path.join(base, "empty"); os.makedirs(os.path.join(d, "input"), exist_ok=True)
        out = os.path.join(d, "out"); work = os.path.join(d, "work")
        code, _ = _run_self(["bootstrap", "--input", os.path.join(d, "input"), "--output", out, "--work", work], base)
        assert code == 0, "bootstrap exit=%s" % code
        r = _assert_valid_json(os.path.join(out, "result.json"))
        assert r.get("status") in ("partial", "input_error", "insufficient_evidence")
        code, _ = _run_self(["finalize", "--input", os.path.join(d, "input"), "--output", out, "--work", work], base)
        assert code == 0
        return "status=%s, contract_keys=%d" % (r["status"], len(r.get("sources", [])))
    add("empty_input", t_empty)

    # 2. 坏 JSON case.json
    def t_bad_json():
        d = os.path.join(base, "badjson"); ip = os.path.join(d, "input"); os.makedirs(ip, exist_ok=True)
        with open(os.path.join(ip, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "x", "output_contract": {"title": 1,')
        out = os.path.join(d, "out"); work = os.path.join(d, "work")
        code, _ = _run_self(["bootstrap", "--input", ip, "--output", out, "--work", work], base)
        assert code == 0
        r = _assert_valid_json(os.path.join(out, "result.json"))
        assert r.get("status") in ("partial", "input_error")
        return "truncated json handled"
    add("bad_json", t_bad_json)

    # 3. 坏编码 case.json
    def t_bad_encoding():
        d = os.path.join(base, "badenc"); ip = os.path.join(d, "input"); os.makedirs(ip, exist_ok=True)
        with open(os.path.join(ip, "case.json"), "wb") as fh:
            fh.write(b'{"case_id":"x"}\n\xff\xfe\x00\xff binary')
        out = os.path.join(d, "out"); work = os.path.join(d, "work")
        code, _ = _run_self(["bootstrap", "--input", ip, "--output", out, "--work", work], base)
        assert code == 0
        r = _assert_valid_json(os.path.join(out, "result.json"))
        assert r.get("case_id"), "case_id should still be present"
        return "non-utf8 tolerated, case_id=%s" % r["case_id"]
    add("bad_encoding", t_bad_encoding)

    # 4. 缺参数：走默认 /input /app/output /app/work，不崩溃
    def t_missing_params():
        code, tail = _run_self(["discover"], base)
        assert code == 0, "discover should exit 0 even if /input missing"
        return "discover degraded gracefully"
    add("missing_params", t_missing_params)

    # 5. 超大文件：超 2MiB 跳过
    def t_oversize():
        d = os.path.join(base, "big"); ip = os.path.join(d, "input"); os.makedirs(ip, exist_ok=True)
        with open(os.path.join(ip, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "big", "output_contract": {"answer": None}}, fh)
        with open(os.path.join(ip, "huge.tex"), "wb") as fh:
            fh.write(b"% " + b"a" * (3 * 1024 * 1024))
        out = os.path.join(d, "out"); work = os.path.join(d, "work")
        code, _ = _run_self(["bootstrap", "--input", ip, "--output", out, "--work", work], base)
        assert code == 0
        inv = _assert_valid_json(os.path.join(work, "inventory.json"))
        huge = [f for f in inv["files"] if f["rel"].endswith("huge.tex")]
        assert huge and huge[0].get("note", "").startswith("skipped"), "oversize file should be skipped"
        return "oversize file skipped"
    add("oversize_file", t_oversize)

    # 6. output_contract 三种形态回填
    def t_contract_forms():
        for label, contract in (
            ("dict", {"title": None, "body": None}),
            ("list_str", ["title", "body"]),
            ("list_dict", [{"title": None}, {"body": None}]),
        ):
            d = os.path.join(base, "cf_" + label); ip = os.path.join(d, "input"); os.makedirs(ip, exist_ok=True)
            with open(os.path.join(ip, "case.json"), "w", encoding="utf-8") as fh:
                json.dump({"case_id": label, "output_contract": contract}, fh)
            out = os.path.join(d, "out"); work = os.path.join(d, "work")
            code, _ = _run_self(["bootstrap", "--input", ip, "--output", out, "--work", work], base)
            assert code == 0
            r = _assert_valid_json(os.path.join(out, "result.json"))
            assert "title" in r and "body" in r, "contract keys should be echoed for %s" % label
        return "dict/list_str/list_dict contracts all echoed"
    add("contract_forms", t_contract_forms)

    # 7. 写盘失败（output 指向只读路径的父目录不存在且不可建，用文件占位模拟）
    def t_write_failure():
        d = os.path.join(base, "wfail"); ip = os.path.join(d, "input"); os.makedirs(ip, exist_ok=True)
        with open(os.path.join(ip, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "wf", "output_contract": {"x": None}}, fh)
        # 用一个普通文件占用 output 路径，使 makedirs 失败
        blocker = os.path.join(d, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("file")
        code, _ = _run_self(["finalize", "--input", ip, "--output", blocker, "--work", os.path.join(d, "work")], base)
        assert code == 0, "write failure must degrade to exit 0"
        return "write failure degraded to exit 0"
    add("write_failure", t_write_failure)

    passed = 0
    failed = 0
    report = []
    for name, fn in tests:
        try:
            detail = fn()
            report.append({"test": name, "ok": True, "detail": detail})
            passed += 1
        except Exception as exc:
            report.append({"test": name, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
            failed += 1
    try:
        shutil.rmtree(base, ignore_errors=True)
    except OSError:
        pass
    summary = {"passed": passed, "failed": failed, "total": passed + failed, "tests": report}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="LaTeX 写作与编译 Skill：bootstrap/discover/finalize/selfcheck/selftest",
    )
    parser.add_argument("command", choices=["bootstrap", "discover", "finalize", "selfcheck", "selftest"])
    parser.add_argument("--input", default=DEFAULT_INPUT, help="输入目录（默认 /input）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录（默认 /app/output）")
    parser.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    parser.add_argument("--status", default=None, help="finalize 时强制 status")
    return parser


def main(argv=None):
    reconfigure_stdio()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse 对未知/缺参的 SystemExit(0/2)：未知参数返回 2，这里兜底为 0，
        # 保证「缺参数 / 未知参数」不导致 Harness 崩溃。
        return 0 if exc.code in (0, 2) else exc.code
    dispatch = {
        "bootstrap": cmd_bootstrap,
        "discover": cmd_discover,
        "finalize": cmd_finalize,
        "selfcheck": cmd_selfcheck,
        "selftest": cmd_selftest,
    }
    try:
        return dispatch[args.command](args)
    except Exception as exc:  # 顶层兜底：任何未捕获异常都不让进程崩溃
        payload = {
            "error": "%s: %s" % (type(exc).__name__, exc),
            "hint": "command failed; ensure input/output/work paths are reachable",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
