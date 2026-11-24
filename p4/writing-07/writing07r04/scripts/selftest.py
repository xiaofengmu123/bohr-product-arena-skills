"""selftest.py — 内置自检（纯标准库，确定性）。

覆盖：空输入、坏 JSON、坏编码、契约发现、骨架落盘、产物校验、编造防火墙。
全部通过退出码 0，否则退出码 1。可在任意平台临时目录运行。
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402

CHECKS = []


def check(name, fn):
    try:
        fn()
        CHECKS.append((name, True, ""))
    except Exception as exc:  # noqa: BLE001
        CHECKS.append((name, False, "%s: %s" % (type(exc).__name__, exc)))


def _mk(tmp, sub, files=None):
    d = os.path.join(tmp, sub)
    os.makedirs(d, exist_ok=True)
    if files:
        for rel, content in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            mode = "wb" if isinstance(content, bytes) else "w"
            kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
            with open(p, mode, **kwargs) as f:
                f.write(content)
    return d


def test_empty_input():
    def f():
        tmp = tempfile.mkdtemp(prefix="st_empty_")
        try:
            inp = _mk(tmp, "input")
            work = _mk(tmp, "work")
            out = _mk(tmp, "output")
            m = C.scan_input(inp, work)
            assert m["input_empty"] is True
            r = C.emit_skeleton(work, out)
            assert r["status"] == C.STATUS_INSUFFICIENT
            assert json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    check("empty_input_insufficient_evidence", f)


def test_bad_json():
    def f():
        tmp = tempfile.mkdtemp(prefix="st_badjson_")
        try:
            inp = _mk(tmp, "input", {"case.json": "{not valid json", "task.md": "# t"})
            work = _mk(tmp, "work")
            m = C.scan_input(inp, work)
            assert any("bad_json" in e for e in m["errors"]), m["errors"]
            # 骨架仍可落盘，status=input_error（无 case_id）
            r = C.emit_skeleton(work, _mk(tmp, "output"))
            assert r["status"] == C.STATUS_INPUT_ERROR
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    check("bad_json_degrades_no_crash", f)


def test_bad_encoding():
    def f():
        tmp = tempfile.mkdtemp(prefix="st_enc_")
        try:
            raw = "汉字内容".encode("gb18030")
            inp = _mk(tmp, "input", {
                "case.json": json.dumps({"case_id": "c1", "output_contract": {"intro": {}}}, ensure_ascii=False),
                "materials/note.md": raw,
            })
            work = _mk(tmp, "work")
            m = C.scan_input(inp, work)
            assert m["case_id"] == "c1"
            assert any("fallback_encoding" in d["reason"] or "fallback_encoding" in str(d) for d in m["degradations"]) or \
                   any("fallback_encoding" in e for e in m["errors"]), (m["errors"], m["degradations"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    check("bad_encoding_fallback", f)


def test_contract_discovery():
    def f():
        cases = [
            ({"output_contract": {"a": {}, "b": {}}}, ["a", "b"]),
            ({"output": ["x", "y"]}, ["x", "y"]),
            ({"expected_output": {"fields": {"z": {}}}}, []),
            ({"output_contract": "single"}, ["single"]),
            ({"nested": {"required_fields": ["p", "q"]}}, ["p", "q"]),
        ]
        for data, expect in cases:
            contract, src = C.discover_contract(data)
            if expect:
                assert contract is not None and C.normalize_contract_fields(contract) == expect, (data, contract, src)
            else:
                # expected_output 内嵌 fields 是字段规范对象而非字段列表；normalize 应返回其键
                contract2, _ = C.discover_contract(data)
                got = C.normalize_contract_fields(contract2) if contract2 is not None else []
                assert got == ["z"], (data, got)
    check("contract_discovery_variants", f)


def test_emit_and_validate():
    def f():
        tmp = tempfile.mkdtemp(prefix="st_emit_")
        try:
            inp = _mk(tmp, "input", {
                "case.json": json.dumps({
                    "case_id": "demo-001",
                    "output_contract": {"introduction": {"type": "string"},
                                        "related_work": {"type": "string"}},
                }),
                "task.md": "# task",
                "materials/paper.md": "background facts",
            })
            work = _mk(tmp, "work")
            out = _mk(tmp, "output")
            C.scan_input(inp, work)
            C.emit_skeleton(work, out)
            rep = C.validate_output(work, out)
            assert "result_json_missing" not in rep["failed"]
            assert any(x.startswith("contract_fields_complete") for x in rep["passed"]), rep
            # 缺 manuscript 应失败
            assert any("main_product_missing" in x for x in rep["failed"]), rep
            # 补主产物后通过
            with open(os.path.join(out, "manuscript.md"), "w", encoding="utf-8") as f:
                f.write("# 引言 [S1]\n")
            rep2 = C.validate_output(work, out)
            assert not any(x.startswith("main_product_missing") for x in rep2["failed"]), rep2
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    check("emit_and_validate_roundtrip", f)


def test_oversized_skip():
    def f():
        tmp = tempfile.mkdtemp(prefix="st_big_")
        try:
            inp = _mk(tmp, "input", {"case.json": json.dumps({"case_id": "c"}),
                                     "materials/big.md": "x" * (C.MAX_FILE_BYTES + 10)})
            work = _mk(tmp, "work")
            m = C.scan_input(inp, work)
            assert any(mat.get("skipped") and mat.get("reason") == "oversized_gt_2mb"
                       for mat in m["materials"]), m["materials"]
            assert any("oversized" in str(d) for d in m["degradations"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    check("oversized_material_skipped", f)


def test_placeholder_firewall():
    def f():
        tmp = tempfile.mkdtemp(prefix="st_fw_")
        try:
            inp = _mk(tmp, "input", {"case.json": json.dumps({"case_id": "c", "output_contract": {"intro": {}}})})
            work = _mk(tmp, "work")
            out = _mk(tmp, "output")
            C.scan_input(inp, work)
            C.emit_skeleton(work, out)
            with open(os.path.join(out, "manuscript.md"), "w", encoding="utf-8") as f:
                f.write("背景 Lorem ipsum 此处插入")
            rep = C.validate_output(work, out)
            assert any("placeholder_text_found" in w for w in rep["warnings"]), rep
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    check("placeholder_firewall_warns", f)


def main():
    test_empty_input()
    test_bad_json()
    test_bad_encoding()
    test_contract_discovery()
    test_emit_and_validate()
    test_oversized_skip()
    test_placeholder_firewall()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    for name, ok, msg in CHECKS:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" - " + msg) if msg else ""))
    print("selftest: %d/%d passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
