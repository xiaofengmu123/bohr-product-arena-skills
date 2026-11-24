#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the academic compliance audit skill.

Subcommands
-----------
validate   check /app/output/result.json against the contract in
           /input/case.json and verify every finding quote against the
           evidence index. Prints PASS/FAIL lines; exit 0 iff no FAIL.
selftest   run empty-input, bad-JSON and bad-encoding scenarios in a temp dir
           and assert a parseable result.json is always produced.

Standard library only. Relative import of main.py / extract.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import extract
import main as M


def _load(path: Path):
    if not path.exists():
        return None, f"missing: {path}"
    obj, err = extract.read_json_robust(path)
    if err:
        return None, err
    return obj, None


def _flatten_keys(contract):
    keys = set()
    if isinstance(contract, dict):
        for k, v in contract.items():
            keys.add(k)
            if isinstance(v, dict):
                for sub in _flatten_keys(v):
                    keys.add(k + "." + sub)
    elif isinstance(contract, (list, type(None))):
        pass
    return keys


def validate(inp: Path, out: Path, work: Path):
    failures, warnings = [], []

    result, err = _load(out / "result.json")
    if err:
        failures.append(f"result.json {err}")
        return failures, warnings

    if not result.get("case_id"):
        failures.append("case_id 为空")

    if result.get("status") not in M.ALLOWED_STATUS:
        failures.append(f"status={result.get('status')} 不在允许集合")

    for k in ("sources", "uncertainty", "change_log"):
        if not isinstance(result.get(k), list):
            failures.append(f"{k} 缺失或非数组")
        elif not result[k]:
            warnings.append(f"{k} 为空数组")

    if not isinstance(result.get("errors"), list) or not isinstance(result.get("degradations"), list):
        failures.append("errors/degradations 缺失或非数组")

    # contract echo
    case, err = _load(inp / "case.json")
    if err:
        warnings.append(f"case.json 不可读: {err}")
    elif isinstance(case, dict) and isinstance(case.get("output_contract"), dict):
        contract_keys = _flatten_keys(case["output_contract"])
        have = set()
        for k, v in result.items():
            have.add(k)
        for k, v in (result.get("output_contract") or {}).items():
            have.add(k)
            if isinstance(v, dict):
                for sub in _flatten_keys(v):
                    have.add(k + "." + sub)
        missing = sorted(k for k in contract_keys if k not in have)
        if missing:
            failures.append("output_contract 缺字段: " + ", ".join(missing))

    # findings schema + quote verification
    evidence, err = _load(work / "evidence.json")
    parsed = (evidence or {}).get("parsed_map") or {}
    findings = ((result.get("report") or {}).get("findings")) or []
    if not isinstance(findings, list):
        failures.append("report.findings 非数组")
    else:
        for f in findings:
            fid = f.get("id", "?")
            missing = [k for k in M.FINDING_REQUIRED if not f.get(k)]
            if missing:
                failures.append(f"finding {fid} 缺字段 {missing}")
            if f.get("type") not in M.FINDING_TYPES:
                failures.append(f"finding {fid} type={f.get('type')} 非法")
            if f.get("severity") not in M.SEVERITIES:
                failures.append(f"finding {fid} severity={f.get('severity')} 非法")
            text = ""
            fpath = str(f.get("file") or "")
            if fpath in parsed:
                text = parsed[fpath]
            else:
                stem = Path(fpath).stem.lower()
                for p, t in parsed.items():
                    if Path(p).stem.lower() == stem:
                        text = t
                        break
            found, ratio, mode = extract.verify_quote(text, f.get("quote") or "")
            if not found:
                failures.append(f"finding {fid} quote 无法回查原文 (mode={mode}, ratio={ratio:.2f})")

    if not (out / "report.md").exists():
        failures.append("report.md 缺失")

    if not result.get("report_path"):
        warnings.append("result.json 未记录 report_path（非致命）")

    return failures, warnings


def _write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def selftest():
    results = []
    base = Path(tempfile.mkdtemp(prefix="audit-selftest-"))

    # 1) empty input dir
    d = base / "empty"
    (d / "input").mkdir(parents=True)
    (d / "output").mkdir()
    (d / "work").mkdir()
    rc = M.main(["run", "--input", str(d / "input"), "--output", str(d / "output"), "--work", str(d / "work")])
    res, err = _load(d / "output" / "result.json")
    ok = rc == 0 and err is None and res.get("status") == "insufficient_evidence"
    results.append(("empty-input", ok, res.get("status") if res else err))

    # 2) bad JSON case.json
    d = base / "badjson"
    (d / "input").mkdir(parents=True)
    (d / "output").mkdir()
    (d / "work").mkdir()
    _write(d / "input" / "case.json", b"{not valid json")
    _write(d / "input" / "task.md", "# task\n".encode("utf-8"))
    _write(d / "input" / "paper.txt", "Results: n = 30, p < 0.05.\n".encode("utf-8"))
    rc = M.main(["run", "--input", str(d / "input"), "--output", str(d / "output"), "--work", str(d / "work")])
    res, err = _load(d / "output" / "result.json")
    ok = rc == 0 and err is None and res.get("status") in ("partial", "input_error")
    results.append(("bad-json", ok, res.get("status") if res else err))

    # 3) bad encoding material
    d = base / "badenc"
    (d / "input").mkdir(parents=True)
    (d / "output").mkdir()
    (d / "work").mkdir()
    _write(d / "input" / "case.json", b'{"case_id":"t1","output_contract":{"summary":"","findings":[]}}')
    _write(d / "input" / "task.md", "# task\n".encode("utf-8"))
    _write(d / "input" / "paper.txt", b"Results: n = 30, p < 0.05.\xff\xfe\x81\n")
    rc = M.main(["run", "--input", str(d / "input"), "--output", str(d / "output"), "--work", str(d / "work")])
    res, err = _load(d / "output" / "result.json")
    ok = rc == 0 and err is None and res.get("status") in ("partial", "ok")
    results.append(("bad-encoding", ok, res.get("status") if res else err))

    # 4) full happy-path with a finding merge
    d = base / "happy"
    (d / "input").mkdir(parents=True)
    (d / "output").mkdir()
    (d / "work").mkdir()
    _write(d / "input" / "case.json", b'{"case_id":"t2","output_contract":{"summary":"","findings":[]}}')
    _write(d / "input" / "task.md", "# task\n".encode("utf-8"))
    _write(d / "input" / "paper.txt", "Results: n = 30, p < 0.05. Patients consented.\n".encode("utf-8"))
    M.main(["run", "--input", str(d / "input"), "--output", str(d / "output"), "--work", str(d / "work")])
    findings = [{
        "id": "F-0001", "type": "suggestion", "severity": "minor", "category": "statistics",
        "title": "p 值未给效应量", "file": str(d / "input" / "paper.txt"), "locator": "paper.txt#L1",
        "quote": "n = 30, p < 0.05", "evidence": "正文仅报告 p 值", "basis": "统计报告规范要求效应量与置信区间",
        "revision": "补充效应量与 95% CI",
    }]
    _write(d / "work" / "findings.json", json.dumps(findings).encode("utf-8"))
    rc = M.main(["merge", "--input", str(d / "input"), "--output", str(d / "output"),
                 "--work", str(d / "work"), "--findings", str(d / "work" / "findings.json"),
                 "--status", "ok"])
    fails, warns = validate(d / "input", d / "output", d / "work")
    ok = rc == 0 and not fails
    results.append(("happy-path", ok, "findings=%d fails=%d" % (
        len(((_load(d / "output" / "result.json")[0] or {}).get("report") or {}).get("findings") or []), len(fails))))

    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit skill self-check")
    parser.add_argument("--input", default=str(M.DEFAULT_INPUT))
    parser.add_argument("--output", default=str(M.DEFAULT_OUTPUT))
    parser.add_argument("--work", default=str(M.DEFAULT_WORK))
    sub = parser.add_subparsers(dest="cmd")
    p_v = sub.add_parser("validate", help="validate result.json against contract and evidence")
    p_v.add_argument("--input", default=str(M.DEFAULT_INPUT))
    p_v.add_argument("--output", default=str(M.DEFAULT_OUTPUT))
    p_v.add_argument("--work", default=str(M.DEFAULT_WORK))
    sub.add_parser("selftest", help="run robustness scenarios")
    args = parser.parse_args(argv)

    if args.cmd == "selftest":
        code = 0
        for name, ok, detail in selftest():
            print(("PASS" if ok else "FAIL"), name, "-", detail)
            if not ok:
                code = 1
        return code

    failures, warnings = validate(Path(args.input), Path(args.output), Path(args.work))
    for w in warnings:
        print("WARN", w)
    for f in failures:
        print("FAIL", f)
    print("validate:", "PASS" if not failures else "FAIL",
          f"({len(failures)} fail, {len(warnings)} warn)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
