"""End-to-end self-test for the citation-management skill.

Runs the deterministic pipeline against an explicitly synthetic, clearly
labelled fixture (NOT a formal test case), then asserts the output contract
is complete: result.json exists, case_id is filled, every output_contract key
is echoed, contract_alignment is populated, and all four artifacts exist.
Exit code 0 means pass; anything else prints the failing assertion.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

CASE_JSON = {
    "case_id": "synthetic-selftest-case",
    "output_contract": {
        "manuscript": "string",
        "references": "string",
        "evidence_map": "string",
        "consistency_report": "string",
        "notes": "",
        "tags": {"type": "array"},
        "score": {"type": "number"},
    },
}

TASK_MD = (
    "# 合成自检任务（非正式案例）\n\n"
    "请对稿件插入引用并重排编号，交付可编辑稿件与同步文献表。\n"
)

MANUSCRIPT_MD = (
    "# 结果\n\n"
    "第一句需要来源支撑[1]。第二句引用同一证据[1,2]。第三句引用范围[3-4]。\n"
)

LIBRARY_BIB = """@article{alpha,
  title={RNA structure prediction},
  author={Smith, John and Li, Wei},
  year={2020},
  journal={Nat Methods},
  volume={17},
  pages={1-10},
  doi={10.1000/alpha}
}
@article{beta,
  title={RNA structure prediction},
  author={Smith, J and Li, W},
  year={2020},
  journal={Nat Methods},
  volume={17},
  pages={1-10},
  doi={10.1000/alpha}
}
@article{gamma,
  title={Protein folding dynamics},
  author={Chen, Ming},
  year={2019},
  journal={Science},
  volume={365},
  pages={12-20}
}
"""


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _write_fixture(root: Path) -> None:
    inp = root / "input"
    out = root / "output"
    work = root / "work"
    inp.mkdir(parents=True)
    out.mkdir(parents=True)
    work.mkdir(parents=True)
    (inp / "case.json").write_text(json.dumps(CASE_JSON, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text(TASK_MD, encoding="utf-8")
    (inp / "materials").mkdir()
    (inp / "materials" / "manuscript.md").write_text(MANUSCRIPT_MD, encoding="utf-8")
    (inp / "materials" / "library.bib").write_text(LIBRARY_BIB, encoding="utf-8")


def main() -> int:
    # 1. CLI help / self-test exit codes.
    help_rc = subprocess.run(
        [sys.executable, "-B", str(SCRIPT_DIR / "main.py"), "--help"],
        capture_output=True, text=True, cwd=str(SCRIPT_DIR),
    ).returncode
    _check(help_rc == 0, f"main.py --help exit {help_rc}")

    selftest_rc = subprocess.run(
        [sys.executable, "-B", str(SCRIPT_DIR / "main.py"), "--self-test"],
        capture_output=True, text=True, cwd=str(SCRIPT_DIR),
    ).returncode
    _check(selftest_rc == 0, f"main.py --self-test exit {selftest_rc}")

    # 2. Full pipeline on a synthetic fixture.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_fixture(root)
        rc = subprocess.run(
            [
                sys.executable, "-B", str(SCRIPT_DIR / "main.py"),
                "--input", str(root / "input"),
                "--output", str(root / "output"),
                "--work", str(root / "work"),
                "--style", "gbt7714",
            ],
            capture_output=True, text=True, cwd=str(SCRIPT_DIR),
        ).returncode
        _check(rc == 0, f"pipeline exit {rc}")

        out = root / "output"
        result_path = out / "result.json"
        _check(result_path.exists(), "result.json missing")
        result = json.loads(result_path.read_text(encoding="utf-8"))

        _check(result.get("case_id") == "synthetic-selftest-case", "case_id not echoed")
        _check(result.get("status") in {"ok", "partial", "insufficient_evidence", "input_error"}, "invalid status")

        # Contract echo completeness.
        contract = CASE_JSON["output_contract"]
        for key in contract:
            _check(key in result, f"contract key '{key}' not echoed in result.json")
        align = result.get("contract_alignment", {})
        _check(align.get("_summary", {}).get("total") == len(contract), "contract_alignment total wrong")
        _check(align["_summary"]["filled"] >= 4, "artifact fields should be filled")
        _check(isinstance(align["_summary"]["empty_fields"], list), "empty_fields not a list")
        _check(align["fields"]["tags"]["value"] == [], "array default not []")

        # Artifacts.
        for f in ("manuscript.md", "references.md", "evidence_map.json", "consistency_report.json"):
            _check((out / f).exists(), f"artifact {f} missing")
        _check("manuscript" in result["artifacts"], "artifacts map missing manuscript")

        # Idempotence: a second run leaves identical fields except runtime.
        first = json.loads((out / "result.json").read_text(encoding="utf-8"))
        rc2 = subprocess.run(
            [
                sys.executable, "-B", str(SCRIPT_DIR / "main.py"),
                "--input", str(root / "input"),
                "--output", str(root / "output"),
                "--work", str(root / "work"),
            ],
            capture_output=True, text=True, cwd=str(SCRIPT_DIR),
        ).returncode
        _check(rc2 == 0, f"second pipeline run exit {rc2}")
        second = json.loads((out / "result.json").read_text(encoding="utf-8"))
        for k in ("case_id", "status", "sources", "uncertainty", "errors", "degradations", "artifacts"):
            _check(first.get(k) == second.get(k), f"non-deterministic field: {k}")

        # Deterministic dedup: 3 library entries collapse to 2 (two DOIs).
        report = json.loads((out / "consistency_report.json").read_text(encoding="utf-8"))
        _check(report["library_entries"] == 2, f"expected 2 kept entries, got {report['library_entries']}")

    print("selftest ok: engine + CLI + contract echo + artifacts + idempotence all passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"SELFTEST FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
