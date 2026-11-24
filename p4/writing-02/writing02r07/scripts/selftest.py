# -*- coding: utf-8 -*-
"""Self-test for the academic-polish Skill (robustness variant).

Runs the main.py pipeline against synthetic inputs covering the robustness
matrix: empty input, bad JSON, bad encoding, oversized file, missing params,
and a normal happy path. Also checks determinism (same input -> same result,
ignoring wall-clock metadata).

Exit code 0 when every scenario degrades gracefully or succeeds; non-zero
otherwise. Standard library only; no network.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main as main_mod  # noqa: E402


class NS:
    """Tiny argparse-like namespace so we can call subcommand functions."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def run(argv_args, cwd=None):
    argv = sys.argv
    sys.argv = ["main.py"] + argv_args
    try:
        code = main_mod.main(argv_args)
    finally:
        sys.argv = argv
    return code


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def scenario(name):
    print(f"\n== {name} ==")


def check(cond, msg):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
    return cond


def main():
    ok = True

    # 1. empty input directory
    scenario("empty input")
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "input")
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        os.makedirs(inp)
        code = run(["scaffold", "--input", inp, "--output", out, "--work", work])
        r = load(os.path.join(out, "result.json"))
        ok &= check(code == 0, "exit 0 on empty input")
        ok &= check(r.get("status") == "insufficient_evidence", "status set honestly")
        ok &= check(isinstance(r.get("sources"), list), "sources present")

    # 2. missing input directory entirely
    scenario("missing input directory")
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        code = run(["scaffold", "--input", os.path.join(d, "nope"),
                    "--output", out, "--work", work])
        r = load(os.path.join(out, "result.json"))
        ok &= check(code == 0, "exit 0 on missing input dir")
        ok &= check(r.get("status") == "insufficient_evidence",
                    "insufficient_evidence recorded")

    # 3. bad JSON in case.json
    scenario("bad JSON case.json")
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "input")
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"), "{ not valid json !")
        write(os.path.join(inp, "task.md"), "# task\n润色这段摘要\n")
        write(os.path.join(inp, "draft.md"),
              "该模型在 5 组数据上达到 98.3% 的准确率（图 1），表明其有效。\n")
        code = run(["scaffold", "--input", inp, "--output", out, "--work", work])
        r = load(os.path.join(out, "result.json"))
        ok &= check(code == 0, "exit 0 on bad JSON")
        ok &= check(any("unreadable" in e for e in r.get("errors", [])),
                    "bad JSON recorded in errors")
        ok &= check(r.get("status") in ("partial", "ok"), "still produced partial draft")

    # 4. oversized file is skipped, not fatal
    scenario("oversized file")
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "input")
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"), '{"id": "case-oversize", "text": "短文。"}')
        write(os.path.join(inp, "big.md"), "x" * (3 * 1024 * 1024))
        code = run(["scaffold", "--input", inp, "--output", out, "--work", work])
        r = load(os.path.join(out, "result.json"))
        ok &= check(code == 0, "exit 0 with oversized file")
        ok &= check(any("超限" in e or "oversized" in e for e in r.get("degradations", [])),
                    "oversized file recorded as degradation")

    # 5. happy path: full pipeline and determinism
    scenario("happy path + determinism")
    draft = ("本研究评估了一种新型催化剂。\n"
             "在 25°C 与 1.5 MPa 下，转化率达到 98.3%（图 1）。\n"
             "结果表明该方法可能比传统路线更高效。\n")
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "input")
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"),
              json.dumps({"id": "case-1", "output_contract": {
                  "deliverable": None, "fact_source_map": [], "version_boundary": []}}))
        write(os.path.join(inp, "task.md"), "# task\n润色摘要，保留事实。\n")
        write(os.path.join(inp, "draft.md"), draft)

        code = run(["scaffold", "--input", inp, "--output", out, "--work", work])
        ok &= check(code == 0, "scaffold exit 0")
        r1 = load(os.path.join(out, "result.json"))
        ok &= check(r1.get("status") == "partial", "scaffold writes partial skeleton")
        ok &= check(r1.get("case_id") == "case-1", "case_id filled")
        ok &= check(isinstance(r1.get("fact_source_map"), list) and
                    len(r1["fact_source_map"]) >= 1, "fact_source_map has entries")
        ok &= check(any(f.get("quote") and f.get("locator")
                        for f in r1["fact_source_map"]), "locator + quote present")

        edits = load(os.path.join(work, "edits.json"))
        ok &= check(len(edits["units"]) >= 3, "sentence units split")
        # model-style edit: fix one hedge without touching facts
        for u in edits["units"]:
            u["revised"] = u["original"].replace("可能", "或许")
            u["edit_type"] = "hedge_rephrase"
            u["reason"] = "改写弱限定词，强度不变"
        write(os.path.join(work, "edits.json"), json.dumps(edits, ensure_ascii=False))

        code = run(["verify", "--input", inp, "--output", out, "--work", work])
        ok &= check(code == 0, "verify exit 0")
        rep = load(os.path.join(work, "check_report.json"))
        ok &= check(isinstance(rep.get("errors"), int), "check report has error count")

        code = run(["finalize", "--input", inp, "--output", out, "--work", work])
        ok &= check(code == 0, "finalize exit 0")
        ok &= check(os.path.exists(os.path.join(out, "manuscript.md")),
                    "manuscript.md exists")
        ok &= check(os.path.exists(os.path.join(out, "glossary.md")),
                    "glossary.md exists")

        code = run(["selfcheck", "--input", inp, "--output", out, "--work", work])
        ok &= check(code == 0, "selfcheck exit 0")

        # determinism: same input re-scaffolded produces identical fact map
        work2 = os.path.join(d, "work2")
        code = run(["scaffold", "--input", inp, "--output", out, "--work", work2])
        r2 = load(os.path.join(out, "result.json"))
        ok &= check(r1.get("fact_source_map") == r2.get("fact_source_map"),
                    "deterministic fact_source_map")

    # 6. bad encoding (latin-1 only bytes) degrades without crash
    scenario("bad encoding fallback")
    with tempfile.TemporaryDirectory() as d:
        inp = os.path.join(d, "input")
        out = os.path.join(d, "output")
        work = os.path.join(d, "work")
        os.makedirs(inp)
        write(os.path.join(inp, "case.json"), '{"id": "enc"}')
        write_bytes(os.path.join(inp, "draft.md"), b"This is latin-1: caf\xe9 study.\n")
        code = run(["scaffold", "--input", inp, "--output", out, "--work", work])
        r = load(os.path.join(out, "result.json"))
        ok &= check(code == 0, "exit 0 on latin-1 fallback")
        ok &= check(r.get("status") in ("partial", "ok"), "draft still parsed")

    print("\n" + ("ALL SELF-TESTS PASSED" if ok else "SELF-TESTS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
