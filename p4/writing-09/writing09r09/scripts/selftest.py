#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for results-discussion-writing scripts (standard library only).

Checks three robustness scenarios plus a minimal happy path:
  1. minimal demo -> valid partial result.json with case_id + contract fields
  2. empty input dir -> exit 0, status=partial, errors/degradations recorded
  3. bad JSON case -> exit 0, errors recorded, result.json still written

Run:  python scripts/selftest.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run_main(args):
    return subprocess.run([sys.executable, MAIN] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def scenario_minimal(tmp):
    d = os.path.join(tmp, "minimal")
    inp, work, out = os.path.join(d, "input"), os.path.join(d, "work"), os.path.join(d, "out")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"),
          json.dumps({"case_id": "demo-case",
                      "output_contract": {"deliverable": None,
                                          "facts_sources": [],
                                          "version_boundaries": {}}},
                     ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# 结果与讨论章节写作\n请依据材料写结果段与讨论段。")
    write(os.path.join(inp, "material.md"), "# 材料\n图1：A组 12.3 ± 1.1，B组 15.8 ± 1.4，p=0.021。")
    r = run_main(["init", "--case", os.path.join(inp, "case.json"),
                  "--task", os.path.join(inp, "task.md"),
                  "--input-dir", inp, "--work", work, "--out", out])
    result_path = os.path.join(out, "result.json")
    ok = r.returncode == 0 and os.path.exists(result_path)
    if ok:
        res = json.load(open(result_path, encoding="utf-8"))
        ok = (res.get("case_id") == "demo-case" and res.get("status") == "partial"
              and "deliverable" in res and "facts_sources" in res
              and "version_boundaries" in res and "pending_human" in res)
    return ok, r, "minimal happy path"


def scenario_empty(tmp):
    d = os.path.join(tmp, "empty")
    inp, work, out = os.path.join(d, "input"), os.path.join(d, "work"), os.path.join(d, "out")
    os.makedirs(inp)
    r = run_main(["init", "--case", os.path.join(inp, "case.json"),
                  "--task", os.path.join(inp, "task.md"),
                  "--input-dir", inp, "--work", work, "--out", out])
    result_path = os.path.join(out, "result.json")
    ok = r.returncode == 0 and os.path.exists(result_path)
    if ok:
        res = json.load(open(result_path, encoding="utf-8"))
        ok = res.get("status") == "partial" and bool(res.get("errors"))
    return ok, r, "empty input dir"


def scenario_badjson(tmp):
    d = os.path.join(tmp, "badjson")
    inp, work, out = os.path.join(d, "input"), os.path.join(d, "work"), os.path.join(d, "out")
    os.makedirs(inp)
    write(os.path.join(inp, "case.json"), "{ this is not json")
    write(os.path.join(inp, "task.md"), "任务：写结果与讨论。")
    write(os.path.join(inp, "material.md"), "材料片段。")
    r = run_main(["init", "--case", os.path.join(inp, "case.json"),
                  "--task", os.path.join(inp, "task.md"),
                  "--input-dir", inp, "--work", work, "--out", out])
    result_path = os.path.join(out, "result.json")
    ok = r.returncode == 0 and os.path.exists(result_path)
    if ok:
        res = json.load(open(result_path, encoding="utf-8"))
        ok = bool(res.get("errors"))
    return ok, r, "bad JSON case"


def main():
    tmp = tempfile.mkdtemp(prefix="skill-selftest-")
    results = []
    try:
        for fn in (scenario_minimal, scenario_empty, scenario_badjson):
            try:
                ok, r, name = fn(tmp)
                results.append({"name": name, "ok": ok, "rc": r.returncode})
            except Exception as exc:  # noqa: BLE001
                results.append({"name": fn.__name__, "ok": False, "rc": None,
                                "error": repr(exc)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    all_ok = all(x["ok"] for x in results)
    print(json.dumps({"all_ok": all_ok, "results": results}, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
