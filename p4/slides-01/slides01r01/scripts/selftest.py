#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slides-01 自检脚本：在临时目录构造最小输入，跑通核心路径并断言不变量。

覆盖三种关键场景（空输入 / 坏 JSON / 正常材料）与产物结构校验：
  - scaffold 落盘骨架，所有契约字段齐备；
  - validate 能识别缺 quote、数值改写、备注复读；
  - build-pptx 产出可被 zipfile + ElementTree 打开的有效 pptx；
  - pdf-pages 能统计页数并识别非 PDF。

用法：python3 -B scripts/selftest.py     （退出码 0=全部通过）
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core_impl as core  # noqa: E402
import main as main_mod  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

MIN_PDF = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n%%EOF\n"
)


def make_draft(case_id="case-x", n=2):
    return {
        "case_id": case_id,
        "slides": [
            {
                "index": i + 1,
                "role": "result" if i == 1 else "method",
                "claim": f"Claim {i + 1}",
                "title": f"Page {i + 1}",
                "bullets": [f"要点 {i + 1}.1"],
                "speaker_notes": f"过渡与强调（不重复页面文字）第 {i + 1} 页",
                "evidence": [{
                    "source_id": "materials/paper.pdf",
                    "locator": f"p{i + 2} Fig.{i + 1}",
                    "quote": f"verbatim sentence {i + 1}",
                    "kind": "figure",
                    "fidelity": "verbatim",
                }],
            }
            for i in range(n)
        ],
    }


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def main() -> int:
    results = []
    tmp = Path(tempfile.mkdtemp(prefix="slides01-selftest-"))
    inp = tmp / "input"
    out = tmp / "output"
    (inp / "materials").mkdir(parents=True, exist_ok=True)

    # 场景 1：正常 case.json（含 output_contract 七字段）+ 材料
    contract = {
        "case_id": "demo-case", "status": "partial", "editable_source": {},
        "export_artifact": {}, "content_map": [], "compliance_checks": [], "exceptions": [],
    }
    core.write_json(inp / "case.json", {"case_id": "demo-case", "output_contract": contract})
    core.write_text(inp / "materials" / "paper.txt", "abstract text with Fig.1\n")

    # 1) scaffold
    case, err = core.read_json(inp / "case.json")
    results.append(check("read case.json", err is None and case["case_id"] == "demo-case", str(err)))
    res = core.scaffold_result(case)
    keys_ok = all(k in res for k in contract) and res["case_id"] == "demo-case"
    results.append(check("scaffold fills all contract keys", keys_ok, str(sorted(res))))
    results.append(check("scaffold adds envelope", all(k in res for k in core.ENVELOPE_FIELDS)))

    # 2) inspect 子命令
    code = main_mod.run(["inspect", "--input", str(inp)])
    results.append(check("main inspect exits 0", code == 0))

    # 3) validate：正常 draft + 缺陷 draft
    draft = make_draft()
    clean = core.validate_draft(draft)
    results.append(check("valid draft has no error findings",
                         not any(f["level"] == "error" for f in clean), str(clean[:2])))
    bad = make_draft()
    bad["slides"][0]["evidence"][0]["quote"] = ""
    bad["slides"][0]["speaker_notes"] = bad["slides"][0]["claim"]
    bad["slides"][1]["bullets"] = ["3.14%"]
    bad["slides"][1]["evidence"][0]["quote"] = "no numbers here"
    finds = core.validate_draft(bad)
    codes = {f["code"] for f in finds}
    results.append(check("validate flags empty quote / notes dup / numeric mismatch",
                         {"ev_no_quote", "notes_duplicate_body", "numeric_not_verbatim"} <= codes, str(codes)))

    # 4) build-pptx（空白 deck）+ 结构校验
    core.write_json(tmp / "draft.json", draft)
    code = main_mod.run(["build-pptx", "--draft", str(tmp / "draft.json"),
                         "--out", str(out / "presentation.pptx")])
    results.append(check("build-pptx exits 0", code == 0))
    ok_v, verr = core.validate_pptx(out / "presentation.pptx")
    results.append(check("generated pptx parses as zip+xml", ok_v, str(verr)))

    # 5) pdf-pages
    core.write_bytes(out / "presentation.pdf", MIN_PDF)
    results.append(check("pdf page count == 2", core.pdf_page_count(out / "presentation.pdf") == 2))
    core.write_text(out / "bad.pdf", "not a pdf")
    results.append(check("non-PDF returns -1", core.pdf_page_count(out / "bad.pdf") == -1))

    # 6) finalize 合并
    code = main_mod.run(["finalize", "--input", str(inp), "--output", str(out),
                         "--draft", str(tmp / "draft.json"),
                         "--pptx", str(out / "presentation.pptx"),
                         "--pdf", str(out / "presentation.pdf")])
    results.append(check("finalize exits 0", code == 0))
    final, ferr = core.read_json(out / "result.json")
    results.append(check("final result.json readable", ferr is None))
    results.append(check("final has evidence_map", isinstance(final.get("evidence_map"), list)))

    # 场景 2：坏 JSON 不崩溃，仍能 scaffold
    core.write_text(tmp / "badinput" / "case.json", "{not json")
    code = main_mod.run(["scaffold", "--input", str(tmp / "badinput"), "--output", str(tmp / "badout")])
    results.append(check("scaffold tolerates bad case.json", code == 0))

    # 场景 3：空输入目录 → 合法结果而非崩溃
    (tmp / "empty").mkdir(exist_ok=True)
    code = main_mod.run(["scaffold", "--input", str(tmp / "empty"), "--output", str(tmp / "emptyout")])
    empty_res, _ = core.read_json(tmp / "emptyout" / "result.json")
    results.append(check("empty input yields valid result.json",
                         code == 0 and isinstance(empty_res, dict) and empty_res.get("case_id")))

    n_pass = sum(results)
    print(f"\n{tmp}")
    print(f"selftest: {n_pass}/{len(results)} passed")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
