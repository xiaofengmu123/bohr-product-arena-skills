# -*- coding: utf-8 -*-
"""main.py — 论文逻辑与论证审查 Skill 入口（纯标准库，确定性）。

三种模式：
  scaffold  发现输入、抽取文本、切分语料，落盘 result.json 骨架 + report.md 骨架 + work/corpus.md
  update    把 Agent 产出的 findings/claims/commitments/coverage 合并进 result.json（排序、重编号、校验）
  selfcheck 校验 result.json 与 report.md 的合法性、摘录一致性

调用示例（路径相对 SKILL.md 所在目录，即 Skill 根目录）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update --output /app/output --work /app/work \
      --findings /app/work/findings.json --claims /app/work/claims.json --status partial
  python3 -B scripts/main.py selfcheck --output /app/output

确定性保证：update 会按 (source, 定位符, 严重度, 类型, 摘录) 的规范键重排并重编号，
同一批 findings/claims 无论以何顺序写入，产物完全一致；脚本无随机性。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import review_core as rc

# 固定输出编码，避免中文在非 UTF-8 控制台报错
for _stream in (sys.stdout, sys.stderr):
    try:
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="论文逻辑与论证审查 Skill 脚本")
    sub = p.add_subparsers(dest="mode", required=True)

    s1 = sub.add_parser("scaffold", help="抽取语料并落盘结果骨架")
    s1.add_argument("--input", default="/input")
    s1.add_argument("--output", default="/app/output")
    s1.add_argument("--work", default="/app/work")
    s1.add_argument("--force", action="store_true", help="覆盖已存在的 result.json")

    s2 = sub.add_parser("update", help="合并 findings/claims 增量更新")
    s2.add_argument("--output", default="/app/output")
    s2.add_argument("--work", default="/app/work")
    s2.add_argument("--findings", default=None)
    s2.add_argument("--claims", default=None)
    s2.add_argument("--commitments", default=None)
    s2.add_argument("--coverage", default=None)
    s2.add_argument("--summary", default=None, help="review.summary 文本，或以 @ 开头的文件路径")
    s2.add_argument("--status", default=None, choices=rc.RESULT_STATUSES)
    s2.add_argument("--pending-human", default=None)
    s2.add_argument("--limitations", default=None)
    s2.add_argument("--uncertainty", default=None)

    s3 = sub.add_parser("selfcheck", help="自检产物合法性")
    s3.add_argument("--output", default="/app/output")
    return p


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------
def _task_meta(discovered: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"challenge_id": None, "task_title": None}
    if discovered.get("case_json"):
        data, err = rc.read_json_tolerant(discovered["case_json"])
        meta["_case_parse_error"] = err
        meta["task_title"] = data.get("title") or data.get("task_title")
        meta["challenge_id"] = (data.get("challenge_id") or data.get("challengeId")
                                or data.get("id"))
    if discovered.get("task_md"):
        r = rc.read_text_safe(discovered["task_md"], max_bytes=200_000)
        for line in r["text"].splitlines()[:80]:
            ls = line.strip()
            if ls.startswith("#") and ls.lstrip("#").strip():
                meta.setdefault("task_title", ls.lstrip("#").strip())
                break
    return meta


def _case_id(discovered: Dict[str, Any]) -> str:
    if discovered.get("case_json"):
        data, _ = rc.read_json_tolerant(discovered["case_json"])
        for key in ("id", "case_id", "caseId", "caseid"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
        return Path(discovered["case_json"]).stem
    return "unknown_case"


def _contract(discovered: Dict[str, Any]) -> Dict[str, Any]:
    """若 case.json 给出 output_contract，原样回填其键；否则返回空 dict。"""
    if not discovered.get("case_json"):
        return {}
    data, _ = rc.read_json_tolerant(discovered["case_json"])
    if isinstance(data, dict):
        c = data.get("output_contract")
        if isinstance(c, dict):
            return dict(c)
    return {}


def cmd_scaffold(input_dir: str, output_dir: str, work_dir: str,
                 force: bool = False) -> Dict[str, Any]:
    """发现输入、抽取语料、落盘骨架；任何异常都保证产出合法 result.json。"""
    out = Path(output_dir)
    work = Path(work_dir)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    result_path = out / "result.json"
    if result_path.exists() and not force:
        return {"result_path": str(result_path), "status": "skipped",
                "note": "result.json 已存在，加 --force 覆盖"}

    discovered = rc.discover_inputs(input_dir)
    meta = _task_meta(discovered)
    case_id = _case_id(discovered)
    contract = _contract(discovered)

    sources: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    errors: List[str] = []
    degradations: List[str] = []

    # 材料抽取（PDF 用 pdftotext，纯文本直接读）
    for path in discovered["materials"]:
        ex = rc.extract_text(path)
        sources.append({"path": path, "kind": ex["kind"], "n_pages": ex["n_pages"],
                        "chars": ex["chars"], "error": ex["error"]})
        if ex["error"]:
            degradations.append("材料 %s 抽取失败: %s" % (path, ex["error"]))
            continue
        records.extend(rc.split_paragraphs(ex["pages"], path))

    # 材料本身为空也算降解
    if not discovered["materials"]:
        degradations.append("未发现任何材料文件（/input 下无 case.json 之外的材料）")

    if discovered.get("case_json"):
        _, err = rc.read_json_tolerant(discovered["case_json"])
        if err:
            errors.append("case.json 解析问题: %s" % err)

    corpus_path = rc.build_corpus_md(records, sources, str(work / "corpus.md"))

    # section_map：按来源、段落顺序的稳定映射
    section_map: List[Dict[str, Any]] = []
    for rec in sorted(records, key=lambda r: (r["src"], r["para"])):
        section_map.append({"section": rec["section"] or "无章节", "kind": rec["kind"],
                            "locator": rc.locator(rec), "source": rec["src"]})

    result = rc.make_skeleton(case_id, meta, contract)
    result["sources"] = sources
    result["errors"] = errors
    result["degradations"] = degradations
    result["review"]["section_map"] = section_map
    result["artifacts"] = [
        {"path": str(result_path), "kind": "result"},
        {"path": str(out / "report.md"), "kind": "report"},
        {"path": corpus_path, "kind": "corpus", "work": True},
    ]
    if not records:
        result["status"] = "insufficient_evidence"
        result["limitations"].append(
            "未抽取到任何可用文本段落，无法开展实质审查；请检查材料是否可读或有无文本层。")

    # 写 meta.json 供 update/selfcheck 复用语料索引
    meta_out = {"case_id": case_id, "input_dir": input_dir, "discovered": discovered,
                "n_records": len(records), "n_sources": len(sources)}
    rc.atomic_write_text(str(work / "meta.json"),
                         json.dumps(meta_out, ensure_ascii=False, indent=2))

    err = rc.atomic_write_text(str(result_path),
                               json.dumps(result, ensure_ascii=False, indent=2))
    if err:
        # 写盘失败：仍尽力输出最小合法结果
        minimal = json.dumps(rc.make_skeleton(case_id, meta, contract),
                             ensure_ascii=False, indent=2)
        out.write_text(minimal, encoding="utf-8")
        return {"result_path": str(result_path), "status": "degraded", "note": err}

    rc.atomic_write_text(str(out / "report.md"), rc.render_report(result))
    return {"result_path": str(result_path), "status": result["status"],
            "n_records": len(records), "n_sources": len(sources)}


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
def _load_list_or_empty(path: Optional[str]) -> List[Any]:
    if not path:
        return []
    data, err = rc.read_json_array(path)
    if err:
        return []
    return data


def _load_summary(arg: Optional[str]) -> Optional[str]:
    if not arg:
        return None
    if arg.startswith("@"):
        r = rc.read_text_safe(arg[1:], max_bytes=50_000)
        return r["text"].strip() or None
    return arg.strip() or None


def cmd_update(output_dir: str, work_dir: str, findings: Optional[str],
               claims: Optional[str], commitments: Optional[str],
               coverage: Optional[str], summary: Optional[str],
               status: Optional[str], pending_human: Optional[str],
               limitations: Optional[str], uncertainty: Optional[str]) -> Dict[str, Any]:
    """合并 Agent 产物；排序重编号；校验；重生成 report.md；记 change_log。"""
    out = Path(output_dir)
    result_path = out / "result.json"
    if not result_path.exists():
        return {"ok": False, "errors": ["result.json 不存在，先运行 scaffold"]}

    data, err = rc.read_json_tolerant(str(result_path))
    if err or not isinstance(data, dict):
        return {"ok": False, "errors": ["result.json 无法解析: %s" % err]}

    # 恢复语料段落索引（供 selfcheck 摘录一致性使用）
    records: List[Dict[str, Any]] = []
    meta_path = Path(work_dir) / "meta.json"
    if meta_path.exists():
        m, _ = rc.read_json_tolerant(str(meta_path))
        # meta 仅存统计信息，语料索引由 corpus.md 重建成本高；selfcheck 直接读 sources 重建
        _ = m

    review = data.setdefault("review", {})
    prev_findings = review.get("findings", [])

    if findings is not None:
        f_list = _load_list_or_empty(findings)
        review["findings"] = rc.renumber(
            sorted(f_list, key=rc.finding_sort_key), "F")
    if claims is not None:
        c_list = _load_list_or_empty(claims)
        review["claims"] = rc.renumber(
            sorted(c_list, key=rc.claim_sort_key), "C")
    if commitments is not None:
        m_list = _load_list_or_empty(commitments)
        review["commitments"] = rc.renumber(
            sorted(m_list, key=rc.commitment_sort_key), "M")
    if coverage is not None:
        cov, cerr = rc.read_json_tolerant(coverage)
        if not cerr and isinstance(cov, dict):
            merged = review.get("coverage", {})
            for dim in rc.COVERAGE_DIMS:
                if isinstance(cov.get(dim), dict):
                    merged[dim] = cov[dim]
            review["coverage"] = merged

    new_summary = _load_summary(summary)
    if new_summary is not None:
        review["summary"] = new_summary

    if pending_human is not None:
        data["pending_human"] = [str(x) for x in _load_list_or_empty(pending_human)]
    if limitations is not None:
        data["limitations"] = [str(x) for x in _load_list_or_empty(limitations)]
    if uncertainty is not None:
        data["uncertainty"] = [str(x) for x in _load_list_or_empty(uncertainty)]

    if status is not None:
        data["status"] = status

    n_f = len(review.get("findings", []))
    data.setdefault("change_log", []).append({
        "ts": rc.now_iso(),
        "event": "incremental_update",
        "note": "findings=%d（前次 %d）；status=%s" % (n_f, len(prev_findings), data.get("status")),
    })

    # 重生成 report.md
    rep_err = rc.atomic_write_text(str(out / "report.md"), rc.render_report(data))
    if rep_err:
        data["errors"] = data.get("errors", []) + [rep_err]

    werr = rc.atomic_write_text(str(result_path),
                                json.dumps(data, ensure_ascii=False, indent=2))
    if werr:
        return {"ok": False, "errors": [werr]}
    return {"ok": True, "status": data.get("status"), "n_findings": n_f}


# ---------------------------------------------------------------------------
# selfcheck
# ---------------------------------------------------------------------------
def cmd_selfcheck(output_dir: str) -> Dict[str, Any]:
    """校验 result.json + report.md；返回 {ok, errors, warnings}。"""
    out = Path(output_dir)
    result_path = out / "result.json"
    errors: List[str] = []
    warnings: List[str] = []

    if not result_path.exists():
        return {"ok": False, "errors": ["result.json 不存在"], "warnings": warnings}
    data, err = rc.read_json_tolerant(str(result_path))
    if err:
        return {"ok": False, "errors": ["result.json 无法解析: %s" % err],
                "warnings": warnings}
    if not isinstance(data, dict):
        return {"ok": False, "errors": ["result.json 顶层不是对象"], "warnings": warnings}

    # 重建段落索引：从 sources 抽取并切分
    records: List[Dict[str, Any]] = []
    for src in data.get("sources", []):
        p = src.get("path", "")
        if not p or not Path(p).exists():
            continue
        ex = rc.extract_text(p)
        records.extend(rc.split_paragraphs(ex["pages"], p))
    para_idx = rc.paragraph_index(records)

    errors.extend(rc.validate_result(data, records, para_idx))

    # report.md 与 result.json 同步：抽查关键字段是否出现
    report_path = out / "report.md"
    if report_path.exists():
        rep = rc.read_text_safe(str(report_path), max_bytes=5_000_000)["text"]
        if data.get("case_id", "") and data["case_id"] not in rep:
            warnings.append("report.md 未出现 case_id，可能与 result.json 不同步")
        if str(data.get("status", "")) and str(data["status"]) not in rep:
            warnings.append("report.md 未出现 status，可能与 result.json 不同步")
    else:
        warnings.append("report.md 不存在（scaffold/update 应生成）")

    ok = len(errors) == 0
    return {"ok": ok, "errors": errors, "warnings": warnings}


def main(argv: Optional[List[str]] = None) -> int:
    args = _argparse().parse_args(argv)
    t0 = time.time()
    if args.mode == "scaffold":
        r = cmd_scaffold(args.input, args.output, args.work, getattr(args, "force", False))
    elif args.mode == "update":
        r = cmd_update(args.output, args.work, args.findings, args.claims,
                       args.commitments, args.coverage, args.summary, args.status,
                       args.pending_human, args.limitations, args.uncertainty)
    elif args.mode == "selfcheck":
        r = cmd_selfcheck(args.output)
    else:
        print("未知模式", file=sys.stderr)
        return 2

    print(json.dumps({**r, "seconds": round(time.time() - t0, 3)},
                     ensure_ascii=False, indent=2))
    if args.mode == "selfcheck" and not r.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
