#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — self-test for the cross-lingual literature-search skill.

Runs offline checks without fabricating benchmark scores or real results:
  1. main.py --help exits 0
  2. empty input dir -> init writes legal result.json (input_error / partial)
  3. bad JSON case -> errors/degradations recorded, no crash
  4. a contract in task.md -> all contract keys preserved in result.json
  5. evidence-map validator flags missing locator/quote and duplicate ids
  6. dedup / language helpers behave deterministically

Standard library only. Exit code 0 when all checks pass.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
sys.path.insert(0, HERE)
import core_impl as cl  # noqa: E402

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def run(args, **kw):
    return subprocess.run(
        [sys.executable, "-B", MAIN] + args,
        capture_output=True, text=True, **kw)


def main():
    tmp = tempfile.mkdtemp(prefix="cls_selftest_")
    try:
        # 1. --help exits 0
        p = run(["--help"])
        check("main.py --help exits 0", p.returncode == 0, p.stderr)

        # 2. empty input dir
        empty_in = os.path.join(tmp, "empty_in")
        empty_out = os.path.join(tmp, "empty_out")
        empty_work = os.path.join(tmp, "empty_work")
        os.makedirs(empty_in)
        p = run(["init", "--input", empty_in, "--output", empty_out,
                 "--work", empty_work])
        check("empty input: init exits 0", p.returncode == 0, p.stderr)
        rpath = os.path.join(empty_out, "result.json")
        obj = json.load(open(rpath, encoding="utf-8"))
        check("empty input: status input_error",
              obj.get("status") == "input_error", obj.get("status"))
        check("empty input: case_id present", bool(obj.get("case_id")))

        # 3. bad JSON case file
        bad_in = os.path.join(tmp, "bad_in")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "c1", "output_contract": {"answer": "x"')
        bad_out = os.path.join(tmp, "bad_out")
        bad_work = os.path.join(tmp, "bad_work")
        p = run(["init", "--input", bad_in, "--output", bad_out, "--work", bad_work])
        check("bad JSON: init exits 0", p.returncode == 0, p.stderr)
        bobj = json.load(open(os.path.join(bad_out, "result.json"), encoding="utf-8"))
        check("bad JSON: error recorded",
              any("bad_json" in str(e) for e in bobj.get("errors", [])), bobj.get("errors"))

        # 4. contract inside task.md
        t_in = os.path.join(tmp, "task_in")
        os.makedirs(t_in)
        with open(os.path.join(t_in, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "c2"}, fh)
        task_md = ('task text output_contract = {"title": "T", "items": ["a"],'
                   ' "meta": {"lang": "pt"}} end')
        with open(os.path.join(t_in, "task.md"), "w", encoding="utf-8") as fh:
            fh.write(task_md)
        t_out = os.path.join(tmp, "task_out")
        t_work = os.path.join(tmp, "task_work")
        run(["init", "--input", t_in, "--output", t_out, "--work", t_work])
        tobj = json.load(open(os.path.join(t_out, "result.json"), encoding="utf-8"))
        check("contract keys preserved: title", "title" in tobj)
        check("contract keys preserved: items", tobj.get("items") == [])
        check("contract nested: meta.lang", tobj.get("meta") == {"lang": None},
              str(tobj.get("meta")))

        # 5. evidence validator
        good_em = [{
            "evidence_id": "ev-1", "claim": "paper is Portuguese original",
            "locator": {"source": "material", "path": "a.pdf#p1", "offset": "para 2"},
            "quote": "resumo", "status": "verified",
        }]
        full = cl.build_skeleton("c", None)
        full["status"] = "ok"
        full["evidence_map"] = good_em
        ok_issues, ok_flag = cl.validate_result(full)
        check("validator accepts good evidence", ok_flag, str(ok_issues))

        bad_em = [{"evidence_id": "ev-1", "claim": "x"}]
        issues, flag = cl.validate_result({
            "case_id": "c", "status": "ok", "evidence_map": bad_em,
        })
        check("validator flags missing locator/quote",
              flag is False and any("missing_" in i for i in issues), str(issues))

        dup_em = [good_em[0], good_em[0]]
        _, dup_flag = cl.validate_result({
            "case_id": "c", "status": "ok", "evidence_map": dup_em,
        })
        check("validator flags duplicate evidence ids", dup_flag is False)

        # 6. determinism of dedup / language helpers
        t1 = cl.normalize_title("Pesquisa sobre Células: um Estudo Novo")
        t2 = cl.normalize_title("pesquisa sobre celulas um estudo novo")
        check("title normalization deterministic + diacritic-insensitive", t1 == t2)
        check("doi normalization", cl.normalize_doi("https://doi.org/10.1/x")
              == "10.1/x")
        check("quote_present whitespace-tolerant",
              cl.quote_present("a  b\nc", "a b c"))
        check("language fallback pt", cl.detect_language("resumo pesquisa artigo não") == "pt")
        check("language fallback en", cl.detect_language("the research article abstract") == "en")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
