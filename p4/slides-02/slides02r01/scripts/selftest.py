#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 包本体自检 + 运行结果校验 + 终版落盘。

子命令：
  selftest   自检本 Skill 包（SKILL.md frontmatter、脚本可编译、无伪造产物），退出码 1=有问题
  check      校验 result.json 的契约字段、产物文件、evidence 溯源（locator+quote），写 self_check.json
  finalize   写终版 status 与终检记录，退出码 1=有缺口
只依赖 Python 标准库。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

VALID_STATUS = {"complete", "partial", "failed", "insufficient_evidence", "input_error"}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def write_atomic(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def root_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------- selftest ----------------
def check_frontmatter(skill_md):
    problems = []
    if not os.path.isfile(skill_md):
        return ["SKILL.md 缺失"]
    try:
        with open(skill_md, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except Exception as e:  # noqa: BLE001
        return ["SKILL.md 读取失败: %s" % e]
    if not text.lstrip().startswith("---"):
        problems.append("SKILL.md 缺 YAML frontmatter 起始行")
        return problems
    end = text.find("\n---", 3)
    if end < 0:
        problems.append("SKILL.md frontmatter 未闭合")
        return problems
    fm = text[3:end]
    for key in ("name:", "description:", "version:"):
        if key not in fm:
            problems.append("frontmatter 缺 %s" % key)
    return problems


def cmd_selftest(args=None):
    root = root_dir()
    problems = check_frontmatter(os.path.join(root, "SKILL.md"))
    scripts_dir = os.path.join(root, "scripts")
    if os.path.isdir(scripts_dir):
        for name in sorted(os.listdir(scripts_dir)):
            if name.endswith(".py"):
                p = os.path.join(scripts_dir, name)
                try:
                    with open(p, "r", encoding="utf-8-sig") as f:
                        compile(f.read(), p, "exec")  # 仅语法检查，不写 __pycache__
                except Exception as e:  # noqa: BLE001
                    problems.append("脚本编译失败 %s: %s" % (name, e))
    else:
        problems.append("scripts/ 缺失")
    for name in ("output", "result.json", "demo"):
        if os.path.exists(os.path.join(root, name)):
            problems.append("包内不应携带预写产物: %s" % name)
    report = {"ok": not problems, "problems": problems, "checked_at": now_iso()}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if problems else 0


# ---------------- check ----------------
def cmd_check(args):
    output_dir = os.environ.get("OUTPUT_DIR", "/app/output")
    result_path = args.result or os.path.join(output_dir, "result.json")
    result, err = load_json(result_path)
    gaps = []
    if err:
        gaps.append("result.json 无法解析: " + err)
        result = None
    if isinstance(result, dict):
        if not result.get("case_id"):
            gaps.append("缺 case_id")
        if result.get("status") not in VALID_STATUS:
            gaps.append("status 非法: %r" % result.get("status"))
        for key in ("sources", "evidence_map", "uncertainty", "pending_human", "change_log"):
            if key not in result:
                gaps.append("缺保留字段 " + key)
        ev = result.get("evidence_map")
        if isinstance(ev, list):
            for i, e in enumerate(ev):
                if not isinstance(e, dict):
                    gaps.append("evidence[%d] 非对象" % i)
                    continue
                if not e.get("claim"):
                    gaps.append("evidence[%d] 缺 claim" % i)
                if not e.get("locator"):
                    gaps.append("evidence[%d] 缺 locator" % i)
                if not e.get("quote"):
                    gaps.append("evidence[%d] 缺逐字 quote" % i)
                if e.get("quote_verified") is False:
                    gaps.append("evidence[%d] quote_verified=false（需确认或标记）" % i)
        pf = result.get("presentation_file")
        if pf and isinstance(pf, str):
            p = pf if os.path.isabs(pf) else os.path.join(output_dir, pf)
            if not os.path.exists(p):
                gaps.append("presentation_file 不存在: %s" % pf)
        else:
            gaps.append("presentation_file 未产出")
    report = {"ok": not gaps, "gaps": gaps, "checked_at": now_iso()}
    write_atomic(os.path.join(output_dir, "self_check.json"), report)
    if isinstance(result, dict):
        result["self_check"] = {"run": True, "gaps": gaps, "checked_at": now_iso()}
        write_atomic(result_path, result)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if gaps else 0


# ---------------- finalize ----------------
def cmd_finalize(args):
    output_dir = os.environ.get("OUTPUT_DIR", "/app/output")
    result_path = args.result or os.path.join(output_dir, "result.json")
    result, err = load_json(result_path)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 1
    status = args.status if args.status in VALID_STATUS else "partial"
    result["status"] = status
    log = result.setdefault("change_log", [])
    if not isinstance(log, list):
        log = []
        result["change_log"] = log
    log.append({"t": now_iso(), "step": "finalize", "action": "finalize",
                "status": status, "reason": args.note or "终版落盘"})
    write_atomic(result_path, result)
    print(json.dumps({"ok": True, "status": status, "result_path": result_path}, ensure_ascii=False))
    return 1 if status != "complete" else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="自检与终检")
    ap.add_argument("--result", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    sub.add_parser("check")
    p = sub.add_parser("finalize")
    p.add_argument("--status", required=True)
    p.add_argument("--note", default="")
    args = ap.parse_args(argv)
    return {"selftest": cmd_selftest, "check": cmd_check, "finalize": cmd_finalize}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
