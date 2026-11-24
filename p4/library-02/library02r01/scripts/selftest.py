"""selftest.py - self-test for the library evidence repair skill.

Builds a synthetic mini case under a temp dir, runs the full pipeline,
then runs --check and asserts key artifacts. Pure standard library.
Exit code 0 = all checks passed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def make_case(root):
    os.makedirs(os.path.join(root, "input", "materials"), exist_ok=True)
    case = {
        "case_id": "selftest-case-001",
        "task": "self test",
        "output_contract": {
            "summary": "string",
            "inventory_count": "integer",
            "notes": "array",
        },
    }
    with open(os.path.join(root, "input", "case.json"), "w",
              encoding="utf-8") as f:
        json.dump(case, f, ensure_ascii=False)
    with open(os.path.join(root, "input", "task.md"), "w",
              encoding="utf-8") as f:
        f.write("# 自检任务\n\n## 版本说明\nv1 初始版本\nv2 修订缓冲液配方\n")
    a = os.path.join(root, "input", "materials", "protocol.txt")
    b = os.path.join(root, "input", "materials", "protocol_v2.txt")
    c = os.path.join(root, "input", "materials", "protocol_copy.txt")
    with open(a, "w", encoding="utf-8") as f:
        f.write("缓冲液配方: 10 mM Tris, pH 8.0\n")
    with open(b, "w", encoding="utf-8") as f:
        f.write("缓冲液配方: 10 mM Tris, pH 7.4\n修订版\n")
    shutil.copyfile(a, c)  # duplicate by content
    # catalog
    with open(os.path.join(root, "input", "catalog.json"), "w",
              encoding="utf-8") as f:
        json.dump([{"path": "materials/protocol.txt", "sha256": "0" * 64}],
                  f, ensure_ascii=False)
    # bad json
    with open(os.path.join(root, "input", "bad.json"), "w",
              encoding="utf-8") as f:
        f.write("{not valid json")
    return a, b, c


def main():
    tmp = tempfile.mkdtemp(prefix="selftest-lib-")
    out = os.path.join(tmp, "output")
    work = os.path.join(tmp, "work")
    try:
        make_case(tmp)
        r = sh([sys.executable, "-B", MAIN,
                "--case-json", os.path.join(tmp, "input", "case.json"),
                "--task-path", os.path.join(tmp, "input", "task.md"),
                "--input-root", os.path.join(tmp, "input"),
                "--output-dir", out, "--work-dir", work])
        if r.returncode != 0:
            print("FAIL full run rc=%d\n%s\n%s" % (r.returncode, r.stdout,
                                                   r.stderr))
            return 1
        res_path = os.path.join(out, "result.json")
        with open(res_path, encoding="utf-8") as f:
            res = json.load(f)
        assert res["case_id"] == "selftest-case-001", res["case_id"]
        assert res["status"] in ("partial", "ok"), res["status"]
        assert res.get("_contract_key") == "output_contract"
        # contract fields backfilled
        for k in ("summary", "inventory_count", "notes"):
            assert k in res, "contract field %s missing" % k
        # artifacts
        for art in ("library_manifest.csv", "directory_tree.txt",
                    "dedup_version.json", "recovery_report.json",
                    "library_index.json", "evidence_index.json",
                    "library_manifest.md", "operation_log.jsonl"):
            p = os.path.join(out, art)
            assert os.path.isfile(p) and os.path.getsize(p) > 0, art
        # evidence map quotes are verbatim and span-annotated
        em = res["evidence_map"]["conclusions"]
        assert len(em) > 0
        for c in em:
            for loc in c.get("locators", []):
                assert "quote" in loc and "quote_span" in loc
        # check subcommand
        r2 = sh([sys.executable, "-B", MAIN, "--check", res_path,
                 "--case-json", os.path.join(tmp, "input", "case.json")])
        if r2.returncode != 0:
            print("FAIL check\n%s\n%s" % (r2.stdout, r2.stderr))
            return 1
        # search subcommand
        r3 = sh([sys.executable, "-B", MAIN, "--search", "缓冲液",
                 "--index", os.path.join(out, "library_index.json")])
        if r3.returncode != 0 or "缓冲液" not in r3.stdout:
            print("FAIL search rc=%d\n%s" % (r3.returncode, r3.stdout))
            return 1
        print("selftest OK: case_id=%s status=%s evidence=%d" % (
            res["case_id"], res["status"], len(em)))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
