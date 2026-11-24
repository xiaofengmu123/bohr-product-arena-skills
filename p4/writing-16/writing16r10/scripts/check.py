#!/usr/bin/env python3
"""result.json 与产物自检（仅标准库，独立于 main.py 可复核）。

用法：
  python3 -B scripts/check.py --output /app/output
退出码：0=通过；1=缺 result.json 或 JSON 坏；2=存在结构化问题。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REQUIRED_FIELDS = ["case_id", "status", "deliverable", "evidence_map",
                   "terminology", "change_log", "sources", "uncertainty",
                   "errors", "degradations"]
VALID_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}


def validate(data, out_dir):
    problems = []
    for k in REQUIRED_FIELDS:
        if k not in data:
            problems.append("missing field: %s" % k)
    if data.get("status") not in VALID_STATUS:
        problems.append("invalid status: %r" % data.get("status"))
    d = data.get("deliverable")
    if d and not (Path(out_dir) / str(d)).exists():
        problems.append("deliverable missing: %s" % d)
    ev = data.get("evidence_map", []) or []
    for i, item in enumerate(ev):
        if not isinstance(item, dict) or "locator" not in item or "quote" not in item:
            problems.append("evidence_map[%d] missing locator/quote" % i)
    return problems


def main():
    ap = argparse.ArgumentParser(description="result.json 自检")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args()
    rp = Path(args.output) / "result.json"
    if not rp.exists():
        print(json.dumps({"ok": False, "problems": ["result.json missing"]},
                         ensure_ascii=False))
        sys.exit(1)
    try:
        data = json.loads(rp.read_text(encoding="utf-8-sig"))
    except Exception as e:
        print(json.dumps({"ok": False, "problems": ["bad json: %s" % e]},
                         ensure_ascii=False))
        sys.exit(1)
    problems = validate(data, args.output)
    print(json.dumps({"ok": not problems, "problems": problems,
                      "status": data.get("status")}, ensure_ascii=False))
    sys.exit(0 if not problems else 2)


if __name__ == "__main__":
    main()
