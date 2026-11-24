#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本：在临时目录内跑通 init→lint→compile→audit→selfcheck，
并验证契约完整、产物真实、重复运行字节级确定、降级路径不崩溃。

用法：python scripts/selftest.py [--keep]   （默认清理临时目录；--keep 保留便于查看）
退出码：0 = 全部通过，1 = 有失败项。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import core  # noqa: E402
import main  # noqa: E402

CONTRACT_KEYS = ["case_id", "status", "editable_source", "export_artifact",
                 "content_map", "compliance_checks", "exceptions"]
ENVELOPE_KEYS = ["sources", "uncertainty", "pending_human", "change_log"]


def build_min_template() -> bytes:
    """构造一个最小但合法的模板 pptx（1 母版 + 1 版式 + 1 主题 + 1 内容页）。"""
    NS = core.NS_P
    RT = core.RT
    W, H = core.DEFAULT_W, core.DEFAULT_H
    parts = {
        "[Content_Types].xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            "</Types>"),
        "_rels/.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/officeDocument" Target="ppt/presentation.xml"/></Relationships>'),
        "ppt/presentation.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentation {NS} saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
            f'<p:sldSz cx="{W}" cy="{H}"/></p:presentation>'),
        "ppt/_rels/presentation.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
            f'<Relationship Id="rId2" Type="{RT}/slide" Target="slides/slide1.xml"/></Relationships>'),
        "ppt/slideMasters/slideMaster1.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sldMaster {NS}><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '</p:spTree></p:cSld>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>'),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            f'<Relationship Id="rId2" Type="{RT}/theme" Target="../theme/theme1.xml"/></Relationships>'),
        "ppt/slideLayouts/slideLayout1.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sldLayout {NS} type="titleAndContent" preserve="1"><p:cSld name="Title and Content"><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>'),
        "ppt/theme/theme1.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Std">'
            '<a:themeElements><a:clrScheme name="Std"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
            '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
            '<a:accent1><a:srgbClr val="1F77B4"/></a:accent1><a:accent2><a:srgbClr val="D62728"/></a:accent2>'
            '<a:accent3><a:srgbClr val="2CA02C"/></a:accent3><a:accent4><a:srgbClr val="9467BD"/></a:accent4>'
            '<a:accent5><a:srgbClr val="FF7F0E"/></a:accent5><a:accent6><a:srgbClr val="17BECF"/></a:accent6>'
            '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
            '<a:fontScheme name="Std"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
            '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
            '<a:fmtScheme name="Std"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
            '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
            '<a:ln w="25400"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
            '<a:ln w="38100"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
            '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
            '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
            '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
            '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
            '</a:fmtScheme></a:themeElements></a:theme>'),
        "ppt/slides/slide1.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sld {NS}><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
            '<p:spPr><a:xfrm><a:off x="609600" y="365760"/><a:ext cx="10972800" cy="1188720"/></a:xfrm></p:spPr>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:t>Template Sample</a:t></a:p></p:txBody></p:sp>'
            '</p:spTree></p:cSld></p:sld>'),
        "ppt/slides/_rels/slide1.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RT}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>'),
    }
    return core.write_zip(parts)


def make_fixture(root: Path) -> None:
    inp = root / "input"
    work = root / "work"
    out = root / "output"
    inp.mkdir(parents=True)
    material = (
        "Sample A was prepared by incipient wetness impregnation on gamma-Al2O3 "
        "with a loading of 5 wt%.\n\n"
        "Catalytic tests were performed in a fixed-bed microreactor loaded with 0.20 g of catalyst. "
        "Under 25 C and a space velocity of 30000 h-1, the conversion of sample A reached 87.4%.\n\n"
        "In a control experiment under identical conditions, the conversion of sample B was only 41.2%.\n\n"
        "After 100 h on stream, the conversion of sample A remained at 84.1%.\n\n"
        "The authors attribute the higher activity to surface oxygen vacancy concentration.\n"
    )
    (inp / "material.md").write_text(material, encoding="utf-8")
    (inp / "task.md").write_text("# 演示任务\n按 output_contract 交付。\n", encoding="utf-8")
    (inp / "template.pptx").write_bytes(build_min_template())
    case = {
        "case_id": "selftest-001",
        "task_instruction": "自检合成示例：读 material.md，套 template.pptx 生成幻灯片。",
        "output_contract": [
            {"name": "case_id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": True},
            {"name": "editable_source", "type": "object", "required": True},
            {"name": "export_artifact", "type": "object", "required": True},
            {"name": "content_map", "type": "array", "required": True},
            {"name": "compliance_checks", "type": "array", "required": True},
            {"name": "exceptions", "type": "array", "required": True},
        ],
    }
    (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    deck = {
        "case_id": "selftest-001",
        "mode": "adapt",
        "plan": {"purpose": "演示", "audience": "评审", "duration_min": 5, "narrative": "主线"},
        "template": {"path": "template.pptx"},
        "pages": [
            {"n": 1, "role": "title", "title": "样品 A 催化性能", "claim": "样品 A 表现出高转化率",
             "bullets": ["基于固定床微反应器评价"], "notes": "开场",
             "evidence": [{"source_id": "S1", "locator": "para.1", "quote": "Sample A was prepared by incipient wetness impregnation"}]},
            {"n": 2, "role": "background", "title": "研究背景", "claim": "活性来源与氧空位相关",
             "bullets": ["氧空位浓度影响活性"],
             "evidence": [{"source_id": "S1", "locator": "para.5", "quote": "surface oxygen vacancy concentration"}]},
            {"n": 3, "role": "method", "title": "方法", "claim": "固定床微反应器评价",
             "bullets": ["0.20 g 催化剂", "30000 h-1 空速"],
             "evidence": [{"source_id": "S1", "locator": "para.2", "quote": "fixed-bed microreactor loaded with 0.20 g of catalyst"}]},
            {"n": 4, "role": "result", "title": "结果", "claim": "样品 A 转化率显著高于 B",
             "bullets": ["A: 87.4%", "B: 41.2%"],
             "evidence": [{"source_id": "S1", "locator": "para.2", "quote": "the conversion of sample A reached 87.4%"},
                          {"source_id": "S1", "locator": "para.3", "quote": "the conversion of sample B was only 41.2%"}]},
            {"n": 5, "role": "conclusion", "title": "结论与局限", "claim": "稳定性良好但机制待补",
             "bullets": ["100 h 后仍 84.1%"],
             "evidence": [{"source_id": "S1", "locator": "para.4", "quote": "the conversion of sample A remained at 84.1%"}]},
        ],
        "uncertainties": [], "pending_human": [],
    }
    (work).mkdir(parents=True)
    (work / "deck.json").write_text(json.dumps(deck, ensure_ascii=False), encoding="utf-8")
    out.mkdir(parents=True)


def run_cli(args: list) -> int:
    return main.main(args)


def check(cond: bool, label: str, failures: list) -> None:
    failures.append(None if cond else label)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


def main_selftest(keep: bool) -> int:
    failures: list = []
    root = Path(tempfile.mkdtemp(prefix="slides-selftest-"))
    try:
        make_fixture(root)
        inp, work, out = root / "input", root / "work", root / "output"
        common = ["--input", str(inp), "--work", str(work), "--output", str(out)]

        print("== 1. init ==")
        rc = run_cli(["init"] + common)
        check(rc == 0, "init 退出码 0", failures)
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        check(result.get("case_id") == "selftest-001", "case_id 回填", failures)
        check(result.get("status") == "partial", "init 后 status=partial", failures)
        check(all(k in result for k in CONTRACT_KEYS), "init 后七字段齐全", failures)

        print("== 2. lint ==")
        check(run_cli(["lint"] + common) == 0, "lint 退出码 0", failures)
        lint = json.loads((work / "lint_report.json").read_text(encoding="utf-8"))
        check(lint.get("fail", 99) == 0, "lint 无 FAIL", failures)

        print("== 3. compile ==")
        check(run_cli(["compile"] + common) == 0, "compile 退出码 0", failures)
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        check(all(k in result for k in CONTRACT_KEYS), "result.json 七字段齐全", failures)
        check(all(k in result for k in ENVELOPE_KEYS), "envelope 字段齐全", failures)
        check(result.get("status") == "ok", f"status=ok（实际 {result.get('status')}）", failures)

        pptx_path = out / "presentation.pptx"
        pptx = pptx_path.read_bytes() if pptx_path.is_file() else b""
        check(pptx[:2] == b"PK", "pptx 为真实 ZIP", failures)
        info = core.deck_info(pptx)
        check(info.get("valid"), "pptx 结构合法", failures)
        check(info.get("slides") == 5, "pptx 页数 == 计划", failures)

        # 模板真继承：母版/主题 sha256 与模板一致
        tpl_info = core.inspect_template((inp / "template.pptx").read_bytes())
        check(info.get("master_sha256") == tpl_info.get("master_sha256"), "母版 sha256 与模板一致（真继承）", failures)
        check(info.get("theme_sha256") == tpl_info.get("theme_sha256"), "主题 sha256 与模板一致", failures)
        check(all(r == "slideLayout1.xml" for r in info.get("slide_rel_layouts") or []), "新页 rels 指向模板版式", failures)

        pdf = (out / "presentation.pdf").read_bytes()
        check(pdf[:5] == b"%PDF-", "PDF 头合法", failures)
        check(core.pdf_page_count(pdf) == 5, "PDF 页数 == 幻灯片数", failures)
        png = (out / "preview.png").read_bytes()
        check(png[:8] == b"\x89PNG\r\n\x1a\n", "PNG 预览存在", failures)

        print("== 4. 回读 audit / selfcheck ==")
        check(run_cli(["audit"] + common) == 0, "audit 退出码 0", failures)
        check(run_cli(["selfcheck"] + common) == 0, "selfcheck 退出码 0", failures)

        print("== 5. 确定性（重复 compile 字节级一致）==")
        before = pptx
        run_cli(["compile"] + common)
        after = (out / "presentation.pptx").read_bytes()
        check(before == after, "两次 compile 的 pptx 字节一致", failures)

        print("== 6. 降级路径：空输入目录 ==")
        empty = root / "empty_in"
        empty.mkdir()
        ework, eout = root / "ework", root / "eout"
        run_cli(["init", "--input", str(empty), "--work", str(ework), "--output", str(eout)])
        eresult = json.loads((eout / "result.json").read_text(encoding="utf-8"))
        check("result.json" in [p.name for p in eout.iterdir()], "空输入仍落 result.json", failures)
        check(eresult.get("status") in core.STATUS, "空输入 status 合法", failures)

        print("== 7. 降级路径：坏 JSON case.json ==")
        bad = root / "bad_in"
        bad.mkdir()
        (bad / "case.json").write_text("{not valid json", encoding="utf-8")
        bwork, bout = root / "bwork", root / "bout"
        check(run_cli(["init", "--input", str(bad), "--work", str(bwork), "--output", str(bout)]) == 0,
              "坏 JSON 不崩溃（退出码 0）", failures)
        bresult = json.loads((bout / "result.json").read_text(encoding="utf-8"))
        check(bresult.get("status") in core.STATUS, "坏 JSON status 合法", failures)

    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
        else:
            print(f"\n临时目录保留：{root}")

    n_fail = sum(1 for f in failures if f is not None)
    print(f"\n自检结果：{len(failures) - n_fail}/{len(failures)} 通过，{n_fail} 失败")
    if n_fail:
        for f in failures:
            if f:
                print(f"  FAIL: {f}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="slides Skill 自检")
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    args = parser.parse_args()
    sys.exit(main_selftest(args.keep))
