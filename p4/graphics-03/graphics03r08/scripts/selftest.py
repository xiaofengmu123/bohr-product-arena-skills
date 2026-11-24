#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill 自检：空输入、坏 JSON、缺输入、重复运行四类场景 + happy path。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

CASE = {
    "case_id": "selftest-case-001",
    "topic": "biological_mechanism_pathway",
    "materials": ["materials/paper.md"],
}
PAPER = """# Example pathway

EGFR is a receptor tyrosine kinase located at the plasma membrane.
Upon ligand binding, EGFR activates RAS.
RAS then activates RAF, and RAF phosphorylates MEK in the cytosol.
MEK activates ERK, which translocates to the nucleus to promote transcription.
A negative feedback: ERK inhibits SOS.
"""


def run_py(args, timeout=120):
    return subprocess.run([sys.executable, MAIN] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def make_case(base):
    os.makedirs(os.path.join(base, "input", "materials"), exist_ok=True)
    with open(os.path.join(base, "input", "case.json"), "w", encoding="utf-8") as f:
        json.dump(CASE, f, ensure_ascii=False)
    with open(os.path.join(base, "input", "materials", "paper.md"), "w", encoding="utf-8") as f:
        f.write(PAPER)
    with open(os.path.join(base, "input", "task.md"), "w", encoding="utf-8") as f:
        f.write("生成 EGFR-RAS-RAF-MEK-ERK 通路的机制图，保留可编辑源文件与导出件。\n")


def check_artifacts(out):
    rp = os.path.join(out, "result.json")
    assert os.path.isfile(rp), "result.json missing"
    with open(rp, "r", encoding="utf-8-sig") as f:
        r = json.load(f)
    assert r.get("case_id") == CASE["case_id"], "case_id mismatch"
    assert r.get("status") in ("ok", "partial", "insufficient_evidence", "input_error")
    assert isinstance(r.get("change_log") or [], list) and r.get("change_log"), "change_log empty"
    svg = os.path.join(out, "mechanism.svg")
    assert os.path.isfile(svg), "mechanism.svg missing"
    with open(svg, "r", encoding="utf-8") as f:
        svg_text = f.read()
    assert svg_text.lstrip().startswith("<"), "SVG invalid"
    assert 'id="layer-nodes"' in svg_text, "SVG 缺少 nodes 图层"
    pdf = os.path.join(out, "mechanism.pdf")
    assert os.path.isfile(pdf), "mechanism.pdf missing"
    with open(pdf, "rb") as f:
        assert f.read(5) == b"%PDF-", "PDF signature invalid"
    return r


def main():
    results = []
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        # 1. happy path + determinism
        base = os.path.join(tmp, "happy")
        make_case(base)
        inp, work, out = os.path.join(base, "input"), os.path.join(base, "work"), os.path.join(base, "output")
        rc = run_py(["run", "--input", inp, "--work", work, "--out", out])
        ok1 = (rc.returncode == 0)
        r1 = check_artifacts(out) if ok1 else None
        with open(os.path.join(out, "mechanism.svg"), "rb") as f:
            svg1 = f.read()
        # 重复运行（清空产物目录后重跑）
        shutil.rmtree(out)
        rc2 = run_py(["run", "--input", inp, "--work", work, "--out", out])
        with open(os.path.join(out, "mechanism.svg"), "rb") as f:
            svg2 = f.read()
        det = (svg1 == svg2)
        results.append(("happy_path", ok1 and det))

        # 2. 空输入
        empty = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(empty, "input"), exist_ok=True)
        rc = run_py(["run", "--input", os.path.join(empty, "input"),
                     "--work", os.path.join(empty, "work"), "--out", os.path.join(empty, "output")])
        r = None
        rp = os.path.join(empty, "output", "result.json")
        if os.path.isfile(rp):
            with open(rp, "r", encoding="utf-8-sig") as f:
                r = json.load(f)
        results.append(("empty_input", rc.returncode == 0 and r is not None and r.get("status") == "insufficient_evidence"))

        # 3. 坏 JSON
        bad = os.path.join(tmp, "bad")
        os.makedirs(os.path.join(bad, "input"), exist_ok=True)
        with open(os.path.join(bad, "input", "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        rc = run_py(["run", "--input", os.path.join(bad, "input"),
                     "--work", os.path.join(bad, "work"), "--out", os.path.join(bad, "output")])
        rp = os.path.join(bad, "output", "result.json")
        exists = os.path.isfile(rp)
        results.append(("bad_json_no_crash", rc.returncode in (0, 1, 2) and exists))

        # 4. 缺输入目录
        missing = os.path.join(tmp, "missing")
        rc = run_py(["run", "--input", os.path.join(missing, "nope"),
                     "--work", os.path.join(missing, "work"), "--out", os.path.join(missing, "output")])
        rp = os.path.join(missing, "output", "result.json")
        results.append(("missing_input_graceful", rc.returncode in (0, 1, 2) and os.path.isfile(rp)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=== Skill 自检结果 ===")
    all_ok = True
    for name, ok in results:
        print("%-22s %s" % (name, "PASS" if ok else "FAIL"))
        all_ok = all_ok and ok
    print("RESULT:", "ALL PASS" if all_ok else "SOME FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
