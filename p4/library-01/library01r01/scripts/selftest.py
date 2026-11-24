"""selftest.py - 运行时自检：在系统临时目录生成合成输入并清理，不随包预置正式测试产物。"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core_impl as ci  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def build_fixture(root):
    case = {
        "case_id": "selftest-case-1",
        "task": "synthetic self-test, not a formal case",
        "output_contract": {
            "library_summary": "required",
            "n_files": 0,
            "report": None,
        },
    }
    _write(os.path.join(root, "case.json"), json.dumps(case, ensure_ascii=False))
    _write(os.path.join(root, "task.md"),
           "# 合成自检任务\n\n版本说明：draft_v2 替换 draft_v1。\n")
    _write(os.path.join(root, "materials", "data", "a.txt"), "hello world v1\n")
    _write(os.path.join(root, "materials", "data", "b.txt"), "hello world v1\n")
    _write(os.path.join(root, "materials", "data", "draft_v1.txt"),
           "version one content\nline2\n")
    _write(os.path.join(root, "materials", "data", "draft_v2.txt"),
           "version two content\nline2 changed\n")
    _write(os.path.join(root, "materials", "notes.md"),
           "版本说明：draft_v2 替换 draft_v1。\n")
    catalog = [
        {"path": "materials/data/a.txt", "sha256": ci.sha256_text("hello world v1\n")},
        {"path": "materials/data/missing.pdf", "sha256": "ffff"},
    ]
    _write(os.path.join(root, "materials", "catalog.json"),
           json.dumps(catalog, ensure_ascii=False))
    _write(os.path.join(root, "materials", "bad.json"), "{ this is not json ")
    os.makedirs(os.path.join(root, "materials", "empty"), exist_ok=True)
    # 断链符号链接（Windows 无特权时跳过，不判失败）
    try:
        os.symlink(os.path.join(root, "materials", "no_such_target.pdf"),
                   os.path.join(root, "materials", "broken_link.pdf"))
    except (OSError, NotImplementedError):
        pass
    return case


def run_case(input_root):
    out = tempfile.mkdtemp(prefix="selftest_out_")
    work = tempfile.mkdtemp(prefix="selftest_work_")
    ci.run_pipeline(input_root, out, work)
    return out, work


def test_full(root, case):
    out, work = run_case(root)
    result_path = os.path.join(out, "result.json")
    with open(result_path, "r", encoding="utf-8") as fh:
        result = json.load(fh)
    assert result.get("case_id") == "selftest-case-1", "case_id 错误"
    assert result.get("status") in ("ok", "partial"), "status 异常"
    for k in ("library_summary", "n_files", "report"):
        assert k in result, f"合同字段缺失: {k}"
    claims = result.get("evidence_map", {}).get("claims", [])
    assert len(claims) > 0, "evidence_map 无条目"
    dup = result.get("computed", {}).get("duplicate_groups", [])
    assert any(any("a.txt" in m for m in g["members"])
               and any("b.txt" in m for m in g["members"]) for g in dup), "去重失败"
    ver = result.get("computed", {}).get("version_candidates", [])
    assert any(any("draft_v1.txt" in n["path"] for n in v["chain"]) for v in ver), "版本链失败"
    recon = result.get("computed", {}).get("reconciliation", {})
    assert recon.get("summary", {}).get("missing", 0) >= 1, "对账 missing 判定失败"
    for name in ("library_manifest.csv", "directory_tree.txt", "dedup_version.json",
                 "recovery_report.json", "library_index.json", "evidence_map.json",
                 "library_manifest.md", "operation_log.jsonl"):
        assert os.path.isfile(os.path.join(out, name)), f"产物缺失: {name}"
    ok, problems = ci.check(result_path, case, out)
    assert ok, f"check 失败: {problems}"
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    return "full pipeline: PASS"


def test_empty():
    root = tempfile.mkdtemp(prefix="selftest_empty_")
    _write(os.path.join(root, "case.json"),
           json.dumps({"case_id": "selftest-empty", "output_contract": {"x": 1}}))
    out, work = run_case(root)
    with open(os.path.join(out, "result.json"), "r", encoding="utf-8") as fh:
        result = json.load(fh)
    assert result.get("status") == "insufficient_evidence", "空目录状态错误"
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    return "empty input: PASS"


def test_missing_case():
    root = tempfile.mkdtemp(prefix="selftest_nocase_")
    _write(os.path.join(root, "task.md"), "# 无 case.json\n")
    _write(os.path.join(root, "materials", "x.txt"), "some content\n")
    out, work = run_case(root)
    with open(os.path.join(out, "result.json"), "r", encoding="utf-8") as fh:
        result = json.load(fh)
    assert result.get("status") == "input_error", "缺 case.json 状态错误"
    assert result.get("case_id") is None or isinstance(result.get("case_id"), str)
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    return "missing case.json: PASS"


def main():
    results = []
    root = tempfile.mkdtemp(prefix="selftest_input_")
    try:
        case = build_fixture(root)
        results.append(test_full(root, case))
        results.append(test_empty())
        results.append(test_missing_case())
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("selftest:", "; ".join(results))
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
