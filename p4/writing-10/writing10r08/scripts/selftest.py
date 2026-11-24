#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：在空输入/坏 JSON/最小案例三种降级输入上跑通 main.py 与 check_revision.py，
并校验 result.json 的契约字段。确定性、无网络、仅标准库。退出码 0 表示全部通过。
用法：python scripts/selftest.py [--tmp <dir>] [--validate /app/output/result.json]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")
CHECK = os.path.join(HERE, "check_revision.py")

REQUIRED_KEYS = {"case_id", "deliverable", "fact_source_mapping",
                 "version_boundary", "status", "sources", "uncertainty",
                 "change_log"}


def run(py, *args):
    p = subprocess.run([sys.executable, "-B", py] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, p.stdout, p.stderr


def make_case(tmp, comments="1. 请补充样本量说明。\n2. 请调整标题表述。\n",
              baseline="# 摘要\n这是摘要。\n\n# 方法\n这是方法。\n",
              case_obj=None, task="审稿回复任务\n"):
    inp = os.path.join(tmp, "input")
    mat = os.path.join(inp, "materials")
    os.makedirs(mat, exist_ok=True)
    with open(os.path.join(inp, "task.md"), "w", encoding="utf-8") as f:
        f.write(task)
    with open(os.path.join(mat, "reviewer-comments.md"), "w", encoding="utf-8") as f:
        f.write(comments)
    with open(os.path.join(mat, "manuscript-baseline.md"), "w", encoding="utf-8") as f:
        f.write(baseline)
    case = case_obj if case_obj is not None else {"case_id": "selftest-001"}
    with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(case, ensure_ascii=False) if not isinstance(case, str)
                else case)
    return inp


def check_result(path):
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    missing = REQUIRED_KEYS - set(r.keys())
    return missing


def test_empty():
    tmp = tempfile.mkdtemp(prefix="srt-empty-")
    try:
        inp = os.path.join(tmp, "input")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        os.makedirs(inp)
        rc, so, se = run(MAIN, "--input", inp, "--output", out, "--work", work)
        rp = os.path.join(out, "result.json")
        ok = rc == 0 and os.path.exists(rp)
        status = None
        if ok:
            with open(rp, encoding="utf-8") as f:
                status = json.load(f).get("status")
        return ok and status in ("insufficient_evidence", "partial"), \
            "empty input rc=%d status=%s" % (rc, status)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bad_json():
    tmp = tempfile.mkdtemp(prefix="srt-bad-")
    try:
        inp = make_case(tmp, case_obj="{not valid json")
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        rc, so, se = run(MAIN, "--input", inp, "--output", out, "--work", work)
        rp = os.path.join(out, "result.json")
        ok = rc == 0 and os.path.exists(rp)
        errs = []
        if ok:
            with open(rp, encoding="utf-8") as f:
                errs = json.load(f).get("errors") or []
        return ok and len(errs) >= 1, \
            "bad json rc=%d errors=%d" % (rc, len(errs))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_minimal():
    tmp = tempfile.mkdtemp(prefix="srt-min-")
    try:
        inp = make_case(tmp)
        out = os.path.join(tmp, "output")
        work = os.path.join(tmp, "work")
        rc, so, se = run(MAIN, "--input", inp, "--output", out, "--work", work)
        rp = os.path.join(out, "result.json")
        lp = os.path.join(out, "revision-ledger.json")
        ok = rc == 0 and os.path.exists(rp) and os.path.exists(lp)
        n_comments = 0
        status = None
        missing = []
        if ok:
            with open(rp, encoding="utf-8") as f:
                r = json.load(f)
                status = r.get("status")
                missing = REQUIRED_KEYS - set(r.keys())
            with open(lp, encoding="utf-8") as f:
                n_comments = len(json.load(f).get("entries") or [])
        return ok and n_comments == 2 and not missing and status == "partial", \
            "minimal rc=%d comments=%d status=%s missing=%s" % (
                rc, n_comments, status, missing)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_checker_detects_match_and_mismatch():
    """证明校验器能同时识别"已改到位"与"声称已改但未落地"两种状态。"""
    tmp = tempfile.mkdtemp(prefix="srt-chk-")
    try:
        out = os.path.join(tmp, "out")
        work = os.path.join(tmp, "work")
        inp = make_case(tmp,
                        comments="1. 请补充样本量说明。\n",
                        baseline="# 方法\n未说明样本量。\n")
        run(MAIN, "--input", inp, "--output", out, "--work", work)
        # 手工填一张"已落地"台账 + 修订稿
        manuscript = "# 方法\n样本量为 120 例。\n"
        with open(os.path.join(out, "revised-manuscript.md"), "w",
                  encoding="utf-8") as f:
            f.write(manuscript)
        ledger = {
            "version_id": 1,
            "entries": [{
                "comment_id": "C1", "comment_type": "data", "severity": "major",
                "sub_requirements": ["说明样本量"],
                "decision": "accept", "tone": "accept",
                "response_summary": "已补充样本量说明",
                "changes": [{"action": "补样本量", "section": "方法",
                             "before": "未说明样本量", "after": "样本量为 120 例",
                             "anchor": "样本量为 120 例", "status": "done"}],
                "evidence": {"needed": True, "status": "done",
                             "source": "materials/manuscript-baseline.md",
                             "cannot_do_reason": None},
                "status": "resolved"}]}
        with open(os.path.join(out, "revision-ledger.json"), "w",
                  encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False)
        with open(os.path.join(out, "response-letter.md"), "w",
                  encoding="utf-8") as f:
            f.write("# 回复信\n## 意见 C1\n已补充样本量。\n")
        result = {"case_id": "selftest-001", "version_boundary":
                  {"changes": [{"comment_id": "C1", "action": "补样本量",
                                "section": "方法", "anchor": "样本量为 120 例",
                                "status": "done"}]}}
        with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        rc1, so1, _ = run(CHECK, "--ledger",
                          os.path.join(out, "revision-ledger.json"),
                          "--manuscript", os.path.join(out, "revised-manuscript.md"),
                          "--response", os.path.join(out, "response-letter.md"),
                          "--result", os.path.join(out, "result.json"),
                          "--report", os.path.join(out, "r1.json"))
        r1 = json.load(open(os.path.join(out, "r1.json"), encoding="utf-8"))
        match_pass = rc1 == 0 and r1.get("passed") is True
        # 改为"未落地"：after 与 anchor 都不在稿件中
        ledger["entries"][0]["changes"] = [{"action": "补样本量", "section": "方法",
                                            "before": "未说明样本量",
                                            "after": "样本量为 999 例",
                                            "anchor": "样本量为 999 例",
                                            "status": "done"}]
        with open(os.path.join(out, "revision-ledger.json"), "w",
                  encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False)
        rc2, so2, _ = run(CHECK, "--ledger",
                          os.path.join(out, "revision-ledger.json"),
                          "--manuscript", os.path.join(out, "revised-manuscript.md"),
                          "--response", os.path.join(out, "response-letter.md"),
                          "--result", os.path.join(out, "result.json"),
                          "--report", os.path.join(out, "r2.json"))
        r2 = json.load(open(os.path.join(out, "r2.json"), encoding="utf-8"))
        mismatch_fail = rc2 == 0 and r2.get("passed") is False
        return match_pass and mismatch_fail, \
            "checker match=%s mismatch-detected=%s" % (match_pass, mismatch_fail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp", default=None)
    ap.add_argument("--validate", default=None)
    a = ap.parse_args()

    results = []
    for name, fn in [("empty_input", test_empty),
                     ("bad_json", test_bad_json),
                     ("minimal_case", test_minimal),
                     ("checker_match_mismatch", test_checker_detects_match_and_mismatch)]:
        try:
            ok, msg = fn()
            results.append({"test": name, "ok": ok, "detail": msg})
        except Exception as e:  # noqa: BLE001
            results.append({"test": name, "ok": False,
                            "detail": "exception: %s" % e})

    if a.validate:
        try:
            missing = check_result(a.validate)
            results.append({"test": "validate_result_schema", "ok": not missing,
                            "detail": "missing keys: %s" % sorted(missing)})
        except Exception as e:  # noqa: BLE001
            results.append({"test": "validate_result_schema", "ok": False,
                            "detail": "exception: %s" % e})

    all_ok = all(r["ok"] for r in results)
    print(json.dumps({"passed": all_ok, "results": results}, ensure_ascii=False,
                     indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
