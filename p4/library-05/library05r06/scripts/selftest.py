#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 包级自检：在临时目录构造最小案例，跑完整链路并断言。

覆盖：正常案例、空输入、坏 JSON、缺 case.json、无模型直接 build、重复运行确定性。
退出码 0 表示自检通过；失败以非零退出码报出（供 CI/评审复跑）。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import core  # noqa: E402
import main as entry  # noqa: E402


def make_case(base: Path, *, bad_json=False, no_case=False, with_files=True, empty=False):
    inp = base / "input"
    work = base / "work"
    out = base / "output"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "task.md").write_text(
        "# 文件库修复\n\n请整理 materials 下的资料，回答：report 最终版本是哪个？\n",
        encoding="utf-8",
    )
    if no_case:
        pass
    elif bad_json:
        (inp / "case.json").write_text("{not valid json", encoding="utf-8")
    else:
        case = {
            "case_id": "SELFTEST-001",
            "output_contract": {
                "files": "file inventory",
                "duplicates": "duplicate groups",
                "version_relations": "version groups",
                "retrieval": "query answers",
                "unmapped_custom_key": "custom",
            },
        }
        (inp / "case.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")

    if with_files and not empty:
        mat = inp / "materials"
        mat.mkdir(exist_ok=True)
        (mat / "notes.md").write_text(
            "行政版本备注：report_v2.pdf 是 report.pdf 的正式修订版，旧版请归档。\n",
            encoding="utf-8",
        )
        (mat / "report.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 100)
        (mat / "report_v2.pdf").write_bytes(b"%PDF-1.4\n" + b"y" * 100)
        # 重复下载：与 report.pdf 内容一致
        (mat / "report (1).pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 100)
        # 无版本标记的同 stem 冲突
        (mat / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (mat / "data_new.csv").write_text("a,b\n3,4\n", encoding="utf-8")
    return inp, work, out


def run_pipeline(base):
    inp, work, out = make_case(base)
    entry.cmd_init(inp, work, out)
    entry.cmd_show(inp, work, out)
    entry.cmd_check(inp, work, out)
    entry.cmd_build(inp, work, out)
    summary = json.loads(entry.cmd_selfcheck(inp, work, out))
    return inp, work, out, summary


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def main():
    root = Path(tempfile.mkdtemp(prefix="lib-selftest-"))
    try:
        results = []

        # 1) 正常案例
        base = root / "normal"
        inp, work, out, summary = run_pipeline(base)
        assert_true(summary.get("ok"), "normal selfcheck not ok: %s" % summary)
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert_true(r.get("case_id") == "SELFTEST-001", "case_id mismatch")
        assert_true(r.get("status") in core.ALLOWED_STATUS, "bad status")
        for k in ("sources", "uncertainty", "change_log", "pending_human"):
            assert_true(k in r, "missing envelope %s" % k)
        # 契约键逐键回填
        for k in ("files", "duplicates", "version_relations", "retrieval"):
            assert_true(k in r, "missing contract key %s" % k)
        # 确定性：再跑一次 build，result.json 除 runtime 外一致
        entry.cmd_build(inp, work, out)
        r2 = json.loads((out / "result.json").read_text(encoding="utf-8"))
        r_no_rt = {k: v for k, v in r.items() if k != "runtime"}
        r2_no_rt = {k: v for k, v in r2.items() if k != "runtime"}
        assert_true(r_no_rt == r2_no_rt, "result.json 非确定性（除 runtime）")
        results.append("normal: ok")

        # 2) 空输入目录
        base = root / "empty"
        inp, work, out = make_case(base, empty=True)
        entry.cmd_init(inp, work, out)
        entry.cmd_build(inp, work, out)
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert_true(r.get("status") == "insufficient_evidence", "empty case status wrong")
        results.append("empty: ok")

        # 3) 坏 JSON
        base = root / "badjson"
        inp, work, out = make_case(base, bad_json=True)
        entry.cmd_init(inp, work, out)
        entry.cmd_build(inp, work, out)
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert_true(r.get("status") == "input_error", "badjson status wrong")
        assert_true(any(e.get("kind") == "invalid_case_json" for e in r.get("errors", [])),
                    "badjson error not recorded")
        results.append("badjson: ok")

        # 4) 缺 case.json（有材料）
        base = root / "nocase"
        inp, work, out = make_case(base, no_case=True)
        entry.cmd_init(inp, work, out)
        entry.cmd_build(inp, work, out)
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert_true(r.get("status") == "input_error", "nocase status wrong")
        assert_true(len(r.get("result", {}).get("files", [])) > 0, "nocase still inventoried")
        results.append("nocase: ok")

        # 5) 无模型直接 build（partial + no_model 降级）
        base = root / "nomodel"
        inp, work, out = make_case(base)
        entry.cmd_init(inp, work, out)
        (work / "model.json").write_text(json.dumps(core.model_template("SELFTEST-001"),
                                                    ensure_ascii=False), encoding="utf-8")
        entry.cmd_build(inp, work, out)
        r = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert_true(r.get("status") == "partial", "nomodel status wrong")
        results.append("nomodel: ok")

        print("SELFTEST PASS: " + "; ".join(results))
        return 0
    except Exception:
        traceback.print_exc()
        print("SELFTEST FAIL", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())