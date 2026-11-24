#!/usr/bin/env python3
"""selftest.py -- 确定性自检：校验 result.json 与产物，产出 selftest_report.json。

本报告的 pass/warn/fail 是 Skill 内部自检结果，不是官方评分，不得对外宣称为
评测分数。同一组输入文件重复运行，报告（除时间戳）内容完全一致；退出码
0=无错误（warning 允许），1=存在错误。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

DEFAULT_OUT = "/app/output/result.json"
DEFAULT_OUTPUT_DIR = "/app/output"
DEFAULT_REPORT = "/app/work/selftest_report.json"
FINAL_STATUS = {"success", "partial", "failed"}
TOP_KEYS = {"case_id", "status", "deliverables", "fact_source_map",
            "version_boundary", "sources", "uncertainty", "change_log"}
FACT_KINDS = {"number", "term", "symbol", "citation", "figure", "claim"}
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*(?:\s*%|‰)?")
TEXT_EXTS = {".md", ".txt", ".tex", ".rst", ".html", ".csv", ".json", ".xml", ".docx"}


def load_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None


def read_text(path):
    try:
        raw = open(path, "rb").read()
    except OSError:
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(checklist, cid, name, level, status, detail):
    checklist.append({"id": cid, "name": name, "level": level,
                      "status": status, "detail": detail})


def norm_number(s):
    """保守归一化：去千分位分隔与百分号，仅比较数字串。"""
    return s.replace(",", "").replace("%", "").replace("‰", "").strip()


def iter_text_files(root, exts):
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() in exts:
                out.append(os.path.join(dirpath, fn))
    return out


def check_glossary(glossary_path, output_dir, files, checklist):
    gl = load_json(glossary_path)
    if not isinstance(gl, dict) or not isinstance(gl.get("terms"), list) or not gl["terms"]:
        add(checklist, "term", "术语表检查", "note", "skip", "未找到有效 glossary.json，跳过")
        return []
    issues = []
    for t in gl["terms"]:
        if not isinstance(t, dict) or "canonical" not in t:
            continue
        canonical = str(t["canonical"])
        variants = [str(v) for v in t.get("variants", []) if isinstance(v, str)]
        used = {}
        for fp in files:
            lines = (read_text(fp) or "").splitlines()
            for ln, line in enumerate(lines, 1):
                for spell in [canonical] + variants:
                    if spell and spell in line:
                        used.setdefault(spell, []).append("{}:{}".format(
                            os.path.relpath(fp, output_dir).replace("\\", "/"), ln))
        if len(used) > 1:
            issues.append({"term": canonical, "used": used})
            add(checklist, "term", "术语表检查", "warning", "warn",
                "术语 {} 出现多种写法：{}".format(canonical, json.dumps(used, ensure_ascii=False)))
        elif used and variants and canonical not in used:
            issues.append({"term": canonical, "used": used})
            add(checklist, "term", "术语表检查", "warning", "warn",
                "术语 {} 仅出现 variant，未见 canonical：{}".format(canonical, json.dumps(used, ensure_ascii=False)))
    if not issues:
        add(checklist, "term", "术语表检查", "check", "pass",
            "已检查 {} 个术语，无漂移".format(len(gl["terms"])))
    return issues


def check_numbers(source_files, target_files, checklist):
    if not source_files:
        add(checklist, "num", "数字保真检查", "note", "skip", "未提供源文件，跳过")
        return []
    src_nums = set()
    for fp in source_files:
        txt = read_text(fp)
        if txt:
            for m in NUMBER_RE.findall(txt):
                src_nums.add(norm_number(m))
    tgt_nums = set()
    for fp in target_files:
        txt = read_text(fp)
        if txt:
            for m in NUMBER_RE.findall(txt):
                tgt_nums.add(norm_number(m))
    missing = sorted(n for n in src_nums if n and n not in tgt_nums)
    extra = sorted(n for n in tgt_nums if n and n not in src_nums)
    detail = "源数字 {} 个，译文数字 {} 个；源有译无 {} 个，译有源无 {} 个".format(
        len(src_nums), len(tgt_nums), len(missing), len(extra))
    # 数字保真为启发式（含中英文万/亿、单位换算等），只报 warning 不报 error
    if missing or extra:
        add(checklist, "num", "数字保真检查", "warning", "warn",
            detail + "（启发式，需人工确认：万/亿与 million/billion 及单位换算除外）")
    else:
        add(checklist, "num", "数字保真检查", "check", "pass", detail)
    return {"missing": missing, "extra": extra}


def main():
    ap = argparse.ArgumentParser(description="确定性自检 result.json 与产物")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--glossary", default=None, help="glossary.json 路径")
    ap.add_argument("--source", action="append", default=None, help="源材料文件（可多次）")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--report", default=DEFAULT_REPORT)
    a = ap.parse_args()

    result = load_json(a.out)
    checklist = []
    errors = 0

    if result is None:
        add(checklist, "json", "result.json 可读性", "error", "fail",
            "无法读取或不是合法 UTF-8 JSON：{}".format(a.out))
        errors += 1
        report = {"generated_at": now_iso(), "result_path": a.out,
                  "checks": checklist, "summary": {"pass": 0, "warn": 0, "fail": 1, "skip": 0},
                  "deterministic_signature": None, "exit_code": 1}
        os.makedirs(os.path.dirname(a.report) or ".", exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        print(json.dumps({"exit_code": 1, "summary": report["summary"]}, ensure_ascii=False))
        return 1

    # 1. case_id
    case_id = result.get("case_id")
    case_doc = load_json(a.case)
    case_expected = None
    if isinstance(case_doc, dict):
        for k in ("case_id", "id", "caseId"):
            if case_doc.get(k) is not None:
                case_expected = str(case_doc[k])
                break
    if not case_id:
        add(checklist, "case_id", "case_id 存在", "error", "fail", "缺少 case_id")
        errors += 1
    elif case_expected and str(case_id) != case_expected:
        add(checklist, "case_id", "case_id 一致", "error", "fail",
            "result.json={!r} 与 case.json={!r} 不一致".format(str(case_id), case_expected))
        errors += 1
    else:
        add(checklist, "case_id", "case_id 一致", "check", "pass", str(case_id))

    # 2. status
    status = result.get("status")
    if status not in FINAL_STATUS:
        add(checklist, "status", "status 合法", "error", "fail",
            "status={!r}，最终须为 success|partial|failed".format(status))
        errors += 1
    else:
        add(checklist, "status", "status 合法", "check", "pass", status)

    # 3. 顶层必填键
    missing_top = sorted(TOP_KEYS - set(result.keys()))
    if missing_top:
        add(checklist, "top", "顶层必填键", "error", "fail", "缺少：{}".format(missing_top))
        errors += 1
    else:
        add(checklist, "top", "顶层必填键", "check", "pass", "齐全")

    # 4. 主产物存在
    dels = result.get("deliverables")
    deliv_files = []
    if not isinstance(dels, list) or not dels:
        add(checklist, "deliv", "主产物非空", "error", "fail", "deliverables 为空")
        errors += 1
    else:
        missing_dels = []
        deliv_files = []
        for d in dels:
            if not isinstance(d, dict) or not d.get("path"):
                missing_dels.append(str(d))
                continue
            if not os.path.isfile(d["path"]):
                missing_dels.append(d["path"])
            elif os.path.splitext(d["path"])[1].lower() in TEXT_EXTS:
                deliv_files.append(d["path"])
        if missing_dels:
            add(checklist, "deliv", "主产物路径存在", "error", "fail",
                "以下路径不存在：{}".format(missing_dels))
            errors += 1
        else:
            add(checklist, "deliv", "主产物路径存在", "check", "pass",
                "{} 个 deliverable 均存在".format(len(dels)))

    # 5. fact_source_map 逐条校验
    fsm = result.get("fact_source_map")
    if not isinstance(fsm, list):
        add(checklist, "fsm", "fact_source_map 结构", "error", "fail", "非数组")
        errors += 1
    else:
        bad = []
        for i, item in enumerate(fsm):
            if not isinstance(item, dict):
                bad.append("{}: 非对象".format(i))
                continue
            if not item.get("item") or not item.get("source"):
                bad.append("{}: 缺 item/source".format(i))
            if item.get("kind") not in FACT_KINDS:
                bad.append("{}: kind={!r} 非法".format(i, item.get("kind")))
            if item.get("kind") == "claim" and (not item.get("locator") or not item.get("quote")):
                bad.append("{}: claim 缺 locator/quote".format(i))
        if bad:
            add(checklist, "fsm", "fact_source_map 条目", "error", "fail", "; ".join(bad[:8]))
            errors += 1
        else:
            add(checklist, "fsm", "fact_source_map 条目", "check", "pass",
                "{} 条均含 item/source，claim 含 locator+quote".format(len(fsm)))

    # 6. version_boundary
    vb = result.get("version_boundary")
    if not isinstance(vb, dict):
        add(checklist, "vb", "version_boundary 结构", "error", "fail", "非对象")
        errors += 1
    else:
        need = {"modifications", "human_confirm", "material_gaps", "anomalies", "editable"}
        miss = sorted(need - set(vb.keys()))
        if miss:
            add(checklist, "vb", "version_boundary 结构", "error", "fail", "缺少：{}".format(miss))
            errors += 1
        elif not isinstance(vb.get("editable"), bool):
            add(checklist, "vb", "version_boundary.editable", "error", "fail", "editable 非布尔")
            errors += 1
        else:
            add(checklist, "vb", "version_boundary 结构", "check", "pass", "齐全")

    # 7. sources/uncertainty/change_log
    if not isinstance(result.get("sources"), list):
        add(checklist, "meta", "sources 结构", "error", "fail", "sources 非数组")
        errors += 1
    if not isinstance(result.get("uncertainty"), list):
        add(checklist, "meta", "uncertainty 结构", "error", "fail", "uncertainty 非数组")
        errors += 1
    if not isinstance(result.get("change_log"), list) or not result.get("change_log"):
        add(checklist, "meta", "change_log 非空", "error", "fail", "change_log 缺失或为空")
        errors += 1
    if errors == 0:
        add(checklist, "meta", "sources/uncertainty/change_log", "check", "pass", "齐全")

    # 8. 术语表检查
    glossary_path = a.glossary
    if not glossary_path:
        for cand in ("/app/work/glossary.json", "/app/output/glossary.json"):
            if os.path.isfile(cand):
                glossary_path = cand
                break
    if glossary_path:
        check_glossary(glossary_path, a.output_dir, deliv_files, checklist)

    # 9. 数字保真（启发式）
    src_files = list(a.source or [])
    if not src_files:
        # 自动从 case_brief 材料清单取源
        brief = load_json("/app/work/case_brief.json")
        if isinstance(brief, dict):
            for m in brief.get("materials", []):
                if m.get("path") and os.path.isfile(m["path"]):
                    src_files.append(m["path"])
    check_numbers(src_files, deliv_files, checklist)

    # 10. 确定性签名：去掉时间变化字段后哈希
    canon = json.loads(json.dumps(result, ensure_ascii=False))
    if isinstance(canon.get("runtime"), dict):
        canon["runtime"] = {k: v for k, v in canon["runtime"].items()
                            if k not in ("duration_sec", "created_at")}
    for e in canon.get("change_log", []):
        if isinstance(e, dict):
            e.pop("time", None)
    det_sig = hashlib.sha256(
        json.dumps(canon, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")).hexdigest()

    n_pass = sum(1 for c in checklist if c["status"] == "pass")
    n_warn = sum(1 for c in checklist if c["status"] == "warn")
    n_fail = sum(1 for c in checklist if c["status"] == "fail")
    n_skip = sum(1 for c in checklist if c["status"] == "skip")
    exit_code = 0 if errors == 0 else 1

    report = {
        "generated_at": now_iso(),
        "result_path": a.out,
        "checks": checklist,
        "summary": {"pass": n_pass, "warn": n_warn, "fail": n_fail, "skip": n_skip},
        "deterministic_signature": det_sig,
        "note": "self-check only; not an official benchmark score",
        "exit_code": exit_code,
    }
    os.makedirs(os.path.dirname(a.report) or ".", exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(json.dumps({"exit_code": exit_code, "summary": report["summary"],
                      "deterministic_signature": det_sig[:16] + "..."},
                     ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
