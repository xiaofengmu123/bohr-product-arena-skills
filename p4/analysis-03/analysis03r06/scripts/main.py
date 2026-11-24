#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化 Skill 主控脚本（仅 Python 标准库）。

五个子命令：
  init     发现输入、提取 output_contract、立即写 result.json 骨架 + work/plan.json
  profile  调用 core_impl 计算 work/data_profile.json（确定性数据画像）
  plan     依据画像与图表规则生成 work/figure_model.json 候选图型
  update   增量更新 result.json 并维护 change_log / runtime
  check    校验契约回填、figure_model 与 data_profile 一致性、产物格式

缺省 --input /input --output /app/output --work /app/work。
任何命令退出码恒为 0（仅 check --fail 且存在 FAIL 项时退出 1），保证 Harness 不被中断。
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core  # noqa: E402

STATUS_VALUES = ("ok", "partial", "insufficient_evidence", "input_error")
RESERVED_KEYS = ("case_id", "status", "artifacts", "sources", "uncertainty",
                 "pending_human", "change_log", "errors", "degradations",
                 "runtime", "data_profile", "figure_model")


# --------------------------------------------------------------------------- #
# result.json 生命周期
# --------------------------------------------------------------------------- #
def load_result(output_dir):
    p = os.path.join(output_dir, "result.json")
    if os.path.exists(p):
        obj, err = core.load_json_robust(p)
        if not err:
            return obj
    return {}


def save_result(output_dir, obj):
    os.makedirs(output_dir, exist_ok=True)
    core.atomic_write_json(os.path.join(output_dir, "result.json"), obj)


def next_seq(obj):
    seqs = [e.get("seq", -1) for e in obj.get("change_log", []) if isinstance(e, dict)]
    return (max(seqs) + 1) if seqs else 0


def append_log(obj, step, action, files=None):
    obj.setdefault("change_log", []).append(
        {"ts": core.utcnow_iso(), "seq": next_seq(obj), "step": step,
         "action": action, "files": files or []})
    return obj


def touch_runtime(obj, finished=False):
    rt = obj.setdefault("runtime", {})
    rt.setdefault("started_at", core.utcnow_iso())
    if finished:
        rt["finished_at"] = core.utcnow_iso()
        try:
            a = datetime.datetime.strptime(rt["started_at"], "%Y-%m-%dT%H:%M:%SZ")
            b = datetime.datetime.strptime(rt["finished_at"], "%Y-%m-%dT%H:%M:%SZ")
            rt["seconds"] = max(0.0, (b - a).total_seconds())
        except (ValueError, KeyError):
            pass
    return obj


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_init(args):
    files, deg = core.discover_input(args.input)
    result = {k: [] for k in ("artifacts", "sources", "uncertainty",
                              "pending_human", "change_log", "errors",
                              "degradations")}
    result["status"] = "partial"
    result["runtime"] = {"started_at": core.utcnow_iso(),
                         "finished_at": None, "seconds": None}
    case_id = None
    if files["case_json"]:
        obj, err = core.load_json_robust(files["case_json"])
        if not err:
            for k in ("case_id", "id", "challenge_id", "challengeId"):
                if obj.get(k):
                    case_id = str(obj[k])
                    break
    if not case_id:
        base = os.path.basename(os.path.normpath(args.input)) or "case"
        case_id = base + "-" + core.content_hash(json.dumps(sorted(deg), ensure_ascii=False))[:8]
    result["case_id"] = case_id
    result["degradations"] = deg
    contract, cpath = None, None
    if files["case_json"]:
        obj, err = core.load_json_robust(files["case_json"])
        if not err:
            contract, cpath = core.find_contract(obj)
    if contract is None and files["task_md"]:
        text, _e, _err = core.read_text_robust(files["task_md"])
        m = re.search(r"(?is)(output_contract|outputContract|result_schema|expected_output)"
                      r"\s*[:：]\s*(\{.*?\})\s*$", text)
        if m:
            try:
                contract = json.loads(m.group(2))
            except json.JSONDecodeError:
                contract = None
    if contract is None:
        contract = {"required": ["case_id", "status", "artifacts", "sources",
                                 "uncertainty", "change_log"]}
        cpath = "minimal_fallback"
    template = core.leafify(contract)
    for k, v in template.items():
        if k not in RESERVED_KEYS:
            result[k] = v
    append_log(result, "init", "落盘 partial 骨架；契约路径=%s；数据文件=%d"
               % (cpath, len(files["data"])), files["data"])
    save_result(args.output, result)
    os.makedirs(args.work, exist_ok=True)
    core.atomic_write_json(os.path.join(args.work, "contract_template.json"), template)
    plan = {"phase_A_done": True, "contract_path": cpath, "case_id": case_id,
            "data_files": [os.path.relpath(p, args.input).replace("\\", "/")
                           for p in files["data"]],
            "steps": [
                {"step": "profile", "desc": "计算 data_profile.json（列类型/缺失/统计/离群）"},
                {"step": "plan", "desc": "依据画像与 task.md 生成/精化 figure_model.json"},
                {"step": "render", "desc": "按 figure_model 用 matplotlib 出图并导出 SVG/PDF/PNG"},
                {"step": "verify", "desc": "check 校验契约回填、图元与源数据一致性"},
                {"step": "finalize", "desc": "补齐图注/证据/uncertainty，设 status 并收尾"}]}
    core.atomic_write_json(os.path.join(args.work, "plan.json"), plan)
    print(json.dumps({"status": "ok", "case_id": case_id, "contract_path": cpath,
                      "n_data": len(files["data"]),
                      "result": os.path.join(args.output, "result.json")},
                     ensure_ascii=False))
    return 0


def cmd_profile(args):
    os.makedirs(args.work, exist_ok=True)
    profile = core.compute_profile(args.input)
    core.atomic_write_json(os.path.join(args.work, "data_profile.json"), profile)
    res = load_result(args.output)
    if res:
        res["data_profile"] = os.path.join(args.work, "data_profile.json")
        res["degradations"] = list(dict.fromkeys(
            res.get("degradations", []) + profile["degradations"]))
        append_log(res, "profile", "计算 data_profile.json：%d 张表" % len(profile["tables"]))
        save_result(args.output, res)
    print(json.dumps({"status": "ok", "n_tables": len(profile["tables"]),
                      "sources": [s["path"] for s in profile["sources"]],
                      "degradations": profile["degradations"]}, ensure_ascii=False))
    return 0


def cmd_plan(args):
    profile, err = core.load_json_robust(os.path.join(args.work, "data_profile.json"))
    if err or not profile:
        print(json.dumps({"status": "error",
                          "reason": "data_profile.json 缺失，先运行 profile"}))
        return 0
    fm = core.propose_figures(profile)
    files, _ = core.discover_input(args.input)
    task_text = ""
    if files["task_md"]:
        task_text, _e, _err = core.read_text_robust(files["task_md"])
    fm["task_summary"] = (task_text[:600] + "…") if len(task_text) > 600 else task_text
    out = os.path.join(args.work, "figure_model.json")
    core.atomic_write_json(out, fm)
    res = load_result(args.output)
    if res:
        res["figure_model"] = out
        append_log(res, "plan", "生成 figure_model.json：%d 张候选图" % len(fm["figures"]))
        save_result(args.output, res)
    print(json.dumps({"status": "ok", "n_figures": len(fm["figures"]),
                      "types": [f["chart_type"] for f in fm["figures"]],
                      "path": out}, ensure_ascii=False))
    return 0


def cmd_update(args):
    res = load_result(args.output)
    if not res:
        print(json.dumps({"status": "error", "reason": "result.json 缺失，先运行 init"}))
        return 0
    if args.set:
        patch = core_parse_set(args.set)
    elif args.set_key and args.set_value is not None:
        patch = {args.set_key: core_parse_value(args.set_value)}
    else:
        patch = {}
    if args.status:
        patch["status"] = args.status if args.status in STATUS_VALUES else "partial"
    for k, v in patch.items():
        core.set_path(res, k, v)
    if args.log:
        append_log(res, args.step or "update", args.log)
    touch_runtime(res)
    save_result(args.output, res)
    print(json.dumps({"status": "ok", "updated": sorted(patch.keys()),
                      "log": args.log or ""}, ensure_ascii=False))
    return 0


def _check_contract(res, template):
    fails = []
    for p in core.contract_paths(template):
        dotted = p.strip("/").replace("/", ".")
        if not core.has_path(res, dotted):
            fails.append("contract_missing: %s" % p)
    return fails


def _check_figure_model(fm, profile):
    fails = []
    known = {src: {c["name"] for c in t.get("columns", [])}
             for src, t in profile.get("tables", {}).items()}
    for f in fm.get("figures", []):
        fid = f.get("figure_id", "?")
        if not f.get("figure_id"):
            fails.append("figure_missing_id")
        if f.get("chart_type") not in core.CHART_TYPES:
            fails.append("%s: bad_chart_type %r" % (fid, f.get("chart_type")))
        src = f.get("source", "")
        avail = known.get(src, set())
        if src and avail:
            for c in f.get("columns", []):
                if c.get("name") not in avail:
                    fails.append("%s: column_not_in_profile %s" % (fid, c.get("name")))
        if f.get("chart_type") == "pie":
            dtypes = set()
            for c in f.get("columns", []):
                for t in profile.get("tables", {}).get(src, {}).get("columns", []):
                    if t["name"] == c.get("name"):
                        dtypes.add(t["dtype"])
            if "numeric" not in dtypes:
                fails.append("%s: pie_needs_composition" % fid)
    return fails


def cmd_check(args):
    res = load_result(args.output)
    fails, warns = [], []
    if not res:
        fails.append("result.json missing")
    else:
        if not res.get("case_id"):
            fails.append("case_id missing")
        if res.get("status") not in STATUS_VALUES:
            fails.append("status invalid: %r" % res.get("status"))
        for k in ("sources", "uncertainty", "change_log"):
            if not isinstance(res.get(k), list):
                fails.append("%s not a list" % k)
    if args.require_contract:
        tpl, err = core.load_json_robust(os.path.join(args.work, "contract_template.json"))
        if err:
            warns.append("contract_template.json missing; skip contract check")
        elif res:
            fails.extend(_check_contract(res, tpl))
    fm_path = res.get("figure_model") if res else None
    if not fm_path and os.path.exists(os.path.join(args.work, "figure_model.json")):
        fm_path = os.path.join(args.work, "figure_model.json")
    if fm_path and os.path.exists(fm_path):
        fm, err = core.load_json_robust(fm_path)
        if err:
            fails.append("figure_model.json bad: %s" % err)
        else:
            pf, perr = core.load_json_robust(os.path.join(args.work, "data_profile.json"))
            if perr:
                warns.append("data_profile.json missing; skip figure/data 一致性")
            else:
                fails.extend(_check_figure_model(fm, pf))
    for art in (res or {}).get("artifacts", []):
        p = art.get("path") if isinstance(art, dict) else art
        if not p:
            continue
        if not os.path.isabs(p):
            p = os.path.join(args.output, p)
        info = core.artifact_info(p)
        if not info.get("valid"):
            fails.append("artifact_invalid: %s (%s)" % (p, info.get("reason", "unknown")))
        elif p.lower().endswith(".png") and info.get("dpi") is not None and info["dpi"] < 300:
            warns.append("png_dpi_below_300: %s = %s" % (p, info["dpi"]))
    out = {"status": "FAIL" if fails else "PASS", "fails": fails, "warns": warns,
           "n_fails": len(fails), "n_warns": len(warns)}
    print(json.dumps(out, ensure_ascii=False))
    return 1 if (args.fail and fails) else 0


def core_parse_set(s):
    s = s.strip()
    if s.startswith("{"):
        return json.loads(s)
    if "=" in s:
        k, v = s.split("=", 1)
        return {k.strip(): core_parse_value(v)}
    raise ValueError("--set 需要 key=value 或 JSON 对象，收到: %r" % s)


def core_parse_value(v):
    v = (v or "").strip()
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default="/input")
    common.add_argument("--output", default="/app/output")
    common.add_argument("--work", default="/app/work")
    ap = argparse.ArgumentParser(description="科研数据可视化 Skill 主控脚本",
                                 parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", parents=[common], help="落盘 result.json 骨架与 plan.json")
    sub.add_parser("profile", parents=[common], help="计算 data_profile.json")
    sub.add_parser("plan", parents=[common], help="生成 figure_model.json 候选图型")
    u = sub.add_parser("update", parents=[common], help="增量更新 result.json")
    u.add_argument("--set", help='key=value 或 JSON 对象，如 \'status="ok"\'')
    u.add_argument("--set-key")
    u.add_argument("--set-value")
    u.add_argument("--status", choices=STATUS_VALUES)
    u.add_argument("--log")
    u.add_argument("--step")
    c = sub.add_parser("check", parents=[common], help="自检 result.json/契约/产物")
    c.add_argument("--require-contract", action="store_true")
    c.add_argument("--fail", action="store_true", help="存在 FAIL 时退出码 1")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return {"init": cmd_init, "profile": cmd_profile, "plan": cmd_plan,
                "update": cmd_update, "check": cmd_check}[args.cmd](args)
    except SystemExit:
        raise
    except Exception as e:
        try:
            res = load_result(args.output)
            if not res:
                res = {"case_id": "error", "status": "input_error", "errors": [str(e)],
                       "sources": [], "uncertainty": [], "pending_human": [],
                       "change_log": [], "degradations": [],
                       "runtime": {"started_at": core.utcnow_iso(),
                                   "finished_at": None, "seconds": None}}
                save_result(args.output, res)
            else:
                res.setdefault("errors", []).append(str(e))
                res["status"] = res.get("status") or "partial"
                save_result(args.output, res)
        except Exception:
            pass
        print(json.dumps({"status": "error", "reason": str(e)}, ensure_ascii=False),
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
