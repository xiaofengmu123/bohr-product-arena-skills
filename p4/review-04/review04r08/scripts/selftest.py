# -*- coding: utf-8 -*-
"""selftest.py — 论文逻辑与论证审查 Skill 的自测脚本（纯标准库）。

在临时目录内构造四类输入，端到端运行 main.py 的 scaffold/selfcheck，验证：
  1) 正常输入 → 产出合法 partial 骨架，output_contract 键回填完整；
  2) 空输入目录 → status=insufficient_evidence，退出码 0，不崩溃；
  3) 坏 JSON case.json → 记录 errors，仍产出合法 result.json；
  4) 坏编码材料 → 回退解码并记录，不崩溃；
  5) 确定性：同一正常输入两次 scaffold 产出 byte 级一致（ts 留空）。

用法：
  python3 -B scripts/selftest.py            # 在 SKILL.md 所在目录运行
  python3 -B scripts/selftest.py --keep     # 保留临时目录以便排查
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import review_core as rc

rc._reconfigure_streams()

HERE = Path(__file__).resolve().parent
MAIN_PY = HERE / "main.py"


def _run(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(MAIN_PY)] + args,
        cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _read_result(out_dir: Path) -> Optional[Dict[str, Any]]:
    p = out_dir / "result.json"
    if not p.is_file():
        return None
    data, _ = rc.read_json_tolerant(str(p))
    return data


def _norm(result: Dict[str, Any]) -> Dict[str, Any]:
    """去除执行元数据后用于确定性比较。"""
    r = json.loads(json.dumps(result, ensure_ascii=False))
    if isinstance(r.get("runtime"), dict):
        r["runtime"]["seconds_elapsed"] = 0.0
    return r


def _make_case(tmp: Path, name: str, *, bad_json: bool = False,
               bad_encoding: bool = False) -> Path:
    d = tmp / name / "input"
    d.mkdir(parents=True)
    (d / "task.md").write_text(
        "# 论文逻辑与论证审查\n\n这是自测用的最小任务说明，仅用于验证脚本接口。\n",
        encoding="utf-8",
    )
    if bad_json:
        (d / "case.json").write_text('{"case_id": "t01", "output_contract": {"report": 1', encoding="utf-8")
    else:
        (d / "case.json").write_text(json.dumps({
            "case_id": "t01",
            "challenge_id": "challenge-test",
            "title": "自测样例",
            "output_contract": {"review_summary": "", "findings_list": []},
        }, ensure_ascii=False), encoding="utf-8")
    mat = d / "materials"
    mat.mkdir()
    if bad_encoding:
        (mat / "paper.txt").write_bytes("摘要：这是一段坏编码示例。\x00\xff\xfe 测试。".encode("utf-16"))
    else:
        (mat / "paper.txt").write_text(
            "# 引言\n\n本研究提出一种方法。\n\n# 方法\n\n采用对照实验。\n\n# 结果\n\n数据显示改进。\n",
            encoding="utf-8",
        )
    return d


def _case_empty(tmp: Path) -> Path:
    d = tmp / "empty" / "input"
    d.mkdir(parents=True)
    return d


def _check(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (mark, name, detail))
    return cond


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="paper-review-selftest-"))
    ok = True

    try:
        # 1) 正常输入
        normal_in = _make_case(tmp, "normal")
        normal_out = tmp / "normal" / "output"
        normal_work = tmp / "normal" / "work"
        r = _run(["scaffold", "--input", str(normal_in),
                  "--output", str(normal_out), "--work", str(normal_work)], HERE)
        res = _read_result(normal_out)
        ok &= _check("normal.scaffold.exit0", r.returncode == 0, r.stderr.strip()[:120])
        ok &= _check("normal.status", res is not None and res.get("status") == "partial",
                     "status=%s" % (res and res.get("status")))
        ok &= _check("normal.contract", res is not None and
                     "review_summary" in res and "findings_list" in res,
                     "contract_keys=%s" % (res and res.get("__contract_keys__")))
        ok &= _check("normal.corpus", (normal_work / "corpus.md").is_file())
        sc = _run(["selfcheck", "--output", str(normal_out), "--work", str(normal_work)], HERE)
        ok &= _check("normal.selfcheck", sc.returncode == 0, sc.stdout.strip()[-80:])

        # 5) 确定性：同一输入、同一输出目录重跑 scaffold（--force），结果一致
        first_snapshot = _read_result(normal_out)
        r = _run(["scaffold", "--input", str(normal_in), "--force",
                  "--output", str(normal_out), "--work", str(normal_work)], HERE)
        res2 = _read_result(normal_out)
        ok &= _check("determinism.exit0", r.returncode == 0)
        ok &= _check("determinism", first_snapshot is not None and res2 is not None and
                     _norm(first_snapshot) == _norm(res2))

        # 2) 空输入
        empty_in = _case_empty(tmp)
        empty_out = tmp / "empty" / "output"
        empty_work = tmp / "empty" / "work"
        r = _run(["scaffold", "--input", str(empty_in),
                  "--output", str(empty_out), "--work", str(empty_work)], HERE)
        eres = _read_result(empty_out)
        ok &= _check("empty.exit0", r.returncode == 0)
        ok &= _check("empty.status", eres is not None and
                     eres.get("status") == "insufficient_evidence",
                     "status=%s" % (eres and eres.get("status")))

        # 3) 坏 JSON
        badj_in = _make_case(tmp, "badj", bad_json=True)
        badj_out = tmp / "badj" / "output"
        badj_work = tmp / "badj" / "work"
        r = _run(["scaffold", "--input", str(badj_in),
                  "--output", str(badj_out), "--work", str(badj_work)], HERE)
        bres = _read_result(badj_out)
        ok &= _check("badj.exit0", r.returncode == 0)
        ok &= _check("badj.valid", bres is not None and bres.get("status") == "partial",
                     "status=%s" % (bres and bres.get("status")))
        ok &= _check("badj.error_recorded", bres is not None and len(bres.get("errors", [])) > 0)

        # 4) 坏编码
        badenc_in = _make_case(tmp, "badenc", bad_encoding=True)
        badenc_out = tmp / "badenc" / "output"
        badenc_work = tmp / "badenc" / "work"
        r = _run(["scaffold", "--input", str(badenc_in),
                  "--output", str(badenc_out), "--work", str(badenc_work)], HERE)
        eres2 = _read_result(badenc_out)
        ok &= _check("badenc.exit0", r.returncode == 0)
        ok &= _check("badenc.valid", eres2 is not None and eres2.get("status") == "partial")

    finally:
        if args.keep:
            print("临时目录保留: %s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 40)
    print("selftest %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
