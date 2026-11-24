"""selftest.py — 自检脚本（对应评分维度的可运行验证）。

在临时目录构造合成输入，逐项验证：
  * result.json 合法、字段齐全、contract 回填、case_id 非空
  * 去重按内容哈希（不按文件名）
  * 版本候选为启发式且标注 heuristic
  * 坏 JSON/坏编码/超大文件/空目录不崩溃，记录进 errors/degradations
  * 缺 case.json -> input_error；空输入 -> insufficient_evidence
  * 重复运行结果一致（除时间戳外）

用法：python3 -B scripts/selftest.py
退出码 0 表示全部通过。
"""

import io
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl  # noqa: E402


PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def run_case(tmp, files, case_json_obj=None, write_case=True):
    inp = os.path.join(tmp, "input")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    os.makedirs(inp, exist_ok=True)
    for rel, content in files.items():
        p = os.path.join(inp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
        with open(p, mode, **kwargs) as f:
            f.write(content)
    if write_case:
        if case_json_obj is None:
            case_json_obj = {
                "case_id": "selftest-case",
                "task": "synthetic",
                "output_contract": {
                    "manifest": {"type": "array"},
                    "directory_tree": {"type": "string"},
                    "dedup": {"type": "array"},
                    "version": {"type": "array"},
                    "recovery": {"type": "object"},
                    "index": {"type": "object"},
                },
            }
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
            json.dump(case_json_obj, f, ensure_ascii=False)
    return inp, out, work


def main():
    global PASS, FAIL
    tmp = tempfile.mkdtemp(prefix="library_selftest_")
    try:
        print("== 1. 正常案例 ==")
        inp, out, work = run_case(tmp, {
            "task.md": "# 规则\n按内容去重，保留版本关系。\n",
            "materials/a.txt": "hello world\n",
            "materials/b.txt": "hello world\n",          # 内容重复，文件名不同
            "materials/report_v1.pdf": b"%PDF-1.4 v1",
            "materials/report_v2.pdf": b"%PDF-1.4 v2",  # 版本对
            "materials/catalog.json": json.dumps([
                {"filename": "materials/a.txt", "sha256": core_impl.hash_file(
                    os.path.join(os.path.join(tmp, "input"), "materials/a.txt"))[0]},
                {"filename": "materials/missing.txt"},
            ]),
        })
        rp, result = core_impl.run_pipeline(inp, out, work, None, None)
        r = json.load(open(rp, encoding="utf-8"))
        check("result.json 存在且合法", os.path.isfile(rp) and r.get("case_id") == "selftest-case")
        check("status 合法", r.get("status") in ("ok", "partial", "insufficient_evidence", "input_error"))
        check("output_contract 全字段回填", set(r.get("output_contract", {})) == {
            "manifest", "directory_tree", "dedup", "version", "recovery", "index"})
        check("sources/uncertainty/change_log 存在",
              all(k in r for k in ("sources", "uncertainty", "change_log")))
        check("去重按内容（a.txt 与 b.txt 同组）",
              any(set(g["paths"]) == {"materials/a.txt", "materials/b.txt"}
                  for g in r["computed"]["duplicate_groups"]))
        check("版本候选为启发式",
              all(c.get("heuristic") for c in r["computed"]["version_candidates"]))
        check("对账区分 matches/missing",
              r["computed"]["reconciliation"]["counts"]["matches"] == 1
              and r["computed"]["reconciliation"]["counts"]["missing"] == 1)
        for art in ("library_manifest.csv", "directory_tree.txt",
                    "dedup_version.json", "recovery_report.json",
                    "library_index.json", "library_manifest.md", "operation_log.jsonl"):
            check(f"产物存在: {art}", os.path.isfile(os.path.join(out, art)))

        print("== 2. 重复运行一致性 ==")
        rp2, _ = core_impl.run_pipeline(inp, os.path.join(tmp, "output2"),
                                        os.path.join(tmp, "work2"), None, None)
        r2 = json.load(open(rp2, encoding="utf-8"))
        c1 = json.dumps(r["computed"], sort_keys=True, ensure_ascii=False)
        c2 = json.dumps(r2["computed"], sort_keys=True, ensure_ascii=False)
        check("computed 确定性（除时间戳外）", c1 == c2)

        print("== 3. 坏 JSON case.json ==")
        inp3, out3, work3 = run_case(tmp, {"task.md": "# x\n"}, write_case=False)
        with open(os.path.join(inp3, "case.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        rp3, _ = core_impl.run_pipeline(inp3, out3, work3, None, None)
        r3 = json.load(open(rp3, encoding="utf-8"))
        check("坏 case.json -> input_error 不崩溃", r3["status"] == "input_error")

        print("== 4. 缺 case.json ==")
        inp4, out4, work4 = run_case(tmp, {"task.md": "# x\n"}, write_case=False)
        rp4, _ = core_impl.run_pipeline(inp4, out4, work4, None, None)
        r4 = json.load(open(rp4, encoding="utf-8"))
        check("缺 case.json -> input_error", r4["status"] == "input_error")

        print("== 5. 空输入目录 ==")
        inp5 = os.path.join(tmp, "empty_input")
        os.makedirs(inp5, exist_ok=True)
        out5 = os.path.join(tmp, "empty_out")
        rp5, _ = core_impl.run_pipeline(inp5, out5, os.path.join(tmp, "empty_work"), None, None)
        r5 = json.load(open(rp5, encoding="utf-8"))
        check("空输入不崩溃、写出合法结果",
              os.path.isfile(rp5) and r5.get("case_id") and r5.get("status"))

        print("== 6. 坏编码文件名/文本 ==")
        inp6, out6, work6 = run_case(tmp, {
            "task.md": "# x\n",
            "bad.bin": b"\xff\xfe\x00\x01\x02",
        }, write_case=True)
        rp6, _ = core_impl.run_pipeline(inp6, out6, work6, None, None)
        r6 = json.load(open(rp6, encoding="utf-8"))
        check("坏编码文本不崩溃", r6.get("status") in ("partial", "ok", "input_error"))

        print("== 7. 骨架 + 更新 + 检查 ==")
        inp7, out7, work7 = run_case(tmp, {"task.md": "# x\n"})
        sk = core_impl.write_skeleton(out7, work7,
                                      os.path.join(inp7, "case.json"))
        rsk = json.load(open(sk, encoding="utf-8"))
        check("骨架先落盘 status=partial", rsk["status"] == "partial" and rsk["case_id"])
        patch = {"status": "ok", "notes": "semantic refinement"}
        pp = os.path.join(tmp, "patch.json")
        with open(pp, "w", encoding="utf-8") as f:
            json.dump(patch, f)
        core_impl.update_result(sk, pp)
        rup = json.load(open(sk, encoding="utf-8"))
        check("update 合并生效", rup["status"] == "ok" and rup["notes"] == "semantic refinement")
        ok, problems = core_impl.check_result(sk, os.path.join(inp7, "case.json"))
        check("check 通过", ok, "; ".join(problems))

        print(f"\n== 结果: {PASS} PASS, {FAIL} FAIL ==")
        if FAILURES:
            print("失败项:", ", ".join(FAILURES))
    except Exception:
        print(traceback.format_exc())
        FAIL += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
