#!/usr/bin/env python3
"""Self-test: run skeleton + evidence + verify on a synthetic minimal input.

Creates a temporary directory, runs the pipeline, and asserts result.json is
valid with case_id and status. Also checks empty-input robustness. Never
writes to /app/output. Only the Python standard library is used.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

STATUSES = {"in_progress", "complete", "partial", "failed"}


def run(script, env):
    return subprocess.run([sys.executable, os.path.join(HERE, script)], env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def main():
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    indir = os.path.join(tmp, "input")
    workdir = os.path.join(tmp, "work")
    outdir = os.path.join(tmp, "output")
    os.makedirs(indir)
    os.makedirs(workdir)
    os.makedirs(outdir)

    with open(os.path.join(indir, "case.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "selftest-case-001", "id": "selftest-case-001"}, f,
                  ensure_ascii=False)
    with open(os.path.join(indir, "task.md"), "w", encoding="utf-8") as f:
        f.write("# 测试任务\n\n对一篇稿件做结构诊断。\n")
    with open(os.path.join(indir, "paper.md"), "w", encoding="utf-8") as f:
        f.write("# 1. 引言\n\n本研究的结论是 X（p=0.03）。\n\n# 2. 方法\n\n我们使用了方法 Y。\n")

    env = dict(os.environ)
    env.update({
        "CASE_JSON": os.path.join(indir, "case.json"),
        "TASK_MD": os.path.join(indir, "task.md"),
        "INPUT_DIR": indir,
        "WORK_DIR": workdir,
        "OUTPUT_DIR": outdir,
        "RESULT_JSON": os.path.join(outdir, "result.json"),
        "EVIDENCE_JSON": os.path.join(workdir, "evidence.json"),
        "VERIFY_REPORT": os.path.join(outdir, "verify_report.json"),
    })

    results = []
    for script in ("skeleton.py", "evidence.py", "verify.py"):
        r = run(script, env)
        results.append({"script": script, "rc": r.returncode, "ok": r.returncode == 0})
        if r.returncode != 0:
            print(json.dumps({"selftest": "FAIL", "script": script,
                              "stderr": r.stderr[:2000]}, ensure_ascii=False, indent=2))
            return 1

    try:
        with open(os.path.join(outdir, "result.json"), "r", encoding="utf-8-sig") as f:
            res = json.load(f)
        assert res.get("case_id") == "selftest-case-001", "case_id mismatch"
        assert res.get("status") in STATUSES, "status missing"
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"selftest": "FAIL", "error": str(e)}, ensure_ascii=False, indent=2))
        return 1

    # empty-input robustness: skeleton should not crash on a missing input dir
    empty_env = dict(env)
    empty_env["INPUT_DIR"] = os.path.join(tmp, "no_such_input")
    r = run("skeleton.py", empty_env)
    results.append({"script": "skeleton.py (empty input)", "rc": r.returncode,
                    "ok": r.returncode == 0})
    if r.returncode != 0:
        print(json.dumps({"selftest": "FAIL", "script": "skeleton.py empty-input",
                          "stderr": r.stderr[:2000]}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"selftest": "PASS", "results": results, "tmp": tmp},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
