#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：在临时目录里跑 空输入/坏 JSON/坏编码/超大文件/缺参数 五种降级场景，
并验证正常数据路径下 skeleton -> inspect -> validate -> report -> finalize 全链路。

仅使用 Python 标准库；只验证脚本自身行为，不预置任何正式案例答案。

用法：
  python scripts/selftest.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")
INSPECT = os.path.join(HERE, "inspect_data.py")


def run(script, *args):
    return subprocess.run([sys.executable, script] + list(args),
                          capture_output=True, text=True)


def check(cond, msg):
    cond = bool(cond)
    status = "PASS" if cond else "FAIL"
    print("[%s] %s" % (status, msg))
    return cond


def main():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        # 1) 空输入 -> insufficient_evidence，退出码 0
        empty_in = os.path.join(tmp, "empty_in")
        empty_out = os.path.join(tmp, "empty_out")
        os.makedirs(empty_in)
        r = run(MAIN, "skeleton", "--input", empty_in, "--output", empty_out)
        res = _load(os.path.join(empty_out, "result.json"))
        ok &= check(r.returncode == 0, "empty input exits 0")
        ok &= check(res and res.get("status") == "insufficient_evidence",
                    "empty input -> insufficient_evidence")

        # 2) 坏 JSON -> 退出码 0，errors 非空
        bad_in = os.path.join(tmp, "bad_in")
        bad_out = os.path.join(tmp, "bad_out")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json ")
        r = run(MAIN, "skeleton", "--input", bad_in, "--output", bad_out)
        res = _load(os.path.join(bad_out, "result.json"))
        ok &= check(r.returncode == 0, "bad json exits 0")
        ok &= check(res and len(res.get("errors") or []) > 0, "bad json recorded in errors")

        # 3) 坏编码 -> 不崩溃，degradations 记录
        enc_in = os.path.join(tmp, "enc_in")
        enc_out = os.path.join(tmp, "enc_out")
        os.makedirs(enc_in)
        with open(os.path.join(enc_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "challenge-self"}')
        with open(os.path.join(enc_in, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# t\n")
        with open(os.path.join(enc_in, "data.txt"), "wb") as fh:
            fh.write("x,y\n1,2\n".encode("latin-1") + b"\xe9\n")
        r = run(INSPECT, "--input", enc_in, "--output", enc_out)
        prof = _load(os.path.join(enc_out, "data_profile.json"))
        ok &= check(r.returncode == 0, "bad encoding inspect exits 0")
        ok &= check(prof is not None, "bad encoding profile produced")
        ok &= check(any(d.get("issue") == "encoding" for d in (prof or {}).get("degradations", [])),
                    "bad encoding recorded in degradations")

        # 4) 超大文件 -> 跳过并记录
        big_in = os.path.join(tmp, "big_in")
        big_out = os.path.join(tmp, "big_out")
        os.makedirs(big_in)
        with open(os.path.join(big_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "challenge-big"}')
        with open(os.path.join(big_in, "big.csv"), "w", encoding="utf-8") as fh:
            fh.write("x,y\n")
            fh.write("0" * (2_000_001))
        r = run(INSPECT, "--input", big_in, "--output", big_out)
        prof = _load(os.path.join(big_out, "data_profile.json"))
        ok &= check(r.returncode == 0, "oversize inspect exits 0")
        ok &= check(any(d.get("issue") == "oversize" for d in (prof or {}).get("degradations", [])),
                    "oversize file recorded in degradations")

        # 5) 缺参数默认值
        ok &= check(core_defaults(), "missing params default to /input /app/output /app/work")

        # 6) 正常数据全链路
        data_in = os.path.join(tmp, "data_in")
        data_out = os.path.join(tmp, "data_out")
        os.makedirs(data_in)
        with open(os.path.join(data_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "challenge-8354c69a"}')
        with open(os.path.join(data_in, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# 实验结果解释与异常分析\n\n- challenge_id: `challenge-8354c69a`\n")
        csv_path = os.path.join(data_in, "data.csv")
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write("time,force_1,force_2\n0,1.0,1.1\n1,1.5,1.4\n2,2.0,2.2\n3,2.5,2.4\n4,3.0,9.9\n")
        r = run(MAIN, "skeleton", "--input", data_in, "--output", data_out)
        ok &= check(r.returncode == 0, "skeleton exits 0")
        r = run(INSPECT, "--input", data_in, "--output", data_out)
        ok &= check(r.returncode == 0, "inspect exits 0")
        res_path = os.path.join(data_out, "result.json")
        prof = _load(os.path.join(data_out, "data_profile.json"))
        ok &= check(prof and any(f.get("status") == "parsed" for f in prof.get("files", [])),
                    "csv parsed into columns")
        ok &= check(any("9.9" in json.dumps(c.get("outliers") or {})
                        for f in prof.get("files", []) for c in f.get("table", {}).get("columns", [])),
                    "outlier detected by profile")
        for args in (("validate", "--input", res_path),
                     ("report", "--input", res_path, "--output", data_out),
                     ("finalize", "--input", res_path)):
            r = run(MAIN, *args)
            ok &= check(r.returncode == 0, "%s exits 0" % args[0])
        ok &= check(os.path.exists(os.path.join(data_out, "report.md")), "report.md written")
        ok &= check(os.path.exists(os.path.join(data_out, "evidence_map.json")), "evidence_map.json written")
        final = _load(res_path)
        ok &= check(final and final.get("runtime", {}).get("finished_at") is not None, "runtime finalized")

        # 7) 重复运行确定性
        r1 = run(MAIN, "skeleton", "--input", data_in, "--output", os.path.join(tmp, "o1"))
        r2 = run(MAIN, "skeleton", "--input", data_in, "--output", os.path.join(tmp, "o2"))
        a = open(os.path.join(tmp, "o1", "result.json"), encoding="utf-8").read()
        b = open(os.path.join(tmp, "o2", "result.json"), encoding="utf-8").read()
        ok &= check(a == b, "repeated skeleton output deterministic")

    print("")
    print("selftest: %s" % ("ALL PASS" if ok else "HAS FAILURES"))
    return 0 if ok else 1


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def core_defaults():
    sys.path.insert(0, HERE)
    try:
        import main as core
    except Exception:
        return False
    return (core.DEFAULT_INPUT == "/input" and core.DEFAULT_OUTPUT == "/app/output"
            and core.DEFAULT_WORK == "/app/work" and core.MAX_FILE_BYTES == 2_000_000)


if __name__ == "__main__":
    sys.exit(main())
