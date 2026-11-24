"""selftest.py - 契约驱动资料库修复 Skill 的自检（仅标准库）。

在临时目录构造真实夹具，覆盖：
  全量运行 / 契约覆盖率 / 去重 / 版本 / 降级 / 确定性复跑 /
  骨架落盘 / 空输入 / 坏 case.json / 缺 case.json / --check。

用法：python3 -B scripts/selftest.py   或   python3 -B scripts/main.py --selftest
退出码：0 全部通过；1 存在失败。
"""
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import core_impl  # noqa: E402


def _write(path, data, bom=False):
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8-sig" if bom else "utf-8", "newline": ""}
    with open(path, mode, **kwargs) as fh:
        fh.write(data)


def _sha(data):
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _make_fixture(base, clean=False):
    root = os.path.join(base, "input")
    os.makedirs(os.path.join(root, "subdir"), exist_ok=True)
    os.makedirs(os.path.join(root, "empty_dir"), exist_ok=True)
    _write(os.path.join(root, "subdir", "nested.txt"), "nested file\n")
    _write(os.path.join(root, "a.txt"), "hello world\n")
    _write(os.path.join(root, "b.txt"), "hello world\n")
    _write(os.path.join(root, "report_v1.txt"), "v1 content\n")
    _write(os.path.join(root, "report_v2.txt"), "v2 content\n")
    _write(os.path.join(root, "report_final.txt"), "v2 content\n")
    _write(os.path.join(root, "中文材料.md"), "# 标题\n这是中文材料\n", bom=True)

    catalog = {"items": [
        {"path": "a.txt", "sha256": _sha("hello world\n")},
        {"path": "b.txt", "sha256": "0" * 64},
        {"path": "missing.pdf"},
    ]}
    _write(os.path.join(root, "catalog.json"),
           json.dumps(catalog, ensure_ascii=False))

    features = {"large_file": False, "bad_json": False,
                "symlink": False, "broken_symlink": False}
    if not clean:
        _write(os.path.join(root, "large.bin"), b"0" * (2 * 1024 * 1024 + 1))
        features["large_file"] = True
        _write(os.path.join(root, "bad.json"), "{not valid json")
        features["bad_json"] = True
        try:
            os.symlink("a.txt", os.path.join(root, "link_a.txt"))
            features["symlink"] = True
        except (OSError, NotImplementedError):
            pass
        try:
            os.symlink("nonexistent.txt", os.path.join(root, "broken_link.txt"))
            features["broken_symlink"] = True
        except (OSError, NotImplementedError):
            pass

    contract = {
        "library_manifest": [],
        "summary": {},
        "duplicate_groups": [],
        "version_candidates": [],
        "directory_tree": [],
        "reconciliation": {},
        "answers": [],
        "total_files": 0,
    }
    case = {"challenge_id": "selftest-0001", "output_contract": contract}
    _write(os.path.join(root, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(root, "task.md"),
           "# 任务\n\n有多少个文件？\n\n版本说明：report_final 替代 report_v2。\n")
    return root, features


def _load_result(output_dir):
    with open(os.path.join(output_dir, "result.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_full_run(tmp):
    root, features = _make_fixture(tmp, clean=False)
    out1 = os.path.join(tmp, "out1")
    out2 = os.path.join(tmp, "out2")
    work = os.path.join(tmp, "work")
    code = core_impl.run(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                         root, out1, work)
    assert code == 0, f"run exit={code}"
    r = _load_result(out1)
    assert r["case_id"] == "selftest-0001", r.get("case_id")
    assert r["status"] == "partial", r["status"]
    cov = r["computed"]["contract_coverage"]
    assert cov["filled"] == cov["total"], f"coverage {cov['filled']}/{cov['total']}"
    assert len(r["computed"]["duplicate_groups"]) == 2
    assert len(r["computed"]["version_candidates"]) == 1
    assert r["computed"]["reconciliation"]["mismatches"]
    assert r["computed"]["reconciliation"]["missing"]
    for f in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
              "recovery_report.json", "library_index.json",
              "library_manifest.md", "operation_log.jsonl"):
        assert os.path.isfile(os.path.join(out1, f)), f
    degs = " ".join(r.get("degradations", []))
    assert "too_large" in degs
    assert "catalog_parse_error" in degs  # bad.json
    if features["broken_symlink"]:
        assert "broken_symlink" in degs
    # 确定性：同输入两次运行 reproducibility 一致
    core_impl.run(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                  root, out2, os.path.join(tmp, "work2"))
    r2 = _load_result(out2)
    assert r["computed"]["reproducibility"] == r2["computed"]["reproducibility"]
    return "full_run(partial+degradations+coverage+determinism)"


def test_clean_run(tmp):
    root, _features = _make_fixture(tmp, clean=True)
    out = os.path.join(tmp, "out")
    code = core_impl.run(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                         root, out, os.path.join(tmp, "work"))
    assert code == 0
    r = _load_result(out)
    assert r["status"] == "ok", r["status"]
    cov = r["computed"]["contract_coverage"]
    assert cov["filled"] == cov["total"], f"coverage {cov['filled']}/{cov['total']}"
    assert r["computed"]["summary"]["total_files"] >= 6
    return "clean_run(status=ok)"


def test_skeleton(tmp):
    root, _ = _make_fixture(tmp, clean=True)
    out = os.path.join(tmp, "out")
    code = core_impl.run_skeleton(os.path.join(root, "case.json"),
                                  os.path.join(root, "task.md"), root, out,
                                  os.path.join(tmp, "work"))
    assert code == 0
    r = _load_result(out)
    assert r["case_id"] == "selftest-0001"
    assert r["status"] in ("partial", "ok")
    # 契约字段已作为占位存在（null/[]），case_id/status 由保留键满足
    assert "library_manifest" in r or "library_manifest" in r["computed"]
    return "skeleton"


def test_empty_input(tmp):
    root = os.path.join(tmp, "empty_input")
    os.makedirs(root, exist_ok=True)
    case = {"challenge_id": "empty-0001", "output_contract": {"summary": {}}}
    _write(os.path.join(root, "case.json"), json.dumps(case))
    _write(os.path.join(root, "task.md"), "# 空\n")
    out = os.path.join(tmp, "out")
    code = core_impl.run(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                         root, out, os.path.join(tmp, "work"))
    assert code == 0
    r = _load_result(out)
    assert r["status"] == "insufficient_evidence", r["status"]
    return "empty_input(insufficient_evidence)"


def test_bad_case_json(tmp):
    root = os.path.join(tmp, "bad_case")
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "case.json"), "{broken")
    _write(os.path.join(root, "task.md"), "# 任务\n")
    _write(os.path.join(root, "x.txt"), "data\n")
    out = os.path.join(tmp, "out")
    code = core_impl.run(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                         root, out, os.path.join(tmp, "work"))
    assert code == 0
    r = _load_result(out)
    assert r["status"] == "input_error", r["status"]
    return "bad_case_json(input_error)"


def test_missing_case_json(tmp):
    root = os.path.join(tmp, "no_case")
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "task.md"), "# 任务\n")
    _write(os.path.join(root, "x.txt"), "data\n")
    out = os.path.join(tmp, "out")
    code = core_impl.run(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                         root, out, os.path.join(tmp, "work"))
    assert code == 0
    r = _load_result(out)
    assert r["status"] == "input_error", r["status"]
    return "missing_case_json(input_error)"


def test_check(tmp):
    root, _ = _make_fixture(tmp, clean=True)
    out = os.path.join(tmp, "out")
    core_impl.run(os.path.join(root, "case.json"), os.path.join(root, "task.md"),
                  root, out, os.path.join(tmp, "work"))
    code = core_impl.run_check(os.path.join(out, "result.json"),
                               os.path.join(root, "case.json"))
    assert code == 0, f"run_check exit={code}"
    return "check(pass)"


def run():
    tests = [test_full_run, test_clean_run, test_skeleton, test_empty_input,
             test_bad_case_json, test_missing_case_json, test_check]
    failures = []
    for fn in tests:
        tmp = tempfile.mkdtemp(prefix="skill_selftest_")
        try:
            label = fn(tmp)
            print(f"PASS  {fn.__name__}: {label}")
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"selftest: {len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
