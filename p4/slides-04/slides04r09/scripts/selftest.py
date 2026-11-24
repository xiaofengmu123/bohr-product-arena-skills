#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""self-test：仅用临时合成输入验证 main.py 的契约与降级路径。

不接触真实案例、不生成正式测试产物、不评估分数。退出码 0 = 通过。
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

# 1x1 透明 PNG（合成测试用，非正式案例素材）
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"

CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      '<Default Extension="xml" ContentType="application/xml"/>'
      '<Default Extension="png" ContentType="image/png"/>'
      '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
      '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
      '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
      '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
      '<Override PartName="/ppt/notesSlides/notesSlide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
      '</Types>')

ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="%s">'
             '<Relationship Id="rId1" Type="%s/officeDocument" Target="ppt/presentation.xml"/>'
             '</Relationships>') % (REL, R)

PRES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
        '<p:sldSz cx="9144000" cy="6858000" type="screen4x3"/>'
        '<p:notesSz cx="6858000" cy="9144000"/>'
        '</p:presentation>') % (A, R, P)

PRES_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="%s">'
             '<Relationship Id="rId1" Type="%s/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
             '<Relationship Id="rId2" Type="%s/slide" Target="slides/slide1.xml"/>'
             '</Relationships>') % (REL, R, R)

MASTER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<p:sldMaster xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
          '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
          '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
          '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
          '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1"/>'
          '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
          '</p:sldMaster>') % (A, R, P)

MASTER_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="%s">'
               '<Relationship Id="rId1" Type="%s/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
               '</Relationships>') % (REL, R)

LAYOUT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<p:sldLayout xmlns:a="%s" xmlns:r="%s" xmlns:p="%s" type="title">'
          '<p:cSld><p:spTree/></p:cSld></p:sldLayout>') % (A, R, P)

LAYOUT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="%s">'
               '<Relationship Id="rId1" Type="%s/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
               '</Relationships>') % (REL, R)

SLIDE = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<p:sld xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
         '<p:cSld><p:spTree>'
         '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
         '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
         '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
         '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
         '<p:spPr><a:xfrm><a:off x="457200" y="228600"/><a:ext cx="8229600" cy="914400"/></a:xfrm>'
         '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
         '<p:txBody><a:bodyPr/><a:lstStyle/>'
         '<a:p><a:r><a:rPr lang="en-US" sz="2400" dirty="0"><a:latin typeface="Calibri"/></a:rPr>'
         '<a:t>Self-test title</a:t></a:r></a:p></p:txBody></p:sp>'
         '<p:pic><p:nvPicPr><p:cNvPr id="3" name="Image"/>'
         '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
         '<p:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
         '<p:spPr><a:xfrm><a:off x="457200" y="228600"/><a:ext cx="914400" cy="914400"/></a:xfrm>'
         '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
         '</p:spTree></p:cSld></p:sld>') % (A, R, P)

SLIDE_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Relationships xmlns="%s">'
              '<Relationship Id="rId1" Type="%s/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
              '<Relationship Id="rId2" Type="%s/image" Target="../media/image1.png"/>'
              '</Relationships>') % (REL, R, R)

NOTES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<p:notes xmlns:a="%s" xmlns:r="%s" xmlns:p="%s">'
         '<p:cSld><p:spTree/></p:cSld></p:notes>') % (A, R, P)


def build_min_pptx(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("ppt/presentation.xml", PRES)
        z.writestr("ppt/_rels/presentation.xml.rels", PRES_RELS)
        z.writestr("ppt/slideMasters/slideMaster1.xml", MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", MASTER_RELS)
        z.writestr("ppt/slideLayouts/slideLayout1.xml", LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", LAYOUT_RELS)
        z.writestr("ppt/slides/slide1.xml", SLIDE)
        z.writestr("ppt/slides/_rels/slide1.xml.rels", SLIDE_RELS)
        z.writestr("ppt/media/image1.png", PNG_1X1)
        z.writestr("ppt/notesSlides/notesSlide1.xml", NOTES)


def run(args):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          capture_output=True, text=True)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    tmp = tempfile.mkdtemp(prefix="slides-selftest-")
    checks = []
    try:
        # 1. --help 退出码 0
        r = run(["--help"])
        checks.append(("help_exit0", r.returncode == 0 and "init" in r.stdout))

        # 2. 空输入目录
        inp = os.path.join(tmp, "empty", "input")
        out = os.path.join(tmp, "empty", "output")
        work = os.path.join(tmp, "empty", "work")
        os.makedirs(inp)
        r = run(["init", "--input", inp, "--output", out, "--work", work])
        res = read_json(os.path.join(out, "result.json"))
        checks.append(("empty_input_degrade", r.returncode == 0 and res["status"] == "insufficient_evidence"))

        # 3. 坏 JSON
        inp = os.path.join(tmp, "bad", "input")
        out = os.path.join(tmp, "bad", "output")
        work = os.path.join(tmp, "bad", "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        r = run(["init", "--input", inp, "--output", out, "--work", work])
        res = read_json(os.path.join(out, "result.json"))
        codes = [u.get("code") for u in res.get("uncertainty", [])]
        checks.append(("bad_json_tolerated", r.returncode == 0 and "bad_case_json" in codes))

        # 4. 正常 case + 幂等
        inp = os.path.join(tmp, "ok", "input")
        out = os.path.join(tmp, "ok", "output")
        work = os.path.join(tmp, "ok", "work")
        os.makedirs(inp)
        case = {"case_id": "selftest-01",
                "output_contract": {"presentation": {"pptx_path": None},
                                    "evidence_map": [], "render_record": {}}}
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case, f, ensure_ascii=False)
        r = run(["init", "--input", inp, "--output", out, "--work", work])
        res = read_json(os.path.join(out, "result.json"))
        c1 = (r.returncode == 0 and res["case_id"] == "selftest-01"
              and res["status"] == "in_progress"
              and "presentation" in res and "evidence_map" in res and "render_record" in res)
        # 幂等：手动写值后再 init，不被覆盖
        res["presentation"]["pptx_path"] = "keep.pptx"
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        run(["init", "--input", inp, "--output", out, "--work", work])
        res2 = read_json(os.path.join(out, "result.json"))
        c2 = res2["presentation"]["pptx_path"] == "keep.pptx"
        checks.append(("init_contract_idempotent", c1 and c2))

        # 5. evidence 逐字 quote 校验
        work2 = os.path.join(tmp, "ok", "work")
        ext = os.path.join(work2, "extracted")
        os.makedirs(ext, exist_ok=True)
        with open(os.path.join(ext, "paper.txt"), "w", encoding="utf-8") as f:
            f.write("The proposed method achieves 91.2% accuracy on the benchmark.\n")
        plan = {"page_plan": [
            {"index": 1, "title": "Results", "claim": "method accuracy",
             "refs": [{"locator": "paper.txt:1", "quote": "achieves 91.2% accuracy"}]},
            {"index": 2, "title": "Limit", "claim": "some claim",
             "refs": [{"locator": "paper.txt:1", "quote": "nonexistent phrase"}]}]}
        with open(os.path.join(work2, "page_plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False)
        r = run(["evidence", "--plan", os.path.join(work2, "page_plan.json"),
                 "--txt-glob", os.path.join(ext, "*.txt"),
                 "--output", out, "--result", os.path.join(out, "result.json")])
        res = read_json(os.path.join(out, "result.json"))
        em = res["evidence_map"]
        c3 = (r.returncode == 0 and em[0]["evidence_status"] == "located"
              and em[1]["evidence_status"] == "partial"
              and em[1]["refs"][0]["status"] == "unverified_quote")
        checks.append(("evidence_quote_verify", c3))

        # 6. validate 合成 pptx + png
        pptx = os.path.join(out, "deck.pptx")
        png = os.path.join(out, "preview.png")
        build_min_pptx(pptx)
        with open(png, "wb") as f:
            f.write(PNG_1X1)
        r = run(["validate", "--pptx", pptx, "--png", png,
                 "--output", out, "--result", os.path.join(out, "result.json")])
        res = read_json(os.path.join(out, "result.json"))
        rr = res["render_record"]
        integ = rr["integrity"]["pptx"]
        c4 = (r.returncode == 0 and integ["slide_count"] == 1
              and len(rr["layouts_used"]) == 1
              and any(m.get("aspect_ok") for m in rr["media"])
              and "Calibri" in rr["fonts"]
              and rr["exports"][0]["signature_ok"])
        checks.append(("validate_happy_path", c4))

        # 7. validate 缺 pptx 诚实降级
        out2 = os.path.join(tmp, "nop", "output")
        os.makedirs(out2)
        r = run(["validate", "--pptx", os.path.join(out2, "none.pptx"),
                 "--output", out2, "--result", os.path.join(out2, "result.json")])
        res = read_json(os.path.join(out2, "result.json"))
        codes = [c.get("code") for c in res["render_record"]["checks"]]
        c5 = r.returncode == 0 and "pptx_missing" in codes
        checks.append(("validate_missing_pptx", c5))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print("[%s] %s" % ("PASS" if ok else "FAIL", name))
        if not ok:
            failed += 1
    print("selftest: %d/%d passed" % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
