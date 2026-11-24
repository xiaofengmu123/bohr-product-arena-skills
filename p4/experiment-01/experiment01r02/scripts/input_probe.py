#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""input_probe.py — 发现并规范化运行时输入。

扫描 /input，读 case.json（utf-8-sig 容错）与 task.md，用 pdftotext 抽取
PDF 正文，发现 output_contract、候选方案与资源/价格/排期合同。
写 /app/work/input_probe.json。绝不编造：缺口以 missing/not_reported 记录。
"""
import argparse
import json
import os
import subprocess
import sys
import time

MAX_FILE_BYTES = 2 * 1024 * 1024
TEXT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv"}
PDF_EXTS = {".pdf"}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, OSError):
            continue
    return None


def read_json(path):
    txt = read_text(path)
    if txt is None:
        return None, "unreadable"
    try:
        return json.loads(txt), "ok"
    except json.JSONDecodeError as exc:
        return None, "bad_json:%s" % exc


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def extract_pdf(pdf_path, work_dir):
    out_dir = os.path.join(work_dir, "pdf_text")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_path = os.path.join(out_dir, base + ".txt")
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", pdf_path, out_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        if r.returncode != 0 or not os.path.exists(out_path):
            return None, "pdftotext_rc=%d" % r.returncode
        txt = read_text(out_path) or ""
        if not txt.strip():
            return out_path, "empty_text"
        return out_path, "ok"
    except FileNotFoundError:
        return None, "pdftotext_not_installed"
    except Exception as exc:  # noqa: BLE001
        return None, "pdftotext_error:%s" % exc


def classify_files(input_dir):
    """Walk /input and classify files; skip oversized files with a note."""
    files = []
    degradations = []
    for root, _dirs, names in os.walk(input_dir):
        for name in sorted(names):
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            rel = os.path.relpath(p, input_dir)
            if size > MAX_FILE_BYTES:
                degradations.append(
                    {"file": rel, "reason": "oversized>%dMB" % (MAX_FILE_BYTES // (1024 * 1024))})
                continue
            ext = os.path.splitext(name)[1].lower()
            files.append({"path": p, "rel": rel, "ext": ext, "size": size})
    return files, degradations


def contract_from(case_obj):
    """Find output_contract in case.json (top-level or nested)."""
    if isinstance(case_obj, dict):
        if isinstance(case_obj.get("output_contract"), dict):
            return case_obj.get("output_contract")
        if isinstance(case_obj.get("outputContract"), dict):
            return case_obj.get("outputContract")
        for v in case_obj.values():
            if isinstance(v, dict) and "output_contract" in v and isinstance(v["output_contract"], dict):
                return v["output_contract"]
    return None


def case_id_from(case_obj, task_text):
    if isinstance(case_obj, dict):
        for key in ("case_id", "challenge_id", "id"):
            val = case_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if task_text:
        for line in task_text.splitlines():
            if "challenge_id" in line and "`" in line:
                seg = line.split("`")[1].strip()
                if seg:
                    return seg
    return None


def extract_candidate_plans(case_obj, files, input_dir):
    """Discover 3 versioned candidate plans from case.json or files."""
    plans = []
    if isinstance(case_obj, dict):
        for key in ("candidate_plans", "plans", "plan_versions", "versions"):
            val = case_obj.get(key)
            if isinstance(val, list):
                plans = val
                break
            if isinstance(val, dict):
                plans = list(val.values())
                break
    if not plans:
        for f in files:
            base = os.path.basename(f["rel"]).lower()
            if ("plan" in base or "candidate" in base or "version" in base) and f["ext"] in TEXT_EXTS:
                txt = read_text(f["path"])
                if txt:
                    plans.append({"locator": "input/" + f["rel"], "raw": txt[:4000]})
    return plans[:3]


def extract_contracts(case_obj, files, input_dir):
    """Discover resource/price/booking contracts."""
    contracts = []
    if isinstance(case_obj, dict):
        for key in ("resource_contract", "price_contract", "booking_contract",
                    "contracts", "resource_contracts"):
            val = case_obj.get(key)
            if isinstance(val, (dict, list)):
                contracts.append({"key": key, "data": val})
    for f in files:
        base = os.path.basename(f["rel"]).lower()
        if ("contract" in base or "price" in base or "booking" in base or "resource" in base) \
                and f["ext"] in TEXT_EXTS:
            txt = read_text(f["path"])
            if txt:
                contracts.append({"locator": "input/" + f["rel"], "raw": txt[:4000]})
    return contracts


def main():
    ap = argparse.ArgumentParser(description="Discover runtime input.")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")  # 与另两个脚本统一，本脚本不使用
    ap.add_argument("--work", default="/app/work")
    args = ap.parse_args()

    report = {
        "probe_ts": now_iso(),
        "input_dir": args.input,
        "files": [],
        "case_json": None,
        "case_json_status": "missing",
        "task_md": None,
        "output_contract": None,
        "case_id": None,
        "pdf_text": {},
        "candidate_plans": [],
        "contracts": [],
        "errors": [],
        "degradations": [],
    }

    if not os.path.isdir(args.input):
        report["errors"].append("input_dir_not_found:%s" % args.input)
    else:
        files, degradations = classify_files(args.input)
        report["files"] = [{"rel": f["rel"], "ext": f["ext"], "size": f["size"]} for f in files]
        report["degradations"].extend(degradations)

        case_path = os.path.join(args.input, "case.json")
        if os.path.isfile(case_path):
            case_obj, status = read_json(case_path)
            report["case_json_status"] = status
            if case_obj is not None:
                report["case_json"] = case_obj
                report["output_contract"] = contract_from(case_obj)
        else:
            report["errors"].append("case.json_missing")

        task_path = os.path.join(args.input, "task.md")
        if os.path.isfile(task_path):
            txt = read_text(task_path)
            report["task_md"] = txt[:8000] if txt else None
        else:
            report["errors"].append("task.md_missing")

        report["case_id"] = case_id_from(
            report["case_json"], report["task_md"])

        for f in files:
            if f["ext"] in PDF_EXTS:
                out_path, status = extract_pdf(f["path"], args.work)
                report["pdf_text"][f["rel"]] = {"status": status, "out_path": out_path}
                if status != "ok":
                    report["degradations"].append(
                        {"file": f["rel"], "reason": status})

        report["candidate_plans"] = extract_candidate_plans(
            report["case_json"], files, args.input)
        report["contracts"] = extract_contracts(
            report["case_json"], files, args.input)

    os.makedirs(args.work, exist_ok=True)
    out_path = os.path.join(args.work, "input_probe.json")
    write_json_atomic(out_path, report)
    print("input_probe: wrote %s" % out_path)
    print("input_probe: files=%d case_id=%s contract=%s plans=%d contracts=%d errors=%d"
          % (len(report["files"]), report["case_id"],
             bool(report["output_contract"]),
             len(report["candidate_plans"]), len(report["contracts"]),
             len(report["errors"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
