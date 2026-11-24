#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：提交/定稿前运行，逐条勾选并返回退出码。

检查项（与 SKILL.md 自检清单一一对应，并覆盖确定性要求）：
  [A] result.json 可解析且顶层字段齐全
  [B] case_id 非空、status/analysis_mode 合法
  [C] 五段 interpretation 结构完整，每条结论带 locator+quote 证据
  [D] 可能原因含支持/排除证据、confound_risk、testability
  [E] 复核步骤含非空 target；后续实验含 reason/expected_outcome
  [F] 证据 locator 指向的文件在输入目录中存在（防虚构来源）
  [G] 确定性：同一输入画像两次逐字节一致（profile_fingerprint 稳定）
  [H] 结果内容指纹与 reproducibility.content_fingerprint 自洽
  [I] SKILL.md frontmatter 含 name/description/version
  [J] 无编造 benchmark_score / 评分字段

用法：
  python scripts/selftest.py --input /input --result /app/output/result.json --skill-root .
"""

import argparse
import hashlib
import json
import os
import re
import sys

sys.dont_write_bytecode = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import main as M
import inspect_data as ID


def _pass(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print("[%s] %s %s" % (mark, name, ("— " + detail) if detail else ""))
    return ok


def check_evidence_locators(obj, input_dir, report):
    """locator 中若含文件路径，验证该文件在输入目录存在（防虚构来源）。"""
    ok = True
    locators = []

    def walk(o):
        if isinstance(o, dict):
            if "locator" in o:
                locators.append(str(o["locator"]))
            if isinstance(o.get("evidence"), list):
                for e in o["evidence"]:
                    if isinstance(e, dict) and e.get("locator"):
                        locators.append(str(e["locator"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    seen = {}
    for loc in locators:
        seen[loc] = seen.get(loc, 0) + 1
    for loc, cnt in sorted(seen.items()):
        path_part = re.split(r"#", loc, 1)[0].strip()
        if not path_part or " " in path_part or not re.search(r"[/.\\]", path_part):
            continue  # 无文件路径形态的定位，跳过存在性检查
        full = os.path.join(input_dir, path_part)
        if not os.path.exists(full):
            ok = False
            report.append("证据 locator 指向不存在的文件: %s" % loc)
    return ok


def run_determinism_check(input_dir, report):
    """同一输入画像两次，比较 JSON 规范串是否逐字节一致。"""
    if not os.path.isdir(input_dir):
        report.append("输入目录不存在，跳过确定性画像复跑: %s" % input_dir)
        return None
    a = ID.profile_input(input_dir)
    b = ID.profile_input(input_dir)
    sa = json.dumps(a, ensure_ascii=False, sort_keys=True, default=str)
    sb = json.dumps(b, ensure_ascii=False, sort_keys=True, default=str)
    if sa != sb:
        report.append("确定性画像两次运行不一致")
        return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Skill 自检")
    ap.add_argument("--input", default=M.DEFAULT_INPUT)
    ap.add_argument("--result", default=os.path.join(M.DEFAULT_OUTPUT, "result.json"))
    ap.add_argument("--skill-root", default=os.path.dirname(SCRIPT_DIR))
    args = ap.parse_args(argv)

    report = []
    checks = {}

    # A/B/C/D/E：契约与证据
    obj = M.load_json_safe(args.result)
    if obj is None:
        checks["A"] = _pass("A result.json 可解析", False, args.result)
        print("selftest: result.json 不可解析，其余结构项视为失败")
        for name in ("B", "C", "D", "E", "F", "H"):
            checks[name] = _pass(name, False, "依赖 A")
        checks["G"] = run_determinism_check(args.input, report)
        checks["I"] = _pass("I SKILL.md frontmatter", False, "未检查（无 result）")
        checks["J"] = _pass("J 无编造评分字段", False, "未检查")
    else:
        missing = [k for k in M.TOP_KEYS if k not in obj]
        checks["A"] = _pass("A result.json 可解析且顶层字段齐全",
                            isinstance(obj, dict) and not missing,
                            "缺 %s" % missing if missing else "字段齐")
        checks["B"] = _pass("B case_id/status/analysis_mode 合法",
                            str(obj.get("case_id", "")).strip()
                            and obj.get("status") in M.STATUSES
                            and obj.get("analysis_mode") in M.ANALYSIS_MODES,
                            "case_id=%r status=%r mode=%r"
                            % (obj.get("case_id"), obj.get("status"), obj.get("analysis_mode")))
        interp = obj.get("interpretation") if isinstance(obj, dict) else None
        c_ok = isinstance(interp, dict) and all(k in interp for k in M.INTERPRETATION_KEYS)
        ev_ok = True
        if isinstance(interp, dict):
            for k in ("trends", "anomalies"):
                for i, item in enumerate(interp.get(k, []) or []):
                    if isinstance(item, dict):
                        ev = item.get("evidence")
                        if not (isinstance(ev, list) and ev
                                and all(isinstance(e, dict)
                                        and str(e.get("locator", "")).strip()
                                        and str(e.get("quote", "")).strip()
                                        for e in ev)):
                            ev_ok = False
                    else:
                        ev_ok = False
        checks["C"] = _pass("C 五段结构完整且趋势/异常结论带 locator+quote", c_ok and ev_ok)
        d_ok = True
        if isinstance(interp, dict):
            for item in interp.get("possible_causes", []) or []:
                if not (isinstance(item, dict)
                        and isinstance(item.get("supporting_evidence"), list) and item.get("supporting_evidence")
                        and isinstance(item.get("excluding_evidence"), list)
                        and "confound_risk" in item and "testability" in item):
                    d_ok = False
        checks["D"] = _pass("D 可能原因含支持/排除证据与可检验性", d_ok)
        e_ok = True
        if isinstance(interp, dict):
            for item in interp.get("verification_steps", []) or []:
                if not (isinstance(item, dict) and str(item.get("target", "")).strip()):
                    e_ok = False
            for item in interp.get("followup_experiments", []) or []:
                if not (isinstance(item, dict)
                        and str(item.get("reason", "")).strip()
                        and str(item.get("expected_outcome", "")).strip()):
                    e_ok = False
        checks["E"] = _pass("E 复核步骤 target / 后续实验 reason+outcome", e_ok)
        checks["F"] = _pass("F 证据 locator 指向文件存在", check_evidence_locators(obj, args.input, report))
        # G 确定性画像
        det = run_determinism_check(args.input, report)
        checks["G"] = _pass("G 同一输入画像两次一致", bool(det), "输入目录不存在则跳过" if det is None else "")
        # H 内容指纹自洽
        if isinstance(obj, dict) and obj.get("reproducibility", {}).get("content_fingerprint"):
            declared = obj["reproducibility"]["content_fingerprint"]
            computed = M.content_fingerprint(obj)
            checks["H"] = _pass("H 内容指纹自洽", declared == computed,
                                "declared=%s… computed=%s…" % (declared[:12], computed[:12]))
        else:
            checks["H"] = _pass("H 内容指纹自洽", False, "尚未 finalize（content_fingerprint 为空）")

    # I SKILL.md frontmatter
    skill_md = os.path.join(args.skill_root, "SKILL.md")
    fm_ok = False
    if os.path.exists(skill_md):
        text, _ = M.read_text(skill_md)
        if text:
            m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n", text, re.S)
            if m:
                fm = m.group(1)
                fm_ok = all(re.search(r"^%s:\s*\S" % k, fm, re.M) for k in ("name", "description", "version"))
    checks["I"] = _pass("I SKILL.md frontmatter 含 name/description/version", fm_ok, skill_md)

    # J 无编造评分字段
    raw = M.read_text(args.result)[0] if os.path.exists(args.result) else ""
    j_ok = not re.search(r'"(benchmark_score|final_score|grader_score|reviewer_score)"\s*:', raw or "")
    checks["J"] = _pass("J 无编造 benchmark_score/评分字段", j_ok)

    print("-" * 60)
    for line in report:
        print("[NOTE] " + line)
    n_pass = sum(1 for v in checks.values() if v)
    print("selftest: %d/%d 项通过" % (n_pass, len(checks)))
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
