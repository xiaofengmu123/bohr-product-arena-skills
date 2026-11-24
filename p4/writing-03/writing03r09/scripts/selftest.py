#!/usr/bin/env python3
"""自检：空输入 / 坏 JSON / 缺字段 / 契约回填 / 重复运行幂等 / 摘录可核验。

只依赖 Python 标准库；从 scripts/ 同目录导入 main 模块，全部用临时目录，
不依赖 /input、/app 等运行期路径。退出码 0 = 全部通过，1 = 存在失败。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import main  # noqa: E402


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_case_json(tmp, case):
    case_path = os.path.join(tmp, "case.json")
    _write(case_path, case)
    return case_path


def test_empty_input():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out")
        case_path = os.path.join(tmp, "case.json")  # 不存在
        rc = main.cmd_scaffold(type("A", (), {"case": case_path, "out": out})())
        r = _read(os.path.join(out, "result.json"))
        assert rc == 0, "空输入 scaffold 不应崩溃"
        assert r["status"] in ("input_error", "failed"), "空输入应标 input_error"
    return "empty-input"


def test_bad_json():
    with tempfile.TemporaryDirectory() as tmp:
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        out = os.path.join(tmp, "out")
        rc = main.cmd_scaffold(type("A", (), {"case": case_path, "out": out})())
        r = _read(os.path.join(out, "result.json"))
        assert rc == 0 and r["status"] == "input_error" and r["errors"], "坏 JSON 应记 errors"
    return "bad-json"


def test_contract_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        case = {
            "case_id": "case-1",
            "output_contract": {"deliverable": "目标产物", "custom_field": "自定义"},
        }
        case_path = run_case_json(tmp, case)
        out = os.path.join(tmp, "out")
        main.cmd_scaffold(type("A", (), {"case": case_path, "out": out})())
        r = _read(os.path.join(out, "result.json"))
        assert r["case_id"] == "case-1"
        assert "custom_field" in r and "deliverable" in r, "契约字段须原样回填"
        assert r["status"] == "partial"
        for k in ("sources", "uncertainty", "pending_human", "change_log"):
            assert k in r, "缺保留字段 %s" % k
    return "contract-roundtrip"


def test_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        case_path = run_case_json(tmp, {"case_id": "case-2"})
        out = os.path.join(tmp, "out")
        a = type("A", (), {"case": case_path, "out": out})()
        main.cmd_scaffold(a)
        first = open(os.path.join(out, "result.json"), "rb").read()
        main.cmd_scaffold(a)
        second = open(os.path.join(out, "result.json"), "rb").read()
        assert first == second, "重复运行结果应一致（幂等）"
        r = _read(os.path.join(out, "result.json"))
        assert len([e for e in r["change_log"] if e["action"] == "scaffold"]) == 1, "change_log 不应重复"
    return "idempotent"


def test_quote_verifiable():
    with tempfile.TemporaryDirectory() as tmp:
        mat = os.path.join(tmp, "materials")
        os.makedirs(mat)
        with open(os.path.join(mat, "paper.txt"), "w", encoding="utf-8") as f:
            f.write("样本量为 120，观察组死亡率 8%。")
        case_path = run_case_json(tmp, {"case_id": "case-3"})
        out = os.path.join(tmp, "out")
        main.cmd_scaffold(type("A", (), {"case": case_path, "out": out})())
        rpath = os.path.join(out, "result.json")
        r = _read(rpath)
        r["fact_source_map"] = [
            {"claim_id": "C1", "claim_text": "观察组死亡率 8%",
             "quote": "观察组死亡率 8%", "locator": "materials/paper.txt",
             "source_ref": "S1", "evidence_level": "direct_quote"},
            {"claim_id": "C2", "claim_text": "虚构数据", "quote": "不存在的句子",
             "locator": "materials/paper.txt", "source_ref": "S1",
             "evidence_level": "unverified"},
        ]
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False)
        rep_path = os.path.join(out, "report.json")
        rc = main.cmd_verify(type("A", (), {
            "result": rpath, "case": case_path, "report": rep_path,
            "material_root": [mat]}))
        rep = _read(rep_path)
        assert rc == 2, "存在不可定位 quote 应退出码 2"
        assert len(rep["checks"]["unlocatable_quotes"]) == 1
    return "quote-verifiable"


def test_missing_case_id():
    with tempfile.TemporaryDirectory() as tmp:
        case_path = run_case_json(tmp, {"output_contract": ["deliverable"]})
        out = os.path.join(tmp, "out")
        main.cmd_scaffold(type("A", (), {"case": case_path, "out": out})())
        rep_path = os.path.join(out, "report.json")
        rc = main.cmd_verify(type("A", (), {
            "result": os.path.join(out, "result.json"),
            "case": case_path, "report": rep_path, "material_root": []}))
        assert rc == 2, "缺 case_id 应退出码 2"
    return "missing-case-id"


def main_run():
    tests = [test_empty_input, test_bad_json, test_contract_roundtrip,
             test_idempotent, test_quote_verifiable, test_missing_case_id]
    failed = 0
    for t in tests:
        try:
            name = t()
            print("PASS %-20s" % name)
        except AssertionError as e:
            failed += 1
            print("FAIL %-20s %s" % (t.__name__, e))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL %-20s 异常: %s" % (t.__name__, e))
    print("selftest: %d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_run())
