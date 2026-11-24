#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the sci-repro-clean skill package.

Three independent modes (standard library only, deterministic):

  package      validate SKILL.md frontmatter, script importability, stdlib-only
  result       validate a produced /app/output/result.json (schema, contract,
               artifacts, sources evidence, report<->CSV consistency)
  determinism  run `main.py --phase full` twice in fresh temp dirs on the SAME
               input and compare canonical content fingerprints, proving that
               identical input yields identical outputs

Usage:
  python scripts/selftest.py --mode package --skill-root /skills/weekly-skill
  python scripts/selftest.py --mode result   --input /input --output /app/output --work /app/work
  python scripts/selftest.py --mode determinism --input /input --work /app/work
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as core


# --------------------------------------------------------------------------
# Mode: package
# --------------------------------------------------------------------------
def _read_frontmatter(skill_md):
    text = core.read_text_robust(skill_md)["text"]
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return None, "missing YAML frontmatter"
    return m.group(1), None


def _parse_frontmatter(fm_text):
    out = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip("\"'")
    return out


def _stdlib_only(dirpath):
    offenders = []
    for root, _dirs, files in os.walk(dirpath):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(root, fn)
            for i, line in enumerate(core.read_text_robust(p)["text"].splitlines()):
                m = re.match(r"\s*(?:from|import)\s+([A-Za-z0-9_.]+)", line)
                if not m:
                    continue
                top = m.group(1).split(".")[0]
                # local module shipped inside this package (not a 3rd-party dep)
                if top == "core_impl":
                    continue
                if top not in ("__future__", "argparse", "csv", "datetime",
                               "decimal", "hashlib", "io", "json", "math",
                               "os", "re", "sys", "unicodedata", "collections",
                               "subprocess", "tempfile", "functools", "itertools",
                               "pathlib", "stat", "shutil", "textwrap"):
                    offenders.append(f"{p}:{i + 1}: import {m.group(1)}")
    return offenders


def check_package(skill_root):
    checks = []
    skill_md = os.path.join(skill_root, "SKILL.md")
    if not os.path.isfile(skill_md):
        return [{"item": "SKILL.md_exists", "ok": False, "detail": "SKILL.md 缺失"}]
    fm, err = _read_frontmatter(skill_md)
    if err:
        checks.append({"item": "frontmatter", "ok": False, "detail": err})
    else:
        meta = _parse_frontmatter(fm)
        for key in ("name", "description", "version"):
            ok = bool(meta.get(key))
            checks.append({"item": f"frontmatter:{key}", "ok": ok,
                           "detail": "" if ok else f"缺 {key}"})
    scripts_dir = os.path.join(skill_root, "scripts")
    for fn in ("main.py", "core_impl.py", "selftest.py"):
        p = os.path.join(scripts_dir, fn)
        exists = os.path.isfile(p)
        checks.append({"item": f"script:{fn}", "ok": exists,
                       "detail": "" if exists else f"{fn} 缺失"})
        if exists:
            src = core.read_text_robust(p)["text"]
            try:
                compile(src, p, "exec")
                checks.append({"item": f"compile:{fn}", "ok": True, "detail": ""})
            except SyntaxError as exc:
                checks.append({"item": f"compile:{fn}", "ok": False,
                               "detail": f"语法错误 L{exc.lineno}: {exc.msg}"})
    offenders = _stdlib_only(scripts_dir)
    checks.append({"item": "stdlib_only", "ok": not offenders,
                   "detail": "" if not offenders else "; ".join(offenders)})
    # main.py --help must exit 0
    try:
        proc = subprocess.run(
            [sys.executable, "-B", os.path.join(scripts_dir, "main.py"), "--help"],
            capture_output=True, text=True, timeout=30)
        ok = proc.returncode == 0
        checks.append({"item": "main_help", "ok": ok,
                       "detail": "" if ok else f"exit {proc.returncode}: {proc.stderr[:200]}"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"item": "main_help", "ok": False, "detail": str(exc)})
    return checks


# --------------------------------------------------------------------------
# Mode: result
# --------------------------------------------------------------------------
def check_result(input_dir, output_dir, work_dir):
    checks = []
    obj, err, _path = core._load_result(output_dir)
    if err:
        return [{"item": "result_json", "ok": False, "detail": err}]
    for key in ("case_id", "status", "sources", "uncertainty", "change_log"):
        ok = key in obj and obj[key] not in (None,)
        checks.append({"item": f"field:{key}", "ok": bool(ok),
                       "detail": "" if ok else f"缺 {key}"})
    ok = obj.get("status") in core.STATUS_ENUM
    checks.append({"item": "status_enum", "ok": ok,
                   "detail": "" if ok else f"非法 status {obj.get('status')!r}"})
    case = core.load_case(input_dir, [os.path.join(work_dir, "extracted")])
    contract = case.get("output_contract") or {}
    missing = [k for k in contract if k not in obj]
    checks.append({"item": "contract_keys", "ok": not missing,
                   "detail": "" if not missing else f"缺契约键 {missing}"})
    artifacts = obj.get("artifacts") or {}
    for key, rel in sorted(artifacts.items()):
        exists = os.path.isfile(os.path.join(output_dir, rel))
        checks.append({"item": f"artifact:{rel}", "ok": exists,
                       "detail": "" if exists else "缺失"})
    sources = obj.get("sources") or []
    bad = [s for s in sources if not (isinstance(s, dict) and s.get("locator") and s.get("quote"))]
    checks.append({"item": "sources_evidence", "ok": not bad,
                   "detail": "" if not bad else f"{len(bad)} 条缺 locator/quote"})
    csv_rel = artifacts.get("cleaned_data")
    if csv_rel:
        csv_path = os.path.join(output_dir, csv_rel)
        ok, detail = core._recompute_consistency(csv_path, obj)
        checks.append({"item": "report_table_consistency", "ok": ok, "detail": detail})
    checks.append({"item": "fingerprint", "ok": True,
                   "detail": core.canonical_fingerprint(obj)[:16] + "…"})
    return checks


# --------------------------------------------------------------------------
# Mode: determinism
# --------------------------------------------------------------------------
def check_determinism(input_dir, work_dir):
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    fps = []
    details = []
    for round_i in (1, 2):
        tmp = tempfile.mkdtemp(prefix=f"det-{round_i}-")
        out_dir = os.path.join(tmp, "output")
        w_dir = os.path.join(tmp, "work")
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(w_dir, exist_ok=True)
        try:
            proc = subprocess.run(
                [sys.executable, "-B", main_py, "--phase", "full",
                 "--input", input_dir, "--output", out_dir, "--work", w_dir],
                capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            details.append(f"round{round_i}: timeout")
            continue
        if proc.returncode != 0:
            details.append(f"round{round_i}: exit {proc.returncode} {proc.stderr[:200]}")
            continue
        rp = os.path.join(out_dir, "result.json")
        if not os.path.isfile(rp):
            details.append(f"round{round_i}: result.json 缺失")
            continue
        obj, err, _ = core._load_result(out_dir)
        if err:
            details.append(f"round{round_i}: {err}")
            continue
        fps.append(core.canonical_fingerprint(obj))
        details.append(f"round{round_i}: fingerprint={fps[-1][:16]}…")
    ok = len(fps) == 2 and fps[0] == fps[1]
    return [{
        "item": "determinism", "ok": ok,
        "detail": "; ".join(details) if details else "未执行",
    }]


# --------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", required=True,
                   choices=["package", "result", "determinism"])
    p.add_argument("--skill-root", default="/skills/weekly-skill")
    p.add_argument("--input", default="/input")
    p.add_argument("--output", default="/app/output")
    p.add_argument("--work", default="/app/work")
    args = p.parse_args(argv)

    if args.mode == "package":
        checks = check_package(args.skill_root)
    elif args.mode == "result":
        checks = check_result(args.input, args.output, args.work)
    else:
        checks = check_determinism(args.input, args.work)

    report = {
        "mode": args.mode,
        "pass": all(c["ok"] for c in checks),
        "n_checks": len(checks),
        "n_failed": sum(1 for c in checks if not c["ok"]),
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
