#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化 Skill 主控脚本（仅标准库）。

职责：
  1) 发现输入：不假设固定文件名/列名/键名，扫描 case.json、task.md 与 CSV/TSV/SQLite 材料；
  2) 提取 output_contract：从 case.json（output_contract/outputContract/contract/
     result_schema/expected_output 等键）或 task.md 中提取，叶值置 null/[] 作回填模板；
  3) 先落盘合法骨架：init 立即写 /app/output/result.json（status=partial），任何中途
     异常都不留空产物；之后用 update/add-source 做增量改进并维护 change_log；
  4) check 自检：校验 result.json 结构与 SVG/PDF/PNG/CSV 产物；check-figure 校验
     图元与源数据一致性；finalize 收尾并记录 runtime。

子命令：
  init            发现输入、提取契约、写 result.json 骨架与 work/plan.md、work/contract_template.json
  update          以 --set / --set-key+--set-value / --status / --log 增量更新 result.json
  add-source      追加证据映射 {locator, quote, kind, field} 到 sources
  add-uncertainty 追加不确定项（--pending 时同时写入 pending_human）
  add-pending     追加待人工确认项到 pending_human
  check-figure    校验 figure_check 图元与源数据一致性与产物文件存在性
  check           校验 result.json 结构与 /app/output 下产物；--require-contract 校验契约字段
  finalize        更新 runtime.finished_at/seconds，运行 check，设最终 status（默认不改）

缺省参数：--input /input --output /app/output --work /app/work。除 check/check-figure 传
--fail 且存在 FAIL 项时退出 1 外，其余命令退出码恒为 0，保证 Harness 不被中断。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile

MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文本文件读取上限 2MB，超限跳过并记录
STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
RESERVED_KEYS = ("case_id", "status", "sources", "uncertainty", "pending_human",
                 "change_log", "errors", "degradations", "runtime", "artifacts",
                 "scorecard", "quality_gain", "figure_check")
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")
DATA_EXTS = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".parquet")
ARTIFACT_FORMATS = (".svg", ".pdf", ".png", ".csv", ".tsv", ".json", ".md", ".py")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def read_text_robust(path, limit=MAX_FILE_BYTES):
    """utf-8-sig 优先，逐级降级 gb18030/latin-1；返回 (text, encoding, error)。"""
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
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return path


def load_json_robust(path):
    """读取 JSON；坏 JSON 不抛异常，返回 (obj, error)。"""
    text, _enc, err = read_text_robust(path)
    if err:
        return {}, err
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return {}, "bad_json: %s" % e
    if not isinstance(obj, dict):
        return {}, "not_object: %s" % type(obj).__name__
    return obj, None


def utcnow_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_json_value(v):
    v = (v or "").strip()
    if v == "":
        return v
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


def content_hash(data_dir):
    h = hashlib.sha256()
    try:
        names = sorted(os.listdir(data_dir))
        h.update("\n".join(names).encode("utf-8", "replace"))
    except OSError:
        pass
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #
def discover_input(input_dir):
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


def _norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_contract(obj, path=""):
    """在 case.json 中查找含 contract/schema 语义的字段，返回 (value, path)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _norm_key(k) in CONTRACT_NORMALIZED:
                return v, (path + "." + k).strip(".")
            r, p = find_contract(v, path + "." + k)
            if r is not None:
                return r, p
        return None, None
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            r, p = find_contract(v, "%s[%d]" % (path, i))
            if r is not None:
                return r, p
    return None, None


def extract_contract_from_task(text):
    """从 task.md 中提取 result.json 字段说明（弱规则兜底，返回 None 表示未命中）。"""
    if not text:
        return None
    # 常见契约说明："result.json 至少包含/必须包含/含以下字段：a, b, c"
    m = re.search(
        r"(?:result\.json|结果文件|输出文件)[^\n]{0,40}?(?:包含|含|字段)[：:]\s*([^\n]+)",
        text)
    if not m:
        return None
    fields = [f.strip().strip("`*\"'").split("(")[0].strip()
              for f in re.split(r"[,，、;；]", m.group(1)) if f.strip()]
    return {f: None for f in fields} if fields else None


def build_template(value):
    """把契约对象递归转成回填模板：dict 保留结构，列表→[]，其他叶值→null。"""
    if isinstance(value, dict):
        return {k: build_template(v) for k, v in value.items()}
    if isinstance(value, list):
        return []
    return None


def contract_top_keys(template):
    return sorted(template.keys()) if isinstance(template, dict) else []


def resolve_case_id(case_obj, input_dir, files):
    for k in ("case_id", "id", "challenge_id", "caseId", "challengeId"):
        v = case_obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    if files.get("case_json"):
        try:
            return "case-" + content_hash(os.path.dirname(files["case_json"]))
        except Exception:
            pass
    return "case-" + content_hash(input_dir)


# --------------------------------------------------------------------------- #
# result.json 生命周期
# --------------------------------------------------------------------------- #
def next_seq(result):
    try:
        seqs = [int(c.get("seq", 0)) for c in result.get("change_log", [])
                if isinstance(c, dict)]
        return (max(seqs) + 1) if seqs else 1
    except (TypeError, ValueError):
        return 1


def append_change(result, step, action, files=None):
    result.setdefault("change_log", [])
    result["change_log"].append({
        "ts": utcnow_iso(),
        "seq": next_seq(result),
        "step": step,
        "action": action,
        "files": files or [],
    })
    return result


def new_result(case_id, template, contract_path, degradations, errors):
    result = {
        "case_id": case_id,
        "status": "partial",
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [],
        "errors": list(errors or []),
        "degradations": list(degradations or []),
        "runtime": {"started_at": utcnow_iso(), "finished_at": None, "seconds": None},
        "artifacts": {"result": "result.json"},
        "scorecard": {},
        "quality_gain": {"baseline": None, "skill": None, "reductions": [], "evidence": []},
        "figure_check": [],
    }
    # 契约字段回填（保留键冲突时以本 Skill 语义为准）
    collisions = []
    if isinstance(template, dict):
        for k, v in template.items():
            if k in result:
                collisions.append(k)
                continue
            result[k] = v
    if collisions:
        result["uncertainty"].append({
            "item": "output_contract 字段与保留键同名，按本 Skill 语义保留",
            "reason": "conflicting keys",
            "keys": collisions,
            "pending_human": False,
        })
    if contract_path:
        result["runtime"]["contract_path"] = contract_path
    result = append_change(result, "init", "落盘 result.json 骨架(status=partial)", None)
    return result


# --------------------------------------------------------------------------- #
# 子命令实现
# --------------------------------------------------------------------------- #
def cmd_init(args):
    files, deg = discover_input(args.input)
    errors = []
    case_obj = {}
    contract = None
    contract_path = None

    if files.get("case_json"):
        case_obj, err = load_json_robust(files["case_json"])
        if err:
            errors.append("case.json: %s" % err)
        contract, contract_path = find_contract(case_obj)
    if contract is None and files.get("task_md"):
        text, _enc, err = read_text_robust(files["task_md"])
        if err:
            errors.append("task.md: %s" % err)
        else:
            contract = extract_contract_from_task(text)
            if contract is not None:
                contract_path = "task.md(heuristic)"
    if contract is None:
        # 兜底：扫描 /input 下其他 JSON 材料中的契约字段
        for mp in files.get("materials", []):
            if not mp.lower().endswith(".json"):
                continue
            m_obj, merr = load_json_robust(mp)
            if merr:
                continue
            contract, contract_path = find_contract(m_obj)
            if contract is not None:
                contract_path = "%s(%s)" % (
                    os.path.relpath(mp, args.input).replace("\\", "/"), contract_path)
                break

    template = build_template(contract) if contract is not None else {}
    case_id = resolve_case_id(case_obj, args.input, files)
    result = new_result(case_id, template, contract_path, deg, errors)

    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    atomic_write_json(os.path.join(args.work, "contract_template.json"), template)

    plan = [
        "# plan.md — 执行计划（骨架）",
        "case_id: " + case_id,
        "contract_path: " + (contract_path or "none"),
        "contract_top_keys: " + json.dumps(contract_top_keys(template), ensure_ascii=False),
        "",
        "阶段 A：读数据字典/字段语义 → profile.py 生成 data_dictionary.json 与 chart_plan.json",
        "阶段 B：清洗口径→统计→选图→编码→标注→导出（SVG+PDF+≥300dpi PNG），逐步 update/add-source",
        "阶段 C：check --require-contract 自检 → 回填 scorecard/quality_gain → finalize",
        "图元一致性：check-figure 校验 figure_check 条目",
        "",
        "degradations: " + json.dumps(deg, ensure_ascii=False),
        "errors: " + json.dumps(errors, ensure_ascii=False),
    ]
    atomic_write_text(os.path.join(args.work, "plan.md"), "\n".join(plan) + "\n")

    summary = {
        "case_id": case_id,
        "status": "partial",
        "files": {k: (os.path.relpath(v, args.input) if v else None)
                  for k, v in files.items() if k in ("case_json", "task_md")},
        "data_count": len(files["data"]),
        "material_count": len(files["materials"]),
        "contract_top_keys": contract_top_keys(template),
        "degradations": len(deg),
        "errors": len(errors),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _load_result(output):
    p = os.path.join(output, "result.json")
    if not os.path.isfile(p):
        return {}, "result.json missing: %s" % p
    return load_json_robust(p)


def cmd_update(args):
    result, err = _load_result(args.output)
    if err:
        result = {}
    step = args.step or "update"
    action = args.log or ("set %s" % json.dumps(args.set, ensure_ascii=False)
                          if args.set else "update")
    patch = {}
    if args.set:
        try:
            patch = parse_set_arg(args.set)
        except ValueError as e:
            if not result:
                result = {"case_id": "unknown", "status": "partial"}
            result = append_change(result, step, "error: %s" % e, None)
            result.setdefault("errors", []).append(str(e))
            atomic_write_json(os.path.join(args.output, "result.json"), result)
            return 0
    elif args.set_key is not None:
        patch = {}
        set_path(patch, args.set_key, parse_json_value(args.set_value))
    if args.status:
        patch["status"] = args.status

    if not result:
        result = {"case_id": "unknown", "status": "partial", "sources": [],
                  "uncertainty": [], "pending_human": [], "change_log": [],
                  "errors": [], "degradations": [], "runtime": {}}

    if args.status and args.status not in STATUS_VALUES:
        result.setdefault("errors", []).append(
            "invalid_status: %r (allowed %s)" % (args.status, ", ".join(STATUS_VALUES)))

    # 保护 change_log/runtime 不被 patch 覆盖
    protected = ("change_log", "runtime")
    for k in protected:
        patch.pop(k, None)
    for k, v in patch.items():
        if isinstance(k, str) and "." in k:
            set_path(result, k, v)   # 点号路径：--set 'a.b=1' 与 --set-key 一致
        else:
            deep_merge(result, {k: v})
    if args.status and args.status in STATUS_VALUES:
        result["status"] = args.status
    result = append_change(result, step, action,
                           args.files if args.files else None)
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    print(json.dumps({"status": result.get("status"), "seq": next_seq(result) - 1,
                      "log": action}, ensure_ascii=False))
    return 0


def cmd_add_source(args):
    result, err = _load_result(args.output)
    if err:
        result = {}
    if not result:
        result = {"case_id": "unknown", "status": "partial", "sources": [],
                  "uncertainty": [], "pending_human": [], "change_log": [],
                  "errors": [], "degradations": [], "runtime": {}}
    entry = {"locator": args.locator or "", "quote": args.quote or "",
             "kind": args.kind or "value"}
    if args.field:
        entry["field"] = args.field
    result.setdefault("sources", []).append(entry)
    result = append_change(result, args.step or "evidence",
                           "add-source: %s" % (args.locator or args.quote))
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def cmd_add_uncertainty(args):
    result, err = _load_result(args.output)
    if err:
        result = {}
    if not result:
        result = {"case_id": "unknown", "status": "partial", "uncertainty": [],
                  "pending_human": [], "change_log": [], "errors": [],
                  "degradations": [], "runtime": {}, "sources": []}
    entry = {"item": args.item or "", "reason": args.reason or "",
             "pending_human": bool(args.pending)}
    result.setdefault("uncertainty", []).append(entry)
    if args.pending:
        result.setdefault("pending_human", []).append(args.item or "")
    result = append_change(result, args.step or "uncertainty",
                           "add-uncertainty: %s" % (args.item or ""))
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def cmd_add_pending(args):
    result, err = _load_result(args.output)
    if err:
        result = {}
    if not result:
        result = {"case_id": "unknown", "status": "partial", "pending_human": [],
                  "change_log": [], "errors": [], "degradations": [], "runtime": {}}
    result.setdefault("pending_human", []).append(args.item or "")
    result = append_change(result, args.step or "pending",
                           "add-pending: %s" % (args.item or ""))
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    print(json.dumps({"pending_human": result.get("pending_human")}, ensure_ascii=False))
    return 0


def _num_eq(a, b, tol=1e-9):
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))
    except (TypeError, ValueError):
        return str(a) == str(b)


def cmd_check_figure(args):
    result, err = _load_result(args.output)
    if err:
        result = {}
    entries = result.get("figure_check", [])
    if not isinstance(entries, list):
        entries = []
    fails, passes = [], 0
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            fails.append("figure_check[%d] not object" % i)
            continue
        fig = e.get("figure") or e.get("path") or ""
        checks = e.get("checks") or []
        if fig:
            full = fig if os.path.isabs(fig) else os.path.join(args.output, fig)
            if not os.path.isfile(full):
                fails.append("figure missing: %s" % fig)
        for c in checks:
            if not isinstance(c, dict):
                fails.append("figure_check[%d].checks 含非对象项" % i)
                continue
            ok = _num_eq(c.get("source_value"), c.get("plotted_value"))
            if c.get("match") is not None:
                ok = bool(c.get("match"))
            if ok:
                passes += 1
            else:
                fails.append("element '%s' mismatch: source=%r plotted=%r (%s)"
                             % (c.get("element"), c.get("source_value"),
                                c.get("plotted_value"), c.get("locator") or fig))
    report = {"figure_check_pass": passes, "figure_check_fail": len(fails),
              "fails": fails}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if fails:
        result.setdefault("errors", []).append(
            "figure_check: %d 处图元与源数据不一致" % len(fails))
        result = append_change(result, "check-figure",
                               "%d fail / %d pass" % (len(fails), passes))
        atomic_write_json(os.path.join(args.output, "result.json"), result)
    return 1 if (fails and args.fail) else 0


def cmd_check(args):
    result, err = _load_result(args.output)
    problems = []
    if err:
        problems.append("FAIL result.json: %s" % err)
        print(json.dumps({"ok": False, "problems": problems}, ensure_ascii=False,
                         indent=2))
        return 1 if args.fail else 0
    if not result.get("case_id"):
        problems.append("FAIL case_id 缺失或为空")
    if result.get("status") not in STATUS_VALUES:
        problems.append("FAIL status 非法: %r" % result.get("status"))
    for k in ("sources", "uncertainty", "pending_human", "change_log",
              "errors", "degradations", "runtime"):
        if k not in result:
            problems.append("FAIL 保留键缺失: %s" % k)
    for k in ("sources", "uncertainty", "pending_human", "change_log"):
        if k in result and not isinstance(result[k], list):
            problems.append("FAIL 键应为列表: %s" % k)
    if args.require_contract:
        tpl, terr = load_json_robust(os.path.join(args.work, "contract_template.json"))
        if terr:
            problems.append("FAIL 无法读取契约模板: %s" % terr)
        else:
            for k in contract_top_keys(tpl):
                if k not in result:
                    problems.append("FAIL 契约字段缺失: %s" % k)
    # 校验产物格式
    art = result.get("artifacts", {})
    if isinstance(art, dict):
        for name, rel in art.items():
            if not isinstance(rel, str) or name == "result":
                continue
            p = rel if os.path.isabs(rel) else os.path.join(args.output, rel)
            if not os.path.isfile(p):
                problems.append("WARN 产物缺失: %s → %s" % (name, rel))
            elif os.path.splitext(p)[1].lower() == ".svg":
                txt, _e, rerr = read_text_robust(p)
                if rerr or "<svg" not in txt:
                    problems.append("WARN SVG 无法解析: %s" % rel)
            elif os.path.splitext(p)[1].lower() == ".pdf":
                try:
                    with open(p, "rb") as fh:
                        head = fh.read(5)
                    if not head.startswith(b"%PDF-"):
                        problems.append("WARN PDF 头部非法: %s" % rel)
                except OSError as e:
                    problems.append("WARN PDF 读取失败: %s" % e)
    fails = [p for p in problems if p.startswith("FAIL")]
    print(json.dumps({"ok": not fails, "problems": problems}, ensure_ascii=False,
                     indent=2))
    return 1 if (fails and args.fail) else 0


def cmd_finalize(args):
    result, err = _load_result(args.output)
    if err:
        result = {}
    if not result:
        result = {"case_id": "unknown", "status": "partial", "sources": [],
                  "uncertainty": [], "pending_human": [], "change_log": [],
                  "errors": [], "degradations": [], "runtime": {}}
    rt = result.setdefault("runtime", {})
    started = rt.get("started_at")
    finished = utcnow_iso()
    rt["finished_at"] = finished
    try:
        if started:
            d0 = datetime.datetime.fromisoformat(started.replace("Z", "+00:00"))
            d1 = datetime.datetime.now(datetime.timezone.utc)
            rt["seconds"] = max(0, int((d1 - d0).total_seconds()))
    except (ValueError, TypeError):
        rt["seconds"] = None
    if args.status and args.status in STATUS_VALUES:
        result["status"] = args.status
    result = append_change(result, "finalize", "收尾并记录 runtime", None)
    atomic_write_json(os.path.join(args.output, "result.json"), result)
    # 落盘最终自检
    return cmd_check(args)


# --------------------------------------------------------------------------- #
# 命令行入口
# --------------------------------------------------------------------------- #
def _common():
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("--input", default="/input")
    c.add_argument("--output", default="/app/output")
    c.add_argument("--work", default="/app/work")
    return c


def build_parser():
    p = argparse.ArgumentParser(description="科研数据可视化 Skill 主控脚本",
                                parents=[_common()])
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init", help="发现输入、提取契约、写骨架", parents=[_common()])

    u = sub.add_parser("update", help="增量更新 result.json", parents=[_common()])
    u.add_argument("--set", help="key=value 或 JSON 对象")
    u.add_argument("--set-key", help="点号路径，如 'statistics.mean'")
    u.add_argument("--set-value", help="配合 --set-key 的 JSON/字符串值")
    u.add_argument("--status", choices=STATUS_VALUES)
    u.add_argument("--log", help="change_log 描述")
    u.add_argument("--step", default="update", help="change_log 步骤名")
    u.add_argument("--files", nargs="*", help="本步涉及文件")

    a = sub.add_parser("add-source", help="追加证据映射", parents=[_common()])
    a.add_argument("--locator", help="如 data.csv#L2")
    a.add_argument("--quote", help="逐字引用片段")
    a.add_argument("--kind", default="value")
    a.add_argument("--field", help="证据对应字段/图元名")
    a.add_argument("--step", default="evidence")

    uc = sub.add_parser("add-uncertainty", help="追加不确定项", parents=[_common()])
    uc.add_argument("--item", required=True)
    uc.add_argument("--reason", default="")
    uc.add_argument("--pending", action="store_true")
    uc.add_argument("--step", default="uncertainty")

    pd = sub.add_parser("add-pending", help="追加待人工确认项", parents=[_common()])
    pd.add_argument("--item", required=True)
    pd.add_argument("--step", default="pending")

    cf = sub.add_parser("check-figure", help="校验图元与源数据一致性", parents=[_common()])
    cf.add_argument("--fail", action="store_true")

    c = sub.add_parser("check", help="校验 result.json 与产物", parents=[_common()])
    c.add_argument("--require-contract", action="store_true")
    c.add_argument("--fail", action="store_true")

    f = sub.add_parser("finalize", help="收尾并记录 runtime", parents=[_common()])
    f.add_argument("--status", choices=STATUS_VALUES)
    f.add_argument("--require-contract", action="store_true")
    f.add_argument("--fail", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "update":
            return cmd_update(args)
        if args.cmd == "add-source":
            return cmd_add_source(args)
        if args.cmd == "add-uncertainty":
            return cmd_add_uncertainty(args)
        if args.cmd == "add-pending":
            return cmd_add_pending(args)
        if args.cmd == "check-figure":
            return cmd_check_figure(args)
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "finalize":
            return cmd_finalize(args)
        build_parser().print_help()
        return 0
    except SystemExit:
        raise
    except Exception as e:  # 任何异常不崩溃：写错误到 stderr，退出码 0
        sys.stderr.write("ERROR(main.py): %s\n" % e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
