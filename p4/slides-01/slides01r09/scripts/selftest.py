#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-01 自检：覆盖空输入 / 坏 JSON / 契约解析 / 骨架 / 证据校验 / pptx / 继承 / PDF。

用法：python3 -B scripts/selftest.py
退出码 = 失败用例数（0 表示全部通过）。仅依赖 Python 标准库。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from slides_lib import (  # noqa: E402
    build_deck, build_skeleton, default_value_for, extract_contract_keys,
    fill_contract, inherit_deck, is_zip_pptx, pdf_page_count, pptx_stats,
    read_json, validate_draft, write_json,
)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def test_io(tmp: Path):
    print("[io]")
    bad = tmp / "bad.json"
    bad.write_bytes(b"{not json")
    obj, err = read_json(bad)
    check("坏 JSON 返回 err", obj is None and err)
    empty = tmp / "empty.json"
    empty.write_bytes(b"")
    obj, err = read_json(empty)
    check("空文件返回 err", obj is None and err)
    ok = tmp / "ok.json"
    ok.write_bytes('{"a":1}'.encode("utf-8-sig"))
    obj, err = read_json(ok)
    check("utf-8-sig 正常读取", err is None and obj == {"a": 1})


def test_contract():
    print("[contract]")
    keys, note = extract_contract_keys({"output_contract": {"a": None, "b": []}})
    check("dict 形态解析", keys == ["a", "b"] and note is None)
    keys, _ = extract_contract_keys({"output_contract": ["x", "y"]})
    check("list[str] 形态解析", keys == ["x", "y"])
    keys, _ = extract_contract_keys({"output_contract": [{"name": "z"}, {"field": "w"}]})
    check("list[dict] 形态解析", keys == ["z", "w"])
    keys, note = extract_contract_keys({})
    check("缺失 output_contract 回退规范字段", keys and note)

    filled, missing = fill_contract(["a", "b", "content_map"], {"a": 1})
    check("回填 + 缺省类型", filled["a"] == 1 and filled["b"] is None
          and filled["content_map"] == [] and missing == ["b", "content_map"])
    check("default_value_for 类型推断", default_value_for("export_artifact") == {}
          and default_value_for("compliance_checks") == []
          and default_value_for("case_id") is None)

    sk = build_skeleton("t1", ["case_id", "evidence_map"], status="partial")
    check("骨架含契约字段 + 信封", sk.get("case_id") == "t1"
          and sk.get("evidence_map") == []
          and all(f in sk for f in
                  ("status", "sources", "uncertainty", "pending_human",
                   "errors", "degradations", "change_log", "runtime"))
          and "started_at" in sk.get("runtime", {}))


def test_validate():
    print("[validate]")
    draft = {"slides": [{"page": 1, "claim": "c", "role": "result",
                         "evidence": [{"locator": "paper.md#p3", "quote": "q"}]}]}
    check("完整草稿无问题", validate_draft(draft) == [])
    draft_bad = {"slides": [{"page": 1, "claim": "c", "role": "result", "evidence": []}]}
    check("缺 evidence 报错", any("无 evidence" in x for x in validate_draft(draft_bad)))
    draft_noquote = {"slides": [{"page": 1, "claim": "c", "role": "result",
                                 "evidence": [{"locator": "x", "quote": ""}]}]}
    check("缺 quote 报错", any("quote" in x for x in validate_draft(draft_noquote)))


def test_pptx(tmp: Path):
    print("[pptx]")
    slides = [{"title": "T1", "body": ["b1", "b2"]}, {"title": "T2", "body": ["b3"]}]
    out = tmp / "deck.pptx"
    ok, err = build_deck(slides, out)
    check("build_deck 成功", ok and err is None and out.exists())
    check("最小 deck 是合法 pptx", is_zip_pptx(out))
    stats, serr = pptx_stats(out)
    check("stats 页数正确", serr is None and stats and stats["slides"] == 2
          and stats["masters"] >= 1 and stats["layouts"] >= 1)

    tpl = tmp / "template.pptx"
    ok, _ = build_deck([{"title": "Tpl", "body": ["placeholder"]}], tpl)
    inh = tmp / "inherited.pptx"
    ok2, err2 = inherit_deck(tpl, inh, [{"title": "A", "body": ["x"]},
                                         {"title": "B", "body": ["y"]}])
    check("inherit_deck 成功", ok and ok2 and err2 is None and inh.exists())
    check("继承产物是合法 pptx", is_zip_pptx(inh))
    stats2, _ = pptx_stats(inh)
    check("继承后页数=原1+新2", stats2 and stats2["slides"] == 3)


def test_pdf(tmp: Path):
    print("[pdf]")
    pdf = tmp / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Page >>\n2 0 obj << /Type /Page >>\n%%EOF")
    cnt, err = pdf_page_count(pdf)
    check("PDF 页数=2", err is None and cnt == 2)
    notpdf = tmp / "x.txt"
    notpdf.write_bytes(b"hello")
    cnt, err = pdf_page_count(notpdf)
    check("非 PDF 报错", cnt is None and err)


def test_cli(tmp: Path):
    print("[cli]")
    main_py = SCRIPT_DIR / "main.py"
    r = subprocess.run([sys.executable, "-B", str(main_py), "--help"],
                       capture_output=True, text=True)
    check("main.py --help 退出码 0", r.returncode == 0)

    inp = tmp / "in"; work = tmp / "work"; out = tmp / "out"
    inp.mkdir(parents=True)
    write_json(inp / "case.json", {
        "case_id": "test-case",
        "output_contract": {
            "case_id": None, "editable_source": None, "export_artifact": None,
            "content_map": None, "compliance_checks": None, "exceptions": None,
        },
    })
    (inp / "paper.md").write_text("# T\n\n内容", encoding="utf-8")
    r = subprocess.run([sys.executable, "-B", str(main_py), "scaffold",
                        "--input", str(inp), "--work", str(work), "--output", str(out)],
                       capture_output=True, text=True)
    check("scaffold 退出码 0", r.returncode == 0, r.stderr)
    res, err = read_json(out / "result.json")
    check("scaffold 产出合法 result.json", err is None and res.get("case_id") == "test-case"
          and all(k in res for k in ("case_id", "editable_source", "export_artifact",
                                     "content_map", "compliance_checks", "exceptions"))
          and res.get("status") == "partial" and isinstance(res.get("sources"), list)
          and isinstance(res.get("change_log"), list) and res["change_log"])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="slides01_selftest_"))
    try:
        test_io(tmp)
        test_contract()
        test_validate()
        test_pptx(tmp)
        test_pdf(tmp)
        test_cli(tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
