#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检脚本：校验 SKILL.md frontmatter、脚本可运行、空输入/坏 JSON 降级、
契约字段完整、体积与文件数限制。仅标准库。

用法：python scripts/selftest.py [--skill-root <path>]
输出：<root>/outputs/selftest_report.json（若可写）与 stdout 摘要。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # Skill 根目录


def sh(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=cwd, timeout=timeout)
        return {"rc": r.returncode, "out": r.stdout[:4000], "err": r.stderr[:2000]}
    except Exception as e:  # noqa: BLE001
        return {"rc": -1, "out": "", "err": "exception: %s" % e}


def check_frontmatter(skill_md):
    text = open(skill_md, encoding="utf-8").read()
    fm = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
    return {"has_name": bool(fm.get("name")),
            "has_description": bool(fm.get("description")),
            "has_version": bool(fm.get("version"))}


def check_size_and_count(root):
    files, total = [], 0
    for dirpath, _, names in os.walk(root):
        for n in names:
            fp = os.path.join(dirpath, n)
            if os.path.isfile(fp):
                sz = os.path.getsize(fp)
                files.append((fp, sz))
                total += sz
    main_size = os.path.getsize(os.path.join(root, "scripts", "main.py")) \
        if os.path.exists(os.path.join(root, "scripts", "main.py")) else 0
    return {"file_count": len(files), "total_bytes": total,
            "main_py_bytes": main_size}


def run_case(name, args, cwd=ROOT):
    r = sh([sys.executable, "-B", "scripts/main.py"] + args, cwd=cwd)
    return {"name": name, "rc": r["rc"], "out": r["out"], "err": r["err"]}


def main(argv=None):
    skill_root = ROOT
    import argparse
    ap = argparse.ArgumentParser(description="结构优化 Skill 自检")
    ap.add_argument("--out", default=None, help="自检报告输出路径（默认临时目录）")
    ns = ap.parse_args(argv)
    report = {"skill_root": skill_root, "checks": []}

    skill_md = os.path.join(skill_root, "SKILL.md")
    if os.path.exists(skill_md):
        fm = check_frontmatter(skill_md)
        report["checks"].append({"item": "frontmatter_name_description_version",
                                 "ok": all(fm.values()), "detail": fm})
    else:
        report["checks"].append({"item": "SKILL_md_exists", "ok": False})

    sc = check_size_and_count(skill_root)
    report["checks"].append({"item": "file_count<=14", "ok": sc["file_count"] <= 14,
                             "detail": sc["file_count"]})
    report["checks"].append({"item": "total<=1.2MB",
                             "ok": sc["total_bytes"] <= 1200 * 1024,
                             "detail": sc["total_bytes"]})
    report["checks"].append({"item": "main_py<30KB",
                             "ok": sc["main_py_bytes"] < 30 * 1024,
                             "detail": sc["main_py_bytes"]})

    # --help 退出码 0
    r = sh([sys.executable, "-B", "scripts/main.py", "--help"], cwd=skill_root)
    report["checks"].append({"item": "main_help_rc0", "ok": r["rc"] == 0,
                             "detail": r["rc"]})

    # 空输入目录 → insufficient_evidence，退出码 0，且 result.json 合法
    tmp = tempfile.mkdtemp(prefix="selftest_")
    try:
        empty_in = os.path.join(tmp, "in"); os.makedirs(empty_in)
        empty_out = os.path.join(tmp, "out"); os.makedirs(empty_out)
        empty_work = os.path.join(tmp, "work"); os.makedirs(empty_work)
        r = sh([sys.executable, "-B", "scripts/main.py", "--full",
                "--case", os.path.join(empty_in, "case.json"),
                "--input", empty_in,
                "--work", empty_work,
                "--out", os.path.join(empty_out, "result.json")], cwd=skill_root)
        res_empty = None
        try:
            res_empty = json.load(open(os.path.join(empty_out, "result.json"),
                                       encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        ok_empty = (r["rc"] == 0 and isinstance(res_empty, dict)
                    and res_empty.get("status") == "insufficient_evidence"
                    and "case_id" in res_empty)
        report["checks"].append({"item": "empty_input_degrade", "ok": ok_empty,
                                 "detail": {"rc": r["rc"],
                                            "status": res_empty.get("status") if res_empty else None}})

        # 坏 JSON case.json → 记录 errors，不崩溃
        bad_in = os.path.join(tmp, "badin"); os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "x", "broken": ')
        with open(os.path.join(bad_in, "notes.md"), "w", encoding="utf-8") as fh:
            fh.write("# Title\n\nThis study shows that A causes B. The result is 3.5 fold.\n")
        bad_out = os.path.join(tmp, "badout"); os.makedirs(bad_out)
        r2 = sh([sys.executable, "-B", "scripts/main.py", "--full",
                 "--case", os.path.join(bad_in, "case.json"),
                 "--input", bad_in,
                 "--work", os.path.join(tmp, "badwork"),
                 "--out", os.path.join(bad_out, "result.json")], cwd=skill_root)
        res_bad = None
        try:
            res_bad = json.load(open(os.path.join(bad_out, "result.json"),
                                     encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        ok_bad = (r2["rc"] == 0 and isinstance(res_bad, dict)
                  and any("bad_json" in str(e) for e in res_bad.get("errors", [])))
        report["checks"].append({"item": "bad_json_tolerated", "ok": ok_bad,
                                 "detail": {"rc": r2["rc"],
                                            "errors": res_bad.get("errors") if res_bad else None}})

        # 正常材料 → sources 带 locator+quote，六类诊断状态齐全
        norm_in = os.path.join(tmp, "normin"); os.makedirs(norm_in)
        with open(os.path.join(norm_in, "case.json"), "w", encoding="utf-8") as fh:
            json.dump({"case_id": "selftest-case", "output_contract": {}}, fh)
        with open(os.path.join(norm_in, "paper.md"), "w", encoding="utf-8") as fh:
            fh.write("# Abstract\nThis study investigates whether X improves Y. "
                     "We show X improves Y by 12% (p<0.05).\n\n"
                     "# Methods\nWe used a standard protocol.\n\n"
                     "# Results\nFigure 1 shows the trend. X caused Y.\n\n"
                     "# Conclusion\nX therefore always improves Y.\n")
        norm_out = os.path.join(tmp, "normout"); os.makedirs(norm_out)
        r3 = sh([sys.executable, "-B", "scripts/main.py", "--full",
                 "--case", os.path.join(norm_in, "case.json"),
                 "--input", norm_in,
                 "--work", os.path.join(tmp, "normwork"),
                 "--out", os.path.join(norm_out, "result.json")], cwd=skill_root)
        res_norm = None
        try:
            res_norm = json.load(open(os.path.join(norm_out, "result.json"),
                                      encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        ok_norm = (r3["rc"] == 0 and isinstance(res_norm, dict)
                   and res_norm.get("case_id") == "selftest-case"
                   and all(k in res_norm for k in
                           ("diagnosis", "edit_plan", "claim_evidence_map",
                            "source_map", "version_boundary"))
                   and res_norm.get("sources")
                   and all("locator" in s and "quote" in s
                           for s in res_norm.get("sources", [])))
        report["checks"].append({"item": "normal_case_contract", "ok": ok_norm,
                                 "detail": {"rc": r3["rc"],
                                            "n_sources": len(res_norm.get("sources", [])) if res_norm else None,
                                            "status": res_norm.get("status") if res_norm else None}})
    finally:
        # 尽力清理临时目录（保留失败现场不利于误删判断，这里可控所以删除）
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)

    report["all_ok"] = all(c["ok"] for c in report["checks"])
    report["summary"] = "PASS %d/%d" % (
        sum(1 for c in report["checks"] if c["ok"]), len(report["checks"]))

    out_path = ns.out or os.path.join(tempfile.gettempdir(),
                                      "selftest_report_%d.json" % os.getpid())
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except OSError:
        out_path = None
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("REPORT_PATH:", out_path)
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
