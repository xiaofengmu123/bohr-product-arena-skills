#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""包级自检：验证鲁棒性要求——空输入、坏 JSON、坏编码、缺参数、超大文件、交付校验。

用法（相对 Skill 根目录）：
    python scripts/selftest.py
全部通过退出码 0，否则退出码 1；结果以 JSON 输出到 stdout。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
sys.path.insert(0, HERE)
import main as core  # noqa: E402  （复用 decode/read 做函数级坏编码测试）


def _run(*args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, MAIN] + list(args),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=e,
    )


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _tmp():
    base = tempfile.mkdtemp(prefix="sfr_selftest_")
    inp = os.path.join(base, "in")
    out = os.path.join(base, "out")
    work = os.path.join(base, "work")
    os.makedirs(inp)
    return base, inp, out, work


def t_help():
    r = _run("--help")
    assert r.returncode == 0, r.stderr
    return True


def t_empty_input():
    _, inp, out, work = _tmp()
    r = _run("bootstrap", "--input", inp, "--out", out, "--work", work)
    assert r.returncode == 0, r.stderr
    res = _read(os.path.join(out, "result.json"))
    assert res.get("status") == "insufficient_evidence", res.get("status")
    assert isinstance(res.get("output_contract"), dict)
    return True


def t_bad_json():
    _, inp, out, work = _tmp()
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write("{ this is not valid json")
    r = _run("bootstrap", "--input", inp, "--out", out, "--work", work)
    assert r.returncode == 0, r.stderr
    res = _read(os.path.join(out, "result.json"))
    assert any(e.get("kind") == "bad_json" for e in res.get("errors", [])), res.get("errors")
    assert res.get("case_id") is None
    return True


def t_bad_encoding():
    payload = "基因\t蛋白\t数值\nTP53\tp53\t12.5\n".encode("gb18030")
    errs = []
    text = core.decode_safe(payload)
    assert text[0] is not None
    return True


def t_missing_params_env():
    _, inp, out, work = _tmp()
    env = {"INPUT_DIR": inp, "OUTPUT_DIR": out, "WORK_DIR": work}
    r = _run("bootstrap", env=env)
    assert r.returncode == 0, r.stderr
    res = _read(os.path.join(out, "result.json"))
    assert res.get("status") == "insufficient_evidence"
    return True


def t_oversized():
    _, inp, out, work = _tmp()
    big = os.path.join(inp, "case.json")
    with open(big, "w", encoding="utf-8") as f:
        f.write('{"case_id": "big-case", "pad": "')
        f.write("a" * (2 * 1024 * 1024 + 10))
        f.write('"}')
    r = _run("bootstrap", "--input", inp, "--out", out, "--work", work,
             "--max-bytes", str(2 * 1024 * 1024))
    assert r.returncode == 0, r.stderr
    res = _read(os.path.join(out, "result.json"))
    assert any(e.get("kind") == "oversized" for e in res.get("errors", [])), res.get("errors")
    return True


def t_check_missing_and_ok():
    _, inp, out, work = _tmp()
    # 契约字段指向不存在的文件 -> ok=False
    res = {
        "case_id": "case-x", "status": "partial",
        "output_contract": {"editable_source": "missing.svg"},
        "sources": [], "uncertainty": [], "change_log": [{"step": "s0", "time_utc": "t", "status": "done", "note": "n"}],
    }
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    r = _run("check", "--out", out, "--work", work)
    assert r.returncode == 0, r.stderr
    report = _read(os.path.join(work, "check_report.json"))
    assert report.get("ok") is False, report.get("failures")

    # 补齐合法 SVG -> ok=True
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="0">TP53</text></svg>'
    with open(os.path.join(out, "missing.svg"), "w", encoding="utf-8") as f:
        f.write(svg)
    r2 = _run("check", "--out", out, "--work", work)
    assert r2.returncode == 0, r2.stderr
    report2 = _read(os.path.join(work, "check_report.json"))
    assert report2.get("ok") is True, report2.get("failures")
    return True


TESTS = [
    ("--help exit 0", t_help),
    ("empty input -> insufficient_evidence", t_empty_input),
    ("bad JSON -> errors recorded, no crash", t_bad_json),
    ("bad encoding gb18030 -> decode fallback", t_bad_encoding),
    ("missing params + env override", t_missing_params_env),
    ("oversized file -> skipped", t_oversized),
    ("deliverable check: missing vs valid SVG", t_check_missing_and_ok),
]


def main():
    results = []
    for name, fn in TESTS:
        try:
            fn()
            results.append({"test": name, "pass": True})
        except Exception as e:  # noqa: BLE001
            results.append({"test": name, "pass": False, "error": "%s: %s" % (type(e).__name__, e)})
    ok = all(r["pass"] for r in results)
    print(json.dumps({"ok": ok, "passed": sum(1 for r in results if r["pass"]),
                      "total": len(results), "results": results},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
