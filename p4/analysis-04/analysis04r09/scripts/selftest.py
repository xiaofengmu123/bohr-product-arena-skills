#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：在临时目录构造 5 类输入，验证 main.py 各子命令退出码 0 且产物合法。

覆盖评分维度里的运行稳定性：空输入、坏 JSON、坏编码、缺参数、正常数据五类场景。
用法：python3 -B scripts/selftest.py   （从包根运行）
退出码 0 表示全部通过；非 0 表示存在失败项，需修 main.py。
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"
STATUSES = {"ok", "partial", "insufficient_evidence", "input_error"}


def run_main(args):
    return subprocess.run([sys.executable, "-B", str(MAIN), *args],
                          capture_output=True, text=True)


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print("[{}] {}".format(tag, name) + ("  <= " + detail if detail and not cond else ""))
    return bool(cond)


def read_json(p):
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    all_ok = True
    tmp = Path(tempfile.mkdtemp(prefix="sci-selftest-"))

    # 1) 空输入目录：退出码 0，结果合法且 insufficient_evidence
    d1 = tmp / "empty_in"
    d1.mkdir()
    o1 = tmp / "empty_out"
    r = run_main(["init", "--input", str(d1), "--output", str(o1), "--work", str(tmp / "w1")])
    res = read_json(o1 / "result.json")
    all_ok &= check("空输入: 退出码 0", r.returncode == 0, str(r.returncode))
    all_ok &= check("空输入: status 合法", res.get("status") in STATUSES, str(res.get("status")))

    # 2) 坏 JSON：退出码 0，errors 记录，仍产出合法 result.json
    d2 = tmp / "badjson_in"
    d2.mkdir()
    (d2 / "case.json").write_bytes(b"{ this is not valid json !!!")
    o2 = tmp / "badjson_out"
    r = run_main(["init", "--input", str(d2), "--output", str(o2), "--work", str(tmp / "w2")])
    res = read_json(o2 / "result.json")
    all_ok &= check("坏 JSON: 退出码 0", r.returncode == 0, str(r.returncode))
    all_ok &= check("坏 JSON: errors 已记录", bool(res.get("errors")), str(res.get("errors")))

    # 3) 坏编码 + 二进制：GBK CSV 可画像，随机二进制被安全跳过，profile 不崩溃
    d3 = tmp / "badec_in"
    d3.mkdir()
    (d3 / "case.json").write_text(
        json.dumps({"case_id": "c-badenc", "output_contract": {"a": 1}}), encoding="utf-8")
    gbk_csv = "姓名,年龄,组别\n张三,30,A\n李四,25,B\n".encode("gbk")
    (d3 / "gbk.csv").write_bytes(gbk_csv)
    (d3 / "blob.bin").write_bytes(bytes(range(256)) * 4)  # 含 NUL，判为二进制
    o3 = tmp / "badec_out"
    r = run_main(["profile", "--input", str(d3), "--output", str(o3), "--work", str(tmp / "w3")])
    prof = read_json(o3 / "data_profile.json")
    all_ok &= check("坏编码: profile 退出码 0", r.returncode == 0, str(r.returncode))
    all_ok &= check("坏编码: 产出 data_profile.json", (o3 / "data_profile.json").exists())

    # 4) 正常 CSV：init + profile + validate 全链路，validate 报告 PASS
    d4 = tmp / "good_in"
    d4.mkdir()
    (d4 / "case.json").write_text(json.dumps({
        "case_id": "c-good",
        "output_contract": {"cleaned_table": None, "stats": None, "figure": None},
    }, ensure_ascii=False), encoding="utf-8")
    (d4 / "iris.csv").write_text(
        "sepal_length,sepal_width,species\n5.1,3.5,setosa\n4.9,3.0,setosa\n"
        "7.0,3.2,versicolor\n6.4,3.2,versicolor\n6.3,3.3,virginica\n", encoding="utf-8")
    o4 = tmp / "good_out"
    w4 = tmp / "w4"
    run_main(["init", "--input", str(d4), "--output", str(o4), "--work", str(w4)])
    run_main(["profile", "--input", str(d4), "--output", str(o4), "--work", str(w4)])
    res = read_json(o4 / "result.json")
    res["cleaned_table"] = str(o4 / "cleaned.csv")
    (o4 / "cleaned.csv").write_text("sepal_length,sepal_width,species\n", encoding="utf-8")
    (o4 / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    r = run_main(["validate", "--input", str(d4), "--output", str(o4), "--work", str(w4)])
    val = read_json(o4 / "validation.json")
    all_ok &= check("正常数据: validate 退出码 0", r.returncode == 0, str(r.returncode))
    all_ok &= check("正常数据: validate 报告 PASS", val.get("passed") is True, str(val.get("checks")))

    # 5) 缺参数：--help 退出码 0；DEFAULT_* 常量与契约一致
    r = run_main(["--help"])
    all_ok &= check("缺参数: --help 退出码 0", r.returncode == 0, str(r.returncode))
    spec = importlib.util.spec_from_file_location("main", MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    defaults_ok = (mod.DEFAULT_INPUT == "/input" and mod.DEFAULT_OUTPUT == "/app/output"
                   and mod.DEFAULT_WORK == "/app/work")
    all_ok &= check("缺参数: 默认路径与契约一致", defaults_ok,
                    "{}/{}/{}".format(mod.DEFAULT_INPUT, mod.DEFAULT_OUTPUT, mod.DEFAULT_WORK))

    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest: {}".format("ALL PASS" if all_ok else "HAS FAILURES"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
