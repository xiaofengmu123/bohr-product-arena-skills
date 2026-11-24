# -*- coding: utf-8 -*-
"""selftest.py — 提交/交付前的自检与确定性验证脚本。

检查项（每项 PASS/FAIL）：
  A. SKILL.md frontmatter：name / description / version 三键齐全；
  B. result.json 契约：case_id 非空、status 合法、output_contract 全键回填到顶层、
     无 NaN/Infinity、证据行 source_id+locator+quote 非空、status=ok 时四项交付齐全；
  C. 端到端确定性：复制 fragments 到临时目录，重跑 骨架→证据→合并 流水线，
     与既有 result.json 逐字节比对；
  D. finalize 幂等：同一目录连续 finalize 两次，result.json 逐字节一致。

退出码：0=全部通过；1=存在失败项。用法见 --help。
"""

import json
import os
import shutil
import sys
import tempfile
import importlib.util


def load_core():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("core_impl", os.path.join(here, "core_impl.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_args(argv):
    args = {"skill": ".", "input": None, "output": None, "work": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--skill", "--input", "--output", "--work") and i + 1 < len(argv):
            args[a[2:]] = argv[i + 1]
            i += 2
        elif a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            i += 1
    return args


class Report:
    def __init__(self):
        self.items = []

    def add(self, name, ok, detail):
        self.items.append((name, bool(ok), detail))

    def summary(self):
        failed = [x for x in self.items if not x[1]]
        lines = []
        for name, ok, detail in self.items:
            lines.append("[%s] %s: %s" % ("PASS" if ok else "FAIL", name, detail))
        lines.append("")
        lines.append("总结：%d 通过，%d 失败" % (len(self.items) - len(failed), len(failed)))
        return "\n".join(lines), failed


def check_frontmatter(skill_dir, rep):
    path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(path):
        rep.add("A.frontmatter", False, "SKILL.md 不存在: %s" % path)
        return None
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as exc:
        rep.add("A.frontmatter", False, "读取失败: %s" % exc)
        return None
    if not text.startswith("---"):
        rep.add("A.frontmatter", False, "缺少 YAML frontmatter 起始 ---")
        return None
    end = text.find("\n---", 3)
    if end < 0:
        rep.add("A.frontmatter", False, "frontmatter 未闭合")
        return None
    fm = text[3:end]
    keys = {}
    for line in fm.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            keys[k.strip()] = v.strip()
    missing = [k for k in ("name", "description", "version") if k not in keys or not keys[k]]
    if missing:
        rep.add("A.frontmatter", False, "缺少字段: %s" % ", ".join(missing))
    else:
        rep.add("A.frontmatter", True, "name=%s version=%s" % (keys["name"], keys["version"]))
    return keys


def check_result(core, input_dir, output_dir, rep):
    result_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(result_path):
        rep.add("B.contract", False, "result.json 不存在")
        return
    text = open(result_path, encoding="utf-8").read()

    def _reject(const):
        raise ValueError("非有限数值: %s" % const)

    try:
        result = json.loads(text, parse_constant=_reject)
    except (ValueError, TypeError) as exc:
        rep.add("B.contract", False, "result.json 非法或含 NaN/Infinity: %s" % exc)
        return

    case_data, case_error = core.safe_json_load(os.path.join(input_dir, "case.json"))
    contract, _ = core.resolve_contract(case_data)

    problems = []
    if not isinstance(result.get("case_id"), str) or not result["case_id"].strip():
        problems.append("case_id 缺失")
    if result.get("status") not in core.ALLOWED_STATUS:
        problems.append("status 非法")
    if not isinstance(result.get("output_contract"), dict):
        problems.append("output_contract 非对象")
    for key in contract:
        if key not in result:
            problems.append("契约键未回填: %s" % key)
    ev = result.get("evidence_matrix") or []
    for i, row in enumerate(ev):
        if not isinstance(row, dict) or not row.get("source_id") or not row.get("locator") or not row.get("quote"):
            problems.append("evidence_matrix[%d] 证据字段不全" % i)
    if result.get("status") == core.STATUS_OK:
        for d in ("research_question", "prior_work", "novelty", "feasibility"):
            if result.get(d) is None:
                problems.append("status=ok 但四项交付缺失: %s" % d)
    if problems:
        rep.add("B.contract", False, "; ".join(problems))
    else:
        rep.add("B.contract", True, "契约字段与证据字段完整，status=%s" % result.get("status"))
    return result


def check_determinism(core, input_dir, work_dir, output_dir, rep):
    if not os.path.isdir(work_dir) or not os.path.isdir(output_dir):
        rep.add("C.determinism", False, "缺少 work 或 output 目录")
        return
    old_path = os.path.join(output_dir, "result.json")
    if not os.path.isfile(old_path):
        rep.add("C.determinism", False, "output/result.json 不存在，无法比对")
        return
    temp = tempfile.mkdtemp(prefix="selftest_")
    try:
        twork = os.path.join(temp, "work")
        tout = os.path.join(temp, "out")
        os.makedirs(twork, exist_ok=True)
        os.makedirs(tout, exist_ok=True)
        src_frag = os.path.join(work_dir, "fragments")
        if os.path.isdir(src_frag):
            shutil.copytree(src_frag, os.path.join(twork, "fragments"))

        entries = core.discover_files(input_dir)
        case_data, case_error = core.safe_json_load(os.path.join(input_dir, "case.json"))
        contract, contract_error = core.resolve_contract(case_data)
        core.build_skeleton(input_dir, twork, tout, case_data, case_error, entries, contract, contract_error)
        core.run_evidence(input_dir, twork, tout)
        rc = core.run_finalize(input_dir, twork, tout)

        new_text = open(os.path.join(tout, "result.json"), encoding="utf-8").read()
        old_text = open(old_path, encoding="utf-8").read()
        if new_text == old_text:
            rep.add("C.determinism", True, "重跑骨架→证据→合并与既有 result.json 字节一致")
        else:
            rep.add("C.determinism", False, "重跑产物与既有 result.json 不一致（检查 fragments 是否与最终 result 同步）")

        # D. finalize 幂等
        core.run_finalize(input_dir, twork, tout)
        again = open(os.path.join(tout, "result.json"), encoding="utf-8").read()
        if new_text == again:
            rep.add("D.idempotent", True, "连续 finalize 两次字节一致")
        else:
            rep.add("D.idempotent", False, "finalize 非幂等")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def run_selftest(skill_dir=".", input_dir=None, output_dir=None, work_dir=None):
    core = load_core()
    rep = Report()

    check_frontmatter(skill_dir, rep)

    if input_dir and output_dir:
        check_result(core, input_dir, output_dir, rep)
        if work_dir:
            check_determinism(core, input_dir, work_dir, output_dir, rep)
    else:
        rep.add("B/C/D", False, "未提供 --input/--output/--work，跳过契约与确定性检查")

    summary, failed = rep.summary()
    print(summary)
    return 1 if failed else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    return run_selftest(args["skill"], args["input"], args["output"], args["work"])


if __name__ == "__main__":
    sys.exit(main())
