#!/usr/bin/env python3
"""selftest：在临时目录对 main.py 做冒烟测试，验证骨架、降级、校验与定稿。仅标准库。

覆盖：空输入目录、正常 case.json、坏 JSON、证据缺口检出、partial 定稿。
退出码 0 = 全部通过；1 = 有失败。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

MAIN = Path(__file__).parent / "main.py"
PASSED = FAILED = 0


def run(args):
    return subprocess.run([sys.executable, str(MAIN)] + args,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print("  PASS %s" % name)
    else:
        FAILED += 1
        print("  FAIL %s: %s" % (name, detail))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    global PASSED, FAILED
    PASSED = FAILED = 0
    with tempfile.TemporaryDirectory(prefix="slides-selftest-") as td:
        base = Path(td)

        # 1) 空输入目录：优雅降级，不崩溃
        inp, work, out = base / "in1", base / "w1", base / "o1"
        inp.mkdir()
        r = run(["scaffold", "--input", str(inp), "--work", str(work), "--output", str(out)])
        res = read_json(out / "result.json")
        check("empty-input graceful",
              r.returncode == 0 and res.get("status") == "partial" and "case_id" in res,
              "returncode=%s" % r.returncode)

        # 2) 正常 case.json：契约键全量回填 + case_id 传播
        inp2, work2, out2 = base / "in2", base / "w2", base / "o2"
        inp2.mkdir()
        (inp2 / "case.json").write_text(json.dumps({
            "case_id": "case-1",
            "output_contract": {"presentation_file": None, "preview_files": None,
                                "page_plan": None, "evidence_map": None},
        }, ensure_ascii=False), encoding="utf-8")
        run(["scaffold", "--input", str(inp2), "--work", str(work2), "--output", str(out2)])
        res2 = read_json(out2 / "result.json")
        check("contract keys present",
              all(k in res2 for k in ("presentation_file", "preview_files", "page_plan", "evidence_map")))
        check("case_id propagated", res2.get("case_id") == "case-1")

        # 3) 坏 JSON：记录到 uncertainty.unresolved，不崩溃
        inp3, work3, out3 = base / "in3", base / "w3", base / "o3"
        inp3.mkdir()
        (inp3 / "case.json").write_text("{ not json", encoding="utf-8")
        r3 = run(["scaffold", "--input", str(inp3), "--work", str(work3), "--output", str(out3)])
        res3 = read_json(out3 / "result.json")
        check("bad-json graceful",
              r3.returncode == 0 and res3.get("status") == "partial"
              and len(res3["uncertainty"]["unresolved"]) > 0)

        # 4) 证据缺口检出：事实缺 quote/locator → check 退出码 1
        run(["record", "--work", str(work2), "--kind", "fact", "--id", "F2", "--quote", ""])
        rc = run(["check", "--work", str(work2), "--output", str(out2)])
        check("check flags gaps", rc.returncode == 1, "returncode=%s" % rc.returncode)

        # 5) partial 定稿：干净工作区 + 合格事实 → 退出码 0，status 生效
        inp5, work5, out5 = base / "in5", base / "w5", base / "o5"
        inp5.mkdir()
        (inp5 / "case.json").write_text(json.dumps({
            "case_id": "case-5",
            "output_contract": {"presentation_file": None, "preview_files": None},
        }, ensure_ascii=False), encoding="utf-8")
        run(["scaffold", "--input", str(inp5), "--work", str(work5), "--output", str(out5)])
        run(["record", "--work", str(work5), "--kind", "fact", "--id", "F1",
             "--quote", "verbatim text", "--loc", "p.3"])
        r5 = run(["finalize", "--work", str(work5), "--output", str(out5), "--status", "partial"])
        res5 = read_json(out5 / "result.json")
        check("finalize partial", r5.returncode == 0 and res5.get("status") == "partial",
              "returncode=%s" % r5.returncode)

    print("\n%d passed, %d failed" % (PASSED, FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
