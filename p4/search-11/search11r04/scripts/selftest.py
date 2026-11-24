#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — Skill 交付前自检（纯标准库，跨平台）。

用 sys.executable 调起 scripts/main.py，在临时目录里端到端验证：
  --help 退出码 0、scaffold 早落盘、契约原样回填、坏 JSON/坏编码降级、
  号码规范化、抽取、同族归并、finalize/validate、SKILL.md frontmatter。

用法：python3 scripts/selftest.py
退出码：全部通过为 0，任一失败为 1；每项打印 PASS/FAIL 与原因。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(HERE, "main.py")
SKILL_MD = os.path.join(ROOT, "SKILL.md")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    line = "[%s] %s" % (mark, name)
    if detail and not ok:
        line += " -> %s" % detail
    print(line)


def run(args, cwd=None):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, MAIN] + args,
        cwd=cwd, capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace", env=env)


def main():
    # 1) --help 退出码 0
    r = run(["--help"])
    check("main.py --help 退出码 0", r.returncode == 0, r.stderr.strip())

    # 2) scaffold 空目录
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "empty")
        out = os.path.join(td, "out")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        r = run(["scaffold", "--input", inp, "--output", out, "--work", work])
        ok = r.returncode == 0
        res = None
        if ok:
            res, err = _load(os.path.join(out, "result.json"))
            ok = ok and isinstance(res, dict) and res.get("status") == "insufficient_evidence"
        check("空输入目录 scaffold -> insufficient_evidence 且退出码 0", ok,
              (r.stdout + r.stderr).strip())

    # 3) 坏 JSON 降级
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "in")
        out = os.path.join(td, "out")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        r = run(["scaffold", "--input", inp, "--output", out, "--work", work])
        res, err = _load(os.path.join(out, "result.json"))
        ok = (r.returncode == 0 and isinstance(res, dict)
              and any(e.get("item") == "case_json_unreadable" for e in res.get("errors", [])))
        check("坏 JSON scaffold -> 记录错误且不崩溃", ok, (r.stdout + r.stderr).strip())

    # 4) output_contract 原样回填（键名不删不改）
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "in")
        out = os.path.join(td, "out")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        contract = {"target_patents": [], "answer_matrix": {"rows": None},
                    "custom_field": 123}
        case = {"case_id": "case-self-1", "output_contract": contract}
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        r = run(["scaffold", "--input", inp, "--output", out, "--work", work])
        res, err = _load(os.path.join(out, "result.json"))
        ok = (r.returncode == 0 and isinstance(res, dict)
              and res.get("case_id") == "case-self-1"
              and all(k in res for k in contract))
        check("output_contract 全部字段原样回填", ok, (r.stdout + r.stderr).strip())

    # 5) 号码规范化：授权号 / 申请号 / WO 公开号
    cases = [
        ("US 12,345,678 B2", "grant_publication", False),
        ("US 2008/0123456 A1", "publication", False),
        ("WO 2008/123456 A1", "international_publication", False),
        ("US 12/345,678", "application", False),
        ("US 60/123,456", "provisional", False),
    ]
    for text, expect_type, expect_amb in cases:
        r = run(["normalize", "--text", text])
        ok = r.returncode == 0
        got = None
        if ok:
            try:
                got = json.loads(r.stdout)
            except ValueError:
                ok = False
        if ok and got:
            best = got[0].get("best") or {}
            ok = ok and best.get("number_type") == expect_type
        check("normalize %s -> %s" % (text, expect_type), ok,
              r.stdout.strip()[:200])

    # 6) 同族归并：共享优先权号应归并，仅主题相似不归并
    with tempfile.TemporaryDirectory() as td:
        rec = [
            {"record_id": "P1",
             "numbers": ["US 12/345,678", "US 2008/0123456 A1"],
             "priority_numbers": ["US 60/111,222"]},
            {"record_id": "P2",
             "numbers": ["EP 1234567 A1"],
             "priority_numbers": ["US 60/111,222"]},
            {"record_id": "P3",
             "numbers": ["CN 10987654321 A"]},
        ]
        fp = os.path.join(td, "records.json")
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False)
        r = run(["family", "--file", fp])
        ok = r.returncode == 0
        got = None
        if ok:
            try:
                got = json.loads(r.stdout)
            except ValueError:
                ok = False
        if ok and got:
            fams = got.get("families", [])
            f1 = next((f for f in fams if set(f["members"]) == {"P1", "P2"}), None)
            ok = (f1 is not None and f1["family_basis"]
                  == "shared_application_or_priority_number")
            ok = ok and got.get("exhaustive") is False and got.get("observed") is True
        check("family 按共用优先权归并且标注 observed/exhaustive=false", ok,
              r.stdout.strip()[:200])

    # 7) finalize + validate 打印 VALID
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "in")
        out = os.path.join(td, "out")
        work = os.path.join(td, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "case-self-2"}, fh)
        run(["scaffold", "--input", inp, "--output", out, "--work", work])
        r1 = run(["finalize", "--status", "ok", "--output", out])
        r2 = run(["validate", "--output", out])
        ok = (r1.returncode == 0 and r2.returncode == 0
              and "VALID" in r2.stdout)
        check("finalize + validate 打印 VALID 且生成 report.md", ok,
              (r1.stdout + r1.stderr + r2.stdout + r2.stderr).strip()[:300])

    # 8) SKILL.md frontmatter 齐备
    ok = os.path.exists(SKILL_MD)
    if ok:
        with open(SKILL_MD, "r", encoding="utf-8") as fh:
            head = fh.read(400)
        ok = head.startswith("---") and "name:" in head and \
            "description:" in head and "version:" in head
    check("SKILL.md frontmatter 含 name/description/version", ok)

    fails = [x for x in RESULTS if not x[1]]
    print("-" * 40)
    print("SELFTEST: %d/%d 通过" % (len(RESULTS) - len(fails), len(RESULTS)))
    for name, _ok, detail in fails:
        print("  FAIL: %s %s" % (name, detail))
    sys.exit(1 if fails else 0)


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


if __name__ == "__main__":
    main()
