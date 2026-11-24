#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性科研数据可视化 Skill 主控脚本（仅标准库）。

职责：
  1) 发现输入：不假设固定文件名/列名/键名，扫描 case.json、task.md、CSV/SQLite 等材料；
  2) 提取 output_contract：从 case.json（output_contract/outputContract/contract/
     result_schema/expected_output 等键）或 task.md 提取，叶值置 null/[] 作为回填模板；
  3) 先落盘合法骨架：init 立即写 /app/output/result.json（status=partial），任何中途
     异常都不留空产物；之后用 update 做增量改进并维护 change_log；
  4) 确定性：manifest 计算输入/输出指纹，固定随机种子，保证同一输入两次产物一致；
  5) check 自检：校验 result.json 结构、契约字段、证据映射与 SVG/PDF/PNG 产物格式。

子命令：
  init     发现输入、提取契约、写 result.json 骨架与 work/plan.md、work/contract.json
  update   以 --set / --set-key+--set-value / --status / --log 增量更新 result.json
  manifest 计算并回写 determinism（seed / input_fingerprint / output_fingerprint）
  stats    用纯标准库做确定性统计（描述统计/分组汇总/相关/检验），写 work/stats.json
  check    校验 result.json 与 /app/output 下的产物；--fail 时遇 FAIL 项才非零退出

缺省参数：--input /input --output /app/output --work /app/work。除 check --fail 存在
FAIL 项外，任何命令退出码恒为 0，保证 Harness 不被中断。
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

import core_impl  # noqa: E402  (确定性统计/清洗/SVG/指纹，仅标准库)

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件读取上限 2MB，超限跳过并记录
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
RESERVED_KEYS = ("case_id", "status", "sources", "uncertainty", "change_log",
                 "errors", "degradations", "runtime", "artifacts", "determinism")
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")
DATA_EXTS = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")


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
            with open(path, "r", encoding=enc) as fh:
                return fh.read(), enc, None
        except UnicodeDecodeError as e:
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
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2,
                                       sort_keys=True) + "\n")
    return path


def load_json_robust(path):
    """读取 JSON；坏 JSON 不抛异常，返回 (obj, error)。"""
    text, enc, err = read_text_robust(path)
    if err:
        return {}, err
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return {}, "bad_json: %s" % e
    return obj, None


def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_json_value(v):
    v = (v or "").strip()
    if v == "":
        return None
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v  # 无法解析时按字符串


def parse_set_arg(s):
    """--set 支持 'status="ok"' 或 JSON 对象 '{"a":1}'。"""
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


def set_path(obj, dotted, value):
    parts = [p for p in dotted.split(".") if p]
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return obj


def get_path(obj, dotted, default=None):
    parts = [p for p in dotted.split(".") if p]
    cur = obj
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


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


# --------------------------------------------------------------------------- #
# 契约提取
# --------------------------------------------------------------------------- #
CONTRACT_NORMALIZED = ("outputcontract", "resultcontract", "contract",
                       "resultschema", "expectedoutput")


def normalize_key(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def find_contract(obj, path=""):
    """在 case.json 中查找含 contract/schema 语义的字段，返回 (value, path)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if normalize_key(k) in CONTRACT_NORMALIZED and isinstance(v, (dict, list)):
                return v, (path + "." + k).strip(".")
            r = find_contract(v, (path + "." + k).strip("."))
            if r[0] is not None:
                return r
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            r = find_contract(v, path + "[%d]" % i)
            if r[0] is not None:
                return r
    return None, ""


def leafify(value):
    """把契约对象的所有叶值置 null（列表置 []），保留键名与层级作为回填模板。"""
    if isinstance(value, dict):
        return {k: leafify(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def extract_contract_from_text(text):
    """从 task.md 等文本中兜底提取 output_contract 字段名。"""
    if not text:
        return {}
    names = re.findall(r"output_contract\s*[:：]\s*([^\n]+)", text)
    if not names:
        names = re.findall(r"result\.json[^\n]*", text)
    return {}


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
def load_case(files):
    """读取 case.json，返回 (case_obj, case_err)。"""
    if not files["case_json"]:
        return {}, "case_json_missing"
    obj, err = load_json_robust(files["case_json"])
    if err:
        return {}, err
    if not isinstance(obj, dict):
        return {}, "case_json_not_object"
    return obj, None


def derive_case_id(case, files, input_dir):
    for k in ("case_id", "id", "challenge_id", "armiId"):
        if isinstance(case.get(k), str) and case.get(k).strip():
            return case[k].strip()
    name = os.path.basename(os.path.abspath(input_dir)) or "case"
    digest = core_impl.compute_input_fingerprint(input_dir)[:12] or "noinput"
    return "%s-%s" % (name, digest)


def cmd_init(args):
    files, deg = discover_input(args.input)
    case, case_err = load_case(files)
    if case_err:
        deg.append("case_json: " + case_err)

    # 契约：优先 case.json，其次 task.md 文本兜底
    contract, cpath = find_contract(case)
    if contract is None:
        task_text = ""
        if files["task_md"]:
            task_text, _enc, _err = read_text_robust(files["task_md"])
        fallback = extract_contract_from_text(task_text)
        if fallback:
            contract = fallback
            cpath = "task.md(extracted)"
        else:
            contract = {}
            deg.append("output_contract_missing: 使用最小字段集兜底")

    template = leafify(contract) if isinstance(contract, dict) else {}
    if not isinstance(contract, dict):
        deg.append("output_contract_not_object: 按空对象回填")

    case_id = derive_case_id(case, files, args.input)

    result = {}
    deep_merge(result, template)
    collision = [k for k in RESERVED_KEYS if k in template]
    now = utcnow_iso()
    result.update({
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "change_log": [{
            "ts": now, "seq": 1, "step": "init",
            "action": "落盘 partial 结果骨架，保证任何中途崩溃都有合法产物",
            "files": ["result.json", "plan.md", "contract.json"],
        }],
        "errors": [],
        "degradations": list(deg),
        "runtime": {"started_at": now, "finished_at": None, "seconds": None},
        "artifacts": [],
        "determinism": {"seed": 42, "input_fingerprint": None,
                        "output_fingerprint": None, "rerun": "pending"},
    })

    if collision:
        result.setdefault("uncertainty", []).append(
            "output_contract 与保留键冲突（%s），以本 Skill 语义为准" % ", ".join(collision))

    os.makedirs(args.work, exist_ok=True)
    os.makedirs(args.output, exist_ok=True)
    atomic_write_json(os.path.join(args.work, "contract.json"),
                      {"template": template, "path": cpath})
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    plan = core_impl.render_plan(files, case, contract, cpath, deg, case_id)
    atomic_write_text(os.path.join(args.work, "plan.md"), plan)
    print(json.dumps({"case_id": case_id, "status": "partial",
                      "contract_path": cpath, "degradations": deg},
                     ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# update
# --------------------------------------------------------------------------- #
def _load_result(output_dir):
    path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(path):
        return None, "result_json_missing"
    obj, err = load_json_robust(path)
    if err:
        return None, err
    if not isinstance(obj, dict):
        return None, "result_json_not_object"
    return obj, None


def cmd_update(args):
    obj, err = _load_result(args.output)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 0
    log = obj.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        obj["change_log"] = log
    next_seq = max([(x.get("seq") if isinstance(x, dict) else 0) for x in log] + [0]) + 1

    changed = False
    if args.set:
        try:
            patch = parse_set_arg(args.set)
        except ValueError as e:
            patch = None
            obj.setdefault("errors", []).append("update_bad_set: %s" % e)
        if isinstance(patch, dict):
            deep_merge(obj, patch)
            changed = True
    if args.set_key is not None:
        set_path(obj, args.set_key, parse_json_value(args.set_value))
        changed = True
    if args.status is not None:
        if args.status not in STATUS_VALUES:
            obj.setdefault("errors", []).append(
                "bad_status: %s (允许 %s)" % (args.status, ", ".join(STATUS_VALUES)))
        else:
            obj["status"] = args.status
            changed = True

    # 每次 update 都追加 change_log（即使只是记录日志）
    entry = {
        "ts": utcnow_iso(), "seq": next_seq,
        "step": args.step or "update",
        "action": args.log or ("set=%s" % args.set if args.set else "manual update"),
        "files": [f for f in (args.files or "").split(",") if f],
    }
    log.append(entry)
    obj["runtime"]["updated_at"] = utcnow_iso()
    atomic_write_json(os.path.join(args.output, "result.json"), obj)
    print(json.dumps({"ok": True, "changed": changed, "seq": next_seq,
                      "status": obj.get("status")}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# manifest（确定性指纹）
# --------------------------------------------------------------------------- #
def cmd_manifest(args):
    obj, err = _load_result(args.output)
    if err:
        obj = {"errors": [], "degradations": [], "determinism": {}}
        obj.setdefault("errors", []).append(err)
    det = obj.setdefault("determinism", {})
    if not isinstance(det, dict):
        det = {}
        obj["determinism"] = det
    det.setdefault("seed", 42)
    det["input_fingerprint"] = core_impl.compute_input_fingerprint(args.input)
    det["output_fingerprint"] = core_impl.compute_dir_fingerprint(args.output)
    # 产物若尚未产生，标记 rerun 状态；由 check 完成复核
    if det.get("output_fingerprint") is None:
        det["rerun"] = "pending"
    else:
        det["rerun"] = det.get("rerun", "pending")
    atomic_write_json(os.path.join(args.output, "result.json"), obj)
    print(json.dumps(det, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# stats（确定性统计，纯标准库）
# --------------------------------------------------------------------------- #
def cmd_stats(args):
    files, deg = discover_input(args.input)
    data_path = None
    if getattr(args, "data", None):
        data_path = args.data
    elif files["data"]:
        data_path = files["data"][0]
    elif files["materials"]:
        data_path = files["materials"][0]
    if not data_path:
        out = {"error": "no_data_found", "degradations": deg}
    else:
        try:
            columns, rows, meta = core_impl.read_table(data_path)
            out = {
                "file": data_path,
                "meta": meta,
                "columns": core_impl.classify_columns(columns, rows),
                "descriptives": core_impl.describe_table(columns, rows),
                "correlations": core_impl.correlation_matrix(columns, rows),
                "outliers": core_impl.outlier_report(columns, rows),
                "degradations": deg,
                "deterministic": True,
            }
        except Exception as e:  # 不崩溃：记录并降级
            out = {"error": "stats_failed: %s" % e, "degradations": deg}
    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(os.path.join(args.work, "stats.json"), out)
    print(json.dumps({"file": data_path, "keys": sorted(out.keys())},
                     ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# check（自检）
# --------------------------------------------------------------------------- #
def _check_png(path):
    """解析 PNG IHDR(宽高) 与 pHYs(分辨率)；返回 (ok, info)。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read(64)
    except OSError as e:
        return False, "unreadable: %s" % e
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return False, "not_png"
    import struct
    w, h = struct.unpack(">II", data[16:24])
    info = {"width": w, "height": h}
    # pHYs 块：位置在 IHDR 之后，逐块扫描到第一个 IDAT 前
    try:
        with open(path, "rb") as fh:
            fh.read(8)
            while True:
                head = fh.read(8)
                if len(head) < 8:
                    break
                length, ctype = struct.unpack(">I4s", head)
                payload = fh.read(length)
                fh.read(4)  # crc
                if ctype == b"pHYs" and len(payload) >= 9:
                    xppm, yppm, unit = struct.unpack(">IIB", payload[:9])
                    if unit == 1:  # 米制
                        info["dpi_x"] = round(xppm * 0.0254)
                        info["dpi_y"] = round(yppm * 0.0254)
                if ctype == b"IDAT":
                    break
    except OSError:
        pass
    return True, info


def _check_svg(path):
    try:
        import xml.etree.ElementTree as ET
        ET.parse(path)
        return True, {}
    except Exception as e:
        return False, "svg_parse_error: %s" % e


def _check_pdf(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
        return head.startswith(b"%PDF-"), {}
    except OSError as e:
        return False, "unreadable: %s" % e


def cmd_check(args):
    obj, err = _load_result(args.output)
    report = {"ok": True, "checks": [], "fails": []}
    if err:
        report["ok"] = False
        report["fails"].append("result_json: " + err)
        atomic_write_json(os.path.join(args.work or args.output, "check_report.json"), report)
        _print_report(report)
        return 1 if args.fail else 0

    def add(name, ok, detail=""):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            report["ok"] = False
            report["fails"].append(name + (": " + detail if detail else ""))

    # 1) 结构
    add("case_id", isinstance(obj.get("case_id"), str) and bool(obj.get("case_id")))
    add("status", obj.get("status") in STATUS_VALUES, "status=%s" % obj.get("status"))
    for key in ("sources", "uncertainty", "change_log", "errors", "degradations"):
        add("has_" + key, isinstance(obj.get(key), list), key)

    # 2) 契约字段回填
    contract_path = os.path.join(args.work or args.output, "contract.json")
    if args.require_contract and os.path.isfile(contract_path):
        ct, cerr = load_json_robust(contract_path)
        template = ct.get("template", {}) if isinstance(ct, dict) else {}
        missing = [k for k in template if k not in obj]
        add("contract_fields", not missing,
            "missing=%s" % (", ".join(missing) if missing else "none"))
    elif args.require_contract:
        add("contract_fields", False, "contract.json missing")

    # 3) 证据溯源
    bad_src = []
    for s in obj.get("sources", []):
        if not isinstance(s, dict) or "locator" not in s or "quote" not in s:
            bad_src.append(s)
    add("sources_shape", not bad_src, "bad=%d" % len(bad_src))

    # 4) 确定性
    det = obj.get("determinism", {})
    add("determinism_seed", "seed" in det, "seed=%s" % det.get("seed"))
    if det.get("input_fingerprint"):
        cur = core_impl.compute_input_fingerprint(args.input)
        add("determinism_input_match", cur == det["input_fingerprint"],
            "stored=%s.. current=%s.." % (det["input_fingerprint"][:8], cur[:8]))
    else:
        add("determinism_input_match", False, "input_fingerprint missing")

    # 5) 产物格式
    for a in obj.get("artifacts", []):
        p = a.get("path") if isinstance(a, dict) else a
        if not isinstance(p, str):
            add("artifact:" + str(a)[:40], False, "not_a_path")
            continue
        absp = p if os.path.isabs(p) else os.path.join(args.output, p)
        exists = os.path.isfile(absp)
        add("artifact_exists:" + os.path.basename(p), exists, p)
        if not exists:
            continue
        low = p.lower()
        fmt = a.get("format") if isinstance(a, dict) else None
        if low.endswith(".png"):
            ok, info = _check_png(absp)
            add("artifact_png:" + os.path.basename(p), ok, str(info))
            if ok and info.get("dpi_x") is not None and info["dpi_x"] < 300:
                add("artifact_png_dpi:" + os.path.basename(p), False,
                    "dpi=%s < 300" % info["dpi_x"])
        elif low.endswith(".svg"):
            ok, info = _check_svg(absp)
            add("artifact_svg:" + os.path.basename(p), ok, str(info))
        elif low.endswith(".pdf"):
            ok, info = _check_pdf(absp)
            add("artifact_pdf:" + os.path.basename(p), ok, str(info))

    os.makedirs(args.work or args.output, exist_ok=True)
    atomic_write_json(os.path.join(args.work or args.output, "check_report.json"), report)
    _print_report(report)
    return 1 if (args.fail and report["fails"]) else 0


def _print_report(report):
    print(json.dumps({"ok": report["ok"], "fails": report["fails"]},
                     ensure_ascii=False))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _add_io_args(sp):
    """--input/--output/--work 必须在子命令后可用（Harness 以子命令开头调用）。"""
    sp.add_argument("--input", default="/input")
    sp.add_argument("--output", default="/app/output")
    sp.add_argument("--work", default="/app/work")


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="确定性科研数据可视化 Skill 主控脚本")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="发现输入、提取契约、写 result.json 骨架")
    _add_io_args(init)

    up = sub.add_parser("update", help="增量更新 result.json")
    _add_io_args(up)
    up.add_argument("--set", help='key=value 或 JSON 对象，如 status="ok"')
    up.add_argument("--set-key", help="点分路径，如 figures.panel_a.title")
    up.add_argument("--set-value", help="与 --set-key 配套的 JSON 值")
    up.add_argument("--status", choices=STATUS_VALUES)
    up.add_argument("--log", help="本次变更说明")
    up.add_argument("--step", help="阶段名，如 clean/stat/chart/export")
    up.add_argument("--files", help="逗号分隔的产物文件列表")

    manifest = sub.add_parser("manifest", help="计算并回写 determinism 指纹")
    _add_io_args(manifest)

    st = sub.add_parser("stats", help="纯标准库确定性统计")
    _add_io_args(st)
    st.add_argument("--data", help="指定数据文件路径（缺省自动发现）")

    chk = sub.add_parser("check", help="自检 result.json 与产物格式")
    _add_io_args(chk)
    chk.add_argument("--require-contract", action="store_true")
    chk.add_argument("--fail", action="store_true", help="存在 FAIL 项时退出码 1")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "update":
            return cmd_update(args)
        if args.cmd == "manifest":
            return cmd_manifest(args)
        if args.cmd == "stats":
            return cmd_stats(args)
        if args.cmd == "check":
            return cmd_check(args)
        return 0
    except SystemExit:
        raise
    except Exception as e:  # 任何异常都落盘最小合法结果，进程不崩溃
        try:
            os.makedirs(args.output, exist_ok=True)
            path = os.path.join(args.output, "result.json")
            if not os.path.isfile(path):
                atomic_write_json(path, {
                    "case_id": "unknown", "status": "input_error",
                    "sources": [], "uncertainty": ["fatal: %s" % e],
                    "change_log": [], "errors": [repr(e)],
                    "degradations": [], "runtime": {"started_at": utcnow_iso()},
                    "artifacts": [], "determinism": {"seed": 42},
                })
        except Exception:
            pass
        sys.stderr.write("main.py error: %s\n" % e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
