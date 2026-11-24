#!/usr/bin/env python3
"""selftest.py — library-organizer 自检：接口、稳定性、幂等、产物合法性。

从 Skill 根目录运行：python3 -B scripts/selftest.py
只依赖标准库；使用临时目录，不触碰 /input 与 /app。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(HERE, "scripts", "main.py")


def run(args):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          capture_output=True, text=True)


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return bool(cond)


def main():
    ok = True
    p = run(["--help"])
    ok &= check("help exit 0", p.returncode == 0, f"rc={p.returncode}")
    ok &= check("help mentions --input", "--input" in p.stdout)

    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "inp"); out = os.path.join(td, "out"); work = os.path.join(td, "work")
        os.makedirs(inp)

        # 空输入 → insufficient_evidence
        p = run(["--input", inp, "--output", out, "--work", work])
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        ok &= check("empty dir status", r.get("status") == "insufficient_evidence",
                    f"status={r.get('status')}")
        ok &= check("empty dir rc 0", p.returncode == 0)

        # 缺输入 → input_error
        p = run(["--input", os.path.join(td, "nope"), "--output", out, "--work", work])
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        ok &= check("missing input status", r.get("status") == "input_error",
                    f"status={r.get('status')}")
        ok &= check("missing input rc 0", p.returncode == 0)

        # 坏 case.json → 不崩溃，degradations 记录
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with open(os.path.join(inp, "a.txt"), "w", encoding="utf-8") as f:
            f.write("hello")
        p = run(["--input", inp, "--output", out, "--work", work])
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        ok &= check("bad json rc 0", p.returncode == 0, f"rc={p.returncode}")
        ok &= check("bad json degraded",
                    any("invalid" in d or "case.json" in d for d in r.get("degradations", [])))

        # 正常输入 → 合法 result.json，幂等
        os.remove(os.path.join(inp, "case.json"))
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "demo-case", "output_contract": {
                "library": {}, "evidence": {}, "operations": [], "failures": {}}}, f)
        with open(os.path.join(inp, "paper.pdf"), "w", encoding="utf-8") as f:
            f.write("%PDF-1.4\n10.1000/test.42\n2019 materials graphene\n")
        with open(os.path.join(inp, "refs.bib"), "w", encoding="utf-8") as f:
            f.write('@article{doe2019, title={Graphene Catalysis}, '
                    'author={Doe, Jane and Roe, John}, year={2019}, doi={10.1000/x.1}}')
        p1 = run(["--input", inp, "--output", out, "--work", work, "--apply"])
        r1 = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        ok &= check("normal rc 0", p1.returncode == 0, f"rc={p1.returncode}")
        ok &= check("normal status valid", r1.get("status") in
                    {"ok", "partial", "insufficient_evidence", "input_error"},
                    f"status={r1.get('status')}")
        ok &= check("case_id echoed", r1.get("case_id") == "demo-case")
        ok &= check("contract keys present",
                    all(k in r1 for k in ("library", "evidence", "operations", "failures")))
        ok &= check("extras present",
                    all(k in r1 for k in ("status", "sources", "uncertainty", "change_log")))
        srcs = {c["source"] for c in r1["library"]["catalog"]}
        ok &= check("catalog covers material files", {"paper.pdf", "refs.bib"} <= srcs,
                    f"n={len(srcs)}")

        # 幂等：连跑两次 catalog 一致
        snap1 = json.dumps(r1["library"]["catalog"], sort_keys=True, ensure_ascii=False)
        p2 = run(["--input", inp, "--output", out, "--work", work, "--apply"])
        r2 = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        snap2 = json.dumps(r2["library"]["catalog"], sort_keys=True, ensure_ascii=False)
        ok &= check("idempotent catalog", snap1 == snap2)

    print("SELFTEST " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())