#!/usr/bin/env python3
"""selftest — 交付前自检（仅标准库）。

覆盖：
  1. SKILL.md 存在且 YAML frontmatter 含 name/description/version。
  2. 三个脚本 --help 退出码 0。
  3. 冒烟测试：合成 case.json + 最小 docx 模板 + tex 原稿，跑 main.py，
     校验 result.json 含 case_id/status 且为合法 JSON。
  4. 契约自检：verify_contract 可运行。

用法：
  python3 -B scripts/selftest.py
退出码 0=通过，非 0=失败（并在 stdout 打印逐项结果）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
SKILL_MD = os.path.join(SKILL_ROOT, "SKILL.md")


def check_frontmatter() -> tuple[bool, str]:
    if not os.path.exists(SKILL_MD):
        return False, "SKILL.md 不存在"
    try:
        text = open(SKILL_MD, encoding="utf-8-sig").read()
    except OSError as exc:
        return False, "SKILL.md 读取失败：%s" % exc
    if not text.startswith("---"):
        return False, "SKILL.md 缺少 YAML frontmatter（首行非 ---）"
    end = text.find("\n---", 3)
    if end < 0:
        return False, "SKILL.md frontmatter 未闭合"
    fm = text[3:end]
    for key in ("name", "description", "version"):
        if not ("\n" + key + ":" in fm or fm.startswith(key + ":")):
            return False, "frontmatter 缺字段：%s" % key
    return True, "frontmatter 齐备"


def check_scripts() -> list[tuple[str, bool, str]]:
    out = []
    for name in ("main.py", "core_impl.py", "selftest.py"):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            out.append((name, False, "缺失"))
            continue
        try:
            r = subprocess.run([sys.executable, "-B", p, "--help"],
                               capture_output=True, text=True, timeout=20)
            ok = r.returncode == 0
            out.append((name, ok, "exit=%d" % r.returncode))
        except Exception as exc:  # noqa: BLE001
            out.append((name, False, str(exc)))
    return out


def _minimal_docx(path: str) -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        '<w:r><w:t>1 引言</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>正文段落内容。</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表1</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1800" '
        'w:bottom="1440" w:left="1800" w:header="851" w:footer="992"/></w:sectPr>'
        '</w:body></w:document>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" '
        'w:eastAsia="SimSun"/><w:sz w:val="21"/></w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:rPr><w:rFonts w:ascii="Liberation Serif"/><w:sz w:val="28"/><w:b/></w:rPr>'
        '<w:pPr><w:spacing w:line="360" w:lineRule="auto"/></w:pPr></w:style>'
        '</w:styles>'
    )
    footnotes = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:type="separator" w:id="-1"><w:p/></w:footnote>'
        '<w:footnote w:id="1"><w:p><w:r><w:t>脚注1</w:t></w:r></w:p></w:footnote>'
        '</w:footnotes>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/footnotes.xml", footnotes)


def smoke_test() -> tuple[bool, str]:
    tmp = tempfile.mkdtemp(prefix="skill_selftest_")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        case = {
            "case_id": "smoke-001",
            "task": "把原稿按模板排版",
            "output_contract": {"deliverable": "排版稿", "fact_source_map": [],
                                "version_boundary": []},
        }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# 任务\n按模板排版\n")
        _minimal_docx(os.path.join(inp, "template.docx"))
        with open(os.path.join(inp, "manuscript.tex"), "w", encoding="utf-8") as fh:
            fh.write("\\documentclass[11pt]{article}\n\\usepackage[a4paper,top=2.5cm]{geometry}\n"
                     "\\begin{document}\n\\section{方法}\n正文。\\footnote{脚注}\n"
                     "\\begin{table}t\\end{table}\n\\end{document}\n")

        main_py = os.path.join(HERE, "main.py")
        r = subprocess.run([sys.executable, "-B", main_py, "--input", inp,
                            "--output", out, "--work", work],
                           capture_output=True, text=True, timeout=60)
        if r.returncode not in (0, 2):
            return False, "main.py 退出码=%d stderr=%s" % (r.returncode, r.stderr[:200])
        result_path = os.path.join(out, "result.json")
        if not os.path.exists(result_path):
            return False, "result.json 未生成"
        res = json.load(open(result_path, encoding="utf-8"))
        if res.get("case_id") != "smoke-001":
            return False, "case_id 错误：%r" % res.get("case_id")
        if res.get("status") != "partial":
            return False, "status 非 partial：%r" % res.get("status")
        for key in ("deliverable", "fact_source_map", "version_boundary",
                    "sources", "uncertainty", "pending_human", "change_log"):
            if key not in res:
                return False, "result.json 缺字段：%s" % key
        wp = os.path.join(work, "work_plan.json")
        if not os.path.exists(wp):
            return False, "work_plan.json 未生成"
        wpj = json.load(open(wp, encoding="utf-8"))
        specs = wpj.get("spec_checklist", {}).get("template_specs", [])
        if not specs:
            return False, "模板规格未反推"
        docx_spec = None
        for s in specs:
            if s.get("spec", {}).get("format") == "docx":
                docx_spec = s.get("spec", {})
        if docx_spec is None:
            return False, "docx 规格未反推：%r" % [s.get("spec", {}).get("format") for s in specs]
        if docx_spec.get("page", {}).get("margins", {}).get("top") != "1440":
            return False, "docx 页边距反推错误：%r" % docx_spec.get("page")
        return True, "冒烟通过（case_id/status/字段/规格反推/工作清单均正确）"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: python3 -B scripts/selftest.py  # 无参数运行完整自检，退出码 0=通过")
        return 0
    results: list[tuple[str, bool, str]] = []
    ok, msg = check_frontmatter()
    results.append(("frontmatter", ok, msg))
    for item in check_scripts():
        results.append(("script:" + item[0], item[1], item[2]))
    ok, msg = smoke_test()
    results.append(("smoke_test", ok, msg))

    print("== selftest ==")
    all_ok = True
    for name, ok, msg in results:
        mark = "PASS" if ok else "FAIL"
        print("%-22s %-4s %s" % (name, mark, msg))
        all_ok = all_ok and ok
    print("== %s ==" % ("ALL PASS" if all_ok else "SOME FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
