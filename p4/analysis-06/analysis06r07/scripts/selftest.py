#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest: run the skill's robustness contract locally, no external deps.

Each scenario builds a fresh input dir and invokes scripts/main.py in a
subprocess, then asserts the documented behaviour. Exits 0 only when every
scenario passes. Suitable to re-run before submission.

Usage:
  python3 -B scripts/selftest.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE / "main.py"

PASS = 0
FAIL = 0
MESSAGES = []


def note(ok, name, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        MESSAGES.append("PASS  %-28s %s" % (name, detail))
    else:
        FAIL += 1
        MESSAGES.append("FAIL  %-28s %s" % (name, detail))


def run_main(input_dir, output_dir, work_dir, extra=None):
    cmd = [sys.executable, "-B", str(MAIN),
           "--input", str(input_dir),
           "--output", str(output_dir),
           "--work", str(work_dir)]
    if extra:
        cmd += extra
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=120)
    result = None
    rp = Path(output_dir) / "result.json"
    if rp.is_file():
        try:
            result = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            result = None
    return proc.returncode, result, proc.stdout.decode("utf-8", "replace")


def make_db(path, with_join=True):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, grp TEXT, "
                 "x REAL, y REAL, z REAL)")
    rows = [(i, "a" if i % 2 == 0 else "b", i * 1.0, i * 2.0, i * 0.5)
            for i in range(1, 21)]
    conn.executemany("INSERT INTO samples VALUES (?,?,?,?,?)", rows)
    if with_join:
        conn.execute("CREATE TABLE meta (gid INTEGER PRIMARY KEY, grp TEXT, "
                     "note TEXT)")
        conn.executemany("INSERT INTO meta VALUES (?,?,?)",
                         [(1, "a", "x"), (2, "b", "y")])
    conn.commit()
    conn.close()


def write_case(root, case_id="selftest-case"):
    case = {"case_id": case_id,
            "output_contract": {"summary": None, "n_samples": None,
                                "groups": None}}
    (Path(root) / "case.json").write_text(
        json.dumps(case, ensure_ascii=False), encoding="utf-8")
    (Path(root) / "task.md").write_text(
        "# Task\n\n统计 samples 表各 grp 分组的 x 均值与样本数。\n",
        encoding="utf-8")


def fresh_root(name):
    root = Path(tempfile.mkdtemp(prefix="selftest_%s_" % name))
    (root / "in").mkdir()
    (root / "out").mkdir()
    (root / "work").mkdir()
    return root


def scenario_normal():
    root = fresh_root("normal")
    write_case(root / "in")
    make_db(str(root / "in" / "data.db"))
    rc, res, out = run_main(root / "in", root / "out", root / "work")
    ok = (rc == 0 and res is not None
          and res.get("case_id") == "selftest-case"
          and res.get("status") in ("ok", "partial", "insufficient_evidence",
                                    "input_error")
          and "summary" in res and "n_samples" in res and "groups" in res
          and isinstance(res.get("sources"), list)
          and isinstance(res.get("change_log"), list)
          and (root / "out" / "report.md").is_file()
          and (root / "out" / "schema.json").is_file()
          and (root / "out" / "summary_stats.csv").is_file()
          and (root / "out" / "evidence_map.json").is_file()
          and any((root / "out" / "figures").glob("*.svg")))
    note(ok, "normal_input", "status=%s" % (res or {}).get("status"))
    shutil.rmtree(root, ignore_errors=True)


def scenario_empty():
    root = fresh_root("empty")
    rc, res, out = run_main(root / "in", root / "out", root / "work")
    ok = (rc == 0 and res is not None
          and res.get("status") == "insufficient_evidence"
          and isinstance(res.get("pending_human"), list)
          and bool(res.get("pending_human")))
    note(ok, "empty_input", "status=%s" % (res or {}).get("status"))
    shutil.rmtree(root, ignore_errors=True)


def scenario_bad_json():
    root = fresh_root("badjson")
    (root / "in" / "case.json").write_text('{"case_id": "broken",', encoding="utf-8")
    make_db(str(root / "in" / "data.db"))
    rc, res, out = run_main(root / "in", root / "out", root / "work")
    ok = (rc == 0 and res is not None
          and res.get("status") in ("input_error", "partial", "insufficient_evidence")
          and isinstance(res.get("errors"), list)
          and any("case.json" in (e.get("item") or "") for e in res.get("errors", [])))
    note(ok, "bad_json", "status=%s errors=%d" % (res.get("status"),
                                                  len(res.get("errors", []))))
    shutil.rmtree(root, ignore_errors=True)


def scenario_bad_encoding():
    root = fresh_root("badenc")
    write_case(root / "in")
    # task.md with bytes that are not valid UTF-8/gb18030; latin-1 fallback.
    (root / "in" / "task.md").write_bytes(b"# Task\n\xff\xfe\x00\x81\x8d binary")
    make_db(str(root / "in" / "data.db"))
    rc, res, out = run_main(root / "in", root / "out", root / "work")
    ok = (rc == 0 and res is not None
          and res.get("status") in ("ok", "partial", "insufficient_evidence",
                                    "input_error")
          and isinstance(res.get("degradations"), list))
    note(ok, "bad_encoding", "status=%s degradations=%d"
         % (res.get("status"), len(res.get("degradations", []))))
    shutil.rmtree(root, ignore_errors=True)


def scenario_oversized():
    root = fresh_root("bigfile")
    write_case(root / "in")
    make_db(str(root / "in" / "data.db"))
    # 3 MiB dictionary file > 2 MiB cap, plus a large plain .txt to skip.
    big = (root / "in" / "big_dict.md")
    big.write_bytes(b"# big\n" + b"x" * (3 * 1024 * 1024))
    rc, res, out = run_main(root / "in", root / "out", root / "work")
    ok = (rc == 0 and res is not None
          and isinstance(res.get("degradations"), list)
          and any("big_dict.md" in (d.get("item") or "") for d in res.get("degradations", [])))
    note(ok, "oversized_file", "status=%s" % (res or {}).get("status"))
    shutil.rmtree(root, ignore_errors=True)


def scenario_default_params():
    # Verify argparse defaults (the documented /input /app/output /app/work).
    sys.path.insert(0, str(HERE))
    import main as main_mod
    p = main_mod.build_parser()
    d = p.parse_args([])
    ok = (d.input == "/input" and d.output == "/app/output" and d.work == "/app/work")
    note(ok, "default_params", "input=%s output=%s work=%s" % (d.input, d.output, d.work))


def scenario_readonly():
    root = fresh_root("readonly")
    write_case(root / "in")
    make_db(str(root / "in" / "data.db"))
    queries = [{"id": "groups", "note": "attempted write",
                "sql": "DELETE FROM samples"}]
    (root / "work" / "queries.json").write_text(
        json.dumps(queries, ensure_ascii=False), encoding="utf-8")
    rc, res, out = run_main(root / "in", root / "out", root / "work")
    blocked = any(q.get("status") in ("blocked", "error")
                  and q.get("id", "").startswith("groups")
                  for q in (res or {}).get("query_log", []))
    ok = (rc == 0 and res is not None and blocked)
    note(ok, "readonly_enforcement", "blocked=%s" % blocked)
    shutil.rmtree(root, ignore_errors=True)


def scenario_determinism():
    root = fresh_root("det")
    write_case(root / "in")
    make_db(str(root / "in" / "data.db"))
    rc1, res1, _ = run_main(root / "in", root / "out", root / "work")
    rc2, res2, _ = run_main(root / "in", root / "out", root / "work")
    if not (res1 and res2):
        note(False, "determinism", "missing result")
        shutil.rmtree(root, ignore_errors=True)
        return
    a = {k: v for k, v in res1.items() if k not in ("runtime", "change_log")}
    b = {k: v for k, v in res2.items() if k not in ("runtime", "change_log")}
    ok = (rc1 == 0 and rc2 == 0 and a == b)
    note(ok, "determinism", "identical=%s" % ok)
    shutil.rmtree(root, ignore_errors=True)


def scenario_valid_queries():
    root = fresh_root("queries")
    write_case(root / "in")
    make_db(str(root / "in" / "data.db"))
    queries = [
        {"id": "n_samples", "note": "count rows",
         "sql": "SELECT COUNT(*) FROM samples"},
        {"id": "groups", "note": "mean x per grp",
         "sql": "SELECT grp, AVG(x), COUNT(*) FROM samples GROUP BY grp "
                "ORDER BY grp"},
    ]
    (root / "work" / "queries.json").write_text(
        json.dumps(queries, ensure_ascii=False), encoding="utf-8")
    rc, res, out = run_main(root / "in", root / "out", root / "work")
    answers = (res or {}).get("answers", {})
    n_ok = isinstance(answers.get("n_samples"), list) and answers.get("n_samples")
    groups_ok = isinstance(answers.get("groups"), list) and len(answers.get("groups", [])) == 2
    ok = (rc == 0 and res is not None and n_ok and groups_ok
          and res.get("status") in ("ok", "partial"))
    note(ok, "valid_queries", "n_samples=%s groups_rows=%s"
         % (answers.get("n_samples"), len(answers.get("groups", []))))
    shutil.rmtree(root, ignore_errors=True)


def main():
    for fn in (scenario_normal, scenario_empty, scenario_bad_json,
               scenario_bad_encoding, scenario_oversized, scenario_default_params,
               scenario_readonly, scenario_determinism, scenario_valid_queries):
        try:
            fn()
        except Exception as e:
            note(False, fn.__name__, "unexpected: %s: %s" % (type(e).__name__, e))
    print("\n".join(MESSAGES))
    print("-" * 60)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
