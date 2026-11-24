"""selftest.py — robustness self-test (in-process, standard library only).

Calls main.py's command handlers directly (no subprocess output capture, which may
be blocked in sandboxed runtimes) across five scenarios and prints PASS/FAIL per
scenario. Always exits 0; pass --strict to exit non-zero when any scenario fails.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core_impl as impl  # noqa: E402


def make_args(**kw):
    d = {"input": None, "output": None, "work": None, "file": None, "inline": None,
         "force": False, "runtime_seconds": 0.0, "runtime_requests": 0}
    d.update(kw)
    return types.SimpleNamespace(**d)


def write_case(dirpath, files):
    for rel, content in files.items():
        p = os.path.join(dirpath, rel)
        parent = os.path.dirname(p)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if isinstance(content, bytes):
            with open(p, "wb") as fh:
                fh.write(content)
        else:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(content)


def _read_result(out):
    return json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))


def scenario_empty_input():
    tmp = tempfile.mkdtemp(prefix="sst_empty_")
    ind, out, work = (os.path.join(tmp, "input"), os.path.join(tmp, "output"), os.path.join(tmp, "work"))
    os.makedirs(ind, exist_ok=True)
    impl.cmd_run(make_args(input=ind, output=out, work=work))
    r = _read_result(out)
    ok = r.get("status") == "insufficient_evidence" and "case_id" in r
    shutil.rmtree(tmp, ignore_errors=True)
    return "empty input dir", ok, r.get("status")


def scenario_bad_json():
    tmp = tempfile.mkdtemp(prefix="sst_badjson_")
    ind, out, work = (os.path.join(tmp, "input"), os.path.join(tmp, "output"), os.path.join(tmp, "work"))
    os.makedirs(ind, exist_ok=True)
    write_case(ind, {"case.json": "{not valid json!!", "task.md": "# 任务\n这是任务说明"})
    impl.cmd_run(make_args(input=ind, output=out, work=work))
    r = _read_result(out)
    ok = r.get("status") in ("input_error", "partial") and bool(r.get("degradations"))
    shutil.rmtree(tmp, ignore_errors=True)
    return "bad JSON input", ok, r.get("status")


def scenario_bad_encoding():
    tmp = tempfile.mkdtemp(prefix="sst_badenc_")
    ind, out, work = (os.path.join(tmp, "input"), os.path.join(tmp, "output"), os.path.join(tmp, "work"))
    os.makedirs(ind, exist_ok=True)
    write_case(ind, {
        "case.json": '{"case_id":"c1","output_contract":{"candidates":{},"evidence_log":{}}}',
        "notes.md": b"\xff\xfe\x00\x01badbytes",
    })
    impl.cmd_run(make_args(input=ind, output=out, work=work))
    r = _read_result(out)
    ok = r.get("status") in ("partial", "ok") and "candidates" in r and "evidence_log" in r
    shutil.rmtree(tmp, ignore_errors=True)
    return "bad encoding tolerated", ok, r.get("status")


def scenario_full_flow():
    tmp = tempfile.mkdtemp(prefix="sst_flow_")
    ind, out, work = (os.path.join(tmp, "input"), os.path.join(tmp, "output"), os.path.join(tmp, "work"))
    os.makedirs(ind, exist_ok=True)
    write_case(ind, {
        "case.json": json.dumps({
            "case_id": "case-1",
            "research_question": "X 疗法对 Y 病的疗效与机制？",
            "output_contract": {"candidates": {}, "evidence_log": {}, "summary": {}},
        }),
        "task.md": "# 案例 1\n研究问题：X 疗法对 Y 病的疗效与机制",
    })
    impl.cmd_run(make_args(input=ind, output=out, work=work))
    plan = {"sub_questions": [
        {"id": "SQ1", "question": "X 疗法的疗效", "object": "X", "mechanism": "疗效",
         "evidence_requirement": "RCT"},
        {"id": "SQ2", "question": "X 疗法的作用机制", "object": "X", "mechanism": "机制",
         "evidence_requirement": "机制研究"},
    ]}
    write_case(work, {"plan_in.json": json.dumps(plan)})
    impl.cmd_set_plan(make_args(file=os.path.join(work, "plan_in.json"), output=out, work=work))
    rec = {"title": "X 治疗 Y 的随机对照试验", "authors": ["A", "B"], "year": 2020,
           "journal": "J", "doi": "10.0/example", "database": "paper_search",
           "sub_question_ids": ["SQ1"], "relevance": 1.0, "relevance_reason": "直接回答疗效",
           "screening": "include", "screening_reason": "符合子问题 SQ1",
           "evidence": {"locator": "abstract#1", "quote": "X significantly improved Y (p<0.01)"}}
    write_case(work, {"rec_in.json": json.dumps([rec])})
    impl.cmd_import(make_args(file=os.path.join(work, "rec_in.json"), output=out, work=work))
    ev = {"query": "X therapy Y disease", "source": "paper_search", "database": "paper_search",
          "timestamp": "2026-01-01T00:00:00Z", "rationale": "round 1", "kind": "query"}
    write_case(work, {"ev_in.json": json.dumps([ev])})
    impl.cmd_evidence(make_args(file=os.path.join(work, "ev_in.json"), output=out, work=work))
    stop = {"stop_decision": {"status": "done", "reason": "SQ1/SQ2 均有证据",
                              "coverage": {"SQ1": 1, "SQ2": 0}}}
    write_case(work, {"stop.json": json.dumps(stop)})
    impl.cmd_set_plan(make_args(file=os.path.join(work, "stop.json"), output=out, work=work))
    fields = {"candidates": "results", "evidence_log": "evidence", "summary": "plan.md"}
    write_case(work, {"fields.json": json.dumps(fields)})
    impl.cmd_set_fields(make_args(file=os.path.join(work, "fields.json"), output=out, work=work))
    impl.cmd_assemble(make_args(input=ind, output=out, work=work, runtime_seconds=120.0, runtime_requests=7))
    impl.cmd_selfcheck(make_args(output=out, work=work))
    r = _read_result(out)
    contract_ok = all(k in r for k in ("candidates", "evidence_log", "summary"))
    results_ok = len(r.get("results", [])) == 1 and r["results"][0].get("rank") == 1
    ledger_ok = r.get("screening", {}).get("totals", {}).get("included") == 1
    ok = r.get("status") == "ok" and contract_ok and results_ok and ledger_ok
    shutil.rmtree(tmp, ignore_errors=True)
    return "full incremental flow", ok, r.get("status")


def scenario_idempotent():
    tmp = tempfile.mkdtemp(prefix="sst_idem_")
    ind, out, work = (os.path.join(tmp, "input"), os.path.join(tmp, "output"), os.path.join(tmp, "work"))
    os.makedirs(ind, exist_ok=True)
    write_case(ind, {"case.json": json.dumps({"case_id": "c2", "output_contract": {"items": {}}})})
    impl.cmd_run(make_args(input=ind, output=out, work=work))
    plan = {"sub_questions": [{"id": "SQ1", "question": "q"}]}
    write_case(work, {"p.json": json.dumps(plan)})
    impl.cmd_set_plan(make_args(file=os.path.join(work, "p.json"), output=out, work=work))
    impl.cmd_run(make_args(input=ind, output=out, work=work))
    r = _read_result(out)
    ok = len(r.get("sub_questions", [])) == 1
    shutil.rmtree(tmp, ignore_errors=True)
    return "idempotent / no wipe on re-run", ok, r.get("status")


def main():
    ap = argparse.ArgumentParser(description="robustness self-test for the search Skill")
    ap.add_argument("--strict", action="store_true", help="exit non-zero on any FAIL")
    args = ap.parse_args()
    scenarios = [scenario_empty_input, scenario_bad_json, scenario_bad_encoding,
                 scenario_full_flow, scenario_idempotent]
    results = []
    for fn in scenarios:
        try:
            name, ok, status = fn()
            results.append((name, ok, status))
        except Exception as exc:  # noqa: BLE001
            results.append((getattr(fn, "__name__", "scenario"), False, "exception: %s" % exc))
    n_ok = sum(1 for _name, ok, _status in results if ok)
    for name, ok, status in results:
        print("%s  %-32s  status=%s" % ("PASS" if ok else "FAIL", name, status))
    print("summary: %d/%d PASS" % (n_ok, len(results)))
    if args.strict and n_ok < len(results):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
