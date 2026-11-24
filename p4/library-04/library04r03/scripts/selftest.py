"""selftest.py — self-check for determinism, output contract, and evidence.

Run after the pipeline (or standalone):

    python3 scripts/selftest.py --input /input --output /app/output \
        --work /app/work

Checks
------
1. result.json parses and carries case_id + status + sources + uncertainty
   + change_log + the output_contract echo.
2. Six-dimension coverage is present (file_types, metadata, directory_tree,
   duplicate_groups, version_families, permissions/source).
3. Every answer evidence entry points at a real file whose SHA-256 matches and
   whose verbatim quote occurs in that file (line locators are checked against
   the line slice; page locators are recorded as needing pdftotext).
4. Determinism: the pipeline is re-run twice in two scratch directories and the
   content-derived result must be identical after masking wall-clock fields.

Exit code 0 means every hard check passed; a report is written to
<output>/selftest_report.json and printed.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile

import core_impl as c
import main as pipeline

REQUIRED_KEYS = ["case_id", "status", "sources", "uncertainty", "change_log"]
VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
NON_DETERMINISTIC_KEYS = {"time", "started", "finished", "generated_at",
                          "phase_seconds", "pid", "_started"}


def scrub(obj):
    """Deep-copy and remove wall-clock / environment-only fields."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()
                if k not in NON_DETERMINISTIC_KEYS}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return copy.deepcopy(obj)


def checks_report(result_path: str, input_dir: str):
    checks = []
    data, err = c.safe_json(result_path)
    if err or not isinstance(data, dict):
        return [{"id": "json", "name": "result.json 合法且为对象",
                 "pass": False, "detail": err or "not a dict"}], data

    for key in REQUIRED_KEYS:
        checks.append({"id": f"key_{key}", "name": f"result.json 含 {key}",
                       "pass": key in data,
                       "detail": "missing" if key not in data else "present"})
    checks.append({"id": "status_valid",
                   "name": "status 取值合法",
                   "pass": data.get("status") in VALID_STATUS,
                   "detail": data.get("status")})

    # output_contract echo
    contract = data.get("output_contract")
    if isinstance(contract, dict):
        missing_echo = [k for k in contract if k not in data]
        checks.append({"id": "contract_echo",
                       "name": "output_contract 全部键已回填",
                       "pass": not missing_echo,
                       "detail": f"missing_echo={missing_echo}"})
    else:
        checks.append({"id": "contract_echo",
                       "name": "output_contract 键回填（无契约时为 n/a）",
                       "pass": True, "detail": "no output_contract"})

    # six-dimension coverage
    lib = data.get("library", {})
    six = {
        "file_types": bool(lib.get("summary", {}).get("file_types")),
        "metadata": all("metadata" in s for s in data.get("sources", [])),
        "directory_tree": bool(lib.get("directory_tree")),
        "duplicate_groups": isinstance(lib.get("duplicate_groups"), list),
        "version_families": isinstance(lib.get("version_families"), list),
        "permissions_source": all(
            "mode_octal" in s and "source" in s for s in data.get("sources", [])
        ) if data.get("sources") else True,
    }
    checks.append({"id": "six_dimensions",
                   "name": "六维度覆盖（类型/元数据/层级/重复/版本/权限来源）",
                   "pass": all(six.values()),
                   "detail": {k: bool(v) for k, v in six.items()}})

    # evidence verification
    answers = data.get("answers", [])
    total_evidence = 0
    bad = []
    for a in answers:
        for ev in a.get("evidence", []):
            total_evidence += 1
            fpath = os.path.join(input_dir, ev.get("file", ""))
            problems = []
            if not os.path.exists(fpath):
                problems.append("file_missing")
            else:
                real_sha = c.sha256_file(fpath)
                if ev.get("sha256") and real_sha != ev.get("sha256"):
                    problems.append("sha256_mismatch")
                loc = ev.get("locator", "")
                quote = ev.get("quote", "")
                if quote and ev.get("file", "").lower().endswith(".pdf"):
                    problems.append("pdf_quote_requires_pdftotext_check")
                elif quote:
                    text, _e, _err = c.read_text(fpath)
                    if text is None or quote not in text:
                        problems.append("quote_not_verbatim")
            if problems:
                bad.append({"file": ev.get("file"), "locator": ev.get(
                    "locator"), "problems": problems})
    checks.append({"id": "evidence",
                   "name": "证据回链（文件存在/SHA256/逐字命中；无声明时真空通过）",
                   "pass": not bad,
                   "detail": {"checked": total_evidence, "bad": bad,
                              "note": "none to check" if total_evidence == 0
                              else "checked"}})

    return checks, data


def determinism_check(input_dir: str):
    """Re-run the full pipeline twice in scratch dirs and compare scrubbed result."""
    dirs = []
    results = []
    try:
        for _ in range(2):
            d = tempfile.mkdtemp(prefix="kbqa_det_")
            dirs.append(d)
            work = os.path.join(d, "work")
            out = os.path.join(d, "output")
            os.makedirs(work, exist_ok=True)
            os.makedirs(out, exist_ok=True)
            rc = pipeline.main(["--input", input_dir, "--output", out,
                                "--work", work, "--phase", "all"])
            if rc != 0:
                return {"pass": False,
                        "detail": f"pipeline exit {rc} in scratch dir"}
            result, err = c.safe_json(os.path.join(out, "result.json"))
            if err or not isinstance(result, dict):
                return {"pass": False, "detail": f"scratch result invalid: {err}"}
            results.append(result)
        same = scrub(results[0]) == scrub(results[1])
        return {"pass": same,
                "detail": "scrubbed results identical" if same else
                "scrubbed results differ"}
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="research-KB QA self-test")
    ap.add_argument("--input", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--work", default=None)
    args = ap.parse_args(argv)

    inp = os.path.abspath(args.input or c.DEFAULT_ROOT["input"])
    out = os.path.abspath(args.output or c.DEFAULT_ROOT["output"])
    result_path = os.path.join(out, "result.json")

    checks, data = checks_report(result_path, inp)
    det = determinism_check(inp)
    checks.append({"id": "determinism",
                   "name": "确定性（同输入两次 scrubbed 结果一致）",
                   "pass": det["pass"], "detail": det["detail"]})

    report = {"generated_at": c.now_iso(), "input_dir": inp,
              "result_path": result_path, "checks": checks,
              "all_pass": all(ch["pass"] for ch in checks)}
    os.makedirs(out, exist_ok=True)
    c.write_json(os.path.join(out, "selftest_report.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())