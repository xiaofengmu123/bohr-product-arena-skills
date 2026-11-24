#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py：鲁棒性自检 + 产物终检。

默认（无参数）：在临时目录跑鲁棒性矩阵——空输入、缺 case、坏 JSON、坏编码、
UTF-8 BOM、超大文件、嵌套材料、契约字段、幂等。全部通过退出码 0，任一失败退出码 1。
`--check --output <dir>`：对已有 result.json 做契约完整性终检（退出码同上）。
只依赖 Python 标准库。
"""

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import main as scaffold


def put(base, name, files):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    for rel, content in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if isinstance(content, bytes):
            with open(p, "wb") as f:
                f.write(content)
        else:
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
    return d


def run_case(base, name, files):
    inp = put(base, name + "_in", files)
    out = os.path.join(base, name + "_out")
    work = os.path.join(base, name + "_work")
    return scaffold.run_scaffold(inp, out, work)


def disk_result_ok(r):
    p = r.get("result_path")
    if not p or not os.path.isfile(p):
        return False, "no result.json on disk"
    try:
        with open(p, "r", encoding="utf-8") as f:
            json.load(f)
        return True, "disk json valid"
    except Exception as e:
        return False, "disk json invalid: %s" % e


def t_empty(tmp):
    r = run_case(tmp, "empty", {})
    res = r["result"]
    return res["status"] == "insufficient_evidence", "status=%s" % res["status"], r


def t_missing_case(tmp):
    r = run_case(tmp, "missing_case", {"task.md": "# task\n"})
    res = r["result"]
    ok = (res["status"] == "insufficient_evidence"
          and "case.json" in res["uncertainty"]["missing_artifacts"])
    return ok, "status=%s missing=%s" % (res["status"], res["uncertainty"]["missing_artifacts"]), r


def t_bad_json(tmp):
    r = run_case(tmp, "bad_json", {"case.json": '{"case_id": "x", "output_contract": [}'})
    res = r["result"]
    ok = (res["status"] == "input_error"
          and any("case_parse" in e for e in res["errors"]))
    return ok, "status=%s errors=%s" % (res["status"], res["errors"]), r


def t_gb18030(tmp):
    data = '{"case_id": "c1", "output_contract": ["presentation_file"]}'.encode("gb18030")
    r = run_case(tmp, "gb18030", {"case.json": data})
    res = r["result"]
    ok = res["case_id"] == "c1" and "presentation_file" in res
    return ok, "case_id=%s" % res["case_id"], r


def t_bom(tmp):
    data = '{"case_id": "c2", "output_contract": {"speaker_notes": null}}'.encode("utf-8-sig")
    r = run_case(tmp, "bom", {"case.json": data})
    res = r["result"]
    ok = res["case_id"] == "c2" and "speaker_notes" in res
    return ok, "case_id=%s" % res["case_id"], r


def t_oversized(tmp):
    inp = put(tmp, "big_in", {
        "case.json": '{"case_id": "c3"}',
        "materials/huge.txt": "a" * (scaffold.TEXT_MAX_BYTES + 10),
    })
    out = os.path.join(tmp, "big_out")
    work = os.path.join(tmp, "big_work")
    r = scaffold.run_scaffold(inp, out, work)
    big = [x for x in r["manifest"] if x["relpath"].endswith("huge.txt")]
    ok = bool(big) and big[0]["skipped"] and str(big[0]["reason"]).startswith("oversized")
    return ok, "%s" % (big[0]["reason"] if big else "not_found"), r


def t_nested(tmp):
    inp = put(tmp, "nest_in", {
        "case.json": '{"case_id": "c4"}',
        "materials/paper.pdf": b"%PDF-1.4 fake\n",
        "materials/fig3.png": b"\x89PNG fake\n",
        "template/master.pptx": b"PK fake pptx\n",
    })
    out = os.path.join(tmp, "nest_out")
    work = os.path.join(tmp, "nest_work")
    r = scaffold.run_scaffold(inp, out, work)
    m = r["manifest"]
    pdf = [x for x in m if x["relpath"].endswith("paper.pdf")]
    ok = len(m) == 4 and "binary" in {x["kind"] for x in m} and pdf and not pdf[0]["skipped"]
    return ok, "files=%d" % len(m), r


def t_contract(tmp):
    r = run_case(tmp, "contract", {
        "case.json": json.dumps({
            "case_id": "case-001",
            "output_contract": {
                "presentation_file": None, "preview_files": [],
                "page_plan": {}, "speaker_notes": None,
            },
        }),
        "task.md": "# task\n",
    })
    res = r["result"]
    ok = (res["case_id"] == "case-001" and res["status"] == "partial"
          and "presentation_file" in res and "preview_files" in res
          and "page_plan" in res and "speaker_notes" in res
          and "sources" in res and "uncertainty" in res
          and "pending_human" in res and res["change_log"])
    return ok, "case_id=%s status=%s" % (res["case_id"], res["status"]), r


def t_idempotent(tmp):
    inp = put(tmp, "idem_in", {"case.json": '{"case_id": "c5", "output_contract": ["a", "b"]}'})
    out = os.path.join(tmp, "idem_out")
    work = os.path.join(tmp, "idem_work")
    r1 = scaffold.run_scaffold(inp, out, work)
    r2 = scaffold.run_scaffold(inp, out, work)
    a1 = r1["result"]
    a2 = r2["result"]
    ok = (a1["case_id"] == a2["case_id"] and a1["status"] == a2["status"]
          and set(a1["uncertainty"].get("missing_artifacts", [])) == set(a2["uncertainty"].get("missing_artifacts", [])))
    return ok, "status=%s/%s" % (a1["status"], a2["status"]), r1


def read_json_file(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except Exception as e:
        return None, str(e)


def check_output(output_dir):
    path = output_dir if output_dir.endswith(".json") else os.path.join(output_dir, "result.json")
    obj, err = read_json_file(path)
    if obj is None:
        print("FAIL check: result.json 不可读/缺失: %s" % err)
        return 1
    problems = []
    if not isinstance(obj.get("case_id"), str) or not obj["case_id"]:
        problems.append("case_id 缺失或非字符串")
    if obj.get("status") not in ("complete", "partial", "insufficient_evidence", "input_error", "failed"):
        problems.append("status 非法: %r" % obj.get("status"))
    for k in ("sources", "uncertainty", "pending_human", "change_log"):
        if k not in obj:
            problems.append("缺少保留字段 %s" % k)
    if not isinstance(obj.get("change_log"), list) or not obj["change_log"]:
        problems.append("change_log 为空")
    if obj.get("status") == "complete":
        reserved = {"case_id", "status", "sources", "uncertainty", "pending_human",
                    "change_log", "errors", "degradations", "runtime"}
        for k, v in obj.items():
            if k in reserved:
                continue
            if v is None:
                problems.append("complete 但契约字段 %s 为 null" % k)
    if problems:
        for pr in problems:
            print("FAIL check: %s" % pr)
        return 1
    print("PASS check: %s case_id=%s status=%s" % (path, obj.get("case_id"), obj.get("status")))
    return 0


def run_matrix():
    tests = [
        ("empty_input", t_empty),
        ("missing_case", t_missing_case),
        ("bad_json", t_bad_json),
        ("gb18030_encoding", t_gb18030),
        ("utf8_bom", t_bom),
        ("oversized_text_skipped", t_oversized),
        ("nested_materials", t_nested),
        ("valid_contract_fields", t_contract),
        ("idempotent_run", t_idempotent),
    ]
    fails = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                ok, msg, r = fn(tmp)
                if ok:
                    dok, dmsg = disk_result_ok(r)
                    if not dok:
                        ok, msg = False, "disk: " + dmsg
                if not ok:
                    fails += 1
                    print("FAIL %-24s %s" % (name, msg))
                else:
                    print("PASS %-24s %s" % (name, msg))
            except Exception as e:
                fails += 1
                print("FAIL %-24s exception %s: %s" % (name, type(e).__name__, e))
    print("selftest: %d/%d passed" % (len(tests) - fails, len(tests)))
    return 1 if fails else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="鲁棒性自检 / 产物终检")
    p.add_argument("--check", action="store_true", help="对 --output 的 result.json 做终检")
    p.add_argument("--output", default="/app/output")
    args = p.parse_args(argv)
    if args.check:
        return check_output(args.output)
    return run_matrix()


if __name__ == "__main__":
    sys.exit(main())
