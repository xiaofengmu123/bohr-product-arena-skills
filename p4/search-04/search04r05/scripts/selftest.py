# -*- coding: utf-8 -*-
"""selftest.py — 自检脚本（仅标准库）。

在系统临时目录构造合成输入（明确标注为演示，不是正式案例），验证：
  1. 空输入目录 → insufficient_evidence，退出码 0
  2. 坏 JSON → input_error，不崩溃
  3. 正常案例 → 契约逐字段满足，papers.csv / edges.csv / citation_graph.svg 可产出
  4. 确定性：同一输入两次运行内容指纹一致
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import citation_lib as lib


def _fingerprint(result):
    keys = ("case_id", "status", "contract", "results", "seed", "seed_key",
            "research_question", "limitations", "pending_human")
    payload = {k: result.get(k) for k in keys}
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _demo_case():
    return {
        "case_id": "demo-case-001",
        "output_contract": {
            "research_question": {"type": "string"},
            "candidate_papers": {"type": "array"},
            "selection_criteria": {"type": "array"},
            "omissions": {"type": "array"},
        },
    }


def _demo_instrs():
    return [
        {"op": "set", "path": "status", "value": "partial",
         "reason": "demo: begin analysis"},
        {"op": "set", "path": "seed_key", "value": "10.1000/seedpaper",
         "reason": "demo: seed identified"},
        {"op": "set", "path": "research_question", "value": "demo RQ",
         "reason": "demo: contract field"},
        {"op": "set_contract", "field": "candidate_papers",
         "value": ["p1", "p2"], "reason": "demo: contract field"},
        {"op": "set_contract", "field": "selection_criteria",
         "value": ["in-scope"], "reason": "demo: contract field"},
        {"op": "set_contract", "field": "omissions",
         "value": [], "reason": "demo: contract field"},
        {"op": "upsert_paper", "paper": {
            "id": "p1", "title": "Seed Paper", "year": 2020, "doi": "10.1000/seedpaper",
            "authors": ["A"],
            "evidence": [{"locator": "materials/a.pdf#p1", "quote": "we propose"}]},
         "reason": "demo: seed paper"},
        {"op": "upsert_paper", "paper": {
            "id": "p2", "title": "Followup", "year": 2022, "doi": "10.1000/followup",
            "authors": ["B"],
            "evidence": [{"locator": "materials/b.pdf#p2", "quote": "extends"}]},
         "reason": "demo: followup"},
        {"op": "add_edge", "edge": {
            "source": "p2", "target": "p1", "relation": "cites",
            "source_label": "Followup", "target_label": "Seed Paper",
            "evidence": [{"locator": "materials/b.pdf#p4", "quote": "builds on"}]},
         "reason": "demo: forward citation"},
        {"op": "dedupe", "reason": "demo: merge versions"},
        {"op": "set_status", "status": "ok", "reason": "demo: complete"},
    ]


def run():
    tmp = tempfile.mkdtemp(prefix="cit_selftest_")
    results = []
    try:
        results.append(_t_empty(tmp))
        results.append(_t_bad_json(tmp))
        results.append(_t_normal(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    all_ok = True
    for name, passed, msg in results:
        all_ok = all_ok and passed
        print("%s %s - %s" % ("[PASS]" if passed else "[FAIL]", name, msg))
    if not all_ok:
        print("selftest FAILED")
        return 1
    print("selftest ok")
    return 0


def _t_empty(tmp):
    d = os.path.join(tmp, "empty")
    os.makedirs(d, exist_ok=True)
    skel = _bootstrap(d)
    ok = skel.get("status") == "insufficient_evidence" and skel.get("case_id")
    return ("empty_input", ok, "status=%s" % skel.get("status"))


def _t_bad_json(tmp):
    d = os.path.join(tmp, "badjson")
    os.makedirs(d, exist_ok=True)
    _write(os.path.join(d, "case.json"), "{ not valid json")
    skel = _bootstrap(d)
    ok = skel.get("status") == "input_error" and skel.get("errors")
    return ("bad_json", ok, "status=%s errors=%d" % (skel.get("status"), len(skel.get("errors", []))))


def _t_normal(tmp):
    d = os.path.join(tmp, "normal")
    os.makedirs(d, exist_ok=True)
    _write(os.path.join(d, "case.json"), json.dumps(_demo_case(), ensure_ascii=False))
    _write(os.path.join(d, "task.md"), "# demo task\n\ndemo synthetic material (not a formal case)\n")

    def build():
        skel = _bootstrap(d)
        for ins in _demo_instrs():
            lib.apply_instruction(skel, ins)
        lib.finalize_artifacts(skel, d)
        return skel

    r1 = build()
    fp1 = _fingerprint(r1)
    r2 = build()
    fp2 = _fingerprint(r2)

    ok, problems = lib.validate(r1)
    contract_ok = (r1.get("contract") or {}).get("satisfied") is True
    artifacts_ok = all(os.path.isfile(os.path.join(d, f)) for f in
                       ("result.json", "report.md", "papers.csv", "edges.csv", "citation_graph.svg"))
    determinism_ok = fp1 == fp2
    ok_all = ok and contract_ok and artifacts_ok and determinism_ok and not problems
    msg = "contract_satisfied=%s artifacts=%s determinism=%s problems=%s" % (
        contract_ok, artifacts_ok, determinism_ok, problems)
    return ("normal_case", ok_all, msg)


def _bootstrap(input_dir):
    files = lib.scan_input(input_dir)
    case = {}
    err = None
    case_path = os.path.join(input_dir, "case.json")
    if os.path.isfile(case_path):
        obj, e = lib.read_json_file(case_path)
        if e is not None:
            err = e
        else:
            case = obj if isinstance(obj, dict) else {}
    skel = lib.build_skeleton(case, input_dir)
    skel["runtime"]["input_files"] = [
        {"rel": f["rel"], "size": f["size"], "skipped_size": f["skipped_size"]} for f in files
    ]
    if not files:
        skel["status"] = "insufficient_evidence"
        skel["limitations"].append("empty input directory")
    elif err is not None:
        skel["status"] = "input_error"
        skel["errors"].append("case.json unreadable: %s" % err)
    lib.recompute_contract(skel)
    return skel


if __name__ == "__main__":
    sys.exit(run())