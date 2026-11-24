#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sci-analysis-exec-robust 自检脚本（纯标准库）。

覆盖鲁棒性场景：空输入、坏 JSON、坏编码、缺参数、超大文件、契约回填、
原子增量 set/log、字段对齐。全部场景跑完打印 PASS/FAIL；仅当存在 FAIL 时退出码为 1。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

import core_impl as C
import main as M


def _mktree(files: dict) -> str:
    """在临时目录中写 {relpath: bytes}，返回目录。"""
    d = tempfile.mkdtemp(prefix="skill-selftest-")
    for rel, data in files.items():
        p = os.path.join(d, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p) or d, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    return d


def _case_json(case_id="case-001", contract=None):
    obj = {"case_id": case_id, "output_contract": contract or {}}
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _scenario(name):
    results = []

    def wrap(fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, "EXCEPTION:%s\n%s" % (e, traceback.format_exc())
        results.append((name, ok, detail))
    return results, wrap


def run_all():
    results = []

    # 1. 空输入目录 -> insufficient_evidence，退出码 0
    def s_empty():
        d = _mktree({})
        out = os.path.join(d, "out")
        r = C.run_init(d, out, os.path.join(d, "work"))
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            obj = json.load(fh)
        ok = r["status"] == "insufficient_evidence" and obj["case_id"] is None
        return ok, "status=%s" % r["status"]

    # 2. 坏 JSON case.json -> input_error + errors 记录，不崩溃
    def s_badjson():
        d = _mktree({"case.json": b'{"case_id": "case-001", '})
        out = os.path.join(d, "out")
        r = C.run_init(d, out, os.path.join(d, "work"))
        ok = r["status"] == "input_error" and any("json" in e for e in r["errors"])
        return ok, "status=%s errors=%s" % (r["status"], r["errors"])

    # 3. 坏编码（GB18030 中文）case.json -> 正确解码并回填 case_id
    def s_badenc():
        payload = json.dumps({"case_id": "gbk-案例-1", "output_contract": {"metric": {"type": "list"}}},
                             ensure_ascii=False).encode("gb18030")
        d = _mktree({"case.json": payload})
        out = os.path.join(d, "out")
        r = C.run_init(d, out, os.path.join(d, "work"))
        ok = r["case_id"] == "gbk-案例-1" and r["status"] == "partial" and isinstance(r.get("metric"), list)
        return ok, "case_id=%s status=%s" % (r.get("case_id"), r.get("status"))

    # 4. 缺参数 -> 环境变量默认目录可用，init 退出码 0 且落盘合法 result.json
    def s_missing_args():
        d = _mktree({"case.json": _case_json("case-missing-args", {"answer": {"type": "list"}})})
        old = {k: os.environ.get(k) for k in ("SKILL_INPUT_DIR", "SKILL_OUTPUT_DIR", "SKILL_WORK_DIR")}
        os.environ["SKILL_INPUT_DIR"] = d
        os.environ["SKILL_OUTPUT_DIR"] = os.path.join(d, "out")
        os.environ["SKILL_WORK_DIR"] = os.path.join(d, "work")
        try:
            code = M.main(["init"])
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        with open(os.path.join(d, "out", "result.json"), encoding="utf-8") as fh:
            obj = json.load(fh)
        ok = code == 0 and obj["case_id"] == "case-missing-args"
        return ok, "exit=%s case_id=%s" % (code, obj.get("case_id"))

    # 5. 超大文件 -> profile 跳过并记录 file_too_large，不崩溃
    def s_oversized():
        d = _mktree({"case.json": _case_json("case-big", {}),
                     "big.csv": b"a,b\n" + b"1,2\n" * 10})
        # 再写入一个超过 5MB 的文件
        big = os.path.join(d, "huge.csv")
        with open(big, "wb") as fh:
            fh.write(b"x,y\n")
            fh.write(b"0" * (C.MAX_TEXT_FILE_BYTES + 1024))
        out = os.path.join(d, "out")
        C.run_profile(d, out, os.path.join(d, "work"))
        with open(os.path.join(out, "data_profile.json"), encoding="utf-8") as fh:
            prof = json.load(fh)
        huge = [e for e in prof["files"] if e["rel"] == "huge.csv"]
        ok = bool(huge) and "file_too_large" in json.dumps(huge[0].get("profile", {}), ensure_ascii=False)
        return ok, "huge.csv profile=%s" % huge[0].get("profile") if huge else "huge.csv missing"

    # 6. 契约回填 -> case_id + 全部 output_contract 字段 + status/sources/uncertainty/change_log
    def s_contract():
        contract = {"summary": {"type": "object"}, "figures": {"type": "list"},
                    "cleaned_table": {"type": "string"}}
        d = _mktree({"case.json": _case_json("case-contract", contract), "data.csv": b"a,b\n1,2\n"})
        out = os.path.join(d, "out")
        r = C.run_init(d, out, os.path.join(d, "work"))
        ok = (r["case_id"] == "case-contract" and "summary" in r and "figures" in r
              and r["figures"] == [] and r["cleaned_table"] is None
              and isinstance(r["change_log"], list) and r["sources"] == [])
        return ok, "fields=%s" % sorted(r.get("output_contract_fields", []))

    # 7. set/log 原子增量 -> 不损坏 JSON，字段与 change_log 同步
    def s_set_log():
        d = _mktree({"case.json": _case_json("case-setlog", {"cleaned_table": {"type": "string"}})})
        out = os.path.join(d, "out")
        C.run_init(d, out, os.path.join(d, "work"))
        C.run_set(out, "cleaned_table", "result_clean.csv", "str")
        C.run_log(out, "clean", "去重完成", status="partial", note="150->149")
        with open(os.path.join(out, "result.json"), encoding="utf-8") as fh:
            obj = json.load(fh)
        ok = obj["cleaned_table"] == "result_clean.csv" and len(obj["change_log"]) >= 3
        return ok, "cleaned_table=%s change_log=%d" % (obj.get("cleaned_table"), len(obj["change_log"]))

    # 8. 字段对齐 -> alignment.json 生成且 matched_count 正确
    def s_align():
        d = _mktree({
            "case.json": _case_json("case-align", {}),
            "data.csv": "Cement,Slag,Compressive\n100,50,30\n".encode("utf-8"),
        })
        dic = os.path.join(d, "dict.json")
        with open(dic, "w", encoding="utf-8") as fh:
            json.dump({"fields": [{"name": "cement", "aliases": ["Cement", "水泥"]},
                                  {"name": "compressive_strength", "aliases": ["Compressive"]}]}, fh,
                      ensure_ascii=False)
        out = os.path.join(d, "out")
        C.run_align(dic, d, out)
        with open(os.path.join(out, "alignment.json"), encoding="utf-8") as fh:
            al = json.load(fh)
        ok = al["matched_count"] == 2 and os.path.isfile(os.path.join(out, "alignment.md"))
        return ok, "matched=%s not_found=%s" % (al["matched_count"], al["not_found_count"])

    # 9. 未知参数 -> 退出码 0
    def s_unknown():
        code = M.main(["--totally-unknown-flag", "init"])
        return code == 0, "exit=%s" % code

    results.append(("空输入目录", *s_empty()))
    results.append(("坏 JSON", *s_badjson()))
    results.append(("坏编码(GB18030)", *s_badenc()))
    results.append(("缺参数(环境变量默认)", *s_missing_args()))
    results.append(("超大文件", *s_oversized()))
    results.append(("契约回填", *s_contract()))
    results.append(("set/log 原子增量", *s_set_log()))
    results.append(("字段对齐", *s_align()))
    results.append(("未知参数", *s_unknown()))
    return results


if __name__ == "__main__":
    results = run_all()
    failed = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print("[%s] %s%s" % (mark, name, (" — " + str(detail)) if detail else ""))
    print("selftest: %d/%d passed" % (len(results) - failed, len(results)))
    sys.exit(1 if failed else 0)
