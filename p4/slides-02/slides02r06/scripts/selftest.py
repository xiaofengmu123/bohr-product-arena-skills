#!/usr/bin/env python3
"""selftest.py — traceable-slide-builder 自检脚本。

只依赖标准库；在系统临时目录构造合成输入（明确标注为示例，非正式案例），
验证 scaffold / record / check-quotes / validate / finalize 五步可运行，
并覆盖空输入、坏 JSON、quote 不匹配、缺 pptx 四种降级路径。任何失败退出码非 0。
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile

import core_impl as C
import main as M


def ns(**kwargs):
    """构造与 argparse 兼容的 Namespace。"""
    out = argparse.Namespace()
    for key, value in kwargs.items():
        setattr(out, key, value)
    return out


def write_fake_pptx(path, n_slides=2):
    """写一个最小合法结构的 PPTX（ZIP 含 [Content_Types].xml 与 slide*.xml）。"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        for i in range(1, n_slides + 1):
            zf.writestr("ppt/slides/slide%d.xml" % i, "<p:sld/>")


def write_fake_pdf(path):
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")


def build_synthetic_case(tmp, quote_text):
    """构造一份合成 case（示例，非正式案例）。"""
    inp = os.path.join(tmp, "input")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "output")
    os.makedirs(inp)
    C.write_json(os.path.join(inp, "case.json"), {
        "case_id": "synth-001",
        "output_contract": {
            "presentation_file": None,
            "preview_files": None,
            "speaker_notes": None,
            "page_plan": None,
            "evidence_map": None,
        },
        "audience": "colleagues",
        "time_budget_min": 10,
        "slide_count": 4,
    })
    C.write_json(os.path.join(inp, "paper.txt"), {
        "note": "合成示例文本，仅用于脚本自检，非正式案例",
    })
    with open(os.path.join(inp, "paper.txt"), "w", encoding="utf-8") as fh:
        fh.write(quote_text)
    return inp, work, out


def check(cond, label):
    if not cond:
        raise AssertionError("自检失败: %s" % label)


def run(callable_, *args, **kwargs):
    """运行并捕获 SystemExit，返回 (retcode,)。"""
    try:
        callable_(*args, **kwargs)
        return 0
    except SystemExit as exc:
        return exc.code or 0


def test_happy_path():
    tmp = tempfile.mkdtemp(prefix="selftest_happy_")
    try:
        quote_text = (
            "Our model achieves an accuracy of 87 percent on the test set. "
            "Figure 3 shows the ablation results across three settings."
        )
        inp, work, out = build_synthetic_case(tmp, quote_text)

        code = run(M.cmd_scaffold, ns(input=inp, work=work, output=out))
        check(code == 0, "scaffold 退出码 0")
        result = C.load_json(os.path.join(out, "result.json"))
        check(result and result.get("case_id") == "synth-001", "骨架含 case_id")
        check(result.get("status") == "partial", "骨架 status=partial")
        check(result.get("presentation_file") is None, "契约字段以 null 回填")

        code = run(M.cmd_record, ns(kind="evidence", work=work, data=json.dumps({
            "id": "e1", "page": 4, "role": "result", "kind": "number",
            "claim": "accuracy 87%",
            "quote": "achieves an accuracy of 87 percent",
            "loc": "paper.txt#L1",
        })))
        check(code == 0, "record evidence 退出码 0")
        run(M.cmd_record, ns(kind="figure", work=work, data=json.dumps({
            "id": "f1", "ref": "Fig 3", "locator": "paper.txt#L1",
            "caption_quote": "Figure 3: ablation results",
            "kind": "bar", "data_binding": "crop_cite", "used_in_pages": [4],
        })))
        run(M.cmd_record, ns(kind="plan", work=work, data=json.dumps({
            "index": 4, "role": "result", "claim": "accuracy 87%",
            "bullets": ["87% accuracy"], "figures": ["Fig 3"],
            "word_budget": 40, "evidence": ["e1"],
        })))

        write_fake_pptx(os.path.join(out, "deck.pptx"))
        write_fake_pdf(os.path.join(out, "preview.pdf"))

        code = run(M.cmd_check_quotes, ns(
            source=os.path.join(inp, "paper.txt"),
            evidence=os.path.join(work, "evidence_map.json"),
            work=work,
        ))
        check(code == 0, "quote 全匹配时 check-quotes 退出码 0")

        self_check, passed = C.run_validate(inp, work, out)
        check(passed, "happy path validate passed, 缺口=%s" % json.dumps(self_check, ensure_ascii=False)[:500])

        code = run(M.cmd_finalize, ns(input=inp, work=work, output=out, status="complete"))
        check(code == 0, "finalize complete 退出码 0")
        final = C.load_json(os.path.join(out, "result.json"))
        check(final.get("status") == "complete", "终版 status=complete")
        check(final.get("presentation_file") == "deck.pptx", "终版含 presentation_file")
        check(final.get("page_plan", {}).get("pages"), "终版含 page_plan")
        check(final.get("sources"), "终版含 sources")
        print("PASS happy_path")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_input():
    tmp = tempfile.mkdtemp(prefix="selftest_empty_")
    try:
        inp, work, out = build_synthetic_case(tmp, "")
        os.remove(os.path.join(inp, "case.json"))  # 空输入目录
        code = run(M.cmd_scaffold, ns(input=inp, work=work, output=out))
        check(code == 0, "空输入 scaffold 不崩溃、退出码 0")
        result = C.load_json(os.path.join(out, "result.json"))
        check(result and result.get("case_id") == "unknown", "空输入 fallback case_id=unknown")
        check(result.get("status") == "partial", "空输入 status=partial")
        print("PASS empty_input")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bad_json():
    tmp = tempfile.mkdtemp(prefix="selftest_badjson_")
    try:
        inp, work, out = build_synthetic_case(tmp, "")
        with open(os.path.join(inp, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        code = run(M.cmd_scaffold, ns(input=inp, work=work, output=out))
        check(code == 0, "坏 JSON scaffold 不崩溃、退出码 0")
        result = C.load_json(os.path.join(out, "result.json"))
        check(result and result.get("status") == "partial", "坏 JSON 仍写合法 partial 骨架")
        print("PASS bad_json")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_quote_mismatch():
    tmp = tempfile.mkdtemp(prefix="selftest_quotemiss_")
    try:
        inp, work, out = build_synthetic_case(tmp, "The reported accuracy is 87 percent.")
        run(M.cmd_scaffold, ns(input=inp, work=work, output=out))
        run(M.cmd_record, ns(kind="evidence", work=work, data=json.dumps({
            "id": "e1", "page": 4, "kind": "number",
            "claim": "accuracy 99%",
            "quote": "accuracy is 99 percent",
            "loc": "paper.txt#L1",
        })))
        code = run(M.cmd_check_quotes, ns(
            source=os.path.join(inp, "paper.txt"),
            evidence=os.path.join(work, "evidence_map.json"),
            work=work,
        ))
        check(code == 1, "quote 不匹配时 check-quotes 退出码 1")
        qc = C.load_json(os.path.join(work, "quote_check.json"))
        check(qc and qc.get("mismatch_count") == 1, "quote_check 记录 1 处不匹配")
        print("PASS quote_mismatch")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_pptx():
    tmp = tempfile.mkdtemp(prefix="selftest_nopptx_")
    try:
        inp, work, out = build_synthetic_case(tmp, "accuracy 87 percent")
        run(M.cmd_scaffold, ns(input=inp, work=work, output=out))
        run(M.cmd_record, ns(kind="evidence", work=work, data=json.dumps({
            "id": "e1", "page": 4, "kind": "number",
            "claim": "accuracy 87%", "quote": "accuracy 87 percent", "loc": "paper.txt#L1",
        })))
        self_check, passed = C.run_validate(inp, work, out)
        check(not passed, "缺 pptx 时 validate 判定未通过")
        check(self_check.get("error_count", 0) >= 1, "self_check 记录至少 1 个 error")
        print("PASS missing_pptx")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    tests = [
        test_happy_path,
        test_empty_input,
        test_bad_json,
        test_quote_mismatch,
        test_missing_pptx,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print("FAIL %s: %s" % (getattr(test, "__name__", "?"), exc))
    if failures:
        print("selftest FAILED: %d/%d 项失败" % (failures, len(tests)))
        sys.exit(1)
    print("selftest PASSED: %d/%d 项通过" % (len(tests), len(tests)))


if __name__ == "__main__":
    main()
