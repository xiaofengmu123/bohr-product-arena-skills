#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 /app/work/plan.json：schema 完整性 + 可行域/硬约束越界检查。

exit code: 0=通过，1=阻断错误，2=仅警告。
用法: python scripts/validate_plan.py [plan.json 路径] [--report 报告路径]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import validate_plan_file, write_json  # noqa: E402

DEFAULT_PLAN = "/app/work/plan.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="校验 plan.json 契约与可行域")
    parser.add_argument("plan", nargs="?", default=DEFAULT_PLAN)
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)
    report = validate_plan_file(args.plan)
    if args.report:
        write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
