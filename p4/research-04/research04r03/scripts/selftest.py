#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py —— 阶段 P4/P5：契约自检 + 确定性核验。

三种模式：
  --check       校验 /app/output/result.json 的结构、枚举、四类产物、quote 可定位、
                无编造分数；写 /app/work/selfcheck.json。退出码：0=全部 PASS/WARN，1=有 FAIL。
  --determinism 复跑 prepare.py+analyze.py 到临时目录，比对 mechanical.sha256 与输入指纹，
                证明“同输入两次运行机械产物一致”。
  --report      把已生成的 selfcheck.json 渲染成 Markdown 清单（便于提交前逐条勾选）。

只用标准库；所有检查确定性、可重复。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

PIPELINE_VERSION = "1.0.0"
STATUS_ENUM = ("ok", "partial", "insufficient_evidence", "input_error")
GAP_TYPES = ("open_question", "method_failure", "unresolved_conflict", "coverage_absence")
REP_KINDS = ("origin", "method", "milestone", "benchmark")
EV_STATUS = ("reported", "not_reported", "source_conflict", "inferred")
EV_CONFIDENCE = ("high", "medium", "low")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": "))


def read_text(path: str):
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                return fh.read(), enc
        except (UnicodeDecodeError, LookupError, OSError):
            continue
    return None, None


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def load_result(out_dir):
    path = os.path.join(out_dir, "result.json")
    if not os.path.exists(path):
        return None, "result.json 不存在"
    text, _ = read_text(path)
    if text is None:
        return None, "result.json 不可读"
    try:
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, "result.json 非合法 JSON：%s" % exc


def iter_numeric_score_keys(obj, prefix=""):
    """递归找出键名含 score/benchmark/白板 且值为数字的路径（用于无编造分数检查）。"""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = "%s.%s" % (prefix, k) if prefix else k
            if re.search(r"score|benchmark|白板|金标准评分", str(k), re.IGNORECASE):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    found.append((path, v))
            found.extend(iter_numeric_score_keys(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(iter_numeric_score_keys(v, "%s[%d]" % (prefix, i)))
    return found


def check_quote_locatable(input_root, ev):
    """逐字 quote 可定位：读原文，先精确匹配、再空白归一匹配。"""
    src = ev.get("source_file") or ""
    quote = ev.get("quote") or ""
    if not src or not quote:
        return False, "缺 source_file 或 quote"
    full = os.path.join(input_root, src.replace("/", os.sep))
    if not os.path.isfile(full):
        return False, "源文件不可读：%s" % src
    text, _ = read_text(full)
    if text is None:
        return False, "源文件编码不可读：%s" % src
    if quote in text:
        return True, ""
    if norm_ws(quote) in norm_ws(text):
        return True, "（空白归一后匹配）"
    return False, "quote 未在 %s 中逐字定位" % src


def run_checks(result, err, args):
    checks = []

    def add(cid, name, status, msg):
        checks.append({"id": cid, "name": name, "status": status, "message": msg})

    # C01 JSON 合法
    if err:
        add("C01", "result.json 为合法 JSON 且可加载", "FAIL", err)
    else:
        add("C01", "result.json 为合法 JSON 且可加载", "PASS", "已加载 result.json")

    if result is None:
        add("C02", "case_id 存在", "SKIP", "无 result.json，无法继续")
        return checks, result

    # C02 case_id
    if isinstance(result.get("case_id"), str) and result["case_id"].strip():
        add("C02", "case_id 存在且非空", "PASS", "case_id=%s" % result["case_id"])
    else:
        add("C02", "case_id 存在且非空", "FAIL", "case_id 缺失或为空")

    # C03 output_contract 回填
    contract = None
    case_path = os.path.join(args.input, "case.json")
    if os.path.isfile(case_path):
        case_text, _ = read_text(case_path)
        if case_text:
            try:
                case = json.loads(case_text)
                contract = case.get("output_contract")
            except Exception:  # noqa: BLE001
                contract = None
    if isinstance(contract, dict) and contract:
        missing = [k for k in contract if k not in result]
        if missing:
            add("C03", "output_contract 全部字段原样回填", "FAIL",
                "缺失契约字段：%s" % ", ".join(missing))
        else:
            add("C03", "output_contract 全部字段原样回填", "PASS",
                "回填 %d 个契约字段" % len(contract))
    else:
        add("C03", "output_contract 全部字段原样回填", "SKIP", "case.json 无 output_contract")

    # C04 status 枚举
    status = result.get("status")
    if status in STATUS_ENUM:
        add("C04", "status 取值合法", "PASS", "status=%s" % status)
    else:
        add("C04", "status 取值合法", "FAIL", "status=%r 不在 %s" % (status, STATUS_ENUM))

    # C05 四类产物
    four = [
        ("time_trend", result.get("time_trend"), "distribution"),
        ("topic_evolution", result.get("topic_evolution"), "stage_divisions/term_mapping/events"),
        ("representative_works", result.get("representative_works"), "criteria/stages"),
        ("research_gaps", result.get("research_gaps"), "gaps/excluded_not_gaps"),
    ]
    missing = [n for n, v, _ in four if not isinstance(v, dict)]
    add("C05", "四类可检查产物字段齐备", "PASS" if not missing else "FAIL",
        "缺失：%s" % ", ".join(missing) if missing else "四类产物字段均在")

    # C06 年度分布
    tt = result.get("time_trend", {})
    dist = tt.get("distribution", []) if isinstance(tt, dict) else []
    unit_note = tt.get("unit_note") if isinstance(tt, dict) else None
    if isinstance(dist, list) and dist:
        add("C06", "time_trend.distribution 有年度计数", "PASS",
            "%d 个年份切片" % len(dist))
    elif unit_note:
        add("C06", "time_trend.distribution 有年度计数", "PASS",
            "distribution 为空但 unit_note 已说明原因：%s" % unit_note[:80])
    else:
        add("C06", "time_trend.distribution 有年度计数", "FAIL",
            "distribution 为空且 unit_note 未说明")

    # C07 年份口径
    proto = result.get("protocol", {})
    ddn = proto.get("date_definition_note") if isinstance(proto, dict) else None
    if ddn:
        add("C07", "protocol.date_definition_note 写明年份口径", "PASS", str(ddn)[:80])
    else:
        add("C07", "protocol.date_definition_note 写明年份口径", "FAIL",
            "date_definition_note 为空（须写明在线发表/见刊/预印本口径）")

    # C08 代表作口径非仅被引
    rw = result.get("representative_works", {})
    criteria = rw.get("criteria", []) if isinstance(rw, dict) else []
    crit_text = " ".join(str(c) for c in criteria).lower()
    has_kind = any(k in crit_text for k in ("origin", "method", "milestone", "benchmark",
                                            "首现", "方法", "里程碑", "基准"))
    if criteria and has_kind:
        add("C08", "代表作 criteria 覆盖多口径（非仅被引）", "PASS",
            "%d 条口径" % len(criteria))
    else:
        add("C08", "代表作 criteria 覆盖多口径（非仅被引）", "FAIL",
            "criteria 缺失或未见 kind 口径")

    # C09 空白排除证据
    gaps = result.get("research_gaps", {}).get("gaps", []) if isinstance(
        result.get("research_gaps"), dict) else []
    bad = []
    for g in gaps:
        if not isinstance(g, dict) or not (g.get("exclusion_evidence") or "").strip():
            bad.append(g.get("id", "?"))
    if gaps and not bad:
        add("C09", "每个空白有 exclusion_evidence", "PASS", "%d 个空白均已写排除证据" % len(gaps))
    elif not gaps:
        add("C09", "每个空白有 exclusion_evidence", "WARN", "research_gaps.gaps 为空，需确认是否材料不足")
    else:
        add("C09", "每个空白有 exclusion_evidence", "FAIL", "缺排除证据：%s" % ", ".join(bad))

    # C10 quote 可定位
    evs = result.get("evidence_map", []) if isinstance(result.get("evidence_map"), list) else []
    unloc = []
    checked = 0
    for ev in evs:
        if not isinstance(ev, dict):
            continue
        if not (ev.get("quote") or "").strip():
            continue
        checked += 1
        ok, msg = check_quote_locatable(args.input, ev)
        if not ok:
            unloc.append("%s(%s)" % (ev.get("id", "?"), msg))
    if checked == 0:
        add("C10", "evidence_map 每条非空 quote 可逐字定位", "WARN",
            "无带 quote 的证据条目（可能材料不足）")
    elif not unloc:
        add("C10", "evidence_map 每条非空 quote 可逐字定位", "PASS",
            "%d 条 quote 全部可定位" % checked)
    else:
        add("C10", "evidence_map 每条非空 quote 可逐字定位", "FAIL",
            "%d 条未定位：%s" % (len(unloc), "; ".join(unloc[:5])))

    # C11 冲突记录
    conflicts = result.get("conflicts", []) if isinstance(result.get("conflicts"), list) else []
    if conflicts:
        okc = all(isinstance(c, dict) and c.get("items") and "resolution" in c for c in conflicts)
        add("C11", "冲突证据均记录且未静默择一", "PASS" if okc else "FAIL",
            "%d 条冲突记录" % len(conflicts))
    else:
        add("C11", "冲突证据均记录且未静默择一", "PASS", "无冲突（conflicts 为空）")

    # C12 change_log
    cl = result.get("change_log", []) if isinstance(result.get("change_log"), list) else []
    if cl:
        add("C12", "change_log 非空且记录增量", "PASS", "%d 条变更记录" % len(cl))
    else:
        add("C12", "change_log 非空且记录增量", "FAIL", "change_log 为空")

    # C13 无编造分数（启发式，人工确认；可能误报 output_contract 合法回填的分数键）
    score_keys = iter_numeric_score_keys(result)
    if score_keys:
        add("C13", "无编造 benchmark score / 白板分数", "WARN",
            "发现疑似数字型分数键（若为 output_contract 原样回填则忽略）：%s" %
            ", ".join("%s=%s" % (k, v) for k, v in score_keys[:5]))
    else:
        add("C13", "无编造 benchmark score / 白板分数", "PASS", "未发现数字型 score/benchmark 键")

    # C14 status 与四类产物一致
    if status == "ok":
        ok4 = not missing and dist and gaps
        add("C14", "status 与四类产物一致", "PASS" if ok4 else "FAIL",
            "status=ok 但四类产物/年度分布/空白不完整" if not ok4 else "status=ok 且产物齐备")
    elif status in ("partial", "insufficient_evidence", "input_error"):
        add("C14", "status 与四类产物一致", "PASS", "status=%s 如实反映未完成度" % status)
    else:
        add("C14", "status 与四类产物一致", "SKIP", "status 不合法，见 C04")

    # C15 确定性侧车
    mech_sha = os.path.join(args.work, "determinism", "mechanical.sha256")
    fp = result.get("input_fingerprint")
    if os.path.isfile(mech_sha) and fp:
        add("C15", "确定性侧车（mechanical.sha256 + input_fingerprint）存在", "PASS",
            "input_fingerprint=%s..." % fp[:12])
    else:
        add("C15", "确定性侧车（mechanical.sha256 + input_fingerprint）存在", "FAIL",
            "缺 mechanical.sha256 或 input_fingerprint（请先运行 analyze.py）")

    return checks, result


def run_determinism(args):
    """复跑 prepare+analyze 到临时目录，比对 mechanical.sha256。"""
    out = {"status": "PASS", "message": "", "details": []}
    mech_sha = os.path.join(args.work, "determinism", "mechanical.sha256")
    if not os.path.isfile(mech_sha):
        out["status"] = "FAIL"
        out["message"] = "缺少 mechanical.sha256，无法比对（请先运行 analyze.py）"
        return out
    with open(mech_sha, "r", encoding="utf-8") as fh:
        ref_sha = fh.read().strip()

    tmp = tempfile.mkdtemp(prefix="selftest_determinism_")
    tmp_out = os.path.join(tmp, "output")
    tmp_work = os.path.join(tmp, "work")
    try:
        for script, label in (("prepare.py", "prepare"), ("analyze.py", "analyze")):
            sp = os.path.join(SCRIPT_DIR, script)
            proc = subprocess.run(
                [sys.executable, sp, "--input", args.input,
                 "--output", tmp_out, "--work", tmp_work],
                capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                out["status"] = "FAIL"
                out["message"] = "%s 复跑失败（退出码 %d）：%s" % (
                    label, proc.returncode, proc.stderr.strip()[:300])
                return out
        new_sha_path = os.path.join(tmp_work, "determinism", "mechanical.sha256")
        with open(new_sha_path, "r", encoding="utf-8") as fh:
            new_sha = fh.read().strip()
        out["details"].append({"reference_sha256": ref_sha, "rerun_sha256": new_sha})
        if ref_sha == new_sha:
            out["message"] = "机械产物 SHA-256 一致：同输入两次运行结果确定"
        else:
            out["status"] = "FAIL"
            out["message"] = "机械产物 SHA-256 不一致（确定性被破坏）"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "FAIL"
        out["message"] = "确定性复跑异常：%s" % exc
    return out


def render_markdown(checks, det=None):
    lines = ["# Skill 自检清单（selftest.py 生成）\n",
             "| ID | 检查项 | 结果 | 说明 |", "|-|-|-|-|"]
    for c in checks:
        lines.append("| %s | %s | %s | %s |" % (c["id"], c["name"], c["status"],
                                                 (c["message"] or "").replace("|", "/")))
    if det:
        lines.append("\n## 确定性核验\n")
        lines.append("- 结果：**%s**" % det["status"])
        lines.append("- %s" % det["message"])
        for d in det.get("details", []):
            lines.append("- %s" % json.dumps(d, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="自检与确定性核验")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--check", action="store_true", help="校验 result.json（默认行为，显式写出仅为可读性）")
    ap.add_argument("--determinism", action="store_true", help="复跑机械流水线并比对 SHA-256")
    ap.add_argument("--report", action="store_true", help="把已有 selfcheck.json 渲染为 Markdown")
    args = ap.parse_args(argv)

    if args.report:
        sp = os.path.join(args.work, "selfcheck.json")
        if not os.path.isfile(sp):
            print("[selftest] 无 selfcheck.json，先运行 --check", file=sys.stderr)
            return 2
        with open(sp, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        print(render_markdown(data.get("checks", []), data.get("determinism")))
        return 0

    result, err = load_result(args.output)
    checks, result = run_checks(result, err, args)

    det = None
    if args.determinism:
        det = run_determinism(args)

    has_fail = any(c["status"] == "FAIL" for c in checks) or (det and det["status"] == "FAIL")
    report = {
        "pipeline_version": PIPELINE_VERSION,
        "case_id": (result or {}).get("case_id"),
        "status": (result or {}).get("status"),
        "checks": checks,
        "determinism": det,
        "summary": "FAIL" if has_fail else "PASS",
    }
    os.makedirs(args.work, exist_ok=True)
    with open(os.path.join(args.work, "selfcheck.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical_json(report))
        fh.write("\n")

    print(render_markdown(checks, det))
    n_fail = sum(1 for c in checks if c["status"] == "FAIL")
    n_warn = sum(1 for c in checks if c["status"] == "WARN")
    print("[selftest] summary=%s fail=%d warn=%d determinism=%s" % (
        report["summary"], n_fail, n_warn, det["status"] if det else "not-run"))
    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
