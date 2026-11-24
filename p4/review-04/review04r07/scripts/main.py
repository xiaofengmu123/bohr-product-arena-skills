# -*- coding: utf-8 -*-
"""main.py — 论文逻辑与论证审查 Skill 的入口脚本（纯标准库）。

三种模式：
  scaffold  发现输入、抽取文本、切分语料，先落盘一版合法 partial 结果骨架
  update    把 Agent 产出的 findings/claims 合并进 result.json（自动回验逐字 quote）
  selfcheck 校验 result.json 与 report.md 的合法性，退出码 0=通过 / 1=有问题

调用示例（路径相对 SKILL.md 所在目录，cwd 任意均可）：
  python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py update   --output /app/output --work /app/work \\
      --findings /app/work/findings.json --claims /app/work/claims.json --status ok
  python3 -B scripts/main.py selfcheck --output /app/output

任何未捕获异常都会触发 _emergency_result：写最小合法 result.json + report.md，
进程退出码 0，不让 Harness 因半截产物而崩溃。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import review_core as rc  # noqa: E402


def _argparse() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="论文逻辑与论证审查 Skill 脚本")
    sub = p.add_subparsers(dest="mode", required=True)

    def add_common(sp):
        sp.add_argument("--input", default=rc.DEFAULT_INPUT, help="输入目录")
        sp.add_argument("--output", default=rc.DEFAULT_OUTPUT, help="最终产物目录")
        sp.add_argument("--work", default=rc.DEFAULT_WORK, help="中间工作目录")

    s1 = sub.add_parser("scaffold", help="抽取语料并落盘结果骨架")
    add_common(s1)
    s1.add_argument("--force", action="store_true", help="保留参数（scaffold 幂等）")

    s2 = sub.add_parser("update", help="合并 findings/claims 增量更新")
    s2.add_argument("--output", default=rc.DEFAULT_OUTPUT)
    s2.add_argument("--work", default=rc.DEFAULT_WORK)
    s2.add_argument("--findings", default=None, help="findings JSON 数组文件")
    s2.add_argument("--claims", default=None, help="claims JSON 数组文件")
    s2.add_argument("--summary", default=None, help="总评文本，或以 @path 读文件")
    s2.add_argument("--status", default=None, choices=rc.RESULT_STATUSES)

    s3 = sub.add_parser("selfcheck", help="自检产物合法性")
    s3.add_argument("--output", default=rc.DEFAULT_OUTPUT)
    return p


def _emergency_result(args) -> None:
    out = Path(getattr(args, "output", rc.DEFAULT_OUTPUT))
    try:
        out.mkdir(parents=True, exist_ok=True)
        minimal = {
            "case_id": "emergency",
            "status": "input_error",
            "task": {"challenge_id": None, "task_title": None},
            "artifacts": {
                "result_json": str(out / "result.json"),
                "report_md": str(out / "report.md"),
            },
            "review": {
                "summary": "",
                "findings": [], "claims": [], "commitments": [],
                "coverage": rc._default_coverage(),
                "section_map": [],
            },
            "sources": [], "uncertainty": [], "pending_review": [],
            "pending_human": [], "limitations": [],
            "errors": ["unhandled_exception_during_run"],
            "degradations": [],
            "change_log": [{"ts": rc._utcnow(), "event": "emergency",
                            "note": "minimal fallback result"}],
            "runtime": {"seconds_elapsed": 0.0, "model_requests_used": 0,
                        "budget": {"requests": 64, "seconds": 900}},
        }
        rc.write_json_atomic(minimal, str(out / "result.json"))
        rc.write_text_atomic(
            "# 审查报告\n\n运行异常，已产出最小合法结果；详见 result.json.errors。\n",
            str(out / "report.md"))
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    p = _argparse()
    args = p.parse_args(argv)
    try:
        if args.mode == "scaffold":
            r = rc.scaffold(args.input, args.output, args.work, force=args.force)
            n_records = 0
            try:
                cj = Path(args.work) / "corpus.json"
                if cj.exists():
                    with open(cj, encoding="utf-8") as f:
                        n_records = len(json.load(f).get("records", []))
            except Exception:  # noqa: BLE001
                n_records = -1
            print("scaffold done: status=%s sources=%d records=%d"
                  % (r["status"], len(r["sources"]), n_records))
            return 0
        if args.mode == "update":
            r = rc.update(output_dir=args.output, work_dir=args.work,
                          findings_file=args.findings, claims_file=args.claims,
                          summary=args.summary, status=args.status)
            nf = len((r.get("review") or {}).get("findings") or [])
            print("update done: status=%s findings=%d" % (r.get("status"), nf))
            return 0
        if args.mode == "selfcheck":
            return rc.selfcheck(args.output)
    except Exception as e:  # noqa: BLE001
        print("ERROR: %s" % e, file=sys.stderr)
        _emergency_result(args)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
