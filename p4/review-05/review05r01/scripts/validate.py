#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate.py — self-check and end-to-end selftest for the audit skill.

Modes
-----
validate  (default) check result.json against the runtime contract: parseable
          JSON, non-empty case_id, honest status, all output_contract keys,
          every finding field complete, every verbatim quote findable in the
          extracted material text.
selftest  build a temporary demo input, run the full pipeline (run -> merge ->
          validate) and assert the outputs. Proves the scripts actually execute
          on the current Python and are deterministic.

Standard library only. Imports main.py and extract.py from the same directory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract  # noqa: E402
import main as orchestrator  # noqa: E402

ALLOWED_STATUS = orchestrator.ALLOWED_STATUS
FINDING_TYPES = orchestrator.FINDING_TYPES
SEVERITIES = orchestrator.SEVERITIES
FINDING_REQUIRED = orchestrator.FINDING_REQUIRED


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _norm(s: str) -> str:
    return "".join(s.split())


def _check_quote(result, texts):
    """Return list of problem strings for quotes not findable in material text."""
    problems = []
    for f in result.get("report", {}).get("findings", []):
        fname = f.get("file")
        quote = f.get("quote", "")
        if not quote:
            problems.append(f"{f.get('id')}: empty quote")
            continue
        if fname not in texts:
            problems.append(f"{f.get('id')}: file {fname!r} not in extracted texts")
            continue
        hay = _norm(texts[fname][0])
        needle = _norm(quote)
        if needle and needle not in hay:
            problems.append(f"{f.get('id')}: quote not found in {fname}")
    return problems


def validate(root: Path, out: Path, work: Path):
    """Return (ok, problems)."""
    problems = []
    result = _load(out / "result.json")
    if result is None:
        return False, ["result.json missing or unparseable"]

    case_id = result.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        problems.append("case_id empty")

    status = result.get("status")
    if status not in ALLOWED_STATUS:
        problems.append(f"status {status!r} not in {sorted(ALLOWED_STATUS)}")

    contract = result.get("output_contract")
    if contract is None:
        problems.append("output_contract key missing")

    for key in ("sources", "uncertainty", "change_log", "errors",
                "degradations", "pending_human"):
        if not isinstance(result.get(key), list):
            problems.append(f"{key} missing or not a list")

    findings = result.get("report", {}).get("findings", [])
    seen = set()
    for f in findings:
        fid = f.get("id")
        if fid in seen:
            problems.append(f"duplicate finding id {fid}")
        seen.add(fid)
        for k in FINDING_REQUIRED:
            if not f.get(k):
                problems.append(f"{fid or '?'}: missing {k}")
        if f.get("type") not in FINDING_TYPES:
            problems.append(f"{fid or '?'}: bad type {f.get('type')!r}")
        if f.get("severity") not in SEVERITIES:
            problems.append(f"{fid or '?'}: bad severity {f.get('severity')!r}")

    if result.get("status") == "ok" and not findings and not result.get("report", {}).get("summary"):
        problems.append("status=ok but no findings and no summary")

    evidence = _load(work / "evidence.json")
    texts = {}
    if evidence:
        for rel, meta in evidence.get("parsed", {}).items():
            p = Path(meta.get("text_path"))
            if p and p.exists():
                t, _err = extract.read_text_robust(p)
                if not _err:
                    texts[rel] = (t,)
    problems.extend(_check_quote(result, texts))

    report_path = out / "report.md"
    if not report_path.exists():
        problems.append("report.md missing")

    return (len(problems) == 0), problems


def _demo_input() -> Path:
    """Create a temporary layout mirroring the runtime contract:
    input / output / work are three separate directories."""
    base = Path(tempfile.mkdtemp(prefix="audit-selftest-"))
    inp = base / "input"
    inp.mkdir()
    (inp / "case.json").write_text(json.dumps({
        "case_id": "demo-case-001",
        "output_contract": {
            "report_format": "json+md",
            "required_fields": None,
            "tags": None,
        },
    }, ensure_ascii=False), encoding="utf-8")
    (inp / "task.md").write_text(
        "# 演示任务\n对材料做学术规范审查。\n", encoding="utf-8")
    (inp / "paper.md").write_text(
        "# 演示论文\n\n## 摘要\n\n本研究招募 40 名参与者，但未说明伦理批件。\n\n"
        "结果具有显著性（p < 0.05）。联系方式：test@example.com。\n\n"
        "图 1 改编自第三方文献。\n\n## 参考文献\n\n[1] Author A. Title. 2020.\n",
        encoding="utf-8")
    return base


def selftest() -> int:
    base = _demo_input()
    inp = base / "input"
    out = base / "output"
    work = base / "work"
    try:
        rc = orchestrator.cmd_run(argparse.Namespace(
            input=str(inp), output=str(out), work=str(work)))
        if rc != 0:
            print(f"selftest FAIL: run rc={rc}")
            return 1
        result = _load(out / "result.json")
        if not result or result.get("status") != "partial":
            print("selftest FAIL: skeleton not written")
            return 1
        evidence = _load(work / "evidence.json")
        n_obs = len(evidence.get("observations", [])) if evidence else 0
        print(f"selftest: skeleton ok, {n_obs} observations")

        # deterministic re-run
        rc2 = orchestrator.cmd_run(argparse.Namespace(
            input=str(inp), output=str(out), work=str(work)))
        evidence2 = _load(work / "evidence.json")
        if rc2 != 0 or evidence2.get("observations") != evidence.get("observations"):
            print("selftest FAIL: non-deterministic extraction")
            return 1
        print("selftest: deterministic re-run ok")

        # simulate one adjudicated finding + one rejection
        obs = evidence["observations"]
        target = next((o for o in obs if o["rule"] == "pii_email"), obs[0] if obs else None)
        findings_payload = {
            "summary": "演示案例：确认一项隐私问题。",
            "findings": [],
            "rejections": [],
            "coverage": {"privacy": {"status": "checked", "note": "演示"}},
            "uncertainty": ["其余维度未展开。"],
            "pending_human": [],
        }
        if target:
            findings_payload["findings"].append({
                "id": "F-001", "type": "norm_violation", "severity": "major",
                "category": "privacy", "title": "可识别邮箱未脱敏",
                "file": target["file"], "locator": target["locator"],
                "quote": target["quote"],
                "evidence": "材料中出现邮箱，未说明授权或脱敏。",
                "basis": "隐私合规要求：可识别个人信息需授权或脱敏。",
                "revision": "删除或脱敏该邮箱，并说明处理依据。",
            })
        for o in obs[:1]:
            findings_payload["rejections"].append(
                {"observation_id": o["id"], "reason": "演示：确认为风格差异。"})
        (work / "findings.json").write_text(
            json.dumps(findings_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        rc3 = orchestrator.cmd_merge(argparse.Namespace(
            input=str(inp), output=str(out), work=str(work),
            findings=str(work / "findings.json")))
        if rc3 != 0:
            print(f"selftest FAIL: merge rc={rc3}")
            return 1
        ok, problems = validate(inp, out, work)
        if not ok:
            print("selftest FAIL:", "; ".join(problems))
            return 1
        print(f"selftest PASS: {len(findings_payload['findings'])} finding merged, "
              f"validate ok")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Self-check for audit skill")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--selftest", action="store_true",
                    help="run end-to-end demo pipeline and exit")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    ok, problems = validate(Path(args.input), Path(args.output), Path(args.work))
    for p in problems:
        print(f"FAIL: {p}")
    if ok:
        print("validate: OK")
        return 0
    print(f"validate: {len(problems)} problem(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
