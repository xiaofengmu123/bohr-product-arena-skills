#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py：对 main.py / validate_deck.py 做鲁棒性自检（仅标准库）。

在临时目录内构造并运行六类场景：
  1) 空输入目录    → 退出码 0，status=insufficient_evidence
  2) 坏 JSON       → 退出码 0，errors 非空，status=input_error
  3) 缺 --input    → 退出码 0，默认指向不存在的 /input，优雅降级
  4) 超大文件      → 退出码 0，跳过并写入 degradations
  5) 重复运行幂等  → merge 后字段在重跑 init 后仍保留
  6) validate_deck → 缺失 pptx 时退出码 0，报告标记 failed

全部通过退出码 0，否则 1。可用于提交前本地自检，不产生正式测试产物。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
VALIDATE = os.path.join(HERE, "validate_deck.py")
PY = sys.executable

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print("[{}] {} {}".format(mark, name, ("- " + detail) if detail else ""))


def run(cmd, **kw):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    kw.setdefault("env", env)
    kw.setdefault("encoding", "utf-8")
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    root = tempfile.mkdtemp(prefix="slides_selftest_")
    try:
        # 1. 空输入
        d = os.path.join(root, "empty")
        out = os.path.join(root, "out_empty")
        work = os.path.join(root, "work_empty")
        os.makedirs(d)
        rc = run([PY, MAIN, "init", "--input", d, "--output", out,
                  "--work", work]).returncode
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("空输入目录", rc == 0 and r["status"] == "insufficient_evidence",
              "status={}".format(r["status"]))

        # 2. 坏 JSON
        d = os.path.join(root, "badjson")
        out = os.path.join(root, "out_badjson")
        work = os.path.join(root, "work_badjson")
        os.makedirs(d)
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        rc = run([PY, MAIN, "init", "--input", d, "--output", out,
                  "--work", work]).returncode
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("坏 JSON 降级", rc == 0 and r["status"] == "input_error"
              and len(r["errors"]) > 0, "status={} errors={}".format(
                  r["status"], len(r["errors"])))

        # 3. 缺 --input（默认指向 /input，环境内容不定；只验证不崩溃、结果合法）
        out = os.path.join(root, "out_nodefault")
        work = os.path.join(root, "work_nodefault")
        rc = run([PY, MAIN, "init", "--output", out, "--work", work]).returncode
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("缺 --input 默认值降级", rc == 0
              and r["status"] in ("ok", "partial", "failed",
                                  "insufficient_evidence", "input_error",
                                  "in_progress"),
              "status={}".format(r["status"]))

        # 4. 超大文件跳过
        d = os.path.join(root, "big")
        out = os.path.join(root, "out_big")
        work = os.path.join(root, "work_big")
        os.makedirs(d)
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "big-case"}, fh)
        with open(os.path.join(d, "huge.pdf"), "wb") as fh:
            fh.write(b"0" * (3 * 1024 * 1024))
        rc = run([PY, MAIN, "init", "--input", d, "--output", out,
                  "--work", work]).returncode
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        skipped = [x for x in r["degradations"]
                   if x.get("detail") == "oversized_skipped"]
        check("超大文件跳过", rc == 0 and len(skipped) == 1,
              "degradations={}".format(len(r["degradations"])))

        # 5. 重复运行幂等（merge 后的字段重跑 init 仍保留）
        d = os.path.join(root, "idem")
        out = os.path.join(root, "out_idem")
        work = os.path.join(root, "work_idem")
        os.makedirs(d)
        with open(os.path.join(d, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "idem-case"}, fh)
        run([PY, MAIN, "init", "--input", d, "--output", out, "--work", work])
        patch = os.path.join(work, "patch.json")
        with open(patch, "w", encoding="utf-8") as fh:
            json.dump({"presentation": {"slide_count": 12}}, fh)
        run([PY, MAIN, "merge", "--patch", patch, "--output", out])
        rc = run([PY, MAIN, "init", "--input", d, "--output", out,
                  "--work", work]).returncode
        r = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        check("重复运行幂等", rc == 0
              and r["presentation"].get("slide_count") == 12,
              "slide_count={}".format(r["presentation"].get("slide_count")))

        # 6. validate_deck 对缺失 pptx 的降级
        out = os.path.join(root, "out_vd")
        rc = run([PY, VALIDATE, "--pptx", os.path.join(root, "nope.pptx"),
                  "--output", out]).returncode
        rep = json.load(open(os.path.join(out, "render_validation.json"),
                             encoding="utf-8"))
        check("validate_deck 缺失文件降级", rc == 0
              and rep["pptx"]["exists"] is False
              and len(rep["pptx"]["errors"]) > 0,
              "errors={}".format(len(rep["pptx"]["errors"])))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    failed = [c for c in CHECKS if not c[1]]
    print("\nselftest: {}/{} passed".format(len(CHECKS) - len(failed), len(CHECKS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
