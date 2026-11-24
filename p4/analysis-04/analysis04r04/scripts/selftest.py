#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-exec 健壮性自检（仅标准库）。

覆盖运行稳定性评分点：空输入、坏 JSON、坏编码、缺参数、缺契约 五种场景。
断言每场景 init 退出码为 0 且落盘合法 result.json。
"""
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import core_impl  # noqa: E402


def run_init(input_dir, output_dir, work_dir):
    """返回 (exit_code, result_dict)。"""
    try:
        core_impl.cmd_init(input_dir, output_dir, work_dir)
        code = 0
    except Exception:  # noqa: BLE001
        code = 1
    rp = os.path.join(output_dir, "result.json")
    result = None
    if os.path.isfile(rp):
        try:
            with open(rp, "r", encoding="utf-8") as fh:
                result = json.load(fh)
        except Exception:  # noqa: BLE001
            result = None
    return code, result


def is_valid(result):
    return isinstance(result, dict) and "status" in result and "change_log" in result


def main():
    base = tempfile.mkdtemp(prefix="selftest_")
    scenarios = []

    # 1 空输入目录
    d = os.path.join(base, "empty")
    os.makedirs(d)
    scenarios.append(("empty_input", d))

    # 2 坏 JSON
    d = os.path.join(base, "badjson")
    os.makedirs(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        fh.write("{not-valid json")
    scenarios.append(("bad_json", d))

    # 3 坏编码（utf-8 不可解码的字节，走 latin-1 兜底）
    d = os.path.join(base, "badenc")
    os.makedirs(d)
    with open(os.path.join(d, "case.json"), "wb") as fh:
        fh.write(b'{"case_id": "c-enc", "output_contract": {"summary": null, "rows": [], "plot": null}, "note": "\xff\xfe\xfa"}')
    scenarios.append(("bad_encoding", d))

    # 4 缺契约（合法 case.json 但无 output_contract）
    d = os.path.join(base, "nocontract")
    os.makedirs(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "c-nocontract"}, fh)
    scenarios.append(("missing_contract", d))

    # 5 正常最小案例（含契约）
    d = os.path.join(base, "valid")
    os.makedirs(d)
    with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
        json.dump({"case_id": "c-valid", "output_contract": {"summary": None, "rows": [], "plot": None}}, fh)
    scenarios.append(("valid", d))

    ok = 0
    failures = []
    for name, inp in scenarios:
        out = os.path.join(base, name + "_out")
        work = os.path.join(base, name + "_work")
        code, result = run_init(inp, out, work)
        result_exists = os.path.isfile(os.path.join(out, "result.json"))
        valid = (code == 0) and result_exists and is_valid(result)
        status = result.get("status") if isinstance(result, dict) else "?"
        print("[%s] exit=%d result=%s valid=%s status=%s" % (name, code, result_exists, valid, status))
        if valid:
            ok += 1
        else:
            failures.append(name)

    print("selftest: %d/%d passed; failures=%s" % (ok, len(scenarios), failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
