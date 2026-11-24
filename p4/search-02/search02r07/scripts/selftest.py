"""selftest.py -- robustness self-test for the search skill package.

Runs scripts/main.py through a set of hermetic scenarios in a temporary
directory and verifies that the package degrades gracefully (exit code 0,
valid result.json, honest status) for:

  1. --help
  2. empty input directory
  3. malformed case.json (bad JSON)
  4. bad-encoding text file
  5. oversized text file (> 2 MB)
  6. full happy-path pipeline (run -> set-plan -> import -> evidence ->
     assemble -> selfcheck)
  7. idempotency (same input twice -> same result.json modulo runtime)

Pure standard library. Usage:  python3 scripts/selftest.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run_main(args, cwd=None):
    return subprocess.run(
        [sys.executable, MAIN] + [str(a) for a in args],
        capture_output=True, text=True, cwd=cwd,
    )


def check(name, ok, detail):
    print("[%s] %s -- %s" % ("PASS" if ok else "FAIL", name, detail))
    return bool(ok)


def read_result(out_dir):
    p = os.path.join(out_dir, "result.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def main():
    base = tempfile.mkdtemp(prefix="skill-selftest-")
    results = []
    try:
        # 1. --help
        r = run_main(["--help"])
        results.append(check("help_exit_0", r.returncode == 0, "exit=%d" % r.returncode))

        # 2. empty input
        d = os.path.join(base, "empty")
        os.makedirs(os.path.join(d, "input"))
        out, work = os.path.join(d, "output"), os.path.join(d, "work")
        r = run_main(["run", "--input", os.path.join(d, "input"), "--output", out, "--work", work])
        res = read_result(out)
        results.append(check("empty_exit_0", r.returncode == 0, "exit=%d" % r.returncode))
        results.append(check("empty_valid_result", res is not None, "result.json 存在且可解析"))
        results.append(check("empty_honest_status", bool(res) and res.get("status") in ("insufficient_evidence", "input_error"),
                             "status=%s" % (res or {}).get("status")))

        # 3. malformed case.json
        d = os.path.join(base, "badjson")
        os.makedirs(os.path.join(d, "input"))
        with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not valid json !!!")
        out, work = os.path.join(d, "output"), os.path.join(d, "work")
        r = run_main(["run", "--input", os.path.join(d, "input"), "--output", out, "--work", work])
        res = read_result(out)
        results.append(check("badjson_exit_0", r.returncode == 0, "exit=%d" % r.returncode))
        results.append(check("badjson_valid_result", res is not None, "result.json 存在且可解析"))
        results.append(check("badjson_errors_recorded", bool(res) and len(res.get("errors") or []) >= 1,
                             "errors=%d" % len((res or {}).get("errors") or [])))

        # 4. bad-encoding text file
        d = os.path.join(base, "badenc")
        os.makedirs(os.path.join(d, "input"))
        with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "c-enc", "research_question": "q"}, fh)
        with open(os.path.join(d, "input", "task.md"), "wb") as fh:
            fh.write(b"task title \xff\xfe\x00\x9c corrupted")
        out, work = os.path.join(d, "output"), os.path.join(d, "work")
        r = run_main(["run", "--input", os.path.join(d, "input"), "--output", out, "--work", work])
        res = read_result(out)
        results.append(check("badenc_exit_0", r.returncode == 0, "exit=%d" % r.returncode))
        results.append(check("badenc_valid_result", res is not None, "result.json 存在且可解析"))

        # 5. oversized text file (> 2 MB)
        d = os.path.join(base, "oversize")
        os.makedirs(os.path.join(d, "input"))
        with open(os.path.join(d, "input", "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "c-big", "research_question": "q"}, fh)
        with open(os.path.join(d, "input", "big.md"), "w", encoding="utf-8") as fh:
            fh.write("a" * (2 * 1024 * 1024 + 100))
        out, work = os.path.join(d, "output"), os.path.join(d, "work")
        r = run_main(["run", "--input", os.path.join(d, "input"), "--output", out, "--work", work])
        res = read_result(out)
        results.append(check("oversize_exit_0", r.returncode == 0, "exit=%d" % r.returncode))
        results.append(check("oversize_degraded", bool(res) and len(res.get("degradations") or []) >= 1,
                             "degradations=%d" % len((res or {}).get("degradations") or [])))

        # 6. full happy path
        d = os.path.join(base, "happy")
        inp, out, work = os.path.join(d, "input"), os.path.join(d, "output"), os.path.join(d, "work")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "case_id": "case-demo",
                "research_question": "近十年 CRISPR 在镰状细胞病中的基因治疗疗效与安全性如何？",
                "output_contract": {"required_papers": {"type": "array"}, "key_findings": {"type": "array"}},
            }, fh, ensure_ascii=False)
        with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# 题目\n检索并筛选相关文献。\n")
        r = run_main(["run", "--input", inp, "--output", out, "--work", work])
        results.append(check("happy_run_exit_0", r.returncode == 0, "exit=%d" % r.returncode))

        plan_path = os.path.join(d, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump({
                "sub_questions": [{"id": "SQ1", "text": "基因编辑疗效", "object": "CRISPR",
                                   "mechanism": "gene editing", "method": "clinical trial",
                                   "time_range": "2016-2026", "comparison": "vs 标准治疗",
                                   "evidence_requirement": "随机对照或队列"}],
                "query_plan": [{"sq_id": "SQ1", "queries": ["CRISPR sickle cell gene therapy"], "dependency": "none"}],
                "term_expansion": [{"concept": "sickle cell", "variants": ["SCD", "镰状细胞病"]}],
                "stop_decision": {"status": "continue", "reason": "首轮未收敛"},
            }, fh, ensure_ascii=False)
        r = run_main(["set-plan", "--file", plan_path, "--work", work])
        results.append(check("happy_setplan_exit_0", r.returncode == 0, "exit=%d" % r.returncode))

        rec_path = os.path.join(d, "rec.json")
        with open(rec_path, "w", encoding="utf-8") as fh:
            json.dump({"records": [{
                "id": "r1", "title": "Example real paper title", "authors": "A, B", "year": 2023,
                "journal": "Nature", "doi": "10.0000/example", "database": "paper_search",
                "sub_question_ids": ["SQ1"], "relevance": 0.9, "relevance_reason": "直接回答疗效",
                "screening": "included", "screening_reason": "高相关且元数据完整",
                "evidence": {"locator": "abstract#p1", "quote": "verbatim quote here"},
            }]}, fh, ensure_ascii=False)
        r = run_main(["import", "--file", rec_path, "--work", work])
        results.append(check("happy_import_exit_0", r.returncode == 0, "exit=%d" % r.returncode))

        ev_path = os.path.join(d, "ev.json")
        with open(ev_path, "w", encoding="utf-8") as fh:
            json.dump({"evidence": [{"query": "CRISPR sickle cell gene therapy", "source": "paper_search",
                                     "database": "paper_search", "rationale": "首轮检索", "kind": "query"}]}, fh, ensure_ascii=False)
        r = run_main(["evidence", "--file", ev_path, "--work", work])
        results.append(check("happy_evidence_exit_0", r.returncode == 0, "exit=%d" % r.returncode))

        r = run_main(["assemble", "--input", inp, "--output", out, "--work", work,
                      "--runtime-seconds", 120, "--runtime-requests", 7])
        results.append(check("happy_assemble_exit_0", r.returncode == 0, "exit=%d" % r.returncode))

        r = run_main(["selfcheck", "--output", out, "--work", work])
        results.append(check("happy_selfcheck_pass", r.returncode == 0, "exit=%d" % r.returncode))

        res = read_result(out)
        results.append(check("happy_contract_echo", bool(res) and res.get("required_papers") == [] and res.get("key_findings") == [],
                             "output_contract 回填 required_papers/key_findings"))
        results.append(check("happy_case_id", bool(res) and res.get("case_id") == "case-demo", "case_id=%r" % (res or {}).get("case_id")))

        # 7. idempotency
        d2 = os.path.join(base, "idem")
        inp2 = os.path.join(d2, "input")
        os.makedirs(inp2)
        with open(os.path.join(inp2, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "c-idem", "research_question": "q"}, fh)
        outA, outB = os.path.join(d2, "outA"), os.path.join(d2, "outB")
        run_main(["run", "--input", inp2, "--output", outA, "--work", os.path.join(d2, "wA")])
        run_main(["run", "--input", inp2, "--output", outB, "--work", os.path.join(d2, "wB")])
        a, b = read_result(outA), read_result(outB)
        same = False
        if a is not None and b is not None:
            a2 = {k: v for k, v in a.items() if k != "runtime"}
            b2 = {k: v for k, v in b.items() if k != "runtime"}
            same = a2 == b2
        results.append(check("idempotent", same, "两次运行 result.json（除 runtime）一致"))

    finally:
        shutil.rmtree(base, ignore_errors=True)

    n_pass = sum(results)
    print("\n%d/%d checks passed" % (n_pass, len(results)))
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
