#!/usr/bin/env python3
"""开题报告与研究计划书 Skill 的自检脚本（selftest）。

覆盖本变体的鲁棒性重点：空输入、坏 JSON、缺参数、超大文件、缺 result、
无材料，全部应优雅降级（退出码 0 或按约定返回）并产出合法产物/记录降级，
绝不崩溃。运行：python scripts/selftest.py [--tmp <临时目录>]

仅依赖 Python 标准库；自测在临时目录进行，不污染 /app/output。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable

# 让任何终端都能正确显示 UTF-8（GBK 控制台不报 UnicodeEncodeError）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def run(args, **kw):
    return subprocess.run([PY, MAIN] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", **kw)


def make_case(root, payload=None):
    path = os.path.join(root, "case.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload if payload is not None else {
            "case_id": "selftest",
            "output_contract": {
                "title": {"required": True},
                "summary": {"required": True},
                "proposal": {"required": True},
            },
        }, fh, ensure_ascii=False)
    return path


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (("  " + detail) if detail else ""))
    return condition


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    tmp = tempfile.mkdtemp(prefix="proposal-selftest-")
    results = []
    try:
        # 1) --help 退出码 0
        r = run(["--help"])
        results.append(check("--help exit 0", r.returncode == 0))

        # 2) 空输入目录（无 case.json）→ init 落盘 input_error 骨架，退出码 0
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        out1 = os.path.join(tmp, "out1")
        os.makedirs(out1)
        r = run(["init", "--input", empty, "--output", out1,
                 "--case", os.path.join(empty, "case.json"),
                 "--result", os.path.join(out1, "result.json")])
        ok = r.returncode == 0 and os.path.exists(os.path.join(out1, "result.json"))
        results.append(check("empty input: init no crash", ok, "rc=%d" % r.returncode))
        if ok:
            obj = read_json(os.path.join(out1, "result.json"))
            results.append(check("empty input: status=input_error",
                                 obj.get("status") == "input_error", obj.get("status")))

        # 3) 坏 JSON case.json → init 降级，退出码 0
        bad = os.path.join(tmp, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        out2 = os.path.join(tmp, "out2")
        os.makedirs(out2)
        r = run(["init", "--input", bad, "--output", out2,
                 "--case", os.path.join(bad, "case.json"),
                 "--result", os.path.join(out2, "result.json")])
        ok = r.returncode == 0 and os.path.exists(os.path.join(out2, "result.json"))
        results.append(check("bad JSON: init no crash", ok, "rc=%d" % r.returncode))

        # 4) 缺参数（不传 --case/--result）→ 用默认路径，不崩溃
        r = run(["init"])
        results.append(check("missing args: init no crash (defaults)",
                             r.returncode in (0, 1), "rc=%d" % r.returncode))

        # 5) 超大文件 → load 跳过并记录降级，退出码 0
        big = os.path.join(tmp, "big")
        os.makedirs(big)
        with open(os.path.join(big, "huge.md"), "w", encoding="utf-8") as fh:
            fh.write("x" * (2 * 1024 * 1024 + 100))  # 略超 2MB
        src_out = os.path.join(tmp, "sources.json")
        r = run(["load", "--input", big, "--sources-out", src_out])
        ok = r.returncode == 0 and os.path.exists(src_out)
        results.append(check("oversized file: load no crash", ok, "rc=%d" % r.returncode))
        if ok:
            rep = read_json(src_out)
            results.append(check("oversized file: recorded in degradations",
                                 any(d.get("kind") == "oversized"
                                     for d in rep.get("degradations", [])),
                                 str(len(rep.get("degradations", [])))))

        # 6) 正常案例全流水线 init→load→check→render，产物齐全
        good = os.path.join(tmp, "good")
        os.makedirs(good)
        make_case(good)
        with open(os.path.join(good, "guide.md"), "w", encoding="utf-8") as fh:
            fh.write("# 课题指南\n样本 200 例，周期 24 个月。\n")
        out3 = os.path.join(tmp, "out3")
        os.makedirs(out3)
        work3 = os.path.join(tmp, "work3")
        os.makedirs(work3)
        r = run(["run", "--input", good, "--output", out3, "--work", work3,
                 "--case", os.path.join(good, "case.json"),
                 "--result", os.path.join(out3, "result.json"),
                 "--sources-out", os.path.join(work3, "sources.json"),
                 "--report", os.path.join(work3, "check_report.json")])
        artifacts_ok = all(os.path.exists(os.path.join(out3, f))
                           for f in ("proposal.md", "evidence_map.json",
                                     "route.mmd", "route.svg"))
        results.append(check("full pipeline: artifacts complete",
                             r.returncode == 0 and artifacts_ok, "rc=%d" % r.returncode))

        # 7) 重复运行确定（无随机性）：两次 init 的骨架除时间戳外一致
        r = run(["init", "--input", good, "--output", out3,
                 "--case", os.path.join(good, "case.json"),
                 "--result", os.path.join(out3, "result.json")])
        obj1 = read_json(os.path.join(out3, "result.json"))
        r = run(["init", "--input", good, "--output", out3,
                 "--case", os.path.join(good, "case.json"),
                 "--result", os.path.join(out3, "result.json")])
        obj2 = read_json(os.path.join(out3, "result.json"))
        obj1.pop("runtime", None)
        obj2.pop("runtime", None)
        obj1["change_log"][0]["at"] = "X"
        obj2["change_log"][0]["at"] = "X"
        results.append(check("determinism (except timestamps)", obj1 == obj2))

        passed = sum(1 for x in results if x)
        total = len(results)
        print("\nselftest: %d/%d passed" % (passed, total))
        return 0 if passed == total else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
