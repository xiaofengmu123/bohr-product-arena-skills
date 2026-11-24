#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 提交前自检：确定性、契约、降级与结构。

只依赖 Python 标准库，不联网、不写 outputs/ 之外的持久文件。所有合成输入
均带 SYNTHETIC_SELFCHECK 标记（非正式案例、非预写答案），在临时目录生成，
默认清理。

验证项：
  1. 同输入两次 init_result 产物字节一致（确定性，--now=fixed）；
  2. result.json 含 case_id / status / sources / uncertainty / change_log
     且内置三字段契约骨架完整；
  3. validate_deck 对合成最小 pptx/PDF/PNG 结构检查通过（rc=0）；
  4. validate_deck 对缺失 pptx 返回 rc=2 且仍写记录（降级不崩溃）；
  5. evidence_map 缺 locator/quote 时被标入 evidence_issues（证据契约）；
  6. SKILL.md frontmatter 的 name/description/version 齐全，且正文 <=180 行。

用法：python scripts/selftest.py [--keep]
退出码：0=全部通过；1=至少一项失败；2=参数错误（argparse 默认）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import zipfile
import zlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(SCRIPT_DIR)
SKILL_PATH = os.path.join(PACKAGE_DIR, "SKILL.md")

sys.path.insert(0, SCRIPT_DIR)
import init_result  # noqa: E402
import validate_deck  # noqa: E402

SYN = "SYNTHETIC_SELFCHECK"  # 合成标记，防止被误认为正式案例/答案

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    suffix = ("  # " + detail) if detail else ""
    print("[%s] %s%s" % (mark, name, suffix))
    return bool(cond)


def run_main(mod, argv):
    """在进程内调用脚本 main()，避免子进程管道；返回退出码。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = mod.main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, buf.getvalue()


def make_png(path, width, height):
    """写一个 1x1 或任意尺寸的最小合法 PNG（RGBA）。"""
    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        c += struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        return c

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * width for _ in range(height))
    idat = zlib.compress(raw)
    with open(path, "wb") as fh:
        fh.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def make_pdf(path):
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.4\n% " + SYN.encode() + b" minimal self-check pdf\n"
                 b"1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n")


def xml_part(content):
    return content.encode("utf-8")


def make_minimal_pptx(path):
    """构造结构上可被 validate_deck 检查的最小 pptx（zip + XML 部件）。"""
    NS_P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    NS_A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'

    parts = {
        "[Content_Types].xml": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
            '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            '<Override PartName="/ppt/notesSlides/notesSlide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships %s><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="ppt/presentation.xml"/></Relationships>' % REL
        ),
        "ppt/presentation.xml": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:presentation %s %s %s>'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
            '<p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>" % (NS_P, NS_A, NS_R)
        ),
        "ppt/_rels/presentation.xml.rels": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships %s>'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>'
            "</Relationships>" % REL
        ),
        "ppt/slideMasters/slideMaster1.xml": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sldMaster %s %s %s><p:cSld name="%s Master"/>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            "</p:sldMaster>" % (NS_P, NS_A, NS_R, SYN)
        ),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships %s>'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
            "</Relationships>" % REL
        ),
        "ppt/slideLayouts/slideLayout1.xml": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sldLayout %s %s %s type="title"><p:cSld name="%s Title Layout"/></p:sldLayout>'
            % (NS_P, NS_A, NS_R, SYN)
        ),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships %s><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" '
            'Target="../slideMasters/slideMaster1.xml"/></Relationships>' % REL
        ),
        "ppt/slides/slide1.xml": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:sld %s %s %s><p:cSld><p:spTree>'
            '<p:sp><p:txBody><a:p><a:r><a:rPr lang="en-US" sz="1800">'
            '<a:latin typeface="Calibri"/></a:rPr><a:t>%s slide text</a:t></a:r></a:p></p:txBody></p:sp>'
            "</p:spTree></p:cSld></p:sld>" % (NS_P, NS_A, NS_R, SYN)
        ),
        "ppt/slides/_rels/slide1.xml.rels": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships %s>'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>'
            "</Relationships>" % REL
        ),
        "ppt/notesSlides/notesSlide1.xml": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<p:notes %s %s %s><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>'
            '<a:t>%s speaker notes</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>'
            % (NS_P, NS_A, NS_R, SYN)
        ),
        "ppt/notesSlides/_rels/notesSlide1.xml.rels": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships %s><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/></Relationships>' % REL
        ),
        "ppt/theme/theme1.xml": xml_part(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<a:theme %s name="%s Theme"><a:themeElements>'
            '<a:fontScheme name="major"><a:majorFont><a:latin typeface="Calibri"/>'
            '<a:ea typeface="SimSun"/></a:majorFont></a:fontScheme>'
            "</a:themeElements></a:theme>" % (NS_A, SYN)
        ),
    }

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
        zf.writestr("ppt/media/sample.png", _png_bytes(2, 2))


def _png_bytes(width, height):
    buf = io.BytesIO()
    def chunk(typ, data):
        buf.write(struct.pack(">I", len(data)))
        buf.write(typ)
        buf.write(data)
        buf.write(struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))
    buf.write(b"\x89PNG\r\n\x1a\n")
    chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * width for _ in range(height))
    chunk(b"IDAT", zlib.compress(raw))
    chunk(b"IEND", b"")
    return buf.getvalue()


def check_skill_frontmatter():
    if not os.path.isfile(SKILL_PATH):
        return False, "SKILL.md 不存在"
    with open(SKILL_PATH, "r", encoding="utf-8-sig") as fh:
        lines = fh.read().splitlines()
    if not lines or lines[0].strip() != "---":
        return False, "缺 YAML frontmatter 起始行"
    keys = {}
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ":" in ln and not ln.strip().startswith("#"):
            k, v = ln.split(":", 1)
            keys[k.strip()] = v.strip()
    ok = all(keys.get(k) for k in ("name", "description", "version"))
    detail = "frontmatter keys=%s" % sorted(keys.keys())
    if not ok:
        return False, detail
    if len(lines) > 180:
        return False, "SKILL.md 行数 %d > 180" % len(lines)
    return True, detail + " lines=%d" % len(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Skill 提交前自检")
    parser.add_argument("--keep", action="store_true", help="保留临时自检产物")
    args = parser.parse_args(argv)

    print("selftest: package=%s" % PACKAGE_DIR)
    tmp = tempfile.mkdtemp(prefix="slides04-selftest-")
    ok = True
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "selfcheck-demo", "title": SYN}, fh,
                      ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# %s task\n生成学术 PPT 并保留证据映射。\n" % SYN)
        with open(os.path.join(inp, "material.txt"), "w", encoding="utf-8") as fh:
            fh.write("%s material line for evidence.\n" % SYN)

        # 1) 确定性：同输入两次运行（不同 output/work）结果字节一致
        out_a = os.path.join(tmp, "out_a")
        out_b = os.path.join(tmp, "out_b")
        work_a = os.path.join(tmp, "work_a")
        work_b = os.path.join(tmp, "work_b")
        rc_a, _ = run_main(init_result, ["--input", inp, "--output", out_a,
                                         "--work", work_a, "--now", "fixed"])
        rc_b, _ = run_main(init_result, ["--input", inp, "--output", out_b,
                                         "--work", work_b, "--now", "fixed"])
        ra = open(os.path.join(out_a, "result.json"), "rb").read()
        rb = open(os.path.join(out_b, "result.json"), "rb").read()
        ok &= check("determinism_init_result", rc_a == 0 and rc_b == 0 and ra == rb,
                    "rc=%d/%d bytes_equal=%s" % (rc_a, rc_b, ra == rb))

        # 2) 契约骨架完整
        rj = json.loads(ra.decode("utf-8"))
        need = {"case_id", "status", "sources", "uncertainty", "change_log",
                "presentation", "evidence_map", "render_record"}
        missing = sorted(need - set(rj.keys()))
        ok &= check("result_contract_fields", not missing, "missing=%s" % missing)
        ok &= check("result_case_id", rj.get("case_id") == "selfcheck-demo",
                    "case_id=%s" % rj.get("case_id"))

        # 3) validate_deck 对合成最小 deck 结构检查通过
        fx = os.path.join(tmp, "fx")
        os.makedirs(fx)
        pptx = os.path.join(fx, "deck.pptx")
        pdf = os.path.join(fx, "deck.pdf")
        png = os.path.join(fx, "page1.png")
        make_minimal_pptx(pptx)
        make_pdf(pdf)
        make_png(png, 960, 540)
        rc_v, _ = run_main(validate_deck, [
            "--pptx", pptx, "--pdf", pdf, "--png", png,
            "--template", pptx, "--result", os.path.join(out_a, "result.json"),
            "--output", fx, "--now", "fixed"])
        ok &= check("validate_deck_ok", rc_v == 0, "rc=%d" % rc_v)

        # 4) 缺失文件 -> rc=2 且仍写 render_record.json
        miss_dir = os.path.join(tmp, "miss")
        os.makedirs(miss_dir)
        rc_m, _ = run_main(validate_deck, ["--pptx", os.path.join(miss_dir, "none.pptx"),
                                           "--output", miss_dir, "--now", "fixed"])
        rr_written = os.path.isfile(os.path.join(miss_dir, "render_record.json"))
        ok &= check("validate_deck_missing_degrades", rc_m == 2 and rr_written,
                    "rc=%d record_written=%s" % (rc_m, rr_written))

        # 5) evidence_map 缺 locator/quote 被标记
        with open(os.path.join(out_a, "result.json"), "r", encoding="utf-8") as fh:
            rj2 = json.load(fh)
        rj2["evidence_map"] = [{"page": 1, "refs": [{"locator": "", "quote": ""}]}]
        with open(os.path.join(out_a, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(rj2, fh, ensure_ascii=False, indent=2, sort_keys=True)
        run_main(validate_deck, ["--pptx", pptx, "--pdf", pdf, "--png", png,
                                 "--result", os.path.join(out_a, "result.json"),
                                 "--output", fx, "--now", "fixed"])
        with open(os.path.join(fx, "render_record.json"), "r", encoding="utf-8") as fh:
            rr = json.load(fh)
        ev_codes = {i["code"] for i in rr.get("evidence_issues", [])}
        ok &= check("evidence_locator_quote_contract",
                    {"evidence_locator_missing", "evidence_quote_empty"} <= ev_codes,
                    "codes=%s" % sorted(ev_codes))

        # 6) SKILL.md frontmatter 与行数
        fm_ok, fm_detail = check_skill_frontmatter()
        ok &= check("skill_frontmatter", fm_ok, fm_detail)

    finally:
        if args.keep:
            print("selftest: kept temp dir -> %s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    total = sum(1 for _, passed, _ in RESULTS if passed)
    print("selftest: %d/%d passed" % (total, len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
