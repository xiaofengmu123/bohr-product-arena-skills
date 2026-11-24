#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— 科研图稿 Skill 的命令行入口（仅标准库）。

用法（SKILL.md 中统一 cd 到 /skills/weekly-skill 后以相对路径调用）：

  python scripts/main.py init      --case /input/case.json --out /app/output --work /app/work
  python scripts/main.py inventory --input /input --work /app/work
  python scripts/main.py template  --case /input/case.json --work /app/work
  python scripts/main.py model     --work /app/work
  python scripts/main.py plan      --work /app/work
  python scripts/main.py render    --work /app/work --out /app/output
  python scripts/main.py check     --work /app/work --out /app/output
  python scripts/main.py finalize  --status complete --work /app/work --out /app/output
  python scripts/main.py run       --case /input/case.json --input /input \
                                   --out /app/output --work /app/work

所有子命令失败时打印 JSON 错误对象并返回非零退出码；缺参数使用默认路径。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def _fail(msg):
    print(json.dumps({"ok": False, "error": str(msg)}, ensure_ascii=False))
    return 1


def _ensure(work, out):
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)


def cmd_init(a):
    _ensure(a.work, a.out)
    case = C.load_case(a.case)
    cid = C.extract_case_id(case)
    res = C.init_result(case, cid, os.path.join(a.out, "result.json"))
    C.write_json(os.path.join(a.work, "case.json"), case)
    print(json.dumps({"ok": True, "case_id": cid, "status": res["status"]}, ensure_ascii=False))
    return 0


def cmd_inventory(a):
    _ensure(a.work, a.out)
    items = C.discover_materials(a.input)
    inv = {"input_dir": a.input, "generated_at": C.now_iso(), "materials": items}
    C.write_json(os.path.join(a.work, "inventory.json"), inv)
    print(json.dumps({"ok": True, "materials": len(items),
                      "text": sum(1 for x in items if x.get("kind") == "text")}, ensure_ascii=False))
    return 0


def cmd_template(a):
    _ensure(a.work, a.out)
    case = C.load_case(a.case)
    cid = C.extract_case_id(case)
    tpl = C.make_template(cid)
    C.write_json(os.path.join(a.work, "figure_model.json"), tpl)
    print(json.dumps({"ok": True, "case_id": cid,
                      "path": os.path.join(a.work, "figure_model.json")}, ensure_ascii=False))
    return 0


def cmd_model(a):
    _ensure(a.work, a.out)
    p = os.path.join(a.work, "figure_model.json")
    if not os.path.isfile(p):
        return _fail("缺少 %s（先执行 template 或由 Agent 写入）" % p)
    try:
        raw = C.read_json(p)
        model = C.normalize_model(raw)
    except Exception as exc:
        return _fail("figure_model.json 结构非法: %s" % exc)
    C.write_json(os.path.join(a.work, "figure_model.json"), model)
    checks = C.validate_model(model)
    C.write_json(os.path.join(a.work, "model_checks.json"), checks)
    crit = [c for c in checks if c["severity"] == "critical" and not c["pass"]]
    print(json.dumps({"ok": True, "entities": len(model["entities"]),
                      "relations": len(model["relations"]), "steps": len(model["steps"]),
                      "evidence": len(model["evidence"]), "critical_failed": len(crit)},
                     ensure_ascii=False))
    return 0


def cmd_plan(a):
    _ensure(a.work, a.out)
    p = os.path.join(a.work, "figure_model.json")
    if not os.path.isfile(p):
        return _fail("缺少 figure_model.json")
    try:
        model = C.normalize_model(C.read_json(p))
    except Exception as exc:
        return _fail(str(exc))
    plan = C.compute_layout(model)
    C.write_json(os.path.join(a.work, "plan.json"), plan)
    print(json.dumps({"ok": True, "placements": len(plan["placements"]),
                      "routes": len(plan["routes"]),
                      "legend_entries": len(plan["legend_entries"])}, ensure_ascii=False))
    return 0


def cmd_render(a):
    _ensure(a.work, a.out)
    mp, pp = os.path.join(a.work, "figure_model.json"), os.path.join(a.work, "plan.json")
    if not os.path.isfile(mp) or not os.path.isfile(pp):
        return _fail("缺少 figure_model.json 或 plan.json")
    model = C.normalize_model(C.read_json(mp))
    plan = C.read_json(pp)
    svg = C.build_svg(plan, model)
    C.write_text(os.path.join(a.out, "figure.svg"), svg)
    C.write_text(os.path.join(a.work, "figure.svg"), svg)
    print(json.dumps({"ok": True, "svg": os.path.join(a.out, "figure.svg"),
                      "bytes": len(svg.encode("utf-8"))}, ensure_ascii=False))
    return 0


def cmd_check(a):
    _ensure(a.work, a.out)
    mp = os.path.join(a.work, "figure_model.json")
    pp = os.path.join(a.work, "plan.json")
    sp = os.path.join(a.work, "figure.svg")
    if not all(os.path.isfile(x) for x in (mp, pp, sp)):
        return _fail("缺少 model/plan/svg，先执行 model -> plan -> render")
    model = C.normalize_model(C.read_json(mp))
    plan = C.read_json(pp)
    svg = C.read_text(sp)
    checks = C.validate_all(model, plan, svg)
    C.write_json(os.path.join(a.work, "checks.json"), checks)
    # 合并进 result.json（增量，不覆盖未知字段）
    rp = os.path.join(a.out, "result.json")
    if os.path.isfile(rp):
        C.merge_result(rp, model=model, plan=plan, checks=checks,
                       note="model/plan/checks 合并")
    crit_fail = [c for c in checks if c["severity"] == "critical" and not c["pass"]]
    print(json.dumps({"ok": True, "checks": len(checks),
                      "critical_failed": len(crit_fail),
                      "critical_passed": C.checks_critical_passed(checks)}, ensure_ascii=False))
    return 0 if C.checks_critical_passed(checks) else 2


def cmd_finalize(a):
    _ensure(a.work, a.out)
    rp = os.path.join(a.out, "result.json")
    if not os.path.isfile(rp):
        return _fail("缺少 result.json（先执行 init）")
    status = a.status if a.status in ("complete", "partial", "failed") else "partial"
    # 若有 checks/plan/model 未合并，先合并一次
    for name, key in (("figure_model.json", "model"), ("plan.json", "plan"),
                      ("checks.json", "checks"), ("inventory.json", "inventory")):
        fp = os.path.join(a.work, name)
        if os.path.isfile(fp):
            kwargs = {}
            if key == "model":
                kwargs["model"] = C.normalize_model(C.read_json(fp))
            elif key == "plan":
                kwargs["plan"] = C.read_json(fp)
            elif key == "checks":
                kwargs["checks"] = C.read_json(fp)
            elif key == "inventory":
                kwargs["inventory"] = C.read_json(fp).get("materials")
            C.merge_result(rp, note="finalize 前合并 %s" % name, **kwargs)
    C.finalize_result(rp, a.work, a.out, status)
    print(json.dumps({"ok": True, "status": status, "result": rp}, ensure_ascii=False))
    return 0


def cmd_run(a):
    # 端到端确定性部分：不替代 Agent 的科研抽取，仅证明管线可跑通并产出诚实 partial。
    # 若 /app/work/figure_model.json 已存在则不覆盖，避免清空 Agent 已填写的模型。
    steps = [
        (cmd_init, argparse.Namespace(case=a.case, work=a.work, out=a.out)),
        (cmd_inventory, argparse.Namespace(input=a.input, work=a.work, out=a.out)),
    ]
    if not os.path.isfile(os.path.join(a.work, "figure_model.json")):
        steps.append((cmd_template, argparse.Namespace(case=a.case, work=a.work, out=a.out)))
    steps += [
        (cmd_model, argparse.Namespace(work=a.work, out=a.out)),
        (cmd_plan, argparse.Namespace(work=a.work, out=a.out)),
        (cmd_render, argparse.Namespace(work=a.work, out=a.out)),
    ]
    for fn, args in steps:
        rc = fn(args)
        if rc != 0:
            return rc
    rc_check = cmd_check(argparse.Namespace(work=a.work, out=a.out))
    mp = os.path.join(a.work, "figure_model.json")
    model = C.normalize_model(C.read_json(mp)) if os.path.isfile(mp) else {}
    has_content = bool(model.get("entities") or model.get("steps"))
    status = "complete" if (rc_check == 0 and has_content) else "partial"
    return cmd_finalize(argparse.Namespace(status=status, work=a.work, out=a.out))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="main.py")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--case", default="/input/case.json")
    common.add_argument("--input", default="/input")
    common.add_argument("--out", default="/app/output")
    common.add_argument("--work", default="/app/work")
    common.add_argument("--status", default="partial",
                        choices=["complete", "partial", "failed"])
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("init", parents=[common])
    sub.add_parser("inventory", parents=[common])
    sub.add_parser("template", parents=[common])
    sub.add_parser("model", parents=[common])
    sub.add_parser("plan", parents=[common])
    sub.add_parser("render", parents=[common])
    sub.add_parser("check", parents=[common])
    sub.add_parser("finalize", parents=[common])
    sub.add_parser("run", parents=[common])

    a = ap.parse_args(argv)
    if a.cmd is None:
        ap.print_help()
        return 1
    handler = {
        "init": cmd_init, "inventory": cmd_inventory, "template": cmd_template,
        "model": cmd_model, "plan": cmd_plan, "render": cmd_render,
        "check": cmd_check, "finalize": cmd_finalize, "run": cmd_run,
    }[a.cmd]
    try:
        return handler(a)
    except Exception as exc:  # 顶层兜底：不崩溃，给出可解释失败
        return _fail("unexpected: %s" % exc)


if __name__ == "__main__":
    sys.exit(main())
