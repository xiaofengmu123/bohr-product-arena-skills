"""科研成果转化写作 Skill · 主入口（纯标准库，退出码恒 0）。

命令（SKILL.md 按此调用，路径一律相对 scripts/）：
  init      [第 0 步·前 3 分钟内] 建 source index + evidence_candidates + partial result.json
  survey    打印环境：输入文件、契约键、可用工具
  show      打印带 locator 的源文（事实/引文从这里逐字复制）
  extract   抽取证据候选 → /app/work/evidence_candidates.json
  check     机械校验 /app/work/draft.json + /app/output/result.json
  build     组装主交付 + result.json + 证据/改动/术语资产
  selfcheck 离线自检（空输入/坏 JSON/无材料/确定性），不联网

设计原则：脚本不替你写正文——正文由 Agent 基于材料真实撰写/改写；
脚本负责发现、骨架落盘、证据抽取、契约回填、机械校验与确定性组装。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import importlib.util
import json
import os
import shutil
import sys
import time

try:
    from . import lib, validate
except ImportError:  # 兼容 `python main.py` 直接运行
    import lib
    import validate

SKILL_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# 环境与路径
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(prog="main.py", description="科研成果转化写作 Skill")
    p.add_argument("cmd", choices=["init", "survey", "show", "extract", "check",
                                   "build", "selfcheck"])
    p.add_argument("--input", default=lib.DEFAULT_INPUT, help="输入目录")
    p.add_argument("--output", default=lib.DEFAULT_OUTPUT, help="产物目录")
    p.add_argument("--work", default=lib.DEFAULT_WORK, help="工作目录")
    p.add_argument("--evidence", action="store_true", help="show 时同时打印证据候选")
    return p.parse_args()


def ensure_dirs(*dirs):
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def load_json(path):
    obj, err = lib.read_json(path)
    return obj, err


# ---------------------------------------------------------------------------
# 状态与运行信息
# ---------------------------------------------------------------------------

def compute_status(index: dict, draft: dict, rep: dict):
    if index.get("case_json_state") == "bad_json":
        return lib.STATUS_INPUT_ERROR
    materials = index.get("materials") or []
    deliverable = (draft.get("deliverable_text") or "").strip()
    if not materials and not deliverable:
        return lib.STATUS_INSUFFICIENT
    if not deliverable:
        return lib.STATUS_PARTIAL
    if rep and rep.get("fail", 0) > 0:
        return lib.STATUS_PARTIAL
    return lib.STATUS_OK


def make_runtime(start: float, index: dict):
    return {
        "skill_version": SKILL_VERSION,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start, 3),
        "model_requests": None,
        "contract_keys": index.get("contract_keys") or [],
        "note": "model_requests/benchmark scores are platform-tracked; not fabricated here",
    }


# ---------------------------------------------------------------------------
# 契约键 → 业务字段映射
# ---------------------------------------------------------------------------

def _build_sources(index: dict):
    out = []
    for m in index.get("materials") or []:
        out.append({
            "source_id": m["source_id"],
            "rel_path": m["rel_path"],
            "path": m["abs_path"],
            "kind": m["kind"],
            "suffix": m["suffix"],
            "size": m["size"],
        })
    task = index.get("task_md")
    if task:
        out.append({"source_id": "TASK", "rel_path": os.path.basename(task),
                    "path": task, "kind": "text", "suffix": ".md"})
    return out


def _business_value(key: str, draft: dict, index: dict):
    d = draft or {}
    if key in ("result", "results"):
        return d.get("result") or {}
    if key == "claims":
        return d.get("claims") or d.get("evidence_map") or []
    if key in ("uncertainties", "uncertainty"):
        return d.get("uncertainties") or []
    if key == "sources":
        return _build_sources(index)
    if key in ("change_log", "changes"):
        return d.get("change_log") or []
    if key == "evidence_map":
        return d.get("evidence_map") or []
    if key == "preserved_facts":
        return d.get("preserved_facts") or []
    if key == "glossary":
        return d.get("glossary") or []
    if key == "pending_human":
        return d.get("pending_human") or []
    if key == "errors":
        return d.get("errors") or []
    if key == "degradations":
        return d.get("degradations") or []
    if key == "limitations":
        return d.get("limitations") or []
    return None


def assemble_result(index: dict, draft: dict, status: str, start: float):
    case_id = index.get("case_id") or (draft or {}).get("case_id") or "unknown"
    skeleton = lib.null_fill_contract(index.get("contract"))
    result = dict(skeleton)
    result["case_id"] = case_id
    result["status"] = status

    # 契约键回填（已知业务键从 draft/index 取，未知键保持空值）
    for k in result:
        if k in ("case_id", "status"):
            continue
        v = _business_value(k, draft, index)
        if v is not None:
            result[k] = v

    # 公共字段（保证存在，且不覆盖已有契约字段；用 setdefault）
    for k, v in {
        "sources": _build_sources(index),
        "uncertainty": (draft or {}).get("uncertainties") or [],
        "change_log": (draft or {}).get("change_log") or [],
        "errors": (draft or {}).get("errors") or [],
        "degradations": (draft or {}).get("degradations") or [],
        "pending_human": (draft or {}).get("pending_human") or [],
        "limitations": (draft or {}).get("limitations") or [],
    }.items():
        result.setdefault(k, v)

    # 扫描错误并入 errors（去重）
    scan_errors = index.get("scan_errors") or []
    if scan_errors:
        merged = list(result.get("errors") or []) + scan_errors
        result["errors"] = list(dict.fromkeys(merged))

    result["runtime"] = make_runtime(start, index)
    result["coverage"] = {
        "deliverable_chars": len((draft or {}).get("deliverable_text") or ""),
        "preserved_facts": len((draft or {}).get("preserved_facts") or []),
        "evidence_map": len((draft or {}).get("evidence_map") or []),
        "change_log": len((draft or {}).get("change_log") or []),
        "glossary": len((draft or {}).get("glossary") or []),
        "contract_keys": len(index.get("contract_keys") or []),
    }
    return result


# ---------------------------------------------------------------------------
# 材料内容读取（含 PDF 纯文本降级，不依赖外部 pdftotext 也能跑）
# ---------------------------------------------------------------------------

def read_material_text(m: dict):
    text = lib.read_text(m["abs_path"])
    if text:
        return text
    if m["suffix"] == ".pdf":
        # 尝试 pdftotext（若可用）；不可用则记录降级，不崩溃
        if shutil.which("pdftotext"):
            import subprocess
            try:
                r = subprocess.run(["pdftotext", "-layout", m["abs_path"], "-"],
                                   capture_output=True, timeout=60)
                if r.returncode == 0:
                    return r.stdout.decode("utf-8", errors="replace")
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def cmd_init(args):
    start = time.time()
    ensure_dirs(args.work, args.output)
    case_path = os.path.join(args.input, "case.json")
    if os.path.isfile(case_path):
        case, cerr = load_json(case_path)
        case_json_state = "ok" if isinstance(case, dict) else "bad_json"
    else:
        case, cerr = None, "case_json_missing"
        case_json_state = "missing"

    index = lib.build_source_index(args.input, case or {},
                                   os.path.join(args.input, "task.md"))
    index["case_json_state"] = case_json_state

    # 抽取证据候选（供 Agent 复用，不替 Agent 决定）
    candidates = []
    for m in index["materials"]:
        if m["kind"] != "text":
            continue
        text = read_material_text(m)
        if text:
            candidates.extend(lib.extract_evidence(text, m["source_id"], m["rel_path"]))
    index["evidence_candidates"] = candidates

    # 骨架 draft（Agent 后续编辑）
    skeleton = {
        "deliverable_name": "rewrite.md",
        "audience": "",
        "genre": "",
        "deliverable_text": "",
        "result": {},
        "preserved_facts": [],
        "evidence_map": [],
        "change_log": [],
        "glossary": [],
        "uncertainties": [],
        "pending_human": [],
        "limitations": [],
        "errors": [cerr] if cerr else [],
        "degradations": [],
        "extra_files": {},
    }

    rep = {"fail": 0, "warn": 0, "items": []}
    status = compute_status(index, skeleton, rep)
    result = assemble_result(index, skeleton, status, start)

    # 落盘：先写 partial result.json（第 0 步骨架）
    _write_json(os.path.join(args.work, "source_index.json"), index)
    _write_json(os.path.join(args.work, "evidence_candidates.json"), candidates)
    _write_json(os.path.join(args.work, "draft.skeleton.json"), skeleton)
    _write_json(os.path.join(args.output, "result.json"), result)

    print("init: case_id=%s status=%s materials=%d contract_keys=%d candidates=%d" % (
        index.get("case_id"), result["status"], len(index["materials"]),
        len(index.get("contract_keys") or []), len(candidates)))
    print("result.json -> %s" % os.path.join(args.output, "result.json"))
    if cerr:
        print("input warning: %s" % cerr)
    return 0


def cmd_survey(args):
    index = load_json(os.path.join(args.work, "source_index.json"))[0] or {}
    print("== input ==")
    print("case_id:", index.get("case_id"))
    print("task_md:", index.get("task_md"))
    print("materials:")
    for m in index.get("materials") or []:
        print("  %s %s (%d bytes, %s)" % (m["source_id"], m["rel_path"], m["size"], m["kind"]))
    print("== contract ==")
    print("keys:", index.get("contract_keys"))
    if index.get("contract_error"):
        print("contract_error:", index["contract_error"])
    print("== tools ==")
    for mod in ("docx", "openpyxl", "pptx", "PIL", "numpy", "pandas", "matplotlib", "fitz"):
        print("  python_module %-10s %s" % (mod, importlib.util.find_spec(mod) is not None))
    for tool in ("pdftotext", "pandoc", "pdflatex"):
        print("  tool %-10s %s" % (tool, shutil.which(tool) or False))
    return 0


def cmd_show(args):
    index = load_json(os.path.join(args.work, "source_index.json"))[0] or {}
    for m in index.get("materials") or []:
        if m["kind"] != "text":
            print("\n[%s] %s (binary/non-text, not shown)" % (m["source_id"], m["rel_path"]))
            continue
        text = read_material_text(m)
        print("\n===== [%s] %s =====" % (m["source_id"], m["rel_path"]))
        if text is None:
            print("(unreadable)")
            continue
        for pno, body in lib.split_paras(text):
            print("\n[%s#para.%d]" % (m["rel_path"], pno))
            print(body)
    if args.evidence:
        cands = load_json(os.path.join(args.work, "evidence_candidates.json"))[0] or []
        print("\n===== evidence candidates =====")
        for c in cands:
            print("[%s] %-16s %-28s %s" % (c["source_id"], c["kind"], c["value"], c["quote"][:60]))
    return 0


def cmd_extract(args):
    ensure_dirs(args.work)
    index = load_json(os.path.join(args.work, "source_index.json"))[0]
    if not index:
        # 未 init 时先 init
        cmd_init(args)
        index = load_json(os.path.join(args.work, "source_index.json"))[0] or {}
    candidates = index.get("evidence_candidates") or []
    _write_json(os.path.join(args.work, "evidence_candidates.json"), candidates)
    print("evidence_candidates: %d -> %s" % (len(candidates),
          os.path.join(args.work, "evidence_candidates.json")))
    return 0


def cmd_check(args):
    index = load_json(os.path.join(args.work, "source_index.json"))[0] or {}
    draft = load_json(os.path.join(args.work, "draft.json"))[0] or {}
    result = load_json(os.path.join(args.output, "result.json"))[0] or {}
    rep = validate.validate(draft, result, index)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


def cmd_build(args):
    start = time.time()
    ensure_dirs(args.work, args.output)
    index = load_json(os.path.join(args.work, "source_index.json"))[0]
    if not index:
        cmd_init(args)
        index = load_json(os.path.join(args.work, "source_index.json"))[0] or {}
    draft = load_json(os.path.join(args.work, "draft.json"))[0]
    if not draft:
        draft = load_json(os.path.join(args.work, "draft.skeleton.json"))[0] or {}
    draft = draft or {}

    rep_draft = validate.validate(draft, {}, index)  # 先只测 draft 本体
    status = compute_status(index, draft, rep_draft)
    result = assemble_result(index, draft, status, start)
    rep = validate.validate(draft, result, index)

    # 用最终校验结果重算状态；有 FAIL 且非 input_error/insufficient 时降为 partial
    final_status = compute_status(index, draft, rep)
    result["status"] = final_status
    if rep["fail"] > 0 and final_status not in (lib.STATUS_INPUT_ERROR,
                                                lib.STATUS_INSUFFICIENT):
        result["status"] = lib.STATUS_PARTIAL
    result["check"] = {"fail": rep["fail"], "warn": rep["warn"]}

    _write_deliverables(args.output, index, draft)
    _write_json(os.path.join(args.output, "result.json"), result)
    _write_report(args.output, index, draft, result, rep)

    print("build: status=%s fail=%d warn=%d -> %s" % (
        result["status"], rep["fail"], rep["warn"],
        os.path.join(args.output, "result.json")))
    return 0


def _write_deliverables(output_dir, index, draft):
    name = (draft.get("deliverable_name") or "rewrite.md").strip()
    if not name or "/" in name or "\\" in name:
        name = "rewrite.md"
    text = draft.get("deliverable_text") or ""
    if text:
        with open(os.path.join(output_dir, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    _write_json(os.path.join(output_dir, "preserved_facts.json"),
                draft.get("preserved_facts") or [])
    _write_json(os.path.join(output_dir, "evidence_map.json"),
                draft.get("evidence_map") or [])
    _write_json(os.path.join(output_dir, "change_log.json"),
                draft.get("change_log") or [])
    _write_glossary_csv(os.path.join(output_dir, "glossary.csv"),
                        draft.get("glossary") or [])


def _write_glossary_csv(path, glossary):
    if not glossary:
        return
    fields = ["term", "canonical", "first_expansion", "synonyms", "note"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in glossary:
            if isinstance(row, dict):
                w.writerow({f: row.get(f, "") for f in fields})


def _write_report(output_dir, index, draft, result, rep):
    lines = ["# 科研成果转化写作 · 交付复核报告", "",
             "- case_id: %s" % result.get("case_id"),
             "- status: %s" % result.get("status"),
             "- audience: %s" % (draft.get("audience") or "未指定"),
             "- genre: %s" % (draft.get("genre") or "未指定"),
             "- 校验: fail=%d warn=%d" % (rep["fail"], rep["warn"]),
             ""]
    if result.get("status") == lib.STATUS_INPUT_ERROR:
        lines.append("> 输入异常：%s" % (index.get("contract_error") or "unknown"))
    if (draft.get("uncertainties") or []):
        lines += ["## 不确定项", ""] + ["- %s" % u for u in draft["uncertainties"]] + [""]
    if (draft.get("pending_human") or []):
        lines += ["## 待人工确认", ""] + ["- %s" % u for u in draft["pending_human"]] + [""]
    if (draft.get("limitations") or []):
        lines += ["## 限制", ""] + ["- %s" % u for u in draft["limitations"]] + [""]
    lines += ["## 产物清单", ""]
    for name in sorted(os.listdir(output_dir)):
        lines.append("- %s" % name)
    with open(os.path.join(output_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(lib.safe_dumps(obj))


# ---------------------------------------------------------------------------
# 离线自检
# ---------------------------------------------------------------------------

def cmd_selfcheck(args):
    import tempfile
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    base = tempfile.mkdtemp(prefix="sk_selfcheck_")
    try:
        # 1. 空输入目录
        empty_in = os.path.join(base, "empty")
        os.makedirs(empty_in)
        _run_sub("init", empty_in, os.path.join(base, "out1"), os.path.join(base, "w1"), check)

        # 2. 坏 JSON
        bad_in = os.path.join(base, "bad")
        os.makedirs(bad_in)
        with open(os.path.join(bad_in, "case.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json ")
        _run_sub("init", bad_in, os.path.join(base, "out2"), os.path.join(base, "w2"), check)

        # 3. 正常案例（合成，非正式）
        good_in = os.path.join(base, "good")
        os.makedirs(good_in)
        _write_json(os.path.join(good_in, "case.json"), {
            "case_id": "selfcheck-001",
            "task_instruction": "把 paper.md 的摘要改写为面向公众的科普短文（≤200字）。",
            "output_contract": {
                "case_id": "string",
                "status": "answered|partial|insufficient_evidence|input_error",
                "result": "对象",
                "claims": "数组",
                "uncertainties": "数组",
            },
        })
        with open(os.path.join(good_in, "task.md"), "w", encoding="utf-8") as fh:
            fh.write("# 自检任务\n\n把 paper.md 改写为科普短文。\n")
        with open(os.path.join(good_in, "paper.md"), "w", encoding="utf-8") as fh:
            fh.write("样品 A 在 25 °C、常压、空速 30000 h-1 下转化率为 87.4%。\n\n"
                     "对照样品 B 在同条件下转化率仅为 41.2%。\n\n"
                     "作者把 A 的更高活性归因于更高的表面氧空位浓度，但该机制有待进一步验证。\n")
        _run_sub("init", good_in, os.path.join(base, "out3"), os.path.join(base, "w3"), check)

        # 写一份 draft 再 build，测组装与确定性
        w3 = os.path.join(base, "w3")
        draft = {
            "deliverable_name": "rewrite.md",
            "audience": "公众",
            "genre": "科普文章",
            "deliverable_text": "一项研究显示，样品 A 在 25 °C 下转化率为 87.4%，高于对照样品 B 的 41.2%。研究人员推测这与表面氧空位有关，但该机制仍有待验证。",
            "result": {"sections": [{"heading": "正文", "locator": "para.1"}]},
            "preserved_facts": [{"fact_id": "F1", "value": "87.4", "unit": "%",
                                 "source_id": "S1", "locator": "paper.md#para.1",
                                 "quote": "转化率为 87.4%", "status": "preserved"}],
            "evidence_map": [{"claim_id": "C1", "claim": "样品 A 转化率 87.4%",
                              "source_id": "S1", "locator": "paper.md#para.1",
                              "quote": "转化率为 87.4%", "kind": "direct", "strength": "established"}],
            "change_log": [{"id": "R1", "action": "rewrite",
                            "original_quote": "作者把 A 的更高活性归因于更高的表面氧空位浓度",
                            "revised_quote": "研究人员推测这与表面氧空位有关",
                            "reason": "科普化表达，保留'推测/有待验证'限定", "locator": "paper.md#para.3"}],
            "glossary": [{"term": "转化率", "canonical": "转化率", "first_expansion": "",
                          "synonyms": "", "note": ""}],
            "uncertainties": ["机制归因有待验证"], "pending_human": [], "limitations": [],
        }
        _write_json(os.path.join(w3, "draft.json"), draft)
        out3 = os.path.join(base, "out3")
        _run_sub("build", good_in, out3, w3, check)
        h1 = _hash_result(os.path.join(out3, "result.json"))
        _run_sub("build", good_in, os.path.join(base, "out4"), w3, check)
        h2 = _hash_result(os.path.join(base, "out4", "result.json"))
        check("determinism", h1 == h2, "hash1=%s hash2=%s" % (h1[:12], h2[:12]))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    npass = sum(1 for _, ok, _ in results if ok)
    nfail = len(results) - npass
    for name, ok, detail in results:
        print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("selfcheck: %d passed, %d failed" % (npass, nfail))
    return 0


def _run_sub(cmd, input_dir, output_dir, work_dir, check):
    # 用相同解释器跑子命令，捕获退出码与落盘结果
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    code = 0
    try:
        r = subprocess.run([sys.executable, "-B", script, cmd,
                            "--input", input_dir, "--output", output_dir,
                            "--work", work_dir],
                           capture_output=True, timeout=120)
        code = r.returncode
    except Exception as exc:
        code = -1
        check("%s exit" % cmd, False, "exception %s" % exc)
        return
    check("%s exit0" % cmd, code == 0, "code=%d" % code)
    result_path = os.path.join(output_dir, "result.json")
    if os.path.isfile(result_path):
        obj, err = lib.read_json(result_path)
        check("%s result_json" % cmd, err is None and isinstance(obj, dict),
              "err=%s" % (err or ""))
        if isinstance(obj, dict):
            check("%s has_case_id" % cmd, bool(obj.get("case_id")),
                  "case_id=%s" % obj.get("case_id"))
            check("%s valid_status" % cmd, obj.get("status") in lib.STATUS_ALL,
                  "status=%s" % obj.get("status"))
    else:
        check("%s result_exists" % cmd, False, "missing result.json")


def _hash_result(path):
    obj, _ = lib.read_json(path)
    if not isinstance(obj, dict):
        return ""
    return lib.content_hash(obj)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = parse_args()
    try:
        return {
            "init": cmd_init,
            "survey": cmd_survey,
            "show": cmd_show,
            "extract": cmd_extract,
            "check": cmd_check,
            "build": cmd_build,
            "selfcheck": cmd_selfcheck,
        }[args.cmd](args)
    except Exception as exc:  # 任何异常都不让进程崩溃，退出码恒 0
        print("error: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
