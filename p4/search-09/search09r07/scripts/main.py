#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""litsearch-audit 运行脚手架（薄入口，逻辑在 core_impl.py）。

职责：
  1. 解析命令行，缺省指向 /input、/app/output、/app/work（可经环境变量覆盖）；
  2. `skeleton` 立即落盘 partial 骨架（3 分钟内必须先执行）；
  3. `locators` 对抽取文本生成段落/句子级定位；
  4. `verify` 逐字核验引文；
  5. `grade` 阈值聚合分级；
  6. `validate` 校验最终 result.json；
  7. `self-test` 运行鲁棒性自检。

用法（在 SKILL.md 所在目录下以相对路径运行）：
  python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 -B scripts/main.py locators --text-file <txt> --source-id <id> --out <json>
  python3 -B scripts/main.py verify --locators <json> --locator <s#p1> --quote <"q">
  python3 -B scripts/main.py grade --assessments <json>
  python3 -B scripts/main.py validate --input /input --output /app/output
  python3 -B scripts/main.py self-test
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import core_impl as core  # noqa: E402


def _default(name, fallback):
    return os.environ.get(name, fallback)


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="litsearch-audit 运行脚手架")
    sub = p.add_subparsers(dest="cmd", required=True)

    sk = sub.add_parser("skeleton", help="立即落盘 partial 骨架")
    sk.add_argument("--input", default=_default("SKILL_INPUT_DIR", core.DEFAULT_INPUT))
    sk.add_argument("--output", default=_default("SKILL_OUTPUT_DIR", core.DEFAULT_OUTPUT))
    sk.add_argument("--work", default=_default("SKILL_WORK_DIR", core.DEFAULT_WORK))

    loc = sub.add_parser("locators", help="生成段落/句子级定位")
    loc.add_argument("--text-file", required=True)
    loc.add_argument("--source-id", required=True)
    loc.add_argument("--out", required=True)

    ver = sub.add_parser("verify", help="逐字核验引文")
    ver.add_argument("--locators", required=True)
    ver.add_argument("--locator", required=True)
    ver.add_argument("--quote", required=True)

    grd = sub.add_parser("grade", help="阈值聚合分级")
    grd.add_argument("--assessments", required=True)

    val = sub.add_parser("validate", help="校验 result.json")
    val.add_argument("--input", default=_default("SKILL_INPUT_DIR", core.DEFAULT_INPUT))
    val.add_argument("--output", default=_default("SKILL_OUTPUT_DIR", core.DEFAULT_OUTPUT))

    sub.add_parser("self-test", help="鲁棒性自检")
    return p


def cmd_skeleton(args):
    path, result, errors = core.run_skeleton(args.input, args.output, args.work)
    print("wrote skeleton:", path)
    print("status:", result.get("status"), "case_id:", result.get("case_id"))
    if errors:
        print("errors:", "; ".join(errors))
    return 0


def cmd_locators(args):
    text, errs = core.read_text(args.text_file)
    if text is None:
        print("read failed:", "; ".join(errs), file=sys.stderr)
        return 1
    records, lmap = core.make_locators(text, args.source_id)
    core.atomic_write_json(args.out, {"source_id": args.source_id, "records": records,
                                      "locator_map": lmap})
    print("locators:", len(records), "->", args.out)
    return 0


def cmd_verify(args):
    data, errs = core.load_json_lenient(args.locators)
    if data is None:
        print("load failed:", "; ".join(errs), file=sys.stderr)
        return 1
    lmap = data.get("locator_map", {})
    ok, detail = core.verify_quote(args.quote, args.locator, lmap)
    print("ok=%s detail=%s" % (ok, detail))
    return 0 if ok else 1


def cmd_grade(args):
    data, errs = core.load_json_lenient(args.assessments)
    if data is None:
        print("load failed:", "; ".join(errs), file=sys.stderr)
        return 1
    if isinstance(data, dict):
        data = data.get("assessment", data.get("assessments", []))
    out = core.grade(data)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args):
    rp = Path(args.output) / "result.json"
    data, errs = core.load_json_lenient(rp)
    if data is None:
        print("validate: cannot read result.json:", "; ".join(errs))
        return 1
    verr, vwarn = core.validate_result(data, args.input, args.output)
    print("errors:", verr or "none")
    print("warnings:", vwarn or "none")
    # error 级问题返回 1，便于 CI 检出；warning 不影响退出码
    return 1 if verr else 0


def cmd_selftest(args):
    passed, failed, details = core.run_self_test()
    for line in details:
        print(line)
    print("self-test: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "skeleton": cmd_skeleton,
        "locators": cmd_locators,
        "verify": cmd_verify,
        "grade": cmd_grade,
        "validate": cmd_validate,
        "self-test": cmd_selftest,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
