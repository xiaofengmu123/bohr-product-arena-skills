"""selftest.py - run a clean-room self check on the Skill package.

Creates disposable inputs in a temp directory, runs the real engine through
main.py, and asserts the output contract, determinism, and degradation paths.

Usage:  python3 -B scripts/selftest.py
Exit code 0 on all-pass, non-zero otherwise.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def run_main(args, cwd=ROOT):
    return subprocess.run(
        [sys.executable, "-B", os.path.join("scripts", "main.py")] + args,
        cwd=cwd, capture_output=True, text=True,
    )


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def canonicalize(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "runtime":
                continue
            if k == "change_log":
                out[k] = [c for c in v if not c.get("time")]
                continue
            out[k] = canonicalize(v)
        return out
    if isinstance(obj, list):
        return [canonicalize(x) for x in obj]
    return obj


def make_scenario(root):
    case = {
        "id": "challenge-self",
        "case_id": "case-self-0001",
        "output_contract": {
            "files": "list of material file paths",
            "duplicate_groups": "list of content-hash duplicate groups",
            "version_candidates": "list of version chains",
            "summary": "counts summary",
            "library_manifest": "path to the manifest artifact",
        },
    }
    with open(os.path.join(root, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(root, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# 科研资料库修复\n\n约定：重复按内容 SHA-256，版本按归一化主干重建。\n版本说明 v2 取代 v1。\n")
    data = os.path.join(root, "materials")
    os.makedirs(os.path.join(data, "raw"), exist_ok=True)
    os.makedirs(os.path.join(data, "empty"), exist_ok=True)
    with open(os.path.join(data, "raw", "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello world\n")
    with open(os.path.join(data, "raw", "b.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello world\n")
    with open(os.path.join(data, "report_v1.md"), "w", encoding="utf-8") as fh:
        fh.write("draft\n")
    with open(os.path.join(data, "report_v2.md"), "w", encoding="utf-8") as fh:
        fh.write("draft final\n")
    with open(os.path.join(data, "bad.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    with open(os.path.join(data, "lib.json"), "w", encoding="utf-8") as fh:
        json.dump([{"path": "materials/raw/a.txt", "sha256": "", "title": "A"}], fh, ensure_ascii=False)
    try:
        os.symlink(os.path.join(data, "raw", "a.txt"), os.path.join(data, "link.txt"))
    except (OSError, NotImplementedError):
        pass
    return root


def main():
    print("== frontmatter and structure ==")
    skill = os.path.join(ROOT, "SKILL.md")
    check("SKILL.md exists", os.path.isfile(skill))
    text = open(skill, encoding="utf-8").read() if os.path.isfile(skill) else ""
    fm_ok = text.startswith("---")
    check("frontmatter present", fm_ok)
    for key in ("name:", "description:", "version:"):
        check(f"frontmatter has {key.strip(':')}", key in text[:2000])
    check("body <= 180 lines", text.count("\n") <= 181)
    check("scripts/main.py exists", os.path.isfile(os.path.join(ROOT, "scripts", "main.py")))
    check("scripts/core_impl.py exists", os.path.isfile(os.path.join(ROOT, "scripts", "core_impl.py")))

    print("== scripts compile ==")
    import py_compile
    for name in ("main.py", "core_impl.py", "selftest.py"):
        ok = True
        try:
            py_compile.compile(os.path.join(ROOT, "scripts", name), doraise=True)
        except Exception as exc:  # noqa: BLE001
            ok = False
            check(f"compile {name}", False, str(exc))
            continue
        check(f"compile {name}", ok)

    print("== CLI help ==")
    r = run_main(["--help"])
    check("main.py --help exit 0", r.returncode == 0, r.stderr[-200:])

    print("== full run on scenario ==")
    tmp = tempfile.mkdtemp(prefix="libskill-selftest-")
    try:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        make_scenario(inp)
        out1 = os.path.join(tmp, "out1")
        out2 = os.path.join(tmp, "out2")
        r = run_main(["--input-root", inp, "--output-dir", out1, "--work-dir", os.path.join(tmp, "work1")])
        check("full run exit 0", r.returncode == 0, r.stderr[-300:])
        result = load(os.path.join(out1, "result.json"))
        check("case_id non-empty", bool(result.get("case_id")))
        check("status ok", result.get("status") == "ok", result.get("status"))
        check("contract files filled", isinstance(result.get("files"), list) and len(result["files"]) > 0)
        check("contract duplicate_groups filled", isinstance(result.get("duplicate_groups"), list))
        cg = result.get("computed", {}).get("duplicate_groups", [])
        check("duplicate group detected (a/b)", any(len(g["paths"]) >= 2 for g in cg))
        vc = result.get("computed", {}).get("version_candidates", [])
        check("version chain detected (report)", any(len(v["members"]) >= 2 for v in vc))
        check("bad.json recorded in errors", any("bad.json" in json.dumps(e, ensure_ascii=False) for e in result.get("errors", [])))
        check("has sources", len(result.get("sources", [])) > 0)
        check("has uncertainty", isinstance(result.get("uncertainty"), list))
        check("has change_log", len(result.get("change_log", [])) > 0)

        for art in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                    "recovery_report.json", "library_index.json", "library_manifest.md",
                    "operation_log.jsonl", "plan.json", "inventory.json",
                    "library_model.json", "derivation_manifest.json"):
            check(f"artifact {art}", os.path.isfile(os.path.join(out1, art)))

        tree = open(os.path.join(out1, "directory_tree.txt"), encoding="utf-8").read()
        check("tree marks empty dir", "[空目录]" in tree)

        print("== --check ==")
        r = run_main(["--check", os.path.join(out1, "result.json"), "--case-json", os.path.join(inp, "case.json")])
        check("--check exit 0", r.returncode == 0, r.stderr[-200:])

        print("== determinism (two runs) ==")
        r = run_main(["--input-root", inp, "--output-dir", out2, "--work-dir", os.path.join(tmp, "work2")])
        check("second run exit 0", r.returncode == 0, r.stderr[-300:])
        r1 = canonicalize(load(os.path.join(out1, "result.json")))
        r2 = canonicalize(load(os.path.join(out2, "result.json")))
        check("result deterministic", r1 == r2)

        print("== skeleton-first ==")
        out3 = os.path.join(tmp, "out3")
        r = run_main(["--input-root", inp, "--output-dir", out3, "--work-dir", os.path.join(tmp, "work3"), "--skeleton-only"])
        check("skeleton exit 0", r.returncode == 0, r.stderr[-300:])
        skel = load(os.path.join(out3, "result.json"))
        check("skeleton status partial", skel.get("status") == "partial", skel.get("status"))
        check("skeleton has case_id", bool(skel.get("case_id")))

        print("== degradation: empty input ==")
        einp = os.path.join(tmp, "empty-input")
        os.makedirs(einp)
        with open(os.path.join(einp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "e", "case_id": "case-e", "output_contract": {"files": "list"}}, fh)
        with open(os.path.join(einp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# task\n")
        eout = os.path.join(tmp, "eout")
        r = run_main(["--input-root", einp, "--output-dir", eout, "--work-dir", os.path.join(tmp, "ework")])
        check("empty input exit 0", r.returncode == 0, r.stderr[-300:])
        eres = load(os.path.join(eout, "result.json"))
        check("empty input insufficient_evidence", eres.get("status") == "insufficient_evidence", eres.get("status"))

        print("== degradation: missing case.json ==")
        minp = os.path.join(tmp, "missing")
        os.makedirs(minp)
        with open(os.path.join(minp, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        mout = os.path.join(tmp, "mout")
        r = run_main(["--input-root", minp, "--output-dir", mout, "--work-dir", os.path.join(tmp, "mwork")])
        check("missing case exit 0", r.returncode == 0, r.stderr[-300:])
        mres = load(os.path.join(mout, "result.json"))
        check("missing case input_error", mres.get("status") == "input_error", mres.get("status"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"SELFTEST FAILED: {len(FAILURES)}/{CHECKS} checks failed")
        for f in FAILURES:
            print("  -", f)
        return 1
    print(f"SELFTEST OK: {CHECKS} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
