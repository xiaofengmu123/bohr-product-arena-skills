#!/usr/bin/env python3
"""validate.py — 执行流程第 5 步：校验 result.json 契约与产物路径。

输出 /app/work/validation_report.json；所有关键检查通过退出码 0，
否则退出码 1（供 SKILL 按报告修正后重跑）。只依赖标准库。
"""
import argparse
import json
import os

ALLOWED_STATUS = ("ok", "partial", "insufficient_evidence", "input_error")

REQUIRED_TOP_LEVEL = ("case_id", "status", "artifacts", "sources", "uncertainty",
                      "change_log", "errors", "degradations", "limitations",
                      "pending_human", "runtime")


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="校验 result.json 契约")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)

    result_path = os.path.join(args.output, "result.json")
    report = {"checks": [], "passed": 0, "failed": 0, "critical_ok": True}
    result = load_json(result_path)

    def add(name, ok, detail, critical=True):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail,
                                 "critical": critical})
        if ok:
            report["passed"] += 1
        else:
            report["failed"] += 1
            if critical:
                report["critical_ok"] = False

    if result is None:
        add("result_exists", False, "result.json 不存在或不是合法 JSON")
    else:
        add("result_exists", True, "result.json 可解析")

        for field in REQUIRED_TOP_LEVEL:
            add("field_%s" % field, field in result,
                "字段 %r %s" % (field, "存在" if field in result else "缺失"))

        status = result.get("status")
        add("status_valid", status in ALLOWED_STATUS,
            "status=%r（允许值 %s）" % (status, "/".join(ALLOWED_STATUS)))

        # output_contract 字段回填检查（与 state.json 的 contract_keys 对照）
        state = load_json(os.path.join(args.work, "state.json")) or {}
        contract_keys = state.get("contract_keys")
        if contract_keys:
            missing = [k for k in contract_keys if k not in result]
            add("contract_fields_filled", not missing,
                "output_contract 缺失字段: %s" % (missing or "无"))

        # 列表型标准字段类型检查
        for field in ("artifacts", "sources", "uncertainty", "change_log",
                      "errors", "degradations", "limitations", "pending_human"):
            add("field_list_%s" % field, isinstance(result.get(field), list),
                "字段 %r 应为列表" % field)

        # change_log 非空（记录 骨架→初稿→改进→收口）
        add("change_log_nonempty", bool(result.get("change_log")),
            "change_log 应有记录")

        # status 判定：ok 需有产物与证据；partial/insufficient_evidence 允许缺证据但须有缺口标注
        is_ok = status == "ok"

        # artifacts 路径存在且相对 /app/output/
        artifacts = result.get("artifacts") or []
        missing_artifacts = []
        for a in artifacts:
            if not isinstance(a, str) or a.startswith(("/", "..")) or "\\" in a:
                missing_artifacts.append("非法路径 %r" % a)
            elif not os.path.exists(os.path.join(args.output, a)):
                missing_artifacts.append("产物缺失 %r" % a)
        add("artifacts_exist", not missing_artifacts,
            "artifacts 检查: %s" % (missing_artifacts or "全部存在"))
        if is_ok:
            add("artifacts_nonempty_for_ok", bool(artifacts),
                "status=ok 时 artifacts 不得为空", critical=True)

        # evidence_map 逐项 locator+quote 非空
        em = result.get("evidence_map")
        if em is None:
            em_path = os.path.join(args.output, "evidence_map.json")
            em = load_json(em_path)
        if em is None:
            add("evidence_map_present", False, "evidence_map.json 不存在或不可解析",
                critical=is_ok)
        else:
            bad = []
            items = em.get("items", em) if isinstance(em, dict) else em
            if isinstance(items, dict):
                items = list(items.values())
            for it in (items or []):
                if not isinstance(it, dict) or not it.get("locator") or not it.get("quote"):
                    bad.append(it)
            add("evidence_map_ok", not bad,
                "evidence_map 逐项 locator+quote 非空；异常 %d 条" % len(bad),
                critical=is_ok)

        # ok 状态必须无 pending_human / limitations 静默缺口：至少写明边界
        if is_ok:
            add("ok_no_silent_gap",
                "MATERIAL GAP" not in str(result.get("manuscript", "")) or
                bool(result.get("pending_human")) or bool(result.get("limitations")),
                "status=ok 时缺口须已标注（pending_human/limitations）或正文无 MATERIAL GAP",
                critical=False)

    with open(os.path.join(args.work, "validation_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["critical_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
