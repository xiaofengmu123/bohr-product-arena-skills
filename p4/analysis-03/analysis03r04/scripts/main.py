#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科研数据可视化 Skill 主控脚本（仅标准库）。

子命令：
  init      发现输入、提取 output_contract、落盘 result.json 骨架 + work/plan.md
  update    增量更新 result.json：--set / --status / --log / --limitation /
            --evidence / --uncertainty / --artifact，并维护 change_log
  check     自检 result.json 结构与产物格式；--require-contract 校验契约回填；
            --auto-status 按证据自动降级；--fail 时存在 FAIL 才退出 1
  selftest  空目录 / 坏 JSON / 契约回填 / 表格读取 / 图型推荐 的内部自测

约定：默认退出码恒为 0（仅 check --fail 且存在 FAIL 项时退出 1），
保证 Harness 不被中断；所有写盘先落临时文件再原子替换。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core

DEFAULTS = {"input": "/input", "output": "/app/output", "work": "/app/work"}


# --------------------------------------------------------------------------- #
# 参数解析辅助
# --------------------------------------------------------------------------- #
def _parse_set(s):
    s = (s or "").strip()
    if not s:
        raise ValueError("--set 不能为空")
    if s.startswith("{"):
        return json.loads(s)
    if "=" in s:
        k, v = s.split("=", 1)
        return {k.strip(): core.parse_json_value(v)}
    raise ValueError("--set 需为 key=value 或 JSON 对象，收到 %r" % s)


def _parse_json_flag(s, flag):
    s = (s or "").strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        raise ValueError("%s 需为 JSON 字符串，收到 %r" % (flag, s))
    if not isinstance(obj, dict):
        raise ValueError("%s 需为 JSON 对象，收到 %r" % (flag, s))
    return obj


def _set_dotted(result, dotted, value):
    parts = [p for p in dotted.split(".") if p]
    cur = result
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_init(args):
    files, deg = core.discover_input(args.input)
    case_obj, case_err = {}, None
    if files["case_json"]:
        case_obj, case_err = core.load_json_robust(files["case_json"])
        if case_err:
            deg.append("case_json_read: " + case_err)
    task_text = ""
    if files["task_md"]:
        task_text, _enc, err = core.read_text_robust(files["task_md"])
        if err:
            deg.append("task_md_read: " + err)
    contract, cpath = core.extract_contract(case_obj, task_text)
    template = core.template_from_value(contract) if contract else {}
    if not template:
        deg.append("contract_missing: 未发现 output_contract，使用最小兜底字段集")
    case_id = core.compute_case_id(case_obj, args.input, files)
    result = core.build_skeleton(case_id, template, cpath, files, deg, case_err)
    core.write_result(args.output, result)
    os.makedirs(args.work, exist_ok=True)
    core.atomic_write_text(os.path.join(args.work, "plan.md"),
                           core.build_plan(files, template, cpath))
    core.atomic_write_json(os.path.join(args.work, "contract_template.json"), template)
    print(json.dumps({
        "case_id": case_id, "status": result["status"],
        "contract_source": cpath, "contract_keys": list(template.keys()),
        "input_files": core.summarize_files(files),
        "degradations": len(result["degradations"]),
    }, ensure_ascii=False))
    return 0


def cmd_update(args):
    result, err = core.load_result(args.output)
    if err:
        raise RuntimeError("无法读取 result.json: " + err)
    applied = []
    if args.set is not None:
        for k, v in _parse_set(args.set).items():
            _set_dotted(result, k, v)
            applied.append("%s=%s" % (k, json.dumps(v, ensure_ascii=False)))
    if args.status:
        result["status"] = args.status
        applied.append("status=%s" % args.status)
    if args.set_key is not None:
        val = core.parse_json_value(args.set_value) if args.set_value is not None else None
        _set_dotted(result, args.set_key, val)
        applied.append("%s=%s" % (args.set_key, json.dumps(val, ensure_ascii=False)))
    for ev in args.evidence or []:
        ev = _parse_json_flag(ev, "--evidence")
        core.add_source(result, ev.get("locator", ""), ev.get("quote", ""),
                        ev.get("kind", "value"), ev.get("level", "verified"))
        applied.append("source:%s" % ev.get("locator", "?"))
    for l in args.limitation or []:
        l = _parse_json_flag(l, "--limitation")
        core.add_limitation(result, l.get("kind", "other"), l.get("level", "warning"),
                            l.get("detail", ""), l.get("pending_human", False),
                            l.get("recoverable", True))
        applied.append("limitation:%s" % l.get("kind", "?"))
    for u in args.uncertainty or []:
        u = _parse_json_flag(u, "--uncertainty")
        core.add_uncertainty(result, u.get("message", ""), u.get("reason", ""),
                             u.get("level", "info"), u.get("pending_human", False))
        applied.append("uncertainty")
    artifact_paths = []
    for a in args.artifact or []:
        a = _parse_json_flag(a, "--artifact")
        core.add_artifact(result, a.get("path", ""), a.get("format", "unknown"),
                          a.get("role", "main"), a.get("dpi"))
        applied.append("artifact:%s" % a.get("path", "?"))
        if a.get("path"):
            artifact_paths.append(a.get("path"))
    if not applied and not args.log:
        raise ValueError("update 未提供任何变更（--set/--status/--log/--evidence/...）")
    core.append_change(result, "update", args.log or "; ".join(applied),
                       artifact_paths)
    core.write_result(args.output, result)
    print(json.dumps({"applied": applied, "change_seq": result["change_log"][-1]["seq"],
                      "status": result.get("status")}, ensure_ascii=False))
    return 0


def cmd_check(args):
    result, err = core.load_result(args.output)
    if err:
        issues = [("FAIL", "result.json 不可读: " + err)]
    else:
        tmpl_path = os.path.join(args.work, "contract_template.json")
        tmpl = {}
        if os.path.isfile(tmpl_path):
            tmpl, _e = core.load_json_robust(tmpl_path)
        if not isinstance(tmpl, dict):
            tmpl = result.get("_contract_template", {}) or {}
        issues = core.check_result(result, require_contract=args.require_contract,
                                   contract_template=tmpl)
        # 产物格式校验
        for a in result.get("artifacts") or []:
            p = a.get("path")
            if not p:
                issues.append(("WARN", "artifact 缺 path"))
                continue
            full = p if os.path.isabs(p) else os.path.join(args.output, p)
            ok, msg = core.validate_artifact(full)
            issues.append(("PASS" if ok else "FAIL", "artifact %s: %s" % (p, msg)))
        # 状态诚实性：按证据自动降级
        ceiling = core.status_ceiling(result)
        declared = result.get("status")
        if args.auto_status and declared != ceiling and \
                core.STATUS_RANK.get(declared, 0) > core.STATUS_RANK.get(ceiling, 0):
            result["status"] = ceiling
            core.append_change(result, "check", "status 由 %s 自动降级为 %s" % (declared, ceiling))
            core.write_result(args.output, result)
            issues.append(("WARN", "status 自动降级: %s -> %s" % (declared, ceiling)))
    fails = [i for i in issues if i[0] == "FAIL"]
    for tag, msg in issues:
        print("%s %s" % (tag, msg))
    print("SUMMARY fail=%d warn=%d pass=%d" % (
        len(fails),
        len([i for i in issues if i[0] == "WARN"]),
        len([i for i in issues if i[0] == "PASS"])))
    return 1 if (args.fail and fails) else 0


def cmd_selftest(args):
    import core_impl
    tmp = args.tmp or core.tempfile.mkdtemp(prefix="selftest_")
    os.makedirs(tmp, exist_ok=True)
    tests = []

    def run(name, fn):
        try:
            fn()
            tests.append((name, True, None))
        except Exception as e:  # noqa: BLE001
            tests.append((name, False, repr(e)))

    def t_empty():
        ind = os.path.join(tmp, "empty")
        os.makedirs(ind, exist_ok=True)
        out = os.path.join(tmp, "out_empty")
        main(["init", "--input", ind, "--output", out, "--work", os.path.join(tmp, "w1")])
        r, e = core.load_result(out)
        assert e is None, e
        assert r["status"] == "insufficient_evidence"

    def t_contract():
        ind = os.path.join(tmp, "case")
        os.makedirs(ind, exist_ok=True)
        case = {"case_id": "t-1", "output_contract": {"a": 1, "nested": {"b": [1, 2]}}}
        core.atomic_write_json(os.path.join(ind, "case.json"), case)
        out = os.path.join(tmp, "out_contract")
        main(["init", "--input", ind, "--output", out, "--work", os.path.join(tmp, "w2")])
        r, e = core.load_result(out)
        assert e is None, e
        assert r["case_id"] == "t-1"
        assert r.get("a") is None and r.get("nested") == {"b": []}

    def t_bad_json():
        ind = os.path.join(tmp, "bad")
        os.makedirs(ind, exist_ok=True)
        core.atomic_write_text(os.path.join(ind, "case.json"), "{bad json")
        out = os.path.join(tmp, "out_bad")
        main(["init", "--input", ind, "--output", out, "--work", os.path.join(tmp, "w3")])
        r, e = core.load_result(out)
        assert e is None, e
        assert r["status"] == "input_error"

    def t_table_and_figure():
        ind = os.path.join(tmp, "tab")
        os.makedirs(ind, exist_ok=True)
        csv_text = "species,sepal_length,sepal_width\nsetosa,5.1,3.5\nversicolor,7.0,3.2\nsetosa,4.9,3.0\nvirginica,6.3,3.3\n"
        core.atomic_write_text(os.path.join(ind, "iris.csv"), csv_text)
        t, degs = core.read_table(os.path.join(ind, "iris.csv"))
        assert t is not None, degs
        assert t["n_rows"] == 4 and "species" in t["columns"]
        prof = core.profile_columns(t["columns"], t["rows"])
        rec = core.recommend_figure(prof, goal="comparison")
        assert rec.get("figure_recommendation"), rec
        assert any(a.get("figure") == "pie" for a in rec["alternatives_rejected"])

    def t_no_fabrication():
        rec = core.recommend_figure({})
        assert rec.get("degradation") == "no_data"
        assert rec.get("limitation", {}).get("pending_human") is True

    def t_status_ceiling():
        r = {"status": "ok", "errors": [], "artifacts": [{"path": "x.png", "format": "png"}],
             "limitations": [{"kind": "data_quality", "level": "missing",
                              "detail": "缺字段", "pending_human": True,
                              "recoverable": True}],
             "uncertainty": [], "change_log": []}
        assert core.status_ceiling(r) == "partial"

    run("empty_input", t_empty)
    run("contract_backfill", t_contract)
    run("bad_json_input", t_bad_json)
    run("table_and_figure", t_table_and_figure)
    run("no_fabrication", t_no_fabrication)
    run("status_ceiling", t_status_ceiling)

    ok = sum(1 for _n, s, _e in tests if s)
    for name, s, e in tests:
        print("PASS" if s else "FAIL", name, ("" if s else e))
    print("SELFTEST %d/%d passed" % (ok, len(tests)))
    return 0 if ok == len(tests) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="main.py", description="科研数据可视化 Skill 主控脚本")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=DEFAULTS["input"])
    common.add_argument("--output", default=DEFAULTS["output"])
    common.add_argument("--work", default=DEFAULTS["work"])

    p_init = sub.add_parser("init", parents=[common],
                            help="落盘 result.json 骨架与 work/plan.md")

    p_upd = sub.add_parser("update", parents=[common], help="增量更新 result.json")
    p_upd.add_argument("--set", help="key=value 或 JSON 对象，如 status=\"ok\"")
    p_upd.add_argument("--status")
    p_upd.add_argument("--set-key")
    p_upd.add_argument("--set-value")
    p_upd.add_argument("--log")
    p_upd.add_argument("--evidence", action="append",
                       help="JSON: {locator,quote,kind,level}")
    p_upd.add_argument("--limitation", action="append",
                       help="JSON: {kind,level,detail,pending_human,recoverable}")
    p_upd.add_argument("--uncertainty", action="append",
                       help="JSON: {message,reason,level,pending_human}")
    p_upd.add_argument("--artifact", action="append",
                       help="JSON: {path,format,role,dpi}")

    p_chk = sub.add_parser("check", parents=[common],
                           help="自检 result.json 与产物")
    p_chk.add_argument("--require-contract", action="store_true", default=True)
    p_chk.add_argument("--no-require-contract", dest="require_contract",
                       action="store_false")
    p_chk.add_argument("--auto-status", action="store_true")
    p_chk.add_argument("--fail", action="store_true")

    p_self = sub.add_parser("selftest", parents=[common], help="内部自测")
    p_self.add_argument("--tmp", help="自测用临时目录（默认自动创建）")
    args = parser.parse_args(argv)

    cmd = args.cmd
    if cmd == "init":
        return cmd_init(args)
    if cmd == "update":
        return cmd_update(args)
    if cmd == "check":
        return cmd_check(args)
    if cmd == "selftest":
        return cmd_selftest(args)
    raise ValueError("未知子命令: %s" % cmd)


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:  # noqa: BLE001
        print("ERROR %s: %s" % (type(e).__name__, e), file=sys.stderr)
        code = 0
    sys.exit(code)
