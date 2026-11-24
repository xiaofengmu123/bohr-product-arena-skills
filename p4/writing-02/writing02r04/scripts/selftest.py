# -*- coding: utf-8 -*-
"""Self-test for the uncertainty-aware academic-polish Skill package.

Runs the full scaffold -> verify -> finalize -> selfcheck pipeline against
temporary directories, plus the degradation cases required by the rubric
(empty input, malformed JSON, bad encoding, missing draft). Exits 0 only when
every assertion passes; prints a compact PASS/FAIL line per scenario.

Standard library only; deterministic; no network.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import main  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        def wrapper():
            root = tempfile.mkdtemp(prefix="selftest-")
            try:
                inp = os.path.join(root, "input")
                out = os.path.join(root, "output")
                work = os.path.join(root, "work")
                os.makedirs(inp)
                fn(inp, out, work)
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                print("FAIL", name, "->", e)
                sys.exit(1)
            finally:
                shutil.rmtree(root, ignore_errors=True)
        CASES.append(wrapper)
        return wrapper
    return deco


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def assert_ok(result_path, expected_status=None):
    r = read(result_path)
    assert isinstance(r, dict), "result.json not a dict"
    if expected_status is not None:
        assert r.get("status") == expected_status, \
            "status %r != %r" % (r.get("status"), expected_status)
    for field in ("sources", "uncertainty", "change_log", "case_id"):
        assert field in r, "missing field " + field
    return r


def run_scaffold(inp, out, work):
    return main.main(["scaffold", "--input", inp, "--output", out,
                      "--work", work])


def happy_pipeline(inp, out, work, draft_text):
    write(os.path.join(inp, "case.json"), json.dumps(
        {"case_id": "demo-1", "id": "demo-1", "text": draft_text,
         "output_contract": {"deliverable": None, "fact_source_map": [],
                             "version_boundary": []}}))
    write(os.path.join(inp, "task.md"),
          "# 任务\n请润色下方摘要，保留事实与限定词。\n")
    assert run_scaffold(inp, out, work) == 0
    assert_ok(os.path.join(out, "result.json"), "partial")

    # simulate the Agent filling in the minimal faithful edits
    edits = read(os.path.join(work, "edits.json"))
    assert edits["units"], "no units scaffolded"
    for u in edits["units"]:
        u["revised"] = u["original"]
        u["edit_type"] = "none"
        u["reason"] = "self-test pass-through"
        u["confidence"] = "high"
    edits["facts"] = [{"fact": "3.5 mg/kg dose [12] (Fig. 1)",
                       "locator": "case.json.text#L1",
                       "quote": "3.5 mg/kg dose [12] (Fig. 1)",
                       "certainty": "verified"}]
    edits["target_lang"] = "en"
    edits["glossary"] = [{"term": "dose", "canonical": "dose",
                          "variants": [], "first_use_locator": "text#L1"}]
    with open(os.path.join(work, "edits.json"), "w", encoding="utf-8") as f:
        json.dump(edits, f, ensure_ascii=False, indent=2)

    assert main.main(["verify", "--input", inp, "--output", out,
                      "--work", work]) == 0
    report = read(os.path.join(work, "check_report.json"))
    assert report["summary"]["errors"] == 0, "unexpected errors: %s" % \
        report["errors"]
    assert main.main(["finalize", "--input", inp, "--output", out,
                      "--work", work]) == 0
    r = assert_ok(os.path.join(out, "result.json"), "ok")
    assert r["deliverable"] == "manuscript.md"
    assert os.path.isfile(os.path.join(out, "manuscript.md"))
    assert os.path.isfile(os.path.join(out, "glossary.md"))
    assert main.main(["selfcheck", "--input", inp, "--output", out,
                      "--work", work]) == 0


@case("happy-path-full-pipeline")
def _happy(inp, out, work):
    happy_pipeline(inp, out, work,
                   "The result may suggest a 3.5 mg/kg dose [12] (Fig. 1).")


@case("empty-input-directory")
def _empty(inp, out, work):
    shutil.rmtree(inp)
    assert run_scaffold(inp, out, work) == 0
    assert_ok(os.path.join(out, "result.json"), "input_error")


@case("malformed-case-json")
def _bad_json(inp, out, work):
    write(os.path.join(inp, "case.json"), "{not valid json")
    write(os.path.join(inp, "task.md"), "# 任务\n")
    assert run_scaffold(inp, out, work) == 0
    assert_ok(os.path.join(out, "result.json"))


@case("bad-encoding-draft")
def _bad_encoding(inp, out, work):
    write(os.path.join(inp, "case.json"), json.dumps({"case_id": "badenc"}))
    write(os.path.join(inp, "task.md"), "# 任务\n")
    with open(os.path.join(inp, "draft.md"), "wb") as f:
        f.write(b"summary text \xff\xfe with broken bytes")
    assert run_scaffold(inp, out, work) == 0
    assert_ok(os.path.join(out, "result.json"))


@case("missing-draft")
def _missing_draft(inp, out, work):
    write(os.path.join(inp, "case.json"), json.dumps({"case_id": "nodraft"}))
    write(os.path.join(inp, "task.md"), "# 任务\n")
    assert run_scaffold(inp, out, work) == 0
    assert_ok(os.path.join(out, "result.json"), "insufficient_evidence")


if __name__ == "__main__":
    for fn in CASES:
        fn()
    print("selftest: all", len(CASES), "scenarios passed")
