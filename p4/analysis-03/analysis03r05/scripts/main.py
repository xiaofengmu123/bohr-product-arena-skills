#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化 Skill 主控脚本（仅标准库）。

职责：
  1) 发现输入：不假设固定文件名/列名/键名，扫描 case.json、task.md、CSV/SQLite；
  2) 提取输出契约：递归发现 output_contract 等别名，叶值置 null/[] 回填模板；
  3) 先落盘合法骨架：init 立即写 /app/output/result.json（status=partial），
     任何中途异常都不留空产物；之后用 update 做增量改进并维护 change_log；
  4) contract-report / check：汇报契约填充进度并校验结构与产物格式；
  5) selftest：自检电池（空目录/坏 JSON/缺参数/契约对齐/增量落盘）。

子命令：
  init             发现输入、提取契约、写 result.json 骨架与 work/plan.md
  update           以 --set / --set-key+--set-value / --status / --log 增量更新
  contract-report  打印 output_contract 填充进度
  check            校验 result.json 与 /app/output 产物；--fail 时遇 FAIL 才非零
  run              init + 基础数据画像（纯标准库），一次性兜底

缺省参数：--input /input --output /app/output --work /app/work。
除 check --fail 且存在 FAIL 项外，任何命令退出码恒为 0，保证 Harness 不被中断。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract  # noqa: E402

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件读取上限 2MB，超限跳过并记录
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json", ".yaml", ".yml")
DATA_EXTS = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")
RESERVED_KEYS = contract.RESERVED_KEYS


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def read_text_robust(path, limit=MAX_FILE_BYTES):
    """utf-8-sig 优先，逐级降级到 gb18030/latin-1；返回 (text, encoding, error)。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return "", None, "unreadable: %s" % e
    if size > limit:
        return "", None, "file_too_large(%d > %d)" % (size, limit)
    last = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                return fh.read(), enc, None
        except (UnicodeDecodeError, UnicodeError) as e:
            last = "%s: %s" % (enc, e)
        except OSError as e:
            return "", None, "unreadable: %s" % e
    return "", None, "undecodable: " + last


def atomic_write_text(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=os.path.splitext(path)[1])
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return path


def load_json_robust(path):
    """读取 JSON；坏 JSON 不抛异常，返回 (obj, error)。"""
    if not os.path.exists(path):
        return {}, "missing: %s" % path
    text, enc, err = read_text_robust(path)
    if err:
        return {}, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return {}, "bad_json: %s" % e
    except ValueError as e:
        return {}, "bad_json: %s" % e


def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_json_value(v):
    v = (v or "").strip()
    if v == "":
        return ""
    try:
        return json.loads(v)
    except (json.JSONDecodeError, ValueError):
        return v  # 无法解析时按字符串


def parse_set_arg(s):
    """--set 支持 'status="ok"'、'a=123'、'a=[1,2]' 或 JSON 对象。"""
    s = s.strip()
    if s.startswith("{"):
        return json.loads(s)
    if "=" in s:
        k, v = s.split("=", 1)
        return {k.strip(): parse_json_value(v)}
    raise ValueError("--set 需要 key=value 或 JSON 对象，收到: %r" % s)


def deep_merge(base, extra):
    for k, v in extra.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _apply_patch(result, patch):
    """应用一组键值；键含 '.' 时视为点分路径写入契约叶，否则普通合并。"""
    for k, v in patch.items():
        if "." in k:
            contract.set_by_path(result, tuple(k.split(".")), v)
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
    """扫描输入目录，返回 (files, degradations)。"""
    files = {"case_json": None, "task_md": None, "materials": [], "data": []}
    deg = []
    if not os.path.isdir(input_dir):
        return files, ["input_dir_missing: %s" % input_dir]
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, input_dir).replace("\\", "/")
            base = name.lower()
            try:
                size = os.path.getsize(p)
            except OSError as e:
                deg.append("unreadable: %s (%s)" % (rel, e))
                continue
            if base == "case.json":
                files["case_json"] = p
            elif base == "task.md":
                files["task_md"] = p
            elif base.endswith(DATA_EXTS):
                files["data"].append(p)
            elif base.endswith(TEXT_EXTS):
                files["materials"].append(p)
            if size > MAX_FILE_BYTES:
                deg.append("file_too_large(skipped): %s (%d bytes)" % (rel, size))
    return files, deg


def case_id_from(case_obj, input_dir):
    """优先取 case_id/id/challenge_id；缺失时用目录名+内容哈希。"""
    for k in ("case_id", "id", "challenge_id", "challengeId", "armiId"):
        if isinstance(case_obj, dict) and case_obj.get(k):
            return str(case_obj[k])
    digest = hashlib.sha1(
        json.dumps(case_obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    base = os.path.basename(os.path.abspath(input_dir)) or "input"
    return "%s-%s" % (base, digest)


def minimal_result(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": [],
        "degradations": [],
        "runtime": {
            "started_at": utcnow_iso(),
            "finished_at": None,
            "seconds": None,
        },
        "artifacts": [],
    }


def append_change_log(result, step, action, files=None, contract_delta=None):
    seq = 1
    for item in result.get("change_log", []):
        if isinstance(item, dict) and isinstance(item.get("seq"), int):
            seq = max(seq, item["seq"] + 1)
    entry = {
        "ts": utcnow_iso(),
        "seq": seq,
        "step": step,
        "action": action,
        "files": files or [],
    }
    if contract_delta is not None:
        entry["contract_delta"] = contract_delta
    result.setdefault("change_log", []).append(entry)
    return result


# --------------------------------------------------------------------------- #
# 子命令实现
# --------------------------------------------------------------------------- #
def cmd_init(args):
    files, deg = discover_input(args.input)
    case_obj = {}
    case_err = None
    if files["case_json"]:
        case_obj, case_err = load_json_robust(files["case_json"])
    else:
        case_err = "case_json_missing"

    cid = case_id_from(case_obj, args.input)
    result = minimal_result(cid)

    # 契约发现与回填
    chosen = None
    if isinstance(case_obj, dict):
        contract_obj, candidates, chosen = contract.discover_contract(case_obj)
        if contract_obj is not None:
            result, added, _leaves = contract.backfill(result, contract_obj)
            result.setdefault("_contract_meta", {
                "found": True,
                "path": contract.path_str(chosen) if chosen else None,
                "leaf_count": len(contract.flatten(contract_obj)),
                "backfilled": added,
                "aliases_checked": len(candidates),
            })
        else:
            result.setdefault("_contract_meta", {
                "found": False,
                "path": None,
                "leaf_count": 0,
                "backfilled": [],
                "aliases_checked": len(candidates),
            })
    else:
        result.setdefault("_contract_meta", {
            "found": False, "path": None, "leaf_count": 0,
            "backfilled": [], "aliases_checked": 0,
        })

    if case_err:
        result["errors"].append("case_json: %s" % case_err)
    for d in deg:
        result["degradations"].append(d)

    if files["case_json"] is None and files["task_md"] is None:
        result["status"] = "insufficient_evidence"
        result["uncertainty"].append({
            "item": "no_case_or_task",
            "reason": "输入目录未发现 case.json 与 task.md",
            "pending_human": True,
        })
    elif case_err and not isinstance(case_obj, dict):
        result["status"] = "input_error"
        result["errors"].append("contract unavailable due to: %s" % case_err)

    append_change_log(result, "init", "落盘 partial 结果骨架并回填契约叶字段",
                      files=[os.path.relpath(files["case_json"], args.input).replace("\\", "/")]
                      if files["case_json"] else [])

    os.makedirs(args.output, exist_ok=True)
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    _write_plan(args, files, chosen)
    return result


def _write_plan(args, files, chosen):
    os.makedirs(args.work, exist_ok=True)
    lines = [
        "# 执行计划（init 生成，可覆写）",
        "",
        "- case.json: %s" % (files["case_json"] or "缺失"),
        "- task.md: %s" % (files["task_md"] or "缺失"),
        "- data: %d 个；materials: %d 个" % (len(files["data"]), len(files["materials"])),
        "- 契约路径: %s" % (contract.path_str(chosen) if chosen else "未发现"),
        "",
        "## 待办（按阶段推进）",
        "1. 读数据字典与字段语义（列名/单位/类型/取值域）。",
        "2. 记录清洗口径：缺失/异常/重复先记录再处理，保留原始值。",
        "3. 统计：描述统计、分组比较、相关/趋势、假设检验前提。",
        "4. 选图：按类型与目的选型，说明不推荐图型的原因。",
        "5. 编码/分面/配色：位置/长度优先，Okabe-Ito 色盲安全配色。",
        "6. 标注与图注：轴=变量(单位)，误差线写明 SD/SEM/CI 与 n，显著性给 p。",
        "7. 导出 SVG+PDF+≥300dpi PNG；中文先探测 CJK 字体。",
        "8. 每步用 `python3 scripts/main.py update` 增量落盘并记 change_log。",
        "9. 收尾运行 check --require-contract 并补齐未填充契约叶。",
    ]
    atomic_write_text(os.path.join(args.work, "plan.md"), "\n".join(lines) + "\n")


def cmd_update(args):
    rpath = os.path.join(args.output, "result.json")
    result, err = load_json_robust(rpath)
    if err:
        result = minimal_result("unknown")
        result["errors"].append("result.json: %s" % err)
    if not isinstance(result, dict):
        result = minimal_result("unknown")

    # 应用更新
    patches = []
    if args.set:
        for item in args.set:
            patches.append(parse_set_arg(item))
    if args.status:
        patches.append({"status": args.status})
    if args.set_key is not None:
        patches.append({args.set_key: parse_json_value(args.set_value)})

    contract_obj = _reload_contract(args.input)
    for patch in patches:
        _apply_patch(result, patch)

    delta = None
    if args.log:
        step = args.step or "update"
        delta = _contract_delta(result, contract_obj)
        append_change_log(result, step, args.log, files=args.files or [],
                          contract_delta=delta)

    # 若 status 被显式给出且不在枚举，置 partial 并记录
    if result.get("status") not in STATUS_VALUES:
        result["errors"].append("invalid_status: %r -> partial" % result.get("status"))
        result["status"] = "partial"

    atomic_write_json(rpath, result)
    return result


def _reload_contract(input_dir):
    files, _deg = discover_input(input_dir)
    if not files["case_json"]:
        return None
    case_obj, err = load_json_robust(files["case_json"])
    if err or not isinstance(case_obj, dict):
        return None
    contract_obj, _cands, _chosen = contract.discover_contract(case_obj)
    return contract_obj


def _contract_delta(result, contract_obj):
    if contract_obj is None:
        return None
    return contract.fill_report(result, contract_obj)


def cmd_contract_report(args):
    rpath = os.path.join(args.output, "result.json")
    result, err = load_json_robust(rpath)
    if err:
        print(json.dumps({"error": err, "total": 0, "filled": 0, "missing": []},
                         ensure_ascii=False))
        return
    contract_obj = _reload_contract(args.input)
    if contract_obj is None:
        print(json.dumps({"error": "contract_not_found", "total": 0, "filled": 0,
                          "missing": []}, ensure_ascii=False))
        return
    rep = contract.fill_report(result, contract_obj)
    rep["complete"] = rep["total"] == rep["filled"]
    print(json.dumps(rep, ensure_ascii=False, indent=2))


def cmd_check(args):
    rpath = os.path.join(args.output, "result.json")
    result, err = load_json_robust(rpath)
    fails = []
    passes = []

    if err:
        fails.append("result.json: %s" % err)
    elif not isinstance(result, dict):
        fails.append("result.json: not_a_dict")
    else:
        if not result.get("case_id"):
            fails.append("missing case_id")
        else:
            passes.append("case_id present")
        if result.get("status") in STATUS_VALUES:
            passes.append("status valid: %s" % result.get("status"))
        else:
            fails.append("invalid/missing status")
        for k in ("sources", "uncertainty", "change_log"):
            if isinstance(result.get(k), list):
                passes.append("%s is list" % k)
            else:
                fails.append("%s missing/not list" % k)

        contract_obj = _reload_contract(args.input)
        if args.require_contract:
            if contract_obj is None:
                fails.append("require-contract but contract not found in case.json")
            else:
                issues = contract.validate(result, contract_obj)
                if issues:
                    fails.append("contract issues: %d (%s)" % (
                        len(issues),
                        "; ".join("%s:%s" % (i["path"], i["issue"]) for i in issues[:6])))
                else:
                    passes.append("contract aligned (%d leaves)" %
                                  len(contract.flatten(contract_obj)))
                rep = contract.fill_report(result, contract_obj)
                passes.append("contract filled %d/%d" % (rep["filled"], rep["total"]))
        _check_artifacts(result, args.output, passes, fails)

    for p in passes:
        print("PASS %s" % p)
    for f in fails:
        print("FAIL %s" % f)
    print("SUMMARY %d pass, %d fail" % (len(passes), len(fails)))
    if args.fail and fails:
        return 1
    return 0


def _check_artifacts(result, output_dir, passes, fails):
    arts = result.get("artifacts", [])
    if not isinstance(arts, list):
        fails.append("artifacts not list")
        return
    found = 0
    for a in arts:
        if not isinstance(a, dict) or not a.get("path"):
            continue
        p = a["path"]
        if not os.path.isabs(p):
            p = os.path.join(output_dir, p)
        if os.path.exists(p):
            found += 1
        else:
            fails.append("artifact missing: %s" % a.get("path"))
    passes.append("artifacts found %d/%d" % (found, len(arts)))


def cmd_run(args):
    """一次性兜底：init + 纯标准库数据画像 + 契约进度落盘。"""
    result = cmd_init(args)
    profile = _basic_profile(args.input)
    if profile:
        result.setdefault("data_profile", profile)
        append_change_log(result, "run", "生成基础数据画像（标准库）", files=[])
    # 依据材料是否存在设置 status
    files, _deg = discover_input(args.input)
    if files["case_json"] and files["data"]:
        if result.get("status") == "partial":
            pass  # 保持 partial，等待 Agent 主分析
    elif not files["case_json"] and not files["task_md"]:
        result["status"] = "insufficient_evidence"
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    return result


def _basic_profile(input_dir):
    """纯标准库读 CSV/TSV，输出最简画像（行/列/类型/描述统计）。"""
    files, _deg = discover_input(input_dir)
    profile = []
    for p in files["data"]:
        base = os.path.basename(p).lower()
        if base.endswith((".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")):
            profile.append({"file": os.path.relpath(p, input_dir).replace("\\", "/"),
                            "note": "binary_data_skipped_stdlib"})
            continue
        text, enc, err = read_text_robust(p)
        if err:
            profile.append({"file": os.path.relpath(p, input_dir).replace("\\", "/"),
                            "error": err})
            continue
        lines = text.splitlines()
        if not lines:
            profile.append({"file": os.path.relpath(p, input_dir).replace("\\", "/"),
                            "note": "empty"})
            continue
        try:
            import csv
            import io
            sample = "\n".join(lines[:2000])
            dialect = csv.Sniffer().sniff(sample[:2048], delimiters=",\t;|")
            reader = csv.reader(io.StringIO(sample), dialect)
            rows = list(reader)
        except Exception as e:
            profile.append({"file": os.path.relpath(p, input_dir).replace("\\", "/"),
                            "error": "csv_parse: %s" % e})
            continue
        if not rows:
            continue
        header = rows[0]
        data = rows[1:]
        numeric = {}
        for ci, col in enumerate(header):
            vals = []
            for r in data:
                if ci < len(r):
                    vals.append(r[ci])
            num = 0
            for v in vals:
                try:
                    float(v)
                    num += 1
                except (TypeError, ValueError):
                    pass
            numeric[col] = {"non_numeric": len(vals) - num, "present": len(vals),
                            "missing": len(vals) - len([v for v in vals if v != ""])}
        profile.append({
            "file": os.path.relpath(p, input_dir).replace("\\", "/"),
            "encoding": enc,
            "n_rows": len(data),
            "n_cols": len(header),
            "columns": {c: numeric.get(c, {}) for c in header},
        })
    return profile


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _common_args():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--output", default="/app/output")
    common.add_argument("--work", default="/app/work")
    return common


def build_parser():
    p = argparse.ArgumentParser(
        prog="main.py",
        description="科研数据可视化 Skill 主控脚本（契约对齐 + 增量落盘 + 自检）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", parents=[_common_args()],
                   help="发现输入、提取契约、写 result.json 骨架")
    up = sub.add_parser("update", parents=[_common_args()], help="增量更新 result.json")
    up.add_argument("--set", action="append", default=[], help="key=value 或 JSON")
    up.add_argument("--set-key", default=None)
    up.add_argument("--set-value", default=None)
    up.add_argument("--status", default=None)
    up.add_argument("--step", default=None)
    up.add_argument("--log", default=None)
    up.add_argument("--files", nargs="*", default=[])

    sub.add_parser("contract-report", parents=[_common_args()],
                   help="打印 output_contract 填充进度")
    chk = sub.add_parser("check", parents=[_common_args()],
                         help="自检 result.json 与产物")
    chk.add_argument("--require-contract", action="store_true")
    chk.add_argument("--fail", action="store_true", help="存在 FAIL 项时退出码 1")
    sub.add_parser("run", parents=[_common_args()],
                   help="init + 基础数据画像兜底")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            cmd_init(args)
        elif args.cmd == "update":
            cmd_update(args)
        elif args.cmd == "contract-report":
            cmd_contract_report(args)
        elif args.cmd == "check":
            return cmd_check(args)
        elif args.cmd == "run":
            cmd_run(args)
        else:
            parser.print_help()
        return 0
    except SystemExit:
        raise
    except Exception as e:  # 任何异常都不中断 Harness，落盘最小合法产物
        try:
            os.makedirs(args.output, exist_ok=True)
            rpath = os.path.join(args.output, "result.json")
            existing, _err = load_json_robust(rpath)
            if not isinstance(existing, dict) or not existing.get("case_id"):
                existing = minimal_result("error")
            existing["errors"] = list(existing.get("errors") or [])
            existing["errors"].append("fatal: %s: %s" % (type(e).__name__, e))
            existing.setdefault("status", "input_error")
            atomic_write_json(rpath, existing)
        except Exception:
            pass
        print("ERROR %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
