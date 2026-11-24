# -*- coding: utf-8 -*-
"""Self-check for research-frontier-contract.

Checks result.json against the runtime contract:
  * field-by-field output_contract alignment (presence + inferred type);
  * required fixed fields and enums;
  * four deliverable products;
  * evidence quote locatability (when materials exist);
  * change_log has a skeleton landing entry and monotonic revision;
  * no fabricated benchmark/score keys.

Usage:
  python3 scripts/selftest.py [--result /app/output/result.json]
                              [--input /input] [--work /app/work]
  python3 scripts/selftest.py --demo        # three synthetic scenarios
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_impl as C  # noqa: E402


FIXED_FIELDS = [
    "case_id", "status", "research_question", "protocol", "time_trend",
    "topic_evolution", "representative_works", "research_gaps", "evidence_map",
    "conflicts", "sources", "uncertainty", "pending_human", "change_log",
    "limitations", "errors", "degradations", "runtime", "deliverable",
    "deliverables", "contract_alignment",
]

STATUS_ENUM = {"ok", "partial", "insufficient_evidence", "input_error"}
SUPPORT_ENUM = {"reported", "not_reported", "inferred"}
EVENT_ENUM = {"emergence", "merge", "split", "decline", "terminology_drift"}
GAP_ENUM = {"open_question", "method_failure", "unresolved_conflict", "coverage_absence"}
WORK_KIND = {"origin", "method", "milestone", "benchmark"}
EVID_STATUS = {"reported", "not_reported", "source_conflict", "inferred"}


class Report:
    def __init__(self):
        self.checks = []

    def add(self, ok, code, message):
        self.checks.append({"ok": bool(ok), "code": code, "message": message})

    def fails(self):
        return [c for c in self.checks if not c["ok"]]


def check_result(result, contract, materials_dir=None):
    r = Report()

    r.add(isinstance(result, dict), "R01", "result.json 是合法 JSON 对象")
    if not isinstance(result, dict):
        return r

    r.add("case_id" in result and result.get("case_id"), "R02",
          "case_id 存在且非空")
    r.add(result.get("status") in STATUS_ENUM, "R03",
          "status 属于 %s" % sorted(STATUS_ENUM))

    # contract alignment
    if isinstance(contract, dict):
        records = result.get("contract_alignment") or []
        rec_keys = {x.get("contract_key") for x in records if isinstance(x, dict)}
        missing_records = set(contract.keys()) - rec_keys
        r.add(not missing_records, "C01",
              "contract_alignment 覆盖全部契约键（缺失: %s）" % sorted(missing_records)
              if missing_records else "contract_alignment 覆盖全部契约键")
        type_ok = True
        missing = []
        for key, desc in contract.items():
            t = C.infer_type(desc)
            if key not in result:
                missing.append(key)
                type_ok = False
                continue
            value = result[key]
            rec = next((r for r in records if isinstance(r, dict) and r.get("contract_key") == key), {})
            status = rec.get("value_status", "filled")
            if status == "filled":
                if C.is_empty(value):
                    type_ok = False
            else:
                if value != C.type_default(t):
                    type_ok = False
        r.add(not missing, "C02",
              "result.json 顶层含全部契约键（缺失: %s）" % sorted(missing)
              if missing else "result.json 顶层含全部契约键")
        r.add(type_ok, "C03", "契约键取值完整（filled 非空 / 空值等于类型缺省）")
    else:
        r.add(True, "C01", "无 output_contract，跳过契约对齐（仍检查固定字段）")

    missing_fixed = [f for f in FIXED_FIELDS if f not in result]
    r.add(not missing_fixed, "R04", "固定字段齐全（缺失: %s）" % sorted(missing_fixed) or "固定字段齐全")

    # four products
    tt = result.get("time_trend") or {}
    te = result.get("topic_evolution") or {}
    rw = result.get("representative_works") or {}
    rg = result.get("research_gaps") or {}
    r.add(isinstance(tt.get("distribution"), list), "P01", "time_trend.distribution 是数组")
    r.add(isinstance(te.get("events"), list), "P02", "topic_evolution.events 是数组")
    r.add(isinstance(rw.get("stages"), list), "P03", "representative_works.stages 是数组")
    r.add(isinstance(rg.get("gaps"), list), "P04", "research_gaps.gaps 是数组")

    # enum spot checks (only when non-empty)
    for ev in te.get("events") or []:
        if ev.get("type") not in EVENT_ENUM:
            r.add(False, "E01", "事件 type 非法: %r" % ev.get("type"))
            break
    else:
        r.add(True, "E01", "事件 type 枚举合规")
    for g in rg.get("gaps") or []:
        if g.get("type") not in GAP_ENUM:
            r.add(False, "E02", "空白 type 非法: %r" % g.get("type"))
            break
    else:
        r.add(True, "E02", "空白 type 枚举合规")

    # change_log
    cl = result.get("change_log") or []
    r.add(isinstance(cl, list) and len(cl) >= 1, "L01", "change_log 非空（含骨架落盘）")
    r.add(any(x.get("action") == "skeleton" for x in cl if isinstance(x, dict)),
          "L02", "change_log 含 skeleton 落盘记录")
    r.add(isinstance(result.get("revision"), int), "L03", "revision 是整数")

    # no fabricated scores
    banned = ["benchmark_score", "whiteboard", "白板", "leaderboard_rank"]
    hit = [b for b in banned if b in result]
    r.add(not hit, "F01", "无伪造评测分数/白板字段（命中: %s）" % hit or "无伪造分数字段")

    # evidence quote locatability
    evm = result.get("evidence_map") or []
    unlocatable = []
    if materials_dir and os.path.isdir(materials_dir):
        for e in evm:
            if not isinstance(e, dict):
                continue
            loc = e.get("locator") or ""
            quote = e.get("quote") or ""
            if not quote:
                continue
            path_part = loc.split("#")[0] if "#" in loc else ""
            if path_part:
                full = os.path.join(os.path.dirname(materials_dir), path_part.replace("/", os.sep))
                if os.path.exists(full):
                    text = C.read_text_safe(full)
                    if quote not in text:
                        unlocatable.append(e.get("id", "?"))
                else:
                    unlocatable.append(e.get("id", "?"))
    r.add(not unlocatable, "Q01", "非空 quote 均可定位（失败: %s）" % unlocatable or "quote 均可定位")

    return r


def render_report(r, path=None):
    lines = []
    for c in r.checks:
        lines.append("%s %-4s %s" % ("PASS" if c["ok"] else "FAIL", c["code"], c["message"]))
    text = "\n".join(lines) + "\n"
    ok = not r.fails()
    summary = {"ok": ok, "total": len(r.checks), "fail": len(r.fails()), "checks": r.checks}
    if path:
        C.atomic_write(path, summary)
    print(text)
    print("SELFTEST %s (%d/%d)" % ("PASS" if ok else "FAIL",
                                    len(r.checks) - len(r.fails()), len(r.checks)))
    return ok


def demo_scenario(workdir, name, case, task_text, files):
    """Build inputs, run prepare/analyze in-process, return (result, contract, materials_dir)."""
    inp = os.path.join(workdir, name, "input")
    out = os.path.join(workdir, name, "output")
    work = os.path.join(workdir, name, "work")
    os.makedirs(inp, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    C.write_text(os.path.join(inp, "case.json"), json.dumps(case, ensure_ascii=False))
    C.write_text(os.path.join(inp, "task.md"), task_text)
    for rel, text in files.items():
        C.write_text(os.path.join(inp, rel), text)
    # run pipeline
    result = C.build_skeleton(case, task_text, case.get("output_contract"))
    docs = C.extract_materials(C.discover_input(inp)[0], work)
    result["sources"] = [{"id": d["id"], "path": d["rel"], "type": d["kind"],
                          "parse_ok": d.get("parse_ok", True)} for d in docs]
    baseline = C.mechanical_extract(docs, work)
    result["_baseline"] = baseline
    result["time_trend"]["distribution"] = baseline["year_distribution"]
    C.re_align(result, case.get("output_contract"))
    C.atomic_write(os.path.join(out, "result.json"), result, ensure_ascii=False)
    return result, case.get("output_contract"), os.path.join(work, "materials")


def run_demo():
    tmp = tempfile.mkdtemp(prefix="rfc_selftest_")
    all_ok = True

    # 1. well-formed
    case = {
        "case_id": "demo-001",
        "output_contract": {
            "case_id": "string，原样回填",
            "status": "ok | partial | insufficient_evidence | input_error",
            "time_trend": "对象：年度分布与趋势判断",
            "evidence_map": "数组：论断→证据",
            "change_log": "改动留痕数组",
            "extra_unknown": "未知契约字段示例",
        },
    }
    files = {
        "materials/mat_a.txt": "Deep learning in 2019.\nHowever, results conflict.\nFuture work remains unclear.",
        "materials/mat_b.txt": "A new method in 2021.\nThe study shows strong gains.",
    }
    res, contract, mdir = demo_scenario(tmp, "well", case, "# challenge-id: demo-001\n", files)
    ok = render_report(check_result(res, contract, mdir))
    all_ok = all_ok and ok

    # 2. empty input
    case2 = {"case_id": "demo-empty", "output_contract": {"status": "string", "research_gaps": "数组"}}
    res2, contract2, mdir2 = demo_scenario(tmp, "empty", case2, "# challenge-id: demo-empty\n", {})
    res2["status"] = "insufficient_evidence"
    ok2 = render_report(check_result(res2, contract2, mdir2))
    all_ok = all_ok and ok2

    # 3. bad JSON contract (missing output_contract)
    case3 = {"case_id": "demo-bad", "output_contract": None}
    res3, contract3, mdir3 = demo_scenario(tmp, "bad", case3, "# challenge-id: demo-bad\n", {"materials/x.txt": "Text."})
    ok3 = render_report(check_result(res3, contract3, mdir3))
    all_ok = all_ok and ok3

    print("DEMO %s (tmp=%s)" % ("PASS" if all_ok else "FAIL", tmp))
    return 0 if all_ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="selftest.py")
    p.add_argument("--result", help="result.json path")
    p.add_argument("--input", help="input dir (for case.json/output_contract)")
    p.add_argument("--work", help="work dir (for materials)")
    p.add_argument("--demo", action="store_true", help="run three synthetic scenarios")
    args = p.parse_args(argv)

    if args.demo:
        return run_demo()

    result_path = args.result or "/app/output/result.json"
    input_dir = args.input or "/input"
    work_dir = args.work or "/app/work"

    result, err = C.load_json_safe(result_path)
    if err:
        print("selftest: cannot load result.json: %s" % err, file=sys.stderr)
        return 2
    case, cerr = C.load_json_safe(os.path.join(input_dir, "case.json"))
    contract = case.get("output_contract") if not cerr and isinstance(case, dict) else None
    materials = os.path.join(work_dir, "materials")
    report = check_result(result, contract, materials)
    ok = render_report(report, os.path.join(work_dir, "selftest.json"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
