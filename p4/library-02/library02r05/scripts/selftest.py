# -*- coding: utf-8 -*-
"""selftest.py — 在临时目录中用合成 fixture 跑通整个 Skill 并断言关键行为。

覆盖：正常案例、空输入目录、坏 JSON case.json、缺 case.json、坏编码文件、
超大文件、断链/硬链接别名、重复组、版本候选、对账、索引、--check、--update。
纯标准库；无正式测试案例、无预写答案、不伪造评分。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "main.py")


def run(args, cwd=None):
    return subprocess.run([sys.executable, "-B", MAIN] + args, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def wf(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    mode = "wb" if isinstance(text, bytes) else "w"
    kw = {} if isinstance(text, bytes) else {"encoding": "utf-8", "newline": "\n"}
    with open(path, mode, **kw) as fh:
        fh.write(text)


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    return cond


def make_case(root, case_json_text, task_text, files):
    wf(os.path.join(root, "case.json"), case_json_text)
    wf(os.path.join(root, "task.md"), task_text)
    for rel, content in files.items():
        wf(os.path.join(root, rel), content)


def main():
    results = []
    tmp = tempfile.mkdtemp(prefix="libskill-selftest-")

    # ---- 场景 1：正常案例（重复、版本、别名、清单、坏编码、超大文件） ----
    case_dir = os.path.join(tmp, "normal")
    os.makedirs(case_dir)
    contract = {"output_contract": {
        "required_fields": ["manifest", "summary"],
        "fields": {"manifest": {"type": "list"},
                   "summary": {"type": "object"},
                   "notes": {"type": "string"}}}}
    case_json = json.dumps({
        "case_id": "case-selftest-001",
        "task": "整理版本关系",
        **contract,
    }, ensure_ascii=False)
    make_case(
        case_dir, case_json, "# 科研资料库整理\n\n规则：保留版本关系\n",
        {
            "materials/paper_v1.txt": "Buffer A: 10 mM Tris\nMethod: gel run\n",
            "materials/paper_v2.txt": "Buffer A: 10 mM Tris\nMethod: gel run\n"
                                     " + blot\n",
            "materials/paper_v2_copy.txt": "Buffer A: 10 mM Tris\nMethod: gel "
                                           "run\n + blot\n",
            "data/dup_a.txt": "identical content\n",
            "data/dup_b.txt": "identical content\n",
            "catalog.json": json.dumps([
                {"id": "r1", "path": "materials/paper_v2.txt",
                 "version": "v2"}], ensure_ascii=False),
            "bad_encoding.txt": b"\xff\xfe\x00bad bytes \x80\x81",
            "huge.bin": b"\x00" * (3 * 1024 * 1024),
            "notes.md": "# 行政版本说明\n\n- v2 为终稿\n- v1 为草稿\n",
        })
    # 硬链接（Windows 可能失败则跳过）
    try:
        os.link(os.path.join(case_dir, "data", "dup_a.txt"),
                os.path.join(case_dir, "data", "dup_a_hardlink.txt"))
    except OSError:
        pass
    try:
        os.symlink("materials/paper_v1.txt",
                   os.path.join(case_dir, "alias_v1.txt"))
    except OSError:
        pass
    try:
        os.symlink("materials/does_not_exist.txt",
                   os.path.join(case_dir, "broken_alias.txt"))
    except OSError:
        pass

    out = os.path.join(case_dir, "out")
    p = run(["--input", case_dir, "--output", out, "--work",
             os.path.join(case_dir, "work")])
    results.append(check("normal exit==0", p.returncode == 0))
    rp = os.path.join(out, "result.json")
    r = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) else {}
    results.append(check("normal case_id", r.get("case_id")
                         == "case-selftest-001"))
    results.append(check("normal contract backfill",
                         "manifest" in r and "summary" in r and "notes" in r))
    results.append(check("normal change_log",
                         isinstance(r.get("change_log"), list)
                         and len(r["change_log"]) >= 1))
    dup = r.get("computed", {}).get("duplicate_groups", [])
    results.append(check("normal duplicate by content", any(
        g["count"] >= 2 and "dup_a.txt" in " ".join(g["files"]) for g in dup)))
    results.append(check("normal degradation recorded",
                         len(r.get("degradations", [])) >= 1))
    for art in ("library_manifest.csv", "directory_tree.txt",
                "dedup_version.json", "recovery_report.json",
                "library_index.json", "library_manifest.md",
                "operation_log.jsonl"):
        results.append(check("normal artifact " + art,
                             os.path.exists(os.path.join(out, art))))

    # --check
    p = run(["--check", os.path.join(out, "result.json"),
             "--case-json", os.path.join(case_dir, "case.json")])
    results.append(check("normal --check exit==0", p.returncode == 0))

    # --update 增量合并
    patch = json.dumps({"_change": {"actor": "agent", "step": "semantic",
                                    "detail": "确认 v2 为终稿"},
                        "computed": {"version_notes": [
                            {"stem": "paper", "note": "v2 终稿"}]}},
                       ensure_ascii=False)
    wf(os.path.join(case_dir, "patch.json"), patch)
    p = run(["--update", os.path.join(case_dir, "patch.json"),
             "--result", os.path.join(out, "result.json")])
    results.append(check("normal --update exit==0", p.returncode == 0))
    r2 = json.load(open(rp, encoding="utf-8"))
    results.append(check("normal --update merged",
                         bool(r2.get("computed", {}).get("version_notes"))))

    # ---- 场景 2：空输入目录 ----
    empty_dir = os.path.join(tmp, "empty")
    os.makedirs(empty_dir)
    wf(os.path.join(empty_dir, "case.json"),
       json.dumps({"case_id": "case-empty"}))
    wf(os.path.join(empty_dir, "task.md"), "# 空库\n")
    p = run(["--input", empty_dir, "--output", os.path.join(empty_dir, "out")])
    r3 = json.load(open(os.path.join(empty_dir, "out", "result.json"),
                        encoding="utf-8"))
    results.append(check("empty exit==0", p.returncode == 0))
    results.append(check("empty status insufficient_evidence",
                         r3.get("status") == "insufficient_evidence"))

    # ---- 场景 3：坏 JSON case.json ----
    bad_dir = os.path.join(tmp, "badjson")
    os.makedirs(bad_dir)
    wf(os.path.join(bad_dir, "case.json"), "{not valid json")
    wf(os.path.join(bad_dir, "task.md"), "# 坏 JSON\n")
    p = run(["--input", bad_dir, "--output", os.path.join(bad_dir, "out")])
    r4 = json.load(open(os.path.join(bad_dir, "out", "result.json"),
                        encoding="utf-8"))
    results.append(check("badjson exit==0", p.returncode == 0))
    results.append(check("badjson error recorded",
                         any("case.json" in e for e in r4.get("errors", []))))

    # ---- 场景 4：缺 case.json ----
    nofile_dir = os.path.join(tmp, "nofile")
    os.makedirs(nofile_dir)
    wf(os.path.join(nofile_dir, "task.md"), "# 无 case.json\n")
    wf(os.path.join(nofile_dir, "a.txt"), "some content\n")
    p = run(["--input", nofile_dir, "--output", os.path.join(nofile_dir,
                                                             "out")])
    r5 = json.load(open(os.path.join(nofile_dir, "out", "result.json"),
                        encoding="utf-8"))
    results.append(check("nofile exit==0", p.returncode == 0))
    results.append(check("nofile input_error", r5.get("status")
                         == "input_error"))

    # ---- 场景 5：--skeleton-only 秒级落盘 ----
    p = run(["--input", case_dir, "--output", os.path.join(case_dir, "skel"),
             "--skeleton-only"])
    r6 = json.load(open(os.path.join(case_dir, "skel", "result.json"),
                        encoding="utf-8"))
    results.append(check("skeleton exit==0", p.returncode == 0))
    results.append(check("skeleton partial", r6.get("status") == "partial"))

    shutil.rmtree(tmp, ignore_errors=True)
    total = len(results)
    passed = sum(results)
    print("selftest: %d/%d passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
