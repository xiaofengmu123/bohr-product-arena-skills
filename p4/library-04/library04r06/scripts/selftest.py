#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — research-kb-qa Skill 的自检（仅标准库，可在任意空机复跑）。

覆盖四类评分相关的失败模式：
  1. 空输入目录 -> status=insufficient_evidence，退出码 0；
  2. 坏 JSON -> status=input_error，不崩溃；
  3. output_contract 原样回填（不变名、不删键、缺值回填）；
  4. 确定性：同一输入跑两次，实质内容（去掉时间戳）逐字节一致。

运行：python3 scripts/selftest.py   或   python3 scripts/main.py selftest
退出码 0 表示全部通过；非 0 表示存在失败。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import core_impl as C  # noqa: E402


def _args(i, o, w):
    return {"input": i, "output": o, "work": w, "answers": None}


def _strip_volatile(d):
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            if k == "runtime":
                continue
            if k == "change_log":
                v = [{kk: vv for kk, vv in e.items() if kk != "time"} for e in v]
                if k in ("generated_at", "mtime"):
                    continue
            out[k] = _strip_volatile(v)
        return out
    if isinstance(d, list):
        return [_strip_volatile(x) for x in d]
    return d


def _write(fp, text):
    os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
    with open(fp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _check(name, cond, detail=""):
    print("  %-24s %s %s" % ("[" + name + "]", "PASS" if cond else "FAIL", detail))
    return bool(cond)


def test_empty(base):
    inp = os.path.join(base, "empty", "input")
    out = os.path.join(base, "empty", "output")
    work = os.path.join(base, "empty", "work")
    os.makedirs(inp)
    _write(os.path.join(inp, "case.json"),
           '{"case_id": "t-empty", "output_contract": {"questions": []}}')
    C.scaffold(_args(inp, out, work))
    C.inventory(_args(inp, out, work))
    C.finalize(_args(inp, out, work))
    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    return _check("empty_input", r.get("status") == "insufficient_evidence",
                  r.get("status"))


def test_bad_json(base):
    inp = os.path.join(base, "bad", "input")
    out = os.path.join(base, "bad", "output")
    work = os.path.join(base, "bad", "work")
    os.makedirs(inp)
    _write(os.path.join(inp, "case.json"), "{not valid json")
    C.scaffold(_args(inp, out, work))
    C.inventory(_args(inp, out, work))
    C.finalize(_args(inp, out, work))
    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    return _check("bad_json", r.get("status") == "input_error" and r.get("case_id") is None,
                  r.get("status"))


def test_contract_echo(base):
    inp = os.path.join(base, "contract", "input")
    out = os.path.join(base, "contract", "output")
    work = os.path.join(base, "contract", "work")
    os.makedirs(os.path.join(inp, "materials"))
    contract = {"questions": ["q1", "q2"], "required_field": "x", "extra": {"nested": 1}}
    _write(os.path.join(inp, "case.json"),
           json.dumps({"case_id": "t-contract", "output_contract": contract},
                      ensure_ascii=False))
    _write(os.path.join(inp, "materials", "note.md"), "# Note\nsome text\n")
    C.scaffold(_args(inp, out, work))
    C.inventory(_args(inp, out, work))
    C.finalize(_args(inp, out, work))
    r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
    ok_echo = r.get("output_contract") == contract
    ok_flat = all(k in r for k in contract)
    return _check("contract_echo", ok_echo and ok_flat, "echo+flatten")


def test_determinism(base):
    inp = os.path.join(base, "det", "input")
    os.makedirs(os.path.join(inp, "materials"))
    _write(os.path.join(inp, "case.json"),
           '{"case_id": "t-det", "output_contract": {"questions": ["What does A say?"]}}')
    v1 = "---\ntitle: A\ntags: [t1]\n---\nhello\nworld\n"
    _write(os.path.join(inp, "materials", "A-v1.md"), v1)
    _write(os.path.join(inp, "materials", "A-v2.md"),
           "---\ntitle: A\ntags: [t1, t2]\n---\nhello\nworld v2\n")
    _write(os.path.join(inp, "materials", "copy-of-v1.txt"), v1)  # byte-identical duplicate
    outs = []
    for tag in ("a", "b"):
        out = os.path.join(base, "det", "out-" + tag)
        work = os.path.join(base, "det", "work-" + tag)
        C.scaffold(_args(inp, out, work))
        C.inventory(_args(inp, out, work))
        C.finalize(_args(inp, out, work))
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        outs.append(_strip_volatile(r))
    same = outs[0] == outs[1]
    r0 = outs[0]
    six = all(k in (r0.get("library") or {}) for k in
              ("file_types", "metadata_fields", "directory_tree",
               "duplicate_files", "version_families", "permissions_and_source"))
    dup = len((r0.get("library") or {}).get("duplicate_files", {}).get("groups", []))
    ver = len((r0.get("library") or {}).get("version_families", []))
    return _check("determinism", same and six and dup == 1 and ver == 1,
                  "six=%s dup=%d ver=%d" % (six, dup, ver))


def run():
    base = tempfile.mkdtemp(prefix="kbskill_selftest_")
    print("selftest base: %s" % base)
    ok = True
    ok = test_empty(base) and ok
    ok = test_bad_json(base) and ok
    ok = test_contract_echo(base) and ok
    ok = test_determinism(base) and ok
    shutil.rmtree(base, ignore_errors=True)
    print("selftest: %s" % ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())