# -*- coding: utf-8 -*-
"""selftest.py — 确定性自检脚本（纯标准库，可在提交前/后反复运行）。

证明三件事（对应评分维度"增益可验证性 / 可靠体验 / 异常处理"）：
  1. 确定性：同一输入运行两次，result.json 内容一致（仅时间戳/耗时等易变字段除外）；
  2. 契约完整：scaffold 回填 output_contract 全部键，update 排序重编号稳定；
  3. 降级健壮：空输入、坏 JSON、坏编码均落盘合法结果且记录 errors/degradations，不崩溃。

运行：
  python3 -B scripts/selftest.py
退出码：0 表示全部通过；1 表示有失败项。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 允许从脚本所在目录导入同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402
import review_core as rc  # noqa: E402

SYNTHETIC_PAPER = """# Synthetic Corpus for Determinism Self-Test (example only, not a real case)

## Introduction

Cancer immunotherapy has transformed oncology in the past decade. Recent trials show durable responses in a subset of patients.

However, the response rate remains below forty percent in most solid tumors, and reliable biomarkers are still lacking.

We hypothesize that tumor mutational burden predicts response to checkpoint blockade.

## Methods

We enrolled two hundred patients with non-small cell lung cancer between 2018 and 2021. All patients received anti-PD-1 therapy.

The primary endpoint was progression-free survival. We measured tumor mutational burden by whole-exome sequencing.

## Results

Patients with high tumor mutational burden had longer progression-free survival than those with low burden (median 12.3 versus 6.1 months).

This difference was statistically significant (p = 0.02). The effect persisted after adjustment for smoking status.

## Discussion

Our results confirm that tumor mutational burden causes better response to immunotherapy.

This causal interpretation is not warranted by the observational design, which can only establish association.
"""

CAUSAL_QUOTE = ("Our results confirm that tumor mutational burden causes better "
                "response to immunotherapy.")
RESULT_QUOTE = ("Patients with high tumor mutational burden had longer "
                "progression-free survival than those with low burden "
                "(median 12.3 versus 6.1 months).")

SYNTHETIC_CASE = {
    "id": "selftest-synthetic-case",
    "challenge_id": "challenge-16d4414d",
    "title": "合成自检案例（示例，非正式案例）",
    "output_contract": {
        "custom_metric": {"required": True, "type": "object"},
        "legacy_extra_key": "must_be_echoed",
    },
}

RESULTS: List[Dict[str, Any]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append({"name": name, "ok": bool(cond), "detail": detail})
    return bool(cond)


def canonicalize(obj: Any) -> Any:
    """递归去掉易变字段，使两次运行的产物可逐字节比较。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "ts" and isinstance(v, str):
                out[k] = "T"
            elif k == "seconds_elapsed":
                out[k] = 0.0
            else:
                out[k] = canonicalize(v)
        return out
    if isinstance(obj, list):
        return [canonicalize(x) for x in obj]
    return obj


def read_result(path: str) -> Dict[str, Any]:
    data, err = rc.read_json_tolerant(path)
    if err:
        raise RuntimeError("result.json 无法解析: %s" % err)
    return data


def find_locator(paper_path: str, quote: str) -> Tuple[Optional[str], Optional[str]]:
    """在材料中定位 quote 所在段落，返回 (locator, section)。"""
    ex = rc.extract_text(paper_path)
    recs = rc.split_paragraphs(ex["pages"], paper_path)
    nq = rc.normalize(quote)
    for rec in recs:
        if nq in rc.normalize(rec["text"]):
            return rc.locator(rec), rec["section"]
    return None, None


def build_synthetic_input(root: Path) -> str:
    in_dir = root / "input"
    (in_dir / "materials").mkdir(parents=True)
    (in_dir / "case.json").write_text(
        json.dumps(SYNTHETIC_CASE, ensure_ascii=False), encoding="utf-8")
    (in_dir / "task.md").write_text(
        "# 合成自检任务\n\n这是确定性自检用合成输入，仅作示例。\n", encoding="utf-8")
    (in_dir / "materials" / "PAPER.md").write_text(SYNTHETIC_PAPER, encoding="utf-8")
    return str(in_dir)


def build_findings(paper_path: str) -> List[Dict[str, Any]]:
    loc, _ = find_locator(paper_path, CAUSAL_QUOTE)
    if not loc:
        raise RuntimeError("合成摘录未在材料中定位到（self-test 数据问题）")
    return [{
        "type": "causal_inference",
        "severity": "major",
        "source": paper_path,
        "locator": loc,
        "quote": CAUSAL_QUOTE,
        "claim": "作者把相关性表述为因果结论。",
        "evidence": "观察性研究设计只能建立相关性；该因果表述超出数据支撑。",
        "reasoning": {"affects_main_conclusion": True, "fixable": True,
                      "affects_reproducibility": False,
                      "note": "把 causes 降级为 is associated with 即可修复"},
        "suggestion": "将 Discussion 中 causes better response 改为 is associated with better response。",
        "status": "confirmed",
        "confidence": "high",
    }]


def build_claims(paper_path: str) -> List[Dict[str, Any]]:
    loc, section = find_locator(paper_path, RESULT_QUOTE)
    if not loc:
        raise RuntimeError("合成主张未在材料中定位到（self-test 数据问题）")
    return [{
        "source": paper_path,
        "locator": loc,
        "text": RESULT_QUOTE,
        "speaker": "author",
        "section": section or "Results",
        "verdict": "supported",
        "evidence": "给出了中位值与 p 值，与统计报告一致。",
    }]


SYNTHETIC_COVERAGE = {
    "factual_verification": {"status": "done", "checked": 1, "note": "示例"},
    "citation_support": {"status": "done", "checked": 0, "note": "示例"},
    "method_design": {"status": "done", "checked": 1, "note": "示例"},
    "statistical_reporting": {"status": "done", "checked": 1, "note": "示例"},
    "argument_chain": {"status": "done", "checked": 1, "note": "示例"},
    "academic_norm": {"status": "partial", "checked": 0, "note": "示例"},
}


def run_pipeline(in_dir: str, out_dir: str, work_dir: str,
                 paper_path: str) -> Dict[str, Any]:
    r1 = main.cmd_scaffold(in_dir, out_dir, work_dir, force=True)
    if r1.get("status") == "degraded":
        raise RuntimeError("scaffold 降级: %s" % r1.get("note"))
    work = Path(work_dir)
    (work / "findings.json").write_text(
        json.dumps(build_findings(paper_path), ensure_ascii=False), encoding="utf-8")
    (work / "claims.json").write_text(
        json.dumps(build_claims(paper_path), ensure_ascii=False), encoding="utf-8")
    (work / "coverage.json").write_text(
        json.dumps(SYNTHETIC_COVERAGE, ensure_ascii=False), encoding="utf-8")
    u = main.cmd_update(out_dir, work_dir, str(work / "findings.json"),
                        str(work / "claims.json"), None, str(work / "coverage.json"),
                        "合成自检总评：仅验证脚本确定性，非正式审查结论。", "partial",
                        None, None, None)
    if not u.get("ok"):
        raise RuntimeError("update 失败: %s" % u.get("errors"))
    return read_result(str(Path(out_dir) / "result.json"))


def main_run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="paper-review-selftest-"))
    try:
        in_dir = build_synthetic_input(tmp)
        paper_path = str(Path(in_dir) / "materials" / "PAPER.md")

        # ---- 1. 确定性：同输入同路径两次运行一致 ----
        # 确定性指：同一输入、同一输出/工作目录路径下运行两次，
        # result.json 内容一致（仅时间戳/耗时等易变字段除外）。
        # 使用同一 out/work 路径，故 artifacts 等绝对路径也保持一致。
        out_dir = str(tmp / "out")
        work_dir = str(tmp / "work")
        a = run_pipeline(in_dir, out_dir, work_dir, paper_path)
        b = run_pipeline(in_dir, out_dir, work_dir, paper_path)
        check("确定性: 两次 scaffold+update 产物一致",
              json.dumps(canonicalize(a), ensure_ascii=False, sort_keys=True)
              == json.dumps(canonicalize(b), ensure_ascii=False, sort_keys=True))

        # ---- 2. 契约完整：output_contract 键回填 ----
        check("契约: 回填 custom_metric", "custom_metric" in a,
              "actual_keys=%s" % sorted(a.keys()))
        check("契约: 回填 legacy_extra_key 且值不变",
              a.get("legacy_extra_key") == "must_be_echoed")
        check("契约: case_id 正确", a.get("case_id") == "selftest-synthetic-case")
        check("契约: 含 status/sources/uncertainty/change_log",
              all(k in a for k in ("status", "sources", "uncertainty", "change_log")))

        # ---- 3. 排序重编号稳定 ----
        fids = [f.get("id") for f in a["review"]["findings"]]
        check("排序: findings 重编号为 F001 起始", fids == ["F001"], "fids=%s" % fids)
        sc = main.cmd_selfcheck(out_dir)
        check("摘录: quote 能在语料中找到（selfcheck 通过）", sc.get("ok"),
              "errors=%s" % sc.get("errors"))

        # ---- 3b. 负例：错误定位应被 selfcheck 捕获 ----
        bad = run_pipeline(in_dir, str(tmp / "outBadLoc"), str(tmp / "workBadLoc"), paper_path)
        bad["review"]["findings"][0]["locator"] = "P9999"
        bad["review"]["findings"][0]["quote"] = "no such sentence in the corpus"
        (Path(tmp / "outBadLoc" / "result.json")).write_text(
            json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        sc_bad = main.cmd_selfcheck(str(tmp / "outBadLoc"))
        check("自检: 错误定位/摘录被 selfcheck 捕获", not sc_bad.get("ok"),
              "errors=%s" % sc_bad.get("errors"))

        # ---- 4. 降级健壮性 ----
        empty_in = tmp / "empty_in"
        empty_in.mkdir()
        main.cmd_scaffold(str(empty_in), str(tmp / "outEmpty"), str(tmp / "workEmpty"), force=True)
        e = read_result(str(tmp / "outEmpty" / "result.json"))
        check("降级: 空输入返回 insufficient_evidence",
              e.get("status") == "insufficient_evidence")
        check("降级: 空输入记录 degradations", bool(e.get("degradations")))

        bad_in = tmp / "bad_in"
        (bad_in / "materials").mkdir(parents=True)
        (bad_in / "case.json").write_text("{not valid json", encoding="utf-8")
        (bad_in / "materials" / "note.txt").write_text("valid text content here", encoding="utf-8")
        main.cmd_scaffold(str(bad_in), str(tmp / "outBad"), str(tmp / "workBad"), force=True)
        eb = read_result(str(tmp / "outBad" / "result.json"))
        check("降级: 坏 JSON 不崩溃且记录 errors", bool(eb.get("errors")))
        check("降级: 坏 JSON 下材料仍被抽取", bool(eb.get("sources")))

        enc_in = tmp / "enc_in"
        (enc_in / "materials").mkdir(parents=True)
        (enc_in / "case.json").write_text(json.dumps({"id": "enc-case"}), encoding="utf-8")
        (enc_in / "materials" / "bad.bin").write_bytes(b"\xff\xfe\x00garbage\x9c")
        main.cmd_scaffold(str(enc_in), str(tmp / "outEnc"), str(tmp / "workEnc"), force=True)
        ee = read_result(str(tmp / "outEnc" / "result.json"))
        check("降级: 坏编码材料不崩溃", "status" in ee and "errors" in ee)

    except Exception as exc:  # noqa: BLE001
        check("整体: 无未捕获异常", False, repr(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    print("=" * 60)
    for r in RESULTS:
        print("[%s] %s%s" % ("PASS" if r["ok"] else "FAIL", r["name"],
                             (" :: " + r["detail"]) if r["detail"] else ""))
    print("-" * 60)
    print("共 %d 项，通过 %d，失败 %d" % (total, passed, total - passed))
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main_run())
