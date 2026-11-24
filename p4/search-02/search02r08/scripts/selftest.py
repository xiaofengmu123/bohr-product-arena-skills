"""selftest.py — robustness battery for the research-question-driven-search Skill.

Run from the skill root:

    python3 -B scripts/selftest.py

It exercises the CLI through the thin wrapper in a throwaway temp directory and
verifies the behaviours the checklist promises: clean --help, empty input,
bad JSON, bad encoding, oversized files, a full happy path with output_contract
echo, byte-identical idempotent re-runs, and all-record screening enforcement.

Exit code 0 = all checks passed; 1 = at least one check failed.
Pure standard library.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
PY = sys.executable

PASSED = []
FAILED = []


def run(args, timeout=60):
    proc = subprocess.run(
        [PY, "-B", MAIN] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print("%-6s %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    return bool(ok)


def write(path, text, encoding="utf-8", mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding=encoding) as fh:
        fh.write(text)


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dirs(root, name):
    base = os.path.join(root, name)
    return (os.path.join(base, "input"), os.path.join(base, "output"), os.path.join(base, "work"))


def test_help(root):
    inp, out, work = dirs(root, "help")
    os.makedirs(inp, exist_ok=True)
    rc, _, _ = run(["--help"])
    return check("--help 退出码 0", rc == 0)


def test_empty_input(root):
    inp, out, work = dirs(root, "empty")
    os.makedirs(inp, exist_ok=True)
    rc, _, _ = run(["run", "--input", inp, "--output", out, "--work", work])
    res = read_json(os.path.join(out, "result.json"))
    return check("空输入目录 → insufficient_evidence 且退出码 0",
                 rc == 0 and res.get("status") == "insufficient_evidence",
                 "rc=%s status=%s" % (rc, res.get("status")))


def test_bad_json(root):
    inp, out, work = dirs(root, "badjson")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "case.json"), "{ this is not json ")
    rc, _, _ = run(["run", "--input", inp, "--output", out, "--work", work])
    res = read_json(os.path.join(out, "result.json"))
    return check("坏 JSON → 不崩溃，记录 input_error/errors",
                 rc == 0 and res.get("status") == "input_error" and bool(res.get("errors")),
                 "rc=%s status=%s" % (rc, res.get("status")))


def test_bad_encoding(root):
    inp, out, work = dirs(root, "badenc")
    os.makedirs(inp, exist_ok=True)
    write_bytes(os.path.join(inp, "case.json"), b"\xff\xfe\x00\x01 broken utf16 \xff")
    rc, _, _ = run(["run", "--input", inp, "--output", out, "--work", work])
    res = read_json(os.path.join(out, "result.json"))
    return check("坏编码 → 不崩溃且 result.json 合法",
                 rc == 0 and res.get("status") in ("input_error", "insufficient_evidence"),
                 "rc=%s status=%s" % (rc, res.get("status")))


def test_large_file(root):
    inp, out, work = dirs(root, "large")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    write_bytes(os.path.join(inp, "materials", "huge.pdf"), b"\x00" * (3 * 1024 * 1024))
    write(os.path.join(inp, "task.md"), "# demo task\nfind evidence for X.\n")
    rc, _, _ = run(["run", "--input", inp, "--output", out, "--work", work])
    res = read_json(os.path.join(out, "result.json"))
    return check("超大文件 → 跳过并记录 degradations",
                 rc == 0 and bool(res.get("degradations")),
                 "rc=%s degradations=%d" % (rc, len(res.get("degradations", []))))


def test_full_pipeline(root):
    inp, out, work = dirs(root, "full")
    os.makedirs(os.path.join(inp, "materials"), exist_ok=True)
    write(os.path.join(inp, "case.json"), json.dumps({
        "id": "case-demo-001",
        "research_question": "Does intervention X improve outcome Y compared with Z over 2010-2024?",
        "output_contract": {"papers": [], "evidence_map": None, "screening_ledger": []},
    }, ensure_ascii=False))
    write(os.path.join(inp, "task.md"), "# demo task\n复杂科研问题检索。\n")
    write(os.path.join(inp, "materials", "paper-a.md"), "# Paper A\nactual abstract text\n")

    plan = {
        "research_question": "Does intervention X improve outcome Y compared with Z over 2010-2024?",
        "key_concepts": [{"term": "intervention X", "variants": ["X", "treatment X"]}],
        "sub_questions": [{
            "id": "SQ1",
            "question": "Efficacy of X vs Z on Y",
            "object": "intervention X",
            "mechanism": "improve outcome Y",
            "method": "randomized controlled trial",
            "time_range": "2010-2024",
            "comparison": "vs Z",
            "evidence_requirement": "head-to-head effect size",
            "required_evidence": ["effect size of X vs Z"],
            "status": "covered",
        }],
        "query_plan": [{
            "id": "Q1", "sub_question_ids": ["SQ1"], "query": "X versus Z outcome Y",
            "database": "paper_search", "dependencies": [], "order": 1, "status": "done",
            "rationale": "core comparison query",
        }],
        "term_expansion": [{"concept": "outcome Y", "variants": ["Y", "Y score"], "lang": "en", "note": ""}],
        "stop_decision": {"status": "sufficient", "reason": "SQ1 covered by one included record", "marginal_gain": "low"},
    }
    write(os.path.join(work, "plan_in.json"), json.dumps(plan, ensure_ascii=False))

    records = [
        {"record_id": "R1", "title": "Real Paper A", "authors": ["Doe J"], "year": 2020,
         "journal": "Journal of X", "doi": "10.1000/xyz", "url": "https://doi.org/10.1000/xyz",
         "database": "paper_search", "sub_question_ids": ["SQ1"], "relevance": 0.9,
         "relevance_reason": "reports head-to-head X vs Z on Y",
         "screening": "included", "screening_reason": "direct evidence for SQ1",
         "evidence": [{"locator": "materials/paper-a.md#L3", "quote": "actual abstract text"}]},
        {"record_id": "R2", "title": "Unrelated Paper", "authors": ["Roe S"], "year": 2019,
         "journal": "Other Journal", "doi": "10.1000/abc",
         "database": "paper_search", "screening": "excluded",
         "screening_reason": "wrong outcome population"},
        {"record_id": "R3", "title": "Grey Abstract", "authors": None, "year": None, "journal": None,
         "doi": None, "database": "web_search", "screening": "uncertain",
         "screening_reason": "abstract only, no full text"},
        {"record_id": "R4", "title": "Real Paper A (duplicate export)", "authors": ["Doe J"], "year": 2020,
         "journal": "Journal of X", "doi": "10.1000/xyz", "database": "openalex",
         "screening": "merged_duplicate", "screening_reason": "same DOI as R1",
         "merged_into": "R1"},
    ]
    write(os.path.join(work, "records_in.json"), json.dumps(records, ensure_ascii=False))

    evidence = [
        {"id": "E1", "kind": "query", "source": "paper_search", "database": "paper_search",
         "timestamp": "2026-01-01T00:00:00Z", "query": "X versus Z outcome Y",
         "rationale": "planned core query", "result_count": 4},
        {"id": "E2", "kind": "conflict", "source": "papers", "database": "materials",
         "timestamp": "2026-01-01T00:01:00Z", "query": None,
         "rationale": "R1 and R4 are duplicate exports of the same DOI; merged, not reconciled"},
    ]
    write(os.path.join(work, "evidence_in.json"), json.dumps(evidence, ensure_ascii=False))

    meta = {"status": "ok", "status_notes": "SQ1 covered; duplicates merged; conflicts flagged",
            "limitations": ["single case demo"], "pending_human": []}
    write(os.path.join(work, "meta_in.json"), json.dumps(meta, ensure_ascii=False))

    steps = [
        ["run", "--input", inp, "--output", out, "--work", work],
        ["set-plan", "--file", os.path.join(work, "plan_in.json"), "--work", work],
        ["import", "--file", os.path.join(work, "records_in.json"), "--work", work],
        ["evidence", "--file", os.path.join(work, "evidence_in.json"), "--work", work],
        ["set-meta", "--file", os.path.join(work, "meta_in.json"), "--work", work],
        ["assemble", "--input", inp, "--output", out, "--work", work, "--runtime-seconds", "120", "--runtime-requests", "7"],
    ]
    for args in steps:
        rc, so, se = run(args)
        if rc != 0:
            return check("完整流程命令均退出 0", False, "%s rc=%s %s" % (args[0], rc, (se or so).strip()[:120]))

    rc, so, se = run(["selfcheck", "--output", out, "--work", work])
    selfcheck_ok = rc == 0
    res = read_json(os.path.join(out, "result.json"))
    contract_ok = all(k in res for k in ("papers", "evidence_map", "screening_ledger"))
    results_ok = len(res.get("results", [])) == 1 and res["results"][0].get("score") is not None
    ledger_ok = res.get("screening", {}).get("total") == 4 and not res["screening"].get("unscreened")

    ok = check("完整流程 selfcheck 全 PASS", selfcheck_ok, so.strip().splitlines()[-1] if so else "")
    ok &= check("output_contract 字段已回填", contract_ok)
    ok &= check("纳入结果含 score 与证据", results_ok)
    ok &= check("全量筛选账本覆盖 4 条记录", ledger_ok)
    return ok


def test_idempotency(root):
    inp, out, work = dirs(root, "idem")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "task.md"), "# demo\nresearch question.\n")
    write(os.path.join(inp, "case.json"), json.dumps({"id": "case-idem", "output_contract": {"x": None}}))
    run(["run", "--input", inp, "--output", out, "--work", work])
    first = open(os.path.join(out, "result.json"), "rb").read()
    run(["run", "--input", inp, "--output", out, "--work", work])
    second = open(os.path.join(out, "result.json"), "rb").read()
    return check("重复运行字节级幂等", first == second)


def test_unscreened_detected(root):
    inp, out, work = dirs(root, "unscreened")
    os.makedirs(inp, exist_ok=True)
    write(os.path.join(inp, "task.md"), "# demo\nquestion.\n")
    write(os.path.join(inp, "case.json"), json.dumps({"id": "case-unscreened"}))
    run(["run", "--input", inp, "--output", out, "--work", work])
    write(os.path.join(work, "records_in.json"),
          json.dumps([{"record_id": "R1", "title": "Paper without decision"}]))
    run(["import", "--file", os.path.join(work, "records_in.json"), "--work", work])
    run(["assemble", "--input", inp, "--output", out, "--work", work])
    rc, so, _ = run(["selfcheck", "--output", out, "--work", work])
    return check("未筛选记录被 selfcheck 检出（退出非 0）", rc != 0 and "全量筛选" in so)


def main():
    root = tempfile.mkdtemp(prefix="skill-selftest-")
    try:
        tests = [
            test_help,
            test_empty_input,
            test_bad_json,
            test_bad_encoding,
            test_large_file,
            test_full_pipeline,
            test_idempotency,
            test_unscreened_detected,
        ]
        for fn in tests:
            try:
                fn(root)
            except Exception as exc:  # noqa: BLE001 - a broken test still reports cleanly
                check(fn.__name__ + " 无异常", False, repr(exc))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("-" * 60)
    print("selftest: %d passed, %d failed" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("FAILED:", ", ".join(FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
