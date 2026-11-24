# -*- coding: utf-8 -*-
"""selftest.py — 鲁棒性自检脚本（仅标准库，供作者在交付前运行）。

覆盖场景：
  1) 空输入目录      -> 退出正常，产出合法 insufficient_evidence 结果
  2) 坏 JSON case.json -> 不崩溃，记录 errors，其余材料继续处理
  3) 缺输入目录/缺参数 -> 退出正常，status=input_error，不抛异常
  4) 超大文件        -> 跳过并记录 degradations，小文件仍被处理
  5) 正常流程        -> scaffold + update + selfcheck 全通过，quote 被回验
  6) 编造 quote      -> 自动标 quote_match=false 并进 pending_review
  7) 确定性          -> 同一输入重复 scaffold，内容一致（除时间戳）

用法（任意工作目录）：
  python3 -B scripts/selftest.py
退出码：0 全部通过；1 存在失败。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import review_core as rc  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        FAILURES.append(name)
        print("FAIL  %s  %s" % (name, detail))


def read_result(out):
    with open(Path(out) / "result.json", encoding="utf-8") as f:
        return json.load(f)


def make_input(base, case_text="{}", task_text="# 测试任务\n", materials=None):
    inp = Path(base) / "input"
    out = Path(base) / "output"
    work = Path(base) / "work"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "case.json").write_text(case_text, encoding="utf-8")
    (inp / "task.md").write_text(task_text, encoding="utf-8")
    if materials:
        matdir = inp / "materials"
        matdir.mkdir(parents=True, exist_ok=True)
        for name, content in materials.items():
            (matdir / name).write_text(content, encoding="utf-8")
    return str(inp), str(out), str(work)


def test_empty_input():
    base = tempfile.mkdtemp(prefix="st_empty_")
    inp = Path(base) / "input"
    inp.mkdir(parents=True)
    out = Path(base) / "output"
    work = Path(base) / "work"
    try:
        r = rc.scaffold(str(inp), str(out), str(work))
        check("empty_input_no_crash",
              r["status"] in ("insufficient_evidence", "input_error"))
        rr = read_result(out)
        check("empty_input_valid_json", bool(rr.get("case_id")) and bool(rr.get("status")))
        check("empty_input_honest_status", rr.get("status") == "insufficient_evidence")
    except Exception as e:  # noqa: BLE001
        check("empty_input_no_crash", False, repr(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_bad_json():
    base = tempfile.mkdtemp(prefix="st_badjson_")
    inp, out, work = make_input(
        base, case_text="{ not valid json {{",
        materials={"paper.md": "## Introduction\n\nThis is a test paragraph with a claim.\n"})
    try:
        r = rc.scaffold(inp, out, work)
        check("bad_json_no_crash", True)
        rr = read_result(out)
        check("bad_json_valid_result", bool(rr.get("case_id")))
        check("bad_json_error_recorded",
              any("case_json" in e for e in rr.get("errors", [])))
        check("bad_json_material_kept",
              any(s.get("chars", 0) > 0 for s in rr.get("sources", [])))
    except Exception as e:  # noqa: BLE001
        check("bad_json_no_crash", False, repr(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_missing_input():
    base = tempfile.mkdtemp(prefix="st_missing_")
    out = Path(base) / "output"
    work = Path(base) / "work"
    nonexist = str(Path(base) / "does_not_exist")
    try:
        r = rc.scaffold(nonexist, str(out), str(work))
        check("missing_input_no_crash", r["status"] == "input_error")
        rr = read_result(out)
        check("missing_input_result_exists", rr.get("status") == "input_error")
    except Exception as e:  # noqa: BLE001
        check("missing_input_no_crash", False, repr(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_oversized_file():
    base = tempfile.mkdtemp(prefix="st_oversize_")
    inp, out, work = make_input(
        base, materials={"huge.txt": "x" * 3000, "small.md": "## Methods\n\nSome text.\n"})
    try:
        r = rc.scaffold(inp, out, work, max_file_bytes=1000)
        check("oversize_no_crash", True)
        rr = read_result(out)
        deg = [d for d in rr.get("degradations", []) if d.get("reason") == "file_too_large"]
        check("oversize_recorded", len(deg) >= 1)
        check("oversize_small_kept",
              any(s.get("chars", 0) > 0 for s in rr.get("sources", [])))
    except Exception as e:  # noqa: BLE001
        check("oversize_no_crash", False, repr(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_normal_flow_and_verify():
    base = tempfile.mkdtemp(prefix="st_normal_")
    paper = ("## Introduction\n\nWe claim that drug X improves outcome Y significantly.\n\n"
             "## Methods\n\nWe used a randomized trial with a control group.\n")
    case = json.dumps({
        "case_id": "case-demo-1", "title": "演示案例",
        "output_contract": {"extra_key": None, "list_key": []},
    }, ensure_ascii=False)
    inp, out, work = make_input(base, case_text=case, materials={"paper.md": paper})
    try:
        r = rc.scaffold(inp, out, work)
        check("normal_scaffold_status", r["status"] == "partial")
        rr = read_result(out)
        check("contract_backfilled",
              "extra_key" in rr and rr["extra_key"] is None and rr["list_key"] == [])
        findings = [{
            "id": "F001", "type": "overclaim", "severity": "major",
            "quote": "drug X improves outcome Y significantly",
            "claim": "作者主张 X 显著改善 Y",
            "evidence": "未给效应量、置信区间或 p 值",
            "reasoning": "影响主结论；可通过补统计量修正；影响可复现性",
            "suggestion": "补充效应量与置信区间，或把 significantly 降级为 observed difference",
            "status": "confirmed", "confidence": "medium",
        }]
        fpath = Path(work) / "findings.json"
        fpath.write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
        claims = [{
            "id": "C001", "text": "drug X improves outcome Y significantly",
            "speaker": "author", "section": "Introduction", "locator": "",
            "verdict": "overclaimed", "evidence": "",
        }]
        cpath = Path(work) / "claims.json"
        cpath.write_text(json.dumps(claims, ensure_ascii=False), encoding="utf-8")
        rc.update(output_dir=out, work_dir=work, findings_file=str(fpath),
                  claims_file=str(cpath), summary="演示摘要", status="ok")
        rr2 = read_result(out)
        check("update_status_ok", rr2.get("status") == "ok")
        f0 = rr2["review"]["findings"][0]
        check("quote_verified", f0.get("quote_match") is True,
              str(f0.get("quote_verify_note")))
        check("quote_locator_filled", bool(f0.get("locator")))
        check("selfcheck_ok", rc.selfcheck(out) == 0)
    except Exception as e:  # noqa: BLE001
        check("normal_flow", False, repr(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_fabricated_quote():
    base = tempfile.mkdtemp(prefix="st_fab_")
    inp, out, work = make_input(
        base, materials={"paper.md": "## Introduction\n\nOnly honest text here.\n"})
    try:
        rc.scaffold(inp, out, work)
        findings = [{
            "id": "F001", "type": "overclaim", "severity": "major",
            "quote": "THIS SENTENCE DOES NOT EXIST IN THE PAPER",
            "claim": "x", "evidence": "y", "reasoning": "z", "suggestion": "s",
            "status": "confirmed", "confidence": "medium",
        }]
        fpath = Path(work) / "findings.json"
        fpath.write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
        rc.update(output_dir=out, work_dir=work, findings_file=str(fpath))
        rr = read_result(out)
        f0 = rr["review"]["findings"][0]
        check("fabricated_quote_flagged", f0.get("quote_match") is False)
        check("fabricated_in_pending_review",
              any("quote" in (p.get("issue") or "") for p in rr.get("pending_review", [])))
    except Exception as e:  # noqa: BLE001
        check("fabricated_quote_flagged", False, repr(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_determinism():
    base = tempfile.mkdtemp(prefix="st_det_")
    paper = ("## Introduction\n\nThis is deterministic text.\n\n"
             "## Results\n\nNumbers 1 2 3.\n")
    inp1, out1, work1 = make_input(base, materials={"paper.md": paper})
    try:
        rc.scaffold(inp1, out1, work1)
        r1 = read_result(out1)
        # 对同一输入、同一产物目录再次 scaffold，应得到相同内容（除时间戳）
        rc.scaffold(inp1, out1, work1)
        r2 = read_result(out1)
        for r in (r1, r2):
            r.pop("change_log", None)
            r.pop("runtime", None)
        check("determinism_scaffold", r1 == r2)
    except Exception as e:  # noqa: BLE001
        check("determinism_scaffold", False, repr(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    tests = [
        test_empty_input, test_bad_json, test_missing_input,
        test_oversized_file, test_normal_flow_and_verify,
        test_fabricated_quote, test_determinism,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            check("unexpected_" + t.__name__, False, repr(e))
    print("")
    print("selftest: %d passed, %d failed" % (PASS, FAIL))
    if FAILURES:
        print("failed tests: %s" % ", ".join(FAILURES))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
