#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py — 结果自检与内置鲁棒性回归（仅标准库）。

两种模式：
  校验真实运行产物：
    python -B scripts/selftest.py --output /app/output --case /input/case.json
  内置鲁棒性回归（空输入 / 坏 JSON / 缺参数 / 超大文件 / 坏编码）：
    python -B scripts/selftest.py --unit

退出码：0 表示全部硬性检查通过；1 表示存在失败项。自检不编造任何评测分数。
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

import core_impl as C
import main as M

ARTIFACT_FILES = [
    "review.md",
    "screening_log.json",
    "evidence_table.jsonl",
    "themes.md",
    "conflicts_and_gaps.md",
]

EVIDENCE_REQUIRED_KEYS = ["evidence_id", "source_id", "locator", "quote", "claim", "strength"]
STRENGTH_LEVELS = {"established", "suggestive", "disputed", "not_reported"}


# ---------------------------------------------------------------------------
# 生产产物校验
# ---------------------------------------------------------------------------

class Checks:
    def __init__(self):
        self.items = []
        self.failed = 0

    def add(self, name, ok, detail=""):
        self.items.append((name, ok, detail))
        if not ok:
            self.failed += 1


def read_text_quiet(path):
    text, _enc, _err = C.read_text(path)
    return text or ""


def load_json_quiet(path):
    obj, _errs = C.read_json(path)
    return obj


def contract_fields_from_case(case_path):
    """从 case.json 读 output_contract 并归一化；读不到返回 None。"""
    if not case_path or not os.path.isfile(case_path):
        return None
    case, errs = C.read_json(case_path)
    if not isinstance(case, dict):
        return None
    contract = C.find_key_recursive(
        case, ("output_contract", "outputcontract", "output_schema", "outputschema"))
    fields = C.normalize_contract(contract)
    return fields


def check_result(output_dir, case_path):
    c = Checks()
    result_path = os.path.join(output_dir, "result.json")

    result = load_json_quiet(result_path)
    if not isinstance(result, dict):
        c.add("result.json 存在且为合法 JSON", False, "缺失或不可解析: " + result_path)
        return c

    c.add("result.json 存在且为合法 JSON", True)

    case_id = result.get("case_id")
    c.add("含非空 case_id", bool(isinstance(case_id, str) and case_id.strip()),
          repr(case_id))

    status = result.get("status")
    c.add("status 取值合法", status in C.ALLOWED_STATUS, repr(status))

    for key in ("sources", "uncertainty", "change_log"):
        c.add("含诚实字段 " + key, key in result)

    if isinstance(result.get("sources"), list):
        bad_src = [s for s in result["sources"]
                   if not isinstance(s, dict) or not s.get("source_id")]
        c.add("sources 每项含 source_id", not bad_src,
              "{} 项缺 source_id".format(len(bad_src)))
    else:
        c.add("sources 为列表", False, repr(type(result.get("sources"))))

    if isinstance(result.get("change_log"), list):
        c.add("change_log 非空", len(result["change_log"]) > 0)
    else:
        c.add("change_log 为列表", False)

    # 契约字段完整性
    fields = contract_fields_from_case(case_path)
    if fields is None:
        c.add("case.json 契约字段可解析（否则跳过完整性检查）", False, "case 不可读或无契约")
    else:
        missing = [name for name, _d in fields if name not in result]
        c.add("output_contract 全部字段已回填", not missing,
              "缺失: " + ", ".join(missing) if missing else "")

    # 主产物存在性
    missing_art = [f for f in ARTIFACT_FILES if not os.path.isfile(os.path.join(output_dir, f))]
    c.add("主产物齐全", not missing_art, "缺失: " + ", ".join(missing_art) if missing_art else "")

    # 证据表合法性
    ev_path = os.path.join(output_dir, "evidence_table.jsonl")
    evidence_ids = set()
    ev_bad = 0
    ev_text = read_text_quiet(ev_path)
    if os.path.isfile(ev_path) and ev_text:
        for ln in ev_text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except Exception:
                ev_bad += 1
                continue
            if isinstance(row, dict):
                evidence_ids.add(row.get("evidence_id"))
                missing_keys = [k for k in EVIDENCE_REQUIRED_KEYS if k not in row]
                if missing_keys:
                    ev_bad += 1
                elif row.get("strength") not in STRENGTH_LEVELS:
                    ev_bad += 1
    c.add("evidence_table.jsonl 每行合法且字段齐全", ev_bad == 0,
          "{} 行非法".format(ev_bad) if ev_bad else "")

    # 综述回链：正文 [S#] 必须指向证据行
    review = read_text_quiet(os.path.join(output_dir, "review.md"))
    cited = set(re.findall(r"\[(S\d+)\]", review))
    unresolved = sorted(cited - evidence_ids)
    c.add("综述 [S#] 标记均可回链到证据表", not unresolved,
          "未解析: " + ", ".join(unresolved) if unresolved else "")

    # 骨架残留检查（仅在声称 ok 时作为硬性要求）
    skeleton_files = [f for f in ARTIFACT_FILES
                      if C.SKELETON_MARKER in read_text_quiet(os.path.join(output_dir, f))]
    if status == "ok":
        c.add("status=ok 时无骨架占位残留", not skeleton_files,
              "残留: " + ", ".join(skeleton_files) if skeleton_files else "")
        c.add("status=ok 时证据表非空", len(evidence_ids) > 0)
        c.add("status=ok 时综述含回链标记", bool(cited))
    elif skeleton_files:
        c.add("partial 状态如实保留骨架（允许）", True,
              "仍为骨架: " + ", ".join(skeleton_files))

    return c


# ---------------------------------------------------------------------------
# 内置鲁棒性回归
# ---------------------------------------------------------------------------

class UnitChecks:
    def __init__(self):
        self.items = []
        self.failed = 0

    def add(self, name, ok, detail=""):
        self.items.append((name, ok, detail))
        if not ok:
            self.failed += 1


def run_unit():
    u = UnitChecks()
    base = tempfile.mkdtemp(prefix="lrs_selftest_")
    try:
        # 场景1：空输入目录 -> insufficient_evidence，退出码 0
        d1 = os.path.join(base, "empty")
        os.makedirs(d1)
        o1, w1 = d1 + "_out", d1 + "_work"
        rc = M.main(["--input", d1, "--output", o1, "--work", w1])
        r1 = load_json_quiet(os.path.join(o1, "result.json"))
        u.add("空输入: 退出码 0", rc == 0, "rc={}".format(rc))
        u.add("空输入: status=insufficient_evidence",
              isinstance(r1, dict) and r1.get("status") == "insufficient_evidence",
              repr(r1.get("status") if isinstance(r1, dict) else None))

        # 场景2：坏 JSON 且无材料 -> input_error，退出码 0，result 仍合法
        d2 = os.path.join(base, "badjson")
        os.makedirs(d2)
        with open(os.path.join(d2, "case.json"), "wb") as f:
            f.write(b"{ this is not valid json \xff")
        o2, w2 = d2 + "_out", d2 + "_work"
        rc2 = M.main(["--input", d2, "--output", o2, "--work", w2])
        r2 = load_json_quiet(os.path.join(o2, "result.json"))
        u.add("坏 JSON: 退出码 0", rc2 == 0, "rc={}".format(rc2))
        u.add("坏 JSON: status=input_error",
              isinstance(r2, dict) and r2.get("status") == "input_error",
              repr(r2.get("status") if isinstance(r2, dict) else None))
        u.add("坏 JSON: result 仍为合法 JSON", isinstance(r2, dict))

        # 场景3：坏 JSON 但有材料 -> partial，材料被盘点且文本被抽取
        d3 = os.path.join(base, "badjson_mat")
        os.makedirs(d3)
        with open(os.path.join(d3, "case.json"), "wb") as f:
            f.write(b"not json at all")
        with open(os.path.join(d3, "paper1.txt"), "w", encoding="utf-8") as f:
            f.write("Title: A real study\nResult: measured value X.\n")
        o3, w3 = d3 + "_out", d3 + "_work"
        rc3 = M.main(["--input", d3, "--output", o3, "--work", w3])
        r3 = load_json_quiet(os.path.join(o3, "result.json"))
        inv3 = load_json_quiet(os.path.join(w3, "inventory.json"))
        u.add("坏 JSON+材料: 退出码 0", rc3 == 0, "rc={}".format(rc3))
        u.add("坏 JSON+材料: status=partial",
              isinstance(r3, dict) and r3.get("status") == "partial",
              repr(r3.get("status") if isinstance(r3, dict) else None))
        u.add("坏 JSON+材料: 材料被盘点",
              isinstance(inv3, dict) and len(inv3.get("materials", [])) == 1)
        ext3 = (inv3 or {}).get("materials", [{}])[0].get("extracted_text")
        u.add("坏 JSON+材料: 文本已抽取", bool(ext3), repr(ext3))

        # 场景4：缺参数 -> argparse 默认值正确
        p = M.build_parser()
        a = p.parse_args([])
        u.add("缺参数: 默认 --input/--output/--work 正确",
              a.input == "/input" and a.output == "/app/output" and a.work == "/app/work",
              "{0}/{1}/{2}".format(a.input, a.output, a.work))

        # 场景5：超大文件 -> 记录 oversized 且不崩溃、不读正文
        d5 = os.path.join(base, "big")
        os.makedirs(d5)
        big = os.path.join(d5, "big.txt")
        with open(big, "wb") as f:
            f.write(b"a" * 3000)
        inv5, _dups5, _deg5 = C.scan_materials([big], os.path.join(d5, "work"), 2000)
        u.add("超大文件: 标记 oversized 且跳过正文",
              len(inv5) == 1 and inv5[0].get("oversized") is True
              and inv5[0].get("readable") is False,
              repr(inv5[0] if inv5 else None))

        # 场景6：坏编码 -> read_text 不抛异常且仍返回文本（latin-1 兜底）
        bad = os.path.join(d5, "bad.txt")
        with open(bad, "wb") as f:
            f.write(b"\xff\xfe\x00\x80 \x81 hello")
        text, _enc, _err = C.read_text(bad)
        u.add("坏编码: read_text 不崩溃且返回文本", text is not None)

        # 场景7：重复运行确定性（骨架层：除时间戳外内容一致）
        o7a, o7b = d1 + "_det_a", d1 + "_det_b"
        M.main(["--input", d1, "--output", o7a, "--work", o7a + "_w"])
        M.main(["--input", d1, "--output", o7b, "--work", o7b + "_w"])
        ra = load_json_quiet(os.path.join(o7a, "result.json"))
        rb = load_json_quiet(os.path.join(o7b, "result.json"))
        def strip_time(o):
            o = dict(o or {})
            o.pop("runtime", None)
            o.pop("change_log", None)
            return json.dumps(o, ensure_ascii=False, sort_keys=True)
        u.add("重复运行: 骨架结果确定一致", strip_time(ra) == strip_time(rb))

    except Exception as e:  # noqa: BLE001
        u.add("内置回归未抛未捕获异常", False, repr(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return u


def report(label, checks):
    print("== {0} ==".format(label))
    for name, ok, detail in checks.items:
        mark = "PASS" if ok else "FAIL"
        line = "[{0}] {1}".format(mark, name)
        if detail:
            line += "  --  " + str(detail)
        print(line)
    print("总计: {0} 项通过 / {1} 项失败".format(
        len(checks.items) - checks.failed, checks.failed))
    return checks.failed


def main(argv=None):
    ap = argparse.ArgumentParser(description="文献综述 Skill 自检")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--unit", action="store_true", help="运行内置鲁棒性回归")
    args = ap.parse_args(argv)

    if args.unit:
        return 1 if report("内置鲁棒性回归", run_unit()) else 0
    return 1 if report("生产产物自检", check_result(args.output, args.case)) else 0


if __name__ == "__main__":
    sys.exit(main())
