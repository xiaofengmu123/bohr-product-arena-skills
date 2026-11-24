#!/usr/bin/env python3
"""academic-paper-writing Skill 运行时脚手架与自检（仅 Python 标准库）。

三个子命令（相对路径调用，位于 Skill 根目录 /skills/weekly-skill 下）：

  skeleton   读取 /input/case.json 与 /input/task.md，立即落盘 result.json 骨架与材料索引；
             任何输入（含空目录、坏 JSON）都返回退出码 0，并写出合法 JSON，绝不抛异常。
  verify     收口复核 result.json / manuscript.md / 证据映射，写 validation_report.json；
             退出码 0=全部通过，1=存在未通过项（脚本本身未崩溃，按报告修正后重跑）。
  selftest   用临时目录跑「空输入 / 坏 JSON / 正常输入」三种场景，验证脚本可运行；
             不产出任何正式结果，退出码 0=自检通过。

示例：
  python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work
  python3 scripts/main.py verify   --input /input --output /app/output --work /app/work
  python3 scripts/main.py selftest
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time

VERSION = "1.1.0"

ALLOWED_STATUS = {
    "in_progress", "partial", "complete",
    "insufficient_evidence", "input_error", "failed",
}

# output_contract 未定义时的默认契约（键为 result.json 顶层字段名）
DEFAULT_CONTRACT_FIELDS = {
    "deliverable": "manuscript.md",
    "fact_source_map": "evidence_map.json",
    "version_limits": "version_limits.json",
}

MARKER_S = "[S:"          # 事实来源标记
MARKER_C = "[C:"          # 文献引用标记
FLAG_TOKENS = ("[待确认]", "[缺失]", "[冲突待确认]", "[无法解析]")

CITE_DEF_RE = re.compile(r"\[C:([^\]\n]+)\]\s*:")   # 参考文献定义形如 [C:key]:


def now_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def emit(obj):
    """打印 JSON 到 stdout，容忍非 UTF-8 locale 的编码失败。"""
    s = json.dumps(obj, ensure_ascii=False)
    try:
        sys.stdout.write(s + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")


def load_json(path):
    """utf-8-sig 容错读取 JSON；失败返回 (None, [err])。"""
    if not os.path.exists(path):
        return None, ["not found: %s" % path]
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return json.load(f), []
    except Exception as e:  # noqa: BLE001
        return None, ["json parse failed (%s): %s" % (path, e)]


def read_text(path):
    """utf-8-sig 容错读取文本。"""
    if not os.path.exists(path):
        return None, ["not found: %s" % path]
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read(), []
    except Exception as e:  # noqa: BLE001
        return None, ["read failed (%s): %s" % (path, e)]


def write_json(path, data):
    """原子化落盘（临时文件 + replace）；失败返回错误列表，不抛异常。"""
    tmp = path + ".tmp"
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return []
    except Exception as e:  # noqa: BLE001
        return ["write failed (%s): %s" % (path, e)]


def guess_kind(name, ext):
    if name == "case.json" or name == "task.md":
        return "task"
    if ext == ".pdf":
        return "paper"
    if ext in (".md", ".txt", ".docx", ".doc", ".tex", ".bib"):
        return "text"
    if ext in (".csv", ".xlsx", ".xls", ".tsv"):
        return "table"
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".tif", ".tiff"):
        return "figure"
    return "other"


def discover_materials(input_dir):
    """扫描输入目录；单文件 >2MB 跳过并记录（不崩溃）。"""
    items, skipped = [], []
    if not os.path.isdir(input_dir):
        return items, ["input dir not found: %s" % input_dir]
    for name in sorted(os.listdir(input_dir)):
        p = os.path.join(input_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        ext = os.path.splitext(name)[1].lower()
        if size > 2 * 1024 * 1024:
            skipped.append({"path": p, "reason": "exceeds 2MB"})
            continue
        items.append({
            "name": name, "path": p, "size": size, "ext": ext,
            "kind": guess_kind(name, ext),
        })
    return items, skipped


def extract_case_id(case, case_path):
    if isinstance(case, dict):
        for k in ("case_id", "id", "challenge_id"):
            v = case.get(k)
            if isinstance(v, str) and v:
                return v
    base = os.path.splitext(os.path.basename(case_path))[0]
    return base if base not in ("case", "") else "case-unknown"


def extract_contract(case):
    """取出 case.json 的 output_contract 全部键；无则用默认契约。"""
    if isinstance(case, dict):
        oc = case.get("output_contract")
        if isinstance(oc, dict) and oc:
            return dict(oc), []
        if oc:
            return {"value": oc}, ["output_contract is not an object; wrapped as 'value'"]
    return dict(DEFAULT_CONTRACT_FIELDS), ["no output_contract in case.json; using defaults"]


# --------------------------------------------------------------------------- skeleton

def cmd_skeleton(args):
    t0 = now_ts()
    errors = []

    case_path = os.path.join(args.input, "case.json")
    task_path = os.path.join(args.input, "task.md")
    case, e1 = load_json(case_path)
    _, e2 = read_text(task_path)
    errors.extend(e1 + e2)

    case_id = extract_case_id(case or {}, case_path)
    contract, c_errs = extract_contract(case)
    errors.extend(c_errs)

    materials, skipped = discover_materials(args.input)
    errors.extend(skipped)

    # 材料索引骨架：后续逐条填充 key_claims/numbers/citations/flags
    material_index = {
        "case_id": case_id,
        "created_at": t0,
        "materials": materials,
        "skipped": skipped,
        "key_claims": [],
        "numbers": [],
        "citations": [],
        "flags": [],
    }

    if not materials:
        status = "insufficient_evidence"
    else:
        status = "in_progress"

    # result.json 顶层：case_id + output_contract 全部键（null 占位）+ 元字段
    result = {"case_id": case_id}
    for k in contract:
        result[k] = None
    result.update({
        "status": status,
        "sources": [],
        "uncertainty": [],
        "pending_human": [],
        "change_log": [{"ts": t0, "step": "skeleton", "note": "initial partial skeleton"}],
        "errors": errors,
        "degradations": skipped,
        "runtime": {
            "start_ts": t0,
            "seconds_budget": 900,
            "request_budget": 64,
            "requests_used": 0,
        },
    })

    errors.extend(write_json(args.output_result, result))
    errors.extend(write_json(os.path.join(args.work, "material_index.json"), material_index))
    errors.extend(write_json(
        os.path.join(args.work, "contract_meta.json"),
        {"case_id": case_id, "contract_keys": sorted(contract.keys()), "defaults_used": bool(c_errs)}))
    emit({
        "ok": True, "case_id": case_id, "status": status,
        "materials": len(materials), "skipped": len(skipped), "errors": errors,
    })
    return 0


# --------------------------------------------------------------------------- verify

def _scan_markers(text, prefix):
    out = []
    i = 0
    while True:
        j = text.find(prefix, i)
        if j < 0:
            break
        k = text.find("]", j)
        if k < 0:
            break
        out.append(text[j + len(prefix):k].strip())
        i = k + 1
    return out


def _scan_cite_defs(text):
    return {m.group(1).strip() for m in CITE_DEF_RE.finditer(text)}


def cmd_verify(args):
    report = {"ok": True, "checks": [], "errors": [], "warnings": []}
    result, errs = load_json(args.output_result)
    if errs or not isinstance(result, dict):
        report["ok"] = False
        report["errors"].extend(errs or ["result.json missing or invalid"])
        report["checks"].append({"id": "result_json_valid", "pass": False,
                                 "note": "; ".join(errs) or "missing"})
        write_json(os.path.join(args.work, "validation_report.json"), report)
        emit(report)
        return 1

    checks = []

    def chk(cid, ok, note=""):
        checks.append({"id": cid, "pass": bool(ok), "note": note})
        if not ok:
            report["ok"] = False

    chk("case_id_present", bool(result.get("case_id")), str(result.get("case_id")))
    chk("status_valid", result.get("status") in ALLOWED_STATUS, str(result.get("status")))
    for k in ("sources", "uncertainty", "pending_human", "change_log"):
        chk("field_" + k, isinstance(result.get(k), list), k)

    # output_contract 键完整且已填充（非 null）
    case, _ = load_json(os.path.join(args.input, "case.json"))
    contract, _ = extract_contract(case)
    contract_keys = sorted(contract.keys())
    missing = [k for k in contract_keys if k not in result]
    unfilled = [k for k in contract_keys if result.get(k) is None]
    chk("contract_keys_complete", not missing, ";".join(missing) or "all present")
    chk("contract_keys_filled", not unfilled, "unfilled: " + ";".join(unfilled))

    # sources 每条都能回溯（ref_id + locator）
    src_ok = all(isinstance(s, dict) and s.get("ref_id") and s.get("locator")
                 for s in result.get("sources", []))
    chk("sources_traceable", src_ok, "%d sources" % len(result.get("sources", [])))

    # 主产物存在且非空
    ms, _ = read_text(os.path.join(args.output, "manuscript.md"))
    chk("manuscript_present", bool(ms and ms.strip()), "manuscript.md 非空")

    if ms:
        s_ids = {s.get("ref_id") for s in result.get("sources", [])}
        used_s = set(_scan_markers(ms, MARKER_S))
        dangling_s = sorted(used_s - s_ids)
        chk("no_dangling_sources", not dangling_s, ";".join(dangling_s) or "ok")

        c_defs = _scan_cite_defs(ms)
        used_c = set(_scan_markers(ms, MARKER_C))
        dangling_c = sorted(used_c - c_defs)
        chk("no_dangling_citations", not dangling_c, ";".join(dangling_c) or "ok")
        unused_c = sorted(c_defs - used_c)
        if unused_c:
            report["warnings"].append("citations defined but never used: " + ";".join(unused_c))

        # 缺项/冲突标记必须被 uncertainty/pending_human 覆盖
        flagged = [t for t in FLAG_TOKENS if t in ms]
        unc_text = json.dumps(result.get("uncertainty", []), ensure_ascii=False) + \
            json.dumps(result.get("pending_human", []), ensure_ascii=False)
        uncovered = [t for t in flagged if t not in unc_text]
        chk("flags_covered", not uncovered, "uncovered: " + ";".join(uncovered) or "ok")

    report["checks"] = checks
    write_json(os.path.join(args.work, "validation_report.json"), report)
    emit(report)
    return 0 if report["ok"] else 1


# --------------------------------------------------------------------------- selftest

def _make_args(input_dir, output_dir, work_dir):
    return argparse.Namespace(input=input_dir, output=output_dir, work=work_dir,
                              output_result=os.path.join(output_dir, "result.json"))


def cmd_selftest(args):
    base = tempfile.mkdtemp(prefix="skill_selftest_")
    results = []
    try:
        # 场景 1：空输入目录 → insufficient_evidence，退出码 0，结果合法
        d1 = os.path.join(base, "empty")
        for sub in ("input", "output", "work"):
            os.makedirs(os.path.join(d1, sub))
        rc1 = cmd_skeleton(_make_args(os.path.join(d1, "input"),
                                      os.path.join(d1, "output"),
                                      os.path.join(d1, "work")))
        r1, _ = load_json(os.path.join(d1, "output", "result.json"))
        results.append(("empty_input",
                        rc1 == 0 and r1 is not None and
                        r1.get("status") == "insufficient_evidence" and bool(r1.get("case_id"))))

        # 场景 2：坏 JSON / 坏编码 → 不崩溃，退出码 0，写合法结果
        d2 = os.path.join(base, "badjson")
        for sub in ("input", "output", "work"):
            os.makedirs(os.path.join(d2, sub))
        with open(os.path.join(d2, "input", "case.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with open(os.path.join(d2, "input", "task.md"), "w", encoding="utf-8") as f:
            f.write("# task\n材料占位")
        rc2 = cmd_skeleton(_make_args(os.path.join(d2, "input"),
                                      os.path.join(d2, "output"),
                                      os.path.join(d2, "work")))
        r2, _ = load_json(os.path.join(d2, "output", "result.json"))
        results.append(("bad_json", rc2 == 0 and r2 is not None and r2.get("status") is not None))

        # 场景 3：正常输入（带 output_contract）→ skeleton + verify 全链路
        d3 = os.path.join(base, "normal")
        for sub in ("input", "output", "work"):
            os.makedirs(os.path.join(d3, sub))
        with open(os.path.join(d3, "input", "case.json"), "w", encoding="utf-8") as f:
            json.dump({"case_id": "case-selftest",
                       "output_contract": {"deliverable": "manuscript.md",
                                           "fact_source_map": "evidence_map.json"}}, f)
        with open(os.path.join(d3, "input", "task.md"), "w", encoding="utf-8") as f:
            f.write("# 示例任务（自检占位）\n")
        with open(os.path.join(d3, "input", "notes.md"), "w", encoding="utf-8") as f:
            f.write("材料占位内容")
        cmd_skeleton(_make_args(os.path.join(d3, "input"),
                                os.path.join(d3, "output"),
                                os.path.join(d3, "work")))
        r3, _ = load_json(os.path.join(d3, "output", "result.json"))
        # 模拟收口：填契约字段 + sources + 稿件
        r3["deliverable"] = "manuscript.md"
        r3["fact_source_map"] = "evidence_map.json"
        r3["status"] = "complete"
        r3["sources"] = [{"ref_id": "S1", "locator": "notes.md", "quote": "材料占位内容",
                          "role": "示例"}]
        r3["uncertainty"] = []
        write_json(os.path.join(d3, "output", "result.json"), r3)
        with open(os.path.join(d3, "output", "manuscript.md"), "w", encoding="utf-8") as f:
            f.write("# 结论\n某论断 [S:S1]，引用 [C:demo]。\n\n## 参考文献\n[C:demo]: Author. Title. Journal 2026.\n")
        rc3 = cmd_verify(_make_args(os.path.join(d3, "input"),
                                    os.path.join(d3, "output"),
                                    os.path.join(d3, "work")))
        rep, _ = load_json(os.path.join(d3, "work", "validation_report.json"))
        results.append(("normal_pipeline", rc3 == 0 and rep is not None and rep.get("ok")))

        ok = all(pass_ for _, pass_ in results)
        emit({"ok": ok, "results": results})
        return 0 if ok else 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(prog="main.py", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd")

    def add_io(sp):
        sp.add_argument("--input", default="/input")
        sp.add_argument("--output", default="/app/output")
        sp.add_argument("--work", default="/app/work")

    add_io(sub.add_parser("skeleton", help="落盘 result.json 骨架与材料索引（缺省动作）"))
    add_io(sub.add_parser("verify", help="收口复核 result.json/manuscript/证据映射"))
    add_io(sub.add_parser("selftest", help="临时目录自检，不产出正式结果"))
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "skeleton"           # 无子命令时安全缺省为 skeleton
    args.output_result = os.path.join(args.output, "result.json")
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    if cmd == "skeleton":
        return cmd_skeleton(args)
    if cmd == "verify":
        return cmd_verify(args)
    if cmd == "selftest":
        return cmd_selftest(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
