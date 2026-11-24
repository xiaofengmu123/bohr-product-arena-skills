#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 包自检（纯标准库）：提交/交付前逐条验证。

覆盖：SKILL.md frontmatter、脚本可导入可运行、契约骨架（output_contract 全字段回填、
未知字段不改名不删除）、信封字段齐全、pptx/pdf 体检、坏 JSON / 空目录降级。
用法：python3 scripts/selftest.py  （退出码 0 = 全部通过）
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PKG_ROOT = SCRIPT_DIR.parent
MAIN = SCRIPT_DIR / "main.py"
PPU = SCRIPT_DIR / "pptx_util.py"

sys.path.insert(0, str(SCRIPT_DIR))
import pptx_util as ppu  # noqa: E402

FAIL = []


def ok(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAIL.append(name)
    print(f"[{mark}] {name}" + (f"  — {detail}" if detail else ""))
    return cond


def read(path):
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def check_frontmatter():
    txt = read(PKG_ROOT / "SKILL.md")
    if not txt:
        ok("SKILL.md exists", False, "missing file")
        return
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", txt, re.S)
    ok("SKILL.md has YAML frontmatter", bool(m))
    if not m:
        return
    body = m.group(1)
    ok("frontmatter name", re.search(r"^name:\s*\S+", body, re.M) is not None)
    ok("frontmatter description", re.search(r"^description:\s*\S+", body, re.M) is not None)
    ok("frontmatter version", re.search(r"^version:\s*\S+", body, re.M) is not None)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def check_scripts_runnable():
    r = run([sys.executable, str(MAIN), "--help"])
    ok("main.py --help exit 0", r.returncode == 0, r.stderr.strip()[:120])
    r2 = run([sys.executable, str(PPU), "--help"])
    ok("pptx_util.py --help exit 0", r2.returncode == 0)


def _min_pptx(path: Path):
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
                   '</Types>')
        z.writestr("ppt/slides/slide1.xml",
                   '<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld name="Slide 1"/></p:sld>')
        z.writestr("ppt/slideLayouts/slideLayout1.xml",
                   '<?xml version="1.0"?><p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" name="\u6807\u9898\u5e7b\u706f\u7247"/>')
        z.writestr("ppt/slideMasters/slideMaster1.xml",
                   '<?xml version="1.0"?><p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" name="Office Theme"/>')
        z.writestr("ppt/theme/theme1.xml",
                   '<?xml version="1.0"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme"/>')


_MIN_PDF = (b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n"
            b"trailer\n<< /Root 1 0 R >>\n%%EOF\n")


def check_contract_build(tmp: Path):
    inp = tmp / "demo" / "input"
    work = tmp / "demo" / "work"
    out = tmp / "demo" / "output"
    inp.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    case = {
        "case_id": "demo-case-001",
        "output_contract": {
            "case_id": "string",
            "editable_source": "object",
            "export_artifact": "object",
            "content_map": "list",
            "compliance_checks": "list",
            "exceptions": "list",
            "custom_note": "string",
        },
    }
    (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    (inp / "paper.md").write_text("exact demo quote line\n", encoding="utf-8")

    plan = {
        "deck_title": "Demo Deck",
        "audience": "reviewers",
        "time_min": 5,
        "slide_cap": 6,
        "narrative": ["问题", "方法", "结论"],
        "pages": [{
            "slide_no": 1,
            "role": "title",
            "claim": "Demo claim",
            "bullets": ["one point"],
            "figure_refs": [],
            "layout": "标题幻灯片",
            "speaker_notes": "过渡与强调点，不重复页面文字",
            "evidence": [{
                "source_id": "paper.md",
                "locator": "paper.md#L1",
                "quote": "exact demo quote",
            }],
        }],
        "sources": [{"source_id": "paper.md", "path": "paper.md", "kind": "markdown"}],
        "uncertainty": [{"item": "演示不确定性"}],
        "pending_human": [],
        "field_assignments": {"custom_note": None},
    }
    (work / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    _min_pptx(out / "presentation.pptx")
    (out / "presentation.pdf").write_bytes(_MIN_PDF)

    r = run([sys.executable, str(MAIN),
             "--input", str(inp), "--work", str(work), "--output", str(out),
             "--plan", str(work / "plan.json"), "--check-quotes"])
    ok("main.py full build exit 0", r.returncode == 0, r.stderr.strip()[:200])

    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    contract_keys = ["case_id", "editable_source", "export_artifact",
                     "content_map", "compliance_checks", "exceptions", "custom_note"]
    missing = [k for k in contract_keys if k not in result]
    ok("all output_contract fields present", not missing, f"missing={missing}")
    ok("unknown field kept (not renamed/dropped)", "custom_note" in result and result["custom_note"] is None)
    ok("case_id echoed", result.get("case_id") == "demo-case-001")
    ok("status valid", result.get("status") in {"ok", "partial", "insufficient_evidence", "input_error"})
    for env in ("sources", "uncertainty", "pending_human", "change_log", "errors", "degradations", "runtime"):
        ok(f"envelope {env} present", env in result)
    ok("change_log non-empty", bool(result.get("change_log")))
    ok("status=ok for valid demo", result.get("status") == "ok", f"got {result.get('status')}")

    inv = ppu.inventory_pptx(out / "presentation.pptx")
    ok("pptx inventory slides=1", inv.get("slides") == 1, str(inv))
    ok("pptx layout detected", inv.get("layout_count", 0) >= 1)
    pdf = ppu.inventory_pdf(out / "presentation.pdf")
    ok("pdf inventory pages=1", pdf.get("pages") == 1, str(pdf))

    # 逐字 quote 命中检查
    ok("quote verbatim hit", ppu.quote_hits(inp / "paper.md", "exact demo quote") > 0)


def check_skeleton(tmp: Path):
    """前 3 分钟骨架：只跑 --skeleton-only，也要有全字段 + partial。"""
    inp = tmp / "skel" / "input"
    out = tmp / "skel" / "output"
    inp.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = {"case_id": "skel-case", "output_contract": ["case_id", "editable_source", "export_artifact"]}
    (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    r = run([sys.executable, str(MAIN),
             "--input", str(inp), "--output", str(out), "--skeleton-only"])
    ok("skeleton-only exit 0", r.returncode == 0)
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    ok("skeleton has status=partial", res.get("status") == "partial")
    ok("skeleton has all contract fields",
       all(k in res for k in ("case_id", "editable_source", "export_artifact")))
    ok("skeleton has change_log", "change_log" in res)


def check_degradation(tmp: Path):
    # 空目录：无 case.json → input_error
    empty = tmp / "empty" / "input"
    empty_out = tmp / "empty" / "output"
    empty.mkdir(parents=True, exist_ok=True)
    empty_out.mkdir(parents=True, exist_ok=True)
    r = run([sys.executable, str(MAIN),
             "--input", str(empty), "--output", str(empty_out)])
    ok("empty input exit 0", r.returncode == 0)
    res = json.loads((empty_out / "result.json").read_text(encoding="utf-8"))
    ok("empty input -> input_error", res.get("status") == "input_error",
       f"got {res.get('status')}")
    ok("empty input case_id=unknown-case", res.get("case_id") == "unknown-case")

    # 坏 JSON → input_error
    bad = tmp / "bad" / "input"
    bad_out = tmp / "bad" / "output"
    bad.mkdir(parents=True, exist_ok=True)
    bad_out.mkdir(parents=True, exist_ok=True)
    (bad / "case.json").write_text("{not valid json", encoding="utf-8")
    r = run([sys.executable, str(MAIN),
             "--input", str(bad), "--output", str(bad_out)])
    ok("bad json exit 0", r.returncode == 0)
    res2 = json.loads((bad_out / "result.json").read_text(encoding="utf-8"))
    ok("bad json -> input_error", res2.get("status") == "input_error")


def main():
    print(f"== slides-01 selftest (pkg={PKG_ROOT}) ==")
    check_frontmatter()
    check_scripts_runnable()
    with tempfile.TemporaryDirectory(prefix="slides01_selftest_") as td:
        tmp = Path(td)
        check_contract_build(tmp)
        check_skeleton(tmp)
        check_degradation(tmp)
    print()
    if FAIL:
        print(f"FAILED: {len(FAIL)} -> {FAIL}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
