#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest：空目录 / 坏 JSON / 缺输入 / 契约回填 / 确定性 / 证据核验 六场景自检。

全部通过返回退出码 0，任一失败返回 1。只依赖标准库，import 同目录 main/litqa，
不依赖子进程，避免受限环境下 named-pipe 失败。
"""

import copy
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import main  # noqa: E402
import litqa  # noqa: E402


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def scenario_empty_dir():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        inp, out, work = base / "in", base / "out", base / "work"
        inp.mkdir()
        r = main.run_skeleton(str(inp), str(out), str(work))
        assert r["status"] in main.ALLOWED_STATUS
        assert (out / "result.json").exists()
        assert isinstance(json.loads((out / "result.json").read_text(encoding="utf-8")), dict)
        return "empty_dir", r["status"]


def scenario_bad_json():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        inp, out, work = base / "in", base / "out", base / "work"
        inp.mkdir()
        _write(inp / "case.json", "{ not valid json")
        _write(inp / "task.md", "# task\n")
        r = main.run_skeleton(str(inp), str(out), str(work))
        assert r["status"] == "input_error"
        assert any("case_json_unparsable" in e or "bad_json" in e for e in r["errors"])
        return "bad_json", r["status"]


def scenario_missing_input():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        out, work = base / "out", base / "work"
        r = main.run_skeleton(str(base / "does_not_exist"), str(out), str(work))
        assert r["status"] == "input_error"
        return "missing_input", r["status"]


def scenario_contract_echo():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        inp, out, work = base / "in", base / "out", base / "work"
        inp.mkdir()
        _write(inp / "case.json", json.dumps({
            "case_id": "case-9",
            "output_contract": {
                "properties": {"retrieved": {"type": "array"},
                               "status_summary": {"type": "string"}},
                "required": ["retrieved"],
            },
        }, ensure_ascii=False))
        _write(inp / "task.md", "# t\n")
        _write(inp / "materials" / "paper.txt",
               "We randomized 120 patients.\n\nPrimary outcome was mortality.\n")
        r = main.run_skeleton(str(inp), str(out), str(work))
        assert r["case_id"] == "case-9"
        assert r.get("retrieved") == [] and r.get("status_summary") is None
        assert r["contract_alignment"]["field_count"] == 2
        assert "retrieved" in r["contract_alignment"]["required_fields"]
        return "contract_echo", r["status"]


def scenario_determinism():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        inp = base / "in"
        inp.mkdir()
        _write(inp / "case.json", json.dumps({"case_id": "c1",
            "output_contract": {"properties": {"hits": {"type": "array"}}}}, ensure_ascii=False))
        _write(inp / "task.md", "# t\n")
        _write(inp / "materials" / "a.txt", "Line one.\n\nLine two.\n")

        def run_once(tag):
            out, work = base / ("out_" + tag), base / ("work_" + tag)
            return main.run_skeleton(str(inp), str(out), str(work))

        r1, r2 = run_once("1"), run_once("2")
        for r in (r1, r2):
            r.pop("runtime", None)
            r.pop("change_log", None)
        assert r1 == r2
        return "determinism", "identical"


def scenario_verify():
    texts = [{"source_id": "paper.txt", "kind": "text", "path": "x",
              "text": "We randomized 120 patients.\n\nPrimary outcome was mortality.\n"}]
    locs = litqa.build_locators(texts)
    good = {"assessment": [{"dimension": "research_design", "checkpoint": "D1",
                            "verdict": "supported", "judgment": "RCT",
                            "locator": "paper.txt#p1", "quote": "randomized 120 patients"}],
            "sources": []}
    rep = litqa.verify_quotes(good, locs)
    assert rep["pass"] == 1 and rep["fail"] == 0
    bad = {"assessment": [{"dimension": "research_design", "checkpoint": "D1",
                           "verdict": "supported", "judgment": "RCT",
                           "locator": "paper.txt#p1", "quote": "randomized 999 patients"}],
           "sources": []}
    rep2 = litqa.verify_quotes(bad, locs)
    assert rep2["fail"] == 1 and rep2["pass"] == 0
    return "verify", "pass/fail=%d/%d" % (rep["pass"], rep2["fail"])


def run_all():
    scenarios = [
        scenario_empty_dir, scenario_bad_json, scenario_missing_input,
        scenario_contract_echo, scenario_determinism, scenario_verify,
    ]
    results = []
    for fn in scenarios:
        try:
            name, detail = fn()
            results.append({"scenario": name, "status": "ok", "detail": detail})
        except Exception as exc:  # noqa: BLE001
            results.append({"scenario": fn.__name__, "status": "fail", "detail": repr(exc)})
    ok = all(r["status"] == "ok" for r in results)
    print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run_all())
