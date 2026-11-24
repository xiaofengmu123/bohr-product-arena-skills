#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — academic-slides-evidence 自检（纯 Python 标准库）。

覆盖四类健壮性场景，全部在临时目录运行，不写正式测试产物、不冒充案例结果：
  1. 空输入目录     -> status=insufficient_evidence，退出码 0
  2. 坏 case.json   -> 记录到 errors/degradations，退出码 0，result.json 仍合法
  3. 合成最小案例   -> 证据映射产生 locator+逐字 quote；pptx 结构检查通过
  4. 重复运行幂等   -> 相同输入两次运行结果一致（case_id/status/claims 数不变）

用法：python scripts/selftest.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS %-26s %s" % (name, detail))
    else:
        FAIL += 1
        print("FAIL %-26s %s" % (name, detail))


def run_main(inp: str, out: str, work: str, extra=None) -> int:
    cmd = [sys.executable, "-B", MAIN,
           "--input", inp, "--output", out, "--work", work] + (extra or [])
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def load(out: str):
    with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
        return json.load(f)


def _minimal_pptx(path: str):
    """构造一个结构合法（可被 zip/XML 解析）的最小 pptx，仅用于自检 inspector。"""
    ct = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' \
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' \
         '<Default Extension="xml" ContentType="application/xml"/>' \
         '<Default Extension="png" ContentType="image/png"/>' \
         '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>' \
         '</Types>'
    rels_root = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' \
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>' \
                '</Relationships>'
    pres = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
           '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    pres_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' \
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>' \
                '</Relationships>'
    master = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
             '<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    layout = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
             '<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    slide_tpl = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' \
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">' \
                '<p:cSld><p:spTree><p:sp><p:spPr><a:xfrm><a:off x="0" y="0"/>' \
                '<a:ext cx="9144000" cy="1143000"/></a:xfrm></p:spPr>' \
                '<p:txBody><a:bodyPr/><a:p><a:r><a:rPr sz="1800"/><a:t>%s</a:t>' \
                '</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
    slide_rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' \
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>' \
                 '</Relationships>'
    notes = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' \
            '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels_root)
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        z.writestr("ppt/slideMasters/slideMaster1.xml", master)
        z.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        z.writestr("ppt/slides/slide1.xml", slide_tpl % "Method uses XYZ")
        z.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        z.writestr("ppt/slides/slide2.xml", slide_tpl % "Conclusion")
        z.writestr("ppt/notesSlides/notesSlide1.xml", notes)
        z.writestr("ppt/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"0" * 16)


def _synthetic_case(root: str):
    inp = os.path.join(root, "input")
    work = os.path.join(root, "work")
    out = os.path.join(root, "output")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        json.dump({
            "case_id": "selftest-001",
            "output_contract": {
                "presentation": {"pptx_path": None},
                "evidence_map": {},
                "render_record": {},
            },
        }, f, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write("# self test\n\nchallenge_id: `selftest-001`\n")
    with open(os.path.join(inp, "materials", "paper.txt"), "w", encoding="utf-8") as f:
        f.write("We evaluated on the XYZ benchmark with 1,024 samples.\n"
                "The proposed method achieves 91.3% accuracy.\n")
    # 页计划（模拟 Agent 在 init 与 evidence 之间落盘的中间产物）
    with open(os.path.join(work, "page_plan.json"), "w", encoding="utf-8") as f:
        json.dump({
            "pages": [{
                "index": 1, "title": "Method",
                "claims": [
                    {"claim": "We evaluated on the XYZ benchmark with 1,024 samples.",
                     "type": "fact"},
                    {"claim": "achieves 91.3% accuracy", "type": "number"},
                ],
            }],
        }, f, ensure_ascii=False)
    _minimal_pptx(os.path.join(out, "deck.pptx"))
    with open(os.path.join(out, "deck.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 dummy")
    return inp, work, out


def main() -> int:
    global PASS, FAIL
    PASS = FAIL = 0
    print("== selftest: academic-slides-evidence ==")

    # 1. 空输入
    with tempfile.TemporaryDirectory() as t:
        inp = os.path.join(t, "input"); os.makedirs(inp)
        out = os.path.join(t, "output"); work = os.path.join(t, "work")
        rc = run_main(inp, out, work, ["--phase", "init"])
        res = load(out)
        check("empty_input_exit0", rc == 0, "rc=%d" % rc)
        check("empty_input_status", res.get("status") == "insufficient_evidence",
              res.get("status"))

    # 2. 坏 JSON
    with tempfile.TemporaryDirectory() as t:
        inp = os.path.join(t, "input"); os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        out = os.path.join(t, "output"); work = os.path.join(t, "work")
        rc = run_main(inp, out, work, ["--phase", "init"])
        res = load(out)
        check("bad_json_exit0", rc == 0, "rc=%d" % rc)
        check("bad_json_recorded",
              any("case.json" in (e.get("detail") if isinstance(e, dict) else str(e))
                  for e in res.get("errors", []))
              or any("case.json" in (d.get("detail") if isinstance(d, dict) else str(d))
                     for d in res.get("degradations", [])),
              "errors/degradations 已记录")

    # 3. 合成最小案例：证据 + pptx 检查
    with tempfile.TemporaryDirectory() as t:
        inp, work, out = _synthetic_case(t)
        rc = run_main(inp, out, work, ["--phase", "init"])
        check("synthetic_init_exit0", rc == 0, "rc=%d" % rc)
        rc = run_main(inp, out, work, ["--phase", "evidence"])
        check("synthetic_evidence_exit0", rc == 0, "rc=%d" % rc)
        rc = run_main(inp, out, work,
                      ["--phase", "validate", "--pptx", os.path.join(out, "deck.pptx"),
                       "--pdf", os.path.join(out, "deck.pdf")])
        check("synthetic_validate_exit0", rc == 0, "rc=%d" % rc)
        rc = run_main(inp, out, work, ["--phase", "finalize"])
        check("synthetic_finalize_exit0", rc == 0, "rc=%d" % rc)
        res = load(out)
        check("case_id_present", res.get("case_id") == "selftest-001",
              str(res.get("case_id")))
        emap = res.get("evidence_map") or {}
        claims = [c for p in emap.get("pages", []) for c in p.get("claims", [])]
        check("evidence_claims_count", len(claims) == 2, "claims=%d" % len(claims))
        ev0 = claims[0]["evidence"][0] if claims and claims[0]["evidence"] else None
        check("evidence_locator_quote",
              ev0 is not None and ev0.get("locator", {}).get("line_start")
              and ev0.get("quote", "").startswith("We evaluated"),
              "quote=%r" % (ev0.get("quote", "")[:30] if ev0 else None))
        check("evidence_verbatim", bool(ev0 and ev0.get("verbatim") is True))
        num_claim = next((c for c in claims if c["type"] == "number"), None)
        check("number_claim_status", num_claim is not None
              and num_claim.get("status") in ("verified", "partial"),
              (num_claim or {}).get("status"))
        rr = res.get("render_record") or {}
        check("pptx_slide_count", (rr.get("integrity") or {}).get("slide_count") == 2,
              str((rr.get("integrity") or {}).get("slide_count")))
        check("pptx_layout_mapped",
              bool((rr.get("integrity") or {}).get("slide_layout_map")),
              "slide1 继承 layout1")
        check("contract_fields_preserved",
              res.get("presentation") is not None
              and res.get("evidence_map") is not None
              and res.get("render_record") is not None)
        check("top_level_meta",
              all(k in res for k in ("case_id", "status", "sources",
                                     "uncertainty", "pending_human", "change_log")))
        snapshot = (res.get("case_id"), res.get("status"),
                    len(emap.get("pages", [])), len(res.get("change_log", [])))

        # 4. 幂等：再次 finalize 不改变关键结构、不重复记账
        run_main(inp, out, work, ["--phase", "finalize"])
        res2 = load(out)
        snapshot2 = (res2.get("case_id"), res2.get("status"),
                     len((res2.get("evidence_map") or {}).get("pages", [])),
                     len(res2.get("change_log", [])))
        check("idempotent_core", snapshot[:3] == snapshot2[:3],
              "%s vs %s" % (snapshot[:3], snapshot2[:3]))
        check("change_log_no_dup", snapshot2[3] == snapshot[3],
              "%d -> %d" % (snapshot[3], snapshot2[3]))

    print("\nSELFTEST %s (pass=%d fail=%d)" %
          ("OK" if FAIL == 0 else "FAILED", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
