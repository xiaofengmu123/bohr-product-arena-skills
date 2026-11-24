#!/usr/bin/env python3
"""selftest.py -- 交付前自检：校验 result.json 输出契约与 SKILL.md frontmatter。

校验项（对应评分维度：输出契约可核验性 / 可靠体验 / 生态文档完整性）：
  A. result.json 存在且为合法 JSON；
  B. case_id 非空；
  C. status 在允许集合内且诚实（partial 不得谎报 complete 需人工判断，本脚本只报值）；
  D. sources / uncertainty / change_log 为 list，pending_human 存在；
  E. change_log 非空（早落盘/增量改动的留痕证据）；
  F. case.json 的 output_contract 全部键已回填到 result.json；
  G. SKILL.md frontmatter 含 name / description / version。

退出码：0 = 全部通过；1 = 存在硬性失败。始终向 stdout 打印 JSON 汇总。
只依赖 Python 标准库。
"""
import argparse
import json
import os
import re
import sys

ALLOWED_STATUS = {"in_progress", "partial", "complete", "failed",
                  "ok", "insufficient_evidence", "input_error"}


def _stdout(text):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(text)
    sys.stdout.flush()


def read_text(path):
    if not path or not os.path.exists(path):
        return ""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_json_safe(path):
    if not path or not os.path.exists(path):
        return None
    try:
        return json.loads(read_text(path))
    except (json.JSONDecodeError, ValueError):
        return None


def parse_frontmatter(text):
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def output_contract_keys(case):
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict):
            return list(oc.keys())
        if isinstance(oc, list):
            return [str(k) for k in oc]
    return ["deliverable", "fact_source_map", "version_limits"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付前自检")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--out", default="/app/output/result.json")
    ap.add_argument("--skill", default="/skills/weekly-skill/SKILL.md")
    args = ap.parse_args(argv)

    checks = []
    ok = True

    def check(name, passed, detail=""):
        nonlocal ok
        if not passed:
            ok = False
        checks.append({"item": name, "pass": bool(passed), "detail": detail})

    # A/B/C/D/E/F -- result.json
    result = read_json_safe(args.out)
    check("result.json 存在且为合法 JSON", result is not None,
          "" if result is not None else "%s 缺失或坏 JSON" % args.out)
    if result is not None:
        check("case_id 非空", bool(result.get("case_id")),
              str(result.get("case_id")))
        status = result.get("status")
        check("status 在允许集合", status in ALLOWED_STATUS, str(status))
        for field in ("sources", "uncertainty", "change_log"):
            check("%s 为 list" % field, isinstance(result.get(field), list),
                  type(result.get(field)).__name__)
        check("pending_human 存在", "pending_human" in result)
        check("change_log 非空",
              isinstance(result.get("change_log"), list)
              and len(result.get("change_log", [])) > 0)

        case = read_json_safe(args.case)
        oc_keys = output_contract_keys(case)
        for k in oc_keys:
            check("output_contract 字段回填: %s" % k, k in result,
                  "missing" if k not in result else "ok")
        check("deliverable 指向存在的产物（若为路径）",
              _deliverable_ok(result.get("deliverable")))

    # G -- SKILL.md frontmatter
    skill_path = args.skill
    fm = parse_frontmatter(read_text(skill_path))
    check("SKILL.md 存在", bool(read_text(skill_path)) or os.path.exists(skill_path),
          skill_path)
    check("frontmatter: name", bool(fm.get("name")), str(fm.get("name")))
    check("frontmatter: description", bool(fm.get("description")))
    check("frontmatter: version", bool(fm.get("version")), str(fm.get("version")))

    summary = {"ok": ok, "status": result.get("status") if result else None,
               "case_id": result.get("case_id") if result else None,
               "checks": checks}
    _stdout(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 0 if ok else 1


def _deliverable_ok(deliverable):
    if deliverable in (None, "", [], {}):
        return True  # 早落盘阶段允许为 null；成稿阶段应由 Agent 填写
    if isinstance(deliverable, str):
        return os.path.exists(deliverable)
    if isinstance(deliverable, list):
        return all(isinstance(x, str) and os.path.exists(x) for x in deliverable)
    return True


if __name__ == "__main__":
    sys.exit(main())
