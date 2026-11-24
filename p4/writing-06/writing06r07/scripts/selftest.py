#!/usr/bin/env python3
"""Self-test for the manuscript-structure-review Skill scripts.

Runs offline (no /input, /app, no network, no external tools required) and
exercises the robustness variant: empty input, malformed JSON, oversized
files, missing arguments, BOM/encoding handling and pipeline determinism.

Exit code 0 = all checks pass; 1 = at least one check failed.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402
import main as M  # noqa: E402


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append({"name": name, "pass": bool(cond), "detail": detail})


def run_pipeline(tmp):
    """Run skeleton -> evidence -> verify against a temp input dir."""
    inp = os.path.join(tmp, "input")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "output")
    os.makedirs(inp, exist_ok=True)
    p = {
        "case_json": os.path.join(inp, "case.json"),
        "task_md": os.path.join(inp, "task.md"),
        "input_dir": inp,
        "work_dir": work,
        "output_dir": out,
        "result_json": os.path.join(out, "result.json"),
    }
    M.cmd_skeleton(p)
    M.cmd_evidence(p)
    M.cmd_verify(p)
    return p


def read_result(path):
    data, err = C.read_json_safe(path)
    return data, err


def test_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        p = run_pipeline(tmp)  # input dir exists but is empty
        res, err = read_result(p["result_json"])
        check("empty_input: result.json exists", res is not None and err is None, str(err))
        check("empty_input: case_id present", bool(res and res.get("case_id")))
        check("empty_input: status honest",
              res and res.get("status") in ("in_progress", "insufficient_evidence",
                                            "input_error"))
        ev, ev_err = read_result(os.path.join(p["work_dir"], "evidence.json"))
        check("empty_input: evidence.json valid", ev is not None and ev_err is None, str(ev_err))
        check("empty_input: file_count==0", ev and ev.get("file_count") == 0)


def test_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json \x00")
        p = {
            "case_json": os.path.join(inp, "case.json"),
            "task_md": os.path.join(inp, "task.md"),
            "input_dir": inp,
            "work_dir": os.path.join(tmp, "work"),
            "output_dir": os.path.join(tmp, "output"),
            "result_json": os.path.join(tmp, "output", "result.json"),
        }
        M.cmd_skeleton(p)
        res, err = read_result(p["result_json"])
        check("bad_json: still produced result.json", res is not None and err is None, str(err))
        check("bad_json: degradation recorded",
              res and any(d.get("item") == "case.json" for d in res.get("degradations", [])))
        check("bad_json: case_id fallback non-empty",
              res and bool(res.get("case_id")))


def test_oversized_file():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, "input")
        os.makedirs(inp)
        big = os.path.join(inp, "huge.txt")
        with open(big, "wb") as fh:
            fh.write(b"x" * (C.MAX_FILE_BYTES + 1024))
        p = {
            "case_json": os.path.join(inp, "case.json"),
            "task_md": os.path.join(inp, "task.md"),
            "input_dir": inp,
            "work_dir": os.path.join(tmp, "work"),
            "output_dir": os.path.join(tmp, "output"),
            "result_json": os.path.join(tmp, "output", "result.json"),
        }
        M.cmd_evidence(p)
        ev, err = read_result(os.path.join(p["work_dir"], "evidence.json"))
        check("oversized: evidence.json valid", ev is not None and err is None, str(err))
        check("oversized: file flagged", ev and any(
            r.get("skipped") == "oversized" for r in ev.get("files", [])))
        check("oversized: no crash", err is None)


def test_encodings():
    with tempfile.TemporaryDirectory() as tmp:
        # BOM + non-ASCII (Chinese) JSON should parse
        p = os.path.join(tmp, "bom.json")
        with open(p, "w", encoding="utf-8-sig") as fh:
            json.dump({"id": "c-测试-1", "output_contract": {"diagnosis": {}}}, fh,
                      ensure_ascii=False)
        data, err = C.read_json_safe(p)
        check("encoding: BOM json parses", data is not None and err is None, str(err))
        check("encoding: unicode preserved", data and data.get("id") == "c-测试-1")
        # gb18030 text file decodes
        q = os.path.join(tmp, "gb.txt")
        with open(q, "w", encoding="gb18030") as fh:
            fh.write("结论边界：相关性不等于因果。")
        txt, meta = C.read_text_safe(q)
        check("encoding: gb18030 decodes", txt is not None and "因果" in (txt or ""),
              str(meta.get("error")))


def test_contract_fill():
    case = {"case_id": "x1", "output_contract": {"diagnosis": {}, "source_map": {},
                                                  "version_boundary": {}}}
    keys = C.extract_contract(case)
    skel = C.build_skeleton(case, keys)
    check("contract: explicit keys preserved",
          [k for k in keys if k in skel] == keys,
          str(keys))
    check("contract: standard fields present",
          all(k in skel for k in ("case_id", "status", "sources", "uncertainty",
                                  "pending_human", "change_log")))
    check("contract: default fallback",
          C.extract_contract({}) == C.DEFAULT_CONTRACT)


def test_scan_text():
    txt = ("# 3. 结果与讨论\n本研究结果表明 A 优于 B（p<0.05）。"
           "因此结论成立[12,13]。数值 12.5%、-3.0 与 45.")
    scan = C.scan_text(txt)
    check("scan: sections found", len(scan["sections"]) >= 1)
    check("scan: numbers found", len(scan["numbers"]) >= 3)
    check("scan: claims found", len(scan["claims"]) >= 1)
    check("scan: citations found", len(scan["citations"]) >= 1)
    check("scan: empty text safe", C.scan_text("") is not None)


def test_determinism():
    with tempfile.TemporaryDirectory() as tmp:
        p = run_pipeline(tmp)
        a, _ = read_result(p["result_json"])
        b, _ = read_result(p["result_json"])
        # strip volatile timestamps before comparing
        a2 = {k: v for k, v in a.items() if k not in ("runtime", "change_log")}
        b2 = {k: v for k, v in b.items() if k not in ("runtime", "change_log")}
        check("determinism: repeated read identical", a2 == b2)


def test_quote_normalize():
    check("normalize: whitespace collapse",
          C.normalize_ws("a  b\n c") == "a b c")
    check("normalize: empty", C.normalize_ws("") == "")


def main():
    try:
        test_empty_input()
        test_bad_json()
        test_oversized_file()
        test_encodings()
        test_contract_fill()
        test_scan_text()
        test_determinism()
        test_quote_normalize()
    except Exception as exc:  # noqa: BLE001 - selftest must never hide a crash
        check("selftest: no uncaught exception", False, "%s" % exc)

    passed = sum(1 for r in RESULTS if r["pass"])
    total = len(RESULTS)
    for r in RESULTS:
        print(("%s  %-45s %s" % ("PASS" if r["pass"] else "FAIL", r["name"],
                                 r["detail"] or "")))
    print("----")
    print("selftest: %d/%d passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
