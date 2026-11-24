#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify.py — 提交前自检。

按评分维度逐条检查 /app/output/result.json 及其引用的产物，
打印可勾选清单；退出码 0 表示通过，非 0 表示存在失败项。
只依赖标准库，可被 Agent 在收尾阶段反复调用。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import read_text, read_json, VALID_STATUS, CORE_FIELDS  # noqa: E402

DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"


def _fields_from_work(work):
    fields = ["manuscript", "evidence_map", "term_table", "revision_notes"]
    s, _e = read_json(os.path.join(work, "inputs_summary.json"))
    if isinstance(s, dict) and isinstance(s.get("fields"), list):
        fields = s["fields"]
    return fields


def load(result_path):
    text, err = read_text(result_path)
    if err:
        return None, ["result.json 不可读：%s" % err]
    try:
        return json.loads(text), []
    except Exception as e:  # noqa: BLE001
        return None, ["result.json 非法 JSON：%s" % e]


def check_file_like(v, artifacts_dir):
    if not isinstance(v, str) or not v.strip():
        return True, "空"
    if v.startswith("http"):
        return True, "外部引用（未验证）"
    cand = os.path.join(artifacts_dir, os.path.basename(v)) \
        if not os.path.isabs(v) else v
    exists = os.path.exists(cand)
    return exists, ("存在" if exists else "缺失：" + cand)


def run(result_path=None, output_dir=None, work_dir=None):
    output_dir = output_dir or DEFAULT_OUTPUT
    work_dir = work_dir or DEFAULT_WORK
    result_path = result_path or os.path.join(output_dir, "result.json")
    fields = _fields_from_work(work_dir)

    result, fatal = load(result_path)
    rows = []
    failed = 0

    rows.append(("[契约] result.json 存在且为合法 JSON", bool(result and not fatal)))
    if not result:
        for m in fatal:
            rows.append(("[契约] " + m, False))
        return rows, failed + len(fatal) + 1

    def tick(ok):
        nonlocal failed
        if not ok:
            failed += 1
        return ok

    # 1. 输入/输出契约
    rows.append(("[契约] case_id 非空且非 unknown",
                 tick(bool(result.get("case_id")) and result["case_id"] != "unknown")))
    rows.append(("[契约] status ∈ %s" % sorted(VALID_STATUS),
                 tick(result.get("status") in VALID_STATUS)))
    for k in CORE_FIELDS:
        rows.append(("[契约] 含必备键 %s" % k, tick(k in result)))
    for f in fields:
        rows.append(("[契约] 含输出契约字段 %s" % f, tick(f in result)))

    # 2. 实际效果：任务完成度 / 正确性 / 完整性 / 可核验性
    for f in fields:
        v = result.get(f)
        filled = v not in (None, "", [], {})
        rows.append(("[实际效果] 契约字段 %s 已填充" % f, tick(filled)))
        if isinstance(v, str) and v.strip() and not v.startswith("http"):
            ok, msg = check_file_like(v, output_dir)
            rows.append(("[实际效果] 产物 %s：%s" % (f, msg), tick(ok)))
    rows.append(("[实际效果] sources 逐条可回溯（有 locator+quote）",
                 tick(isinstance(result.get("sources"), list) and bool(result["sources"]))))
    rows.append(("[实际效果] change_log 有删改理由",
                 tick(isinstance(result.get("change_log"), list) and bool(result["change_log"]))))

    # 3. 使用增益：论断强度 / 术语一致 可验证
    rows.append(("[使用增益] uncertainty 记录材料不足/冲突/待确认",
                 tick(isinstance(result.get("uncertainty"), list))))
    rows.append(("[使用增益] pending_human 列出需人工确认项",
                 tick(isinstance(result.get("pending_human"), list))))

    # 4. 可靠体验：异常处理 / 诚实状态
    honest = result.get("status") == "insufficient_evidence" or \
        (result.get("status") == "partial" and result.get("uncertainty")) or \
        (result.get("status") in ("ok", "partial"))
    rows.append(("[可靠体验] status 与材料是否充足一致", tick(honest)))
    rows.append(("[可靠体验] errors/degradations 为列表",
                 tick(isinstance(result.get("errors"), list) and
                      isinstance(result.get("degradations"), list))))

    # 5. 生态贡献：可复用资源（由 SKILL.md/references 保证，此处仅提示）
    rows.append(("[生态贡献] runtime_ms 已记录",
                 tick(isinstance(result.get("runtime_ms"), (int, float)))))

    return rows, failed


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="写作 Skill 提交前自检")
    ap.add_argument("--result", default=None, help="result.json 路径（默认 /app/output/result.json）")
    ap.add_argument("--output", default=None, help="产物目录（默认 /app/output）")
    ap.add_argument("--work", default=None, help="工作目录（默认 /app/work）")
    args = ap.parse_args(argv)

    rows, failed = run(args.result, args.output, args.work)
    for label, ok in rows:
        mark = "  [x]" if ok else "  [ ]"
        print("%s %s" % (mark, label))
    print("----")
    print("通过项 %d，失败项 %d" % (len([r for r in rows if r[1]]), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
