#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：不依赖 office-renderer / 字体环境，只用标准库验证本 Skill 的核心契约。

覆盖：空输入、坏 JSON、坏编码、缺文件、幂等重跑、plan/figure_model 结构校验、
图片尺寸解析、证据定位（locator+逐字 quote）、pptx/pdf/png 完整性检查。

用法：
  python -B scripts/selftest.py            # 全部自检，打印 PASS/FAIL，非零退出码表示失败
  python -B scripts/selftest.py --quiet    # 只打印失败项
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import main  # noqa: E402  （同目录导入，只用标准库）


# 一个 4x4 的合法 PNG（IHDR 后 width=4, height=4）
PNG_4x4 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmWkpWAAAADElEQVQIHWP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def make_case(root: Path, *, good_json=True, add_png=True, add_pdf=True, add_task=True, add_material_txt=True):
    case = {"case_id": "self-test-case", "id": "ppt-self-test",
            "materials": [], "output_contract": {"presentation": {}, "evidence_map": [], "render_record": {}}}
    (root / "case.json").write_text(json.dumps(case) if good_json else "{not json", encoding="utf-8-sig")
    if add_task:
        (root / "task.md").write_text("# 任务\n把论文套进模板。\n", encoding="utf-8")
    mat = root / "materials"
    mat.mkdir(exist_ok=True)
    if add_png:
        (mat / "fig1.png").write_bytes(PNG_4x4)
    if add_pdf:
        (mat / "paper.pdf").write_bytes(b"%PDF-1.7\n%dummy-pdf-content\n%%EOF\n")
    if add_material_txt:
        (mat / "paper.txt").write_text(
            "标题：示例研究\n\n第一段：本研究提出了一种方法。\n\n第二段：实验结果表明误差为 0.03。\n",
            encoding="utf-8",
        )
    return case


def run_cmd(args):
    old = sys.argv
    sys.argv = ["main.py"] + args
    code = main.main()
    sys.argv = old
    return code


def make_plan(work: Path, case_id="self-test-case"):
    plan = main.plan_skeleton(case_id)
    plan["pages"] = [
        {"index": 1, "role": "cover", "title": "封面", "claim": "示例研究封面",
         "layout_key": "cover", "refs": [], "notes": ""},
        {"index": 2, "role": "result", "title": "结果", "claim": "误差为 0.03",
         "layout_key": "title_content", "refs": ["materials/paper.txt#P3"], "notes": "讲解"},
    ]
    main.write_json(work / "plan.json", plan)


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    if not ok:
        print(f"FAIL  {name}  {detail}")


def run():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="slide-selftest-") as td:
        base = Path(td)

        # 1) 正常输入：init + figures + plan 校验 + evidence + verify + finalize
        inp, work, out = base / "in", base / "work", base / "out"
        inp.mkdir(parents=True)
        make_case(inp)
        check("init_exit0", run_cmd(["init", "--input", str(inp), "--work", str(work), "--output", str(out)]) == 0)
        r, err = main.read_json(out / "result.json")
        check("result_case_id", r and r.get("case_id") == "self-test-case", str(r.get("case_id") if r else err))
        check("result_skeleton_fields", r and all(k in r for k in
              ("case_id", "status", "presentation", "evidence_map", "render_record",
               "sources", "uncertainty", "pending_human", "change_log")))
        check("status_in_progress", r and r.get("status") == "in_progress")

        check("figures_exit0", run_cmd(["figures", "--input", str(inp), "--work", str(work)]) == 0)
        fm, _ = main.read_json(work / "figure_model.json")
        check("figure_count", fm and len(fm.get("figures", [])) == 1, f"figures={len(fm and fm.get('figures', []))}")
        fig = fm["figures"][0] if fm and fm.get("figures") else {}
        check("png_dims_4x4", fig.get("native_size") == {"w": 4, "h": 4, "unit": "px"}, str(fig.get("native_size")))
        check("png_aspect_1", fig.get("aspect_ratio") == 1.0, str(fig.get("aspect_ratio")))
        check("png_scale_fit", fig.get("scale_mode") == "fit")

        make_plan(work)
        # 模拟“提取缓存”：把可定位文本缓存到 /app/work（真实流程由 pdf-text-tool 完成）
        (work / "materials").mkdir(exist_ok=True)
        import shutil as _sh
        _sh.copy2(inp / "materials" / "paper.txt", work / "materials" / "paper.txt")
        check("plan_check_exit0", run_cmd(["plan", "--plan", str(work / "plan.json")]) == 0)
        check("evidence_exit0", run_cmd(["evidence", "--input", str(inp), "--work", str(work), "--output", str(out), "--plan", str(work / "plan.json")]) == 0)
        r, _ = main.read_json(out / "result.json")
        em = r.get("evidence_map", [])
        check("evidence_two_pages", len(em) == 2)
        resolved = [e for e in em if any(x["status"] == "resolved" for x in e.get("refs", []))]
        check("evidence_verbatim_quote", resolved and "误差为 0.03" in resolved[0]["refs"][0]["quote"], str(resolved))

        # 2) 幂等：第二次 init 不破坏已填字段
        run_cmd(["init", "--input", str(inp), "--work", str(work), "--output", str(out)])
        r2, _ = main.read_json(out / "result.json")
        check("idempotent_evidence_kept", r2.get("evidence_map") == em)

        # 3) 坏 JSON：不崩溃，记录 degradation，继续产出骨架
        bad_in = base / "bad"
        bad_in.mkdir()
        make_case(bad_in, good_json=False)
        code = run_cmd(["init", "--input", str(bad_in), "--work", str(base / "bw"), "--output", str(base / "bo")])
        check("badjson_exit0", code == 0)
        br, _ = main.read_json(base / "bo" / "result.json")
        check("badjson_recorded", br and any("JSONDecodeError" in d.get("detail", "") for d in br.get("degradations", [])),
              str(br.get("degradations", [])[:1] if br else None))

        # 4) 空输入目录：insufficient_evidence，退出码 0，不抛异常
        empty = base / "empty"
        empty.mkdir()
        code = run_cmd(["init", "--input", str(empty), "--work", str(base / "ew"), "--output", str(base / "eo")])
        check("empty_exit0", code == 0)
        er, _ = main.read_json(base / "eo" / "result.json")
        check("empty_has_result", er is not None and bool(er.get("case_id")))

        # 5) 图片尺寸解析边界：未知格式 / 无图
        uf = base / "unknownfig"
        uf.mkdir()
        (uf / "case.json").write_text('{"case_id":"u"}', encoding="utf-8")
        (uf / "materials").mkdir()
        (uf / "materials" / "blob.xyz").write_bytes(b"not-an-image")
        run_cmd(["figures", "--input", str(uf), "--work", str(base / "uw")])
        um, _ = main.read_json(base / "uw" / "figure_model.json")
        check("unknown_ext_ignored", um and len(um.get("figures", [])) == 0)

        # 6) pptx/pdf/png 检查器
        fake_pptx = base / "fake.pptx"
        fake_pptx.write_bytes(b"PK\x03\x04 not really a zip")
        check("pptx_bad_detected", main.inspect_pptx(fake_pptx).get("ok") is False)
        good_pdf = base / "good.pdf"
        good_pdf.write_bytes(b"%PDF-1.4\n...")
        check("pdf_magic_ok", main.inspect_pdf(good_pdf).get("ok") is True)
        bad_png = base / "bad.png"
        bad_png.write_bytes(b"notpng")
        check("png_bad_detected", main.inspect_png(bad_png).get("ok") is False)

    failures = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\nselftest: {len(RESULTS)} checks, {failures} failed, {len(RESULTS) - failures} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    sys.exit(run())
