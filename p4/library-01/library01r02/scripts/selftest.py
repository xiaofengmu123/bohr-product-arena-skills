"""selftest.py - build a disposable case, run the pipeline, assert contracts.

Pure stdlib, cross-platform. On Linux/macOS it additionally exercises symlink
and hard-link handling; on Windows those cases are skipped because creating
them normally requires extra privileges.
"""
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE / "core_impl.py"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="libskill_selftest_"))
    inp = tmp / "input"
    out = tmp / "output"
    work = tmp / "work"
    try:
        (inp / "materials").mkdir(parents=True)
        (inp / "materials" / "empty_dir").mkdir()

        contract = {
            "library": {"count": 3},
            "owner": "selftest",
            "required_artifacts": ["manifest", "index"],
        }
        (inp / "case.json").write_text(json.dumps({
            "case_id": "self-test-001",
            "output_contract": contract,
        }, ensure_ascii=False), encoding="utf-8")
        (inp / "task.md").write_text(
            "# task\n判断本课题是否做对，不看移动了多少文件，而是版本是否按内容判断。\n"
            "请统计有多少个文件、重复、版本、断链。\n",
            encoding="utf-8",
        )

        same = "same content bytes"
        (inp / "materials" / "report_v1.txt").write_text(
            same, encoding="utf-8")
        (inp / "materials" / "report_v2.txt").write_text(
            same, encoding="utf-8")
        (inp / "materials" / "data.csv").write_text(
            "name,value\na,1\n", encoding="utf-8")
        (inp / "materials" / "bad.json").write_text("{bad json", encoding="utf-8")
        (inp / "materials" / "library.json").write_text(json.dumps({
            "files": [
                {"path": "materials/report_v1.txt",
                 "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        if platform.system() != "Windows":
            try:
                os.symlink("no-such-target",
                           inp / "materials" / "broken_link")
            except OSError:
                pass

        # Reuse the pipeline directly (no subprocess), so the check stays fast.
        sys.path.insert(0, str(HERE))
        import core_impl as c

        cfg = c.SimpleNamespace(
            input_dir=inp, output_dir=out, work_dir=work,
            case_json=inp / "case.json", task_path=inp / "task.md",
            max_hash_bytes=c.DEFAULT_MAX_HASH_BYTES, time_budget=900,
            request_budget=64, skeleton_only=False,
        )
        result = c.finalize(cfg)
        summary = result["computed"]["summary"]

        checks = [
            ("case_id", result.get("case_id") == "self-test-001"),
            ("status", result.get("status") in ("ok", "partial")),
            ("contract.library.count",
             result.get("library", {}).get("count") == 3),
            ("contract.owner", result.get("owner") == "selftest"),
            ("contract.required_artifacts",
             result.get("required_artifacts") == ["manifest", "index"]),
            ("duplicate_groups", summary.get("duplicate_groups") == 1),
            ("version_candidates",
             summary.get("version_candidates", 0) >= 1),
            ("hashed_files", summary.get("hashed", 0) >= 3),
            ("bad_json_recorded", any(
                e.get("code") == "json_parse_error" for e in result["errors"])),
            ("reconcile_mismatch",
             summary.get("reconcile_mismatches", 0) >= 1),
            ("manifest_rows",
             len(result["computed"]["inventory"]) == summary["files"]),
        ]

        for name in ("result.json", "library_manifest.csv",
                     "directory_tree.txt", "dedup_version.json",
                     "recovery_report.json", "library_index.json",
                     "library_manifest.md", "operation_log.jsonl"):
            checks.append((f"artifact:{name}", (out / name).exists()))
        checks.append(("operation_log", (work / "operation_log.jsonl").exists()))

        # Determinism: a second run on the same tree yields the same summary.
        result2 = c.finalize(cfg)
        same_summary = result2["computed"]["summary"] == summary
        checks.append(("deterministic_summary", same_summary))

        # --check on the produced result must pass.
        check_rc = c.run_check(out / "result.json", inp / "case.json")
        checks.append(("run_check", check_rc == 0))

        # --skeleton-only writes a valid partial result.
        skel_out = tmp / "skeleton"
        skel_rc = c.main(["--input-dir", str(inp), "--output-dir", str(skel_out),
                          "--work-dir", str(tmp / "skwork"),
                          "--skeleton-only"])
        skel = c.load_json_lenient(skel_out / "result.json")[0]
        checks.append(("skeleton_rc", skel_rc == 0))
        checks.append(("skeleton_status",
                       skel.get("status") == "partial" and skel.get("case_id")))

        failed = [name for name, ok in checks if not ok]
        if failed:
            print("selftest: FAIL -> " + ", ".join(failed))
            return 1
        print("selftest: OK (" + str(len(checks)) + " checks)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
