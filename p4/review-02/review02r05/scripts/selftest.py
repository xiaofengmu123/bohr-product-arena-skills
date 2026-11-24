# -*- coding: utf-8 -*-
"""selftest.py — self-test for citation-evidence-audit.

Verifies the runtime contract without touching the real platform cases:

1.  --help exits 0
2.  a synthetic case with a txt material and an output_contract produces a
    parseable result.json with every contract field echoed at top level
3.  the emitted result passes `main.py check`
4.  repeated finalize is idempotent (same findings count, same change_log size)
5.  an empty input directory degrades honestly to `insufficient_evidence`
6.  a broken result makes `check` exit non-zero

Only the standard library is used. No network, no randomness, no benchmark
scores are invented. Run from the package root:

    python -B scripts/selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"

PASS = 0
FAIL = 0


def record(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def run_py(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(MAIN), *args],
        capture_output=True, text=True, timeout=120,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    # 1. --help
    cp = run_py("--help")
    record("--help exits 0", cp.returncode == 0, cp.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp = root / "input"
        out = root / "output"
        work = root / "work"

        # 2. synthetic case with output_contract + txt material
        write(inp / "case.json", json.dumps({
            "case_id": "selftest-case-1",
            "task": "audit the attached material",
            "output_contract": {
                "required_evidence": ["locator", "quote", "basis"],
                "language": "zh",
                "checklist_categories": [],
            },
        }, ensure_ascii=False))
        write(inp / "task.md", "# 审查任务\n对附件材料完成引用与事实核验。\n")
        write(inp / "materials" / "paper.txt",
              "Background: This claim is unsupported.\n\n"
              "Methods: N = 42 participants.\n\n"
              "Results: The treatment group improved (p < 0.05).\n")
        cp = run_py("run", "--input", str(inp), "--output", str(out), "--work", str(work))
        record("run exits 0 on synthetic case", cp.returncode == 0, cp.stderr)

        result_path = out / "result.json"
        result = {}
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            parse_ok = True
        except Exception as exc:  # noqa: BLE001
            parse_ok = False
            result = {}
            detail = str(exc)
        record("result.json is valid JSON", parse_ok, "parse error" if not parse_ok else "")

        required = ["case_id", "status", "summary", "review_report", "evidence",
                    "sources", "uncertainty", "pending_human", "limitations",
                    "errors", "degradations", "change_log", "runtime"]
        missing = [k for k in required if k not in result]
        record("all top-level contract fields present", not missing, str(missing))

        echoed = [k for k in ("required_evidence", "language", "checklist_categories") if k in result]
        record("output_contract keys echoed at top level", len(echoed) == 3, str(echoed))
        record("case_id preserved", result.get("case_id") == "selftest-case-1")

        cp_check = run_py("check", "--input", str(inp), "--output", str(out), "--work", str(work))
        record("check passes emitted result", cp_check.returncode == 0, cp_check.stdout + cp_check.stderr)

        # 4. idempotence
        cp = run_py("finalize", "--input", str(inp), "--output", str(out), "--work", str(work))
        result2 = json.loads(result_path.read_text(encoding="utf-8"))
        same = (
            len(result2["review_report"]["findings"]) == len(result["review_report"]["findings"])
            and len(result2["change_log"]) == len(result["change_log"])
            and result2["summary"]["verdict"] == result["summary"]["verdict"]
        )
        record("repeated finalize is idempotent", cp.returncode == 0 and same, "content drifted")

        # 5. empty input degrades honestly
        empty_in = root / "empty_input"
        empty_in.mkdir(parents=True, exist_ok=True)
        empty_out = root / "empty_output"
        empty_work = root / "empty_work"
        cp = run_py("run", "--input", str(empty_in), "--output", str(empty_out), "--work", str(empty_work))
        empty_result = json.loads((empty_out / "result.json").read_text(encoding="utf-8"))
        record("empty input -> insufficient_evidence",
               empty_result.get("status") == "insufficient_evidence"
               and empty_result.get("summary", {}).get("verdict") == "insufficient",
               empty_result.get("status", ""))
        record("empty input run exits 0", cp.returncode == 0, cp.stderr)

        # 6. a broken result makes check exit non-zero
        broken_out = root / "broken_output"
        broken_out.mkdir(parents=True, exist_ok=True)
        write(broken_out / "result.json", json.dumps({"case_id": "x"}, ensure_ascii=False))
        cp = run_py("check", "--input", str(inp), "--output", str(broken_out), "--work", str(work))
        record("check rejects broken result", cp.returncode != 0, f"exit={cp.returncode}")

    print(f"\nselftest summary: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
