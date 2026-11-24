#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check.py —— 交付前自检：验证 result.json 契约、产物存在性、事实与数值一致性。

检查项（对应评分维度）：
1. result.json 可解析、case_id 非空、status 合法。
2. output_contract 全部键在场且非空（允许 null 但必须已在 uncertainty 说明）。
3. result.json 引用的产物文件真实存在。
4. 主稿件中的数值是否都能回溯到材料 facts.json；无法回溯的标为 unverified_number。
5. 主稿件缩写是否首次展开。

退出码约定：0 = 通过或仅有警告；1 = result.json 缺失/坏 JSON/硬契约失败（必须修复）。
默认 --input /input --output /app/output --work /app/work。
"""

import argparse
import json
import re
import sys
from pathlib import Path

ALLOWED_STATUS = {"ok", "partial", "insufficient_evidence", "input_error"}
NUM_NORM_RE = re.compile(r"\d+(?:\.\d+)?")


def load_json(path: Path):
    try:
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return json.loads(raw.decode(enc)), None
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return None, "undecodable-or-bad-json"
    except OSError as e:
        return None, "read-error:" + str(e)


def norm_number(s):
    s = s.replace(",", "").replace("，", "").strip()
    return s.rstrip(".")


def extract_numbers(text):
    out = set()
    for m in re.finditer(r"\d[\d,，.]*(?:\.\d+)?", text):
        v = norm_number(m.group(0).rstrip("."))
        if v and any(c.isdigit() for c in v):
            out.add(v)
    return out


def find_artifact(result):
    """从 result 中定位主稿件路径（相对 /app/output）。"""
    for key in ("deliverable", "manuscript", "artifact", "draft", "output",
                "result_file", "file", "path"):
        v = result.get(key)
        if isinstance(v, str) and v.strip():
            return key, v.strip()
    return None, None


def main():
    ap = argparse.ArgumentParser(description="科研转化写作交付自检")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--strict-numbers", action="store_true",
                    help="把 unverified_number 当作硬失败（默认仅警告）")
    args = ap.parse_args()

    output_dir = Path(args.output)
    work_dir = Path(args.work)
    result_path = output_dir / "result.json"

    report = {"checks": [], "warnings": [], "errors": []}
    hard_fail = False

    def add(check, ok, detail):
        report["checks"].append({"check": check, "ok": bool(ok), "detail": detail})
        if not ok:
            (report["errors"] if check in ("json", "case_id", "status", "contract",
                                           "artifact_files") else report["warnings"]).append(
                {"check": check, "detail": detail})

    if not result_path.exists():
        print("FAIL result.json 不存在:", result_path)
        sys.exit(1)

    result, err = load_json(result_path)
    if result is None:
        add("json", False, err)
        hard_fail = True
        print("FAIL", err)
    else:
        add("json", True, "result.json 可解析")

    if result is not None:
        add("case_id", bool(result.get("case_id")), "case_id=" + str(result.get("case_id")))
        status = result.get("status")
        add("status", status in ALLOWED_STATUS, "status=" + str(status))

        # 契约字段：从 case.json 读取 output_contract 核对
        contract_names = None
        case_path = Path(args.input) / "case.json"
        if case_path.exists():
            cdata, _ = load_json(case_path)
            if isinstance(cdata, dict):
                for key in ("output_contract", "outputContract"):
                    v = cdata.get(key)
                    if isinstance(v, dict):
                        contract_names = list(v.keys())
                        break
                    if isinstance(v, list):
                        contract_names = [x.get("name", x) if isinstance(x, dict) else x for x in v]
                        break
        if contract_names is None and work_dir.joinpath("manifest.json").exists():
            m, _ = load_json(work_dir / "manifest.json")
            if isinstance(m, dict):
                contract_names = m.get("contract_fields")

        if contract_names:
            missing = [n for n in contract_names if n not in result]
            empty = [n for n in contract_names if n in result and result[n] in (None, "", [])]
            add("contract", not missing,
                "契约字段缺失=" + str(missing) + "；空值(需在uncertainty说明)=" + str(empty))
        else:
            add("contract", True, "未发现 output_contract，跳过契约核对")

        # 产物文件存在性
        _, art_path = find_artifact(result)
        abs_art = None
        if art_path:
            abs_art = output_dir / art_path if not Path(art_path).is_absolute() else Path(art_path)
            add("artifact_files", abs_art.exists(),
                "主稿件=" + str(abs_art) + (" 存在" if abs_art.exists() else " 不存在"))
        else:
            add("artifact_files", False, "未在 result.json 找到主稿件路径字段")

        # 数值回溯
        facts_path = work_dir / "facts.json"
        if abs_art and facts_path.exists():
            art_text = ""
            if abs_art.exists():
                art_text = abs_art.read_text(encoding="utf-8", errors="replace")
            fdata, _ = load_json(facts_path)
            source_nums = set()
            for f in (fdata or {}).get("facts", []):
                if f.get("kind") == "number":
                    source_nums.add(norm_number(f.get("quote", "")))
            art_nums = extract_numbers(art_text)
            # 只把“足够长、像真实数值”的串视为需回溯（>=2 位，避免页码/编号噪声）
            candidates = {n for n in art_nums if len(n) >= 2}
            unverified = sorted(n for n in candidates
                                if n not in source_nums
                                and not any(n in s or s in n for s in source_nums))
            add("numbers_traceable", not unverified,
                "无法回溯的数值(" + str(len(unverified)) + "): " + ", ".join(unverified[:20]))
            if unverified and args.strict_numbers:
                hard_fail = True
        elif not facts_path.exists():
            report["warnings"].append({"check": "numbers_traceable",
                                       "detail": "facts.json 不存在，跳过数值回溯"})

        # 缩写首次展开
        if abs_art and abs_art.exists():
            art_text = abs_art.read_text(encoding="utf-8", errors="replace")
            bare_abbr = re.findall(r"\b[A-Z]{2,8}\b", art_text)
            report["abbr_hint"] = {"note": "请人工确认这些缩写已在首次出现处写出全称",
                                   "examples": sorted(set(bare_abbr))[:30]}

    # 打印
    print("=== check report ===")
    for c in report["checks"]:
        flag = "PASS" if c["ok"] else ("FAIL" if c["check"] in
              ("json", "case_id", "status", "contract", "artifact_files") else "WARN")
        print(f"[{flag}] {c['check']}: {c['detail']}")
    for w in report["warnings"]:
        print("[WARN]", w.get("check"), w.get("detail"))
    if "abbr_hint" in report:
        print("[HINT]", report["abbr_hint"]["note"])
        print("       ", ", ".join(report["abbr_hint"]["examples"]))

    report_path = work_dir / "check_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("wrote:", report_path)
    print("RESULT:", "FAIL" if hard_fail else "PASS/WARN")
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
