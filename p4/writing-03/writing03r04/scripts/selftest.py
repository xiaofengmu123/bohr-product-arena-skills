#!/usr/bin/env python3
"""Skill 包自检：在临时目录构造用例并验证主流程。

仅依赖 Python 标准库。覆盖：
  - 正常输入（含 output_contract 与可定位摘录）→ 主流程幂等、verify 退出码 0
  - 无 output_contract → 三段式兜底契约、verify 退出码 0
  - 空输入目录 → 产出合法结果且 status=input_error、verify 退出码 2
  - 坏 JSON → 产出合法结果且 status=input_error、verify 退出码 2
退出码: 0 = 全部通过; 1 = 存在失败。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
VERIFY = os.path.join(HERE, "verify_contract.py")


def run(pyfile, args):
    return subprocess.run([sys.executable, pyfile] + args,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_normal(root):
    inp, out, work = _dirs(root)
    material = "Material text: The sample reported an increase of 12.5 percent.\nSecond sentence for context.\n"
    write(os.path.join(inp, "material.txt"), material)
    case = {
        "case_id": "case-selftest-001",
        "task": "format adaptation demo",
        "output_contract": {
            "deliverable": {"content": "", "artifact_path": [], "description": "demo"},
            "fact_source_map": [
                {"item_id": "FMT-001", "item_type": "content_unit",
                 "check": "preserve measured value",
                 "spec_source": {"locator": "material.txt",
                                 "quote": "The sample reported an increase of 12.5 percent."},
                 "applied_value": "kept", "verification": "measured", "status": "pass"}
            ],
            "version_boundary": {"changes": [], "pending_human": [], "material_gaps": [], "exceptions": []},
        },
    }
    write(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# task\n")
    return inp, out, work


def make_fallback(root):
    inp, out, work = _dirs(root)
    write(os.path.join(inp, "material.txt"), "Some content with no specific quote target.\n")
    write(os.path.join(inp, "case.json"),
          json.dumps({"case_id": "case-selftest-002", "task": "no output_contract"}, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# task\n")
    return inp, out, work


def make_empty(root):
    inp, out, work = _dirs(root)
    return inp, out, work


def make_badjson(root):
    inp, out, work = _dirs(root)
    write(os.path.join(inp, "case.json"), "{not valid json")
    write(os.path.join(inp, "task.md"), "# task\n")
    return inp, out, work


def _dirs(root):
    inp = os.path.join(root, "input")
    out = os.path.join(root, "output")
    work = os.path.join(root, "work")
    os.makedirs(inp, exist_ok=True)
    return inp, out, work


def main(argv=None):
    ap = argparse.ArgumentParser(description="Skill 包自检。")
    ap.add_argument("--skill-root", default=HERE)
    ap.parse_args(argv)

    failures = []
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        # 用例 A：output_contract 透传 + 可定位摘录
        a_in, a_out, a_work = make_normal(os.path.join(tmp, "a"))
        r = run(MAIN, ["--case", os.path.join(a_in, "case.json"), "--task", os.path.join(a_in, "task.md"),
                       "--out", a_out, "--work", a_work, "--material-root", a_in, "--material-root", a_work])
        if r.returncode != 0:
            failures.append("caseA main rc=%d" % r.returncode)
        res_a = read_json(os.path.join(a_out, "result.json"))
        for key in ("case_id", "status", "sources", "uncertainty", "change_log",
                    "deliverable", "fact_source_map", "version_boundary"):
            if key not in res_a:
                failures.append("caseA missing key %s" % key)
        if res_a.get("status") != "partial":
            failures.append("caseA status=%s" % res_a.get("status"))
        # 幂等：再跑一次，scaffold 的 change_log 条目仍为 1
        run(MAIN, ["--case", os.path.join(a_in, "case.json"), "--task", os.path.join(a_in, "task.md"),
                   "--out", a_out, "--work", a_work, "--material-root", a_in, "--material-root", a_work])
        res_a2 = read_json(os.path.join(a_out, "result.json"))
        scaffolds = [e for e in res_a2.get("change_log", [])
                     if isinstance(e, dict) and e.get("action") == "scaffold"]
        if len(scaffolds) != 1:
            failures.append("caseA scaffold entries=%d (expected 1)" % len(scaffolds))
        rv = run(VERIFY, ["--result", os.path.join(a_out, "result.json"),
                          "--case", os.path.join(a_in, "case.json"),
                          "--report", os.path.join(a_out, "verification_report.json"),
                          "--material-root", a_in, "--material-root", a_work])
        if rv.returncode != 0:
            failures.append("caseA verify rc=%d (want 0)" % rv.returncode)

        # 用例 B：无 output_contract → 三段式兜底
        b_in, b_out, b_work = make_fallback(os.path.join(tmp, "b"))
        rb = run(MAIN, ["--case", os.path.join(b_in, "case.json"), "--task", os.path.join(b_in, "task.md"),
                        "--out", b_out, "--work", b_work, "--material-root", b_in, "--material-root", b_work])
        if rb.returncode != 0:
            failures.append("caseB main rc=%d" % rb.returncode)
        res_b = read_json(os.path.join(b_out, "result.json"))
        if res_b.get("status") != "partial":
            failures.append("caseB status=%s" % res_b.get("status"))
        rvb = run(VERIFY, ["--result", os.path.join(b_out, "result.json"),
                           "--case", os.path.join(b_in, "case.json"),
                           "--report", os.path.join(b_out, "verification_report.json"),
                           "--material-root", b_in, "--material-root", b_work])
        if rvb.returncode != 0:
            failures.append("caseB verify rc=%d (want 0)" % rvb.returncode)

        # 用例 C：空输入目录
        e_in, e_out, e_work = make_empty(os.path.join(tmp, "e"))
        re_ = run(MAIN, ["--case", os.path.join(e_in, "case.json"), "--task", os.path.join(e_in, "task.md"),
                         "--out", e_out, "--work", e_work, "--material-root", e_in, "--material-root", e_work])
        if re_.returncode != 0:
            failures.append("empty main rc=%d" % re_.returncode)
        res_e = read_json(os.path.join(e_out, "result.json"))
        if res_e.get("status") != "input_error":
            failures.append("empty status=%s (want input_error)" % res_e.get("status"))
        rve = run(VERIFY, ["--result", os.path.join(e_out, "result.json"),
                           "--case", os.path.join(e_in, "case.json"),
                           "--report", os.path.join(e_out, "verification_report.json"),
                           "--material-root", e_in, "--material-root", e_work])
        if rve.returncode != 2:
            failures.append("empty verify rc=%d (want 2)" % rve.returncode)

        # 用例 D：坏 JSON
        j_in, j_out, j_work = make_badjson(os.path.join(tmp, "j"))
        rj = run(MAIN, ["--case", os.path.join(j_in, "case.json"), "--task", os.path.join(j_in, "task.md"),
                        "--out", j_out, "--work", j_work, "--material-root", j_in, "--material-root", j_work])
        if rj.returncode != 0:
            failures.append("badjson main rc=%d" % rj.returncode)
        res_j = read_json(os.path.join(j_out, "result.json"))
        if res_j.get("status") != "input_error":
            failures.append("badjson status=%s (want input_error)" % res_j.get("status"))

        print("SELFTEST %s" % ("PASS" if not failures else "FAIL"))
        for f in failures:
            print("  FAIL: %s" % f)
        return 0 if not failures else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
