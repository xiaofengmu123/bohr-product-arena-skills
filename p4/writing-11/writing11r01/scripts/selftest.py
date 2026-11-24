#!/usr/bin/env python3
"""Self-test for scholar-bilingual-translator.

Exercises discover/init/add-evidence/check/finalize on synthetic data, plus
empty-input and bad-JSON degradation. Standard library only. Exit 0 = all pass.
"""
import json
import os
import shutil
import sys
import tempfile

import core_impl as core


def _make_tree(root):
    inp = os.path.join(root, "input")
    os.makedirs(inp, exist_ok=True)
    case = {
        "case_id": "selftest-case-001",
        "task": "Translate to Chinese, preserve numbers and citations.",
        "output_contract": ["deliverables", "fact_source_map", "version_boundary"],
        "materials": [{"path": "paper.md", "type": "text"}],
    }
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, ensure_ascii=False)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
        fh.write("# task\nTranslate and keep evidence.\n")
    with open(os.path.join(inp, "paper.md"), "w", encoding="utf-8") as fh:
        fh.write("The model reports 95.2% accuracy [12].\n")


def main():
    results = []
    root = tempfile.mkdtemp(prefix="sbt_selftest_")
    try:
        _make_tree(root)
        inp = os.path.join(root, "input")
        work = os.path.join(root, "work")
        outd = os.path.join(root, "output")
        os.makedirs(work, exist_ok=True)
        os.makedirs(outd, exist_ok=True)

        brief = core.discover_input(inp)
        assert brief["case_id"] == "selftest-case-001", brief
        assert "paper.md" in brief["materials"], brief
        results.append(("discover", True))

        result_path = os.path.join(outd, "result.json")
        store = core.ResultStore(result_path)
        store.init("selftest-case-001", brief["output_contract"])
        results.append(("init", True))

        dl_path = os.path.join(outd, "translation.md")
        with open(dl_path, "w", encoding="utf-8") as fh:
            fh.write("# \u8bd1\u6587\n\u6a21\u578b\u62a5\u544a 95.2% \u7684\u51c6\u786e\u7387 [12]\u3002\n")
        store.add_list("deliverables", {
            "path": dl_path, "format": "markdown", "title": "translation",
            "description": "selftest", "kind": "translation",
        })

        seg = {
            "id": "seg-001",
            "source_locator": {"file": "paper.md", "locator": "line 1"},
            "source_quote": "The model reports 95.2% accuracy [12].",
            "target_locator": {"file": "translation.md", "locator": "line 2"},
            "target_text": "\u6a21\u578b\u62a5\u544a 95.2% \u7684\u51c6\u786e\u7387 [12]\u3002",
        }
        store.add_evidence("segments", seg)
        store.add_evidence("numbers", {
            "number": "95.2", "unit": "%",
            "source_locator": {"file": "paper.md", "locator": "line 1"},
            "source_quote": "95.2% accuracy",
            "target_text": "95.2% \u7684\u51c6\u786e\u7387", "verified": True,
        })
        store.add_evidence("citations", {
            "citation": "[12]",
            "source_locator": {"file": "paper.md", "locator": "line 1"},
            "source_quote": "[12]",
            "target_text": "[12]", "preserved": True,
        })
        bad, err = store.add_evidence("segments", {"id": "seg-bad", "target_text": "x"})
        assert bad is None and err is not None, "missing locator/quote must be rejected"
        results.append(("add_evidence", True))

        gpath = os.path.join(work, "glossary.json")
        with open(gpath, "w", encoding="utf-8") as fh:
            json.dump({
                "terms": [{"term": "accuracy", "canonical": "\u51c6\u786e\u7387", "variants": ["\u7cbe\u5ea6", "\u51c6\u786e\u5ea6"]}]
            }, fh, ensure_ascii=False)

        report_path = os.path.join(work, "report.json")
        rep, cerr = core.check_consistency(result_path, gpath, report_path)
        assert cerr is None, cerr
        assert rep["counts"]["errors"] == 0, rep
        results.append(("check", True))

        data, err = store.finalize("success")
        assert err is None, err
        results.append(("finalize", True))

        data2, _ = core.load_json_safe(result_path)
        problems = core.validate_result(data2)
        assert not problems, problems
        results.append(("validate", True))

        empty = os.path.join(root, "empty")
        os.makedirs(empty, exist_ok=True)
        b2 = core.discover_input(empty)
        assert "case.json" in b2["missing"]
        results.append(("empty_input", True))

        badjson = os.path.join(root, "bad", "case.json")
        os.makedirs(os.path.dirname(badjson), exist_ok=True)
        with open(badjson, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        _, err = core.load_json_safe(badjson)
        assert err is not None
        results.append(("bad_json", True))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    failed = [name for name, ok in results if not ok]
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"selftest: {len(results) - len(failed)}/{len(results)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
