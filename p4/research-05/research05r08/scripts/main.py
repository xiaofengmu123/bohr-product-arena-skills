#!/usr/bin/env python3
"""科研选题与创新性评估 Skill 的可执行入口。

子命令:
  skeleton  读取 case.json，先落盘 status=partial 的合法 result.json 骨架
  evidence  扫描 /input 材料、抽取 PDF 文字、刷新 sources 与 material_inventory
  finalize  深合并 /app/work/fragments/*.json 到骨架，校验契约并写 report.md

仅依赖 Python 标准库；pdftotext 缺失时降级并记录。
"""
import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

DEFAULT_INPUT = "/input"
DEFAULT_OUTPUT = "/app/output"
DEFAULT_WORK = "/app/work"

TEXT_EXTS = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".tex", ".bib", ".ris", ".log", ".nq", ".ttl",
    ".jsonl", ".ndjson",
}
PDF_EXT = ".pdf"
OFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".ods"}
META_NAMES = {"case.json", "task.md"}
MAX_FILE_BYTES = 2 * 1024 * 1024

EMPTY = {
    "research_question": None,
    "search_protocol": None,
    "prior_work": None,
    "novelty": None,
    "feasibility": None,
    "evidence_matrix": [],
    "conflicts": [],
    "synthesis": None,
    "sources": [],
    "uncertainty": None,
    "pending_human": [],
    "errors": [],
    "degradations": [],
    "change_log": [],
}

FOUR_DELIVERABLES = ("research_question", "prior_work", "novelty", "feasibility")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log_change(entry, message, level="info"):
    entry.append({"time": now_iso(), "level": level, "message": message})


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        return None


def read_text_safe(path):
    raw = read_bytes(path)
    if raw is None:
        return None, None, "unable to read " + path
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8"), "utf-8", None
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), "utf-8-replace", "UTF-8 解码失败，已用替换符降级"


def read_json_safe(path):
    text, enc, err = read_text_safe(path)
    if text is None:
        return None, err
    try:
        return json.loads(text), err
    except (json.JSONDecodeError, ValueError) as e:
        return None, "bad JSON: " + str(e)


def sanitize(obj):
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


def collect_nonfinite(obj, path, out):
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            out.append(path or "<root>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            collect_nonfinite(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            collect_nonfinite(v, f"{path}[{i}]", out)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
    return path


def dir_hash(path):
    h = hashlib.sha1()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(root, name), path)
            h.update(rel.encode("utf-8", "replace"))
            h.update(b"\0")
            try:
                size = os.path.getsize(os.path.join(root, name))
            except OSError:
                size = -1
            h.update(str(size).encode())
    return h.hexdigest()[:16]


def extract_case_id(case_obj, input_dir):
    for key in ("case_id", "caseId", "task_id", "taskId", "instance_id"):
        v = case_obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unresolved-" + dir_hash(input_dir)


def extract_contract(case_obj):
    for key in ("output_contract", "contract"):
        v = case_obj.get(key)
        if isinstance(v, dict):
            return v
    return None


def looks_textual(data):
    sample = data[:4096]
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def new_result(case_id=None, status="partial"):
    result = {
        "case_id": case_id,
        "status": status,
        "output_contract": {},
        "schema_version": "2.0",
    }
    result.update(copy.deepcopy(EMPTY))
    result["runtime"] = {"started_at": now_iso(), "finished_at": None,
                         "elapsed_seconds": None,
                         "notes": "骨架先行落盘；后续 Phase 增量更新"}
    return result


def cmd_skeleton(args):
    result = new_result()
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    input_exists = os.path.isdir(args.input)
    case_path = os.path.join(args.input, "case.json") if input_exists else None
    case_obj = None
    contract = None

    if not input_exists:
        result["status"] = "input_error"
        result["errors"].append("input directory missing: " + args.input)
        log_change(result["change_log"], "skeleton: input directory missing -> input_error")
    elif not (case_path and os.path.isfile(case_path)):
        result["status"] = "input_error"
        result["errors"].append("case.json missing in " + args.input)
        log_change(result["change_log"], "skeleton: case.json missing -> input_error")
    else:
        case_obj, err = read_json_safe(case_path)
        if case_obj is None:
            result["status"] = "input_error"
            result["errors"].append("case.json 不可解析: " + (err or ""))
            log_change(result["change_log"], "skeleton: case.json 不可解析 -> input_error")
        else:
            result["case_id"] = extract_case_id(case_obj, args.input)
            contract = extract_contract(case_obj)
            if contract is not None:
                result["output_contract"] = copy.deepcopy(contract)
                for k in contract:
                    if k not in result:
                        result[k] = None
            log_change(result["change_log"],
                       "skeleton: case_id=%s contract_keys=%s" % (result["case_id"], sorted(contract or {})))

    write_json(os.path.join(args.output, "result.json"), result)
    manifest = {
        "input": args.input,
        "output": args.output,
        "work": args.work,
        "case_id": result["case_id"],
        "status": result["status"],
        "created_at": now_iso(),
    }
    write_json(os.path.join(args.work, "input_manifest.json"), manifest)
    print("skeleton: status=%s case_id=%s -> %s" % (
        result["status"], result["case_id"], os.path.join(args.output, "result.json")))
    return 0


def extract_pdf_text(pdf_path, out_txt, degradations):
    exe = shutil.which("pdftotext")
    if exe is None:
        degradations.append("pdftotext 不可用，" + os.path.basename(pdf_path) + " 标为 text_unavailable")
        return False
    try:
        proc = subprocess.run([exe, "-enc", "UTF-8", pdf_path, out_txt],
                              capture_output=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as e:
        degradations.append("pdftotext 失败 " + os.path.basename(pdf_path) + ": " + str(e))
        return False
    if proc.returncode != 0 or not os.path.isfile(out_txt):
        degradations.append("pdftotext 退出码 %s %s" % (proc.returncode, os.path.basename(pdf_path)))
        return False
    return True


def text_path_for(rel, text_dir):
    flat = rel.replace("/", "__").replace("\\", "__")
    return os.path.join(text_dir, flat + ".txt")


def cmd_evidence(args):
    result_path = os.path.join(args.output, "result.json")
    if not os.path.isfile(result_path):
        print("evidence: result.json 不存在，先跑 skeleton", file=sys.stderr)
        cmd_skeleton(args)
    result, err = read_json_safe(result_path)
    if result is None or not isinstance(result, dict):
        result = new_result(status="input_error")
        result["errors"].append("result.json 不可读: " + str(err))
        result["change_log"].append({"time": now_iso(), "level": "error",
                                     "message": "evidence: result.json 不可读，重建骨架"})

    degradations = result.get("degradations")
    if not isinstance(degradations, list):
        degradations = []
        result["degradations"] = degradations
    errors = result.get("errors")
    if not isinstance(errors, list):
        errors = []
        result["errors"] = errors

    text_dir = os.path.join(args.work, "text")
    os.makedirs(text_dir, exist_ok=True)
    inventory = []
    sources = []

    if not os.path.isdir(args.input):
        result["status"] = "input_error"
        errors.append("evidence: input directory missing: " + args.input)
        result["sources"] = sources
        write_json(os.path.join(args.work, "material_inventory.json"), inventory)
        write_json(result_path, result)
        print("evidence: input_error (missing /input)")
        return 0

    files = []
    for root, dirs, names in os.walk(args.input):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            files.append(os.path.join(root, name))
    files.sort()

    readable_count = 0
    for p in files:
        rel = os.path.relpath(p, args.input)
        base = os.path.basename(p)
        try:
            size = os.path.getsize(p)
        except OSError as e:
            errors.append("evidence: 无法读取大小 %s: %s" % (rel, e))
            continue
        sid = "src-" + hashlib.sha1(rel.encode("utf-8", "replace")).hexdigest()[:10]
        is_meta = base in META_NAMES
        entry = {"source_id": sid, "path": rel, "size": size, "is_meta": is_meta}

        if size > MAX_FILE_BYTES:
            entry.update({"kind": "skipped_oversize", "status": "skipped"})
            degradations.append("跳过超限文件 %s (%d bytes > 2MB)" % (rel, size))
        else:
            ext = os.path.splitext(base)[1].lower()
            if ext == PDF_EXT:
                entry["kind"] = "pdf"
                out_txt = text_path_for(rel, text_dir)
                if extract_pdf_text(p, out_txt, degradations):
                    entry.update({"status": "text_extracted", "text_path": out_txt})
                    if not is_meta:
                        readable_count += 1
                else:
                    entry["status"] = "text_unavailable"
            elif ext in TEXT_EXTS:
                entry["kind"] = "text"
                text, enc, terr = read_text_safe(p)
                entry["encoding"] = enc
                if text is None:
                    entry["status"] = "unreadable"
                    errors.append("evidence: 无法读取 %s: %s" % (rel, terr))
                else:
                    out_txt = text_path_for(rel, text_dir)
                    try:
                        os.makedirs(os.path.dirname(out_txt), exist_ok=True)
                        write_text(out_txt, text)
                        entry.update({"status": "readable", "text_path": out_txt})
                        if terr:
                            degradations.append("%s: %s" % (rel, terr))
                        if not is_meta:
                            readable_count += 1
                    except OSError as e:
                        entry["status"] = "unreadable"
                        errors.append("evidence: 写提取文本失败 %s: %s" % (rel, e))
            elif ext in OFFICE_EXTS:
                entry.update({"kind": "office_binary", "status": "binary_no_text"})
                degradations.append("%s: 办公二进制格式，未做文字抽取；如需引用请另附文本或转 PDF" % rel)
            else:
                raw = read_bytes(p)
                if raw is not None and looks_textual(raw):
                    entry["kind"] = "text_heuristic"
                    text, enc, terr = read_text_safe(p)
                    entry["encoding"] = enc
                    out_txt = text_path_for(rel, text_dir)
                    try:
                        os.makedirs(os.path.dirname(out_txt), exist_ok=True)
                        write_text(out_txt, text or "")
                        entry.update({"status": "readable", "text_path": out_txt})
                        if terr:
                            degradations.append("%s: %s" % (rel, terr))
                        if not is_meta:
                            readable_count += 1
                    except OSError as e:
                        entry["status"] = "unreadable"
                        errors.append("evidence: 写提取文本失败 %s: %s" % (rel, e))
                else:
                    entry.update({"kind": "binary_unknown", "status": "ignored_binary"})

        sources.append(entry)
        inventory.append(entry)

    result["sources"] = sources
    if result.get("status") != "input_error":
        if readable_count == 0:
            result["status"] = "insufficient_evidence"
            errors.append("无可用研究材料（仅元信息或无可读文字材料）")
    log_change(result["change_log"],
               "evidence: 扫描 %d 文件，可读研究材料 %d 个" % (len(files), readable_count))

    write_json(os.path.join(args.work, "material_inventory.json"), inventory)
    write_json(result_path, result)
    print("evidence: files=%d readable=%d status=%s" % (len(files), readable_count, result["status"]))
    return 0


def _item_key(item):
    """稳定序列化整个元素，用于列表去重；保证 finalize 幂等（可复跑）。"""
    try:
        return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    except (ValueError, TypeError):
        return repr(item)


def _merge_lists(base, patch):
    out = list(base)
    seen = set(_item_key(item) for item in out)
    for item in patch:
        k = _item_key(item)
        if k not in seen:
            out.append(copy.deepcopy(item))
            seen.add(k)
    return out


def deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        for k, v in patch.items():
            if k in base and base[k] not in (None, [], {}):
                base[k] = deep_merge(base[k], v)
            else:
                base[k] = copy.deepcopy(v)
        return base
    if isinstance(base, list) and isinstance(patch, list):
        return _merge_lists(base, patch)
    return copy.deepcopy(patch)


def validate(result):
    required = list(EMPTY.keys()) + ["case_id", "status", "output_contract", "runtime"]
    for k in required:
        if k not in result:
            result[k] = copy.deepcopy(EMPTY.get(k))

    contract = result.get("output_contract")
    if isinstance(contract, dict):
        for k in contract:
            if k not in result:
                result[k] = None

    ev = result.get("evidence_matrix")
    if isinstance(ev, list):
        for e in ev:
            if isinstance(e, dict):
                e.setdefault("claim_type", "fact")
                e.setdefault("status", "ok")
                if not e.get("locator"):
                    e["locator"] = "not_reported"
                if not e.get("quote"):
                    e["quote"] = "not_reported"

    return result


def compute_status(result):
    status = result.get("status")
    if status in ("input_error", "insufficient_evidence"):
        return
    four = [result.get(k) for k in FOUR_DELIVERABLES]
    ev = result.get("evidence_matrix") or []
    syn = result.get("synthesis")
    if all(isinstance(x, dict) for x in four) and ev and isinstance(syn, dict):
        result["status"] = "ok"
    else:
        result["status"] = "partial"


def fmt(v):
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, ensure_ascii=False)
        except (ValueError, TypeError):
            return str(v)
    return str(v)


def render_report(result):
    L = []
    L.append("# 科研选题与创新性评估报告\n")
    L.append("- case_id: `%s`" % (result.get("case_id") or "(unresolved)"))
    L.append("- status: `%s`" % result.get("status"))
    L.append("- schema_version: `%s`\n" % result.get("schema_version", "-"))

    rq = result.get("research_question")
    if isinstance(rq, dict):
        L.append("## 1. 研究问题定义")
        if isinstance(rq.get("testable_statement"), str):
            L.append("**可检验陈述**：%s\n" % rq["testable_statement"])
        for k in ("object", "variables", "baseline", "expected_conclusion", "falsification"):
            v = rq.get(k)
            if v:
                L.append("- %s: %s" % (k, fmt(v)))
        if rq.get("status") or rq.get("gaps"):
            L.append("- 状态: %s；缺口: %s" % (rq.get("status"), fmt(rq.get("gaps"))))
        L.append("")

    sp = result.get("search_protocol")
    if isinstance(sp, dict):
        L.append("## 检索协议")
        for k in ("query_terms", "inclusion_criteria", "exclusion_criteria", "databases", "screening_notes"):
            v = sp.get(k)
            if v:
                L.append("- %s: %s" % (k, fmt(v)))
        L.append("")

    pw = result.get("prior_work")
    if isinstance(pw, dict):
        L.append("## 2. 已有工作比较")
        cm = pw.get("comparison_matrix") or []
        if cm:
            L.append("比较矩阵 %d 行：" % len(cm))
            for row in cm:
                if isinstance(row, dict):
                    L.append("- %s | 任务: %s | 方法: %s | 数据: %s | 评价: %s | 结论: %s" % (
                        row.get("work", "(未命名)"), row.get("task", ""), row.get("method", ""),
                        row.get("data", ""), row.get("evaluation", ""), row.get("conclusion", "")))
        if pw.get("closest_work"):
            L.append("- 最相近工作: %s" % fmt(pw.get("closest_work")))
        if pw.get("non_comparable_reasons"):
            L.append("- 不可比原因: %s" % fmt(pw.get("non_comparable_reasons")))
        L.append("")

    nv = result.get("novelty")
    if isinstance(nv, dict):
        L.append("## 3. 创新点识别")
        for c in (nv.get("claims") or []):
            if isinstance(c, dict):
                L.append("- [%s] %s: %s；覆盖: %s" % (
                    c.get("verdict", "?"), c.get("type", ""), c.get("claim", ""), c.get("covered_by", "")))
        L.append("")

    fb = result.get("feasibility")
    if isinstance(fb, dict):
        L.append("## 4. 可行性判断")
        for p in (fb.get("preconditions") or []):
            if isinstance(p, dict):
                L.append("- [%s] %s: %s" % (p.get("status", "?"), p.get("dimension", ""), p.get("statement", "")))
        if fb.get("conditional_conclusion"):
            L.append("- 带条件结论: %s" % fb.get("conditional_conclusion"))
        if fb.get("failure_risks"):
            L.append("- 失败风险: %s" % fmt(fb.get("failure_risks")))
        L.append("")

    syn = result.get("synthesis")
    if isinstance(syn, dict):
        L.append("## 5. 综合结论")
        for c in (syn.get("conclusions") or []):
            L.append("- %s" % c)
        if syn.get("research_gaps"):
            L.append("\n研究空白：%s" % fmt(syn.get("research_gaps")))
        if syn.get("boundary_statement"):
            L.append("\n边界说明：%s" % syn.get("boundary_statement"))
        L.append("")

    ev = result.get("evidence_matrix") or []
    if ev:
        L.append("## 6. 证据矩阵（%d 条）" % len(ev))
        for e in ev:
            if isinstance(e, dict):
                L.append("- `%s` %s [%s] %s" % (
                    e.get("source_id", ""), e.get("locator", ""), e.get("claim_type", ""), e.get("claim", "")))
                q = e.get("quote", "")
                if q:
                    L.append("  > %s" % q)
        L.append("")

    conf = result.get("conflicts") or []
    if conf:
        L.append("## 7. 冲突证据（%d 条）" % len(conf))
        for c in conf:
            if isinstance(c, dict):
                L.append("- %s: A=%s / B=%s → %s" % (
                    c.get("topic", ""), fmt(c.get("side_a")), fmt(c.get("side_b")), c.get("resolution", "")))
        L.append("")

    unc = result.get("uncertainty")
    if isinstance(unc, dict):
        L.append("## 8. 不确定性")
        for k in ("biases", "gaps", "unresolved"):
            v = unc.get(k)
            if v:
                L.append("- %s: %s" % (k, fmt(v)))
        L.append("")

    ph = result.get("pending_human") or []
    if ph:
        L.append("## 9. 待人工确认（%d 条）" % len(ph))
        for x in ph:
            L.append("- %s" % x)
        L.append("")

    errs = result.get("errors") or []
    deg = result.get("degradations") or []
    if errs or deg:
        L.append("## 10. 限制与降级")
        for e in errs:
            L.append("- error: %s" % e)
        for d in deg:
            L.append("- degraded: %s" % d)
        L.append("")

    L.append("---")
    L.append("来源文件 %d 个；改动记录 %d 条。" % (
        len(result.get("sources") or []), len(result.get("change_log") or [])))
    return "\n".join(L)


def cmd_finalize(args):
    result_path = os.path.join(args.output, "result.json")
    if not os.path.isfile(result_path):
        print("finalize: result.json 不存在，先跑 skeleton", file=sys.stderr)
        cmd_skeleton(args)
    result, err = read_json_safe(result_path)
    if result is None or not isinstance(result, dict):
        result = new_result(status="input_error")
        result["errors"].append("result.json 不可读: " + str(err))

    frag_dir = os.path.join(args.work, "fragments")
    frags = []
    if os.path.isdir(frag_dir):
        for name in sorted(os.listdir(frag_dir)):
            if name.endswith(".json"):
                obj, ferr = read_json_safe(os.path.join(frag_dir, name))
                if obj is None:
                    result.setdefault("errors", []).append("fragment 不可解析 %s: %s" % (name, ferr))
                else:
                    frags.append((name, obj))
    if not frags:
        result.setdefault("errors", []).append("finalize: 未发现 fragments，保持骨架状态")

    for name, frag in frags:
        deep_merge(result, frag)
        log_change(result.setdefault("change_log", []), "finalize: 合并 fragment " + name)

    result = validate(result)
    compute_status(result)

    nf = []
    collect_nonfinite(result, "", nf)
    if nf:
        result.setdefault("degradations", []).append(
            "finalize: 将非有限数值替换为 null（%d 处）: %s" % (len(nf), nf[:5]))
        result = sanitize(result)

    rt = result.get("runtime") or {}
    rt["finished_at"] = now_iso()
    if rt.get("started_at"):
        try:
            t0 = datetime.fromisoformat(rt["started_at"])
            t1 = datetime.now(timezone.utc).astimezone()
            rt["elapsed_seconds"] = round((t1 - t0).total_seconds(), 1)
        except (ValueError, TypeError):
            rt["elapsed_seconds"] = None
    result["runtime"] = rt

    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as e:
        result.setdefault("errors", []).append("finalize: 结果含 NaN/Inf，已拦截: " + str(e))
        result["status"] = "partial"
        write_json(result_path, result)
        return 2

    write_json(result_path, result)
    write_text(os.path.join(args.output, "report.md"), render_report(result))
    print("finalize: status=%s frags=%d -> result.json + report.md" % (result["status"], len(frags)))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="main.py",
                                description="科研选题与创新性评估 Skill 入口")
    sub = p.add_subparsers(dest="cmd", required=True)
    handlers = {"skeleton": cmd_skeleton, "evidence": cmd_evidence, "finalize": cmd_finalize}
    for name, fn in handlers.items():
        sp = sub.add_parser(name, help="run %s phase" % name)
        sp.add_argument("--input", default=DEFAULT_INPUT)
        sp.add_argument("--output", default=DEFAULT_OUTPUT)
        sp.add_argument("--work", default=DEFAULT_WORK)
        sp.set_defaults(func=fn)
    return p


def main():
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
