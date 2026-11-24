#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检：不依赖第三方包，用临时目录跑一遍 scaffold/inventory/spec/bind/check，
并验证空输入、坏 JSON 两种降级路径不抛异常。退出码 0=通过，1=失败。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="deck-spec-selftest-") as tmp:
        inp = os.path.join(tmp, "input")
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "output")
        os.makedirs(inp, exist_ok=True)
        os.makedirs(work, exist_ok=True)
        os.makedirs(out, exist_ok=True)

        case = {
            "case_id": "selftest-case",
            "output_contract": {
                "presentation_file": None,
                "preview_files": [],
                "speaker_notes": None,
                "page_plan": None,
                "evidence_map": None,
                "version_records": None,
            },
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)

        def _run(name, *cmd, expect=0):
            argv = [MAIN, "--input-dir", inp, "--work-dir", work,
                    "--output-dir", out, *cmd]
            p = subprocess.run([sys.executable, "-B", *argv], cwd=tmp,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if p.returncode != expect:
                failures.append(f"{name}: exit={p.returncode} (expect {expect})\n{p.stdout}\n{p.stderr}")
            return p

        _run("scaffold", "scaffold")
        res_path = os.path.join(out, "result.json")
        if not os.path.exists(res_path):
            failures.append("scaffold: result.json 未生成")
        else:
            with open(res_path, encoding="utf-8") as f:
                r = json.load(f)
            for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
                if key not in r:
                    failures.append(f"scaffold: 缺少保留字段 {key}")
            if r.get("case_id") != "selftest-case":
                failures.append("scaffold: case_id 不符")
            for fld in case["output_contract"]:
                if fld not in r:
                    failures.append(f"scaffold: 缺少契约字段 {fld}")

        # 材料与 spec。
        with open(os.path.join(inp, "paper.md"), "w", encoding="utf-8") as f:
            f.write("# Test\n\nMaterial for inventory.")
        _run("inventory", "inventory")

        spec = {
            "title": "selftest", "audience": "同行", "template": "母版.pptx",
            "slide_count": 1,
            "pages": [{
                "role": "result", "claim": "关键结果 X=1.0",
                "locator": "paper.md#L1-L1", "quote": "# Test",
                "notes": "讲稿备注", "figure": [], "table": [],
            }],
        }
        with open(os.path.join(work, "deck_spec.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        _run("spec", "spec")
        _run("bind", "bind")
        _run("version", "version", "--note", "self", os.path.join(inp, "paper.md"))

        # check 应在缺主产物时退出 1，但不崩溃。
        p = _run("check", "check", expect=1)
        sc = os.path.join(work, "self_check.json")
        if not os.path.exists(sc):
            failures.append("check: self_check.json 未生成")

        _run("record", "record", "--step", "selftest", "--action", "ok", "--status", "ok")
        _run("finalize", "finalize", "--status", "partial")

    # 降级路径：空输入、坏 JSON。
    for label, content in (("empty", None), ("badjson", "not-json{")):
        with tempfile.TemporaryDirectory(prefix="deck-spec-degrade-") as tmp:
            inp = os.path.join(tmp, "input")
            os.makedirs(inp, exist_ok=True)
            if content is not None:
                with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
                    f.write(content)
            p = subprocess.run(
                [sys.executable, "-B", MAIN, "--input-dir", inp,
                 "--work-dir", os.path.join(tmp, "work"),
                 "--output-dir", os.path.join(tmp, "output"), "scaffold"],
                cwd=tmp, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            if p.returncode != 0:
                failures.append(f"{label}: scaffold 崩溃 exit={p.returncode}\n{p.stdout}\n{p.stderr}")

    if failures:
        print("SELFTEST FAIL")
        for item in failures:
            print(" - " + item.replace("\n", "\n   "))
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
