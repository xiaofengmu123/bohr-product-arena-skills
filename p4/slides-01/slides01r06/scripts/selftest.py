#!/usr/bin/env python3
"""Self-test for the slides-01 skill package.

Builds a small synthetic input (explicitly marked as such) in a temporary
directory and exercises scaffold -> discover -> validate -> build, plus the
degradation paths (empty input, malformed case.json). Exits non-zero on any
failed assertion. Uses only the standard library.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main as runner  # noqa: E402
import pptxlib  # noqa: E402

CASE = {
    "case_id": "selftest-case",
    "output_contract": {
        "editable_source": {"format": "pptx"},
        "export_artifact": {"pdf": {}},
        "content_map": [],
        "compliance_checks": [],
        "exceptions": [],
    },
}


def run(*steps, input_dir: Path, output_dir: Path, work_dir: Path) -> int:
    return runner.main(["--input", str(input_dir), "--output", str(output_dir),
                        "--work", str(work_dir), "--step", ",".join(steps)])


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="slides01_selftest_") as td:
        base = Path(td)
        inp, out, work = base / "input", base / "output", base / "work"
        inp.mkdir(parents=True)
        out.mkdir(parents=True)
        work.mkdir(parents=True)
        (inp / "case.json").write_text(json.dumps(CASE, ensure_ascii=False), encoding="utf-8")
        (inp / "task.md").write_text("# selftest task\n这是自检合成材料。\n", encoding="utf-8")
        (inp / "paper.pdf").write_bytes(b"%PDF-1.4 fake content for discovery only")

        plan = {
            "case_id": "selftest-case", "title": "Synthetic self-test talk",
            "audience": "colleagues", "minutes": 8, "slide_limit": 3,
            "narrative": "问题 → 方法 → 结果",
            "pages": [
                {"id": "p1", "role": "title", "claim": "本文研究问题 X。",
                 "bullet_points": ["背景一句话", "贡献一句话"],
                 "evidence": [{"type": "text", "locator": "paper.pdf#p1",
                               "quote": "for discovery only"}],
                 "notes": "开场介绍背景，不读页面文字。", "footer": "Selftest"},
                {"id": "p2", "role": "method", "claim": "方法为 M。",
                 "bullet_points": ["方法步骤", "关键参数"],
                 "evidence": [{"type": "figure", "label": "Fig. 1",
                               "locator": "paper.pdf#p2", "quote": "for discovery only"}],
                 "notes": "强调方法与基线差异。", "footer": "Selftest"},
                {"id": "p3", "role": "result", "claim": "结果显示 R。",
                 "bullet_points": ["主结果数值", "单位与误差"],
                 "evidence": [{"type": "figure", "label": "Fig. 2",
                               "locator": "paper.pdf#p3", "quote": "for discovery only"}],
                 "notes": "读数时注意单位。", "footer": "Selftest"},
            ],
        }
        figures = {
            "figures": [
                {"label": "Fig. 1", "kind": "data", "used": True,
                 "caption": "示意", "locator": "paper.pdf#p2", "quote": "for discovery only"},
                {"label": "Fig. 2", "kind": "data", "used": True,
                 "caption": "示意", "locator": "paper.pdf#p3", "quote": "for discovery only"},
            ],
            "tables": [], "equations": [],
        }
        (work / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        (work / "figure_model.json").write_text(
            json.dumps(figures, ensure_ascii=False), encoding="utf-8")

        rc = run("scaffold", "discover", "validate", "build",
                 input_dir=inp, output_dir=out, work_dir=work)
        if rc != 0:
            failures.append(f"pipeline exit {rc}")

        result, err = runner.read_json(out / "result.json")
        if err:
            failures.append(f"result.json: {err}")
        else:
            if result.get("case_id") != "selftest-case":
                failures.append("case_id not echoed")
            if result.get("status") not in {"ok", "partial"}:
                failures.append(f"unexpected status {result.get('status')}")
            if not result.get("change_log"):
                failures.append("change_log empty")

        pptx = out / "presentation.pptx"
        ok, msg = pptxlib.validate_pptx(pptx)
        if not ok:
            failures.append(f"pptx invalid: {msg}")
        else:
            with __import__("zipfile").ZipFile(pptx) as z:
                slides = [n for n in z.namelist() if re_match_slide(n)]
                if len(slides) != 3:
                    failures.append(f"expected 3 slide parts, got {len(slides)}")

    # Degradation: empty input dir -> still writes a legal result.json, exit 0.
    with tempfile.TemporaryDirectory(prefix="slides01_empty_") as td:
        base = Path(td)
        inp, out, work = base / "input", base / "output", base / "work"
        inp.mkdir(parents=True)
        rc = run("scaffold", input_dir=inp, output_dir=out, work_dir=work)
        result, err = runner.read_json(out / "result.json")
        if rc != 0:
            failures.append("empty-input exit != 0")
        if err or not isinstance(result, dict) or not result.get("case_id"):
            failures.append("empty-input result.json missing case_id")

    # Degradation: malformed case.json -> recorded, not a crash.
    with tempfile.TemporaryDirectory(prefix="slides01_bad_") as td:
        base = Path(td)
        inp, out, work = base / "input", base / "output", base / "work"
        inp.mkdir(parents=True)
        (inp / "case.json").write_text("{not valid json", encoding="utf-8")
        rc = run("scaffold", input_dir=inp, output_dir=out, work_dir=work)
        result, err = runner.read_json(out / "result.json")
        if rc != 0:
            failures.append("bad-json exit != 0")
        if result is None or result.get("case_id") != "unknown-case":
            failures.append("bad-json did not fall back to unknown-case")

    if failures:
        for f in failures:
            print(f"[FAIL] {f}", file=sys.stderr)
        print(f"selftest failed: {len(failures)} issue(s)")
        return 1
    print("selftest ok: scaffold/discover/validate/build + empty-input + bad-json all pass")
    return 0


def re_match_slide(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"ppt/slides/slide\d+\.xml", name))


if __name__ == "__main__":
    raise SystemExit(main())
