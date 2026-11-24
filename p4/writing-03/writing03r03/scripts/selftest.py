#!/usr/bin/env python3
"""Self-test for the academic-format-adaptation Skill scripts.

Runs the main pipeline on synthetic inputs and checks:
  * empty input      -> valid skeleton, exit 0, honest status
  * bad JSON         -> caught, errors recorded, valid result still written
  * bad encoding     -> text reader degrades instead of raising
  * quote locatability -> located vs missing quotes both handled
  * determinism      -> same synthetic input twice -> byte-identical canonical
                        result (runtime block excluded by design)

Exit code: 0 all pass, 1 one or more checks fail.
Standard library only.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")

SYNTHETIC_CASE = {
    "id": "case-synth-0001",
    "output_contract": {
        "deliverable": "改稿",
        "fact_source_map": "来源映射",
        "version_boundary": "版本边界",
    },
    "task": "把给定稿件按模板要求重排标题层级并核对参考文献格式",
}

MATERIAL_TEXT = (
    "# 结果\n"
    "实验组在 12 周随访中的平均收缩压下降 8.4 mmHg（95% CI 6.1–10.7）。\n"
    "三线表 1 给出了各组基线特征。\n"
    "参考文献采用作者-年份制。\n"
)

QUOTE_PRESENT = "平均收缩压下降 8.4 mmHg"
QUOTE_ABSENT = "平均舒张压下降 9.9 mmHg"


def run_main(args, timeout=120):
    return subprocess.run([sys.executable, "-B", MAIN] + args,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def norm_result(path):
    """Read result.json and strip the volatile runtime/digest blocks."""
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    obj.pop("runtime", None)
    obj.pop("content_digest", None)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def write_case(tmp, case_obj, material_text=None, material_name="paper.md"):
    inp = os.path.join(tmp, "input")
    os.makedirs(inp, exist_ok=True)
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case_obj, fh, ensure_ascii=False)
    if material_text is not None:
        mats = os.path.join(inp, "materials")
        os.makedirs(mats, exist_ok=True)
        with open(os.path.join(mats, material_name), "w", encoding="utf-8") as fh:
            fh.write(material_text)
    return inp


def make_result_with_fsmap(out_path):
    """Inject a fact_source_map into a scaffolded result for quote checks."""
    with open(out_path, encoding="utf-8") as fh:
        obj = json.load(fh)
    obj["fact_source_map"] = [
        {
            "claim_id": "c-0001",
            "claim_text": "平均收缩压下降 8.4 mmHg",
            "quote": QUOTE_PRESENT,
            "locator": "paper.md#结果",
            "source_ref": "paper.md",
            "evidence_level": "reported_value",
        },
        {
            "claim_id": "c-0002",
            "claim_text": "平均舒张压下降 9.9 mmHg",
            "quote": QUOTE_ABSENT,
            "locator": "paper.md#结果",
            "source_ref": "paper.md",
            "evidence_level": "fabricated_for_test",
        },
    ]
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    return obj


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    return cond


def main():
    results = []
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        print("== 1/6 empty input ==")
        d = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(d, "input"), exist_ok=True)
        os.makedirs(os.path.join(d, "out"), exist_ok=True)
        os.makedirs(os.path.join(d, "work"), exist_ok=True)
        r = run_main(["scaffold", "--input", os.path.join(d, "input"),
                      "--out", os.path.join(d, "out"), "--work", os.path.join(d, "work")])
        outp = os.path.join(d, "out", "result.json")
        ok = r.returncode == 0 and os.path.exists(outp)
        results.append(check("empty-input scaffold", ok, "rc=%d" % r.returncode))
        if ok:
            with open(outp, encoding="utf-8") as fh:
                obj = json.load(fh)
            results.append(check("empty-input status", obj.get("status") == "partial",
                                 "status=%r" % obj.get("status")))

        print("== 2/6 bad JSON case.json ==")
        d = os.path.join(tmp, "badjson")
        os.makedirs(os.path.join(d, "input"), exist_ok=True)
        with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json")
        r = run_main(["scaffold", "--input", os.path.join(d, "input"),
                      "--out", os.path.join(d, "out"), "--work", os.path.join(d, "work")])
        out_json = os.path.join(d, "out", "result.json")
        ok = r.returncode == 0 and os.path.exists(out_json)
        results.append(check("bad-json scaffold", ok, "rc=%d" % r.returncode))
        if ok:
            with open(out_json, encoding="utf-8") as fh:
                obj = json.load(fh)
            results.append(check("bad-json error recorded",
                                 any("invalid JSON" in str(e) for e in obj.get("errors", [])),
                                 "errors=%r" % obj.get("errors")))

        print("== 3/6 bad encoding material ==")
        d = os.path.join(tmp, "badenc")
        os.makedirs(os.path.join(d, "input", "materials"), exist_ok=True)
        with open(os.path.join(d, "input", "materials", "paper.txt"), "wb") as fh:
            fh.write("中文字符串".encode("gb18030"))
        r = run_main(["scaffold", "--input", os.path.join(d, "input"),
                      "--out", os.path.join(d, "out"), "--work", os.path.join(d, "work")])
        results.append(check("bad-encoding scaffold", r.returncode == 0, "rc=%d" % r.returncode))

        print("== 4/6 quote locatability ==")
        d = os.path.join(tmp, "quote")
        inp = write_case(d, SYNTHETIC_CASE, MATERIAL_TEXT)
        r = run_main(["scaffold", "--input", inp, "--out", os.path.join(d, "out"),
                      "--work", os.path.join(d, "work")])
        make_result_with_fsmap(os.path.join(d, "out", "result.json"))
        r = run_main(["verify", "--result", os.path.join(d, "out", "result.json"),
                      "--case", os.path.join(inp, "case.json"),
                      "--material-root", inp,
                      "--work", os.path.join(d, "work"),
                      "--report", os.path.join(d, "out", "verification_report.json")])
        results.append(check("verify exits warnings", r.returncode == 1, "rc=%d" % r.returncode))
        rep_path = os.path.join(d, "out", "verification_report.json")
        if os.path.exists(rep_path):
            with open(rep_path, encoding="utf-8") as fh:
                rep = json.load(fh)
            warns = rep.get("warnings", [])
            located = any("c-0001" in w for w in warns)
            missing = any("c-0002" in w and "not located" in w for w in warns)
            results.append(check("located quote not flagged", not located))
            results.append(check("missing quote flagged", missing, "warnings=%d" % len(warns)))

        print("== 5/6 determinism (same input twice) ==")
        base = os.path.join(tmp, "det")
        inp = write_case(base, SYNTHETIC_CASE, MATERIAL_TEXT)
        outs = []
        for i in (1, 2):
            d = os.path.join(base, "run%d" % i)
            os.makedirs(os.path.join(d, "work"), exist_ok=True)
            r = run_main(["scaffold", "--input", inp, "--out", os.path.join(d, "out"),
                          "--work", os.path.join(d, "work")])
            make_result_with_fsmap(os.path.join(d, "out", "result.json"))
            run_main(["seal", "--result", os.path.join(d, "out", "result.json")])
            outs.append(norm_result(os.path.join(d, "out", "result.json")))
        results.append(check("deterministic canonical output", outs[0] == outs[1]))

        print("== 6/6 digest stability ==")
        d = os.path.join(base, "run1")
        r = run_main(["digest", "--result", os.path.join(d, "out", "result.json")])
        dig1 = json.loads(r.stdout).get("content_digest")
        d2 = os.path.join(base, "run2")
        r = run_main(["digest", "--result", os.path.join(d2, "out", "result.json")])
        dig2 = json.loads(r.stdout).get("content_digest")
        results.append(check("digest identical", dig1 is not None and dig1 == dig2,
                             "digest=%s" % dig1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for x in results if x)
    total = len(results)
    print("\nselftest: %d/%d checks passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
