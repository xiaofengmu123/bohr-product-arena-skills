#!/usr/bin/env python3
"""Self-test: synthetic smoke test of the skill pipeline.

Creates a temp demo input, runs the real CLIs (main.py init/check/finalize and
consistency_check.py), then asserts the result.json is a legal, honest partial
result and that checks detect planted defects. Exits 0 on pass, 1 on failure.
Never touches formal cases; never fabricates benchmark scores.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def run(cmd, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd)


def step(name, cond):
    if not cond:
        print(f"FAIL {name}")
        return False
    print(f"ok   {name}")
    return True


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        out = root / "output"
        work = root / "work"
        (inp / "materials").mkdir(parents=True)

        case = {
            "id": "demo-case-1",
            "task": "write methods section",
            "output_contract": {
                "deliverable": "manuscript.md",
                "fact_source_map": {"format": "evidence_map.json"},
                "version_limits": {"max_words": 1200},
            },
        }
        (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False),
                                       encoding="utf-8")
        (inp / "task.md").write_text("# demo task\n", encoding="utf-8")
        (inp / "materials" / "note.md").write_text(
            "# notes\nn=42, p=0.03, [C:C1] effect reported.\n", encoding="utf-8")
        (inp / "materials" / "data.csv").write_text(
            "group,value\na,1\nb,2\n", encoding="utf-8")

        # 1) init
        r = run([PY, str(HERE / "main.py"), "--step", "init",
                 "--input", str(inp), "--output", str(out), "--work", str(work)])
        if not step("init exit 0", r.returncode == 0):
            print(r.stdout, r.stderr)
            return 1
        result_path = out / "result.json"
        if not step("result.json exists", result_path.is_file()):
            return 1
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not step("result has case_id + status + change_log",
                    result.get("case_id") == "demo-case-1"
                    and result.get("status") in ("partial", "in_progress")
                    and isinstance(result.get("change_log"), list)):
            return 1
        if not step("contract fields echoed",
                    result.get("deliverable") == "manuscript.md"
                    and "fact_source_map" in result
                    and "version_limits" in result):
            return 1

        # 2) write a draft with planted defects
        draft = out / "manuscript.md"
        draft.write_text(
            "# Abstract\n摘要内容。\n\n# Methods\n方法内容 [S:S99] 样本 n=42 [C:C2]。\n\n"
            "# Results\n结果见 Figure 3，未定义引用 [C:C9]。\n\n"
            "# Discussion\n讨论。\n\n# Conclusion\n结论。\n\n"
            "# References\n[C:C1] Author A. Title. J. 2020;1(1):1. DOI:10.1/x\n",
            encoding="utf-8")

        # 3) check
        r = run([PY, str(HERE / "consistency_check.py"), "--draft", str(draft),
                 "--index", str(work / "material_index.json"),
                 "--evidence", str(work / "evidence_map.json"),
                 "--out", str(work / "check_report.json")])
        if not step("check exit 0 (non-strict)", r.returncode == 0):
            print(r.stdout, r.stderr)
            return 1
        report = json.loads((work / "check_report.json").read_text(encoding="utf-8"))
        keys = {f.get("check") for f in report["findings"]}
        if not step("dangling source detected", "dangling_source" in keys):
            return 1
        if not step("undefined citation detected", "undefined_citation" in keys):
            return 1

        # 4) finalize
        r = run([PY, str(HERE / "main.py"), "--step", "finalize",
                 "--input", str(inp), "--output", str(out), "--work", str(work),
                 "--status", "partial"])
        if not step("finalize exit 0", r.returncode == 0):
            print(r.stdout, r.stderr)
            return 1
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not step("finalized status honest",
                    result.get("status") == "partial"
                    and result.get("deliverable") == "manuscript.md"
                    and "sources" in result and "uncertainty" in result
                    and "pending_human" in result):
            return 1

        # 5) empty input robustness
        empty = root / "empty_in"; empty.mkdir()
        empty_out = root / "empty_out"; empty_work = root / "empty_work"
        r = run([PY, str(HERE / "main.py"), "--step", "init",
                 "--input", str(empty), "--output", str(empty_out),
                 "--work", str(empty_work)])
        if not step("empty input exit 0", r.returncode == 0):
            return 1
        empty_result = json.loads((empty_out / "result.json").read_text(encoding="utf-8"))
        if not step("empty input -> insufficient_evidence",
                    empty_result.get("status") == "insufficient_evidence"):
            return 1

    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
